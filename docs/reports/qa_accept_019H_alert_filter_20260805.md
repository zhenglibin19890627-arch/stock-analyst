# QA 验收报告 019H — is_estimated 过滤补全（预警层 alert_engine.py）

**验收人**：QA 工程师（独立验收，不采信开发自验结论）
**验收日期**：2026-08-05
**批次**：019H（019F 备查项后续，小批次修复）
**任务书**：`docs/tasks/dev_tasks_20260805_019H_is_estimated过滤补全.md`（v2 定稿）
**架构评审**：`docs/reviews/review_019H_is_estimated_20260805.md`（有条件通过，M-1/M-2/M-3/R-1/R-2/R-3）
**开发自验**：`reports/dev_selftest_019H_alert_filter_20260805.md`（仅对照参考，结论不采信）
**验收结论**：**✅ 通过（7/7 用例 PASS）**

---

## 一、验收方法说明

QA 独立执行全量核查，不依赖开发自验结论，逐项重新验证：

- 源码级 Read 核查（直接读取改动文件真实内容）
- grep + Python 字节级比对（canonical 子串逐字符一致性，独立脚本验证）
- git diff 核查（区分本批次改动与 019A-019G 历史累积改动）
- py_compile 编译验证
- 内存级 SQLite 隔离实测 `check_capital_outflow`（自建测试数据，5 场景含任务书 A/B/C + 2 附加对照）
- 运行实例动态验证（PID 30488 `python app.py`，端口 5000）：API 返回核查 + playwright 前端 DOM 渲染核查 + 截图存档

**测试数据隔离**：实测使用内存级 SQLite（不落盘）；唯一一次真实库写入采用专用日期 `2099-12-31`，验收结束前已删除，最终复核 `COUNT=0`（见第五节）。

---

## 二、逐用例验收结果

### TC-019H-1 代码级核查 — ✅ PASS（5/5）

| # | 检查项 | 证据 | 结果 |
|---|---|---|---|
| 1 | 过滤表达式存在，alert_engine.py 恰 1 处 | grep `AND (is_estimated = 0 OR is_estimated IS NULL)` 全仓命中，alert_engine.py 仅 L205 一处 | ✅ |
| 2 | 逐字符一致性（红线 1） | Python 字节级比对（独立脚本，UTF-8 字节相等）：alert_engine L205 / data_adapter L282 / advisor L1126 / analysis_engine L132 四处 `byte_exact=True`，repr 完全一致 | ✅ |
| 3 | 无附加 SQL 条件（红线 7） | Read L201-209：SQL 仅 `WHERE stock_id=?` + `AND (is_estimated = 0 OR is_estimated IS NULL)`，无 `main_net_inflow IS NOT NULL` 等附加条件；NULL 行由 Python L216 `r['main_net_inflow'] is not None` 过滤 | ✅ |
| 4 | 签名不变（红线 3） | Read L183：`def check_capital_outflow(cursor, stock_id, n_days=3):`（无下划线前缀，n_days 默认 3） | ✅ |
| 5 | 参数绑定不变（红线 8） | Read L207-208：`LIMIT ?` 位置不变，绑定 `(stock_id, n_days * 2)` 顺序不变 | ✅ |

改动后代码（L199-209，QA 直接 Read 原文）：

```python
    # 查询最近 N*2 个交易日（考虑缺失，多取）
    # 019H：过滤估算行（is_estimated=1），确保预警判定仅使用真实资金流数据
    cursor.execute(
        """SELECT trade_date, main_net_inflow
           FROM raw_capital_flow
           WHERE stock_id=?
           AND (is_estimated = 0 OR is_estimated IS NULL)
           ORDER BY trade_date DESC
           LIMIT ?""",
        (stock_id, n_days * 2),
    )
```

### TC-019H-2 编译验证 — ✅ PASS

```
python -m py_compile modules/alert_engine.py → 无错误（exit 0）
```

### TC-019H-3 预警纯净验证（★核心，验收 3）— ✅ PASS（3/3 + 2 附加对照）

**方法**：QA 独立构造内存级 SQLite（stocks + raw_capital_flow 表结构与真实库一致，含 `is_estimated` 列），插入非港股 `market='a_stock'` 记录后直接调用 `check_capital_outflow`。测试日期专用 `2099-12-xx`。

| 场景 | 数据构造 | 期望 | 实测 | 结果 |
|---|---|---|---|---|
| **A 负路径（不误报）** | 2 行 `is_estimated=0` 净流出（-100/-200）+ 1 行 `is_estimated=1` 净流出（-300） | `None`（估算行不计入窗口，真实数据不足 3 日） | `result=None` | ✅ |
| **B 正路径（不漏报）** | 3 行 `is_estimated=0` 净流出（-100/-200/-300）+ 穿插 1 行 `is_estimated=1`（-999） | 触发且 `total_outflow=600.0`（估算 -999 未混入） | `total_outflow=600.0`，`consecutive_days=3`，`dates=['2099-12-31','2099-12-30','2099-12-28']`（估算日 2099-12-29 不在窗口） | ✅ |
| **C NULL 维持（红线 7）** | 3 行 `is_estimated=0` 净流出 + 1 行 `main_net_inflow=NULL`（is_estimated=0） | 触发且 `total_outflow=600.0`（NULL 行由 Python L216 过滤） | `total_outflow=600.0`，`consecutive_days=3` | ✅ |
| 附加-港股 | `market='hk_stock'`，3 行净流出 | `None`（L196-197 直接跳过） | `result=None` | ✅ |
| 附加-纯估算 | 3 行 `is_estimated=1` 净流出 | `None`（估算不计入，更安全，R-3 预期行为） | `result=None` | ✅ |

实测汇总：**PASS=5 FAIL=0**。正路径 `total_outflow=600.0 = 100+200+300`，估算行 -999 未混入统计；负路径无假信号。

### TC-019H-4 展示层回归验证（验收 4，M-1 裁定"不改"）— ✅ PASS（3/3）

| # | 检查项 | 证据 | 结果 |
|---|---|---|---|
| 1 | app.py L770 零改动、无过滤 | Read L768-774：`SELECT * FROM raw_capital_flow WHERE stock_id = ?`（`SELECT *` 含 is_estimated 字段返回），无过滤条件，有意不过滤（M-1） | ✅ |
| 2 | 前端标注在位（019E 既有状态） | Read index.html L2481 `hasEstimated`（表头"含估算兜底数据"动态文案）+ L2490 `estTag`（行级橙色"估算"上标）均在位 | ✅ |
| 3 | 运行实例动态验证 | 运行实例 PID 30488（`python app.py`，端口 5000）实测：① 插入专用日期估算行后 `GET /api/stocks/4/capital` 返回 `count=1`，`is_estimated=1` 行存在且带 `_display=-123.45 万元`（未被过滤）；② playwright+系统 Edge 打开首页执行 `viewData(4)`：DOM 断言 `2099-12-31` 行 ✅、`估算` 上标 ✅、表头 `含估算兜底数据` ✅；③ 截图存档 `screenshots/qa_019h_est_tag.png`（126KB） | ✅ |

### TC-019H-5 全仓 grep 闭合确认（验收 5，R-2 口径）— ✅ PASS

canonical 子串 `AND (is_estimated = 0 OR is_estimated IS NULL)` 全仓 *.py 命中 **8 处**：modules 内 **6 处** + tests 断言常量 2 处（不计入）：

| # | 文件:行 | 用途 | 状态 |
|---|---|---|---|
| 1 | data_adapter.py L282 | 主评分链路 | 019E 既有 |
| 2 | advisor.py L1126 | 顾问资金因子链路 | 019E 既有 |
| 3 | data_collector.py L1477 | 补采去重校验 | 019E 既有 |
| 4 | analysis_engine.py L132 | legacy v4 降级路径 | 019F 既有 |
| 5 | data_collector.py L1903 | EM 前置校验变体（含 canonical 子串） | 019E 既有 |
| 6 | **alert_engine.py L205** | **预警判定查询（本批次新增）** | **019H 本次修复** ✅ |
| 7-8 | tests/qa_019f_isolation_test.py L199/L214 | 测试断言常量定义 | 不计入 |

展示层 app.py L770 有意不过滤，不计入（M-1）。全仓无其他遗漏读取入口。

### TC-019H-6 019F 回归验证（验收 6）— ✅ PASS（9/9）

```
python -m pytest tests/qa_019f_isolation_test.py -v → 9 passed in 0.13s
```

- T1-T4（已有 4 处评分链路过滤未被破坏）：PASSED
- T8/T9（过滤表达式一致性）：PASSED
- 红线 5（评分纯净）确认：本批次改动未影响评分链路

### TC-019H-7 零改动确认（验收 7）— ✅ PASS

| 核查方式 | 证据 | 结果 |
|---|---|---|
| git diff（仅本批次文件） | `git diff modules/alert_engine.py`：仅 +2 行（019H 注释 L200 + 过滤条件 L205），无其他改动 | ✅ |
| 全仓 019H 痕迹扫描 | grep `019H` 全仓 *.py：**仅 alert_engine.py L200 一处**（注释），其余文件无本批次痕迹 | ✅ |
| 其余 M 文件 diff 核查 | git diff 全部 17 个其他 M 文件（app.py / index.html / data_adapter.py / advisor.py / data_collector.py / analysis_engine.py / daily_report.py / db_manager.py / config_engine_switch.json 等）：`019H` 计数均为 0（其 M 状态为 019A-019G 历史累积改动，非本批次） | ✅ |
| 基线未动文件 | git status：export_engine.py / scoring_engine.py / config_weights.json / config.py / requirements.txt **不在改动列表**（相对 git 基线零改动） | ✅ |
| 红线 6（展示层不动） | app.py L770、index.html L2481/L2490 均保持 019E 既有状态（Read 原文比对） | ✅ |

---

## 三、红线遵守情况

| 红线 | 内容 | QA 核查结果 |
|---|---|---|
| 1 | 过滤表达式逐字符一致，不自创变体 | ✅ 字节级比对 4 处 byte_exact |
| 2 | 改动仅限 alert_engine.py（1 处 SQL） | ✅ 019H 痕迹全仓仅 1 处 |
| 3 | 签名 `check_capital_outflow(cursor, stock_id, n_days=3)` 不变 | ✅ L183 实测 |
| 4 | 零代码约束（无新依赖/无 schema 迁移） | ✅ requirements.txt 未动，is_estimated 列 019E 已就位 |
| 5 | 评分纯净（4 处既有过滤不受影响） | ✅ 019F 回归 9/9 |
| 6 | 展示层不动（app.py / index.html / export_engine.py） | ✅ git diff 无 019H 痕迹 |
| 7 | 无附加 SQL 条件（NULL 由 Python 过滤） | ✅ L201-209 核查 + 场景 C 实测 |
| 8 | LIMIT ? 位置与 (stock_id, n_days*2) 绑定不变 | ✅ L207-208 核查 |

## 四、验收总结

| 用例 | 内容 | 结果 |
|---|---|---|
| TC-019H-1 | 代码级核查（5 项） | ✅ PASS |
| TC-019H-2 | 编译验证 | ✅ PASS |
| TC-019H-3 | 预警纯净验证（A/B/C + 2 附加） | ✅ PASS（5/5） |
| TC-019H-4 | 展示层回归验证（含运行实例动态验证） | ✅ PASS（3/3） |
| TC-019H-5 | 全仓 grep 闭合确认（modules 6 处） | ✅ PASS |
| TC-019H-6 | 019F 回归测试 | ✅ PASS（9/9） |
| TC-019H-7 | 零改动确认 | ✅ PASS |

**合计：7/7 用例 PASS，8/8 红线遵守。**

## 五、测试数据清理确认

| 项 | 内容 | 结果 |
|---|---|---|
| 隔离实测数据 | 内存级 SQLite（5 场景），进程结束即销毁，未落盘 | ✅ 无残留 |
| 真实库专用日期数据 | 仅 1 次写入 `2099-12-31`（stock_id=4, is_estimated=1, -123.45），API/DOM 验证后已删除 | ✅ |
| 最终复核 | `SELECT COUNT(*) FROM raw_capital_flow WHERE trade_date LIKE '2099%'` = **0**（API 测试脚本与清理脚本双重确认） | ✅ |
| 临时脚本 | QA 自建脚本存放于系统临时目录，验收结束已全部删除；未在仓库内新增任何脚本 | ✅ |
| 仓库新增文件 | 仅 `screenshots/qa_019h_est_tag.png`（验收证据存档，124KB 级） | ✅ 保留 |

## 六、验收结论

**✅ 通过。** 本批次改动严格收敛为一文件一处（`modules/alert_engine.py` L200-205：注释 + 过滤条件），过滤表达式与评分链路 4 处闭合点逐字符一致；预警纯净实测证实**既不误报（负路径）也不漏报（正路径）**，NULL 行处理不受影响（场景 C）；019F 回归 9/9 通过，评分链路未被破坏；展示层保持 019E 裁定状态（API 实测估算行正常返回 + 前端双层标注正常渲染）。

**提请 PM 双签确认后，报监理批准关闭 019H。**

---

## 七、范围外已知项（本批次不验收，维持登记）

| 文件:行 | 用途 | 缺过滤原因 | 跟踪 |
|---|---|---|---|
| export_engine.py L278-285 | Excel 导出层 | 019H 架构评审 M-2 新发现，风险低 | 建议 019I 处理 |
| app.py L770 / index.html | 展示层 | 019E 已裁定"不过滤、前端标注"（M-1） | 维持现状 |

> **QA 备注**：验收期间未修改任何功能代码；`git status` 中 app.py 等 M 状态文件均为 019A-019G 历史批次累积改动，本批次唯一改动文件为 `modules/alert_engine.py`（已通过 019H 痕迹 + git diff 双重确认）。

---

## 八、PM+QA 双签确认（2026-08-05）

### PM 独立核验记录

PM 未采信 QA 验收结论，独立复验以下关键项：

| 核验项 | PM 方法 | 结果 |
|---|---|---|
| 代码级核查 | Read alert_engine.py L183-209：L200 注释在位、L205 过滤表达式在位、L183 签名 `check_capital_outflow(cursor, stock_id, n_days=3)` 正确、L208 参数绑定不变、无附加 SQL 条件 | ✅ PASS |
| 编译验证 | `python -m py_compile modules/alert_engine.py` → exit code 0 | ✅ PASS |
| 全仓 grep 闭合 | canonical 子串源码命中 6 处（modules 内），与 QA 报告 TC-5 一致 | ✅ PASS |
| 逐字符一致性 | alert_engine L205 与 data_adapter L282 / analysis_engine L132 表达式逐字符一致 | ✅ PASS |
| 展示层零改动 | app.py L770 仍无过滤（M-1 裁定）；index.html L2481-2490 019E 既有标注逻辑在位 | ✅ PASS |
| 019F 回归 | PM 独立复跑 `pytest tests/qa_019f_isolation_test.py -v` → 9 passed in 0.14s | ✅ PASS |
| 测试数据清理 | PM 独立查询 `SELECT COUNT(*) FROM raw_capital_flow WHERE trade_date LIKE '2099%'` → **0** | ✅ PASS |
| 截图存档 | `screenshots/qa_019h_est_tag.png` 存在，126910 bytes | ✅ PASS |

### 双签签署

| 角色 | 签署 | 日期 |
|---|---|---|
| **QA** | ✅ 验收通过（7/7 用例 PASS，8/8 红线遵守） | 2026-08-05 |
| **PM** | ✅ 独立核验通过（代码级 Read + py_compile + grep + 019F 回归复跑 + 测试数据清理确认 + 截图核查），确认 QA 报告结论可信 | 2026-08-05 |

**双签结论：019H 批次开发成果验收通过，提请监理批准关闭。**
