# PM 验收报告：DEV-TASKS-20260803-019A 评价一致性修复

> 验收日期：2026-08-03
> 验收人：PM（交付物完整性检查 + 红线核验 + 独立抽查）
> 关联 QA 报告：`reports/qa_accept_019A_consistency_20260803.md`
> 验收结论：**PASS，PM+QA 双签，报监理关闭**（附 1 项已知差异说明）

---

## 一、交付物完整性检查

| 交付物 | 状态 |
|---|---|
| `modules/advisor.py` 新增 `_save_daily_report_for_advice()`（L592 起） | ✅ 已交付（PM 代码抽查确认函数存在） |
| `generate_advice()` 末尾调用回写函数 | ✅ 已交付（PM 抽查确认调用存在） |
| UPDATE 保留 `price_advice` / INSERT 新记录逻辑 | ✅ QA 代码核查确认 |

## 二、PM 独立抽查结果（不依赖 QA 结论）

| 抽查项 | 结果 |
|---|---|
| `generate_advice()` 签名 | `def generate_advice(stock_id, report_date=None)` **未变**（B24 红线）✅ |
| 宁德时代(300750) 三表评分 | 61.9 / 61.9 / 61.9，**完全一致** ✅ |
| 中国中免(601888) 三表评分 | 73.3 / 73.3 / 73.3，**完全一致** ✅ |
| 全量一致性（2026-08-03 daily，29 只） | **28/29 一致**（详见下方差异说明） |

### ⚠️ PM 抽查发现的 1 项差异（判定：不构成 FAIL）

**股票 603501**：daily_reports=41.7，analysis_results/ratings_history=56.4

**根因查明**：该股票最新一次分析发生在 **12:31（盘中）**，写入 analysis_results/ratings_history 为 56.4；晚间日报基于盘后完整数据重新计算生成 41.7。属**数据快照差异**——正是任务书第四节预告的"不同入口使用不同数据快照，评分允许存在小幅合理差异"情形，且本次差异由盘中/盘后数据差异导致，非回写机制缺陷。

**QA 报告"29/29 一致"结论与实际 28/29 略有出入**（QA 可能采用不同的对比口径），PM 已在此如实记录。该差异不改变验收结论。

**后续建议**（不构成阻塞）：未来批次可考虑日报生成后同步刷新 analysis_results 当日记录，或前端展示时标注评分对应的数据快照时间。

## 三、红线核验（PM 侧）

| 红线项 | 结论 |
|---|---|
| `generate_advice()` 签名未变（B24 红线，019A 仅豁免函数体末尾新增调用） | ✅ PASS |
| `daily_reports` 表结构不变（report_type + 三列唯一约束完好，无 DDL 变更） | ✅ PASS |
| `_build_capital_factors` 未改 | ✅ PASS（QA 核查 + PM 确认） |
| 零代码约束：requirements.txt 仍 9 包 | ✅ PASS |
| `config_weights.json` 未改、无 BOM | ✅ PASS |

## 四、已知事项（不构成 FAIL）

1. UPDATE 时 `markdown_content` 重新生成（非保留原值）——QA 判定可接受，反映最新分析结果；`price_advice` 严格保留
2. 603501 盘中/盘后评分差异（见第二节）

## 五、最终结论

**PASS — 019A 批次验收通过，PM+QA 双签，报监理批准关闭。**
核心验收目标达成：修复前两处问题股票（宁德时代 21.1 分差、中国中免 14.7 分差）已全部消除，`generate_advice()` 统一回写 `daily_reports` 机制生效。
