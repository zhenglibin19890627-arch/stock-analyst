# 架构评审 019H — is_estimated 过滤补全（预警层 + 展示层）【架构师定稿】

**评审日期**：2026-08-05
**评审人**：架构师（独立复核，本版为定稿）
**任务书**：`docs/tasks/dev_tasks_20260805_019H_is_estimated过滤补全.md`（PM 签发 v1）
**初稿说明**：本文件初稿系 PM 越权代笔（2026-08-05）。架构师按独立性原则**独立 Read 代码逐项核验**，未采信 PM 结论。初稿全部结论经独立复核确认，另修订 3 项（M-3 范围扩大、R-1/R-2/R-3 新增）。修订处以【架构师修订】标注。
**评审结论**：⚠️ **有条件通过**（M-1 展示层方案修正 + M-2 范围外备查登记 + M-3 任务书事实性错误更正与扩大 + R-1/R-2/R-3 新增）

---

## 〇、评审摘要（决策点裁定总览）

| 决策点 | 内容 | 架构师裁定 | 与 PM 越权评审一致性 | 结论 |
|---|---|---|---|---|
| A-1 | alert_engine.py 预警过滤（任务书 Task 1） | ✅ 采纳，无修改 | 一致 | 通过 |
| A-2 | app.py 展示层（任务书 Task 2） | ✅ 采纳 PM 结论 (a)——**不改 app.py** | 一致 | 有条件通过（M-1） |
| A-3 | export_engine.py 导出过滤（PM 新发现 M-2） | 📌 登记备查（范围外，建议 019I） | 一致 | M-2 登记 |
| A-4 | 全仓 raw_capital_flow 读取入口完整性 | ✅ PM 14 处扫描无遗漏；无动态 SQL、无 ORM | 一致 | 通过 |
| A-5 | 范围与红线完备性 | ✅ 完备，补充 2 条红线（R-2/R-3 相关） | 一致 + 补充 | 通过（含补充） |

**核心结论**：
1. **M-1（经独立复核确认）**：任务书"前端未做视觉区分"系事实错误。前端 `index.html` L2481-2490 在 019E Task 4.1 中**已实现**表头级 + 行级双层估算标注。且 019E 架构评审（`review_019E_capital_fallback_20260804.md` R-3 行）已明确裁定展示层"**不过滤，前端按 is_estimated 字段标注**"——任务书方案 A 与 019E 既定设计冲突。**裁定：不改 app.py**。
2. **M-2（经独立复核确认）**：`export_engine.py` L278-285 Excel 导出查询确缺 is_estimated 过滤，风险低，登记备查，不在本批次处理。
3. **M-3（经独立复核确认，范围扩大）**：除任务书 L83 前端描述错误外，任务书另有**函数名事实错误**（`_check_capital_outflow` → 实际 `check_capital_outflow`，见 R-1），一并更正。

**实际改动**：从任务书"两文件两处"收敛为 **"一文件一处"**（仅 `modules/alert_engine.py` L200-207 一处 SQL）。

---

## 〇-1、独立复核签署记录

| 项 | PM 初稿结论 | 架构师独立核验证据 | 裁定 |
|---|---|---|---|
| M-1 | 不改 app.py（前端已有标注） | ① `templates/index.html` L2481-2482 `hasEstimated`→`capitalSourceLabel` 表头动态文案；L2490 `estTag` 行级橙色"估算"上标；L2491 渲染入口。② `app.py` L770 `SELECT *` 已含 is_estimated 字段并随 JSON 返回（SQLite INTEGER→JSON number，前端 `=== 1` 严格相等可用）。③ 019E 评审 `review_019E_capital_fallback_20260804.md` L127-128（R-3 展示类允许读取不过滤）+ L205-214（E-6 标注实现），系 019E 已批准的架构设计。④ 任务书 L83 描述与以上均不符 | ✅ 确认 |
| M-2 | export_engine 缺过滤，登记备查 | `modules/export_engine.py` L278-285：`SELECT ... WHERE rcf.trade_date = (SELECT MAX(trade_date) ...)`，确无 is_estimated 过滤；若最新日恰为估算行，Excel 含估算值且无标注。触发概率低（估算行仅 EM 全失败时存在，恢复后覆盖），不在评分链路 | ✅ 确认 |
| M-3 | 任务书前端描述错误需更正 | 与 M-1 证据链相同；另发现任务书函数名错误（R-1），并入 M-3 更正范围 | ✅ 确认（扩大） |

---

## 一、独立核验记录

### 1.1 alert_engine.py 预警层（任务书 Task 1）

**核验文件**：`modules/alert_engine.py`
**实际函数**：`check_capital_outflow(cursor, stock_id, n_days=3)`（L183，**无下划线前缀**，见 R-1）
**调用方**：`_RULE_CHECKERS['capital_outflow']`（L277-279，lambda 传 `(cur, sid, n_days=...)`，签名不涉及）

**确认**：
- L200-207 SQL `SELECT trade_date, main_net_inflow FROM raw_capital_flow WHERE stock_id=? ORDER BY trade_date DESC LIMIT ?`（绑定 `(stock_id, n_days*2)`）**确实无 is_estimated 过滤**
- 判定逻辑：取最近 N 个有数据交易日，`all(main_net_inflow < 0)` 且非 None（L214/L221），估算行会被当作"有数据日"参与连续净流出判定——估算值（成交额×涨跌幅推算）与真实主力净流入相关性低，存在假信号风险（019F 评审 L74 亦已登记，标中风险）
- 港股跳过（L194-197），无需额外处理

**附加行为分析（【架构师修订】R-3 关联）**：过滤后估算行不再占用 `LIMIT n_days*2` 名额、不计入"有数据交易日"窗口——真实数据覆盖反而提升；当窗口内真实数据不足 n_days 时返回 None 不触发预警（更安全，属预期行为变化，见 R-3）。

**结论**：Task 1 方案正确，通过。SQL 补 `AND (is_estimated = 0 OR is_estimated IS NULL)`，签名不变。

### 1.2 app.py 展示层（任务书 Task 2）—— 关键修正

**核验文件**：`app.py` L757-796（路由 `api_get_capital`，查询 L768-774）+ `templates/index.html` L2479-2497

**任务书声称**（L83）：
> "但当前 `templates/index.html` 资金面展示区域**未对估算行做任何视觉区分**（无"估算"标签/颜色提示），用户无法区分真实数据与估算数据"

**架构师独立核验结果**：❌ **与代码不符**。前端在 019E Task 4.1 已实现两层估算标注（与 019E 评审 E-6/R-3 设计一致）：

| 层级 | 位置 | 实现 |
|---|---|---|
| 表头级 | index.html L2481-2482 | `hasEstimated = capital.data.some(d => d.is_estimated === 1)` → 表头动态显示"来源：东方财富（含估算兜底数据）" |
| 行级 | index.html L2490 | `estTag = d.is_estimated === 1 ? '<sup style="color:#e67e22;font-size:11px">估算</sup>' : ''` → 估算行主力净流入后追加橙色"估算"上标 |
| 数据链路 | app.py L770 `SELECT *` | is_estimated 字段随行返回（019E 自验报告"API 返回"亦确认） |

**方案 A（过滤）有害性评估——成立**：资金面展示为"最近 10 条"时间序列，直接过滤估算日会在序列中制造日期缺口（估算日"消失"），用户无法区分"缺数据"与"无数据日"；而带标注展示信息完整且直观。且方案 A 违背 019E 已批准的"展示类允许读取不过滤"架构裁定（R-3），属于推翻既有设计而非补全。

**裁定**：采纳 PM 结论 (a)——**不修改 app.py L770、不修改 index.html**。

### 1.3 既有过滤表达式一致性

| # | 文件 | 行 | 表达式 |
|---|---|---|---|
| 1 | data_adapter.py | L282 | `AND (is_estimated = 0 OR is_estimated IS NULL)` |
| 2 | advisor.py | L1126 | `AND (is_estimated = 0 OR is_estimated IS NULL)` |
| 3 | data_collector.py | L1477 | `AND (is_estimated = 0 OR is_estimated IS NULL)`（补采去重，拼接式） |
| 4 | analysis_engine.py | L132 | `AND (is_estimated = 0 OR is_estimated IS NULL)` |
| 5 | data_collector.py | L1903 | `AND main_net_inflow IS NOT NULL AND (is_estimated = 0 OR is_estimated IS NULL)`（EM 前置校验变体，含 canonical 子串，019E 既有，非新增） |

**确认**：1-4 处 canonical 表达式逐字符一致；第 5 处为 019E 既有变体（QA 019F 测试 T9 按 canonical 子串计数 ≥2 校验，与 grep 行为一致）。新增过滤点必须与 1-4 处逐字符一致（**不得附加 `main_net_inflow IS NOT NULL` 等额外条件**——NULL 已由 alert_engine L214 的 Python 过滤处理，见红线补充 7）。

### 1.4 全仓 raw_capital_flow 读取入口扫描（A-4）

**方法**：全仓 grep `raw_capital_flow`（52 处命中，全部为源码字面量——表名无变量拼接；项目无 ORM，全部 sqlite3 直连，无间接读取路径）。

**读取/管理类入口**：

| 文件 | 行 | 用途 | 过滤状态 | 本批次 |
|---|---|---|---|---|
| data_adapter.py L281 | 主评分链路 | ✅ 已过滤 | 不碰 |
| advisor.py L1125 | 顾问链路 | ✅ 已过滤 | 不碰 |
| analysis_engine.py L131 | legacy 降级 | ✅ 已过滤 | 不碰 |
| data_collector.py L1475 | 补采去重 | ✅ 已过滤 | 不碰 |
| data_collector.py L1902 | EM 恢复前置校验 | ✅ 已过滤 | 不碰 |
| **alert_engine.py L202** | **预警连续净流出** | ❌ **缺过滤** | **Task 1 改** |
| **app.py L770** | **展示层** | ❌ 缺过滤 | **不改（M-1）** |
| data_adapter.py L502 | COUNT 计数 | 无需过滤 | 不碰 |
| data_collector.py L2245 | COUNT+MAX 检查 | 无需过滤 | 不碰 |
| data_collector.py L2491 | MAX(trade_date) margin | 无需过滤 | 不碰 |
| **export_engine.py L280-283** | **Excel 导出** | ❌ **缺过滤** | **范围外（M-2）** |
| app.py L609 | DELETE 清理 | 无需过滤（写） | 不碰 |
| app.py L1168 | COUNT 统计 | 无需过滤 | 不碰 |
| scripts/b26_margin_backfill.py L58、diag_*.py | 一次性诊断/回填脚本 | 无需过滤 | 不碰 |
| tests/qa_019f_isolation_test.py | 测试夹具建表/插数 | 无需过滤 | 不碰 |

**写入路径**（15 处，data_collector.py L1429/L1438/L1962/L2014/L2064/L2122/L2128/L2158/L2164/L2194/L2200/L2384/L2395/L2570/L2580）：INSERT/UPDATE/INSERT OR REPLACE，无需过滤；其中 019E M-7 已确认 EM 写入显式携带 `is_estimated=0`，估算写入显式携带 `is_estimated=1`。

**结论**：PM 初稿 14 处扫描**无遗漏**。读取入口缺过滤者仅 alert_engine L202（本批次）与 export_engine L280（范围外），与 PM 结论一致。

---

## 二、方案裁定

### Task 1：alert_engine.py 预警过滤 —— ✅ 通过

**裁定**：同意 PM 方案，无修改。

**改动**：`modules/alert_engine.py` L200-207 SQL 补一行过滤：

```python
    # 019H：过滤估算行（is_estimated=1），确保预警判定仅使用真实资金流数据
    cursor.execute(
        """SELECT trade_date, main_net_inflow
           FROM raw_capital_flow
           WHERE stock_id=?
           AND (is_estimated = 0 OR is_estimated IS NULL)
           ORDER BY trade_date DESC
           LIMIT ?""",
        (stock_id, n_days * 2),
    )
```

**约束**（含【架构师修订】）：
- 过滤表达式与已有 4 处 canonical 逐字符一致，**不得附加额外条件**（NULL 处理维持 Python L214 现状）
- 函数签名 `check_capital_outflow(cursor, stock_id, n_days=3)` 不变（红线中函数名按实际代码书写，见 R-1）
- 参数绑定 `(stock_id, n_days * 2)` 顺序不变；LIMIT 语义不变
- 不改动该函数其余任何代码（注释按既有"019X-X："前缀风格）

### Task 2：app.py 展示层 —— ❌ 方案 A 否决，修正为"不改"

**裁定理由**（经独立复核，与 PM 初稿一致）：
1. 前端已有标注（019E Task 4.1）：index.html L2481-2490 双层标注，用户可区分真实/估算数据——任务书"前端未做视觉区分"系事实错误
2. 方案 A 有害：过滤制造时间序列日期缺口，比带标注展示更令人困惑
3. 019E 既定架构裁定（R-3"展示类允许读取不过滤"）明确"带标注展示"是设计意图，方案 A 属推翻既有设计

**裁定**：不修改 app.py L770、不修改 index.html；验收标准删除"展示层过滤验证"，改为"展示层回归验证"。

---

## 三、评审意见

### M-1（采纳，经架构师独立复核确认）：任务书 Task 2 方案修正

任务书第三节"任务 2"整体替换为：

> **任务 2：app.py 展示层 —— 不改（架构评审否决方案 A）**
>
> 经架构师核验，前端 `index.html` L2481-2490 在 019E Task 4.1 中已实现估算行双层标注（表头"含估算兜底数据"+ 行级橙色"估算"上标），且 019E 架构评审已裁定展示层"不过滤、前端标注"。方案 A（直接过滤估算行）会导致时间序列缺口且推翻既定设计。**裁定：不修改 app.py 和 index.html**。

验收标准第四节第 4 条（展示层验证）删除，替换为：
> 4. **展示层回归验证**：调用 `/api/stocks/<id>/capital` → 确认估算行仍正常返回（`is_estimated=1` 行存在），前端 index.html L2490 估算标注正常显示（QA 截图核查）

### M-2（采纳，经架构师独立复核确认）：新增备查项登记

全仓扫描确认 `export_engine.py` L278-285 的 Excel 导出查询缺 is_estimated 过滤（取每股最新交易日行，若最新日恰为估算行，Excel 含估算值且无标注）。

**风险评估**：
- 导出引擎不在评分链路，风险**低**
- 触发概率低：估算行仅 EM 全失败时存在，EM 恢复后自动覆盖归 0
- 与展示层 API 不同：Excel 为静态文件，无前端标注通道

**裁定：范围外备查，不在本批次处理**。建议后续批次（如 019I）统一处理，处理选项建议：① 导出查询补 canonical 过滤；② 导出增加"数据估算"标注列；③ 维持现状并在导出说明中披露。任务书"明确不改范围"追加 `modules/export_engine.py` — 不碰（范围外备查，见评审 M-2）。

### M-3（采纳并经独立复核确认，范围扩大）：任务书事实性错误更正

任务书第一节"备查项 2"风险分析中以下表述需更正：

**原文**（错误）：
> "但当前 `templates/index.html` 资金面展示区域**未对估算行做任何视觉区分**（无"估算"标签/颜色提示），用户无法区分真实数据与估算数据"

**更正为**：
> "前端 `templates/index.html` 在 019E Task 4.1 中**已实现**估算行双层标注（表头级"含估算兜底数据"提示 + 行级橙色"估算"上标标签）。展示层数据标注已完成，无需额外处理。本项经架构评审裁定为'不改'。"

**（【架构师修订】范围扩大）** 另更正任务书函数名错误（详见 R-1）：任务书 L44/L103/L134/L196/L219 中 `_check_capital_outflow(cursor, stock_id, n_days)` 一律更正为 `check_capital_outflow(cursor, stock_id, n_days=3)`。

### R-1（【架构师修订】新发现）：任务书函数名事实错误

任务书 5 处（L44/L103/L134/L196/L219）将预警函数写作 `_check_capital_outflow(cursor, stock_id, n_days)`。实际代码 `modules/alert_engine.py` L183 为 **`check_capital_outflow(cursor, stock_id, n_days=3)`**（无下划线前缀、n_days 有默认值 3）。全仓无 `_check_capital_outflow` 符号。PM 初稿已按正确名称书写，但未将其登记为任务书错误。**处理**：并入 M-3 更正范围；红线第 3 条以实际签名为准。属文档性错误，不影响方案本身。

### R-2（【架构师修订】新发现）：闭合数口径精确化

PM 初稿验收第 5 条写"评分链路 4 处 + 预警层 1 处 = **5 处**"。实际 019H 落地后 canonical 子串 `AND (is_estimated = 0 OR is_estimated IS NULL)` 全仓 grep 命中为 **6 处**：4 处评分链路 + data_collector L1903（019E 既有 EM 前置校验变体，含 canonical 子串）+ alert_engine L202（本批次新增）。019F 测试 T9 即按 canonical 子串计数（data_collector ≥2）。**处理**：验收第 5 条口径改为"grep 命中 6 处（既有 5 处 + 新增 1 处），QA 按子串计数断言"。

### R-3（【架构师修订】新发现）：预警行为变化说明与验收场景补充

过滤落地后，估算日不再计入"最近 N 个有数据交易日"窗口（且不再占用 LIMIT 名额）：真实数据覆盖提升；窗口内真实数据不足 n_days 时不触发预警（更安全，属预期行为，非缺陷）。PM 初稿验收第 3 条场景（1 估算 + 1 真实）只能验证"不误报"负路径，无法验证过滤未破坏真实预警路径。**处理**：验收第 3 条补充正路径场景（见五-3）。

### R-4（保留）：PM 核验流程改进建议

本次 PM 任务书对 app.py 展示层的风险评估基于未读取前端代码（index.html 资金面渲染逻辑）。建议 PM 后续签发涉及前端展示的任务书时，将前端消费端代码纳入核验范围，避免"后端视角"导致方案偏差。

---

## 四、修订后任务范围（定稿）

| Task | 文件 | 改动 | 状态 |
|---|---|---|---|
| Task 1 | `modules/alert_engine.py` L200-207 | SQL 补 is_estimated 过滤 | ✅ 通过 |
| ~~Task 2~~ | ~~app.py L770~~ | ~~不改~~ | ❌ 否决 |

**实际改动：1 个文件，1 处 SQL。**

**明确不改范围**（追加 export_engine.py）：
- `app.py` — 不碰（展示层，前端已有标注，M-1）
- `templates/index.html` — 不碰（已有估算标注，M-1）
- `modules/export_engine.py` — 不碰（范围外备查，M-2）
- `modules/data_adapter.py` — 不碰（019E 已过滤）
- `modules/advisor.py` — 不碰（019E 已过滤）
- `modules/data_collector.py` — 不碰（019E/019F 已过滤）
- `modules/analysis_engine.py` — 不碰（019F 已过滤）
- `modules/scoring_engine.py` — 不碰
- `modules/daily_report.py` — 不碰
- `database/db_manager.py` — 不碰（is_estimated 列 019E 已就位，db_manager L963）
- `config_*.json` / `config.py` — 不碰
- `requirements.txt` — 不碰（维持 9 包）

---

## 五、修订后验收标准

1. **代码级核查（PM 独立核验）**：
   - `alert_engine.py` `check_capital_outflow` SQL 含 `AND (is_estimated = 0 OR is_estimated IS NULL)`（grep 该文件恰 1 处）
   - 过滤表达式与 `data_adapter.py` L282 等 4 处**逐字符一致**（grep 比对）
2. **编译验证**：`python -m py_compile modules/alert_engine.py` 无错误
3. **预警纯净验证（QA 重点，【架构师修订】补正路径场景）**：
   - 负路径：写入 2 行真实数据（连续净流出但不足 3 日）+ 1 行 `is_estimated=1`（净流出）→ 断言**不触发**预警（估算行不计入窗口）
   - 正路径：写入 3 行 `is_estimated=0` 连续净流出 + 其间穿插 1 行 `is_estimated=1` → 断言**正常触发**预警且 `total_outflow` 仅统计真实行（过滤未破坏真实路径）
4. **展示层回归验证**：`/api/stocks/<id>/capital` 仍返回估算行（不删行），前端 L2490 估算标注正常显示（QA 截图核查）
5. **全仓 grep（闭合确认）**：canonical 子串 `AND (is_estimated = 0 OR is_estimated IS NULL)` 命中 **6 处** = 4 处评分链路 + data_collector L1903（既有）+ alert_engine L202（本批次新增）；展示层有意不过滤，不计入（【架构师修订】原"5 处"口径修正）
6. **回归验证**：019F 隔离测试 `tests/qa_019f_isolation_test.py` 全通过（T8/T9 过滤表达式一致性 + 已有 4 处过滤未被破坏）
7. **零改动确认**：app.py、index.html、export_engine.py 及所有评分链路文件内容不变（QA 用文件哈希核查）

---

## 六、红线约束（确认 + 补充）

**确认（任务书第五节）**：
1. **过滤表达式一致性红线**：`AND (is_estimated = 0 OR is_estimated IS NULL)` 逐字符一致
2. **范围红线**：改动仅限 `modules/alert_engine.py`（1 处 SQL）
3. **签名红线**：`check_capital_outflow(cursor, stock_id, n_days=3)` 签名不变（函数名按实际代码，任务书误写 `_check_capital_outflow` 见 R-1）；`/api/stocks/<int:stock_id>/capital` 路由不碰
4. **零代码约束**：不引入新依赖（维持 9 包）；无 schema 迁移
5. **评分纯净红线**：不影响已有 4 处评分链路过滤，QA 跑 019F 回归
6. **展示层不动红线**：app.py / index.html / export_engine.py 一律不碰

**【架构师修订】补充**：
7. **SQL 变体红线**：Task 1 不得在 canonical 表达式外附加 `main_net_inflow IS NOT NULL` 等额外 SQL 条件（NULL 行维持由 Python L214 `main_net_inflow is not None` 过滤处理，表达式必须与既有 4 处逐字符一致）
8. **参数绑定红线**：`LIMIT ?` 位置不变，参数元组 `(stock_id, n_days * 2)` 顺序不变

---

## 七、流程路径

```
✅ PM 签发 v1 → ✅ PM 越权初评（已声明）→ ✅ 架构师独立复核定稿（本评审）→ ⏳ PM 按 M-1/M-2/M-3/R-1/R-2/R-3 修订任务书 v2 → 待监理批准 → 待开发执行 → 待 QA 验收 → 待双签 → 待关闭
```

---

## 八、签署

> **架构师独立复核签署**：本评审由架构师于 2026-08-05 独立完成（逐项 Read 代码核验：alert_engine.py / app.py / index.html / export_engine.py / data_adapter.py / advisor.py / data_collector.py / analysis_engine.py / db_manager.py / qa_019f_isolation_test.py / 019E-019F 评审文档，全仓 grep 复核），未采信 PM 结论。**M-1 / M-2 / M-3 三项结论经架构师独立复核确认正确，予以签署确认**；另新增修订项 R-1（函数名错误）、R-2（闭合数口径）、R-3（行为变化与验收场景）及红线补充 7/8。
>
> **架构师总结**：019H 实际改动从 PM 任务书的"两文件两处"收敛为"一文件一处"（仅 alert_engine.py L200-207）。app.py 展示层方案 A 被否决的核心理由有二：前端已在 019E 实现估算标注（任务书事实错误），且方案 A 违背 019E 已批准的"展示层不过滤、前端标注"架构裁定。export_engine.py 缺过滤为本次评审确认的范围外新发现（M-2），风险低，建议 019I 处理。请 PM 按 M-1/M-2/M-3/R-1/R-2/R-3 修订任务书 v2 后提交监理批准。
