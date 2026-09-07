"""市场行情模块：行业资金流向（东方财富直连 + 系统代理回退 + SQLite 快照缓存）。

用途：市场行情页「行业资金流向」卡片的数据源。
- 抓取东财 push2 行业资金流排行（全部分页），直连优先、失败回退系统代理；
- 成功快照写入 industry_fund_flow 表（按交易日幂等覆盖），断网/限流时页面读库仍可用；
- 大盘指数沿用 modules/index_collector（本模块不重复实现）。

红线：不修改 data_collector.py / scoring_engine.py；本模块自管 industry_fund_flow 表。
"""

import json
import logging
import os
import threading
import time
from datetime import datetime, timedelta, timezone

import requests

from database.db_manager import get_connection

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

EM_URL = 'https://{host}/api/qt/clist/get'
# 东财 push2 主机轮换：单主机被限流时切换编号主机（020R-34）
EM_HOSTS = ['push2.eastmoney.com', '82.push2.eastmoney.com', 'push2his.eastmoney.com']
EM_FIELDS = 'f12,f14,f2,f3,f62,f184,f66,f69,f72,f75,f78,f81,f84,f87,f204,f205,f124'
EM_BASE_PARAMS = {
    'pz': '100',
    'po': '1',
    'np': '1',
    'ut': 'b2884a393a59ad64002292a3e90d46a5',
    'fltt': '2',
    'invt': '2',
    'fid0': 'f62',
    'fs': 'm:90 t:2',
    'stat': '1',
    'fields': EM_FIELDS,
    'rt': '52975239',
}

# 刷新失败冷却：连续失败后 10 分钟内不再硬闯东财，直接回放上次快照（020R-34）
REFRESH_COOLDOWN_SECONDS = 600
_last_failure_at = None

# 021C：冷却状态落盘——内存态在服务重启（联调/看门狗拉起）时被清零，
# 实测 2026-08-15 晚 23:14~23:28 重启后 1 分钟内连续硬闯东财 7 次。
# 落盘文件与 logs/em_ban_state.json（东财熔断）同风格，重启后冷却仍生效。
_COOLDOWN_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    'logs',
    'industry_ff_cooldown.json',
)


def _num(v):
    """东财字段 → float（'-'/''/None → None）。"""
    try:
        if v is None or v == '-' or v == '':
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _request_page(page_no, host, use_proxy):
    params = dict(EM_BASE_PARAMS)
    params['pn'] = str(page_no)
    params['_'] = str(int(time.time() * 1000))
    kwargs = {}
    if not use_proxy:
        kwargs['proxies'] = {'http': None, 'https': None}
    return requests.get(EM_URL.format(host=host), params=params, timeout=(5, 20), **kwargs)


def _request_page_robust(page_no):
    """单页请求：主机轮换 × (直连 2 次 → 系统代理 2 次)，全失败抛异常。"""
    last_err = None
    for host in EM_HOSTS:
        for use_proxy in (False, True):
            for attempt in range(2):
                try:
                    resp = _request_page(page_no, host, use_proxy)
                    resp.raise_for_status()
                    data = resp.json()
                    if not data or not data.get('data'):
                        raise RuntimeError('东财返回空 data')
                    return data
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    logger.warning(
                        '[行业资金流] 第%s页 %s %s 第%s次失败: %s',
                        page_no, host, '代理' if use_proxy else '直连', attempt + 1, e,
                    )
                    time.sleep(1.5)
    raise last_err if last_err else RuntimeError('行业资金流请求失败')


def _mark_failure():
    """记录一次刷新失败时间（内存 + 落盘双写，021C：重启不清零）。"""
    global _last_failure_at
    now = datetime.now(_CN_TZ)
    _last_failure_at = now
    try:
        os.makedirs(os.path.dirname(_COOLDOWN_STATE_FILE), exist_ok=True)
        with open(_COOLDOWN_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'failed_at': now.isoformat()}, f)
    except OSError:
        pass  # 落盘失败不影响主流程（内存态仍生效）


def refresh_in_cooldown():
    """刷新失败后冷却中？返回剩余秒数，否则 None（内存 + 落盘双态）。"""
    last = _last_failure_at
    if last is None:
        # 021C：内存态缺失（如服务重启）时读落盘状态
        try:
            with open(_COOLDOWN_STATE_FILE, encoding='utf-8') as f:
                data = json.load(f)
            last = datetime.fromisoformat(data.get('failed_at'))
            if last.tzinfo is None:
                last = last.replace(tzinfo=_CN_TZ)
        except (OSError, ValueError, KeyError, TypeError):
            last = None
    if last is None:
        return None
    elapsed = (datetime.now(_CN_TZ) - last).total_seconds()
    if elapsed < REFRESH_COOLDOWN_SECONDS:
        return int(REFRESH_COOLDOWN_SECONDS - elapsed)
    return None


def fetch_industry_fund_flow():
    """抓取东财行业资金流全部页 → (items, updated_at)。部分页失败时保留已获取部分。"""
    first = _request_page_robust(1)
    data = first.get('data') or {}
    total = int(data.get('total') or 0)
    pages = max(1, (total + 99) // 100)
    rows = list(data.get('diff') or [])

    for pn in range(2, pages + 1):
        try:
            page_data = _request_page_robust(pn)
            rows += list((page_data.get('data') or {}).get('diff') or [])
        except Exception as e:  # noqa: BLE001
            logger.warning('[行业资金流] 第%s页抓取失败（保留已获取部分）: %s', pn, e)
            break
        time.sleep(1.0)

    items = []
    updated_at = None
    for d in rows:
        ts = d.get('f124')
        if ts and updated_at is None:
            try:
                updated_at = datetime.fromtimestamp(int(ts), tz=_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')
            except (TypeError, ValueError, OSError):
                pass
        items.append(
            {
                'code': str(d.get('f12') or ''),
                'name': str(d.get('f14') or ''),
                'pct_change': _num(d.get('f3')),
                'main_net': _num(d.get('f62')),
                'main_pct': _num(d.get('f184')),
                'super_net': _num(d.get('f66')),
                'big_net': _num(d.get('f72')),
                'mid_net': _num(d.get('f78')),
                'small_net': _num(d.get('f84')),
                'lead_stock': str(d.get('f205') or '') or None,
            }
        )
    if not items:
        raise RuntimeError('东财行业资金流返回空数据')
    return items, updated_at


def save_industry_fund_flow(items, trade_date):
    """按交易日幂等覆盖快照。"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('DELETE FROM industry_fund_flow WHERE trade_date = ?', (trade_date,))
        for it in items:
            cursor.execute(
                'INSERT INTO industry_fund_flow '
                '(trade_date, code, name, pct_change, main_net, main_pct, super_net, big_net, mid_net, small_net, lead_stock) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                (
                    trade_date, it['code'], it['name'], it['pct_change'], it['main_net'], it['main_pct'],
                    it['super_net'], it['big_net'], it['mid_net'], it['small_net'], it['lead_stock'],
                ),
            )
        conn.commit()
        logger.info('[行业资金流] 快照已保存: %s 共 %d 条', trade_date, len(items))
    finally:
        conn.close()


def refresh_industry_fund_flow():
    """抓取并落库 → (items, trade_date, updated_at)；失败记录冷却时间后抛出。"""
    try:
        items, updated_at = fetch_industry_fund_flow()
    except Exception:  # noqa: BLE001
        _mark_failure()
        raise
    trade_date = updated_at[:10] if updated_at else datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    save_industry_fund_flow(items, trade_date)
    return items, trade_date, updated_at


# ============================================================
# 021BJ: 缺口回补 —— 东财 push2his 行业资金流"历史日K"接口
# 背景：实时快照接口错过采集日（如东财断连日）数据即丢失，
#       历史接口可按行业代码回补指定交易日的每日主力净流入。
# ============================================================

_EM_FFLOW_HIST_URL = 'https://{host}/api/qt/stock/fflow/daykline/get'
_FFLOW_HIST_PARAMS = {
    'lmt': '0', 'klt': '101',
    'fields1': 'f1,f2,f3,f7',
    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
    'ut': 'b2884a393a59ad64002292a3e90d46a5',
}
_BACKFILL_CONSECUTIVE_FAIL_LIMIT = 5
_backfill_thread_lock = threading.Lock()
_backfill_running = {'flag': False}
# 021BJ：历史接口成功主机粘性——EM 丢包率高的时段，死主机组合的整轮重试代价极高，
# 一旦某 (host, proxy) 组合成功则优先重试该组合，大幅提速。
_fflow_hist_preferred = {'host': None, 'use_proxy': None}


def _parse_fflow_hist_row(row, trade_date, code, name):
    """历史日K行 'date,main,small,mid,big,super,pcts...,close,pct,...' → item dict。

    不匹配目标日期 / 非法行返回 None。（纯函数，离线可测）
    字段映射：f52主力 f53小单 f54中单 f55大单 f56超大单 f57主力占比 f63涨跌幅。
    """
    try:
        parts = row.split(',')
        if len(parts) < 13 or parts[0] != trade_date:
            return None
        return {
            'code': code,
            'name': name,
            'pct_change': _num(parts[12]),
            'main_net': _num(parts[1]),
            'main_pct': _num(parts[6]),
            'super_net': _num(parts[5]),
            'big_net': _num(parts[4]),
            'mid_net': _num(parts[3]),
            'small_net': _num(parts[2]),
            'lead_stock': None,   # 历史接口无领涨股字段
        }
    except (IndexError, ValueError):
        return None


def _request_fflow_hist_robust(code):
    """历史日K请求：优先上次成功组合，否则主机轮换 × (直连 2 次 → 系统代理 2 次)。"""
    last_err = None
    base = dict(_FFLOW_HIST_PARAMS)
    base['secid'] = f'90.{code}'

    def _attempt(host, use_proxy):
        p = dict(base)
        p['_'] = str(int(time.time() * 1000))
        kwargs = {}
        if not use_proxy:
            kwargs['proxies'] = {'http': None, 'https': None}
        resp = requests.get(_EM_FFLOW_HIST_URL.format(host=host), params=p,
                            timeout=(4, 12), **kwargs)
        resp.raise_for_status()
        data = resp.json()
        klines = (data.get('data') or {}).get('klines')
        if klines is None:
            raise RuntimeError('东财返回空 data')
        return klines

    pref_host = _fflow_hist_preferred['host']
    if pref_host:
        for _ in range(4):
            try:
                return _attempt(pref_host, _fflow_hist_preferred['use_proxy'])
            except Exception as e:  # noqa: BLE001
                last_err = e
                time.sleep(0.6)

    for host in EM_HOSTS:
        for use_proxy in (False, True):
            for attempt in range(2):
                try:
                    klines = _attempt(host, use_proxy)
                    _fflow_hist_preferred['host'] = host
                    _fflow_hist_preferred['use_proxy'] = use_proxy
                    return klines
                except Exception as e:  # noqa: BLE001
                    last_err = e
                    time.sleep(0.6)
    raise last_err if last_err else RuntimeError('历史资金流请求失败')


def backfill_industry_fund_flow(trade_date):
    """回补指定交易日的行业资金流快照（历史接口逐行业）。

    探针先行：第 1 个行业的历史中无该日（休市日）则跳过全部，避免 496 次空跑。
    连续失败超限即中止（东财整体不可达时快速放弃，冷却由调用方处理）。
    Returns: {'ok', 'count'/'skipped'/'error', 'trade_date'}
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT code, name FROM industry_fund_flow WHERE trade_date = '
            '(SELECT MAX(trade_date) FROM industry_fund_flow) GROUP BY code, name')
        codes = [(r['code'], r['name']) for r in cursor.fetchall()]
    finally:
        conn.close()
    if not codes:
        return {'ok': False, 'error': '无行业代码基准（先成功刷新一次）', 'trade_date': trade_date}

    def _flush(rows):
        """分块落库（回补开始时已清空该日，纯 INSERT 即幂等）。"""
        if not rows:
            return
        conn = get_connection()
        try:
            conn.executemany(
                'INSERT INTO industry_fund_flow '
                '(trade_date, code, name, pct_change, main_net, main_pct, super_net, '
                'big_net, mid_net, small_net, lead_stock) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                [(trade_date, it['code'], it['name'], it['pct_change'], it['main_net'],
                  it['main_pct'], it['super_net'], it['big_net'], it['mid_net'],
                  it['small_net'], it['lead_stock']) for it in rows],
            )
            conn.commit()
        finally:
            conn.close()

    # 开局清空该日（幂等重跑），之后分块提交——中止/崩溃最多损失当前未满块
    conn = get_connection()
    try:
        conn.execute('DELETE FROM industry_fund_flow WHERE trade_date = ?', (trade_date,))
        conn.commit()
    finally:
        conn.close()

    items = []
    flushed = 0
    consecutive_fails = 0
    for i, (code, name) in enumerate(codes):
        try:
            klines = _request_fflow_hist_robust(code)
        except Exception as e:  # noqa: BLE001
            consecutive_fails += 1
            logger.warning('[行业资金流] 回补%s 第%d/%d个行业(%s)失败: %s',
                           trade_date, i + 1, len(codes), code, e)
            if consecutive_fails >= _BACKFILL_CONSECUTIVE_FAIL_LIMIT:
                _flush(items[flushed:])
                return {'ok': False, 'error': f'连续{consecutive_fails}个行业失败，中止回补',
                        'trade_date': trade_date, 'fetched': len(items)}
            continue
        consecutive_fails = 0
        row = next((r for r in klines if r.split(',')[0] == trade_date), None)
        if i == 0 and row is None:
            return {'ok': False, 'skipped': '探针行业历史中无该日（休市或超回看范围）',
                    'trade_date': trade_date}
        item = _parse_fflow_hist_row(row, trade_date, code, name) if row else None
        if item:
            items.append(item)
        if len(items) - flushed >= 100:
            _flush(items[flushed:])
            flushed = len(items)
        if (i + 1) % 50 == 0:
            logger.info('[行业资金流] 回补%s 进度: %d/%d（已取 %d）', trade_date, i + 1, len(codes), len(items))
        time.sleep(0.2)

    if not items:
        return {'ok': False, 'error': '回补结果为空', 'trade_date': trade_date}
    _flush(items[flushed:])   # 收尾提交剩余
    return {'ok': True, 'count': len(items), 'trade_date': trade_date}


def _previous_weekday(date_str):
    """date_str 的上一个工作日（周一回退到周五）。"""
    d = datetime.strptime(date_str, '%Y-%m-%d').date()
    d -= timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d.strftime('%Y-%m-%d')


def maybe_backfill_gap_async():
    """刷新成功后检测近期工作日缺口（最多回看 10 个日历日），后台线程回补。

    多日断连会留下多个缺口：按时间正序逐日回补，缺几天补几天。
    休市/节假日由回补内探针自动跳过；进行中不重复触发。
    Returns: 启动回补时的缺口日期列表（无缺口/已在进行中返回 None）。
    """
    if not _backfill_thread_lock.acquire(blocking=False):
        return None
    try:
        if _backfill_running['flag']:
            return None
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('SELECT MAX(trade_date) FROM industry_fund_flow')
            row = cursor.fetchone()
            latest = row[0] if row else None
            gap_dates = []
            if latest:
                cursor.execute(
                    'SELECT DISTINCT trade_date FROM industry_fund_flow '
                    'WHERE trade_date >= ?',
                    ((datetime.strptime(str(latest)[:10], '%Y-%m-%d')
                      - timedelta(days=10)).strftime('%Y-%m-%d'),))
                have = {str(r['trade_date'])[:10] for r in cursor.fetchall()}
                probe = str(latest)[:10]
                for _ in range(10):
                    probe = _previous_weekday(probe)
                    if probe in have:
                        break           # 从最新日向前连续无缺口即止
                    gap_dates.append(probe)
                gap_dates.reverse()     # 时间正序逐日补
        finally:
            conn.close()
        if not gap_dates:
            return None
        _backfill_running['flag'] = True

        def _worker():
            try:
                for gd in gap_dates:
                    result = backfill_industry_fund_flow(gd)
                    logger.info('[行业资金流] 缺口回补 %s: %s', gd, result)
            except Exception as e:  # noqa: BLE001
                logger.warning('[行业资金流] 缺口回补异常: %s', e)
            finally:
                _backfill_running['flag'] = False

        t = threading.Thread(target=_worker, name='fflow-gap-backfill', daemon=True)
        t.start()
        return gap_dates
    finally:
        _backfill_thread_lock.release()


def get_latest_industry_fund_flow():
    """读库最新交易日快照 → (items, trade_date, updated_at)；无数据返回 ([], None, None)。"""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(trade_date) AS d FROM industry_fund_flow')
        row = cursor.fetchone()
        if not row or not row['d']:
            return [], None, None
        trade_date = row['d']
        cursor.execute(
            'SELECT code, name, pct_change, main_net, main_pct, super_net, big_net, mid_net, small_net, lead_stock '
            'FROM industry_fund_flow WHERE trade_date = ? ORDER BY main_net DESC',
            (trade_date,),
        )
        items = [dict(r) for r in cursor.fetchall()]
        cursor.execute(
            'SELECT MAX(created_at) AS t FROM industry_fund_flow WHERE trade_date = ?', (trade_date,)
        )
        crow = cursor.fetchone()
        return items, trade_date, (crow['t'] if crow else None)
    finally:
        conn.close()


def get_industry_fund_flow_dates():
    """020R-53：可用交易日列表（新→旧），供前端时间维度选择。"""
    conn = get_connection()
    try:
        rows = conn.execute(
            'SELECT DISTINCT trade_date FROM industry_fund_flow ORDER BY trade_date DESC'
        ).fetchall()
        return [str(r['trade_date']) for r in rows]
    finally:
        conn.close()


def get_industry_fund_flow_for_date(trade_date):
    """020R-53：读取指定交易日快照 → (items, updated_at)。

    items 每行附加 main_net_5d：截至该日（含）的前 5 个交易日主力净流入累计，
    用于行业资金流向的时间维度趋势展示。
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT code, name, pct_change, main_net, main_pct, super_net, big_net, mid_net, small_net, lead_stock '
            'FROM industry_fund_flow WHERE trade_date = ? ORDER BY main_net DESC',
            (trade_date,),
        )
        items = [dict(r) for r in cursor.fetchall()]

        # 截至该日的前 5 个交易日累计（含当日）
        cursor.execute(
            """
            WITH recent AS (
                SELECT DISTINCT trade_date FROM industry_fund_flow
                WHERE trade_date <= ? ORDER BY trade_date DESC LIMIT 5
            )
            SELECT code, SUM(main_net) AS main_net_5d
            FROM industry_fund_flow
            WHERE trade_date IN (SELECT trade_date FROM recent)
            GROUP BY code
        """,
            (trade_date,),
        )
        s5 = {r['code']: r['main_net_5d'] for r in cursor.fetchall()}

        # 020R-54：连续净流入/流出天数（正=连续流入，负=连续流出）
        streaks = compute_streaks(trade_date)

        for it in items:
            it['main_net_5d'] = s5.get(it['code'])
            it['streak_days'] = streaks.get(it['code'], 0)

        cursor.execute(
            'SELECT MAX(created_at) AS t FROM industry_fund_flow WHERE trade_date = ?', (trade_date,)
        )
        crow = cursor.fetchone()
        return items, (crow['t'] if crow else None)
    finally:
        conn.close()


# ============================================================
# 020R-54：时间维度利用——市场温度计 / 连续方向 / 个股行业资金背景
# ============================================================

# 自选股行业名（同花顺旧口径）→ 东财行业板块名（申万 2021 口径）别名映射
INDUSTRY_ALIAS = {
    '医药制造': '医药生物',
    '保健食品': '食品饮料',
    '家电行业': '家用电器',
    '物流行业': '物流',
    '电气设备': '电力设备',
    '酿酒行业': '食品饮料',
    '禽畜养殖': '农林牧渔',
    '旅游酒店': '社会服务',
    '食品加工': '食品饮料',
    '安防设备': '计算机设备',
}


def match_board_name(industry, board_names):
    """自选股行业名 → 东财行业板块名；精确 → 别名 → 双向子串，均无则 None。"""
    if not industry:
        return None
    if industry in board_names:
        return industry
    alias = INDUSTRY_ALIAS.get(industry)
    if alias and alias in board_names:
        return alias
    for b in board_names:
        if industry in b or b in industry:
            return b
    return None


def compute_streaks(trade_date):
    """020R-54：每个行业截至 trade_date 的连续净流入/流出天数。

    返回 {code: int}：正数=连续净流入天数，负数=连续净流出天数，0=首日或无方向。
    """
    conn = get_connection()
    try:
        rows = conn.execute(
            'SELECT code, trade_date, main_net FROM industry_fund_flow '
            'WHERE trade_date <= ? ORDER BY code, trade_date DESC',
            (trade_date,),
        ).fetchall()
        by_code: dict = {}
        for r in rows:
            by_code.setdefault(r['code'], []).append(r['main_net'])
        streaks = {}
        for code, seq in by_code.items():
            days = 0
            for mn in seq:  # 最新在前
                if mn is None or mn == 0:
                    break
                sgn = 1 if mn > 0 else -1
                if days == 0:
                    days = sgn
                elif (days > 0) == (sgn > 0):
                    days += sgn
                else:
                    break
            streaks[code] = days
        return streaks
    finally:
        conn.close()


def get_industry_flow_summary(trade_date):
    """020R-54：市场资金温度计——全行业主力净流入合计、流入/流出/持平家数。"""
    conn = get_connection()
    try:
        row = conn.execute(
            'SELECT COUNT(*) n, SUM(main_net) total_net, '
            'SUM(CASE WHEN main_net > 0 THEN 1 ELSE 0 END) inflow_n, '
            'SUM(CASE WHEN main_net < 0 THEN 1 ELSE 0 END) outflow_n '
            'FROM industry_fund_flow WHERE trade_date = ?',
            (trade_date,),
        ).fetchone()
        if not row or not row['n']:
            return None
        n = row['n']
        inflow = row['inflow_n'] or 0
        outflow = row['outflow_n'] or 0
        return {
            'trade_date': trade_date,
            'total_net': row['total_net'],
            'total': n,
            'inflow_count': inflow,
            'outflow_count': outflow,
            'flat_count': n - inflow - outflow,
        }
    finally:
        conn.close()


def get_industry_flow_bg_map(trade_date=None):
    """020R-54：最新（或指定）交易日全部板块的资金背景字典 {板块名: bg}。

    bg: {board, trade_date, main_net, main_pct, pct_change, rank, total, streak_days}
    供个股行业背景批量关联（自选股看板/建议/每日报告共用，一次计算全板块）。
    """
    conn = get_connection()
    try:
        if not trade_date:
            row = conn.execute('SELECT MAX(trade_date) d FROM industry_fund_flow').fetchone()
            trade_date = row['d'] if row else None
        if not trade_date:
            return {}
        rows = conn.execute(
            'SELECT code, name, main_net, main_pct, pct_change FROM industry_fund_flow '
            'WHERE trade_date = ? ORDER BY main_net DESC',
            (trade_date,),
        ).fetchall()
        if not rows:
            return {}
        total = len(rows)
        streaks = compute_streaks(trade_date)
        bg_map = {}
        for i, r in enumerate(rows):
            bg_map[r['name']] = {
                'board': r['name'],
                'trade_date': trade_date,
                'main_net': r['main_net'],
                'main_pct': r['main_pct'],
                'pct_change': r['pct_change'],
                'rank': i + 1,
                'total': total,
                'streak_days': streaks.get(r['code'], 0),
            }
        return bg_map
    finally:
        conn.close()


def get_industry_flow_bg(industry, trade_date=None):
    """020R-54：个股所属行业资金背景 → dict 或 None（无板块匹配时）。"""
    if not industry:
        return None
    bg_map = get_industry_flow_bg_map(trade_date)
    if not bg_map:
        return None
    board = match_board_name(industry, list(bg_map.keys()))
    return bg_map.get(board) if board else None
