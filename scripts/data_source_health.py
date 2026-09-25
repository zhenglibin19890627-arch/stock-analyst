#!/usr/bin/env python3
"""数据源健康巡检脚本（只读·零写库）——腾讯 / 东方财富 / 新浪 / 通达信 mootdx 四源一键体检。

背景：项目数据源脆弱（东财 push2 系有 WAF 风控、mootdx 服务端停更、新浪接口偶有变动），
本脚本提供一键连通性体检，帮助随时掌握数据源健康度（2026-09-25 新增，t5）。

用法（在项目根目录执行）：
    python scripts/data_source_health.py           # 控制台摘要 + logs/ 报告
    python scripts/data_source_health.py --json    # 追加机器可读 JSON 输出（stdout）
    python scripts/data_source_health.py --help    # 帮助

探测纪律（防 WAF 封禁，硬编码不可调高频率）：
    1. 全局限速：任意两次网络请求（含重试/备用服务器切换）之间 ≥ 2.5 秒（红线 ≥2 秒，留余量）；
    2. 单请求超时：HTTP 连接 5s + 读取 10s（合计 ≤15s）；mootdx 走 daemon 线程 join(15s) 封顶
       （R18 模式：daemon 线程 + join(timeout)，严禁 ThreadPoolExecutor）；
    3. 重试 ≤1：腾讯/新浪/mootdx 最多 1 次重试（换备用服务器或原端点重发）；
       东方财富失败**不重试**（0 次重试，WAF 窗口式丢弃持续 2~4 分钟，二次叩门只会加深风控画像）；
    4. 只读：每源仅 1 个最小 GET / 行情请求，零写库、零 modules/ 业务代码改动、零新增依赖。

退出码：0 = 全通；1 = 降级（含预期降级）；2 = 不可用（有源异常）。
    预期降级项（mootdx 断供）失败时计入「降级」不计入「不可用」；
    北向资金停更为静态登记项（无独立可探测端点，详见报告说明），标注展示但不参与退出码判定。

状态三类（报告与控制台统一口径）：
    ✅ 正常     —— 探测成功，端点返回有效行情数据；
    ❌ 异常     —— 非已知降级源探测失败（腾讯/东财/新浪），需要关注；
    ⚠️ 预期降级 —— 已知停更/断供源的失败（mootdx 断供、北向停更），属已知事实而非新故障，
                  对应 blueprints/system.py 健康度面板「灰灯」同口径。

数据源降级链参考（README 数据源表 + modules/collector 现状）：
    - K线：腾讯（web.ifzq.gtimg.cn fqkline，主源）→ mootdx 日K（仅A股，兜底）；换手率旁路走东财 push2his；
    - 资金面：腾讯 westock（主源）→ 东财三层 → 新浪 lscjfb（vip.stock.finance.sina.com.cn 主力口径）
      → 估算兜底（R2 链序）；新浪实时行情 hq.sinajs.cn 为行情/选股快照层；
    - 北向资金：ak.stock_hsgt_individual_em 自 2024-08-16 政策性停更（B26 已降权 0.10、30 天缓存冻结）；
    - mootdx：TDX 服务端自 2026-09-10 起对 mootdx 0.11.7 停止返回行情数据（021BX t7 定案：
      4 台备用池 TCP 全通但 quotes/bars 空载荷；五档盘口为展示维度，评分链不消费，恢复后自动续采）。

本脚本刻意不 import 项目 modules/（保持独立、零副作用、零写库），探测端点与请求头
逐一对齐 modules/collector 现有实现（_http_get / _http_get_em / _fetch_capital_flow_sina /
mootdx._mootdx_verify），确保「体检通过」与「采集可用」同口径。
"""
from __future__ import annotations

import argparse
import json
import random
import re
import socket
import sys
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

import requests

# ============================================================
# 常量（探测纪律的单一事实来源，全部取保守值）
# ============================================================
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOGS_DIR = PROJECT_ROOT / 'logs'

RATE_LIMIT_SECONDS = 2.5      # 全局限速下限（秒）；红线要求 ≥2，取 2.5 留 25% 余量
RATE_JITTER_SECONDS = 0.5     # 在下限之上附加 0~0.5s 随机抖动，避免固定节拍画像
HTTP_TIMEOUT: tuple[int, int] = (5, 10)   # (连接, 读取) 秒，合计 ≤15s（与生产 _http_get_em 同款）
MOOTDX_JOIN_TIMEOUT = 15      # mootdx 探测线程 join 封顶（秒），R18：daemon 线程 + join
MOOTDX_SOCKET_TIMEOUT = 10    # mootdx 客户端 socket 超时（秒，与生产 mootdx.py 一致）
MAX_RETRIES = 1               # 每源重试上限（次）；东财固定 0 次（见 probe_eastmoney 注释）

# 最小探测标的：与生产示例同款（600276 恒瑞医药 / 000001 平安银行，皆为流动性最好的标的之一）
_SAMPLE_SH = '600276'         # 腾讯/东财/新浪 共用样本（沪市 A 股）
_MOOTDX_SAMPLE = '000001'     # mootdx 探测样本（生产 _mootdx_verify 同款）

# 迷你 UA 池（本地内嵌 3 个，不 import modules.collector.http_client 以保持零依赖、零全局副作用）
_UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
]

# mootdx 备用服务器池头部两台（与 modules/collector/mootdx.py _MOOTDX_FALLBACK_SERVERS 对齐；
# 仅取前两台 = 初始 1 次 + 最多 1 次重试，不扫全池、绝不触发 bestip 全网扫描）
_MOOTDX_POOL: list[tuple[str, int]] = [
    ('115.238.56.198', 7709),   # 浙江电信
    ('115.238.90.165', 7709),   # 浙江电信
]

# 已知降级（预期降级）登记：失败时按「预期降级」归类而非「异常」，与健康度面板灰灯同口径
_KNOWN_DEGRADED_NOTE = {
    'mootdx': (
        '已知断供：TDX 服务端自 2026-09-10 起对 mootdx 0.11.7 停止返回行情数据'
        '（021BX t7 定案：备用池 TCP 全通但 quotes/bars 空载荷；盘口为展示维度，'
        '评分链不消费，恢复后自动续采）——失败按「预期降级」而非故障'
    ),
    'north': (
        '政策性停更：ak.stock_hsgt_individual_em 自 2024-08-16 起停更（港交所政策变更），'
        'B26 已降权 0.10、30 天缓存冻结展示——已知事实，静态登记不发起探测'
    ),
}

# 状态常量（三类，报告/控制台/JSON 统一用同名中文）
ST_OK = '正常'
ST_EXPECTED = '预期降级'
ST_ERROR = '异常'

_STATUS_ICON = {ST_OK: '✅', ST_EXPECTED: '⚠️', ST_ERROR: '❌'}


# ============================================================
# 基础设施：全局限速器 / daemon 线程超时包装（R18 模式）
# ============================================================
class _RateLimiter:
    """全局限速器：任意两次网络请求（含重试、备用服务器切换、跨数据源）之间强制 ≥ 间隔。

    单进程内所有探测共享同一实例——「全局限速 ≥2 秒/请求」是硬约束，
    不允许某一源绕过（例如连续两个源背靠背发请求）。
    """

    def __init__(self, min_interval: float, jitter: float) -> None:
        self._min_interval = min_interval
        self._jitter = jitter
        self._last_ts: float | None = None
        self._lock = threading.Lock()

    def wait(self) -> float:
        """占位并阻塞至距上次请求 ≥ 间隔（+随机抖动），返回实际等待秒数。"""
        interval = self._min_interval + random.uniform(0.0, self._jitter)
        with self._lock:
            now = time.monotonic()
            wait_s = 0.0
            if self._last_ts is not None:
                wait_s = max(0.0, self._last_ts + interval - now)
            self._last_ts = now + wait_s  # 先占位再睡，保证并发调用也不失速
        if wait_s > 0:
            time.sleep(wait_s)
        return wait_s


def _run_with_timeout(fn: Callable[[], tuple[bool, str, str | None]], timeout_s: float):
    """daemon 线程 + join(timeout) 超时保护（R18 模式）。

    fn 返回 (ok, detail, error)；超时返回 (False, '', '探测超时(≤15s 封顶)')。
    """
    box: dict[str, tuple[bool, str, str | None]] = {}

    def _target() -> None:
        try:
            box['r'] = fn()
        except Exception as e:  # noqa: BLE001 —— 探测层兜底：任何异常都转为失败记录
            box['r'] = (False, '', f'{type(e).__name__}: {e}')

    t = threading.Thread(target=_target, daemon=True)
    t.start()
    t.join(timeout=timeout_s)
    if t.is_alive():
        return (False, '', f'探测超时（{timeout_s:.0f}s 封顶，daemon 线程已被放弃）')
    return box.get('r', (False, '', '探测线程无返回'))


# ============================================================
# 探测结果结构
# ============================================================
def _new_record(source: str, endpoint: str, kind: str = 'probe') -> dict[str, Any]:
    return {
        'source': source,
        'endpoint': endpoint,
        'kind': kind,          # probe=实测探测；static=静态登记（无网络请求）
        'status': ST_ERROR,
        'latency_ms': None,
        'attempts': 0,
        'detail': '',
        'error': None,
        'note': '',
    }


def _to_float(text: Any) -> float | None:
    try:
        return float(str(text).strip().strip('"').strip(';'))
    except (TypeError, ValueError):
        return None


# ============================================================
# HTTP 探测（腾讯 / 东财 / 新浪 共用骨架）
# ============================================================
_Validator = Callable[[requests.Response], tuple[bool, str]]


def _http_probe(
    record: dict[str, Any],
    url: str,
    params: dict[str, str] | None,
    headers: dict[str, str],
    validate: _Validator,
    rate: _RateLimiter,
    allow_retry: bool,
) -> dict[str, Any]:
    """只读 GET 探测骨架：全局限速 → 直连请求（timeout=(5,10)）→ 业务校验 → 记录。

    - 直连（trust_env=False + 空代理）：四源统一口径（东财见 probe_eastmoney 实测注记——
      本机系统代理进程不在线时走代理即 ProxyError，直连 + Referer 才可达）；
    - allow_retry=False 时失败即返回（东财专用：WAF 敏感源 0 重试）；
    - 校验失败（空载荷/字段缺失）与传输失败均可触发 ≤1 次重试（非东财源）。
    """
    attempts_allowed = 1 + (MAX_RETRIES if allow_retry else 0)
    last_error: str | None = None
    last_detail = ''
    for attempt in range(1, attempts_allowed + 1):
        record['attempts'] = attempt
        rate.wait()
        t0 = time.perf_counter()
        try:
            session = requests.Session()
            session.trust_env = False
            session.headers.update(headers)
            resp = session.get(
                url, params=params, timeout=HTTP_TIMEOUT, proxies={'http': None, 'https': None}
            )
            record['latency_ms'] = round((time.perf_counter() - t0) * 1000)
            if resp.status_code != 200:
                last_error = f'HTTP {resp.status_code}'
                last_detail = ''
                continue
            ok, detail = validate(resp)
            if ok:
                record['status'] = ST_OK
                record['detail'] = detail
                record['error'] = None
                return record
            last_error = '响应内容校验未通过（空载荷或字段缺失）'
            last_detail = detail
        except (requests.RequestException, ValueError) as e:
            record['latency_ms'] = round((time.perf_counter() - t0) * 1000)
            last_error = f'{type(e).__name__}: {e}'
            last_detail = ''
    record['status'] = ST_ERROR
    record['error'] = last_error
    record['detail'] = last_detail
    return record


def _validate_tencent(resp: requests.Response) -> tuple[bool, str]:
    """腾讯实时行情校验：GBK 解码 → `~` 分段 ≥35 → [3] 最新价 > 0（与 _refresh_kline_today_bar 同口径）。"""
    resp.encoding = 'gbk'
    parts = resp.text.split('~')
    if len(parts) < 35:
        return False, f'响应仅 {len(parts)} 段（预期 ≥35）'
    price = _to_float(parts[3])
    if not price or price <= 0:
        return False, f'最新价异常（parts[3]={parts[3]!r}）'
    return True, f'实测行情 {parts[1]}：最新价 {price}，{len(parts)} 段字段'


def _validate_eastmoney(resp: requests.Response) -> tuple[bool, str]:
    """东财 push2his 日K 校验：JSON → data.klines 非空（与 021BW 换手率旁路同端点同口径）。"""
    payload = resp.json()
    data = payload.get('data') if isinstance(payload, dict) else None
    klines = (data or {}).get('klines') or []
    if not klines:
        return False, '响应 JSON 无 data.klines（空载荷，可能被 WAF 拦截）'
    latest = str(klines[-1]).split(',')
    tail = f'收盘 {latest[2]}' if len(latest) > 2 else '字段不全'
    return True, f'实测日K {len(klines)} 根，最新 {latest[0]} {tail}'


def _validate_sina(resp: requests.Response) -> tuple[bool, str]:
    """新浪实时行情校验：GBK → hq_str_sh600276="…" → 逗号分段 ≥10 → [3] 最新价 > 0。"""
    resp.encoding = 'gbk'
    match = re.search(r'hq_str_sh600276="([^"]*)"', resp.text)
    if not match:
        return False, '响应中无 hq_str_sh600276 行情串（接口变动或 Referer 被拒）'
    parts = match.group(1).split(',')
    if len(parts) < 10:
        return False, f'行情串仅 {len(parts)} 段（预期 ≥10）'
    close = _to_float(parts[3])
    if not close or close <= 0:
        return False, f'最新价异常（parts[3]={parts[3]!r}）'
    return True, f'实测行情 {parts[0]}：最新价 {close}，{len(parts)} 段字段'


def probe_tencent(rate: _RateLimiter) -> dict[str, Any]:
    """腾讯行情（qt.gtimg.cn）：1 次最小实时行情 GET（生产盘中刷新同端点）。"""
    record = _new_record(
        '腾讯行情（qt.gtimg.cn）', 'https://qt.gtimg.cn/q=sh600276'
    )
    record['note'] = '实时行情/K线主源；港资金面估算与腾讯估值（PE/PB）同域名族'
    return _http_probe(
        record,
        f'https://qt.gtimg.cn/q=sh{_SAMPLE_SH}',
        None,
        {'User-Agent': random.choice(_UA_POOL), 'Referer': 'https://finance.qq.com'},
        _validate_tencent,
        rate,
        allow_retry=True,
    )


def probe_eastmoney(rate: _RateLimiter) -> dict[str, Any]:
    """东方财富（push2his）：1 次最小日K GET（021BW 换手率旁路同端点）。

    请求口径（2026-09-25 本机三组对照实测定案）：
    - **直连**（trust_env=False + 空代理）：与生产 _http_get_em 默认（EM_USE_PROXY=False）同口径。
      本机系统代理（127.0.0.1:7897）配置存在但进程不在线 → 走代理即 ProxyError，直连才可达；
    - **带浏览器 Referer**（https://quote.eastmoney.com/）：实测直连无 Referer 会被服务端
      无响应掐断（RemoteDisconnected），带 Referer 稳定 200。
    ⚠️ WAF 最敏感源：失败不重试（0 次重试），请求参数取最小集（fields2 仅 3 列、
    时间窗 7 天），绝不触发 push2 编号子域轮换/多轮退避等生产行为。
    """
    record = _new_record(
        '东方财富（push2 系）', 'https://push2his.eastmoney.com/api/qt/stock/kline/get'
    )
    record['note'] = (
        '资金面兜底/换手率旁路/业绩预告低频源；探测与生产 _http_get_em 默认同口径'
        '（直连 + 浏览器 Referer）；生产另内置熔断冷却、编号子域轮换与代理开关'
    )
    beg = '20260918'  # 固定 7 天窗（避免每次运行拼日期；只读探测无需新鲜窗口）
    return _http_probe(
        record,
        'https://push2his.eastmoney.com/api/qt/stock/kline/get',
        {
            'secid': f'1.{_SAMPLE_SH}',
            'fields1': 'f1,f2,f3,f4,f5,f6',
            'fields2': 'f51,f52,f53',   # 最小列集：日期/开盘/收盘
            'klt': '101',               # 日K
            'fqt': '1',                 # 前复权
            'beg': beg,
            'end': '20500101',
        },
        {
            'User-Agent': random.choice(_UA_POOL),
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'zh-CN,zh;q=0.8',
            'Referer': 'https://quote.eastmoney.com/',
        },
        _validate_eastmoney,
        rate,
        allow_retry=False,  # 东财 0 重试：WAF 窗口期二次叩门只会加深风控画像
    )


def probe_sina(rate: _RateLimiter) -> dict[str, Any]:
    """新浪行情（hq.sinajs.cn）：1 次最小实时行情 GET（生产 _fetch_capital_flow_sina 同端点同请求头）。

    说明：新浪主力口径 lscjfb（vip.stock.finance.sina.com.cn）与 hq.sinajs.cn 同属新浪源族，
    按「每源 1 次请求」纪律不单独探测（该端点仅在资金面降级链低频使用）。
    """
    record = _new_record('新浪（hq.sinajs.cn）', 'http://hq.sinajs.cn/list=sh600276')
    record['note'] = '行情/选股快照层；主力资金口径 lscjfb 同属新浪源族（本脚本不重复探测）'
    return _http_probe(
        record,
        f'http://hq.sinajs.cn/list=sh{_SAMPLE_SH}',
        None,
        {'User-Agent': random.choice(_UA_POOL), 'Referer': 'https://finance.sina.com.cn'},
        _validate_sina,
        rate,
        allow_retry=True,
    )


# ============================================================
# mootdx 探测（TCP/通达信协议，非 HTTP）
# ============================================================
def _mootdx_lib_probe_once(rate: _RateLimiter, host: str, port: int) -> tuple[bool, str, str | None]:
    """mootdx 库单台探测：与生产 _mootdx_verify 同款——Quotes.factory + quotes('000001')。

    只读实时行情，不触碰 bars/盘口，不写任何数据；heartbeat=False 避免遗留心跳线程。
    返回 (ok, detail, error)。空载荷不是异常而是「已知断供态」证据，用 ok=False + detail 表达。
    """
    from mootdx.quotes import Quotes  # 延迟导入：mootdx 为可选源（未安装则上层走 TCP 探测）

    rate.wait()
    t0 = time.perf_counter()
    client = Quotes.factory(
        market='std', server=(host, port), timeout=MOOTDX_SOCKET_TIMEOUT, heartbeat=False
    )
    try:
        df = client.quotes(symbol=_MOOTDX_SAMPLE)
    finally:
        try:
            client.close()
        except Exception:  # noqa: BLE001 —— close 失败不影响判定
            pass
    latency = round((time.perf_counter() - t0) * 1000)
    if df is None or len(df) == 0:
        return False, f'{host}:{port} TCP 可达但实时行情返回空载荷（{latency}ms）', None
    price = _to_float(df.iloc[0].get('price'))
    return True, f'{host}:{port} 实时行情正常（{_MOOTDX_SAMPLE} 最新价 {price}，{latency}ms）', None


def _mootdx_tcp_probe_once(rate: _RateLimiter, host: str, port: int) -> tuple[bool, str, str | None]:
    """纯 TCP 连接探测（mootdx 库未安装时的兜底）：仅验证端口可达，不发送任何业务协议。"""
    rate.wait()
    t0 = time.perf_counter()
    with socket.create_connection((host, port), timeout=MOOTDX_SOCKET_TIMEOUT):
        latency = round((time.perf_counter() - t0) * 1000)
    return True, f'{host}:{port} TCP 可达（{latency}ms，仅连接层）', None


def probe_mootdx(rate: _RateLimiter) -> dict[str, Any]:
    """通达信 mootdx 探测：初始 1 次 + 最多 1 次重试（连接级失败才换池内下一台）。

    - mootdx 库可用：quotes 实测（连接正常但空载荷 = 已知断供态，立即停止不再换机——
      021BX t7 已实证全池同状态，多台探测纯属浪费请求）；
    - 库未安装：退化为一台 TCP 连接探测，并如实标注「仅连接层」；
    - 任何失败归类「预期降级」（已知断供项），计入降级不计入不可用。
    """
    host, port = _MOOTDX_POOL[0]
    record = _new_record('通达信 mootdx（TDX 行情）', f'tcp://{host}:{port}（备用池头部）')
    record['note'] = 'K线兜底/五档盘口源（仅A股）；' + _KNOWN_DEGRADED_NOTE['mootdx']
    try:
        import mootdx  # noqa: F401 —— 仅探测库可用性

        lib_available = True
    except ImportError:
        lib_available = False

    probe_fn = _mootdx_lib_probe_once if lib_available else _mootdx_tcp_probe_once
    detail_parts: list[str] = []
    for idx, (h, p) in enumerate(_MOOTDX_POOL[:1 + MAX_RETRIES]):
        record['attempts'] = idx + 1
        ok, detail, error = _run_with_timeout(
            lambda h=h, p=p: probe_fn(rate, h, p), MOOTDX_JOIN_TIMEOUT
        )
        record['endpoint'] = f'tcp://{h}:{p}（备用池第{idx + 1}台）'
        record['error'] = error
        if ok:
            record['status'] = ST_OK
            record['detail'] = detail + ('' if lib_available else '（mootdx 库未安装，仅验证连接层）')
            record['error'] = None
            return record
        detail_parts.append(error or detail)
        if error is None:
            # 非超时、非异常的失败 = 「空载荷」已知断供态：证据已足，不再换机重试
            record['status'] = ST_EXPECTED
            record['detail'] = detail
            record['error'] = None
            return record
    record['status'] = ST_EXPECTED  # 已知断供源：失败按预期降级归类（非故障）
    record['detail'] = '；'.join(x for x in detail_parts if x)
    return record


# ============================================================
# 静态登记项（无网络请求）
# ============================================================
def static_north_capital() -> dict[str, Any]:
    """北向资金：政策性停更（2024-08-16 起）静态登记。

    不发起网络请求：该源无独立可探测端点（akshare 路径随东财主链），且停更为永久性已知事实；
    因此标注展示、不参与退出码判定（详见报告「状态口径」节）。
    """
    record = _new_record('北向资金（akshare·东财沪深港通）', '（静态登记，无独立探测端点）', kind='static')
    record['status'] = ST_EXPECTED
    record['detail'] = _KNOWN_DEGRADED_NOTE['north']
    record['attempts'] = 0
    return record


# ============================================================
# 汇总 / 报告输出
# ============================================================
def _summarize(probes: list[dict[str, Any]], statics: list[dict[str, Any]]) -> tuple[int, str]:
    """退出码聚合：探测源 任一异常→2；任一预期降级→1；否则 0。静态登记项不计码（报告内说明）。"""
    if any(p['status'] == ST_ERROR for p in probes):
        return 2, '不可用：存在非已知降级源的探测失败（腾讯/东财/新浪），请优先排查'
    if any(p['status'] == ST_EXPECTED for p in probes) or any(
        s['status'] == ST_EXPECTED for s in statics
    ):
        return 1, '降级：探测源存在失败或已知降级项（详见报告），主链可自动降级运行'
    return 0, '全通：四源探测全部正常'


def _fmt_row(rec: dict[str, Any]) -> str:
    latency = f"{rec['latency_ms']}ms" if rec['latency_ms'] is not None else '—'
    attempts = f"{rec['attempts']}次" if rec['attempts'] else '—'
    return (
        f"| {rec['source']} | {_STATUS_ICON[rec['status']]} {rec['status']} "
        f"| {latency} | {attempts} | {rec['endpoint']} |"
    )


def build_markdown(
    probes: list[dict[str, Any]],
    statics: list[dict[str, Any]],
    exit_code: int,
    conclusion: str,
    generated_at: datetime,
) -> str:
    """生成中文 Markdown 巡检报告（UTF-8 无 BOM）。"""
    now = generated_at.strftime('%Y-%m-%d %H:%M')
    lines = [
        '# 数据源健康巡检报告',
        '',
        f'- **生成时间**：{now}',
        f'- **总体结论**：退出码 {exit_code}（0=全通 / 1=降级 / 2=不可用）——{conclusion}',
        '- **探测纪律**：全局限速 ≥2.5s/请求（含抖动）、单请求超时 ≤15s、重试 ≤1'
        '（东财 0 重试）；全部只读，零写库、零 modules/ 业务代码改动。',
        '',
        '## 摘要（四源实测）',
        '',
        '| 数据源 | 状态 | 延迟 | 请求次数 | 探测端点 |',
        '|--------|------|------|----------|----------|',
    ]
    lines.extend(_fmt_row(p) for p in probes)
    lines += [
        '',
        '## 静态登记（不发起探测）',
        '',
        '| 数据源 | 状态 | 说明 |',
        '|--------|------|------|',
    ]
    lines.extend(
        f"| {s['source']} | {_STATUS_ICON[s['status']]} {s['status']} | {s['detail']} |"
        for s in statics
    )
    lines += [
        '',
        '## 各源明细',
        '',
    ]
    for p in probes:
        lines += [
            f"### {p['source']}——{_STATUS_ICON[p['status']]} {p['status']}",
            '',
            f"- 端点：`{p['endpoint']}`",
            f"- 延迟：{p['latency_ms'] if p['latency_ms'] is not None else '—'} ms；"
            f"请求次数：{p['attempts']}",
            f"- 结果：{p['detail'] or '—'}",
        ]
        if p['error']:
            lines.append(f"- 失败原因：`{p['error']}`")
        lines.append(f"- 降级链定位：{p['note']}")
        lines.append('')
    lines += [
        '## 状态口径',
        '',
        '- **✅ 正常**：探测成功且返回有效行情数据；',
        '- **❌ 异常**：非已知降级源（腾讯/东财/新浪）探测失败——需要关注，可能影响当日采集；',
        '- **⚠️ 预期降级**：已知停更/断供项（mootdx 断供、北向停更）——已知事实而非新故障，',
        '  与运行时健康度面板（`/api/health/sources`）灰灯同口径；失败计入「降级」（退出码 1），不计入「不可用」。',
        '',
        '> 北向资金为静态登记项：无独立可探测端点且停更为永久性已知事实，\n'
        '> 若计入退出码将使「0=全通」永不可达，故仅登记展示、不参与退出码判定\n'
        '>（mootdx 为实测探测项，其结果正常参与判定）。',
        '',
        '## 数据源降级链参考（README 数据源表 / modules/collector 现状）',
        '',
        '- **K线**：腾讯（web.ifzq.gtimg.cn fqkline，主源）→ mootdx 日K（仅A股兜底）；换手率旁路走东财 push2his；',
        '- **资金面**：腾讯 westock（主源）→ 东财三层 → 新浪 lscjfb 主力口径 → 估算兜底（R2 链序，真实数据不被降级源覆盖）；',
        '- **东财风控**：生产内置熔断冷却、push2 编号子域轮换、全局最小请求间隔；'
        '本脚本对东财仅 1 次最小请求（直连 + 浏览器 Referer，与生产 _http_get_em 默认口径一致）、'
        '失败不重试；',
        '- **北向资金**：2024-08-16 政策性停更（B26 降权 0.10、30 天缓存冻结）；',
        '- **mootdx**：2026-09-10 起 TDX 服务端对 mootdx 0.11.7 停止返回数据（TCP 全通、载荷为空），'
        '五档盘口为展示维度、评分链不消费，恢复后自动续采。',
        '',
        '---',
        '*本报告由 scripts/data_source_health.py 自动生成（只读巡检，不构成任何投资建议）。*',
    ]
    return '\n'.join(lines) + '\n'


def build_json_payload(
    probes: list[dict[str, Any]],
    statics: list[dict[str, Any]],
    exit_code: int,
    conclusion: str,
    generated_at: datetime,
    report_path: Path,
) -> dict[str, Any]:
    """机器可读 JSON 载荷（--json 时输出到 stdout）。"""
    return {
        'generated_at': generated_at.strftime('%Y-%m-%d %H:%M:%S'),
        'rate_limit_seconds': RATE_LIMIT_SECONDS,
        'timeout_seconds': sum(HTTP_TIMEOUT),
        'max_retries': MAX_RETRIES,
        'status_legend': {ST_OK: '探测成功', ST_EXPECTED: '已知停更/断供（预期降级）', ST_ERROR: '探测失败'},
        'probes': probes,
        'static_registry': statics,
        'summary': {
            'ok': sum(1 for p in probes if p['status'] == ST_OK),
            'expected_degraded': sum(1 for p in probes if p['status'] == ST_EXPECTED)
            + sum(1 for s in statics if s['status'] == ST_EXPECTED),
            'error': sum(1 for p in probes if p['status'] == ST_ERROR),
            'exit_code': exit_code,
            'exit_meaning': conclusion,
            'exit_code_scope': '退出码由四个实测探测源聚合；静态登记项仅展示不参与判定（见报告说明）',
        },
        'report': str(report_path.relative_to(PROJECT_ROOT)),
    }


# ============================================================
# 入口
# ============================================================
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog='data_source_health.py',
        description='数据源健康巡检（只读）：腾讯/东方财富/新浪/mootdx 四源限速探测，'
        '产出 Markdown 报告与退出码（0=全通 1=降级 2=不可用）。',
        epilog='示例：python scripts/data_source_health.py --json',
    )
    parser.add_argument(
        '--json',
        action='store_true',
        help='在控制台摘要之后追加输出机器可读 JSON（stdout，UTF-8）',
    )
    args = parser.parse_args(argv)

    # 控制台编码兜底：GBK 管道环境下状态图标（✅/⚠️/❌）不可编码时降级为 '?'，避免 UnicodeEncodeError
    # （真实 Windows 控制台走 WriteConsoleW 不受影响；报告文件始终 UTF-8 无损）
    try:
        if sys.stdout.encoding and sys.stdout.encoding.lower() not in ('utf-8', 'utf8'):
            sys.stdout.reconfigure(errors='replace')
    except (AttributeError, ValueError):  # noqa: BLE001 —— 非 TextIO 场景忽略
        pass

    generated_at = datetime.now()
    rate = _RateLimiter(RATE_LIMIT_SECONDS, RATE_JITTER_SECONDS)

    # 每源恰好 1 个最小只读请求（含重试上限），顺序探测共享全局限速器
    probes = [
        probe_tencent(rate),
        probe_eastmoney(rate),
        probe_sina(rate),
        probe_mootdx(rate),
    ]
    statics = [static_north_capital()]

    exit_code, conclusion = _summarize(probes, statics)

    # 写报告（logs/ 不存在则创建；同分钟重跑加秒后缀防覆盖）
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    report_path = LOGS_DIR / f'data_source_health_{generated_at.strftime("%Y%m%d_%H%M")}.md'
    if report_path.exists():
        report_path = LOGS_DIR / f'data_source_health_{generated_at.strftime("%Y%m%d_%H%M%S")}.md'
    report_path.write_text(
        build_markdown(probes, statics, exit_code, conclusion, generated_at), encoding='utf-8'
    )

    # 控制台摘要
    print('=' * 62)
    print('数据源健康巡检（只读·限速探测）')
    print('=' * 62)
    for p in probes:
        latency = f"{p['latency_ms']}ms" if p['latency_ms'] is not None else '—'
        print(f"{_STATUS_ICON[p['status']]} {p['source']:<22} {p['status']:<5} {latency:>8}")
        if p['detail']:
            print(f"    └ {p['detail']}")
        if p['error']:
            print(f"    └ 失败原因: {p['error']}")
    for s in statics:
        print(f"{_STATUS_ICON[s['status']]} {s['source']:<22} {s['status']:<5} （静态登记，不探测）")
        print(f"    └ {s['detail']}")
    print('-' * 62)
    print(f'结论：退出码 {exit_code}（0=全通 1=降级 2=不可用）——{conclusion}')
    print(f'报告：{report_path}')
    print(f'纪律：全局限速 ≥{RATE_LIMIT_SECONDS}s/请求 · 单请求超时 ≤{sum(HTTP_TIMEOUT)}s · 重试 ≤{MAX_RETRIES}')

    if args.json:
        payload = build_json_payload(probes, statics, exit_code, conclusion, generated_at, report_path)
        print('-' * 62)
        print(json.dumps(payload, ensure_ascii=False, indent=2))

    return exit_code


if __name__ == '__main__':
    sys.exit(main())
