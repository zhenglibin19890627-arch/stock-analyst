# DEV-TASKS-20260803-019D-ARCH：019D 报告生成时间分钟级展示与三入口评分同源对齐 — 架构方案评审任务书

> **签发人**：PM  | **签发日期**：2026-08-03 | **状态**：待架构师执行

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：架构师

**职责边界**：
- 评审 PM 签发的 019D 开发任务书 v1（`docs/tasks/dev_tasks_20260803_019D_score_time_alignment.md`），聚焦口径裁定与边界场景决策点
- 对每个决策点给出明确裁定（采纳/修改/否决）+ 理由
- **不编码、不验收、不写功能代码**
- 交付物：`docs/reviews/review_019D_score_time_alignment_20260803.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 架构师仅做方案评审，不执行任何代码修改

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db` |
| 技术栈 | Python + Flask + SQLite + Jinja2 单页应用（templates/index.html 单文件 6159 行） |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包） |
| 前序批次 | 019A 已关闭：`_save_daily_report_for_advice` 统一回写 daily_reports 三表一致；013 已关闭：intraday 盘中快报不删除 daily，二者可同日并存 |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 推荐模型 | **kimi k3**（评审类任务） |
| 窗口类型 | **Quests 独立窗口** |
| 执行模式 | 单代理 agent |
| 交付物 | `docs/reviews/review_019D_score_time_alignment_20260803.md` |

---

## 一、需求背景

### 1.1 用户反馈

> 「分析报告点刷新和总览看板、每日报告的评分对不上，报告时间精确到分钟，方便核对是否报告不是同一时间生成的报告」

### 1.2 PM 现场核查结论（2026-08-03，行号为核查时快照）

| # | 缺陷 | 位置 | 现状 |
|---|---|---|---|
| 1 | 看板 JOIN 无 report_type/status 过滤 | `app.py` L1846-L1865 `api_portfolio_watchlist_scores` | `LEFT JOIN daily_reports dr ON s.id=dr.stock_id AND dr.report_date=?`，daily+intraday 并存时同股出 2 行；failed 行也参与 |
| 2 | report-latest 无 report_type 过滤 | `app.py` L912-L918 `api_get_report_latest` | 并存时 fetchone 命中任意一条 |
| 3 | 三入口无分钟级生成时间 | index.html L4195 / L4714 / L4561 / L4624 | 详情页"评级时间"实为 `rating_date`=K线数据日期（advisor.py L1318）；看板/日报仅显示日期 |
| 对照 | 已有正确口径 | `/api/ratings` L1430-1449、`get_latest_reports` L841-878 | daily 优先、无 daily 取 intraday、status='ok' |

### 1.3 相关写入语义（评审必读）

- `_save_report`（daily_report.py L228-L300）：daily 删当日全部（含 intraday）再插入；intraday 仅删旧 intraday 不动 daily → **daily 生成后 intraday 被清，反向不成立**
- `_save_daily_report_for_advice`（advisor.py L592-L682）：手动刷新/一键分析回写，已有 daily 行则 UPDATE（含 generated_at），无则先删当日全部再 INSERT daily
- `generated_at` 统一为 `datetime.now(_CN_TZ).isoformat()`（含时分秒微秒+08:00）
- `api_get_report_latest` 存在 B11-DETAIL-LOAD 路径：当日无报告时静默触发 `generate_advice` 并**直接返回引擎实时结果**（不经 DB 行）

---

## 二、评审决策点（请逐项裁定）

### D-1：读取口径裁定
三处读取（看板 JOIN / report-latest / MAX generated_at）统一采用"daily 优先、无 daily 取 intraday、status='ok'"，与 `/api/ratings` 和 `get_latest_reports` 完全一致。是否采纳？是否建议抽取为共享辅助函数（app.py 内）以避免第三套口径散落？

### D-2：看板重复行的历史脏数据处置
修复 JOIN 过滤后，**历史并存脏行被读取侧隔离即可**，不做数据清理（写入侧语义保证后续 daily 生成会清掉 intraday）。是否采纳"不迁移不清理"？还是建议一次性清理脚本？

### D-3：B11-DETAIL-LOAD 实时路径的 generated_at
当日无报告时 report-latest 静默触发分析并直接返回引擎结果，该响应**无 DB 行、无 generated_at**。裁定：(a) 在实时响应中补充 `generated_at = datetime.now(_CN_TZ).isoformat()`（app.py 端点内拼装，不改 advisor.py）；还是 (b) 前端显示"—"？PM 倾向 (a)。

### D-4：前端时间格式化实现
统一 `YYYY-MM-DD HH:MM`。候选：(a) ISO 字符串切片（`s.slice(0,16).replace('T',' ')`，零依赖、无时区换算风险）；(b) `new Date(s)` + 本地化格式。PM 倾向 (a)（generated_at 已带 +08:00，避免浏览器时区二次引入差异）。请裁定。

### D-5：日报列表"本批生成时间"取值
表头显示该批各行 generated_at 的 MAX（前端计算），行级列显示各自 HH:MM。生成汇总视图（renderDailyReport）的 results 无 generated_at 字段——裁定：(a) 后端 `generate_daily_report` 返回值追加 `finished_at` 字段（daily_report.py 读取侧返回值扩展，不改写入）；还是 (b) 生成汇总视图仅显示日期、引导用户点"查看最新报告"看分钟时间？PM 倾向 (a)（改动小且口径一致）。

### D-6：范围确认
改动限于 `app.py` + `templates/index.html`（若 D-5 选 a 则 + `modules/daily_report.py` 返回值一处）。红线 8 条（任务书第五节）是否完备？有无遗漏风险（如 ETag 行为变化、watchlist-scores 排序 CASE 表达式兼容性）？

---

## 三、交付物要求

`docs/reviews/review_019D_score_time_alignment_20260803.md`，含：
1. 逐决策点裁定（采纳/修改/否决 + 理由）
2. 新发现的风险项（R-x 编号）
3. 对任务书的具体修订点清单（若有）
4. 评审结论（通过 / 有条件通过 / 不通过）

---

> **PM 备注**：本批次为读取侧口径修复 + 前端展示层改动，无 schema 变更、无引擎触碰，风险面小；但涉及 4 处读取口径统一，请重点裁定 D-1/D-3/D-5。架构师请在 Quests 独立窗口以本任务书全文作为启动提示词执行。
