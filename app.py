"""
Stock Analyst 主程序
启动方法：python app.py
然后在浏览器打开 http://127.0.0.1:5000

路由层已按业务域拆分为 blueprints/ 包（自 2026-08-13 起）：
  - blueprints/watchlist.py      自选股/分组/个股数据/采集/批量分析
  - blueprints/analysis.py       四维分析/评级/建议/v5 评分演示
  - blueprints/portfolio.py      持仓/组合/流水/成本修正/价格刷新
  - blueprints/report.py         每日报告
  - blueprints/system.py         健康检查/引擎切换/数据库统计
  - blueprints/backtest.py       评级回测/价格回测/自动优化
  - blueprints/export.py         报告导出(Excel)
  - blueprints/index_ratings.py  指数数据与评级
  - blueprints/alerts.py         智能预警规则与扫描
  - blueprints/_utils.py         共享展示层格式化工具函数
本文件仅保留：应用工厂、蓝图注册、首页路由与启动逻辑。
"""

import os
import sys

# 绕过系统代理（避免 Clash/V2Ray 未运行时网络请求失败）
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'

# pythonw（无控制台）启动时 sys.stdout/sys.stderr 为 None：
# 第三方库（如 akshare 的 tqdm 进度条）写入 stderr 会抛
# 'NoneType' object has no attribute 'write'，导致同花顺批量资金流采集
# 主接口崩溃（主接口失败→重试→备选→回退EM，同花顺净额辅助数据全程缺失）。
# 兜底重定向到 devnull，让依赖标准流的库正常工作。
if sys.stdout is None:
    sys.stdout = open(os.devnull, 'w', encoding='utf-8')
if sys.stderr is None:
    sys.stderr = open(os.devnull, 'w', encoding='utf-8')

# 确保能找到项目内的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from blueprints import ALL_BLUEPRINTS
from config import (
    FLASK_DEBUG,
    FLASK_HOST,
    FLASK_PORT,
)
from database.db_manager import init_database
from flask import Flask, render_template

app = Flask(__name__)

# 注册全部业务域蓝图
for _bp in ALL_BLUEPRINTS:
    app.register_blueprint(_bp)


# ============================================================
# 页面路由
# ============================================================


@app.route('/')
def index():
    """首页 —— 数据采集测试页面"""
    return render_template('index.html')


# ============================================================
# 启动
# ============================================================


def main():
    """启动程序"""
    # === 012-A: 全局文件日志配置 ===
    import logging
    from logging.handlers import TimedRotatingFileHandler

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)

    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, 'app.log'), when='midnight', backupCount=7, encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s %(message)s'))
    file_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)  # addHandler 而非 basicConfig，避免冲突

    logging.getLogger(__name__).info(f'===== Stock Analyst 启动 PID={os.getpid()} =====')
    # === 012-A END ===

    print('=' * 60)
    print('  Stock Analyst 智能个股分析与评级系统')
    print('  正在初始化数据库...')
    print('=' * 60)

    init_database()

    # P0-1: 评级配置自检（三处评级定义一致性，不一致时告警但不阻断启动）
    from modules.analysis_engine import validate_rating_config

    rating_issues = validate_rating_config()
    if rating_issues:
        logging.getLogger(__name__).warning(f'[评级配置自检] 发现 {len(rating_issues)} 个问题：')
        print('  [WARN] 评级配置自检发现不一致（评级可能错乱），请检查：')
        print('         config_weights.json / config.py / modules/scoring_engine.py')
        for msg in rating_issues:
            logging.getLogger(__name__).warning(f'[评级配置自检] {msg}')
            print(f'         - {msg}')
    else:
        logging.getLogger(__name__).info('[评级配置自检] 三处评级定义一致（80/65/50/30）')

    # US-11: 启动每日报告定时调度器
    from modules.daily_report import start_scheduler

    start_scheduler()

    # 数据完整性驱动的持续补采调度器（缺口检测 + 自动退避，直到数据完整）
    from modules.backfill_scheduler import start_backfill_scheduler

    start_backfill_scheduler()

    print()
    print('  ============================================================')
    print(f'  [OK] 服务就绪，访问地址：http://{FLASK_HOST}:{FLASK_PORT}')
    print(f'  v5.0 评分引擎演示：http://{FLASK_HOST}:{FLASK_PORT}/api/v5/scoring-demo')
    print(f'  健康检查：          http://{FLASK_HOST}:{FLASK_PORT}/api/health')
    print('  按 Ctrl+C 可以停止程序')
    print('  ============================================================')
    print()

    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)


if __name__ == '__main__':
    main()
