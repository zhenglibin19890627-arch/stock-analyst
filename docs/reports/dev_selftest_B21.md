# DEV-SELFTEST-B21 自验报告

**批次**：B21（PE/PB 估值数据行对齐 + holder_increase 采集修复）
**开发**：AI 开发（GLM）
**自验日期**：2026-07-26
**任务书**：docs/tasks/dev_tasks_20260726_B21.md

---

## 一、关键纠偏：任务书基线被推翻

任务书 §一 背景称"PE/PB 填充率 0%"，该数据源自诊断脚本 `_diag_pe.py`。经实测，**该结论系诊断脚本方法错误导致的误判**。

### 根因：诊断脚本与 adapter 取行方法不一致

| 角色 | 取"最新行"方法 | 000333 取到的行 |
|---|---|---|
| 诊断脚本 `_diag_pe.py` | `f.id = (SELECT MAX(id) ...)` | id=2265, report_date=2025-06-30, **PE=NULL** |
| **data_adapter.py（真实）** | `ORDER BY report_date DESC LIMIT 1` | id=49, report_date=2026-07-15, **PE=14.58** |

PE/PB 由腾讯估值接口独立 UPDATE 到当时最新 report_date 行（如 2026-07-15），而 `fetch_a_fundamental` INSERT 的财报行 report_date 是季报日（如 2026-03-31）。`ORDER BY report_date DESC` 恰好把含 PE 的"采集日行"排在最前，故 adapter 实际能读到。

### 实测填充率（adapter 真实输出，27 只）

| 字段 | adapter 实测 | 任务书基线 |
|---|---|---|
| PE | **25/27 (93%)** ✅ | 0% ❌（误判） |
| PB | **25/27 (93%)** ✅ | 0% ❌（误判） |
| holder_increase | 0/27 (0%) | 0%（此项准确） |

无 PE/PB 的 2 只（688795 摩尔线程-U / 688802 沐曦股份-U）为未盈利科创板，腾讯接口本身无数据。

---

## 二、已实施：方案1 防御兜底（data_adapter.py）

鉴于 PE/PB 实测已达标，本次按"防御兜底"定位实施方案1：当前 PE/PB 靠"恰好 report_date 最大的旧行"撑着，状态脆弱；未来 force 重跑若 INSERT 了 report_date 更大的季报行，adapter 又会读不到。聚合回退机制可兜底保障。

### 修改内容

**文件**：`modules/data_adapter.py` — `_read_fundamental_data`（L211 起）

**逻辑**：读取最新 report_date 行后，对 `pe_ratio` / `pb_ratio` / `holder_increase` 三个字段，若为 NULL 则按 `report_date DESC` 从其他行取最近非空值回退填充。

**范围限定原则**：仅对这三个"时点值"（PE/PB 随价变动、holder_increase 为布尔标记）做跨行回退，语义正确；`gross_margin`/`revenue_yoy` 等"期间值"跨季聚合语义错误，故不纳入。

---

## 三、验收项核验

| # | 验收项 | 预期 | 实测 | 结果 |
|---|---|---|---|---|
| V1 | adapter 聚合回退 PE/PB | 最新行 NULL 时回退次新行 | 受控测试：插入 2026-09-30+PE=NULL 行后，回退取到 43.69 | ✅ PASS |
| V2 | PE/PB 填充率 | ≥89% | 93% (25/27)，修改后零回归 | ✅ PASS |
| V3 | holder_increase 回退读取 | NULL 时从其他行取 | 逻辑就绪；全库 0 行非空故当下无效 | ✅ 逻辑 PASS |
| V4 | fundamental data_quality | ≥89% | 多数 A 股 89%（PE/PB 有值） | ✅ PASS |
| V5 | news ≥100%（holder 有值时） | holder 有值则 100% | holder 全库无值，news 硬顶 50% | ⚠️ 受阻于采集端 |
| V6 | 红线守恒 | data_collector 三处 if False 不变 | L1645/L1684/L1717 原封不动 | ✅ PASS |
| V7 | 评分不变或合理变化 | PE/PB 有值后估值因子生效 | adapter 输出 PE/PB 与改前一致 | ✅ PASS |

### 受控测试详情（_diag_b21_fallback_test.py，已清理）

```
[基线] 600276 report_date=2026-03-31  PE=43.69
[模拟] 插入临时行 report_date=2026-09-30 PE=NULL（模拟新季报行）
[回退] report_date=2026-09-30  PE=43.69（从旧行回退）  ✅
[断言1] 最新行仍是 2026-09-30 且 PE 回退=43.69: PASS
[断言2] adapter 输出 PE 不变(43.69→43.69): PASS
[清理] 已删除临时行，数据还原
[断言3] 数据还原正确: PASS
```

---

## 四、遗留问题（方案1 无法解决，需单独立项）

### 1. holder_increase 全库 0 行非空（采集端问题）

聚合读取（方案1）对 holder_increase **完全无效**，因为库里根本无数据可回退。根因在采集端 `fetch_holder_increase`（data_collector.py L663）依赖雪球接口 `stock_inner_trade_xq()` 返回 None。

**修复需触碰红线文件 data_collector.py**（任务书禁止修改），需监理授权后单独立项。建议方向：雪球接口降级处理或接入备选增减持数据源。

### 2. gross_margin 等"期间值"错位

部分股票 gross_margin 在最新 report_date 行为 NULL（导致 fund_completeness 卡在 89%），但属"期间值"，不可跨 report_date 聚合。需在采集端确保写入最新行，非本次读取侧修复范畴。

---

## 五、结论

- 方案1 防御兜底已实施并通过受控测试，PE/PB 读取稳健性提升，零回归。
- PE/PB 实测填充率 93%，已达验收线（任务书 0% 基线系诊断方法误判）。
- holder_increase 需采集端单独立项修复（红线文件，待监理授权）。

**自验结论：方案1 部分 PASS；holder_increase 维度待采集端修复后复验。**
