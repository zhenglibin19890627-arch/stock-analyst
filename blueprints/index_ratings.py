"""指数数据与评级 API 蓝图(自 app.py 拆分,函数体零改动)。"""

from database.db_manager import get_connection
from flask import Blueprint, jsonify

bp = Blueprint('index_ratings', __name__)

@bp.route('/api/index-ratings', methods=['GET'])
def api_index_ratings():
    """获取所有指数最新评级"""
    try:
        from modules.index_collector import get_latest_ratings

        indices = get_latest_ratings()
        # 获取最新更新时间
        updated_at = None
        if indices:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT MAX(created_at) as t FROM index_ratings')
            row = cursor.fetchone()
            conn.close()
            if row and row['t']:
                updated_at = row['t']
        return jsonify(
            {
                'success': True,
                'indices': indices,
                'updated_at': updated_at,
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'指数评级获取失败: {str(e)}'}), 500


@bp.route('/api/index-ratings/refresh', methods=['POST'])
def api_index_ratings_refresh():
    """触发指数数据采集 + 重新评级"""
    try:
        from modules.index_collector import refresh_all

        results = refresh_all()
        # 返回最新评级
        from modules.index_collector import get_latest_ratings

        indices = get_latest_ratings()
        return jsonify(
            {
                'success': True,
                'indices': indices,
                'message': f'已刷新 {len([r for r in results if "error" not in r])}/{len(results)} 只指数',
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'指数刷新失败: {str(e)}'}), 500


# ============================================================
# P3-B: 智能预警 API（/api/alerts/*）
# 规则 CRUD + 未读查询 + 标记已读，均只读消费 alert_rules/alert_history
# ============================================================
