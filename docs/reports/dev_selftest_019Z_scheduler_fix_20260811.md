# 开发自测报告：019Z 调度器启动入口修复

> 批次：019Z ｜ 开发角色自测 ｜ 2026-08-11
> 任务书：`docs/tasks/dev_tasks_20260811_019Z_scheduler_fix.md`

---

## 一、改动说明

**文件**：`modules/daily_report.py`（唯一改动文件）
**函数**：`start_scheduler()` 函数体内（L246-253）

**改动前后对比**：

```python
# 改动前（原 L246-247）
    _schedule_next()
    _schedule_optimizer_next()  # M9: 启动每周优化定时器

# 改动后（现 L246-254）
    now = datetime.now(_CN_TZ)
    last_window_time = now.replace(hour=17, minute=10, second=0, microsecond=0)
    if now >= last_window_time:
        # 019Z: 今天三窗已全部结束（17:10之后启动），排到明天
        _schedule_next()
    else:
        # 019Z: 今天还有窗未到，注册今天的窗1（已过钟点由1秒补触发兜底）
        _register_capital_window(0)
    _schedule_optimizer_next()  # M9: 启动每周优化定时器
```

**语义**：17:10 及之后启动 → 排明天（旧行为，正确）；17:10 之前启动 → 注册今天窗1（16:10 前的正常等待，16:10~17:10 之间由 `_register_capital_window` 既有的"钟点已过1秒补触发"兜底串联）。

## 二、边界验证（mock 时间四场景，全 PASS）

mock 方式：替换 `daily_report.datetime` 的 `now()` 为模拟时钟（基准日 2026-08-11 周二），`threading.Timer` 替换为捕获型（不真启动线程），`_run_capital_window`/`_run_full_report_flow`/`_schedule_optimizer_next` 桩为 no-op；补触发链用手动触发 delay==1 的捕获 Timer 模拟。

```
===== S1 15:00 启动 =====
下次资金流采集窗1/3: 2026-08-11 16:10 (4200秒后)
✅ 每日报告定时调度器已启动（默认每日16:10，每周日20:00自动优化）
>>> 判定: PASS

===== S2 16:20 启动 =====
下次资金流采集窗1/3: 2026-08-11 16:10 (1秒后)
>>> 判定: PASS

===== S2 窗1补触发->注册窗2 =====
下次资金流采集窗2/3: 2026-08-11 16:40 (1200秒后)
>>> 判定: PASS

===== S3 16:50 启动 =====
下次资金流采集窗1/3: 2026-08-11 16:10 (1秒后)
>>> 判定: PASS

===== S3 窗1补触发->窗2补触发 =====
下次资金流采集窗2/3: 2026-08-11 16:40 (1秒后)
下次资金流采集窗3/3: 2026-08-11 17:10 (1200秒后)
>>> 判定: PASS

===== S4 18:00 启动 =====
下次定时报告: 2026-08-12 16:10 (79800秒后)
>>> 判定: PASS
```

| 场景 | 启动时间 | 分支 | 验证结果 |
|------|---------|------|---------|
| S1 | 15:00 | `_register_capital_window(0)` | 窗1/3 今天16:10，正常等待4200秒 ✓ |
| S2 | 16:20 | `_register_capital_window(0)` | 窗1已过→1秒补触发→注册窗2(16:40,1200秒) ✓ |
| S3 | 16:50 | `_register_capital_window(0)` | 窗1→1秒补触发，窗2→1秒补触发，窗3(17:10)等待 ✓ |
| S4 | 18:00 | `_schedule_next()` | 下次定时报告 明天16:10 ✓ |

> 另验证：17:10 整点启动（now==17:10:00）走 `_schedule_next()`（三窗已全过，与任务书边界一致）。

## 三、文件 mtime 锚点（红线核验用，git 不可用）

| 文件 | mtime |
|------|-------|
| `modules/daily_report.py`（改动） | 2026-08-11 19:39:18 |
| `modules/daily_report.py`（改动前） | 19:38 前（改动前 mtime，用于对照） |

## 四、回归确认（其他函数未改）

1. **构造性证明**：补丁采用字节级单点替换（暂存→回写），`Compare-Object` 对比改动前后全文件仅 9 行差异（8 新增 + 1 移除，`_schedule_next()`/`_schedule_optimizer_next()` 两行新旧相同被去重），其余 1191 行逐字节一致。
2. **定义锚点**（行号与改动前完全一致）：

```
L63:  def _scheduler_tick(window_idx=0):
L96:  def _run_capital_window(window_idx):
L135: def _register_capital_window(window_idx):
L157: def _run_full_report_flow():
L200: def _schedule_next():
L212: def start_scheduler():
```

3. `_scheduler_tick` / `_schedule_next` / `_register_capital_window` 函数体零改动（补丁仅触碰 start_scheduler 函数体内 L246-247 两行）。
4. `_CAPITAL_WINDOW_TIMES` / `_CAPITAL_WINDOW_COUNT` 常量未动（任务书红线第1、2条满足）。

## 五、回归测试

- `python -m py_compile modules/daily_report.py` → OK
- `ruff check modules/daily_report.py` → All checks passed
- `python -m pytest tests/ -q` → **355 passed, 1 warning in 1.67s**（与 019Y 基线一致，全绿）

## 六、红线合规自检

1. 只改 `start_scheduler()` 函数体内启动入口 ✓（仅 L246-253）
2. 未改 `daily_report.py` 以外文件 ✓
3. 未新增依赖 ✓

---

> 开发角色呈报，2026-08-11
> 状态：待监理/QA 验收
