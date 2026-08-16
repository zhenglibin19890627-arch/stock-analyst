"""
数据采集模块 —— 核心模块
负责获取 A股和港股的全部数据（基本面、技术面、消息面、资金面）。

数据源说明：
- K线数据：腾讯财经接口（稳定可用）
- 基本面数据：新浪财经接口（通过 akshare 调用）
- 资金面数据：东方财富 push2 接口（稳定可用）
- 消息面数据：公告数据有限，标注缺失

设计原则：
1. 每个采集函数都有容错处理，失败不崩溃，返回状态标记
2. 获取到的数据自动存入数据库
3. 数据缺失时记录原因，供报告生成时提示用户
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


# ============================================================
# 解决系统代理干扰问题
# 有些用户电脑装了 Clash/V2Ray 等代理软件，默认会读取系统代理配置。
# 对于腾讯/新浪接口：直连即可，需禁用代理。
# 对于东方财富 push2 接口：直连可能被封锁，需要通过系统代理访问。
# 因此采用分策略处理：akshare 内部统一禁用代理（直连），
# 东方财富资金流向单独使用智能回退逻辑（先直连再走代理）。
# ============================================================
import random as _random
import urllib.request as _urlreq

import requests

# ============================================================
# UA池（≥20个真实浏览器UA，随机选取，降低被风控概率）
# ============================================================
_UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:119.0) Gecko/20100101 Firefox/119.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 OPR/104.0.0.0',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; WOW64; rv:118.0) Gecko/20100101 Firefox/118.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:118.0) Gecko/20100101 Firefox/118.0',
]


def _random_ua():
    """从UA池中随机选取一个User-Agent"""
    return _random.choice(_UA_POOL)


_original_request = requests.Session.request


def _no_proxy_request(self, *args, **kwargs):
    """
    绕过系统代理，直接连接（适用于 akshare 内部的腾讯/新浪请求）。
    东方财富域名例外：不干预其连接方式（部分接口需要原始行为）。
    """
    # 提取 URL（args[1] 或 kwargs['url']）
    url = ''
    if len(args) > 1:
        url = str(args[1])
    elif 'url' in kwargs:
        url = str(kwargs['url'])

    # 东方财富域名：使用原始请求行为（不强制禁用代理）
    if 'eastmoney.com' in url:
        return _original_request(self, *args, **kwargs)

    # 其他域名（腾讯/新浪等）：强制禁用代理，直连
    self.trust_env = False
    if 'proxies' not in kwargs or kwargs['proxies'] is None:
        kwargs['proxies'] = {'http': None, 'https': None}
    return _original_request(self, *args, **kwargs)


requests.Session.request = _no_proxy_request


# ============================================================
# 代理健康检查：连续失败2次的代理自动禁用30分钟
# ============================================================
class ProxyHealthTracker:
    """跟踪代理健康状态，连续失败后自动禁用"""

    def __init__(self):
        self._fail_count = 0
        self._disabled_until = None

    def is_available(self):
        """代理是否可用（未被禁用或禁用已过期）"""
        if self._disabled_until is None:
            return True
        if datetime.now(_CN_TZ).timestamp() > self._disabled_until:
            self._disabled_until = None
            self._fail_count = 0
            logger.info('代理已恢复可用（禁用期结束）')
            return True
        return False

    def record_failure(self):
        """记录一次失败"""
        self._fail_count += 1
        if self._fail_count >= 2:
            self._disabled_until = datetime.now(_CN_TZ).timestamp() + 1800  # 30分钟
            logger.warning(f'代理连续失败{self._fail_count}次，自动禁用30分钟')

    def record_success(self):
        """记录一次成功"""
        self._fail_count = 0
        self._disabled_until = None


_proxy_health = ProxyHealthTracker()

# 现在才导入 akshare（它内部使用的 requests 已经被 patch 了）
import akshare as ak

# 添加项目根目录到路径，确保能导入 config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
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
logger = logging.getLogger(__name__)


# ============================================================
# 工具函数
# ============================================================


def _http_get(url, params=None, headers=None, timeout=15):
    """统一的 HTTP GET 请求（直连模式，用于腾讯/新浪接口），使用随机UA"""
    session = requests.Session()
    session.trust_env = False
    session.headers.update({'User-Agent': _random_ua(), 'Referer': 'https://finance.qq.com'})
    if headers:
        session.headers.update(headers)
    resp = session.get(url, params=params, timeout=timeout, proxies={'http': None, 'https': None})
    resp.raise_for_status()
    return resp


# ============================================================
# 019X T1：东方财富失败重试退避参数（仅 _http_get_em 内部使用）
# 019W 诊断：WAF 窗口式丢弃持续 2~4 分钟，原 1.5~3.5s 短间隔重试全撞窗口内；
# 改为 30s→60s→60s 轮间退避（各加 ±15% 随机抖动），轮数提至 4。
# 注意：不得修改全局 MAX_RETRIES=3（同时服务新浪/腾讯源的 @retry 装饰器）。
# ============================================================
_EM_RETRY_BACKOFFS = (30.0, 60.0, 60.0)  # 轮间等待序列（秒），attempt 0/1/2 → 30/60/60
_EM_RETRY_JITTER = 0.15                   # 每轮等待 ±15% 随机抖动
_EM_RETRY_ROUNDS = 4                      # 本函数轮数（仅内部，不影响其他数据源）


def _http_get_em(url, params=None, timeout=15, max_retries=None):
    """
    东方财富专用请求：智能回退 + 多轮重试 + UA池 + 随机延迟。
    019X T3：由 EM_USE_PROXY 开关控制代理路径——默认 False 只走直连，
    代理分支与 _proxy_health 健康检查保留代码、开关跳过（留回滚能力）。
    019X T1：轮间失败退避 30s→60s→60s（各 ±15% 抖动），轮数 4（仅本函数）。
    每轮1~2次尝试（代理开启时 proxy+direct），共_EM_RETRY_ROUNDS轮。
    """
    system_proxies = _urlreq.getproxies()
    last_error = None
    global _EM_LAST_REQUEST_TS  # 019Z：全局最小请求间隔
    # 代理健康检查：仅当开关开启且存在系统代理时才检查（EM_USE_PROXY=False 时零触碰）
    proxy_available = bool(EM_USE_PROXY) and bool(system_proxies) and _proxy_health.is_available()
    rounds = max_retries if max_retries else _EM_RETRY_ROUNDS
    # connect_timeout=5, read_timeout=10
    timeout_tuple = (5, 10) if timeout == 15 else timeout

    for attempt in range(rounds):
        # 019Z：第 3 轮起尝试 push2/push2his 编号子域轮换（不同边缘节点可绕部分 WAF 拦截）
        req_url = url
        if attempt >= 2 and 'push2' in url and 'eastmoney.com' in url:
            req_url = _rotate_em_host(url)
            if req_url != url:
                logger.info(f'东方财富第{attempt + 1}轮尝试编号子域: {req_url.split("/")[2]}')

        order = []
        if proxy_available:
            order = [('proxy', True), ('direct', False)]
        else:
            order = [('direct', False)]

        for label, use_proxy in order:
            try:
                # 请求间随机延迟 1.5~3.5秒
                if attempt > 0 or label == 'direct':
                    _delay = _random.uniform(1.5, 3.5)
                    time.sleep(_delay)
                # 019Z：东财全局最小请求间隔（社区阈值 <5 次/秒）
                _wait = _EM_MIN_INTERVAL_SECONDS - (time.time() - _EM_LAST_REQUEST_TS)
                if _wait > 0:
                    time.sleep(_wait)
                session = requests.Session()
                session.trust_env = use_proxy
                session.headers.update(
                    {
                        'User-Agent': _random_ua(),
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'zh-CN,zh;q=0.8',
                    }
                )
                try:
                    if use_proxy:
                        resp = session.get(
                            req_url, params=params, timeout=timeout_tuple, proxies=system_proxies
                        )
                    else:
                        resp = session.get(
                            req_url,
                            params=params,
                            timeout=timeout_tuple,
                            proxies={'http': None, 'https': None},
                        )
                finally:
                    _EM_LAST_REQUEST_TS = time.time()
                resp.raise_for_status()
                logger.info(f'东方财富{label}成功（第{attempt + 1}轮）')
                if use_proxy:
                    _proxy_health.record_success()
                return resp
            except Exception as e:
                last_error = e
                if use_proxy:
                    _proxy_health.record_failure()
                logger.info(f'东方财富{label}失败: ' + str(e)[:80])

        if attempt < rounds - 1:
            # 019X T1：轮间退避 30s→60s→60s（各 ±15% 抖动），总窗口约 2~3 分钟
            base_wait = (
                _EM_RETRY_BACKOFFS[attempt]
                if attempt < len(_EM_RETRY_BACKOFFS)
                else _EM_RETRY_BACKOFFS[-1]
            )
            wait = base_wait * _random.uniform(1 - _EM_RETRY_JITTER, 1 + _EM_RETRY_JITTER)
            logger.info(f'东方财富第{attempt + 1}轮失败，等待{wait:.1f}秒后重试...')
            time.sleep(wait)

    if proxy_available:
        raise ConnectionError(f'东方财富接口无法访问（直连和代理均失败，重试{rounds}轮）: {last_error}')
    raise ConnectionError(f'东方财富接口无法访问（直连重试{rounds}轮均失败，EM_USE_PROXY=False 未走代理）: {last_error}')


def retry(func, max_retries=MAX_RETRIES, delay=1):
    """重试装饰器：网络请求失败时自动重试。"""

    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                logger.warning(f'第{attempt + 1}次尝试失败: {e}')
                if attempt < max_retries - 1:
                    time.sleep(delay)
        logger.error(f'重试{max_retries}次后仍失败: {last_error}')
        return None

    return wrapper


def get_stock_id(symbol, market):
    """根据股票代码和市场，从数据库获取 stock_id"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM stocks WHERE symbol = ? AND market = ?', (symbol, market))
    row = cursor.fetchone()
    conn.close()
    return row['id'] if row else None


def save_data_status(stock_id, dimension, status, message=''):
    """记录数据采集状态到数据库（使用北京时间时间戳）。
    011优化：同维度同日只保留最新一条（先删后插），避免 data_status 无限增长。
    """
    ts = now_cn()
    today_prefix = ts[:10]  # YYYY-MM-DD
    conn = get_connection()
    cursor = conn.cursor()
    # 011：删除同维度同日的旧记录，仅保留最新一条
    cursor.execute(
        """DELETE FROM data_status
           WHERE stock_id = ? AND dimension = ? AND fetched_at LIKE ?""",
        (stock_id, dimension, today_prefix + '%'),
    )
    cursor.execute(
        """
        INSERT INTO data_status (stock_id, dimension, status, message, fetched_at)
        VALUES (?, ?, ?, ?, ?)
    """,
        (stock_id, dimension, status, message, ts),
    )
    conn.commit()
    conn.close()


def _log_error_to_db(
    stock_id, module, error_type, error_message, dimension=None, traceback_str=None
):
    """012-C: 统一写入 error_logs 表"""
    try:
        conn = get_connection()
        conn.execute(
            'INSERT INTO error_logs (stock_id, module, error_type, error_message, dimension, traceback) VALUES (?,?,?,?,?,?)',
            (
                stock_id,
                module,
                error_type,
                error_message,
                dimension,
                traceback_str[:2000] if traceback_str else None,
            ),  # 截断至2000字符
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # 写日志失败不阻塞业务


def _normalize_hk_symbol(symbol):
    """
    将港股代码统一转换为5位数字格式。
    'HK3690' → '03690', '00700' → '00700', '3690' → '03690'
    """
    s = symbol.strip().upper()
    s = s.removeprefix('HK')
    # 去除可能的.HK后缀
    s = s.removesuffix('.HK')
    # 补全为5位
    s = s.zfill(5)
    return s


def _get_tencent_prefix(symbol, market):
    """根据股票代码和市场，返回腾讯接口需要的前缀和完整代码"""
    if market == 'hk_stock':
        # 港股代码统一为5位数字，腾讯接口格式: hk03690
        hk_code = _normalize_hk_symbol(symbol)
        return 'hk', hk_code
    elif market == 'a_stock':
        # A股：6开头=上海(sh)，0/3开头=深圳(sz)
        if symbol.startswith('6'):
            return 'sh', symbol
        else:
            return 'sz', symbol
    return '', symbol


@retry
def _fetch_valuation_tencent(symbol, market):
    """
    从腾讯实时行情接口获取 PE/PB 估值数据。
    注意：A股和港股的字段索引不同！
      A股: [39]=PE(TTM), [46]=PB
      港股: [39]=PE(TTM), [43]=PB  (港股[46]是英文股票名而非PB)
    返回: (pe, pb)
    """
    prefix, normalized_code = _get_tencent_prefix(symbol, market)
    url = 'https://qt.gtimg.cn/q=' + prefix + normalized_code

    resp = _http_get(url)
    text = resp.text
    parts = text.split('~')

    if len(parts) < 47:
        raise ValueError('腾讯行情数据字段不足: ' + str(len(parts)))

    # PE 字段 A股和港股都在 [39]
    pe_str = parts[39].strip().strip('"')

    # PB 字段：A股在 [46]，港股在 [43]
    if market == 'hk_stock':
        pb_str = parts[43].strip().strip('"') if len(parts) > 43 else ''
    else:
        pb_str = parts[46].strip().strip(';').strip('"')

    pe = None
    pb = None
    try:
        pe = float(pe_str) if pe_str else None
    except ValueError:
        pass
    try:
        pb = float(pb_str) if pb_str else None
    except ValueError:
        pass

    logger.info(f'腾讯估值 {symbol}: PE={pe}, PB={pb} (market={market})')
    return pe, pb


# ============================================================
# 019Y T1：mootdx 行情适配层
# （K线/实时行情降级备用源 + 五档盘口增量数据维度）
#
# 大白话说明：
# - mootdx 走通达信 TCP socket 协议，不经过 requests/httpx，
#   因此不受本项目 requests.Session.request 全局 patch 影响（天然隔离）。
# - mootdx 仅支持 A股（沪市 6 开头、深市 0/3 开头，不带 sh/sz 前缀）。
#   港股 K线/盘口仍走现有源（腾讯/akshare），估值港股仍走 akshare。
# - 首次初始化会挑选最快的行情服务器（约 5 秒），之后全局复用（单例缓存）。
# ============================================================
import threading as _threading_019y

_MOOTDX_CLIENT = None  # 全局单例客户端（首次初始化约 0.5 秒，之后复用）
_MOOTDX_INIT_DONE = False  # 首次初始化是否已尝试过（成功或失败后不再重复扫描服务器）
_MOOTDX_LOCK = _threading_019y.Lock()

# 019Y M-2：备用服务器池（2026-08-11 实测可用，返回完整行情/K线）。
# 通达信服务器存在区域性故障（部分服务器 TCP 连接成功但返回空数据），
# 因此先逐个健康检查备用池，全部失败才走 bestip 全网扫描（约 70 秒）。
_MOOTDX_FALLBACK_SERVERS = [
    ('115.238.56.198', 7709),  # 浙江电信（实测可用）
    ('115.238.90.165', 7709),  # 浙江电信（实测可用）
    ('218.75.126.9', 7709),    # 浙江电信（实测可用）
    ('180.153.18.170', 7709),  # 上海电信（实测可用）
]


def _mootdx_verify(client):
    """019Y：mootdx 客户端健康检查——发一次实时行情请求，判断服务器是否真的返回数据。
    有些服务器 TCP 连接成功但返回空数据（实测 110.41.147.114 / 218.6.170.47），必须实测验证。
    """
    try:
        df = client.quotes(symbol='000001')
        return df is not None and len(df) > 0
    except Exception:
        return False


def _mootdx_client():
    """获取 mootdx 全局单例客户端（线程安全）。
    初始化顺序（019Y M-2）：
    1) 备用服务器池逐个健康检查（快，实测约 0.1 秒/个）；
    2) 全部失败才走 bestip 全网扫描（慢，约 70 秒，且可能选中故障服务器）；
    首次初始化后全局复用（单例缓存），避免每只股票都重新初始化。
    """
    global _MOOTDX_CLIENT, _MOOTDX_INIT_DONE
    if _MOOTDX_CLIENT is not None:
        return _MOOTDX_CLIENT
    if _MOOTDX_INIT_DONE:
        return None  # 首次初始化已失败（全部服务器不可用），不再重复扫描
    with _MOOTDX_LOCK:
        if _MOOTDX_CLIENT is not None or _MOOTDX_INIT_DONE:
            return _MOOTDX_CLIENT
        from mootdx.quotes import Quotes
        try:
            # 1) 备用服务器池（已知可用，优先）
            for host, port in _MOOTDX_FALLBACK_SERVERS:
                try:
                    logger.info(f'[mootdx] 尝试备用服务器 {host}:{port}（健康检查中）...')
                    client = Quotes.factory(market='std', server=(host, port), timeout=10, heartbeat=True)
                    if _mootdx_verify(client):
                        _MOOTDX_CLIENT = client
                        logger.info(f'[mootdx] 备用服务器 {host}:{port} 健康检查通过，全局单例缓存')
                        return client
                    logger.warning(f'[mootdx] 备用服务器 {host}:{port} 返回空数据，换下一台')
                    try:
                        client.close()
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f'[mootdx] 备用服务器 {host}:{port} 连接失败: {e}')
            # 2) bestip 全网扫描（兜底，首次约 70 秒）
            logger.info('[mootdx] 备用服务器池全部不可用，走 bestip 全网扫描（约需 1 分钟）...')
            client = Quotes.factory(market='std', bestip=True, timeout=15, heartbeat=True)
            if _mootdx_verify(client):
                _MOOTDX_CLIENT = client
                logger.info('[mootdx] bestip 服务器健康检查通过，全局单例缓存')
                return client
            logger.warning('[mootdx] bestip 选中的服务器返回空数据（服务器区域性故障）')
            try:
                client.close()
            except Exception:
                pass
        except Exception as e:
            logger.error(f'[mootdx] 客户端初始化异常: {e}')
        _MOOTDX_INIT_DONE = True
        logger.error('[mootdx] 全部服务器均不可用，本次运行不再重试（K线/盘口降级将标记失败）')
        return None


def _mootdx_symbol(symbol, market):
    """A股代码 → mootdx 代码（不带前缀）。港股/非A股返回 None（mootdx 支持有限）。"""
    if market != 'a_stock':
        return None
    if symbol.startswith(('6', '0', '3')):
        return symbol
    return None


def _fetch_kline_mootdx(symbol):
    """019Y T1：mootdx 日K线（frequency=9 表示日线）。
    返回与腾讯接口同格式的 DataFrame（日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅），
    供 fetch_kline 统一入库。失败抛异常由调用方处理。
    """
    client = _mootdx_client()
    if client is None:
        raise ValueError('mootdx 客户端不可用（初始化失败）')
    bars = client.bars(symbol=symbol, frequency=9, offset=KLINE_DAYS)
    if bars is None or len(bars) == 0:
        raise ValueError('mootdx K线返回空数据')
    rows = []
    for _, r in bars.iterrows():
        dt = str(r.get('datetime', ''))
        try:
            rows.append(
                {
                    '日期': dt.split(' ')[0] if ' ' in dt else dt[:10],
                    '开盘': float(r.get('open', 0) or 0),
                    '收盘': float(r.get('close', 0) or 0),
                    '最高': float(r.get('high', 0) or 0),
                    '最低': float(r.get('low', 0) or 0),
                    '成交量': float(r.get('vol', 0) or 0),
                    '成交额': float(r.get('amount', 0) or 0),
                }
            )
        except (ValueError, TypeError):
            continue
    out = pd.DataFrame(rows)
    if not out.empty:
        out['涨跌幅'] = out['收盘'].pct_change() * 100
        out['涨跌幅'] = out['涨跌幅'].fillna(0)
    return out


def backfill_kline_history_mootdx(stock_id, symbol, market, min_bars=600):
    """020R-48 二期：A股用 mootdx 前复权全量历史补齐日线历史（仅补缺口，不覆盖已有行）。

    用途：月线 MACD（需 26 个月）等多周期指标需要 2 年以上日线；腾讯接口深度受限。
    raw_kline 有 UNIQUE(stock_id, trade_date)，INSERT OR IGNORE 保证已有行（腾讯主源）不被覆盖。
    返回 (status, message)。
    """
    if market != 'a_stock':
        return 'skipped', '仅A股支持 mootdx 历史补采'
    code = _mootdx_symbol(symbol, market)
    if not code:
        return 'skipped', 'mootdx 不支持该代码'

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT MIN(trade_date) AS m, COUNT(*) AS c FROM raw_kline WHERE stock_id = ?',
        (stock_id,),
    )
    row = cur.fetchone()
    if row and row['c'] and int(row['c']) >= min_bars:
        conn.close()
        return 'skipped', f'日线已有{row["c"]}根(≥{min_bars})，无需补采'

    client = _mootdx_client()
    if client is None:
        conn.close()
        return 'failed', 'mootdx 客户端不可用'

    try:
        try:
            bars = client.bars(symbol=code, frequency=9, offset=800, adjust='qfq')
        except Exception:  # noqa: BLE001 —— adjust 参数不兼容时回退
            bars = client.bars(symbol=code, frequency=9, offset=800)
        if bars is None or len(bars) == 0:
            conn.close()
            return 'failed', 'mootdx 全量历史返回空数据'

        inserted = 0
        for _, r in bars.iterrows():
            dt = str(r.get('datetime', ''))
            trade_date = dt.split(' ')[0] if ' ' in dt else dt[:10]
            try:
                cur.execute(
                    'INSERT OR IGNORE INTO raw_kline '
                    '(stock_id, trade_date, open, close, high, low, volume, amount, pct_change, data_source) '
                    "VALUES (?,?,?,?,?,?,?,?,?,'mootdx')",
                    (
                        stock_id, trade_date,
                        float(r.get('open', 0) or 0), float(r.get('close', 0) or 0),
                        float(r.get('high', 0) or 0), float(r.get('low', 0) or 0),
                        float(r.get('vol', 0) or 0), float(r.get('amount', 0) or 0),
                        None,
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
            except (ValueError, TypeError):
                continue
        conn.commit()
        cur.execute('SELECT COUNT(*) AS c FROM raw_kline WHERE stock_id = ?', (stock_id,))
        total = cur.fetchone()['c']
        conn.close()
        logger.info(f'[020R-48 历史补采] {symbol}: 新增{inserted}根，共{total}根日线')
        return 'success', f'新增{inserted}根，共{total}根日线'
    except Exception as e:  # noqa: BLE001
        conn.close()
        logger.warning(f'[020R-48 历史补采] {symbol} 失败: {e}')
        return 'failed', f'历史补采失败: {e}'


def _fetch_realtime_quote_mootdx(symbol):
    """019Y T1：mootdx 实时行情（含五档买卖盘）。
    返回 dict：{'price','pct_change','bid1_price'..'bid5_price','bid1_vol'..'bid5_vol',
               'ask1_price'..'ask5_price','ask1_vol'..'ask5_vol','quote_time'}
    失败返回 None（不抛异常，不阻塞主流程）。
    """
    try:
        client = _mootdx_client()
        if client is None:
            logger.warning(f'[mootdx] {symbol} 客户端不可用，跳过实时行情')
            return None
        df = client.quotes(symbol=symbol)
        if df is None or len(df) == 0:
            logger.warning(f'[mootdx] {symbol} 实时行情返回空数据')
            return None
        row = df.iloc[0]
        price = _safe_num(row.get('price'))
        last_close = _safe_num(row.get('last_close'))
        if price is None or price <= 0:
            logger.warning(f'[mootdx] {symbol} 实时行情价格异常: price={price}')
            return None
        pct = round((price - last_close) / last_close * 100, 2) if last_close else None
        quote = {'price': price, 'pct_change': pct}
        for lvl in range(1, 6):
            quote[f'bid{lvl}_price'] = _safe_num(row.get(f'bid{lvl}'))
            quote[f'bid{lvl}_vol'] = _safe_num(row.get(f'bid_vol{lvl}'))
            quote[f'ask{lvl}_price'] = _safe_num(row.get(f'ask{lvl}'))
            quote[f'ask{lvl}_vol'] = _safe_num(row.get(f'ask_vol{lvl}'))
        quote['quote_time'] = str(row.get('servertime', ''))[:8]
        return quote
    except Exception as e:
        logger.warning(f'[mootdx] {symbol} 实时行情获取失败: {e}')
        return None


def get_realtime_quote_mootdx(symbol):
    """019Y T1：对外只读接口——供 app.py 实时价格刷新降级使用（只取价格，不写库）。"""
    q = _fetch_realtime_quote_mootdx(symbol)
    if q is None or q.get('price') is None:
        return None
    return {'price': q['price'], 'pct_change': q.get('pct_change')}


def _ensure_kline_source_column():
    """019Y：确保 raw_kline.data_source 列存在（幂等，兼容未迁移的旧库）"""
    try:
        conn = get_connection()
        conn.execute('ALTER TABLE raw_kline ADD COLUMN data_source TEXT DEFAULT NULL')
        conn.commit()
        conn.close()
    except Exception:
        pass  # 列已存在


def fetch_orderbook(symbol, market, force_full=False):
    """019Y T1：采集五档盘口快照（mootdx 实时行情）。
    每只股票每天仅保留最新一条快照（UNIQUE(stock_id, trade_date)，重复采集覆盖当日）。
    仅支持 A股（mootdx 港股支持有限）。失败不阻塞主流程。
    021C：非交易日（周末）跳过——mootdx 周末返回上一交易日快照，
    若盖当日日期会形成周末脏行（2026-08-15 实测 23 行），与资金面 020L 同原则。
    返回: (状态, 消息)
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 021C：周末守卫（与 fetch_capital_flow 020L 同原则）
    if datetime.now(_CN_TZ).weekday() >= 5:
        save_data_status(stock_id, 'orderbook', 'skipped', '非交易日跳过（mootdx 盘口）')
        logger.info(f'[{symbol}] 非交易日（周末），跳过五档盘口采集')
        return 'skipped', '非交易日跳过（mootdx 盘口）'

    mootdx_code = _mootdx_symbol(symbol, market)
    if not mootdx_code:
        save_data_status(stock_id, 'orderbook', 'skipped', 'mootdx暂不支持港股盘口')
        return 'skipped', 'mootdx暂不支持港股盘口'

    try:
        quote = _fetch_realtime_quote_mootdx(mootdx_code)
        if not quote or quote.get('price') is None:
            save_data_status(stock_id, 'orderbook', 'failed', 'mootdx实时行情获取失败')
            return 'failed', 'mootdx实时行情获取失败'

        today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO stock_orderbook
            (stock_id, trade_date, quote_time, latest_price, pct_change,
             bid1_price, bid1_vol, bid2_price, bid2_vol, bid3_price, bid3_vol,
             bid4_price, bid4_vol, bid5_price, bid5_vol,
             ask1_price, ask1_vol, ask2_price, ask2_vol, ask3_price, ask3_vol,
             ask4_price, ask4_vol, ask5_price, ask5_vol, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                stock_id,
                today_str,
                quote.get('quote_time'),
                quote['price'],
                quote.get('pct_change'),
                quote.get('bid1_price'),
                quote.get('bid1_vol'),
                quote.get('bid2_price'),
                quote.get('bid2_vol'),
                quote.get('bid3_price'),
                quote.get('bid3_vol'),
                quote.get('bid4_price'),
                quote.get('bid4_vol'),
                quote.get('bid5_price'),
                quote.get('bid5_vol'),
                quote.get('ask1_price'),
                quote.get('ask1_vol'),
                quote.get('ask2_price'),
                quote.get('ask2_vol'),
                quote.get('ask3_price'),
                quote.get('ask3_vol'),
                quote.get('ask4_price'),
                quote.get('ask4_vol'),
                quote.get('ask5_price'),
                quote.get('ask5_vol'),
                'mootdx',
            ),
        )
        conn.commit()
        conn.close()
        msg = f'五档盘口已入库（mootdx，快照{quote.get("quote_time")}，最新价{quote["price"]}）'
        save_data_status(stock_id, 'orderbook', 'success', msg)
        logger.info(f'[{symbol}] {msg}')
        return 'success', msg
    except Exception as e:
        save_data_status(stock_id, 'orderbook', 'failed', str(e))
        logger.warning(f'[{symbol}] 五档盘口采集失败: {e}')
        return 'failed', str(e)


# ============================================================
# K线数据采集（A股 + 港股统一使用腾讯接口）
# ============================================================


@retry
def _fetch_kline_tencent(symbol, market):
    """
    从腾讯财经接口获取日K线数据（前复权）。
    腾讯接口返回格式：[日期, 开盘, 收盘, 最高, 最低, 成交量]
    """
    prefix, normalized_code = _get_tencent_prefix(symbol, market)
    tencent_code = f'{prefix}{normalized_code}'

    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    params = {'param': f'{tencent_code},day,,,{KLINE_DAYS},qfq'}

    resp = _http_get(url, params=params)
    data = resp.json()

    # 解析数据
    stock_data = data.get('data', {}).get(tencent_code, {})
    kline_list = stock_data.get('qfqday') or stock_data.get('day') or []

    if not kline_list:
        return pd.DataFrame()

    # 转为 DataFrame
    rows = []
    for item in kline_list:
        # A股格式: [date, open, close, high, low, volume]
        # 港股格式: [date, open, close, high, low, volume, {extra}]
        if len(item) >= 6:
            rows.append(
                {
                    '日期': item[0],
                    '开盘': float(item[1]),
                    '收盘': float(item[2]),
                    '最高': float(item[3]),
                    '最低': float(item[4]),
                    '成交量': float(item[5]),
                }
            )

    df = pd.DataFrame(rows)
    # 计算涨跌幅
    if not df.empty:
        df['涨跌幅'] = df['收盘'].pct_change() * 100
        df['涨跌幅'] = df['涨跌幅'].fillna(0)

    return df


def fetch_kline(symbol, market, force_full=False):
    """
    采集K线数据并存入数据库。A股和港股统一使用此函数。
    011增量优化：同日跳过（last_date >= 今日 → 跳过），全量覆盖确保复权因子一致。
    返回: (状态字符串, 消息)
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 011增量：同日跳过检查
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                'SELECT MAX(trade_date) as last_date FROM raw_kline WHERE stock_id = ?', (stock_id,)
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['last_date']:
                last_date = str(row['last_date'])[:10]
                today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                if last_date >= today_str:
                    skip_msg = f'同日跳过(K线已有{last_date}数据)'
                    save_data_status(stock_id, 'kline', 'success', skip_msg)
                    logger.info(f'[{symbol}] {skip_msg}')
                    return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[{symbol}] K线增量检查异常(降级为全量): {e}')

    # 019Y T1：K线降级链路 —— 腾讯野接口（主源）→ mootdx（备用源，仅A股）→ 标记失败
    # 主源失败时自动降级，数据来源在日志与数据库（raw_kline.data_source）中标注 mootdx
    kline_source = None  # None=腾讯主源；'mootdx'=降级备用源
    try:
        df = _fetch_kline_tencent(symbol, market)
        if df is None or df.empty:
            raise ValueError('腾讯接口返回空数据')
    except Exception as e_tencent:
        logger.warning(f'[{symbol}] 腾讯K线获取失败（尝试mootdx降级）: {e_tencent}')
        df = None
        mootdx_code = _mootdx_symbol(symbol, market)
        if mootdx_code:
            try:
                df = _fetch_kline_mootdx(mootdx_code)
                kline_source = 'mootdx'
                logger.info(f'[{symbol}] mootdx K线降级成功（数据来源标注 mootdx）')
            except Exception as e_mootdx:
                logger.error(f'[{symbol}] mootdx K线降级也失败: {e_mootdx}')
        if df is None or df.empty:
            save_data_status(stock_id, 'kline', 'failed', '腾讯接口与mootdx降级均失败')
            return 'failed', '腾讯接口与mootdx降级均失败'

    try:
        _ensure_kline_source_column()
        conn = get_connection()
        cursor = conn.cursor()

        saved_count = 0
        for _, row in df.iterrows():
            trade_date = str(row['日期']).split(' ')[0]
            try:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_kline
                    (stock_id, trade_date, open, close, high, low, volume, amount, turnover, pct_change, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        trade_date,
                        float(row.get('开盘', 0) or 0),
                        float(row.get('收盘', 0) or 0),
                        float(row.get('最高', 0) or 0),
                        float(row.get('最低', 0) or 0),
                        float(row.get('成交量', 0) or 0),
                        float(row.get('成交额', 0) or 0),  # 腾讯接口不提供成交额（留空）；mootdx 有
                        0,  # 换手率（同上）
                        float(row.get('涨跌幅', 0) or 0),
                        kline_source,
                    ),
                )
                saved_count += 1
            except Exception:
                continue

        conn.commit()
        conn.close()

        src_tag = '（数据来源: mootdx 降级）' if kline_source else ''
        save_data_status(stock_id, 'kline', 'success', f'成功获取{saved_count}条K线数据{src_tag}')
        market_name = 'A股' if market == 'a_stock' else '港股'
        logger.info(f'[{market_name} {symbol}] K线数据采集成功，共{saved_count}条{src_tag}')
        return 'success', f'获取{saved_count}条K线数据{src_tag}'

    except Exception as e:
        save_data_status(stock_id, 'kline', 'failed', str(e))
        logger.error(f'[{symbol}] K线数据采集失败: {e}')
        return 'failed', str(e)


# ============================================================
# A股 —— 基本面数据
# ============================================================

# ============================================================
# 019P：新浪关键指标摘要（stock_financial_abstract）主源解析
# 结构（架构师实测 M-2，无需转置）：行=指标（80），列=['选项','指标']+报告期列（最新在前）
# ============================================================
_FUND_ABSTRACT_TIMEOUT = 30  # 019P：abstract 调用超时阈值（秒），实测单次约 1s

# 019P R-1：abstract 同名指标去重优先级（选项组：常用指标优先 + 取第一行）
_ABSTRACT_GROUP_PRIORITY = [
    '常用指标',
    '每股指标',
    '盈利能力',
    '成长能力',
    '收益质量',
    '财务风险',
    '营运能力',
]

# 019P M-2：abstract 指标行名 → raw_fundamental 列 映射表（架构师实测行名）
_ABSTRACT_FIELD_MAP = [
    ('毛利率', 'gross_margin'),
    ('销售净利率', 'net_margin'),
    ('营业总收入增长率', 'revenue_growth'),
    ('归属母公司净利润增长率', 'profit_growth'),
    ('净资产收益率(ROE)', 'roe'),
    ('资产负债率', 'debt_ratio'),
    ('流动比率', 'current_ratio'),
    ('速动比率', 'quick_ratio'),
    ('经营活动净现金/归属母公司的净利润', 'ocf_to_net_profit'),
]


def _call_ak_with_timeout(fn, label, timeout=_FUND_ABSTRACT_TIMEOUT):
    """019P P3（必需）：daemon 线程 join(timeout) 包装 akshare 基本面接口调用。
    019I 模式同型，自建于基本面区域（THS 的 _call_with_timeout 为函数内闭包，不可复用）。
    超时返回 (None, True)；正常返回 (result, False)。abstract/analysis_indicator 严禁裸调用。
    """
    import threading as _threading_019p

    box = {}
    t = _threading_019p.Thread(target=lambda: box.update(r=fn()), daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        logger.warning(f'[019P] {label} 超时({timeout}s)，按失败处理')
        return None, True
    return box.get('r'), False


def _fetch_a_fundamental_sina(symbol):
    """019P：主源切换——akshare 新浪关键指标摘要 stock_financial_abstract（M-2/A-1）。
    返回原始 DataFrame（行=指标，列=['选项','指标']+报告期列最新在前）。
    P3 超时保护；异常/超时向上抛出，由 fetch_a_fundamental 降级现接口（P2）。
    注：不挂 @retry——abstract 失败即降级（P2），避免批量场景超时重试累积（最坏 3×30s 超批次上限）。
    """
    df, timed_out = _call_ak_with_timeout(
        lambda: ak.stock_financial_abstract(symbol=symbol), f'A股 {symbol} abstract'
    )
    if timed_out:
        raise TimeoutError(f'stock_financial_abstract 超时(>{_FUND_ABSTRACT_TIMEOUT}s)')
    return df


@retry
def _fetch_a_fundamental_sina_indicator(symbol):
    """019P P2：降级层——现接口 stock_financial_analysis_indicator（保留原路径）。
    P3 超时保护；异常/超时向上抛出由 fetch_a_fundamental 处理。
    """
    df, timed_out = _call_ak_with_timeout(
        lambda: ak.stock_financial_analysis_indicator(symbol=symbol, start_year='2020'),
        f'A股 {symbol} analysis_indicator',
    )
    if timed_out:
        raise TimeoutError(f'stock_financial_analysis_indicator 超时(>{_FUND_ABSTRACT_TIMEOUT}s)')
    return df


def _abstract_name_index(df, symbol):
    """019P R-1：按指标名建立去重索引（选项组优先级 + 取第一行）。
    返回 {指标名: 行下标}，仅包含 _ABSTRACT_FIELD_MAP 中的指标名。
    """
    rank = {g: i for i, g in enumerate(_ABSTRACT_GROUP_PRIORITY)}
    wanted = {name for name, _ in _ABSTRACT_FIELD_MAP}
    name_to_idx = {}
    for idx, r in df.iterrows():
        name = str(r.get('指标', '')).strip()
        if name not in wanted:
            continue
        if name not in name_to_idx:
            name_to_idx[name] = idx
        else:
            cur_rank = rank.get(str(df.iloc[name_to_idx[name]].get('选项', '')).strip(), len(rank))
            new_rank = rank.get(str(r.get('选项', '')).strip(), len(rank))
            if new_rank < cur_rank:
                name_to_idx[name] = idx
    return name_to_idx


def _extract_abstract_rows(df, symbol, max_periods=8):
    """019P M-2/R-2：abstract DataFrame → 最近 max_periods 期财报行。
    按报告期列遍历（最新在前，R-2 修正）；20260331→2026-03-31；
    写最近 8 期（2 年，否决全历史 100+ 期防 UI 膨胀 R-8）。
    返回 [(report_date, {db_col: value_or_None}), ...] 最新在前。
    """
    if df is None or df.empty:
        return []
    name_to_idx = _abstract_name_index(df, symbol)
    if not name_to_idx:
        logger.warning(f'[019P {symbol}] abstract 未匹配到指标行，实际列: {list(df.columns)[:5]}')
        return []
    period_cols = [c for c in df.columns if isinstance(c, str) and len(c) == 8 and c.isdigit()][
        :max_periods
    ]
    if not period_cols:
        logger.warning(f'[019P {symbol}] abstract 未识别到报告期列: {list(df.columns)[:5]}')
        return []
    rows = []
    for pcol in period_cols:
        report_date = f'{pcol[:4]}-{pcol[4:6]}-{pcol[6:8]}'
        vals = {}
        for name, db_col in _ABSTRACT_FIELD_MAP:
            idx = name_to_idx.get(name)
            vals[db_col] = _safe_num(df.iloc[idx][pcol]) if idx is not None else None
        rows.append((report_date, vals))
    return rows


def _safe_row_val(r, *keys):
    """019P：DataFrame 行安全取数（沿用原 safe_get 语义，模块级化供降级层复用）"""
    for k in keys:
        if k in r.index:
            val = r[k]
            if pd.notna(val):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return None
    return None


def _extract_indicator_rows(df, max_periods=4):
    """019P P2：降级层 analysis_indicator DataFrame → 最近 max_periods 期行（旧逻辑保留）。
    返回 [(report_date, {db_col: value_or_None}), ...] 最新在前。
    """
    rows = []
    total_rows = len(df)
    take_count = min(max_periods, total_rows)
    for idx in range(total_rows - 1, total_rows - 1 - take_count, -1):
        row = df.iloc[idx]
        report_date = str(row.get('日期', '')).split(' ')[0]
        vals = {
            'roe': _safe_row_val(row, '净资产收益率(%)', '加权净资产收益率(%)'),
            'gross_margin': _safe_row_val(row, '销售毛利率(%)'),
            'net_margin': _safe_row_val(row, '销售净利率(%)'),
            'debt_ratio': _safe_row_val(row, '资产负债率(%)'),
            'current_ratio': _safe_row_val(row, '流动比率'),
            'quick_ratio': _safe_row_val(row, '速动比率'),
            'revenue_growth': _safe_row_val(row, '主营业务收入增长率(%)'),
            'profit_growth': _safe_row_val(row, '净利润增长率(%)'),
            'ocf_to_net_profit': _safe_row_val(
                row, '经营现金净流量对净利润的比率(%)', '经营现金净流量与净利润的比率(%)'
            ),
        }
        rows.append((report_date, vals))
    return rows


# ============================================================
# 业绩预告采集（东财 stock_yjyg_em，A股）
# ============================================================
# 全市场预告 DataFrame 缓存：按报告期 key，1 小时 TTL，
# 避免批量场景每只股票重复下载全市场 4800+ 行数据
_FORECAST_CACHE = {'data': {}, 'ts': 0.0}
_FORECAST_CACHE_TTL = 3600


def _forecast_report_periods():
    """候选报告期列表（新→旧）：今年中报、今年一季报、去年年报"""
    y = datetime.now(_CN_TZ).year
    return [f'{y}0630', f'{y}0331', f'{y - 1}1231']


def _get_forecast_df_for_period(period):
    """拉取指定报告期的全市场业绩预告（带 1 小时内存缓存），返回 DataFrame 或 None"""
    global _FORECAST_CACHE
    now_ts = time.time()
    cached = _FORECAST_CACHE['data'].get(period)
    if cached is not None and (now_ts - _FORECAST_CACHE['ts']) < _FORECAST_CACHE_TTL:
        return cached
    try:
        import akshare as ak

        logger.info(f'[业绩预告] 请求 stock_yjyg_em(date={period})')
        df = ak.stock_yjyg_em(date=period)
        if df is None or df.empty:
            logger.warning(f'[业绩预告] 报告期 {period} 返回空数据')
            return None
        df['_code6'] = df['股票代码'].astype(str).str.zfill(6)
        _FORECAST_CACHE['data'][period] = df
        _FORECAST_CACHE['ts'] = now_ts
        return df
    except Exception as e:
        logger.warning(f'[业绩预告] 报告期 {period} 获取失败: {e}')
        return None


def collect_forecast(stock_id, symbol, market='a_stock'):
    """采集单只股票业绩预告（东财），写入 raw_forecast。

    港股无东财业绩预告数据，跳过。
    按报告期（今年中报→一季报→去年年报）逐期尝试，写入全部命中的预告行。
    返回 (status, message)。
    """
    if market != 'a_stock':
        save_data_status(stock_id, 'forecast', 'skipped', '港股无东财业绩预告数据')
        return 'skipped', '港股无东财业绩预告数据'

    try:
        written = 0
        used_periods = []
        for period in _forecast_report_periods():
            df = _get_forecast_df_for_period(period)
            if df is None or df.empty:
                continue
            stock_rows = df[df['_code6'] == symbol]
            if stock_rows.empty:
                continue
            used_periods.append(period)
            conn = get_connection()
            cursor = conn.cursor()
            for _, row in stock_rows.iterrows():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_forecast
                        (stock_id, symbol, report_period, indicator, change_desc,
                         forecast_value, change_pct, change_reason, forecast_type,
                         last_year_value, announce_date, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'akshare_em')
                """,
                    (
                        stock_id,
                        symbol,
                        period,
                        str(row.get('预测指标') or ''),
                        str(row.get('业绩变动') or '')[:1000],
                        _safe_num(row.get('预测数值')),
                        _safe_num(row.get('业绩变动幅度')),
                        str(row.get('业绩变动原因') or '')[:1500],
                        str(row.get('预告类型') or ''),
                        _safe_num(row.get('上年同期值')),
                        str(row.get('公告日期') or '')[:10],
                    ),
                )
                written += 1
            conn.commit()
            conn.close()

        if written > 0:
            msg = f'业绩预告已入库 {written} 条（报告期: {", ".join(used_periods)}）'
            save_data_status(stock_id, 'forecast', 'success', msg)
            return 'success', msg
        msg = f'最近三个报告期暂无业绩预告（{", ".join(_forecast_report_periods())}）'
        save_data_status(stock_id, 'forecast', 'success', msg)
        return 'success', msg
    except Exception as e:
        logger.warning(f'[{symbol}] 业绩预告采集失败: {e}')
        save_data_status(stock_id, 'forecast', 'failed', str(e))
        return 'failed', str(e)


# ============================================================
# 业绩快报采集（东财 stock_yjkb_em，A股）020R-50
# ============================================================
# 全市场快报 DataFrame 缓存：按报告期 key，1 小时 TTL（与预告共用 TTL 常量）
_EXPRESS_CACHE = {'data': {}, 'ts': 0.0}


def _get_express_df_for_period(period):
    """拉取指定报告期的全市场业绩快报（带 1 小时内存缓存），返回 DataFrame 或 None"""
    global _EXPRESS_CACHE
    now_ts = time.time()
    cached = _EXPRESS_CACHE['data'].get(period)
    if cached is not None and (now_ts - _EXPRESS_CACHE['ts']) < _FORECAST_CACHE_TTL:
        return cached
    try:
        import akshare as ak

        logger.info(f'[业绩快报] 请求 stock_yjkb_em(date={period})')
        df = ak.stock_yjkb_em(date=period)
        if df is None or df.empty:
            logger.warning(f'[业绩快报] 报告期 {period} 返回空数据')
            return None
        df['_code6'] = df['股票代码'].astype(str).str.zfill(6)
        _EXPRESS_CACHE['data'][period] = df
        _EXPRESS_CACHE['ts'] = now_ts
        return df
    except Exception as e:
        logger.warning(f'[业绩快报] 报告期 {period} 获取失败: {e}')
        return None


def collect_express(stock_id, symbol, market='a_stock'):
    """采集单只股票业绩快报（东财），写入 raw_express。

    港股无东财业绩快报数据，跳过。
    按报告期（今年中报→一季报→去年年报）逐期尝试，写入全部命中的快报行。
    返回 (status, message)。
    """
    if market != 'a_stock':
        save_data_status(stock_id, 'express', 'skipped', '港股无东财业绩快报数据')
        return 'skipped', '港股无东财业绩快报数据'

    try:
        written = 0
        used_periods = []
        for period in _forecast_report_periods():
            df = _get_express_df_for_period(period)
            if df is None or df.empty:
                continue
            stock_rows = df[df['_code6'] == symbol]
            if stock_rows.empty:
                continue
            used_periods.append(period)
            conn = get_connection()
            cursor = conn.cursor()
            for _, row in stock_rows.iterrows():
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_express
                        (stock_id, symbol, report_period, eps, revenue, revenue_yoy,
                         np, np_yoy, roe, announce_date, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'akshare_em')
                """,
                    (
                        stock_id,
                        symbol,
                        period,
                        _safe_num(row.get('每股收益')),
                        _safe_num(row.get('营业收入-营业收入')),
                        _safe_num(row.get('营业收入-同比增长')),
                        _safe_num(row.get('净利润-净利润')),
                        _safe_num(row.get('净利润-同比增长')),
                        _safe_num(row.get('净资产收益率')),
                        str(row.get('公告日期') or '')[:10],
                    ),
                )
                written += 1
            conn.commit()
            conn.close()

        if written > 0:
            msg = f'业绩快报已入库 {written} 条（报告期: {", ".join(used_periods)}）'
            save_data_status(stock_id, 'express', 'success', msg)
            return 'success', msg
        msg = f'最近三个报告期暂无业绩快报（{", ".join(_forecast_report_periods())}）'
        save_data_status(stock_id, 'express', 'success', msg)
        return 'success', msg
    except Exception as e:
        logger.warning(f'[{symbol}] 业绩快报采集失败: {e}')
        save_data_status(stock_id, 'express', 'failed', str(e))
        return 'failed', str(e)


def _safe_num(v):
    """安全数值转换：None/NaN/非数值 → None"""
    try:
        import math

        if v is None:
            return None
        f = float(v)
        if math.isnan(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def fetch_a_fundamental(symbol, force_full=False):
    """采集A股基本面数据：财务指标 + PE/PB估值
    011增量：80天财报TTL + 24h PE/PB TTL，双门控独立。
    """
    stock_id = get_stock_id(symbol, 'a_stock')
    if not stock_id:
        return 'failed', f'数据库中未找到A股 {symbol}'

    # 011增量门控
    skip_financial = False  # 是否跳过财报采集
    skip_pepb = False  # 是否跳过PE/PB采集
    # 019P-A6：存量自动回补标记（最新期毛利率缺失触发，message 区分 R-4）
    backfill_triggered = False

    if not force_full:
        # 门控A：财报数据TTL（80天）
        # 019P-A6：TTL 门控内附加完整性检查——最新一期 gross_margin IS NULL → 不跳过财报采集
        # （"不重复获取"= 已有完整数据不重复获取；完整性缺失时获取缺项不构成重复，B10 先例精神）
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                'SELECT MAX(report_date) as last_report FROM raw_fundamental WHERE stock_id = ?',
                (stock_id,),
            )
            row = cursor_chk.fetchone()
            if row and row['last_report']:
                last_report_date = str(row['last_report'])[:10]
                days_since = (
                    datetime.now(_CN_TZ).replace(tzinfo=None)
                    - datetime.strptime(last_report_date, '%Y-%m-%d')
                ).days
                if days_since < FUNDAMENTAL_REPORT_TTL_DAYS:
                    # 019P-A6：完整性检查（最新期毛利率缺失 → 自动回补，不跳过）
                    cursor_chk.execute(
                        'SELECT gross_margin FROM raw_fundamental WHERE stock_id = ? AND report_date = ?',
                        (stock_id, last_report_date),
                    )
                    gm_row = cursor_chk.fetchone()
                    if gm_row is not None and gm_row['gross_margin'] is None:
                        backfill_triggered = True
                        logger.info(
                            f'[A股 {symbol}] 最新期({last_report_date})毛利率缺失，触发财报补全（abstract 重采）'
                        )
                    else:
                        skip_financial = True
                        logger.info(f'[A股 {symbol}] 财报数据{days_since}天内，跳过财报采集')

                    # 门控B：PE/PB TTL（24h），仅当财报跳过时检查
                    if skip_financial:
                        cursor_chk.execute(
                            """SELECT fetched_at FROM data_status
                               WHERE stock_id = ? AND dimension = 'fundamental'
                               ORDER BY fetched_at DESC LIMIT 1""",
                            (stock_id,),
                        )
                        status_row = cursor_chk.fetchone()
                        if status_row and status_row['fetched_at']:
                            last_fetch = datetime.strptime(
                                str(status_row['fetched_at'])[:19], '%Y-%m-%d %H:%M:%S'
                            )
                            hours_since = (
                                datetime.now(_CN_TZ).replace(tzinfo=None) - last_fetch
                            ).total_seconds() / 3600
                            if hours_since < PE_PB_CACHE_TTL_HOURS:
                                skip_pepb = True
            conn_chk.close()
        except Exception as e:
            logger.warning(f'[A股 {symbol}] 基本面增量检查异常(降级为全量): {e}')

    # 两门控都跳过 → 整体跳过
    if skip_financial and skip_pepb:
        skip_msg = '同日跳过(财报80天TTL内+PE/PB 24h内)'
        save_data_status(stock_id, 'fundamental', 'success', skip_msg)
        return 'success', skip_msg

    warnings = []
    saved_count = 0

    # --- 财务分析指标（019P：abstract 主源，P2 失败降级 analysis_indicator）---
    # 019P-A1：主源切换 stock_financial_abstract（新浪关键指标摘要）：
    #   - 结构适配（M-2）：行=指标，列=['选项','指标']+报告期列（最新在前），无需转置
    #   - 同名指标去重（R-1）：选项=常用指标优先 + 取第一行
    #   - 写最近 8 期（2 年，防 UI 膨胀 R-8）；次新股（688795/688802）顺带解决（发现 4）
    #   - 数据源标注：'sina_abstract'；降级路径标 'sina_analysis_indicator'（A-3）
    used_abstract = False
    if not skip_financial:
        fin_rows = None
        try:
            df_abstract = _fetch_a_fundamental_sina(symbol)
            fin_rows = _extract_abstract_rows(df_abstract, symbol)
            if fin_rows:
                used_abstract = True
                logger.info(f'[A股 {symbol}] abstract 解析 {len(fin_rows)} 期财报（最新在前）')
            else:
                logger.warning(f'[A股 {symbol}] abstract 数据为空，降级现接口')
        except Exception as e:
            # P2：abstract 失败仅记日志（降级由 message 前缀标注，不重复写入 warnings 致 partial）
            logger.warning(f'[A股 {symbol}] abstract 获取失败(降级现接口): {e}')

        if fin_rows is None or not fin_rows:
            # P2 降级层：现接口 stock_financial_analysis_indicator（保留原路径）
            try:
                df_ind = _fetch_a_fundamental_sina_indicator(symbol)
                if df_ind is not None and not df_ind.empty:
                    fin_rows = _extract_indicator_rows(df_ind)
                    used_abstract = False
                else:
                    warnings.append('财务分析指标数据为空')
            except Exception as e:
                warnings.append(f'财务指标获取失败: {e}')
                logger.warning(f'[A股 {symbol}] 财务指标获取失败(降级层): {e}')

        # 019Y T2：P3 备用层——baostock 财务数据（仅A股，akshare 两层全失败时降级使用）
        used_baostock = False
        if fin_rows is None or not fin_rows:
            try:
                bs_rows = fetch_fundamental_baostock(symbol)
                if bs_rows:
                    fin_rows = bs_rows
                    used_baostock = True
                    # P3 成功时，移除 P2 层累积的失败警告（数据已由备用源补全，不误报缺失）
                    warnings = [
                        w
                        for w in warnings
                        if not w.startswith('财务指标获取失败') and w != '财务分析指标数据为空'
                    ]
                    logger.info(f'[A股 {symbol}] baostock 财务备用源成功: {len(bs_rows)} 期')
                else:
                    warnings.append('baostock财务备用源也无数据（akshare+baostock均失败）')
            except Exception as e:
                warnings.append(f'baostock财务备用源失败: {e}')
                logger.warning(f'[A股 {symbol}] baostock财务备用源失败: {e}')
        else:
            used_baostock = False

        if fin_rows:
            conn = get_connection()
            cursor = conn.cursor()
            # P1（必需）：读取既有 ocf 值，abstract 该期 ocf 为 NaN 时保留原值（回归红线）
            cursor.execute(
                'SELECT report_date, ocf_to_net_profit FROM raw_fundamental WHERE stock_id = ?',
                (stock_id,),
            )
            existing_ocf = {str(r['report_date']): r['ocf_to_net_profit'] for r in cursor.fetchall()}
            # 019Y T2：数据源标注三通道——sina_abstract / baostock / sina_analysis_indicator
            if used_abstract:
                data_source = 'sina_abstract'
            elif used_baostock:
                data_source = 'baostock'
            else:
                data_source = 'sina_analysis_indicator'
            for report_date, vals in fin_rows:
                ocf = vals.get('ocf_to_net_profit')
                if ocf is None and existing_ocf.get(report_date) is not None:
                    # P1：abstract ocf=NaN 且 DB 已有值 → 保留原值（实测 600276 20260331 场景）
                    ocf = existing_ocf[report_date]
                    logger.info(
                        f'[A股 {symbol}] {report_date} ocf 保留既有值 {ocf}（abstract 该期为 NaN）'
                    )

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_fundamental
                    (stock_id, report_date,
                     roe, gross_margin, net_margin, debt_ratio,
                     current_ratio, quick_ratio,
                     revenue_growth, profit_growth,
                     ocf_to_net_profit, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        report_date,
                        vals.get('roe'),
                        vals.get('gross_margin'),
                        vals.get('net_margin'),
                        vals.get('debt_ratio'),
                        vals.get('current_ratio'),
                        vals.get('quick_ratio'),
                        vals.get('revenue_growth'),
                        vals.get('profit_growth'),
                        ocf,
                        data_source,
                    ),
                )
                saved_count += 1
                logger.info(f'[A股 {symbol}] 财报: {report_date}, ROE={vals.get("roe")}')

            conn.commit()
            conn.close()

    # --- 估值指标 PE/PB（腾讯实时行情接口）---
    # PE/PB 是实时行情数据，不单独创建记录，而是合并到最新的财报记录中
    pe_val = None
    pb_val = None
    if not skip_pepb:
        try:
            result = _fetch_valuation_tencent(symbol, 'a_stock')
            if result is None:
                warnings.append('PE/PB获取失败（腾讯接口无响应）')
            else:
                pe_val, pb_val = result
                if (pe_val is None or pe_val == 0) and (pb_val is None or pb_val == 0):
                    warnings.append('PE/PB数据为空')
                else:
                    logger.info(f'[A股 {symbol}] 估值数据: PE={pe_val}, PB={pb_val}')
        except Exception as e:
            warnings.append(f'PE/PB获取失败: {e}')
            logger.warning(f'[A股 {symbol}] PE/PB获取失败: {e}')

    # 将 PE/PB 更新到最新的财报记录中（不创建新行）
    if pe_val is not None or pb_val is not None:
        conn2 = get_connection()
        cursor2 = conn2.cursor()
        # 获取最新财报记录的 report_date
        cursor2.execute(
            'SELECT report_date FROM raw_fundamental WHERE stock_id = ? ORDER BY report_date DESC LIMIT 1',
            (stock_id,),
        )
        latest_row = cursor2.fetchone()
        if latest_row:
            cursor2.execute(
                """
                UPDATE raw_fundamental SET pe_ratio = ?, pb_ratio = ?
                WHERE stock_id = ? AND report_date = ?
            """,
                (pe_val, pb_val, stock_id, latest_row['report_date']),
            )
            conn2.commit()
            logger.info(f'[A股 {symbol}] PE/PB 已合并到财报 {latest_row["report_date"]}')
        conn2.close()

    # 011：返回逻辑调整（增加 skip_financial 仅PE/PB更新的场景）
    # 019P-A3：data_status message 前缀标注数据源（'新浪abstract财报+腾讯估值' /
    #          '新浪指标(analysis_indicator降级)+腾讯估值' / 港股见 fetch_hk_fundamental）
    if skip_financial and not skip_pepb:
        # 仅采集了PE/PB（财报跳过）
        if not warnings:
            save_data_status(stock_id, 'fundamental', 'success', '腾讯估值: PE/PB更新成功(财报跳过)')
            return 'success', '腾讯估值: PE/PB更新成功(财报跳过)'
        else:
            save_data_status(stock_id, 'fundamental', 'partial', '腾讯估值: ' + '; '.join(warnings))
            return 'partial', '腾讯估值: ' + '; '.join(warnings)
    elif saved_count > 0 and not warnings:
        # 019Y T2：三通道来源标注（abstract主源 / baostock备用 / analysis_indicator降级）
        if used_abstract:
            src_tag = '新浪abstract财报+腾讯估值'
        elif used_baostock:
            src_tag = 'baostock财务备用+腾讯估值'
        else:
            src_tag = '新浪指标(analysis_indicator降级)+腾讯估值'
        if backfill_triggered:
            # 019P-R4：回补场景 message 区分（与"同日跳过"区分）
            msg = f'{src_tag}: 财报补全(毛利率缺失触发)'
        else:
            msg = f'{src_tag}: 基本面数据采集成功'
        save_data_status(stock_id, 'fundamental', 'success', msg)
        return 'success', msg
    elif saved_count > 0 and warnings:
        if used_abstract:
            src_tag = '新浪abstract财报+腾讯估值'
        elif used_baostock:
            src_tag = 'baostock财务备用+腾讯估值'
        else:
            src_tag = '新浪指标(analysis_indicator降级)+腾讯估值'
        save_data_status(
            stock_id, 'fundamental', 'partial', f'{src_tag}: 获取{saved_count}条财务数据，缺失: ' + '; '.join(warnings)
        )
        return 'partial', f'获取{saved_count}条财务数据，缺失: ' + '; '.join(warnings)
    else:
        save_data_status(stock_id, 'fundamental', 'failed', '; '.join(warnings))
        return 'failed', '; '.join(warnings)


def fetch_fundamental_detail(symbol: str) -> dict:
    """B10: 调用 akshare 财务分析指标接口，补全基本面字段。
    作为 fetch_a_fundamental 的补充：仅更新数据库中为 NULL 的字段，不覆盖已有值。

    Returns:
        dict: {roe, gross_margin, revenue_yoy, net_profit_yoy,
               ocf_to_profit, debt_to_asset, current_ratio}
        失败时返回空 dict
    """
    result = {}
    try:
        df = ak.stock_financial_analysis_indicator(symbol=symbol, start_year='2023')
        if df is None or df.empty:
            return result
        row = df.iloc[-1]  # 取最新一期

        def _safe_float(r, *keys):
            for k in keys:
                if k in r.index:
                    val = r[k]
                    if pd.notna(val):
                        try:
                            return round(float(val), 4)
                        except (ValueError, TypeError):
                            pass
            return None

        result['roe'] = _safe_float(row, '净资产收益率(%)', '加权净资产收益率(%)')
        result['gross_margin'] = _safe_float(row, '销售毛利率(%)')
        result['revenue_yoy'] = _safe_float(row, '主营业务收入增长率(%)')
        result['net_profit_yoy'] = _safe_float(row, '净利润增长率(%)')
        result['ocf_to_profit'] = _safe_float(
            row, '经营现金净流量对净利润的比率(%)', '经营现金净流量与净利润的比率(%)'
        )
        result['debt_to_asset'] = _safe_float(row, '资产负债率(%)')
        result['current_ratio'] = _safe_float(row, '流动比率')
        # 移除 None 值
        result = {k: v for k, v in result.items() if v is not None}
        logger.info(f'[B10 基本面补全 {symbol}] 获取到 {len(result)} 个字段: {list(result.keys())}')
    except Exception as e:
        logger.warning(f'[B10 基本面补全 {symbol}] 接口失败(静默降级): {e}')
    return result


def _apply_fundamental_detail(stock_id: int, detail: dict):
    """B10: 将 fetch_fundamental_detail 的结果写入 raw_fundamental（仅填充 NULL 字段）"""
    if not detail:
        return
    # 字段映射: detail key -> DB column
    col_map = {
        'roe': 'roe',
        'gross_margin': 'gross_margin',
        'revenue_yoy': 'revenue_growth',
        'net_profit_yoy': 'profit_growth',
        'ocf_to_profit': 'ocf_to_net_profit',
        'debt_to_asset': 'debt_ratio',
        'current_ratio': 'current_ratio',
    }
    conn = get_connection()
    cursor = conn.cursor()
    # 检查是否有任何记录
    cursor.execute('SELECT COUNT(*) as cnt FROM raw_fundamental WHERE stock_id = ?', (stock_id,))
    if cursor.fetchone()['cnt'] == 0:
        conn.close()
        return
    # 仅更新当前为 NULL 的字段
    updates = []
    params = []
    for detail_key, db_col in col_map.items():
        if detail_key in detail:
            updates.append(f'{db_col} = COALESCE({db_col}, ?)')
            params.append(detail[detail_key])
    if updates:
        params.append(stock_id)
        sql = f'UPDATE raw_fundamental SET {", ".join(updates)} WHERE stock_id = ?'
        cursor.execute(sql, params)
        conn.commit()
        logger.info(f'[B10 基本面补全] stock_id={stock_id} 更新了 {len(updates)} 个字段')
    conn.close()


def fetch_holder_increase(symbol: str, preloaded_df=None):
    """B10: 获取近30天大股东/高管增减持信息（仅A股）。
    数据源：雪球内部交易接口 stock_inner_trade_xq()
    列结构: 股票代码/股票名称/变动日期/变动人/变动股数/成交均价/变动后持股数/与董监高关系/董监高职务
    增减持方向通过 '变动股数' 正负判断（正=增持，负=减持）

    B11-API-DEDUP：支持预加载数据(preloaded_df)和模块级缓存，避免批量时重复调用全市场接口。

    020R-44 三态语义：
        True  = 近30天有增持（利好）
        False = 近30天无增持（含：有减持、或30天内/全市场无任何披露记录）
        None  = 接口不可用/返回空（数据缺失，评分时该项权重归零）
    """
    global _holder_cache, _holder_cache_time

    try:
        if preloaded_df is not None:
            df = preloaded_df
        elif (
            _holder_cache is not None
            and _holder_cache_time
            and (time.time() - _holder_cache_time) < 600
        ):
            # 10分钟内缓存有效
            df = _holder_cache
        else:
            df = ak.stock_inner_trade_xq()
            _holder_cache = df
            _holder_cache_time = time.time()

        if df is None or df.empty:
            return None
        # 股票代码列含前缀 SZ/SH，如 'SZ000858'
        code_col = '股票代码'
        if code_col not in df.columns:
            return None
        # 过滤本股票
        sub = df[df[code_col].str.contains(symbol, na=False)]
        if sub.empty:
            # 020R-44：全市场无该股任何披露记录 → 视为近30天无增持（False），
            # 与「接口失败(None)」区分开
            return False
        # 过滤近30天
        date_col = '变动日期'
        if date_col not in sub.columns:
            return None
        cutoff = (datetime.now(_CN_TZ) - timedelta(days=30)).strftime('%Y-%m-%d')
        sub = sub[sub[date_col].astype(str) >= cutoff]
        if sub.empty:
            # 020R-44：30天内无变动 → 近30天无增持（False）
            return False
        # 判断方向：通过 '变动股数' 正负判断（正=增持，负=减持）
        shares_col = '变动股数'
        if shares_col not in sub.columns:
            return None
        shares = sub[shares_col].tolist()
        has_increase = any(float(s) > 0 for s in shares if s is not None)
        has_decrease = any(float(s) < 0 for s in shares if s is not None)
        if has_increase and not has_decrease:
            return True
        elif has_decrease and not has_increase:
            return False
        elif has_increase and has_decrease:
            # 既有增持又有减持，按最近一笔判断
            latest_shares = sub.iloc[0][shares_col]
            return float(latest_shares) > 0 if latest_shares is not None else None
        return False
    except Exception as e:
        logger.warning(f'[B10 股东增减持 {symbol}] 接口失败(静默降级): {e}')
        return None


def _save_holder_increase(stock_id: int, holder_increase):
    """B10: 将 holder_increase 写入 raw_fundamental 表（ALTER TABLE 新增列）"""
    conn = get_connection()
    cursor = conn.cursor()
    # 确保列存在（幂等 ALTER TABLE）
    try:
        cursor.execute('ALTER TABLE raw_fundamental ADD COLUMN holder_increase BOOLEAN')
        conn.commit()
        logger.info('[B10] raw_fundamental 表新增 holder_increase 列')
    except Exception:
        pass  # 列已存在
    # 更新最新记录
    if holder_increase is not None:
        cursor.execute(
            'UPDATE raw_fundamental SET holder_increase = ? WHERE stock_id = ? AND report_date = (SELECT MAX(report_date) FROM raw_fundamental WHERE stock_id = ?)',
            (holder_increase, stock_id, stock_id),
        )
        conn.commit()
        logger.info(f'[B10 股东增减持] stock_id={stock_id}, holder_increase={holder_increase}')
    conn.close()


# ============================================================
# 020R-48B：周线/月线聚合（由日线 resample，供多周期技术面评分与展示）
# ============================================================


def aggregate_period_klines(stock_id):
    """由 raw_kline 日线聚合周线（ISO 自然周，周一~周五）与月线（自然月），幂等覆盖。

    周/月线 K 线口径：open=首日开盘、high=区间最高、low=区间最低、close=末日收盘、
    volume=区间合计、trade_date=区间最后一个交易日。
    返回 (status, message)。
    """
    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT trade_date, open, high, low, close, volume FROM raw_kline '
        'WHERE stock_id = ? ORDER BY trade_date ASC',
        (stock_id,),
    )
    rows = cur.fetchall()
    if len(rows) < 5:
        conn.close()
        return 'skipped', '日线不足5条，跳过周期聚合'

    df = pd.DataFrame([dict(r) for r in rows])
    df['trade_date'] = pd.to_datetime(df['trade_date'])
    df['iso_year'] = df['trade_date'].dt.isocalendar()['year']
    df['iso_week'] = df['trade_date'].dt.isocalendar()['week']
    df['ym'] = df['trade_date'].dt.to_period('M')

    weekly = (
        df.groupby(['iso_year', 'iso_week'])
        .agg(
            open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
            close=('close', 'last'), volume=('volume', 'sum'),
            trade_date=('trade_date', 'last'),
        )
        .reset_index(drop=True)
    )
    monthly = (
        df.groupby('ym')
        .agg(
            open=('open', 'first'), high=('high', 'max'), low=('low', 'min'),
            close=('close', 'last'), volume=('volume', 'sum'),
            trade_date=('trade_date', 'last'),
        )
        .reset_index(drop=True)
    )

    for table, wdf in (('raw_kline_weekly', weekly), ('raw_kline_monthly', monthly)):
        for _, r in wdf.iterrows():
            cur.execute(
                f'INSERT OR REPLACE INTO {table} '
                '(stock_id, trade_date, open, close, high, low, volume) '
                'VALUES (?,?,?,?,?,?,?)',
                (
                    stock_id, str(r['trade_date'])[:10],
                    float(r['open'] or 0), float(r['close'] or 0),
                    float(r['high'] or 0), float(r['low'] or 0),
                    float(r['volume'] or 0),
                ),
            )
    conn.commit()
    conn.close()
    logger.info(f'[020R-48 周期聚合] stock_id={stock_id}: 周线{len(weekly)}根/月线{len(monthly)}根')
    return 'success', f'周线{len(weekly)}根/月线{len(monthly)}根'


# ============================================================
# 020R-45：股东人数与机构持仓采集（A股专属，资金面-筹码结构）
# ============================================================

HOLD_INST_TYPES = ['基金持仓', 'QFII持仓', '社保持仓', '券商持仓', '保险持仓']  # 020R-45：阳光私募接口不支持，5类

_fund_hold_cache: dict = {}
_fund_hold_cache_time: dict = {}


def _num_or_none(v):
    """宽松数值转换（'-'/''/NaN → None）。"""
    try:
        if v is None or v == '' or v == '-':
            return None
        if pd.isna(v):
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _latest_fund_hold_dates():
    """候选机构持仓报告期（YYYYMMDD）：今天之前最近若干季度末，从新到旧。"""
    today = datetime.now(_CN_TZ)
    candidates = set()
    for year in (today.year, today.year - 1, today.year - 2):
        for md in ('1231', '0930', '0630', '0331'):
            d = f'{year}{md}'
            if d <= today.strftime('%Y%m%d'):
                candidates.add(d)
    return sorted(candidates, reverse=True)


def _get_fund_hold_table(hold_type, date):
    """取某类机构持仓全市场表（10分钟模块级缓存，批量采集复用）。失败返回 None。"""
    key = (hold_type, date)
    now = time.time()
    cached_at = _fund_hold_cache_time.get(key, 0)
    if key in _fund_hold_cache and cached_at and (now - cached_at) < 600:
        return _fund_hold_cache[key]
    try:
        df = ak.stock_report_fund_hold(symbol=hold_type, date=date)
        _fund_hold_cache[key] = df
        _fund_hold_cache_time[key] = now
        return df
    except Exception as e:
        logger.warning(f'[020R-45 机构持仓] {hold_type} {date} 获取失败: {e}')
        return None


def fetch_holder_structure(symbol: str):
    """020R-45：采集股东人数（东财户数明细）与机构持仓（东财六类机构持股汇总）。

    Returns:
        dict: stat_date / holder_count / holder_count_change_pct / total_shares /
              inst_shares / inst_ratio / inst_report_date
        接口不可用或数据异常时返回 None（静默降级，不阻塞主流程）。
    """
    try:
        gdhs = ak.stock_zh_a_gdhs_detail_em(symbol=symbol)
        if gdhs is None or gdhs.empty:
            return None
        # 020R-45：该接口按日期升序返回，最新一期在最后一行（iloc[-1]）
        latest = gdhs.iloc[-1]
        total_shares = _num_or_none(latest.get('总股本'))
        result = {
            'stat_date': str(latest.get('股东户数统计截止日'))[:10],
            'holder_count': _num_or_none(latest.get('股东户数-本次')),
            'holder_count_change_pct': _num_or_none(latest.get('股东户数-增减比例')),
            'total_shares': total_shares,
            'inst_shares': None,
            'inst_ratio': None,
            'inst_report_date': None,
        }
        # 机构持仓：六类机构持股汇总 / 总股本
        inst_shares = 0.0
        inst_date = None
        for hold_type in HOLD_INST_TYPES:
            for date in _latest_fund_hold_dates():
                df = _get_fund_hold_table(hold_type, date)
                if df is None or df.empty or '股票代码' not in df.columns:
                    continue
                sub = df[df['股票代码'].astype(str).str.contains(symbol, na=False)]
                if sub.empty:
                    continue
                if '持股总数' in df.columns:
                    try:
                        inst_shares += float(sub.iloc[0]['持股总数'] or 0)
                    except (TypeError, ValueError):
                        pass
                inst_date = inst_date or date
                break  # 该类型取最近一个有数据的报告期
        if inst_date:
            result['inst_shares'] = round(inst_shares, 2)
            result['inst_report_date'] = inst_date
            if total_shares and total_shares > 0:
                result['inst_ratio'] = round(inst_shares / total_shares * 100, 2)
        return result
    except Exception as e:
        logger.warning(f'[020R-45 股东人数/机构持仓 {symbol}] 采集失败(静默降级): {e}')
        return None


def _save_holder_structure(stock_id: int, data):
    """020R-45：股东人数/机构持仓快照落库（按 stat_date 幂等，保留最近 12 期）。"""
    if not data or data.get('stat_date') is None:
        return
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'INSERT OR REPLACE INTO holder_structure '
        '(stock_id, stat_date, holder_count, holder_count_change_pct, total_shares, '
        'inst_shares, inst_ratio, inst_report_date) VALUES (?,?,?,?,?,?,?,?)',
        (
            stock_id, data['stat_date'], data.get('holder_count'),
            data.get('holder_count_change_pct'), data.get('total_shares'),
            data.get('inst_shares'), data.get('inst_ratio'), data.get('inst_report_date'),
        ),
    )
    cursor.execute(
        'DELETE FROM holder_structure WHERE stock_id=? AND stat_date NOT IN '
        '(SELECT stat_date FROM holder_structure WHERE stock_id=? ORDER BY stat_date DESC LIMIT 12)',
        (stock_id, stock_id),
    )
    conn.commit()
    conn.close()
    logger.info(
        f'[020R-45 股东人数/机构持仓] stock_id={stock_id}, '
        f'holder_change={data.get("holder_count_change_pct")}, inst_ratio={data.get("inst_ratio")}'
    )


# ============================================================
# 020R-47：南向资金（港股通）大盘快照采集（仅展示参考，不参评）
# 数据源：akshare stock_hsgt_hist_em(symbol='南向资金')——实测仍正常更新。
# ============================================================


def _num_or_none_flow(v):
    try:
        if v is None or v == '-' or v == '':
            return None
        if pd.isna(v):
            return None
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def fetch_south_flow_snapshot():
    """020R-47：抓取南向资金最新一行 → dict；失败返回 None。

    单位：当日净买/买卖额=亿元；持股市值换算为万亿。
    """
    try:
        df = ak.stock_hsgt_hist_em(symbol='南向资金')
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        hold_mv = _num_or_none_flow(row.get('持股市值'))
        return {
            'trade_date': str(row.get('日期'))[:10],
            'net_buy': _num_or_none_flow(row.get('当日成交净买额')),
            'buy_amount': _num_or_none_flow(row.get('买入成交额')),
            'sell_amount': _num_or_none_flow(row.get('卖出成交额')),
            'cumulative_net': _num_or_none_flow(row.get('历史累计净买额')),
            'hold_market_value': round(hold_mv / 1e12, 2) if hold_mv is not None else None,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[020R-47 南向资金] 抓取失败(静默降级): {e}')
        return None


# ============================================================
# 港股 —— 基本面数据
# ============================================================


@retry
def _fetch_hk_fundamental_em(symbol):
    """通过akshare获取港股财务指标，需传入5位数字代码"""
    hk_code = _normalize_hk_symbol(symbol)
    logger.info(f'港股财务指标请求: symbol={symbol} -> hk_code={hk_code}')
    df = ak.stock_financial_hk_analysis_indicator_em(symbol=hk_code, indicator='年度')
    return df


def fetch_hk_fundamental(symbol, force_full=False):
    """采集港股基本面数据
    011增量：80天财报TTL（港股无独立PE/PB门控，与财报一并采集）。
    """
    stock_id = get_stock_id(symbol, 'hk_stock')
    if not stock_id:
        return 'failed', f'数据库中未找到港股 {symbol}'

    # 011增量：80天财报门控
    # 019P-A6：附加完整性检查（最新期毛利率缺失 → 自动回补）；占位行修复（M-5）为收敛前提
    backfill_triggered = False
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                'SELECT MAX(report_date) as last_report FROM raw_fundamental WHERE stock_id = ?',
                (stock_id,),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['last_report']:
                last_report_date = str(row['last_report'])[:10]
                days_since = (
                    datetime.now(_CN_TZ).replace(tzinfo=None)
                    - datetime.strptime(last_report_date, '%Y-%m-%d')
                ).days
                if days_since < FUNDAMENTAL_REPORT_TTL_DAYS:
                    # 019P-A6：完整性检查——最新真实财报行毛利率缺失 → 回补（M-5 清理占位行后收敛）
                    conn_chk2 = get_connection()
                    cur_chk2 = conn_chk2.cursor()
                    cur_chk2.execute(
                        'SELECT gross_margin FROM raw_fundamental WHERE stock_id = ? AND report_date = ?',
                        (stock_id, last_report_date),
                    )
                    gm_row = cur_chk2.fetchone()
                    conn_chk2.close()
                    if gm_row is not None and gm_row['gross_margin'] is None:
                        backfill_triggered = True
                        logger.info(
                            f'[港股 {symbol}] 最新期({last_report_date})毛利率缺失，触发财报补全'
                        )
                    else:
                        skip_msg = f'同日跳过(港股财报{days_since}天内)'
                        save_data_status(stock_id, 'fundamental', 'success', skip_msg)
                        return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[港股 {symbol}] 基本面增量检查异常(降级为全量): {e}')

    warnings = []
    saved_count = 0

    try:
        df_fin = _fetch_hk_fundamental_em(symbol)
        if df_fin is not None and not df_fin.empty:
            conn = get_connection()
            cursor = conn.cursor()

            for idx in range(min(3, len(df_fin))):
                row = df_fin.iloc[idx]

                def safe_get(r, *keys):
                    for k in keys:
                        if k in r.index:
                            val = r[k]
                            if pd.notna(val):
                                try:
                                    return float(val)
                                except (ValueError, TypeError):
                                    return None
                    return None

                # P0-HK-FUND-002：akshare>=1.18 后港股财务指标列名由中文漂移为英文，
                # safe_get 兼容新旧两套 key（英文优先，中文兜底）。
                report_date = str(
                    row.get('REPORT_DATE', row.get('日期', row.get('DATA_DATE', '')))
                ).split(' ')[0]

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_fundamental
                    (stock_id, report_date,
                     roe, gross_margin, net_margin, debt_ratio,
                     current_ratio, revenue_growth, profit_growth,
                     ocf_to_net_profit, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        report_date,
                        safe_get(row, 'ROE_AVG', '净资产收益率(%)', '净资产收益率（%）'),
                        safe_get(row, 'GROSS_PROFIT_RATIO', '销售毛利率(%)', '毛利率（%）'),
                        safe_get(row, 'NET_PROFIT_RATIO', '销售净利率(%)', '净利率（%）'),
                        safe_get(row, 'DEBT_ASSET_RATIO', '资产负债率(%)', '资产负债率（%）'),
                        safe_get(row, 'CURRENT_RATIO', '流动比率', '流动比率（倍）'),
                        safe_get(
                            row, 'OPERATE_INCOME_YOY', '营业收入增长率(%)', '营业收入同比增长（%）'
                        ),
                        safe_get(
                            row, 'HOLDER_PROFIT_YOY', '净利润增长率(%)', '净利润同比增长（%）'
                        ),
                        None,  # ocf_to_net_profit：指标接口无直接对应字段，留空降级（不引入估算）
                        'em_hk',  # 019P-A3：港股财报来源标注（东方财富 EM）
                    ),
                )
                saved_count += 1

            conn.commit()
            conn.close()

            # 019P M-5（R-3 高优先）：清理"全部指标字段 NULL 且 report_date 晚于最新真实财报行"的占位行。
            # 占位行仅含 PE/PB 时点值，真实财报行存在后无信息增量（PE/PB 下次采集即重新合并，零数据损失）。
            # 前提：仅财报写入成功时清理；同时是 A-6 自动回补收敛的前提（否则港股每日触发回补且永不收敛）。
            try:
                conn_cln = get_connection()
                cur_cln = conn_cln.cursor()
                cur_cln.execute(
                    """DELETE FROM raw_fundamental WHERE stock_id = ?
                       AND roe IS NULL AND gross_margin IS NULL AND net_margin IS NULL
                       AND debt_ratio IS NULL AND current_ratio IS NULL AND quick_ratio IS NULL
                       AND revenue_growth IS NULL AND profit_growth IS NULL
                       AND ocf_to_net_profit IS NULL
                       AND report_date > (
                           SELECT MAX(report_date) FROM raw_fundamental WHERE stock_id = ?
                           AND (roe IS NOT NULL OR gross_margin IS NOT NULL
                                OR net_margin IS NOT NULL OR debt_ratio IS NOT NULL
                                OR current_ratio IS NOT NULL OR revenue_growth IS NOT NULL
                                OR profit_growth IS NOT NULL OR ocf_to_net_profit IS NOT NULL)
                       )""",
                    (stock_id, stock_id),
                )
                cleaned = cur_cln.rowcount
                conn_cln.commit()
                conn_cln.close()
                if cleaned:
                    logger.info(f'[港股 {symbol}] 清理 PE/PB 占位行 {cleaned} 条（全指标NULL）')
            except Exception as e:
                logger.warning(f'[港股 {symbol}] 占位行清理异常(不阻塞): {e}')
        else:
            warnings.append('港股财务指标数据为空')
    except Exception as e:
        warnings.append(f'港股财务指标获取失败: {e}')
        logger.warning(f'[港股 {symbol}] 财务指标获取失败: {e}')

    # --- 估值指标 PE/PB（腾讯实时行情接口）---
    try:
        result = _fetch_valuation_tencent(symbol, 'hk_stock')
        if result is None:
            warnings.append('PE/PB获取失败（腾讯接口无响应）')
        else:
            pe, pb = result
            if pe is not None or pb is not None:
                conn2 = get_connection()
                cursor2 = conn2.cursor()
                # P0-HK-FUND-002：PE/PB 合并到最新财报行（对齐 A 股逻辑），
                # 不再 INSERT 新行，避免 data_adapter 只读到 PE/PB 而丢财报指标。
                # 019P M-5：改为取最新"含指标值"的真实财报行（排除全指标 NULL 的 PE/PB 占位行 R-3）
                cursor2.execute(
                    """SELECT report_date FROM raw_fundamental WHERE stock_id = ?
                       AND (roe IS NOT NULL OR gross_margin IS NOT NULL
                            OR net_margin IS NOT NULL OR debt_ratio IS NOT NULL
                            OR current_ratio IS NOT NULL OR revenue_growth IS NOT NULL
                            OR profit_growth IS NOT NULL OR ocf_to_net_profit IS NOT NULL)
                       ORDER BY report_date DESC LIMIT 1""",
                    (stock_id,),
                )
                latest_fund_row = cursor2.fetchone()
                if latest_fund_row:
                    cursor2.execute(
                        """
                        UPDATE raw_fundamental SET pe_ratio = ?, pb_ratio = ?
                        WHERE stock_id = ? AND report_date = ?
                    """,
                        (pe, pb, stock_id, latest_fund_row['report_date']),
                    )
                    logger.info(
                        f'[港股 {symbol}] PE/PB 已合并到财报 {latest_fund_row["report_date"]}: PE={pe}, PB={pb}'
                    )
                else:
                    # 无财报记录时才创建新行
                    today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                    cursor2.execute(
                        """
                        INSERT OR REPLACE INTO raw_fundamental
                        (stock_id, report_date, pe_ratio, pb_ratio)
                        VALUES (?, ?, ?, ?)
                    """,
                        (stock_id, today, pe, pb),
                    )
                    logger.info(f'[港股 {symbol}] 估值数据(无财报行,新建): PE={pe}, PB={pb}')
                conn2.commit()
                conn2.close()
            else:
                warnings.append('PE/PB数据为空')
    except Exception as e:
        warnings.append(f'PE/PB获取失败: {e}')
        logger.warning(f'[港股 {symbol}] PE/PB获取失败: {e}')

    # P0-HK-FUND-002：返回逻辑对齐 A 股三档（success/partial/failed）
    # 019P-A3：data_status message 前缀标注数据源（'港股EM财报+腾讯估值'）
    if saved_count > 0 and not warnings:
        if backfill_triggered:
            msg = '港股EM财报+腾讯估值: 财报补全(毛利率缺失触发)'
        else:
            msg = '港股EM财报+腾讯估值: 港股基本面数据采集成功'
        save_data_status(stock_id, 'fundamental', 'success', msg)
        return 'success', msg
    elif saved_count > 0 and warnings:
        save_data_status(
            stock_id,
            'fundamental',
            'partial',
            '港股EM财报+腾讯估值: 获取' + str(saved_count) + '条财务数据，缺失: ' + '; '.join(warnings),
        )
        return 'partial', f'获取{saved_count}条财务数据，缺失: ' + '; '.join(warnings)
    else:
        save_data_status(stock_id, 'fundamental', 'failed', '; '.join(warnings))
        return 'failed', '; '.join(warnings)


# ============================================================
# 019Y T2：估值数据 + 限售解禁 + baostock 财务备用源
#
# 大白话说明：
# - 估值（PE/PB/PS/PCF/股息率）是项目此前缺失的数据维度，单独存入新表 stock_valuation。
# - 降级链路：akshare（A股 stock_a_indicator_lg，1.18 版本不存在时自动回退
#   stock_value_em；港股 stock_hk_valuation_baidu）→ baostock（仅A股）→ 标记缺失。
# - baostock 登录/登出成对管理（批次级），登录一次全局复用，不逐只重复登录。
# - baostock 走 TCP socket，不经过 requests 全局 patch，天然隔离。
# - baostock 不支持港股：港股估值仍走 akshare。
# ============================================================
import atexit as _atexit_019y
import threading as _threading_019y_bs

_BS_LOGGED_IN = False
_BS_LOCK = _threading_019y_bs.Lock()


def _bs_ensure_login():
    """019Y：baostock 登录（幂等，全局只登录一次；线程安全）。
    返回 True=已登录/登录成功；False=登录失败。
    """
    global _BS_LOGGED_IN
    if _BS_LOGGED_IN:
        return True
    with _BS_LOCK:
        if _BS_LOGGED_IN:
            return True
        try:
            import baostock as bs
            lg = bs.login()
            if lg.error_code == '0':
                _BS_LOGGED_IN = True
                logger.info('[baostock] 登录成功（生命周期：批次级，全局复用）')
                return True
            logger.warning(f'[baostock] 登录失败: code={lg.error_code} msg={lg.error_msg}')
            return False
        except Exception as e:
            logger.warning(f'[baostock] 登录异常: {e}')
            return False


def _bs_logout():
    """019Y：baostock 登出（与登录成对，进程退出时兜底）"""
    global _BS_LOGGED_IN
    if not _BS_LOGGED_IN:
        return
    try:
        import baostock as bs
        bs.logout()
        _BS_LOGGED_IN = False
        logger.info('[baostock] 已登出（生命周期成对）')
    except Exception as e:
        logger.warning(f'[baostock] 登出异常: {e}')


_atexit_019y.register(_bs_logout)


def _bs_code(symbol, market):
    """A股代码 → baostock 代码（sz.000001 / sh.600276）。港股不支持返回 None。"""
    if market != 'a_stock':
        return None
    if symbol.startswith('6'):
        return 'sh.' + symbol
    if symbol.startswith(('0', '3')):
        return 'sz.' + symbol
    return None


def _pick_val(row, names, frags):
    """019Y：从 DataFrame 行取数——先精确匹配 names，再按 frags 子串匹配（兼容 akshare 列名漂移）。
    找不到或值为空返回 None。
    """
    for n in names:
        if n in row.index:
            v = row[n]
            if pd.notna(v):
                return v
    for f in frags:
        for n in row.index:
            if f in str(n):
                v = row[n]
                if pd.notna(v):
                    return v
    return None


def _fetch_valuation_akshare(symbol, market):
    """019Y T2：akshare 估值主源（A股+港股）。
    任务书指定 stock_a_indicator_lg，但本机 akshare 1.18.53 已无此接口
    （乐咕估值更名），自动回退同源接口 stock_value_em（东财估值，同 PE/PB/PS 口径）。
    返回最近一条 dict 或 None；异常/超时向上抛出由 fetch_valuation 降级 baostock。
    """
    def _ak_call():
        if market == 'a_stock':
            fn = getattr(ak, 'stock_a_indicator_lg', None)
            if fn is not None:
                return fn(symbol=symbol)
            return ak.stock_value_em(symbol=symbol)
        if market == 'hk_stock':
            fn = getattr(ak, 'stock_hk_valuation_baidu', None)
            if fn is not None:
                return fn(symbol=symbol)
            return None
        return None

    df, timed_out = _call_ak_with_timeout(_ak_call, f'{symbol} 估值')
    if timed_out:
        raise TimeoutError('akshare 估值接口超时')
    if df is None or len(df) == 0:
        return None
    row = df.iloc[-1]  # 接口按日期升序，取最新一行
    raw_date = _pick_val(row, ['数据日期', '日期', 'trade_date'], ['数据日期', '日期'])
    val = {
        'trade_date': str(raw_date)[:10] if raw_date is not None else None,
        'pe_ttm': _safe_num(_pick_val(row, ['PE(TTM)', 'pe_ttm'], ['PE(TTM)'])),
        'pe': _safe_num(_pick_val(row, ['PE(静)', 'PE(动)', 'pe'], ['PE(静)', 'PE(动)'])),
        'pb_mrq': _safe_num(_pick_val(row, ['市净率', 'pb_mrq', 'pb'], ['市净率'])),
        'ps_ttm': _safe_num(_pick_val(row, ['市销率', 'ps_ttm'], ['市销率'])),
        'ps': None,
        'pcf_ncf_ttm': _safe_num(_pick_val(row, ['市现率', 'pcf_ncf_ttm'], ['市现率'])),
        'dv_ttm': _safe_num(_pick_val(row, ['股息率'], ['股息率'])),
        'total_mv': _safe_num(_pick_val(row, ['总市值', 'total_mv'], ['总市值'])),
    }
    return val


def _fetch_valuation_baostock(symbol, market):
    """019Y T2：baostock 估值备用源（仅A股，peTTM/pbMRQ/psTTM/pcfNcfTTM）。
    返回最近一条交易日 dict 或 None（失败不抛异常）：
    {'trade_date','pe_ttm','pb_mrq','ps_ttm','pcf_ncf_ttm'}
    """
    code = _bs_code(symbol, market)
    if not code:
        return None
    if not _bs_ensure_login():
        return None
    try:
        import baostock as bs
        now = datetime.now(_CN_TZ).replace(tzinfo=None)
        start_d = (now - timedelta(days=7)).strftime('%Y-%m-%d')
        end_d = now.strftime('%Y-%m-%d')
        rs = bs.query_history_k_data_plus(
            code,
            'date,code,peTTM,pbMRQ,psTTM,pcfNcfTTM',
            start_date=start_d,
            end_date=end_d,
            frequency='d',
        )
        rows = []
        while (rs.error_code == '0') & rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            logger.warning(f'[baostock] {symbol} 估值返回空数据: {rs.error_msg}')
            return None
        last = rows[-1]
        return {
            'trade_date': last[0],
            'pe_ttm': _safe_num(last[2]),
            'pb_mrq': _safe_num(last[3]),
            'ps_ttm': _safe_num(last[4]),
            'pcf_ncf_ttm': _safe_num(last[5]),
        }
    except Exception as e:
        logger.warning(f'[baostock] {symbol} 估值获取异常: {e}')
        return None


def fetch_valuation(symbol, market, force_full=False):
    """019Y T2：采集估值数据（PE/PB/PS/PCF/股息率）存入 stock_valuation 表。
    降级链路：akshare → baostock（仅A股）→ 腾讯行情 PE/PB（仅港股，021C）→ 标记缺失。
    估值属低频数据（日级），同日跳过。
    返回: (状态, 消息)
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 日级低频：同日跳过
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT fetched_at FROM data_status
                   WHERE stock_id = ? AND dimension = 'valuation'
                   ORDER BY fetched_at DESC LIMIT 1""",
                (stock_id,),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['fetched_at']:
                last_date = str(row['fetched_at'])[:10]
                today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                if last_date >= today_str:
                    skip_msg = '同日跳过(估值当日已采集)'
                    save_data_status(stock_id, 'valuation', 'success', skip_msg)
                    logger.info(f'[{symbol}] {skip_msg}')
                    return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[{symbol}] 估值同日检查异常(降级为采集): {e}')

    val = None
    src = None
    # 主源：akshare（A股/港股）
    try:
        val = _fetch_valuation_akshare(symbol, market)
        if val and val.get('trade_date'):
            src = 'akshare'
            logger.info(f'[{symbol}] akshare 估值命中: {val["trade_date"]}')
    except Exception as e:
        logger.warning(f'[{symbol}] akshare估值失败(尝试baostock降级): {e}')
    # 备用源：baostock（仅A股）
    if not val:
        try:
            val = _fetch_valuation_baostock(symbol, market)
            if val and val.get('trade_date'):
                src = 'baostock'
                logger.info(f'[{symbol}] baostock 估值备用源命中: {val["trade_date"]}')
        except Exception as e:
            logger.warning(f'[{symbol}] baostock估值失败: {e}')

    # 021C：港股第二备源——腾讯行情 PE(TTM)/PB 实时快照。
    # 背景：akshare 港股 baidu 估值接口已失效（JSON 解析错误，2026-08-16 实测）、
    # baostock 不支持港股，导致港股估值恒失败（HK3690 等）。
    # 腾讯仅提供 PE/PB 两字段，其余字段保持缺失（诚实标注来源，不伪造）。
    if not val and market == 'hk_stock':
        try:
            pe, pb = _fetch_valuation_tencent(symbol, market)
            if pe is not None or pb is not None:
                val = {
                    'trade_date': None,  # 下方以最新K线日期为准（腾讯快照无日期字段）
                    'pe_ttm': pe,
                    'pb_mrq': pb,
                    'pe': None,
                    'ps_ttm': None,
                    'ps': None,
                    'pcf_ncf_ttm': None,
                    'dv_ttm': None,
                    'total_mv': None,
                }
                src = 'tencent'
                logger.info(f'[{symbol}] 腾讯估值备用源命中: PE={pe}, PB={pb}')
        except Exception as e:
            logger.warning(f'[{symbol}] 腾讯估值备用源失败: {e}')

    if not val or not val.get('trade_date'):
        if val and src == 'tencent':
            # 腾讯快照无日期：估值对应交易日 = 该股最新K线日期，避免周末脏日期
            try:
                conn_td = get_connection()
                td_row = conn_td.execute(
                    'SELECT MAX(trade_date) d FROM raw_kline WHERE stock_id=?', (stock_id,)
                ).fetchone()
                conn_td.close()
                if td_row and td_row['d']:
                    val['trade_date'] = str(td_row['d'])[:10]
                    logger.info(f'[{symbol}] 腾讯估值交易日取最新K线: {val["trade_date"]}')
            except Exception as e:
                logger.warning(f'[{symbol}] 读取最新K线日期失败: {e}')

    if not val or not val.get('trade_date'):
        fail_msg = 'akshare与baostock估值均失败'
        if market == 'hk_stock':
            # baostock 不支持港股，仅 akshare 一路失败（诚实标注，不误导）
            fail_msg = 'akshare与腾讯估值均失败（港股；baostock不支持港股）'
        save_data_status(stock_id, 'valuation', 'failed', fail_msg)
        return 'failed', fail_msg

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO stock_valuation
        (stock_id, trade_date, pe_ttm, pe, pb_mrq, ps_ttm, ps, pcf_ncf_ttm, dv_ttm, total_mv, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            stock_id,
            val['trade_date'],
            val.get('pe_ttm'),
            val.get('pe'),
            val.get('pb_mrq'),
            val.get('ps_ttm'),
            val.get('ps'),
            val.get('pcf_ncf_ttm'),
            val.get('dv_ttm'),
            val.get('total_mv'),
            src,
        ),
    )
    conn.commit()
    conn.close()
    msg = (
        f'估值已入库({src}): PE_TTM={val.get("pe_ttm")}, '
        f'PB={val.get("pb_mrq")}, PS_TTM={val.get("ps_ttm")}'
    )
    save_data_status(stock_id, 'valuation', 'success', msg)
    logger.info(f'[{symbol}] {msg}')
    return 'success', msg


def fetch_fundamental_baostock(symbol):
    """019Y T2：baostock 财务数据备用源（仅A股）。
    仅在 akshare 财务接口（abstract / analysis_indicator）全部失败时降级使用。
    query_profit_data 逐季度获取，返回 [(report_date, {db_col: value}), ...] 最新在前。
    roeAvg/npMargin/gpMargin 为小数比例，×100 转百分比。
    """
    code = _bs_code(symbol, 'a_stock')
    if not code:
        return []
    if not _bs_ensure_login():
        return []
    try:
        import baostock as bs
        now = datetime.now(_CN_TZ)
        rows_out = []
        # 近 8 个季度（2 年），最新在前
        quarters = []
        for back in range(8):
            total = now.year * 4 + (now.month - 1) // 3 - back
            y, q = divmod(total, 4)
            if q == 0:
                y, q = y - 1, 4
            quarters.append((y, q))
        for y, q in quarters:
            rs = bs.query_profit_data(code=code, year=y, quarter=q)
            lst = []
            while (rs.error_code == '0') & rs.next():
                lst.append(rs.get_row_data())
            if not lst:
                continue
            rec = dict(zip(rs.fields, lst[0]))
            stat_date = str(rec.get('statDate', ''))
            if not stat_date:
                continue
            report_date = stat_date[:10]
            vals = {}
            roe = _safe_num(rec.get('roeAvg'))
            np_m = _safe_num(rec.get('npMargin'))
            gp_m = _safe_num(rec.get('gpMargin'))
            if roe is not None:
                vals['roe'] = round(roe * 100, 2)
            if np_m is not None:
                vals['net_margin'] = round(np_m * 100, 2)
            if gp_m is not None:
                vals['gross_margin'] = round(gp_m * 100, 2)
            if vals:
                rows_out.append((report_date, vals))
        return rows_out
    except Exception as e:
        logger.warning(f'[baostock] {symbol} 财务备用源异常: {e}')
        return []


def fetch_restricted_release(symbol, market='a_stock', force_full=False):
    """019Y T2：采集个股限售解禁明细（风险因子，事件级）存入 stock_restricted_release 表。
    数据源：akshare stock_restricted_release_queue_em（东方财富个股解禁时间表）。
    当日快照语义：每次采集整表按 stock_id 重建（DELETE + INSERT）。
    仅 A股；港股无免费解禁接口返回 skipped。
    返回: (状态, 消息)
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'
    if market != 'a_stock':
        save_data_status(stock_id, 'restricted_release', 'skipped', '限售解禁仅A股')
        return 'skipped', '限售解禁仅A股'

    # 日级低频：同日跳过
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT fetched_at FROM data_status
                   WHERE stock_id = ? AND dimension = 'restricted_release'
                   ORDER BY fetched_at DESC LIMIT 1""",
                (stock_id,),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['fetched_at']:
                last_date = str(row['fetched_at'])[:10]
                today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                if last_date >= today_str:
                    skip_msg = '同日跳过(限售解禁当日已采集)'
                    save_data_status(stock_id, 'restricted_release', 'success', skip_msg)
                    logger.info(f'[{symbol}] {skip_msg}')
                    return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[{symbol}] 限售解禁同日检查异常(降级为采集): {e}')

    try:
        df, timed_out = _call_ak_with_timeout(
            lambda: ak.stock_restricted_release_queue_em(symbol=symbol),
            f'{symbol} 限售解禁',
        )
        if timed_out:
            raise TimeoutError('限售解禁接口超时')
        conn = get_connection()
        cursor = conn.cursor()
        # 当日快照：先清空该股旧记录，再写入本次最新解禁列表
        cursor.execute('DELETE FROM stock_restricted_release WHERE stock_id = ?', (stock_id,))
        saved = 0
        if df is not None and len(df) > 0:
            for _, r in df.iterrows():
                raw_date = _pick_val(r, ['解禁时间'], ['解禁时间'])
                release_date = str(raw_date)[:10] if raw_date is not None else None
                if not release_date:
                    continue
                ratio = _safe_num(_pick_val(r, ['占总市值比例', '占解禁前总股本比例'], ['占总市值比例', '总股本比例']))
                if ratio is not None:
                    ratio = round(ratio * 100, 2)  # 小数比例 → 百分比
                cursor.execute(
                    """
                    INSERT INTO stock_restricted_release
                    (stock_id, release_date, release_type, release_shares,
                     actual_shares, actual_mv, release_ratio, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        release_date,
                        str(_pick_val(r, ['限售股类型', '解禁类型'], ['限售股类型', '解禁类型']) or '')
                        or None,
                        _safe_num(_pick_val(r, ['解禁数量'], ['解禁数量'])),
                        _safe_num(_pick_val(r, ['实际解禁数量'], ['实际解禁数量'])),
                        _safe_num(_pick_val(r, ['实际解禁数量市值', '实际解禁市值'], ['解禁市值'])),
                        ratio,
                        'akshare',
                    ),
                )
                saved += 1
        conn.commit()
        conn.close()
        msg = f'限售解禁已入库(akshare): {saved} 条记录'
        save_data_status(stock_id, 'restricted_release', 'success', msg)
        logger.info(f'[{symbol}] {msg}')
        return 'success', msg
    except Exception as e:
        save_data_status(stock_id, 'restricted_release', 'failed', str(e))
        logger.warning(f'[{symbol}] 限售解禁采集失败: {e}')
        return 'failed', str(e)


# ============================================================
# 资金面数据采集
# 主数据源：东方财富 stock_individual_fund_flow（通过akshare调用，直连可用）
#   - 优势：返回120天历史资金流向数据，含主力/超大单/大单/中单/小单
#   - 限制：仅支持A股
# 备用数据源：东方财富 push2 接口（部分网络环境被封，仅作回退）
# ============================================================


# ============================================================
# P0-CAPITAL-001：同花顺全市场批量资金流向源
# 单次调用返回全 A 股当日资金流向（5197+只），从根因消除东方财富逐只限流。
# 1 小时缓存（避免重复下载，单次约 6-12s）。
# 018: 同花顺净额为辅助指标（全部资金净流入），非主力净流入。
# 主力净流入由东方财富逐只采集提供（含分层：超大单/大单/中单/小单）。
# ============================================================
_THS_CAPITAL_CACHE = {'data': None, 'ts': 0.0}  # 模块级缓存：{DataFrame, 时间戳}
_THS_CAPITAL_CACHE_TTL = 3600  # 缓存有效期（秒）= 1 小时
_THS_CONSECUTIVE_FAIL_COUNT = 0  # FIX-B：THS连续失败计数
_THS_FAIL_THRESHOLD = 3  # FIX-B：连续失败阈值，达到后标记降级
_THS_REQUEST_TIMEOUT = 60  # 019I：单次 THS 接口请求超时（秒）

# ============================================================
# 019C：东方财富（EM）回退循环优化常量
# 用于 fetch_capital_flow_batch 中 THS批量源失败后的逐只回退循环
# 机制：错峰 → 分批 → 退避 → 冷却 → 熔断 → 整体软超时
# ============================================================
_EM_CONSECUTIVE_FAIL_COUNT = 0  # 进程级连续失败计数（R-4：Flask不重启时跨次批量生效）
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
_EM_LAST_REQUEST_TS = 0.0    # 东财全局最小请求间隔记录
_EM_MIN_INTERVAL_SECONDS = 0.5  # 东财请求全局最小间隔（社区阈值：<5 次/秒）
# 020C：熔断状态持久化文件（重启后仍记忆"东财不可用"，避免每轮从头挨 4 轮重试）
_EM_BAN_STATE_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'logs', 'em_ban_state.json'
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


def _rotate_em_host(url):
    """东财 push2/push2his 编号子域轮换（1~99）：不同边缘节点可绕部分 WAF 拦截。"""
    for base in ('//push2.eastmoney.com/', '//push2his.eastmoney.com/'):
        if base in url:
            return url.replace(base, f'//{_random.randint(1, 99)}.{base[2:]}')
    return url


# ============================================================
# 020A：腾讯自选股（westock）资金面备用层
# 数据源为腾讯自选股（社区实测不封 IP），主力净流入口径 = 超大单+大单（与东财同概念，
# 探针实测 600276：MainNetFlow == JumboNetFlow + BlockNetFlow，精确相等）。
# 交付方式：npm CLI（westock-data-clawhub@1.0.4 版本锁定）经腾讯共享签名网关；
# 探针审计结论：CLI 仅访问 proxy.finance.qq.com 单域名，无其他网络行为。
# 位置：东财三层 → 腾讯 westock → 新浪主力口径 → 估算兜底。
# 共享通道存在失效可能 → 连续失败进入冷却 + 失败自动降级，不阻塞主链路。
# ============================================================
_WESTOCK_PACKAGE = 'westock-data-clawhub@1.0.4'
_WESTOCK_TIMEOUT_SECONDS = 45    # npx 冷启动较慢，超时放宽
_WESTOCK_COOLDOWN_SECONDS = 1800  # 连续失败后的冷却时长（30 分钟）
_WESTOCK_COOLDOWN_FAIL_N = 3     # 连续失败 N 次进入冷却
_WESTOCK_COOLDOWN_UNTIL = 0.0    # 冷却截止时间戳
_WESTOCK_CONSECUTIVE_FAIL = 0    # 连续失败计数


def _westock_cooldown_active():
    """westock 层是否处于冷却期。"""
    return time.time() < _WESTOCK_COOLDOWN_UNTIL


def _westock_record_failure():
    """记录 westock 层失败；连续失败达阈值进入冷却。"""
    global _WESTOCK_CONSECUTIVE_FAIL, _WESTOCK_COOLDOWN_UNTIL
    _WESTOCK_CONSECUTIVE_FAIL += 1
    if _WESTOCK_CONSECUTIVE_FAIL >= _WESTOCK_COOLDOWN_FAIL_N:
        _WESTOCK_COOLDOWN_UNTIL = time.time() + _WESTOCK_COOLDOWN_SECONDS
        logger.warning(
            f'[westock] 连续失败 {_WESTOCK_CONSECUTIVE_FAIL} 次，'
            f'进入冷却 {_WESTOCK_COOLDOWN_SECONDS // 60} 分钟（期间跳过该层）'
        )
        _WESTOCK_CONSECUTIVE_FAIL = 0


def _westock_reset():
    """westock 层成功后重置连续失败计数。"""
    global _WESTOCK_CONSECUTIVE_FAIL
    _WESTOCK_CONSECUTIVE_FAIL = 0


def _westock_cli_query(command, codes, date_str=''):
    """调用 westock CLI（npx 子进程），返回 Markdown 输出文本；失败返回 None。"""
    import shutil
    import subprocess as _sp

    if not shutil.which('npx') and not shutil.which('npm'):
        logger.warning('[westock] 本机未安装 npx/node，跳过腾讯自选股资金面层')
        return None
    # npx 在 Windows 上是 npx.cmd 批处理，CreateProcess 不能直接执行，须经 cmd /c 包装
    cmd = ['cmd', '/c', 'npx', '-y', _WESTOCK_PACKAGE, command, codes]
    if date_str:
        cmd += ['--date', date_str]
    try:
        proc = _sp.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=_WESTOCK_TIMEOUT_SECONDS,
            creationflags=_sp.CREATE_NO_WINDOW,
        )
    except (_sp.TimeoutExpired, OSError) as e:
        logger.warning(f'[westock] CLI 调用失败: {e}')
        return None
    out = (proc.stdout or '').strip()
    if proc.returncode != 0 or not out:
        logger.warning(
            f'[westock] CLI 返回码={proc.returncode}，无有效输出'
            f'（stderr 摘要: {(proc.stderr or "")[:120]}）'
        )
        return None
    return out


def _parse_westock_markdown(text):
    """解析 westock CLI 的 Markdown 表格输出，返回第一张表第一行数据 {列名: 值}。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith('|')]
    if len(lines) < 2:
        return None
    headers = [h.strip() for h in lines[0].strip('|').split('|')]
    # 过滤分隔行（|---|:--:|---|）
    data_lines = [ln for ln in lines[1:] if not set(ln) <= set('|-: ')]
    if not data_lines:
        return None
    cells = [c.strip() for c in data_lines[0].strip('|').split('|')]
    if len(cells) != len(headers):
        return None
    return dict(zip(headers, cells))


def _fetch_capital_flow_westock(symbol, market, date_str=''):
    """
    020A：腾讯自选股资金面备用层（A股 asfund / 港股 hkfund）。
    020I：date_str 非空时追加 --date 参数逐日查询历史（补采调度器回填用），
    并严格校验返回 EndDate == date_str（与新浪 M-2 同一红线：严禁取错日）；
    不匹配只记录日志、不计入 westock 连续失败（避免回填拖垮实时链路）。
    返回单日 dict {trade_date, main_net_inflow, super_large_net, large_net,
    medium_net, small_net} 或 None。
    - A股返回四档分解（主力=超大+大，与东财同口径）；港股仅主力净额+总额。
    - 金额元→万元（与 raw_capital_flow 全库口径一致）；港股为万港元。
    """
    if _westock_cooldown_active():
        logger.info(f'[{symbol}] westock 冷却期，跳过腾讯自选股资金面层')
        return None
    prefix, code = _get_tencent_prefix(symbol, market)
    command = 'hkfund' if market == 'hk_stock' else 'asfund'
    wcode = f'{prefix}{code}'
    try:
        text = _westock_cli_query(command, wcode, date_str=date_str)
        if not text:
            _westock_record_failure()
            return None
        row = _parse_westock_markdown(text)
        if not row:
            logger.warning(f'[{symbol}] westock 输出无法解析: {text[:150]}')
            _westock_record_failure()
            return None
        if date_str and (row.get('EndDate') or '').strip() != date_str:
            logger.warning(
                f'[{symbol}] westock --date {date_str} 返回 EndDate={row.get("EndDate")} 不匹配，放弃'
            )
            return None
        main_net = _safe_float_wan(row.get('MainNetFlow'))
        jumbo = _safe_float_wan(row.get('JumboNetFlow'))
        block = _safe_float_wan(row.get('BlockNetFlow'))
        mid = _safe_float_wan(row.get('MidNetFlow'))
        small = _safe_float_wan(row.get('SmallNetFlow'))
        if all(v is None for v in (main_net, jumbo, block, mid, small)):
            logger.warning(f'[{symbol}] westock 返回字段全空，不采用')
            _westock_record_failure()
            return None
        # 020O：主力净流入占比 = 主力净额 ÷ 成交额；
        # 成交额 = 主力买入+主力卖出+散户买入+散户卖出（A股 MainInFlow 系 / 港股 MainIn 系）。
        _main_in = _safe_float_wan(
            row.get('MainInFlow') if row.get('MainInFlow') is not None else row.get('MainIn')
        )
        _main_out = _safe_float_wan(
            row.get('MainOutFlow') if row.get('MainOutFlow') is not None else row.get('MainOut')
        )
        _retail_in = _safe_float_wan(
            row.get('RetailInFlow') if row.get('RetailInFlow') is not None else row.get('RetailIn')
        )
        _retail_out = _safe_float_wan(
            row.get('RetailOutFlow') if row.get('RetailOutFlow') is not None else row.get('RetailOut')
        )
        _main_pct = None
        if all(v is not None for v in (_main_in, _main_out, _retail_in, _retail_out)):
            _turnover_wan = _main_in + _main_out + _retail_in + _retail_out
            if _turnover_wan and main_net is not None:
                _main_pct = round(main_net / _turnover_wan * 100, 2)
        # 020O：全资金净流入——仅港股 hkfund 提供（TotalNetFlow=主力+散户主动净额，
        # 有实际意义）；A股 asfund 散户为被动镜像、全口径恒等0，返回 None 不写入。
        _total_net = _safe_float_wan(row.get('TotalNetFlow'))
        _westock_reset()
        return {
            'trade_date': row.get('EndDate') or '',
            'main_net_inflow': main_net,
            'main_net_inflow_pct': _main_pct,
            'super_large_net': jumbo,
            'large_net': block,
            'medium_net': mid,
            'small_net': small,
            'total_net_inflow': _total_net,
        }
    except Exception as e:
        logger.warning(f'[{symbol}] westock 资金面层失败: {e}')
        _westock_record_failure()
        return None

# ============================================================
# 019Q：新浪资金流（lscjfb 主力口径）常量与模块级超时包装
# _call_with_timeout 复制自 019I 嵌套版（_fetch_capital_flow_ths_batch 内部 L1424-1436），
# 提升为模块级并新增 timeout 参数（M-3/D-4/M-10）；既有 THS 嵌套版零改动（避免回归面扩大）。
# 新浪网络调用必须走本模块级 _call_with_timeout，严禁裸调用（含 https 回退的第二次请求）。
# ============================================================
_SINA_REQUEST_TIMEOUT = 15  # 019Q：单次新浪接口请求超时（秒），探针实测 0.2~0.6s


def _call_with_timeout(fn, label, timeout=_SINA_REQUEST_TIMEOUT):
    """019Q：模块级 daemon 线程包装网络调用，超时返回 (None, True)，正常返回 (result, False)"""
    import threading as _threading_019Q

    box = {}
    t = _threading_019Q.Thread(target=lambda: box.update(r=fn()), daemon=True)
    t.start()
    t.join(timeout=timeout)
    if t.is_alive():
        logger.warning(f'[网络调用] {label} 超时({timeout}s)，跳过')
        return None, True
    return box.get('r'), False

# B11-API-DEDUP：股东增减持接口缓存（10分钟TTL，避免批量时重复调用全市场接口）
_holder_cache = None
_holder_cache_time = None


def _fetch_capital_flow_ths_batch():
    """
    调用同花顺 stock_fund_flow_individual() 获取全 A 股当日资金流向。
    带模块级缓存（1小时TTL），避免批量场景重复下载。
    FIX-B：THS失败时重试1次（5秒间隔），并尝试备选接口 stock_individual_fund_flow_rank()。
    返回 pandas.DataFrame 或 None。
    """
    global _THS_CONSECUTIVE_FAIL_COUNT

    now_ts = time.time()
    if (
        _THS_CAPITAL_CACHE['data'] is not None
        and (now_ts - _THS_CAPITAL_CACHE['ts']) < _THS_CAPITAL_CACHE_TTL
    ):
        logger.info('[同花顺批量] 命中内存缓存（1小时TTL内），直接复用')
        return _THS_CAPITAL_CACHE['data']

    # FIX-B：连续失败达阈值时跳过THS，直接返回None（由调用方回退EM逐只）
    if _THS_CONSECUTIVE_FAIL_COUNT >= _THS_FAIL_THRESHOLD:
        logger.warning(
            f'[同花顺批量] 连续失败已达阈值({_THS_CONSECUTIVE_FAIL_COUNT})，跳过THS，回退EM逐只'
        )
        return None

    # 019I：THS 接口调用增加超时保护，防止服务器不响应时无限阻塞
    # M-1 修正：禁止使用 with ThreadPoolExecutor（with 退出时 shutdown(wait=True)
    #          会 join 挂死线程，修复无效——经架构师运行时实验 + PM 独立复现确认）
    # 改用 daemon 线程 join(timeout) 模式：
    #   - daemon 线程不参与解释器退出 join，进程退出不被阻塞（R-1 消除）
    #   - t.join(timeout=N) 超时后立即返回，不等待挂死线程
    import threading as _threading_019I

    def _call_with_timeout(fn, label):
        """019I：daemon 线程包装 THS 接口调用，超时返回 (None, True)，正常返回 (result, False)"""
        box = {}
        t = _threading_019I.Thread(
            target=lambda: box.update(r=fn()),
            daemon=True,
        )
        t.start()
        t.join(timeout=_THS_REQUEST_TIMEOUT)
        if t.is_alive():
            logger.warning(f'[同花顺批量] {label} 超时({_THS_REQUEST_TIMEOUT}s)，跳过')
            return None, True
        return box.get('r'), False

    # FIX-B：主接口 stock_fund_flow_individual()
    df, _primary_timed_out = _call_with_timeout(_try_ths_primary, '主接口')

    # 019I M-2：主接口超时（hang）视为服务器不响应，跳过重试直接尝试备选
    #（THS 阶段上界 185s→120s；非超时的普通失败仍按 FIX-B 重试1次）
    if df is None and not _primary_timed_out:
        logger.info('[同花顺批量] 主接口失败，5秒后重试1次...')
        time.sleep(5)
        df, _ = _call_with_timeout(_try_ths_primary, '主接口(重试)')

    # FIX-B：重试仍失败时，尝试备选接口 stock_individual_fund_flow_rank()
    if df is None:
        logger.info('[同花顺批量] 重试仍失败，尝试备选接口 stock_individual_fund_flow_rank()...')
        df, _ = _call_with_timeout(_try_ths_rank_backup, '备选接口')

    if df is not None:
        _THS_CONSECUTIVE_FAIL_COUNT = 0  # 成功则重置计数
        _THS_CAPITAL_CACHE['data'] = df
        _THS_CAPITAL_CACHE['ts'] = time.time()
        logger.info(f'[同花顺批量] 获取成功: {len(df)} 只股票当日资金流向')
        return df

    # 全部失败
    _THS_CONSECUTIVE_FAIL_COUNT += 1
    logger.warning(f'[同花顺批量] 全部接口失败，连续失败计数={_THS_CONSECUTIVE_FAIL_COUNT}')
    return None


def _try_ths_primary():
    """FIX-B：主接口 ak.stock_fund_flow_individual()"""
    try:
        logger.info('[同花顺批量] 请求 stock_fund_flow_individual()（全市场资金流向）...')
        df = ak.stock_fund_flow_individual()
        if df is None or df.empty:
            logger.warning('[同花顺批量] 主接口返回空数据')
            return None
        return df
    except Exception as e:
        logger.warning(f'[同花顺批量] 主接口获取失败: {e}')
        return None


def _try_ths_rank_backup():
    """FIX-B：备选接口 ak.stock_individual_fund_flow_rank(indicator='今日')
    列名与主接口不同，统一映射为 {股票代码, 净额, 成交额} 供后续处理。"""
    try:
        logger.info('[同花顺批量] 请求 stock_individual_fund_flow_rank(indicator=今日)...')
        df = ak.stock_individual_fund_flow_rank(indicator='今日')
        if df is None or df.empty:
            logger.warning('[同花顺批量] 备选接口返回空数据')
            return None
        # 列名映射：备选接口 → 主接口格式
        col_map = {
            '代码': '股票代码',
            '今日主力净流入-净额': '净额',
            '今日成交额': '成交额',
        }
        rename = {k: v for k, v in col_map.items() if k in df.columns}
        if rename:
            df = df.rename(columns=rename)
        # 确保有核心列
        if '股票代码' not in df.columns:
            logger.warning(f'[同花顺批量] 备选接口缺少股票代码列，实际列: {list(df.columns)}')
            return None
        logger.info(f'[同花顺批量] 备选接口获取成功: {len(df)} 行')
        return df
    except Exception as e:
        logger.warning(f'[同花顺批量] 备选接口获取失败: {e}')
        return None


def _em_batch_collect(symbols, log_prefix='EM回退', progress_cb=None):
    """
    019C/019E 共享：EM 逐只采集循环（错峰/分批/退避/冷却/熔断/软超时六项机制）。
    直接沿用共享常量 _EM_INTER_DELAY_RANGE ~ _EM_FALLBACK_TOTAL_CAP_SECONDS
    及模块级计数器 _EM_CONSECUTIVE_FAIL_COUNT，不新增平行常量。

    Args:
        symbols: list[str] 待采集 A 股代码列表
        log_prefix: str 日志前缀（'EM回退' / '资金面补采'，QA 依赖区分）
        progress_cb: 可选进度回调 progress_cb(idx, total, symbol)，
                     每只股票开始采集前调用（供日报进度文件逐只更新，动效不再长时间静止）

    Returns:
        dict: {'success_count': n, 'fail_count': n, 'source': str}
    """
    global _EM_CONSECUTIVE_FAIL_COUNT
    em_success = 0
    em_fail = 0
    start_ts = time.time()
    cooldown_done = False  # 本轮冷却是否已触发（避免重复暂停）
    timed_out = False
    circuit_broken = False
    remaining = list(symbols)
    total = len(remaining)

    for idx, sym in enumerate(remaining):
        # 进度回调：每只开始前通知（EM 逐只阶段可能耗时 30 分钟+，前端需可见进展）
        if progress_cb:
            try:
                progress_cb(idx, total, sym)
            except Exception:
                pass  # 进度回调失败不影响采集
        # --- 6. 整体软超时检查（每只开始前） ---
        elapsed = time.time() - start_ts
        if elapsed > _EM_FALLBACK_TOTAL_CAP_SECONDS:
            unprocessed = remaining[idx:]
            logger.warning(
                f'[{log_prefix}] 整体软超时({int(elapsed)}s>{_EM_FALLBACK_TOTAL_CAP_SECONDS}s)，'
                f'终止剩余 {len(unprocessed)} 只未采集: {unprocessed}'
            )
            em_fail += len(unprocessed)
            timed_out = True
            break

        # --- 5. 熔断检查（模块级计数 R-3/R-4） ---
        if _EM_CONSECUTIVE_FAIL_COUNT >= _EM_CIRCUIT_BREAK_N:
            unprocessed = remaining[idx:]
            logger.warning(
                f'[{log_prefix}] 熔断触发（连续失败{_EM_CONSECUTIVE_FAIL_COUNT}'
                f'>={_EM_CIRCUIT_BREAK_N}），'
                f'终止本轮回退，剩余 {len(unprocessed)} 只未采集: {unprocessed}'
            )
            _em_record_ban()  # 019Z：进入冷却期，后续东财资金面请求直接走备用源
            em_fail += len(unprocessed)
            circuit_broken = True
            break

        # --- 2. 分批间隔（每_BATCH_SIZE只进入新批次） ---
        if idx > 0 and idx % _EM_BATCH_SIZE == 0:
            batch_gap = _random.uniform(*_EM_BATCH_GAP_RANGE)
            logger.info(
                f'[{log_prefix}] 进入第{idx // _EM_BATCH_SIZE + 1}批'
                f'（第{idx + 1}只），批间停顿{batch_gap:.1f}s'
            )
            time.sleep(batch_gap)
        elif idx > 0:
            # --- 1. 错峰 + 3. 退避 ---
            base_delay = _random.uniform(*_EM_INTER_DELAY_RANGE)
            if _EM_CONSECUTIVE_FAIL_COUNT > 0:
                delay = min(
                    base_delay * (2 ** _EM_CONSECUTIVE_FAIL_COUNT),
                    _EM_BACKOFF_CAP_SECONDS,
                )
                logger.info(
                    f'[{log_prefix}] {sym} 退避延迟{delay:.1f}s'
                    f'（连续失败{_EM_CONSECUTIVE_FAIL_COUNT}次，'
                    f'基础{base_delay:.1f}s×2^{_EM_CONSECUTIVE_FAIL_COUNT}）'
                )
            else:
                delay = base_delay
                logger.info(f'[{log_prefix}] {sym} 错峰延迟{delay:.1f}s')
            time.sleep(delay)

        # --- 4. 冷却（连续失败≥阈值时额外暂停一次） ---
        if (
            _EM_CONSECUTIVE_FAIL_COUNT >= _EM_COOLDOWN_FAIL_N
            and not cooldown_done
        ):
            logger.warning(
                f'[{log_prefix}] 连续失败{_EM_CONSECUTIVE_FAIL_COUNT}'
                f'>={_EM_COOLDOWN_FAIL_N}，'
                f'冷却暂停{_EM_COOLDOWN_SECONDS}s后继续...'
            )
            time.sleep(_EM_COOLDOWN_SECONDS)
            cooldown_done = True

        # --- 采集 ---
        try:
            result = fetch_capital_flow(sym, 'a_stock')
            if result and result[0] == 'success':
                em_success += 1
                if _EM_CONSECUTIVE_FAIL_COUNT > 0:
                    logger.info(
                        f'[{log_prefix}] {sym} 成功，连续失败计数重置'
                        f'({_EM_CONSECUTIVE_FAIL_COUNT}→0)'
                    )
                # 020D：仅当成功来自东财源时才重置计数/解除熔断——
                # westock/新浪顶替成功不等于"东财恢复"，否则会形成
                # "westock 成功→解除熔断→下一只又挨东财 4 轮重试"的乒乓循环。
                if '东方财富' in (result[1] or ''):
                    _EM_CONSECUTIVE_FAIL_COUNT = 0  # 7. 计数重置（R-3：含同日跳过）
                    _em_clear_ban()  # 019Z：东财恢复即解除熔断冷却
                else:
                    logger.info(
                        f'[{log_prefix}] {sym} 非东财源成功（{result[1]}），'
                        '熔断冷却保持生效'
                    )
                cooldown_done = False  # 成功后重置冷却标记
            else:
                em_fail += 1
                _EM_CONSECUTIVE_FAIL_COUNT += 1
                logger.warning(
                    f'[{log_prefix}] {sym} 采集失败'
                    f'(result={result[0] if result else "None"})，'
                    f'连续失败计数={_EM_CONSECUTIVE_FAIL_COUNT}'
                )
        except Exception as e:
            em_fail += 1
            _EM_CONSECUTIVE_FAIL_COUNT += 1
            logger.warning(
                f'[{log_prefix}] {sym} 采集异常: {e}，'
                f'连续失败计数={_EM_CONSECUTIVE_FAIL_COUNT}'
            )

    # 构造返回值（标注终止原因）
    source = f'EM逐只({log_prefix}'
    if timed_out:
        source += '，软超时终止'
    if circuit_broken:
        source += f'，熔断终止(EM连续失败={_EM_CONSECUTIVE_FAIL_COUNT})'
    source += f'，成功{em_success}/失败{em_fail})'
    return {
        'success_count': em_success,
        'fail_count': em_fail,
        'source': source,
    }


def fetch_capital_flow_batch(a_stock_symbols, progress_cb=None):
    """
    018改造：同花顺批量预取 — 仅写入辅助指标 ths_net_inflow。
    同花顺"净额"= 全部资金净流入（总主动买入-总主动卖出），非主力净流入。
    本函数不写入 main_net_inflow / main_net_inflow_pct；
    主力净流入链路为：东财三层 → 新浪 lscjfb 主力口径(sina_main) → 估算兜底（仅展示不参评）；
    019S 起不再使用同花顺顶替主力净流入（ths_total 仅为历史存量，不产生新顶替行）。
    同花顺净额作为辅助指标，用于判断主力与散户行为背离。

    Args:
        a_stock_symbols: list[str]，A 股代码列表（如 ['600276','000333',...]）
        progress_cb: 可选进度回调（EM 回退逐只采集阶段每只调用），
                     用于日报进度文件逐只更新

    Returns:
        dict: {'success_count': n, 'fail_count': n, 'source': '同花顺批量(辅助指标)'}
    """
    # 019G：交易日校验 — 周末（周六/周日）跳过 THS 批量预取（含补采），
    # 避免非交易日 THS 接口返回旧数据被写入。法定节假日落在工作日时
    # 仍执行（THS 返回前一交易日数据，低概率可接受）。
    now = datetime.now(_CN_TZ)
    if now.weekday() >= 5:  # 5=周六, 6=周日
        logger.info(f'[同花顺批量] 非交易日（{now.strftime("%A")}），跳过 THS 批量预取（含补采）')
        return {
            'success_count': 0, 'fail_count': 0,
            'source': '同花顺批量(非交易日跳过)',
            'skipped': True, 'reason': 'non_trading_day'
        }

    if not a_stock_symbols:
        return {'success_count': 0, 'fail_count': 0, 'source': '同花顺批量(空列表)'}

    today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    df = _fetch_capital_flow_ths_batch()
    if df is None:
        # FIX-B：THS不可用时回退EM逐只采集（019C六项机制增强，已提取为_em_batch_collect共享函数）
        logger.warning('[同花顺批量] 批量源不可用（含重试+备选均失败），回退EM逐只采集（019C增强）')
        return _em_batch_collect(a_stock_symbols, log_prefix='EM回退', progress_cb=progress_cb)

    # 同花顺股票代码列可能为 int64（000333→333）或字符串，统一规整为6位字符串
    def _norm_code(v):
        s = str(v).strip()
        if s.replace('.', '').isdigit():
            return str(int(float(s))).zfill(6)
        return s

    code_col = '股票代码'
    if code_col not in df.columns:
        logger.warning(f'[同花顺批量] 缺少 {code_col} 列，实际列: {list(df.columns)}')
        return {
            'success_count': 0,
            'fail_count': len(a_stock_symbols),
            'source': '同花顺批量(字段异常)',
        }

    df_norm = df.copy()
    df_norm['_code6'] = df_norm[code_col].apply(_norm_code)

    success_count = 0
    fail_count = 0
    conn = get_connection()
    cursor = conn.cursor()

    for symbol in a_stock_symbols:
        stock_id = get_stock_id(symbol, 'a_stock')
        if not stock_id:
            fail_count += 1
            continue

        rows = df_norm[df_norm['_code6'] == symbol]
        if rows.empty:
            fail_count += 1
            logger.info(f'[同花顺批量] {symbol} 未在批量结果中命中')
            continue

        row = rows.iloc[0]
        # 净额为中文金额格式（如"-6.78亿"/"3.19亿"），解析后单位为元，÷1e4 转万元
        main_net_yuan = _parse_cn_amount(row.get('净额'))

        if main_net_yuan is None:
            fail_count += 1
            continue

        ths_net = round(main_net_yuan / 1e4, 2)  # 元→万元

        # 018: 仅写入辅助字段 ths_net_inflow，不影响 main_net_inflow
        # 使用 UPDATE（当天已有东财数据时）或 INSERT OR IGNORE（当天无数据时）
        cursor.execute(
            """
            UPDATE raw_capital_flow SET ths_net_inflow = ?
            WHERE stock_id = ? AND trade_date = ?
        """,
            (ths_net, stock_id, today_str),
        )
        if cursor.rowcount == 0:
            # 当天尚无东财数据，插入占位行（仅含 ths_net_inflow）
            cursor.execute(
                """
                INSERT OR IGNORE INTO raw_capital_flow
                (stock_id, trade_date, ths_net_inflow)
                VALUES (?, ?, ?)
            """,
                (stock_id, today_str, ths_net),
            )
        success_count += 1

    conn.commit()
    conn.close()
    logger.info(
        f'[同花顺批量] 辅助指标写入完成: 成功 {success_count}/{len(a_stock_symbols)}，source=同花顺批量(辅助指标)'
    )

    # ============================================================
    # 019E Task 1：批量补采正向触发机制（D1）
    # THS 批量成功后，检查哪些股票仍缺少当日真实资金面数据，
    # 对缺失股票执行 EM 逐只补采（完整复用 019C 六项机制）。
    # 评审 E-2：补采清单 = 输入列表 - 已有真实数据的股票
    # ============================================================
    import inspect as _inspect
    try:
        _caller_file = _inspect.stack()[1].filename
        _trigger_source = '日报批次' if 'daily_report' in _caller_file else 'batch-analyze'
    except Exception:
        _trigger_source = 'batch-analyze'

    # 补采清单生成（评审 E-2 裁定）
    # 019Q Task 3（M-5）：补采清单 SQL 扩为 NOT IN ('ths_total','sina_main')。
    # 语义：只有东财真数据（capital_source IS NULL 且非估算）才算"已完成"；
    # sina_main / ths_total 行仍进入补采清单 —— 东财 30 分钟内恢复时可覆盖回补
    # （"东财恢复后自动回补"的实现），新浪重采不降级已有数据（019Q QA F9 实证）。
    # 019S：'ths_total' 字面量保留不动——防御存量 ths_total 行（方案 b 处置后已清零），
    # 若删除则存量行被计为"已有真实数据"，东财恢复后永不回补覆盖；
    # 待存量清零确认后经新批次评审简化（可改为 NOT IN ('sina_main') 或删除）。
    supplement_symbols = list(a_stock_symbols)
    try:
        conn_sup = get_connection()
        cursor_sup = conn_sup.cursor()
        real_sids = set()
        for sym in a_stock_symbols:
            sid = get_stock_id(sym, 'a_stock')
            if sid:
                cursor_sup.execute(
                    'SELECT 1 FROM raw_capital_flow WHERE stock_id=? AND trade_date=? '
                    'AND main_net_inflow IS NOT NULL '
                    'AND (is_estimated = 0 OR is_estimated IS NULL) '
                    "AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main','westock'))",
                    (sid, today_str),
                )
                if cursor_sup.fetchone():
                    real_sids.add(sid)
        conn_sup.close()
        supplement_symbols = [
            s for s in a_stock_symbols
            if get_stock_id(s, 'a_stock') not in real_sids
        ]
    except Exception as e:
        logger.warning(f'[资金面补采] 补采清单生成异常(降级为全量补采): {e}')

    if supplement_symbols:
        logger.info(
            f'[资金面补采] 触发来源={_trigger_source}，'
            f'补采清单({len(supplement_symbols)}/{len(a_stock_symbols)}只): {supplement_symbols}'
        )
        supplement_result = _em_batch_collect(
            supplement_symbols, log_prefix='资金面补采', progress_cb=progress_cb
        )
        return {
            'success_count': success_count + supplement_result['success_count'],
            'fail_count': fail_count + supplement_result['fail_count'],
            'source': f'同花顺批量(辅助指标) + 资金面补采(成功{supplement_result["success_count"]}/失败{supplement_result["fail_count"]})',
        }

    return {'success_count': success_count, 'fail_count': fail_count, 'source': '同花顺批量(辅助指标)'}


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


def _fetch_capital_flow_tencent_hk(symbol, market):
    """
    港股资金面估算fallback：从腾讯K线数据中提取成交额和涨跌幅，
    估算主力资金流向。仅在东方财富资金流接口不可用时使用。

    估算逻辑：
    - main_net_inflow = 日成交额(万港元) * 涨跌幅 / 100
    - main_net_inflow_pct = 涨跌幅（作为资金净流入占比的近似）
    - 其他分项（超大单/大单/中单/小单）留空

    返回 list[dict] 或 None。
    """
    if market != 'hk_stock':
        return None

    prefix, normalized_code = _get_tencent_prefix(symbol, market)
    tencent_code = f'{prefix}{normalized_code}'

    url = 'https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get'
    params = {'param': f'{tencent_code},day,,,{KLINE_DAYS},qfq'}

    try:
        resp = _http_get(url, params=params)
        data = resp.json()
        stock_data = data.get('data', {}).get(tencent_code, {})
        kline_list = stock_data.get('qfqday') or stock_data.get('day') or []

        if not kline_list:
            return None

        results = []
        for item in kline_list:
            # 港股格式: [date, open, close, high, low, volume, {extra}, change_pct, turnover_wan, ...]
            if len(item) < 6:
                continue
            trade_date = item[0]
            try:
                close = float(item[2])
                volume = float(item[5])
            except (ValueError, IndexError):
                continue

            # 涨跌幅（优先取腾讯返回的，否则从收盘价计算）
            change_pct = 0.0
            if len(item) > 7:
                try:
                    change_pct = float(item[7])
                except (ValueError, TypeError):
                    pass

            # 成交额（万港元）
            turnover_wan = 0.0
            if len(item) > 8:
                try:
                    turnover_wan = float(item[8])
                except (ValueError, TypeError):
                    pass
            if turnover_wan == 0 and volume > 0 and close > 0:
                # fallback: 成交量 * 收盘价 / 10000
                turnover_wan = volume * close / 1e4

            # 估算主力净流入 = 成交额 * 涨跌幅 / 100
            main_net = round(turnover_wan * change_pct / 100, 2)
            main_net_pct = round(change_pct, 2)

            results.append(
                {
                    '日期': trade_date,
                    '主力净流入-净额': main_net,
                    '主力净流入-净占比': main_net_pct,
                    '小单净流入-净额': 0,
                    '中单净流入-净额': 0,
                    '大单净流入-净额': 0,
                    '超大单净流入-净额': 0,
                }
            )

        logger.info(f'[{symbol}] 腾讯资金面估算: {len(results)}天数据')
        return results if results else None

    except Exception as e:
        logger.warning(f'[{symbol}] 腾讯资金面估算失败: {e}')
        return None


def _fetch_capital_flow_sina(symbol, market):
    """
    新浪财经资金面Fallback：从新浪实时行情中提取大单/中单/小单资金流向。
    数据源：hq.sinajs.cn（直连不需代理）
    返回 list[dict]（仅当日快照）或 None。
    """
    import re

    try:
        # 新浪代码格式：sh600276 / sz000333 / hk03690
        if market == 'hk_stock':
            sina_code = 'hk' + _normalize_hk_symbol(symbol)
        elif symbol.startswith('6'):
            sina_code = 'sh' + symbol
        else:
            sina_code = 'sz' + symbol

        url = f'http://hq.sinajs.cn/list={sina_code}'
        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {'User-Agent': _random_ua(), 'Referer': 'https://finance.sina.com.cn'}
        )
        resp = session.get(url, timeout=(5, 10), proxies={'http': None, 'https': None})
        resp.encoding = 'gbk'
        text = resp.text

        # 新浪行情格式（A股）：var hq_str_sh600276="名称,开盘,昨收,最新价,最高,..."
        # 新浪行情格式（港股）：var hq_str_hk03690="名称,开盘,昨收,..."
        match = re.search(r'="([^"]+)"', text)
        if not match:
            logger.warning(f'[{symbol}] 新浪资金面返回空数据')
            return None

        parts = match.group(1).split(',')
        if len(parts) < 10:
            return None

        trade_date = datetime.now(_CN_TZ).strftime('%Y-%m-%d')

        # 从新浪行情中估算资金流向（涨跌幅 * 成交量 * 收盘价）
        if market == 'hk_stock':
            close = float(parts[6]) if parts[6] else 0
            prev_close = float(parts[3]) if parts[3] else 0
            volume = float(parts[12]) if len(parts) > 12 and parts[12] else 0
        else:
            close = float(parts[3]) if parts[3] else 0
            prev_close = float(parts[2]) if parts[2] else 0
            volume = float(parts[8]) if len(parts) > 8 and parts[8] else 0

        if close <= 0 or prev_close <= 0:
            return None

        change_pct = (close - prev_close) / prev_close * 100
        turnover_wan = volume * close / 1e4 if volume > 0 else 0
        main_net = round(turnover_wan * change_pct / 100, 2)

        results = [
            {
                '日期': trade_date,
                '主力净流入-净额': main_net,
                '主力净流入-净占比': round(change_pct, 2),
                '小单净流入-净额': 0,
                '中单净流入-净额': 0,
                '大单净流入-净额': 0,
                '超大单净流入-净额': 0,
            }
        ]

        logger.info(f'[{symbol}] 新浪资金面快照: 1天数据, main_net={main_net}万')
        return results

    except Exception as e:
        logger.warning(f'[{symbol}] 新浪资金面失败: {e}')
        return None


def _fetch_capital_flow_netease(symbol, market):
    """
    网易财经资金面Fallback：从网易历史行情接口获取历史资金流向。
    数据源：quotes.money.163.com（直连不需代理）
    返回 list[dict]（历史数据）或 None。
    """
    try:
        # 网易代码格式：0600276(上海前缀0) / 1000333(深圳前缀1) / 203690(港股前缀2)
        if market == 'hk_stock':
            ne_code = '2' + _normalize_hk_symbol(symbol)
            base_url = 'http://quotes.money.163.com/service/zhdkline'
            params = {
                'code': ne_code,
                'fields': 'DATE;CLOSE;HIGH;LOW;VOLUME;AMOUNT;CHGP',
                'count': str(KLINE_DAYS),
            }
        else:
            ne_code = ('0' if symbol.startswith('6') else '1') + symbol
            base_url = 'http://quotes.money.163.com/service/zhdkline'
            params = {
                'code': ne_code,
                'fields': 'DATE;CLOSE;HIGH;LOW;VOLUME;AMOUNT;CHGP',
                'count': str(KLINE_DAYS),
            }

        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {'User-Agent': _random_ua(), 'Referer': 'http://quotes.money.163.com'}
        )
        resp = session.get(
            base_url, params=params, timeout=(5, 10), proxies={'http': None, 'https': None}
        )
        resp.raise_for_status()
        resp.encoding = 'gbk'

        lines = resp.text.strip().split('\n')
        if len(lines) < 2:
            return None

        # 解析CSV（第一行是表头，跳过）
        results = []
        for line in lines[1:]:
            cols = [c.strip() for c in line.split(';')]
            if len(cols) < 7:
                continue
            try:
                trade_date = cols[0]
                close = float(cols[1])
                change_pct = float(cols[6]) if cols[6] else 0
                volume = float(cols[4]) if cols[4] else 0
                amount = float(cols[5]) if cols[5] else 0

                # 网易amount单位为元，转为万元
                turnover_wan = amount / 1e4 if amount > 0 else 0
                if turnover_wan == 0 and volume > 0 and close > 0:
                    turnover_wan = volume * close / 1e4

                main_net = round(turnover_wan * change_pct / 100, 2)
                results.append(
                    {
                        '日期': trade_date,
                        '主力净流入-净额': main_net,
                        '主力净流入-净占比': round(change_pct, 2),
                        '小单净流入-净额': 0,
                        '中单净流入-净额': 0,
                        '大单净流入-净额': 0,
                        '超大单净流入-净额': 0,
                    }
                )
            except (ValueError, IndexError):
                continue

        logger.info(f'[{symbol}] 网易资金面估算: {len(results)}天数据')
        return results if results else None

    except Exception as e:
        logger.warning(f'[{symbol}] 网易资金面失败: {e}')
        return None


def _fetch_capital_flow_sina_main(symbol, market, target_date=None):
    """
    019Q Task 1：新浪资金流主力口径采集（lscjfb 历史逐日分单接口，A股）。

    ⚠️ 命名规避（M-1）：既有估算源 _fetch_capital_flow_sina（hq.sinajs.cn 实时行情估算，
    019E 链路）零改动；本函数为新增主力口径源，命名为 _fetch_capital_flow_sina_main。

    网络规格（M-3/D-4/M-10）：
    - 协议 https 优先、失败回退 http（仅回退 1 次，不做代理尝试）
    - GBK 解码（errors='replace'）；必须带 UA（_random_ua()）+ Referer https://finance.sina.com.cn
    - 禁用系统代理：urllib.build_opener(ProxyHandler({}))
    - 全部网络调用（含 https 回退的第二次请求）走模块级 _call_with_timeout（单次 15s）
    - 每只请求后间隔 0.5~1.0s（防限流；29 只串行上限 ~29s，仅在 EM 失败路径发生）

    严格日期匹配（M-2，正确性红线）：lscjfb 是历史逐日表，非交易日/当日未发布时
    "最新行"即上一交易日（探针实证：周日最新行=08-07）。必须 opendate == target_date
    精确匹配才返回，不匹配一律返回 None 落回下一层，严禁"取最新行"实现。

    Args:
        symbol: str 股票代码（A股，6开头→sh 前缀，0/3开头→sz 前缀；港股不适用返回 None）
        market: str 市场（'a_stock'）
        target_date: str YYYY-MM-DD；None 表示当日（当日采集 num=2，回补窗口 num=15）

    Returns:
        dict 或 None：
        {'trade_date', 'main_net_inflow', 'super_large_net', 'large_net',
         'medium_net', 'small_net'}（万元，round 2）
        main_net_inflow = (r0_net + r1_net) / 1e4（主力 = 超大单 + 大单，与 EM 同定义，
        行内自洽 main == super_large + large）；四档之和 == netamount/1e4（新浪恒等式）。
        不写 main_net_inflow_pct（lscjfb ratioamount 为总净占比 netamount/turnover，
        非主力净流入占比，口径错位，M-4/D-6）。
    """
    try:
        if market != 'a_stock' or not symbol:
            return None

        # A股 symbol → 新浪 daima 映射（与 019K D-6 一致，港股不适用）
        daima = ('sh' + symbol) if symbol.startswith('6') else ('sz' + symbol)

        if target_date is None:
            target_date = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
            num = 2  # 当日采集：覆盖今日+上一交易日
        else:
            num = 15  # 回补窗口：覆盖目标日期（15 个交易日窗口，覆盖近 10 交易日完整性口径）

        def _request_once(proto):
            url = (
                f'{proto}://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                f'MoneyFlow.ssl_qsfx_lscjfb?page=1&num={num}&sort=opendate&asc=0&daima={daima}'
            )
            req = _urlreq.Request(
                url,
                headers={'User-Agent': _random_ua(), 'Referer': 'https://finance.sina.com.cn'},
            )
            opener = _urlreq.build_opener(_urlreq.ProxyHandler({}))  # 禁用系统代理
            resp = opener.open(req, timeout=_SINA_REQUEST_TIMEOUT)
            return resp.read().decode('gbk', errors='replace')

        # https 优先、失败回退 http（仅回退 1 次）；两次均走 _call_with_timeout（M-10）
        text, _timed_out = _call_with_timeout(
            lambda: _request_once('https'), f'新浪lscjfb https({symbol})'
        )
        if text is None:
            text, _ = _call_with_timeout(
                lambda: _request_once('http'), f'新浪lscjfb http回退({symbol})'
            )
        if text is None:
            logger.warning(f'[{symbol}] 新浪lscjfb https/http 均失败或超时')
            return None

        # JSON 解析健壮性（R-8）：接口偶发 null/非严格 JSON；非数组/空 → None
        data = None
        try:
            data = json.loads(text)
        except Exception:
            try:
                start = text.find('[')
                end = text.rfind(']')
                if start >= 0 and end > start:
                    data = json.loads(text[start:end + 1])
            except Exception:
                data = None
        if not isinstance(data, list) or not data:
            logger.warning(f'[{symbol}] 新浪lscjfb 返回非数组或为空')
            return None

        # 严格日期匹配：opendate == target_date 才写，严禁"取最新行"（M-2）
        for row in data:
            if not isinstance(row, dict):
                continue
            opendate = str(row.get('opendate', '') or '').strip()
            if opendate != target_date:
                continue
            # 金额统一走 _safe_float_wan（019N 模式，元→万元，None 语义）
            r0_wan = _safe_float_wan(row.get('r0_net'))  # 超大单净额
            r1_wan = _safe_float_wan(row.get('r1_net'))  # 大单净额
            r2_wan = _safe_float_wan(row.get('r2_net'))  # 中单净额
            r3_wan = _safe_float_wan(row.get('r3_net'))  # 小单净额
            if any(v is None for v in (r0_wan, r1_wan, r2_wan, r3_wan)):
                logger.warning(f'[{symbol}] 新浪lscjfb {opendate} 分单字段缺失，放弃')
                return None
            main_wan = round(r0_wan + r1_wan, 2)  # 主力 = 超大单 + 大单（行内自洽）
            logger.info(
                f'[{symbol}] 新浪lscjfb 命中 {opendate}: main={main_wan} 万'
                f'（r0={r0_wan}, r1={r1_wan}, r2={r2_wan}, r3={r3_wan}）'
            )
            return {
                'trade_date': opendate,
                'main_net_inflow': main_wan,
                'super_large_net': r0_wan,
                'large_net': r1_wan,
                'medium_net': r2_wan,
                'small_net': r3_wan,
            }
        # 无当日行（如非交易日最新行=上一交易日）→ 不写入，落回下一层
        logger.info(f'[{symbol}] 新浪lscjfb 无 {target_date} 行（最新行=上一交易日），返回 None')
        return None
    except Exception as e:
        logger.warning(f'[{symbol}] 新浪lscjfb 顶替采集异常: {e}')
        return None
    finally:
        # 每只请求后间隔 0.5~1.0s（防限流）
        time.sleep(_random.uniform(0.5, 1.0))


def backfill_capital_history(symbol, market, dates):
    """020H：逐日回补资金面历史缺口。
    020I：链序改为 腾讯 westock --date（A股+港股）→ 新浪 lscjfb（仅A股）。

    供补采调度器在东财不可用（熔断）期间回填近 10 个交易日的历史缺失日；
    EM 恢复后 push2his 120 天历史会自动覆盖回补（顶替行不阻断 EM 回填）。
    返回成功回补的日期列表。
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return []
    filled = []
    for d in dates:
        source = None
        row = None
        try:
            row = _fetch_capital_flow_westock(symbol, market, date_str=d)
            if row:
                source = 'westock'
        except Exception:
            row = None
        if row is None and market == 'a_stock':
            try:
                row = _fetch_capital_flow_sina_main(symbol, market, target_date=d)
                if row:
                    source = 'sina_main'
            except Exception:
                row = None
        if not row or not source:
            logger.info(f'[{symbol}] 历史资金面回补 {d}: 无可用数据源，跳过')
            continue
        try:
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, '
                'total_net_inflow=?, super_large_net=?, '
                'large_net=?, medium_net=?, small_net=?, is_estimated=0, capital_source=? '
                'WHERE stock_id=? AND trade_date=?',
                (
                    row['main_net_inflow'],
                    row.get('main_net_inflow_pct'),
                    row.get('total_net_inflow'),
                    row['super_large_net'],
                    row['large_net'],
                    row['medium_net'],
                    row['small_net'],
                    source,
                    stock_id,
                    d,
                ),
            )
            if cur.rowcount == 0:
                cur.execute(
                    'INSERT OR IGNORE INTO raw_capital_flow '
                    '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, total_net_inflow, '
                    'super_large_net, large_net, '
                    'medium_net, small_net, is_estimated, capital_source) '
                    'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)',
                    (
                        stock_id,
                        d,
                        row['main_net_inflow'],
                        row.get('main_net_inflow_pct'),
                        row.get('total_net_inflow'),
                        row['super_large_net'],
                        row['large_net'],
                        row['medium_net'],
                        row['small_net'],
                        source,
                    ),
                )
            conn.commit()
            conn.close()
            filled.append(d)
            logger.info(
                f'[{symbol}] 历史资金面回补成功: {d} 主力={row["main_net_inflow"]}万({source})'
            )
        except Exception as e:
            logger.warning(f'[{symbol}] 历史资金面回补 {d} 失败: {e}')
    return filled


def backfill_hk_total_net(symbol, dates):
    """020O：港股资金面补充回填（腾讯 hkfund TotalNetFlow + 主力净流入占比）。

    仅 UPDATE total_net_inflow / main_net_inflow_pct 列，不动 main_net_inflow 等主力字段——
    港股存量主力多为东财真实数据（EM 恢复前不降级覆盖）。
    A股不适用（asfund 散户为被动镜像、全口径恒等0，无全净额数据）。
    返回成功回补的日期列表。
    """
    stock_id = get_stock_id(symbol, 'hk_stock')
    if not stock_id:
        return []
    filled = []
    for d in dates:
        try:
            row = _fetch_capital_flow_westock(symbol, 'hk_stock', date_str=d)
            if not row or row.get('total_net_inflow') is None:
                logger.info(f'[{symbol}] 港股全净额回补 {d}: 腾讯无该日数据，跳过')
                continue
            conn = get_connection()
            cur = conn.cursor()
            cur.execute(
                'UPDATE raw_capital_flow SET total_net_inflow=?, main_net_inflow_pct=? '
                'WHERE stock_id=? AND trade_date=?',
                (row['total_net_inflow'], row.get('main_net_inflow_pct'), stock_id, d),
            )
            conn.commit()
            conn.close()
            filled.append(d)
            logger.info(
                f'[{symbol}] 港股回补成功: {d} 全净额={row["total_net_inflow"]}万 '
                f'占比={row.get("main_net_inflow_pct")}%(腾讯)'
            )
        except Exception as e:
            logger.warning(f'[{symbol}] 港股回补 {d} 失败: {e}')
    return filled


def fetch_capital_flow(symbol, market):
    """
    采集资金面数据。
    主力净流入来源阶梯（019S 定稿，M-11 更新）：
    Layer 1: 东方财富 push2his 个股历史资金流向（A股+港股，真实，capital_source=NULL）
    Layer 2: 东方财富 push2 实时资金流向（A股+港股，真实）
    Layer 3: akshare stock_individual_fund_flow（仅A股，底层仍为东方财富）
    EM 三层全失败时降级阶梯：
      ① 新浪 lscjfb 主力口径顶替（capital_source='sina_main'，r0+r1 超大单+大单，
         is_estimated=0 参与评分，019Q）
      ② 估算兜底（is_estimated=1，仅展示，不参与评分；019S 起不再使用同花顺顶替主力净流入）
    链路：东财三层 → 新浪 lscjfb 主力口径(sina_main) → 估算兜底（仅展示不参评）。
    同日已有真实数据时自动跳过采集（防覆盖机制：EM > 新浪 > 估算）。
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 020L：周末守卫 — 周六/周日休市，资金面全链路跳过（东财/腾讯/新浪/估算各层）。
    # 根因：周末定时日报仍逐只采集，估算兜底层把源返回日期写成非交易日脏行
    # （实测 08-09 周日 23 行 is_estimated=1 脏数据，挤占前端 LIMIT 10 展示名额）。
    # 与 019G 同花顺周末跳过同原则；周一开盘后自动恢复采集。
    if datetime.now(_CN_TZ).weekday() >= 5:
        logger.info(f'[{symbol}] 周末休市（weekday>=5），跳过资金面采集（020L）')
        return 'skipped', '周末休市，跳过资金面采集'

    global _EM_CONSECUTIVE_FAIL_COUNT  # 020B：逐只链路复用进程级东财连续失败计数（熔断传播）

    warnings = []
    saved_count = 0
    skipped = 0  # 019N: 跳过的异常数据行数（EM 三层全字段 None 行，不写 NULL 占位）
    source = ''

    # ============================================================
    # 018/019K/019Q: 前置校验层 — 仅检测东方财富已写入的当日真实数据。
    # 同花顺批量预取仅写入辅助字段 ths_net_inflow，不会触发本跳过逻辑。
    # 019K: THS 顶替行（capital_source='ths_total'）同样不触发跳过——
    # 019Q: 新浪顶替行（capital_source='sina_main'）同样不触发跳过——
    # 东财恢复后必须能重采覆盖，故本跳过 SQL 显式排除顶替行（M-5 扩展 NOT IN）。
    # 019S 起主力净流入链路为：东财三层 → 新浪 lscjfb 主力口径(sina_main) → 估算兜底
    # （仅展示不参评）；ths_total 仅为历史存量，不再产生新顶替行。
    # ============================================================
    today_str_pre = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    conn_pre = get_connection()
    cursor_pre = conn_pre.cursor()
    # 019E Task 2.4：前置校验适配——估算行（is_estimated=1）不阻止 EM 恢复后重写
    # 019K Task 3：前置校验排除 THS 顶替行（capital_source='ths_total'），保证 EM 恢复可回补
    # 019Q Task 3：防覆盖 SQL 扩展为 NOT IN ('ths_total','sina_main')（M-5）
    # 019S：'ths_total' 字面量保留不动——防御存量 ths_total 行（08-05/08-06 共 27 行），
    # 若删除则存量行会被误判为"已有真实数据"，东财恢复后永不回补覆盖；
    # 待存量清零后（方案 b 处置 + 只读断言）经新批次评审简化。
    cursor_pre.execute(
        'SELECT COUNT(*) AS cnt FROM raw_capital_flow WHERE stock_id = ? AND trade_date = ? '
        'AND main_net_inflow IS NOT NULL AND (is_estimated = 0 OR is_estimated IS NULL) '
        "AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main','westock'))",
        (stock_id, today_str_pre),
    )
    pre_cnt = cursor_pre.fetchone()['cnt']
    conn_pre.close()
    if pre_cnt > 0:
        skip_msg = f'同日跳过(已有真实资金流数据,记录数={pre_cnt})'
        logger.info(f'[{symbol}] {skip_msg}（东方财富已写入）')
        save_data_status(stock_id, 'capital', 'success', skip_msg)
        return 'success', f'今日已有真实资金流数据（{pre_cnt}条），跳过采集'

    # ============================================================
    # 同日真实数据防覆盖机制（P3-A验收前置修复）
    # 若今日已通过东方财富成功采集资金流数据，跳过本次采集
    # 防止后续操作触发fallback用估算值覆盖真实数据
    # ============================================================
    today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    conn_skip = get_connection()
    cursor_skip = conn_skip.cursor()
    cursor_skip.execute(
        'SELECT message FROM data_status WHERE stock_id = ? AND dimension = ? '
        'AND fetched_at LIKE ? ORDER BY fetched_at DESC LIMIT 1',
        (stock_id, 'capital', today_str + '%'),
    )
    skip_row = cursor_skip.fetchone()
    conn_skip.close()
    if skip_row and skip_row['message']:
        _src_msg = skip_row['message']
        if _src_msg.startswith('东方财富'):
            logger.info(f'[{symbol}] 今日已有东方财富真实资金流数据，跳过采集（防覆盖）')
            save_data_status(
                stock_id, 'capital', 'success', f'同日跳过(已有真实数据): {_src_msg[:60]}'
            )
            return 'success', '今日已有东方财富真实资金流数据，跳过采集（防覆盖）'

    # === 主数据源：东方财富个股资金流向历史（A股+港股，secid区分）===
    try:
        rows_data = _fetch_capital_flow_em_individual(symbol, market)
        if rows_data:
            conn = get_connection()
            cursor = conn.cursor()
            skipped = 0

            for row in rows_data:
                trade_date = str(row.get('日期', '')).strip()
                if not trade_date:
                    continue

                # 019N: 安全转换（None/NaN/'-'/±Inf → None，移除 or 0 伪造零），金额元→万元，占比不转换
                main_net = _safe_float_wan(row.get('主力净流入-净额'))
                main_net_pct = _safe_float_pct(row.get('主力净流入-净占比'))
                super_large = _safe_float_wan(row.get('超大单净流入-净额'))
                large = _safe_float_wan(row.get('大单净流入-净额'))
                medium = _safe_float_wan(row.get('中单净流入-净额'))
                small = _safe_float_wan(row.get('小单净流入-净额'))

                # 019N: 六字段全 None → 跳过该行（不写 NULL 占位行、不清空该日既有字段）
                if all(v is None for v in (main_net, main_net_pct, super_large, large, medium, small)):
                    skipped += 1
                    continue

                # 019E M-7：EM 写入显式携带 is_estimated=0（防御估算→真实覆盖时标记归位）
                # 019K Task 3：EM 写入显式携带 capital_source=NULL（顶替行被 EM 覆盖后来源归位）
                # 020G：UPDATE + INSERT OR IGNORE（保留同花顺辅助字段 ths_net_inflow，
                # 与 westock/新浪层同模式；INSERT OR REPLACE 会整行替换冲掉它）
                cursor.execute(
                    'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, '
                    'super_large_net=?, large_net=?, medium_net=?, small_net=?, '
                    'is_estimated=0, capital_source=NULL '
                    'WHERE stock_id=? AND trade_date=?',
                    (
                        main_net,
                        main_net_pct,
                        super_large,
                        large,
                        medium,
                        small,
                        stock_id,
                        trade_date,
                    ),
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        'INSERT OR IGNORE INTO raw_capital_flow '
                        '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, '
                        'super_large_net, large_net, medium_net, small_net, is_estimated, capital_source) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)',
                        (
                            stock_id,
                            trade_date,
                            main_net,
                            main_net_pct,
                            super_large,
                            large,
                            medium,
                            small,
                        ),
                    )
                # 019N: saved_count 仅计主字段 main 非 None 的行（假成功修正）
                if main_net is not None:
                    saved_count += 1

            conn.commit()
            conn.close()
            source = '东方财富(个股历史)'
            _EM_CONSECUTIVE_FAIL_COUNT = 0  # 020B：东财成功，重置连续失败计数
            _em_clear_ban()
            logger.info(
                f'[{symbol}] 资金面保存成功: {saved_count}天有效数据, 跳过 {skipped} 天异常数据'
            )
        else:
            warnings.append('东方财富个股资金流向返回空数据')
    except Exception as e:
        warnings.append(f'东方财富个股资金流向获取失败: {e}')
        logger.warning(f'[{symbol}] 东方财富个股资金流向获取失败: {e}')

    # === 备用数据源1：东方财富 push2（港股必须走这里）===
    if saved_count == 0:
        try:
            klines = _fetch_capital_flow_em(symbol, market)
            if klines is None:
                warnings.append('东方财富push2接口无法访问（直连和代理均失败）')
            elif klines:
                conn = get_connection()
                cursor = conn.cursor()
                skipped = 0

                for line in klines:
                    parts = line.split(',')
                    if len(parts) >= 6:
                        trade_date = parts[0]
                        # 019N: 安全转换（parts 为原始字符串，'nan'/'-'/空 → None），元转万元
                        main_net = _safe_float_wan(parts[1])
                        small_net = _safe_float_wan(parts[2])
                        medium_net = _safe_float_wan(parts[3])
                        large_net = _safe_float_wan(parts[4])
                        super_large_net = _safe_float_wan(parts[5])

                        # 019N: 五字段全 None → 跳过该行（不写 NULL 占位行）
                        if all(
                            v is None
                            for v in (main_net, small_net, medium_net, large_net, super_large_net)
                        ):
                            skipped += 1
                            continue

                        # 019E M-7：EM 写入显式携带 is_estimated=0
                        # 019K Task 3：EM 写入显式携带 capital_source=NULL（顶替行被 EM 覆盖后来源归位）
                        # 020G：UPDATE + INSERT OR IGNORE（保留 ths_net_inflow）
                        cursor.execute(
                            'UPDATE raw_capital_flow SET main_net_inflow=?, super_large_net=?, '
                            'large_net=?, medium_net=?, small_net=?, main_net_inflow_pct=NULL, '
                            'is_estimated=0, capital_source=NULL '
                            'WHERE stock_id=? AND trade_date=?',
                            (
                                main_net,
                                super_large_net,
                                large_net,
                                medium_net,
                                small_net,
                                stock_id,
                                trade_date,
                            ),
                        )
                        if cursor.rowcount == 0:
                            cursor.execute(
                                'INSERT OR IGNORE INTO raw_capital_flow '
                                '(stock_id, trade_date, main_net_inflow, '
                                'super_large_net, large_net, medium_net, small_net, is_estimated, capital_source) '
                                'VALUES (?, ?, ?, ?, ?, ?, ?, 0, NULL)',
                                (
                                    stock_id,
                                    trade_date,
                                    main_net,
                                    super_large_net,
                                    large_net,
                                    medium_net,
                                    small_net,
                                ),
                            )
                        # 019N: saved_count 仅计主字段 main 非 None 的行
                        if main_net is not None:
                            saved_count += 1

                conn.commit()
                conn.close()
                source = '东方财富(push2)'
                _EM_CONSECUTIVE_FAIL_COUNT = 0  # 020B
                _em_clear_ban()
            else:
                warnings.append('东方财富push2资金流向数据为空')
        except Exception as e:
            warnings.append(f'东方财富push2获取失败: {e}')
            logger.warning(f'[{symbol}] 东方财富push2获取失败: {e}')

    # === 备用数据源2：akshare内置接口（最后降级方案，仅A股；019Z：东财熔断冷却期同样跳过）===
    if saved_count == 0 and market == 'a_stock' and not _em_banned():
        try:
            logger.info(f'[{symbol}] 尝试akshare备用数据源...')
            df_ak = ak.stock_individual_fund_flow(stock=symbol, market=_get_em_market_code(symbol))
            if df_ak is not None and not df_ak.empty:
                conn = get_connection()
                cursor = conn.cursor()
                skipped = 0

                for _, row in df_ak.iterrows():
                    trade_date = str(row.get('日期', '')).strip()
                    if not trade_date:
                        continue

                    # 019N: 安全转换（df 值为 np.float64/str，pd.isna 兼容），金额元→万元，占比不转换
                    main_net = _safe_float_wan(row.get('主力净流入-净额'))
                    main_net_pct = _safe_float_pct(row.get('主力净流入-净占比'))
                    super_large = _safe_float_wan(row.get('超大单净流入-净额'))
                    large = _safe_float_wan(row.get('大单净流入-净额'))
                    medium = _safe_float_wan(row.get('中单净流入-净额'))
                    small = _safe_float_wan(row.get('小单净流入-净额'))

                    # 019N: 六字段全 None → 跳过该行（不写 NULL 占位行）
                    if all(
                        v is None for v in (main_net, main_net_pct, super_large, large, medium, small)
                    ):
                        skipped += 1
                        continue

                    # 019E M-7：EM 写入显式携带 is_estimated=0
                    # 019K Task 3：EM 写入显式携带 capital_source=NULL（顶替行被 EM 覆盖后来源归位）
                    # 020G：UPDATE + INSERT OR IGNORE（保留 ths_net_inflow）
                    cursor.execute(
                        'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, '
                        'super_large_net=?, large_net=?, medium_net=?, small_net=?, '
                        'is_estimated=0, capital_source=NULL '
                        'WHERE stock_id=? AND trade_date=?',
                        (
                            main_net,
                            main_net_pct,
                            super_large,
                            large,
                            medium,
                            small,
                            stock_id,
                            trade_date,
                        ),
                    )
                    if cursor.rowcount == 0:
                        cursor.execute(
                            'INSERT OR IGNORE INTO raw_capital_flow '
                            '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, '
                            'super_large_net, large_net, medium_net, small_net, is_estimated, capital_source) '
                            'VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0, NULL)',
                            (
                                stock_id,
                                trade_date,
                                main_net,
                                main_net_pct,
                                super_large,
                                large,
                                medium,
                                small,
                            ),
                        )
                    # 019N: saved_count 仅计主字段 main 非 None 的行
                    if main_net is not None:
                        saved_count += 1

                conn.commit()
                conn.close()
                source = 'akshare(备用)'
                _EM_CONSECUTIVE_FAIL_COUNT = 0  # 020B
                _em_clear_ban()
                logger.info(
                    f'[{symbol}] akshare备用源成功: {saved_count}天有效数据, 跳过 {skipped} 天异常数据'
                )
            else:
                warnings.append('akshare备用数据源返回空数据')
        except Exception as e:
            warnings.append(f'akshare备用源失败: {e}')
            logger.warning(f'[{symbol}] akshare备用源失败: {e}')

    # ============================================================
    # 020B：记录"东财三层失败"标志（westock 成功与否不影响此判定——westock 能成功
    # 恰恰说明东财不可用），用于熔断累计。
    # ============================================================
    em_failed_this_stock = saved_count == 0

    # ============================================================
    # 020A：腾讯自选股（westock）资金面备用层 — 东财三层全失败后、新浪之前
    # A股 asfund / 港股 hkfund，主力口径=超大+大（与东财同概念，社区实测不封 IP）。
    # 写库 is_estimated=0、capital_source='westock'；东财恢复后仍可覆盖回补
    # （防覆盖/补采清单 SQL 的 NOT IN 列表已含 'westock'）。
    # ============================================================
    if saved_count == 0:
        try:
            w_row = _fetch_capital_flow_westock(symbol, market)
            if w_row:
                w_date = (
                    w_row['trade_date']
                    or datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                )
                conn = get_connection()
                cursor = conn.cursor()
                # 020F：UPDATE + INSERT OR IGNORE（与新浪层同模式）——
                # INSERT OR REPLACE 会整行替换，冲掉同花顺批量预取的辅助字段 ths_net_inflow
                cursor.execute(
                    'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, '
                    'total_net_inflow=?, super_large_net=?, '
                    'large_net=?, medium_net=?, small_net=?, is_estimated=0, capital_source=? '
                    'WHERE stock_id=? AND trade_date=?',
                    (
                        w_row['main_net_inflow'],
                        w_row.get('main_net_inflow_pct'),
                        w_row.get('total_net_inflow'),
                        w_row['super_large_net'],
                        w_row['large_net'],
                        w_row['medium_net'],
                        w_row['small_net'],
                        'westock',
                        stock_id,
                        w_date,
                    ),
                )
                if cursor.rowcount == 0:
                    cursor.execute(
                        'INSERT OR IGNORE INTO raw_capital_flow '
                        '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, total_net_inflow, '
                        'super_large_net, large_net, '
                        'medium_net, small_net, is_estimated, capital_source) '
                        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0, 'westock')",
                        (
                            stock_id,
                            w_date,
                            w_row['main_net_inflow'],
                            w_row.get('main_net_inflow_pct'),
                            w_row.get('total_net_inflow'),
                            w_row['super_large_net'],
                            w_row['large_net'],
                            w_row['medium_net'],
                            w_row['small_net'],
                        ),
                    )
                conn.commit()
                conn.close()
                if w_row['main_net_inflow'] is not None:
                    saved_count = 1
                source = '腾讯自选股(westock)'
                logger.info(
                    f'[{symbol}] 腾讯自选股资金面成功: '
                    f'主力净流入={w_row["main_net_inflow"]}万, date={w_date}'
                )
            else:
                warnings.append('腾讯自选股资金面层无数据')
        except Exception as e:
            warnings.append(f'腾讯自选股资金面层失败: {e}')
            logger.warning(f'[{symbol}] 腾讯自选股资金面层失败: {e}')

    # ============================================================
    # 019E Task 2：估算兜底（仅展示用，不参与评分）
    # EM 三层全失败时，降级到估算源（新浪/腾讯/网易）写入当日 1 行。
    # 估算值通过 is_estimated=1 标记，data_adapter/advisor SQL 层过滤确保不进入评分。
    # 估算源公式"成交额×涨跌幅/100"与真实主力净流入无相关性，仅供展示。
    # ============================================================
    # 019E Task 2.6（M-4）：拆除提前 return，改为标志位继续执行估算降级链路
    em_all_failed = (saved_count == 0)
    est_source = ''
    # 020B：东财三层失败的股票累计熔断计数（含 westock 顶替成功的股票），
    # 达到阈值进入冷却——后续股票跳过东财直连（019Z 机制原先只在批量回退循环生效，
    # 手动报告生成的逐只链路此前每只都要空烧 4 轮重试 ≈5 分钟）。
    if em_failed_this_stock:
        _EM_CONSECUTIVE_FAIL_COUNT += 1
        if _EM_CONSECUTIVE_FAIL_COUNT >= _EM_CIRCUIT_BREAK_N:
            _em_record_ban()
    if em_all_failed:
        logger.warning(
            f'[{symbol}] 东方财富三层全失败（push2his/push2/akshare），'
            '尝试新浪顶替 → 估算兜底（链路：东财三层 → 新浪 lscjfb 主力口径(sina_main) → 估算兜底仅展示不参评）'
        )

        # ============================================================
        # 019Q Task 2：新浪 lscjfb 真实数据顶替（019S 起为 EM 三层全失败时唯一真实顶替源，D-1）
        # 主力口径（r0+r1 超大单+大单），与 EM"主力=超大+大"同概念，口径逼近度最高
        # （019K 实证 THS 全部资金口径同日符号可相反，019S 已弃用）。
        # 写四档 + 主力（D-6），不写 main_net_inflow_pct（ratioamount 为总净占比，M-4）。
        # 严格日期匹配：lscjfb 无当日行（非交易日/未发布）→ 返回 None → 落回估算兜底（M-2）。
        # 写入模式复用 019K 规格：UPDATE + INSERT OR IGNORE，严禁 INSERT OR REPLACE；
        # 无条件 UPDATE（不带来源守卫，M-5）——可覆盖估算行（口径更优）。
        # ============================================================
        try:
            sina_row = _fetch_capital_flow_sina_main(symbol, market)
            if sina_row:
                conn = get_connection()
                cur = conn.cursor()
                cur.execute(
                    'UPDATE raw_capital_flow SET main_net_inflow=?, super_large_net=?, '
                    'large_net=?, medium_net=?, small_net=?, is_estimated=0, capital_source=? '
                    'WHERE stock_id=? AND trade_date=?',
                    (
                        sina_row['main_net_inflow'],
                        sina_row['super_large_net'],
                        sina_row['large_net'],
                        sina_row['medium_net'],
                        sina_row['small_net'],
                        'sina_main',
                        stock_id,
                        today_str,
                    ),
                )
                if cur.rowcount == 0:
                    cur.execute(
                        'INSERT OR IGNORE INTO raw_capital_flow '
                        '(stock_id, trade_date, main_net_inflow, super_large_net, large_net, '
                        'medium_net, small_net, is_estimated, capital_source) '
                        'VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?)',
                        (
                            stock_id,
                            today_str,
                            sina_row['main_net_inflow'],
                            sina_row['super_large_net'],
                            sina_row['large_net'],
                            sina_row['medium_net'],
                            sina_row['small_net'],
                            'sina_main',
                        ),
                    )
                conn.commit()
                conn.close()
                saved_count = 1
                save_data_status(
                    stock_id, 'capital', 'fallback',
                    '新浪顶替(主力口径r0+r1；东财恢复后自动回补)'
                )
                logger.info(
                    f'[{symbol}] 新浪 lscjfb 主力口径顶替成功: '
                    f'main={sina_row["main_net_inflow"]} 万'
                    f'（is_estimated=0，capital_source=sina_main，仅写当日 1 行）'
                )
                return 'fallback', '新浪顶替(主力口径r0+r1；东财恢复后自动回补)'
        except Exception as e:
            warnings.append(f'新浪顶替失败: {e}')
            logger.warning(f'[{symbol}] 新浪 lscjfb 顶替失败: {e}')
        # 019S：THS 顶替块已移除（监理裁定：主力净流入链路不得使用同花顺数据）。
        # 新浪顶替失败（无当日行或异常）→ 直接落回估算兜底（源3/4/5），链路不断（静默降级）。

        # === 估算源3：腾讯K线估算（港股专用fallback，直连不需代理）===
        if saved_count == 0 and market == 'hk_stock':
            try:
                logger.info(f'[{symbol}] 尝试腾讯K线估算（兜底展示）...')
                tencent_rows = _fetch_capital_flow_tencent_hk(symbol, market)
                if tencent_rows:
                    # 019E Task 2.2：估算仅写当日 1 行（不污染历史序列）
                    row = tencent_rows[0]
                    trade_date = str(row.get('日期', '')).strip()
                    if trade_date:
                        main_net = round(float(row.get('主力净流入-净额', 0) or 0), 2)
                        main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)
                        conn = get_connection()
                        cursor = conn.cursor()
                        # 019E Task 2.7（M-5）：UPDATE + INSERT OR IGNORE（禁止 INSERT OR REPLACE，避免清除占位行已有字段）
                        # 019K Task 3：估算 UPDATE 追加来源守卫（防御性——估算不得覆盖 THS 顶替行）
                        # 019S：'ths_total' 字面量保留不动——防御存量 ths_total 行，待存量清零后经新批次评审简化（估算守卫可简化为仅 'sina_main'）
                        cursor.execute(
                            'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, is_estimated=1 '
                            'WHERE stock_id=? AND trade_date=? '
                            "AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main','westock'))",
                            (main_net, main_net_pct, stock_id, trade_date),
                        )
                        if cursor.rowcount == 0:
                            cursor.execute(
                                'INSERT OR IGNORE INTO raw_capital_flow '
                                '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, is_estimated) '
                                'VALUES (?, ?, ?, ?, 1)',
                                (stock_id, trade_date, main_net, main_net_pct),
                            )
                        conn.commit()
                        conn.close()
                        saved_count = 1
                        est_source = '腾讯K线估算'
                        logger.info(f'[{symbol}] 估算兜底成功(腾讯K线估算): {trade_date} 1行')
                else:
                    warnings.append('腾讯K线估算返回空数据')
            except Exception as e:
                warnings.append(f'腾讯K线估算失败: {e}')
                logger.warning(f'[{symbol}] 腾讯K线估算失败: {e}')

        # === 估算源4：新浪财经资金面（直连不需代理）===
        if saved_count == 0:
            try:
                logger.info(f'[{symbol}] 尝试新浪财经估算（兜底展示）...')
                sina_rows = _fetch_capital_flow_sina(symbol, market)
                if sina_rows:
                    row = sina_rows[0]
                    trade_date = str(row.get('日期', '')).strip()
                    if trade_date:
                        main_net = round(float(row.get('主力净流入-净额', 0) or 0), 2)
                        main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)
                        conn = get_connection()
                        cursor = conn.cursor()
                        # 019K Task 3：估算 UPDATE 追加来源守卫（防御性——估算不得覆盖 THS 顶替行）
                        # 019S：'ths_total' 字面量保留不动——防御存量 ths_total 行，待存量清零后经新批次评审简化（估算守卫可简化为仅 'sina_main'）
                        cursor.execute(
                            'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, is_estimated=1 '
                            'WHERE stock_id=? AND trade_date=? '
                            "AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main','westock'))",
                            (main_net, main_net_pct, stock_id, trade_date),
                        )
                        if cursor.rowcount == 0:
                            cursor.execute(
                                'INSERT OR IGNORE INTO raw_capital_flow '
                                '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, is_estimated) '
                                'VALUES (?, ?, ?, ?, 1)',
                                (stock_id, trade_date, main_net, main_net_pct),
                            )
                        conn.commit()
                        conn.close()
                        saved_count = 1
                        est_source = '新浪财经'
                        logger.info(f'[{symbol}] 估算兜底成功(新浪财经): {trade_date} 1行')
                else:
                    warnings.append('新浪财经资金面返回空数据')
            except Exception as e:
                warnings.append(f'新浪财经资金面失败: {e}')
                logger.warning(f'[{symbol}] 新浪财经估算失败: {e}')

        # === 估算源5：网易财经历史资金流向（直连不需代理）===
        if saved_count == 0:
            try:
                logger.info(f'[{symbol}] 尝试网易财经估算（兜底展示）...')
                netease_rows = _fetch_capital_flow_netease(symbol, market)
                if netease_rows:
                    row = netease_rows[0]
                    trade_date = str(row.get('日期', '')).strip()
                    if trade_date:
                        main_net = round(float(row.get('主力净流入-净额', 0) or 0), 2)
                        main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)
                        conn = get_connection()
                        cursor = conn.cursor()
                        # 019K Task 3：估算 UPDATE 追加来源守卫（防御性——估算不得覆盖 THS 顶替行）
                        # 019S：'ths_total' 字面量保留不动——防御存量 ths_total 行，待存量清零后经新批次评审简化（估算守卫可简化为仅 'sina_main'）
                        cursor.execute(
                            'UPDATE raw_capital_flow SET main_net_inflow=?, main_net_inflow_pct=?, is_estimated=1 '
                            'WHERE stock_id=? AND trade_date=? '
                            "AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main','westock'))",
                            (main_net, main_net_pct, stock_id, trade_date),
                        )
                        if cursor.rowcount == 0:
                            cursor.execute(
                                'INSERT OR IGNORE INTO raw_capital_flow '
                                '(stock_id, trade_date, main_net_inflow, main_net_inflow_pct, is_estimated) '
                                'VALUES (?, ?, ?, ?, 1)',
                                (stock_id, trade_date, main_net, main_net_pct),
                            )
                        conn.commit()
                        conn.close()
                        saved_count = 1
                        est_source = '网易财经'
                        logger.info(f'[{symbol}] 估算兜底成功(网易财经): {trade_date} 1行')
                else:
                    warnings.append('网易财经资金面返回空数据')
            except Exception as e:
                warnings.append(f'网易财经资金面失败: {e}')
                logger.warning(f'[{symbol}] 网易财经估算失败: {e}')

        # 019E Task 2.8（M-6）：估算成功返回 'estimated'（非 'success'，确保019C回退循环不误计为成功）
        if saved_count > 0 and em_all_failed:
            est_msg = f'估算兜底({est_source})，仅展示用，待东方财富恢复后覆盖'
            save_data_status(stock_id, 'capital', 'estimated', est_msg)
            logger.info(f'[{symbol}] 资金面估算兜底完成: {est_source}，返回estimated')
            return 'estimated', est_msg

    # === 所有数据源均失败时写入error_logs ===
    if saved_count == 0:
        try:
            conn_err = get_connection()
            cursor_err = conn_err.cursor()
            cursor_err.execute(
                """
                INSERT INTO error_logs (stock_id, module, error_type, error_message)
                VALUES (?, ?, ?, ?)
            """,
                (stock_id, 'capital_flow', 'all_sources_failed', '; '.join(warnings)[:500]),
            )
            conn_err.commit()
            conn_err.close()
        except Exception:
            pass

    if saved_count > 0:
        # 检查数据库中已有的历史记录数和最新日期
        conn_chk = get_connection()
        cursor_chk = conn_chk.cursor()
        cursor_chk.execute(
            'SELECT COUNT(*) as cnt, MAX(trade_date) as latest FROM raw_capital_flow WHERE stock_id = ?',
            (stock_id,),
        )
        row_chk = cursor_chk.fetchone()
        total_records = row_chk['cnt']
        latest_cap_date = row_chk['latest']
        conn_chk.close()

        # 检查K线最新日期
        conn_k = get_connection()
        cursor_k = conn_k.cursor()
        cursor_k.execute(
            'SELECT MAX(trade_date) as latest FROM raw_kline WHERE stock_id = ?', (stock_id,)
        )
        latest_kline_date = cursor_k.fetchone()['latest']
        conn_k.close()

        # 日期对齐策略说明（方案B：沿用T-1数据 + 标注截止日）
        date_note = ''
        if latest_kline_date and latest_cap_date and latest_kline_date > latest_cap_date:
            date_note = f'。注意：资金面截止日为{latest_cap_date}（K线最新日为{latest_kline_date}），四维分析时应沿用T-1资金面数据并在报告中标注截止日期'
            logger.info(
                f'[{symbol}] 资金面日期对齐: K线={latest_kline_date}, 资金面={latest_cap_date}, 采用T-1策略'
            )

        msg = f'{source}采集成功，写入 {saved_count} 天有效数据（跳过 {skipped} 天异常数据）。数据库累计{total_records}条记录{date_note}'
        save_data_status(stock_id, 'capital', 'success', msg)
        return 'success', msg
    else:
        all_warnings = '; '.join(warnings) if warnings else '所有数据源均失败'
        save_data_status(stock_id, 'capital', 'failed', all_warnings)
        return 'failed', all_warnings


# ============================================================
# DATASRC-C：北向资金净买入采集（仅沪深港通标的）
# 数据源：akshare stock_hsgt_individual_em（东方财富沪深港通个股）
# 字段映射：当日增持估计净买额(元) -> north_holding_change(万元)
# 覆盖范围：仅沪深港通标的，非标的填 None
# 采集频率：每日1次（T+0 收盘后）
# ============================================================


def fetch_north_capital(symbol, market, force_full=False):
    """
    采集北向资金净买入数据（DATASRC-C 子任务2.1）。
    仅对沪深港通标的有效，非标的直接跳过（填 None）。
    使用 UPDATE 写入 raw_capital_flow.north_holding_change，不破坏已有字段。
    011增量：30天缓存（数据源自2024-08-16停更）。
    失败时不阻塞主流程，仅记录 warning。

    Returns: (status, message)
    """
    if market != 'a_stock':
        # 北向资金仅 A 股，港股不受影响（A/H 双市场独立红线）
        return 'skipped', '北向资金仅A股标的'

    stock_id = get_stock_id(symbol, 'a_stock')
    if not stock_id:
        return 'failed', f'数据库中未找到A股 {symbol}'

    # 011增量：30天缓存检查
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT fetched_at FROM data_status
                   WHERE stock_id = ? AND dimension = ?
                   ORDER BY fetched_at DESC LIMIT 1""",
                (stock_id, 'north_capital'),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['fetched_at']:
                last_fetch = datetime.strptime(str(row['fetched_at'])[:19], '%Y-%m-%d %H:%M:%S')
                days_since = (datetime.now(_CN_TZ).replace(tzinfo=None) - last_fetch).days
                if days_since < NORTH_CAPITAL_CACHE_DAYS:
                    skip_msg = f'北向资金缓存有效({days_since}天/{NORTH_CAPITAL_CACHE_DAYS}天)'
                    logger.info(f'[DATASRC-C] {symbol} {skip_msg}')
                    return 'skipped', skip_msg
        except Exception as e:
            logger.warning(f'[DATASRC-C] {symbol} 北向资金缓存检查异常(降级): {e}')

    try:
        logger.info(f'[DATASRC-C] 北向资金采集: {symbol}')
        df = ak.stock_hsgt_individual_em(symbol=symbol)
        if df is None or df.empty:
            # 非沪深港通标的或接口无数据 -> 填 None（不填0，不估算）
            logger.info(f'[DATASRC-C] {symbol} 非沪深港通标的或无北向数据，跳过')
            return 'skipped', '非沪深港通标的（无北向资金数据）'

        # 取最近一条记录（最新日期）
        # 列名兼容：akshare 版本差异可能导致列名微调
        date_col = None
        net_buy_col = None
        for col in df.columns:
            if '日期' in str(col):
                date_col = col
            if '净买' in str(col) or '增持' in str(col):
                if '额' in str(col):
                    net_buy_col = col

        if date_col is None or net_buy_col is None:
            # 尝试按位置取（列顺序：持股日期,收盘价,涨跌幅,持股股数,持股市值,占比,增持股数,增持净买额,持股市值变化）
            cols = list(df.columns)
            if len(cols) >= 8:
                date_col = cols[0]
                net_buy_col = cols[7]  # 当日增持估计净买额
            else:
                logger.warning(f'[DATASRC-C] {symbol} 北向数据列名无法识别: {cols}')
                return 'failed', '北向数据列名无法识别'

        latest_row = df.iloc[-1]
        trade_date_raw = str(latest_row[date_col]).split(' ')[0]
        # 统一日期格式 YYYY-MM-DD
        trade_date = trade_date_raw.replace('/', '-')
        if len(trade_date) == 8 and trade_date.isdigit():
            trade_date = f'{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}'

        # B26：北向数据源停更标注（ak.stock_hsgt_individual_em 自2024-08-16起停更，港交所政策变更）
        if trade_date < '2024-08-16':
            logger.info(
                f'[DATASRC-C] {symbol} 北向资金数据源停更，最新数据日期 {trade_date}，不影响评分（B26已降权至0.10）'
            )

        net_buy_yuan = latest_row[net_buy_col]
        if pd.isna(net_buy_yuan):
            logger.info(f'[DATASRC-C] {symbol} 最新北向净买额为NaN，跳过')
            return 'skipped', '北向净买额为NaN'

        # 元 -> 万元（保留2位小数）
        net_buy_wan = round(float(net_buy_yuan) / 1e4, 2)

        # UPDATE 写入（不 INSERT，避免破坏已有 main_net_inflow 等字段）
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE raw_capital_flow SET north_holding_change = ?
            WHERE stock_id = ? AND trade_date = ?
        """,
            (net_buy_wan, stock_id, trade_date),
        )
        updated = cursor.rowcount

        # 如果该日期无记录，INSERT 一条仅含 north 的记录（不覆盖其他字段）
        if updated == 0:
            cursor.execute(
                """
                INSERT OR IGNORE INTO raw_capital_flow
                (stock_id, trade_date, north_holding_change)
                VALUES (?, ?, ?)
            """,
                (stock_id, trade_date, net_buy_wan),
            )
            updated = cursor.rowcount

        conn.commit()
        conn.close()

        logger.info(
            f'[DATASRC-C] {symbol} 北向资金写入成功: {trade_date} net_buy={net_buy_wan}万元'
        )
        save_data_status(
            stock_id, 'north_capital', 'success', f'北向资金净买入 {net_buy_wan}万元 ({trade_date})'
        )
        return 'success', f'北向资金净买入 {net_buy_wan}万元 ({trade_date})'

    except Exception as e:
        logger.warning(f'[DATASRC-C] {symbol} 北向资金采集失败(不阻塞): {e}')
        save_data_status(stock_id, 'north_capital', 'failed', f'北向资金采集失败: {e}')
        return 'failed', f'北向资金采集失败: {e}'


# ============================================================
# DATASRC-C：融资余额采集（仅融资融券标的）
# 数据源：akshare stock_margin_detail_sse / stock_margin_detail_szse
# 字段映射：融资余额(元) -> margin_balance(万元)
# 覆盖范围：仅融资融券标的（约2200只），非标的填 None
# 采集频率：每日1次（T+1 公布）
# ============================================================

# 融资融券数据缓存（按日期缓存全市场数据，避免逐只重复请求）
_MARGIN_CACHE = {'sse': {}, 'szse': {}}  # {date_str: DataFrame}


def _fetch_margin_data_sse(date_str):
    """获取上交所融资融券数据（带缓存）"""
    if date_str in _MARGIN_CACHE['sse']:
        return _MARGIN_CACHE['sse'][date_str]
    try:
        df = ak.stock_margin_detail_sse(date=date_str)
        _MARGIN_CACHE['sse'][date_str] = df
        return df
    except Exception as e:
        logger.debug(f'[DATASRC-C] 上交所融资融券数据获取跳过({date_str}): {e}')
        _MARGIN_CACHE['sse'][date_str] = None
        return None


def _fetch_margin_data_szse(date_str):
    """获取深交所融资融券数据（带缓存）"""
    if date_str in _MARGIN_CACHE['szse']:
        return _MARGIN_CACHE['szse'][date_str]
    try:
        df = ak.stock_margin_detail_szse(date=date_str)
        _MARGIN_CACHE['szse'][date_str] = df
        return df
    except Exception as e:
        logger.debug(f'[DATASRC-C] 深交所融资融券数据获取跳过({date_str}): {e}')
        _MARGIN_CACHE['szse'][date_str] = None
        return None


def fetch_margin_balance(symbol, market, force_full=False):
    """
    采集融资余额数据（DATASRC-C 子任务2.2）。
    仅对融资融券标的有效，非标的直接跳过（填 None）。
    使用 UPDATE 写入 raw_capital_flow.margin_balance，不破坏已有字段。
    融资余额为 T+1 公布，采集最近两个交易日数据以支持 data_adapter 计算日变化。
    011增量：有数据时仅补近期1-15天；无数据时保持全量回填。
    失败时不阻塞主流程，仅记录 warning。

    Returns: (status, message)
    """
    if market != 'a_stock':
        # 融资融券仅 A 股
        return 'skipped', '融资融券仅A股标的'

    stock_id = get_stock_id(symbol, 'a_stock')
    if not stock_id:
        return 'failed', f'数据库中未找到A股 {symbol}'

    try:
        logger.info(f'[DATASRC-C] 融资余额采集: {symbol}')

        # 确定交易所（6开头=上交所，0/3开头=深交所）
        is_sse = symbol.startswith('6') or symbol.startswith('9')

        # 011增量：确定起始日期范围
        today = datetime.now(_CN_TZ).replace(tzinfo=None)
        if not force_full:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT MAX(trade_date) as last_margin_date FROM raw_capital_flow
                   WHERE stock_id = ? AND margin_balance IS NOT NULL""",
                (stock_id,),
            )
            chk_row = cursor_chk.fetchone()
            conn_chk.close()
            if chk_row and chk_row['last_margin_date']:
                last_margin = datetime.strptime(str(chk_row['last_margin_date'])[:10], '%Y-%m-%d')
                # 从上次有数据的次日开始，仅补近期（上限15天防止遗漏）
                days_to_try_max = min(15, (today - last_margin).days + 2)
                if days_to_try_max <= 0:
                    return 'skipped', '融资余额已是最新'
            else:
                days_to_try_max = 159  # 无数据时保持全量
        else:
            days_to_try_max = 159  # force_full时全量

        dates_to_try = []
        for delta in range(1, days_to_try_max + 1):
            d = today - timedelta(days=delta)
            # 跳过周末
            if d.weekday() < 5:
                dates_to_try.append(d.strftime('%Y%m%d'))

        updated_count = 0
        _no_match_count = 0  # B26：连续3个日期无匹配才判定为非标的
        for date_str in dates_to_try:
            if is_sse:
                df = _fetch_margin_data_sse(date_str)
            else:
                df = _fetch_margin_data_szse(date_str)

            if df is None or df.empty:
                continue

            # 在 DataFrame 中查找该股票
            # 上交所列名：证券代码 / 融资余额(元)
            # 深交所列名：证券代码 / 融资余额(元)
            code_col = None
            balance_col = None
            for col in df.columns:
                col_str = str(col)
                if '代码' in col_str:
                    code_col = col
                if '融资余额' in col_str:
                    balance_col = col

            if code_col is None or balance_col is None:
                logger.warning(f'[DATASRC-C] 融资融券列名无法识别: {list(df.columns)}')
                break

            # 匹配股票代码（代码列可能为 int 或 str）
            df_match = df[df[code_col].astype(str).str.zfill(6) == symbol]
            if df_match.empty:
                # B26：连续3个日期均无匹配才判定为非标的（容错单日接口异常）
                _no_match_count += 1
                if _no_match_count >= 3 and updated_count == 0:
                    logger.info(f'[DATASRC-C] {symbol} 非融资融券标的，跳过')
                    return 'skipped', '非融资融券标的（连续3个日期无数据）'
                if _no_match_count >= 3:
                    break
                continue

            row = df_match.iloc[0]
            balance_yuan = row[balance_col]
            if pd.isna(balance_yuan):
                continue

            # 元 -> 万元
            balance_wan = round(float(balance_yuan) / 1e4, 2)

            # 格式化日期 YYYY-MM-DD
            trade_date = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'

            # UPDATE 写入
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE raw_capital_flow SET margin_balance = ?
                WHERE stock_id = ? AND trade_date = ?
            """,
                (balance_wan, stock_id, trade_date),
            )
            updated = cursor.rowcount

            if updated == 0:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO raw_capital_flow
                    (stock_id, trade_date, margin_balance)
                    VALUES (?, ?, ?)
                """,
                    (stock_id, trade_date, balance_wan),
                )
                updated = cursor.rowcount

            conn.commit()
            conn.close()
            updated_count += updated

            # B26：回填完整历史（上限150条，受日期范围限制；对齐主力资金历史天数）
            if updated_count >= 150:
                break

        if updated_count > 0:
            logger.info(f'[DATASRC-C] {symbol} 融资余额写入成功: {updated_count}条记录')
            return 'success', f'融资余额已更新({updated_count}条记录)'
        else:
            logger.info(f'[DATASRC-C] {symbol} 未找到融资融券数据')
            return 'skipped', '非融资融券标的或数据未公布'

    except Exception as e:
        logger.warning(f'[DATASRC-C] {symbol} 融资余额采集失败(不阻塞): {e}')
        return 'failed', f'融资余额采集失败: {e}'


# ============================================================
# 消息面数据采集
# ============================================================


def fetch_sentiment(symbol, market, force_full=False):
    """
    采集消息面数据（模块4接入）。
    调用 news_collector 采集新闻 + 情绪分析。
    011增量：当日已有 → 跳过。
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 011增量：当日跳过
    if not force_full:
        try:
            today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT COUNT(*) as cnt FROM news_sentiment
                   WHERE stock_id = ? AND news_date LIKE ?""",
                (stock_id, today_str + '%'),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['cnt'] > 0:
                skip_msg = f'当日跳过(消息面已有{row["cnt"]}条记录)'
                save_data_status(stock_id, 'sentiment', 'success', skip_msg)
                logger.info(f'[{symbol}] {skip_msg}')
                return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[{symbol}] 消息面增量检查异常(降级为全量): {e}')

    market_name = 'A股' if market == 'a_stock' else '港股'

    try:
        from modules.news_collector import collect_news

        status, msg = collect_news(stock_id, symbol, market)
        save_data_status(stock_id, 'sentiment', status, msg)
        logger.info(f'[{market_name} {symbol}] 消息面采集完成: {status}')
        return status, msg
    except Exception as e:
        error_msg = f'{market_name} {symbol} 消息面采集异常: {e!s}'
        logger.error(error_msg, exc_info=True)
        save_data_status(stock_id, 'sentiment', 'failed', error_msg)
        return 'failed', error_msg


# ============================================================
# INDUSTRY-DYNAMIC：行业分类动态获取
# ============================================================

# B14: 行业本地映射兜底（akshare API 被封时使用）
# 数据来源：东方财富行业分类（2026-07 手动确认）
_LOCAL_INDUSTRY_MAP = {
    '000333': '家电行业',
    '000858': '酿酒行业',
    '000977': '计算机设备',
    '002230': '通信设备',
    '002352': '物流行业',
    '002415': '安防设备',
    '002458': '禽畜养殖',
    '002714': '食品加工',
    '300015': '医疗服务',
    '300124': '电气设备',
    '300146': '保健食品',
    '300750': '电池',
    '600276': '医药制造',
    '600519': '酿酒行业',
    '601012': '光伏设备',
    '601888': '旅游酒店',
    '603501': '半导体',
    '688017': '半导体',
    '688041': '半导体',
    '688047': '半导体',
    '688795': '半导体',
    '688802': '半导体',
    '688981': '半导体',
}


def fetch_stock_industry(symbol: str, market: str = 'a_stock') -> str:
    """获取个股行业分类。
    A股：优先 akshare stock_individual_info_em API，失败时使用本地映射兜底。
    港股：无免费行业接口，默认返回“港股”。
    获取失败时返回“未分类”，不阻塞主流程。
    """
    if market == 'hk_stock' or symbol.upper().startswith('HK'):
        return '港股'
    # 优先尝试 API
    try:
        df = ak.stock_individual_info_em(symbol=symbol)
        if df is not None and not df.empty:
            row = df[df['item'] == '行业']
            if not row.empty:
                val = str(row.iloc[0]['value']).strip()
                if val:
                    return val
    except Exception as e:
        logger.warning(f'[{symbol}] 行业API失败，尝试本地映射: {e}')
    # B14: API 失败时兜底本地映射
    local = _LOCAL_INDUSTRY_MAP.get(symbol)
    if local:
        return local
    return '未分类'


# ============================================================
# 统一采集入口
# ============================================================


def collect_stock_data(symbol, market, force_full=False):
    """
    对外统一接口：根据市场类型，自动调用对应的数据采集函数。
    011增加 force_full 参数透传（强制全量采集，绕过所有增量缓存）。

    参数:
        symbol: 股票代码（如 '000001' 或 '00700'）
        market: 市场（'a_stock' 或 'hk_stock'）
        force_full: True时绕过所有增量缓存，全量采集

    返回:
        dict: 各维度采集状态汇总
    """
    results = {}
    market_name = 'A股' if market == 'a_stock' else '港股'
    ff = '[FULL_REFRESH] ' if force_full else ''

    # 012-C: 获取 stock_id 用于错误日志记录
    stock_id = get_stock_id(symbol, market)

    logger.info(f'========== 开始采集{market_name} {symbol} {ff}==========')

    # K线（A股和港股统一用腾讯接口）
    try:
        results['kline'] = fetch_kline(symbol, market, force_full=force_full)
    except Exception as e:
        results['kline'] = ('failed', f'K线采集异常: {e}')
        logger.warning(f'[{symbol}] K线采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='kline',
            traceback_str=traceback.format_exc(),
        )

    # 020R-48：日线更新后同步聚合周线/月线（失败不阻塞主流程）
    if stock_id:
        try:
            agg_status, agg_msg = aggregate_period_klines(stock_id)
            results['kline_periods'] = (agg_status, agg_msg)
        except Exception as e:
            logger.warning(f'[{symbol}] 周/月线聚合异常(不阻塞): {e}')

    # 基本面
    if market == 'a_stock':
        try:
            results['fundamental'] = fetch_a_fundamental(symbol, force_full=force_full)
        except Exception as e:
            results['fundamental'] = ('failed', f'基本面采集异常: {e}')
            logger.warning(f'[{symbol}] 基本面采集失败: {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='fundamental',
                traceback_str=traceback.format_exc(),
            )
        # B10: 基本面字段补全（仅填充NULL字段，不覆盖已有值）
        try:
            if stock_id:
                # B11-API-DEDUP：检查 fetch_a_fundamental 是否已获取足够数据，避免重复调用
                conn_chk = get_connection()
                cursor_chk = conn_chk.cursor()
                cursor_chk.execute(
                    """
                    SELECT roe, gross_margin, revenue_growth, profit_growth,
                           ocf_to_net_profit, debt_ratio, current_ratio
                    FROM raw_fundamental WHERE stock_id=? ORDER BY report_date DESC LIMIT 1
                """,
                    (stock_id,),
                )
                fund_row = cursor_chk.fetchone()
                conn_chk.close()

                need_detail = True
                if fund_row:
                    non_null = sum(
                        1
                        for v in [
                            fund_row['roe'],
                            fund_row['gross_margin'],
                            fund_row['revenue_growth'],
                            fund_row['profit_growth'],
                            fund_row['ocf_to_net_profit'],
                            fund_row['debt_ratio'],
                            fund_row['current_ratio'],
                        ]
                        if v is not None
                    )
                    if non_null >= 5:
                        need_detail = False
                        logger.info(
                            f'[{symbol}] 基本面已有{non_null}个字段，跳过 fetch_fundamental_detail'
                        )

                if need_detail:
                    detail = fetch_fundamental_detail(symbol)
                    _apply_fundamental_detail(stock_id, detail)

                # B10: 股东增减持采集
                holder_val = fetch_holder_increase(symbol)
                _save_holder_increase(stock_id, holder_val)
                results['holder_increase'] = ('success', f'holder_increase={holder_val}')

                # 020R-45: 股东人数与机构持仓采集（A股专属，失败静默降级）
                hs_data = fetch_holder_structure(symbol)
                _save_holder_structure(stock_id, hs_data)
                results['holder_structure'] = (
                    'success',
                    f'holder_change={hs_data.get("holder_count_change_pct") if hs_data else None}, '
                    f'inst_ratio={hs_data.get("inst_ratio") if hs_data else None}',
                )
        except Exception as e:
            logger.warning(f'[{symbol}] B10基本面补全/股东增减持异常(不阻塞): {e}')
    else:
        try:
            results['fundamental'] = fetch_hk_fundamental(symbol, force_full=force_full)
        except Exception as e:
            results['fundamental'] = ('failed', f'港股基本面采集异常: {e}')
            logger.warning(f'[{symbol}] 港股基本面采集失败: {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='fundamental',
                traceback_str=traceback.format_exc(),
            )

    # 资金面（A股和港股统一用东方财富push2接口）
    # 红线：fetch_capital_flow 不加 force_full 参数（同日跳过基于已有真实数据，不重复采集）
    try:
        results['capital'] = fetch_capital_flow(symbol, market)
    except Exception as e:
        results['capital'] = ('failed', f'资金面采集异常: {e}')
        logger.warning(f'[{symbol}] 资金面采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='capital',
            traceback_str=traceback.format_exc(),
        )

    # DATASRC-C：北向资金 + 融资融券补齐（仅 A股，失败不阻塞主流程）
    if market == 'a_stock':
        try:
            results['north_capital'] = fetch_north_capital(symbol, market, force_full=force_full)
        except Exception as e:
            results['north_capital'] = ('failed', f'北向资金采集异常: {e}')
            logger.warning(f'[{symbol}] 北向资金采集异常(不阻塞): {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='north',
                traceback_str=traceback.format_exc(),
            )
        try:
            results['margin_balance'] = fetch_margin_balance(symbol, market, force_full=force_full)
        except Exception as e:
            results['margin_balance'] = ('failed', f'融资余额采集异常: {e}')
            logger.warning(f'[{symbol}] 融资余额采集异常(不阻塞): {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='margin',
                traceback_str=traceback.format_exc(),
            )

    # 019Y：新增数据维度（失败不阻塞主流程）
    # 五档盘口（mootdx，仅A股，实时快照，每只每天保留最新一条）
    try:
        results['orderbook'] = fetch_orderbook(symbol, market)
    except Exception as e:
        results['orderbook'] = ('failed', f'五档盘口采集异常: {e}')
        logger.warning(f'[{symbol}] 五档盘口采集异常(不阻塞): {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='orderbook',
            traceback_str=traceback.format_exc(),
        )

    # 估值数据（akshare主源 → baostock备用，日级低频，同日跳过）
    try:
        results['valuation'] = fetch_valuation(symbol, market)
    except Exception as e:
        results['valuation'] = ('failed', f'估值采集异常: {e}')
        logger.warning(f'[{symbol}] 估值采集异常(不阻塞): {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='valuation',
            traceback_str=traceback.format_exc(),
        )

    # 限售解禁（akshare，仅A股，事件级风险因子）
    try:
        results['restricted_release'] = fetch_restricted_release(symbol, market)
    except Exception as e:
        results['restricted_release'] = ('failed', f'限售解禁采集异常: {e}')
        logger.warning(f'[{symbol}] 限售解禁采集异常(不阻塞): {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='restricted_release',
            traceback_str=traceback.format_exc(),
        )

    # 业绩预告（A股；港股跳过）
    try:
        results['forecast'] = collect_forecast(stock_id, symbol, market)
    except Exception as e:
        results['forecast'] = ('failed', f'业绩预告采集异常: {e}')
        logger.warning(f'[{symbol}] 业绩预告采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='forecast',
            traceback_str=traceback.format_exc(),
        )

    # 业绩快报（A股；港股跳过）020R-50
    try:
        results['express'] = collect_express(stock_id, symbol, market)
    except Exception as e:
        results['express'] = ('failed', f'业绩快报采集异常: {e}')
        logger.warning(f'[{symbol}] 业绩快报采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='express',
            traceback_str=traceback.format_exc(),
        )

    # 消息面
    try:
        results['sentiment'] = fetch_sentiment(symbol, market, force_full=force_full)
    except Exception as e:
        results['sentiment'] = ('failed', f'消息面采集异常: {e}')
        logger.warning(f'[{symbol}] 消息面采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='sentiment',
            traceback_str=traceback.format_exc(),
        )

    logger.info(f'========== {symbol} 数据采集完成 {ff}==========')
    return results
