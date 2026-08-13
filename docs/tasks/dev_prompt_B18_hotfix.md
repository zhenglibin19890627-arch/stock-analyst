# B18-Hotfix 开发提示词

**推荐模型：kimi k3（Kimi Plan）**

---

## 任务概述

执行 B18-Hotfix：评分引擎激进校准 + rating_mapping 阈值下调。

**前置状态**：B18 温和校准已完成（scoring_engine.py 已修改 30 处），但验收未达标。本次在 B18 基础上继续调整。

---

## 修改目标

### 目标 1：rating_mapping 阈值下调（config_weights.json）

```json
"rating_mapping": {
  "strong_buy": 80,
  "buy": 65,
  "hold": 50,
  "reduce": 30,
  "sell": 0
}
```

**变更**：85→80（strong_buy）、70→65（buy），其余不变。

### 目标 2：评分引擎激进校准（scoring_engine.py）

在 B18 温和校准基础上，继续上调各子项基准值：

#### T1 资金面（再上调 +5~8 分）

| 子项 | B18 温和值 | Hotfix 激进值 |
|---|---|---|
| score_main_capital ≥5000万 | 92 | **95** |
| score_main_capital 1000~5000万 | 80 | **85** |
| score_main_capital 0~1000万 | 68 | **75** |
| score_main_capital -1000~0万 | 45 | **50** |
| score_main_capital -5000~-1000万 | 30 | **35** |
| score_main_capital <-5000万 | 15 | **20** |
| score_north_capital 缺失默认 | 55 | **60** |
| score_margin_capital 缺失默认 | 55 | **60** |

#### T2 基本面（再上调 +5~8 分）

| 子项 | B18 温和值 | Hotfix 激进值 |
|---|---|---|
| score_valuation PE≤15 | 92 | **95** |
| score_valuation PE 15~25 | 75 | **80** |
| score_valuation PE 25~40 | 55 | **60** |
| score_profitability ROE≥15% | 92 | **95** |
| score_profitability ROE 10~15% | 70 | **78** |
| score_profitability ROE 5~10% | 50 | **55** |
| score_growth 营收≥20% | 92 | **95** |
| score_growth 营收 10~20% | 70 | **78** |
| score_growth 营收 0~10% | 50 | **55** |
| score_cashflow OCF/净利润≥0.8 | 83 | **88** |
| score_fin_health 资产负债率≤30% | 92 | **95** |

#### T3 技术面（再上调 +3~5 分）

| 子项 | B18 温和值 | Hotfix 激进值 |
|---|---|---|
| score_ma 金叉基准 | 75 | **80** |
| score_ma 均线上加分 | +3 | **+4** |
| score_trend MACD 多头 | 70 | **75** |
| score_obos RSI 健康区 | 82 | **85** |
| score_vol_price ≥2000万 | 78 | **82** |
| score_volatility 40~70% 位置 | 80 | **83** |

#### T4 消息面（维持 B18 值，不再下调）

| 子项 | B18 温和值 | Hotfix 值 |
|---|---|---|
| score_sentiment 映射 | (sentiment+1)*48 | **维持** |
| score_holder 增持 | 82 | **维持** |

---

## 红线清单（不可变更）

1. data_collector.py L1645/L1684/L1717 三处 `if False` 硬禁用不得恢复
2. config_weights.json 写入必须无 BOM
3. 零代码约束：无新 pip 依赖
4. 仅修改 scoring_engine.py 和 config_weights.json，不动其他模块

---

## 交付物

1. `modules/scoring_engine.py` — 激进校准后版本
2. `config_weights.json` — rating_mapping 80/65/50/30
3. `scripts/calibrate_verify.py` — 更新对比数据（校准前 vs 温和 vs 激进）
4. `reports/dev_selftest_B18_hotfix.md` — 自验报告

---

## 验收标准

| 编号 | 标准 | 目标值 |
|---|---|---|
| AC1 | 70+ 股票占比 | ≥ 20% |
| AC2 | 评分区间跨度 | ≥ 40 分 |
| AC3 | 四维均分 | 50~65 |
| AC4 | 评级覆盖档位 | ≥ 3 档 |
| AC5 | 四场景测试 | 4/4 PASS |
| AC6 | 红线核验 | 6/6 PASS |

---

## 执行步骤

1. 读取当前 scoring_engine.py（B18 温和校准后版本）
2. 按上表修改各子项基准值
3. 修改 config_weights.json rating_mapping
4. 运行 `python -m modules.scoring_engine` 四场景测试
5. 运行 `scripts/calibrate_verify.py` 验证分布
6. 执行红线核验（Grep 检查 if False、检查 BOM）
7. 编写自验报告

---

*AI 产品经理签发 | 2026-07-25*
