# 开发提示词 B20

**推荐模型：glm5.2（GLM Plan）**
**任务书：docs/tasks/dev_tasks_20260726_B20.md**

---

## 你的任务

修复 v5 引擎四维因子明细不输出的问题，使分析报告页四维详情卡片正常显示具体因子（均线趋势/RSI状态/PE/ROE等），而不是"暂无关键因子"。

## 项目环境

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（路径含空格）
- 技术栈：Python + Flask + SQLite + akshare
- PowerShell 不支持 `&&`，用 `;` 分隔命令
- Python 多行逻辑必须写临时 .py 文件执行

## 根因

`modules/advisor.py` L604 的 `_build_v5_factors(v5_result, dim_key)` 函数只从 v5_result 中提取了 `dimension_score` 和 `data_completeness`，**没有**输出前端期望的具体因子字段（ma_trend/rsi_status/pe_ratio/roe 等）。

v5 引擎的子评分函数（scoring_engine.py 的 score_ma/score_valuation 等）内部有计算因子明细，但这些 detail 没有传递到 AnalysisResult 对象，`_build_v5_factors` 无法获取。

## 修复方案（推荐方案B：改写 _build_v5_factors）

### 修改文件：modules/advisor.py

**1. 改写 `_build_v5_factors` 函数（L604-651）**

增加 `stock_id` 参数，从 DB 读取原始数据构建因子明细：

```python
def _build_v5_factors(stock_id, v5_result, dim_key):
```

按维度从 DB 读取数据构建因子，必须输出前端期望的 key：

| 维度 | 必须输出的 key（至少2个） | DB 数据源 |
|---|---|---|
| kline | ma_trend, rsi_status | raw_kline 表（close_5ma/close_20ma/rsi等字段） |
| fundamental | pe_ratio, roe | raw_fundamental 表（pe_ratio/roe等字段） |
| capital_flow | main_trend, main_pct | raw_capital_flow 表（main_net_inflow等字段） |
| news | avg_sentiment, positive_ratio | news_sentiment 表（avg_sentiment等字段） |

**前端期望的完整字段清单**（参考 index.html L4626-4630）：
```javascript
kline: ['ma_trend', 'rsi_status', 'recent_trend', 'volume', 'boll_position']
fundamental: ['pe_ratio', 'roe', 'revenue_growth', 'pb_ratio', 'net_margin', 'debt_ratio']
capital_flow: ['main_trend', 'consecutive', 'main_pct', 'super_large', 'main_avg_5d']
news: ['avg_sentiment', 'positive_ratio', 'top_news', 'news_activity', 'extreme_warning']
```

**2. 更新调用处**（L573）

```python
# 原：factors = _build_v5_factors(v5_result, dim_key)
# 改：factors = _build_v5_factors(stock_id, v5_result, dim_key)
```

### DB 表结构参考

**raw_kline 表**字段含：trade_date, open, high, low, close, volume, close_5ma, close_10ma, close_20ma, close_60ma, rsi_14, macd_dif, macd_dea, kdj_k, kdj_d, kdj_j, boll_upper, boll_mid, boll_lower, volume_ratio 等

**raw_fundamental 表**字段含：report_date, roe, pe_ratio, pb_ratio, gross_margin, net_margin, revenue_yoy, debt_ratio, current_ratio, ocf_to_profit 等

**raw_capital_flow 表**字段含：trade_date, main_net_inflow, super_large_net_inflow, large_net_inflow, medium_net_inflow, small_net_inflow 等

**news_sentiment 表**字段含：fetched_at, avg_sentiment, positive_count, negative_count, neutral_count 等

## 因子值格式参考（与 legacy 引擎输出对齐）

```python
# kline
factors['ma_trend'] = f'多头排列(MA5={ma5:.2f} > MA20={ma20:.2f})'  # 或空头排列
factors['rsi_status'] = f'超买({rsi:.1f})'  # >70超买 / <30超卖 / 否则正常
factors['recent_trend'] = f'近5日{"上涨" if pct > 0 else "下跌"}{abs(pct):.1f}%'

# fundamental
factors['pe_ratio'] = f'{pe:.2f}'
factors['roe'] = f'{roe:.2f}%'
factors['pb_ratio'] = f'{pb:.2f}'

# capital_flow
factors['main_trend'] = f'主力{"净流入" if inflow > 0 else "净流出"}{abs(inflow):.0f}万元'
factors['main_pct'] = f'{pct:.2f}%'

# news
factors['avg_sentiment'] = f'+{sent:.2f}(正面)'  # 或负值(负面)
factors['positive_ratio'] = f'正面{pos}/负面{neg}/中性{neu}'
```

## 红线（绝对禁止）

1. **data_collector.py** L1645/L1684/L1717 三处 `if False` 不可修改
2. **config_weights.json** 不可修改
3. **templates/index.html** 不可修改（后端适配前端，不反向）
4. **daily_report.py** 的 `_pick_top_factors` 优先级列表不动
5. **scoring_engine.py** 评分逻辑不动（不改变分数计算）
6. **不引入**新 pip 依赖

## 自验要求

1. 启动 Flask：`python app.py`
2. 调用 API 核验因子输出：
   - `GET http://127.0.0.1:5000/api/stocks/27/advise`（POST方法）
   - `GET http://127.0.0.1:5000/api/stocks/27/report-latest`
   - 检查 dimensions.kline.factors 含 ma_trend 或 rsi_status
   - 检查 dimensions.fundamental.factors 含 pe_ratio 或 roe
3. 浏览器访问 `http://127.0.0.1:5000`，选海康威视查看报告，确认四维详情有内容
4. Grep 核验红线守恒

自验报告归档至 `reports/dev_selftest_B20.md`。
