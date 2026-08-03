"""
模块2：四维分析引擎
基于K线技术面、基本面、资金面（消息面暂不可用）三个维度，
通过量化因子打分，输出0-100综合评级。

核心功能：
1. 分维度评分（技术面/基本面/资金面/消息面）
2. 权重自适应归一化（数据缺失时自动重分配权重）
3. 外部JSON热加载权重配置
4. 评级档位映射（A/B+/B/C/D）
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

# 确保能找到项目根目录的 config 模块
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import RATING_THRESHOLDS, WEIGHTS_A_STOCK, WEIGHTS_CONFIG_FILE, WEIGHTS_HK_STOCK
from database.db_manager import get_connection

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# 北京时间
_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')


# ============================================================
# 一、权重配置热加载
# ============================================================


def _load_weights_config():
    """
    从 config_weights.json 热加载权重配置。
    文件不存在或解析失败时回退到 config.py 中的代码级默认值。
    """
    try:
        with open(WEIGHTS_CONFIG_FILE, encoding='utf-8') as f:
            return json.load(f)
    except FileNotFoundError:
        logger.warning(f'权重配置文件不存在: {WEIGHTS_CONFIG_FILE}，使用代码级默认值')
        return None
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f'权重配置文件解析失败: {e}，使用代码级默认值')
        return None


def _get_raw_weights(market):
    """
    获取指定市场的原始权重（未归一化）。
    优先读取JSON文件，回退到config.py。
    """
    config = _load_weights_config()
    if config:
        market_key = 'a_stock' if market == 'a_stock' else 'hk_stock'
        section = config.get(market_key, {})
        weights = section.get('weights')
        if weights:
            return weights

    # 回退到config.py代码级默认值
    if market == 'a_stock':
        return dict(WEIGHTS_A_STOCK)
    else:
        return dict(WEIGHTS_HK_STOCK)


def _get_rating_from_config():
    """从JSON文件获取评级映射，回退到config.py"""
    config = _load_weights_config()
    if config and 'rating_mapping' in config:
        return config['rating_mapping']
    # 回退：将config.py的元组格式转换
    result = {}
    for grade, (lo, hi, label) in RATING_THRESHOLDS.items():
        result[grade] = {'min': lo, 'max': hi, 'label': label}
    return result


# ============================================================
# 二、数据读取层
# ============================================================


def _read_kline_data(stock_id, limit=60):
    """读取K线数据（按日期升序，用于技术指标计算）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT trade_date, open, close, high, low, volume, pct_change
        FROM raw_kline WHERE stock_id = ?
        ORDER BY trade_date DESC LIMIT ?
    """,
        (stock_id, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    rows.reverse()  # 转为升序（旧→新），方便计算均线
    return rows


def _read_fundamental_data(stock_id):
    """读取最新基本面数据"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM raw_fundamental WHERE stock_id = ?
        ORDER BY report_date DESC LIMIT 4
    """,
        (stock_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _read_capital_data(stock_id, limit=20):
    """读取资金面数据（按日期升序）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM raw_capital_flow WHERE stock_id = ?
        ORDER BY trade_date DESC LIMIT ?
    """,
        (stock_id, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    rows.reverse()
    return rows


def _read_news_sentiment(stock_id):
    """读取最新消息面日聚合数据"""
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


def _read_news_detail(stock_id, limit=10):
    """读取逐条新闻明细（含情绪得分），用于前端透明化展示"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT title, sentiment_score, info_date, source
        FROM raw_sentiment
        WHERE stock_id = ? AND info_type = 'news'
        ORDER BY info_date DESC LIMIT ?
    """,
        (stock_id, limit),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def _char_bigrams(text):
    """生成中文字符bigrams（2-gram），用于中文标题相似度计算"""
    text = text.strip()
    if len(text) < 2:
        return {text} if text else set()
    return {text[i : i + 2] for i in range(len(text) - 1)}


def _jaccard_similarity(set_a, set_b):
    """计算两个集合的Jaccard相似系数"""
    if not set_a or not set_b:
        return 0.0
    intersection = set_a & set_b
    union = set_a | set_b
    return len(intersection) / len(union) if union else 0.0


_SIMILARITY_THRESHOLD = 0.35


def _dedup_news(news_rows):
    """
    两级去重：
    第一级：按(title, source_name, publish_date)三元组精确去重
    第二级：按标题Jaccard相似度≥0.6聚类，同一事件簇保留情绪绝对值最高的一条

    返回: (去重后列表, 原始条数)
    """
    raw_count = len(news_rows)

    # === 第一级：三元组精确去重 ===
    exact_deduped = {}
    for row in news_rows:
        src_raw = row.get('source', '') or ''
        source_name = src_raw.split('|')[0].strip() if '|' in src_raw else src_raw.strip()
        title = (row.get('title', '') or '').strip()
        pub_date = (row.get('info_date', '') or '').strip()

        key = (title, source_name, pub_date)
        score = abs(row.get('sentiment_score', 0) or 0)

        if key not in exact_deduped:
            exact_deduped[key] = row
        else:
            existing_score = abs(exact_deduped[key].get('sentiment_score', 0) or 0)
            if score > existing_score:
                exact_deduped[key] = row

    exact_list = list(exact_deduped.values())

    # === 第二级：标题语义相似度聚类 ===
    # 预计算所有标题的bigrams
    items = []
    for row in exact_list:
        title = (row.get('title', '') or '').strip()
        bigrams = _char_bigrams(title)
        score = abs(row.get('sentiment_score', 0) or 0)
        items.append(
            {
                'row': row,
                'title': title,
                'bigrams': bigrams,
                'score': score,
                'merged': False,
            }
        )

    result = []
    for i in range(len(items)):
        if items[i]['merged']:
            continue
        # 当前条目作为簇代表
        best = items[i]
        for j in range(i + 1, len(items)):
            if items[j]['merged']:
                continue
            sim = _jaccard_similarity(items[i]['bigrams'], items[j]['bigrams'])
            if sim >= _SIMILARITY_THRESHOLD:
                items[j]['merged'] = True
                # 保留情绪绝对值最高的一条
                if items[j]['score'] > best['score']:
                    best['row'] = items[j]['row']
                    best['score'] = items[j]['score']
        result.append(best['row'])

    return result, raw_count


def _generate_news_summary(
    news_data, news_detail_deduped, extreme_warning, symbol, raw_news_count=0
):
    """
    自动生成消息面核心见解（结构化摘要，≤3句）。

    三要素：
    1. 情绪定性：avg_sentiment 定性描述
    2. 关键事件：Top1-2高影响力新闻标题
    3. 风险提示：极端情绪时提示人工复核

    禁止：直接罗列新闻标题列表作为见解内容
    """
    avg_sentiment = news_data.get('avg_sentiment', 0) or 0
    total_count = len(news_detail_deduped)

    parts = []

    # 1. 情绪定性
    if avg_sentiment > 0.3:
        sentiment_word = '显著正面'
    elif avg_sentiment > 0.1:
        sentiment_word = '偏正面'
    elif avg_sentiment < -0.3:
        sentiment_word = '显著负面'
    elif avg_sentiment < -0.1:
        sentiment_word = '偏负面'
    else:
        sentiment_word = '中性'

    parts.append(
        f'近24h消息面{sentiment_word}（avg_sentiment={avg_sentiment:.2f}，'
        f'有效新闻{total_count}条（原始{raw_news_count if raw_news_count else total_count}条）'
    )

    # 2. 关键事件（按情绪绝对值排序取Top1-2）
    sorted_news = sorted(
        news_detail_deduped, key=lambda r: abs(r.get('sentiment_score', 0) or 0), reverse=True
    )
    top_news = sorted_news[:2]
    event_titles = []
    for n in top_news:
        title = (n.get('title', '') or '').strip()
        if not title:
            continue
        short_title = title[:25] + ('...' if len(title) > 25 else '')
        score = n.get('sentiment_score', 0) or 0
        tag = '利好' if score > 0.1 else ('利空' if score < -0.1 else '中性')
        event_titles.append(f'「{short_title}」({tag})')

    if event_titles:
        parts.append('关键事件：' + '、'.join(event_titles))

    # 3. 风险提示
    if extreme_warning:
        parts.append('⚠️ 情绪极端，建议人工复核原文（请前往「查看数据」页查看新闻详情）')

    return '。'.join(parts) + '。'


def _get_stock_info(stock_id):
    """获取股票基本信息"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, symbol, name, market FROM stocks WHERE id = ?', (stock_id,))
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ============================================================
# 三、技术面评分引擎（K线）
# ============================================================


def _calc_ma(closes, period):
    """计算简单移动平均线"""
    if len(closes) < period:
        return None
    return sum(closes[-period:]) / period


def _calc_rsi(closes, period=14):
    """计算RSI（相对强弱指标）"""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(len(closes) - period, len(closes)):
        diff = closes[i] - closes[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - (100.0 / (1.0 + rs))


def _calc_bollinger(closes, period=20):
    """计算布林带位置（当前价格在中轨上方/下方的百分比位置）"""
    if len(closes) < period:
        return None, None, None
    ma = sum(closes[-period:]) / period
    variance = sum((x - ma) ** 2 for x in closes[-period:]) / period
    std = variance**0.5
    upper = ma + 2 * std
    lower = ma - 2 * std
    return upper, ma, lower


def score_kline(kline_data):
    """
    K线技术面评分（0-100）

    因子构成：
    1. MA5/MA20金叉死叉趋势（25分）
    2. RSI超买超卖状态（25分）
    3. 布林带位置（20分）
    4. 近5日涨跌幅趋势（15分）
    5. 成交量异动（15分）
    """
    factors = {}
    total_score = 0.0

    if not kline_data or len(kline_data) < 5:
        return 0.0, {'error': 'K线数据不足（至少需要5条）'}, {}

    closes = [float(r['close'] or 0) for r in kline_data]
    volumes = [float(r['volume'] or 0) for r in kline_data]
    latest_close = closes[-1]

    # --- 因子1：MA5/MA20趋势（25分） ---
    ma5 = _calc_ma(closes, 5)
    ma20 = _calc_ma(closes, 20)

    if ma5 is not None and ma20 is not None:
        if ma5 > ma20:
            # 金叉状态（多头排列）
            deviation = (ma5 - ma20) / ma20 * 100 if ma20 > 0 else 0
            # 偏离度越大得分越高，但有上限
            trend_score = min(25.0, 15.0 + deviation * 2)
            factors['ma_trend'] = f'多头排列(MA5={ma5:.2f} > MA20={ma20:.2f})'
        else:
            # 死叉状态（空头排列）
            deviation = (ma20 - ma5) / ma20 * 100 if ma20 > 0 else 0
            trend_score = max(0.0, 12.0 - deviation * 2)
            factors['ma_trend'] = f'空头排列(MA5={ma5:.2f} < MA20={ma20:.2f})'
        factors['ma5'] = round(ma5, 2)
        factors['ma20'] = round(ma20, 2)
    else:
        trend_score = 12.0  # 数据不足给中性分
        factors['ma_trend'] = '数据不足'
    total_score += trend_score
    factors['_ma_score'] = round(trend_score, 1)

    # --- 因子2：RSI状态（25分） ---
    rsi = _calc_rsi(closes, 14)
    if rsi is not None:
        if rsi > 70:
            # 超买区，有回调风险
            rsi_score = max(5.0, 25.0 - (rsi - 70) * 1.5)
            factors['rsi_status'] = f'超买({rsi:.1f})'
        elif rsi < 30:
            # 超卖区，有反弹机会
            rsi_score = max(5.0, 25.0 - (30 - rsi) * 1.0)
            factors['rsi_status'] = f'超卖({rsi:.1f})'
        elif 45 <= rsi <= 65:
            # 健康上升趋势（55为最佳）
            rsi_score = 20.0 + max(0, 5 - abs(rsi - 55))
            factors['rsi_status'] = f'健康({rsi:.1f})'
        else:
            # 中性区域
            rsi_score = 15.0
            factors['rsi_status'] = f'中性({rsi:.1f})'
        factors['rsi'] = round(rsi, 1)
    else:
        rsi_score = 12.0
        factors['rsi_status'] = '数据不足'
    total_score += rsi_score
    factors['_rsi_score'] = round(rsi_score, 1)

    # --- 因子3：布林带位置（20分） ---
    upper, mid, lower = _calc_bollinger(closes, 20)
    if upper is not None and mid is not None and lower is not None:
        band_width = upper - lower
        if band_width > 0:
            position = (latest_close - lower) / band_width * 100
            # 位置在40%-60%最佳（中轨附近偏上），过高或过低扣分
            if 40 <= position <= 70:
                boll_score = 18.0
            elif position > 90:
                boll_score = 5.0  # 触及上轨，回调风险
            elif position < 10:
                boll_score = 8.0  # 触及下轨
            else:
                boll_score = 12.0
            factors['boll_position'] = f'{position:.1f}%'
        else:
            boll_score = 10.0
            factors['boll_position'] = '带宽为零'
        factors['boll_upper'] = round(upper, 2)
        factors['boll_mid'] = round(mid, 2)
        factors['boll_lower'] = round(lower, 2)
    else:
        boll_score = 10.0
        factors['boll_position'] = '数据不足'
    total_score += boll_score
    factors['_boll_score'] = round(boll_score, 1)

    # --- 因子4：近5日涨跌幅趋势（15分） ---
    if len(kline_data) >= 6:
        recent_5 = kline_data[-5:]
        # pct_change在DB中是数值型（非格式化字符串）
        pct_5d = []
        for r in recent_5:
            v = r.get('pct_change')
            if v is not None:
                try:
                    pct_5d.append(float(v))
                except (ValueError, TypeError):
                    pass

        if pct_5d:
            cumulative = sum(pct_5d)
            if cumulative > 5:
                trend_pct_score = min(15.0, 10.0 + cumulative * 0.5)
                factors['recent_trend'] = f'强势上涨(5日累计{cumulative:+.2f}%)'
            elif cumulative > 0:
                trend_pct_score = 10.0 + cumulative * 0.5
                factors['recent_trend'] = f'温和上涨(5日累计{cumulative:+.2f}%)'
            elif cumulative > -5:
                trend_pct_score = max(3.0, 8.0 + cumulative * 0.3)
                factors['recent_trend'] = f'小幅调整(5日累计{cumulative:+.2f}%)'
            else:
                trend_pct_score = max(0.0, 5.0 + cumulative * 0.2)
                factors['recent_trend'] = f'持续下跌(5日累计{cumulative:+.2f}%)'
        else:
            trend_pct_score = 7.0
            factors['recent_trend'] = '涨跌幅数据缺失'
    else:
        trend_pct_score = 7.0
        factors['recent_trend'] = '数据不足'
    total_score += trend_pct_score
    factors['_trend_score'] = round(trend_pct_score, 1)

    # --- 因子5：成交量异动（15分） ---
    if len(volumes) >= 10:
        avg_vol_20 = sum(volumes[-min(20, len(volumes)) :]) / min(20, len(volumes))
        latest_vol = volumes[-1]
        if avg_vol_20 > 0:
            vol_ratio = latest_vol / avg_vol_20
            if vol_ratio > 2.0:
                # 放量明显（超过均量2倍）
                if latest_close > closes[-2] if len(closes) >= 2 else False:
                    vol_score = 15.0  # 放量上涨
                    factors['volume'] = f'放量上涨({vol_ratio:.1f}倍)'
                else:
                    vol_score = 6.0  # 放量下跌
                    factors['volume'] = f'放量下跌({vol_ratio:.1f}倍)'
            elif vol_ratio > 1.3:
                vol_score = 12.0
                factors['volume'] = f'温和放量({vol_ratio:.1f}倍)'
            elif vol_ratio > 0.7:
                vol_score = 10.0
                factors['volume'] = f'正常量({vol_ratio:.1f}倍)'
            else:
                vol_score = 7.0
                factors['volume'] = f'缩量({vol_ratio:.1f}倍)'
        else:
            vol_score = 7.0
            factors['volume'] = '均量为零'
    else:
        vol_score = 7.0
        factors['volume'] = '数据不足'
    total_score += vol_score
    factors['_vol_score'] = round(vol_score, 1)

    # 最新日期
    data_cutoff = kline_data[-1].get('trade_date', '') if kline_data else None

    return round(total_score, 1), factors, {'kline': data_cutoff}


# ============================================================
# 四、基本面评分引擎
# ============================================================


def score_fundamental(fund_data):
    """
    基本面评分（0-100）

    因子构成：
    1. ROE净资产收益率（25分）
    2. 净利率（15分）
    3. 营收增长率（20分）
    4. 资产负债率（15分）
    5. PE/PB估值百分位（25分，缺失时降权并重分配）
    """
    factors = {}
    total_score = 0.0

    if not fund_data:
        return 0.0, {'error': '无基本面数据'}, {}

    latest = fund_data[0]  # 最新财报

    # --- 因子1：ROE（25分） ---
    roe = latest.get('roe')
    if roe is not None:
        try:
            roe = float(roe)
            if roe >= 20:
                roe_score = 25.0
                factors['roe'] = f'{roe:.2f}% (优秀)'
            elif roe >= 15:
                roe_score = 21.0
                factors['roe'] = f'{roe:.2f}% (良好)'
            elif roe >= 10:
                roe_score = 16.0
                factors['roe'] = f'{roe:.2f}% (一般)'
            elif roe >= 5:
                roe_score = 10.0
                factors['roe'] = f'{roe:.2f}% (偏低)'
            elif roe >= 0:
                roe_score = 5.0
                factors['roe'] = f'{roe:.2f}% (较差)'
            else:
                roe_score = 0.0
                factors['roe'] = f'{roe:.2f}% (亏损)'
        except (ValueError, TypeError):
            roe_score = 0.0
            factors['roe'] = '数据异常'
    else:
        roe_score = 0.0
        factors['roe'] = '缺失'
    total_score += roe_score
    factors['_roe_score'] = round(roe_score, 1)

    # --- 因子2：净利率（15分） ---
    net_margin = latest.get('net_margin')
    if net_margin is not None:
        try:
            net_margin = float(net_margin)
            if net_margin >= 30:
                nm_score = 15.0
            elif net_margin >= 20:
                nm_score = 12.0
            elif net_margin >= 10:
                nm_score = 9.0
            elif net_margin >= 5:
                nm_score = 6.0
            elif net_margin >= 0:
                nm_score = 3.0
            else:
                nm_score = 0.0
            factors['net_margin'] = f'{net_margin:.2f}%'
        except (ValueError, TypeError):
            nm_score = 0.0
            factors['net_margin'] = '数据异常'
    else:
        nm_score = 0.0
        factors['net_margin'] = '缺失'
    total_score += nm_score
    factors['_nm_score'] = round(nm_score, 1)

    # --- 因子3：营收增长率（20分） ---
    rev_growth = latest.get('revenue_growth')
    if rev_growth is not None:
        try:
            rev_growth = float(rev_growth)
            if rev_growth >= 30:
                rg_score = 20.0
            elif rev_growth >= 20:
                rg_score = 16.0
            elif rev_growth >= 10:
                rg_score = 12.0
            elif rev_growth >= 0:
                rg_score = 8.0
            elif rev_growth >= -10:
                rg_score = 4.0
            else:
                rg_score = 0.0
            factors['revenue_growth'] = f'{rev_growth:.2f}%'
        except (ValueError, TypeError):
            rg_score = 0.0
            factors['revenue_growth'] = '数据异常'
    else:
        rg_score = 0.0
        factors['revenue_growth'] = '缺失'
    total_score += rg_score
    factors['_rg_score'] = round(rg_score, 1)

    # --- 因子4：资产负债率（15分） ---
    debt_ratio = latest.get('debt_ratio')
    if debt_ratio is not None:
        try:
            debt_ratio = float(debt_ratio)
            if debt_ratio <= 30:
                dr_score = 15.0
            elif debt_ratio <= 50:
                dr_score = 12.0
            elif debt_ratio <= 60:
                dr_score = 9.0
            elif debt_ratio <= 70:
                dr_score = 6.0
            else:
                dr_score = 3.0
            factors['debt_ratio'] = f'{debt_ratio:.2f}%'
        except (ValueError, TypeError):
            dr_score = 0.0
            factors['debt_ratio'] = '数据异常'
    else:
        dr_score = 0.0
        factors['debt_ratio'] = '缺失'
    total_score += dr_score
    factors['_dr_score'] = round(dr_score, 1)

    # --- 因子5：PE/PB估值（25分） ---
    pe = latest.get('pe_ratio')
    pb = latest.get('pb_ratio')
    pe_pb_factors = 0  # 有效因子数（0/1/2）

    if pe is not None:
        try:
            pe = float(pe)
            pe_pb_factors += 1
            if pe <= 0:
                pe_score = 3.0  # 亏损
            elif pe <= 15:
                pe_score = 12.5  # 低估
            elif pe <= 25:
                pe_score = 10.0  # 合理
            elif pe <= 40:
                pe_score = 7.0  # 偏高
            elif pe <= 60:
                pe_score = 4.0  # 高估
            else:
                pe_score = 1.0  # 严重高估
            factors['pe_ratio'] = f'{pe:.2f}'
        except (ValueError, TypeError):
            pe_score = 0.0
            pe = None
    else:
        pe_score = 0.0

    if pb is not None:
        try:
            pb = float(pb)
            pe_pb_factors += 1
            if pb <= 1:
                pb_score = 12.5  # 破净
            elif pb <= 2:
                pb_score = 10.0
            elif pb <= 4:
                pb_score = 7.0
            elif pb <= 6:
                pb_score = 4.0
            else:
                pb_score = 1.0
            factors['pb_ratio'] = f'{pb:.2f}'
        except (ValueError, TypeError):
            pb_score = 0.0
            pb = None
    else:
        pb_score = 0.0

    # PE/PB缺失时的降权重分配逻辑
    if pe_pb_factors == 0:
        # 两个都缺失：25分降权，按比例重分配到前4个因子
        # 前四个因子满分=25+15+20+15=75，实际得分已经按满分75计
        # 此处不额外加分，直接标注
        factors['pe_pb_status'] = 'PE/PB均缺失，估值因子跳过'
        pe_pb_final = 0.0
    elif pe_pb_factors == 1:
        # 只有一个：该因子按满分12.5*2=25计算（即放大1倍）
        single_score = pe_score if pe is not None else pb_score
        pe_pb_final = min(25.0, single_score * 2)
        factors['pe_pb_status'] = f'仅{"PE" if pe is not None else "PB"}可用'
    else:
        pe_pb_final = pe_score + pb_score

    total_score += pe_pb_final
    factors['_pepb_score'] = round(pe_pb_final, 1)

    data_cutoff = latest.get('report_date', '')

    return round(total_score, 1), factors, {'fundamental': data_cutoff}


# ============================================================
# 五、资金面评分引擎
# ============================================================


def score_capital_flow(capital_data):
    """
    资金面评分（0-100）

    因子构成：
    1. 主力净流入趋势（近5日均量）（30分）
    2. 超大单占比（20分）
    3. 连续流入/流出天数（25分）
    4. 主力净流入占比（%）（25分）
    """
    factors = {}
    total_score = 0.0

    if not capital_data:
        return 0.0, {'error': '无资金面数据'}, {}

    # 取最近的数据（已按升序排列）
    recent = capital_data[-min(10, len(capital_data)) :]
    latest = recent[-1]

    # --- 因子1：主力净流入趋势（30分） ---
    recent_5 = recent[-min(5, len(recent)) :]
    main_flows = []
    for r in recent_5:
        v = r.get('main_net_inflow')
        if v is not None:
            try:
                main_flows.append(float(v))
            except (ValueError, TypeError):
                pass

    if main_flows:
        avg_main = sum(main_flows) / len(main_flows)
        positive_days = sum(1 for x in main_flows if x > 0)
        ratio = positive_days / len(main_flows)

        if ratio >= 0.8 and avg_main > 0:
            trend_score = 30.0
            factors['main_trend'] = f'持续大幅流入(5日均{avg_main:.2f}万元)'
        elif ratio >= 0.6:
            trend_score = 22.0
            factors['main_trend'] = f'温和流入(5日均{avg_main:.2f}万元)'
        elif ratio >= 0.4:
            trend_score = 15.0
            factors['main_trend'] = f'多空均衡(5日均{avg_main:.2f}万元)'
        elif ratio >= 0.2:
            trend_score = 8.0
            factors['main_trend'] = f'温和流出(5日均{avg_main:.2f}万元)'
        else:
            trend_score = 2.0
            factors['main_trend'] = f'持续流出(5日均{avg_main:.2f}万元)'
        factors['main_avg_5d'] = round(avg_main, 2)
    else:
        trend_score = 0.0
        factors['main_trend'] = '数据缺失'
    total_score += trend_score
    factors['_trend_score'] = round(trend_score, 1)

    # --- 因子2：超大单占比（20分） ---
    super_large = latest.get('super_large_net')
    large = latest.get('large_net')
    if super_large is not None and large is not None:
        try:
            sl = float(super_large)
            lg = float(large)

            if sl > 0 and sl > abs(lg):
                sl_score = 20.0
                factors['super_large'] = f'超大单主导({sl:.2f}万元)'
            elif sl > 0:
                sl_score = 15.0
                factors['super_large'] = f'超大单净流入({sl:.2f}万元)'
            elif sl > -abs(lg) if lg != 0 else False:
                sl_score = 8.0
                factors['super_large'] = f'超大单小幅流出({sl:.2f}万元)'
            else:
                sl_score = 3.0
                factors['super_large'] = f'超大单大幅流出({sl:.2f}万元)'
        except (ValueError, TypeError):
            sl_score = 0.0
            factors['super_large'] = '数据异常'
    else:
        sl_score = 0.0
        factors['super_large'] = '缺失'
    total_score += sl_score
    factors['_sl_score'] = round(sl_score, 1)

    # --- 因子3：连续流入/流出天数（25分） ---
    consecutive = 0
    direction = None
    for r in reversed(recent):
        v = r.get('main_net_inflow')
        if v is None:
            continue
        try:
            v = float(v)
        except (ValueError, TypeError):
            continue

        if direction is None:
            direction = 'in' if v > 0 else 'out'
            consecutive = 1
        elif (direction == 'in' and v > 0) or (direction == 'out' and v <= 0):
            consecutive += 1
        else:
            break

    if direction == 'in':
        if consecutive >= 5:
            consec_score = 25.0
        elif consecutive >= 3:
            consec_score = 20.0
        elif consecutive >= 2:
            consec_score = 15.0
        else:
            consec_score = 10.0
        factors['consecutive'] = f'连续流入{consecutive}天'
    elif direction == 'out':
        if consecutive >= 5:
            consec_score = 2.0
        elif consecutive >= 3:
            consec_score = 5.0
        elif consecutive >= 2:
            consec_score = 10.0
        else:
            consec_score = 12.0
        factors['consecutive'] = f'连续流出{consecutive}天'
    else:
        consec_score = 12.0
        factors['consecutive'] = '无数据'
    total_score += consec_score
    factors['_consec_score'] = round(consec_score, 1)

    # --- 因子4：主力净流入占比（25分） ---
    main_pct = latest.get('main_net_inflow_pct')
    if main_pct is not None:
        try:
            main_pct = float(main_pct)
            if main_pct >= 10:
                pct_score = 25.0
            elif main_pct >= 5:
                pct_score = 20.0
            elif main_pct >= 2:
                pct_score = 16.0
            elif main_pct >= 0:
                pct_score = 12.0
            elif main_pct >= -5:
                pct_score = 6.0
            elif main_pct >= -10:
                pct_score = 3.0
            else:
                pct_score = 0.0
            factors['main_pct'] = f'{main_pct:.2f}%'
        except (ValueError, TypeError):
            pct_score = 0.0
            factors['main_pct'] = '数据异常'
    else:
        pct_score = 0.0
        factors['main_pct'] = '缺失'
    total_score += pct_score
    factors['_pct_score'] = round(pct_score, 1)

    data_cutoff = latest.get('trade_date', '')

    return round(total_score, 1), factors, {'capital_flow': data_cutoff}


# ============================================================
# 五b、消息面评分引擎（模块4接入）
# ============================================================


def score_news(news_data):
    """
    消息面评分（0-100）

    评分因子构成（3因子，满分100）：
      因子1: 日均情绪得分(40分) — avg_sentiment从-1~1映射到0~40
      因子2: 新闻活跃度(30分) — 总新闻条数，10条满分
      因子3: 正面占比(30分) — positive_count/total_count映射到0~30
    """
    factors = {}
    total_score = 0.0

    avg_sentiment = news_data.get('avg_sentiment', 0.0)
    total_count = news_data.get('total_count', 0)
    pos_count = news_data.get('positive_count', 0)
    neg_count = news_data.get('negative_count', 0)

    # 因子1: 日均情绪得分(40分)
    # avg_sentiment: -1(极空)~+1(极多) → 映射到 0~40分
    # 0分为极空，20分为中性，40分为极多
    sentiment_score = max(0.0, min(40.0, (avg_sentiment + 1.0) * 20.0))
    total_score += sentiment_score
    factors['avg_sentiment'] = (
        f'{avg_sentiment:+.2f}({"正面" if avg_sentiment > 0.1 else "负面" if avg_sentiment < -0.1 else "中性"})'
    )
    factors['_sentiment_score'] = round(sentiment_score, 1)

    # 因子2: 新闻活跃度(30分)
    # 10条满分，按比例折算
    activity_score = min(30.0, total_count / 10.0 * 30.0)
    total_score += activity_score
    factors['news_activity'] = f'{total_count}条新闻'
    factors['_activity_score'] = round(activity_score, 1)

    # 因子3: 正面占比(30分)
    # positive/total → 0~30分映射
    if total_count > 0:
        pos_ratio = pos_count / total_count
        pos_score = pos_ratio * 30.0
    else:
        pos_score = 0.0
    total_score += pos_score
    factors['positive_ratio'] = (
        f'正面{pos_count}/负面{neg_count}/中性{news_data.get("neutral_count", 0)}'
    )
    factors['_pos_score'] = round(pos_score, 1)

    # 头条新闻
    top_title = news_data.get('top_news_title', '')
    if top_title:
        factors['top_news'] = top_title[:50] + ('...' if len(top_title) > 50 else '')

    news_date = news_data.get('news_date', '')

    return round(total_score, 1), factors, {'news': news_date}


# ============================================================
# 六、综合分析主入口
# ============================================================


def _map_rating(total_score):
    """总分 → 中文5档评级（RATING-ALIGN-004）
    按min降序遍历，第一个 score >= min 即匹配，避免区间间隙问题。
    """
    rating_map = _get_rating_from_config()
    # 按 min 降序排列：强烈推荐买入(85) > 推荐买入(70) > 持有观望(50) > 建议减仓(30) > 强烈建议卖出(0)
    sorted_ratings = sorted(rating_map.items(), key=lambda x: x[1]['min'], reverse=True)
    for grade, info in sorted_ratings:
        if total_score >= info['min']:
            return grade, info.get('label', '')
    # 兜底：分数低于所有min时返回最低评级
    if sorted_ratings:
        last = sorted_ratings[-1]
        return last[0], last[1].get('label', '')
    return '强烈建议卖出', ''


def _normalize_weights(raw_weights, available_dims):
    """
    权重归一化：数据缺失的维度权重归零，剩余维度按原比例重新归一化。

    关键规则：
    1. 不可用的维度（不在available_dims中）权重直接归零
    2. 可用但配置权重为0的维度，自动分配最低权重(MIN_WEIGHT=0.05)，确保不遗漏
    3. 剩余维度按比例重新归一化至总和=1.0

    返回: (归一化后的权重字典, 是否发生了重分配)
    """
    MIN_WEIGHT = 0.05  # 可用维度的最低权重保障

    # 对可用维度分配权重：配置权重>0的用原值，配置权重=0的给最低权重
    active = {}
    for k in available_dims:
        config_w = raw_weights.get(k, 0)
        if config_w > 0:
            active[k] = config_w
        else:
            # 可用但配置权重为0，分配最低权重避免被完全忽略
            active[k] = MIN_WEIGHT
            logger.info(f'维度 {k} 可用但配置权重为0，分配最低权重 {MIN_WEIGHT}')

    total = sum(active.values())
    if total == 0:
        # 所有维度权重都为0（理论上不会到这，因为available_dims至少有MIN_WEIGHT）
        n = len(available_dims)
        if n > 0:
            return {k: round(1.0 / n, 4) for k in available_dims}, True
        return {}, True

    # 归一化
    normalized = {k: round(v / total, 4) for k, v in active.items()}
    was_rescaled = abs(total - 1.0) > 0.001
    return normalized, was_rescaled


def analyze_stock(stock_id):
    """
    对指定股票执行四维综合分析，返回完整评分结果。

    返回结构：
    {
        "stock_code": "600276",
        "score_date": "2026-07-16",
        "total_score": 72.5,
        "rating": "B+",
        "dimensions": {
            "kline": {"score": 68.0, "weight": 0.30, "factors": {...}},
            "fundamental": {"score": 75.0, "weight": 0.30, "factors": {...}},
            "capital_flow": {"score": 78.0, "weight": 0.40, "factors": {...}},
            "news": {"score": 0, "weight": 0.0, "factors": {}, "status": "unavailable"}
        },
        "data_cutoff": {
            "kline": "2026-07-16",
            "fundamental": "2026-03-31",
            "capital_flow": "2026-07-15"
        }
    }
    """
    logger.info(f'开始分析 stock_id={stock_id}')

    # 1. 获取股票信息
    stock_info = _get_stock_info(stock_id)
    if not stock_info:
        return {'success': False, 'message': f'未找到股票 stock_id={stock_id}'}

    symbol = stock_info['symbol']
    name = stock_info.get('name', '')
    market = stock_info['market']

    # 2. 读取各维度数据
    kline_data = _read_kline_data(stock_id, limit=60)
    fund_data = _read_fundamental_data(stock_id)
    capital_data = _read_capital_data(stock_id, limit=20)

    # 3. 各维度打分
    dimensions = {}
    data_cutoffs = {}
    available_dims = set()

    # --- K线技术面 ---
    if kline_data and len(kline_data) >= 5:
        kl_score, kl_factors, kl_cutoff = score_kline(kline_data)
        dimensions['kline'] = {
            'score': kl_score,
            'weight': 0,  # 占位，后续归一化
            'factors': kl_factors,
            'status': 'ok',
            'data_count': len(kline_data),
        }
        data_cutoffs.update(kl_cutoff)
        available_dims.add('kline')
    else:
        dimensions['kline'] = {
            'score': 0,
            'weight': 0,
            'factors': {},
            'status': 'insufficient_data',
            'reason': f'K线数据不足（{len(kline_data) if kline_data else 0}条，需≥5条）',
        }

    # --- 基本面 ---
    if fund_data:
        fund_score, fund_factors, fund_cutoff = score_fundamental(fund_data)
        dimensions['fundamental'] = {
            'score': fund_score,
            'weight': 0,
            'factors': fund_factors,
            'status': 'ok',
            'data_count': len(fund_data),
        }
        data_cutoffs.update(fund_cutoff)
        available_dims.add('fundamental')
    else:
        dimensions['fundamental'] = {
            'score': 0,
            'weight': 0,
            'factors': {},
            'status': 'unavailable',
            'reason': '无基本面数据',
        }

    # --- 资金面 ---
    if capital_data:
        cap_score, cap_factors, cap_cutoff = score_capital_flow(capital_data)
        dimensions['capital_flow'] = {
            'score': cap_score,
            'weight': 0,
            'factors': cap_factors,
            'status': 'ok',
            'data_count': len(capital_data),
        }
        data_cutoffs.update(cap_cutoff)
        available_dims.add('capital_flow')
    else:
        dimensions['capital_flow'] = {
            'score': 0,
            'weight': 0,
            'factors': {},
            'status': 'unavailable',
            'reason': '无资金面数据',
        }

    # --- 消息面（模块4接入后动态读取） ---
    news_data = _read_news_sentiment(stock_id)
    news_summary = ''
    if news_data and news_data.get('total_count', 0) > 0:
        news_score, news_factors, news_cutoff = score_news(news_data)

        # 读取新闻明细并去重（语义级）
        news_detail_raw = _read_news_detail(stock_id, limit=15)
        news_detail_deduped, raw_news_count = _dedup_news(news_detail_raw)
        logger.info(
            f'[{symbol}] 新闻去重: 原始{raw_news_count}条 → 有效{len(news_detail_deduped)}条'
        )

        # 极端情绪预警检查
        avg_s = news_data.get('avg_sentiment', 0) or 0
        is_extreme = abs(avg_s) >= 0.95
        if is_extreme:
            news_factors['extreme_warning'] = True
            logger.warning(f'[{symbol}] 消息面极端情绪预警: avg_sentiment={avg_s:.4f}')
            top3_titles = [r.get('title', '')[:50] for r in news_detail_deduped[:3]]
            logger.warning(f'[{symbol}] 极端情绪Top3新闻: {top3_titles}')
            news_factors['extreme_warning_titles'] = top3_titles

        # 生成消息面核心见解（结构化摘要）
        news_summary = _generate_news_summary(
            news_data, news_detail_deduped, is_extreme, symbol, raw_news_count
        )

        dimensions['news'] = {
            'score': news_score,
            'weight': 0,  # 由归一化分配
            'factors': news_factors,
            'status': 'ok',
            'data_count': news_data['total_count'],
            'extreme_warning': is_extreme,
        }
        data_cutoffs.update(news_cutoff)
        available_dims.add('news')
    else:
        dimensions['news'] = {
            'score': 0,
            'weight': 0,
            'factors': {},
            'status': 'unavailable',
            'reason': '无消息面数据（请先执行消息面采集）',
        }

    # 4. 权重归一化
    raw_weights = _get_raw_weights(market)
    normalized_weights, was_rescaled = _normalize_weights(raw_weights, available_dims)

    # 填充归一化后的权重到各维度
    for dim_key in dimensions:
        if dim_key in normalized_weights:
            dimensions[dim_key]['weight'] = normalized_weights[dim_key]
        else:
            dimensions[dim_key]['weight'] = 0.0

    # 5. 计算综合得分
    total_score = 0.0
    for dim_key, dim_info in dimensions.items():
        total_score += dim_info['score'] * dim_info['weight']

    total_score = round(total_score, 1)

    # 6. 评级映射
    rating, rating_label = _map_rating(total_score)

    # 7. 组装结果
    rating_time = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M')
    score_date = rating_time[:10]
    operation_suggestion = _generate_operation_suggestion(rating, dimensions)

    result = {
        'success': True,
        'stock_code': symbol,
        'stock_name': name,
        'market': market,
        'score_date': score_date,
        'rating_time': rating_time,
        'operation_suggestion': operation_suggestion,
        'news_summary': news_summary,
        'total_score': total_score,
        'rating': rating,
        'rating_label': rating_label,
        'dimensions': dimensions,
        'data_cutoff': data_cutoffs,
        'weight_rescaled': was_rescaled,
        'active_dimensions': list(available_dims),
    }

    logger.info(
        f'[{symbol}] 分析完成: 总分={total_score}, 评级={rating}, 活跃维度={list(available_dims)}'
    )

    # 8. 写入数据库
    _save_analysis_result(stock_id, result)

    return result


def _generate_operation_suggestion(rating, dimensions):
    """
    根据四维得分+评级自动生成一句话操作建议（严禁硬编码）。
    逻辑：提取最强/最弱维度亮点 + 评级结论，拼接为一句话。
    """
    # 收集可用维度
    active = []
    for dk in ['kline', 'fundamental', 'capital_flow', 'news']:
        d = dimensions.get(dk, {})
        if d.get('status') == 'ok':
            active.append((dk, d.get('score', 0), d))

    if not active:
        return '数据不足，暂无法给出建议'

    active.sort(key=lambda x: x[1], reverse=True)
    strongest_dim = active[0]
    weakest_dim = active[-1]

    # 提取亮点关键词
    highlights = []
    factors_s = strongest_dim[2].get('factors', {})
    if strongest_dim[0] == 'capital_flow':
        trend = factors_s.get('main_trend', '')
        if '流入' in trend:
            highlights.append('资金面强劲')
        elif '流出' in trend:
            highlights.append('资金面偏弱')
    elif strongest_dim[0] == 'news':
        sentiment = factors_s.get('avg_sentiment', '')
        if '正面' in str(sentiment):
            highlights.append('消息面利好')
        elif '负面' in str(sentiment):
            highlights.append('消息面偏空')
    elif strongest_dim[0] == 'kline':
        ma = factors_s.get('ma_trend', '')
        if '多头' in ma:
            highlights.append('技术面多头')
        elif '空头' in ma:
            highlights.append('技术面偏弱')
    elif strongest_dim[0] == 'fundamental':
        roe = factors_s.get('roe', '')
        if '优秀' in str(roe) or '良好' in str(roe):
            highlights.append('基本面稳健')

    # 提取风险关键词
    risks = []
    factors_w = weakest_dim[2].get('factors', {})
    if weakest_dim[0] == 'capital_flow':
        trend = factors_w.get('main_trend', '')
        if '流出' in trend:
            risks.append('资金面承压')
    elif weakest_dim[0] == 'kline':
        rsi = factors_w.get('rsi_status', '')
        if '超买' in rsi:
            risks.append('技术面超买')
        elif '超卖' in rsi:
            risks.append('技术面超卖')

    # 组装
    parts = []
    if highlights:
        parts.append('+'.join(highlights[:2]))
    if risks:
        parts.append('但' + risks[0])

    # 评级结论
    # RATING-ALIGN-004：操作建议对齐中文5档
    rating_action = {
        '强烈推荐买入': '短线可重点关注',
        '推荐买入': '逢低可考虑布局',
        '持有观望': '持有观望为主',
        '建议减仓': '谨慎参与或减仓',
        '强烈建议卖出': '建议回避或止损',
    }
    action_text = rating_action.get(rating, '观望')

    if parts:
        return '，'.join(parts) + '，' + action_text
    else:
        return action_text


def _save_analysis_result(stock_id, result):
    """将分析结果保存到 analysis_results 表"""
    try:
        conn = get_connection()
        cursor = conn.cursor()

        dims = result['dimensions']

        # 收集数据警告
        warnings = []
        for dim_key, dim_info in dims.items():
            status = dim_info.get('status', '')
            if status != 'ok':
                warnings.append(
                    {'dimension': dim_key, 'status': status, 'reason': dim_info.get('reason', '')}
                )

        cursor.execute(
            """
            INSERT OR REPLACE INTO analysis_results
            (stock_id, analysis_date,
             fundamental_score, technical_score, sentiment_score, capital_score,
             fundamental_weight, technical_weight, sentiment_weight, capital_weight,
             total_score, rating, data_warnings,
             rating_time, operation_suggestion)
            VALUES (?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?)
        """,
            (
                stock_id,
                result['score_date'],
                dims.get('fundamental', {}).get('score', 0),
                dims.get('kline', {}).get('score', 0),
                dims.get('news', {}).get('score', 0),
                dims.get('capital_flow', {}).get('score', 0),
                dims.get('fundamental', {}).get('weight', 0),
                dims.get('kline', {}).get('weight', 0),
                dims.get('news', {}).get('weight', 0),
                dims.get('capital_flow', {}).get('weight', 0),
                result['total_score'],
                result['rating'],
                json.dumps(warnings, ensure_ascii=False),
                result.get('rating_time', ''),
                result.get('operation_suggestion', ''),
            ),
        )

        conn.commit()
        conn.close()
        logger.info(f'stock_id={stock_id} 分析结果已写入数据库')
    except Exception as e:
        logger.error(f'保存分析结果失败: {e}')


# ============================================================
# 七、命令行入口
# ============================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='四维分析引擎 - 命令行模式')
    parser.add_argument('stock_id', type=int, help='股票ID')
    args = parser.parse_args()

    result = analyze_stock(args.stock_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
