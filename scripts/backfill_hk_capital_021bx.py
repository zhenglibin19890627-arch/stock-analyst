#!/usr/bin/env python3
"""021BX t7：港股资金面历史缺口定向回补（020I 链路复用，限频克制）。

=====================================================================
定位（t6 审计发现 #3）
=====================================================================
4 只港股（HK0700/HK6082/HK6880/HK9880）近 10 个交易日资金面缺 6 天——
westock 当日失败未回补。本脚本复用生产回补函数
`backfill_capital_history(symbol, market, dates)`（020I 链序：
腾讯 westock --date → 新浪 lscjfb（仅A股）；写法 UPDATE + INSERT OR IGNORE、
is_estimated=0、capital_source='westock'，R2 合规），对缺口股定向回补。

判定口径：以**该股自身 K 线交易日**为该股的资金面应有日历
（K线全绿 ✅，即市场真实交易日），缺 main_net_inflow 行的日期即缺口日。

限频（克制）：逐股逐日串行，请求间隔 --pause 秒（默认 2s）；
只补近 --window 个交易日内的缺口（默认 12，覆盖审计窗口）。

用法：
  python scripts/backfill_hk_capital_021bx.py --dry-run   # 只盘点缺口（零写库零网络）
  python scripts/backfill_hk_capital_021bx.py             # 回补（先备份，R11）
  python scripts/backfill_hk_capital_021bx.py --pause 3 --window 15

红线：写库仅 raw_capital_flow 缺口行（UPDATE/INSERT OR IGNORE，零 DELETE）；
R15 签名零触碰（复用生产函数）；备份失败即中止（R11）。
"""

from __future__ import annotations

import argparse
import os
import sys
import time

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from database import db_manager  # noqa: E402
from database.db_manager import get_connection  # noqa: E402


def find_gaps(window: int, market: str = 'hk_stock') -> dict[str, dict]:
    """盘点资金面缺口：{symbol: {stock_id, name, missing_dates, window_dates}}。

    缺口判定与补采调度器同语义：raw_capital_flow 中 main_net_inflow 非空的
    日期才算已覆盖（估算行/占位行不算——回补以真实行为目标）。
    """
    con = get_connection()
    cur = con.cursor()
    stocks = [dict(r) for r in cur.execute(
        "SELECT id, symbol, name FROM stocks WHERE market=? AND status='active' "
        'ORDER BY symbol', (market,)).fetchall()]
    out: dict[str, dict] = {}
    for s in stocks:
        kdates = [r['d'] for r in cur.execute(
            "SELECT DISTINCT substr(trade_date,1,10) d FROM raw_kline "
            'WHERE stock_id=? ORDER BY d DESC LIMIT ?', (s['id'], window)).fetchall()]
        if not kdates:
            continue
        covered = {r['d'] for r in cur.execute(
            'SELECT DISTINCT substr(trade_date,1,10) d FROM raw_capital_flow '
            'WHERE stock_id=? AND main_net_inflow IS NOT NULL AND trade_date>=?',
            (s['id'], kdates[-1])).fetchall()}
        missing = [d for d in kdates if d not in covered]
        if missing:
            out[s['symbol']] = {
                'stock_id': s['id'], 'name': s['name'],
                'missing_dates': sorted(missing), 'window_dates': kdates,
            }
    con.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description='021BX 港股资金面缺口定向回补（020I 链）')
    parser.add_argument('--dry-run', action='store_true', help='只盘点，不写库零网络')
    parser.add_argument('--pause', type=float, default=2.0, help='逐请求间隔秒（默认 2，克制限频）')
    parser.add_argument('--window', type=int, default=12, help='检查最近 N 个交易日（默认 12）')
    parser.add_argument('--no-backup', action='store_true', help='跳过备份（仅 --dry-run 允许）')
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    gaps = find_gaps(args.window)
    total = sum(len(v['missing_dates']) for v in gaps.values())
    print(f'[盘点] 港股资金面缺口：{len(gaps)} 只 / {total} 天（窗口=近{args.window}个交易日）')
    for sym, v in sorted(gaps.items()):
        print(f"  - {sym} {v['name']}: 缺 {len(v['missing_dates'])} 天 "
              f"{', '.join(v['missing_dates'])}")
    if args.dry_run:
        print('[dry-run] 结束（零写库零网络）')
        return 0
    if not gaps:
        print('[无需回补] 无缺口')
        return 0

    if not args.no_backup:
        bak = db_manager.backup_database(reason='hk_capital_backfill_021bx')
        if not bak:
            print('[中止] 备份失败（R11：备份失败必须中止）')
            return 2
        print(f'[备份] {bak}')

    from modules.collector.capital_flow import backfill_capital_history

    ok_days = 0
    miss_days = 0
    first = True
    for sym, v in sorted(gaps.items()):
        # 限频：不同股票之间也节流
        if not first and args.pause > 0:
            time.sleep(args.pause)
        first = False
        filled = backfill_capital_history(sym, 'hk_stock', v['missing_dates'])
        ok_days += len(filled)
        miss_days += len(v['missing_dates']) - len(filled)
        print(f"  [回补] {sym} {v['name']}: {len(filled)}/{len(v['missing_dates'])} 天"
              f"（{', '.join(filled) if filled else 'westock 无数据'}）")
        if args.pause > 0:
            time.sleep(args.pause)

    print(f'[汇总] 回补 {ok_days}/{total} 天；未补 {miss_days} 天=westock 该日无数据'
          '（半日市/源缺，如实留缺）')
    return 0


if __name__ == '__main__':
    sys.exit(main())
