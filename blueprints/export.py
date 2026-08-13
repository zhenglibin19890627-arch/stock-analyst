"""报告导出(Excel) API 蓝图(自 app.py 拆分,函数体零改动)。"""

from flask import Blueprint, jsonify, request

bp = Blueprint('export', __name__)

@bp.route('/api/export/daily-report')
def api_export_daily_report():
    """导出每日报告为 Excel"""
    from datetime import datetime, timedelta, timezone

    from flask import send_file

    from modules.export_engine import export_daily_report

    _tz = timezone(timedelta(hours=8))
    date = request.args.get('date') or datetime.now(_tz).strftime('%Y-%m-%d')
    try:
        buf = export_daily_report(date)
        filename = f'StockAnalyst_\u65e5\u62a5_{date}.xlsx'
        return send_file(
            buf,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'导出失败: {e!s}'}), 500


@bp.route('/api/export/watchlist')
def api_export_watchlist():
    """导出自选股总览为 Excel"""
    from datetime import datetime, timedelta, timezone

    from flask import send_file

    from modules.export_engine import export_watchlist

    _tz = timezone(timedelta(hours=8))
    today = datetime.now(_tz).strftime('%Y-%m-%d')
    try:
        buf = export_watchlist()
        filename = f'StockAnalyst_\u81ea\u9009\u80a1_{today}.xlsx'
        return send_file(
            buf,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'导出失败: {e!s}'}), 500


@bp.route('/api/export/backtest')
def api_export_backtest():
    """导出回测报告为 Excel"""
    from datetime import datetime, timedelta, timezone

    from flask import send_file

    from modules.export_engine import export_backtest

    _tz = timezone(timedelta(hours=8))
    market = request.args.get('market', 'a_stock')
    today = datetime.now(_tz).strftime('%Y-%m-%d')
    market_name = 'A\u80a1' if market == 'a_stock' else '\u6e2f\u80a1'
    try:
        buf = export_backtest(market)
        filename = f'StockAnalyst_\u56de\u6d4b_{market_name}_{today}.xlsx'
        return send_file(
            buf,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'导出失败: {e!s}'}), 500


# ============================================================
# B8: 指数评级 API
# ============================================================
