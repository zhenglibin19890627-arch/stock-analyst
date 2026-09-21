"""持仓域路由：持仓列表/组合汇总（GET summary）+ 持仓录入与删除
（原 portfolio.py 区段逐字搬运）。

注意（AGENTS.md §3 多交易账户约定）：同股可多账户分仓，聚合视图按
_parse_account_scope 的口径处理。"""

import json

from flask import jsonify, request

from blueprints._utils import _latest_report_join_sql
from blueprints.portfolio import bp
from blueprints.portfolio._scope import (
    _account_exists,
    _get_default_account_id,
    _parse_account_scope,
)
from database.db_manager import get_connection


@bp.route('/api/portfolio/holdings', methods=['GET'])
def api_get_holdings():
    """获取持仓列表（含股票信息 + 最新价格缓存）。

    021S 多账户：?account_id=<id> 按账户过滤；缺省/'all' 返回全部账户持仓，
    每行附带 account_id / account_name 供前端区分展示。
    """
    group_id = request.args.get('group_id', '')
    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        SELECT h.id, h.account_id, h.stock_id, h.group_id, h.cost_price, h.quantity,
               h.notes, h.created_at, h.updated_at,
               h.realized_pnl, h.status, h.is_cost_adjusted,
               s.symbol, s.name, s.market,
               a.name as account_name,
               pg.name as group_name,
               pc.latest_price as latest_price, pc.pct_change as price_pct_change,
               pc.updated_at as price_cache_time
        FROM holdings h
        INNER JOIN stocks s ON h.stock_id = s.id
        LEFT JOIN accounts a ON h.account_id = a.id
        LEFT JOIN groups pg ON h.group_id = pg.id AND pg.type='portfolio'
        LEFT JOIN price_cache pc ON h.stock_id = pc.stock_id
        WHERE 1=1
    """
    params = []
    if group_id:
        sql += ' AND h.group_id = ?'
        params.append(group_id)
    if account_id is not None:
        sql += ' AND h.account_id = ?'
        params.append(account_id)
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
        # 021BO：配置常量在调用点经包 facade 取值（测试可 monkeypatch 蓝图模块属性）
        from blueprints.portfolio import PRICE_CACHE_TTL_HOURS

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

        # ---- 021BL：持仓盈亏比例（券商口径的 盈亏% 列） ----
        if price is not None and qty > 0 and cost_price > 0:
            h['unrealized_pnl_pct'] = round((price - cost_price) / cost_price * 100, 2)
        else:
            h['unrealized_pnl_pct'] = None

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
    try:
        account_scope = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    conn = get_connection()
    cursor = conn.cursor()

    # ---------- 1. 持仓汇总（021S：支持按账户过滤） ----------
    sql = """
        SELECT h.id, h.account_id, h.stock_id, h.group_id, h.cost_price, h.quantity,
               h.notes, h.created_at, h.updated_at,
               h.realized_pnl, h.status, h.is_cost_adjusted,
               s.symbol, s.name, s.market,
               a.name as account_name,
               pg.name as group_name,
               pc.latest_price as latest_price, pc.pct_change as price_pct_change,
               pc.updated_at as price_cache_time
        FROM holdings h
        INNER JOIN stocks s ON h.stock_id = s.id
        LEFT JOIN accounts a ON h.account_id = a.id
        LEFT JOIN groups pg ON h.group_id = pg.id AND pg.type='portfolio'
        LEFT JOIN price_cache pc ON h.stock_id = pc.stock_id
        WHERE 1=1
    """
    params = []
    if group_id:
        sql += ' AND h.group_id = ?'
        params.append(group_id)
    if account_scope is not None:
        sql += ' AND h.account_id = ?'
        params.append(account_scope)

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

    # ---------- 1.5 021S：全账户视图时输出分账户汇总 ----------
    accounts_breakdown = None
    if account_scope is None:
        _acc_map = {}
        for h in holdings:
            aid = h.get('account_id')
            if aid is None:
                continue
            m = _acc_map.setdefault(
                aid,
                {
                    'account_id': aid,
                    'account_name': h.get('account_name') or f'账户{aid}',
                    'total_market_value': 0.0,
                    'total_unrealized_pnl': 0.0,
                    'total_realized_pnl': 0.0,
                    'holding_count': 0,
                    'active_count': 0,
                    '_has_mv': False,
                    '_has_upnl': False,
                },
            )
            qty = h.get('quantity') or 0
            price = h.get('latest_price')
            cost_price = h.get('cost_price') or 0
            realized = h.get('realized_pnl') or 0
            m['holding_count'] += 1
            m['total_realized_pnl'] += realized
            if qty > 0:
                m['active_count'] += 1
            if price is not None and price > 0 and qty > 0:
                m['total_market_value'] += float(
                    decimal.Decimal(str(qty * price)).quantize(
                        decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                    )
                )
                m['_has_mv'] = True
                m['total_unrealized_pnl'] += float(
                    decimal.Decimal(str((price - cost_price) * qty)).quantize(
                        decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                    )
                )
                m['_has_upnl'] = True
        accounts_breakdown = []
        for m in sorted(_acc_map.values(), key=lambda x: x['account_id']):
            _has_mv = m.pop('_has_mv')
            _has_upnl = m.pop('_has_upnl')
            m['total_market_value'] = round(m['total_market_value'], 2) if _has_mv else None
            m['total_unrealized_pnl'] = round(m['total_unrealized_pnl'], 2) if _has_upnl else None
            m['total_realized_pnl'] = round(m['total_realized_pnl'], 2)
            accounts_breakdown.append(m)

    result = {
        'success': True,
        'account_scope': account_scope if account_scope is not None else 'all',
        'accounts_breakdown': accounts_breakdown,
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
    # 021AE：v5 单引擎——'history' 统计历史行（engine_version 为 NULL/旧值）
    engine_stats = {'v5': 0, 'history': 0}
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
            if ev == 'v5':
                engine_stats['v5'] += 1
            else:
                engine_stats['history'] += 1
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



@bp.route('/api/portfolio/holdings/<int:stock_id>', methods=['POST'])
def api_upsert_holding(stock_id):
    """创建或更新持仓（成本价/数量/分组/账户）。

    021S 多账户：body.account_id 指定归属账户（缺省=默认账户）；
    同一股票可在不同账户各有一条持仓，冲突键为 (account_id, stock_id)。
    """
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

    # 解析目标账户：显式指定 > 默认账户
    account_id = data.get('account_id')
    if account_id is not None:
        try:
            account_id = int(account_id)
        except (TypeError, ValueError):
            conn.close()
            return jsonify({'success': False, 'message': f'非法 account_id: {account_id}'}), 400
        if not _account_exists(cursor, account_id):
            conn.close()
            return jsonify({'success': False, 'message': '账户不存在'}), 404
    else:
        account_id = _get_default_account_id(cursor)
        if account_id is None:
            conn.close()
            return jsonify({'success': False, 'message': '系统无可用账户，请先创建'}), 500

    cursor.execute(
        """
        INSERT INTO holdings (account_id, stock_id, group_id, cost_price, quantity, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(account_id, stock_id) DO UPDATE SET
            group_id = excluded.group_id,
            cost_price = excluded.cost_price,
            quantity = excluded.quantity,
            notes = excluded.notes,
            updated_at = datetime('now', 'localtime')
    """,
        (account_id, stock_id, group_id, cost_price, quantity, notes),
    )

    holding_id = cursor.execute(
        'SELECT id FROM holdings WHERE stock_id = ? AND account_id = ?',
        (stock_id, account_id),
    ).fetchone()['id']
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'holding_id': holding_id, 'account_id': account_id})


@bp.route('/api/portfolio/holdings/<int:stock_id>', methods=['DELETE'])
def api_delete_holding(stock_id):
    """删除持仓：交易流水保留（holding_id 置 NULL）。

    021S 多账户：同一股票可能存在多条（分账户）持仓。
    - ?account_id=<id> 删除指定账户的持仓
    - 缺省时若该股票仅一条持仓则直接删除；多条则返回 409 要求指定账户
    """
    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    conn = get_connection()
    cursor = conn.cursor()
    if account_id is None:
        cursor.execute('SELECT id, account_id FROM holdings WHERE stock_id = ?', (stock_id,))
        rows = cursor.fetchall()
        if not rows:
            conn.close()
            return jsonify({'success': False, 'message': '持仓不存在'}), 404
        if len(rows) > 1:
            conn.close()
            return jsonify(
                {
                    'success': False,
                    'message': '该股票在多个账户均有持仓，请指定 account_id 后删除',
                    'holdings': [dict(r) for r in rows],
                }
            ), 409
        holding_id = rows[0]['id']
    else:
        row = cursor.execute(
            'SELECT id FROM holdings WHERE stock_id = ? AND account_id = ?',
            (stock_id, account_id),
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'message': '该账户下不存在此股票的持仓'}), 404
        holding_id = row['id']

    # 交易流水的 holding_id 置 NULL（流水保留，账户归属不变）
    cursor.execute('UPDATE trade_records SET holding_id = NULL WHERE holding_id = ?', (holding_id,))
    cursor.execute('DELETE FROM holdings WHERE id = ?', (holding_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '持仓已删除，交易流水已保留'})


