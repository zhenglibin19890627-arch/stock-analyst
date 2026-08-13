# 开发自验报告：010 回测引擎方法论修复与评级有效性诊断

> **任务编号**：DEV-TASKS-20260729-010-DEV  
> **开发日期**：2026-07-29  
> **推荐模型**：glm5.2  
> **状态**：开发完成，待PM/QA验收

---

## 一、任务目标

修复 price_backtest.py 回测引擎的三项方法论缺陷，并新增评级有效性诊断能力：

| 子任务 | 优先级 | 目标 |
|---|---|---|
| 010-1 | P0 | 同步009动态止盈算法（MIN_TARGET_GAIN + _calc_resistance + 动态止盈公式） |
| 010-2 | P0 | 修复止盈命中率稀释Bug（无持仓样本 take_profit_hit 被错误计入分母） |
| 010-3 | P1 | 新增锚点标记（confirmed/mismatched/unknown）+ 高偏差风险标记（high/medium/low） |
| 010-4 | P1 | 可信样本报告（confidence_report + period_comparison） |

---

## 二、改动清单

### 2.1 修改文件（2个，符合预期）

| 文件 | 改动类型 | 改动行数 | 说明 |
|---|---|---|---|
| `modules/price_backtest.py` | 核心修改 | +227行 / -15行 | 010-1~010-4 全部核心逻辑 |
| `database/db_manager.py` | 新增函数 | +36行 | _ensure_price_backtest_columns 幂等迁移 |

### 2.2 受保护文件（零修改确认）

| 文件 | 状态 |
|---|---|
| `modules/advisor.py` | 未修改 |
| `modules/price_advisor.py` | 未修改 |
| `modules/backtest_engine.py` | 未修改 |
| `modules/scoring_engine.py` | 未修改 |
| `modules/data_collector.py` | 未修改 |
| `config_weights.json` | 未修改 |

### 2.3 详细改动说明

#### modules/price_backtest.py

**010-1 动态止盈同步：**
- 新增 `MIN_TARGET_GAIN` 常量（5档值与 price_advisor.py L64-70 完全一致）
- 新增 `_calc_resistance(close, ma60, boll_upper)` 函数（与 price_advisor.py L223-236 一致）
- 重写 `_gen_with_position(close, cost_price, rating, ma60=None, boll_upper=None)`：
  - 旧公式：`take_profit = cost * (1 + target_gain)`（固定止盈）
  - 新公式：`take_profit = max(min_tp, min(fixed_tp, resistance))`（动态止盈）
- 修改 `_gen_price_advice_at_date` 调用方传递 `ma60/boll_upper`

**010-2 稀释Bug修复：**
- `_check_hit` 函数末尾的统一循环中移除 `take_profit`
- 新增独立判断：仅当 `advice.take_profit is not None` 时才将 `take_profit_hit` 设为0
- 效果：无持仓样本的 take_profit_hit 保持 NULL，不再稀释止盈命中率分母

**010-3 锚点标记 + 高偏差风险：**
- 新增 `_normalize_rating_for_compare(rating_str)`：复用 scoring_engine.normalize_rating 兼容历史A/B+/B/C/D
- 新增 `_mark_rating_confidence(cursor, stock_id, bt_date, rating)`：查找回测日前后5天内最近评级记录，归一化比较
- 新增 `_calc_bias_risk(all_kline, bt_idx, rating)`：减仓/卖出评级 + 近60日涨幅 >30% → high
- 主回测循环 INSERT 语句新增5个字段值

**010-4 可信样本报告：**
- `compute_price_backtest_report` 新增 `confidence_report` 字段（4组统计 + 小样本标注）
- 新增 `period_comparison` 字段（recent_12d vs earlier 分时段对比）

#### database/db_manager.py

- 新增 `_ensure_price_backtest_columns(cursor=None)` 函数，参考 backtest_engine._ensure_columns 模式
- 幂等添加5列：rating_confidence / anchor_rating_date / anchor_rating / bias_risk / days_since_rating
- 在 `_migrate_columns` 末尾调用，init_database 时自动执行

---

## 三、自验结果

### V1：force=True 重跑

```
Result: {'total': 938, 'success': 938, 'errors': 0, 'skipped': 0}
```

- 938条记录全部重新生成，0错误

### V2：010-1 动态止盈验证

| 检查项 | 结果 |
|---|---|
| MIN_TARGET_GAIN 常量存在（5档值） | ✅ |
| _calc_resistance 函数存在 | ✅ |
| _gen_with_position 使用动态止盈公式 | ✅ |
| _gen_price_advice_at_date 传递 ma60/boll_upper | ✅ |

有持仓样本 take_profit 示例（动态计算结果）：

```
take_profit=57.12, close=63.79, ma60=None, boll_upper=65.0368
take_profit=57.12, close=62.83, ma60=None, boll_upper=65.4864
```

### V3：010-2 稀释Bug修复验证

| 检查项 | 结果 | 期望 |
|---|---|---|
| has_position=0 且 t20_hit_take_profit NOT NULL | **0** | 0 |
| has_position=0 且 t20_hit_take_profit IS NULL | **713** | >0 |
| has_position=0 且 t5_hit_take_profit NOT NULL | **0** | 0 |

**结论：713条无持仓样本的止盈命中率不再被错误计入分母。**

### V4：010-3 锚点标记 + 偏差风险验证

**锚点可信度分布：**

| rating_confidence | 数量 | 占比 |
|---|---|---|
| confirmed | 7 | 0.7% |
| mismatched | 27 | 2.9% |
| unknown | 904 | 96.4% |

> unknown 占比高符合预期：ratings_history 时间跨度仅07-16~07-29（约12天），绝大多数历史回测日前后5天内无评级记录。

**偏差风险分布：**

| bias_risk | 数量 | 占比 |
|---|---|---|
| high | 47 | 5.0% |
| medium | 51 | 5.4% |
| low | 840 | 89.6% |

**days_since_rating 分布（非NULL部分）：**

| 天数 | 数量 |
|---|---|
| 0 | 28 |
| 1 | 5 |
| 2 | 1 |

### V5：010-4 可信样本报告验证

**confidence_report 字段：**

| 分组 | total | 小样本标注 |
|---|---|---|
| confirmed | 7 | "样本量不足（<30），仅供参考" |
| mismatched | 27 | 无（≥20） |
| unknown | 904 | 无 |
| bias_risk_high | 47 | "高偏差风险样本，命中率可能虚高" |

**period_comparison 字段：**

| 时段 | total |
|---|---|
| recent_12d (≥2026-07-16) | 30 |
| earlier (<2026-07-16) | 908 |

标注："近12天有真实评级数据，命中率可能更准确；之前时段存在未来函数偏差"

---

## 四、偏差分析

### 4.1 锚点覆盖率偏低（unknown=96.4%）

**根因**：ratings_history 表实际数据时间跨度仅约12天（2026-07-16~07-29），而回测覆盖约938个历史交易日（跨度数月）。绝大多数历史回测日前后5天内无对应评级记录。

**影响**：confirmed+mismatched 合计仅34条（3.6%），锚点验证能力有限。随 ratings_history 持续积累（M8回测框架运行），此比例将逐步提升。

**缓解措施**：010-4 的 period_comparison 字段已通过分时段统计部分弥补此问题——recent_12d 时段的30条样本对应有真实评级数据的日期区间。

### 4.2 止盈命中率数值变化说明

修复稀释Bug后，止盈命中率仅统计有持仓样本（225条），不再被713条无持仓样本稀释。整体 t20 止盈命中率为 55.56%（125/225），相比修复前的混合口径数值有明显变化，属预期修正效果。

---

## 五、后续路径建议

1. **前端展示（任务5可选）**：在 templates/index.html 回测报告区域新增"可信样本统计"卡片，展示锚点覆盖率和高偏差风险提示。当前API已返回新字段，前端可后续迭代接入。
2. **M8回测引擎协同**：010-3的锚点标记与M8评级有效性回测框架形成互补——010标记的是"当前评级锚点"可信度，M8评估的是"评级方向准确性"。两者数据可交叉分析。
3. **ratings_history积累**：随着每日评级持续写入，锚点覆盖率（confirmed+mismatched）将逐步提升，010-3的标记价值随之增长。

---

## 六、交付物清单

| 交付物 | 路径 | 说明 |
|---|---|---|
| 核心修改 | `modules/price_backtest.py` | 010-1~010-4 全部逻辑（+227行） |
| 表结构迁移 | `database/db_manager.py` | _ensure_price_backtest_columns（+36行） |
| 回测数据 | `price_backtest_results` 表 | 938条记录已 force=True 重跑 |
| 本自验报告 | `reports/dev_selftest_010_backtest_methodology_20260729.md` | 本文档 |

---

## 七、开发声明

1. 10条技术红线零触碰（advisor.py / price_advisor.py / backtest_engine.py / scoring_engine.py / data_collector.py / config_weights.json / data_collector三处if False 等均未修改）。
2. 无新增 pip 依赖（仅使用标准库 sqlite3 + 已有项目模块）。
3. force=True 重跑938条记录全部成功，0 errors。
4. 表结构迁移为幂等操作（ALTER TABLE ADD COLUMN + try-except），可安全重复执行。
5. 本报告数据均为实测结果，未做任何人工调整。
