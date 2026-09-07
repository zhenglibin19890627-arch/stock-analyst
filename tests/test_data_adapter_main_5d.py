"""
data_adapter._mean_main_inflow_5d 纯函数测试（2026-09-07 主力资金降噪字段）。

口径与 advisor.main_avg_5d 一致：过滤估算行后的正序序列，最近 5 行非空值平均。
"""

from modules.data_adapter import _mean_main_inflow_5d


def test_mean_main_inflow_5d_basic():
    rows = [
        {'main_net_inflow': 100.0},
        {'main_net_inflow': 200.0},
        {'main_net_inflow': None},
        {'main_net_inflow': 300.0},
        {'main_net_inflow': 400.0},
        {'main_net_inflow': 500.0},
        {'main_net_inflow': 700.0},
    ]
    # 最近 5 行（None/300/400/500/700）非空值 = 300,400,500,700 → 均值 475
    assert _mean_main_inflow_5d(rows) == 475.0


def test_mean_main_inflow_5d_short_history():
    # 次新股不足 5 行：按实际行数平均
    rows = [{'main_net_inflow': 100.0}, {'main_net_inflow': 300.0}]
    assert _mean_main_inflow_5d(rows) == 200.0


def test_mean_main_inflow_5d_all_missing():
    assert _mean_main_inflow_5d([{'main_net_inflow': None}]) is None
    assert _mean_main_inflow_5d([]) is None
