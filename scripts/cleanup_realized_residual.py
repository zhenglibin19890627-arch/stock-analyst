"""021BR t3：holdings.realized_pnl 残留一次性清理脚本（诊断 A11）。

背景（t1 诊断 docs/reports/021br_matrix_diag_20260922.md §1.4）：
    2026-09-18 卖出算法切换为摊薄成本法后，部分持仓行的 realized_pnl 仍是旧算法
    残留（实测中国中免银河账户 -1,341.41，摊薄法重放应为 0.0，全库唯一不一致行）。
    新流水触发的 _recalculate_holding 全量重放会自然修正，但无新流水的行会一直残留。

本脚本做什么：
    1. 对全部 holdings 行按（stock_id, account_id）用摊薄成本法逐笔重放
       trade_records（与 blueprints/portfolio/trades.py _recalculate_holding
       2026-09-18 版口径逐字对齐：买入计佣入成本、部分卖出净得摊薄、清仓转
       已实现、分红/补税记已实现）；
    2. 只比对 realized_pnl——数量/成本不比对不回写（成本含用户人工修正，
       is_cost_adjusted=1 属设计内行为，严禁覆盖）；
    3. 默认 dry-run：只打印将要修改的行，不写库；
    4. --apply：先 R11 自动备份（backup_database，失败即中止），再逐行回写。

用法（项目根目录）：
    python scripts/cleanup_realized_residual.py           # 只读预览
    python scripts/cleanup_realized_residual.py --apply   # 备份后执行清理

红线：R11（破坏性操作前必须备份且失败中止）；不改 trade_records、不碰
cost_price/quantity、不触碰 advisor（B24）。执行与否由用户批准后裁定。
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import backup_database, get_connection  # noqa: E402


def replay_holding(cursor, stock_id: int, account_id: int) -> dict:
    """按（股票, 账户）重放流水，返回 {quantity, avg_cost, realized_pnl}（只读）。

    口径镜像 _recalculate_holding（2026-09-18 摊薄成本法版），不写任何表。
    """
    cursor.execute(
        """
        SELECT trade_type, price, quantity, amount, commission
        FROM trade_records
        WHERE stock_id=? AND account_id=?
        ORDER BY trade_date ASC, created_at ASC
        """,
        (stock_id, account_id),
    )
    trades = cursor.fetchall()

    total_qty = 0
    avg_cost = 0.0
    total_cost = 0.0
    realized_pnl = 0.0

    for t in trades:
        qty = int(t['quantity'] or 0)
        price = float(t['price'] or 0)
        amount = float(t['amount'] or 0)
        commission = float(t['commission'] or 0)

        if t['trade_type'] == 'buy':
            if qty > 0:
                buy_amount = amount if amount > 0 else qty * price
                total_cost += buy_amount + commission
                total_qty += qty
                avg_cost = total_cost / total_qty if total_qty > 0 else 0
        elif t['trade_type'] == 'sell':
            if qty > 0:
                sell_qty = min(qty, total_qty) if total_qty > 0 else qty
                sell_amount = amount if amount > 0 else price * sell_qty
                if qty > sell_qty:
                    sell_amount = sell_amount * sell_qty / qty
                sell_net = sell_amount - commission
                if total_qty > qty:
                    # 部分卖出：净得摊薄进剩余成本，不转已实现
                    total_cost -= sell_net
                    total_qty -= qty
                    avg_cost = total_cost / total_qty if total_qty > 0 else 0
                else:
                    # 清仓卖出：差额一次性转已实现
                    realized_pnl += sell_net - total_cost
                    total_qty = 0
                    total_cost = 0
                    avg_cost = 0
        elif t['trade_type'] == 'dividend':
            realized_pnl += max(0, amount) - commission
        elif t['trade_type'] == 'dividend_tax':
            realized_pnl -= max(0, amount) + commission

    return {
        'quantity': total_qty,
        'avg_cost': round(avg_cost, 4),
        'realized_pnl': round(realized_pnl, 2),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description='holdings.realized_pnl 残留清理（R11 备份保护）')
    parser.add_argument('--apply', action='store_true',
                        help='实际执行（默认 dry-run 只预览；执行前自动备份，失败即中止）')
    args = parser.parse_args()

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            'SELECT id, stock_id, account_id, quantity, cost_price, realized_pnl, status '
            'FROM holdings ORDER BY id'
        )
        holdings = [dict(r) for r in cursor.fetchall()]

        mismatches = []
        for h in holdings:
            replay = replay_holding(cursor, h['stock_id'], h['account_id'])
            if abs(float(h['realized_pnl'] or 0) - replay['realized_pnl']) >= 0.01:
                mismatches.append((h, replay))

        print(f'共扫描 {len(holdings)} 行 holdings；realized_pnl 与摊薄法重放不一致 {len(mismatches)} 行：')
        for h, replay in mismatches:
            print(
                f"  holding_id={h['id']} stock_id={h['stock_id']} account_id={h['account_id']} "
                f"账面 realized={h['realized_pnl']} → 重放 {replay['realized_pnl']}"
                f"（数量 {h['quantity']} vs 重放 {replay['quantity']}，成本 {h['cost_price']} 不动）"
            )

        if not mismatches:
            print('无不一致行，无需清理。')
            return 0

        if not args.apply:
            print('\n[dry-run] 未写库。确认无误后执行：python scripts/cleanup_realized_residual.py --apply')
            return 0

        # R11：破坏性操作前自动备份，失败必须中止
        print('\n[R11] 正在备份数据库…')
        if not backup_database(reason='realized_residual_cleanup'):
            print('[中止] 备份失败——按红线 R11 中止清理，数据库未被修改。')
            return 1

        for h, replay in mismatches:
            cursor.execute(
                'UPDATE holdings SET realized_pnl = ?, '
                "updated_at = datetime('now', 'localtime') WHERE id = ?",
                (replay['realized_pnl'], h['id']),
            )
            print(f"  已回写 holding_id={h['id']} realized_pnl → {replay['realized_pnl']}")
        conn.commit()
        print(f'\n[完成] 共回写 {len(mismatches)} 行；数量/成本/流水均未改动。')
        return 0
    finally:
        conn.close()


if __name__ == '__main__':
    raise SystemExit(main())
