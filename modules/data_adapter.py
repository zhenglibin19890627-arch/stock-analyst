"""
数据适配器 —— 连接 SQLite 真实数据源与 v5.0 StockData 标准契约

职责：
1. 从数据库读取 K线/基本面/资金面/消息面 原始数据
2. 计算技术指标（MA/MACD/KDJ/RSI/布林带/量比）
3. 映射字段并组装 StockData 契约对象
4. 返回可直接传入 scoring_engine.analyze() 的 StockData

使用方式：
    from modules.data_adapter import load_stockdata_from_db
    data = load_stockdata_from_db(stock_id)
    result = scoring_engine.analyze(data)
"""

import logging
import os
import sys
from datetime import timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import get_connection

from modules.data_contract import StockData

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')


# ================================================================
# 一、技术指标计算（从 K 线序列推导）
# ================================================================


def _calc_ma(closes: list[float], period: int) -> float | None:
    """简单移动平均线"""
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 4)


def _calc_ema(values: list[float], period: int) -> float | None:
    """指数移动平均线"""
    if len(values) < period:
        return None
    k = 2.0 / (period + 1)
    ema = sum(values[:period]) / period  # 初始值用 SMA
    for i in range(period, len(values)):
        ema = values[i] * k + ema * (1 - k)
    return round(ema, 4)


def _calc_macd(closes: list[float]) -> tuple[float | None, float | None]:
    """计算 MACD DIF 和 DEA（12,26,9 参数）

    Returns:
        (dif, dea) — DIF = EMA12 - EMA26, DEA = EMA9(DIF)
    """
    if len(closes) < 35:  # 需要 26+9 天数据
        return None, None

    # 计算 EMA12 和 EMA26 序列
    k12 = 2.0 / (12 + 1)
    k26 = 2.0 / (26 + 1)

    ema12_list = []
    ema26_list = []

    # 初始值用前 N 个的 SMA
    ema12 = sum(closes[:12]) / 12
    ema26 = sum(closes[:26]) / 26

    for i in range(len(closes)):
        if i >= 12:
            ema12 = closes[i] * k12 + ema12 * (1 - k12)
        if i >= 26:
            ema26 = closes[i] * k26 + ema26 * (1 - k26)
        if i >= 26:
            ema12_list.append(ema12)
            ema26_list.append(ema26)

    # DIF 序列
    dif_list = [e12 - e26 for e12, e26 in zip(ema12_list, ema26_list)]

    if len(dif_list) < 9:
        return round(dif_list[-1], 4) if dif_list else None, None

    # DEA = EMA9(DIF)
    dea = _calc_ema(dif_list, 9)
    return round(dif_list[-1], 4), dea


def _calc_kdj(kline_rows: list[dict], period: int = 9) -> float | None:
    """计算 KDJ 的 K 值（9,3,3 参数）

    Returns:
        K 值，或 None
    """
    if len(kline_rows) < period:
        return None

    highs = [float(r['high'] or 0) for r in kline_rows]
    lows = [float(r['low'] or 0) for r in kline_rows]
    closes = [float(r['close'] or 0) for r in kline_rows]

    # 计算最近 period 的 RSV
    recent_high = max(highs[-period:])
    recent_low = min(lows[-period:])
    close = closes[-1]

    if recent_high == recent_low:
        rsv = 50.0
    else:
        rsv = (close - recent_low) / (recent_high - recent_low) * 100

    # K = 2/3 * 前K + 1/3 * RSV（假设前K=50初始化）
    k_prev = 50.0
    k = 2.0 / 3.0 * k_prev + 1.0 / 3.0 * rsv
    return round(k, 2)


def _calc_rsi(closes: list[float], period: int = 14) -> float | None:
    """计算 RSI（Wilder平滑算法，与同花顺/通达信一致）

    与旧版SMA算法的区别：
    1. 使用全部历史数据递推（而非仅取最近period天）
    2. 采用Wilder指数平滑：avg = (avg_prev * (period-1) + val) / period
       而非简单移动平均 sum/period
    """
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    # 第一个SMA初始化（前period天）
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder指数平滑递推（关键区别：累积历史记忆）
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def _calc_bollinger(
    closes: list[float], period: int = 20
) -> tuple[float | None, float | None, float | None]:
    """计算布林带（upper, mid, lower）"""
    if len(closes) < period:
        return None, None, None
    ma = sum(closes[-period:]) / period
    variance = sum((x - ma) ** 2 for x in closes[-period:]) / period
    std = variance**0.5
    upper = ma + 2 * std
    lower = ma - 2 * std
    return round(upper, 4), round(ma, 4), round(lower, 4)


def _calc_volume_ratio(volumes: list[float]) -> float | None:
    """计算量比 = 当日成交量 / 过去5日平均成交量"""
    if len(volumes) < 6 or volumes[-1] is None:
        return None
    avg_5d = sum(volumes[-6:-1]) / 5 if all(v is not None for v in volumes[-6:-1]) else None
    if avg_5d is None or avg_5d == 0:
        return None
    return round(volumes[-1] / avg_5d, 2)


# ================================================================
# 二、代码格式转换
# ================================================================


def _format_stock_code(symbol: str, market: str) -> str:
    """内部 symbol → StockData.code 格式

    A股: 600276 → 600276.SH（沪市）/ 000001 → 000001.SZ（深市）
    港股: HK3690 → 03690.HK
    """
    if market == 'hk_stock':
        code = symbol.replace('HK', '').replace('hk', '')
        return f'{code.zfill(5)}.HK'
    else:
        if symbol.startswith('6') or symbol.startswith('9'):
            return f'{symbol}.SH'
        else:
            return f'{symbol}.SZ'


def _market_to_contract(market: str) -> str:
    """数据库 market → StockData.market"""
    return 'HK' if market == 'hk_stock' else 'A'


# ================================================================
# 三、数据库读取层
# ================================================================


def _read_kline_data(stock_id: int, limit: int = 60) -> list[dict]:
    """读取最近 N 天 K 线数据（正序，最早→最新）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT trade_date, open, close, high, low, volume, amount, pct_change
        FROM raw_kline WHERE stock_id = ?
        ORDER BY trade_date DESC LIMIT ?
    """,
        (stock_id, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    rows.reverse()  # 转为正序
    return rows


def _read_fundamental_data(stock_id: int) -> dict | None:
    """读取最新一期基本面数据

    B21：聚合回退解决"行错位"——PE/PB 由腾讯估值接口独立 UPDATE 到当时最新 report_date 行，
    holder_increase 由股东增减持流程独立写入；force 重跑后 fetch_a_fundamental INSERT 的新行使
    这些字段错位到旧行，导致最新行读取为 NULL。为 NULL 时从其他行按 report_date DESC 取最近
    非空值兜底，保障 adapter 读取完整度。

    范围限定 pe_ratio/pb_ratio/holder_increase：这三个是"时点值"，跨行取最新非空语义正确；
    gross_margin/revenue_yoy 等"期间值"跨季聚合语义错误，故不纳入回退。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM raw_fundamental WHERE stock_id = ?
        ORDER BY report_date DESC LIMIT 1
    """,
        (stock_id,),
    )
    row = cursor.fetchone()
    if row is None:
        conn.close()
        return None
    fund = dict(row)  # 转可变 dict，便于回退填充

    # B21：对易错位的时点字段做聚合回退（最新行为 NULL 时，取次新非空行）
    _FALLBACK_FIELDS = ('pe_ratio', 'pb_ratio', 'holder_increase')
    for field in _FALLBACK_FIELDS:
        if fund.get(field) is None:
            cursor.execute(
                f'SELECT {field} AS val FROM raw_fundamental '
                f'WHERE stock_id = ? AND {field} IS NOT NULL '
                f'ORDER BY report_date DESC LIMIT 1',
                (stock_id,),
            )
            fb = cursor.fetchone()
            if fb and fb['val'] is not None:
                fund[field] = fb['val']
                logger.info(f'[B21] stock_id={stock_id} {field} 聚合回退取值={fb["val"]}')

    conn.close()
    return fund


def _read_capital_data(stock_id: int, limit: int = 10) -> list[dict]:
    """读取最近 N 天资金面数据（正序）。
    019E-R1：过滤估算行（is_estimated=1），确保评分仅使用真实数据。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM raw_capital_flow WHERE stock_id = ?
        AND (is_estimated = 0 OR is_estimated IS NULL)
        ORDER BY trade_date DESC LIMIT ?
    """,
        (stock_id, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    rows.reverse()
    return rows


def _read_news_sentiment(stock_id: int) -> dict | None:
    """读取最新消息面聚合数据"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM news_sentiment WHERE stock_id = ?
        ORDER BY news_date DESC LIMIT 1
    """,
        (stock_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def _read_stock_info(stock_id: int) -> dict | None:
    """读取股票基本信息"""
    conn = get_connection()
    cursor = conn.cursor()
    # B17-T2：读取 industry 以支持行业权重覆盖
    cursor.execute(
        'SELECT id, symbol, name, market, industry FROM stocks WHERE id = ?', (stock_id,)
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ================================================================
# 四、主函数：组装 StockData
# ================================================================


def load_stockdata_from_db(stock_id: int) -> StockData | None:
    """从数据库加载真实数据，组装为 StockData 契约对象

    Args:
        stock_id: 数据库 stocks.id
    Returns:
        StockData 对象，或 None（数据不足无法构建）
    """
    # 1. 读取股票信息
    stock_info = _read_stock_info(stock_id)
    if not stock_info:
        logger.warning(f'stock_id={stock_id} 不存在')
        return None

    symbol = stock_info['symbol']
    name = stock_info.get('name', '')
    market = stock_info['market']

    # 2. 读取 K 线（需要至少60天计算技术指标）
    kline_rows = _read_kline_data(stock_id, limit=60)
    if not kline_rows or len(kline_rows) < 5:
        logger.warning(f'[{symbol}] K线数据不足({len(kline_rows)}条)，无法构建 StockData')
        return None

    latest = kline_rows[-1]
    closes = [float(r['close'] or 0) for r in kline_rows]
    volumes = [float(r['volume'] or 0) for r in kline_rows]

    # 3. 计算技术指标
    ma5 = _calc_ma(closes, 5)
    ma10 = _calc_ma(closes, 10)
    ma20 = _calc_ma(closes, 20)
    ma60 = _calc_ma(closes, 60)
    rsi_14 = _calc_rsi(closes, 14)
    boll_upper, boll_mid, boll_lower = _calc_bollinger(closes, 20)
    macd_dif, macd_dea = _calc_macd(closes)
    kdj_k = _calc_kdj(kline_rows)
    volume_ratio = _calc_volume_ratio(volumes)

    # 4. 读取基本面
    fund = _read_fundamental_data(stock_id)

    # 5. 读取资金面
    cap_rows = _read_capital_data(stock_id, limit=10)

    # 6. 读取消息面
    news = _read_news_sentiment(stock_id)

    # 7. 组装 StockData
    code = _format_stock_code(symbol, market)
    market_contract = _market_to_contract(market)
    trade_date_str = str(latest['trade_date']).replace('-', '')  # YYYYMMDD

    # 资金面映射
    main_net_inflow = None
    north_net_buy = None
    margin_balance_chg = None

    if cap_rows:
        latest_cap = cap_rows[-1]
        main_net_inflow = latest_cap.get('main_net_inflow')

        # DATASRC-C: north_net_buy 向后搜索最近非空值
        # （北向资金数据可能不在最新一行）
        for row in reversed(cap_rows):
            v = row.get('north_holding_change')
            if v is not None:
                north_net_buy = v
                break

        # DATASRC-C: margin_balance_chg 向后搜索最近两个非空值计算日变化
        # （融资余额 T+1 公布，可能不在最新一行）
        margin_vals = []
        for row in reversed(cap_rows):
            mb = row.get('margin_balance')
            if mb is not None:
                margin_vals.append(mb)
                if len(margin_vals) >= 2:
                    break
        if len(margin_vals) >= 2:
            margin_balance_chg = round(margin_vals[0] - margin_vals[1], 2)

    # 消息面映射
    news_sentiment = None
    news_count = None
    news_positive_ratio = None
    news_negative_count = None
    if news:
        avg_sent = news.get('avg_sentiment')
        if avg_sent is not None:
            news_sentiment = float(avg_sent)
        # B22: 扩展消息面字段（从 news_sentiment 表已采集字段映射）
        total = news.get('total_count')
        pos = news.get('positive_count')
        neg = news.get('negative_count')
        if total is not None and total > 0:
            news_count = int(total)
            news_negative_count = int(neg) if neg is not None else 0
            if pos is not None:
                news_positive_ratio = round(pos / total, 2)

    # 构建 StockData
    data = StockData(
        code=code,
        market=market_contract,
        trade_date=trade_date_str,
        close=float(latest['close']),
        # 技术指标
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma60=ma60,
        macd_dif=macd_dif,
        macd_dea=macd_dea,
        kdj_k=kdj_k,
        rsi_14=rsi_14,
        volume=int(volumes[-1]) if volumes[-1] else None,
        volume_ratio=volume_ratio,
        boll_upper=boll_upper,
        boll_lower=boll_lower,
        # 基本面
        pe_ttm=fund.get('pe_ratio') if fund else None,
        pb=fund.get('pb_ratio') if fund else None,
        roe=fund.get('roe') if fund else None,
        gross_margin=fund.get('gross_margin') if fund else None,
        revenue_yoy=fund.get('revenue_growth') if fund else None,
        net_profit_yoy=fund.get('profit_growth') if fund else None,
        ocf_to_profit=fund.get('ocf_to_net_profit') if fund else None,
        debt_to_asset=fund.get('debt_ratio') if fund else None,
        current_ratio=fund.get('current_ratio') if fund else None,
        # 消息面与资金面
        news_sentiment=news_sentiment,
        news_count=news_count,  # B22 新增
        news_positive_ratio=news_positive_ratio,  # B22 新增
        news_negative_count=news_negative_count,  # B22 新增
        main_net_inflow=main_net_inflow,
        north_net_buy=north_net_buy,
        margin_balance_chg=margin_balance_chg,
        holder_increase=fund.get('holder_increase')
        if fund and 'holder_increase' in fund.keys()
        else None,  # B10: 从数据库读取实际值
        # 扩展
        extra={'name': name, 'stock_id': stock_id},
    )

    # B17-T2：携带行业信息，供 scoring_engine._load_dim_weights 做行业权重覆盖
    data.industry = (stock_info.get('industry') or '').strip() or None

    data.compute_data_quality()

    logger.info(
        f'[{code}] StockData 构建完成: '
        f'close={data.close}, ma5={ma5}, rsi={rsi_14}, '
        f'pe={data.pe_ttm}, pb={data.pb}, roe={data.roe}, '
        f'main_inflow={main_net_inflow}, sentiment={news_sentiment}, '
        f'data_quality={data.data_quality.model_dump() if data.data_quality else None}'
    )

    return data


# ================================================================
# 五、命令行测试入口
# ================================================================

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    # 查找数据库中有数据的股票
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT s.id, s.symbol, s.name, s.market,
               (SELECT COUNT(*) FROM raw_kline WHERE stock_id=s.id) as kline_cnt,
               (SELECT COUNT(*) FROM raw_fundamental WHERE stock_id=s.id) as fund_cnt,
               (SELECT COUNT(*) FROM raw_capital_flow WHERE stock_id=s.id) as cap_cnt
        FROM stocks s
        WHERE EXISTS(SELECT 1 FROM raw_kline WHERE stock_id=s.id)
        ORDER BY kline_cnt DESC LIMIT 5
    """)
    stocks = [dict(r) for r in cursor.fetchall()]
    conn.close()

    print(f'\n找到 {len(stocks)} 只有数据的股票:')
    for s in stocks:
        print(
            f'  id={s["id"]}  {s["symbol"]}  K线:{s["kline_cnt"]}  基本面:{s["fund_cnt"]}  资金面:{s["cap_cnt"]}'
        )

    print()

    for s in stocks[:3]:
        print(f'\n{"=" * 60}')
        print(f'  测试: {s["symbol"]} (stock_id={s["id"]})')
        print(f'{"=" * 60}')

        data = load_stockdata_from_db(s['id'])
        if data is None:
            print('  [SKIP] 数据不足，无法构建 StockData')
            continue

        # 打印技术指标
        print(f'  code={data.code}, market={data.market}, close={data.close}')
        print(f'  trade_date={data.trade_date}')
        print('\n  技术指标:')
        print(f'    MA5={data.ma5}, MA10={data.ma10}, MA20={data.ma20}, MA60={data.ma60}')
        print(f'    MACD: DIF={data.macd_dif}, DEA={data.macd_dea}')
        print(f'    KDJ_K={data.kdj_k}, RSI_14={data.rsi_14}')
        print(f'    BOLL: upper={data.boll_upper}, lower={data.boll_lower}')
        print(f'    Volume={data.volume}, VolumeRatio={data.volume_ratio}')
        print('\n  基本面:')
        print(f'    PE={data.pe_ttm}, PB={data.pb}, ROE={data.roe}')
        print(f'    GrossMargin={data.gross_margin}, RevenueYoY={data.revenue_yoy}')
        print(f'    NetProfitYoY={data.net_profit_yoy}, DebtToAsset={data.debt_to_asset}')
        print('\n  资金面:')
        print(f'    MainNetInflow={data.main_net_inflow}, NorthNetBuy={data.north_net_buy}')
        print(f'    MarginBalanceChg={data.margin_balance_chg}')
        print('\n  消息面:')
        print(f'    NewsSentiment={data.news_sentiment}')
        print('\n  数据质量:')
        if data.data_quality:
            dq = data.data_quality
            print(
                f'    技术面={dq.technical:.0%}  基本面={dq.fundamental:.0%}  '
                f'消息面={dq.news:.0%}  资金面={dq.capital:.0%}'
            )
        missing = data.missing_fields()
        if missing:
            print(f'    缺失字段({len(missing)}): {missing}')

    print(f'\n{"=" * 60}')
    print('  数据适配器测试完成 [PASS]')
    print(f'{"=" * 60}')
