"""
模块3：评级与建议生成模块
基于模块2四维评分结果，生成可读的投资建议文本、仓位感知个性化操作指导、评级变更追踪与风险提示。

核心功能：
1. 操作建议生成（买入/加仓/持有/减仓/清仓/观望）
2. 建议详情文本（100-200字中文解读）
3. 仓位感知（浮盈浮亏 + 针对性建议）
4. 评级变更检测（对比上次评级，写入change_logs）
5. 风险提示（自动识别极端因子）
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import get_connection
from modules import scoring_engine
from modules.analysis_engine import analyze_stock
from modules.engine_switcher import record_v5_failure, record_v5_success, should_use_v5

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

DIM_NAMES = {
    'kline': '技术面',
    'fundamental': '基本面',
    'capital_flow': '资金面',
    'news': '消息面',
}

# v5引擎子项key → 中文标签映射（用于转换factors）
_V5_SUBITEM_LABELS = {
    'ma': '均线趋势',
    'trend': 'MACD趋势',
    'obos': '超买超卖',
    'vol_price': '成交量',
    'vol_ratio': '量比',
    'volatility': '波动率',
    'valuation': '估值',
    'profitability': '盈利能力',
    'growth': '成长性',
    'cashflow': '现金流',
    'fin_health': '财务健康',
    'sentiment': '市场情绪',
    'holder': '股东行为',
    'main_capital': '主力资金',
    'north_capital': '互联互通',
    'margin_capital': '杠杆资金',
}


# ============================================================
# 一、数据读取层
# ============================================================


def _read_position(stock_id):
    """读取持仓信息（014修复：优先 holdings 表，fallback positions 表）"""
    conn = get_connection()
    cursor = conn.cursor()

    # 优先查 holdings 表（新表，持仓管理页面写入）
    try:
        cursor.execute(
            "SELECT cost_price, quantity FROM holdings WHERE stock_id = ? AND status = 'active'",
            (stock_id,),
        )
        row = cursor.fetchone()
        if row and row['quantity'] and row['quantity'] > 0:
            conn.close()
            return {'cost_price': row['cost_price'], 'quantity': row['quantity']}
    except Exception:
        pass

    # Fallback: 旧 positions 表
    cursor.execute('SELECT cost_price, quantity FROM positions WHERE stock_id = ?', (stock_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row['quantity'] and row['quantity'] > 0:
        return {'cost_price': row['cost_price'], 'quantity': row['quantity']}
    return None


def _read_latest_close(stock_id):
    """读取最新收盘价"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT close, trade_date FROM raw_kline
        WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 1
    """,
        (stock_id,),
    )
    row = cursor.fetchone()
    conn.close()
    if row:
        return {'close': row['close'], 'date': row['trade_date']}
    return None


def _read_last_rating(stock_id):
    """读取上一次评级记录"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT rating, total_score, rating_date
        FROM ratings_history
        WHERE stock_id = ? ORDER BY rating_date DESC LIMIT 1
    """,
        (stock_id,),
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


# ============================================================
# 二、操作建议矩阵
# ============================================================


def _determine_action(rating, has_position, is_profitable):
    """
    根据评级+持仓状态确定操作建议。
    返回简短的操作指令字符串。
    """
    # RATING-ALIGN-004：操作建议矩阵对齐中文5档
    matrix = {
        '强烈推荐买入': {
            (False, False): '买入',
            (True, True): '加仓',
            (True, False): '继续持有',
        },
        '推荐买入': {
            (False, False): '买入',
            (True, True): '持有',
            (True, False): '继续持有',
        },
        '持有观望': {
            (False, False): '关注',
            (True, True): '持有',
            (True, False): '持有观望',
        },
        '建议减仓': {
            (False, False): '观望',
            (True, True): '持有观望',
            (True, False): '考虑减仓',
        },
        '强烈建议卖出': {
            (False, False): '回避',
            (True, True): '减仓',
            (True, False): '建议止损',
        },
    }

    rating_matrix = matrix.get(rating, matrix.get('持有观望', {}))
    key = (has_position, is_profitable if has_position else False)
    return rating_matrix.get(key, '观望')


# ============================================================
# 三、建议详情文本生成
# ============================================================


def _describe_dimension(dim_key, dim_info):
    """为单个维度生成一句话描述"""
    score = dim_info.get('score', 0)
    factors = dim_info.get('factors', {})
    name = DIM_NAMES.get(dim_key, dim_key)

    if score >= 75:
        qualifier = '表现优秀'
    elif score >= 60:
        qualifier = '表现良好'
    elif score >= 40:
        qualifier = '表现一般'
    elif score >= 20:
        qualifier = '表现较弱'
    else:
        qualifier = '表现较差'

    # 提取关键因子亮点
    highlights = []

    if dim_key == 'kline':
        ma_trend = factors.get('ma_trend', '')
        rsi_status = factors.get('rsi_status', '')
        recent = factors.get('recent_trend', '')
        if '多头' in ma_trend:
            highlights.append('均线多头排列')
        elif '空头' in ma_trend:
            highlights.append('均线空头排列')
        if '健康' in rsi_status:
            highlights.append('RSI处于健康区间')
        elif '超买' in rsi_status:
            highlights.append('RSI超买')
        elif '超卖' in rsi_status:
            highlights.append('RSI超卖')
        if recent:
            highlights.append(recent.split('(')[0])

    elif dim_key == 'fundamental':
        roe_val = factors.get('roe', '')
        debt = factors.get('debt_ratio', '')
        pe = factors.get('pe_ratio', '')
        # 019P：趋势文案优先追加（评分理由；改善/平稳入 highlights，恶化走 _detect_risks）
        fund_trend = factors.get('fund_trend', '')
        if fund_trend and '恶化' not in fund_trend and '历史数据不足' not in fund_trend:
            highlights.append(f'基本面趋势:{fund_trend}')
        if '优秀' in roe_val or '良好' in roe_val:
            highlights.append(f'ROE{roe_val.split("(")[0] if "(" in roe_val else roe_val}')
        if debt and '%' in debt:
            highlights.append(f'负债率{debt}')
        if pe and pe != '缺失':
            highlights.append(f'PE={pe}')

    elif dim_key == 'capital_flow':
        trend = factors.get('main_trend', '')
        consec = factors.get('consecutive', '')
        if '流入' in trend:
            highlights.append('主力资金流入')
        elif '流出' in trend:
            highlights.append('主力资金流出')
        if consec:
            highlights.append(consec)

    highlight_text = '，'.join(highlights[:3]) if highlights else '数据有限'
    return f'{name}{qualifier}（{score:.1f}分），{highlight_text}'


def _build_detail_text(analysis):
    """生成100-200字的综合建议详情文本"""
    stock_name = analysis.get('stock_name', analysis.get('stock_code', ''))
    total = analysis['total_score']
    rating = analysis['rating']
    rating_label = analysis.get('rating_label', '')
    dims = analysis['dimensions']

    # 找最强和最弱维度
    active_dims = []
    for dk in ['kline', 'fundamental', 'capital_flow']:
        d = dims.get(dk, {})
        if d.get('status') == 'ok':
            active_dims.append((dk, d))

    active_dims.sort(key=lambda x: x[1].get('score', 0), reverse=True)

    parts = []
    parts.append(f'{stock_name}综合评分{total:.1f}分，评级{rating}（{rating_label}）。')

    # 最强维度
    if active_dims:
        strongest = active_dims[0]
        parts.append(_describe_dimension(strongest[0], strongest[1]) + '。')

    # 最弱维度
    if len(active_dims) > 1:
        weakest = active_dims[-1]
        parts.append(_describe_dimension(weakest[0], weakest[1]) + '。')

    # 资金面单独提及（如果活跃且不是最强也不是最弱）
    cap = dims.get('capital_flow', {})
    if cap.get('status') == 'ok':
        cap_consec = cap.get('factors', {}).get('consecutive', '')
        cap_trend = cap.get('factors', {}).get('main_trend', '')
        if cap_consec or cap_trend:
            parts.append(f'资金面方面，{cap_trend}，{cap_consec}。')

    # 消息面说明
    news = dims.get('news', {})
    if news.get('status') != 'ok':
        parts.append('消息面数据暂不可用，当前评级仅基于三维数据。')

    return ''.join(parts)


# ============================================================
# 四、仓位感知建议
# ============================================================


def _build_position_advice(position, latest_close_info, rating):
    """生成仓位感知的个性化建议"""
    if not position or not latest_close_info:
        return None

    cost = position['cost_price']
    qty = position['quantity']
    latest_close = latest_close_info['close']
    latest_date = latest_close_info['date']

    profit_pct = (latest_close - cost) / cost * 100 if cost > 0 else 0
    is_profitable = profit_pct >= 0
    market_value = latest_close * qty

    parts = []
    parts.append(
        f'您当前持仓{qty}股，成本价{cost:.2f}元，最新收盘价{latest_close:.2f}元（{latest_date}），'
    )

    if is_profitable:
        parts.append(f'浮盈{profit_pct:.1f}%（市值{market_value:,.0f}元）。')
    else:
        parts.append(f'浮亏{abs(profit_pct):.1f}%（市值{market_value:,.0f}元）。')

    # RATING-ALIGN-004：仓位建议对齐中文5档
    if rating in ('强烈推荐买入', '推荐买入'):
        if is_profitable and profit_pct > 30:
            parts.append('涨幅较大，评级优良，建议持有为主，可逢回调小幅加仓。')
        elif is_profitable:
            parts.append('评级优良，建议持有，适量加仓。')
        else:
            parts.append('评级优良，当前浮亏中，建议耐心持有等待反弹。')
    elif rating == '持有观望':
        if is_profitable:
            parts.append('评级中等，建议持有，关注后续资金面变化。')
        else:
            parts.append('评级中等且浮亏，建议持有观望，设好止损位。')
    elif rating == '建议减仓':
        if is_profitable:
            parts.append('评级偏低，建议持有观望，考虑适当减仓锁定利润。')
        else:
            parts.append('评级偏低且浮亏，建议考虑减仓控制风险。')
    elif rating == '强烈建议卖出':
        if is_profitable:
            parts.append('评级较差，建议及时减仓锁定利润。')
        else:
            parts.append('评级较差，建议止损离场，控制损失。')

    return ''.join(parts)


# ============================================================
# 五、风险提示检测
# ============================================================


def _detect_risks(dimensions):
    """扫描各维度因子，自动识别风险信号"""
    risks = []

    # 技术面风险
    kl = dimensions.get('kline', {})
    kl_factors = kl.get('factors', {})

    rsi_val = kl_factors.get('rsi')
    if rsi_val is not None:
        try:
            rsi_f = float(rsi_val)
            if rsi_f > 70:
                risks.append(f'RSI={rsi_f:.1f}，已进入超买区域，短期有回调风险')
            elif rsi_f < 30:
                risks.append(f'RSI={rsi_f:.1f}，处于超卖区域，可能存在反弹机会（也意味着下行压力）')
        except (ValueError, TypeError):
            pass

    ma_trend = kl_factors.get('ma_trend', '')
    if '空头' in ma_trend:
        risks.append('MA5低于MA20，均线空头排列，短期趋势偏弱')

    boll_pos = kl_factors.get('boll_position', '')
    if boll_pos and '%' in boll_pos:
        try:
            pos_val = float(boll_pos.replace('%', ''))
            if pos_val > 90:
                risks.append(f'布林带位置{boll_pos}，触及上轨，回调风险加大')
        except (ValueError, TypeError):
            pass

    vol = kl_factors.get('volume', '')
    if '缩量' in vol:
        risks.append(f'成交量{vol}，上涨动能可能减弱')

    # 基本面风险
    fund = dimensions.get('fundamental', {})
    fund_factors = fund.get('factors', {})

    pe = fund_factors.get('pe_ratio')
    if pe and pe != '缺失':
        try:
            pe_f = float(pe)
            if pe_f > 60:
                risks.append(f'PE={pe_f:.1f}，估值严重偏高')
            elif pe_f > 40:
                risks.append(f'PE={pe_f:.1f}，估值偏高')
        except (ValueError, TypeError):
            pass

    pb = fund_factors.get('pb_ratio')
    if pb and pb != '缺失':
        try:
            pb_f = float(pb)
            if pb_f > 6:
                risks.append(f'PB={pb_f:.1f}，市净率过高')
        except (ValueError, TypeError):
            pass

    roe_val = fund_factors.get('roe', '')
    if '亏损' in roe_val:
        risks.append(f'ROE{roe_val}，公司处于亏损状态')

    # 019P：基本面趋势恶化 → 追加恶化提示（风险提示；仅展示不进评分）
    fund_trend = fund_factors.get('fund_trend', '')
    if fund_trend and '恶化' in fund_trend:
        risks.append(f'基本面趋势恶化：{fund_trend}')

    # 资金面风险
    cap = dimensions.get('capital_flow', {})
    cap_factors = cap.get('factors', {})

    main_trend = cap_factors.get('main_trend', '')
    if '持续流出' in main_trend or '温和流出' in main_trend:
        risks.append(f'主力资金{main_trend.split("(")[0]}')

    consec = cap_factors.get('consecutive', '')
    if '连续流出' in consec:
        risks.append(f'资金{consec}，流出趋势明显')

    main_pct = cap_factors.get('main_pct', '')
    if main_pct and '%' in main_pct:
        try:
            pct_val = float(main_pct.replace('%', ''))
            if pct_val < -5:
                risks.append(f'主力净流入占比{main_pct}，资金流出幅度较大')
        except (ValueError, TypeError):
            pass

    return risks


# ============================================================
# 五-1、日报关键因子与 Markdown 构建（019A 从 daily_report 收敛至 advisor，单一来源）
# ============================================================


def _pick_top_factors(dim_key, factors_dict):
    """提取每个维度最多3个关键因子"""
    priority = {
        'kline': [
            'ma_trend',
            'rsi_status',
            'recent_trend',
            'volume',
            'boll_position',
            'dimension_score',
            'data_completeness',
        ],
        'fundamental': [
            # 019P：fund_trend 首位（日报关键因子必显，监理核心诉求可见性落地）
            'fund_trend',
            'pe_ratio',
            'roe',
            'revenue_growth',
            'pb_ratio',
            'net_margin',
            'debt_ratio',
            'dimension_score',
            'data_completeness',
        ],
        'capital_flow': [
            'main_trend',
            'consecutive',
            'main_pct',
            'super_large',
            'main_avg_5d',
            'dimension_score',
            'data_completeness',
        ],
        'news': [
            'avg_sentiment',
            'positive_ratio',
            'top_news',
            'news_activity',
            'extreme_warning',
            'dimension_score',
            'data_completeness',
        ],
    }
    keys = priority.get(dim_key, [])
    result = {}
    count = 0
    # 020R-40：数据完整度固定包含（report-latest 快照路径据此计算 data_quality，
    # 与实时 advise 路径保持一致；原先排优先级末尾永远取不到）
    if factors_dict.get('data_completeness') is not None:
        result['data_completeness'] = factors_dict['data_completeness']
        count += 1
    for k in keys:
        if k in factors_dict and factors_dict[k] is not None:
            # 020R-6：不再截断因子值——长文本完整入参，展示层负责换行完整显示
            result[k] = factors_dict[k]
            count += 1
            if count >= 3:
                break
    # 补充其他因子
    for k, v in factors_dict.items():
        if count >= 4:
            break
        if k not in result and not k.startswith('_') and v is not None:
            # 020R-6：不再截断因子值（同上）
            result[k] = v
            count += 1
    return result


def _build_key_factors(advice_result):
    """从 advisor 结果提取关键因子摘要（019A 收敛至 advisor，单一来源）"""
    factors = {}
    dims = advice_result.get('dimensions', {})

    for dim_key in ['kline', 'fundamental', 'capital_flow', 'news']:
        d = dims.get(dim_key, {})
        if d.get('status') == 'ok':
            factors[dim_key] = {
                'score': round(d.get('score', 0), 1),
                'weight': round(d.get('weight', 0), 4),
                'top_factors': _pick_top_factors(dim_key, d.get('factors', {})),
            }

    return factors


def _build_markdown_single(advice_result, prev_score):
    """构建单只股票的 Markdown 报告片段（019A 收敛至 advisor，单一来源）"""
    code = advice_result.get('stock_code', '')
    name = advice_result.get('stock_name', '')
    engine = advice_result.get('engine_version', 'legacy')
    total = advice_result.get('total_score', 0)
    rating = advice_result.get('rating', '?')
    rating_label = advice_result.get('rating_label', '')
    action = advice_result.get('action_advice', '')

    engine_tag = '🚀 v5引擎' if engine == 'v5' else '⚙️ 经典引擎（简化版）'

    md = f'### {name} ({code}) — {engine_tag}\n\n'

    # 评分行
    score_change_str = ''
    if prev_score is not None:
        change = total - prev_score
        arrow = '↑' if change > 0 else ('↓' if change < 0 else '→')
        score_change_str = f'（较昨日 {arrow} {abs(change):.1f}）'
    md += f'- **综合评分**：{total:.1f}（{rating}级 · {rating_label}）{score_change_str}\n'
    md += f'- **操作建议**：{action}\n'

    # 四维评分
    dims = advice_result.get('dimensions', {})
    dim_names = {
        'kline': '技术面',
        'fundamental': '基本面',
        'capital_flow': '资金面',
        'news': '消息面',
    }
    dim_scores = []
    for dk in ['kline', 'fundamental', 'capital_flow', 'news']:
        d = dims.get(dk, {})
        score = d.get('score', 0) if d.get('status') == 'ok' else None
        if score is not None:
            dim_scores.append(f'{dim_names[dk]} {score:.1f}')
        else:
            dim_scores.append(f'{dim_names[dk]} —')
    md += f'- **四维**：{" | ".join(dim_scores)}\n'

    # 数据完整度（仅v5）
    if advice_result.get('data_quality'):
        dq = advice_result['data_quality']
        md += (
            f'- **数据完整度**：技术{int(dq.get("technical", 0) * 100)}% '
            f'基本{int(dq.get("fundamental", 0) * 100)}% '
            f'资金{int(dq.get("capital", 0) * 100)}% '
            f'消息{int(dq.get("news", 0) * 100)}%\n'
        )

    # 降级提示
    warnings = advice_result.get('data_warnings', [])
    if warnings:
        md += f'- **降级提示**：{len(warnings)}条降级规则触发\n'

    # 风险提示
    risks = advice_result.get('risk_warnings', [])
    if risks:
        md += '- **风险提示**：\n'
        for r in risks[:3]:
            md += f'  - {r}\n'

    md += '\n'
    return md


# ============================================================
# 六、数据库写入
# ============================================================


def _save_daily_report_for_advice(stock_id, analysis, prev_score, engine_used, report_date=None):
    """019A 修复：generate_advice 统一回写 daily_reports 表

    确保每日报告/批量分析/手动刷新/一键分析任一入口触发后，
    daily_reports 与 analysis_results、ratings_history 三表评分一致。
    已有日报时 UPDATE 评分字段并保留 markdown_content/price_advice；
    无日报时 INSERT 新记录。失败不阻塞主流程。
    """
    try:
        total_score = analysis.get('total_score')
        if total_score is None:
            return
        conn = get_connection()
        cursor = conn.cursor()

        report_date = report_date or datetime.now(_CN_TZ).strftime('%Y-%m-%d')
        rating = analysis.get('rating', '')
        rating_label = analysis.get('rating_label', '')
        score_change = (
            round(float(total_score) - float(prev_score), 1) if prev_score is not None else None
        )
        key_factors = _build_key_factors(analysis)
        data_warnings = list(analysis.get('data_warnings', []) or [])
        # 020R-41：刷新报告/一键分析路径补齐「数据完整度」行（与每日报告路径一致），
        # 避免覆盖 daily_reports 后完整度提示丢失（原路径只有引擎降级提示）
        try:
            from modules.daily_report import _build_data_freshness

            freshness = _build_data_freshness(stock_id)
            data_warnings = data_warnings + [
                f'数据完整度：{line}' for line in (freshness.get('lines') or [])
            ]
        except Exception as e:  # noqa: BLE001
            logger.warning(f'[020R-41] 数据完整度行补充失败 stock_id={stock_id}: {e}')
        generated_at = datetime.now(_CN_TZ).isoformat()
        markdown = _build_markdown_single(analysis, prev_score)

        # 查询是否已有该日该股票 daily 报告
        cursor.execute(
            "SELECT id, markdown_content, price_advice FROM daily_reports "
            "WHERE stock_id=? AND report_date=? AND report_type='daily'",
            (stock_id, report_date),
        )
        existing = cursor.fetchone()

        if existing:
            cursor.execute(
                """UPDATE daily_reports
                   SET engine_version=?, total_score=?, rating=?, rating_label=?,
                       prev_score=?, score_change=?, key_factors=?, data_warnings=?,
                       status='ok', error_msg=NULL, generated_at=?, markdown_content=?
                   WHERE id=?""",
                (
                    engine_used,
                    total_score,
                    rating,
                    rating_label,
                    prev_score,
                    score_change,
                    json.dumps(key_factors, ensure_ascii=False) if key_factors else None,
                    json.dumps(data_warnings, ensure_ascii=False) if data_warnings else None,
                    generated_at,
                    markdown,
                    existing['id'],
                ),
            )
        else:
            # 与 _save_report 的 daily 语义保持一致：当天无 daily 记录时，
            # 先删除当天该股票所有记录（含 intraday），避免 daily+intraday 并存
            cursor.execute(
                'DELETE FROM daily_reports WHERE report_date=? AND stock_id=?',
                (report_date, stock_id),
            )
            cursor.execute(
                """INSERT INTO daily_reports
                   (report_date, stock_id, stock_code, stock_name, engine_version,
                    total_score, rating, rating_label, prev_score, score_change,
                    key_factors, data_warnings, status, error_msg, markdown_content,
                    generated_at, price_advice, report_type)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'ok', NULL, ?, ?, NULL, 'daily')""",
                (
                    report_date,
                    stock_id,
                    analysis.get('stock_code', ''),
                    analysis.get('stock_name', ''),
                    engine_used,
                    total_score,
                    rating,
                    rating_label,
                    prev_score,
                    score_change,
                    json.dumps(key_factors, ensure_ascii=False) if key_factors else None,
                    json.dumps(data_warnings, ensure_ascii=False) if data_warnings else None,
                    markdown,
                    generated_at,
                ),
            )
        conn.commit()
        conn.close()
        logger.info(f'stock_id={stock_id} 建议已同步写入 daily_reports (score={total_score})')
    except Exception as e:
        logger.error(f'generate_advice 回写 daily_reports 失败: {e}')


def _save_rating(stock_id, analysis, action_advice, is_changed, latest_close):
    """将评级记录写入 ratings_history 表

    B12-T2: price_at_rating 使用 rating_date 对应的 K 线收盘价，
    而非分析运行时的最新 K 线（避免 K 线未采集时取到前一日价格）。
    latest_close 参数保留（不破坏调用方签名），但不再用于 price_at_rating。
    """
    rating_date = analysis.get('score_date', datetime.now(_CN_TZ).strftime('%Y-%m-%d'))

    conn = get_connection()
    cursor = conn.cursor()

    # B12-T2: price_at_rating 使用 rating_date 对应的 K 线收盘价
    cursor.execute(
        """
        SELECT close FROM raw_kline
        WHERE stock_id = ? AND trade_date <= ?
        ORDER BY trade_date DESC LIMIT 1
    """,
        (stock_id, rating_date),
    )
    row = cursor.fetchone()
    price = float(row['close']) if row and row['close'] is not None else None

    cursor.execute(
        """
        INSERT OR REPLACE INTO ratings_history
        (stock_id, rating_date, rating, total_score, action_advice, is_change, price_at_rating)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (
            stock_id,
            rating_date,
            analysis['rating'],
            analysis['total_score'],
            action_advice,
            1 if is_changed else 0,
            price,
        ),
    )

    conn.commit()
    conn.close()
    logger.info(f'stock_id={stock_id} 评级记录已写入 ratings_history (price={price})')


def _save_analysis_results_for_v5(stock_id, analysis, operation_suggestion='', report_date=None):
    """P3-A 引擎对齐修复：v5路径同步写入 analysis_results 表

    确保 /api/ratings（读 analysis_results）与每日报告（读 daily_reports）
    数据源一致。仅当走 v5 引擎时调用；旧引擎路径内部分析时会自行写入。
    """
    try:
        dims = analysis.get('dimensions', {})
        rating_time = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M')
        if report_date:
            score_date = report_date
        else:
            score_date = analysis.get('score_date', rating_time[:10])

        # 构造与旧引擎一致的 data_warnings 格式
        warnings = []
        for dim_key, dim_info in dims.items():
            status = dim_info.get('status', '')
            if status != 'ok':
                warnings.append(
                    {'dimension': dim_key, 'status': status, 'reason': dim_info.get('reason', '')}
                )

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO analysis_results
            (stock_id, analysis_date,
             fundamental_score, technical_score, sentiment_score, capital_score,
             fundamental_weight, technical_weight, sentiment_weight, capital_weight,
             total_score, rating, data_warnings,
             rating_time, operation_suggestion)
            VALUES (?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?, ?,
                    ?, ?, ?,
                    ?, ?)
        """,
            (
                stock_id,
                score_date,
                dims.get('fundamental', {}).get('score', 0),
                dims.get('kline', {}).get('score', 0),
                dims.get('news', {}).get('score', 0),
                dims.get('capital_flow', {}).get('score', 0),
                dims.get('fundamental', {}).get('weight', 0),
                dims.get('kline', {}).get('weight', 0),
                dims.get('news', {}).get('weight', 0),
                dims.get('capital_flow', {}).get('weight', 0),
                analysis['total_score'],
                analysis['rating'],
                json.dumps(warnings, ensure_ascii=False),
                rating_time,
                operation_suggestion,
            ),
        )
        conn.commit()
        conn.close()
        logger.info(f'stock_id={stock_id} v5分析结果已同步写入 analysis_results')
    except Exception as e:
        logger.error(f'v5路径写入 analysis_results 失败: {e}')


def _save_change_log(stock_id, prev_rating, new_rating, prev_score, new_score):
    """评级变化时写入 change_logs 表"""
    conn = get_connection()
    cursor = conn.cursor()

    # 评级档位变化
    if prev_rating != new_rating:
        cursor.execute(
            """
            INSERT INTO change_logs (stock_id, log_type, dimension, old_value, new_value, description)
            VALUES (?, 'rating_change', NULL, ?, ?, ?)
        """,
            (stock_id, prev_rating, new_rating, f'评级从{prev_rating}变为{new_rating}'),
        )

    # 分数变化超过5分
    if prev_score is not None and new_score is not None:
        score_diff = new_score - prev_score
        if abs(score_diff) >= 5:
            cursor.execute(
                """
                INSERT INTO change_logs (stock_id, log_type, dimension, old_value, new_value, description)
                VALUES (?, 'score_change', NULL, ?, ?, ?)
            """,
                (
                    stock_id,
                    f'{prev_score:.1f}',
                    f'{new_score:.1f}',
                    f'综合评分从{prev_score:.1f}变为{new_score:.1f}（{"上升" if score_diff > 0 else "下降"}{abs(score_diff):.1f}分）',
                ),
            )

    conn.commit()
    conn.close()


# ============================================================
# 七、主入口
# ============================================================


def _convert_v5_to_legacy(stock_id: int, v5_result) -> dict:
    """将 v5 AnalysisResult 转换为旧引擎兼容的分析结果格式

    确保前端报告页面无需修改即可同时展示新旧引擎结果。
    """
    # 读取股票信息
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT symbol, name, market FROM stocks WHERE id = ?', (stock_id,))
    stock_row = cursor.fetchone()
    conn.close()
    symbol = stock_row['symbol'] if stock_row else ''
    name = stock_row['name'] if stock_row else ''
    market = stock_row['market'] if stock_row else ''

    # 维度得分映射（v5 → 旧格式key）
    dim_scores = {
        'kline': v5_result.technical_score,
        'fundamental': v5_result.fundamental_score,
        'capital_flow': v5_result.capital_score,
        'news': v5_result.sentiment_score,
    }
    dim_weights = {
        'kline': v5_result.technical_weight,
        'fundamental': v5_result.fundamental_weight,
        'capital_flow': v5_result.capital_weight,
        'news': v5_result.sentiment_weight,
    }

    # B20：加载 StockData 一次，供 _build_v5_factors 构建具体因子明细
    # （MA/RSI/BOLL 等技术指标已在 data_adapter.load_stockdata_from_db 内计算完成）
    from modules.data_adapter import load_stockdata_from_db

    stock_data = None
    try:
        stock_data = load_stockdata_from_db(stock_id)
    except Exception as e:
        logger.warning(f'[B20] stock_id={stock_id} 加载 StockData 失败: {e}')

    # 转换维度详情（从 StockData + v5_result 构建 factors）
    dimensions = {}
    for dim_key, score_val in dim_scores.items():
        weight_val = dim_weights[dim_key]
        if score_val is not None:
            # 构建 factors（用于前端报告页面展示）
            factors = _build_v5_factors(stock_id, stock_data, v5_result, dim_key)
            dimensions[dim_key] = {
                'score': round(score_val, 1),
                'weight': round(weight_val, 4),
                'factors': factors,
                'status': 'ok',
            }
        else:
            dimensions[dim_key] = {
                'score': 0,
                'weight': 0,
                'factors': {},
                'status': 'unavailable',
            }

    return {
        'success': True,
        'stock_code': symbol,
        'stock_name': name,
        'market': market,
        'score_date': v5_result.score_date,
        'total_score': v5_result.total_score,
        'rating': v5_result.rating,
        'rating_label': v5_result.rating_label,
        'dimensions': dimensions,
        'data_cutoff': {},
        'news_summary': '',
        'engine_version': 'v5',
        'data_warnings': v5_result.data_warnings,
        'data_quality': v5_result.data_quality,
    }


def _build_v5_factors(stock_id, stock_data, v5_result, dim_key):
    """构建前端可读的 factors 字典（B20 修复版）

    旧版仅输出 dimension_score + data_completeness，导致前端四维详情卡片
    显示"暂无关键因子"。本版从 StockData 契约（含已计算的 MA/RSI/BOLL 等技术指标）
    及 DB 原始表读取具体因子，输出前端 _pickTopFactors 期望的 key
    （ma_trend/rsi_status/pe_ratio/roe/main_trend/avg_sentiment 等）。

    Args:
        stock_id: 股票 id
        stock_data: data_adapter.load_stockdata_from_db 返回的 StockData（可能为 None）
        v5_result: AnalysisResult 对象
        dim_key: 维度 key（kline/fundamental/capital_flow/news）
    """
    factors = {}

    # —— 保留：维度得分摘要（_pick_top_factors 兼容字段）——
    score_map = {
        'kline': v5_result.technical_score,
        'fundamental': v5_result.fundamental_score,
        'capital_flow': v5_result.capital_score,
        'news': v5_result.sentiment_score,
    }
    score = score_map.get(dim_key)
    if score is not None:
        factors['dimension_score'] = f'{score:.1f}'

    # —— 保留：数据完整度（report-latest L837 依赖此字段计算 data_quality）——
    if v5_result.data_quality:
        dq_map = {
            'kline': 'technical',
            'fundamental': 'fundamental',
            'capital_flow': 'capital',
            'news': 'news',
        }
        dq_key = dq_map.get(dim_key)
        if dq_key and dq_key in v5_result.data_quality:
            completeness = v5_result.data_quality[dq_key]
            factors['data_completeness'] = f'{completeness:.0%}'

    # —— 保留：降级提示（仅当字段缺失时补充，不影响真实因子优先展示）——
    dim_field_map = {
        'kline': [
            'ma5',
            'ma10',
            'ma20',
            'ma60',
            'macd_dif',
            'macd_dea',
            'kdj_k',
            'rsi_14',
            'volume',
            'volume_ratio',
            'boll_upper',
            'boll_lower',
        ],
        'fundamental': [
            'pe_ttm',
            'pb',
            'roe',
            'gross_margin',
            'revenue_yoy',
            'net_profit_yoy',
            'ocf_to_profit',
            'debt_to_asset',
            'current_ratio',
        ],
        'news': ['news_sentiment', 'holder_increase'],
        'capital_flow': ['main_net_inflow', 'north_net_buy', 'margin_balance_chg'],
    }
    dim_fields = dim_field_map.get(dim_key, [])
    for field in dim_fields:
        if field in v5_result.degradations:
            rule = v5_result.degradations[field]
            if '归零' in rule:
                factors[field + '_status'] = '缺失(已归零)'
            elif '降权' in rule:
                factors[field + '_status'] = '缺失(已降权)'
            elif '默认值' in rule or '中性' in rule:
                factors[field + '_status'] = '缺失(默认填充)'

    # —— 新增（B20）：具体因子明细 ——
    if stock_data is None:
        return factors

    try:
        if dim_key == 'kline':
            _build_kline_factors(factors, stock_data, stock_id)
        elif dim_key == 'fundamental':
            _build_fundamental_factors(factors, stock_data, stock_id)
        elif dim_key == 'capital_flow':
            _build_capital_factors(factors, stock_data, stock_id)
        elif dim_key == 'news':
            _build_news_factors(factors, stock_data, stock_id)
    except Exception as e:
        logger.warning(f'[B20] stock_id={stock_id} dim={dim_key} 因子构建异常: {e}')

    return factors


def _build_kline_factors(factors, stock_data, stock_id):
    """技术面因子：ma_trend / rsi_status / recent_trend / volume / boll_position"""
    ma5, ma20 = stock_data.ma5, stock_data.ma20
    if ma5 is not None and ma20 is not None:
        if ma5 > ma20:
            factors['ma_trend'] = f'多头排列(MA5={ma5:.2f} > MA20={ma20:.2f})'
        else:
            factors['ma_trend'] = f'空头排列(MA5={ma5:.2f} < MA20={ma20:.2f})'

    rsi = stock_data.rsi_14
    if rsi is not None:
        status = '超买' if rsi > 70 else ('超卖' if rsi < 30 else '正常')
        factors['rsi_status'] = f'{status}({rsi:.1f})'

    close = stock_data.close
    bu, bl = stock_data.boll_upper, stock_data.boll_lower
    if close is not None and bu is not None and bl is not None:
        if close >= bu:
            factors['boll_position'] = '触及上轨(超买区)'
        elif close <= bl:
            factors['boll_position'] = '触及下轨(超卖区)'
        else:
            factors['boll_position'] = '通道内运行'

    vol = stock_data.volume
    if vol is not None and vol > 0:
        if vol >= 1e8:
            factors['volume'] = f'{vol / 1e8:.2f}亿股'
        elif vol >= 1e4:
            factors['volume'] = f'{vol / 1e4:.1f}万股'
        else:
            factors['volume'] = f'{vol:.0f}股'

    # 近5日走势（从 DB 读取最近6个收盘价计算累计涨跌幅）
    try:
        conn = get_connection()
        c = conn.cursor()
        rows = c.execute(
            'SELECT close FROM raw_kline WHERE stock_id=? AND close IS NOT NULL '
            'ORDER BY trade_date DESC LIMIT 6',
            (stock_id,),
        ).fetchall()
        conn.close()
        if rows and len(rows) >= 6:
            latest_c = float(rows[0]['close'])
            base_c = float(rows[5]['close'])
            if base_c > 0:
                pct = (latest_c - base_c) / base_c * 100
                direction = '上涨' if pct > 0 else ('下跌' if pct < 0 else '持平')
                factors['recent_trend'] = f'近5日{direction}{abs(pct):.1f}%'
    except Exception as e:
        logger.debug(f'[B20] kline recent_trend 计算失败 stock_id={stock_id}: {e}')


# ============================================================
# 019P：基本面趋势分析（仅展示不进评分，口径双轨制 M-3/A-2）
# 红线：scoring_engine / config_weights / data_contract 零改动（评分反映当前快照，趋势是派生信息）
# ============================================================
_FUND_TREND_THRESHOLD = 1.0  # 019P：|Δ| < 1pct 视为"平稳"（防噪声）

# 019P：环比指标清单（期间/时点型，可直接比较相邻期，"较上期"）
# (DB列, 中文名, 是否越小越好) —— 负债率下降=改善
_FUND_TREND_QOQ_METRICS = [
    ('gross_margin', '毛利率', False),
    ('net_margin', '净利率', False),
    ('debt_ratio', '负债率', True),
    ('current_ratio', '流动比率', False),
    ('quick_ratio', '速动比率', False),
]


def _fund_trend_state(delta, lower_better=False):
    """019P：|Δ| < 1pct → 平稳；否则按方向判定改善/恶化"""
    if delta is None or abs(delta) < _FUND_TREND_THRESHOLD:
        return '平稳'
    improved = delta < 0 if lower_better else delta > 0
    return '改善' if improved else '恶化'


def _build_fund_trend(stock_id):
    """019P（M-3/A-2）：从 raw_fundamental 多期行计算基本面趋势。

    口径双轨制：
    - 环比仅限期间/时点型指标（毛利率/净利率/资产负债率/流动比率/速动比率，文案"较上期"）
    - 累计型 ROE 禁止环比（Q1 vs 年报不可比 R-5）→ 仅同比
      （report_date 前推 1 年匹配同类型报告期，无同期数据跳过该指标）
    - 增速指标（营收/净利增长）不做环比，表述"增速加快/放缓"（比较相邻两期增速）
    - 变化阈值：|Δ| < 1pct 视为"平稳"
    数据不足兜底（R-9）：缺期时跳过对应指标；全缺输出"历史数据不足，暂无趋势判断"，不崩溃。
    混合来源比较接受（存量旧期 analysis_indicator + 新期 abstract，同新浪域可比，文档化）。

    Returns:
        (summary: str, details: list[str], direction: str)
        direction: 'improve' / 'worsen' / 'flat' / 'insufficient'
    """
    try:
        conn = get_connection()
        c = conn.cursor()
        rows = c.execute(
            'SELECT report_date, roe, gross_margin, net_margin, debt_ratio, current_ratio, '
            'quick_ratio, revenue_growth, profit_growth FROM raw_fundamental '
            'WHERE stock_id = ? AND report_date != "" AND report_date IS NOT NULL '
            'ORDER BY report_date DESC LIMIT 8',
            (stock_id,),
        ).fetchall()
        conn.close()
    except Exception as e:
        logger.debug(f'[019P] fund_trend 查询失败 stock_id={stock_id}: {e}')
        return '历史数据不足，暂无趋势判断', [], 'insufficient'

    rows = [dict(r) for r in rows]
    if not rows:
        return '历史数据不足，暂无趋势判断', [], 'insufficient'

    latest = rows[0]
    latest_date = str(latest.get('report_date') or '')[:10]
    details = []
    improve_cnt = 0
    worsen_cnt = 0

    def _fmt(v):
        try:
            return f'{float(v):.2f}'
        except (ValueError, TypeError):
            return str(v)

    # ---- 1. 环比指标（期间/时点型，"较上期"）----
    for col, label, lower_better in _FUND_TREND_QOQ_METRICS:
        cur = latest.get(col)
        if cur is None:
            continue
        prev = None
        for r in rows[1:]:
            if r.get(col) is not None:
                prev = r[col]
                break
        if prev is None:
            continue
        delta = float(cur) - float(prev)
        state = _fund_trend_state(delta, lower_better)
        if state == '改善':
            improve_cnt += 1
        elif state == '恶化':
            worsen_cnt += 1
        details.append(f'{label}较上期{state}({_fmt(prev)}%→{_fmt(cur)}%)')

    # ---- 2. ROE 仅同比（累计型禁止环比 R-5）----
    roe_cur = latest.get('roe')
    if roe_cur is not None and latest_date:
        target = None
        try:
            y, m, d = int(latest_date[:4]), latest_date[5:7], latest_date[8:10]
            target = f'{y - 1:04d}-{m}-{d}'
        except (ValueError, TypeError, IndexError):
            target = None
        if target:
            for r in rows:
                if str(r.get('report_date') or '')[:10] == target and r.get('roe') is not None:
                    prev_roe = float(r['roe'])
                    state = _fund_trend_state(float(roe_cur) - prev_roe, False)
                    if state == '改善':
                        improve_cnt += 1
                    elif state == '恶化':
                        worsen_cnt += 1
                    details.append(f'ROE同比{state}({_fmt(prev_roe)}%→{_fmt(roe_cur)}%)')
                    break

    # ---- 3. 增速指标（营收/净利增长）：表述"增速加快/放缓"（比较相邻两期增速）----
    for col, label in (('revenue_growth', '营收增速'), ('profit_growth', '净利增速')):
        cur = latest.get(col)
        if cur is None:
            continue
        prev = None
        for r in rows[1:]:
            if r.get(col) is not None:
                prev = r[col]
                break
        if prev is None:
            continue
        delta = float(cur) - float(prev)
        if abs(delta) < _FUND_TREND_THRESHOLD:
            state = '平稳'
        else:
            state = '加快' if delta > 0 else '放缓'
            if delta > 0:
                improve_cnt += 1
            else:
                worsen_cnt += 1
        details.append(f'{label}{state}({_fmt(prev)}%→{_fmt(cur)}%)')

    if not details:
        return '历史数据不足，暂无趋势判断', [], 'insufficient'

    # ---- 4. 汇总（多数投票 + 阈值）----
    if improve_cnt > worsen_cnt:
        direction = 'improve'
        summary = '基本面较上期改善'
    elif worsen_cnt > improve_cnt:
        direction = 'worsen'
        summary = '基本面较上期恶化'
    else:
        direction = 'flat'
        summary = '基本面较上期平稳'

    if details and direction in ('improve', 'worsen'):
        summary += '（' + '、'.join(details[:3]) + '）'
    return summary, details, direction


def _build_fundamental_factors(factors, stock_data, stock_id):
    """基本面因子：pe_ratio / roe / pb_ratio / revenue_growth / debt_ratio / net_margin
    019P：追加 fund_trend（趋势汇总，仅展示不进评分）"""
    pe = stock_data.pe_ttm
    if pe is not None:
        factors['pe_ratio'] = f'{pe:.2f}'

    roe = stock_data.roe
    if roe is not None:
        factors['roe'] = f'{roe:.2f}%'

    pb = stock_data.pb
    if pb is not None:
        factors['pb_ratio'] = f'{pb:.2f}'

    rev = stock_data.revenue_yoy
    if rev is not None:
        factors['revenue_growth'] = f'{rev:.2f}%'

    debt = stock_data.debt_to_asset
    if debt is not None:
        factors['debt_ratio'] = f'{debt:.2f}%'

    # net_margin / gross_margin 不在 StockData 契约中，从 DB 补充
    try:
        conn = get_connection()
        c = conn.cursor()
        row = c.execute(
            'SELECT net_margin, gross_margin FROM raw_fundamental '
            'WHERE stock_id=? ORDER BY report_date DESC LIMIT 1',
            (stock_id,),
        ).fetchone()
        conn.close()
        if row:
            nm = row['net_margin']
            if nm is not None:
                factors['net_margin'] = f'{nm:.2f}%'
            gm = row['gross_margin']
            if gm is not None:
                factors['gross_margin'] = f'{gm:.2f}%'
    except Exception as e:
        logger.debug(f'[B20] fundamental net_margin 读取失败 stock_id={stock_id}: {e}')

    # 019P（M-3/A-2）：趋势因子（仅展示不进评分）
    # 数据源：raw_fundamental 多期行（abstract 写 8 期 + 存量回补后天然具备）
    try:
        trend_summary, trend_details, _trend_dir = _build_fund_trend(stock_id)
        if trend_summary:
            factors['fund_trend'] = trend_summary
            if trend_details:
                factors['fund_trend_detail'] = '；'.join(trend_details[:3])
    except Exception as e:
        logger.debug(f'[019P] fund_trend 计算失败 stock_id={stock_id}: {e}')


def _build_capital_factors(factors, stock_data, stock_id):
    """资金面因子：main_trend / main_pct / super_large / main_avg_5d / consecutive"""
    inflow = stock_data.main_net_inflow
    if inflow is not None:
        direction = '净流入' if inflow > 0 else ('净流出' if inflow < 0 else '持平')
        factors['main_trend'] = f'主力{direction}{abs(inflow):.0f}万元'

    # 从 DB 读取资金面明细（占比/超大单/连续性/5日均）
    try:
        conn = get_connection()
        c = conn.cursor()
        # 019E-R2：过滤估算行（is_estimated=1），确保评分仅使用真实数据
        rows = c.execute(
            'SELECT trade_date, main_net_inflow, main_net_inflow_pct, super_large_net '
            'FROM raw_capital_flow WHERE stock_id=? '
            'AND (is_estimated = 0 OR is_estimated IS NULL) '
            'ORDER BY trade_date DESC LIMIT 5',
            (stock_id,),
        ).fetchall()
        conn.close()
        if rows:
            latest = rows[0]
            pct = latest['main_net_inflow_pct']
            if pct is not None:
                factors['main_pct'] = f'{pct:.2f}%'
            sl = latest['super_large_net']
            if sl is not None:
                tag = '流入' if sl > 0 else '流出'
                factors['super_large'] = f'超大单净{abs(sl):.0f}万元({tag})'

            valid = [float(r['main_net_inflow']) for r in rows if r['main_net_inflow'] is not None]
            if valid:
                avg5 = sum(valid) / len(valid)
                tag = '净流入' if avg5 > 0 else '净流出'
                factors['main_avg_5d'] = f'5日均{tag}{abs(avg5):.0f}万元'

                if len(valid) >= 2:
                    streak = 0
                    pos = valid[0] > 0
                    for v in valid:
                        if v != 0 and (v > 0) == pos:
                            streak += 1
                        else:
                            break
                    if streak >= 2:
                        tag = '连续净流入' if pos else '连续净流出'
                        factors['consecutive'] = f'{tag}{streak}日'
    except Exception as e:
        logger.debug(f'[B20] capital 明细读取失败 stock_id={stock_id}: {e}')


def _build_news_factors(factors, stock_data, stock_id):
    """消息面因子：avg_sentiment / positive_ratio / news_activity / top_news"""
    sent = stock_data.news_sentiment
    if sent is not None:
        tag = '正面' if sent > 0.05 else ('负面' if sent < -0.05 else '中性')
        factors['avg_sentiment'] = f'{sent:+.2f}({tag})'

    try:
        conn = get_connection()
        c = conn.cursor()
        row = c.execute(
            'SELECT avg_sentiment, positive_count, negative_count, neutral_count, '
            'total_count, top_news_title FROM news_sentiment '
            'WHERE stock_id=? ORDER BY news_date DESC LIMIT 1',
            (stock_id,),
        ).fetchone()
        conn.close()
        if row:
            pos = row['positive_count'] or 0
            neg = row['negative_count'] or 0
            neu = row['neutral_count'] or 0
            total = row['total_count'] or (pos + neg + neu)
            if total > 0:
                factors['news_count'] = f'{total}条'
                factors['positive_ratio'] = f'正面{pos}/负面{neg}/中性{neu}'
                factors['news_activity'] = f'近日本{total}条'
            title = row['top_news_title']
            if title:
                # 020R-6：新闻标题不再截断（展示层换行完整显示）
                factors['top_news'] = str(title)
    except Exception as e:
        logger.debug(f'[B20] news 明细读取失败 stock_id={stock_id}: {e}')


def generate_advice(stock_id, report_date=None):
    """
    主入口：调用分析引擎 + 生成完整建议。
    支持灰度切换：根据 engine_switcher 配置决定使用 v5 或旧引擎。
    v5引擎异常时自动降级到旧引擎。

    返回包含评级、建议、风险提示的完整JSON结构。
    """
    logger.info(f'开始生成建议 stock_id={stock_id}')

    # 0. 灰度切换：判断使用哪个引擎
    use_v5 = should_use_v5(stock_id)
    analysis = None
    engine_used = 'legacy'

    if use_v5:
        try:
            v5_result = scoring_engine.analyze_from_db(stock_id)
            if v5_result is not None:
                analysis = _convert_v5_to_legacy(stock_id, v5_result)
                engine_used = 'v5'
                logger.info(f'[stock_id={stock_id}] 使用 v5 引擎评分成功')
                # P3-A: 记录 v5 成功（重置熔断计数）
                record_v5_success(stock_id)
                # P3-A 引擎对齐修复：v5路径同步写入 analysis_results 表
                # 确保 /api/ratings（读 analysis_results）与每日报告数据源一致
                _save_analysis_results_for_v5(stock_id, analysis, '', report_date=report_date)
            else:
                logger.warning(f'[stock_id={stock_id}] v5引擎返回None，降级到旧引擎')
                # P3-A: 记录 v5 失败（递增熔断计数）
                record_v5_failure(stock_id)
        except Exception as e:
            logger.warning(f'[stock_id={stock_id}] v5引擎异常({e})，降级到旧引擎')
            # P3-A: 记录 v5 失败（递增熔断计数）
            record_v5_failure(stock_id)

    # v5不可用时使用旧引擎
    if analysis is None:
        analysis = analyze_stock(stock_id)
        engine_used = 'legacy'
        if not analysis.get('success'):
            return {'success': False, 'message': analysis.get('message', '分析失败')}

    # 2. 读取持仓 + 最新收盘价 + 历史评级
    position = _read_position(stock_id)
    latest_close_info = _read_latest_close(stock_id)
    prev_rating_info = _read_last_rating(stock_id)

    prev_rating = prev_rating_info['rating'] if prev_rating_info else None
    prev_score = prev_rating_info['total_score'] if prev_rating_info else None

    # 3. 计算仓位状态
    has_position = position is not None
    is_profitable = False
    if has_position and latest_close_info and position['cost_price'] > 0:
        is_profitable = latest_close_info['close'] >= position['cost_price']

    # 4. 生成操作建议
    action = _determine_action(analysis['rating'], has_position, is_profitable)

    # 5. 生成建议详情
    detail = _build_detail_text(analysis)

    # 6. 仓位感知建议
    pos_advice = _build_position_advice(position, latest_close_info, analysis['rating'])

    # 7. 风险提示
    risks = _detect_risks(analysis['dimensions'])

    # 8. 评级变更检测
    # RATING-ALIGN-004：normalize 历史评级后比较（避免新旧档位字符串不同导致误判变更）
    prev_rating_norm = (
        scoring_engine.normalize_rating(prev_rating, prev_score) if prev_rating else None
    )
    is_changed = prev_rating_norm is not None and prev_rating_norm != analysis['rating']

    # 9. 最强/最弱维度
    active_scores = []
    for dk in ['kline', 'fundamental', 'capital_flow']:
        d = analysis['dimensions'].get(dk, {})
        if d.get('status') == 'ok':
            active_scores.append((dk, d.get('score', 0)))
    active_scores.sort(key=lambda x: x[1], reverse=True)

    strongest = (
        {'name': DIM_NAMES.get(active_scores[0][0], ''), 'score': active_scores[0][1]}
        if active_scores
        else None
    )
    weakest = (
        {'name': DIM_NAMES.get(active_scores[-1][0], ''), 'score': active_scores[-1][1]}
        if active_scores
        else None
    )

    # 10. 写入数据库
    _save_rating(stock_id, analysis, action, is_changed, latest_close_info)

    # 019A: 统一回写 daily_reports，确保三表/三处展示一致
    # 每日报告/批量分析/手动刷新/一键分析任一入口触发后，daily_reports 同步更新
    _save_daily_report_for_advice(stock_id, analysis, prev_score, engine_used, report_date)

    if is_changed or (prev_score is not None and abs(analysis['total_score'] - prev_score) >= 5):
        # RATING-ALIGN-004：change_log 统一用归一化后的评级，避免新旧档位字符串不同导致误记
        _save_change_log(
            stock_id,
            prev_rating_norm or '—',
            analysis['rating'],
            prev_score,
            analysis['total_score'],
        )

    # M8-BACKTEST-003：评级变更时自动触发回测（不阻塞主流程）
    if is_changed:
        try:
            from modules.backtest_engine import BacktestEngine

            bt_date = analysis.get('score_date', datetime.now(_CN_TZ).strftime('%Y-%m-%d'))
            BacktestEngine().auto_trigger_backtest(stock_id, bt_date)
        except Exception as e:
            logger.warning(f'M8 auto_trigger_backtest skipped: {e}')

    # 11. 组装返回
    rating_date = analysis.get('score_date', datetime.now(_CN_TZ).strftime('%Y-%m-%d'))

    result = {
        'success': True,
        'stock_code': analysis['stock_code'],
        'stock_name': analysis.get('stock_name', ''),
        'market': analysis.get('market', ''),
        'rating_date': rating_date,
        'rating': analysis['rating'],
        'rating_label': analysis.get('rating_label', ''),
        'total_score': analysis['total_score'],
        'action_advice': action,
        'advice_detail': detail,
        'position_advice': pos_advice,
        'risk_warnings': risks,
        'strongest_dim': strongest,
        'weakest_dim': weakest,
        'rating_changed': is_changed,
        'previous_rating': prev_rating_norm,  # RATING-ALIGN-004：返回归一化评级供前端一致展示
        'previous_score': prev_score,
        'has_position': has_position,
        'latest_close': latest_close_info['close'] if latest_close_info else None,
        'latest_close_date': latest_close_info['date'] if latest_close_info else None,
        'dimensions': analysis['dimensions'],
        'data_cutoff': analysis.get('data_cutoff', {}),
        'news_summary': analysis.get('news_summary', ''),
        'engine_version': engine_used,
        'data_warnings': analysis.get('data_warnings', []),
        'data_quality': analysis.get('data_quality'),
    }

    logger.info(
        f'[{analysis["stock_code"]}] 建议生成完成: engine={engine_used}, action={action}, risks={len(risks)}条'
    )

    return result


# ============================================================
# 命令行入口
# ============================================================

if __name__ == '__main__':
    import argparse

    parser = argparse.ArgumentParser(description='评级与建议生成 - 命令行模式')
    parser.add_argument('stock_id', type=int, help='股票ID')
    args = parser.parse_args()

    result = generate_advice(args.stock_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
