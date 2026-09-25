"""表结构迁移域（t3 拆包）：backtest_results 幂等加列 + 位置/回撤列回填。

划分依据：全部围绕 backtest_results 表结构自愈的库侧操作——_ensure_columns
（ALTER TABLE ADD COLUMN 幂等迁移，BacktestEngine.__init__ 调用）、
_calc_pos_and_dd20 / _backfill_pos_dd（2026-09-18 回测提升①，报告聚合前自愈回填）。
实现体为原文件 L142-241 逐字节搬移。
"""

from modules.backtest_engine._env import get_connection, logger

# ============================================================
# 二、表结构迁移（安全追加列，幂等）
# ============================================================


def _ensure_columns():
    """确保 backtest_results 表有动态回测所需列（ALTER TABLE ADD COLUMN，幂等）。"""
    conn = get_connection()
    cursor = conn.cursor()
    needed = {
        'dynamic_end_date': 'TEXT',
        'dynamic_return': 'REAL',
        'dynamic_is_correct': 'INTEGER',
        'is_simulated': 'INTEGER DEFAULT 0',
        # 019T T3（评审 §4.2）：基准对比列（A股对标沪深300 / 港股对标恒指）
        'bench_return_1d': 'REAL',
        'bench_return_1w': 'REAL',
        'bench_return_1m': 'REAL',
        'alpha_1d': 'REAL',
        'alpha_1w': 'REAL',
        'alpha_1m': 'REAL',
        'is_correct_alpha': 'INTEGER',
        # 2026-09-18（回测提升①）：个股位置分位 + 事后20日最大回撤——
        # 支撑「分档×位置矩阵」与「避损口径」（验证结论：低位买入68% vs 高位买入33%）
        'pos_pctile': 'REAL',
        'dd20': 'REAL',
    }
    cursor.execute('PRAGMA table_info(backtest_results)')
    existing = {row['name'] for row in cursor.fetchall()}
    for col, col_type in needed.items():
        if col not in existing:
            try:
                cursor.execute(f'ALTER TABLE backtest_results ADD COLUMN {col} {col_type}')
                logger.info(f'backtest_results: added column {col}')
            except Exception as e:
                logger.warning(f'backtest_results: cannot add {col}: {e}')
    conn.commit()
    conn.close()


def _calc_pos_and_dd20(cursor, stock_id, rating_date):
    """评级日个股 60 日位置分位 + 事后 20 日最大回撤（2026-09-18 回测提升①）。

    - pos_pctile: 评级日收盘在近 60 日高低区间的分位（0~1，回测验证的核心分层因子）
    - dd20: 评级日后 20 个交易日最低收盘相对评级日收盘的回撤%（避损口径）
    数据不足返回 (None, None)，不硬造。
    """
    ks = [r[0] for r in cursor.execute(
        'SELECT close FROM raw_kline WHERE stock_id = ? AND trade_date <= ? '
        'ORDER BY trade_date DESC LIMIT 60',
        (stock_id, rating_date)).fetchall()]
    pos = None
    if len(ks) >= 40:
        hi, lo, now = max(ks), min(ks), ks[0]
        pos = round((now - lo) / (hi - lo), 3) if hi > lo else None
    fwd = [r[0] for r in cursor.execute(
        'SELECT close FROM raw_kline WHERE stock_id = ? AND trade_date > ? '
        'ORDER BY trade_date LIMIT 20',
        (stock_id, rating_date)).fetchall()]
    base = cursor.execute(
        'SELECT close FROM raw_kline WHERE stock_id = ? AND trade_date <= ? '
        'ORDER BY trade_date DESC LIMIT 1',
        (stock_id, rating_date)).fetchone()
    dd = round((min(fwd) / base[0] - 1) * 100, 2) if (fwd and base and base[0]) else None
    return pos, dd


def _backfill_pos_dd():
    """自愈式回填 pos_pctile/dd20 为 NULL 的行（报告聚合前调用，幂等增量）。

    raw_kline 表不存在时（极简测试库）直接跳过——不阻塞报告生成。
    """
    conn = get_connection()
    cursor = conn.cursor()
    has_kline = cursor.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='raw_kline'"
    ).fetchone()[0]
    if not has_kline:
        conn.close()
        return 0
    cursor.execute(
        'SELECT DISTINCT stock_id, rating_date FROM backtest_results '
        'WHERE pos_pctile IS NULL AND dd20 IS NULL')
    todo = cursor.fetchall()
    if not todo:
        conn.close()
        return 0
    n = 0
    for row in todo:
        pos, dd = _calc_pos_and_dd20(cursor, row['stock_id'], row['rating_date'])
        cursor.execute(
            'UPDATE backtest_results SET pos_pctile = ?, dd20 = ? '
            'WHERE stock_id = ? AND rating_date = ?',
            (pos, dd, row['stock_id'], row['rating_date']))
        n += 1
    conn.commit()
    conn.close()
    if n:
        logger.info(f'[backtest] 位置/回撤列回填 {n} 行')
    return n
