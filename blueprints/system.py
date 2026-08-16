"""健康检查/引擎切换/数据库统计 API 蓝图(自 app.py 拆分,函数体零改动)。"""

import json
import os

from flask import Blueprint, jsonify, request

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
# P3-A：引擎灰度管理 API
# ============================================================


_ROLLBACK_AUDIT_LOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'rollback_audit.log'
)


@bp.route('/api/engine/status')
def api_engine_status():
    """获取当前灰度状态：mode/whitelist/blacklist/熔断状态/各股票引擎分配
    ⚠️ 仅运维：前端无入口，供灰度切换人工核查（勿删）。
    """
    from modules.engine_switcher import get_grayscale_status

    return jsonify(get_grayscale_status())


@bp.route('/api/engine/rollback-all', methods=['POST'])
def api_engine_rollback_all():
    """一键全量回退：将所有股票切回 legacy 引擎（⚠️ 仅运维：前端无入口，勿删）

    P3-A 强制修正项2：
    - 需要 confirm=true 查询参数，缺失时返回 400
    - 操作写入审计日志（时间、来源IP、previous_mode）
    - 回退后返回 ALERT-SYSTEM 标记
    """
    # 修正项2①：确认机制
    confirm = request.args.get('confirm', '').lower()
    if confirm != 'true':
        return jsonify(
            {'success': False, 'message': '请添加 ?confirm=true 参数以确认一键回退操作'}
        ), 400

    from modules.engine_switcher import rollback_all_to_legacy

    result = rollback_all_to_legacy()

    if result.get('success'):
        # 修正项2②：写入审计日志
        audit_dir = os.path.dirname(_ROLLBACK_AUDIT_LOG)
        if not os.path.exists(audit_dir):
            os.makedirs(audit_dir)

        from datetime import datetime, timedelta, timezone

        cn_tz = timezone(timedelta(hours=8))
        audit_entry = {
            'timestamp': datetime.now(cn_tz).isoformat(),
            'action': 'rollback-all',
            'source_ip': request.remote_addr or 'unknown',
            'previous_mode': result.get('previous_mode'),
            'new_mode': 'all_legacy',
        }
        with open(_ROLLBACK_AUDIT_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(audit_entry, ensure_ascii=False) + '\n')

        # 修正项2③：系统级预警标记
        result['alert'] = 'ALERT-SYSTEM: 全量引擎已回退至 legacy，请运维确认'
        result['audit_logged'] = True

    return jsonify(result)


# ============================================================
# M8-BACKTEST-003：评级有效性回测 API
# ============================================================
