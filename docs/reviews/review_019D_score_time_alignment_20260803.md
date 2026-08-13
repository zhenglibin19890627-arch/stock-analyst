# 架构评审报告：019D 报告生成时间分钟级展示与三入口评分同源对齐

> 评审日期：2026-08-03
> 评审人：架构师（独立评审，不编码、不验收）
> 评审对象：`docs/tasks/dev_tasks_20260803_019D_score_time_alignment.md`（v1 初稿）
> 关联：019A（已关闭，写同源修复）、013（intraday 引入）、B11-SCORE-SYNC / B11-DETAIL-LOAD
> 评审结论：**有条件通过** —— 方向正确、改动面可控，按本报告裁定修订 3 处后可交 PM 定稿

---

## 〇、PM 标注代码现状核验结果（2026-08-03 现场复核）

| PM 标注 | 核验结果 |
|---|---|
| 看板 JOIN 无 report_type/status 过滤（app.py L1857） | ✅ 属实。`LEFT JOIN daily_reports dr ON s.id = dr.stock_id AND dr.report_date = ?`，仅按 date 过滤 |
| report-latest 无 report_type 过滤（app.py L916） | ✅ 属实。`WHERE dr.stock_id = ? AND dr.report_date = ? AND dr.status = 'ok'`，有 status 但无 report_type |
| `/api/ratings` 有 daily 优先 + status='ok'（app.py L1430-1449） | ✅ 属实。先 COUNT 判定 target_type，再 WHERE status='ok' AND report_type=? |
| `get_latest_reports` 口径为"daily 优先、无 daily 取 intraday、status='ok'"（daily_report.py L841-878） | ⚠️ **部分属实**。有 daily 优先 + intraday 降级逻辑，但 **L858-863 / L867-875 两段 SQL 均无 `status='ok'` 过滤**。PM 引用为"完全一致"的参照标准，实际并非完全一致——详见 R-1 |
| 三入口无分钟级时间展示（index.html L4195/L4714/L4561/L4624） | ✅ 属实。四处均仅显示到天（YYYY-MM-DD），无分钟级 |
| rating_date 取 score_date（K 线数据日期，advisor.py L1318） | ✅ 属实。`rating_date = analysis.get('score_date', ...)`，是数据截至日非生成时刻 |
| generated_at 为 ISO 格式含 +08:00（_save_report L255 / _save_daily_report_for_advice L615） | ✅ 属实。`datetime.now(_CN_TZ).isoformat()` |
| B11-DETAIL-LOAD 实时路径直接返回引擎结果、无 generated_at（app.py L927-937） | ✅ 属实。`return jsonify(advice)` 直接返回，advice dict 中无 generated_at 字段 |
| report-latest DB 行路径已含 generated_at（app.py L1100） | ✅ 属实。`'generated_at': row['generated_at']` 已在返回 JSON 中 |

**评审中新增发现（PM 任务书未标注，影响裁定）：**

1. **R-1：`get_latest_reports` 自身缺少 `status='ok'` 过滤。** daily_report.py L858-863（daily 查询）和 L867-875（intraday 降级查询）的 WHERE 子句均为 `WHERE report_date = ? AND report_type = 'daily'`，**无 `status = 'ok'`**。这意味着失败的报告行（status='failed'）也会出现在每日报告列表中。PM 将其引为"D-1 统一口径的参照标准"，但该函数本身并不完全正确。同理 `get_reports_by_date`（L887-906）也无 status='ok'。**本批次应一并修复**。

2. **R-2：report-latest 回退路径（引擎也失败时）同样缺少 report_type/status 过滤。** app.py L952-958 的回退 SQL 为 `WHERE dr.stock_id = ? AND dr.report_date = ?`，无 report_type 也无 status。虽然该路径触发概率极低（当日无报告 + 引擎也失败），但口径不一致仍应统一。

3. **R-3：前端 `_dashData` 构建时丢弃了 API 已返回的 `generated_at`。** index.html L4695 构建 `_dashData` 时提取了 `reportDate: scores.report_date`，但未提取 `scores.generated_at`。watchlist-scores API（app.py L1958）已返回 `generated_at` 字段，前端只需增加一行赋值即可获得，任务 3-1 的后端无需任何改动。

4. **R-4：`renderDailyReportList` 的数据源已包含 generated_at（SELECT *），无需后端改动。** 前端 `renderDailyReportList(reportDate, reports)` 的 reports 数组来自 `/api/daily-report/latest` → `get_latest_reports()` → `SELECT * FROM daily_reports`，每行均含 generated_at 字段。任务 3-2 的表头 MAX 和行级 HH:MM 展示纯前端即可实现。

---

## 一、决策点裁定

### D-1：读取口径裁定 — **采纳（含扩展），强烈建议抽取共享辅助函数**

**裁定：采纳，但范围需从 3 处扩展至 5 处**

1. **口径统一方向完全正确**。daily 优先、无 daily 取 intraday、status='ok' 三条件必须同时满足，与 `/api/ratings`（L1430-1449）的标杆逻辑完全对齐。

2. **范围必须扩展**（基于 R-1 / R-2）：
   - 原任务书列 3 处读取（看板 JOIN / report-latest / MAX generated_at）
   - 实际需统一为 **5 处**：
     - ① 看板 JOIN（app.py L1857）
     - ② 看板 MAX(generated_at) 查询（app.py L1888-1891）
     - ③ report-latest 当日查询（app.py L912-918）
     - ④ report-latest 引擎失败回退查询（app.py L952-958）← **PM 遗漏**
     - ⑤ `get_latest_reports` + `get_reports_by_date`（daily_report.py L858-875 / L887-906）← **PM 遗漏 + 参照标准自身有缺陷**

3. **强烈建议抽取共享辅助函数**。理由：
   - 口径分散是本次缺陷的根因——/api/ratings 用了正确口径，看板没用，report-latest 用了一半，get_latest_reports 用了另一半。如果仅在各处分别打补丁，未来新增读取入口仍会遗漏。
   - 建议在 app.py 中新增辅助函数 `_resolve_report_type(cursor, report_date)`：
     ```python
     def _resolve_report_type(cursor, report_date):
         """统一口径：判定当日应取 daily 还是 intraday（daily 优先）"""
         cursor.execute(
             "SELECT COUNT(*) as cnt FROM daily_reports "
             "WHERE report_date=? AND report_type='daily' AND status='ok'",
             (report_date,),
         )
         return 'daily' if cursor.fetchone()['cnt'] > 0 else 'intraday'
     ```
   - 该函数与 /api/ratings L1431-1437 现有逻辑完全一致，仅做提取复用。
   - **注意**：`get_latest_reports` / `get_reports_by_date` 位于 daily_report.py 模块中，不在 app.py 内。这两处建议直接在 SQL 中增加 `AND status='ok'`（改动极小），不强制跨模块共享函数，避免引入模块间耦合。app.py 内的 3 处读取使用共享函数。

4. **红线 #6（改动限于 app.py + index.html）需修订**：D-1 修复必须触及 daily_report.py 的两个查询函数（加 `AND status='ok'`），详见修订清单。

---

### D-2：看板重复行的历史脏数据处置 — **采纳"不迁移不清理"**

**裁定：采纳**

1. **读取侧隔离即可**。修复 JOIN 过滤后，并存脏行被 WHERE 隔离，不影响展示。用户不会看到重复行。

2. **不做数据清理的理由充分**：
   - 写入侧语义保证后续 daily 生成会清掉 intraday（_save_report L257-261：daily 类型 DELETE 当日全部含 intraday）；
   - `_save_daily_report_for_advice`（advisor.py L647-653）无 daily 行时也先 DELETE 当日全部再 INSERT daily；
   - 手动刷新/一键分析/批量分析的任一入口触发后，历史脏行自动被清理。
   - 编写一次性清理脚本增加执行风险（需用户操作），违背零代码用户原则。

3. **唯一需关注的窗口期**：用户不进行任何手动操作、也不触发定时日报的极端场景下，脏行持续存在但不影响展示。这在实际使用中不构成问题。

---

### D-3：B11-DETAIL-LOAD 实时路径的 generated_at — **采纳方案 (a)**

**裁定：采纳 (a)，在 app.py 端点内拼装 generated_at**

1. **方案 (a) 正确**。实时路径（app.py L927-937）返回的是 `generate_advice(stock_id)` 的 advice dict，该 dict 无 generated_at 字段。在端点内 `return jsonify(advice)` 之前补充 `advice['generated_at'] = datetime.now(_CN_TZ).isoformat()` 即可，改动在 app.py 内、不触碰 advisor.py。

2. **与 PM 倾向一致，且实现简单**：
   ```python
   advice['generated_at'] = datetime.now(_CN_TZ).isoformat()
   return jsonify(advice)
   ```
   注意：该路径函数顶部已定义 `_CN_TZ`（L905），无需额外导入。

3. **语义准确性**：实时路径的 generated_at 代表"报告生成时刻"，与 DB 行路径的 generated_at（_save_report 写入时刻）语义一致，均为 ISO 格式 +08:00。前端 `renderFullReport` 统一从 `adviseData.generated_at` 读取，两条路径（DB 行 / 实时引擎）行为一致。

4. **方案 (b)（前端显示"—"）被否决**。理由：用户反馈的核心诉求是"核对报告是否同一时间生成"，实时路径显示"—"会制造新的困惑（"为什么这只显示—那只显示时间？"）。

---

### D-4：前端时间格式化实现 — **采纳方案 (a)，ISO 字符串切片**

**裁定：采纳 (a)**

1. **方案 (a) 正确**。`generated_at` 格式为 `2026-08-03T16:57:57.123456+08:00`，ISO 字符串切片 `s.slice(0,16).replace('T',' ')` 得到 `2026-08-03 16:57`，零依赖、无时区换算风险。

2. **方案 (b) 被否决的关键理由**（与 PM 一致并补充）：
   - `new Date(s)` 会触发浏览器本地时区解析。虽然 generated_at 带 `+08:00` 后缀，但不同浏览器对 ISO 8601 带偏移量的解析存在边缘差异（尤其 Safari 旧版），引入不可控变量。
   - 本项目全部用户在中国（+08:00），generated_at 已是目标时区，无需二次换算。字符串切片是最确定的方案。

3. **建议新增前端辅助函数**（统一调用，避免四处散写切片逻辑）：
   ```javascript
   function _fmtGenTime(s) {
       if (!s || typeof s !== 'string') return '—';
       return s.slice(0, 16).replace('T', ' ');
   }
   ```
   放在 index.html 已有辅助函数区域（如 `_scoreColor` 附近），四处展示点统一调用。

4. **防御性处理**：`_fmtGenTime` 对 undefined / null / 非字符串返回 `'—'`。这覆盖了 D-3 实时路径万一补充失败时的降级场景，也覆盖了 DB 行 generated_at 为 NULL 的历史数据。

---

### D-5：日报列表"本批生成时间"取值 — **采纳方案 (a)，但需区分两个视图**

**裁定：采纳 (a)，细化如下**

本决策点涉及两个前端视图，数据来源不同，改动也不同：

#### 5-1：`renderDailyReportList`（每日报告列表，从 DB 读取）

- **无需后端改动**。reports 数组来自 `get_latest_reports()` 的 `SELECT *`，每行已含 generated_at（R-4 已验证）。
- 前端实现：
  - 表头"本批生成时间"：遍历 reports 取 `generated_at` 的 MAX，经 `_fmtGenTime` 格式化。
  - 行级"生成于"列：每行 `generated_at` 经 `_fmtGenTime` 取 HH:MM 部分。
- **前提条件**：R-1 修复后，get_latest_reports 增加 `status='ok'` 过滤，确保返回的行均为有效行（否则 failed 行的 generated_at 也会参与 MAX 计算）。

#### 5-2：`renderDailyReport`（生成汇总视图，从 generate_daily_report 返回值读取）

- **需后端改动**（采纳方案 a）。`generate_daily_report()` 返回的 summary dict（daily_report.py L672-686）不含任何时间戳字段。results 数组中各 item（L585-596 / L361-371）也不含 generated_at。
- **改动方式**：在 summary dict 中追加一个 `finished_at` 字段：
  ```python
  # L672 summary dict 中追加
  'finished_at': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
  ```
  该值在批次完成后（L636 `_update_progress_file` 之后、L672 summary 构建之前）生成，与进度文件的 `finished_at`（L651）语义一致、时间几乎相同。
- **不改写入语义**：此改动仅扩展函数返回值（读取侧），不修改 `_save_report` / `_save_daily_report_for_advice` 的 INSERT/UPDATE/DELETE 逻辑。红线 #2（写入红线）不受影响。
- **红线 #6 需修订**：此改动触及 daily_report.py 的 `generate_daily_report` 函数返回值。

#### 5-3：`renderDailyReport` 的 results 行级时间

- tasks 任务 3-3 仅要求在表头显示生成时间，**不要求 results 每行显示 generated_at**。理由合理：results 来自函数返回值（内存态），无独立 generated_at；且生成汇总视图的语义是"刚生成的批次"，各行生成时间几乎相同（批次耗时几十秒~几分钟），行级差异无意义。
- **不采纳**给 results 各 item 追加 generated_at（改动面过大，需穿透 `_process_single_stock` 返回值，触及写入侧链路）。

---

### D-6：范围确认与红线完备性 — **红线需修订 2 处**

**裁定：有条件采纳，红线 #6 必须修订**

1. **红线 #6 原文**："改动限于 `app.py` 与 `templates/index.html`"
   - **必须修订为**："改动限于 `app.py`、`templates/index.html`、`modules/daily_report.py`（仅 `get_latest_reports` / `get_reports_by_date` 增加 status='ok' 过滤 + `generate_daily_report` 返回值追加 finished_at 字段，不改写入语义）"
   - 修订理由：D-1 要求修复 get_latest_reports/get_reports_by_date 的 status='ok' 缺失（R-1），D-5 要求 generate_daily_report 返回值追加 finished_at。这两项均在 daily_report.py 中。

2. **其余红线（#1-#5, #7-#8）完备**：
   - #1 签名红线：本批次不触碰 advisor.py ✅
   - #2 写入红线：不改 _save_report / _save_daily_report_for_advice 的 INSERT/UPDATE/DELETE ✅（D-5 的 finished_at 是返回值扩展，不是写入逻辑）
   - #3 配置红线：config.py / config_weights.json 不动 ✅
   - #4 零依赖红线：不引入新 pip 包 ✅（D-4 用纯字符串切片）
   - #5 数据安全：无 schema 变更、不删除数据 ✅
   - #7 缓存红线：ETag 排除 generated_at 的做法不变 ✅（详见下方分析）
   - #8 口径红线：与 /api/ratings 完全一致 ✅（D-1 已裁定）

3. **ETag 行为变化分析（PM 问询项）**：
   - **ETag payload 排除 generated_at 的机制不变**（app.py L1963-1964）：`etag_payload = {k: v for k, v in result.items() if k != 'generated_at'}`。
   - **但 stocks 数组内容会变**：增加 report_type/status 过滤后，看板 JOIN 从"可能返回 2 行"变为"确定返回 1 行（或 0 行）"，stocks 数组的元素数量和内容都会变，ETag 值必然变化。
   - **这是预期行为、正确行为**。修复后 ETag 值的变化意味着客户端缓存失效、重新拉取正确数据。不存在"ETag 不变但数据已变"的不一致风险。
   - **结论：无需特殊处理 ETag**，红线 #7 不受影响。

4. **watchlist-scores 排序 CASE 表达式兼容性分析（PM 问询项）**：
   - 当前 ORDER BY：`CASE WHEN dr.total_score IS NULL THEN 1 ELSE 0 END, dr.total_score DESC`（L1860-1862）
   - 增加 `AND dr.report_type = ? AND dr.status = 'ok'` 到 LEFT JOIN 后，不匹配的行 dr.* 全为 NULL，CASE 表达式将其排到末尾（THEN 1），**行为完全兼容**。
   - 需注意的是：增加 report_type 参数需要先判定 target_type（与 /api/ratings 同理），即在 JOIN 前增加一次 COUNT 查询。这会多一次轻量 SQL 查询（COUNT），对性能无可感知影响。

---

## 二、新发现风险项

| 编号 | 风险 | 级别 | 处置建议 |
|---|---|---|---|
| R-1 | `get_latest_reports` / `get_reports_by_date` 缺少 status='ok' 过滤，failed 行混入列表 | 中 | 本批次一并修复（WHERE 增加 `AND status='ok'`），纳入 D-1 范围 |
| R-2 | report-latest 引擎失败回退路径（L952-958）无 report_type/status 过滤 | 低 | 本批次一并修复，口径统一 |
| R-3 | 前端 `_dashData` 构建时丢弃 scores.generated_at | 无风险 | 前端补一行赋值即可，属任务 3-1 正常改动 |
| R-4 | renderDailyReportList 数据源已含 generated_at，PM 误判需后端改动 | 无风险 | 前端直接使用，属任务 3-2 正常改动 |
| R-5 | get_latest_reports 修复 status='ok' 后，前端 renderDailyReportList 对 failed 行的渲染分支（L4639-4644）将不再触发 | 低 | 需确认：是否有用户依赖在"每日报告"页查看 failed 行的 error_msg？若无依赖则安全；若有依赖，建议保留"每日报告"页的 failed 行展示（取 status='ok' OR status='failed'），仅"评级列表/看板"严格过滤 status='ok'。**建议 QA 验收时确认此点** |

---

## 三、任务书修订点清单

| # | 位置 | 修订内容 |
|---|---|---|
| 1 | 任务书 §1.2 对照行 | 将 get_latest_reports 的描述从"status='ok'"修正为"**缺少 status='ok'**，本批次补齐" |
| 2 | 任务书 §三 任务 1 | 范围扩展：明确包含 report-latest 回退路径（L952-958）的口径修复 |
| 3 | 任务书 §三 新增任务 | 新增"任务 1b：修复 get_latest_reports / get_reports_by_date 的 status='ok' 过滤"（daily_report.py 两处 SQL 各加 `AND status='ok'`） |
| 4 | 任务书 §三 任务 2 | 明确 B11-DETAIL-LOAD 实时路径补充 `advice['generated_at'] = datetime.now(_CN_TZ).isoformat()` |
| 5 | 任务书 §五 红线 #6 | 修订为："改动限于 `app.py`、`templates/index.html`、`modules/daily_report.py`（仅 get_latest_reports / get_reports_by_date 加 status='ok' + generate_daily_report 返回值追加 finished_at，不改写入语义）" |
| 6 | 任务书 §五 红线 #8 | 补充："get_latest_reports / get_reports_by_date 也须纳入统一口径，不得遗漏" |
| 7 | 任务书 §四 验收标准 | 增加："7. 每日报告列表页不出现 failed 行（status='ok' 过滤生效），或确认 failed 行展示策略" |
| 8 | 任务书 §三 任务 1 | 建议补充："抽取 `_resolve_report_type` 辅助函数，app.py 内 3 处读取统一调用" |

---

## 四、评审结论

### 结论：**有条件通过**

**条件**：PM 按本报告"任务书修订点清单"8 项修订定稿后，报监理批准交开发执行。

**总体评价**：

- **方向正确**：用户反馈"评分对不上"的根因（读取侧口径不一致）诊断准确，修复方向（统一为 daily 优先 + status='ok'）与 /api/ratings 标杆完全对齐。
- **改动面可控**：核心为 SQL WHERE 条件补齐 + 前端展示层增加时间格式化，无 schema 变更、无引擎触碰、无新依赖引入。
- **PM 核查质量高**：8 处代码标注中 7 处完全属实，仅 get_latest_reports 的 status='ok' 描述有偏差（R-1），属可理解的疏漏。
- **关键裁定**：D-1（扩展至 5 处 + 共享函数）、D-3（实时路径补 generated_at）、D-5（区分两视图、generate_daily_report 返回值扩展）为重点关注项，均已在裁定中给出明确实现路径。

**风险评级：低**。本批次为读取侧过滤修复 + 前端展示层改动，写入语义和引擎逻辑完全不动。最大的"改动"是 get_latest_reports 加 status='ok'（可能影响 failed 行的展示策略，R-5），需 QA 验收时确认。

---

> 架构师签字：2026-08-03
> 交付物：`docs/reviews/review_019D_score_time_alignment_20260803.md`
> 下一步：PM 按修订清单定稿 → 监理批准 → 开发执行
