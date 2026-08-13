# 开发任务书 019D — 报告生成时间分钟级展示与三入口评分同源对齐【定稿】

**签发日期**：2026-08-03
**签发人**：PM
**批次编号**：019D
**优先级**：P1
**关联批次**：019A（评分一致性修复，已关闭）、013（盘中快报 intraday 引入）、B11-SCORE-SYNC（评级列表同源 daily_reports）
**状态**：监理已批准（2026-08-03），进入开发执行阶段
**评审报告**：`docs/reviews/review_019D_score_time_alignment_20260803.md`（结论：有条件通过，8 项修订已全部落实到本定稿）

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 推荐窗口类型 | Quests 独立窗口（单代理执行） |
| 推荐模型 | glm5.2（编码任务） |
| 执行模式 | 单代理开发 + 自验 |
| 流程路径 | PM 签发 v1 → 架构师评审（已通过）→ PM 修订定稿 → 监理批准（已批准 2026-08-03）→ 开发执行+自验（当前）→ QA 独立验收 → PM+QA 双签 → 监理批准关闭 |

---

## 一、背景与用户反馈

用户反馈：**「分析报告点刷新和总览看板、每日报告的评分对不上，报告时间精确到分钟，方便核对是否报告不是同一时间生成的报告」**。

### PM 现场核查结论（2026-08-03）+ 架构评审复核裁定

019A 已实现「任一分析入口 → `_save_daily_report_for_advice` 统一回写 daily_reports」，三入口数据源理论上同源。核查 + 评审复核确认 **读取侧口径缺陷 + 时间展示缺失** 是"对不上"的根因：

#### 缺陷 1：总览看板 JOIN 缺少 report_type / status 过滤（评分错行）

`app.py` `api_portfolio_watchlist_scores`（约 L1857）四表 JOIN：

```sql
LEFT JOIN daily_reports dr ON s.id = dr.stock_id AND dr.report_date = ?
```

- **未过滤 `report_type`**：013 起 intraday（盘中快报）**不删除 daily**（`daily_report.py` `_save_report` L262-267），同日 daily+intraday 可并存 → 同一股票 JOIN 出 **2 行**，看板出现重复行且评分各取一条
- **未过滤 `status='ok'`**：采集失败的行（status='failed'、total_score NULL）也参与 JOIN

#### 缺陷 2：report-latest 两处查询未区分 report_type（评分错行）

- 当日查询（约 L912-918）：`WHERE dr.stock_id = ? AND dr.report_date = ? AND dr.status = 'ok'`，无 `report_type` 过滤
- **引擎失败回退查询（约 L952-958，评审 R-2 新增发现）**：`WHERE dr.stock_id = ? AND dr.report_date = ?`，无 `report_type` 也无 `status` 过滤

daily+intraday 并存时 `fetchone()` 命中任意一条 → 详情页评分可能来自 intraday，而每日报告页优先取 daily → **对不上**。

#### 缺陷 3（评审 R-1 新增）：get_latest_reports / get_reports_by_date 缺少 status='ok' 过滤

`daily_report.py` L858-863（daily 查询）与 L867-875（intraday 降级查询）、`get_reports_by_date`（L887-906）的 WHERE 子句**均无 `status='ok'`** → failed 行会出现在每日报告列表中，且其 generated_at 会污染"本批生成时间"的 MAX 计算。

#### 缺陷 4：三入口均无分钟级生成时间展示（用户无法核对）

| 入口 | 现状 | 问题 |
|---|---|---|
| 分析报告详情（renderFullReport，index.html 约 L4195） | 仅显示 `评级时间 = rating_date` | `rating_date` 取 `analysis.get('score_date')`（advisor.py L1318），是 **K 线数据日期（到天）**，不是报告生成时刻；点刷新后该值可能不变，用户误以为报告未更新 |
| 总览看板（renderDashboard，约 L4714） | 仅显示 `报告日期：YYYY-MM-DD` | API 已返回整批 `generated_at`（MAX，L1958）但前端构建 `_dashData` 时未提取（评审 R-3），纯前端补赋值即可 |
| 每日报告页（renderDailyReport L4561 / renderDailyReportList L4624） | 仅显示报告日期 | 列表页数据源 `get_latest_reports` 的 `SELECT *` 已含 generated_at（评审 R-4），纯前端展示即可；生成汇总视图的返回值无时间字段，需后端补 `finished_at`（评审 D-5 裁定） |

`daily_reports.generated_at` 以 ISO 格式（含时分秒微秒+时区）存储，信息完备，**主要为展示层缺失**。

#### 正确口径标杆

`/api/ratings`（app.py L1430-1449）：**先 COUNT 判定当日是否存在 `status='ok' AND report_type='daily'`，有则 target_type='daily'，否则 'intraday'；读取时限定 `status='ok' AND report_type=?`**。本批次全部读取入口统一向此标杆对齐。

---

## 二、执行角色

**开发**

---

## 三、任务范围

### 任务 1：app.py 内 4 处读取口径统一（看板 JOIN / MAX generated_at / report-latest 当日查询 / report-latest 回退查询）

1. **抽取共享辅助函数**（评审 D-1 裁定，防止未来新入口遗漏口径）：

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

   该逻辑与 `/api/ratings` L1431-1437 现有逻辑完全一致，仅做提取复用（`/api/ratings` 亦改为调用此函数，行为不变）。

2. **看板 JOIN（约 L1857）**：JOIN 前调用 `_resolve_report_type` 判定 target_type，JOIN 条件增加 `AND dr.report_type = ? AND dr.status = 'ok'`。LEFT JOIN 语义保留（无报告行 dr.* 为 NULL，ORDER BY CASE 将其排末尾，行为兼容）。

3. **看板 MAX(generated_at) 查询（约 L1888-1891）**：同步加 `report_type = ? AND status='ok'` 过滤。

4. **report-latest 当日查询（约 L912-918）**：改为 daily 优先、无 daily 时取 intraday（以 `_resolve_report_type` 判定后限定 `report_type = ? AND status='ok'`）。

5. **report-latest 引擎失败回退查询（约 L952-958）**：同口径修复（`report_type = ? AND status='ok'`）。

6. **语义说明**：同日 daily+intraday 并存时统一取 daily（与 013-Hotfix 口径一致）；intraday 仅作为无 daily 时的降级数据源。

### 任务 1b：daily_report.py 两处读取函数补 status='ok'（评审 R-1，新增任务）

- `get_latest_reports`（L858-863 daily 查询、L867-875 intraday 降级查询）两段 SQL 的 WHERE 各增加 `AND status='ok'`
- `get_reports_by_date`（L887-906）同步增加 `AND status='ok'`
- 直接在 SQL 中修改，**不跨模块共享 `_resolve_report_type`**（评审裁定：避免模块间耦合，改动极小）
- **R-5 注意**：修复后每日报告列表不再返回 failed 行，`renderDailyReportList` 对 failed 行的渲染分支（约 L4639-4644）将不再触发。该分支代码**保留不删**（防御性），QA 验收时确认用户无"在每日报告页查看 failed 行 error_msg"的依赖

### 任务 2：report-latest 响应补 generated_at + 实时路径补 generated_at（评审 D-3 裁定）

- **DB 行路径**：确保返回 JSON 中包含 `generated_at` 字段（`dr.*` 已含，核查 jsonify 组装未丢弃即可）
- **B11-DETAIL-LOAD 实时路径（约 L927-937）**：当日无报告触发 `generate_advice` 直接返回引擎结果时，在 `return jsonify(advice)` 之前补充：

  ```python
  advice['generated_at'] = datetime.now(_CN_TZ).isoformat()
  return jsonify(advice)
  ```

  该路径函数顶部已定义 `_CN_TZ`（约 L905），无需额外导入。语义与 DB 行路径一致（报告生成时刻，ISO +08:00）。

### 任务 3：三入口分钟级生成时间展示（templates/index.html + 必要后端字段）

统一时间格式：**`YYYY-MM-DD HH:MM`**。前端新增统一辅助函数（评审 D-4 裁定，放 `_scoreColor` 等辅助函数附近，四处展示点统一调用，不得引入第三方时间库）：

```javascript
function _fmtGenTime(s) {
    if (!s || typeof s !== 'string') return '—';
    return s.slice(0, 16).replace('T', ' ');
}
```

ISO 字符串切片方案零依赖、无浏览器时区解析差异（generated_at 已是 +08:00 目标时区，无需二次换算）；对 undefined/null/非字符串防御性返回 `—`（兼容历史 NULL 行）。

| # | 位置 | 改动 |
|---|---|---|
| 3-1 | `renderDashboard` 顶部栏（约 L4713） | 「报告日期」旁新增「生成时间：HH:MM」。数据源为 API 已有 `generated_at` 字段——前端构建 `_dashData`（约 L4695）时补提取 `scores.generated_at`（评审 R-3：后端无需改动） |
| 3-2 | `renderDailyReportList`（约 L4617） | 表头区新增「本批生成时间：YYYY-MM-DD HH:MM」（取该批各行 `generated_at` 最大值，经 `_fmtGenTime` 格式化）；表格新增「生成于」列（每行经 `_fmtGenTime`）。数据源已含 generated_at（评审 R-4：纯前端，后端无需改动） |
| 3-3 | `renderDailyReport`（生成汇总视图，约 L4561） | 「报告日期」旁新增「生成时间：YYYY-MM-DD HH:MM」，数据源为任务 3-3b 新增的 `finished_at` 返回值字段。**results 各行不加行级时间**（评审 D-5-3 裁定：批次内各行生成时刻几乎相同，行级无意义，且需穿透写入侧链路，改动面过大，不采纳） |
| 3-4 | `renderFullReport`（约 L4195） | 「评级时间」下方新增一行「报告生成于：YYYY-MM-DD HH:MM」，数据源 `adviseData.generated_at`（DB 行路径与实时路径均已在任务 2 保证该字段存在）；经 `_fmtGenTime` 格式化。**不得用 rating_date 冒充生成时间**（rating_date 行保留，语义为数据截至日） |

#### 任务 3-3b：generate_daily_report 返回值追加 finished_at（评审 D-5-2 裁定，daily_report.py）

`generate_daily_report()` 返回的 summary dict（约 L672-686）追加一个字段：

```python
'finished_at': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S'),
```

- 取值时机：批次完成后（约 L636 `_update_progress_file` 之后、summary 构建之前），与进度文件 `finished_at`（L651）语义一致
- `_CN_TZ` 已在 daily_report.py L44 定义，无需新增导入
- **仅扩展函数返回值（读取侧），不修改任何 INSERT/UPDATE/DELETE 写入逻辑**，红线 #2 不受影响
- 注意：`finished_at` 为空格分隔格式（`YYYY-MM-DD HH:MM:SS`），与 `generated_at` 的 ISO 格式（`T` 分隔）不同；`_fmtGenTime` 的 `slice(0,16).replace('T',' ')` 对两种格式均正确（无 `T` 时 replace 为 no-op）

### 明确不改范围

- **`modules/advisor.py` 不改**（本批次不触碰）
- **`modules/daily_report.py` 写入语义不改**：`_save_report` / `_save_daily_report_for_advice` 的 INSERT/UPDATE/DELETE 逻辑与 generated_at ISO 格式均不动；daily_report.py 的改动仅限任务 1b（两个读取函数加 status 过滤）与任务 3-3b（返回值加 finished_at）
- `scoring_engine.py`、`config.py`、`config_weights.json` 一律不碰
- `/api/ratings` 接口响应结构不变（前端已依赖 `rating_time`/`created_at` 兼容字段）；仅其内部 target_type 判定改为调用 `_resolve_report_type`，行为等价
- 盘中快报/日报的生成流程、复用逻辑（B11-REPORT-REUSE）不动
- ETag 缓存机制保留（304 行为不变）；修复后 stocks 数组内容变化导致 ETag 值变化属预期行为（客户端重新拉取正确数据），无需特殊处理（评审 D-6 分析）
- 历史并存脏行**不迁移不清理**（评审 D-2 裁定）：读取侧过滤隔离即可；后续任一 daily 生成入口触发时 `_save_report` 写入语义会自动清理

---

## 四、验收标准

1. 同日 daily+intraday 并存场景下：总览看板每只股票仅 1 行、取 daily 评分；每日报告页、评级列表、看板三处评分一致
2. `report-latest` 在并存场景返回 daily 行，且响应含 `generated_at`（DB 行路径与 B11-DETAIL-LOAD 实时路径均含）
3. 三入口均可见分钟级生成时间（看板顶部 / 日报列表表头+行级 / 详情页"报告生成于"）
4. 点击"刷新报告"后，详情页"报告生成于"更新为当前分钟（验证 generated_at 回写链路贯通，非新增写入逻辑）
5. 前端时间格式统一 `YYYY-MM-DD HH:MM`（经 `_fmtGenTime`），无第三方库引入；generated_at 缺失时显示 `—`
6. 无 daily 仅 intraday 场景：看板/日报/详情页正确降级展示 intraday 数据（不出现空列表误报）
7. 每日报告列表页不出现 failed 行（get_latest_reports 的 status='ok' 过滤生效）；QA 验收时确认用户无查看 failed 行 error_msg 的依赖（评审 R-5）
8. `generate_daily_report` 返回值含 `finished_at`，生成汇总视图正确显示「生成时间」

---

## 五、红线约束

1. **签名红线（B24）**：`generate_advice()` 签名不变（本批次不触碰 advisor.py）
2. **写入红线（019A）**：`_save_daily_report_for_advice` / `_save_report` 写入语义与字段不改（任务 3-3b 的 finished_at 是返回值扩展，不是写入逻辑）
3. **配置红线**：`config_weights.json` 不得修改（含 BOM 检查）；`config.py` 不动
4. **零依赖红线**：不引入新 pip 依赖（requirements.txt 维持 9 包）
5. **数据安全**：不得删除/覆盖现有数据；本批次为读取侧过滤修复 + 前端展示，无 schema 变更；历史脏行不迁移不清理（评审 D-2 裁定）
6. **范围约束**（评审修订）：改动限于 `app.py`、`templates/index.html`、`modules/daily_report.py`（仅 `get_latest_reports` / `get_reports_by_date` 增加 status='ok' 过滤 + `generate_daily_report` 返回值追加 finished_at 字段，不改写入语义）
7. **缓存红线**：watchlist-scores ETag 机制保留，排除 generated_at 的既有做法不变（修复后 ETag 值变化属预期，无需处理）
8. **口径红线**（评审修订）：daily 优先/intraday 降级/status='ok' 口径必须与 `/api/ratings` 完全一致，不得出现第三套口径；`get_latest_reports` / `get_reports_by_date` 也须纳入统一口径，不得遗漏；app.py 内 4 处读取统一调用 `_resolve_report_type`

---

## 六、执行顺序

```
Step 1: 架构师评审（已完成：有条件通过，8 项修订已落实到定稿）
Step 2: PM 修订定稿，监理批准（2026-08-03 已批准，完成）
Step 3: 开发执行任务 1 / 1b / 2 / 3 / 3-3b（改动限 app.py + index.html + daily_report.py 三文件）【当前】
Step 4: 开发自验（构造 daily+intraday 并存场景核验 + 三入口截图核对时间展示 + failed 行过滤验证）
Step 5: 提交开发报告，PM 安排 QA 独立验收（含 R-5 failed 行展示策略确认）
Step 6: PM+QA 双签，报监理批准关闭
```

---

> **PM 备注**：本定稿已获监理批准（2026-08-03），开发请于 Quests 独立窗口（推荐模型 glm5.2）以本定稿全文作为启动提示词执行。——019A 解决了"写同源"，本批次解决"读同口径 + 时间可核对"。相比 v1 的主要变化（依评审裁定）：读取口径统一范围从 3 处扩展至 5+2 处（含评审新发现 R-1/R-2）、新增 `_resolve_report_type` 共享函数与 `_fmtGenTime` 前端辅助函数、实时路径补 generated_at（D-3）、生成汇总视图加 finished_at（D-5）、范围约束扩展至 daily_report.py 三处限定改动（D-6）。用户反馈的评分差异在本批次口径修复后应可解释（历史并存脏行场景）。QA 验收时需构造 daily+intraday 并存数据验证，并确认 R-5。开发请在监理批准后，于 Quests 独立窗口以本定稿全文作为启动提示词执行。
