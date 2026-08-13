# 架构评审报告：019L "刷新报告"后生成时间显示"—"修复（/advise 端点补 generated_at）

> 评审日期：2026-08-05
> 评审人：架构师（独立评审，不编码、不验收）
> 评审对象：`docs/tasks/dev_tasks_20260805_019L_advise_generated_at.md`（v1 初稿）
> 关联：019D（generated_at 链路）、019G（时间展示）、019N（并行批次，data_collector.py）
> 评审结论：**有条件通过** —— 技术方案正确、单点低风险；2 处文字性修订（端点名称笔误 + 验收细化）落实后可交 PM 定稿

---

## 〇、PM 标注代码现状核验结果（独立 Read/grep 复核，不采信 PM 结论）

| PM 标注 | 核验结果 |
|---|---|
| `/advise` 端点未补 generated_at（app.py L1117-1134） | ✅ 属实。`api_advise_stock` 成功分支仅做 005/009 后处理，无 generated_at 赋值 |
| 019D 先例 L939-940 已补 | ✅ 属实。`advice['generated_at'] = datetime.now(_CN_TZ).isoformat()`（report-latest 实时回退路径） |
| 019D 先例函数内 import L902-905 | ✅ 属实。`from datetime import datetime, timezone` + `from datetime import timedelta as _td` + `_CN_TZ` 定义均在函数内 |
| 前端 L4185 刷新按钮走实时路径 | ✅ 属实。`onclick="loadReport(' + stockId + ', true)"`；`loadReport` L4119 非 forceRefresh 走 report-latest，forceRefresh=true 直接走 `_loadReportFromAdvise`（L4138 else 分支 → L4146 POST /advise） |
| 前端 L4213 渲染 generated_at | ✅ 属实。`_fmtGenTime(adviseData.generated_at)` |
| `_fmtGenTime` L5412-5415 非字符串返回 '—' | ✅ 属实。`if (!s || typeof s !== 'string') return '—'`；`slice(0,16).replace('T',' ')` 与 ISO(+08:00) 格式兼容（`2026-08-05T14:23:45.123+08:00` → `2026-08-05 14:23`） |
| 根因：generate_advice 返回 dict 无 generated_at | ✅ 属实。全仓 grep：advisor.py 中 `generated_at` 仅 L615 一处（`_sync_report_to_db` DB 写入局部变量）；`generate_advice` 返回 dict（advisor.py L1324）不含该字段。→ /advise 响应必然缺失 |
| `/refresh` 端点 L860-869 同型遗漏 | ⚠️ **端点名称有误（见 R-1）**：实际路由为 `/api/stocks/<int:stock_id>/refresh-full`（app.py **L836**），函数 `api_refresh_full`（L837-871）；同型遗漏（成功分支未补 generated_at）属实 |

**评审中新增发现：**

1. **R-1：任务书端点名称笔误。** 任务书 §三/§七 两处写 `/api/stocks/<id>/refresh`，实际路由为 `/refresh-full`（app.py L836）。不影响技术方案，但登记备查记录须更正，否则未来检索失真。
2. **R-2（信息性）：`/advise` 共 3 个前端调用点。** 除刷新报告（L4146）外，`generateAdvice`（index.html L2212）与批量分析路径（L2271）也 POST /advise。本批次补字段对三者均为纯增量（仅多返回一个键），无任何回归面；`generateAdvice` 用 `renderAdviceResult` 渲染，不读取 generated_at，不受影响。
3. **R-3：`refresh-full` 前端零调用。** grep 全仓：index.html 中 `/refresh` 相关调用仅 `api/portfolio/refresh-prices`（L3615）与 `api/index-ratings/refresh`（L4965），**无任何 `refresh-full` 调用**。该端点为纯 API 入口，用户界面不可达 → 其 generated_at 缺失不构成用户可见缺陷，"备查不处理"成立。
4. **R-4：019N 文件隔离确认。** 019N 任务书明文"改动范围仅 `modules/data_collector.py`"，且"`app.py` 不碰"；019L 仅动 app.py。文件零重叠，并行无冲突 ✅。

---

## 一、决策点裁定

### A-1：补字段位置与时机 — **采纳**

**核验证据**（app.py L1117-1134）：

```python
@app.route('/api/stocks/<int:stock_id>/advise', methods=['POST'])
def api_advise_stock(stock_id):
    from modules.advisor import generate_advice
    try:
        result = generate_advice(stock_id)
        if result.get('success'):
            from modules.price_advisor import generate_price_advice
            result['price_advice'] = generate_price_advice(stock_id, result)
            if result.get('price_advice', {}).get('action_suggestion'):
                result['position_advice'] = result['price_advice']['action_suggestion']
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'建议生成失败: {str(e)}'}), 500
```

1. **补在 success 分支末尾（005/009 之后）合理**：与 019D 先例 L934-940 结构逐段同型（price_advice → position_advice 覆盖 → generated_at）。
2. **失败分支不补，与 /report-latest 语义一致**：对照 L929-941，generated_at 仅在 `if advice.get('success')` 分支内补充；`generate_advice` 自身失败时返回 `{'success': False, 'message': ...}`（advisor.py L1238-1239），`/advise` 原样透出、不加时间戳——语义统一。
3. **更优位置（generate_advice 内部）否决**：B24 红线禁止修改 advisor.py；且 019D 已裁定该模式（端点层拼装），无重复论证必要。
4. **边界情况**：若 `generate_price_advice` 抛异常 → 外层 except → 500 + success=False（失败态不补，符合语义）；若返回失败 dict（不抛异常）→ 主分析仍算成功，generated_at 照补——与先例行为一致，可接受。

### A-2：时间格式与时区一致性 — **采纳**

1. **格式逐字一致**：`datetime.now(_CN_TZ).isoformat()` 与 L940 完全相同（含 `+08:00` 后缀），前端 `_fmtGenTime` slice(0,16) 兼容（已核验 L5412-5415，见〇表）。
2. **函数内 import 符合全文件惯例**：grep 证实 app.py **无顶部 datetime 导入**，全文件 37 处 datetime 导入均为函数内（L902-905 同型），PM 方案不扩大改动面，采纳。
3. **时间语义**：/advise 为实时重算路径，generated_at = 实时重算完成时刻；刷新后时间更新是预期行为（019D 评审 D-3 已裁定"实时路径 generated_at 代表报告生成时刻"），与 DB 快照时刻（report-latest DB 行路径 L1107 取 `row['generated_at']`）语义各自自洽，前端已按来源展示，无混淆风险。

### A-3：是否一并修复 /refresh-full 端点 — **维持备查（采纳 PM）**

1. **前端零调用已核实**（R-3）：`/refresh-full`（app.py L836）无任何 JS 调用点，纯 API 入口，其缺失不构成用户可见缺陷。
2. 一并修复将扩大本批次范围（另一端点 + 另一处 import），收益为零（无人可达），违背范围收敛原则。
3. 登记信息按 R-1 更正端点名为 `refresh-full` 后维持备查；若监理指示扩展，改动模式与 A-1 完全相同，可零成本追加。

### A-4：范围与红线确认 — **完备**

| 红线 | 核验 |
|---|---|
| B24（不改 advisor） | ✅ 方案在 app.py 端点层补字段，generate_advice 零修改 |
| 范围（仅 app.py 一端点） | ✅ 仅 `api_advise_stock`，成功分支 4 行（import×2 + _CN_TZ + 赋值） |
| 语义（失败不补） | ✅ 见 A-1 第 2/4 点；price_advice 异常 → 500 失败态不补，无遗漏 |
| 零代码 | ✅ datetime/timezone/timedelta 均为标准库；requirements.txt 9 包无变化 |
| 并行隔离（019N） | ✅ R-4：019N 仅 data_collector.py，且明文不碰 app.py |

### A-5：验收标准充分性 — **充分，补充 2 项细化（M-2）**

1. **mock 方式可行**：`api_advise_stock` 内 `from modules.advisor import generate_advice` 为函数内导入 → `patch('modules.advisor.generate_advice')`（模块属性级）即可生效；Flask test_client POST `/api/stocks/1/advise` 为推荐方式（`jsonify` 需应用上下文，直接调视图函数不可靠）。**需补充**：mock 成功态时必须同时 patch `modules.price_advisor.generate_price_advice`（否则真实执行会查库/查持仓，破坏 mock 隔离）——任务书现状仅提 mock generate_advice。
2. **"不覆盖既有字段"断言非必需**：已核实 generate_advice 返回 dict 无 generated_at 键（〇表），覆盖场景不存在；若 QA 愿加一行 `assert 'generated_at' not in mock_result` 属低成本防御，不强制。
3. **时间偏差断言建议明确化**：任务书"值≈当前时间"建议落为 `<60s` 断言——`datetime.fromisoformat(resp['generated_at'])`（Python 3.12 原生支持 `+08:00` 后缀）与 QA 自建 `datetime.now(timezone(timedelta(hours=8)))` 求差，成本低、断言强，建议纳入。
4. 其余 4 条（代码核查 / py_compile / 005·009 行为保持 / 零改动哈希）充分。

---

## 二、新发现风险项

| 编号 | 风险 | 级别 | 处置建议 |
|---|---|---|---|
| R-1 | 任务书两处端点名写为 `/refresh`，实际为 `/refresh-full`（app.py L836） | 低 | 修订任务书 §三/§七 名称（M-1）；备查记录以正确名称登记 |
| R-2 | `/advise` 另有两处前端调用点（L2212 / L2271）将收到新增 generated_at | 无风险 | 纯增量键，渲染函数不读取该键，无回归；QA 无需额外验证 |
| R-3 | `/refresh-full` 前端零调用，generated_at 缺失不构成用户可见缺陷 | 无风险 | 维持备查；未来前端若接入须同步补 generated_at |
| R-4 | 019N 文件隔离 | 无风险 | 已确认零重叠（019L=app.py / 019N=data_collector.py），QA 验收各自独立 mock |

---

## 三、任务书修订点清单

| # | 位置 | 修订内容 |
|---|---|---|
| M-1 | §三"明确不改范围" + §七 备注 2 | 端点名称更正为 `/api/stocks/<int:stock_id>/refresh-full`（app.py L836-871，函数 `api_refresh_full`），备查登记同步更正 |
| M-2 | §四 验收标准 3 | 细化：① 成功态 mock 须同时 patch `modules.price_advisor.generate_price_advice`（防真实执行破坏隔离）；② 时间断言明确为"与 mock 执行时刻偏差 <60s"（`fromisoformat` 解析 + 东八区 now 求差）；③ mock 走 Flask test_client POST（`jsonify` 需应用上下文，勿直接调视图函数） |

---

## 四、评审结论

### 结论：**有条件通过**

**条件**：PM 按"任务书修订点清单"落实 M-1、M-2 两处文字性修订后定稿 v2，报监理批准交开发执行。

**总体评价**：

- **根因诊断准确**：独立复核确认 /advise 端点（app.py L1117-1134）确为 019D 遗漏点；前端链路（L4185 → loadReport(_,true) → L4146 POST /advise → L4213 渲染）与 `_fmtGenTime` 降级行为（L5412-5415）全部属实。
- **方案正确且最小化**：与 019D 先例（L939-940 / L902-905）逐字同型，格式、时区、import 惯例全站一致；单端点 4 行改动，无行为变化（仅增键）。
- **范围收敛得当**：refresh-full 前端零调用（R-3）使"备查不处理"成立；019N 文件隔离充分（R-4）。
- **风险评级：低**。唯一实质性发现为端点名称笔误（R-1/M-1），不影响方案执行，修订即闭环。

---

> 架构师签字：2026-08-05
> 交付物：`docs/reviews/review_019L_advise_generated_at_20260805.md`
> 下一步：PM 按 M-1/M-2 修订任务书定稿 v2 → 监理批准 → 开发执行（019L 与 019N 可并行）
