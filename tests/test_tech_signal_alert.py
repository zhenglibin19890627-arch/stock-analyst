"""
021BP 决策闭环 项1+项2：自选股买点信号离线巡检 → 智能预警

覆盖（隔离临时库 + 合成K线，不触网）：
1. 离线复算：raw_kline/raw_kline_weekly → market_screener 信号纯函数
   （深跌+缺口反弹形态实机校准：交叉确定落在最新一根K线）
2. 预警链路：scan_once 经 _RULE_CHECKERS 扩展写入 alert_history——
   UNIQUE(rule_id, stock_id, trigger_date) 幂等、"今日出现"口径、
   共振星级门槛、文案无裸 '<'（021BN 教训）
3. 白名单/种子：默认规则种子含 tech_signal；blueprint 与 engine 白名单同步
"""

import datetime as dt
import json

import pytest

from blueprints import alerts as alerts_bp
from database import db_manager
from modules import alert_engine
from modules.market_screener import (
    compute_watchlist_signal_result,
    scan_watchlist_signals,
)

# ---------------- 合成K线构造（形态已实机校准） ----------------

def _deep_v_gap_closes():
    """45 根深跌 + 1 根横盘 + 1 根缺口大阳 → MACD水下金叉 + KDJ低位金叉
    同日触发且触发日 == 最新一根（window=3 默认窗口内）。"""
    base = [110 - i * 1.5 for i in range(45)]
    return base + [base[-1], base[-1] + 8.0]


_WEEKLY_UP = [100 + i * 1.0 for i in range(40)]  # 周线稳步上行 → 周线 DIF>DEA


def _dates(n, start=(2026, 9, 1)):
    d0 = dt.date(*start)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def _mk_rows(closes):
    return [
        {'date': d, 'open': c * 0.99, 'close': c, 'high': c * 1.01,
         'low': c * 0.98, 'volume': 1000.0}
        for d, c in zip(_dates(len(closes)), closes)
    ]


def _insert_klines(conn, stock_id, closes, table='raw_kline'):
    conn.executemany(
        f'INSERT OR IGNORE INTO {table} '
        '(stock_id, trade_date, open, close, high, low, volume) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        [(stock_id, d, c * 0.99, c, c * 1.01, c * 0.98, 1000.0)
         for d, c in zip(_dates(len(closes)), closes)],
    )
    conn.commit()


def _add_stock_rule(stock_id, threshold):
    """插入个股级 tech_signal 规则（优先级高于全局规则）。"""
    conn = db_manager.get_connection()
    conn.execute(
        'INSERT INTO alert_rules (rule_type, stock_id, threshold, enabled) '
        "VALUES ('tech_signal', ?, ?, 1)",
        (stock_id, threshold),
    )
    conn.commit()
    conn.close()


def _tech_alert_rows():
    conn = db_manager.get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM alert_history WHERE alert_type='tech_signal'").fetchall()]
    conn.close()
    return rows


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """隔离临时库（含默认规则种子 + 1 只活跃自选股），返回 stock_id"""
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'test_tech_signal.db'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')"
    )
    conn.commit()
    stock_id = conn.execute('SELECT id FROM stocks LIMIT 1').fetchone()['id']
    conn.close()
    return stock_id


# ---------------- 种子与白名单同步 ----------------

class TestSeedsAndWhitelists:
    def test_default_rule_seeded(self, db):
        """默认规则种子含全局 tech_signal（enabled=1，无阈值）"""
        conn = db_manager.get_connection()
        row = conn.execute(
            'SELECT threshold, enabled FROM alert_rules '
            "WHERE rule_type='tech_signal' AND stock_id IS NULL"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row['enabled'] == 1
        assert row['threshold'] is None

    def test_seed_idempotent_on_reinit(self, db):
        """二次 init_database 不重复种子（WHERE NOT EXISTS 幂等）"""
        db_manager.init_database()
        conn = db_manager.get_connection()
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM alert_rules WHERE rule_type='tech_signal'"
        ).fetchone()['c']
        conn.close()
        assert n == 1

    def test_whitelist_sync_blueprint_engine(self):
        """blueprint 与 engine 的类型白名单必须一致（缺一即漏：API 会 400/UI 裸 key）"""
        assert set(alerts_bp._VALID_ALERT_TYPES) == set(alert_engine.VALID_RULE_TYPES)
        assert 'tech_signal' in alert_engine.VALID_RULE_TYPES
        assert 'tech_signal' in alert_engine._RULE_CHECKERS


# ---------------- 项1：离线复算 ----------------

class TestOfflineRecompute:
    def test_scan_watchlist_signals_full_chain(self, db):
        """库内日K+周K → 信号 + 周线共振；交叉落在最新一根；口径标注"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _deep_v_gap_closes())
        _insert_klines(conn, db, _WEEKLY_UP, table='raw_kline_weekly')
        conn.close()

        result = scan_watchlist_signals()
        assert result['scope'] == 'watchlist_offline'
        assert result['stock_count'] == 1
        assert result['errors'] == []
        assert len(result['results']) == 1

        item = result['results'][0]
        assert item['stock_id'] == db
        assert item['symbol'] == '600519'
        assert {h['signal'] for h in item['matches']} == {
            'macd_golden_below', 'kdj_golden_low',
        }
        # "今日出现"口径的前提：交叉触发日 == 最新已采集K线日
        assert {h['trigger_date'] for h in item['matches']} == {item['kline_upto']}
        assert [r['key'] for r in item['resonances']] == ['res_week_daily']
        assert item['resonances'][0]['stars'] == 5
        assert item['kline_count'] == 47

    def test_stock_ids_filter(self, db):
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _deep_v_gap_closes())
        conn.close()

        hit = scan_watchlist_signals(stock_ids=[db])
        assert hit['stock_count'] == 1 and len(hit['results']) == 1
        miss = scan_watchlist_signals(stock_ids=[99999])
        assert miss['stock_count'] == 0 and miss['results'] == []

    def test_suspended_stock_not_scanned(self, db):
        """巡检范围 = active 自选股（suspended 不参与）"""
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO stocks (symbol, market, name, status) "
            "VALUES ('000001', 'a_stock', '平安银行', 'suspended')"
        )
        conn.commit()
        sid2 = conn.execute(
            "SELECT id FROM stocks WHERE symbol='000001'").fetchone()['id']
        _insert_klines(conn, sid2, _deep_v_gap_closes())
        conn.close()

        result = scan_watchlist_signals()
        assert result['stock_count'] == 1  # 仅 600519
        assert result['results'] == []

    def test_compute_empty_and_insufficient(self):
        empty = compute_watchlist_signal_result([])
        assert empty['matches'] == []
        assert empty['kline_upto'] is None
        assert empty['kline_count'] == 0

        short = compute_watchlist_signal_result(_mk_rows([100 - i for i in range(20)]))
        assert short['matches'] == []          # <35 根：detect_signals 门槛
        assert short['kline_count'] == 20
        assert short['kline_upto'] is not None  # 数据新鲜度仍回报


# ---------------- 项2：预警链路 ----------------

class TestScanOnceAlertChain:
    def test_alert_written_and_idempotent(self, db):
        """命中写 alert_history（走既有写入路径）；同日二次扫描幂等跳过"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _deep_v_gap_closes())
        _insert_klines(conn, db, _WEEKLY_UP, table='raw_kline_weekly')
        conn.close()

        first = alert_engine.scan_once()
        assert first['success'] is True
        assert first['triggered'] == 1  # 其余3类规则无数据不触发
        assert first['errors'] == 0

        rows = _tech_alert_rows()
        assert len(rows) == 1
        row = rows[0]
        assert '贵州茅台(600519)' in row['message']
        assert '今日出现买点信号' in row['message']
        assert 'MACD水下金叉' in row['message']
        assert 'KDJ低位金叉' in row['message']
        assert '周线共振波段' in row['message']
        assert '<' not in row['message']  # 021BN 教训：渲染文案禁止裸 '<'
        detail = json.loads(row['trigger_value'])
        assert {s['signal'] for s in detail['signals']} == {
            'macd_golden_below', 'kdj_golden_low',
        }
        assert detail['resonances'][0]['stars'] == 5

        # 同日二次扫描：命中照旧，UNIQUE(rule_id, stock_id, trigger_date) 幂等
        second = alert_engine.scan_once()
        assert second['success'] is True
        assert second['triggered'] == 0
        assert second['skipped_idempotent'] == 1
        assert len(_tech_alert_rows()) == 1

    def test_no_alert_when_signal_not_on_latest_bar(self, db):
        """新鲜度门槛：窗口内命中但触发日非最新一根 → 前日巡检已覆盖，不提醒"""
        closes = _deep_v_gap_closes() + [_deep_v_gap_closes()[-1]]  # 追加1根横盘
        conn = db_manager.get_connection()
        _insert_klines(conn, db, closes)
        conn.close()

        # 巡检层（候选展示口径）仍回报窗口命中；预警层按"今日出现"收敛
        result = scan_watchlist_signals(stock_ids=[db])
        assert len(result['results']) == 1
        assert alert_engine.scan_once()['triggered'] == 0
        assert _tech_alert_rows() == []

    def test_no_alert_when_cross_out_of_window(self, db):
        """交叉发生在窗口外（追加3根横盘隔开）→ 巡检与预警都不报"""
        closes = _deep_v_gap_closes() + [_deep_v_gap_closes()[-1]] * 3
        conn = db_manager.get_connection()
        _insert_klines(conn, db, closes)
        conn.close()

        assert scan_watchlist_signals(stock_ids=[db])['results'] == []
        assert alert_engine.scan_once()['triggered'] == 0

    def test_min_stars_gate_suppresses_below_threshold(self, db):
        """星级门槛：无周K → 最高共振=同日双金叉4星；个股规则门槛5星 → 不提醒"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _deep_v_gap_closes())
        conn.close()
        _add_stock_rule(db, 5)

        result = alert_engine.scan_once()
        assert result['success'] is True
        assert result['triggered'] == 0
        assert _tech_alert_rows() == []

    def test_min_stars_gate_passes_on_weekly_resonance(self, db):
        """星级门槛：周线共振5星 ≥ 门槛 → 提醒且个股规则优先于全局规则"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _deep_v_gap_closes())
        _insert_klines(conn, db, _WEEKLY_UP, table='raw_kline_weekly')
        conn.close()
        _add_stock_rule(db, 5)

        result = alert_engine.scan_once()
        assert result['triggered'] == 1
        rows = _tech_alert_rows()
        assert len(rows) == 1
        assert '周线共振波段' in rows[0]['message']

    def test_insufficient_klines_skip_silently(self, db):
        """K线不足35根：静默跳过，不报错不提醒"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, [100 - i for i in range(20)])
        conn.close()

        result = alert_engine.scan_once()
        assert result['success'] is True
        assert result['triggered'] == 0
        assert result['errors'] == 0

    def test_no_klines_no_alert_no_error(self, db):
        """无任何K线：静默跳过"""
        result = alert_engine.scan_once()
        assert result['success'] is True
        assert result['triggered'] == 0
        assert result['errors'] == 0
        assert _tech_alert_rows() == []
