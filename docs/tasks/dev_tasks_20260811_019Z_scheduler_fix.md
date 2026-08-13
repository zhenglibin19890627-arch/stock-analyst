# 开发任务书：019Z 调度器启动入口修复

> 签发：PM，2026-08-11
> 批次：019Z
> 前置：019X T2 三窗调度的启动入口 bug，2026-08-11 手动批次验证时发现

---

## 一、Bug 描述

### 根因

`daily_report.py` 的 `start_scheduler()`（L246）启动时调用了 `_schedule_next()`：

```python
# 现状（L246）
_schedule_next()
```

而 `_schedule_next()`（L200-204）的语义是**"窗3结束后注册次日窗1"**，硬编码了 `+ timedelta(days=1)`：

```python
def _schedule_next():
    """019X T2：窗3结束后注册次日窗1（16:10）"""
    tomorrow = now.replace(hour=16, minute=10, ...) + timedelta(days=1)  # 总是+1天
```

**后果**：每次 app.py 启动，调度器都把下次执行排到明天，今天的三窗（16:10/16:40/17:10）永远不会被注册。这就是为什么 8/11 三窗全部未触发。

### 大白话

闹钟 App 启动时，接的是"明天再响"按钮，而不是"今天第一次响"按钮。不管你几点开机，都从明天才开始响。

---

## 二、修复方案

### 核心改动

`start_scheduler()` 中，根据当前时间判断走哪个入口：

```python
# 修复后
now = datetime.now(_CN_TZ)
last_window_time = now.replace(hour=17, minute=10, second=0, microsecond=0)
if now >= last_window_time:
    # 今天三窗已全部结束（17:10之后启动），排到明天
    _schedule_next()
else:
    # 今天还有窗未到，注册今天的窗1
    _register_capital_window(0)
```

### 边界情况（必须处理）

| 启动时间 | 行为 |
|---|---|
| 16:10 之前 | `_register_capital_window(0)` → 窗1正常等待到16:10触发 |
| 16:10~16:40 之间 | 窗1时间已过 → 1秒补触发窗1 → 窗1注册窗2(16:40)正常等待 |
| 16:40~17:10 之间 | 窗1已过补触发 → 窗2已过补触发 → 窗3(17:10)正常等待 |
| 17:10 之后 | 三窗全过 → `_schedule_next()` 排明天（避免三窗连续补触发） |

### 不改动的部分

- `_schedule_next()` 函数本身不改（它被 `_scheduler_tick` 窗3结束后调用，语义正确）
- `_register_capital_window()` 不改（它的"钟点已过1秒补触发"逻辑是正确的）
- `_scheduler_tick()` 三窗串联逻辑不改
- `_CAPITAL_WINDOW_TIMES` / `_CAPITAL_WINDOW_COUNT` 常量不改

---

## 三、红线

1. **只改 `start_scheduler()` 函数体内的启动入口**，不改动调度器的其他任何函数
2. **不改 `daily_report.py` 以外的文件**
3. **不新增依赖**

---

## 四、自测报告要求（必须随回件提交）

1. **改动说明**：改动了哪个函数、改了哪几行、改动前后对比
2. **边界验证（4种场景）**：用 mock 时间模拟四种启动场景，验证调度器日志输出正确
   - 场景1：15:00 启动 → 应输出"下次资金流采集窗1/3: 今天 16:10"
   - 场景2：16:20 启动 → 应输出"窗1 补触发"日志
   - 场景3：16:50 启动 → 应输出窗1窗2补触发、窗3等待
   - 场景4：18:00 启动 → 应输出"下次定时报告: 明天 16:10"
3. **文件 mtime 锚点**：daily_report.py 改动后的 mtime
4. **回归确认**：确认 `_scheduler_tick` / `_schedule_next` / `_register_capital_window` 未被修改

---

## 五、验收标准

- app.py 在 16:10 前启动，日志出现"下次资金流采集窗1/3: 今天 16:10"（而非明天）
- app.py 在 17:10 后启动，日志出现"下次定时报告: 明天 16:10"（旧行为，正确）

---

> 签发：PM，2026-08-11
> 批次：019Z
> 状态：待开发
