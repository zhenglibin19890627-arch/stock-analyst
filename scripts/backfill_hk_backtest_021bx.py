"""021BX t7：K2 港股回测一次性补跑（零代码操作，走生产端点同路径）。"""
import json
import sys
import urllib.request

sys.path.insert(0, r'C:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from database.db_manager import backup_database, get_connection  # noqa: E402

# 1) 备份（R11 谨慎；虽为加法计算，仍留快照）
bak = backup_database(reason='hk_backtest_backfill_021bx')
print('[备份]', bak)

# 2) 跑前行数
con = get_connection()
cur = con.cursor()
hk_before = cur.execute(
    "SELECT COUNT(*) FROM backtest_results WHERE market='hk_stock'").fetchone()[0]
hk_pending = cur.execute(
    "SELECT COUNT(*) FROM ratings_history rh JOIN stocks s ON s.id=rh.stock_id "
    "WHERE s.market='hk_stock' AND rh.rating_date >= '2026-08-25' "
    "AND rh.price_at_rating IS NOT NULL AND rh.price_at_rating > 0 "
    "AND rh.id NOT IN (SELECT rating_id FROM backtest_results WHERE rating_id IS NOT NULL)"
).fetchone()[0]
all_before = cur.execute('SELECT COUNT(*) FROM backtest_results').fetchone()[0]
con.close()
print(f'[前] hk backtest 行: {hk_before}；全表: {all_before}；近30天待补评级: {hk_pending}')

# 3) 补跑（生产端点同路径：batch_backtest(market=hk_stock, days=30, force=False)）
req = urllib.request.Request(
    'http://127.0.0.1:5000/api/backtest/rerun',
    data=json.dumps({'market': 'hk_stock', 'days': 30, 'force': False}).encode('utf-8'),
    headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=600) as r:
    payload = json.loads(r.read().decode('utf-8'))
print('[端点]', json.dumps(payload, ensure_ascii=False)[:400])

# 4) 跑后行数
con = get_connection()
cur = con.cursor()
hk_after = cur.execute(
    "SELECT COUNT(*) FROM backtest_results WHERE market='hk_stock'").fetchone()[0]
all_after = cur.execute('SELECT COUNT(*) FROM backtest_results').fetchone()[0]
latest = cur.execute(
    "SELECT MAX(rating_date) FROM backtest_results WHERE market='hk_stock'").fetchone()[0]
still_pending = cur.execute(
    "SELECT COUNT(*) FROM ratings_history rh JOIN stocks s ON s.id=rh.stock_id "
    "WHERE s.market='hk_stock' AND rh.rating_date >= '2026-08-25' "
    "AND rh.price_at_rating IS NOT NULL AND rh.price_at_rating > 0 "
    "AND rh.id NOT IN (SELECT rating_id FROM backtest_results WHERE rating_id IS NOT NULL)"
).fetchone()[0]
con.close()
print(f'[后] hk backtest 行: {hk_after}（+{hk_after - hk_before}）；全表: {all_after} '
      f'（+{all_after - all_before}）；hk 最新评级日: {latest}；近30天仍未补: {still_pending}')
