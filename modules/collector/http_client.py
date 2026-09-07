"""HTTP 层：UA 池、requests.Session 无代理补丁（必须先于 akshare 导入）、代理健康、_http_get/_http_get_em/retry/_call_with_timeout、东财全局最小间隔状态。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
# ============================================================
# 解决系统代理干扰问题
# 有些用户电脑装了 Clash/V2Ray 等代理软件，默认会读取系统代理配置。
# 对于腾讯/新浪接口：直连即可，需禁用代理。
# 对于东方财富 push2 接口：直连可能被封锁，需要通过系统代理访问。
# 因此采用分策略处理：akshare 内部统一禁用代理（直连），
# 东方财富资金流向单独使用智能回退逻辑（先直连再走代理）。
# ============================================================
import random as _random
import time
import urllib.request as _urlreq
from datetime import datetime

import requests

from config import EM_USE_PROXY, MAX_RETRIES
from modules.collector._env import _CN_TZ, logger

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
import akshare as ak  # noqa: F401  （补丁先于 akshare 导入的顺序锚点；ak 经 facade 再导出）

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


def _rotate_em_host(url):
    """东财 push2/push2his 编号子域轮换（1~99）：不同边缘节点可绕部分 WAF 拦截。

    OPT-3：原位于 capital 段（3274），因唯一调用方是 _http_get_em 而随迁至本模块。
    """
    for base in ('//push2.eastmoney.com/', '//push2his.eastmoney.com/'):
        if base in url:
            return url.replace(base, f'//{_random.randint(1, 99)}.{base[2:]}')
    return url


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
_EM_LAST_REQUEST_TS = 0.0    # 东财全局最小请求间隔记录
_EM_MIN_INTERVAL_SECONDS = 0.5  # 东财请求全局最小间隔（社区阈值：<5 次/秒）

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


def _call_ak_with_timeout(fn, label, timeout=30):
    """019P P3（必需）：daemon 线程 join(timeout) 包装 akshare 基本面接口调用。
    019I 模式同型，自建于基本面区域（THS 的 _call_with_timeout 为函数内闭包，不可复用）。
    超时返回 (None, True)；正常返回 (result, False)。abstract/analysis_indicator 严禁裸调用。

    OPT-3：原位于 fundamental 段（1094），valuation/fundamental 两域共用而随 HTTP 工具族迁入本模块。
    默认 30s = 原 fundamental 段 _FUND_ABSTRACT_TIMEOUT 的值（两域全部 3 个调用点均依赖默认值）。
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
