# 014 紧急 Bug 修复 — PM+QA 双签验收报告

| 项 | 内容 |
|---|---|
| 编号 | DEV-TASKS-20260731-014 |
| 任务 | 紧急 Bug 修复（评级列表重复 + 持仓标签错误） |
| 日期 | 2026-07-31 |
| 结果 | **✅ 验收通过，批次关闭** |

---

## 一、批次总览

| 阶段 | 文档 | 结果 |
|---|---|---|
| 开发+自验 | `docs/tasks/dev_tasks_20260731_014_dev.md` → `reports/dev_selftest_014_bugfix_20260731.md` | B1~B7 全 PASS |
| better-harness | 代码格式化（import排序+black格式化）+ tests 目录 | 纯格式化，无逻辑变更 |
| PM 终验 | 本报告 | 通过 |

---

## 二、Bug 修复确认

| Bug | 修复前 | 修复后 | 状态 |
|---|---|---|---|
| #1 评级列表重复 | 58条（29 daily + 29 intraday 混合） | 29条（仅 daily） | ✅ |
| #2 持仓标签错误 | stock_id=21 中国中免 has_position=False | has_position=True | ✅ |

---

## 三、红线核验

| 红线 | 状态 | 说明 |
|---|---|---|
| advisor.py generate_advice 函数体 | ✅ 未触碰 | better-harness 仅格式化（换行/尾逗号），逻辑不变 |
| advisor.py _build_capital_factors | ✅ 未触碰 | 同上 |
| advisor.py _read_position | ✅ 豁免修改 | 014 修复（holdings 优先），监理已批准 |
| data_collector.py 三处 if False | ✅ 未触碰 | |
| config_weights.json | ✅ 未触碰 | |
| scoring_engine.py | ✅ 未触碰 | better-harness 仅格式化（空格/引号），逻辑值不变 |
| 011 增量逻辑 | ✅ 未触碰 | |
| 012 日志/超时配置 | ✅ 未触碰 | |
| 零代码约束 | ✅ | requirements.txt 无变化（8包） |

---

## 四、better-harness 改动评估

| 项 | 结论 |
|---|---|
| 改动性质 | import 排序(isort) + 代码格式化(black) + 新增 tests/ 目录 |
| 逻辑变更 | 无（纯格式化） |
| requirements.txt | 无变化 |
| 模块加载 | app.py + 全部关键模块 import 正常 |
| 红线影响 | 无（generate_advice / _build_capital_factors / scoring_engine 仅格式化） |

---

## 五、双签

| 角色 | 判定 | 说明 |
|---|---|---|
| **QA** | ✅ 通过 | 改动极小（2文件2函数），监理批准免复验 |
| **PM** | ✅ 通过 | Bug 已修复，红线未触碰，better-harness 改动安全 |

---

**014 批次正式关闭。**
