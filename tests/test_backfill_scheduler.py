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
from modules import data_collector as dc


def _trading_days(n=5):
    """020H 语义：交易日历由 K 线数据驱动（并集口径），
    测试用最近 n 个自然日构造日历即可（非交易日判定不由自然日决定）。"""
    return [(datetime.now() - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(n)]


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """临时库：完整股票 + 完整数据 1 只，缺资金面 1 只，全新无数据 1 只。

    021A 更新：对齐 020H 缺口检测新语义——
    - 日历 = 全市场 K 线日期并集（近 10 个交易日窗口），股票1/2 覆盖全部 5 个日历日
    - 新增 'ths' 维度：A股最新交易日需有同花顺辅助净额（ths_net_inflow）
    - capital 判定 = 日历窗口内每个交易日均有主力净流入行（非仅"当日"）
    """
    db_file = tmp_path / 'test_backfill.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()

    conn = get_connection()
    days = _trading_days()

    # 股票 1：完整（全日历日资金面 + 同花顺辅助净额 + K线/基本面/消息面齐全）
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600001', 'a_stock', '完整股')")
    # 股票 2：缺资金面（K线/基本面/消息面齐全）
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600002', 'a_stock', '缺资金面')")
    # 股票 3：全新无数据
    conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600003', 'a_stock', '全新股')")

    for i, sid in enumerate([1, 2, 3], start=1):
        # K线：股票1/2 覆盖全部日历日（构成全市场交易日历），股票3 无
        if i < 3:
            for d in days:
                conn.execute(
                    'INSERT INTO raw_kline (stock_id, trade_date, close) VALUES (?, ?, 10.0)',
                    (sid, d),
                )
        # 基本面：股票1/2 有，股票3 无（缺口判定只看"完全无数据"）
        if i < 3:
            conn.execute(
                "INSERT INTO raw_fundamental (stock_id, report_date, roe) VALUES (?, ?, 10.0)",
                (sid, days[-1]),
            )
        # 消息面：股票1/2 有 3 天内，股票3 无
        if i < 3:
            conn.execute(
                'INSERT INTO news_sentiment (stock_id, news_date, avg_sentiment) VALUES (?, ?, 0.1)',
                (sid, days[3]),
            )
        # 资金面：仅股票1 覆盖全部日历日（真实主力净流入）+ 最新日历日同花顺辅助净额
        if i == 1:
            for d in days:
                conn.execute(
                    'INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow) VALUES (?, ?, 100.0)',
                    (sid, d),
                )
            conn.execute(
                'UPDATE raw_capital_flow SET ths_net_inflow = 50.0 WHERE stock_id=? AND trade_date=?',
                (sid, days[0]),
            )

    conn.commit()
    conn.close()
    return db_file


class TestGapDetection:
    def test_detects_gaps(self, db):
        gaps = bs._get_stocks_with_gaps()
        # 股票1 完整 → 不在缺口列表
        assert 1 not in gaps, '完整股票不应有缺口'
        # 股票2 缺资金面（主力净流入 + 同花顺辅助净额）
        assert 2 in gaps
        assert set(gaps[2]['dims']) == {'capital', 'ths'}
        # 股票3 全维度缺（020H 起含 ths）
        assert 3 in gaps
        assert set(gaps[3]['dims']) == {'kline', 'capital', 'ths', 'fundamental', 'news'}

    def test_all_complete_no_gaps(self, db, monkeypatch):
        # 给股票2/3 补齐资金面（日历窗口全部交易日 + 同花顺辅助净额）后：
        # 股票2 完整，股票3 仍缺其它维度
        conn = get_connection()
        days = _trading_days()
        for sid in (2, 3):
            for d in days:
                conn.execute(
                    'INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow) VALUES (?, ?, 100.0)',
                    (sid, d),
                )
            conn.execute(
                'UPDATE raw_capital_flow SET ths_net_inflow = 50.0 WHERE stock_id=? AND trade_date=?',
                (sid, days[0]),
            )
        conn.commit()
        conn.close()
        gaps = bs._get_stocks_with_gaps()
        assert 2 not in gaps
        assert 3 in gaps
        assert 'capital' not in gaps[3]['dims']
        assert 'ths' not in gaps[3]['dims']


class TestTickBackoff:
    def _run_tick(self, monkeypatch, collect_results):
        """执行一次 _tick（patch 采集与调度），返回注册的下一间隔。"""
        captured = {}

        def fake_collect(stock_id, symbol, market, missing_cap_dates=None):
            # 按调用顺序循环返回结果（020H 起 _collect_one 增加 missing_cap_dates 关键字参数）
            return collect_results.pop(0) if collect_results else True

        def fake_schedule(interval_min):
            captured['interval'] = interval_min

        monkeypatch.setattr(bs, '_collect_one', fake_collect)
        monkeypatch.setattr(bs, '_schedule_next', fake_schedule)
        # 021A：_tick 先做同花顺批量刷新（真实网络调用），测试内隔离网络
        monkeypatch.setattr(dc, 'fetch_capital_flow_batch', lambda symbols: {'source': 'test'})
        bs._tick()
        return captured.get('interval')

    def test_all_fail_backoff_doubles(self, db, monkeypatch):
        """本轮全失败 → 间隔翻倍（30 → 60）。"""
        monkeypatch.setattr(bs, '_backoff_min', bs.BASE_INTERVAL_MIN)
        interval = self._run_tick(monkeypatch, [False, False, False, False, False])
        assert interval == bs.BASE_INTERVAL_MIN * 2

    def test_partial_success_resets(self, db, monkeypatch):
        """部分成功 → 重置为基础间隔。"""
        monkeypatch.setattr(bs, '_backoff_min', bs.MAX_INTERVAL_MIN)  # 先处于退避态
        interval = self._run_tick(monkeypatch, [True, True, False])
        assert interval == bs.BASE_INTERVAL_MIN

    def test_backoff_capped(self, db, monkeypatch):
        """连续失败退避不超过上限（30 → 60 → 120 封顶）。"""
        monkeypatch.setattr(bs, '_backoff_min', bs.BASE_INTERVAL_MIN)
        self._run_tick(monkeypatch, [False, False, False])
        interval = self._run_tick(monkeypatch, [False, False, False])
        assert interval == bs.MAX_INTERVAL_MIN

    def test_no_gaps_goes_idle(self, db, monkeypatch):
        """无缺口 → 低频巡检间隔。"""
        monkeypatch.setattr(bs, '_get_stocks_with_gaps', dict)
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
