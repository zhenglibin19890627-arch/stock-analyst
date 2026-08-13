# QA 验收报告 019J — 单只处理超时保护修复（报告线程 join 挂死缺陷 R-3）

**批次**：019J（P1，报告线程永久挂死风险）
**角色**：QA（独立验收）
**验收日期**：2026-08-05
**QA 任务书**：`docs/tasks/qa_tasks_20260805_019J.md`
**开发任务书**：`docs/tasks/dev_tasks_20260805_019J_single_stock_timeout_fix.md`（v2 定稿）
**评审报告**：`docs/reviews/review_019J_single_stock_timeout_fix_20260805.md`（架构师，✅ 通过，M-1~M-3 并入 v2）
**开发自验报告**：`reports/dev_selftest_019J_single_stock_timeout_fix_20260805.md`（仅对照参考，QA 未采信其结论，独立构造测试）
**验收结论**：✅ **通过**（验收用例 9/9，断言 48/48 PASS，红线 10/10 满足）

---

## 〇、验收环境与独立性声明

| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`（3.12） |
| 唯一改动文件 | `modules/daily_report.py`（L529-584 单只超时控制块 + L27-28 未使用 import 清理，M-1 可选已执行） |
| mock 测试 | QA 独立构造的临时脚本 8 份（harness + V3/V4/V5/V6/V7/V8 + 函数体 AST 对比），置于 `%TEMP%\opencode\qa019j\` 执行，**验收结束后删除，未留存仓库**；未复用开发临时脚本（开发自验报告已声明其脚本已删除） |
| 数据库隔离 | 全部 mock 测试使用 `%TEMP%\qa019j_dbs\` 临时 SQLite 库（含 daily_reports 全字段 schema + 019J 报告日期用远未来 `2099-12-31`）；真实 `stock_analyst.db` 零触碰（实测 LastWriteTime 仍为 2026-08-05 16:52:37 生产批次时刻，19:41-19:44 QA 测试期间无写入） |
| 网络隔离 | 全部 mock：`fetch_capital_flow_batch`/`_update_progress_file` no-op、`get_connection` 指向临时库、`_process_single_stock` 注入模拟 worker、`_REPORTS_DIR` 重定向 %TEMP%；零真实网络请求 |
| 独立性 | 本报告结论全部基于 QA 独立构造的测试证据；开发自验报告仅作对照参考，未采信其 PASS/FAIL 结论 |

---

## 一、V1：代码级核查 ✅（8/8 PASS）

| 核查项 | 验证方法 | 结果 | 证据 |
|---|---|---|---|
| 全文件 `with ThreadPoolExecutor` 写法 = 0 处（M-1 红线） | `rg "with ThreadPoolExecutor" modules/daily_report.py` | ✅ PASS | 0 命中（rg exit 1）；全文 grep `ThreadPoolExecutor\|FuturesTimeout` 仅 1 处命中 = **L322 docstring**「供 ThreadPoolExecutor 调用」，属注释文字，非代码写法 |
| 存在 `threading.Thread(target=..., daemon=True)` | Read L542 | ✅ PASS | `t = threading.Thread(target=_run_single_stock, daemon=True)` |
| 存在 `t.join(timeout=STOCK_TIMEOUT_SECONDS)` | Read L544 | ✅ PASS | `t.join(timeout=STOCK_TIMEOUT_SECONDS)` |
| 超时分支用 `t.is_alive()` 判定 | Read L545 | ✅ PASS | `if t.is_alive():` 进入超时分支（L549-579） |
| 线程内异常显式捕获（box['exc']） | Read L534-540 / L581-584 | ✅ PASS | L534 `box = {'exc': None}`；L536-540 闭包 try/except 存 `box['exc']`；L582-583 `if box.get('exc') is not None: raise box['exc']` 重抛走外层 except |
| 超时 failed 记录 error_msg=`采集超时({STOCK_TIMEOUT_SECONDS}s)` | Read L551-569 | ✅ PASS | L566 `error_msg=f'采集超时({STOCK_TIMEOUT_SECONDS}s)'`（业务值 90 → `采集超时(90s)`；mock 运行时替换为 5s） |
| `_process_single_stock` 函数体零改动 | **AST 级源码段对比 git 基线** | ✅ PASS | QA 以 `ast.get_source_segment` 提取函数完整源码段对比 git 基线 commit a22d291 与当前工作区：`HEAD L467 len=3736 | CUR L321 len=3736` → `PROCESS_SINGLE_STOCK_IDENTICAL: PASS`（字节级一致）；git diff 亦无该函数区域 hunk |
| L27-28 futures import 已删除（M-1 清理） | Read L20-30 + grep | ✅ PASS | L20-27 import 清单：atexit/json/logging/os/sys/threading/time/datetime，无 `concurrent.futures`；grep 无 `FuturesTimeout` 代码级命中 |

> 补充核查（红线 6）：模块顶部 import 无新增第三方包，`threading` 为 Python 标准库且改动前已在 L25，零新依赖；`config.py` 未改（`STOCK_TIMEOUT_SECONDS=90` / `BATCH_TIMEOUT_SECONDS=1800` 原样，哈希见 V9）。

---

## 二、V2：编译验证 ✅（PASS）

```
python -m py_compile modules/daily_report.py → PYCOMPILE_EXIT:0（无错误）
```

---

## 三、V3：超时保护验证（含显式耗时断言，可捕获 M-1 缺陷）✅（8/8 PASS）

**QA 独立 mock 组合**（全部注入 `modules.daily_report` 模块级）：

| mock 目标 | 注入实现 |
|---|---|
| `_get_all_stocks` | 2 只：S001（id=1，挂死）/ S002（id=2，正常） |
| `_update_progress_file` | no-op |
| `fetch_capital_flow_batch` | no-op（返回 None） |
| `get_connection` | **%TEMP% 临时库**连接工厂（与 db_manager 同构：WAL + busy_timeout） |
| `_process_single_stock` | S001 = 记录当前线程后 `time.sleep(120)`（永久挂死）；S002 = 写 ok + 快速返回成功 dict |
| `STOCK_TIMEOUT_SECONDS` | 运行时替换为 **5**（业务值 90 由开发自验 + 架构师对照实验覆盖，本批次缩短验收时长） |
| `_REPORTS_DIR` | 重定向 %TEMP%（防污染项目 reports/） |

**实测结果**：

| 断言 | 预期 | 实测 | 结果 |
|---|---|---|---|
| **单只处理耗时 < STOCK_TIMEOUT+5s（10s）**（显式耗时断言） | ≈5s | **elapsed = 5.04s** | ✅ PASS |
| 日志含 `[日报进度] S001 超时(5s)，跳过` | 命中 | 命中（`2026-08-05 19:44:41,781 ERROR [日报进度] S001 超时(5s)，跳过`，S001 开始后 5.007s 打出） | ✅ PASS |
| fail_count==1 / success_count==1（跳过超时股继续处理下一只） | 1 / 1 | `fail=1 success=1` | ✅ PASS |
| results 含 failed 条目（error=`采集超时(5s)`） | 命中 | `{'stock_id': 1, 'symbol': 'S001', 'status': 'failed', 'error': '采集超时(5s)'}` | ✅ PASS |
| DB failed 记录存在（status='failed'，error_msg=`采集超时(5s)`） | 命中 | 临时库 1 行：`status='failed', error_msg='采集超时(5s)', engine_version=None` | ✅ PASS |
| 下一只 S002 正常处理并写 ok | 命中 | 临时库 1 行：`status='ok', total_score=88.5, engine_version='v5'` | ✅ PASS |
| 超时后后台 worker 仍存活且 daemon=True（未 join 未等——红线 8/10） | 存活 | `alive=True daemon=True enumerated=True`（`threading.enumerate()` 含该线程） | ✅ PASS |
| 批次整体未触发软超时 break（total==2） | 2 | `total=2`，success+fail=2，批次完成日志「成功1/失败1 耗时5s」 | ✅ PASS |

**分析**：批次 5.04s 即完成并继续处理 S002 —— daemon 线程 + `join(timeout)` 方案甲生效。**关键缺陷捕获能力**：with 块旧版（git HEAD L640-670）在该场景须等 mock 挂死满 120s 才返回（架构师对照实验：5.01s 超时 → 120s 退出），elapsed=120s 会击穿 10s 断言——本断言确认 M-1 缺陷未被复制。

---

## 四、V4：worker 迟到自愈验证（M-2 细化：分两次查库）✅（6/6 PASS）

**QA mock 组合**：`_process_single_stock` = S001 `sleep(10)` 后调用**真实 `_save_report`** 写 ok + 返回成功 dict（模拟 08-05 600276 实证行为）；S002 快速写 ok；`STOCK_TIMEOUT_SECONDS=5`；2 只。

**实测结果（分两次查库，显式时序）**：

| 时序 | 断言 | 预期 | 实测 | 结果 |
|---|---|---|---|---|
| ① 批次返回后（elapsed=5.05s，S001 worker 仍在 sleep） | S001 超时分支触发（elapsed < 10s） | ≈5s | **elapsed = 5.05s** | ✅ PASS |
| ① 同刻 | failed 中间态存在（status='failed'，error_msg 含"超时"） | 命中 | 临时库 1 行 `status='failed', error_msg='采集超时(5s)'` | ✅ PASS |
| ① 同刻 | results 含 S001 failed 条目 | 命中 | `{'symbol': 'S001', 'status': 'failed', 'error': '采集超时(5s)'}` | ✅ PASS |
| ② 等待 12s（mock sleep 10s 完成后） | 被覆盖为 ok、error_msg 为 None（ok 终态） | 命中 | 1 行 `status='ok', error_msg=None, total_score=66.0`（generated_at=19:42:36 即 worker 迟到时刻） | ✅ PASS |
| ② 同刻 | 同日同股仅 1 条记录（无重复） | rows=1 | `count=1`（DELETE+INSERT 幂等覆盖，无 failed+ok 双行） | ✅ PASS |
| ② 同刻 | 终态数据完整（评分落库） | 命中 | `total_score=66.0, engine_version='v5', markdown_content='QA-V4-selfheal-ok'` | ✅ PASS |

**分析**：failed 中间态 → ok 终态覆盖完整验证（红线 7 数据自愈不丢分）。另 QA 首轮脚本曾因 mock 设计失误（S002 亦 sleep 10s 致双超时）运行一次，该失误轮次意外旁证：**双超时场景下 S001 worker 迟到自愈仍生效**（failed→ok 覆盖成功，无重复行），行为与单超时一致。

---

## 五、V5：异常路径验证（红线 9 语义隔离）✅（4/4 PASS）

**QA mock 组合**：`_process_single_stock` = S001 `sleep(2)` 后 `raise RuntimeError('测试异常-019J-QA')`；S002 正常写 ok；`STOCK_TIMEOUT_SECONDS=5`；2 只。

**实测结果**：

| 断言 | 预期 | 实测 | 结果 |
|---|---|---|---|
| 异常路径未误判为超时（elapsed≈2s < STOCK_TIMEOUT=5s） | ≈2s | **elapsed = 2.04s** | ✅ PASS |
| fail_count==1 / 批次继续 success_count==1 | 1 / 1 | `fail=1 success=1` | ✅ PASS |
| results 异常文案**非**"采集超时" | `测试异常-019J-QA` | `error='测试异常-019J-QA'` | ✅ PASS |
| DB error_msg 为异常文案且不含"超时"（红线 9 语义隔离） | 命中 | `error_msg='测试异常-019J-QA'`（无"超时"字样） | ✅ PASS |

**分析**：线程内异常经 box['exc'] 显式捕获后重抛，走外层 except（fail_count+1 + failed 记录），与现状语义一致；未与超时文案混淆——红线 9（语义隔离）成立。

---

## 六、V6：成功路径回归验证 ✅（3/3 PASS）

**QA mock 组合**：S001 正常返回（reused=False，score=88.5，score_change=8.5）+ 写 ok；S002 返回 reused=True（当日已有有效报告）；`STOCK_TIMEOUT_SECONDS=5`；2 只。

**实测结果**：

| 断言 | 预期 | 实测 | 结果 |
|---|---|---|---|
| success_count==2 无失败 / reuse_count==1 / v5_count==2 | 2 / 1 / 2 | `success=2 fail=0 reuse=1 v5=2` | ✅ PASS |
| results 全部 ok 条目（score 正确） | 命中 | S001 `score=88.5 engine='v5'`；S002 `score=70.0`，均 status='ok' | ✅ PASS |
| 无额外开销（elapsed < 4s，未触发超时路径） | <4s | **elapsed = 0.02s** | ✅ PASS |

---

## 七、V7：进程退出验证（019I M-4 同款）✅（2/2 PASS）

**QA 测试方法**：子进程跑批次（`STOCK_TIMEOUT_SECONDS=5`、worker `time.sleep(120)` 永久挂死）后主线程批次结束即退出，PowerShell Stopwatch 测量进程 wall time。

**实测结果**：

| 断言 | 预期 | 实测 | 结果 |
|---|---|---|---|
| 进程 10 秒内退出（daemon 不阻塞） | returncode=0，wall < 10s | **returncode=0，wall = 7.06s**（含 ~2s 模块导入开销；纯批次后退出 < 5.2s） | ✅ PASS |
| 批次耗时为超时值而非 worker 挂死时长 | ≈5s（非 120s） | **BATCH_ELAPSED = 5.03s**，`SUMMARY fail_count=1 success_count=0` | ✅ PASS |

**分析**：挂死 daemon 线程存活期间进程正常退出（`threading._shutdown` 仅 join 非 daemon 线程），随进程回收——红线 8（进程退出不阻塞）成立；批次耗时为 5s 超时值而非 120s 挂死时长。

---

## 八、V8：report_type 隔离回归（M-2 可选，已执行）✅（4/4 PASS）

**QA mock 组合**：临时库预置 S001 daily ok 行（score=50.0）→ 跑 intraday 批次（S001 `sleep(10)` 自愈写 ok intraday，`STOCK_TIMEOUT_SECONDS=5`）→ 分两次查库。

**实测结果**：

| 断言 | 预期 | 实测 | 结果 |
|---|---|---|---|
| intraday 超时中间态：failed intraday + daily ok 并存 | 命中 | 2 行：`daily/ok/50.0` + `intraday/failed/采集超时(5s)` | ✅ PASS |
| intraday 自愈覆盖不删除 daily 行（daily+intraday 各 1 行） | 2 行 | 2 行：`daily/ok` + `intraday/ok（QA-V8-intraday-healed）` | ✅ PASS |
| 两行均为 ok | 均 ok | 均 `status='ok'` | ✅ PASS |
| 批次汇总正确（total=1） | 1 | `total=1` | ✅ PASS |

**分析**：`_save_report` L255-265 行为回归确认——daily 全删（含 intraday）、intraday 仅删 intraday；自愈覆盖未误删 daily 行（红线 7 不丢分 + 013 report_type 隔离语义保持）。

---

## 九、V9：零改动确认 ✅（12/12 PASS）

**QA 独立计算 SHA256（前 16 位），与 QA 任务书 PM 快照比对（11/11 一致）**：

| 文件 | QA 实测 | PM 快照 | 结果 |
|---|---|---|---|
| modules/daily_report.py（**唯一改动文件**） | `94C20A5CB7C78A7C` | `94C20A5CB7C78A7C` | ✅ 一致 |
| app.py | `8F8373C029E76390` | `8F8373C029E76390` | ✅ 一致 |
| config.py | `F6CE1F84B8DDACDA` | `F6CE1F84B8DDACDA` | ✅ 一致 |
| requirements.txt | `DBE076A7458C5788` | `DBE076A7458C5788` | ✅ 一致 |
| templates/index.html | `769ECE1C80627DB7` | `769ECE1C80627DB7` | ✅ 一致 |
| modules/data_adapter.py | `0792E5006D7DCED9` | `0792E5006D7DCED9` | ✅ 一致 |
| modules/advisor.py | `CA1857B0F6452B20` | `CA1857B0F6452B20` | ✅ 一致 |
| modules/analysis_engine.py | `DF71A6FE4FD7685D` | `DF71A6FE4FD7685D` | ✅ 一致 |
| modules/alert_engine.py | `053F0CDB4DA62385` | `053F0CDB4DA62385` | ✅ 一致 |
| modules/data_collector.py | `D8D8CFF92AFCEFBE` | `D8D8CFF92AFCEFBE` | ✅ 一致 |
| database/db_manager.py | `76407851552761F5` | `76407851552761F5` | ✅ 一致 |

**函数体 diff 为空（增强验证）**：QA 以 AST `get_source_segment` 提取 `_process_single_stock` 完整源码段，对比 **git 基线 commit a22d291**（`git show HEAD:stock_analyst/modules/daily_report.py`）与当前工作区版本：

```
HEAD L467 len=3736 | CUR L321 len=3736
PROCESS_SINGLE_STOCK_IDENTICAL: PASS
```

> 与 019I QA 同款增强验证：证明该函数自仓库初始化以来从未被任何批次（含 019A-019H 累积改动与 019J）触碰。git diff 中 `_process_single_stock` 区域无任何 hunk；019J 相关 hunk 仅 L672-746 区域（`with ThreadPoolExecutor` 块 → daemon 线程块）与 L24-31（M-1 import 清理），与任务书范围红线一致。

---

## 十、红线遵守核查清单（10/10 满足）

| 红线 | 核查方法 | 结果 |
|---|---|---|
| 1. 功能红线：单只超时立即跳过继续；worker 永久挂死不拖死报告线程 | V3 耗时断言 + V7 | ✅ V3 批次 5.04s（mock 挂死 120s 场景）；V7 进程 7.06s 退出、批次 5.03s |
| 2. M-1 红线：严禁 `with ThreadPoolExecutor` | V1 grep 全文件 | ✅ 0 处（仅 L322 docstring 注释文字） |
| 3. 范围红线：仅 daily_report.py L529-584 | V9 零改动确认 | ✅ 11 文件哈希全一致；git diff 无其他功能 hunk |
| 4. 语义红线：超时/异常/成功三路径与现状一致 | V3/V5/V6 断言 | ✅ 超时=failed+continue、异常=外层 except、成功=reuse/fallback/counts 逻辑不变 |
| 5. 签名红线：`_process_single_stock` 签名和函数体不变 | V1 AST 对比 | ✅ 与 git 基线字节级一致（3736 字符） |
| 6. 零代码约束：无新依赖、不碰 config.py/DB schema | V1 import 核查 + V9 | ✅ threading 标准库（改动前已在）；config.py/db_manager.py 哈希不变 |
| 7. 数据自愈红线：不阻止 worker 迟到 ok 覆盖 | V4 分两次查库 + V8 | ✅ failed 中间态 → ok 终态、同日同股仅 1 行；intraday 自愈不删 daily 行 |
| 8. 进程退出红线：挂死 worker 存活时退出不被阻塞 | V7 | ✅ returncode=0，wall 7.06s（worker 挂死 120s 存活中） |
| 9. 语义隔离红线（M-3）：超时 error_msg 固定 `采集超时(90s)`，异常路径不复用 | V3/V5 断言 | ✅ V3 `采集超时(5s)` / V5 `测试异常-019J-QA` 互不复用 |
| 10. worker 不可阻断红线（M-3）：超时分支严禁 join/等待/终止 worker | V3 断言 + Read L545-579 | ✅ V3-7 worker 存活 + daemon + 在 enumerate 中；代码核查超时分支无任何 join/终止调用 |

---

## 十一、新发现问题（QA 独立发现，均非阻断）

1. **无功能/代码级新发现问题**。
2. **运行实例提醒（PM 备注确认）**：当前运行中 app.py 进程仍为旧代码（含 `with ThreadPoolExecutor` 缺陷块），QA 验收基于 mock 测试与静态核查，**须用户重启 `python app.py` 后单只超时保护修复才在运行实例生效**。
3. **QA 测试过程说明（非产品问题）**：V4 首轮执行因 QA 脚本设计失误（S002 mock 亦 sleep 10s 导致双超时）暴露脚本问题，QA 修正脚本后重跑 6/6 PASS；该失误轮次同时旁证双超时场景 worker 自愈仍生效。
4. **范围外已登记风险（架构师登记，本批次不验收）**：R-6（迟到 worker 跨批次覆盖）、R-7（超时边界极窄竞态）、R-8（box 吞 BaseException）——均低危"接受观察"，QA 实测未观察到异常表现。

---

## 十二、验收结论

**✅ 通过（QA 独立验收）**

- 验收用例：**9/9 通过**（V1 代码级 8/8、V2 编译、V3 超时保护 8/8、V4 自愈 6/6、V5 异常路径 4/4、V6 成功路径 3/3、V7 进程退出 2/2、V8 report_type 隔离 4/4、V9 零改动 12/12），共 **48/48 检查 PASS、0 FAIL**。
- 红线遵守：**10/10 满足**（含 M-1 红线 `with ThreadPoolExecutor` 0 处、M-3 红线 9/10）。
- **M-1 缺陷未复发实证**：V3 显式耗时断言 elapsed=5.04s（with 块旧版需 120s）——019I M-4 同款捕获能力成立。
- 开发自验报告结论与 QA 独立结论方向一致（daemon 线程方案生效、自愈覆盖、语义隔离），但 QA 结论基于独立构造的测试证据，未依赖开发自验。

---

## 十三、签署

| 项 | 内容 |
|---|---|
| 验收人 | QA（独立验收） |
| 验收日期 | 2026-08-05 |
| 交付物 | 本报告 `reports/qa_accept_019J_single_stock_timeout_fix_20260805.md` |
| 临时脚本清理 | QA mock 测试脚本（qa019j_harness.py、qa019j_v3/v4/v5/v6/v7_proc/v8.py、qa019j_cmp_body.py）、日志证据文件（v3_out.txt~v8_out.txt）、git 基线提取文件均已删除，未留存仓库（红线：临时脚本清理 ✅） |
| 后续流程 | 待 PM+QA 双签 → 监理批准关闭；提醒用户重启 app.py 后修复生效 |

> **QA 签署**：本报告由 QA 于 2026-08-05 独立完成。静态核查文件：modules/daily_report.py（L20-30、L321-439、L529-584）、config.py（L113-117）、git 基线版本（AST 对比）；实测为 QA 独立构造的受控 mock 场景（V3-V8，%TEMP% 临时库 + 零网络），全部断言 48/48 PASS，红线 10/10 满足。未采信开发自验报告结论。

---

## PM+QA 双签块（019J）

**双签日期**：2026-08-05

### PM 独立核验结论

**PM 独立复跑（2026-08-05，不采信 QA 结论）**：

| 核验项 | 方法 | 结果 |
|---|---|---|
| V1 代码级核查（红线 2） | `rg "with ThreadPoolExecutor" modules/daily_report.py` | ✅ 0 命中（rg exit 1），M-1 红线满足 |
| V1 实现抽查 | Read L529-584：daemon 线程 + box 模式 + `is_alive()` 超时分支 + continue 不 join | ✅ 与任务书 v2 方案甲一致 |
| V2 编译 | `python -m py_compile modules/daily_report.py` | ✅ PASS |
| V9 哈希（唯一改动文件） | SHA256 `daily_report.py` | ✅ `94C20A5CB7C78A7C` 与 QA/自验快照一致 |
| V9 哈希（10 个非改动文件） | SHA256 复算 | ✅ 全部与 QA 报告一致（app.py 8F8373...、config.py F6CE1F...、data_collector.py D8D8CFF... 等 10/10） |
| 核心语义独立实验 | daemon 线程 `join(timeout=3)` + worker `sleep(120)` 挂死 | ✅ elapsed=3.01s（未等 120s）、alive=True、daemon=True——超时立即返回不 join，红线 8/10 成立 |

**PM 核验结论**：QA 报告结论与 PM 独立复跑方向一致（代码实现、编译、零改动、核心超时语义均实证成立）。QA 48/48 断言基于独立构造的 mock 证据（%TEMP% 临时库、零网络），可信。**PM 同意 QA 验收结论：通过。**

### 双签签署

| 角色 | 签署人 | 日期 | 结论 |
|---|---|---|---|
| QA | QA（独立验收） | 2026-08-05 | ✅ 通过（9/9 用例，48/48 断言，10/10 红线） |
| PM | PM（独立核验） | 2026-08-05 | ✅ 同意（独立复跑 6/6 项通过） |

### 关闭前提醒

1. **运行实例重启**：当前运行中 app.py 仍为旧代码，用户须重启 `python app.py` 后单只超时保护才生效
2. **架构师登记风险（接受观察，不在本批次）**：R-6（迟到 worker 跨批次覆盖）、R-7（超时边界极窄竞态）、R-8（box 吞 BaseException）
3. **备查项（不在本批次）**：R-2（EM 逐只回退 `_em_batch_collect` 无超时包装，中风险，待架构师裁定是否立项）

---

> **状态**：✅ QA 独立验收通过（2026-08-05）→ ✅ PM+QA 双签（2026-08-05）→ ✅ 监理批准关闭（2026-08-05）

---

## 关闭块（019J）

**监理批准关闭日期**：2026-08-05

**关闭结论**：✅ **019J 批次正式关闭**

| 流程节点 | 日期 | 状态 |
|---|---|---|
| PM 签发任务书 v1 | 2026-08-05 | ✅ |
| 架构师评审（通过，M-1~M-3 并入 v2） | 2026-08-05 | ✅ |
| 监理批准 v2 | 2026-08-05 | ✅ |
| 开发执行 + 自验（30/30 PASS） | 2026-08-05 | ✅ |
| QA 独立验收（9/9 用例 48/48 断言 10/10 红线） | 2026-08-05 | ✅ |
| PM+QA 双签 | 2026-08-05 | ✅ |
| 监理批准关闭 | 2026-08-05 | ✅ |

**关闭时遗留事项（登记，不阻塞关闭）**：
1. 运行实例重启：用户须重启 `python app.py` 后单只超时保护修复生效
2. 登记风险（接受观察）：R-6（迟到 worker 跨批次覆盖）、R-7（超时边界极窄竞态）、R-8（box 吞 BaseException）
3. 备查项：R-2（EM 逐只回退 `_em_batch_collect` 无超时包装，019K 候选，P2）

> **PM 签署**：019J 已按流程完成全部节点并经监理批准，正式关闭。归档完毕。
