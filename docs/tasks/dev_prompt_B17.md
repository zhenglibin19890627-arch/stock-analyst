# B17 开发提示词（Dev Prompt）

> 本文件供开发窗口（另一个 Quests）直接使用。请严格按任务书 `docs/tasks/dev_tasks_20260725_B17.md` 执行。

## 项目路径

```
C:\Users\zlb19\Desktop\Qoder cn\stock_analyst
```

## 环境约束（必须遵守）

- Python 路径：`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`
- PowerShell 不支持 `&&`，用 `;` 分隔
- 项目路径含空格，PowerShell 中需引号包裹
- 多行 Python 逻辑必须写临时 .py 文件再执行
- 不引入新 pip 依赖
- `data_collector.py` L1645/L1684/L1717 三处 `if False` 绝对不可改
- `config_weights.json` 写入必须用 `json.dump`（无 BOM），禁用 PowerShell Set-Content
- `rating_mapping` 五档阈值（85/70/50/30）本批次不改

---

## T1（P0）JUDGEMENT_MATRIX 判定阈值优化

### 改动位置

`modules/backtest_engine.py` L40-46

### 当前代码

```python
JUDGEMENT_MATRIX = {
    '强烈推荐买入': {'direction': 'up', 'correct_min': 2.0, 'wrong_max': -3.0},
    '推荐买入': {'direction': 'up', 'correct_min': 1.0, 'wrong_max': -2.0},
    '持有观望': {'direction': 'neutral', 'correct_low': -3.0, 'correct_high': 3.0},
    '建议减仓': {'direction': 'down', 'correct_max': -1.0, 'wrong_min': 3.0},
    '强烈建议卖出': {'direction': 'down', 'correct_max': -1.0, 'wrong_min': 3.0},
}
```

### 修改为

```python
JUDGEMENT_MATRIX = {
    '强烈推荐买入': {'direction': 'up', 'correct_min': 1.0, 'wrong_max': -3.0},
    '推荐买入': {'direction': 'up', 'correct_min': 0.5, 'wrong_max': -2.0},
    '持有观望': {'direction': 'neutral', 'correct_low': -2.0, 'correct_high': 2.0},
    '建议减仓': {'direction': 'down', 'correct_max': -0.5, 'wrong_min': 2.0},
    '强烈建议卖出': {'direction': 'down', 'correct_max': -0.5, 'wrong_min': 2.0},
}
```

### 验证

改完后重跑回测验证命中率变化：
```python
# 临时脚本 _verify_b17_t1.py
import requests

r = requests.post('http://127.0.0.1:5000/api/backtest/run', json={'market': 'a_stock'})
data = r.json()
for rs in data.get('rating_stats', []):
    print(f'{rs["rating"]}: 命中率={rs.get("accuracy")}, 样本={rs["total"]}')
```

---

## T2（P1）行业权重模板落地

### 步骤 1：修改 `config_weights.json`

在现有结构中新增 `industry_overrides` 字段（与 `a_stock`/`hk_stock` 同级）：

```json
{
  "_说明": "四维分析引擎权重配置文件 — 修改后无需重启服务，下次分析自动生效",
  "_更新时间": "2026-07-25",
  "a_stock": {
    "weights": { "kline": 0.2632, "fundamental": 0.2105, "capital_flow": 0.3684, "news": 0.1579 }
  },
  "hk_stock": {
    "weights": { "kline": 0.2739, "fundamental": 0.396, "capital_flow": 0.2739, "news": 0.0561 }
  },
  "industry_overrides": {
    "半导体":     { "kline": 0.20, "fundamental": 0.30, "capital_flow": 0.35, "news": 0.15 },
    "计算机设备": { "kline": 0.20, "fundamental": 0.25, "capital_flow": 0.35, "news": 0.20 },
    "通信设备":   { "kline": 0.22, "fundamental": 0.23, "capital_flow": 0.35, "news": 0.20 },
    "酿酒行业":   { "kline": 0.25, "fundamental": 0.30, "capital_flow": 0.30, "news": 0.15 },
    "家电行业":   { "kline": 0.25, "fundamental": 0.30, "capital_flow": 0.30, "news": 0.15 },
    "医药制造":   { "kline": 0.25, "fundamental": 0.28, "capital_flow": 0.30, "news": 0.17 },
    "医疗服务":   { "kline": 0.25, "fundamental": 0.28, "capital_flow": 0.30, "news": 0.17 }
  },
  "rating_mapping": { ... 保持不变 ... }
}
```

**重要**：必须用 Python 脚本写入（确保无 BOM）：
```python
import json

with open('config_weights.json', 'r', encoding='utf-8') as f:
    config = json.load(f)
config['industry_overrides'] = {...}
config['_更新时间'] = '2026-07-25'
with open('config_weights.json', 'w', encoding='utf-8') as f:
    json.dump(config, f, ensure_ascii=False, indent=2)
```

### 步骤 2：修改 `modules/scoring_engine.py`

**`_load_dim_weights` 函数**（L882-894）：

```python
def _load_dim_weights(market: str, industry: str = None) -> dict[str, float]:
    """从 config_weights.json 热加载维度权重，支持行业覆盖，回退到默认值"""
    try:
        with open(_WEIGHTS_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)

        # B17-T2: 行业权重覆盖（仅A股）
        if industry and market == 'A':
            overrides = config.get('industry_overrides', {})
            if industry in overrides:
                return overrides[industry]

        # 原有逻辑：按市场加载默认权重
        market_key = 'a_stock' if market == 'A' else 'hk_stock'
        weights = config.get(market_key, {}).get('weights')
        if weights:
            return weights
    except (FileNotFoundError, json.JSONDecodeError, IOError) as e:
        logger.warning(f'权重配置加载失败: {e}，使用默认值')

    return dict(_DEFAULT_DIM_WEIGHTS.get(market, _DEFAULT_DIM_WEIGHTS['A']))
```

**`analyze()` 函数调用处**（L1043）：

```python
# 原：raw_weights = _load_dim_weights(data.market)
# 改为：
raw_weights = _load_dim_weights(data.market, getattr(data, 'industry', None))
```

### 步骤 3：修改 `modules/data_adapter.py`

在 `load_stockdata_from_db()` 函数中，确保从 stocks 表读取 `industry` 字段并赋值到 StockData：

```python
# 在构建 StockData 对象后（或查询 stocks 表时），添加：
stock_data.industry = row['industry']  # StockData extra="allow" 支持动态属性
```

查找该函数中 SELECT stocks 表的 SQL 语句，确保包含 `industry` 列。如果已有 SELECT *，则只需在构建 StockData 后赋值即可。

### 验证

```python
# 临时脚本 _verify_b17_t2.py
import sys

sys.path.insert(0, r'c:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
from modules.scoring_engine import _load_dim_weights

# 半导体行业应返回行业权重
w1 = _load_dim_weights('A', '半导体')
assert w1['kline'] == 0.20, f'Expected 0.20, got {w1["kline"]}'
assert w1['fundamental'] == 0.30

# 无行业应 fallback 到默认
w2 = _load_dim_weights('A', None)
assert abs(w2['kline'] - 0.2632) < 0.001

# 未覆盖行业应 fallback
w3 = _load_dim_weights('A', '航空航天')
assert abs(w3['kline'] - 0.2632) < 0.001

# 港股不受行业覆盖影响
w4 = _load_dim_weights('H', '半导体')
assert abs(w4['kline'] - 0.2739) < 0.001

print('T2 全部验证通过!')
```

---

## T3（P2）回测页面增加 T+1 周指标展示

### 改动位置

`templates/index.html` — 回测页面渲染函数（搜索 `backtest` 或 `回测` 相关渲染代码）

### 要求

1. 回测评级汇总表新增一列 **"周收益"**（`return_1w` 平均值）
   - 正数红色 `#e74c3c`，负数绿色 `#27ae60`
   - 格式：`+2.02%` 或 `-0.58%`

2. 回测页面顶部增加提示文字：
```html
<div style="background:#e8f4fd;border:1px solid #b3d9f2;border-radius:6px;padding:8px 12px;margin-bottom:12px;font-size:13px;color:#1a5276;">
  💡 提示：T+1日受短期波动影响较大，建议结合周收益综合判断评级有效性
</div>
```

3. 确认回测 API（`/api/backtest/run`）返回的 `rating_stats` 中是否已包含周收益数据：
   - 搜索 `app.py` 中 backtest 相关路由
   - 如果 `rating_stats` 未包含 `avg_return_1w`，需在 `backtest_engine.py` 的统计逻辑中补充

### 验证

- 启动服务，进入回测页面，点击"运行回测"
- 确认表格有"周收益"列且颜色正确
- 确认顶部有提示文字

---

## 执行顺序

```
T1（0.5h，纯常量）→ T2（2-3h，核心功能）→ T3（1h，前端）
```

---

## 自验清单

完成后请逐项验证并写入 `reports/dev_selftest_B17.md`：

| # | 验证项 | 方法 |
|---|---|---|
| 1 | JUDGEMENT_MATRIX 推荐买入 correct_min=0.5 | Grep |
| 2 | 买入/减仓判定对称（均为 0.5%） | Read |
| 3 | config_weights.json 含 industry_overrides（7 个行业） | Read |
| 4 | config_weights.json 无 BOM | Python `open('rb')` 检查前 3 字节 |
| 5 | 半导体股票分析使用行业权重 | 调用 _load_dim_weights('A','半导体') |
| 6 | 无行业/未覆盖行业 fallback 到默认 | 调用 _load_dim_weights('A', None) |
| 7 | 港股不受 industry_overrides 影响 | 调用 _load_dim_weights('H','半导体') |
| 8 | 回测页面显示"周收益"列 | 启动服务查看 |
| 9 | 回测页面有提示文字 | 同上 |
| 10 | requirements.txt 无变化 | 文件时间戳 |
| 11 | data_collector.py 三处 if False 不变 | Grep |
| 12 | rating_mapping 五档阈值不变 | Read config_weights.json |

---

## 红线提醒

1. ❌ 不引入新 pip 依赖
2. ❌ 不改 `data_collector.py` L1645/L1684/L1717
3. ❌ 不改 `rating_mapping` 五档阈值（85/70/50/30）
4. ❌ 不破坏 `data_contract.py` Pydantic 模型核心字段
5. ❌ 不超出 T1/T2/T3 三个任务范围
6. ✅ config_weights.json 必须用 json.dump 写入（无 BOM）
7. ✅ 保持 `python app.py` 一键启动不变
