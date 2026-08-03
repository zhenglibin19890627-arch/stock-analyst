"""
模块4：消息面数据采集与情绪分析

数据源: akshare stock_news_em (东方财富个股新闻)
情绪模型: SnowNLP(40%) + 金融关键词字典(60%)
输出: raw_sentiment(逐条) + news_sentiment(日聚合)

核心函数:
1. collect_news(stock_id, symbol, market) — 主入口
2. _analyze_sentiment(text) — 混合情绪分析
3. _aggregate_daily(news_items) — 聚合为日情绪
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import get_connection

from modules.sentiment_dict import get_sentiment_score as _keyword_score

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

# SnowNLP 延迟导入（首次使用时加载模型）
_SnowNLP = None


def _get_snownlp():
    global _SnowNLP
    if _SnowNLP is None:
        try:
            from snownlp import SnowNLP

            _SnowNLP = SnowNLP
            logger.info('SnowNLP 模型加载成功')
        except ImportError:
            logger.warning('SnowNLP 未安装，情绪分析将仅使用关键词匹配')
            _SnowNLP = False
    return _SnowNLP


# ============================================================
# 一、数据采集
# ============================================================


def _convert_symbol_for_akshare(symbol, market):
    """
    将内部symbol转换为akshare所需格式。
    A股: 600276 → '600276' (不变)
    港股: HK3690 → '03690' (strip HK前缀, 前补0至5位)
    """
    if market == 'hk_stock':
        code = symbol.replace('HK', '').replace('hk', '')
        return code.zfill(5)
    return symbol


def _fetch_news_from_akshare(symbol, market):
    """
    调用akshare获取个股新闻。
    返回 DataFrame 或 None。
    """
    import akshare as ak

    ak_symbol = _convert_symbol_for_akshare(symbol, market)
    logger.info(f'[{symbol}] akshare stock_news_em(symbol={ak_symbol})')

    df = ak.stock_news_em(symbol=ak_symbol)
    if df is None or df.empty:
        logger.warning(f'[{symbol}] akshare 返回空数据')
        return None

    return df


# ============================================================
# 二、情绪分析
# ============================================================


def _analyze_sentiment(text):
    """
    混合情绪分析: SnowNLP(40%) + 金融关键词(60%)
    返回 -1.0 ~ +1.0 的情绪得分。
    """
    if not text or not isinstance(text, str) or len(text.strip()) == 0:
        return 0.0

    # 1. 金融关键词匹配 (权重 60%)
    kw_score = _keyword_score(text)

    # 2. SnowNLP (权重 40%)
    SnowNLP_cls = _get_snownlp()
    if SnowNLP_cls:
        try:
            # SnowNLP输出 0~1，映射到 -1~+1
            snownlp_raw = SnowNLP_cls(text).sentiments
            snownlp_score = snownlp_raw * 2 - 1
        except Exception as e:
            logger.debug(f'SnowNLP分析异常: {e}，仅使用关键词')
            snownlp_score = 0.0
            # SnowNLP失败时，关键词权重提升到100%
            return max(-1.0, min(1.0, kw_score))
    else:
        # SnowNLP不可用时，100%用关键词
        return max(-1.0, min(1.0, kw_score))

    # 3. 加权融合
    final = 0.4 * snownlp_score + 0.6 * kw_score
    return max(-1.0, min(1.0, final))


# ============================================================
# 三、数据库写入
# ============================================================


def _save_raw_sentiment(stock_id, news_items):
    """逐条新闻写入 raw_sentiment 表"""
    conn = get_connection()
    cursor = conn.cursor()

    saved = 0
    for item in news_items:
        try:
            title = item.get('title', '')
            content = item.get('content', '')
            date_str = item.get('date', '')
            source = item.get('source', '')
            url = item.get('url', '')
            sentiment = item.get('sentiment', 0.0)

            # 写入 raw_sentiment（info_type='news'）
            cursor.execute(
                """
                INSERT OR REPLACE INTO raw_sentiment
                (stock_id, info_type, title, content, sentiment_score, info_date, source)
                VALUES (?, 'news', ?, ?, ?, ?, ?)
            """,
                (
                    stock_id,
                    title[:500],
                    content[:1000],
                    round(sentiment, 4),
                    date_str[:10] if date_str else None,
                    f'{source}|{url}' if url else source,
                ),
            )
            saved += 1
        except Exception as e:
            logger.debug(f'写入raw_sentiment失败: {e}')

    conn.commit()
    conn.close()
    return saved


def _save_news_sentiment(stock_id, news_items):
    """聚合写入 news_sentiment 表（按最新日期聚合）"""
    if not news_items:
        return None

    today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')

    # 聚合统计
    total = len(news_items)
    sentiments = [item.get('sentiment', 0.0) for item in news_items]
    avg_sentiment = sum(sentiments) / total if total > 0 else 0.0

    positive_count = sum(1 for s in sentiments if s > 0.1)
    negative_count = sum(1 for s in sentiments if s < -0.1)
    neutral_count = total - positive_count - negative_count

    # 选取情绪最强烈的新闻作为top_news
    top_item = max(news_items, key=lambda x: abs(x.get('sentiment', 0)))
    top_title = top_item.get('title', '')

    # 收集URL列表
    urls = [item.get('url', '') for item in news_items if item.get('url')]
    urls_json = json.dumps(urls[:5], ensure_ascii=False)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute(
        """
        INSERT OR REPLACE INTO news_sentiment
        (stock_id, news_date, avg_sentiment, positive_count, negative_count,
         neutral_count, total_count, top_news_title, source_urls)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            stock_id,
            today,
            round(avg_sentiment, 4),
            positive_count,
            negative_count,
            neutral_count,
            total,
            top_title[:500],
            urls_json,
        ),
    )

    conn.commit()
    conn.close()

    logger.info(
        f'stock_id={stock_id} news_sentiment已写入: '
        f'date={today}, avg={avg_sentiment:.4f}, '
        f'pos={positive_count}/neg={negative_count}/neu={neutral_count}'
    )

    return {
        'news_date': today,
        'avg_sentiment': round(avg_sentiment, 4),
        'positive_count': positive_count,
        'negative_count': negative_count,
        'neutral_count': neutral_count,
        'total_count': total,
        'top_news_title': top_title,
    }


def _save_error_log(stock_id, error_type, error_message):
    """写入错误日志"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO error_logs (stock_id, module, error_type, error_message)
            VALUES (?, 'news_collector', ?, ?)
        """,
            (stock_id, error_type, str(error_message)[:500]),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f'写入error_logs失败: {e}')


# ============================================================
# 四、主入口
# ============================================================


def collect_news(stock_id, symbol, market):
    """
    主入口：采集新闻 + 情绪分析 + 写入DB

    参数:
        stock_id: 数据库stock_id
        symbol: 股票代码 (如 600276 / HK3690)
        market: a_stock / hk_stock

    返回:
        tuple: (status, message)
            status: 'success' / 'partial' / 'failed'
    """
    market_name = 'A股' if market == 'a_stock' else '港股'
    logger.info(f'========== 开始采集{market_name} {symbol} 消息面 ==========')

    try:
        # 1. 获取新闻数据
        df = _fetch_news_from_akshare(symbol, market)
        if df is None or df.empty:
            msg = f'{market_name} {symbol} 消息面数据为空（akshare未返回新闻）'
            logger.warning(msg)
            _save_error_log(stock_id, 'empty_data', msg)
            return 'partial', msg

        logger.info(f'[{symbol}] akshare返回 {len(df)} 条新闻')

        # 2. 逐条情绪分析
        news_items = []
        for _, row in df.iterrows():
            title = str(row.get('新闻标题', ''))
            content = str(row.get('新闻内容', ''))
            date_str = str(row.get('发布时间', ''))
            source = str(row.get('文章来源', ''))
            url = str(row.get('新闻链接', ''))

            # 组合文本做情绪分析
            full_text = f'{title} {content}'
            sentiment = _analyze_sentiment(full_text)

            news_items.append(
                {
                    'title': title,
                    'content': content,
                    'date': date_str,
                    'source': source,
                    'url': url,
                    'sentiment': sentiment,
                }
            )

        # 3. 写入 raw_sentiment (逐条)
        _save_raw_sentiment(stock_id, news_items)

        # 4. 写入 news_sentiment (日聚合)
        summary = _save_news_sentiment(stock_id, news_items)

        if summary:
            msg = (
                f'{market_name} {symbol} 消息面采集成功：'
                f'{summary["total_count"]}条新闻，'
                f'情绪得分{summary["avg_sentiment"]:.2f}，'
                f'正面{summary["positive_count"]}/'
                f'负面{summary["negative_count"]}/'
                f'中性{summary["neutral_count"]}'
            )
            logger.info(msg)
            return 'success', msg
        else:
            return 'failed', f'{symbol} 消息面聚合失败'

    except Exception as e:
        error_msg = f'{market_name} {symbol} 消息面采集异常: {str(e)}'
        logger.error(error_msg, exc_info=True)
        _save_error_log(stock_id, 'exception', str(e))
        return 'failed', error_msg


# ============================================================
# 命令行入口
# ============================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='消息面采集 - 命令行模式')
    parser.add_argument('stock_id', type=int, help='股票ID')
    parser.add_argument('symbol', type=str, help='股票代码')
    parser.add_argument('market', type=str, help='市场(a_stock/hk_stock)')
    args = parser.parse_args()

    status, msg = collect_news(args.stock_id, args.symbol, args.market)
    print(f'\n状态: {status}')
    print(f'详情: {msg}')
