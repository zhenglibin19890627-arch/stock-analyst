"""
周/月线聚合器测试（2026-09-07 日频污染修复）。

背景：020R-48 初版 bug 曾把日频行写入周/月表，修复版用 INSERT OR REPLACE
只覆盖同名（周末日）行，非周末的日频行永久滞留，导致周/月线指标被日频数据
扭曲（趋势罗盘"月线上涨·强"实际是近两三周日频数据）。聚合器改为"先删后插"
整表重建后，任何历史污染在每次采集时自动清除。
"""

from datetime import date, timedelta

import pytest

from database import db_manager
from modules.collector.kline import aggregate_period_klines


@pytest.fixture()
def pdb(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'agg.db'))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    return db_manager


def _daily_rows(stock_id, n=80):
    """n 个连续交易日（跳过周末）的日线行，价格缓涨。"""
    d = date(2026, 5, 4)
    rows = []
    px = 100.0
    while len(rows) < n:
        if d.weekday() < 5:
            px += 0.5
            rows.append((stock_id, d.isoformat(), px * 0.99, px, px * 1.01, px * 0.98, 1000.0))
        d += timedelta(days=1)
    return rows


def test_rebuild_purges_legacy_daily_rows(pdb):
    """先删后插：历史日频污染行在重建后被清除，周/月表恢复纯周/月频。"""
    conn = pdb.get_connection()
    cur = conn.cursor()
    rows = _daily_rows(1)
    cur.executemany(
        'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
        'VALUES (?,?,?,?,?,?,?)',
        rows,
    )
    # 模拟历史污染：日频日期的行滞留在周/月表
    cur.execute(
        "INSERT INTO raw_kline_weekly (stock_id, trade_date, open, close, high, low, volume) "
        "VALUES (1, '2026-08-05', 1, 1, 1, 1, 1)"
    )
    cur.execute(
        "INSERT INTO raw_kline_monthly (stock_id, trade_date, open, close, high, low, volume) "
        "VALUES (1, '2026-08-06', 1, 1, 1, 1, 1)"
    )
    conn.commit()
    conn.close()

    status, _msg = aggregate_period_klines(1)
    assert status == 'success'

    conn = pdb.get_connection()
    cur = conn.cursor()
    wd = [r[0] for r in cur.execute(
        'SELECT trade_date FROM raw_kline_weekly WHERE stock_id=1 ORDER BY trade_date').fetchall()]
    md = [r[0] for r in cur.execute(
        'SELECT trade_date FROM raw_kline_monthly WHERE stock_id=1 ORDER BY trade_date').fetchall()]
    conn.close()

    assert '2026-08-05' not in wd  # 污染行被清除
    assert '2026-08-06' not in md
    # 周频：相邻两行间隔 ≥3 天（周五→周一跨周最小 3 天），同周不重复
    ds = [date.fromisoformat(x) for x in wd]
    assert len(ds) >= 10
    assert all((b - a).days >= 3 for a, b in zip(ds, ds[1:]))
    # 月频：每月至多一行
    assert len({x[:7] for x in md}) == len(md)
    # 最后一条 = 最后一根日线的日期（当前不完整周/月也成行）
    assert wd[-1] == rows[-1][1]
    assert md[-1] == rows[-1][1]


def test_idempotent_rerun(pdb):
    """重复聚合结果幂等（行数与内容稳定，不因重跑翻倍）。"""
    conn = pdb.get_connection()
    cur = conn.cursor()
    cur.executemany(
        'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume) '
        'VALUES (?,?,?,?,?,?,?)',
        _daily_rows(2),
    )
    conn.commit()
    conn.close()

    aggregate_period_klines(2)
    conn = pdb.get_connection()
    cur = conn.cursor()
    c1 = cur.execute('SELECT COUNT(*) FROM raw_kline_weekly WHERE stock_id=2').fetchone()[0]
    conn.close()

    aggregate_period_klines(2)
    conn = pdb.get_connection()
    cur = conn.cursor()
    c2 = cur.execute('SELECT COUNT(*) FROM raw_kline_weekly WHERE stock_id=2').fetchone()[0]
    conn.close()
    assert c1 == c2
