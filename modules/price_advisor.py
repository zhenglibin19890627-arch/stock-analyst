"""
009 价格建议增强模块 (Price Advisor Enhanced)

在 generate_advice 返回后调用（后处理集成），不修改 generate_advice（B24红线）。
基于评级结果 + 技术指标 + 持仓成本 + 资金面 + 交易流水，生成结构化价格建议。

增强功能（009）：
  1. 操作建议状态机（S1-S4 × 5评级矩阵，S4破止损禁止加仓）
  2. 止盈价动态化（双约束：技术阻力位 vs 固定止盈 vs 最低止盈）
  3. 网格价位（ATR动态间距，无持仓3档买入，有持仓1补+3减）
  4. 资金面信号转化（7档修饰词，不覆盖基础建议）
  5. 交易流水分析（加仓节奏/成本趋势/买卖时机）

向后兼容：保留所有005字段，新增 grid/capital_signal/trade_analysis/state/action_suggestion。
仅依赖标准库（sqlite3/math/re/datetime），无新 pip 依赖（零代码约束）。
"""

import json
import logging
import os
import re
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import get_connection

logger = logging.getLogger(__name__)


# ================================================================
# 评级 -> 参数映射表（005 基线 + 009 扩展）
# ================================================================

# 无持仓：评级 -> 建议仓位百分比
RATING_POSITION_PCT = {
    '强烈推荐买入': 80,
    '推荐买入': 50,
    '持有观望': 20,
    '建议减仓': 0,
    '强烈建议卖出': 0,
}

# 有持仓：评级 -> 目标涨幅（固定止盈价 = cost * (1 + target_gain)）
RATING_TARGET_GAIN = {
    '强烈推荐买入': 0.25,
    '推荐买入': 0.20,
    '持有观望': 0.12,
    '建议减仓': 0.08,
    '强烈建议卖出': 0.05,
}

# 有持仓：评级 -> 止损比例
RATING_STOP_LOSS = {
    '强烈推荐买入': 0.08,
    '推荐买入': 0.07,
    '持有观望': 0.05,
    '建议减仓': 0.04,
    '强烈建议卖出': 0.03,
}

# 009新增：有持仓 -> 最低目标涨幅（保底止盈价 = cost * (1 + min_target_gain)）
MIN_TARGET_GAIN = {
    '强烈推荐买入': 0.08,
    '推荐买入': 0.06,
    '持有观望': 0.04,
    '建议减仓': 0.03,
    '强烈建议卖出': 0.02,
}

# 021AJ/021AL：目标/止损距离市场校准（依据 price_backtest 真实样本重放模拟）
# A股（021AJ，125 条）：空仓目标中位 +9.8~12.1% vs 20日高点 P75 +7.7% → 命中 15~20%；
#      持仓止损分档 3~8%（建议减仓 -4%）vs 20日低点中位 -5.7% → 触发 66%。
#      校准：目标封顶 +7.5% → 命中 40%；持仓止损 -11% → 触发 16%；RR 0.83 → 2.47+。
# 港股（021AL，28 条）：波幅约 A股 1.5 倍（20日高点 P75 21.4% vs A股 7.7%，与 021P
#      观望带波幅比 1.75 同量级）。空仓目标距中位 +25%（布林上轨/60日线在大波动下
#      推得过远）→ 命中 17%；持仓止损 -5%（持有观望档）→ 触发 40%。
#      校准：目标封顶 +11% → 命中 52%；持仓止损 -16% → 触发 11%；RR 0.97 → 4.87。
# 代价：单次止盈赚得更少、真破位亏得更多——换取目标可达、止损不被日常波动洗出。
TARGET_CAP = {'a_stock': 0.075, 'hk_stock': 0.11}        # 无持仓目标价距现价上限
POSITION_STOP_PCT = {'a_stock': 0.11, 'hk_stock': 0.16}  # 有持仓止损距离（覆盖评级分档）
# 样本提醒：港股真实样本仅 28 条（持仓侧 5 条）——常数为波动率推算初值，
# 样本积累后（≥100 条）应复核（A股 125 条标定同样需随数据滚动复核）。

# 2026-09-18（A 方向，用户拍板）：堵"止盈追涨上移/止损无底线下移"两个动态失真
# ① 止损成本底线：止损价不低于 成本×(1-底线比例)——浮亏有固定最大亏损锁定，
#    A股 -8%（宽于日常波幅不洗出、紧于 -11% 现价锚）；港股波幅 1.5 倍取 -14%。
#    深亏中现价已破底线 → 止损线高于现价 → 状态机 S4 建议清仓（特性而非 bug）
POSITION_COST_FLOOR_PCT = {'a_stock': 0.08, 'hk_stock': 0.14}
# ② 止盈棘轮（滚动窗口）：止盈价 = max(公式值, 近N天历史报告最高止盈)——
#    价格在持有周期内只升不降，堵"每天按新现价重画、永远差一口气"的追逐失真；
#    滚动窗口（10 自然日）而非永久棘轮——长熊中老高位逐步过期，止盈温和回落可成交
TP_RATCHET_WINDOW_DAYS = 10


def _norm_market(market):
    """市场标识归一化：('hk_stock', 'HK') → 'hk_stock'，其余 → 'a_stock'（021AJ）。"""
    return 'hk_stock' if market in ('hk_stock', 'HK') else 'a_stock'

# 有持仓：评级 -> 操作建议文本（005基线，009状态机优先使用）
RATING_ACTION_SUGGESTION = {
    '强烈推荐买入': '加仓20%',
    '推荐买入': '加仓20%',
    '持有观望': '持有',  # 021BH：动作词统一，"持有观望"仅保留为评级档位名
    '建议减仓': '减仓50%',
    '强烈建议卖出': '清仓',
}

# 009新增：状态名称映射
STATE_NAMES = {
    'S1': '已超目标',
    'S2': '浮盈中',
    'S3': '浮亏中',
    'S4': '已破止损',
}

# 009新增：状态 x 评级 -> 操作建议矩阵
# 核心规则：S4（已破止损）必须含'止损'或'清仓'，禁止'加仓'
ACTION_MATRIX = {
    '强烈推荐买入': {
        'S1': '已达目标，分批止盈',
        'S2': '持有，等待止盈',
        'S3': '浮亏中，可逢低补仓',
        'S4': '已破止损，建议止损观望',
    },
    '推荐买入': {
        'S1': '已达目标，建议止盈',
        'S2': '持有，等待止盈',
        'S3': '浮亏中，持有',
        'S4': '已破止损，建议止损',
    },
    '持有观望': {
        'S1': '已达目标，建议止盈',
        'S2': '持有',
        'S3': '浮亏中，持有',
        'S4': '已破止损，建议止损',
    },
    '建议减仓': {
        'S1': '已达目标，建议止盈',
        'S2': '考虑减仓锁定利润',
        'S3': '建议减仓控制风险',
        'S4': '已破止损，建议清仓',
    },
    '强烈建议卖出': {
        'S1': '已达目标，立即止盈',
        'S2': '建议减仓',
        'S3': '建议止损离场',
        'S4': '已破止损，立即清仓',
    },
}

_DISCLAIMER = '以上价格建议仅供参考，不构成投资建议'

# 021AS：减仓/清仓评级的建议减仓比例（展示与区间推导共用）
REDUCE_RATING_PCT = {
    '建议减仓': 50,
    '强烈建议卖出': 100,
}

# 021BF：买入侧评级（无持仓时区间呈现为"买入区间"并给买入网格）；
# 观望档为"参考区间"；减仓/卖出档为"支撑参考区间"且不给买入话术/网格
BUY_SIDE_RATINGS = ('强烈推荐买入', '推荐买入')


def _recent_lows(stock_id):
    """021BG：近期真实低点（20日/60日最低 low），支撑观察梯队的优先锚点。

    Returns:
        tuple: (low20, low60)，数据不足的周期返回 None。
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT low FROM raw_kline WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 60',
            (stock_id,),
        )
        lows = [r['low'] for r in cursor.fetchall() if r['low'] is not None]
        conn.close()
        low20 = min(lows[:20]) if len(lows) >= 5 else None
        low60 = min(lows) if len(lows) >= 20 else None
        return low20, low60
    except Exception as e:
        logger.debug(f'近期低点读取失败 stock_id={stock_id}: {e}')
        return None, None


def _build_support_watch_grid(close, buy_low, atr, low20=None, low60=None):
    """021BG：减仓/卖出评级的支撑观察梯队（观察预案，非买入建议）。

    设计：
    - 真实技术位优先（仅取现价下方）：区间下沿 buy_low（未跌破时）、20日最低、60日最低；
    - ATR 外推补足至 3 档（步长 0.8ATR / 1.2ATR，无 ATR 回退现价百分比）；
    - 严格自上而下递减、去重（间隔 ≥0.01），最多 3 档；
    - pct=None（无仓位语义），type='watch'——前端渲染为灰色"观察"行。
    """
    step1 = atr * 0.8 if (atr and atr > 0) else close * 0.02
    step2 = atr * 1.2 if (atr and atr > 0) else close * 0.035
    levels = []

    def _push(p):
        p = round(p, 2)
        if p > 0 and p < close and (not levels or p <= levels[-1] - 0.01):
            levels.append(p)
            return True
        return False

    for cand in (buy_low, low20, low60):
        if cand and cand > 0:
            _push(cand)
    guard = 0
    while len(levels) < 3 and guard < 6:
        guard += 1
        nxt = (levels[-1] if levels else close) - (step1 if not levels else step2)
        if not _push(nxt):
            break

    labels = ('支撑观察一', '支撑观察二', '支撑观察三')
    return [
        {'level': i + 1, 'price': p, 'pct': None, 'type': 'watch', 'label': labels[i]}
        for i, p in enumerate(levels)
    ]


def _build_reduce_range(close, rating, atr, cost_price):
    """021AS：减仓/清仓评级的建议减仓价格区间。

    设计（价格优先、不追求卖在最高）：
    - 建议减仓：下限=现价（立即执行），上限=现价+0.6ATR（最近反弹位，
      与网格第一止盈位同间距；无 ATR 回退 现价×1.03）；上限再被
      min(成本价, ...) 封顶——浮亏时不诱导"等回本再减"，回本减仓
      属于浮亏网格的档位语义，不混入减仓评级区间。
    - 强烈建议卖出：区间退化 [现价, 现价]（尽快离场，不等反弹）。
    - 其他评级返回 None（不属于减仓语义）。
    """
    pct = REDUCE_RATING_PCT.get(rating)
    if pct is None:
        return None
    if pct >= 100:
        return {'low': round(close, 2), 'high': round(close, 2), 'pct': 100}
    upper = close + atr * 0.6 if (atr and atr > 0) else close * 1.03
    # 浮亏时不给"等回本"的上限（减仓控制风险优先于回本执念）
    if cost_price and cost_price > close:
        upper = min(upper, max(close, cost_price))
    return {'low': round(close, 2), 'high': round(upper, 2), 'pct': pct}


# ================================================================
# 数据读取辅助（005基线，保留不动）
# ================================================================


def _ratchet_take_profit(stock_id, formula_tp, today):
    """止盈棘轮（滚动窗口，2026-09-18 A 方向）。

    取近 TP_RATCHET_WINDOW_DAYS 天历史报告快照（has_position 口径）的最高止盈，
    与公式值取大——止盈价在持有周期内只升不降，堵"每日按新现价重画、
    永远差一口气"的追逐失真；窗口外的老高位自然过期，长熊中温和回落可成交。

    Returns:
        (ratcheted_tp, tp_base)：棘轮后止盈价、棘轮基数（无历史时为 None）
    """
    try:
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        cutoff = (_dt.strptime(str(today)[:10], '%Y-%m-%d') - _td(days=TP_RATCHET_WINDOW_DAYS)).strftime('%Y-%m-%d')
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT price_advice FROM daily_reports
                   WHERE stock_id = ? AND status = 'ok' AND report_type = 'daily'
                   AND report_date >= ? AND report_date < ?
                   AND price_advice IS NOT NULL
                   ORDER BY report_date DESC""",
                (stock_id, cutoff, str(today)[:10]),
            )
            hist_max = None
            for (paj,) in cur.fetchall():
                try:
                    pa = json.loads(paj) if paj else {}
                except (ValueError, TypeError):
                    continue
                if pa.get('has_position') and pa.get('take_profit'):
                    try:
                        v = float(pa['take_profit'])
                    except (TypeError, ValueError):
                        continue
                    hist_max = v if hist_max is None else max(hist_max, v)
        finally:
            conn.close()
        if hist_max is not None:
            return max(formula_tp, hist_max), hist_max
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[price-advisor] 止盈棘轮查询失败 stock_id={stock_id}: {e}')
    return formula_tp, None


def _calc_atr(stock_id, period=14):
    """计算 ATR（Average True Range）

    从 raw_kline 取最近 period+1 天 high/low/close，
    计算每日 TR 再做 period 日 SMA。

    Returns:
        float: ATR 值，或 None（数据不足）
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT high, low, close FROM raw_kline '
            'WHERE stock_id = ? ORDER BY trade_date DESC LIMIT ?',
            (stock_id, period + 1),
        )
        rows = cursor.fetchall()
        conn.close()

        if not rows or len(rows) < 2:
            return None

        rows = list(reversed(rows))

        trs = []
        for i in range(1, len(rows)):
            high = rows[i]['high']
            low = rows[i]['low']
            prev_close = rows[i - 1]['close']
            if high is None or low is None or prev_close is None:
                continue
            tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
            trs.append(tr)

        if not trs:
            return None

        return round(sum(trs) / len(trs), 4)
    except Exception as e:
        logger.debug(f'ATR计算失败 stock_id={stock_id}: {e}')
        return None


def _read_cost_price(stock_id):
    """读取持仓成本价，优先 holdings 表（账户无关聚合），fallback positions 表。

    021BR t3（021W 残留收口）：holdings 改 021BQ 同款 SUM 聚合口径——
    同股多账户分仓取数量汇总 + 加权平均成本（与 trader_advisor/action_list
    三处同口径），修复旧「ORDER BY quantity DESC LIMIT 1」单行取最大账户成本
    导致的止损位混基数（如中免 61.05 单账户 vs 57.53 聚合双数值并存）。
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                'SELECT SUM(quantity) AS total_qty, '
                'CASE WHEN SUM(quantity) > 0 '
                'THEN SUM(quantity * cost_price) / SUM(quantity) END AS avg_cost '
                'FROM holdings WHERE stock_id = ? AND quantity > 0',
                (stock_id,),
            )
            row = cursor.fetchone()
            if (
                row
                and row['total_qty']
                and row['total_qty'] > 0
                and row['avg_cost']
                and row['avg_cost'] > 0
            ):
                conn.close()
                return row['avg_cost']
        except Exception:
            pass

        cursor.execute('SELECT cost_price, quantity FROM positions WHERE stock_id = ?', (stock_id,))
        row = cursor.fetchone()
        conn.close()
        if (
            row
            and row['quantity']
            and row['quantity'] > 0
            and row['cost_price']
            and row['cost_price'] > 0
        ):
            return row['cost_price']
        return None
    except Exception:
        return None


def _safe_get(data_obj, attr):
    """安全读取 StockData 可选字段"""
    if data_obj is None:
        return None
    val = getattr(data_obj, attr, None)
    return val if val else None


# ================================================================
# 009新增：技术阻力位计算（决策点2）
# ================================================================


def _calc_resistance(close, ma60, boll_upper):
    """计算技术面阻力位

    取 boll_upper 和 ma60 中 > close 的最小值（最近阻力位）。
    都不可用时降级为 close * 1.10。
    """
    candidates = []
    if boll_upper and boll_upper > close:
        candidates.append(boll_upper)
    if ma60 and ma60 > close:
        candidates.append(ma60)
    if candidates:
        return min(candidates)
    return close * 1.10


# ================================================================
# 009新增：操作建议状态机（决策点1）
# ================================================================


def _determine_action_by_state(close, cost_price, take_profit, stop_loss, rating):
    """根据当前价与关键价格线的关系，确定状态和操作建议

    状态优先级：S4 > S1 > S3 > S2（破止损最优先）

    Returns:
        tuple: (state_code, state_name, action_suggestion)
    """
    if close < stop_loss:
        state = 'S4'
    elif close >= take_profit:
        state = 'S1'
    elif close > cost_price:
        state = 'S2'
    else:
        state = 'S3'

    state_name = STATE_NAMES.get(state, '')
    rating_actions = ACTION_MATRIX.get(rating, ACTION_MATRIX.get('持有观望', {}))
    action = rating_actions.get(state, '持有')  # 021BH：动作词统一

    return state, state_name, action


# ================================================================
# 009新增：网格价位构建（决策点3）
# ================================================================


def _build_grid(
    close,
    buy_range_low,
    buy_range_high,
    atr,
    cost_price,
    take_profit,
    stop_loss,
    rating,
    has_position,
    state=None,
):
    """构建网格价位计划

    无持仓：3档买入网格（ATR*0.8间距）
    有持仓（020P 锚定现价；021AQ 浮亏重构）：
      - 两级止损：止损清仓位（100%，止损价-1ATR）→ 破位减仓位（50% @止损价）
        ——跌破止损先减半控损，减半后仍下行则清仓离场，不再"没到回本价就躺平"
      - 分批补仓：补仓一档（10%，max(止损+0.5ATR, 现价-1ATR)——回测口径锚点，
        公式勿改）→ 补仓二档（15%，现价-2.2ATR，与一档拉开间距，合计≤25%）
      - 浮亏且止盈目标 < 回本价：回本减仓位（50% @回本价）→ 回本清仓位
        （100%，回本价+0.6ATR）——先落袋一半，剩余略上方清掉，不一把梭
      - 浮亏且止盈目标 ≥ 回本价：回本减仓位（30%）→ 第一止盈位（50%）→ 最终止盈位（100%）
      - 浮盈：第一止盈位（50%，现价+0.6ATR）→ 最终止盈位（100%）
      - S4已破止损（防御分支）：只给离场档不给补仓档
    全部档位严格自下而上递增，锚定现价与成本孰高，不与止盈/止损档位倒挂。
    """
    grid = []

    if not has_position:
        # ---- 无持仓：3档买入网格 ----
        grid.append(
            {
                'level': 1,
                'price': round(buy_range_low, 2),
                'pct': 40,
                'type': 'buy',
                'label': '第一买入位',
            }
        )

        if atr and atr > 0:
            mid_price = buy_range_low + atr * 0.8
            # 确保第二买入位在第一和第三之间
            if mid_price < buy_range_high - 0.01:
                grid.append(
                    {
                        'level': 2,
                        'price': round(mid_price, 2),
                        'pct': 35,
                        'type': 'buy',
                        'label': '第二买入位',
                    }
                )

        grid.append(
            {
                'level': len(grid) + 1,
                'price': round(buy_range_high, 2),
                'pct': 25,
                'type': 'buy',
                'label': '第三买入位',
            }
        )

    else:
        # ---- 有持仓：021AQ 浮亏网格重构（两级止损 + 分批补仓 + 分批回本）----
        level = 1

        if state == 'S4':
            # 已破止损（防御分支，现价锚定止损下常态不可达）：只给离场档
            if atr and atr > 0:
                exit_price = max(close - atr * 1.0, close * 0.95)
            else:
                exit_price = close * 0.95
            grid.append(
                {
                    'level': level,
                    'price': round(exit_price, 2),
                    'pct': 100,
                    'type': 'reduce',
                    'label': '止损清仓位',
                }
            )
            level += 1
            # 反抽减仓：反弹离场位（止损线在现价上方时用止损线，否则现价略上方）
            rebound = max(stop_loss, close * 1.02)
            grid.append(
                {
                    'level': level,
                    'price': round(rebound, 2),
                    'pct': 50,
                    'type': 'reduce',
                    'label': '反抽减仓位',
                }
            )
            level += 1
        else:
            # 两级止损（自下而上）：先减半控损，减半后仍下行则清仓
            if atr and atr > 0:
                hard_exit = max(stop_loss - atr * 1.0, stop_loss * 0.96)
            else:
                hard_exit = stop_loss * 0.96
            grid.append(
                {
                    'level': level,
                    'price': round(hard_exit, 2),
                    'pct': 100,
                    'type': 'reduce',
                    'label': '止损清仓位',
                }
            )
            level += 1
            grid.append(
                {
                    'level': level,
                    'price': round(stop_loss, 2),
                    'pct': 50,
                    'type': 'reduce',
                    'label': '破位减仓位',
                }
            )
            level += 1

            # 分批补仓：一档公式为回测补仓区间口径锚点（勿改），二档拉开间距；
            # 列表按价格自下而上（二档更深在前、一档在后），执行顺序按价格从高到低
            if atr and atr > 0:
                add1 = max(stop_loss + atr * 0.5, close - atr * 1.0)
            else:
                add1 = close * 0.97
            if atr and atr > 0:
                add2 = max(stop_loss + atr * 0.5, close - atr * 2.2)
                if add2 <= add1 - atr * 0.3:
                    grid.append(
                        {
                            'level': level,
                            'price': round(add2, 2),
                            'pct': 15,
                            'type': 'add',
                            'label': '补仓二档',
                        }
                    )
                    level += 1
            grid.append(
                {
                    'level': level,
                    'price': round(add1, 2),
                    'pct': 10,
                    'type': 'add',
                    'label': '补仓一档',
                }
            )
            level += 1

        _underwater = cost_price is not None and close < cost_price

        if _underwater and take_profit < cost_price:
            # 浮亏且止盈目标低于回本价：回本先落袋一半，剩余略上方清掉
            # （021AQ：替代原"回本清仓100%"一把梭——死等回本可能等不到，
            #   分批离场兼顾解套与反弹两头）
            grid.append(
                {
                    'level': level,
                    'price': round(cost_price, 2),
                    'pct': 50,
                    'type': 'reduce',
                    'label': '回本减仓位',
                }
            )
            level += 1
            if atr and atr > 0:
                recover_exit = max(cost_price + atr * 0.6, cost_price * 1.02)
            else:
                recover_exit = cost_price * 1.03
            grid.append(
                {
                    'level': level,
                    'price': round(recover_exit, 2),
                    'pct': 100,
                    'type': 'reduce',
                    # 2026-09-09：原"回本清仓位"名不副实——recover_exit =
                    # max(成本+0.6ATR, 成本×1.02) 高于成本，是回本之后再清的价位
                    'label': '回本后清仓位',
                }
            )
        else:
            if _underwater:
                # 浮亏但止盈目标在回本价上方：先回本减仓，再向上分批止盈
                grid.append(
                    {
                        'level': level,
                        'price': round(cost_price, 2),
                        'pct': 30,
                        'type': 'reduce',
                        'label': '回本减仓位',
                    }
                )
                level += 1
                if atr and atr > 0:
                    tp1 = max(cost_price + atr * 0.6, close + atr * 0.6)
                else:
                    tp1 = max(cost_price * 1.03, close * 1.03)
            else:
                # 浮盈：现价上方直接分批止盈
                if atr and atr > 0:
                    tp1 = close + atr * 0.6
                else:
                    tp1 = close * 1.03

            # 第一止盈位（必须在最终止盈价下方才有意义）
            if tp1 < take_profit - 0.01:
                grid.append(
                    {
                        'level': level,
                        'price': round(tp1, 2),
                        'pct': 50,
                        'type': 'reduce',
                        'label': '第一止盈位',
                    }
                )
                level += 1

            # 最终止盈位
            grid.append(
                {
                    'level': level,
                    'price': round(take_profit, 2),
                    'pct': 100,
                    'type': 'reduce',
                    'label': '最终止盈位',
                }
            )

    return grid


# ================================================================
# 009新增：资金面因子解析与信号分类（决策点4）
# ================================================================


def _parse_capital_factors(factors):
    """解析资金面因子文本为结构化数据

    输入示例：
        {'main_trend': '主力净流入21800万元',
         'consecutive': '连续净流入2日',
         'super_large': '超大单净8000万元(流入)'}

    Returns:
        dict: {main_inflow, consecutive_days, super_large_inflow, ...}
    """
    result = {
        'main_inflow': None,
        'main_pct': None,
        'consecutive_days': 0,
        'super_large_inflow': None,
        'avg_5d_inflow': None,
    }

    if not factors or not isinstance(factors, dict):
        return result

    try:
        # main_trend: '主力净流入21800万元' -> 21800
        mt = factors.get('main_trend', '')
        if mt:
            m = re.search(r'净(流入|流出)([\d.]+)万', str(mt))
            if m:
                val = float(m.group(2))
                result['main_inflow'] = val if m.group(1) == '流入' else -val

        # consecutive: '连续净流入2日' -> +2
        consec = factors.get('consecutive', '')
        if consec:
            m = re.search(r'连续净(流入|流出)(\d+)日', str(consec))
            if m:
                val = int(m.group(2))
                result['consecutive_days'] = val if m.group(1) == '流入' else -val

        # super_large: '超大单净8000万元(流入)' -> +8000
        sl = factors.get('super_large', '')
        if sl:
            m = re.search(r'超大单净([\d.]+)万.*\((流入|流出)\)', str(sl))
            if m:
                val = float(m.group(1))
                result['super_large_inflow'] = val if m.group(2) == '流入' else -val

        # main_pct: '5.23%' -> 5.23
        pct = factors.get('main_pct', '')
        if pct:
            m = re.search(r'(-?[\d.]+)%', str(pct))
            if m:
                result['main_pct'] = float(m.group(1))

        # main_avg_5d: '5日均净流入12345万元' -> 12345
        avg5 = factors.get('main_avg_5d', '')
        if avg5:
            m = re.search(r'均净(流入|流出)([\d.]+)万', str(avg5))
            if m:
                val = float(m.group(2))
                result['avg_5d_inflow'] = val if m.group(1) == '流入' else -val

    except Exception as e:
        logger.debug(f'资金面因子解析失败: {e}')

    return result


def _classify_capital_signal(parsed):
    """将解析后的资金面数据分类为7档信号(-2 ~ +2)

    Returns:
        dict or None: {strength, label, modifier, risk_warning}
    """
    main = parsed.get('main_inflow')
    consec = parsed.get('consecutive_days', 0)
    super_large = parsed.get('super_large_inflow')

    # 完全没有资金面数据时返回None
    if main is None and consec == 0 and super_large is None:
        return None

    strength = 0
    label = '中性'
    modifier = ''
    risk_warning = None

    if main is not None:
        if consec >= 3 and main > 0:
            strength = 2
            label = '强流入'
            modifier = '资金面强支撑，'
        elif consec >= 2 and main > 0:
            strength = 1
            label = '中流入'
            modifier = '资金面偏积极，'
        elif main > 0:
            strength = 0.5
            label = '弱流入'
            modifier = '，资金面略有流入'
        elif consec <= -3 and main < 0:
            strength = -2
            label = '强流出'
            modifier = '资金面明显流出，'
        elif consec <= -2 and main < 0:
            strength = -1
            label = '中流出'
            modifier = '资金面偏弱，'
        elif main < 0:
            strength = -0.5
            label = '弱流出'
            modifier = '，注意资金面略有流出'

    # 超大单异常流出风险提示
    if super_large is not None and super_large < -5000:
        risk_warning = '超大单大幅流出，警惕主力撤离'

    if strength == 0 and not risk_warning:
        return None

    return {
        'strength': strength,
        'label': label,
        'modifier': modifier,
        'risk_warning': risk_warning,
    }


def _apply_capital_modifier(action_suggestion, capital_signal):
    """将资金面修饰词应用到操作建议（强信号前置，弱信号后置）"""
    if not capital_signal:
        return action_suggestion
    modifier = capital_signal.get('modifier')
    if not modifier:
        return action_suggestion
    strength = capital_signal.get('strength', 0)
    if abs(strength) >= 1:
        return modifier + action_suggestion
    else:
        return action_suggestion + modifier


# ================================================================
# 009新增：交易流水分析（决策点5）
# ================================================================


def _parse_date(date_str):
    """安全解析日期字符串 (YYYY-MM-DD)"""
    if not date_str:
        return None
    try:
        return datetime.strptime(str(date_str)[:10], '%Y-%m-%d')
    except (ValueError, TypeError):
        return None


def _analyze_trade_rhythm(buys):
    """维度1：加仓节奏分析"""
    try:
        intervals = []
        for i in range(1, len(buys)):
            d1 = _parse_date(buys[i - 1]['trade_date'])
            d2 = _parse_date(buys[i]['trade_date'])
            if d1 and d2:
                intervals.append((d2 - d1).days)

        if not intervals:
            return None

        avg_interval = sum(intervals) / len(intervals)

        if avg_interval <= 3:
            return {
                'pattern': '频繁加仓',
                'avg_interval': round(avg_interval, 1),
                'risk': '追涨风险较高',
            }
        elif avg_interval <= 10:
            return {'pattern': '分批建仓', 'avg_interval': round(avg_interval, 1), 'risk': None}
        else:
            return {'pattern': '低频加仓', 'avg_interval': round(avg_interval, 1), 'risk': None}
    except Exception:
        return None


def _analyze_cost_trend(buys):
    """维度2：成本变化趋势"""
    try:
        sorted_buys = sorted(buys, key=lambda x: x['trade_date'] or '')
        costs = []
        total_qty = 0
        total_amount = 0.0
        for b in sorted_buys:
            qty = b['quantity'] or 0
            price = b['price'] or 0
            if qty > 0 and price > 0:
                total_qty += qty
                total_amount += price * qty
                costs.append(total_amount / total_qty)

        if len(costs) < 2:
            return None

        if costs[-1] < costs[0]:
            trend = 'down'
            suggestion = '低位补仓有效摊薄成本'
        elif costs[-1] > costs[0]:
            trend = 'up'
            suggestion = '注意追高加仓推高成本'
        else:
            trend = 'flat'
            suggestion = '成本保持稳定'

        return {
            'trend': trend,
            'first_cost': round(costs[0], 2),
            'last_cost': round(costs[-1], 2),
            'suggestion': suggestion,
        }
    except Exception:
        return None


def _analyze_trade_timing(rows):
    """维度3：买卖时机统计（FIFO配对）"""
    try:
        pairs = []
        buy_queue = []

        for t in sorted(rows, key=lambda x: x['trade_date'] or ''):
            if t['trade_type'] == 'buy':
                buy_queue.append(t)
            elif t['trade_type'] == 'sell' and buy_queue:
                buy = buy_queue.pop(0)
                buy_price = buy['price'] or 0
                sell_price = t['price'] or 0
                if buy_price > 0:
                    profit = (sell_price - buy_price) / buy_price * 100
                    pairs.append(
                        {
                            'buy_date': buy['trade_date'],
                            'sell_date': t['trade_date'],
                            'profit_pct': round(profit, 1),
                        }
                    )

        if not pairs:
            return None

        wins = [p for p in pairs if p['profit_pct'] > 0]
        win_rate = len(wins) / len(pairs) * 100
        avg_profit = sum(p['profit_pct'] for p in pairs) / len(pairs)

        return {
            'total_trades': len(pairs),
            'win_rate': round(win_rate, 1),
            'avg_profit_pct': round(avg_profit, 1),
        }
    except Exception:
        return None


def _analyze_trade_records(stock_id):
    """分析交易流水（加仓节奏/成本趋势/买卖时机）"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT trade_type, price, quantity, trade_date '
            'FROM trade_records WHERE stock_id=? ORDER BY trade_date',
            (stock_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        if not rows:
            return {'available': False, 'reason': '无交易记录'}

        buys = [r for r in rows if r['trade_type'] == 'buy']

        if len(buys) < 2:
            return {'available': False, 'reason': '买入记录不足2笔'}

        rhythm = _analyze_trade_rhythm(buys)
        cost_trend = _analyze_cost_trend(buys)
        timing = _analyze_trade_timing(rows)

        # 汇总摘要
        parts = []
        if rhythm:
            parts.append(rhythm['pattern'])
        if cost_trend:
            parts.append(cost_trend['suggestion'])
        if timing:
            parts.append(f'历史胜率{timing["win_rate"]:.0f}%')

        summary = '，'.join(parts) if parts else '交易数据不足'

        return {
            'available': True,
            'trade_count': len(rows),
            'rhythm': rhythm,
            'cost_trend': cost_trend,
            'timing': timing,
            'summary': summary,
        }
    except Exception as e:
        logger.debug(f'交易流水分析失败 stock_id={stock_id}: {e}')
        return {'available': False, 'reason': f'分析异常: {e}'}


# ================================================================
# 核心算法：无持仓模式（009增强）
# ================================================================


def _gen_no_position(close, rating, ma20, ma60, boll_upper, boll_lower, atr, capital_signal=None,
                     market='a_stock', low20=None, low60=None):
    """无持仓：买入区间 / 目标价 / 止损价 / 建议仓位 / 网格 / 操作建议"""

    position_pct = RATING_POSITION_PCT.get(rating, 0)

    # ---- 买入中枢 ----
    pivot = ma20 if ma20 and ma20 > 0 else close

    # ---- 买入区间 ----
    if atr and atr > 0:
        buy_low = pivot - atr * 0.5
        buy_high = pivot + atr * 0.3
    else:
        buy_low = close * 0.97
        buy_high = close * 1.03

    # 约束1: boll_lower 可用时扩展下限
    if boll_lower and boll_lower > 0 and boll_lower < buy_low:
        buy_low = boll_lower

    # 约束2: 买入上限不超过 close * 1.05
    max_high = close * 1.05
    buy_high = min(buy_high, max_high)

    buy_low = min(buy_low, buy_high)

    # ---- 目标价 ----
    if boll_upper and ma60:
        target_price = max(boll_upper, ma60)
    elif boll_upper:
        target_price = boll_upper
    elif ma60:
        target_price = ma60
    else:
        target_price = close * 1.10

    min_target = close * 1.05
    target_price = max(target_price, min_target)

    # 021AJ：目标价封顶（市场校准）——目标定在 20 日波幅可达高度（实测 P75≈+7.7%），
    # 避免 max(boll_upper, ma60) 在宽幅期把目标推到摸不到的位置（命中仅 15~20% → 40%）
    cap_gain = TARGET_CAP.get(_norm_market(market))
    if cap_gain:
        target_price = min(target_price, close * (1 + cap_gain))

    # ---- 止损价 ----
    if atr and atr > 0:
        stop_loss = buy_low - atr * 1.5
    else:
        stop_loss = close * 0.95

    # ---- 预期涨幅/最大回撤 ----
    expected_gain_pct = round((target_price - close) / close * 100, 1)
    max_loss_pct = round((stop_loss - close) / close * 100, 1)

    # ---- 021BF：区间语义随评级（修复"评级建议减仓却说可逢低买入"的矛盾）----
    # 买入档：买入区间 + 逢低买入话术 + 买入网格；
    # 观望档：参考区间 + 同结构话术（与建议仓位20%一致）；
    # 减仓/卖出档：区间退化为技术支撑参考，不给买入话术、不给买入网格（与仓位0%一致）
    if rating in REDUCE_RATING_PCT:
        zone_label = '支撑参考区间'
        action_suggestion = f'评级为{rating}（建议仓位0%），下方价格仅为技术支撑参考，不建议买入'
        # 021BG：支撑观察梯队（观察预案，非买入建议）——真实低点优先，ATR 外推补足
        grid = _build_support_watch_grid(close, buy_low, atr, low20=low20, low60=low60)
    else:
        zone_label = '买入区间' if rating in BUY_SIDE_RATINGS else '参考区间'
        if close < buy_low:
            action_suggestion = f'当前价低于{zone_label}，可逢低买入'
        elif close <= buy_high:
            action_suggestion = f'当前价在{zone_label}内，可按计划买入'
        else:
            action_suggestion = f'当前价高于{zone_label}，建议等待回调'
        # ---- 009新增：网格买入计划 ----
        grid = _build_grid(close, buy_low, buy_high, atr, None, None, None, rating, has_position=False)

    action_suggestion = _apply_capital_modifier(action_suggestion, capital_signal)

    return {
        'available': True,
        'has_position': False,
        'position_pct': position_pct,
        'buy_range_low': round(buy_low, 2),
        'buy_range_high': round(buy_high, 2),
        'zone_label': zone_label,  # 021BF：区间语义标签（买入区间/参考区间/支撑参考区间）
        'target_price': round(target_price, 2),
        'stop_loss': round(stop_loss, 2),
        'current_close': round(close, 2),
        'expected_gain_pct': expected_gain_pct,
        'max_loss_pct': max_loss_pct,
        'action_suggestion': action_suggestion,
        'grid': grid,
        'capital_signal': capital_signal,
        'disclaimer': _DISCLAIMER,
    }


# ================================================================
# 核心算法：有持仓模式（009重写状态机）
# ================================================================


def _gen_with_position(close, cost_price, rating, ma60, boll_upper, atr, capital_signal=None,
                       market='a_stock', stock_id=None, ref_date=None):
    """有持仓：状态机 / 动态止盈 / 网格 / 操作建议 / 浮盈

    020P：止盈/止损锚定现价（与成本解耦）——市场不看个人成本，
    目标与止损只由 评级档位 + 现价 + 技术阻力 决定；
    成本仅用于浮盈浮亏展示与网格回本位。

    2026-09-18（A 方向）：两个稳定锚定修正——
    ① 止损成本底线：stop_loss = max(现价×(1-止损比例), 成本×(1-底线比例))，
       浮亏有固定最大亏损锁定，不再无底线下移；深亏破底线时止损线高于现价，
       状态机判 S4 建议清仓（特性）。
    ② 止盈棘轮（滚动窗口）：take_profit = max(公式值, 近N天历史最高止盈)，
       持有期内只升不降，堵"每日重画、永远差一口气"的追逐失真。
    """

    target_gain = RATING_TARGET_GAIN.get(rating, 0.12)
    stop_loss_pct = RATING_STOP_LOSS.get(rating, 0.05)
    min_target_gain = MIN_TARGET_GAIN.get(rating, 0.04)

    # 021AJ/021AL：止损距离市场校准——A股评级分档 3~8%（建议减仓 -4%）实测 T+20 触发
    # 66%（20 日低点中位 -5.7%，日常波动即砸穿）→ 统一 -11%；港股 -5%（持有观望档）
    # 触发 40% → -16%（波幅约 A股 1.5 倍，20日低点 P25 -13%）。
    calibrated_stop = POSITION_STOP_PCT.get(_norm_market(market))
    if calibrated_stop is not None:
        stop_loss_pct = calibrated_stop

    # ---- 020P：止盈价锚定现价（双约束公式）----
    # 固定止盈价 = close * (1 + target_gain)
    # 技术阻力位 = _calc_resistance(close, ma60, boll_upper)
    # 最低止盈价 = close * (1 + min_target_gain)
    # 止盈价 = max(最低止盈价, min(固定止盈价, 技术阻力位))
    fixed_tp = close * (1 + target_gain)
    resistance = _calc_resistance(close, ma60, boll_upper)
    min_tp = close * (1 + min_target_gain)
    take_profit = max(min_tp, min(fixed_tp, resistance))

    # ---- 2026-09-18：止盈棘轮（滚动窗口）——持有期内只升不降 ----
    tp_ratchet_base = None
    if stock_id is not None:
        take_profit, tp_ratchet_base = _ratchet_take_profit(
            stock_id, take_profit, ref_date or datetime.now().strftime('%Y-%m-%d')
        )

    # ---- 020P：止损价锚定现价（评级止损比例）+ 2026-09-18 成本绝对底线 ----
    stop_loss = close * (1 - stop_loss_pct)
    floor_pct = POSITION_COST_FLOOR_PCT.get(_norm_market(market), 0.08)
    stop_cost_floor = None
    if cost_price and cost_price > 0:
        stop_cost_floor = cost_price * (1 - floor_pct)
        stop_loss = max(stop_loss, stop_cost_floor)

    # ---- 009新增：操作建议状态机 ----
    state, state_name, action_suggestion = _determine_action_by_state(
        close, cost_price, take_profit, stop_loss, rating
    )

    action_suggestion = _apply_capital_modifier(action_suggestion, capital_signal)

    # ---- 浮盈百分比 ----
    profit_pct = round((close - cost_price) / cost_price * 100, 1)

    # ---- 009新增：网格操作计划 ----
    grid = _build_grid(
        close,
        None,
        None,
        atr,
        cost_price,
        take_profit,
        stop_loss,
        rating,
        has_position=True,
        state=state,
    )

    # 021AS：减仓/清仓评级给出建议减仓价格区间
    reduce_range = _build_reduce_range(close, rating, atr, cost_price)

    return {
        'available': True,
        'has_position': True,
        'take_profit': round(take_profit, 2),
        'stop_loss': round(stop_loss, 2),
        'cost_price': round(cost_price, 2),
        'current_close': round(close, 2),
        'profit_pct': profit_pct,
        'state': state,
        'state_name': state_name,
        'action_suggestion': action_suggestion,
        'grid': grid,
        'reduce_range': reduce_range,  # 021AS：减仓/清仓评级的建议减仓区间
        # 2026-09-18 A 方向：稳定锚定元数据（前端展示"棘轮来源/成本底线"用）
        'tp_ratchet_base': round(tp_ratchet_base, 2) if tp_ratchet_base else None,
        'stop_cost_floor': round(stop_cost_floor, 2) if stop_cost_floor else None,
        'capital_signal': capital_signal,
        'disclaimer': _DISCLAIMER,
    }


# ================================================================
# 主入口（009增强）
# ================================================================


def generate_price_advice(stock_id, advice_result):
    """
    根据评级建议结果，生成价格建议（后处理集成，不修改 generate_advice）。

    Args:
        stock_id: 股票 ID
        advice_result: generate_advice 的返回字典
                       （含 rating/has_position/latest_close/dimensions 等）

    Returns:
        dict: 价格建议字典（含 grid/capital_signal/trade_analysis/state 等增强字段）
    """
    try:
        # 1. 提取基础信息
        close = advice_result.get('latest_close')
        if not close or close <= 0:
            return {'available': False, 'reason': '停牌或数据不足'}

        rating = advice_result.get('rating', '')

        # 2. 独立判断持仓状态（不依赖 advice_result['has_position']）
        cost_price = _read_cost_price(stock_id)
        has_position = cost_price is not None and cost_price > 0

        # 3. 加载 StockData 获取技术指标
        try:
            from modules.data_adapter import load_stockdata_from_db

            stock_data = load_stockdata_from_db(stock_id)
        except Exception as e:
            logger.debug(f'load_stockdata_from_db 失败 stock_id={stock_id}: {e}')
            stock_data = None

        ma20 = _safe_get(stock_data, 'ma20')
        ma60 = _safe_get(stock_data, 'ma60')
        boll_upper = _safe_get(stock_data, 'boll_upper')
        boll_lower = _safe_get(stock_data, 'boll_lower')

        # 4. 计算 ATR
        atr = _calc_atr(stock_id)

        # 4b. 021BG：近期真实低点（支撑观察梯队锚点，无持仓减仓分支使用）
        low20, low60 = _recent_lows(stock_id)

        # 5. 009新增：解析资金面因子
        capital_factors = {}
        try:
            dims = advice_result.get('dimensions', {})
            capital_dim = dims.get('capital_flow') or dims.get('capital') or {}
            capital_factors = capital_dim.get('factors', {}) or {}
        except Exception:
            pass

        parsed_capital = _parse_capital_factors(capital_factors)
        capital_signal = _classify_capital_signal(parsed_capital)

        # 6. 生成建议（021AJ：传市场——A股目标封顶/止损校准，港股暂沿用旧逻辑）
        advice_market = advice_result.get('market') or 'a_stock'
        if has_position and cost_price and cost_price > 0:
            result = _gen_with_position(
                close, cost_price, rating, ma60, boll_upper, atr, capital_signal,
                market=advice_market,
                stock_id=stock_id,
                ref_date=advice_result.get('rating_date') or datetime.now().strftime('%Y-%m-%d'),
            )
            # 009新增：交易流水分析
            result['trade_analysis'] = _analyze_trade_records(stock_id)
            return result

        return _gen_no_position(
            close, rating, ma20, ma60, boll_upper, boll_lower, atr, capital_signal,
            market=advice_market, low20=low20, low60=low60
        )

    except Exception as e:
        logger.error(f'generate_price_advice 异常 stock_id={stock_id}: {e}', exc_info=True)
        return {'available': False, 'reason': f'计算异常: {e}'}
