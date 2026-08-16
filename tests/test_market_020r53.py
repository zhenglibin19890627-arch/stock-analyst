# -*- coding: utf-8 -*-
"""020R-53：行业资金流向时间维度测试（历史日期读取 + 5日累计 + 端点 date 参数）"""

import json

import pytest

import app as app_module
from database import db_manager


@pytest.fixture
def iff_db(tmp_path, monkeypatch):
    db_file = tmp_path / 'iff53_test.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    # 3 个交易日 × 2 个行业：BK1 主力净流入 100/200/300，BK2 为 -50/-100/-150
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


class TestIndustryFundFlowTimeDimension:
    def test_dates_desc(self, iff_db):
        from modules.market_overview import get_industry_fund_flow_dates

        dates = get_industry_fund_flow_dates()
        assert dates == ['2026-08-14', '2026-08-13', '2026-08-12']

    def test_for_date_with_trailing_5d(self, iff_db):
        from modules.market_overview import get_industry_fund_flow_for_date

        # 最新日：5 日窗口含全部 3 天 → BK1=600, BK2=-300
        items, updated_at = get_industry_fund_flow_for_date('2026-08-14')
        by_code = {it['code']: it for it in items}
        assert by_code['BK1']['main_net'] == 300.0
        assert by_code['BK1']['main_net_5d'] == 600.0
        assert by_code['BK2']['main_net_5d'] == -300.0
        assert updated_at is not None

        # 中间日：窗口只有 08-12/08-13 两天 → BK1=300
        items, _ = get_industry_fund_flow_for_date('2026-08-13')
        by_code = {it['code']: it for it in items}
        assert by_code['BK1']['main_net_5d'] == 300.0

    def test_endpoint_date_param(self, iff_db):
        app_module.app.config['TESTING'] = True
        client = app_module.app.test_client()

        # 默认最新日
        resp = client.get('/api/market/industry-fund-flow')
        data = json.loads(resp.data)
        assert data['success'] is True
        assert data['trade_date'] == '2026-08-14'
        assert data['dates'] == ['2026-08-14', '2026-08-13', '2026-08-12']
        assert data['items'][0]['code'] == 'BK1'  # 主力净流入降序
        assert data['items'][0]['main_net_5d'] == 600.0

        # 指定历史日期
        resp = client.get('/api/market/industry-fund-flow?date=2026-08-12')
        data = json.loads(resp.data)
        assert data['trade_date'] == '2026-08-12'
        assert data['items'][0]['main_net_5d'] == 100.0

        # 未知日期回退到最新
        resp = client.get('/api/market/industry-fund-flow?date=2099-01-01')
        data = json.loads(resp.data)
        assert data['trade_date'] == '2026-08-14'
