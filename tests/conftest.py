"""
pytest 公共配置与 fixtures

测试隔离原则：
- 所有测试通过 MockDataProvider / 直接构造 StockData 生成纯内存数据
- 不依赖运行中的数据库，不发起任何网络请求
- conftest 负责把项目根目录(stock_analyst/)加入 sys.path，使 `from modules.xxx` 可被正常导入
"""

import os
import sys

# 项目根目录（stock_analyst/），即 tests/ 的父目录
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

import pytest

from modules.mock_data_provider import MockDataProvider


@pytest.fixture(scope='session')
def provider():
    """全局可复用的 MockDataProvider 实例（端到端测试用）"""
    return MockDataProvider()
