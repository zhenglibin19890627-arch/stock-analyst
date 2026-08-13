# 开发任务书 019E — 资金面批量补采正向触发 + 估算兜底展示与 EM 覆盖重写【v2 定稿】

**签发日期**：2026-08-03（跨日执行，定稿 2026-08-04）
**签发人**：PM
**批次编号**：019E（019C/019D 后续）
**优先级**：P1
**关联批次**：018（资金源策略）、019C（EM 重试六项机制，已双签关闭）、019D（已关闭，2026-08-04 监理批准）
**开发自验**：✅ 已完成（自验报告：`reports/dev_selftest_019E_capital_fallback_20260804.md`）
**架构评审**：✅ 有条件通过 → 已按 M-1~M-10 全部修订定稿（评审报告：`docs/reviews/review_019E_capital_fallback_20260804.md`）
**监理批准**：✅ 已批准进入开发（2026-08-04 监理裁定）

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 推荐窗口类型 | Quests 独立窗口（单代理执行） |
| 推荐模型 | 开发：glm5.2（已完成）→ QA：kimi k3（验收类任务） |
| 执行模式 | 已关闭（关闭记录：`reports/qa_accept_019E_capital_fallback_20260804.md` 第九节） |
| 流程路径 | ✅PM 签发 v1 → ✅架构师评审 → ✅监理批准 → ✅开发执行+自验 → ✅QA 独立验收（5 组用例全 PASS）→ ✅PM+QA 双签 → ✅监理批准关闭（2026-08-04） |

---

## 一、背景

### 事故复盘（2026-08-03，PM 现场核查证据链闭环）

1. **旧代码污染**：08-03 16:31 前运行的是 018 改造前旧代码，THS 批量把同花顺"净额"写入 `main_net_inflow`，污染前置校验，29 只全部"同日跳过"EM 逐只，当日 29 份日报资金面评分失真
2. **新代码无正向补采触发点**：20:14 切换新代码后，`fetch_capital_flow_batch` 在 **THS 批量成功时只写 `ths_net_inflow` + 占位行**，不回退 EM；后续日报批次全部 B11 复用 → 19 只 `main_net_inflow` 持续 NULL，无任何补采机会
3. **深夜风控压制**：PM 手动触发补采（监理批准），前半程成功 5 只后东财接口全面拒绝，最终仍有 **14 只缺失**（000858/000977/002230/002352/002415/002458/002714/300124/300146/601012/601888/603501/688047/688981）

### 设计缺陷结论

| # | 缺陷 | 本批次对策 |
|---|---|---|
| D1 | 批量场景缺少"当日 main 缺失时补采 EM 逐只"的正向触发机制 | 任务 1 |
| D2 | EM 全部失败时直接 failed，页面无任何资金面展示，且无自动重试恢复路径 | 任务 2 + 任务 3（监理/用户裁定：估算仅兜底展示，不参与评分；EM 恢复后覆盖重写） |

### 历史约束（开发必读）

新浪/腾讯/网易三源在 P3-A 验收时被 `if False` 硬禁用，原因是其**无真实主力资金数据**，只能"成交额×涨跌幅"估算，估算值与真实主力净流入无相关性。**本批次恢复三源的唯一用途是兜底展示**，必须通过数据标记 + 读取侧过滤确保估算值**永远不进入评分**。

---

## 二、执行角色

**开发**

---

## 三、任务范围

> **架构师建议的开发顺序（必须遵循）**：先改过滤点（data_adapter + advisor）→ 再改迁移（db_manager）→ 再改采集端（data_collector）→ 最后改前端。确保评分隔离在估算数据写入前就已就位。

### 任务 1：批量补采正向触发机制（D1）

在 `fetch_capital_flow_batch`（`modules/data_collector.py`）THS 批量流程**之后**（无论 THS 成功与否），新增补采环节：

1. **补采清单生成**（架构评审 E-2 裁定）：
   - 查询已有真实数据的股票：`main_net_inflow IS NOT NULL AND (is_estimated = 0 OR is_estimated IS NULL)`
   - 补采清单 = **输入的 `a_stock_symbols` 列表** 减去已有真实数据的股票（取并集逻辑——THS 批量失败时部分股票可能完全无行，不能只查表内 NULL 行）
   - 该清单天然覆盖两类待补采对象：`main_net_inflow IS NULL` 的占位行/无行股票、`is_estimated=1` 的估算行（EM 恢复后需重写）
2. 清单非空 → 对清单执行 EM 逐只补采循环，**完整复用 019C 六项机制**（错峰/分批/退避/冷却/熔断/整体软超时，常量 `_EM_INTER_DELAY_RANGE`~`_EM_FALLBACK_TOTAL_CAP_SECONDS` 及模块级计数器 `_EM_CONSECUTIVE_FAIL_COUNT` 等**直接沿用共享，不新增平行常量**；回退循环已触发熔断时补采循环第一只即熔断退出，属预期行为，不得绕过）
3. 日志措辞需可区分：`[资金面补采] …`（与 019C 的"同花顺批量失败回退"路径区分，QA 依赖日志核查），并在补采循环入口以 INFO 日志标注触发来源（日报批次 vs batch-analyze）
4. THS 批量失败走 019C 原回退路径时**无需显式去重**：回退循环已成功的股票 `main_net_inflow` 已 NOT NULL 且 `is_estimated=0`，天然不进补采清单（评审 E-2① 已论证）

### 任务 2：估算兜底写入（D2，仅展示用）

解除 `fetch_capital_flow` 内新浪/腾讯/网易三处 `if False` 硬禁用（**仅限当前 EM 三层全失败时**），按原降级顺序写入估算值，强制约束：

| # | 约束 | 实现要点（均为架构评审裁定，不可偏离） |
|---|---|---|
| 2.1 | **数据标记** | `raw_capital_flow` 新增列 `is_estimated INTEGER NOT NULL DEFAULT 0`；迁移入口：`db_manager.py` `_safe_add_columns` 列表（L961 `ths_net_inflow` 条目之后追加 `('raw_capital_flow', 'is_estimated', 'INTEGER NOT NULL DEFAULT 0')`），app 启动时 `init_database()` 自动幂等迁移，**禁止手工 ALTER**；估算写入行 `is_estimated=1`，真实 EM 行恒为 0 |
| 2.2 | **仅写当日** | 估算只写当日 1 行（与 EM 写 120 天历史不同），避免污染历史序列 |
| 2.3 | **data_status** | 估算兜底成功时写 `status='estimated'`，message 注明"估算兜底(新浪/腾讯/网易)，仅展示用，待东方财富恢复后覆盖"；任务 1 的补采清单须将估算行视为待补采（估算行不阻止后续 EM 重试） |
| 2.4 | **防跳过校验适配** | `fetch_capital_flow` 前置校验（L1822-1826）改为 `main_net_inflow IS NOT NULL AND (is_estimated = 0 OR is_estimated IS NULL)`，否则估算行会阻止 EM 恢复后重写（评审 E-5 C-3） |
| 2.5 | **评分隔离（最高风险，过滤点在 DB 查询层）** | ① `data_adapter.py` `_read_capital_data()`（L273-287）SQL 增加 `AND (is_estimated = 0 OR is_estimated IS NULL)`；② `advisor.py` `_build_capital_factors()`（L1122-1126）SQL 同步增加该条件。**注意：`scoring_engine.py` 无需改动**——其 `score_main_capital()` 读 StockData 内存对象，过滤必须在 data_adapter/advisor 的 SQL 层完成（评审 E-3/M-1/M-2）。估算行存在时评分沿用最近真实交易日（T-1 或更早）数据；若 LIMIT N 行过滤后不足（评审 R-3），因子降级行为与当前 EM 失败时一致，属预期 |
| 2.6 | **拆除提前 return（M-4，不做则估算兜底静默失效）** | `fetch_capital_flow` L2016-2024 的 `if saved_count == 0: return 'failed'` 必须改为标志位（如 `em_all_failed = (saved_count == 0)`）并继续向下执行估算降级链路；估算成功返回 `('estimated', msg)`；估算也全失败时在最末尾（L2138 附近）返回 `('failed', fail_msg)` |
| 2.7 | **估算写入语句（M-5，禁止 INSERT OR REPLACE）** | 估算写入必须用 **UPDATE + INSERT OR IGNORE** 模式（参考 `north_holding_change` L2298-2314 写法）：先 UPDATE 当日行（SET main_net_inflow/main_net_inflow_pct/is_estimated=1），`rowcount==0` 时再 INSERT OR IGNORE。原因：INSERT OR REPLACE = DELETE+INSERT，会清除占位行已有的 `ths_net_inflow` 等字段（数据丢失） |
| 2.8 | **返回值语义（M-6）** | 估算成功时 `fetch_capital_flow` 返回 `('estimated', msg)` 而非 `('success', ...)`，确保 019C 回退循环 `result[0]=='success'` 判定不将估算计为成功（不误重置连续失败计数，评审 E-5 C-1 已验证其余消费方兼容） |
| 2.9 | **展示标注** | 见任务 4（前端） |

### 任务 3：EM 覆盖重写（D2）

EM 采集恢复成功时（任何触发路径：补采/单只分析/批量）：

1. 当日行 INSERT OR REPLACE 覆盖估算值，**三处 EM 写入语句（L1881/L1932/L1981）必须显式携带 `is_estimated` 字段并赋值 0**（M-7，防御 DEFAULT 隐性依赖），确保估算→真实覆盖时标记归位
2. `data_status` 由 `estimated` 更新为 `success`（沿用现有 save_data_status 先删后插语义）
3. **不自动 force 重生成当日日报**（评审 E-4 裁定采纳 PM 倾向）：当日日报已按 T-1 真实数据评分属可接受口径，估算行已被 R-1/R-2 过滤不影响评分；在日报批次日志中记录"本批次 N 只股票资金面仍为估算数据，待东方财富恢复后自动覆盖"供用户知晓；用户可通过"重新分析"按钮手动触发

### 任务 4：前端适配（最小改动）

1. **资金面表格估算标注**（`index.html` L2477-2490，评审 E-6 唯一标注点）：`is_estimated===1` 的行在 `main_net_inflow` 值后追加 `<sup style="color:#e67e22;font-size:11px">估算</sup>`；表头文案动态化：存在估算行时显示"来源：东方财富（含估算兜底数据）"，否则"来源：东方财富"
2. **采集状态映射增加 estimated 分支**（两处，评审 E-5 C-4/C-5）：`index.html` L2541-2542 与 L2067-2072 的三元链增加 `estimated → ⚠️估算`（复用现有 `status-partial` CSS 类），否则估算态会误显示"❌失败"
3. **不标注的位置**：评分卡片、价格建议资金面色块——基于过滤后真实数据评分，标注反而引起困惑（评审 E-6 裁定）

### 明确不改范围

- `fetch_capital_flow(symbol, market)` **签名不变**（011 红线）；`generate_advice()` 签名不变（B24 红线）；`fetch_capital_flow_batch` 签名不变
- **`modules/scoring_engine.py` 无需改动**（评审 E-3/M-3，过滤在上游 SQL 层）
- 估算源不得写入历史多日数据、不得参与任何评分/评级计算
- `config_weights.json`（含 BOM）、`config.py` 超时常量不动
- 019C 六项机制的既有实现不改，仅复用

---

## 四、验收标准

1. 构造场景：当日 main 为 NULL（仅占位行或无行）→ 触发 `fetch_capital_flow_batch` → 补采清单正确生成并执行，日志含 `[资金面补采]` 及触发来源标注，六项机制生效（时间戳可核查）
2. 构造场景：EM 三层全失败 → 估算兜底实际执行（日志含估算兜底字样，验证提前 return 已拆除）→ 写入 1 行 `is_estimated=1`，data_status='estimated'，返回值 `('estimated', ...)`；**评分使用的仍是 T-1 真实数据**：断言 `data_adapter._read_capital_data()` 返回的 main_net_inflow 对应行 `is_estimated=0` 且 trade_date ≠ today（M-9 评分纯净隔离验证）
3. 估算行存在时再次触发 EM 采集成功 → 当日行被覆盖、`is_estimated=0`、data_status='success'；**且 `ths_net_inflow` 字段未被清除**（M-10 INSERT OR REPLACE 陷阱检查——估算写入用 UPDATE 模式、EM 写入显式携带 is_estimated=0）
4. 前端展示估算数据时有"估算"标注与表头动态文案；真实数据无标注；采集状态显示"⚠️估算"而非"❌失败"
5. 全仓 grep：无第三套估算源启用路径（除本批次解除的三处）；`is_estimated` 读取过滤覆盖 data_adapter + advisor 两处评分链路 SQL；scoring_engine.py 零改动
6. 开发报告含：三个场景自验记录 + 迁移方式说明（`_safe_add_columns` 条目）+ 影响面清单 + R-1~R-5 风险项逐项确认（架构师要求）

---

## 五、红线约束

1. **评分纯净红线**：估算值在任何路径下不得进入评分/评级计算（最高优先级，违反即 FAIL 退回）；过滤点以本任务书 2.5 列出的 data_adapter + advisor 两处 SQL 为准
2. **签名红线**：`fetch_capital_flow` / `generate_advice()` / `fetch_capital_flow_batch` 对外签名不变
3. **主链路红线**：东财三层降级（push2his → push2 → akshare）结构不破坏，EM 永远是第一优先；估算仅在三层全失败后兜底
4. **数据安全**：不得删除/覆盖存量真实历史数据；ALTER TABLE 仅新增列且带默认值；估算写入禁止 INSERT OR REPLACE（须 UPDATE + INSERT OR IGNORE）
5. **零代码约束**：不引入新 pip 依赖（requirements.txt 维持 9 包）；schema 迁移由 app 启动自动完成
6. **范围约束**：改动限于 `modules/data_collector.py`（必改）、`modules/data_adapter.py`（必改，R-1 过滤）、`modules/advisor.py`（必改，R-2 过滤）、`database/db_manager.py`（仅 `_safe_add_columns` 列表追加一条）、`templates/index.html`（估算标注 + status 映射）；`modules/scoring_engine.py` 及其余文件一律不碰

---

## 六、执行顺序

```
Step 1: ✅ PM 签发 v1
Step 2: ✅ 架构师评审（有条件通过，M-1~M-10 + R-1~R-5）
Step 3: ✅ PM 按评审裁定修订定稿（本稿 v2），✅ 监理已批准（2026-08-04）
Step 4: 开发执行 + 自验（三场景 + 过滤点先行顺序）← 当前
Step 5: QA 独立验收 → PM+QA 双签 → 监理批准关闭
```

---

> **PM 备注**：本批次直接源于 08-03 资金面采集事故与用户（监理）指示。v1 的三处关键错误已按评审修正：①评分过滤点从 scoring_engine 改为 data_adapter/advisor 的 SQL 层（否则估算值泄漏进评分）；②L2016 提前 return 必须拆除否则估算代码不可达；③估算写入禁止 INSERT OR REPLACE 防止 ths_net_inflow 数据丢失。开发请务必遵循"先过滤点→迁移→采集端→前端"的顺序。
