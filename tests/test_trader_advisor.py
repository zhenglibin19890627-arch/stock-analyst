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
    build_operations_matrix,
    capital_narrative,
    classify_stage,
    detect_disagreement,
    generate_trader_advice,
    signal_stage_linkage,
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


# ================================================================
# 021BQ 操作矩阵：双视角 / 评级主从 / 短线信号联动 / 多账户聚合
# ================================================================

def _mk_signals(buy_today=None, sell_today=None, buy_res=None, sell_res=None,
                upto='2026-09-18'):
    return {
        'kline_upto': upto,
        'buy_today': buy_today or [],
        'sell_today': sell_today or [],
        'buy_window': list(buy_today or []),
        'sell_window': list(sell_today or []),
        'buy_resonances': buy_res or [],
        'sell_resonances': sell_res or [],
    }


def _sell_hit(label='MACD水上死叉', key='macd_dead_above'):
    return {'signal': key, 'label': label, 'trigger_date': '2026-09-18', 'note': ''}


def _buy_hit(label='MACD水下金叉', key='macd_golden_below'):
    return {'signal': key, 'label': label, 'trigger_date': '2026-09-18', 'note': ''}


class _MkData:
    """build_operations_matrix 的最小 data（仅需 ma20）"""

    def __init__(self, ma20=20.0):
        self.ma20 = ma20


_STAGE_OK = {'code': STAGE_MARKUP_EARLY, 'name': '拉升初期', 'confidence': '中'}
_PA = {'stop_loss': 12.0, 'buy_low': 19.0, 'buy_high': 21.0}


class TestOperationsMatrix:
    """双视角操作矩阵纯函数（021BQ 增量键 operations）"""

    def test_view_and_dual_rows(self):
        ops = build_operations_matrix(_MkData(), _STAGE_OK, '持有观望',
                                      {'qty': 1000, 'cost': 20.0}, 21.0,
                                      _mk_signals(), _PA)
        assert ops['view'] == 'held'
        assert ops['holding']['qty'] == 1000
        assert ops['holding']['pnl_pct'] == 5.0
        assert ops['held_rows'] and ops['empty_rows']  # 双视角行同时输出
        assert ops['top_action'].startswith('止损·')   # 当前视角首行摘要
        # 空仓行对同一输入也可独立成立（构造无持仓输入验证）
        ops2 = build_operations_matrix(_MkData(), _STAGE_OK, '持有观望',
                                       {'qty': 0, 'cost': None}, 21.0,
                                       _mk_signals(), _PA)
        assert ops2['view'] == 'empty'

    def test_stop_level_takes_higher_of_two_sources(self):
        """止损位 = max(成本×0.92 纪律线, 价格建议止损)，双源标注取值口径"""
        ops = build_operations_matrix(_MkData(), _STAGE_OK, '持有观望',
                                      {'qty': 1000, 'cost': 13.9}, 14.0,
                                      _mk_signals(), _PA)
        stop_rows = [r for r in ops['held_rows'] if r['action'] == '止损']
        assert len(stop_rows) == 1
        # max(13.9×0.92=12.788→12.79, 12.0) = 12.79
        assert stop_rows[0]['level_value'] == 12.79
        assert stop_rows[0]['source'] == '纪律/价格建议取高者'

    def test_stop_level_pa_only_when_no_cost(self):
        ops = build_operations_matrix(_MkData(), _STAGE_OK, '持有观望',
                                      {'qty': 1000, 'cost': None}, 14.0,
                                      _mk_signals(), _PA)
        stop_rows = [r for r in ops['held_rows'] if r['action'] == '止损']
        assert stop_rows[0]['level_value'] == 12.0
        assert stop_rows[0]['source'] == '价格建议'

    def test_sell_signal_reduce_row_with_conflict_note(self):
        """卖点信号×买入档评级 → 减仓行引用具体信号，行内附调和注记（主从契约）"""
        ops = build_operations_matrix(_MkData(), _STAGE_OK, '推荐买入',
                                      {'qty': 1000, 'cost': 20.0}, 21.0,
                                      _mk_signals(sell_today=[_sell_hit()]), _PA)
        reduce_rows = [r for r in ops['held_rows'] if r['action'] == '减仓']
        assert len(reduce_rows) == 1
        assert 'MACD水上死叉' in reduce_rows[0]['trigger']
        assert '以评级为主' in reduce_rows[0]['note']
        # 联动解读同样显式标注相悖 + 减仓行引用 MA20 价位
        assert any('相悖' in line and '以评级为主' in line for line in ops['linkage'])
        assert any('MA20' in r['level'] for r in ops['held_rows'])

    def test_reduce_conditional_when_no_sell_signal(self):
        """无卖出信号 → 减仓行走条件触发式（若出现死叉/破位），不输出无条件指令"""
        ops = build_operations_matrix(_MkData(), _STAGE_OK, '推荐买入',
                                      {'qty': 1000, 'cost': 20.0}, 21.0,
                                      _mk_signals(), _PA)
        cond = [r for r in ops['held_rows'] if r['action'] == '减仓（条件）']
        assert len(cond) == 1
        assert cond[0]['trigger'].startswith('若')

    def test_empty_buy_trigger_with_rating_gating(self):
        """空仓视角：买点信号×买入档 → 买入触发行带价格建议区间；×减仓档 → 降级为观察并附调和注记"""
        ops = build_operations_matrix(_MkData(), _STAGE_OK, '推荐买入',
                                      {'qty': 0, 'cost': None}, 21.0,
                                      _mk_signals(buy_today=[_buy_hit()]), _PA)
        buy_rows = [r for r in ops['empty_rows'] if r['action'] == '买入触发']
        assert len(buy_rows) == 1
        assert 'MACD水下金叉' in buy_rows[0]['trigger']
        assert '买入区间 19.00~21.00' in buy_rows[0]['level']
        assert '支持' in buy_rows[0]['trigger']

        ops2 = build_operations_matrix(_MkData(), _STAGE_OK, '建议减仓',
                                       {'qty': 0, 'cost': None}, 21.0,
                                       _mk_signals(buy_today=[_buy_hit()]), _PA)
        watch_rows = [r for r in ops2['empty_rows'] if r['action'] == '试仓观察']
        assert len(watch_rows) == 1
        assert '仅观察不买入' in watch_rows[0]['trigger']
        assert '以评级为主' in watch_rows[0]['note']
        assert not [r for r in ops2['empty_rows'] if r['action'] == '买入触发']

    def test_baseline_rows_without_signals(self):
        """无任何信号 → 基线条件行仍然成立（止损/持有/观望/等待信号）"""
        ops = build_operations_matrix(_MkData(), _STAGE_OK, '持有观望',
                                      {'qty': 1000, 'cost': 20.0}, 21.0,
                                      _mk_signals(), _PA)
        actions = [r['action'] for r in ops['held_rows']]
        assert '止损' in actions and '持有' in actions and '减仓（条件）' in actions
        assert ops['signals_today'] == []
        assert ops['linkage'] == []
        e_actions = [r['action'] for r in ops['empty_rows']]
        assert '观望' in e_actions and '等待信号' in e_actions

    def test_linkage_stage_x_signal_semantics(self):
        """联动解读语义锚点：弱势+买点=超跌反弹不接飞刀；上升+卖点=趋势内回调"""
        link_down = signal_stage_linkage(STAGE_DECLINE, '建议减仓',
                                         _mk_signals(buy_today=[_buy_hit()]))
        assert any('超跌反弹' in line and '不接飞刀' in line for line in link_down)
        link_up = signal_stage_linkage(STAGE_MARKUP_FULL, '推荐买入',
                                       _mk_signals(sell_today=[_sell_hit()]))
        assert any('回调警示' in line for line in link_up)
        link_acc = signal_stage_linkage(STAGE_ACCUMULATION, '持有观望',
                                        _mk_signals(buy_today=[_buy_hit(label='KDJ低位金叉', key='kdj_golden_low')]))
        assert any('启动前兆' in line for line in link_acc)

    def test_sell_resonance_raises_reduce_priority(self):
        """窗口内 bear 共振 ≥4 星（非当日）→ 减仓/回避行同样成立"""
        res = [{'key': 'res_week_bear', 'label': '周线空头波段卖', 'stars': 5,
                'kind': 'bear', 'note': '', 'signals': ''}]
        ops = build_operations_matrix(_MkData(), _STAGE_OK, '持有观望',
                                      {'qty': 1000, 'cost': 20.0}, 21.0,
                                      _mk_signals(sell_res=res), _PA)
        assert any(r['action'] == '减仓' for r in ops['held_rows'])
        assert any(r['action'] == '回避' for r in ops['empty_rows'])
        assert '周线空头波段卖' in ops['held_rows'][-2]['trigger']

    def test_no_bare_lt_in_matrix(self):
        import json as _json
        ops = build_operations_matrix(_MkData(), _STAGE_OK, '推荐买入',
                                      {'qty': 1000, 'cost': 20.0}, 21.0,
                                      _mk_signals(sell_today=[_sell_hit()],
                                                  buy_today=[_buy_hit()]), _PA)
        assert '<' not in _json.dumps(ops, ensure_ascii=False)


class TestOperationsEndToEnd:
    """generate_trader_advice 端到端（临时库造K线/持仓/日报）"""

    def _seed_calibrated_kline(self, client, stock_id, closes):
        """021BQ 校准 OHLC（open=0.99c/high=1.01c/low=0.98c）——与卖侧信号
        校准几何一致（ramp+加速阳+大阴 → 双死叉落最新一根）"""
        import datetime as dt
        d0 = dt.date(2026, 6, 1)
        conn = db_manager.get_connection()
        try:
            for i, cl in enumerate(closes):
                d = (d0 + dt.timedelta(days=i)).isoformat()
                conn.execute(
                    "INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (stock_id, d, cl * 0.99, cl, cl * 1.01, cl * 0.98, 1000))
            conn.commit()
        finally:
            conn.close()

    def test_operations_incremental_key(self, client):
        """operations 为纯增量键：既有键（stage/capital/playbook/disagreement/
        rating/total_score/disclaimer）结构零改动"""
        sid = client._stock_id
        _seed_kline(client, sid, [10 + i * 0.05 for i in range(60)])
        _seed_rating(client, sid, '持有观望', 57.0)
        r = generate_trader_advice(sid)
        assert r['available']
        for key in ('stage', 'capital', 'playbook', 'disagreement',
                    'rating', 'total_score', 'disclaimer'):
            assert key in r
        ops = r['operations']
        assert ops['view'] in ('held', 'empty')
        assert isinstance(ops['held_rows'], list) and isinstance(ops['empty_rows'], list)
        assert 'signals_today' in ops and 'linkage' in ops

    def test_multi_account_holding_aggregated(self, client):
        """多账户同股分仓 → 数量汇总 + 加权平均成本（修复 ORDER BY id LIMIT 1 单行取缺口）"""
        sid = client._stock_id
        _seed_kline(client, sid, [10 + i * 0.05 for i in range(60)])
        _seed_rating(client, sid, '持有观望', 57.0)
        conn = db_manager.get_connection()
        try:
            conn.execute("INSERT INTO accounts (name) VALUES ('账户A')")
            conn.execute("INSERT INTO accounts (name) VALUES ('账户B')")
            # 账户A 600股@10 + 账户B 400股@20 → 汇总 1000股 加权成本 14.0
            # （若仍是旧的单行取 ORDER BY id LIMIT 1，只会读到 600@10）
            conn.execute(
                'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
                'VALUES (1, ?, 10.0, 600)', (sid,))
            conn.execute(
                'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
                'VALUES (2, ?, 20.0, 400)', (sid,))
            conn.commit()
        finally:
            conn.close()

        r = generate_trader_advice(sid)
        assert r['available']
        assert r['playbook']['profile'].startswith('持仓 1,000 股 · 成本 14.00')
        ops = r['operations']
        assert ops['view'] == 'held'
        assert ops['holding']['qty'] == 1000
        assert ops['holding']['cost'] == 14.0
        # 止损位 = 成本×0.92 = 12.88（加权成本口径）
        stop_rows = [row for row in ops['held_rows'] if row['action'] == '止损']
        assert stop_rows and stop_rows[0]['level_value'] == 12.88

    def test_sell_signal_fused_into_operations(self, client):
        """端到端：死叉形态K线 → operations.signals_today 出现卖侧事件 +
        联动解读 + 买入档评级相悖调和（信号层真实融合，非装饰键）"""
        sid = client._stock_id
        closes = [60 + i * 2.0 for i in range(44)] + [156.0, 138.0]
        self._seed_calibrated_kline(client, sid, closes)
        _seed_rating(client, sid, '推荐买入', 70.0)

        r = generate_trader_advice(sid)
        assert r['available']
        ops = r['operations']
        sell_sigs = [s for s in ops['signals_today'] if s['side'] == 'sell']
        assert {s['signal'] for s in sell_sigs} == {'macd_dead_above', 'kdj_dead_high'}
        # 同日触发（校准形态：双死叉落最新一根K线）
        assert len({s['trigger_date'] for s in sell_sigs}) == 1
        assert any('卖出信号与评级「推荐买入」相悖' in line for line in ops['linkage'])
        assert any('以评级为主' in line for line in ops['linkage'])
        # 空仓视角同步给出回避动作
        assert any(row['action'] == '回避' for row in ops['empty_rows'])

    def test_derive_trader_signal_top_action_passthrough(self, client):
        """看板派生增量键：key_factors.trader.top_action 透传（旧键零改动）"""
        from blueprints.portfolio import _derive_trader_signal
        kf = json.dumps({
            'kline': {'score': 50},
            'trader': {
                'stage_name': '主升期',
                'has_disagreement': False,
                'disagreement_text': None,
                'top_action': '止损·12.88',
            },
        }, ensure_ascii=False)
        sig = _derive_trader_signal(kf)
        assert sig['stage_name'] == '主升期'
        assert sig['has_disagreement'] is False
        assert sig['top_action'] == '止损·12.88'


# ================================================================
# 021BR 分域不对称层级：纪律最高 / 减仓操盘手独立触发 / 买入评级门控
#   + 止损状态置顶 + 共振时效（中免实测形态回归，测试锁死）
# ================================================================

_RES_BUY = [{'key': 'res_week_daily', 'label': '周线共振波段', 'stars': 5,
             'kind': 'bull', 'note': '', 'signals': 'KDJ低位金叉@2026-09-21'}]

_STAGE_DECLINE = {'code': STAGE_DECLINE, 'name': '下跌期', 'confidence': '强'}


class TestAsymmetricHierarchy021BR:
    """层级契约①②③ + 状态置顶 + 共振时效（用户拍板，测试锁死）"""

    def _zhongmian_ops(self):
        """中免 2026-09-22 实测形态：现价 52.27 低于止损线 56.16（成本 57.53），
        MA20 52.78 已破，同屏买点共振 5 星（触发日 09-21 = 窗口内历史信号）"""
        return build_operations_matrix(
            _MkData(ma20=52.78), _STAGE_DECLINE, '持有观望',
            {'qty': 2100, 'cost': 57.53}, 52.27,
            _mk_signals(buy_res=_RES_BUY, upto='2026-09-22'),
            {'stop_loss': 56.16}, kline_tail=[
                ('2026-09-18', 55.90), ('2026-09-21', 53.10), ('2026-09-22', 52.27)])

    def test_zhongmian_no_hold_when_stop_triggered(self):
        """契约①回归：止损已触发 + 共振5星同现 → 无「持有」行、
        linkage 无「持仓者持有」、共振行改写为反抽减仓参考并带历史触发日期"""
        ops = self._zhongmian_ops()
        assert not [r for r in ops['held_rows'] if r['action'] == '持有']
        assert all('持仓者持有' not in line for line in ops['linkage'])
        assert any('止损纪律已触发' in line and '反抽减仓参考' in line
                   for line in ops['linkage'])
        # 共振时效：窗口内历史触发必须带日期且标注非今日，不得读起来像新信号
        assert any('2026-09-21' in line and '非今日' in line for line in ops['linkage'])

    def test_zhongmian_stop_status_pinned_top(self):
        """契约③状态置顶：ops.status 显式止损已触发（现价/触发线/触发日期）；
        止损行 status='triggered' 且附现价；top_action 带「已触发」"""
        ops = self._zhongmian_ops()
        st = ops['status']
        assert st and st['kind'] == 'stop_triggered'
        assert st['close'] == 52.27 and st['stop_line'] == 56.16
        assert st['trigger_date'] == '2026-09-18'  # 当前破位段首日
        assert st['ma20_broken'] is True
        stop_rows = [r for r in ops['held_rows'] if r['action'] == '止损']
        assert stop_rows[0]['status'] == 'triggered'
        assert '现价 52.27' in stop_rows[0]['trigger']
        assert ops['top_action'].startswith('止损·56.16')
        assert '已触发' in ops['top_action']

    def test_layer_annotations_on_all_rows(self):
        """契约②标注面：每行带 layer（纪律/战术/战略）+ 分域头部话术；
        止损=纪律、减仓检查=战术、仓位纪律=战略"""
        ops = self._zhongmian_ops()
        for r in ops['held_rows'] + ops['empty_rows']:
            assert r['layer'] in ('纪律', '战术', '战略'), r
        by_action = {r['action']: r for r in ops['held_rows']}
        assert by_action['止损']['layer'] == '纪律'
        assert by_action['减仓检查']['layer'] == '战术'
        assert by_action['仓位纪律']['layer'] == '战略'
        assert ops['hierarchy_note'] == '纪律无条件执行 · 减仓听操盘手 · 加仓看评级'

    def test_breakdown_independent_of_rating_lag(self):
        """契约②：强下跌（卖点+破位）× 评级滞后（持有观望）→ 确定性减仓行 +
        「操盘手纪律触发，评级尚未跟上（当前评级 持有观望）」；
        全矩阵不再出现「按评级执行风控」式推给评级"""
        ops = build_operations_matrix(
            _MkData(ma20=52.78), _STAGE_DECLINE, '持有观望',
            {'qty': 1000, 'cost': 57.53}, 52.27,
            _mk_signals(sell_today=[_sell_hit()], upto='2026-09-22'),
            {'stop_loss': 56.16})
        reduce_rows = [r for r in ops['held_rows'] if r['action'] == '减仓']
        assert reduce_rows and reduce_rows[0]['note']
        assert '操盘手纪律触发' in reduce_rows[0]['note']
        assert '评级尚未跟上（当前评级 持有观望）' in reduce_rows[0]['note']
        check_rows = [r for r in ops['held_rows'] if r['action'] == '减仓检查']
        assert check_rows and '操盘手破位纪律触发' in check_rows[0]['trigger']
        assert '按评级执行风控' not in json.dumps(ops, ensure_ascii=False)

    def test_weak_rating_aligned_note(self):
        """契约②补齐：减仓档评级 × 卖点信号 → 同向注记（非推诿非滞后）"""
        ops = build_operations_matrix(
            _MkData(), _STAGE_OK, '建议减仓', {'qty': 1000, 'cost': 20.0}, 19.0,
            _mk_signals(sell_today=[_sell_hit()]), _PA)
        reduce_rows = [r for r in ops['held_rows'] if r['action'] == '减仓']
        assert reduce_rows[0]['note'] and '同向' in reduce_rows[0]['note']

    def test_buy_domain_still_rating_gated(self):
        """契约③：买入域仍评级门控——减仓档评级 × 今日买点 → 试仓观察
        （绝不输出买入触发），行层级=战略"""
        ops = build_operations_matrix(
            _MkData(), _STAGE_OK, '建议减仓', {'qty': 0, 'cost': None}, 21.0,
            _mk_signals(buy_today=[_buy_hit()]), _PA)
        assert not [r for r in ops['empty_rows'] if '买入触发' in r['action']]
        watch = [r for r in ops['empty_rows'] if r['action'] == '试仓观察']
        assert watch and watch[0]['layer'] == '战略'
        assert '仅观察不买入' in watch[0]['trigger']

    def test_resonance_normal_wording_when_stop_not_triggered(self):
        """止损未触发 + 共振5星 → 共振行保持「成立」措辞（带日期时效）"""
        ops = build_operations_matrix(
            _MkData(ma20=20.0), _STAGE_OK, '持有观望',
            {'qty': 1000, 'cost': 20.0}, 21.0,
            _mk_signals(buy_res=_RES_BUY, upto='2026-09-22'), _PA)
        assert any('共振成立' in line and '2026-09-21' in line for line in ops['linkage'])
        assert not any('止损纪律已触发' in line for line in ops['linkage'])

    def test_linkage_direct_stop_gate(self):
        """signal_stage_linkage 直调门控：close 低于 stop_level → 共振行改写"""
        lines = signal_stage_linkage(STAGE_DECLINE, '持有观望',
                                     _mk_signals(buy_res=_RES_BUY, upto='2026-09-22'),
                                     close=52.27, stop_level=56.16)
        assert any('止损纪律已触发' in line and '反抽减仓参考' in line for line in lines)
        assert all('持仓者持有' not in line for line in lines)

    def test_weak_stage_resonance_no_hold(self):
        """汤臣形态回归（诊断 A9）：下跌期 + 今日买点 + 共振 → 共振行与买点行
        同语义（反抽减仓），不再互斥出现「反抽减仓」与「持仓者持有」"""
        lines = signal_stage_linkage(STAGE_DECLINE, '持有观望',
                                     _mk_signals(buy_today=[_buy_hit()],
                                                 buy_res=_RES_BUY, upto='2026-09-22'))
        assert any('反抽减仓' in line for line in lines)
        assert all('持仓者持有' not in line for line in lines)

    def test_hold_row_survives_when_stop_not_triggered(self):
        """纪律未触发 + MA20 上方 → 「持有」行保留（层级压制只在纪律触发时生效）；
        状态行为 None"""
        ops = build_operations_matrix(
            _MkData(ma20=20.0), _STAGE_OK, '持有观望',
            {'qty': 1000, 'cost': 20.0}, 21.0, _mk_signals(), _PA)
        assert any(r['action'] == '持有' for r in ops['held_rows'])
        assert ops['status'] is None

    def test_no_bare_lt_in_hierarchy_texts(self):
        """021BN 延续：层级/状态新文案同样不得引入裸 <"""
        ops = self._zhongmian_ops()
        assert '<' not in json.dumps(ops, ensure_ascii=False)

    def test_zhongmian_end_to_end(self, client):
        """端到端（临时库）：止损线失守 → operations.status 置顶 + 触发日期回填 +
        无「持有」行 + top_action 带已触发（/trader-advice 端点与看板链路同源）"""
        import datetime as dt
        sid = client._stock_id
        closes = [60.0] * 58 + [55.5, 52.27]  # 最后两根失守 56.16
        d0 = dt.date(2026, 6, 20)
        conn = db_manager.get_connection()
        try:
            conn.execute("INSERT INTO accounts (name) VALUES ('测试账户')")
            for i, cl in enumerate(closes):
                d = (d0 + dt.timedelta(days=i)).isoformat()
                conn.execute(
                    "INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (sid, d, cl * 0.99, cl, cl * 1.01, cl * 0.98, 1000))
            conn.execute(
                "INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name, "
                "engine_version, total_score, rating, key_factors, price_advice, status, report_type) "
                "VALUES ('2026-08-18', ?, '002230', '测试股', 'v5', 55.0, '持有观望', '{}', ?, 'ok', 'daily')",
                (sid, json.dumps({'stop_loss': 56.16})))
            conn.execute(
                'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
                'VALUES (1, ?, 57.53, 2100)', (sid,))
            conn.commit()
        finally:
            conn.close()
        r = generate_trader_advice(sid)
        assert r['available']
        ops = r['operations']
        st = ops['status']
        assert st and st['kind'] == 'stop_triggered'
        assert st['stop_line'] == 56.16
        assert st['trigger_date'] == '2026-08-17'  # 倒数第2根（d0+58）= 破位段首日
        assert not [row for row in ops['held_rows'] if row['action'] == '持有']
        assert '已触发' in ops['top_action']
        assert ops['hierarchy_note'] == '纪律无条件执行 · 减仓听操盘手 · 加仓看评级'


class TestStageLeadDisagreement021BR:
    """分歧检测补档（诊断 §3.5）：持有观望 × 强置信弱势阶段 → stage_leads_rating"""

    def test_neutral_rating_strong_decline_flagged(self):
        d = detect_disagreement('持有观望', {'code': STAGE_DECLINE, 'confidence': '强'})
        assert d and d['type'] == 'stage_leads_rating'
        d2 = detect_disagreement('持有观望', {'code': STAGE_DISTRIBUTION, 'confidence': '强'})
        assert d2 and d2['type'] == 'stage_leads_rating'

    def test_mid_or_weak_confidence_not_flagged(self):
        assert detect_disagreement('持有观望', {'code': STAGE_DECLINE, 'confidence': '中'}) is None
        assert detect_disagreement('持有观望', {'code': STAGE_DECLINE, 'confidence': '低'}) is None

    def test_neutral_rating_bull_stage_not_flagged(self):
        assert detect_disagreement('持有观望', {'code': STAGE_MARKUP_FULL, 'confidence': '强'}) is None

    def test_playbook_stage_lead_branch(self):
        from modules.trader_advisor import build_playbook
        stg = {'code': STAGE_DECLINE, 'name': '下跌期', 'confidence': '强', 'evidence': []}
        cap = {'summary': 'x', 'tone': 'bearish', 'details': [], 'blind_spots': []}
        pb = build_playbook(stg, cap, '持有观望', 1000, 57.5, 52.3,
                            {'type': 'stage_leads_rating'})
        text = ' '.join(pb['actions'])
        assert '阶段领先于评级' in text
        assert '评级尚未跟上' in text
        assert '加仓继续看评级' in text
        assert any('纪律优先' in w for w in pb['watch_signals'])


class TestT3Consistency021BR:
    """021BR t3：分仓明细口径可核对 + 现价源统一 + price_advice_override 时序"""

    def test_multi_account_breakdown_row(self):
        """矩阵持仓行附各账户分仓明细（用户可对账券商 App）+ 成本差异大提示；
        operations.holding.accounts 逐账户透出"""
        ops = build_operations_matrix(
            _MkData(ma20=20.0), _STAGE_OK, '持有观望',
            {'qty': 1000, 'cost': 14.0,
             'accounts': [{'account_id': 1, 'name': '账户A', 'qty': 600, 'cost': 10.0},
                          {'account_id': 2, 'name': '账户B', 'qty': 400, 'cost': 20.0}]},
            15.0, _mk_signals(), _PA)
        row = [r for r in ops['held_rows'] if r['action'] == '仓位纪律'][0]
        assert '账户A 600@10.00 + 账户B 400@20.00' in row['trigger']
        assert '成本为加权摊薄口径' in row['trigger']
        assert '止损线以各账户实际成本为准' in row['trigger']
        assert len(ops['holding']['accounts']) == 2

    def test_single_account_no_breakdown(self):
        """单账户（或无 accounts 输入）→ 不加分仓明细与差异提示（口径提示只在多仓时出现）"""
        ops = build_operations_matrix(
            _MkData(), _STAGE_OK, '持有观望',
            {'qty': 1000, 'cost': 20.0,
             'accounts': [{'account_id': 1, 'name': '账户A', 'qty': 1000, 'cost': 20.0}]},
            21.0, _mk_signals(), _PA)
        row = [r for r in ops['held_rows'] if r['action'] == '仓位纪律'][0]
        assert '成本为加权摊薄口径' not in row['trigger']
        assert '止损线以各账户实际成本为准' not in row['trigger']

    def test_price_source_same_as_trigger(self):
        """现价源标注与止损触发判定同一价格源（close 一致 + 日期=最新K线日）"""
        tail = [('2026-09-18', 55.9), ('2026-09-21', 53.1), ('2026-09-22', 52.27)]
        ops = build_operations_matrix(
            _MkData(ma20=52.78), _STAGE_DECLINE, '持有观望',
            {'qty': 2100, 'cost': 57.53}, 52.27,
            _mk_signals(upto='2026-09-22'), {'stop_loss': 56.16}, kline_tail=tail)
        ps = ops['price_source']
        assert ps['close'] == 52.27
        assert ps['date'] == '2026-09-22'
        assert '日K收盘' in ps['desc']
        st = ops['status']
        assert st and st['close'] == ps['close']  # 触发判定与展示现价同源
        row = [r for r in ops['held_rows'] if r['action'] == '仓位纪律'][0]
        assert '触发判定同源' in row['trigger']

    def test_price_advice_override_threading(self, client):
        """端到端：日报 price_advice=NULL 行不再拖累价位层——override 传入即生效
        （时序缺陷修正：key_factors.trader.top_action 与报告行 price_advice 同源）"""
        sid = client._stock_id
        _seed_kline(client, sid, [10 + i * 0.05 for i in range(60)])
        _seed_rating(client, sid, '持有观望', 57.0)  # price_advice 列 NULL
        conn = db_manager.get_connection()
        try:
            conn.execute("INSERT INTO accounts (name) VALUES ('账户A')")
            conn.execute("INSERT INTO accounts (name) VALUES ('账户B')")
            conn.execute(
                'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
                'VALUES (1, ?, 10.0, 600)', (sid,))
            conn.execute(
                'INSERT INTO holdings (account_id, stock_id, cost_price, quantity) '
                'VALUES (2, ?, 20.0, 400)', (sid,))
            conn.commit()
        finally:
            conn.close()
        # 无 override（旧路径）：读库 price_advice=NULL → 止损仅纪律线 12.88
        r0 = generate_trader_advice(sid)
        stop0 = [r for r in r0['operations']['held_rows'] if r['action'] == '止损'][0]
        assert stop0['level_value'] == 12.88
        # 有 override：当日已算好的价格建议止损 13.5 → 双源取高者 13.5
        r1 = generate_trader_advice(sid, price_advice_override={'stop_loss': 13.5})
        stop1 = [r for r in r1['operations']['held_rows'] if r['action'] == '止损'][0]
        assert stop1['level_value'] == 13.5
        assert stop1['source'] == '纪律/价格建议取高者'
        # 分仓明细端到端透出
        assert len(r1['operations']['holding']['accounts']) == 2
