# 013 盘中快报模式 — PM+QA 双签验收报告

| 项 | 内容 |
|---|---|
| 编号 | DEV-TASKS-20260731-013 |
| 任务 | 盘中快报模式 |
| 日期 | 2026-07-31 |
| 结果 | **✅ 验收通过，批次关闭** |

---

## 一、批次总览

| 阶段 | 文档 | 结果 |
|---|---|---|
| 开发 | `docs/tasks/dev_tasks_20260731_013_dev.md` | V1~V8 全 PASS |
| QA 验收 | `docs/tasks/qa_tasks_20260731_013.md` → `reports/qa_accept_013_intraday_20260731.md` | Q1~Q19 全 PASS（Q20 FAIL → Hotfix） |
| Hotfix | `docs/tasks/dev_tasks_20260731_013_hotfix.md` → `reports/dev_selftest_013_hotfix_20260731.md` | H1~H6 全 PASS |
| PM 终验 | 本报告 | 通过 |

---

## 二、功能交付确认

| 功能点 | 状态 |
|---|---|
| 盘中快报 API（`POST /api/daily-report/generate-intraday`） | ✅ |
| report_type 列 + 三列唯一约束迁移 | ✅ |
| 快报不覆盖 daily（并存） | ✅ |
| 快报覆盖快报（仅保留最新） | ✅ |
| 盘后 daily 删除 intraday（最终版） | ✅ |
| 18:00 定时调度不受影响 | ✅ |
| 前端"📊 盘中快报"按钮（含列表视图） | ✅ |
| 报告类型徽标（盘中快报/收盘报告） | ✅ |
| latest/by_date 查询去重（优先 daily） | ✅ |

---

## 三、红线核验

| 红线 | 状态 |
|---|---|
| advisor.py generate_advice / _build_capital_factors | ✅ 未触碰 |
| data_collector.py 三处 if False | ✅ 未触碰 |
| config_weights.json rating_mapping | ✅ 未触碰 |
| scoring_engine.py v5 | ✅ 未触碰 |
| 011 增量逻辑 | ✅ 未触碰 |
| 012 日志/超时配置 | ✅ 未触碰 |
| 零代码约束（8包） | ✅ 未触碰 |

---

## 四、代码变更文件清单

| 文件 | 改动 |
|---|---|
| `database/db_manager.py` | +迁移函数 `_migrate_daily_reports_type` |
| `modules/daily_report.py` | _save_report/_process_single_stock/generate_daily_report 增加 report_type；get_latest_reports/get_reports_by_date 去重 |
| `app.py` | +API `/api/daily-report/generate-intraday` |
| `templates/index.html` | +盘中快报按钮 + JS + 徽标 + 列表视图按钮 |

---

## 五、数据库变更

| 变更 | 说明 |
|---|---|
| `daily_reports` 新增列 | `report_type TEXT DEFAULT 'daily'` |
| 唯一约束变更 | `(report_date, stock_id)` → `(report_date, stock_id, report_type)` |
| 历史数据 | 全部标记为 `report_type='daily'`（兼容） |
| 总表数 | 仍为 30 张（无新表） |

---

## 六、双签

| 角色 | 判定 | 说明 |
|---|---|---|
| **QA** | ✅ 通过 | Q1~Q19 全 PASS；Q20 问题已 Hotfix 修复（H1 PASS）；监理批准免复验 |
| **PM** | ✅ 通过 | 交付物完整、红线未触碰、零代码约束满足、无任务蔓延 |

---

## 七、遗留事项

无。QA 附注的 latest 混合问题已在 Hotfix 中修复。

---

**013 批次正式关闭。**
