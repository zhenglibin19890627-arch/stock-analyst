"""报告生成域（t6 拆包）：日报批量编排核心（generate_daily_report + 单股处理）。

- 修正项2：防抖保护 _generate_lock（跨域共享可变状态与唯一消费者同模块单宿；
  _scheduler 域与 modules/backfill_scheduler 经 facade 取同一 Lock 对象）；
- 019J/R18（M-1 红线）：单只超时 = daemon 线程 + join(timeout=STOCK_TIMEOUT_SECONDS)，
  严禁 executor 上下文管理器（退出时 __exit__ 的 shutdown(wait=True) 会 join 挂死 worker）；
  批次软超时 = BATCH_TIMEOUT_SECONDS 口径，两者零变化；
- B11-REPORT-REUSE / 013 report_type 复用口径 / 020J skip_collect / 021Z market_filter
  语义逐字保持；R9 写库路径经 _store._save_report，行为零变化。
"""

import threading
import time
from datetime import datetime

from modules.daily_report._env import (
    _CN_TZ,
    BATCH_TIMEOUT_SECONDS,
    STOCK_TIMEOUT_SECONDS,
    _build_key_factors,
    _build_markdown_single,
    collect_stock_data,
    fetch_capital_flow_batch,
    generate_advice,
    get_connection,
    logger,
)
from modules.daily_report._freshness import _build_data_freshness, _has_collection_today
from modules.daily_report._progress import _update_progress_file, _update_progress_stage
from modules.daily_report._store import _save_report
from modules.daily_report._summary import _build_markdown_summary, _check_score_differences

_generate_lock = threading.Lock()  # 修正项2：防抖保护


def _get_all_stocks():
    """获取所有自选股列表"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, s.symbol, s.name, s.market
        FROM stocks s
        WHERE s.status = 'active'
        ORDER BY s.id
    """)
    stocks = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return stocks


def _get_prev_score(stock_id, report_date):
    """获取上一交易日的评分（用于计算分数变动）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT total_score FROM daily_reports
        WHERE stock_id = ? AND report_date < ? AND status = 'ok'
        ORDER BY report_date DESC LIMIT 1
    """,
        (stock_id, report_date),
    )
    row = cursor.fetchone()
    conn.close()
    return row['total_score'] if row else None


# 019A: 关键因子/Markdown 构建函数已收敛至 advisor 模块，此处统一导入
# （_build_key_factors / _build_markdown_single 见 _env 顶部导入）


def _process_single_stock(stock, target_date, force, report_type='daily', skip_collect=False):
    """012-B: 单只股票处理（供 ThreadPoolExecutor 调用）

    将原 for 循环体内的逻辑封装为独立函数。
    返回值与原有 results.append 结构一致。
    013: report_type 透传至 _save_report，并限定复用检查范围。
    020J: skip_collect=True 跳过采集（历史报告重生成，纯分析重算）。
    """
    stock_id = stock['id']
    symbol = stock['symbol']
    name = stock.get('name', '')
    market = stock.get('market', 'a_stock')

    # B11-REPORT-REUSE：检查当日是否已有有效报告，有则跳过采集+分析
    # B15-T2: force=True 时跳过复用检查
    # 013: 复用检查限定为同 report_type，intraday 不会复用 daily
    if not force:
        conn_check = get_connection()
        cursor_check = conn_check.cursor()
        cursor_check.execute(
            'SELECT total_score, rating, rating_label, engine_version, key_factors, '
            'data_warnings, markdown_content, generated_at, prev_score, score_change '
            'FROM daily_reports WHERE stock_id=? AND report_date=? AND status="ok" '
            'AND report_type=?',
            (stock_id, target_date, report_type),
        )
        existing = cursor_check.fetchone()
        conn_check.close()
    else:
        existing = None

    if existing:
        # 020R-58：当日已有有效报告，但若该股今天没有任何采集记录
        # （如报告由调度批次前的 advise 生成），仍补充采集并重新分析，
        # 避免「报告先于批次生成」导致当日数据缺失（小米 08-16 案例）。
        if skip_collect or _has_collection_today(stock_id, target_date):
            # 今日已有有效报告，直接使用已有数据，跳过采集+分析
            logger.info(f'[{symbol}] 今日已有有效报告，跳过采集+分析')
            engine = existing['engine_version'] or 'legacy'
            total_score = existing['total_score'] or 0
            rating_val = existing['rating'] or ''
            score_change = existing['score_change']

            return {
                'stock_id': stock_id,
                'symbol': symbol,
                'name': name,
                'status': 'ok',
                'engine': engine,
                'score': total_score,
                'rating': rating_val,
                'score_change': score_change,
                'reused': True,
            }
        logger.info(f'[{symbol}] 今日已有有效报告但无当日采集记录，补充采集后重新分析')

    # FIX-A 改动2：每只股票先采集后分析
    # 012-B 增强：线程内更新进度 stage（采集阶段），前端进度条可显示"当前在干什么"
    # 020J：skip_collect=True 跳过采集（历史报告重生成：数据已回填完毕，纯分析）
    if not skip_collect:
        _update_progress_stage(symbol, '采集数据中')
        collect_stock_data(symbol, market)
    else:
        logger.info(f'[{symbol}] skip_collect：跳过采集，直接重新分析')
    _update_progress_stage(symbol, '数据检查中')
    # 数据完整度检查：报告生成前检查各维度数据新鲜度/来源，
    # 检查结果随报告输出（data_warnings + markdown），让报告说明数据完整度情况
    freshness = _build_data_freshness(stock_id)
    _update_progress_stage(symbol, '分析评分中')
    # 统一调用 advisor.generate_advice()（021AE 起 v5 单引擎）
    advice = generate_advice(stock_id, report_date=target_date)

    if not advice.get('success'):
        _update_progress_stage(symbol, '生成失败')
        raise Exception(advice.get('message', '生成失败'))

    _update_progress_stage(symbol, '写入报告')

    # 005: 日报集成价格建议
    from modules.price_advisor import generate_price_advice

    price_advice = generate_price_advice(stock_id, advice) if advice.get('success') else None

    engine = advice.get('engine_version', 'v5')
    total_score = advice.get('total_score', 0)
    rating = advice.get('rating', '')
    rating_label = advice.get('rating_label', '')

    # 获取前日分数
    prev_score = _get_prev_score(stock_id, target_date)
    score_change = round(total_score - prev_score, 1) if prev_score is not None else None

    # 构建关键因子
    key_factors = _build_key_factors(advice)

    # 2026-09-18：操盘手建议摘要预计算（阶段名 + 评级分歧标记）——
    # 看板「操作建议」卡零重算读取（portfolio._derive_trader_signal 派生展示）；
    # 只读函数不触碰 generate_advice（B24 红线），失败静默降级不阻塞报告
    # 021BQ：增量键 top_action（操作矩阵当前视角首行动作摘要，旧键零改动）
    # 021BR t3：传入 price_advice_override——advisor 末尾已先插当日 price_advice=NULL
    # 报告行，若 trader 摘要再读库会拿到 NULL 价位层 → top_action 恒为纪律线、
    # 与报告行内 price_advice 止损同日双数值；传入第 2 步已算好的 price_advice
    # 使 key_factors.trader.top_action 与报告行 price_advice 同源
    try:
        from modules.trader_advisor import generate_trader_advice

        _ta = generate_trader_advice(stock_id, price_advice_override=price_advice)
        if _ta.get('available'):
            key_factors['trader'] = {
                'stage_name': _ta['stage'].get('name'),
                'has_disagreement': bool(_ta.get('disagreement')),
                'disagreement_text': (_ta.get('disagreement') or {}).get('text'),
                'top_action': (_ta.get('operations') or {}).get('top_action'),
            }
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[daily-report] trader 摘要预计算失败 stock_id={stock_id}: {e}')

    # 构建单只 Markdown（末尾追加数据完整度小节）
    md_content = _build_markdown_single(advice, prev_score)
    md_content += '\n\n## 数据完整度\n\n'
    for line in freshness['lines']:
        md_content += f'- {line}\n'

    # 数据提示合并：引擎降级提示 + 数据完整度说明
    data_warnings = list(advice.get('data_warnings', []) or [])
    data_warnings.extend(f'数据完整度：{line}' for line in freshness['lines'])

    # 写入数据库
    _save_report(
        report_date=target_date,
        stock_id=stock_id,
        stock_code=symbol,
        stock_name=name,
        engine_version=engine,
        total_score=total_score,
        rating=rating,
        rating_label=rating_label,
        prev_score=prev_score,
        score_change=score_change,
        key_factors=key_factors,
        data_warnings=data_warnings,
        markdown_content=md_content,
        price_advice=price_advice,
        report_type=report_type,
    )

    return {
        'stock_id': stock_id,
        'symbol': symbol,
        'name': name,
        'status': 'ok',
        'engine': engine,
        'score': total_score,
        'rating': rating,
        'score_change': score_change,
        'reused': False,
        'freshness': freshness,
    }


def generate_daily_report(target_date=None, force=False, report_type='daily', skip_collect=False, market_filter=None):
    """生成每日分析报告

    Args:
        target_date: 报告日期(YYYY-MM-DD)，默认今天
        force: 强制全量刷新，忽略已有结果
        report_type: 报告类型 'daily'(盘后日报) / 'intraday'(盘中快报)
        skip_collect: 020J：跳过全部采集（同花顺预取 + 逐只采集），纯用库内已有数据重新分析，
            用于数据回填后的历史报告重生成（数据已采集完毕，避免重复打外部接口）
        market_filter: 021Z：市场过滤（None=全部 / 'a_stock' / 'hk_stock'）。
            港股 16:00 收盘晚于 A股主批次(15:54)，港股报告由独立批次按此过滤生成。
    Returns:
        dict: 生成结果汇总
    """
    # 修正项2：防抖保护
    if not _generate_lock.acquire(timeout=5):
        logger.warning('报告生成任务已在进行中，跳过重复触发')
        return {'success': False, 'message': '报告生成任务已在进行中'}

    try:
        if target_date is None:
            target_date = datetime.now(_CN_TZ).strftime('%Y-%m-%d')

        logger.info(f'开始生成每日报告 date={target_date}' + (f'（市场={market_filter}）' if market_filter else ''))

        stocks = _get_all_stocks()
        if market_filter:
            stocks = [s for s in stocks if s.get('market') == market_filter]
        if not stocks:
            return {'success': False, 'message': '没有自选股' + (f'（市场={market_filter}）' if market_filter else '')}

        results = []
        success_count = 0
        fail_count = 0
        v5_count = 0
        legacy_count = 0
        fallback_count = 0
        reuse_count = 0

        # === 012-B: 批次超时 + 进度追踪 ===
        # 初始进度提前写入（在资金面批量预取之前）：预取阶段可能耗时较长
        # （同花顺接口异常时回退 EM 逐只采集 + 重试），若等预取结束才写进度文件，
        # 前端进度条会长时间无反馈，误以为"生成卡死/失败"
        total = len(stocks)
        started_at_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')

        _update_progress_file(
            {
                'date': target_date,
                'total': total,
                'current': 0,
                'current_symbol': '',
                'current_name': '',
                'stage': '准备中',
                'status': 'running',
                'started_at': started_at_str,
                'last_update': '',
                'finished_at': None,
            }
        )

        # === 018: 循环前批量预取A股同花顺辅助指标（不阻断东财逐只采集） ===
        # 020J：skip_collect=True（历史报告重生成）跳过预取，纯用库内数据
        if skip_collect:
            logger.info('[日报] skip_collect：跳过同花顺资金面批量预取')
        a_symbols = [s['symbol'] for s in stocks if s['market'] == 'a_stock']
        if a_symbols and not skip_collect:
            _update_progress_file(
                {
                    'date': target_date,
                    'total': total,
                    'current': 0,
                    'current_symbol': f'共{len(a_symbols)}只',
                    'current_name': '',
                    'stage': '资金面批量预取中',
                    'status': 'running',
                    'started_at': started_at_str,
                    'last_update': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                    'finished_at': None,
                }
            )
            try:
                # EM 回退逐只采集阶段可能耗时 30 分钟+，逐只更新进度文件
                # （否则前端动效长时间停在"资金面批量预取中"像卡住）
                def _prefetch_progress(idx, prefetch_total, sym):
                    _update_progress_file(
                        {
                            'date': target_date,
                            'total': prefetch_total,
                            'current': idx + 1,
                            'current_symbol': sym,
                            'current_name': '',
                            'stage': f'资金面批量预取中（EM逐只 {idx + 1}/{prefetch_total}）',
                            'status': 'running',
                            'started_at': started_at_str,
                            'last_update': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                            'finished_at': None,
                        }
                    )

                batch_result = fetch_capital_flow_batch(a_symbols, progress_cb=_prefetch_progress)
                logger.info(f'[日报] 资金面批量预取: {batch_result}')
            except Exception as e:
                logger.warning(f'[日报] 资金面批量预取失败(不阻断): {e}')

        # 预取结束，进入逐只处理阶段（批次超时从此刻起算，预取耗时不计入）
        batch_start = time.time()

        _update_progress_file(
            {
                'date': target_date,
                'total': total,
                'current': 0,
                'current_symbol': '',
                'current_name': '',
                'stage': '开始处理',
                'status': 'running',
                'started_at': started_at_str,
                'last_update': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                'finished_at': None,
            }
        )

        for idx, stock in enumerate(stocks, 1):
            # 整体超时检查（软超时）
            if time.time() - batch_start > BATCH_TIMEOUT_SECONDS:
                remaining = total - idx + 1
                logger.warning(
                    f'[日报进度] 批次整体超时({BATCH_TIMEOUT_SECONDS}s)，剩余{remaining}只跳过'
                )
                fail_count += remaining
                break

            symbol = stock['symbol']
            name = stock.get('name', '')
            stock_id = stock['id']
            logger.info(f'[日报进度] {idx}/{total} 开始 {symbol} {name}')
            _update_progress_file(
                {
                    'date': target_date,
                    'total': total,
                    'current': idx,
                    'current_symbol': symbol,
                    'current_name': name,
                    'stage': '开始处理',
                    'status': 'running',
                    'started_at': started_at_str,
                    'last_update': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                    'finished_at': None,
                }
            )

            # 单只超时控制（019J：daemon 线程 + join(timeout)，替代 executor 上下文管理器
            # M-1 红线：executor 上下文管理器退出时 __exit__ 调用 shutdown(wait=True)
            # 会 join 挂死 worker，超时保护形同虚设——本实现超时后立即 continue，不 join 不等待 worker）
            try:
                # box 模式：线程内异常不自动传播，必须显式捕获（否则超时判定会误判）
                box = {'exc': None}

                def _run_single_stock():
                    try:
                        box['r'] = _process_single_stock(
                            stock, target_date, force, report_type, skip_collect
                        )
                    except Exception as e:
                        box['exc'] = e

                t = threading.Thread(target=_run_single_stock, daemon=True)
                t.start()
                t.join(timeout=STOCK_TIMEOUT_SECONDS)
                if t.is_alive():
                    # 超时：写 failed 记录 + results.append + continue，不等待 worker
                    # （worker 迟到完成时 _process_single_stock 内部 _save_report
                    #   DELETE+INSERT 会覆盖 failed 为 ok，数据自愈不丢分）
                    fail_count += 1
                    logger.error(f'[日报进度] {symbol} 超时({STOCK_TIMEOUT_SECONDS}s)，跳过')
                    _save_report(
                        report_date=target_date,
                        stock_id=stock_id,
                        stock_code=symbol,
                        stock_name=name,
                        engine_version=None,
                        total_score=None,
                        rating=None,
                        rating_label=None,
                        prev_score=None,
                        score_change=None,
                        key_factors=None,
                        data_warnings=None,
                        markdown_content=None,
                        status='failed',
                        error_msg=f'采集超时({STOCK_TIMEOUT_SECONDS}s)',
                        price_advice=None,
                        report_type=report_type,
                    )
                    results.append(
                        {
                            'stock_id': stock_id,
                            'symbol': symbol,
                            'name': name,
                            'status': 'failed',
                            'error': f'采集超时({STOCK_TIMEOUT_SECONDS}s)',
                        }
                    )
                    continue

                # 线程内异常重抛，走外层 except（fail_count+1 + failed 记录，与现状一致）
                if box.get('exc') is not None:
                    raise box['exc']
                result = box['r']

                # 处理成功结果
                if result.get('reused'):
                    reuse_count += 1
                if result.get('is_fallback'):
                    fallback_count += 1
                success_count += 1
                engine = result.get('engine', 'legacy')
                if engine == 'v5':
                    v5_count += 1
                else:
                    legacy_count += 1

                results.append(
                    {
                        'stock_id': result['stock_id'],
                        'symbol': result['symbol'],
                        'name': result['name'],
                        'status': 'ok',
                        'engine': engine,
                        'score': result.get('score'),
                        'rating': result.get('rating'),
                        'score_change': result.get('score_change'),
                    }
                )

            except Exception as e:
                fail_count += 1
                error_msg = str(e)
                logger.error(f'[{symbol}] 报告生成失败: {error_msg}')

                # 记录失败
                _save_report(
                    report_date=target_date,
                    stock_id=stock_id,
                    stock_code=symbol,
                    stock_name=name,
                    engine_version=None,
                    total_score=None,
                    rating=None,
                    rating_label=None,
                    prev_score=None,
                    score_change=None,
                    key_factors=None,
                    data_warnings=None,
                    markdown_content=None,
                    status='failed',
                    error_msg=error_msg,
                    price_advice=None,
                    report_type=report_type,
                )

                results.append(
                    {
                        'stock_id': stock_id,
                        'symbol': symbol,
                        'name': name,
                        'status': 'failed',
                        'error': error_msg,
                    }
                )

            logger.info(f'[日报进度] {symbol} 完成')

        # 批次完成
        elapsed = int(time.time() - batch_start)
        logger.info(
            f'[日报进度] ===== 批次完成 成功{success_count}/失败{fail_count} 耗时{elapsed}s ====='
        )
        _update_progress_file(
            {
                'date': target_date,
                'total': total,
                'current': total,
                'current_symbol': '',
                'current_name': '',
                'stage': '完成',
                'status': 'done',
                'started_at': started_at_str,
                'last_update': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
                'finished_at': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
            }
        )
        # === 012-B END ===

        # P3-A：评分差异监控（v5 vs legacy）
        score_diff_flags = _check_score_differences(target_date, results)

        # 生成汇总 Markdown 并保存到文件
        full_md = _build_markdown_summary(target_date, results)

        # 012-C: 失败摘要
        failure_summary = None
        if fail_count > 0:
            by_reason = {}
            for r in results:
                if r.get('status') == 'failed':
                    reason = r.get('error', '未知')
                    by_reason.setdefault(reason, []).append(r.get('symbol', ''))
            failure_summary = {'total_failed': fail_count, 'by_reason': by_reason}

        summary = {
            'success': True,
            'report_date': target_date,
            'finished_at': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),  # 019D: 批次生成时刻
            'total': len(stocks),
            'success_count': success_count,
            'fail_count': fail_count,
            'v5_count': v5_count,
            'legacy_count': legacy_count,
            'fallback_count': fallback_count,
            'reuse_count': reuse_count,
            'score_diff_flags': score_diff_flags,
            'failure_summary': failure_summary,  # 012-C 新增
            'results': results,
            'markdown': full_md,
        }

        logger.info(
            f'每日报告生成完成 date={target_date}: '
            f'成功{success_count}/失败{fail_count} '
            f'v5={v5_count} legacy={legacy_count} fallback={fallback_count} '
            f'score_diff_flags={len(score_diff_flags)}'
        )

        return summary

    finally:
        _generate_lock.release()
