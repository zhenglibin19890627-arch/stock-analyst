"""四维分析/评级/建议/v5 评分演示 API 蓝图(自 app.py 拆分,函数体零改动)。"""

from database.db_manager import get_connection
from flask import Blueprint, jsonify, request

from blueprints._utils import _derive_obos_signal, _resolve_report_type

bp = Blueprint('analysis', __name__)

@bp.route('/api/stocks/<int:stock_id>/analyze', methods=['POST'])
def api_analyze_stock(stock_id):
    """执行四维分析引擎评分（统一走 advisor.generate_advice 入口，与每日报告一致）"""
    from modules.advisor import generate_advice

    try:
        result = generate_advice(stock_id)
        # 005: 后处理集成价格建议（不修改 generate_advice）
        if result.get('success'):
            from modules.price_advisor import generate_price_advice

            result['price_advice'] = generate_price_advice(stock_id, result)
            # 009补充：动态操作建议覆盖旧建议，避免矛盾
            if result.get('price_advice', {}).get('action_suggestion'):
                result['position_advice'] = result['price_advice']['action_suggestion']
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'分析失败: {str(e)}'}), 500


@bp.route('/api/stocks/<int:stock_id>/refresh-full', methods=['POST'])
def api_refresh_full(stock_id):
    """011：强制全量刷新数据 + 重新分析。
    绕过所有增量缓存，重新采集全部维度数据。
    """
    from modules.advisor import generate_advice
    from modules.data_collector import collect_stock_data

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT symbol, name, market FROM stocks WHERE id = ?', (stock_id,))
    stock = cursor.fetchone()
    conn.close()

    if not stock:
        return jsonify({'success': False, 'message': '股票不存在'}), 404

    symbol = stock['symbol']
    market = stock['market']

    try:
        # 步骤1：强制全量采集
        collect_stock_data(symbol, market, force_full=True)

        # 步骤2：重新分析
        result = generate_advice(stock_id)
        if result.get('success'):
            from modules.price_advisor import generate_price_advice

            result['price_advice'] = generate_price_advice(stock_id, result)
            if result.get('price_advice', {}).get('action_suggestion'):
                result['position_advice'] = result['price_advice']['action_suggestion']

        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'全量刷新失败: {str(e)}'}), 500


@bp.route('/api/stocks/<int:stock_id>/analysis', methods=['GET'])
def api_get_analysis(stock_id):
    """查看最近的分析结果"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM analysis_results WHERE stock_id = ?
        ORDER BY analysis_date DESC LIMIT 5
    """,
        (stock_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': rows, 'count': len(rows)})


@bp.route('/api/stocks/<int:stock_id>/report-latest', methods=['GET'])
def api_get_report_latest(stock_id):
    """P3-A 附加修复：从 daily_reports 表读取该股票最新报告的评分数据。

    与列表页(watchlist-scores)完全同源，确保 engine_version/total_score/
    rating/generated_at 四元组一致。

    B11-DETAIL-LOAD：如果当日无报告，自动触发分析（对用户透明，无需手动刷新）。

    返回格式兼容前端 renderFullReport 所需的 adviseData 结构。
    """
    from datetime import datetime, timezone
    from datetime import timedelta as _td

    _CN_TZ = timezone(_td(hours=8), name='Asia/Shanghai')
    today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')

    conn = get_connection()
    cursor = conn.cursor()

    # 019D: 先判定当日 target_type（daily 优先），再限定 report_type + status='ok'
    target_type = _resolve_report_type(cursor, today)
    cursor.execute(
        """SELECT dr.*, s.symbol, s.name, s.market
           FROM daily_reports dr
           JOIN stocks s ON dr.stock_id = s.id
           WHERE dr.stock_id = ? AND dr.report_date = ?
           AND dr.status = 'ok' AND dr.report_type = ? """,
        (stock_id, today, target_type),
    )
    row = cursor.fetchone()

    # B11-DETAIL-LOAD：当日无报告时，自动触发分析（静默）
    if not row:
        conn.close()
        try:
            from modules.advisor import generate_advice

            advice = generate_advice(stock_id)
            if advice.get('success'):
                # 005: 追加 price_advice（与 /advise 端点一致）
                from modules.price_advisor import generate_price_advice as _gpa2

                advice['price_advice'] = _gpa2(stock_id, advice)
                # 009补充：动态操作建议覆盖旧建议，避免矛盾
                if advice.get('price_advice', {}).get('action_suggestion'):
                    advice['position_advice'] = advice['price_advice']['action_suggestion']
                # 分析成功，直接返回引擎结果
                # 019D: 补充 generated_at（报告生成时刻，与 DB 行路径一致）
                advice['generated_at'] = datetime.now(_CN_TZ).isoformat()
                return jsonify(advice)
        except Exception:
            pass

        # 引擎也失败，回退到历史报告
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(report_date) as latest_date FROM daily_reports')
        date_row = cursor.fetchone()
        latest_date = date_row['latest_date'] if date_row else None

        if not latest_date:
            conn.close()
            return jsonify({'success': False, 'message': '无报告数据'})

        # 019D: 回退查询同步统一口径（report_type + status='ok'）
        fallback_type = _resolve_report_type(cursor, latest_date)
        cursor.execute(
            """SELECT dr.*, s.symbol, s.name, s.market
               FROM daily_reports dr
               JOIN stocks s ON dr.stock_id = s.id
               WHERE dr.stock_id = ? AND dr.report_date = ?
               AND dr.status = 'ok' AND dr.report_type = ?""",
            (stock_id, latest_date, fallback_type),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'success': False, 'message': f'{latest_date} 无该股票报告'})
    else:
        latest_date = today
        conn.close()

    # 解析 key_factors 重建 dimensions
    import json as _json

    key_factors = {}
    try:
        if row['key_factors']:
            key_factors = _json.loads(row['key_factors'])
    except (ValueError, TypeError):
        pass

    # 从 key_factors 构建 dimensions 结构（兼容 renderFullReport）
    dimensions = {}
    for dim_key, dim_data in key_factors.items():
        dimensions[dim_key] = {
            'status': 'ok',
            'score': dim_data.get('score', 0),
            'weight': dim_data.get('weight', 0),
            'factors': dim_data.get('top_factors', {}),
        }

    # 解析 data_warnings
    data_warnings = []
    try:
        if row['data_warnings']:
            data_warnings = _json.loads(row['data_warnings'])
    except (ValueError, TypeError):
        pass

    # B15-T3: 从 key_factors 推算 data_quality（各维度完整度）
    # U7(#5): 当无法从 data_completeness 解析完整度时，不再默认100%，
    #         标记为 None（前端显示「已采集」），避免与实际数据矛盾
    _dq_map = {
        'kline': 'technical',
        'fundamental': 'fundamental',
        'capital_flow': 'capital',
        'news': 'news',
    }
    data_quality = {}
    for dim_key, dq_name in _dq_map.items():
        dim_info = key_factors.get(dim_key)
        if dim_info:
            # 尝试从 top_factors 中解析 data_completeness 百分比
            completeness_str = dim_info.get('top_factors', {}).get('data_completeness', '')
            if completeness_str and '%' in str(completeness_str):
                try:
                    data_quality[dq_name] = float(str(completeness_str).replace('%', '')) / 100.0
                except (ValueError, TypeError):
                    data_quality[dq_name] = None  # 解析失败，标记为未统计
            else:
                data_quality[dq_name] = None  # 无完整度字段，标记为未统计（不再默认100%）
        else:
            data_quality[dq_name] = 0.0

    # B15-T3: 从 dimensions 提取最强/最弱维度
    _dim_name_map = {
        'kline': '技术面',
        'fundamental': '基本面',
        'capital_flow': '资金面',
        'news': '消息面',
    }
    strongest_dim = None
    weakest_dim = None
    if dimensions:
        scored_dims = [
            (k, v.get('score', 0)) for k, v in dimensions.items() if v.get('status') == 'ok'
        ]
        if scored_dims:
            scored_dims.sort(key=lambda x: x[1], reverse=True)
            best_key, best_score = scored_dims[0]
            worst_key, worst_score = scored_dims[-1]
            strongest_dim = {'name': _dim_name_map.get(best_key, best_key), 'score': best_score}
            weakest_dim = {'name': _dim_name_map.get(worst_key, worst_key), 'score': worst_score}

    # B15-T3: 使用 markdown_content 作为 advice_detail
    advice_detail = row['markdown_content'] if row['markdown_content'] else None

    # 005: price_advice 实时计算（不使用日报缓存，确保持仓状态正确识别）
    # Bugfix: 日报缓存中的 price_advice 可能在持仓修复前生成，导致状态错误
    price_advice = None
    try:
        from modules.price_advisor import generate_price_advice as _gpa

        # 查最新收盘价（report-latest 上下文中无 latest_close）
        _conn_pa = get_connection()
        _cur_pa = _conn_pa.cursor()
        _cur_pa.execute(
            'SELECT close FROM raw_kline WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1',
            (stock_id,),
        )
        _r = _cur_pa.fetchone()
        _conn_pa.close()
        _latest_close = float(_r['close']) if _r and _r['close'] else None
        price_advice = _gpa(
            stock_id,
            {
                'rating': row['rating'] or '持有观望',
                'latest_close': _latest_close,
                'has_position': False,  # price_advisor 会自行查持仓
            },
        )
    except Exception as _e:
        import logging

        logging.getLogger(__name__).warning(f'report-latest price_advice 实时计算失败: {_e}')

    result = {
        'success': True,
        'stock_id': stock_id,
        'stock_code': row['stock_code'] or row['symbol'],
        'stock_name': row['stock_name'] or row['name'],
        'market': row['market'],
        # 评分四元组（与列表页同源）
        'engine_version': row['engine_version'],
        'total_score': row['total_score'],
        'rating': row['rating'],
        'rating_label': row['rating_label'],
        'rating_date': latest_date,
        # 评分变动
        'prev_score': row['prev_score'],
        'score_change': row['score_change'],
        # 四维数据（从 key_factors 重建）
        'dimensions': dimensions,
        'data_warnings': data_warnings,
        # B15-T3: 投资建议字段补充
        'advice_detail': advice_detail,
        'position_advice': None,
        'price_advice': price_advice,
        'strongest_dim': strongest_dim,
        'weakest_dim': weakest_dim,
        'data_quality': data_quality if data_quality else None,
        # 来源标记
        'data_source': 'daily_reports',
        'generated_at': row['generated_at'],
    }

    # 009补充：动态操作建议覆盖旧建议，避免矛盾
    if result.get('price_advice', {}).get('action_suggestion'):
        result['position_advice'] = result['price_advice']['action_suggestion']

    return jsonify(result)


@bp.route('/api/stocks/<int:stock_id>/advise', methods=['POST'])
def api_advise_stock(stock_id):
    """执行模块2分析+模块3建议生成，返回完整评级建议"""
    from modules.advisor import generate_advice

    try:
        result = generate_advice(stock_id)
        # 005: 后处理集成价格建议（不修改 generate_advice）
        if result.get('success'):
            from modules.price_advisor import generate_price_advice

            result['price_advice'] = generate_price_advice(stock_id, result)
            # 009补充：动态操作建议覆盖旧建议，避免矛盾
            if result.get('price_advice', {}).get('action_suggestion'):
                result['position_advice'] = result['price_advice']['action_suggestion']
            # 019L: 补充 generated_at（报告生成时刻，与 /report-latest 019D 同型）
            from datetime import datetime, timezone
            from datetime import timedelta as _td

            _CN_TZ = timezone(_td(hours=8), name='Asia/Shanghai')
            result['generated_at'] = datetime.now(_CN_TZ).isoformat()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'建议生成失败: {str(e)}'}), 500


@bp.route('/api/stocks/<int:stock_id>/ratings', methods=['GET'])
def api_get_ratings(stock_id):
    """查看评级历史记录"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT rh.*, s.symbol, s.name
        FROM ratings_history rh
        JOIN stocks s ON rh.stock_id = s.id
        WHERE rh.stock_id = ?
        ORDER BY rh.rating_date DESC LIMIT 10
    """,
        (stock_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': rows, 'count': len(rows)})


@bp.route('/api/ratings', methods=['GET'])
def api_get_ratings_list():
    """
    评级列表：返回所有已分析股票的最新评级结果。
    B11-SCORE-SYNC：数据源统一为 daily_reports 表（与看板/日报同源）。
    支持排序：sort_by=rating_time|total_score，order=desc|asc
    支持筛选：rating=强烈推荐买入|推荐买入|持有观望|建议减仓|强烈建议卖出
    """
    sort_by = request.args.get('sort_by', 'rating_time')
    order = request.args.get('order', 'desc')
    rating_filter = request.args.get('rating', '')

    # 白名单防注入
    valid_sort = {
        'rating_time': 'dr.generated_at',
        'total_score': 'dr.total_score',
        'rating': 'dr.rating',
        'symbol': 's.symbol',
    }
    sort_col = valid_sort.get(sort_by, 'dr.generated_at')
    sort_dir = 'DESC' if order.lower() == 'desc' else 'ASC'

    # B11-SCORE-SYNC：从 daily_reports 表读取最新一期报告（与看板/日报同源）
    conn = get_connection()
    cursor = conn.cursor()

    # 先查最新报告日期
    cursor.execute('SELECT MAX(report_date) as latest_date FROM daily_reports')
    date_row = cursor.fetchone()
    latest_date = date_row['latest_date'] if date_row else None

    if not latest_date:
        conn.close()
        return jsonify({'success': True, 'ratings': [], 'count': 0})

    # 019D: 统一口径，调用共享辅助函数（daily 优先，无 daily 时取 intraday）
    target_type = _resolve_report_type(cursor, latest_date)

    sql = """
        SELECT dr.stock_code, dr.stock_name, dr.total_score, dr.rating,
               dr.rating_label, dr.engine_version, dr.generated_at,
               dr.prev_score, dr.score_change, dr.key_factors,
               dr.report_date, dr.report_type, dr.status as report_status,
               s.id as stock_id, s.symbol, s.name, s.market,
               sg.name as group_name
        FROM daily_reports dr
        JOIN stocks s ON dr.stock_id = s.id
        LEFT JOIN groups sg ON s.group_id = sg.id AND sg.type='watchlist'
        WHERE dr.report_date = ? AND dr.status = 'ok' AND dr.report_type = ?
    """
    params = [latest_date, target_type]

    if rating_filter:
        sql += ' AND dr.rating_label = ?'
        params.append(rating_filter)

    sql += f' ORDER BY {sort_col} {sort_dir}'

    cursor.execute(sql, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # 适配前端期望的字段名（保持与旧 analysis_results 接口兼容）
    from datetime import datetime, timezone
    from datetime import timedelta as _td

    _now = datetime.now(timezone(_td(hours=8)))
    for row in rows:
        # 兼容旧字段名
        row['rating_time'] = row.get('generated_at', '')
        row['created_at'] = row.get('generated_at', '')
        # DEV-TASKS-20260727-003：超买超卖信号（从已有 key_factors 派生）
        row['obos_signal'] = _derive_obos_signal(row.get('key_factors'))
        # 数据时效标识
        row['data_stale'] = False
        rt = row.get('generated_at') or ''
        if rt:
            try:
                rt_dt = datetime.fromisoformat(rt)
                if rt_dt.tzinfo is None:
                    rt_dt = rt_dt.replace(tzinfo=timezone(_td(hours=8)))
                hours_diff = (_now - rt_dt).total_seconds() / 3600
                row['data_stale'] = hours_diff > 24
            except (ValueError, TypeError):
                pass

    return jsonify(
        {
            'success': True,
            'ratings': rows,
            'count': len(rows),
        }
    )


# ============================================================
# 持仓管理 API
# ============================================================


@bp.route('/api/v5/scoring-demo', methods=['GET'])
def api_v5_scoring_demo():
    """v5.0 评分引擎演示接口（使用 MockDataProvider 生成模拟数据并评分）

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


# ============================================================
# US-11: 每日报告 API
# ============================================================
