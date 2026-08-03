# 009 补充要求：position_advice 与 price_advice 矛盾修复

> **追加日期**：2026-07-29
> **原因**：监理发现美团(stock_id=6) position_advice 说"适量加仓"，price_advice 说"建议止盈"，两个建议矛盾。

---

## 问题

| 建议来源 | 模块 | 美团实际输出 |
|---|---|---|
| position_advice | advisor.py `_build_position_advice`（红线不可改） | "评级优良，建议持有，适量加仓" |
| price_advice.action_suggestion | price_advisor.py | "已超目标，建议止盈" |

两个模块各自独立给建议，互不协调。

## 解决方案

**在 app.py 调用层处理（不改 advisor.py 红线）**：

当 price_advice 有动态操作建议时（即 009 状态机输出的 action_suggestion），app.py 调用层用 price_advice 的建议**替换** position_advice 文本。

### 实现方式

在 app.py 的 /advise、/analyze、report-latest 端点中，price_advice 生成后追加一步：

```python
result['price_advice'] = generate_price_advice(stock_id, result)

# 009补充：当 price_advice 有动态操作建议时，覆盖 position_advice
if result.get('price_advice', {}).get('action_suggestion'):
    result['position_advice'] = result['price_advice']['action_suggestion']
```

### 影响的代码位置

| 端点 | 文件/行号 | 改动 |
|---|---|---|
| /advise | app.py L962-964 之后 | +2行 |
| /analyze | app.py L763-764 之后 | +2行 |
| report-latest（实时计算分支） | app.py L930 之后 | +2行 |
| report-latest（自动触发分支） | app.py L820 之后 | +2行 |

总计 +8行，分布在 4 处调用点。

### 不影响的代码

- advisor.py：零修改（红线）
- _build_position_advice：零修改（红线）
- position_advice 仍由 generate_advice 生成，只是调用层覆盖展示值

### 向后兼容

- price_advice.action_suggestion 为空时（如数据不足 available=false），position_advice 保持原值不变
- price_advice.action_suggestion 有值时（状态机输出），position_advice 被覆盖为动态建议

---

## 更新后的文件修改清单

| 文件 | 类型 | 改动 |
|---|---|---|
| modules/price_advisor.py | **重写** | 状态机+动态止盈+网格+资金面+交易分析 |
| templates/index.html | **重写** | 网格表格+状态颜色+资金面/交易摘要 |
| **app.py** | **修改(+8行)** | **4处调用点追加 position_advice 覆盖逻辑** |

> 原任务书标注 app.py 零改动，现修正为 +8行（4处×2行）。
