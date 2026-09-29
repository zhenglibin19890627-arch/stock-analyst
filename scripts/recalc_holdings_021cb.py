"""
021CB：持仓重算口径迁移脚本（分红/红利补税「红到成本」）

背景：021CB 起 `_recalculate_holding` 的分红(dividend)/红利补税(dividend_tax)
口径变更为「红到成本」（用户口径，对齐券商 App 摊薄成本展示）——持仓中
折进/折回成本价；清仓后到账仍计已实现盈亏。存量 holdings 的
cost_price / realized_pnl 是旧口径（分红计入已实现盈亏）的产物，
需一次性重算刷新（衍生表同步，表结构与流水数据零改动）。

顺带落库一项用户提供的实际费用订正（消除估算口径差）：
    trade_records id=62（002352 顺丰控股 · 东方财富 · 2026-09-28 卖出 500 股 @30.40）
    佣金估算 12.75 → 交割单/App 实收 12.60（commission_estimated 1 → 0）

用法（在项目根执行）：
    python scripts/recalc_holdings_021cb.py            # dry-run：只打印将发生的变化，不写库
    python scripts/recalc_holdings_021cb.py --apply    # 先自动备份数据库，再落库

红线提示：写库前强制 backup_database()（R11）；本脚本只 UPDATE holdings
衍生字段与 id=62 单行费用，不触碰任何原始流水表结构。
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database import db_manager  # noqa: E402

# 用户提供的实际费用订正清单：(trade_id, 实际费用, 备注)
FEE_FIXES = [
    (62, 12.60, '002352 东财 09-28 卖出 500 股：交割单/App 实收（用户提供 2026-09-25）'),
]


def _snapshot(cursor):
    cursor.execute(
        'SELECT id, stock_id, account_id, cost_price, quantity, realized_pnl '
        'FROM holdings ORDER BY account_id, stock_id'
    )
    return {r['id']: dict(r) for r in cursor.fetchall()}


def main():
    apply_mode = '--apply' in sys.argv
    print('=' * 72)
    print('  021CB 持仓重算口径迁移（分红/补税 → 红到成本）' + ('【--apply 落库模式】' if apply_mode else '【dry-run 预览】'))
    print('=' * 72)

    conn = db_manager.get_connection()
    import sqlite3

    conn.row_factory = sqlite3.Row  # _recalculate_holding / 快照均按列名取值
    cursor = conn.cursor()

    before = _snapshot(cursor)

    if apply_mode:
        backup = db_manager.backup_database(reason='recalc_holdings_021cb')
        if not backup:
            print('[中止] 数据库备份失败（R11：失败必须中止）——未做任何修改。')
            return 1
        print(f'[备份] {backup}')

        # 021X 口径：用户编辑填入实际值 → commission_estimated 清零
        for trade_id, fee, note in FEE_FIXES:
            cursor.execute(
                'UPDATE trade_records SET commission=?, commission_estimated=0 WHERE id=?',
                (fee, trade_id),
            )
            print(f'[费用订正] trade id={trade_id} → 佣金 {fee}（est=0）：{note}')
        conn.commit()

    # 逐（股票, 账户）重算（内存中演算；apply 模式下同一连接稍后统一提交）
    from blueprints.portfolio.trades import _recalculate_holding

    results = []
    for hid, row in sorted(before.items()):
        stock_id = row['stock_id']
        account_id = row['account_id']
        new = _recalculate_holding(cursor, stock_id, account_id)
        results.append((hid, stock_id, account_id, new))

    if apply_mode:
        conn.commit()

    print()
    print(f"{'hid':>4} {'stock':>8} {'acct':>4} | {'旧成本':>10} {'新成本':>10} {'旧real':>9} {'新real':>9} {'股数':>6}")
    print('-' * 72)
    changed = 0
    for hid, stock_id, account_id, new in results:
        row = before[hid]
        old_cost = round(float(row['cost_price'] or 0), 4)
        old_real = round(float(row['realized_pnl'] or 0), 2)
        cost_changed = abs(old_cost - new['avg_cost']) > 1e-9
        real_changed = abs(old_real - new['realized_pnl']) > 1e-9
        if cost_changed or real_changed:
            changed += 1
        flag = '*' if (cost_changed or real_changed) else ' '
        print(
            f'{hid:>4} {stock_id:>8} {account_id:>4} | {old_cost:>10.4f} {new["avg_cost"]:>10.4f} '
            f'{old_real:>9.2f} {new["realized_pnl"]:>9.2f} {new["quantity"]:>6} {flag}'
        )
    print('-' * 72)
    print(f'共 {len(results)} 个持仓，{changed} 个发生变化（* 标记）。')

    if not apply_mode:
        print('\n[dry-run] 未写库。确认无误后加 --apply 落库（会先自动备份数据库）。')
    else:
        print('\n[--apply] 重算与费用订正已提交。')
    conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
