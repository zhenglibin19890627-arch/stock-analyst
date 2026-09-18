"""
操盘手建议（2026-09-18）——阶段判定 / 主力解读 / 分歧处理 单元测试

锁死的行为契约（用户拍板的主从结构）：
- 评级是唯一动作主指令；分歧时不推翻评级——动作收窄为「放缓择时」或「等确认」，
  且永不输出与评级相反的加仓/清仓指令
- 分歧仅在评级边界档 × 中高置信阶段产生；低置信阶段不硬造分歧
- 阶段判定 6 态；量价结构（量能趋势/配合度/背离）为判定核心输入

隔离临时库：直接构造 raw_kline/holdings/daily_reports，走 generate_trader_advice 全流程。
"""

import json

import pytest

import app as app_module
from database import db_manager
from modules.trader_advisor import (
    STAGE_ACCUMULATION,
    STAGE_DECLINE,
    STAGE_DISTRIBUTION,
    STAGE_MARKUP_EARLY,
    STAGE_MARKUP_FULL,
    STAGE_RANGE,
    capital_narrative,
    classify_stage,
    detect_disagreement,
    generate_trader_advice,
)


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test_trader.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    app_module.app.config['TESTING'] = True
    c = app_module.app.test_client()

    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('002230', 'a_stock', '测试股')")
        conn.commit()
        cur = conn.cursor()
        cur.execute("SELECT id FROM stocks WHERE symbol='002230'")
        c._stock_id = cur.fetchone()['id']
    finally:
        conn.close()
    return c


def _seed_kline(client, stock_id, closes, volumes=None):
    """写入日K序列（收盘价列表；量默认恒定 1000）"""
    conn = db_manager.get_connection()
    try:
        for i, cl in enumerate(closes):
            vol = volumes[i] if volumes else 1000
            conn.execute(
                "INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (stock_id, f'2026-{6 + i // 28:02d}-{1 + i % 28:02d}', cl, cl, cl * 1.02, cl * 0.98, vol),
            )
        conn.commit()
    finally:
        conn.close()


def _seed_rating(client, stock_id, rating, score=50):
    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name, "
            "engine_version, total_score, rating, key_factors, status, report_type) "
            "VALUES ('2026-09-18', ?, '002230', '测试股', 'v5', ?, ?, '{}', 'ok', 'daily')",
            (stock_id, score, rating),
        )
        conn.commit()
    finally:
        conn.close()


def _mk_inputs(closes, volumes=None, holder=None, rating=None, score=50,
               ma5=None, ma20=None, ma60=None, daily='na', weekly='na', monthly='na',
               divergence=False):
    """构造 classify_stage/detect_disagreement 的最小 inputs"""
    n = len(closes)
    vols = volumes or [1000] * n
    # 类体作用域访问不到外层参数——先落局部变量
    _ma5 = ma5 if ma5 is not None else sum(closes[-5:]) / 5
    _ma20 = ma20 if ma20 is not None else sum(closes[-20:]) / 20
    _ma60 = ma60 if ma60 is not None else sum(closes) / n
    _holder = holder
    vs = {
        'vol_trend': round(sum(vols[-5:]) / sum(vols[-20:]) * 4, 2),
        'fit_ratio': 0.7,
        'top_divergence': divergence,
        'position_pctile': round((closes[-1] - min(closes)) / (max(closes) - min(closes)), 2) if max(closes) > min(closes) else 0.5,
        'amplitude_shrink': True,
        'low60': min(closes),
        'high60': max(closes),
    }

    class _Data:
        close = closes[-1]
        ma5 = _ma5
        ma20 = _ma20
        ma60 = _ma60
        rsi_14 = 50.0
        holder_count_change_pct = _holder
        main_net_inflow_5day = 500.0
        main_net_inflow = 800.0
        margin_balance_chg = 100.0
        institution_hold_ratio = None
        news_sentiment = 0.1

    return {
        'data': _Data(),
        'vs': vs,
        'trends': {
            'timeframes': {
                'daily': {'trend': daily},
                'weekly': {'trend': weekly},
                'monthly': {'trend': monthly},
            },
            'overall': {'trend': 'sideways'},
        },
        'rating': rating,
        'total_score': score,
        'holding_qty': 0,
        'cost_price': None,
    }


class TestVolumeStructure:
    """量价结构五件套"""

    def test_vol_trend_scales_with_volume(self, client):
        from modules.trader_advisor import _volume_structure
        closes = [10.0] * 40
        vols = [1000] * 35 + [3000] * 5  # 近5日放量
        vs = _volume_structure(
            [{'close': c, 'volume': v, 'high': c, 'low': c} for c, v in zip(closes, vols)])
        assert vs['vol_trend'] == pytest.approx(2.0, abs=0.1)  # mean5=3000 / mean20=1500
        assert vs['position_pctile'] is None  # 恒价无高低区间，分位不硬造

    def test_short_kline_degrades(self, client):
        from modules.trader_advisor import _volume_structure
        vs = _volume_structure([{'close': 10.0, 'volume': 100} for _ in range(10)])
        assert vs['vol_trend'] is None


class TestStageClassification:
    """6 态阶段判定"""

    def test_markup_full_bull_divergent(self):
        # 60日从8涨到14，最近10天放量：多头排列发散 + 量能抬升 + 高位 + 周日共振
        closes = [8 + i * 0.1 for i in range(60)]
        vols = [1000] * 50 + [2500] * 10  # 放量段在窗口尾部之外也有常量段，5日/20日均量拉开
        st = classify_stage(_mk_inputs(closes, vols, daily='up', weekly='up'))
        assert st['code'] == STAGE_MARKUP_FULL
        assert st['confidence'] == '强'

    def test_markup_early_pullback_pattern(self):
        # 底部横盘后温和抬升：多头排列成型 + 缩量健康上涨 + 日线上涨
        closes = [10 - i * 0.05 for i in range(30)] + [8.5 + i * 0.05 for i in range(30)]
        vols = [2000] * 30 + [1200] * 30  # 缩量上涨（惜售）
        st = classify_stage(_mk_inputs(closes, vols, daily='up'))
        assert st['code'] == STAGE_MARKUP_EARLY

    def test_decline_monthly_bear(self):
        closes = [14 - i * 0.06 for i in range(60)]
        st = classify_stage(_mk_inputs(closes, monthly='down', weekly='down', daily='down'))
        assert st['code'] == STAGE_DECLINE

    def test_accumulation_low_shrink(self):
        # 深跌后低位缩量横盘（量能阶梯萎缩）+ 户数下降
        closes = [15 - i * 0.1 for i in range(35)] + [11.5] * 25
        vols = [3000] * 35 + [1500] * 10 + [500] * 15
        st = classify_stage(_mk_inputs(closes, vols, holder=-12.0))
        assert st['code'] == STAGE_ACCUMULATION

    def test_range_fallback(self):
        # 无明显特征的震荡——兜底
        closes = [10 + (i % 3) * 0.1 for i in range(60)]
        st = classify_stage(_mk_inputs(closes))
        assert st['code'] == STAGE_RANGE

    def test_distribution_divergence(self):
        # 高位 + 新高量缩背离（mock 背离标记）
        closes = [10 + i * 0.08 for i in range(50)] + [14.0, 14.05, 14.1, 14.15, 14.2] * 2
        vols = [2500] * 40 + [1000] * 20  # 新高段量能腰斩
        st = classify_stage(_mk_inputs(closes, vols, daily='sideways', weekly='up',
                                       divergence=True))
        assert st['code'] == STAGE_DISTRIBUTION

    def test_evidence_populated(self):
        st = classify_stage(_mk_inputs([10.0] * 60, holder=-5.0))
        assert st['evidence'], '判断依据不得为空'
        assert any('筹码' in e for e in st['evidence'])


class TestDisagreement:
    """分歧检测：主从结构的核心契约"""

    def test_reversal_opportunity_detected(self):
        st = {'code': STAGE_ACCUMULATION, 'confidence': '中'}
        d = detect_disagreement('建议减仓', st)
        assert d and d['type'] == 'reversal_opportunity'

    def test_structural_risk_detected(self):
        st = {'code': STAGE_DISTRIBUTION, 'confidence': '强'}
        d = detect_disagreement('强烈推荐买入', st)
        assert d and d['type'] == 'structural_risk'

    def test_weak_confidence_no_disagreement(self):
        st = {'code': STAGE_ACCUMULATION, 'confidence': '低'}
        assert detect_disagreement('建议减仓', st) is None

    def test_consistent_no_disagreement(self):
        st = {'code': STAGE_DECLINE, 'confidence': '强'}
        assert detect_disagreement('建议减仓', st) is None
        st2 = {'code': STAGE_MARKUP_FULL, 'confidence': '强'}
        assert detect_disagreement('推荐买入', st2) is None


class TestPlaybookDisagreement:
    """分歧场景对策：永不输出与评级相反的指令（用户锁死的行为）"""

    def test_reversal_keeps_rating_as_master(self):
        from modules.trader_advisor import build_playbook
        st = {'code': STAGE_ACCUMULATION, 'name': '底部吸筹区', 'confidence': '中',
              'evidence': ['均线：MA5 领先/落后 MA20 -1.0%，MA20=13.31，MA60=13.55']}
        cap = {'summary': 'x', 'tone': 'mixed', 'details': [], 'blind_spots': []}
        dis = {'type': 'reversal_opportunity'}
        pb = build_playbook(st, cap, '建议减仓', 1000, 13.9, 13.0, dis)
        text = ' '.join(pb['actions'])
        assert '减仓仍是主指令' in text
        assert '不建议恐慌割肉' in text
        assert '加仓' not in text.replace('可小仓回补', '')  # 唯一例外是裁决信号里的回补
        assert any('转多确认' in w for w in pb['watch_signals'])
        assert any('防守线' in w for w in pb['watch_signals'])

    def test_structural_risk_downgrades_buy(self):
        from modules.trader_advisor import build_playbook
        st = {'code': STAGE_DISTRIBUTION, 'name': '顶部出货区', 'confidence': '强', 'evidence': []}
        cap = {'summary': 'x', 'tone': 'bearish', 'details': [], 'blind_spots': []}
        dis = {'type': 'structural_risk'}
        pb = build_playbook(st, cap, '强烈推荐买入', 1000, 10.0, 14.0, dis)
        text = ' '.join(pb['actions'])
        assert '冲高分批兑现' in text
        assert '评级动量仍强' in text

    def test_normal_stage_watch_signals_adaptive(self):
        from modules.trader_advisor import build_playbook
        st = {'code': STAGE_MARKUP_EARLY, 'name': '拉升初期', 'confidence': '中',
              'evidence': ['均线：MA5 领先/落后 MA20 +1.0%，MA20=21.00，MA60=20.50']}
        cap = {'summary': 'x', 'tone': 'bullish', 'details': [], 'blind_spots': []}
        pb = build_playbook(st, cap, '持有观望', 1000, 20.0, 21.5, None)  # close > ma20
        assert any('上修至主升期' in w for w in pb['watch_signals'])
        st2 = {'code': STAGE_RANGE, 'name': '震荡无趋势', 'confidence': '低',
               'evidence': ['均线：MA5 领先/落后 MA20 -2.0%，MA20=10.50，MA60=10.60']}
        pb2 = build_playbook(st2, cap, '持有观望', 0, None, 10.0, None)  # close < ma20
        assert any('站上 MA20' in w for w in pb2['watch_signals'])


class TestCapitalNarrative:
    """主力行为组合推断"""

    def test_divergence_flow_out_chips_in(self):
        class _D:
            main_net_inflow_5day = -614.0
            main_net_inflow = -200.0
            holder_count_change_pct = -12.0
            margin_balance_chg = 100.0
            institution_hold_ratio = None

        cap = capital_narrative(_D())
        assert cap['tone'] == 'mixed'
        assert '分歧' in cap['summary']
        assert any('收集' in d for d in cap['details'])
        assert cap['blind_spots']  # 盲区必须明示

    def test_bearish_flow_out_chips_out(self):
        class _D:
            main_net_inflow_5day = -800.0
            main_net_inflow = None
            holder_count_change_pct = 8.0
            margin_balance_chg = -100.0
            institution_hold_ratio = 5.0

        cap = capital_narrative(_D())
        assert cap['tone'] == 'bearish'


class TestWatchlistDerive:
    """看板零重算派生（portfolio._derive_trader_signal，照 obos_signal 先例）"""

    def test_derive_from_key_factors_json(self):
        from blueprints.portfolio import _derive_trader_signal
        kf = json.dumps({
            'kline': {'score': 50},
            'trader': {
                'stage_name': '底部吸筹区',
                'has_disagreement': True,
                'disagreement_text': '与评级「建议减仓」分歧：阶段特征更接近底部吸筹区',
            },
        }, ensure_ascii=False)
        sig = _derive_trader_signal(kf)
        assert sig['stage_name'] == '底部吸筹区'
        assert sig['has_disagreement'] is True
        assert '分歧' in sig['disagreement_text']

    def test_derive_none_when_absent_or_bad(self):
        from blueprints.portfolio import _derive_trader_signal
        assert _derive_trader_signal(None) is None
        assert _derive_trader_signal('{}') is None
        assert _derive_trader_signal('not-json') is None
        assert _derive_trader_signal(json.dumps({'kline': {'score': 50}})) is None


class TestEndToEnd:
    """端点全流程（真实库数据）"""

    def test_endpoint_available(self, client):
        sid = client._stock_id
        closes = [15 - i * 0.1 for i in range(35)] + [11.5] * 25
        _seed_kline(client, sid, closes)
        _seed_rating(client, sid, '建议减仓', 49.0)
        r = generate_trader_advice(sid)
        assert r['available']
        assert r['stage']['code'] in (
            STAGE_ACCUMULATION, STAGE_DECLINE, STAGE_RANGE, STAGE_MARKUP_EARLY,
            STAGE_MARKUP_FULL, STAGE_DISTRIBUTION)
        assert r['playbook']['watch_signals']
        assert '参考非指令' in r['disclaimer']

    def test_endpoint_404_without_data(self, client):
        resp = client.get(f'/api/stocks/{client._stock_id}/trader-advice')
        assert resp.status_code == 404

    def test_endpoint_200_with_data(self, client):
        sid = client._stock_id
        closes = [10 + i * 0.05 for i in range(60)]
        _seed_kline(client, sid, closes)
        _seed_rating(client, sid, '持有观望', 57.0)
        resp = client.get(f'/api/stocks/{sid}/trader-advice')
        assert resp.status_code == 200
        d = resp.get_json()
        assert d['success']
        assert d['stage']['name']
        assert d['capital']['summary']
