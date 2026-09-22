"""
P3-B 智能预警模块 (Alert Engine)

基于监理批准的 3 类预警规则（G1-G3）+ 021BP/021BQ 决策闭环新增信号类规则：
  1. rating_change  评级跨档变化（升级/降级）
  2. score_below    评分跌破阈值（默认65）
  3. capital_outflow 主力资金连续净流出（默认3天）
  4. tech_signal    自选股买点技术信号（021BP 项2：复用 market_screener
                    信号纯函数对已采集K线离线复算，零网络；阈值语义=共振
                    星级门槛，选填）
  5. sell_signal    自选股卖点技术信号（021BQ 项C：check_tech_signal 的
                    同构镜像，走卖侧平行库 SELL_SIGNAL_LIBRARY/ bear 共振，
                    在线全市场扫描不受影响；阈值语义=共振星级门槛，选填）

设计要点（架构师评审 review_alert_P3B_20260727.md + 021BP/021BQ 方案）：
  - scan_once() 每日日报后调用 1 次（G3；15:54 窗3 收盘批次，收盘后触发）
  - 双层异常隔离：外层整体 try/except，内层单只股票失败不阻塞其他
  - 幂等：alert_history 表 (rule_id, stock_id, trigger_date) 唯一约束 + INSERT OR IGNORE
  - 规则优先级：个股规则(stock_id匹配) > 全局规则(stock_id IS NULL)
  - 评级跨档必须复用 scoring_engine.normalize_rating（D4，不重新实现）
  - 连续净流出取最近 N 个"有数据"的交易日（D3，缺失跳过不中断，窗口含今天）
  - 只读消费 ratings_history / analysis_results / raw_capital_flow / raw_kline* /
    daily_reports（021BQ 评级上下文注记），不回写引擎源表（V8；tech/sell_signal
    同样只读K线，不产候选入库）
  - 信号×评级相悖调和（021BQ 项④）：卖点信号撞买入档评级 / 买点信号撞减仓
    卖出档评级时，消息附注"仅波段参考，以评级为主"——评级是唯一动作主指令
"""

import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import get_connection

# D4 红线：复用 scoring_engine.normalize_rating，不得重新实现评级映射
from modules.scoring_engine import normalize_rating

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

# 档位顺序映射（与 config_weights.json rating_mapping 80/65/50/30 对齐）
RATING_ORDER = {
    '强烈推荐买入': 5,
    '推荐买入': 4,
    '持有观望': 3,
    '建议减仓': 2,
    '强烈建议卖出': 1,
}

# 合法规则类型白名单（API 校验用；与 blueprints/alerts.py _VALID_ALERT_TYPES 同步）
VALID_RULE_TYPES = ('rating_change', 'score_below', 'capital_outflow', 'tech_signal',
                    'sell_signal')


# ================================================================
# 规则查询：个股优先 > 全局回退（D5 热加载，每次实时读取）
# ================================================================


def _get_active_rule(cursor, rule_type, stock_id):
    """获取生效中的规则：先查个股规则，无则回退全局规则。

    Returns:
        dict(row) 或 None
    """
    # 先查个股规则
    cursor.execute(
        'SELECT * FROM alert_rules WHERE rule_type=? AND stock_id=? AND enabled=1',
        (rule_type, stock_id),
    )
    row = cursor.fetchone()
    if row:
        return dict(row)
    # 回退全局规则
    cursor.execute(
        'SELECT * FROM alert_rules WHERE rule_type=? AND stock_id IS NULL AND enabled=1',
        (rule_type,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def _get_stock_info(cursor, stock_id):
    """获取股票 symbol/name/market"""
    cursor.execute('SELECT symbol, name, market FROM stocks WHERE id=?', (stock_id,))
    row = cursor.fetchone()
    if row:
        return dict(row)
    return {'symbol': '', 'name': f'stock#{stock_id}', 'market': 'a_stock'}


# ================================================================
# 规则1：评级跨档变化（D4，复用 normalize_rating）
# ================================================================


def check_rating_change(cursor, stock_id):
    """检查评级跨档变化。

    对比 ratings_history 最近两次评级，档位不同则触发。
    首次评级（不足2条）不触发。

    Returns:
        dict 或 None
    """
    cursor.execute(
        """SELECT rating, total_score, rating_date
           FROM ratings_history
           WHERE stock_id=?
           ORDER BY rating_date DESC
           LIMIT 2""",
        (stock_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]

    if len(rows) < 2:
        return None  # 首次评级或历史不足

    latest = rows[0]
    previous = rows[1]

    # 复用 scoring_engine 归一化（红线：不得重新实现）
    old_norm = normalize_rating(previous['rating'], previous['total_score'])
    new_norm = normalize_rating(latest['rating'], latest['total_score'])

    if old_norm == new_norm:
        return None  # 同档不触发

    old_order = RATING_ORDER.get(old_norm, 0)
    new_order = RATING_ORDER.get(new_norm, 0)
    level_change = new_order - old_order

    if level_change == 0:
        return None  # 映射后同档

    return {
        'old_rating': old_norm,
        'new_rating': new_norm,
        'old_score': previous['total_score'],
        'new_score': latest['total_score'],
        'direction': 'upgrade' if level_change > 0 else 'downgrade',
        'level_change': level_change,
        'latest_date': latest['rating_date'],
    }


# ================================================================
# 规则2：评分跌破阈值（默认65）
# ================================================================


def check_score_below(cursor, stock_id, threshold=65):
    """检查最新综合评分是否跌破阈值。

    数据源：analysis_results 最新一条。

    Returns:
        dict 或 None
    """
    cursor.execute(
        """SELECT total_score, analysis_date, rating
           FROM analysis_results
           WHERE stock_id=?
           ORDER BY analysis_date DESC
           LIMIT 1""",
        (stock_id,),
    )
    row = cursor.fetchone()
    if not row:
        return None  # 无记录

    score = row['total_score']
    if score is None:
        return None

    if score < threshold:
        return {
            'score': score,
            'threshold': threshold,
            'analysis_date': row['analysis_date'],
        }
    return None


# ================================================================
# 规则3：主力连续净流出（默认3天，D3 跳过缺失语义）
# ================================================================


def check_capital_outflow(cursor, stock_id, n_days=3):
    """检查主力资金连续净流出。

    架构师 D3 评审：
      - 取最近 N 个"有数据"的交易日（非自然日），缺失跳过不中断
      - 窗口含今天
      - 港股无两融数据，直接跳过

    Returns:
        dict 或 None
    """
    # 港股无资金面数据，直接跳过
    info = _get_stock_info(cursor, stock_id)
    if info.get('market') == 'hk_stock':
        return None

    # 查询最近 N*2 个交易日（考虑缺失，多取）
    # 019H：过滤估算行（is_estimated=1），确保预警判定仅使用真实资金流数据
    cursor.execute(
        """SELECT trade_date, main_net_inflow
           FROM raw_capital_flow
           WHERE stock_id=?
           AND (is_estimated = 0 OR is_estimated IS NULL)
           ORDER BY trade_date DESC
           LIMIT ?""",
        (stock_id, n_days * 2),
    )
    rows = [dict(r) for r in cursor.fetchall()]

    if not rows:
        return None

    # 过滤有效数据，取最近 N 个有数据的交易日
    valid = [r for r in rows if r['main_net_inflow'] is not None]
    if len(valid) < n_days:
        return None  # 数据不足

    recent_n = valid[:n_days]

    # 判定：最近 N 个有数据的交易日是否全部净流出
    if not all(r['main_net_inflow'] < 0 for r in recent_n):
        return None

    total_outflow = sum(abs(r['main_net_inflow']) for r in recent_n)
    dates = [r['trade_date'] for r in recent_n]

    return {
        'consecutive_days': n_days,
        'total_outflow': round(total_outflow, 2),
        'latest_date': recent_n[0]['trade_date'],
        'dates': dates,
    }


# ================================================================
# 规则4：自选股买点技术信号（021BP 决策闭环 项2）
#   复用 market_screener 信号纯函数对已采集K线离线复算（零网络，R5 不涉）；
#   只读 raw_kline/raw_kline_weekly（V8 只读消费，不产候选入库）；
#   不涉评级映射（R7 天然规避）。
# ================================================================


def check_tech_signal(cursor, stock_id, min_stars=None, window=3):
    """检查自选股最新交易日是否出现买点技术信号（离线复算，零网络）。

    判定口径（"今日出现"才提醒——每日巡检幂等，不重复轰炸）：
      - 信号：仅认触发日 == 最新已采集K线日（kline_upto）的金叉命中；
        窗口内的历史命中不计（前一日巡检已覆盖，同日重复由 UNIQUE 约束去重）
      - 共振：由检测窗口内全部命中推导（星级/双指标系同窗判定需要窗口上下文），
        随当日信号一并列出
      - min_stars（规则阈值，选填）：共振星级门槛——设 3/4/5 时，仅当出现
        不低于该星级的共振组合才提醒；留空=任意买点信号都提醒

    数据不足（<35 根日K）静默跳过，不报错不提醒。

    Returns:
        dict: {'signals': [{signal,label,trigger_date}], 'resonances': [{key,label,stars}],
               'kline_upto', 'kline_count'}
        None: 无信号 / 数据不足 / 未达星级门槛
    """
    # 延迟导入：信号库在同仓 market_screener 模块（含网络函数，避免无关导入开销）
    from modules.market_screener import (
        _read_watchlist_klines,
        compute_watchlist_signal_result,
    )

    daily_rows, weekly_rows = _read_watchlist_klines(cursor, stock_id)
    item = compute_watchlist_signal_result(daily_rows, weekly_rows, window=window)
    kline_upto = item['kline_upto']
    if not kline_upto:
        return None  # 无已采集K线

    hits_today = [h for h in item['matches'] if h['trigger_date'] == kline_upto]
    resonances = item['resonances']
    if min_stars is not None:
        resonances = [r for r in resonances if r['stars'] >= int(min_stars)]

    if not hits_today:
        return None
    if min_stars is not None and not resonances:
        return None

    current_rating = _get_current_rating(cursor, stock_id)
    return {
        'signals': [
            {'signal': h['signal'], 'label': h['label'], 'trigger_date': h['trigger_date']}
            for h in hits_today
        ],
        'resonances': [
            {'key': r['key'], 'label': r['label'], 'stars': r['stars']} for r in resonances
        ],
        'kline_upto': kline_upto,
        'kline_count': item['kline_count'],
        'current_rating': current_rating,
        'rating_conflict': _rating_conflict('tech_signal', current_rating),
    }


# ================================================================
# 规则5：自选股卖点技术信号（021BQ 决策闭环 项C）
#   与 check_tech_signal 同构镜像；复用 market_screener 卖侧平行库
#   （SELL_SIGNAL_LIBRARY + detect_sell_* + bear 共振，不触碰买侧信号库，
#   在线全市场扫描仍只产买点）；只读 raw_kline/raw_kline_weekly + daily_reports
#   （V8 只读消费，不产候选入库）；不涉评级映射（R7 天然规避）。
# ================================================================

# 信号×评级相悖调和（021BP 行动清单 _CONFLICT_RATINGS 机制镜像，021BQ 项④）：
# 卖点信号撞买入档评级 / 买点信号撞减仓卖出档评级时，消息附注
# "仅波段参考，以评级为主"——评级是唯一动作主指令，信号仅短线波段参考。
_SELL_CONFLICT_RATINGS = ('推荐买入', '强烈推荐买入')
_BUY_CONFLICT_RATINGS = ('建议减仓', '强烈建议卖出')


def _get_current_rating(cursor, stock_id):
    """最新有效日报的评级（消息上下文注记用；无报告返回 None）。

    只读 daily_reports（V8 合规）；口径与 trader_advisor._gather_inputs 读取
    "最新评级"一致（status='ok' 且 report_type='daily'，report_date 降序首行）。
    """
    cursor.execute(
        "SELECT rating FROM daily_reports "
        "WHERE stock_id = ? AND status = 'ok' AND report_type = 'daily' "
        'ORDER BY report_date DESC LIMIT 1',
        (stock_id,),
    )
    row = cursor.fetchone()
    return row['rating'] if row else None


def _rating_conflict(alert_type, rating):
    """判定信号方向与当前评级是否相悖（仅 tech_signal / sell_signal 参与判定）。"""
    if not rating:
        return False
    if alert_type == 'sell_signal':
        return rating in _SELL_CONFLICT_RATINGS
    if alert_type == 'tech_signal':
        return rating in _BUY_CONFLICT_RATINGS
    return False


def check_sell_signal(cursor, stock_id, min_stars=None, window=3):
    """检查自选股最新交易日是否出现卖点技术信号（离线复算，零网络）。

    判定口径与 check_tech_signal 同构镜像（"今日出现"才提醒——每日巡检幂等，
    不重复轰炸）：
      - 信号：仅认触发日 == 最新已采集K线日（kline_upto）的死叉/破位命中；
        窗口内的历史命中不计（前一日巡检已覆盖，同日重复由 UNIQUE 约束去重）
      - 共振：由检测窗口内全部命中推导（bear 库：双死叉/周线空头/顶背离），
        随当日信号一并列出
      - min_stars（规则阈值，选填）：共振星级门槛——设 3/4/5 时，仅当出现
        不低于该星级的 bear 共振组合才提醒；留空=任意卖点信号都提醒

    数据不足（<35 根日K）静默跳过，不报错不提醒。

    Returns:
        dict: {'signals': [{signal,label,trigger_date}], 'resonances': [{key,label,stars}],
               'kline_upto', 'kline_count', 'current_rating', 'rating_conflict'}
        None: 无信号 / 数据不足 / 未达星级门槛
    """
    # 延迟导入：卖侧平行库在同仓 market_screener 模块（含网络函数，避免无关导入开销）
    from modules.market_screener import (
        _read_watchlist_klines,
        compute_watchlist_sell_result,
    )

    daily_rows, weekly_rows = _read_watchlist_klines(cursor, stock_id)
    item = compute_watchlist_sell_result(daily_rows, weekly_rows, window=window)
    kline_upto = item['kline_upto']
    if not kline_upto:
        return None  # 无已采集K线

    hits_today = [h for h in item['sell_matches'] if h['trigger_date'] == kline_upto]
    resonances = item['sell_resonances']
    if min_stars is not None:
        resonances = [r for r in resonances if r['stars'] >= int(min_stars)]

    if not hits_today:
        return None
    if min_stars is not None and not resonances:
        return None

    current_rating = _get_current_rating(cursor, stock_id)
    return {
        'signals': [
            {'signal': h['signal'], 'label': h['label'], 'trigger_date': h['trigger_date']}
            for h in hits_today
        ],
        'resonances': [
            {'key': r['key'], 'label': r['label'], 'stars': r['stars']} for r in resonances
        ],
        'kline_upto': kline_upto,
        'kline_count': item['kline_count'],
        'current_rating': current_rating,
        'rating_conflict': _rating_conflict('sell_signal', current_rating),
    }


# ================================================================
# 消息格式化
# ================================================================


def _rating_context_suffix(detail):
    """信号×评级相悖时的调和注记（021BQ 项④：评级是主指令，信号仅波段参考）。

    021BN 教训：本函数输出会进入前端渲染文案，禁止裸 '<' 字符。"""
    if detail.get('rating_conflict') and detail.get('current_rating'):
        return (f'；注意：当前评级「{detail["current_rating"]}」与该信号方向相悖，'
                '仅波段参考，以评级为主')
    return ''


def _format_message(alert_type, stock_info, detail):
    """构建人类可读的预警消息"""
    name = stock_info.get('name', '')
    symbol = stock_info.get('symbol', '')

    if alert_type == 'rating_change':
        d = detail['direction']
        arrow = '⬆ 升级' if d == 'upgrade' else '⬇ 降级'
        return (
            f'{name}({symbol}) 评级{arrow}：'
            f'{detail["old_rating"]} → {detail["new_rating"]}，'
            f'评分 {detail["old_score"]:.1f} → {detail["new_score"]:.1f}'
        )
    if alert_type == 'score_below':
        return (
            f'{name}({symbol}) 评分跌破阈值：'
            f'当前 {detail["score"]:.1f} 分 < 阈值 {detail["threshold"]} 分'
        )
    if alert_type == 'capital_outflow':
        return (
            f'{name}({symbol}) 主力资金连续{detail["consecutive_days"]}日净流出，'
            f'累计流出 {detail["total_outflow"]:.2f} 万元'
            f'（基于最近{detail["consecutive_days"]}个有数据交易日）'
        )
    if alert_type == 'tech_signal':
        # 021BN 教训：会被前端渲染的文案禁止裸 '<' 字符（本分支全部用文字描述）
        labels = '、'.join(s['label'] for s in detail['signals'])
        msg = f'{name}({symbol}) 今日出现买点信号：{labels}'
        if detail.get('resonances'):
            res_str = '、'.join(f"{r['label']}（{r['stars']}星）" for r in detail['resonances'])
            msg += f'；共振组合：{res_str}'
        msg += f'（基于截至{detail["kline_upto"]}的已采集K线离线复算）'
        msg += _rating_context_suffix(detail)
        return msg
    if alert_type == 'sell_signal':
        # 021BQ 项C：卖点信号文案与买点同构镜像（禁裸 '<'，021BN 教训）
        labels = '、'.join(s['label'] for s in detail['signals'])
        msg = f'{name}({symbol}) 今日出现卖点信号：{labels}'
        if detail.get('resonances'):
            res_str = '、'.join(f"{r['label']}（{r['stars']}星）" for r in detail['resonances'])
            msg += f'；共振组合：{res_str}'
        msg += f'（基于截至{detail["kline_upto"]}的已采集K线离线复算）'
        msg += _rating_context_suffix(detail)
        return msg
    return f'{name}({symbol}) 触发 {alert_type} 预警'


# ================================================================
# 扫描入口（F1.1）
# ================================================================

# 规则类型 → 检查函数 + 阈值提取
_RULE_CHECKERS = {
    'rating_change': lambda cur, rule, sid: check_rating_change(cur, sid),
    'score_below': lambda cur, rule, sid: check_score_below(
        cur, sid, threshold=(rule['threshold'] if rule['threshold'] is not None else 65)
    ),
    'capital_outflow': lambda cur, rule, sid: check_capital_outflow(
        cur, sid, n_days=(int(rule['threshold']) if rule['threshold'] is not None else 3)
    ),
    # 阈值语义=共振星级门槛（选填；None=任意买点信号都提醒）
    'tech_signal': lambda cur, rule, sid: check_tech_signal(
        cur, sid,
        min_stars=(int(rule['threshold']) if rule['threshold'] is not None else None),
    ),
    # 021BQ 项C：卖点信号检查器（与 tech_signal 同构镜像）
    # 阈值语义=bear 共振星级门槛（选填；None=任意卖点信号都提醒）
    'sell_signal': lambda cur, rule, sid: check_sell_signal(
        cur, sid,
        min_stars=(int(rule['threshold']) if rule['threshold'] is not None else None),
    ),
}


def scan_once():
    """预警扫描入口（每日日报后调用1次）

    流程：
      1. 扫描所有 enabled=1 的规则类型
      2. 规则查询优先级：个股规则 > 全局规则
      3. 幂等：同规则同股票同日已触发则跳过（INSERT OR IGNORE）
      4. 单只股票失败不阻塞其他股票（双层异常隔离）

    Returns:
        dict: 扫描结果汇总
    """
    today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    triggered = 0
    skipped = 0
    errors = 0
    stock_count = 0

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # 获取所有启用中的规则类型（去重）
        cursor.execute('SELECT DISTINCT rule_type FROM alert_rules WHERE enabled=1')
        active_types = [r['rule_type'] for r in cursor.fetchall()]

        if not active_types:
            logger.info('[P3-B] 无启用中的预警规则，跳过扫描')
            return {'success': True, 'triggered': 0, 'message': '无启用规则'}

        # 获取所有活跃自选股
        cursor.execute('SELECT id FROM stocks WHERE status="active" ORDER BY id')
        stock_ids = [r['id'] for r in cursor.fetchall()]
        stock_count = len(stock_ids)

        if not stock_ids:
            logger.info('[P3-B] 无自选股，跳过扫描')
            return {'success': True, 'triggered': 0, 'message': '无自选股'}

        for stock_id in stock_ids:
            # 内层异常隔离：单只股票失败不阻塞
            try:
                stock_info = _get_stock_info(cursor, stock_id)

                for rule_type in active_types:
                    try:
                        rule = _get_active_rule(cursor, rule_type, stock_id)
                        if not rule:
                            continue

                        checker = _RULE_CHECKERS.get(rule_type)
                        if not checker:
                            continue

                        detail = checker(cursor, rule, stock_id)
                        if detail is None:
                            continue

                        # 构建预警记录并幂等写入
                        message = _format_message(rule_type, stock_info, detail)
                        trigger_value = json.dumps(detail, ensure_ascii=False, default=str)

                        cursor.execute(
                            """INSERT OR IGNORE INTO alert_history
                               (rule_id, stock_id, alert_type, trigger_value,
                                message, is_read, trigger_date)
                               VALUES (?, ?, ?, ?, ?, 0, ?)""",
                            (rule['id'], stock_id, rule_type, trigger_value, message, today),
                        )
                        if cursor.rowcount > 0:
                            triggered += 1
                            logger.info(f'[P3-B] 预警触发: {message}')
                        else:
                            skipped += 1  # 今日已触发（幂等跳过）

                    except Exception as e:
                        errors += 1
                        logger.error(
                            f'[P3-B] 规则 {rule_type} 检查 stock_id={stock_id} 异常: {e}',
                            exc_info=True,
                        )

            except Exception as e:
                errors += 1
                logger.error(f'[P3-B] 股票 stock_id={stock_id} 扫描异常: {e}', exc_info=True)

        conn.commit()
        logger.info(
            f'[P3-B] 预警扫描完成: 股票{stock_count}只, 触发{triggered}条, '
            f'幂等跳过{skipped}条, 错误{errors}个'
        )
        return {
            'success': True,
            'date': today,
            'stock_count': stock_count,
            'triggered': triggered,
            'skipped_idempotent': skipped,
            'errors': errors,
        }

    except Exception as e:
        logger.error(f'[P3-B] 预警扫描整体异常: {e}', exc_info=True)
        return {'success': False, 'error': str(e)}
    finally:
        conn.close()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
    result = scan_once()
    print(json.dumps(result, ensure_ascii=False, indent=2))
