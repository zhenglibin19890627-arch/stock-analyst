# QA验收报告：012 日志系统+日报诊断+采集健壮性

> 验收人：QA | 日期：2026-07-30 | 状态：✅通过

---

## 测试结果

| 项 | 结论 | 证据摘要 |
|---|---|---|
| Q1 文件日志生成 | **PASS** | `logs/app.log` 存在(126行)；含启动banner `===== Stock Analyst 启动 PID=37448 =====`；含werkzeug请求日志 `GET /api/health 200`；格式 `时间 [模块名] 级别 消息` 正确 |
| Q2 日志轮转配置 | **PASS** | `app.py` L3543-3556: `TimedRotatingFileHandler(when='midnight', backupCount=7, encoding='utf-8')`；使用 `root.addHandler(file_handler)` 而非 basicConfig |
| Q3 日报进度日志 | **PASS** | 55条 `[日报进度]` 日志；格式 `X/Y 开始 symbol name`（如 `1/27 开始 600276 恒瑞医药`）；批次汇总 `===== 批次完成 成功27/失败0 耗时0s =====` |
| Q4 进度文件 | **PASS** | `logs/report_progress.json` 存在；JSON有效含 date/total/current/status/started_at/finished_at；status="done" |
| Q5 单只超时机制 | **PASS** | `daily_report.py` L569-588: `ThreadPoolExecutor(max_workers=1)` + `future.result(timeout=STOCK_TIMEOUT_SECONDS)`；`config.py` L118: `STOCK_TIMEOUT_SECONDS=90`；超时后 logger.error + _save_report(status='failed') + continue |
| Q6 批次整体超时 | **PASS** | `config.py` L120: `BATCH_TIMEOUT_SECONDS=1800`；`daily_report.py` L548: `if time.time()-batch_start > BATCH_TIMEOUT_SECONDS: break`；有 warning 日志 |
| Q7 error_logs表增强 | **PASS** | PRAGMA确认含 `dimension TEXT` + `traceback TEXT`；`db_manager.py` L714-722 migration 用 try/SELECT except/ALTER 做列存在性检查 |
| Q8 _log_error_to_db | **PASS** | `data_collector.py` L287-299: 函数存在；外层 try/except pass 保护；traceback 截断 `[:2000]`；collect_stock_data 中 7 处调用覆盖 kline/fundamental(A+HK)/capital/north/margin/sentiment 共6维度 |
| Q9 failure_summary | **PASS** | `daily_report.py` L669-676: fail_count>0 时聚合 by_reason；L689 写入 summary dict；全成功时 failure_summary=None（本次27/0验证） |
| Q10 011增量回归 | **PASS** | `fetch_kline('600276','a_stock')` → "同日跳过(K线已有2026-07-30数据)"；`fetch_a_fundamental('000333')` → "同日跳过(财报80天TTL内+PE/PB 24h内)"；`force_full=True` → "获取251条K线数据" 正常绕过 |

---

## 红线核验

| 红线 | 结论 | 证据 |
|---|---|---|
| advisor.py generate_advice 未修改 | ✅ | L869 签名 `def generate_advice(stock_id, report_date=None):` 无变动 |
| scoring_engine.py 未修改 | ✅ | 仅含原有 `import logging` + `__main__` 块 basicConfig，无012改动 |
| config_weights.json 未修改 | ✅ | rating_mapping: 80/65/50/30 四档完整 |
| data_collector.py 三处 if False | ✅ | L1776、L1815、L1848 三处 `if False` 硬禁用仍在 |
| fetch_capital_flow 签名不变 | ✅ | L1577: `def fetch_capital_flow(symbol, market):` |
| 零代码约束 | ✅ | requirements.txt 仍8包（akshare/Flask/pandas/numpy/python-dateutil/pydantic/requests/openpyxl） |
| 011增量逻辑完整 | ✅ | Q10 回归验证通过 |

---

## 总结论

**10/10 测试项全部 PASS，7/7 红线全部确认。**

012-A（文件日志系统）、012-B（日报进度追踪+超时控制）、012-C（error_logs增强）三个子任务功能正确、实现完整、未触碰红线。

**建议 PM + QA 双签关闭 012。**
