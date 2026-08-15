"""消息面指标明细：为分析报告「四维评分详情·消息面」卡提供两个子项快照。

数据源：news_sentiment 最新一行 + raw_fundamental.holder_increase（调用方传入），
纯函数、无 DB/网络依赖。口径与 modules/scoring_engine.py 的情绪/股东行为子项对齐。
"""


def compute_news_detail(news_row, holder_increase):
    """news_row: news_sentiment 行 dict（可 None）；holder_increase: bool|None → 明细 dict。"""
    if not news_row and holder_increase is None:
        return None

    d = {}

    # 1) 情绪（权重 0.70）：news_sentiment(-1~+1) + 新闻量/正面占比
    if news_row:
        d['news_date'] = str(news_row.get('news_date'))[:10]
        avg = news_row.get('avg_sentiment')
        if avg is not None:
            d['avg_sentiment'] = round(avg, 2)
            if avg > 0.3:
                d['sentiment_state'] = '显著正面'
            elif avg > 0.1:
                d['sentiment_state'] = '偏正面'
            elif avg < -0.3:
                d['sentiment_state'] = '显著负面'
            elif avg < -0.1:
                d['sentiment_state'] = '偏负面'
            else:
                d['sentiment_state'] = '中性'
        total = news_row.get('total_count')
        pos = news_row.get('positive_count')
        neg = news_row.get('negative_count')
        if total is not None:
            d['total_count'] = int(total)
        if pos is not None:
            d['positive_count'] = int(pos)
            if total and total > 0:
                d['positive_ratio'] = round(pos / total * 100, 1)
        if neg is not None:
            d['negative_count'] = int(neg)
        if news_row.get('top_news_title'):
            d['top_news'] = str(news_row['top_news_title'])

    # 2) 股东行为（权重 0.30）：大股东/高管是否增持
    if holder_increase is not None:
        d['holder'] = bool(holder_increase)

    return d
