"""
021BY：位置分位证据选股 + 行业资金流软联动（市场扫描器第一批优化）

覆盖（离线，不触网）：
1. TestPositionPctile：位置分位纯函数——与 backtest_engine._calc_pos_and_dd20
   公式对拍（同源唯一真相守卫：同输入必同值，改口径必红）、不足40根 None、
   hi==lo None、分带边界（low<0.4 ≤ mid <0.7 ≤ high）、60 根回看窗。
2. TestRunSignalChunkPos：第②段透传——命中结果附 pos_pctile/pos_band
   （复用已拉取日K，零新增请求；不进 SIGNAL_LIBRARY/RESONANCE_LIBRARY 判定）。
3. TestScanSignalsBlueprint：scan-signals 端点行业资金流软联动（读库零网络）——
   匹配成功附 industry_flow_bg；匹配不上不硬造；无资金流数据不报错。
"""

import json

import pytest

import app as app_module
from database import db_manager
from modules.backtest_engine import _calc_pos_and_dd20
from modules.market_screener import (
    position_pctile_of_klines,
    run_signal_chunk,
)


def _mk_klines(closes):
    """closes → K线行（与 021BI 测试同型：high/low 由 close 微扰生成）。"""
    rows = []
    for i, c in enumerate(closes):
        rows.append({
            'date': f'2026-{1 + i // 28:02d}-{1 + i % 28:02d}',
            'open': c * 0.99,
            'close': c,
            'high': c * 1.01,
            'low': c * 0.98,
            'volume': 1000.0,
        })
    return rows


def _deep_v_closes():
    """深V形态：45 根下跌 + 4 根反弹（触发 KDJ低位金叉 + MACD水下金叉）。"""
    closes = [110 - i * 1.5 for i in range(45)]
    closes += [closes[-1] + 2.0 * (i + 1) for i in range(4)]
    return closes


# ================================================================
# C1: 位置分位纯函数（口径同源守卫）
# ================================================================


class TestPositionPctile:
    def test_matches_backtest_engine_formula(self, tmp_path, monkeypatch):
        """公式对拍（同源唯一真相）：同一份 60 根收盘，扫描器纯函数与
        backtest_engine._calc_pos_and_dd20（021BU 证据列 pos_pctile 的来源）必须同值。"""
        monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'pos_check.db'))
        db_manager.init_database()
        closes = [100 + (i * 37 % 89) + i * 0.7 for i in range(60)]   # 确定性乱序波动
        conn = db_manager.get_connection()
        conn.execute(
            'INSERT INTO stocks (id, symbol, market, name) VALUES (9999, ?, ?, ?)',
            ('999999', 'a_stock', '对拍用'),
        )
        for i, c in enumerate(closes):
            conn.execute(
                'INSERT INTO raw_kline (stock_id, trade_date, close, high, low, volume) '
                'VALUES (9999, ?, ?, ?, ?, 1000)',
                (f'2026-{4 + i // 28:02d}-{1 + i % 28:02d}', c, c * 1.01, c * 0.98),
            )
        last_date = f'2026-{4 + 59 // 28:02d}-{1 + 59 % 28:02d}'
        bt_pos, _ = _calc_pos_and_dd20(conn.cursor(), 9999, last_date)
        conn.close()
        scan = position_pctile_of_klines(_mk_klines(closes))
        assert bt_pos is not None
        assert scan['pos_pctile'] == pytest.approx(bt_pos, abs=1e-9)
        assert scan['pos_band'] in ('low', 'mid', 'high')

    def test_too_few_bars_none(self):
        """不足 40 根 → 数据不足，None 不硬造。"""
        assert position_pctile_of_klines(_mk_klines([100 - i for i in range(39)])) == {
            'pos_pctile': None, 'pos_band': None}

    def test_flat_closes_none(self):
        """60 根全部同价（hi==lo）→ 无法定义分位，None。"""
        assert position_pctile_of_klines(_mk_klines([50.0] * 60)) == {
            'pos_pctile': None, 'pos_band': None}

    def test_band_boundaries(self):
        """分带边界：0.39 low / 0.4 mid / 0.699 mid / 0.7 high（与 POS_BANDS 同阈值）。"""
        base = [100.0] * 40 + [200.0] + [100.0] * 18      # hi=200 lo=100 span=100
        for now, want_band in ((139.0, 'low'), (140.0, 'mid'),
                               (169.9, 'mid'), (170.0, 'high')):
            closes = base[:] + [now]
            got = position_pctile_of_klines(_mk_klines(closes))
            assert got['pos_band'] == want_band, f'now={now}'
            assert got['pos_pctile'] == round((now - 100.0) / 100.0, 3)

    def test_lookback_window_60(self):
        """仅回看最近 60 根：第 1 根的极端值(300)不进 hi/lo。"""
        closes = [300.0] + [100.0] * 59 + [140.0]         # 61 根 → 窗口剔除首根
        got = position_pctile_of_klines(_mk_klines(closes))
        assert got['pos_pctile'] == 1.0                    # (140-100)/(140-100)
        assert got['pos_band'] == 'high'

    def test_missing_close_rows_skipped(self):
        """close 缺失的行跳过不计数（不抛错）。"""
        rows = _mk_klines([100.0 + i for i in range(50)])
        rows.insert(0, {'date': '2026-01-01'})            # 无 close 字段
        got = position_pctile_of_klines(rows)
        assert got['pos_pctile'] is not None

    def test_empty_input_none(self):
        assert position_pctile_of_klines([]) == {'pos_pctile': None, 'pos_band': None}
        assert position_pctile_of_klines(None) == {'pos_pctile': None, 'pos_band': None}


# ================================================================
# C1: run_signal_chunk 透传（mock K线，零网络）
# ================================================================


class TestRunSignalChunkPos:
    def test_hits_carry_position_fields(self, monkeypatch):
        """命中结果附 pos_pctile/pos_band，且与纯函数对同一份 K 线同值。"""
        klines = _mk_klines(_deep_v_closes())
        monkeypatch.setattr('modules.market_screener.fetch_kline', lambda sym, count=120: klines)
        monkeypatch.setattr('modules.market_screener.fetch_kline_weekly', lambda sym, count=60: [])

        out = run_signal_chunk([{'symbol': 'sz300750', 'name': '测试股'}], window=4)
        assert out['errors'] == []
        assert out['results'], '深V形态应命中至少一个金叉信号'
        hit = out['results'][0]
        assert 'pos_pctile' in hit and 'pos_band' in hit
        expect = position_pctile_of_klines(klines)
        assert hit['pos_pctile'] == expect['pos_pctile']
        assert hit['pos_band'] == expect['pos_band']

    def test_hit_judgment_untouched_by_position(self, monkeypatch):
        """位置分位不参与命中判定：chunk 内 matches 与 detect_signals 直接输出一致
        （位置只做展示/筛选/排序标注——「只产买点」命中语义不动的回归位）。"""
        from modules.market_screener import SIGNAL_LIBRARY, detect_signals

        klines = _mk_klines(_deep_v_closes())
        monkeypatch.setattr('modules.market_screener.fetch_kline', lambda sym, count=120: klines)
        monkeypatch.setattr('modules.market_screener.fetch_kline_weekly', lambda sym, count=60: [])
        out = run_signal_chunk([{'symbol': 'sz300750', 'name': '测试股'}], window=4)
        direct = detect_signals(klines, wanted=list(SIGNAL_LIBRARY.keys()), window=4)
        assert [m['signal'] for m in out['results'][0]['matches']] == \
               [m['signal'] for m in direct]


# ================================================================
# C2: scan-signals 端点行业资金流软联动（读库 seed + mock screener）
# ================================================================


@pytest.fixture
def flow_db(tmp_path, monkeypatch):
    db_file = tmp_path / 'flow021by.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    rows = [
        ('2026-08-13', 'BK1', '半导体', 200.0),
        ('2026-08-13', 'BK2', '白酒', -100.0),
        ('2026-08-14', 'BK1', '半导体', 300.0),
        ('2026-08-14', 'BK2', '白酒', -150.0),
    ]
    for trade_date, code, name, main_net in rows:
        conn.execute(
            'INSERT INTO industry_fund_flow '
            '(trade_date, code, name, pct_change, main_net, main_pct, super_net, big_net, mid_net, small_net, lead_stock) '
            'VALUES (?, ?, ?, 0, ?, 0, 0, 0, 0, 0, NULL)',
            (trade_date, code, name, main_net),
        )
    conn.commit()
    conn.close()
    return db_manager


def _mock_chunk(monkeypatch, results):
    monkeypatch.setattr(
        'modules.market_screener.run_signal_chunk',
        lambda entries, signals=None, window=3: {'results': results, 'errors': []},
    )


class TestScanSignalsBlueprint:
    def _post(self, entries):
        app_module.app.config['TESTING'] = True
        client = app_module.app.test_client()
        resp = client.post(
            '/api/market/scan-signals',
            data=json.dumps({'entries': entries, 'signals': ['kdj_golden'], 'window': 3}),
            content_type='application/json',
        )
        return json.loads(resp.data)

    def test_matched_industry_gets_flow_bg(self, flow_db, monkeypatch):
        """新浪行业名匹配东财板块 → 附 industry_flow_bg（读库零网络）。"""
        _mock_chunk(monkeypatch, [{'symbol': 'sh600000', 'name': '某半导体', 'matches': [
            {'signal': 'kdj_golden', 'label': 'KDJ金叉'}]}])
        data = self._post([{'symbol': 'sh600000', 'name': '某半导体', 'industry': '半导体'}])
        assert data['success'] is True
        item = data['results'][0]
        assert item['industry_flow_bg'] is not None
        assert item['industry_flow_bg']['main_net'] == 300.0
        assert item['industry_flow_bg']['streak_days'] == 2
        assert data['industry_match_hit'] == 1
        assert data['industry_match_total'] == 1

    def test_unmatched_industry_none_no_error(self, flow_db, monkeypatch):
        """匹配不上的行业 → bg 为 None，不硬造、不报错。"""
        _mock_chunk(monkeypatch, [{'symbol': 'sh600000', 'name': '某股', 'matches': [
            {'signal': 'kdj_golden', 'label': 'KDJ金叉'}]}])
        data = self._post([{'symbol': 'sh600000', 'name': '某股', 'industry': '不存在的行业'}])
        assert data['success'] is True
        assert data['results'][0]['industry_flow_bg'] is None
        assert data['industry_match_hit'] == 0

    def test_entry_without_industry_ok(self, flow_db, monkeypatch):
        """entries 不带 industry（旧前端兼容）→ 正常返回，无 bg。"""
        _mock_chunk(monkeypatch, [{'symbol': 'sh600000', 'name': '某股', 'matches': []}])
        data = self._post([{'symbol': 'sh600000', 'name': '某股'}])
        assert data['success'] is True
        assert data['results'][0].get('industry_flow_bg') is None

    def test_no_flow_data_no_fields(self, tmp_path, monkeypatch):
        """库内无行业资金流数据 → 不附 bg 字段值也不报错（软联动纯增量）。"""
        monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'noflow021by.db'))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        _mock_chunk(monkeypatch, [{'symbol': 'sh600000', 'name': '某股', 'matches': []}])
        data = self._post([{'symbol': 'sh600000', 'name': '某股', 'industry': '半导体'}])
        assert data['success'] is True
        assert data['results'][0].get('industry_flow_bg') is None
        assert 'industry_match_hit' not in data

    def test_alias_and_substring_matching(self, flow_db, monkeypatch):
        """别名（酿酒行业→食品饮料走 INDUSTRY_ALIAS）与空行业名降级路径。"""
        _mock_chunk(monkeypatch, [
            {'symbol': 'sh600000', 'name': '酒企', 'matches': []},
            {'symbol': 'sz000001', 'name': '白名单外', 'matches': []},
        ])
        data = self._post([
            {'symbol': 'sh600000', 'name': '酒企', 'industry': '酿酒行业'},
            {'symbol': 'sz000001', 'name': '白名单外', 'industry': ''},
        ])
        assert data['success'] is True
        bgs = {r['symbol']: r['industry_flow_bg'] for r in data['results']}
        assert bgs['sh600000'] is None          # 库内无食品饮料板块 → 匹配不上不硬造
        assert bgs['sz000001'] is None          # 空行业名不参与匹配


# ================================================================
# t4/F-V2: 行业背景缓存——轻查询键探测先行，命中不重复全量装配
# ================================================================


class TestIndustryBgCache:
    def _reset_cache(self):
        import blueprints.market as market_bp

        market_bp._industry_flow_cache['key'] = None
        market_bp._industry_flow_cache['bg_map'] = {}
        return market_bp

    def _counting_map(self, monkeypatch):
        """monkeypatch get_industry_flow_bg_map：计数调用次数，委托真实实现。"""
        import modules.market_overview as mo

        calls = {'n': 0}
        real = mo.get_industry_flow_bg_map

        def counting():
            calls['n'] += 1
            return real()

        monkeypatch.setattr(mo, 'get_industry_flow_bg_map', counting)
        return calls

    def test_cache_hit_skips_full_assembly(self, flow_db, monkeypatch):
        """F-V2：同交易日第二次调用命中缓存——全量装配（含 compute_streaks 全表扫描）只跑一次。"""
        market_bp = self._reset_cache()
        calls = self._counting_map(monkeypatch)
        first = market_bp._scan_industry_bg_map()
        second = market_bp._scan_industry_bg_map()
        assert calls['n'] == 1                       # 修复前：每次调用都全量装配（n=2）
        assert first == second
        assert first['半导体']['main_net'] == 300.0

    def test_empty_table_zero_full_assembly(self, tmp_path, monkeypatch):
        """F-V2：键探测（MAX(trade_date) 轻查询）先行——空表直接返回空，不进全量装配。"""
        monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'cacheempty021by.db'))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        market_bp = self._reset_cache()
        calls = self._counting_map(monkeypatch)
        assert market_bp._scan_industry_bg_map() == {}
        assert calls['n'] == 0                       # 键探测即返回，全量装配零次

    def test_new_trade_date_reassembles(self, flow_db, monkeypatch):
        """F-V2 数据新鲜度：新交易日落库 → 缓存键变化 → 重新全量装配。"""
        market_bp = self._reset_cache()
        calls = self._counting_map(monkeypatch)
        first = market_bp._scan_industry_bg_map()
        assert calls['n'] == 1
        # 模拟新交易日数据落库（键随之变化）
        conn = db_manager.get_connection()
        conn.execute(
            'INSERT INTO industry_fund_flow '
            '(trade_date, code, name, pct_change, main_net, main_pct, super_net, big_net, mid_net, small_net, lead_stock) '
            "VALUES ('2026-08-15', 'BK1', '半导体', 0, 400.0, 0, 0, 0, 0, 0, NULL)"
        )
        conn.commit()
        conn.close()
        fresh = market_bp._scan_industry_bg_map()
        assert calls['n'] == 2                       # 键变化 → 重新装配
        assert fresh['半导体']['main_net'] == 400.0  # 新数据可见（旧缓存未粘连）
