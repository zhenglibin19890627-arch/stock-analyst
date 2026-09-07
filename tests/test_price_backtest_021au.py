"""
021AU：价格回测口径升级——锚点当日真实评级 + 持仓状态流水倒推

覆盖：
1. _historical_position_state：买入前/后状态、加权成本、卖出清仓、
   未来日期流水不参与（无未来函数）、空流水
2. _find_anchor_rating：±5 天窗口取最近评级
3. 集成：run_price_backtest 回放行的评级=锚点真实评级、
   has_position=回测日时点持仓、无锚点行退回实时评级且 anchor 为空
4. 分评级统计只含真实锚点样本
"""

import pytest

from database import db_manager

# ---------- 1. 历史持仓倒推（纯函数） ----------

def _tr(dt, ttype, qty, price, commission=0):
    return {
        'trade_date': dt,
        'trade_type': ttype,
        'quantity': qty,
        'price': price,
        'commission': commission,
    }


class TestHistoricalPositionState:
    def test_buy_after_date_ignored(self):
        """未来函数防线：回测日之后的买入不计入。"""
        from modules.price_backtest import _historical_position_state

        trades = [_tr('2026-02-01', 'buy', 1000, 10.0)]
        has_pos, cost = _historical_position_state(trades, '2026-01-15')
        assert has_pos is False and cost is None

    def test_buy_before_date_with_cost(self):
        from modules.price_backtest import _historical_position_state

        trades = [
            _tr('2026-01-05', 'buy', 1000, 10.0, commission=5.0),
            _tr('2026-01-10', 'buy', 1000, 12.0, commission=5.0),
        ]
        has_pos, cost = _historical_position_state(trades, '2026-01-15')
        assert has_pos is True
        assert cost == pytest.approx((10000 + 5 + 12000 + 5) / 2000, abs=0.001)

    def test_sell_all_clears(self):
        from modules.price_backtest import _historical_position_state

        trades = [
            _tr('2026-01-05', 'buy', 1000, 10.0),
            _tr('2026-01-08', 'sell', 1000, 11.0),
        ]
        has_pos, cost = _historical_position_state(trades, '2026-01-15')
        assert has_pos is False and cost is None

    def test_empty_trades(self):
        from modules.price_backtest import _historical_position_state

        has_pos, cost = _historical_position_state([], '2026-01-15')
        assert has_pos is False and cost is None


# ---------- 2/3/4. 锚点评级 + 集成 ----------

@pytest.fixture()
def replay_env(tmp_path, monkeypatch):
    """隔离库 + 1 只 A股 + 60 根K线 + 两笔评级锚点 + 一笔跨时点买入。

    日期换算：base=2026-01-01，idx i → 1/1 + i 天（1月31天）。
    回放点 idx 35/40/45/50/55 → 2/5、2/10、2/15、2/20、2/25。
    锚点：2/16 建议减仓（距 2/15 为 1 天、2/20 为 4 天）、
    2/27 推荐买入（距 2/25 为 2 天）。
    买入 2/11 → 2/5、2/10 时点无持仓；2/15 起有持仓（成本 10.0）。
    """
    import datetime as dt

    db_path = str(tmp_path / 'test_pb_021au.db')
    monkeypatch.setattr(db_manager, 'DB_PATH', db_path)
    db_manager.init_database()

    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')"
    )
    sid = conn.execute('SELECT id FROM stocks').fetchone()['id']

    base = dt.date(2026, 1, 1)
    for i in range(60):
        d = (base + dt.timedelta(days=i)).isoformat()
        close = 10.0 + (i % 7) * 0.1  # 温和波动
        conn.execute(
            'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume, amount, pct_change) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
            (sid, d, close - 0.05, close, close + 0.2, close - 0.2, 1000000, 10000000, 0.5),
        )

    conn.execute(
        'INSERT INTO ratings_history (stock_id, rating_date, rating, total_score) VALUES (?, ?, ?, ?)',
        (sid, '2026-02-16', '建议减仓', 40.0),
    )
    conn.execute(
        'INSERT INTO ratings_history (stock_id, rating_date, rating, total_score) VALUES (?, ?, ?, ?)',
        (sid, '2026-02-27', '推荐买入', 75.0),
    )

    conn.execute(
        "INSERT INTO trade_records (stock_id, trade_type, price, quantity, amount, commission, trade_date) "
        "VALUES (?, 'buy', 10.0, 1000, 10000, 0, '2026-02-11')",
        (sid,),
    )
    conn.commit()
    conn.close()
    return sid


def _rows(sid):
    conn = db_manager.get_connection()
    rows = [
        dict(r)
        for r in conn.execute(
            'SELECT backtest_date, rating, has_position, anchor_rating_date, anchor_rating '
            'FROM price_backtest_results WHERE stock_id=? ORDER BY backtest_date',
            (sid,),
        )
    ]
    conn.close()
    return rows


def test_find_anchor_rating_window(replay_env):
    from modules.price_backtest import _find_anchor_rating

    conn = db_manager.get_connection()
    cur = conn.cursor()
    sid = replay_env
    hit = _find_anchor_rating(cur, sid, '2026-02-16')
    assert hit is not None and hit['rating'] == '建议减仓'
    hit2 = _find_anchor_rating(cur, sid, '2026-02-21')  # 距 2/16 为 5 天，距 2/27 为 6 天
    assert hit2 is not None and hit2['rating'] == '建议减仓'
    miss = _find_anchor_rating(cur, sid, '2026-02-05')  # 距 2/16 为 11 天
    assert miss is None
    conn.close()


def test_replay_uses_anchor_rating_and_position(replay_env):
    from modules.price_backtest import run_price_backtest

    sid = replay_env
    result = run_price_backtest(market='a_stock', force=True)
    assert result['success'] > 0
    rows = _rows(sid)

    by_date = {r['backtest_date']: r for r in rows}
    # 锚点行：评级=锚点当日真实评级
    r15 = by_date['2026-02-15']
    assert r15['anchor_rating_date'] == '2026-02-16'
    assert r15['rating'] == '建议减仓'
    assert r15['has_position'] == 1, '2/11 买入后应有持仓'

    r25 = by_date['2026-02-25']
    assert r25['rating'] == '推荐买入'
    assert r25['anchor_rating'] == '推荐买入'

    # 无锚点行（2/5、2/10）：anchor 为空，评级=实时评分（重建点）
    early = [r for r in rows if r['backtest_date'] <= '2026-02-10']
    assert len(early) == 2
    for r in early:
        assert r['anchor_rating_date'] is None
        assert r['has_position'] == 0, '2/11 买入前的时点应为无持仓（无未来函数）'


def test_report_rating_stats_real_only(replay_env):
    """分评级统计只含真实锚点样本。"""
    from modules.price_backtest import compute_price_backtest_report, run_price_backtest

    run_price_backtest(market='a_stock', force=True)
    rep = compute_price_backtest_report('a_stock')
    stats = rep.get('rating_stats') or {}
    total_real = rep.get('real_sample', {}).get('total', 0)
    assert sum(v['total'] for v in stats.values()) <= total_real
