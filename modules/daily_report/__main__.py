"""命令行入口（t6 拆包）：保留原 modules/daily_report.py 的 __main__ CLI。

原 `python modules/daily_report.py --date/--list` 直接执行路径随包化消失，
等价入口为 `python -m modules.daily_report --date/--list`（本文件，代码逐字保留）。
"""

import json
import logging

from modules.daily_report import generate_daily_report, get_report_history

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    import argparse

    parser = argparse.ArgumentParser(description='US-11 每日报告生成')
    parser.add_argument('--date', type=str, default=None, help='报告日期 YYYY-MM-DD')
    parser.add_argument('--list', action='store_true', help='查看历史报告列表')
    args = parser.parse_args()

    if args.list:
        history = get_report_history(page=1, page_size=10)
        print(json.dumps(history, ensure_ascii=False, indent=2))
    else:
        result = generate_daily_report(args.date)
        print(
            json.dumps(
                {k: v for k, v in result.items() if k != 'markdown'}, ensure_ascii=False, indent=2
            )
        )
