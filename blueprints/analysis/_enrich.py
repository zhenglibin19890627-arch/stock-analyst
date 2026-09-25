"""响应增强域（t7 拆包）：advise/analyze/report-latest 三路径共用的后处理装配器。

原 blueprints/analysis.py 区段逐字搬运：上一轮快照 / 风险解析 / 数据完整度补齐 /
失配注记（021BS）/ 回测证据三件套（021BU）/ 共振快照（021BZ）/ 打分子项注入（021V）。
仅做后处理增强，不修改 generate_advice 本体（B24 红线）；无路由。
"""

import logging

from database.db_manager import get_connection


def _prev_report_snapshot(stock_id, before_date):
    """上一轮有效日报快照（评分对比展示用，2026-09-09）。

    取同股 report_type='daily' + status='ok' + report_date < before_date 的
    最近一份，提取 总分/评级/四维分（key_factors[dim].score）。

    Returns: dict {report_date, total_score, rating, dims} 或 None（无历史报告）。
    只读、失败静默降级为 None，不影响报告主流程。
    """
    import json as _json

    try:
        conn = get_connection()
        try:
            cur = conn.cursor()
            cur.execute(
                """SELECT report_date, total_score, rating, key_factors, price_advice
                   FROM daily_reports
                   WHERE stock_id = ? AND report_type = 'daily' AND status = 'ok'
                   AND report_date < ?
                   ORDER BY report_date DESC LIMIT 1""",
                (stock_id, before_date),
            )
            row = cur.fetchone()
            if not row:
                return None
            dims = {}
            price_lines = {}
            try:
                kf = _json.loads(row['key_factors']) if row['key_factors'] else {}
                for dk in ('kline', 'fundamental', 'capital_flow', 'news'):
                    info = kf.get(dk)
                    if info and info.get('score') is not None:
                        dims[dk] = info['score']
            except (ValueError, TypeError):
                pass
            # 2026-09-18 A 方向：上一轮止盈/止损（价格建议卡对比展示）
            try:
                _pa = _json.loads(row['price_advice']) if row['price_advice'] else {}
                if isinstance(_pa, dict) and _pa.get('has_position'):
                    if _pa.get('take_profit') is not None:
                        price_lines['take_profit'] = _pa['take_profit']
                    if _pa.get('stop_loss') is not None:
                        price_lines['stop_loss'] = _pa['stop_loss']
            except (ValueError, TypeError):
                pass
            return {
                'report_date': row['report_date'],
                'total_score': row['total_score'],
                'rating': row['rating'],
                'dims': dims,
                'price_lines': price_lines,
            }
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(
            f'[prev_report] 上一轮评分快照查询失败 stock_id={stock_id}: {e}'
        )
        return None


def _parse_markdown_risks(md):
    """020R-43：从 markdown_content 解析「**风险提示**」列表（快照路径 risk_warnings 字段来源）。"""
    risks = []
    in_risk = False
    for line in (md or '').split('\n'):
        if '**风险提示**' in line:
            in_risk = True
            continue
        if not in_risk:
            continue
        s = line.strip()
        if s.startswith('- '):
            risks.append(s[2:].strip())
        elif s == '':
            continue
        else:
            break
    return risks


def _enrich_data_warnings(result, stock_id):
    """020R-41：advise/analyze 响应补齐「数据完整度」行（与每日报告路径同口径）。

    刷新报告原先只有引擎降级提示（如资金面提示），数据滞后等完整度行丢失；
    此处按 daily_report._build_data_freshness 追加，保证两条路径一致。
    """
    try:
        from modules.daily_report import _build_data_freshness

        freshness = _build_data_freshness(stock_id)
        result['data_warnings'] = list(result.get('data_warnings') or []) + [
            f'数据完整度：{line}' for line in (freshness.get('lines') or [])
        ]
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'数据完整度行补充失败 stock_id={stock_id}: {e}')


def _attach_score_tier_note(result):
    """021BS P1-1：报告响应统一附加「分数×档位失配」持续注记（外层调和，B24 合规）。

    评级本身不改（仍由 advisor.generate_advice 权威产出）；仅当总分所处档位
    区间与评级不一致（021AG 迟滞保持态/存量报告旧口径）时附说明，前端评分卡
    据此展示口径横幅。判定面与报告落库注记同源（rating_hysteresis 纯函数）。
    失败静默——注记缺失不阻塞报告主流程。
    """
    try:
        from modules.rating_hysteresis import score_tier_mismatch_note

        result['score_tier_note'] = score_tier_mismatch_note(
            result.get('total_score'),
            result.get('rating'),
            result.get('market') or 'a_stock',
        ) or None
    except Exception as e:  # noqa: BLE001 —— 注记属增强展示，失败不阻塞
        logging.getLogger(__name__).warning(f'失配注记计算失败 stock_id={result.get("stock_id")}: {e}')


def _attach_backtest_evidence(result, stock_id):
    """021BU：报告响应附加回测证据三件套（读取面现算、零落库、零写库；B24 合规外层落点）。

    - rating_evidence：评级旁「历史命中徽章」（backtest_engine.rating_evidence_for，
      与看板 watchlist-scores 同源同值；C 级样本不足由数据层断流百分数——诚实原则）；
    - position_note：评级位置分化警示（position_note_for，2026-09-18 既有机制，
      判定规则零改动，只扩消费面）；
    - price_advice_evidence：价格建议历史基准（price_advice_evidence_summary，
      真实锚点主口径市场级聚合）。
    证据属增强展示：任一失败静默降级（对应键置 None），不阻塞报告主流程。
    """
    market = result.get('market') or 'a_stock'
    rating = result.get('rating')
    try:
        from modules.backtest_engine import (
            position_note_for,
            price_advice_evidence_summary,
            rating_evidence_for,
        )

        result['rating_evidence'] = rating_evidence_for(market, rating) if rating else None
        result['position_note'] = (
            position_note_for(stock_id, rating) if rating else None
        )
        result['price_advice_evidence'] = price_advice_evidence_summary(market)
    except Exception as e:  # noqa: BLE001 —— 证据缺失不影响报告主数据
        logging.getLogger(__name__).warning(f'[021BU] 回测证据计算失败 stock_id={stock_id}: {e}')
        result.setdefault('rating_evidence', None)
        result.setdefault('position_note', None)
        result.setdefault('price_advice_evidence', None)


def _attach_resonance_snapshot(result, stock_id):
    """021BZ：报告响应附加「当前有效共振」快照（读取面离线复算、零网络零写库；B24 合规外层落点）。

    复用 market_screener 自选股离线复算链（_read_watchlist_klines +
    compute_watchlist_signal_result / compute_watchlist_sell_result，毫秒级）：
    买/卖两侧各至多一条（检测器取最高档），逐条经 resonance_view 附统一徽标
    数据（类型/方向/强弱/触发日/时效）。口径随行：scope=watchlist_offline +
    window=3 + kline_upto/kline_count + 强弱分级诚实声明（星级重标非回测验证）。
    与在线扫描端点同一映射（单一事实源在 market_screener 展示层）。
    快照属增强展示：失败静默降级（键置 None），不阻塞报告主流程。
    """
    try:
        from modules.market_screener import (
            _read_watchlist_klines,
            compute_watchlist_sell_result,
            compute_watchlist_signal_result,
            resonance_grade_note,
            resonance_view,
        )

        window = 3
        conn = get_connection()
        try:
            daily, weekly = _read_watchlist_klines(conn.cursor(), stock_id)
        finally:
            conn.close()
        upto = str(daily[-1]['date']) if daily else None
        buy_item = compute_watchlist_signal_result(daily, weekly, window=window)
        sell_item = compute_watchlist_sell_result(daily, weekly, window=window)
        result['resonance_snapshot'] = {
            'scope': 'watchlist_offline',
            'window': window,
            'kline_upto': upto,
            'kline_count': buy_item.get('kline_count'),
            'buy': [resonance_view(r, upto) for r in (buy_item.get('resonances') or [])],
            'sell': [resonance_view(r, upto) for r in (sell_item.get('sell_resonances') or [])],
            'note': resonance_grade_note(kline_upto=upto, window=window),
        }
    except Exception as e:  # noqa: BLE001 —— 快照缺失不影响报告主数据
        logging.getLogger(__name__).warning(f'[021BZ] 共振快照计算失败 stock_id={stock_id}: {e}')
        result.setdefault('resonance_snapshot', None)


def _attach_scoring_subitems(stock_id, result):
    """021V：基本面/资金面/消息面打分子项注入（021S 技术面同口径）。

    只读复用评分引擎（score_dimension + 各维 SUBITEMS，引擎零改动），在四维明细
    dict 上附带 scoring_score / scoring_subitems（name/score/normalized_weight/
    completeness/degradation/detail）；明细为 None 的维度跳过。
    失败静默降级——前端退回纯指标读数形态，不影响报告主流程。
    """
    try:
        from modules.data_adapter import load_stockdata_from_db
        from modules.scoring_engine import (
            CAPITAL_SUBITEMS,
            FUNDAMENTAL_SUBITEMS,
            NEWS_SUBITEMS,
            score_dimension,
        )

        sd = load_stockdata_from_db(stock_id)
        if sd is None:
            return
        for detail_key, subitems, dim_name in (
            ('fundamental_detail', FUNDAMENTAL_SUBITEMS, 'fundamental'),
            ('capital_detail', CAPITAL_SUBITEMS, 'capital'),
            ('news_detail', NEWS_SUBITEMS, 'news'),
        ):
            detail = result.get(detail_key)
            if not isinstance(detail, dict):
                continue
            sub_score, sub_detail = score_dimension(sd, subitems, dim_name)
            if sub_detail.get('subitems'):
                detail['scoring_score'] = sub_score
                detail['scoring_subitems'] = sub_detail['subitems']
    except Exception as e:  # noqa: BLE001 —— 子项得分属增强展示，失败不阻塞
        logging.getLogger(__name__).warning(f'打分子项计算失败 stock_id={stock_id}: {e}')
