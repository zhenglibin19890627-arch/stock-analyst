# 架构评审 019J — 单只处理超时保护修复（报告线程 join 挂死缺陷 R-3）【架构师定稿】

**评审日期**：2026-08-05
**评审人**：架构师（独立 Read 代码核验 + 本机 Python 3.12.9 运行时实验，不采信 PM 结论）
**任务书**：`docs/tasks/dev_tasks_20260805_019J_single_stock_timeout_fix.md`（PM 签发 v1）
**关联先例**：019I 架构评审（`review_019I_ths_batch_timeout_20260805.md`，方案甲生产实证）
**评审结论**：✅ **通过**（无强制修订项；附 3 条建议性补充 M-1~M-3 与 3 项新登记风险 R-6~R-8）

---

## 〇、评审摘要（决策点裁定总览）

| 决策点 | 内容 | 架构师裁定 | 结论 |
|---|---|---|---|
| A-1 | 超时保护方案选型（daemon 线程 + box 模式） | ✅ **采纳**（实验 1-4 实证四态语义正确；方案乙复核否决理由成立） | 通过 |
| A-2 | worker 迟到自愈是否保留 | ✅ **保留**（DB 独立实证覆盖行为真实存在；极窄竞态登记 R-7，不追加防护） | 通过 |
| A-3 | 线程泄漏与并发上限 | ✅ **可接受**（最坏 29 个 daemon 线程；跨批次覆盖登记 R-6，评估可接受） | 通过 |
| A-4 | R-2 是否纳入本批次 | ⚠️ **维持登记**（019K 候选，P2；不纳入 019J——跨模块一次变更一个缺陷面） | 通过（排除） |
| A-5 | 超时后写库线程安全 | ✅ **可接受**（WAL + busy_timeout=10s 实证；DELETE+INSERT 幂等，不加唯一索引） | 通过 |
| A-6 | 范围与红线确认 | ✅ **完备**（intraday 自动覆盖；补充建议性红线 2 条并入 M-3） | 通过（含补充） |
| A-7 | 验收标准充分性 | ✅ **充分**（95s 断言合理且能捕获 with 块缺陷；补充 3 项细化建议 M-2） | 通过（含补充） |

**核心结论**：
1. **缺陷机制独立确认**（对照实验实证）：`with ThreadPoolExecutor` 块在 `future.result(timeout=5)` 于 5.01s 抛 TimeoutError 后，with 块退出时刻为 **120.00s**（= 挂死 worker 时长）——PM 根因定位正确，L570 `continue` 在 with 块内，超时保护形同虚设。
2. **PM 方案（daemon 线程 + box 模式）经本机实验 4 例全 PASS**：超时立即返回不 join（5.00s）、异常显式传播（box['exc']）、成功取 box['r']、临界完成无竞态（4.90s 完成走成功路径）。与 019I 方案甲同源，方向正确，无需修改。
3. **独立日志+DB 实证**（非采信 PM）：16:23:21 600276 超时 → 16:28:42 才继续（阻塞 5m21s）；16:30:40 000333 超时 → 16:39:10 才继续（阻塞 8m30s）；批次 1845s 超 1800s 截断。DB 当前仅存覆盖后 ok 记录（600276 id 1044 / 000333 id 1048，generated_at 分别为 16:28:42.084 / 16:39:10.406），同日同股仅 1 条——**自愈行为真实存在**。
4. **无强制修订项**：本批次按任务书 v1 可直接进入监理批准与开发执行；3 条建议性补充（M-1~M-3）不阻塞开发。

---

## 〇-1、独立核验签署记录

| 项 | PM 结论 | 架构师独立核验证据 | 裁定 |
|---|---|---|---|
| 根因 | L531-570 with 块 join 挂死线程 | Read `daily_report.py` L533 `with ThreadPoolExecutor(max_workers=1)`、L538 `future.result(timeout=STOCK_TIMEOUT_SECONDS)`、L570 `continue`（在 with 块内）；对照实验：TimeoutError 5.01s 后 with 块 120.00s 才退出 | ✅ 确认 |
| 阻塞窗口实证 | 600276 5m20s / 000333 8m30s | app.log 独立检索：16:23:21 超时→16:28:42 2/29（5m21s）；16:30:40 超时→16:39:10 5/29（8m30s）；16:52:37 批次整体超时剩 13 只，批次 成功13/失败16 耗时1845s | ✅ 确认 |
| 自愈覆盖实证 | id 1043 failed→1044 ok | DB 独立查询：2026-08-05 600276 仅 id 1044 ok（generated_at 16:28:42.084=超时后 worker 完成时刻）；000333 仅 id 1048 ok（16:39:10.406）——failed 记录已被覆盖，同日仅 1 条 | ✅ 确认 |
| daemon 方案语义 | 超时立即返回、不阻塞退出 | 实验 1：join(timeout=5) 后 5.00s 返回，is_alive=True，thread.daemon=True；019I 生产实证（THS 预取 5199 只 45s 完成） | ✅ 确认 |
| R-2 挂死窗口 | `_em_batch_collect` 单只调用无超时 | Read `data_collector.py` L1261-1271（软超时检查在循环开始处）+ L1326 `fetch_capital_flow(sym,'a_stock')` 无超时包装；熔断（L1273-1283）仅对"失败"计数生效，挂死不计数 → 软超时与熔断对挂死均失效 | ✅ 确认（不纳入本批次） |

---

## 一、独立核验记录（关键证据）

### 1.1 缺陷机制核验（`modules/daily_report.py` L531-570）

Read 实证（L531-570）：
- L533 `with ThreadPoolExecutor(max_workers=1) as executor:` — with 块
- L534-536 `future = executor.submit(_process_single_stock, stock, target_date, force, report_type)` — worker 提交
- L538 `result = future.result(timeout=STOCK_TIMEOUT_SECONDS)` — 90s 超时
- L539 `except FuturesTimeout:` — 超时分支
- L540-569 超时处理（fail_count += 1、_save_report failed、results.append failed）
- **L570 `continue` — 位于 with 块内部** → with 块退出时 `__exit__` 调用 `shutdown(wait=True)` join 挂死 worker

**对照实验（本机 Python 3.12.9，复现缺陷机制）**：
```
[对照] with块: TimeoutError at 5.01s, continue 在 with 内部
[对照] with块退出时刻: 120.00s (挂死 120s worker，阻塞至其完成=缺陷)
```
→ 与 019I 实验 1 结论一致：with 块版本超时后仍阻塞至 worker 完成；若 worker 永久挂死，**报告线程永久卡死**。019I 红线 8 登记（R-3）成立。

### 1.2 方案验证实验（本机 Python 3.12.9，PM box 模式 4 例全 PASS）

实验脚本按任务书 L126-145 原样实现 box 模式（`box={'exc': None}` + `_run` 闭包 + `Thread(daemon=True)` + `join(timeout=5)` + `is_alive()` 分支），4 例结果：

| 用例 | 结果 | 语义 |
|---|---|---|
| case1 超时（worker sleep 120s） | elapsed=5.00s，alive=True，daemon=True → 超时分支 | ✅ 超时立即返回，不 join |
| case2 worker 抛异常 | elapsed=0.00s → `box['exc']` 显式传播 | ✅ 异常路径语义保持 |
| case3 worker 正常返回 | elapsed=0.00s → `box['r']` 取回 | ✅ 成功路径语义保持 |
| case4 临界完成（sleep 4.9s < 5s） | elapsed=4.90s，alive=False → 成功路径 `OK: late-ok` | ✅ join 返回后 is_alive 判定无竞态 |

→ **PM 方案语义正确性实证**：超时/异常/成功/临界四态均符合任务书语义要求。

### 1.3 自愈行为独立实证（DB 查询）

`SELECT id, report_date, stock_code, status, generated_at FROM daily_reports WHERE report_date='2026-08-05' AND stock_code IN ('600276','000333')`：
```
id 1044, 600276, status=ok, generated_at=2026-08-05T16:28:42.084+08:00
id 1048, 000333, status=ok, generated_at=2026-08-05T16:39:10.406+08:00
```
- 超时被跳过（16:23:21/16:30:40 已写 failed 记录）→ worker 迟到完成 → `_save_report`（DELETE+INSERT）覆盖为 ok → **当前库中仅剩 ok 记录，同日同股仅 1 条**。自愈行为真实且结果干净（无重复行）。

### 1.4 相关代码核验

| 位置 | 内容 | 结论 |
|---|---|---|
| `daily_report.py` L25 | `import threading` 已存在 | ✅ 直接用 |
| `daily_report.py` L27-28 | `from concurrent.futures import ThreadPoolExecutor` / `TimeoutError as FuturesTimeout`（模块顶部） | ⚠️ 修复后变未使用，见 M-1 |
| `daily_report.py` L323-439 | `_process_single_stock`：复用检查→采集→generate_advice→_save_report→返回 dict，或 raise | ✅ 零改动可行（裸名调用 L535，monkeypatch 可行） |
| `daily_report.py` L410-426 / L228-299 | `_save_report`：DELETE（daily 删全部类型 / intraday 仅删 intraday，L257-267）+ INSERT + commit 单事务 | ✅ 类型隔离确认；幂等（同日同股仅 1 条） |
| `daily_report.py` L504-511 | 批次整体软超时（循环内检查） | ✅ 不受影响 |
| `daily_report.py` L598-632 | 外层 `except Exception`：fail_count+1 + failed 记录 + results.append | ✅ box['exc'] re-raise 后语义一致 |
| `daily_report.py` L453 / L698-699 | `_generate_lock` acquire(timeout=5) / finally release | ✅ 迟到 worker 不持锁 |
| `daily_report.py` L308-320 | `_update_progress_file` 仅主线程调用（L489/517/641） | ✅ 无并发 |
| `config.py` L113-117 | `STOCK_TIMEOUT_SECONDS=90` / `BATCH_TIMEOUT_SECONDS=1800` | ✅ 不改 |
| `database/db_manager.py` L34-37 | `sqlite3.connect(timeout=10)` + WAL + `busy_timeout=10000` | ✅ 写锁竞争上限 10s |
| `data_collector.py` L1099 / L1153-1165 | 019I `_THS_REQUEST_TIMEOUT=60` + `_call_with_timeout`（daemon 线程，返回 `(r, timed_out)`） | ✅ 先例同源 |
| `data_collector.py` L1261-1326 | `_em_batch_collect` 软超时在循环开始处；L1326 单只调用无超时包装 | ✅ R-2 挂死窗口仍存在 |

全仓 grep `ThreadPoolExecutor`：仅 `daily_report.py` L533 一处使用（+ L27 import、L324 docstring）；`data_collector.py` 仅在注释中提及。

---

## 二、逐决策点裁定

### A-1：超时保护方案选型 —— ✅ 采纳（无修改）

**裁定**：采纳 PM 方案（daemon 线程 `threading.Thread(daemon=True)` + `t.join(timeout=STOCK_TIMEOUT_SECONDS)` + `is_alive()` 超时分支 + `box` 异常容器）。实验 1-4 实证四态语义与任务书要求完全一致。

**核验记录**：
- 缺陷机制成立（1.1 对照实验）：`continue` 在 with 块内，with 退出 join 挂死线程 → PM 根因正确。
- daemon 线程在 Windows + Python 3.12 下语义正确：`join(timeout)` 超时立即返回（实验 1：5.00s）、`is_alive()` 判定准确（实验 4：临界完成无竞态）、`daemon=True` 不阻塞解释器退出（实验 1 属性确认 + 019I 生产实证）。本方案纯标准库线程 API，不依赖信号（signal.alarm 在 Windows 不可用，019I 已裁定），平台语义一致。
- **box 模式为最简等效方案**：本调用点需处理"返回值 + 异常 + continue"三态，019I 的 `_call_with_timeout`（返回 `(r, timed_out)`、异常静默吞掉返回 None）**不适用**——019I 场景 `_try_ths_*` 自兜底返回 None，而 `_process_single_stock` 抛异常必须走外层 `except Exception`（fail_count + failed 记录，L598-632）。box 模式是必要增强，非过度设计。
- **方案乙复核（`future = executor.submit(...)` + 显式 `shutdown(wait=False)`）——维持否决**：019I 实验 3 + stdlib 源码核验（`concurrent/futures/thread.py` L194-199 创建线程未传 daemon=True）——TPE 工作线程**非 daemon**，挂死僵尸线程阻塞解释器退出（R-1）→ 违反本批次红线 8（进程退出不阻塞）。方案甲（daemon Thread）是唯一同时满足"超时立即返回 + 进程退出不阻塞"的标准库方案。
- 与 019I 方案甲一致性：✅ 同模式（daemon + join(timeout)），生产实证有效；差异仅在本处需显式异常传播（box['exc']）与超时分支写库（continue），符合任务书语义要求。

**实现注意（不修改任务书）**：
1. 超时分支的 `is_alive()` 判定在 `join(timeout)` 返回后执行——顺序正确（实验 4）。
2. `box['r']` 仅在 `is_alive()==False` 且 `box['exc'] is None` 时读取，无 KeyError 风险（异常完成→raise 先于读 r；超时→continue 不读 r）。
3. 边缘语义差异（可接受，登记观察）：worker 内抛 `KeyboardInterrupt`/`SystemExit`（BaseException 子类）时，`except Exception` 不捕获 → 线程静默死亡 → `box['r']` KeyError → 外层按普通异常记为 failed。修复前该场景会向上传播出整个函数。实际影响近零（worker 线程内极少出现），且新语义（记 failed 不炸批次）反而更稳健。
4. 内层闭包 `_run` 定义于循环内可接受（作用域收敛）；开发若偏好模块级辅助函数亦可，不强制。

### A-2：worker 迟到自愈行为 —— ✅ 保留（附边界登记）

**裁定**：保留自愈（超时后 worker 后台继续，完成时 `_save_report` 覆盖 failed→ok）。

**核验记录**：
- **自愈行为真实存在**（1.3 DB 实证）：600276/000333 超时后 worker 均完成评分并覆盖为 ok，当前库中同日同股仅 1 条 ok 记录。覆盖由 `_save_report` 的 DELETE+INSERT 单事务实现（L257-299）。
- **竞态分析**：主线程超时写 failed 与 worker 迟到写 ok 均为独立连接 + 单事务（DELETE+INSERT+commit）。SQLite WAL + `busy_timeout=10000`（db_manager L34-37）保证写写串行化——两写最终顺序由 commit 先后决定，**最后提交者胜**。期望时序（worker 后完成→ok 后提交→ok 胜出）确定成立。
- **极窄竞态（新登记 R-7）**：worker 恰在 `is_alive()==True` 判定后、主线程 failed 写库 commit 前完成并先提交 ok → failed 覆盖 ok → 该股当日数据丢失（worker 不再写第二次）。窗口 = 毫秒级 × 挂死 ≥90s 的概率 → 概率可忽略；影响 = 当日该股记录为 failed（下次手动生成可自愈，非永久丢失）。**不追加防护**：唯一低成本防护（failed 写前复查 is_alive）自身引入新竞态且复杂化；防丢失可依赖下次生成。
- **类型互覆核验**：`_save_report` DELETE 条件按 report_type 区分（L257-267：daily 删含 intraday 的全部记录；intraday 仅删 intraday）→ **不存在类型互相覆盖**。intraday 自愈不会误删 daily 行，反之亦然。QA"同日仅 1 条"断言足够，无需额外防护。
- 任务书语义"超时写 failed + worker 迟到覆盖"与现状一致（08-05 实证）→ 保留。

### A-3：线程泄漏与并发上限 —— ✅ 可接受（登记 R-6）

**裁定**：可接受，无需泄漏防护；新发现跨批次覆盖风险登记 R-6（低危，接受）。

**核验记录**：
- **数量级评估**：最坏 29 个后台 daemon 线程（29 只全超时且全挂死），默认栈 1MB → 约 29MB 提交内存；挂死线程存活至进程退出，不回收。与 019I（每批 ≤3 个）相比 ×10，但 29 个绝对量级小；且触发前提为"单只挂死"（低频事件，今日 29 只中仅 3 只超时）。**可接受**。
- **进程退出**：daemon=True（实验 1 属性确认）→ 挂死线程随进程退出被 OS 回收，不阻塞（红线 8 满足）。
- **运行期影响**：僵尸线程不阻塞主线程继续循环（超时即 continue）；新批次不受影响（`_generate_lock` 在批次间 finally 释放，L698-699）。
- **并发写库**：worker 迟到写 ok 与主线程/后续批次/预警扫描写库并发 → WAL + busy_timeout=10s 覆盖，写事务短（单条 DELETE+INSERT）无死锁；最大等待 10s 可接受。
- **新发现风险 R-6（跨批次覆盖）**：批次 A 超时 worker 挂死超过批次 A 结束时刻，批次 B（同日期同类型）生成期间 worker 完成写库 → 用批次 A 时刻的旧评分覆盖批次 B 的新记录。触发概率低（需 worker 挂死 > 批次间隔），影响轻微（同日期同类型记录被提前 ~10 分钟的数据覆盖）。防护需在 `_save_report` 增加批次时间戳校验（改签名/表结构）→ 违反零代码与范围红线，**裁定接受 + 登记观察**，不追加防护。
- **线程计数上限防护**：不需要。29 个已是理论上限，进程每日可重启（start.bat），无累积性（每次批次后旧线程若完成即回收）。

### A-4：R-2 是否纳入本批次 —— ⚠️ 维持登记（不纳入，019K 候选，P2）

**裁定**：维持登记，不纳入 019J。理由如下：

1. **R-2 挂死窗口独立确认仍存在**：`_em_batch_collect`（data_collector.py L1238-1359）软超时检查（L1261-1271）在**每只循环开始前**，单只调用 `fetch_capital_flow(sym, 'a_stock')`（L1326）无超时包装 → 单只挂死时循环卡死，软超时检查点无法到达。熔断（L1273-1283）仅对**失败计数**生效（L1338/L1346 失败才 +1），**挂死不计数** → 熔断对挂死场景失效。理论可无限阻塞。
2. **但实际暴露面窄**：R-2 仅存在于**批量预取 EM 回退链路**（generate_daily_report L479 前置预取）。019J 修复后，EM 单只调用在**单只路径**（`_process_single_stock` → `collect_stock_data` → `fetch_capital_flow`）已被 90s 包装覆盖；R-2 剩余暴露面仅为"THS 双接口全失败 + EM 单只挂死"同现（今日实证 EM 全失败熔断工作正常——失败型防护有效，挂死型未观测到）。预取链路产出为辅助指标（资金面），挂死影响是预取阶段阻塞，非报告主路径数据丢失。
3. **范围纪律**：R-2 属 data_collector.py（不同模块），与 019J（daily_report.py 单文件）跨模块捆绑违反"一次变更一个缺陷面"；019I A-8 已定先例（"登记后续批次，本批次不扩大范围"）。本批次任务书范围、验收、红线均已收敛，追加将稀释验收质量。
4. **修复成本已预置**：019J 落地后 `_call_with_timeout` 模式成熟可复用；019K 预计改动 <20 行（data_collector.py 单函数包装 1 个调用点）。若监理判断需要提前，可单独签发小批次，不阻塞。

**裁定后附注**：PM 倾向与本裁定一致；本裁定不改变任务书范围。

### A-5：超时后 `_save_report` 写库的线程安全 —— ✅ 可接受（不加唯一索引）

**裁定**：可接受，无需防护。

**核验记录**：
- 主线程超时分支写 failed 与 worker 迟到写 ok 均走 `_save_report`（独立连接 + 单事务 DELETE+INSERT+commit）。WAL + busy_timeout=10s（db_manager L34-37）→ 写写冲突互等 ≤10s 后成功，无死锁（无嵌套事务、无跨连接锁、无长事务）。
- 最终状态确定性：最后提交者胜（A-2 分析）。期望时序下 ok 必胜；唯一不确定窗口为 R-7 极窄竞态（已登记，概率可忽略）。
- **唯一索引（report_type+stock_id+report_date）不需要**：DELETE+INSERT 模式本身保证同日同股仅 1 条（DB 实证 id 1043→1044 覆盖后仅 1 条）；加唯一索引与现有模式冗余，且改 schema 违反零代码红线与"不碰 DB"范围。裁定：不加。
- 幂等约束已由 `_save_report` 的 DELETE 语义天然满足（L257-267）。

### A-6：范围与红线确认 —— ✅ 完备（补充建议性红线 2 条）

**裁定**：完备；补充 2 条建议性红线（并入 M-3，不阻塞）。

**核验记录**：
- **红线逐条核验**：M-1（对照实验实证 with 块缺陷，严禁复制）✅；范围（全仓 grep 仅 L533 一处使用，缺陷点唯一）✅；语义（超时/异常/成功三路径任务书描述与现状一致，核验 L540-596/L598-632）✅；签名（`_process_single_stock` L323-439 零改动可行——调用为裸名 L535，替换 executor.submit 为 daemon 线程后函数体不接触）✅；零代码（threading 标准库，L25 已 import）✅；数据自愈（A-2 裁定保留）✅；进程退出（daemon=True）✅。
- **遗漏风险点核验**：
  - `_update_progress_file`（L308-320）：仅主线程调用（L489/L517/L641），worker 不触碰 → 无并发 ✅
  - `_generate_lock`（L453/L698-699）：finally 释放实证 → 迟到 worker 不持锁、不阻塞后续批次 ✅
  - **intraday 路径**：`generate_daily_report(target_date, force, report_type)` 同一函数承载两种类型（L442-449），report_type 经 L535/L559 透传，L531-570 块类型无关 → **修复自动覆盖 daily + intraday，无需额外改动** ✅
- **补充建议性红线**（并入 M-3）：
  - **红线 9（语义隔离）**：超时分支 failed 记录 error_msg 必须为 `采集超时(90s)`（L557 现状），异常路径不得复用该文案——QA 按 error_msg 区分两类失败断言。
  - **红线 10（worker 不可阻断）**：超时分支严禁对 worker 做任何 join/等待/终止尝试（不得在超时后追加任何阻塞调用）；worker 迟到写库不得被取消。
- **import 清理（M-1）**：修复后 L27-28 的 `ThreadPoolExecutor`/`FuturesTimeout` 变未使用。可选清理（同文件内无害微改，不违反范围红线精神）；若不清理，ruff 可能提示 F401（未使用 import）——零代码用户无 ruff 约束，非阻塞。

### A-7：验收标准充分性 —— ✅ 充分（补充 3 项细化建议 M-2）

**裁定**：充分，按 M-2 细化后执行。

**核验记录**：
- **95s 断言合理且有效**：STOCK_TIMEOUT_SECONDS=90 + 容差 5s。对照实验实证——with 块版本 mock 挂死 120s 时需等满 120s 才返回，95s 断言恰好失败 → **该断言可捕获 M-1 缺陷**（019I M-4 同款思路）。QA 机器时序抖动余量 25s，充分。
- **QA mock 可行性确认**：`_process_single_stock` 为模块级函数（L323），调用点为裸名（L535）→ 运行时模块属性查找 → QA 可 `daily_report._process_single_stock = fake` 直接替换；`generate_daily_report` 模块级可导入。✅ 函数可替换性成立。
- **补充细化建议**：
  1. **超时-自愈时序断言显式化**：标准 3/4 补充"分两次查库"——超时返回后（≤95s，worker 仍在 sleep）立即查库断言 failed 记录存在（error_msg 含"超时"）；≥120s 后再查库断言被覆盖为 ok 且同日同股仅 1 条。当前标准 3/4 各自覆盖一点但未明确时序，显式化可防 QA 一次性晚查漏验 failed 中间态。
  2. **report_type 隔离回归断言（可选）**：intraday 自愈覆盖不删除 daily 行（`_save_report` L257-267 行为回归）。
  3. **线程属性断言（可选）**：超时后 `threading.enumerate()` 中断言新增线程 `daemon=True`（验证红线 8 属性级保证）。
  4. **进程退出断言（标准 7）**：已含（挂死 worker 存活时退出 ≤10s）✅ 无需补充。

---

## 三、新发现的风险项（R-x，延续 019I 编号）

### R-6（低）：迟到 worker 跨批次覆盖（019J 修复引入的新增风险面）

019J 修复首次引入"worker 存活期超过其批次"的窗口（修复前 with 块会等 worker 完成）。批次 A 超时 worker 挂死期间批次 B（同日期同类型）启动并完成 → worker 迟到写库覆盖批次 B 的新记录为批次 A 时刻的旧评分。
- **证据**：`_generate_lock` 仅串行化批次入口（L453/L698-699），不隔离迟到 worker；`_save_report` DELETE+INSERT 最后提交者胜。
- **影响**：同日期同类型记录被 ~10 分钟级旧数据覆盖；概率低（需 worker 挂死 > 批次间隔），无数据永久丢失（下次生成覆盖）。
- **处理**：接受，登记观察。防护需批次时间戳（改签名/schema）超出零代码红线，不实施。

### R-7（低）：超时边界极窄竞态——failed 覆盖已完成的 ok

worker 恰在 `is_alive()==True` 判定后、主线程 failed 写库 commit 前完成并先提交 ok → failed 覆盖 ok → 该股当日数据丢失（worker 不再写第二次）。
- **证据**：实验 4 证明临界完成判定正确（≤4.9s 完成走成功路径），但 `is_alive()==True` 与主线程 commit 之间存在毫秒级窗口；时序要求"worker 挂死 ≥90s 后恰在毫秒窗口内完成"，概率可忽略。
- **影响**：单只当日记录为 failed（下次生成自愈）。
- **处理**：接受，登记观察。不追加防护（低成本防护引入新竞态）。

### R-8（低）：box 模式吞 BaseException 子类（行为微变）

worker 内抛 `KeyboardInterrupt`/`SystemExit` 时 `except Exception` 不捕获 → 线程静默死亡 → `box['r']` KeyError → 外层记为普通失败。修复前该异常会传播出整个函数。
- **证据**：任务书 L133 `except Exception as e`（A-1 实验 case2 验证 Exception 传播正常）。
- **影响**：近零（worker 线程内极少出现该类异常）；新语义（记 failed 不炸批次）反而更稳健。
- **处理**：接受，登记观察。

---

## 四、修订项（M-x）

> **本批次无强制修订项（M-1 级 = 无）。以下均为建议性补充，不阻塞监理批准与开发执行。**

### M-1（建议）：未使用 import 清理（可选）

修复后 `daily_report.py` L27-28 `from concurrent.futures import ThreadPoolExecutor` / `TimeoutError as FuturesTimeout` 不再使用。开发可顺手删除（同文件内无害微改）；保留亦无害（不影响运行，ruff F401 属 lint 级提示，零代码用户无此约束）。**不阻塞，不纳入验收**。

### M-2（建议）：验收时序断言显式化（QA 执行细则）

- 标准 3/4 补充"分两次查库"时序（见 A-7 补充建议 1）：超时返回后立即查 failed 中间态；≥120s 后查 ok 终态 + 同日仅 1 条。
- 可选补充：report_type 隔离回归断言（intraday 覆盖不删 daily 行）；`threading.enumerate()` daemon 属性断言。

### M-3（建议）：任务书红线补充 2 条（不改变范围）

- **红线 9（语义隔离）**：超时分支 error_msg 固定 `采集超时(90s)`，不得与异常路径文案混用（QA 按 error_msg 区分断言）。
- **红线 10（worker 不可阻断）**：超时分支严禁对 worker 追加任何 join/等待/终止调用；不得取消 worker 迟到写库。

---

## 五、评审结论

### ✅ 通过（无强制修订项）

1. **根因实锤**（独立核验）：with 块 `shutdown(wait=True)` join 挂死线程（对照实验 5.01s 超时 → 120s 才退出）；日志（5m21s/8m30s 阻塞窗口、批次 1845s 截断）与 DB（覆盖后仅 ok 记录 id 1044/1048）独立实证，无需复核 PM 根因结论的重复性工作。
2. **方案裁定**：PM 方案甲（daemon 线程 + box 模式）经本机 4 例实验全 PASS，语义与任务书完全一致；与 019I 方案甲同源且生产实证有效；方案乙复核否决（TPE 非 daemon → 进程退出阻塞，R-1）。**采纳，零修改**。
3. **范围裁定**：daily_report.py L531-570 单一缺陷点；intraday 自动覆盖；`_update_progress_file`/`_generate_lock` 无并发问题；红线完备（补充 2 条建议性红线）。
4. **取舍裁定**：自愈保留（实证真实、无类型互覆、竞态可接受）；R-2 维持登记（019K 候选，跨模块一次变更一个缺陷面）；不加唯一索引（DELETE+INSERT 已幂等）；线程泄漏可接受（最坏 29 个 daemon 线程）。
5. **新登记风险**：R-6（跨批次覆盖，低）、R-7（超时边界竞态，低）、R-8（BaseException 吞并，低）——均评估可接受，不追加防护（防护成本 > 收益且触碰零代码/范围红线）。

**流程路径**：
```
✅ PM 签发 v1 → ✅ 架构师独立评审（本评审）→ ⏳ 监理批准 → ⏳ 开发执行+自验（可含 M-1 可选清理）→ ⏳ QA 独立验收（按 M-2 细化时序断言）→ ⏳ PM+QA 双签 → ⏳ 监理批准关闭
```

---

## 六、签署

> **架构师独立评审签署**：本评审由架构师于 2026-08-05 独立完成，未采信 PM 结论。核验文件：modules/daily_report.py（L1-80 / L225-300 / L308-439 / L442-639 / L640-699）、modules/data_collector.py（L1090-1219 / L1238-1359）、config.py（L111-117）、database/db_manager.py（L20-39）、docs/tasks/dev_tasks_20260805_019J_single_stock_timeout_fix.md、docs/reviews/review_019I_ths_batch_timeout_20260805.md、logs/app.log（2026-08-05 16:15-16:52 时段）、stock_analyst.db（daily_reports 表 id 1044/1048），并完成本机 Python 3.12.9 运行时实验 5 项（box 模式 4 例全 PASS + with 块对照复现缺陷）。
>
> **架构师总结**：PM 任务书根因定位、方案选型、范围收敛、验收设计**全部正确**——本批次是 019I 评审结论中"先例红线"登记的正式落地修复。独立核验确认：缺陷机制（with 块 join 挂死）、日志实证（5m21s/8m30s 阻塞）、DB 实证（自愈覆盖后同日仅 1 条）均属实；PM 方案（daemon 线程 + box 模式）经本机实验四态全 PASS，语义要求全部满足，与 019I 方案甲同源且生产实证有效。裁定全部采纳：自愈保留（真实特性，竞态可接受）、R-2 维持登记（跨模块，019K 候选）、不加唯一索引、线程泄漏可接受。新登记 3 项低危风险（R-6 跨批次覆盖 / R-7 超时边界竞态 / R-8 BaseException 吞并）均评估为"接受，不追加防护"。**评审结论：通过**，无强制修订项，建议性补充 M-1~M-3 不阻塞开发；请监理批准后交付开发执行，QA 验收按 M-2 细化时序断言。
