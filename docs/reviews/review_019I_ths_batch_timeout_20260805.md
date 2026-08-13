# 架构评审 019I — THS 批量预取超时保护（报告生成卡住修复）【架构师定稿】

**评审日期**：2026-08-05
**评审人**：架构师（独立复核，本版为定稿）
**任务书**：`docs/tasks/dev_tasks_20260805_019I_ths_batch_timeout.md`（PM 签发 v1）
**评审方式**：独立 Read 代码核验 + 本机 Python 3.12.9 运行时实验（不采信 PM 结论）
**评审结论**：⚠️ **有条件通过**（M-1 强制修正 + M-2 建议 + M-3/M-4 文档与验收修订 + R-1~R-5 新发现）

---

## 〇、评审摘要（决策点裁定总览）

| 决策点 | 内容 | 架构师裁定 | 结论 |
|---|---|---|---|
| A-1 | 超时方案选型（ThreadPoolExecutor 包装） | ⚠️ **修改（部分否决）**：方向正确，但 `with` 块写法**不解决挂死**（实验实证） | 强制修正 M-1 |
| A-2 | 超时阈值 60 秒 | ✅ 采纳 60s；185s 最坏上界可接受（有界优于无限），优化建议见 M-2 | 通过 |
| A-3 | 常量放置位置 | ✅ 采纳（data_collector.py 模块级，与 `_THS_*`/`_EM_*` 常量族一致） | 通过 |
| A-4 | `_call_with_timeout` 函数设计 | ⚠️ 采纳设计，但 with 块必须替换（并入 M-1）；import 位置表述更正（M-3） | 有条件通过 |
| A-5 | 线程泄漏风险评估 | ⚠️ **需额外处理**：3.12.9 TPE 线程**非 daemon**，僵尸线程会**阻塞进程退出**（重启端口占用风险） | 强制修正 M-1（方案） |
| A-6 | 降级链路完整性 | ✅ 确认完整（在 A-1 修复前提下）；PM 原样代码下"超时返回 None"实际不成立 | 通过（附条件） |
| A-7 | 范围与红线确认 | ✅ 完备，补充 2 条红线（R-1/R-5 相关） | 通过（含补充） |
| A-8 | 其他裸 akshare 调用 | ✅ 仅处理 THS；EM 逐只残留挂死窗口登记 R-2（019J 候选） | 通过（含登记） |

**核心结论（本评审最重要发现）**：
1. **PM 任务书方案按原文实现无法修复缺陷**。`with _Pool(max_workers=1) as pool:` 退出时调用 `shutdown(wait=True)`，会 **join 挂死的 worker 线程**。本机实验实证：1 秒超时后 with 块在 6 秒（=挂死时长）才退出——若线程永不结束，**函数仍会永久阻塞**（仅多打印一条超时日志）。必须改为 `finally: pool.shutdown(wait=False)`（实验实证 1 秒内返回），或采用 daemon 线程 join 模式（推荐，见 M-1）。
2. **Python 3.12.9 的 ThreadPoolExecutor 工作线程不是 daemon 线程**（源码核验 + 运行时实证 `daemon=False`）。超时后遗留的僵尸线程将**阻塞解释器退出**：用户在 THS 挂死窗口内重启应用时，旧进程无法退出 → 5000 端口被占用 → 新实例启动失败。这直接回答了 PM 在 A-5 的核心关切。

---

## 〇-1、独立核验签署记录

| 项 | PM 结论 | 架构师独立核验证据 | 裁定 |
|---|---|---|---|
| 根因 | THS 接口无超时保护无限阻塞 | `data_collector.py` L1175 `ak.stock_fund_flow_individual()` 裸调用（try/except 仅捕获异常，挂死不抛异常）；akshare 1.18.53 源码 `stock_fund_flow_individual(symbol="即时")` **无 timeout 参数**，内部 `requests.get(url, headers=headers)` **无 timeout**（默认 None 无限等待）；app.log L186-188 实证 11:46:28 请求后 18+ 分钟空白 | ✅ 确认 |
| 无其他超时覆盖 | 批量预取前无任何超时 | `daily_report.py` L505 `BATCH_TIMEOUT_SECONDS` 检查点在**循环内部**，L479 批量预取在循环**之前**，不受覆盖；`config.py` L115-117 仅覆盖单只/批次循环 | ✅ 确认 |
| 60s 依据 | 正常 7 秒留 8 倍余量 | app.log.2026-08-03 实证 7 秒返回 5197 只 | ✅ 确认 |
| with 块可解决超时 | 任务书 L121-127 原样代码 | **❌ 实验证伪**（见 A-1） | 修订 M-1 |

---

## 一、独立核验记录（关键证据）

### 1.1 缺陷根因核验（`modules/data_collector.py`）

- **L1120-1168** `_fetch_capital_flow_ths_batch()`：确认当前**无任何超时保护**。调用链 L1145 `df = _try_ths_primary()` → L1175 `df = ak.stock_fund_flow_individual()`，函数体 try/except Exception 仅捕获异常返回 None——**服务器挂住（TCP 连接保持但不返回数据）时不抛异常，无限阻塞**，与 PM 根因分析一致。
- **L1148-1151** 主接口失败重试 1 次（5 秒等待）；**L1154-1156** 备选接口；**L1158-1163** 成功才写缓存并重置失败计数；**L1166** 全失败时 `_THS_CONSECUTIVE_FAIL_COUNT += 1`（每次生成只 +1，需 3 次生成才达阈值 3 → 前 3 次生成每次都全量走完 THS 链路）。
- **akshare 1.18.53 源码核验**（`akshare/stock_feature/stock_fund_flow.py` L41 起）：`stock_fund_flow_individual(symbol: str = "即时")` 签名**无 timeout 参数**；L86 起内部 `r = requests.get(url, headers=headers)` **未传 timeout**（requests 默认 `timeout=None` = 无限等待）。**结论：无法通过 akshare 参数层传递超时**，requests 层 timeout 注入不可行（除非 monkeypatch，侵入性过强，否决）。
- 备选接口 `ak.stock_individual_fund_flow_rank` 实际来自 `akshare.stock.stock_fund_em`（**东方财富**接口，非 THS 服务器）——与主接口**不同服务器**，超时语义应区分（见 R-5）。
- **`signal.alarm` 不可用**（Windows 无 SIGALRM）；**`socket.setdefaulttimeout()` 否决**：进程级全局副作用，Flask `threaded=True`（app.py L3967）下会污染并发请求的 EM 采集等其他 HTTP 路径。**线程包装是 Windows + 零新依赖约束下唯一务实方案** —— PM 选型方向正确。

### 1.2 运行时实验（本机 Python 3.12.9，实证 A-1/A-5 裁定）

**实验 1（PM 原案：with 块）**：
```
timeout raised at 1.0 s
with-block exited at 6.0 s -> BLOCKS waiting for hung thread   （挂 6 秒的线程，with 块等满 6 秒才退出）
```
→ `future.result(timeout=N)` 按时抛 `TimeoutError`，但 **with 块退出时 `__exit__` 调用 `shutdown(wait=True)`，join 挂死线程直至其自然结束**。若线程永不结束（本次事故场景），**函数依旧永久阻塞**。任务书 L121-127 原样代码**不能修复缺陷**。

**实验 2（修正案：显式 shutdown(wait=False)）**：
```
timeout raised at 1.0 s
shutdown(wait=False) returned at 1.0 s -> non-blocking
```
→ `shutdown(wait=False)` 不 join 线程，1 秒内返回。**修复有效**。

**实验 3（线程 daemon 属性）**：`ThreadPoolExecutor-0_0 daemon= False` —— 3.12.9 工作线程**非 daemon**。源码核验（`Lib\concurrent\futures\thread.py` L194-199）：`threading.Thread(name=..., target=_worker, args=...)` **未传 daemon=True**；L239 `shutdown(wait=True)` 为 `for t in self._threads: t.join()`；模块级 L25-31 `_python_exit()` 在解释器退出时 **join 全部 TPE 工作线程**。**推论：挂死线程 → 进程退出被阻塞 → 重启时 5000 端口占用 → 新实例启动失败**（见 A-5/R-1）。

### 1.3 降级链路核验（A-6）

- L1158-1163：缓存写入与失败计数重置**仅在 `df is not None`** 时执行；L1166 失败计数仅在全部失败路径 +1；L1167 日志后 L1168 `return None`。
- 调用方 `fetch_capital_flow_batch()` L1373-1376：`df is None` → 日志 → `_em_batch_collect(a_stock_symbols)`（019C 六机制 EM 回退），结构上超时返回 None 与现有失败路径完全一致。**确认完整**。
- 前提：仅当 A-1 修正落地后"超时 → 真正返回 None"才成立；PM 原样代码下该路径被 with 块阻塞，A-6 的"确认完整"不成立。

### 1.4 调用方与先例核验（A-7/A-8）

- `daily_report.py` L475-482：批量预取包在 try/except 中（"不阻断"）；L489 进度文件在预取**之后**才写入 → THS 挂死期间前端无任何进度更新。
- `daily_report.py` L531-549：单只超时先例 `with ThreadPoolExecutor(max_workers=1) as executor:` + `future.result(timeout=STOCK_TIMEOUT_SECONDS)` —— **同一 with 块缺陷的既有先例**（单只处理挂死时同样会在 with 退出处阻塞）。019I 修复不得复制该模式（R-3）。
- 任务书 L149 称"`from concurrent.futures` 导入放在函数内部（与 daily_report.py L533 的使用模式一致）"——**事实不精确**：daily_report.py L27-28 的 import 在**模块顶部**，仅**使用**在函数内（M-3）。
- 第二调用方 `app.py` L1298（批量分析 batch-analyze 预取）：与日报共用 `fetch_capital_flow_batch` → **本修复自动覆盖两个入口**，无需改 app.py。
- 全仓 `ak.` 调用点 19 处（data_collector.py 16 + index_collector 2 + news_collector 1），见 A-8 附表。

---

## 二、逐决策点裁定

### A-1：超时保护方案选型 —— ⚠️ 修改（方向采纳，实现细节部分否决）

**裁定**：采纳"线程池/线程 + `future.result(timeout=N)`"的**技术方向**（Windows 下唯一务实选项，见 1.1）；**否决任务书 L121-127 的 `with` 块写法**（实验 1 实证：with 退出时 `shutdown(wait=True)` join 挂死线程，**修复无效**）。

**替代方案评估**：
| 方案 | 评估 | 结论 |
|---|---|---|
| requests 层 timeout 透传 | akshare 签名无 timeout 参数、内部裸 `requests.get`（1.18.53 源码核验） | 不可行 |
| `signal.alarm` | Windows 无 SIGALRM | 不可行 |
| `socket.setdefaulttimeout()` | 进程级全局，污染 Flask 并发请求的其他 HTTP 路径 | 否决 |
| monkeypatch akshare | 侵入性强、零代码约束内难以维护 | 否决 |
| 子进程隔离 + kill | 可彻底杀挂死调用，但引入进程管理复杂度、跨进程 DataFrame 传递成本 | 否决（过度设计） |
| **线程包装 + 显式 `shutdown(wait=False)`** | 标准库、零依赖、非阻塞返回（实验 2 实证） | ✅ **采纳（修正版）** |
| **daemon 线程 join 模式**（推荐，见下） | 标准库、零依赖、且**消除进程退出阻塞**（实验 3/R-1） | ✅ **推荐采纳** |

**推荐实现（M-1，二选一，推荐前者）**：

方案甲（daemon 线程 join，推荐——完整闭合 A-5）：
```python
def _call_with_timeout(fn, label):
    """019I：daemon 线程包装 THS 接口调用，超时返回 None"""
    box = {}
    t = threading.Thread(target=lambda: box.update(r=fn()), daemon=True)
    t.start()
    t.join(timeout=_THS_REQUEST_TIMEOUT)
    if t.is_alive():
        logger.warning(f'[同花顺批量] {label} 超时({_THS_REQUEST_TIMEOUT}s)，跳过')
        return None
    return box.get('r')
```
- daemon 线程不被 `threading._shutdown` join，**进程退出不被阻塞**（挂死线程随进程退出被 OS 回收）
- `fn` 内部异常：线程静默死亡 → `box` 为空 → 返回 None，降级语义与现有失败路径一致（`_try_ths_primary`/`_try_ths_rank_backup` 自身已捕获 Exception）
- `t.join(timeout=N)` 返回后 `is_alive()` 判断无竞态；`box` 写入发生在 join 返回前（happens-before 保证）

方案乙（TPE 修正版，最小改动）：
```python
def _call_with_timeout(fn, label):
    """019I：线程池包装 THS 接口调用，超时返回 None（不得使用 with 块）"""
    pool = _Pool(max_workers=1)
    try:
        future = pool.submit(fn)
        return future.result(timeout=_THS_REQUEST_TIMEOUT)
    except _FutTimeout:
        logger.warning(f'[同花顺批量] {label} 超时({_THS_REQUEST_TIMEOUT}s)，跳过')
        return None
    finally:
        pool.shutdown(wait=False)
```
- **红线**：不得使用 `with _Pool(...)`（`shutdown(wait=True)` join 挂死线程）；必须显式 `shutdown(wait=False)`
- 残余风险：TPE 工作线程非 daemon（实验 3），挂死线程会阻塞进程退出（R-1），需 QA 验收进程退出场景（M-4）

### A-2：超时阈值 60 秒 —— ✅ 采纳

- **60s 合理**：正常路径 7s（8/3 日志实证），60s = 8.5 倍余量；本次事故挂死 18+ 分钟 → 60s 判定为 hang 不误杀。
- **185s 最坏上界可接受**（60 主 + 5 等待 + 60 重试 + 60 备选）：有界（≈3 分钟）远优于无限；且 019I 落地后 EM 回退链路有 600s 软超时 → 全程有界。验收红线"不得永久阻塞"满足。
- **体验注记**：进度文件在预取后才写入（daily_report.py L489），THS 阶段前端无进度 → 60s+ 静默等待是既定体验，非本批次范围。
- **优化建议（M-2，非阻塞）**：区分"超时"与"异常"——超时（服务器无响应）后**跳过 5 秒重试**（同服务器 5 秒后重试大概率再超时），保留备选接口（不同服务器，EM rank，可能可用）；THS 阶段上界 185s → 120s。因需要 `_call_with_timeout` 区分返回语义（如返回 `(df, timed_out)` 或抛 `_FutTimeout`），复杂度略增，裁定为**建议项不强制**。

### A-3：超时常量放置位置 —— ✅ 采纳（data_collector.py 模块级）

- 与既有 `_THS_CAPITAL_CACHE_TTL`/`_THS_FAIL_THRESHOLD`（L1096-1098）及 019C `_EM_*` 常量族（L1105-1113）**同层级同风格**；`_EM_FALLBACK_TOTAL_CAP_SECONDS=600` 同为模块级先例。
- `config.py` 仅承载用户可见的报表级配置（`STOCK_TIMEOUT_SECONDS`/`BATCH_TIMEOUT_SECONDS`），THS 超时属内部实现细节，符合"零代码用户不调整"原则。
- **命名规范**：`_THS_REQUEST_TIMEOUT = 60` 置于 L1098 后（THS 常量块内），前缀与命名规则一致。✅ 无修改。

### A-4：`_call_with_timeout` 函数设计 —— ⚠️ 采纳（并入 M-1 修正）

- **闭包 vs 模块级**：仅 3 处调用点均在本函数内，闭包可接受（作用域收敛、不污染模块命名空间）；模块级亦可（便于 QA 单测直接引用）。**裁定：按 PM 方案函数内定义，不强制**。
- **with 块**：❌ 必须替换（A-1，M-1）。
- **import 位置**：任务书 L149 声称"与 daily_report.py L533 模式一致"系**事实错误**（daily_report.py L27-28 在模块顶部 import，L533 仅是使用）。裁定：函数内 import 无害（stdlib import 有缓存），可保留；任务书表述更正（M-3）。若采纳方案甲，则改为模块顶部 `import threading`。
- **异常语义**：`_call_with_timeout` 仅需捕获 `_FutTimeout`；worker 内其他异常由 `_try_ths_*` 自兜底返回 None，`future.result` 透传的 BaseException 子类（KeyboardInterrupt 等）应继续向上传播（正确）。无需放宽为 catch-all。

### A-5：线程泄漏风险评估 —— ⚠️ 需额外处理（PM 关切成立，且比预期更严重）

**独立核验新增事实（比 PM 预估严重）**：3.12.9 TPE 工作线程**非 daemon**（实验 3 + stdlib 源码 L194-199）。影响链：

| 影响 | 评估 |
|---|---|
| 运行期僵尸线程累积 | 每次报告生成最多 3 个（主/重试/备选各 1 池），每线程约 1MB 提交内存 + 1 条 TCP 连接；THS 挂死时生成频率低（用户连续点击触发有限），**内存层面可接受** |
| **进程退出被阻塞** | 非 daemon 僵尸线程 → 解释器退出时 `threading._shutdown` join 全部非 daemon 线程（stdlib L25-31）→ **挂死期间重启应用，旧进程无法退出 → 5000 端口占用 → 新实例启动失败**。这是 PM 未预见的**运营级风险** |
| 请求最终收敛 | 僵尸线程持有的 TCP 连接由服务端/OS 侧最终关闭（分钟~小时级），线程随之终止，无永久泄漏 |

**裁定**：**需要额外处理**。
- **采纳方案甲（daemon 线程）**：daemon 线程不参与解释器退出 join → **进程退出阻塞问题彻底消除**（挂死线程随进程退出被 OS 回收），僵尸仅存于运行期（同方案乙）。
- 若开发选择方案乙（TPE + `shutdown(wait=False)`）：**必须**登记 R-1 残余风险（重启阻塞窗口 = THS 挂死窗口，60-185s/次），并在验收中验证进程可正常退出（M-4）。
- **不使用模块级共享单池**（复用挂死线程的方案）：挂死 worker 占住唯一线程，后续提交排队等待 → 新调用反而被旧挂死阻塞，**比新建池更差**。每调用新建池（≤1 worker）是正确选择。

### A-6：降级链路完整性 —— ✅ 确认完整（附 A-1 前提）

核验 L1158-1168、L1373-1376（证据见 1.3）：
- 超时 → `_call_with_timeout` 返回 None → 与现有异常失败路径**完全一致**：5 秒等待重试 → 备选接口 → 全失败 `_THS_CONSECUTIVE_FAIL_COUNT += 1` → 返回 None → `fetch_capital_flow_batch` L1373 回退 `_em_batch_collect`（EM 逐只）✅
- 缓存仅在 `df is not None` 时写入（L1158-1161）→ 超时后 `_THS_CAPITAL_CACHE` 不写 ✅
- 超时后 worker 后续完成返回的数据被丢弃（不写缓存、不计数）——浪费一次请求但无正确性问题，下次调用重新获取 ✅
- **唯一例外**：PM 原样代码下（with 块）"超时返回 None"在 L1168 之前就被 with 退出阻塞 → A-6 的成立依赖 A-1 修正。

### A-7：范围与红线确认 —— ✅ 完备（补充 2 条）

- 范围收敛正确：**仅 `data_collector.py` 的 `_fetch_capital_flow_ths_batch()` + 常量块**。daily_report.py 调用层 L475-482 已有 try/except 不阻断，无需包装；app.py L1298 第二调用方同源受益。
- **是否需同步处理 daily_report.py 批量预取调用层**：不需要（非阻断设计已存在）。
- 红线 5 条完备，**补充**：
  - **红线 6（挂死线程红线）**：超时路径严禁 join/等待挂死线程——禁止 `with ThreadPoolExecutor` 写法；必须 `shutdown(wait=False)` 或 daemon 线程 join。
  - **红线 7（缓存红线）**：超时/失败路径不得写入 `_THS_CAPITAL_CACHE`（现有结构保证，列为验收断言）。
  - 另注意任务书红线 3 的签名描述与现有代码一致（`_try_ths_primary()`/`_try_ths_rank_backup()` 函数体不改），核验无误。

### A-8：其他裸 akshare 调用 —— ✅ 仅处理 THS（其余登记）

全仓 `ak.` 调用点（19 处）盘点：

| 调用点 | 位置 | 风险评级 | 本批次 |
|---|---|---|---|
| `stock_fund_flow_individual`（THS 主） | data_collector L1175 | 🔴 批量预取层，已实测挂死 | **改** |
| `stock_individual_fund_flow_rank`（备选） | data_collector L1190 | 🔴 同上链路 | **改** |
| `stock_individual_fund_flow`（EM 逐只） | data_collector L2044 | 🟠 **EM 回退循环内单只调用**，单次挂死会击穿 600s 软超时（软超时检查在每只循环**前**，挂死调用内不生效） | **登记 R-2** |
| `stock_financial_analysis_indicator` | L544/L749 | 🟡 单只路径，受 daily_report 90s 包装（该包装带 with 块缺陷，见 R-3） | 不碰 |
| `stock_inner_trade_xq` | L843 | 🟡 单只路径 | 不碰 |
| `stock_financial_hk_analysis_indicator_em` | L918 | 🟡 单只路径 | 不碰 |
| `stock_hsgt_individual_em` | L2331 | 🟡 单只路径（B26 已停更标注） | 不碰 |
| `stock_margin_detail_sse/szse` | L2437/L2451 | 🟡 带日期缓存，每日 1 次 | 不碰 |
| `stock_individual_info_em` | L2702 | 🟡 单只路径 | 不碰 |
| `stock_zh_index_daily`/`stock_hk_index_daily_em` | index_collector L55/L57 | 🟡 指数采集，独立流程 | 不碰 |
| `stock_news_em` | news_collector L75 | 🟡 新闻采集，独立流程 | 不碰 |

**裁定**：019I 仅处理 THS 批量链路（2 处包装）——这是实测根因与报告生成阻塞的直接点；EM 逐只挂死窗口（R-2）与 daily_report with 块隐患（R-3）登记后续批次。

---

## 三、新发现的风险项（R-x）

### R-1（严重）：3.12.9 TPE 工作线程非 daemon → 挂死僵尸线程阻塞进程退出

**证据**：运行时实验 `ThreadPoolExecutor-0_0 daemon= False`；stdlib `thread.py` L194-199 创建线程未传 `daemon=True`；L25-31 `_python_exit()` 在解释器退出前 join 全部 TPE 工作线程。
**影响**：THS 挂死窗口内用户重启应用 → 旧进程无法退出 → 5000 端口占用 → 新实例启动失败（`Address already in use`）。
**处理**：M-1 方案甲（daemon 线程）彻底消除；若选方案乙，此风险存续并需 M-4 验收。

### R-2（中）：EM 逐只回退循环存在同类挂死窗口

`_em_batch_collect`（L1214-1339）的 600s 整体软超时检查在**每只循环开始前**（L1237-1247），但单只 `fetch_capital_flow` 内部（L2033 push2 请求 / L2044 `ak.stock_individual_fund_flow`）任一调用挂死，循环即超时失效。019I 落地后 THS 挂死将更频繁走到 EM 回退路径，此窗口实际可达。**处理**：登记 019J 候选（EM 逐只单调用超时包装，复用本批次 `_call_with_timeout` 模式）；本批次不扩大范围。

### R-3（中）：daily_report.py L533 的 with 块先例携带同一缺陷

单只处理 `with ThreadPoolExecutor(max_workers=1)`（daily_report.py L533-549）退出时同样 join 挂死线程——单只处理挂死时 90s 超时保护**形同虚设**（时间到了但 with 退出仍阻塞）。本次事故未在此处显现（单只路径各接口可返回），属潜伏缺陷。**处理**：登记后续批次；019I 开发**不得复制该模式**（红线 6）。

### R-4（低）：任务书 L149 先例引用事实不精确

daily_report.py 的 `ThreadPoolExecutor` import 在**模块顶部**（L27-28），并非任务书所称"导入放在函数内部（与 L533 使用模式一致）"。属文档性错误，并入 M-3 更正。

### R-5（低）：备选接口为东方财富服务器，超时语义应区分

`ak.stock_individual_fund_flow_rank` 来自 `akshare.stock.stock_fund_em`（EM 源），与 THS 主接口**不同服务器**。THS 挂死时备选可能可用（M-2 优化保留备选的理由）；反之亦然。本批次统一 60s 包装正确，无需分支。

---

## 四、修订项（M-x，PM 据此修订任务书 v2）

### M-1（强制，阻塞验收）：`_call_with_timeout` 实现修正

任务书 L121-127 代码**整体替换**。禁止 `with` 块写法（实验实证会 join 挂死线程导致修复失效）。**推荐方案甲（daemon 线程 join）**，备选方案乙（TPE + `finally: pool.shutdown(wait=False)`），代码见 A-1。二选一：
- 方案甲：消除 R-1（进程退出阻塞），推荐；
- 方案乙：最小改动，但存续 R-1，须通过 M-4 验收。

红线 6（挂死线程红线）随本项加入任务书。

### M-2（建议，不阻塞）：超时跳过重试（THS 阶段上界 185s → 120s）

超时（服务器无响应）后跳过 5 秒等待 + 主接口重试（同服务器重试大概率再超时），直接尝试备选接口（不同服务器）；THS 阶段上界 185s → 120s。需 `_call_with_timeout` 返回超时标志或抛 `_FutTimeout` 供调用方区分。**裁定为建议项**：185s 有界上界已满足功能红线，本项为体验优化，由开发评估实现成本后决定。

### M-3（文档）：任务书事实性表述更正

任务书 L149"`from concurrent.futures` 导入放在函数内部（与 daily_report.py L533 的 ThreadPoolExecutor 使用模式一致）"更正为："daily_report.py 在**模块顶部** import（L27-28），使用在函数内（L533）；019I 的 import 位置由开发按所选方案决定（方案甲需模块顶部 `import threading`）"。

### M-4（验收补充）：超时断言显式化 + 进程退出验证

1. **显式耗时断言**：QA 验收标准第 3 条补充"断言 `_fetch_capital_flow_ths_batch` 在 **65 秒内**返回 None"——该断言恰好可捕获 M-1 缺陷（with 块版本需等 mock 挂死时长 120s 才返回，耗时断言失败）。
2. **进程退出验证（方案乙必选/方案甲建议）**：挂死线程存活时启动/退出应用进程，断言进程可在合理时间内退出（避免 5000 端口占用隐患，R-1）。
3. 补充红线 7 断言：超时后 `_THS_CAPITAL_CACHE['data']` 仍为 None。

---

## 五、修订后任务范围（定稿）

| Task | 文件 | 改动 | 状态 |
|---|---|---|---|
| Task 1 | `modules/data_collector.py` | THS 常量块追加 `_THS_REQUEST_TIMEOUT = 60`（L1098 后） | ✅ 通过 |
| Task 2 | `modules/data_collector.py` L1120-1168 | `_fetch_capital_flow_ths_batch()` 内定义 `_call_with_timeout`（按 M-1 修正版），包装主/重试/备选 3 处调用 | ✅ 通过（M-1 修正） |
| 不改 | `_try_ths_primary()` / `_try_ths_rank_backup()` 函数体 | 签名与实现零改动 | ✅ |
| 不改 | `daily_report.py` / `app.py` / `config.py` / `requirements.txt` | 零改动（app.py L1298 调用方自动受益） | ✅ |
| 范围外 | EM 逐只挂死窗口 | 登记 R-2（019J 候选） | 📌 |
| 范围外 | daily_report L533 with 块隐患 | 登记 R-3 | 📌 |

**实际改动：1 个文件，1 个函数 + 1 个常量，零新依赖。**

---

## 六、修订后验收标准

1. **代码级核查（PM 独立核验）**：
   - `_THS_REQUEST_TIMEOUT = 60` 存在于 THS 常量块（L1098 后）
   - 主/重试/备选 3 处调用均经 `_call_with_timeout` 包装；**无 `with ThreadPoolExecutor` 写法**（grep 该函数内为 0 处）
   - 超时路径返回 None；`_try_ths_primary`/`_try_ths_rank_backup` 函数体零改动
2. **编译验证**：`python -m py_compile modules/data_collector.py` 无错误
3. **超时保护验证（QA 重点，M-4 强化）**：
   - mock `_try_ths_primary` 为 `time.sleep(120)` → 断言 **65 秒内**返回 None（含耗时断言，可捕获 with 块缺陷）
   - 断言日志含 `[同花顺批量] 主接口 超时(60s)，跳过`
4. **降级链路完整性验证**：主接口超时 → 5 秒等待 + 重试 → 备选 → 全失败 `_THS_CONSECUTIVE_FAIL_COUNT += 1` → 返回 None → 调用方回退 `_em_batch_collect`（全部按序断言）；断言超时后 `_THS_CAPITAL_CACHE['data']` 仍为 None（红线 7）
5. **正常路径回归**：mock 正常返回 DataFrame → 透明传递无额外开销；成功时失败计数重置、缓存写入
6. **进程退出验证（M-4，方案乙必选）**：挂死线程存活期间退出进程 → 断言进程在合理时间（如 10s）内退出（方案甲自动满足）
7. **零改动确认**：daily_report.py、app.py、config.py、requirements.txt、index.html 文件哈希不变；`_try_ths_primary`/`_try_ths_rank_backup` 函数体 diff 为空

---

## 七、红线约束（确认 + 补充）

**确认（任务书第五节 5 条）**：功能红线（不得永久阻塞）✅ 范围红线（仅 data_collector.py）✅ 签名红线（三函数签名/函数体不变）✅ 零代码约束（stdlib 线程库，无新依赖）✅ 降级安全红线（超时返回 None 一致走降级）✅

**补充**：
6. **挂死线程红线**：超时路径严禁 join/等待挂死线程——禁止 `with ThreadPoolExecutor` 写法；必须 `shutdown(wait=False)` 或 daemon 线程 join（M-1，实验实证）
7. **缓存红线**：超时/失败路径不得写入 `_THS_CAPITAL_CACHE`（列入验收断言）
8. **先例红线**：不得复制 daily_report.py L533 的 with 块模式（R-3，该模式自身带 join 挂死缺陷）

---

## 八、流程路径

```
✅ PM 签发 v1 → ✅ 架构师独立评审（本评审）→ ⏳ PM 按 M-1~M-4/R-1~R-5 修订任务书 v2 → 待监理批准 → 待开发执行+自验 → 待 QA 独立验收（含 M-4 强化断言）→ 待双签 → 待关闭
```

---

## 九、签署

> **架构师独立评审签署**：本评审由架构师于 2026-08-05 独立完成。核验文件：modules/data_collector.py（L1095-1168 / L1171-1211 / L1214-1339 / L1342-1376 / L1873-1912 / L2425-2454）、modules/daily_report.py（L20-49 / L460-549）、app.py（L1265-1314 / L3202-3249 / L3967）、config.py（L111-117）、requirements.txt、logs/app.log L186-188、app.log.2026-08-03、akshare 1.18.53 源码（stock_fund_flow.py、thread.py）、stdlib concurrent/futures/thread.py（Python 3.12.9），并完成 3 项本机运行时实验。未采信 PM 结论。
>
> **架构师总结**：PM 任务书的**根因定位、60s 阈值、常量位置、范围收敛均正确**，但**核心实现存在致命缺陷**：`with ThreadPoolExecutor` 块退出时的 `shutdown(wait=True)` 会 join 挂死线程——按任务书原样实现，修复无效，报告生成依旧永久阻塞（仅多打一条日志）。经实验实证后裁定：必须显式 `shutdown(wait=False)` 或采用 daemon 线程 join 模式。同时发现 PM 未预见的升级风险：Python 3.12.9 的 TPE 工作线程**非 daemon**，挂死僵尸线程会阻塞进程退出，用户在故障窗口内重启应用将面临端口占用无法启动。推荐方案甲（daemon 线程）可一并消除该风险。EM 逐只回退循环存在同类挂死窗口（R-2）与 daily_report 先例隐患（R-3），登记后续批次处理。**评审结论：有条件通过**，请 PM 按 M-1（强制）~M-4 修订任务书 v2 后提交监理批准。
