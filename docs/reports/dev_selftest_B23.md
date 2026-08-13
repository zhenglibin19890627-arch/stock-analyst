# B23 开发自验报告

**批次**：B23
**任务**：回测模拟改四维评分（B16 遗留观察项 #3）
**任务书**：`docs/tasks/dev_tasks_20260726_B23.md`
**推荐模型**：glm5.2（GLM Plan）
**自验日期**：2026-07-26
**自验结论**：✅ 全部通过

---

## 一、改动摘要

**修改文件**：`modules/backtest_engine.py`（唯一改动文件）

**核心改动**：`run_historical_simulation()`（L710 起）从"技术面单维度评分"改为"四维综合评分"。

| 项 | 改动前 | 改动后 |
|---|---|---|
| 评分来源 | `_calc_technical_score_from_kline()` 截取 K 线切片做纯技术面 | `data_adapter.load_stockdata_from_db()` + `scoring_engine.analyze()` 四维综合评分 |
| 评级映射 | `_score_to_rating(tech_score)` | `analysis.rating`（analyze 返回的中文5档） |
| 评分时点 | 每个模拟评级日单独评分 | 每只股票调用一次（load_stockdata_from_db 返回当前最新数据，整股共享同一四维评级） |
| 幂等策略 | skip-if-exists（重跑跳过旧行，新逻辑不生效） | 先 DELETE is_simulated=1 旧行再重新生成（保证四维逻辑生效且无重复行） |

**设计说明（前瞻偏差）**：`load_stockdata_from_db` 返回的是当前最新数据，因此同一只股票的所有模拟评级日共享同一四维评级，基本面/资金面/消息面为回测时点之后的数据，引入轻微前瞻偏差。任务书明确该偏差对"评估评级有效性"可接受。

**幂等性说明**：模拟数据（is_simulated=1）是可重建的派生数据，每次执行先清除再重新生成；真实回测数据（is_simulated=0）不受影响。重复执行结果一致、不产生重复行。

---

## 二、验收项核验

### V1：run_historical_simulation 调用四维评分 ✅

代码审查：函数内调用 `load_stockdata_from_db(stock_id)` 构建完整 StockData，再调用 `analyze(stock_data)` 取 `analysis.rating`，替换原有 `_calc_technical_score_from_kline` + `_score_to_rating`。运行日志每只股票均显示 `活跃维度=['capital_flow', 'fundamental', 'kline', 'news']`，四维全部激活。

### V2：模拟回测正常执行无报错 ✅

执行结果：
```
{'total': 324, 'success': 324, 'errors': 0, 'skipped': 0}
```

### 自验要求1：执行无报错 ✅（见 V2）

### 自验要求2：is_simulated=1 记录评分不再全是技术面 ✅

评级分布（四维综合评分产出，分布在3个档位）：

| 评级 | 条数 |
|---|---|
| 持有观望 | 156 |
| 建议减仓 | 96 |
| 推荐买入 | 72 |
| **合计** | **324** |

评级取值合法性：出现的评级 `['建议减仓', '持有观望', '推荐买入']`，全部 ∈ 中文5档（`强烈推荐买入/推荐买入/持有观望/建议减仓/强烈建议卖出`），无非法取值。

**幂等性**：连续两次执行结果完全一致（均为 324/324/0/0），`(stock_id, rating_date)` 重复组合数 = 0，无重复行。

抽样明细（最新10条）：
| 股票 | 评级日 | rating | ret_1d | correct |
|---|---|---|---|---|
| 600276 恒瑞医药 | 2026-07-20 | 持有观望 | -1.15 | 1 |
| HK3690 美团-W | 2026-07-20 | 推荐买入 | -1.27 | None |
| 300146 汤臣倍健 | 2026-07-20 | 持有观望 | 3.21 | 0 |
| 000333 美的集团 | 2026-07-20 | 推荐买入 | -0.04 | None |
| 002352 顺丰控股 | 2026-07-20 | 持有观望 | -0.32 | 1 |
| 300750 宁德时代 | 2026-07-20 | 推荐买入 | 1.53 | 1 |
| 300124 汇创技术 | 2026-07-20 | 建议减仓 | 3.01 | 0 |
| 688017 绿的谐波 | 2026-07-20 | 建议减仓 | 8.57 | 0 |
| 600519 贵州茅台 | 2026-07-20 | 持有观望 | -1.47 | 1 |
| 688981 中芯国际 | 2026-07-20 | 持有观望 | 11.11 | 0 |

### V3 / 自验要求3：红线守恒 ✅

Grep 核验 `data_collector.py` 三处 `if False` 硬禁用，行号与改动前一致：

| 行号 | 内容 |
|---|---|
| L1645 | `if False and saved_count == 0 and market == 'hk_stock':` |
| L1684 | `if False and saved_count == 0:` |
| L1717 | `if False and saved_count == 0:` |

### V4：评分逻辑不变 ✅

`scoring_engine.py` 的 `def analyze(data: StockData) -> AnalysisResult:` 签名与实现未改动（L1018 不变）。

**未修改的红线文件**（本次仅改动 `backtest_engine.py`）：
- `data_collector.py` ✓
- `scoring_engine.py` ✓
- `data_contract.py` ✓
- `data_adapter.py` ✓
- `config_weights.json` ✓
- `app.py` ✓
- `templates/index.html` ✓
- 未引入新 pip 依赖 ✓

---

## 三、遗留说明

1. 原技术面辅助函数 `_calc_technical_score_from_kline()`、`_score_to_rating()`、`_SIM_RATING_THRESHOLDS` 在本次改动后不再被 `run_historical_simulation` 调用，保留在模块中（自包含、无副作用），未删除以控制改动边界。如需清理可单独提 patch。
2. 模拟回测的评级来自当前最新数据的四维评分，同一股票多个模拟评级日共享同一评级；这是任务书认可的方案。若未来需要"每个历史时点的真实四维评级"，需先落库历史基本面/资金面/消息面快照（M9 范畴）。

---

**自验结论：V1/V2/V3/V4 全部通过，B23 交付完成。**
