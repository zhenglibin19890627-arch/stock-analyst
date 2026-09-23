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

from flask import Flask, render_template

from blueprints import ALL_BLUEPRINTS
from config import (
    FLASK_DEBUG,
    FLASK_HOST,
    FLASK_PORT,
)
from database.db_manager import init_database

app = Flask(__name__)

# 注册全部业务域蓝图
for _bp in ALL_BLUEPRINTS:
    app.register_blueprint(_bp)


# ============================================================
# 020S：HTML 与静态资源禁止缓存——用户每次刷新都拿到最新前端改动。
# 此前浏览器缓存旧 HTML（引用旧版本号资源），前端修改一直"看不到"。
# ============================================================


@app.after_request
def _no_cache_html_static(resp):
    from flask import request as _request

    if _request.path == '/' or _request.path.startswith('/static/'):
        resp.headers['Cache-Control'] = 'no-cache, no-store, must-revalidate'
        resp.headers['Pragma'] = 'no-cache'
        resp.headers['Expires'] = '0'
    return resp


# ============================================================
# 页面路由
# ============================================================


@app.route('/')
def index():
    """首页 —— 数据采集测试页面

    020S：静态资源版本号自动跟随文件修改时间（mtime），
    配合 after_request 禁止缓存，前端改动无需手动升级版本号即可生效。
    """
    import os as _os

    _base = _os.path.dirname(_os.path.abspath(__file__))

    def _ver(rel_path):
        try:
            return str(int(_os.path.getmtime(_os.path.join(_base, rel_path))))
        except OSError:
            return '0'

    # OPT-4（2026-09-07）：app.js 按业务域拆分为 8 文件，按依赖顺序加载（dict 保序）
    js_versions = {
        name: _ver(f'static/js/{name}.js')
        for name in ('core', 'watchlist', 'analysis', 'portfolio',
                     'backtest', 'alerts', 'market', 'boot')
    }

    return render_template(
        'index.html',
        css_version=_ver('static/css/app.css'),
        js_versions=js_versions,
        # OPT-7：本地化第三方库按各自 mtime 独立编版本
        echarts_version=_ver('static/vendor/echarts.min.js'),
        marked_version=_ver('static/vendor/marked.min.js'),
    )


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

    # === 021BN-c：实例唯一性守卫 ===
    # 实测缺陷（2026-09-08）：start.bat 与每分钟巡检的 watchdog 在"杀旧进程→绑定端口"
    # 的间隙撞车，各拉起一份 app.py；输掉绑定竞争的实例带着全套调度器变僵尸，
    # 所有采集双倍触发（日志成对）、浪费东财接口配额加剧风控。守卫：端口已有健康
    # 实例时本进程立即退出；app.run 绑定失败也显式退出（不再带调度器苟活）。
    import urllib.request

    try:
        with urllib.request.urlopen(
            f'http://127.0.0.1:{FLASK_PORT}/api/health', timeout=2
        ) as _resp:
            import json as _json

            if _json.loads(_resp.read().decode('utf-8')).get('status') == 'running':
                logging.getLogger(__name__).warning(
                    '===== 端口 %s 已有运行实例，本进程 PID=%s 退出（防双实例双倍采集）=====',
                    FLASK_PORT, os.getpid(),
                )
                print(f'[提示] 端口 {FLASK_PORT} 已有 Stock Analyst 实例在运行，本进程退出。')
                sys.exit(0)
    except Exception:  # noqa: BLE001 —— 探测失败=端口无服务，正常继续启动（绑定仍会仲裁）
        pass

    print('=' * 60)
    print('  Stock Analyst 智能个股分析与评级系统')
    print('  正在初始化数据库...')
    print('=' * 60)

    init_database()

    # 2026-09-07：每日自动数据库备份（幂等；服务常驻期间由补采 tick 每日续备）
    try:
        from database.db_manager import auto_backup_db

        auto_backup_db()
    except Exception as e:  # noqa: BLE001 —— 备份失败不阻断启动
        logging.getLogger(__name__).warning(f'[自动备份] 启动备份失败: {e}')

    # P0-1: 评级配置自检（三处评级定义一致性，不一致时告警但不阻断启动）
    from modules.rating_config import validate_rating_config

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

    # 021BT：盘中巡检调度器（仅交易时段活动；失败静默降级；收盘确认口径零改动；
    # 巡检写库面仅 price_cache，raw_kline/预警/新表零写入）
    try:
        from modules.intraday_patrol import start_intraday_patrol

        start_intraday_patrol()
    except Exception as e:  # noqa: BLE001 —— 巡检启动失败不阻断主服务
        logging.getLogger(__name__).warning(f'[盘中巡检] 启动失败（本次运行无盘中感知）: {e}')

    print()
    print('  ============================================================')
    print(f'  [OK] 服务就绪，访问地址：http://{FLASK_HOST}:{FLASK_PORT}')
    print(f'  v5.0 评分引擎演示：http://{FLASK_HOST}:{FLASK_PORT}/api/v5/scoring-demo')
    print(f'  健康检查：          http://{FLASK_HOST}:{FLASK_PORT}/api/health')
    print('  按 Ctrl+C 可以停止程序')
    print('  ============================================================')
    print()

    # 021BN-c：绑定失败（输给并起实例）必须显式退出——调度器已启动的进程
    # 若在此苟活，就成了无端口但持续采集的僵尸实例（当日实测根源）。
    try:
        app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)
    except OSError as e:
        logging.getLogger(__name__).error(
            '===== 端口 %s 绑定失败（已有实例运行？）：%s → 本进程退出 =====', FLASK_PORT, e
        )
        sys.exit(1)


if __name__ == '__main__':
    main()
