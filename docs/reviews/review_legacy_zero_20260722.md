# 评审意见：经典引擎股票"有数据却0分"根因分析

| 项目 | 内容 |
|---|---|
| **文档编号** | REVIEW-LEGACY-ZERO-20260722（v2，依据 ARCH-TASK-20260722 任务B 重写） |
| **评审类型** | 根因排查（架构师，响应 ARCH-TASK-20260722 任务B） |
| **评审日期** | 2026-07-22 |
| **评审人** | 架构师（AI） |
| **关联任务** | ARCH-TASK-20260722 任务B / US-04 / US-11 |
| **结论** | **补强（紧急）** — 日报生成与数据采集并发执行，日报先于采集完成→部分股票DB无数据→0分 |

---

## 一、现象描述

### 1.1 PM任务书描述

07-22 日报显示，10 只经典引擎股票评分为 **0.0**（D级），四维数据全空。但 PM 的数据库实测发现核心矛盾：

> "有3个维度数据采集成功，但日报显示四维全空、0分。说明引擎路由或降级逻辑有问题——**有数据却不评分**"

### 1.2 PM提供的数据库证据

| 股票 | kline | fundamental | capital | sentiment | 日报得分 |
|---|---|---|---|---|---|
| HK9988 阿里巴巴 | ✅ | partial | ❌ | ✅ | 0.0 |
| 000858 五粮液 | ✅ | ✅ | ❌ | ✅ | 0.0 |
| HK1810 小米 | ✅ | partial | ❌ | ✅ | 0.0 |
| 002714 牧原 | ✅ | ✅ | ❌ | ✅ | 0.0 |
| 002415 海康威视 | ✅ | ✅ | ❌ | ✅ | 0.0 |
| 000977 浪潮信息 | ✅ | ✅ | ❌ | ✅ | 0.0 |
| 688041 海光信息 | ✅ | ✅ | ❌ | ✅ | 0.0 |
| 688795 摩尔线程 | ✅ | ❌ | ❌ | ✅ | 0.0 |
| 688802 沐曦股份 | ✅ | ❌ | ❌ | ✅ | 0.0 |
| 601012 隆基绿能 | ✅ | ✅ | ❌ | ✅ | 0.0 |

kline 和 sentiment 全部 success，但日报 0 分。

---

## 二、根因定位

### 2.1 根因一（决定性）：日报生成先于数据采集完成

**DB时序实证**（架构师独立验证）：

```
data_status 全维度采集时间窗口：
  kline:       07:04:14 ~ 07:14:20（26只逐只串行）
  fundamental: 07:04:18 ~ 07:14:21
  capital:     07:04:22 ~ 07:14:26
  sentiment:   07:04:22 ~ 07:14:26

daily_reports 生成时间：
  全部8只0分股票: 2026-07-22T07:09:25（同一批次）
```

**关键矛盾解析**：PM 看到的 data_status kline=success 是**真实的**——但这些记录是在日报生成**之后**才写入的。

以 000858（五粮液）为例：

| 事件 | 时间戳 | 说明 |
|---|---|---|
| data_status.capital 记录 | 07:11:03 | **在日报之后 1分38秒** |
| daily_report 生成 | **07:09:25** | 日报在此刻读取DB |
| raw_kline 最新记录 | trade_date=2026-07-21 | 仅昨日数据（07:11才由采集进程写入） |

**结论**：000858 的 kline 数据在 07:09:25（日报时刻）**尚未写入 DB**，分析引擎读到空表→0分。PM 在**事后**查询 data_status 看到 success，产生了"有数据却0分"的表象。

### 2.2 根因二（架构缺陷）：日报流程缺少数据采集步骤

**代码对比**：

| 流程 | 采集步骤 | 分析步骤 | 代码位置 |
|---|---|---|---|
| batch-analyze API | ✅ `collect_stock_data(symbol, market)` | `generate_advice(sid)` | [app.py L995/L999](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\app.py#L995) |
| **daily_report** | ❌ **无** | `generate_advice(stock_id)` | [daily_report.py L340-342](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\daily_report.py#L340) |

[daily_report.py L335-342](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\daily_report.py#L335)：

```python
for stock in stocks:
    stock_id = stock['id']
    symbol = stock['symbol']
    # ❌ 缺少: collect_stock_data(symbol, market)
    try:
        advice = generate_advice(stock_id)   # 直接分析，不采集
```

**全局搜索验证**：`daily_report.py` 中 `collect_stock_data` 和 `fetch_capital_flow_batch` 的匹配数为 **0**。

**后果**：日报仅读取 DB 中已有数据。若没有外部进程（batch-analyze API / 手动触发）先行采集，所有股票都会因 DB 无数据而 0 分。

### 2.3 为什么v5白名单12只有数据、10只没有？

07-22 的数据采集进程（推测为 07:04 手动触发的 batch-analyze）**逐只串行**处理 26 只股票：

| 序号 | 股票 | capital采集时间 | 在07:09:25前完成？ | 日报得分 |
|---|---|---|---|---|
| 1 | 601888 | 07:04:22 | ✅ 是 | 有数据 |
| 2-12 | v5白名单+部分 | 07:04~07:06 | ✅ 是 | 有数据 |
| 13 | 688047 龙芯中科 | 07:09:24 | ✅ 勉强 | 41.1（部分） |
| 14 | 002230 科大讯飞 | 07:09:34 | ❌ 否 | 78.9（历史数据） |
| 15-24 | 000858等8只 | 07:09~07:11 | ❌ 否 | **0.0** |
| 25-26 | HK9988/HK1810 | 07:14 | ❌ 否 | **0.0** |

> 注：002230（科大讯飞）虽在 07:09:34 才完成当日采集，但其 DB 中有**历史测试遗留数据**，所以日报仍读到了部分 K线记录→78.9分。

### 2.4 引擎路由排除

排查任务要求排查"为何降级到经典引擎"——**实际不存在降级**：

- [config_engine_switch.json](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\config_engine_switch.json) mode=`whitelist`
- 白名单含 12 只股票，其余 **设计上就是经典引擎**
- blacklist 为空，无熔断触发

### 2.5 经典引擎空数据处理验证

经典引擎 [_read_kline_data](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\analysis_engine.py#L91) 逻辑：

```python
SELECT trade_date, open, close, high, low, volume, pct_change
FROM raw_kline WHERE stock_id = ?
ORDER BY trade_date DESC LIMIT ?    # 无日期过滤，读最近N条
```

- DB 有记录 → 返回数据 → 计算技术指标 → 非零分
- DB **无记录** → 返回 `[]` → `len([]) < 5` → `insufficient_data` → 该维度 0 分

**确认**：0 分的唯一原因是 DB 在日报时刻无 K线记录，不是引擎 Bug。

---

## 三、影响评估

| 影响面 | 级别 | 说明 |
|---|---|---|
| 用户体验 | 🔴 **致命** | 阿里、五粮液等知名股票日报显示0分"D级建议卖出"，严重误导 |
| 系统可信度 | 🔴 高 | 用户会质疑系统可靠性，影响产品信任 |
| M8回测数据 | 🟡 中 | 0分记录写入 ratings_history，污染回测基线 |
| 变更日志 | 🟡 中 | 0→非0跳变被记录为"评级变更"，触发无意义回测 |

---

## 四、修复方案

### 方案A（推荐·紧急）：日报循环内增加数据采集

在 [daily_report.py generate_daily_report()](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\daily_report.py#L305) 做两处改动：

**改动1：循环前批量预取A股资金面**

```python
from modules.data_collector import collect_stock_data, fetch_capital_flow_batch

a_symbols = [s['symbol'] for s in stocks if s['market'] == 'a_stock']
if a_symbols:
    try:
        batch_result = fetch_capital_flow_batch(a_symbols)
        logger.info(f'[日报] 资金面批量预取: {batch_result}')
    except Exception as e:
        logger.warning(f'[日报] 资金面批量预取失败(不阻断): {e}')
```

**改动2：循环内每只股票先采集后分析**

```python
for stock in stocks:
    stock_id = stock['id']
    symbol = stock['symbol']
    market = stock['market']
    try:
        collect_stock_data(symbol, market)      # 新增
        advice = generate_advice(stock_id)       # 原有
```

**预估工时**：0.5 人天

**与任务A方案A的关系**：完全相同——一个改动同时解决两个问题（capital采集失败 + 0分问题）。

### 方案B（备选）：分离定时采集任务

将数据采集与报告生成分离：
- T-0 17:30：定时采集所有自选股数据（独立定时器）
- T-0 18:00：生成每日报告（读取已采集数据）

**优点**：采集失败不阻断报告生成（可标注"数据采集异常"）
**缺点**：增加复杂度，需两个定时器协调

### 性能考量

| 场景 | 耗时 | 说明 |
|---|---|---|
| 24只逐只采集（K线+基本面+资金面+消息面） | ~8-12分钟 | 含 random.uniform(1.5,3.5)s 延迟 |
| 同花顺批量预取（A股资金面） | ~11秒（1次调用） | THS可用时替代东财逐只 |

日报定时器 18:00 触发（收盘后3小时），用户无感知等待。

### 07-22时序问题专项说明

07-22 的 0 分问题不仅因"日报无采集步骤"，还因**手动触发时序冲突**：

```
07:04  用户手动触发 batch-analyze → 开始逐只采集
07:09  用户手动触发日报生成 → 采集尚未完成
       → 后半段股票无数据 → 0分
```

方案A 实施后，日报自带采集步骤，**不再依赖外部触发**，时序冲突自动消除。

---

## 五、红线核验

| 红线 | 本方案状态 |
|---|---|
| ① 零代码约束 | ✅ 仅调用已有 `collect_stock_data`，不引入新依赖 |
| ② 需求基线 | ✅ §2.4.1 要求"用户手动触发更新后生成报告"，方案使自动报告也包含采集 |
| ③ v5数据契约 | ✅ 无影响 |
| ④ 禁用估算值 | ✅ 采集走真实数据源 |
| ⑤ 防覆盖机制 | ✅ collect_stock_data 内部已有防覆盖逻辑 |
| ⑥ M8→M9顺序 | ✅ 无影响 |
| ⑦ A/H双市场独立 | ✅ collect_stock_data 已按 market 分流 |

---

## 六、结论与建议

| 项 | 结论 |
|---|---|
| **"有数据却0分"的解答** | data_status 中的 success 记录是日报**之后**写入的。日报生成时 DB 中尚无这些股票的数据。**表象矛盾，实际不矛盾** |
| **引擎降级** | ❌ 不存在降级。12只非白名单设计上走经典引擎 |
| **代码根因** | daily_report.py 缺少 `collect_stock_data()` 调用，日报不触发任何数据采集 |
| **运营根因** | 07-22 手动触发 batch-analyze（07:04）与日报生成（07:09）并发，时序冲突 |
| **修复** | 方案A：日报循环内增加采集步骤（0.5人天），与任务A方案A完全合并 |
| **紧急度** | 🔴 **致命** — 影响用户可信度，建议立即修复 |

**报监理批准后交开发执行。任务A/B方案A为同一改动，合并实施。**

---

**编制人**：架构师 | **编制时间**：2026-07-22 | **版本**：v2（依据 ARCH-TASK-20260722 重写）
