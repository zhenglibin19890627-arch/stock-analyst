"""看板评分路由：/api/portfolio/watchlist-scores + 价格区间/操盘手信号解析
（原 portfolio.py 区段逐字搬运；_parse_pa_zone/_derive_trader_signal 为离线纯函数，
测试经 blueprints.portfolio facade 导入）。"""

import json
import logging

from flask import jsonify, request

from blueprints._utils import _derive_obos_signal, _latest_report_join_sql
from blueprints.portfolio import bp
from database.db_manager import get_connection

# ============================================================
# P2: 自选股批量评分看板接口（只读聚合，零引擎侵入）
# ============================================================


def _parse_pa_zone(pa_json):
    """021BM：从最新报告已存 price_advice JSON 提取看板建议卡所需区间摘要。

    只读展示层（零重算）：区间标签/上下沿 + 买入侧前两档网格
    （无持仓即"第一/第二买入位"，有持仓即"补仓一档/二档"）。
    无数据或解析失败返回 None（前端退化为不显示区间，不影响主卡片）。
    """
    if not pa_json:
        return None
    try:
        pa = json.loads(pa_json) if isinstance(pa_json, str) else pa_json
    except (TypeError, ValueError):
        return None
    if not isinstance(pa, dict):
        return None
    low, high = pa.get('buy_range_low'), pa.get('buy_range_high')
    if low is None or high is None:
        return None
    levels = []
    for g in pa.get('grid') or []:
        if isinstance(g, dict) and g.get('type') == 'buy' and g.get('price') is not None:
            levels.append(
                {'price': g.get('price'), 'pct': g.get('pct'), 'label': g.get('label') or ''}
            )
        if len(levels) >= 2:
            break
    return {
        'label': pa.get('zone_label') or '买入区间',
        'low': round(low, 2),
        'high': round(high, 2),
        'stop_loss': pa.get('stop_loss'),
        'levels': levels,
    }


def _derive_trader_signal(kf_json):
    """2026-09-18：从最新报告 key_factors.trader 摘要提取看板分歧标记（零重算）。

    日报生成时由 trader_advisor 预计算（阶段名 + 与评级的分歧）；
    无摘要/解析失败返回 None（前端不显示标记，不影响主卡片）。
    """
    if not kf_json:
        return None
    try:
        kf = json.loads(kf_json) if isinstance(kf_json, str) else kf_json
        t = (kf or {}).get('trader') or {}
        if not t.get('stage_name'):
            return None
        return {
            'stage_name': t.get('stage_name'),
            'has_disagreement': bool(t.get('has_disagreement')),
            'disagreement_text': t.get('disagreement_text'),
        }
    except (TypeError, ValueError):
        return None


@bp.route('/api/portfolio/watchlist-scores')
def api_portfolio_watchlist_scores():
    """自选股批量评分看板数据（单次四表 JOIN，零引擎侵入）。

    数据源：stocks + holdings + price_cache + daily_reports
    返回：全部自选股的最新评分、评级、引擎版本、持仓、市值、行业分类。
    缓存：ETag + If-None-Match → 304
    """
    import decimal
    import hashlib
    from datetime import datetime, timedelta, timezone

    _CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')
    conn = get_connection()
    cursor = conn.cursor()

    # 019R: 每股最新一份有效报告（ROW_NUMBER 派生表 LEFT JOIN），不再依赖
    # 全局单一 MAX(report_date) JOIN；无报告股票由 LEFT JOIN 兜底（评分 NULL 排末尾）。
    # 019D 口径（daily 优先 + status='ok'）逐股化后内置于 _latest_report_join_sql()。
    cursor.execute(
        """
        SELECT s.id, s.symbol, s.name, s.market, s.status, s.industry,
               h.cost_price, h.quantity, h.realized_pnl,
               pc.latest_price, pc.pct_change as price_pct_change,
               lr.engine_version, lr.total_score, lr.rating,
               lr.rating_label, lr.score_change, lr.prev_score,
               lr.key_factors, lr.data_warnings, lr.status as report_status, lr.generated_at,
               lr.report_date, lr.price_advice
        FROM stocks s
        LEFT JOIN holdings h ON h.id = (
            -- 021S 多账户：同一股票可能有多条分账户持仓，
            -- 看板每股仅一行，取持仓数量最大的一条展示
            SELECT h2.id FROM holdings h2
            WHERE h2.stock_id = s.id
            ORDER BY h2.quantity DESC LIMIT 1
        )
        LEFT JOIN price_cache pc ON s.id = pc.stock_id
        LEFT JOIN """ + _latest_report_join_sql() + """
        ON lr.stock_id = s.id
        WHERE s.status != 'delisted'
        ORDER BY
            CASE WHEN lr.total_score IS NULL THEN 1 ELSE 0 END,
            lr.total_score DESC
    """
    )

    rows = [dict(row) for row in cursor.fetchall()]

    # 顶层日期：入选报告（每股最新有效报告）的最新/最早日期
    report_dates = sorted({r['report_date'] for r in rows if r.get('report_date')})
    latest_report_date = report_dates[-1] if report_dates else None
    report_date_min = report_dates[0] if report_dates else None

    # 每股最新报告集合中的 MAX(generated_at)（稳定值，用于ETag；口径随多日期同步）
    report_generated_at = None
    if latest_report_date:
        cursor.execute(
            'SELECT MAX(lr.generated_at) as gen_at FROM ' + _latest_report_join_sql()
        )
        gen_row = cursor.fetchone()
        report_generated_at = gen_row['gen_at'] if gen_row else None

    conn.close()

    # 020R-54：行业资金背景——最新交易日全板块一次计算，逐股按行业名匹配（港股/未匹配为 None）
    industry_bg_map = {}
    match_board_name = None
    try:
        from modules.market_overview import get_industry_flow_bg_map
        from modules.market_overview import match_board_name as _mbn

        industry_bg_map = get_industry_flow_bg_map()
        match_board_name = _mbn
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'[020R-54] 行业资金背景加载失败: {e}')

    stocks = []
    for r in rows:
        qty = r.get('quantity') or 0
        price = r.get('latest_price')
        price = round(price, 2) if price is not None else None

        # 精确市值
        market_value = None
        if price is not None and price > 0 and qty > 0:
            raw_mv = qty * price
            market_value = float(
                decimal.Decimal(str(raw_mv)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )

        # 浮动盈亏
        unrealized = None
        cost = r.get('cost_price') or 0
        if price is not None and qty > 0:
            raw_pnl = (price - cost) * qty
            unrealized = float(
                decimal.Decimal(str(raw_pnl)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )

        # 行业分类（从 stocks.industry 读取，INDUSTRY-DYNAMIC）
        industry = r.get('industry') or '未分类'

        # 020R-54：行业资金背景（展示层关联，不影响评分）
        industry_flow_bg = None
        if r.get('market') != 'hk_stock' and industry != '未分类' and industry_bg_map and match_board_name:
            try:
                board = match_board_name(industry, list(industry_bg_map.keys()))
                industry_flow_bg = industry_bg_map.get(board) if board else None
            except Exception:  # noqa: BLE001
                industry_flow_bg = None

        stocks.append(
            {
                'id': r['id'],
                'symbol': r['symbol'],
                'name': r['name'],
                'market': r['market'],
                'industry': industry,
                'industry_flow_bg': industry_flow_bg,
                'cost_price': round(cost, 2) if cost else None,
                'quantity': qty,
                'latest_price': price,
                'price_pct_change': r.get('price_pct_change'),
                'market_value': market_value,
                'unrealized_pnl': unrealized,
                'engine_version': r.get('engine_version'),
                'total_score': round(r['total_score'], 1)
                if r.get('total_score') is not None
                else None,
                'rating': r.get('rating'),
                'rating_label': r.get('rating_label'),
                'score_change': round(r['score_change'], 1)
                if r.get('score_change') is not None
                else None,
                'has_report': r.get('report_status') == 'ok',
                'generated_at': r.get('generated_at'),
                'report_date': r.get('report_date'),
                'data_warnings': r.get('data_warnings'),
                # DEV-TASKS-20260727-003：超买超卖信号（从 key_factors 派生，不暴露原始因子）
                'obos_signal': _derive_obos_signal(r.get('key_factors')),
                # 2026-09-18：操盘手阶段×评级分歧标记（日报预计算，零重算）
                'trader_signal': _derive_trader_signal(r.get('key_factors')),
                # 021BM：价格建议区间（最新报告已存 JSON，零重算；建议卡买入侧展示）
                'pa_zone': _parse_pa_zone(r.get('price_advice')),
            }
        )

    result = {
        'success': True,
        'report_date': latest_report_date,
        'report_date_min': report_date_min,
        'generated_at': report_generated_at or datetime.now(_CN_TZ).isoformat(),
        'stocks': stocks,
        'total': len(stocks),
    }

    # ETag 缓存（排除 generated_at 避免时间戳波动）
    etag_payload = {k: v for k, v in result.items() if k != 'generated_at'}
    etag = hashlib.md5(json.dumps(etag_payload, sort_keys=True, default=str).encode()).hexdigest()
    if request.headers.get('If-None-Match') == etag:
        return '', 304
    resp = jsonify(result)
    resp.headers['ETag'] = etag
    return resp


