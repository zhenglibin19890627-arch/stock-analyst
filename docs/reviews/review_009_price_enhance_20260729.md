# 评审意见：009 价格建议增强 — 架构方案评审

| 项目 | 内容 |
|---|---|
| **文档编号** | REVIEW-009-PRICE-ENHANCE-20260729 |
| **评审类型** | 架构方案评审（架构师，响应 DEV-TASKS-20260729-009-ARCH） |
| **评审日期** | 2026-07-29 |
| **评审人** | 架构师（AI） |
| **关联需求** | 005 价格建议上线后用户反馈的 4 大核心问题 + 交易流水分析需求 |
| **评审对象** | price_advisor.py 算法增强（状态机/动态止盈/网格价位/资金面转化/交易流水） |
| **总体结论** | **全部 7 项决策点方案已确定，可进入开发阶段** |

---

## 〇、评审基础

### 0.1 评审背景

005 价格建议上线后，用户以中国中免（stock_id=21）为例反馈 4 个核心问题：

```
当前价：55.40  |  成本价：60.78（浮亏 -8.9%）
止盈价：72.94（需涨 31.6%）  |  止损价：56.53（当前价已跌破！）
操作建议：加仓20%（当前价<止损价，建议加仓=逻辑矛盾）
```

核心矛盾：**操作建议是固定文本映射（评级→固定文本），不感知当前价与止损/止盈线的位置关系**，导致"跌破止损仍建议加仓"的逻辑矛盾。

### 0.2 已审阅代码清单

| 文件 | 审阅范围 | 关键内容 |
|---|---|---|
| `docs/tasks/dev_tasks_20260729_009_arch.md` | 全文（195行） | 任务书 7 项决策点 |
| `modules/price_advisor.py` | 全文（343行） | 当前算法：常量映射、无持仓/有持仓算法、主入口 |
| `modules/advisor.py` | L98-134, L254-301, L785-831, L869-1005 | _determine_action、_build_position_advice、_build_capital_factors、generate_advice 返回结构 |
| `modules/data_adapter.py` | L36-170 | _calc_ma、_calc_bollinger、_calc_rsi 等技术指计算 |
| `templates/index.html` | L4113-4168 | 前端价格建议表格渲染逻辑 |
| `app.py` | L754-767, L955-967 | /analyze 端点、/advise 端点 price_advice 集成 |
| `docs/reviews/review_005_price_advice_20260728.md` | 全文（524行） | 005 评审报告（算法基线参考） |
| `trade_records` 表 | PRAGMA + 数据查询 | 表结构（10 列）、27 条记录、6 只股票 |

### 0.3 数据资产验证

| 验证项 | 结果 | 影响 |
|---|---|---|
| trade_records 表结构 | ✅ 10 列：id/holding_id/stock_id/trade_type/price/quantity/amount/trade_date/notes/created_at | 交易流水分析可行 |
| trade_records 数据量 | ⚠️ 27 条，集中在 stock_id=4（14条）和 stock_id=21（9条），其余 4 只各 1 条 | 数据稀疏，分析结论需谨慎 |
| advice_result['dimensions'] | ✅ generate_advice 返回含 dimensions.capital_flow.factors | 资金面因子可通过 advice_result 传入 price_advisor |
| StockData.ma20/ma60/boll_upper/boll_lower | ✅ Optional 字段，data_adapter 已计算 | 动态止盈的技术面阻力位可用 |
| raw_kline high/low/close | ✅ 005 已验证 | ATR 计算继续可用 |

### 0.4 资金面因子传递路径确认

```
generate_advice(stock_id)
  → analysis = analyze_stock(stock_id) 或 v5 转换
  → analysis['dimensions']['capital_flow']['factors'] = {
      'main_trend': '主力净流入21800万元',
      'main_pct': '5.23%',
      'consecutive': '连续净流入2日',
      'main_avg_5d': '5日均净流入12345万元',
      'super_large': '超大单净8000万元(流入)'
    }
  → result['dimensions'] = analysis['dimensions']  (L995)
  → app.py: result['price_advice'] = generate_price_advice(stock_id, result)
  → price_advisor 可通过 advice_result['dimensions']['capital_flow']['factors'] 读取
```

**结论**：资金面因子无需修改 advisor.py，price_advisor 可直接从 advice_result 中提取。

---

## 一、决策点 1：操作建议状态机设计

### 1.1 问题分析

当前 `_gen_with_position`（price_advisor.py L249-263）的操作建议是**纯评级映射**：

```python
action_suggestion = RATING_ACTION_SUGGESTION.get(rating, '持有观望')
# '推荐买入' → '加仓20%'（无论当前价是否已跌破止损价）
```

**缺失的感知维度**：当前价（close）与止损价（stop_loss）、止盈价（take_profit）、成本价（cost_price）的位置关系。

### 1.2 状态机设计

#### 状态定义（有持仓场景）

以 close 与三条价格线的关系定义 6 种状态：

| 状态编号 | 条件 | 状态名 | 说明 |
|---|---|---|---|
| S1 | close ≥ take_profit | 已超目标 | 当前价已达到或超过止盈价 |
| S2 | cost_price < close < take_profit | 浮盈中 | 当前价在成本价与止盈价之间 |
| S3 | stop_loss ≤ close ≤ cost_price | 浮亏中 | 当前价在止损价与成本价之间 |
| S4 | close < stop_loss | 已破止损 | 当前价已跌破止损价 |
| S5 | close == cost_price | 成本线 | 当前价恰好在成本价（罕见，归入 S2 或 S3） |
| S6 | take_profit ≤ cost_price | 异常 | 止盈价≤成本价（数据异常，降级处理） |

#### 状态 × 评级 → 操作建议矩阵

| 评级 | S1 已超目标 | S2 浮盈中 | S3 浮亏中 | S4 已破止损 |
|---|---|---|---|---|
| **强烈推荐买入** | 已达目标，分批止盈 | 持有，等待止盈 | 浮亏中，可逢低补仓 | 已破止损，建议止损观望 |
| **推荐买入** | 已达目标，建议止盈 | 持有，等待止盈 | 浮亏中，持有观望 | 已破止损，建议止损 |
| **持有观望** | 已达目标，建议止盈 | 持有观望 | 浮亏中，持有观望 | 已破止损，建议止损 |
| **建议减仓** | 已达目标，建议止盈 | 考虑减仓锁定利润 | 建议减仓控制风险 | 已破止损，建议清仓 |
| **强烈建议卖出** | 已达目标，立即止盈 | 建议减仓 | 建议止损离场 | 已破止损，立即清仓 |

#### 关键设计决策

1. **S4（已破止损）是核心修复点**：无论评级多高，只要 close < stop_loss，操作建议必须包含"止损"或"清仓"，**禁止出现"加仓"**。
2. **S1（已超目标）统一建议止盈**：当前 005 算法已有此逻辑（L259-261），保留并强化。
3. **S3（浮亏中）区分评级**：高评级（强烈推荐买入）可建议"逢低补仓"，低评级建议"减仓/止损"。
4. **状态优先级**：S4 > S1 > S3 > S2（破止损最优先，已超目标次之）。

### 1.3 无持仓场景的状态感知

无持仓场景不涉及止损/止盈，但需感知当前价与买入区间的关系：

| 条件 | 建议 |
|---|---|
| close < buy_range_low | 当前价低于买入区间，可逢低买入 |
| buy_range_low ≤ close ≤ buy_range_high | 当前价在买入区间内，可按计划买入 |
| close > buy_range_high | 当前价高于买入区间，建议等待回调 |

### 1.4 实现方案

在 `_gen_with_position` 中，将固定映射替换为状态机：

```python
# 伪代码
def _determine_action_by_state(close, cost_price, take_profit, stop_loss, rating):
    if close < stop_loss:
        state = 'S4'
    elif close >= take_profit:
        state = 'S1'
    elif close > cost_price:
        state = 'S2'
    else:
        state = 'S3'
    
    # 查矩阵
    return ACTION_MATRIX[rating][state]
```

**改动范围**：仅 `_gen_with_position` 函数内部，新增一个辅助函数 `_determine_action_by_state`。

---

## 二、决策点 2：止盈价动态化方案

### 2.1 问题分析

当前止盈价 = 成本价 × (1 + 固定目标涨幅)，不参考任何技术面阻力位。导致止盈价可能远高于实际阻力位（如中国中免止盈价 72.94，需涨 31.6%，远超合理预期）。

### 2.2 候选方案对比

| 方案 | 公式 | 优势 | 劣势 |
|---|---|---|---|
| A | 止盈价 = min(成本价×(1+目标涨幅), 技术面阻力位) | 保守，不会给出过高目标 | 可能过于保守，限制盈利空间 |
| B | 止盈价 = 技术面阻力位（MA60/布林上轨/前高） | 完全市场化 | 忽略成本价，可能低于成本 |
| **C** | **止盈价 = max(成本价×(1+最低目标涨幅), min(成本价×(1+目标涨幅), 技术面阻力位))** | 平衡成本约束与市场阻力 | 实现稍复杂 |

### 2.3 意见：**推荐方案 C — 双约束止盈价**

**公式**：

```
固定止盈价 = cost_price × (1 + target_gain)          # 现有逻辑
技术阻力位 = max(boll_upper, ma60)                     # 若无则用 close × 1.10
最低止盈价 = cost_price × (1 + min_target_gain)        # 保底目标（如 +5%）

止盈价 = max(最低止盈价, min(固定止盈价, 技术阻力位))
```

**最低目标涨幅映射**（新增常量）：

| 评级 | 最低目标涨幅 | 说明 |
|---|---|---|
| 强烈推荐买入 | +8% | 高信心，保底 8% |
| 推荐买入 | +6% | 保底 6% |
| 持有观望 | +4% | 保底 4% |
| 建议减仓 | +3% | 保底 3% |
| 强烈建议卖出 | +2% | 保底 2% |

**逻辑解释**：
- 若技术阻力位 > 固定止盈价 → 止盈价 = 固定止盈价（不受阻力位限制，给足空间）
- 若技术阻力位 < 固定止盈价 → 止盈价 = 技术阻力位（尊重市场阻力）
- 若技术阻力位 < 最低止盈价 → 止盈价 = 最低止盈价（保底，确保有盈利空间）

### 2.4 技术阻力位计算

```python
def _calc_resistance(close, ma60, boll_upper):
    """计算技术面阻力位"""
    candidates = []
    if boll_upper and boll_upper > close:
        candidates.append(boll_upper)
    if ma60 and ma60 > close:
        candidates.append(ma60)
    
    if candidates:
        return min(candidates)  # 取最近的阻力位
    else:
        return close * 1.10  # 降级：10% 固定目标
```

**注意**：阻力位必须 > 当前价（已突破的阻力位不再是阻力位）。

### 2.5 中国中免案例验证

```
成本价：60.78  |  当前价：55.40  |  评级：推荐买入
固定止盈价 = 60.78 × 1.20 = 72.94
技术阻力位 = max(boll_upper≈62, ma60≈58) = 62（假设值）
最低止盈价 = 60.78 × 1.06 = 64.43

止盈价 = max(64.43, min(72.94, 62)) = max(64.43, 62) = 64.43

对比：原方案 72.94（需涨 31.6%）→ 新方案 64.43（需涨 16.3%），更合理
```

---

## 三、决策点 3：网格价位设计

### 3.1 需求分析

用户需要分批建仓/减仓的阶梯价位，而非单一的止盈/止损价。

### 3.2 网格间距算法：**推荐 ATR 动态间距**

**理由**：
- 固定百分比（如 3%/5%）不区分高波动股和低波动股
- ATR 反映个股实际波动率，高波动股网格宽、低波动股网格窄
- 005 已有 ATR 计算基础（_calc_atr），无需新增依赖

**间距公式**：

```
网格间距 = ATR × grid_factor
```

**grid_factor 映射**：

| 场景 | grid_factor | 说明 |
|---|---|---|
| 无持仓买入网格 | 0.8 | 买入阶梯间距 |
| 有持仓补仓网格 | 1.0 | 补仓位间距 |
| 有持仓减仓网格 | 0.6 | 减仓位间距（更密集，锁定利润） |

### 3.3 无持仓：买入网格

**3 档买入位**（从低到高）：

```
第一买入位 = buy_range_low（买入区间下限）
第二买入位 = buy_range_low + ATR × 0.8
第三买入位 = buy_range_high（买入区间上限）
```

**仓位分配**：

| 档位 | 仓位比例 | 触发条件 |
|---|---|---|
| 第一买入位 | 总仓位的 40% | 价格跌至第一买入位 |
| 第二买入位 | 总仓位的 35% | 价格跌至第二买入位 |
| 第三买入位 | 总仓位的 25% | 价格跌至第三买入位 |

**输出结构**：

```python
'grid': [
    {'level': 1, 'price': 52.00, 'pct': 40, 'type': 'buy', 'label': '第一买入位'},
    {'level': 2, 'price': 53.50, 'pct': 35, 'type': 'buy', 'label': '第二买入位'},
    {'level': 3, 'price': 55.00, 'pct': 25, 'type': 'buy', 'label': '第三买入位'},
]
```

### 3.4 有持仓：补仓/减仓网格

**补仓位**（当前价 < 成本价时）：

```
补仓位 = max(stop_loss + ATR × 0.5, close - ATR × 1.0)
```

**减仓位**（当前价 > 成本价时，或反弹至成本价附近）：

```
第一减仓位 = cost_price（回本减仓）
第二减仓位 = cost_price + ATR × 0.6
第三减仓位 = take_profit（止盈位清仓）
```

**输出结构**：

```python
'grid': [
    {'level': 1, 'price': 53.00, 'pct': 10, 'type': 'add', 'label': '补仓位'},
    {'level': 2, 'price': 60.78, 'pct': 30, 'type': 'reduce', 'label': '回本减仓位'},
    {'level': 3, 'price': 64.43, 'pct': 50, 'type': 'reduce', 'label': '第一止盈位'},
    {'level': 4, 'price': 72.94, 'pct': 100, 'type': 'reduce', 'label': '最终止盈位'},
]
```

### 3.5 网格数量决策

| 场景 | 网格数 | 理由 |
|---|---|---|
| 无持仓买入 | 3 档 | 用户零代码，3 档最易理解和执行 |
| 有持仓补仓 | 1 档 | 补仓是单一动作，多档补仓过于复杂 |
| 有持仓减仓 | 2-3 档 | 回本减仓 + 止盈减仓，最多 3 档 |

**原则**：网格总数不超过 5 个，避免信息过载。

---

## 四、决策点 4：资金面转化方案

### 4.1 资金面因子提取

从 `advice_result['dimensions']['capital_flow']['factors']` 中提取结构化数据：

```python
def _parse_capital_factors(factors):
    """解析资金面因子文本为结构化数据"""
    result = {
        'main_inflow': None,  # 主力净流入（万元，正=流入，负=流出）
        'main_pct': None,  # 主力净流入占比
        'consecutive_days': 0,  # 连续净流入/流出天数（正=流入，负=流出）
        'super_large_inflow': None,  # 超大单净流入（万元）
        'avg_5d_inflow': None,  # 5日均净流入（万元）
    }

    # 解析 main_trend: '主力净流入21800万元' → 21800
    # 解析 consecutive: '连续净流入2日' → +2, '连续净流出3日' → -3
    # 解析 super_large: '超大单净8000万元(流入)' → +8000
    # ...

    return result
```

### 4.2 资金面信号分类

| 信号 | 条件 | 强度 |
|---|---|---|
| 强流入 | consecutive_days ≥ 3 且 main_inflow > 0 | +2 |
| 中流入 | consecutive_days ≥ 2 且 main_inflow > 0 | +1 |
| 弱流入 | main_inflow > 0 但无连续 | +0.5 |
| 中性 | main_inflow ≈ 0 | 0 |
| 弱流出 | main_inflow < 0 但无连续 | -0.5 |
| 中流出 | consecutive_days ≤ -2 且 main_inflow < 0 | -1 |
| 强流出 | consecutive_days ≤ -3 且 main_inflow < 0 | -2 |
| 超大单异常 | super_large_inflow < -5000（流出超 5000 万） | 额外 -1 |

### 4.3 资金面对操作建议的影响方式：**推荐"修饰词"方案**

**不覆盖基础建议，而是添加修饰词/附加提示**。

| 资金面信号 | 对基础建议的修饰 |
|---|---|
| 强流入（+2） | 基础建议前加"资金面强支撑，" |
| 中流入（+1） | 基础建议前加"资金面偏积极，" |
| 弱流入（+0.5） | 基础建议后加"，资金面略有流入" |
| 中性（0） | 不修饰 |
| 弱流出（-0.5） | 基础建议后加"，注意资金面略有流出" |
| 中流出（-1） | 基础建议前加"资金面偏弱，" |
| 强流出（-2） | 基础建议前加"资金面明显流出，" |
| 超大单异常流出 | 追加风险提示"超大单大幅流出，警惕主力撤离" |

**示例**：

```
基础建议：持有，等待止盈
强流入修饰后：资金面强支撑，持有，等待止盈
强流出修饰后：资金面明显流出，持有，等待止盈
```

### 4.4 资金面对网格价位的影响

| 资金面信号 | 对网格的影响 |
|---|---|
| 强流入 | 补仓位可更积极（grid_factor × 0.8，间距缩小） |
| 强流出 | 补仓位更保守（grid_factor × 1.2，间距扩大） |
| 超大单异常流出 | 暂停补仓建议，仅保留减仓/止损建议 |

### 4.5 输出结构

```python
'capital_signal': {
    'strength': 2,           # -2 到 +2
    'label': '强流入',       # 文本标签
    'modifier': '资金面强支撑，',  # 修饰词
    'risk_warning': None,    # 额外风险提示（如有）
}
```

---

## 五、决策点 5：交易流水分析方案

### 5.1 数据现状评估

| 指标 | 数值 | 评估 |
|---|---|---|
| 总记录数 | 27 条 | 较少 |
| 涉及股票 | 6 只 | 覆盖面窄 |
| stock_id=4 | 14 条 | 相对充足 |
| stock_id=21 | 9 条 | 基本可用 |
| 其余 4 只 | 各 1 条 | 无法分析 |

**结论**：交易流水分析**可行但需降级策略**——数据充足时输出分析结果，数据不足时静默跳过。

### 5.2 分析维度设计

#### 维度 1：加仓节奏分析

```python
def _analyze_trade_rhythm(trades):
    """分析用户加仓节奏"""
    buys = [t for t in trades if t['trade_type'] == 'buy']
    if len(buys) < 2:
        return None

    # 计算买入间隔天数
    intervals = []
    for i in range(1, len(buys)):
        delta = (buys[i]['trade_date'] - buys[i - 1]['trade_date']).days
        intervals.append(delta)

    avg_interval = sum(intervals) / len(intervals)

    # 判断节奏
    if avg_interval <= 3:
        return {'pattern': '频繁加仓', 'avg_interval': avg_interval, 'risk': '追涨风险较高'}
    elif avg_interval <= 10:
        return {'pattern': '分批建仓', 'avg_interval': avg_interval, 'risk': None}
    else:
        return {'pattern': '低频加仓', 'avg_interval': avg_interval, 'risk': None}
```

#### 维度 2：成本变化趋势

```python
def _analyze_cost_trend(trades, current_cost):
    """分析加仓后成本价变化趋势"""
    buys = [t for t in trades if t['trade_type'] == 'buy']
    if len(buys) < 2:
        return None

    # 模拟每次加仓后的成本价
    costs = []
    total_qty = 0
    total_amount = 0
    for b in sorted(buys, key=lambda x: x['trade_date']):
        total_qty += b['quantity']
        total_amount += b['price'] * b['quantity']
        costs.append(total_amount / total_qty)

    # 判断趋势
    if len(costs) >= 2:
        if costs[-1] < costs[0]:
            trend = 'down'  # 成本递减（低位补仓，好）
        elif costs[-1] > costs[0]:
            trend = 'up'  # 成本递增（追高加仓，差）
        else:
            trend = 'flat'

        return {
            'trend': trend,
            'first_cost': costs[0],
            'last_cost': costs[-1],
            'current_cost': current_cost,
            'suggestion': '低位补仓有效摊薄成本' if trend == 'down' else '注意追高加仓推高成本',
        }
```

#### 维度 3：买卖时机统计

```python
def _analyze_trade_timing(trades):
    """统计历史买卖点的盈亏"""
    # 配对买卖（FIFO）
    pairs = []
    buy_queue = []

    for t in sorted(trades, key=lambda x: x['trade_date']):
        if t['trade_type'] == 'buy':
            buy_queue.append(t)
        elif t['trade_type'] == 'sell' and buy_queue:
            buy = buy_queue.pop(0)
            profit = (t['price'] - buy['price']) / buy['price'] * 100
            pairs.append(
                {
                    'buy_date': buy['trade_date'],
                    'sell_date': t['trade_date'],
                    'buy_price': buy['price'],
                    'sell_price': t['price'],
                    'profit_pct': profit,
                }
            )

    if not pairs:
        return None

    wins = [p for p in pairs if p['profit_pct'] > 0]
    win_rate = len(wins) / len(pairs) * 100
    avg_profit = sum(p['profit_pct'] for p in pairs) / len(pairs)

    return {
        'total_trades': len(pairs),
        'win_rate': round(win_rate, 1),
        'avg_profit_pct': round(avg_profit, 1),
        'suggestion': f'历史交易胜率{win_rate:.0f}%，平均盈亏{avg_profit:+.1f}%',
    }
```

### 5.3 输出结构

```python
'trade_analysis': {
    'available': True,  # 数据是否充足
    'trade_count': 9,   # 总交易笔数
    'rhythm': {...},    # 加仓节奏（可选）
    'cost_trend': {...}, # 成本趋势（可选）
    'timing': {...},    # 买卖时机（可选）
    'summary': '分批建仓，成本递减，历史胜率67%',  # 一句话摘要
}
```

### 5.4 是否新建模块？

**意见：不新建模块，在 price_advisor.py 内新增辅助函数**。

理由：
- 交易流水分析是价格建议的辅助功能，不是独立领域
- 预估代码量 80-100 行，不足以独立成模块
- 保持 price_advisor.py 单一职责（价格建议相关计算）

---

## 六、决策点 6：前端展示方案

### 6.1 展示升级：从"单值表格"到"网格表格"

#### 无持仓：买入网格表格

```
┌─────────────────────────────────────────────────────────┐
│ 💰 价格建议（当前无持仓）              评级：推荐买入      │
├──────────┬──────────┬──────────┬────────────────────────┤
│ 建议仓位  │   50%    │ 当前价   │   55.40                │
│ 买入区间  │ 52.00-58.00 │ 目标价 │   64.43             │
│ 止损价    │   49.00  │ 最大回撤 │   -11.6%               │
├──────────┴──────────┴──────────┴────────────────────────┤
│ 📊 网格买入计划                                           │
│ ┌──────┬──────────┬────────┬──────────────────────────┐ │
│ │ 档位 │ 买入价位  │ 仓位   │ 说明                     │ │
│ │  1   │  52.00   │  40%   │ 第一买入位（逢低买入）    │ │
│ │  2   │  53.50   │  35%   │ 第二买入位               │ │
│ │  3   │  55.00   │  25%   │ 第三买入位               │ │
│ └──────┴──────────┴────────┴──────────────────────────┘ │
│ 资金面：主力连续净流入2日，资金面偏积极                    │
└─────────────────────────────────────────────────────────┘
```

#### 有持仓：操作网格表格

```
┌─────────────────────────────────────────────────────────┐
│ 💰 价格建议（持仓中）                   评级：推荐买入     │
├──────────┬──────────┬──────────┬────────────────────────┤
│ 成本价    │  60.78   │ 当前价   │   55.40                │
│ 浮盈      │  -8.9%   │ 状态     │   已破止损             │
│ 止盈价    │  64.43   │ 止损价   │   56.53                │
│ 操作建议  │ 已破止损，建议止损观望                        │
├──────────┴──────────┴──────────┴────────────────────────┤
│ 📊 操作网格计划                                           │
│ ┌──────┬──────────┬────────┬──────────────────────────┐ │
│ │ 档位 │ 价位     │ 仓位   │ 说明                     │ │
│ │  1   │  53.00   │  10%   │ 补仓位（谨慎补仓）        │ │
│ │  2   │  60.78   │  30%   │ 回本减仓位               │ │
│ │  3   │  64.43   │  50%   │ 第一止盈位               │ │
│ └──────┴──────────┴────────┴──────────────────────────┘ │
│ 资金面：主力净流入21800万元，连续净流入2日                │
│ 交易分析：分批建仓，成本递减，历史胜率67%                  │
└─────────────────────────────────────────────────────────┘
```

### 6.2 前端改动范围

| 文件 | 改动内容 | 预估行数 |
|---|---|---|
| `templates/index.html` | 重写 price_advice section 渲染逻辑（网格表格 + 资金面 + 交易分析） | +120 行（替换现有 56 行） |
| `templates/index.html` | 新增网格表格 CSS 样式 | +40 行 |

### 6.3 状态颜色编码

| 状态 | 颜色 | CSS class |
|---|---|---|
| 已超目标 | 绿色 | `pa-up` |
| 浮盈中 | 浅绿 | `pa-up-light` |
| 浮亏中 | 橙色 | `pa-warning` |
| 已破止损 | 红色 | `pa-down` |
| 买入位 | 蓝色 | `pa-buy` |
| 减仓位 | 橙色 | `pa-reduce` |
| 补仓位 | 紫色 | `pa-add` |

---

## 七、决策点 7：影响面分析

### 7.1 文件修改清单

| 文件 | 改动类型 | 改动内容 | 预估行数 |
|---|---|---|---|
| `modules/price_advisor.py` | **重写** | 状态机、动态止盈、网格价位、资金面转化、交易流水分析 | ~500 行（现有 343 行，净增 ~160 行） |
| `templates/index.html` | **重写** | price_advice section 渲染逻辑 + CSS | +160 行（替换现有 56 行） |
| `app.py` | **零改动** | 无需修改（price_advice 集成方式不变） | 0 |
| `modules/advisor.py` | **零改动** | 红线保护 | 0 |
| `modules/data_adapter.py` | **零改动** | 无需修改 | 0 |
| `modules/daily_report.py` | **零改动** | 无需修改 | 0 |
| `database/db_manager.py` | **零改动** | 无需新加列 | 0 |

**总计**：重写 2 个文件，零改动 5 个文件。

### 7.2 不修改的文件（红线保护）

| 文件 | 原因 |
|---|---|
| `modules/advisor.py` | B24 红线，generate_advice 主入口不可改 |
| `modules/data_collector.py` | L1645/L1684/L1717 三处 if False 不可改 |
| `config_weights.json` | rating_mapping 不可改 |
| `modules/data_contract.py` | StockData 契约不可破坏 |
| `modules/scoring_engine.py` | 评分逻辑与价格建议无关 |
| `app.py` | price_advice 集成方式已确定，无需修改 |
| `modules/daily_report.py` | 日报集成方式已确定，无需修改 |

### 7.3 API 返回结构变更

`/api/stocks/<id>/advise` 和 `/api/stocks/<id>/analyze` 返回的 `price_advice` 字段结构变更：

**无持仓模式新增字段**：
- `grid`: 买入网格数组
- `capital_signal`: 资金面信号
- `action_suggestion`: 动态操作建议（新增）

**有持仓模式新增字段**：
- `grid`: 操作网格数组
- `capital_signal`: 资金面信号
- `trade_analysis`: 交易流水分析
- `state`: 当前状态（S1/S2/S3/S4）
- `action_suggestion`: 动态操作建议（替换固定文本）

**向后兼容**：保留所有现有字段（take_profit/stop_loss/cost_price/current_close/profit_pct 等），前端可选择性展示。

---

## 八、红线合规性确认

| # | 红线 | 合规性 | 说明 |
|---|---|---|---|
| 1 | **generate_advice 不可改** | ✅ 合规 | advisor.py L869 函数签名和函数体零修改 |
| 2 | **price_advisor.py 可修改** | ✅ 合规 | 本次评审核心对象，算法参数可调整 |
| 3 | **data_collector 三处 if False** | ✅ 合规 | 不触碰 data_collector.py |
| 4 | **零代码约束** | ✅ 合规 | 无新 pip 依赖，仅用标准库 + sqlite3 |
| 5 | **不回写** | ✅ 合规 | 纯计算输出，不修改数据采集逻辑 |
| 6 | **config_weights.json** | ✅ 合规 | 不修改 rating_mapping |
| 7 | **A/H 双市场兼容** | ✅ 合规 | 汇率已在数据层转换，算法透明 |
| 8 | **_build_capital_factors 不可改** | ✅ 合规 | 仅通过 advice_result['dimensions'] 读取，不修改 advisor.py |

---

## 九、后续开发任务拆分建议

### 9.1 拆分粒度

建议拆分为 **3 个子任务**：

| 子任务 | 内容 | 预估工作量 | 依赖 |
|---|---|---|---|
| **009-DEV-A** | 后端核心算法：状态机 + 动态止盈 + 网格价位 | 1 天 | 无 |
| **009-DEV-B** | 后端增强：资金面转化 + 交易流水分析 | 0.5 天 | 009-DEV-A 完成 |
| **009-DEV-C** | 前端展示：网格表格 + 资金面 + 交易分析 + CSS | 0.5 天 | 009-DEV-B 完成 |

### 9.2 验收标准建议

**009-DEV-A 验收标准**：
1. `_gen_with_position` 实现状态机（S1-S4），跌破止损时操作建议不含"加仓"
2. 止盈价采用双约束公式（固定止盈价 vs 技术阻力位 vs 最低止盈价）
3. 无持仓输出 3 档买入网格，有持仓输出补仓/减仓网格
4. 中国中免案例验证：close=55.40, cost=60.78 时，状态=S4，操作建议含"止损"

**009-DEV-B 验收标准**：
1. 从 advice_result['dimensions']['capital_flow']['factors'] 提取资金面因子
2. 资金面信号分为 7 档（强流入到强流出），修饰基础操作建议
3. trade_records 数据充足时（≥2 笔买入）输出交易分析
4. 数据不足时 trade_analysis.available=False，静默跳过

**009-DEV-C 验收标准**：
1. 报告页面价格建议 section 升级为网格表格
2. 状态颜色编码正确（已破止损=红色，已超目标=绿色）
3. 资金面提示和交易分析摘要显示在网格表格下方
4. 移动端显示正常（无溢出）

### 9.3 开发顺序建议

```
009-DEV-A（核心算法）
  ↓
009-DEV-B（增强功能）
  ↓
009-DEV-C（前端展示）
  ↓
QA 验收（中国中免案例 + 边界场景）
```

---

## 十、风险点和注意事项

### 10.1 技术风险

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| 状态机逻辑复杂，边界条件多 | 中 | 单元测试覆盖 5 档评级 × 4 种状态 = 20 种组合 |
| 技术阻力位计算依赖 ma60/boll_upper 数据质量 | 中 | 降级链完善（无阻力位 → 固定 10% 目标） |
| 交易流水数据稀疏，分析结论不可靠 | 低 | 数据不足时静默跳过，不输出误导性结论 |
| 资金面因子文本解析脆弱 | 低 | 正则表达式 + 异常捕获，解析失败时降级为中性 |

### 10.2 业务风险

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| 网格价位过多导致用户困惑 | 中 | 网格总数不超过 5 个，每档有明确标签 |
| 状态机建议与用户预期不符 | 低 | 保留评级作为参考，状态机建议附加评级说明 |
| 交易流水分析涉及用户隐私 | 低 | 仅在本地计算，不上传任何数据 |

### 10.3 注意事项

1. **状态机优先级**：S4（已破止损）> S1（已超目标）> S3（浮亏中）> S2（浮盈中），破止损最优先。

2. **技术阻力位必须 > 当前价**：已突破的阻力位不再是阻力位，需过滤。

3. **网格价位必须排序**：买入网格从低到高，减仓网格从低到高。

4. **资金面修饰词位置**：强信号（±2）放基础建议前，弱信号（±0.5）放基础建议后。

5. **交易流水分析的降级**：trade_records 不足 2 笔买入时，rhythm 和 cost_trend 返回 None。

---

## 十一、总体结论

### 11.1 推荐方案汇总

| 决策点 | 推荐 | 核心理由 |
|---|---|---|
| 1. 操作建议状态机 | 6 状态 × 5 评级矩阵，S4 破止损禁止加仓 | 修复"跌破止损仍建议加仓"逻辑矛盾 |
| 2. 止盈价动态化 | 方案 C：双约束（固定止盈价 vs 技术阻力位 vs 最低止盈价） | 平衡成本约束与市场阻力 |
| 3. 网格价位设计 | ATR 动态间距，无持仓 3 档买入，有持仓 1 补 + 2-3 减 | 反映个股波动率，网格总数 ≤5 |
| 4. 资金面转化 | 修饰词方案（不覆盖基础建议，添加修饰/附加提示） | 保持建议一致性，避免冲突 |
| 5. 交易流水分析 | 3 维度（加仓节奏/成本趋势/买卖时机），数据不足时静默跳过 | 增强建议精准度，不误导 |
| 6. 前端展示 | 网格表格 + 状态颜色编码 + 资金面/交易分析摘要 | 信息结构清晰，视觉层次分明 |
| 7. 影响面 | 重写 2 文件（price_advisor.py + index.html），零改动 5 文件 | 改动集中，红线零触碰 |

### 11.2 架构师声明

- 以上所有结论基于对 `price_advisor.py`、`advisor.py`、`data_adapter.py`、`app.py`、`templates/index.html` 的**实际代码审阅**和**数据库实证查询**
- trade_records 表结构和数据量已通过 `PRAGMA table_info(trade_records)` 和 `SELECT COUNT(*)` 确认
- 推荐方案确保 B24 红线零触碰，generate_advice 函数签名和函数体完全不变
- 状态机参数（网格间距系数、最低目标涨幅、资金面信号阈值）为初始建议值，可在开发阶段根据实际数据微调
- 中国中免案例（stock_id=21）已用于验证状态机和动态止盈方案的合理性

---

*评审完毕。如需 PM 对任何决策点进行二次讨论或要求架构师补充分析，请反馈。*
