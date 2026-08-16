"""市场行情 API 蓝图：大盘指数复用 index_ratings，本蓝图提供行业资金流向。"""

from flask import Blueprint, jsonify, request

bp = Blueprint('market', __name__)


@bp.route('/api/market/industry-fund-flow', methods=['GET'])
def api_market_industry_fund_flow():
    """获取行业资金流向快照（默认最新交易日；?date=YYYY-MM-DD 查看历史）。

    020R-53：响应含 dates（可用交易日列表，供时间维度选择），
    items 每行含 main_net_5d（截至该日的前 5 个交易日主力净流入累计）。
    """
    try:
        from modules.market_overview import (
            get_industry_fund_flow_dates,
            get_industry_fund_flow_for_date,
            get_industry_flow_summary,
        )

        dates = get_industry_fund_flow_dates()
        if not dates:
            return jsonify(
                {'success': True, 'trade_date': None, 'updated_at': None, 'items': [], 'dates': [], 'count': 0}
            )
        date_arg = (request.args.get('date') or '').strip()
        trade_date = date_arg if date_arg in dates else dates[0]
        items, updated_at = get_industry_fund_flow_for_date(trade_date)
        # 020R-54：市场资金温度计（全行业合计 + 流入/流出家数）
        summary = get_industry_flow_summary(trade_date)
        return jsonify(
            {
                'success': True,
                'trade_date': trade_date,
                'updated_at': updated_at,
                'items': items,
                'dates': dates,
                'count': len(items),
                'summary': summary,
            }
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({'success': False, 'error': f'{e!s}', 'items': [], 'count': 0}), 500


@bp.route('/api/market/industry-fund-flow/refresh', methods=['POST'])
def api_market_industry_fund_flow_refresh():
    """触发东财行业资金流实时抓取并落库；冷却期内直接回放上次快照。"""
    from modules.market_overview import (
        get_industry_fund_flow_dates,
        get_industry_fund_flow_for_date,
        refresh_in_cooldown,
        refresh_industry_fund_flow,
    )

    # 020R-34：刷新失败后 10 分钟冷却——不再硬闯东财，回放上次快照并提示
    cooldown_left = refresh_in_cooldown()
    if cooldown_left is not None:
        dates = get_industry_fund_flow_dates()
        if dates:
            items, updated_at = get_industry_fund_flow_for_date(dates[0])
            trade_date = dates[0]
        else:
            items, updated_at, trade_date = [], None, None
        minutes = max(1, cooldown_left // 60 + 1)
        return jsonify(
            {
                'success': True,
                'trade_date': trade_date,
                'updated_at': updated_at,
                'items': items,
                'dates': dates,
                'count': len(items),
                'cooldown': True,
                'note': f'东财接口限流冷却中，约 {minutes} 分钟后可重试，当前显示上次快照',
            }
        )

    try:
        items, trade_date, updated_at = refresh_industry_fund_flow()
        dates = get_industry_fund_flow_dates()
        # 刷新落库后重新读取（附带 5 日累计列）
        items, updated_at = get_industry_fund_flow_for_date(trade_date)
        return jsonify(
            {
                'success': True,
                'trade_date': trade_date,
                'updated_at': updated_at,
                'items': items,
                'dates': dates,
                'count': len(items),
                'cooldown': False,
                'note': None,
            }
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({'success': False, 'error': f'{e!s}'}), 500
