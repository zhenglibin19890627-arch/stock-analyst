# DEV-TASKS-20260729-010-ARCH：010 回测引擎方法论修复与评级有效性诊断 — 架构方案评审任务书

> **签发人**：PM  | **签发日期**：2026-07-29 | **状态**：待架构师执行

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 推荐模型 | **kimi k3** |
| 窗口类型 | **Quests 独立窗口** |
| 执行模式 | 单代理 agent |
| 交付物 | `docs/reviews/review_010_backtest_methodology_20260729.md` |

---

## 一、需求背景

### 1.1 问题发现过程

PM 在验收 009 后，对 007 价格建议回测数据（price_backtest_results 表，938 条）做了全面诊断，发现**评级与实际市场表现完全倒挂**：

| 评级 | T+20平均最多涨 | T+20平均最多跌 | 涨跌比 | 期望表现 |
|---|---|---|---|---|
| 推荐买入（无持仓） | +6.4% | -6.4% | 1.00:1 | 涨远大于跌 |
| 持有观望（无持仓） | +9.7% | -8.0% | 1.21:1 | 涨跌均衡 |
| 建议减仓（无持仓） | **+15.8%** | -7.9% | **2.00:1** | 跌大于涨 |

**结论：系统最不看好的"建议减仓"股票，反而涨得最猛（20天平均涨15.8%），而最看好的"推荐买入"股票涨跌对半开。**

### 1.2 根因定位

根因在 `price_backtest.py` 第460-469行：

```python
# 当前回测逻辑（有缺陷）
stock_data = load_stockdata_from_db(stock_id)  # 加载全量数据（含未来信息）
analysis = analyze(stock_data)  # 用"现在"的全量数据算评级
rating = analysis.rating  # ← 这是"2026-07-29"的评级！
# 然后把这个"现在的评级"套到过去250天每一个回测点上
```

**问题本质**：被评"建议减仓"的股票，往往是因为前期大涨后到达高位，系统现在判断该减仓。但回测把这些股票**前期上涨段的回测点也标成了"建议减仓"**，造成严重的未来函数偏差（look-ahead bias）。

### 1.3 数据现状约束

| 数据源 | 记录数 | 时间跨度 | 评估 |
|---|---|---|---|
| ratings_history（历史评级） | 200条 | 7月16日~7月28日（仅12天） | ❌ 太稀疏，无法支撑动态评级回测 |
| raw_kline（K线） | ~7000条 | 2025年7月~2026年7月（约260天/股） | ✅ 充足 |
| price_backtest_results（回测结果） | 938条 | 覆盖全K线区间 | ⚠️ 评级为静态当前值 |

**关键约束**：历史评级数据仅有12天，无法对每个历史回测点算出"当时的评级"。

---

## 二、PM 拟定的初步方案（待架构师评审）

### 2.1 四个子任务

| # | 子任务 | 说明 | PM初步思路 |
|---|---|---|---|
| **010-1** | price_backtest 同步 009 动态止盈 | 回测引擎仍在用旧的固定止盈公式，需同步 MIN_TARGET_GAIN + _calc_resistance + 动态止盈公式 | 从 price_advisor.py 复制常量和函数 |
| **010-2** | 修复稀释 Bug | 无持仓样本 take_profit_hit 应为 NULL 而非 0，否则整体止盈命中率被稀释 | _check_hit 函数中 has_position=0 时 take_profit 相关字段设为 None |
| **010-3** | 评级锚点可信样本标记 | 利用 ratings_history 现有200条记录作为"锚点"，在 price_backtest_results 中标记每个回测点是"可信"还是"可疑" | 新增列 rating_confidence（high/medium/low） |
| **010-4** | 基于可信样本重出命中率报告 | 过滤掉可疑样本后，重新统计各评级命中率，判断评级倒挂是否缓解 | 修改报告生成逻辑 |

### 2.2 010-3 的核心思路（评级锚点法）

由于无法对每个历史回测点算动态评级，采用**反向锚定法**：

```
步骤1：取 ratings_history 中每条记录（stock_id, rating_date, rating）
步骤2：对 price_backtest_results 中同一 stock_id 的回测点：
  - 如果 backtest_date 在某条历史评级的前后3个交易日内 → 标记为"可信"（rating_matched）
  - 如果 backtest_date 周围3个交易日无历史评级 → 标记为"可疑"（rating_unknown）
步骤3：如果"可信"样本的评级与回测记录中的 rating 不一致 → 标记为"不匹配"（rating_mismatch）
```

最终三级分类：

| 级别 | 含义 | 统计处理 |
|---|---|---|
| `confirmed` | 回测日附近有历史评级记录且评级一致 | 最可信样本 |
| `mismatched` | 回测日附近有历史评级记录但评级不一致 | 说明评级已变化，当前评级不适用于该回测点 |
| `unknown` | 回测日附近无历史评级记录 | 无法判断 |

---

## 三、当前代码现状

### 3.1 price_backtest.py 回测主循环（L395-560）

```python
def run_price_backtest(market=None, force=False):
    for stock in stocks:
        # 问题：用当前数据加载和计算评级（静态）
        stock_data = load_stockdata_from_db(stock_id)
        analysis = analyze(stock_data)
        rating = analysis.rating  # ← 全部回测点共用这一个评级

        for bt_idx in bt_indices:
            indicators = _calc_historical_indicators(kline_slice)  # 技术指标是历史的
            advice = _gen_price_advice_at_date(indicators, rating, cost_price)  # 评级是当前的
            # 命中判定...
```

### 3.2 price_backtest.py 有持仓止盈算法（L202-230）— 未同步009

```python
def _gen_with_position(close, cost_price, rating):
    # 旧公式：固定止盈（未同步009动态止盈）
    take_profit = cost_price * (1 + target_gain)
    # 009实际系统用的是：max(min_tp, min(fixed_tp, resistance))
```

### 3.3 price_backtest.py 命中判定（L266-340）

```python
def _check_hit(kline_slice, advice, period_label):
    result = {
        f'{period_label}_hit_take_profit': None,  # 字段定义是None
        # ...
    }
    # 但无持仓时 advice['take_profit'] 为 None
    # 导致 high >= None 的判断结果被设为 0 而非保持 None
```

### 3.4 backtest_engine.py（M8引擎，方法论正确但数据少）

```python
# M8引擎用的是历史真实评级（方法论正确）
def run_fixed_period_backtest(self, rating_id):
    rating_row = ...  # 从 ratings_history 取记录
    rating_date = rating_row['rating_date']
    # 查 T+N 收盘价验证
```

M8引擎方法论正确，但ratings_history只有200条记录、12天跨度，无法做T+20验证。

---

## 四、技术红线

| # | 红线 | 说明 |
|---|---|---|
| 1 | **generate_advice 不可改** | advisor.py L869 函数签名和函数体零修改 |
| 2 | **_build_capital_factors 不可改** | advisor.py L785 资金面因子构建函数 |
| 3 | **data_collector 三处 if False** | L1645/L1684/L1717 不可恢复 |
| 4 | **零代码约束** | 无新 pip 依赖（当前8包） |
| 5 | **不回写** | 不修改数据采集逻辑，不自动写入 ratings_history |
| 6 | **config_weights.json** | rating_mapping 不可改 |
| 7 | **scoring_engine.py** | v5引擎不可修改（评分逻辑不变） |
| 8 | **price_advisor.py** | 可修改（009已开放），但本次任务**不是修改price_advisor** |
| 9 | **price_backtest.py** | 本次**核心修改对象**，可修改 |

---

## 五、架构评审决策点

### 决策点1：010-3 评级锚点法的可行性和方案设计

PM 提出了"评级锚点法"（见 2.2），架构师需评估：

| 评估项 | 说明 |
|---|---|
| 方法可行性 | 200条历史评级、27只股票、12天跨度，能覆盖多少回测点？覆盖率是否足够？ |
| 时间窗口选择 | "前后3个交易日"是否合理？太宽会引入噪声，太窄会覆盖率太低 |
| 三级分类标准 | confirmed/mismatched/unknown 的定义是否科学？ |
| 替代方案 | 是否有更好的方法在数据稀疏条件下评估评级有效性？ |
| 偏差风险 | 即使在confirmed样本中，是否仍存在系统性偏差？ |

### 决策点2：010-1 动态止盈同步的范围

price_backtest.py 的 `_gen_with_position` 需要同步 009 的动态止盈算法，架构师需明确：

| 评估项 | 说明 |
|---|---|
| 同步范围 | 需要同步哪些常量和函数？（MIN_TARGET_GAIN, _calc_resistance, 动态止盈公式） |
| 复制 vs 导入 | 继续从 price_advisor.py 复制常量（保持独立性），还是改为 import？PM倾向复制（与现有007风格一致） |
| 回测一致性 | 同步后是否需要 force=True 重跑全部938条？ |

### 决策点3：010-2 稀释Bug的修复方案

| 评估项 | 说明 |
|---|---|
| 修复位置 | 在 _check_hit 函数中，还是在 _gen_price_advice_at_date 返回值中？ |
| 影响范围 | 修复后是否影响已有数据的统计？需要 force=True 重跑？ |
| 向后兼容 | 报告生成逻辑是否需要同步修改（过滤NULL而非0）？ |

### 决策点4：010-3 新增字段的表结构设计

| 评估项 | 说明 |
|---|---|
| 新增列 | `rating_confidence TEXT`（confirmed/mismatched/unknown）还是多列？ |
| 锚点信息 | 是否需要记录匹配到的历史评级日期和评级值？ |
| 幂等性 | ALTER TABLE ADD COLUMN 是否幂等？ |

### 决策点5：回测引擎长期演进方向

架构师需评估以下长期方向并给出优先级建议：

| 方向 | 说明 | PM初步判断 |
|---|---|---|
| A. 积累数据后做完整动态评级回测 | ratings_history积累2个月后，每个回测点用历史动态评级 | 正确但需等待 |
| B. 回测时实时重算历史评级 | 用历史K线切片+历史财务数据重新analyze() | 可能可行但财务数据历史可能不全 |
| C. 两个回测引擎合并 | price_backtest.py 和 backtest_engine.py 合并 | 长期方向但工作量大 |

### 决策点6：010-4 可信样本报告的输出格式

| 评估项 | 说明 |
|---|---|
| 报告结构 | 在现有报告中增加"可信样本子报告"section，还是独立报告？ |
| 最低样本量 | confirmed样本数低于多少时统计无意义？ |
| 展示方式 | 前端是否需要展示可信度标记？ |

### 决策点7：影响面分析

需评估涉及哪些文件修改、改动范围、是否需要前端改动。

---

## 六、交付要求

架构师需输出评审报告 `docs/reviews/review_010_backtest_methodology_20260729.md`，必须包含：

1. **评级锚点法可行性与方案确认/修正**（决策点1）
2. **动态止盈同步方案**（决策点2）
3. **稀释Bug修复方案**（决策点3）
4. **表结构设计方案**（决策点4）
5. **回测引擎长期演进建议**（决策点5）
6. **可信样本报告方案**（决策点6）
7. **影响面分析**（决策点7）
8. **红线合规性确认**
9. **后续开发任务拆分建议**（明确010-1~010-4的优先级和依赖关系）

---

## 七、参考文档

| 文档 | 用途 |
|---|---|
| `docs/reviews/review_007_price_backtest_design_20260728.md` | 007回测方案设计（530行） |
| `docs/reviews/review_009_price_enhance_20260729.md` | 009架构师评审（779行） |
| `modules/price_backtest.py` | 007回测引擎（766行） |
| `modules/price_advisor.py` | 009增强版价格建议（878行，L756-764动态止盈公式） |
| `modules/backtest_engine.py` | M8评级有效性回测引擎（1013行，方法论正确） |
| `reports/accept_007_price_backtest_20260728.md` | 007验收报告 |

---

## 八、监理确认

> 监理确认后，将本任务书内容粘贴到 Quests 独立窗口（kimi k3 模型）执行。
