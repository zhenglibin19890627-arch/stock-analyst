"""汇总与查询域（t6 拆包）：批次汇总 Markdown + 评分差异监控 + 报告查询 API。

_REPORTS_DIR 原居单文件模块头，随唯一消费方 _build_markdown_summary 单宿本模块
（tests/test_daily_report_summary 的 monkeypatch 指向本模块）；
__file__ 目录层级由 ×2 变 ×3（包内多一层），落点路径相同（项目根/reports）。
get_latest_reports / get_report_history 为 blueprints/report.py 经 facade 消费的查询面。
"""

import os

from modules.daily_report._env import get_connection, logger

_REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'reports')


def _check_score_differences(report_date, results):
    """P3-A 强制补充：评分差异监控

    从 daily_reports 表查每只股票最近一次 engine=legacy 的评分，
    与当前 v5 评分比对，差异>15分则标记人工复核。

    数据源：daily_reports 表（ratings_history 无 engine_version 字段）
    """
    flags = []
    DIFF_THRESHOLD = 15.0

    try:
        conn = get_connection()
        cursor = conn.cursor()

        for r in results:
            if r.get('status') != 'ok' or r.get('engine') != 'v5':
                continue

            stock_id = r['stock_id']
            v5_score = r.get('score', 0)

            # 查最近一次 legacy 引擎的评分
            cursor.execute(
                """SELECT total_score, report_date FROM daily_reports
                   WHERE stock_id = ? AND engine_version = 'legacy'
                     AND report_date < ?
                   ORDER BY report_date DESC LIMIT 1""",
                (stock_id, report_date),
            )
            legacy_row = cursor.fetchone()

            if legacy_row and legacy_row['total_score'] is not None:
                legacy_score = legacy_row['total_score']
                diff = abs(v5_score - legacy_score)

                if diff > DIFF_THRESHOLD:
                    flags.append(
                        {
                            'stock_id': stock_id,
                            'symbol': r.get('symbol', ''),
                            'name': r.get('name', ''),
                            'v5_score': v5_score,
                            'legacy_score': legacy_score,
                            'diff': round(diff, 1),
                            'legacy_date': legacy_row['report_date'],
                            'message': f'{r.get("name", "")} v5={v5_score} vs legacy={legacy_score}，差异{round(diff, 1)}分需人工复核',
                        }
                    )
                    logger.warning(
                        f'评分差异告警: stock_id={stock_id} symbol={r.get("symbol", "")} '
                        f'v5={v5_score} legacy={legacy_score} diff={round(diff, 1)}'
                    )

        conn.close()
    except Exception as e:
        logger.error(f'评分差异监控失败: {e}')

    return flags


def _build_markdown_summary(report_date, results):
    """构建汇总 Markdown 报告并保存到文件"""
    ok_results = [r for r in results if r['status'] == 'ok']
    failed_results = [r for r in results if r['status'] == 'failed']

    md = f'# 📊 每日分析报告 — {report_date}\n\n'

    # 概览表
    md += '## 一、概览\n\n'
    md += '| 股票 | 代码 | 引擎 | 总分 | 评级 | 较前次 |\n'
    md += '|:---|:---|:---:|:---:|:---:|:---:|\n'
    for r in ok_results:
        engine_tag = '🚀' if r.get('engine') == 'v5' else '⚙️'
        change_str = ''
        if r.get('score_change') is not None:
            change = r['score_change']
            arrow = '↑' if change > 0 else ('↓' if change < 0 else '→')
            change_str = f'{arrow} {abs(change):.1f}'
        md += f'| {r["name"]} | {r["symbol"]} | {engine_tag} | {r["score"]:.1f} | {r["rating"]} | {change_str} |\n'

    # 数据完整度概览（报告生成前的数据检查结果）
    issue_stocks = [r for r in ok_results if r.get('freshness', {}).get('has_issue')]
    md += '\n### 数据完整度概览\n\n'
    if issue_stocks:
        for r in issue_stocks:
            md += f'- **{r["name"]}**（{r["symbol"]}）⚠️\n'
            for line in r['freshness']['lines']:
                if '⚠️' in line:
                    md += f'  - {line}\n'
    else:
        md += '全部股票数据完整，无滞后或替代源问题\n'
    md += '\n'

    if failed_results:
        # 021BP 项4（§4.4 可见性缺口）：超时/失败股此前只报数量，名字与原因埋在
        # POST 响应 JSON 里——概览表现在显式列出"哪些股被跳过及原因"。
        # 仅追加 markdown 内容，不动任何写库路径（R9 合规）；看板侧由行动清单
        # 卡消费 daily_reports status='failed' 行（t3 action-list）。
        md += f'\n> ⚠️ {len(failed_results)} 只股票生成失败（采集超时或数据异常被跳过，明细如下）\n\n'
        md += '| 股票 | 代码 | 失败原因 |\n'
        md += '|:---|:---|:---|\n'
        for r in failed_results:
            error_text = str(r.get('error') or '未知原因').replace('<', '&lt;')
            md += f'| {r["name"]} | {r["symbol"]} | {error_text} |\n'

    # 重点关注
    md += '\n## 二、重点关注\n\n'

    # 评分异动（变动 >5 分）
    big_changes = [
        r for r in ok_results if r.get('score_change') is not None and abs(r['score_change']) >= 5
    ]
    if big_changes:
        md += '### ⚠️ 评分异动\n\n'
        for r in big_changes:
            change = r['score_change']
            direction = '上涨' if change > 0 else '下跌'
            md += f'- **{r["name"]}**（{r["symbol"]}）：{direction} {abs(change):.1f}分 → {r["score"]:.1f}\n'
    else:
        md += '### 评分异动\n\n无显著异动\n'

    md += '\n'

    # 汇总统计
    md += '## 三、引擎统计\n\n'
    v5_count = sum(1 for r in ok_results if r.get('engine') == 'v5')
    legacy_count = sum(1 for r in ok_results if r.get('engine') == 'legacy')
    md += f'- v5引擎：{v5_count} 只\n'
    md += f'- 经典引擎：{legacy_count} 只\n'
    md += f'- 生成失败：{len(failed_results)} 只\n\n'

    # 逐只详情从数据库读取
    md += '## 四、逐只详情\n\n'

    conn = get_connection()
    cursor = conn.cursor()
    for r in results:
        if r['status'] == 'ok':
            cursor.execute(
                'SELECT markdown_content FROM daily_reports WHERE report_date=? AND stock_id=?',
                (report_date, r['stock_id']),
            )
            row = cursor.fetchone()
            if row and row['markdown_content']:
                md += row['markdown_content']
    conn.close()

    # 保存到文件
    os.makedirs(_REPORTS_DIR, exist_ok=True)
    filepath = os.path.join(_REPORTS_DIR, f'{report_date}.md')
    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(md)

    return md


# ================================================================
# 查询接口
# ================================================================


def get_latest_reports():
    """获取最新一期报告列表（013-Hotfix：优先 daily，无 daily 时取 intraday）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MAX(report_date) as latest_date FROM daily_reports
    """)
    row = cursor.fetchone()
    if not row or not row['latest_date']:
        conn.close()
        return {'success': True, 'report_date': None, 'reports': []}

    latest_date = row['latest_date']

    # 013-Hotfix: 优先取 daily，无 daily 时取 intraday，避免同一天混合返回导致列表重复
    cursor.execute(
        """
        SELECT * FROM daily_reports
        WHERE report_date = ? AND report_type = 'daily' AND status = 'ok'
        ORDER BY total_score DESC
    """,
        (latest_date,),
    )
    reports = [dict(r) for r in cursor.fetchall()]

    if not reports:
        cursor.execute(
            """
            SELECT * FROM daily_reports
            WHERE report_date = ? AND report_type = 'intraday' AND status = 'ok'
            ORDER BY total_score DESC
        """,
            (latest_date,),
        )
        reports = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {'success': True, 'report_date': latest_date, 'reports': reports}


def get_report_history(page=1, page_size=30):
    """获取报告历史（分页）"""
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT COUNT(DISTINCT report_date) as cnt FROM daily_reports')
    total = cursor.fetchone()['cnt']

    offset = (page - 1) * page_size
    cursor.execute(
        """
        SELECT report_date, COUNT(*) as stock_count,
               SUM(CASE WHEN status='ok' THEN 1 ELSE 0 END) as ok_count,
               SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as fail_count,
               SUM(CASE WHEN engine_version='v5' THEN 1 ELSE 0 END) as v5_count,
               MAX(generated_at) as generated_at
        FROM daily_reports
        GROUP BY report_date
        ORDER BY report_date DESC
        LIMIT ? OFFSET ?
    """,
        (page_size, offset),
    )
    dates = [dict(r) for r in cursor.fetchall()]
    conn.close()

    return {
        'success': True,
        'total': total,
        'page': page,
        'page_size': page_size,
        'dates': dates,
    }
