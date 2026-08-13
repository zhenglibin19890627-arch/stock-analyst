# 开发自验报告 019L — "刷新报告"后生成时间显示"—"修复（/advise 端点补 generated_at）

**批次**：019L（P2，前端展示缺陷；DB 时间未丢，仅刷新报告路径显示"—"）
**角色**：开发工程师（单人，内嵌任务书窗口独立执行）
**自验日期**：2026-08-05
**任务书**：`docs/tasks/dev_tasks_20260805_019L_advise_generated_at.md`（v2 定稿，M-1/M-2 已并入）
**架构评审**：`docs/reviews/review_019L_advise_generated_at_20260805.md`（⚠️ 有条件通过，M-1/M-2 修订后定稿 v2）
**状态**：开发执行完成 + 自验通过（13/13），待 QA 独立验收 → PM+QA 双签 → 监理批准关闭

---

## 一、改动清单（严格 1 文件 1 端点）

| # | 文件 | 改动内容 | 位置 |
|---|---|---|---|
| 1 | `app.py` | `api_advise_stock` 成功分支补充 `generated_at`（东八区 `datetime.now(_CN_TZ).isoformat()`），与 `/report-latest` L939-940 019D 同型；`from datetime` 放函数内 | L1132-1137（成功分支 005/009 之后） |

**改动明细（6 行）**：

```python
# 019L: 补充 generated_at（报告生成时刻，与 /report-latest 019D 同型）
from datetime import datetime, timezone
from datetime import timedelta as _td

_CN_TZ = timezone(_td(hours=8), name='Asia/Shanghai')
result['generated_at'] = datetime.now(_CN_TZ).isoformat()
```

**其余文件零改动**（templates/index.html / modules/advisor.py / modules/data_collector.py / config.py / requirements.txt / database/ 等均未触碰；git diff 中其他文件变动为并行批次 019A-019P 既有未提交变更，非本批次产生）。

---

## 二、验证环境与手段

- 解释器：`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`
- 自验方式（M-2 细化）：Flask `test_client` POST `/api/stocks/1/advise`（jsonify 应用上下文内），**同时 patch** `modules.advisor.generate_advice` 与 `modules.price_advisor.generate_price_advice`（防 price_advisor 真实执行查库/持仓破坏 mock 隔离）
- 自验脚本：`C:\Users\zlb19\Desktop\Qoder cn\.dev_019L_work\selftest_019L.py`（工作区根，可复现；不含任何生产库写入）

---

## 三、自验结果（13/13 全部 PASS）

| # | 场景 | 断言要点 | 结果 |
|---|---|---|---|
| 1-1 | 成功态 HTTP | POST /advise 返回 200 | ✅ |
| 1-2 | 成功态 success | `success=True` | ✅ |
| 1-3 | **generated_at 存在** | 响应含 `generated_at`（实测 `2026-08-05T23:11:21.558722+08:00`） | ✅ |
| 1-4 | 格式 | `YYYY-MM-DDTHH:MM:SS` 前缀（前端 `_fmtGenTime` slice(0,16) 兼容） | ✅ |
| 1-5 | **时间偏差 <60s** | `datetime.fromisoformat(generated_at)` 与 mock 执行时刻（东八区）求差 **0.2s** | ✅ |
| 1-6 | 005 行为保持 | `price_advice.action_suggestion` 正常回传 | ✅ |
| 1-7 | 009 行为保持 | `position_advice` 被动态操作建议覆盖 | ✅ |
| 2-1 | 失败态 HTTP | success=False 返回 200（语义与 /report-latest 一致） | ✅ |
| 2-2 | 失败态 success | `success=False` | ✅ |
| 2-3 | **失败态无 generated_at** | 响应无 `generated_at` 字段（语义红线） | ✅ |
| 2-4 | message 错误信息 | `message` 含错误文案 | ✅ |
| 3-1 | 异常态 | generate_advice 抛异常 → HTTP 500 | ✅ |
| 3-2 | **异常态无 generated_at** | 500 响应无 `generated_at` | ✅ |

---

## 四、静态与回归验证

| 项 | 结果 |
|---|---|
| `python -m py_compile app.py` | ✅ 无错误 |
| 成功/失败/异常三分支行为（test_client mock，上述 13 项） | ✅ 全部 PASS |
| 前端联动（QA 可选项，JS 纯断言） | 未执行（QA 职责外自验范围；`_fmtGenTime` 对 ISO 串 slice(0,16) 逻辑既有且未改，019G 已验收） |
| 范围外文件 | ✅ 未修改（本会话仅编辑 `app.py` 一个文件；git 其余变动为并行批次既有状态） |

---

## 五、红线落实核对

| 红线 | 落实 |
|---|---|
| B24 红线（generate_advice 禁止修改） | ✅ 仅端点层后处理补字段，advisor.py 零触碰 |
| 范围红线（仅 app.py 一个端点） | ✅ 改动仅 `api_advise_stock` 一处 6 行 |
| 语义红线（失败路径不补 generated_at） | ✅ 用例 2/3 实证无 generated_at；正常路径行为零变化（仅增字段，005/009 行为用例 1-6/1-7 实证保持） |
| 零代码约束（无新 pip 依赖） | ✅ datetime/timezone/timedelta 标准库；config.py/DB schema 未碰 |
| 并行批次隔离（不碰 data_collector.py / 019N） | ✅ 本批次未触碰；QA mock 独立隔离，不与 019N 混用 |
| 019D 同型先例 | ✅ L1136-1137 与 /report-latest L939-940 完全同型，时间格式全站一致 |
| 明确不改范围 | ✅ templates/index.html、refresh-full 端点（前端零调用，登记备查）、config.py、requirements.txt、database/ 均未碰 |

---

## 六、开发备注

1. **实现与任务书规格一致**：代码逐字照搬任务书 L1132-1137 定稿示例，无偏差。
2. **refresh-full 同型遗漏点（M-1 登记）**：`api_refresh_full`（app.py L836-871）同样未补 generated_at；架构师核验该端点前端零调用（R-3），不构成用户可见缺陷，本批次维持备查不处理；如监理要求可零成本追加（模式与 A-1 相同）。
3. **自验脚本复现**：`C:\Users\zlb19\Desktop\Qoder cn\.dev_019L_work\selftest_019L.py`，QA 可直接运行复现（输出 13 PASS + `SELFTEST_019L_OK`）；全程 mock，无生产库读写。
4. **终端中文乱码说明**：自验输出在 PowerShell GBK 代码页下显示乱码，但 PASS 判定与 `SELFTEST_019L_OK` 退出码不受影响（脚本内断言逻辑基于结构化数据）。

---

**开发自验签名**：开发工程师，2026-08-05。以上自验在隔离 mock 环境完成，未执行正式验收（由 QA 独立执行）。
