"""风控修正域路由：流水编辑限制（T+1 锁/金额阈值二次验证，红线 风控载体）+
持仓成本修正 + 调仓建议（原 portfolio.py 区段逐字搬运）。

⚠️ 风控阈值（config.COST_ADJUSTMENT_* / TRADE_*）禁止随意放宽（AGENTS.md §7.2）；
本模块在调用点经包 facade 取配置常量，保持测试 monkeypatch 语义。"""

from flask import jsonify, request

from blueprints.portfolio import bp
from blueprints.portfolio._scope import _get_default_account_id, _parse_account_scope
from database.db_manager import get_connection

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
    # 021S：清算状态按（股票, 账户）维度判断；旧流水无账户归属时按默认账户
    account_id = (
        trade['account_id']
        if trade['account_id'] is not None
        else _get_default_account_id(cursor)
    )

    # 1. 检查持仓是否已清算（仅本账户）
    cursor.execute(
        'SELECT quantity, status FROM holdings WHERE stock_id=? AND account_id=?',
        (stock_id, account_id),
    )
    h = cursor.fetchone()
    if h and (h['quantity'] or 0) <= 0:
        return (
            False,
            '该股票已清算（持仓数量=0），历史流水仅允许查看，禁止'
            + ('编辑' if operation == 'edit' else '删除'),
            403,
        )

    # 2. T+1锁定检查
    # 021BO：配置常量在调用点经包 facade 取值（测试可 monkeypatch 蓝图模块属性）
    from blueprints.portfolio import TRADE_AMOUNT_VERIFY_THRESHOLD, TRADE_T1_LOCK_ENABLED

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
        # 021BO：配置常量在调用点经包 facade 取值（测试可 monkeypatch 蓝图模块属性）
        from blueprints.portfolio import (
            COST_ADJUSTMENT_COOLDOWN_HOURS,
            COST_ADJUSTMENT_DEVIATION_THRESHOLD,
        )

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


@bp.route('/api/portfolio/holdings/<int:stock_id>/trade-suggestion', methods=['GET'])
def api_trade_suggestion(stock_id):
    """获取近3次同股票买入记录，用于预填推荐。
    021S：可选 ?account_id= 按账户过滤（缺省=全部账户）。
    返回 avg_price, avg_quantity, count, latest_trade_date
    """
    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        SELECT price, quantity, trade_date
        FROM trade_records
        WHERE stock_id=? AND trade_type='buy'
    """
    params = [stock_id]
    if account_id is not None:
        sql += ' AND account_id = ?'
        params.append(account_id)
    sql += ' ORDER BY trade_date DESC, created_at DESC LIMIT 3'
    cursor.execute(sql, params)
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


