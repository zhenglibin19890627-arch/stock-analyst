# DEV-TASKS-20260805-019I-ARCH：019I THS 批量预取超时保护 — 架构方案评审任务书

> **签发人**：PM  | **签发日期**：2026-08-05 | **状态**：待架构师执行

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：架构师

**职责边界**：
- 复核 PM 签发的 019I 开发任务书（`docs/tasks/dev_tasks_20260805_019I_ths_batch_timeout.md`）
- 对每个决策点给出明确裁定 + 理由
- **不编码、不验收、不写功能代码**
- 交付物：`docs/reviews/review_019I_ths_batch_timeout_20260805.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 架构师仅做方案评审，不执行任何代码修改
- PM 产出的任务书仅供参考，架构师须独立 Read 代码核验，不采信 PM 结论

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 技术栈 | Python + Flask + SQLite + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包） |
| 前序批次 | 019E/019F/019G/019H 均✅已关闭；019I 为新缺陷修复批次 |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 交付物 | `docs/reviews/review_019I_ths_batch_timeout_20260805.md` |

---

## 一、需求背景

### 1.1 缺陷描述

用户反馈**每次生成今日报告或盘中报告都会卡住**，页面长时间无响应。

PM 通过日志分析定位根因：`ak.stock_fund_flow_individual()`（同花顺全市场资金流向接口）调用无超时保护，当同花顺服务器不响应时无限阻塞，导致报告生成线程 hang 死。

**日志实证**：
- 2026-08-05 app.log：11:46:28 发起请求后日志空白 18+ 分钟，报告线程未恢复
- 对比 2026-08-03 正常路径：该接口 7 秒内返回 5197 只股票数据

### 1.2 关键代码位置（评审必读，请独立 Read 核验）

| 位置 | 说明 |
|---|---|
| `modules/data_collector.py` L1094-1098 | THS 常量块（缓存 TTL、失败计数、失败阈值） |
| `modules/data_collector.py` L1120-1168 | `_fetch_capital_flow_ths_batch()` — **本批次核心改动函数** |
| `modules/data_collector.py` L1171-1182 | `_try_ths_primary()` — 主接口调用（任务书不改此函数） |
| `modules/data_collector.py` L1185-1211 | `_try_ths_rank_backup()` — 备选接口调用（任务书不改此函数） |
| `modules/data_collector.py` L1342-1372 | `fetch_capital_flow_batch()` — 调用方（任务书不改此函数） |
| `modules/daily_report.py` L475-482 | 批量预取调用入口（任务书不改） |
| `modules/daily_report.py` L531-541 | 单只股票超时保护模式（`ThreadPoolExecutor + future.result`）— **任务书参考的先例** |
| `config.py` L113-117 | `STOCK_TIMEOUT_SECONDS=90` / `BATCH_TIMEOUT_SECONDS=1800` |

### 1.3 PM 任务书核心方案

PM 提出在 `_fetch_capital_flow_ths_batch()` 中用 `ThreadPoolExecutor(max_workers=1) + future.result(timeout=60)` 包装 THS 接口调用，超时后返回 `None`，走现有降级链路（备选接口 → 失败计数 → EM 回退）。

新增常量 `_THS_REQUEST_TIMEOUT = 60` 放在 data_collector.py THS 常量块内（不放 config.py）。

---

## 二、评审决策点（请逐项裁定）

### A-1：超时保护方案选型（核心）

PM 方案用 `ThreadPoolExecutor(max_workers=1) + future.result(timeout=N)` 包装 akshare 调用。

**架构师请核验**：
- Read `data_collector.py` L1120-1168，确认 `_fetch_capital_flow_ths_batch()` 当前确实无超时保护
- 确认 `ThreadPoolExecutor + future.result(timeout=N)` 方案在 Windows + Python 3.12 下是否可行（线程内 akshare HTTP 调用超时后线程是否能被正确回收）
- 评估是否有更简洁的替代方案（如 `requests` 层 timeout、`signal.alarm` 等）
- 注意：`signal.alarm` 在 Windows 不可用；akshare 内部 HTTP 库（requests）的 timeout 是否可通过参数传递

**裁定**：采纳 / 修改 / 否决 + 理由

### A-2：超时阈值 60 秒是否合理

PM 依据：正常路径约 7 秒（8/3 日志），60 秒留 8 倍余量。

**架构师请核验**：
- 60 秒是否过长（用户等待体验）
- 60 秒是否过短（网络波动误杀）
- 是否应考虑动态超时或分级超时（主接口 vs 备选接口）
- 注意：主接口失败后有 5 秒等待 + 重试，备选接口再调用，最坏情况总时长 = 60 + 5 + 60 + 60 = 185 秒，是否可接受

**裁定**：采纳 60s / 修改为 Ns + 理由

### A-3：超时常量放置位置

PM 方案：放在 `data_collector.py` THS 常量块（L1094-1098 区域），不放 config.py。理由是 THS 超时是内部实现细节，零代码用户不需要在 config.py 中看到。

**架构师请核验**：
- 是否应与 `STOCK_TIMEOUT_SECONDS` / `BATCH_TIMEOUT_SECONDS` 统一放在 config.py
- 还是按 PM 方案放模块内部（与 `_THS_CAPITAL_CACHE_TTL` 等同级）

**裁定**：采纳（放 data_collector）/ 修改（放 config.py）+ 理由

### A-4：`_call_with_timeout` 函数设计

PM 方案在 `_fetch_capital_flow_ths_batch()` 内部定义 `_call_with_timeout(fn, label)` 闭包辅助函数。

**架构师请核验**：
- 闭包设计是否合理（vs 模块级函数 vs 内联代码）
- `from concurrent.futures import ThreadPoolExecutor as _Pool, TimeoutError as _FutTimeout` 放在函数内部是否合适
- 线程池 `with` 上下文退出时，超时未完成的线程是否会被正确清理（Python 线程无法被强制 kill，线程池退出时 daemon 线程行为）

**裁定**：采纳 / 修改 / 否决 + 理由

### A-5：线程泄漏风险评估（重要）

`ThreadPoolExecutor` 超时后 `future.result(timeout=N)` 抛 `TimeoutError`，但底层线程仍在运行（akshare 的 HTTP 请求仍在等待）。`with` 块退出时线程池调用 `shutdown(wait=False)`，daemon 线程会在主进程退出时被回收，但若 app.py 长期运行，这些"僵尸线程"是否累积。

**架构师请核验**：
- 评估线程泄漏的实际影响（每次报告生成最多 3 次 THS 调用，每天触发频次低）
- 是否需要额外的线程清理机制
- 确认 `ThreadPoolExecutor(max_workers=1)` 的 `with` 块退出行为

**裁定**：可接受 / 需要额外处理 + 理由

### A-6：降级链路完整性

PM 方案声称超时后返回 `None`，与现有失败路径完全一致，后续降级链路不受影响。

**架构师请核验**：
- Read `_fetch_capital_flow_ths_batch()` L1158-1168，确认返回 `None` 后的后续逻辑：
  - `_THS_CONSECUTIVE_FAIL_COUNT += 1` 是否会正确执行（超时 ≠ 抛异常，需确认代码路径）
  - `fetch_capital_flow_batch()` L1372 调用方拿到 `None` 后是否正确回退到 EM 逐只采集
- 确认超时后 `_THS_CAPITAL_CACHE` 不会被写入（缓存只在 `df is not None` 时写入）

**裁定**：确认完整 / 存在缺陷 + 详情

### A-7：范围与红线确认

任务书第五节红线：功能红线（不得永久阻塞）、范围红线（仅 data_collector.py）、签名红线、零代码约束、降级安全红线。

**架构师请核验**：
- 红线是否完备
- 是否有遗漏的风险点
- 改动范围是否过窄（是否应同时处理 daily_report.py 批量预取调用层的超时包装）

**裁定**：完备 / 需补充 + 详情

### A-8：是否需要同步处理其他裸 akshare 调用

`data_collector.py` 中可能有其他 akshare 接口调用同样缺少超时保护。

**架构师请核验**：
- grep `ak.` 在 data_collector.py 中的所有调用点
- 评估是否有其他高频/高风险调用需要同步加超时
- 裁定：本批次仅处理 THS / 同步处理其他 + 列表

**裁定**：仅处理 THS / 需扩展 + 列表

---

## 三、交付物要求

`docs/reviews/review_019I_ths_batch_timeout_20260805.md`，含：

1. **逐决策点裁定**（A-1 ~ A-8，每项采纳/修改/否决 + 理由）
2. **独立核验的代码证据**（关键结论须附 Read 到的代码行号和内容）
3. **新发现的风险项**（R-x 编号，如有）
4. **评审结论**（通过 / 有条件通过 / 不通过）
5. **若裁定需修订任务书**，明确列出修订项（M-x 编号），PM 将据此修订任务书后交付开发

---

## 四、PM 备注

1. **本批次 PM 未越权评审**：PM 仅完成了日志分析和根因定位（排查报告已在对话中），未自行产出 review 文档。架构师请以完全独立视角评审。
2. **根因已由日志实锤**：app.log L186-188 的 18 分钟空白期 + 对比 8/3 正常路径 7 秒返回，根因（THS 接口 hang 住 + 无超时保护）已确认。架构师评审重点是**方案选型和风险评估**，而非根因复核。
3. **紧迫性**：当前 app.py PID 46172 的报告生成功能仍处于阻断状态，用户无法正常生成今日报告/盘中报告。建议架构师尽快完成评审，以便开发执行。
4. **线程泄漏是核心风险点**（A-5）：PM 方案用 `ThreadPoolExecutor` 包装超时，但 Python 无法强制 kill 线程，超时线程会继续运行直到 akshare HTTP 调用自然超时或进程退出。架构师需重点评估这在长期运行的 Flask 进程中是否可接受。
