# 紧急修复任务书：RSI计算算法修正（SMA→Wilder）

**编号**：DEV-TASKS-20260727-004
**类型**：Bug修复（数据准确性）
**优先级**：**高**（影响超买超卖判断+技术面评分+回测准确性）
**签发日期**：2026-07-27
**签发人**：AI 产品经理
**监理批准**：待批准

---

## 一、任务卡

| 项 | 内容 |
|---|---|
| **任务名** | RSI计算算法修正：SMA → Wilder平滑 |
| **Bug描述** | 系统RSI使用SMA算法，与同花顺等主流软件（Wilder算法）严重偏差 |
| **推荐开发模型** | glm5.2（单函数修复） |
| **窗口类型** | Quests（独立窗口） |
| **执行模式** | 单代理 |
| **项目路径** | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| **Python** | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |

---

## 二、Bug详情

### 2.1 实证数据

| 股票 | 系统RSI(14) | 同花顺RSI(12) | Wilder手算(14) | SMA手算(14) |
|---|---|---|---|---|
| 贵州茅台 | 71.54（超买） | 60.37（正常） | 57.84 | 71.54 |
| 绿的谐波 | 23.24（超卖） | 36.94（正常） | — | — |

**结论**：系统RSI=71.54与SMA手算完全一致，证明系统用的是SMA算法。Wilder算法结果57.84与同花顺~60一致。

### 2.2 根因代码

文件：`modules/data_adapter.py`，函数 `_calc_rsi`（L123-142）

**当前错误代码（SMA）**：
```python
def _calc_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    # 错误：只取最近period天
    for i in range(len(closes) - period, len(closes)):
        diff = closes[i] - closes[i - 1]
        ...
    # 错误：简单移动平均（无历史记忆）
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    ...
```

**问题**：
1. L129：`range(len(closes) - period, len(closes))` 只取最近14天，丢弃历史
2. L137-138：`sum/period` 是简单平均，没有Wilder指数平滑递推

### 2.3 影响范围

| 影响项 | 说明 |
|---|---|
| 超买超卖徽标 | RSI误报超买/超卖，徽标错误触发 |
| 技术面评分 | score_obos 用RSI评分，RSI>70给20分(严重超买) vs 实际~58给87分(健康)，技术面分数失真 |
| 回测准确性 | 历史评级的RSI数据全部基于错误算法 |

---

## 三、修复方案

### 3.1 正确算法（Wilder平滑）

```python
def _calc_rsi(closes: list[float], period: int = 14) -> Optional[float]:
    """计算 RSI（Wilder平滑算法，与同花顺/通达信一致）"""
    if len(closes) < period + 1:
        return None
    gains = []
    losses = []
    for i in range(1, len(closes)):
        diff = closes[i] - closes[i - 1]
        gains.append(max(diff, 0))
        losses.append(max(-diff, 0))

    # 第一个SMA初始化
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period

    # Wilder指数平滑递推（关键区别）
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return round(100.0 - (100.0 / (1.0 + rs)), 2)
```

### 3.2 改动范围

| 文件 | 改动 | 说明 |
|---|---|---|
| `modules/data_adapter.py` | **改** `_calc_rsi` 函数（L123-142） | SMA→Wilder，约20行 |
| `modules/scoring_engine.py` | **不改** | score_obos 逻辑不变，输入值变正确 |
| `modules/advisor.py` | **不改** | 🛑 B24红线 |
| `config_weights.json` | **不改** | |
| `modules/data_collector.py` | **不改** | 🛑 红线 |
| `requirements.txt` | **不改** | 无新依赖 |
| `templates/index.html` | **不改** | 徽标逻辑不变 |

---

## 四、红线清单

| 红线 | 说明 |
|---|---|
| scoring_engine.py | 不改（RSI值传入正确后评分自动正确） |
| advisor.py | 不改（B24红线） |
| data_collector.py L1645/L1684/L1717 | 不改 |
| config_weights.json | 不改 |
| 无新依赖 | 不引入talib等计算库 |

---

## 五、自验要求

| # | 验证项 | 方法 | 通过标准 |
|---|---|---|---|
| V1 | 茅台RSI验证 | 修复后计算茅台RSI(14) | 结果应在55~62区间（接近同花顺60），不再是71.54 |
| V2 | 绿的谐波RSI验证 | 修复后计算绿的谐波RSI(14) | 结果应在33~40区间（接近同花顺37），不再是23.24 |
| V3 | 全量RSI重算 | 27只股票RSI全部重算 | 无超买/超卖误报（除非真有RSI>70或<30的） |
| V4 | 红线核验 | scoring_engine/advisor/data_collector未改 | 零修改 |
| V5 | 零代码运行 | python app.py启动正常 | 页面评分展示正常 |

---

## 六、流程

本任务为**单函数Bug修复**，不涉及架构变更，**无需架构师评审**。

```
PM签发(本任务书) → 监理批准 → 开发编码+自验(glm5.2)
  → QA验收 → PM+QA双签 → 监理关闭
```

修复完成后，003超买超卖徽标的QA验收可一并完成（RSI正确后徽标才准）。

---

*当前状态：任务书已签发，待监理批准后执行。*
