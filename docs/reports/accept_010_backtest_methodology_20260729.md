# PM+QA双签验收报告：010 回测引擎方法论修复与评级有效性诊断

> **批次**：010  | **验收日期**：2026-07-29  | **状态**：✅ 通过，批次关闭

---

## 一、批次信息

| 项 | 内容 |
|---|---|
| 任务编号 | DEV-TASKS-20260729-010-DEV |
| 架构评审 | `docs/reviews/review_010_backtest_methodology_20260729.md`（656行，7决策点） |
| 开发任务书 | `docs/tasks/dev_tasks_20260729_010_dev.md`（325行） |
| QA验收任务书 | `docs/tasks/qa_tasks_20260729_010.md`（137行） |
| 开发自验 | `reports/dev_selftest_010_backtest_methodology_20260729.md`（206行） |
| QA验收 | `reports/qa_accept_010_backtest_methodology_20260729.md`（162行，20/20通过） |

---

## 二、交付物清单

| 交付物 | 路径 | 状态 |
|---|---|---|
| 核心修改 | `modules/price_backtest.py`（+227行/-15行） | ✅ |
| 表结构迁移 | `database/db_manager.py`（+36行） | ✅ |
| 回测数据 | `price_backtest_results` 表（938条重跑 + 新增5列） | ✅ |
| 架构评审报告 | `docs/reviews/review_010_backtest_methodology_20260729.md` | ✅ |
| 开发任务书 | `docs/tasks/dev_tasks_20260729_010_dev.md` | ✅ |
| QA验收任务书 | `docs/tasks/qa_tasks_20260729_010.md` | ✅ |
| 开发自验报告 | `reports/dev_selftest_010_backtest_methodology_20260729.md` | ✅ |
| QA验收报告 | `reports/qa_accept_010_backtest_methodology_20260729.md` | ✅ |

---

## 三、验收结果汇总

### 3.1 PM交付物检查

| 维度 | 结果 |
|---|---|
| 红线核验 | 10/10 合规 |
| 核心修改验证 | 9/9 通过 |
| 数据验证 | 全部通过 |

### 3.2 QA功能验收

| 维度 | 结果 |
|---|---|
| 红线复核 | 10/10 合规 |
| 测试用例 | 20/20 通过 |
| 阻塞性问题 | 0 |
| 验收结论 | 通过 |

### 3.3 核心成果

| 子任务 | 修复前 | 修复后 | 效果 |
|---|---|---|---|
| 010-1 动态止盈同步 | 固定公式 `cost*(1+gain)` | 动态公式 `max(min_tp, min(fixed_tp, resistance))` | 与009系统一致，误差0% |
| 010-2 稀释Bug | 713条无持仓 take_profit_hit=0（污染分母） | 全部为NULL（正确排除） | 推荐买入止盈命中率 8.0%→32.59% |
| 010-3 锚点标记 | 无可信度标记 | confirmed=7/mismatched=27/unknown=904 + bias_risk标记 | 量化未来函数偏差 |
| 010-4 可信样本报告 | 无分时段/分可信度统计 | confidence_report + period_comparison | 偏差可视化 |

### 3.4 命中率修正后数据（T+20，有持仓）

| 评级 | 止盈命中率（修复前混合口径） | 止盈命中率（修复后正确口径） |
|---|---|---|
| 推荐买入 | 8.0% | **32.59%** |
| 持有观望 | 10.7% | **91.11%** |
| 建议减仓 | 14.0% | **88.89%** |

---

## 四、QA观察项（非阻塞）

| # | 观察项 | 说明 | 后续建议 |
|---|---|---|---|
| 1 | Q1-1测试样本集中度高 | 3只有持仓样本均为stock_id=4 | 后续扩大测试样本范围 |
| 2 | mismatched样本27条未触发小样本标注 | 27>=20阈值，代码逻辑正确 | 统计结论需谨慎 |

---

## 五、红线清单更新（010后）

| 红线 | 说明 | 变化 |
|---|---|---|
| advisor.py generate_advice | B24红线 | 不变 |
| advisor.py _build_capital_factors | 不可改 | 不变 |
| data_collector.py L1645/L1684/L1717 | 三处 if False | 不变 |
| config_weights.json | rating_mapping 80/65/50/30 | 不变 |
| 零代码约束 | 8包 | 不变 |
| scoring_engine.py | v5引擎 | 不变 |
| price_advisor.py | 009后可修改 | 不变 |
| price_backtest.py | **010后含动态止盈+锚点标记+可信报告** | **更新** |
| price_backtest_results表 | **新增5列（rating_confidence/anchor_rating_date/anchor_rating/bias_risk/days_since_rating）** | **更新** |

---

## 六、双签确认

| 角色 | 结论 | 签字 |
|---|---|---|
| PM | 交付物完整，红线10/10合规，数据验证通过 | ✅ |
| QA | 20/20用例通过，0阻塞问题 | ✅ |

**010批次关闭。**

---

## 七、后续方向

| 方向 | 说明 | 时间线 |
|---|---|---|
| ratings_history持续积累 | 当前200条/12天，锚点覆盖率3.6% | 每日自动积累 |
| 完整动态评级回测 | 积累至400+条/40+天后启动 | 约2个月后 |
| 前端可信样本卡片 | 可选，API已就绪 | 随前端批量优化一起做 |
