# 测试用例：M8-BACKTEST-003 评级有效性监测（回测）框架

| 项目 | 内容 |
|---|---|
| **用例编号** | TC-M8-003 |
| **关联任务** | DEV-TASKS-20260721 任务卡3 + QA-TASK-20260722 任务C |
| **需求基线** | `docs/requirements_v1.1.md` §2.8（评级有效性监测） |
| **验收标准** | ① 429行 ratings_history 全量回测完成；② backtest_results 填充率100%；③ A/H双市场独立回测报告可查看；④ 评级变更自动触发回测验证通过 |
| **设计方** | QA（质量保障） |
| **设计日期** | 2026-07-22 |
| **状态** | 测试用例预编制（待执行） |

---

## 一、测试范围与判定依据

### 1.1 需求基线要点（§2.8）

- 固定周期回测：评级后 1d / 1w / 1m 股价表现
- 动态周期回测：评级发布至下一次评级变更期间表现
- 触发条件：每次评级变更自动触发回测
- A股和港股分别独立回测
- 对比基准：仅对比个股自身历史表现，不与大盘/行业指数比较
- 判定逻辑：买入评级后股价上涨=正确，卖出评级后股价下跌=正确
- 永久存储，A/H分别存储可分别查看

### 1.2 ⚠️ 关键语义澄清（验收标准①②）

任务书验收标准①"429行全量回测"与②"填充率100%"存在**语义精度问题**，QA 据代码实测锚点澄清如下：

- [backtest_engine.py L337](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py)：`batch_backtest()` 仅回测 `price_at_rating IS NOT NULL AND price_at_rating > 0` 的记录
- [app.py L2818](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\app.py)：`/api/backtest/status` 的 coverage = `backtest_results数 / 有价格的ratings数`

**QA 判定口径**：
- "全量回测"= 所有 `price_at_rating > 0` 的评级记录都被处理（非全部行数）
- "填充率100%"= `coverage = total_backtests / total_ratings_with_price = 1.0`（非要求 price_1d/1w/1m 全部非空）
- 若存在 `price_at_rating IS NULL` 的评级记录，应**单独统计并标注**，不计入失败

> ⚠️ **数据基线更新（2026-07-22）**：产品经理 QA-TASK-20260722 任务C 第1项标注 `ratings_history` 已增至 **501行**（07-20 方案时为429行）。灰度已从12只扩面至 **26只**。验收标准①以最新501行为准。

### 1.3 判定矩阵（实测锚点）

[backtest_engine.py L40-76](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py) `JUDGEMENT_MATRIX` + `_judge()`：

| 评级 | 方向 | 正确阈值 | 错误阈值 | 中间区 |
|---|---|---|---|---|
| 强烈推荐买入 | up | return≥+2% | return≤-3% | 其余=中性(None) |
| 推荐买入 | up | return≥+1% | return≤-2% | 其余=中性 |
| 持有观望 | neutral | -3%≤return≤+3% | 超出即错误(0) | 无中性区 |
| 建议减仓 | down | return≤-1% | return≥+3% | 其余=中性 |
| 强烈建议卖出 | down | return≤-1% | return≥+3% | 其余=中性 |

### 1.4 双档位兼容机制

`run_fixed_period_backtest` 调用 `normalize_rating(rating_raw, total_score)` 归一化历史 A/B+/B/C/D，再进 JUDGEMENT_MATRIX。

---

## 二、正常场景测试

### 2.1 固定周期回测（T+1 / T+5 / T+20）

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-FT-01 | 取一条历史评级记录（有 price_at_rating，且 T+1 有K线），调 `run_fixed_period_backtest(rating_id)` | 返回 `success=True`，含 `price_1d/return_1d/is_correct_1d` 字段 | P0 |
| TC-M8-FT-02 | 同上，验证 `return_1d` 计算公式 | `return_1d = round((price_1d - price_at)/price_at*100, 2)`，精度2位小数 | P0 |
| TC-M8-FT-03 | 验证 `is_correct` 主判定逻辑（[L218-222](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py)） | 优先取 1d，None 时回退 1w，再 None 回退 1m | P1 |
| TC-M8-FT-04 | 验证 T+5（1周）周期 | `price_1w/return_1w/is_correct_1w` 正确填充 | P0 |
| TC-M8-FT-05 | 验证 T+20（1月）周期 | `price_1m` 字段存在（数据不足时可为 None，见边界测试） | P1 |

### 2.2 动态周期回测

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-DY-01 | 取一只有多条 is_change=1 的股票（如 HK3690 有19次变更），回测变更前的评级 | `dynamic_end_date/dynamic_return/dynamic_is_correct` 正确填充 | P0 |
| TC-M8-DY-02 | 验证动态周期终点 = 下一次 is_change=1 的评级日（[L286-291](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py)） | end_date 为评级日之后首个变更日 | P1 |
| TC-M8-DY-03 | 动态收益计算 | `dynamic_return = round((end_price - price_at)/price_at*100, 2)` | P1 |

### 2.3 市场级回测报告

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-MR-01 | `compute_market_report('a_stock')` | 返回 total/accuracy/rating_stats/period_accuracy/dynamic_accuracy 等 | P0 |
| TC-M8-MR-02 | 验证分级准确率 `rating_stats` | 按评级档位分组，含 total/correct/wrong/neutral/accuracy | P0 |
| TC-M8-MR-03 | 验证周期准确率 `period_accuracy` | 含 1d/1w/1m 三个周期的 correct/wrong/accuracy | P1 |
| TC-M8-MR-04 | 验证 `date_range` 字段 | 格式为 `起 ~ 止`，反映实际评级日范围 | P2 |

### 2.4 个股回测明细

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-SD-01 | `compute_stock_detail(stock_id)` 取一只有多条评级的股票 | 返回 records 数组，含每次评级+回测结果 | P0 |
| TC-M8-SD-02 | 无回测数据的股票 | 返回 `{'success': False, 'message': '该股票暂无回测数据'}`，不抛异常 | P1 |

---

## 三、A/H 双市场独立测试（验收标准③）

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-MK-01 | `compute_market_report('a_stock')` 与 `compute_market_report('hk_stock')` 分别调用 | 两者 total 之和 = 全部回测记录数，无交叉污染 | P0 |
| TC-M8-MK-02 | `batch_backtest(market='hk_stock')` | 仅回测港股评级，A股记录不受影响 | P0 |
| TC-M8-MK-03 | `/api/backtest/status` 的 `market_distribution` | 含 a_stock/hk_stock 两个 key，计数独立 | P0 |
| TC-M8-MK-04 | 港股 HK3690 回测结果 market 字段 | 值为 `hk_stock`（非 a_stock） | P1 |
| TC-M8-MK-05 | A股（如 600276）回测结果 market 字段 | 值为 `a_stock` | P1 |

---

## 四、评级变更自动触发测试（验收标准④）

> 代码锚点：[advisor.py L731-738](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\advisor.py)，`is_changed=True` 时调 `auto_trigger_backtest`。

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-TR-01 | 构造一次评级变更（is_changed=True），触发 generate_advice | `auto_trigger_backtest` 被调用，返回 `success=True` | P0 |
| TC-M8-TR-02 | 评级**未变更**（is_changed=False） | **不应**触发 auto_trigger_backtest | P0 |
| TC-M8-TR-03 | 自动触发失败（如数据库锁） | 主流程不受影响，仅记录 warning 日志（[L738](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\advisor.py) try/except） | P0 |
| TC-M8-TR-04 | `auto_trigger_backtest` 内部 rating 记录不存在 | 返回 `{'success': False, 'error': 'rating record not found'}` | P1 |
| TC-M8-TR-05 | T+1 数据尚不可用（评级当日回测） | 仍写入 backtest_results，price_1d 可为 None，不阻塞（符合方案场景A"等待T+1后补算"） | P1 |

### 4.1 定时补算（fill_pending_backtests）

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-FL-01 | `fill_pending_backtests()` 补算到期未回测记录 | 返回 `{pending, filled}`，filled ≤ pending | P1 |
| TC-M8-FL-02 | 连续调用两次 fill_pending | 第二次 pending=0（已补算，幂等） | P2 |

---

## 五、双档位判定矩阵兼容测试

| 用例ID | 输入评级（DB原始） | total_score | 归一化后 | 判定矩阵匹配 | 优先级 |
|---|---|---|---|---|---|
| TC-M8-DM-01 | 'A' | 88 | 强烈推荐买入 | 命中 up方向(≥+2%正确) | P0 |
| TC-M8-DM-02 | 'B+' | 75 | 推荐买入 | 命中 up方向(≥+1%正确) | P0 |
| TC-M8-DM-03 | 'B' | 60 | 持有观望 | 命中 neutral方向(-3%~+3%正确) | P0 |
| TC-M8-DM-04 | 'C' | 55 | 持有观望 | 与 'B' 同档判定（字符串映射合并） | P1 |
| TC-M8-DM-05 | 'D' | 40 | 建议减仓 | 命中 down方向(≤-1%正确) | P0 |
| TC-M8-DM-06 | '强烈推荐买入' | 90 | 强烈推荐买入（原样） | 命中矩阵 | P1 |
| TC-M8-DM-07 | 未知评级字符串 'X' | None | 返回 'X'（不崩），`_judge` 返回 None（中性） | P2 |

### 5.1 判定矩阵逻辑验证（_judge）

| 用例ID | rating_norm | return_pct | 预期 is_correct | 优先级 |
|---|---|---|---|---|
| TC-M8-JG-01 | 强烈推荐买入 | +3.0 | 1（正确，≥+2%） | P0 |
| TC-M8-JG-02 | 强烈推荐买入 | +1.5 | None（中性，未达+2%未破-3%） | P0 |
| TC-M8-JG-03 | 强烈推荐买入 | -4.0 | 0（错误，≤-3%） | P0 |
| TC-M8-JG-04 | 持有观望 | +1.0 | 1（正确，-3%~+3%内） | P0 |
| TC-M8-JG-05 | 持有观望 | +5.0 | 0（错误，超出+3%） | P0 |
| TC-M8-JG-06 | 建议减仓 | -2.0 | 1（正确，≤-1%） | P0 |
| TC-M8-JG-07 | 建议减仓 | +1.0 | None（中性，未破+3%未达-1%） | P1 |
| TC-M8-JG-08 | (任一) | None | None（收益率缺失不判定） | P1 |

> **执行建议**：TC-M8-JG 系列可直接调用 `_judge(rating_norm, return_pct)` 单元测试，无需数据库，建议作为冒烟集。

---

## 六、全量回测与填充率测试（验收标准①②）

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-FU-01 | 调 `batch_backtest()`（market=None, force=True）全量回测 | 返回 `{total, success, errors}`，errors 应为0或极少 | P0 |
| TC-M8-FU-02 | 调 `/api/backtest/status` 查看 coverage | `coverage = total_backtests / total_ratings_with_price` 应 = 1.0（有价格的评级全部回测） | P0 |
| TC-M8-FU-03 | 统计 `price_at_rating IS NULL` 的评级记录数 | 单独标注"无价格未回测记录数=N"，不计入失败 | P0 |
| TC-M8-FU-04 | 重跑 `batch_backtest(force=True)` | UPSERT 更新已有记录（[L233-251](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py)），**不产生重复行** | P0 |
| TC-M8-FU-05 | 重跑 `batch_backtest(force=False)` | 跳过已有结果的记录（[L349-350](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py)），success=0 | P1 |

---

## 七、边界场景测试

### 7.1 数据不足（T+20 超出范围）

| 用例ID | 场景 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-BD-01 | 评级日距今不足20交易日，T+20 无K线 | `price_1m=None, return_1m=None`，不报错（符合方案7.5.3 insufficient_data） | P0 |
| TC-M8-BD-02 | 评级日为最新交易日，T+1 尚无数据 | `price_1d=None`，回测仍写入，is_correct 主判定回退到 1w/1m | P1 |

### 7.2 无变更记录（动态回测空链）

| 用例ID | 场景 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-BD-03 | 顺丰控股(002352) 0次变更（方案7.5.1标注） | `dynamic_end_date=None`，动态回测返回空，固定回测正常 | P1 |

### 7.3 异常输入

| 用例ID | 场景 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-BD-04 | `run_fixed_period_backtest(999999)`（不存在的 rating_id） | 返回 `{'success': False, 'error': 'rating_id=999999 not found'}`，不抛异常 | P0 |
| TC-M8-BD-05 | `price_at_rating = 0` 的记录 | 被 batch_backtest 的 WHERE 条件过滤，不参与回测 | P1 |
| TC-M8-BD-06 | `compute_stock_detail(999999)`（不存在的 stock_id） | 返回 `{'success': False, 'message': '该股票暂无回测数据'}` | P1 |

### 7.4 小样本警告

| 用例ID | 场景 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-BD-07 | 市场报告 total < 30 | `small_sample_warning = True`，`sample_period_note` 含"样本期"标注（方案7.5.3） | P1 |
| TC-M8-BD-08 | 个股明细 total < 10 | `small_sample_warning = True` | P2 |

---

## 八、D4 权重实验场景测试

> 代码锚点：[backtest_engine.py L612-724](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py) `WeightExperimentRunner`。

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-WE-01 | `list_experiments()` | 返回 ≥2 个实验（d4_news_0_to_20, d4_hk_news_boost） | P1 |
| TC-M8-WE-02 | `run_experiment('d4_news_0_to_20')` | 返回 control_accuracy/experiment_accuracy/delta_accuracy | P1 |
| TC-M8-WE-03 | 无回测数据时运行实验 | 返回 note="当前无回测数据"，delta_accuracy=None（不崩溃） | P2 |
| TC-M8-WE-04 | `run_experiment('not_exist')` | 返回 `{'success': False, 'error': 'experiment not_exist not found'}` | P2 |
| TC-M8-WE-05 | **关键边界**：实验是否修改生产权重 | 仅模拟计算，config_weights.json / config.py 不被修改（方案4.4重要边界） | P0 |

---

## 九、表结构迁移与幂等测试

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-DD-01 | `BacktestEngine()` 初始化 | `_ensure_columns()` 为 backtest_results 添加 dynamic_end_date/dynamic_return/dynamic_is_correct 列 | P1 |
| TC-M8-DD-02 | 重复初始化（列已存在） | ALTER TABLE 不重复执行（[L94-100](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py) PRAGMA table_info 判断），幂等无报错 | P1 |

---

## 十、零代码约束与回归测试

| 用例ID | 场景 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-M8-ZC-01 | M8 是否引入 requirements.txt 之外依赖 | backtest_engine.py 仅 import 标准库 + 项目内模块，无新 pip 依赖 | P0 |
| TC-M8-ZC-02 | 回测结果是否在 Web 界面可视化 | /api/backtest/* 接口可访问，用户无需查数据库 | P1 |
| TC-M8-RG-01 | 既有功能回归：batch-analyze 12只白名单 | 正常生成，评级变更触发回测不阻塞主流程 | P0 |
| TC-M8-RG-02 | 数据库既有数据不丢失 | 回测不删除/覆盖 ratings_history 原始数据（仅读取） | P0 |
| TC-M8-RG-03 | backtest_results 永久存储 | 重跑为 UPSERT 更新，不 DELETE 既有行 | P0 |

---

## 十一、执行说明

- **冒烟集（建议先行，无数据库依赖）**：TC-M8-JG-01~08（`_judge` 单元）、TC-M8-DM-01~07（`normalize_rating` 单元）
- **核心集（需数据库）**：TC-M8-FT / TC-M8-MK / TC-M8-TR / TC-M8-FU
- **P0 必过，任一失败即驳回**
- 执行顺序：冒烟集 → 表结构迁移(九) → 全量回测(六) → 正常场景(二、三、四) → 自动触发(四) → 边界(七) → D4实验(八)

**设计人**：QA | **设计日期**：2026-07-22
