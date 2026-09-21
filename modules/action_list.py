"""
021BP 决策闭环 项3：今日行动清单（只读聚合）

目标：用户 30 秒看完"今天该关注什么"。聚合四路现成数据——
  路1 daily_reports 最新行：评级升降 / 超时缺报股显式列出（补日报 §4.4
      "失败股从概览表消失"的可见性缺口）/ 操盘手摘要（key_factors.trader，
      已预计算零重算）
  路2 自选股买点信号离线复算（t2 market_screener.scan_watchlist_signals，
      今日命中 + 共振星级）
  路3 alert_history 当日触发（未读）
  路4 （经路1 的 overview 行透出操盘手阶段/分歧，供卡片副表）

排序约定（"今日应做"）：
  P1 评级升降 > P2 买点信号（共振≥4星优先）> P3 预警未读 > P4 超时缺报股

红线合规：
  - R9：只读聚合，不写 daily_reports/评分/评级表，不动任何写入路径；
  - V8：只读消费源表；
  - B24/R13：零触碰 generate_advice；
  - R7：评级升降方向仅消费 alert_engine.RATING_ORDER 既有顺序表（允许范围），
    不重实现"分数→评级"映射；
  - 021BN 教训：会被前端渲染的文案（reason 等）禁止裸 '<' 字符。
"""

import json
import logging
from datetime import datetime, timedelta, timezone

from modules.alert_engine import RATING_ORDER

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

# 共振星级门槛：≥4 星的买点共振在 P2 类内排前（res_double_golden 同日4星 /
# res_week_daily·res_bottom_reverse·res_zero_relay 5星，跨日双金叉3星）
_TOP_RESONANCE_STARS = 4


def get_action_list():
    """聚合四路现成数据并装配今日行动清单（只读，不写任何表）。

    信号路失败降级为空（单路数据缺失不阻塞清单，与日报挂载点异常隔离同思路）。
    Returns: build_action_list 的返回结构（见其 docstring）。
    """
    today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')

    from database.db_manager import get_connection

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # 活跃自选股（≤100 只）
        cursor.execute("SELECT id, symbol, name FROM stocks WHERE status='active' ORDER BY id")
        stocks = [
            {'stock_id': r['id'], 'symbol': r['symbol'] or '', 'name': r['name'] or ''}
            for r in cursor.fetchall()
        ]

        # 每只股票最近两期 daily 报告（rn=1 最新 / rn=2 上期；评级升降对比用）
        cursor.execute(
            """
            SELECT report_date, stock_id, stock_code, stock_name, total_score, rating,
                   rating_label, score_change, status, error_msg, key_factors,
                   ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY report_date DESC) AS rn
            FROM daily_reports
            WHERE report_type='daily'
            """
        )
        report_rows = [dict(r) for r in cursor.fetchall() if r['rn'] in (1, 2)]

        # 当日预警（含已读——未读才产行动项，已读计数不产）
        cursor.execute(
            'SELECT stock_id, alert_type, message, is_read '
            'FROM alert_history WHERE trigger_date=?',
            (today,),
        )
        alerts_today = [dict(r) for r in cursor.fetchall()]
    finally:
        conn.close()

    # 路2：t2 信号复算（独立连接；失败降级为空）
    try:
        from modules.market_screener import scan_watchlist_signals

        signal_result = scan_watchlist_signals()
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[行动清单] 信号复算失败（本清单无信号项）: {e}')
        signal_result = None

    return build_action_list(
        today=today, stocks=stocks, report_rows=report_rows,
        alerts_today=alerts_today, signal_result=signal_result,
    )


def build_action_list(today, stocks, report_rows, alerts_today, signal_result):
    """纯逻辑装配今日行动清单（不触库不触网，全部输入由调用方给定）。

    Args:
        today: 'YYYY-MM-DD'
        stocks: [{'stock_id','symbol','name'}]（active 自选股）
        report_rows: daily_reports 行 dict 列表，含 'rn'（1=最新/2=上期），
            列含 report_date/stock_id/stock_code/stock_name/total_score/rating/
            rating_label/score_change/status/error_msg/key_factors
        alerts_today: [{'stock_id','alert_type','message','is_read'}]
        signal_result: scan_watchlist_signals() 返回（None=信号路降级）

    Returns: {
        'date', 'items'（排序后行动项）, 'overview'（今日有报告股概览+操盘手摘要）,
        'failed_stocks'（今日失败股显式列出）, 'stats'（八项计数）
    }
    """
    latest_by_stock = {}
    prev_by_stock = {}
    for row in report_rows:
        sid = row['stock_id']
        if row.get('rn') == 1:
            latest_by_stock[sid] = row
        elif row.get('rn') == 2:
            prev_by_stock[sid] = row

    items = []
    overview = []
    failed_stocks = []
    stats = {
        'active_count': len(stocks),
        'reported_ok_today': 0,
        'failed_today': 0,
        'missing_today': 0,
        'rating_moves': 0,
        'signal_hits': 0,
        'resonance_hits': 0,
        'unread_alerts_today': 0,
    }

    # ---- 路1：每日报告（评级升降 / 超时缺报 / 概览+操盘手摘要） ----
    for s in stocks:
        sid = s['stock_id']
        latest = latest_by_stock.get(sid)

        if latest is None or latest.get('report_date') != today:
            stats['missing_today'] += 1
            continue

        if latest.get('status') != 'ok':
            # 今日报告生成失败（典型=采集超时）——显式列出，补 §4.4 可见性缺口
            stats['failed_today'] += 1
            err = str(latest.get('error_msg') or '生成失败')
            failed_stocks.append(
                {'stock_id': sid, 'symbol': s['symbol'], 'name': s['name'], 'error': err}
            )
            items.append({
                'priority': 4,
                'priority_label': '缺报补数',
                'kind': 'report_failed',
                'stock_id': sid, 'symbol': s['symbol'], 'name': s['name'],
                'reason': f'今日报告生成失败（{err}），建议重算今日报告补齐',
                'detail': {'error': err},
            })
            continue

        kf = _parse_key_factors(latest.get('key_factors'))
        trader = (kf or {}).get('trader') or {}
        stats['reported_ok_today'] += 1
        overview.append({
            'stock_id': sid, 'symbol': s['symbol'], 'name': s['name'],
            'total_score': latest.get('total_score'),
            'rating': latest.get('rating'),
            'rating_label': latest.get('rating_label'),
            'score_change': latest.get('score_change'),
            # 操盘手摘要（key_factors.trader，日报期预计算；零重算）
            'trader_stage': trader.get('stage_name'),
            'has_disagreement': bool(trader.get('has_disagreement')),
        })

        move = _rating_move_item(prev_by_stock.get(sid), latest, trader)
        if move:
            stats['rating_moves'] += 1
            items.append(move)

    # ---- 路2：t2 信号复算（今日命中才算行动项；共振≥4星类内排前） ----
    if signal_result:
        stock_by_id = {s['stock_id']: s for s in stocks}
        for sig in signal_result.get('results') or []:
            s = stock_by_id.get(sig.get('stock_id'))
            if s is None:
                continue
            kline_upto = sig.get('kline_upto')
            hits_today = [
                h for h in sig.get('matches') or []
                if h.get('trigger_date') == kline_upto
            ]
            if not hits_today:
                continue  # 窗口内历史命中：前日巡检已覆盖，非"今日出现"
            resonances = sig.get('resonances') or []
            top_stars = max((r.get('stars') or 0) for r in resonances) if resonances else 0
            stats['signal_hits'] += 1
            if top_stars >= _TOP_RESONANCE_STARS:
                stats['resonance_hits'] += 1
            labels = '、'.join(h['label'] for h in hits_today)
            reason = f'今日出现买点信号：{labels}'
            if resonances:
                res_str = '、'.join(f"{r['label']}（{r['stars']}星）" for r in resonances)
                reason += f'；共振组合：{res_str}'
            items.append({
                'priority': 2,
                'priority_label': '买点信号',
                'kind': 'tech_signal',
                'stock_id': s['stock_id'], 'symbol': s['symbol'], 'name': s['name'],
                'reason': reason,
                'detail': {
                    'signals': [
                        {'signal': h['signal'], 'label': h['label'],
                         'trigger_date': h['trigger_date']}
                        for h in hits_today
                    ],
                    'resonances': [
                        {'key': r['key'], 'label': r['label'], 'stars': r['stars']}
                        for r in resonances
                    ],
                    'top_stars': top_stars,
                    'kline_upto': kline_upto,
                },
            })

    # ---- 路3：当日预警未读 ----
    unread_by_stock = {}
    for a in alerts_today:
        if a.get('is_read'):
            continue
        stats['unread_alerts_today'] += 1
        unread_by_stock.setdefault(a['stock_id'], []).append(a)
    stock_by_id = {s['stock_id']: s for s in stocks}
    for sid, alerts in unread_by_stock.items():
        s = stock_by_id.get(sid)
        if s is None:
            continue
        first_msg = str(alerts[0].get('message') or '')
        items.append({
            'priority': 3,
            'priority_label': '预警未读',
            'kind': 'alert_unread',
            'stock_id': sid, 'symbol': s['symbol'], 'name': s['name'],
            'reason': f'今日 {len(alerts)} 条预警未读：{first_msg}',
            'detail': {
                'count': len(alerts),
                'alert_types': sorted({a.get('alert_type') or '' for a in alerts}),
            },
        })

    items.sort(key=_sort_key)
    return {
        'date': today,
        'items': items,
        'overview': overview,
        'failed_stocks': failed_stocks,
        'stats': stats,
    }


def _rating_move_item(prev_row, latest_row, trader):
    """今日评级 vs 上期评级 → P1 行动项（无上期/同档返回 None）。

    方向判定仅消费 alert_engine.RATING_ORDER 既有顺序表（R7 允许范围）；
    档位名在顺序表外（历史遗留档位）时报"变动"不报方向。
    """
    if prev_row is None:
        return None
    new_rating = latest_row.get('rating')
    old_rating = prev_row.get('rating')
    if not new_rating or not old_rating or new_rating == old_rating:
        return None

    old_order = RATING_ORDER.get(old_rating)
    new_order = RATING_ORDER.get(new_rating)
    if old_order is not None and new_order is not None and new_order != old_order:
        direction = 'upgrade' if new_order > old_order else 'downgrade'
        kind = f'rating_{direction}'
        arrow = {'upgrade': '升至', 'downgrade': '降至'}.get(direction, '变为')
    else:
        direction = 'change'
        kind = 'rating_change'
        arrow = '变为'

    reason = f'今日评级{arrow} {new_rating}（上期 {old_rating}）'
    chg = latest_row.get('score_change')
    if isinstance(chg, (int, float)):
        reason += f'，评分变动 {chg:+.1f}'
    if trader.get('has_disagreement') and trader.get('disagreement_text'):
        reason += f'；操盘手提示：{trader["disagreement_text"]}'

    return {
        'priority': 1,
        'priority_label': '评级变动',
        'kind': kind,
        'stock_id': latest_row['stock_id'],
        'symbol': latest_row.get('stock_code') or '',
        'name': latest_row.get('stock_name') or '',
        'reason': reason,
        'detail': {
            'old_rating': old_rating,
            'new_rating': new_rating,
            'direction': direction,
            'total_score': latest_row.get('total_score'),
            'score_change': chg,
        },
    }


def _sort_key(item):
    """"今日应做"排序：P1 评级升降（降级风控优先，再按评分变动幅度）>
    P2 买点信号（共振≥4星优先）> P3 预警未读 > P4 缺报补数；类内按代码。"""
    p = item['priority']
    detail = item.get('detail') or {}
    if p == 1:
        dir_rank = 0 if item['kind'] == 'rating_downgrade' else 1
        chg = detail.get('score_change')
        magnitude = abs(chg) if isinstance(chg, (int, float)) else 0
        return (p, dir_rank, -magnitude, item['symbol'])
    if p == 2:
        return (p, -(detail.get('top_stars') or 0), item['symbol'])
    return (p, 0, item['symbol'])


def _parse_key_factors(raw):
    """daily_reports.key_factors JSON → dict（解析失败返回 None，不阻塞清单）。"""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (TypeError, ValueError):
        return None
    return value if isinstance(value, dict) else None
