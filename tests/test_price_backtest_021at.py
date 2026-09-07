"""
021AT：价格回测 force 重跑的市场定向清除

回归背景：run_price_backtest(market='a_stock', force=True) 原实现全表清空后
仅重建 A股——用户在回测中心选"A股"点重跑，170 条港股结果被误抹。
测试库无 K线股票 → run_price_backtest 在清表后于"无符合股票"处提前返回，
正好隔离验证清表作用域。
"""

import sqlite3

import pytest

from database import db_manager


@pytest.fixture()
def env(tmp_path, monkeypatch):
    db_path = str(tmp_path / 'test_pb_clear_021at.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()
    conn = db_manager.get_connection()
    # 预置两个市场的旧行
    for mkt in ('a_stock', 'hk_stock'):
        conn.execute(
            'INSERT INTO price_backtest_results (stock_id, backtest_date, rating, market, has_position) '
            "VALUES (1, '2026-08-01', '持有观望', ?, 0)",
            (mkt,),
        )
    conn.commit()
    conn.close()
    return db_path


def _markets_left(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        'SELECT market, COUNT(*) FROM price_backtest_results GROUP BY market'
    ).fetchall()
    conn.close()
    return dict(rows)


def test_force_with_market_keeps_other_market(env):
    """force=True + market='a_stock'：只清 A股行，港股行保留。"""
    from modules.price_backtest import run_price_backtest

    result = run_price_backtest(market='a_stock', force=True)
    assert result['total'] == 0  # 无符合条件股票，提前返回（清表已执行）
    left = _markets_left(env)
    assert left.get('a_stock', 0) == 0, 'A股行应被清除'
    assert left.get('hk_stock', 0) == 1, '港股行必须保留（021AT 修复点）'


def test_force_without_market_clears_all(env):
    """force=True + market=None：全表清空（原全量重跑行为不变）。"""
    from modules.price_backtest import run_price_backtest

    run_price_backtest(market=None, force=True)
    assert _markets_left(env) == {}


def test_no_force_keeps_everything(env):
    """force=False：不清表。"""
    from modules.price_backtest import run_price_backtest

    run_price_backtest(market='a_stock', force=False)
    left = _markets_left(env)
    assert left.get('a_stock', 0) == 1 and left.get('hk_stock', 0) == 1
