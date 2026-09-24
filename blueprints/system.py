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
#
# 021BN 口径修正（实测缺陷：红行几乎全是误报）：
# 1) skipped（"港股无此数据源" / "当日重复采集自动跳过"）不再计入成功率的分子分母
#    ——旧口径下港股占比越高、节流越正常，表越红；
# 2) north_capital（北向资金）2024-08-16 政策停更，标记 grey 并排除出告警判定，
#    每日"采集成功"读的只是冻结缓存，成功≠新鲜；
# 3) error_logs 中非数据源模块（应用层提示日志）不进"数据源健康度"。
# ============================================================

# 维度接口说明（前端"说明"列）：采集内容 + 数据源链路 + 已知限制
_DIMENSION_META = {
    'kline': {'label': 'K线行情', 'desc': '日K线：腾讯财经 → 通达信mootdx 备援'},
    'fundamental': {'label': '财务摘要', 'desc': '财报摘要：新浪抽象财报 + 腾讯PE/PB（季度级更新）'},
    'capital': {'label': '个股资金流', 'desc': '主力资金：东财 → 腾讯自选股 → 新浪 多级降级'},
    'sentiment': {'label': '新闻情绪', 'desc': '个股新闻与情绪：东方财富'},
    'valuation': {'label': '估值PE/PB', 'desc': '实时估值：腾讯（每日首采，当日重复自动跳过——跳过多是正常的）'},
    'express': {'label': '业绩快报', 'desc': '业绩快报：东方财富（港股无此数据源，跳过属正常）'},
    'forecast': {'label': '业绩预告', 'desc': '业绩预告：东方财富（港股无此数据源，跳过属正常）'},
    'orderbook': {'label': '五档盘口', 'desc': '五档盘口：通达信mootdx（港股不支持，非交易时段跳过）。'
                 '021BX：2026-09-10 起 TDX 服务端停止返回数据（全部服务器连通但空载荷，'
                 'mootdx 0.11.7 实测），属采集源连通性问题——盘口为展示维度，'
                 '评分链不消费，停更不影响评级（恢复后自动续采）'},
    'restricted_release': {'label': '限售解禁', 'desc': '限售解禁时间表：东方财富（港股无此数据，跳过属正常）'},
    'north_capital': {'label': '北向资金', 'desc': '北向资金：2024-08-16 政策停更，展示冻结缓存（不计入告警）'},
    'valuation_history': {'label': '估值历史', 'desc': '估值历史序列：腾讯'},
}

# 已知停更维度：数据源政策性断供，健康度标灰并排除出红黄绿告警
_DISCONTINUED_DIMENSIONS = {'north_capital'}

# 非数据源的 error_logs 模块（应用层提示日志），不进"数据源健康度"
_NON_SOURCE_MODULES = {'prefill_analytics'}


def _level_of(success_rate, consecutive_failures, hours_since_last_ok, has_try):
    """维度级健康度：红=成功率<50% 或 连续失败≥3；黄=近7天有失败 或 距上次成功>48h；绿=其余。

    021BN：success_rate 可为 None（如全部记录为 skipped，无有效分母）→ 不判红黄，
    视为绿——没有任何真实尝试失败过，不该告警。
    """
    rate_bad = success_rate is not None and (
        success_rate < 0.5 or consecutive_failures >= 3
    )
    if has_try and rate_bad:
        return 'red'
    rate_imperfect = success_rate is not None and success_rate < 1.0
    if has_try and (rate_imperfect or (hours_since_last_ok is not None and hours_since_last_ok > 48)):
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

    # ---- 按维度聚合 data_status ----
    # 021BN 口径：skipped（节流/维度不适用）不计入成功率分子分母，也不算连续失败——
    # 连续失败 = 同股同维度自最新记录起、跳过 skipped 后连续 failed/error 段长度
    #（success/partial/estimated 视为"拿到数据"即定稿），取各股最大。
    dims = {}
    streaks = {}
    done = set()
    for r in rows:
        status = r['status'] or ''
        skipped = status == 'skipped'
        ok = status == 'success'
        has_data = status in ('success', 'partial', 'estimated')
        d = dims.setdefault(
            r['dimension'],
            {'dimension': r['dimension'], 'total': 0, 'ok': 0, 'skipped': 0, 'last_ok': None, 'last_try': None},
        )
        d['total'] += 1
        if skipped:
            d['skipped'] += 1
        if ok:
            d['ok'] += 1
            if d['last_ok'] is None:
                d['last_ok'] = r['fetched_at']
        if d['last_try'] is None:
            d['last_try'] = r['fetched_at']
        if skipped:
            continue  # 节流/不适用：不算失败，也不定稿失败段（物理上等于"这次没尝试"）
        key = (r['stock_id'], r['dimension'])
        if key not in done:
            if has_data:
                done.add(key)  # 领先失败段结束（拿到数据即定稿）
            else:
                streaks[key] = streaks.get(key, 0) + 1

    dim_list = []
    for d in dims.values():
        effective_total = d['total'] - d['skipped']
        rate = (d['ok'] / effective_total) if effective_total else None
        consec = max([v for (sid, dim), v in streaks.items() if dim == d['dimension']] or [0])
        hours_ok = (
            (now - datetime.strptime(d['last_ok'], '%Y-%m-%d %H:%M:%S')).total_seconds() / 3600
            if d['last_ok'] else None
        )
        meta = _DIMENSION_META.get(d['dimension'], {})
        discontinued = d['dimension'] in _DISCONTINUED_DIMENSIONS
        dim_list.append(
            {
                **d,
                'success_rate': round(rate, 4) if rate is not None else None,
                'consecutive_failures': consec,
                # 021BN：维度接口说明（前端"说明"列）
                'label': meta.get('label', d['dimension']),
                'desc': meta.get('desc', ''),
                'discontinued': discontinued,
                # 停更维度固定灰灯：数据源已死是已知事实，不是需要处理的异常
                'level': 'grey' if discontinued else _level_of(rate, consec, hours_ok, d['total'] > 0),
            }
        )
    dim_list.sort(key=lambda x: {'red': 0, 'yellow': 1, 'green': 2, 'grey': 3}[x['level']])

    # ---- 按数据源模块聚合 error_logs ----
    mods = {}
    for r in err_rows:
        if (r['module'] or '') in _NON_SOURCE_MODULES:
            continue  # 021BN：应用层提示日志不是数据源
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

    # 整体分级：停更维度（grey）是已知事实，不参与最差级别判定
    levels = [d['level'] for d in dim_list if d['level'] != 'grey'] + [m['level'] for m in mod_list]
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
