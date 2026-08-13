# DEV-TASKS-20260805-019L-ARCH：019L "刷新报告"时间显示修复（/advise 补 generated_at）— 架构方案评审任务书

> **签发人**：PM  | **签发日期**：2026-08-05 | **状态**：待架构师执行

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：架构师

**职责边界**：
- 复核 PM 签发的 019L 开发任务书（`docs/tasks/dev_tasks_20260805_019L_advise_generated_at.md`）
- 对每个决策点（A-1~A-5）给出明确裁定 + 理由
- **不编码、不验收、不写功能代码**
- 交付物：`docs/reviews/review_019L_advise_generated_at_20260805.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 架构师仅做方案评审，不执行任何代码修改
- PM 产出的任务书仅供参考，架构师须独立 Read 代码核验，不采信 PM 结论

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包） |
| 前序批次 | 019D（generated_at 链路）/ 019G（时间展示）；019N（并行批次，data_collector.py） |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 交付物 | `docs/reviews/review_019L_advise_generated_at_20260805.md` |

---

## 一、需求背景

### 1.1 缺陷描述

报告页"🔄 刷新报告"按钮后，"报告生成于"显示"—"；普通查看正常。

### 1.2 根因（PM 定位）

- 前端刷新按钮 → `loadReport(stockId, true)` → `/api/stocks/<id>/advise` 实时路径
- 前端 L4213 渲染 `adviseData.generated_at`，undefined → `_fmtGenTime` 返回 '—'
- 后端 `/advise` 端点（app.py L1117-1134）**未补 generated_at**（019D 只在 /report-latest 回退路径 L939-940 补过，此处遗漏）

### 1.3 关键代码位置（评审必读，请独立 Read 核验）

| 位置 | 说明 |
|---|---|
| `app.py` L1117-1134 | `api_advise_stock` — **本批次唯一改动端点** |
| `app.py` L939-940 | 019D 先例：/report-latest 回退路径补 `advice['generated_at'] = datetime.now(_CN_TZ).isoformat()` |
| `app.py` L902-905 | 019D 先例：函数内 import datetime/timezone/_td + _CN_TZ 定义 |
| `app.py` L860-869 | `/api/stocks/<id>/refresh` 全量刷新端点（同型遗漏，备查不处理） |
| `templates/index.html` L4185 | 刷新报告按钮 → `loadReport(stockId, true)` |
| `templates/index.html` L4213 | 渲染 `_fmtGenTime(adviseData.generated_at)` |
| `templates/index.html` L5412-5415 | `_fmtGenTime`：非字符串返回 '—' |

---

## 二、评审决策点（请逐项裁定）

### A-1：补字段位置与时机

PM 方案：`api_advise_stock` 成功分支（`result.get('success')`）内补 `result['generated_at']`。

**架构师请核验**：
- Read app.py L1117-1134，确认补在 success 分支（price_advice 之后）是否合理
- 失败分支不补是否与 /report-latest 语义一致（L929-941 对照）
- 是否有更合适的位置（如 generate_advice 内部——注意 B24 红线禁止改 advisor）

**裁定**：采纳 / 修改 / 否决 + 理由

### A-2：时间格式与时区一致性

PM 方案：`datetime.now(_CN_TZ).isoformat()`，_CN_TZ=东八区。

**架构师请核验**：
- Read 019D 先例 L939-940 / L902-905，确认格式完全一致（前端 `_fmtGenTime` slice(0,16) 兼容）
- 函数内 import 与函数顶部 import（app.py 是否已有 datetime 顶部导入）——避免重复导入或改用顶部
- 时间语义：实时重算时刻 vs DB 快照时刻——两者混用是否会引起用户困惑（刷新后时间变化是预期行为）

**裁定**：采纳 / 修改 + 理由

### A-3：是否一并修复 /refresh 端点

**背景**：`/api/stocks/<id>/refresh`（L860-869）同样未补 generated_at。

**架构师请核验**：该端点是否被前端使用（grep index.html 中 refresh 相关调用）；一并修复 vs 登记备查的裁定。

**裁定**：一并修复 / 维持备查 + 理由

### A-4：范围与红线确认

任务书红线：B24（不改 advisor）、范围（仅 app.py 一端点）、语义（失败不补）、零代码、并行隔离（不碰 data_collector.py）。

**架构师请核验**：
- B24 红线边界：本方案在端点层补字段，未触碰 generate_advice——确认合规
- 是否有遗漏风险（如 price_advice 生成失败时 generated_at 是否仍补）
- 与 019N 并行的文件隔离是否充分（app.py vs data_collector.py）

**裁定**：完备 / 需补充 + 详情

### A-5：验收标准充分性

任务书验收 5 条：代码核查、py_compile、mock 功能（success/失败两态）、前端联动（可选）、零改动。

**架构师请核验**：
- mock 调 `api_advise_stock` 的方式（Flask test_client vs 直接调函数）是否可行
- 是否需断言"price_advice 后补 generated_at 不覆盖既有字段"
- 是否需断言时间戳与当前时刻偏差（如 <60s）

**裁定**：充分 / 需补充 + 详情

---

## 三、交付物要求

`docs/reviews/review_019L_advise_generated_at_20260805.md`，含：

1. **逐决策点裁定**（A-1 ~ A-5，每项采纳/修改/否决 + 理由）
2. **独立核验的代码证据**（关键结论须附 Read 到的代码行号和内容）
3. **新发现的风险项**（R-x 编号，如有）
4. **评审结论**（通过 / 有条件通过 / 不通过）
5. **若裁定需修订任务书**，明确列出修订项（M-x 编号），PM 将据此修订任务书后交付开发

---

## 四、PM 备注

1. **本批次 PM 未越权评审**：PM 仅完成根因定位（前后端代码链）与同型先例（019D L939-940）确认，未自行产出 review 文档。
2. **低风险小批次**：单文件单端点单行级改动，评审重点为格式一致性（A-2）与范围收敛（A-3），预计快速通过。
3. **与 019N 并行**：019L 只动 app.py，019N 只动 data_collector.py，无文件重叠；两个架构评审可并行安排。
4. **用户视角**：修复后刷新报告显示"报告生成于：<当前时刻>"，与普通查看格式一致；时间变新是实时重算的预期行为，非缺陷。
