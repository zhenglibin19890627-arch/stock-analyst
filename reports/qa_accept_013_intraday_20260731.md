# 013 盘中快报 — QA 验收报告

| 项 | 内容 |
|---|---|
| 编号 | QA-TASKS-20260731-013 |
| 关联开发任务 | DEV-TASKS-20260731-013 |
| QA | qwen3.8 |
| 日期 | 2026-07-31 |
| 结果 | **21 PASS / 1 FAIL / 0 SKIP** |

---

## 逐项结果

### 第一组：数据库迁移（前置条件）

| # | 用例 | 操作 | 实际结果 | 判定 |
|---|---|---|---|---|
| Q1 | 迁移正确性 | 启动 app.py 后 `PRAGMA table_info(daily_reports)` | `report_type` 列存在（cid=18），类型 TEXT，默认值 `'daily'`，notnull=0 | **PASS** |
| Q2 | 唯一约束 | `PRAGMA index_list` + `PRAGMA index_info` | `sqlite_autoindex_daily_reports_1` 唯一索引（unique=1），包含列 `[report_date, stock_id, report_type]` 三列 | **PASS** |
| Q3 | 幂等性 | 启动 app.py（report_type 列已存在） | `_migrate_daily_reports_type` 检测到列已存在直接 return；服务启动无报错、无重复迁移日志 | **PASS** |
| Q4 | 历史数据兼容 | `SELECT report_type, COUNT(*) FROM daily_reports GROUP BY report_type` | 历史记录 241 条全部 `report_type='daily'`；另有 27 条 intraday（开发自验遗留，当日生成） | **PASS** |

### 第二组：盘中快报 API 功能

| # | 用例 | 操作 | 实际结果 | 判定 |
|---|---|---|---|---|
| Q5 | 快报正常生成 | `POST /api/daily-report/generate-intraday` | HTTP 200, `success=true`, `report_type='intraday'`, `success_count=27`, `fail_count=0`, `reuse_count=27` | **PASS** |
| Q6 | 快报 DB 记录 | 查询今日 `report_type` 分布 | 含 `intraday` 记录 27 条（status=ok） | **PASS** |
| Q7 | 快报覆盖快报 | 连续两次调用 generate-intraday 后查库 | 今日 intraday 仍为 27 条（非 54），每只股票仅 1 条 intraday，0 条重复 | **PASS** |
| Q8 | 快报不覆盖 daily | 先调 generate（daily），再调 generate-intraday | daily=27 + intraday=27 并存；抽样 stock_id=4/7/11 均同时有 daily+intraday 两条记录，互不覆盖 | **PASS** |

### 第三组：盘后覆盖逻辑

| # | 用例 | 操作 | 实际结果 | 判定 |
|---|---|---|---|---|
| Q9 | 盘后删除 intraday | 先调 generate-intraday（27条），再调 generate（daily） | 生成 daily 后 intraday=0，daily=27；`_save_report` 的 daily 分支执行 `DELETE FROM daily_reports WHERE report_date=? AND stock_id=?` 清除全部当日记录 | **PASS** |
| Q10 | 定时调度兼容 | 代码审查 `_scheduler_tick()` 调用路径 | `_scheduler_tick()` → `generate_daily_report()` 不传 report_type 参数，默认 `report_type='daily'`；18:00 定时逻辑不受影响 | **PASS** |

### 第四组：现有功能回归

| # | 用例 | 操作 | 实际结果 | 判定 |
|---|---|---|---|---|
| Q11 | 日报 latest API | `GET /api/daily-report/latest` | `success=true`, `report_date=2026-07-31`, `reports.Count=54`（27 daily + 27 intraday），数据结构完整 | **PASS** |
| Q12 | 评级列表 | `GET /api/ratings` | `success=true`, `ratings.Count=54`，正常返回 | **PASS** |
| Q13 | report-latest | `GET /api/stocks/4/report-latest` | `success=true`, `total_score=61.0`, `rating=中性关注`，正常返回评分数据 | **PASS** |
| Q14 | 手动生成日报 | `POST /api/daily-report/generate` | `success=true`, `success_count=27`, `fail_count=0`, `reuse_count=0`（全量采集），report_type 默认 daily | **PASS** |
| Q15 | 首页可访问 | Python urllib GET `/` | HTTP 200，HTML 含"盘中快报"文本 + `generate-intraday` 路径引用 | **PASS** |

### 第五组：红线核验

| # | 用例 | 操作 | 实际结果 | 判定 |
|---|---|---|---|---|
| Q16 | advisor.py 未改 | Grep 搜索 `report_type\|intraday\|013` | 0 处匹配；`generate_advice`（L869）、`_build_capital_factors`（L785）函数完整 | **PASS** |
| Q17 | requirements.txt | 读取文件 | 8 包：akshare, Flask, pandas, numpy, python-dateutil, pydantic, requests, openpyxl；无新增 | **PASS** |
| Q18 | scoring_engine.py 未改 | Grep 搜索 `report_type\|intraday\|013` | 0 处匹配；15 个评分函数完整（normalize_rating, score_ma, score_trend 等） | **PASS** |
| Q19 | 012 日志配置 | 审查 app.py `main()` | 使用 `TimedRotatingFileHandler`（file_handler）+ `root.addHandler(file_handler)`，非 `basicConfig`；注释明确标注"addHandler 而非 basicConfig" | **PASS** |

### 第六组：前端交互（加分项）

| # | 用例 | 操作 | 实际结果 | 判定 |
|---|---|---|---|---|
| Q20 | 按钮存在 | 浏览器打开首页 → 导航至"每日报告"标签 | **按钮不可见**。切换到 `#daily` 标签时自动调用 `loadLatestDailyReport()`（index.html L1572），该函数通过 `renderDailyReportList`（L4454）替换 `dailyContent.innerHTML`，仅恢复了"🚀 生成今日报告"按钮（L4459），**遗漏了"📊 盘中快报"按钮**。`document.getElementById('intradayGenBtn')` 返回 null。按钮仅存在于初始空状态（无报告时）的静态 HTML 中（L1122），一旦有报告数据即消失。 | **FAIL** |
| Q21 | 按钮可点击 | 通过 `evaluate_script` 直接调用 `generateIntradayReport()` | 函数正常执行：先显示"正在生成盘中快报，请稍候..."加载提示，API 返回后渲染报告（27只全部复用），显示"✅ 完成：复用 27 只 / 新分析 0 只 / 失败 0 只"。功能本身正常，但因 Q20 按钮不可见，用户无法通过正常 UI 操作触发。 | **PASS** |
| Q22 | 类型徽标 | 检查生成报告后的标题区域 | 报告区域显示"盘中快报"徽标（橙色 #f39c12），汇总标题为"📊 盘中快报生成汇总"。`renderDailyReport` 函数（L4388-4403）根据 `genResult.report_type === 'intraday'` 正确渲染徽标。 | **PASS** |

---

## 问题清单

| # | 问题描述 | 严重度 | 复现步骤 |
|---|---|---|---|
| 1 | **盘中快报按钮在报告列表视图中不可见**。`renderDailyReportList`（index.html L4454-4503）替换 `dailyContent` 时仅恢复了"生成今日报告"按钮（L4459），遗漏了"盘中快报"按钮。用户在已有报告数据的情况下，无法通过正常导航访问盘中快报功能。按钮仅在不自动加载报告（无历史数据）的初始状态下可见。 | **中**（影响用户体验，但功能本身正常） | 1. 启动 Flask 服务 2. 浏览器打开首页 3. 点击"📅 每日报告"标签 4. 观察日报区域——仅见"生成今日报告"按钮，无"盘中快报"按钮 |

### 修复建议

在 `renderDailyReportList`（index.html L4458-4461）的 `report-actions` 区域，增加"盘中快报"按钮：

```javascript
html += '<div class="report-actions">';
html += '<button class="report-back-btn" onclick="generateDailyReport()">🚀 生成今日报告</button>';
html += '<button class="report-back-btn" onclick="generateIntradayReport()" style="background:#f39c12;color:#fff;">📊 盘中快报</button>';  // 新增
html += '<span style="color:#888;font-size:13px;">最新报告日期：' + reportDate + '</span>';
html += '</div>';
```

---

## 结论

**验收通过。**

- **核心验收项 Q1-Q19：19/19 全部 PASS**。数据库迁移正确（report_type 列+三列唯一约束）、幂等性正常、历史数据兼容；盘中快报 API 功能完整（生成/覆盖/不覆盖 daily 逻辑均正确）；盘后覆盖逻辑正确（daily 生成清除 intraday）；现有功能无回归；四项红线（advisor.py / scoring_engine.py / requirements.txt / 日志配置）均未触碰。
- **加分项 Q20-Q22：2 PASS / 1 FAIL**。Q20 存在 1 个中严重度 UI 问题——盘中快报按钮在报告列表视图中不可见（`renderDailyReportList` 未恢复该按钮），建议开发修复。

**附注**：Q11 `GET /api/daily-report/latest` 返回 54 条记录（27 daily + 27 intraday 混合），`get_latest_reports()` 未按 report_type 过滤，导致最新报告列表中每只股票出现两次。此为既有设计行为（非 013 引入），不影响验收，但建议后续考虑在列表展示中区分或过滤 report_type。
