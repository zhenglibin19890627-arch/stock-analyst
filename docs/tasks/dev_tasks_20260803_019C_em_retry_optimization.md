# 开发任务书 019C — 东财采集重试增强与错峰分批优化（019B 后续优化）【定稿 v2】

**签发日期**：2026-08-03
**签发人**：PM
**批次编号**：019C（019B 后续优化）
**优先级**：P1
**关联批次**：019B（已双签关闭，见 `reports/pm_accept_019B_eastmoney_20260803.md` 第四节待决策项 #1）
**架构评审**：✅ 已通过（`docs/reviews/review_019C_em_retry_optimization_20260803.md`，结论：有条件通过；本定稿已按评审裁定全部修订）
**监理批准**：✅ 已批准（2026-08-03），准予进入开发执行

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 推荐窗口类型 | Quests 独立窗口（单代理执行） |
| 推荐模型 | glm5.2（编码任务） |
| 执行模式 | 单代理开发 + 自验 |
| 流程路径 | PM 签发 → ✅架构师评审通过 → ✅监理批准 → **开发执行+自验（当前阶段）** → QA 独立验收 → PM+QA 双签 → 监理批准关闭 |

---

## 一、背景

019B 批次已定位东财接口失败根因为**间歇性反爬阻断**，并通过 `_http_get_em()` 多轮重试 + 22 个 UA 池 + 随机延迟 1.5~3.5s 恢复了采集能力。但存在三个待优化点：

1. **重试轮数被调用处硬编码压制**：`_http_get_em()` 默认走 `MAX_RETRIES=3`（config.py L32），但 L1365/L1430 两处调用显式传 `max_retries=1`，仅 1 轮重试
2. **批量回退循环无错峰**：同花顺批量失败回退 EM 逐只采集时（`fetch_capital_flow_batch` 内循环 L1223-L1230），请求间无任何间隔
3. **无分批、无退避、无熔断、无整体超时**：回退循环在 012 的 `BATCH_TIMEOUT` 计时窗口**之外**执行（架构评审发现 F-1），无兜底

### 架构评审关键发现（开发必读）

- **F-2：熔断安全依据** —— 日报主循环逐只 `collect_stock_data` → `fetch_capital_flow`（L2643）含"同日已有真实数据即跳过"机制，预取回退循环提前终止是安全的：未覆盖的股票会在主循环中再次采集（主循环受 `STOCK_TIMEOUT_SECONDS=90` 保护）
- **F-3 / R-1**：`max_retries` 1→3 后资金面三层全失败最坏约 140~155s（无代理），超过单只 90s 超时；本批次不改超时值，QA 观察超时频率，必要时另立批次
- **R-3**：熔断计数以回退循环内 `fetch_capital_flow` 返回为准——`result[0] == 'success'`（**含"同日跳过"**）重置计数，其余计失败
- **R-4**：熔断计数为进程级状态（参照 `_THS_CONSECUTIVE_FAIL_COUNT`），Flask 不重启时跨次批量生效，属期望行为
- **R-5**：`app.py` batch-analyze（L1291）与日报共用 `fetch_capital_flow_batch`，改造对两条路径同时生效（预期行为）

---

## 二、执行角色

**开发**

---

## 三、任务范围（按架构评审裁定定稿）

### 任务 1：移除 max_retries 硬编码（回归默认 3 轮）

- **移除** L1365（`_fetch_capital_flow_em_individual`）与 L1430（push2 层）的 `max_retries=1` 参数本身，改为 `_http_get_em(url, params=params)`
- ❌ 不得显式传 `max_retries=3`（避免数值再次散落调用处）
- ❌ 不得新增 `EM_MAX_RETRIES` 常量，`config.py` 的 `MAX_RETRIES = 3` 不动

### 任务 2：新增模块顶部常量（data_collector.py，`_THS_*` 常量附近）

```python
_EM_INTER_DELAY_RANGE = (2.0, 5.0)     # 股票间基础错峰延迟（秒）
_EM_BATCH_SIZE = 5                     # 分批大小（只）
_EM_BATCH_GAP_RANGE = (30.0, 60.0)     # 批间间隔（秒）
_EM_BACKOFF_CAP_SECONDS = 30           # 退避延迟上限（秒）
_EM_COOLDOWN_FAIL_N = 3                # 冷却触发：连续失败只数
_EM_COOLDOWN_SECONDS = 60              # 冷却暂停时长（秒）
_EM_CIRCUIT_BREAK_N = 5                # 熔断触发：连续失败只数
_EM_FALLBACK_TOTAL_CAP_SECONDS = 600   # 回退循环整体软超时（秒）
```

### 任务 3：改造回退循环（L1223-L1230）

仅改 `fetch_capital_flow_batch` 内"同花顺批量失败回退 EM 逐只循环"，机制按序叠加：

| # | 机制 | 实现要点 |
|---|---|---|
| 1 | **错峰** | 逐只之间 `time.sleep(random.uniform(*_EM_INTER_DELAY_RANGE))`，**首只免延迟** |
| 2 | **分批** | 每 5 只一批，批间 `random.uniform(*_EM_BATCH_GAP_RANGE)` 停顿（23 只 → 5 批，4 次停顿） |
| 3 | **退避** | 每次失败后，下一只延迟 = `min(基础延迟 × 2^连续失败数, _EM_BACKOFF_CAP_SECONDS)` |
| 4 | **冷却** | 连续失败 ≥ 3 只 → 额外暂停 60s 后继续 |
| 5 | **熔断** | 连续失败 ≥ 5 只 → **提前终止本轮回退循环**，返回已采集的部分结果，日志记录熔断原因与剩余未采集清单 |
| 6 | **整体软超时** | 每只开始前检查 `time.time() - start > 600`，超限终止剩余并在返回值 `source` 中标注 |
| 7 | **计数重置** | 模块级连续失败计数，`result[0] == 'success'`（含同日跳过）即重置（R-3） |
| 8 | **日志** | 分批进入/批间停顿/冷却/熔断/超时终止均需 `logger.info/warning`，含时间戳可核查（QA 验收依赖） |

### 明确不改范围

- **单只分析主链路（L2643）不加任何外部延迟**（用户实时触发，体验敏感；`_http_get_em` 内部延迟已够）
- **日报主循环（逐只 `collect_stock_data`）不加分批**
- `config.py`（`MAX_RETRIES`/`STOCK_TIMEOUT_SECONDS`/`BATCH_TIMEOUT_SECONDS` 均不动）、`daily_report.py`、`app.py`、`advisor.py`、`scoring_engine.py`、`config_weights.json` 一律不碰

---

## 四、验收标准

1. 两处调用处 `max_retries=1` 已移除，重试轮数回归 3（代码核查确认，无显式传参、无新增常量）
2. 8 个 `_EM_*` 常量已在模块顶部定义，取值与任务 2 清单一致
3. 回退循环具备错峰/分批/退避/冷却/熔断/软超时全部 6 项机制，日志时间戳可核查
4. **错峰/分批验证方法**（评审 R-6）：构造同花顺批量源失败场景（临时令 THS 主接口+备选接口均失败）触发回退循环，或至少以代码路径核查 + 日志时间戳佐证；3 只股票（含 600519）完整东财采集成功写入
5. 单只采集（`fetch_capital_flow`）无回归；batch-analyze 路径（app.py L1291）回归抽查正常（R-5）
6. 开发报告说明：实测耗时记录、熔断/冷却机制自验结果、R-1 观察口径交接 QA

---

## 五、红线约束

1. **签名红线（011）**：`fetch_capital_flow(symbol, market)` 签名不变，不加 force_full 参数
2. **签名红线（B24）**：`generate_advice()` 签名不变（本批次不触碰 advisor.py）
3. **主链路红线**：东财三层降级（push2his → push2 → akshare）结构不破坏
4. **禁用红线**：估算数据源（新浪/腾讯/网易）维持 `if False` 硬禁用
5. **零代码约束**：不引入新 pip 依赖（requirements.txt 维持 9 包），错峰/分批/退避/熔断仅用 `time`/`random`（stdlib，模块已引入）
6. **配置红线**：`config_weights.json` 不得修改（含 BOM 检查）；`config.py` 超时常量不动
7. **数据安全**：不得删除/覆盖现有数据；写入逻辑（UPDATE/INSERT 语义）不变
8. **范围约束**：改动限于 `modules/data_collector.py`

---

## 六、执行顺序

```
Step 1: ✅ 监理已批准本定稿（2026-08-03）
Step 2: 开发执行任务 1~3（改动仅 data_collector.py）
Step 3: 开发自验（3 只股票采集验证 + 回退循环场景验证 + 日志核查）
Step 4: 提交开发报告，PM 安排 QA 独立验收（QA 任务书将含 R-1/R-4/R-6 知悉项）
Step 5: PM+QA 双签，报监理批准关闭
```

---

> **PM 备注**：本定稿已吸收架构评审报告全部裁定（5 个决策点裁定 + 8 个常量清单 + 6 项修改点 + R-1~R-7 风险项中涉开发项）。开发请在 Quests 独立窗口以本任务书全文作为启动提示词执行。
