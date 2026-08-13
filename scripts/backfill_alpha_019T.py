# -*- coding: utf-8 -*-
"""
019T T3 存量补算脚本：backtest_results 基准/alpha 列幂等补算（评审开放项 C：全量 820 行）

用法:
    python backfill_alpha_019T.py --db <目标库路径> --market a_stock [--reason 019T_alpha_backfill_a]

- 幂等：重跑覆盖同名列，结果确定性一致
- 写库前 backup_database(reason) 备份
- market: a_stock（000300 基准）/ hk_stock（HSI，待采集修复入库后执行）
- 对齐规则同 T1/T3 评审 §2.2：基准价 = index_kline.trade_date <= rating_date 最近收盘
- 缺基准 → 对应列 NULL、is_correct_alpha NULL（不判定、不代理）
"""
import argparse
import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

PROJECT = r'C:\Users\zlb19\Desktop\Qoder cn\stock_analyst'
sys.path.insert(0, PROJECT)
import config


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--db', required=True, help='目标 sqlite 库路径（生产库或临时副本）')
    ap.add_argument('--market', required=True, choices=['a_stock', 'hk_stock'])
    ap.add_argument('--reason', default='019T_alpha_backfill')
    ap.add_argument('--backup', action='store_true', help='写库前备份（生产库必须开启）')
    args = ap.parse_args()

    # 指向目标库
    config.DB_PATH = args.db
    import database.db_manager as dbm

    dbm.DB_PATH = args.db

    from modules.backtest_engine import BacktestEngine, _ensure_columns

    if args.backup:
        path = dbm.backup_database(args.reason)
        if not path:
            print('[FATAL] 备份失败，中止补算')
            sys.exit(1)
        print(f'[备份] {path}')

    _ensure_columns()  # 幂等迁移：追加 7 列
    engine = BacktestEngine()

    conn = dbm.get_connection()
    cursor = conn.cursor()
    rows = cursor.execute(
        'SELECT id, rating_date, rating, return_1d, return_1w, return_1m '
        'FROM backtest_results WHERE market = ? ORDER BY id', (args.market,)
    ).fetchall()
    conn.close()

    total = len(rows)
    filled = 0
    no_bench = 0
    for r in rows:
        block = engine._compute_alpha_block(
            args.market, r['rating_date'], r['rating'],
            {'return_1d': r['return_1d'], 'return_1w': r['return_1w'], 'return_1m': r['return_1m']},
        )
        if block['alpha_1d'] is None and block['alpha_1w'] is None and block['alpha_1m'] is None:
            no_bench += 1
        elif block['alpha_1d'] is not None or block['alpha_1w'] is not None or block['alpha_1m'] is not None:
            filled += 1
        conn = dbm.get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'UPDATE backtest_results SET bench_return_1d=?, bench_return_1w=?, bench_return_1m=?, '
            'alpha_1d=?, alpha_1w=?, alpha_1m=?, is_correct_alpha=? WHERE id=?',
            (block['bench_return_1d'], block['bench_return_1w'], block['bench_return_1m'],
             block['alpha_1d'], block['alpha_1w'], block['alpha_1m'],
             block['is_correct_alpha'], r['id']),
        )
        conn.commit()
        conn.close()

    print(f'[补算] market={args.market} total={total} 至少一个 alpha 非 NULL={filled} '
          f'全 NULL(缺基准/无收益)={no_bench}')
    print('DONE')


if __name__ == '__main__':
    main()
