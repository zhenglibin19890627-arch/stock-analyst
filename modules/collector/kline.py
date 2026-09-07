"""K线采集：腾讯主源 + mootdx 兜底 + 当日 bar 刷新 + 周期聚合。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
from datetime import datetime

import pandas as pd

from config import KLINE_DAYS
from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.http_client import _http_get, retry
from modules.collector.mootdx import (
    _ensure_kline_source_column,
    _fetch_kline_mootdx,
    _mootdx_symbol,
)
from modules.collector.symbols_status import _get_tencent_prefix, get_stock_id, save_data_status

# ============================================================
# K线数据采集（A股 + 港股统一使用腾讯接口）
# ============================================================


@retry
def _fetch_kline_tencent(symbol, market):
    """
    从腾讯财经接口获取日K线数据（前复权）。
    腾讯接口返回格式：[日期, 开盘, 收盘, 最高, 最低, 成交量]
    """
    prefix, normalized_code = _get_tencent_prefix(symbol, market)
    tencent_code = f'{prefix}{normalized_code}'

    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    params = {'param': f'{tencent_code},day,,,{KLINE_DAYS},qfq'}

    resp = _http_get(url, params=params)
    data = resp.json()

    # 解析数据
    stock_data = data.get('data', {}).get(tencent_code, {})
    kline_list = stock_data.get('qfqday') or stock_data.get('day') or []

    if not kline_list:
        return pd.DataFrame()

    # 转为 DataFrame
    rows = []
    for item in kline_list:
        # A股格式: [date, open, close, high, low, volume]
        # 港股格式: [date, open, close, high, low, volume, {extra}]
        if len(item) >= 6:
            rows.append(
                {
                    '日期': item[0],
                    '开盘': float(item[1]),
                    '收盘': float(item[2]),
                    '最高': float(item[3]),
                    '最低': float(item[4]),
                    '成交量': float(item[5]),
                }
            )

    df = pd.DataFrame(rows)
    # 计算涨跌幅
    if not df.empty:
        df['涨跌幅'] = df['收盘'].pct_change() * 100
        df['涨跌幅'] = df['涨跌幅'].fillna(0)

    return df


def _is_intraday_session(market='a_stock'):
    """020R-59：是否处于盘中交易时段（周一~周五，不含节假日感知）。

    A股：9:30-11:30 / 13:00-15:00；港股：9:30-12:00 / 13:00-16:00。
    节假日无法感知：节假日命中时段时刷新会拿到上一交易日数据（无今日行→安全跳过）。
    """
    now = datetime.now(_CN_TZ)
    if now.weekday() >= 5:
        return False
    hm = now.hour * 60 + now.minute
    if market == 'hk_stock':
        return (9 * 60 + 30 <= hm <= 12 * 60) or (13 * 60 <= hm <= 16 * 60)
    return (9 * 60 + 30 <= hm <= 11 * 60 + 30) or (13 * 60 <= hm <= 15 * 60)


def _refresh_kline_today_bar(symbol, market, stock_id, today_str):
    """020R-60：用腾讯实时行情一个请求刷新今日bar（不再下载历史K线）。

    字段（A股/港股同构，2026-08-17 实测）：[3]现价 [4]昨收 [5]今开
    [6]成交量（A股=手/港股=股，与 fqkline 同单位）[33]最高 [34]最低。
    返回 True=已刷新今日bar；False=无有效行情（未开盘/休市/接口异常）。
    """
    prefix, code = _get_tencent_prefix(symbol, market)
    try:
        resp = _http_get(f'https://qt.gtimg.cn/q={prefix}{code}')
        resp.encoding = 'gbk'
        parts = resp.text.split('~')
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[{symbol}] 腾讯行情获取失败(保持旧bar): {e}')
        return False

    def _f(i):
        try:
            s = parts[i].strip().strip('"').strip(';')
            return float(s) if s else None
        except (ValueError, IndexError):
            return None

    if len(parts) < 35:
        return False
    now_price = _f(3)
    prev_close = _f(4)
    open_p = _f(5)
    volume = _f(6)
    high_p = _f(33)
    low_p = _f(34)
    if not now_price or not open_p or not high_p or not low_p:
        return False

    pct = None
    if prev_close and prev_close > 0:
        pct = round((now_price - prev_close) / prev_close * 100, 2)

    conn = get_connection()
    try:
        # 保留既有行的 amount/turnover（行情接口的成交额单位与库内口径未统一，不覆盖）
        existing = conn.execute(
            'SELECT amount, turnover FROM raw_kline WHERE stock_id=? AND trade_date=?',
            (stock_id, today_str),
        ).fetchone()
        conn.execute(
            'INSERT OR REPLACE INTO raw_kline '
            '(stock_id, trade_date, open, close, high, low, volume, amount, turnover, pct_change, data_source) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)',
            (
                stock_id,
                today_str,
                open_p,
                now_price,
                high_p,
                low_p,
                volume,
                existing['amount'] if existing else None,
                existing['turnover'] if existing else None,
                pct,
            ),
        )
        conn.commit()
    finally:
        conn.close()
    logger.info(f'[{symbol}] 盘中刷新今日K线(腾讯行情) {today_str} close={now_price}')
    return True


def fetch_kline(symbol, market, force_full=False):
    """
    采集K线数据并存入数据库。A股和港股统一使用此函数。
    011增量优化：同日跳过（last_date >= 今日 → 跳过），全量覆盖确保复权因子一致。
    返回: (状态字符串, 消息)
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 011增量：同日跳过检查
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                'SELECT MAX(trade_date) as last_date FROM raw_kline WHERE stock_id = ?', (stock_id,)
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['last_date']:
                last_date = str(row['last_date'])[:10]
                today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                if last_date >= today_str:
                    # 020R-59：盘中时段刷新当日未收盘 bar（不重拉历史），
                    # 使一键分析/盘中快报在交易时段内每次都能拿到最新盘中价
                    if _is_intraday_session(market):
                        try:
                            if _refresh_kline_today_bar(symbol, market, stock_id, today_str):
                                save_data_status(
                                    stock_id, 'kline', 'success', f'盘中刷新今日K线({today_str})'
                                )
                                return 'success', f'盘中刷新今日K线({today_str})'
                        except Exception as e:  # noqa: BLE001 —— 刷新失败保持旧bar，走原跳过
                            logger.warning(f'[{symbol}] 盘中刷新今日K线失败(保持旧bar): {e}')
                    skip_msg = f'同日跳过(K线已有{last_date}数据)'
                    save_data_status(stock_id, 'kline', 'success', skip_msg)
                    logger.info(f'[{symbol}] {skip_msg}')
                    return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[{symbol}] K线增量检查异常(降级为全量): {e}')

    # 019Y T1：K线降级链路 —— 腾讯野接口（主源）→ mootdx（备用源，仅A股）→ 标记失败
    # 主源失败时自动降级，数据来源在日志与数据库（raw_kline.data_source）中标注 mootdx
    kline_source = None  # None=腾讯主源；'mootdx'=降级备用源
    try:
        df = _fetch_kline_tencent(symbol, market)
        if df is None or df.empty:
            raise ValueError('腾讯接口返回空数据')
    except Exception as e_tencent:
        logger.warning(f'[{symbol}] 腾讯K线获取失败（尝试mootdx降级）: {e_tencent}')
        df = None
        mootdx_code = _mootdx_symbol(symbol, market)
        if mootdx_code:
            try:
                df = _fetch_kline_mootdx(mootdx_code)
                kline_source = 'mootdx'
                logger.info(f'[{symbol}] mootdx K线降级成功（数据来源标注 mootdx）')
            except Exception as e_mootdx:
                logger.error(f'[{symbol}] mootdx K线降级也失败: {e_mootdx}')
        if df is None or df.empty:
            save_data_status(stock_id, 'kline', 'failed', '腾讯接口与mootdx降级均失败')
            return 'failed', '腾讯接口与mootdx降级均失败'

    try:
        _ensure_kline_source_column()
        conn = get_connection()
        cursor = conn.cursor()

        saved_count = 0
        for _, row in df.iterrows():
            trade_date = str(row['日期']).split(' ')[0]
            try:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_kline
                    (stock_id, trade_date, open, close, high, low, volume, amount, turnover, pct_change, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        trade_date,
                        float(row.get('开盘', 0) or 0),
                        float(row.get('收盘', 0) or 0),
                        float(row.get('最高', 0) or 0),
                        float(row.get('最低', 0) or 0),
                        float(row.get('成交量', 0) or 0),
                        float(row.get('成交额', 0) or 0),  # 腾讯接口不提供成交额（留空）；mootdx 有
                        0,  # 换手率（同上）
                        float(row.get('涨跌幅', 0) or 0),
                        kline_source,
                    ),
                )
                saved_count += 1
            except Exception:
                continue

        conn.commit()
        conn.close()

        src_tag = '（数据来源: mootdx 降级）' if kline_source else ''
        save_data_status(stock_id, 'kline', 'success', f'成功获取{saved_count}条K线数据{src_tag}')
        market_name = 'A股' if market == 'a_stock' else '港股'
        logger.info(f'[{market_name} {symbol}] K线数据采集成功，共{saved_count}条{src_tag}')
        return 'success', f'获取{saved_count}条K线数据{src_tag}'

    except Exception as e:
        save_data_status(stock_id, 'kline', 'failed', str(e))
        logger.error(f'[{symbol}] K线数据采集失败: {e}')
        return 'failed', str(e)


# ============================================================
# 020R-48B：周线/月线聚合（由日线 resample，供多周期技术面评分与展示）
# ============================================================


def aggregate_period_klines(stock_id):
    """由 raw_kline 日线聚合周线（ISO 自然周，周一~周五）与月线（自然月），幂等覆盖。

    周/月线 K 线口径：open=首日开盘、high=区间最高、low=区间最低、close=末日收盘、
    volume=区间合计、trade_date=区间最后一个交易日。
    返回 (status, message)。
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT trade_date, open, high, low, close, volume FROM raw_kline '
        'WHERE stock_id = ? ORDER BY trade_date ASC',
        (stock_id,),
    )
    rows = cur.fetchall()
    if len(rows) < 5:
        conn.close()
        return 'skipped', '日线不足5条，跳过周期聚合'

    df = pd.DataFrame([dict(r) for r in rows])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df['iso_year'] = df['trade_date'].dt.isocalendar()['year']
    df['iso_week'] = df['trade_date'].dt.isocalendar()['week']
    df['ym'] = df['trade_date'].dt.to_period('M')

    weekly = (
        df.groupby(['iso_year', 'iso_week'])
        .agg(
            open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
            close=('close', 'last'), volume=('volume', 'sum'),
            trade_date=('trade_date', 'last'),
        )
        .reset_index(drop=True)
    )
    monthly = (
        df.groupby('ym')
        .agg(
            open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
            close=('close', 'last'), volume=('volume', 'sum'),
            trade_date=('trade_date', 'last'),
        )
        .reset_index(drop=True)
    )

    for table, wdf in (('raw_kline_weekly', weekly), ('raw_kline_monthly', monthly)):
        for _, r in wdf.iterrows():
            cur.execute(
                f'INSERT OR REPLACE INTO {table} '
                '(stock_id, trade_date, open, close, high, low, volume) '
                'VALUES (?,?,?,?,?,?,?)',
                (
                    stock_id, str(r['trade_date'])[:10],
                    float(r['open'] or 0), float(r['close'] or 0),
                    float(r['high'] or 0), float(r['low'] or 0),
                    float(r['volume'] or 0),
                ),
            )
    conn.commit()
    conn.close()
    logger.info(f'[020R-48 周期聚合] stock_id={stock_id}: 周线{len(weekly)}根/月线{len(monthly)}根')
    return 'success', f'周线{len(weekly)}根/月线{len(monthly)}根'
