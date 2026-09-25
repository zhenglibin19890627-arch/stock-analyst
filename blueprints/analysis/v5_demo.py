"""v5 评分引擎演示/调试域路由（t7 拆包）：scoring-demo / scoring-analyze / scoring-validation。

原 blueprints/analysis.py 区段逐字搬运。⚠️ 仅调试：前端无入口，评分引擎自验证
工具（勿删）；test_routes 引用 scoring-demo（勿删）。
"""

from flask import jsonify, request

from blueprints.analysis import bp


@bp.route('/api/v5/scoring-demo', methods=['GET'])
def api_v5_scoring_demo():
    """v5.0 评分引擎演示接口（使用 MockDataProvider 生成模拟数据并评分）
    ⚠️ 仅调试/演示：前端无入口，app.py 启动横幅指引手动访问；test_routes 引用（勿删）。

    Query params:
      - scenario: normal / boundary / partial（默认 normal）
      - code: 股票代码（默认 600519.SH）
      - market: A / HK（默认 A）
      - close: 收盘价（默认随机）
      - missing_rate: partial场景缺失率（默认 0.3）
    """
    from modules.mock_data_provider import MockDataProvider
    from modules.scoring_engine import analyze

    scenario = request.args.get('scenario', 'normal')
    code = request.args.get('code', '600519.SH')
    market = request.args.get('market', 'A')
    close_str = request.args.get('close', '')
    missing_rate_str = request.args.get('missing_rate', '0.3')

    try:
        close = float(close_str) if close_str else None
        missing_rate = float(missing_rate_str)
    except (ValueError, TypeError):
        close = None
        missing_rate = 0.3

    provider = MockDataProvider()
    try:
        data = provider.generate(
            scenario,
            code=code,
            market=market,
            close=close,
            missing_rate=missing_rate,
            seed=42,
        )
    except Exception as e:
        return jsonify({'success': False, 'message': f'数据生成失败: {e}'}), 400

    result = analyze(data)

    return jsonify(
        {
            'success': True,
            'input_data': {
                'code': data.code,
                'market': data.market,
                'trade_date': data.trade_date,
                'close': data.close,
                'scenario': scenario,
            },
            'result': result.model_dump(),
        }
    )


@bp.route('/api/v5/scoring-analyze', methods=['POST'])
def api_v5_scoring_analyze():
    """v5.0 评分引擎分析接口（接收 StockData JSON，返回评分结果）
    ⚠️ 仅调试：前端无入口，供手动契约调试（勿删）。

    Body: StockData 契约字段（至少需 code/market/trade_date/close 四个必填项）
    """
    from modules.data_contract import StockData
    from modules.scoring_engine import analyze

    raw = request.get_json(silent=True) or {}

    # 必填字段校验
    required = ['code', 'market', 'trade_date', 'close']
    for f in required:
        if f not in raw or raw[f] is None:
            return jsonify({'success': False, 'message': f'缺少必填字段: {f}'}), 400

    try:
        data = StockData(**raw)
    except Exception as e:
        return jsonify({'success': False, 'message': f'StockData 构造失败: {e}'}), 400

    result = analyze(data)

    return jsonify(
        {
            'success': True,
            'result': result.model_dump(),
        }
    )


@bp.route('/api/v5/scoring-validation', methods=['GET'])
def api_v5_scoring_validation():
    """v5.0 评分引擎验证接口（运行 exhaustive 56 条极端值快速检查）
    ⚠️ 仅调试：前端无入口，评分引擎自验证工具（勿删）。

    返回每条用例的评分摘要及 NaN/Inf/范围检查结果。
    """
    import math

    from modules.mock_data_provider import MockDataProvider
    from modules.scoring_engine import analyze

    provider = MockDataProvider()
    batch = provider.generate(
        'boundary',
        boundary_mode='exhaustive',
        code='600519.SH',
        market='A',
        trade_date='20260718',
        close=100.0,
    )

    results = []
    all_pass = True
    for i, data in enumerate(batch):
        try:
            result = analyze(data)
            has_nan = any(math.isnan(v) for v in [result.total_score] if v is not None) or any(
                math.isnan(getattr(result, a, 0) or 0)
                for a in [
                    'technical_score',
                    'fundamental_score',
                    'sentiment_score',
                    'capital_score',
                ]
            )
            in_range = 0 <= result.total_score <= 100
            ok = not has_nan and in_range
            if not ok:
                all_pass = False

            # 找到被修改的字段
            extremes = provider.BOUNDARY_EXTREMES
            case_field = ''
            case_val = None
            cum = 0
            for field_name, extreme_values in extremes.items():
                for val in extreme_values:
                    if cum == i:
                        case_field = field_name
                        case_val = val
                    cum += 1

            results.append(
                {
                    'case_id': f'BV-{i + 1}',
                    'field': case_field,
                    'extreme_value': case_val,
                    'total_score': result.total_score,
                    'rating': result.rating,
                    'tech': result.technical_score,
                    'fund': result.fundamental_score,
                    'news': result.sentiment_score,
                    'capital': result.capital_score,
                    'nan_check': 'OK' if not has_nan else 'FAIL',
                    'range_check': 'OK' if in_range else 'FAIL',
                }
            )
        except Exception as e:
            all_pass = False
            results.append(
                {
                    'case_id': f'BV-{i + 1}',
                    'error': str(e),
                    'nan_check': 'CRASH',
                    'range_check': 'N/A',
                }
            )

    return jsonify(
        {
            'success': True,
            'total_cases': len(results),
            'all_pass': all_pass,
            'summary': f'{len(results)}条用例, {"全部通过" if all_pass else "存在异常"}',
            'cases': results,
        }
    )
