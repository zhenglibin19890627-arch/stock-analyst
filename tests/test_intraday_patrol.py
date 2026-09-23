"""021BT 盘中巡检测试：触线/逼近/异动判定 + 时段门控 + 节假日守卫 + 失败静默降级
+ 写库面（仅 price_cache）+ 行动清单路0b 桥接 + _scan_stop_discipline 参数化
+ realtime_quotes 快照解析（含港股时间戳归一）。

全部合成/mock（镜像 tests/test_intraday_020r59 的 _FakeDT/临时库模式）：
- 时钟：_FakeDT 逐模块固定 datetime.now（巡检模块命名空间独立，补丁只打
  modules.intraday_patrol 的 datetime 与 _is_intraday_session 绑定）；
- 网络：monkeypatch modules.intraday_patrol._fetch_quote_snapshot_batch /
  requests.get，零真实网络；
- 库：tmp_path + monkeypatch db_manager.DB_PATH 临时库。
"""

import datetime as _dt
import json

import pytest

import config
import modules.intraday_patrol as patrol
import modules.realtime_quotes as rq
from database import db_manager
from modules.action_list import _scan_stop_discipline, build_action_list

TRADING_DT = _dt.datetime(2026, 9, 23, 10, 30)  # 周三盘中
TODAY = '2026-09-23'
TODAY_COMPACT = '20260923'


class _FakeDT:
    """伪造 datetime：now() 返回固定时刻"""

    def __init__(self, real):
        self._real = real

    def now(self, tz=None):
        return self._real


@pytest.fixture(autouse=True)
def _reset_patrol_state(monkeypatch):
    """每例重置巡检内存态（monkeypatch 保存原值，测试后自动还原）。"""
    monkeypatch.setattr(patrol, '_LAST_SNAPSHOT', None)
    monkeypatch.setattr(patrol, '_consecutive_failures', 0)
    monkeypatch.setattr(patrol, '_paused', False)
    monkeypatch.setattr(patrol, '_stale_rounds', 0)
    monkeypatch.setattr(patrol, '_last_manual_refresh_ts', 0.0)
    monkeypatch.setattr(patrol, '_timer', None)
    yield


@pytest.fixture
def pdb(tmp_path, monkeypatch):
    """隔离库：两只持仓（成本 10.0 / 5.0）+ 5 日量能参照（各 1000 手）"""
    db_file = tmp_path / 'patrol.db'
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
    for i in range(5):
        d = (_dt.date(2026, 9, 16) + _dt.timedelta(days=i)).isoformat()
        conn.execute(
            'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
            'VALUES (1, ?, 9.9, 10.0, 10.1, 9.8, 1000)', (d,))
    conn.commit()
    conn.close()
    return db_manager


def _patch_clock(monkeypatch, dt=TRADING_DT, in_session=True):
    monkeypatch.setattr(patrol, 'datetime', _FakeDT(dt))
    monkeypatch.setattr(patrol, '_is_intraday_session', lambda market='a_stock': in_session)


def _patch_fetch(monkeypatch, quotes=None, error=None):
    """替换巡检批量取价；返回调用记录 {'n': 次数, 'symbols': 末次入参}"""
    calls = {'n': 0, 'symbols': None}

    def _fake(symbols_markets):
        calls['n'] += 1
        calls['symbols'] = list(symbols_markets)
        if error is not None:
            raise error
        return dict(quotes or {})

    monkeypatch.setattr(patrol, '_fetch_quote_snapshot_batch', _fake)
    return calls


def _seed_pa_stop(stock_id, stop_loss):
    conn = db_manager.get_connection()
    conn.execute(
        """INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name,
           total_score, rating, rating_label, score_change, status, report_type, price_advice)
           VALUES ('2026-09-22', ?, '600276', '恒瑞医药', 60.0, '持有观望', '持有观望',
                   0.0, 'ok', 'daily', ?)""",
        (stock_id, json.dumps({'stop_loss': stop_loss})),
    )
    conn.commit()
    conn.close()


def _quote(sid, price, pct, volume=None, ts=TODAY_COMPACT + '103000'):
    return {'price': price, 'pct_change': pct, 'prev_close': None,
            'volume': volume, 'quote_ts': ts}


# ============================================================
# 一、状态判定纯函数（触线/逼近/正常/无止损/异动/边界）
# ============================================================


class TestEvaluateStockState:
    def test_below_stop(self):
        state, _ = patrol.evaluate_stock_state(9.0, 9.2, 0.5, 1.0, 3.0)
        assert state == 'below_stop'

    def test_boundary_price_equals_stop_is_below(self):
        """任务口径：实时价 ≤ 有效止损即触线（触及即警），线价边界触发"""
        state, _ = patrol.evaluate_stock_state(9.2, 9.2, 0.0, 1.0, 3.0)
        assert state == 'below_stop'

    def test_near_stop_band(self):
        """逼近带内（距止损 0.87% <1%）→ near_stop；带外（1.09%）→ normal"""
        s1, _ = patrol.evaluate_stock_state(9.28, 9.2, 0.1, 1.0, 3.0)
        s2, _ = patrol.evaluate_stock_state(9.30, 9.2, 0.1, 1.0, 3.0)
        assert s1 == 'near_stop'
        assert s2 == 'normal'

    def test_no_stop_unknown(self):
        state, _ = patrol.evaluate_stock_state(9.0, None, 0.5, 1.0, 3.0)
        assert state == 'unknown'

    def test_swing_threshold(self):
        """|涨跌幅| ≥3% 判异动；+2.9% 与 -2.99% 不判"""
        assert patrol.evaluate_stock_state(11.0, 9.2, 3.0, 1.0, 3.0)[1] is True
        assert patrol.evaluate_stock_state(11.0, 9.2, -3.5, 1.0, 3.0)[1] is True
        assert patrol.evaluate_stock_state(11.0, 9.2, 2.9, 1.0, 3.0)[1] is False


class TestEffectiveStop:
    def test_dual_source_takes_higher(self):
        """双源取高者：max(成本×0.92, pa_stop)，来源标注与 _stop_level 同词表"""
        level, src = patrol._effective_stop(10.0, 9.5)
        assert (level, src) == (9.5, '纪律/价格建议取高者')
        level, src = patrol._effective_stop(10.0, 9.0)
        assert (level, src) == (9.2, '纪律/价格建议取高者')

    def test_single_and_none(self):
        assert patrol._effective_stop(10.0, None) == (9.2, '纪律')
        assert patrol._effective_stop(None, 9.5) == (9.5, '价格建议')
        assert patrol._effective_stop(None, None) == (None, None)


# ============================================================
# 二、时段门控与非交易时段零动作
# ============================================================


class TestSessionGating:
    def test_in_session_runs(self, pdb, monkeypatch):
        _patch_clock(monkeypatch, in_session=True)
        calls = _patch_fetch(monkeypatch, quotes={1: _quote(1, 10.5, 1.0)})
        result = patrol.run_patrol_round()
        assert result['ok'] is True
        assert calls['n'] == 1
        assert result['snapshot']['stocks'][0]['state'] == 'normal'

    def test_out_of_session_zero_request(self, pdb, monkeypatch):
        """午休/盘后/周末：零请求（替身触发即失败证明）"""
        _patch_clock(monkeypatch, in_session=False)

        def _boom(*a, **k):
            raise AssertionError('非交易时段不应发起行情请求')

        monkeypatch.setattr(patrol, '_fetch_quote_snapshot_batch', _boom)
        result = patrol.run_patrol_round()
        assert result['ok'] is False
        assert result['skipped'] is True
        assert result['reason'] == '非交易时段'

    def test_disabled_never_runs(self, pdb, monkeypatch):
        _patch_clock(monkeypatch, in_session=True)
        monkeypatch.setattr(config, 'INTRADAY_PATROL_ENABLED', False)
        calls = _patch_fetch(monkeypatch, quotes={})
        result = patrol.run_patrol_round()
        assert result['skipped'] is True
        assert calls['n'] == 0

    def test_no_holdings_zero_request(self, tmp_path, monkeypatch):
        db_file = tmp_path / 'empty.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        db_manager.init_database()
        _patch_clock(monkeypatch, in_session=True)

        def _boom(*a, **k):
            raise AssertionError('无持仓不应发起行情请求')

        monkeypatch.setattr(patrol, '_fetch_quote_snapshot_batch', _boom)
        result = patrol.run_patrol_round()
        assert result['ok'] is True
        assert result['snapshot']['stocks'] == []


class TestPatrolTick:
    def test_tick_in_session_runs_round(self, pdb, monkeypatch):
        _patch_clock(monkeypatch, in_session=True)
        monkeypatch.setattr(patrol, '_schedule_next', lambda interval: None)
        calls = _patch_fetch(monkeypatch, quotes={1: _quote(1, 9.0, -1.0)})
        patrol._patrol_tick()
        assert calls['n'] == 1
        assert patrol._LAST_SNAPSHOT is not None

    def test_tick_out_of_session_silent(self, pdb, monkeypatch):
        """非时段 tick：零请求零写库（空转代价=一次布尔判断）"""
        _patch_clock(monkeypatch, in_session=False)
        monkeypatch.setattr(patrol, '_schedule_next', lambda interval: None)

        def _boom(*a, **k):
            raise AssertionError('非交易时段 tick 不应巡检')

        monkeypatch.setattr(patrol, '_fetch_quote_snapshot_batch', _boom)
        patrol._patrol_tick()
        assert patrol._LAST_SNAPSHOT is None


# ============================================================
# 三、节假日/停牌守卫（快照时间戳非今日）
# ============================================================


class TestQuoteGuard:
    def test_stale_ts_skipped_no_write(self, pdb, monkeypatch):
        """快照时间戳=昨日（节假日/停牌）→ 跳过不写库不报警，显示价回落缓存"""
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO price_cache (stock_id, latest_price, pct_change, updated_at) "
            "VALUES (1, 7.77, -0.3, '2026-09-22 15:00:00')")
        conn.commit()
        conn.close()
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 9.0, -1.0, ts='20260922103000')})
        result = patrol.run_patrol_round()
        assert result['stale_skipped'] == 1
        assert result['written'] == 0
        row = result['snapshot']['stocks'][0]
        assert row['quote_ok'] is False
        assert row['price'] == pytest.approx(7.77)  # 今日快照被守卫跳过，显示回落缓存
        conn = db_manager.get_connection()
        n = conn.execute('SELECT COUNT(*) FROM price_cache').fetchone()[0]
        conn.close()
        assert n == 1  # 守卫轮未新增写库

    def test_two_all_stale_rounds_pause(self, pdb, monkeypatch):
        """连续 2 轮全部非今日（疑似节假日）→ 暂停至时段边界"""
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 9.0, -1.0, ts='20260922103000')})
        patrol.run_patrol_round()
        assert patrol._paused is False
        patrol.run_patrol_round()
        assert patrol._paused is True

    def test_today_ts_written(self, pdb, monkeypatch):
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 10.88, 2.1)})
        result = patrol.run_patrol_round()
        assert result['written'] == 1
        assert result['snapshot']['stocks'][0]['as_of'] == '10:30'
        conn = db_manager.get_connection()
        row = conn.execute(
            'SELECT latest_price FROM price_cache WHERE stock_id=1').fetchone()
        conn.close()
        assert row['latest_price'] == pytest.approx(10.88)


# ============================================================
# 四、快照状态与 price_cache 写库面
# ============================================================


class TestStatesAndSnapshot:
    def test_below_and_swing_states(self, pdb, monkeypatch):
        """stock1 触线（9.0 < 9.2 成本线）；stock2 异动（+3.5%，价格在线上为 normal）"""
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={
            1: _quote(1, 9.0, -2.0),
            2: _quote(2, 5.5, 3.5),
        })
        result = patrol.run_patrol_round()
        rows = {r['stock_id']: r for r in result['snapshot']['stocks']}
        assert rows[1]['state'] == 'below_stop'
        assert rows[1]['stop_line'] == pytest.approx(9.2)
        assert rows[1]['distance_pct'] == pytest.approx(-2.17, abs=0.01)
        assert rows[2]['state'] == 'normal'  # 5.5 距成本线 4.6 约 19.6%
        assert rows[2]['swing'] is True
        assert result['snapshot']['counts']['alerts'] == 2

    def test_pa_stop_dual_source_in_snapshot(self, pdb, monkeypatch):
        """日报价格建议止损 9.6 高于成本线 9.2 → 有效止损 9.6"""
        _seed_pa_stop(1, 9.6)
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 9.65, -1.0)})
        result = patrol.run_patrol_round()
        row = result['snapshot']['stocks'][0]
        assert row['stop_line'] == pytest.approx(9.6)
        assert row['stop_source'] == '纪律/价格建议取高者'
        assert row['state'] == 'near_stop'  # 距 9.6 约 0.52%

    def test_ma20_computed_and_distance(self, pdb, monkeypatch):
        """t3：距 MA20%——补 20 根 close=10.0 的已完成日K → MA20=10.0，
        现价 10.5 → 距离 +5.0%（收盘口径，排除当日行）"""
        conn = db_manager.get_connection()
        for i in range(20):
            d = (_dt.date(2026, 8, 20) + _dt.timedelta(days=i)).isoformat()
            conn.execute(
                'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
                'VALUES (1, ?, 10.0, 10.0, 10.2, 9.8, 1000)', (d,))
        conn.commit()
        conn.close()
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 10.5, 1.0)})
        result = patrol.run_patrol_round()
        row = result['snapshot']['stocks'][0]
        assert row['ma20'] == pytest.approx(10.0)
        assert row['ma20_distance_pct'] == pytest.approx(5.0)

    def test_ma20_insufficient_bars_none(self, pdb, monkeypatch):
        """t3：不足 20 根已完成日K → ma20 显式缺失（None，前端显示"—"，不凑数）"""
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 10.5, 1.0)})
        result = patrol.run_patrol_round()
        row = result['snapshot']['stocks'][0]
        assert row['ma20'] is None
        assert row['ma20_distance_pct'] is None

    def test_volume_spike(self, pdb, monkeypatch):
        """当日量 3000 ≥ 近 5 日均量 1000 ×1.5 → 量能异动"""
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 10.5, 1.0, volume=3000)})
        result = patrol.run_patrol_round()
        row = result['snapshot']['stocks'][0]
        assert row['vol_spike'] is True
        assert row['swing'] is True
        assert row['state'] == 'normal'

    def test_cache_fallback_display(self, pdb, monkeypatch):
        """无今日快照（休市后读取）→ price_cache 兜底显示，quote_ok=False"""
        _patch_clock(monkeypatch, in_session=False)
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO price_cache (stock_id, latest_price, pct_change, updated_at) "
            "VALUES (1, 10.2, 0.5, '2026-09-23 10:30:00')")
        conn.commit()
        conn.close()
        snap = patrol.build_snapshot(TRADING_DT, patrol._held_positions(), None)
        row = snap['stocks'][0]
        assert row['price'] == pytest.approx(10.2)
        assert row['as_of'] == '10:30'
        assert row['quote_ok'] is False
        assert snap['session']['in_session'] is False
        assert snap['disclaimer'] == '盘中口径，以收盘确认为准'


class TestWriteFacade:
    def test_write_only_for_fetched_keeps_old(self, pdb, monkeypatch):
        """仅今日快照股写 price_cache；未获取股旧缓存原样保留（禁归零）"""
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO price_cache (stock_id, latest_price, pct_change, updated_at) "
            "VALUES (2, 4.44, -0.5, '2026-09-22 15:00:00')")
        conn.commit()
        conn.close()
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 10.5, 1.0)})
        patrol.run_patrol_round()
        conn = db_manager.get_connection()
        rows = {r['stock_id']: r['latest_price']
                for r in conn.execute('SELECT stock_id, latest_price FROM price_cache')}
        conn.close()
        assert rows[1] == pytest.approx(10.5)
        assert rows[2] == pytest.approx(4.44)  # 旧值未动

    def test_failure_zero_price_cache_writes(self, pdb, monkeypatch):
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, error=RuntimeError('网络不可达'))
        patrol.run_patrol_round()
        conn = db_manager.get_connection()
        n = conn.execute('SELECT COUNT(*) FROM price_cache').fetchone()[0]
        conn.close()
        assert n == 0


# ============================================================
# 五、失败静默降级（保旧快照 + 连败暂停 + 恢复）
# ============================================================


class TestDegradation:
    def test_fetch_failure_degrades_silently(self, pdb, monkeypatch):
        """取价异常 → 不抛错、快照降级标注、连败计数 1、旧缓存不写"""
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO price_cache (stock_id, latest_price, pct_change, updated_at) "
            "VALUES (1, 10.2, 0.5, '2026-09-23 09:35:00')")
        conn.commit()
        conn.close()
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, error=RuntimeError('超时'))
        result = patrol.run_patrol_round()  # 不应抛出
        assert result['ok'] is False
        assert result['written'] == 0
        assert patrol._consecutive_failures == 1
        snap = result['snapshot']
        assert snap['degraded'] is True
        assert '数据源暂不可达' in snap['degrade_note']
        assert snap['stocks'][0]['price'] == pytest.approx(10.2)  # 旧值兜底

    def test_three_failures_pause_force_bypass(self, pdb, monkeypatch):
        """连败 3 轮 → 暂停（后续轮零请求）；force 手动绕过暂停"""
        _patch_clock(monkeypatch)
        calls = _patch_fetch(monkeypatch, error=RuntimeError('不可达'))
        for _ in range(3):
            patrol.run_patrol_round()
        assert patrol._paused is True
        before = calls['n']
        result = patrol.run_patrol_round()
        assert result['skipped'] is True
        assert calls['n'] == before  # 暂停中零请求

        # force 手动：仍失败则维持暂停计数；改成功则解除
        monkeypatch.setattr(
            patrol, '_fetch_quote_snapshot_batch',
            lambda symbols: {1: _quote(1, 10.5, 1.0)})
        result = patrol.run_patrol_round(force=True)
        assert result['ok'] is True
        assert patrol._paused is False
        assert patrol._consecutive_failures == 0

    def test_out_of_session_tick_resets_pause(self, pdb, monkeypatch):
        """时段边界：非时段 tick 将连败/暂停态清零（下时段自动恢复）"""
        _patch_clock(monkeypatch, in_session=False)
        monkeypatch.setattr(patrol, '_schedule_next', lambda interval: None)

        def _boom(*a, **k):
            raise AssertionError('时段边界重置不应发请求')

        monkeypatch.setattr(patrol, '_fetch_quote_snapshot_batch', _boom)
        patrol._paused = True
        patrol._consecutive_failures = 3
        patrol._stale_rounds = 2
        patrol._patrol_tick()
        assert patrol._paused is False
        assert patrol._consecutive_failures == 0
        assert patrol._stale_rounds == 0


# ============================================================
# 六、手动一键刷新（冷却）
# ============================================================


class TestManualRefresh:
    def test_cooldown_second_call_blocked(self, pdb, monkeypatch):
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 10.5, 1.0)})
        r1 = patrol.request_manual_refresh()
        assert r1['ok'] is True
        r2 = patrol.request_manual_refresh()
        assert r2['ok'] is False
        assert r2['cooldown'] > 0

    def test_refresh_out_of_session_clear_reason(self, pdb, monkeypatch):
        _patch_clock(monkeypatch, in_session=False)
        _patch_fetch(monkeypatch, quotes={})
        r = patrol.request_manual_refresh()
        assert r['ok'] is False
        assert r['reason'] == '非交易时段'


# ============================================================
# 七、行动清单路0b 桥接（kind=intraday_alert，P0 置顶）
# ============================================================


def _beijing_day_offset(days=0):
    """真实北京时间今日±N 日（021BV 日期防腐：_seed_snapshot 默认日期随真实时钟
    滚动——021BT 曾写死 '2026-09-23'，跨日后 get_intraday_alerts 今日门控全哑）"""
    tz = _dt.timezone(_dt.timedelta(hours=8))
    return (_dt.datetime.now(tz) + _dt.timedelta(days=days)).strftime('%Y-%m-%d')


def _seed_snapshot(states_by_sid, date=None, updated_at='10:30:00'):
    """手工置巡检内存快照（模拟已跑过巡检轮）；date 缺省=真实今日（不写死）"""
    rows = []
    for sid, (symbol, name, price, pct, stop, state, swing) in states_by_sid.items():
        rows.append({
            'stock_id': sid, 'symbol': symbol, 'name': name, 'market': 'a_stock',
            'price': price, 'pct_change': pct, 'as_of': '10:30', 'quote_ok': True,
            'note': None, 'stop_line': stop, 'stop_source': '纪律',
            'discipline_stop': stop, 'pa_stop': None,
            'distance_pct': 2.0 if stop else None, 'state': state,
            'swing': swing, 'vol_spike': False, 'volume': None, 'volume_ref': None,
        })
    patrol._LAST_SNAPSHOT = {
        'date': date or _beijing_day_offset(0), 'updated_at': updated_at, 'source': 'auto',
        'session': {'in_session': True, 'markets': ['a_stock']},
        'stocks': rows,
        'counts': {'below_stop': 0, 'near_stop': 0, 'normal': 0, 'unknown': 0,
                   'no_data': 0, 'alerts': len(rows)},
        'degraded': False, 'degrade_note': None,
        'disclaimer': '盘中口径，以收盘确认为准',
    }


class TestActionListBridge:
    def _stocks(self):
        return [
            {'stock_id': 1, 'symbol': '600276', 'name': '恒瑞医药'},
            {'stock_id': 2, 'symbol': '003816', 'name': '广弘控股'},
        ]

    def test_intraday_alert_p0_tops_discipline(self):
        """触线快照 → kind=intraday_alert P0 行动项，置顶于收盘口径纪律行；
        标注『盘中口径，以收盘确认为准』；无裸 '<'"""
        _seed_snapshot({
            1: ('600276', '恒瑞医药', 9.0, -2.0, 9.2, 'below_stop', False),
        })
        disc = [{'stock_id': 2, 'total_qty': 2000, 'avg_cost': 5.0, 'close': 4.5,
                 'close_date': '2026-09-22', 'discipline_stop': 4.6,
                 'pa_stop': None, 'effective_stop': 4.6}]
        result = build_action_list(TODAY, self._stocks(), [], [], None,
                                   held_map={1: {'total_qty': 1000, 'avg_cost': 10.0},
                                             2: {'total_qty': 2000, 'avg_cost': 5.0}},
                                   discipline_rows=disc)
        kinds = [it['kind'] for it in result['items']]
        assert kinds[0] == 'intraday_alert'
        assert kinds[1] == 'stop_discipline'
        top = result['items'][0]
        assert top['priority'] == 0
        assert top['priority_label'] == '盘中触线'
        assert '盘中触及止损线' in top['reason']
        assert '现价 9.00' in top['reason']
        assert '盘中口径，以收盘确认为准' in top['reason']
        assert top['detail']['intraday'] is True
        assert top['detail']['disclaimer'] == '盘中口径，以收盘确认为准'
        assert result['stats']['intraday_alerts'] == 1
        rendered = json.dumps(result['items'], ensure_ascii=False)
        assert '<' not in rendered

    def test_swing_only_item(self):
        """异动（±3%+）独立成项，state 保持 normal 档 severity 排序"""
        _seed_snapshot({
            2: ('003816', '广弘控股', 5.5, 3.5, None, 'unknown', True),
        })
        result = build_action_list(TODAY, self._stocks(), [], [], None)
        items = [it for it in result['items'] if it['kind'] == 'intraday_alert']
        assert len(items) == 1
        assert '盘中快速异动' in items[0]['reason']
        assert '+3.50%' in items[0]['reason']
        assert items[0]['detail']['state'] == 'unknown'

    def test_no_snapshot_no_items(self):
        """巡检未运行（无快照）→ 零盘中项，不虚构"""
        result = build_action_list(TODAY, self._stocks(), [], [], None)
        assert not [it for it in result['items'] if it['kind'] == 'intraday_alert']
        assert result['stats']['intraday_alerts'] == 0

    def test_stale_snapshot_no_items(self):
        """跨日旧快照（昨日）→ 不产生今日盘中项（日期取真实昨日，保证恒非今日）"""
        _seed_snapshot({
            1: ('600276', '恒瑞医药', 9.0, -2.0, 9.2, 'below_stop', False),
        }, date=_beijing_day_offset(-2))
        alerts = patrol.get_intraday_alerts()
        assert alerts == []


def test_get_intraday_alerts_sorted_by_severity():
    """严重度排序：触线 > 逼近 > 异动"""
    _seed_snapshot({
        2: ('003816', '广弘控股', 5.5, 3.5, None, 'unknown', True),
        1: ('600276', '恒瑞医药', 9.0, -2.0, 9.2, 'below_stop', False),
    })
    alerts = patrol.get_intraday_alerts()
    assert [a['state'] for a in alerts] == ['below_stop', 'unknown']


# ============================================================
# 八、_scan_stop_discipline 参数化（收盘判定零变化 + price_map 只增不改）
# ============================================================


class TestScanStopDisciplineParam:
    def _cursor(self):
        conn = db_manager.get_connection()
        return conn, conn.cursor()

    def _seed_kline(self, stock_id, close=7.5):
        conn = db_manager.get_connection()
        conn.execute(
            'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
            "VALUES (?, '2026-09-22', 9.0, ?, 9.8, 8.6, 1000)", (stock_id, close))
        conn.commit()
        conn.close()

    def test_default_close_behavior_unchanged(self, pdb):
        """不传 price_map：现价=raw_kline 收盘，行内不带增量键（021BR 行为逐字保持）"""
        self._seed_kline(1, 7.5)
        conn, cursor = self._cursor()
        try:
            rows = _scan_stop_discipline(cursor, {1: {'total_qty': 1000, 'avg_cost': 10.0}})
        finally:
            conn.close()
        assert len(rows) == 1
        assert rows[0]['close'] == pytest.approx(7.5)
        assert rows[0]['close_date'] == '2026-09-22'
        assert 'price_source' not in rows[0]
        assert 'as_of' not in rows[0]

    def test_price_map_intraday_overrides(self, pdb):
        """传 price_map：判定用盘中价，行内带 price_source='intraday' 与 as_of"""
        self._seed_kline(1, 7.5)  # 收盘口径会触发；盘中价 9.5 不应触发
        conn, cursor = self._cursor()
        try:
            rows = _scan_stop_discipline(
                cursor, {1: {'total_qty': 1000, 'avg_cost': 10.0}},
                price_map={1: {'price': 9.5, 'as_of': '10:30'}})
            assert rows == []
            rows2 = _scan_stop_discipline(
                cursor, {1: {'total_qty': 1000, 'avg_cost': 10.0}},
                price_map={1: {'price': 8.9, 'as_of': '14:05'}})
        finally:
            conn.close()
        assert len(rows2) == 1
        assert rows2[0]['close'] == pytest.approx(8.9)
        assert rows2[0]['price_source'] == 'intraday'
        assert rows2[0]['as_of'] == '14:05'

    def test_price_map_missing_stock_skipped(self, pdb):
        """price_map 缺该股 → 跳过（不回退收盘口径，避免口径混用）"""
        self._seed_kline(1, 7.5)
        conn, cursor = self._cursor()
        try:
            rows = _scan_stop_discipline(
                cursor, {1: {'total_qty': 1000, 'avg_cost': 10.0}}, price_map={})
        finally:
            conn.close()
        assert rows == []

    def test_build_reason_intraday_branch(self):
        """build_action_list 纪律行按 price_source 分支：盘中口径文案（无裸 '<'）"""
        stocks = [{'stock_id': 1, 'symbol': '600276', 'name': '恒瑞医药'}]
        disc = [{'stock_id': 1, 'total_qty': 1000, 'avg_cost': 10.0, 'close': 8.9,
                 'close_date': '14:05', 'discipline_stop': 9.2, 'pa_stop': 9.5,
                 'effective_stop': 9.5, 'price_source': 'intraday', 'as_of': '14:05'}]
        result = build_action_list(TODAY, stocks, [], [], None, discipline_rows=disc)
        reason = result['items'][0]['reason']
        assert '止损纪律盘中触发' in reason
        assert '现价 8.90（14:05 盘中口径，以收盘确认为准）' in reason
        assert '<' not in reason

        disc2 = [dict(disc[0])]
        disc2[0].pop('price_source')
        result2 = build_action_list(TODAY, stocks, [], [], None, discipline_rows=disc2)
        reason2 = result2['items'][0]['reason']
        assert '止损纪律已触发' in reason2
        assert '日K收盘' in reason2


# ============================================================
# 九、realtime_quotes 快照解析（合成响应，零网络）
# ============================================================


class _FakeResp:
    def __init__(self, text):
        self.text = text


def _tencent_line(code, price, prev, pct, ts, volume='800'):
    parts = [''] * 40
    parts[0] = f'v_{code}="1'
    parts[1] = 'name'
    parts[3] = str(price)
    parts[4] = str(prev)
    parts[6] = str(volume)
    parts[30] = ts
    parts[32] = str(pct)
    return f'v_{code}="' + '~'.join(parts) + '";'


class TestRealtimeQuotesParsing:
    def test_snapshot_batch_parses_a_and_hk(self, monkeypatch):
        """A/H 混合一行请求；港股时间戳 'YYYY/MM/DD HH:MM:SS' 归一为 YYYYMMDDHHMMSS"""
        import requests

        text = (
            _tencent_line('sh600276', 46.88, 46.50, 0.82, '20260923103000', '1200')
            + _tencent_line('hk03690', 84.95, 87.65, -3.08, '2026/09/23 16:08:14')
        )
        monkeypatch.setattr(requests, 'get', lambda url, timeout=8, **kw: _FakeResp(text))
        result = rq._fetch_quote_snapshot_batch([(1, '600276', 'a_stock'),
                                                 (2, 'HK3690', 'hk_stock')])
        assert result[1]['price'] == pytest.approx(46.88)
        assert result[1]['pct_change'] == pytest.approx(0.82)
        assert result[1]['volume'] == pytest.approx(1200)
        assert result[1]['quote_ts'] == '20260923103000'
        assert result[2]['price'] == pytest.approx(84.95)
        assert result[2]['quote_ts'] == '20260923160814'  # 归一
        assert result[2]['prev_close'] == pytest.approx(87.65)

    def test_invalid_rows_skipped(self, monkeypatch):
        import requests

        text = 'v_sh600276="1~x~600276~10.0";' + _tencent_line(
            'sz003816', 5.0, 5.1, -1.9, '20260923103000')
        monkeypatch.setattr(requests, 'get', lambda url, timeout=8, **kw: _FakeResp(text))
        result = rq._fetch_quote_snapshot_batch([(1, '600276', 'a_stock'),
                                                 (2, '003816', 'a_stock')])
        assert set(result) == {2}

    def test_all_batches_fail_raises(self, monkeypatch):
        import requests

        def _fail(url, timeout=8, **kw):
            raise OSError('network down')

        monkeypatch.setattr(requests, 'get', _fail)
        with pytest.raises(RuntimeError):
            rq._fetch_quote_snapshot_batch([(1, '600276', 'a_stock')])

    def test_price_batch_contract_unchanged(self, monkeypatch):
        """下沉回归：_fetch_realtime_price_batch 契约不变（仅 price/pct_change 两键，
        港股前缀归一，test_routes 021M 用例同款断言）"""
        import requests

        text = _tencent_line('sh600276', 46.88, 46.50, 0.82, '20260923103000')
        monkeypatch.setattr(requests, 'get', lambda url, timeout=8, **kw: _FakeResp(text))
        result = rq._fetch_realtime_price_batch([(1, '600276', 'a_stock')])
        assert result[1] == {'price': 46.88, 'pct_change': 0.82}


# ============================================================
# 十、调度器启动（幂等）
# ============================================================


class TestSchedulerLifecycle:
    def test_start_idempotent_and_stop(self, pdb, monkeypatch):
        timer_secs = []

        def _fake_timer(sec, fn):
            timer_secs.append(sec)
            return type('T', (), {'start': lambda self: None})()

        monkeypatch.setattr(patrol.threading, 'Timer', _fake_timer)
        patrol.start_intraday_patrol()
        patrol.start_intraday_patrol()  # 幂等：第二次启动不再创建 Timer
        assert patrol._started is True
        assert timer_secs == [0]  # 启动即检（Timer(0) 立即跑首个 tick）
        patrol.stop_intraday_patrol()
        assert patrol._started is False


# ============================================================
# 十一、t3 速览卡后端增量：今日信号标记 overlay + 读路径行键
# ============================================================


class TestTodaySignalOverlay:
    def test_scan_today_signal_labels(self, pdb, monkeypatch):
        """信号 overlay：仅"最新K线日"命中计入（与行动清单路2 口径一致）；
        买/卖两侧分别聚合，side/label 正确"""
        import modules.market_screener as ms

        def fake_buy(stock_ids=None, **kw):
            return {'results': [{'stock_id': 1, 'kline_upto': '2026-09-22',
                                 'matches': [
                                     {'signal': 'res_double_golden', 'label': '日线周线双金叉',
                                      'trigger_date': '2026-09-22'},
                                     {'signal': 'macd_golden', 'label': 'MACD金叉',
                                      'trigger_date': '2026-09-18'},  # 非最新K线日：不计
                                 ]}]}

        def fake_sell(stock_ids=None, **kw):
            return {'results': [{'stock_id': 2, 'kline_upto': '2026-09-22',
                                 'sell_matches': [
                                     {'signal': 'kdj_dead_high', 'label': 'KDJ高位死叉',
                                      'trigger_date': '2026-09-22'}]}]}

        monkeypatch.setattr(ms, 'scan_watchlist_signals', fake_buy)
        monkeypatch.setattr(ms, 'scan_watchlist_sell_signals', fake_sell)
        labels = patrol._scan_today_signal_labels([1, 2])
        assert len(labels[1]) == 1  # 历史命中被过滤
        assert labels[1][0]['side'] == 'buy'
        assert labels[1][0]['label'] == '日线周线双金叉'
        assert labels[2][0]['side'] == 'sell'
        assert labels[2][0]['label'] == 'KDJ高位死叉'

    def test_scan_screener_failure_silent(self, pdb, monkeypatch):
        """信号复算整体失败 → 静默空表（标记非关键信息，不阻塞速览卡）"""
        import modules.market_screener as ms

        def _boom(stock_ids=None, **kw):
            raise RuntimeError('复算失败')

        monkeypatch.setattr(ms, 'scan_watchlist_signals', _boom)
        monkeypatch.setattr(ms, 'scan_watchlist_sell_signals', _boom)
        assert patrol._scan_today_signal_labels([1]) == {}

    def test_scan_empty_ids_short_circuit(self):
        """无持仓 → 零扫描（不导入 screener 不触库）"""
        assert patrol._scan_today_signal_labels([]) == {}

    def test_dashboard_snapshot_row_keys(self, pdb, monkeypatch):
        """t3 读路径契约：stocks 行携带 ma20/ma20_distance_pct/signal_labels 键
        （真实 screener 在临时库上跑，5根K线不足门槛 → 空标记）"""
        _patch_clock(monkeypatch)
        snap = patrol.get_snapshot_for_dashboard()
        assert snap['success'] is True
        row = snap['stocks'][0]
        assert 'ma20' in row and 'ma20_distance_pct' in row
        assert row['ma20'] is None  # pdb 仅 5 根K线
        assert row['signal_labels'] == []


# ============================================================
# 十二、t5 收尾修复：F1 非时段兜底快照不产盘中项 + F2 阈值单一来源
# ============================================================


class TestT5F1FallbackNoAlerts:
    def test_fallback_snapshot_no_intraday_alerts(self, pdb, monkeypatch):
        """F1 回归锁：非交易时段打开看板 → fallback 兜底快照（价格源=缓存）不得
        产生盘中触线行动项（终验实测缺陷复现：缓存价破线曾被 P0 置顶）；
        速览卡展示不受影响（快照仍生成、状态仍如实标注）"""
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO price_cache (stock_id, latest_price, pct_change, updated_at) "
            "VALUES (1, 9.0, -2.0, '2026-09-22 15:00:00')")  # 缓存价 9.0 破成本线 9.2
        conn.commit()
        conn.close()
        _patch_clock(monkeypatch, in_session=False)  # 非交易时段
        snap = patrol.get_snapshot_for_dashboard()
        assert snap['source'] == 'fallback'
        assert snap['stocks'][0]['state'] == 'below_stop'  # 展示层如实标注
        assert patrol.get_intraday_alerts() == []  # 提醒层被门控
        result = build_action_list(
            TODAY, [{'stock_id': 1, 'symbol': '600276', 'name': '恒瑞医药'}], [], [], None)
        assert not [it for it in result['items'] if it['kind'] == 'intraday_alert']

    def test_auto_round_cached_row_no_alert(self, pdb, monkeypatch):
        """F1 行级实时门：auto 轮中 quote_ok=False（如混合市场休市侧缓存兜底行）
        不产提醒——提醒必须来自本_round今日实时快照"""
        _seed_snapshot({
            1: ('600276', '恒瑞医药', 9.0, -2.0, 9.2, 'below_stop', False),
            2: ('003816', '广弘控股', 4.5, -1.0, 4.6, 'below_stop', False),
        })
        patrol._LAST_SNAPSHOT['stocks'][0]['quote_ok'] = False  # 缓存兜底行
        alerts = patrol.get_intraday_alerts()
        assert [a['stock_id'] for a in alerts] == [2]  # 仅实时快照行

    def test_auto_round_alerts_still_flow(self, pdb, monkeypatch):
        """F1 语义边界：门控仅收紧非巡检来源，巡检真实轮（auto）提醒不受影响"""
        _patch_clock(monkeypatch)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 9.0, -2.0)})
        patrol.run_patrol_round()  # 真实轮 → source='auto'
        alerts = patrol.get_intraday_alerts()
        assert len(alerts) == 1
        assert alerts[0]['state'] == 'below_stop'


class TestT5F2ThresholdSingleSource:
    def _stocks(self):
        return [{'stock_id': 1, 'symbol': '600276', 'name': '恒瑞医药'}]

    def test_near_text_follows_config(self, monkeypatch):
        """F2：逼近文案读 config.INTRADAY_NEAR_STOP_PCT——改阈值文案单点生效"""
        _seed_snapshot({
            1: ('600276', '恒瑞医药', 9.28, 0.1, 9.2, 'near_stop', False),
        })
        monkeypatch.setattr(config, 'INTRADAY_NEAR_STOP_PCT', 2.0)
        result = build_action_list(TODAY, self._stocks(), [], [], None)
        near_items = [it for it in result['items'] if it['kind'] == 'intraday_alert']
        assert len(near_items) == 1
        assert '不足 2%' in near_items[0]['reason']
        assert '不足 1%' not in near_items[0]['reason']

    def test_default_near_text_is_one_pct(self, monkeypatch):
        """默认阈值 1.0 → 文案'不足 1%'（回归基线）"""
        _seed_snapshot({
            1: ('600276', '恒瑞医药', 9.28, 0.1, 9.2, 'near_stop', False),
        })
        result = build_action_list(TODAY, self._stocks(), [], [], None)
        near_items = [it for it in result['items'] if it['kind'] == 'intraday_alert']
        assert '不足 1%' in near_items[0]['reason']

    def test_snapshot_thresholds_block(self, pdb, monkeypatch):
        """F2：快照透出 thresholds（config 值），前端高亮消费 state 的数据基础；
        巡检判定本身同步跟随阈值（9.28 距 9.2 约 0.87%：1% 下 near，2.5% 下 normal）"""
        _patch_clock(monkeypatch)
        monkeypatch.setattr(config, 'INTRADAY_NEAR_STOP_PCT', 2.5)
        monkeypatch.setattr(config, 'INTRADAY_SWING_PCT', 4.0)
        _patch_fetch(monkeypatch, quotes={1: _quote(1, 9.28, 0.1)})
        result = patrol.run_patrol_round()
        snap = result['snapshot']
        assert snap['thresholds']['near_stop_pct'] == 2.5
        assert snap['thresholds']['swing_pct'] == 4.0
        assert snap['stocks'][0]['state'] == 'near_stop'  # 0.87% 在 2.5% 带内
