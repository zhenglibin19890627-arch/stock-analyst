# 开发自验报告 019H — is_estimated 过滤补全（预警层 alert_engine.py）

**批次**：019H（019F 备查项后续，小批次修复）
**开发**：开发工程师（单人，Quests 独立窗口）
**自验日期**：2026-08-05
**任务书**：`docs/tasks/dev_tasks_20260805_019H_is_estimated过滤补全.md`（v2 定稿）
**架构评审**：`docs/reviews/review_019H_is_estimated_20260805.md`（有条件通过，M-1/M-2/M-3/R-1/R-2/R-3）
**状态**：开发执行完成 + 自验通过，待 QA 独立验收 → PM+QA 双签 → 监理批准关闭

---

## 一、改动清单（严格一文件一处，M-1 收敛口径）

### 任务 1：alert_engine.py — 预警查询过滤补全（中风险，核心）

- **文件**：`modules/alert_engine.py`
- **函数**：`check_capital_outflow(cursor, stock_id, n_days=3)`（L183-234）
- **改动**：
  1. L200 补 019H 追溯注释：`# 019H：过滤估算行（is_estimated=1），确保预警判定仅使用真实资金流数据`
  2. L205 SQL 增一行过滤：`AND (is_estimated = 0 OR is_estimated IS NULL)`（11 空格缩进，与同 SQL 内 `FROM`/`WHERE` 对齐）
- **签名**：`check_capital_outflow(cursor, stock_id, n_days=3)` 不变 ✅
- **参数绑定**：`(stock_id, n_days * 2)` 顺序不变，`LIMIT ?` 位置不变（红线 8）✅
- **无附加 SQL 条件**：未附加 `main_net_inflow IS NOT NULL`（红线 7，NULL 行维持由 Python L216 `r['main_net_inflow'] is not None` 过滤处理）✅
- **改动后 L199-209**：

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

### 任务 2：app.py 展示层 — 不改（架构评审 M-1 否决方案 A，本批次零改动）

前端 `templates/index.html` L2481-2490 已在 019E Task 4.1 实现估算行双层标注（表头"含估算兜底数据"提示 + 行级橙色"估算"上标），019E 架构评审已裁定展示层"不过滤、前端标注"。**本批次不修改 app.py 与 index.html。**

---

## 二、自验结果（对照任务书验收标准）

### V1：代码级核查 ✅（验收 1）

- grep `AND (is_estimated = 0 OR is_estimated IS NULL)` **alert_engine.py 恰 1 处**（L205）
- 过滤表达式与评分链路 3 处参照基准**逐字符一致**（PowerShell 字节级比对 `[regex]::Escape` 全命中 MATCH）：

| 文件 | 行 | 比对结果 |
|---|---|---|
| data_adapter.py | L282 | ✅ MATCH |
| advisor.py | L1126 | ✅ MATCH |
| analysis_engine.py | L132 | ✅ MATCH |
| **alert_engine.py（本批次新增）** | **L205** | ✅ MATCH |

### V2：编译验证 ✅（验收 2）

```
python -m py_compile modules/alert_engine.py → 成功（$? = True）
```

### V3：预警纯净验证 ✅（验收 3，R-3 补正路径场景）

临时脚本（内存级 SQLite 建/拆，测试后即删，未留存于仓库）执行 `check_capital_outflow` 三场景：

| 场景 | 数据构造 | 期望 | 结果 |
|---|---|---|---|
| **负路径** | 2 行真实净流出 + 1 行 `is_estimated=1` 净流出（2026-08-01~03） | **不触发**（估算行不计入窗口） | ✅ PASS |
| **正路径** | 3 行 `is_estimated=0` 连续净流出（-100/-200/-300）+ 其间穿插 1 行 `is_estimated=1`（-999） | **触发**且 `total_outflow=600.0`（估算行 -999 未混入统计） | ✅ PASS |
| **NULL 维持**（红线 7） | 3 行真实净流出 + 1 行 `main_net_inflow=NULL` | 触发且 `total_outflow=600.0`（NULL 由 Python 层过滤） | ✅ PASS |

### V4：展示层回归验证 ✅（验收 4，静态核查）

- 本批次**未触碰** app.py / index.html，`/api/stocks/<id>/capital` 路由与前端估算标注代码保持 019E 既有状态（估算行仍返回，前端 L2481-2490 双层标注在位）
- 路由级动态回归建议由 QA 在运行实例上复核（见交接说明）

### V5：全仓 grep 闭合确认 ✅（验收 5，R-2 口径修正）

canonical 子串 `AND (is_estimated = 0 OR is_estimated IS NULL)` 全仓 *.py 命中 **6 处**（modules 内）：

| # | 文件:行 | 用途 | 状态 |
|---|---|---|---|
| 1 | data_adapter.py L282 | 主评分链路 | 019E 既有 |
| 2 | advisor.py L1126 | 顾问资金因子链路 | 019E 既有 |
| 3 | data_collector.py L1477 | 补采去重校验 | 019E 既有 |
| 4 | analysis_engine.py L132 | legacy v4 降级路径 | 019F 既有 |
| 5 | data_collector.py L1903 | EM 前置校验变体（含 canonical 子串，非新增） | 019E 既有 |
| 6 | **alert_engine.py L205** | **预警判定查询（本批次新增）** | **019H 本次修复 ✅** |

展示层有意不过滤，不计入。`tests/qa_019f_isolation_test.py` 中 2 处为测试断言常量定义，不计入。

### V6：019F 回归验证 ✅（验收 6）

```
python -m pytest tests/qa_019f_isolation_test.py -v → 9/9 PASSED（0.30s）
```

- T8/T9 过滤表达式一致性：通过
- 已有 4 处评分链路过滤（T1-T4）：未被破坏

### V7：零改动确认 ✅（验收 7）

- 本会话仅对 `modules/alert_engine.py` 调用 1 次编辑（Edit 工具）
- `git status` 确认：**本批次新增改动仅 `modules/alert_engine.py` 一个文件**（会话开始时该文件不在改动列表中，会话结束后为唯一新增 M 项）
- 临时自验脚本已删除，未留存
- app.py / index.html / export_engine.py / data_adapter.py / advisor.py / data_collector.py / analysis_engine.py / scoring_engine.py / daily_report.py / db_manager.py / config 系列 / requirements.txt 均零改动

> 注：`git status` 中其余 M 文件为 019A-019G 历史批次累积的未提交改动，非本批次产生。

---

## 三、红线遵守情况

| 红线 | 遵守情况 |
|---|---|
| 1. 过滤表达式一致性（逐字符，不自创变体） | ✅ L205 与 4 处评分链路基准字节级一致 |
| 2. 范围红线（仅 alert_engine.py 1 处 SQL） | ✅ 严格收敛 |
| 3. 签名红线 | ✅ `check_capital_outflow(cursor, stock_id, n_days=3)` 不变；capital 路由不碰 |
| 4. 零代码约束 | ✅ 无新 pip 依赖（requirements.txt 未碰）；无 schema 迁移 |
| 5. 评分纯净红线（不可回退） | ✅ 019F 隔离测试 9/9 通过，4 处既有过滤未破坏 |
| 6. 展示层不动红线 | ✅ app.py / index.html / export_engine.py 零改动 |
| 7. SQL 变体红线 | ✅ 未附加 `main_net_inflow IS NOT NULL`；NULL 行维持 Python 层过滤（自验 V3 场景 3 实证） |
| 8. 参数绑定红线 | ✅ `LIMIT ?` 位置不变，`(stock_id, n_days * 2)` 顺序不变 |

---

## 四、QA 交接说明（独立验收指引）

### 4.1 验收标准映射

| 任务书验收 | QA 执行方式建议 |
|---|---|
| 1. 代码级核查 | grep `alert_engine.py` 恰 1 处；与 data_adapter L282 / advisor L1126 / analysis_engine L132 逐字符比对 |
| 2. 编译验证 | `python -m py_compile modules/alert_engine.py` |
| 3. 预警纯净验证 | 自建隔离 DB 执行负/正路径场景（负：2 真实 + 1 估算不触发；正：3 真实 + 穿插 1 估算触发且 total_outflow 仅含真实行）。注意 `_get_stock_info` 需 stocks 表存在对应 `market != 'hk_stock'` 记录 |
| 4. 展示层回归 | 运行实例 `GET /api/stocks/<id>/capital` 确认估算行仍返回（`is_estimated=1` 行存在）；截图核查 index.html L2490 橙色"估算"标注与表头提示 |
| 5. 全仓 grep 闭合 | canonical 子串命中 **6 处**（见 V5 表），展示层不计入 |
| 6. 019F 回归 | `python -m pytest tests/qa_019f_isolation_test.py -v` 全通过 |
| 7. 零改动确认 | 文件哈希比对 app.py / index.html / export_engine.py 及全部评分链路文件与开发前基线一致 |

### 4.2 开发环境备注

- 临时验证脚本已删除（红线 2 范围收敛），QA 需自行构造隔离测试数据
- 本批次未重启运行中的 app.py 进程；若 QA 需动态验证展示层，请自行启动实例
- 自验脚本核心数据构造（可复用）：
  - 负路径：`(sid,'2026-08-01',-100.0,0) (sid,'2026-08-02',-200.0,0) (sid,'2026-08-03',-300.0,1)` → 期望 `None`
  - 正路径：`(sid,'2026-08-01',-100.0,0) (sid,'2026-08-02',-999.0,1) (sid,'2026-08-03',-200.0,0) (sid,'2026-08-04',-300.0,0)` → 期望触发且 `total_outflow=600.0`

---

## 五、范围外已知项（本批次不处理）

| 文件:行 | 用途 | 缺过滤原因 | 跟踪建议 |
|---|---|---|---|
| export_engine.py | 报告导出层 | 019H 架构评审 M-2 新发现，风险低（导出仅展示用） | 建议 019I 处理 |
| app.py / index.html | 展示层 | 019E 已裁定"不过滤、前端标注"（M-1 否决方案 A） | 维持现状 |

---

## 六、结论

本批次按任务书 v2 定稿执行：改动严格收敛为一文件一处（`modules/alert_engine.py` L200-207），过滤表达式与评分链路 4 处闭合点逐字符一致，签名/参数绑定/语法结构均不变，8 条红线全部遵守。自验覆盖编译、负/正/NULL 三路径行为、019F 回归（9/9）、全仓闭合计数（6 处）、零改动确认，全部通过。

**自验结论：通过。提请 QA 独立验收 → PM+QA 双签 → 监理批准关闭。**
