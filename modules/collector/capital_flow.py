"""资金面编排：fetch_capital_flow 链序、批量采集 _em_batch_collect/fetch_capital_flow_batch、历史回补、EM 连续失败计数。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
import random as _random
import time
from datetime import datetime

import akshare as ak

from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.capital_em import (
    _EM_BACKOFF_CAP_SECONDS,
    _EM_BATCH_GAP_RANGE,
    _EM_BATCH_SIZE,
    _EM_CIRCUIT_BREAK_N,
    _EM_COOLDOWN_FAIL_N,
    _EM_COOLDOWN_SECONDS,
    _EM_FALLBACK_TOTAL_CAP_SECONDS,
    _EM_INTER_DELAY_RANGE,
    _em_banned,
    _em_clear_ban,
    _em_record_ban,
    _fetch_capital_flow_em,
    _fetch_capital_flow_em_individual,
    _get_em_market_code,
    _parse_cn_amount,
    _safe_float_pct,
    _safe_float_wan,
)
from modules.collector.capital_sources import (
    _fetch_capital_flow_netease,
    _fetch_capital_flow_sina,
    _fetch_capital_flow_sina_main,
    _fetch_capital_flow_tencent_hk,
)
from modules.collector.capital_ths import _fetch_capital_flow_ths_batch
from modules.collector.capital_westock import _fetch_capital_flow_westock
from modules.collector.kline import _is_intraday_session
from modules.collector.symbols_status import get_stock_id, save_data_status

# ============================================================
# 019C：东方财富（EM）回退循环优化常量
# 用于 fetch_capital_flow_batch 中 THS批量源失败后的逐只回退循环
# 机制：错峰 → 分批 → 退避 → 冷却 → 熔断 → 整体软超时
# ============================================================
_EM_CONSECUTIVE_FAIL_COUNT = 0  # 进程级连续失败计数（R-4：Flask不重启时跨次批量生效）


def _em_batch_collect(symbols, log_prefix='EM回退', progress_cb=None):
    """
    019C/019E 共享：EM 逐只采集循环（错峰/分批/退避/冷却/熔断/软超时六项机制）。
    直接沿用共享常量 _EM_INTER_DELAY_RANGE ~ _EM_FALLBACK_TOTAL_CAP_SECONDS
    及模块级计数器 _EM_CONSECUTIVE_FAIL_COUNT，不新增平行常量。

    Args:
        symbols: list[str] 待采集 A 股代码列表
        log_prefix: str 日志前缀（'EM回退' / '资金面补采'，QA 依赖区分）
        progress_cb: 可选进度回调 progress_cb(idx, total, symbol)，
                     每只股票开始采集前调用（供日报进度文件逐只更新，动效不再长时间静止）

    Returns:
        dict: {'success_count': n, 'fail_count': n, 'source': str}
    """
    global _EM_CONSECUTIVE_FAIL_COUNT
    em_success = 0
    em_fail = 0
    start_ts = time.time()
    cooldown_done = False  # 本轮冷却是否已触发（避免重复暂停）
    timed_out = False
    circuit_broken = False
    remaining = list(symbols)
    total = len(remaining)

    for idx, sym in enumerate(remaining):
        # 进度回调：每只开始前通知（EM 逐只阶段可能耗时 30 分钟+，前端需可见进展）
        if progress_cb:
            try:
                progress_cb(idx, total, sym)
            except Exception:
                pass  # 进度回调失败不影响采集
        # --- 6. 整体软超时检查（每只开始前） ---
        elapsed = time.time() - start_ts
        if elapsed > _EM_FALLBACK_TOTAL_CAP_SECONDS:
            unprocessed = remaining[idx:]
            logger.warning(
                f'[{log_prefix}] 整体软超时({int(elapsed)}s>{_EM_FALLBACK_TOTAL_CAP_SECONDS}s)，'
                f'终止剩余 {len(unprocessed)} 只未采集: {unprocessed}'
            )
            em_fail += len(unprocessed)
            timed_out = True
            break

        # --- 5. 熔断检查（模块级计数 R-3/R-4） ---
        if _EM_CONSECUTIVE_FAIL_COUNT >= _EM_CIRCUIT_BREAK_N:
            unprocessed = remaining[idx:]
            logger.warning(
                f'[{log_prefix}] 熔断触发（连续失败{_EM_CONSECUTIVE_FAIL_COUNT}'
                f'>={_EM_CIRCUIT_BREAK_N}），'
                f'终止本轮回退，剩余 {len(unprocessed)} 只未采集: {unprocessed}'
            )
            _em_record_ban()  # 019Z：进入冷却期，后续东财资金面请求直接走备用源
            em_fail += len(unprocessed)
            circuit_broken = True
            break

        # --- 2. 分批间隔（每_BATCH_SIZE只进入新批次） ---
        if idx > 0 and idx % _EM_BATCH_SIZE == 0:
            batch_gap = _random.uniform(*_EM_BATCH_GAP_RANGE)
            logger.info(
                f'[{log_prefix}] 进入第{idx // _EM_BATCH_SIZE + 1}批'
                f'（第{idx + 1}只），批间停顿{batch_gap:.1f}s'
            )
            time.sleep(batch_gap)
        elif idx > 0:
            # --- 1. 错峰 + 3. 退避 ---
            base_delay = _random.uniform(*_EM_INTER_DELAY_RANGE)
            if _EM_CONSECUTIVE_FAIL_COUNT > 0:
                delay = min(
                    base_delay * (2 ** _EM_CONSECUTIVE_FAIL_COUNT),
                    _EM_BACKOFF_CAP_SECONDS,
                )
                logger.info(
                    f'[{log_prefix}] {sym} 退避延迟{delay:.1f}s'
                    f'（连续失败{_EM_CONSECUTIVE_FAIL_COUNT}次，'
                    f'基础{base_delay:.1f}s×2^{_EM_CONSECUTIVE_FAIL_COUNT}）'
                )
            else:
                delay = base_delay
                logger.info(f'[{log_prefix}] {sym} 错峰延迟{delay:.1f}s')
            time.sleep(delay)

        # --- 4. 冷却（连续失败≥阈值时额外暂停一次） ---
        if (
            _EM_CONSECUTIVE_FAIL_COUNT >= _EM_COOLDOWN_FAIL_N
            and not cooldown_done
        ):
            logger.warning(
                f'[{log_prefix}] 连续失败{_EM_CONSECUTIVE_FAIL_COUNT}'
                f'>={_EM_COOLDOWN_FAIL_N}，'
                f'冷却暂停{_EM_COOLDOWN_SECONDS}s后继续...'
            )
            time.sleep(_EM_COOLDOWN_SECONDS)
            cooldown_done = True

        # --- 采集 ---
        try:
            result = fetch_capital_flow(sym, 'a_stock')
            if result and result[0] == 'success':
                em_success += 1
                if _EM_CONSECUTIVE_FAIL_COUNT > 0:
                    logger.info(
                        f'[{log_prefix}] {sym} 成功，连续失败计数重置'
                        f'({_EM_CONSECUTIVE_FAIL_COUNT}→0)'
                    )
                # 020D：仅当成功来自东财源时才重置计数/解除熔断——
                # westock/新浪顶替成功不等于"东财恢复"，否则会形成
                # "westock 成功→解除熔断→下一只又挨东财 4 轮重试"的乒乓循环。
                if '东方财富' in (result[1] or ''):
                    _EM_CONSECUTIVE_FAIL_COUNT = 0  # 7. 计数重置（R-3：含同日跳过）
                    _em_clear_ban()  # 019Z：东财恢复即解除熔断冷却
                else:
                    logger.info(
                        f'[{log_prefix}] {sym} 非东财源成功（{result[1]}），'
                        '熔断冷却保持生效'
                    )
                cooldown_done = False  # 成功后重置冷却标记
            else:
                em_fail += 1
                _EM_CONSECUTIVE_FAIL_COUNT += 1
                logger.warning(
                    f'[{log_prefix}] {sym} 采集失败'
                    f'(result={result[0] if result else "None"})，'
                    f'连续失败计数={_EM_CONSECUTIVE_FAIL_COUNT}'
                )
        except Exception as e:
            em_fail += 1
            _EM_CONSECUTIVE_FAIL_COUNT += 1
            logger.warning(
                f'[{log_prefix}] {sym} 采集异常: {e}，'
                f'连续失败计数={_EM_CONSECUTIVE_FAIL_COUNT}'
            )

    # 构造返回值（标注终止原因）
    source = f'EM逐只({log_prefix}'
    if timed_out:
        source += '，软超时终止'
    if circuit_broken:
        source += f'，熔断终止(EM连续失败={_EM_CONSECUTIVE_FAIL_COUNT})'
    source += f'，成功{em_success}/失败{em_fail})'
    return {
        'success_count': em_success,
        'fail_count': em_fail,
        'source': source,
    }


def fetch_capital_flow_batch(a_stock_symbols, progress_cb=None):
    """
    018改造：同花顺批量预取 — 仅写入辅助指标 ths_net_inflow。
    同花顺"净额"= 全部资金净流入（总主动买入-总主动卖出），非主力净流入。
    本函数不写入 main_net_inflow / main_net_inflow_pct；
    主力净流入链路（021L 起）为：腾讯 westock（主源）→ 东财三层（兜底）→ 新浪 lscjfb 主力口径(sina_main)
    → 估算兜底（仅展示不参评）；019S 起不再使用同花顺顶替主力净流入（ths_total 仅为历史存量）。
    同花顺净额作为辅助指标，用于判断主力与散户行为背离。

    Args:
        a_stock_symbols: list[str]，A 股代码列表（如 ['600276','000333',...]）
        progress_cb: 可选进度回调（EM 回退逐只采集阶段每只调用），
                     用于日报进度文件逐只更新

    Returns:
        dict: {'success_count': n, 'fail_count': n, 'source': '同花顺批量(辅助指标)'}
    """
    # 019G：交易日校验 — 周末（周六/周日）跳过 THS 批量预取（含补采），
    # 避免非交易日 THS 接口返回旧数据被写入。法定节假日落在工作日时
    # 仍执行（THS 返回前一交易日数据，低概率可接受）。
    now = datetime.now(_CN_TZ)
    if now.weekday() >= 5:  # 5=周六, 6=周日
        logger.info(f'[同花顺批量] 非交易日（{now.strftime("%A")}），跳过 THS 批量预取（含补采）')
        return {
            'success_count': 0, 'fail_count': 0,
            'source': '同花顺批量(非交易日跳过)',
            'skipped': True, 'reason': 'non_trading_day'
        }

    if not a_stock_symbols:
        return {'success_count': 0, 'fail_count': 0, 'source': '同花顺批量(空列表)'}

    today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    df = _fetch_capital_flow_ths_batch()
    if df is None:
        # FIX-B：THS不可用时回退EM逐只采集（019C六项机制增强，已提取为_em_batch_collect共享函数）
        logger.warning('[同花顺批量] 批量源不可用（含重试+备选均失败），回退EM逐只采集（019C增强）')
        return _em_batch_collect(a_stock_symbols, log_prefix='EM回退', progress_cb=progress_cb)

    # 同花顺股票代码列可能为 int64（000333→333）或字符串，统一规整为6位字符串
    def _norm_code(v):
        s = str(v).strip()
        if s.replace('.', '').isdigit():
            return str(int(float(s))).zfill(6)
        return s

    code_col = '股票代码'
    if code_col not in df.columns:
        logger.warning(f'[同花顺批量] 缺少 {code_col} 列，实际列: {list(df.columns)}')
        return {
            'success_count': 0,
            'fail_count': len(a_stock_symbols),
            'source': '同花顺批量(字段异常)',
        }

    df_norm = df.copy()
    df_norm['_code6'] = df_norm[code_col].apply(_norm_code)

    success_count = 0
    fail_count = 0
    conn = get_connection()
    cursor = conn.cursor()

    for symbol in a_stock_symbols:
        stock_id = get_stock_id(symbol, 'a_stock')
        if not stock_id:
            fail_count += 1
            continue

        rows = df_norm[df_norm['_code6'] == symbol]
        if rows.empty:
            fail_count += 1
            logger.info(f'[同花顺批量] {symbol} 未在批量结果中命中')
            continue

        row = rows.iloc[0]
        # 净额为中文金额格式（如"-6.78亿"/"3.19亿"），解析后单位为元，÷1e4 转万元
        main_net_yuan = _parse_cn_amount(row.get('净额'))

        if main_net_yuan is None:
            fail_count += 1
            continue

        ths_net = round(main_net_yuan / 1e4, 2)  # 元→万元

        # 018: 仅写入辅助字段 ths_net_inflow，不影响 main_net_inflow
        # 使用 UPDATE（当天已有东财数据时）或 INSERT OR IGNORE（当天无数据时）
        cursor.execute(
            """
            UPDATE raw_capital_flow SET ths_net_inflow = ?
            WHERE stock_id = ? AND trade_date = ?
        """,
            (ths_net, stock_id, today_str),
        )
        if cursor.rowcount == 0:
            # 当天尚无东财数据，插入占位行（仅含 ths_net_inflow）
            cursor.execute(
                """
                INSERT OR IGNORE INTO raw_capital_flow
                (stock_id, trade_date, ths_net_inflow)
                VALUES (?, ?, ?)
            """,
                (stock_id, today_str, ths_net),
            )
        success_count += 1

    conn.commit()
    conn.close()
    logger.info(
        f'[同花顺批量] 辅助指标写入完成: 成功 {success_count}/{len(a_stock_symbols)}，source=同花顺批量(辅助指标)'
    )

    # ============================================================
    # 019E Task 1：批量补采正向触发机制（D1）
    # THS 批量成功后，检查哪些股票仍缺少当日真实资金面数据，
    # 对缺失股票执行 EM 逐只补采（完整复用 019C 六项机制）。
    # 评审 E-2：补采清单 = 输入列表 - 已有真实数据的股票
    # ============================================================
    import inspect as _inspect
    try:
        _caller_file = _inspect.stack()[1].filename
        _trigger_source = '日报批次' if 'daily_report' in _caller_file else 'batch-analyze'
    except Exception:
        _trigger_source = 'batch-analyze'

    # 补采清单生成（评审 E-2 裁定）
    # 019Q Task 3（M-5）：补采清单 SQL 扩为 NOT IN ('ths_total','sina_main')。
    # 021L：NOT IN 列表移除 'westock'——westock 为主源，其行计为"已完成"，
    # 不再进入补采清单重试东财（日常东财请求密度归零是 021L 的核心目标）；
    # sina_main / ths_total 行仍进入补采清单 —— 主源链恢复时可覆盖升级。
    # 019S：'ths_total' 字面量保留不动——防御存量 ths_total 行（方案 b 处置后已清零），
    # 若删除则存量行被计为"已有真实数据"，回补永不触发；
    # 待存量清零确认后经新批次评审简化（可改为 NOT IN ('sina_main') 或删除）。
    supplement_symbols = list(a_stock_symbols)
    try:
        conn_sup = get_connection()
        cursor_sup = conn_sup.cursor()
        real_sids = set()
        for sym in a_stock_symbols:
            sid = get_stock_id(sym, 'a_stock')
            if sid:
                cursor_sup.execute(
                    'SELECT 1 FROM raw_capital_flow WHERE stock_id=? AND trade_date=? '
                    'AND main_net_inflow IS NOT NULL '
                    'AND (is_estimated = 0 OR is_estimated IS NULL) '
                    "AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main'))",
                    (sid, today_str),
                )
                if cursor_sup.fetchone():
                    real_sids.add(sid)
        conn_sup.close()
        supplement_symbols = [
            s for s in a_stock_symbols
            if get_stock_id(s, 'a_stock') not in real_sids
        ]
    except Exception as e:
        logger.warning(f'[资金面补采] 补采清单生成异常(降级为全量补采): {e}')

    if supplement_symbols:
        logger.info(
            f'[资金面补采] 触发来源={_trigger_source}，'
            f'补采清单({len(supplement_symbols)}/{len(a_stock_symbols)}只): {supplement_symbols}'
        )
        supplement_result = _em_batch_collect(
            supplement_symbols, log_prefix='资金面补采', progress_cb=progress_cb
        )
        return {
            'success_count': success_count + supplement_result['success_count'],
            'fail_count': fail_count + supplement_result['fail_count'],
            'source': f'同花顺批量(辅助指标) + 资金面补采(成功{supplement_result["success_count"]}/失败{supplement_result["fail_count"]})',
        }

    return {'success_count': success_count, 'fail_count': fail_count, 'source': '同花顺批量(辅助指标)'}


def backfill_capital_history(symbol, market, dates):
    """020H：逐日回补资金面历史缺口。
    020I：链序为 腾讯 westock --date（A股+港股）→ 新浪 lscjfb（仅A股）。
    021L：此链是历史缺口的主回补通道（日常采集 westock 仅写当日 1 行；
    东财 push2his 120 天批量仅在 westock 失败的兜底路径偶发覆盖）。

    供补采调度器回填近 10 个交易日的历史缺失日。
    返回成功回补的日期列表。
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return []
    filled = []
    for d in dates:
        source = None
        row = None
        try:
            row = _fetch_capital_flow_westock(symbol, market, date_str=d)
            if row:
                source = 'westock'
        except Exception:
            row = None
        if row is None and market == 'a_stock':
            try:
                row = _fetch_capital_flow_sina_main(symbol, market, target_date=d)
                if row:
                    source = 'sina_main'
            except Exception:
                row = None
        if not row or not source:
            logger.info(f'[{symbol}] 历史资金面回补 {d}: 无可用数据源，跳过')
            continue
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, '
                'total_net_inflow=?, super_large_net=?, '
                'large_net=?, medium_net=?, small_net=?, is_estimated=0, capital_source=? '
                'WHERE stock_id=? AND trade_date=?',
                (
                    row['main_net_inflow'],
                    row.get('main_net_inflow_pct'),
                    row.get('total_net_inflow'),
                    row['super_large_net'],
                    row['large_net'],
                    row['medium_net'],
                    row['small_net'],
                    source,
                    stock_id,
                    d,
                ),
            )
            if cur.rowcount == 0:
                cur.execute(
                    'INSERT OR IGNORE INTO raw_capital_flow '
                    '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, total_net_inflow, '
                    'super_large_net, large_net, '
                    'medium_net, small_net, is_estimated, capital_source) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)',
                    (
                        stock_id,
                        d,
                        row['main_net_inflow'],
                        row.get('main_net_inflow_pct'),
                        row.get('total_net_inflow'),
                        row['super_large_net'],
                        row['large_net'],
                        row['medium_net'],
                        row['small_net'],
                        source,
                    ),
                )
            conn.commit()
            conn.close()
            filled.append(d)
            logger.info(
                f'[{symbol}] 历史资金面回补成功: {d} 主力={row["main_net_inflow"]}万({source})'
            )
        except Exception as e:
            logger.warning(f'[{symbol}] 历史资金面回补 {d} 失败: {e}')
    return filled


def backfill_hk_total_net(symbol, dates):
    """020O：港股资金面补充回填（腾讯 hkfund TotalNetFlow + 主力净流入占比）。

    仅 UPDATE total_net_inflow / main_net_inflow_pct 列，不动 main_net_inflow 等主力字段——
    港股存量主力多为东财真实数据（EM 恢复前不降级覆盖）。
    A股不适用（asfund 散户为被动镜像、全口径恒等0，无全净额数据）。
    返回成功回补的日期列表。
    """
    stock_id = get_stock_id(symbol, 'hk_stock')
    if not stock_id:
        return []
    filled = []
    for d in dates:
        try:
            row = _fetch_capital_flow_westock(symbol, 'hk_stock', date_str=d)
            if not row or row.get('total_net_inflow') is None:
                logger.info(f'[{symbol}] 港股全净额回补 {d}: 腾讯无该日数据，跳过')
                continue
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                'UPDATE raw_capital_flow SET total_net_inflow=?, main_net_inflow_pct=? '
                'WHERE stock_id=? AND trade_date=?',
                (row['total_net_inflow'], row.get('main_net_inflow_pct'), stock_id, d),
            )
            conn.commit()
            conn.close()
            filled.append(d)
            logger.info(
                f'[{symbol}] 港股回补成功: {d} 全净额={row["total_net_inflow"]}万 '
                f'占比={row.get("main_net_inflow_pct")}%(腾讯)'
            )
        except Exception as e:
            logger.warning(f'[{symbol}] 港股回补 {d} 失败: {e}')
    return filled


def fetch_capital_flow(symbol, market):
    """
    采集资金面数据。
    主力净流入来源阶梯（021L 定稿：westock 提为主源）：
    Layer 1: 腾讯自选股 westock asfund/hkfund（A股+港股，真实，capital_source='westock'，
             主力口径=超大+大，与东财精确同口径——020A 探针实证相等）
    Layer 2: 东方财富 push2his 个股历史资金流向（A股+港股，真实，capital_source=NULL）
    Layer 3: 东方财富 push2 实时资金流向（A股+港股，真实）
    Layer 4: akshare stock_individual_fund_flow（仅A股，底层仍为东方财富）
    全部失败时降级阶梯：
      ① 新浪 lscjfb 主力口径顶替（capital_source='sina_main'，r0+r1 超大单+大单，
         is_estimated=0 参与评分，019Q）
      ② 估算兜底（is_estimated=1，仅展示，不参与评分；019S 起不再使用同花顺顶替主力净流入）
    链路：腾讯 westock（主源）→ 东财三层（兜底）→ 新浪 lscjfb 主力口径(sina_main) → 估算兜底（仅展示不参评）。
    同日已有真实数据时自动跳过采集（防覆盖机制：westock/东财真实 > 新浪 > 估算；
    021L 起 westock 行计为"当日已完成"，东财不再每日自动回补，历史缺口由补采调度器
    020I 链 westock --date → 新浪 负责）。
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 020L：周末守卫 — 周六/周日休市，资金面全链路跳过（东财/腾讯/新浪/估算各层）。
    # 根因：周末定时日报仍逐只采集，估算兜底层把源返回日期写成非交易日脏行
    # （实测 08-09 周日 23 行 is_estimated=1 脏数据，挤占前端 LIMIT 10 展示名额）。
    # 与 019G 同花顺周末跳过同原则；周一开盘后自动恢复采集。
    if datetime.now(_CN_TZ).weekday() >= 5:
        logger.info(f'[{symbol}] 周末休市（weekday>=5），跳过资金面采集（020L）')
        return 'skipped', '周末休市，跳过资金面采集'

    global _EM_CONSECUTIVE_FAIL_COUNT  # 020B：逐只链路复用进程级东财连续失败计数（熔断传播）

    warnings = []
    saved_count = 0
    skipped = 0  # 019N: 跳过的异常数据行数（EM 三层全字段 None 行，不写 NULL 占位）
    source = ''

    # ============================================================
    # 018/019K/019Q/021L: 前置校验层 — 检测当日已写入的真实数据。
    # 同花顺批量预取仅写入辅助字段 ths_net_inflow，不会触发本跳过逻辑。
    # 019K: THS 顶替行（capital_source='ths_total'）同样不触发跳过——
    # 019Q: 新浪顶替行（capital_source='sina_main'）同样不触发跳过——
    # 链路可将其覆盖升级为同口径真实数据。
    # 021L 起 westock 为主源：westock 行（capital_source='westock'）计为"当日已完成"
    # （与东财真实行 capital_source=NULL 同待遇）——不再为覆盖 westock 而每日重试东财；
    # 历史缺口由补采调度器 020I 链负责。sina_main/ths_total 行仍不触发跳过。
    # ============================================================
    today_str_pre = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    conn_pre = get_connection()
    cursor_pre = conn_pre.cursor()
    # 019E Task 2.4：前置校验适配——估算行（is_estimated=1）不阻止重写
    # 019K Task 3：前置校验排除 THS 顶替行（capital_source='ths_total'），保证可回补
    # 019Q Task 3：防覆盖 SQL 扩展为 NOT IN ('ths_total','sina_main')（M-5）
    # 019S：'ths_total' 字面量保留不动——防御存量 ths_total 行（08-05/08-06 共 27 行），
    # 若删除则存量行会被误判为"已有真实数据"，回补永不触发；
    # 待存量清零后（方案 b 处置 + 只读断言）经新批次评审简化。
    # 021L：NOT IN 列表移除 'westock'——westock 行计为已完成（主源语义）。
    cursor_pre.execute(
        'SELECT COUNT(*) AS cnt FROM raw_capital_flow WHERE stock_id = ? AND trade_date = ? '
        'AND main_net_inflow IS NOT NULL AND (is_estimated = 0 OR is_estimated IS NULL) '
        "AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main'))",
        (stock_id, today_str_pre),
    )
    pre_cnt = cursor_pre.fetchone()['cnt']
    conn_pre.close()
    # 020R-59：盘中时段不跳过——主源路径会 UPSERT 当日行（盘中累计值随点击时刻刷新）
    intraday_refresh = _is_intraday_session(market)
    if pre_cnt > 0 and not intraday_refresh:
        skip_msg = f'同日跳过(已有真实资金流数据,记录数={pre_cnt})'
        logger.info(f'[{symbol}] {skip_msg}（东方财富已写入）')
        save_data_status(stock_id, 'capital', 'success', skip_msg)
        return 'success', f'今日已有真实资金流数据（{pre_cnt}条），跳过采集'

    # ============================================================
    # 同日真实数据防覆盖机制（P3-A验收前置修复）
    # 若今日已通过主源（westock/东财）成功采集资金流数据，跳过本次采集
    # 防止后续操作触发fallback用估算值覆盖真实数据
    # 021L：westock 提为主源，成功消息（'腾讯自选股'开头）与东财同待遇触发跳过
    # ============================================================
    today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    conn_skip = get_connection()
    cursor_skip = conn_skip.cursor()
    cursor_skip.execute(
        'SELECT message FROM data_status WHERE stock_id = ? AND dimension = ? '
        'AND fetched_at LIKE ? ORDER BY fetched_at DESC LIMIT 1',
        (stock_id, 'capital', today_str + '%'),
    )
    skip_row = cursor_skip.fetchone()
    conn_skip.close()
    if skip_row and skip_row['message']:
        _src_msg = skip_row['message']
        if _src_msg.startswith(('东方财富', '腾讯自选股')) and not intraday_refresh:
            logger.info(f'[{symbol}] 今日已有主源真实资金流数据，跳过采集（防覆盖）')
            save_data_status(
                stock_id, 'capital', 'success', f'同日跳过(已有真实数据): {_src_msg[:60]}'
            )
            return 'success', '今日已有主源真实资金流数据，跳过采集（防覆盖）'

    # ============================================================
    # 021L 主源：腾讯自选股（westock）资金面 — 原 020A 备用层提为主源
    # A股 asfund / 港股 hkfund，主力口径=超大+大（与东财同概念，020A 探针实证
    # MainNetFlow == JumboNetFlow + BlockNetFlow 精确相等；社区实测不封 IP）。
    # 写库 is_estimated=0、capital_source='westock'（参与评分）；
    # westock 行计为"当日已完成"（前置校验/补采清单/延迟补采 SQL 同步认定）。
    # 成功后东财三层整体跳过（saved_count>0）——东财自此仅低频兜底。
    # UPDATE 无来源守卫（可覆盖估算行/顶替行，口径更优），估算层守卫不受影响。
    # ============================================================
    if saved_count == 0:
        try:
            w_row = _fetch_capital_flow_westock(symbol, market)
            if w_row:
                w_date = (
                    w_row['trade_date']
                    or datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                )
                conn = get_connection()
                cursor = conn.cursor()
                # 020F：UPDATE + INSERT OR IGNORE（与新浪层同模式）——
                # INSERT OR REPLACE 会整行替换，冲掉同花顺批量预取的辅助字段 ths_net_inflow
                # 021Q：UPDATE 追加南下两列（COALESCE 保旧值——非港股通标的/接口无
                # _lgtHoldInfo 时写 NULL 会清掉已有南下数据，重跑不降级）
                cursor.execute(
                    'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, '
                    'total_net_inflow=?, super_large_net=?, '
                    'large_net=?, medium_net=?, small_net=?, is_estimated=0, capital_source=?, '
                    'south_net_buy=COALESCE(?, south_net_buy), '
                    'south_hold_ratio=COALESCE(?, south_hold_ratio) '
                    'WHERE stock_id=? AND trade_date=?',
                    (
                        w_row['main_net_inflow'],
                        w_row.get('main_net_inflow_pct'),
                        w_row.get('total_net_inflow'),
                        w_row['super_large_net'],
                        w_row['large_net'],
                        w_row['medium_net'],
                        w_row['small_net'],
                        'westock',
                        w_row.get('south_net_buy'),
                        w_row.get('south_hold_ratio'),
                        stock_id,
                        w_date,
                    ),
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        'INSERT OR IGNORE INTO raw_capital_flow '
                        '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, total_net_inflow, '
                        'super_large_net, large_net, '
                        'medium_net, small_net, is_estimated, capital_source, '
                        'south_net_buy, south_hold_ratio) '
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'westock', ?, ?)",
                        (
                            stock_id,
                            w_date,
                            w_row['main_net_inflow'],
                            w_row.get('main_net_inflow_pct'),
                            w_row.get('total_net_inflow'),
                            w_row['super_large_net'],
                            w_row['large_net'],
                            w_row['medium_net'],
                            w_row['small_net'],
                            w_row.get('south_net_buy'),
                            w_row.get('south_hold_ratio'),
                        ),
                    )
                conn.commit()
                conn.close()
                if w_row['main_net_inflow'] is not None:
                    saved_count = 1
                source = '腾讯自选股(westock)'
                logger.info(
                    f'[{symbol}] 腾讯自选股资金面成功(主源): '
                    f'主力净流入={w_row["main_net_inflow"]}万, date={w_date}'
                )
            else:
                warnings.append('腾讯自选股资金面层无数据')
        except Exception as e:
            warnings.append(f'腾讯自选股资金面层失败: {e}')
            logger.warning(f'[{symbol}] 腾讯自选股资金面层失败: {e}')

    # === 兜底层1：东方财富个股资金流向历史（A股+港股，secid区分；021L 起为兜底）===
    if saved_count == 0:
        try:
            rows_data = _fetch_capital_flow_em_individual(symbol, market)
            if rows_data:
                conn = get_connection()
                cursor = conn.cursor()
                skipped = 0

                for row in rows_data:
                    trade_date = str(row.get('日期', '')).strip()
                    if not trade_date:
                        continue

                    # 019N: 安全转换（None/NaN/'-'/±Inf → None，移除 or 0 伪造零），金额元→万元，占比不转换
                    main_net = _safe_float_wan(row.get('主力净流入-净额'))
                    main_net_pct = _safe_float_pct(row.get('主力净流入-净占比'))
                    super_large = _safe_float_wan(row.get('超大单净流入-净额'))
                    large = _safe_float_wan(row.get('大单净流入-净额'))
                    medium = _safe_float_wan(row.get('中单净流入-净额'))
                    small = _safe_float_wan(row.get('小单净流入-净额'))

                    # 019N: 六字段全 None → 跳过该行（不写 NULL 占位行、不清空该日既有字段）
                    if all(v is None for v in (main_net, main_net_pct, super_large, large, medium, small)):
                        skipped += 1
                        continue

                    # 019E M-7：EM 写入显式携带 is_estimated=0（防御估算→真实覆盖时标记归位）
                    # 019K Task 3：EM 写入显式携带 capital_source=NULL（顶替行被 EM 覆盖后来源归位）
                    # 020G：UPDATE + INSERT OR IGNORE（保留同花顺辅助字段 ths_net_inflow，
                    # 与 westock/新浪层同模式；INSERT OR REPLACE 会整行替换冲掉它）
                    cursor.execute(
                        'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, '
                        'super_large_net=?, large_net=?, medium_net=?, small_net=?, '
                        'is_estimated=0, capital_source=NULL '
                        'WHERE stock_id=? AND trade_date=?',
                        (
                            main_net,
                            main_net_pct,
                            super_large,
                            large,
                            medium,
                            small,
                            stock_id,
                            trade_date,
                        ),
                    )
                    if cursor.rowcount == 0:
                        cursor.execute(
                            'INSERT OR IGNORE INTO raw_capital_flow '
                            '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, '
                            'super_large_net, large_net, medium_net, small_net, is_estimated, capital_source) '
                            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)',
                            (
                                stock_id,
                                trade_date,
                                main_net,
                                main_net_pct,
                                super_large,
                                large,
                                medium,
                                small,
                            ),
                        )
                    # 019N: saved_count 仅计主字段 main 非 None 的行（假成功修正）
                    if main_net is not None:
                        saved_count += 1

                conn.commit()
                conn.close()
                source = '东方财富(个股历史)'
                _EM_CONSECUTIVE_FAIL_COUNT = 0  # 020B：东财成功，重置连续失败计数
                _em_clear_ban()
                logger.info(
                    f'[{symbol}] 资金面保存成功: {saved_count}天有效数据, 跳过 {skipped} 天异常数据'
                )
            else:
                warnings.append('东方财富个股资金流向返回空数据')
        except Exception as e:
            warnings.append(f'东方财富个股资金流向获取失败: {e}')
            logger.warning(f'[{symbol}] 东方财富个股资金流向获取失败: {e}')

    # === 备用数据源1：东方财富 push2（港股必须走这里）===
    if saved_count == 0:
        try:
            klines = _fetch_capital_flow_em(symbol, market)
            if klines is None:
                warnings.append('东方财富push2接口无法访问（直连和代理均失败）')
            elif klines:
                conn = get_connection()
                cursor = conn.cursor()
                skipped = 0

                for line in klines:
                    parts = line.split(',')
                    if len(parts) >= 6:
                        trade_date = parts[0]
                        # 019N: 安全转换（parts 为原始字符串，'nan'/'-'/空 → None），元转万元
                        main_net = _safe_float_wan(parts[1])
                        small_net = _safe_float_wan(parts[2])
                        medium_net = _safe_float_wan(parts[3])
                        large_net = _safe_float_wan(parts[4])
                        super_large_net = _safe_float_wan(parts[5])

                        # 019N: 五字段全 None → 跳过该行（不写 NULL 占位行）
                        if all(
                            v is None
                            for v in (main_net, small_net, medium_net, large_net, super_large_net)
                        ):
                            skipped += 1
                            continue

                        # 019E M-7：EM 写入显式携带 is_estimated=0
                        # 019K Task 3：EM 写入显式携带 capital_source=NULL（顶替行被 EM 覆盖后来源归位）
                        # 020G：UPDATE + INSERT OR IGNORE（保留 ths_net_inflow）
                        cursor.execute(
                            'UPDATE raw_capital_flow SET main_net_inflow=?, super_large_net=?, '
                            'large_net=?, medium_net=?, small_net=?, main_net_inflow_pct=NULL, '
                            'is_estimated=0, capital_source=NULL '
                            'WHERE stock_id=? AND trade_date=?',
                            (
                                main_net,
                                super_large_net,
                                large_net,
                                medium_net,
                                small_net,
                                stock_id,
                                trade_date,
                            ),
                        )
                        if cursor.rowcount == 0:
                            cursor.execute(
                                'INSERT OR IGNORE INTO raw_capital_flow '
                                '(stock_id, trade_date, main_net_inflow, '
                                'super_large_net, large_net, medium_net, small_net, is_estimated, capital_source) '
                                'VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL)',
                                (
                                    stock_id,
                                    trade_date,
                                    main_net,
                                    super_large_net,
                                    large_net,
                                    medium_net,
                                    small_net,
                                ),
                            )
                        # 019N: saved_count 仅计主字段 main 非 None 的行
                        if main_net is not None:
                            saved_count += 1

                conn.commit()
                conn.close()
                source = '东方财富(push2)'
                _EM_CONSECUTIVE_FAIL_COUNT = 0  # 020B
                _em_clear_ban()
            else:
                warnings.append('东方财富push2资金流向数据为空')
        except Exception as e:
            warnings.append(f'东方财富push2获取失败: {e}')
            logger.warning(f'[{symbol}] 东方财富push2获取失败: {e}')

    # === 备用数据源2：akshare内置接口（最后降级方案，仅A股；019Z：东财熔断冷却期同样跳过）===
    if saved_count == 0 and market == 'a_stock' and not _em_banned():
        try:
            logger.info(f'[{symbol}] 尝试akshare备用数据源...')
            df_ak = ak.stock_individual_fund_flow(stock=symbol, market=_get_em_market_code(symbol))
            if df_ak is not None and not df_ak.empty:
                conn = get_connection()
                cursor = conn.cursor()
                skipped = 0

                for _, row in df_ak.iterrows():
                    trade_date = str(row.get('日期', '')).strip()
                    if not trade_date:
                        continue

                    # 019N: 安全转换（df 值为 np.float64/str，pd.isna 兼容），金额元→万元，占比不转换
                    main_net = _safe_float_wan(row.get('主力净流入-净额'))
                    main_net_pct = _safe_float_pct(row.get('主力净流入-净占比'))
                    super_large = _safe_float_wan(row.get('超大单净流入-净额'))
                    large = _safe_float_wan(row.get('大单净流入-净额'))
                    medium = _safe_float_wan(row.get('中单净流入-净额'))
                    small = _safe_float_wan(row.get('小单净流入-净额'))

                    # 019N: 六字段全 None → 跳过该行（不写 NULL 占位行）
                    if all(
                        v is None for v in (main_net, main_net_pct, super_large, large, medium, small)
                    ):
                        skipped += 1
                        continue

                    # 019E M-7：EM 写入显式携带 is_estimated=0
                    # 019K Task 3：EM 写入显式携带 capital_source=NULL（顶替行被 EM 覆盖后来源归位）
                    # 020G：UPDATE + INSERT OR IGNORE（保留 ths_net_inflow）
                    cursor.execute(
                        'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, '
                        'super_large_net=?, large_net=?, medium_net=?, small_net=?, '
                        'is_estimated=0, capital_source=NULL '
                        'WHERE stock_id=? AND trade_date=?',
                        (
                            main_net,
                            main_net_pct,
                            super_large,
                            large,
                            medium,
                            small,
                            stock_id,
                            trade_date,
                        ),
                    )
                    if cursor.rowcount == 0:
                        cursor.execute(
                            'INSERT OR IGNORE INTO raw_capital_flow '
                            '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, '
                            'super_large_net, large_net, medium_net, small_net, is_estimated, capital_source) '
                            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)',
                            (
                                stock_id,
                                trade_date,
                                main_net,
                                main_net_pct,
                                super_large,
                                large,
                                medium,
                                small,
                            ),
                        )
                    # 019N: saved_count 仅计主字段 main 非 None 的行
                    if main_net is not None:
                        saved_count += 1

                conn.commit()
                conn.close()
                source = 'akshare(备用)'
                _EM_CONSECUTIVE_FAIL_COUNT = 0  # 020B
                _em_clear_ban()
                logger.info(
                    f'[{symbol}] akshare备用源成功: {saved_count}天有效数据, 跳过 {skipped} 天异常数据'
                )
            else:
                warnings.append('akshare备用数据源返回空数据')
        except Exception as e:
            warnings.append(f'akshare备用源失败: {e}')
            logger.warning(f'[{symbol}] akshare备用源失败: {e}')

    # ============================================================
    # 020B/021L：EM 兜底层失败标志，用于熔断累计。
    # 021L 语义：仅当"westock 失败且东财三层也未写入任何数据"才计失败——
    # westock 成功时东财根本未被尝试，不构成"东财不可用"的证据，不累计熔断
    # （旧序中 westock 成功前东财已挨过失败，故需计入；新序无此情形）。
    # ============================================================
    em_failed_this_stock = saved_count == 0

    # ============================================================
    # 019E Task 2：估算兜底（仅展示用，不参与评分）
    # EM 三层全失败时，降级到估算源（新浪/腾讯/网易）写入当日 1 行。
    # 估算值通过 is_estimated=1 标记，data_adapter/advisor SQL 层过滤确保不进入评分。
    # 估算源公式"成交额×涨跌幅/100"与真实主力净流入无相关性，仅供展示。
    # ============================================================
    # 019E Task 2.6（M-4）：拆除提前 return，改为标志位继续执行估算降级链路
    em_all_failed = (saved_count == 0)
    est_source = ''
    # 020B：东财兜底层失败的股票累计熔断计数（021L 起仅在 westock 也失败时才会发生），
    # 达到阈值进入冷却——后续股票跳过东财直连（019Z 机制原先只在批量回退循环生效，
    # 手动报告生成的逐只链路此前每只都要空烧 4 轮重试 ≈5 分钟）。
    if em_failed_this_stock:
        _EM_CONSECUTIVE_FAIL_COUNT += 1
        if _EM_CONSECUTIVE_FAIL_COUNT >= _EM_CIRCUIT_BREAK_N:
            _em_record_ban()
    if em_all_failed:
        logger.warning(
            f'[{symbol}] 资金面主源与东财兜底全失败（westock + push2his/push2/akshare），'
            '尝试新浪顶替 → 估算兜底（链路：腾讯 westock → 东财三层 → 新浪 lscjfb 主力口径(sina_main) '
            '→ 估算兜底仅展示不参评）'
        )

        # ============================================================
        # 019Q Task 2：新浪 lscjfb 真实数据顶替（019S 起为 EM 三层全失败时唯一真实顶替源，D-1）
        # 主力口径（r0+r1 超大单+大单），与 EM"主力=超大+大"同概念，口径逼近度最高
        # （019K 实证 THS 全部资金口径同日符号可相反，019S 已弃用）。
        # 写四档 + 主力（D-6），不写 main_net_inflow_pct（ratioamount 为总净占比，M-4）。
        # 严格日期匹配：lscjfb 无当日行（非交易日/未发布）→ 返回 None → 落回估算兜底（M-2）。
        # 写入模式复用 019K 规格：UPDATE + INSERT OR IGNORE，严禁 INSERT OR REPLACE；
        # 无条件 UPDATE（不带来源守卫，M-5）——可覆盖估算行（口径更优）。
        # ============================================================
        try:
            sina_row = _fetch_capital_flow_sina_main(symbol, market)
            if sina_row:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    'UPDATE raw_capital_flow SET main_net_inflow=?, super_large_net=?, '
                    'large_net=?, medium_net=?, small_net=?, is_estimated=0, capital_source=? '
                    'WHERE stock_id=? AND trade_date=?',
                    (
                        sina_row['main_net_inflow'],
                        sina_row['super_large_net'],
                        sina_row['large_net'],
                        sina_row['medium_net'],
                        sina_row['small_net'],
                        'sina_main',
                        stock_id,
                        today_str,
                    ),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        'INSERT OR IGNORE INTO raw_capital_flow '
                        '(stock_id, trade_date, main_net_inflow, super_large_net, large_net, '
                        'medium_net, small_net, is_estimated, capital_source) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)',
                        (
                            stock_id,
                            today_str,
                            sina_row['main_net_inflow'],
                            sina_row['super_large_net'],
                            sina_row['large_net'],
                            sina_row['medium_net'],
                            sina_row['small_net'],
                            'sina_main',
                        ),
                    )
                conn.commit()
                conn.close()
                saved_count = 1
                save_data_status(
                    stock_id, 'capital', 'fallback',
                    '新浪顶替(主力口径r0+r1；主源恢复后自动回补)'
                )
                logger.info(
                    f'[{symbol}] 新浪 lscjfb 主力口径顶替成功: '
                    f'main={sina_row["main_net_inflow"]} 万'
                    f'（is_estimated=0，capital_source=sina_main，仅写当日 1 行）'
                )
                return 'fallback', '新浪顶替(主力口径r0+r1；主源恢复后自动回补)'
        except Exception as e:
            warnings.append(f'新浪顶替失败: {e}')
            logger.warning(f'[{symbol}] 新浪 lscjfb 顶替失败: {e}')
        # 019S：THS 顶替块已移除（监理裁定：主力净流入链路不得使用同花顺数据）。
        # 新浪顶替失败（无当日行或异常）→ 直接落回估算兜底（源3/4/5），链路不断（静默降级）。

        # === 估算源3：腾讯K线估算（港股专用fallback，直连不需代理）===
        if saved_count == 0 and market == 'hk_stock':
            try:
                logger.info(f'[{symbol}] 尝试腾讯K线估算（兜底展示）...')
                tencent_rows = _fetch_capital_flow_tencent_hk(symbol, market)
                if tencent_rows:
                    # 019E Task 2.2：估算仅写当日 1 行（不污染历史序列）
                    row = tencent_rows[0]
                    trade_date = str(row.get('日期', '')).strip()
                    if trade_date:
                        main_net = round(float(row.get('主力净流入-净额', 0) or 0), 2)
                        main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)
                        conn = get_connection()
                        cursor = conn.cursor()
                        # 019E Task 2.7（M-5）：UPDATE + INSERT OR IGNORE（禁止 INSERT OR REPLACE，避免清除占位行已有字段）
                        # 019K Task 3：估算 UPDATE 追加来源守卫（防御性——估算不得覆盖 THS 顶替行）
                        # 019S：'ths_total' 字面量保留不动——防御存量 ths_total 行，待存量清零后经新批次评审简化（估算守卫可简化为仅 'sina_main'）
                        cursor.execute(
                            'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, is_estimated=1 '
                            'WHERE stock_id=? AND trade_date=? '
                            "AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main','westock'))",
                            (main_net, main_net_pct, stock_id, trade_date),
                        )
                        if cursor.rowcount == 0:
                            cursor.execute(
                                'INSERT OR IGNORE INTO raw_capital_flow '
                                '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, is_estimated) '
                                'VALUES (?, ?, ?, ?, 1)',
                                (stock_id, trade_date, main_net, main_net_pct),
                            )
                        conn.commit()
                        conn.close()
                        saved_count = 1
                        est_source = '腾讯K线估算'
                        logger.info(f'[{symbol}] 估算兜底成功(腾讯K线估算): {trade_date} 1行')
                else:
                    warnings.append('腾讯K线估算返回空数据')
            except Exception as e:
                warnings.append(f'腾讯K线估算失败: {e}')
                logger.warning(f'[{symbol}] 腾讯K线估算失败: {e}')

        # === 估算源4：新浪财经资金面（直连不需代理）===
        if saved_count == 0:
            try:
                logger.info(f'[{symbol}] 尝试新浪财经估算（兜底展示）...')
                sina_rows = _fetch_capital_flow_sina(symbol, market)
                if sina_rows:
                    row = sina_rows[0]
                    trade_date = str(row.get('日期', '')).strip()
                    if trade_date:
                        main_net = round(float(row.get('主力净流入-净额', 0) or 0), 2)
                        main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)
                        conn = get_connection()
                        cursor = conn.cursor()
                        # 019K Task 3：估算 UPDATE 追加来源守卫（防御性——估算不得覆盖 THS 顶替行）
                        # 019S：'ths_total' 字面量保留不动——防御存量 ths_total 行，待存量清零后经新批次评审简化（估算守卫可简化为仅 'sina_main'）
                        cursor.execute(
                            'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, is_estimated=1 '
                            'WHERE stock_id=? AND trade_date=? '
                            "AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main','westock'))",
                            (main_net, main_net_pct, stock_id, trade_date),
                        )
                        if cursor.rowcount == 0:
                            cursor.execute(
                                'INSERT OR IGNORE INTO raw_capital_flow '
                                '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, is_estimated) '
                                'VALUES (?, ?, ?, ?, 1)',
                                (stock_id, trade_date, main_net, main_net_pct),
                            )
                        conn.commit()
                        conn.close()
                        saved_count = 1
                        est_source = '新浪财经'
                        logger.info(f'[{symbol}] 估算兜底成功(新浪财经): {trade_date} 1行')
                else:
                    warnings.append('新浪财经资金面返回空数据')
            except Exception as e:
                warnings.append(f'新浪财经资金面失败: {e}')
                logger.warning(f'[{symbol}] 新浪财经估算失败: {e}')

        # === 估算源5：网易财经历史资金流向（直连不需代理）===
        if saved_count == 0:
            try:
                logger.info(f'[{symbol}] 尝试网易财经估算（兜底展示）...')
                netease_rows = _fetch_capital_flow_netease(symbol, market)
                if netease_rows:
                    row = netease_rows[0]
                    trade_date = str(row.get('日期', '')).strip()
                    if trade_date:
                        main_net = round(float(row.get('主力净流入-净额', 0) or 0), 2)
                        main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)
                        conn = get_connection()
                        cursor = conn.cursor()
                        # 019K Task 3：估算 UPDATE 追加来源守卫（防御性——估算不得覆盖 THS 顶替行）
                        # 019S：'ths_total' 字面量保留不动——防御存量 ths_total 行，待存量清零后经新批次评审简化（估算守卫可简化为仅 'sina_main'）
                        cursor.execute(
                            'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, is_estimated=1 '
                            'WHERE stock_id=? AND trade_date=? '
                            "AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main','westock'))",
                            (main_net, main_net_pct, stock_id, trade_date),
                        )
                        if cursor.rowcount == 0:
                            cursor.execute(
                                'INSERT OR IGNORE INTO raw_capital_flow '
                                '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, is_estimated) '
                                'VALUES (?, ?, ?, ?, 1)',
                                (stock_id, trade_date, main_net, main_net_pct),
                            )
                        conn.commit()
                        conn.close()
                        saved_count = 1
                        est_source = '网易财经'
                        logger.info(f'[{symbol}] 估算兜底成功(网易财经): {trade_date} 1行')
                else:
                    warnings.append('网易财经资金面返回空数据')
            except Exception as e:
                warnings.append(f'网易财经资金面失败: {e}')
                logger.warning(f'[{symbol}] 网易财经估算失败: {e}')

        # 019E Task 2.8（M-6）：估算成功返回 'estimated'（非 'success'，确保019C回退循环不误计为成功）
        if saved_count > 0 and em_all_failed:
            est_msg = f'估算兜底({est_source})，仅展示用，待东方财富恢复后覆盖'
            save_data_status(stock_id, 'capital', 'estimated', est_msg)
            logger.info(f'[{symbol}] 资金面估算兜底完成: {est_source}，返回estimated')
            return 'estimated', est_msg

    # === 所有数据源均失败时写入error_logs ===
    if saved_count == 0:
        try:
            conn_err = get_connection()
            cursor_err = conn_err.cursor()
            cursor_err.execute(
                """
                INSERT INTO error_logs (stock_id, module, error_type, error_message)
                VALUES (?, ?, ?, ?)
            """,
                (stock_id, 'capital_flow', 'all_sources_failed', '; '.join(warnings)[:500]),
            )
            conn_err.commit()
            conn_err.close()
        except Exception:
            pass

    if saved_count > 0:
        # 检查数据库中已有的历史记录数和最新日期
        conn_chk = get_connection()
        cursor_chk = conn_chk.cursor()
        cursor_chk.execute(
            'SELECT COUNT(*) as cnt, MAX(trade_date) as latest FROM raw_capital_flow WHERE stock_id = ?',
            (stock_id,),
        )
        row_chk = cursor_chk.fetchone()
        total_records = row_chk['cnt']
        latest_cap_date = row_chk['latest']
        conn_chk.close()

        # 检查K线最新日期
        conn_k = get_connection()
        cursor_k = conn_k.cursor()
        cursor_k.execute(
            'SELECT MAX(trade_date) as latest FROM raw_kline WHERE stock_id = ?', (stock_id,)
        )
        latest_kline_date = cursor_k.fetchone()['latest']
        conn_k.close()

        # 日期对齐策略说明（方案B：沿用T-1数据 + 标注截止日）
        date_note = ''
        if latest_kline_date and latest_cap_date and latest_kline_date > latest_cap_date:
            date_note = f'。注意：资金面截止日为{latest_cap_date}（K线最新日为{latest_kline_date}），四维分析时应沿用T-1资金面数据并在报告中标注截止日期'
            logger.info(
                f'[{symbol}] 资金面日期对齐: K线={latest_kline_date}, 资金面={latest_cap_date}, 采用T-1策略'
            )

        msg = f'{source}采集成功，写入 {saved_count} 天有效数据（跳过 {skipped} 天异常数据）。数据库累计{total_records}条记录{date_note}'
        save_data_status(stock_id, 'capital', 'success', msg)
        return 'success', msg
    else:
        all_warnings = '; '.join(warnings) if warnings else '所有数据源均失败'
        save_data_status(stock_id, 'capital', 'failed', all_warnings)
        return 'failed', all_warnings
