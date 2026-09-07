"""
data_adapter._roe_annualize 纯函数测试（2026-09-07 ROE 年化口径修复）。

背景：报告期累计 ROE 直接对比年度档位阈值会系统性低估非年报公司
（中报只含半年利润，中免中报 5.46% 年化后 10.92%）。
"""

import pytest

from modules.data_adapter import _roe_annualize


@pytest.mark.parametrize(
    'roe,report_date,expected',
    [
        (5.46, '2026-06-30', 10.92),  # 中报 ×2（中免实测值）
        (4.16, '2026-03-31', 16.64),  # 一季报 ×4（中免实测值）
        (9.0, '2026-09-30', 12.0),  # 三季报 ×4/3
        (6.48, '2025-12-31', 6.48),  # 年报不折（中免实测值）
        (5.46, '20260630', 10.92),  # 紧凑格式鲁棒
        (5.46, None, None),  # 无报告期 → 不折（保守回退）
        (None, '2026-06-30', None),  # 无 ROE
        (None, None, None),
        (5.46, 'bad', None),  # 畸形日期 → 不折
    ],
)
def test_roe_annualize(roe, report_date, expected):
    assert _roe_annualize(roe, report_date) == expected
