# 架构评审报告 — 019S 主力资金弃用同花顺（移除 ths_total 顶替）

| 项 | 内容 |
|---|---|
| 批次号 | 019S |
| 主题 | 主力净流入降级链路移除"同花顺全部资金口径顶替"（capital_source='ths_total'） |
| 评审人 | 架构师（只读评审，未改码未写库） |
| 评审日期 | 2026-08-09 |
| 依据 | 任务书 `docs\tasks\dev_tasks_20260809_019S_remove_ths_capital_fallback.md` 全文；`modules/data_collector.py`、`modules/daily_report.py`、`modules/data_adapter.py`、`modules/scoring_engine.py`、`modules/analysis_engine.py`、`modules/advisor.py`、`modules/alert_engine.py`、`templates/index.html`、`database/db_manager.py`、`tests/`、`scripts/`；生产库只读核验 |
| 监理前提 | 主力净流入数据来源链路不得使用同花顺数据（不可推翻） |

---

## 一、结论

**有条件批准（Conditional Approve）**

本次移除体量小（1 个写入块 + 注释修订 + 存量处置），方向与监理裁定一致，链路缩短后两条评分引擎均存在明确的"资金面缺位"容错路径（已逐条只读核实），不存在硬阻塞项。

**批准的前提条件（对应第四节附加约束）：**
1. 3 处 `'ths_total'` SQL 字面量（前置校验、补采清单、估算守卫）**全部保留不动**（R-3 裁定，理由见 §二）；不得以"代码整洁"提前简化，仅登记为条件性技术债。
2. 存量 27 行处置按**开放项 B 推荐方案（b）**执行，且必须：先备份 → 非交易时段运行 → 只读断言 27 行全命中、残余为 0。
3. 移除块后注释全量对齐两级阶梯（EM→sina_main→估算），并补充任务书未覆盖的 `index.html`、`db_manager.py` 注释点（§四约束 6）。

---

## 二、逐条风险裁定表

| 编号 | 风险项 | 裁定 | 证据 / 说明 |
|---|---|---|---|
| R-1 | 存量行口径（08-05/08-06 共 27 行曾参与当日评分与日报，追溯重算不在范围） | **通过** | 历史评分/评级/日报已固化于 analysis_results、ratings_history、daily_reports 等表，本批次不触碰。处置仅作用于 raw_capital_flow 原始行；处置后历史评分与新原始行不一致属 R-1 既定范围内（不追溯），接受登记。 |
| R-2 | 链路缩短代价：东财+新浪双失败日资金面因子缺位（估算不参评） | **通过（确认容错路径存在）** | 只读核实 4 条读取路径均过滤 `is_estimated=1`：`data_adapter._read_capital_data`(L273-290)、`advisor._build_capital_factors`(L1289-1337)、`analysis_engine._read_capital_data`(L123-132)、`alert_engine.check_capital_outflow`(L199-226)。缺位时不崩、链路不断：v5 引擎 `score_main_capital`(L766-788) None→中性 0.0；`score_north/margin_capital` 缺失返回 70/68；旧引擎空数据→dimension 'unavailable' weight=0、总评分在其余维度再分配(L1142-1160)。**附加观察（登记，非阻塞）**：v5 引擎 main_capital 缺位时中性 0.0 经 B18 校准映射到 **85 分**（非 50 真中性），即"缺位"在 v5 下表现为"中性偏正"，与旧引擎"权重归零"语义不同。此为 019E 既有设计（D02），非本批次引入，但移除 THS 后受影响日数扩大，请监理知悉。 |
| R-3 | 019Q 回补机制耦合（补采清单/防覆盖 NOT IN ('ths_total','sina_main')） | **缓解措施（裁定：3 处字面量全部保留）** | 逐处核对见下表。核心：存量 27 行仍在库，**前置校验(L2400)与补采清单(L1803)若删除 'ths_total'，存量行会被误判为"已有真实数据"，导致东财恢复后永不回补覆盖**；估算守卫(L2773/2811/2849)保留为防御性（估算仅写当日、存量全为过去日期，本不冲突，但保留成本≈0 且语义一致）。 |
| R-4 | B11 效应（非交易日浏览个股详情触发实时分析写库） | **通过** | 仅影响自测纪律，本批次无破坏性代码改动。流程约定见验收标准 5/6。 |
| R-5 | 辅助指标边界（ths_net_inflow 保留与否） | **待监理裁定（开放项 A）** | 建议**默认保留**，理由见 §三。 |
| R-6 | 注释腐化（018/019K/019Q 注释互相引用） | **缓解措施** | 已全文检索 `ths_total`/`同花顺`，非文档残留点与整改建议见 `§四-约束6`。历史归档文档（docs/reports、docs/reviews、docs/tasks）不整改。 |

### R-3 逐处核对（3 处 SQL 字面量）

| 位置 | 现状 | 处置 | 理由 |
|---|---|---|---|
| 前置校验 `data_collector.py` L2397-2401 `NOT IN ('ths_total','sina_main')` | 排除顶替行，使存量 ths_total 行**不**被计为"已有真实数据"→ 不跳过 EM 采集 | **必须保留** | 删除后存量 ths_total 行被计为真实（pre_cnt>0）→ 跳过采集 → 存量 27 行**永远无法被东财回补覆盖**（019K M-11「EM 回补红线」被破坏）。 |
| 补采清单 `data_collector.py` L1799-1804 `NOT IN ('ths_total','sina_main')` | ths_total 行仍进入补采清单，东财 30 分钟内恢复时可覆盖回补 | **必须保留** | 删除后存量 ths_total 行从补采清单消失 → 东财恢复后不重采 → 存量滞留。 |
| 估算 UPDATE 守卫 `data_collector.py` L2773 / L2811 / L2849 `NOT IN ('ths_total','sina_main')` | 估算不得覆盖顶替行 | **建议保留**（防御性） | 估算只写当日，存量 ths_total 全为 08-05/08-06 过去日期，功能上不冲突；保留与另两处语义一致、规避未来存量行与当日行共存时的误覆盖，成本≈0。 |

补充 R-3 第 4 处相关点（虽无字面量）：`daily_report.py` L222-226 缺口 SQL 只判 `capital_source IS NULL AND (is_estimated=0 OR is_estimated IS NULL)`，ths_total/sina_main/估算行均计为"缺口"→ 正确触发延迟补采，**无需改动**。

---

## 三、开放项建议（A / B，供监理拍板）

### 开放项 A — ths_net_inflow 辅助指标：**建议保留（推荐）**

理由：
1. **不违反裁定**：监理裁定针对"主力净流入（main_net_inflow）的来源链路"。ths_net_inflow 是独立辅助列、前端明确标注"辅"、只读展示不参评（4 条评分读取路径均不引用），语义上与裁定无冲突。
2. **爆炸半径悬殊**：保留时改动=仅删 fetch_capital_flow 内 1 个写入块；移除则需级联拆除 `fetch_capital_flow_batch` 整体（THS 批量网络请求、EM 回退 `_em_batch_collect`、019E 补采触发、日报 16:10 调度、前端"同花顺净额"列、`db_manager` 迁移列、`tests/qa_019f` 测试 schema）——远超本裁定意图且引入新回归面。
3. **既有展示价值**：前端"主力 vs 散户背离"观察依赖该列（index.html L2511/2524），移除将损失功能。

风险登记：EM INSERT OR REPLACE 恢复时清空 ths_net_inflow 属 019K R-7 既有行为（当日 THS 批量重跑可重建），无新增风险。

**若监理裁定"主力相关界面一律不见同花顺数字"→ 连辅助展示一并移除 → 另立项执行，本批次不并吞。**

### 开放项 B — 存量 27 行处置：**建议方案 b（清空 main_net_inflow 退回估算展示）**

| 方案 | 结论 | 理由 |
|---|---|---|
| a 保留原样待自然回补 | 不推荐（可作零写库后备） | 存量 27 行 `is_estimated=0`，**当前仍在参与评分**（读取路径只滤 is_estimated=1）→ "同花顺口径冒充主力参评"在存量上继续存在，与监理裁定精神相悖；且解除依赖 EM 恢复，而 EM 自 08-06 起持续全败（08-06 东财 0 行、08-07 东财 0 行），解除时点不可控。 |
| **b 清空 main 退回估算** | **推荐** | ① 只读核验实证：27 行中**约 12 行 main_net_inflow ≠ ths_net_inflow**（08-06 THS 批量二次预取覆盖 ths_net_inflow 所致），含**方向相反**案例（宁德时代 main=+145500/ths=-91700；中芯国际 +21600/-18200；龙芯中科 -2709.91/+230.09）——即使仅展示也自相矛盾，误导性强。② 处置动作小、可回滚：备份 → `UPDATE raw_capital_flow SET main_net_inflow=NULL, is_estimated=1, capital_source=NULL`（保留 ths_net_inflow 与其他字段）→ 只读断言（27 行全命中、残余 ths_total 行=0、is_estimated=0 且 capital_source='ths_total' 行=0）。③ 与"宁缺毋滥"及移除后新链路行为完全一致：这些日子展示为"估算"、不参评。 |
| c 其他（改回 sina_main / 删行） | 不推荐 | 改 sina 需逐日逐只重采（08-06 新浪 lscjfb 当日无行才走的 THS，重采大概率无果）；删行破坏审计轨迹且连带丢失 ths_net_inflow 辅助值。 |

**特别提示（方案 b 附带影响）**：处置后，若后续任何重算/回测再次读 raw_capital_flow，08-05/08-06 的 27 只股票该日资金面将按估算语义处理（与 R-1 不追溯范围一致）。执行须实测处置脚本仅命中 27 行，脚本内断言 `changes()==27` 且 WHERE 限定 `capital_source='ths_total'`，禁止通配 UPDATE。

---

## 四、给开发的附加约束

1. **'ths_total' 字面量冻结**：3 处（L2400 前置 / L1803 补采清单 / L2773·2811·2849 估算守卫）一律不动。登记条件性技术债：**仅当存量 ths_total 行清零后**（方案 b 执行完毕并有与只读断言），经新批次评审方可简化（估算守卫可简化为仅 'sina_main'；前置/补采可简化为 NOT IN ('sina_main') 或删除）。
2. **移除块范围**：仅移除 `fetch_capital_flow` 内 THS 顶替写入块（约 L2704-2752，含 L2702 备注注释），`_fetch_capital_flow_ths_batch` 等网络函数**保留**（供辅助指标引用）。移除后 sina 失败自然落位估算源3/4/5，链路不断。
3. **注释全量对齐两级阶梯**：至少覆盖 `fetch_capital_flow` docstring(L2362-2370)、前置校验注释(L2381-2396)、L2637 日志文案"尝试新浪顶替 → THS 顶替 → 估算兜底"、`fetch_capital_flow_batch` docstring(L1662-1669)、`daily_report.py` L171-180 docstring、`db_manager.py` L964 注释。表述统一为"东财三层 → 新浪 lscjfb 主力口径(sina_main) → 估算兜底（仅展示不参评）"。
4. **验收测试（:memory:/临时库，禁生产写操作）**：① sina 失败→估算行 is_estimated=1 且 capital_source=NULL、status='estimated'、不抛异常；② 存量 ths_total 行不被估算 UPDATE 覆盖（构造同日期场景断言 rowcount=0）；③ 补采清单仍含 ths_total 行；④ EM 恢复重采 → 存量 ths_total 行被 INSERT OR REPLACE 覆盖、capital_source 归 NULL（回归 019K D1）；⑤ `python -m pytest tests/` 全绿（现有测试均不引用 ths_total 写入行为，预期零破坏）；⑥ 防覆盖四态（EM>新浪>估算）对照 019Q 验收结论逐条回归。
5. **回归面纪律**：08-10（周一）16:30 019Q 收尾核验定时任务窗口前后禁止做任何生产库写操作；开发自测不得在交易日盘中做破坏性实验。存量处置脚本（若监理批准 b）必须在非交易时段执行，先 `备份 stock_analyst.db`，执行后保留只读断言输出。
6. **任务书未覆盖的引用点（登记）**：
   - `templates/index.html` L2502/2506/2519（capital_source==='ths_total' 前端标注）→ **保留**（存量行诚实标注"同花顺"）；若采纳方案 b，新数据永不触发该分支，成为无害死分支，可在注释中说明，勿删展示逻辑以免存量期误导。
   - `templates/index.html` L2499/2516 注释、L2068/2579 `'fallback'→'顶替'` 状态映射 → L2068/2579 属通用 fallback（sina_main 仍返回 'fallback'），**不改**；L2499/2516 注释随代码同步修订。
   - `database/db_manager.py` L964 注释 → 更新。
   - `scripts/backfill_capital_sina_019q.py`（019Q 一次性补采脚本，注释提及"可覆盖 THS 行"）→ **诊断脚本豁免**，登记备查，若投入使用须排产于 16:30 窗口之外。
   - `tests/qa_019f_isolation_test.py`（schema 含 ths_net_inflow 列）→ 若保留辅助指标则无需改，登记。
   - `app.py` L1291 注释（同花顺批量预取辅助指标）→ 若保留辅助指标则无需改。
   - `docs/PM_接手提示词_20260809.md` L65-67 描述旧三级链路 → 运维入口文档，建议同步更正（可选整改，不阻塞）。
   - 历史 `docs/reports/*`、`docs/reviews/*`、`docs/tasks/*` → 归档豁免，不整改。
7. **范围禁止**：本批次不得触碰 `advisor.generate_advice`（B24 红线）、评分权重、风控阈值；不新增/删除表结构（capital_source、ths_net_inflow、is_estimated 列均保留）。

---

## 五、待监理批准事项清单

1. 本评审总体"有条件批准"是否认可。
2. 开放项 A：ths_net_inflow 辅助指标保留（推荐）or 移除（另立项）。
3. 开放项 B：存量 27 行处置方案 b（推荐）or a。
4. R-2 附加观察（v5 中性填充 0.0→85 分语义）是否接受登记。

报告输出完毕，等待监理批准，本批次止步于此。
