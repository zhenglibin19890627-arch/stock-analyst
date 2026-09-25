"""权重实验域（t3 拆包，D4 裁定预留）：WeightExperimentRunner。

划分依据：M9 预留接口（仅模拟计算、不改生产权重），依赖引擎对照组报告，
与生产回测链隔离。实现体为原文件 L1764-1887 逐字节搬移。
"""

from modules.backtest_engine._env import get_connection
from modules.backtest_engine.engine import BacktestEngine

# ============================================================
# 五、权重实验场景（D4 裁定预留）
# ============================================================


class WeightExperimentRunner:
    """权重实验场景（M9 预留接口）

    ⚠️ 重要边界：本模块仅模拟计算，不修改生产权重。
    """

    EXPERIMENTS = [
        {
            'id': 'd4_news_0_to_20',
            'name': 'D4-消息面权重 0→20%',
            'description': '将A股消息面权重从当前值提升至20%，其他维度同比缩减',
            'market': 'a_stock',
            'changes': {'news': 0.20, 'kline': -0.05, 'fundamental': -0.10, 'capital_flow': -0.05},
        },
        {
            'id': 'd4_hk_news_boost',
            'name': 'D4-港股消息面增强',
            'description': '港股消息面权重从10%提升至20%，测试港股评级敏感性',
            'market': 'hk_stock',
            'changes': {'news': 0.20, 'kline': -0.05, 'fundamental': -0.05, 'capital_flow': -0.10},
        },
    ]

    def list_experiments(self):
        """列出所有可用实验场景。"""
        return [
            {
                'id': e['id'],
                'name': e['name'],
                'description': e['description'],
                'market': e['market'],
            }
            for e in self.EXPERIMENTS
        ]

    def run_experiment(self, experiment_id):
        """执行权重实验：用实验权重对历史评级重新评分，对比准确率差异。

        ⚠️ 仅模拟计算，不修改生产权重。

        Returns: dict with ΔAccuracy and comparison details
        """
        exp = None
        for e in self.EXPERIMENTS:
            if e['id'] == experiment_id:
                exp = e
                break
        if not exp:
            return {'success': False, 'error': f'experiment {experiment_id} not found'}

        # 读取当前市场回测结果作为对照组
        engine = BacktestEngine()
        control_report = engine.compute_market_report(exp['market'])

        if control_report.get('total', 0) == 0:
            return {
                'success': True,
                'experiment_id': experiment_id,
                'experiment_name': exp['name'],
                'note': '当前无回测数据，无法执行实验。请先运行批量回测。',
                'control_accuracy': None,
                'experiment_accuracy': None,
                'delta_accuracy': None,
            }

        # 模拟：读取历史评级数据，用实验权重重新计算评级，然后模拟回测
        # 由于无法实际重跑评分引擎（需要历史四维数据），这里用统计方法估算
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT br.* FROM backtest_results br WHERE br.market = ? ORDER BY br.rating_date',
            (exp['market'],),
        )
        bt_rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        # 简化模拟：消息面权重提升 → 评级分布向高/低两端偏移
        # 实际的权重实验需要 M9 阶段完整的历史四维数据重算
        # 这里提供框架和接口，实际计算标注为"待M9完整实施"

        control_accuracy = control_report.get('accuracy', 0)
        # 估算：权重变化对准确率的影响（基于历史弹性系数的粗略估算）
        # 实际影响需要完整的历史四维数据重算
        estimated_impact = self._estimate_weight_impact(exp, bt_rows)

        return {
            'success': True,
            'experiment_id': experiment_id,
            'experiment_name': exp['name'],
            'market': exp['market'],
            'weight_changes': exp['changes'],
            'control_accuracy': round(control_accuracy, 4),
            'control_total': control_report.get('total', 0),
            'experiment_accuracy': round(control_accuracy + estimated_impact, 4),
            'delta_accuracy': round(estimated_impact, 4),
            'note': '基于短期样本的初步结论，M9阶段复核。权重变化仅模拟，不影响生产配置。',
            'sample_warning': control_report.get('small_sample_warning', True),
        }

    @staticmethod
    def _estimate_weight_impact(exp, bt_rows):
        """粗略估算权重变化对准确率的影响。

        方法：统计消息面维度得分与评级准确率的相关性，
        然后根据权重变化幅度估算影响。

        ⚠️ 这是简化估算，M9阶段需要完整重算。
        """
        # 无历史四维数据时，返回保守估算
        # 消息面权重从0→20%，预期对准确率有 ±2-5% 的边际影响
        # 但方向不确定（取决于消息面因子的有效性）
        # 返回0表示"无显著变化"的保守估计
        if not bt_rows:
            return 0.0

        # 简单方法：看当前准确率与50%（随机）的偏差
        # 如果当前准确率>50%，说明评级有一定有效性，增加消息面权重可能提升或降低
        # 保守返回0，实际影响待M9完整评估
        return 0.0
