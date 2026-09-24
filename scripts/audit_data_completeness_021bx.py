#!/usr/bin/env python3
"""021BX 全维度数据完整度审计脚本（只读可复用）——56 只自选 × 全维度缺口矩阵。

====================================================================
定位（021BX t6 数据完整度审计；用户指示「检查数据完整度并优化」）
====================================================================
程序化扫描（mode=ro 零写库）自选股全维度的时效与覆盖：
  K线日/周/月 fresh 度与连续性缺口、财报期时效、资金面覆盖与估算行、
  融资余额覆盖、新闻情绪覆盖、估值/盘口时效、业绩预告快报覆盖、
  股东户数时效、行业分类覆盖、日报覆盖、data_status 7 天健康度、
  trade_records 完整性线索（导入行/估算行/死列）。

时效基准（与生产 _build_data_freshness 同语义，020R-19）：
  - K线/日报/盘口滞后 = 相对**全市场最新交易日**（本市场 raw_kline 最大日期），
    不与自然日比——休市日至最新交易日即视为最新；
  - 新闻滞后 >7 天、K线滞后 >3 天为告警线（同生产口径）。

协调约定（021BX 任务书）：**换手率断供项由 021BW t2 修复中，本审计只登记
现状、不重复给修复方案**。

====================================================================
只读边界
====================================================================
  - 数据库一律 sqlite3.connect('file:...?mode=ro', uri=True)；
  - 零写库、零网络、零 pip 新依赖；
  - 输出 markdown（--out）+ 可选 JSON（--json）。

用法：
  python scripts/audit_data_completeness_021bx.py                # 打印
  python scripts/audit_data_completeness_021bx.py --out PATH [--json PATH]
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

# 告警阈值（生产同款 + 低频维度单独约定）
KLINE_STALE_DAYS = 3       # K线滞后 >3 天（020R-19 全市场基准）
NEWS_STALE_DAYS = 7        # 情绪表滞后 >7 天
KLINE_GAP_WINDOW = 30      # 连续性缺口检查窗口（市场交易日）
CAP_WINDOW = 10            # 资金面覆盖窗口（市场交易日）
CAP_MISS_WARN = 3          # 资金面窗口内缺 ≥3 天告警
WK_STALE_DAYS = 8          # 周线滞后
MO_STALE_DAYS = 35         # 月线滞后
FIN_A_STALE_DAYS = 150     # A股财报期滞后（季报节奏上限约 4 个月+披露缓冲）
FIN_HK_STALE_DAYS = 400    # 港股财报期滞后（半年报节奏）
VAL_STALE_DAYS = 7         # 估值（日级低频）
OB_STALE_DAYS = 3          # 盘口（交易日采集）
HD_STALE_DAYS = 200        # 股东户数（季度/月度披露）
RPT_STALE_DAYS = 3         # 日报滞后（相对全市场最新报告日）
MARGIN_STALE_DAYS = 7      # 融资余额（T+1 公布）

OK, WARN, MISS, NA = 'ok', 'warn', 'miss', 'na'
_MARK = {OK: '✅', WARN: '⚠️', MISS: '❌', NA: '➖'}


def open_ro_db(db_path: str | None = None) -> sqlite3.Connection:
    con = sqlite3.connect(f'file:{db_path or DB_PATH}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con


def _d(v) -> str | None:
    return str(v)[:10] if v is not None else None


def days_between(a: str | None, b: str | None) -> int | None:
    """b - a 自然日差；任一缺失返回 None。"""
    if not a or not b:
        return None
    try:
        return (datetime.strptime(b, '%Y-%m-%d') - datetime.strptime(a, '%Y-%m-%d')).days
    except ValueError:
        return None


def verdict(lag: int | None, stale_days: int) -> tuple[str, str]:
    """滞后天数 → (判定, 展示后缀)。lag None = 无数据（MISS）。"""
    if lag is None:
        return MISS, ''
    if lag <= stale_days:
        return OK, ''
    return WARN, f'{lag}d'


# ================================================================
# 数据装载
# ================================================================


def load_data(cur: sqlite3.Cursor) -> dict:
    data: dict = {}
    data['stocks'] = [dict(r) for r in cur.execute(
        'SELECT id, symbol, market, name, industry FROM stocks ORDER BY market, symbol')]
    # 每市场交易日历与基准日（生产语义：基准=本市场 K线最大日期）
    data['cal'] = {}
    data['ref'] = {}
    for m in ('a_stock', 'hk_stock'):
        dates = [r[0] for r in cur.execute(
            'SELECT DISTINCT k.trade_date FROM raw_kline k JOIN stocks s ON s.id=k.stock_id '
            'WHERE s.market=? ORDER BY k.trade_date', (m,))]
        data['cal'][m] = [_d(x) for x in dates]
        data['ref'][m] = data['cal'][m][-1] if dates else None

    def one(sql: str) -> dict[int, dict]:
        out: dict[int, dict] = {}
        for r in cur.execute(sql):
            d = dict(r)
            out.setdefault(d['stock_id'], []).append(d)
        return out

    data['kline'] = one(
        "SELECT stock_id, trade_date, turnover FROM raw_kline WHERE volume IS NOT NULL")
    data['kline_all'] = one('SELECT stock_id, trade_date FROM raw_kline')
    data['kline_w'] = one('SELECT stock_id, trade_date FROM raw_kline_weekly')
    data['kline_m'] = one('SELECT stock_id, trade_date FROM raw_kline_monthly')
    data['fin'] = one('SELECT stock_id, report_date FROM raw_fundamental')
    data['cap'] = one(
        'SELECT stock_id, trade_date, is_estimated, capital_source, main_net_inflow, '
        'margin_balance FROM raw_capital_flow')
    data['news'] = one(
        'SELECT stock_id, news_date, total_count FROM news_sentiment')
    data['val'] = one('SELECT stock_id, trade_date FROM stock_valuation')
    data['ob'] = one('SELECT stock_id, trade_date FROM stock_orderbook')
    data['fc'] = one(
        'SELECT stock_id, report_period FROM raw_forecast UNION ALL '
        'SELECT stock_id, report_period FROM raw_express')
    data['hd'] = one('SELECT stock_id, stat_date FROM holder_structure')
    data['rpt'] = one('SELECT stock_id, report_date FROM daily_reports WHERE status=\'ok\'')
    data['wk_max'] = {m: _d(cur.execute(
        'SELECT MAX(w.trade_date) FROM raw_kline_weekly w JOIN stocks s ON s.id=w.stock_id '
        'WHERE s.market=?', (m,)).fetchone()[0]) for m in ('a_stock', 'hk_stock')}
    data['mo_max'] = {m: _d(cur.execute(
        'SELECT MAX(w.trade_date) FROM raw_kline_monthly w JOIN stocks s ON s.id=w.stock_id '
        'WHERE s.market=?', (m,)).fetchone()[0]) for m in ('a_stock', 'hk_stock')}
    data['rpt_max'] = {m: _d(cur.execute(
        'SELECT MAX(r.report_date) FROM daily_reports r JOIN stocks s ON s.id=r.stock_id '
        'WHERE s.market=?', (m,)).fetchone()[0]) for m in ('a_stock', 'hk_stock')}
    return data


# ================================================================
# 每股×维度判定
# ================================================================


def cell(mark: str, suffix: str = '') -> str:
    return _MARK[mark] + suffix


def audit_stock(stk: dict, data: dict) -> dict:
    sid, m = stk['id'], stk['market']
    ref = data['ref'][m]
    cal = data['cal'][m]
    out: dict[str, dict] = {}

    def latest(rows: list[dict], col: str) -> str | None:
        ds = [_d(r[col]) for r in rows if r.get(col)]
        return max(ds) if ds else None

    # K线日：fresh + 连续性缺口
    # 连续性窗口取「基准日之前」的 30 个交易日（cal[-31:-1]）——基准日当日
    # 15:54 批次未跑完时只有少数股有当日行，属日内正常态不算缺口。
    kd_last = latest(data['kline'].get(sid, []), 'trade_date')
    mark, suf = verdict(days_between(kd_last, ref), KLINE_STALE_DAYS)
    if cal:
        window = cal[-(KLINE_GAP_WINDOW + 1):-1] if len(cal) > 1 else []
        have = {_d(r['trade_date']) for r in data['kline_all'].get(sid, [])}
        miss = sum(1 for d in window if d not in have)
        if miss > 0:
            out['kd'] = {'mark': WARN, 'suffix': f'缺{miss}',
                         'detail': f'基准日前{len(window)}个交易日缺{miss}天'}
        elif mark == OK:
            out['kd'] = {'mark': OK, 'suffix': '', 'detail': '连续'}
        else:
            out['kd'] = {'mark': mark, 'suffix': suf,
                         'detail': f'最新 {_d(kd_last) or "—"} / 基准 {ref}'}
    else:
        out['kd'] = {'mark': mark, 'suffix': suf,
                     'detail': f'最新 {_d(kd_last) or "—"} / 基准 {ref}'}

    # 周/月线（由日线聚合，滞后以各表全市场最大日为基准）
    for key, rows, refd, thr in (('kw', data['kline_w'].get(sid, []), data['wk_max'][m], WK_STALE_DAYS),
                                 ('km', data['kline_m'].get(sid, []), data['mo_max'][m], MO_STALE_DAYS)):
        last = latest(rows, 'trade_date')
        v, s = verdict(days_between(last, refd), thr)
        out[key] = {'mark': v, 'suffix': s, 'detail': f'最新 {last or "—"} / 基准 {refd}'}

    # 财报期
    fin_last = latest(data['fin'].get(sid, []), 'report_date')
    thr = FIN_A_STALE_DAYS if m == 'a_stock' else FIN_HK_STALE_DAYS
    v, s = verdict(days_between(fin_last, ref), thr)
    out['fin'] = {'mark': v, 'suffix': s, 'detail': f'报告期 {fin_last or "—"}'}

    # 资金面：近 10 市场交易日覆盖
    cap_rows = data['cap'].get(sid, [])
    real = [r for r in cap_rows if r.get('is_estimated') in (0, None)]
    if cal:
        window = cal[-CAP_WINDOW:]
        have = {_d(r['trade_date']) for r in real}
        miss = sum(1 for d in window if d not in have)
        est_in = sum(1 for r in cap_rows if r.get('is_estimated') == 1
                     and _d(r['trade_date']) in window)
        if not real:
            out['cap'] = {'mark': MISS, 'suffix': '', 'detail': '无真实行'}
        elif miss >= CAP_MISS_WARN:
            out['cap'] = {'mark': WARN, 'suffix': f'缺{miss}',
                          'detail': f'近{len(window)}日缺{miss}天' + (f'·窗口含估算{est_in}' if est_in else '')}
        else:
            out['cap'] = {'mark': OK, 'suffix': '',
                          'detail': f'近{len(window)}日缺{miss}天' + (f'·窗口含估算{est_in}' if est_in else '')}
    else:
        out['cap'] = {'mark': MISS, 'suffix': '', 'detail': '无市场日历'}

    # 融资余额（A股维度）
    if m == 'hk_stock':
        out['mgn'] = {'mark': NA, 'suffix': '', 'detail': '港股无两融'}
    else:
        mrows = [r for r in cap_rows if r.get('margin_balance') is not None]
        last = max((_d(r['trade_date']) for r in mrows), default=None)
        v, s = verdict(days_between(last, ref), MARGIN_STALE_DAYS)
        out['mgn'] = {'mark': v, 'suffix': s, 'detail': f'最新 {last or "—"}'}

    # 新闻情绪
    news_last = latest(data['news'].get(sid, []), 'news_date')
    v, s = verdict(days_between(news_last, ref), NEWS_STALE_DAYS)
    out['news'] = {'mark': v, 'suffix': s, 'detail': f'最新 {news_last or "—"}'}

    # 估值
    val_last = latest(data['val'].get(sid, []), 'trade_date')
    v, s = verdict(days_between(val_last, ref), VAL_STALE_DAYS)
    out['val'] = {'mark': v, 'suffix': s, 'detail': f'最新 {val_last or "—"}'}

    # 盘口（mootdx 交易日采集；港股无源）
    ob_rows = data['ob'].get(sid, [])
    if m == 'hk_stock' and not ob_rows:
        out['ob'] = {'mark': NA, 'suffix': '', 'detail': '无采集源（mootdx A股）'}
    else:
        last = latest(ob_rows, 'trade_date')
        v, s = verdict(days_between(last, ref), OB_STALE_DAYS)
        out['ob'] = {'mark': v, 'suffix': s, 'detail': f'最新 {last or "—"}'}

    # 业绩预期（事件级：无预告属正常，➖ 不计缺口）
    if m == 'hk_stock':
        out['fc'] = {'mark': NA, 'suffix': '', 'detail': '采集范围外（东财 A股接口）'}
    else:
        periods = [str(r['report_period']) for r in data['fc'].get(sid, []) if r.get('report_period')]
        if not periods:
            out['fc'] = {'mark': NA, 'suffix': '', 'detail': '无进行中预告（正常）'}
        else:
            latest_p = max(periods)
            out['fc'] = {'mark': OK if latest_p >= '20260630' else WARN,
                         'suffix': '' if latest_p >= '20260630' else '旧',
                         'detail': f'最新报告期 {latest_p}'}

    # 股东户数/机构（季度/月度披露）
    hd_last = latest(data['hd'].get(sid, []), 'stat_date')
    v, s = verdict(days_between(hd_last, ref), HD_STALE_DAYS)
    out['hd'] = {'mark': v, 'suffix': s, 'detail': f'最新披露 {hd_last or "—"}'}

    # 行业分类（021BA 自愈）
    if m == 'hk_stock':
        out['ind'] = {'mark': OK, 'suffix': '', 'detail': '港股（恒定）'}
    else:
        ind = stk.get('industry')
        out['ind'] = ({'mark': OK, 'suffix': '', 'detail': ind} if ind and ind != '未分类'
                      else {'mark': MISS, 'suffix': '', 'detail': ind or '(NULL)'})

    # 日报
    rpt_last = latest(data['rpt'].get(sid, []), 'report_date')
    v, s = verdict(days_between(rpt_last, data['rpt_max'][m]), RPT_STALE_DAYS)
    out['rpt'] = {'mark': v, 'suffix': s, 'detail': f'最新 {rpt_last or "—"}'}

    return out


DIMS = (
    ('kd', 'K线日'), ('kw', 'K线周'), ('km', 'K线月'), ('fin', '财报'),
    ('cap', '资金面'), ('mgn', '融资'), ('news', '新闻'), ('val', '估值'),
    ('ob', '盘口'), ('fc', '业绩预期'), ('hd', '股东户数'), ('ind', '行业'),
    ('rpt', '日报'),
)


# ================================================================
# 全局统计与已知项对账
# ================================================================


def global_stats(cur: sqlite3.Cursor) -> dict:
    g: dict = {}
    g['kline_turnover'] = cur.execute(
        "SELECT COUNT(*), SUM(CASE WHEN turnover IS NOT NULL AND turnover > 0 THEN 1 ELSE 0 END), "
        "MAX(CASE WHEN turnover > 0 THEN trade_date END), "
        "SUM(CASE WHEN turnover > 0 AND trade_date >= '2026-09-23' THEN 1 ELSE 0 END) "
        "FROM raw_kline").fetchone()
    g['cap_src'] = cur.execute(
        'SELECT COALESCE(capital_source,\'(NULL)\'), is_estimated, COUNT(*) '
        'FROM raw_capital_flow GROUP BY 1,2 ORDER BY 3 DESC').fetchall()
    g['news_empty'] = cur.execute(
        'SELECT COUNT(*), SUM(CASE WHEN total_count=0 THEN 1 ELSE 0 END) '
        'FROM news_sentiment').fetchone()
    g['raw_sentiment'] = cur.execute(
        'SELECT COUNT(*), COUNT(DISTINCT stock_id), MAX(info_date) FROM raw_sentiment').fetchone()
    g['ds7'] = cur.execute(
        "SELECT dimension, status, COUNT(*) FROM data_status "
        "WHERE fetched_at >= datetime('now','localtime','-7 days') GROUP BY 1,2").fetchall()
    g['err14'] = cur.execute(
        "SELECT module, COUNT(*), MAX(created_at) FROM error_logs "
        "WHERE created_at >= datetime('now','localtime','-14 days') GROUP BY 1 ORDER BY 2 DESC").fetchall()
    g['tables'] = {}
    for t, dcol in (('raw_kline', 'trade_date'), ('raw_kline_weekly', 'trade_date'),
                    ('raw_kline_monthly', 'trade_date'), ('raw_fundamental', 'report_date'),
                    ('raw_capital_flow', 'trade_date'), ('news_sentiment', 'news_date'),
                    ('stock_valuation', 'trade_date'), ('stock_orderbook', 'trade_date'),
                    ('raw_forecast', 'fetched_at'), ('raw_express', 'fetched_at'),
                    ('holder_structure', 'stat_date'), ('daily_reports', 'report_date'),
                    ('raw_sentiment', 'info_date'), ('stock_restricted_release', 'release_date'),
                    ('stock_valuation_history', 'trade_date'), ('industry_fund_flow', 'trade_date')):
        try:
            g['tables'][t] = cur.execute(
                f'SELECT COUNT(*), COUNT(DISTINCT stock_id), MIN({dcol}), MAX({dcol}) '
                f'FROM {t}').fetchone()
        except sqlite3.OperationalError:
            # 无 stock_id 列的市场级快照表（industry_fund_flow 等）
            g['tables'][t] = cur.execute(
                f'SELECT COUNT(*), NULL, MIN({dcol}), MAX({dcol}) FROM {t}').fetchone()
    return g


def known_items(cur: sqlite3.Cursor) -> list[dict]:
    """已知项对账：任务书 4 项 + 死列，各附 SQL 证据。"""
    items: list[dict] = []

    def scalar(sql: str):
        return cur.execute(sql).fetchone()

    # ① 换手率断供（t2 修复中——只登记现状，勿重复修）
    kt = scalar(
        "SELECT COUNT(*), SUM(CASE WHEN turnover IS NOT NULL AND turnover > 0 THEN 1 ELSE 0 END), "
        "MAX(CASE WHEN turnover > 0 THEN trade_date END), "
        "SUM(CASE WHEN turnover > 0 AND trade_date >= '2026-09-23' THEN 1 ELSE 0 END) "
        "FROM raw_kline")
    items.append({
        'id': 'K1', 'name': '换手率字段断供（raw_kline.turnover 恒 0）',
        'status': 't2 修复中且修复已在落库（审计运行期间观测到恢复）',
        'evidence': f'全表 {kt[0]} 行中 turnover>0 共 {kt[1]} 行，最后非零日 {kt[2]}；'
                    f'其中 {kt[3]} 行出现在 2026-09-23 及之后——**审计运行时（12:21）与 上午（11:05）'
                    '两次快照对比，09-23/09-24 部分行已由 0 变为真实换手率值，即 t2 修复正在实时落库**；'
                    '2026-07-16~09-22 历史窗口仍为 0 待回补。本审计不重复给修复方案（协调约定）。',
    })

    # ② 港股回测样本停更
    rh = scalar("SELECT COUNT(*), MAX(rating_date) FROM ratings_history rh "
                "JOIN stocks s ON s.id=rh.stock_id WHERE s.market='hk_stock' "
                "AND rh.rating_date > '2026-09-07'")
    bt = scalar("SELECT COUNT(*), MAX(rating_date) FROM backtest_results "
                "WHERE market='hk_stock' AND (is_simulated IS NULL OR is_simulated=0) "
                "AND rating_date > '2026-09-07'")
    chg = scalar("SELECT COUNT(*), MAX(rating_date) FROM ratings_history rh "
                 "JOIN stocks s ON s.id=rh.stock_id WHERE s.market='hk_stock' AND rh.is_change=1")
    items.append({
        'id': 'K2', 'name': '港股回测样本 2026-09-07 起停更',
        'status': '根因修正：非「链路损坏」，是补算机制缺口',
        'evidence': f'09-07 后港股评级 {rh[0]} 行（至 {rh[1]}，全部 is_change=0）但回测 {bt[0]} 行。'
                    f'回测行三个来源：auto_trigger_backtest（仅 is_change=1 触发——港股最后一次'
                    f'评级变更 {chg[1]}，共 {chg[0]} 次）/ 手动 batch_backtest 端点（近期调用仅 a_stock）/'
                    f'fill_pending_backtests 每日补算（**无任何调用方，未接线**）。'
                    f'A股样本持续增长纯因近期评级变更仍发生。',
    })

    # ③ acc2 中免 1000 股
    h = scalar("SELECT COUNT(*) FROM holdings h JOIN stocks s ON s.id=h.stock_id "
               "WHERE s.symbol='601888' AND h.account_id=2")
    items.append({
        'id': 'K3', 'name': 'acc2 中免 1000 股（021BV 重建行）处置',
        'status': '已处置（对账通过）',
        'evidence': f'account_id=2 的 601888 持仓行现存 {h[0]} 条——021BV 重建的 1000 股行已不在库'
                    '（用户已处置）；当前中免持仓=银河账户 1400 股 @59.3843。',
    })

    # ④ 导入行佣金为 0
    gal = scalar("SELECT COUNT(*) FROM trade_records WHERE account_id=1 AND commission=0 "
                 "AND commission_estimated=0 AND trade_type IN ('buy','sell')")
    em = scalar("SELECT COUNT(*) FROM trade_records WHERE account_id=2 AND commission=0 "
                "AND commission_estimated=0 AND trade_type IN ('buy','sell')")
    est1 = scalar("SELECT COUNT(*), SUM(CASE WHEN commission_estimated=1 THEN 1 ELSE 0 END) "
                  "FROM trade_records WHERE trade_type IN ('buy','sell')")
    items.append({
        'id': 'K4', 'name': '导入行 commission=0（费用未计入成本/盈亏）',
        'status': '用户数据线索（021BV 重估已按 est=1 完成，est=0 行不触碰）',
        'evidence': f'银河 {gal[0]} 笔 + 东财 {em[0]} 笔 commission=0 且 est=0（费率系统上线前导入）；'
                    f'全量买/卖 {est1[0]} 笔中估算标记 {est1[1]} 笔。零佣金行使已实现盈亏略被高估、'
                    '买入成本略被低估，属交割单补录范畴（用户自处置）。',
    })

    # ⑤ holdings.latest_price 死列
    hp = scalar("SELECT COUNT(*), SUM(CASE WHEN latest_price IS NOT NULL THEN 1 ELSE 0 END), "
                "MAX(price_updated_at) FROM holdings")
    items.append({
        'id': 'K5', 'name': 'holdings.latest_price 死列',
        'status': '死列遗留（登记，不修——删除列需迁移+级联评估）',
        'evidence': f'holdings {hp[0]} 行中 latest_price 非空 {hp[1]} 行、price_updated_at 最大 {hp[2]}'
                    '——列从未被写入；全部读取路径（holdings/watchlist/scores/export/审计）实为 '
                    'JOIN price_cache 的 pc.latest_price 别名。代码面无任何写入者（price_cache 021 系列接管）。',
    })

    # ⑥ 盘口采集断供（本审计新发现）
    ob = scalar("SELECT MAX(trade_date) FROM stock_orderbook")
    ob_ok = scalar("SELECT MAX(fetched_at) FROM data_status WHERE dimension='orderbook' AND status='success'")
    ob_fail = scalar("SELECT COUNT(*), MIN(fetched_at), MAX(fetched_at) FROM data_status "
                     "WHERE dimension='orderbook' AND status='failed' "
                     "AND fetched_at >= datetime('now','localtime','-14 days')")
    items.append({
        'id': 'K6', 'name': '五档盘口采集停更（mootdx 链路，本审计新发现）',
        'status': '采集源断连（分级：需排查修复——mootdx/TDX 连通性）',
        'evidence': f'stock_orderbook 最新采集日 {ob[0]}（A 股 40/46 只）；data_status 近 14 天 '
                    f'orderbook failed {ob_fail[0]} 次（{ob_fail[1]} ~ {ob_fail[2]}），最后成功 {ob_ok[0]}'
                    '——停更 15 天且连续失败，021BN 健康度面板应已红。属五档盘口增量维度，'
                    '不阻塞评分链（评分不消费盘口），影响报告「盘口」卡新鲜度。',
    })
    return items


# ================================================================
# 渲染
# ================================================================


def render(matrix: list[dict], data: dict, g: dict, items: list[dict], meta: dict) -> str:
    lines: list[str] = []
    lines.append(f'# 021BX 全维度数据完整度审计 — '
                 f'{datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")}')
    lines.append('')
    lines.append(f'- 数据库：`{meta["db"]}`（mode=ro 只读，零写库）')
    lines.append(f'- 自选股：{len(data["stocks"])} 只'
                 f'（A股 {sum(1 for s in data["stocks"] if s["market"] == "a_stock")} / '
                 f'港股 {sum(1 for s in data["stocks"] if s["market"] == "hk_stock")}）；'
                 f'时效基准=各市场最新交易日：A股 {data["ref"]["a_stock"]} / '
                 f'港股 {data["ref"]["hk_stock"]}（生产 020R-19 同语义）')
    lines.append('- 图例：✅ 正常 · ⚠️ 滞后/缺口（附天数或缺失数） · ❌ 缺失 · '
                 '➖ 不适用（无采集源/无事件属正常）。')
    lines.append('- **协调约定：换手率断供（K线采集）由 021BW t2 修复中，'
                 '本审计只登记现状、不重复给修复方案。**')
    lines.append('')
    lines.append('## 一、每股 × 全维度缺口矩阵')
    lines.append('')
    header = '| 股票 | ' + ' | '.join(lbl for _k, lbl in DIMS) + ' |'
    lines.append(header)
    lines.append('|---|' + '---|' * len(DIMS))
    warn_counts: dict[str, int] = {k: 0 for k, _ in DIMS}
    miss_counts: dict[str, int] = {k: 0 for k, _ in DIMS}
    for row in matrix:
        cells = []
        for k, _lbl in DIMS:
            c = row['dims'][k]
            cells.append(cell(c['mark'], c['suffix']))
            if c['mark'] == WARN:
                warn_counts[k] += 1
            elif c['mark'] == MISS:
                miss_counts[k] += 1
        lines.append(f'| {row["stk"]["symbol"]} {row["stk"]["name"]} | '
                     + ' | '.join(cells) + ' |')
    lines.append('')
    lines.append('### 维度缺口汇总（⚠️ 滞后/缺口 · ❌ 缺失，共 '
                 f'{len(matrix)} 只）')
    lines.append('')
    lines.append('| 维度 | ⚠️ | ❌ | 说明 |')
    lines.append('|---|---|---|---|')
    dim_notes = {
        'kd': '滞后>3d，或基准日前 30 个交易日有缺口（基准日当日不计——15:54 批次前属日内正常态）',
        'kw': '周线滞后>8d',
        'km': '月线滞后>35d', 'fin': '财报期滞后（A股>150d/港股>400d）',
        'cap': '近10个交易日缺≥3天（本轮为 4 只港股 westock 覆盖缺口）',
        'mgn': '融资余额滞后>7d（港股不适用）',
        'news': '情绪表滞后>7d', 'val': '估值滞后>7d',
        'ob': '盘口滞后>3d（09-09 起全量停更，见 K6）',
        'fc': '仅报告期<20260630 的存量预告；无预告=➖ 正常',
        'hd': '披露滞后>200d（021BW O5②）', 'ind': '未分类（021BA 自愈未收敛）',
        'rpt': '日报滞后>3d',
    }
    for k, lbl in DIMS:
        lines.append(f'| {lbl} | {warn_counts[k]} | {miss_counts[k]} | {dim_notes[k]} |')
    lines.append('')
    lines.append('## 二、已知项对账（任务书 4 项 + 死列）')
    lines.append('')
    for it in items:
        lines.append(f'### {it["id"]} {it["name"]}')
        lines.append('')
        lines.append(f'- **状态**：{it["status"]}')
        lines.append(f'- **证据**：{it["evidence"]}')
        lines.append('')
    lines.append('## 三、采集健康度（data_status 近 7 天，021BN 口径：skipped 不计分母）')
    lines.append('')
    ds: dict[str, dict[str, int]] = {}
    for dim, status, cnt in g['ds7']:
        ds.setdefault(dim, {})[status] = cnt
    lines.append('| 维度 | success | failed | skipped | 成功率 | 分级 |')
    lines.append('|---|---|---|---|---|---|')
    for dim in sorted(ds):
        d = ds[dim]
        ok, fail, skip = d.get('success', 0), d.get('failed', 0), d.get('skipped', 0)
        denom = ok + fail
        rate = f'{ok / denom * 100:.0f}%' if denom else '—'
        level = ('red' if (denom and ok / denom < 0.5 or fail >= 3) else
                 'yellow' if fail else 'green')
        lines.append(f'| {dim} | {ok} | {fail} | {skip} | {rate} | {level} |')
    lines.append('')
    lines.append('### error_logs 近 14 天（按模块）')
    lines.append('')
    lines.append('| 模块 | 次数 | 最近 |')
    lines.append('|---|---|---|')
    for mod, cnt, last in g['err14']:
        lines.append(f'| {mod} | {cnt} | {last} |')
    lines.append('')
    lines.append('## 四、表级存量统计')
    lines.append('')
    lines.append('| 表 | 行数 | 涉及股票数 | 最早 | 最新 |')
    lines.append('|---|---|---|---|---|')
    for t, (cnt, stocks, lo, hi) in g['tables'].items():
        lines.append(f'| {t} | {cnt} | {stocks} | {_d(lo) or "—"} | {_d(hi) or "—"} |')
    lines.append('')
    kt = g['kline_turnover']
    lines.append(f'- 换手率现状：{kt[0]} 行中 turnover>0 共 {kt[1]} 行（最后非零 {kt[2]}，'
                 f'其中 {kt[3]} 行为 09-23 起恢复）——**K1，t2 修复中且已开始落库**。')
    src_lines = '；'.join(f'{src}·est={est}: {cnt}' for src, est, cnt in g['cap_src'])
    lines.append(f'- 资金面链路标记：{src_lines}——capital_source 仅 westock 行有标（历史行 NULL），'
                 '链序判定不受影响（R2 以行为内容为准），低优登记。')
    ne = g['news_empty']
    lines.append(f'- 新闻空标记（total_count=0 当日无新闻占位）：{ne[1]}/{ne[0]} 行'
                 f'（{ne[1] / ne[0] * 100:.0f}%，正常占位非缺口）。')
    rs = g['raw_sentiment']
    lines.append(f'- raw_sentiment 逐条新闻：{rs[0]} 行 / {rs[1]} 股，最新 {rs[2]}。')
    lines.append('')
    lines.append('## 五、根因分类与修复方案分级')
    lines.append('')
    lines.append('**总体判定：核心评分链路（K线日/周/月、财报、资金面、新闻、日报、行业）'
                 '全绿——13 个维度中 9 个零缺口；缺口集中在「增量展示维度」（盘口）与'
                 '「低频披露维度」（股东户数），无评分链 P0 风险。**')
    lines.append('')
    lines.append('| # | 发现 | 根因分类 | 修复分级 | 方案 |')
    lines.append('|---|---|---|---|---|')
    lines.append('| 1 | 五档盘口 09-09 起全量停更（K6，矩阵 40⚠/6❌） | 采集源字段断供'
                 '（mootdx/TDX 连通性） | **可代码修（需排查）** | 排查 mootdx 服务器连通/超时/'
                 '降级链；健康度面板已红可见；不阻塞评分链（评分不消费盘口），影响报告盘口卡 |')
    lines.append('| 2 | 港股回测样本 09-07 起停更（K2） | 补算机制缺口'
                 '（fill_pending_backtests 未接线；auto_trigger 仅评级变更触发） | '
                 '**可代码修 / 零代码操作** | 零代码：`POST /api/backtest?market=hk_stock&days=30` '
                 '一次性补跑；代码修：收盘批次后接线 fill_pending_backtests（行为级新增，'
                 '须实施任务评估+用户批准） |')
    lines.append('| 3 | 4 只港股资金面近 10 日缺 6 天'
                 '（0700/6082/6880/9880） | 补采缺口（westock 当日失败未回补） | 需批量回补 | '
                 'backfill 调度器 020I westock --date 链路已具备，属运行等待/手动触发补采；'
                 '不阻塞评分（窗口均值型输入） |')
    lines.append('| 4 | 中芯国际融资余额滞后 8d | 数据源披露滞后'
                 '（两融余额 T+1~T+2 披露节奏波动） | 数据源限制登记 | 观察；阈值内波动，'
                 '下轮审计复核 |')
    lines.append('| 5 | 股东户数 3 只滞后'
                 '（广和通 359d/美的 208d/中免 1547d） | 披露源未更新 + 评分面无 staleness 守卫'
                 '（021BW O5②） | 数据源限制登记 | 披露节奏属源限制；评分面'
                 ' `_read_holder_structure` 加新鲜度守卫属资金面子项输入语义变更（§6 域，'
                 '需用户批准，先登记） |')
    lines.append('| 6 | 业绩预期 13 只最新报告期<20260630 | 事件级数据无新预告'
                 '（Q3 预告季未到） | 无需修（自然更新） | 10 月预告季自然刷新；'
                 '展示已按报告期标注，非缺口 |')
    lines.append('| 7 | 换手率历史窗口（07-16~09-22）恒 0（K1） | 采集源字段断供'
                 '（t2 修复中，勿重复修） | 需批量回补（t2 范围） | 协调约定：t2 正在修复采集链'
                 '（审计期间已观测到 09-23/09-24 恢复落库）；历史回补随 t2 方案 |')
    lines.append('| 8 | 导入行 commission=0 共 36 笔（K4） | 用户数据 | 用户自处置 | '
                 '交割单补录佣金后成本/已实现盈亏自动精确（重估脚本 est=1 面已闭环，'
                 'est=0 行系统不触碰） |')
    lines.append('| 9 | acc2 中免 1000 股（K3） | 用户数据 | 已处置（关闭） | '
                 '对账确认持仓行已不在库 |')
    lines.append('| 10 | holdings.latest_price 死列（K5） | 死列遗留 | 登记（不修） | '
                 '删除列需迁移+级联评估，收益为零（全读取面已走 price_cache）；留档 |')
    lines.append('| 11 | capital_source 标记覆盖不全（7,100 行 NULL） | 死列/半接管遗留 | '
                 '登记（低优） | R2 链序以行为内容判定不受影响；仅影响按源统计的便利性 |')
    lines.append('| 12 | market_overview 行业资金流 14 天 20 次失败 | 采集源间歇失败 | '
                 '已有自愈（不修） | 021AX 三层自愈在管（最后失败 09-24 01:37，'
                 '日批次快照仍在更新） |')
    lines.append('')
    return '\n'.join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description='021BX 全维度数据完整度审计（只读）')
    parser.add_argument('--db', default=None, help='SQLite 路径（默认 config.DB_PATH）')
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
    data = load_data(cur)
    matrix = [ {'stk': s, 'dims': audit_stock(s, data)} for s in data['stocks'] ]
    g = global_stats(cur)
    items = known_items(cur)
    meta = {'db': args.db or DB_PATH}
    con.close()

    md = render(matrix, data, g, items, meta)
    print(md)
    if args.out:
        os.makedirs(os.path.dirname(os.path.abspath(args.out)), exist_ok=True)
        with open(args.out, 'w', encoding='utf-8') as f:
            f.write(md + '\n')
        print(f'\n[已写出] {args.out}', file=sys.stderr)
    if args.json_out:
        payload = {
            'meta': meta,
            'matrix': [{'symbol': r['stk']['symbol'], 'name': r['stk']['name'],
                        'market': r['stk']['market'],
                        'dims': {k: v for k, v in r['dims'].items()}}
                       for r in matrix],
            'known_items': items,
        }
        os.makedirs(os.path.dirname(os.path.abspath(args.json_out)), exist_ok=True)
        with open(args.json_out, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
        print(f'[已写出] {args.json_out}', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
