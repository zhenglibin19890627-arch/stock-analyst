"""
021BS R2 N01 看板 trader 摘要读取面 live 兜底 + N03 卡头文案统一——单元/集成测试。

锁定契约（t4 修复，captain 选型①）：
- trader_advisor.derive_trader_signal_summary：stored 优先透出（零现算）；
  stored 缺失（019A 刷新覆盖丢失形态）且提供 stock_id 时 live 现算兜底；
  兜底失败静默回 None；无 stock_id 不兜底（离线调用面行为不变）
- 看板面（watchlist-scores trader_signal / 行动清单 overview）：中免形态回归——
  stored 缺 trader + live 止损已触发 → 看板仍显示止损状态（不沉默）
- build_action_list 纯函数默认关闭兜底（不触库，既有测试行为不变）
- analysis.js 操盘手卡头副词与 021BR 分域表述统一（旧文案移除）
"""

import json

import pytest

import app as app_module
from database import db_manager
from modules.action_list import build_action_list
from modules.trader_advisor import derive_trader_signal_summary

# ---------------- 合成数据 ----------------

_STORED_KF = json.dumps({
    'kline': {'score': 50.0},
    'trader': {'stage_name': '主升期', 'has_disagreement': False,
               'disagreement_text': None, 'top_action': '持有（浮盈中）'},
})

# 中免形态（021BS R2 实锤）：live 止损已触发 + 分歧在场，stored 摘要被刷新覆盖丢失
_LIVE_ZHONGMIAN = {
    'available': True,
    'stage': {'code': 'distribution', 'name': '顶部出货区', 'confidence': '强'},
    'disagreement': {'type': 'stage_leads_rating', 'text': '阶段领先于评级'},
    'operations': {'top_action': '止损·56.16（已触发）',
                   'status': {'kind': 'stop_triggered', 'close': 52.27, 'stop_line': 56.16}},
}


@pytest.fixture
def _no_live(monkeypatch):
    """哨兵：live 现算被调用即失败（验证 stored 优先 / 默认不兜底）"""
    def _boom(*_a, **_k):
        raise AssertionError('live generate_trader_advice 不应被调用')
    monkeypatch.setattr('modules.trader_advisor.generate_trader_advice', _boom)


class TestDeriveTraderSignalSummary:
    def test_stored_present_passthrough_without_live(self, _no_live):
        sig = derive_trader_signal_summary(_STORED_KF, stock_id=1)
        assert sig['stage_name'] == '主升期'
        assert sig['top_action'] == '持有（浮盈中）'
        assert sig['has_disagreement'] is False

    def test_stored_missing_falls_back_to_live(self, monkeypatch):
        monkeypatch.setattr(
            'modules.trader_advisor.generate_trader_advice',
            lambda sid, **k: dict(_LIVE_ZHONGMIAN))
        sig = derive_trader_signal_summary('{"kline": {"score": 50}}', stock_id=7)
        # 中免形态回归：stored 缺 trader + live 止损已触发 → 摘要携带止损状态
        assert sig['top_action'] == '止损·56.16（已触发）'
        assert sig['stage_name'] == '顶部出货区'
        assert sig['has_disagreement'] is True
        assert sig['disagreement_text'] == '阶段领先于评级'

    def test_stored_invalid_json_falls_back(self, monkeypatch):
        monkeypatch.setattr(
            'modules.trader_advisor.generate_trader_advice',
            lambda sid, **k: dict(_LIVE_ZHONGMIAN))
        sig = derive_trader_signal_summary('not-json', stock_id=7)
        assert sig['top_action'] == '止损·56.16（已触发）'

    def test_live_unavailable_returns_none(self, monkeypatch):
        monkeypatch.setattr(
            'modules.trader_advisor.generate_trader_advice',
            lambda sid, **k: {'available': False, 'reason': '数据不足'})
        assert derive_trader_signal_summary('{}', stock_id=7) is None

    def test_live_raises_returns_none(self, monkeypatch):
        def _boom(_sid, **_k):
            raise RuntimeError('db busy')
        monkeypatch.setattr('modules.trader_advisor.generate_trader_advice', _boom)
        assert derive_trader_signal_summary('{}', stock_id=7) is None

    def test_no_stock_id_never_falls_back(self, _no_live):
        assert derive_trader_signal_summary('{}') is None
        assert derive_trader_signal_summary(None) is None
        assert derive_trader_signal_summary('{}', stock_id=None) is None


# ---------------- 端点面：watchlist-scores / 行动清单 ----------------

@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test_fallback.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    app_module.app.config['TESTING'] = True
    c = app_module.app.test_client()
    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('601888', 'a_stock', '中国中免')")
        conn.commit()
        cur = conn.cursor()
        cur.execute("SELECT id FROM stocks WHERE symbol='601888'")
        c._sid = cur.fetchone()['id']
    finally:
        conn.close()
    return c


def _seed_report(stock_id, key_factors):
    from datetime import datetime

    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name, "
            "engine_version, total_score, rating, key_factors, status, report_type, "
            "generated_at) VALUES (date('now', 'localtime'), ?, '601888', '中国中免', "
            "'v5', 56.4, '持有观望', ?, 'ok', 'daily', ?)",
            (stock_id, key_factors, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


class TestWatchlistScoresFallback:
    def test_missing_stored_shows_live_stop_status(self, client, monkeypatch):
        """中免形态回归：stored 缺 trader + live 止损已触发 → 看板 chip 不沉默"""
        _seed_report(client._sid, '{"kline": {"score": 50.0}}')
        monkeypatch.setattr(
            'modules.trader_advisor.generate_trader_advice',
            lambda sid, **k: dict(_LIVE_ZHONGMIAN))
        resp = client.get('/api/portfolio/watchlist-scores')
        assert resp.status_code == 200
        row = next(s for s in resp.get_json()['stocks'] if s['id'] == client._sid)
        assert row['trader_signal']['top_action'] == '止损·56.16（已触发）'
        assert row['trader_signal']['has_disagreement'] is True

    def test_stored_present_uses_stored_no_live(self, client, monkeypatch):
        """stored 在场 → 原样透出，live 不被调用（零重复现算）"""
        _seed_report(client._sid, _STORED_KF)

        def _boom(*_a, **_k):
            raise AssertionError('stored 在场时不应 live 现算')
        monkeypatch.setattr('modules.trader_advisor.generate_trader_advice', _boom)
        resp = client.get('/api/portfolio/watchlist-scores')
        row = next(s for s in resp.get_json()['stocks'] if s['id'] == client._sid)
        assert row['trader_signal']['top_action'] == '持有（浮盈中）'
        assert row['trader_signal']['stage_name'] == '主升期'

    def test_no_report_row_gets_no_chip(self, client, monkeypatch):
        """无报告股不兜底（看板不为从未分析的股票凭空造 chip）"""
        monkeypatch.setattr(
            'modules.trader_advisor.generate_trader_advice',
            lambda sid, **k: dict(_LIVE_ZHONGMIAN))
        resp = client.get('/api/portfolio/watchlist-scores')
        row = next(s for s in resp.get_json()['stocks'] if s['id'] == client._sid)
        assert row['trader_signal'] is None

    def test_live_insufficient_degrades_silently(self, client, monkeypatch):
        """兜底失败（数据不足）静默回 None，不阻塞看板（现状退化）"""
        _seed_report(client._sid, '{"kline": {"score": 50.0}}')
        monkeypatch.setattr(
            'modules.trader_advisor.generate_trader_advice',
            lambda sid, **k: {'available': False, 'reason': '数据不足'})
        resp = client.get('/api/portfolio/watchlist-scores')
        assert resp.status_code == 200
        row = next(s for s in resp.get_json()['stocks'] if s['id'] == client._sid)
        assert row['trader_signal'] is None


class TestActionListFallback:
    @staticmethod
    def _row(stock_id, key_factors, rn=1):
        return {'report_date': '2026-09-23', 'stock_id': stock_id,
                'stock_code': '601888', 'stock_name': '中国中免', 'total_score': 56.4,
                'rating': '持有观望', 'rating_label': '持有观望', 'score_change': None,
                'status': 'ok', 'error_msg': None, 'key_factors': key_factors, 'rn': rn}

    def test_fallback_enabled_live_stop_visible(self, monkeypatch):
        """看板端点路径（live_trader_fallback=True）：stored 缺失 → live 止损状态透出"""
        monkeypatch.setattr(
            'modules.trader_advisor.derive_trader_signal_summary',
            lambda kf, stock_id=None: {
                'stage_name': '顶部出货区', 'has_disagreement': True,
                'disagreement_text': '阶段领先于评级', 'top_action': '止损·56.16（已触发）'})
        result = build_action_list(
            '2026-09-23', [{'stock_id': 1, 'symbol': '601888', 'name': '中国中免'}],
            [self._row(1, '{"kline": {"score": 50.0}}')], [], None,
            live_trader_fallback=True)
        ov = result['overview'][0]
        assert ov['trader_stage'] == '顶部出货区'
        assert ov['has_disagreement'] is True

    def test_default_off_never_touches_live(self, monkeypatch):
        """纯函数默认关闭兜底：stored 缺失 → 现状（None），live 不被触库"""
        monkeypatch.setattr(
            'modules.trader_advisor.derive_trader_signal_summary',
            lambda kf, stock_id=None: (_ for _ in ()).throw(
                AssertionError('默认关闭时不应兜底')))
        result = build_action_list(
            '2026-09-23', [{'stock_id': 1, 'symbol': '601888', 'name': '中国中免'}],
            [self._row(1, '{"kline": {"score": 50.0}}')], [], None)
        assert result['overview'][0]['trader_stage'] is None
        assert result['overview'][0]['has_disagreement'] is False

    def test_stored_preferred_when_fallback_enabled(self, monkeypatch):
        """兜底开启但 stored 在场 → stored 优先（不重复现算）"""
        monkeypatch.setattr(
            'modules.trader_advisor.derive_trader_signal_summary',
            lambda kf, stock_id=None: (_ for _ in ()).throw(
                AssertionError('stored 在场时不应兜底')))
        result = build_action_list(
            '2026-09-23', [{'stock_id': 1, 'symbol': '601888', 'name': '中国中免'}],
            [self._row(1, _STORED_KF)], [], None, live_trader_fallback=True)
        assert result['overview'][0]['trader_stage'] == '主升期'
        assert result['overview'][0]['has_disagreement'] is False

    def test_fallback_feeds_rating_move_reason(self, monkeypatch):
        """评级变动项 reason 附操盘手分歧提示（兜底摘要同样进入 _rating_move_item）"""
        monkeypatch.setattr(
            'modules.trader_advisor.derive_trader_signal_summary',
            lambda kf, stock_id=None: {
                'stage_name': '顶部出货区', 'has_disagreement': True,
                'disagreement_text': '阶段领先于评级', 'top_action': 'x'})
        rows = [self._row(1, '{}', rn=1),
                dict(self._row(1, '{}', rn=2), report_date='2026-09-20',
                     rating='推荐买入', rating_label='推荐买入')]
        result = build_action_list(
            '2026-09-23', [{'stock_id': 1, 'symbol': '601888', 'name': '中国中免'}],
            rows, [], None, live_trader_fallback=True)
        move = next(it for it in result['items'] if it['kind'] == 'rating_downgrade')
        assert '操盘手提示：阶段领先于评级' in move['reason']


class TestN03CardHeaderCopy:
    """021BS R2 N03：卡头副词与 021BR 分域契约统一（纯前端一行）"""

    @staticmethod
    def _js():
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'static', 'js', 'analysis.js')
        with open(path, encoding='utf-8') as fh:
            return fh.read()

    def test_old_copy_removed(self):
        assert '仓位动作以评级为准' not in self._js()

    def test_new_copy_unified_with_matrix_subtitle(self):
        js = self._js()
        assert '纪律无条件执行 · 减仓听操盘手 · 加仓看评级' in js
        # 卡头与 ④ 操作矩阵副词同源（至少两处分域表述，同屏一致不再互斥）
        assert js.count('纪律无条件执行 · 减仓听操盘手 · 加仓看评级') >= 2
