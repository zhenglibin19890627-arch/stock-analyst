# QA 独立验收报告 019L — "刷新报告"后生成时间显示"—"修复（/advise 端点补 generated_at）

**批次**：019L（P2，前端展示缺陷；DB 时间未丢，仅刷新报告路径显示"—"）
**角色**：QA（独立验收，不兼任 PM/架构师/开发）
**验收日期**：2026-08-05
**任务书**：`docs/tasks/dev_tasks_20260805_019L_advise_generated_at.md`（v2 定稿，M-1/M-2 已并入）
**架构评审**：`docs/reviews/review_019L_advise_generated_at_20260805.md`（⚠️ 有条件通过，M-1/M-2）
**自验报告**：`reports/dev_selftest_019L_advise_generated_at_20260805.md`（仅对照参考，**未采信其结论**）
**验收结论**：**通过**

---

## 〇、独立性声明

- 本验收全部证据由 QA 独立获取：静态核查（Read/grep）+ 独立构造的 mock 实测脚本（`qa_019L_accept_mock.py`，存于系统临时目录，**未复用**开发脚本 `.dev_019L_work/selftest_019L.py`）+ 哈希独立复算。
- 开发自验报告结论未采信；其内容（13/13 断言）仅用于与本报告独立证据对照，两方结论方向一致。
- 验收全程：**零真实网络请求**、**真实库零写入**（实测见 DB 保护断言）、**未修改任何功能代码**。

---

## 一、验证环境

| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst` |
| 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 验证方式 | 静态代码核查 + `py_compile` + Flask `test_client` mock 实测 + node 纯 JS 断言 + SHA256 哈希比对 |
| 实测脚本 | `%TEMP%\opencode\qa_019L_accept_mock.py`（验收结束后已删除） |

---

## 二、V1：代码级核查 —— ✅ 全过（6/6）

| # | 核查项 | 验证方法 | 结果 | 证据 |
|---|---|---|---|---|
| 1 | `api_advise_stock` 成功分支含 `result['generated_at'] = datetime.now(_CN_TZ).isoformat()` | Read app.py L1132-1137 | ✅ | L1137 实测：`result['generated_at'] = datetime.now(_CN_TZ).isoformat()`；L1136 `_CN_TZ = timezone(_td(hours=8), name='Asia/Shanghai')` |
| 2 | 失败分支（success=False）无该行 | Read app.py L1125-1138 | ✅ | `generated_at` 赋值在 `if result.get('success'):` 块内（L1125-1137）；失败时整块跳过，返回 `jsonify(result)` 原样 |
| 3 | 异常分支（except → 500）无该行 | Read app.py L1139-1140 | ✅ | 500 响应体仅 `{'success': False, 'message': f'建议生成失败: {str(e)}'}`，无 generated_at |
| 4 | 与 019D 先例 L939-940 同型 | Read 对照 L939-940 vs L1136-1137 | ✅ | 同型：函数内 `from datetime import datetime, timezone` + `timedelta as _td` + `timezone(_td(hours=8), name='Asia/Shanghai')` + `datetime.now(_CN_TZ).isoformat()`；格式/时区/函数内 import 完全一致 |
| 5 | `from datetime` 函数内 import | Read L1133-1134 | ✅ | `from datetime import datetime, timezone` / `from datetime import timedelta as _td` 均为函数内局部导入，未动文件顶部 import |
| 6 | `refresh-full` 端点未改动（M-1 备查） | Read L836-871 | ✅ | `api_refresh_full` 无 generated_at 行、结构原样（force 采集→generate_advice→price_advice 后处理→jsonify）；确认前端零调用（grep index.html 无 `/refresh-full` 引用），备查不处理符合任务书 |

补充核查（范围）：grep `app.py` 全文件 `generated_at` 赋值共 4 处——L940（019D 既有）、L1107（DB 行既有）、L1137（**019L 新增**）、L1825/L1987（每日报告既有，019D 时期）；本批次新增仅 L1137 一处，无其他端点被触碰。

## 三、V2：编译验证 —— ✅ PASS

```
python -m py_compile app.py → PY_COMPILE_OK（无错误）
```

## 四、V3：成功态功能验证 —— ✅ 全过（8/8）

**mock 方式（M-2 落实）**：Flask `test_client` POST `/api/stocks/1/advise`（jsonify 应用上下文内）；**同时 patch** `modules.advisor.generate_advice`（返回成功态假数据）与 `modules.price_advisor.generate_price_advice`（返回 `{'action_suggestion': '加仓', ...}`），杜绝真实执行查库/持仓破坏隔离；时间断言窗口 t0/t1 取东八区 mock 前后时刻。

| # | 断言 | 结果 | 实测证据 |
|---|---|---|---|
| 1 | HTTP 200 | ✅ | `status=200` |
| 2 | success=True | ✅ | `success=True` |
| 3 | **generated_at 存在** | ✅ | `generated_at='2026-08-05T23:51:02.616950+08:00'` |
| 4 | 格式 `YYYY-MM-DDTHH:MM:SS` 前缀（slice(0,16) 兼容） | ✅ | `prefix='2026-08-05T23:51:02'`，正则 `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}` 命中 |
| 5 | 时区后缀 +08:00 | ✅ | `suffix='+08:00'` |
| 6 | **时间偏差 <60s** | ✅ | `datetime.fromisoformat(generated_at)` 与东八区 now 求差：**0.1429s**（取 t0/t1 双端最坏值，均 <60s） |
| 7 | 005 行为保持（price_advice.action_suggestion 回传） | ✅ | `price_advice.action_suggestion='加仓'` 原样回传 |
| 8 | 009 行为保持（position_advice 被动态操作建议覆盖） | ✅ | `position_advice='加仓'`（mock 基线值 '持有' 被覆盖） |

## 五、V4：失败态验证 —— ✅ 全过（4/4）

**mock 组合**：`generate_advice` 返回 `{'success': False, 'message': '无数据，请先采集'}`（同时 patch price_advisor 防御）。

| # | 断言 | 结果 | 实测证据 |
|---|---|---|---|
| 1 | HTTP 200（语义与 /report-latest 一致） | ✅ | `status=200` |
| 2 | success=False | ✅ | `success=False` |
| 3 | **无 generated_at 字段（语义红线）** | ✅ | 响应 keys=`['message', 'success']`，无 generated_at |
| 4 | message 含错误信息 | ✅ | `message='无数据，请先采集'` 原样透传 |

## 六、V5：异常态验证 —— ✅ 全过（3/3）

**mock 组合**：`generate_advice` 抛 `RuntimeError('mock engine crash')`。

| # | 断言 | 结果 | 实测证据 |
|---|---|---|---|
| 1 | HTTP 500 | ✅ | `status=500` |
| 2 | **500 响应无 generated_at** | ✅ | 响应 keys=`['message', 'success']`，无 generated_at |
| 3 | message 含"建议生成失败" | ✅ | `message='建议生成失败: mock engine crash'` |

## 七、V6：前端联动验证 —— ✅ PASS（node 纯 JS 断言，v24.14.0）

复刻 `templates/index.html` L5412-5415 `_fmtGenTime` 逻辑（ISO 串 `slice(0,16).replace('T',' ')`；非字符串/空返回 '—'）：

```
_fmtGenTime('2026-08-05T23:11:21.558722+08:00') → '2026-08-05 23:11'   ✅
_fmtGenTime(undefined) → '—'   ✅
_fmtGenTime(null)     → '—'   ✅
_fmtGenTime(123)      → '—'   ✅
```

链路闭合确认（Read index.html）：L4185 刷新按钮 `loadReport(stockId, true)`（forceRefresh=true）→ L4111-4119 跳过缓存走 `_loadReportFromAdvise` → L4146 `POST /api/stocks/<id>/advise` → L4162 `renderFullReport(adviseData,...)` → L4213 `报告生成于：_fmtGenTime(adviseData.generated_at)`。后端补 generated_at 后该路径不再显示"—"。前端本批次未修改（哈希不变，见 V7）。

## 八、V7：零改动确认 —— ✅ 全过（11/11 与任务书 V7 表一致）

SHA256 前 16 位独立复算（与 019K QA V10 报告记录对照）：

| 文件 | 实测哈希 | 任务书 V7 表 | 019K 结束值（QA 019K 记载） | 判定 |
|---|---|---|---|---|
| app.py | `5C73F6EA320D838D` | `5C73F6EA320D838D`（本批次改动） | `8F8373C029E76390`（未变） | ✅ 与表一致；相对 019K 变化 = **本批次唯一功能改动** |
| templates/index.html | `79F3F330F7148D49` | `79F3F330F7148D49`（不变） | `79F3F330F7148D49` | ✅ 不变 |
| modules/advisor.py | `CA1857B0F6452B20` | `CA1857B0F6452B20`（不变） | `CA1857B0F6452B20` | ✅ 不变（B24 红线） |
| modules/data_collector.py | `B2CACC622E2A9ABA` | `B2CACC622E2A9ABA`（019N 预期改动，非本批次） | `4C847FAD888F20BA` | ✅ 与表一致；相对 019K 变化属 **019N 并行批次**（任务书明确非本批次） |
| modules/analysis_engine.py | `DF71A6FE4FD7685D` | `DF71A6FE4FD7685D`（不变） | `DF71A6FE4FD7685D` | ✅ 不变 |
| modules/alert_engine.py | `053F0CDB4DA62385` | `053F0CDB4DA62385`（不变） | `053F0CDB4DA62385` | ✅ 不变 |
| modules/scoring_engine.py | `DD9DBFBBD005B35D` | `DD9DBFBBD005B35D`（不变） | `DD9DBFBBD005B35D` | ✅ 不变 |
| modules/data_adapter.py | `0792E5006D7DCED9` | `0792E5006D7DCED9`（不变） | `0792E5006D7DCED9` | ✅ 不变 |
| config.py | `F6CE1F84B8DDACDA` | `F6CE1F84B8DDACDA`（不变） | `F6CE1F84B8DDACDA` | ✅ 不变 |
| requirements.txt | `DBE076A7458C5788` | `DBE076A7458C5788`（不变） | `DBE076A7458C5788` | ✅ 不变 |
| database/db_manager.py | `2D222BE42F298258` | `2D222BE42F298258`（不变） | `2D222BE42F298258` | ✅ 不变 |

结论：本批次功能改动仅 `app.py` 一处（V1 实证仅 L1132-1137 新增 6 行）；其余文件哈希与 019K 记录基线一致；data_collector.py 变化为 019N 并行批次既有状态，非本批次产生。

## 九、真实库零污染保护 —— ✅ PASS

实测脚本前后对 `stock_analyst.db` 取 `(size, mtime)`：`before=(7241728, 1785938606.9449813) after=(7241728, 1785938606.9449813)` —— 完全一致，零写入。全程 patch 双模块，未触发任何真实网络请求。

## 十、红线核查清单

| # | 红线 | 核查方法 | 结果 |
|---|---|---|---|
| 1 | B24（不改 advisor.generate_advice） | V1 + V7（advisor.py 哈希不变） | ✅ 仅在 app.py 端点层后处理补字段，advisor.py 零触碰 |
| 2 | 范围（仅 app.py 一端点） | V1（全文件 generated_at 赋值仅新增 L1137 一处）+ V7 | ✅ |
| 3 | 语义（失败/异常不补 generated_at） | V4/V5 实测（两态响应均无该字段） | ✅ |
| 4 | 零代码（标准库） | V1（datetime/timezone/timedelta 标准库，函数内 import）| ✅ 无新 pip 依赖；config.py/DB schema 未碰 |
| 5 | 并行隔离（019N） | V7（data_collector.py 变化为 019N 预期，非本批次）+ 独立 mock 不混用 | ✅ |
| 6 | 019D 同型先例 | V1-4（格式/时区/函数内 import 与 L939-940 完全一致） | ✅ |

## 十一、新发现问题

| 编号 | 级别 | 描述 | 处置建议 |
|---|---|---|---|
| O-1 | 观察项（非阻塞） | 任务书 V7 表表头标"019K 基线"，但 app.py/data_collector.py 两行实为 PM 复算时的"当前/预期状态"（分别对应 019L 改动后、019N 预期），与 019K QA 报告记载的 019K 结束值（`8F8373C029E76390` / `4C847FAD888F20BA`）不同。表内注解（"本批次改动"/"019N 预期改动"）已正确表达语义，QA 已按注解口径核验，不构成验收阻塞。 | PM 后续签发任务书时建议将表头改为"基线/预期状态（PM 复算）"避免歧义 |

无功能缺陷、无红线违反、无遗留临时文件（mock 脚本已删除）。

## 十二、验收结论

**✅ 通过**

| 验证项 | 结果 |
|---|---|
| V1 代码级核查 | 6/6 ✅ |
| V2 编译 | ✅ |
| V3 成功态 | 8/8 ✅（generated_at 存在、格式/时区正确、偏差 0.1429s、005/009 保持） |
| V4 失败态 | 4/4 ✅（无 generated_at） |
| V5 异常态 | 3/3 ✅（500 无 generated_at、message 正确） |
| V6 前端联动 | ✅（_fmtGenTime 纯 JS 断言） |
| V7 零改动确认 | 11/11 ✅ |
| 真实库零污染 | ✅ |
| 红线清单 | 6/6 ✅ |
| **合计** | **39/39 断言全部 PASS** |

019L 修复实现与任务书 v2 规格逐字一致，行为语义正确（成功补、失败/异常不补），前端链路闭合，范围外零改动。同意验收通过，可进入 PM+QA 双签与监理批准关闭流程。

---

**QA 签署**：QA（独立验收），2026-08-05。本报告全部证据由 QA 独立获取，未采信开发自验结论；mock 脚本在验收结束后已删除。

---

## PM+QA 双签块（019L）

**双签日期**：2026-08-05

### PM 独立核验结论

**PM 独立复跑（2026-08-05，不采信 QA 结论）**：

| 核验项 | 方法 | 结果 |
|---|---|---|
| V1 代码级核查 | Read app.py L1132-1137（成功分支补 generated_at，失败/异常分支无）| ✅ 与任务书 v2 逐字一致 |
| V2 编译 | `python -m py_compile app.py` | ✅ PASS |
| **核心功能独立复跑**（PM 自建 test_client mock）| POST /api/stocks/1/advise → generated_at 存在、时间偏差 **0.0s**、price_advice 保持 | ✅ PASS |
| V4/V5 语义（失败/异常无 generated_at）| 代码级确认（赋值在 success 块内）| ✅ |
| V7 零改动 | 范围外 10 文件哈希与 QA 报告一致（app.py 为本批次唯一改动；data_collector.py 变化为 019N/019P 并行批次）| ✅ |

**PM 核验结论**：QA 报告结论与 PM 独立复跑方向一致（代码实现、编译、核心功能、语义、零改动均实证成立）。QA 39/39 断言基于独立构造的 mock 证据（test_client + 双 patch + 真实库零污染实测），可信。观察项 O-1（任务书 V7 表头表述歧义）为非阻塞文档问题，PM 后续任务书将表头改为"基线/预期状态（PM 复算）"。**PM 同意 QA 验收结论：通过。**

### 双签签署

| 角色 | 签署人 | 日期 | 结论 |
|---|---|---|---|
| QA | QA（独立验收） | 2026-08-05 | ✅ 通过（39/39 断言 PASS） |
| PM | PM（独立核验） | 2026-08-05 | ✅ 同意（独立复跑 5/5 项通过） |

### 关闭前提醒

1. **运行实例重启**：当前运行中 app.py 为旧代码，019L 修复须重启 `python app.py` 后生效
2. **备查登记**：`/api/stocks/<id>/refresh-full`（app.py L836-871）同型遗漏，前端零调用（架构师核验 R-3），如未来前端接入须同步补 generated_at
3. **O-1 文档改进**：PM 后续任务书哈希表表头统一为"基线/预期状态（PM 复算）"

---

> **状态**：✅ QA 独立验收通过（2026-08-05）→ ✅ PM+QA 双签（2026-08-05）→ ✅ 监理批准关闭（2026-08-05）

---

## 关闭块（019L）

**监理批准关闭日期**：2026-08-05

**关闭结论**：✅ **019L 批次正式关闭**

| 流程节点 | 日期 | 状态 |
|---|---|---|
| PM 签发任务书 v1 | 2026-08-05 | ✅ |
| 架构师评审（有条件通过，M-1/M-2 并入 v2） | 2026-08-05 | ✅ |
| 监理批准 v2 | 2026-08-05 | ✅ |
| 开发执行 + 自验（13/13 PASS） | 2026-08-05 | ✅ |
| QA 独立验收（39/39 断言 PASS） | 2026-08-05 | ✅ |
| PM+QA 双签 | 2026-08-05 | ✅ |
| 监理批准关闭 | 2026-08-05 | ✅ |

**关闭时遗留事项（登记，不阻塞关闭）**：
1. 运行实例重启：用户须重启 `python app.py` 后 019L 生效
2. 备查：`/api/stocks/<id>/refresh-full` 同型遗漏（前端零调用，未来接入须补 generated_at）
3. O-1 文档改进：任务书哈希表表头统一为"基线/预期状态（PM 复算）"

> **PM 签署**：019L 已按流程完成全部节点并经监理批准，正式关闭。归档完毕。
