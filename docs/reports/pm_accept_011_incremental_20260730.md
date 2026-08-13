# PM 验收报告：011 数据采集全链路增量优化

> **验收人**：PM  | **验收日期**：2026-07-30 | **任务编号**：DEV-TASKS-20260730-011-DEV

---

## 一、验收结论

| 项 | 结果 |
|---|---|
| **PM验收** | ✅ **通过**（交付物完整 + 红线全绿 + 零代码合规 + 无任务蔓延） |
| **QA验收** | ⏳ 待监理指派 QA 独立窗口执行 |
| **状态** | PM 验收通过，待 QA 功能验收后双签关闭 |

---

## 二、交付物完整性检查

| 交付物 | 路径 | 状态 |
|---|---|---|
| 开发自验报告 | `reports/dev_selftest_011_incremental_20260730.md`（154行） | ✅ 完整（V1-V10 + R1-R4 全PASS） |
| config.py 修改 | 新增3个配置项（L26/28/30） | ✅ |
| data_collector.py 修改 | 8个函数签名变更 + save_data_status 去重 | ✅ |
| app.py 修改 | 新增 /refresh-full 路由（L773-807） | ✅ |

### 函数签名变更清单（与任务书对照）

| 函数 | 任务书规格 | 实际实现 | 结果 |
|---|---|---|---|
| `fetch_kline` | `(symbol, market, force_full=False)` | `(symbol, market, force_full=False)` L412 | ✅ |
| `fetch_a_fundamental` | `(symbol, force_full=False)` | `(symbol, force_full=False)` L502 | ✅ |
| `fetch_hk_fundamental` | `(symbol, force_full=False)` | `(symbol, force_full=False)` L853 | ✅ |
| `fetch_north_capital` | `(symbol, market, force_full=False)` | `(symbol, market, force_full=False)` L1918 | ✅ |
| `fetch_margin_balance` | `(symbol, market, force_full=False)` | `(symbol, market, force_full=False)` L2078 | ✅ |
| `fetch_sentiment` | `(symbol, market, force_full=False)` | `(symbol, market, force_full=False)` L2224 | ✅ |
| `collect_stock_data` | `(symbol, market, force_full=False)` | `(symbol, market, force_full=False)` L2332 | ✅ |
| `save_data_status` | 先删后插去重 | DELETE+INSERT L272-281 | ✅ |
| `fetch_capital_flow` | **不改**（红线） | `(symbol, market)` L1561 不变 | ✅ |

---

## 三、红线核验（9条全绿）

| # | 红线项 | 核验方法 | 结果 |
|---|---|---|---|
| R1 | `data_collector.py` L1760 `if False` 腾讯估算 | Grep `if False and` → 3处均在 | ✅ |
| R2 | `data_collector.py` L1799 `if False` 新浪估算 | 同上 | ✅ |
| R3 | `data_collector.py` L1832 `if False` 网易估算 | 同上 | ✅ |
| R4 | `advisor.py` `generate_advice` (L869) | 签名 `(stock_id, report_date=None)` 不变 | ✅ |
| R5 | `advisor.py` `_build_capital_factors` (L785) | 签名 `(factors, stock_data, stock_id)` 不变 | ✅ |
| R6 | `config_weights.json` rating_mapping | 80/65/50/30 不变 | ✅ |
| R7 | 零代码约束 | requirements.txt 8包不变 | ✅ |
| R8 | `scoring_engine.py` | 函数列表不变，未在修改文件清单中 | ✅ |
| R9 | `fetch_capital_flow` 不回写 | 签名+内部逻辑不变（L1561起） | ✅ |

---

## 四、零代码约束核验

| 项 | 结果 |
|---|---|
| requirements.txt 行数 | 8行（未增加） |
| 包列表 | akshare, Flask, pandas, numpy, python-dateutil, pydantic, requests, openpyxl |
| 新增依赖 | 无 |
| 数据库迁移 | 无（save_data_status 用先删后插，未 ALTER TABLE） |

**结论**：✅ 零代码用户可独立运行

---

## 五、任务蔓延评估

| 检查项 | 结果 |
|---|---|
| 修改文件范围 | 仅 config.py + data_collector.py + app.py = 与任务书一致 |
| 新增文件 | 仅自验报告 = 与任务书一致 |
| 未授权文件修改 | 无 |
| 架构师决策点实现 | 5个决策点全部按裁定实现 |

### 开发自主决策项（合理偏离）

| 项 | 说明 | PM 评估 |
|---|---|---|
| news_sentiment 列名修正 | 任务书写 `analysis_date`，实际表为 `news_date`，开发已修正 | ✅ 合理（表结构适配） |
| north_capital 补充 save_data_status | 原代码缺失导致缓存检查无法读取上次采集时间，开发补充 | ✅ 合理（DP-4 实现必要条件） |

---

## 六、关键实现抽查

### 6.1 K线同日跳过（DP-1）

```python
# L422-442：逻辑正确
if not force_full:
    → 查 raw_kline MAX(trade_date)
    → last_date >= today_str → 跳过
    → 异常时降级为全量（安全兜底）
```
✅ 符合架构师裁定（同日跳过+全量覆盖，不用from增量）

### 6.2 save_data_status 去重（011-F）

```python
# L272-281：先删后插
DELETE FROM data_status WHERE stock_id=? AND dimension=? AND fetched_at LIKE today%
INSERT INTO data_status (...)
```
✅ 无需 ALTER TABLE，零代码用户友好

### 6.3 /refresh-full API（DP-5）

```python
# L773-807：路由正确
POST /api/stocks/<id>/refresh-full
→ collect_stock_data(symbol, market, force_full=True)
→ generate_advice(stock_id)
→ generate_price_advice
```
✅ 符合任务书规格

---

## 七、待 QA 功能验收项

PM 仅做交付物完整性+红线核验，以下需 QA 独立功能验收：

| # | 验收项 | 验收方法 |
|---|---|---|
| Q1 | K线同日跳过实际效果 | 启动 Flask → 分析一只股票 → 立即再次分析 → 检查第二次是否跳过 |
| Q2 | 基本面80天门控实际效果 | 分析一只 report_date < 80天的股票 → 检查财报是否被跳过 |
| Q3 | PE/PB 24h门控 | 分析一只当天已分析的股票 → 检查PE/PB是否跳过 |
| Q4 | 消息面当日跳过 | 同一只股票当日二次分析 → 检查消息面是否跳过 |
| Q5 | force_full 全量刷新 | 调用 /refresh-full API → 检查全部维度重新采集 |
| Q6 | 首次分析兜底 | 新增股票首次分析 → 确认全量采集不跳过 |
| Q7 | data_status 去重 | 同维度同日多次采集 → 确认仅保留1条 |

---

## 八、验收签字

| 角色 | 状态 |
|---|---|
| PM 验收 | ✅ 通过（2026-07-30） |
| QA 验收 | ⏳ 待执行 |
| 监理批准 | ⏳ 待批准 |

---

> **PM 备注**：PM 验收通过红线核验+交付物完整性+零代码合规+任务蔓延评估。功能验收需 QA 在独立 Quests 窗口执行（Q1-Q7），通过后双签关闭 011。
