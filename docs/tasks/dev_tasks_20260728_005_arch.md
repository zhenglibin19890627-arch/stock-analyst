# DEV-TASKS-20260728-005-ARCH：005 价格建议 — 架构方案评审任务书

> **签发人**：PM ｜ **签发日期**：2026-07-28 ｜ **状态**：待架构师执行

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 推荐模型 | **kimi k3**（涉及红线约束的复杂架构决策） |
| 窗口类型 | **Quests 独立窗口** |
| 执行模式 | 单代理 agent |
| 交付物 | `docs/reviews/review_005_price_advice_20260728.md` |

---

## 一、需求背景

**需求来源**：`docs/requirements_v1.1.md` §2.3.2 操作建议（差异化逻辑）

### 无持仓状态
- 聚焦买入时机判断
- 给出具体买入建议：**买入价位区间**、**建议仓位**（如 20%/50%/80%）
- 给出**目标价**、**止损价**

### 有持仓状态
- 支持加仓、减仓、止盈、止损等动态操作建议
- 止盈线、止损线基于持仓成本动态计算
- 给出具体操作建议：**加仓比例**、**减仓比例**、**止盈价位**、**止损价位**

### 当前差距
`advisor.py` 的 `generate_advice` 已输出：
- `action_advice`：买入/持有/减仓等文字建议
- `position_advice`：仓位感知文本（含浮盈浮亏、市值）

但**未输出具体价格区间**（买入价位区间、目标价、止损价、止盈价等），这是本次需求的核心增量。

---

## 二、核心矛盾与约束

### 核心矛盾（评审重点）

`generate_advice`（`advisor.py` L869）是 **B24 红线，禁止修改主入口**。
但价格建议需要进入输出结果，**如何在不修改 generate_advice 的前提下集成？**

> 这是本次架构评审的核心决策点，架构师必须给出明确推荐方案。

### 硬约束清单

| # | 约束 | 说明 |
|---|---|---|
| 1 | **红线** | `advisor.py` 的 `generate_advice` 主入口函数签名和函数体不可修改（B24） |
| 2 | **红线** | `data_collector.py` L1645/L1684/L1717 三处 `if False` 不可修改 |
| 3 | **零代码约束** | 无新 pip 依赖，仅用标准库 + 已有依赖（pandas/numpy 等） |
| 4 | **不回写** | 价格建议为纯计算输出，不修改数据采集逻辑，不新增采集调用 |
| 5 | **rating_mapping** | 80/65/50/30 评级边界不可修改 |
| 6 | **港股兼容** | 价格建议算法需兼容 A 股和港股（港股已有固定汇率转换） |

---

## 三、已有数据资产盘点

### StockData 契约（`modules/data_contract.py`）可用字段

| 类别 | 字段 | 说明 |
|---|---|---|
| 价格 | `close` | 最新收盘价（必填，统一人民币计价） |
| 均线 | `ma5`, `ma10`, `ma20`, `ma60` | 各周期均线价格 |
| 布林带 | `boll_upper`, `boll_lower` | 布林带上下轨 |
| 成交量 | `volume`, `volume_ratio` | 成交量与量比 |
| RSI | `rsi_14` | 14日RSI（004已修复为Wilder算法） |

### 数据库表

| 表 | 可用字段 | 用途 |
|---|---|---|
| `raw_kline` | `close`, `high`, `low`, `trade_date` | 计算 ATR / 历史波动率 / 支撑压力位 |
| `positions` | `cost_price`, `quantity` | 持仓信息（已有 `_read_position` 读取） |
| `ratings_history` | `price_at_rating`, `rating` | 评级时价格 |

> 注意：raw_kline 表是否有 `high`/`low` 字段，请架构师确认（影响 ATR 计算方案）。

### 现有调用链路（generate_advice 被 6+ 处调用）

```
app.py /api/stocks/<id>/advise       → generate_advice → jsonify(result)
app.py /api/stocks/<id>/analyze      → generate_advice → jsonify(result)
app.py /api/stocks/<id>/report-latest→ 从 daily_reports 表读取（非直接调 generate_advice）
app.py 批量分析                       → generate_advice → 逐只处理
daily_report.py generate_daily_report → generate_advice → 写入 daily_reports 表
test_us11_consistency.py             → generate_advice → 测试验证
```

返回的 `result` 字典当前包含：`action_advice`, `advice_detail`, `position_advice`, `risk_warnings`, `latest_close`, `dimensions` 等。

---

## 四、架构评审决策点

### 决策点 1：模块归属

| 方案 | 说明 | 需评估 |
|---|---|---|
| A | 新建 `modules/price_advisor.py` 独立模块 | 与现有模块化架构一致性、可测试性 |
| B | 在 `advisor.py` 新增辅助函数（不改 generate_advice） | 内聚性 vs 文件膨胀（advisor.py 已 1020 行） |

### 决策点 2：集成方式（核心 — 必须给出推荐）

| 方案 | 说明 | 需评估 |
|---|---|---|
| C | **后处理集成**：app.py 调用层在 `generate_advice` 之后调用 price_advisor，将结果合并进 JSON 再返回 | 改动面、各调用点是否都需合并 |
| D | **独立 API 端点**：新建 `/api/stocks/<id>/price-advice`，前端单独请求 | 前端复杂度、与报告数据一致性 |
| E | **daily_report 集成**：报告生成时也调用 price_advisor，写入 daily_reports | 持久化需求、存储格式 |

> 架构师需明确：哪些调用链路需要价格建议（advise 端点？日报？批量？），哪些不需要。

### 决策点 3：价格算法（需给出具体公式与参数）

| 建议项 | 候选算法方向 | 数据依赖 |
|---|---|---|
| 买入价位区间 | 均线支撑位（MA20/MA60）/ 布林带下轨附近 | ma20, ma60, boll_lower |
| 目标价 | 均线压力位 / 布林带上轨 / 前期高点 | ma20, boll_upper, raw_kline |
| 止损价 | ATR × 系数（如 2×ATR）/ 固定百分比（如 -5%） | raw_kline（需 high/low）或 close |
| 止盈价（持仓） | 持仓成本 × 涨幅目标 / 阻力位 | positions.cost_price |
| 建议仓位 | 评级档位映射（80+→80%, 65+→50%, 50+→20%） | rating |

> 架构师需评估：raw_kline 是否有 high/low 字段（影响 ATR 可行性）；若无，用 close 波动率替代的合理性。

### 决策点 4：仓位建议联动

- 建议仓位（20%/50%/80%）与评级档位如何映射？需给出映射表。
- 与现有 `_build_position_advice` 文本建议是否冲突或冗余？如何协调？
- 降级场景：无 ma20/无持仓数据时价格建议如何降级输出？

### 决策点 5：前端展示

- 报告页面（`templates/index.html`）中价格建议展示在哪个区域？
- 展示格式：表格 / 文本 / 图表标注？
- 列表页是否需要展示价格区间摘要（如"买入区间 15.2-16.0"）？
- 需架构师评估前端改动范围（003 超买超卖徽标的改动可作参考）。

### 决策点 6：回测/日报集成

- `daily_report.py` 报告生成是否需要包含价格建议？
- 是否需要将价格建议持久化到 DB（新表 or 现有表加列）？
- 若持久化，回测模块是否需要验证价格建议准确率？

### 决策点 7：边界与降级

- 数据不足时（如 ma20 缺失、raw_kline 不足 20 天）的降级策略
- 港股特有情况（汇率已转换，但港股波动特征不同）
- 价格建议是否需要免责声明（"仅供参考，不构成投资建议"）

---

## 五、交付要求

架构师需输出评审报告 `docs/reviews/review_005_price_advice_20260728.md`，**必须包含**：

1. **推荐方案**（决策点1+2 的明确推荐 + 理由）
2. **价格算法方案**（决策点3 的具体公式 + 参数 + 数据依赖确认）
3. **仓位映射表**（决策点4）
4. **影响面分析**（涉及哪些文件修改，每个文件改什么）
5. **红线合规性确认**（逐条确认不违反约束清单）
6. **风险点和注意事项**
7. **对后续开发任务书的建议**（拆分粒度，如是否拆为"算法实现"+"前端展示"两个子任务）

---

## 六、边界声明

- 本任务为**纯架构评审，不写功能代码**。
- 评审通过后，PM 将基于评审结论签发开发任务书（DEV-TASKS-20260728-005-DEV）。
- 若架构师发现需求有重大风险或不合理处，可在评审报告中提出异议，PM 将转交监理决策。

---

## 附录：参考文件索引

| 文件 | 行号 | 用途 |
|---|---|---|
| `modules/advisor.py` | L869 | generate_advice 主入口（红线） |
| `modules/advisor.py` | L53-77 | _read_position / _read_latest_close（持仓+价格读取） |
| `modules/advisor.py` | L974-1001 | result 字典组装（输出结构） |
| `modules/data_contract.py` | L35-124 | StockData 契约（价格/均线/布林带字段） |
| `app.py` | L940-949 | /advise 端点（generate_advice 调用） |
| `app.py` | L754-762 | /analyze 端点 |
| `modules/daily_report.py` | L449-456 | 日报生成调用链 |
| `docs/requirements_v1.1.md` | L120-131 | §2.3.2 需求原文 |
