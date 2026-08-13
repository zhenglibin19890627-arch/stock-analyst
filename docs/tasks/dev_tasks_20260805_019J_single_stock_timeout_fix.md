# 开发任务书 019J — 单只处理超时保护修复（报告线程 join 挂死缺陷 R-3）

**签发日期**：2026-08-05
**签发人**：PM
**批次编号**：019J
**优先级**：P1（超时保护形同虚设，存在报告线程永久挂死风险）
**关联批次**：019I（THS 批量预取超时保护，M-1 红线先例）
**架构评审**：✅ 通过（2026-08-05，无强制修订项；评审报告：`docs/reviews/review_019J_single_stock_timeout_fix_20260805.md`），已按 M-1（可选清理）/M-2（验收时序断言）/M-3（红线 9/10）并入修订定稿 v2

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：开发人员

**职责边界**：
- 按本任务书规格实现单只处理超时保护修复（方案甲 daemon 线程），完成编码+自验
- 不负责正式验收（QA 独立验收）
- 不修改红线区域（advisor.generate_advice、风控阈值、DB schema）
- 交付物：修改后的 `modules/daily_report.py` + 自验报告 `reports/dev_selftest_019J_single_stock_timeout_fix_20260805.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 开发人员仅做编码+自验，不执行正式验收
- 架构师评审结论未出前，本任务书为 v1；评审通过后 PM 会修订定稿 v2，开发以定稿为准

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
| 流程路径 | ✅PM 签发 v1 → ✅架构师评审（通过，M-1~M-3 并入 v2） → ✅监理批准 v2（2026-08-05） → ✅开发执行+自验（30/30 PASS） → ✅QA 独立验收（9/9 用例 48/48 断言 10/10 红线） → ✅PM+QA 双签（2026-08-05） → ✅监理批准关闭（2026-08-05） |

---

## 一、背景

### 1.1 缺陷现象

每日报告批次中，单只股票处理 90 秒超时后，报告生成线程并未立即跳过继续，而是**被阻塞等待超时的 worker 线程完成**，阻塞窗口可达数分钟；若 worker 线程永久挂死（如无超时的 socket 调用），报告线程将**永久卡死**，整批报告无法完成。

### 1.2 日志实证（2026-08-05 16:14 批次）

```
16:21:51  [日报进度] 1/29 开始 600276 恒瑞医药
16:23:21  [日报进度] 600276 超时(90s)，跳过          ← 日志已宣布跳过
          ← 日志空白 5 分 20 秒（主线程被 shutdown(wait=True) join 挂死）
16:28:41  worker 线程自行完成 600276（评分 63.1 持有观望）→ 主线程才恢复
16:28:42  [日报进度] 2/29 开始 HK3690                 ← 迟滞 5 分 20 秒
```

```
16:29:10  [日报进度] 4/29 开始 000333 美的集团
16:30:40  [日报进度] 000333 超时(90s)，跳过
          ← 日志空白 8 分 30 秒
16:39:10  [日报进度] 5/29 开始 002352 顺丰控股        ← 迟滞 8 分 30 秒
```

同日批次结果：**成功13/失败16 耗时1845s**（1800s 整体预算耗尽截断，剩余 13 只未处理）。

### 1.3 根因分析

`modules/daily_report.py` L531-570 单只超时控制使用 `with ThreadPoolExecutor(max_workers=1)` 块：

```python
with ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(_process_single_stock, stock, target_date, force, report_type)
    try:
        result = future.result(timeout=STOCK_TIMEOUT_SECONDS)
    except FuturesTimeout:
        ...
        continue          # ← L570，在 with 块内部
```

**缺陷机制**：`future.result(timeout=90)` 抛 TimeoutError 后，代码虽已写入 failed 记录并 `continue`，但 `continue` 位于 `with` 块内部——with 块退出时 `__exit__` 调用 `shutdown(wait=True)`，**join 仍在运行的 worker 线程**，主线程阻塞至 worker 自行完成。超时保护形同虚设。

**与 019I M-1 同款缺陷**：019I 架构师经运行时实验实证——`with ThreadPoolExecutor` 块的 `shutdown(wait=True)` 会 join 挂死 worker；已在 019I 红线 8"先例红线"中登记：daily_report.py L533 该模式自身携带同一缺陷（R-3）。本批次即为该登记的正式修复。

### 1.4 现有超时保护覆盖情况（修复后期望）

| 调用点 | 现状 | 修复后 |
|---|---|---|
| 单只股票处理 | ⚠️ `ThreadPoolExecutor + future.result(timeout=90)`，with 块退出 join 挂死线程 | ✅ daemon 线程 + `join(timeout=90)`，超时立即继续 |
| 批次整体循环 | ✅ `BATCH_TIMEOUT_SECONDS=1800s` 软超时检查（循环内部，不受影响） | 不变 |
| THS 批量预取 | ✅ 019I 已修复（daemon 线程 + `join(timeout=60)`） | 不变 |

---

## 二、执行角色

**开发**（单人）

---

## 三、任务范围

> **改动范围收敛：仅 `modules/daily_report.py` 一个文件，L531-570 单只超时控制块。**

### 任务 1：单只超时控制改用 daemon 线程 + join(timeout)（方案甲）

**文件**：`modules/daily_report.py`
**位置**：L531-570（单只超时控制块）
**改动**：将 `with ThreadPoolExecutor(max_workers=1)` 块替换为 daemon 线程 `threading.Thread(daemon=True) + t.join(timeout=STOCK_TIMEOUT_SECONDS)`，超时后立即写 failed 记录并 `continue`，**严禁等待/join 超时 worker**。

> **M-1 红线（019I 经验教训，本批次绝对禁止）**：任何"超时保护"若用 `with ThreadPoolExecutor` 块实现均属无效修复——with 块退出时 `__exit__` 调用 `shutdown(wait=True)` 会 join 挂死 worker 线程。正确模式：daemon 线程 `Thread(daemon=True) + t.join(timeout=N)`，超时后立即返回且不阻塞进程退出。

**预期实现要点**（最终实现风格以开发为准，但必须满足以下语义）：

```python
# 单只超时控制（019J：daemon 线程方案，替代 with ThreadPoolExecutor）
box = {'exc': None}

def _run():
    try:
        box['r'] = _process_single_stock(stock, target_date, force, report_type)
    except Exception as e:
        box['exc'] = e

t = threading.Thread(target=_run, daemon=True)
t.start()
t.join(timeout=STOCK_TIMEOUT_SECONDS)
if t.is_alive():
    # 超时：写 failed 记录 + results.append + continue，不等待 worker（worker 迟到完成会自愈覆盖记录）
    ...
if box.get('exc') is not None:
    raise box['exc']          # 保持现有异常路径语义（外层 except 处理）
result = box['r']
```

**语义要求**：
1. **超时路径**：`t.is_alive()` 为 True → 与现状完全一致的 failed 记录（`_save_report(status='failed', error_msg='采集超时(90s)')`）+ `results.append(failed)` + `continue`，**不 join、不等待 worker**
2. **异常路径**：worker 线程内异常须捕获存入 box，外层按现状异常处理（fail_count += 1 + failed 记录）——线程内异常不会自动传播，必须显式捕获（否则超时判定会误判）
3. **成功路径**：`box['r']` 按现状处理成功结果（reuse/fallback/success_count 等逻辑不变）
4. **worker 迟到自愈（保持现有行为）**：超时后 worker 线程继续在后台运行（daemon），最终完成时 `_process_single_stock` 内部 L410 `_save_report`（DELETE+INSERT）会**覆盖 failed 记录为 ok 记录**——这是 08-05 实证的现有行为（id 1043 failed → id 1044 ok），须保留（数据自愈，不丢分）
5. `threading` 已在模块顶部 L25 import，直接使用
6. 每次超时产生一个后台 daemon 线程（挂死后不回收），批次内最多 29 个，可接受（019I 已同款评估）

**明确不改范围**：

- `_process_single_stock()`（L323-439）— 函数体零改动（仅调用方式从 executor.submit 变为 daemon 线程）
- 批次整体超时软检查（L504-511）— 不碰
- 成功结果处理（L572-596）— 不碰
- 外层异常处理（L598-632）— 不碰
- `_save_report()` — 不碰
- `modules/data_collector.py` — 不碰
- `modules/data_adapter.py` / `modules/advisor.py` / `modules/analysis_engine.py` / `modules/alert_engine.py` — 不碰
- `app.py` / `config.py` / `templates/index.html` — 不碰
- `database/db_manager.py` — 不碰
- `requirements.txt` — 不碰（threading 是 Python 标准库，无新依赖）

**M-1（建议，可选，不阻塞不纳入验收）— 未使用 import 清理**：修复后 `daily_report.py` L27-28 `from concurrent.futures import ThreadPoolExecutor` / `TimeoutError as FuturesTimeout` 不再使用。开发可顺手删除（同文件内无害微改）；保留亦无害（不影响运行，ruff F401 属 lint 级提示）。**两者均可，不纳入验收。**

---

## 四、验收标准

1. **代码级核查（PM 独立核验）**：
   - `daily_report.py` L531 区域 **`with ThreadPoolExecutor` 写法为 0 处**（M-1 红线，grep 核验）
   - 存在 `daemon=True` 的 `threading.Thread` 创建 + `t.join(timeout=STOCK_TIMEOUT_SECONDS)`
   - 超时分支（`t.is_alive()`）与现状 failed 记录逻辑一致（error_msg=`采集超时(90s)`）
   - 线程内异常有显式捕获（box['exc'] 模式或等效）
   - `_process_single_stock` 函数体 diff 为空
2. **编译验证**：`python -m py_compile modules/daily_report.py` 无错误
3. **超时保护验证（QA 重点）**：
   - mock `_process_single_stock` 为 `time.sleep(120)` → 断言单只处理在 **95 秒内**返回并进入下一只（含显式耗时断言——可捕获 with 块缺陷：with 块版本需等 mock 挂死 120s 才返回，耗时断言失败）
   - 断言超时后写入 failed 记录（status='failed'，error_msg 含"超时"）、`results` 含 failed 条目、`fail_count` 正确
4. **worker 迟到自愈验证（QA 重点，M-2 细化时序断言）**：
   - **分两次查库（显式时序）**：① 超时返回后（≤95s，worker 仍在 sleep）立即查库 → 断言 failed 记录存在（error_msg 含"超时"）——验证 failed 中间态；② 等待 ≥120s（mock sleep 完成后）再查库 → 断言被覆盖为 ok、同日同股仅 1 条记录——验证 ok 终态
   - 断言后台 worker 完成后数据库记录被覆盖为 ok（failed → ok，同 stock_id 同日仅 1 条，无重复）
5. **异常路径验证**：mock `_process_single_stock` 抛异常（sleep 10s 后 raise）→ 断言按现状异常处理（fail_count+1、failed 记录 error_msg 为异常文案**非**"采集超时"、批次继续）
6. **成功路径回归验证**：mock 正常返回 → 断言成功处理逻辑不变（success_count、reuse、fallback、results ok 条目）
7. **进程退出验证（019I M-4 同款）**：挂死 worker 存活期间退出进程 → 断言进程在合理时间（如 10s）内退出（daemon 线程自动满足，不阻塞进程退出）
8. **零改动确认**：除 `daily_report.py` 外所有文件哈希不变；`_process_single_stock` 函数体 diff 为空
9. **M-2 可选补充断言（QA 可酌情执行）**：
   - report_type 隔离回归：intraday 自愈覆盖不删除 daily 行（`_save_report` L257-267 行为回归）
   - 线程属性断言：超时后 `threading.enumerate()` 中断言新增线程 `daemon=True`（验证红线 8 属性级保证）

---

## 五、红线约束

1. **功能红线**：修复后单只超时时报告线程必须**立即**跳过继续（不得阻塞至 worker 完成）；worker 永久挂死时报告线程不得被拖死
2. **M-1 红线（最高优先级）**：**严禁 `with ThreadPoolExecutor` 写法**（019I 运行时实验实证 `shutdown(wait=True)` join 挂死线程，修复无效）；必须 daemon 线程 `join(timeout)` 或等效不等待方案
3. **范围红线**：改动仅限 `modules/daily_report.py` 的 L531-570 单只超时控制块，其余文件一律不碰
4. **语义红线**：超时路径与现状一致（failed 记录 + results.append + continue）；异常路径与现状一致（外层 except 处理）；成功路径处理逻辑零改动
5. **签名红线**：`_process_single_stock()` 签名和函数体不变
6. **零代码约束**：不引入新 pip 依赖（threading 是标准库）；不改 config.py；不改 DB schema
7. **数据自愈红线**：不得阻止后台 worker 迟到完成时的 ok 记录覆盖（现有 DELETE+INSERT 行为保留，防丢分）
8. **进程退出红线**：挂死 worker 存活期间进程退出不得被阻塞（daemon=True 保证）
9. **语义隔离红线（M-3）**：超时分支 failed 记录 error_msg 必须为 `采集超时(90s)`（L557 现状），异常路径不得复用该文案——QA 按 error_msg 区分两类失败断言
10. **worker 不可阻断红线（M-3）**：超时分支严禁对 worker 追加任何 join/等待/终止调用；不得取消 worker 迟到写库

---

## 六、执行顺序

```
Step 1: ✅ PM 签发 v1
Step 2: ✅ 架构师评审（2026-08-05 通过，无强制修订项；建议性 M-1~M-3 已并入 v2）
Step 3: ✅ 监理批准 v2（2026-08-05）
Step 4: ✅ 开发执行 + 自验（2026-08-05，30/30 PASS；M-1 import 清理已执行）
Step 5: ✅ QA 独立验收（2026-08-05，9/9 用例 48/48 断言 10/10 红线）→ ✅ PM+QA 双签（2026-08-05）→ ✅ 监理批准关闭（2026-08-05）
```

---

## 七、PM 备注

1. **立项来源**：监理批复 019J 立项（R-3 修复）。R-3 为 019I 架构评审登记的"先例红线"缺陷：daily_report.py L533 `with ThreadPoolExecutor` 块携带 join 挂死缺陷，019I 红线 8 明确"下一批次修复时严禁复制该模式"。2026-08-05 16:14 批次实证：600276 阻塞 5 分 20 秒、000333 阻塞 8 分 30 秒，批次 1845s 耗尽 1800s 整体预算被截断（成功13/失败16，剩余 13 只未处理）。
2. **方案选择**：沿用 019I 已实证有效的方案甲（daemon 线程 + join(timeout)）。019I 中方案乙（TPE + shutdown(wait=False)）因 TPE 工作线程非 daemon、挂死线程阻塞进程退出（R-1）而不推荐，本批次不采用。
3. **worker 迟到自愈是特性非缺陷**：08-05 实证 600276 超时后 worker 仍于 16:28:41 完成评分并覆盖写入 ok 记录——报告数据未丢。修复后该行为保留（daemon 线程后台继续），QA 须验证覆盖写入后同 stock 同日仅 1 条记录。
4. **R-2 备查（不在本批次范围）**：EM 逐只回退 `_em_batch_collect` 单只调用无超时包装（019I 架构评审登记，中风险）。本批次仅修复 R-3；R-2 是否纳入由架构师评审裁定。
5. **展示层备查（已结案）**：app.py L770 / index.html 展示层 is_estimated 标注维持现状（019E 裁定、019H M-1 再次确认）。
6. **export_engine 备查（未立项）**：export_engine.py L278-285 导出层缺 is_estimated 过滤（低风险），不在本批次范围。
7. **今日批次数据状态**：16:52:37 批次已结束（成功13/失败16），`reports/2026-08-05.md` 已生成，DB 今日 18 条 ok 记录（含 worker 自愈覆盖的 600276/000333）。
8. **v2 修订说明（2026-08-05，架构评审通过后并入）**：
   - **评审结论**：✅ 通过，无强制修订项。架构师独立核验确认根因（with 块对照实验：5.01s 超时 → 120s 才退出）、方案语义（box 模式本机 4 例实验全 PASS）、自愈行为（DB id 1044/1048 实证）、R-2 维持登记。
   - **M-1（建议，可选）**：L27-28 未使用 import 清理，不阻塞不纳入验收。
   - **M-2（建议，QA 执行细则）**：验收标准 4 补充"分两次查库"时序断言（failed 中间态 → ok 终态）；可选 report_type 隔离回归 + `threading.enumerate()` daemon 属性断言（已并入验收标准 9）。
   - **M-3（建议，红线补充）**：红线 9（语义隔离：超时 error_msg 固定 `采集超时(90s)`）+ 红线 10（worker 不可阻断：严禁 join/等待/终止）。
   - **架构师新登记风险（本批次不处理，接受观察）**：R-6（低，迟到 worker 跨批次覆盖）、R-7（低，超时边界极窄竞态 failed 覆盖 ok）、R-8（低，box 模式吞 BaseException 子类）——均评估"接受，不追加防护"。
