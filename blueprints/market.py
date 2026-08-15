"""市场行情 API 蓝图：大盘指数复用 index_ratings，本蓝图提供行业资金流向。"""

from flask import Blueprint, jsonify

bp = Blueprint('market', __name__)


@bp.route('/api/market/industry-fund-flow', methods=['GET'])
def api_market_industry_fund_flow():
    """获取行业资金流向最新快照（优先读库，不触发网络）。"""
    try:
        from modules.market_overview import get_latest_industry_fund_flow

        items, trade_date, updated_at = get_latest_industry_fund_flow()
        return jsonify(
            {
                'success': True,
                'trade_date': trade_date,
                'updated_at': updated_at,
                'items': items,
                'count': len(items),
            }
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({'success': False, 'error': f'行业资金流获取失败: {e!s}', 'items': [], 'count': 0}), 500


@bp.route('/api/market/industry-fund-flow/refresh', methods=['POST'])
def api_market_industry_fund_flow_refresh():
    """触发东财行业资金流实时抓取并落库。"""
    try:
        from modules.market_overview import refresh_industry_fund_flow

        items, trade_date, updated_at = refresh_industry_fund_flow()
        return jsonify(
            {
                'success': True,
                'trade_date': trade_date,
                'updated_at': updated_at,
                'items': items,
                'count': len(items),
            }
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({'success': False, 'error': f'行业资金流刷新失败: {e!s}'}), 500
