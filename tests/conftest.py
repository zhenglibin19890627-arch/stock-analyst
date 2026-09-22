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


@pytest.fixture(autouse=True)
def _isolate_db_backup_dir(tmp_path_factory, monkeypatch):
    """021BR 实测缺陷：部分测试只 patch DB_PATH 不 patch BACKUP_DIR，
    init_database 迁移（B12/013）触发的备份会写进真实 backups/ 目录，
    并经 MAX_BACKUPS=10 保留策略把真实历史备份挤掉。autouse 兜底：
    所有测试的迁移备份一律进临时目录（测试内显式再 patch 者以其为准）。"""
    from database import db_manager

    backup_dir = tmp_path_factory.mktemp('db_backups')
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(backup_dir))


@pytest.fixture(scope='session')
def provider():
    """全局可复用的 MockDataProvider 实例（端到端测试用）"""
    return MockDataProvider()
