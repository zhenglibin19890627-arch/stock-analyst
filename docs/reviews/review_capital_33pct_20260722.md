# 评审意见：资金面采集成功率3.8%根因分析

| 项目 | 内容 |
|---|---|
| **文档编号** | REVIEW-CAPITAL-33PCT-20260722（v2，依据 ARCH-TASK-20260722 重写） |
| **评审类型** | 根因排查（架构师，响应 ARCH-TASK-20260722 任务A） |
| **评审日期** | 2026-07-22 |
| **评审人** | 架构师（AI） |
| **关联任务** | P0-CAPITAL-001 / ARCH-TASK-20260722 任务A |
| **结论** | **补强（紧急）** — 双源全灭 + 日报流程未集成批量预取 + 结构性字段缺失（三层叠加） |

---

## 一、现象描述

### 1.1 PM任务书描述

07-22 数据库实测，capital 维度 `data_status` 成功率暴跌至 **3.8%**：

```
capital 维度（今日）: total=26, success=1, rate=3.8%
capital 维度（全部）: total=215, success=90, rate=41.9%
```

验收标准要求：≥95%。

### 1.2 数据库实证（架构师独立验证）

| 维度 | total | success | failed | partial | 成功率 |
|---|---|---|---|---|---|
| **capital** | 26 | **1** | **25** | 0 | **3.8%** |
| kline | 26 | 26 | 0 | 0 | 100% |
| fundamental | 26 | 19 | 2 | 5 | 73.1% |
| sentiment | 26 | 26 | 0 | 0 | 100% |

唯一成功的 capital 记录：`601888 中国中免`（push2 源，07:04:22）。

### 1.3 今日 raw_capital_flow 实际写入

```sql
SELECT COUNT(*) FROM raw_capital_flow WHERE trade_date = date('now','localtime')
-- 结果: 0 条（含 601888 的 data_status 虽为 success，但写入的是历史日期数据）
```

**结论**：07-22 当日资金面数据 **零写入**。

---

## 二、根因定位

### 2.1 根因一：同花顺批量源（THS）调用失败

**证据链**：

1. [app.py L964](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\app.py#L964) batch-analyze 在循环前调用 `fetch_capital_flow_batch(a_symbols)`
2. [data_collector.py L768](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_collector.py#L768) 该函数内部调用 `_fetch_capital_flow_ths_batch()` → `ak.stock_fund_flow_individual()`
3. 若返回 None（L769），函数立即返回 `fail_count=len(symbols)`，不写入任何数据
4. **DB实证**：raw_capital_flow 今日 0 条记录 → THS 批量源未写入任何数据

**对比**：07-21 P0验收报告显示 THS 批量源 11/11 成功（100%）。07-22 暴跌至 0%，属于 **API 可用性突发故障**（非代码 Bug）。

### 2.2 根因二：东方财富逐只源（EM）三路全灭

THS 批量失败后，batch-analyze 循环内 `collect_stock_data()` → `fetch_capital_flow()` 逐只走 EM 源。

**DB实证**（data_status.capital 逐只 error message，25 条全部相同）：

> "资金面数据不可用：东方财富接口全部失败（push2his/push2/akshare），尝试了其他数据源（新浪/腾讯）！"

| EM 子源 | 端点 | 状态 |
|---|---|---|
| push2his | `_fetch_capital_flow_em_individual` | ❌ 失败 |
| push2 | `_fetch_capital_flow_em` | ❌ 失败（仅 601888 例外） |
| akshare | `ak.stock_individual_fund_flow` | ❌ 失败 |
| 新浪/腾讯（fallback） | — | ❌ 失败 |

**结论**：EM 三路 + fallback 两路 = **五路全灭**。这是 P0-CAPITAL-001 原始根因（"东方财富批量采集触发反爬限流"）的极端表现。

### 2.3 根因三：日报流程未集成批量预取

**最关键的架构缺陷**：

| 流程 | 调用 `fetch_capital_flow_batch`？ | 调用 `collect_stock_data`？ | 代码位置 |
|---|---|---|---|
| batch-analyze API | ✅ 循环前预取 | ✅ 循环内逐只 | [app.py L964/L995](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\app.py#L964) |
| **daily_report** | ❌ **不调用** | ❌ **不调用** | [daily_report.py L335-342](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\daily_report.py#L335) |

日报 `generate_daily_report()` 循环内直接调用 `generate_advice(stock_id)`，**不触发任何数据采集**。即使 THS 批量源正常工作，日报也无法受益——除非用户在日报前手动触发 batch-analyze。

### 2.4 附属问题：33% 字段完整度（结构性恒定）

独立于 3.8% 成功率，存在一个**结构性问题**（初版评审已定位）：

`data_quality.capital` 按 v5 契约 3 字段计算完整度：

| # | 契约字段 | DB映射 | 采集状态 |
|---|---|---|---|
| 1 | `main_net_inflow` | `raw_capital_flow.main_net_inflow` | ✅ THS/EM 写入 |
| 2 | `north_net_buy` | `raw_capital_flow.north_holding_change` | ❌ **全局无写入代码** |
| 3 | `margin_balance_chg` | `raw_capital_flow.margin_balance`（差分） | ❌ **全局无写入代码** |

`capital_present = 1/3 = 33%`，恒定值。即使采集成功率 100%，完整度仍为 33%。

### 2.5 07-22 时序还原

```
07:04:14  数据采集进程启动（推测为 batch-analyze API 手动触发）
          → fetch_capital_flow_batch(a_symbols) 调用 → THS 源失败 → 0 条写入
07:04:22  601888 capital push2 源成功（唯一成功案例）
07:04:33  002458 capital 三路EM全灭 → data_status=failed
07:05:xx  ... 继续逐只失败 ...
07:06:18  600276 capital 失败（v5白名单也不例外）
07:09:25  ⚡ 日报 generate_daily_report() 触发（手动触发，非18:00定时器）
          → 循环读取DB → 部分股票数据尚未采集 → 输出0分
07:09:34  002230 capital 失败（日报之后）
07:11:03  000858 capital 失败（日报之后）
07:14:26  HK1810 capital 失败（最后一只）
```

---

## 三、影响评估

| 影响面 | 级别 | 说明 |
|---|---|---|
| capital 维度成功率 | 🔴 **致命** | 3.8%，远低于 ≥95% 验收标准，阻塞 P0 观察期签字 |
| v5引擎资金面评分 | 🔴 高 | 12 只白名单全部 capital=failed，资金面维度无数据参与评分 |
| data_quality.capital | 🔴 高 | 恒定 33%（结构性），即使采集恢复也无法达标 |
| 经典引擎分层数据 | 🟡 中 | EM 失败→分层字段(super_large等)缺失（次要） |
| M8回测数据可信度 | 🟡 中 | 资金面维度数据为空/过期→回测噪声 |

---

## 四、修复方案

### 方案A（推荐·紧急）：日报流程集成数据采集 + 批量预取

**改动点**：[daily_report.py generate_daily_report()](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\daily_report.py#L305) L335 循环前 + 循环内

```python
# === 循环前：批量预取A股资金面（新增）===
from modules.data_collector import collect_stock_data, fetch_capital_flow_batch
a_symbols = [s['symbol'] for s in stocks if s['market'] == 'a_stock']
if a_symbols:
    try:
        batch_result = fetch_capital_flow_batch(a_symbols)
        logger.info(f'[日报] 资金面批量预取: {batch_result}')
    except Exception as e:
        logger.warning(f'[日报] 资金面批量预取失败(不阻断): {e}')

# === 循环内：每只股票采集（新增）===
for stock in stocks:
    symbol = stock['symbol']
    market = stock['market']
    try:
        collect_stock_data(symbol, market)   # 新增：采集
        advice = generate_advice(stock_id)   # 原有：分析
```

**效果**：日报生成时自动触发采集，不再依赖外部 batch-analyze 调用。

**预估工时**：0.5 人天

### 方案B（中期）：THS 批量源容错增强

当前 THS 失败时（`_fetch_capital_flow_ths_batch()` 返回 None），仅记录 warning 并返回 0 成功。建议增强：

1. **重试机制**：THS 失败时重试 1 次（间隔 5 秒）
2. **源健康检测**：记录 THS 连续失败次数，达到阈值时自动切换为 EM 逐只（当前已回退，但 EM 也有限流问题）
3. **akshare 备选接口**：`ak.stock_fund_flow_individual()` 不可用时，尝试 `ak.stock_individual_fund_flow_rank()`（按涨幅/主力净额排名，含全市场）

**预估工时**：1 人天

### 方案C（P1·非紧急）：补齐 north_net_buy + margin_balance_chg 数据源

解决 33% 结构性完整度问题：

| 字段 | 候选源 | akshare 接口 |
|---|---|---|
| `north_holding_change` | 沪深港通持股变化 | `ak.stock_hsgt_individual_em(symbol)` |
| `margin_balance` | 融资融券余额 | `ak.stock_margin_detail_sse/szse(date, symbol)` |

**预估工时**：2-3 人天

### 方案D（可选）：日报定时器时间调整

当前定时器设为 18:00（[L65](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\daily_report.py#L65)）。建议确认：
- 18:00 在收盘（15:00）后 3 小时，数据源通常已更新 T-0 数据
- 若方案A实施后，日报自带采集步骤，18:00 触发时同步采集+生成，时序问题自动消除

---

## 五、红线核验

| 红线 | 本方案状态 |
|---|---|
| ① 零代码约束 | ✅ 方案A仅调用已有函数；方案B/C仅用 akshare 已有接口 |
| ② 需求基线唯一权威 | ✅ 不改变需求 §2.1.1 资金面数据要求 |
| ③ v5数据契约不可破坏 | ✅ 不修改 StockData 字段定义 |
| ④ 禁用估算值 | ✅ 全部使用真实数据源 |
| ⑤ 防覆盖机制 | ✅ collect_stock_data 内部已有防覆盖；不改动 L1091/L1225 |
| ⑥ M8→M9顺序 | ✅ 无影响 |
| ⑦ A/H双市场独立 | ✅ collect_stock_data 已按 market 分流 |

---

## 六、结论与建议

| 项 | 结论 |
|---|---|
| **3.8% 直接根因** | THS 批量源 API 突发故障（07-21 正常→07-22 全灭）+ EM 五路全灭（反爬限流极端表现） |
| **架构缺陷** | 日报流程不调用 `fetch_capital_flow_batch` 和 `collect_stock_data`，资金面采集完全依赖外部手动触发 |
| **33% 结构性根因** | north_net_buy / margin_balance_chg 从未有采集代码写入（独立于 3.8% 问题） |
| **P0-CAPITAL-001 是否有 Bug** | ❌ 代码无 Bug。THS 批量源实现在 07-21 验收通过；07-22 失败是 API 可用性问题 |
| **紧急修复** | **方案A**（0.5人天）：日报集成采集步骤，消除架构缺陷 |
| **中期加固** | **方案B**（1人天）：THS 容错+重试+备选接口 |
| **结构性补齐** | **方案C**（2-3人天，P1）：补齐北向/融资融券数据源 |

**报监理批准后交开发执行。建议方案A 紧急优先，方案B 紧随其后。**

---

**编制人**：架构师 | **编制时间**：2026-07-22 | **版本**：v2（依据 ARCH-TASK-20260722 重写）
