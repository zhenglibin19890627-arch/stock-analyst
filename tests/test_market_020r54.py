# -*- coding: utf-8 -*-
"""020R-54：行业资金流向利用层测试（温度计/连续方向/个股行业背景）"""

import json

import pytest

import app as app_module
from database import db_manager


@pytest.fixture
def iff_db(tmp_path, monkeypatch):
    db_file = tmp_path / 'iff54_test.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    # 2 个行业 × 3 日：BK1 连续 3 日流入；BK2 连续 3 日流出
    rows = [
        ('2026-08-12', 'BK1', '半导体', 100.0),
        ('2026-08-12', 'BK2', '白酒', -50.0),
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


class TestFlowStreaksAndSummary:
    def test_streaks(self, iff_db):
        from modules.market_overview import compute_streaks

        s = compute_streaks('2026-08-14')
        assert s['BK1'] == 3
        assert s['BK2'] == -3

    def test_summary(self, iff_db):
        from modules.market_overview import get_industry_flow_summary

        sm = get_industry_flow_summary('2026-08-14')
        assert sm['total'] == 2
        assert sm['total_net'] == 150.0  # 当日 BK1 +300 / BK2 -150
        assert sm['inflow_count'] == 1
        assert sm['outflow_count'] == 1
        assert sm['flat_count'] == 0


class TestIndustryAliasAndBg:
    def test_match_board_name(self):
        from modules.market_overview import match_board_name

        boards = ['半导体', '物流', '医药生物', '电力设备', '家用电器', '食品饮料', '电池']
        assert match_board_name('物流行业', boards) == '物流'
        assert match_board_name('医药制造', boards) == '医药生物'
        assert match_board_name('电池', boards) == '电池'
        assert match_board_name('半导体', boards) == '半导体'
        assert match_board_name('港股', boards) is None
        assert match_board_name('不存在的行业', boards) is None

    def test_bg_map_rank_and_streak(self, iff_db):
        from modules.market_overview import get_industry_flow_bg_map

        m = get_industry_flow_bg_map('2026-08-14')
        assert m['半导体']['rank'] == 1
        assert m['半导体']['main_net'] == 300.0
        assert m['半导体']['streak_days'] == 3
        assert m['白酒']['rank'] == 2
        assert m['白酒']['streak_days'] == -3


class TestEndpointSummary:
    def test_get_has_summary_and_streak(self, iff_db):
        app_module.app.config['TESTING'] = True
        client = app_module.app.test_client()
        resp = client.get('/api/market/industry-fund-flow')
        data = json.loads(resp.data)
        assert data['success'] is True
        assert data['summary']['total_net'] == 150.0
        assert data['summary']['inflow_count'] == 1
        assert data['items'][0]['streak_days'] == 3
