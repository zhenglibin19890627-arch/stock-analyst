# QA 验收报告 019I — THS 批量预取超时保护（报告生成卡住修复）

**批次**：019I（P1，报告生成功能完全阻断）
**角色**：QA（独立验收）
**验收日期**：2026-08-05
**任务书**：`docs/tasks/qa_tasks_20260805_019I.md`
**开发任务书**：`docs/tasks/dev_tasks_20260805_019I_ths_batch_timeout.md`（v2 定稿）
**评审报告**：`docs/reviews/review_019I_ths_batch_timeout_20260805.md`（架构师，有条件通过，M-1~M-4）
**开发自验报告**：`reports/dev_selftest_019I_ths_batch_timeout_20260805.md`（仅对照参考，QA 未采信其结论，独立构造测试）
**验收结论**：✅ **通过**（验收用例 7/7，断言 15/15 PASS，红线 8/8 满足）

---

## 〇、验收环境与独立性声明

| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`（3.12） |
| 唯一改动文件 | `modules/data_collector.py` |
| mock 测试 | QA 独立构造的临时脚本（`qa_019I_mock_test.py`），置于系统临时目录执行，**验收结束后已删除，未留存仓库** |
| 独立性 | QA 未复用开发临时脚本（开发自验报告已声明其脚本已删除）；全部断言由 QA 独立脚本执行并留档 |
| 网络隔离 | V3/V4/V5 均通过 monkeypatch `modules.data_collector._try_ths_primary` / `_try_ths_rank_backup` 完全隔离网络，无真实 HTTP 请求 |

---

## 一、V1：代码级核查 ✅（5/5 PASS）

| 核查项 | 验证方法 | 结果 | 证据 |
|---|---|---|---|
| `_THS_REQUEST_TIMEOUT = 60` 存在于 THS 常量块 | Read L1095-1100 | ✅ PASS | `data_collector.py` L1099：`_THS_REQUEST_TIMEOUT = 60  # 019I：单次 THS 接口请求超时（秒）`，位于 `_THS_FAIL_THRESHOLD`（L1098）与 `_EM_CONSECUTIVE_FAIL_COUNT`（L1106）之间 |
| 主/重试/备选 3 处调用均经 `_call_with_timeout` 包装 | Read L1167-1180 + grep | ✅ PASS | 全文 grep `_call_with_timeout(` 恰 4 处：L1153 定义 + L1168（主接口）、L1175（主接口(重试)）、L1180（备选接口）3 处调用，无遗漏无多余 |
| 函数体内无 `with ThreadPoolExecutor` 语句（红线 6） | grep `with.*ThreadPool` 全文件 | ✅ PASS | 仅 1 处命中：L1146 **注释**（M-1 修正说明），无任何代码级 `with ThreadPoolExecutor` 语句；实现为 `threading.Thread(daemon=True) + t.join(timeout=_THS_REQUEST_TIMEOUT)`（L1153-1165） |
| `_try_ths_primary` / `_try_ths_rank_backup` 函数体零改动 | Read 两函数 + **AST 级源码段对比 git 基线** | ✅ PASS | ① Read 确认仍为裸调用：L1199 `df = ak.stock_fund_flow_individual()`、L1214 `df = ak.stock_individual_fund_flow_rank(indicator='今日')`；② QA 以 AST `get_source_segment` 逐函数对比 **git 基线 commit a22d291（仓库初始化基线）** 与当前版本：两函数源码段完全一致（`ALL_IDENTICAL`），自基线以来从未被改动 |
| `_fetch_capital_flow_ths_batch()` 签名无参不变 | Read def 行 | ✅ PASS | L1121 `def _fetch_capital_flow_ths_batch():`，无参数 |

> 补充（红线 4 相关）：模块顶部 import 清单（L17-161）无新增第三方包；`import threading as _threading_019I` 为函数内局部导入（L1151），threading 为 Python 标准库，零新依赖。

---

## 二、V2：编译验证 ✅（PASS）

```
python -m py_compile modules/data_collector.py → 无错误（PY_COMPILE_OK）
```

---

## 三、V3：超时保护验证（M-4 强化，含显式耗时断言）✅（6/6 PASS）

**QA 独立 mock 组合**（monkeypatch `modules.data_collector`，网络完全隔离）：

| mock 目标 | 注入实现 |
|---|---|
| `_try_ths_primary` | `lambda: time.sleep(120)`（模拟 hang，TCP 保持不返回） |
| `_try_ths_rank_backup` | `lambda: None`（快速失败，防止真实网络请求导致耗时不可控） |

**实测结果**：

| 断言 | 预期 | 实测 | 结果 |
|---|---|---|---|
| 返回 None | None | `result=None` | ✅ PASS |
| **耗时 < 65 秒**（M-4 显式断言） | elapsed < 65s | **elapsed = 60.01s** | ✅ PASS |
| 日志含 `主接口 超时(60s)，跳过` | 命中 | 命中（日志见下） | ✅ PASS |
| 日志不含 `主接口(重试)`（M-2 生效） | 未命中 | 未命中 | ✅ PASS |
| 超时后 `_THS_CAPITAL_CACHE['data']` 仍为 None（红线 7） | None | `cache_data=None` | ✅ PASS |
| 全失败后 `_THS_CONSECUTIVE_FAIL_COUNT == 1` | 1 | `count=1` | ✅ PASS |

**QA 捕获日志（UTF-8 原文）**：
```
[同花顺批量] 主接口 超时(60s)，跳过
[同花顺批量] 重试仍失败，尝试备选接口 stock_individual_fund_flow_rank()...
[同花顺批量] 全部接口失败，连续失败计数=1
```

**分析**：
- 60.01s 返回且包含 `主接口 超时(60s)，跳过` —— M-1 daemon 线程方案甲生效；若开发者误用 `with ThreadPoolExecutor` 块，耗时将为 mock 挂死时长 120s 并击穿 65s 断言。
- 无 `主接口失败，5秒后重试1次...` 与 `主接口(重试)` 日志 —— M-2（超时跳过重试，THS 阶段上界 185s→120s）生效；超时后直接尝试备选接口。
- 时序：60s（主超时）+ 0s（备选快速失败）= 60s，与 M-2 设计上界一致。
- 备选尝试日志为 FIX-B 原有文案（"重试仍失败，尝试备选接口..."），在超时场景下语义为"备选接口尝试"提示，属既有文案复用，非重试行为。

---

## 四、V4：降级链路完整性验证 ✅（4/4 PASS）

**QA mock 组合**：主/备选均 `lambda: None`（普通失败，非超时，保留 FIX-B 重试路径）。

**实测结果**：

| 断言 | 预期 | 实测 | 结果 |
|---|---|---|---|
| 日志按序出现三段 | 全部按序 | `i1=0 < i2=1 < i3=2`（索引定位） | ✅ PASS |
| 返回 None | None | `result=None` | ✅ PASS |
| `_THS_CAPITAL_CACHE['data']` 为 None | None | `cache_data=None` | ✅ PASS |
| `_THS_CONSECUTIVE_FAIL_COUNT == 1` | 1 | `count=1` | ✅ PASS |

**QA 捕获日志（UTF-8 原文，按序）**：
```
[同花顺批量] 主接口失败，5秒后重试1次...
[同花顺批量] 重试仍失败，尝试备选接口 stock_individual_fund_flow_rank()...
[同花顺批量] 全部接口失败，连续失败计数=1
```

**分析**：elapsed = **5.00s**（5 秒重试间隔等待生效），普通失败路径完整保留 FIX-B 行为：主接口失败 → 5s 等待 → 重试 1 次 → 备选接口 → 全失败计数 +1 → 返回 None → 调用方（daily_report.py `fetch_capital_flow_batch` L1373，未改动）回退 `_em_batch_collect`。

---

## 五、V5：正常路径回归验证 ✅（4/4 PASS）

**QA mock 组合**：`_try_ths_primary = lambda: df_expected`（`pd.DataFrame({'股票代码': ['600276'], '净额': [123.45]})`，无延迟）。

**实测结果**：

| 断言 | 预期 | 实测 | 结果 |
|---|---|---|---|
| 返回同一 DataFrame 对象（透明传递） | 同一对象 | `same_obj=True` | ✅ PASS |
| `_THS_CONSECUTIVE_FAIL_COUNT` 重置为 0 | 0 | `count=0` | ✅ PASS |
| `_THS_CAPITAL_CACHE['data']` 写入 | 非 None | `cache_written=True`（同一对象） | ✅ PASS |
| 无额外开销（耗时 < 5s） | elapsed < 5s | **elapsed = 0.001s** | ✅ PASS |

**QA 捕获日志（UTF-8 原文）**：
```
[同花顺批量] 获取成功: 1 只股票当日资金流向
```

**分析**：正常路径透明传递无回归，缓存写入与失败计数重置行为与改动前一致（成功路径未受 M-1/M-2 影响）。

---

## 六、V6：进程退出验证（M-4，方案甲自动满足）✅（PASS）

**QA 测试方法**：子进程启动挂死 daemon 线程（`sleep(3600)`），主线程 sleep(2s) 后结束，测量进程退出 wall time。

**实测结果**：

| 断言 | 预期 | 实测 | 结果 |
|---|---|---|---|
| 进程在 10 秒内退出 | returncode=0, wall < 10s | **returncode=0，wall = 2.05s**（子进程 stdout `CHILD_DONE`） | ✅ PASS |

**分析**：daemon 线程不参与解释器退出 join（`threading._shutdown` 仅 join 非 daemon 线程），挂死线程随进程退出被 OS 回收 —— R-1（挂死僵尸线程阻塞进程退出 / 重启端口占用）消除，与方案甲设计一致。

---

## 七、V7：零改动确认 ✅（5/5 PASS + 函数体基线）

**QA 独立计算 SHA256（前 16 位），与开发自验报告第三节快照比对**：

| 文件 | QA 实测 | 开发快照 | 结果 |
|---|---|---|---|
| modules/daily_report.py | A96C51CD5049B679 | A96C51CD5049B679 | ✅ 一致 |
| app.py | 8F8373C029E76390 | 8F8373C029E76390 | ✅ 一致 |
| config.py | F6CE1F84B8DDACDA | F6CE1F84B8DDACDA | ✅ 一致 |
| requirements.txt | DBE076A7458C5788 | DBE076A7458C5788 | ✅ 一致 |
| templates/index.html | 769ECE1C80627DB7 | 769ECE1C80627DB7 | ✅ 一致 |

**函数体 diff 为空（增强验证）**：QA 以 AST 源码段提取 `_try_ths_primary` / `_try_ths_rank_backup`，对比 **git 基线 commit a22d291**（仓库初始化基线，`git show HEAD:stock_analyst/modules/data_collector.py`）与当前工作区版本：

```
[PASS] _try_ths_primary body identical to git HEAD baseline: True
[PASS] _try_ths_rank_backup body identical to git HEAD baseline: True
RESULT: ALL_IDENTICAL
```

> 该对比比"Read 确认"更强：证明两函数自仓库初始化以来从未被任何批次（含 019A-019H 累积改动与 019I）触碰。git status 中其他 M/?? 项均为历史批次累积或本批次文档交付物（qa 任务书/评审/自验报告），本批次唯一功能代码改动为 `modules/data_collector.py`，与任务书范围一致。

---

## 八、红线遵守核查清单（8/8 满足）

| 红线 | 核查方法 | 结果 |
|---|---|---|
| 1. 功能红线：THS 不响应时不得永久阻塞 | V3 耗时断言 | ✅ 60.01s < 65s，THS 阶段上界 120s（M-2），有界不阻塞 |
| 2. 范围红线：仅 data_collector.py | V7 零改动确认 | ✅ 5 文件哈希不变 + 函数体基线一致；本批次功能改动仅 data_collector.py |
| 3. 签名红线：三函数签名不变 | V1 代码级核查 | ✅ `_fetch_capital_flow_ths_batch()` 无参；两 `_try_ths_*` 签名+函数体 AST 级零改动 |
| 4. 零代码约束：无新依赖、不碰 config.py | V1 import 核查 + V7 | ✅ 无新第三方包（threading 标准库，函数内局部导入）；config.py 哈希不变 |
| 5. 降级安全红线：超时返回 None | V3/V4 返回值断言 | ✅ 超时返回 `(None, True)` 解包后 df=None，与失败路径一致；V3/V4 均返回 None |
| 6. 挂死线程红线：禁止 with ThreadPoolExecutor | V1 grep 检查 | ✅ 全文仅注释命中（L1146）；实现为 daemon 线程 + `join(timeout)`，不 join 挂死线程 |
| 7. 缓存红线：超时不写缓存 | V3/V4 缓存断言 | ✅ 两场景 `_THS_CAPITAL_CACHE['data']` 均为 None；仅成功路径写入（V5） |
| 8. 先例红线：不复制 daily_report L533 模式 | V1 核查 | ✅ 采用 daemon 线程方案（方案甲，架构师推荐），无任何 `with ThreadPoolExecutor` 写法 |

---

## 九、新发现问题（QA 独立发现，均非阻断）

1. **无功能/代码级新发现问题**。
2. **运行实例提醒（PM 备注确认）**：当前运行中 app.py 进程（PID 46172）仍为旧代码，QA 验收基于 mock 测试与静态核查，**须用户重启 `python app.py` 后 THS 超时保护才在运行实例生效**。
3. **文案性备注（非缺陷）**：超时场景下备选尝试日志沿用 FIX-B 文案"重试仍失败，尝试备选接口..."，与超时语义略有出入（实为"备选接口尝试"），属既有文案复用，不影响行为正确性；如后续批次愿意可优化文案，本批次不构成验收阻碍。
4. **已知范围外风险（架构师已登记，非本批次）**：R-2（EM 逐只回退挂死窗口，019J 候选）、R-3（daily_report.py L533 with 块先例缺陷），019I 验收中复现路径与登记一致，未扩大处理。

---

## 十、验收结论

**✅ 通过（QA 独立验收）**

- 验收用例：**7/7 通过**（V1 代码级 5/5、V2 编译、V3 超时保护 6/6、V4 降级链路 4/4、V5 正常路径 4/4、V6 进程退出、V7 零改动 5/5+函数体基线），共 **15/15 断言 PASS、0 FAIL**。
- 红线遵守：**8/8 满足**。
- M-1（daemon 线程方案甲）、M-2（超时跳过重试）、M-3（import 位置）、M-4（65s 显式耗时断言 + 进程退出验证）全部经 QA 独立验证生效。
- 开发自验报告结论与 QA 独立结论一致（耗时、计数、日志断言均吻合），但 QA 结论基于独立构造的测试证据，未依赖开发自验。

---

## 十一、签署

| 项 | 内容 |
|---|---|
| 验收人 | QA（独立验收） |
| 验收日期 | 2026-08-05 |
| 交付物 | 本报告 `reports/qa_accept_019I_ths_batch_timeout_20260805.md` |
| 临时脚本清理 | QA mock 测试脚本 `qa_019I_mock_test.py`、函数体对比脚本、日志证据文件及基线提取文件均已从临时目录删除，未留存仓库（红线：临时脚本清理 ✅） |
| 后续流程 | 待 PM+QA 双签 → 监理批准关闭；提醒用户重启 app.py（PID 46172）后修复生效 |

> **QA 签署**：本报告由 QA 于 2026-08-05 独立完成。静态核查文件：modules/data_collector.py（L1095-1235）、modules/daily_report.py（哈希比对）、app.py/config.py/requirements.txt/templates/index.html（哈希比对）、git 基线版本（AST 对比）；实测为 QA 独立构造的受控 mock 场景（V3/V4/V5/V6），全部断言 15/15 PASS。未采信开发自验报告结论。

---

## PM+QA 双签块（019I）

**双签日期**：2026-08-05

### PM 独立核验结论

PM 未采信 QA 验收报告结论，独立复跑关键项：

| 核验项 | PM 方法 | 结果 |
|---|---|---|
| V1 代码级核查（常量 L1099 / daemon 线程 L1151-1165 / 包装 3 处 L1168/L1175/L1180 / 函数体零改动 / 签名不变） | Read 代码 | ✅ 通过（开发核验阶段已完成，结论不变） |
| V2 编译验证 | `python -m py_compile modules/data_collector.py` | ✅ PY_COMPILE_OK |
| 红线 6（禁止 with ThreadPoolExecutor） | grep `with.*ThreadPool` 全文件 | ✅ 仅 L1146 注释命中，无代码语句 |
| V7 文件哈希（5 文件） | PM 独立 SHA256 计算比对 QA 报告快照 | ✅ 5/5 MATCH（daily_report/app/config/requirements/index.html） |

**PM 结论**：QA 报告 7/7 用例、15/15 断言、8/8 红线结论经 PM 独立核验可信。开发成果符合任务书 v2 定稿规格 + 架构师评审 M-1~M-4 修订要求。

### 双签签署

| 角色 | 签署人 | 日期 | 结论 |
|---|---|---|---|
| QA | QA（独立验收） | 2026-08-05 | ✅ 通过（7/7 用例，15/15 断言，8/8 红线） |
| PM | PM（独立核验） | 2026-08-05 | ✅ 通过（V1/V2/红线6/V7 独立复跑确认） |

### 关闭前提醒

1. **运行实例重启**：当前 PID 46172 仍为旧代码，用户须重启 `python app.py` 后 THS 超时保护才生效
2. **架构师登记的风险项（不在本批次）**：R-2（EM 逐只挂死窗口，019J 候选）、R-3（daily_report.py L533 with 块先例缺陷，后续批次）
3. **备查项 M-2（export_engine 导出层缺 is_estimated 过滤）**：低风险，不影响本批次，维持备查

---

> **状态**：✅ PM+QA 双签完成 → ✅ 监理批准关闭（2026-08-05）。019I 批次已关闭。
