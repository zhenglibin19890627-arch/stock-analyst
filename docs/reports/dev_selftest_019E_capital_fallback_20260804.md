# 开发自验报告 019E — 资金面批量补采正向触发 + 估算兜底展示与 EM 覆盖重写

**任务编号**：019E  
**开发日期**：2026-08-04  
**开发**：AI Agent（glm5.2）  
**任务书版本**：v2 定稿  
**关联**：019C（EM 重试六项机制）、018（资金源策略）

---

## 一、改动文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `modules/data_adapter.py` | 必改（R-1） | `_read_capital_data()` SQL 增加 `AND (is_estimated = 0 OR is_estimated IS NULL)` 过滤 |
| `modules/advisor.py` | 必改（R-2） | `_build_capital_factors()` SQL 增加同上过滤 |
| `database/db_manager.py` | 仅追加一条 | `_safe_add_columns` 迁移列表追加 `('raw_capital_flow', 'is_estimated', 'INTEGER NOT NULL DEFAULT 0')` |
| `modules/data_collector.py` | 必改（核心） | 任务 1 补采机制 + 任务 2 估算兜底 + 任务 3 EM 覆盖 + 019C 循环提取为共享函数 |
| `templates/index.html` | 必改（前端） | 估算标注 + status 映射 estimated 分支 |
| `modules/scoring_engine.py` | **零改动** | 评审 E-3/M-3 裁定 |

---

## 二、任务实现说明

### 任务 1：批量补采正向触发机制（D1）

- **共享函数提取**：将 019C 回退循环（错峰/分批/退避/冷却/熔断/软超时）提取为 `_em_batch_collect(symbols, log_prefix)` 共享函数，直接沿用 `_EM_INTER_DELAY_RANGE`~`_EM_FALLBACK_TOTAL_CAP_SECONDS` 共享常量及 `_EM_CONSECUTIVE_FAIL_COUNT` 模块级计数器，不新增平行常量
- **补采清单生成**（评审 E-2）：在 `fetch_capital_flow_batch` THS 批量成功路径之后，查询已有真实数据的股票（`main_net_inflow IS NOT NULL AND (is_estimated = 0 OR is_estimated IS NULL)`），补采清单 = 输入列表 - 已有真实数据的股票
- **触发来源标注**：通过 `inspect.stack()` 判断调用方（`daily_report.py` → "日报批次"；`app.py` → "batch-analyze"），在补采循环入口以 INFO 日志标注
- **日志区分**：补采循环使用 `[资金面补采]` 前缀，与 019C 的 `[EM回退]` 区分

### 任务 2：估算兜底写入（D2，仅展示用）

| 约束 | 实现状态 |
|---|---|
| 2.1 数据标记 | `is_estimated` 列由 `_safe_add_columns` 幂等迁移，估算写入行 `is_estimated=1` |
| 2.2 仅写当日 | 估算只取返回数据的 `[0]`（最新 1 天），不写历史序列 |
| 2.3 data_status | 估算成功写 `status='estimated'`，message 注明"估算兜底(新浪/腾讯/网易)，仅展示用" |
| 2.4 防跳过校验 | 前置校验 SQL 改为 `AND (is_estimated = 0 OR is_estimated IS NULL)`，估算行不阻止 EM 重写 |
| 2.5 评分隔离 | data_adapter + advisor 两处 SQL 均增加 `is_estimated` 过滤（R-1/R-2） |
| 2.6 拆除提前 return | L2016 的 `if saved_count == 0: return 'failed'` 改为 `em_all_failed = (saved_count == 0)` 标志位，继续执行估算降级链路 |
| 2.7 UPDATE+INSERT OR IGNORE | 三处估算写入均使用 UPDATE 当日行 → rowcount==0 时 INSERT OR IGNORE，禁止 INSERT OR REPLACE |
| 2.8 返回值语义 | 估算成功返回 `('estimated', msg)`，不返回 `('success', ...)` |
| 2.9 展示标注 | 前端资金面表格 `is_estimated===1` 行追加 `<sup>估算</sup>`，表头动态文案 |

### 任务 3：EM 覆盖重写

- 三处 EM 写入语句（push2his/push2/akshare）INSERT OR REPLACE 均显式携带 `is_estimated` 字段并赋值 0（M-7）
- EM 覆盖估算行后 `is_estimated` 自动归 0，data_status 更新为 `success`
- 不自动 force 重生成日报（评审 E-4）

### 任务 4：前端适配

- 资金面表格：估算行追加 `<sup style="color:#e67e22">估算</sup>`，表头动态显示"来源：东方财富（含估算兜底数据）"
- 采集状态映射（两处）：增加 `estimated → ⚠️估算`，复用 `status-partial` CSS 类

---

## 三、三场景自验记录

### 场景 1：补采清单正确生成

- **构造**：当日 main_net_inflow 为 NULL（仅有 ths_net_inflow 占位行）
- **验证**：补采清单查询返回"无真实数据" → 该股票正确进入补采清单
- **结果**：PASS

### 场景 2：估算兜底写入 + 评分隔离（M-9）

- **构造**：EM 全失败 → 估算写入 1 行 `is_estimated=1`，data_status='estimated'
- **验证 M-10**：ths_net_inflow=1234.56 在估算写入（UPDATE 模式）后保留
- **验证 M-9**：`data_adapter._read_capital_data()` 返回 0 行（估算行被过滤）→ 评分纯净隔离
- **验证 R-2**：advisor SQL 同样过滤估算行
- **结果**：PASS

### 场景 3：EM 覆盖重写（M-7）

- **构造**：估算行存在 → 模拟 EM INSERT OR REPLACE 写入（携带 is_estimated=0）
- **验证**：is_estimated 归 0，main_net_inflow 被覆盖为真实值
- **验证**：data_adapter 返回 1 行真实数据，is_estimated=0
- **结果**：PASS

### 场景 4：返回值语义（M-6）

- fetch_capital_flow 返回值三态正确：`success`（EM 成功）/ `estimated`（估算兜底）/ `failed`（全失败）
- **结果**：PASS

---

## 四、影响面清单

| 影响项 | 说明 |
|---|---|
| 数据库 schema | `raw_capital_flow` 新增 `is_estimated` 列（app 启动自动迁移） |
| 评分链路 | data_adapter `_read_capital_data` + advisor `_build_capital_factors` SQL 增加过滤条件；scoring_engine 零改动 |
| 采集流程 | `fetch_capital_flow` 新增估算兜底返回路径；`fetch_capital_flow_batch` 新增补采触发 |
| 前端展示 | 资金面表格估算标注 + 采集状态 estimated 分支 |
| API 返回 | app.py 的 `/api/stocks/<id>/capital` 使用 `SELECT *`，`is_estimated` 自动随行返回 |

---

## 五、R-1~R-5 风险项确认

| 风险项 | 说明 | 状态 |
|---|---|---|
| R-1 | data_adapter SQL 过滤——估算行不进入 v5 评分数据 | ✅ 已验证 |
| R-2 | advisor SQL 过滤——估算行不进入资金面因子计算 | ✅ 已验证 |
| R-3 | 019C 模块级计数器 `_EM_CONSECUTIVE_FAIL_COUNT` 跨调用共享 | ✅ 共享函数直接使用 |
| R-4 | 熔断后补采循环第一只即退出（属预期行为） | ✅ 共享函数逻辑一致 |
| R-5 | 估算行 LIMIT N 过滤后不足时因子降级 | ✅ 与当前 EM 失败时行为一致 |

---

## 六、红线约束检查

| 红线 | 检查结果 |
|---|---|
| 评分纯净红线 | ✅ 估算值在任何路径下不进入评分（SQL 层过滤已验证） |
| 签名红线 | ✅ `fetch_capital_flow(symbol, market)` / `generate_advice(stock_id, report_date=None)` / `fetch_capital_flow_batch(a_stock_symbols)` 签名不变 |
| 主链路红线 | ✅ 东财三层降级结构不破坏，EM 永远第一优先 |
| 数据安全 | ✅ ALTER TABLE 仅新增列带默认值；估算写入禁止 INSERT OR REPLACE（用 UPDATE+INSERT OR IGNORE） |
| 零代码约束 | ✅ 无新 pip 依赖；schema 迁移由 app 启动自动完成 |
| 范围约束 | ✅ 改动仅限 5 个文件；scoring_engine.py 零改动 |

---

## 七、静态结构核查（11 项全通过）

1. ✅ `is_estimated` 列存在（migration OK）
2. ✅ data_adapter._read_capital_data 有 is_estimated 过滤
3. ✅ advisor._build_capital_factors 有 is_estimated 过滤
4. ✅ scoring_engine.py 零 is_estimated 引用
5. ✅ fetch_capital_flow 返回 ('estimated', ...) 估算成功路径
6. ✅ _em_batch_collect 复用 019C 共享常量
7. ✅ 三处 EM INSERT 语句均携带 is_estimated=0
8. ✅ 估算写入用 UPDATE + INSERT OR IGNORE（无 INSERT OR REPLACE）
9. ✅ 三个函数签名不变
10. ✅ 无 `if False` 硬禁用残留
11. ✅ fetch_capital_flow_batch 含补采逻辑与 `[资金面补采]` 日志前缀

---

**结论**：019E 四项任务（补采触发 + 估算兜底 + EM 覆盖 + 前端适配）全部实现完成，三场景自验通过，红线约束全部守住。待 QA 独立验收。
