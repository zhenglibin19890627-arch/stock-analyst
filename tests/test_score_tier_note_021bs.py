"""
021BS P1-1 分数×档位失配持续标注——读取面/看板面集成测试。

锁定契约：
- 看板 watchlist-scores 每股透出 score_tier_note（存量报告由读取面按同源
  纯函数现算补齐；一致为 None）——审计脚本的纯 GET 可观测标注面
- 报告页 report-latest 快照路径：key_factors 落库注记优先，缺失则现算补齐；
  key_factors 非维度键（trader 摘要/score_tier_note）不得混入 dimensions、
  不得污染最强/最弱维度、字符串值不得使快照路径崩溃
- blueprints.analysis._attach_score_tier_note：实时路径统一附加注记

隔离临时库：直接构造 stocks/daily_reports 行，不触网、不触发实时重评
（报告行 generated_at 取当前时刻，快照保持新鲜以绕开 021K 15 分钟重评写库）。
"""

from datetime import datetime

import pytest

import app as app_module
from database import db_manager


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test_stn.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    app_module.app.config['TESTING'] = True
    c = app_module.app.test_client()

    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('300229', 'a_stock', '拓尔思')")
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')")
        conn.commit()
        cur = conn.cursor()
        cur.execute("SELECT id FROM stocks WHERE symbol='300229'")
        c._mismatch_id = cur.fetchone()['id']
        cur.execute("SELECT id FROM stocks WHERE symbol='600519'")
        c._consistent_id = cur.fetchone()['id']
    finally:
        conn.close()
    return c


def _seed_report(stock_id, score, rating, key_factors='{}'):
    """写入今日最新 ok 日报（generated_at=现在，保持快照新鲜）"""
    conn = db_manager.get_connection()
    try:
        conn.execute(
            "INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name, "
            "engine_version, total_score, rating, key_factors, status, report_type, "
            "generated_at) "
            "VALUES (date('now', 'localtime'), ?, '300229', '拓尔思', 'v5', ?, ?, ?, "
            "'ok', 'daily', ?)",
            (stock_id, score, rating, key_factors, datetime.now().isoformat()),
        )
        conn.commit()
    finally:
        conn.close()


class TestWatchlistScoresScoreTierNote:
    def test_mismatch_report_gets_note(self, client):
        """拓尔思形态：52.0 属持有观望区间 × 评级建议减仓 → 注记在场"""
        _seed_report(client._mismatch_id, 52.0, '建议减仓')
        resp = client.get('/api/portfolio/watchlist-scores')
        assert resp.status_code == 200
        data = resp.get_json()
        row = next(s for s in data['stocks'] if s['id'] == client._mismatch_id)
        assert row['score_tier_note']
        assert '持有观望' in row['score_tier_note']
        assert '建议减仓' in row['score_tier_note']
        assert '<' not in row['score_tier_note']
        # 评级本身不受注记影响（只加说明）
        assert row['rating'] == '建议减仓'
        assert row['total_score'] == 52.0

    def test_consistent_report_note_is_none(self, client):
        _seed_report(client._consistent_id, 70.0, '推荐买入')
        resp = client.get('/api/portfolio/watchlist-scores')
        row = next(
            s for s in resp.get_json()['stocks'] if s['id'] == client._consistent_id)
        assert row['score_tier_note'] is None

    def test_no_report_note_is_none(self, client):
        """无报告行（LEFT JOIN 兜底）：不报错、注记为 None"""
        resp = client.get('/api/portfolio/watchlist-scores')
        row = next(
            s for s in resp.get_json()['stocks'] if s['id'] == client._mismatch_id)
        assert row['score_tier_note'] is None


class TestReportLatestSnapshotNote:
    def test_stored_note_preferred_and_dims_pure(self, client):
        """落库注记优先透出；非维度键不混入 dimensions（021BS 加固）"""
        kf = (
            '{"kline": {"score": 43.3, "weight": 0.2715, "top_factors": {}}, '
            '"trader": {"stage_name": "下跌期", "has_disagreement": false}, '
            '"score_tier_note": "落库注记A"}'
        )
        _seed_report(client._mismatch_id, 52.0, '建议减仓', key_factors=kf)
        resp = client.get(f"/api/stocks/{client._mismatch_id}/report-latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['score_tier_note'] == '落库注记A'
        # 字符串注记键 + trader 字典键均不得混入 dimensions
        assert set(data['dimensions'].keys()) <= {'kline', 'fundamental', 'capital_flow', 'news'}
        assert 'kline' in data['dimensions']

    def test_computed_note_fills_legacy_report(self, client):
        """021BS 前存量报告（key_factors 无注记）：读取面现算补齐"""
        _seed_report(client._mismatch_id, 52.0, '建议减仓', key_factors='{}')
        resp = client.get(f"/api/stocks/{client._mismatch_id}/report-latest")
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['score_tier_note']
        assert '持有观望' in data['score_tier_note']

    def test_consistent_report_note_is_none(self, client):
        _seed_report(client._consistent_id, 70.0, '推荐买入')
        resp = client.get(f"/api/stocks/{client._consistent_id}/report-latest")
        assert resp.status_code == 200
        assert resp.get_json()['score_tier_note'] is None


class TestAttachScoreTierNote:
    def test_attaches_note_on_mismatch(self):
        from blueprints.analysis import _attach_score_tier_note

        result = {'total_score': 52.0, 'rating': '建议减仓', 'market': 'a_stock'}
        _attach_score_tier_note(result)
        assert result['score_tier_note']
        assert '持有观望' in result['score_tier_note']

    def test_none_on_consistent(self):
        from blueprints.analysis import _attach_score_tier_note

        result = {'total_score': 47.5, 'rating': '建议减仓', 'market': 'a_stock'}
        _attach_score_tier_note(result)
        assert result['score_tier_note'] is None

    def test_survives_bad_input(self):
        from blueprints.analysis import _attach_score_tier_note

        result = {'total_score': 'abc', 'rating': None, 'market': None}
        _attach_score_tier_note(result)
        assert result['score_tier_note'] is None


class TestAnalysisJsSurfaces:
    """021BS 前端锁定：失配注记横幅 + 三层口径固定脚注（文案标记在位）"""

    @staticmethod
    def _js():
        import os

        path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'static', 'js', 'analysis.js',
        )
        with open(path, encoding='utf-8') as fh:
            return fh.read()

    def test_caliber_footnote_fixed(self):
        """021BS P2①：固定脚注（不依赖异步罗盘数据）+ 指引看④操作矩阵"""
        js = self._js()
        assert '口径说明：本页「技术面子分」为评分动量口径' in js
        assert '正常分层而非矛盾' in js
        assert '④ 操作矩阵' in js

    def test_score_tier_note_banner(self):
        """021BS P1-1：评分卡按响应字段渲染失配口径横幅"""
        js = self._js()
        assert 'adviseData.score_tier_note' in js
        assert '⚖️' in js
