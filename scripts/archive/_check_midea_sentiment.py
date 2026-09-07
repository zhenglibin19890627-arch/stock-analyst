# -*- coding: utf-8 -*-
import sqlite3
from database.db_manager import get_connection
conn = get_connection()
cursor = conn.cursor()

# 检查美的集团 (stock_id=11) 的消息面数据
print('=== news_sentiment 表（最近5条）===')
rows = cursor.execute('''
    SELECT news_date, total_count, avg_sentiment, top_news_title
    FROM news_sentiment WHERE stock_id=11
    ORDER BY news_date DESC LIMIT 5
''').fetchall()
for r in rows:
    print(f'  {r["news_date"]} | total={r["total_count"]} | avg={r["avg_sentiment"]} | {r["top_news_title"][:30]}')

print('\n=== raw_sentiment 表（最新新闻日期）===')
row = cursor.execute('''
    SELECT MAX(info_date) as latest_date
    FROM raw_sentiment WHERE stock_id=11 AND info_type='news'
''').fetchone()
print(f'  最新新闻日期: {row["latest_date"]}')

conn.close()
