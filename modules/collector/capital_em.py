"""资金面·东方财富源（熔断冷却/主机轮换/封禁状态文件）。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
import json
import math
import os
import time

import pandas as pd

from modules.collector._env import logger
from modules.collector.http_client import _http_get_em
from modules.collector.symbols_status import _normalize_hk_symbol

_EM_INTER_DELAY_RANGE = (2.0, 5.0)     # 股票间基础错峰延迟（秒）
_EM_BATCH_SIZE = 5                     # 分批大小（只）
_EM_BATCH_GAP_RANGE = (30.0, 60.0)     # 批间间隔（秒）
_EM_BACKOFF_CAP_SECONDS = 30           # 退避延迟上限（秒）
_EM_COOLDOWN_FAIL_N = 3                # 冷却触发：连续失败只数
_EM_COOLDOWN_SECONDS = 60              # 冷却暂停时长（秒）
_EM_CIRCUIT_BREAK_N = 5                # 熔断触发：连续失败只数
_EM_FALLBACK_TOTAL_CAP_SECONDS = 600   # 回退循环整体软超时（秒）

# ============================================================
# 019Z：东财"当日熔断冷却"状态（进程级）
# 批量回退循环触发熔断后，冷却窗口内所有东财资金面请求直接跳过（走新浪/估算），
# 避免每只股票空耗 4 轮 × 30~60s 重试（约 2.5 分钟/只 × 29 只 ≈ 70 分钟无谓等待）。
# 依据社区实测（a-stock-data SKILL 2026-06）：东财临时封禁通常"几分钟到几小时"，
# 冷却 2 小时后自动恢复尝试；期间任意一次东财成功即提前解除。
# ============================================================
_EM_BAN_UNTIL = 0.0          # 熔断冷却截止时间戳（0=未熔断）
_EM_BAN_TTL_SECONDS = 7200   # 冷却时长（2 小时）
# 020C：熔断状态持久化文件（重启后仍记忆"东财不可用"，避免每轮从头挨 4 轮重试）
# OPT-3：原文件位于 modules/ 下取两层 dirname；本文件位于 modules/collector/ 下，需三层
_EM_BAN_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'logs', 'em_ban_state.json'
)


def _em_ban_state_load():
    """从持久化文件读取熔断截止时间戳（不存在/过期返回 0）。"""
    try:
        with open(_EM_BAN_STATE_FILE, encoding='utf-8') as f:
            data = json.load(f)
            until = float(data.get('until', 0))
            if until > time.time():
                return until
    except (OSError, ValueError, TypeError):
        pass
    return 0.0


def _em_ban_state_save(until):
    """写入熔断截止时间戳（尽力而为，失败不影响主流程）。"""
    try:
        with open(_EM_BAN_STATE_FILE, 'w', encoding='utf-8') as f:
            json.dump({'until': until}, f)
    except OSError:
        pass


def _em_ban_state_clear():
    """清除持久化熔断状态。"""
    try:
        os.remove(_EM_BAN_STATE_FILE)
    except OSError:
        pass


def _em_banned() -> bool:
    """东财是否处于熔断冷却期（True=跳过东财直连，直接走备用源）。

    020C：内存状态未熔断时回查持久化文件——重启后仍记忆当日熔断，
    不再从头挨 4 轮 × 30~60s 的空等。
    """
    global _EM_BAN_UNTIL
    if time.time() < _EM_BAN_UNTIL:
        return True
    persisted = _em_ban_state_load()
    if persisted:
        _EM_BAN_UNTIL = persisted
        logger.info(f'[东财熔断] 从持久化状态恢复冷却期（至 {persisted:.0f}）')
        return True
    return False


def _em_record_ban():
    """记录东财熔断冷却窗口（批量回退循环熔断触发时调用）。"""
    global _EM_BAN_UNTIL
    _EM_BAN_UNTIL = time.time() + _EM_BAN_TTL_SECONDS
    _em_ban_state_save(_EM_BAN_UNTIL)
    logger.warning(
        f'[东财熔断] 进入冷却期 {_EM_BAN_TTL_SECONDS // 3600} 小时，'
        '期间跳过东财资金面直连（push2his/push2/akshare），直接走备用源（已持久化）'
    )


def _em_clear_ban():
    """东财任意请求成功后解除熔断冷却。"""
    global _EM_BAN_UNTIL
    if _EM_BAN_UNTIL:
        logger.info('[东财熔断] 采集成功，解除冷却期')
    _EM_BAN_UNTIL = 0.0
    _em_ban_state_clear()


# OPT-3：_rotate_em_host 随其唯一调用方 _http_get_em 归入 http_client.py（断循环导入）


def _get_em_market_code(symbol):
    """根据股票代码返回东方财富市场标识（'sh' 或 'sz'）"""
    if symbol.startswith('6') or symbol.startswith('9'):
        return 'sh'
    else:
        return 'sz'


def _parse_cn_amount(val_str):
    """
    解析中文金额格式：'65.14亿' -> 6514000000, '-7200.36万' -> -72003600
    使用 round 解决浮点精度问题（如 20.65*1e8=2064999999.9999998）
    """
    if val_str is None or val_str == '' or (isinstance(val_str, float) and pd.isna(val_str)):
        return None
    val_str = str(val_str).strip()
    try:
        if '亿' in val_str:
            return round(float(val_str.replace('亿', '')) * 1e8, 2)
        elif '万' in val_str:
            return round(float(val_str.replace('万', '')) * 1e4, 2)
        else:
            return round(float(val_str), 2)
    except ValueError:
        return None


def _safe_num(val):
    """019N: 安全数值转换。None/空串/'nan'/'NaN'/'-'/'None'(strip后)/数值NaN/±Inf → None；
    ValueError/TypeError → None；其余 → float"""
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        if s == '' or s.lower() in ('nan', 'none', '-', 'inf', '-inf'):
            return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    if pd.isna(f) or not math.isfinite(f):
        return None
    return f


def _safe_float_wan(val):
    """安全转换（元→万元，round 2），None 透传"""
    f = _safe_num(val)
    return round(f / 1e4, 2) if f is not None else None


def _safe_float_pct(val):
    """安全转换（% 字段，round 2），None 透传"""
    f = _safe_num(val)
    return round(f, 2) if f is not None else None


def _fetch_capital_flow_em_individual(symbol, market):
    """
    直接请求东方财富个股资金流向接口（不走akshare，避免代理干扰）。
    使用 _http_get_em 实现直连+代理智能回退+多轮重试。
    返回 list[dict]（含120天历史数据）或 None。
    支持A股和港股（通过secid区分）。
    019Z：东财熔断冷却期内直接返回 None（跳过 4 轮空等，链路自动落新浪/估算）。
    """
    if _em_banned():
        logger.warning(f'{symbol} 东财处于熔断冷却期，跳过个股资金流向直连（走备用源）')
        return None

    # 获取secid（A股: 0/1.代码, 港股: 116.5位代码）
    secid = _get_em_secid(symbol, market)

    url = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
    params = {
        'lmt': '0',
        'klt': '101',
        'secid': secid,
        'fields1': 'f1,f2,f3,f7',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
    }

    logger.info(f'请求东方财富个股资金流向: {symbol} (market={market}, secid={secid})')

    try:
        resp = _http_get_em(url, params=params)
        data = resp.json()

        klines = data.get('data', {}).get('klines', [])
        if not klines:
            logger.warning(f'{symbol} 东方财富个股资金流向返回空数据')
            return None

        # 解析逗号分隔的数据行
        # 格式: 日期,主力净额,小单净额,中单净额,大单净额,超大单净额,主力占比,小单占比,中单占比,大单占比,超大单占比,收盘价,涨跌幅,-,-
        results = []
        for line in klines:
            parts = line.split(',')
            if len(parts) < 13:
                continue
            row = {
                '日期': parts[0],
                # 019N M-2: 改用 _safe_num（None 语义），'-'/空/NaN 不再抛 ValueError 炸整批
                '主力净流入-净额': _safe_num(parts[1]),
                '小单净流入-净额': _safe_num(parts[2]),
                '中单净流入-净额': _safe_num(parts[3]),
                '大单净流入-净额': _safe_num(parts[4]),
                '超大单净流入-净额': _safe_num(parts[5]),
                '主力净流入-净占比': _safe_num(parts[6]),
                '小单净流入-净占比': _safe_num(parts[7]),
                '中单净流入-净占比': _safe_num(parts[8]),
                '大单净流入-净占比': _safe_num(parts[9]),
                '超大单净流入-净占比': _safe_num(parts[10]),
                '收盘价': _safe_num(parts[11]),
                '涨跌幅': _safe_num(parts[12]),
            }
            results.append(row)

        logger.info(f'{symbol} 获取到 {len(results)} 天资金流向历史数据')
        return results
    except Exception as e:
        logger.error(f'{symbol} 东方财富个股资金流向异常: {e}')
        return None


def _get_em_secid(symbol, market):
    """获取东方财富的 secid 格式"""
    if market == 'hk_stock':
        hk_code = _normalize_hk_symbol(symbol)
        return f'116.{hk_code}'
    elif market == 'a_stock':
        if symbol.startswith('6'):
            return f'1.{symbol}'
        else:
            return f'0.{symbol}'
    return f'0.{symbol}'


def _fetch_capital_flow_em(symbol, market):
    """从东方财富 push2 接口获取资金流向数据（019Z：熔断冷却期内直接跳过）。"""
    if _em_banned():
        logger.warning(f'{symbol} 东财处于熔断冷却期，跳过 push2 资金流向直连（走备用源）')
        return None

    secid = _get_em_secid(symbol, market)
    url = 'https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get'
    params = {
        'secid': secid,
        'lmt': 10,  # 最近10天
        'klt': '101',
        'fields1': 'f1,f2,f3,f7',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
    }
    logger.info(f'请求资金流向: secid={secid}, url={url}')
    resp = _http_get_em(url, params=params)
    data = resp.json()

    klines = data.get('data', {}).get('klines', [])
    logger.info(f'资金流向响应: 获取到 {len(klines)} 条数据')
    if not klines:
        logger.warning(f'资金流向数据为空, 完整响应: {str(data)[:300]}')
    return klines
