# QA 验收报告：DEV-TASKS-20260803-018 资金面数据源修复

> 任务编号：QA-TASKS-20260803-018_019AB
> 关联开发任务：DEV-TASKS-20260803-018
> 验收日期：2026-08-03
> 验收人：QA（独立验收）
> 验收方式：数据库查询 + 代码核查 + 浏览器实测

---

## 一、测试用例结果

### TC-018-1 — 表结构与迁移（风险：低）

| 检查项 | 结果 | 证据 |
|---|---|---|
| `PRAGMA table_info(raw_capital_flow)` 含 `ths_net_inflow` 列（REAL） | **PASS** | DB 查询确认列存在，类型为 REAL |
| `db_manager.py` 建表语句含该列 | **PASS** | `db_manager.py` L250 建表语句含 `ths_net_inflow REAL -- 同花顺全资金净流入(万元)，辅助指标（018新增）` |
| ALTER TABLE 兼容迁移存在（幂等） | **PASS** | `db_manager.py` L961 迁移列表含 `('raw_capital_flow', 'ths_net_inflow', 'REAL')`，L963-967 `try-except sqlite3.OperationalError: pass` 保证重复初始化不报错 |

**结论：PASS**

---

### TC-018-2 — 批量预取写入隔离（风险：中）★重点

| 检查项 | 结果 | 证据 |
|---|---|---|
| `fetch_capital_flow_batch()` SQL 不再写 `main_net_inflow`/`main_net_inflow_pct` | **PASS** | `data_collector.py` L1288-1304：仅 `UPDATE ... SET ths_net_inflow=?` 和 `INSERT ... (stock_id, trade_date, ths_net_inflow) VALUES (?,?,?)`，无 `main_net_inflow`/`main_net_inflow_pct` 写入 |
| 同花顺净额仅写入 `ths_net_inflow` 列 | **PASS** | L1284 `ths_net = round(main_net_yuan / 1e4, 2)`，L1290/1300 仅写 `ths_net_inflow` |
| 函数返回值 `source` 标注为辅助指标口径 | **PASS** | L1312 `source='同花顺批量(辅助指标)'`；L1214 空列表返回 `source='同花顺批量(空列表)'` |

**结论：PASS**

---

### TC-018-3 — 数据清理结果（风险：中）★重点

| 检查项 | 结果 | 证据 |
|---|---|---|
| 同花顺口径脏数据已清零 | **PASS** | `SELECT COUNT(*) FROM raw_capital_flow WHERE super_large_net IS NULL AND main_net_inflow IS NOT NULL` = **0** |
| 有 `main_net_inflow` 的记录同时有分单数据 | **PASS** | 共 2024 条 `main_net_inflow` 记录，其中 2024 条含 `super_large_net`，2024 条含 `large_net`（100% 覆盖，东财口径特征） |
| `ths_net_inflow` 与 `main_net_inflow` 可并存于同一行 | **PASS** | 1 条记录同时含两字段（东财主力 + 同花顺辅助并存） |

**补充数据**：`ths_net_inflow` 非空记录共 235 条，覆盖 2026-08-03（22 只）、07-31（21 只）、07-30（20 只）等交易日。

**结论：PASS**

---

### TC-018-4 — 前端展示（风险：低）

| 检查项 | 结果 | 证据 |
|---|---|---|
| 资金面区域新增"同花顺净额（辅）"列 | **PASS** | 浏览器实测：`viewData(18)`（贵州茅台）页面资金面表格表头含 `同花顺净额辅`（`<sup>` 标注） |
| 列标注为辅助指标，不与主力净流入混淆 | **PASS** | `index.html` L2480 表头含 `title="同花顺全部资金净流入（总主动买入-总主动卖出），辅助指标"` + `<sup>辅</sup>` 标注；L2487 补充说明"主力净流入来源：东方财富（超大单+大单）；同花顺净额为辅助指标（全部资金净流入），两者口径不同" |
| 原有资金面列显示正常 | **PASS** | 浏览器实测：日期/主力净流入/主力净流入占比/超大单/大单 列均正常显示（如 2026-08-03: main=-2218.04, super=-18577.15, large=16359.1） |

**结论：PASS**

---

## 二、红线核验

| 红线项 | 核验方法 | 结论 |
|---|---|---|
| `fetch_capital_flow(symbol, market)` 签名未加 force_full | `data_collector.py` L1683 `def fetch_capital_flow(symbol, market):` | **PASS** — 无 force_full 参数（011 红线） |
| 东财逐只采集主链路未被破坏 | `_fetch_capital_flow_em_individual`(L1342) / `_fetch_capital_flow_em`(L1417) / akshare 降级(L1847) 三层链路完好 | **PASS** — 主链路逻辑完好，写入 main_net_inflow + 分单数据 |
| 评分引擎未因 018 改变 | `scoring_engine.py` 无 `ths_net_inflow` 引用，仍使用 `main_net_inflow` | **PASS** |
| 无新增 pip 依赖 | `requirements.txt` 仍为 9 个包 | **PASS** — akshare/Flask/pandas/numpy/python-dateutil/pydantic/requests/openpyxl/pytest |
| `config_weights.json` 未改 | rating_mapping 80/65/50/30 完好，无 BOM | **PASS** — 阈值完好，UTF-8 无 BOM |

---

## 三、已知问题记录

| # | 问题 | QA 备注 |
|---|---|---|
| 1 | 600519 的 `ths_net_inflow` 显示为 `—`（NULL） | 该股票仅有东财数据（2026-08-03 未跑同花顺批量），属正常现象，非缺陷 |
| 2 | `ths_net_inflow` 与 `main_net_inflow` 并存记录仅 1 条 | 因同花顺批量仅写当天（占位行），东财数据已有历史行，两者在同一 trade_date 的同一行才会并存。当前只有当天同时有两条数据源的行才并存，符合 UPDATE/INSERT 设计逻辑 |

---

## 四、最终结论

**全部 PASS，可双签。**

- 4 项测试用例全部 PASS
- 5 项红线核验全部 PASS
- 表结构迁移正确（幂等 ALTER TABLE）
- 同花顺口径脏数据已清零
- 写入隔离正确（仅写 ths_net_inflow，不碰 main_net_inflow）
- 前端展示新增列且标注辅助指标

---

## 五、验收环境

- 测试时间：2026-08-03
- Python：C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe
- 数据库：stock_analyst/stock_analyst.db
- 验证方式：DB 查询（PRAGMA/SELECT COUNT）+ 代码核查（data_collector.py/db_manager.py/scoring_engine.py）+ 浏览器实测（Chrome @ http://127.0.0.1:5000）
