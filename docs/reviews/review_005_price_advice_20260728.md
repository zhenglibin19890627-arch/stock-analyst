# 评审意见：005 价格建议 — 架构方案评审

| 项目 | 内容 |
|---|---|
| **文档编号** | REVIEW-005-PRICE-ADVICE-20260728 |
| **评审类型** | 架构方案评审（架构师，响应 DEV-TASKS-20260728-005-ARCH） |
| **评审日期** | 2026-07-28 |
| **评审人** | 架构师（AI） |
| **关联需求** | requirements_v1.1.md §2.3.2 操作建议（差异化逻辑） |
| **评审对象** | 价格建议模块（买入区间/目标价/止损价/止盈价/建议仓位） |
| **总体结论** | **方案 A + C 推荐**（新建独立模块 + 后处理集成），算法方案详见 §三 |

---

## 〇、评审基础

### 0.1 评审背景

需求 §2.3.2 要求差异化操作建议：
- **无持仓**：买入价位区间、建议仓位（20%/50%/80%）、目标价、止损价
- **有持仓**：加仓/减仓比例、止盈价位、止损价位（基于持仓成本动态计算）

当前 `generate_advice` 已输出 `action_advice`（文字建议）和 `position_advice`（仓位感知文本），但**未输出具体价格数值**，这是本次需求的核心增量。

### 0.2 核心矛盾

`generate_advice`（advisor.py L869）是 **B24 红线，主入口函数签名和函数体不可修改**。但价格建议需要进入输出结果。**如何在不修改 generate_advice 的前提下集成？**

### 0.3 数据资产验证（架构师独立查库）

| 验证项 | 结果 | 影响 |
|---|---|---|
| raw_kline 表 high/low 字段 | ✅ **存在**（`['id','stock_id','trade_date','open','close','high','low','volume','amount','turnover','pct_change']`） | ATR 止损算法可行 |
| positions 表 cost_price/quantity | ✅ 已有（advisor.py L53-62 已读取） | 有持仓场景可计算止盈/止损 |
| StockData.ma5/ma10/ma20/ma60 | ✅ Optional 字段（data_contract.py L72-75） | 均线支撑/压力位可用 |
| StockData.boll_upper/boll_lower | ✅ Optional 字段（data_contract.py L82-83） | 布林带上下轨可用 |
| StockData.rsi_14 | ✅ Optional 字段（data_contract.py L79） | 超买超卖状态可用 |
| 港股汇率转换 | ✅ 固定汇率 0.92（data_contract.py L42-43） | 价格已统一人民币计价 |

### 0.4 已审阅代码清单

| 文件 | 审阅范围 | 关键内容 |
|---|---|---|
| `docs/tasks/dev_tasks_20260728_005_arch.md` | 全文（193行） | 任务书 7 项决策点 |
| `docs/requirements_v1.1.md` | L120-131 | §2.3.2 需求原文 |
| `modules/advisor.py` | L1-100, L254-301, L538-640, L869-1005 | generate_advice 主入口、_read_position、_build_position_advice、_convert_v5_to_legacy、result 字典组装 |
| `modules/data_contract.py` | L35-124 | StockData 契约（close/ma/boll/rsi/volume） |
| `app.py` | L754-762, L940-949, L1060-1145 | /analyze、/advise、批量分析端点 |
| `modules/daily_report.py` | L440-470 | 日报生成调用链 |
| `templates/index.html` | L2125-2170, L3987-4110 | 前端建议展示区域（评级卡片 + 投资建议详情） |

### 0.5 generate_advice 调用链路（共 5 处）

```
app.py /api/stocks/<id>/advise       → generate_advice → jsonify(result)     [L940-949]
app.py /api/stocks/<id>/analyze      → generate_advice → jsonify(result)     [L754-762]
app.py 批量分析                       → generate_advice → 逐只处理            [L1137]
daily_report.py generate_daily_report → generate_advice → 写入 daily_reports  [L453]
test_us11_consistency.py             → generate_advice → 测试验证
```

---

## 一、决策点 1：模块归属

### 1.1 候选方案对比

| 方案 | 说明 | 优势 | 劣势 |
|---|---|---|---|
| **A** | 新建 `modules/price_advisor.py` 独立模块 | 与现有模块化架构一致（advisor/analysis_engine/scoring_engine 均为独立模块）；可独立测试；不增加 advisor.py 膨胀 | 新增 1 个文件 |
| **B** | 在 advisor.py 新增辅助函数 | 不新增文件 | advisor.py 已 1020 行，继续膨胀违反单一职责；与 generate_advice 同文件增加误触红线风险 |

### 1.2 意见：**推荐方案 A — 新建 `modules/price_advisor.py`**

**理由：**

1. **架构一致性**：项目已有模块划分惯例 — `analysis_engine.py`（分析）、`scoring_engine.py`（评分）、`advisor.py`（建议文本）、`backtest_engine.py`（回测）、`alert_engine.py`（预警）。价格建议是独立的计算逻辑，新建 `price_advisor.py` 完全符合现有模式。

2. **红线安全距离**：将价格建议代码放在独立文件中，物理隔离 B24 红线（advisor.py），降低开发误触风险。

3. **可测试性**：独立模块可单独编写单元测试，不依赖 advisor.py 的复杂调用链。

4. **文件膨胀控制**：advisor.py 已 1020 行，继续添加价格算法（预估 200-300 行）将使文件超过 1300 行，维护困难。

---

## 二、决策点 2：集成方式（核心 — 必须给出推荐）

### 2.1 候选方案对比

| 方案 | 说明 | 改动面 | 数据一致性 | 前端复杂度 |
|---|---|---|---|---|
| **C** | **后处理集成**：app.py 调用层在 generate_advice 之后调用 price_advisor，合并进 JSON | app.py 3 处端点 + daily_report.py 1 处 | 高（同一请求周期内计算） | 低（前端无需额外请求） |
| **D** | **独立 API 端点**：新建 `/api/stocks/<id>/price-advice` | app.py 新增 1 端点 | 中（两次请求可能数据不同步） | 高（前端需 2 次请求 + 状态管理） |
| **E** | **daily_report 集成**：报告生成时调用 price_advisor，写入 daily_reports | daily_report.py + DB 加列 | 高（持久化） | 低（前端从报告读取） |

### 2.2 意见：**推荐方案 C — 后处理集成**

**理由：**

1. **红线零触碰**：generate_advice 函数体完全不改。在 app.py 调用层（generate_advice 返回后）调用 price_advisor，将结果合并进 JSON 响应。这是唯一完全不触碰 B24 红线的集成方式。

2. **数据一致性保障**：价格建议与评级建议在同一请求周期内计算，使用同一份数据快照（相同的 latest_close、相同的 rating），避免方案 D 两次请求间数据变化导致的不一致。

3. **前端零额外请求**：前端当前只需 1 次 `/advise` 或 `/analyze` 请求即可获取完整建议。方案 D 需要 2 次请求，增加前端状态管理复杂度（loading/error/数据合并）。

4. **改动面可控**：仅需修改 app.py 的 3 处端点（/advise、/analyze、批量分析）和 daily_report.py 的 1 处调用点，共 4 处。每处改动模式统一：`result = generate_advice(sid)` → `result['price_advice'] = generate_price_advice(sid, result)` → `return jsonify(result)`。

5. **方案 D 的致命缺陷**：独立端点意味着前端需要先调 `/advise` 获取评级，再调 `/price-advice` 获取价格建议。但价格建议依赖评级结果（仓位映射需要 rating），两次请求间若数据更新，rating 和 price_advice 可能不匹配。

6. **方案 E 的适用场景**：日报持久化可作为方案 C 的**补充**（见 §六 决策点 6），但不作为主要集成方式。

### 2.3 各调用链路集成策略

| 调用点 | 是否需要价格建议 | 集成方式 | 理由 |
|---|---|---|---|
| `/api/stocks/<id>/advise` | ✅ 需要 | 后处理合并 | 用户主动请求建议，需要完整输出 |
| `/api/stocks/<id>/analyze` | ✅ 需要 | 后处理合并 | 与 /advise 同一入口（统一走 generate_advice） |
| 批量分析 | ✅ 需要 | 后处理合并 | 批量结果中每只股票应包含价格建议 |
| `daily_report.py` | ✅ 需要 | 后处理合并 | 日报中应包含价格建议（见 §六） |
| `test_us11_consistency.py` | ❌ 不需要 | 不修改 | 测试验证的是评级一致性，价格建议不影响 |

---

## 三、决策点 3：价格算法方案

### 3.1 算法总览

| 建议项 | 算法 | 数据依赖 | 降级策略 |
|---|---|---|---|
| 买入价位区间 | MA20 ± ATR×0.5 或 BOLL 下轨附近 | ma20, boll_lower, raw_kline(high/low/close) | 无 ma20 → 用 close ± 5% |
| 目标价 | BOLL 上轨 或 MA60 | boll_upper, ma60 | 无 boll_upper → 用 close × 1.10 |
| 止损价（无持仓） | 买入区间下限 - ATR×1.0 | raw_kline(high/low/close) | 无 ATR → 用 close × 0.95 |
| 止盈价（有持仓） | 成本价 × (1 + 目标涨幅%) | positions.cost_price | 固定 +15% |
| 止损价（有持仓） | 成本价 × (1 - 止损比例%) | positions.cost_price | 固定 -7% |
| 建议仓位 | 评级档位映射 | rating | 持有观望 → 0% |

### 3.2 ATR 计算（Average True Range）

**数据基础确认**：raw_kline 表包含 `high`/`low`/`close` 字段，ATR 可行。

```
TR（真实波幅）= max(high - low, |high - prev_close|, |low - prev_close|)
ATR(14) = TR 的 14 日简单移动平均
```

**SQL 取数**：从 raw_kline 取最近 15 天的 high/low/close（14 天 ATR + 1 天 prev_close）。

**降级**：若 raw_kline 不足 15 天，用已有天数计算；若不足 2 天，ATR 不可用，降级为固定百分比。

### 3.3 买入价位区间（无持仓）

**主算法**：
```
买入中枢 = MA20（若可用）否则 close
买入区间下限 = 买入中枢 - ATR × 0.5
买入区间上限 = 买入中枢 + ATR × 0.3
```

**约束**：
- 若 boll_lower 可用且低于买入区间下限，则将下限调整为 boll_lower（布林带下轨是强支撑）
- 买入区间上限不超过 close × 1.05（避免追高）

**降级链**：
1. ma20 + ATR 可用 → 主算法
2. ma20 不可用，ATR 可用 → 用 close 替代 ma20
3. ATR 不可用 → close × 0.97 ~ close × 1.03（固定 ±3%）

### 3.4 目标价（无持仓）

**主算法**：
```
目标价 = max(boll_upper, ma60)  若两者均可用
目标价 = boll_upper              若仅 boll_upper 可用
目标价 = ma60                    若仅 ma60 可用
```

**约束**：目标价不低于 close × 1.05（至少 5% 空间才有买入价值）

**降级**：close × 1.10（固定 10% 目标）

### 3.5 止损价（无持仓）

**主算法**：
```
止损价 = 买入区间下限 - ATR × 1.0
```

**降级**：close × 0.95（固定 -5%）

### 3.6 止盈价（有持仓）

**主算法**：
```
止盈价 = cost_price × (1 + 目标涨幅%)
```

**目标涨幅映射**（与评级联动）：

| 评级 | 目标涨幅 | 理由 |
|---|---|---|
| 强烈推荐买入 | +25% | 高信心，给足空间 |
| 推荐买入 | +20% | 较高信心 |
| 持有观望 | +12% | 中性，保守止盈 |
| 建议减仓 | +8% | 低信心，快速止盈 |
| 强烈建议卖出 | +5% | 极低信心，尽快离场 |

**约束**：止盈价不低于当前 close（若已浮盈超过目标，建议立即止盈）

### 3.7 止损价（有持仓）

**主算法**：
```
止损价 = cost_price × (1 - 止损比例%)
```

**止损比例映射**：

| 评级 | 止损比例 | 理由 |
|---|---|---|
| 强烈推荐买入 | -8% | 高信心，宽容止损 |
| 推荐买入 | -7% | 标准止损 |
| 持有观望 | -5% | 中性，严格止损 |
| 建议减仓 | -4% | 低信心，严格止损 |
| 强烈建议卖出 | -3% | 极低信心，极严格止损 |

**约束**：止损价不低于 close × 0.90（最大亏损不超过 10%）

### 3.8 港股兼容

- 所有价格字段（close/ma/boll）已在 data_adapter 层通过固定汇率 0.92 转换为人民币计价
- ATR 计算基于 raw_kline 的 high/low/close，同样是人民币计价
- **无需额外汇率处理**，算法对 A 股和港股完全透明

---

## 四、决策点 4：仓位映射表

### 4.1 评级 → 建议仓位映射

| 评级 | 总分区间 | 建议仓位 | 说明 |
|---|---|---|---|
| 强烈推荐买入 | 80+ | **80%** | 高信心，重仓 |
| 推荐买入 | 65-79 | **50%** | 较高信心，半仓 |
| 持有观望 | 50-64 | **20%** | 中性，轻仓试探 |
| 建议减仓 | 30-49 | **0%** | 不建议新建仓 |
| 强烈建议卖出 | 0-29 | **0%** | 不建议新建仓 |

### 4.2 与现有 _build_position_advice 的协调

| 维度 | _build_position_advice（现有） | price_advisor（新增） | 关系 |
|---|---|---|---|
| 输出形式 | 自然语言文本 | 结构化数值 | **互补** |
| 内容 | 浮盈浮亏 + 文字建议 | 具体价格 + 仓位比例 | **互补** |
| 持仓感知 | ✅ 有 | ✅ 有 | 一致 |
| 评级联动 | ✅ 有 | ✅ 有 | 一致 |

**协调策略**：
- `position_advice` 继续提供文字描述（"建议持有，适量加仓"）
- `price_advice` 提供具体数值（"买入区间 15.20-16.00，目标价 18.50，止损价 14.80"）
- 前端展示时两者并列，文字建议在上，数值建议在下
- **无冗余**：一个说"做什么"，一个说"在什么价位做"

### 4.3 降级场景

| 场景 | 降级策略 |
|---|---|
| 无 ma20 且无 boll_lower | 买入区间 = close × 0.97 ~ close × 1.03 |
| 无 ATR（raw_kline 不足 2 天） | 止损价 = close × 0.95；买入区间用固定 ±3% |
| 无持仓数据 | 输出无持仓模式（买入区间/目标价/止损价/建议仓位） |
| 有持仓但 cost_price 为 0 | 视为无持仓处理 |
| 港股数据 | 无需特殊处理（汇率已在数据层转换） |

---

## 五、决策点 5：前端展示

### 5.1 展示位置

在报告页面的「投资建议详情」区域（index.html L4062-4107），在「仓位建议」section 之后新增「价格建议」section。

**具体位置**：index.html L4073-4080 的 `position_advice` section 之后，L4082 的 `strongest_dim` section 之前。

### 5.2 展示格式

**无持仓状态**（表格形式）：

```
┌─────────────────────────────────────────┐
│ 💰 价格建议（当前无持仓）                  │
├──────────┬──────────────┬────────────────┤
│ 建议仓位  │    50%       │ 评级：推荐买入   │
│ 买入区间  │ 15.20 - 16.00│ 当前价：15.85   │
│ 目标价    │    18.50     │ 预期涨幅：+16.7% │
│ 止损价    │    14.80     │ 最大回撤：-6.6%  │
└──────────┴──────────────┴────────────────┘
```

**有持仓状态**（表格形式）：

```
┌─────────────────────────────────────────┐
│ 💰 价格建议（持仓中）                      │
├──────────┬──────────────┬────────────────┤
│ 止盈价    │    18.50     │ 成本价：15.00   │
│ 止损价    │    13.95     │ 当前价：15.85   │
│ 操作建议  │  加仓 20%    │ 浮盈：+5.7%    │
└──────────┴──────────────┴────────────────┘
```

### 5.3 列表页摘要

列表页（自选股总览看板）**暂不展示**价格建议摘要。理由：
- 列表页空间紧凑，价格区间文本（如"15.2-16.0"）信息量低
- 用户需点击进入详情页才能看到完整价格建议
- 避免列表页信息过载

### 5.4 前端改动范围

| 文件 | 改动内容 | 预估行数 |
|---|---|---|
| `templates/index.html` | 在「投资建议详情」区域新增 price_advice section 渲染逻辑 | +40 行 |
| `templates/index.html` | 新增 price_advice 的 CSS 样式（表格/卡片） | +30 行 |

**参考**：003 超买超卖徽标的改动模式（在现有卡片中新增 section），本次改动模式相同。

---

## 六、决策点 6：日报/回测集成

### 6.1 日报集成

**建议**：日报生成时调用 price_advisor，将价格建议**作为 JSON 字段**写入 daily_reports 表。

**实现方式**：
- daily_report.py L453 在 `advice = generate_advice(stock_id)` 之后，调用 `price_advice = generate_price_advice(stock_id, advice)`
- 将 `price_advice` 字典序列化为 JSON 字符串，存入 daily_reports 表的 `price_advice` 列（新增列）
- 前端从 `/api/stocks/<id>/report-latest` 读取日报时，price_advice 随日报一同返回

**DB 变更**：
```sql
ALTER TABLE daily_reports ADD COLUMN price_advice TEXT;  -- JSON 字符串
```

### 6.2 回测集成

**建议**：**暂不集成**。

理由：
- 价格建议的准确性验证需要定义"成功"标准（如买入后 N 天内达到目标价）
- 当前回测框架（backtest_engine.py）验证的是评级变更后的价格走势，非价格建议的命中率
- 价格建议回测可作为后续独立任务（M9 或 P4 阶段）

### 6.3 持久化策略

| 数据 | 持久化 | 存储位置 | 理由 |
|---|---|---|---|
| 实时价格建议（/advise, /analyze） | ❌ 不持久化 | 仅内存计算 | 实时请求，无需存储 |
| 日报价格建议 | ✅ 持久化 | daily_reports.price_advice 列 | 日报需可追溯 |
| 批量分析价格建议 | ❌ 不持久化 | 仅响应 JSON | 批量结果为临时查询 |

---

## 七、决策点 7：边界与降级

### 7.1 数据不足降级策略

| 场景 | 降级策略 | 输出示例 |
|---|---|---|
| ma20 缺失 | 用 close 替代 ma20 作为买入中枢 | 买入区间 = close ± ATR×0.5 |
| boll_upper/boll_lower 缺失 | 目标价 = close × 1.10 | 固定 10% 目标 |
| ATR 不可用（raw_kline < 2 天） | 止损价 = close × 0.95 | 固定 -5% 止损 |
| ma20 + boll + ATR 全缺失 | 买入区间 = close × 0.97 ~ close × 1.03 | 固定 ±3% |
| 持仓 cost_price = 0 或 NULL | 视为无持仓 | 输出无持仓模式 |
| 港股 | 无需特殊处理 | 汇率已在数据层转换 |

### 7.2 免责声明

**必须添加**。在价格建议 section 底部添加固定文本：

```
⚠️ 以上价格建议仅供参考，不构成投资建议。股市有风险，投资需谨慎。
```

**展示位置**：价格建议表格下方，小字号灰色文本。

### 7.3 极端场景处理

| 场景 | 处理策略 |
|---|---|
| 停牌（close = 0 或 NULL） | 不输出价格建议，返回 `{'available': False, 'reason': '停牌或数据不足'}` |
| 涨停/跌停 | 正常输出，但在风险提示中追加"当前处于涨跌停状态，注意流动性风险" |
| 新股上市（< 5 天数据） | ATR 不可用，降级为固定百分比 |
| ST 股票 | 正常输出，但在风险提示中追加"ST 股票，注意退市风险" |

---

## 八、影响面分析

### 8.1 文件修改清单

| 文件 | 改动类型 | 改动内容 | 预估行数 |
|---|---|---|---|
| `modules/price_advisor.py` | **新建** | 价格建议计算模块（ATR 计算、买入区间、目标价、止损价、止盈价、仓位映射） | ~250 行 |
| `app.py` | **修改** | /advise 端点（L940-949）：generate_advice 后追加 price_advice 合并 | +5 行 |
| `app.py` | **修改** | /analyze 端点（L754-762）：同上 | +5 行 |
| `app.py` | **修改** | 批量分析（L1137）：同上 | +5 行 |
| `modules/daily_report.py` | **修改** | 日报生成（L453）：generate_advice 后追加 price_advice 计算 + 写入 daily_reports | +10 行 |
| `templates/index.html` | **修改** | 投资建议详情区域新增 price_advice section 渲染 + CSS | +70 行 |
| `database/db_manager.py` | **修改** | daily_reports 表新增 price_advice 列（ALTER TABLE） | +5 行 |

**总计**：新建 1 个文件，修改 5 个文件，新增约 350 行代码。

### 8.2 不修改的文件（红线保护）

| 文件 | 原因 |
|---|---|
| `modules/advisor.py` | B24 红线，generate_advice 主入口不可改 |
| `modules/data_collector.py` | L1645/L1684/L1717 三处 if False 不可改 |
| `config_weights.json` | rating_mapping 80/65/50/30 不可改 |
| `modules/data_contract.py` | StockData 契约不可破坏 |
| `modules/scoring_engine.py` | 评分逻辑与价格建议无关 |

---

## 九、红线合规性确认

| # | 红线 | 合规性 | 说明 |
|---|---|---|---|
| 1 | **零代码约束** | ✅ 合规 | 无新 pip 依赖，仅用标准库 + sqlite3 |
| 2 | **需求基线唯一权威** | ✅ 合规 | 方案完全对齐 requirements_v1.1.md §2.3.2 |
| 3 | **generate_advice 红线** | ✅ 合规 | 函数签名和函数体零修改，采用后处理集成 |
| 4 | **data_collector 三处 if False** | ✅ 合规 | 不触碰 data_collector.py |
| 5 | **config_weights.json** | ✅ 合规 | 不修改 rating_mapping |
| 6 | **data_contract.py** | ✅ 合规 | 仅读取 StockData 字段，不修改契约 |
| 7 | **A/H 双市场兼容** | ✅ 合规 | 汇率已在数据层转换，算法透明 |
| 8 | **不回写** | ✅ 合规 | 纯计算输出，不修改数据采集逻辑，不新增采集调用 |

---

## 十、风险点和注意事项

### 10.1 技术风险

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| ATR 计算依赖 raw_kline 数据质量 | 中 | 降级链完善（ATR → 固定百分比） |
| 价格建议与评级建议不一致 | 低 | 同一请求周期内计算，使用同一数据快照 |
| 前端表格渲染在移动端溢出 | 低 | 使用响应式表格（参考现有 advice-card 样式） |
| daily_reports 表加列失败 | 低 | ALTER TABLE 是标准 SQLite 操作，已有先例 |

### 10.2 业务风险

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| 用户过度依赖价格建议 | 中 | 强制免责声明 + 风险提示 |
| 价格建议在极端行情下失效 | 中 | 降级策略 + 涨跌停风险提示 |
| 港股波动特征与 A 股不同 | 低 | 汇率已转换，算法参数对两市场通用 |

### 10.3 注意事项

1. **price_advisor 的输入**：应接收 `generate_advice` 的返回结果（含 rating/has_position/latest_close），而非重新查询数据库。避免重复查询和数据不一致。

2. **ATR 计算的 SQL 优化**：从 raw_kline 取最近 15 天数据时，使用 `ORDER BY trade_date DESC LIMIT 15`，避免全表扫描。

3. **前端展示的条件渲染**：当 `price_advice.available = False` 时（数据不足），前端应显示"数据不足，暂无价格建议"而非空白。

4. **日报 price_advice 列的 JSON 格式**：建议使用紧凑 JSON（无空格），减少存储空间。

---

## 十一、对后续开发任务书的建议

### 11.1 拆分粒度

建议拆分为 **2 个子任务**：

| 子任务 | 内容 | 预估工作量 | 依赖 |
|---|---|---|---|
| **005-DEV-A** | 后端算法实现：新建 price_advisor.py + app.py 端点集成 + daily_report.py 集成 + DB 加列 | 1 天 | 无 |
| **005-DEV-B** | 前端展示：index.html 价格建议 section 渲染 + CSS + 免责声明 | 0.5 天 | 005-DEV-A 完成 |

### 11.2 验收标准建议

**005-DEV-A 验收标准**：
1. `/api/stocks/<id>/advise` 返回 JSON 中包含 `price_advice` 字段
2. 无持仓时输出：buy_range_low/buy_range_high/target_price/stop_loss/position_pct
3. 有持仓时输出：take_profit/stop_loss/action_suggestion
4. 数据不足时输出：`available: false` + 降级原因
5. 日报生成后 daily_reports 表 price_advice 列有值

**005-DEV-B 验收标准**：
1. 报告页面「投资建议详情」区域显示价格建议表格
2. 无持仓/有持仓两种状态展示正确
3. 免责声明显示在价格建议下方
4. 移动端显示正常（无溢出）

---

## 十二、总体结论

### 12.1 推荐方案汇总

| 决策点 | 推荐 | 理由 |
|---|---|---|
| 1. 模块归属 | **A：新建 modules/price_advisor.py** | 架构一致性、红线安全距离、可测试性 |
| 2. 集成方式 | **C：后处理集成** | 红线零触碰、数据一致性、前端零额外请求 |
| 3. 价格算法 | ATR + MA/BOLL 组合算法 | 数据基础已确认（raw_kline 有 high/low） |
| 4. 仓位映射 | 80+→80%, 65+→50%, 50+→20%, <50→0% | 与评级档位对齐 |
| 5. 前端展示 | 报告页「投资建议详情」新增 section | 改动模式与 003 超买超卖徽标一致 |
| 6. 日报集成 | 日报持久化 price_advice JSON | 可追溯性 |
| 7. 边界降级 | 完善降级链 + 免责声明 | 数据不足时优雅降级 |

### 12.2 架构师声明

- 以上所有结论基于对 `advisor.py`、`data_contract.py`、`app.py`、`daily_report.py`、`templates/index.html` 的**实际代码审阅**和**数据库实证查询**
- raw_kline 表 high/low 字段已通过 `PRAGMA table_info(raw_kline)` 确认存在
- 推荐方案（A + C）确保 B24 红线零触碰，generate_advice 函数签名和函数体完全不变
- 价格算法参数（ATR 系数、目标涨幅、止损比例）为初始建议值，可在开发阶段根据回测数据微调

---

*评审完毕。如需 PM 对任何决策点进行二次讨论或要求架构师补充分析，请反馈。*
