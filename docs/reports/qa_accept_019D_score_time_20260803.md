# QA 验收报告 — 019D 报告生成时间分钟级展示与三入口评分同源对齐

| 项 | 内容 |
|---|---|
| 编号 | QA-TASKS-20260803-019D |
| 关联开发任务 | DEV-TASKS-20260803-019D |
| 验收人 | QA（独立验收，不依赖开发自验报告） |
| 验收日期 | 2026-08-04 |
| 验收环境 | Windows 25H2 / Python 3.12 / SQLite |
| 验证方式 | 静态代码核查 + 受控实测（临时脚本 INSERT 测试行）+ 函数级调用验证 |
| 数据库 | `stock_analyst.db`（2026-08-03 报告 29 行，全部 daily/ok） |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |

---

## 一、数据前置条件检查

```sql
SELECT COUNT(*) FROM raw_capital_flow
WHERE trade_date='2026-08-03' AND main_net_inflow IS NULL;
```

| 检查项 | 预期 | 实际 | 结论 |
|---|---|---|---|
| main_net_inflow NULL 行数 | 0 | **14**（总 23 行） | ⚠️ EM 补采未完成 |

**说明**：14 条 main_net_inflow 为 NULL，EM 逐只补采尚未完成。此条件影响 TC-019D-2 中涉及资金面评分的**真实数据对比**有效性。但 QA 本次验收采用**受控测试数据**（专用日期 2099-12-31，使用独立构造的评分值），口径一致性验证不依赖真实资金面数据。验收结论不受此前置条件影响。

---

## 二、测试用例逐条验收

### TC-019D-1 — `_resolve_report_type` 与四处调用核查 — **PASS**

| # | 检查项 | 方法 | 结果 | 证据 |
|---|---|---|---|---|
| 1-1 | `_resolve_report_type` 逻辑与 `/api/ratings` 原判定逻辑等价 | 代码核查 app.py L1402-1413 | PASS | `COUNT(*) WHERE report_date=? AND report_type='daily' AND status='ok'`，COUNT>0 返回 `'daily'`，否则 `'intraday'`，与原 L1431-1437 逻辑完全一致 |
| 1-2 | 看板 JOIN 调用该函数并限定 `report_type=? AND status='ok'` | 代码核查 app.py L1863 + L1877-1878 | PASS | `target_type = _resolve_report_type(cursor, latest_report_date)` → JOIN 条件含 `AND dr.report_type = ? AND dr.status = 'ok'` |
| 1-3 | 看板 MAX(generated_at) 调用该函数并限定 | 代码核查 app.py L1908-1911 | PASS | `WHERE report_date = ? AND report_type = ? AND status = 'ok'`，params 含 target_type |
| 1-4 | report-latest 当日查询调用该函数 | 代码核查 app.py L912 + L918 | PASS | `target_type = _resolve_report_type(cursor, today)` → `AND dr.status = 'ok' AND dr.report_type = ?` |
| 1-5 | report-latest 回退查询调用该函数 | 代码核查 app.py L957 + L963 | PASS | `fallback_type = _resolve_report_type(cursor, latest_date)` → `AND dr.status = 'ok' AND dr.report_type = ?` |
| 1-6 | `/api/ratings` 改为调用该函数后行为不变 | 代码核查 app.py L1452 + L1464 | PASS | `target_type = _resolve_report_type(cursor, latest_date)` → `WHERE dr.report_date = ? AND dr.status = 'ok' AND dr.report_type = ?`；响应字段含 `rating_time`(=dr.generated_at)、`created_at` 等兼容字段不变 |

---

### TC-019D-2 — daily+intraday 并存场景实测 — **PASS** ★★核心验收项

**构造方法**：选用真实股票 id=4（600276 恒瑞医药），在专用测试日期 `2099-12-31` 插入 daily 行（status='ok', score=88.8）与 intraday 行（status='ok', score=55.5），两行评分不同。

#### 场景 A：daily + intraday 并存

| # | 检查项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 2-1 | `_resolve_report_type` 返回 daily | `'daily'` | `'daily'` | PASS |
| 2-2 | 看板 JOIN 该股票仅 1 行 | 1 行 | 1 行 | PASS |
| 2-2b | 看板 JOIN 评分 = daily(88.8) | 88.8 | 88.8, type=daily | PASS |
| 2-3 | report-latest 取 daily 行 | score=88.8 | 88.8 | PASS |
| 2-3b | report-latest 行含 generated_at | 非空 | `2099-12-31T10:01:00.000000+08:00` | PASS |
| 2-4 | `/api/ratings` 查询取 daily(88.8) | 88.8 | 88.8 | PASS |
| 2-5 | `get_latest_reports` 仅返回 daily 行 | 1 行 | 1 行 | PASS |
| 2-5b | `get_latest_reports` daily 评分 = 88.8 | 88.8 | 88.8, type=daily | PASS |

#### 场景 B：删除 daily 仅留 intraday（降级）

| # | 检查项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 2-6 | `_resolve_report_type` 降级返回 intraday | `'intraday'` | `'intraday'` | PASS |
| 2-7 | 看板 JOIN 降级返回 intraday 行 | 1 行 intraday | 1 行, type=intraday, score=55.5 | PASS |
| 2-8 | `get_latest_reports` 降级返回 intraday | 1 行 intraday | 1 行, type=intraday | PASS |
| 2-8b | `get_latest_reports` 降级评分 = 55.5 | 55.5 | 55.5 | PASS |

---

### TC-019D-3 — failed 行过滤核查 — **PASS**

**构造方法**：选用第二只真实股票 id=6（HK3690 美团-W），在 `2099-12-31` 插入唯一一行 daily(status='failed', score=NULL)。UNIQUE 约束 `(report_date, stock_id, report_type)` 限制同一股票同日同类型仅一行。

| # | 检查项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 3-1 | `get_latest_reports` 不返回 failed 行 | 0 行 | 0 行 | PASS |
| 3-2 | `get_reports_by_date` 不返回 failed 行 | 0 行 | 0 行 | PASS |
| 3-3 | `_resolve_report_type` 仅有 failed 时返回 intraday | `'intraday'` | `'intraday'`（daily COUNT=0） | PASS |
| 3-4 | 看板 JOIN status='ok' 不匹配 failed 行 | ok_count=0 | 0 | PASS |
| 3-5 | MAX(generated_at) status='ok' 排除 failed 行 | NULL | NULL | PASS |

**R-5 确认**：前端 `renderDailyReportList` 对 failed 行的渲染分支（index.html L4653-4658）保留未删（防御性），代码核查确认存在 `if (r.status !== 'ok')` 分支。结论：**用户无"在每日报告页查看 failed 行 error_msg"的依赖**——failed 行本质是采集/分析失败记录，用户通过日志（012 日志系统）查看失败原因更为合理，每日报告列表仅需展示成功报告。该分支保留为防御性代码不影响功能。

---

### TC-019D-4 — 三入口分钟级时间展示 — **PASS** ★重点

| # | 检查项 | 方法 | 结果 | 证据 |
|---|---|---|---|---|
| 4-1 | `_fmtGenTime` 函数存在且逻辑正确 | 代码核查 index.html L5393-5396 | PASS | `if (!s || typeof s !== 'string') return '—'; return s.slice(0,16).replace('T', ' ');` |
| 4-2 | ISO 格式输入正确（Python 模拟） | 受控测试 | PASS | `'2026-08-03T16:57:57.123+08:00'` → `'2026-08-03 16:57'` |
| 4-3 | 空格分隔格式输入正确（finished_at） | 受控测试 | PASS | `'2026-08-03 16:57:57'` → `'2026-08-03 16:57'`（无 T 时 replace 为 no-op） |
| 4-4 | None / undefined / 空串 → '—' | 受控测试 | PASS | 5 种异常输入均返回 `'—'` |
| 4-5 | 看板顶部「生成时间」调用 `_fmtGenTime` | 代码核查 L4731-4732 | PASS | `if (data.generatedAt) { ... _fmtGenTime(data.generatedAt) ... }` |
| 4-6 | 看板 `_dashData` 提取 `scores.generated_at` | 代码核查 L4710 | PASS | `generatedAt: scores.generated_at` |
| 4-7 | 日报列表表头「本批生成时间」调用 `_fmtGenTime` | 代码核查 L4635-4636 | PASS | `if (batchGenTime) { ... _fmtGenTime(batchGenTime) ... }`，batchGenTime 取各行 MAX |
| 4-8 | 日报列表行级「生成于」列调用 `_fmtGenTime` | 代码核查 L4649(表头) + L4675(行) | PASS | 表头 `<th>生成于</th>` + 行级 `_fmtGenTime(r.generated_at)` |
| 4-9 | 生成汇总视图「生成时间」调用 `_fmtGenTime` | 代码核查 L4563-4564 | PASS | `if (genResult.finished_at) { ... _fmtGenTime(genResult.finished_at) ... }` |
| 4-10 | 详情页「报告生成于」调用 `_fmtGenTime` | 代码核查 L4196 | PASS | `_fmtGenTime(adviseData.generated_at)` |
| 4-11 | 详情页数据源为 `generated_at`（非 rating_date） | 代码核查 L4195-4196 | PASS | L4195 `评级时间：rating_date`（保留，语义=数据截至日）；L4196 `报告生成于：generated_at`（新增，语义=生成时刻） |

---

### TC-019D-5 — generated_at 回写链路贯通 — **PASS**

| # | 检查项 | 方法 | 结果 | 证据 |
|---|---|---|---|---|
| 5-1 | B11-DETAIL-LOAD 实时路径含 generated_at | 代码核查 app.py L939-941 | PASS | `advice['generated_at'] = datetime.now(_CN_TZ).isoformat()` 在 `return jsonify(advice)` 之前 |
| 5-2 | report-latest DB 行路径含 generated_at | 代码核查 app.py L1107 | PASS | `'generated_at': row['generated_at']` 在返回 result dict 中 |
| 5-3 | `_save_daily_report_for_advice` UPDATE 写入 generated_at | 代码核查 advisor.py L631, L642 | PASS | `SET ... generated_at=?` UPDATE 时刷新为当前时刻 |
| 5-4 | `_save_daily_report_for_advice` INSERT 写入 generated_at | 代码核查 advisor.py L615, L659-660 | PASS | `generated_at = datetime.now(_CN_TZ).isoformat()` 后 INSERT |
| 5-5 | `_save_report` 写入 generated_at（ISO 格式） | 代码核查 daily_report.py L255 | PASS | `generated_at = datetime.now(_CN_TZ).isoformat()` |

**链路总结**：刷新报告 → `generate_advice` → `_save_daily_report_for_advice` UPDATE/INSERT generated_at → `report-latest` 读取 row['generated_at'] → 前端 `renderFullReport` 显示 `_fmtGenTime(adviseData.generated_at)`。全链路贯通。

---

### TC-019D-6 — finished_at 返回值核查 — **PASS**

| # | 检查项 | 方法 | 结果 | 证据 |
|---|---|---|---|---|
| 6-1 | `generate_daily_report()` 返回 dict 含 `finished_at` | 代码核查 daily_report.py L675 | PASS | `'finished_at': datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')` |
| 6-2 | `finished_at` 格式为 `YYYY-MM-DD HH:MM:SS` | 代码核查 | PASS | `strftime('%Y-%m-%d %H:%M:%S')` |
| 6-3 | 取值时机为批次完成后 | 代码核查 L636-675 | PASS | 在 `_update_progress_file`（L636）之后、summary dict（L672）构建处 |
| 6-4 | 无 INSERT/UPDATE/DELETE 写入逻辑被改动 | 代码核查 | PASS | finished_at 仅出现在 summary dict 返回值中，不在任何写入 SQL 中 |

---

## 三、红线核验

| # | 红线项 | 核验方法 | 结论 | 证据 |
|---|---|---|---|---|
| 1 | `generate_advice()` 签名未变 | advisor.py `def generate_advice(stock_id, report_date=None)` | PASS | 签名 `generate_advice(stock_id, report_date=None)` 不变；advisor.py 不在 019D 改动范围 |
| 2 | `_save_report` / `_save_daily_report_for_advice` 写入语义未改 | 代码核查 daily_report.py L228-300 / advisor.py L592-680 | PASS | DELETE+INSERT 幂等模式不变；generated_at 仍为 `datetime.now(_CN_TZ).isoformat()` ISO 格式；daily 类型 DELETE 当日全部（含 intraday）语义保留 |
| 3 | `config_weights.json` 未改（含 BOM 检查）；`config.py` 不动 | git diff + BOM 检查 | PASS | git diff HEAD 输出为空（无变更）；BOM 检查：文件头为 `{\r\n`，无 BOM（`\xef\xbb\xbf`） |
| 4 | 无新增 pip 依赖 | requirements.txt 行数 | PASS | 9 行（9 个包），前端无第三方时间库（`_fmtGenTime` 纯字符串切片） |
| 5 | 数据安全 | 检查无 schema 变更 / 无数据删除 | PASS | 本批次为读取侧过滤修复 + 前端展示 + 返回值扩展，无 ALTER TABLE / DROP；历史脏行不迁移不清理（评审 D-2） |
| 6 | 改动限于 app.py / index.html / daily_report.py | 代码核查 | PASS | 019D 相关代码变更（`_resolve_report_type`、4 处调用、`_fmtGenTime`+5 展示点、`get_latest_reports`/`get_reports_by_date` 加 status='ok'、`finished_at` 返回值、实时路径 generated_at）全部位于此三文件。git diff 中其他文件变更（advisor.py 等）属 019A/B/C 等历史批次累积未提交变更 |
| 7 | watchlist-scores ETag 机制保留 | 代码核查 app.py L1985 | PASS | `etag_payload = {k: v for k, v in result.items() if k != 'generated_at'}` 排除机制不变 |
| 8 | 口径唯一性 | 全仓 grep report_type + status | PASS | 所有读取入口统一使用 `_resolve_report_type`（app.py 内）或直接 SQL `status='ok' AND report_type=?`（daily_report.py 内），无第三套口径。advisor.py L620-621 的 `report_type='daily'` 查询为 B11-REPORT-REUSE 写入侧逻辑（查询已有 daily 以决定 UPDATE/INSERT），不在 019D 读取口径范围内，属既有逻辑未改 |

---

## 四、构造测试行插入/清理记录

| 操作 | 日期 | stock_id | stock_code | report_type | status | score | 清理 |
|---|---|---|---|---|---|---|---|
| INSERT | 2099-12-31 | 4 | 600276 | daily | ok | 88.8 | ✅ 已删 |
| INSERT | 2099-12-31 | 4 | 600276 | intraday | ok | 55.5 | ✅ 已删 |
| DELETE daily (场景B) | 2099-12-31 | 4 | 600276 | daily | ok | 88.8 | — |
| INSERT | 2099-12-31 | 6 | HK3690 | daily | failed | NULL | ✅ 已删 |

**清理复核**：

```sql
SELECT COUNT(*) FROM daily_reports WHERE report_date='2099-12-31';
-- 结果：0（全部清理完毕）
```

清理方式：`DELETE FROM daily_reports WHERE report_date='2099-12-31'`（专用测试日期，精确删除，无误删真实数据风险）。临时脚本文件已删除。

---

## 五、PM 提示 QA 关注/知悉项确认

| # | 项目 | QA 结论 |
|---|---|---|
| 1 | R-5 failed 行渲染分支 | **确认保留**（index.html L4653-4658）。用户无在每日报告页查看 failed 行 error_msg 的依赖，该分支为防御性保留不触发。failed 行过滤为正确行为。 |
| 2 | 历史并存脏行不迁移 | **当日无并存脏行**（2026-08-03 全部 29 行为 daily/ok）。读取侧过滤隔离即可，不判 FAIL。 |
| 3 | ETag 值变化 | 属预期行为。修复后 stocks 数组内容变化（重复行消除）导致 ETag 值变化，客户端重新拉取正确数据。ETag 排除 generated_at 的机制不变。 |
| 4 | finished_at 与 generated_at 格式差异 | `_fmtGenTime` 的 `slice(0,16).replace('T',' ')` 对 ISO 格式（T 分隔）和空格分隔格式均正确。受控测试验证两种输入均输出 `YYYY-MM-DD HH:MM`。 |
| 5 | UNIQUE 约束注意事项 | daily_reports 表 UNIQUE(report_date, stock_id, report_type) 已验证生效。构造并存行需用不同 report_type；清理按测试日期精确删除。 |

---

## 六、验收结论

### **全部 PASS 可双签**

| 维度 | 结果 |
|---|---|
| TC-019D-1（口径统一） | ✅ PASS（6/6） |
| TC-019D-2（并存场景） | ✅ PASS（12/12）★★核心 |
| TC-019D-3（failed 过滤） | ✅ PASS（5/5） |
| TC-019D-4（时间展示） | ✅ PASS（11/11）★重点 |
| TC-019D-5（链路贯通） | ✅ PASS（5/5） |
| TC-019D-6（finished_at） | ✅ PASS（4/4） |
| 红线核验 | ✅ PASS（8/8） |
| **总计** | **51/51 PASS, 0 FAIL** |

**总体评价**：

- 019D 批次的核心目标——「三入口评分同源对齐」与「报告生成时间分钟级展示」——均已正确实现
- `_resolve_report_type` 共享辅助函数有效统一了 app.py 内 4 处读取口径，与 `/api/ratings` 标杆完全一致
- daily_report.py 两处读取函数（`get_latest_reports` / `get_reports_by_date`）已补齐 `status='ok'` 过滤，failed 行不再混入列表
- 前端 `_fmtGenTime` 辅助函数在 5 个展示点统一调用，ISO 切片方案零依赖、无时区风险
- generated_at 回写链路（刷新 → UPDATE → 读取 → 展示）完整贯通
- finished_at 返回值扩展为纯读取侧改动，写入语义未受影响
- 所有红线项通过，无第三套口径、无新依赖、无配置变更、无数据安全风险

**遗留说明**（不构成本批次 FAIL）：
- 资金面 main_net_inflow NULL（14/23）属 EM 补采任务（019C），与本批次代码正确性无关
- 历史并存脏行（当日无）属预期，读取侧过滤已隔离

---

> QA 签字：2026-08-04
> 交付物：`reports/qa_accept_019D_score_time_20260803.md`
> 下一步：提交 PM，PM+QA 双签后报监理关闭


---

## 七、PM+QA 双签

### PM 核验意见

| 核验项 | 结论 |
|---|---|
| 六条用例（TC-019D-1~6）51/51 PASS | ✅ 确认，核心项 TC-019D-2（daily+intraday 并存）与重点项 TC-019D-4（分钟级展示）证据链完整 |
| 红线核验 8/8 PASS | ✅ 确认，无第三套口径、无新依赖、无 schema 变更、ETag 排除机制保留 |
| 数据前置条件偏差处理 | ✅ 认可 QA 判定：14 只 main_net_inflow NULL 属 EM 补采遗留（补救①/019E 任务 1 覆盖），QA 采用受控测试数据独立验证口径一致性，结论不受影响 |
| 构造测试行清理 | ✅ 确认 2099-12-31 测试行全部清理（COUNT=0），临时脚本已删除 |
| 知悉项 5 条 | ✅ 全部确认，无遗留风险 |

**PM 结论**：019D「报告生成时间分钟级展示与三入口评分同源对齐」达成批次目标，同意 QA 验收结论，**双签通过**，报请监理批准关闭。

> PM 签字：2026-08-04
> QA 签字：2026-08-04（见本报告第六节）
> 状态：双签完成 → 待监理批准关闭


---

## 八、监理批准关闭

| 项 | 内容 |
|---|---|
| 关闭裁定 | ✅ 监理批准关闭（2026-08-04） |
| 关闭依据 | QA 独立验收 51/51 PASS + PM 核验双签（本报告第六、七节） |
| 遗留事项 | 无本批次遗留；14 只 main_net_inflow NULL 属 EM 补采遗留，由补救①（08-04 盘中自然补采）+ 019E 任务 1（批量补采正向触发）覆盖 |
| **批次状态** | **019D 已关闭** |
