# DEV-TASKS-20260804-019E-ARCH：019E 资金面批量补采正向触发 + 估算兜底展示与 EM 覆盖重写 — 架构方案评审任务书

> **签发人**：PM  | **签发日期**：2026-08-04 | **状态**：待架构师执行

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：架构师

**职责边界**：
- 评审 PM 签发的 019E 开发任务书 v1（`docs/tasks/dev_tasks_20260804_019E_capital_fallback.md`），聚焦数据标记方案、评分纯净隔离与触发机制裁定
- 对每个决策点给出明确裁定（采纳/修改/否决）+ 理由
- **不编码、不验收、不写功能代码**
- 交付物：`docs/reviews/review_019E_capital_fallback_20260804.md`

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
| 前序批次 | 018：主力净流入唯一来源=东方财富逐只，THS 批量仅写辅助指标 ths_net_inflow，估算源 if False 硬禁用；019C：EM 回退六项机制（错峰/分批/退避/冷却/熔断/软超时）已双签关闭 |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 推荐模型 | **kimi k3**（评审类任务） |
| 窗口类型 | **Quests 独立窗口** |
| 执行模式 | 单代理 agent |
| 交付物 | `docs/reviews/review_019E_capital_fallback_20260804.md` |

---

## 一、需求背景

### 1.1 事故与用户指示（2026-08-03）

1. **事故**：08-03 旧代码 THS 污染 → 29 只全跳过 EM → 日报资金面评分失真；切新代码后 `fetch_capital_flow_batch` 在 THS 成功时不回退 EM，19 只 `main_net_inflow` 持续 NULL 无补采机会；PM 手动补采又被深夜风控压制，最终 14 只缺失
2. **监理裁定③**：签发任务书，`fetch_capital_flow_batch` 增加"当日 main 缺失时补采 EM 逐只"正向触发机制（复用 019C 六项机制）
3. **用户新指示**：「资金面东方财富获取失败的时候采用新浪/腾讯/网易获取，等什么时候东方财富能够获取到数据再重新写入」；监理裁定补充：**估算仅兜底展示，不参与评分**

### 1.2 关键代码位置（评审必读，行号为签发时快照）

| 位置 | 说明 |
|---|---|
| `data_collector.py` L1795 `fetch_capital_flow` | EM 三层降级主链路；L1819-1832 前置校验（同日 main NOT NULL → 跳过）；L2028/L2069/L2104 三处 `if False` 估算源死代码（腾讯/新浪/网易） |
| `data_collector.py` L1233-1349 | 019C：THS 批量失败（df is None）才回退 EM 逐只，六项机制常量 `_EM_*` 在模块顶部 |
| `data_collector.py` L1400-1424 | THS 成功时仅 UPDATE `ths_net_inflow` + INSERT OR IGNORE 占位行 |
| `data_collector.py` L280-302 `save_data_status` | 先删后插，同维度同日仅留最新一条 |
| `scoring_engine.py` | 评分引擎读取 raw_capital_flow 处（**请评审时全量盘点读取入口**） |
| `raw_capital_flow` 表 | 现有列含 stock_id/trade_date/main_net_inflow/main_net_inflow_pct/ths_net_inflow 等，**无 is_estimated 列** |

### 1.3 历史约束（评审必读）

新浪/腾讯/网易三源被禁用的原因是**无真实主力资金数据**，只能"成交额×涨跌幅"估算，估算值与真实主力净流入无相关性（P3-A 验收结论）。本批次恢复三源的唯一用途是兜底展示——**评分纯净是本批次最高风险点**。

---

## 二、评审决策点（请逐项裁定）

### E-1：估算数据标记方案
任务书 2.1 建议：`raw_capital_flow` 新增 `is_estimated INTEGER NOT NULL DEFAULT 0`（ALTER TABLE）。备选：(a) 采纳；(b) 复用/新增 `data_source` 类文本字段标记来源（如 'estimated_sina'）；(c) 其他。请权衡存量数据兼容、读取侧过滤复杂度、未来扩展性后裁定。

### E-2：补采正向触发的插入位置与去重
任务书任务 1 建议：`fetch_capital_flow_batch` THS 流程之后统一查询"当日 main 为 NULL 清单"再补采。请裁定：①THS 失败走 019C 原回退路径时，回退循环与补采循环的执行关系（回退已采集的是否天然不进补采清单？是否需要显式去重？）；②补采清单查询条件（NULL 判定 vs data_status='estimated' 判定，两者是否合并为一条 SQL）；③该机制对 app.py batch-analyze 路径（与日报共用 fetch_capital_flow_batch，见 019C 评审 R-5）同时生效是否可接受。

### E-3：评分纯净隔离的读取入口盘点
任务书 2.5 仅提及 scoring_engine.py。请**全量盘点** raw_capital_flow 的读取入口（评分引擎、advisor.py 数据组装、app.py 展示接口、daily_report.py 等），逐一裁定是否需要过滤 `is_estimated=1`，并明确：展示类读取（任务 2.6）允许读估算行、评分类读取必须过滤——两类入口的边界清单。

### E-4：EM 恢复后是否 force 重生成当日日报
任务书任务 3 第 3 点。PM 倾向"不自动重生成"（当日日报已按 T-1 真实数据评分属可接受口径；且自动重生成涉及 B11-REPORT-REUSE 语义变更）。请裁定：(a) 不自动重生成（PM 倾向）；(b) 检测到"估算→真实"升级时自动 force 重生成该股票当日日报；(c) 仅在页面提示用户手动刷新。

### E-5：data_status 新增 'estimated' 状态的兼容性
现有消费方对 status 的判定（如 019C 熔断计数 `result[0]=='success'`、前置防覆盖校验 message 前缀判定、前端/API 展示 status 处）是否兼容第三态 'estimated'？请逐一核查并给出需要适配的位置清单。

### E-6：前端"估算"标注范围
任务书 2.6。请裁定标注的具体展示点（报告详情资金面数值旁 / 数据状态提示处 / 其他）与措辞，坚持最小改动原则（零代码用户可读、不引入新组件）。

### E-7：范围与红线确认
任务书第五节 6 条红线是否完备？重点核查：①估算源 `_fetch_capital_flow_sina/_tencent/_netease` 死代码复活后的异常处理（网络失败不得阻塞主流程）；②ALTER TABLE 的执行时机（app 启动时幂等迁移 vs 开发手工执行——零代码用户约束下必须自动化）；③估算只写当日 1 行与现有 INSERT OR REPLACE 按 (stock_id, trade_date) 唯一键的语义冲突检查。

---

## 三、交付物要求

`docs/reviews/review_019E_capital_fallback_20260804.md`，含：
1. 逐决策点裁定（采纳/修改/否决 + 理由）
2. 新发现的风险项（R-x 编号）
3. 对任务书的具体修订点清单（若有）
4. 评审结论（通过 / 有条件通过 / 不通过）

---

> **PM 备注**：本批次涉及 schema 变更（新增列）、估算源复活与评分引擎读取链路，风险面高于 019D，请重点裁定 E-1/E-3/E-5/E-7②。架构师请在 Quests 独立窗口以本任务书全文作为启动提示词执行。
