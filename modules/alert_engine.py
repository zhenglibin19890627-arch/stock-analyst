"""
P3-B 智能预警模块 (Alert Engine)

基于监理批准的 3 类预警规则（G1-G3）：
  1. rating_change  评级跨档变化（升级/降级）
  2. score_below    评分跌破阈值（默认65）
  3. capital_outflow 主力资金连续净流出（默认3天）

设计要点（架构师评审 review_alert_P3B_20260727.md）：
  - scan_once() 每日日报后调用 1 次（G3）
  - 双层异常隔离：外层整体 try/except，内层单只股票失败不阻塞其他
  - 幂等：alert_history 表 (rule_id, stock_id, trigger_date) 唯一约束 + INSERT OR IGNORE
  - 规则优先级：个股规则(stock_id匹配) > 全局规则(stock_id IS NULL)
  - 评级跨档必须复用 scoring_engine.normalize_rating（D4，不重新实现）
  - 连续净流出取最近 N 个"有数据"的交易日（D3，缺失跳过不中断，窗口含今天）
  - 只读消费 ratings_history / analysis_results / raw_capital_flow，不回写引擎源表（V8）
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

# 合法规则类型白名单（API 校验用）
VALID_RULE_TYPES = ('rating_change', 'score_below', 'capital_outflow')


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
    cursor.execute(
        """SELECT trade_date, main_net_inflow
           FROM raw_capital_flow
           WHERE stock_id=?
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
# 消息格式化
# ================================================================


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
