"""账户 CRUD 路由：/api/accounts GET/POST/PUT/DELETE（原 portfolio.py 区段逐字搬运）。"""

from flask import jsonify, request

from blueprints.portfolio import bp
from blueprints.portfolio._scope import _account_exists, _get_default_account_id
from database.db_manager import get_connection


@bp.route('/api/accounts', methods=['GET'])
def api_list_accounts():
    """交易账户列表（含持仓/流水统计，供前端切换器与管理弹窗使用）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT a.id, a.name, a.broker, a.notes, a.is_default,
               a.display_order, a.created_at,
               (SELECT COUNT(*) FROM holdings h
                 WHERE h.account_id = a.id AND h.quantity > 0) AS holding_count,
               (SELECT COUNT(*) FROM trade_records tr
                 WHERE tr.account_id = a.id) AS trade_count
        FROM accounts a
        ORDER BY a.is_default DESC, a.display_order ASC, a.id ASC
    """
    )
    accounts = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'accounts': accounts, 'count': len(accounts)})


@bp.route('/api/accounts', methods=['POST'])
def api_create_account():
    """新建交易账户。Body: {name(必填), broker(可选), notes(可选)}"""
    import sqlite3 as _sqlite3

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': '账户名称不能为空'}), 400
    if len(name) > 50:
        return jsonify({'success': False, 'message': '账户名称不能超过 50 字符'}), 400

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO accounts (name, broker, notes) VALUES (?, ?, ?)',
            (name, (data.get('broker') or '').strip(), data.get('notes') or ''),
        )
        account_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'account_id': account_id, 'message': f'账户「{name}」已创建'})
    except _sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'message': f'账户名称「{name}」已存在'}), 409


@bp.route('/api/accounts/<int:account_id>', methods=['PUT'])
def api_update_account(account_id):
    """编辑交易账户。Body 可含: name / broker / notes / display_order"""
    import sqlite3 as _sqlite3

    data = request.get_json(silent=True) or {}
    conn = get_connection()
    cursor = conn.cursor()
    if not _account_exists(cursor, account_id):
        conn.close()
        return jsonify({'success': False, 'message': '账户不存在'}), 404

    fields, params = [], []
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            conn.close()
            return jsonify({'success': False, 'message': '账户名称不能为空'}), 400
        if len(name) > 50:
            conn.close()
            return jsonify({'success': False, 'message': '账户名称不能超过 50 字符'}), 400
        fields.append('name = ?')
        params.append(name)
    for col in ('broker', 'notes', 'display_order'):
        if col in data:
            fields.append(f'{col} = ?')
            params.append(data[col])

    if fields:
        try:
            cursor.execute(
                f'UPDATE accounts SET {", ".join(fields)} WHERE id = ?',
                (*params, account_id),
            )
            conn.commit()
        except _sqlite3.IntegrityError:
            conn.close()
            return jsonify({'success': False, 'message': '账户名称已被其他账户使用'}), 409
    conn.close()
    return jsonify({'success': True, 'message': '账户已更新'})


@bp.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def api_delete_account(account_id):
    """删除交易账户（风控约束）：
    1. 默认账户禁止删除（存量数据归属锚点）
    2. 最后一个账户禁止删除
    3. 账户下仍有持仓时需 force_confirm=true 二次确认；
       删除时持仓移除、流水保留并归入默认账户（审计可追溯）。
    """
    data = request.get_json(silent=True) or {}
    force_confirm = data.get('force_confirm', False)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM accounts WHERE id = ?', (account_id,))
    account = cursor.fetchone()
    if not account:
        conn.close()
        return jsonify({'success': False, 'message': '账户不存在'}), 404
    if account['is_default']:
        conn.close()
        return jsonify({'success': False, 'message': '默认账户不可删除'}), 403

    total = cursor.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
    if total <= 1:
        conn.close()
        return jsonify({'success': False, 'message': '至少保留一个账户，不可删除'}), 400

    holding_cnt = cursor.execute(
        'SELECT COUNT(*) FROM holdings WHERE account_id = ?', (account_id,)
    ).fetchone()[0]
    active_cnt = cursor.execute(
        'SELECT COUNT(*) FROM holdings WHERE account_id = ? AND quantity > 0', (account_id,)
    ).fetchone()[0]
    trade_cnt = cursor.execute(
        'SELECT COUNT(*) FROM trade_records WHERE account_id = ?', (account_id,)
    ).fetchone()[0]

    if holding_cnt > 0 and not force_confirm:
        conn.close()
        return jsonify(
            {
                'success': False,
                'message': (
                    f'账户「{account["name"]}」下有 {holding_cnt} 条持仓记录'
                    f'（{active_cnt} 条在仓），删除后这些持仓将一并移除、'
                    f'{trade_cnt} 条流水保留并归入默认账户。确认请传 force_confirm=true'
                ),
                'holding_count': holding_cnt,
                'active_count': active_cnt,
                'trade_count': trade_cnt,
                'need_force_confirm': True,
            }
        ), 409

    default_id = _get_default_account_id(cursor)
    try:
        conn.execute('BEGIN IMMEDIATE')
        # 流水断链 + 归入默认账户（保留备查）
        cursor.execute(
            'UPDATE trade_records SET holding_id = NULL '
            'WHERE holding_id IN (SELECT id FROM holdings WHERE account_id = ?)',
            (account_id,),
        )
        cursor.execute(
            'UPDATE trade_records SET account_id = ? WHERE account_id = ?',
            (default_id, account_id),
        )
        cursor.execute('DELETE FROM holdings WHERE account_id = ?', (account_id,))
        cursor.execute('DELETE FROM accounts WHERE id = ?', (account_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'删除账户失败（已回滚）：{e}'}), 500
    conn.close()
    return jsonify(
        {'success': True, 'message': f'账户「{account["name"]}」已删除，{trade_cnt} 条流水归入默认账户'}
    )

