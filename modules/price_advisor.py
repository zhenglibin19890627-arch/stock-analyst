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

# 有持仓：评级 -> 操作建议文本（005基线，009状态机优先使用）
RATING_ACTION_SUGGESTION = {
    '强烈推荐买入': '加仓20%',
    '推荐买入': '加仓20%',
    '持有观望': '持有观望',
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
        'S3': '浮亏中，持有观望',
        'S4': '已破止损，建议止损',
    },
    '持有观望': {
        'S1': '已达目标，建议止盈',
        'S2': '持有观望',
        'S3': '浮亏中，持有观望',
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


# ================================================================
# 数据读取辅助（005基线，保留不动）
# ================================================================


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
    """读取持仓成本价，优先 holdings 表，fallback positions 表"""
    try:
        conn = get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute(
                'SELECT cost_price, quantity FROM holdings '
                "WHERE stock_id = ? AND status = 'active'",
                (stock_id,),
            )
            row = cursor.fetchone()
            if (
                row
                and row['quantity']
                and row['quantity'] > 0
                and row['cost_price']
                and row['cost_price'] > 0
            ):
                conn.close()
                return row['cost_price']
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
    action = rating_actions.get(state, '持有观望')

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
    有持仓：1补+3减网格（S4已破止损时跳过补仓位）
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
        # ---- 有持仓：补仓 + 减仓网格 ----
        level = 1

        # 补仓位（S4已破止损时跳过，避免"破止损仍加仓"矛盾）
        if state != 'S4':
            if atr and atr > 0:
                add_price = max(stop_loss + atr * 0.5, close - atr * 1.0)
            else:
                add_price = close * 0.97
            grid.append(
                {
                    'level': level,
                    'price': round(add_price, 2),
                    'pct': 10,
                    'type': 'add',
                    'label': '补仓位',
                }
            )
            level += 1

        # 回本减仓位
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

        # 第一止盈位
        if atr and atr > 0:
            tp1 = cost_price + atr * 0.6
        else:
            tp1 = cost_price * 1.03
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


def _gen_no_position(close, rating, ma20, ma60, boll_upper, boll_lower, atr, capital_signal=None):
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

    # ---- 止损价 ----
    if atr and atr > 0:
        stop_loss = buy_low - atr * 1.5
    else:
        stop_loss = close * 0.95

    # ---- 预期涨幅/最大回撤 ----
    expected_gain_pct = round((target_price - close) / close * 100, 1)
    max_loss_pct = round((stop_loss - close) / close * 100, 1)

    # ---- 009新增：操作建议（感知价位）----
    if close < buy_low:
        action_suggestion = '当前价低于买入区间，可逢低买入'
    elif close <= buy_high:
        action_suggestion = '当前价在买入区间内，可按计划买入'
    else:
        action_suggestion = '当前价高于买入区间，建议等待回调'

    action_suggestion = _apply_capital_modifier(action_suggestion, capital_signal)

    # ---- 009新增：网格买入计划 ----
    grid = _build_grid(close, buy_low, buy_high, atr, None, None, None, rating, has_position=False)

    return {
        'available': True,
        'has_position': False,
        'position_pct': position_pct,
        'buy_range_low': round(buy_low, 2),
        'buy_range_high': round(buy_high, 2),
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


def _gen_with_position(close, cost_price, rating, ma60, boll_upper, atr, capital_signal=None):
    """有持仓：状态机 / 动态止盈 / 网格 / 操作建议 / 浮盈

    020P：止盈/止损锚定现价（与成本解耦）——市场不看个人成本，
    目标与止损只由 评级档位 + 现价 + 技术阻力 决定；
    成本仅用于浮盈浮亏展示与网格回本位。
    """

    target_gain = RATING_TARGET_GAIN.get(rating, 0.12)
    stop_loss_pct = RATING_STOP_LOSS.get(rating, 0.05)
    min_target_gain = MIN_TARGET_GAIN.get(rating, 0.04)

    # ---- 020P：止盈价锚定现价（双约束公式）----
    # 固定止盈价 = close * (1 + target_gain)
    # 技术阻力位 = _calc_resistance(close, ma60, boll_upper)
    # 最低止盈价 = close * (1 + min_target_gain)
    # 止盈价 = max(最低止盈价, min(固定止盈价, 技术阻力位))
    fixed_tp = close * (1 + target_gain)
    resistance = _calc_resistance(close, ma60, boll_upper)
    min_tp = close * (1 + min_target_gain)
    take_profit = max(min_tp, min(fixed_tp, resistance))

    # ---- 020P：止损价锚定现价（评级止损比例）----
    stop_loss = close * (1 - stop_loss_pct)

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

        # 6. 生成建议
        if has_position and cost_price and cost_price > 0:
            result = _gen_with_position(
                close, cost_price, rating, ma60, boll_upper, atr, capital_signal
            )
            # 009新增：交易流水分析
            result['trade_analysis'] = _analyze_trade_records(stock_id)
            return result

        return _gen_no_position(
            close, rating, ma20, ma60, boll_upper, boll_lower, atr, capital_signal
        )

    except Exception as e:
        logger.error(f'generate_price_advice 异常 stock_id={stock_id}: {e}', exc_info=True)
        return {'available': False, 'reason': f'计算异常: {e}'}
