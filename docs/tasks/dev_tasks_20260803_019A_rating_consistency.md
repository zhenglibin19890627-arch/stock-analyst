# 开发任务书 019A — 评价不一致根因修复

**签发日期**：2026-08-03
**签发人**：PM
**批次编号**：019A
**优先级**：P0

---

## 一、背景

监理反馈：每日报告和总览看板的评价未同步，分析报告点刷新后评价不同，三处数据不一致。

### 不一致数据（2026-08-03）

| 股票 | daily_reports | analysis_results | 差值 |
|---|---|---|---|
| 宁德时代（300750) | 83.0（强烈推荐买入） | 61.9（持有观望） | 21.1 |
| 中国中免（601888) | 58.6（持有观望） | 73.3（推荐买入） | 14.7 |

### 初步诊断

- `generate_advice()` 只写 `analysis_results` + `ratings_history`，不写 `daily_reports`
- 同一天内两次引擎计算结果本身不同（数据快照不同 + v5引擎对数据变化敏感）
- 三张表存储同一份评分，职责边界模糊，缺乏单一数据源

---

## 二、执行角色

**架构师（分析阶段）→ 开发（实施阶段）**

---

## 三、架构师分析范围

1. **为什么同一天内两次引擎计算结果差异巨大（83.0 vs 61.9）？**
   - 是否因为两次调用之间底层数据发生了变化？
   - v5引擎的子项权重动态调整机制是否放大了数据变化的影响？
   - 是否需要引入数据快照机制？

2. **当前三表架构是否合理？**
   - `daily_reports` / `analysis_results` / `ratings_history` 三张表的职责边界
   - 是否应该是单一数据源（Single Source of Truth）？

3. **修复方案设计**
   - 如何确保无论哪个入口触发分析，所有展示端数据一致？
   - 是否需要评分平滑机制（差异<5分不更新）？
   - 前端是否需要显示"数据快照时间"？

---

## 四、开发实施范围（待架构师产出方案后细化）

**预计核心改动**：
- `generate_advice()` 增加回写 `daily_reports` 表的逻辑
- 确保：每日报告/批量分析/手动刷新，任意入口触发后 `daily_reports` 同步更新

**预计涉及文件**：
- `modules/advisor.py` — 核心评分入口
- `modules/daily_report.py` — 日报写入逻辑

---

## 五、验收标准

1. 任意入口触发分析后，`daily_reports`、`analysis_results`、`ratings_history` 三表评分一致
2. 总览看板、分析报告默认加载、分析报告刷新后，三处显示的评分一致
3. 不破坏现有功能（每日报告生成、批量分析、一键分析正常运行）

---

## 六、红线约束

1. **零代码约束**：不引入新pip依赖，双击start.bat即用
2. **不破坏现有功能**：每日报告生成、批量分析、一键分析、总览看板正常
3. **数据安全**：修改前先备份数据库
4. **向后兼容**：`daily_reports` 表结构不变，仅增加写入逻辑

---

## 七、关键代码位置

| 文件 | 位置 | 说明 |
|---|---|---|
| `modules/advisor.py` | L947-L1101 | `generate_advice()` 主入口 |
| `modules/advisor.py` | L506-L544 | `_save_analysis_results_for_v5()` 写 analysis_results |
| `modules/advisor.py` | L437-L470 | `_save_rating()` 写 ratings_history |
| `modules/daily_report.py` | L413-L425 | `INSERT INTO daily_reports` |
| `app.py` | L816-L833 | `/api/stocks/<id>/analyze` 端点 |
| `app.py` | L891-L1000 | `/api/stocks/<id>/report-latest` 端点 |
| `templates/index.html` | L4093-L4121 | `loadReport()` 双路径加载 |

---

## 八、执行顺序

```
Step 1: 架构师分析根因，产出修复方案
Step 2: PM审核方案，确认实施范围
Step 3: 开发实施修复
Step 4: PM验收
```
