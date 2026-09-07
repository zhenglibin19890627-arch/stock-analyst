"""一次性补数脚本：补 2026-08-18 市场行情数据（行业资金流 + 指数K线）。

背景：2026-08-18 17:13 日报批次后行业资金流落库时东财全挂（连续 Connection
aborted，当日东财故障期），指数K线同步缺失 → 市场行情页 08-18 无数据。

方案：
- 行业资金流：实时排行接口只有"当前时点"，历史用东财 push2his 板块资金流
  daykline 接口（secid=90.BKxxxx），逐板块取 2026-08-18 行。
  字段映射（与实时接口 f62/f184/f66/f72/f78/f84/f3 同口径，已对照 08-17
  存量数据逐字段校准）：
    f52=主力净额 f53=小单 f54=中单 f55=大单 f56=超大单
    f57=主力净占比 f63=当日涨跌幅
  领涨股（f205）历史接口无 → 置 NULL（可接受降级，历史回看重点是净额）。
- 指数K线：直接调 index_collector.fetch_all_index_kline()（akshare 全量
  序列 INSERT OR REPLACE，幂等覆盖，自动补齐 08-18）。

用法：python scripts/backfill_industry_flow_0818.py
"""

import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('NO_PROXY', '*')
os.environ.setdefault('no_proxy', '*')

import requests

from database.db_manager import get_connection

TARGET_DATE = sys.argv[1] if len(sys.argv) > 1 else '2026-08-18'

FFLOW_URL = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
FIELDS2 = 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65'


def fetch_board_history(secid, retries=1):
    """拉单板块资金流历史（最近5行），返回 klines 列表；失败返回 None。

    secid 传完整形式（如 90.BK1201）。retries=1 快速失败（EM 慢响应日
    长 retry 会把整体拖到小时级；失败板块靠断点续跑补）。
    """
    params = {
        'lmt': '5',
        'klt': '101',
        'secid': f'90.{secid}' if not secid.startswith('90.') else secid,
        'fields1': 'f1,f2,f3,f7',
        'fields2': FIELDS2,
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
    }
    for attempt in range(retries + 1):
        try:
            r = requests.get(
                FFLOW_URL,
                params=params,
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0'},
                timeout=8,
            )
            data = (r.json() or {}).get('data') or {}
            return data.get('klines') or []
        except Exception as e:
            if attempt < retries:
                time.sleep(1.0)
                continue
            print(f'    [FAIL] {secid}: {str(e)[:80]}', flush=True)
            return None


def parse_target_row(klines):
    """从历史行中取目标日行，映射为 industry_fund_flow 字段（单位：元）。"""
    for k in klines:
        parts = k.split(',')
        if len(parts) < 14 or parts[0] != TARGET_DATE:
            continue
        try:
            return {
                'main_net': float(parts[1]),    # f52 主力净额
                'small_net': float(parts[2]),   # f53 小单
                'mid_net': float(parts[3]),     # f54 中单
                'big_net': float(parts[4]),     # f55 大单
                'super_net': float(parts[5]),   # f56 超大单
                'main_pct': float(parts[6]),    # f57 主力净占比
                'pct_change': float(parts[12]), # f63 当日涨跌幅
            }
        except (ValueError, IndexError):
            return None
    return None


def backfill_industry_flow():
    conn = get_connection()
    cur = conn.cursor()
    # 板块清单来源日：目标日前最近一个有快照的交易日
    cur.execute(
        'SELECT MAX(trade_date) d FROM industry_fund_flow WHERE trade_date < ?', (TARGET_DATE,)
    )
    base = cur.fetchone()['d']
    if not base:
        print('[行业资金流] 无可用基准日（库内无早于目标日的快照），退出')
        conn.close()
        return 0
    cur.execute(
        'SELECT code, name FROM industry_fund_flow WHERE trade_date=? ORDER BY code',
        (base,),
    )
    boards = [(r['code'], r['name']) for r in cur.fetchall()]
    # 断点续跑：跳过目标日已有的板块
    cur.execute('SELECT code FROM industry_fund_flow WHERE trade_date=?', (TARGET_DATE,))
    done = {r['code'] for r in cur.fetchall()}
    conn.close()
    boards = [(c, n) for c, n in boards if c not in done]
    print(f'[行业资金流] 板块清单来自 {base}，剩余待补 {len(boards)} 个（已补 {len(done)}），目标 {TARGET_DATE}', flush=True)

    ok, miss, empty = 0, 0, []

    def _fetch_one(item):
        """单板块：拉历史 → 解析目标行。返回 (code, name, klines, row)。"""
        code, name = item
        klines = fetch_board_history(code)
        row = parse_target_row(klines) if klines is not None else None
        return code, name, klines, row

    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=6) as pool:
        for i, (code, name, klines, row) in enumerate(pool.map(_fetch_one, boards), 1):
            if klines is None:
                miss += 1
            elif row is None:
                empty.append(name)
            else:
                conn = get_connection()
                c = conn.cursor()
                c.execute(
                    'INSERT OR REPLACE INTO industry_fund_flow '
                    '(trade_date, code, name, pct_change, main_net, main_pct, super_net, big_net, mid_net, small_net, lead_stock) '
                    'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                    (
                        TARGET_DATE, code, name, row['pct_change'], row['main_net'], row['main_pct'],
                        row['super_net'], row['big_net'], row['mid_net'], row['small_net'], None,
                    ),
                )
                conn.commit()
                conn.close()
                ok += 1
            if i % 50 == 0:
                print(f'  进度 {i}/{len(boards)}（成功{ok} 失败{miss} 无目标行{len(empty)}）', flush=True)

    print(f'[行业资金流] 完成：成功 {ok} / {len(boards)}，接口失败 {miss}，无目标日行 {len(empty)}', flush=True)
    if empty:
        print(f'  无 {TARGET_DATE} 行的板块（可能为该日新增/退市板块）: {empty[:10]}{"..." if len(empty) > 10 else ""}')
    return ok


def backfill_index_kline():
    from modules.index_collector import fetch_all_index_kline

    results = fetch_all_index_kline()
    print(f'[指数K线] 写入结果: {results}')


if __name__ == '__main__':
    print(f'===== 补 {TARGET_DATE} 市场行情数据 =====')
    n = backfill_industry_flow()
    backfill_index_kline()

    # 验证
    conn = get_connection()
    cur = conn.cursor()
    cur.execute('SELECT COUNT(*) FROM industry_fund_flow WHERE trade_date=?', (TARGET_DATE,))
    print(f'\n[验证] industry_fund_flow {TARGET_DATE}: {cur.fetchone()[0]} 行')
    cur.execute(
        'SELECT name, main_net, pct_change FROM industry_fund_flow '
        'WHERE trade_date=? ORDER BY main_net DESC LIMIT 5',
        (TARGET_DATE,),
    )
    for r in cur.fetchall():
        print(f'  {r["name"]:10s} 主力净流入 {r["main_net"] / 1e8:+.2f} 亿  涨跌 {r["pct_change"]:+.2f}%')
    cur.execute('SELECT COUNT(*) FROM index_kline WHERE trade_date=?', (TARGET_DATE,))
    print(f'[验证] index_kline {TARGET_DATE}: {cur.fetchone()[0]} 行')
    conn.close()
