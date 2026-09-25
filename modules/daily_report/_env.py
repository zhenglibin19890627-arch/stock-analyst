"""环境基座（t6 拆包）：路径守卫 + 公共 logger + 中国时区 + 共享导入绑定。

比照 modules/collector/_env / modules/backtest_engine/_env 拆分先例的包内约定：
1. sys.path 守卫（项目根注入）先于 database/modules 导入生效——与拆分前单文件
   模块级顺序一致；__file__ 目录层级由 ×2 变 ×3（包内多一层），注入路径相同；
2. 公共 logger 名保持 'modules.daily_report'——与拆分前 logging.getLogger(__name__)
   的 logger 名逐字一致（日志面零变化；scripts/selftest_019x 按此名挂 handler）；
3. 本文件的导入绑定供 facade 再导出与兄弟子模块取用，自身不消费——F401 以行内
   noqa 豁免（同 database/db_manager.py facade 的行内 noqa 先例，不动 pyproject）；
4. 子模块不得反向 `from modules.daily_report import X`（facade 循环导入禁忌），
   一律按绝对子模块路径互相导入。

需求映射：US-11 每日报告模块；R9 日报不变量宿主 = _store，R18 超时模式宿主 = _generator。
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone  # noqa: F401  —— datetime 供 facade 再导出

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from config import (  # noqa: E402, F401  —— 路径守卫后导入；绑定供 facade 再导出
    BATCH_TIMEOUT_SECONDS,
    STOCK_TIMEOUT_SECONDS,
)
from database.db_manager import get_connection  # noqa: E402, F401  —— 同上

# 019A: 收敛三表一致逻辑，关键因子/Markdown 构建函数统一从 advisor 导入
# 避免 daily_report 与 advisor 重复定义导致 drift
from modules.advisor import (  # noqa: E402, F401  —— 同上
    _build_key_factors,
    _build_markdown_single,
    generate_advice,
)

# FIX-A：日报流程集成数据采集
from modules.data_collector import (  # noqa: E402, F401  —— 同上
    collect_stock_data,
    fetch_capital_flow_batch,
)

logger = logging.getLogger('modules.daily_report')

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')
