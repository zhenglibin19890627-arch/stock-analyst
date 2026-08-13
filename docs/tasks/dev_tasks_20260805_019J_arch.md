# DEV-TASKS-20260805-019J-ARCH：019J 单只处理超时保护修复（R-3）— 架构方案评审任务书

> **签发人**：PM  | **签发日期**：2026-08-05 | **状态**：待架构师执行

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：架构师

**职责边界**：
- 复核 PM 签发的 019J 开发任务书（`docs/tasks/dev_tasks_20260805_019J_single_stock_timeout_fix.md`）
- 对每个决策点给出明确裁定 + 理由
- **不编码、不验收、不写功能代码**
- 交付物：`docs/reviews/review_019J_single_stock_timeout_fix_20260805.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 架构师仅做方案评审，不执行任何代码修改
- PM 产出的任务书仅供参考，架构师须独立 Read 代码核验，不采信 PM 结论

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 技术栈 | Python + Flask + SQLite + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包） |
| 前序批次 | 019I 已关闭（THS 批量预取超时保护，daemon 线程方案甲实证有效）；019J 为 R-3 缺陷修复 |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 交付物 | `docs/reviews/review_019J_single_stock_timeout_fix_20260805.md` |

---

## 一、需求背景

### 1.1 缺陷描述（R-3，019I 已登记）

`modules/daily_report.py` L531-570 单只处理超时控制使用 `with ThreadPoolExecutor(max_workers=1)` 块。当 `future.result(timeout=90)` 抛 TimeoutError 后，代码 `continue` 位于 with 块内部——with 块退出时 `__exit__` 调用 `shutdown(wait=True)` **join 仍在运行的 worker 线程**，主线程阻塞至 worker 自行完成。超时保护形同虚设；若 worker 永久挂死（无超时 socket 调用），报告线程永久卡死。

**日志实证（2026-08-05 16:14 批次）**：
- 600276：16:23:21 超时宣布跳过 → 日志空白 5 分 20 秒 → 16:28:41 worker 自行完成评分（63.1 持有观望）→ 16:28:42 才继续 2/29
- 000333：16:30:40 超时 → 空白 8 分 30 秒 → 16:39:10 才继续 5/29
- 批次结果：成功13/失败16 耗时1845s，1800s 整体预算耗尽截断，剩余 13 只未处理

**同款缺陷已在 019I 实证**：019I 架构师运行时实验 + PM 独立复现确认——`with ThreadPoolExecutor` 的 `shutdown(wait=True)` join 挂死线程，任何"超时保护"用 with 块实现均属无效修复。019I 红线 8 登记"daily_report.py L533 该模式自身携带同一缺陷，下一批次修复时严禁复制该模式"。

### 1.2 关键代码位置（评审必读，请独立 Read 核验）

| 位置 | 说明 |
|---|---|
| `modules/daily_report.py` L531-570 | **本批次核心改动块**：单只超时控制（with ThreadPoolExecutor + future.result(timeout=90) + 超时 continue） |
| `modules/daily_report.py` L323-439 | `_process_single_stock()` — worker 函数（任务书要求零改动） |
| `modules/daily_report.py` L410-426 | worker 内部 `_save_report`（DELETE+INSERT，超时后 worker 迟到完成会覆盖 failed 记录为 ok——数据自愈） |
| `modules/daily_report.py` L504-511 | 批次整体超时软检查（BATCH_TIMEOUT_SECONDS=1800，不受影响） |
| `modules/daily_report.py` L598-632 | 外层异常处理（线程异常不传播，需显式捕获设计） |
| `modules/daily_report.py` L25 | `import threading`（已存在） |
| `config.py` L113-117 | `STOCK_TIMEOUT_SECONDS=90` / `BATCH_TIMEOUT_SECONDS=1800` |
| `modules/data_collector.py` L1094-1211 | 019I 修复先例：`_THS_REQUEST_TIMEOUT=60` + daemon 线程 `_call_with_timeout` 方案甲 |

### 1.3 PM 任务书核心方案

将 L531-570 的 `with ThreadPoolExecutor(max_workers=1)` 块替换为：

```python
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
    # 超时：写 failed 记录 + results.append + continue，不等待 worker
    ...
if box.get('exc') is not None:
    raise box['exc']          # 保持现有异常路径语义
result = box['r']
```

语义要求：超时立即跳过（不 join）；worker 线程内异常显式捕获存 box（线程异常不自动传播）；成功路径处理逻辑零改动；保留 worker 迟到自愈（后台 daemon 线程完成后 _save_report 覆盖 failed→ok）。

---

## 二、评审决策点（请逐项裁定）

### A-1：超时保护方案选型（核心）

PM 方案：daemon 线程 `threading.Thread(daemon=True) + t.join(timeout=STOCK_TIMEOUT_SECONDS)`，超时后 `continue` 不等待。

**架构师请核验**：
- Read `daily_report.py` L531-570，确认 `continue` 位于 with 块内部（缺陷机制成立）
- 确认 daemon 线程方案在 Windows + Python 3.12 下语义正确（超时立即返回、不 join、进程退出不阻塞）
- 评估 `box['exc']` 异常捕获模式是否是最简等效方案；是否有更优实现（如 `future = executor.submit(...)` + 显式 `executor.shutdown(wait=False)` 不推荐项复核）
- 注意：与 019I 方案甲保持一致性（可复用 `_call_with_timeout` 模式思路，但本处需处理返回值 + 异常 + continue 三态）

**裁定**：采纳 / 修改 / 否决 + 理由

### A-2：worker 迟到自愈行为是否保留

08-05 实证：600276 超时后 worker 仍于 16:28:41 完成评分，`_save_report`（DELETE+INSERT）覆盖 failed 记录为 ok（id 1043→1044）。PM 任务书要求保留该行为（防丢分）。

**架构师请核验**：
- 保留该行为是否有竞态风险（主线程已 continue 处理下一只，worker 后台写库是否安全——SQLite 锁 busy_timeout=10s 是否覆盖）
- 是否应在超时时主动放弃 worker 结果（如标记超时后 worker 写入无效），还是接受自愈
- 若保留，QA 验收"覆盖后同 stock 同日仅 1 条记录"是否足够；是否需要防"intraday/daily 类型互相覆盖"的检查（_save_report 的 DELETE 条件含 report_type）

**裁定**：保留自愈 / 放弃 worker 结果 / 需补充防护 + 理由

### A-3：线程泄漏与并发上限

每次超时产生 1 个后台 daemon 线程（挂死后不回收，直至进程退出），批次 29 只最坏 29 个。

**架构师请核验**：
- 与 019I 评估（每批次最多 3 个 THS 线程）相比，本处最坏 29 个，是否可接受
- 是否需要泄漏防护（如线程计数上限、超时后不再产生新 worker）
- 后台 worker 并发写库是否与主线程/其他线程（预警扫描、优化器）产生锁竞争

**裁定**：可接受 / 需额外处理 + 理由

### A-4：R-2 是否纳入本批次（重要）

019I 架构评审登记 **R-2（中）**：`_em_batch_collect` 单只调用无超时包装，挂死可击穿 600s 软超时（data_collector.py EM 逐只回退链路）。

**架构师请核验**：
- Read `data_collector.py` `_em_batch_collect` 相关代码，确认挂死窗口是否仍存在
- 裁定：R-2 纳入 019J 一并修复 / 维持登记后续批次 / 评估后判定非实际风险
- 注意：今日 EM 接口全失败场景下熔断机制工作正常（连续失败 5 次 → 冷却 60s → 熔断终止回退），但单只挂死窗口未实测

**裁定**：纳入本批次 / 维持登记 / 降级销项 + 理由

### A-5：超时后 `_save_report` 写库的线程安全

超时分支在主线程写 failed 记录；同期后台 worker 可能稍后对同一 stock 写 ok 记录（DELETE+INSERT）。两处均操作 SQLite。

**架构师请核验**：
- `_save_report` 的事务/锁行为（db_manager busy_timeout=10s）是否足够
- 是否存在主线程写 failed 与 worker 写 ok 交错导致最终状态不确定的窗口
- 是否需要加幂等约束（如 report_type+stock_id+report_date 唯一索引）

**裁定**：可接受 / 需防护 + 理由

### A-6：范围与红线确认

任务书第五节红线：M-1 严禁 with 块、范围仅 daily_report.py L531-570、语义不变、签名不变、零代码约束、数据自愈保留、进程退出不阻塞。

**架构师请核验**：
- 红线是否完备
- 是否有遗漏风险点（如 `_update_progress_file` 并发、`_generate_lock` 防抖与线程退出关系）
- 改动范围是否过窄（是否应同步处理盘中报告 intraday 路径——同一函数 generate_daily_report 已覆盖两种 report_type，确认无需额外改动）

**裁定**：完备 / 需补充 + 详情

### A-7：验收标准充分性

任务书验收标准 8 条：代码核查（with 块 0 处）、py_compile、超时耗时断言（95s 内）、worker 自愈验证、异常路径、成功路径回归、进程退出验证、零改动确认。

**架构师请核验**：
- 耗时断言 95s 是否合理（STOCK_TIMEOUT_SECONDS=90 + 容差 5s）
- 是否需补充并发/幂等类断言（结合 A-2/A-5 裁定）
- QA 独立窗口 mock `_process_single_stock` 的方式是否可行（函数可替换性）

**裁定**：充分 / 需补充 + 详情

---

## 三、交付物要求

`docs/reviews/review_019J_single_stock_timeout_fix_20260805.md`，含：

1. **逐决策点裁定**（A-1 ~ A-7，每项采纳/修改/否决 + 理由）
2. **独立核验的代码证据**（关键结论须附 Read 到的代码行号和内容）
3. **新发现的风险项**（R-x 编号，如有）
4. **评审结论**（通过 / 有条件通过 / 不通过）
5. **若裁定需修订任务书**，明确列出修订项（M-x 编号），PM 将据此修订任务书后交付开发

---

## 四、PM 备注

1. **本批次 PM 未越权评审**：PM 仅完成了日志分析、根因定位与实证核验（DB 记录 id 1043 failed→1044 ok 覆盖、app.log 时间线），未自行产出 review 文档。架构师请以完全独立视角评审。
2. **根因已由日志+DB 实锤**：600276/000333 两次超时后阻塞窗口实证（5m20s/8m30s），批次 1845s 超 1800s 预算截断。架构师评审重点是**方案选型、自愈行为取舍、R-2 是否纳入**，而非根因复核。
3. **与 019I 方案甲的一致性**：019I 已在生产实证有效（THS 预取 5199 只 45s 完成）。本批次沿用同一线程模式，但调用点语义更复杂（需处理返回值、异常、continue 三态），请架构师评估 box 模式或更优写法。
4. **紧迫性**：R-3 属"迟早出事"型隐患——今日 3 次超时均侥幸恢复（worker 最终完成），若 worker 永久挂死则整份报告永久无法生成。建议尽快评审以便开发执行。
5. **R-2 的裁量空间**：PM 倾向 R-2 维持登记（与本批次改动点不同模块，一次变更一个缺陷面），但尊重架构师独立裁定；若架构师认为同属"超时保护缺失"应一并处理，PM 将扩充任务书。
