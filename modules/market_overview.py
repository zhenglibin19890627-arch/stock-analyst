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
