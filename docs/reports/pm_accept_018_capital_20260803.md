# PM 验收报告：DEV-TASKS-20260803-018 资金面数据源修复

> 验收日期：2026-08-03
> 验收人：PM（交付物完整性检查 + 红线核验 + 独立抽查）
> 关联 QA 报告：`reports/qa_accept_018_capital_20260803.md`
> 验收结论：**PASS，PM+QA 双签，报监理关闭**

---

## 一、交付物完整性检查

| 交付物 | 状态 |
|---|---|
| `database/db_manager.py`：建表新增 `ths_net_inflow REAL` + ALTER TABLE 幂等迁移 | ✅ 已交付 |
| `modules/data_collector.py`：`fetch_capital_flow_batch()` 仅写 `ths_net_inflow`，不再写 `main_net_inflow`/`main_net_inflow_pct` | ✅ 已交付 |
| `templates/index.html`：资金面表格新增"同花顺净额（辅）"列 + 口径说明 | ✅ 已交付 |
| `modules/daily_report.py` / `app.py`：注释更新 | ✅ 已交付 |
| 数据清理：同花顺口径错误数据删除 | ✅ 已执行 |

## 二、PM 独立抽查结果（不依赖 QA 结论）

| 抽查项 | 结果 |
|---|---|
| `SELECT COUNT(*) FROM raw_capital_flow WHERE super_large_net IS NULL AND main_net_inflow IS NOT NULL` | **= 0**（脏数据已清零）✅ |
| `PRAGMA table_info(raw_capital_flow)` 含 `ths_net_inflow` 列 | ✅ |

与 QA 报告结论一致（QA 4 项 TC 全 PASS + 5 项红线全 PASS）。

## 三、红线核验（PM 侧）

| 红线项 | 结论 |
|---|---|
| 东财逐只采集主链路（三层降级）未被破坏 | ✅ PASS |
| 评分引擎未引用 `ths_net_inflow`，仍用 `main_net_inflow` | ✅ PASS |
| `fetch_capital_flow(symbol, market)` 签名未加 force_full（011 红线） | ✅ PASS |
| 零代码约束：requirements.txt 仍 9 包 | ✅ PASS |
| `config_weights.json` 未改、无 BOM | ✅ PASS |

## 四、已知事项（不构成 FAIL）

1. 600519 的 `ths_net_inflow` 为 NULL（当天未跑同花顺批量），属正常现象
2. 两数据源并存记录仅 1 条，符合 UPDATE/INSERT 设计逻辑

## 五、最终结论

**PASS — 018 批次验收通过，PM+QA 双签，报监理批准关闭。**
