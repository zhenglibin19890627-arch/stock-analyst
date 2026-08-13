# QA 抽查报告 019Q-S1 — 补采清单语义注释修订（纯文字，零功能改动）

**批次**：019Q-S1（019Q 收尾小任务，P3，QA 观察项 1 裁定落实）
**角色**：QA 抽查工程师（独立验收）
**抽查日期**：2026-08-09
**任务书**：`docs/tasks/dev_tasks_20260809_019Q-S1_comment_fix.md`
**开发自验报告**：`docs/reports/dev_selftest_019Q-S1_comment_fix_20260809.md`
**抽查结论**：✅ **通过**（4/4 项验收标准满足，1 条低危观察项登记）

---

## 一、抽查范围与方法

按任务书第四节验收标准逐项核验：

| # | 验收标准 | 核验方法 |
|---|---|---|
| ① | 两处文字与实际 SQL 语义一致 | 逐字比对现文 vs 任务书二节目标文字；对照 `data_collector.py` L1799-1805 补采清单 SQL 真值语义；回读 019Q QA 报告（观察项 1 / F9）作背景佐证 |
| ② | 改动仅注释/docstring，无功能代码变更 | 仓库基线仅 1 个初始提交（`git log` 实证 a22d291），无法 git diff 隔离批次 → 采用"目标区域阅读 + 周围代码完整性"方式：目标区域逐行定性、周边可执行代码结构完整性检查、全仓旧文案残留检索 |
| ③ | 编译 + pytest 回归通过 | `python -m py_compile` 两文件 + `python -m pytest tests/` 全量复跑 |
| ④ | 开发报告输出到 docs/reports/ | 文件存在性 + 内容自洽性核验 |

## 二、验收标准 ①：注释现文 vs 补采清单 SQL 真实语义

### 事实来源（唯一基准）：`modules/data_collector.py` L1799-1805 补采清单 SQL

```sql
SELECT 1 FROM raw_capital_flow WHERE stock_id=? AND trade_date=?
  AND main_net_inflow IS NOT NULL
  AND (is_estimated = 0 OR is_estimated IS NULL)
  AND (capital_source IS NULL OR capital_source NOT IN ('ths_total','sina_main'))
```

**谓词真值分析**：某行被计为"已有真实数据"（即**不进**补采清单）⇔ ① `main_net_inflow IS NOT NULL` ② 非估算 ③ `capital_source IS NULL` 或不属于 `{'ths_total','sina_main'}`。

- `capital_source='sina_main'` 行：条件 ③ 不满足（NOT IN 命中外层 AND）→ **仍进补采清单** ✅
- `capital_source='ths_total'` 行：条件 ③ 不满足 → **仍进补采清单** ✅
- `capital_source IS NULL`（东财）且非估算：条件满足 → 视为已完成，不进清单 ✅

### 现文 vs 任务书目标文字比对（两处均逐字一致）

**任务 1**（`modules/data_collector.py` L1787-1790，四行 `#` 注释）：
```
# 019Q Task 3（M-5）：补采清单 SQL 扩为 NOT IN ('ths_total','sina_main')。
# 语义：只有东财真数据（capital_source IS NULL 且非估算）才算"已完成"；
# sina_main / ths_total 行仍进入补采清单 —— 东财 30 分钟内恢复时可覆盖回补
# （"东财恢复后自动回补"的实现），新浪重采不降级已有数据（019Q QA F9 实证）。
```
与任务书二节任务 1 目标文字**逐字一致** ✅；"sina_main / ths_total 行仍进入补采清单"与 SQL 谓词真值分析完全吻合 ✅。

**任务 2**（`modules/daily_report.py` L176-179，`_capital_retry_once` docstring 内）：
```
fetch_capital_flow_batch(a_symbols)——复用 019E 补采清单入口：只有东财真数据
（capital_source IS NULL 且非估算）才算"已完成"；sina_main / ths_total 行仍
进入补采清单 —— 东财 30 分钟内恢复时可覆盖回补（"东财恢复后自动回补"的实现），
新浪重采不降级已有数据（019Q QA F9 实证）。异常隔离仅记日志。
```
语义与任务 1 完全一致（sina_main 行**仍在**补采清单、东财恢复可覆盖回补）✅；docstring 其余部分（锁超时防并发、异常隔离仅记日志）与函数体行为相符，未被破坏。

**背景佐证**：019Q QA 报告 `qa_accept_019Q_sina_capital_fallback_20260809.md` 观察项 1（L108-111）实证"补采清单谓词语义为 sina_main 行不计入已有真实数据 → 仍进入补采清单"，与现文一致；F9（L77）实证新浪重采不降级 THS/估算数据，与注释中"新浪重采不降级已有数据（019Q QA F9 实证）"引用一致。

**结论**：标准 ① ✅ 通过。

## 三、验收标准 ②：仅注释/docstring，无功能代码变更

**git 基线核验**：`git log --oneline` 仅 `a22d291 初始化: 工作区版本控制基线` 1 个提交；工作区存在大量先前批次（019E/019K/019Q 主任务）未提交改动，`git diff` 无法隔离 019Q-S1 批次 → 确认采用"目标区域阅读 + 周围代码完整性"替代方法（与开发报告方法一致，抽查方独立复读确认）。

### 3.1 目标区域逐行定性

**区域 1：`modules/data_collector.py` L1786-1790**
- L1786 `# 补采清单生成（评审 E-2 裁定）`：`#` 注释行
- L1787-1790 四行：全部为 `#` 注释行，内容与任务书目标文字一致
- L1791 起 `supplement_symbols = list(a_stock_symbols)`：可执行代码，紧接注释块后，结构完整

**区域 2：`modules/daily_report.py` L172-180**
- L172-180：位于 `_capital_retry_once` 三引号 docstring 内（L171 `def` 后首行 `"""` 起），全部为文档字符串文字
- L181 起 `if not _generate_lock.acquire(timeout=5):`：可执行代码，结构完整

### 3.2 周围代码完整性检查（无重复/断裂/异常）

| 检查项 | 结果 |
|---|---|
| `data_collector.py`：补采清单 try/except（L1792-1814）→ 清单分支返回（L1816-1826）→ 兜底返回（L1828），成对闭合 | ✅ 完整 |
| `_em_batch_collect`（L1532）被补采分支引用（L1821），`fetch_capital_flow_batch`（L1660）为唯一定义 | ✅ 各函数唯一定义 |
| `daily_report.py`：`_capital_retry_once`（L171 唯一定义）函数体 try/except/finally 成对闭合（L181-191）；`_schedule_capital_retry`（L194）、`_scheduler_tick`（L57）均唯一定义、结构完整 | ✅ |
| 全仓旧文案残留检索：`grep "sina_main 行被\|→ 幂等\|被排除" *.py` | ✅ **零残留**（旧错误表述未遗留在任何 .py 文件） |
| 语法层面：两文件 `python -m py_compile` 通过（见标准 ③） | ✅ 无缩进/引号破坏 |

### 3.3 开发报告 Diff 摘要复核

开发报告二节 diff 摘要（-2 +4 / -2 +3，任务 2 首行"复用 019E 补采清单入口"保留）与现文读回结果一致；报告中"仅编辑 2 文件、共 7 行注释/docstring 变更"与抽查复读相符，未发现报告中所述行数以外的注释/文字改动。

**结论**：标准 ② ✅ 通过。

## 四、验收标准 ③：编译 + pytest 回归（QA 独立复跑）

| 项 | 命令 | 结果 |
|---|---|---|
| 编译 | `python -m py_compile modules/data_collector.py modules/daily_report.py` | ✅ 无错误 |
| 回归 | `python -m pytest tests/ -q` | ✅ **343 passed, 1 warning**（urllib3 版本提示，既有，与开发报告一致） |

QA 独立复跑结果与开发自验报告完全一致（343/343）。

**结论**：标准 ③ ✅ 通过。

## 五、验收标准 ④：开发报告交付

`docs/reports/dev_selftest_019Q-S1_comment_fix_20260809.md` 存在，含改动清单（2 文件）、diff 摘要（逐行 前后对照）、自验结果（编译 + pytest + 语义核对 + 红线核对）四要素，满足任务书四节第 4 条。

**结论**：标准 ④ ✅ 通过。

## 六、观察项登记（低危，不阻塞）

**O-1（低危）**：现文"只有东财真数据（capital_source IS NULL 且非估算）才算'已完成'"为**简化表述**——SQL 谓词完整含 `main_net_inflow IS NOT NULL` 与 `capital_source NOT IN` OR 分支。经核对：① 数据模型中 capital_source 枚举仅 NULL/ths_total/sina_main 三值，OR 分支即 NULL 情形；② EM 写入路径必写 main_net_inflow；③ 该文字即任务书二节钦定目标文字，与 SQL 真值方向零矛盾、零误导。登记备查，无需整改。

## 七、抽查结论与签署

| 验收标准 | 结论 |
|---|---|
| ① 注释语义与 SQL 一致 | ✅ |
| ② 仅注释/docstring 变更 | ✅ |
| ③ 编译 + pytest 回归 | ✅（py_compile 通过，pytest 343/343） |
| ④ 开发报告交付 | ✅ |

**QA 抽查结论：✅ 通过（有条件无——全部通过，O-1 为知情登记）。建议 PM 双签后关闭 019Q-S1。**

| 角色 | 签署 |
|---|---|
| QA 抽查工程师 | ✅ 2026-08-09 |
| PM 双签 | ⏳ 待签 |

## 附：抽查证据链

- 任务书：`docs/tasks/dev_tasks_20260809_019Q-S1_comment_fix.md`
- 开发自验报告：`docs/reports/dev_selftest_019Q-S1_comment_fix_20260809.md`
- 019Q 主 QA 报告（观察项 1 / F9 依据）：`docs/reports/qa_accept_019Q_sina_capital_fallback_20260809.md`
- 目标代码现文：`modules/data_collector.py` L1786-1790、L1799-1805；`modules/daily_report.py` L171-191
- 回归证据：pytest 343 passed；py_compile 两文件通过；git log 仅 a22d291 单基线
