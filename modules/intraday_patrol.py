"""021BT 盘中巡检调度器：交易时段定时巡检持仓股实时快照 + 触线/逼近/异动判定。

定位（021BT 批次 t2，方案 docs/reports/021bt_intraday_plan_20260923.md）：
- 风控判定口径仍以日K收盘为准（**收盘确认纪律零改动**）；本模块只做"盘中感知层"——
  交易时段每 INTRADAY_PATROL_INTERVAL_MIN 分钟取一次持仓股实时快照（腾讯批量，
  持仓 8 只=1 请求/轮），判定三类盘中状态并在看板速览卡/行动清单即时可见：
    a) below_stop 盘中触及止损线：实时价 <= 有效止损（双源取高者，与
       trader_advisor._stop_level / action_list._scan_stop_discipline 同口径）；
    b) near_stop  逼近止损：有效止损 < 现价，且距止损不足 INTRADAY_NEAR_STOP_PCT%；
    c) swing      快速异动：|盘中涨跌幅| >= INTRADAY_SWING_PCT% 或量能异动
       （当日累计成交量 >= 近 5 日均量 × INTRADAY_VOL_SPIKE_RATIO）。
- 数据源风控（021BN-c 教训）：零东财、零新浪、零 mootdx、零 @retry；批量 1 请求/轮；
  非交易时段 tick 空转代价=一次布尔判断；失败静默降级——保旧快照（price_cache
  旧值不覆盖不归零）+ 连败 INTRADAY_PATROL_PAUSE_AFTER_FAILS 轮暂停至时段边界。
- 节假日盲区守卫：`_is_intraday_session` 无节假日感知（020R-59 已知边界），腾讯
  批量响应每行快照时间戳（parts[30]）非今日 → 判定非交易日/停牌，该股静默跳过
  不写库不报警；连续 2 轮全部非今日 → 暂停至时段边界（下个交易时段自动恢复）。
- 写库面：仅 price_cache（既有表既有语义，INSERT OR REPLACE 幂等，与手动"刷新
  价格"同表同口径）；raw_kline / alert_history / 新表零写入（收盘口径隔离风险归零）。
- 所有对外提示一律带『盘中口径，以收盘确认为准』（snapshot.disclaimer / 行动项标注）。

调度模式：镜像 backfill_scheduler——threading.Timer 串联注册（daemon，天然无重叠，
随主进程存亡），app.py main() 注册 start_intraday_patrol()（幂等）；021BN-c 单实例
守卫不受影响（巡检 Timer 随唯一 app 实例存亡）。
"""

import atexit
import json
import logging
import threading
import time
from datetime import datetime, timedelta, timezone

import config
from database.db_manager import get_connection
from modules.collector.kline import _is_intraday_session
from modules.realtime_quotes import _fetch_quote_snapshot_batch

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

DISCLAIMER = '盘中口径，以收盘确认为准'

# ============================================================
# 调度器与巡检内存态（跨轮共享；快照仅内存不落盘——021BT 决策点3：触线不留痕）
# ============================================================
_timer = None
_started = False
_atexit_registered = False

_LAST_SNAPSHOT: dict | None = None
_snap_lock = threading.Lock()  # 快照读写互斥（Timer tick 线程 ↔ Flask 手动刷新线程）
_consecutive_failures = 0
_paused = False    # 连败/疑似非交易日暂停（至时段边界由 tick 自动解除）
_stale_rounds = 0  # 连续"全部快照非今日"轮数（节假日盲区守卫）
_last_manual_refresh_ts = 0.0


# ============================================================
# 数据读取（零网络）
# ============================================================


def _held_positions():
    """持仓聚合（021W 多账户安全：GROUP BY stock_id 账户无关，无 JOIN 重复行）。

    Returns: [{'stock_id','symbol','market','name','total_qty','avg_cost'}]
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT s.id AS stock_id, s.symbol, s.market, s.name,
                   SUM(h.quantity) AS total_qty,
                   SUM(h.quantity * h.cost_price) / SUM(h.quantity) AS avg_cost
            FROM holdings h
            JOIN stocks s ON s.id = h.stock_id
            WHERE h.quantity > 0
            GROUP BY s.id
            ORDER BY s.id
            """
        )
        return [
            {
                'stock_id': r['stock_id'],
                'symbol': r['symbol'] or '',
                'market': r['market'] or 'a_stock',
                'name': r['name'] or '',
                'total_qty': int(r['total_qty'] or 0),
                'avg_cost': float(r['avg_cost']) if r['avg_cost'] else None,
            }
            for r in cursor.fetchall()
        ]
    finally:
        conn.close()


def _read_pa_stop(cursor, stock_id):
    """最新 ok 日报 price_advice.stop_loss（与 _scan_stop_discipline 同 SQL 同解析）。"""
    cursor.execute(
        "SELECT price_advice FROM daily_reports "
        "WHERE stock_id = ? AND status = 'ok' AND report_type = 'daily' "
        'ORDER BY report_date DESC LIMIT 1',
        (stock_id,),
    )
    r = cursor.fetchone()
    if not r or not r['price_advice']:
        return None
    try:
        pa = json.loads(r['price_advice'])
        if isinstance(pa, dict) and pa.get('stop_loss') is not None:
            return float(pa['stop_loss'])
    except (TypeError, ValueError):
        pass
    return None


def _effective_stop(cost, pa_stop):
    """有效止损 = max(聚合成本×0.92 纪律线, 最新日报建议止损)（双源取高者）。

    Returns: (level|None, source) —— source 与 trader_advisor._stop_level 同词表：
    '纪律' / '价格建议' / '纪律/价格建议取高者'。
    """
    disc = round(float(cost) * 0.92, 2) if cost else None
    candidates = [(disc, '纪律'), (pa_stop, '价格建议')]
    vals = [(v, s) for v, s in candidates if v]
    if not vals:
        return None, None
    level, source = max(vals, key=lambda x: x[0])
    if len(vals) == 2:
        source = '纪律/价格建议取高者'
    return round(level, 2), source


def _read_volume_ref(cursor, stock_id, today_str):
    """量能异动参照：今日之前最近 5 根日K成交量均值（单位与腾讯快照[6]同：A股=手/港股=股）。"""
    cursor.execute(
        'SELECT volume FROM raw_kline '
        "WHERE stock_id = ? AND substr(trade_date, 1, 10) < ? AND volume IS NOT NULL "
        'ORDER BY trade_date DESC LIMIT 5',
        (stock_id, today_str),
    )
    vols = [float(r['volume']) for r in cursor.fetchall() if r['volume']]
    return sum(vols) / len(vols) if vols else None


def _read_price_cache_map(cursor, stock_ids):
    """price_cache 兜底显示价（快照缺失/休市市场的持仓；只读，禁止归零语义）。"""
    if not stock_ids:
        return {}
    placeholders = ','.join('?' * len(stock_ids))
    cursor.execute(
        f'SELECT stock_id, latest_price, pct_change, updated_at FROM price_cache '
        f'WHERE stock_id IN ({placeholders})',
        tuple(stock_ids),
    )
    return {
        r['stock_id']: {
            'latest_price': r['latest_price'],
            'pct_change': r['pct_change'],
            'updated_at': r['updated_at'],
        }
        for r in cursor.fetchall()
    }


def _read_ma20(cursor, stock_id, today_str):
    """MA20 参照（收盘口径）：今日之前最近 20 根日K收盘均线；不足 20 根返回 None
    （显式缺失不凑数，速览卡显示"—"）。排除当日行——盘中盘中快报可能残留
    tencent_intraday 当日行，MA20 必须只基于已完成日K（收盘口径）。"""
    cursor.execute(
        'SELECT close FROM raw_kline '
        "WHERE stock_id = ? AND substr(trade_date, 1, 10) < ? AND close IS NOT NULL "
        'ORDER BY trade_date DESC LIMIT 20',
        (stock_id, today_str),
    )
    closes = [float(r['close']) for r in cursor.fetchall() if r['close']]
    if len(closes) < 20:
        return None
    return round(sum(closes) / len(closes), 3)


# ============================================================
# 状态判定（纯函数）
# ============================================================


def evaluate_stock_state(price, effective_stop, pct_change, near_pct, swing_pct):
    """单股盘中状态判定（纯函数，触线 > 逼近 > 正常；异动为独立标记可叠加）。

    - 'below_stop': 实时价 <= 有效止损（触及即警，任务口径 a）；
    - 'near_stop':  有效止损 < 实时价，且 (price/stop-1)*100 不足 near_pct%（口径 b）；
    - 'normal':     其余；止损线缺失 → 'unknown'（显式"无止损参考"，不静默）。
    swing: |pct_change| >= swing_pct%（口径 c 之涨幅异动；量能异动由调用方并集）。
    """
    if effective_stop is None:
        state = 'unknown'
    elif price <= effective_stop:
        state = 'below_stop'
    elif (price / effective_stop - 1.0) * 100.0 < near_pct:
        state = 'near_stop'
    else:
        state = 'normal'
    swing = pct_change is not None and abs(pct_change) >= swing_pct
    return state, swing


def _quote_hhmm(quote_ts):
    """归一 quote_ts 'YYYYMMDDHHMMSS' → 'HH:MM'（缺失返回 None）。"""
    if not quote_ts or len(quote_ts) < 12:
        return None
    return f'{quote_ts[8:10]}:{quote_ts[10:12]}'


def _cache_hhmm(updated_at):
    """price_cache.updated_at 'YYYY-MM-DD HH:MM:SS' → 'HH:MM'（缺失返回 None）。"""
    s = str(updated_at or '')
    return s[11:16] if len(s) >= 16 else None


def build_snapshot(now, positions, quotes=None, *, source='auto', degraded=False,
                   degrade_note=None):
    """组装盘中快照 dict（纯组装：零网络；quotes=None 时全部走 price_cache 兜底显示）。

    Args:
        now: 北京时间 datetime（测试注入 _FakeDT 产物亦可）。
        positions: _held_positions() 输出。
        quotes: {stock_id: realtime_quotes 快照 dict}（已过"快照时间戳=今日"守卫）；
            缺股 → price_cache 兜底（quote_ok=False）。
        source: 'auto' 定时轮 / 'manual' 手动一键刷新 / 'fallback' 读取时兜底。
    """
    today_str = now.strftime('%Y-%m-%d')
    near_pct = float(getattr(config, 'INTRADAY_NEAR_STOP_PCT', 1.0))
    swing_pct = float(getattr(config, 'INTRADAY_SWING_PCT', 3.0))
    vol_ratio = float(getattr(config, 'INTRADAY_VOL_SPIKE_RATIO', 1.5))
    markets = sorted({p['market'] for p in positions})

    stop_map = {}
    volref_map = {}
    ma20_map = {}
    cache_map = {}
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for p in positions:
            sid = p['stock_id']
            pa_stop = _read_pa_stop(cursor, sid)
            level, src = _effective_stop(p['avg_cost'], pa_stop)
            stop_map[sid] = {
                'pa_stop': round(pa_stop, 2) if pa_stop else None,
                'effective_stop': level,
                'stop_source': src,
                'discipline_stop': round(float(p['avg_cost']) * 0.92, 2)
                if p['avg_cost'] else None,
            }
            volref_map[sid] = _read_volume_ref(cursor, sid, today_str)
            ma20_map[sid] = _read_ma20(cursor, sid, today_str)
        cache_map = _read_price_cache_map(cursor, [p['stock_id'] for p in positions])
    finally:
        conn.close()

    rows = []
    counts = {'below_stop': 0, 'near_stop': 0, 'normal': 0, 'unknown': 0, 'no_data': 0}
    alert_count = 0
    for p in positions:
        sid = p['stock_id']
        stop = stop_map[sid]
        q = (quotes or {}).get(sid)
        if q:
            price = float(q['price'])
            pct = q.get('pct_change')
            pct = float(pct) if pct is not None else None
            as_of = _quote_hhmm(q.get('quote_ts'))
            volume = q.get('volume')
            quote_ok = True
            note = None
        else:
            c = cache_map.get(sid)
            if c and c['latest_price']:
                price = float(c['latest_price'])
                pct = float(c['pct_change']) if c['pct_change'] is not None else None
                as_of = _cache_hhmm(c['updated_at'])
            else:
                price = None
                pct = None
                as_of = None
            volume = None
            quote_ok = False
            note = '休市' if not _is_intraday_session(p['market']) else '本轮未获取'
            if price is None:
                note = '暂无价格'
        if price is None:
            state, swing = 'no_data', False
            distance = None
        else:
            state, swing = evaluate_stock_state(price, stop['effective_stop'], pct,
                                                near_pct, swing_pct)
            distance = (
                round((price / stop['effective_stop'] - 1.0) * 100.0, 2)
                if stop['effective_stop'] else None
            )
        vol_spike = bool(
            vol_ratio > 0 and volume and volref_map[sid]
            and volume >= volref_map[sid] * vol_ratio
        )
        swing = swing or vol_spike
        if state in ('below_stop', 'near_stop') or swing:
            alert_count += 1
        counts[state] = counts.get(state, 0) + 1
        ma20 = ma20_map.get(sid)
        ma20_distance = (
            round((price / ma20 - 1.0) * 100.0, 2) if (ma20 and price) else None
        )
        rows.append({
            'stock_id': sid,
            'symbol': p['symbol'],
            'name': p['name'],
            'market': p['market'],
            'price': price,
            'pct_change': pct,
            'as_of': as_of,
            'quote_ok': quote_ok,
            'note': note,
            'stop_line': stop['effective_stop'],
            'stop_source': stop['stop_source'],
            'discipline_stop': stop['discipline_stop'],
            'pa_stop': stop['pa_stop'],
            'distance_pct': distance,
            'ma20': ma20,
            'ma20_distance_pct': ma20_distance,
            'state': state,
            'swing': swing,
            'vol_spike': vol_spike,
            'volume': volume,
            'volume_ref': volref_map[sid],
        })

    return {
        'date': today_str,
        'updated_at': now.strftime('%H:%M:%S'),
        'source': source,
        'session': {
            'in_session': any(_is_intraday_session(m) for m in markets),
            'markets': markets,
        },
        'stocks': rows,
        'counts': {**counts, 'alerts': alert_count},
        'degraded': degraded,
        'degrade_note': degrade_note,
        'disclaimer': DISCLAIMER,
    }


# ============================================================
# 巡检主入口
# ============================================================


def run_patrol_round(force=False, now=None):
    """跑一轮盘中巡检：持仓 → 止损线 → 批量快照 → 判定 → 写 price_cache → 内存快照。

    Args:
        force: True=手动一键刷新（绕过连败暂停；仍受交易时段门控与总开关约束）。
        now: 注入时刻（测试用）。

    Returns: {'ok','skipped','reason','snapshot','written','stale_skipped'}
    """
    global _LAST_SNAPSHOT, _consecutive_failures, _paused, _stale_rounds

    if not getattr(config, 'INTRADAY_PATROL_ENABLED', True):
        return {'ok': False, 'skipped': True, 'reason': '盘中巡检已停用',
                'snapshot': get_snapshot(), 'written': 0, 'stale_skipped': 0}

    now = now or datetime.now(_CN_TZ)
    positions = _held_positions()
    if not positions:
        with _snap_lock:
            _LAST_SNAPSHOT = build_snapshot(now, [], None, source='auto')
        return {'ok': True, 'skipped': False, 'reason': '无持仓',
                'snapshot': _LAST_SNAPSHOT, 'written': 0, 'stale_skipped': 0}

    if _paused and not force:
        return {'ok': False, 'skipped': True, 'reason': '巡检暂停中（时段边界自动恢复）',
                'snapshot': get_snapshot(), 'written': 0, 'stale_skipped': 0}

    # 交易时段门控：按持仓市场分别判定（A/H 混合时任一市场在盘中即执行其批量请求）
    markets = {p['market'] for p in positions}
    active = {m for m in markets if _is_intraday_session(m)}
    if not active:
        return {'ok': False, 'skipped': True, 'reason': '非交易时段',
                'snapshot': get_snapshot(), 'written': 0, 'stale_skipped': 0}

    symbols_markets = [
        (p['stock_id'], p['symbol'], p['market'])
        for p in positions if p['market'] in active
    ]

    fetch_note = None
    try:
        quotes_raw = _fetch_quote_snapshot_batch(symbols_markets)
    except Exception as e:  # noqa: BLE001 —— 失败静默降级：保旧快照 + 连败节流
        logger.warning(f'[盘中巡检] 快照获取失败（保旧快照，静默降级）: {e}')
        quotes_raw = None
        fetch_note = f'数据源暂不可达（{_short(e)}）'

    if quotes_raw is None:
        _consecutive_failures += 1
        pause_after = int(getattr(config, 'INTRADAY_PATROL_PAUSE_AFTER_FAILS', 3))
        paused_now = _consecutive_failures >= pause_after
        if paused_now and not _paused:
            logger.warning(
                '[盘中巡检] 连败 %d 轮（阈值 %d），暂停至时段边界',
                _consecutive_failures, pause_after,
            )
        _paused = _paused or paused_now
        with _snap_lock:
            _LAST_SNAPSHOT = build_snapshot(
                now, positions, None, source='auto', degraded=True,
                degrade_note=fetch_note,
            )
        return {'ok': False, 'skipped': False, 'reason': fetch_note,
                'snapshot': _LAST_SNAPSHOT, 'written': 0, 'stale_skipped': 0}

    # 节假日/停牌守卫：快照时间戳非今日 → 该股跳过（不写库不报警）
    today_compact = now.strftime('%Y%m%d')
    quotes = {}
    stale_skipped = 0
    for sid, q in quotes_raw.items():
        if (q.get('quote_ts') or '')[:8] != today_compact:
            stale_skipped += 1
            continue
        quotes[sid] = q
    if quotes_raw and not quotes:
        _stale_rounds += 1
        if _stale_rounds >= 2 and not _paused:
            logger.info('[盘中巡检] 连续 %d 轮全部快照非今日（疑似节假日），暂停至时段边界',
                        _stale_rounds)
            _paused = True
    else:
        _stale_rounds = 0
    # 写 price_cache（仅今日有效快照；失败股不写——保旧值禁归零，与手动刷新同原则）
    written = 0
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for sid, q in quotes.items():
            cursor.execute(
                "INSERT OR REPLACE INTO price_cache "
                '(stock_id, latest_price, pct_change, updated_at) '
                "VALUES (?, ?, ?, datetime('now', 'localtime'))",
                (sid, q['price'], q['pct_change']),
            )
            written += 1
        conn.commit()
    finally:
        conn.close()

    all_stale = bool(quotes_raw) and not quotes
    with _snap_lock:
        _LAST_SNAPSHOT = build_snapshot(
            now, positions, quotes, source='auto',
            degraded=all_stale,
            degrade_note='快照非今日（疑似节假日/停牌），本轮未写库' if all_stale else None,
        )
    if quotes:
        # 有今日有效数据 = 数据源可达：解除连败/节假日暂停。
        # 全部非今日（节假日守卫触发）时不清暂停态——暂停由时段边界 tick 解除。
        _consecutive_failures = 0
        _paused = False
    logger.info(
        '[盘中巡检] 一轮完成：持仓 %d 只，快照 %d，写 price_cache %d，非今日跳过 %d',
        len(positions), len(quotes), written, stale_skipped,
    )
    return {'ok': True, 'skipped': False, 'reason': None, 'snapshot': _LAST_SNAPSHOT,
            'written': written, 'stale_skipped': stale_skipped}


def request_manual_refresh():
    """前端速览卡「一键刷新」：60s 冷却 + 绕过连败暂停；非时段/停用返回明确 reason。"""
    global _last_manual_refresh_ts
    cooldown = float(getattr(config, 'INTRADAY_MANUAL_REFRESH_COOLDOWN_SEC', 60))
    now_mono = time.monotonic()
    remain = int(cooldown - (now_mono - _last_manual_refresh_ts))
    if remain > 0:
        return {'ok': False, 'skipped': True, 'cooldown': remain,
                'reason': f'冷却中（{remain}s 后可再次刷新）', 'snapshot': get_snapshot()}
    _last_manual_refresh_ts = now_mono
    result = run_patrol_round(force=True)
    result['cooldown'] = int(cooldown)
    return result


# ============================================================
# 读路径（看板/行动清单消费；零网络零写库）
# ============================================================


def _patrol_state():
    return {
        'enabled': bool(getattr(config, 'INTRADAY_PATROL_ENABLED', True)),
        'interval_min': float(getattr(config, 'INTRADAY_PATROL_INTERVAL_MIN', 5)),
        'consecutive_failures': _consecutive_failures,
        'paused': _paused,
    }


def get_snapshot():
    """当前快照（叠加实时巡检状态）；无快照返回 None。零网络零写库。"""
    snap = _LAST_SNAPSHOT
    if snap is None:
        return None
    out = dict(snap)
    out['patrol'] = _patrol_state()
    return out


def _scan_today_signal_labels(stock_ids):
    """今日信号标记 overlay（只读离线复算，零网络；仅持仓 id，买卖两侧各扫一次）。

    "今日"口径与行动清单路2/2b 一致：命中触发日 == 最新已采集K线日（最新一根
    K线上的信号，前日巡检已覆盖的历史命中不计）。扫描失败静默返回空表（不阻塞
    速览卡，信号标记仅辅助信息）。

    Returns: {stock_id: [{'side': 'buy'|'sell', 'label', 'signal', 'date'}]}
    """
    labels: dict = {}
    if not stock_ids:
        return labels
    try:
        from modules.market_screener import (
            scan_watchlist_sell_signals,
            scan_watchlist_signals,
        )

        for side, scan, match_key in (
            ('buy', scan_watchlist_signals, 'matches'),
            ('sell', scan_watchlist_sell_signals, 'sell_matches'),
        ):
            try:
                result = scan(stock_ids=stock_ids) or {}
            except Exception as e:  # noqa: BLE001 —— 单侧失败不阻塞另一侧
                logger.warning('[盘中速览] %s 信号复算失败（标记留空）: %s', side, e)
                continue
            for sig in result.get('results') or []:
                kline_upto = sig.get('kline_upto')
                hits = [
                    h for h in sig.get(match_key) or []
                    if h.get('trigger_date') == kline_upto
                ]
                if not hits:
                    continue
                labels.setdefault(sig['stock_id'], []).extend([
                    {'side': side, 'label': h.get('label') or h.get('signal') or '',
                     'signal': h.get('signal') or '', 'date': h.get('trigger_date')}
                    for h in hits
                ])
    except Exception as e:  # noqa: BLE001 —— 整体失败静默（信号标记非关键信息）
        logger.warning('[盘中速览] 信号标记 overlay 失败（留空）: %s', e)
    return labels


def get_snapshot_for_dashboard():
    """速览端点读路径：今日内存快照优先；缺失/跨日 → 零网络兜底现算（price_cache 显示价）。

    读取时叠加"今日信号标记"（零网络离线复算，仅持仓 id）与实时巡检状态。
    """
    global _LAST_SNAPSHOT
    snap = _LAST_SNAPSHOT
    today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    if snap is None or snap.get('date') != today_str:
        now = datetime.now(_CN_TZ)
        positions = _held_positions()
        snap = build_snapshot(
            now, positions, None, source='fallback',
            degraded=snap is not None,
            degrade_note=None if snap is None else '快照非今日（跨日/重启后未巡检）',
        )
        _LAST_SNAPSHOT = snap
    out = dict(snap)
    rows = [dict(r) for r in snap.get('stocks') or []]
    sig_labels = _scan_today_signal_labels([r['stock_id'] for r in rows])
    for r in rows:
        r['signal_labels'] = sig_labels.get(r['stock_id']) or []
    out['stocks'] = rows
    out['patrol'] = _patrol_state()
    out['success'] = True
    return out


def get_intraday_alerts():
    """行动清单路0b 数据源：当前快照中需要提醒的持仓股（触线/逼近/异动）。

    只读内存快照；快照非今日（跨日/重启后）不产生提醒（巡检未运行时不虚构）。
    Returns: [{'stock_id','symbol','name','price','pct_change','as_of','stop_line',
               'distance_pct','state','swing','vol_spike','updated_at'}]
    """
    snap = _LAST_SNAPSHOT
    if not snap or snap.get('date') != datetime.now(_CN_TZ).strftime('%Y-%m-%d'):
        return []
    alerts = []
    for row in snap.get('stocks') or []:
        if row.get('state') in ('below_stop', 'near_stop') or row.get('swing'):
            alerts.append({
                'stock_id': row['stock_id'],
                'symbol': row['symbol'],
                'name': row['name'],
                'price': row['price'],
                'pct_change': row['pct_change'],
                'as_of': row['as_of'],
                'stop_line': row['stop_line'],
                'distance_pct': row['distance_pct'],
                'state': row['state'],
                'swing': row['swing'],
                'vol_spike': row['vol_spike'],
                'updated_at': snap.get('updated_at'),
            })
    sev = {'below_stop': 0, 'near_stop': 1}
    alerts.sort(key=lambda a: (sev.get(a['state'], 2), a['symbol']))
    return alerts


# ============================================================
# 调度器（Timer 串联，镜像 backfill_scheduler 模式）
# ============================================================


def _schedule_next(interval_min):
    global _timer
    _timer = threading.Timer(interval_min * 60, _patrol_tick)
    _timer.daemon = True
    _timer.start()


def _patrol_tick():
    """巡检 tick：时段内跑一轮；非时段零请求空转（顺带在时段边界重置暂停态）。"""
    global _paused, _stale_rounds, _consecutive_failures
    try:
        if getattr(config, 'INTRADAY_PATROL_ENABLED', True):
            positions = _held_positions()
            markets = {p['market'] for p in positions}
            if any(_is_intraday_session(m) for m in markets):
                run_patrol_round()
            elif _paused or _stale_rounds or _consecutive_failures:
                # 时段边界（收盘/午休/周末）：连败与节假日暂停自动恢复
                _paused = False
                _stale_rounds = 0
                _consecutive_failures = 0
                logger.info('[盘中巡检] 已到时段边界，巡检暂停态重置')
    except Exception as e:  # noqa: BLE001 —— tick 全包：异常不影响下一轮调度
        logger.warning(f'[盘中巡检] tick 异常（不影响下一轮）: {e}')
    finally:
        interval = max(float(getattr(config, 'INTRADAY_PATROL_INTERVAL_MIN', 5)), 1.0)
        _schedule_next(interval)


def start_intraday_patrol():
    """启动盘中巡检调度器（幂等，app.py main() 调用）。

    启动即检：首个 tick 立即执行（Timer(0)）——正处交易时段则立刻有快照，
    避免服务午间重启后空等一个间隔；非时段时空转零开销。
    """
    global _started, _atexit_registered
    if _started:
        return
    _started = True
    if not _atexit_registered:
        atexit.register(stop_intraday_patrol)
        _atexit_registered = True
    interval = max(float(getattr(config, 'INTRADAY_PATROL_INTERVAL_MIN', 5)), 1.0)
    first = threading.Timer(0, _patrol_tick)
    first.daemon = True
    first.start()
    logger.info('[盘中巡检] 调度器已启动（间隔 %g 分钟，仅交易时段活动；总开关 %s）',
                interval, getattr(config, 'INTRADAY_PATROL_ENABLED', True))


def stop_intraday_patrol():
    """停止盘中巡检调度器（进程退出时调用）。"""
    global _timer, _started
    if _timer is not None:
        _timer.cancel()
        _timer = None
    _started = False
    logger.info('[盘中巡检] 调度器已停止')


def _reset_state_for_tests():
    """测试辅助：重置调度器与快照内存态（生产零调用）。"""
    global _LAST_SNAPSHOT, _consecutive_failures, _paused, _stale_rounds
    global _last_manual_refresh_ts, _timer, _started
    if _timer is not None:
        _timer.cancel()
    _timer = None
    _started = False
    _LAST_SNAPSHOT = None
    _consecutive_failures = 0
    _paused = False
    _stale_rounds = 0
    _last_manual_refresh_ts = 0.0


# ============================================================
# 内部小工具
# ============================================================


def _short(e, limit=60):
    """异常消息压缩（日志/降级文案用，防长堆栈刷屏）。"""
    return str(e)[:limit] or e.__class__.__name__
