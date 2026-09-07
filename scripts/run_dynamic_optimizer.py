"""动态窗口权重优化器——零代码入口（021AI）

用法：
    python scripts/run_dynamic_optimizer.py          # A股搜索 + 中文报告（只诊断，不改权重）
    python scripts/run_dynamic_optimizer.py --apply  # 同上；若数据门槛与测试段提升
                                                     # 双达标才写入权重（否则仍只报告）

输出：当前权重 vs 最优候选的前向验证对比、门槛裁决、后续数据积累建议。
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.dynamic_optimizer import (  # noqa: E402
    MIN_JUDGED_WINDOWS,
    MIN_TEST_GAIN,
    MIN_V5_RATING_DAYS,
    apply_weights,
    search,
)


def fmt_acc(v):
    return f'{v * 100:.1f}%' if v is not None else '—'


def main():
    apply_flag = '--apply' in sys.argv
    res = search(market='a_stock', verbose=True)
    meta = res['meta']

    print()
    print('=' * 62)
    print('动态窗口权重优化报告（021AI）——目标：评级有效期内方向命中')
    print('=' * 62)
    print(f"数据：{meta['stocks']} 只股票（已排除行业覆盖 {len(meta['override_excluded'])} 只），"
          f"{meta['total_days']} 个评级日（其中 v5 引擎 {meta['v5_days']} 天）")
    print(f"前向切分：{res['split_date']} 之前训练 / 之后测试（70/30）")
    print()

    mc = res['metrics_current']
    print(f"当前权重: {res['current_weights']}")
    print(f"  训练段: {fmt_acc(mc['train']['accuracy'])}（{mc['train']['n_judged']} 窗口）"
          f"  测试段: {fmt_acc(mc['test']['accuracy'])}（{mc['test']['n_judged']} 窗口）")
    print(f"  全期:   {fmt_acc(mc['full']['accuracy'])}（{mc['full']['n_judged']} 窗口）")
    print()

    if res.get('metrics_best'):
        mb = res['metrics_best']
        print(f"最优候选: {res['best_weights']}")
        print(f"  训练段: {fmt_acc(mb['train_acc'])}（{mb['train_n']}）"
              f"  测试段: {fmt_acc(mb['test_acc'])}（{mb['test_n']}）"
              f"  测试段提升: {'+' if (mb['test_gain'] or 0) >= 0 else ''}"
              f"{(mb['test_gain'] or 0) * 100:.1f}pp")
        print()
        print('前五候选（训练段选优，测试段核验）:')
        for i, t in enumerate(res['top5'], 1):
            print(f"  {i}. {t['weights']} 训练 {fmt_acc(t['train_acc'])} / 测试 {fmt_acc(t['test_acc'])}")
    else:
        print('无有效候选（训练窗口不足）。')
    print()

    print('裁决：', res['recommendation'])
    if res.get('pilot_only'):
        print(f"  · 数据门槛：v5 纯净评级日 {meta['v5_days']} < {MIN_V5_RATING_DAYS}"
              '（引擎 08-14 切换，此前维度分口径不同，混池结果仅作方向参考）')
        print('  · 每个交易日约积累 20~25 个 v5 评级日，约一个月后可达标重估')
    mc_n = mc['test']['n_judged']
    if mc_n < MIN_JUDGED_WINDOWS:
        print(f"  · 窗口门槛：测试段 {mc_n} < {MIN_JUDGED_WINDOWS}，样本不足以下结论")
    if res.get('metrics_best') and not res['gate_passed'] and not res.get('pilot_only'):
        gain = res['metrics_best'].get('test_gain') or 0
        if gain < MIN_TEST_GAIN:
            print(f"  · 提升门槛：测试段提升 {gain * 100:.1f}pp < {MIN_TEST_GAIN * 100:.0f}pp"
                  '（未达采纳线，防止过拟合噪声）')
    print('  · 港股不参与（样本过薄）；行业覆盖股票不受基座权重影响')
    print()

    if apply_flag and res['gate_passed']:
        old = apply_weights('a_stock', res['best_weights'], res['recommendation'])
        print('已写入新权重（热加载生效，无需重启）：')
        print(f"  旧: {old}")
        print(f"  新: {res['best_weights']}")
    elif apply_flag:
        print('--apply 已指定，但门槛未达标——不写权重（防止过拟合）。')
    else:
        print('（只读模式：未指定 --apply，任何情况都不改权重）')


if __name__ == '__main__':
    main()
