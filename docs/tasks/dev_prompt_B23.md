# 开发提示词 B23

**推荐模型：glm5.2（GLM Plan）**
**任务书：docs/tasks/dev_tasks_20260726_B23.md**

---

## 你的任务

将 `run_historical_simulation()`（backtest_engine.py L710）从仅技术面单维度评分改为调用四维综合评分。

## 根因

当前 `run_historical_simulation` 只截取 K 线数据做技术面评分（L711-719 注释明确写"技术面评分"），不反映四维综合评级有效性。

## 修改方案

**文件**：`modules/backtest_engine.py` — `run_historical_simulation()` 函数（L710 起）

**修改思路**：
1. 在每个模拟评级日，除了截取 K 线数据外，调用 `data_adapter.load_stockdata_from_db(stock_id)` 获取完整 StockData（含基本面/资金面/消息面）
2. 用 `scoring_engine.analyze(stock_data)` 替代原有的技术面单维度评分
3. analyze() 返回的 `total_score` 和 `rating` 用于后续的收益率计算和 is_correct 判定

**注意**：
- `load_stockdata_from_db` 获取的是**当前**最新数据，不是历史时间点的数据。这在模拟回测中引入了轻微的前瞻偏差（使用了回测时点之后的基本面数据），但对于评估"评级有效性"是可接受的
- 如果 `load_stockdata_from_db` 返回 None（数据不足），跳过该股票
- 保持幂等性（重复执行不产生重复行）

## 红线

1. data_collector.py / scoring_engine.py / data_contract.py / data_adapter.py 不可修改
2. config_weights.json 不可修改
3. templates/index.html / app.py 不可修改
4. 不引入新 pip 依赖

## 自验要求

1. 执行 `run_historical_simulation()` 确认无报错
2. 检查 backtest_results 表 is_simulated=1 的记录评分不再全是技术面
3. Grep 核验红线守恒

自验报告归档至 `reports/dev_selftest_B23.md`。
