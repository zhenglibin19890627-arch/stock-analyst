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

# 021CC 指标阶段槽：判定输入取最近 80 根日K（严格版指标预热约 35 根已够，
# 多读为剔除 OHLC 坏行留余量），与持仓页同口径
_PHASE_KLINE_LIMIT = 80


def _indicator_phase(stock_id):
    """021CC 指标阶段（趋势罗盘旁并列展示，状态非信号；只读零写库）。

    离线读 raw_kline 最近 80 根 → screener 行格式（trade_date→date 改名、
    OHLC 缺失行剔除）→ modules.market_screener.detect_indicator_phase。
    K 线不足 35 根或无数据返回 None（响应不带 phase 键，不硬标）；
    坏行/缺表等异常同样降级为 None，不阻断罗盘主体。
    """
    from database.db_manager import get_connection
    from modules.market_screener import detect_indicator_phase

    try:
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                'SELECT trade_date, open, close, high, low, volume '
                'FROM raw_kline WHERE stock_id = ? ORDER BY trade_date DESC LIMIT ?',
                (stock_id, _PHASE_KLINE_LIMIT),
            )
            rows = cursor.fetchall()
        finally:
            conn.close()
        kline_rows = []
        for row in reversed(rows):
            d = dict(row)
            if any(d.get(k) is None for k in ('open', 'close', 'high', 'low')):
                continue
            kline_rows.append({
                'date': str(d['trade_date']),
                'open': float(d['open']),
                'close': float(d['close']),
                'high': float(d['high']),
                'low': float(d['low']),
                'volume': float(d['volume'] or 0.0),
            })
        return detect_indicator_phase(kline_rows)
    except Exception:  # 阶段为展示增强，任何坏数据降级为无阶段
        return None


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

    # 021CC 指标阶段槽：罗盘管价格趋势、阶段管指标动量，互补并列；
    # 离线只读复算，数据不足（K线<35根）不带 phase 键，不硬标
    phase = _indicator_phase(stock_id)

    from modules.collector._env import now_cn

    payload = {
        'success': True,
        'stock_id': stock_id,
        'code': data.code,
        'close': data.close,
        **result,
    }
    if phase:
        payload['phase'] = phase
    payload['generated_at'] = now_cn()
    return jsonify(payload)


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
