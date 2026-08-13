"""持仓/组合/流水/成本修正/价格刷新 API 蓝图(自 app.py 拆分,函数体零改动)。"""

import json

from flask import Blueprint, jsonify, request

from blueprints._utils import _derive_obos_signal, _latest_report_join_sql
from config import (
    COST_ADJUSTMENT_COOLDOWN_HOURS,
    COST_ADJUSTMENT_DEVIATION_THRESHOLD,
    PRICE_CACHE_TTL_HOURS,
    TRADE_AMOUNT_VERIFY_THRESHOLD,
    TRADE_T1_LOCK_ENABLED,
)
from database.db_manager import get_connection

bp = Blueprint('portfolio', __name__)

@bp.route('/api/portfolio/holdings', methods=['GET'])
def api_get_holdings():
    """获取所有持仓列表（含股票信息 + 最新价格缓存）"""
    group_id = request.args.get('group_id', '')

    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        SELECT h.id, h.stock_id, h.group_id, h.cost_price, h.quantity,
               h.notes, h.created_at, h.updated_at,
               h.realized_pnl, h.status, h.is_cost_adjusted,
               s.symbol, s.name, s.market,
               pg.name as group_name,
               pc.latest_price as latest_price, pc.pct_change as price_pct_change,
               pc.updated_at as price_cache_time
        FROM holdings h
        INNER JOIN stocks s ON h.stock_id = s.id
        LEFT JOIN groups pg ON h.group_id = pg.id AND pg.type='portfolio'
        LEFT JOIN price_cache pc ON h.stock_id = pc.stock_id
    """
    params = []
    if group_id:
        sql += ' WHERE h.group_id = ?'
        params.append(group_id)
    sql += ' ORDER BY h.updated_at DESC'

    cursor.execute(sql, params)
    holdings = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # 格式化 latest_price / price_updated_at / 精确市值计算
    import decimal
    from datetime import datetime, timedelta

    now = datetime.now()
    for h in holdings:
        h['latest_price'] = (
            round(h.get('latest_price'), 2) if h.get('latest_price') is not None else None
        )
        cache_time = h.pop('price_cache_time', None)
        h['price_updated_at'] = cache_time
        # 价格是否过期（超过 PRICE_CACHE_TTL_HOURS 小时）
        h['price_expired'] = False
        if cache_time:
            try:
                dt = datetime.strptime(cache_time, '%Y-%m-%d %H:%M:%S')
                if (now - dt) > timedelta(hours=PRICE_CACHE_TTL_HOURS):
                    h['price_expired'] = True
            except (ValueError, TypeError):
                h['price_expired'] = True
        elif h['latest_price'] is not None:
            h['price_expired'] = True

        # ---- 精确市值计算 ----
        # market_value = quantity × latest_price（银行家舍入法，保留2位小数）
        qty = h.get('quantity') or 0
        price = h.get('latest_price')
        if price is not None and price > 0 and qty > 0:
            raw = qty * price
            # 银行家舍入法（Round Half To Even）
            h['market_value'] = float(
                decimal.Decimal(str(raw)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )
        else:
            # latest_price 为 NULL 时，市值显示 None（前端渲染 '--'）
            h['market_value'] = None

        # 向后兼容：旧字段 estimated_market_value 标记 @deprecated
        h['estimated_market_value'] = h['market_value']

        # ---- 浮动盈亏计算 ----
        # unrealized_pnl = (latest_price - avg_cost) × quantity
        cost_price = h.get('cost_price') or 0
        realized = h.get('realized_pnl') or 0
        if price is not None and qty > 0:
            raw_pnl = (price - cost_price) * qty
            h['unrealized_pnl'] = float(
                decimal.Decimal(str(raw_pnl)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )
        else:
            h['unrealized_pnl'] = None

        # ---- 总收益 = 已实现盈亏 + 浮动盈亏 ----
        if h['unrealized_pnl'] is not None:
            h['total_pnl'] = round(realized + h['unrealized_pnl'], 2)
        else:
            h['total_pnl'] = round(realized, 2) if realized != 0 else None

        # ---- 数据状态标签 ----
        if price is None:
            h['data_status'] = 'offline'  # 无价格数据
        elif h['price_expired']:
            h['data_status'] = 'cache'  # 缓存（过期）
        else:
            h['data_status'] = 'realtime'  # 实时

    return jsonify({'success': True, 'holdings': holdings, 'count': len(holdings)})


@bp.route('/api/portfolio/summary')
def api_portfolio_summary():
    """账户级汇总指标：总市值 / 当日盈亏 / 持仓数量 / 平均评分。

    P2 扩展：在原有持仓汇总基础上，增加评分维度（平均评分/评级分布/引擎统计），
    数据来自 daily_reports 表最新一期，与每日报告页同源。
    """
    import decimal
    import hashlib
    from datetime import datetime, timedelta, timezone

    _CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')
    group_id = request.args.get('group_id', '')
    conn = get_connection()
    cursor = conn.cursor()

    # ---------- 1. 持仓汇总（原逻辑不变） ----------
    sql = """
        SELECT h.id, h.stock_id, h.group_id, h.cost_price, h.quantity,
               h.notes, h.created_at, h.updated_at,
               h.realized_pnl, h.status, h.is_cost_adjusted,
               s.symbol, s.name, s.market,
               pg.name as group_name,
               pc.latest_price as latest_price, pc.pct_change as price_pct_change,
               pc.updated_at as price_cache_time
        FROM holdings h
        INNER JOIN stocks s ON h.stock_id = s.id
        LEFT JOIN groups pg ON h.group_id = pg.id AND pg.type='portfolio'
        LEFT JOIN price_cache pc ON h.stock_id = pc.stock_id
    """
    params = []
    if group_id:
        sql += ' WHERE h.group_id = ?'
        params.append(group_id)

    cursor.execute(sql, params)
    holdings = [dict(row) for row in cursor.fetchall()]

    total_market_value = 0.0
    total_unrealized = 0.0
    total_realized = 0.0
    has_market_value = False
    has_unrealized = False

    for h in holdings:
        qty = h.get('quantity') or 0
        price = h.get('latest_price')
        price = round(price, 2) if price is not None else None
        cost_price = h.get('cost_price') or 0
        realized = h.get('realized_pnl') or 0

        total_realized += realized

        if price is not None and price > 0 and qty > 0:
            mv = qty * price
            total_market_value += float(
                decimal.Decimal(str(mv)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )
            has_market_value = True

            upnl = (price - cost_price) * qty
            total_unrealized += float(
                decimal.Decimal(str(upnl)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )
            has_unrealized = True

    result = {
        'success': True,
        'total_market_value': round(total_market_value, 2) if has_market_value else None,
        'total_unrealized_pnl': round(total_unrealized, 2) if has_unrealized else None,
        'total_realized_pnl': round(total_realized, 2),
        'holding_count': len(holdings),
        'active_count': sum(1 for h in holdings if (h.get('quantity') or 0) > 0),
    }

    unrealized = result['total_unrealized_pnl']
    if unrealized is not None:
        result['total_pnl'] = round(result['total_realized_pnl'] + unrealized, 2)
    else:
        result['total_pnl'] = (
            result['total_realized_pnl'] if result['total_realized_pnl'] != 0 else None
        )

    # ---------- 2. P2 新增：评分维度汇总（来自 daily_reports 表） ----------
    # 019R: 与看板同口径——每股最新一份有效报告（status='ok'、daily 优先），
    # 聚合范围限定非退市自选股（与看板行集自洽），不再按全局单一 MAX(report_date) 聚合
    cursor.execute(
        'SELECT MAX(lr.report_date) as latest_date, MIN(lr.report_date) as min_date '
        'FROM stocks s JOIN ' + _latest_report_join_sql() + ' ON lr.stock_id = s.id '
        "WHERE s.status != 'delisted'"
    )
    date_row = cursor.fetchone()
    latest_report_date = date_row['latest_date'] if date_row else None
    report_date_min = date_row['min_date'] if date_row else None

    avg_score = None
    rating_dist = {}
    engine_stats = {'v5': 0, 'legacy': 0}
    scores_list = []
    report_generated_at = None

    if latest_report_date:
        cursor.execute(
            'SELECT lr.total_score, lr.rating, lr.engine_version, lr.generated_at '
            'FROM stocks s JOIN ' + _latest_report_join_sql() + ' ON lr.stock_id = s.id '
            "WHERE s.status != 'delisted'"
        )
        for r in cursor.fetchall():
            sc = r['total_score']
            if sc is not None:
                scores_list.append(sc)
            rt = r['rating']
            if rt:
                rating_dist[rt] = rating_dist.get(rt, 0) + 1
            ev = r['engine_version']
            if ev in engine_stats:
                engine_stats[ev] += 1
            ga = r['generated_at']
            if ga and (report_generated_at is None or ga > report_generated_at):
                report_generated_at = ga

    if scores_list:
        avg_score = round(sum(scores_list) / len(scores_list), 1)

    result['report_date'] = latest_report_date
    result['report_date_min'] = report_date_min
    result['avg_score'] = avg_score
    result['rating_distribution'] = rating_dist
    result['engine_stats'] = engine_stats
    result['generated_at'] = report_generated_at or datetime.now(_CN_TZ).isoformat()

    conn.close()

    # ---------- 3. ETag 缓存（排除 generated_at 避免时间戳波动） ----------
    etag_payload = {k: v for k, v in result.items() if k != 'generated_at'}
    etag = hashlib.md5(json.dumps(etag_payload, sort_keys=True, default=str).encode()).hexdigest()
    if request.headers.get('If-None-Match') == etag:
        return '', 304
    resp = jsonify(result)
    resp.headers['ETag'] = etag
    return resp


# ============================================================
# P2: 自选股批量评分看板接口（只读聚合，零引擎侵入）
# ============================================================


@bp.route('/api/portfolio/watchlist-scores')
def api_portfolio_watchlist_scores():
    """自选股批量评分看板数据（单次四表 JOIN，零引擎侵入）。

    数据源：stocks + holdings + price_cache + daily_reports
    返回：全部自选股的最新评分、评级、引擎版本、持仓、市值、行业分类。
    缓存：ETag + If-None-Match → 304
    """
    import decimal
    import hashlib
    from datetime import datetime, timedelta, timezone

    _CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')
    conn = get_connection()
    cursor = conn.cursor()

    # 019R: 每股最新一份有效报告（ROW_NUMBER 派生表 LEFT JOIN），不再依赖
    # 全局单一 MAX(report_date) JOIN；无报告股票由 LEFT JOIN 兜底（评分 NULL 排末尾）。
    # 019D 口径（daily 优先 + status='ok'）逐股化后内置于 _latest_report_join_sql()。
    cursor.execute(
        """
        SELECT s.id, s.symbol, s.name, s.market, s.status, s.industry,
               h.cost_price, h.quantity, h.realized_pnl,
               pc.latest_price, pc.pct_change as price_pct_change,
               lr.engine_version, lr.total_score, lr.rating,
               lr.rating_label, lr.score_change, lr.prev_score,
               lr.key_factors, lr.status as report_status, lr.generated_at,
               lr.report_date
        FROM stocks s
        LEFT JOIN holdings h     ON s.id = h.stock_id
        LEFT JOIN price_cache pc ON s.id = pc.stock_id
        LEFT JOIN """ + _latest_report_join_sql() + """
        ON lr.stock_id = s.id
        WHERE s.status != 'delisted'
        ORDER BY
            CASE WHEN lr.total_score IS NULL THEN 1 ELSE 0 END,
            lr.total_score DESC
    """
    )

    rows = [dict(row) for row in cursor.fetchall()]

    # 顶层日期：入选报告（每股最新有效报告）的最新/最早日期
    report_dates = sorted({r['report_date'] for r in rows if r.get('report_date')})
    latest_report_date = report_dates[-1] if report_dates else None
    report_date_min = report_dates[0] if report_dates else None

    # 每股最新报告集合中的 MAX(generated_at)（稳定值，用于ETag；口径随多日期同步）
    report_generated_at = None
    if latest_report_date:
        cursor.execute(
            'SELECT MAX(lr.generated_at) as gen_at FROM ' + _latest_report_join_sql()
        )
        gen_row = cursor.fetchone()
        report_generated_at = gen_row['gen_at'] if gen_row else None

    conn.close()

    stocks = []
    for r in rows:
        qty = r.get('quantity') or 0
        price = r.get('latest_price')
        price = round(price, 2) if price is not None else None

        # 精确市值
        market_value = None
        if price is not None and price > 0 and qty > 0:
            raw_mv = qty * price
            market_value = float(
                decimal.Decimal(str(raw_mv)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )

        # 浮动盈亏
        unrealized = None
        cost = r.get('cost_price') or 0
        if price is not None and qty > 0:
            raw_pnl = (price - cost) * qty
            unrealized = float(
                decimal.Decimal(str(raw_pnl)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )

        # 行业分类（从 stocks.industry 读取，INDUSTRY-DYNAMIC）
        industry = r.get('industry') or '未分类'

        stocks.append(
            {
                'id': r['id'],
                'symbol': r['symbol'],
                'name': r['name'],
                'market': r['market'],
                'industry': industry,
                'cost_price': round(cost, 2) if cost else None,
                'quantity': qty,
                'latest_price': price,
                'price_pct_change': r.get('price_pct_change'),
                'market_value': market_value,
                'unrealized_pnl': unrealized,
                'engine_version': r.get('engine_version'),
                'total_score': round(r['total_score'], 1)
                if r.get('total_score') is not None
                else None,
                'rating': r.get('rating'),
                'rating_label': r.get('rating_label'),
                'score_change': round(r['score_change'], 1)
                if r.get('score_change') is not None
                else None,
                'has_report': r.get('report_status') == 'ok',
                'generated_at': r.get('generated_at'),
                'report_date': r.get('report_date'),
                # DEV-TASKS-20260727-003：超买超卖信号（从 key_factors 派生，不暴露原始因子）
                'obos_signal': _derive_obos_signal(r.get('key_factors')),
            }
        )

    result = {
        'success': True,
        'report_date': latest_report_date,
        'report_date_min': report_date_min,
        'generated_at': report_generated_at or datetime.now(_CN_TZ).isoformat(),
        'stocks': stocks,
        'total': len(stocks),
    }

    # ETag 缓存（排除 generated_at 避免时间戳波动）
    etag_payload = {k: v for k, v in result.items() if k != 'generated_at'}
    etag = hashlib.md5(json.dumps(etag_payload, sort_keys=True, default=str).encode()).hexdigest()
    if request.headers.get('If-None-Match') == etag:
        return '', 304
    resp = jsonify(result)
    resp.headers['ETag'] = etag
    return resp


@bp.route('/api/portfolio/holdings/<int:stock_id>', methods=['POST'])
def api_upsert_holding(stock_id):
    """创建或更新持仓（成本价/数量/分组）"""
    data = request.get_json(silent=True) or {}
    cost_price = data.get('cost_price', 0)
    quantity = data.get('quantity', 0)
    group_id = data.get('group_id')
    notes = data.get('notes', '')

    conn = get_connection()
    cursor = conn.cursor()

    # 检查股票是否存在
    cursor.execute('SELECT id FROM stocks WHERE id = ?', (stock_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'message': '股票不存在'}), 404

    cursor.execute(
        """
        INSERT INTO holdings (stock_id, group_id, cost_price, quantity, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(stock_id) DO UPDATE SET
            group_id = excluded.group_id,
            cost_price = excluded.cost_price,
            quantity = excluded.quantity,
            notes = excluded.notes,
            updated_at = datetime('now', 'localtime')
    """,
        (stock_id, group_id, cost_price, quantity, notes),
    )

    holding_id = cursor.execute(
        'SELECT id FROM holdings WHERE stock_id = ?', (stock_id,)
    ).fetchone()['id']
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'holding_id': holding_id})


@bp.route('/api/portfolio/holdings/<int:stock_id>', methods=['DELETE'])
def api_delete_holding(stock_id):
    """删除持仓：交易流水保留（holding_id 置 NULL）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM holdings WHERE stock_id = ?', (stock_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'message': '持仓不存在'}), 404

    holding_id = row['id']
    # 交易流水的 holding_id 置 NULL（流水保留）
    cursor.execute('UPDATE trade_records SET holding_id = NULL WHERE holding_id = ?', (holding_id,))
    cursor.execute('DELETE FROM holdings WHERE id = ?', (holding_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '持仓已删除，交易流水已保留'})


# ============================================================
# 交易流水 CRUD + 持仓重算联动
# ============================================================


def _recalculate_holding(cursor, stock_id):
    """根据该股票所有有效流水（按时间顺序）重新计算持仓。
    计算规则：
    - 买入(buy)：增加数量，加权平均成本 = (旧持仓成本 + 新买入金额) / 新数量
    - 卖出(sell)：减少数量，已实现盈亏 += (卖出价 - 持仓均价) * 卖出数量
    - 分红(dividend)：数量不变，已实现盈亏 += 分红金额(amount)
    - 若重算后数量 ≤ 0，标记 status='cleared'（保留记录，不物理删除）
    返回重算后的持仓快照 dict。
    """
    # 确保持仓记录存在
    cursor.execute('SELECT id FROM holdings WHERE stock_id=?', (stock_id,))
    h_row = cursor.fetchone()
    if not h_row:
        # 自动创建持仓记录
        cursor.execute(
            'INSERT OR IGNORE INTO holdings (stock_id, cost_price, quantity, realized_pnl, status) '
            'VALUES (?, 0, 0, 0, "active")',
            (stock_id,),
        )
        cursor.execute('SELECT id FROM holdings WHERE stock_id=?', (stock_id,))
        h_row = cursor.fetchone()

    holding_id = h_row['id']

    # 按时间顺序获取所有有效流水
    cursor.execute(
        """
        SELECT trade_type, price, quantity, amount, trade_date, created_at
        FROM trade_records
        WHERE stock_id=?
        ORDER BY trade_date ASC, created_at ASC
    """,
        (stock_id,),
    )
    trades = cursor.fetchall()

    total_qty = 0  # 总持仓数量
    avg_cost = 0.0  # 加权平均成本
    total_cost = 0.0  # 总成本（用于计算均价）
    realized_pnl = 0.0  # 已实现盈亏

    for t in trades:
        qty = int(t['quantity'] or 0)
        price = float(t['price'] or 0)
        amount = float(t['amount'] or 0)

        if t['trade_type'] == 'buy':
            if qty > 0:
                total_cost += qty * price
                total_qty += qty
                avg_cost = total_cost / total_qty if total_qty > 0 else 0
        elif t['trade_type'] == 'sell':
            if qty > 0:
                sell_qty = min(qty, total_qty) if total_qty > 0 else qty
                realized_pnl += (price - avg_cost) * sell_qty
                total_qty -= qty
                if total_qty <= 0:
                    total_qty = 0
                    total_cost = 0
                else:
                    total_cost = avg_cost * total_qty
        elif t['trade_type'] == 'dividend':
            # 分红：金额直接计入已实现盈亏
            realized_pnl += max(0, amount)

    # 判断状态
    status = 'cleared' if total_qty <= 0 else 'active'

    # 更新持仓表
    cursor.execute(
        """
        UPDATE holdings SET
            quantity = ?,
            cost_price = ?,
            realized_pnl = ?,
            status = ?,
            updated_at = datetime('now', 'localtime')
        WHERE stock_id = ?
    """,
        (total_qty, round(avg_cost, 4), round(realized_pnl, 2), status, stock_id),
    )

    return {
        'stock_id': stock_id,
        'holding_id': holding_id,
        'quantity': total_qty,
        'avg_cost': round(avg_cost, 4),
        'realized_pnl': round(realized_pnl, 2),
        'total_value': round(avg_cost * total_qty, 2),
        'status': status,
    }


@bp.route('/api/portfolio/holdings/<int:stock_id>/trades', methods=['GET'])
def api_get_trades(stock_id):
    """查看某只股票的交易流水"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT tr.*, s.symbol, s.name
        FROM trade_records tr
        INNER JOIN stocks s ON tr.stock_id = s.id
        WHERE tr.stock_id = ?
        ORDER BY tr.trade_date DESC, tr.created_at DESC
    """,
        (stock_id,),
    )
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'trades': trades, 'count': len(trades)})


@bp.route('/api/portfolio/trades', methods=['GET'])
def api_get_all_trades():
    """全局交易流水列表（所有股票，支持分页与筛选）"""
    conn = get_connection()
    cursor = conn.cursor()
    trade_type = request.args.get('type', '')
    sql = """
        SELECT tr.*, s.symbol, s.name, s.market
        FROM trade_records tr
        INNER JOIN stocks s ON tr.stock_id = s.id
    """
    params = []
    if trade_type in ('buy', 'sell', 'dividend'):
        sql += ' WHERE tr.trade_type = ?'
        params.append(trade_type)
    sql += ' ORDER BY tr.trade_date DESC, tr.created_at DESC'
    cursor.execute(sql, params)
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()
    # 汇总统计
    total_buy = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'buy')
    total_sell = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'sell')
    total_dividend = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'dividend')
    return jsonify(
        {
            'success': True,
            'trades': trades,
            'count': len(trades),
            'summary': {
                'total_buy_amount': round(total_buy, 2),
                'total_sell_amount': round(total_sell, 2),
                'total_dividend': round(total_dividend, 2),
                'net_amount': round(total_sell + total_dividend - total_buy, 2),
            },
        }
    )


@bp.route('/api/portfolio/cost-adjustments', methods=['GET'])
def api_get_all_cost_adjustments():
    """全局成本修正历史列表（所有持仓，审计追溯）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT pca.*, h.stock_id, s.symbol, s.name
        FROM position_cost_adjustments pca
        LEFT JOIN holdings h ON pca.holding_id = h.id
        LEFT JOIN stocks s ON h.stock_id = s.id
        ORDER BY pca.created_at DESC
    """)
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # FIX-ADJUST-UI：字段别名映射，使返回JSON与前端期望字段名一致
    for rec in records:
        rec['original_avg_cost'] = rec.pop('old_cost', None)
        rec['adjusted_avg_cost'] = rec.pop('new_cost', None)
        rec['adjustment_reason'] = rec.pop('reason', None)
        rec['adjustment_notes'] = rec.get('operator_ip', '') or ''

    return jsonify({'success': True, 'adjustments': records, 'count': len(records)})


@bp.route('/api/portfolio/holdings/<int:stock_id>/trades', methods=['POST'])
def api_add_trade(stock_id):
    """新增交易流水记录（自动触发持仓重算）"""
    data = request.get_json(silent=True) or {}
    trade_type = data.get('trade_type', '').strip()
    if trade_type not in ('buy', 'sell', 'dividend'):
        return jsonify({'success': False, 'message': 'trade_type 必须为 buy/sell/dividend'}), 400

    price = data.get('price', 0)
    quantity = data.get('quantity', 0)
    amount = data.get('amount') or (float(price) * int(quantity) if quantity else 0)
    trade_date = data.get('trade_date', '')
    notes = data.get('notes', '')

    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')  # 加锁，防止并发冲突

        # 获取 holding_id（如果持仓存在）
        cursor.execute('SELECT id FROM holdings WHERE stock_id = ?', (stock_id,))
        h = cursor.fetchone()
        holding_id = h['id'] if h else None

        cursor.execute(
            """
            INSERT INTO trade_records (holding_id, stock_id, trade_type, price, quantity, amount, trade_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (holding_id, stock_id, trade_type, price, quantity, amount, trade_date, notes),
        )

        trade_id = cursor.lastrowid

        # 触发持仓重算
        recalculated = _recalculate_holding(cursor, stock_id)

        conn.commit()
        conn.close()
        return jsonify(
            {'success': True, 'trade_id': trade_id, 'recalculated_position': recalculated}
        )
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'新增流水失败（已回滚）：{e}'}), 500


@bp.route('/api/portfolio/trades/<int:trade_id>', methods=['PUT'])
def api_update_trade(trade_id):
    """编辑交易流水（触发持仓重算，事务保护）。
    Body 可包含：trade_type, price, quantity, amount, trade_date, notes, stock_id(跨股票编辑)
    """
    data = request.get_json(silent=True) or {}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')

        # 操作限制检查（已清算 / T+1锁定 / 大额二次验证）
        allowed, err_msg, status_code = _check_trade_edit_restriction(
            cursor, trade_id, 'edit', force_confirm=data.get('force_confirm', False)
        )
        if not allowed:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': err_msg}), status_code

        # 获取流水原始记录
        cursor.execute('SELECT * FROM trade_records WHERE id=?', (trade_id,))
        trade = cursor.fetchone()
        if not trade:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': '流水记录不存在'}), 404

        old_stock_id = trade['stock_id']
        new_stock_id = data.get('stock_id', old_stock_id)

        # 动态构建 UPDATE 语句
        fields = []
        params = []
        for col in ['trade_type', 'price', 'quantity', 'amount', 'trade_date', 'notes', 'stock_id']:
            if col in data:
                fields.append(f'{col} = ?')
                params.append(data[col])

        if fields:
            # 校验 trade_type
            if 'trade_type' in data and data['trade_type'] not in ('buy', 'sell', 'dividend'):
                conn.rollback()
                conn.close()
                return jsonify(
                    {'success': False, 'message': 'trade_type 必须为 buy/sell/dividend'}
                ), 400

            # 如果 stock_id 变了，更新 holding_id 指向
            if new_stock_id != old_stock_id:
                cursor.execute('SELECT id FROM holdings WHERE stock_id=?', (new_stock_id,))
                new_h = cursor.fetchone()
                new_holding_id = new_h['id'] if new_h else None
                fields.append('holding_id = ?')
                params.append(new_holding_id)

            params.append(trade_id)
            cursor.execute(f'UPDATE trade_records SET {", ".join(fields)} WHERE id=?', params)

            # 重算新股票持仓
            recalculated = _recalculate_holding(cursor, new_stock_id)

            # 如果跨股票编辑，还要重算原股票持仓
            if new_stock_id != old_stock_id:
                recalculated_old = _recalculate_holding(cursor, old_stock_id)
                conn.commit()
                conn.close()
                return jsonify(
                    {
                        'success': True,
                        'recalculated_position': recalculated,
                        'recalculated_old_position': recalculated_old,
                    }
                )
        else:
            recalculated = _recalculate_holding(cursor, old_stock_id)

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'recalculated_position': recalculated})

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'编辑流水失败（已回滚）：{e}'}), 500


@bp.route('/api/portfolio/trades/<int:trade_id>', methods=['DELETE'])
def api_delete_trade(trade_id):
    """删除交易流水（触发持仓重算，事务保护）。
    Body 可包含：force_confirm(bool) —— 单笔超 5 万流水删除需二次验证
    """
    data = request.get_json(silent=True) or {}
    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')

        # 操作限制检查（已清算 / T+1锁定 / 大额二次验证）
        allowed, err_msg, status_code = _check_trade_edit_restriction(
            cursor, trade_id, 'delete', force_confirm=data.get('force_confirm', False)
        )
        if not allowed:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': err_msg}), status_code

        cursor.execute('SELECT stock_id FROM trade_records WHERE id=?', (trade_id,))
        trade = cursor.fetchone()
        if not trade:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': '流水记录不存在'}), 404

        stock_id = trade['stock_id']
        cursor.execute('DELETE FROM trade_records WHERE id=?', (trade_id,))

        recalculated = _recalculate_holding(cursor, stock_id)

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'recalculated_position': recalculated})

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'删除流水失败（已回滚）：{e}'}), 500


# ============================================================
# 持仓成本人工修正 + 行情刷新 + 操作限制检查
# ============================================================


def _check_trade_edit_restriction(cursor, trade_id, operation='edit', force_confirm=False):
    """检查流水编辑/删除限制，返回 (allowed, error_msg, status_code)。
    限制规则：
    1. 已清算（持仓数量=0）的股票，禁止编辑/删除其历史流水
    2. T+1锁定：当日提交的流水次日才允许修改
    3. 单笔流水金额超过 TRADE_AMOUNT_VERIFY_THRESHOLD 时需二次验证
       （force_confirm=true 放行，与成本调整二次确认模式一致）
    """
    cursor.execute('SELECT * FROM trade_records WHERE id=?', (trade_id,))
    trade = cursor.fetchone()
    if not trade:
        return False, '流水记录不存在', 404

    stock_id = trade['stock_id']

    # 1. 检查持仓是否已清算
    cursor.execute('SELECT quantity, status FROM holdings WHERE stock_id=?', (stock_id,))
    h = cursor.fetchone()
    if h and (h['quantity'] or 0) <= 0:
        return (
            False,
            '该股票已清算（持仓数量=0），历史流水仅允许查看，禁止'
            + ('编辑' if operation == 'edit' else '删除'),
            403,
        )

    # 2. T+1锁定检查
    if TRADE_T1_LOCK_ENABLED:
        from datetime import datetime, timedelta

        created_str = trade['created_at']
        if created_str:
            try:
                created_dt = datetime.strptime(created_str, '%Y-%m-%d %H:%M:%S')
                if datetime.now() < created_dt + timedelta(days=1):
                    return (
                        False,
                        '当日提交的流水需 T+1 后才允许修改（流水创建时间：' + created_str + '）',
                        403,
                    )
            except (ValueError, TypeError):
                pass

    # 3. 单笔大额流水二次验证（force_confirm 放行）
    amount = trade['amount'] or 0
    if amount > TRADE_AMOUNT_VERIFY_THRESHOLD:
        if not force_confirm:
            return (
                False,
                f'单笔流水金额 {amount:,.0f} 元超过 {TRADE_AMOUNT_VERIFY_THRESHOLD:,.0f} 元阈值，'
                '需二次验证（force_confirm=true 确认后放行）',
                403,
            )

    return True, None, 200


@bp.route('/api/positions/<int:holding_id>/cost-adjustment', methods=['POST'])
def api_cost_adjustment(holding_id):
    """持仓成本人工修正接口。
    Body: adjusted_avg_cost(float), adjustment_reason(str), force_confirm(bool, optional)
    事务保护：修正记录写入 + 持仓更新 原子性提交。
    """
    data = request.get_json(silent=True) or {}
    adjusted_cost = data.get('adjusted_avg_cost')
    reason = (data.get('adjustment_reason') or '').strip()
    force_confirm = data.get('force_confirm', False)
    operator_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
    device_fp = data.get('device_fingerprint', '')

    # 基本校验
    if adjusted_cost is None:
        return jsonify({'success': False, 'message': 'adjusted_avg_cost 不能为空'}), 400
    try:
        adjusted_cost = float(adjusted_cost)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'adjusted_avg_cost 必须是数字'}), 400
    if adjusted_cost < 0:
        return jsonify({'success': False, 'message': '修正值不能为负'}), 400
    if not reason:
        return jsonify({'success': False, 'message': 'adjustment_reason 不能为空'}), 400

    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')  # 加锁

        # 获取当前持仓
        cursor.execute('SELECT * FROM holdings WHERE id=?', (holding_id,))
        holding = cursor.fetchone()
        if not holding:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': '持仓不存在'}), 404

        old_cost = holding['cost_price'] or 0
        stock_id = holding['stock_id']

        # 限制1：同一持仓 24 小时内最多修正 1 次
        from datetime import datetime, timedelta

        cursor.execute(
            """
            SELECT created_at FROM position_cost_adjustments
            WHERE holding_id=?
            ORDER BY created_at DESC LIMIT 1
        """,
            (holding_id,),
        )
        last_adj = cursor.fetchone()
        if last_adj:
            try:
                last_dt = datetime.strptime(last_adj['created_at'], '%Y-%m-%d %H:%M:%S')
                elapsed = datetime.now() - last_dt
                if elapsed < timedelta(hours=COST_ADJUSTMENT_COOLDOWN_HOURS):
                    remaining = timedelta(hours=COST_ADJUSTMENT_COOLDOWN_HOURS) - elapsed
                    remaining_hours = round(remaining.total_seconds() / 3600, 1)
                    conn.rollback()
                    conn.close()
                    return jsonify(
                        {
                            'success': False,
                            'message': f'同一持仓 {COST_ADJUSTMENT_COOLDOWN_HOURS} 小时内仅允许修正 1 次，请 {remaining_hours} 小时后重试',
                        }
                    ), 429
            except (ValueError, TypeError):
                pass

        # 限制2：偏离度检查
        deviation_pct = 0.0
        if old_cost > 0:
            deviation_pct = abs(adjusted_cost - old_cost) / old_cost
        else:
            deviation_pct = 1.0 if adjusted_cost > 0 else 0.0

        if deviation_pct > COST_ADJUSTMENT_DEVIATION_THRESHOLD and not force_confirm:
            conn.rollback()
            conn.close()
            return jsonify(
                {
                    'success': False,
                    'message': f'修正值偏离当前成本 {round(deviation_pct * 100, 1)}%，超过 ±{int(COST_ADJUSTMENT_DEVIATION_THRESHOLD * 100)}% 阈值，需二次确认（force_confirm=true）',
                    'deviation_pct': round(deviation_pct, 4),
                    'old_cost': old_cost,
                    'new_cost': adjusted_cost,
                    'need_force_confirm': True,
                }
            ), 400

        # 事务：写入修正记录 + 更新持仓
        cursor.execute(
            """
            INSERT INTO position_cost_adjustments
                (holding_id, stock_id, old_cost, new_cost, reason, operator, operator_ip, device_fingerprint, deviation_pct)
            VALUES (?, ?, ?, ?, ?, 'user', ?, ?, ?)
        """,
            (
                holding_id,
                stock_id,
                old_cost,
                adjusted_cost,
                reason,
                operator_ip,
                device_fp,
                round(deviation_pct, 4),
            ),
        )

        cursor.execute(
            """
            UPDATE holdings SET
                cost_price = ?,
                is_cost_adjusted = 1,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
        """,
            (adjusted_cost, holding_id),
        )

        conn.commit()
        conn.close()

        return jsonify(
            {
                'success': True,
                'message': '成本修正成功',
                'adjustment': {
                    'holding_id': holding_id,
                    'stock_id': stock_id,
                    'old_cost': round(old_cost, 4),
                    'new_cost': round(adjusted_cost, 4),
                    'reason': reason,
                    'deviation_pct': round(deviation_pct, 4),
                    'adjusted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                },
            }
        )

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'成本修正失败（已回滚）：{e}'}), 500


@bp.route('/api/positions/<int:holding_id>/cost-adjustments', methods=['GET'])
def api_get_cost_adjustments(holding_id):
    """查询某持仓的成本修正历史记录（审计追溯）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM position_cost_adjustments
        WHERE holding_id=?
        ORDER BY created_at DESC
    """,
        (holding_id,),
    )
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'adjustments': records, 'count': len(records)})


@bp.route('/api/portfolio/holdings/<int:stock_id>/trade-suggestion', methods=['GET'])
def api_trade_suggestion(stock_id):
    """获取近3次同股票买入记录，用于预填推荐。
    返回 avg_price, avg_quantity, count, latest_trade_date
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT price, quantity, trade_date
        FROM trade_records
        WHERE stock_id=? AND trade_type='buy'
        ORDER BY trade_date DESC, created_at DESC
        LIMIT 3
    """,
        (stock_id,),
    )
    trades = cursor.fetchall()
    conn.close()

    if not trades:
        return jsonify({'success': True, 'suggestion': None})

    prices = [t['price'] for t in trades if t['price']]
    qtys = [t['quantity'] for t in trades if t['quantity']]
    avg_price = sum(prices) / len(prices) if prices else None
    avg_qty = sum(qtys) / len(qtys) if qtys else None

    return jsonify(
        {
            'success': True,
            'suggestion': {
                'avg_price': round(avg_price, 4) if avg_price else None,
                'avg_quantity': int(avg_qty) if avg_qty else None,
                'count': len(trades),
                'latest_trade_date': trades[0]['trade_date'],
            },
        }
    )


@bp.route('/api/analytics/prefill', methods=['POST'])
def api_prefill_analytics():
    """预填埋点统计：记录预填字段使用率、修改率、二次确认触发率。
    轻量实现：写入 error_logs 表复用（module='prefill_analytics'）。
    """
    data = request.get_json(silent=True) or {}
    event_type = data.get(
        'event_type', ''
    )  # prefill_shown / field_modified / cost_confirm_triggered
    stock_id = data.get('stock_id')
    detail = data.get('detail', '')

    if not event_type:
        return jsonify({'success': False, 'message': 'event_type 不能为空'}), 400

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO error_logs (stock_id, module, error_type, error_message)
        VALUES (?, 'prefill_analytics', ?, ?)
    """,
        (stock_id, event_type, detail),
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '埋点已记录'})


@bp.route('/api/portfolio/realized-pnl', methods=['GET'])
def api_realized_pnl():
    """已实现盈亏精确查询（加权平均成本法）。

    计算方法：加权平均法（Weighted Average Cost）
    - 买入(buy)：total_cost += qty × price，加权平均成本 = total_cost / total_qty
    - 卖出(sell)：realized_pnl += (卖出价 - 加权平均成本) × 卖出数量
    - 分红(dividend)：realized_pnl += 分红金额
    - 不含交易手续费（与券商对账单口径一致）

    参数：
      stock_id  - 可选，筛选特定股票
      period    - daily / weekly / monthly（默认不聚合，返回总额）
      start_date - 可选，筛选起始日期
      end_date   - 可选，筛选结束日期
    """
    stock_id = request.args.get('stock_id', '')
    period = request.args.get('period', '')  # daily/weekly/monthly
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    conn = get_connection()
    cursor = conn.cursor()

    # 构建查询条件
    where_clauses = []
    params = []
    if stock_id:
        where_clauses.append('stock_id = ?')
        params.append(stock_id)
    if start_date:
        where_clauses.append('trade_date >= ?')
        params.append(start_date)
    if end_date:
        where_clauses.append('trade_date <= ?')
        params.append(end_date)

    where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'

    cursor.execute(
        f"""
        SELECT tr.stock_id, tr.trade_type, tr.price, tr.quantity, tr.amount,
               tr.trade_date, tr.created_at, s.symbol, s.name
        FROM trade_records tr
        INNER JOIN stocks s ON tr.stock_id = s.id
        WHERE {where_sql}
        ORDER BY tr.stock_id ASC, tr.trade_date ASC, tr.created_at ASC
    """,
        params,
    )
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # ---- 按股票分组逐笔计算（加权平均法）----
    stock_map = {}  # {stock_id: {symbol, name, trades, realized_pnl, ...}}
    for t in trades:
        sid = t['stock_id']
        if sid not in stock_map:
            stock_map[sid] = {
                'stock_id': sid,
                'symbol': t['symbol'],
                'name': t['name'],
                'total_qty': 0,
                'avg_cost': 0.0,
                'total_cost': 0.0,
                'realized_pnl': 0.0,
                'dividend_received': 0.0,
                'buy_count': 0,
                'sell_count': 0,
                'dividend_count': 0,
                'periods': {},  # 用于按日/周/月聚合
            }
        s = stock_map[sid]
        qty = int(t['quantity'] or 0)
        price = float(t['price'] or 0)
        amount = float(t['amount'] or 0)
        tdate = t['trade_date'] or ''

        if t['trade_type'] == 'buy':
            if qty > 0:
                s['total_cost'] += qty * price
                s['total_qty'] += qty
                s['avg_cost'] = s['total_cost'] / s['total_qty'] if s['total_qty'] > 0 else 0
            s['buy_count'] += 1
            pnl_delta = 0.0
        elif t['trade_type'] == 'sell':
            if qty > 0 and s['total_qty'] > 0:
                sell_qty = min(qty, s['total_qty'])
                pnl_delta = (price - s['avg_cost']) * sell_qty
                s['realized_pnl'] += pnl_delta
                s['total_qty'] -= qty
                if s['total_qty'] <= 0:
                    s['total_qty'] = 0
                    s['total_cost'] = 0
                else:
                    s['total_cost'] = s['avg_cost'] * s['total_qty']
            else:
                pnl_delta = 0.0
            s['sell_count'] += 1
        elif t['trade_type'] == 'dividend':
            pnl_delta = amount if amount > 0 else 0.0
            s['realized_pnl'] += pnl_delta
            s['dividend_received'] += pnl_delta
            s['dividend_count'] += 1
        else:
            pnl_delta = 0.0

        # 按期间聚合
        if period and pnl_delta != 0:
            if period == 'daily':
                key = tdate
            elif period == 'weekly':
                # ISO 周
                try:
                    from datetime import datetime as _dt

                    d = _dt.strptime(tdate, '%Y-%m-%d')
                    iso_year, iso_week, _ = d.isocalendar()
                    key = f'{iso_year}-W{iso_week:02d}'
                except (ValueError, TypeError):
                    key = tdate[:7] if len(tdate) >= 7 else tdate
            elif period == 'monthly':
                key = tdate[:7] if len(tdate) >= 7 else tdate
            else:
                key = tdate

            if key not in s['periods']:
                s['periods'][key] = 0.0
            s['periods'][key] += pnl_delta

    # 汇总
    total_realized = 0.0
    total_dividend = 0.0
    results = []
    for sid, s in stock_map.items():
        pnl = round(s['realized_pnl'], 2)
        total_realized += pnl
        total_dividend += s['dividend_received']
        row = {
            'stock_id': sid,
            'symbol': s['symbol'],
            'name': s['name'],
            'realized_pnl': pnl,
            'dividend_received': round(s['dividend_received'], 2),
            'current_qty': s['total_qty'],
            'avg_cost': round(s['avg_cost'], 4),
            'buy_count': s['buy_count'],
            'sell_count': s['sell_count'],
            'dividend_count': s['dividend_count'],
        }
        if period:
            row['period_breakdown'] = {k: round(v, 2) for k, v in sorted(s['periods'].items())}
        results.append(row)

    results.sort(key=lambda x: x['realized_pnl'], reverse=True)

    return jsonify(
        {
            'success': True,
            'method': 'weighted_average_cost',
            'fee_included': False,
            'total_realized_pnl': round(total_realized, 2),
            'total_dividend': round(total_dividend, 2),
            'count': len(results),
            'details': results,
        }
    )


def _fetch_realtime_price_batch(symbols_markets):
    """批量获取实时行情价格（腾讯接口）。
    symbols_markets: [(stock_id, symbol, market), ...]
    返回: {stock_id: {'price': float, 'pct_change': float}}
    019Y T1：腾讯主源缺失的 A股 自动降级 mootdx（通达信行情，TCP socket，不经过 requests patch）。
    """
    import logging as _logging019y

    import requests as _requests

    _log019y = _logging019y.getLogger(__name__)

    result = {}
    if not symbols_markets:
        return result

    # 构造腾讯批量请求代码：sh600000,sz000001,hk00700
    tencent_codes = []
    stock_id_map = {}
    for stock_id, symbol, market in symbols_markets:
        if market == 'hk_stock':
            # 港股：5位数字
            hk_code = symbol.zfill(5)
            tc = 'hk' + hk_code
        elif market == 'a_stock':
            if symbol.startswith('6'):
                tc = 'sh' + symbol
            else:
                tc = 'sz' + symbol
        else:
            continue
        tencent_codes.append(tc)
        stock_id_map[tc] = stock_id

    if not tencent_codes:
        return result

    # 腾讯批量行情接口（逗号分隔，最多约50只一次）
    batch_size = 40
    for i in range(0, len(tencent_codes), batch_size):
        batch = tencent_codes[i : i + batch_size]
        url = 'https://qt.gtimg.cn/q=' + ','.join(batch)
        try:
            resp = _requests.get(url, timeout=8)
            lines = resp.text.strip().split(';')
            for line in lines:
                line = line.strip()
                if not line or '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) < 5:
                    continue
                # 从原始行中提取代码
                var_match = line.split('=')[0].strip()
                tc_code = var_match.replace('v_', '').strip()
                sid = stock_id_map.get(tc_code)
                if not sid:
                    continue
                # parts[1]=名称, parts[3]=最新价, parts[32]=涨跌幅(%)
                try:
                    price = float(parts[3]) if parts[3] else 0
                    pct = float(parts[32]) if len(parts) > 32 and parts[32] else 0
                    if price > 0:
                        result[sid] = {'price': price, 'pct_change': pct}
                except (ValueError, IndexError):
                    continue
        except Exception:
            continue

    # 019Y T1：腾讯主源缺失的 A股 → mootdx 降级（每只一次 socket 查询，量小）
    missing_a = [
        (stock_id, symbol)
        for stock_id, symbol, market in symbols_markets
        if market == 'a_stock' and stock_id not in result
    ]
    if missing_a:
        try:
            from modules.data_collector import get_realtime_quote_mootdx

            for stock_id, symbol in missing_a:
                q = get_realtime_quote_mootdx(symbol)
                if q and q.get('price') and q['price'] > 0:
                    result[stock_id] = {'price': q['price'], 'pct_change': q.get('pct_change')}
                    _log019y.info(
                        f'[019Y] {symbol} 实时价格走 mootdx 降级: price={q["price"]}'
                    )
        except Exception as e:
            _log019y.warning(f'[019Y] mootdx 实时价格降级失败: {e}')

    return result


@bp.route('/api/portfolio/refresh-prices', methods=['POST'])
def api_refresh_prices():
    """刷新股票的最新价格（实时行情采集）。
    数据源：腾讯财经实时行情接口（免费、无需密钥）。
    同步刷新持仓 + 自选股的 price_cache。
    失败时保留旧缓存，禁止价格归零。
    """
    import time as _time

    start_ts = _time.time()
    data_source = 'tencent_realtime'
    fetch_errors = []

    conn = get_connection()
    cursor = conn.cursor()

    # 获取所有需要刷新价格的股票（持仓 + 自选股 合并去重）
    cursor.execute("""
        SELECT DISTINCT s.id as stock_id, s.symbol, s.market
        FROM stocks s
        WHERE s.status != 'delisted'
    """)
    all_stocks = [(row['stock_id'], row['symbol'], row['market']) for row in cursor.fetchall()]

    # 尝试实时采集
    price_data = _fetch_realtime_price_batch(all_stocks)
    realtime_count = len(price_data)

    updated_count = 0
    fallback_count = 0

    for stock_id, symbol, market in all_stocks:
        if stock_id in price_data:
            # 实时价格写入缓存
            p = price_data[stock_id]
            cursor.execute(
                """
                INSERT OR REPLACE INTO price_cache (stock_id, latest_price, pct_change, updated_at)
                VALUES (?, ?, ?, datetime('now', 'localtime'))
            """,
                (stock_id, p['price'], p['pct_change']),
            )
            updated_count += 1
        else:
            # 降级：从最新K线获取收盘价（保留旧缓存，不归零）
            cursor.execute(
                """
                SELECT close, pct_change FROM raw_kline
                WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1
            """,
                (stock_id,),
            )
            kline = cursor.fetchone()
            if kline and kline['close']:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO price_cache (stock_id, latest_price, pct_change, updated_at)
                    VALUES (?, ?, ?, datetime('now', 'localtime'))
                """,
                    (stock_id, kline['close'], kline['pct_change']),
                )
                fallback_count += 1
            else:
                fetch_errors.append(f'{symbol}: 无数据源')

    conn.commit()
    conn.close()

    fetch_duration_ms = int((_time.time() - start_ts) * 1000)

    return jsonify(
        {
            'success': True,
            'message': f'已刷新 {updated_count + fallback_count}/{len(all_stocks)} 只股票的价格',
            'updated_count': updated_count + fallback_count,
            'realtime_count': realtime_count,
            'fallback_count': fallback_count,
            'total': len(all_stocks),
            'data_source': data_source,
            'fetch_duration_ms': fetch_duration_ms,
            'errors': fetch_errors[:10],  # 最多返回10条错误
        }
    )


# ============================================================
# v5.0 四维评分引擎原型 API
# ============================================================
