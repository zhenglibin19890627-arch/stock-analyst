#!/usr/bin/env python3
"""021BW O5①：raw_kline.turnover 历史断供段回补脚本（东财日K换手率旁路）。

=====================================================================
背景（021BW t1 检验发现 → t2 修复）
=====================================================================
raw_kline.turnover 自基线起被 fetch_kline 硬编码 0（腾讯 fqkline 与 mootdx
日K接口均不返回该字段；全库 25,541 行实测无一非零）——t1 情绪检验因此
判定「换手率代理不可检验」。采集写回已于 021BW 修复（换手率旁路 +
既有真值只升不降）；本脚本对**历史断供段**做一次性回补：

  - 数据源：东财日K push2his kline/get（fields2 末位 f61=换手率，%）
    ——与采集侧 fetch_kline_turnover_em 同源同函数（单一真相）；
  - 填充语义：只填缺失（turnover IS NULL 或 <=0），**绝不覆盖非零真值**；
  - 备份先行：写入前调用 db_manager.backup_database（R11 精神——虽为
    UPDATE 非破坏性操作，仍按 AGENTS §9.3 数据库变更先备份）；
  - 诚实记录：逐股统计 回补行数/无法对齐行数/失败原因，退出码非 0
    仅当「全部股票失败」（部分失败属网络现实，如实入档）。

=====================================================================
用法
=====================================================================
  python scripts/backfill_kline_turnover_021bw.py            # 全量回补（先备份）
  python scripts/backfill_kline_turnover_021bw.py --dry-run  # 只盘点不写库
  python scripts/backfill_kline_turnover_021bw.py --market a_stock
  python scripts/backfill_kline_turnover_021bw.py --limit 3  # 灰度试跑

红线合规：UPDATE 仅限 raw_kline.turnover 缺失段（零 DELETE/DROP）；
R1/R2/R15 不涉及（不动采集链签名与资金面）；零 pip 新依赖；
EM 请求复用 _http_get_em 全局最小间隔（019Z）+ 单轮封顶（不退避阻塞）。
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from database import db_manager  # noqa: E402
from database.db_manager import get_connection  # noqa: E402
from modules.collector.kline import fetch_kline_turnover_em  # noqa: E402

CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')


def _date(d) -> str:
    return str(d)[:10]


def inventory(cur: sqlite3.Cursor, market: str | None) -> list[dict]:
    """待回补盘点：有K线且存在缺失换手率行的股票（含最早日期，供 lookback 计算）。"""
    sql = (
        'SELECT s.id, s.symbol, s.name, s.market, '
        'COUNT(*) AS total_rows, '
        'SUM(CASE WHEN k.turnover IS NULL OR k.turnover <= 0 THEN 1 ELSE 0 END) AS missing_rows, '
        'MIN(k.trade_date) AS min_date, MAX(k.trade_date) AS max_date '
        'FROM raw_kline k JOIN stocks s ON s.id = k.stock_id '
        'WHERE (:market IS NULL OR s.market = :market) '
        'GROUP BY s.id HAVING missing_rows > 0 ORDER BY total_rows DESC'
    )
    return [dict(r) for r in cur.execute(sql, {'market': market}).fetchall()]


def backfill_stock(stock: dict, dry_run: bool) -> dict:
    """单股回补：EM 全窗口换手率 → 只填缺失段。返回诚实统计。"""
    sid, symbol, market = stock['id'], stock['symbol'], stock['market']
    min_date = _date(stock['min_date'])
    today = datetime.now(CN_TZ).date()
    lookback = max(60, (today - datetime.strptime(min_date, '%Y-%m-%d').date()).days + 30)

    tmap = fetch_kline_turnover_em(symbol, market, lookback_days=lookback)
    out = {
        'symbol': symbol, 'name': stock['name'], 'market': market,
        'missing': stock['missing_rows'], 'filled': 0, 'em_rows': len(tmap),
        'status': 'ok', 'error': '',
    }
    if not tmap:
        out['status'] = 'failed'
        out['error'] = 'EM 换手率旁路返回空（见运行日志）'
        return out

    con = get_connection()
    try:
        cur = con.cursor()
        rows = cur.execute(
            'SELECT trade_date FROM raw_kline WHERE stock_id = ? '
            'AND (turnover IS NULL OR turnover <= 0)',
            (sid,),
        ).fetchall()
        filled = 0
        for r in rows:
            d = _date(r['trade_date'])
            tv = tmap.get(d)
            if tv is None or tv <= 0:
                continue  # EM 该日亦无值（停牌/未上市）：如实留缺失
            if not dry_run:
                cur.execute(
                    'UPDATE raw_kline SET turnover = ? '
                    'WHERE stock_id = ? AND trade_date = ? '
                    'AND (turnover IS NULL OR turnover <= 0)',
                    (float(tv), sid, d),
                )
            filled += 1
        if not dry_run:
            con.commit()
        out['filled'] = filled
    finally:
        con.close()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description='021BW 换手率历史断供回补（东财旁路）')
    parser.add_argument('--db', default=None, help='SQLite 路径（默认 config.DB_PATH）')
    parser.add_argument('--market', default=None, choices=['a_stock', 'hk_stock'])
    parser.add_argument('--limit', type=int, default=None, help='只处理前 N 只（灰度）')
    parser.add_argument('--dry-run', action='store_true', help='只盘点与试算，不写库')
    parser.add_argument('--no-backup', action='store_true',
                        help='跳过写库前备份（仅 --dry-run 下允许）')
    parser.add_argument('--pause', type=float, default=2.0,
                        help='逐股间隔秒数（防东财 WAF 频控，默认 2；实测突发 ~16 只后'
                             '进入 2~4 分钟窗口式丢弃，见 019W）')
    parser.add_argument('--retry-passes', type=int, default=2,
                        help='失败股重试轮数（含首轮共 N 轮，轮间冷却 150s；默认 2）')
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    if args.db:
        db_manager.DB_PATH = args.db  # 脚本级覆盖（与 query 脚本同款用法）

    con = get_connection()
    cur = con.cursor()
    stocks = inventory(cur, args.market)
    con.close()
    if args.limit:
        stocks = stocks[:args.limit]

    total_missing = sum(s['missing_rows'] for s in stocks)
    print(f'[盘点] 待回补股票 {len(stocks)} 只 / 缺失换手率行 {total_missing} 行'
          f'（market={args.market or "全部"}，dry_run={args.dry_run}）')

    if args.dry_run:
        for s in stocks[:20]:
            print(f"  - {s['symbol']} {s['name']}：缺失 {s['missing_rows']}"
                  f"/{s['total_rows']} 行（{_date(s['min_date'])} ~ {_date(s['max_date'])}）")
        print('[dry-run] 不写库结束')
        return 0

    if not args.no_backup:
        backup_path = db_manager.backup_database(reason='backfill_turnover_021bw')
        if not backup_path:
            print('[中止] 写库前备份失败（R11：备份失败必须中止）')
            return 2
        print(f'[备份] {backup_path}')

    import time

    results: dict[str, dict] = {}
    pending = list(stocks)
    for pass_no in range(1, max(1, args.retry_passes) + 1):
        if pass_no > 1:
            failed = [s for s in pending if results[s['symbol']]['status'] != 'ok']
            if not failed:
                break
            print(f'[冷却] 第 {pass_no} 轮前等待 150s（东财 WAF 窗口式丢弃 2~4 分钟，019W）...')
            time.sleep(150)
            pending = failed
        for i, s in enumerate(pending, 1):
            r = backfill_stock(s, dry_run=False)
            results[s['symbol']] = r
            print(f"[轮{pass_no} {i}/{len(pending)}] {r['symbol']} {r['name']}: "
                  f"回补 {r['filled']}/{r['missing']} 行"
                  f"（EM 行 {r['em_rows']}）"
                  + ('' if r['status'] == 'ok' else f" —— {r['error']}"))
            if args.pause > 0 and i < len(pending):
                time.sleep(args.pause)

    final = list(results.values())
    ok_n = sum(1 for r in final if r['status'] == 'ok')
    filled_total = sum(r['filled'] for r in final)
    print(f'[汇总] 成功 {ok_n}/{len(final)} 只，累计回补 {filled_total} 行'
          + ('；未对齐行=EM 该日无值（停牌/未上市/超窗口）或旁路失败（如实留缺失，'
             '可再次运行本脚本续补——幂等只填缺失）' if ok_n < len(final) else ''))

    # 验证：库内非零换手率行数
    con = get_connection()
    cur = con.cursor()
    nonzero = cur.execute(
        'SELECT COUNT(*) FROM raw_kline WHERE turnover IS NOT NULL AND turnover > 0'
    ).fetchone()[0]
    total = cur.execute('SELECT COUNT(*) FROM raw_kline').fetchone()[0]
    con.close()
    print(f'[验证] 全库非零换手率行：{nonzero}/{total}')
    return 0 if ok_n > 0 or not final else 1


if __name__ == '__main__':
    sys.exit(main())
