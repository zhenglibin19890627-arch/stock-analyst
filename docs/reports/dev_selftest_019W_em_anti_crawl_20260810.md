# 开发自测报告 019W：东方财富反爬阻断诊断

> 任务书：`docs/tasks/dev_tasks_20260810_019W_em_anti_crawl_diagnosis.md`
> 批次性质：**只诊断、不落地**（零生产改动、零数据库写入）
> 执行时间：2026-08-10 20:43–20:53（避开 16:00-17:00 与周日 20:00 周批窗口）
> 执行环境：Python 3.12.9（`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`），akshare 1.18.53（未升级）

---

## 一、红线执行情况

| 红线 | 状态 | 说明 |
|---|---|---|
| 零生产改动 | ✅ | 未修改 `modules/*.py`、`app.py`、`config*.json`、`*.bat`、`requirements.txt`、模板；仅新增 `scripts/diag_019w_em_anti_crawl.py` 与两份报告 |
| 零数据库写入 | ✅ | 诊断脚本不含任何 `stock_analyst.db` 连接代码；未点击前端个股详情、未手动触发采集 |
| 请求量封顶 | ✅ | 东财域名测试请求全批次 **54 次**（≤150），全部串行，相邻间隔 ≥2 秒（T2d=15s、T2e=30s） |
| 时间窗口 | ✅ | 全部测试在 20:43–20:53 完成，避开 16:00-17:00 采集批次与周日 20:00 周批 |
| 代理边界 | ✅ | 仅进程内 `proxies` 参数与临时 `os.environ`（退出前恢复）；未触碰系统代理注册表、未安装任何新依赖 |
| 脚本落位 | ✅ | 诊断脚本 `scripts/diag_019w_em_anti_crawl.py`，批次结束前登记保留 |

---

## 二、请求计数总表（脚本内计数器逐次累计）

| 测试项 | 东财请求数 | 成功 | 失败 | 成功率 |
|---|---|---|---|---|
| T1 基线复现（push2his×3 + push2×3，直连） | 6 | 2 | 4 | 33% |
| T2a 请求头矩阵（完整/极简/生产头 ×3，直连） | 9 | 0 | 9 | 0% |
| T2a-extra curl.exe 原生 TLS 栈（无自定义头 ×2） | 2 | 2 | 0 | 100% |
| T2a 第二轮（完整/极简/生产头 ×3，直连） | 9 | 6 | 3 | 67% |
| T2a-extra 第二轮（curl 原生 ×2 + curl 带 requests 头 ×2） | 4 | 0 | 4 | 0% |
| T2b 端点对照（quote×2、kline×2、www×1，直连） | 5 | 5 | 0 | 100% |
| T2c 直连 ×3 | 3 | 0 | 3 | 0% |
| T2c 显式代理 127.0.0.1:7897 ×3 | 3 | 0 | 3 | 0% |
| T2c env 代理（http_proxy/https_proxy） ×3 | 3 | 0 | 3 | 0% |
| T2d 15 秒间隔 ×3（直连） | 3 | 1 | 2 | 33% |
| T2e 30 秒间隔 ×5（直连，模拟生产重试） | 5 | 2 | 3 | 40% |
| T4 akshare 东财源 ×1 | 1 | 0 | 1 | 0% |
| 补充：代理出口→东财 quote ×1 | 1 | 0 | 1 | 0% |
| **合计** | **54** | **18** | **36** | **33.3%** |

> 说明：直连口径（T1/T2a/T2b/T2c-direct/T2d/T2e 合计 35 次）成功率 14/35=40%；代理口径（T2c 6 次 + 补充 1 次 + T4 1 次）成功率 0/8。成功率随「窗口期」在 0%~100% 间大幅波动（详见下方逐次记录）。

---

## 三、T1 基线复现：原始测试记录（20:43:25–20:43:35，直连）

**端点**：`https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get`（生产参数，secid=1.600519）与 `https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get`

| # | 时间 | 端点 | 结果 | HTTP | 错误类型 / 摘要 |
|---|---|---|---|---|---|
| 1 | 20:43:25 | push2his | ✅ 200 | 200 | 真实数据：`{"rc":0,...,"name":"贵州茅台","klines":["2026-02-06,-544408832.0,...`（elapsed 0.60s） |
| 2 | 20:43:27 | push2his | ✅ 200 | 200 | 同上（elapsed 0.61s，svr=183640596） |
| 3 | 20:43:29 | push2his | ❌ | – | `ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))`（0.53s） |
| 4 | 20:43:31 | push2 | ❌ | – | 同上 RemoteDisconnected（0.50s） |
| 5 | 20:43:33 | push2 | ❌ | – | 同上 RemoteDisconnected（0.41s） |
| 6 | 20:43:35 | push2 | ❌ | – | 同上 RemoteDisconnected（0.39s） |

**代表性异常栈原文（#3，`http.client.RemoteDisconnected` 顶层）**：

```
File "...urllib3\connectionpool.py", line 787, in urlopen
    response = self._make_request(...)
File "...urllib3\connectionpool.py", line 534, in _make_request
    response = conn.getresponse()
File "...urllib3\connection.py", line 571, in getresponse
    httplib_response = super().getresponse()
File "...http\client.py", line 1430, in getresponse
    response.begin()
File "...http\client.py", line 331, in begin
    version, status, reason = self._read_status()
File "...http\client.py", line 300, in _read_status
    raise RemoteDisconnected("Remote end closed connection without")
http.client.RemoteDisconnected: Remote end closed connection without response
→ requests.exceptions.ConnectionError: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
```

**代表性成功响应原文（#1 前 200 字符）**：

```json
{"rc":0,"rt":22,"svr":183640596,"lt":1,"full":0,"dlmkts":"","dsc":"0","data":{"code":"600519","market":1,"name":"贵州茅台","klines":["2026-02-06,-544408832.0,-291068.0,544699904.0,-488497152.0,-55911680.0"
```

**结论**：失败签名与 08-09/08-10 两日生产日志完全一致（100% RemoteDisconnected，无解析类错误）；但 push2his 并非全断——同 12 秒内成功 2 次后进入丢弃窗口。**拦截为「窗口式连接丢弃」，非永久封禁。**

---

## 四、T2a 请求头策略矩阵：原始测试记录

### 第一轮（20:43:46–20:44:06，push2his，直连）

| # | 时间 | 头组 | 结果 | 错误类型 |
|---|---|---|---|---|
| 1 | 20:43:46 | 完整浏览器头 | ❌ | RemoteDisconnected |
| 2 | 20:43:48 | 完整浏览器头 | ❌ | RemoteDisconnected |
| 3 | 20:43:50 | 完整浏览器头 | ❌ | RemoteDisconnected |
| 4 | 20:43:52 | 极简头（仅 UA） | ❌ | RemoteDisconnected |
| 5 | 20:43:54 | 极简头（仅 UA） | ❌ | RemoteDisconnected |
| 6 | 20:43:56 | 极简头（仅 UA） | ❌ | RemoteDisconnected |
| 7 | 20:43:58 | 生产头（UA+Accept+Accept-Language） | ❌ | RemoteDisconnected |
| 8 | 20:44:00 | 生产头 | ❌ | RemoteDisconnected |
| 9 | 20:44:02 | 生产头 | ❌ | RemoteDisconnected |
| 10 | 20:44:04 | **curl.exe 原生（无自定义头）** | ✅ 200 | –（elapsed 0.36s） |
| 11 | 20:44:06 | **curl.exe 原生（无自定义头）** | ✅ 200 | –（elapsed 0.26s） |

### 第二轮（20:44:46–20:45:10，push2his，直连）

| # | 时间 | 头组 | 结果 | HTTP | 备注 |
|---|---|---|---|---|---|
| 1 | 20:44:46 | 完整浏览器头 | ✅ 200 | 200 | 响应为 gzip 二进制（手动置 Accept-Encoding: gzip 后 requests 不自动解压），HTTP 200 即放行 |
| 2 | 20:44:48 | 完整浏览器头 | ✅ 200 | 200 | 同上 |
| 3 | 20:44:50 | 完整浏览器头 | ✅ 200 | 200 | 同上（**requests 连续 3 连成功**） |
| 4 | 20:44:52 | 极简头 | ✅ 200 | 200 | `{"rc":0,...,"name":"贵州茅台","klines":[...}` |
| 5 | 20:44:54 | 极简头 | ❌ | – | RemoteDisconnected |
| 6 | 20:44:56 | 极简头 | ❌ | – | RemoteDisconnected |
| 7 | 20:44:58 | 生产头 | ✅ 200 | 200 | 真实数据（elapsed 2.5s） |
| 8 | 20:45:00 | 生产头 | ❌ | – | RemoteDisconnected |
| 9 | 20:45:02 | 生产头 | ✅ 200 | 200 | 真实数据（svr=181669906） |
| 10 | 20:45:04 | curl.exe 原生 | ❌ | 000 | `curl: (56) schannel: server closed abruptly (missing close_notify)` |
| 11 | 20:45:06 | curl.exe 原生 | ❌ | 000 | 同上 |
| 12 | 20:45:08 | curl + requests 同款头（UA/Accept/Accept-Language） | ❌ | 000 | 同上 |
| 13 | 20:45:10 | curl + requests 同款头 | ❌ | 000 | 同上 |

**结论**：① 请求头组合对拦截无影响（三组头在同一窗口内命运一致）；② 两轮之间出现「requests 全败窗口内 curl 两连成」与「curl 全败窗口内 requests 部分成功」的**互相倒置**——WAF 对不同客户端指纹（OpenSSL vs Schannel TLS 指纹）差异化丢弃，且窗口随分钟级时间推移在开放/收紧间切换。

---

## 五、T2b 端点对照：原始测试记录（20:45:30–20:45:38，直连）

| # | 时间 | 端点 | 结果 | HTTP | 响应摘要 |
|---|---|---|---|---|---|
| 1 | 20:45:30 | push2 `api/qt/stock/get`（行情） | ✅ | 200 | `{"rc":0,...,"data":{"f43":134886,"f57":"600519","f58":"贵州茅台"}}` |
| 2 | 20:45:32 | push2 `api/qt/stock/get`（行情） | ✅ | 200 | 同上（svr=177617600） |
| 3 | 20:45:34 | push2his `api/qt/stock/kline/get`（K线） | ✅ | 200 | `{"rc":102,...,"data":null}`（HTTP 层放行） |
| 4 | 20:45:36 | push2his `api/qt/stock/kline/get`（K线） | ✅ | 200 | 同上（svr=181735240） |
| 5 | 20:45:38 | `www.eastmoney.com/` 主站 | ✅ | 200 | HTML 首页（`published at 2026/8/10 20:45:01 by www.eastmoney.com PJ 73`） |

**结论**：同一时刻非 fflow 端点 5/5 全通（含真实行情数据），说明拦截**非域名级全断**，而是对 fflow 接口（量化采集高频目标）所在的 WAF 规则更严；阻断窗口期集中在 fflow 请求时表现最明显。

---

## 六、T2c 直连/代理/环境变量三态：原始测试记录（20:46:02–20:46:18，push2his fflow）

前置核查输出：
- `getproxies_with_NO_PROXY`（本 shell 会话存在 NO_PROXY 环境变量）→ `{"no": "127.0.0.1,localhost,::1"}`（**掩盖注册表代理**）
- `getproxies_after_pop_NO_PROXY` → `{"http": "http://127.0.0.1:7897", "https": "http://127.0.0.1:7897", "ftp": "http://127.0.0.1:7897"}`（读取注册表系统代理，与 Flask 进程行为一致）

| # | 时间 | 模式 | 结果 | 错误类型 |
|---|---|---|---|---|
| 1 | 20:46:02 | 直连（proxies=None+trust_env=False） | ❌ | ConnectionError / RemoteDisconnected |
| 2 | 20:46:04 | 直连 | ❌ | ConnectionError / RemoteDisconnected |
| 3 | 20:46:06 | 直连 | ❌ | ConnectionError / RemoteDisconnected |
| 4 | 20:46:08 | 显式代理 127.0.0.1:7897 | ❌ | **ProxyError**：`HTTPSConnectionPool(host='push2his.eastmoney.com', port=443): Max retries exceeded ... (Caused by ProxyError('Unable to connect to proxy', RemoteDisconnected('Remote end closed connection without response')))` |
| 5 | 20:46:10 | 显式代理 | ❌ | ProxyError 同上 |
| 6 | 20:46:12 | 显式代理 | ❌ | ProxyError 同上 |
| 7 | 20:46:14 | env 代理（http_proxy/https_proxy 临时设置） | ❌ | ProxyError 同上 |
| 8 | 20:46:16 | env 代理 | ❌ | ProxyError 同上 |
| 9 | 20:46:18 | env 代理 | ❌ | ProxyError 同上 |

> env 代理临时设置已在脚本 finally 块恢复原值。

**代理对照补充（非预算内探针，不干扰结论）**：
- 代理→百度：`ok=true, status=200`（**代理本身活着**，对一般站点可用）
- 代理→新浪 hq.sinajs.cn：`ok=false, status=403`（新浪自身反爬返回 403，与东财无关）
- 代理→东财 quote：`ProxyError`（代理对东财连接同样被丢）

**结论**：① 直连失败与代理失败并存；② 代理（Clash verge-mihomo）对东财表现为「CONNECT 后连接被关闭」（`Unable to connect to proxy, RemoteDisconnected`），说明**代理出口路径同样被东财 WAF 丢弃**（Clash 对国内域名通常走 DIRECT 规则，出口 IP 与本机直连相同）；③ 代理路径无恢复价值。

---

## 七、T2d 间隔梯度（15 秒）：原始测试记录（20:46:57–20:47:27，push2his，直连）

| # | 时间 | 结果 | 错误类型 |
|---|---|---|---|
| 1 | 20:46:57 | ❌ | RemoteDisconnected |
| 2 | 20:47:12 | ✅ 200 | 真实数据 `{"rc":0,...,"name":"贵州茅台",...}`（svr=177617933） |
| 3 | 20:47:27 | ❌ | RemoteDisconnected |

**结论**：15 秒间隔对单次成功概率无明显提升（1/3），窗口持续时间超过 15 秒。

---

## 八、T2e 恢复可行性（30 秒间隔 × 5，模拟生产重试捕获窗口）：原始测试记录（20:51:07–20:53:07）

| # | 时间 | 结果 | HTTP | 错误类型 |
|---|---|---|---|---|
| 1 | 20:51:07 | ✅ 200 | 200 | 真实数据（svr=177617937） |
| 2 | 20:51:37 | ❌ | – | RemoteDisconnected |
| 3 | 20:52:07 | ❌ | – | RemoteDisconnected |
| 4 | 20:52:37 | ❌ | – | RemoteDisconnected |
| 5 | 20:53:07 | ✅ 200 | 200 | 真实数据（svr=177617939） |

**结论**：**窗口期持续约 2–4 分钟，周期性重试（30 秒间隔 × 5）可在 2 分钟内捕获开放窗口**（2/5 成功）——「等待+重试捕获窗口」策略当前可行，与 019B 的 15 秒周期重试恢复结论一致。

---

## 九、T3 本机网络环境只读排查：原始记录（无东财请求产生）

| 项目 | 结果 |
|---|---|
| 系统代理注册表（HKCU Internet Settings） | `ProxyEnable=1`，`ProxyServer=127.0.0.1:7897`，`ProxyOverride=localhost;127.*;192.168.*;10.*;172.16-31.*;<local>`，`AutoConfigURL=null` |
| 环境变量代理 | 仅 `NO_PROXY=127.0.0.1,localhost,::1`（用户/机器级环境变量均无 http_proxy/https_proxy；NO_PROXY 来自当前会话） |
| getproxies()（当前会话） | `{'no': '127.0.0.1,localhost,::1'}`；剔除 NO_PROXY 后读注册表返回 `{'http':'http://127.0.0.1:7897',...}` |
| DNS | push2his→117.184.38.143；push2→101.226.30.206；www→113.240.66.218；hq.sinajs.cn→120.83.145.204（解析正常，且 push2his 解析结果随 DNS 轮询变化：20:38 时为 140.207.67.156） |
| TCP 443 | push2his ✅、push2 ✅（连接建立成功，RemoteDisconnected 发生在 TLS 之后） |
| ping | push2his：0% 丢包，平均 21–22ms |
| 代理端口 7897 | 监听中（verge-mihomo 进程），TCP 连通 ✅ |
| 公网出口 IP | `183.134.206.110`，中国浙江杭州电信（家宽动态 IP） |

**结论**：本机网络环境无异常——DNS/TCP/ping/代理监听全部正常，公网为普通电信家宽，**排除本机网络环境为根因**。

---

## 十、T4 akshare 层对照：原始记录（20:48 前后，同一进程）

| 接口 | 结果 | 输出 |
|---|---|---|
| `ak.stock_zh_index_daily(symbol="sh000001")`（新浪） | ✅ 成功 | 8700 行，列 `[date, open, high, low, close, volume]`，**末行日期 2026-08-10（当日实时数据）**，elapsed 2.82s |
| `ak.stock_individual_fund_flow(stock="600519", market="sh")`（东财） | ❌ 失败 | `ProxyError: HTTPSConnectionPool(host='push2his.eastmoney.com', port=443): Max retries exceeded ... RemoteDisconnected`，elapsed 0.36s |

**结论**：同一 akshare 进程、同一会话内新浪接口正常、东财接口失败——**库本身与请求链路无关，问题特定于东财侧**（与生产两日「新浪正常、东财 78 次失败 100% RemoteDisconnected」的事实基线吻合）。

---

## 十一、生产日志原始证据（取证，只读引用）

1. **两日 78 次 akshare 失败全部为 RemoteDisconnected**（`Select-String` 计数：78/78）：
   ```
   2026-08-09 11:27:51,923 [modules.data_collector] WARNING [600276] akshare备用源失败: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
   2026-08-09 11:28:58,793 [modules.data_collector] WARNING [300146] akshare备用源失败: ('Connection aborted.', RemoteDisconnected('Remote end closed connection without response'))
   ```
2. **东财三层全失败 → 新浪顶替成功（is_estimated=0 真实数据，08-10 共 23 行入库）**：
   ```
   2026-08-10 16:44:27,972 [modules.data_collector] WARNING [600276] 东方财富三层全失败（push2his/push2/akshare），尝试新浪顶替 → 估算兜底...
   2026-08-10 16:44:29,056 [modules.data_collector] INFO [600276] 新浪 lscjfb 主力口径顶替成功: main=-52038.91 万（is_estimated=0，capital_source=sina_main，仅写当日 1 行）
   ```
3. **HSI 指数东财失败→新浪降级成功（08-10 17:49）**：
   ```
   2026-08-10 17:49:36,091 [modules.index_collector] WARNING [指数K线] 恒生指数(HSI) EM 接口不可用(('Connection aborted.', RemoteDisconnected('Remote end closed connection without response')))，降级新浪源
   ```
4. **失败分布按小时（全部分布在采集批窗口内，口径 A：含「akshare备用源失败」的行，文件 `logs\app.log.2026-08-09` + `logs\app.log`）**：08-09 11 时 23 次；08-10 16 时 19 次、17 时 36 次（合计 78 次）。
5. **东财最近一次生产成功**：08-06 14:00:12 `东方财富proxy成功（第1轮）`；此前 08-05 21:53:43 `direct成功`、08-04 14:25:57 `direct成功`。08-09/08-10 无任何成功记录。

---

## 十二、自测结论

1. **根因定位（证据链）**：东财侧 WAF 对本机出口 IP（183.134.206.110 杭州电信）实施**窗口式连接级丢弃**（RemoteDisconnected 发生在 TLS 之后、HTTP 响应之前，TCP/DNS 全程正常）；窗口随分钟级时间推移开放/收紧，**采集批高密度请求期间（16:10 批）窗口长时间关闭**导致两日 100% 失败；客户端 TLS 指纹（python-requests/OpenSSL vs curl/Schannel）在部分窗口被差异化对待，但非决定性因素（curl 同样存在全败窗口）。
2. **已验证可行的恢复策略**：周期重试捕获开放窗口（T2e：30 秒间隔 2/5 成功，2 分钟内捕获）——与 019B 既有结论一致，生产 `_http_get_em`（MAX_RETRIES=3 轮 ×2 路径）配合更长退避可显著提高窗口命中率。
3. **无效策略（本批次实测）**：请求头组合调整（0 效果）、显式/环境变量代理（0/8，代理出口同被丢弃）、15 秒间隔（无提升）。
4. **排除项**：本机网络环境、akshare 库、DNS/TCP/代理监听、新浪系接口（全部正常）。

> 自测记录全部来自脚本逐次 JSON 输出（`scripts/diag_019w_em_anti_crawl.py` 各子命令运行结果），未做任何挑选性删改。

---

## 十三、修订记录（019W-S1 返工修订）

> 返工单：`docs/tasks/dev_tasks_20260810_019W-S1_report_numbers_fix.md`。本修订为**纯文档修订**：零生产改动、零数据库写入、零新增东财请求。

| # | 修订位置 | 改前数字 | 改后数字（口径 A） |
|---|---|---|---|
| 1 | `diag_019W_em_anti_crawl_20260810.md` 第一节「四类根因判定」表「频率级」行 | 08-09 11 时 403 次、08-10 16-17 时 935 次 | 08-09 11 时 23 次、08-10 16 时 19 次、17 时 36 次（合计 78 次） |
| 2 | `diag_019W_em_anti_crawl_20260810.md` 第二节 2.1 第 4 条 | 08-09 11 时 403 次；08-10 16 时 293 次、17 时 642 次 | 08-09 11 时 23 次；08-10 16 时 19 次、17 时 36 次（合计 78 次） |
| 3 | 本报告第十一节第 4 条 | 08-09 11 时 403 次；08-10 16 时 293 次、17 时 642 次 | 08-09 11 时 23 次；08-10 16 时 19 次、17 时 36 次（合计 78 次） |

**口径 A 复核命令**（PowerShell，项目根目录 `stock_analyst/` 执行，UTF-8 逐行匹配）：

```powershell
$all = @(); foreach ($f in @('logs\app.log.2026-08-09','logs\app.log')) { $all += Get-Content -LiteralPath $f -Encoding UTF8 | Where-Object { $_ -match 'akshare备用源失败' } }; "total: $($all.Count)"; $all | ForEach-Object { $_.Substring(11,2) } | Group-Object | Sort-Object Name | ForEach-Object { "hour $($_.Name): $($_.Count)" }
```

**复核输出原文**：

```
total: 78
hour 11: 23
hour 16: 19
hour 17: 36
```

> 备查口径 B（同两文件全部含 `RemoteDisconnected` 的行，含重试与直连层）：258 / 190 / 412，合计 860——不采用。
