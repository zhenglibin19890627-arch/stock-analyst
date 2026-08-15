#!/usr/bin/env python3
"""数据源三方校准脚本：东财 vs 腾讯(westock) vs 新浪(lscjfb) 主力净流入

背景：东财恢复后，定量比较两个备用源"谁更接近东财"（平均绝对误差 /
方向一致率 / 反号样本），为资金面降级链路排序提供数据依据。
结果写入 logs/source_calibration_<日期>.md 并打印摘要。

用法（项目根目录执行）：
    python scripts/calibrate_sources.py

注意：
  - 仅在东财可用的交易日运行（否则脚本会检测到东财不可用并提示跳过）；
  - 校准期间临时移开东财熔断状态文件，结束后恢复——不影响运行中应用的熔断；
  - 全程约 5~8 分钟（23 只 A 股 × 三源）。
"""
import os
import sys
import time
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import modules.data_collector as dc
from database.db_manager import get_connection

BAN_FILE = dc._EM_BAN_STATE_FILE
BAN_BAK = BAN_FILE + '.calibration_bak'


def _backup_ban_state():
    if os.path.exists(BAN_FILE):
        os.rename(BAN_FILE, BAN_BAK)
        return True
    return False


def _restore_ban_state():
    if os.path.exists(BAN_BAK):
        os.rename(BAN_BAK, BAN_FILE)


def main():
    print('=' * 60)
    print('  数据源三方校准：东财 vs 腾讯 vs 新浪（主力净流入，万元）')
    print('=' * 60)

    banned_bak = _backup_ban_state()
    try:
        # 1. 东财可用性探测（600276）
        print('[1/4] 探测东财可用性（600276）...')
        probe = dc._fetch_capital_flow_em_individual('600276', 'a_stock')
        if not probe:
            print('[X] 东财仍不可用，校准跳过。请在东财恢复后重新运行。')
            return 1
        target_date = probe[-1].get('日期', '')
        print(f'[OK] 东财可用，校准基准日: {target_date}')

        # 2. 自选股 A 股清单
        conn = get_connection()
        symbols = [r['symbol'] for r in conn.execute(
            "SELECT symbol FROM stocks WHERE status='active' AND market='a_stock' ORDER BY symbol")]
        conn.close()
        print(f'[2/4] 待校准 A 股: {len(symbols)} 只')

        # 3. 三方采集
        rows = []
        for i, sym in enumerate(symbols, 1):
            em = w = s = None
            try:
                em_rows = dc._fetch_capital_flow_em_individual(sym, 'a_stock')
                for r in (em_rows or []):
                    if str(r.get('日期', '')) == target_date:
                        em = dc._safe_float_wan(r.get('主力净流入-净额'))
            except Exception:
                pass
            try:
                wr = dc._fetch_capital_flow_westock(sym, 'a_stock')
                if wr and wr.get('trade_date') == target_date:
                    w = wr.get('main_net_inflow')
            except Exception:
                pass
            try:
                sr = dc._fetch_capital_flow_sina_main(sym, 'a_stock', target_date=target_date)
                s = sr['main_net_inflow'] if sr else None
            except Exception:
                pass
            rows.append((sym, em, w, s))
            status = ''.join('+' if v is not None else '.' for v in (em, w, s))
            print(f'  [{i}/{len(symbols)}] {sym} 东财/腾讯/新浪={status}')
            time.sleep(0.5)

        # 4. 统计
        def diff(a, b):
            return abs(a - b) if a is not None and b is not None else None

        em_w = [(em, w) for _, em, w, _ in rows if em is not None and w is not None]
        em_s = [(em, s) for _, em, _, s in rows if em is not None and s is not None]
        flips_w = sum(1 for em, w in em_w if em * w < 0)
        flips_s = sum(1 for em, s in em_s if em * s < 0)
        mae_w = (sum(diff(e, w) for e, w in em_w) / len(em_w)) if em_w else None
        mae_s = (sum(diff(e, s) for e, s in em_s) / len(em_s)) if em_s else None
        agree_w = (1 - flips_w / len(em_w)) if em_w else None
        agree_s = (1 - flips_s / len(em_s)) if em_s else None

        print('\n[4/4] 校准结果摘要：')
        print(f'  腾讯 vs 东财: 样本 {len(em_w)} 只 | 平均绝对误差 {mae_w:,.2f} 万 | 方向一致率 {agree_w:.1%} | 反号 {flips_w} 只')
        print(f'  新浪 vs 东财: 样本 {len(em_s)} 只 | 平均绝对误差 {mae_s:,.2f} 万 | 方向一致率 {agree_s:.1%} | 反号 {flips_s} 只')
        winner = '腾讯' if (mae_w or 1e18) < (mae_s or 1e18) else '新浪'
        print(f'  => 更接近东财的备用源: 【{winner}】')

        out = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            'logs', f'source_calibration_{target_date}.md',
        )
        with open(out, 'w', encoding='utf-8') as f:
            f.write(f'# 数据源三方校准报告（{target_date}）\n\n')
            f.write(f'生成时间: {datetime.now():%Y-%m-%d %H:%M:%S}\n\n')
            f.write('| 代码 | 东财(万) | 腾讯(万) | 新浪(万) | 腾讯差 | 新浪差 |\n')
            f.write('|---|---|---|---|---|---|\n')
            for sym, em, w, s in rows:
                f.write(
                    f'| {sym} | {em if em is not None else "-"} | '
                    f'{w if w is not None else "-"} | {s if s is not None else "-"} | '
                    f'{diff(em, w) if diff(em, w) is not None else "-"} | '
                    f'{diff(em, s) if diff(em, s) is not None else "-"} |\n'
                )
            f.write(f'\n- 腾讯 vs 东财：样本 {len(em_w)}，MAE {mae_w}，方向一致率 {agree_w}，反号 {flips_w}\n')
            f.write(f'- 新浪 vs 东财：样本 {len(em_s)}，MAE {mae_s}，方向一致率 {agree_s}，反号 {flips_s}\n')
        print(f'\n报告已写入: {out}')
        return 0
    finally:
        if banned_bak:
            _restore_ban_state()


if __name__ == '__main__':
    sys.exit(main())
