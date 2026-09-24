"""交易流水域路由：/api/portfolio/holdings/<id>/trades 与 /api/portfolio/trades
CRUD + 持仓重算口径 _recalculate_holding（原 portfolio.py 区段逐字搬运）。

⚠️ _recalculate_holding 为（股票, 账户）维度隔离重算（AGENTS.md §3 多交易账户），
改流水逻辑必须保持该口径。"""

import math

from flask import jsonify, request

from blueprints.portfolio import bp
from blueprints.portfolio._scope import (
    _account_exists,
    _get_default_account_id,
    _parse_account_scope,
)
from blueprints.portfolio.controls import _check_trade_edit_restriction
from database.db_manager import get_connection
from modules.trade_fees import estimate_trade_fee_items


def _query_float(raw):
    """021BW：查询串数字解析——缺省/空串 → 0.0；非法/非有限数 → None（调用方转 400）。"""
    if raw is None or str(raw).strip() == '':
        return 0.0
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return None
    return val if math.isfinite(val) else None

# ============================================================
# 交易流水 CRUD + 持仓重算联动
# ============================================================


def _recalculate_holding(cursor, stock_id, account_id):
    """根据该（股票, 账户）下所有有效流水（按时间顺序）重新计算持仓。
    021S 多账户：重算范围严格限定在单一账户内，账户间成本/盈亏互不影响。
    计算规则（2026-09-18 起卖出改摊薄成本法，用户口径）：
    - 买入(buy)：增加数量，加权平均成本 = (旧持仓成本 + 新买入金额) / 新数量
    - 卖出(sell)：摊薄成本法——
      · 部分卖出（卖出后仍持仓）：卖出净得（成交额-费用）从总成本中扣除，
        剩余成本 = (旧总成本 - 卖出净得) / 剩余数量 → 成本价被利润平摊降低，
        不记已实现盈亏（仍持仓中，盈亏继续体现在浮动盈亏里）
      · 清仓卖出：剩余总成本与卖出净得的差额一次性转入已实现盈亏
        （数学上与旧逐笔口径的清仓累计值一致：累计卖出净得 - 总买入成本）
    - 分红(dividend)：数量不变，已实现盈亏 += 分红金额(amount)
    - 红利补税(dividend_tax)：数量不变，已实现盈亏 -= 补税金额(amount)——
      021AM：股息红利差异扣税（A股按持股期限补扣/港股通代扣20%）与派息日
      不同天发生，作独立流水记录，金额为补扣税款（正数存储）
    - 若重算后数量 ≤ 0，标记 status='cleared'（保留记录，不物理删除）
    返回重算后的持仓快照 dict。
    """
    # 确保持仓记录存在
    cursor.execute(
        'SELECT id FROM holdings WHERE stock_id=? AND account_id=?',
        (stock_id, account_id),
    )
    h_row = cursor.fetchone()
    if not h_row:
        # 自动创建持仓记录
        cursor.execute(
            'INSERT OR IGNORE INTO holdings (account_id, stock_id, cost_price, quantity, realized_pnl, status) '
            'VALUES (?, ?, 0, 0, 0, "active")',
            (account_id, stock_id),
        )
        cursor.execute(
            'SELECT id FROM holdings WHERE stock_id=? AND account_id=?',
            (stock_id, account_id),
        )
        h_row = cursor.fetchone()

    holding_id = h_row['id']

    # 按时间顺序获取本账户内所有有效流水
    cursor.execute(
        """
        SELECT trade_type, price, quantity, amount, commission, trade_date, created_at
        FROM trade_records
        WHERE stock_id=? AND account_id=?
        ORDER BY trade_date ASC, created_at ASC
    """,
        (stock_id, account_id),
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
        # 021X：手续费口径——买入计入成本基数，卖出/分红扣减已实现盈亏
        commission = float(t['commission'] or 0)

        if t['trade_type'] == 'buy':
            if qty > 0:
                # 021BL：优先实际成交金额（021AM 金额直填），缺失回退 价格×数量——与券商口径一致
                buy_amount = amount if amount > 0 else qty * price
                total_cost += buy_amount + commission
                total_qty += qty
                avg_cost = total_cost / total_qty if total_qty > 0 else 0
        elif t['trade_type'] == 'sell':
            if qty > 0:
                sell_qty = min(qty, total_qty) if total_qty > 0 else qty
                # 021BL：已实现盈亏按实际成交金额口径（券商一致）：卖出金额-费用-卖出数量×摊薄成本
                sell_amount = amount if amount > 0 else price * sell_qty
                if qty > sell_qty:
                    sell_amount = sell_amount * sell_qty / qty  # 超卖钳制时按比例折算
                sell_net = sell_amount - commission
                # 2026-09-18：摊薄成本法——卖出净得平摊到剩余持仓
                if total_qty > qty:
                    # 部分卖出：净得从总成本扣除 → 成本价被利润平摊降低；
                    # 不转已实现（仍持仓中，盈亏留在浮动盈亏里按新成本计算）
                    total_cost -= sell_net
                    total_qty -= qty
                    avg_cost = total_cost / total_qty if total_qty > 0 else 0
                else:
                    # 清仓卖出：剩余总成本与净得的差额一次性转已实现盈亏
                    realized_pnl += sell_net - total_cost
                    total_qty = 0
                    total_cost = 0
                    avg_cost = 0   # 021BL：清仓后成本价归零（券商口径），不再残留旧成本
        elif t['trade_type'] == 'dividend':
            # 分红：金额计入已实现盈亏（手续费/划扣费一并扣除）
            realized_pnl += max(0, amount) - commission
        elif t['trade_type'] == 'dividend_tax':
            # 021AM：红利补税——金额从已实现盈亏中扣减（amount 正数=补扣税款）
            realized_pnl -= max(0, amount) + commission

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
        WHERE id = ?
    """,
        (total_qty, round(avg_cost, 4), round(realized_pnl, 2), status, holding_id),
    )

    return {
        'stock_id': stock_id,
        'account_id': account_id,
        'holding_id': holding_id,
        'quantity': total_qty,
        'avg_cost': round(avg_cost, 4),
        'realized_pnl': round(realized_pnl, 2),
        'total_value': round(avg_cost * total_qty, 2),
        'status': status,
    }


@bp.route('/api/portfolio/holdings/<int:stock_id>/trades', methods=['GET'])
def api_get_trades(stock_id):
    """查看某只股票的交易流水（021S：可选 ?account_id= 按账户过滤）"""
    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        SELECT tr.*, s.symbol, s.name, a.name as account_name
        FROM trade_records tr
        INNER JOIN stocks s ON tr.stock_id = s.id
        LEFT JOIN accounts a ON tr.account_id = a.id
        WHERE tr.stock_id = ?
    """
    params = [stock_id]
    if account_id is not None:
        sql += ' AND tr.account_id = ?'
        params.append(account_id)
    sql += ' ORDER BY tr.trade_date DESC, tr.created_at DESC'
    cursor.execute(sql, params)
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'trades': trades, 'count': len(trades)})


@bp.route('/api/portfolio/trades', methods=['GET'])
def api_get_all_trades():
    """全局交易流水列表（所有股票，支持分页与筛选；021S 支持按账户过滤）"""
    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    conn = get_connection()
    cursor = conn.cursor()
    trade_type = request.args.get('type', '')
    sql = """
        SELECT tr.*, s.symbol, s.name, s.market, a.name as account_name
        FROM trade_records tr
        INNER JOIN stocks s ON tr.stock_id = s.id
        LEFT JOIN accounts a ON tr.account_id = a.id
        WHERE 1=1
    """
    params = []
    if trade_type in ('buy', 'sell', 'dividend', 'dividend_tax'):
        sql += ' AND tr.trade_type = ?'
        params.append(trade_type)
    if account_id is not None:
        sql += ' AND tr.account_id = ?'
        params.append(account_id)
    sql += ' ORDER BY tr.trade_date DESC, tr.created_at DESC'
    cursor.execute(sql, params)
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()
    # 汇总统计（021AM：红利补税单列并计入净流入）
    total_buy = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'buy')
    total_sell = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'sell')
    total_dividend = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'dividend')
    total_dividend_tax = sum(
        t['amount'] or 0 for t in trades if t.get('trade_type') == 'dividend_tax'
    )
    return jsonify(
        {
            'success': True,
            'trades': trades,
            'count': len(trades),
            'summary': {
                'total_buy_amount': round(total_buy, 2),
                'total_sell_amount': round(total_sell, 2),
                'total_dividend': round(total_dividend, 2),
                'total_dividend_tax': round(total_dividend_tax, 2),
                'net_amount': round(
                    total_sell + total_dividend - total_dividend_tax - total_buy, 2
                ),
            },
        }
    )


@bp.route('/api/portfolio/cost-adjustments', methods=['GET'])
def api_get_all_cost_adjustments():
    """全局成本修正历史列表（所有持仓，审计追溯）"""
    conn = get_connection()
    cursor = conn.cursor()
    # 020R-22：直接用修正记录自带的 stock_id 关联股票——
    # 此前经 holdings 关联，持仓被删后 holding 断链，h.stock_id(NULL) 还会覆盖
    # pca.stock_id 导致股票代码/名称丢失。
    # 021S：经 holding_id 弱关联带出账户名（断链时显示 '--'，不影响主字段）
    cursor.execute("""
        SELECT pca.*, s.symbol, s.name, a.name as account_name
        FROM position_cost_adjustments pca
        LEFT JOIN stocks s ON pca.stock_id = s.id
        LEFT JOIN holdings h ON pca.holding_id = h.id
        LEFT JOIN accounts a ON h.account_id = a.id
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


# ============================================================
# 021BW：录流水费用实时预估（纯计算零写库，GET）
# ============================================================


@bp.route('/api/portfolio/fee-estimate', methods=['GET'])
def api_fee_estimate():
    """录流水费用实时预估：按 价格×数量×买卖方向×所属账户券商配置 分项估算。

    021BW 轻量特性：纯计算零写库（至多三条 SELECT，无 INSERT/UPDATE/事务），
    与落库自动估算共用 modules/trade_fees 口径（resolve_broker_profile_for_account：
    broker 字段优先、账户名兜底，方案 A），预估条与落库佣金永不分叉。

    参数：trade_type（buy/sell/dividend/dividend_tax，必填）/ price / quantity /
    amount（直填优先，与 POST L277 同式）/ account_id（缺省=默认账户）/ stock_id
    （取 stocks.market 判定 A股/港股，缺省按 a_stock；港股印花税口径差异显著，建议携带）。
    amount<=0 不是错误：返回 200 全零 + 空态文案（前端防抖高频触发下无错误分支最简）。
    """
    trade_type = (request.args.get('trade_type') or '').strip()
    if trade_type not in ('buy', 'sell', 'dividend', 'dividend_tax'):
        return jsonify(
            {'success': False, 'message': 'trade_type 必须为 buy/sell/dividend/dividend_tax'}
        ), 400

    price = _query_float(request.args.get('price'))
    quantity_raw = _query_float(request.args.get('quantity'))
    amount_direct = _query_float(request.args.get('amount'))
    if price is None or quantity_raw is None or amount_direct is None:
        return jsonify({'success': False, 'message': 'price/quantity/amount 必须是数字'}), 400
    quantity = int(quantity_raw)  # 同 POST int(quantity) 截断
    amount = amount_direct or (price * quantity if quantity else 0)  # 与 POST 落库基数同式

    conn = get_connection()
    cursor = conn.cursor()

    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        conn.close()
        return jsonify({'success': False, 'message': str(e)}), 400

    account_name = None
    broker = None
    if account_id is not None:
        if not _account_exists(cursor, account_id):
            conn.close()
            return jsonify({'success': False, 'message': '账户不存在'}), 404
    else:
        account_id = _get_default_account_id(cursor)

    if account_id is not None:
        cursor.execute('SELECT name, broker FROM accounts WHERE id = ?', (account_id,))
        acc_row = cursor.fetchone()
        if acc_row:
            account_name = acc_row['name']
            broker = acc_row['broker']

    market = 'a_stock'
    stock_id = None
    raw_stock = request.args.get('stock_id')
    if raw_stock not in (None, ''):
        try:
            stock_id = int(raw_stock)
        except (TypeError, ValueError):
            conn.close()
            return jsonify({'success': False, 'message': f'非法 stock_id: {raw_stock}'}), 400
        cursor.execute('SELECT market FROM stocks WHERE id = ?', (stock_id,))
        stock_row = cursor.fetchone()
        if stock_row and stock_row['market']:
            market = stock_row['market']
    conn.close()

    items = estimate_trade_fee_items(
        trade_type, amount,
        account_name=account_name, market=market, broker=broker,
    )
    estimation_note = '估算值仅供参考，以次日交割单为准；可在编辑流水中改为实际值'
    if str(market) == 'hk_stock':
        estimation_note += '；港股为简化模型，未含组合费/汇兑等'

    return jsonify(
        {
            'success': True,
            'input': {
                'account_id': account_id,
                'stock_id': stock_id,
                'market': market,
                'trade_type': trade_type,
                'price': price,
                'quantity': quantity,
                'amount': amount,
            },
            'account_name': account_name,
            'broker_label': items['broker_label'],
            'commission': items['commission'],
            'stamp_tax': items['stamp_tax'],
            'transfer_fee': items['transfer_fee'],
            'misc_fee': items['misc_fee'],
            'total': items['total'],
            'by_rate': items['by_rate'],
            'applied_rules': items['applied_rules'],
            'estimation_note': estimation_note,
        }
    )


@bp.route('/api/portfolio/holdings/<int:stock_id>/trades', methods=['POST'])
def api_add_trade(stock_id):
    """新增交易流水记录（自动触发持仓重算）。

    021S 多账户：body.account_id 指定归属账户（缺省=默认账户），
    重算仅影响该账户内该股票的持仓。
    """
    data = request.get_json(silent=True) or {}
    trade_type = data.get('trade_type', '').strip()
    # 021AM：dividend_tax=红利补税（差异扣税独立流水，日期与派息日不同）
    if trade_type not in ('buy', 'sell', 'dividend', 'dividend_tax'):
        return jsonify({'success': False, 'message': 'trade_type 必须为 buy/sell/dividend/dividend_tax'}), 400

    price = data.get('price', 0)
    quantity = data.get('quantity', 0)
    amount = data.get('amount') or (float(price) * int(quantity) if quantity else 0)
    trade_date = data.get('trade_date', '')
    notes = data.get('notes', '')
    # 021X：手续费（可选，默认0；买入计入成本，卖出/分红扣减已实现盈亏）
    # 021BK：佣金留空 → 自动估算（按账户匹配券商费率，隔天交割单出来后可改实际值）
    raw_commission = data.get('commission')
    commission_explicit = raw_commission is not None and str(raw_commission).strip() != ''
    try:
        commission = float(raw_commission or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'commission 必须是数字'}), 400
    if commission < 0:
        return jsonify({'success': False, 'message': '手续费不能为负数'}), 400
    commission_estimated = 0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')  # 加锁，防止并发冲突

        # 解析目标账户（显式指定 > 默认账户），并校验存在性
        account_id = data.get('account_id')
        if account_id is not None:
            try:
                account_id = int(account_id)
            except (TypeError, ValueError):
                conn.rollback()
                conn.close()
                return jsonify({'success': False, 'message': f'非法 account_id: {account_id}'}), 400
            if not _account_exists(cursor, account_id):
                conn.rollback()
                conn.close()
                return jsonify({'success': False, 'message': '账户不存在'}), 404
        else:
            account_id = _get_default_account_id(cursor)
            if account_id is None:
                conn.rollback()
                conn.close()
                return jsonify({'success': False, 'message': '系统无可用账户，请先创建'}), 500

        # 021BK：佣金留空 → 按账户券商费率自动估算（A股：佣金+印花税(卖)+过户费）
        # 021BW 方案 A：与预估端点同口径——SELECT 增 broker，broker 优先、账户名兜底
        # （存量账户 broker 为空串 → 兜底账户名匹配，行为与旧口径完全一致）
        if not commission_explicit and trade_type in ('buy', 'sell'):
            try:
                cursor.execute('SELECT name, broker FROM accounts WHERE id = ?', (account_id,))
                acc_row = cursor.fetchone()
                cursor.execute('SELECT market FROM stocks WHERE id = ?', (stock_id,))
                stock_row = cursor.fetchone()
                commission = estimate_trade_fee_items(
                    trade_type, amount,
                    account_name=(acc_row['name'] if acc_row else None),
                    market=(stock_row['market'] if stock_row else 'a_stock'),
                    broker=(acc_row['broker'] if acc_row else None),
                )['total']
                commission_estimated = 1 if commission > 0 else 0
            except Exception:  # noqa: BLE001 — 估算失败不影响录入（回落为 0）
                commission = 0.0
                commission_estimated = 0

        # 获取本账户内该股票的 holding_id（如果持仓存在）
        cursor.execute(
            'SELECT id FROM holdings WHERE stock_id = ? AND account_id = ?',
            (stock_id, account_id),
        )
        h = cursor.fetchone()
        holding_id = h['id'] if h else None

        cursor.execute(
            """
            INSERT INTO trade_records (holding_id, account_id, stock_id, trade_type, price, quantity, amount, commission, commission_estimated, trade_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (holding_id, account_id, stock_id, trade_type, price, quantity, amount, commission, commission_estimated, trade_date, notes),
        )

        trade_id = cursor.lastrowid

        # 触发持仓重算（仅本账户）
        recalculated = _recalculate_holding(cursor, stock_id, account_id)

        conn.commit()
        conn.close()
        return jsonify(
            {'success': True, 'trade_id': trade_id, 'recalculated_position': recalculated,
             'commission': commission, 'commission_estimated': bool(commission_estimated)}
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
        # 021S：账户归属变更（旧流水 account_id 为 NULL 时归入默认账户）
        old_account_id = (
            trade['account_id']
            if trade['account_id'] is not None
            else _get_default_account_id(cursor)
        )
        new_account_id = data.get('account_id', old_account_id)
        if new_account_id is not None and int(new_account_id) != int(old_account_id):
            if not _account_exists(cursor, int(new_account_id)):
                conn.rollback()
                conn.close()
                return jsonify({'success': False, 'message': '目标账户不存在'}), 404
            new_account_id = int(new_account_id)
        elif new_account_id is not None:
            new_account_id = int(new_account_id)

        # 动态构建 UPDATE 语句（account_id 单独处理，不走通用列循环）
        fields = []
        params = []
        for col in ['trade_type', 'price', 'quantity', 'amount', 'commission', 'trade_date', 'notes', 'stock_id']:
            if col in data:
                if col == 'commission':
                    # 021X：手续费校验（非负数字）
                    try:
                        c = float(data['commission'] or 0)
                    except (TypeError, ValueError):
                        conn.rollback()
                        conn.close()
                        return jsonify({'success': False, 'message': 'commission 必须是数字'}), 400
                    if c < 0:
                        conn.rollback()
                        conn.close()
                        return jsonify({'success': False, 'message': '手续费不能为负数'}), 400
                fields.append(f'{col} = ?')
                params.append(data[col])
                if col == 'commission':
                    # 021BK：手工填写佣金即视为实际值，清除估算标记
                    fields.append('commission_estimated = 0')

        if fields:
            # 校验 trade_type
            if 'trade_type' in data and data['trade_type'] not in (
                'buy', 'sell', 'dividend', 'dividend_tax',
            ):
                conn.rollback()
                conn.close()
                return jsonify(
                    {'success': False, 'message': 'trade_type 必须为 buy/sell/dividend'}
                ), 400

            # 如果股票或账户变了，更新 holding_id 指向（021S：按 账户+股票 定位）
            if new_stock_id != old_stock_id or new_account_id != old_account_id:
                cursor.execute(
                    'SELECT id FROM holdings WHERE stock_id=? AND account_id=?',
                    (new_stock_id, new_account_id),
                )
                new_h = cursor.fetchone()
                new_holding_id = new_h['id'] if new_h else None
                fields.append('holding_id = ?')
                params.append(new_holding_id)
                fields.append('account_id = ?')
                params.append(new_account_id)

            params.append(trade_id)
            cursor.execute(f'UPDATE trade_records SET {", ".join(fields)} WHERE id=?', params)

            # 重算新归属（股票, 账户）持仓
            recalculated = _recalculate_holding(cursor, new_stock_id, new_account_id)

            # 如果跨股票/跨账户编辑，还要重算原归属持仓
            if new_stock_id != old_stock_id or new_account_id != old_account_id:
                recalculated_old = _recalculate_holding(cursor, old_stock_id, old_account_id)
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
            recalculated = _recalculate_holding(cursor, old_stock_id, old_account_id)

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

        cursor.execute('SELECT stock_id, account_id FROM trade_records WHERE id=?', (trade_id,))
        trade = cursor.fetchone()
        if not trade:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': '流水记录不存在'}), 404

        stock_id = trade['stock_id']
        # 021S：旧流水 account_id 为 NULL 时按默认账户重算
        account_id = (
            trade['account_id']
            if trade['account_id'] is not None
            else _get_default_account_id(cursor)
        )
        cursor.execute('DELETE FROM trade_records WHERE id=?', (trade_id,))

        recalculated = _recalculate_holding(cursor, stock_id, account_id)

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'recalculated_position': recalculated})

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'删除流水失败（已回滚）：{e}'}), 500


