"""环境基座：路径守卫、公共 logger（名保持 modules.data_collector）、CN 时区、项目配置导入。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""


import json
import logging
import math
import os
import sys
import time
import traceback

# 获取本地时区当前时间（北京时间），所有时间戳统一使用此时区
from datetime import datetime, timedelta, timezone
from datetime import timedelta as _td

import pandas as pd

_CN_TZ = timezone(_td(hours=8), name='Asia/Shanghai')


def now_cn():
    """返回北京时间字符串（格式 YYYY-MM-DD HH:MM:SS），用于所有数据库写入"""
    return datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')

# 添加项目根目录到路径，确保能导入 config
# （OPT-3：原文件位于 modules/ 下取两层 dirname；本文件位于 modules/collector/ 下，需三层）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
from config import (
    EM_USE_PROXY,
    FUNDAMENTAL_REPORT_TTL_DAYS,
    KLINE_DAYS,
    MAX_RETRIES,
    NORTH_CAPITAL_CACHE_DAYS,
    PE_PB_CACHE_TTL_HOURS,
)
from database.db_manager import get_connection

# 日志配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
# OPT-3：logger 名保持拆分前的 'modules.data_collector'，日志检索/过滤行为不变
logger = logging.getLogger('modules.data_collector')
