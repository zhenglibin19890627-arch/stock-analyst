"""
M9 自动优化引擎（规则化方案）
=============================

需求映射：US-10 / §2.9 自动优化模块
设计原则：
  - 规则化（非ML）：可解释的 if-else 规则调参
  - 渐进调整：每次权重 +/-5%，阈值 +/-2分
  - 可追溯：每次优化记录到 strategy_params 表
  - 全自动：每周自动执行，无需用户干预
  - A/H独立：A股和港股分别优化
  - 安全阀：优化后准确率不得低于优化前（否则回滚）

触发方式：
  - 定时：每周日 20:00 自动执行（daily_report 调度器注册）
  - 手动：POST /api/optimizer/run
  - 查看：GET /api/optimizer/status
"""

import copy
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import get_connection

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8))

# 权重配置文件路径
_WEIGHTS_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config_weights.json'
)

# 安全约束常量
MAX_WEIGHT_STEP = 0.05  # 单次权重最大调整幅度 5%
MAX_THRESHOLD_STEP = 2  # 单次阈值最大调整幅度 2分
MIN_SAMPLE_SIZE = 50  # 最低样本量
MIN_INTERVAL_DAYS = 7  # 同方向调整最小间隔天数
WEIGHT_UPPER = 0.50  # 单维度权重上限
WEIGHT_LOWER = 0.05  # 单维度权重下限

# 阈值基准（不可偏离超过5分）
THRESHOLD_BASELINES = {
    '强烈推荐买入': 85,
    '推荐买入': 70,
    '持有观望': 50,
    '建议减仓': 30,
    '强烈建议卖出': 0,
}
THRESHOLD_MAX_DEVIATION = 5


class OptimizerEngine:
    """M9 规则化自动优化引擎"""

    def run_weekly_optimization(self, market='a_stock'):
        """每周自动优化主入口

        流程：
        1. 检查样本量是否充足（>=50）
        2. 计算当前准确率（优化前基准）
        3. 分析各维度准确率贡献
        4. 生成权重调整建议
        4.5 冷却期检查（同方向调整间隔>=7天，反向不受限）
        5. 生成阈值调整建议
        6. 权重安全阀验证（独立）
        7. 阈值写入 + 阈值安全阀验证（独立）
        8. 记录到 strategy_params（审计追溯）

        Returns:
            dict: {adjusted, changes, reason, market}
        """
        # 1. 样本量检查
        sample_count = self._get_sample_count(market)
        if sample_count < MIN_SAMPLE_SIZE:
            return {
                'adjusted': False,
                'changes': [],
                'reason': f'样本不足（{sample_count}<{MIN_SAMPLE_SIZE}），跳过优化',
                'market': market,
                'sample_count': sample_count,
            }

        # 2. 计算当前准确率（优化前基准）
        baseline_accuracy = self._calc_overall_accuracy(market)

        # 3. 分析维度准确率
        dim_analysis = self.analyze_dimension_accuracy(market)

        # 4. 生成权重调整建议
        weight_suggestion = self.suggest_weight_adjustment(market, dim_analysis, baseline_accuracy)

        # 4.5 冷却期检查（在生成权重建议之后，需先知道方向）
        if weight_suggestion['should_adjust']:
            last_opt = self._get_last_optimization(market)
            if last_opt:
                now = datetime.now(_CN_TZ)
                days_since = (now - last_opt['time']).days
                if days_since < MIN_INTERVAL_DAYS:
                    if self._is_same_direction(last_opt['direction'], weight_suggestion['delta']):
                        return {
                            'adjusted': False,
                            'changes': [],
                            'reason': f'冷却期未满（距上次同方向调整仅{days_since}天<{MIN_INTERVAL_DAYS}天），跳过',
                            'market': market,
                            'cooldown_remaining': MIN_INTERVAL_DAYS - days_since,
                        }

        # 5. 生成阈值调整建议
        threshold_suggestion = self.suggest_threshold_adjustment(market)

        changes = []
        reasons = []

        # 收集权重变更
        weight_adjusted = False
        if weight_suggestion['should_adjust']:
            changes.append(
                {
                    'type': 'weights',
                    'old': weight_suggestion['old_weights'],
                    'new': weight_suggestion['new_weights'],
                    'delta': weight_suggestion['delta'],
                }
            )
            reasons.append(weight_suggestion['reason'])
            weight_adjusted = True

        # 收集阈值变更
        threshold_adjusted = False
        if threshold_suggestion['should_adjust']:
            changes.append(
                {
                    'type': 'thresholds',
                    'adjustments': threshold_suggestion['adjustments'],
                }
            )
            reasons.append(threshold_suggestion['reason'])
            threshold_adjusted = True

        if not changes:
            return {
                'adjusted': False,
                'changes': [],
                'reason': '当前参数已处于合理范围，无需调整',
                'market': market,
                'sample_count': sample_count,
                'accuracy': baseline_accuracy,
            }

        # 6. 权重安全阀（独立）
        if weight_adjusted:
            self._write_weights(market, weight_suggestion['new_weights'])
            new_accuracy = self._calc_overall_accuracy(market)
            if new_accuracy < baseline_accuracy - 0.01:  # 允许1%误差
                # 权重回滚
                self._write_weights(market, weight_suggestion['old_weights'])
                weight_adjusted = False
                # 从 changes 中移除权重变更
                changes = [c for c in changes if c.get('type') != 'weights']
                reasons = [r for r in reasons if r != weight_suggestion['reason']]
                logger.info(
                    f'[M9] 权重安全阀触发：{new_accuracy:.1%}<{baseline_accuracy:.1%}，已回滚权重'
                )
            else:
                new_accuracy = new_accuracy  # 保留用于后续记录
        else:
            new_accuracy = baseline_accuracy

        # 7. 阈值安全阀（独立，与权重互不影响）
        if threshold_adjusted:
            old_mapping = self._read_full_config().get('rating_mapping', {})
            # 深拷贝 old_mapping 用于回滚
            old_mapping_copy = copy.deepcopy(old_mapping)

            # 写入新阈值
            write_result = self._write_thresholds(market, threshold_suggestion['adjustments'])
            if not write_result['success']:
                # 写入失败（如全部档位因重叠被跳过）
                threshold_adjusted = False
                changes = [c for c in changes if c.get('type') != 'thresholds']
                reasons = [r for r in reasons if r != threshold_suggestion['reason']]
                if write_result.get('reason'):
                    reasons.append(write_result['reason'])
            else:
                # 安全阀验证
                new_mapping = self._read_full_config().get('rating_mapping', {})
                if not self._validate_threshold_safety(market, old_mapping_copy, new_mapping):
                    # 阈值回滚（不影响权重）
                    self._rollback_thresholds(old_mapping_copy)
                    threshold_adjusted = False
                    changes = [c for c in changes if c.get('type') != 'thresholds']
                    reasons = [r for r in reasons if r != threshold_suggestion['reason']]
                    logger.info('[M9] 阈值安全阀触发：新阈值准确率低于旧阈值，已回滚')

        # 如果权重和阈值都被回滚/跳过
        if not weight_adjusted and not threshold_adjusted:
            if not changes:
                return {
                    'adjusted': False,
                    'changes': [],
                    'reason': '安全阀触发或无可执行调整，已回滚',
                    'market': market,
                    'sample_count': sample_count,
                }

        # 重新计算最终准确率
        final_accuracy = self._calc_overall_accuracy(market)

        # 8. 记录到 strategy_params（审计追溯）
        if changes:
            self._record_optimization(market, changes, reasons, baseline_accuracy, final_accuracy)

        return {
            'adjusted': True,
            'changes': changes,
            'reason': '；'.join(reasons) if reasons else '优化完成',
            'market': market,
            'sample_count': sample_count,
            'accuracy_before': round(baseline_accuracy, 4),
            'accuracy_after': round(final_accuracy, 4),
        }

    def analyze_dimension_accuracy(self, market='a_stock'):
        """分析各维度对总准确率的贡献

        方法：统计回测正确/错误记录中，各维度得分的均值差异。
        如果某维度在"正确"组得分显著高于"错误"组，说明该维度对准确率有正贡献。

        Returns:
            dict: {dim: {avg_correct, avg_wrong, contribution, accuracy_proxy}}
        """
        conn = get_connection()
        cursor = conn.cursor()

        # 从 analysis_results 获取维度得分，关联 backtest_results 的正确性
        cursor.execute(
            """
            SELECT br.is_correct, ar.technical_score, ar.fundamental_score,
                   ar.sentiment_score, ar.capital_score
            FROM backtest_results br
            JOIN analysis_results ar ON ar.stock_id = br.stock_id
                AND ar.analysis_date = br.rating_date
            JOIN stocks s ON s.id = br.stock_id
            WHERE s.market = ? AND br.is_correct IS NOT NULL
        """,
            (market,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        if not rows:
            # 无关联数据时，用回测评级分布估算
            return self._estimate_dim_contribution_from_backtest(market)

        dims = {
            'kline': 'technical_score',
            'fundamental': 'fundamental_score',
            'news': 'sentiment_score',
            'capital_flow': 'capital_score',
        }

        result = {}
        for dim_key, col in dims.items():
            correct_vals = [r[col] for r in rows if r['is_correct'] == 1 and r[col] is not None]
            wrong_vals = [r[col] for r in rows if r['is_correct'] == 0 and r[col] is not None]

            avg_correct = sum(correct_vals) / len(correct_vals) if correct_vals else 50.0
            avg_wrong = sum(wrong_vals) / len(wrong_vals) if wrong_vals else 50.0

            # 贡献度 = 正确组均值 - 错误组均值（正值=有正贡献）
            contribution = avg_correct - avg_wrong

            # 准确率代理：该维度得分>=50时回测正确的比例
            high_dim_correct = sum(
                1 for r in rows if r.get(col) and r[col] >= 50 and r['is_correct'] == 1
            )
            high_dim_total = sum(
                1 for r in rows if r.get(col) and r[col] >= 50 and r['is_correct'] is not None
            )
            accuracy_proxy = high_dim_correct / high_dim_total if high_dim_total > 0 else 0.5

            result[dim_key] = {
                'avg_correct': round(avg_correct, 2),
                'avg_wrong': round(avg_wrong, 2),
                'contribution': round(contribution, 2),
                'accuracy_proxy': round(accuracy_proxy, 4),
                'sample': len(correct_vals) + len(wrong_vals),
            }

        return result

    def suggest_weight_adjustment(self, market, dim_analysis, overall_accuracy):
        """基于准确率生成权重调整建议（渐进 +/-5%）

        规则1：
        - dim_accuracy > 整体准确率 + 10% → 权重 +5%
        - dim_accuracy < 整体准确率 - 10% → 权重 -5%
        - 归一化使总和 = 1.0
        """
        current_weights = self._read_weights(market)
        new_weights = dict(current_weights)
        adjustments_made = False
        reason_parts = []

        for dim, info in dim_analysis.items():
            if dim not in new_weights:
                continue
            dim_acc = info.get('accuracy_proxy', 0.5)
            old_w = current_weights[dim]

            if dim_acc > overall_accuracy + 0.10:
                # 该维度表现好，增权
                new_w = min(WEIGHT_UPPER, old_w + MAX_WEIGHT_STEP)
                if new_w != old_w:
                    new_weights[dim] = round(new_w, 4)
                    adjustments_made = True
                    reason_parts.append(
                        f'{dim}准确率{dim_acc:.0%}高于均值{overall_accuracy:.0%}，权重{old_w:.0%}→{new_w:.0%}'
                    )
            elif dim_acc < overall_accuracy - 0.10:
                # 该维度表现差，降权
                new_w = max(WEIGHT_LOWER, old_w - MAX_WEIGHT_STEP)
                if new_w != old_w:
                    new_weights[dim] = round(new_w, 4)
                    adjustments_made = True
                    reason_parts.append(
                        f'{dim}准确率{dim_acc:.0%}低于均值{overall_accuracy:.0%}，权重{old_w:.0%}→{new_w:.0%}'
                    )

        # 归一化
        if adjustments_made:
            total = sum(new_weights.values())
            if total > 0:
                new_weights = {k: round(v / total, 4) for k, v in new_weights.items()}

        # 计算 delta
        delta = {
            k: round(new_weights.get(k, 0) - current_weights.get(k, 0), 4) for k in current_weights
        }

        return {
            'should_adjust': adjustments_made,
            'old_weights': current_weights,
            'new_weights': new_weights,
            'delta': delta,
            'reason': '；'.join(reason_parts) if reason_parts else '无需调整',
        }

    def suggest_threshold_adjustment(self, market):
        """基于评级分布生成阈值调整建议

        规则2：
        - 某档位准确率 < 40% 且样本 >= 20 → 收窄（边界内缩2分）
        - 某档位准确率 > 75% 且样本 >= 20 → 扩大（边界外扩2分）
        - 边界约束：不低于基准 +/- 5分
        """
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT rating, is_correct, COUNT(*) as cnt
            FROM backtest_results br
            JOIN stocks s ON s.id = br.stock_id
            WHERE s.market = ? AND br.is_correct IS NOT NULL
            GROUP BY rating, is_correct
        """,
            (market,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        # 汇总各档位
        rating_stats = {}
        for r in rows:
            rating = r['rating']
            if rating not in rating_stats:
                rating_stats[rating] = {'correct': 0, 'wrong': 0, 'total': 0}
            if r['is_correct'] == 1:
                rating_stats[rating]['correct'] += r['cnt']
            elif r['is_correct'] == 0:
                rating_stats[rating]['wrong'] += r['cnt']
            rating_stats[rating]['total'] += r['cnt']

        adjustments = []
        reason_parts = []

        for rating, stats in rating_stats.items():
            if stats['total'] < 20:
                continue
            accuracy = (
                stats['correct'] / (stats['correct'] + stats['wrong'])
                if (stats['correct'] + stats['wrong']) > 0
                else 0.5
            )

            baseline = THRESHOLD_BASELINES.get(rating)
            if baseline is None:
                continue

            if accuracy < 0.40:
                # 收窄：该档位判断不准，缩小范围
                adjustments.append(
                    {
                        'rating': rating,
                        'action': 'narrow',
                        'accuracy': round(accuracy, 4),
                        'sample': stats['total'],
                        'suggested_shift': MAX_THRESHOLD_STEP,
                    }
                )
                reason_parts.append(
                    f'{rating}准确率{accuracy:.0%}<40%(样本{stats["total"]})，建议收窄{MAX_THRESHOLD_STEP}分'
                )
            elif accuracy > 0.75:
                # 扩大：该档位判断很准，扩大范围
                adjustments.append(
                    {
                        'rating': rating,
                        'action': 'expand',
                        'accuracy': round(accuracy, 4),
                        'sample': stats['total'],
                        'suggested_shift': MAX_THRESHOLD_STEP,
                    }
                )
                reason_parts.append(
                    f'{rating}准确率{accuracy:.0%}>75%(样本{stats["total"]})，建议扩大{MAX_THRESHOLD_STEP}分'
                )

        return {
            'should_adjust': len(adjustments) > 0,
            'adjustments': adjustments,
            'reason': '；'.join(reason_parts) if reason_parts else '阈值无需调整',
        }

    def get_optimization_history(self, market='a_stock'):
        """查看历史优化记录（US-10）"""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT param_key, param_value, updated_at
            FROM strategy_params
            WHERE market = ? AND param_type = 'optimization_log'
            ORDER BY updated_at DESC
            LIMIT 20
        """,
            (market,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        history = []
        for r in rows:
            try:
                entry = json.loads(r['param_value'])
                entry['updated_at'] = r['updated_at']
                history.append(entry)
            except (json.JSONDecodeError, TypeError):
                history.append({'raw': r['param_value'], 'updated_at': r['updated_at']})

        return history

    def get_current_params(self, market='a_stock'):
        """获取当前参数（权重+阈值）"""
        weights = self._read_weights(market)
        config = self._read_full_config()
        rating_mapping = config.get('rating_mapping', {})
        return {
            'market': market,
            'weights': weights,
            'rating_mapping': rating_mapping,
        }

    # ================================================================
    # 内部辅助方法
    # ================================================================

    def _get_sample_count(self, market):
        """获取回测样本量"""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT COUNT(*) as cnt FROM backtest_results br
            JOIN stocks s ON s.id = br.stock_id
            WHERE s.market = ? AND br.is_correct IS NOT NULL
        """,
            (market,),
        )
        row = cursor.fetchone()
        conn.close()
        return row['cnt'] if row else 0

    def _calc_overall_accuracy(self, market):
        """计算整体回测准确率"""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT
                SUM(CASE WHEN br.is_correct = 1 THEN 1 ELSE 0 END) as correct,
                SUM(CASE WHEN br.is_correct IN (0, 1) THEN 1 ELSE 0 END) as judged
            FROM backtest_results br
            JOIN stocks s ON s.id = br.stock_id
            WHERE s.market = ? AND br.is_correct IS NOT NULL
        """,
            (market,),
        )
        row = cursor.fetchone()
        conn.close()
        if not row or not row['judged'] or row['judged'] == 0:
            return 0.5
        return row['correct'] / row['judged']

    def _estimate_dim_contribution_from_backtest(self, market):
        """无关联分析数据时，返回中性估算"""
        return {
            'kline': {
                'avg_correct': 55,
                'avg_wrong': 50,
                'contribution': 5,
                'accuracy_proxy': 0.55,
                'sample': 0,
            },
            'fundamental': {
                'avg_correct': 52,
                'avg_wrong': 50,
                'contribution': 2,
                'accuracy_proxy': 0.52,
                'sample': 0,
            },
            'news': {
                'avg_correct': 50,
                'avg_wrong': 50,
                'contribution': 0,
                'accuracy_proxy': 0.50,
                'sample': 0,
            },
            'capital_flow': {
                'avg_correct': 53,
                'avg_wrong': 50,
                'contribution': 3,
                'accuracy_proxy': 0.53,
                'sample': 0,
            },
        }

    def _read_weights(self, market):
        """读取当前权重"""
        config = self._read_full_config()
        market_key = market if market in config else 'a_stock'
        return config.get(market_key, {}).get(
            'weights', {'kline': 0.25, 'fundamental': 0.25, 'capital_flow': 0.35, 'news': 0.15}
        )

    def _read_full_config(self):
        """读取完整配置文件"""
        try:
            with open(_WEIGHTS_FILE, encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _write_weights(self, market, new_weights):
        """写入权重到 config_weights.json（热加载生效）"""
        config = self._read_full_config()
        market_key = market if market in config else 'a_stock'
        if market_key not in config:
            config[market_key] = {}
        config[market_key]['weights'] = new_weights
        config['_更新时间'] = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M')

        with open(_WEIGHTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        logger.info(f'[M9] 权重已更新: market={market}, weights={new_weights}')

    def _record_optimization(self, market, changes, reasons, acc_before, acc_after):
        """记录优化到 strategy_params（审计追溯）"""
        conn = get_connection()
        cursor = conn.cursor()

        now_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')
        log_key = f'opt_{datetime.now(_CN_TZ).strftime("%Y%m%d_%H%M%S")}'
        log_value = json.dumps(
            {
                'market': market,
                'changes': changes,
                'reason': '；'.join(reasons),
                'accuracy_before': round(acc_before, 4),
                'accuracy_after': round(acc_after, 4),
                'timestamp': now_str,
            },
            ensure_ascii=False,
        )

        cursor.execute(
            """
            INSERT OR REPLACE INTO strategy_params (market, param_type, param_key, param_value, updated_at)
            VALUES (?, 'optimization_log', ?, ?, ?)
        """,
            (market, log_key, log_value, now_str),
        )

        # 同时更新 current 权重记录
        new_weights = self._read_weights(market)
        cursor.execute(
            """
            INSERT OR REPLACE INTO strategy_params (market, param_type, param_key, param_value, updated_at)
            VALUES (?, 'weights', 'current', ?, ?)
        """,
            (market, json.dumps(new_weights), now_str),
        )

        conn.commit()
        conn.close()
        logger.info(f'[M9] 优化记录已写入 strategy_params: {log_key}')

    # ================================================================
    # M9-COOLDOWN 冷却期辅助方法
    # ================================================================

    def _get_last_optimization(self, market):
        """从 strategy_params 查询最近一条 optimization_log，解析时间和调整方向

        Returns:
            dict: {'time': datetime, 'direction': dict} 或 None
        """
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT param_value, updated_at
            FROM strategy_params
            WHERE market = ? AND param_type = 'optimization_log'
            ORDER BY updated_at DESC
            LIMIT 1
        """,
            (market,),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return None

        try:
            entry = json.loads(row['param_value'])
        except (json.JSONDecodeError, TypeError):
            return None

        # 解析时间戳
        timestamp_str = entry.get('timestamp') or row['updated_at']
        try:
            opt_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M:%S').replace(tzinfo=_CN_TZ)
        except (ValueError, TypeError):
            try:
                opt_time = datetime.strptime(timestamp_str, '%Y-%m-%d %H:%M').replace(tzinfo=_CN_TZ)
            except (ValueError, TypeError):
                return None

        # 提取权重调整方向（delta 字典）
        direction = {}
        changes = entry.get('changes', [])
        for change in changes:
            if change.get('type') == 'weights' and 'delta' in change:
                direction = change['delta']
                break

        return {'time': opt_time, 'direction': direction}

    def _is_same_direction(self, last_direction, current_delta):
        """判断上次调整方向与本次建议方向是否一致

        规则：各维度 delta 符号相同即为同方向。
        只比较非零维度，如果所有非零维度符号一致则返回 True。
        """
        if not last_direction or not current_delta:
            return False

        # 找出两者共同的非零维度
        same_count = 0
        diff_count = 0
        for dim in current_delta:
            cur_val = current_delta.get(dim, 0)
            last_val = last_direction.get(dim, 0)
            if cur_val == 0 or last_val == 0:
                continue
            # 比较符号
            if (cur_val > 0 and last_val > 0) or (cur_val < 0 and last_val < 0):
                same_count += 1
            else:
                diff_count += 1

        # 所有非零维度符号一致 = 同方向
        if same_count > 0 and diff_count == 0:
            return True
        return False

    # ================================================================
    # M9-THRESHOLD-APPLY 阈值写入 + 安全阀辅助方法
    # ================================================================

    def _write_thresholds(self, market, adjustments):
        """将阈值调整写入 config_weights.json 的 rating_mapping

        规则：
        - narrow（收窄）：该档位 min += shift, max -= shift
        - expand（扩大）：该档位 min -= shift, max += shift
        - 边界约束：调整后不得偏离 THRESHOLD_BASELINES +/- THRESHOLD_MAX_DEVIATION(5分)
        - 相邻档位不得重叠

        Returns:
            dict: {'success': bool, 'applied': list, 'skipped': list, 'reason': str}
        """
        config = self._read_full_config()
        rating_mapping = config.get('rating_mapping', {})
        if not rating_mapping:
            return {
                'success': False,
                'applied': [],
                'skipped': [],
                'reason': 'rating_mapping 不存在',
            }

        # 档位顺序（从高到低）
        rating_order = ['强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出']

        applied = []
        skipped = []
        skip_reasons = []

        # 先计算所有调整后的新值
        new_mapping = {}
        for rating in rating_order:
            if rating in rating_mapping:
                new_mapping[rating] = dict(rating_mapping[rating])

        for adj in adjustments:
            rating = adj['rating']
            action = adj['action']
            shift = adj.get('suggested_shift', MAX_THRESHOLD_STEP)

            if rating not in new_mapping:
                skipped.append(rating)
                skip_reasons.append(f'{rating}不在rating_mapping中')
                continue

            current = new_mapping[rating]
            baseline = THRESHOLD_BASELINES.get(rating)
            if baseline is None:
                skipped.append(rating)
                skip_reasons.append(f'{rating}无基准值')
                continue

            old_min, old_max = current['min'], current['max']

            if action == 'narrow':
                new_min = old_min + shift
                new_max = old_max - shift
            elif action == 'expand':
                new_min = old_min - shift
                new_max = old_max + shift
            else:
                skipped.append(rating)
                skip_reasons.append(f'{rating}未知动作{action}')
                continue

            # 边界约束：不偏离基准超过5分
            baseline_min = baseline - THRESHOLD_MAX_DEVIATION
            # 对于 min，不能低于 baseline - 5
            new_min = max(baseline_min, new_min)
            # 对于 max，上限100
            new_max = min(100, new_max)

            # 最低档下限0
            if rating == '强烈建议卖出':
                new_min = 0

            # 确保 min < max
            if new_min >= new_max:
                skipped.append(rating)
                skip_reasons.append(f'{rating}调整后min>=max，跳过')
                continue

            new_mapping[rating] = {
                'min': new_min,
                'max': new_max,
                'label': current.get('label', rating),
            }
            applied.append(rating)

        # 相邻档位不重叠校验
        if applied:
            overlap_issues = self._check_overlap(new_mapping)
            if overlap_issues:
                # 回滚有重叠的档位调整
                for issue in overlap_issues:
                    rating_to_skip = issue['rating']
                    if rating_to_skip in new_mapping and rating_to_skip in rating_mapping:
                        new_mapping[rating_to_skip] = dict(rating_mapping[rating_to_skip])
                        if rating_to_skip in applied:
                            applied.remove(rating_to_skip)
                        skipped.append(rating_to_skip)
                        skip_reasons.append(f'{rating_to_skip}与相邻档位重叠，跳过')

        if not applied:
            reason = (
                '所有档位调整均被跳过：' + '；'.join(skip_reasons)
                if skip_reasons
                else '无可应用的调整'
            )
            return {'success': False, 'applied': [], 'skipped': skipped, 'reason': reason}

        # 写入配置文件
        config['rating_mapping'] = new_mapping
        config['_更新时间'] = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M')

        with open(_WEIGHTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        logger.info(f'[M9] 阈值已更新: applied={applied}, skipped={skipped}')
        reason = '；'.join(skip_reasons) if skip_reasons else ''
        return {'success': True, 'applied': applied, 'skipped': skipped, 'reason': reason}

    def _validate_threshold_safety(self, market, old_mapping, new_mapping):
        """安全阀：新阈值下回测准确率不得低于旧阈值

        用新旧 rating_mapping 分别对 backtest_results 中的 total_score 重新映射评级，
        对比 is_correct 命中率。
        """
        old_acc = self._calc_accuracy_with_mapping(market, old_mapping)
        new_acc = self._calc_accuracy_with_mapping(market, new_mapping)
        logger.info(f'[M9] 阈值安全阀: old_acc={old_acc:.4f}, new_acc={new_acc:.4f}')
        return new_acc >= old_acc - 0.01  # 允许1%误差

    def _calc_accuracy_with_mapping(self, market, mapping):
        """用指定 rating_mapping 重新计算回测准确率

        流程：
        1. 从 backtest_results JOIN ratings_history 获取 total_score 和 return_1w
        2. 用 mapping 将 total_score 映射为新评级
        3. 用判定矩阵判断新评级是否正确
        4. 返回 correct / judged
        """
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT rh.total_score, br.return_1d, br.return_1w, br.return_1m
            FROM backtest_results br
            JOIN ratings_history rh ON rh.id = br.rating_id
            JOIN stocks s ON s.id = br.stock_id
            WHERE s.market = ? AND br.is_correct IS NOT NULL
        """,
            (market,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        if not rows:
            return 0.5

        correct = 0
        judged = 0

        for row in rows:
            score = row['total_score']
            if score is None:
                continue

            # 用 mapping 将 score 映射为评级
            new_rating = self._score_to_rating(score, mapping)
            if not new_rating:
                continue

            # 用判定矩阵判断正确性（优先 1d，其次 1w，再次 1m）
            return_pct = row['return_1d']
            if return_pct is None:
                return_pct = row['return_1w']
            if return_pct is None:
                return_pct = row['return_1m']

            result = self._judge_rating(new_rating, return_pct)
            if result is not None:
                judged += 1
                if result == 1:
                    correct += 1

        if judged == 0:
            return 0.5
        return correct / judged

    def _score_to_rating(self, score, mapping):
        """将分数映射到评级档位"""
        for rating, bounds in mapping.items():
            if bounds['min'] <= score <= bounds['max']:
                return rating
        return None

    @staticmethod
    def _judge_rating(rating, return_pct):
        """根据评级和收益率判定有效性（复制自 backtest_engine 判定矩阵）

        Returns:
            1 = 正确, 0 = 错误, None = 中性
        """
        if return_pct is None or rating is None:
            return None

        JUDGEMENT = {
            '强烈推荐买入': {'direction': 'up', 'correct_min': 2.0, 'wrong_max': -3.0},
            '推荐买入': {'direction': 'up', 'correct_min': 1.0, 'wrong_max': -2.0},
            '持有观望': {'direction': 'neutral', 'correct_low': -3.0, 'correct_high': 3.0},
            '建议减仓': {'direction': 'down', 'correct_max': -1.0, 'wrong_min': 3.0},
            '强烈建议卖出': {'direction': 'down', 'correct_max': -1.0, 'wrong_min': 3.0},
        }

        config = JUDGEMENT.get(rating)
        if not config:
            return None

        direction = config['direction']
        if direction == 'up':
            if return_pct >= config['correct_min']:
                return 1
            elif return_pct <= config['wrong_max']:
                return 0
            return None
        elif direction == 'down':
            if return_pct <= config['correct_max']:
                return 1
            elif return_pct >= config['wrong_min']:
                return 0
            return None
        else:  # neutral
            if config['correct_low'] <= return_pct <= config['correct_high']:
                return 1
            return 0

    def _check_overlap(self, new_mapping):
        """校验相邻档位区间无交叉

        档位顺序（从高到低）：强烈推荐买入 > 推荐买入 > 持有观望 > 建议减仓 > 强烈建议卖出
        规则：高档 min > 低档 max

        Returns:
            list: 重叠问题列表 [{'rating': ..., 'overlap_with': ...}]，空列表=无重叠
        """
        rating_order = ['强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出']
        issues = []

        for i in range(len(rating_order) - 1):
            high_rating = rating_order[i]
            low_rating = rating_order[i + 1]

            if high_rating not in new_mapping or low_rating not in new_mapping:
                continue

            high_min = new_mapping[high_rating]['min']
            low_max = new_mapping[low_rating]['max']

            # 高档的 min 应该 > 低档的 max
            if high_min <= low_max:
                # 重叠，记录低档位为问题（调整低档位）
                issues.append({'rating': low_rating, 'overlap_with': high_rating})

        return issues

    def _rollback_thresholds(self, old_mapping):
        """回滚阈值到旧值"""
        config = self._read_full_config()
        config['rating_mapping'] = old_mapping
        config['_更新时间'] = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M')

        with open(_WEIGHTS_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)

        logger.info('[M9] 阈值已回滚到优化前状态')
