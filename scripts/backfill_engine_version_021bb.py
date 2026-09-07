"""021BB 一次性数据修复：回填 ratings_history 历史行的引擎标记。

背景：
- ratings_history.engine_version 标记 2026-08-14 才上线，此前（7/16 起）的 514 条
  v5 时代评级该列为 NULL，回测中心将其误标为"未标记(历史)/历史引擎"。
- 事实依据：系统自 2026-07-16 起评级即由 v5 引擎生成（021AU 记录），
  经典引擎 021AE（2026-08-22）整体退役，全库不存在任何 legacy 标记，
  当前代码恒写 engine_version='v5'（advisor.py 021AE 注释）。
- 因此 NULL → 'v5' 为事实性回填，非口径放宽。

配套：backtest_engine.compute_market_report 已改为按自然键
(stock_id, rating_date) JOIN（021BB），孤儿 rating_id 不再导致归因失败。

运行（项目根目录）：python scripts/backfill_engine_version_021bb.py
幂等：已是 'v5' 或已回填过的行不会重复修改。
"""

import sys

sys.path.insert(0, '.')

from database.db_manager import backup_database, get_connection


def main():
    backup = backup_database(reason='021bb_backfill_engine_version')
    if backup:
        print(f'[OK] 已备份数据库: {backup}')
    else:
        print('[WARN] 备份失败（尽力而为语义），继续执行回填')

    conn = get_connection()
    try:
        before = conn.execute(
            "SELECT COUNT(*) AS c FROM ratings_history "
            "WHERE engine_version IS NULL OR TRIM(engine_version) = ''"
        ).fetchone()['c']
        print(f'待回填 NULL 引擎标记: {before} 条')
        if before == 0:
            print('[OK] 无需回填')
            return
        conn.execute(
            "UPDATE ratings_history SET engine_version = 'v5' "
            "WHERE engine_version IS NULL OR TRIM(engine_version) = ''"
        )
        conn.commit()
        after = conn.execute(
            "SELECT COUNT(*) AS c FROM ratings_history "
            "WHERE engine_version IS NULL OR TRIM(engine_version) = ''"
        ).fetchone()['c']
        total_v5 = conn.execute(
            "SELECT COUNT(*) AS c FROM ratings_history WHERE engine_version = 'v5'"
        ).fetchone()['c']
        print(f'[OK] 回填完成: {before - after} 条 -> v5；剩余 NULL: {after}；全表 v5 标记: {total_v5} 条')
    finally:
        conn.close()


if __name__ == '__main__':
    main()
