# 开发自验报告（2026-07-22 第二批任务）

| 项目 | 内容 |
|---|---|
| **文档编号** | DEV-VERIFY-20260722-B2 |
| **任务书** | DEV-TASKS-20260722-B2 |
| **执行人** | 开发（GLM） |
| **执行日期** | 2026-07-22 |

---

## 任务卡 1：M8-BT-FILL（回测补算）

### 执行结果

| 验收标准 | 结果 | 证据 |
|---|---|---|
| 1. `fill_pending_backtests()` 执行成功 | ✅ PASS | pending=31, filled=31 |
| 2. 覆盖率 >= 99% | ✅ PASS | 535/535 = **100.00%** |
| 3. 幂等性（第二次 pending=0） | ✅ PASS | 第2次: pending=0, filled=0 |
| 4. 不产生重复行（UPSERT） | ✅ PASS | 535条唯一 rating_id |
| 5. 不删除/覆盖 ratings_history | ✅ PASS | 仅读取，未修改 |
| 6. 零代码约束不变 | ✅ PASS | 无新依赖，python app.py 一键启动 |

### 代码修复

- `modules/backtest_engine.py` L593: 修复 `fill_pending_backtests()` 中 `row['id']` → `row['rating_id']`（SQL 别名不匹配 bug）

### 结论

**M8-BT-FILL 验收通过，M8-BACKTEST-003 可最终关闭。**

---

## 任务卡 2：DATASRC-C（资金面结构补齐）

### 执行结果

| 验收标准 | 结果 | 证据 |
|---|---|---|
| 1. 沪深港通标的 north_net_buy 有真实值 | ⚠️ 受限 | API数据截止2024-08-16（见风险说明） |
| 2. 融资融券标的 margin_balance_chg 有真实值 | ✅ PASS | 600276:+10065.69万, 000333:-3845.87万, 300750:+4173.05万 |
| 3. 非标的填 None（不填0，不估算） | ✅ PASS | 港股HK3690: skipped; 非两融标的: None |
| 4. capital 完整度 33%→>=66% | ✅ PASS | 33% → **67%** (2/3字段有值) |
| 5. 数据源失败容错（不阻塞主流程） | ✅ PASS | try/except + warning 日志 |
| 6. 零代码约束（仅 akshare 已有接口） | ✅ PASS | 无新 pip 依赖 |
| 7. 防覆盖机制不破坏 | ✅ PASS | 使用 UPDATE 仅修改目标列，main_net_inflow 完好 |
| 8. 禁用估算值红线（三处 if False 不恢复） | ✅ PASS | 未触碰任何 if False 代码块 |

### 北向资金风险说明

`ak.stock_hsgt_individual_em(symbol)` 接口当前仅返回截止 2024-08-16 的历史数据，
无法获取近期北向资金净买入。此情况属于任务书风险表"akshare 北向资金接口变更/限流（中概率）"。

**缓解措施**：
- 采集函数已实现完整容错（失败返回 skipped/failed + warning 日志）
- 评分引擎对 north_net_buy=None 执行降权处理（DEGRADATION_RULES: 权重降低型）
- 一旦 API 恢复提供近期数据，采集函数将自动写入并生效

### 代码变更清单

| 文件 | 变更内容 |
|---|---|
| `modules/data_collector.py` | 新增 `fetch_north_capital()` + `fetch_margin_balance()` 采集函数；`collect_stock_data()` 集成调用 |
| `modules/data_adapter.py` | 资金面映射增强：north/margin 向后搜索最近非空值（兼容 T+1 延迟） |
| `modules/backtest_engine.py` | 修复 `fill_pending_backtests()` 字段名 bug |

### 红线核验

| 红线 | 状态 |
|---|---|
| 零代码约束 | ✅ 仅调用 akshare 已有接口（stock_hsgt_individual_em / stock_margin_detail_sse / stock_margin_detail_szse） |
| 需求基线 | ✅ 需求 2.1.1 资金面"融资融券余额变化、北向资金流向" |
| 禁用估算值 | ✅ 非标的填 None，不填估算值 |
| 防覆盖机制 | ✅ 使用 UPDATE 仅修改 north_holding_change / margin_balance 列 |
| A/H 双市场独立 | ✅ 北向资金/融资融券仅 A 股，港股自动跳过 |

### 结论

**DATASRC-C 核心目标达成：capital 完整度从 33% 提升至 67%（>=66%），融资融券数据源已补齐。北向资金受上游 API 限制暂无近期数据，代码已就绪待 API 恢复。**

---

## 回归验证

- [x] 零代码约束：`python app.py` 一键启动不变
- [x] 防覆盖机制：main_net_inflow 未被破坏
- [x] 三处 `if False` 未恢复
- [x] 港股 HK3690 不受影响（A/H 双市场独立）
- [x] 评分引擎兼容 None（降权处理正常）

---

**报告版本**：v1.0 | **编制日期**：2026-07-22 | **编制人**：开发（GLM）
