"""四维分析/评级/建议/v5 评分演示 API 蓝图(自 app.py 拆分,函数体零改动)。"""

import logging

from flask import Blueprint, jsonify, request

from blueprints._utils import _resolve_report_type
from database.db_manager import get_connection

logger = logging.getLogger(__name__)

bp = Blueprint('analysis', __name__)


def _technical_detail_for_stock(stock_id):
    """020R-35/48B：计算技术指标明细（日线六类 + 周线/月线多周期，与评分引擎同口径）。

    注：本函数为纯展示层；周线/月线数据在评分引擎中已参评
    （技术面 7 子项：月线方向 25% + 周线波段 45% + 日线择时 30%）。
    失败或数据不足时返回 None，不影响报告主流程。
    """
    try:
        from modules.technical_detail import compute_technical_detail

        conn = get_connection()
        cursor = conn.cursor()
        merged = {}
        # 日线（全量）
        cursor.execute(
            'SELECT trade_date, close, high, low, volume FROM raw_kline '
            'WHERE stock_id = ? ORDER BY trade_date ASC',
            (stock_id,),
        )
        rows = cursor.fetchall()
        if len(rows) >= 20:
            merged.update(
                compute_technical_detail(
                    [float(r['close'] or 0) for r in rows],
                    [float(r['high'] or 0) for r in rows],
                    [float(r['low'] or 0) for r in rows],
                    [float(r['volume'] or 0) for r in rows],
                    latest_date=str(rows[-1]['trade_date'])[:10],
                    key_prefix='',
                    min_bars=20,
                ) or {}
            )
        # 020R-48B：周线/月线多周期（评分同口径）
        for table, prefix, min_bars in (
            ('raw_kline_weekly', 'weekly_', 20),
            ('raw_kline_monthly', 'monthly_', 5),
        ):
            try:
                cursor.execute(
                    f'SELECT trade_date, close, high, low, volume FROM {table} '
                    'WHERE stock_id = ? ORDER BY trade_date ASC',
                    (stock_id,),
                )
                prows = cursor.fetchall()
                if len(prows) >= min_bars:
                    merged.update(
                        compute_technical_detail(
                            [float(r['close'] or 0) for r in prows],
                            [float(r['high'] or 0) for r in prows],
                            [float(r['low'] or 0) for r in prows],
                            [float(r['volume'] or 0) for r in prows],
                            latest_date=str(prows[-1]['trade_date'])[:10],
                            key_prefix=prefix,
                            min_bars=min_bars,
                        ) or {}
                    )
            except Exception as e:  # noqa: BLE001 —— 表缺失/字段漂移时跳过该周期
                logging.getLogger(__name__).warning(
                    f'多周期指标计算失败 stock_id={stock_id} table={table}: {e}'
                )
        conn.close()

        # 021S：技术面打分子项（月线方向/周线波段/日线择时 7 子项得分+权重+明细）。
        # 复用评分引擎同口径计算（只读调用，不改引擎）；失败静默降级——
        # 前端退回仅显示指标读数（旧形态），不影响报告主流程。
        try:
            from modules.data_adapter import load_stockdata_from_db
            from modules.scoring_engine import TECHNICAL_SUBITEMS, score_dimension

            sd = load_stockdata_from_db(stock_id)
            if sd is not None:
                sub_score, sub_detail = score_dimension(sd, TECHNICAL_SUBITEMS, 'technical')
                if sub_detail.get('subitems'):
                    # 与 analyze() 同口径的月线空头惩罚标记（仅展示文案，分值不重复收缩）
                    if (
                        sub_score is not None
                        and sd.monthly_ma5 is not None
                        and sd.monthly_ma10 is not None
                        and sd.monthly_ma5 < sd.monthly_ma10
                    ):
                        sub_detail['monthly_penalty'] = '月线空头（MA5 低于 MA10），技术面得分 ×0.85'
                    merged['scoring_score'] = sub_score
                    merged['scoring_subitems'] = sub_detail['subitems']
                    if sub_detail.get('monthly_penalty'):
                        merged['monthly_penalty'] = sub_detail['monthly_penalty']
        except Exception as e:  # noqa: BLE001 —— 子项得分属增强展示，失败不阻塞
            logging.getLogger(__name__).warning(
                f'技术面打分子项计算失败 stock_id={stock_id}: {e}'
            )

        return merged if merged else None
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'技术指标明细计算失败 stock_id={stock_id}: {e}')
        return None


def _fundamental_detail_for_stock(stock_id):
    """020R-37/49/50：读取 raw_fundamental 最新一期 + 最新业绩预期（快报优先于预告），
    计算基本面五类子项展示明细。

    纯展示层增强：失败或数据不足时返回 None，不影响报告主流程。
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT report_date, pe_ratio, pb_ratio, roe, gross_margin, '
            'revenue_growth, profit_growth, ocf_to_net_profit, debt_ratio, current_ratio '
            'FROM raw_fundamental WHERE stock_id = ? ORDER BY report_date DESC LIMIT 1',
            (stock_id,),
        )
        row = cursor.fetchone()
        conn.close()
        # 020R-49/50：与评分同一套取用逻辑（get_latest_forecast_info），保证展示与打分一致
        fund_period = (
            str(row['report_date'])[:10].replace('-', '') if row and row['report_date'] else None
        )
        from modules.data_adapter import get_latest_forecast_info

        fc_info = get_latest_forecast_info(stock_id, fund_period)
        if not row and not fc_info:
            return None

        from modules.fundamental_detail import compute_fundamental_detail

        return compute_fundamental_detail(dict(row) if row else None, fc_info)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'基本面指标明细计算失败 stock_id={stock_id}: {e}')
        return None


def _capital_detail_for_stock(stock_id):
    """020R-38/45/47：读取 raw_capital_flow + holder_structure（+港股南向参考），计算资金面子项展示明细。

    纯展示层增强：失败或数据不足时返回 None，不影响报告主流程。
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT market FROM stocks WHERE id = ?', (stock_id,))
        mrow = cursor.fetchone()
        is_hk = bool(mrow and mrow['market'] == 'hk_stock')
        cursor.execute(
            'SELECT trade_date, main_net_inflow, north_holding_change, margin_balance, '
            'south_net_buy, south_hold_ratio '
            'FROM raw_capital_flow WHERE stock_id = ? ORDER BY trade_date ASC',
            (stock_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        # 020R-45：股东人数/机构持仓最新一期（021Q：含港股机构股东数量）
        cursor.execute(
            'SELECT stat_date, holder_count, holder_count_change_pct, total_shares, '
            'inst_shares, inst_ratio, inst_report_date, inst_count, inst_count_change_pct '
            'FROM holder_structure '
            'WHERE stock_id = ? ORDER BY stat_date DESC LIMIT 1',
            (stock_id,),
        )
        hs_row = cursor.fetchone()
        conn.close()

        south_flow = None
        if is_hk:
            # 020R-47：港股展示南向资金大盘参考（不参评）
            try:
                from modules.south_flow import get_latest_south_flow

                south_flow = get_latest_south_flow()
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).warning(f'南向资金快照读取失败 stock_id={stock_id}: {e}')

        if not rows and not hs_row and not south_flow:
            return None

        from modules.capital_detail import compute_capital_detail

        return compute_capital_detail(rows, dict(hs_row) if hs_row else None, south_flow)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'资金面指标明细计算失败 stock_id={stock_id}: {e}')
        return None


def _industry_flow_bg_for_stock(stock_id):
    """020R-54：个股所属行业资金背景（市场行情数据关联）。

    纯展示层增强：港股/无行业/板块未匹配时返回 None，不影响建议主流程。
    """
    try:
        conn = get_connection()
        row = conn.execute('SELECT industry, market FROM stocks WHERE id = ?', (stock_id,)).fetchone()
        conn.close()
        if not row or not row['industry'] or row['market'] == 'hk_stock':
            return None
        from modules.market_overview import get_industry_flow_bg

        return get_industry_flow_bg(row['industry'])
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'行业资金背景读取失败 stock_id={stock_id}: {e}')
        return None


def _news_detail_for_stock(stock_id):
    """020R-39：读取 news_sentiment 最新聚合 + 股东增持标志，计算消息面两个子项展示明细。

    纯展示层增强：失败或数据不足时返回 None，不影响报告主流程。
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT news_date, avg_sentiment, positive_count, negative_count, neutral_count, '
            'total_count, top_news_title FROM news_sentiment WHERE stock_id = ? '
            'ORDER BY news_date DESC LIMIT 1',
            (stock_id,),
        )
        news_row = cursor.fetchone()
        # 股东增持标志：与 data_adapter 一致，向后搜索最近非空值
        cursor.execute(
            'SELECT holder_increase FROM raw_fundamental WHERE stock_id = ? '
            'ORDER BY report_date DESC LIMIT 20',
            (stock_id,),
        )
        holder = None
        for r in cursor.fetchall():
            if r['holder_increase'] is not None:
                holder = bool(r['holder_increase'])
                break
        conn.close()
        if not news_row and holder is None:
            return None

        from modules.news_detail import compute_news_detail

        return compute_news_detail(dict(news_row) if news_row else None, holder)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'消息面指标明细计算失败 stock_id={stock_id}: {e}')
        return None


def _parse_markdown_risks(md):
    """020R-43：从 markdown_content 解析「**风险提示**」列表（快照路径 risk_warnings 字段来源）。"""
    risks = []
    in_risk = False
    for line in (md or '').split('\n'):
        if '**风险提示**' in line:
            in_risk = True
            continue
        if not in_risk:
            continue
        s = line.strip()
        if s.startswith('- '):
            risks.append(s[2:].strip())
        elif s == '':
            continue
        else:
            break
    return risks


def _enrich_data_warnings(result, stock_id):
    """020R-41：advise/analyze 响应补齐「数据完整度」行（与每日报告路径同口径）。

    刷新报告原先只有引擎降级提示（如资金面提示），数据滞后等完整度行丢失；
    此处按 daily_report._build_data_freshness 追加，保证两条路径一致。
    """
    try:
        from modules.daily_report import _build_data_freshness

        freshness = _build_data_freshness(stock_id)
        result['data_warnings'] = list(result.get('data_warnings') or []) + [
            f'数据完整度：{line}' for line in (freshness.get('lines') or [])
        ]
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'数据完整度行补充失败 stock_id={stock_id}: {e}')


@bp.route('/api/stocks/<int:stock_id>/analyze', methods=['POST'])
def api_analyze_stock(stock_id):
    """执行四维分析引擎评分（统一走 advisor.generate_advice 入口，与每日报告一致）"""
    from modules.advisor import generate_advice

    try:
        result = generate_advice(stock_id)
        # 005: 后处理集成价格建议（不修改 generate_advice）
        if result.get('success'):
            from modules.price_advisor import generate_price_advice

            result['price_advice'] = generate_price_advice(stock_id, result)
            # 009补充：动态操作建议覆盖旧建议，避免矛盾
            if result.get('price_advice', {}).get('action_suggestion'):
                result['position_advice'] = result['price_advice']['action_suggestion']
            # 020R-41：补齐数据完整度行（与每日报告路径一致）
            _enrich_data_warnings(result, stock_id)
            # 020R-43：advice_detail 统一为结构化 markdown（与快照路径一致，不再是一段纯文本）
            from modules.advisor import _build_markdown_single

            result['advice_detail'] = _build_markdown_single(result, result.get('previous_score'))
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'分析失败: {e!s}'}), 500


def _attach_scoring_subitems(stock_id, result):
    """021V：基本面/资金面/消息面打分子项注入（021S 技术面同口径）。

    只读复用评分引擎（score_dimension + 各维 SUBITEMS，引擎零改动），在四维明细
    dict 上附带 scoring_score / scoring_subitems（name/score/normalized_weight/
    completeness/degradation/detail）；明细为 None 的维度跳过。
    失败静默降级——前端退回纯指标读数形态，不影响报告主流程。
    """
    try:
        from modules.data_adapter import load_stockdata_from_db
        from modules.scoring_engine import (
            CAPITAL_SUBITEMS,
            FUNDAMENTAL_SUBITEMS,
            NEWS_SUBITEMS,
            score_dimension,
        )

        sd = load_stockdata_from_db(stock_id)
        if sd is None:
            return
        for detail_key, subitems, dim_name in (
            ('fundamental_detail', FUNDAMENTAL_SUBITEMS, 'fundamental'),
            ('capital_detail', CAPITAL_SUBITEMS, 'capital'),
            ('news_detail', NEWS_SUBITEMS, 'news'),
        ):
            detail = result.get(detail_key)
            if not isinstance(detail, dict):
                continue
            sub_score, sub_detail = score_dimension(sd, subitems, dim_name)
            if sub_detail.get('subitems'):
                detail['scoring_score'] = sub_score
                detail['scoring_subitems'] = sub_detail['subitems']
    except Exception as e:  # noqa: BLE001 —— 子项得分属增强展示，失败不阻塞
        logging.getLogger(__name__).warning(f'打分子项计算失败 stock_id={stock_id}: {e}')


def _enrich_advice_result(stock_id, result):
    """021K：实时建议结果的统一增强后处理。

    /advise 端点与 report-latest 实时路径共用，保证「打开报告」与「手动刷新」
    返回完全同源同构的数据（021E~021K 系列教训：两路口径漂移 = UX 不一致）。
    仅做后处理增强，不修改 generate_advice 本体（B24 红线）。
    """
    from datetime import datetime, timedelta, timezone

    from modules.advisor import _build_markdown_single
    from modules.price_advisor import generate_price_advice

    _CN_TZ = timezone(timedelta(hours=8))

    # 005: 价格建议（后处理集成）
    result['price_advice'] = generate_price_advice(stock_id, result)
    # 009补充：动态操作建议覆盖旧建议，避免矛盾
    if result.get('price_advice', {}).get('action_suggestion'):
        result['position_advice'] = result['price_advice']['action_suggestion']
    # 019L: generated_at（与 DB 行路径一致）
    result['generated_at'] = datetime.now(_CN_TZ).isoformat()
    # 020R-41：补齐数据完整度行
    _enrich_data_warnings(result, stock_id)
    # 020R-43：advice_detail 结构化 markdown
    result['advice_detail'] = _build_markdown_single(result, result.get('previous_score'))
    # 020R-35/37/38/39：四维指标明细（四维评分详情卡片数据）
    result['technical_detail'] = _technical_detail_for_stock(stock_id)
    result['fundamental_detail'] = _fundamental_detail_for_stock(stock_id)
    result['capital_detail'] = _capital_detail_for_stock(stock_id)
    result['news_detail'] = _news_detail_for_stock(stock_id)
    # 021V：基本面/资金面/消息面打分子项（021S 技术面同口径，实时/快照两路径同源）
    _attach_scoring_subitems(stock_id, result)
    # 020R-54：行业资金背景
    result['industry_flow_bg'] = _industry_flow_bg_for_stock(stock_id)
    return result


def _generate_fresh_advice(stock_id):
    """021K：实时重评 = generate_advice + 统一增强；任何失败返回 None（调用方回落快照）。"""
    try:
        from modules.advisor import generate_advice

        advice = generate_advice(stock_id)
        if advice.get('success'):
            return _enrich_advice_result(stock_id, advice)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            f'[report-latest] 实时重评失败 stock_id={stock_id}: {e}'
        )
    return None


# 021K：当日报告快照过旧阈值（分钟）——超过即服务端实时重评（与手动刷新同源）
_REPORT_STALE_MINUTES = 15


@bp.route('/api/stocks/<int:stock_id>/report-latest', methods=['GET'])
def api_get_report_latest(stock_id):
    """P3-A 附加修复：从 daily_reports 表读取该股票最新报告的评分数据。

    与列表页(watchlist-scores)完全同源，确保 engine_version/total_score/
    rating/generated_at 四元组一致。

    B11-DETAIL-LOAD：如果当日无报告，自动触发分析（对用户透明，无需手动刷新）。

    返回格式兼容前端 renderFullReport 所需的 adviseData 结构。
    """
    from datetime import datetime, timezone
    from datetime import timedelta as _td

    _CN_TZ = timezone(_td(hours=8), name='Asia/Shanghai')
    today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')

    conn = get_connection()
    cursor = conn.cursor()

    # 019D: 先判定当日 target_type（daily 优先），再限定 report_type + status='ok'
    target_type = _resolve_report_type(cursor, today)
    cursor.execute(
        """SELECT dr.*, s.symbol, s.name, s.market
           FROM daily_reports dr
           JOIN stocks s ON dr.stock_id = s.id
           WHERE dr.stock_id = ? AND dr.report_date = ?
           AND dr.status = 'ok' AND dr.report_type = ? """,
        (stock_id, today, target_type),
    )
    row = cursor.fetchone()

    # 021K：当日已有报告但快照过旧（>15分钟，工作日）→ 服务端实时重评，
    # 与手动「🔄 刷新报告」同源同副作用；失败回落快照展示（不影响打开）。
    if row is not None:
        _stale = True
        try:
            _gen_dt = datetime.fromisoformat(row['generated_at'])
            _stale = (datetime.now(_CN_TZ) - _gen_dt).total_seconds() > _REPORT_STALE_MINUTES * 60
        except (ValueError, TypeError):
            pass  # 时间戳缺失/解析失败 → 视为过旧
        if _stale and datetime.now(_CN_TZ).weekday() < 5:
            conn.close()
            _fresh = _generate_fresh_advice(stock_id)
            if _fresh is not None:
                return jsonify(_fresh)
            # 重评失败：重开连接回落快照路径
            conn = get_connection()
            cursor = conn.cursor()
            target_type = _resolve_report_type(cursor, today)
            cursor.execute(
                """SELECT dr.*, s.symbol, s.name, s.market
                   FROM daily_reports dr
                   JOIN stocks s ON dr.stock_id = s.id
                   WHERE dr.stock_id = ? AND dr.report_date = ?
                   AND dr.status = 'ok' AND dr.report_type = ? """,
                (stock_id, today, target_type),
            )
            row = cursor.fetchone()

    # B11-DETAIL-LOAD：当日无报告时，自动触发分析（静默）
    if not row:
        conn.close()
        # 020M：周末/休市日不实时生成——实时 advice 缺日报快照才有的综合文本(markdown)，
        # 前端「投资建议详情-综合分析」会整块缺失；周末直接回退最新历史日报快照（完整展示）。
        _is_weekend = datetime.now(_CN_TZ).weekday() >= 5
        if _is_weekend:
            logging.getLogger(__name__).info(
                f'[report-latest] stock_id={stock_id} 当日({today})无报告且为周末，'
                '跳过实时生成，直接回退最新日报快照（020M）'
            )
        else:
            # 021K：实时重评统一走 _generate_fresh_advice（含全量字段增强，
            # 与手动「🔄 刷新报告」完全同源——修复首开缺四维明细/亮点/行业背景的口径漂移）
            _fresh = _generate_fresh_advice(stock_id)
            if _fresh is not None:
                return jsonify(_fresh)

        # 引擎也失败，回退到历史报告
        conn = get_connection()
        cursor = conn.cursor()
        # 020M：按股票取最新报告日期（而非全表 MAX）——部分股票已有当日行时，
        # 不会回退到"别的股票才有报告的日期"导致本股票查无报告
        cursor.execute(
            "SELECT MAX(report_date) as latest_date FROM daily_reports "
            "WHERE stock_id = ? AND status='ok'",
            (stock_id,),
        )
        date_row = cursor.fetchone()
        latest_date = date_row['latest_date'] if date_row else None

        if not latest_date:
            conn.close()
            return jsonify({'success': False, 'message': '无报告数据'})

        # 019D: 回退查询同步统一口径（report_type + status='ok'）
        fallback_type = _resolve_report_type(cursor, latest_date)
        cursor.execute(
            """SELECT dr.*, s.symbol, s.name, s.market
               FROM daily_reports dr
               JOIN stocks s ON dr.stock_id = s.id
               WHERE dr.stock_id = ? AND dr.report_date = ?
               AND dr.status = 'ok' AND dr.report_type = ?""",
            (stock_id, latest_date, fallback_type),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'success': False, 'message': f'{latest_date} 无该股票报告'})
    else:
        latest_date = today
        conn.close()

    # 解析 key_factors 重建 dimensions
    import json as _json

    key_factors = {}
    try:
        if row['key_factors']:
            key_factors = _json.loads(row['key_factors'])
    except (ValueError, TypeError):
        pass

    # 从 key_factors 构建 dimensions 结构（兼容 renderFullReport）
    dimensions = {}
    for dim_key, dim_data in key_factors.items():
        dimensions[dim_key] = {
            'status': 'ok',
            'score': dim_data.get('score', 0),
            'weight': dim_data.get('weight', 0),
            'factors': dim_data.get('top_factors', {}),
        }

    # 解析 data_warnings
    data_warnings = []
    try:
        if row['data_warnings']:
            data_warnings = _json.loads(row['data_warnings'])
    except (ValueError, TypeError):
        pass

    # B15-T3: 从 key_factors 推算 data_quality（各维度完整度）
    # U7(#5): 当无法从 data_completeness 解析完整度时，不再默认100%，
    #         标记为 None（前端显示「已采集」），避免与实际数据矛盾
    _dq_map = {
        'kline': 'technical',
        'fundamental': 'fundamental',
        'capital_flow': 'capital',
        'news': 'news',
    }
    data_quality = {}
    for dim_key, dq_name in _dq_map.items():
        dim_info = key_factors.get(dim_key)
        if dim_info:
            # 尝试从 top_factors 中解析 data_completeness 百分比
            completeness_str = dim_info.get('top_factors', {}).get('data_completeness', '')
            if completeness_str and '%' in str(completeness_str):
                try:
                    data_quality[dq_name] = float(str(completeness_str).replace('%', '')) / 100.0
                except (ValueError, TypeError):
                    data_quality[dq_name] = None  # 解析失败，标记为未统计
            else:
                data_quality[dq_name] = None  # 无完整度字段，标记为未统计（不再默认100%）
        else:
            data_quality[dq_name] = 0.0

    # B15-T3: 从 dimensions 提取最强/最弱维度
    _dim_name_map = {
        'kline': '技术面',
        'fundamental': '基本面',
        'capital_flow': '资金面',
        'news': '消息面',
    }
    strongest_dim = None
    weakest_dim = None
    if dimensions:
        scored_dims = [
            (k, v.get('score', 0)) for k, v in dimensions.items() if v.get('status') == 'ok'
        ]
        if scored_dims:
            scored_dims.sort(key=lambda x: x[1], reverse=True)
            best_key, best_score = scored_dims[0]
            worst_key, worst_score = scored_dims[-1]
            strongest_dim = {'name': _dim_name_map.get(best_key, best_key), 'score': best_score}
            weakest_dim = {'name': _dim_name_map.get(worst_key, worst_key), 'score': worst_score}

    # B15-T3: 使用 markdown_content 作为 advice_detail
    advice_detail = row['markdown_content'] if row['markdown_content'] else None

    # 005: price_advice 实时计算（不使用日报缓存，确保持仓状态正确识别）
    # Bugfix: 日报缓存中的 price_advice 可能在持仓修复前生成，导致状态错误
    # 020M：latest_close/latest_close_date 提升为结果字段（快照路径原缺，前端
    # 「最新收盘」行不显示）
    _latest_close = None
    _latest_close_date = None
    price_advice = None
    try:
        from modules.price_advisor import generate_price_advice as _gpa

        # 查最新收盘价（report-latest 上下文中无 latest_close）
        _conn_pa = get_connection()
        _cur_pa = _conn_pa.cursor()
        _cur_pa.execute(
            "SELECT close, substr(trade_date, 1, 10) AS td FROM raw_kline "
            'WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1',
            (stock_id,),
        )
        _r = _cur_pa.fetchone()
        _conn_pa.close()
        _latest_close = float(_r['close']) if _r and _r['close'] else None
        _latest_close_date = _r['td'] if _r else None
        price_advice = _gpa(
            stock_id,
            {
                'rating': row['rating'] or '持有观望',
                'latest_close': _latest_close,
                'has_position': False,  # price_advisor 会自行查持仓
            },
        )
    except Exception as _e:
        logging.getLogger(__name__).warning(f'report-latest price_advice 实时计算失败: {_e}')

    result = {
        'success': True,
        'stock_id': stock_id,
        'stock_code': row['stock_code'] or row['symbol'],
        'stock_name': row['stock_name'] or row['name'],
        'market': row['market'],
        # 评分四元组（与列表页同源）
        'engine_version': row['engine_version'],
        'total_score': row['total_score'],
        'rating': row['rating'],
        'rating_label': row['rating_label'],
        'rating_date': latest_date,
        # 评分变动
        'prev_score': row['prev_score'],
        'score_change': row['score_change'],
        # 四维数据（从 key_factors 重建）
        'dimensions': dimensions,
        'data_warnings': data_warnings,
        # B15-T3: 投资建议字段补充
        'advice_detail': advice_detail,
        'position_advice': None,
        'price_advice': price_advice,
        # 020M：补齐快照路径缺失的展示字段（实时 advise 路径有、快照路径原缺）
        'action_advice': (price_advice or {}).get('action_suggestion'),
        'latest_close': _latest_close,
        'latest_close_date': _latest_close_date,
        'strongest_dim': strongest_dim,
        'weakest_dim': weakest_dim,
        'data_quality': data_quality if data_quality else None,
        # 来源标记
        'data_source': 'daily_reports',
        'generated_at': row['generated_at'],
    }

    # 009补充：动态操作建议覆盖旧建议，避免矛盾
    if result.get('price_advice', {}).get('action_suggestion'):
        result['position_advice'] = result['price_advice']['action_suggestion']

    # 020R-35：技术指标明细（均线/MACD/RSI/KDJ/布林/量能，供四维评分详情技术面展示）
    result['technical_detail'] = _technical_detail_for_stock(stock_id)
    # 020R-37：基本面指标明细（估值/盈利/成长/现金流/财务健康，供基本面卡展示）
    result['fundamental_detail'] = _fundamental_detail_for_stock(stock_id)
    # 020R-38：资金面指标明细（主力/北向/两融，供资金面卡展示）
    result['capital_detail'] = _capital_detail_for_stock(stock_id)
    # 020R-39：消息面指标明细（情绪/股东行为，供消息面卡展示）
    result['news_detail'] = _news_detail_for_stock(stock_id)
    # 021V：快照路径同样注入打分子项（与 _enrich_advice_result 实时路径同源）
    _attach_scoring_subitems(stock_id, result)
    # 020R-43：快照路径补齐 risk_warnings（从日报 markdown 解析，与实时路径一致）
    result['risk_warnings'] = _parse_markdown_risks(row['markdown_content'])

    return jsonify(result)


@bp.route('/api/stocks/<int:stock_id>/advise', methods=['POST'])
def api_advise_stock(stock_id):
    """执行模块2分析+模块3建议生成，返回完整评级建议"""
    from modules.advisor import generate_advice

    try:
        result = generate_advice(stock_id)
        # 021K：成功时走统一增强（与 report-latest 实时路径同源，杜绝两路口径漂移）
        if result.get('success'):
            result = _enrich_advice_result(stock_id, result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'建议生成失败: {e!s}'}), 500


# ============================================================
# 持仓管理 API
# ============================================================


@bp.route('/api/v5/scoring-demo', methods=['GET'])
def api_v5_scoring_demo():
    """v5.0 评分引擎演示接口（使用 MockDataProvider 生成模拟数据并评分）
    ⚠️ 仅调试/演示：前端无入口，app.py 启动横幅指引手动访问；test_routes 引用（勿删）。

    Query params:
      - scenario: normal / boundary / partial（默认 normal）
      - code: 股票代码（默认 600519.SH）
      - market: A / HK（默认 A）
      - close: 收盘价（默认随机）
      - missing_rate: partial场景缺失率（默认 0.3）
    """
    from modules.mock_data_provider import MockDataProvider
    from modules.scoring_engine import analyze

    scenario = request.args.get('scenario', 'normal')
    code = request.args.get('code', '600519.SH')
    market = request.args.get('market', 'A')
    close_str = request.args.get('close', '')
    missing_rate_str = request.args.get('missing_rate', '0.3')

    try:
        close = float(close_str) if close_str else None
        missing_rate = float(missing_rate_str)
    except (ValueError, TypeError):
        close = None
        missing_rate = 0.3

    provider = MockDataProvider()
    try:
        data = provider.generate(
            scenario,
            code=code,
            market=market,
            close=close,
            missing_rate=missing_rate,
            seed=42,
        )
    except Exception as e:
        return jsonify({'success': False, 'message': f'数据生成失败: {e}'}), 400

    result = analyze(data)

    return jsonify(
        {
            'success': True,
            'input_data': {
                'code': data.code,
                'market': data.market,
                'trade_date': data.trade_date,
                'close': data.close,
                'scenario': scenario,
            },
            'result': result.model_dump(),
        }
    )


@bp.route('/api/v5/scoring-analyze', methods=['POST'])
def api_v5_scoring_analyze():
    """v5.0 评分引擎分析接口（接收 StockData JSON，返回评分结果）
    ⚠️ 仅调试：前端无入口，供手动契约调试（勿删）。

    Body: StockData 契约字段（至少需 code/market/trade_date/close 四个必填项）
    """
    from modules.data_contract import StockData
    from modules.scoring_engine import analyze

    raw = request.get_json(silent=True) or {}

    # 必填字段校验
    required = ['code', 'market', 'trade_date', 'close']
    for f in required:
        if f not in raw or raw[f] is None:
            return jsonify({'success': False, 'message': f'缺少必填字段: {f}'}), 400

    try:
        data = StockData(**raw)
    except Exception as e:
        return jsonify({'success': False, 'message': f'StockData 构造失败: {e}'}), 400

    result = analyze(data)

    return jsonify(
        {
            'success': True,
            'result': result.model_dump(),
        }
    )


@bp.route('/api/v5/scoring-validation', methods=['GET'])
def api_v5_scoring_validation():
    """v5.0 评分引擎验证接口（运行 exhaustive 56 条极端值快速检查）
    ⚠️ 仅调试：前端无入口，评分引擎自验证工具（勿删）。

    返回每条用例的评分摘要及 NaN/Inf/范围检查结果。
    """
    import math

    from modules.mock_data_provider import MockDataProvider
    from modules.scoring_engine import analyze

    provider = MockDataProvider()
    batch = provider.generate(
        'boundary',
        boundary_mode='exhaustive',
        code='600519.SH',
        market='A',
        trade_date='20260718',
        close=100.0,
    )

    results = []
    all_pass = True
    for i, data in enumerate(batch):
        try:
            result = analyze(data)
            has_nan = any(math.isnan(v) for v in [result.total_score] if v is not None) or any(
                math.isnan(getattr(result, a, 0) or 0)
                for a in [
                    'technical_score',
                    'fundamental_score',
                    'sentiment_score',
                    'capital_score',
                ]
            )
            in_range = 0 <= result.total_score <= 100
            ok = not has_nan and in_range
            if not ok:
                all_pass = False

            # 找到被修改的字段
            extremes = provider.BOUNDARY_EXTREMES
            case_field = ''
            case_val = None
            cum = 0
            for field_name, extreme_values in extremes.items():
                for val in extreme_values:
                    if cum == i:
                        case_field = field_name
                        case_val = val
                    cum += 1

            results.append(
                {
                    'case_id': f'BV-{i + 1}',
                    'field': case_field,
                    'extreme_value': case_val,
                    'total_score': result.total_score,
                    'rating': result.rating,
                    'tech': result.technical_score,
                    'fund': result.fundamental_score,
                    'news': result.sentiment_score,
                    'capital': result.capital_score,
                    'nan_check': 'OK' if not has_nan else 'FAIL',
                    'range_check': 'OK' if in_range else 'FAIL',
                }
            )
        except Exception as e:
            all_pass = False
            results.append(
                {
                    'case_id': f'BV-{i + 1}',
                    'error': str(e),
                    'nan_check': 'CRASH',
                    'range_check': 'N/A',
                }
            )

    return jsonify(
        {
            'success': True,
            'total_cases': len(results),
            'all_pass': all_pass,
            'summary': f'{len(results)}条用例, {"全部通过" if all_pass else "存在异常"}',
            'cases': results,
        }
    )


# ============================================================
# 趋势罗盘（2026-09-07）：个股日/周/月三周期趋势判定（只读，无副作用）
# ============================================================


@bp.route('/api/stocks/<int:stock_id>/trend', methods=['GET'])
def api_stock_trend(stock_id):
    """个股三周期趋势：短期(日线)/中期(周线)/长期(月线) → 上涨/下跌/震荡。

    数据复用 data_adapter 组装的 StockData（含 020R-48 周线/月线指标），
    判定逻辑见 modules/trend_analyzer.py；不写库、不触发采集。
    """
    from modules.data_adapter import load_stockdata_from_db
    from modules.trend_analyzer import analyze_trends

    try:
        data = load_stockdata_from_db(stock_id)
    except Exception as e:  # noqa: BLE001 —— 数据缺失/坏行时按"数据不足"降级
        logger.warning(f'[trend] stock_id={stock_id} 构建失败: {e}')
        data = None

    if data is None:
        return jsonify({'success': False, 'message': '数据不足，请先采集数据'}), 404

    result = analyze_trends(data)
    from modules.collector._env import now_cn

    return jsonify(
        {
            'success': True,
            'stock_id': stock_id,
            'code': data.code,
            'close': data.close,
            **result,
            'generated_at': now_cn(),
        }
    )


# ============================================================
# US-11: 每日报告 API
# ============================================================
