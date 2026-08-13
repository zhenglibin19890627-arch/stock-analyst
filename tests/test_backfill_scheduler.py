"""
backfill_scheduler 单元测试（P：数据完整性驱动的持续补采调度器）

覆盖：
1. 缺口检测 _get_stocks_with_gaps：各维度判定（kline/fundamental/capital/news）
2. 调度退避策略：全失败翻倍退避 / 部分成功重置 / 无缺口降为低频巡检
3. 防重复启动（幂等）

隔离：monkeypatch DB_PATH 指向临时库；tick 测试 patch _collect_one 与 _schedule_next，
不触网、不注册真实 Timer。
"""

from datetime import datetime, timedelta

import pytest

from database import db_manager
from database.db_manager import get_connection, init_database
from modules import backfill_scheduler as bs


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """临时库：完整股票 + 完整数据 1 只，缺资金面 1 只，全新无数据 1 只。"""
    db_file = tmp_path / 'test_backfill.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()

    conn = get_connection()
    today = datetime.now().strftime('%Y-%m-%d')
    days3 = (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    days5 = (datetime.now() - timedelta(days=5)).strftime('%Y-%m-%d')

    # 股票 1：完整（今日资金面 + 3天内K线/消息 + 基本面）
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600001', 'a_stock', '完整股')")
    # 股票 2：缺资金面（K线/基本面/消息面齐全）
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600002', 'a_stock', '缺资金面')")
    # 股票 3：全新无数据
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600003', 'a_stock', '全新股')")

    for i, sid in enumerate([1, 2, 3], start=1):
        # K线：股票1/2 有 3 天内数据，股票3 无
        if i < 3:
            conn.execute(
                'INSERT INTO raw_kline (stock_id, trade_date, close) VALUES (?, ?, 10.0)',
                (sid, days3),
            )
        # 基本面：股票1/2 有，股票3 无
        if i < 3:
            conn.execute(
                "INSERT INTO raw_fundamental (stock_id, report_date, roe) VALUES (?, ?, 10.0)",
                (sid, days5),
            )
        # 消息面：股票1/2 有 3 天内，股票3 无
        if i < 3:
            conn.execute(
                'INSERT INTO news_sentiment (stock_id, news_date, avg_sentiment) VALUES (?, ?, 0.1)',
                (sid, days3),
            )
        # 资金面：仅股票1 有今日数据
        if i == 1:
            conn.execute(
                'INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow) VALUES (?, ?, 100.0)',
                (sid, today),
            )

    conn.commit()
    conn.close()
    return db_file


class TestGapDetection:
    def test_detects_gaps(self, db):
        gaps = bs._get_stocks_with_gaps()
        # 股票1 完整 → 不在缺口列表
        assert 1 not in gaps, '完整股票不应有缺口'
        # 股票2 缺资金面
        assert 2 in gaps and gaps[2]['dims'] == ['capital']
        # 股票3 全维度缺
        assert 3 in gaps
        assert set(gaps[3]['dims']) == {'kline', 'fundamental', 'capital', 'news'}

    def test_all_complete_no_gaps(self, db, monkeypatch):
        # 给股票2/3 补齐资金面后：股票2 完整，股票3 仍缺其它维度
        conn = get_connection()
        today = datetime.now().strftime('%Y-%m-%d')
        for sid in (2, 3):
            conn.execute(
                'INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow) VALUES (?, ?, 100.0)',
                (sid, today),
            )
        conn.commit()
        conn.close()
        gaps = bs._get_stocks_with_gaps()
        assert 2 not in gaps
        assert 3 in gaps and 'capital' not in gaps[3]['dims']


class TestTickBackoff:
    def _run_tick(self, monkeypatch, collect_results):
        """执行一次 _tick（patch 采集与调度），返回注册的下一间隔。"""
        captured = {}

        def fake_collect(stock_id, symbol, market):
            # 按 stock_id 循环返回结果
            return collect_results.pop(0) if collect_results else True

        def fake_schedule(interval_min):
            captured['interval'] = interval_min

        monkeypatch.setattr(bs, '_collect_one', fake_collect)
        monkeypatch.setattr(bs, '_schedule_next', fake_schedule)
        monkeypatch.setattr(bs, '_backoff_min', bs.BASE_INTERVAL_MIN)
        bs._tick()
        return captured.get('interval')

    def test_all_fail_backoff_doubles(self, db, monkeypatch):
        """本轮全失败 → 间隔翻倍（30 → 60）。"""
        interval = self._run_tick(monkeypatch, [False, False, False, False, False])
        assert interval == bs.BASE_INTERVAL_MIN * 2

    def test_partial_success_resets(self, db, monkeypatch):
        """部分成功 → 重置为基础间隔。"""
        monkeypatch.setattr(bs, '_backoff_min', bs.MAX_INTERVAL_MIN)  # 先处于退避态
        interval = self._run_tick(monkeypatch, [True, True, False])
        assert interval == bs.BASE_INTERVAL_MIN

    def test_backoff_capped(self, db, monkeypatch):
        """退避不超过上限。"""
        monkeypatch.setattr(bs, '_backoff_min', bs.MAX_INTERVAL_MIN)
        interval = self._run_tick(monkeypatch, [False])
        assert interval <= bs.MAX_INTERVAL_MIN

    def test_no_gaps_goes_idle(self, db, monkeypatch):
        """无缺口 → 低频巡检间隔。"""
        monkeypatch.setattr(bs, '_get_stocks_with_gaps', lambda: {})
        interval = self._run_tick(monkeypatch, [])
        assert interval == bs.IDLE_INTERVAL_MIN


class TestSchedulerLifecycle:
    def test_start_idempotent(self, monkeypatch):
        """重复启动幂等（只注册一次）。"""
        calls = []

        def fake_schedule(interval_min):
            calls.append(interval_min)

        monkeypatch.setattr(bs, '_schedule_next', fake_schedule)
        bs._scheduler_started = False
        bs.start_backfill_scheduler()
        bs.start_backfill_scheduler()
        assert len(calls) == 1
        bs.stop_backfill_scheduler()
        assert bs._scheduler_started is False
