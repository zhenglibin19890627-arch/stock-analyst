#!/usr/bin/env python3
"""021CC 共振分布监控脚本（只读·零网络·零写库）——共振档位触发数 + 健康修正来源分布。

背景（docs/reports/021cc_indicator_phase_star_plan_20261010.md §5「分布监控」）：
021CC 重构前 5★ 占有共振条目 100%（星级通胀）。本脚本对自选股库内 K 线做离线
复算快照，输出每档共振（5★/4★/3★/2★/无共振）触发数与健康修正三来源分布，
供 021CD 校准与「防星级通胀回归」日常巡检使用。

用法（在项目根目录执行；自带 sys.path 注入）：
    python scripts/resonance_stats_021cc.py           # 控制台表格
    python scripts/resonance_stats_021cc.py --json    # 追加机器可读 JSON（stdout）
    python scripts/resonance_stats_021cc.py --help

口径：
  1. 复用 modules/market_screener._read_watchlist_klines（raw_kline / raw_kline_weekly
     只读读取）+ compute_watchlist_signal_result（信号+共振纯函数复算），与行动清单 /
     巡检端点同一条判定链路——监控数字即用户所见数字；
  2. 档位计数为**条目口径**：叠加不折叠（021CC T3）后每只股票可命中多组合，各条目
     按自身星级分别计入。行动清单 stats 的 '3' 桶按「3★及以下」口径收纳健康修正降档
     的 2★；本脚本将 2★ 单独计档，便于观察健康修正触发面（另附个股口径：主组合星级）；
  3. 健康修正（021CC §3.4，只减不加，下限 2★）在共振条目注记中以
     「健康修正-N（原因…）」体现，本脚本直接重算 market_screener._buy_health_deductions
     得到同值的三来源分布（RSI超买追高 / 震荡骗线频繁 / 量能未确认，各 -1），
     统计总体为「有共振条目的股票」（无共振股票的修正不作用于任何星级，不计入）；
  4. 只读：全程 SELECT，不 commit 不建表不触网；库文件缺失时直接报错退出（不代建，
     避免隐式创建空库违背零写库约束）。

退出码：0 = 快照完成；2 = 库文件缺失等致命错误。
"""

import argparse
import json
import os
import sys
from datetime import datetime

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from modules.market_screener import (  # noqa: E402
    _buy_health_deductions,
    _read_watchlist_klines,
    compute_watchlist_signal_result,
)


def _db_path():
    """库路径经 facade 动态取值——与 get_connection 同源（021BO monkeypatch 语义保持）。"""
    from database.db_manager import DB_PATH

    return DB_PATH

# 档位桶（021CC 后条目星级取值域：组合基础 3~5★ − 健康修正，下限 2★）
STAR_BUCKETS = ('5', '4', '3', '2')
# 健康修正三来源（与 market_screener._buy_health_deductions 的 mods 文案逐一对应）
HEALTH_SOURCE_LABELS = ('RSI超买追高', '震荡骗线频繁', '量能未确认')


def collect_rows():
    """遍历全部 active 自选股，只读复算信号+共振（零网络零写库）。

    Returns: {
        'stocks_total': active 自选股数,
        'rows': [每股一条：{stock_id, symbol, name, kline_count, kline_upto,
                            matches_n, combos: [{key, label, stars}],
                            health_ded, health_mods}]（combos 为空 = 无共振）,
        'errors': [{stock_id, error}]（单只失败不阻塞，与巡检同型）,
    }
    """
    from database.db_manager import get_connection  # 调用时经 facade 取值（monkeypatch 友好）

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id, symbol, name FROM stocks WHERE status='active' ORDER BY id")
        stocks = [dict(r) for r in cursor.fetchall()]
        rows, errors = [], []
        for s in stocks:
            try:
                daily, weekly = _read_watchlist_klines(cursor, s['id'])
                item = compute_watchlist_signal_result(daily, weekly)
                ded, mods = 0, []
                if item['resonances']:
                    # 与 detect_resonances 内部同输入重算：来源分布与注记逐值一致
                    ded, mods = _buy_health_deductions(daily)
                rows.append({
                    'stock_id': s['id'],
                    'symbol': s['symbol'] or '',
                    'name': s['name'] or '',
                    'kline_count': item['kline_count'],
                    'kline_upto': item['kline_upto'],
                    'matches_n': len(item['matches']),
                    'combos': [{'key': r['key'], 'label': r['label'], 'stars': r['stars']}
                               for r in item['resonances']],
                    'health_ded': ded,
                    'health_mods': list(mods),
                })
            except Exception as e:  # noqa: BLE001 —— 单只失败不阻塞（与巡检同型）
                errors.append({'stock_id': s['id'], 'error': str(e)[:120]})
        return {'stocks_total': len(stocks), 'rows': rows, 'errors': errors}
    finally:
        conn.close()


def aggregate(collected):
    """把逐股明细聚合为分布快照（纯函数，便于单测）。"""
    rows = collected['rows']
    star_dist = {b: 0 for b in STAR_BUCKETS}            # 条目口径：逐共振条目计入
    stock_star_dist = {b: 0 for b in STAR_BUCKETS}      # 个股口径：主组合（首条目）星级
    combo_stars = {}                                    # 组合 × 星级：{key: {label, '4': n, ...}}
    health_sources = {label: 0 for label in HEALTH_SOURCE_LABELS}
    health_ded_dist = {}                                # 扣减档分布：{'1': n, '2': n, ...}
    stocks_with_resonance = 0
    stocks_health_flagged = 0
    stocks_with_kline = 0
    stocks_insufficient = 0
    entries_total = 0
    latest_upto = None

    for r in rows:
        if r['kline_count'] > 0:
            stocks_with_kline += 1
        if 0 < r['kline_count'] < 35:
            stocks_insufficient += 1                    # detect_* 门槛：日K不足 35 根不可判
        if r['kline_upto'] and (latest_upto is None or r['kline_upto'] > latest_upto):
            latest_upto = r['kline_upto']
        if not r['combos']:
            continue
        stocks_with_resonance += 1
        entries_total += len(r['combos'])
        first = True
        for c in r['combos']:
            bucket = str(c['stars'])
            star_dist[bucket] = star_dist.get(bucket, 0) + 1
            per_combo = combo_stars.setdefault(c['key'], {'label': c['label']})
            per_combo[bucket] = per_combo.get(bucket, 0) + 1
            if first:
                stock_star_dist[bucket] = stock_star_dist.get(bucket, 0) + 1
                first = False
        if r['health_ded'] > 0:
            stocks_health_flagged += 1
            key = str(r['health_ded'])
            health_ded_dist[key] = health_ded_dist.get(key, 0) + 1
            for m in r['health_mods']:
                if m in health_sources:
                    health_sources[m] += 1

    stocks_ok = len(rows)
    return {
        'scope': 'watchlist_offline_stats',
        'generated_at': datetime.now().isoformat(timespec='seconds'),
        'db_path': os.path.basename(_db_path()),
        'stocks_total': collected['stocks_total'],
        'stocks_ok': stocks_ok,
        'stocks_with_kline': stocks_with_kline,
        'stocks_insufficient_kline': stocks_insufficient,
        'error_count': len(collected['errors']),
        'kline_upto_latest': latest_upto,
        'resonance_entries_total': entries_total,
        'stocks_with_resonance': stocks_with_resonance,
        'star_dist': star_dist,                     # 条目口径（2★ 单独计档）
        'stock_star_dist': stock_star_dist,         # 个股口径（主组合星级）
        'res_combo_stars': combo_stars,             # 021CD 分档回测的种子维度
        'health': {
            'stocks_flagged': stocks_health_flagged,
            'ded_dist': dict(sorted(health_ded_dist.items())),
            'sources': health_sources,
        },
        'errors': collected['errors'],
        'per_stock': [
            {k: r[k] for k in ('stock_id', 'symbol', 'name', 'kline_count', 'kline_upto',
                               'matches_n', 'combos', 'health_ded', 'health_mods')}
            for r in rows if r['combos']
        ],
    }


def _pct(part, whole):
    """百分比字符串（总体为 0 时返回 '-'，不除零）。"""
    return f'{part / whole * 100:.1f}%' if whole else '-'


def render_console(stats):
    """快照 → 控制台表格文本（中文、无裸 '<'）。"""
    lines = []
    bar = '=' * 64
    lines.append(bar)
    lines.append(' 021CC 共振分布监控（只读离线快照 · 零网络零写库）')
    lines.append(bar)
    lines.append(f"库文件：{stats['db_path']} ｜ 快照时刻：{stats['generated_at']}")
    lines.append(
        f"自选股 active：{stats['stocks_total']} 只 ｜ 有日K：{stats['stocks_with_kline']} 只"
        f" ｜ 日K不足35根（不可判）：{stats['stocks_insufficient_kline']} 只"
        f" ｜ 读取失败：{stats['error_count']} 只")
    lines.append(f"日K最新截至：{stats['kline_upto_latest'] or '—'}")
    lines.append('')

    lines.append('—— 共振档位分布（条目口径：叠加多组合逐条计入；2★=健康修正降档单独计档）——')
    entries = stats['resonance_entries_total']
    if entries:
        for b in STAR_BUCKETS:
            n = stats['star_dist'][b]
            tag = '（健康修正后）' if b == '2' else ''
            lines.append(f'  {b}★ {tag:<8} {n:>4}   ({_pct(n, entries)})')
    else:
        lines.append('  （本批无共振条目）')
    lines.append(f'  条目合计 {entries}'
                 f' ｜ 无共振股票 {stats["stocks_total"] - stats["stocks_with_resonance"]} 只'
                 f'（{_pct(stats["stocks_total"] - stats["stocks_with_resonance"], stats["stocks_total"])}）')
    lines.append('  个股口径（主组合星级）：'
                 + ' ｜ '.join(f'{b}★ {stats["stock_star_dist"][b]}' for b in STAR_BUCKETS))
    lines.append('')

    lines.append('—— 组合 × 星级 ——')
    if stats['res_combo_stars']:
        for key, info in stats['res_combo_stars'].items():
            stars = '  '.join(f'{b}★×{info[b]}' for b in STAR_BUCKETS if info.get(b))
            lines.append(f'  {info["label"]:<10} {stars}')
    else:
        lines.append('  （无）')
    lines.append('')

    h = stats['health']
    lines.append('—— 健康修正来源分布（021CC §3.4：只减不加，各 -1，下限 2★）——')
    lines.append(f'  触发股票：{h["stocks_flagged"]}/{stats["stocks_total"]}'
                 f'（{_pct(h["stocks_flagged"], stats["stocks_total"])}）')
    if h['ded_dist']:
        ded_txt = '  '.join(f'-{k}×{v}' for k, v in h['ded_dist'].items())
        lines.append(f'  扣减档分布：{ded_txt}')
    for label in HEALTH_SOURCE_LABELS:
        lines.append(f'  {label}  {h["sources"][label]:>4}')
    lines.append(bar)
    return '\n'.join(lines)


def main(argv=None):
    # 重定向/管道场景下 Windows 默认走本地码页（GBK），中文与 JSON 会乱码——统一 UTF-8
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, 'reconfigure'):
            stream.reconfigure(encoding='utf-8', errors='replace')

    parser = argparse.ArgumentParser(
        description='021CC 共振分布监控：每档共振触发数 + 健康修正来源分布（只读离线）')
    parser.add_argument('--json', action='store_true', help='追加机器可读 JSON 输出（stdout）')
    args = parser.parse_args(argv)

    db_path = _db_path()
    if not os.path.exists(db_path):
        print(f'[致命] 库文件不存在：{db_path}（零写库约束：不代建空库，请先运行 python app.py 初始化）',
              file=sys.stderr)
        return 2

    stats = aggregate(collect_rows())
    print(render_console(stats))
    if args.json:
        print(json.dumps(stats, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    sys.exit(main())
