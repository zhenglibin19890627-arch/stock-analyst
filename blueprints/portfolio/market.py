"""数据预填与价格刷新域路由：/api/analytics/prefill + 腾讯实时批量价格 +
/api/portfolio/refresh-prices（原 portfolio.py 区段逐字搬运）。

021BT：`_fetch_realtime_price_batch` 逐字平移至 modules/realtime_quotes.py
（蓝图与盘中巡检调度器同源复用，消除 modules→blueprints 反向依赖）；
本文件保留同名再导出——`blueprints.portfolio.market._fetch_realtime_price_batch`
与 facade `blueprints.portfolio._fetch_realtime_price_batch` 引用面零变化，
refresh-prices 端点行为零变化（含"刷全部非退市股"的既有范围）。
"""

from flask import jsonify, request

from blueprints.portfolio import bp
from database.db_manager import get_connection
from modules.realtime_quotes import _fetch_realtime_price_batch  # noqa: F401  # 021BT 再导出


@bp.route('/api/analytics/prefill', methods=['POST'])
def api_prefill_analytics():
    """预填埋点统计：记录预填字段使用率、修改率、二次确认触发率。
    轻量实现：写入 error_logs 表复用（module='prefill_analytics'）。
    """
    data = request.get_json(silent=True) or {}
    event_type = data.get(
        'event_type', ''
    )  # prefill_shown / field_modified / cost_confirm_triggered
    stock_id = data.get('stock_id')
    detail = data.get('detail', '')

    if not event_type:
        return jsonify({'success': False, 'message': 'event_type 不能为空'}), 400

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO error_logs (stock_id, module, error_type, error_message)
        VALUES (?, 'prefill_analytics', ?, ?)
    """,
        (stock_id, event_type, detail),
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '埋点已记录'})


@bp.route('/api/portfolio/refresh-prices', methods=['POST'])
def api_refresh_prices():
    """刷新股票的最新价格（实时行情采集）。
    数据源：腾讯财经实时行情接口（免费、无需密钥）。
    同步刷新持仓 + 自选股的 price_cache。
    失败时保留旧缓存，禁止价格归零。
    """
    import time as _time

    start_ts = _time.time()
    data_source = 'tencent_realtime'
    fetch_errors = []

    conn = get_connection()
    cursor = conn.cursor()

    # 获取所有需要刷新价格的股票（持仓 + 自选股 合并去重）
    cursor.execute("""
        SELECT DISTINCT s.id as stock_id, s.symbol, s.market
        FROM stocks s
        WHERE s.status != 'delisted'
    """)
    all_stocks = [(row['stock_id'], row['symbol'], row['market']) for row in cursor.fetchall()]

    # 尝试实时采集
    price_data = _fetch_realtime_price_batch(all_stocks)
    realtime_count = len(price_data)

    updated_count = 0
    fallback_count = 0

    for stock_id, symbol, market in all_stocks:
        if stock_id in price_data:
            # 实时价格写入缓存
            p = price_data[stock_id]
            cursor.execute(
                """
                INSERT OR REPLACE INTO price_cache (stock_id, latest_price, pct_change, updated_at)
                VALUES (?, ?, ?, datetime('now', 'localtime'))
            """,
                (stock_id, p['price'], p['pct_change']),
            )
            updated_count += 1
        else:
            # 降级：从最新K线获取收盘价（保留旧缓存，不归零）
            cursor.execute(
                """
                SELECT close, pct_change FROM raw_kline
                WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1
            """,
                (stock_id,),
            )
            kline = cursor.fetchone()
            if kline and kline['close']:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO price_cache (stock_id, latest_price, pct_change, updated_at)
                    VALUES (?, ?, ?, datetime('now', 'localtime'))
                """,
                    (stock_id, kline['close'], kline['pct_change']),
                )
                fallback_count += 1
            else:
                fetch_errors.append(f'{symbol}: 无数据源')

    conn.commit()
    conn.close()

    fetch_duration_ms = int((_time.time() - start_ts) * 1000)

    return jsonify(
        {
            'success': True,
            'message': f'已刷新 {updated_count + fallback_count}/{len(all_stocks)} 只股票的价格',
            'updated_count': updated_count + fallback_count,
            'realtime_count': realtime_count,
            'fallback_count': fallback_count,
            'total': len(all_stocks),
            'data_source': data_source,
            'fetch_duration_ms': fetch_duration_ms,
            'errors': fetch_errors[:10],  # 最多返回10条错误
        }
    )


# ============================================================
# v5.0 四维评分引擎原型 API
# ============================================================
