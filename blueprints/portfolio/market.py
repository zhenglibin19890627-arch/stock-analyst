"""数据预填与价格刷新域路由：/api/analytics/prefill + 腾讯实时批量价格 +
/api/portfolio/refresh-prices（原 portfolio.py 区段逐字搬运）。"""

from flask import jsonify, request

from blueprints.portfolio import bp
from database.db_manager import get_connection


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


def _fetch_realtime_price_batch(symbols_markets):
    """批量获取实时行情价格（腾讯接口）。
    symbols_markets: [(stock_id, symbol, market), ...]
    返回: {stock_id: {'price': float, 'pct_change': float}}
    019Y T1：腾讯主源缺失的 A股 自动降级 mootdx（通达信行情，TCP socket，不经过 requests patch）。
    """
    import logging as _logging019y

    import requests as _requests

    from modules.data_collector import _normalize_hk_symbol

    _log019y = _logging019y.getLogger(__name__)

    result = {}
    if not symbols_markets:
        return result

    # 构造腾讯批量请求代码：sh600000,sz000001,hk00700
    tencent_codes = []
    stock_id_map = {}
    for stock_id, symbol, market in symbols_markets:
        if market == 'hk_stock':
            # 港股：库内 HK3690 形态 → 腾讯 hk03690（剥离 HK 前缀+左补零至5位）
            # 021M 修复：原 symbol.zfill(5) 对带前缀代码（'HK3690' 已6字符）无效，
            # 生成 hkHK3690 错误代码 → 腾讯返回空 → 港股全部降级写入昨收（实测
            # 2026-08-18：cache 价=08-17 收盘 88.0，实时价实为 84.95）
            hk_code = _normalize_hk_symbol(symbol)
            tc = 'hk' + hk_code
        elif market == 'a_stock':
            if symbol.startswith('6'):
                tc = 'sh' + symbol
            else:
                tc = 'sz' + symbol
        else:
            continue
        tencent_codes.append(tc)
        stock_id_map[tc] = stock_id

    if not tencent_codes:
        return result

    # 腾讯批量行情接口（逗号分隔，最多约50只一次）
    batch_size = 40
    for i in range(0, len(tencent_codes), batch_size):
        batch = tencent_codes[i : i + batch_size]
        url = 'https://qt.gtimg.cn/q=' + ','.join(batch)
        try:
            resp = _requests.get(url, timeout=8)
            lines = resp.text.strip().split(';')
            for line in lines:
                line = line.strip()
                if not line or '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) < 5:
                    continue
                # 从原始行中提取代码
                var_match = line.split('=')[0].strip()
                tc_code = var_match.replace('v_', '').strip()
                sid = stock_id_map.get(tc_code)
                if not sid:
                    continue
                # parts[1]=名称, parts[3]=最新价, parts[32]=涨跌幅(%)
                try:
                    price = float(parts[3]) if parts[3] else 0
                    pct = float(parts[32]) if len(parts) > 32 and parts[32] else 0
                    if price > 0:
                        result[sid] = {'price': price, 'pct_change': pct}
                except (ValueError, IndexError):
                    continue
        except Exception:
            continue

    # 019Y T1：腾讯主源缺失的 A股 → mootdx 降级（每只一次 socket 查询，量小）
    missing_a = [
        (stock_id, symbol)
        for stock_id, symbol, market in symbols_markets
        if market == 'a_stock' and stock_id not in result
    ]
    if missing_a:
        try:
            from modules.data_collector import get_realtime_quote_mootdx

            for stock_id, symbol in missing_a:
                q = get_realtime_quote_mootdx(symbol)
                if q and q.get('price') and q['price'] > 0:
                    result[stock_id] = {'price': q['price'], 'pct_change': q.get('pct_change')}
                    _log019y.info(
                        f'[019Y] {symbol} 实时价格走 mootdx 降级: price={q["price"]}'
                    )
        except Exception as e:
            _log019y.warning(f'[019Y] mootdx 实时价格降级失败: {e}')

    return result


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
