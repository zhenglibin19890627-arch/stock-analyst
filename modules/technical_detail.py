"""技术指标明细计算：为分析报告「四维评分详情·技术面」提供六类指标快照。

数据源：raw_kline 的收盘/最高/最低/成交量（调用方传入列表），纯函数、无 DB/网络依赖。
指标口径尽量贴近评分引擎：MA 多头空头排列、MACD(12,26,9)、RSI(14)、KDJ(9,3,3)、
BOLL(20,2)、量比（最新量/20日均量）。
"""


def _sma(vals, n):
    """简单移动平均（最近 n 个）。"""
    if len(vals) < n or n <= 0:
        return None
    return sum(vals[-n:]) / n


def _ema_list(vals, n):
    """EMA 序列（与 vals 等长，首值=首个收盘）。"""
    if not vals or n <= 0:
        return []
    k = 2.0 / (n + 1)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * k + out[-1] * (1 - k))
    return out


def _rsi(closes, n=14):
    """RSI（Wilder 简化版，初始窗口平均）。"""
    if len(closes) < n + 1:
        return None
    gains = 0.0
    losses = 0.0
    for i in range(1, n + 1):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains += diff
        else:
            losses -= diff
    avg_gain = gains / n
    avg_loss = losses / n
    if avg_loss == 0:
        return 100.0
    return 100.0 - 100.0 / (1.0 + avg_gain / avg_loss)


def _kdj(highs, lows, closes, n=9):
    """KDJ(9,3,3)，返回 (k, d, j)。"""
    if len(closes) < n:
        return None
    k = 50.0
    d = 50.0
    for i in range(len(closes)):
        window_high = max(highs[max(0, i - n + 1): i + 1])
        window_low = min(lows[max(0, i - n + 1): i + 1])
        if window_high == window_low:
            rsv = 50.0
        else:
            rsv = (closes[i] - window_low) / (window_high - window_low) * 100.0
        k = 2.0 / 3.0 * k + 1.0 / 3.0 * rsv
        d = 2.0 / 3.0 * d + 1.0 / 3.0 * k
    j = 3.0 * k - 2.0 * d
    return k, d, j


def _macd(closes):
    """MACD(12,26,9)，返回 (dif, dea, hist, state)。"""
    if len(closes) < 26:
        return None
    ema12 = _ema_list(closes, 12)
    ema26 = _ema_list(closes, 26)
    dif = [a - b for a, b in zip(ema12, ema26)]
    dea = _ema_list(dif, 9)
    hist = (dif[-1] - dea[-1]) * 2
    state = '多头' if dif[-1] > dea[-1] else '空头'
    if len(dif) >= 2 and len(dea) >= 2:
        if dif[-1] > dea[-1] and dif[-2] <= dea[-2]:
            state = '金叉(转多头)'
        elif dif[-1] < dea[-1] and dif[-2] >= dea[-2]:
            state = '死叉(转空头)'
    return dif[-1], dea[-1], hist, state


def compute_technical_detail(closes, highs, lows, volumes, latest_date='', key_prefix='', min_bars=20):
    """计算六类技术指标快照 → dict（数据不足时返回 None）。

    key_prefix: 键前缀（''=日线 / 'weekly_' / 'monthly_'，020R-48 多周期）。
    min_bars: 最小K线根数（日线20；月线可放宽到5，MACD/MA10 等按各自窗口内部守卫）。
    """
    if not closes or len(closes) < min_bars:
        return None

    p = key_prefix
    detail = {f'{p}latest_date': latest_date, f'{p}latest_close': round(closes[-1], 2)}

    # 1) 均线系统 MA5/MA10/MA20（020R-48：按可用均线降级——月线历史短时仅 MA5/MA10）
    ma5 = _sma(closes, 5)
    ma10 = _sma(closes, 10)
    ma20 = _sma(closes, 20)
    if ma5 is not None and ma10 is not None and ma20 is not None:
        detail[f'{p}ma5'] = round(ma5, 2)
        detail[f'{p}ma10'] = round(ma10, 2)
        detail[f'{p}ma20'] = round(ma20, 2)
        if ma5 > ma10 > ma20:
            detail[f'{p}ma_state'] = '多头排列'
        elif ma5 < ma10 < ma20:
            detail[f'{p}ma_state'] = '空头排列'
        else:
            detail[f'{p}ma_state'] = '均线纠缠'
    elif ma5 is not None and ma10 is not None:
        detail[f'{p}ma5'] = round(ma5, 2)
        detail[f'{p}ma10'] = round(ma10, 2)
        if ma5 > ma10:
            detail[f'{p}ma_state'] = '短期多头'
        else:
            detail[f'{p}ma_state'] = '短期空头'
    elif ma5 is not None:
        detail[f'{p}ma5'] = round(ma5, 2)
        if closes[-1] > ma5:
            detail[f'{p}ma_state'] = '价在MA5上方'
        else:
            detail[f'{p}ma_state'] = '价在MA5下方'

    # 2) MACD 趋势
    macd = _macd(closes)
    if macd:
        detail[f'{p}macd_dif'] = round(macd[0], 3)
        detail[f'{p}macd_dea'] = round(macd[1], 3)
        detail[f'{p}macd_hist'] = round(macd[2], 3)
        detail[f'{p}macd_state'] = macd[3]

    # 3) RSI(14) 超买超卖
    rsi = _rsi(closes, 14)
    if rsi is not None:
        detail[f'{p}rsi14'] = round(rsi, 1)
        if rsi > 70:
            detail[f'{p}rsi_state'] = '超买'
        elif rsi < 30:
            detail[f'{p}rsi_state'] = '超卖'
        elif 45 <= rsi <= 65:
            detail[f'{p}rsi_state'] = '健康'
        else:
            detail[f'{p}rsi_state'] = '中性'

    # 4) KDJ 超买超卖
    kdj = _kdj(highs, lows, closes)
    if kdj:
        detail[f'{p}kdj_k'] = round(kdj[0], 1)
        detail[f'{p}kdj_d'] = round(kdj[1], 1)
        detail[f'{p}kdj_j'] = round(kdj[2], 1)
        if kdj[0] > 80:
            detail[f'{p}kdj_state'] = '超买'
        elif kdj[0] < 20:
            detail[f'{p}kdj_state'] = '超卖'
        elif 40 <= kdj[0] <= 60:
            detail[f'{p}kdj_state'] = '健康'
        elif 20 <= kdj[0] < 40:
            detail[f'{p}kdj_state'] = '偏弱'
        else:
            detail[f'{p}kdj_state'] = '中性偏强'

    # 5) 布林带 BOLL(20,2)
    if ma20 is not None:
        window = closes[-20:]
        mean = ma20
        variance = sum((v - mean) ** 2 for v in window) / 20.0
        std = variance ** 0.5
        upper = mean + 2 * std
        lower = mean - 2 * std
        detail[f'{p}boll_upper'] = round(upper, 2)
        detail[f'{p}boll_mid'] = round(mean, 2)
        detail[f'{p}boll_lower'] = round(lower, 2)
        if upper - lower > 0:
            pos = max(0.0, min(100.0, (closes[-1] - lower) / (upper - lower) * 100))
            detail[f'{p}boll_position'] = round(pos, 1)
            if pos > 90:
                detail[f'{p}boll_state'] = '触及上轨'
            elif pos >= 70:
                detail[f'{p}boll_state'] = '上轨区'
            elif pos >= 50:
                detail[f'{p}boll_state'] = '中轨上方'
            elif pos >= 30:
                detail[f'{p}boll_state'] = '中轨下方'
            elif pos >= 10:
                detail[f'{p}boll_state'] = '下轨区'
            else:
                detail[f'{p}boll_state'] = '触及下轨'

    # 6) 量能配合：最新量 / 20日均量
    if volumes and len(volumes) >= 20:
        avg20 = sum(volumes[-20:]) / 20.0
        latest_vol = volumes[-1]
        if avg20 > 0 and latest_vol > 0:
            vr = latest_vol / avg20
            detail[f'{p}vol_ratio'] = round(vr, 2)
            if vr >= 1.8:
                detail[f'{p}vol_state'] = '明显放量'
            elif vr >= 1.3:
                detail[f'{p}vol_state'] = '温和放量'
            elif vr <= 0.7:
                detail[f'{p}vol_state'] = '明显缩量'
            elif vr <= 0.85:
                detail[f'{p}vol_state'] = '温和缩量'
            else:
                detail[f'{p}vol_state'] = '平量'

    return detail
