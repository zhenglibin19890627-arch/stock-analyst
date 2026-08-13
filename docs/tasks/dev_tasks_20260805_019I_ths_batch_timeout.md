# 开发任务书 019I — THS 批量预取超时保护（报告生成卡住修复）

**签发日期**：2026-08-05
**签发人**：PM
**批次编号**：019I
**优先级**：P1（报告生成功能完全阻断）
**关联批次**：019C（EM 回退优化）、019G（THS 周末跳过）
**架构评审**：✅ 有条件通过（评审报告：`docs/reviews/review_019I_ths_batch_timeout_20260805.md`），已按 M-1（强制）/M-2（建议）/M-3（文档）/M-4（验收）修订定稿 v2

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：开发人员

**职责边界**：
- 按本任务书规格实现 THS 批量预取超时保护，完成编码+自验
- 不负责正式验收（QA 独立验收）
- 不修改 daily_report.py / app.py / config.py / 评分引擎 / 红线区域
- 交付物：修改后的 `modules/data_collector.py` + 自验报告 `reports/dev_selftest_019I_ths_batch_timeout_20260805.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 开发人员仅做编码+自验，不执行正式验收
- 架构师已评审通过（有条件），请严格按 v2 修订后的方案甲（daemon 线程）实现

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格，命令行需引号） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db` |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包） |
| PowerShell | 不支持 `&&`，用 `;` 代替；含 f-string 的 Python 代码须写临时 .py 文件执行 |

### 环境约束（硬性，违反将导致执行失败）
1. **项目在 IDE 工区外**：路径含空格，Write 工具直写会报错，须 "工作区 Copy + SearchReplace 编辑 + Copy-Item 覆盖回项目目录"
2. **PowerShell 中文**：追加中文到文件用 `[System.IO.File]::ReadAllText + WriteAllText`（UTF-8），禁止 Add-Content/Out-File（乱码）
3. **PowerShell 内联 Python**：含 `*` 的 SQL 会被通配符解析破坏，须用 `chr(39)` 包裹字符串或写临时 .py 脚本
4. **用户是零代码用户**：所有技术决策以"零代码用户可独立运行"为最高优先级

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 流程路径 | ✅PM 签发 v1 → ✅架构师评审（有条件通过，M-1~M-4 已修订 v2） → ✅监理批准 v2 → ✅开发执行+自验 → ✅QA 独立验收（7/7 PASS） → ✅PM+QA 双签 → ✅监理批准关闭（2026-08-05） |

---

## 一、背景

### 1.1 缺陷现象

用户反馈**每次生成今日报告或盘中报告都会卡住**，页面长时间无响应。

### 1.2 日志实证

**2026-08-05 app.log L186-188**（卡住实例）：
```
11:46:28  开始生成每日报告 date=2026-08-05
11:46:28  [同花顺批量] 请求 stock_fund_flow_individual()（全市场资金流向）...
          ← 日志空白 18+ 分钟，报告生成线程完全阻塞
12:04:45  (仅其他 API 请求，报告线程仍卡着)
```

**对比 2026-08-03 app.log.2026-08-03 L352-356**（正常实例）：
```
11:50:50  开始生成每日报告 date=2026-08-03
11:50:50  [同花顺批量] 请求 stock_fund_flow_individual()...
11:50:57  [同花顺批量] 获取成功: 5197 只  ← 仅 7 秒
11:50:57  [日报进度] 1/29 开始 600276 恒瑞医药  ← 正常进入循环
```

### 1.3 根因分析

**`ak.stock_fund_flow_individual()` 调用无超时保护，当同花顺服务器不响应时无限阻塞。**

同步阻塞链路：
```
前端 POST /api/daily-report/generate
  → app.py L3212: generate_daily_report()              ← 同步阻塞
  → daily_report.py L479: fetch_capital_flow_batch()   ← 循环前批量预取，无超时包装
  → data_collector.py L1372: _fetch_capital_flow_ths_batch()  ← 无超时包装
  → data_collector.py L1175: ak.stock_fund_flow_individual()  ← akshare HTTP 无 timeout，HANG 死
```

### 1.4 现有超时保护覆盖情况

| 调用点 | 超时保护 | 说明 |
|---|---|---|
| 单只股票处理 | ✅ `STOCK_TIMEOUT_SECONDS=90s` | `ThreadPoolExecutor + future.result(timeout=90)` |
| 批次整体循环 | ✅ `BATCH_TIMEOUT_SECONDS=1800s` | 但检查点在**循环内部**，批量预取在循环**之前** |
| **THS 批量预取** | ❌ **无任何超时** | **裸调用 ak 接口，hang 住时无限等待** |

### 1.5 为什么"每次"都卡

1. THS 缓存 `_THS_CAPITAL_CACHE`（1 小时 TTL）只在**成功获取后**才写入。首次 hang → 缓存为空 → 下次仍需请求 → 仍 hang。
2. 连续失败降级 `_THS_CONSECUTIVE_FAIL_COUNT`（阈值 3）只在接口**抛异常返回 None** 时 +1。但 hang 住（TCP 连接保持但无数据返回，不抛异常）时**永远不会触发**降级计数。
3. 结果：报告生成线程从批量预取步骤开始永久阻塞，前端请求无法返回。

---

## 二、执行角色

**开发**（单人）

---

## 三、任务范围

> **改动范围收敛：仅 `modules/data_collector.py` 一个文件，一个函数。**

### 任务 1：`_fetch_capital_flow_ths_batch()` 超时包装

**文件**：`modules/data_collector.py`
**函数**：`_fetch_capital_flow_ths_batch()`（L1120-1168）
**改动**：用 daemon 线程 `threading.Thread(daemon=True) + t.join(timeout=N)` 包装 THS 主接口和备选接口调用，超时后视为失败，正常走降级链路

> **M-1 修正说明（架构师强制修订）**：v1 原案使用 `with ThreadPoolExecutor` 块，经架构师本机运行时实验实证——`with` 块退出时 `__exit__` 调用 `shutdown(wait=True)` 会 **join 挂死的 worker 线程**，导致函数仍永久阻塞（修复无效）。PM 已独立复现该实验确认。v2 改用 daemon 线程方案（架构师推荐方案甲），彻底消除阻塞。

**新增常量**（L1094-1098 区域，THS 常量块内追加）：
```python
_THS_REQUEST_TIMEOUT = 60  # 019I：单次 THS 接口请求超时（秒）
```

**改动前**（L1144-1156 核心逻辑）：
```python
    # FIX-B：主接口 stock_fund_flow_individual()
    df = _try_ths_primary()

    # FIX-B：主接口失败时重试1次（间隔5秒）
    if df is None:
        logger.info('[同花顺批量] 主接口失败，5秒后重试1次...')
        time.sleep(5)
        df = _try_ths_primary()

    # FIX-B：重试仍失败时，尝试备选接口 stock_individual_fund_flow_rank()
    if df is None:
        logger.info('[同花顺批量] 重试仍失败，尝试备选接口 stock_individual_fund_flow_rank()...')
        df = _try_ths_rank_backup()
```

**改动后**（v2，M-1 daemon 线程方案甲——架构师推荐）：
```python
    # 019I：THS 接口调用增加超时保护，防止服务器不响应时无限阻塞
    # M-1 修正：禁止使用 with ThreadPoolExecutor（with 退出时 shutdown(wait=True)
    #          会 join 挂死线程，修复无效——经架构师运行时实验 + PM 独立复现确认）
    # 改用 daemon 线程 join(timeout) 模式：
    #   - daemon 线程不参与解释器退出 join，进程退出不被阻塞（R-1 消除）
    #   - t.join(timeout=N) 超时后立即返回，不等待挂死线程
    import threading as _threading_019I

    def _call_with_timeout(fn, label):
        """019I：daemon 线程包装 THS 接口调用，超时返回 None"""
        box = {}
        t = _threading_019I.Thread(
            target=lambda: box.update(r=fn()),
            daemon=True,
        )
        t.start()
        t.join(timeout=_THS_REQUEST_TIMEOUT)
        if t.is_alive():
            logger.warning(f'[同花顺批量] {label} 超时({_THS_REQUEST_TIMEOUT}s)，跳过')
            return None
        return box.get('r')

    # FIX-B：主接口 stock_fund_flow_individual()
    df = _call_with_timeout(_try_ths_primary, '主接口')

    # FIX-B：主接口失败时重试1次（间隔5秒）
    if df is None:
        logger.info('[同花顺批量] 主接口失败，5秒后重试1次...')
        time.sleep(5)
        df = _call_with_timeout(_try_ths_primary, '主接口(重试)')

    # FIX-B：重试仍失败时，尝试备选接口 stock_individual_fund_flow_rank()
    if df is None:
        logger.info('[同花顺批量] 重试仍失败，尝试备选接口 stock_individual_fund_flow_rank()...')
        df = _call_with_timeout(_try_ths_rank_backup, '备选接口')
```

> **备选实现（方案乙，TPE + shutdown(wait=False)，不推荐）**：若开发评估后选择此方案，须显式 `pool = _Pool(max_workers=1)` + `finally: pool.shutdown(wait=False)`，**严禁 `with` 块**。但 TPE 工作线程非 daemon（Python 3.12.9 实证 `daemon=False`），挂死线程会阻塞进程退出（R-1 存续），须额外通过 M-4 进程退出验收。**PM 推荐方案甲。**

**约束**：
- 超时常量 `_THS_REQUEST_TIMEOUT = 60` 放在 THS 常量块（L1094-1098 区域），不放在 config.py（避免用户可见配置膨胀，THS 内部实现细节）
- `_call_with_timeout` 作为 `_fetch_capital_flow_ths_batch` 的内部辅助函数定义（不暴露为模块级函数）
- 超时后必须返回 `None`（与现有失败路径完全一致），确保后续降级链路（缓存检查、失败计数、EM 回退）不受影响
- `_try_ths_primary()` 和 `_try_ths_rank_backup()` 函数本身**不改动**（仅调用方式变化）
- `import threading` 导入位置：方案甲使用 daemon 线程，需 `import threading`（已在 data_collector.py 模块顶部导入，函数内直接使用 `threading.Thread` 即可，无需函数内重复 import）（M-3 更正：v1 原称"与 daily_report.py L533 使用模式一致"有误——daily_report.py 在**模块顶部** L27 import，使用在 L533）
- 60 秒超时依据：正常获取约 7 秒（8/3 日志），留 8 倍余量；超过 60 秒基本可判定为 hang

### 明确不改范围

- **`modules/daily_report.py`** — 不碰（已有单只超时 + 批次超时保护）
- **`modules/data_adapter.py`** — 不碰
- **`modules/advisor.py`** — 不碰
- **`modules/analysis_engine.py`** — 不碰
- **`modules/alert_engine.py`** — 不碰
- **`app.py`** — 不碰（API 路由层不改动）
- **`config.py`** — 不碰（超时常量放 data_collector 内部）
- **`templates/index.html`** — 不碰
- **`database/db_manager.py`** — 不碰
- **`requirements.txt`** — 不碰（`concurrent.futures` 是 Python 标准库，无新依赖）
- **`data_collector.py` 中除 `_fetch_capital_flow_ths_batch` 外的所有代码** — 不碰

---

## 四、验收标准

1. **代码级核查（PM 独立核验）**：
   - `_THS_REQUEST_TIMEOUT = 60` 存在于 THS 常量块（L1098 后）
   - 主/重试/备选 3 处调用均经 `_call_with_timeout` 包装
   - **无 `with ThreadPoolExecutor` 写法**（grep `_fetch_capital_flow_ths_batch` 函数体内为 0 处——M-1 红线）
   - 超时路径返回 None；`_try_ths_primary`/`_try_ths_rank_backup` 函数体零改动
2. **编译验证**：`python -m py_compile modules/data_collector.py` 无错误
3. **超时保护验证（QA 重点，M-4 强化）**：
   - mock `_try_ths_primary` 为 `time.sleep(120)` → 断言 `_fetch_capital_flow_ths_batch` 在 **65 秒内**返回 `None`（含显式耗时断言——可捕获 M-1 with 块缺陷：with 块版本需等 mock 挂死时长 120s 才返回，耗时断言失败）
   - 断言日志含 `[同花顺批量] 主接口 超时(60s)，跳过`
4. **降级链路完整性验证（QA 重点）**：
   - 主接口超时 → 5 秒等待 + 重试 → 备选 → 全失败 `_THS_CONSECUTIVE_FAIL_COUNT += 1` → 返回 None → 调用方回退 `_em_batch_collect`（全部按序断言）
   - 断言超时后 `_THS_CAPITAL_CACHE['data']` 仍为 None（红线 7）
5. **正常路径回归验证**：
   - mock 正常返回 DataFrame → 透明传递无额外开销；成功时失败计数重置、缓存写入
6. **进程退出验证（M-4，方案乙必选 / 方案甲自动满足）**：
   - 挂死线程存活期间退出进程 → 断言进程在合理时间（如 10s）内退出（方案甲 daemon 线程自动满足）
7. **零改动确认**：`daily_report.py`、`app.py`、`config.py`、`requirements.txt`、`index.html` 文件哈希不变；`_try_ths_primary`/`_try_ths_rank_backup` 函数体 diff 为空

---

## 五、红线约束

1. **功能红线**：修复后报告生成（今日报告 + 盘中报告）在 THS 服务器不响应时**不得永久阻塞**，必须在约 60 秒内降级到 EM 回退
2. **范围红线**：改动仅限 `modules/data_collector.py` 的 `_fetch_capital_flow_ths_batch()` 函数 + THS 常量块新增一个常量，其余文件一律不碰
3. **签名红线**：`_fetch_capital_flow_ths_batch()` 签名不变（无参）；`_try_ths_primary()` / `_try_ths_rank_backup()` 签名和函数体不变
4. **零代码约束**：不引入新 pip 依赖（`concurrent.futures` 是 Python 标准库）；不改动 config.py；不改动数据库 schema
5. **降级安全红线**：超时后必须返回 `None`（与现有失败路径完全一致），后续缓存检查 / 失败计数 / EM 回退逻辑不得受影响
6. **挂死线程红线（M-1 补充）**：超时路径严禁 join/等待挂死线程——**禁止 `with ThreadPoolExecutor` 写法**（实验实证 `shutdown(wait=True)` join 挂死线程，修复无效）；必须 daemon 线程 join 或显式 `shutdown(wait=False)`
7. **缓存红线（M-1 补充）**：超时/失败路径不得写入 `_THS_CAPITAL_CACHE`（现有结构保证，列入验收断言）
8. **先例红线（R-3 补充）**：不得复制 `daily_report.py` L533 的 `with ThreadPoolExecutor` 模式（该模式自身携带同一 join 挂死缺陷，属潜伏缺陷）

---

## 六、执行顺序

```
Step 1: ✅ PM 签发 v1
Step 2: ✅ 架构师评审（有条件通过，M-1~M-4 + R-1~R-5）
Step 3: ✅ PM 按 M-1~M-4 修订任务书 v2
Step 4: ✅ 监理批准 v2
Step 5: ✅ 开发执行 + 自验（11/11 PASS）
Step 6: ✅ QA 独立验收（7/7 用例，15/15 断言，8/8 红线） → ✅ PM+QA 双签 → ✅ 监理批准关闭（2026-08-05）
```

---

## 七、PM 备注

1. **根因来源**：监理指示"检查日志，找下每次生成今日报告或盘中报告都会卡住的原因"，PM 通过 app.log 日志分析定位到 THS 批量预取无超时保护的根因，监理确认后立项。
2. **设计选择说明**：超时常量放在 `data_collector.py` 而非 `config.py`，原因是 THS 超时是内部实现细节，零代码用户不需要在 config.py 中看到和调整此值。如架构师认为应放 config.py 统一管理，PM 接受调整。
3. **60 秒超时依据**：正常获取约 7 秒（8/3 日志实证），60 秒留约 8 倍余量。过短可能误杀网络波动，过长则用户等待体验差（报告生成页面卡 60 秒已是不佳体验，但远好于无限卡住）。架构师可酌情调整。
4. **备查项 M-2 不在本批次范围**：export_engine.py 导出层缺 is_estimated 过滤（低风险备查项），不影响报告生成卡住问题，不在 019I 处理。
5. **进程状态提醒**：当前运行 PID 46172（非载明的 30488），开发执行前需确认代码版本基线。
6. **v2 修订说明（M-1~M-4）**：
   - **M-1（强制）**：v1 的 `with ThreadPoolExecutor` 写法经架构师运行时实验实证为无效修复（with 退出时 `shutdown(wait=True)` join 挂死线程）。PM 独立复现实验确认。v2 改用 daemon 线程 join 方案（方案甲）。
   - **M-2（建议，非强制）**：架构师建议超时后跳过主接口重试（THS 阶段上界 185s→120s），PM 评定为非阻塞建议项，开发可自行评估是否实现。
   - **M-3（文档）**：v1 约束中"import 位置与 daily_report.py L533 一致"表述有误，已更正。
   - **M-4（验收）**：验收标准第 3 条补充显式耗时断言（65s），新增进程退出验证（第 6 条）。
7. **架构师登记的风险项（本批次不处理）**：
   - **R-2（中）**：EM 逐只回退循环存在同类挂死窗口（`_em_batch_collect` 单只调用挂死击穿 600s 软超时），登记 019J 候选。
   - **R-3（中）**：`daily_report.py` L533 的 `with ThreadPoolExecutor` 先例携带同一 join 挂死缺陷（单只处理超时保护形同虚设），登记后续批次。
   - R-4/R-5 为低风险文档/语义注记，不影响开发。
