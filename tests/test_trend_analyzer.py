"""
趋势罗盘测试（2026-09-07）：三周期趋势判定 + 端点。

覆盖：
- 五信号打分的三分类边界（上涨/下跌/震荡）与置信度
- 数据不足 → na 且不影响其他周期
- 多周期加权综合 + 三周期共振
- GET /api/stocks/<id>/trend 端点（隔离库，monkeypatch 适配器，不触网）
"""

import pytest

from app import app
from database import db_manager
from modules.data_contract import StockData
from modules.trend_analyzer import DOWN, NA, SIDEWAYS, UP, analyze_trends


def _stock(**kw):
    """构造 StockData：默认全多头（close 12 > ma5 11 > ma20 10, DIF 0.5 > DEA 0.3 > 0）。"""
    base = dict(
        code='000333',
        market='A',
        trade_date='20260907',
        close=12.0,
        ma5=11.0,
        ma20=10.0,
        macd_dif=0.5,
        macd_dea=0.3,
    )
    base.update(kw)
    return StockData(**base)


def test_full_bull_is_strong_up():
    r = analyze_trends(_stock())
    d = r['timeframes']['daily']
    assert d['trend'] == UP
    assert d['strength'] == '强'
    assert d['score'] == 5.0
    assert any('多头排列' in x for x in d['reasons'])


def test_full_bear_is_strong_down():
    r = analyze_trends(
        _stock(close=8.0, ma5=9.0, ma20=10.0, macd_dif=-0.5, macd_dea=-0.3)
    )
    d = r['timeframes']['daily']
    assert d['trend'] == DOWN
    assert d['score'] == -5.0
    assert any('空头排列' in x for x in d['reasons'])


def test_mixed_signals_is_sideways():
    # 多头排列(+2) 但 MACD 死叉(-1)、现价破20日线(-1)、DIF<0(-0.5)、现价破5日线(-0.5) → -1 震荡
    r = analyze_trends(
        _stock(close=9.8, ma5=11.0, ma20=10.0, macd_dif=-0.2, macd_dea=0.1)
    )
    assert r['timeframes']['daily']['trend'] == SIDEWAYS
    # 震荡理由应提示分歧
    assert any('交织' in x or '但 ' in x for x in r['timeframes']['daily']['reasons'])


def test_threshold_boundary():
    # +4.0 边界：多头排列+站上20日线+金叉+DIF>0，仅现价<5日线（+2+1+1+0.5-0.5）→ 上涨·强
    r = analyze_trends(_stock(close=10.5, ma5=11.0, ma20=10.0, macd_dif=0.4, macd_dea=0.3))
    d = r['timeframes']['daily']
    assert d['trend'] == UP and d['score'] == 4.0 and d['strength'] == '强'
    # +3.0 → 上涨·中：多头排列+站上20日线+现价>5日线，但 MACD 死叉回落（DIF>0 但 DIF<DEA）
    r2 = analyze_trends(_stock(close=11.5, ma5=11.0, ma20=10.0, macd_dif=0.2, macd_dea=0.4))
    d2 = r2['timeframes']['daily']
    assert d2['trend'] == UP and d2['score'] == 3.0 and d2['strength'] == '中'


def test_missing_data_is_na_and_isolated():
    r = analyze_trends(_stock(weekly_ma10=None))
    assert r['timeframes']['weekly']['trend'] == NA
    assert r['timeframes']['daily']['trend'] == UP  # 日线不受影响


def test_all_missing_everything_na():
    r = analyze_trends(_stock(ma5=None, ma20=None, macd_dif=None, macd_dea=None))
    assert r['overall']['trend'] == NA
    for tf in r['timeframes'].values():
        assert tf['trend'] == NA


def test_rebound_up_capped_at_mid_when_dif_below_zero():
    """中国中免实测反馈：均线多头+金叉但 DIF<0 → 上涨但强度上限"中"（反弹修复非强势）。"""
    r = analyze_trends(_stock(close=12.0, ma5=11.0, ma20=10.0, macd_dif=-0.1, macd_dea=-0.3))
    d = r['timeframes']['daily']
    assert d['trend'] == UP and d['strength'] == '中'
    assert any('反弹修复' in x for x in d['reasons'])


def test_rebound_down_capped_when_dif_above_zero():
    """镜像：空头排列但 DIF>0 → 下跌但强度上限"中"（强势整理回落）。"""
    r = analyze_trends(_stock(close=8.0, ma5=9.0, ma20=10.0, macd_dif=0.2, macd_dea=0.5))
    d = r['timeframes']['daily']
    assert d['trend'] == DOWN and d['strength'] == '中'


def test_resonance_when_all_timeframes_agree():
    r = analyze_trends(
        _stock(
            weekly_ma10=11.5, weekly_ma20=10.5,
            weekly_macd_dif=0.6, weekly_macd_dea=0.4,
            monthly_ma5=11.0, monthly_ma10=10.0,
            monthly_macd_dif=0.5, monthly_macd_dea=0.2,
        )
    )
    o = r['overall']
    assert o['trend'] == UP
    assert o['resonance'] == '三周期共振上涨'
    assert any('共振' in x for x in o['reasons'])


def test_monthly_outweighs_daily_in_overall():
    # 日线强空(-5)、月线强多(+5)：加权 (−5*1 + 5*2)/3 = +1.67 → 震荡（长期定方向）
    r = analyze_trends(
        _stock(
            close=8.0, ma5=9.0, ma20=10.0, macd_dif=-0.5, macd_dea=-0.3,
            monthly_ma5=7.0, monthly_ma10=6.0,
            monthly_macd_dif=0.5, monthly_macd_dea=0.3,
        )
    )
    assert r['timeframes']['daily']['trend'] == DOWN
    assert r['timeframes']['monthly']['trend'] == UP
    assert r['overall']['trend'] == SIDEWAYS


@pytest.fixture()
def tdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'trend.db'))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    return app.test_client()


def test_trend_endpoint(tdb, monkeypatch):
    import modules.data_adapter as adapter

    monkeypatch.setattr(adapter, 'load_stockdata_from_db', lambda stock_id: _stock())
    r = tdb.get('/api/stocks/1/trend')
    assert r.status_code == 200
    d = r.get_json()
    assert d['success'] and d['code'] == '000333'
    assert set(d['timeframes'].keys()) == {'daily', 'weekly', 'monthly'}
    assert d['timeframes']['daily']['trend'] == UP
    assert 'overall' in d and 'generated_at' in d


def test_trend_endpoint_404_when_no_data(tdb, monkeypatch):
    import modules.data_adapter as adapter

    monkeypatch.setattr(adapter, 'load_stockdata_from_db', lambda stock_id: None)
    r = tdb.get('/api/stocks/1/trend')
    assert r.status_code == 404
    assert '采集' in r.get_json()['message']
