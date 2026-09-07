"""动态窗口目标权重优化器（M9-dynamic，021AI）
=============================================

背景（021AG/021AH 诊断链）：
  - M9 周度优化器（optimizer_engine）以 T+1 日准确率为代理调权，且其"安全阀"
    比较写入权重前后的存量回测行——权重写入不改变存量行，阀门永远不触发
    （strategy_params 史料：before==after 全部相等）。
  - 用户目标（C）：以"评级有效期内方向命中"（动态窗口准确率，021AH 修正口径）
    为目标重配维度权重。

方法（历史重放 + 网格搜索 + 前向验证）：
  1. 重放：对每个 (股票, 评级日) 用存量维度分 × 候选权重 → 总分 → 评级
     （市场差异化门槛 + 021AG 迟滞 ±3 分）→ 改评点 → 动态窗口 →
     021AH 判定（观望边界 ±1% 模糊不计入）→ 准确率。
  2. 网格：四维权重 0.05 步长、单维 ∈ [0.05, 0.50]、和为 1，全枚举。
  3. 前向验证（walk-forward）：按日期 70/30 切分，训练段选优（并列取离
     当前权重最近者），测试段验证——测试段不参与选择。
  4. 数据门槛（防过拟合）：v5 纯净评级日 < MIN_V5_RATING_DAYS 或训练/测试
     可判定窗口 < MIN_JUDGED_WINDOWS 时，只出诊断报告不改权重。

诚实性约束：
  - 维度分跨引擎切换点（2026-08-14）均值漂移 7~11 分——两代数据混池只作
    pilot 参考，正式改权重必须等 v5 纯净样本足够；
  - 行业覆盖股票（industry_overrides 命中）用固定行业权重，不受基座候选
    影响——搜索评估只纳入非覆盖股票（纯归因），港股样本过薄不动；
  - 测试段提升 < MIN_TEST_GAIN 不推荐改权（宁可不动）。

入口：scripts/run_dynamic_optimizer.py（零代码用户中文报告）。
"""

import itertools
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import RATING_HYSTERESIS_ENABLED, RATING_HYSTERESIS_MARGIN
from database.db_manager import get_connection
from modules.backtest_engine import _judge
from modules.rating_hysteresis import get_effective_thresholds

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8))
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_WEIGHTS_FILE = os.path.join(_ROOT, 'config_weights.json')

DIMS = ('kline', 'fundamental', 'capital_flow', 'news')
DIM_COLS = {
    'kline': 'technical_score',
    'fundamental': 'fundamental_score',
    'capital_flow': 'capital_score',
    'news': 'sentiment_score',
}

# 网格与门槛
GRID_STEP = 0.05
WEIGHT_MIN, WEIGHT_MAX = 0.05, 0.50
TRAIN_RATIO = 0.70
MIN_V5_RATING_DAYS = 300  # v5 纯净评级日门槛（当前约 8 天/周积累，约一个月后达标）
MIN_JUDGED_WINDOWS = 100  # 训练/测试段各自的可判定窗口下限
MIN_TEST_GAIN = 0.03  # 测试段相对当前权重的最低提升（3pp）


def _load_config():
    with open(_WEIGHTS_FILE, encoding='utf-8') as f:
        return json.load(f)


def _rating_from_score(total, thresholds):
    """总分 → 档位（与 scoring_engine._map_rating 同序：按 min 降序取首个 ≥）。"""
    for grade, info in sorted(thresholds.items(), key=lambda x: x[1]['min'], reverse=True):
        if total >= info['min']:
            return grade
    return min(thresholds.items(), key=lambda x: x[1]['min'])[0]


def load_replay_data(market='a_stock', exclude_override=True):
    """加载重放数据：每个 (股票, 评级日) 的维度分 + 当日价格。

    Returns:
        dict: {stock_id: [{'date', 'price', 'dims': {dim: score}, 'engine'}]}
        meta: {'override_excluded': [stock_id], 'v5_days': n, 'total_days': n}
    """
    config = _load_config()
    overrides = set(config.get('industry_overrides', {}) or {})

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        f"""
        SELECT rh.stock_id, rh.rating_date, rh.price_at_rating, rh.engine_version,
               s.industry,
               {', '.join(DIM_COLS[d] for d in DIMS)}
        FROM ratings_history rh
        JOIN stocks s ON s.id = rh.stock_id
        JOIN analysis_results ar ON ar.stock_id = rh.stock_id
            AND ar.analysis_date = rh.rating_date
        WHERE s.market = ? AND rh.price_at_rating > 0
        ORDER BY rh.stock_id, rh.rating_date
        """,
        (market,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    by_stock = {}
    excluded = []
    v5_days = 0
    for r in rows:
        sid = r['stock_id']
        if sid not in by_stock:
            # A股行业覆盖命中 → 固定行业权重，排除出搜索（纯归因）
            if exclude_override and (r['industry'] or '') in overrides:
                excluded.append(sid)
                by_stock[sid] = None  # 占位标记排除
                continue
            by_stock[sid] = []
        if by_stock[sid] is None:
            continue
        dims = {d: r[DIM_COLS[d]] for d in DIMS}
        if any(v is None for v in dims.values()):
            continue  # 缺维行跳过（重放要求四维齐备）
        if r['engine_version'] == 'v5':
            v5_days += 1
        by_stock[sid].append(
            {
                'date': r['rating_date'],
                'price': r['price_at_rating'],
                'dims': dims,
                'engine': r['engine_version'],
            }
        )
    data = {k: v for k, v in by_stock.items() if v}
    meta = {
        'override_excluded': excluded,
        'v5_days': v5_days,
        'total_days': sum(len(v) for v in data.values()),
        'stocks': len(data),
    }
    return data, meta


def replay_rating_sequence(days, weights, market):
    """单股票序列重放：候选权重 → 总分 → （迟滞后）评级序列与改评点。"""
    thresholds = get_effective_thresholds(market)
    seq = []
    prev_rating = None
    for d in days:
        avail = {k: v for k, v in d['dims'].items() if v is not None}
        w_sum = sum(weights[k] for k in avail)
        total = sum(weights[k] / w_sum * v for k, v in avail.items()) if w_sum > 0 else 50.0
        raw = _rating_from_score(total, thresholds)
        final = raw
        if RATING_HYSTERESIS_ENABLED and prev_rating is not None and raw != prev_rating:
            # 与 rating_hysteresis.apply_hysteresis 同规则（此处内联避免循环依赖）
            raw_rank = thresholds[raw]['min']
            prev_rank = thresholds[prev_rating]['min']
            if raw_rank > prev_rank:
                if total < thresholds[raw]['min'] + RATING_HYSTERESIS_MARGIN:
                    final = prev_rating
            else:
                if total >= thresholds[prev_rating]['min'] - RATING_HYSTERESIS_MARGIN:
                    final = prev_rating
        seq.append({'date': d['date'], 'price': d['price'], 'rating': final})
        prev_rating = final
    return seq


def build_windows(data, weights, market='a_stock'):
    """全部股票重放 → 动态窗口列表 [(start_date, rating, ret, judged)]。"""
    windows = []
    for sid, days in data.items():
        seq = replay_rating_sequence(days, weights, market)
        change_idx = [i for i in range(1, len(seq)) if seq[i]['rating'] != seq[i - 1]['rating']]
        for i in range(len(seq)):
            nxt = next((j for j in change_idx if j > i), None)
            if nxt is None:
                continue
            p0, p1 = seq[i]['price'], seq[nxt]['price']
            if not p0 or not p1:
                continue
            ret = (p1 / p0 - 1) * 100
            judged = _judge(seq[i]['rating'], ret, market, neutral_borderline=True)
            windows.append((seq[i]['date'], seq[i]['rating'], ret, judged))
    return windows


def score_windows(windows, split_date=None):
    """汇总窗口准确率。split_date 给定时按窗口起始日分段。"""
    def _agg(ws):
        judged = [w for w in ws if w[3] is not None]
        n = len(judged)
        c = sum(w[3] for w in judged)
        by_rating = {}
        for w in judged:
            d = by_rating.setdefault(w[1], [0, 0])
            d[0] += w[3]
            d[1] += 1
        directional = [w for w in judged if w[1] in ('强烈推荐买入', '推荐买入', '建议减仓', '强烈建议卖出')]
        dn = len(directional)
        dc = sum(w[3] for w in directional)
        return {
            'n_judged': n,
            'correct': c,
            'accuracy': round(c / n, 4) if n else None,
            'by_rating': {k: (v[0], v[1]) for k, v in by_rating.items()},
            'directional_n': dn,
            'directional_accuracy': round(dc / dn, 4) if dn else None,
        }

    if split_date is None:
        return _agg(windows)
    train = [w for w in windows if w[0] <= split_date]
    test = [w for w in windows if w[0] > split_date]
    return {'train': _agg(train), 'test': _agg(test)}


def grid_candidates():
    """四维权重网格：步长 0.05，单维 [0.05,0.50]，和为 1（全枚举）。"""
    units = int(round(1.0 / GRID_STEP))
    lo, hi = int(round(WEIGHT_MIN / GRID_STEP)), int(round(WEIGHT_MAX / GRID_STEP))
    out = []
    for combo in itertools.product(range(lo, hi + 1), repeat=4):
        if sum(combo) == units:
            w = dict(zip(DIMS, (round(c * GRID_STEP, 2) for c in combo)))
            out.append(w)
    return out


def search(market='a_stock', verbose=True):
    """主入口：重放 + 网格 + 前向验证 + 门槛裁决。

    Returns:
        dict: {gate_passed, recommendation, current_weights, best_weights,
               metrics_current, metrics_best, split_date, meta, candidates_tried}
    """
    config = _load_config()
    current = dict(config[market]['weights'])

    data, meta = load_replay_data(market)
    if not data:
        return {'gate_passed': False, 'recommendation': '无重放数据', 'meta': meta}

    all_windows = build_windows(data, current, market)
    dates = sorted({w[0] for w in all_windows})
    if len(dates) < 5:
        return {'gate_passed': False, 'recommendation': '窗口样本过少', 'meta': meta}
    split_date = dates[int(len(dates) * TRAIN_RATIO)]

    base = score_windows(all_windows, split_date)
    metrics_current = {'train': base['train'], 'test': base['test'],
                       'full': score_windows(all_windows)}

    def _l1(w):
        return sum(abs(w[d] - current[d]) for d in DIMS)

    # 数据门槛：v5 纯净样本不足 → pilot（混池参考，不改权重）
    gate_v5 = meta['v5_days'] >= MIN_V5_RATING_DAYS
    pilot_only = not gate_v5

    results = []
    for cand in grid_candidates():
        ws = build_windows(data, cand, market)
        s = score_windows(ws, split_date)
        tr, te = s['train'], s['test']
        if tr['n_judged'] < max(30, MIN_JUDGED_WINDOWS // 2):
            continue  # 训练窗口过少的向量无统计意义
        results.append({
            'weights': cand,
            'train_acc': tr['accuracy'],
            'train_n': tr['n_judged'],
            'test_acc': te['accuracy'],
            'test_n': te['n_judged'],
            'l1': _l1(cand),
        })

    results.sort(key=lambda r: (-(r['train_acc'] or 0), r['l1']))
    best = results[0] if results else None

    gate_windows = (
        best
        and base['train']['n_judged'] >= MIN_JUDGED_WINDOWS
        and base['test']['n_judged'] >= MIN_JUDGED_WINDOWS
    )
    gain = None
    if best and metrics_current['test']['accuracy'] is not None and best['test_acc'] is not None:
        gain = round(best['test_acc'] - metrics_current['test']['accuracy'], 4)
    gate_gain = gain is not None and gain >= MIN_TEST_GAIN
    gate_passed = (not pilot_only) and gate_windows and gate_gain

    if verbose:
        print(f'[dynamic_optimizer] market={market} stocks={meta["stocks"]} '
              f'days={meta["total_days"]} (v5 {meta["v5_days"]}) '
              f'override_excluded={len(meta["override_excluded"])} '
              f'candidates={len(results)} split={split_date}')

    return {
        'gate_passed': gate_passed,
        'pilot_only': pilot_only,
        'recommendation': (
            '采纳候选权重' if gate_passed
            else ('样本门槛未过（v5 纯净数据不足或测试段提升不足），维持当前权重'
                  if best else '无有效候选，维持当前权重')
        ),
        'current_weights': current,
        'best_weights': best['weights'] if best else None,
        'metrics_current': metrics_current,
        'metrics_best': ({'train_acc': best['train_acc'], 'train_n': best['train_n'],
                          'test_acc': best['test_acc'], 'test_n': best['test_n'],
                          'test_gain': gain} if best else None),
        'split_date': split_date,
        'meta': meta,
        'top5': [{'weights': r['weights'], 'train_acc': r['train_acc'],
                  'test_acc': r['test_acc'], 'test_n': r['test_n']} for r in results[:5]],
    }


def apply_weights(market, new_weights, reason):
    """（仅 gate_passed 时调用）写入 config_weights.json + strategy_params 审计。"""
    config = _load_config()
    old = dict(config[market]['weights'])
    config[market]['weights'] = {k: round(v, 4) for k, v in new_weights.items()}
    config['_更新时间'] = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M')
    config['_备注'] = (
        f'{market} 权重由 dynamic_optimizer（021AI）更新：{reason}。'
        '行业覆盖/评级门槛/其他市场不受影响。'
    )
    with open(_WEIGHTS_FILE, 'w', encoding='utf-8') as f:
        json.dump(config, f, ensure_ascii=False, indent=2)

    conn = get_connection()
    cursor = conn.cursor()
    now_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute(
        "INSERT OR REPLACE INTO strategy_params "
        '(market, param_type, param_key, param_value, updated_at) '
        "VALUES (?, 'optimization_log', ?, ?, ?)",
        (
            market,
            f'dynopt_{datetime.now(_CN_TZ).strftime("%Y%m%d_%H%M%S")}',
            json.dumps({'type': 'dynamic_optimizer', 'old': old, 'new': new_weights,
                        'reason': reason}, ensure_ascii=False),
            now_str,
        ),
    )
    conn.commit()
    conn.close()
    logger.info(f'[dynamic_optimizer] 权重已写入 market={market}: {new_weights}')
    return old
