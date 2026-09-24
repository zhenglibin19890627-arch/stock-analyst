#!/usr/bin/env python3
"""021BW 情绪代理证据查询脚本（只读可复用）——五类情绪代理分层回测检验。

=====================================================================
定位（021BW t1 情绪证据全景）
====================================================================
回答「情绪在你的数据里到底有没有预测力」：对五类情绪代理各自分桶，
对齐 backtest_results 的 T+5（return_1w）/ T+20（return_1m）后续收益，
算各桶上涨占比与平均收益；并做两层增量对照（避免与已知因子重复计价）：

  ① 评级档内对照：同一评级档内部各桶是否仍分化（控制评级维度后
     是否有增量预测力）；
  ② 资金面维度分内对照：与资金面评分重叠的代理（主力/散户/杠杆）
     在同一 capital_score 带内是否仍分化（控制已计价因子）。

五类代理（全部 as-of 评级日，只用当时可得信息，无未来函数）：
  S1 新闻情绪     news_sentiment 近 7 日最新非空行 avg_sentiment
  S2 主力情绪     raw_capital_flow 真实行（R1：过滤 is_estimated=1）
                  ——5 日累计净流入 / 连续同向天数 / 超大单 5 日符号
  S3 散户情绪     中小单 5 日累计符号；主力×散户 2×2 组合；
                  holder_structure 股东户数变化（最新披露，staleness 声明）
  S4 杠杆情绪     raw_capital_flow.margin_balance 5 日变化幅度
  S5 市场热度     raw_kline 换手率 / 量比（当日量 ÷ 前 5 日均量）

样本分级（021BU 同款诚实展示；任务门槛 ≥20/桶）：
  A 级  n ≥ 30   —— 可信，全展示
  B 级  20 ≤ n < 30 —— 展示带「样本偏小」标注
  C 级  n < 20   —— INFO：不展示百分数，仅展示 n（宁缺毋滥）
  成对分化显著门槛：两侧桶各 n≥20 且差 ≥15pp（比 021BU 的 ≥10/带更严，
  因桶对照是本批核心结论，防小样本巧合）。

====================================================================
只读边界
====================================================================
  - 数据库一律 sqlite3.connect('file:...?mode=ro', uri=True)；
  - 零写库、零网络、零 pip 新依赖；
  - 评级/档位/周期口径复用生产既有纯函数（R7 合规：复用不重实现）；
  - 行为统计（trade_records）为只读描述统计，样本 C 级不作依据。

====================================================================
用法
====================================================================
  python scripts/query_sentiment_evidence_021bw.py                 # 全量打印
  python scripts/query_sentiment_evidence_021bw.py --market a_stock
  python scripts/query_sentiment_evidence_021bw.py --out PATH --json PATH
  python scripts/query_sentiment_evidence_021bw.py --db PATH
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

CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

# ---- 样本分级门槛（021BU 同款 + 任务 ≥20/桶门槛）----
N_FULL = 30       # A 级
N_PARTIAL = 20    # B 级（= 任务桶级展示门槛；<20 → INFO）
GAP_MIN_PP = 15.0  # 成对分化最小差（两侧各 n≥N_PARTIAL）
STALE_DAYS = 7     # 代理行的「as-of 新鲜度」窗口（自然日）

# 资金面维度分带（analysis_results.capital_score，增量对照②用）
CAP_BANDS = (
    ('low', 0.0, 50.0, '资金面<50'),
    ('mid', 50.0, 70.0, '资金面50-70'),
    ('high', 70.0, 101.0, '资金面≥70'),
)

# 维度分带通用边界（复用 CAP_BANDS 三段：低/中/高）
DIM_BAND_LABELS = {
    'capital_score': ('资金面<50', '资金面50-70', '资金面≥70'),
    'sentiment_score': ('消息面<50', '消息面50-70', '消息面≥70'),
    'technical_score': ('技术面<50', '技术面50-70', '技术面≥70'),
}

RATING_TIERS = ('推荐买入', '持有观望', '建议减仓')  # 样本主要三档（其余 C 级并入「其他档」）

# 「不可得」桶：不进统计（无桶信息；只计入可得率缺失列）
MISS_BUCKETS = frozenset({'数据不足', '缺失', '换手率缺失', '换手率字段失活(=0)',
                          '无披露', '无近期披露(180日)', '近7日无有效新闻'})

PERIODS = (
    ('t5', 'T+5', 'return_1w', 'alpha_1w'),
    ('t20', 'T+20', 'return_1m', 'alpha_1m'),
)


# ================================================================
# 工具
# ================================================================


def open_ro_db(db_path: str | None = None) -> sqlite3.Connection:
    """只读连接（mode=ro），行工厂 sqlite3.Row。"""
    path = db_path or DB_PATH
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con


def sample_class(n: int) -> str:
    if n >= N_FULL:
        return 'A'
    if n >= N_PARTIAL:
        return 'B'
    return 'C'


def _date(d) -> str:
    return str(d)[:10]


def _days_between(a: str, b: str) -> int:
    """b - a 的自然日差（ISO 日期字符串）。"""
    try:
        return (datetime.strptime(b, '%Y-%m-%d') - datetime.strptime(a, '%Y-%m-%d')).days
    except ValueError:
        return 9999


def fmt_stat(d: dict | None, with_alpha: bool = False) -> str:
    """诚实展示一格：n≥20 显示「上涨xx%·均+x.x%」，n<20 只给 INFO n。"""
    if not d or d.get('n', 0) <= 0:
        return '—'
    n = d['n']
    if sample_class(n) == 'C':
        return f'INFO(n={n})'
    up_pct = d['up'] / n * 100
    avg = d['sum'] / n
    tag = '' if sample_class(n) == 'A' else '⚠偏小'
    base = f'{up_pct:.0f}%/均{avg:+.1f}{tag} (n={n})'
    if with_alpha and d.get('an'):
        alpha_up = d['aup'] / d['an'] * 100
        base += f'·超额{alpha_up:.0f}%'
    return base


# ================================================================
# 数据装载（每股时序缓存 + as-of 切片）
# ================================================================


def load_bt_rows(cur: sqlite3.Cursor, market: str | None) -> list[dict]:
    """真实评级回测行（021BB 自然键去重，max(id)）。"""
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
    best: dict[tuple, dict] = {}
    for r in rows:
        key = (r['stock_id'], _date(r['rating_date']))
        if key not in best or r['id'] > best[key]['id']:
            best[key] = r
    return sorted(best.values(), key=lambda r: (_date(r['rating_date']), r['stock_id']))


def load_dim_scores(cur: sqlite3.Cursor, dim_col: str) -> dict[tuple, float]:
    """(stock_id, analysis_date) → 维度分（增量对照基准；capital/sentiment/technical）。

    dim_col 为本脚本常量白名单内的列名（非用户输入），无注入面。
    """
    assert dim_col in DIM_BAND_LABELS, f'意外维度列: {dim_col}'
    sql = ('SELECT stock_id, analysis_date, ' + dim_col + ' FROM analysis_results '
           'WHERE ' + dim_col + ' IS NOT NULL')
    out: dict[tuple, float] = {}
    for r in cur.execute(sql).fetchall():
        out[(r['stock_id'], _date(r['analysis_date']))] = float(r[dim_col])
    return out


def load_series(cur: sqlite3.Cursor, sql: str, date_col: str) -> dict[int, list[dict]]:
    """按 stock_id 分组的时序缓存（date_col 升序）。"""
    out: dict[int, list[dict]] = {}
    for r in cur.execute(sql).fetchall():
        d = dict(r)
        out.setdefault(d['stock_id'], []).append(d)
    for sid in out:
        out[sid].sort(key=lambda x: _date(x[date_col]))
    return out


def asof(series: list[dict], date_col: str, as_of: str, stale_days: int | None = None
         ) -> list[dict]:
    """as-of 切片：date ≤ as_of 的行（评级日当天可得信息）。"""
    cut = [r for r in series if _date(r[date_col]) <= as_of]
    if stale_days is not None and cut:
        latest = _date(cut[-1][date_col])
        if _days_between(latest, as_of) > stale_days:
            return []  # 最新行已过期 → 视为不可得
    return cut


# ================================================================
# 五类代理的桶函数（输入均为 as-of 行列表）
# ================================================================


def bucket_news(news_rows: list[dict], as_of: str) -> str:
    """S1 新闻情绪：近 7 日最新非空行（total_count>0）avg_sentiment 分桶。"""
    recent = [r for r in news_rows if _days_between(_date(r['news_date']), as_of) <= STALE_DAYS]
    row = next((r for r in reversed(recent) if (r.get('total_count') or 0) > 0), None)
    if row is None:
        return '近7日无有效新闻'
    s = row.get('avg_sentiment')
    if s is None:
        return '近7日无有效新闻'
    if s > 0.1:
        return '正面(>0.1)'
    if s < -0.1:
        return '负面(<-0.1)'
    return '中性(±0.1)'


def _real_flow(rows: list[dict], field: str) -> list[dict]:
    """真实资金流行（R1：过滤 is_estimated=1）且字段非空，最近 N 行。"""
    return [r for r in rows
            if (r.get('is_estimated') in (0, None)) and r.get(field) is not None]


def capital_features(flow_rows: list[dict], as_of: str) -> dict:
    """S2/S3 资金流特征（as-of，最近 ≤5 个真实交易日）。"""
    out = {'flow5': None, 'streak': None, 'super5': None, 'retail5': None, 'rows': 0}
    main_rows = _real_flow(flow_rows, 'main_net_inflow')[-5:]
    out['rows'] = len(main_rows)
    if main_rows:
        vals = [r['main_net_inflow'] for r in main_rows]
        out['flow5'] = sum(vals)
        # 连续同向（从最新行往回数）
        last = vals[-1]
        streak = 0
        for v in reversed(vals):
            if (v > 0) == (last > 0) and v != 0:
                streak += 1
            else:
                break
        out['streak'] = ('in', streak) if last > 0 else ('out', streak)
        sup = [r['super_large_net'] for r in main_rows if r.get('super_large_net') is not None]
        if sup:
            out['super5'] = sum(sup)
        med = [r['medium_net'] for r in main_rows if r.get('medium_net') is not None]
        sml = [r['small_net'] for r in main_rows if r.get('small_net') is not None]
        if med and sml:
            out['retail5'] = sum(med) + sum(sml)
    return out


def bucket_main_flow5(feat: dict) -> str:
    v = feat['flow5']
    if v is None:
        return '数据不足'
    if v >= 2000:
        return '5日大幅流入(≥2千万)'
    if v > 200:
        return '5日小幅流入'
    if v < -2000:
        return '5日大幅流出(≤-2千万)'
    if v < -200:
        return '5日小幅流出'
    return '5日均衡(±2百万)'


def bucket_main_streak(feat: dict) -> str:
    s = feat['streak']
    if s is None:
        return '数据不足'
    direction, n = s
    if direction == 'in':
        return '连续≥3日流入' if n >= 3 else '流入1-2日'
    return '连续≥3日流出' if n >= 3 else '流出1-2日'


def bucket_super(feat: dict) -> str:
    v = feat['super5']
    if v is None:
        return '缺失'
    return '超大单5日净买入' if v > 0 else '超大单5日净卖出'


def bucket_retail(feat: dict) -> str:
    v = feat['retail5']
    if v is None:
        return '缺失'
    return '散户5日净买入' if v > 0 else '散户5日净卖出'


def bucket_combo(feat: dict) -> str:
    """主力×散户 2×2（capital_narrative 叙述的组合推断的可检验版）。"""
    m, r = feat['flow5'], feat['retail5']
    if m is None or r is None:
        return '数据不足'
    main_in = m > 0
    retail_buy = r > 0
    if main_in and not retail_buy:
        return '主力流入×散户净卖(吸筹形态)'
    if main_in and retail_buy:
        return '主力流入×散户净买(共振进攻)'
    if not main_in and retail_buy:
        return '主力流出×散户净买(派发嫌疑)'
    return '主力流出×散户净卖(齐撤)'


def bucket_holder(holder_rows: list[dict], as_of: str) -> str:
    """S3b 股东户数变化（as-of 最新披露，180 自然日新鲜度守卫）。

    注：评分面 _read_holder_structure 取最新一期**无 staleness 守卫**（2022 年披露
    可被 2026 年评级引用）；本检验取比评分更严的 180 日窗，属诚实口径。
    """
    rows = [r for r in holder_rows
            if _date(r['stat_date']) <= as_of and r.get('holder_count_change_pct') is not None]
    if not rows or _days_between(_date(rows[-1]['stat_date']), as_of) > 180:
        return '无近期披露(180日)'
    chg = float(rows[-1]['holder_count_change_pct'])
    if chg <= -3:
        return '户数降幅≥3%(集中)'
    if chg >= 3:
        return '户数增幅≥3%(分散)'
    return '户数稳定(±3%内)'


def bucket_margin(flow_rows: list[dict]) -> str:
    """S4 杠杆情绪：margin_balance 最新 vs 5 个真实行前的变化幅度。"""
    rows = [r for r in flow_rows if r.get('margin_balance') is not None][-6:]
    if len(rows) < 2:
        return '数据不足'
    now, base = rows[-1]['margin_balance'], rows[0]['margin_balance']
    if not base:
        return '数据不足'
    chg_pct = (now - base) / abs(base) * 100
    if chg_pct > 0.5:
        return '融资余额5日升(>+0.5%)'
    if chg_pct < -0.5:
        return '融资余额5日降(<-0.5%)'
    return '融资余额5日平(±0.5%)'


def bucket_turnover(kline_rows: list[dict]) -> str:
    """S5a 换手率（评级日当日；A股口径）。

    实测：raw_kline.turnover 自 2026-07 起窗口内恒为 0.0（字段失活，非 NULL）——
    0/负值一律按「缺失」处理，不得混入低换手桶。
    """
    if not kline_rows:
        return '数据不足'
    t = kline_rows[-1].get('turnover')
    if t is None or t <= 0:
        return '换手率字段失活(=0)'
    if t > 7:
        return '换手>7%'
    if t > 3:
        return '换手3-7%'
    if t > 1:
        return '换手1-3%'
    return '换手<1%'


def bucket_vol_ratio(kline_rows: list[dict]) -> str:
    """S5b 量比 = 当日量 / 前 5 日均量（_volume_structure.vol_trend 同阈值）。"""
    rows = [r for r in kline_rows
            if r.get('volume') not in (None, 0)][-6:]
    if len(rows) < 6:
        return '数据不足'
    base = sum(r['volume'] for r in rows[:-1]) / 5
    if base <= 0:
        return '数据不足'
    ratio = rows[-1]['volume'] / base
    if ratio > 2.5:
        return '极端放量(>2.5)'
    if ratio > 1.2:
        return '温和放量(1.2-2.5)'
    if ratio >= 0.8:
        return '平量(0.8-1.2)'
    return '缩量(<0.8)'


# 代理注册表：key → (标题, 桶函数(feat) / (rows, as_of) 形态说明)
NEWS_KEY = 'news'
PROXIES_FLOW = ('main_flow5', 'main_streak', 'super5', 'retail5', 'combo', 'margin')
PROXIES_OTHER = ('news', 'holder', 'turnover', 'vol_ratio')
ALL_PROXIES = ('news', 'main_flow5', 'main_streak', 'super5', 'retail5', 'combo',
               'holder', 'margin', 'turnover', 'vol_ratio')

PROXY_TITLES = {
    'news': 'S1 新闻情绪（近7日最新非空 avg_sentiment）',
    'main_flow5': 'S2a 主力5日累计净流入（真实行）',
    'main_streak': 'S2b 主力连续同向天数',
    'super5': 'S2c 超大单5日净向',
    'retail5': 'S3a 散户（中小单）5日净向',
    'combo': 'S3a+ 主力×散户 2×2 组合（capital_narrative 可检验版）',
    'holder': 'S3b 股东户数变化（最新披露，低频）',
    'margin': 'S4 融资余额5日变化（A股）',
    'turnover': 'S5a 换手率（评级日当日）',
    'vol_ratio': 'S5b 量比（当日量/前5日均量）',
}


# ================================================================
# 聚合
# ================================================================


def _new_cell() -> dict:
    out = {p[0]: {'n': 0, 'up': 0, 'sum': 0.0, 'an': 0, 'aup': 0} for p in PERIODS}
    return out


def agg_add(cell: dict, row: dict) -> None:
    for key, _label, col, acol in PERIODS:
        v = row.get(col)
        d = cell[key]
        if v is not None:
            d['n'] += 1
            d['sum'] += float(v)
            if v > 0:
                d['up'] += 1
        a = row.get(acol)
        if a is not None:
            d['an'] += 1
            if a > 0:
                d['aup'] += 1


def blank_store() -> dict:
    return {p: {} for p in ALL_PROXIES}


def build_stores(cur: sqlite3.Cursor, market: str | None) -> tuple[list[dict], dict, dict, dict]:
    """主样本 + 三层聚合仓：总体 / 评级档内 / 维度分带内（资金面/消息面/技术面）。"""
    rows = load_bt_rows(cur, market)
    dim_scores = {d: load_dim_scores(cur, d) for d in DIM_BAND_LABELS}

    news_s = load_series(cur, 'SELECT stock_id, news_date, avg_sentiment, total_count '
                              'FROM news_sentiment', 'news_date')
    flow_s = load_series(cur, 'SELECT stock_id, trade_date, is_estimated, main_net_inflow, '
                              'super_large_net, medium_net, small_net, margin_balance '
                              'FROM raw_capital_flow', 'trade_date')
    kline_s = load_series(cur, 'SELECT stock_id, trade_date, volume, turnover FROM raw_kline',
                          'trade_date')
    holder_s = load_series(cur, 'SELECT stock_id, stat_date, holder_count_change_pct '
                                'FROM holder_structure', 'stat_date')

    store_all = blank_store()
    store_by_rating = blank_store()
    store_by_dim: dict[str, dict] = {d: blank_store() for d in DIM_BAND_LABELS}
    coverage = {p: {'ok': 0, 'miss': 0} for p in ALL_PROXIES}

    for row in rows:
        sid, as_of = row['stock_id'], _date(row['rating_date'])
        news_rows = asof(news_s.get(sid, []), 'news_date', as_of)
        flow_rows = asof(flow_s.get(sid, []), 'trade_date', as_of)
        kl_rows = asof(kline_s.get(sid, []), 'trade_date', as_of)

        feat = capital_features(flow_rows, as_of)
        buckets = {
            'news': bucket_news(news_rows, as_of),
            'main_flow5': bucket_main_flow5(feat),
            'main_streak': bucket_main_streak(feat),
            'super5': bucket_super(feat),
            'retail5': bucket_retail(feat),
            'combo': bucket_combo(feat),
            'holder': bucket_holder(holder_s.get(sid, []), as_of),
            'margin': bucket_margin(flow_rows),
            'turnover': bucket_turnover(kl_rows),
            'vol_ratio': bucket_vol_ratio(kl_rows),
        }
        for p, b in buckets.items():
            if b in MISS_BUCKETS:
                coverage[p]['miss'] += 1
                continue  # 不可得桶不进统计（无桶信息，混入只会制造伪样本量）
            coverage[p]['ok'] += 1
            cell = store_all[p].setdefault(b, _new_cell())
            agg_add(cell, row)
            rating = row.get('rating') or '?'
            tier = rating if rating in RATING_TIERS else '其他档(C级)'
            agg_add(store_by_rating[p].setdefault((tier, b), _new_cell()), row)
            for d, scores in dim_scores.items():
                v = scores.get((sid, as_of))
                if v is None:
                    continue
                band = next((DIM_BAND_LABELS[d][i] for i, (_k, lo, hi, _lbl)
                             in enumerate(CAP_BANDS) if lo <= v < hi), None)
                if band:
                    agg_add(store_by_dim[d][p].setdefault((band, b), _new_cell()), row)

    return rows, store_all, store_by_rating, {'by_dim': store_by_dim, 'coverage': coverage}


# ================================================================
# 行为统计（trade_records，只读描述统计）
# ================================================================


def behavior_stats(cur: sqlite3.Cursor) -> dict:
    """用户自身行为模式（C 级描述统计，不作依据）：摊薄加仓/高位买入/
    止损后再入场/浮亏卖出。成本以「历史买入 VWAP」近似（诚实标注）。"""
    trades = [dict(r) for r in cur.execute(
        'SELECT stock_id, trade_type, price, quantity, amount, trade_date FROM trade_records '
        "WHERE trade_type IN ('buy','sell') AND price IS NOT NULL AND quantity IS NOT NULL "
        'ORDER BY stock_id, trade_date, id').fetchall()]
    kline_s = load_series(cur, 'SELECT stock_id, trade_date, close, high, low, volume '
                               'FROM raw_kline', 'trade_date')

    stats = {
        'n_trades': len(trades),
        'n_stocks': len({t['stock_id'] for t in trades}),
        'n_buy': sum(1 for t in trades if t['trade_type'] == 'buy'),
        'n_sell': sum(1 for t in trades if t['trade_type'] == 'sell'),
        'avg_down': {'n': 0, 'total_buy': 0},
        'high_pos_buy': {'n': 0, 'total': 0},   # 买入价处于近60日分位 ≥0.7
        'low_pos_buy': {'n': 0, 'total': 0},    # 买入价处于近60日分位 ≤0.3
        'reentry': {'n': 0, 'lower': 0, 'win_soon': 0},
        'loss_sell': {'n': 0, 'total_sell': 0},
        'per_stock': [],
    }
    by_stock: dict[int, list[dict]] = {}
    for t in trades:
        by_stock.setdefault(t['stock_id'], []).append(t)

    for sid, seq in sorted(by_stock.items()):
        cum_qty, cum_amt = 0, 0.0
        avg_down_n = buy_n = 0
        last_sell = None
        reentry_n = reentry_lower = 0
        loss_sell_n = sell_n = 0
        hi_n = lo_n = 0
        for t in seq:
            date, price = _date(t['trade_date']), float(t['price'])
            if t['trade_type'] == 'buy':
                buy_n += 1
                vwap = cum_amt / cum_qty if cum_qty > 0 else None
                if vwap is not None and price < vwap:
                    avg_down_n += 1
                # 近 60 根K线位置分位（as-of 买入日，_calc_pos_and_dd20 同公式简化版）
                kl = [r for r in kline_s.get(sid, []) if _date(r['trade_date']) <= date][-60:]
                closes = [float(r['close']) for r in kl if r.get('close')]
                if len(closes) >= 20:
                    lo, hi = min(closes), max(closes)
                    if hi > lo:
                        pos = (price - lo) / (hi - lo)
                        if pos >= 0.7:
                            hi_n += 1
                        elif pos <= 0.3:
                            lo_n += 1
                if last_sell is not None and 0 <= _days_between(last_sell['date'], date) <= 30:
                    reentry_n += 1
                    if price < last_sell['price']:
                        reentry_lower += 1
                    last_sell = None
                cum_qty += int(t['quantity'])
                cum_amt += float(t['amount'] or (price * t['quantity']))
            else:
                sell_n += 1
                vwap = cum_amt / cum_qty if cum_qty > 0 else None
                if vwap is not None and price < vwap:
                    loss_sell_n += 1
                last_sell = {'date': date, 'price': price}
                cum_qty = max(0, cum_qty - int(t['quantity']))
        stats['avg_down']['n'] += avg_down_n
        stats['avg_down']['total_buy'] += buy_n
        stats['high_pos_buy']['n'] += hi_n
        stats['high_pos_buy']['total'] += buy_n
        stats['low_pos_buy']['n'] += lo_n
        stats['low_pos_buy']['total'] += buy_n
        stats['reentry']['n'] += reentry_n
        stats['reentry']['lower'] += reentry_lower
        stats['loss_sell']['n'] += loss_sell_n
        stats['loss_sell']['total_sell'] += sell_n
        stats['per_stock'].append({
            'stock_id': sid, 'buys': buy_n, 'sells': sell_n,
            'avg_down': avg_down_n, 'high_pos': hi_n, 'low_pos': lo_n,
            'reentry30d': reentry_n, 'loss_sell': loss_sell_n,
        })
    return stats


# ================================================================
# 显著结论识别
# ================================================================


def notable_pairs(store: dict) -> list[dict]:
    """桶间成对分化：同代理内所有桶对，两侧 n≥20 且上涨占比差 ≥15pp。"""
    out: list[dict] = []
    for proxy, buckets in store.items():
        items = [(b, d['t5']) for b, d in buckets.items() if d['t5']['n'] >= N_PARTIAL]
        for i in range(len(items)):
            for j in range(i + 1, len(items)):
                (b1, d1), (b2, d2) = items[i], items[j]
                u1, u2 = d1['up'] / d1['n'], d2['up'] / d2['n']
                gap = (u1 - u2) * 100
                if abs(gap) < GAP_MIN_PP:
                    continue
                hi_b, hi_d, lo_b, lo_d = (b1, d1, b2, d2) if gap > 0 else (b2, d2, b1, d1)
                out.append({'proxy': proxy, 'period': 'T+5',
                            'hi': hi_b,
                            'hi_stat': f"{hi_d['up'] / hi_d['n'] * 100:.0f}% (n={hi_d['n']})",
                            'lo': lo_b,
                            'lo_stat': f"{lo_d['up'] / lo_d['n'] * 100:.0f}% (n={lo_d['n']})",
                            'gap_pp': round(abs(gap), 1)})
    return sorted(out, key=lambda x: -abs(x['gap_pp']))


def within_tier_spread(store_by_rating: dict, proxy: str) -> dict:
    """增量对照①：同一评级档内最大桶差（两侧各 n≥20）vs 总体桶差。"""
    tiers: dict[str, list[dict]] = {}
    for (tier, _b), cell in store_by_rating[proxy].items():
        tiers.setdefault(tier, []).append(cell['t5'])
    max_gap, detail = 0.0, ''
    for tier, cells in tiers.items():
        valid = [c for c in cells if c['n'] >= N_PARTIAL]
        for i in range(len(valid)):
            for j in range(i + 1, len(valid)):
                gap = abs(valid[i]['up'] / valid[i]['n'] - valid[j]['up'] / valid[j]['n']) * 100
                if gap > max_gap:
                    max_gap = gap
                    detail = f'{tier} 内 {gap:.1f}pp'
    return {'max_within_tier_gap_pp': round(max_gap, 1), 'detail': detail}


# ================================================================
# 渲染
# ================================================================


def render_proxy_table(store: dict, proxy: str) -> list[str]:
    lines = [f'### {PROXY_TITLES[proxy]}', '']
    buckets = store.get(proxy) or {}
    if not buckets:
        lines += ['（无样本）', '']
        return lines
    lines.append('| 桶 | T+5（上涨%/均收益%·超额/样本） | T+20 |')
    lines.append('|---|---|---|')
    for b in sorted(buckets, key=lambda x: -buckets[x]['t5']['n']):
        cell = buckets[b]
        lines.append(f'| {b} | {fmt_stat(cell["t5"], with_alpha=True)} '
                     f'| {fmt_stat(cell["t20"])} |')
    lines.append('')
    return lines


def render_control_table(store_ctrl: dict, proxy: str, ctrl_label: str) -> list[str]:
    lines = [f'#### {PROXY_TITLES[proxy]} × {ctrl_label}', '']
    buckets = store_ctrl.get(proxy) or {}
    if not buckets:
        lines += ['（无可用样本）', '']
        return lines
    groups: dict[str, list[tuple]] = {}
    for (g, b), cell in buckets.items():
        groups.setdefault(g, []).append((b, cell))
    lines.append(f'| {ctrl_label} | 桶 | T+5 | T+20 |')
    lines.append('|---|---|---|---|')
    for g in sorted(groups):
        for b, cell in sorted(groups[g], key=lambda x: -x[1]['t5']['n']):
            lines.append(f'| {g} | {b} | {fmt_stat(cell["t5"])} | {fmt_stat(cell["t20"])} |')
    lines.append('')
    return lines


def render(rows: list[dict], store_all: dict, store_by_rating: dict, extras: dict,
           behavior: dict, market: str | None, meta: dict) -> str:
    lines: list[str] = []
    mk = f'（{market}）' if market else '（全市场）'
    lines.append(f'# 021BW 情绪代理证据查询 — '
                 f'{datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")} {mk}')
    lines.append('')
    lines.append(f'- 数据库：`{meta["db"]}`（mode=ro 只读）')
    lines.append(f'- 评级回测真实样本：{meta["n_br"]} 行（is_simulated=0，自然键去重；'
                 f'样本期 {meta["date_range"] or "—"}）')
    lines.append('- 样本分级（021BU 同款 + 本批 ≥20/桶门槛）：A=n≥30 全展示；'
                 'B=20~29 带「⚠偏小」；C<20 仅 INFO(n)，不展示百分数。'
                 f'成对分化显著门槛：两侧各 n≥{N_PARTIAL} 且差 ≥{GAP_MIN_PP:.0f}pp。')
    lines.append('- 口径：全部代理 as-of 评级日（只用当时可得信息，无未来函数）；'
                 'T+5=return_1w、T+20=return_1m；「上涨%」=后续收益>0 占比，'
                 '「超额」=alpha（对基准）>0 占比；资金流代理过滤 is_estimated=1（R1）。')
    lines.append('')
    lines.append('## 一、代理可得率（评级日对齐：桶可用 / 缺失）')
    lines.append('')
    lines.append('| 代理 | 可得 | 缺失/不可得 |')
    lines.append('|---|---|---|')
    cov = extras['coverage']
    for p in ALL_PROXIES:
        lines.append(f'| {PROXY_TITLES[p]} | {cov[p]["ok"]} | {cov[p]["miss"]} |')
    lines.append('')
    lines.append('## 二、五类代理分层检验（桶 → T+5 / T+20 后续表现）')
    lines.append('')
    for p in ALL_PROXIES:
        lines += render_proxy_table(store_all, p)
    lines.append('## 三、增量对照①：评级档内分化（控制评级维度后是否仍有预测力）')
    lines.append('')
    lines.append('读法：若某代理只在「总体」有分化、同一评级档内部无分化，'
                 '说明其信息已被评级（已知因子）计价，不构成增量证据。')
    lines.append('')
    spread_rows = []
    for p in ALL_PROXIES:
        sp = within_tier_spread(store_by_rating, p)
        spread_rows.append((p, sp))
        lines += render_control_table(store_by_rating, p, '评级档')
    lines.append('## 四、增量对照②：维度分带内分化（控制已计价的同源因子）')
    lines.append('')
    lines.append('读法：与某维度分同源输入的代理，若带内无分化 = 信息已被该维度计价，'
                 '只宜展示不宜再加权；带内仍有稳定分化 = 存在增量信息（样本门槛同前）。')
    lines.append('')
    lines.append('### 资金面分带 × 主力/散户/杠杆（同源：资金面子项）')
    lines.append('')
    for p in ('main_flow5', 'retail5', 'margin', 'combo'):
        lines += render_control_table(extras['by_dim']['capital_score'], p, '资金面分带')
    lines.append('### 消息面分带 × 新闻情绪（同源：消息面子项）')
    lines.append('')
    lines += render_control_table(extras['by_dim']['sentiment_score'], 'news', '消息面分带')
    lines.append('### 技术面分带 × 量比（同源：日线量比子项）')
    lines.append('')
    lines += render_control_table(extras['by_dim']['technical_score'], 'vol_ratio', '技术面分带')
    lines.append('## 五、显著成对分化汇总（两侧各 n≥20 且差 ≥15pp，T+5）')
    lines.append('')
    pairs = notable_pairs(store_all)
    if pairs:
        lines.append('| 代理 | 高桶 | 低桶 | 差 |')
        lines.append('|---|---|---|---|')
        for f in pairs[:20]:
            lines.append(f'| {PROXY_TITLES[f["proxy"]]} | {f["hi"]}：{f["hi_stat"]} '
                         f'| {f["lo"]}：{f["lo_stat"]} | {f["gap_pp"]:+.0f}pp |')
    else:
        lines.append('（当前样本下无满足门槛的显著分化）')
    lines.append('')
    lines.append('### 增量判定速览（档内最大桶差，两侧各 n≥20 才计）')
    lines.append('')
    lines.append('| 代理 | 评级档内最大桶差 |')
    lines.append('|---|---|')
    for p, sp in spread_rows:
        d = sp['detail'] or '—（无满足门槛的档内分化）'
        lines.append(f'| {PROXY_TITLES[p]} | {d} |')
    lines.append('')
    lines.append('## 六、用户行为统计（trade_records 只读描述，C 级样本不作依据）')
    lines.append('')
    b = behavior
    lines.append(f'- 样本：{b["n_trades"]} 笔流水 / {b["n_stocks"]} 只股票 '
                 f'（买 {b["n_buy"]} / 卖 {b["n_sell"]}）——**C 级，仅描述不作依据**；'
                 '成本为「历史买入 VWAP」近似（未含费与历史持仓快照）。')
    ab, tb = b['avg_down']['n'], max(1, b['avg_down']['total_buy'])
    lines.append(f'- 摊薄加仓（买入价低于此前买入均价）：{ab}/{b["avg_down"]["total_buy"]} 笔'
                 f'（{ab / tb * 100:.0f}%）')
    hb, htot = b['high_pos_buy']['n'], max(1, b['high_pos_buy']['total'])
    lb = b['low_pos_buy']['n']
    lines.append(f'- 买入位置（近60日K线分位）：高位区(≥0.7) {hb}/{b["high_pos_buy"]["total"]} 笔'
                 f'（{hb / htot * 100:.0f}%）、低位区(≤0.3) {lb} 笔')
    re_ = b['reentry']
    lines.append(f'- 卖出后 30 日内再入场：{re_["n"]} 次（其中再入价更低 {re_["lower"]} 次）')
    ls, lt = b['loss_sell']['n'], max(1, b['loss_sell']['total_sell'])
    lines.append(f'- 浮亏卖出（卖价<买入VWAP）：{ls}/{b["loss_sell"]["total_sell"]} 笔'
                 f'（{ls / lt * 100:.0f}%）')
    lines.append('')
    lines.append('## 七、口径声明')
    lines.append('')
    lines.append('- 样本期为回测窗口（约 50 个交易日），非全周期检验；'
                 'T+20 样本天然远少于 T+5（回测窗口尚不足 20 个交易日的行无 T+20）。')
    lines.append('- 换手率字段失活：raw_kline.turnover 回测窗口内恒为 0.0（2026-07 起），'
                 'S5a 当前**不可检验**；历史行（窗口前）有值但无对齐样本。')
    lines.append('- 中小单与主力为机械镜像：单行 main+medium+small≡0（逐行实测中位偏差'
                 ' ~1e-13），故 S3a（散户·中小单口径）≈ −S2a，二者是同一信息的正反面，'
                 '2×2 组合只剩 2 格非空——**中小单口径不构成独立散户情绪源**；'
                 '独立的散户筹码信号仅股东户数（低频披露）。')
    lines.append('- 股东户数为季度/月度披露：本检验加 180 日新鲜度守卫；评分面'
                 ' _read_holder_structure 取最新一期**无 staleness 守卫**（观察项，见方案）。')
    lines.append('- 量比与技术面「量比」子项同源输入；主力/杠杆与资金面子项同源输入——'
                 '对照②正是为检验「重复计价」而设。')
    lines.append('- A/H 独立（R20）：市场间数字严禁互推。')
    lines.append('')
    return '\n'.join(lines)


# ================================================================
# 主流程
# ================================================================


def main() -> int:
    parser = argparse.ArgumentParser(description='021BW 情绪代理证据查询（只读）')
    parser.add_argument('--db', default=None, help='SQLite 路径（默认 config.DB_PATH）')
    parser.add_argument('--market', default=None, choices=['a_stock', 'hk_stock'],
                        help='只统计指定市场（缺省=全市场）')
    parser.add_argument('--out', default=None, help='markdown 输出路径')
    parser.add_argument('--json', dest='json_out', default=None, help='JSON 输出路径')
    args = parser.parse_args()

    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass

    con = open_ro_db(args.db)
    cur = con.cursor()
    rows, store_all, store_by_rating, extras = build_stores(cur, args.market)
    behavior = behavior_stats(cur)
    dates = sorted({_date(r['rating_date']) for r in rows})
    meta = {
        'db': args.db or DB_PATH,
        'n_br': len(rows),
        'date_range': f'{dates[0]} ~ {dates[-1]}' if dates else '',
        'market': args.market,
    }
    con.close()

    md = render(rows, store_all, store_by_rating, extras, behavior, args.market, meta)
    print(md)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(md + '\n')
        print(f'\n[已写出] {args.out}', file=sys.stderr)
    if args.json_out:
        payload = {
            'meta': meta,
            'coverage': extras['coverage'],
            'overall': {p: {b: c for b, c in buckets.items()}
                        for p, buckets in store_all.items()},
            'by_rating': {p: {f'{g}|{b}': c for (g, b), c in buckets.items()}
                          for p, buckets in store_by_rating.items()},
            'by_dim_band': {d: {p: {f'{g}|{b}': c for (g, b), c in buckets.items()}
                                for p, buckets in stores.items()}
                            for d, stores in extras['by_dim'].items()},
            'notable_pairs': notable_pairs(store_all),
            'within_tier': {p: within_tier_spread(store_by_rating, p)
                            for p in ALL_PROXIES},
            'behavior': behavior,
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        print(f'[已写出] {args.json_out}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
