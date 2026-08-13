"""019Q：资金面历史缺口回补脚本（新浪 lscjfb 主力口径，运维侧一次性工具，先例 b26_margin_backfill.py）

用法（项目根目录执行）：
    python scripts/backfill_capital_sina_019q.py 2026-08-07 [--symbols 600519,300750]

对每个 A 股自选股、指定缺口日期按阶梯写回（D-5 裁定）：
    ① 东方财富 push2his（按日期取，EM 真实数据，INSERT OR REPLACE + capital_source=NULL 归位）
    ② 新浪 lscjfb（按 opendate == 目标日期 严格匹配，UPDATE + INSERT OR IGNORE，
       is_estimated=0，capital_source='sina_main'）
THS 无历史当日数据，不参与历史回补。

写入规则与主链路一致：UPDATE + INSERT OR IGNORE（sina 写）、is_estimated=0、
capital_source='sina_main'、严格日期匹配（M-2，严禁"取最新行"）。
不改 app.py / 不入调度，保持零代码用户一键启动面不变，脚本仅开发者/运维使用。

回补后该日行 is_estimated=0 + sina_main，参与后续 5 日均/连续性因子——期望行为
（真实数据），与 019K D-3 已接受的"混用"同范畴。

幂等：sina 写为 UPDATE + INSERT OR IGNORE，可重复运行。
"""

import argparse
import io
import os
import sys

# Windows PowerShell 中文输出兜底
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 设置项目根目录到 sys.path（脚本在 scripts/ 下，需引入上级 modules）
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
os.chdir(project_root)

from database.db_manager import get_connection
from modules.data_collector import (
    _fetch_capital_flow_em_individual,
    _fetch_capital_flow_sina_main,
    _safe_float_pct,
    _safe_float_wan,
    get_stock_id,
)


def _write_em_row(symbol, target_date):
    """阶梯①：东方财富 push2his（按日期取）写回；命中并写入返回 True，否则 False"""
    stock_id = get_stock_id(symbol, 'a_stock')
    if not stock_id:
        return False
    rows = _fetch_capital_flow_em_individual(symbol, 'a_stock')
    if not rows:
        return False
    for row in rows:
        if str(row.get('日期', '') or '').strip() != target_date:
            continue
        # 019N 模式：安全转换（None/NaN/'-'/±Inf → None），金额元→万元，占比不转换
        main_net = _safe_float_wan(row.get('主力净流入-净额'))
        main_net_pct = _safe_float_pct(row.get('主力净流入-净占比'))
        super_large = _safe_float_wan(row.get('超大单净流入-净额'))
        large = _safe_float_wan(row.get('大单净流入-净额'))
        medium = _safe_float_wan(row.get('中单净流入-净额'))
        small = _safe_float_wan(row.get('小单净流入-净额'))
        if all(v is None for v in (main_net, main_net_pct, super_large, large, medium, small)):
            return False
        # 与主链路 EM 写入一致（L2304-2321）：INSERT OR REPLACE + is_estimated=0
        # + capital_source=NULL（真实数据最高优先级，覆盖一切顶替/估算并归位来源）
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            """
            INSERT OR REPLACE INTO raw_capital_flow
            (stock_id, trade_date, main_net_inflow, main_net_inflow_pct,
             super_large_net, large_net, medium_net, small_net, is_estimated, capital_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)
            """,
            (stock_id, target_date, main_net, main_net_pct, super_large, large, medium, small),
        )
        conn.commit()
        conn.close()
        return True
    return False


def _write_sina_row(symbol, target_date):
    """阶梯②：新浪 lscjfb（按日期严格匹配）写回；命中并写入返回 True，否则 False"""
    stock_id = get_stock_id(symbol, 'a_stock')
    if not stock_id:
        return False
    row = _fetch_capital_flow_sina_main(symbol, 'a_stock', target_date)
    if not row:
        return False
    conn = get_connection()
    cur = conn.cursor()
    # 与主链路 sina 顶替一致：无条件 UPDATE（不带来源守卫，可覆盖 THS/估算行）
    # + INSERT OR IGNORE；禁止 INSERT OR REPLACE；is_estimated=0、capital_source='sina_main'
    cur.execute(
        'UPDATE raw_capital_flow SET main_net_inflow=?, super_large_net=?, large_net=?, '
        'medium_net=?, small_net=?, is_estimated=0, capital_source=? '
        'WHERE stock_id=? AND trade_date=?',
        (
            row['main_net_inflow'],
            row['super_large_net'],
            row['large_net'],
            row['medium_net'],
            row['small_net'],
            'sina_main',
            stock_id,
            target_date,
        ),
    )
    if cur.rowcount == 0:
        cur.execute(
            'INSERT OR IGNORE INTO raw_capital_flow '
            '(stock_id, trade_date, main_net_inflow, super_large_net, large_net, '
            'medium_net, small_net, is_estimated, capital_source) '
            'VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)',
            (
                stock_id,
                target_date,
                row['main_net_inflow'],
                row['super_large_net'],
                row['large_net'],
                row['medium_net'],
                row['small_net'],
                'sina_main',
            ),
        )
    conn.commit()
    conn.close()
    return True


def main():
    parser = argparse.ArgumentParser(description='019Q 资金面历史缺口回补（EM push2his → 新浪 lscjfb 阶梯）')
    parser.add_argument('date', help='缺口日期 YYYY-MM-DD')
    parser.add_argument('--symbols', default=None, help='逗号分隔的股票代码列表，默认全部 A 股自选股')
    args = parser.parse_args()

    target_date = args.date.strip()

    if args.symbols:
        symbols = [s.strip() for s in args.symbols.split(',') if s.strip()]
    else:
        conn = get_connection()
        cur = conn.cursor()
        cur.execute(
            "SELECT symbol FROM stocks WHERE market = 'a_stock' AND status = 'active' ORDER BY symbol"
        )
        symbols = [r['symbol'] for r in cur.fetchall()]
        conn.close()

    print('===== 019Q 资金面历史缺口回补 =====')
    print(f'目标日期: {target_date}，股票数: {len(symbols)}')

    em_ok = 0
    sina_ok = 0
    skipped = 0
    for i, symbol in enumerate(symbols, 1):
        print(f'[{i}/{len(symbols)}] {symbol} ...', end=' ', flush=True)
        try:
            if _write_em_row(symbol, target_date):
                em_ok += 1
                print('EM push2his 写回')
            elif _write_sina_row(symbol, target_date):
                sina_ok += 1
                print('新浪 lscjfb 写回（sina_main）')
            else:
                skipped += 1
                print('两源均无当日数据，跳过')
        except Exception as e:
            skipped += 1
            print(f'异常: {e}')

    print('\n===== 回补结果 =====')
    print(f'EM 写回: {em_ok}  新浪写回: {sina_ok}  跳过/异常: {skipped}')


if __name__ == '__main__':
    main()
