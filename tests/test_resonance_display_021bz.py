"""
021BZ 共振类型与强弱显性化：展示层纯函数 + 双处落点 + 契约锁 + 诚实文案

依据：docs/reports/021bz_resonance_plan_20260925.md（t1 方案 §3/§4/§6）。

覆盖：
1. 纯函数：强弱分级映射边界（5/4/3★→强/中/弱；2★/None/非数→None 不硬造）、
   方向标注（bull→多/bear→空/未知→None）、触发日提取（多日期取 max、空串、
   无 @date→None）、resonance_view additive（不改原键、不动入参、时效口径）、
   分级诚实声明（grade note）与文案禁裸 '<'
2. 契约锁：detect_resonances/detect_sell_resonances/compute_watchlist_* 输出
   键集零变化；run_signal_chunk 既有键全保留 + additive kline_upto
3. 端点：scan-signals / watchlist-signals / watchlist-sell-signals 响应共振带
   统一徽标数据；/advise 与 /report-latest 的 resonance_snapshot 在场且两路径
   键同构；无K线降级不崩；响应值与纯函数直调同源同值（两处展示同源）
4. 行为等价：trader_advisor._res_date_desc 复用共享提取后 021BR 语义不变

隔离：临时库（monkeypatch DB_PATH），不触网、不触碰真实 stock_analyst.db。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

import app as app_module
from database import db_manager
from modules.market_screener import (
    GRADE_NOTE_KEY,
    RESONANCE_GRADE,
    compute_watchlist_sell_result,
    compute_watchlist_signal_result,
    detect_resonances,
    detect_sell_resonances,
    direction_label_for,
    latest_trigger_date_of,
    resonance_grade_note,
    resonance_view,
    run_signal_chunk,
    strength_grade_for,
)


def _mk_klines(closes):
    """closes → K线行（与 021BI/021BY 测试同型）。"""
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
    """深V形态：45 根下跌 + 2 根反弹（双金叉落在 window=3 内，触发双金叉共振跨日 3★）。"""
    closes = [110 - i * 1.5 for i in range(45)]
    closes += [closes[-1] + 2.0 * (i + 1) for i in range(2)]
    return closes


def _bear_hits(same_day=True):
    """卖出侧命中（双死叉）：同日 4★ / 跨日 3★ 可切换。"""
    d = '2026-09-22' if same_day else None
    return [
        {'signal': 'macd_dead_below', 'label': 'MACD水下死叉',
         'trigger_date': d or '2026-09-20', 'note': ''},
        {'signal': 'kdj_dead_high', 'label': 'KDJ高位死叉',
         'trigger_date': d or '2026-09-22', 'note': ''},
    ]


def _bull_hits(same_day=True):
    """买侧命中（双金叉）：同日 4★ / 跨日 3★ 可切换。"""
    d = '2026-09-22' if same_day else None
    return [
        {'signal': 'macd_golden_below', 'label': 'MACD水下金叉',
         'trigger_date': d or '2026-09-20', 'note': ''},
        {'signal': 'kdj_golden_low', 'label': 'KDJ低位金叉',
         'trigger_date': d or '2026-09-22', 'note': ''},
    ]


# ================================================================
# 一、纯函数：强弱分级 / 方向 / 触发日 / 展示视图 / 诚实声明
# ================================================================


class TestStrengthGrade:
    def test_full_mapping(self):
        """5★/4★/3★ → 强/中/弱（纯标注映射全表）。"""
        assert RESONANCE_GRADE == {5: '强', 4: '中', 3: '弱'}
        assert strength_grade_for(5) == '强'
        assert strength_grade_for(4) == '中'
        assert strength_grade_for(3) == '弱'

    def test_unknown_not_fabricated(self):
        """未知/缺失不硬造：2★/None/字符串/浮点整值边界。"""
        assert strength_grade_for(2) is None
        assert strength_grade_for(None) is None
        assert strength_grade_for('x') is None
        assert strength_grade_for(True) is None          # bool 防御（int 子类）
        assert strength_grade_for(5.0) == '强'            # 数值整值兼容
        assert strength_grade_for(4.5) is None            # 非整星级不硬造

    def test_dynamic_star_double_golden(self):
        """双金叉动态星：同日 4★→中、跨日 3★→弱（检测输出零改动，映射自然成立）。"""
        same = detect_resonances(_bull_hits(same_day=True))
        cross = detect_resonances(_bull_hits(same_day=False))
        assert same[0]['stars'] == 4 and strength_grade_for(same[0]['stars']) == '中'
        assert cross[0]['stars'] == 3 and strength_grade_for(cross[0]['stars']) == '弱'


class TestDirectionLabel:
    def test_bull_bear(self):
        assert direction_label_for('bull') == '多'
        assert direction_label_for('bear') == '空'

    def test_unknown_none(self):
        assert direction_label_for('') is None
        assert direction_label_for('long') is None
        assert direction_label_for(None) is None


class TestLatestTriggerDate:
    def test_multi_date_takes_max(self):
        sig = 'KDJ低位金叉@2026-09-24 + MACD水下金叉@2026-09-22'
        assert latest_trigger_date_of(sig) == '2026-09-24'

    def test_empty_and_missing(self):
        assert latest_trigger_date_of('') is None
        assert latest_trigger_date_of('无日期字串') is None
        assert latest_trigger_date_of(None) is None

    def test_single_date(self):
        assert latest_trigger_date_of('MACD水上金叉@2026-09-01') == '2026-09-01'


class TestResonanceView:
    def test_additive_keys_original_untouched(self):
        """视图 = 原条目浅拷贝 + 追加键；原 dict 键集与值零改动。"""
        res = detect_resonances(_bull_hits(same_day=True))[0]
        before = dict(res)
        view = resonance_view(res, kline_upto='2026-09-22')
        assert res == before                                    # 入参未被改动
        assert set(before.keys()) <= set(view.keys())           # 原键全保留
        assert {'grade', 'direction', 'trigger_date',
                'timeliness'} <= set(view.keys()) - set(before.keys())
        assert view['grade'] == '中'
        assert view['direction'] == '多'
        assert view['trigger_date'] == '2026-09-22'
        assert view['timeliness'] == '今日'

    def test_timeliness_history_not_today(self):
        """早于 kline_upto → 窗口内历史（历史触发不得读起来像新信号）。"""
        res = detect_resonances(_bull_hits(same_day=True))[0]
        view = resonance_view(res, kline_upto='2026-09-23')
        assert view['timeliness'] == '窗口内历史，非今日'

    def test_missing_upto_or_trigger_none(self):
        """kline_upto 缺失 / 触发日解析失败 → timeliness=None，不硬造。"""
        res = detect_resonances(_bull_hits())[0]
        v1 = resonance_view(res, kline_upto=None)
        assert v1['timeliness'] is None and v1['trigger_date'] == '2026-09-22'
        bare = {'key': 'x', 'label': 'y', 'stars': 5, 'kind': 'bull', 'note': '', 'signals': ''}
        v2 = resonance_view(bare, kline_upto='2026-09-22')
        assert v2['trigger_date'] is None and v2['timeliness'] is None

    def test_bear_view(self):
        """卖侧条目：direction=空、grade 随 stars。"""
        res = detect_sell_resonances(_bear_hits(same_day=True))[0]
        view = resonance_view(res, kline_upto='2026-09-22')
        assert view['direction'] == '空'
        assert view['grade'] == '中'
        assert view['key'] == 'res_double_dead'

    def test_non_dict_none(self):
        assert resonance_view(None) is None
        assert resonance_view('x') is None


class TestGradeNote:
    def test_note_filled(self):
        note = resonance_grade_note(kline_upto='2026-09-25', window=3)
        assert '强弱分级说明' in note
        assert '2026-09-25' in note
        assert '3 个交易日' in note
        assert '星级（5★/4★/3★）的直接重标' in note
        assert '不构成' in note

    def test_missing_upto_placeholder(self):
        note = resonance_grade_note(kline_upto=None)
        assert '截止 —' in note

    def test_no_bare_less_than(self):
        """文案禁裸 '<'：模板与全填充输出扫描（021BW N10 同款手法）。"""
        assert '<' not in GRADE_NOTE_KEY
        for upto in (None, '2026-09-25'):
            for w in (1, 3, 10):
                assert '<' not in resonance_grade_note(kline_upto=upto, window=w)


# ================================================================
# 二、契约锁：检测器/复算链输出键集零变化（additive 只在组装层）
# ================================================================


class TestDetectorContractLock:
    def test_detect_resonances_keys(self):
        res = detect_resonances(_bull_hits(same_day=True))
        assert len(res) == 1
        assert set(res[0].keys()) == {'key', 'label', 'stars', 'kind', 'note', 'signals'}

    def test_detect_sell_resonances_keys(self):
        res = detect_sell_resonances(_bear_hits(same_day=False))
        assert len(res) == 1
        assert set(res[0].keys()) == {'key', 'label', 'stars', 'kind', 'note', 'signals'}
        assert res[0]['kind'] == 'bear'

    def test_compute_watchlist_keys(self):
        klines = _mk_klines(_deep_v_closes())
        buy = compute_watchlist_signal_result(klines, None)
        assert set(buy.keys()) == {'matches', 'resonances', 'kline_upto', 'kline_count'}
        sell = compute_watchlist_sell_result(klines, None)
        assert set(sell.keys()) == {'side', 'sell_matches', 'sell_resonances',
                                    'kline_upto', 'kline_count'}


class TestRunSignalChunkContract:
    def test_existing_keys_plus_kline_upto(self, monkeypatch):
        """既有键全保留 + additive kline_upto（=最后K线日期，零新增请求）。"""
        klines = _mk_klines(_deep_v_closes())
        monkeypatch.setattr('modules.market_screener.fetch_kline', lambda sym, count=120: klines)
        monkeypatch.setattr('modules.market_screener.fetch_kline_weekly', lambda sym, count=60: [])
        out = run_signal_chunk([{'symbol': 'sz300750', 'name': '测试股'}], window=4)
        assert out['errors'] == []
        hit = out['results'][0]
        for legacy in ('symbol', 'name', 'matches', 'pos_pctile', 'pos_band', 'resonances'):
            assert legacy in hit, legacy
        assert hit['kline_upto'] == klines[-1]['date']
        # 共振条目本体仍为检测器原样（视图映射在端点组装层，chunk 不改写）
        assert set(hit['resonances'][0].keys()) == {'key', 'label', 'stars', 'kind', 'note', 'signals'}


# ================================================================
# 三、端点：三扫描端点徽标数据 + 报告双路径 resonance_snapshot
# ================================================================


class TestScanSignalsEndpoint:
    def _post(self, entries, signals=None):
        app_module.app.config['TESTING'] = True
        client = app_module.app.test_client()
        resp = client.post(
            '/api/market/scan-signals',
            data=json.dumps({'entries': entries, 'signals': signals or ['kdj_golden'], 'window': 3}),
            content_type='application/json',
        )
        return json.loads(resp.data)

    def test_resonances_carry_view_fields(self, monkeypatch):
        """响应共振带 grade/direction/trigger_date/timeliness（kline_upto 透传口径）。"""
        res = detect_resonances(_bull_hits(same_day=True))[0]
        monkeypatch.setattr(
            'modules.market_screener.run_signal_chunk',
            lambda entries, signals=None, window=3: {
                'results': [{'symbol': 'sh600000', 'name': '某股', 'matches': [],
                             'pos_pctile': None, 'pos_band': None,
                             'kline_upto': '2026-09-25', 'resonances': [res]}],
                'errors': [],
            },
        )
        data = self._post([{'symbol': 'sh600000', 'name': '某股'}])
        assert data['success'] is True
        view = data['results'][0]['resonances'][0]
        assert view['grade'] == '中'
        assert view['direction'] == '多'
        assert view['trigger_date'] == '2026-09-22'
        assert view['timeliness'] == '窗口内历史，非今日'
        # 检测器原键零丢失（additive）
        assert set(res.keys()) <= set(view.keys())

    def test_no_resonance_no_error(self, monkeypatch):
        """无共振命中 → 响应正常（空列表），不硬造字段。"""
        monkeypatch.setattr(
            'modules.market_screener.run_signal_chunk',
            lambda entries, signals=None, window=3: {
                'results': [{'symbol': 'sh600000', 'name': '某股', 'matches': [],
                             'pos_pctile': None, 'pos_band': None,
                             'kline_upto': '2026-09-25', 'resonances': []}],
                'errors': [],
            },
        )
        data = self._post([{'symbol': 'sh600000', 'name': '某股'}])
        assert data['success'] is True
        assert data['results'][0]['resonances'] == []


class TestWatchlistScanEndpoints:
    def _client(self):
        app_module.app.config['TESTING'] = True
        return app_module.app.test_client()

    def test_buy_endpoint_view_fields(self, monkeypatch):
        res = detect_resonances(_bull_hits(same_day=True))[0]
        monkeypatch.setattr(
            'modules.market_screener.scan_watchlist_signals',
            lambda stock_ids=None, signals=None, window=3, daily_limit=250: {
                'scope': 'watchlist_offline', 'stock_count': 1,
                'results': [{'stock_id': 1, 'symbol': '600001', 'name': '甲',
                             'matches': [], 'resonances': [res],
                             'kline_upto': '2026-09-25', 'kline_count': 49}],
                'errors': [],
            },
        )
        resp = self._client().get('/api/market/scan/watchlist-signals')
        assert resp.status_code == 200
        view = resp.get_json()['results'][0]['resonances'][0]
        assert view['grade'] == '中' and view['direction'] == '多'
        assert view['timeliness'] == '窗口内历史，非今日'

    def test_sell_endpoint_view_fields(self, monkeypatch):
        res = detect_sell_resonances(_bear_hits(same_day=True))[0]
        monkeypatch.setattr(
            'modules.market_screener.scan_watchlist_sell_signals',
            lambda stock_ids=None, signals=None, window=3, daily_limit=250: {
                'scope': 'watchlist_offline', 'side': 'sell', 'stock_count': 1,
                'results': [{'stock_id': 1, 'symbol': '600001', 'name': '甲',
                             'sell_matches': [], 'sell_resonances': [res],
                             'kline_upto': '2026-09-25', 'kline_count': 49}],
                'errors': [],
            },
        )
        resp = self._client().get('/api/market/scan/watchlist-sell-signals')
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['side'] == 'sell'
        view = body['results'][0]['sell_resonances'][0]
        assert view['grade'] == '中' and view['direction'] == '空'


# ---- 报告双路径环境：临时库 + 深V K线（真实触发共振）+ 今日快照报告 ----


def _seed_stock_with_klines(stock_id=1, symbol='600001', name='甲'):
    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (id, symbol, market, name) VALUES (?, ?, 'a_stock', ?)",
            (stock_id, symbol, name),
        )
        klines = _mk_klines(_deep_v_closes())
        for k in klines:
            conn.execute(
                'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (stock_id, k['date'], k['open'], k['close'], k['high'], k['low'], k['volume']),
            )
        conn.commit()
    finally:
        conn.close()
    return _mk_klines(_deep_v_closes())


@pytest.fixture()
def report_db(tmp_path, monkeypatch):
    """隔离库 + 股票/K线 + 今日新鲜 ok daily 快照（report-latest 走快照路径不触发重评）。"""
    db_file = tmp_path / 'test_res_021bz.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    klines = _seed_stock_with_klines()

    now_cn = datetime.now(timezone(timedelta(hours=8)))
    today = now_cn.strftime('%Y-%m-%d')
    generated_at = (now_cn - timedelta(minutes=1)).isoformat()
    conn = db_manager.get_connection()
    try:
        conn.execute(
            'INSERT INTO daily_reports (stock_id, report_date, report_type, engine_version, '
            'total_score, rating, rating_label, status, generated_at, markdown_content) '
            "VALUES (1, ?, 'daily', 'v5', 55.0, '持有观望', '观望', 'ok', ?, '## 报告')",
            (today, generated_at),
        )
        conn.commit()
    finally:
        conn.close()
    return {'klines': klines, 'today': today}


SNAPSHOT_KEYS = {'scope', 'window', 'kline_upto', 'kline_count', 'buy', 'sell', 'note'}


class TestReportResonanceSnapshot:
    def test_report_latest_snapshot_present(self, report_db):
        """快照路径：resonance_snapshot 在场、结构完整、买侧共振带全徽标字段。"""
        from app import app

        app.config['TESTING'] = True
        client = app.test_client()
        resp = client.get('/api/stocks/1/report-latest')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        snap = data['resonance_snapshot']
        assert snap is not None
        assert set(snap.keys()) == SNAPSHOT_KEYS
        assert snap['scope'] == 'watchlist_offline'
        assert snap['window'] == 3
        assert snap['kline_upto'] == report_db['klines'][-1]['date']
        assert snap['kline_count'] == len(report_db['klines'])
        assert len(snap['buy']) == 1 and snap['sell'] == []
        view = snap['buy'][0]
        assert view['key'] == 'res_double_golden'
        assert view['direction'] == '多'
        assert view['grade'] in ('中', '弱')            # 同日4★→中 / 跨日3★→弱
        assert view['trigger_date'] == report_db['klines'][-1]['date']
        assert snap['note'] == resonance_grade_note(
            kline_upto=snap['kline_upto'], window=snap['window'])
        assert '<' not in snap['note']

    def test_advise_snapshot_present_and_isomorphic(self, report_db, monkeypatch):
        """实时路径（mock 引擎）：resonance_snapshot 在场，且与快照路径键同构（两路径同源）。"""
        from app import app

        monkeypatch.setattr(
            'modules.advisor.generate_advice',
            lambda stock_id: {
                'success': True, 'stock_id': stock_id, 'stock_code': '600001',
                'stock_name': '甲', 'market': 'a_stock', 'engine_version': 'v5',
                'total_score': 55.0, 'rating': '持有观望', 'rating_label': '观望',
                'dimensions': {},
            },
        )
        app.config['TESTING'] = True
        client = app.test_client()
        resp = client.post('/api/stocks/1/advise')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        snap = data['resonance_snapshot']
        assert snap is not None
        assert set(snap.keys()) == SNAPSHOT_KEYS
        assert len(snap['buy']) == 1 and snap['buy'][0]['key'] == 'res_double_golden'

        # 两路径同构：对同一份K线，键集与买侧视图逐字段一致
        fresh = client.get('/api/stocks/1/report-latest').get_json()['resonance_snapshot']
        assert set(fresh.keys()) == set(snap.keys())
        assert fresh['buy'] == snap['buy']

    def test_same_source_as_pure_functions(self, report_db):
        """两处展示同源硬断言：响应值 == 共享纯函数直调值（同源同值）。"""
        from app import app

        app.config['TESTING'] = True
        client = app.test_client()
        snap = client.get('/api/stocks/1/report-latest').get_json()['resonance_snapshot']
        upto = report_db['klines'][-1]['date']
        expect = compute_watchlist_signal_result(report_db['klines'], None, window=3)
        assert snap['kline_upto'] == expect['kline_upto'] == upto
        assert snap['buy'] == [resonance_view(r, upto) for r in expect['resonances']]

    def test_no_klines_degrades_gracefully(self, report_db):
        """无K线股票：快照诚实为空（buy/sell 空 + kline_upto=None），success 不崩。"""
        from app import app

        conn = db_manager.get_connection()
        try:
            conn.execute(
                "INSERT INTO stocks (id, symbol, market, name) VALUES (2, '000002', 'a_stock', '乙')")
            conn.execute(
                'INSERT INTO daily_reports (stock_id, report_date, report_type, engine_version, '
                'total_score, rating, rating_label, status, generated_at, markdown_content) '
                "VALUES (2, ?, 'daily', 'v5', 50.0, '持有观望', '观望', 'ok', ?, '## 报告')",
                (report_db['today'], datetime.now(timezone(timedelta(hours=8))).isoformat()),
            )
            conn.commit()
        finally:
            conn.close()
        app.config['TESTING'] = True
        client = app.test_client()
        resp = client.get('/api/stocks/2/report-latest')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        snap = data['resonance_snapshot']
        assert snap is not None
        assert snap['buy'] == [] and snap['sell'] == []
        assert snap['kline_upto'] is None
        assert '<' not in snap['note']


# ================================================================
# 四、行为等价：_res_date_desc 复用共享提取（021BR 语义回归）
# ================================================================


class TestResDateDescEquivalence:
    def test_today_history_and_unknown(self):
        from modules.trader_advisor import _res_date_desc

        res = {'signals': 'KDJ低位金叉@2026-09-24 + MACD水下金叉@2026-09-22'}
        assert _res_date_desc(res, '2026-09-24') == '触发于 2026-09-24（今日）'
        assert _res_date_desc(res, '2026-09-25') == '触发于 2026-09-24（窗口内历史，非今日）'
        assert _res_date_desc(res, None) == '触发于 2026-09-24（窗口内历史，非今日）'
        assert _res_date_desc(None, '2026-09-24') == '触发日不详'
        assert _res_date_desc({'signals': ''}, '2026-09-24') == '触发日不详'

    def test_shared_extractor_is_single_implementation(self):
        """两处日期提取同源：_res_date_desc 结果由 latest_trigger_date_of 派生。"""
        from modules.market_screener import latest_trigger_date_of as _lo
        from modules.trader_advisor import _res_date_desc

        sig = 'A@2026-09-20 + B@2026-09-23'
        assert _lo(sig) == '2026-09-23'
        assert '2026-09-23' in _res_date_desc({'signals': sig}, '2026-09-23')
