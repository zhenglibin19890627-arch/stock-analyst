"""评级回测/价格回测/自动优化 API 蓝图(自 app.py 拆分,函数体零改动)。"""

from flask import Blueprint, jsonify, request

from database.db_manager import get_connection

bp = Blueprint('backtest', __name__)

@bp.route('/api/backtest/market-report')
def api_backtest_market_report():
    """市场级回测报告（A股/港股独立）
    参数: market=a_stock/hk_stock, include_simulated=true/false
    """
    market = request.args.get('market', 'a_stock')
    include_simulated = request.args.get('include_simulated', 'false').lower() == 'true'
    try:
        from modules.backtest_engine import BacktestEngine

        engine = BacktestEngine()
        report = engine.compute_market_report(market, include_simulated=include_simulated)
        return jsonify({'success': True, 'report': report})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backtest/stock/<int:stock_id>')
def api_backtest_stock_detail(stock_id):
    """个股回测明细"""
    try:
        from modules.backtest_engine import BacktestEngine

        engine = BacktestEngine()
        detail = engine.compute_stock_detail(stock_id)
        return jsonify(detail)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backtest/rerun', methods=['POST'])
def api_backtest_rerun():
    """手动重跑回测
    Body: {"market": "a_stock", "days": null, "force": false}
    """
    data = request.get_json(silent=True) or {}
    market = data.get('market')
    days = data.get('days')
    force = data.get('force', False)
    try:
        from modules.backtest_engine import BacktestEngine

        engine = BacktestEngine()
        result = engine.batch_backtest(market=market, days=days, force=force)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backtest/simulate', methods=['POST'])
def api_backtest_simulate():
    """M9-PREFILL：技术面模拟回测回填（60天）
    手动触发，幂等执行。
    """
    try:
        from modules.backtest_engine import run_historical_simulation

        result = run_historical_simulation()
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backtest/status')
def api_backtest_status():
    """回测概览（用于看板）"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as cnt FROM backtest_results')
        total_bt = cursor.fetchone()['cnt']
        cursor.execute('SELECT COUNT(*) as cnt FROM ratings_history WHERE price_at_rating > 0')
        total_ratings = cursor.fetchone()['cnt']
        cursor.execute('SELECT market, COUNT(*) as cnt FROM backtest_results GROUP BY market')
        market_dist = {r['market']: r['cnt'] for r in cursor.fetchall()}
        conn.close()
        return jsonify(
            {
                'success': True,
                'total_backtests': total_bt,
                'total_ratings_with_price': total_ratings,
                'coverage': round(total_bt / total_ratings, 4) if total_ratings > 0 else 0,
                'market_distribution': market_dist,
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backtest/weight-experiments')
def api_backtest_weight_experiments():
    """权重实验场景列表（D4裁定预留）"""
    try:
        from modules.backtest_engine import WeightExperimentRunner

        runner = WeightExperimentRunner()
        return jsonify({'success': True, 'experiments': runner.list_experiments()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/backtest/weight-experiments/<experiment_id>/run', methods=['POST'])
def api_backtest_run_experiment(experiment_id):
    """执行权重实验（仅模拟计算，不修改生产权重）"""
    try:
        from modules.backtest_engine import WeightExperimentRunner

        runner = WeightExperimentRunner()
        result = runner.run_experiment(experiment_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# 007-PRICE-BACKTEST: 价格建议命中率回测 API
# ============================================================


@bp.route('/api/price-backtest/run', methods=['POST'])
def api_price_backtest_run():
    """007: 触发价格建议回测
    Body: {"market": "a_stock", "force": false}
    """
    data = request.get_json(silent=True) or {}
    market = data.get('market', 'a_stock')
    force = data.get('force', False)
    try:
        from modules.price_backtest import run_price_backtest

        result = run_price_backtest(market=market, force=force)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/price-backtest/report')
def api_price_backtest_report():
    """007: 获取价格建议回测报告
    参数: market=a_stock/hk_stock
    """
    market = request.args.get('market', 'a_stock')
    try:
        from modules.price_backtest import compute_price_backtest_report

        report = compute_price_backtest_report(market)
        return jsonify({'success': True, 'report': report})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# M9-OPTIMIZE: 自动优化引擎 API
# ============================================================


@bp.route('/api/optimizer/run', methods=['POST'])
def api_optimizer_run():
    """M9 手动触发优化
    Body: {"market": "a_stock"} 或 {"market": "hk_stock"} 或 {} (默认a_stock)
    """
    data = request.get_json(silent=True) or {}
    market = data.get('market', 'a_stock')
    try:
        from modules.optimizer_engine import OptimizerEngine

        engine = OptimizerEngine()
        result = engine.run_weekly_optimization(market)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@bp.route('/api/optimizer/status')
def api_optimizer_status():
    """M9 查看当前参数 + 优化历史（US-10）
    参数: market=a_stock/hk_stock
    """
    market = request.args.get('market', 'a_stock')
    try:
        from modules.optimizer_engine import OptimizerEngine

        engine = OptimizerEngine()
        params = engine.get_current_params(market)
        history = engine.get_optimization_history(market)
        return jsonify(
            {
                'success': True,
                'params': params,
                'history': history,
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# US11-EXPORT: 报告导出接口（Excel 下载）
# ============================================================
