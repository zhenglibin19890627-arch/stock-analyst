"""
021A/021B 红线治理：红线自动核验纳入测试套件。

单一事实来源：docs/RED_LINES.md；核验实现：scripts/check_redlines.py。
本测试保证红线核验随 `pytest tests/` 一起执行——红线从"人肉 grep"
升级为"行为锁"（测试门禁自动守护）。

021B 起（P1 行为锁迁移）：受保护函数的**签名锁定**也在此执行——
签名变化是行为契约破坏的第一信号，先于运行时错误暴露。
"""

import inspect
import os
import sys

# scripts/ 无 __init__.py，直接把目录加入 sys.path 导入
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))

import check_redlines as crl  # noqa: E402

from modules import advisor  # noqa: E402
from modules import data_collector as dc  # noqa: E402


def test_redlines_all_pass():
    results = crl.run_all_checks()
    failed = [r for r in results if not r['ok']]
    detail = '; '.join(f"[{r['rid']}] {r['name']}: {r['detail']}" for r in failed)
    assert not failed, f'红线核验失败 {len(failed)} 项:\n{detail}'


def test_b24_generate_advice_signature_locked():
    """R13（B24）：generate_advice 签名锁定（stock_id, report_date）"""
    params = list(inspect.signature(advisor.generate_advice).parameters)
    assert params == ['stock_id', 'report_date'], f'签名被修改: {params}'


def test_b24_build_capital_factors_signature_locked():
    """R14：_build_capital_factors 资金面因子构建函数签名锁定"""
    params = list(inspect.signature(advisor._build_capital_factors).parameters)
    assert params == ['factors', 'stock_data', 'stock_id'], f'签名被修改: {params}'


def test_fetch_capital_flow_signature_locked():
    """R15（011）：fetch_capital_flow 签名锁定（symbol, market），不可加参数"""
    params = list(inspect.signature(dc.fetch_capital_flow).parameters)
    assert params == ['symbol', 'market'], f'签名被修改: {params}'
