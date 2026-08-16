"""
007 价格建议回测验证模块 (Price Backtest)

验证 005 价格建议算法的命中率。采用方案B（新建独立回测）+ T+5/T+20 双周期。
不修改 price_advisor.py（红线），复制算法常量保持同步。

回测逻辑：
  1. 对每只股票，使用当前最新评级（与历史模拟回测同口径；run_historical_simulation 已随 021D 删除）
  2. 从第35个交易日开始，每隔5天取一个回测点
  3. 在每个回测点，用该日之前的K线计算历史技术指标，生成价格建议
  4. 取后续T+5/T+20的K线，基于日内high/low判定命中
  5. 写入 price_backtest_results 表

仅依赖标准库 + sqlite3，无新 pip 依赖（零代码约束）。
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import (
    _ensure_price_backtest_columns,
    backup_database,
    get_connection,
)
from modules.data_adapter import _calc_bollinger, _calc_ma

logger = logging.getLogger(__name__)


# ================================================================
# 评级→参数映射表（从 price_advisor.py 复制，不 import，保持同步）
# ================================================================

RATING_POSITION_PCT = {
    '强烈推荐买入': 80,
    '推荐买入': 50,
    '持有观望': 20,
    '建议减仓': 0,
    '强烈建议卖出': 0,
}

RATING_TARGET_GAIN = {
    '强烈推荐买入': 0.25,
    '推荐买入': 0.20,
    '持有观望': 0.12,
    '建议减仓': 0.08,
    '强烈建议卖出': 0.05,
}

RATING_STOP_LOSS = {
    '强烈推荐买入': 0.08,
    '推荐买入': 0.07,
    '持有观望': 0.05,
    '建议减仓': 0.04,
    '强烈建议卖出': 0.03,
}

RATING_ACTION_SUGGESTION = {
    '强烈推荐买入': '加仓20%',
    '推荐买入': '加仓20%',
    '持有观望': '持有观望',
    '建议减仓': '减仓50%',
    '强烈建议卖出': '清仓',
}

# 009同步：有持仓 -> 最低目标涨幅（保底止盈价 = cost * (1 + min_target_gain)）
# 与 price_advisor.py L64-70 同步，修改时需双向同步
MIN_TARGET_GAIN = {
    '强烈推荐买入': 0.08,
    '推荐买入': 0.06,
    '持有观望': 0.04,
    '建议减仓': 0.03,
    '强烈建议卖出': 0.02,
}


# ================================================================
# 1. 历史技术指标计算
# ================================================================


def _calc_historical_atr(kline_rows, period=14):
    """从K线行列表计算ATR（Average True Range）

    与 price_advisor._calc_atr 算法一致：
    取最近 period+1 天 high/low/close，计算每日TR再做period日SMA。

    Args:
        kline_rows: K线dict列表（正序，最早→最新），含high/low/close
        period: ATR周期（默认14）

    Returns:
        float: ATR值，或None（数据不足）
    """
    if len(kline_rows) < period + 1:
        return None

    recent = kline_rows[-(period + 1) :]  # 取最近 period+1 天
    trs = []
    for i in range(1, len(recent)):
        high = float(recent[i]['high'] or 0)
        low = float(recent[i]['low'] or 0)
        prev_close = float(recent[i - 1]['close'] or 0)
        if high is None or low is None or prev_close is None:
            continue
        tr = max(high - low, abs(high - prev_close), abs(low - prev_close))
        trs.append(tr)

    if not trs:
        return None

    return round(sum(trs) / len(trs), 4)


def _calc_historical_indicators(kline_slice):
    """计算历史时点的技术指标

    Args:
        kline_slice: K线dict列表（正序），截止到回测日（含）

    Returns:
        dict: {close, ma20, ma60, boll_upper, boll_lower, atr}
    """
    closes = [float(r['close'] or 0) for r in kline_slice]

    ma20 = _calc_ma(closes, 20)
    ma60 = _calc_ma(closes, 60)
    boll_upper, _, boll_lower = _calc_bollinger(closes, 20)
    atr = _calc_historical_atr(kline_slice, 14)
    close = closes[-1] if closes else None

    return {
        'close': close,
        'ma20': ma20,
        'ma60': ma60,
        'boll_upper': boll_upper,
        'boll_lower': boll_lower,
        'atr': atr,
    }


# ================================================================
# 2. 历史价格建议生成（复用 price_advisor 算法逻辑，不 import）
# ================================================================


def _calc_resistance(close, ma60, boll_upper):
    """计算技术面阻力位（与 price_advisor._calc_resistance 逻辑一致，L223-236）

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


def _gen_no_position(close, rating, ma20, ma60, boll_upper, boll_lower, atr):
    """无持仓：买入区间 / 目标价 / 止损价 / 建议仓位

    与 price_advisor._gen_no_position 逻辑完全一致。
    """
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

    # 约束1: 若 boll_lower 可用且 < 买入下限 → 下限 = boll_lower
    if boll_lower and boll_lower > 0 and boll_lower < buy_low:
        buy_low = boll_lower

    # 约束2: 买入上限不超过 close × 1.05
    max_high = close * 1.05
    buy_high = min(buy_high, max_high)

    # 确保下限不超过上限
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

    return {
        'available': True,
        'has_position': False,
        'position_pct': position_pct,
        'buy_range_low': round(buy_low, 2),
        'buy_range_high': round(buy_high, 2),
        'target_price': round(target_price, 2),
        'stop_loss': round(stop_loss, 2),
        'take_profit': None,
    }


def _gen_with_position(close, cost_price, rating, ma60=None, boll_upper=None, atr=None):
    """有持仓：止盈价 / 止损价 / 补仓价位（020P 现价锚定版）

    与 price_advisor._gen_with_position 逻辑一致，修改时需双向同步。
    020P：止盈/止损锚定现价（与成本解耦）——take_profit = max(min_tp, min(fixed_tp, resistance))
    其中 fixed_tp/min_tp 均基于 close；止损 = close * (1 - 评级止损比例)。
    补仓价位与 price_advisor._build_grid 有持仓补仓位一致（S4 已破止损时不设）。
    """
    target_gain = RATING_TARGET_GAIN.get(rating, 0.12)
    min_target_gain = MIN_TARGET_GAIN.get(rating, 0.04)
    stop_loss_pct = RATING_STOP_LOSS.get(rating, 0.05)

    # ---- 动态止盈：max(min_tp, min(fixed_tp, resistance))（020P：锚定现价）----
    fixed_tp = close * (1 + target_gain)
    resistance = _calc_resistance(close, ma60, boll_upper)
    min_tp = close * (1 + min_target_gain)
    take_profit = max(min_tp, min(fixed_tp, resistance))

    # ---- 止损价（020P：锚定现价，评级比例）----
    stop_loss = close * (1 - stop_loss_pct)

    # ---- 补仓价位（网格补仓位，与 price_advisor._build_grid 同公式）----
    add_price = None
    if close > stop_loss:  # S4 已破止损时不设补仓位
        if atr and atr > 0:
            add_price = max(stop_loss + atr * 0.5, close - atr * 1.0)
        else:
            add_price = close * 0.97

    return {
        'available': True,
        'has_position': True,
        'position_pct': None,
        'buy_range_low': None,
        'buy_range_high': None,
        'target_price': None,
        'stop_loss': round(stop_loss, 2),
        'take_profit': round(take_profit, 2),
        'add_price': round(add_price, 2) if add_price is not None else None,
    }


def _gen_price_advice_at_date(indicators, rating, cost_price):
    """在指定日期生成价格建议

    Args:
        indicators: _calc_historical_indicators 返回的指标dict
        rating: 当前最新评级
        cost_price: 持仓成本价（None表示无持仓）

    Returns:
        dict: 价格建议字典
    """
    close = indicators['close']
    if not close or close <= 0:
        return {'available': False, 'reason': '停牌或数据不足'}

    ma20 = indicators['ma20']
    ma60 = indicators['ma60']
    boll_upper = indicators['boll_upper']
    boll_lower = indicators['boll_lower']
    atr = indicators['atr']

    # 有持仓模式（传递 ma60/boll_upper 用于动态止盈计算）
    if cost_price and cost_price > 0:
        return _gen_with_position(close, cost_price, rating, ma60, boll_upper, indicators['atr'])

    # 无持仓模式
    return _gen_no_position(close, rating, ma20, ma60, boll_upper, boll_lower, atr)


# ================================================================
# 3. 命中判定（基于日内 high/low）
# ================================================================


def _check_hit(kline_slice, advice, period_label):
    """检查T+N窗口内是否命中各价格建议项

    命中定义（方案设计报告 §一）：
      买入区间命中: 存在任意一天 low<=buy_range_high 且 high>=buy_range_low
      目标价命中:   存在任意一天 high>=target_price
      止损价命中:   存在任意一天 low<=stop_loss
      止盈价命中:   存在任意一天 high>=take_profit（有持仓时）

    Args:
        kline_slice: T+N窗口内的K线dict列表
        advice: 价格建议dict
        period_label: 't5' 或 't20'

    Returns:
        dict: 各项命中结果 + 首次命中天数 + max_high/min_low
    """
    result = {
        f'{period_label}_hit_buy_range': None,
        f'{period_label}_hit_target': None,
        f'{period_label}_hit_stop_loss': None,
        f'{period_label}_hit_take_profit': None,
        f'{period_label}_hit_add': None,
        f'{period_label}_hit_hold': None,
        f'{period_label}_days_to_buy_range': None,
        f'{period_label}_days_to_target': None,
        f'{period_label}_days_to_stop_loss': None,
        f'{period_label}_days_to_take_profit': None,
        f'{period_label}_max_high': None,
        f'{period_label}_min_low': None,
    }

    if not kline_slice or len(kline_slice) == 0:
        return result

    highs = [float(r['high'] or 0) for r in kline_slice]
    lows = [float(r['low'] or 0) for r in kline_slice]
    max_high = max(highs) if highs else None
    min_low = min(lows) if lows else None
    result[f'{period_label}_max_high'] = max_high
    result[f'{period_label}_min_low'] = min_low

    buy_low = advice.get('buy_range_low')
    buy_high = advice.get('buy_range_high')
    target_price = advice.get('target_price')
    stop_loss = advice.get('stop_loss')
    take_profit = advice.get('take_profit')

    # 补仓区间（有持仓网格补仓位）：优先 advice['add_price']，兼容 grid 中 type='add' 档位
    add_price = None
    add_raw = advice.get('add_price')
    if add_raw is not None:
        add_price = float(add_raw)
    else:
        grid = advice.get('grid')
        if isinstance(grid, list):
            for g in grid:
                if g.get('type') == 'add' and g.get('price'):
                    add_price = float(g['price'])
                    break

    for day_idx, row in enumerate(kline_slice):
        day_high = float(row['high'] or 0)
        day_low = float(row['low'] or 0)

        # 买入区间命中: low <= buy_range_high 且 high >= buy_range_low
        if buy_low is not None and buy_high is not None:
            if day_low <= buy_high and day_high >= buy_low:
                if result[f'{period_label}_hit_buy_range'] is None:
                    result[f'{period_label}_hit_buy_range'] = 1
                    result[f'{period_label}_days_to_buy_range'] = day_idx + 1

        # 目标价命中: high >= target_price
        if target_price is not None:
            if day_high >= target_price:
                if result[f'{period_label}_hit_target'] is None:
                    result[f'{period_label}_hit_target'] = 1
                    result[f'{period_label}_days_to_target'] = day_idx + 1

        # 止损价命中: low <= stop_loss
        if stop_loss is not None:
            if day_low <= stop_loss:
                if result[f'{period_label}_hit_stop_loss'] is None:
                    result[f'{period_label}_hit_stop_loss'] = 1
                    result[f'{period_label}_days_to_stop_loss'] = day_idx + 1

        # 止盈价命中: high >= take_profit（有持仓时）
        if take_profit is not None:
            if day_high >= take_profit:
                if result[f'{period_label}_hit_take_profit'] is None:
                    result[f'{period_label}_hit_take_profit'] = 1
                    result[f'{period_label}_days_to_take_profit'] = day_idx + 1

        # 补仓区间命中: low <= add_price（有持仓补仓位，出现过加仓机会）
        if add_price is not None:
            if day_low <= add_price:
                if result[f'{period_label}_hit_add'] is None:
                    result[f'{period_label}_hit_add'] = 1

    # 未命中的设为0（buy_range/target/stop_loss）
    for key in [
        f'{period_label}_hit_buy_range',
        f'{period_label}_hit_target',
        f'{period_label}_hit_stop_loss',
    ]:
        if result[key] is None:
            result[key] = 0

    # take_profit 特殊处理：仅当 advice 中 take_profit 不为 None 时才设为0，
    # 否则保持 None（避免无持仓样本稀释止盈命中率）
    tp_key = f'{period_label}_hit_take_profit'
    if take_profit is not None and result[tp_key] is None:
        result[tp_key] = 0

    # 补仓区间特殊处理：仅当有补仓价位时未命中设0，否则保持 None
    add_key = f'{period_label}_hit_add'
    if add_price is not None and result[add_key] is None:
        result[add_key] = 0

    # 持有区间命中（有持仓且止损/止盈价齐全时）：
    # 股价在 [stop_loss, take_profit] 内保持（未触发止盈也未触发止损），
    # 即"持有观望"建议有效期内未被打破；仅对中性持有语义有意义。
    hold_key = f'{period_label}_hit_hold'
    if stop_loss is not None and take_profit is not None:
        hit_sl = result[f'{period_label}_hit_stop_loss']
        hit_tp = result[tp_key]
        if hit_sl == 0 and hit_tp == 0:
            result[hold_key] = 1
        else:
            result[hold_key] = 0

    return result


# ================================================================
# 4. 持仓成本读取（与 price_advisor._read_cost_price 逻辑一致）
# ================================================================


def _read_cost_price(stock_id):
    """读取持仓成本价（优先 holdings 表，fallback positions 表）"""
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 优先查 holdings 表
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

        # Fallback: 查 positions 表
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


# ================================================================
# 4.5 010-3: 锚点标记 + 高偏差风险标记
# ================================================================


def _normalize_rating_for_compare(rating_str):
    """归一化评级用于锚点比较（兼容历史A/B+/B/C/D与中文5档）。

    使用 scoring_engine.normalize_rating 确保与评级存储口径一致。
    """
    from modules.scoring_engine import normalize_rating

    return normalize_rating(rating_str)


def _mark_rating_confidence(cursor, stock_id, bt_date, rating):
    """查找回测日前后5天内最近的评级记录，判断锚点可信度。

    Args:
        cursor: 数据库游标
        stock_id: 股票ID
        bt_date: 回测日期 (YYYY-MM-DD)
        rating: 回测使用的当前评级

    Returns:
        dict: {rating_confidence, anchor_rating_date, anchor_rating, days_since_rating}
    """
    result = {
        'rating_confidence': 'unknown',
        'anchor_rating_date': None,
        'anchor_rating': None,
        'days_since_rating': None,
    }

    try:
        cursor.execute(
            """
            SELECT rating_date, rating,
                   CAST(ABS(julianday(rating_date) - julianday(?)) AS INTEGER) as days_diff
            FROM ratings_history
            WHERE stock_id = ?
              AND rating_date BETWEEN date(?, '-5 days') AND date(?, '+5 days')
            ORDER BY days_diff ASC
            LIMIT 1
        """,
            (bt_date, stock_id, bt_date, bt_date),
        )
        row = cursor.fetchone()

        if row:
            anchor_rating_raw = row['rating']
            anchor_date = row['rating_date']
            days_diff = row['days_diff']

            # 归一化两端评级进行比较
            bt_norm = _normalize_rating_for_compare(rating)
            anchor_norm = _normalize_rating_for_compare(anchor_rating_raw)

            if bt_norm and anchor_norm:
                if bt_norm == anchor_norm:
                    result['rating_confidence'] = 'confirmed'
                else:
                    result['rating_confidence'] = 'mismatched'
            else:
                result['rating_confidence'] = 'unknown'

            result['anchor_rating_date'] = anchor_date
            result['anchor_rating'] = anchor_rating_raw
            result['days_since_rating'] = days_diff
    except Exception as e:
        logger.debug(f'_mark_rating_confidence stock_id={stock_id} date={bt_date}: {e}')

    return result


def _calc_bias_risk(all_kline, bt_idx, rating):
    """计算高偏差风险标记。

    仅当评级为"建议减仓"或"强烈建议卖出"时评估。
    基于近60日涨幅判断偏差风险等级：
      - 涨幅>30% -> high（评级看空但股价大涨，偏差风险高）
      - 涨幅>15% -> medium
      - 其他 -> low

    Args:
        all_kline: 全部K线数据列表
        bt_idx: 回测点索引
        rating: 当前评级

    Returns:
        str: 'high' / 'medium' / 'low'
    """
    if rating not in ('建议减仓', '强烈建议卖出'):
        return 'low'

    try:
        current_close = float(all_kline[bt_idx]['close'] or 0)
        past_idx = max(0, bt_idx - 60)
        past_close = float(all_kline[past_idx]['close'] or 0)

        if past_close <= 0 or current_close <= 0:
            return 'low'

        gain_pct = (current_close - past_close) / past_close

        if gain_pct > 0.30:
            return 'high'
        elif gain_pct > 0.15:
            return 'medium'
        else:
            return 'low'
    except Exception:
        return 'low'


# ================================================================
# 5. 主回测函数
# ================================================================


def run_price_backtest(market=None, force=False):
    """价格建议回测主函数

    对每只股票，从第35个交易日开始，每隔5天取一个回测点。
    在每个回测点生成历史价格建议，验证T+5/T+20命中率。

    Args:
        market: 'a_stock'/'hk_stock'/None（None=全部市场）
        force: True时先清除旧记录再重跑

    Returns:
        dict: {total, success, errors, skipped}
    """
    from modules.data_adapter import load_stockdata_from_db
    from modules.scoring_engine import analyze

    # 010-3: 确保新增的5列存在（幂等）
    _ensure_price_backtest_columns()

    conn = get_connection()
    cursor = conn.cursor()

    # force=True 时清除旧记录（全表清空，破坏性操作 → 先备份）
    if force:
        backup_database('clear_price_backtest_results')
        cursor.execute('DELETE FROM price_backtest_results')
        cleared = cursor.rowcount if cursor.rowcount else 0
        conn.commit()
        if cleared:
            logger.info(f'price_backtest: 清除 {cleared} 条旧记录')

    # 获取所有有足够K线数据的股票
    if market:
        cursor.execute(
            """
            SELECT s.id, s.symbol, s.name, s.market,
                   COUNT(k.id) as kline_cnt
            FROM stocks s
            JOIN raw_kline k ON k.stock_id = s.id
            WHERE s.market = ?
            GROUP BY s.id
            HAVING kline_cnt >= 35
            ORDER BY s.id
        """,
            (market,),
        )
    else:
        cursor.execute("""
            SELECT s.id, s.symbol, s.name, s.market,
                   COUNT(k.id) as kline_cnt
            FROM stocks s
            JOIN raw_kline k ON k.stock_id = s.id
            GROUP BY s.id
            HAVING kline_cnt >= 35
            ORDER BY s.id
        """)
    stocks = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not stocks:
        return {
            'total': 0,
            'success': 0,
            'errors': 0,
            'skipped': 0,
            'message': '无足够K线数据的股票',
        }

    total = 0
    success = 0
    errors = 0
    skipped = 0

    for stock in stocks:
        stock_id = stock['id']
        stock_market = stock['market']

        # 1. 获取当前最新评级（与历史模拟回测同口径；run_historical_simulation 已随 021D 删除）
        stock_data = load_stockdata_from_db(stock_id)
        if stock_data is None:
            logger.info(f'price_backtest stock_id={stock_id}: StockData构建失败，跳过')
            skipped += 1
            continue

        try:
            analysis = analyze(stock_data)
            rating = analysis.rating
        except Exception as e:
            logger.error(f'price_backtest stock_id={stock_id} analyze失败: {e}')
            errors += 1
            continue

        # 2. 获取当前持仓成本价
        cost_price = _read_cost_price(stock_id)

        # 3. 读取全部K线数据（正序）
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT trade_date, open, close, high, low, volume, amount, pct_change
            FROM raw_kline WHERE stock_id = ?
            ORDER BY trade_date ASC
        """,
            (stock_id,),
        )
        all_kline = [dict(r) for r in cursor.fetchall()]
        conn.close()

        if len(all_kline) < 35:
            skipped += 1
            continue

        total_days = len(all_kline)
        min_prefix = 35  # 确保MACD/MA20/BOLL/ATR可计算
        end_idx = total_days - 1  # 留出尾部给T+N验证

        if min_prefix >= end_idx:
            skipped += 1
            continue

        # 每隔5个交易日取一个回测点
        bt_indices = list(range(min_prefix, end_idx, 5))

        conn = get_connection()
        cursor = conn.cursor()

        for bt_idx in bt_indices:
            total += 1
            bt_date = all_kline[bt_idx]['trade_date']

            try:
                # 4. 计算历史技术指标（截止到回测日）
                kline_slice = all_kline[: bt_idx + 1]
                indicators = _calc_historical_indicators(kline_slice)

                if not indicators['close'] or indicators['close'] <= 0:
                    errors += 1
                    continue

                # 5. 生成价格建议
                advice = _gen_price_advice_at_date(indicators, rating, cost_price)

                if not advice.get('available'):
                    errors += 1
                    continue

                # 6. 取T+5和T+20的K线切片
                t5_slice = all_kline[bt_idx + 1 : bt_idx + 6]  # T+1 ~ T+5
                t20_slice = all_kline[bt_idx + 1 : bt_idx + 21]  # T+1 ~ T+20

                # 7. 命中判定
                t5_result = _check_hit(t5_slice, advice, 't5')
                t20_result = _check_hit(t20_slice, advice, 't20')

                # 8. 写入数据库
                has_position = 1 if advice['has_position'] else 0

                # 010-3: 锚点标记 + 偏差风险
                anchor_info = _mark_rating_confidence(cursor, stock_id, bt_date, rating)
                bias_risk = _calc_bias_risk(all_kline, bt_idx, rating)

                cursor.execute(
                    """
                    INSERT INTO price_backtest_results
                    (stock_id, backtest_date, rating, market, has_position,
                     buy_range_low, buy_range_high, target_price, stop_loss, take_profit, position_pct,
                     close_at_backtest, ma20, ma60, boll_upper, boll_lower, atr,
                     t5_hit_buy_range, t5_hit_target, t5_hit_stop_loss, t5_hit_take_profit,
                     t5_hit_add, t5_hit_hold,
                     t5_days_to_buy_range, t5_days_to_target, t5_days_to_stop_loss, t5_days_to_take_profit,
                     t5_max_high, t5_min_low,
                     t20_hit_buy_range, t20_hit_target, t20_hit_stop_loss, t20_hit_take_profit,
                     t20_hit_add, t20_hit_hold,
                     t20_days_to_buy_range, t20_days_to_target, t20_days_to_stop_loss, t20_days_to_take_profit,
                     t20_max_high, t20_min_low,
                     rating_confidence, anchor_rating_date, anchor_rating, bias_risk, days_since_rating)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                    (
                        stock_id,
                        bt_date,
                        rating,
                        stock_market,
                        has_position,
                        advice.get('buy_range_low'),
                        advice.get('buy_range_high'),
                        advice.get('target_price'),
                        advice.get('stop_loss'),
                        advice.get('take_profit'),
                        advice.get('position_pct'),
                        indicators['close'],
                        indicators['ma20'],
                        indicators['ma60'],
                        indicators['boll_upper'],
                        indicators['boll_lower'],
                        indicators['atr'],
                        t5_result['t5_hit_buy_range'],
                        t5_result['t5_hit_target'],
                        t5_result['t5_hit_stop_loss'],
                        t5_result['t5_hit_take_profit'],
                        t5_result['t5_hit_add'],
                        t5_result['t5_hit_hold'],
                        t5_result['t5_days_to_buy_range'],
                        t5_result['t5_days_to_target'],
                        t5_result['t5_days_to_stop_loss'],
                        t5_result['t5_days_to_take_profit'],
                        t5_result['t5_max_high'],
                        t5_result['t5_min_low'],
                        t20_result['t20_hit_buy_range'],
                        t20_result['t20_hit_target'],
                        t20_result['t20_hit_stop_loss'],
                        t20_result['t20_hit_take_profit'],
                        t20_result['t20_hit_add'],
                        t20_result['t20_hit_hold'],
                        t20_result['t20_days_to_buy_range'],
                        t20_result['t20_days_to_target'],
                        t20_result['t20_days_to_stop_loss'],
                        t20_result['t20_days_to_take_profit'],
                        t20_result['t20_max_high'],
                        t20_result['t20_min_low'],
                        anchor_info['rating_confidence'],
                        anchor_info['anchor_rating_date'],
                        anchor_info['anchor_rating'],
                        bias_risk,
                        anchor_info['days_since_rating'],
                    ),
                )
                success += 1

            except Exception as e:
                logger.error(f'price_backtest stock_id={stock_id} date={bt_date}: {e}')
                errors += 1

        conn.commit()
        conn.close()

    logger.info(
        f'007价格建议回测完成: total={total}, success={success}, errors={errors}, skipped={skipped}'
    )
    return {
        'total': total,
        'success': success,
        'errors': errors,
        'skipped': skipped,
    }


# ================================================================
# 6. 报告生成
# ================================================================


def compute_price_backtest_report(market='a_stock'):
    """生成价格建议回测报告

    Args:
        market: 'a_stock' 或 'hk_stock'

    Returns:
        dict: 完整报告（命中率/时间效率/偏差分析/分组统计/综合评估）
    """
    # 确保新增列存在（补仓区间/持有区间等，幂等；旧库首次读取前补齐）
    _ensure_price_backtest_columns()

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM price_backtest_results WHERE market = ?', (market,))
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if not rows:
        return {
            'market': market,
            'total_points': 0,
            'message': '暂无回测数据，请先运行回测',
        }

    total_points = len(rows)

    # ---- 辅助函数 ----
    def _hit_rate(field):
        valid = [r for r in rows if r.get(field) is not None]
        if not valid:
            return None
        hits = sum(1 for r in valid if r[field] == 1)
        return round(hits / len(valid), 4)

    def _avg_days(field_hit, field_days):
        """计算平均首次命中天数（仅命中案例）"""
        hit_rows = [r for r in rows if r.get(field_hit) == 1 and r.get(field_days) is not None]
        if not hit_rows:
            return None
        return round(sum(r[field_days] for r in hit_rows) / len(hit_rows), 1)

    # ---- 核心命中率 ----
    hit_rates = {
        't5': {
            'buy_range': _hit_rate('t5_hit_buy_range'),
            'target': _hit_rate('t5_hit_target'),
            'stop_loss': _hit_rate('t5_hit_stop_loss'),
            'take_profit': _hit_rate('t5_hit_take_profit'),
        },
        't20': {
            'buy_range': _hit_rate('t20_hit_buy_range'),
            'target': _hit_rate('t20_hit_target'),
            'stop_loss': _hit_rate('t20_hit_stop_loss'),
            'take_profit': _hit_rate('t20_hit_take_profit'),
        },
    }

    # ---- 时间效率 ----
    avg_days = {
        't5': {
            'buy_range': _avg_days('t5_hit_buy_range', 't5_days_to_buy_range'),
            'target': _avg_days('t5_hit_target', 't5_days_to_target'),
            'stop_loss': _avg_days('t5_hit_stop_loss', 't5_days_to_stop_loss'),
            'take_profit': _avg_days('t5_hit_take_profit', 't5_days_to_take_profit'),
        },
        't20': {
            'buy_range': _avg_days('t20_hit_buy_range', 't20_days_to_buy_range'),
            'target': _avg_days('t20_hit_target', 't20_days_to_target'),
            'stop_loss': _avg_days('t20_hit_stop_loss', 't20_days_to_stop_loss'),
            'take_profit': _avg_days('t20_hit_take_profit', 't20_days_to_take_profit'),
        },
    }

    # ---- 020R-52：真实样本口径（主口径）----
    # 真实评级回测点 = anchor_rating_date 非空（回测时点有真实评级记录，无未来函数）；
    # anchor_rating_date 为空 = 历史重建点（未来函数偏差，仅作参照）。
    real_rows = [r for r in rows if r.get('anchor_rating_date')]
    real_total = len(real_rows)

    def _real_hit(field):
        valid = [r for r in real_rows if r.get(field) is not None]
        if not valid:
            return None
        return round(sum(1 for r in valid if r[field] == 1) / len(valid), 4)

    def _real_avg_days(field_hit, field_days):
        hit_rows = [
            r for r in real_rows if r.get(field_hit) == 1 and r.get(field_days) is not None
        ]
        if not hit_rows:
            return None
        return round(sum(r[field_days] for r in hit_rows) / len(hit_rows), 1)

    real_hit_rates = {
        't5': {
            'buy_range': _real_hit('t5_hit_buy_range'),
            'target': _real_hit('t5_hit_target'),
            'stop_loss': _real_hit('t5_hit_stop_loss'),
            'take_profit': _real_hit('t5_hit_take_profit'),
        },
        't20': {
            'buy_range': _real_hit('t20_hit_buy_range'),
            'target': _real_hit('t20_hit_target'),
            'stop_loss': _real_hit('t20_hit_stop_loss'),
            'take_profit': _real_hit('t20_hit_take_profit'),
        },
    }
    real_avg_days = {
        't5': {
            'buy_range': _real_avg_days('t5_hit_buy_range', 't5_days_to_buy_range'),
            'target': _real_avg_days('t5_hit_target', 't5_days_to_target'),
            'stop_loss': _real_avg_days('t5_hit_stop_loss', 't5_days_to_stop_loss'),
            'take_profit': _real_avg_days('t5_hit_take_profit', 't5_days_to_take_profit'),
        },
        't20': {
            'buy_range': _real_avg_days('t20_hit_buy_range', 't20_days_to_buy_range'),
            'target': _real_avg_days('t20_hit_target', 't20_days_to_target'),
            'stop_loss': _real_avg_days('t20_hit_stop_loss', 't20_days_to_stop_loss'),
            'take_profit': _real_avg_days('t20_hit_take_profit', 't20_days_to_take_profit'),
        },
    }
    real_risk_reward = None
    if (
        real_hit_rates['t20']['target'] is not None
        and real_hit_rates['t20']['stop_loss'] is not None
        and real_hit_rates['t20']['stop_loss'] > 0
    ):
        real_risk_reward = round(
            real_hit_rates['t20']['target'] / real_hit_rates['t20']['stop_loss'], 2
        )
    real_composite_score = None
    if (
        real_hit_rates['t20']['target'] is not None
        and real_hit_rates['t20']['buy_range'] is not None
        and real_hit_rates['t20']['stop_loss'] is not None
    ):
        real_composite_score = round(
            real_hit_rates['t20']['target'] * 0.4
            + real_hit_rates['t20']['buy_range'] * 0.3
            + (1 - real_hit_rates['t20']['stop_loss']) * 0.3,
            4,
        )

    # ---- 偏差分析 ----
    # 目标价平均偏差: 未命中案例中 (target_price - max_high) / target_price
    target_miss = [
        r
        for r in rows
        if r.get('t20_hit_target') == 0
        and r.get('target_price')
        and r.get('t20_max_high')
        and r['target_price'] > 0
    ]
    target_avg_dev = None
    if target_miss:
        devs = [(r['target_price'] - r['t20_max_high']) / r['target_price'] for r in target_miss]
        target_avg_dev = round(sum(devs) / len(devs), 4)

    # 止损价平均偏差: 未命中案例中 (min_low - stop_loss) / stop_loss
    stop_miss = [
        r
        for r in rows
        if r.get('t20_hit_stop_loss') == 0
        and r.get('stop_loss')
        and r.get('t20_min_low')
        and r['stop_loss'] > 0
    ]
    stop_avg_dev = None
    if stop_miss:
        devs = [(r['t20_min_low'] - r['stop_loss']) / r['stop_loss'] for r in stop_miss]
        stop_avg_dev = round(sum(devs) / len(devs), 4)

    # 买入区间宽度: (buy_range_high - buy_range_low) / close
    buy_rows = [
        r
        for r in rows
        if r.get('buy_range_low')
        and r.get('buy_range_high')
        and r.get('close_at_backtest')
        and r['close_at_backtest'] > 0
    ]
    buy_avg_width = None
    if buy_rows:
        widths = [
            (r['buy_range_high'] - r['buy_range_low']) / r['close_at_backtest'] for r in buy_rows
        ]
        buy_avg_width = round(sum(widths) / len(widths), 4)

    deviation = {
        'target_avg_dev': target_avg_dev,
        'stop_loss_avg_dev': stop_avg_dev,
        'buy_range_avg_width': buy_avg_width,
    }

    # ---- 分评级统计（无持仓/有持仓拆分：各组分母一致，可横向比较）----
    rating_order = ['强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出']
    rating_stats = {}
    for rating in rating_order:
        r_rows = [r for r in rows if r.get('rating') == rating]
        if not r_rows:
            continue

        # 无持仓样本有 买入区间/目标价/止损价；有持仓样本有 止盈价/止损价
        np_rows = [r for r in r_rows if not r.get('has_position')]
        hp_rows = [r for r in r_rows if r.get('has_position')]

        def _rhr(field, sub):
            valid = [r for r in sub if r.get(field) is not None]
            if not valid:
                return None
            hits = sum(1 for r in valid if r[field] == 1)
            return round(hits / len(valid), 4)

        rating_stats[rating] = {
            'total': len(r_rows),
            # 无持仓样本（分母=np_total）：买入区间/目标价/止损价
            'np_total': len(np_rows),
            'np_t20_buy_range': _rhr('t20_hit_buy_range', np_rows),
            'np_t20_target': _rhr('t20_hit_target', np_rows),
            'np_t20_stop_loss': _rhr('t20_hit_stop_loss', np_rows),
            # 有持仓样本（分母=hp_total）：补仓区间/持有区间/止盈价/止损价
            'hp_total': len(hp_rows),
            'hp_t20_add': _rhr('t20_hit_add', hp_rows),
            'hp_t20_hold': _rhr('t20_hit_hold', hp_rows),
            'hp_t20_take_profit': _rhr('t20_hit_take_profit', hp_rows),
            'hp_t20_stop_loss': _rhr('t20_hit_stop_loss', hp_rows),
        }

    # ---- 综合评估 ----
    # 风险收益比 = 目标价命中率 / 止损价命中率（T+20）
    risk_reward = None
    t20_target = hit_rates['t20']['target']
    t20_stop = hit_rates['t20']['stop_loss']
    if t20_target is not None and t20_stop is not None and t20_stop > 0:
        risk_reward = round(t20_target / t20_stop, 2)

    # 综合得分: 加权（目标价40% + 买入区间30% + 止损控制30%）
    composite_score = None
    t20_t = hit_rates['t20']['target']
    t20_b = hit_rates['t20']['buy_range']
    t20_s = hit_rates['t20']['stop_loss']
    if t20_t is not None and t20_b is not None and t20_s is not None:
        # 止损控制 = 1 - 止损命中率（命中率越低越好）
        stop_control = 1 - t20_s
        composite_score = round(t20_t * 0.4 + t20_b * 0.3 + stop_control * 0.3, 4)

    # 有持仓/无持仓统计
    no_pos = [r for r in rows if r.get('has_position') == 0]
    has_pos = [r for r in rows if r.get('has_position') == 1]

    # ---- 010-4: 可信样本报告 ----
    def _hr_sub(sub_rows, field):
        """计算子集命中率"""
        valid = [r for r in sub_rows if r.get(field) is not None]
        if not valid:
            return None
        hits = sum(1 for r in valid if r[field] == 1)
        return round(hits / len(valid), 4)

    # 按锚点可信度分组
    confidence_report = {}
    for conf in ['confirmed', 'mismatched', 'unknown']:
        conf_rows = [r for r in rows if r.get('rating_confidence') == conf]
        total_c = len(conf_rows)
        entry = {
            'total': total_c,
            't20_target': _hr_sub(conf_rows, 't20_hit_target'),
            't20_stop_loss': _hr_sub(conf_rows, 't20_hit_stop_loss'),
            't20_take_profit': _hr_sub(conf_rows, 't20_hit_take_profit'),
        }
        if conf == 'confirmed' and 0 < total_c < 30:
            entry['note'] = '样本量不足（<30），仅供参考'
        elif conf == 'mismatched' and 0 < total_c < 20:
            entry['note'] = '样本量极少（<20），仅作定性参考'
        confidence_report[conf] = entry

    # 高偏差风险样本
    high_risk_rows = [r for r in rows if r.get('bias_risk') == 'high']
    confidence_report['bias_risk_high'] = {
        'total': len(high_risk_rows),
        't20_target': _hr_sub(high_risk_rows, 't20_hit_target'),
        't20_stop_loss': _hr_sub(high_risk_rows, 't20_hit_stop_loss'),
        'note': '高偏差风险样本，命中率可能虚高',
    }

    # ---- 分时段统计 ----
    recent_rows = [r for r in rows if str(r.get('backtest_date', '')) >= '2026-07-16']
    earlier_rows = [r for r in rows if str(r.get('backtest_date', '')) < '2026-07-16']
    period_comparison = {
        'recent_12d': {
            'total': len(recent_rows),
            't20_target': _hr_sub(recent_rows, 't20_hit_target'),
            't20_stop_loss': _hr_sub(recent_rows, 't20_hit_stop_loss'),
            't20_buy_range': _hr_sub(recent_rows, 't20_hit_buy_range'),
        },
        'earlier': {
            'total': len(earlier_rows),
            't20_target': _hr_sub(earlier_rows, 't20_hit_target'),
            't20_stop_loss': _hr_sub(earlier_rows, 't20_hit_stop_loss'),
            't20_buy_range': _hr_sub(earlier_rows, 't20_hit_buy_range'),
        },
        'note': '近12天有真实评级数据，命中率可能更准确；之前时段存在未来函数偏差',
    }

    # ---- 020R-52：真实样本优先解读（主口径=真实评级回测点，无未来函数）----
    interpretation_parts = []
    interpretation_tones = []

    def _add_interp(text, tone='neutral'):
        interpretation_parts.append(text)
        interpretation_tones.append(tone)

    if total_points > 0:
        _add_interp(
            f'价格建议回测共 {total_points} 个回测点（无持仓 {len(no_pos)} 个 / 有持仓 {len(has_pos)} 个）：'
            f'其中 {real_total} 个为真实评级回测点（无未来函数），'
            f'其余 {total_points - real_total} 个为历史重建点（存在未来函数偏差，仅作参照）。'
        )
        if real_total > 0:
            if real_total < 30:
                _add_interp(
                    f'真实样本仅 {real_total} 个（<30），以下命中率仅供趋势参考，样本不足结论易反转。',
                    'bad',
                )
            _r5b = real_hit_rates['t5']['buy_range']
            _r20b = real_hit_rates['t20']['buy_range']
            _rt = real_hit_rates['t20']['target']
            _rs = real_hit_rates['t20']['stop_loss']
            if _r20b is not None:
                _add_interp(
                    f'买入区间命中（真实样本）：T+5 {_r5b * 100:.0f}%、T+20 {_r20b * 100:.0f}%——'
                    + ('买入区间定价合理，回调到位概率较高。' if _r20b >= 0.6 else '买入区间命中一般，可适当放宽或下移区间。'),
                    'good' if _r20b >= 0.6 else 'bad',
                )
            if _rt is not None:
                _add_interp(
                    f'目标价命中（真实样本）：T+20 {_rt * 100:.0f}%——'
                    + ('目标价设定合理，实现概率较高。' if _rt >= 0.35 else '目标价偏乐观，实际达成概率有限。'),
                    'good' if _rt >= 0.35 else 'bad',
                )
            if _rs is not None:
                _add_interp(
                    f'止损线触发（真实样本）：T+20 {_rs * 100:.0f}%——'
                    + ('止损保护有效且触发频率适中。' if _rs <= 0.15 else '止损触发偏频繁，止损位可能偏紧。'),
                    'good' if _rs <= 0.15 else 'bad',
                )
            if real_risk_reward is not None:
                _add_interp(
                    f'风险收益比（真实样本）{real_risk_reward}（目标命中/止损触发）——'
                    + ('目标兑现机会大于止损风险。' if real_risk_reward >= 1 else '止损风险高于目标兑现机会，需谨慎。'),
                    'good' if real_risk_reward >= 1 else 'bad',
                )
            if real_total >= 30 and real_composite_score is not None:
                _add_interp(
                    f'价格建议综合得分（真实样本）{real_composite_score:.3f}'
                    '（目标价40% + 买入区间30% + 止损控制30%）。',
                    'good' if real_composite_score >= 0.6 else ('bad' if real_composite_score < 0.4 else 'neutral'),
                )
            # 全样本参照（含未来函数偏差的重建点，仅陈列不参与结论）
            if t20_b is not None and t20_target is not None and t20_stop is not None:
                _add_interp(
                    f'全样本参照（含历史重建点）：买入区间 T+20 {t20_b * 100:.0f}%、'
                    f'目标价 {t20_target * 100:.0f}%、止损 {t20_stop * 100:.0f}%——'
                    '因含未来函数偏差，不作为定价结论依据。',
                    'neutral',
                )
        else:
            # 无真实样本：退回全样本解读（沿用旧逻辑），并前置数据质量警示
            _add_interp('当前无真实评级回测点，以下结论来自历史重建点，存在未来函数偏差，可信度低。', 'bad')
            _t5_b = hit_rates['t5']['buy_range']
            _t20_b = hit_rates['t20']['buy_range']
            if _t20_b is not None:
                _add_interp(
                    f'买入区间命中：T+5 {_t5_b * 100:.0f}%、T+20 {_t20_b * 100:.0f}%——'
                    + ('买入区间定价合理，回调到位概率较高。' if _t20_b >= 0.6 else '买入区间命中一般，可适当放宽或下移区间。'),
                    'good' if _t20_b >= 0.6 else 'bad',
                )
            if t20_target is not None:
                _add_interp(
                    f'目标价命中：T+20 {t20_target * 100:.0f}%——'
                    + ('目标价设定合理，实现概率较高。' if t20_target >= 0.35 else '目标价偏乐观，实际达成概率有限。'),
                    'good' if t20_target >= 0.35 else 'bad',
                )
            if t20_stop is not None:
                _add_interp(
                    f'止损线触发：T+20 {t20_stop * 100:.0f}%——'
                    + ('止损保护有效且触发频率适中。' if t20_stop <= 0.15 else '止损触发偏频繁，止损位可能偏紧。'),
                    'good' if t20_stop <= 0.15 else 'bad',
                )
    else:
        _add_interp('暂无价格建议回测数据，无法解读。', 'bad')

    return {
        'market': market,
        'total_points': total_points,
        'no_position_count': len(no_pos),
        'has_position_count': len(has_pos),
        'hit_rates': hit_rates,
        'avg_days': avg_days,
        # 020R-52：真实样本口径（主口径，前端优先展示）
        'real_sample': {'total': real_total},
        'real_hit_rates': real_hit_rates,
        'real_avg_days': real_avg_days,
        'real_risk_reward': real_risk_reward,
        'real_composite_score': real_composite_score,
        'deviation': deviation,
        'rating_stats': rating_stats,
        'risk_reward_ratio': risk_reward,
        'composite_score': composite_score,
        'confidence_report': confidence_report,
        'period_comparison': period_comparison,
        'interpretation_parts': interpretation_parts,
        'interpretation_tones': interpretation_tones,
    }
