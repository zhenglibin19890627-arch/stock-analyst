"""019B 验证：确认贵州茅台(600519)东财数据恢复后数据库写入情况。只读。"""
import sys
sys.path.insert(0, r'c:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
from modules.data_collector import get_connection, get_stock_id

stock_id = get_stock_id('600519', 'a_stock')
print(f'stock_id = {stock_id}')

conn = get_connection()
cur = conn.cursor()

# 今日 raw_capital_flow
cur.execute(
    "SELECT trade_date, main_net_inflow, main_net_inflow_pct, super_large_net, large_net, medium_net, small_net "
    "FROM raw_capital_flow WHERE stock_id=? AND trade_date='2026-08-03'",
    (stock_id,),
)
print('\n[2026-08-03 最新交易日数据]')
for r in cur.fetchall():
    print(dict(r))

# 最近5条
cur.execute(
    "SELECT trade_date, main_net_inflow FROM raw_capital_flow WHERE stock_id=? ORDER BY trade_date DESC LIMIT 5",
    (stock_id,),
)
print('\n[最近5条 main_net_inflow]')
for r in cur.fetchall():
    print(dict(r))

# 总数
cur.execute("SELECT COUNT(*) AS c FROM raw_capital_flow WHERE stock_id=?", (stock_id,))
print(f'\n[total rows] {cur.fetchone()["c"]}')

# data_status 最新 capital 记录
cur.execute(
    "SELECT stock_id, dimension, status, message, fetched_at FROM data_status "
    "WHERE stock_id=? AND dimension='capital' ORDER BY fetched_at DESC LIMIT 3",
    (stock_id,),
)
print('\n[data_status 最新3条]')
for r in cur.fetchall():
    print(dict(r))

conn.close()
print('\n验证完成')