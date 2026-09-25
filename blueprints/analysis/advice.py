"""评级/建议/报告读取域路由（t7 拆包）：analyze / advise / report-latest。

原 blueprints/analysis.py 区段逐字搬运：四维分析（005/009/020R-41/43/021BS）、
统一增强装配 _enrich_advice_result（021K：实时/快照两路同源）、服务端实时重评
（021K 15 分钟阈值）、report-latest 快照读取主路径（B11-DETAIL-LOAD / 019D /
020M / B15-T3 / 021BS / 021BU / 021BZ）。路由经 facade bp 挂载，URL 与端点名
与拆分前逐字一致。
"""

import logging

from flask import jsonify

from blueprints._utils import _resolve_report_type
from blueprints.analysis import bp
from blueprints.analysis._details import (
    _capital_detail_for_stock,
    _fundamental_detail_for_stock,
    _industry_flow_bg_for_stock,
    _news_detail_for_stock,
    _technical_detail_for_stock,
)
from blueprints.analysis._enrich import (
    _attach_backtest_evidence,
    _attach_resonance_snapshot,
    _attach_score_tier_note,
    _attach_scoring_subitems,
    _enrich_data_warnings,
    _parse_markdown_risks,
    _prev_report_snapshot,
)
from database.db_manager import get_connection


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
            # 020R-41：补齐数据完整度行（与每日报告路径一致）
            _enrich_data_warnings(result, stock_id)
            # 020R-43：advice_detail 统一为结构化 markdown（与快照路径一致，不再是一段纯文本）
            from modules.advisor import _build_markdown_single

            result['advice_detail'] = _build_markdown_single(result, result.get('previous_score'))
            # 021BS P1-1：结构化失配注记（与 markdown 行同源）
            _attach_score_tier_note(result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'分析失败: {e!s}'}), 500


def _enrich_advice_result(stock_id, result):
    """021K：实时建议结果的统一增强后处理。

    /advise 端点与 report-latest 实时路径共用，保证「打开报告」与「手动刷新」
    返回完全同源同构的数据（021E~021K 系列教训：两路口径漂移 = UX 不一致）。
    仅做后处理增强，不修改 generate_advice 本体（B24 红线）。
    """
    from datetime import datetime, timedelta, timezone

    from modules.advisor import _build_markdown_single
    from modules.price_advisor import generate_price_advice

    _CN_TZ = timezone(timedelta(hours=8))

    # 005: 价格建议（后处理集成）
    result['price_advice'] = generate_price_advice(stock_id, result)
    # 009补充：动态操作建议覆盖旧建议，避免矛盾
    if result.get('price_advice', {}).get('action_suggestion'):
        result['position_advice'] = result['price_advice']['action_suggestion']
    # 019L: generated_at（与 DB 行路径一致）
    result['generated_at'] = datetime.now(_CN_TZ).isoformat()
    # 020R-41：补齐数据完整度行
    _enrich_data_warnings(result, stock_id)
    # 020R-43：advice_detail 结构化 markdown
    result['advice_detail'] = _build_markdown_single(result, result.get('previous_score'))
    # 020R-35/37/38/39：四维指标明细（四维评分详情卡片数据）
    result['technical_detail'] = _technical_detail_for_stock(stock_id)
    result['fundamental_detail'] = _fundamental_detail_for_stock(stock_id)
    result['capital_detail'] = _capital_detail_for_stock(stock_id)
    result['news_detail'] = _news_detail_for_stock(stock_id)
    # 021V：基本面/资金面/消息面打分子项（021S 技术面同口径，实时/快照两路径同源）
    _attach_scoring_subitems(stock_id, result)
    # 020R-54：行业资金背景
    result['industry_flow_bg'] = _industry_flow_bg_for_stock(stock_id)
    # 021BS P1-1：结构化失配注记（与 markdown 行同源；实时路径评级为最终评级）
    _attach_score_tier_note(result)
    # 021BU：回测证据三件套（评级徽章/位置注记/价格基准，与看板同源同值）
    _attach_backtest_evidence(result, stock_id)
    # 021BZ：当前有效共振快照（类型/强弱/方向/触发日；离线复算零网络零写库）
    _attach_resonance_snapshot(result, stock_id)
    # 2026-09-09：上一轮评分快照（总分+四维分对比展示）——实时路径当前报告日期=今天
    result['prev_report'] = _prev_report_snapshot(
        stock_id, datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    )
    return result


def _generate_fresh_advice(stock_id):
    """021K：实时重评 = generate_advice + 统一增强；任何失败返回 None（调用方回落快照）。"""
    try:
        from modules.advisor import generate_advice

        advice = generate_advice(stock_id)
        if advice.get('success'):
            return _enrich_advice_result(stock_id, advice)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            f'[report-latest] 实时重评失败 stock_id={stock_id}: {e}'
        )
    return None


# 021K：当日报告快照过旧阈值（分钟）——超过即服务端实时重评（与手动刷新同源）
_REPORT_STALE_MINUTES = 15


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

    # 021K：当日已有报告但快照过旧（>15分钟，工作日）→ 服务端实时重评，
    # 与手动「🔄 刷新报告」同源同副作用；失败回落快照展示（不影响打开）。
    if row is not None:
        _stale = True
        try:
            _gen_dt = datetime.fromisoformat(row['generated_at'])
            _stale = (datetime.now(_CN_TZ) - _gen_dt).total_seconds() > _REPORT_STALE_MINUTES * 60
        except (ValueError, TypeError):
            pass  # 时间戳缺失/解析失败 → 视为过旧
        if _stale and datetime.now(_CN_TZ).weekday() < 5:
            conn.close()
            _fresh = _generate_fresh_advice(stock_id)
            if _fresh is not None:
                return jsonify(_fresh)
            # 重评失败：重开连接回落快照路径
            conn = get_connection()
            cursor = conn.cursor()
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
        # 020M：周末/休市日不实时生成——实时 advice 缺日报快照才有的综合文本(markdown)，
        # 前端「投资建议详情-综合分析」会整块缺失；周末直接回退最新历史日报快照（完整展示）。
        _is_weekend = datetime.now(_CN_TZ).weekday() >= 5
        if _is_weekend:
            logging.getLogger(__name__).info(
                f'[report-latest] stock_id={stock_id} 当日({today})无报告且为周末，'
                '跳过实时生成，直接回退最新日报快照（020M）'
            )
        else:
            # 021K：实时重评统一走 _generate_fresh_advice（含全量字段增强，
            # 与手动「🔄 刷新报告」完全同源——修复首开缺四维明细/亮点/行业背景的口径漂移）
            _fresh = _generate_fresh_advice(stock_id)
            if _fresh is not None:
                return jsonify(_fresh)

        # 引擎也失败，回退到历史报告
        conn = get_connection()
        cursor = conn.cursor()
        # 020M：按股票取最新报告日期（而非全表 MAX）——部分股票已有当日行时，
        # 不会回退到"别的股票才有报告的日期"导致本股票查无报告
        cursor.execute(
            "SELECT MAX(report_date) as latest_date FROM daily_reports "
            "WHERE stock_id = ? AND status='ok'",
            (stock_id,),
        )
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
    # 021BS：只取四维规范键且值须为 dict——key_factors 亦可携带非维度注记键
    # （trader 摘要 / score_tier_note），混入会使 dim_data.get 崩溃（字符串值）
    # 或伪造成 0 分「维度」污染最强/最弱维度（字典值）
    dimensions = {}
    for dim_key in ('kline', 'fundamental', 'capital_flow', 'news'):
        dim_data = key_factors.get(dim_key)
        if not isinstance(dim_data, dict):
            continue
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
    # 020M：latest_close/latest_close_date 提升为结果字段（快照路径原缺，前端
    # 「最新收盘」行不显示）
    _latest_close = None
    _latest_close_date = None
    price_advice = None
    try:
        from modules.price_advisor import generate_price_advice as _gpa

        # 查最新收盘价（report-latest 上下文中无 latest_close）
        _conn_pa = get_connection()
        _cur_pa = _conn_pa.cursor()
        _cur_pa.execute(
            "SELECT close, substr(trade_date, 1, 10) AS td FROM raw_kline "
            'WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1',
            (stock_id,),
        )
        _r = _cur_pa.fetchone()
        _conn_pa.close()
        _latest_close = float(_r['close']) if _r and _r['close'] else None
        _latest_close_date = _r['td'] if _r else None
        price_advice = _gpa(
            stock_id,
            {
                'rating': row['rating'] or '持有观望',
                'latest_close': _latest_close,
                'has_position': False,  # price_advisor 会自行查持仓
            },
        )
    except Exception as _e:
        logging.getLogger(__name__).warning(f'report-latest price_advice 实时计算失败: {_e}')

    # 021BS P1-1：分数×档位失配持续注记——存量报告优先读落库注记
    # （key_factors.score_tier_note），无则按同源纯函数现算补齐（读取路径
    # 消费方门控，B24 合规；使 021BS 修复前生成的存量报告同样带说明）
    _stored_note = (
        key_factors.get('score_tier_note') if isinstance(key_factors, dict) else None
    )
    _note_src = {
        'stock_id': stock_id,
        'total_score': row['total_score'],
        'rating': row['rating'],
        'market': row['market'],
    }
    _attach_score_tier_note(_note_src)
    score_tier_note = _stored_note or _note_src.get('score_tier_note')

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
        # 021BS P1-1：失配口径注记（一致/无法判定为 None）
        'score_tier_note': score_tier_note,
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
        # 020M：补齐快照路径缺失的展示字段（实时 advise 路径有、快照路径原缺）
        'action_advice': (price_advice or {}).get('action_suggestion'),
        'latest_close': _latest_close,
        'latest_close_date': _latest_close_date,
        'strongest_dim': strongest_dim,
        'weakest_dim': weakest_dim,
        'data_quality': data_quality if data_quality else None,
        # 2026-09-09：上一轮评分快照（总分+四维分对比展示）
        'prev_report': _prev_report_snapshot(stock_id, latest_date),
        # 来源标记
        'data_source': 'daily_reports',
        'generated_at': row['generated_at'],
    }

    # 009补充：动态操作建议覆盖旧建议，避免矛盾
    if result.get('price_advice', {}).get('action_suggestion'):
        result['position_advice'] = result['price_advice']['action_suggestion']

    # 020R-35：技术指标明细（均线/MACD/RSI/KDJ/布林/量能，供四维评分详情技术面展示）
    result['technical_detail'] = _technical_detail_for_stock(stock_id)
    # 020R-37：基本面指标明细（估值/盈利/成长/现金流/财务健康，供基本面卡展示）
    result['fundamental_detail'] = _fundamental_detail_for_stock(stock_id)
    # 020R-38：资金面指标明细（主力/北向/两融，供资金面卡展示）
    result['capital_detail'] = _capital_detail_for_stock(stock_id)
    # 020R-39：消息面指标明细（情绪/股东行为，供消息面卡展示）
    result['news_detail'] = _news_detail_for_stock(stock_id)
    # 021V：快照路径同样注入打分子项（与 _enrich_advice_result 实时路径同源）
    _attach_scoring_subitems(stock_id, result)
    # 020R-43：快照路径补齐 risk_warnings（从日报 markdown 解析，与实时路径一致）
    result['risk_warnings'] = _parse_markdown_risks(row['markdown_content'])
    # 021BU：回测证据三件套（快照路径与实时路径同源；读取面现算零落库）
    _attach_backtest_evidence(result, stock_id)
    # 021BZ：当前有效共振快照（与实时路径同源同构；存量报告读取路径现算即刻带块）
    _attach_resonance_snapshot(result, stock_id)

    return jsonify(result)


@bp.route('/api/stocks/<int:stock_id>/advise', methods=['POST'])
def api_advise_stock(stock_id):
    """执行模块2分析+模块3建议生成，返回完整评级建议"""
    from modules.advisor import generate_advice

    try:
        result = generate_advice(stock_id)
        # 021K：成功时走统一增强（与 report-latest 实时路径同源，杜绝两路口径漂移）
        if result.get('success'):
            result = _enrich_advice_result(stock_id, result)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'建议生成失败: {e!s}'}), 500
