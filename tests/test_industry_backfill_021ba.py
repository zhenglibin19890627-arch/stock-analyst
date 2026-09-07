"""
行业分类自愈测试（021BA）

覆盖：
1. fetch_stock_industry 三级源链：EM 直连（f127）→ akshare 备源 → 本地映射 → 未分类
2. patient 参数对 EM 直连轮数的控制（同步场景 1 轮 / 后台场景 2 轮）
3. _maybe_backfill_industry：无缺口零请求 / 缺口限量补取 / 失败不写库

隔离：全部 monkeypatch，不触网；调度器测试用临时库（对齐 test_backfill_scheduler 模式）。
"""

import pandas as pd
import pytest

from database import db_manager
from database.db_manager import get_connection, init_database
from modules import backfill_scheduler as bs
from modules import data_collector as dc
from modules.collector import sentiment_industry as _si  # noqa: E402  # OPT-3 补丁指向实现子模块

# ============================================================
# 工具
# ============================================================


class _FakeResp:
    """模拟 requests.Response：_http_get_em 的返回只需 .json()"""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


@pytest.fixture()
def tmp_db(tmp_path, monkeypatch):
    """临时库 + 初始化表结构"""
    db_file = tmp_path / 'test_industry.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()
    return str(db_file)


# ============================================================
# 1. fetch_stock_industry 源链
# ============================================================


def test_industry_em_direct_success(monkeypatch):
    """EM 直连返回有效 f127 → 直接采用（东财行业名体系）"""
    monkeypatch.setattr(_si, '_em_banned', lambda: False)
    monkeypatch.setattr(
        _si,
        '_http_get_em',
        lambda *a, **k: _FakeResp({'data': {'f127': '半导体'}}),
    )
    assert dc.fetch_stock_industry('300034', 'a_stock') == '半导体'


def test_industry_patient_controls_retry_rounds(monkeypatch):
    """patient=True → EM 直连 max_retries=2；默认（同步场景）→ 1 轮快速失败"""
    captured = {}

    def fake_get(url, params=None, timeout=15, max_retries=None):
        captured['max_retries'] = max_retries
        return _FakeResp({'data': {'f127': '橡胶'}})

    monkeypatch.setattr(_si, '_em_banned', lambda: False)
    monkeypatch.setattr(_si, '_http_get_em', fake_get)
    dc.fetch_stock_industry('002716', 'a_stock')
    assert captured['max_retries'] == 1
    dc.fetch_stock_industry('002716', 'a_stock', patient=True)
    assert captured['max_retries'] == 2


def test_industry_em_dash_falls_to_akshare(monkeypatch):
    """EM 直连返回 '-'（无效）→ 降级 akshare 备源"""
    monkeypatch.setattr(_si, '_em_banned', lambda: False)
    monkeypatch.setattr(
        _si, '_http_get_em', lambda *a, **k: _FakeResp({'data': {'f127': '-'}})
    )
    monkeypatch.setattr(
        dc.ak,
        'stock_individual_info_em',
        lambda symbol: pd.DataFrame([{'item': '行业', 'value': '轮胎'}]),
    )
    assert dc.fetch_stock_industry('002716', 'a_stock') == '轮胎'


def test_industry_all_fail_local_map(monkeypatch):
    """EM 直连/akshare 全挂 + 本地映射命中 → 返回映射值"""
    monkeypatch.setattr(_si, '_em_banned', lambda: True)  # 熔断期跳过 EM 直连

    def _raise(symbol):
        raise ConnectionError('EM down')

    monkeypatch.setattr(dc.ak, 'stock_individual_info_em', _raise)
    assert dc.fetch_stock_industry('600519', 'a_stock') == '酿酒行业'


def test_industry_all_fail_unmapped(monkeypatch):
    """三级源全挂且无本地映射 → 返回'未分类'（不抛异常，不阻塞主流程）"""
    monkeypatch.setattr(_si, '_em_banned', lambda: True)

    def _raise(symbol):
        raise ConnectionError('EM down')

    monkeypatch.setattr(dc.ak, 'stock_individual_info_em', _raise)
    assert dc.fetch_stock_industry('999999', 'a_stock') == '未分类'


def test_industry_hk_no_network(monkeypatch):
    """港股恒返'港股'，不发起任何请求"""

    def _boom(*a, **k):
        raise AssertionError('港股不应发起行业请求')

    monkeypatch.setattr(_si, '_http_get_em', _boom)
    assert dc.fetch_stock_industry('00700', 'hk_stock') == '港股'


# ============================================================
# 2. _maybe_backfill_industry 调度器自愈
# ============================================================


def _insert_stock(symbol, industry, market='a_stock'):
    conn = get_connection()
    try:
        conn.execute(
            'INSERT INTO stocks (symbol, market, name, industry) VALUES (?, ?, ?, ?)',
            (symbol, market, f'股{symbol}', industry),
        )
        conn.commit()
        return conn.execute(
            'SELECT id FROM stocks WHERE symbol = ?', (symbol,)
        ).fetchone()['id']
    finally:
        conn.close()


def test_backfill_no_gap_zero_request(tmp_db, monkeypatch):
    """无未分类股票 → 零请求、零写库"""

    def _boom(*a, **k):
        raise AssertionError('无缺口不应发起行业请求')

    monkeypatch.setattr(dc, 'fetch_stock_industry', _boom)
    _insert_stock('600100', '半导体')  # 已分类
    bs._maybe_backfill_industry()


def test_backfill_success_writes_db(tmp_db, monkeypatch):
    """缺口股票补取成功 → 写库；仍返'未分类'的失败股不写库"""
    _insert_stock('300034', '未分类')
    _insert_stock('002716', '未分类')

    def fake_fetch(symbol, market='a_stock', patient=False):
        assert patient is True  # 后台场景必须 patient
        return {'300034': '航空装备'}.get(symbol, '未分类')

    monkeypatch.setattr(dc, 'fetch_stock_industry', fake_fetch)
    bs._maybe_backfill_industry()

    conn = get_connection()
    try:
        ind = {
            r['symbol']: r['industry']
            for r in conn.execute('SELECT symbol, industry FROM stocks').fetchall()
        }
    finally:
        conn.close()
    assert ind['300034'] == '航空装备'  # 成功 → 已更新
    assert ind['002716'] == '未分类'  # 失败 → 保持原样，留给下轮


def test_backfill_limited_per_round(tmp_db, monkeypatch):
    """4 只缺口 → 每轮只补 INDUSTRY_FIX_PER_ROUND 只（随机抽样不卡队首）"""
    for s in ('600001', '600002', '600003', '600004'):
        _insert_stock(s, '未分类')
    called = []

    def fake_fetch(symbol, market='a_stock', patient=False):
        called.append(symbol)
        return '化学制药'

    monkeypatch.setattr(dc, 'fetch_stock_industry', fake_fetch)
    bs._maybe_backfill_industry()
    assert len(called) == bs.INDUSTRY_FIX_PER_ROUND


def test_backfill_fetch_exception_isolated(tmp_db, monkeypatch):
    """单只补取抛异常 → 不阻塞本轮其余股票"""

    def fake_fetch(symbol, market='a_stock', patient=False):
        if symbol == '300034':
            raise ConnectionError('网络异常')
        return '半导体'

    _insert_stock('300034', '未分类')
    _insert_stock('603501', '未分类')
    monkeypatch.setattr(dc, 'fetch_stock_industry', fake_fetch)
    bs._maybe_backfill_industry()  # 不应抛异常
    conn = get_connection()
    try:
        ind = {
            r['symbol']: r['industry']
            for r in conn.execute('SELECT symbol, industry FROM stocks').fetchall()
        }
    finally:
        conn.close()
    assert ind['300034'] == '未分类'
    assert ind['603501'] == '半导体'
