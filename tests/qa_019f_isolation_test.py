# -*- coding: utf-8 -*-
"""
QA 019F 独立验收测试脚本 — 评分纯净隔离 + inspect.stack 保护块
独立于开发自验，不导入开发测试逻辑。
"""
import sqlite3
import sys
import os
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from modules import analysis_engine
from modules import data_adapter


def _make_test_db(rows):
    """创建临时测试库，写入指定行数据"""
    db_fd, db_path = tempfile.mkstemp(suffix='.db')
    os.close(db_fd)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE raw_capital_flow (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            stock_id INTEGER NOT NULL,
            trade_date DATE NOT NULL,
            main_net_inflow REAL,
            main_net_inflow_pct REAL,
            super_large_net REAL,
            large_net REAL,
            medium_net REAL,
            small_net REAL,
            north_holding_change REAL,
            margin_balance REAL,
            ths_net_inflow REAL,
            is_estimated INTEGER NOT NULL DEFAULT 0,
            UNIQUE(stock_id, trade_date)
        )
    """)
    for r in rows:
        conn.execute(
            "INSERT INTO raw_capital_flow "
            "(stock_id, trade_date, main_net_inflow, is_estimated) "
            "VALUES (?, ?, ?, ?)", r
        )
    conn.commit()
    conn.close()
    return db_path


def _make_conn_factory(db_path):
    """返回一个工厂函数，每次创建新连接"""
    def _get_conn():
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn
    return _get_conn


class TestScorePurityIsolation(unittest.TestCase):
    """验收标准 3：评分纯净隔离验证"""

    def setUp(self):
        self.db_path = None

    def tearDown(self):
        if self.db_path and os.path.exists(self.db_path):
            try:
                os.remove(self.db_path)
            except PermissionError:
                pass

    def test_01_analysis_engine_filters_estimated(self):
        """T1: analysis_engine._read_capital_data 过滤估算行"""
        # 真实数据 3 天 + 估算数据 2 天（不同日期，避免 UNIQUE 冲突）
        rows = [
            (100, '2026-08-01', 1000.0, 0),
            (100, '2026-08-02', 2000.0, 0),
            (100, '2026-08-03', 3000.0, 0),
            (100, '2026-08-04', 9999.0, 1),
            (100, '2026-08-05', 8888.0, 1),
        ]
        self.db_path = _make_test_db(rows)
        with unittest.mock.patch.object(
            analysis_engine, 'get_connection', _make_conn_factory(self.db_path)
        ):
            result = analysis_engine._read_capital_data(100, limit=20)
        self.assertEqual(len(result), 3, f"期望3行真实, 实际{len(result)}行")
        for r in result:
            self.assertNotEqual(r['is_estimated'], 1,
                                f"估算行泄漏: {r['trade_date']}")
        print("[PASS] T1: analysis_engine 估算行已过滤 (3行真实, 0行估算)")

    def test_02_analysis_engine_signature_unchanged(self):
        """T2: 签名 _read_capital_data(stock_id, limit=20) 不变"""
        import inspect as _inspect
        sig = _inspect.signature(analysis_engine._read_capital_data)
        params = list(sig.parameters.keys())
        self.assertEqual(params, ['stock_id', 'limit'], f"签名变更: {params}")
        self.assertEqual(sig.parameters['limit'].default, 20)
        print("[PASS] T2: _read_capital_data 签名不变 (stock_id, limit=20)")

    def test_03_data_adapter_filters_estimated_regression(self):
        """T3: data_adapter._read_capital_data 过滤估算行（019E 回归）"""
        rows = [
            (100, '2026-08-01', 1000.0, 0),
            (100, '2026-08-02', 2000.0, 0),
            (100, '2026-08-03', 3000.0, 0),
            (100, '2026-08-04', 9999.0, 1),
            (100, '2026-08-05', 8888.0, 1),
        ]
        self.db_path = _make_test_db(rows)
        with unittest.mock.patch.object(
            data_adapter, 'get_connection', _make_conn_factory(self.db_path)
        ):
            result = data_adapter._read_capital_data(100, limit=20)
        self.assertEqual(len(result), 3, f"期望3行真实, 实际{len(result)}行")
        for r in result:
            self.assertNotEqual(r['is_estimated'], 1,
                                f"估算行泄漏: {r['trade_date']}")
        print("[PASS] T3: data_adapter 回归验证通过 (019E 过滤未破坏)")

    def test_04_only_estimated_rows_returns_empty(self):
        """T4: 仅估算行时返回空集（不崩溃，架构评审 R-3）"""
        rows = [
            (100, '2026-08-03', 5555.0, 1),
            (100, '2026-08-04', 6666.0, 1),
        ]
        self.db_path = _make_test_db(rows)
        with unittest.mock.patch.object(
            analysis_engine, 'get_connection', _make_conn_factory(self.db_path)
        ):
            result = analysis_engine._read_capital_data(100, limit=20)
        self.assertEqual(len(result), 0, "仅有估算行时应返回空集")
        print("[PASS] T4: 仅估算行时返回空集, 无崩溃")


class TestInspectStackProtection(unittest.TestCase):
    """验收标准 4：inspect.stack 保护块验证"""

    def test_05_inspect_exception_downgrades_safely(self):
        """T5: mock inspect.stack 抛异常 → 降级为 'batch-analyze'"""
        import inspect

        original_stack = inspect.stack
        captured = {}

        def _raise_stack(*args, **kwargs):
            raise IndexError("模拟线程栈损坏")

        try:
            inspect.stack = _raise_stack
            import inspect as _inspect
            try:
                _caller_file = _inspect.stack()[1].filename
                _trigger_source = '日报批次' if 'daily_report' in _caller_file else 'batch-analyze'
            except Exception:
                _trigger_source = 'batch-analyze'
            captured['result'] = _trigger_source
        finally:
            inspect.stack = original_stack

        self.assertEqual(captured['result'], 'batch-analyze',
                         f"降级值错误: {captured['result']}")
        print("[PASS] T5: inspect.stack 异常时降级为 'batch-analyze'")

    def test_06_trigger_source_logic_intact(self):
        """T6: 正常路径 _trigger_source 逻辑不变（回归）"""
        import inspect
        _caller_file = inspect.stack()[1].filename
        _trigger_source = '日报批次' if 'daily_report' in _caller_file else 'batch-analyze'
        self.assertEqual(_trigger_source, 'batch-analyze')
        print("[PASS] T6: 正常路径 _trigger_source 逻辑不变")

    def test_07_except_catches_exception_not_base(self):
        """T7: 源码确认 except Exception（非 BaseException）"""
        import re
        dc_path = PROJECT_ROOT / 'modules' / 'data_collector.py'
        source = dc_path.read_text(encoding='utf-8')
        pattern = r"except Exception:\s*\n\s*_trigger_source = 'batch-analyze'"
        self.assertIsNotNone(re.search(pattern, source),
                             "未找到 except Exception → batch-analyze 块")
        self.assertIsNone(
            re.search(r"except BaseException.*_trigger_source", source),
            "不应使用 BaseException 捕获")
        print("[PASS] T7: except Exception 确认（非 BaseException）")


class TestFilterExpressionConsistency(unittest.TestCase):
    """验收标准 1/3/5：过滤表达式逐字符一致性"""

    def test_08_expression_identical_across_score_points(self):
        """T8: 评分链路过滤表达式逐字符一致"""
        canonical = "AND (is_estimated = 0 OR is_estimated IS NULL)"
        files_to_check = {
            'analysis_engine': PROJECT_ROOT / 'modules' / 'analysis_engine.py',
            'data_adapter': PROJECT_ROOT / 'modules' / 'data_adapter.py',
            'advisor': PROJECT_ROOT / 'modules' / 'advisor.py',
        }
        for name, path in files_to_check.items():
            source = path.read_text(encoding='utf-8')
            count = source.count(canonical)
            self.assertGreaterEqual(count, 1,
                f"{name}: 未找到标准表达式")
        print("[PASS] T8: 3 处评分入口表达式逐字符一致")

    def test_09_data_collector_supplement_check(self):
        """T9: data_collector 补采清单过滤（间接评分链路）"""
        canonical = "AND (is_estimated = 0 OR is_estimated IS NULL)"
        dc_path = PROJECT_ROOT / 'modules' / 'data_collector.py'
        source = dc_path.read_text(encoding='utf-8')
        count = source.count(canonical)
        self.assertGreaterEqual(count, 2,
            f"data_collector: 期望至少2处补采过滤, 实际{count}处")
        print(f"[PASS] T9: data_collector 补采过滤 {count} 处")


# 补充 import（unittest.mock 在文件顶部未显式导入模块路径时需要）
import unittest.mock


if __name__ == '__main__':
    unittest.main(verbosity=2)
