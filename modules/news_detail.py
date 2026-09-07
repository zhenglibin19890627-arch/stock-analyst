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


# ============================================================
# 新闻去重（021AE 自 modules/analysis_engine.py 迁入，
# 唯一消费方 blueprints/watchlist.py 的个股新闻卡）
# ============================================================


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
