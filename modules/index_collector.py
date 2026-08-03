"""
指数数据采集 + 评级模块 (B8: INDEX-DATA / INDEX-SCORE)

功能：
1. 从 akshare 获取 A股/港股 指数K线数据
2. 计算技术指标（MA/MACD/KDJ/RSI/BOLL/量比）
3. 构造 StockData 契约 → 调用 scoring_engine.analyze() → 生成评级
4. 存入 index_kline / index_ratings 表

红线：不修改 scoring_engine.py / data_collector.py
"""

import json
import logging

import pandas as pd

logger = logging.getLogger(__name__)

# ================================================================
# 指数列表定义（硬编码，用户无需配置）
# ================================================================

INDEX_LIST = [
    {'code': '000001', 'name': '上证指数', 'market': 'A', 'ak_symbol': 'sh000001'},
    {'code': '399001', 'name': '深证成指', 'market': 'A', 'ak_symbol': 'sz399001'},
    {'code': '000300', 'name': '沪深300', 'market': 'A', 'ak_symbol': 'sh000300'},
    {'code': '399006', 'name': '创业板指', 'market': 'A', 'ak_symbol': 'sz399006'},
    {'code': '000688', 'name': '科创50', 'market': 'A', 'ak_symbol': 'sh000688'},
    {'code': 'HSI', 'name': '恒生指数', 'market': 'HK', 'ak_symbol': 'HSI'},
    {'code': 'HSTECH', 'name': '恒生科技指数', 'market': 'HK', 'ak_symbol': 'HSTECH'},
]


# ================================================================
# 一、K线数据获取
# ================================================================


def fetch_index_kline(index_info: dict) -> pd.DataFrame:
    """获取单只指数的K线数据

    Args:
        index_info: INDEX_LIST 中的一项
    Returns:
        DataFrame(date, open, high, low, close, volume)，按日期升序
    """
    import akshare as ak

    symbol = index_info['ak_symbol']
    market = index_info['market']

    try:
        if market == 'A':
            df = ak.stock_zh_index_daily(symbol=symbol)
        else:
            df = ak.stock_hk_index_daily_em(symbol=symbol)

        if df is None or df.empty:
            logger.warning(f'[指数K线] {index_info["name"]}({symbol}) 返回空数据')
            return pd.DataFrame()

        # 统一列名
        df = df.rename(
            columns={
                'date': 'date',
                'open': 'open',
                'high': 'high',
                'low': 'low',
                'close': 'close',
                'volume': 'volume',
            }
        )

        # 确保必要列存在
        for col in ['date', 'open', 'high', 'low', 'close', 'volume']:
            if col not in df.columns:
                logger.warning(f'[指数K线] {index_info["name"]} 缺少列: {col}')
                return pd.DataFrame()

        # 只保留需要的列
        df = df[['date', 'open', 'high', 'low', 'close', 'volume']].copy()

        # date 列统一为字符串 YYYY-MM-DD
        df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')

        # 按日期升序排列
        df = df.sort_values('date').reset_index(drop=True)

        # 只保留最近 300 天（足够计算 MA60 + MACD 等）
        if len(df) > 300:
            df = df.tail(300).reset_index(drop=True)

        logger.info(f'[指数K线] {index_info["name"]}({symbol}) 获取 {len(df)} 条数据')
        return df

    except Exception as e:
        logger.error(f'[指数K线] {index_info["name"]}({symbol}) 获取失败: {e}')
        return pd.DataFrame()


def fetch_all_index_kline() -> dict:
    """批量获取所有指数K线并存入数据库

    Returns:
        {index_code: row_count} 成功写入条数
    """
    from database.db_manager import get_connection

    results = {}
    conn = get_connection()
    cursor = conn.cursor()

    for idx in INDEX_LIST:
        try:
            df = fetch_index_kline(idx)
            if df.empty:
                results[idx['code']] = 0
                continue

            count = 0
            for _, row in df.iterrows():
                try:
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO index_kline
                        (index_code, trade_date, open, high, low, close, volume)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            idx['code'],
                            row['date'],
                            float(row['open']) if pd.notna(row['open']) else None,
                            float(row['high']) if pd.notna(row['high']) else None,
                            float(row['low']) if pd.notna(row['low']) else None,
                            float(row['close']) if pd.notna(row['close']) else None,
                            int(row['volume']) if pd.notna(row['volume']) else None,
                        ),
                    )
                    count += 1
                except Exception as e:
                    logger.debug(f'[指数K线] 写入失败 {idx["code"]} {row["date"]}: {e}')

            conn.commit()
            results[idx['code']] = count
            logger.info(f'[指数K线] {idx["name"]} 写入 {count} 条')

        except Exception as e:
            logger.error(f'[指数K线] {idx["name"]} 处理失败: {e}')
            results[idx['code']] = 0

    conn.close()
    return results


# ================================================================
# 二、技术指标计算
# ================================================================


def compute_technical_indicators(df: pd.DataFrame) -> dict:
    """基于K线 DataFrame 计算技术指标，返回最新一天的指标字典

    计算指标：MA5/MA10/MA20/MA60、MACD(DIF/DEA)、KDJ(K值)、RSI(14)、
             布林带(上/下轨)、量比

    Args:
        df: K线 DataFrame(date, open, high, low, close, volume)，按日期升序
    Returns:
        dict 包含所有技术指标的最新值
    """
    if df.empty or len(df) < 5:
        return {}

    close = df['close'].astype(float)
    high = df['high'].astype(float)
    low = df['low'].astype(float)
    volume = df['volume'].astype(float)

    # ---- MA ----
    ma5 = close.rolling(5).mean()
    ma10 = close.rolling(10).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()

    # ---- MACD (12, 26, 9) ----
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    dif = ema12 - ema26
    dea = dif.ewm(span=9, adjust=False).mean()

    # ---- KDJ (9, 3, 3) ----
    low_9 = low.rolling(9).min()
    high_9 = high.rolling(9).max()
    rsv = (close - low_9) / (high_9 - low_9) * 100
    rsv = rsv.fillna(50)
    k = rsv.ewm(com=2, adjust=False).mean()

    # ---- RSI (14) ----
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.rolling(14).mean()
    avg_loss = loss.rolling(14).mean()
    rs = avg_gain / avg_loss.replace(0, float('nan'))
    rsi_14 = 100 - (100 / (1 + rs))

    # ---- 布林带 (20, 2) ----
    boll_mid = close.rolling(20).mean()
    boll_std = close.rolling(20).std()
    boll_upper = boll_mid + 2 * boll_std
    boll_lower = boll_mid - 2 * boll_std

    # ---- 量比 (当日成交量 / 过去5日平均成交量) ----
    vol_ma5 = volume.rolling(5).mean()
    volume_ratio = volume / vol_ma5.replace(0, float('nan'))

    # 取最新一行
    last = len(df) - 1
    indicators = {
        'ma5': _safe_float(ma5.iloc[last]),
        'ma10': _safe_float(ma10.iloc[last]),
        'ma20': _safe_float(ma20.iloc[last]),
        'ma60': _safe_float(ma60.iloc[last]),
        'macd_dif': _safe_float(dif.iloc[last]),
        'macd_dea': _safe_float(dea.iloc[last]),
        'kdj_k': _safe_float(k.iloc[last]),
        'rsi_14': _safe_float(rsi_14.iloc[last]),
        'boll_upper': _safe_float(boll_upper.iloc[last]),
        'boll_lower': _safe_float(boll_lower.iloc[last]),
        'volume': int(volume.iloc[last]) if pd.notna(volume.iloc[last]) else None,
        'volume_ratio': _safe_float(volume_ratio.iloc[last]),
    }
    return indicators


def _safe_float(val):
    """安全转换为 float，NaN/Inf 返回 None"""
    if val is None or pd.isna(val):
        return None
    f = float(val)
    if f != f or f == float('inf') or f == float('-inf'):
        return None
    return round(f, 4)


# ================================================================
# 三、指数评级（INDEX-SCORE）
# ================================================================


def score_index(index_info: dict) -> dict:
    """对单只指数执行评级

    流程：
    1. 从 index_kline 读取最近 250 天K线
    2. 计算技术指标
    3. 构造 StockData（仅技术面字段，其余 None）
    4. 调用 scoring_engine.analyze()
    5. 存入 index_ratings 表

    Args:
        index_info: INDEX_LIST 中的一项
    Returns:
        评级结果字典
    """
    from database.db_manager import get_connection

    from modules.data_contract import StockData
    from modules.scoring_engine import analyze

    code = index_info['code']
    name = index_info['name']
    market = index_info['market']

    # 1. 从数据库读取K线
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT trade_date, open, high, low, close, volume
        FROM index_kline
        WHERE index_code = ?
        ORDER BY trade_date DESC
        LIMIT 250
    """,
        (code,),
    )
    rows = cursor.fetchall()
    conn.close()

    if not rows or len(rows) < 5:
        raise ValueError(f'{name} K线数据不足（需至少5条，当前{len(rows) if rows else 0}条）')

    # 转为 DataFrame（升序）
    data_list = []
    for r in reversed(rows):
        data_list.append(
            {
                'date': r['trade_date'],
                'open': r['open'],
                'high': r['high'],
                'low': r['low'],
                'close': r['close'],
                'volume': r['volume'] or 0,
            }
        )
    df = pd.DataFrame(data_list)

    # 2. 计算技术指标
    indicators = compute_technical_indicators(df)
    if not indicators:
        raise ValueError(f'{name} 技术指标计算失败')

    # 最新收盘价和日期
    latest_close = float(df['close'].iloc[-1])
    latest_date_str = df['date'].iloc[-1]  # YYYY-MM-DD
    trade_date = latest_date_str.replace('-', '')  # YYYYMMDD

    # 涨跌幅
    pct_change = None
    if len(df) >= 2:
        prev_close = float(df['close'].iloc[-2])
        if prev_close > 0:
            pct_change = round((latest_close - prev_close) / prev_close * 100, 2)

    # 3. 构造 StockData（仅技术面，基本面/消息面/资金面全部 None）
    stock_data = StockData(
        code=f'{code}.IDX',
        market=market,
        trade_date=trade_date,
        close=latest_close,
        ma5=indicators.get('ma5'),
        ma10=indicators.get('ma10'),
        ma20=indicators.get('ma20'),
        ma60=indicators.get('ma60'),
        macd_dif=indicators.get('macd_dif'),
        macd_dea=indicators.get('macd_dea'),
        kdj_k=indicators.get('kdj_k'),
        rsi_14=indicators.get('rsi_14'),
        volume=indicators.get('volume'),
        volume_ratio=indicators.get('volume_ratio'),
        boll_upper=indicators.get('boll_upper'),
        boll_lower=indicators.get('boll_lower'),
    )

    # 4. 调用评分引擎
    result = analyze(stock_data)

    # 5. 存入 index_ratings 表
    detail_json = json.dumps(
        {
            'technical_score': result.technical_score,
            'operation_suggestion': result.operation_suggestion,
            'data_warnings': result.data_warnings,
            'technical_weight': result.technical_weight,
        },
        ensure_ascii=False,
    )

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO index_ratings
        (index_code, index_name, market, trade_date, total_score, rating,
         rating_label, kline_score, capital_score, close_price, pct_change, detail_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            code,
            name,
            market,
            latest_date_str,
            result.total_score,
            result.rating,
            result.rating_label,
            result.technical_score,
            result.capital_score,
            latest_close,
            pct_change,
            detail_json,
        ),
    )
    conn.commit()
    conn.close()

    logger.info(f'[指数评级] {name}: 总分={result.total_score}, 评级={result.rating}')

    return {
        'index_code': code,
        'name': name,
        'market': market,
        'trade_date': latest_date_str,
        'close': latest_close,
        'pct_change': pct_change,
        'total_score': result.total_score,
        'rating': result.rating,
        'rating_label': result.rating_label,
        'kline_score': result.technical_score,
    }


def score_all_indices() -> list:
    """批量评级所有指数，返回结果列表（单只失败不阻塞其他）"""
    results = []
    for idx in INDEX_LIST:
        try:
            r = score_index(idx)
            results.append(r)
        except Exception as e:
            logger.warning(f'[指数评级] {idx["name"]} 失败: {e}')
            results.append(
                {
                    'index_code': idx['code'],
                    'name': idx['name'],
                    'market': idx['market'],
                    'error': str(e),
                }
            )
    return results


def get_latest_ratings() -> list:
    """从 index_ratings 表获取所有指数最新评级"""
    from database.db_manager import get_connection

    conn = get_connection()
    cursor = conn.cursor()

    # 获取每只指数最新一条评级
    cursor.execute("""
        SELECT ir.* FROM index_ratings ir
        INNER JOIN (
            SELECT index_code, MAX(trade_date) as max_date
            FROM index_ratings
            GROUP BY index_code
        ) latest ON ir.index_code = latest.index_code AND ir.trade_date = latest.max_date
        ORDER BY ir.index_code
    """)
    rows = cursor.fetchall()
    conn.close()

    results = []
    for r in rows:
        results.append(
            {
                'code': r['index_code'],
                'name': r['index_name'],
                'market': r['market'],
                'trade_date': r['trade_date'],
                'close': r['close_price'],
                'pct_change': r['pct_change'],
                'total_score': r['total_score'],
                'rating': r['rating'],
                'rating_label': r['rating_label'],
                'kline_score': r['kline_score'],
            }
        )
    return results


def refresh_all() -> list:
    """完整刷新流程：采集K线 → 评级 → 返回结果"""
    logger.info('[指数评级] 开始刷新所有指数...')
    fetch_all_index_kline()
    results = score_all_indices()
    logger.info('[指数评级] 刷新完成')
    return results
