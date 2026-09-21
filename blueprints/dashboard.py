"""021BP 决策闭环 项3：今日行动清单聚合 API 蓝图（只读，R9 合规——不写任何表）。"""

import logging

from flask import Blueprint, jsonify

logger = logging.getLogger(__name__)

bp = Blueprint('dashboard', __name__)


@bp.route('/api/dashboard/action-list', methods=['GET'])
def api_dashboard_action_list():
    """今日行动清单：聚合四路现成数据（30 秒看完"今天该关注什么"）。

    路1 daily_reports 最新行——评级升降 / 超时缺报股显式列出（补可见性缺口）/
        操盘手摘要（key_factors.trader 预计算，零重算）；
    路2 t2 自选股信号离线复算——今日命中 + 共振星级（零网络）；
    路3 alert_history 当日触发（未读）。

    排序：评级升降 > 买点共振≥4星 > 预警未读 > 超时缺报股。
    只读聚合：不写 daily_reports/评分/评级/预警表（R9/V8 合规），零触碰
    generate_advice（B24）。
    """
    try:
        from modules.action_list import get_action_list

        result = get_action_list()
        return jsonify({'success': True, **result})
    except Exception as e:  # noqa: BLE001
        logger.error(f'[行动清单] 聚合失败: {e}', exc_info=True)
        return jsonify({'success': False, 'message': str(e), 'items': []}), 500
