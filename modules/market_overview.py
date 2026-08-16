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
