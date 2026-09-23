"""021BV 盘中速览卡 v2 后端测试：当日浮动盈亏字段 + 当日已录流水概览
+ 行动作指引 overlay + 端点契约冒烟 + 前端排序逻辑（node harness 包装）。

镜像 tests/test_intraday_patrol.py 的 _FakeDT/临时库/零网络模式：
- 盈亏计算（①）：holdings 账户无关聚合（与持仓列表端点 unrealized_pnl 公式同源）；
- 流水概览（⑤）：trade_records 只读聚合，dividend 不计、跨日不计；
- 行动作指引（③）：stored key_factors.trader.top_action 优先（零重算），
  触线/逼近行 live 兜底（只读），其余行 stored 缺失即沉默；
- 端点（冒烟）：/api/dashboard/intraday 行增量键 + today_trades 顶层键；
- 排序逻辑（②）：纯前端实现，经 tests/js/intraday_sort_test.js（node）验证，
  node 缺失时跳过（CI 兼容）。

全程零写库契约断言（price_cache 巡检写入除外，本文件不触发巡检写路径）。
"""

import datetime as _dt
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import modules.intraday_patrol as patrol
from database import db_manager

TRADING_DT = _dt.datetime(2026, 9, 23, 10, 30)  # 周三盘中
TODAY = '2026-09-23'


class _FakeDT:
    """伪造 datetime：now() 返回固定时刻"""

    def __init__(self, real):
        self._real = real

    def now(self, tz=None):
        return self._real


@pytest.fixture(autouse=True)
def _reset_patrol_state(monkeypatch):
    """每例重置巡检内存态（镜像 test_intraday_patrol）。"""
    monkeypatch.setattr(patrol, '_LAST_SNAPSHOT', None)
    monkeypatch.setattr(patrol, '_consecutive_failures', 0)
    monkeypatch.setattr(patrol, '_paused', False)
    monkeypatch.setattr(patrol, '_stale_rounds', 0)
    monkeypatch.setattr(patrol, '_last_manual_refresh_ts', 0.0)
    monkeypatch.setattr(patrol, '_timer', None)
    yield


@pytest.fixture
def pdb(tmp_path, monkeypatch):
    """隔离库：两只持仓（成本 10.0/qty 1000 与成本 5.0/qty 2000），无 K 线无缓存"""
    db_file = tmp_path / 'patrol_v2.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600276', 'a_stock', '恒瑞医药')")
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('003816', 'a_stock', '广弘控股')")
    conn.execute(
        'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
        'VALUES (1, 1, 10.0, 1000)')
    conn.execute(
        'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
        'VALUES (1, 2, 5.0, 2000)')
    conn.commit()
    conn.close()
    return db_manager


def _patch_clock(monkeypatch, dt=TRADING_DT, in_session=True):
    monkeypatch.setattr(patrol, 'datetime', _FakeDT(dt))
    monkeypatch.setattr(patrol, '_is_intraday_session', lambda market='a_stock': in_session)


def _seed_trades(rows):
    """rows: [(stock_id, trade_type, amount, trade_date)]；commission 独立场景另插"""
    conn = db_manager.get_connection()
    for sid, ttype, amount, tdate in rows:
        conn.execute(
            'INSERT INTO trade_records (account_id, stock_id, trade_type, price, '
            'quantity, amount, commission, trade_date) '
            'VALUES (1, ?, ?, 10.0, 100, ?, 5.0, ?)',
            (sid, ttype, amount, tdate),
        )
    conn.commit()
    conn.close()


def _seed_report_with_top_action(stock_id, top_action, report_date='2026-09-22'):
    """日报带 key_factors.trader.top_action（stored 摘要形态）"""
    kf = json.dumps({'trader': {'stage_name': '震荡持仓',
                                'has_disagreement': False,
                                'top_action': top_action}})
    conn = db_manager.get_connection()
    conn.execute(
        """INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name,
           total_score, rating, rating_label, score_change, status, report_type,
           key_factors)
           VALUES (?, ?, '600276', '恒瑞医药', 60.0, '持有观望', '持有观望',
                   0.0, 'ok', 'daily', ?)""",
        (report_date, stock_id, kf),
    )
    conn.commit()
    conn.close()


# ============================================================
# 一、① 当日浮动盈亏字段（build_snapshot 行增量，holdings 聚合同源）
# ============================================================


class TestPnlFields:
    def test_pnl_with_quote(self, pdb, monkeypatch):
        """实时价路径：现价 10.5 × 1000 股 对成本 10.0 → 盈 500.00（+5.0%）"""
        _patch_clock(monkeypatch)
        monkeypatch.setattr(
            patrol, '_fetch_quote_snapshot_batch',
            lambda symbols: {1: {'price': 10.5, 'pct_change': 5.0, 'prev_close': None,
                                 'volume': None, 'quote_ts': '20260923103000'}})
        result = patrol.run_patrol_round()
        row = result['snapshot']['stocks'][0]
        assert row['total_qty'] == 1000
        assert row['avg_cost'] == pytest.approx(10.0)
        assert row['market_value'] == pytest.approx(10500.0)
        assert row['unrealized_pnl'] == pytest.approx(500.0)
        assert row['unrealized_pnl_pct'] == pytest.approx(5.0)

    def test_pnl_with_cache_fallback(self, pdb, monkeypatch):
        """缓存兜底路径：price_cache 10.2 → 盈 200.00（+2.0%）——公式与实时价一致"""
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO price_cache (stock_id, latest_price, pct_change, updated_at) "
            "VALUES (1, 10.2, 2.0, '2026-09-23 10:30:00')")
        conn.commit()
        conn.close()
        _patch_clock(monkeypatch, in_session=False)
        snap = patrol.build_snapshot(TRADING_DT, patrol._held_positions(), None)
        row = snap['stocks'][0]
        assert row['unrealized_pnl'] == pytest.approx(200.0)
        assert row['unrealized_pnl_pct'] == pytest.approx(2.0)
        assert row['market_value'] == pytest.approx(10200.0)

    def test_pnl_none_without_price(self, pdb, monkeypatch):
        """无价（无快照无缓存）→ 盈亏/市值显式 None，数量成本仍透出（前端显示'—'）"""
        _patch_clock(monkeypatch, in_session=False)
        snap = patrol.build_snapshot(TRADING_DT, patrol._held_positions(), None)
        row = snap['stocks'][0]
        assert row['price'] is None
        assert row['market_value'] is None
        assert row['unrealized_pnl'] is None
        assert row['unrealized_pnl_pct'] is None
        assert row['total_qty'] == 1000
        assert row['avg_cost'] == pytest.approx(10.0)

    def test_pnl_multi_account_weighted_cost(self, pdb, monkeypatch):
        """多账户分仓：同股 acc1(10.0×1000) + acc2(5.0×1000) → 加权成本 7.5，
        现价 10.5 → 盈亏 (10.5-7.5)×2000 = 6000（021W 账户无关聚合口径）"""
        conn = db_manager.get_connection()
        conn.execute(
            'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
            'VALUES (2, 1, 5.0, 1000)')
        conn.execute(
            "INSERT INTO price_cache (stock_id, latest_price, pct_change, updated_at) "
            "VALUES (1, 10.5, 5.0, '2026-09-23 10:30:00')")
        conn.commit()
        conn.close()
        _patch_clock(monkeypatch, in_session=False)
        snap = patrol.build_snapshot(TRADING_DT, patrol._held_positions(), None)
        row = snap['stocks'][0]
        assert row['total_qty'] == 2000
        assert row['avg_cost'] == pytest.approx(7.5)
        assert row['unrealized_pnl'] == pytest.approx(6000.0)
        assert row['unrealized_pnl_pct'] == pytest.approx(40.0)
        assert row['market_value'] == pytest.approx(21000.0)

    def test_pnl_none_zero_cost(self, pdb, monkeypatch):
        """成本 0（清仓残留/未录成本）→ avg_cost 归 None → 盈亏不计算（防除零）"""
        conn = db_manager.get_connection()
        conn.execute('UPDATE holdings SET cost_price = 0 WHERE stock_id = 1')
        conn.commit()
        conn.close()
        _patch_clock(monkeypatch, in_session=False)
        snap = patrol.build_snapshot(TRADING_DT, patrol._held_positions(), None)
        row = snap['stocks'][0]
        assert row['avg_cost'] is None
        assert row['unrealized_pnl'] is None
        assert row['unrealized_pnl_pct'] is None


# ============================================================
# 二、⑤ 当日已录流水概览（trade_records 只读聚合）
# ============================================================


class TestTodayTrades:
    def test_counts_and_amounts(self, pdb, monkeypatch):
        """今日 买2 笔 1000 / 卖1 笔 800；昨日流水与 dividend 均不计"""
        _seed_trades([
            (1, 'buy', 600.0, TODAY),
            (1, 'buy', 400.0, TODAY),
            (2, 'sell', 800.0, TODAY),
            (1, 'buy', 9999.0, '2026-09-22'),   # 昨日：不计
            (2, 'dividend', 50.0, TODAY),        # 分红：不计
        ])
        _patch_clock(monkeypatch)
        tt = patrol._read_today_trades(TODAY)
        assert tt['buy_count'] == 2
        assert tt['buy_amount'] == pytest.approx(1000.0)
        assert tt['sell_count'] == 1
        assert tt['sell_amount'] == pytest.approx(800.0)
        assert tt['total_count'] == 3

    def test_empty_day_all_zero(self, pdb):
        tt = patrol._read_today_trades(TODAY)
        assert tt == {'buy_count': 0, 'buy_amount': 0.0,
                      'sell_count': 0, 'sell_amount': 0.0, 'total_count': 0}

    def test_attached_to_dashboard_snapshot(self, pdb, monkeypatch):
        """get_snapshot_for_dashboard 读取时现查（非巡检轮缓存）——录流水后刷新即见"""
        _seed_trades([(1, 'buy', 12345.0, TODAY)])
        _patch_clock(monkeypatch)
        snap = patrol.get_snapshot_for_dashboard()
        assert snap['today_trades']['total_count'] == 1
        assert snap['today_trades']['buy_amount'] == pytest.approx(12345.0)

    def test_no_trades_key_still_present(self, pdb, monkeypatch):
        """零流水日：键仍存在（前端无需判缺），total_count=0"""
        _patch_clock(monkeypatch)
        snap = patrol.get_snapshot_for_dashboard()
        assert snap['today_trades']['total_count'] == 0


# ============================================================
# 三、③ 行动作指引 overlay（stored 优先 / 警示行 live 兜底 / 其余沉默）
# ============================================================


class TestTopActionOverlay:
    def test_stored_top_action_used(self, pdb, monkeypatch):
        """stored key_factors.trader.top_action 零重算透出，source='stored'"""
        _seed_report_with_top_action(1, '止损·9.20')
        _patch_clock(monkeypatch, in_session=False)
        snap = patrol.get_snapshot_for_dashboard()
        rows = {r['stock_id']: r for r in snap['stocks']}
        assert rows[1]['top_action'] == '止损·9.20'
        assert rows[1]['top_action_source'] == 'stored'
        assert rows[2]['top_action'] is None  # 无报告股沉默

    def test_live_fallback_only_for_alert_rows(self, pdb, monkeypatch):
        """stored 缺失：仅触线行 live 兜底（derive_trader_signal_summary），
        无数据行不触发（控成本）；缓存价 9.0 破成本线 9.2 → below_stop"""
        calls = []

        def _fake_live(kf_json=None, stock_id=None):
            calls.append(stock_id)
            return {'stage_name': '震荡持仓', 'has_disagreement': False,
                    'disagreement_text': None, 'top_action': '止损·9.20（已触发）'}

        import modules.trader_advisor as ta
        monkeypatch.setattr(ta, 'derive_trader_signal_summary', _fake_live)
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO price_cache (stock_id, latest_price, pct_change, updated_at) "
            "VALUES (1, 9.0, -2.0, '2026-09-22 15:00:00')")
        conn.commit()
        conn.close()
        _patch_clock(monkeypatch, in_session=False)  # 非时段：fallback 快照（缓存价）
        snap = patrol.get_snapshot_for_dashboard()
        rows = {r['stock_id']: r for r in snap['stocks']}
        assert rows[1]['state'] == 'below_stop'
        assert rows[1]['top_action'] == '止损·9.20（已触发）'
        assert rows[1]['top_action_source'] == 'live'
        assert rows[2]['top_action'] is None
        assert calls == [1]  # live 兜底仅触线行一次

    def test_normal_row_without_stored_stays_silent(self, pdb, monkeypatch):
        """普通行（非触线/逼近）stored 缺失 → 不现算，top_action=None"""

        def _boom(kf_json=None, stock_id=None):
            raise AssertionError('非警示行不应触发 live 现算')

        import modules.trader_advisor as ta
        monkeypatch.setattr(ta, 'derive_trader_signal_summary', _boom)
        _patch_clock(monkeypatch, in_session=False)
        snap = patrol.get_snapshot_for_dashboard()
        for r in snap['stocks']:
            assert r['top_action'] is None

    def test_overlay_failure_silent(self, pdb, monkeypatch):
        """overlay 整体异常 → 静默留空，不阻塞速览端点"""
        _patch_clock(monkeypatch)

        def _boom(rows):
            raise RuntimeError('overlay 崩溃')

        monkeypatch.setattr(patrol, '_scan_today_top_actions', _boom)
        snap = patrol.get_snapshot_for_dashboard()
        assert snap['success'] is True
        for r in snap['stocks']:
            assert r['top_action'] is None

    def test_stored_parse_failure_falls_to_none(self, pdb, monkeypatch):
        """key_factors 坏 JSON → stored 解析失败，非警示行不兜底 → None（不抛错）"""
        conn = db_manager.get_connection()
        conn.execute(
            """INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name,
               total_score, rating, rating_label, score_change, status, report_type,
               key_factors)
               VALUES ('2026-09-22', 1, '600276', '恒瑞医药', 60.0, '持有观望',
                       '持有观望', 0.0, 'ok', 'daily', '{broken json')""")
        conn.commit()
        conn.close()
        _patch_clock(monkeypatch, in_session=False)
        snap = patrol.get_snapshot_for_dashboard()
        rows = {r['stock_id']: r for r in snap['stocks']}
        assert rows[1]['top_action'] is None


# ============================================================
# 四、端点契约冒烟（GET /api/dashboard/intraday 行/顶层增量键）
# ============================================================


class TestDashboardEndpointV2:
    def test_endpoint_contract_v2(self, pdb, monkeypatch):
        import app as app_module

        _seed_trades([(1, 'buy', 500.0, TODAY), (1, 'sell', 300.0, TODAY)])
        _seed_report_with_top_action(1, '持有·MA20=10.00')
        _patch_clock(monkeypatch)
        app_module.app.config['TESTING'] = True
        client = app_module.app.test_client()
        resp = client.get('/api/dashboard/intraday')
        assert resp.status_code == 200, resp.data[:300]
        body = resp.get_json()
        assert body['success'] is True
        # 顶层增量：today_trades（⑤）
        assert body['today_trades']['total_count'] == 2
        assert body['today_trades']['buy_amount'] == pytest.approx(500.0)
        assert body['today_trades']['sell_amount'] == pytest.approx(300.0)
        # 行增量：盈亏（①）+ 行动作指引（③）
        row = body['stocks'][0]
        for key in ('total_qty', 'avg_cost', 'market_value',
                    'unrealized_pnl', 'unrealized_pnl_pct',
                    'top_action', 'top_action_source', 'signal_labels'):
            assert key in row, f'行缺增量键 {key}'
        assert row['unrealized_pnl'] is None or isinstance(
            row['unrealized_pnl'], (int, float))
        sid_row = {r['stock_id']: r for r in body['stocks']}[1]
        assert sid_row['top_action'] == '持有·MA20=10.00'
        # 旧契约键零改动（回归）
        for key in ('date', 'updated_at', 'source', 'session', 'counts',
                    'thresholds', 'patrol', 'degraded', 'disclaimer'):
            assert key in body, f'顶层缺既有键 {key}'
        # 后端副本零裸 '<'（文案契约）
        assert '<' not in json.dumps(body, ensure_ascii=False)


# ============================================================
# 五、② 前端排序逻辑（node 契约测试包装；node 缺失跳过）
# ============================================================


class TestJsSortLogic:
    def test_intraday_sort_harness(self):
        node = shutil.which('node')
        if not node:
            pytest.skip('node 不可用，跳过前端排序逻辑契约测试')
        script = Path(__file__).resolve().parent / 'js' / 'intraday_sort_test.js'
        result = subprocess.run(
            [node, str(script)], capture_output=True, text=True, timeout=60,
            cwd=str(Path(__file__).resolve().parent.parent),
        )
        assert result.returncode == 0, (
            f'排序契约测试失败:\n{result.stdout}\n{result.stderr}')
        assert 'intraday sort tests passed' in result.stdout
