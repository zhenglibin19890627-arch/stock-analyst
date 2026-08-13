# 开发自验报告 019J — 单只处理超时保护修复（报告线程 join 挂死缺陷 R-3）

**批次**：019J（P1，超时保护形同虚设，存在报告线程永久挂死风险）
**角色**：开发工程师（单人，内嵌任务书窗口独立执行）
**自验日期**：2026-08-05
**任务书**：`docs/tasks/dev_tasks_20260805_019J_single_stock_timeout_fix.md`（v2 定稿，M-1~M-3 已并入）
**架构评审**：`docs/reviews/review_019J_single_stock_timeout_fix_20260805.md`（✅ 通过，无强制修订项；M-1 可选清理 / M-2 验收时序断言 / M-3 红线 9/10）
**状态**：开发执行完成 + 自验通过（30/30），待 QA 独立验收 → PM+QA 双签 → 监理批准关闭

---

## 一、改动清单（严格一文件，两处改动）

**文件**：`modules/daily_report.py`（唯一改动文件，哈希 `A96C51CD...` → `94C20A5C...`）

### 改动 1（核心）：单只超时控制改用 daemon 线程 + join(timeout)（方案甲）

**位置**：L529-584（单只超时控制块，原 L531-570）

**改动内容**：`with ThreadPoolExecutor(max_workers=1) + future.result(timeout=90)` 块替换为：

```python
            # 单只超时控制（019J：daemon 线程 + join(timeout)，替代 executor 上下文管理器
            # M-1 红线：executor 上下文管理器退出时 __exit__ 调用 shutdown(wait=True)
            # 会 join 挂死 worker，超时保护形同虚设——本实现超时后立即 continue，不 join 不等待 worker）
            try:
                # box 模式：线程内异常不自动传播，必须显式捕获（否则超时判定会误判）
                box = {'exc': None}

                def _run_single_stock():
                    try:
                        box['r'] = _process_single_stock(stock, target_date, force, report_type)
                    except Exception as e:
                        box['exc'] = e

                t = threading.Thread(target=_run_single_stock, daemon=True)
                t.start()
                t.join(timeout=STOCK_TIMEOUT_SECONDS)
                if t.is_alive():
                    # 超时：写 failed 记录 + results.append + continue，不等待 worker
                    # （worker 迟到完成时 _process_single_stock 内部 _save_report
                    #   DELETE+INSERT 会覆盖 failed 为 ok，数据自愈不丢分）
                    fail_count += 1
                    logger.error(f'[日报进度] {symbol} 超时({STOCK_TIMEOUT_SECONDS}s)，跳过')
                    _save_report(
                        ...
                        status='failed',
                        error_msg=f'采集超时({STOCK_TIMEOUT_SECONDS}s)',
                        ...
                    )
                    results.append(
                        {
                            ...
                            'status': 'failed',
                            'error': f'采集超时({STOCK_TIMEOUT_SECONDS}s)',
                        }
                    )
                    continue

                # 线程内异常重抛，走外层 except（fail_count+1 + failed 记录，与现状一致）
                if box.get('exc') is not None:
                    raise box['exc']
                result = box['r']
```

**语义保持（与现状逐项对照）**：
- **超时路径**：`t.is_alive()` → failed 记录（error_msg=`采集超时(90s)`）+ `results.append(failed)` + `continue`，与现状 L539-570 完全一致（仅触发机制从 `except FuturesTimeout` 变为 `is_alive()` 分支）
- **异常路径**：box 捕获线程内异常 → 重抛 → 外层 `except Exception` 处理（fail_count+1 + failed 记录），与现状一致
- **成功路径**：`box['r']` → L586-610 成功处理逻辑（reuse/fallback/success_count/v5_count）零改动
- **worker 迟到自愈**：超时后 worker 为后台 daemon 线程继续运行，`_process_single_stock` 内部 L408 `_save_report`（DELETE+INSERT）覆盖 failed 为 ok——08-05 实证行为保留

### 改动 2（M-1 建议项，已执行）：未使用 import 清理

删除 L27-28：`from concurrent.futures import ThreadPoolExecutor` 与 `from concurrent.futures import TimeoutError as FuturesTimeout`（修复后不再使用，ruff F401 会告警；threading 已在模块顶部 L25 import）。

---

## 二、自验结果（对照任务书验收标准）

### V1：代码级核查 ✅（验收 1）

| 核查项 | 结果 |
|---|---|
| `daily_report.py` 全文件 `with ThreadPoolExecutor` 写法 = **0 处**（grep 核验，M-1 红线） | ✅ rg 命中 0（仅 `_process_single_stock` docstring L322 含"供 ThreadPoolExecutor 调用"字样——019A 遗留文本，函数体零改动红线内，且不含 `with ThreadPoolExecutor` 写法） |
| `threading.Thread(target=_run_single_stock, daemon=True)` + `t.join(timeout=STOCK_TIMEOUT_SECONDS)` | ✅ L542/L544 |
| 超时分支（`t.is_alive()`）与现状 failed 记录逻辑一致（error_msg=`采集超时(90s)`） | ✅ L545-579 |
| 线程内异常显式捕获（box['exc'] 模式） | ✅ L534-540/L582-583 |
| `_process_single_stock` 函数体 diff 为空 | ✅ 函数体与开发前逐行比对一致（仅行号因 import 删除偏移 -2）；签名、docstring、内部逻辑零改动 |
| 批次整体软超时检查（L503-511）/ 成功处理（L586-610）/ 外层异常（L612-646）/ `_save_report` 均未触碰 | ✅ |

### V2：编译验证 ✅（验收 2）

```
python -m py_compile modules/daily_report.py → 成功
```

ruff check：仅 1 项 **I001（import 排序）为存量问题**（已验证 HEAD 基线版本同样报 I001，019A 批次遗留，非本批次引入；本批次不扩大范围）。无新增 lint 问题。

### V3：超时保护验证 ✅（验收 3，含显式耗时断言）

临时脚本（自验后已删）mock 场景，**真实 90s 超时 + mock worker 挂死 120s**，实测：

| 断言 | 实测 | 结果 |
|---|---|---|
| mock `_process_single_stock` = `sleep(120)`（挂死）→ 批次 95 秒内返回并进入下一只 | **elapsed = 90.1s < 95s** | ✅ PASS |
| 日志含 `[日报进度] S001 超时(90s)，跳过` | 命中 | ✅ PASS |
| fail_count==1 / success_count==1（跳过超时股继续处理下一只） | 1 / 1 | ✅ PASS |
| results 含 failed 条目（error=`采集超时(90s)`） | 命中 | ✅ PASS |
| DB failed 记录（status='failed'，error_msg='采集超时(90s)'） | 命中 | ✅ PASS |
| 下一只 S002 正常处理并写 ok（total_score=75.0） | 命中 | ✅ PASS |
| 超时后后台 worker 仍存活且 daemon=True（未 join 未等） | alive_daemon=1，全 daemon | ✅ PASS |
| 批次整体未触发软超时 break（total==2） | 2 | ✅ PASS |

> **缺陷捕获能力**：with 块旧版在该场景下需等 worker 满 120s 才返回（架构评审对照实验：5.01s 超时 → 120s 才退出），95s 断言必失败——本断言可捕获 M-1 缺陷（019I M-4 同款思路）。

### V4：worker 迟到自愈验证 ✅（验收 4，M-2 细化——分两次查库）

mock `_process_single_stock` = sleep(10) 后调用真实 `_save_report` 写 ok + 返回成功（模拟 600276 实证行为），STOCK_TIMEOUT=5：

| 时序 | 断言 | 实测 | 结果 |
|---|---|---|---|
| ① 超时返回后（≤5s，worker 仍在 sleep）立即查库 | failed 记录存在，error_msg 含"超时" | `采集超时(5s)` | ✅ PASS |
| ①（同刻）| results 含 failed 条目 | 命中 | ✅ PASS |
| ② 等待 ≥10s（mock sleep 完成后）再查库 | 被覆盖为 ok、error_msg 为 None | ok / None | ✅ PASS |
| ②（同刻）| 同日同股仅 1 条记录（无重复） | rows=1 | ✅ PASS |
| ②（同刻）| 终态数据完整（评分 63.1 落库） | 63.1 | ✅ PASS |

### V5：异常路径验证 ✅（验收 5）

mock `_process_single_stock` = sleep(2) 后 raise `RuntimeError('测试采集异常-019J')`，STOCK_TIMEOUT=5，2 只：

| 断言 | 实测 | 结果 |
|---|---|---|
| 异常路径未误判为超时（elapsed=2.0s < STOCK_TIMEOUT） | 2.0s | ✅ PASS |
| fail_count==1 / 批次继续 success_count==1 | 1 / 1 | ✅ PASS |
| results 异常文案**非**"采集超时" | `测试采集异常-019J` | ✅ PASS |
| DB error_msg 为异常文案（红线 9 语义隔离） | 命中，不含"超时" | ✅ PASS |

### V6：成功路径回归验证 ✅（验收 6）

mock 正常返回（S001 全新 reused=False，S002 reused=True）：

| 断言 | 实测 | 结果 |
|---|---|---|
| success_count==2 无失败 / reuse_count==1 / v5_count==2 | 2 / 1 / 2 | ✅ PASS |
| results 全部 ok 条目（score=75.0） | 命中 | ✅ PASS |
| 无额外开销（elapsed < 4s，未触发超时路径） | 0.0s | ✅ PASS |

### V7：进程退出验证 ✅（验收 7，019I M-4 同款）

子进程实验：挂死 worker（`sleep(120)`，超时 5s 后主线程退出）存活期间进程退出：

| 断言 | 实测 | 结果 |
|---|---|---|
| 进程 10 秒内退出（daemon 不阻塞） | **6.5s** 退出（含 5s join 超时 + 1.5s 启动），returncode=0 | ✅ PASS |
| 批次耗时为超时值而非 worker 挂死时长 | batch=5.0s（非 120s） | ✅ PASS |

### V8（验收 9，M-2 可选补充）：report_type 隔离回归 ✅

预置 daily ok 行 → 跑 intraday 批次（超时 + worker 自愈覆盖 intraday）：

| 断言 | 实测 | 结果 |
|---|---|---|
| intraday 自愈覆盖不删除 daily 行（2 行：daily+intraday） | daily+intraday 各 1 行 | ✅ PASS |
| 两行均为 ok | 均 ok | ✅ PASS |

> `_save_report` L257-267 行为（daily 全删 / intraday 仅删 intraday）经新调用链路回归确认。

### 全仓回归

`python -m pytest tests/` → **310 passed**（无失败，019I 后新增测试亦全过）。

---

## 三、红线遵守情况

| 红线 | 遵守情况 |
|---|---|
| 1. 功能红线：单只超时时报告线程立即跳过继续；worker 永久挂死不拖死报告线程 | ✅ V3 elapsed=90.1s（未等 120s mock）；V7 进程退出 6.5s 不被挂死 worker 阻塞 |
| 2. M-1 红线（最高）：严禁 `with ThreadPoolExecutor` 写法 | ✅ 全文件 grep `with ThreadPoolExecutor` = 0；daemon 线程 + `join(timeout)` 不等待 |
| 3. 范围红线：仅 `modules/daily_report.py` L529-584 | ✅ 本会话仅覆盖写入 daily_report.py 1 次；其余文件哈希与 019I 开发结束时一致（见第四节） |
| 4. 语义红线：超时/异常/成功三路径与现状一致 | ✅ V3/V5/V6 逐项断言 |
| 5. 签名红线：`_process_single_stock()` 签名和函数体不变 | ✅ V1 AST/文本比对零改动 |
| 6. 零代码约束：无新 pip 依赖；config.py / DB schema 未碰 | ✅ threading 为标准库；config.py 哈希未变 |
| 7. 数据自愈红线：不阻止 worker 迟到 ok 覆盖 | ✅ V4 分两次查库实证 failed → ok，且不 join/不终止 worker |
| 8. 进程退出红线：挂死 worker 存活期间退出不被阻塞 | ✅ daemon=True，V7 实证 6.5s 退出 |
| 9. 语义隔离红线（M-3）：超时 error_msg 固定 `采集超时(90s)`，异常路径不复用 | ✅ V3-6 断言 `采集超时(90s)`；V5-5 断言异常文案且不含"超时" |
| 10. worker 不可阻断红线（M-3）：超时分支严禁 join/等待/终止 worker | ✅ 超时分支仅写记录+continue；V3-8 断言 worker 存活（未 join） |

---

## 四、零改动文件哈希快照（开发结束时刻，SHA256 前 16 位，供 QA 复核）

| 文件 | 哈希 | 与 019I 开发结束时对比 |
|---|---|---|
| modules/daily_report.py | 94C20A5CB7C78A7C | **本批次唯一改动文件**（019I 时 A96C51CD5049B679） |
| app.py | 8F8373C029E76390 | 不变 |
| config.py | F6CE1F84B8DDACDA | 不变 |
| requirements.txt | DBE076A7458C5788 | 不变 |
| templates/index.html | 769ECE1C80627DB7 | 不变 |
| modules/data_adapter.py | 0792E5006D7DCED9 | 不变 |
| modules/advisor.py | CA1857B0F6452B20 | 不变 |
| modules/analysis_engine.py | DF71A6FE4FD7685D | 不变 |
| modules/alert_engine.py | 053F0CDB4DA62385 | 不变 |
| modules/data_collector.py | D8D8CFF92AFCEFBE | 不变（019I 交付物） |
| database/db_manager.py | 76407851552761F5 | 不变 |

> 019I 自验报告第三节的快照与本表逐一比对：除 daily_report.py 外**全部一致**——自 019I 开发结束时点至今，仓库唯一被本批次改动的文件即 daily_report.py。
> 临时自验脚本已删除，未留存仓库；自验 DB 均为 %TEMP% 隔离库，未触碰真实 `stock_analyst.db`。

---

## 五、QA 交接说明（独立验收指引）

### 5.1 验收标准映射

| 任务书验收 | QA 执行方式建议 |
|---|---|
| 1. 代码级核查 | `rg "with ThreadPoolExecutor" modules/daily_report.py` → 0 命中；grep `daemon=True`（L542）、`join(timeout=STOCK_TIMEOUT_SECONDS)`（L544）、`t.is_alive()`（L545）、`box['exc']`（L540/L583）；`_process_single_stock` 函数体与上一版本 diff 为空（或对照本报告 V1 特征） |
| 2. 编译验证 | `python -m py_compile modules/daily_report.py` |
| 3. 超时保护（95s 断言） | **推荐 mock 组合**：`daily_report._get_all_stocks = lambda: [A, B]`、`_update_progress_file = no-op`、`fetch_capital_flow_batch = no-op`、`get_connection` 指向临时库、`_process_single_stock = lambda: time.sleep(120)`（A 挂死）/ 快速返回（B）→ 期望 elapsed ≈ **90s（<95s）**、fail_count=1、success_count=1、日志含 `超时(90s)，跳过`、failed 记录 error_msg=`采集超时(90s)`。若用旧 with 块版本，该场景需等 120s 且失败——断言可捕获 M-1 缺陷 |
| 4. worker 迟到自愈 | 分两次查库：① 批次返回后立即查 → failed（error_msg 含"超时"）；② mock worker `sleep(10)` 后调用真实 `_save_report` 写 ok 再返回 → 等 ≥10s 查 → ok 终态、同日同股仅 1 条 |
| 5. 异常路径 | `_process_single_stock = sleep(2) 后 raise RuntimeError('X')`、STOCK_TIMEOUT=5（模块属性 `daily_report.STOCK_TIMEOUT_SECONDS` 可运行时替换）→ error_msg='X' 非"采集超时"、fail_count+1、批次继续 |
| 6. 成功路径 | mock 正常返回 → success_count/reuse/fallback/results ok 条目不变 |
| 7. 进程退出 | 子进程跑批次（超时 5s、worker sleep(120)）后立即退出 → 10s 内 returncode=0 |
| 8. 零改动 | 哈希比对第四节快照；`git status` 本批次新增 M 项仅 `modules/daily_report.py` |
| 9. M-2 可选 | report_type 隔离回归（V8 已验证组合）；`threading.enumerate()` 超时后新增线程 `daemon=True` |

### 5.2 开发环境备注

- 临时自验脚本已删除（红线 3 范围收敛），QA 需自行构造 mock；上方 mock 组合为验证过的推荐配置（自验 30/30 PASS）
- 本批次未重启运行中的 app.py 进程（若有运行实例仍为旧代码，须重启后修复生效）
- 自验运行环境：Python 3.12（`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`）
- 自验全程未触碰真实数据库（`stock_analyst.db` 无写入），无网络请求
