# DEV-TASKS-20260803-019C-ARCH：019C 东财采集重试增强与错峰分批优化 — 架构方案评审任务书

> **签发人**：PM  | **签发日期**：2026-08-03 | **状态**：待架构师执行

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：架构师

**职责边界**：
- 评审 PM 签发的 019C 开发任务书（`docs/tasks/dev_tasks_20260803_019C_em_retry_optimization.md`），聚焦参数取值与策略决策点
- 对每个决策点给出明确裁定（采纳/修改/否决）+ 理由
- 评估最坏耗时与日报生成总时长的影响
- **不编码、不验收、不写功能代码**
- 交付物：`docs/reviews/review_019C_em_retry_optimization_20260803.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 架构师仅做方案评审，不执行任何代码修改

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db`（在 stock_analyst 子目录内！） |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包） |
| 前序批次 | 019B 已双签关闭：东财失败根因 = 间歇性反爬；`_http_get_em()` 已具备多轮重试 + 22 个 UA 池 + 随机延迟 1.5~3.5s 能力 |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 推荐模型 | **kimi k3**（评审类任务） |
| 窗口类型 | **Quests 独立窗口** |
| 执行模式 | 单代理 agent |
| 交付物 | `docs/reviews/review_019C_em_retry_optimization_20260803.md` |

---

## 一、需求背景

### 1.1 问题描述

019B 批次验收时 PM 记录待决策项：东财逐只采集的重试轮数被调用处硬编码压制为 1，且批量采集无错峰、无分批，间歇性反爬下仍存在大面积失败风险。PM 已据此签发 019C 开发任务书，**现需架构师对其参数取值与策略做评审裁定**。

### 1.2 PM 现场核验的代码现状（2026-08-03）

| 文件 | 位置 | 现状 |
|---|---|---|
| `config.py` | L32 | `MAX_RETRIES = 3`（已存在） |
| `modules/data_collector.py` | L185-L248 | `_http_get_em(url, params, timeout=15, max_retries=None)`：`rounds = max_retries if max_retries else MAX_RETRIES`；每轮 2 次尝试；`timeout_tuple = (5, 10)`（connect=5s, read=10s）；轮间随机延迟 1.5~3.5s；22 个 UA 池 |
| `modules/data_collector.py` | L1365 | `_fetch_capital_flow_em_individual()` 调用处硬编码 `max_retries=1` |
| `modules/data_collector.py` | L1430 | push2 实时接口调用处硬编码 `max_retries=1` |
| `modules/data_collector.py` | L1223-L1230 | 同花顺批量失败回退 EM 逐只循环（股票间无任何间隔） |
| `modules/data_collector.py` | L2643 | 单只分析主链路 `fetch_capital_flow(symbol, market)`（用户单只分析入口调用） |

### 1.3 耗时模型（PM 估算，待架构师复核）

单只最坏耗时（`max_retries=3`，每轮 2 次尝试，单次超时上限 15s + 轮间延迟 1.5~3.5s）：

```
3 轮 × (2 次尝试 × 15s + 轮间延迟 3.5s) ≈ 100s/只（最坏）
当前硬编码 max_retries=1 时最坏 ≈ 33s/只
```

系统内 A 股约 23 只：若批量回退 EM 循环叠加错峰间隔 2~5s，全量最坏耗时显著上升，**需架构师评估与日报生成总时长（012 批次已关注卡住风险）的兼容性**。

---

## 二、架构师需裁定的决策点

### DP-1：重试取值方式 — 移除硬编码 vs 显式传参

**PM 倾向**：移除 L1365/L1430 的 `max_retries=1`，回归默认值 `MAX_RETRIES=3`（config.py 集中管控）

**需架构师裁定**：
- 移除硬编码回归默认 vs 显式传 `max_retries=3`，哪个更利于后续维护？
- 东财接口是否需要区别于全局 `MAX_RETRIES` 的独立常量（如 `EM_MAX_RETRIES`）？定义在 config.py 还是 data_collector.py 模块顶部？
- 注意 012 批次评审曾讨论 MAX_RETRIES 合理性，本次只调东财链路，不得动全局默认值

### DP-2：错峰延迟的适用范围与取值

**PM 建议值**：请求间随机延迟 2~5 秒

**需架构师裁定**：
- **关键问题：单只分析主链路（L2643）是否加延迟？** 单只分析为用户实时触发，加延迟影响体验；PM 倾向错峰仅作用于批量场景（L1223 循环），单只依赖 `_http_get_em` 内部已有的轮间随机延迟
- 延迟取值 2~5s 是否合理？与 `_http_get_em` 内部轮间延迟 1.5~3.5s 是否叠加过度？
- 延迟用 `random.uniform` 即可（stdlib），是否需考虑与 UA 轮换的协同

### DP-3：连续失败退避策略

**PM 初步思路**：批量循环中连续失败 N 只后，加长后续间隔（如翻倍）或暂停一个冷却期

**需架构师裁定**：
- 是否需要退避机制？阈值 N 与冷却时长取值
- 是否引入"熔断"：连续失败超过阈值时提前终止本轮批量采集（剩余股票留给下次），避免无效请求加剧反爬封禁
- 注意已有先例：同花顺链路 L1107 起有 `_THS_CONSECUTIVE_FAIL_COUNT` 连续失败计数机制，可否参照其模式

### DP-4：分批参数取值

**PM 建议值**：每批 5~10 只，批间间隔 30~60 秒

**需架构师裁定**：
- 批大小与批间间隔的最终取值（结合系统内约 23 只 A 股的规模给出总耗时估算）
- 分批仅作用于批量回退 EM 循环（L1223），日报主循环（逐只 collect_stock_data）是否也适用？日报循环本身是逐只串行多维度采集，天然有间隔，PM 倾向不动
- 参数集中定义位置：模块顶部常量 vs config.py

### DP-5：最坏耗时与日报总时长兼容性

**需架构师裁定**：
- 按最终参数估算：批量回退场景全量 23 只的期望耗时与最坏耗时
- 是否需要为批量采集设整体超时上限（与 012 批次的超时机制呼应）
- 若耗时不可接受，是否允许分批参数偏保守（宁可慢、保成功率）——东财数据是主力净流入唯一真实来源，PM 倾向成功率优先

---

## 三、红线清单（架构师评审时需注意不可违反）

| 红线 | 说明 | 位置 |
|---|---|---|
| 签名红线（011） | `fetch_capital_flow(symbol, market)` 签名不变，不加 force_full 参数 | data_collector.py L1683 |
| 签名红线（B24） | `generate_advice()` 签名不变（019C 不应触碰 advisor.py） | advisor.py |
| 主链路红线 | 东财三层降级（push2his → push2 → akshare）结构不破坏 | data_collector.py L1683 起 |
| 禁用红线 | 估算数据源（新浪/腾讯/网易）维持 `if False` 硬禁用 | data_collector.py |
| 评分红线 | `_build_capital_factors` 与 `scoring_engine.py` 不可修改 | advisor.py / scoring_engine.py |
| 配置红线 | `config_weights.json` 不可修改（含 BOM 检查） | |
| 零代码约束 | 无新 pip 依赖（当前 9 包），错峰分批仅用 stdlib（time/random） | requirements.txt |
| 范围约束 | 改动限于 `modules/data_collector.py`（及必要的日志） | |

---

## 四、验收标准

架构师评审报告需包含：

1. **每个决策点（DP-1~DP-5）的明确裁定**：采纳/修改/否决 + 理由 + 最终参数取值
2. **最坏耗时估算**：按最终参数给出单只与批量全量的期望/最坏耗时
3. **红线复核**：确认 019C 开发任务书方案不触碰第三节红线清单
4. **改动范围确认**：最终修改点清单（预计仅 `modules/data_collector.py`）
5. **风险提示**：评审中发现的遗漏或潜在回归点（如有）

---

## 五、参考资料

| 文件 | 用途 |
|---|---|
| `docs/tasks/dev_tasks_20260803_019C_em_retry_optimization.md` | 019C 开发任务书（评审对象） |
| `reports/pm_accept_019B_eastmoney_20260803.md` | 019B 验收报告（待决策项 #1 来源） |
| `reports/dev_diag_019B_em_failure_20260803.md` | 019B 排查报告（间歇性反爬根因） |
| `modules/data_collector.py` L185-L248 | `_http_get_em` 重试机制实现 |
| `modules/data_collector.py` L1365 / L1430 | 两处 `max_retries=1` 硬编码调用点 |
| `modules/data_collector.py` L1223-L1230 | 批量回退 EM 逐只循环 |
| `modules/data_collector.py` L1107 起 | 同花顺连续失败计数机制（退避策略参照） |
| `config.py` L32 | `MAX_RETRIES = 3` |

---

> **PM 备注**：本任务书已内嵌角色定义，监理可直接全文粘贴到 Quests 窗口。架构师评审通过并报监理批准后，019C 开发任务书将按评审裁定修订定稿，再交开发执行。
