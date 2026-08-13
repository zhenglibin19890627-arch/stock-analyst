# QA 验收报告 019G — 同花顺交易日校验 + 报告时间展示优化

**验收人**：QA 工程师（独立验收，不采信开发自验结论）
**验收日期**：2026-08-05
**批次**：019G（019F 后续）
**任务书**：`docs/tasks/dev_tasks_20260804_019G_time_display_ths_skip.md`（v2，含架构评审 M-1/M-2/M-3/M-4/R-2 修订）
**开发自验**：`reports/dev_selftest_019G_time_display_ths_skip_20260804.md`
**验收结论**：**✅ 通过（7/7 验收标准全 PASS）**

---

## 一、验收方法说明

QA 独立执行全量核查，**不采信开发自验结论**，逐项重新验证：

1. **源码级 Read 核查**：直接读取三个改动文件真实内容（data_collector.py L1356-1366 / index.html L4203、L4798、L4845 / app.py L1871、L1896、L1972）
2. **编译验证**：`py_compile` 两文件独立复跑
3. **M-4 monkeypatch 测试**：独立编写测试脚本（`%TEMP%\opencode\qa_019g_m4_test.py`，未入仓库），`mock.patch.object(dc, 'datetime', FakeDatetime)` 拦截 `now()`，并 mock `_fetch_capital_flow_ths_batch` / `_em_batch_collect` 隔离网络与 DB
4. **浏览器实测**：Playwright（chromium headless）启动真实浏览器访问 `http://127.0.0.1:5000`，验证看板与个股详情页实际渲染 + JS 错误监听
5. **范围核验**：019G 特征标记全库检索 + 文件最后写入时间戳比对
6. **DB 交叉验证**：SQLite 直接查询 `daily_reports` / `price_cache` 交叉核对 API 返回值

**服务重启说明**：验收前运行中的 app.py（PID 20824，启动于 08-04 18:13）为 019G 改动前的旧代码（watchlist-scores 响应无每股 `generated_at`，实测确认），按 PM 备注"改动后需重启 app.py 生效"由 QA 重启（PID 30488，08-05 08:29），重启后 API 返回每股 `generated_at`（29/29 只股票）。

---

## 二、逐项验收结果

### AC-1：交易日校验（任务 1）— ✅ 通过

**核查方式**：Read `modules/data_collector.py` L1356-1366 + M-4 monkeypatch 实测

**源码核验**（L1356-1366，与任务书方案逐字符一致）：
```python
    # 019G：交易日校验 — 周末（周六/周日）跳过 THS 批量预取（含补采），
    now = datetime.now(_CN_TZ)
    if now.weekday() >= 5:  # 5=周六, 6=周日
        logger.info(f'[同花顺批量] 非交易日（{now.strftime("%A")}），跳过 THS 批量预取（含补采）')
        return {
            'success_count': 0, 'fail_count': 0,
            'source': '同花顺批量(非交易日跳过)',
            'skipped': True, 'reason': 'non_trading_day'
        }
```
- 早退位置在 docstring 之后、`if not a_stock_symbols` 空列表分支之前（L1368）✅，早退先于 THS 请求与 DB 写入
- `datetime`（L24）/`_CN_TZ`（L29）均为模块内定义，无新增导入 ✅
- 仅 `weekday()` 判断，无节假日库 ✅
- 早退返回值含 `source` 键（M-2 契约统一）✅
- 函数签名 `fetch_capital_flow_batch(a_stock_symbols)` 不变 ✅

**M-4 monkeypatch 实测**（独立脚本，`FakeDatetime.now()` 返回固定时刻）：

| 场景 | 构造 | 断言 | 结果 |
|---|---|---|---|
| 周日 | `2026-08-02 10:00 +08:00`（weekday=6） | 返回 dict 含 `skipped=True`、`success_count=0`、`fail_count=0`、`source='同花顺批量(非交易日跳过)'`、`reason='non_trading_day'`；`_fetch_capital_flow_ths_batch` 未被调用；`_em_batch_collect` 亦未被调用（早退先于回退逻辑） | ✅ PASS |
| 周二回归 | `2026-08-04 10:00 +08:00`（weekday=1） | 返回 dict 无 `skipped` 键；`_fetch_capital_flow_ths_batch` 被调用（THS 不可用 → EM 回退 stub 返回） | ✅ PASS |

作用域控制（M-4 提示）：FakeDatetime 仅存在于 `mock.patch` 上下文内，模块内 `now_cn` / `today_str` 同受 patch 影响但不影响本测试断言；测试未触碰真实 DB。

### AC-2：个股详情页时间展示（任务 2）— ✅ 通过

**核查方式**：Read `index.html` L4203-4207 + 浏览器实测

- "评级时间"行已删除，改动后仅剩两行（L4203-4207）：
  - `报告生成于：_fmtGenTime(adviseData.generated_at)`（保留）
  - `最新收盘：adviseData.latest_close.toFixed(2)（latest_close_date）`（保留，if 守卫未动）
- 全文件"评级时间"剩余 3 处（L1118 筛选选项、L2698/L2745 批量分析结果表头）均为无关业务，个股详情页行已删 ✅
- `rating_date` 字段后端不删（API 返回不动），前端其余引用（L2347/L2635/L5667）均为无关业务 ✅

**浏览器实测**（Playwright，个股详情页 DOM 精确提取）：
- 评分卡时间信息区仅剩：`报告生成于：2026-08-05 08:32`、`评分引擎`、`数据完整度` 三行（截图证据：`.rating-time` 元素列表）✅ 无"评级时间"行
- "报告生成于"为分钟级格式 `YYYY-MM-DD HH:MM` ✅
- "最新收盘"行：实时引擎路径下实测显示 `最新收盘：45.52（2026-08-04）` ✅（保留且功能正常）；DB 快照路径（report-latest）无 `latest_close` 键属**既有行为**（app.py L1057 注释"report-latest 上下文中无 latest_close"，019G 未触碰该端点，非本批次回归）

### AC-3：总览看板"生成时间"列（任务 3）— ✅ 通过

**核查方式**：Read `index.html` L4798/L4845 + `app.py` L1871/L1896/L1972 + 浏览器实测 + DB 交叉验证

**后端数据源闭环**（M-1 方案 A）：
- 主查询 SELECT（app.py L1871）：末尾 `dr.generated_at` ✅
- 无报告降级分支（L1896）：`NULL as generated_at` ✅
- `stocks[]` 字典（L1972）：`'generated_at': r.get('generated_at')` ✅
- ETag（L1987）：仅排除顶层 `generated_at`，新增 `stocks[].generated_at` 进入 etag_payload 属正确行为（新报告→新时间→刷新缓存），与任务书一致 ✅

**浏览器实测**：
- 表头 9 列：`股票/引擎/评分/评级/较昨日/生成时间/行业/市值/操作`，"生成时间"位于"较昨日"与"行业"之间（下标 5）✅
- 每行生成时间分钟级格式：有报告行实测 `2026-08-04 17:47`、`2026-08-04 16:36`、`2026-08-04 17:00` 等（29/29 行均分钟级，首次实测）✅
- 空值显示"—"：无报告行实测 28 行全部显示 `—`（`_fmtGenTime` L5403 空值分支）✅
- 列数 8→9 一致；`dashSort`（按字段键排序）与 `dashApplyFilter`（数据过滤后整体重渲染）均无列下标依赖，实测点击"评分"表头排序 + 引擎筛选后列数仍 9，无需适配 ✅
- 新单元格含 `white-space:nowrap`（M-3）✅

### AC-4：编译验证 — ✅ 通过

```
python -m py_compile modules/data_collector.py  → 成功
python -m py_compile app.py                      → 成功
```

### AC-5：范围红线核验 — ✅ 通过

工作树存在历史批次（019A-019F）未提交改动，本批次核验 019G 增量：

| 核验项 | 方法 | 结果 |
|---|---|---|
| 019G 特征标记全库检索 | grep `019G\|非交易日跳过\|non_trading_day`（*.py） | 仅 `modules/data_collector.py` L1356/L1364/L1365 三处命中，app.py/index.html 改动无标记属正常（SELECT/字典/渲染行无需标记）✅ |
| 本批次 3 文件最后写入时间 | 文件 LastWriteTime | data_collector.py 08-05 08:14:45、app.py 08-05 08:15:00、index.html 08-05 08:15:08 — 同一批次窗口 ✅ |
| 范围外文件零改动 | LastWriteTime | analysis_engine（08-04 17:17，019F）、data_adapter/advisor/db_manager（08-04 01:04，019B）、scoring_engine（08-03）、daily_report（08-04 14:38，019E）、requirements.txt（08-03）— 均非 08-05 写入 ✅ |
| app.py 仅限 watchlist-scores 接口 | Read L1838-1993 | 3 处改动均在接口内（L1871/L1896/L1972），其余代码未动 ✅ |
| 签名红线 | Read L1342 | `fetch_capital_flow_batch(a_stock_symbols)` 不变 ✅ |
| requirements.txt 9 包 | Read | 未改动，零新依赖 ✅ |

### AC-6：前端无 JS 错误 — ✅ 通过

**浏览器实测**（Playwright 监听 console/pageerror，覆盖看板加载、排序、筛选、详情页跳转全流程）：
- `pageerror`（真实 JS 异常）：**0 条** ✅
- console error：仅 1 条 `Failed to load resource: 404`，经服务端 werkzeug 日志交叉确认唯一 404 为 `GET /favicon.ico`（浏览器默认请求，index.html 无 favicon 声明，属既有环境行为，与 019G 无关）✅
- 看板与详情页均正常渲染（表头 9 列、29 行数据、详情页评分卡完整）✅

### AC-7：M-4 QA 测试建议 — ✅ 通过

已按 M-4 方法执行（详见 AC-1 测试记录）：
- `mock.patch.object(dc, 'datetime', FakeDatetime)` 拦截 `now()`，作用域限于 patch 上下文（模块内 `now_cn`、`today_str` 同引用 `datetime`，M-4 提示已遵守）✅
- 断言 1：周日返回 dict 含 `skipped=True` ✅
- 断言 2：`_fetch_capital_flow_ths_batch` 未被调用 ✅

---

## 三、测试执行记录

```
V1 编译: python -m py_compile modules/data_collector.py; python -m py_compile app.py → 均成功
V2 M-4:  %TEMP%\opencode\qa_019g_m4_test.py → 场景1(周日) PASS + 场景2(周二回归) PASS，整体 ALL PASS
V3 前端: %TEMP%\opencode\qa_019g_frontend_test.py / _v3.py（Playwright chromium headless）
   - 看板表头 9 列、生成时间列分钟级、空值 28 行"—"、dashSort 排序、dashApplyFilter 筛选
   - 详情页无"评级时间"行、"报告生成于"分钟级、pageerror=0
V4 API:  /api/portfolio/watchlist-scores → 29 只股票全部含 generated_at（2026-08-04T17:47:25+08:00 等）
V5 DB:   SQLite 直查 daily_reports 交叉核对（08-05 唯一报告与 API/页面显示一致）
```

---

## 四、红线遵守情况

| 红线 | QA 核查结果 |
|---|---|
| 范围红线（仅 3 文件） | ✅ 019G 标记仅 data_collector.py；三文件写入时间同批次窗口；范围外文件零 08-05 写入 |
| 签名红线 | ✅ `fetch_capital_flow_batch(a_stock_symbols)` 不变 |
| 评分纯净红线 | ✅ 本批次未触碰评分链路（analysis_engine/scoring_engine/data_adapter/advisor 零改动） |
| 零代码约束 | ✅ 仅 `weekday()` 内置方法，无节假日库；requirements.txt 维持 9 包 |
| 数据安全红线 | ✅ 019G 代码零 SQL 变更；QA 未执行任何删除/覆盖操作 |

---

## 五、QA 测试过程说明（透明记录）

1. **服务重启**：验收需加载 019G 新代码，QA 将旧进程（PID 20824）重启为新进程（PID 30488），符合 PM"改动后需重启生效"备注。
2. **B11 自动生成一条 08-05 报告**：QA 浏览器实测点击个股详情（快手 01024.HK，stock_id=41）时，系统按既有 B11-DETAIL-LOAD 行为（app.py L898/L923-941：当日无报告自动生成）在 08:32:11 生成了一条 2026-08-05 的 `daily_reports` 记录。该行由系统正常业务逻辑产生，QA 未执行任何删除/覆盖，16:10 每日批次将正常重生成 08-05 全量报告。此状态不影响本批次验收结论。
3. **favicon 404**：浏览器默认请求 `/favicon.ico` 404，index.html 无 favicon 声明，既有环境行为，与 019G 无关。

---

## 六、验收结论

**✅ 通过（7/7）。**

019G 批次三项改动经 QA 独立全量验收：
- 任务 1（交易日校验）：源码与任务书 v2 逐字符一致，M-4 monkeypatch 实测周日 `skipped=True` 且 THS 未调用、周二正常回归
- 任务 2（删除"评级时间"行）：详情页浏览器实测无该行，"报告生成于"分钟级，"最新收盘"保留且功能正常
- 任务 3（看板"生成时间"列）：数据源闭环（app.py 3 处）+ 前端渲染闭环（表头/行）全部实测通过，空值"—"，列数 8→9 且 dashSort/dashApplyFilter 无需适配
- 编译、范围红线、前端 JS 错误三项核验全部通过

**提请 PM+QA 双签 → 监理批准关闭。**

---

## 八、PM 独立核验记录（双签前 PM 复核）

**核验日期**：2026-08-05
**核验方式**：PM 独立复跑 + 代码级抽查（不采信 QA 结论，不采信开发自验）

| 编号 | 核验项 | 方法 | 结果 |
|---|---|---|---|
| PM-V1 | 编译验证 | `py_compile data_collector.py` + `py_compile app.py` | ✅ 双通过 |
| PM-V2 | 任务 1 交易日校验 | Read data_collector.py L1356-1366 | ✅ weekday() 判断在位、source 键已补、日志含“含补采” |
| PM-V3 | 任务 2 删除评级时间行 | Read index.html L4203 | ✅ 直接是“报告生成于”，无“评级时间”行 |
| PM-V4 | 任务 3a app.py 数据源 | Read app.py L1871/L1896/L1972 | ✅ 主查询含 dr.generated_at、降级分支含 NULL as generated_at、stocks 字典含 generated_at |
| PM-V5 | 任务 3b 看板新列 | Grep index.html L4798/L4845 | ✅ 表头“生成时间”、单元格含 _fmtGenTime(st.generated_at) + white-space:nowrap |
| PM-V6 | 签名不变 | Read data_collector.py L1342 | ✅ fetch_capital_flow_batch(a_stock_symbols) 签名不变 |
| PM-V7 | app.py 进程 | Get-Process | ✅ PID 30488 在线（QA 重启，019G 代码已加载） |

**PM 核验结论**：7 项全部通过，与 QA 验收结论一致。双签认可。

**PM 签字**：✅ PM 已核验，认可 QA 验收结论，双签完成。
**QA 签字**：✅ QA 独立验收 7/7 PASS，双签完成。

---

## 九、批次关闭记录

**批次编号**：019G
**关闭日期**：2026-08-05
**关闭状态**：✅ 监理已批准关闭（2026-08-05）

### 流程 completeness 核查

| 步骤 | 状态 | 文档 |
|---|---|---|
| PM 签发任务书 v1 | ✅ | `docs/tasks/dev_tasks_20260804_019G_time_display_ths_skip.md` |
| 架构师评审 | ✅ 有条件通过（M-1/M-2/M-3/M-4 已修订 v2） | `docs/reviews/review_019G_time_display_ths_skip_20260804.md` |
| 监理批准 | ✅ | 2026-08-04 监理裁定 |
| 开发执行 + 自验 | ✅ | `reports/dev_selftest_019G_time_display_ths_skip_20260804.md` |
| QA 独立验收 | ✅ 7/7 PASS | `reports/qa_accept_019G_time_display_ths_skip_20260804.md` |
| PM+QA 双签 | ✅ | 本报告第八节 |
| 监理批准关闭 | ✅ 已批准（2026-08-05） | — |

### 019G 资产清单（后续批次需知悉）

1. `data_collector.py` L1356-1366 交易日校验 — 周末跳过 THS 批量预取（含补采）
2. `index.html` L4203 删除“评级时间”行 — 问题②③合并解决
3. `app.py` L1871/L1896/L1972 watchlist-scores 数据源扩展 — 每股 generated_at 字段
4. `index.html` L4798/L4845 看板新增“生成时间”列 — M-3 nowrap
5. 范围外已知项（备查）：`alert_engine.py` L201 预警缺 is_estimated 过滤（中风险，范围外，019F 架构评审 R-1 备查）

**019G 批次已关闭。**

---

## 七、附件

- 任务书：`docs/tasks/dev_tasks_20260804_019G_time_display_ths_skip.md`（v2）
- 架构评审：`docs/reviews/review_019G_time_display_ths_skip_20260804.md`
- 开发自验：`reports/dev_selftest_019G_time_display_ths_skip_20260804.md`
- QA 测试脚本（临时目录，未入仓库）：`%TEMP%\opencode\qa_019g_m4_test.py`、`qa_019g_frontend_test.py`、`qa_019g_frontend_v3.py`
- 服务端日志：`logs/app.log`（werkzeug 请求记录）、`logs/qa_019g_restart.log`（重启记录）
