"""021BW t4：F2 回测样本覆盖缺口补跑（15 只 08-28 后新增 A股，零代码操作）。

t3 复审 F2（P2）：15 只新股各有 3~16 条评级但 0 条回测行（全部 is_change=0，
auto_trigger 仅评级变更触发）；与港股停更同根因（fill_pending_backtests 未接线）。
本脚本走生产端点原路径补跑 A股近 45 天评级（加法 UPSERT，备份先行，前后行数留证）。
"""
import json
import sys
import urllib.request

sys.path.insert(0, r'C:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from database.db_manager import backup_database, get_connection  # noqa: E402

bak = backup_database(reason='astock_backtest_backfill_021bw_t4')
print('[备份]', bak)

con = get_connection()
cur = con.cursor()
a_before = cur.execute(
    "SELECT COUNT(*) FROM backtest_results WHERE market='a_stock'").fetchone()[0]
pending = cur.execute(
    "SELECT COUNT(DISTINCT rh.stock_id) FROM ratings_history rh "
    "JOIN stocks s ON s.id=rh.stock_id WHERE s.market='a_stock' "
    'AND rh.price_at_rating IS NOT NULL AND rh.price_at_rating > 0 '
    'AND rh.id NOT IN (SELECT rating_id FROM backtest_results WHERE rating_id IS NOT NULL)'
).fetchone()[0]
pending_rows = cur.execute(
    "SELECT COUNT(*) FROM ratings_history rh "
    "JOIN stocks s ON s.id=rh.stock_id WHERE s.market='a_stock' "
    'AND rh.price_at_rating IS NOT NULL AND rh.price_at_rating > 0 '
    'AND rh.id NOT IN (SELECT rating_id FROM backtest_results WHERE rating_id IS NOT NULL)'
).fetchone()[0]
con.close()
print(f'[前] a_stock 回测行: {a_before}；待补：{pending} 只 / {pending_rows} 条评级')

req = urllib.request.Request(
    'http://127.0.0.1:5000/api/backtest/rerun',
    data=json.dumps({'market': 'a_stock', 'days': 45, 'force': False}).encode('utf-8'),
    headers={'Content-Type': 'application/json'}, method='POST')
with urllib.request.urlopen(req, timeout=900) as r:
    payload = json.loads(r.read().decode('utf-8'))
print('[端点]', json.dumps(payload, ensure_ascii=False)[:400])

con = get_connection()
cur = con.cursor()
a_after = cur.execute(
    "SELECT COUNT(*) FROM backtest_results WHERE market='a_stock'").fetchone()[0]
still = cur.execute(
    "SELECT COUNT(*) FROM ratings_history rh "
    "JOIN stocks s ON s.id=rh.stock_id WHERE s.market='a_stock' "
    'AND rh.price_at_rating IS NOT NULL AND rh.price_at_rating > 0 '
    'AND rh.id NOT IN (SELECT rating_id FROM backtest_results WHERE rating_id IS NOT NULL)'
).fetchone()[0]
covered = cur.execute(
    'SELECT COUNT(DISTINCT stock_id) FROM backtest_results WHERE market=\'a_stock\''
).fetchone()[0]
con.close()
print(f'[后] a_stock 回测行: {a_after}（+{a_after - a_before}）；仍未补评级: {still}；'
      f'回测覆盖 A股: {covered}/46 只')
