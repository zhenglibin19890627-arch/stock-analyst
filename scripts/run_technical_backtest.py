# -*- coding: utf-8 -*-
"""020R-51-B：技术面专项历史回测一键入口

用法（项目根目录）：
    python scripts\\run_technical_backtest.py          # 全部市场
    python scripts\\run_technical_backtest.py a_stock  # 仅 A 股
    python scripts\\run_technical_backtest.py hk_stock # 仅港股

输出：reports/technical_backtest_YYYYMMDD.md
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.technical_backtest import run_and_save  # noqa: E402


def main():
    arg = sys.argv[1] if len(sys.argv) > 1 else 'all'
    market = None if arg == 'all' else arg
    res, path = run_and_save(market=market)
    print(f"样本数={res['samples']}  报告={path}")
    ov = res['overall']
    print(
        f"整体: 观测={ov['n_all']}  T+5均={ov['t5']['avg']}%  T+20均={ov['t20']['avg']}%  "
        f"T+20方向命中={ov['t20']['dir_hit']}  基准T+20={res['benchmark_avg_ret20']}%"
    )
    for b in ('偏多', '中性', '偏空'):
        st = res['buckets'][b]
        print(
            f"{b}: 观测={st['n_all']}  T+5均={st['t5']['avg']}%  T+20均={st['t20']['avg']}%  "
            f"T+20方向命中={st['t20']['dir_hit']}"
        )
    if res['skipped']:
        print(f"跳过(日线不足60根): {', '.join(res['skipped'])}")


if __name__ == '__main__':
    main()
