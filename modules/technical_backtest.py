# -*- coding: utf-8 -*-
"""020R-51-B：技术面专项历史回测

用当前 v5 技术面评分规则（月线方向/周线波段/日线择时 7 子项 + 月线空头×0.85 惩罚）
按历史时点逐日重算技术面得分，统计 T+5 / T+20 方向命中率与区间收益。

规则一致性保证：
- 指标计算直接复用 data_adapter 的私有函数（_calc_ma/_calc_macd/_calc_rsi/
  _calc_bollinger/_calc_volume_ratio/_calc_kdj），与实盘评分完全同源；
- 日线指标窗口取最近 60 根日线（与 load_stockdata_from_db 的 limit=60 一致）；
- 周线按 ISO 自然周、月线按自然月聚合（与 aggregate_period_klines 一致）；
- 维度评分复用 scoring_engine.score_dimension + TECHNICAL_SUBITEMS（当前规则）；
- 月线空头惩罚（×0.85）与 scoring_engine.analyze() 一致。

诚实边界：
- 仅覆盖技术面维度；四维综合评级无法历史重算（基本面/资金面/消息面缺少时点快照，
  强行重算会引入前视偏差）。
- 样本为 (股票, 交易日) 观测，个股间日期不完全对齐、存在横截面相关，
  统计结论仅供技术面规则有效性参考，不代表综合评级回测结果。
"""

import os
import sys
from datetime import date as _date
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import get_connection
from modules.data_adapter import (
    _calc_bollinger,
    _calc_kdj,
    _calc_ma,
    _calc_macd,
    _calc_rsi,
    _calc_volume_ratio,
)
from modules.data_contract import StockData
from modules.scoring_engine import TECHNICAL_SUBITEMS, score_dimension

DAILY_WINDOW = 60  # 与 data_adapter._read_kline_data(stock_id, limit=60) 一致
WEEKLY_MIN = 20  # 与 load_stockdata_from_db 周线门槛一致
MONTHLY_MIN = 5  # 月线门槛一致
MIN_DAILY_BARS = 60  # 参与回测的最少日线根数（保证日线指标可算）

BULL = 65.0  # 偏多阈值
BEAR = 45.0  # 偏空阈值

logger = __import__('logging').getLogger(__name__)


def _iso_week_key(d: _date) -> tuple:
    y, w, _ = d.isocalendar()
    return (y, w)


def _month_key(d: _date) -> tuple:
    return (d.year, d.month)


def _score_technical_at(closes, volumes, highs, lows, date_str, wcloses, mcloses):
    """在指定历史时点用当前技术面规则打分。

    Args:
        closes/volumes/highs/lows: 截至当日的全量序列（升序，含当日）
        date_str: 当日 YYYYMMDD
        wcloses/mcloses: 截至当日的周线/月线收盘序列（升序）
    Returns:
        (score or None, detail)
    """
    # 日线指标：最近 60 根（与实盘一致）
    dc = closes[-DAILY_WINDOW:]
    dv = volumes[-DAILY_WINDOW:]
    kline_rows = [
        {'high': highs[i], 'low': lows[i], 'close': closes[i]}
        for i in range(len(closes) - DAILY_WINDOW, len(closes))
    ]

    ma5 = _calc_ma(dc, 5)
    ma10 = _calc_ma(dc, 10)
    ma20 = _calc_ma(dc, 20)
    ma60 = _calc_ma(dc, 60)
    rsi_14 = _calc_rsi(dc, 14)
    boll_upper, _mid, boll_lower = _calc_bollinger(dc, 20)
    macd_dif, macd_dea = _calc_macd(dc)
    kdj_k = _calc_kdj(kline_rows)
    volume_ratio = _calc_volume_ratio(dv)

    # 周线指标（≥20 根才计算，与实盘一致）
    weekly_ma10 = weekly_ma20 = weekly_macd_dif = weekly_macd_dea = None
    weekly_rsi14 = weekly_boll_position = None
    if len(wcloses) >= WEEKLY_MIN:
        weekly_ma10 = _calc_ma(wcloses, 10)
        weekly_ma20 = _calc_ma(wcloses, 20)
        weekly_macd_dif, weekly_macd_dea = _calc_macd(wcloses)
        weekly_rsi14 = _calc_rsi(wcloses, 14)
        wup, _wmid, wlow = _calc_bollinger(wcloses, 20)
        if wup is not None and wlow is not None and wup - wlow > 0:
            weekly_boll_position = max(
                0.0, min(100.0, (wcloses[-1] - wlow) / (wup - wlow) * 100)
            )

    # 月线指标（≥5 根才计算，与实盘一致）
    monthly_ma5 = monthly_ma10 = monthly_macd_dif = monthly_macd_dea = None
    if len(mcloses) >= MONTHLY_MIN:
        monthly_ma5 = _calc_ma(mcloses, 5)
        monthly_ma10 = _calc_ma(mcloses, 10)
        monthly_macd_dif, monthly_macd_dea = _calc_macd(mcloses)

    data = StockData(
        code='000000.XSHE',
        market='A',
        trade_date=date_str,
        close=float(closes[-1]),
        # 日线技术指标
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma60=ma60,
        macd_dif=macd_dif,
        macd_dea=macd_dea,
        kdj_k=kdj_k,
        rsi_14=rsi_14,
        volume=int(volumes[-1]) if volumes[-1] else None,
        volume_ratio=volume_ratio,
        boll_upper=boll_upper,
        boll_lower=boll_lower,
        # 周线/月线多周期指标
        weekly_ma10=weekly_ma10,
        weekly_ma20=weekly_ma20,
        weekly_macd_dif=weekly_macd_dif,
        weekly_macd_dea=weekly_macd_dea,
        weekly_rsi14=weekly_rsi14,
        weekly_boll_position=weekly_boll_position,
        monthly_ma5=monthly_ma5,
        monthly_ma10=monthly_ma10,
        monthly_macd_dif=monthly_macd_dif,
        monthly_macd_dea=monthly_macd_dea,
    )

    score, detail = score_dimension(data, TECHNICAL_SUBITEMS, 'technical')
    # 020R-48：月线空头（MA5<MA10）时技术面得分 ×0.85（与 analyze() 一致）
    if (
        score is not None
        and monthly_ma5 is not None
        and monthly_ma10 is not None
        and monthly_ma5 < monthly_ma10
    ):
        score = round(score * 0.85, 1)
        detail['monthly_penalty'] = '月线空头(MA5<MA10)，技术面得分×0.85'
    return score, detail


def _bucket(score: float) -> str:
    if score >= BULL:
        return '偏多'
    if score <= BEAR:
        return '偏空'
    return '中性'


def _stats(rows: list[dict], period: str) -> dict:
    """对一组观测行统计 T+N 区间收益与方向命中率。

    方向命中率仅在偏多/偏空观测上计算（中性无方向预期，不计入分母）。
    """
    rets = [r[f'ret_{period}'] for r in rows if r.get(f'ret_{period}') is not None]
    n = len(rets)
    if not rets:
        return {'n': 0, 'avg': None, 'median': None, 'pos_rate': None, 'dir_hit': None}
    hits = 0
    judged = 0
    for r in rows:
        ret = r.get(f'ret_{period}')
        if ret is None:
            continue
        bucket = _bucket(r['score'])
        if bucket == '偏多':
            judged += 1
            if ret > 0:
                hits += 1
        elif bucket == '偏空':
            judged += 1
            if ret < 0:
                hits += 1
    pos = sum(1 for x in rets if x > 0)
    sorted_rets = sorted(rets)
    median = sorted_rets[len(sorted_rets) // 2]
    return {
        'n': n,
        'avg': round(sum(rets) / n * 100, 2),
        'median': round(median * 100, 2),
        'pos_rate': round(pos / n, 4),
        'dir_hit': round(hits / judged, 4) if judged else None,
    }


def run_technical_backtest(market=None):
    """对自选股执行技术面专项历史回测，返回统计字典。"""
    conn = get_connection()
    stocks = conn.execute(
        "SELECT id, symbol, name, market FROM stocks WHERE status='active' ORDER BY id"
    ).fetchall()
    stocks = [dict(r) for r in stocks]
    if market:
        stocks = [s for s in stocks if s['market'] == market]

    all_rows = []  # 每股每日一条：{symbol, name, market, date, score, ret_5, ret_20}
    stock_summaries = []
    skipped = []

    for st in stocks:
        krows = conn.execute(
            'SELECT trade_date, open, high, low, close, volume FROM raw_kline '
            'WHERE stock_id=? ORDER BY trade_date ASC',
            (st['id'],),
        ).fetchall()
        if len(krows) < MIN_DAILY_BARS:
            skipped.append(f"{st['symbol']} {st['name']}({len(krows)}根)")
            continue

        dates = [r['trade_date'] for r in krows]
        closes = [float(r['close'] or 0) for r in krows]
        highs = [float(r['high'] or 0) for r in krows]
        lows = [float(r['low'] or 0) for r in krows]
        volumes = [float(r['volume'] or 0) for r in krows]
        ddates = [_date.fromisoformat(str(d)[:10]) for d in dates]

        wcloses = []
        mcloses = []
        n = len(krows)
        stock_rows = []
        for i in range(MIN_DAILY_BARS - 1, n):
            # 增量维护周线/月线收盘序列
            if i == 0 or _iso_week_key(ddates[i]) != _iso_week_key(ddates[i - 1]):
                if i > 0:
                    wcloses.append(closes[i - 1])
            if i == 0 or _month_key(ddates[i]) != _month_key(ddates[i - 1]):
                if i > 0:
                    mcloses.append(closes[i - 1])

            date_str = str(dates[i])[:10].replace('-', '')
            # 实盘聚合含"当前未完成周/月"的部分K线（当日采集后即参与聚合），
            # 故打分时把当根收盘并入周/月序列，与 raw_kline_weekly/monthly 口径一致
            score, _detail = _score_technical_at(
                closes[: i + 1],
                volumes[: i + 1],
                highs[: i + 1],
                lows[: i + 1],
                date_str,
                wcloses + [closes[i]],
                mcloses + [closes[i]],
            )
            if score is None:
                continue
            ret5 = closes[i + 5] / closes[i] - 1 if i + 5 < n else None
            ret20 = closes[i + 20] / closes[i] - 1 if i + 20 < n else None
            row = {
                'symbol': st['symbol'],
                'name': st['name'],
                'market': st['market'],
                'date': date_str,
                'score': score,
                'ret_5': ret5,
                'ret_20': ret20,
            }
            stock_rows.append(row)
            all_rows.append(row)

        # 每股小计
        sr5 = [r for r in stock_rows if r['ret_5'] is not None]
        sr20 = [r for r in stock_rows if r['ret_20'] is not None]
        stock_summaries.append(
            {
                'symbol': st['symbol'],
                'name': st['name'],
                'market': st['market'],
                'n': len(stock_rows),
                'avg_score': round(sum(r['score'] for r in stock_rows) / len(stock_rows), 1)
                if stock_rows
                else None,
                'avg_ret5': round(sum(r['ret_5'] for r in sr5) / len(sr5) * 100, 2) if sr5 else None,
                'avg_ret20': round(sum(r['ret_20'] for r in sr20) / len(sr20) * 100, 2)
                if sr20
                else None,
            }
        )
    conn.close()

    # 分档统计
    buckets = {}
    for b in ('偏多', '中性', '偏空'):
        rows = [r for r in all_rows if _bucket(r['score']) == b]
        buckets[b] = {
            'n_all': len(rows),
            't5': _stats(rows, '5'),
            't20': _stats(rows, '20'),
        }

    overall = {
        'n_all': len(all_rows),
        't5': _stats(all_rows, '5'),
        't20': _stats(all_rows, '20'),
    }
    # 基准：全部观测点的朴素持有 T+20 平均（与分档对照）
    bench20 = [r['ret_20'] for r in all_rows if r['ret_20'] is not None]

    return {
        'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'thresholds': {'bull': BULL, 'bear': BEAR},
        'stocks_total': len(stocks),
        'skipped': skipped,
        'samples': len(all_rows),
        'overall': overall,
        'buckets': buckets,
        'benchmark_avg_ret20': round(sum(bench20) / len(bench20) * 100, 2) if bench20 else None,
        'stock_summaries': sorted(stock_summaries, key=lambda x: -(x['n'] or 0)),
    }


def _fmt_pct(x):
    return '—' if x is None else f'{x:+.2f}%'


def _fmt_ratio(x):
    return '—' if x is None else f'{x:.1%}'


def render_markdown(result: dict) -> str:
    """将回测统计渲染为 Markdown 报告。"""
    L = []
    L.append('# 技术面专项历史回测报告（020R-51-B）')
    L.append('')
    L.append(f"- 生成时间：{result['generated_at']}")
    L.append(f"- 评分口径：当前 v5 技术面 7 子项（月线方向 0.25 / 周线波段 0.45 / 日线择时 0.30）+ 月线空头 ×0.85 惩罚")
    L.append(f"- 分档：得分 ≥ {result['thresholds']['bull']:.0f} = 偏多；≤ {result['thresholds']['bear']:.0f} = 偏空；其余 = 中性")
    L.append(f"- 样本：{result['samples']} 个 (股票×交易日) 观测；跳过 {len(result['skipped'])} 只（日线不足 60 根）：{', '.join(result['skipped']) or '无'}")
    L.append('')
    L.append('> ⚠️ 诚实边界：本回测**仅覆盖技术面维度**，不代表四维综合评级；样本存在横截面相关，结论仅供规则参考。')
    L.append('')
    L.append('## 一、分档统计（T+5 / T+20）')
    L.append('')
    L.append('| 分档 | 观测数 | T+5 平均 | T+5 中位 | T+5 上涨率 | T+5 方向命中 | T+20 平均 | T+20 中位 | T+20 上涨率 | T+20 方向命中 |')
    L.append('|---|---|---|---|---|---|---|---|---|---|')
    for b in ('偏多', '中性', '偏空'):
        st = result['buckets'][b]
        L.append(
            f"| {b} | {st['n_all']} | {_fmt_pct(st['t5']['avg'])} | {_fmt_pct(st['t5']['median'])} "
            f"| {_fmt_ratio(st['t5']['pos_rate'])} | {_fmt_ratio(st['t5']['dir_hit'])} "
            f"| {_fmt_pct(st['t20']['avg'])} | {_fmt_pct(st['t20']['median'])} "
            f"| {_fmt_ratio(st['t20']['pos_rate'])} | {_fmt_ratio(st['t20']['dir_hit'])} |"
        )
    ov = result['overall']
    L.append(
        f"| **全部观测** | {ov['n_all']} | {_fmt_pct(ov['t5']['avg'])} | {_fmt_pct(ov['t5']['median'])} "
        f"| {_fmt_ratio(ov['t5']['pos_rate'])} | {_fmt_ratio(ov['t5']['dir_hit'])} "
        f"| {_fmt_pct(ov['t20']['avg'])} | {_fmt_pct(ov['t20']['median'])} "
        f"| {_fmt_ratio(ov['t20']['pos_rate'])} | {_fmt_ratio(ov['t20']['dir_hit'])} |"
    )
    L.append('')
    L.append(f"- 基准对照：全部观测点朴素持有 T+20 平均 **{_fmt_pct(result['benchmark_avg_ret20'])}**")
    L.append('- 方向命中定义：偏多且 T+N 收益 > 0，或偏空且 T+N 收益 < 0 计为命中。')
    L.append('')
    L.append('## 二、个股摘要（按观测数排序）')
    L.append('')
    L.append('| 代码 | 名称 | 市场 | 观测数 | 平均技术分 | T+5 平均 | T+20 平均 |')
    L.append('|---|---|---|---|---|---|---|')
    for s in result['stock_summaries']:
        mk = 'A' if s['market'] == 'a_stock' else 'H'
        L.append(
            f"| {s['symbol']} | {s['name']} | {mk} | {s['n']} | {s['avg_score']} "
            f"| {_fmt_pct(s['avg_ret5'])} | {_fmt_pct(s['avg_ret20'])} |"
        )
    L.append('')
    L.append('## 三、方法与边界')
    L.append('')
    L.append('1. 仅用当日及以前数据逐日重算（无前视）；日线指标窗口=最近 60 根日线，与实盘评分一致。')
    L.append('2. 周线/月线按 ISO 自然周/自然月聚合，与采集层 aggregate_period_klines 口径一致。')
    L.append('3. 指标与维度评分函数直接复用生产代码（data_adapter + scoring_engine.score_dimension），保证规则零漂移。')
    L.append('4. 月线数据不足 5 根/周线不足 20 根时对应子项按生产降级链处理（权重归零/降权），早期样本技术分口径略窄。')
    L.append('5. 个股间日期不完全对齐（停牌差异），观测非独立；分档差异需长期稳定才具参考意义。')
    L.append('')
    return '\n'.join(L)


def run_and_save(market=None, out_dir=None):
    """执行回测并把 Markdown 报告写入 reports/ 目录，返回 (result, report_path)。"""
    result = run_technical_backtest(market=market)
    md = render_markdown(result)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_dir = out_dir or os.path.join(root, 'reports')
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(
        out_dir, f"technical_backtest_{datetime.now().strftime('%Y%m%d')}.md"
    )
    with open(path, 'w', encoding='utf-8') as f:
        f.write(md)
    logger.info(f'技术面专项回测报告已写入: {path}')
    return result, path


if __name__ == '__main__':
    res, p = run_and_save()
    print(f'samples={res["samples"]} report={p}')
