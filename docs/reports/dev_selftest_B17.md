# B17 开发自验报告

| 项 | 内容 |
|---|---|
| 批次 | B17 |
| 日期 | 2026-07-25 |
| 任务书 | `docs/tasks/dev_tasks_20260725_B17.md` |
| 状态 | ✅ T1/T2/T3 全部完成并通过自验 |

---

## 一、改动清单

| 任务 | 文件 | 改动 |
|---|---|---|
| T1 | `modules/backtest_engine.py` L40-46 | JUDGEMENT_MATRIX 判定阈值优化（降低买入/减仓门槛，收窄中性区） |
| T2 | `modules/scoring_engine.py` L882 | `_load_dim_weights` 新增 `industry` 参数，支持行业权重覆盖（仅A股） |
| T2 | `modules/scoring_engine.py` L1055 | `analyze()` 调用传入 `getattr(data, 'industry', None)` |
| T2 | `modules/data_adapter.py` L247 | `_read_stock_info` SELECT 增加 `industry` 列 |
| T2 | `modules/data_adapter.py` L394 | 构建 StockData 后赋值 `data.industry`（StockData extra="allow"） |
| T2 | `config_weights.json` | 新增 `industry_overrides`（7个行业）+ `_更新时间` 更新（json.dump 写入，无 BOM） |
| T3 | `templates/index.html` L956-959 | 回测页顶部新增提示文字横幅 |
| T3 | `templates/index.html` L4895-4898 | 分级准确率表 T+1日/周收益列增加红涨绿跌颜色（正数红#e74c3c，负数绿#27ae60） |

> T3 说明：分级准确率表的"T+1周均收益"列在 B16 前已存在（数据层 `rating_stats.avg_return_1w` 由 `backtest_engine.compute_market_report` 计算），本批次补足其红涨绿跌颜色样式。

---

## 二、自验清单（对照任务书 12 项）

| # | 验证项 | 方法 | 结果 |
|---|---|---|---|
| 1 | JUDGEMENT_MATRIX 推荐买入 correct_min=0.5 | Grep | ✅ 0.5 |
| 2 | 买入/减仓判定对称（均为 0.5%） | Read | ✅ 买入+0.5 / 减仓-0.5 |
| 3 | config_weights.json 含 industry_overrides（7 个行业） | Read | ✅ 7个：半导体/计算机设备/通信设备/酿酒行业/家电行业/医药制造/医疗服务 |
| 4 | config_weights.json 无 BOM | Python open(rb) | ✅ 前3字节 `b'{\r\n'`，无 `\xef\xbb\xbf` |
| 5 | 半导体股票分析使用行业权重 | `_load_dim_weights('A','半导体')` | ✅ kline=0.20, fundamental=0.30 |
| 6 | 无行业/未覆盖行业 fallback 到默认 | `_load_dim_weights('A',None)` / `('A','航空航天')` | ✅ kline≈0.2632 |
| 7 | 港股不受 industry_overrides 影响 | `_load_dim_weights('H','半导体')` | ✅ kline≈0.2739 |
| 8 | 回测页面显示"周收益"列 | 启动服务 + DOM 检查 | ✅ "T+1周均收益"列已渲染 |
| 9 | 回测页面有提示文字 | 启动服务 + DOM 检查 | ✅ "💡 提示：T+1日受短期波动影响较大..." |
| 10 | requirements.txt 无变化 | 文件读取 | ✅ 8 项依赖，未增减 |
| 11 | data_collector.py 三处 if False 不变 | Grep | ✅ L1645/L1684/L1717 完好 |
| 12 | rating_mapping 五档阈值不变 | Read config_weights.json | ✅ 85/70/50/30 |

---

## 三、T1 验证详情（JUDGEMENT_MATRIX）

调整前后对照：

| 评级 | 字段 | 调整前 | 调整后 |
|---|---|---|---|
| 强烈推荐买入 | correct_min | 2.0 | **1.0** |
| 推荐买入 | correct_min | 1.0 | **0.5** |
| 持有观望 | correct_low/high | ±3.0 | **±2.0** |
| 建议减仓 | correct_max | -1.0 | **-0.5** |
| 强烈建议卖出 | correct_max | -1.0 | **-0.5** |

判定对称性：推荐买入门槛 +0.5% 与建议减仓门槛 -0.5% 完全对称。

---

## 四、T2 验证详情（行业权重）

`_load_dim_weights(market, industry)` 加载优先级：
1. A股 + 提供行业名 → 查 `industry_overrides[industry]`，命中返回行业权重
2. 未命中 / 港股 / 无行业 → 按市场加载默认权重（a_stock / hk_stock）
3. 配置异常 → 内存默认值

实测：
```
_load_dim_weights('A','半导体')   → kline=0.20, fundamental=0.30, capital_flow=0.35, news=0.15 (合计1.0)
_load_dim_weights('A', None)       → kline=0.2632 (A股默认)
_load_dim_weights('A','航空航天')  → kline=0.2632 (未覆盖，fallback)
_load_dim_weights('H','半导体')    → kline=0.2739 (港股不受影响)
```

数据通路验证：`data_adapter._read_stock_info` 已 SELECT industry 列，构建 StockData 后赋值 `data.industry`；`scoring_engine.analyze()` 通过 `getattr(data,'industry',None)` 取值并传入权重加载函数。

---

## 五、T3 验证详情（回测页周收益展示）

通过 `http://127.0.0.1:5000/#backtest` 实测（A股，147 条样本）：

1. **顶部提示横幅**：DOM 文本节点确认存在
   `💡 提示：T+1日受短期波动影响较大，建议结合周收益综合判断评级有效性`

2. **分级准确率表"周收益"列颜色**（evaluate_script 取计算样式）：

| 评级 | 周收益 | 颜色 |
|---|---|---|
| 持有观望 | +0.82% | rgb(231,76,60) = #e74c3c 红 ✅ |
| 建议减仓 | -0.49% | rgb(39,174,96) = #27ae60 绿 ✅ |
| 强烈建议卖出 | +1.64% | rgb(231,76,60) = #e74c3c 红 ✅ |
| 推荐买入 | — | rgb(153,153,153) = #999 灰（无周数据） ✅ |

数据源：`/api/backtest/market-report` 返回的 `rating_stats[*].avg_return_1w` 已由 `backtest_engine` 计算就绪，前端无需后端改动。

---

## 六、红线核验

| # | 红线 | 结果 |
|---|---|---|
| 1 | 不引入新 pip 依赖 | ✅ requirements.txt 未变（8 项） |
| 2 | data_collector.py L1645/L1684/L1717 三处 if False 不变 | ✅ Grep 确认完好 |
| 3 | config_weights.json 写入无 BOM（json.dump） | ✅ 前3字节 `b'{\r\n'` |
| 4 | data_contract.py 核心字段不变 | ✅ 未改动 |
| 5 | 零代码启动流程不变（python app.py） | ✅ 服务正常启动 |
| 6 | rating_mapping 五档阈值不变（85/70/50/30） | ✅ |

---

## 七、结论

B17 三项任务（T1 判定阈值优化 / T2 行业权重模板 / T3 回测页周收益展示）均已实现并通过全部 12 项自验 + 6 项红线核验。回测页可正常访问，行业权重热加载机制生效，前端颜色与提示符合需求。等待 PM/QA 验收。
