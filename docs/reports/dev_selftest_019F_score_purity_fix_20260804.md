# 开发自验报告 019F — AF-1 评分纯净缺口修复 + inspect.stack 保护块

**批次**：019F（019E 后续，小批次修复）
**开发**：开发工程师（单人）
**自验日期**：2026-08-04
**任务书**：`docs/tasks/dev_tasks_20260804_019F_score_purity_fix.md`
**架构评审**：`docs/reviews/review_019F_score_purity_20260804.md`（有条件通过，M-1 文档措辞 / M-2 追溯注释已落实）
**状态**：自验通过，待监理汇报 → QA 独立验收 → PM+QA 双签

---

## 一、改动清单（严格两文件各一处）

### 任务 1：analysis_engine.py — AF-1 评分纯净过滤（核心）

- **文件**：`modules/analysis_engine.py`
- **函数**：`_read_capital_data(stock_id, limit=20)`（L123-140）
- **改动**：
  1. docstring 补 019F 追溯注释（M-2）：`019F：过滤估算行（is_estimated=1），确保评分仅使用真实数据（与 data_adapter/advisor 同源）。`
  2. SQL 增一行过滤：`AND (is_estimated = 0 OR is_estimated IS NULL)`（L132，8 空格缩进）
- **签名**：`_read_capital_data(stock_id, limit=20)` 不变 ✅
- **改动后 L129-134**：

```python
    cursor.execute(
        """
        SELECT * FROM raw_capital_flow WHERE stock_id = ?
        AND (is_estimated = 0 OR is_estimated IS NULL)
        ORDER BY trade_date DESC LIMIT ?
    """,
        (stock_id, limit),
    )
```

### 任务 2：data_collector.py — inspect.stack 保护块

- **文件**：`modules/data_collector.py`
- **位置**：`fetch_capital_flow_batch` 内 L1446-1451（019E 补采触发来源判断）
- **改动**：裸调用 `_inspect.stack()[1].filename` 包裹 `try-except`，异常降级为 `'batch-analyze'`
- **签名**：`fetch_capital_flow_batch` 不变 ✅
- **改动后 L1446-1451**：

```python
    import inspect as _inspect
    try:
        _caller_file = _inspect.stack()[1].filename
        _trigger_source = '日报批次' if 'daily_report' in _caller_file else 'batch-analyze'
    except Exception:
        _trigger_source = 'batch-analyze'
```

---

## 二、自验结果

### V1：编译验证 ✅

```
python -m py_compile modules/analysis_engine.py   → 成功（$? = True）
python -m py_compile modules/data_collector.py     → 成功（$? = True）
```

两个文件均无语法错误。

### V2：评分纯净过滤表达式核查 ✅

grep `is_estimated = 0 OR is_estimated IS NULL` 全仓命中（评分链路 4 处读取入口全覆盖 + 2 处补采校验）：

| # | 文件:行 | 用途 | 019F 状态 |
|---|---|---|---|
| 1 | data_adapter.py L282 | 主评分链路（参照基准） | 019E 已过滤（回归未破坏） |
| 2 | advisor.py L1126 | 顾问资金因子链路 | 019E 已过滤（回归未破坏） |
| 3 | data_collector.py L1465 | 补采去重校验 | 019E 已过滤（回归未破坏） |
| 4 | **analysis_engine.py L132** | **legacy v4 降级评分路径** | **019F 本次修复 ✅** |
| 5 | data_collector.py L1891 | 补采计数校验 | 019E 已过滤（回归未破坏） |

### V3：过滤表达式逐字符一致性 ✅

逐字符比对分析（本批次新增表达式 vs 参照基准）：

- analysis_engine.py L132：`        AND (is_estimated = 0 OR is_estimated IS NULL)`
- data_adapter.py L282：    `        AND (is_estimated = 0 OR is_estimated IS NULL)`
- advisor.py L1126：        `AND (is_estimated = 0 OR is_estimated IS NULL) `

核心表达式 `AND (is_estimated = 0 OR is_estimated IS NULL)` 逐字符完全一致（空格/大小写/括号无变体）。analysis_engine 与 data_adapter 缩进同为 8 空格。

### V4：inspect.stack 保护块核查 ✅

grep `_trigger_source = 'batch-analyze'` 命中 data_collector.py L1451，前置 `except Exception:`（L1450）。确认：

- `except` 捕获 `Exception`（非 `BaseException`），不会吞 `KeyboardInterrupt`/`SystemExit` ✅
- 降级值 `'batch-analyze'` 非空字符串，类型一致 ✅
- `import inspect as _inspect` 保留在 try 块上方 ✅

### V5：零改动确认 ✅

- 本会话仅对 `modules/analysis_engine.py`、`modules/data_collector.py` 各调用一次 SearchReplace，未触碰其余任何文件。
- `git status` 确认 **scoring_engine.py 未出现在改动列表中**（相对 HEAD 零改动）。
- `db_manager.py`、`index.html`、`config_weights.json`、`requirements.txt`、`advisor.py`、`data_adapter.py` 等文件本次未调用任何编辑工具，保持 019E 既有状态。

> 注：`git status` 显示的众多 `M` 文件为 019A-019E 历史批次累积的未提交改动，非本批次产生。

### V6：红线遵守情况

| 红线 | 遵守情况 |
|---|---|
| 评分纯净红线（过滤表达式四处同源） | ✅ analysis_engine 新增点与 data_adapter/advisor/data_collector 逐字符一致 |
| 范围红线（仅两文件各一处） | ✅ 严格收敛 |
| 签名红线 | ✅ `_read_capital_data` / `fetch_capital_flow_batch` 签名均不变 |
| 零代码约束 | ✅ 未引入新依赖，requirements.txt 未碰；无 schema 迁移 |
| 降级安全红线 | ✅ `except Exception` + 降级值 `'batch-analyze'`（非空字符串） |

---

## 三、范围外已知项（源自架构评审 M-1 登记，本批次不处理）

| 文件:行 | 用途 | 缺过滤原因 | 跟踪建议 |
|---|---|---|---|
| app.py L770 | /api/capital 前端展示 | 019E 设计估算"仅供展示"故故意不过滤 | 展示层 UX，范围外 |
| alert_engine.py L201 | 连续净流出预警 | 预警用，存在误报风险（R-1） | 建议后续批次评估（如 019G） |

---

## 四、环境约束遵守

- ✅ 未重启运行中的 app.py（PID 36692），仅改文件 + py_compile 静态验证
- ✅ 文件内容核对使用 Read 工具（规避 PowerShell 5.1 中文乱码）
- ✅ SearchReplace 直接编辑目标文件，未触发"工作区外 Write"问题

---

## 五、结论

本批次两项改动（AF-1 评分纯净过滤 + inspect.stack 保护块）均按任务书与架构评审定稿执行，编译通过、过滤表达式四处逐字符一致、范围严格收敛、红线全部遵守。

**自验结论：通过。提请监理汇报 → QA 独立验收。**
