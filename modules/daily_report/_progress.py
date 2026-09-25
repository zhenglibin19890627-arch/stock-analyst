"""进度追踪域（t6 拆包）：012-B 批次进度文件 logs/report_progress.json 读写。

_REPORT_PROGRESS_PATH 原居单文件模块头（供 blueprints/report.py 经 facade 复用），
随唯一消费方单宿本模块；__file__ 目录层级由 ×2 变 ×3（包内多一层），落点路径相同。
"""

import json
import os
import threading
from datetime import datetime

from modules.daily_report._env import _CN_TZ

# ================================================================
# 012-B: 进度追踪 + 超时控制辅助函数
# ================================================================

# 进度文件并发写锁（线程池内多只股票同时更新 stage 时防互相覆盖）
_progress_lock = threading.Lock()
# 进度文件路径（供查询 API 复用）
_REPORT_PROGRESS_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    'logs',
    'report_progress.json',
)


def _update_progress_file(data: dict):
    """012-B: 更新进度文件 logs/report_progress.json（线程安全）"""
    try:
        os.makedirs(os.path.dirname(_REPORT_PROGRESS_PATH), exist_ok=True)
        with _progress_lock:
            with open(_REPORT_PROGRESS_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 进度文件写入失败不阻塞业务


def _update_progress_stage(symbol: str, stage: str, current: int | None = None):
    """增量更新进度文件的 stage（当前正在做什么）与 last_update。

    由工作线程（_process_single_stock）调用：读现有进度 → 更新 stage → 写回。
    失败静默，不阻塞采集流程。
    """
    try:
        with _progress_lock:
            data = {}
            try:
                with open(_REPORT_PROGRESS_PATH, encoding='utf-8') as f:
                    data = json.load(f)
            except Exception:
                return
            data['stage'] = stage
            if symbol:
                data['current_symbol'] = symbol
            if current is not None:
                data['current'] = current
            data['last_update'] = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')
            with open(_REPORT_PROGRESS_PATH, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception:
        pass  # 进度写入失败不阻塞业务
