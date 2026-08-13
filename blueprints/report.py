"""每日报告 API 蓝图(自 app.py 拆分,函数体零改动)。"""

from flask import Blueprint, jsonify, request

bp = Blueprint('report', __name__)

@bp.route('/api/daily-report/progress')
def api_daily_report_progress():
    """012-B: 查询报告生成进度（前端进度条轮询）"""
    import json as _json
    import os as _os

    # 进度文件由 modules/daily_report.py 写入，路径以该模块为准（单一来源，防漂移）
    from modules.daily_report import _REPORT_PROGRESS_PATH

    progress_path = _REPORT_PROGRESS_PATH
    if not _os.path.exists(progress_path):
        return jsonify({'success': True, 'progress': None})
    try:
        with open(progress_path, encoding='utf-8') as f:
            progress = _json.load(f)
        return jsonify({'success': True, 'progress': progress})
    except Exception as e:
        return jsonify({'success': False, 'message': f'读取进度失败: {e}'}), 500


@bp.route('/api/daily-report/generate', methods=['POST'])
def api_daily_report_generate():
    """手动触发每日报告生成"""
    from modules.daily_report import generate_daily_report

    data = request.get_json(silent=True) or {}
    target_date = data.get('date')
    force = data.get('force', False)  # B15-T2: 强制刷新选项

    try:
        result = generate_daily_report(target_date, force=force)
        return jsonify(
            {
                'success': result['success'],
                'report_date': result['report_date'],
                'total': result['total'],
                'success_count': result['success_count'],
                'fail_count': result['fail_count'],
                'v5_count': result['v5_count'],
                'legacy_count': result['legacy_count'],
                'fallback_count': result['fallback_count'],
                'reuse_count': result.get('reuse_count', 0),
                'results': result['results'],
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'message': f'报告生成失败: {str(e)}'}), 500


@bp.route('/api/daily-report/generate-intraday', methods=['POST'])
def api_daily_report_generate_intraday():
    """013: 盘中快报 — 生成 intraday 报告，不覆盖已有 daily"""
    from modules.daily_report import generate_daily_report

    try:
        result = generate_daily_report(report_type='intraday')
        return jsonify(
            {
                'success': result['success'],
                'report_date': result['report_date'],
                'report_type': 'intraday',
                'total': result['total'],
                'success_count': result['success_count'],
                'fail_count': result['fail_count'],
                'v5_count': result['v5_count'],
                'legacy_count': result['legacy_count'],
                'fallback_count': result['fallback_count'],
                'reuse_count': result.get('reuse_count', 0),
                'results': result['results'],
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'message': f'盘中快报生成失败: {str(e)}'}), 500


@bp.route('/api/daily-report/latest')
def api_daily_report_latest():
    """获取最新一期报告"""
    from modules.daily_report import get_latest_reports

    return jsonify(get_latest_reports())


@bp.route('/api/daily-report/<report_date>')
def api_daily_report_by_date(report_date):
    """获取指定日期的报告"""
    from modules.daily_report import get_reports_by_date

    return jsonify(get_reports_by_date(report_date))


@bp.route('/api/daily-report/history')
def api_daily_report_history():
    """报告历史列表（分页）"""
    from modules.daily_report import get_report_history

    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 30))
    return jsonify(get_report_history(page, page_size))
