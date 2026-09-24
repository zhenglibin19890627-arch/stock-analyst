"""
021BX t7 数据完整度实施测试：K2 回测新鲜度断言 + 港股资金面缺口盘点 + K6 健康提示

覆盖（t7 实施「每修一项附测试 + 审计/扫描脚本同步断言化」）：
1. 审计脚本 K2 断言分支：回测样本最新评级日 vs 评级最新评级日差 ≤2 天=对齐（✅）、
   超 2 天=告警（⚠️）——防港股回测停更复发（K2 根因=fill_pending_backtests 未接线）
2. 港股资金面缺口盘点 find_gaps：以该股自身 K 线交易日为日历，
   main_net_inflow 非空才算已覆盖（估算/占位行不算）
3. K6 连通性健康提示：orderbook 说明列带源侧断供事实 + 展示维降级声明 + 文案禁裸 '<'

隔离：临时库（monkeypatch DB_PATH），不触网、不触碰真实库。
"""

import importlib.util
import os

import pytest

from database import db_manager
from database.db_manager import get_connection, init_database

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_script(stem: str):
    """按路径加载 scripts/ 下脚本模块（与审计 r2 复用 r1 同款机制）。"""
    path = os.path.join(_ROOT, 'scripts', f'{stem}.py')
    spec = importlib.util.spec_from_file_location(stem, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_file = tmp_path / 't_021bx.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('HK0700', 'hk_stock', '腾讯控股')")
    conn.commit()
    conn.close()
    yield db_manager


def _seed_hk_backtest(db, rating_date, rating_id=1):
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO ratings_history (stock_id, rating_date, rating, total_score, "
        "price_at_rating) VALUES (1, '2026-09-20', '持有观望', 55.0, 100.0)")
    conn.execute(
        'INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, '
        "price_at_rating) VALUES (1, ?, 'hk_stock', ?, '持有观望', 100.0)",
        (rating_id, rating_date),
    )
    conn.commit()
    conn.close()


class TestK2BacktestFreshnessAssertion:
    """审计脚本 K2 断言：港股回测样本与评级对齐（差 ≤2 天），停更复发即 ⚠️。"""

    def _k2(self):
        audit = _load_script('audit_data_completeness_021bx')
        conn = db_manager.get_connection()
        cur = conn.cursor()
        items = audit.known_items(cur)
        conn.close()
        k2 = next(it for it in items if it['id'] == 'K2')
        return k2

    def test_aligned_pass(self, db):
        """回测覆盖最新评级 → 差 0 天 → ✅（t7 补跑后的常态）。"""
        _seed_hk_backtest(db, '2026-09-20')
        k2 = self._k2()
        assert '✅' in k2['status']
        assert '差 0 天' in k2['status']

    def test_stall_warns(self, db):
        """评级前进而回测停滞 → 差超 2 天 → ⚠️（K2 停更复发即触发断言）。"""
        _seed_hk_backtest(db, '2026-09-10')  # 评级 09-20，回测止步 09-10
        k2 = self._k2()
        assert '⚠️' in k2['status']
        assert '落后评级 10 天' in k2['status']

    def test_no_bare_less_than(self, db):
        _seed_hk_backtest(db, '2026-09-20')
        k2 = self._k2()
        assert '<' not in k2['status']
        assert '<' not in k2['evidence']


class TestFindGaps:
    """港股资金面缺口盘点：以该股自身 K 线交易日为日历，真实行才算覆盖。"""

    def _seed(self, db, with_capital_dates):
        conn = db_manager.get_connection()
        kdates = ['2026-09-24', '2026-09-23', '2026-09-22', '2026-09-21', '2026-09-18']
        for d in kdates:
            conn.execute(
                'INSERT INTO raw_kline (stock_id, trade_date, close) VALUES (1, ?, 100.0)', (d,))
        for d in with_capital_dates:
            conn.execute(
                'INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow, '
                'is_estimated) VALUES (1, ?, -100.0, 0)', (d,))
        conn.commit()
        conn.close()
        return kdates

    def test_detects_missing_days(self, db):
        hk = _load_script('backfill_hk_capital_021bx')
        kdates = self._seed(db, with_capital_dates=['2026-09-18', '2026-09-21', '2026-09-22'])
        gaps = hk.find_gaps(window=5)
        assert 'HK0700' in gaps
        assert gaps['HK0700']['missing_dates'] == ['2026-09-23', '2026-09-24']
        assert gaps['HK0700']['window_dates'] == kdates  # 窗口=该股自身交易日（降序）

    def test_estimated_placeholder_not_covered(self, db):
        """估算/占位行（main_net_inflow 为 NULL）不算覆盖——回补以真实行为目标。"""
        hk = _load_script('backfill_hk_capital_021bx')
        conn = db_manager.get_connection()
        for d in ('2026-09-18', '2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24'):
            conn.execute(
                'INSERT INTO raw_kline (stock_id, trade_date, close) VALUES (1, ?, 100.0)', (d,))
        conn.execute(
            'INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow, '
            'is_estimated) VALUES (1, ?, NULL, 1)', ('2026-09-18',))  # 估算占位行
        conn.commit()
        conn.close()
        gaps = hk.find_gaps(window=5)
        # 09-18 虽有行但 main_net_inflow 为 NULL → 仍算缺口
        assert len(gaps['HK0700']['missing_dates']) == 5

    def test_no_gap_no_entry(self, db):
        hk = _load_script('backfill_hk_capital_021bx')
        self._seed(db, with_capital_dates=[
            '2026-09-18', '2026-09-21', '2026-09-22', '2026-09-23', '2026-09-24'])
        assert hk.find_gaps(window=5) == {}


class TestK6HealthHint:
    """K6 连通性健康提示：orderbook 说明列带源侧断供事实与展示维降级声明。"""

    def test_orderbook_desc_hint(self):
        from blueprints.system import _DIMENSION_META

        desc = _DIMENSION_META['orderbook']['desc']
        assert '<' not in desc  # 文案禁裸 '<'
        assert '2026-09-10' in desc  # 源侧断供起点（事实标注）
        assert '评分链不消费' in desc  # 展示维降级声明（不阻塞评分）
        assert 'mootdx' in desc  # 数据源链路如实标注
