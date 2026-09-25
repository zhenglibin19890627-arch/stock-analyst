"""只读卡片域路由（t7 拆包）：趋势罗盘 / 操盘手建议 / 评级位置标注。

原 blueprints/analysis.py 区段逐字搬运（2026-09-07/2026-09-18 系列）：报告页卡片
异步拉取，只读纯函数，不写库、不触发采集、不触碰 generate_advice（B24 红线）。
"""

import logging

from flask import jsonify, request

from blueprints.analysis import bp

# t7 拆包：logger 名保持 blueprints.analysis（与拆分前单文件 logging.getLogger(__name__)
# 的 logger 名逐字一致，日志面零变化；getLogger 注册表同名即同对象）
logger = logging.getLogger('blueprints.analysis')


@bp.route('/api/stocks/<int:stock_id>/trend', methods=['GET'])
def api_stock_trend(stock_id):
    """个股三周期趋势：短期(日线)/中期(周线)/长期(月线) → 上涨/下跌/震荡。

    数据复用 data_adapter 组装的 StockData（含 020R-48 周线/月线指标），
    判定逻辑见 modules/trend_analyzer.py；不写库、不触发采集。
    """
    from modules.data_adapter import load_stockdata_from_db
    from modules.trend_analyzer import analyze_trends

    try:
        data = load_stockdata_from_db(stock_id)
    except Exception as e:  # noqa: BLE001 —— 数据缺失/坏行时按"数据不足"降级
        logger.warning(f'[trend] stock_id={stock_id} 构建失败: {e}')
        data = None

    if data is None:
        return jsonify({'success': False, 'message': '数据不足，请先采集数据'}), 404

    result = analyze_trends(data)
    from modules.collector._env import now_cn

    return jsonify(
        {
            'success': True,
            'stock_id': stock_id,
            'code': data.code,
            'close': data.close,
            **result,
            'generated_at': now_cn(),
        }
    )


@bp.route('/api/stocks/<int:stock_id>/trader-advice', methods=['GET'])
def api_stock_trader_advice(stock_id):
    """操盘手建议：股价阶段（6态）/ 主力行为解读 / 对策与裁决信号 / 评级分歧标注。

    逻辑见 modules/trader_advisor.py；与趋势罗盘同模式——报告页卡片异步拉取，
    只读纯函数，不写库、不触发采集、不触碰 generate_advice（B24 红线）。
    """
    from modules.trader_advisor import generate_trader_advice

    result = generate_trader_advice(stock_id)
    if not result.get('available'):
        return jsonify({'success': False, 'message': result.get('reason', '数据不足')}), 404

    from modules.collector._env import now_cn

    return jsonify(
        {
            'success': True,
            'stock_id': stock_id,
            **result,
            'generated_at': now_cn(),
        }
    )


@bp.route('/api/stocks/<int:stock_id>/position-note', methods=['GET'])
def api_stock_position_note(stock_id):
    """评级位置标注（2026-09-18 回测提升②）：当前位置 × 历史矩阵的条件化提示。

    分化可信（两带各>=10条且差>=15pp）才返回，否则 404（前端不显示，不硬造结论）。
    rating 缺省取最新评级；只读不写库，不触碰 generate_advice（B24 红线）。
    """
    rating = request.args.get('rating')
    if not rating:
        from database.db_manager import get_connection

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT rating FROM ratings_history WHERE stock_id = ? '
            'ORDER BY rating_date DESC LIMIT 1', (stock_id,))
        row = cursor.fetchone()
        conn.close()
        rating = row['rating'] if row else None
    if not rating:
        return jsonify({'success': False, 'message': '暂无评级'}), 404

    from modules.backtest_engine import position_note_for

    note = position_note_for(stock_id, rating)
    if not note:
        return jsonify({'success': False, 'message': '无显著分化或样本不足'}), 404
    return jsonify({'success': True, 'stock_id': stock_id, 'rating': rating, **note})
