"""智能预警规则与扫描 API 蓝图(自 app.py 拆分,函数体零改动)。"""

from database.db_manager import get_connection
from flask import Blueprint, jsonify, request

bp = Blueprint('alerts', __name__)

_VALID_ALERT_TYPES = ('rating_change', 'score_below', 'capital_outflow')


@bp.route('/api/alerts/rules', methods=['GET'])
def api_get_alert_rules():
    """查询全部预警规则列表（含全局规则与个股规则）"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ar.id, ar.rule_type, ar.stock_id, ar.threshold, ar.enabled,
                   ar.created_at, ar.updated_at,
                   s.symbol, s.name, s.market
            FROM alert_rules ar
            LEFT JOIN stocks s ON ar.stock_id = s.id
            ORDER BY ar.rule_type, ar.stock_id IS NULL DESC, ar.id
        """)
        rules = []
        for row in cursor.fetchall():
            r = dict(row)
            r['scope'] = '全局' if r['stock_id'] is None else '个股'
            rules.append(r)
        conn.close()
        return jsonify({'success': True, 'rules': rules, 'total': len(rules)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/alerts/rules', methods=['POST'])
def api_create_alert_rule():
    """新增预警规则（校验 rule_type 仅3种）。
    Body: {rule_type, stock_id?, threshold?, enabled?}
    """
    try:
        data = request.get_json(silent=True) or {}
        rule_type = data.get('rule_type', '').strip()
        if rule_type not in _VALID_ALERT_TYPES:
            return jsonify(
                {'success': False, 'message': f'rule_type 仅支持 {_VALID_ALERT_TYPES}'}
            ), 400

        stock_id = data.get('stock_id')
        threshold = data.get('threshold')
        enabled = 1 if data.get('enabled', 1) else 0

        # stock_id 存在性校验（非全局规则时）
        if stock_id is not None:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM stocks WHERE id=?', (stock_id,))
            if not cursor.fetchone():
                conn.close()
                return jsonify({'success': False, 'message': f'stock_id={stock_id} 不存在'}), 400
            conn.close()

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO alert_rules (rule_type, stock_id, threshold, enabled)
            VALUES (?, ?, ?, ?)
        """,
            (rule_type, stock_id, threshold, enabled),
        )
        new_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': new_id, 'message': '规则创建成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/alerts/rules/<int:rule_id>', methods=['PUT'])
def api_update_alert_rule(rule_id):
    """修改预警规则（threshold/enabled）。
    Body: {threshold?, enabled?}
    """
    try:
        data = request.get_json(silent=True) or {}
        fields = []
        params = []

        if 'threshold' in data:
            fields.append('threshold=?')
            params.append(data['threshold'])
        if 'enabled' in data:
            fields.append('enabled=?')
            params.append(1 if data['enabled'] else 0)

        if not fields:
            return jsonify(
                {'success': False, 'message': '无可更新字段（支持 threshold/enabled）'}
            ), 400

        fields.append("updated_at=datetime('now', 'localtime')")
        params.append(rule_id)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f'UPDATE alert_rules SET {", ".join(fields)} WHERE id=?', params)
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'success': False, 'message': f'规则 id={rule_id} 不存在'}), 404
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': '规则更新成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/alerts/rules/<int:rule_id>', methods=['DELETE'])
def api_delete_alert_rule(rule_id):
    """删除预警规则（软删除 enabled=0，保留历史关联）。
    Query: force=1 时物理删除（保留关联历史记录的 rule_id）
    """
    try:
        force = request.args.get('force', '0') == '1'
        conn = get_connection()
        cursor = conn.cursor()

        if force:
            cursor.execute('UPDATE alert_rules SET enabled=0 WHERE id=?', (rule_id,))
            affected = cursor.rowcount
        else:
            cursor.execute('UPDATE alert_rules SET enabled=0 WHERE id=?', (rule_id,))
            affected = cursor.rowcount

        if affected == 0:
            conn.close()
            return jsonify({'success': False, 'message': f'规则 id={rule_id} 不存在'}), 404
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': '规则已停用（软删除）'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/alerts/unread', methods=['GET'])
def api_get_unread_alerts():
    """查询未读预警列表（is_read=0，按 triggered_at DESC）。
    Query: limit（默认20）
    """
    try:
        limit = request.args.get('limit', '20')
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = 20
        limit = max(1, min(limit, 200))

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ah.id, ah.rule_id, ah.stock_id, ah.alert_type, ah.trigger_value,
                   ah.message, ah.is_read, ah.triggered_at, ah.trigger_date,
                   s.symbol, s.name, s.market
            FROM alert_history ah
            LEFT JOIN stocks s ON ah.stock_id = s.id
            WHERE ah.is_read = 0
            ORDER BY ah.triggered_at DESC
            LIMIT ?
        """,
            (limit,),
        )
        alerts = [dict(row) for row in cursor.fetchall()]

        # 查询未读总数
        cursor.execute('SELECT COUNT(*) as cnt FROM alert_history WHERE is_read=0')
        unread_count = cursor.fetchone()['cnt']
        conn.close()
        return jsonify({'success': True, 'alerts': alerts, 'unread_count': unread_count})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/alerts/<int:alert_id>/read', methods=['POST'])
def api_mark_alert_read(alert_id):
    """标记单条预警已读"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE alert_history SET is_read=1 WHERE id=?', (alert_id,))
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'success': False, 'message': f'预警 id={alert_id} 不存在'}), 404
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': '已标记已读'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/alerts/read-all', methods=['POST'])
def api_mark_all_alerts_read():
    """全部标记已读"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE alert_history SET is_read=1 WHERE is_read=0')
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return jsonify(
            {'success': True, 'message': f'已标记 {affected} 条预警为已读', 'updated': affected}
        )
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@bp.route('/api/alerts/scan', methods=['POST'])
def api_trigger_alert_scan():
    """手动触发一次预警扫描（调试/补扫用，不影响定时调度）"""
    try:
        from modules.alert_engine import scan_once

        result = scan_once()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ============================================================
# 启动
# ============================================================
