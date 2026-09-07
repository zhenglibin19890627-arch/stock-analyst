"""健康检查/数据库统计 API 蓝图。

021AE：引擎灰度管理 API（/api/engine/status、/api/engine/rollback-all）随经典引擎
一并删除——灰度已完成（all_v5 稳定运行超一个月），rollback-all 无处可退且误触发有害。
"""

from flask import Blueprint, jsonify

from config import FLASK_PORT
from database.db_manager import get_connection

bp = Blueprint('system', __name__)

@bp.route('/api/db-stats', methods=['GET'])
def api_db_stats():
    """数据库统计信息"""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}
    tables = [
        'stocks',
        'raw_kline',
        'raw_fundamental',
        'raw_capital_flow',
        'raw_sentiment',
        'data_status',
        'news_sentiment',
        'error_logs',
    ]
    for table in tables:
        cursor.execute(f'SELECT COUNT(*) as count FROM {table}')
        stats[table] = cursor.fetchone()['count']

    conn.close()
    return jsonify({'success': True, 'stats': stats})


@bp.route('/api/health', methods=['GET'])
def api_health():
    """健康检查接口（⚠️ 仅运维：watchdog 每分钟巡检 + start.bat 启动校验依赖此端点，勿删）"""
    return jsonify(
        {
            'success': True,
            'status': 'running',
            'service': 'Stock Analyst',
            'version': 'v5.0',
            'port': FLASK_PORT,
        }
    )


# ============================================================
# OPT-8（2026-09-07）：数据源健康度视图（只读，不新增采集行为）
# 聚合 data_status（维度 × 近7天成功率/最后成功/连续失败）与
# error_logs（模块 × 近7天错误数/最后错误时间），红/黄/绿一眼可见。
# ============================================================


def _level_of(success_rate, consecutive_failures, hours_since_last_ok, has_try):
    """维度级健康度：红=成功率<50% 或 连续失败≥3；黄=近7天有失败 或 距上次成功>48h；绿=其余。"""
    if has_try and (success_rate < 0.5 or consecutive_failures >= 3):
        return 'red'
    if (has_try and success_rate < 1.0) or (hours_since_last_ok is not None and hours_since_last_ok > 48):
        return 'yellow'
    return 'green'


@bp.route('/api/health/sources', methods=['GET'])
def api_health_sources():
    """数据源健康度：近 7 天（数据源 × 维度）聚合。只读。"""
    from datetime import datetime, timedelta

    from modules.collector._env import now_cn

    now = datetime.strptime(now_cn(), '%Y-%m-%d %H:%M:%S')
    cutoff = (now - timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')

    conn = get_connection()
    rows = conn.execute(
        """SELECT stock_id, dimension, status, fetched_at
           FROM data_status WHERE fetched_at >= ?
           ORDER BY stock_id, dimension, fetched_at DESC""",
        (cutoff,),
    ).fetchall()
    err_rows = conn.execute(
        """SELECT module, dimension, error_type, error_message, created_at
           FROM error_logs WHERE created_at >= ?
           ORDER BY created_at DESC""",
        (cutoff,),
    ).fetchall()
    conn.close()

    # ---- 按维度聚合 data_status（连续失败 = 同股同维度自最新记录起领先失败段长度，遇成功定稿，取各股最大）----
    dims = {}
    streaks = {}
    done = set()
    for r in rows:
        d = dims.setdefault(
            r['dimension'],
            {'dimension': r['dimension'], 'total': 0, 'ok': 0, 'last_ok': None, 'last_try': None},
        )
        d['total'] += 1
        ok = (r['status'] or '') == 'success'
        if ok:
            d['ok'] += 1
            if d['last_ok'] is None:
                d['last_ok'] = r['fetched_at']
        if d['last_try'] is None:
            d['last_try'] = r['fetched_at']
        key = (r['stock_id'], r['dimension'])
        if key not in done:
            if ok:
                done.add(key)  # 领先失败段结束（成功定稿）
            else:
                streaks[key] = streaks.get(key, 0) + 1

    dim_list = []
    for d in dims.values():
        rate = (d['ok'] / d['total']) if d['total'] else None
        consec = max([v for (sid, dim), v in streaks.items() if dim == d['dimension']] or [0])
        hours_ok = (
            (now - datetime.strptime(d['last_ok'], '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600
            if d['last_ok'] else None
        )
        dim_list.append(
            {
                **d,
                'success_rate': round(rate, 4) if rate is not None else None,
                'consecutive_failures': consec,
                'level': _level_of(rate if rate is not None else 0, consec, hours_ok, d['total'] > 0),
            }
        )
    dim_list.sort(key=lambda x: {'red': 0, 'yellow': 1, 'green': 2}[x['level']])

    # ---- 按数据源模块聚合 error_logs ----
    mods = {}
    for r in err_rows:
        m = mods.setdefault(
            r['module'],
            {'module': r['module'], 'errors_7d': 0, 'last_error_at': None, 'dimensions': set()},
        )
        m['errors_7d'] += 1
        if m['last_error_at'] is None:
            m['last_error_at'] = r['created_at']
        if r['dimension']:
            m['dimensions'].add(r['dimension'])
    mod_list = []
    for m in mods.values():
        hours_err = (
            (now - datetime.strptime(m['last_error_at'], '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600
            if m['last_error_at'] else None
        )
        mod_list.append(
            {
                'module': m['module'],
                'errors_7d': m['errors_7d'],
                'last_error_at': m['last_error_at'],
                'dimensions': sorted(m['dimensions']),
                'level': 'red' if (hours_err is not None and hours_err <= 24) else 'yellow',
            }
        )
    mod_list.sort(key=lambda x: (x['last_error_at'] or ''), reverse=True)

    levels = [d['level'] for d in dim_list] + [m['level'] for m in mod_list]
    overall = 'red' if 'red' in levels else ('yellow' if 'yellow' in levels else 'green')

    return jsonify(
        {
            'success': True,
            'window_days': 7,
            'overall_level': overall,
            'generated_at': now_cn(),
            'dimensions': dim_list,
            'sources': mod_list,
        }
    )


# ============================================================
# M8-BACKTEST-003：评级有效性回测 API
# ============================================================
