"""021BP 决策闭环 项3：今日行动清单聚合 API 蓝图（只读，R9 合规——不写任何表）。

021BT 增：盘中速览两端点（GET /api/dashboard/intraday 读内存快照零网络兜底 /
POST /api/dashboard/intraday/refresh 手动一键刷新带冷却）。巡检写库面仅 price_cache。
"""

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


@bp.route('/api/dashboard/intraday', methods=['GET'])
def api_dashboard_intraday():
    """021BT 盘中速览：持仓股盘中状态一屏（读巡检内存快照，缺失时零网络兜底）。

    响应形态（t3 前端速览卡消费契约）：
    {success, date, updated_at, source('auto'|'manual'|'fallback'),
     session:{in_session, markets}, stocks:[{stock_id, symbol, name, market,
       price, pct_change, as_of, quote_ok, note, stop_line, stop_source,
       distance_pct, ma20, ma20_distance_pct,
       state('below_stop'|'near_stop'|'normal'|'unknown'|'no_data'),
       swing, vol_spike, volume, signal_labels:[{side,label,date}],
       top_action, top_action_source('stored'|'live')}], counts,
      thresholds:{near_stop_pct, swing_pct},
      patrol:{enabled, interval_min, consecutive_failures, paused},
      today_trades:{buy_count, buy_amount, sell_count, sell_amount, total_count},
      degraded, degrade_note, disclaimer:'盘中口径，以收盘确认为准'}
    ma20/ma20_distance_pct 为收盘口径 MA20 参照（不足 20 根为 null）；signal_labels
    为读取时零网络离线复算的"最新K线日"信号标记（买卖两侧，失败留空数组）；
    thresholds 透出 config 阈值（F2 单一来源：前端高亮消费 state 字段，不自行算 1%）。
    021BV 行增量：total_qty/avg_cost/market_value/unrealized_pnl/unrealized_pnl_pct
    （holdings 账户无关聚合，与持仓列表 unrealized_pnl 公式同源；价/量/成本缺失
    为 null）；top_action/top_action_source 为操盘手矩阵首行动作文（stored 日报
    预计算优先，触线/逼近行 live 只读兜底；收盘口径，与行内盘中 state 并列展示）；
    today_trades 为当日已录买卖流水概览（trade_records 只读聚合，金额不含费；
    读取失败为 null）。
    提醒语义（F1）：仅 source∈(auto,manual) 的快照产行动清单盘中项，fallback 兜底
    快照仅供本端点展示。
    只读：零写库；无快照兜底亦零网络（price_cache 显示价，禁止归零）。
    """
    try:
        from modules.intraday_patrol import get_snapshot_for_dashboard

        return jsonify(get_snapshot_for_dashboard())
    except Exception as e:  # noqa: BLE001
        logger.error(f'[盘中速览] 读取失败: {e}', exc_info=True)
        return jsonify({
            'success': False, 'message': str(e), 'stocks': [],
            'disclaimer': '盘中口径，以收盘确认为准',
        }), 500


@bp.route('/api/dashboard/intraday/refresh', methods=['POST'])
def api_dashboard_intraday_refresh():
    """021BT 盘中速览一键刷新：立即跑一轮巡检（60s 冷却；非时段/停用/暂停返回明确 reason）。"""
    try:
        from modules.intraday_patrol import request_manual_refresh

        result = request_manual_refresh()
        return jsonify({'success': True, **result})
    except Exception as e:  # noqa: BLE001
        logger.error(f'[盘中速览] 手动刷新失败: {e}', exc_info=True)
        return jsonify({'success': False, 'message': str(e)}), 500
