# 代码评审意见：RATING-ALIGN-004 + M8-BACKTEST-003

| 项目 | 内容 |
|---|---|
| **文档编号** | REVIEW-CODE-20260722 |
| **评审类型** | 代码评审（架构师，响应 ARCH-TASK-20260722 任务C） |
| **评审日期** | 2026-07-22 |
| **评审人** | 架构师（AI） |
| **关联任务** | RATING-ALIGN-004 / M8-BACKTEST-003 |
| **结论** | **有条件通过** — 核心逻辑正确，2项需修复，3项建议优化 |

---

## 一、RATING-ALIGN-004 评审

### 1.1 5档边界 85/70/50/30 — ✅ 通过

[scoring_engine.py L58-63](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\scoring_engine.py#L58) `RATING_THRESHOLDS`：

```python
'强烈推荐买入': {'min': 85, 'max': 100},
'推荐买入':     {'min': 70, 'max': 84},
'持有观望':     {'min': 50, 'max': 69},
'建议减仓':     {'min': 30, 'max': 49},
'强烈建议卖出': {'min': 0,  'max': 29},
```

[_map_rating()](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\scoring_engine.py#L909) L914-920：按 min 降序遍历，`total_score >= info['min']` 匹配。

**边界值逐项验证**（对应 QA 测试用例 #1-#9）：

| 得分 | 匹配路径 | 结果 | 正确？ |
|---|---|---|---|
| 85.0 | 85≥85 → 强烈推荐买入 | 强烈推荐买入 | ✅ |
| 84.9 | 84.9<85, 84.9≥70 → 推荐买入 | 推荐买入 | ✅ |
| 70.0 | 70≥70 → 推荐买入 | 推荐买入 | ✅ |
| 69.9 | 69.9<70, 69.9≥50 → 持有观望 | 持有观望 | ✅ |
| 50.0 | 50≥50 → 持有观望 | 持有观望 | ✅ |
| 49.9 | 49.9<50, 49.9≥30 → 建议减仓 | 建议减仓 | ✅ |
| 30.0 | 30≥30 → 建议减仓 | 建议减仓 | ✅ |
| 29.9 | 29.9<30, 29.9≥0 → 强烈建议卖出 | 强烈建议卖出 | ✅ |
| 0.0 | 0≥0 → 强烈建议卖出 | 强烈建议卖出 | ✅ |

**结论**：全部9个边界值测试通过。

### 1.2 历史 A/B+/B/C/D 兼容 — 🟡 有条件通过

[normalize_rating()](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\scoring_engine.py#L76) L76-96：

**设计正确**：
- 有 `total_score` 时 → 调用 `_map_rating(float(total_score))` 精确映射（推荐路径）
- 无 `total_score` 时 → 用 `RATING_LEGACY_MAP` 字符串近似映射（降级路径）

**ISSUE-1（🟡 需修复）：`RATING_LEGACY_MAP` 映射偏差**

[scoring_engine.py L67-73](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\scoring_engine.py#L67)：

```python
RATING_LEGACY_MAP = {
    'A': '强烈推荐买入',
    'B+': '推荐买入',
    'B': '持有观望',
    'C': '持有观望',  # ← 应为 '建议减仓'
    'D': '建议减仓',  # ← 应为 '强烈建议卖出'
}
```

**问题**：旧5档(A/B+/B/C/D)映射到新5档时，C 和 D 各偏移了一档。且**无任何旧档映射到"强烈建议卖出"**。

**影响范围**：仅影响 `ratings_history` 中 `total_score` 为 NULL 的历史记录。实际数据库中绝大多数记录有 total_score，走精确映射路径，影响有限。

**建议修复**：
```python
'C': '建议减仓',
'D': '强烈建议卖出',
```

### 1.3 前端展示同步 — 🟡 有条件通过

**已切换的区域**：

| 位置 | 状态 | 说明 |
|---|---|---|
| 筛选下拉框 (L821-825) | ✅ | 中文5档 |
| 报告页 rating_label (L1777/L1890) | ✅ | 显示 `data.rating_label` |
| CSS 样式映射 (L2171-2175) | ✅ | 兼容新旧 |
| 看板评级排序 (L4136) | ✅ | `ratingOrder` 中文5档 |

**ISSUE-2（🟡 需修复）：看板筛选器仍使用旧字母档位**

[templates/index.html L4042](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\templates\index.html#L4042)：

```html
<option value="B">B (谨慎买入)</option>
<option value="C">C (持有观望)</option>
<option value="D">D (减仓/卖出)</option>
```

**问题**：看板评级筛选器仍使用旧字母代码（B/C/D），与新中文5档数据不匹配。新数据评级字段值为"强烈推荐买入"等中文，筛选 `value="B"` 无法命中。

**建议修复**：改为中文5档：
```html
<option value="强烈推荐买入">强烈推荐买入</option>
<option value="推荐买入">推荐买入</option>
<option value="持有观望">持有观望</option>
<option value="建议减仓">建议减仓</option>
<option value="强烈建议卖出">强烈建议卖出</option>
```

### 1.4 RATING-ALIGN-004 评审汇总

| 验收标准 | 状态 | 说明 |
|---|---|---|
| ① 新评级严格按 85/70/50/30 边界划分 | ✅ 通过 | 9个边界值全验证正确 |
| ② 历史 A/B+/B/C/D 数据可追溯 | ✅ 通过 | normalize_rating 优先分数精确映射 |
| ③ 用户界面全量切换为中文5档 | 🟡 有条件 | ISSUE-2：看板筛选器遗漏 |
| ④ M8 回测兼容双档位 | ✅ 通过 | 见 M8 评审 |

---

## 二、M8-BACKTEST-003 评审

### 2.1 三层架构 — ✅ 通过

| 层 | 实现 | 说明 |
|---|---|---|
| 数据层 | `ratings_history` + `raw_kline` 读取 | 通过 SQL 查询获取评级和价格 |
| 计算层 | [BacktestEngine](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py#L109) 类 | 固定周期 + 动态周期 + 判定矩阵 |
| 存储层 | `backtest_results` 表（UPSERT） | 幂等写入，支持重跑 |

[_ensure_columns()](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py#L83) L83-102：安全追加列（ALTER TABLE ADD COLUMN，幂等），设计正确。

### 2.2 固定周期回测 — ✅ 通过

[run_fixed_period_backtest()](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py#L178) L178-276：

- `FIXED_PERIODS = {'1d': 1, '1w': 5, '1m': 20}` — 对应 T+1/T+5/T+20
- T+N 价格获取使用 `OFFSET n-1`（L136-137），正确跳过非交易日
- 收益率计算（L170-174）：`(target - base) / base * 100`，正确
- 主判定优先级：1d → 1w → 1m（L218-222），合理

### 2.3 动态周期回测 — ✅ 通过

[_compute_dynamic()](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py#L280) L280-314：

- 查找 `rating_date` 之后下一次 `is_change=1` 的评级记录（L286-291）
- 用评级变更时的价格作为终点（L298-299）
- 若终点价格为空，回退到最新K线收盘价（L302-305）
- 逻辑正确，边界处理完善

### 2.4 判定矩阵 — 🟡 有条件通过

[_judge()](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py#L49) L49-76 + `JUDGEMENT_MATRIX` L40-46：

**设计正确**：
- 买入方向(up)：收益≥阈值=正确，收益≤负阈值=错误，中间=中性(None)
- 卖出方向(down)：收益≤负阈值=正确，收益≥正阈值=错误
- 中性方向(neutral)：收益在窄幅区间=正确，否则=错误

**ISSUE-3（🟡 建议优化）：建议减仓与强烈建议卖出阈值完全相同**

```python
'建议减仓':     {'direction': 'down', 'correct_max': -1.0, 'wrong_min': 3.0},
'强烈建议卖出': {'direction': 'down', 'correct_max': -1.0, 'wrong_min': 3.0},  # 完全相同！
```

**建议**：强烈建议卖出应有更严格的判定（如 `correct_max: -2.0, wrong_min: 5.0`），体现"强烈"的预期更强。

**ISSUE-4（🟡 建议优化）：持有观望中性判定无中性区**

```python
else:  # neutral
    if config['correct_low'] <= return_pct <= config['correct_high']:
        return 1
    return 0  # 超出范围直接判 wrong，无 None 区
```

对比 up/down 方向有"中性区"（correct_min 和 wrong_max 之间返回 None），neutral 方向没有。持有观望的股票涨5%被判"错误"过于严格。

**建议**：增加中性区（如 ±3%~±10% 返回 None），仅极端偏离才判 wrong。

### 2.5 A/H 双市场独立 — ✅ 通过

- `batch_backtest(market='a_stock'/'hk_stock'/None)` 支持按市场筛选
- `compute_market_report(market)` 分别生成 A股/港股报告
- `backtest_results` 表有 `market` 列，存储时区分
- 前端有市场选择器（[L884](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\templates\index.html#L884) `btMarketSelect`）

### 2.6 双档位兼容 — ✅ 通过

[backtest_engine.py L205](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py#L205)：

```python
rating_norm = normalize_rating(rating_raw, rating_row.get('total_score'))
```

通过 `normalize_rating()` 统一新旧评级到中文5档，再查 `JUDGEMENT_MATRIX`。设计正确。

### 2.7 自动触发机制 — ✅ 通过

[advisor.py L710-738](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\advisor.py#L710)：

```python
is_changed = prev_rating_norm is not None and prev_rating_norm != analysis['rating']
_save_rating(stock_id, analysis, action, is_changed, latest_close_info)
if is_changed:
    from modules.backtest_engine import BacktestEngine

    BacktestEngine().auto_trigger_backtest(stock_id, bt_date)
```

- 评级变更检测：归一化后比较（L710），兼容新旧档位
- 触发时机：`_save_rating` 之后，非阻塞（try/except L737-738）
- `fill_pending_backtests()` 补算机制：填充到期未回测的记录

### 2.8 Web 界面可视化 — ✅ 通过

- 导航栏有"🔬 回测中心"入口（[L721](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\templates\index.html#L721)）
- 市场级报告展示（`loadBacktestMarketReport`）
- 个股回测明细（`loadBacktestStockDetail`）
- 手动重跑按钮（`rerunBacktest`）
- API 端点齐全：`/api/backtest/market-report`、`/api/backtest/stock/<id>`、`/api/backtest/rerun`、`/api/backtest/status`

### 2.9 D4 权重实验预留 — ✅ 通过（占位实现可接受）

[WeightExperimentRunner](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\backtest_engine.py#L612) L612-724：

- 2个实验场景定义（A股 + 港股消息面 0→20%）
- `run_experiment()` 提供框架和接口
- `_estimate_weight_impact()` 返回 0.0（标注"待M9完整实施"）
- ⚠️ 占位实现，不影响M8验收，M9阶段需完整实现

### 2.10 M8-BACKTEST-003 评审汇总

| 验收标准 | 状态 | 说明 |
|---|---|---|
| ① ratings_history 全量回测 | ✅ | batch_backtest 支持全量 |
| ② backtest_results 填充率100% | ✅ | UPSERT + fill_pending 机制 |
| ③ A/H 双市场独立报告 | ✅ | 市场参数分流 |
| ④ 评级变更自动触发 | ✅ | advisor.py 集成 |
| ⑤ Web 界面可视化 | ✅ | 回测中心页面 |

---

## 三、ISSUE 汇总与处置建议

| # | 级别 | 任务 | 问题 | 处置建议 |
|---|---|---|---|---|
| ISSUE-1 | 🟡 需修复 | RATING-ALIGN-004 | `RATING_LEGACY_MAP` C/D 映射偏移 | 改 C→建议减仓, D→强烈建议卖出（0.1人天） |
| ISSUE-2 | 🟡 需修复 | RATING-ALIGN-004 | 看板筛选器 L4042 仍用旧字母档位 | 改为中文5档下拉选项（0.1人天） |
| ISSUE-3 | 🟡 建议优化 | M8-BACKTEST-003 | 建议减仓与强烈建议卖出阈值相同 | 区分阈值（可在观察期后调整） |
| ISSUE-4 | 🟡 建议优化 | M8-BACKTEST-003 | 持有观望无中性区，判定过于严格 | 增加 ±3%~±10% 中性区 |
| ISSUE-5 | 🟢 信息 | M8-BACKTEST-003 | 权重实验为占位实现 | 预期行为，M9阶段完善 |

---

## 四、红线核验

| 红线 | RATING-ALIGN-004 | M8-BACKTEST-003 |
|---|---|---|
| ① 零代码约束 | ✅ 档位切换对用户透明 | ✅ 回测在Web界面展示 |
| ② 需求基线 | ✅ 严格对齐 §2.3.1 | ✅ 严格对齐 §2.8 |
| ③ v5数据契约 | ✅ 无影响 | ✅ 无影响 |
| ④ 禁用估算值 | ✅ 不涉及 | ✅ 回测使用真实价格数据 |
| ⑤ 防覆盖机制 | ✅ 不涉及 | ✅ UPSERT 幂等 |
| ⑥ M8→M9顺序 | ✅ 不涉及 | ✅ 权重实验仅模拟不改生产 |
| ⑦ A/H双市场独立 | ✅ 不涉及 | ✅ 市场参数分流 |

---

## 五、结论

| 任务 | 结论 | 前置条件 |
|---|---|---|
| **RATING-ALIGN-004** | **有条件通过** | 修复 ISSUE-1 + ISSUE-2 后可验收 |
| **M8-BACKTEST-003** | **通过** | ISSUE-3/4 建议在回测数据积累后优化 |

**报监理批准后交开发执行 ISSUE-1 + ISSUE-2 修复（合计 0.2人天）。**

---

**编制人**：架构师 | **编制时间**：2026-07-22
