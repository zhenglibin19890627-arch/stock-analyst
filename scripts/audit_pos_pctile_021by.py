#!/usr/bin/env python3
"""021BY 复审：位置分位口径同源核验（只读，可重复执行）。

====================================================================
定位（021BY t3 复审项②；方案 docs/reports/021by_market_plan_20260924.md §2 口径声明）
====================================================================
独立 SQL 抽样复算：对真实库 raw_kline（近 60 根收盘）按审计脚本内**独立实现**的
公式复算位置分位，与三处生产实现逐一比对——
  1) market_screener.position_pctile_of_klines   （021BY C1 扫描侧，round 3）
  2) backtest_engine._current_pos_pctile          （021BU 证据面/position_note_for，round 3）
  3) trader_advisor._volume_structure             （看板操盘手，round 2）
任何一处与独立基准不一致 = 审计失败（同源唯一真相被破坏）。

只读边界：sqlite3 mode=ro 连接；零写库、零网络、零 pip 新依赖。
用法：
  python scripts/audit_pos_pctile_021by.py               # 打印摘要
  python scripts/audit_pos_pctile_021by.py --out PATH    # 落盘 markdown
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import DB_PATH  # noqa: E402

SAMPLE_N = 10
LOOKBACK = 60
MIN_BARS = 40
ROUND_DIGITS = 3


def _band(pos):
    if pos is None:
        return None
    return 'low' if pos < 0.4 else ('high' if pos >= 0.7 else 'mid')


def _volume_structure_style_pos(closes):
    """trader_advisor._volume_structure 口径的独立复算（round 2，>=20 根）。

    同源细节：trader 侧 closes 过滤条件是 `close AND volume 均为真值`
    （volume=0/None 的行被剔除，窗口因此可能短于 60 根）——独立基准按同一
    过滤规则复算，确保比对对象是公式本身而非窗口选取差异。
    """
    if len(closes) < 20:
        return None
    hi, lo = max(closes), min(closes)
    if hi <= lo:
        return None
    return round((closes[-1] - lo) / (hi - lo), 2)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', default=None)
    ap.add_argument('--sample', type=int, default=SAMPLE_N)
    args = ap.parse_args()

    # 审计基准：脚本内独立实现（不 import 被测函数参与基准计算）
    def independent_pos(closes):
        if len(closes) < MIN_BARS:
            return None, None
        hi, lo, now = max(closes), min(closes), closes[-1]
        if hi <= lo:
            return None, None
        pos = round((now - lo) / (hi - lo), ROUND_DIGITS)
        return pos, _band(pos)

    from modules.backtest_engine import _current_pos_pctile
    from modules.market_screener import position_pctile_of_klines
    from modules.trader_advisor import _volume_structure

    con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    cur = con.cursor()
    stocks = cur.execute(
        'SELECT stock_id, COUNT(*) n FROM raw_kline GROUP BY stock_id '
        'HAVING n >= ? ORDER BY stock_id LIMIT ?',
        (MIN_BARS, args.sample),
    ).fetchall()

    rows = []
    failures = []
    for s in stocks:
        sid = s['stock_id']
        ks = cur.execute(
            'SELECT trade_date, close, volume FROM raw_kline WHERE stock_id = ? '
            'ORDER BY trade_date DESC LIMIT ?',
            (sid, LOOKBACK),
        ).fetchall()
        ks = list(reversed(ks))                      # 时间正序
        closes = [float(r['close']) for r in ks if r['close'] is not None]
        latest = ks[-1]['trade_date'] if ks else None
        # trader 侧窗口（复刻其过滤规则：close AND volume 均为真值；volume=0 行剔除）
        trader_closes = [float(r['close']) for r in ks
                         if r['close'] is not None and r['volume']]

        base_pos, base_band = independent_pos(closes)

        # ① 扫描侧纯函数（喂同一种格式的 K 线行——扫描器公式只用 close）
        kline_rows = [{'close': r['close'], 'volume': r['volume']} for r in ks]
        c1 = position_pctile_of_klines(kline_rows)
        # ② 回测面现算（读库路径，同 60 根窗）
        c2 = _current_pos_pctile(sid)
        # ③ 操盘手量价结构（round 2 口径，容差 = 舍入差 0.005）
        c3 = _volume_structure(kline_rows)['position_pctile']
        c3_indep = _volume_structure_style_pos(trader_closes)

        row_ok = (c1['pos_pctile'] == base_pos and c1['pos_band'] == base_band
                  and c2 == base_pos
                  and c3_indep == c3
                  and (c3 is None or abs(c3 - base_pos) <= 0.005 + 1e-9))
        if not row_ok:
            failures.append(sid)
        rows.append((sid, latest, len(closes), base_pos, base_band,
                     c1['pos_pctile'], c1['pos_band'], c2, c3, 'OK' if row_ok else 'FAIL'))
    con.close()

    now = datetime.now().isoformat(timespec='seconds')
    lines = [
        '# 021BY 复审：位置分位口径同源核验 — ' + now + '',
        '',
        f'- 数据库：`{DB_PATH}`（mode=ro 只读，零写库）',
        f'- 抽样：{len(rows)} 只（raw_kline >= {MIN_BARS} 根，按 stock_id 升序前 N）',
        f'- 独立基准公式：近 {LOOKBACK} 根收盘 (now-lo)/(hi-lo) round {ROUND_DIGITS}；'
        f'分带 low<0.4 / mid / high>=0.7；不足 {MIN_BARS} 根 None',
        '- 比对对象：① market_screener.position_pctile_of_klines（021BY C1）'
        '② backtest_engine._current_pos_pctile（021BU 证据面）'
        '③ trader_advisor._volume_structure（round 2，容差 0.005）',
        '',
        '| stock_id | 最新K线 | 根数 | 独立基准 | 分带 | ①扫描 | ①分带 | ②回测 | ③操盘手 | 判定 |',
        '|---|---|---|---|---|---|---|---|---|---|',
    ]
    for (sid, latest, n, base_pos, base_band, p1, b1, p2, p3, verdict) in rows:
        lines.append(
            f'| {sid} | {latest} | {n} | {base_pos} | {base_band or "—"} '
            f'| {p1} | {b1 or "—"} | {p2} | {p3} | {verdict} |')
    lines += ['', f'**结论：{"全部一致（同源成立）" if not failures else "不一致样本 " + str(failures)}'
              f'（{len(rows) - len(failures)}/{len(rows)} OK）**', '']
    text = '\n'.join(lines)
    print(text)
    if args.out:
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(text)
        print(f'[已写出] {args.out}')
    return 1 if failures else 0


if __name__ == '__main__':
    sys.exit(main())
