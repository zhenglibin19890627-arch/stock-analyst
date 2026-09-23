#!/usr/bin/env python3
"""021BU 证据查询脚本（只读可复用）——回测中心分档×位置×周期命中率挖掘。

=====================================================================
定位（021BU t1 证据挖掘）
=====================================================================
从回测中心真实数据（backtest_results / price_backtest_results / ratings_history）
挖掘可入分析报告的证据，并按样本量分级（诚实原则）：

  A 级  n ≥ 30   —— 可信，可全量展示（命中率 + n/m）
  B 级  20 ≤ n < 30 —— 谨慎参考，展示必须带「样本偏小」标注
  C 级  n < 20   —— 样本不足：不展示百分数，仅展示 n（宁缺毋滥）

口径同源（同源同值，一致性设计的基准面）：
  - 周期判定复用 modules.backtest_engine._judge（市场差异化 021P/021AH/021AK
    的固定周期口径：neutral_borderline=False, vol_scale=None——与
    run_fixed_period_backtest 完全一致）；
  - 位置分位/事后回撤复用 modules.backtest_engine._calc_pos_and_dd20
    （60 日分位 + 20 日回撤，只读 SELECT，不改库）；
  - 评级归一复用 modules.scoring_engine.normalize_rating（R7：不重实现
    分数→评级映射）；档位顺序复用 modules.alert_engine.RATING_ORDER；
  - 引擎分层 JOIN 用自然键 (stock_id, rating_date)（021BB 口径，
    ratings_history INSERT OR REPLACE 下 id 不稳定）。

=====================================================================
只读边界
=====================================================================
  - 数据库一律 sqlite3.connect('file:...?mode=ro', uri=True)；
  - 零写库（不调用 _ensure_columns / _backfill_pos_dd 等任何写路径）、
    零网络、零 pip 新依赖；
  - 输出为 markdown（stdout 或 --out 文件）+ 可选 --json 结构化导出。

=====================================================================
用法
=====================================================================
  python scripts/query_backtest_evidence_021bu.py                 # 全量证据打印
  python scripts/query_backtest_evidence_021bu.py --out PATH      # 同时写 markdown
  python scripts/query_backtest_evidence_021bu.py --json PATH     # 同时写 JSON
  python scripts/query_backtest_evidence_021bu.py --db PATH       # 指定库（默认 config.DB_PATH）
"""

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import DB_PATH  # noqa: E402

# ---- 同源口径函数（只读纯函数；R7 合规：复用不重实现）----
from modules.alert_engine import RATING_ORDER  # noqa: E402
from modules.backtest_engine import _calc_pos_and_dd20, _judge  # noqa: E402
from modules.scoring_engine import normalize_rating  # noqa: E402

CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

RATING_ORDER_BY_RANK = sorted(RATING_ORDER, key=lambda r: RATING_ORDER[r])  # 卖出→买入

# 位置分位分带（与 compute_market_report 的 POS_BANDS 同口径）
POS_BANDS = (
    ('low', 0.0, 0.4, '低位<40%'),
    ('mid', 0.4, 0.7, '中位40-70%'),
    ('high', 0.7, 1.01, '高位≥70%'),
)

# 固定周期列 → 展示名（backtest_results 列口径：1d=1 个交易日, 1w=5, 1m=20）
PERIODS = (
    ('1d', 'T+1'),
    ('1w', 'T+5'),
    ('1m', 'T+20'),
)

# 分级样本门槛（诚实原则；≥20/30 分层展示）
N_FULL = 30    # A 级：全展示
N_PARTIAL = 20  # B 级：带标注展示
# 分化显著门槛（与 position_note_for / 市场报告解读同口径）
N_BAND_MIN = 10
GAP_MIN_PP = 15


# ================================================================
# 工具
# ================================================================


def open_ro_db(db_path: str | None = None) -> sqlite3.Connection:
    """只读连接（mode=ro），行工厂为 sqlite3.Row。"""
    path = db_path or DB_PATH
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con


def sample_class(n: int) -> str:
    """样本量分级（诚实原则）：A≥30 全展示 / B≥20 带标注 / C<20 不展示百分数。"""
    if n >= N_FULL:
        return 'A'
    if n >= N_PARTIAL:
        return 'B'
    return 'C'


def fmt_cell(n: int, correct: int) -> str:
    """诚实展示格式：A/B 级显示「xx.x% (c/n)」，C 级只显示「样本不足 n」不显示百分数。"""
    if n <= 0:
        return '—'
    cls = sample_class(n)
    if cls == 'C':
        return f'样本不足(n={n})'
    pct = correct / n * 100
    tag = '' if cls == 'A' else '⚠偏小'
    return f'{pct:.0f}% ({correct}/{n}){tag}'


def band_of(pos: float | None) -> str | None:
    if pos is None:
        return None
    for key, lo, hi, _label in POS_BANDS:
        if lo <= pos < hi:
            return key
    return None


def load_rating_rows(cur: sqlite3.Cursor, market: str | None = None) -> list[dict]:
    """真实评级回测行（is_simulated=0），自然键 JOIN 引擎版本（021BB 口径）。"""
    sql = (
        'SELECT br.*, rh.engine_version FROM backtest_results br '
        'LEFT JOIN ratings_history rh '
        'ON rh.stock_id = br.stock_id AND rh.rating_date = br.rating_date '
        'WHERE (br.is_simulated IS NULL OR br.is_simulated = 0) '
        'AND br.rating_id IS NOT NULL AND br.rating_id != -1'
    )
    args: list = []
    if market:
        sql += ' AND br.market = ?'
        args.append(market)
    rows = [dict(r) for r in cur.execute(sql, args).fetchall()]
    # 自然键去重：同 (stock_id, rating_date) 取 max(id)（REPLACE 后旧 rating_id
    # 孤儿行在 batch_backtest 才被清理，查询面先行去重保证每股每日一行）
    best: dict[tuple, dict] = {}
    for r in rows:
        key = (r['stock_id'], r['rating_date'])
        if key not in best or r['id'] > best[key]['id']:
            best[key] = r
    return sorted(best.values(), key=lambda r: (r['rating_date'] or '', r['stock_id']))


def period_correct(row: dict, period: str) -> int | None:
    """按固定周期口径现判（与 run_fixed_period_backtest 同源：_judge 固定带）。"""
    ret = row.get(f'return_{period}')
    if ret is None:
        return None
    return _judge(row.get('rating'), ret, row.get('market') or 'a_stock')


# ================================================================
# 一、评级回测：分档 × 位置分位 × 周期 + 引擎分层
# ================================================================


def rating_period_matrix(rows: list[dict]) -> dict:
    """分档×位置×周期命中矩阵 + 总体/引擎分层。分母=可判定行（None 不计入）。"""
    matrix: dict[str, dict[str, dict[str, dict[str, int]]]] = {}
    overall: dict[str, dict[str, int]] = {p: {'n': 0, 'c': 0} for p, _ in PERIODS}
    overall['dyn'] = {'n': 0, 'c': 0}
    engine: dict[str, dict[str, dict[str, int]]] = {}

    for r in rows:
        ev = r.get('engine_version') or '未标记(历史)'
        eng = engine.setdefault(ev, {p: {'n': 0, 'c': 0} for p, _ in PERIODS})
        eng.setdefault('dyn', {'n': 0, 'c': 0})
        rating = r.get('rating') or '?'
        pos = r.get('pos_pctile')
        b = band_of(pos)
        for period, _label in PERIODS:
            verdict = period_correct(r, period)
            if verdict is None:
                continue
            overall[period]['n'] += 1
            eng[period]['n'] += 1
            if verdict == 1:
                overall[period]['c'] += 1
                eng[period]['c'] += 1
            if b and rating != '?':
                cell = matrix.setdefault(rating, {}).setdefault(b, {}).setdefault(
                    period, {'n': 0, 'c': 0})
                cell['n'] += 1
                if verdict == 1:
                    cell['c'] += 1
        dyn = r.get('dynamic_is_correct')
        if dyn in (0, 1):
            overall['dyn']['n'] += 1
            eng['dyn']['n'] += 1
            if dyn == 1:
                overall['dyn']['c'] += 1
                eng['dyn']['c'] += 1
            if b and rating != '?':
                cell = matrix.setdefault(rating, {}).setdefault(b, {}).setdefault(
                    'dyn', {'n': 0, 'c': 0})
                cell['n'] += 1
                if dyn == 1:
                    cell['c'] += 1
    return {'matrix': matrix, 'overall': overall, 'engine': engine}


def rating_stats_overall(rows: list[dict]) -> dict:
    """分档总体（T+1 主口径 + 动态）——报告侧「评级旁命中率」的候选证据。"""
    stats: dict[str, dict[str, dict[str, int]]] = {}
    for r in rows:
        rating = r.get('rating') or '?'
        s = stats.setdefault(rating, {
            '1d': {'n': 0, 'c': 0}, 'dyn': {'n': 0, 'c': 0}})
        v = period_correct(r, '1d')
        if v is not None:
            s['1d']['n'] += 1
            if v == 1:
                s['1d']['c'] += 1
        dyn = r.get('dynamic_is_correct')
        if dyn in (0, 1):
            s['dyn']['n'] += 1
            if dyn == 1:
                s['dyn']['c'] += 1
    return stats


# ================================================================
# 二、价格建议命中（price_backtest_results）
# ================================================================

PA_FIELDS = (
    ('t5_hit_buy_range', '买入区间·T+5', 'np'),
    ('t20_hit_buy_range', '买入区间·T+20', 'np'),
    ('t20_hit_target', '目标价·T+20', 'np'),
    ('t5_hit_stop_loss', '止损·T+5', 'any'),
    ('t20_hit_stop_loss', '止损·T+20', 'any'),
    ('t20_hit_take_profit', '止盈·T+20', 'hp'),
    ('t20_hit_hold', '持有区间·T+20', 'hp'),
    ('t20_hit_add', '补仓触发·T+20', 'hp'),
)


def price_advice_matrix(cur: sqlite3.Cursor, market: str | None = None) -> dict:
    """价格建议命中率：真实锚点样本（anchor 非空，无未来函数）为主口径。

    另现算每行回测日的 60 日位置分位（复用 _calc_pos_and_dd20，只读），
    得到「分档×位置」价格建议命中矩阵。
    """
    sql = 'SELECT * FROM price_backtest_results'
    args: list = []
    if market:
        sql += ' WHERE market = ?'
        args.append(market)
    rows = [dict(r) for r in cur.execute(sql, args).fetchall()]
    for r in rows:
        pos, _dd = _calc_pos_and_dd20(cur, r['stock_id'], str(r['backtest_date'])[:10])
        r['_pos'] = pos
        r['_band'] = band_of(pos)

    real = [r for r in rows if r.get('anchor_rating_date')]

    def agg(sub: list[dict]) -> dict:
        out: dict[str, dict[str, int]] = {}
        for field, _label, kind in PA_FIELDS:
            if kind == 'np':
                sub2 = [r for r in sub if not r.get('has_position')]
            elif kind == 'hp':
                sub2 = [r for r in sub if r.get('has_position')]
            else:
                sub2 = sub
            n = sum(1 for r in sub2 if r.get(field) is not None)
            c = sum(1 for r in sub2 if r.get(field) == 1)
            out[field] = {'n': n, 'c': c}
        return out

    result = {
        'total': len(rows),
        'real_total': len(real),
        'overall_all': agg(rows),
        'overall_real': agg(real),
        'by_rating_real': {},
        'by_band_real': {},
        'pos_coverage_real': sum(1 for r in real if r.get('_band')),
    }
    for rating in RATING_ORDER_BY_RANK:
        sub = [r for r in real if r.get('rating') == rating]
        if sub:
            result['by_rating_real'][rating] = agg(sub)
    for key, _lo, _hi, label in POS_BANDS:
        sub = [r for r in real if r.get('_band') == key]
        if sub:
            result['by_band_real'][label] = agg(sub)
    return result


# ================================================================
# 三、评级升降后表现（ratings_history 序列 × 回测前瞻收益）
# ================================================================


def rating_change_performance(cur: sqlite3.Cursor, market: str | None = None) -> dict:
    """评级升/降档后表现：方向 = 相邻两次评级档位差（RATING_ORDER）；
    前瞻收益取该评级行自然键对应的回测行（return_1w/1m + dynamic_return）。

    口径声明：升降档只在真实变更（is_change=1 或相邻档位不同）时计；
    前瞻收益与「评级有效性」同源（backtest_results，同列同口径）。
    """
    sql = (
        'SELECT rh.stock_id, rh.rating_date, rh.rating, rh.total_score, '
        'rh.is_change, rh.engine_version, s.market '
        'FROM ratings_history rh JOIN stocks s ON s.id = rh.stock_id'
    )
    args: list = []
    if market:
        sql += ' WHERE s.market = ?'
        args.append(market)
    sql += ' ORDER BY rh.stock_id, rh.rating_date'
    hist = [dict(r) for r in cur.execute(sql, args).fetchall()]

    # 自然键 → 回测行（取 max(id)，与 load_rating_rows 同去重口径）
    bt: dict[tuple, dict] = {}
    for r in cur.execute(
        'SELECT * FROM backtest_results '
        'WHERE (is_simulated IS NULL OR is_simulated = 0) '
        'AND rating_id IS NOT NULL AND rating_id != -1'
    ).fetchall():
        d = dict(r)
        key = (d['stock_id'], d['rating_date'])
        if key not in bt or d['id'] > bt[key]['id']:
            bt[key] = d

    events: list[dict] = []
    by_stock: dict[int, list[dict]] = {}
    for h in hist:
        by_stock.setdefault(h['stock_id'], []).append(h)
    for _sid, seq in by_stock.items():
        for i in range(1, len(seq)):
            prev, curr = seq[i - 1], seq[i]
            prev_norm = normalize_rating(prev['rating'], prev.get('total_score'))
            curr_norm = normalize_rating(curr['rating'], curr.get('total_score'))
            if not prev_norm or not curr_norm:
                continue
            diff = RATING_ORDER.get(curr_norm, 3) - RATING_ORDER.get(prev_norm, 3)
            if diff == 0:
                continue
            fwd = bt.get((curr['stock_id'], curr['rating_date']))
            events.append({
                'stock_id': curr['stock_id'],
                'rating_date': curr['rating_date'],
                'direction': 'upgrade' if diff > 0 else 'downgrade',
                'from': prev_norm,
                'to': curr_norm,
                'is_change': curr.get('is_change'),
                'engine_version': curr.get('engine_version'),
                'return_1w': fwd.get('return_1w') if fwd else None,
                'return_1m': fwd.get('return_1m') if fwd else None,
                'dynamic_return': fwd.get('dynamic_return') if fwd else None,
            })

    def agg(sub: list[dict]) -> dict:
        out: dict[str, dict[str, object]] = {}
        for field in ('return_1w', 'return_1m', 'dynamic_return'):
            vals = [e[field] for e in sub if e.get(field) is not None]
            up = sum(1 for v in vals if v > 0)
            out[field] = {
                'n': len(vals),
                'up': up,
                'avg': round(sum(vals) / len(vals), 2) if vals else None,
            }
        return out

    upgrades = [e for e in events if e['direction'] == 'upgrade']
    downgrades = [e for e in events if e['direction'] == 'downgrade']
    return {
        'total_changes': len(events),
        'upgrade': agg(upgrades),
        'downgrade': agg(downgrades),
        'upgrade_by_engine': {
            ev or '未标记(历史)': agg([e for e in upgrades
                                     if (e.get('engine_version') or '未标记(历史)') == ev])
            for ev in {e.get('engine_version') or '未标记(历史)' for e in upgrades}
        },
        'downgrade_by_engine': {
            ev or '未标记(历史)': agg([e for e in downgrades
                                     if (e.get('engine_version') or '未标记(历史)') == ev])
            for ev in {e.get('engine_version') or '未标记(历史)' for e in downgrades}
        },
    }


# ================================================================
# 四、显著结论识别（分化门槛与生产同口径）
# ================================================================


def notable_findings(m: dict) -> list[dict]:
    """从分档×位置×周期矩阵提取显著分化：两带各 n≥10 且差 ≥15pp。"""
    findings: list[dict] = []
    matrix = m['matrix']
    for rating, bands in matrix.items():
        lo = bands.get('low', {})
        hi = bands.get('high', {})
        for period, label in PERIODS + (('dyn', '动态窗口'),):
            lo_c, lo_n = lo.get(period, {}).get('c', 0), lo.get(period, {}).get('n', 0)
            hi_c, hi_n = hi.get(period, {}).get('c', 0), hi.get(period, {}).get('n', 0)
            if lo_n >= N_BAND_MIN and hi_n >= N_BAND_MIN:
                gap = (lo_c / lo_n - hi_c / hi_n) * 100
                if abs(gap) >= GAP_MIN_PP:
                    lead = '低' if gap > 0 else '高'
                    findings.append({
                        'rating': rating, 'period': label,
                        'low': f'{lo_c / lo_n * 100:.0f}% ({lo_c}/{lo_n})',
                        'high': f'{hi_c / hi_n * 100:.0f}% ({hi_c}/{hi_n})',
                        'gap_pp': round(gap, 1), 'lead': lead,
                    })
    return findings


# ================================================================
# 渲染
# ================================================================

CLASS_DESC = (
    f'样本分级（诚实原则）：A=n≥{N_FULL} 可全展示；B={N_PARTIAL}~{N_FULL - 1} 带标注展示；'
    f'C<n<{N_PARTIAL} 样本不足，不展示百分数。'
    f'分化显著门槛：两带各 n≥{N_BAND_MIN} 且差 ≥{GAP_MIN_PP}pp（与 position_note_for 同口径）。'
)


def render(m: dict, pa: dict, rc: dict, findings: list[dict], market: str | None,
           meta: dict) -> str:
    lines: list[str] = []
    mk = f'（{market}）' if market else '（全市场）'
    lines.append(f'# 021BU 回测证据查询 — {datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")} {mk}')
    lines.append('')
    lines.append(f'- 数据库：`{meta["db"]}`（mode=ro 只读）')
    lines.append(f'- 评级回测真实样本：{meta["n_br"]} 行（is_simulated=0，自然键去重后；'
                 f'样本期 {meta["date_range"] or "—"}）')
    lines.append(f'- 价格建议回测：{pa["total"]} 点，其中真实锚点 {pa["real_total"]} 点'
                 f'（主口径，无未来函数）；位置分位可算 {pa["pos_coverage_real"]} 点')
    lines.append(f'- {CLASS_DESC}')
    lines.append('')

    # ---- 一、总体与引擎分层 ----
    lines.append('## 一、评级有效性总体与引擎分层')
    lines.append('')
    lines.append('| 口径 | 命中率 | 可判定 |')
    lines.append('|---|---|---|')
    for period, label in PERIODS:
        d = m['overall'][period]
        lines.append(f'| {label}（固定周期） | {fmt_cell(d["n"], d["c"])} | {d["n"]} |')
    d = m['overall']['dyn']
    lines.append(f'| 动态窗口（评级有效期） | {fmt_cell(d["n"], d["c"])} | {d["n"]} |')
    lines.append('')
    lines.append('### 按引擎版本分层')
    lines.append('')
    lines.append('| 引擎版本 | T+1 | T+5 | T+20 | 动态 |')
    lines.append('|---|---|---|---|---|')
    for ev, eng in sorted(m['engine'].items()):
        cells = [fmt_cell(eng[p]['n'], eng[p]['c']) for p, _ in PERIODS]
        cells.append(fmt_cell(eng['dyn']['n'], eng['dyn']['c']))
        lines.append(f'| {ev} | ' + ' | '.join(cells) + ' |')
    lines.append('')

    # ---- 二、分档 × 位置 × 周期 ----
    lines.append('## 二、分档 × 位置分位 × 周期命中矩阵')
    lines.append('')
    lines.append('| 评级档 | 位置带 | T+1 | T+5 | T+20 | 动态 |')
    lines.append('|---|---|---|---|---|---|')
    for rating in RATING_ORDER_BY_RANK:
        bands = m['matrix'].get(rating)
        if not bands:
            continue
        for key, _lo, _hi, label in POS_BANDS:
            cell = bands.get(key)
            if not cell:
                continue
            cells = [fmt_cell(cell[p]['n'], cell[p]['c']) if p in cell else '—'
                     for p, _ in PERIODS]
            cells.append(fmt_cell(cell['dyn']['n'], cell['dyn']['c'])
                         if 'dyn' in cell else '—')
            lines.append(f'| {rating} | {label} | ' + ' | '.join(cells) + ' |')
    lines.append('')

    # ---- 三、分档总体（评级旁展示候选）----
    lines.append('## 三、分档总体命中率（「评级旁 n/m」展示候选）')
    lines.append('')
    lines.append('| 评级档 | T+1 主口径 | 动态窗口 |')
    lines.append('|---|---|---|')
    for rating in RATING_ORDER_BY_RANK:
        s = m.get('rating_stats', {}).get(rating)
        if not s:
            continue
        lines.append(f'| {rating} | {fmt_cell(s["1d"]["n"], s["1d"]["c"])} '
                     f'| {fmt_cell(s["dyn"]["n"], s["dyn"]["c"])} |')
    lines.append('')

    # ---- 四、价格建议命中 ----
    lines.append('## 四、价格建议命中率（真实锚点样本）')
    lines.append('')
    lines.append('| 建议项 | 全体（含重建点） | 真实锚点（主口径） |')
    lines.append('|---|---|---|')

    def _lbl(field: str) -> str:
        for f, label2, _k in PA_FIELDS:
            if f == field:
                return label2
        return field

    for field, label, _kind in PA_FIELDS:
        a = pa['overall_all'][field]
        b = pa['overall_real'][field]
        lines.append(f'| {label} | {fmt_cell(a["n"], a["c"])} | {fmt_cell(b["n"], b["c"])} |')
    lines.append('')
    lines.append('### 按评级档 × 真实锚点')
    lines.append('')
    header = '| 评级档 | n | ' + ' | '.join(_lbl(f) for f, _l, _k in PA_FIELDS) + ' |'
    lines.append(header)
    lines.append('|---|---|' + '---|' * len(PA_FIELDS))
    for rating, cells in pa['by_rating_real'].items():
        row = [fmt_cell(c['n'], c['c']) for c in cells.values()]
        total_n = sum(c['n'] for c in cells.values())
        lines.append(f'| {rating} | {total_n} | ' + ' | '.join(row) + ' |')
    lines.append('')
    lines.append('### 按位置带 × 真实锚点（位置分位为查询时现算，同 _calc_pos_and_dd20 公式）')
    lines.append('')
    lines.append(header)
    lines.append('|---|---|' + '---|' * len(PA_FIELDS))
    for label, cells in pa['by_band_real'].items():
        row = [fmt_cell(c['n'], c['c']) for c in cells.values()]
        total_n = sum(c['n'] for c in cells.values())
        lines.append(f'| {label} | {total_n} | ' + ' | '.join(row) + ' |')
    lines.append('')

    # ---- 五、评级升降后表现 ----
    lines.append('## 五、评级升降后表现')
    lines.append('')
    lines.append(f'真实升降档事件：{rc["total_changes"]} 次（相邻评级档位变化，'
                 '前瞻收益与评级回测同源）')
    lines.append('')
    lines.append('| 方向 | 前瞻窗口 | 样本 | 上涨占比 | 平均收益% |')
    lines.append('|---|---|---|---|---|')
    for dir_key, dir_label in (('upgrade', '升档后'), ('downgrade', '降档后')):
        for field, wlabel in (('return_1w', 'T+5'), ('return_1m', 'T+20'),
                              ('dynamic_return', '动态窗口')):
            d = rc[dir_key][field]
            if d['n'] == 0:
                lines.append(f'| {dir_label} | {wlabel} | 0 | — | — |')
                continue
            up_pct = f'{d["up"] / d["n"] * 100:.0f}%'
            avg = f'{d["avg"]:+.1f}' if d['avg'] is not None else '—'
            lines.append(f'| {dir_label} | {wlabel} | {d["n"]} | {up_pct} | {avg} |')
    lines.append('')

    # ---- 六、显著结论 ----
    lines.append('## 六、显著分化结论（两带各 n≥10 且差 ≥15pp）')
    lines.append('')
    if findings:
        lines.append('| 评级档 | 周期 | 低位带 | 高位带 | 分化 | 结论 |')
        lines.append('|---|---|---|---|---|---|')
        for f in findings:
            lines.append(f'| {f["rating"]} | {f["period"]} | {f["low"]} | {f["high"]} '
                         f'| {f["gap_pp"]:+.0f}pp | {f["lead"]}位更可信 |')
    else:
        lines.append('（当前样本下无满足门槛的显著分化）')
    lines.append('')

    return '\n'.join(lines)


# ================================================================
# 主流程
# ================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description='021BU 回测证据查询（只读）')
    parser.add_argument('--db', default=None, help='SQLite 路径（默认 config.DB_PATH）')
    parser.add_argument('--market', default=None, choices=['a_stock', 'hk_stock'],
                        help='只统计指定市场（缺省=全市场）')
    parser.add_argument('--out', default=None, help='markdown 输出路径')
    parser.add_argument('--json', dest='json_out', default=None, help='JSON 输出路径')
    args = parser.parse_args()

    # Windows 控制台默认 GBK，输出含 ⚠/✓ 等符号时强制 UTF-8（仅本进程 stdout）
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    con = open_ro_db(args.db)
    cur = con.cursor()
    rows = load_rating_rows(cur, args.market)
    m = rating_period_matrix(rows)
    m['rating_stats'] = rating_stats_overall(rows)
    pa = price_advice_matrix(cur, args.market)
    rc = rating_change_performance(cur, args.market)
    findings = notable_findings(m)
    dates = sorted({r['rating_date'] for r in rows if r.get('rating_date')})
    meta = {
        'db': args.db or DB_PATH,
        'n_br': len(rows),
        'date_range': f'{dates[0]} ~ {dates[-1]}' if dates else '',
    }
    con.close()

    md = render(m, pa, rc, findings, args.market, meta)
    print(md)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(md + '\n')
        print(f'\n[已写出] {args.out}', file=sys.stderr)
    if args.json_out:
        payload = {'meta': meta, 'rating_matrix': m, 'price_advice': pa,
                   'rating_change': rc, 'findings': findings}
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        print(f'[已写出] {args.json_out}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
