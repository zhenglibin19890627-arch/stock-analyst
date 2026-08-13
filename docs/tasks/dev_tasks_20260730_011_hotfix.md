# DEV-TASKS-20260730-011-HOTFIX：011 时区Bug紧急修复

> **签发人**：PM  | **签发日期**：2026-07-30 | **状态**：待开发执行
> **关联**：QA验收报告 `reports/qa_accept_011_incremental_20260730.md`（BUG-1/2/3）

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：开发人员

**职责边界**：
- 按任务书要求修复 3 处时区 Bug
- 修复后执行自验（运行验证脚本确认门控生效）
- 交付自验报告 `reports/dev_selftest_011_hotfix_20260730.md`
- **不负责正式验收**（QA 独立复验）

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 开发仅做编码+自验，不做正式验收判定

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db`（在stock_analyst子目录内！） |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前8包） |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | Bug修复（3处1行改动） |
| 推荐模型 | **glm5.2 / qwen3.8**（并列优先） |
| 窗口类型 | **Quests 独立窗口** |
| 执行模式 | 单代理 agent |
| 预计耗时 | 10分钟（含自验） |
| 交付物 | 修复代码 + `reports/dev_selftest_011_hotfix_20260730.md` |

---

## 一、Bug 描述

QA 独立验收发现 3 个 Critical 级 Bug，**同一根因**：

`datetime.now(_CN_TZ)` 返回 tz-aware 对象，`datetime.strptime()` 返回 tz-naive 对象，两者直接相减抛出：
```
TypeError: can't subtract offset-naive and offset-aware datetimes
```

异常被外层 `except` 静默捕获，导致增量门控形同虚设。

---

## 二、修复清单（3处，每处1行改动）

### FIX-1：fetch_a_fundamental（A股基本面80天门控）

**文件**：`modules/data_collector.py` **L526**

**当前代码（BUG）**：
```python
days_since = (datetime.now(_CN_TZ) - datetime.strptime(last_report_date, '%Y-%m-%d')).days
```

**修复为**：
```python
days_since = (
    datetime.now(_CN_TZ).replace(tzinfo=None) - datetime.strptime(last_report_date, '%Y-%m-%d')
).days
```

### FIX-2：fetch_hk_fundamental（港股基本面80天门控）

**文件**：`modules/data_collector.py` **L874**

**当前代码（BUG）**：
```python
days_since = (datetime.now(_CN_TZ) - datetime.strptime(last_report_date, '%Y-%m-%d')).days
```

**修复为**：
```python
days_since = (
    datetime.now(_CN_TZ).replace(tzinfo=None) - datetime.strptime(last_report_date, '%Y-%m-%d')
).days
```

### FIX-3：fetch_margin_balance（融资余额增量补取）

**文件**：`modules/data_collector.py` **L2104**

**当前代码（BUG）**：
```python
today = datetime.now(_CN_TZ)
```

**修复为**：
```python
today = datetime.now(_CN_TZ).replace(tzinfo=None)
```

> 注：L2118 `(today - last_margin).days` 使用此 `today` 变量，修复 L2104 即可。

---

## 三、正确范例（参照）

`fetch_north_capital` L1951 已正确使用该模式：
```python
days_since = (datetime.now(_CN_TZ).replace(tzinfo=None) - last_fetch).days
```

同文件 L541 也正确使用了：
```python
hours_since = (datetime.now(_CN_TZ).replace(tzinfo=None) - last_fetch).total_seconds() / 3600
```

---

## 四、红线约束（修复时不可违反）

| 红线 | 说明 |
|---|---|
| 仅改3行 | 不得修改其他任何代码 |
| `fetch_capital_flow` 签名 | `(symbol, market)` 不可加 force_full 参数 |
| `advisor.py` generate_advice | 函数签名和函数体不可修改 |
| 011 增量逻辑 | 不得破坏其他增量跳过逻辑 |
| 零代码约束 | 无新 pip 依赖 |
| `data_collector.py` 三处 `if False` | 硬禁用不可修改 |

---

## 五、自验要求

修复后，开发需执行以下验证（写临时 .py 脚本执行）：

### V1：A股80天门控生效
```python
# 调用 fetch_a_fundamental('000333')（DB中 report_date=2026-07-15, <80天）
# 预期：返回含"跳过"字样，日志无 WARNING "增量检查异常"
```

### V2：港股80天门控生效
```python
# 调用 fetch_hk_fundamental('HK3690')（DB中 report_date=2026-07-22, <80天）
# 预期：返回 ('success', '同日跳过(港股财报X天内)')
```

### V3：融资余额增量正常
```python
# 调用 fetch_margin_balance('600276', 'a_stock')
# 预期：不抛 TypeError，正常返回 success/skipped
```

### V4：force_full 仍可绕过
```python
# 调用 fetch_a_fundamental('000333', force_full=True)
# 预期：全量采集成功（不受门控影响）
```

### 自验报告格式
交付 `reports/dev_selftest_011_hotfix_20260730.md`，包含 V1~V4 执行结果。

---

## 六、后续流程

```
开发修复+自验 → QA复验Q2/Q3(+港股/融资余额) → PM+QA双签 → 监理批准关闭011
```

QA 复验由 PM 另行签发复验任务书（预计15分钟）。

---

> **PM 备注**：本任务书已内嵌角色定义，监理可直接全文粘贴到 Quests 窗口执行。
