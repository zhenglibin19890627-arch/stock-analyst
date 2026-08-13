"""019B 诊断：查询 data_status 与 raw_capital_flow 现状。只读，不改数据。"""
import sys
sys.path.insert(0, r'c:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
from database.db_manager import get_connection

conn = get_connection()
c = conn.cursor()

print('=== data_status 最近 15 条 capital ===')
c.execute("SELECT stock_id, dimension, status, message, fetched_at FROM data_status WHERE dimension='capital' ORDER BY fetched_at DESC LIMIT 15")
for r in c.fetchall():
    print(dict(r))

print()
print('=== raw_capital_flow 最近日 main_net_inflow 非空统计 ===')
c.execute("""
    SELECT trade_date,
           COUNT(*) AS total,
           SUM(CASE WHEN main_net_inflow IS NOT NULL THEN 1 ELSE 0 END) AS has_main,
           SUM(CASE WHEN ths_net_inflow IS NOT NULL THEN 1 ELSE 0 END) AS has_ths
    FROM raw_capital_flow
    GROUP BY trade_date ORDER BY trade_date DESC LIMIT 8
""")
for r in c.fetchall():
    print(dict(r))

print()
print('=== raw_capital_flow 最新交易日样例 ===')
c.execute("""
    SELECT r.stock_id, s.symbol, s.name, r.trade_date, r.main_net_inflow, r.ths_net_inflow
    FROM raw_capital_flow r
    LEFT JOIN stocks s ON s.id = r.stock_id
    WHERE r.trade_date = (SELECT MAX(trade_date) FROM raw_capital_flow)
    ORDER BY r.stock_id
""")
for r in c.fetchall():
    print(dict(r))

conn.close()