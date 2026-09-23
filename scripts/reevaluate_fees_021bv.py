"""021BV 银河费用修复：存量 commission_estimated=1 流水重估 + 持仓全量重算。

背景（t1 诊断 docs/reports/021bv_fee_diag_20260923.md + 队长裁定，用户预授权）：
config.TRADE_FEE_BROKERS 银河档 commission_min 5.0→0.0（免 5）。本脚本把该参数
变更落到存量数据：重估全部 estimated 流水（银河 7 笔预期 56.92→36.93 形态；
东财档未变更、重估为等值幂等），随后对涉及的（股票, 账户）逐对执行
_recalculate_holding 全量重算，并按 021BK/eaf9369 语义清理重算口径持仓的
is_cost_adjusted 标记（成本已回归原始流水口径；position_cost_adjustments
审计记录保留）。

安全契约（R11 / 021BK / 任务边界）：
  1. 默认 dry-run 零写库；仅 --apply 落库；
  2. --apply 先经 db_manager.backup_database 在线热备份，备份失败（返回 None）
     立即中止、零写入（红线 R11）；
  3. 只回写 commission_estimated=1 的行；用户实填/交割单行（est=0）绝不触碰
     （含银河 33 笔 comm=0 批量导入旧行——021BK 既定决策不补估）；
  4. commission_estimated 标记保持 1（仍是估算口径，交割单到了仍可改实际值）；
  5. T+1 锁、B24、R7、classify_stage 零触碰（本脚本不走编辑路由，仅数据修复）；
  6. 单事务（BEGIN IMMEDIATE）提交全部更新+重算，失败整体回滚。

用法（项目根目录执行）：
  python scripts/reevaluate_fees_021bv.py            # dry-run（默认）
  python scripts/reevaluate_fees_021bv.py --apply    # 备份→重估→重算→清标记
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from blueprints.portfolio.trades import _recalculate_holding  # noqa: E402
from database.db_manager import backup_database, get_connection  # noqa: E402
from modules.trade_fees import estimate_trade_fee  # noqa: E402


def collect_estimated_rows(cursor):
    """全部 buy/sell 估算流水（含账户名/市场，供费率匹配与金额回退）。

    仅 commission_estimated=1 行进入本函数视野——est=0（用户实填）在 SQL 层即
    被排除，从源头保证「用户实填行绝不碰」。
    """
    cursor.execute(
        """
        SELECT tr.id, tr.stock_id, tr.account_id, tr.trade_type,
               tr.price, tr.quantity, tr.amount, tr.commission, tr.trade_date,
               a.name AS account_name, s.market AS market, s.symbol AS symbol
        FROM trade_records tr
        JOIN accounts a ON a.id = tr.account_id
        JOIN stocks s ON s.id = tr.stock_id
        WHERE tr.commission_estimated = 1 AND tr.trade_type IN ('buy', 'sell')
        ORDER BY tr.id
        """
    )
    return [dict(r) for r in cursor.fetchall()]


def build_plan(rows):
    """逐行重估并生成计划（纯函数，零写库）。

    金额口径与录入端一致（021BL）：amount>0 用 amount，否则回退 price×quantity。
    Returns: (plan_rows, affected_pairs)
      plan_rows: [{...原行, 'fee_amount', 'new_commission', 'delta'}]
      affected_pairs: {(stock_id, account_id)}——需要 _recalculate_holding 的对
      （含等值行：幂等重算兼作持仓行缺失修复路径，t1 §6）
    """
    plan = []
    affected = set()
    for r in rows:
        amount = float(r['amount'] or 0)
        if amount <= 0:
            amount = float(r['price'] or 0) * int(r['quantity'] or 0)
        new_fee = estimate_trade_fee(
            r['trade_type'], amount,
            account_name=r['account_name'], market=r['market'],
        )
        old = float(r['commission'] or 0)
        plan.append({**r, 'fee_amount': round(amount, 2),
                     'new_commission': new_fee, 'delta': round(new_fee - old, 2)})
        affected.add((r['stock_id'], r['account_id']))
    return plan, affected


def print_plan(plan, affected, mode):
    """打印计划/结果清单与汇总（dry-run 与 apply 共用版式）。"""
    changed = [p for p in plan if p['delta'] != 0]
    print(f'[{mode}] estimated 流水 {len(plan)} 笔，涉及（股票,账户）对 '
          f'{len(affected)} 组；有数值变化 {len(changed)} 笔')
    print(f"{'id':>5} {'日期':<10} {'账户':<8} {'代码':<8} {'类型':<5} "
          f"{'金额':>12} {'旧佣金':>9} {'新佣金':>9} {'Δ':>9}")
    for p in plan:
        mark = '' if p['delta'] == 0 else ('  +' if p['delta'] > 0 else '  -')
        print(f"{p['id']:>5} {str(p['trade_date'] or ''):<10} "
              f"{str(p['account_name'] or '')[:7]:<8} {str(p['symbol'] or ''):<8} "
              f"{p['trade_type']:<5} {p['fee_amount']:>12,.2f} "
              f"{float(p['commission'] or 0):>9.2f} {p['new_commission']:>9.2f} "
              f"{p['delta']:>9.2f}{mark}")
    sum_old = sum(float(p['commission'] or 0) for p in plan)
    sum_new = sum(p['new_commission'] for p in plan)
    print(f'合计：旧 {sum_old:.2f} → 新 {sum_new:.2f}（Δ {sum_new - sum_old:+.2f}）')
    if changed:
        print('变更明细（对账 t1 情景 A：银河 7 笔 56.92→36.93）：')
        for p in changed:
            print(f"  id={p['id']} {p['account_name']} {p['trade_type']} "
                  f"{p['fee_amount']:,.2f} → {p['new_commission']:.2f}")


def apply_plan(plan, affected):
    """落库：R11 备份（失败即中止）→ 单事务重估+重算+清 is_cost_adjusted。

    Returns: (exit_code, message)
    """
    backup_path = backup_database('021bv_fee_reeval')
    if not backup_path:
        msg = 'R11 备份失败（backup_database 返回 None）——已中止，零写入'
        print(f'[中止] {msg}')
        return 1, msg

    conn = get_connection()
    try:
        cursor = conn.cursor()
        conn.execute('BEGIN IMMEDIATE')
        updated = 0
        for p in plan:
            if p['delta'] == 0:
                continue  # 等值行不回写（幂等）
            cursor.execute(
                'UPDATE trade_records SET commission = ? WHERE id = ? '
                'AND commission_estimated = 1',
                (p['new_commission'], p['id']),
            )
            if cursor.rowcount != 1:
                conn.rollback()
                msg = f"行 id={p['id']} 更新计数异常（rowcount={cursor.rowcount}）——已回滚"
                print(f'[错误] {msg}')
                return 1, msg
            updated += 1

        recalc = []
        for stock_id, account_id in sorted(affected):
            recalc.append(_recalculate_holding(cursor, stock_id, account_id))
            # 021BK/eaf9369 语义：重算口径=原始流水成本 → 清「已人工修正」标记
            # （cost_adjustments 审计记录保留，仅状态位收敛）
            cursor.execute(
                'UPDATE holdings SET is_cost_adjusted = 0 '
                'WHERE stock_id = ? AND account_id = ?',
                (stock_id, account_id),
            )
        conn.commit()
    except Exception as e:  # noqa: BLE001 —— 任何异常整体回滚，不留半截状态
        try:
            conn.rollback()
        except Exception:  # noqa: BLE001
            pass
        msg = f'落库失败（已整体回滚，可用备份 {backup_path} 核对）: {e}'
        print(f'[错误] {msg}')
        return 1, msg
    finally:
        conn.close()

    print(f'[apply] R11 备份: {backup_path}')
    print(f'[apply] 佣金回写 {updated} 笔（est 标记保持 1；est=0 实填行未触碰）')
    print(f'[apply] 持仓重算 + is_cost_adjusted 清零 {len(recalc)} 组：')
    for h in recalc:
        print(f"  stock={h['stock_id']} account={h['account_id']} → "
              f"qty={h['quantity']} cost={h['avg_cost']} "
              f"realized={h['realized_pnl']} status={h['status']}")
    print('回退路径：config 银河档 commission_min 改回 5.0 后重跑本脚本 --apply'
          '（或从上述备份恢复）')
    return 0, 'ok'


def main(argv=None):
    parser = argparse.ArgumentParser(
        description='021BV 银河费用修复：estimated 流水重估 + 持仓重算（默认 dry-run）')
    parser.add_argument('--apply', action='store_true',
                        help='落库（先 R11 备份，失败即中止）；缺省仅打印计划')
    args = parser.parse_args(argv)

    conn = get_connection()
    try:
        rows = collect_estimated_rows(conn.cursor())
    finally:
        conn.close()
    plan, affected = build_plan(rows)

    if not args.apply:
        print_plan(plan, affected, 'dry-run 零写库')
        print('确认无误后执行：python scripts/reevaluate_fees_021bv.py --apply')
        return 0
    code, _ = apply_plan(plan, affected)
    return code


if __name__ == '__main__':
    sys.exit(main())
