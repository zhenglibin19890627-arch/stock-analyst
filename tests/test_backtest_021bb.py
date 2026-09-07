"""
回测引擎归因自然键 JOIN 测试（021BB）

背景：ratings_history 用 INSERT OR REPLACE（同股同日重评换新 id），旧按
rh.id = br.rating_id 的 JOIN 产生孤儿 rating_id（实测 338 条），引擎归因失败
被误标"未标记(历史)"。021BB 改按自然键 (stock_id, rating_date) JOIN。

覆盖：
1. 孤儿 rating_id + (stock_id, rating_date) 匹配 → 归因 v5
2. 完全无匹配评级行 → 仍为"未标记(历史)"（诚实降级，不臆造标记）
3. REPLACE 换 id 后自然键仍稳定命中

隔离：临时库，不触网（对齐 test_backfill_scheduler 模式）。
"""

import pytest

from database import db_manager
from database.db_manager import get_connection, init_database
from modules.backtest_engine import BacktestEngine


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_file = tmp_path / 'test_ev_join.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()

    conn = get_connection()
    try:
        # 两只股票
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600001', 'a_stock', '甲')"
        )
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600002', 'a_stock', '乙')"
        )
        # 股票1：评级行（v5 标记），rating_date 与回测行对齐——但回测行的
        # rating_id 指向不存在的 id（模拟 REPLACE 换 id 后的孤儿）
        conn.execute(
            "INSERT INTO ratings_history (stock_id, rating_date, rating, total_score, engine_version) "
            "VALUES (1, '2026-08-20', '持有观望', 55.0, 'v5')"
        )
        conn.execute(
            "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
            "price_at_rating, return_1d, is_correct, is_simulated) "
            "VALUES (1, 999999, 'a_stock', '2026-08-20', '持有观望', 10.0, 1.2, 1, 0)"
        )
        # 股票2：回测行存在，但评级行已被 REPLACE 后日期漂移（无自然键匹配）
        conn.execute(
            "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
            "price_at_rating, return_1d, is_correct, is_simulated) "
            "VALUES (2, 888888, 'a_stock', '2026-08-21', '推荐买入', 20.0, -0.5, 0, 0)"
        )
        conn.commit()
    finally:
        conn.close()
    yield


def test_orphan_rating_id_attributed_via_natural_key(db):
    """孤儿 rating_id 仍经 (stock_id, rating_date) 归因到 v5"""
    report = BacktestEngine().compute_market_report('a_stock')
    assert report['total'] == 2
    ev = report['engine_stats']
    assert ev.get('v5', {}).get('total') == 1  # 股票1：自然键命中 → v5
    assert '未标记(历史)' in ev  # 股票2：无匹配评级行 → 诚实降级为未标记
    assert ev['未标记(历史)']['total'] == 1


def test_natural_key_join_tolerates_replaced_rating(db):
    """REPLACE 换 id 场景：自然键指向当前行（同为该股当日评级）→ 归因不丢"""
    conn = get_connection()
    try:
        # 模拟 REPLACE：删旧插新，id 变化但 (stock_id, rating_date) 不变
        conn.execute('DELETE FROM ratings_history WHERE stock_id = 1')
        conn.execute(
            "INSERT INTO ratings_history (stock_id, rating_date, rating, total_score, engine_version) "
            "VALUES (1, '2026-08-20', '推荐买入', 68.0, 'v5')"
        )
        conn.commit()
    finally:
        conn.close()
    report = BacktestEngine().compute_market_report('a_stock')
    ev = report['engine_stats']
    assert ev.get('v5', {}).get('total') == 1  # 股票1 归因成功
    assert '未标记(历史)' in ev
