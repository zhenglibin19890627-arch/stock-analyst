# 架构师评审报告 — 019G（同花顺交易日校验 + 报告时间展示优化）

**评审对象**：`docs/tasks/dev_tasks_20260804_019G_time_display_ths_skip.md`
**评审人**：架构师
**评审日期**：2026-08-04
**评审方式**：代码核验（data_collector.py / app.py / templates/index.html / daily_report.py）

---

## 〇、评审结论

### ⚠️ 有条件通过

- 任务 1（THS 交易日校验）、任务 2（删除评级时间行）：**方案安全，通过**。
- 任务 3（看板新增"生成时间"列）：**数据源前提不成立**——任务书声称 `watchlist-scores` 的 `stocks[].generated_at` 已存在，经代码核验**不属实**（引用行号属于另一接口）。需按 M-1 修订任务书后方可进入开发，否则验收标准第 3 条必然不通过。

**放行条件**：任务书按 M-1/M-2 修订后放行（此为本评审的强制前置项）。

---

## 一、评审要点覆盖

### 1. 交易日校验方案安全性和消费方兼容性 ✅（附 2 处修订建议）

**方案安全性**：
- 任务书代码片段 `datetime.now(_CN_TZ)` / `_CN_TZ` 在 `data_collector.py` 中**已就位**：L24 导入 `datetime`，L29 模块级定义 `_CN_TZ = timezone(_td(hours=8), ...)`。无需新增导入，零新依赖成立。
- 非交易日早退位于 `fetch_capital_flow_batch` 入口（L1342），早退前不产生任何 THS 请求与 DB 写入，满足"不写脏数据"目标；`success_count=0/fail_count=0` 语义与现有空列表分支（L1356-1357）一致。
- 周末跳过同时跳过 019E 补采触发点（补采逻辑位于 THS 成功路径之后，L1440-1488），但日报逐只路径 `_process_single_stock`（daily_report.py L374）仍会调用 `collect_stock_data` → `fetch_capital_flow`（EM 逐只），**主力资金面主链路不受影响**，仅 `ths_net_inflow` 辅助指标在周末不写入（正是修复目标）。见 R-2。

**消费方兼容性（核验全部调用方，共 2 处）**：
| 调用方 | 位置 | 对返回值的处理 | 兼容性 |
|---|---|---|---|
| app.py batch-analyze | L1298-1299 | 仅 `print` 整个 dict，外层 try/except | ✅ 兼容，`skipped` 无影响 |
| daily_report.py 日报批次 | L479-482 | 仅 `logger.info` 整个 dict，外层 try/except | ✅ 兼容，`skipped` 无影响 |

- 两处均不访问 `success_count/fail_count/source` 等键，新增 `skipped/reason` 键零风险；且均有异常兜底。
- 全仓 grep 无第三处调用方。

**问题**：
- 任务书注"`from config import _CN_TZ  # 已有导入`"不准确——`_CN_TZ` 实际为 data_collector.py 模块内定义（L29），非从 config 导入。代码本身可运行，仅文档表述需修正。
- 新早退返回值 `{'success_count':0, 'fail_count':0, 'skipped':True, 'reason':...}` **缺少 `source` 键**，与函数内其余 4 个返回点（均含 `source`）形状不一致；其余返回点也不含 `skipped` 键。当前无消费者读取这些键，无即期风险，但建议统一契约。见 M-2。

### 2. 删除评级时间行是否有副作用 ✅ 无副作用

**代码核验**：
- L4203 目标行删除后，剩余"报告生成于"（L4204）与"最新收盘"（L4205-4208）为独立 div，互不依赖。
- CSS：`.rating-time` 规则（L687 `.score-card .rating-time`）仍被 L4204/L4206/L4214/L4243 使用，删除一行不影响样式；该 div 无 id、无 JS 选择器引用（grep 确认无 `querySelector('.rating-time')` 类代码）。
- **全仓 rating_date 使用点**（其余 3 处前端 + 后端）均独立于 L4203：
  - `index.html` L2347（评分演示卡"评级日期"）— 独立数据源；
  - `index.html` L2635（持仓评级列表 `rating_time` 兼容字段）— 独立列表；
  - `index.html` L5666（回测表格 `r.rating_date`）— 独立数据源；
  - `app.py` L1091（API 仍返回 `rating_date`）— 保留无副作用，删除前端行即可。
- 后端字段不删除（api 返回与 DB 结构不动），零后端风险。

### 3. 看板新增列的数据源完整性和布局影响 ❌ 数据源不成立（强制修订项）

**数据源核验（任务书引用有误）**：
- 任务书称"`watchlist-scores` 返回的 `stocks[].generated_at` 已存在（app.py L1456 SELECT 含 `dr.generated_at`，L1485 `row['rating_time'] = row.get('generated_at', '')`）"。
- **核验结果**：L1456/L1485 属于**评级列表接口** `api_ratings`（约 L1414-1508，SELECT 确实含 `dr.generated_at`，并映射 `rating_time`），**不是**看板数据源。
- 看板数据源 `/api/portfolio/watchlist-scores`（app.py L1838-1991）：
  - 主查询 SELECT（L1866-1871）**不含 `dr.generated_at`**（仅 engine_version/total_score/rating/rating_label/score_change/prev_score/key_factors）；
  - `stocks[]` 字典（L1948-1974）**无 `generated_at` 键**（含无报告降级分支 L1888-1901 亦无）；
  - 仅顶层 `result['generated_at']`（L1979）为批次级 MAX，非每股级。
- 若按任务书现状开发，`st.generated_at` 恒为 undefined → `_fmtGenTime` 防御性返回 `'—'`，**整列全空**，验收标准第 3 条（每行显示对应股票时间）必然失败。且看板头部 L4740 已显示批次级"生成时间"，无每股数据时新增列纯属冗余重复。
- **结论**：任务 3 的"数据源已就位、app.py 不碰"前提错误，存在任务书内部矛盾（§三 范围声明"app.py 不碰" vs §三 任务 3 数据源引用）。**必须修订任务书**：要么把 app.py 的 SELECT+stocks 字典扩展纳入范围（3 文件），要么改用批次级时间（降低价值）。

**布局影响**：
- 表头（L4798 较昨日 → L4799 行业之间）与行（L4844 → L4845 之间）同步插列后列数 8→9 一致，`dashRenderTable` 遍历渲染不受列序影响。
- `dashSort`（L4991-5029）按数据对象字段排序（name/score/change/mv），不依赖 DOM 列索引，新列无 onclick 排序 → **无需适配**。
- `dashApplyFilter`（L4973-4988）按数据属性过滤，**无需适配**。
- `_fmtGenTime`（L5401）已存在（019D 交付），对 undefined/null/非字符串返回 `'—'`，含空值兜底。✅
- 表宽 100%、固定 padding，新增一列不影响布局结构；建议新单元格加 `white-space:nowrap` 防窄屏换行（见 M-3）。

### 4. 范围收敛性 ⚠️ 名义 2 文件，实际任务 3 需 3 文件

- 任务 1：仅 `modules/data_collector.py` ✅
- 任务 2：仅 `templates/index.html` ✅
- 任务 3：按 M-1 修订后需 **`app.py`**（watchlist-scores SELECT + stocks dict 扩展）+ `templates/index.html`，与任务书"app.py 不碰"红线冲突 → 需任务书修订为 3 文件或降级方案。

### 5. 签名/兼容性 ✅

- `fetch_capital_flow_batch(a_stock_symbols)` 签名不变 ✅（019E 验收已固化）。
- 返回值仅新增键、不删键，2 个消费方仅打印/日志，兼容 ✅。
- 不引入新依赖（`weekday()` 内置），requirements.txt 维持 9 包 ✅。
- `analysis_engine/adapter/advisor/scoring_engine/db_manager/daily_report` 均不需改动 ✅。

---

## 二、修改建议（编号 M-1 …）

### M-1（强制，阻塞放行）修订任务书任务 3 的数据源方案
任务书 §三 任务 3 的"数据来源确认"引用错误（L1456/L1485 属评级列表接口，非 watchlist-scores）。二选一修订：
- **方案 A（推荐，推荐修改）**：任务范围扩展至 3 文件，在 `app.py` `api_portfolio_watchlist_scores` 中：
  1. 主查询 SELECT（L1866-1871）增加 `dr.generated_at`；
  2. `stocks[]` 字典（L1948-1974）增加 `'generated_at': r.get('generated_at')`；
  3. 同步"无报告降级分支"（L1888-1901）置 `NULL as generated_at`；
  4. 任务书"明确不改范围"清单删除"app.py 不碰"条目，验收标准第 5 条同步删除 app.py 零改动要求。
- **方案 B**：若坚持 2 文件，任务 3 降级为"每行渲染批次级 `_dashData.generatedAt`"（数据源为顶层 `result.generated_at`，L1979），但会与看板头部 L4740 已显示的批次时间重复，价值低；验收标准第 3 条应改为"各行显示批次生成时间"。

另：即使按方案 A 实现，无报告股票（`has_report=false`）的 `generated_at` 为 NULL → 显示 `'—'`，验收标准第 3 条应明确"空值显示 '—'"的判定口径。

### M-2 统一 `fetch_capital_flow_batch` 返回值契约（建议，非阻塞）
- 新早退分支补 `'source': '同花顺批量(非交易日跳过)'`，与其余返回点形状对齐；
- 建议（可选）其余返回点统一补 `'skipped': False`，形成稳定契约，避免未来消费者歧义；
- 任务书代码片段注释 `from config import _CN_TZ # 已有导入` 更正为"模块内已定义（L29）"；
- 同步更新函数 docstring 的 Returns 说明（含 skipped 语义）。

### M-3 看板新列渲染细节（建议）
- 新"生成时间"单元格（`font-size:12px;color:#666`）建议追加 `white-space:nowrap;`，防窄屏时间文本换行破坏行高；
- 表头新列建议保留与其他列一致的 `padding:10px;border-bottom:2px solid #ddd;` 样式（任务书已含）。

### M-4 QA 构造周末场景的测试建议（建议）
- 验收标准第 1 条"构造 weekday()=6 场景"需在测试中 monkeypatch `modules.data_collector.datetime`（模块内引用的是类对象，可 `monkeypatch.setattr('modules.data_collector.datetime', Fake)`），注意该模块其余函数（如 `now_cn`、L1359 的 `today_str`）也引用同一 `datetime`，monkeypatch 需控制作用域或让 Fake 仅拦截 `now()`；
- 建议新增对返回 dict 含 `skipped=True` 且不触发 THS 请求的断言（可用 `_fetch_capital_flow_ths_batch` 桩验证未被调用）。

---

## 三、风险项（编号 R-1 …）

### R-1（高，阻塞任务 3 验收）`stocks[].generated_at` 不存在
看板数据源 watchlist-scores（app.py L1838-1991）的 SELECT 与 stocks 字典均无 `generated_at`；任务书引用 L1456/L1485 为评级列表接口。按任务书原样开发则新列全空"—"，验收失败。→ 依 M-1 修订后消除。

### R-2（低，可接受）周末跳过同时跳过 019E 补采触发
019E 补采（EM 逐只补 `main_net_inflow`）内嵌于 THS 成功路径之后（L1440-1488），周末早退使该补采在非交易日不触发。影响：周末日报批次中缺失 `main_net_inflow` 的股票无法经此入口补采。缓解：日报逐只路径 `collect_stock_data`（daily_report.py L374）仍执行 EM 采集，主力资金面主链路不受影响；下个交易日批次自动补采。建议在任务书"数据安全红线"中补充说明，并在日志（`logger.info`）中明确"非交易日跳过（含补采）"。

### R-3（低）watchlist-scores 的 ETag 缓存与新列交互
依 M-1 方案 A 增加每股 `generated_at` 后，该字段进入 `etag_payload`（L1985 仅排除顶层 `generated_at`），报告重生成后 ETag 随之变化 —— 属正确行为（新报告 → 新时间 → 刷新缓存），无风险，仅提示 QA 回归时注意 304 与字段变化的一致性。

### R-4（低）工作日法定节假日仍执行 THS
任务书已声明接受（约束条款），属已知可接受范围，无新增风险；若后续要求精确可升级节假日判断（不在本批次）。

---

## 四、评审结论汇总

| 评审项 | 结论 | 依据 |
|---|---|---|
| 任务 1 交易日校验（安全+消费方兼容） | ✅ 通过（附 M-2 契约建议） | 调用方仅 print/logger，均有 try/except；`_CN_TZ`/`datetime` 已就位 |
| 任务 2 删除评级时间行（副作用） | ✅ 通过 | 无 JS/CSS 依赖，其余 rating_date 使用点独立 |
| 任务 3 看板新增列（数据源+布局） | ❌ 数据源不成立，需修订 | watchlist-scores 无每股 generated_at；布局/排序/筛选无需适配 |
| 范围收敛性 | ⚠️ 需由 2 文件扩至 3 文件（方案 A）或降级（方案 B） | 与"app.py 不碰"红线矛盾 |
| 签名/兼容性 | ✅ 通过 | 签名不变、返回只增键、零新依赖 |

> **架构师结论**：任务 1、2 方案成熟可直接开发；任务 3 必须先按 M-1 修订任务书（推荐方案 A：app.py 增加 `dr.generated_at` 的 SELECT 与 stocks 字段，范围扩至 3 文件）后方可放行。请监理裁定。
