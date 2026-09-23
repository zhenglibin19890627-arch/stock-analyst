"""
021BP 决策闭环 项3 + 021BQ 项D：今日行动清单（只读聚合）

目标：用户 30 秒看完"今天该关注什么"。聚合五路现成数据——
  路1 daily_reports 最新行：评级升降 / 超时缺报股显式列出（补日报 §4.4
      "失败股从概览表消失"的可见性缺口）/ 操盘手摘要（key_factors.trader，
      已预计算零重算）
  路2 自选股买点信号离线复算（t2 market_screener.scan_watchlist_signals，
      今日命中 + 共振星级）
  路2b 自选股卖点信号离线复算（021BQ scan_watchlist_sell_signals，
      今日命中 + bear 共振；持仓标记来自 holdings 账户无关聚合）
  路3 alert_history 当日触发（未读）
  路4 （经路1 的 overview 行透出操盘手阶段/分歧，供卡片副表）

排序约定（"今日应做"，021BR 起持仓纪律置顶）：
  P1 持仓纪律·止损已触发（纪律无条件最高）> P1 评级升降 > P2 卖出信号·持仓
  （风控优先）> P2 买点信号（共振≥4星优先）> P2 卖出信号·空仓（回避信息垫底）
  > P3 预警未读 > P4 超时缺报股

红线合规：
  - R9：只读聚合，不写 daily_reports/评分/评级表，不动任何写入路径；
  - V8：只读消费源表（raw_kline*/holdings/daily_reports/alert_history）；
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

# 021BP 修订（实测五粮液案例）：信号与这些评级并存时视为"相悖"——下跌趋势中的
# MACD 水下金叉等属超跌反弹型买点，裸标"买点信号"会让用户以为系统翻多，与个股
# 报告"建议减仓"、操盘手"强下跌"自相矛盾（AGENTS.md §9.6：判定标准是真实操作
# 路径下的端到端表现，契约层自洽 ≠ 用户体验正确）。
_CONFLICT_RATINGS = ('建议减仓', '强烈建议卖出')

# 021BQ 项D：买点相悖机制的卖侧镜像——卖点信号撞上买入档评级时显式调和
# （"短线回调警示，评级未变"），评级仍是动作主指令。方向与 _CONFLICT_RATINGS
# 相反，独立元组，勿复用。
_SELL_CONFLICT_RATINGS = ('推荐买入', '强烈推荐买入')


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

        # 021BQ 项D：持仓标记（holdings 账户无关聚合——同股多账户分仓只记一行，
        # 满足 021W 多行约定；quantity>0 才算持仓中）
        cursor.execute(
            'SELECT stock_id, SUM(quantity) AS total_qty, '
            'CASE WHEN SUM(quantity) > 0 '
            'THEN SUM(quantity * cost_price) / SUM(quantity) END AS avg_cost '
            'FROM holdings WHERE quantity > 0 GROUP BY stock_id'
        )
        held_map = {
            r['stock_id']: {'total_qty': int(r['total_qty'] or 0), 'avg_cost': r['avg_cost']}
            for r in cursor.fetchall()
        }

        # 021BR t3 路0：持仓纪律扫描（止损已触发的持仓股——诊断缺陷 F 静默缺口；
        # 与操盘手矩阵同口径：有效止损 = max(聚合成本×0.92, 最新日报 price_advice
        # .stop_loss)，现价 = raw_kline 最新日K收盘，触发判定同一价格源）
        discipline_rows = _scan_stop_discipline(cursor, held_map)
    finally:
        conn.close()

    # 路2：t2 买点信号复算（独立连接；失败降级为空）
    try:
        from modules.market_screener import scan_watchlist_signals

        signal_result = scan_watchlist_signals()
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[行动清单] 信号复算失败（本清单无信号项）: {e}')
        signal_result = None

    # 路2b：021BQ 卖点信号复算（独立连接；失败降级为空，不阻塞清单）
    try:
        from modules.market_screener import scan_watchlist_sell_signals

        sell_result = scan_watchlist_sell_signals()
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[行动清单] 卖点信号复算失败（本清单无卖点项）: {e}')
        sell_result = None

    return build_action_list(
        today=today, stocks=stocks, report_rows=report_rows,
        alerts_today=alerts_today, signal_result=signal_result,
        sell_result=sell_result, held_map=held_map,
        discipline_rows=discipline_rows,
        live_trader_fallback=True,  # 021BS R2 N01：看板端点启用 trader 摘要 live 兜底
    )


def _scan_stop_discipline(cursor, held_map):
    """持仓纪律扫描（021BR t3 路0，只读）：止损已触发的持仓股逐只列出。

    有效止损 = max(聚合成本×0.92 纪律线, 最新 ok 日报 price_advice.stop_loss)
    （与 trader_advisor._stop_level 双源取高者同口径）；现价 = raw_kline 最新
    日K收盘（与操盘手矩阵触发判定同一价格源）。任一数据缺失降级跳过。

    Returns: [{'stock_id','total_qty','avg_cost','close','close_date',
               'discipline_stop','pa_stop','effective_stop'}]（未触发的股不列出）
    """
    rows = []
    for sid, info in (held_map or {}).items():
        qty = int((info or {}).get('total_qty') or 0)
        cost = (info or {}).get('avg_cost')
        if not qty or not cost:
            continue
        try:
            cursor.execute(
                'SELECT trade_date, close FROM raw_kline '
                'WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 1',
                (sid,),
            )
            k = cursor.fetchone()
            if not k or not k['close']:
                continue
            close = float(k['close'])
            cursor.execute(
                "SELECT price_advice FROM daily_reports "
                "WHERE stock_id = ? AND status = 'ok' AND report_type = 'daily' "
                'ORDER BY report_date DESC LIMIT 1',
                (sid,),
            )
            r = cursor.fetchone()
            pa_stop = None
            if r and r['price_advice']:
                try:
                    pa = json.loads(r['price_advice'])
                    if isinstance(pa, dict) and pa.get('stop_loss') is not None:
                        pa_stop = float(pa['stop_loss'])
                except (TypeError, ValueError):
                    pa_stop = None
            disc = round(float(cost) * 0.92, 2)
            candidates = [v for v in (disc, pa_stop) if v]
            if not candidates:
                continue
            eff = max(candidates)
            if close < eff:
                rows.append({
                    'stock_id': sid,
                    'total_qty': qty,
                    'avg_cost': float(cost),
                    'close': close,
                    'close_date': str(k['trade_date']),
                    'discipline_stop': disc,
                    'pa_stop': pa_stop,
                    'effective_stop': round(eff, 2),
                })
        except Exception as e:  # noqa: BLE001 —— 单股失败不阻塞其余持仓
            logger.warning(f'[行动清单] 持仓纪律扫描跳过 stock_id={sid}: {e}')
    return rows


def build_action_list(today, stocks, report_rows, alerts_today, signal_result,
                      sell_result=None, held_map=None, discipline_rows=None,
                      live_trader_fallback=False):
    """纯逻辑装配今日行动清单（不触库不触网，全部输入由调用方给定）。

    Args:
        today: 'YYYY-MM-DD'
        stocks: [{'stock_id','symbol','name'}]（active 自选股）
        report_rows: daily_reports 行 dict 列表，含 'rn'（1=最新/2=上期），
            列含 report_date/stock_id/stock_code/stock_name/total_score/rating/
            rating_label/score_change/status/error_msg/key_factors
        alerts_today: [{'stock_id','alert_type','message','is_read'}]
        signal_result: scan_watchlist_signals() 返回（None=信号路降级）
        sell_result: 021BQ scan_watchlist_sell_signals() 返回（None=卖点路降级）
        held_map: 021BQ 持仓标记 {stock_id: {'total_qty', 'avg_cost'}}
            （holdings 账户无关聚合；None=全部视为空仓）
        discipline_rows: 021BR 持仓纪律扫描行（_scan_stop_discipline 输出；
            None=未扫描，不产行动项）
        live_trader_fallback: 021BS R2 N01——stored key_factors.trader 缺失
            （批次后刷新覆盖丢失形态）时 live 现算兜底（只读；会触库）。
            仅看板端点路径（get_action_list）启用；纯函数默认关闭，行为不变。

    Returns: {
        'date', 'items'（排序后行动项）, 'overview'（今日有报告股概览+操盘手摘要）,
        'failed_stocks'（今日失败股显式列出）, 'stats'（十一项计数）
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
        'stop_discipline_hits': 0,
        'rating_moves': 0,
        'signal_hits': 0,
        'resonance_hits': 0,
        'sell_hits': 0,
        'sell_resonance_hits': 0,
        'unread_alerts_today': 0,
    }

    # ---- 路0：持仓纪律·止损已触发（021BR t3，纪律无条件最高，置顶） ----
    stock_by_id = {s['stock_id']: s for s in stocks}
    for d in discipline_rows or []:
        s = stock_by_id.get(d['stock_id'])
        if s is None:
            continue
        stats['stop_discipline_hits'] += 1
        stop_parts = []
        if d.get('discipline_stop') is not None:
            stop_parts.append(f"成本线 {d['discipline_stop']:.2f}")
        if d.get('pa_stop'):
            stop_parts.append(f"建议止损 {d['pa_stop']:.2f}")
        items.append({
            'priority': 1,
            'priority_label': '持仓纪律',
            'kind': 'stop_discipline',
            'stock_id': s['stock_id'], 'symbol': s['symbol'], 'name': s['name'],
            'reason': (
                f"止损纪律已触发：现价 {d['close']:.2f}（{d['close_date']} 日K收盘）"
                f"低于有效止损 {d['effective_stop']:.2f}"
                f"（{' / '.join(stop_parts)} 取高者）"
                f"，持仓 {d['total_qty']:,} 股——纪律无条件执行，不等评级、不等反抽"
            ),
            'detail': {
                'close': d['close'],
                'close_date': d['close_date'],
                'stop_line': d['effective_stop'],
                'discipline_stop': d['discipline_stop'],
                'pa_stop': d.get('pa_stop'),
                'total_qty': d['total_qty'],
                'avg_cost': d['avg_cost'],
            },
        })

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
        # 021BS R2 N01：stored 摘要缺失/无效（批次后刷新覆盖丢失形态）时
        # live 现算兜底（只读，零写库；兜底失败静默退化回 stored/空——
        # 使看板 ⚡/阶段摘要与个股页 live 同面，消除「一面沉默一面报警」）
        if live_trader_fallback and not (
                isinstance(trader, dict) and trader.get('stage_name')):
            try:
                from modules.trader_advisor import derive_trader_signal_summary

                trader = derive_trader_signal_summary(kf, stock_id=sid) or trader
            except Exception:  # noqa: BLE001 —— 兜底失败不阻塞清单装配
                pass
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

    # ---- 路2：t2 买点信号复算（今日命中才算行动项；共振≥4星类内排前） ----
    stock_by_id = {s['stock_id']: s for s in stocks}
    if signal_result:
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
            # 评级相悖调和（021BP 修订）：最新评级为减仓/卖出档时显式标注，
            # detail.rating_conflict 供前端徽标转琥珀色"反弹信号·与评级相悖"
            latest = latest_by_stock.get(s['stock_id'])
            conflict_rating = None
            if latest and latest.get('status') == 'ok' and latest.get('rating') in _CONFLICT_RATINGS:
                conflict_rating = latest.get('rating')
                score = latest.get('total_score')
                score_txt = f'（{score:.1f}分）' if isinstance(score, (int, float)) else ''
                reason += (
                    f'。当前综合评级「{conflict_rating}」{score_txt}：'
                    '此为下跌趋势中的超跌反弹信号，未获趋势确认——'
                    '仅作调仓窗口/短线波段参考，非趋势反转买入'
                )
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
                    'rating_conflict': conflict_rating,
                },
            })

    # ---- 路2b：021BQ 卖点信号复算（今日命中；持仓者风控优先，空仓回避垫底） ----
    if sell_result:
        for sig in sell_result.get('results') or []:
            s = stock_by_id.get(sig.get('stock_id'))
            if s is None:
                continue
            kline_upto = sig.get('kline_upto')
            hits_today = [
                h for h in sig.get('sell_matches') or []
                if h.get('trigger_date') == kline_upto
            ]
            if not hits_today:
                continue  # 窗口内历史命中：前日巡检已覆盖，非"今日出现"
            resonances = sig.get('sell_resonances') or []
            top_stars = max((r.get('stars') or 0) for r in resonances) if resonances else 0
            stats['sell_hits'] += 1
            if top_stars >= _TOP_RESONANCE_STARS:
                stats['sell_resonance_hits'] += 1

            # 持仓标记（holdings 账户无关聚合；空仓者的卖点信号弱相关——排序垫底）
            held_info = (held_map or {}).get(s['stock_id']) or {}
            held_qty = int(held_info.get('total_qty') or 0)
            held = held_qty > 0
            avg_cost = held_info.get('avg_cost')

            labels = '、'.join(h['label'] for h in hits_today)
            reason = f'今日出现卖点信号：{labels}'
            if resonances:
                res_str = '、'.join(f"{r['label']}（{r['stars']}星）" for r in resonances)
                reason += f'；共振组合：{res_str}'
            if held:
                cost_txt = f'（成本 {avg_cost:.2f}）' if avg_cost else ''
                reason += f'。持仓 {held_qty:,} 股{cost_txt}——按纪律执行减仓/止损检查'
            else:
                reason += '。当前空仓——回避新买入，等待企稳'

            # 评级相悖调和（021BP 机制的卖侧镜像）：最新评级为买入档时显式标注，
            # detail.rating_conflict 供前端徽标转琥珀色"卖出信号·与评级相悖"
            latest = latest_by_stock.get(s['stock_id'])
            conflict_rating = None
            if latest and latest.get('status') == 'ok' and latest.get('rating') in _SELL_CONFLICT_RATINGS:
                conflict_rating = latest.get('rating')
                score = latest.get('total_score')
                score_txt = f'（{score:.1f}分）' if isinstance(score, (int, float)) else ''
                reason += (
                    f'。当前综合评级「{conflict_rating}」{score_txt}：'
                    '卖点信号与评级方向相悖——短线回调警示，评级未变，'
                    '信号仅波段参考，以评级为主'
                )

            items.append({
                'priority': 2,
                'priority_label': '卖点信号',
                'kind': 'sell_signal',
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
                    'rating_conflict': conflict_rating,
                    'held': held,
                    'total_qty': held_qty,
                    'avg_cost': avg_cost,
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

    # 021BR：全行统一持仓标记——前端"只看持仓"筛选与 📍徽标依赖（此前仅卖侧行携带）
    for it in items:
        info = (held_map or {}).get(it.get('stock_id')) or {}
        it['held'] = int(info.get('total_qty') or 0) > 0

    # 021BR：全行统一持仓标记——前端"只看持仓"筛选与 📍徽标依赖（此前仅卖侧行携带）
    for it in items:
        info = (held_map or {}).get(it.get('stock_id')) or {}
        it['held'] = int(info.get('total_qty') or 0) > 0

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
    """"今日应做"排序：P1 持仓纪律·止损已触发（021BR 纪律无条件最高）>
    P1 评级升降（降级风控优先，再按评分变动幅度）>
    P2 卖出信号·持仓（风控优先）> P2 买点信号（共振≥4星优先）
    > P2 卖出信号·空仓（回避信息垫底）> P3 预警未读 > P4 缺报补数；类内按代码。"""
    p = item['priority']
    detail = item.get('detail') or {}
    if p == 1:
        if item['kind'] == 'stop_discipline':
            dir_rank = -1  # 021BR：持仓纪律置顶于一切评级项
        else:
            dir_rank = 0 if item['kind'] == 'rating_downgrade' else 1
        chg = detail.get('score_change')
        magnitude = abs(chg) if isinstance(chg, (int, float)) else 0
        return (p, dir_rank, -magnitude, item['symbol'])
    if p == 2:
        # 021BQ：side_rank——卖出+持仓=0 < 买点=1 < 卖出+空仓=2
        # （持仓风控优先于他人买点，空仓回避信息垫底）
        if item['kind'] == 'sell_signal':
            side_rank = 0 if detail.get('held') else 2
        else:
            side_rank = 1
        return (p, side_rank, -(detail.get('top_stars') or 0), item['symbol'])
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
