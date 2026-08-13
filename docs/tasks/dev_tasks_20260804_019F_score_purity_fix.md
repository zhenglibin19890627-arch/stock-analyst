# 开发任务书 019F — AF-1 评分纯净缺口修复 + inspect.stack 保护块

**签发日期**：2026-08-04
**签发人**：PM
**批次编号**：019F（019E 后续，小批次修复）
**优先级**：P1（评分纯净红线相关）
**关联批次**：019E（资金面估算兜底与 EM 覆盖，已双签关闭 2026-08-04）、019D（三入口评分同源，已关闭）
**关联评审项**：AF-1（019E 验收阶段 PM 提出的遗留缺口，待监理裁定后立项）
**架构评审**：✅ 有条件通过（评审报告：`docs/reviews/review_019F_score_purity_20260804.md`），已按 M-1/M-2 修订定稿 v2

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 推荐窗口类型 | Quests 独立窗口（单代理执行） |
| 推荐模型 | 开发：glm5.2 → QA：kimi k3（验收类任务） |
| 执行模式 | 已关闭 |
| 流程路径 | ✅PM 签发 v1 → ✅架构师评审（有条件通过，M-1/M-2 已修订） → ✅监理批准 → ✅开发执行+自验 → ✅QA 独立验收（9/9 PASS）→ ✅PM+QA 双签 → ✅监理批准关闭（2026-08-04） |

---

## 一、背景

### 缺陷溯源（AF-1）

019E 批次在评分隔离层面做了三处过滤点加固（data_adapter L282、advisor L1126、data_collector 补采清单 L1462），确保估算值 `is_estimated=1` 不进入评分链路。

但在 PM 核验阶段发现 **第四处** 读取入口遗漏：

| 文件 | 位置 | 用途 | 过滤状态 |
|---|---|---|---|
| `data_adapter.py` `_read_capital_data` | L280-284 | **主评分链路**（StockData 构建） | ✅ 已过滤（019E M-1） |
| `advisor.py` `_build_capital_factors` | L1124-1127 | **顾问链路**（资金因子构建） | ✅ 已过滤（019E M-2） |
| `data_collector.py` 补采清单 | L1460-1462 | 补采去重校验 | ✅ 已过滤（019E E-2） |
| **`analysis_engine.py` `_read_capital_data`** | **L129-131** | **legacy v4 路径（v5 熔断降级时触发）** | ❌ **缺过滤（AF-1）** |

**风险分析**：
- `analysis_engine._read_capital_data` 在常规路径不触发（主链路走 `data_adapter`）
- 但 v5 分析引擎在**内部异常熔断降级**时会回退调用 legacy v4 路径，该路径 `SELECT * FROM raw_capital_flow WHERE stock_id=? ORDER BY trade_date DESC LIMIT ?` **无 `is_estimated` 过滤**
- 若当日存在估算行（`is_estimated=1`），估算值将泄漏进降级路径评分 → **违反评分纯净红线**

### 第二处缺陷（inspect.stack 裸调用）

`data_collector.py` L1446-1448 的 019E 补采触发来源判断逻辑：

```python
import inspect as _inspect
_caller_file = _inspect.stack()[1].filename    # ← 裸调用，无 try-except
_trigger_source = '日报批次' if 'daily_report' in _caller_file else 'batch-analyze'
```

`inspect.stack()` 在极端环境（线程栈损坏 / C 扩展异常）可能抛 `IndexError` 或 `AttributeError`，导致整个 `fetch_capital_flow_batch` 崩溃 → 资金面采集全停。需加 try-except 保护块，降级为默认值 `'batch-analyze'`。

---

## 二、执行角色

**开发**（单人）

---

## 三、任务范围

> **改动极小，范围严格收敛：仅两个文件，各一处。**

### 任务 1：analysis_engine.py 评分纯净过滤（AF-1 核心）

**文件**：`modules/analysis_engine.py`
**函数**：`_read_capital_data(stock_id, limit=20)`（L123-137）
**改动**：SQL 补一行过滤条件 + docstring 追溯注释（M-2）

**改动前**（L123-130）：
```python
def _read_capital_data(stock_id, limit=20):
    """读取资金面数据（按日期升序）"""
    ...
    cursor.execute(
        """
        SELECT * FROM raw_capital_flow WHERE stock_id = ?
        ORDER BY trade_date DESC LIMIT ?
    """,
```

**改动后**：
```python
def _read_capital_data(stock_id, limit=20):
    """读取资金面数据（按日期升序）。
    019F：过滤估算行（is_estimated=1），确保评分仅使用真实数据（与 data_adapter/advisor 同源）。
    """
    ...
    cursor.execute(
        """
        SELECT * FROM raw_capital_flow WHERE stock_id = ?
        AND (is_estimated = 0 OR is_estimated IS NULL)
        ORDER BY trade_date DESC LIMIT ?
    """,
```

**约束**：
- 过滤条件必须与 `data_adapter.py` L282、`advisor.py` L1126 **完全一致**（同一表达式 `AND (is_estimated = 0 OR is_estimated IS NULL)`）
- 函数签名 `_read_capital_data(stock_id, limit=20)` 不变
- 不改动该函数其余任何代码

### 任务 2：data_collector.py inspect.stack 保护块

**文件**：`modules/data_collector.py`
**位置**：L1446-1448（`fetch_capital_flow_batch` 内，019E 补采触发来源判断）
**改动**：裸调用包裹 try-except，异常时降级为默认值

**改动前**（L1446-1448）：
```python
import inspect as _inspect
_caller_file = _inspect.stack()[1].filename
_trigger_source = '日报批次' if 'daily_report' in _caller_file else 'batch-analyze'
```

**改动后**：
```python
import inspect as _inspect
try:
    _caller_file = _inspect.stack()[1].filename
    _trigger_source = '日报批次' if 'daily_report' in _caller_file else 'batch-analyze'
except Exception:
    _trigger_source = 'batch-analyze'
```

**约束**：
- 仅包裹这三行，不影响周围任何逻辑
- 降级值 `'batch-analyze'` 是安全默认（batch-analyze 触发路径无特殊副作用）
- `import inspect as _inspect` 保留在 try 块上方（局部导入，沿用 019E 原有写法）

### 明确不改范围

- **`modules/data_adapter.py`** — 不碰（019E 已完成过滤）
- **`modules/advisor.py`** — 不碰（019E 已完成过滤）
- **`modules/scoring_engine.py`** — 不碰（评分读内存对象，过滤在上游 SQL 层）
- **`modules/daily_report.py`** — 不碰
- **`database/db_manager.py`** — 不碰（`is_estimated` 列已在 019E 迁移就位）
- **`templates/index.html`** — 不碰
- **`config_weights.json` / `config.py`** — 不碰
- **`requirements.txt`** — 不碰（维持 9 包）
- `analysis_engine.py` / `data_collector.py` 中除上述两处外的所有代码 — 不碰

---

## 四、验收标准

1. **代码级核查（PM 自验）**：
   - `analysis_engine.py` `_read_capital_data` SQL 含 `AND (is_estimated = 0 OR is_estimated IS NULL)`
   - `data_collector.py` L1446-1448 区段被 try-except 包裹，except 分支赋值 `_trigger_source = 'batch-analyze'`
   - 两处过滤表达式与 `data_adapter.py` L282 完全一致（grep 全文比对）
2. **编译验证**：`python -m py_compile modules/analysis_engine.py` + `python -m py_compile modules/data_collector.py` 均无错误
3. **评分纯净隔离验证**（QA 重点）：
   - 构造场景：当日写入 1 行 `is_estimated=1` 估算数据 + 1 行 `is_estimated=0` 真实数据
   - 调用 `analysis_engine._read_capital_data(stock_id)` → 断言返回行中**不含** `is_estimated=1` 的行
   - 调用 `data_adapter._read_capital_data(stock_id)` → 同断言（回归验证，确保 019E 过滤未被破坏）
4. **inspect.stack 保护验证**（QA 重点）：
   - mock `inspect.stack` 抛异常 → 断言 `_trigger_source` 降级为 `'batch-analyze'`，`fetch_capital_flow_batch` 不崩溃
5. **全仓 grep（评分链路）**：`is_estimated = 0 OR is_estimated IS NULL` 过滤表达式应覆盖 4 处**评分链路**读取入口（analysis_engine + data_adapter + advisor + data_collector 补采清单）

   **已知范围外项（本批次不处理，登记备查，源自架构评审 M-1）**：
   - `app.py` L770 `/api/capital` — 展示用，019E 设计估算值"仅供展示"故故意不过滤
   - `alert_engine.py` L201 连续净流出判定 — 预警用，存在误报风险（架构评审 R-1），建议后续批次评估
6. **零改动确认**：`scoring_engine.py`、`db_manager.py`、`index.html`、`config_weights.json`、`requirements.txt` 文件内容不变（QA 用 git diff 或文件哈希核查）

---

## 五、红线约束

1. **评分纯净红线**（最高优先级）：估算值在任何路径下不得进入评分/评级计算；本批次修复的第四处过滤点必须与前三处表达式完全一致
2. **范围红线**：改动仅限 `modules/analysis_engine.py`（1 处 SQL）+ `modules/data_collector.py`（1 处 try-except 块），其余文件一律不碰
3. **签名红线**：`_read_capital_data(stock_id, limit=20)` 签名不变；`fetch_capital_flow_batch` 签名不变
4. **零代码约束**：不引入新 pip 依赖（requirements.txt 维持 9 包）；无 schema 迁移（`is_estimated` 列已在 019E 就位）
5. **降级安全红线**：inspect.stack 异常时降级值必须为 `'batch-analyze'`（非空字符串，确保后续日志/逻辑安全）

---

## 六、执行顺序

```
Step 1: ✅ PM 签发 v1
Step 2: ✅ 架构师评审（有条件通过，M-1 文档措辞修正 + M-2 追溯注释）
Step 3: ✅ 监理批准
Step 4: ✅ 开发执行 + 自验
Step 5: ✅ QA 独立验收（9/9 PASS）→ ✅ PM+QA 双签 → ✅ 监理批准关闭（2026-08-04）
```

---

> **PM 备注**：本批次为 019E 验收阶段发现的遗留缺口修复（AF-1），改动极小（两文件各一处），但评分纯净红线相关，定为 P1。v2 已按架构评审 M-1（验收标准第 5 条措辞修正 + 范围外已知项登记）、M-2（docstring 追溯注释）修订定稿。开发注意：过滤表达式必须逐字符与 data_adapter L282 一致，不得自创变体。inspect.stack 保护块的 except 分支必须捕获 `Exception`（非 `BaseException`），避免吞掉 `KeyboardInterrupt`/`SystemExit`。
>
> **架构评审 R-1 备查**：`alert_engine.py` L201 预警缺 is_estimated 过滤（中风险，范围外），建议后续单独立项评估（如 019G）。
