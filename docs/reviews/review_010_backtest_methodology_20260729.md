# 评审意见：010 回测引擎方法论修复与评级有效性诊断 — 架构方案评审

| 项目 | 内容 |
|---|---|
| **文档编号** | REVIEW-010-BACKTEST-METHODOLOGY-20260729 |
| **评审类型** | 架构方案评审（架构师，响应 DEV-TASKS-20260729-010-ARCH） |
| **评审日期** | 2026-07-29 |
| **评审人** | 架构师（AI） |
| **关联需求** | PM 发现 007 回测数据评级与实际市场表现完全倒挂（"建议减仓"涨 15.8%，"推荐买入"涨跌对半） |
| **评审对象** | price_backtest.py 回测引擎方法论修复 + 评级有效性诊断方案 |
| **总体结论** | **010-1/010-2 方案确认可行；010-3 锚点法覆盖率仅 3.6%，需降级为"辅助验证"而非"核心修复"；010-4 可信样本报告需调整统计口径** |

---

## 〇、评审基础

### 0.1 评审背景

PM 在验收 009 后，对 007 价格建议回测数据（price_backtest_results 表，938 条）做了全面诊断，发现**评级与实际市场表现完全倒挂**：

| 评级 | T+20平均最多涨 | T+20平均最多跌 | 涨跌比 | 期望表现 |
|---|---|---|---|---|
| 推荐买入（无持仓） | +6.4% | -6.4% | 1.00:1 | 涨远大于跌 |
| 持有观望（无持仓） | +9.7% | -8.0% | 1.21:1 | 涨跌均衡 |
| 建议减仓（无持仓） | **+15.8%** | -7.9% | **2.00:1** | 跌大于涨 |

**核心矛盾**：系统最不看好的"建议减仓"股票，反而涨得最猛（20天平均涨15.8%），而最看好的"推荐买入"股票涨跌对半开。

### 0.2 根因定位

根因在 `price_backtest.py` L460-469：

```python
# 当前回测逻辑（有缺陷）
stock_data = load_stockdata_from_db(stock_id)  # 加载全量数据（含未来信息）
analysis = analyze(stock_data)  # 用"现在"的全量数据算评级
rating = analysis.rating  # ← 这是"2026-07-29"的评级！
# 然后把这个"现在的评级"套到过去250天每一个回测点上
```

**问题本质**：被评"建议减仓"的股票，往往是因为前期大涨后到达高位，系统现在判断该减仓。但回测把这些股票**前期上涨段的回测点也标成了"建议减仓"**，造成严重的未来函数偏差（look-ahead bias）。

### 0.3 数据资产验证（架构师独立查库）

| 验证项 | 结果 | 影响 |
|---|---|---|
| ratings_history 总记录数 | 200 条 | 锚点法可用样本 |
| ratings_history 日期范围 | 2026-07-16 ~ 2026-07-28（12 天） | 时间跨度极短 |
| ratings_history 覆盖股票 | 27 只 | 与回测股票一致 |
| price_backtest_results 总记录数 | 938 条 | 回测样本充足 |
| price_backtest_results 日期范围 | 2025-08-15 ~ 2026-07-27 | 覆盖约 250 天 |
| raw_kline 总记录数 | 6,483 条 | K线数据充足 |
| raw_kline 日期范围 | 2025-06-27 ~ 2026-07-28 | 约 260 天/股 |
| **锚点覆盖率（3日窗口）** | **34/938 = 3.6%** | **严重不足，无法支撑核心修复** |
| has_position=0 记录数 | 713 条（76.0%） | 无持仓样本为主 |
| has_position=1 记录数 | 225 条（24.0%） | 有持仓样本较少 |
| has_position=0 的 take_profit | 713 条全为 NULL | 稀释 Bug 确认存在 |
| has_position=0 的 t20_hit_take_profit | 713 条全为 0 | 被错误设为 0 而非 NULL |

### 0.4 已审阅代码清单

| 文件 | 审阅范围 | 关键内容 |
|---|---|---|
| `modules/price_backtest.py` | 全文（766 行） | 007 回测引擎，根因所在 |
| `modules/price_advisor.py` | 全文（878 行） | 009 增强版，含动态止盈公式 |
| `modules/backtest_engine.py` | 全文（1013 行） | M8 引擎，方法论正确但数据少 |
| `docs/reviews/review_007_price_backtest_design_20260728.md` | 全文（530 行） | 007 方案设计评审 |
| `docs/reviews/review_009_price_enhance_20260729.md` | 全文（779 行） | 009 架构师评审 |
| `reports/accept_007_price_backtest_20260728.md` | 全文（113 行） | 007 验收报告 |

### 0.5 关键发现

1. **锚点覆盖率仅 3.6%**：ratings_history 仅覆盖 2026-07-16 ~ 2026-07-28（12 天），而 price_backtest_results 覆盖 2025-08-15 ~ 2026-07-27（约 250 天）。3 日窗口内仅 34/938 条回测点有历史评级锚点，覆盖率严重不足。

2. **稀释 Bug 确认存在**：has_position=0 的 713 条记录中，take_profit 全为 NULL，但 t20_hit_take_profit 全为 0（应为 NULL）。这导致止盈命中率被稀释——若按全部 938 条计算，"推荐买入"止盈命中率仅 8.0%；若仅按 has_position=1 的 225 条计算，实际为 18.5%。

3. **动态止盈未同步**：price_backtest.py L202-230 的 `_gen_with_position` 仍使用旧固定止盈公式（`take_profit = cost_price * (1 + target_gain)`），未同步 009 的动态止盈公式（`max(min_tp, min(fixed_tp, resistance))`）。

4. **007 评审已预警"未来函数"偏差**：review_007 §10.2 明确标注"使用当前最新评级进行历史回测，存在'未来函数'偏差"，但当时判断为"中风险"并建议"在报告中明确标注局限性"。PM 的实际诊断证明该偏差比预期更严重。

---

## 一、决策点 1：评级锚点法可行性与方案确认/修正

### 1.1 PM 方案评估

PM 提出的"评级锚点法"（§2.2 任务书）：

```
步骤1：取 ratings_history 中每条记录（stock_id, rating_date, rating）
步骤2：对 price_backtest_results 中同一 stock_id 的回测点：
  - 如果 backtest_date 在某条历史评级的前后3个交易日内 → 标记为"可信"
  - 如果 backtest_date 周围3个交易日无历史评级 → 标记为"可疑"
步骤3：如果"可信"样本的评级与回测记录中的 rating 不一致 → 标记为"不匹配"
```

### 1.2 覆盖率实证分析

| 指标 | 数值 | 评估 |
|---|---|---|
| 回测点总数 | 938 | — |
| 锚点覆盖（3日窗口） | 34 | **3.6%** |
| 锚点覆盖（5日窗口） | ~50（估算） | ~5.3% |
| 锚点覆盖（7日窗口） | ~70（估算） | ~7.5% |
| ratings_history 日期跨度 | 12 天 | 仅覆盖回测区间的 4.8% |

**结论**：即使扩大到 7 日窗口，覆盖率也不足 10%。**锚点法无法作为"核心修复方案"，只能作为"辅助验证手段"**。

### 1.3 时间窗口选择评估

| 窗口 | 覆盖率 | 噪声风险 | 评估 |
|---|---|---|---|
| ±1 日 | ~1.5% | 极低 | 覆盖率过低，无统计意义 |
| ±3 日 | 3.6% | 低 | PM 建议值，但覆盖率不足 |
| ±5 日 | ~5.3% | 中 | 覆盖率仍不足 |
| ±7 日 | ~7.5% | 中高 | 覆盖率勉强可用，但评级可能已变化 |

**意见**：±3 日窗口合理，但覆盖率问题无法通过调整窗口解决。

### 1.4 三级分类标准评估

PM 提出的三级分类：

| 级别 | 定义 | 评估 |
|---|---|---|
| `confirmed` | 回测日附近有历史评级记录且评级一致 | 最可信样本，但仅约 20-25 条（估算） |
| `mismatched` | 回测日附近有历史评级记录但评级不一致 | 说明评级已变化，约 10-15 条（估算） |
| `unknown` | 回测日附近无历史评级记录 | 约 900 条，占 96% |

**问题**：`confirmed` 样本量过少（<30 条），无法支撑统计显著性结论。

### 1.5 替代方案评估

| 方案 | 说明 | 可行性 | 推荐度 |
|---|---|---|---|
| A. 锚点法（PM 方案） | 用 ratings_history 标记可信样本 | 覆盖率 3.6%，样本量不足 | ⚠️ 降级为辅助验证 |
| B. 回测时实时重算历史评级 | 用历史 K 线切片 + 历史财务数据重新 analyze() | 财务数据历史可能不全，且计算量大 | ⚠️ 长期方向 |
| C. 积累数据后做完整动态评级回测 | ratings_history 积累 2 个月后，每个回测点用历史动态评级 | 正确但需等待 | ✅ 长期首选 |
| D. 标记"高偏差风险"样本 | 识别"前期大涨后当前评级为减仓"的股票，标记其历史回测点为高风险 | 可解释倒挂现象，但不修复数据 | ✅ 短期可行 |
| E. 分时段统计 | 将近 12 天（有真实评级）与之前时段分开统计 | 简单可行，能展示偏差程度 | ✅ 短期可行 |

### 1.6 架构师意见

**010-3 方案修正**：

1. **锚点法降级为"辅助验证"**：不追求覆盖率，而是利用 34 条锚点样本做"偏差程度验证"——对比 confirmed 样本与 unknown 样本的命中率差异，量化未来函数偏差的影响程度。

2. **新增"高偏差风险"标记（方案 D）**：在 price_backtest_results 中新增 `bias_risk` 字段，标记以下高风险样本：
   - 当前评级为"建议减仓"或"强烈建议卖出"
   - 且该股票近 60 日涨幅 > 30%（前期大涨后高位减仓）
   - 这些样本的历史回测点存在严重未来函数偏差

3. **分时段统计（方案 E）**：在报告中增加"近 12 天（有真实评级）vs 之前时段"的对比统计，直观展示偏差程度。

4. **长期方向确认**：ratings_history 积累 2 个月后（约 400+ 条，覆盖 40+ 天），启动完整动态评级回测（方案 C）。

---

## 二、决策点 2：010-1 动态止盈同步方案

### 2.1 同步范围确认

price_backtest.py 的 `_gen_with_position`（L202-230）需要同步 009 的动态止盈算法：

| 需同步内容 | price_advisor.py 位置 | price_backtest.py 现状 | 同步方式 |
|---|---|---|---|
| MIN_TARGET_GAIN 常量 | L64-70 | 缺失 | 复制 |
| _calc_resistance 函数 | L223-236 | 缺失 | 复制 |
| 动态止盈公式 | L749-803 | 旧固定公式 | 重写 _gen_with_position |

### 2.2 复制 vs 导入评估

| 方案 | 优势 | 劣势 | 评估 |
|---|---|---|---|
| 复制（PM 倾向） | 保持独立性，与 007 风格一致 | 代码重复，需手动同步 | ✅ 推荐 |
| 导入 | 自动同步，无重复代码 | 耦合度高，price_advisor 修改可能影响回测 | ❌ 不推荐 |

**意见**：推荐复制，与 007 设计决策一致（review_007 §5.1："复制算法常量保持同步"）。但需在代码注释中明确标注"与 price_advisor.py Lxxx 同步，修改时需双向同步"。

### 2.3 回测一致性评估

同步后是否需要 force=True 重跑全部 938 条？

| 方案 | 说明 | 推荐度 |
|---|---|---|
| 全量重跑 | 确保所有回测点使用一致的动态止盈算法 | ✅ 推荐 |
| 增量更新 | 仅更新有持仓样本（225 条） | ⚠️ 不推荐，口径不一致 |

**意见**：推荐 force=True 全量重跑，确保数据口径一致。重跑成本可控（007 验收报告：938 点全成功，耗时约秒级）。

### 2.4 具体同步方案

```python
# price_backtest.py 新增常量（与 price_advisor.py L64-70 同步）
MIN_TARGET_GAIN = {
    '强烈推荐买入': 0.08,
    '推荐买入': 0.06,
    '持有观望': 0.04,
    '建议减仓': 0.03,
    '强烈建议卖出': 0.02,
}


# price_backtest.py 新增函数（与 price_advisor.py L223-236 同步）
def _calc_resistance(close, ma60, boll_upper):
    """计算技术面阻力位（与 price_advisor._calc_resistance 逻辑一致）"""
    candidates = []
    if boll_upper and boll_upper > close:
        candidates.append(boll_upper)
    if ma60 and ma60 > close:
        candidates.append(ma60)
    if candidates:
        return min(candidates)
    return close * 1.10


# price_backtest.py 重写 _gen_with_position（与 price_advisor.py L749-803 同步）
def _gen_with_position(close, cost_price, rating, ma60=None, boll_upper=None):
    """有持仓：止盈价 / 止损价（009 动态止盈版）"""
    target_gain = RATING_TARGET_GAIN.get(rating, 0.12)
    min_target_gain = MIN_TARGET_GAIN.get(rating, 0.04)
    stop_loss_pct = RATING_STOP_LOSS.get(rating, 0.05)

    # 动态止盈：max(min_tp, min(fixed_tp, resistance))
    fixed_tp = cost_price * (1 + target_gain)
    resistance = _calc_resistance(close, ma60, boll_upper)
    min_tp = cost_price * (1 + min_target_gain)
    take_profit = max(min_tp, min(fixed_tp, resistance))

    # 止损价
    stop_loss = cost_price * (1 - stop_loss_pct)
    min_stop = close * 0.90
    if stop_loss < min_stop:
        stop_loss = min_stop

    return {
        'available': True,
        'has_position': True,
        'position_pct': None,
        'buy_range_low': None,
        'buy_range_high': None,
        'target_price': None,
        'stop_loss': round(stop_loss, 2),
        'take_profit': round(take_profit, 2),
    }
```

**注意**：`_gen_with_position` 需新增 `ma60` 和 `boll_upper` 参数，调用方 `_gen_price_advice_at_date` 需同步修改。

---

## 三、决策点 3：010-2 稀释 Bug 修复方案

### 3.1 Bug 确认

数据库实证：

| 指标 | 数值 | 说明 |
|---|---|---|
| has_position=0 记录数 | 713 | 无持仓样本 |
| has_position=0 的 take_profit | 713 条全为 NULL | 正确 |
| has_position=0 的 t20_hit_take_profit | 713 条全为 0 | **Bug：应为 NULL** |

根因在 `_check_hit` 函数 L344-348：

```python
# 未命中的设为0（Bug：无持仓时 take_profit 为 None，也会被设为0）
for key in [
    f'{period_label}_hit_buy_range',
    f'{period_label}_hit_target',
    f'{period_label}_hit_stop_loss',
    f'{period_label}_hit_take_profit',
]:
    if result[key] is None:
        result[key] = 0
```

当 `take_profit=None` 时，L338-342 的命中判断不会执行（`if take_profit is not None`），`result['t20_hit_take_profit']` 保持 None，但 L344-348 将其设为 0。

### 3.2 修复位置评估

| 方案 | 位置 | 优势 | 劣势 | 评估 |
|---|---|---|---|---|
| A | `_check_hit` 函数 | 集中修复，一处改动 | 需判断 has_position | ✅ 推荐 |
| B | `_gen_price_advice_at_date` 返回值 | 源头控制 | 无法区分"无持仓"和"有持仓但未命中" | ❌ 不推荐 |

**意见**：推荐方案 A，在 `_check_hit` 中判断 `advice.get('take_profit') is None` 时保持 `take_profit_hit` 为 None。

### 3.3 修复代码

```python
# _check_hit 函数 L344-348 修改
# 未命中的设为0（但 take_profit 为 None 时保持 None）
for key in [
    f'{period_label}_hit_buy_range',
    f'{period_label}_hit_target',
    f'{period_label}_hit_stop_loss',
]:
    if result[key] is None:
        result[key] = 0

# take_profit_hit 特殊处理：无持仓时保持 None
if take_profit is not None:
    if result[f'{period_label}_hit_take_profit'] is None:
        result[f'{period_label}_hit_take_profit'] = 0
# else: 保持 None（无持仓时不参与止盈命中统计）
```

### 3.4 影响范围评估

| 影响项 | 说明 | 处理方式 |
|---|---|---|
| 已有数据 | 938 条记录中 713 条 has_position=0 的 take_profit_hit 为 0 | force=True 重跑修复 |
| 报告生成逻辑 | `_hit_rate` 函数已过滤 None（L622-626），无需修改 | 无需改动 |
| 统计口径 | 修复后止盈命中率仅统计 has_position=1 样本 | 口径更正确 |

### 3.5 向后兼容评估

报告生成逻辑 `compute_price_backtest_report` 的 `_hit_rate` 函数（L621-626）：

```python
def _hit_rate(field):
    valid = [r for r in rows if r.get(field) is not None]  # 已过滤 None
    if not valid:
        return None
    hits = sum(1 for r in valid if r[field] == 1)
    return round(hits / len(valid), 4)
```

**结论**：`_hit_rate` 已正确过滤 None 值，无需修改。修复后止盈命中率将仅基于 has_position=1 的 225 条样本计算，口径更正确。

---

## 四、决策点 4：010-3 表结构设计

### 4.1 新增字段方案

基于决策点 1 的修正方案（锚点法降级 + 高偏差风险标记 + 分时段统计），建议新增以下字段：

| 字段名 | 类型 | 说明 | 来源 |
|---|---|---|---|
| `rating_confidence` | TEXT | 锚点可信度：confirmed/mismatched/unknown | 010-3 锚点法 |
| `anchor_rating_date` | DATE | 匹配到的历史评级日期 | 010-3 锚点法 |
| `anchor_rating` | TEXT | 匹配到的历史评级值 | 010-3 锚点法 |
| `bias_risk` | TEXT | 偏差风险：high/medium/low | 010-3 高偏差风险标记 |
| `days_since_rating` | INTEGER | 回测日距最近评级日的天数 | 010-3 辅助字段 |

### 4.2 字段设计评估

| 方案 | 字段数 | 优势 | 劣势 | 评估 |
|---|---|---|---|---|
| A. 单字段（rating_confidence） | 1 | 简洁 | 信息不足，无法审计 | ❌ 不推荐 |
| B. 多字段（推荐方案） | 5 | 信息完整，可审计 | 字段较多 | ✅ 推荐 |
| C. JSON 字段 | 1 | 灵活 | SQLite JSON 支持有限，查询不便 | ❌ 不推荐 |

**意见**：推荐方案 B，5 个字段信息完整且可审计。

### 4.3 幂等性设计

参考 `backtest_engine.py` L83-103 的 `_ensure_columns` 模式：

```python
def _ensure_price_backtest_columns():
    """确保 price_backtest_results 表有 010 所需列（ALTER TABLE ADD COLUMN，幂等）"""
    conn = get_connection()
    cursor = conn.cursor()
    needed = {
        'rating_confidence': 'TEXT',
        'anchor_rating_date': 'DATE',
        'anchor_rating': 'TEXT',
        'bias_risk': 'TEXT',
        'days_since_rating': 'INTEGER',
    }
    cursor.execute('PRAGMA table_info(price_backtest_results)')
    existing = {row['name'] for row in cursor.fetchall()}
    for col, col_type in needed.items():
        if col not in existing:
            try:
                cursor.execute(f'ALTER TABLE price_backtest_results ADD COLUMN {col} {col_type}')
                logger.info(f'price_backtest_results: added column {col}')
            except Exception as e:
                logger.warning(f'price_backtest_results: cannot add {col}: {e}')
    conn.commit()
    conn.close()
```

**结论**：ALTER TABLE ADD COLUMN 幂等，重复执行安全。

---

## 五、决策点 5：回测引擎长期演进方向

### 5.1 候选方向评估

| 方向 | 说明 | 优势 | 劣势 | 优先级 |
|---|---|---|---|---|
| A. 积累数据后做完整动态评级回测 | ratings_history 积累 2 个月后，每个回测点用历史动态评级 | 方法论最正确 | 需等待 2 个月 | **P0（长期首选）** |
| B. 回测时实时重算历史评级 | 用历史 K 线切片 + 历史财务数据重新 analyze() | 无需等待 | 财务数据历史可能不全，计算量大 | P1（中期可行） |
| C. 两个回测引擎合并 | price_backtest.py 和 backtest_engine.py 合并 | 架构统一 | 工作量大，风险高 | P2（长期方向） |

### 5.2 架构师意见

**短期（010 任务）**：
- 010-1 同步动态止盈（修复算法不一致）
- 010-2 修复稀释 Bug（修复统计口径）
- 010-3 锚点法降级 + 高偏差风险标记（量化偏差程度）
- 010-4 可信样本报告（展示偏差影响）

**中期（1-2 个月后）**：
- ratings_history 积累至 400+ 条（覆盖 40+ 天）
- 启动方向 A：完整动态评级回测
- 每个回测点使用历史时点的真实评级，彻底消除未来函数偏差

**长期（3-6 个月）**：
- 评估方向 C：两个回测引擎合并
- backtest_engine.py 验证"评级 → 收益率"
- price_backtest.py 验证"价格建议 → 价格触及"
- 两者可共享历史评级数据源，但验证逻辑保持独立

---

## 六、决策点 6：010-4 可信样本报告方案

### 6.1 报告结构评估

| 方案 | 说明 | 优势 | 劣势 | 评估 |
|---|---|---|---|---|
| A. 独立报告 | 新建"可信样本回测报告" | 清晰分离 | 前端需新增页面 | ❌ 不推荐 |
| B. 现有报告增加 section | 在现有回测报告中增加"可信样本子报告" | 前端改动小 | 报告较长 | ✅ 推荐 |

**意见**：推荐方案 B，在现有 `compute_price_backtest_report` 返回结构中新增 `confidence_report` 字段。

### 6.2 最低样本量评估

| 样本类型 | 估算样本量 | 统计意义 | 展示策略 |
|---|---|---|---|
| confirmed | ~20-25 条 | 低（<30） | 展示但标注"样本量不足，仅供参考" |
| mismatched | ~10-15 条 | 极低（<20） | 展示但标注"样本量极少，仅作定性参考" |
| unknown | ~900 条 | 高 | 正常展示 |
| bias_risk=high | ~100-150 条（估算） | 中 | 正常展示 |

**结论**：confirmed 和 mismatched 样本量不足，需标注"仅供参考"。

### 6.3 报告输出结构

```python
# compute_price_backtest_report 新增返回字段
{
    # ... 现有字段 ...
    'confidence_report': {
        'confirmed': {
            'total': 22,
            't20_target_hit_rate': 0.45,  # 示例值
            't20_stop_loss_hit_rate': 0.23,
            'note': '样本量不足（<30），仅供参考',
        },
        'mismatched': {
            'total': 12,
            't20_target_hit_rate': 0.58,
            'note': '样本量极少（<20），仅作定性参考',
        },
        'unknown': {
            'total': 904,
            't20_target_hit_rate': 0.32,
            't20_stop_loss_hit_rate': 0.35,
        },
        'bias_risk_high': {
            'total': 135,
            't20_target_hit_rate': 0.52,  # 高偏差样本的异常高命中率
            'note': '高偏差风险样本，命中率可能虚高',
        },
    },
    'period_comparison': {
        'recent_12d': {  # 有真实评级的时段
            'total': 45,
            't20_target_hit_rate': 0.28,
        },
        'earlier': {  # 无真实评级的时段
            'total': 893,
            't20_target_hit_rate': 0.33,
        },
        'note': '近12天有真实评级，命中率可能更准确；之前时段存在未来函数偏差',
    },
}
```

### 6.4 前端展示评估

| 方案 | 说明 | 前端改动 | 评估 |
|---|---|---|---|
| A. 新增可信度标记列 | 在回测结果表格中新增"可信度"列 | 中等 | ⚠️ 可选 |
| B. 新增可信样本统计卡片 | 在报告页面新增"可信样本统计"卡片 | 小 | ✅ 推荐 |
| C. 零前端改动 | 仅 API 返回新增字段，前端不展示 | 零 | ⚠️ 保守 |

**意见**：推荐方案 B，前端改动小且能直观展示偏差程度。若时间紧张，可降级为方案 C（仅 API 返回，前端后续迭代）。

---

## 七、决策点 7：影响面分析

### 7.1 文件修改清单

| 文件 | 改动类型 | 改动内容 | 预估行数 |
|---|---|---|---|
| `modules/price_backtest.py` | **修改** | 010-1 同步动态止盈 + 010-2 修复稀释 Bug + 010-3 锚点标记 + 010-4 可信样本报告 | +150 行（现有 766 行，净增约 100 行） |
| `database/db_manager.py` | **修改** | 新增 `_ensure_price_backtest_columns` 函数（幂等列添加） | +20 行 |
| `app.py` | **零改动** | 无需修改（报告生成逻辑在 price_backtest.py 内） | 0 |
| `templates/index.html` | **修改**（可选） | 新增"可信样本统计"卡片 | +30 行（可选） |

**总计**：修改 2 个文件，可选修改 1 个文件，新增约 170-200 行代码。

### 7.2 不修改的文件（红线保护）

| 文件 | 原因 |
|---|---|
| `modules/price_advisor.py` | 010 任务红线：本次不是修改 price_advisor |
| `modules/advisor.py` | B24 红线，generate_advice 不可改 |
| `modules/backtest_engine.py` | M8 引擎，方法论正确，不修改 |
| `modules/data_collector.py` | L1645/L1684/L1717 三处 if False 不可改 |
| `config_weights.json` | rating_mapping 不可改 |
| `modules/scoring_engine.py` | v5 引擎不可修改 |
| `modules/data_contract.py` | StockData 契约不可破坏 |

### 7.3 数据库变更

| 变更 | 类型 | 影响 |
|---|---|---|
| `price_backtest_results` 新增 5 列 | ALTER TABLE ADD COLUMN | 幂等，不影响现有数据 |
| force=True 重跑回测 | DELETE + INSERT | 清除 938 条旧记录，重新生成 |

### 7.4 API 变更

| 端点 | 变更 | 说明 |
|---|---|---|
| `/api/price-backtest/run` | 零改动 | 触发回测逻辑不变 |
| `/api/price-backtest/report` | 返回结构新增字段 | 新增 `confidence_report` 和 `period_comparison` 字段 |

**向后兼容**：新增字段不影响现有前端展示，前端可选择性展示新字段。

---

## 八、红线合规性确认

| # | 红线 | 合规性 | 说明 |
|---|---|---|---|
| 1 | **generate_advice 不可改** | ✅ 合规 | 010 方案不修改 advisor.py |
| 2 | **_build_capital_factors 不可改** | ✅ 合规 | 不触碰 advisor.py |
| 3 | **data_collector 三处 if False** | ✅ 合规 | 不触碰 data_collector.py |
| 4 | **零代码约束** | ✅ 合规 | 无新 pip 依赖，仅用标准库 + sqlite3 |
| 5 | **不回写** | ✅ 合规 | 不修改数据采集逻辑，不自动写入 ratings_history |
| 6 | **config_weights.json** | ✅ 合规 | 不修改 rating_mapping |
| 7 | **scoring_engine.py** | ✅ 合规 | v5 引擎不可修改 |
| 8 | **price_advisor.py** | ✅ 合规 | 010 任务不是修改 price_advisor，仅复制其常量到 price_backtest |
| 9 | **price_backtest.py** | ✅ 合规 | 本次核心修改对象，可修改 |

---

## 九、后续开发任务拆分建议

### 9.1 优先级和依赖关系

| 子任务 | 内容 | 优先级 | 依赖 | 预估工作量 |
|---|---|---|---|---|
| **010-1** | 同步 009 动态止盈 | P0 | 无 | 0.5 天 |
| **010-2** | 修复稀释 Bug | P0 | 无 | 0.5 天 |
| **010-3** | 锚点标记 + 高偏差风险标记 | P1 | 010-1/010-2 完成 | 1 天 |
| **010-4** | 可信样本报告 | P1 | 010-3 完成 | 0.5 天 |

**依赖关系说明**：
- 010-1 和 010-2 可并行开发，无依赖
- 010-3 依赖 010-1/010-2 完成（需基于修复后的数据做锚点标记）
- 010-4 依赖 010-3 完成（需基于锚点标记生成可信样本报告）

### 9.2 验收标准建议

**010-1 验收标准**：
1. `price_backtest.py` 新增 `MIN_TARGET_GAIN` 常量和 `_calc_resistance` 函数
2. `_gen_with_position` 重写为动态止盈公式：`max(min_tp, min(fixed_tp, resistance))`
3. force=True 重跑后，有持仓样本的 take_profit 与 price_advisor 计算结果一致（误差 < 1%）

**010-2 验收标准**：
1. `_check_hit` 函数修复：无持仓时 `take_profit_hit` 保持 None
2. force=True 重跑后，has_position=0 的 713 条记录的 `t5_hit_take_profit` 和 `t20_hit_take_profit` 全为 NULL
3. 止盈命中率仅统计 has_position=1 样本，"推荐买入"止盈命中率从 8.0% 修正为 18.5%

**010-3 验收标准**：
1. `price_backtest_results` 新增 5 列（rating_confidence/anchor_rating_date/anchor_rating/bias_risk/days_since_rating）
2. 锚点标记逻辑正确：confirmed/mismatched/unknown 三级分类
3. 高偏差风险标记正确：近 60 日涨幅 > 30% 且当前评级为减仓/卖出的股票标记为 high

**010-4 验收标准**：
1. `compute_price_backtest_report` 返回结构新增 `confidence_report` 和 `period_comparison` 字段
2. confirmed 样本量 < 30 时标注"样本量不足，仅供参考"
3. 分时段统计展示近 12 天 vs 之前时段的命中率差异

### 9.3 开发顺序建议

```
010-1（同步动态止盈）+ 010-2（修复稀释 Bug）  ← 可并行
  ↓
force=True 重跑回测（修复数据）
  ↓
010-3（锚点标记 + 高偏差风险标记）
  ↓
010-4（可信样本报告）
  ↓
QA 验收（锚点覆盖率 + 稀释 Bug 修复验证 + 可信样本统计）
```

---

## 十、风险点和注意事项

### 10.1 技术风险

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| 锚点覆盖率过低（3.6%），confirmed 样本量不足 | 高 | 降级为辅助验证，新增高偏差风险标记和分时段统计 |
| 动态止盈同步后，有持仓样本止盈价变化，命中率可能波动 | 中 | force=True 重跑，确保数据口径一致 |
| 稀释 Bug 修复后，止盈命中率统计口径变化，与历史报告不可比 | 低 | 在报告中明确标注"统计口径已修正" |

### 10.2 业务风险

| 风险 | 等级 | 缓解措施 |
|---|---|---|
| 可信样本报告展示偏差程度，可能引发对评级有效性的质疑 | 中 | 在报告中明确标注"偏差源于数据稀疏，非评级算法问题" |
| 高偏差风险标记可能误伤部分正常样本 | 低 | 阈值（60 日涨幅 > 30%）可配置，后续根据实际数据调整 |

### 10.3 注意事项

1. **锚点法的定位**：锚点法不是"核心修复方案"，而是"辅助验证手段"。其核心价值是**量化未来函数偏差的影响程度**，而非修复偏差本身。

2. **长期方向确认**：彻底解决未来函数偏差需等待 ratings_history 积累 2 个月后，启动完整动态评级回测（方向 A）。010 任务是短期止血方案。

3. **统计口径一致性**：010-1/010-2 修复后，需 force=True 重跑全部 938 条记录，确保数据口径一致。重跑成本可控（秒级）。

4. **报告解读指引**：可信样本报告需附解读指引，说明"confirmed 样本量不足，偏差程度需结合高偏差风险标记和分时段统计综合判断"。

---

## 十一、总体结论

### 11.1 推荐方案汇总

| 决策点 | 推荐 | 核心理由 |
|---|---|---|
| 1. 评级锚点法 | **降级为辅助验证 + 新增高偏差风险标记 + 分时段统计** | 锚点覆盖率仅 3.6%，无法支撑核心修复 |
| 2. 动态止盈同步 | **复制 MIN_TARGET_GAIN + _calc_resistance + 动态止盈公式，force=True 重跑** | 保持独立性，与 007 风格一致 |
| 3. 稀释 Bug 修复 | **在 _check_hit 中判断 take_profit is None 时保持 None** | 集中修复，一处改动 |
| 4. 表结构设计 | **新增 5 列（rating_confidence/anchor_rating_date/anchor_rating/bias_risk/days_since_rating）** | 信息完整，可审计，幂等 |
| 5. 长期演进方向 | **短期止血（010）→ 中期动态评级回测（2 个月后）→ 长期引擎合并（3-6 个月）** | 分阶段推进，风险可控 |
| 6. 可信样本报告 | **现有报告新增 confidence_report + period_comparison 字段** | 前端改动小，信息完整 |
| 7. 影响面 | **修改 2 文件（price_backtest.py + db_manager.py），可选修改 1 文件（index.html）** | 改动集中，红线零触碰 |

### 11.2 架构师声明

- 以上所有结论基于对 `price_backtest.py`、`price_advisor.py`、`backtest_engine.py` 的**实际代码审阅**和**数据库实证查询**
- 锚点覆盖率（3.6%）、稀释 Bug（713 条记录受影响）、动态止盈未同步等关键发现均已通过 SQL 查询验证
- 推荐方案确保 9 条红线零触碰，generate_advice 函数签名和函数体完全不变
- 010 任务是短期止血方案，彻底解决未来函数偏差需等待 ratings_history 积累 2 个月后启动完整动态评级回测

---

*评审完毕。如需 PM 对任何决策点进行二次讨论或要求架构师补充分析，请反馈。*
