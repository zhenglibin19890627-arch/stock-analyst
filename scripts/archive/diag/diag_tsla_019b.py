"""019B 诊断：查询宁德时代(300750) 成功采集记录及 raw_capital_flow 历史 main_net_inflow 分布。只读。"""
import sys

sys.path.insert(0, r'c:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
from database.db_manager import get_connection

conn = get_connection()
c = conn.cursor()

print('=== 宁德时代(300750) data_status 全部记录 ===')
c.execute("""
    SELECT d.fetched_at, d.status, d.message
    FROM data_status d
    JOIN stocks s ON s.id=d.stock_id
    WHERE s.symbol='300750' AND d.dimension='capital'
    ORDER BY d.fetched_at DESC LIMIT 10
""")
for r in c.fetchall():
    print(dict(r))

print()
print('=== 宁德时代 raw_capital_flow 最近 12 个交易日 ===')
c.execute("""
    SELECT r.trade_date, r.main_net_inflow, r.main_net_inflow_pct, r.ths_net_inflow
    FROM raw_capital_flow r
    JOIN stocks s ON s.id=r.stock_id
    WHERE s.symbol='300750'
    ORDER BY r.trade_date DESC LIMIT 12
""")
for r in c.fetchall():
    print(dict(r))

print()
print('=== 各股票 main_net_inflow 最近有值的日期 ===')
c.execute("""
    SELECT s.symbol, s.name, MAX(r.trade_date) AS last_main_date
    FROM raw_capital_flow r JOIN stocks s ON s.id=r.stock_id
    WHERE r.main_net_inflow IS NOT NULL
    GROUP BY r.stock_id
    ORDER BY last_main_date DESC
""")
for r in c.fetchall():
    print(dict(r))

conn.close()
