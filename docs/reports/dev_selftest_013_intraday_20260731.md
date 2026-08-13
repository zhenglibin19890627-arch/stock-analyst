# DEV-TASKS-20260731-013 盘中快报模式 — 开发自验报告

| 项 | 内容 |
|---|---|
| 编号 | DEV-TASKS-20260731-013 |
| 开发人员 | 开发（智能体单代理） |
| 自验日期 | 2026-07-31 |
| Python | 3.12 (C:\Users\zlb19\AppData\Local\Programs\Python\Python312) |
| 结果 | **全部通过 (V1~V8 ✅)** |

---

## 一、代码变更清单

| # | 文件 | 变更内容 |
|---|---|---|
| 1 | `database/db_manager.py` | 新增 `_migrate_daily_reports_type(cursor)` 迁移函数；`init_database()` 中 `_migrate_columns()` 之后调用 |
| 2 | `modules/daily_report.py` | `_save_report()` 新增 `report_type` 参数，UPSERT 改为 DELETE+INSERT；`_process_single_stock()` 新增 `report_type` 参数，复用检查限定 report_type；`generate_daily_report()` 新增 `report_type` 参数并透传至所有子调用 |
| 3 | `app.py` | 新增 `/api/daily-report/generate-intraday` POST 端点 |
| 4 | `templates/index.html` | 新增 `.btn-warning` CSS；新增"📊 盘中快报"按钮；新增 `generateIntradayReport()` JS 函数；`renderDailyReport()` 增加报告类型徽标 |

---

## 二、红线检查

| 红线 | 状态 | 说明 |
|---|---|---|
| advisor.py generate_advice | ✅ 未触碰 | 无改动 |
| advisor.py _build_capital_factors | ✅ 未触碰 | 无改动 |
| data_collector.py 三处 if False | ✅ 未触碰 | 无改动 |
| config_weights.json rating_mapping | ✅ 未触碰 | 无改动 |
| 零代码约束 (8包) | ✅ 未触碰 | requirements.txt 无变化 |
| scoring_engine.py v5 | ✅ 未触碰 | 无改动 |
| 011 增量逻辑 (force参数) | ✅ 未触碰 | force 参数和增量跳过逻辑保持不变 |
| 012 日志配置 | ✅ 未触碰 | 无改动 |
| 012 超时配置 | ✅ 未触碰 | STOCK_TIMEOUT_SECONDS=90 / BATCH_TIMEOUT_SECONDS=1800 |

---

## 三、自验结果 V1~V8

### V1: 迁移幂等性 ✅ PASS

- **方法**: 连续调用 `init_database()` 两次，检查 `daily_reports` 表结构和唯一约束
- **结果**:
  - 迁移前: `report_type` 列不存在
  - 第一次调用: `[013迁移] daily_reports 新增 report_type 列，重建唯一约束...` → 成功
  - 第二次调用: 无迁移日志、无报错（幂等跳过）
  - 唯一约束: `sqlite_autoindex_daily_reports_1` columns=`['report_date', 'stock_id', 'report_type']` ✅

### V2: 盘中快报生成 ✅ PASS

- **方法**: 启动 Flask 后 `POST /api/daily-report/generate-intraday`
- **结果**:
  ```
  HTTP 200
  success=True
  report_type=intraday
  report_date=2026-07-31
  total=27, success_count=27, fail_count=0
  v5_count=27, legacy_count=0
  ```
  - DB 确认: 2026-07-31 有 27 条 intraday 记录，status 均为 ok

### V3: 快报不覆盖 daily ✅ PASS

- **方法**: 直接调用 `_save_report()` 先存 daily 再存 intraday，查询记录数
- **结果**:
  - 保存 daily 后: daily=1
  - 保存 intraday 后: daily=1, intraday=1, total=2 ✅ (daily 未被覆盖)

### V4: 快报覆盖快报 ✅ PASS

- **方法**: 连续两次 `_save_report(report_type='intraday')`
- **结果**:
  - 第一次后: intraday=1
  - 第二次后: intraday=1 ✅ (仅保留最新一条)

### V5: 盘后覆盖快报 ✅ PASS

- **方法**: 先存 intraday 再存 daily
- **结果**:
  - 保存 intraday 后: intraday=1
  - 保存 daily 后: intraday=0, daily=1, total=1 ✅ (intraday 被清除)

### V6: 现有功能不受影响 ✅ PASS

- **方法**: 调用 `get_latest_reports()`、`get_reports_by_date()`、`get_report_history()`、`MAX(report_date)` SQL
- **结果**:
  - `get_latest_reports()`: success=True, date=2026-07-30, count=27, report_type=daily
  - `get_reports_by_date('2026-07-30')`: success=True, count=27
  - `get_report_history()`: success=True, total=12, 返回正常
  - `MAX(report_date)`: 正常返回 2026-07-30
  - 现有 API `/api/daily-report/latest` 和 `/api/ratings` 均正常响应

### V7: 前端按钮可用 ✅ PASS

- **方法**: 访问首页 HTML，检查按钮、JS 函数、API 调用路径
- **结果**:
  - 含 `generateIntradayReport` 函数: True
  - 含"盘中快报"文案: True
  - 含 API 调用路径 `/api/daily-report/generate-intraday`: True
  - 含类型徽标(盘中快报/收盘报告): True
  - `.btn-warning` CSS 已定义: True

### V8: 零依赖 ✅ PASS

- **方法**: 读取 requirements.txt 统计包数
- **结果**: 8 包，与变更前完全一致
  ```
  akshare>=1.16.0, Flask>=3.0.0, pandas>=2.1.0, numpy>=1.26.0,
  python-dateutil>=2.8.0, pydantic>=2.12.0, requests>=2.28.0, openpyxl>=3.1.0
  ```

---

## 四、业务规则验证

| 场景 | 期望行为 | 实测 | 结果 |
|---|---|---|---|
| 盘中点击快报，当天无报告 | 生成 intraday | 27 条 intraday 写入 | ✅ |
| 盘中再次点击快报（已有 intraday） | 覆盖上一条 intraday | intraday 记录数保持 1 | ✅ |
| 盘中点击快报，当天已有 daily | 生成 intraday，不动 daily | daily=1 + intraday=1 并存 | ✅ |
| 盘后生成（daily） | 删除当天 intraday + 保存 daily | intraday 被删除，仅剩 daily | ✅ |

---

## 五、技术实现细节

### 5.1 复用检查增强（013 新增）

`_process_single_stock()` 中的增量复用检查（B11-REPORT-REUSE）新增 `report_type` 过滤条件：
```sql
WHERE stock_id=? AND report_date=? AND status="ok" AND report_type=?
```
- intraday 只复用 intraday，不会误复用已有 daily 数据
- daily 复用逻辑不变，仍按 daily 类型查询

### 5.2 DELETE + INSERT 替代 UPSERT

原 `ON CONFLICT(report_date, stock_id) DO UPDATE` 已替换为：
- daily: `DELETE WHERE report_date=? AND stock_id=?`（清除所有类型当天记录）
- intraday: `DELETE WHERE ... AND report_type='intraday'`（仅清除 intraday）

---

## 六、总结

**V1~V8 全部通过 ✅**，红线约束全部遵守，零新增依赖。盘中快报功能可交付 PM/QA 验收。
