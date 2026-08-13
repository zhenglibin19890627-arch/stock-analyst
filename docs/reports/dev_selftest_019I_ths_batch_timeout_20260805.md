# 开发自验报告 019I — THS 批量预取超时保护（报告生成卡住修复）

**批次**：019I（P1，报告生成功能完全阻断）
**角色**：开发工程师（单人，内嵌任务书窗口独立执行）
**自验日期**：2026-08-05
**任务书**：`docs/tasks/dev_tasks_20260805_019I_ths_batch_timeout.md`（v2 定稿，M-1~M-4 已修订）
**架构评审**：`docs/reviews/review_019I_ths_batch_timeout_20260805.md`（有条件通过，M-1/M-2/M-3/M-4/R-1~R-5）
**状态**：开发执行完成 + 自验通过（11/11），待 QA 独立验收 → PM+QA 双签 → 监理批准关闭

---

## 一、改动清单（严格一文件，两处改动）

**文件**：`modules/data_collector.py`（唯一改动文件，基线 SHA256 `9F0721...A649786` → 当前 `D8D8CF...AF81`）

### 改动 1：THS 常量块新增超时常量（L1099，位于 `_THS_FAIL_THRESHOLD` L1098 与 EM 常量块 L1106 之间）

```python
_THS_REQUEST_TIMEOUT = 60  # 019I：单次 THS 接口请求超时（秒）
```

### 改动 2：`_fetch_capital_flow_ths_batch()` 核心调用逻辑（L1145-1181）加 daemon 线程超时包装

按 v2 定稿方案甲（daemon 线程 join(timeout)，**无 `with ThreadPoolExecutor`**），并实现 M-2（见下方决策说明）：

```python
    # 019I：THS 接口调用增加超时保护，防止服务器不响应时无限阻塞
    # M-1 修正：禁止使用 with ThreadPoolExecutor（with 退出时 shutdown(wait=True)
    #          会 join 挂死线程，修复无效——经架构师运行时实验 + PM 独立复现确认）
    # 改用 daemon 线程 join(timeout) 模式：
    #   - daemon 线程不参与解释器退出 join，进程退出不被阻塞（R-1 消除）
    #   - t.join(timeout=N) 超时后立即返回，不等待挂死线程
    import threading as _threading_019I

    def _call_with_timeout(fn, label):
        """019I：daemon 线程包装 THS 接口调用，超时返回 (None, True)，正常返回 (result, False)"""
        box = {}
        t = _threading_019I.Thread(
            target=lambda: box.update(r=fn()),
            daemon=True,
        )
        t.start()
        t.join(timeout=_THS_REQUEST_TIMEOUT)
        if t.is_alive():
            logger.warning(f'[同花顺批量] {label} 超时({_THS_REQUEST_TIMEOUT}s)，跳过')
            return None, True
        return box.get('r'), False

    # FIX-B：主接口 stock_fund_flow_individual()
    df, _primary_timed_out = _call_with_timeout(_try_ths_primary, '主接口')

    # 019I M-2：主接口超时（hang）视为服务器不响应，跳过重试直接尝试备选
    #（THS 阶段上界 185s→120s；非超时的普通失败仍按 FIX-B 重试1次）
    if df is None and not _primary_timed_out:
        logger.info('[同花顺批量] 主接口失败，5秒后重试1次...')
        time.sleep(5)
        df, _ = _call_with_timeout(_try_ths_primary, '主接口(重试)')

    # FIX-B：重试仍失败时，尝试备选接口 stock_individual_fund_flow_rank()
    if df is None:
        logger.info('[同花顺批量] 重试仍失败，尝试备选接口 stock_individual_fund_flow_rank()...')
        df, _ = _call_with_timeout(_try_ths_rank_backup, '备选接口')
```

**签名**：`_fetch_capital_flow_ths_batch()` 无参不变；`_try_ths_primary()` / `_try_ths_rank_backup()` 签名与函数体**零改动**。

### M-2 实现决策说明（任务书：建议项，开发可自行评估——本开发评估后**实现**）

| 评估项 | 结论 |
|---|---|
| **验收 3 的"65 秒内返回 None"断言** | 该断言为 M-4 强化项（捕获 M-1 with 块缺陷）。若主接口挂死（mock sleep 120）后仍走重试，链路耗时 = 60s 主超时 + 5s 间隔 + 60s 重试超时 = 125s（备选 mock 快速失败），**必然击穿 65s 断言**；若重试/备选也挂死则 185s。实现 M-2 后主接口超时直接走备选（快速失败）= 60s，**稳定满足 65s**。结论：M-2 是实现验收 3 的必要条件 |
| **用户等待体验** | THS hang 时报告生成阻塞 185s → 120s，减 35% |
| **容错损失** | 仅当主接口**超时**（服务器不响应，非普通失败）时跳过重试；普通失败（抛异常/空数据）仍按 FIX-B 重试 1 次，容错路径不变 |
| **实现** | `_call_with_timeout` 返回 `(result, timed_out)` 二元组；超时仍返回 `None` 语义（红线 5 保持），调用处仅用 `timed_out` 决定是否重试；备选接口无论主接口失败/超时都会尝试，THS 阶段上界 = 60s 主 + 60s 备选 = 120s，与架构师 M-2 建议完全一致 |

> **M-3 更正核实**：任务书 M-3 称"`import threading` 已在 data_collector.py 模块顶部导入"——经核实**不属实**（模块顶部 L17-48 无 threading，全文件 grep `import threading` 为 0）。按任务书 v2 定稿示例代码在函数内 `import threading as _threading_019I`（局部导入，Python 合法），既满足方案甲需求，又不扩大改动范围。

---

## 二、自验结果（对照任务书验收标准）

### V1：代码级核查 ✅（验收 1）

| 核查项 | 结果 |
|---|---|
| `_THS_REQUEST_TIMEOUT = 60` 存在于 THS 常量块（L1099，L1098 < L1099 < L1106） | ✅ |
| 主/重试/备选 3 处调用均经 `_call_with_timeout` 包装（AST 统计恰 3 处） | ✅ |
| 函数体无 `with ThreadPoolExecutor` 语句（AST With 节点检查 = 0；唯一文本命中在 M-1 说明注释内，为任务书 v2 定稿代码原文，非代码写法） | ✅ |
| 超时路径返回 None（`(None, True)` → 主流程 df=None） | ✅ |
| `_try_ths_primary` / `_try_ths_rank_backup` 函数体零改动（AST 源码段断言：不含 `_call_with_timeout`，保留 `ak.stock_fund_flow_individual()` / `ak.stock_individual_fund_flow_rank(indicator='今日')` 原始特征行） | ✅ |
| `_fetch_capital_flow_ths_batch()` 签名无参不变 | ✅ |

### V2：编译验证 ✅（验收 2）

```
python -m py_compile modules/data_collector.py → 成功
```

### V3：超时保护验证 ✅（验收 3，M-4 强化——含显式耗时断言）

临时脚本（自验后即删，未留存仓库）mock 场景，实测：

| 断言 | 实测 | 结果 |
|---|---|---|
| mock `_try_ths_primary` = `time.sleep(120)`（挂死）、备选快速失败 → `_fetch_capital_flow_ths_batch` 返回 None | **elapsed = 60.0s < 65s** ✅ | PASS |
| 日志含 `[同花顺批量] 主接口 超时(60s)，跳过` | 命中 | PASS |
| M-2：日志不含 `主接口(重试)`（超时跳过重试） | 未命中 | PASS |
| 超时后 `_THS_CAPITAL_CACHE['data']` 仍为 None（红线 7） | None | PASS |
| 全失败后 `_THS_CONSECUTIVE_FAIL_COUNT == 1` | 1 | PASS |

> 若未实现 M-2，该场景耗时为 125s（60+5+60），65s 断言必失败——V3 结果同时实证 M-2 决策的必要性。

### V4：降级链路完整性验证 ✅（验收 4）

mock 主/备选全部快速失败（普通失败，非超时），按序断言日志：

```
主接口失败，5秒后重试1次...（elapsed≈5s，等待生效）
→ 重试仍失败，尝试备选接口 stock_individual_fund_flow_rank()...
→ 全部接口失败，连续失败计数=1
→ 返回 None；_THS_CAPITAL_CACHE['data'] 仍为 None（红线 7）
```

- 全部按序断言 PASS（`i1 < i2 < i3`，elapsed = 5.0s，计数 = 1，缓存未写入）
- 返回 None 后调用方回退 `_em_batch_collect` 为 daily_report.py 既有逻辑，本批次未触碰（红线段落 5 保持）

### V5：正常路径回归验证 ✅（验收 5）

| 断言 | 实测 | 结果 |
|---|---|---|
| mock 主接口返回 DataFrame → 透明传递（同一对象） | 同一对象 | PASS |
| 成功时 `_THS_CONSECUTIVE_FAIL_COUNT` 重置为 0 | 0 | PASS |
| 成功时缓存写入（`_THS_CAPITAL_CACHE['data']` 为返回 DataFrame） | 写入 | PASS |
| 无额外开销（elapsed < 5s） | **0.002s** | PASS |

另有全仓回归：`python -m pytest tests/` → **301 passed**；`tests/qa_019f_isolation_test.py` → **9/9 passed**（019F 评分纯净回归，未破坏）。

### V6：进程退出验证 ✅（验收 6，M-4；方案甲自动满足）

子进程实验：daemon 线程挂死（`sleep(3600)`）存活期间，主线程结束后进程退出。

| 断言 | 实测 | 结果 |
|---|---|---|
| 进程在 10s 内退出（含 2s 主线程存活期） | **2.04s** 退出，returncode=0 | PASS |

daemon 线程不参与解释器退出 join → 进程退出不被挂死线程阻塞（R-1 消除）。

### V7：零改动确认 ✅（验收 7）

- 本会话文件操作仅：`modules/data_collector.py` 覆盖 1 次（哈希 `9F0721...` → `D8D8CF...`，改动为上述两处）
- 其他全部文件哈希快照（开发结束时记录，供 QA 复核）：见第三节表格
- `git status`：data_collector.py 之外的 M 项为 019A-019H 历史批次累积未提交改动，非本会话产生；本会话无新增/删除仓库内文件
- 临时自验脚本已删除，未留存

---

## 三、红线遵守情况

| 红线 | 遵守情况 |
|---|---|
| 1. 功能红线：THS 不响应时不得永久阻塞 | ✅ 60s 单次超时，120s 阶段上界（M-2），V3 实证 60.0s 返回 |
| 2. 范围红线：仅 data_collector.py 函数+常量块 | ✅ 仅 1 文件 2 处改动；其余文件哈希不变（见下） |
| 3. 签名红线 | ✅ 三函数签名均不变；`_try_ths_primary`/`_try_ths_rank_backup` 函数体零改动（V1） |
| 4. 零代码约束 | ✅ 无新 pip 依赖（threading 为标准库，函数内局部导入）；config.py 未碰；无 schema 变更 |
| 5. 降级安全红线：超时后返回 None | ✅ 超时返回 `(None, True)`，主流程 df=None，与现有失败路径一致；缓存检查/失败计数/EM 回退逻辑未动（V3/V4 实证） |
| 6. 挂死线程红线（M-1） | ✅ 无 `with ThreadPoolExecutor` 语句（AST 检查=0）；daemon 线程 + `join(timeout)` 不等待挂死线程；进程退出 2.04s（V6） |
| 7. 缓存红线（M-1） | ✅ 超时/失败路径不写 `_THS_CAPITAL_CACHE`（V3/V4 断言 `data` 为 None） |
| 8. 先例红线（R-3） | ✅ 未复制 daily_report.py L533 `with ThreadPoolExecutor` 模式；本函数为 daemon 线程方案 |

**零改动文件哈希快照**（开发结束时刻，SHA256 前 16 位，供 QA 复核）：

| 文件 | 哈希 |
|---|---|
| modules/daily_report.py | A96C51CD5049B679 |
| app.py | 8F8373C029E76390 |
| config.py | F6CE1F84B8DDACDA |
| requirements.txt | DBE076A7458C5788 |
| templates/index.html | 769ECE1C80627DB7 |
| modules/data_adapter.py | 0792E5006D7DCED9 |
| modules/advisor.py | CA1857B0F6452B20 |
| modules/analysis_engine.py | DF71A6FE4FD7685D |
| modules/alert_engine.py | 053F0CDB4DA62385 |
| database/db_manager.py | 76407851552761F5 |

---

## 四、QA 交接说明（独立验收指引）

### 4.1 验收标准映射

| 任务书验收 | QA 执行方式建议 |
|---|---|
| 1. 代码级核查 | grep `_THS_REQUEST_TIMEOUT = 60`（L1099，位于 `_THS_FAIL_THRESHOLD` 与 `_EM_CONSECUTIVE_FAIL_COUNT` 之间）；grep `_call_with_timeout(` 函数体内 3 处；`_try_ths_primary`/`_try_ths_rank_backup` 函数体 diff 为空（对比开发前版本或本报告 V1 特征断言） |
| 2. 编译验证 | `python -m py_compile modules/data_collector.py` |
| 3. 超时保护（65s 断言） | **推荐 mock 组合**：`_try_ths_primary = lambda: time.sleep(120)`（挂死）、`_try_ths_rank_backup = lambda: None`（快速失败）→ 期望 elapsed ≈ **60s（<65s）**，返回 None，日志含 `主接口 超时(60s)，跳过`，且**不含** `主接口(重试)`（M-2 生效）。⚠️ 注意：若只 mock 主接口、不 mock 备选，备选会真实请求网络导致耗时不可控；若 mock 重试/备选全部 sleep(120) 且**无 M-2**，总耗时 185s 将击穿断言——本实现已实现 M-2 消除该风险 |
| 4. 降级链路 | mock 主/备选均快速失败（返回 None，非超时）→ 断言日志按序：`主接口失败，5秒后重试1次` → `重试仍失败，尝试备选接口` → `全部接口失败，连续失败计数=1`；返回 None；`_THS_CAPITAL_CACHE['data']` 为 None；调用方回退 `_em_batch_collect` 建议在运行实例复核（daily_report.py 未改动） |
| 5. 正常路径 | mock 主接口返回 DataFrame → 同一对象透传、计数重置 0、缓存写入、elapsed < 5s |
| 6. 进程退出 | 子进程启动挂死 daemon 线程（`sleep(3600)`）后主线程结束 → 进程 10s 内退出（实测 2.04s） |
| 7. 零改动 | 哈希比对：开发后所有关键文件与本节第三节快照一致；`git status` 确认本批次新增 M 项仅 `modules/data_collector.py` |

### 4.2 M-2 语义提醒（重要）

- **超时**（hang，60s 无响应）→ 跳过重试（含 5s 间隔），直接尝试备选；THS 阶段上界 120s
- **普通失败**（抛异常/空数据/返回 None）→ 维持 FIX-B 原行为：5s 等待 + 重试 1 次
- 两种路径最终失败时均：`_THS_CONSECUTIVE_FAIL_COUNT += 1`、不写缓存、返回 None → 调用方回退 EM

### 4.3 开发环境备注

- 临时自验脚本已删除（红线 2 范围收敛），QA 需自行构造 mock 测试；上方 mock 组合为验证过的推荐配置
- 本批次未重启运行中的 app.py 进程（运行中 PID 46172 仍为旧代码，须重启后 THS 超时保护生效）
- 自验运行环境：Python 3.12（`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`）
