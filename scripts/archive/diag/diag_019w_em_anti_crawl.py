#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
019W 批次诊断脚本：东方财富反爬阻断诊断（只诊断、不落地）

任务书：docs/tasks/dev_tasks_20260810_019W_em_anti_crawl_diagnosis.md
红线：
  1. 零生产改动 —— 本脚本不 import 任何生产模块、不改任何生产文件；
  2. 零数据库写入 —— 本脚本完全不连接 stock_analyst.db；
  3. 请求量封顶 —— 东财域名测试请求总量 ≤150，全部串行，相邻请求间隔 ≥2 秒；
  4. 代理边界 —— 仅进程内 env/proxies 参数设置，退出前恢复，不碰注册表、不装依赖。

用法（在项目根目录执行）：
  python scripts/diag_019w_em_anti_crawl.py t1      # T1 基线复现
  python scripts/diag_019w_em_anti_crawl.py t2a     # T2a 请求头策略矩阵
  python scripts/diag_019w_em_anti_crawl.py t2b     # T2b 端点对照
  python scripts/diag_019w_em_anti_crawl.py t2c     # T2c 直连/代理/环境变量三态
  python scripts/diag_019w_em_anti_crawl.py t2d     # T2d 间隔梯度
  python scripts/diag_019w_em_anti_crawl.py t3      # T3 本机网络环境只读排查
  python scripts/diag_019w_em_anti_crawl.py t4      # T4 akshare 对照
  python scripts/diag_019w_em_anti_crawl.py summary # 打印累计请求计数

输出约定：每行一条 JSON 记录（步骤/端点/模式/是否成功/HTTP状态/错误类型/错误摘要/耗时），
末尾打印累计计数器。脚本自身状态（计数）只写内存，不落盘。
"""

import argparse
import io
import json
import os
import socket
import subprocess
import sys
import time
import traceback
import urllib.parse
import urllib.request
import winreg
from datetime import datetime

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")

# ---------------------------------------------------------------------------
# 全局请求计数与间隔控制（红线 3）
# ---------------------------------------------------------------------------
EM_REQUEST_BUDGET = 150
MIN_INTERVAL_SEC = 2.0
_last_em_ts = None


class Budget:
    em = 0
    sina = 0
    other = 0


def _log(line):
    print(line, flush=True)


def _guard_interval():
    """东财请求间强制 ≥2 秒串行间隔"""
    global _last_em_ts
    now = time.time()
    if _last_em_ts is not None:
        wait = MIN_INTERVAL_SEC - (now - _last_em_ts)
        if wait > 0:
            time.sleep(wait)
    _last_em_ts = time.time()


def em_request(url, params=None, headers=None, proxies=None, trust_env=None,
               timeout=(6, 12), label="", step=""):
    """东财域名单次请求（计入预算，强制串行间隔）。返回结构化记录。"""
    import requests  # 仅用 requests（已安装），不新增依赖

    if Budget.em >= EM_REQUEST_BUDGET:
        _log(json.dumps({"step": step, "label": label, "error": "BUDGET_EXCEEDED",
                         "msg": f"东财请求已达上限 {EM_REQUEST_BUDGET}"}, ensure_ascii=False))
        return None
    _guard_interval()
    Budget.em += 1
    rec = {
        "ts": datetime.now().strftime("%H:%M:%S"),
        "step": step,
        "label": label,
        "host": urllib.parse.urlparse(url).netloc,
        "path": url,
        "ok": False,
        "http_status": None,
        "err_type": None,
        "err_msg": None,
        "traceback": None,
        "elapsed_s": None,
        "cum_em_count": Budget.em,
    }
    t0 = time.time()
    try:
        sess = requests.Session()
        sess.trust_env = trust_env if trust_env is not None else True
        if proxies is not None:
            resp = sess.get(url, params=params, headers=headers, proxies=proxies, timeout=timeout)
        else:
            resp = sess.get(url, params=params, headers=headers, timeout=timeout)
        rec["elapsed_s"] = round(time.time() - t0, 2)
        rec["http_status"] = resp.status_code
        if resp.status_code == 200:
            rec["ok"] = True
        rec["resp_head"] = resp.text[:200] if resp.status_code == 200 else ""
    except Exception as e:
        rec["elapsed_s"] = round(time.time() - t0, 2)
        rec["err_type"] = type(e).__name__
        rec["err_msg"] = str(e)[:300]
        rec["traceback"] = traceback.format_exc()
    _log(json.dumps(rec, ensure_ascii=False))
    return rec


# ---------------------------------------------------------------------------
# 生产参数（只读参考 data_collector.py，不改动任何生产代码）
# ---------------------------------------------------------------------------
FFLOW_PARAMS = {
    "lmt": "0",
    "klt": "101",
    "secid": "1.600519",
    "fields1": "f1,f2,f3,f7",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65",
    "ut": "b2884a393a59ad640022922a3e90d46a5",
}
FFLOW_PARAMS_P2 = {
    "secid": "1.600519",
    "lmt": 10,
    "klt": "101",
    "fields1": "f1,f2,f3,f7",
    "fields2": "f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63",
    "ut": "b2884a393a59ad640022922a3e90d46a5",
}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
HEADERS_PROD = {"User-Agent": UA, "Accept": "application/json, text/plain, */*", "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8"}
HEADERS_FULL = {
    "User-Agent": UA,
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection": "keep-alive",
    "Referer": "https://quote.eastmoney.com/",
    "Origin": "https://quote.eastmoney.com",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-site",
    "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="120", "Google Chrome";v="120"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Windows"',
}
HEADERS_MIN = {"User-Agent": UA}
PUSH2HIS = "https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get"
PUSH2 = "https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get"
PROXY_ADDR = "http://127.0.0.1:7897"


def _no_proxy():
    return {"http": None, "https": None}


# ---------------------------------------------------------------------------
# T1 基线复现：最小化请求确认失败签名
# ---------------------------------------------------------------------------
def t1():
    _log("== T1 基线复现：push2his / push2 fflow 端点（各 3 次，直连，生产参数）==")
    for i in range(3):
        em_request(PUSH2HIS, params=FFLOW_PARAMS, headers=HEADERS_PROD,
                   proxies=_no_proxy(), trust_env=False, label=f"push2his-direct-{i + 1}", step="T1")
    for i in range(3):
        em_request(PUSH2, params=FFLOW_PARAMS_P2, headers=HEADERS_PROD,
                   proxies=_no_proxy(), trust_env=False, label=f"push2-direct-{i + 1}", step="T1")


# ---------------------------------------------------------------------------
# T2a 请求头策略矩阵
# ---------------------------------------------------------------------------
def t2a():
    _log("== T2a 请求头矩阵：完整浏览器头 / 极简头 / 生产头（各 3 次，push2his，直连）==")
    for i in range(3):
        em_request(PUSH2HIS, params=FFLOW_PARAMS, headers=HEADERS_FULL,
                   proxies=_no_proxy(), trust_env=False, label=f"full-browser-hdrs-{i + 1}", step="T2a")
    for i in range(3):
        em_request(PUSH2HIS, params=FFLOW_PARAMS, headers=HEADERS_MIN,
                   proxies=_no_proxy(), trust_env=False, label=f"minimal-hdrs-{i + 1}", step="T2a")
    for i in range(3):
        em_request(PUSH2HIS, params=FFLOW_PARAMS, headers=HEADERS_PROD,
                   proxies=_no_proxy(), trust_env=False, label=f"prod-like-hdrs-{i + 1}", step="T2a")
    _log("== T2a-extra 原生 TLS 栈对照（curl.exe/Schannel，不带自定义头）==")
    qs = "&".join(f"{k}={v}" for k, v in FFLOW_PARAMS.items())
    for i in range(2):
        _guard_interval()
        Budget.em += 1
        rec = {"ts": datetime.now().strftime("%H:%M:%S"), "step": "T2a-extra", "label": f"curl-native-{i + 1}",
               "host": "push2his.eastmoney.com", "path": "curl.exe -sS -o NUL -w %{http_code}", "ok": False,
               "http_status": None, "err_type": None, "err_msg": None, "elapsed_s": None,
               "cum_em_count": Budget.em}
        t0 = time.time()
        try:
            r = subprocess.run(
                ["curl.exe", "-sS", "-o", "NUL", "-w", "%{http_code}", "--connect-timeout", "6",
                 "--max-time", "15", f"{PUSH2HIS}?{qs}"],
                capture_output=True, text=True, timeout=20)
            rec["elapsed_s"] = round(time.time() - t0, 2)
            code = r.stdout.strip()
            rec["http_status"] = code if code else None
            rec["ok"] = code == "200"
            if not rec["ok"]:
                rec["err_msg"] = r.stderr.strip()[:300]
                rec["err_type"] = "curl_nonzero_or_non200"
        except Exception as e:
            rec["elapsed_s"] = round(time.time() - t0, 2)
            rec["err_type"] = type(e).__name__
            rec["err_msg"] = str(e)[:300]
        _log(json.dumps(rec, ensure_ascii=False))
    _log("== T2a-extra2 指纹隔离：curl 携带 requests 同款请求头（UA/Accept/Accept-Language）==")
    for i in range(2):
        _guard_interval()
        Budget.em += 1
        rec = {"ts": datetime.now().strftime("%H:%M:%S"), "step": "T2a-extra2",
               "label": f"curl-with-requests-hdrs-{i + 1}",
               "host": "push2his.eastmoney.com", "path": "curl.exe -H UA/Accept/Accept-Language", "ok": False,
               "http_status": None, "err_type": None, "err_msg": None, "elapsed_s": None,
               "cum_em_count": Budget.em}
        t0 = time.time()
        try:
            r = subprocess.run(
                ["curl.exe", "-sS", "-o", "NUL", "-w", "%{http_code}", "--connect-timeout", "6",
                 "--max-time", "15",
                 "-H", f"User-Agent: {UA}",
                 "-H", "Accept: application/json, text/plain, */*",
                 "-H", "Accept-Language: zh-CN,zh;q=0.9,en;q=0.8",
                 f"{PUSH2HIS}?{qs}"],
                capture_output=True, text=True, timeout=20)
            rec["elapsed_s"] = round(time.time() - t0, 2)
            code = r.stdout.strip()
            rec["http_status"] = code if code else None
            rec["ok"] = code == "200"
            if not rec["ok"]:
                rec["err_msg"] = r.stderr.strip()[:300]
                rec["err_type"] = "curl_nonzero_or_non200"
        except Exception as e:
            rec["elapsed_s"] = round(time.time() - t0, 2)
            rec["err_type"] = type(e).__name__
            rec["err_msg"] = str(e)[:300]
        _log(json.dumps(rec, ensure_ascii=False))


# ---------------------------------------------------------------------------
# T2b 端点对照：域名级 vs 接口级
# ---------------------------------------------------------------------------
def t2b():
    _log("== T2b 端点对照：quote 行情 / kline 历史 / 主站（各 2 次 + 主站 1 次）==")
    quote = "https://push2.eastmoney.com/api/qt/stock/get"
    quote_params = {"secid": "1.600519", "fields": "f43,f57,f58", "ut": "fa5fd1943c7b386f172d6893dbfba10b"}
    kline = "https://push2his.eastmoney.com/api/qt/stock/kline/get"
    kline_params = {"secid": "1.600519", "klt": "101", "fqt": "1", "lmt": "5",
                    "fields1": "f1,f2,f3", "fields2": "f51,f52,f53,f54,f55,f56,f57",
                    "ut": "fa5fd1943c7b386f172d6893dbfba10b"}
    for i in range(2):
        em_request(quote, params=quote_params, headers=HEADERS_PROD,
                   proxies=_no_proxy(), trust_env=False, label=f"quote-get-{i + 1}", step="T2b")
    for i in range(2):
        em_request(kline, params=kline_params, headers=HEADERS_PROD,
                   proxies=_no_proxy(), trust_env=False, label=f"kline-get-{i + 1}", step="T2b")
    em_request("https://www.eastmoney.com/", headers=HEADERS_PROD,
               proxies=_no_proxy(), trust_env=False, label="www-home", step="T2b")


# ---------------------------------------------------------------------------
# T2c 直连 / 系统代理 / 显式代理环境变量 三态对照
# ---------------------------------------------------------------------------
def t2c():
    _log("== T2c 连接三态：直连 / 显式代理 127.0.0.1:7897 / 环境变量代理（各 3 次，push2his）==")
    _log("== 前置说明：本 shell 存在 NO_PROXY 环境变量，getproxies() 仅返回 {'no': ...}（掩盖注册表代理）==")
    _log(json.dumps({"step": "T2c", "note": "getproxies_with_NO_PROXY", "value": urllib.request.getproxies()}, ensure_ascii=False))
    saved_no_proxy = os.environ.pop("NO_PROXY", None)
    _log(json.dumps({"step": "T2c", "note": "getproxies_after_pop_NO_PROXY", "value": urllib.request.getproxies()}, ensure_ascii=False))
    if saved_no_proxy is not None:
        os.environ["NO_PROXY"] = saved_no_proxy
    for i in range(3):
        em_request(PUSH2HIS, params=FFLOW_PARAMS, headers=HEADERS_PROD,
                   proxies=_no_proxy(), trust_env=False, label=f"direct-{i + 1}", step="T2c")
    for i in range(3):
        em_request(PUSH2HIS, params=FFLOW_PARAMS, headers=HEADERS_PROD,
                   proxies={"http": PROXY_ADDR, "https": PROXY_ADDR}, trust_env=False,
                   label=f"explicit-proxy7897-{i + 1}", step="T2c")
    # 环境变量态：临时设置 http_proxy/https_proxy，退出前恢复
    old_env = {k: os.environ.get(k) for k in ("http_proxy", "https_proxy")}
    os.environ["http_proxy"] = PROXY_ADDR
    os.environ["https_proxy"] = PROXY_ADDR
    try:
        for i in range(3):
            em_request(PUSH2HIS, params=FFLOW_PARAMS, headers=HEADERS_PROD,
                       proxies=None, trust_env=True, label=f"env-proxy-{i + 1}", step="T2c")
    finally:
        for k, v in old_env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


# ---------------------------------------------------------------------------
# T2d 间隔梯度：019B 恢复配方（15 秒间隔 × 3）
# ---------------------------------------------------------------------------
def t2d():
    _log("== T2d 间隔梯度：15 秒间隔小样本（3 次，push2his，直连）==")
    global MIN_INTERVAL_SEC, _last_em_ts
    old_interval = MIN_INTERVAL_SEC
    MIN_INTERVAL_SEC = 15.0
    try:
        for i in range(3):
            em_request(PUSH2HIS, params=FFLOW_PARAMS, headers=HEADERS_PROD,
                       proxies=_no_proxy(), trust_env=False, label=f"gap15s-{i + 1}", step="T2d")
    finally:
        MIN_INTERVAL_SEC = old_interval


# ---------------------------------------------------------------------------
# T3 本机网络环境只读排查（不产生任何东财请求）
# ---------------------------------------------------------------------------
def t3():
    _log("== T3 本机网络环境只读排查 ==")
    # 1. 系统代理注册表（只读）
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Internet Settings") as k:
            def _qv(name, default=None):
                try:
                    return winreg.QueryValueEx(k, name)[0]
                except OSError:
                    return default
            en = _qv("ProxyEnable")
            srv = _qv("ProxyServer")
            ovr = _qv("ProxyOverride")
            acu = _qv("AutoConfigURL")
        _log(json.dumps({"step": "T3", "note": "registry_proxy",
                         "ProxyEnable": en, "ProxyServer": srv, "ProxyOverride": ovr,
                         "AutoConfigURL": acu}, ensure_ascii=False))
    except Exception as e:
        _log(json.dumps({"step": "T3", "note": "registry_proxy_error", "err": str(e)}, ensure_ascii=False))
    # 2. 环境变量代理
    env_proxies = {k: v for k, v in os.environ.items() if "proxy" in k.lower()}
    _log(json.dumps({"step": "T3", "note": "env_proxy_vars", "value": env_proxies}, ensure_ascii=False))
    _log(json.dumps({"step": "T3", "note": "getproxies_current",
                     "value": urllib.request.getproxies()}, ensure_ascii=False))
    # 3. DNS 解析
    for host in ("push2his.eastmoney.com", "push2.eastmoney.com", "www.eastmoney.com", "hq.sinajs.cn"):
        try:
            _log(json.dumps({"step": "T3", "note": "dns", "host": host,
                             "ip": socket.gethostbyname(host)}, ensure_ascii=False))
        except Exception as e:
            _log(json.dumps({"step": "T3", "note": "dns_error", "host": host, "err": str(e)}, ensure_ascii=False))
    # 4. TCP 443 连通性
    for host in ("push2his.eastmoney.com", "push2.eastmoney.com"):
        s = socket.socket()
        s.settimeout(5)
        try:
            s.connect((host, 443))
            _log(json.dumps({"step": "T3", "note": "tcp443", "host": host, "ok": True}, ensure_ascii=False))
        except Exception as e:
            _log(json.dumps({"step": "T3", "note": "tcp443", "host": host, "ok": False, "err": str(e)}, ensure_ascii=False))
        finally:
            s.close()
    # 5. ping（只读，ICMP 可能被防火墙丢弃，仅作参考）
    for host in ("push2his.eastmoney.com",):
        try:
            r = subprocess.run(["ping", "-n", "1", "-w", "3000", host],
                               capture_output=True, text=True, timeout=10)
            _log(json.dumps({"step": "T3", "note": "ping", "host": host,
                             "rc": r.returncode, "out": r.stdout[-120:].strip()}, ensure_ascii=False))
        except Exception as e:
            _log(json.dumps({"step": "T3", "note": "ping_error", "host": host, "err": str(e)}, ensure_ascii=False))
    # 6. 代理端口监听状态
    s = socket.socket()
    s.settimeout(3)
    try:
        s.connect(("127.0.0.1", 7897))
        _log(json.dumps({"step": "T3", "note": "proxy_port_7897", "ok": True}, ensure_ascii=False))
    except Exception as e:
        _log(json.dumps({"step": "T3", "note": "proxy_port_7897", "ok": False, "err": str(e)}, ensure_ascii=False))
    finally:
        s.close()


# ---------------------------------------------------------------------------
# T4 akshare 层对照：同一会话内 新浪成功 + 东财失败
# ---------------------------------------------------------------------------
def t4():
    _log("== T4 akshare 层对照 ==")
    import akshare as ak
    # 对照生产环境：pop NO_PROXY 以读注册表代理（与 Flask 进程行为一致），退出前恢复
    saved_no_proxy = os.environ.pop("NO_PROXY", None)
    try:
        # 新浪系：指数日线
        _guard_interval()
        Budget.sina += 1
        t0 = time.time()
        try:
            df = ak.stock_zh_index_daily(symbol="sh000001")
            _log(json.dumps({"step": "T4", "label": "ak-stock_zh_index_daily(sina)", "ok": True,
                             "rows": len(df) if df is not None else 0,
                             "cols": list(df.columns)[:8] if df is not None else [],
                             "tail_date": str(df.iloc[-1, 0]) if df is not None and len(df) else None,
                             "elapsed_s": round(time.time() - t0, 2)}, ensure_ascii=False))
        except Exception as e:
            _log(json.dumps({"step": "T4", "label": "ak-stock_zh_index_daily(sina)", "ok": False,
                             "err_type": type(e).__name__, "err_msg": str(e)[:300],
                             "elapsed_s": round(time.time() - t0, 2)}, ensure_ascii=False))
        # 东财系：个股资金流（生产备用源，底层 push2his）
        _guard_interval()
        Budget.em += 1
        t0 = time.time()
        try:
            df = ak.stock_individual_fund_flow(stock="600519", market="sh")
            _log(json.dumps({"step": "T4", "label": "ak-stock_individual_fund_flow(em)", "ok": True,
                             "rows": len(df) if df is not None else 0,
                             "elapsed_s": round(time.time() - t0, 2)}, ensure_ascii=False))
        except Exception as e:
            _log(json.dumps({"step": "T4", "label": "ak-stock_individual_fund_flow(em)", "ok": False,
                             "err_type": type(e).__name__, "err_msg": str(e)[:300],
                             "elapsed_s": round(time.time() - t0, 2)}, ensure_ascii=False))
    finally:
        if saved_no_proxy is not None:
            os.environ["NO_PROXY"] = saved_no_proxy


# ---------------------------------------------------------------------------
# T2e 恢复可行性：30 秒间隔长窗口探测（模拟生产重试捕获开放窗口）
# ---------------------------------------------------------------------------
def t2e():
    _log("== T2e 恢复可行性：30 秒间隔 × 5 次（push2his，直连，生产头）==")
    global MIN_INTERVAL_SEC
    old_interval = MIN_INTERVAL_SEC
    MIN_INTERVAL_SEC = 30.0
    try:
        for i in range(5):
            em_request(PUSH2HIS, params=FFLOW_PARAMS, headers=HEADERS_PROD,
                       proxies=_no_proxy(), trust_env=False, label=f"hunt30s-{i + 1}", step="T2e")
    finally:
        MIN_INTERVAL_SEC = old_interval


def summary():
    _log(json.dumps({"summary": "019W 东财诊断请求计数",
                     "em": Budget.em, "sina": Budget.sina, "other": Budget.other,
                     "em_budget": EM_REQUEST_BUDGET}, ensure_ascii=False))


def main():
    ap = argparse.ArgumentParser(description="019W 东方财富反爬阻断诊断（只读）")
    ap.add_argument("cmd", choices=["t1", "t2a", "t2b", "t2c", "t2d", "t2e", "t3", "t4", "summary"])
    args = ap.parse_args()
    globals()[args.cmd]()
    summary()


if __name__ == "__main__":
    main()
