# 开发窗口启动提示词：019Z 调度器启动入口修复（2026-08-11）

> 本文档供开发窗口启动时作为首条消息粘贴使用。
> 任务书：`docs/tasks/dev_tasks_20260811_019Z_scheduler_fix.md`（请先完整阅读）

---

## 一、你的角色与任务

你是 019Z 批次的**开发角色**。PM 已签发任务书，监理已批准。

这是一个**极小改动**：`daily_report.py` 的 `start_scheduler()` 函数体里，根据当前时间判断走 `_register_capital_window(0)`（今天还有窗）还是 `_schedule_next()`（今天三窗全过，排明天）。当前 bug 是无条件调 `_schedule_next()`，导致每次启动都排到明天，三窗永远不触发。

**任务书路径**：`docs/tasks/dev_tasks_20260811_019Z_scheduler_fix.md`

---

## 二、项目环境

- **项目路径**：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（路径含空格，PowerShell 须加引号）
- **Python**：`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`（3.12.9）
- **日志**：`logs\app.log`

---

## 三、Bug 定位（PM 已完成诊断，直接用）

**文件**：`modules/daily_report.py`
**函数**：`start_scheduler()`（L212-248）
**问题行**：L246 `_schedule_next()`

**关键代码（PM 已读源码确认）**：
- `start_scheduler()` L246：`_schedule_next()` ← **bug：无条件排到明天**
- `_schedule_next()` L200-209：`tomorrow = now.replace(...) + timedelta(days=1)` ← 语义是"次日"
- `_register_capital_window(0)` L135-154：注册今天窗1，有"钟点已过1秒补触发"兜底（L146-147）
- `_CAPITAL_WINDOW_TIMES` L60：`((16, 10), (16, 40), (17, 10))`
- `_scheduler_tick()` L63-80：三窗串联，窗3结束调 `_schedule_next()` 排次日（这个是对的）

---

## 四、修复方案（PM 已设计，开发确认实现）

`start_scheduler()` 中，用 L246 之前已有的 `now`（或新增获取），判断当前时间是否已过 17:10：

```python
now = datetime.now(_CN_TZ)
last_window = now.replace(hour=17, minute=10, second=0, microsecond=0)
if now >= last_window:
    _schedule_next()              # 三窗全过，排明天
else:
    _register_capital_window(0)   # 今天还有窗，注册窗1
```

**四种边界场景**（自测报告必须覆盖）：

| 启动时间 | 应走分支 | 预期日志 |
|---|---|---|
| 15:00 | `_register_capital_window(0)` | "下次资金流采集窗1/3: 今天 16:10" |
| 16:20 | `_register_capital_window(0)` | 窗1已过1秒补触发 → 注册窗2 |
| 16:50 | `_register_capital_window(0)` | 窗1窗2已过补触发 → 窗3等待 |
| 18:00 | `_schedule_next()` | "下次定时报告: 明天 16:10" |

---

## 五、开发纪律

1. **五步中转法**：Write 到工作区 → Copy-Item 回写 → Select-String 锚点核验 → DeleteFile 删临时 → 呈报
2. **git 不可用作红线核验** → 用文件 mtime
3. **PowerShell 编码坑**：中文输出 GBK 乱码但数值可读
4. **红线**：只改 `start_scheduler()` 函数体内，不碰其他函数

---

## 六、自测报告要求

1. 改动说明（函数、行号、前后对比）
2. 四种边界场景的 mock 时间验证日志
3. daily_report.py 的 mtime 锚点
4. 确认其他函数未改（`_scheduler_tick` / `_schedule_next` / `_register_capital_window`）

---

> 粘贴本文件全部内容到新窗口作为首条消息，开发角色即获得 019Z 完整上下文。
