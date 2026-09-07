"""
technical_detail.py 展示读数修复测试（2026-09-07 批判性检查三连修）。

覆盖：
1. 均线状态——深空头反弹初期不再误标"均线纠缠"（中免月线 MA5=55.42 vs MA10=67.54）；
2. MACD 状态——零轴下"多头"补"反弹"语境注记；
3. 周/月量能——分母剔除当根（进行中的周/月只含几天量，含当根会结构性"明显缩量"）。
"""

from modules.technical_detail import _macd, _sma, compute_technical_detail

# 深空头 + 反弹初期：MA5=65.6 远低于 MA10=69.4（差 5.5%），但 MA10>MA20 打破严格单调
_DEEP_GAP_SERIES = [60.0] * 20 + [62, 66, 72, 80, 86] + [80, 72, 64, 58, 54]
# 三线黏合（完全平稳，MA5=MA10=MA20，各线差距 0%）
_TIGHT_SERIES = [100.0] * 30
# 零轴下金叉：50 根深跌 + 15 根强反弹（dif=-1.76 > dea=-5.34 且 dif<0，实测验证）
_REBOUND_SERIES = [80.0 - i * 1.0 for i in range(50)] + [1.5 + i * 2.5 for i in range(15)]


def test_ma_state_deep_gap_is_bearish_not_entangled():
    """MA5 远低于 MA10（>2%黏合阈值）但非严格单调 → 空头排列（原逻辑误标纠缠）。"""
    detail = compute_technical_detail(_DEEP_GAP_SERIES, _DEEP_GAP_SERIES, _DEEP_GAP_SERIES, [], min_bars=20)
    assert detail['ma_state'] == '空头排列'


def test_ma_state_tight_is_entangled():
    """三线黏合（差距 ≤2%）→ 均线纠缠。"""
    detail = compute_technical_detail(_TIGHT_SERIES, _TIGHT_SERIES, _TIGHT_SERIES, [], min_bars=20)
    assert detail['ma_state'] == '均线纠缠'


def test_ma_state_strict_monotonic_unchanged():
    """严格单调多头/空头排列行为不变。"""
    up = [100.0 + i * 1.0 for i in range(30)]
    down = [130.0 - i * 1.0 for i in range(30)]
    assert compute_technical_detail(up, up, up, [], min_bars=20)['ma_state'] == '多头排列'
    assert compute_technical_detail(down, down, down, [], min_bars=20)['ma_state'] == '空头排列'


def test_macd_state_zero_axis_rebound_note():
    """零轴下金叉 → "零轴下多头(反弹)"，不再裸标"多头"。"""
    dif, dea, _hist, state = _macd(_REBOUND_SERIES)
    assert dif > dea and dif < 0
    assert state == '零轴下多头(反弹)'


def test_macd_state_pure_bull_bear_unchanged():
    """零轴上多头/零轴下空头标签不变。"""
    up = [100.0 + i * 1.5 for i in range(60)]
    down = [160.0 - i * 1.5 for i in range(60)]
    assert _macd(up)[3] == '多头'
    assert _macd(down)[3] == '空头'


def test_weekly_vol_ratio_excludes_current_bar():
    """周/月量能分母剔除当根：当根 200、前 20 根各 1000 → vr=0.2（旧口径 0.21 含当根）。"""
    closes = [100.0 + (i % 5) * 0.2 for i in range(21)]
    volumes = [1000.0] * 20 + [200.0]
    detail = compute_technical_detail(
        closes, closes, closes, volumes, key_prefix='weekly_', min_bars=20
    )
    assert detail['weekly_vol_ratio'] == 0.2


def test_daily_vol_ratio_unchanged():
    """日线口径不变：分母含当根（最近 20 根均量）。"""
    closes = [100.0 + (i % 5) * 0.2 for i in range(21)]
    volumes = [1000.0] * 20 + [200.0]
    detail = compute_technical_detail(closes, closes, closes, volumes, min_bars=20)
    assert detail['vol_ratio'] == 0.21  # 200/960 → 0.21
