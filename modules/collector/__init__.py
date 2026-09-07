"""
数据采集子包（OPT-3，2026-09-07）——自 modules/data_collector.py 按数据源拆分。

导入顺序保证（关键不变量）：本包的 __init__ 强制 `_env` → `http_client` 最先加载，
使 requests.Session 的无代理补丁先于 akshare 导入生效（与拆分前模块级顺序一致）。
任何直接 `import modules.collector.<子模块>` 也会先触发本 __init__，顺序不变被保证。

公共 logger 名保持 'modules.data_collector'（见 _env.py）。
对外入口仍是 modules/data_collector.py facade（全量再导出）。
"""

from modules.collector import (
    _env,  # noqa: F401  路径守卫/公共 logger，必须最先
    http_client,  # noqa: F401  requests 补丁，必须先于 akshare
)
