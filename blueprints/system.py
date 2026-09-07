"""健康检查/数据库统计 API 蓝图。

021AE：引擎灰度管理 API（/api/engine/status、/api/engine/rollback-all）随经典引擎
一并删除——灰度已完成（all_v5 稳定运行超一个月），rollback-all 无处可退且误触发有害。
"""

from flask import Blueprint, jsonify

from config import FLASK_PORT
from database.db_manager import get_connection

bp = Blueprint('system', __name__)

@bp.route('/api/db-stats', methods=['GET'])
def api_db_stats():
    """数据库统计信息"""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}
    tables = [
        'stocks',
        'raw_kline',
        'raw_fundamental',
        'raw_capital_flow',
        'raw_sentiment',
        'data_status',
        'news_sentiment',
        'error_logs',
    ]
    for table in tables:
        cursor.execute(f'SELECT COUNT(*) as count FROM {table}')
        stats[table] = cursor.fetchone()['count']

    conn.close()
    return jsonify({'success': True, 'stats': stats})


@bp.route('/api/health', methods=['GET'])
def api_health():
    """健康检查接口（⚠️ 仅运维：watchdog 每分钟巡检 + start.bat 启动校验依赖此端点，勿删）"""
    return jsonify(
        {
            'success': True,
            'status': 'running',
            'service': 'Stock Analyst',
            'version': 'v5.0',
            'port': FLASK_PORT,
        }
    )


# ============================================================
# M8-BACKTEST-003：评级有效性回测 API
# ============================================================
