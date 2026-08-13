# 开发自测报告 019S — 主力资金弃用同花顺（移除 ths_total 顶替）

| 项 | 内容 |
|---|---|
| 批次号 | 019S |
| 开发 | Stock Analyst 项目开发角色 |
| 自测日期 | 2026-08-09（周日，非交易日） |
| 任务书 | `docs/tasks/dev_tasks_20260809_019S_remove_ths_capital_fallback.md` |
| 架构评审 | `docs/reports/review_019S_remove_ths_capital_fallback_20260809.md`（附加约束 7 条逐条照办） |
| 监理裁定 | 主力净流入链路：东财三层 → 新浪 lscjfb 主力口径(sina_main) → 估算兜底（is_estimated=1 仅展示不参评）；ths_net_inflow 辅助指标保留；存量 27 行按方案 b 处置 |

---

## 一、结论

**完成。** 五个开发任务全部实施完毕并通过自测：

1. THS 顶替写入块整块移除（含 L2702 备注注释），新浪顶替失败自然落回估算兜底，链路不断、不抛异常。
2. `'ths_total'` 字面量 3 处 SQL（前置校验 / 补采清单 / 估算守卫×3）全部保留不动，旁补保留原因注释（评审约束 1）。
3. 注释全量对齐两级阶梯（评审约束 3），全文检索 `ths_total` / `同花顺` 无残留误导表述（docs/ 归档豁免；`scripts/backfill_capital_sina_019q.py` 诊断脚本豁免，仅登记）。
4. 存量 27 行按方案 b 处置完成：备份 → 限定 WHERE 的 UPDATE → `changes()==27` → 三条只读断言全过（评审约束 2）。
5. 自测 7 项全部 PASS；`python -m pytest tests/` 343 passed（与 019Q 验收基线一致）；ruff 仅 2 项既有告警（019A/018 遗留，不在本批次 diff 内）。

### 改动文件清单（行号为改动后现状）

| 文件 | 改动 | 行号范围 |
|---|---|---|
| `modules/data_collector.py` | **Task 1** THS 顶替写入块移除，替换为 019S 落回估算注释 | 原 L2702-2752 → 现 L2706-2707 |
| 〃 | **Task 3** `fetch_capital_flow` docstring 阶梯说明改两级 | L2359-2372 |
| 〃 | **Task 2+3** 前置校验注释对齐 + 字面量保留注释（SQL 本身 L2404 不动） | L2381-2402 |
| 〃 | **Task 3** L2637 日志文案改"尝试新浪顶替 → 估算兜底" | L2639-2641 |
| 〃 | **Task 3** 新浪顶替块头注释去除 THS 提及 | L2644-2652 |
| 〃 | **Task 2** 估算 UPDATE 守卫旁补保留注释 3 处（SQL L2729/2768/2807 不动） | L2725 / L2764 / L2803 |
| 〃 | **Task 3** `fetch_capital_flow_batch` docstring 改两级阶梯 | L1660-1676 |
| 〃 | **Task 2+3** 补采清单注释 + 字面量保留注释（SQL 本身 L1804 不动） | L1785-1791 |
| `modules/daily_report.py` | **Task 3** `_capital_retry_once` docstring 同步两级阶梯 | L172-183 |
| `database/db_manager.py` | **Task 3** capital_source 列注释补充 019S 说明 | L964-965 |
| `templates/index.html` | **Task 3** L2499/2516 注释更新（含"无害死分支"说明；L2502/2506/2519 同花顺标注保留不删；L2068/2579 通用映射不改） | L2500-2501 / L2519-2520 |

红线核对：未触碰 `advisor.generate_advice`、评分权重、风控阈值；未新增/删除表结构（capital_source、ths_net_inflow、is_estimated 列原样）；除 Task 4 处置外无任何生产库写操作；未点击个股详情实时分析。

---

## 二、逐项自测结果

### 2.1 对应 Task 5 各项（全部在 :memory:/临时库副本执行，禁生产库写操作）

| # | 自测项 | 方法 | 结果 |
|---|---|---|---|
| T5-1 | 新浪失败 → 落回估算 | 临时库 mock：EM 三层 None、sina_main None、新浪估算源有值 | **PASS**：返回 `('estimated', ...)`；行 `is_estimated=1`、`capital_source=NULL`、`main=500.0`；`data_status.status='estimated'`；不抛异常 |
| T5-2 | 同日已有顶替行 → 估算 UPDATE 守卫生效 | 预置同日期 `sina_main` 行与 `ths_total` 行各 1，新浪失败触发估算 | **PASS**：估算后两行均未被覆盖（sina_main 行 main=111/cs='sina_main'/est=0 原样；ths_total 行 main=222 原样），每行仅 1 条记录（INSERT OR IGNORE 亦未重复插入） |
| T5-3 | 补采清单仍包含存量 ths_total 行 | 构造 600519 真数据行 + 000001 ths_total 行 + 300750 sina_main 行（当日），mock THS 批量成功 + `_em_batch_collect` 记录入参 | **PASS**：补采清单 `['000001','300750']`（ths_total/sina_main 行仍进入清单，真数据行被排除），source 含"资金面补采" |
| T5-4 | EM 恢复重采 → 顶替行被 INSERT OR REPLACE 覆盖、capital_source 归 NULL（回归 019K D1） | 处置前备份副本 `stock_analyst_backup_019S_20260809_2209.db` 复制为临时库，mock EM 第一层返回对应日期行 | **PASS**：ths_total 行（688017/08-05）覆盖后 `cs=NULL`、`est=0`、`main=8888.89`；sina_main 行（600276/08-07）同样归 NULL。ths_net_inflow 随 REPLACE 清空属 019K R-7 既有行为（当日 THS 批量可重建），登记 |
| T5-5 | `python -m pytest tests/` 全绿 | 项目根执行 | **PASS**：343 passed（与 019Q 验收基线一致，零破坏） |
| T5-6 | 防覆盖四态（EM > 新浪 > 估算）对照 019Q 验收结论逐条回归 | 临时库逐态构造 | 见下表 |
| T5-7 | 生产库处置后只读总核验 | 只读查询 | **PASS**：见第三节与核验输出 |

### 2.2 防覆盖四态回归明细（对照 019Q QA E1/E3/E5/E6/E7）

| 状态 | 场景 | 断言 | 结果 |
|---|---|---|---|
| 四态1 | EM 恢复 → 覆盖顶替行且归位 NULL | T5-4（ths_total + sina_main 双覆盖） | **PASS** |
| 四态2 | EM 真实行存在 → 前置跳过（防降级覆盖） | 预置 cs=NULL/est=0/main=9999 → 返回 `'success' 同日跳过`，main 保持 9999 | **PASS** |
| 四态3 | 估算 → 不得覆盖顶替行（NOT IN 守卫生效） | T5-2（sina_main + ths_total 双场景，行原样、rowcount 语义等价） | **PASS** |
| 四态4a | 新浪 → 覆盖估算行（019Q E7） | 预置 est=1/cs=NULL/main=100 → 返回 `'fallback'`，行变 cs='sina_main'/est=0/main=300/四档齐 | **PASS** |
| 四态4b | 新浪 → 覆盖存量 ths_total 行（019Q E6） | 预置 cs='ths_total'/main=222 → 返回 `'fallback'`，行变 cs='sina_main'/est=0/main=300 | **PASS** |

### 2.3 任务书验收标准 6 条

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | 不再产生新顶替：任何路径不再写 capital_source='ths_total'；东财+新浪全失败落回估算（is_estimated=1），链路不断不抛异常 | **通过** | grep 全仓无任何写 `'ths_total'` 的代码路径（仅 4 处 SQL 字面量为只读防御，见 Task 2）；T5-1 实证估算落位；py_compile 通过 |
| 2 | 防覆盖逻辑不被破坏：新浪顶替行、东财真数据行既有保护逐条回归 | **通过** | §2.2 四态全 PASS（对照 019Q E1/E3/E5/E6/E7） |
| 3 | 估算兜底回归：估算行 is_estimated=1、仅展示、不参与评分（4 条读取路径过滤不变） | **通过** | 4 条读取路径（`data_adapter._read_capital_data` L273-290、`advisor._build_capital_factors` L1289-1337、`analysis_engine._read_capital_data` L123-132、`alert_engine.check_capital_outflow` L199-226）本批次零改动（只读核验）；pytest 全绿 |
| 4 | 存量 27 行处置（方案 b）及只读断言 | **通过** | 见第三节 |
| 5 | 回归面：周一定时日报批次不受影响；自测不做盘中破坏性实验 | **通过** | 全部自测在周日非交易时段用临时库/只读完成；生产库仅 Task 4 处置一次写操作（22:09，非交易日）；处置后生产库 mtime=2026-08-09 22:09:33 不再变化 |
| 6 | 零数据破坏：自测只读/临时库副本，禁生产库写实验 | **通过** | 自测脚本全部指向 `%TEMP%\tmp_019s_*` 临时库与备份副本；生产库只读核验无写语句 |

---

## 三、Task 4 存量 27 行处置记录

### 3.1 备份

- 备份文件：`stock_analyst_backup_019S_20260809_2209.db`（留在项目目录 `stock_analyst/`）
- 大小：8,134,656 字节（与处置前生产库一致）
- 备份时刻：2026-08-09 22:09（处置前）

### 3.2 处置 SQL（一次性脚本 `_tmp_019s_dispose.py`，已执行后删除；WHERE 严格限定）

```sql
UPDATE raw_capital_flow SET main_net_inflow=NULL, is_estimated=1, capital_source=NULL
WHERE capital_source='ths_total'
```

- 禁止通配 UPDATE、禁止 INSERT OR REPLACE：满足（仅上述限定 UPDATE）。
- 保留 ths_net_inflow 与其他字段：满足（断言核验）。

### 3.3 执行输出（原文节选）

```
[处置前] capital_source='ths_total' 行数 = 27
[处置前] main_net_inflow != ths_net_inflow 行数 = 12；符号相反行数 = 6
[处置前] 日期分布 = {'2026-08-05': 4, '2026-08-06': 23}
[UPDATE] changes() = 27
[COMMIT] 已提交
[断言1] capital_source='ths_total' 残余行数 = 0  (期望 0)
[断言2] is_estimated=0 且顶替口径行残余 = 23  (期望 0)
[断言3] 被处置 27 行核验：异常行数 = 0  (期望 0)
```

### 3.4 缺陷与修复（首轮断言2谓词错误）

首轮脚本断言 2 谓词写为 `capital_source IN ('ths_total','sina_main') AND is_estimated=0`，把 **08-07 的 23 行合法 sina_main 行（019Q 设计，is_estimated=0）** 误计入"曾为顶替口径残余"，导致 RESULT 打印"未通过"。**实际处置 UPDATE 已正确提交**（changes()==27 断言通过、断言 3 对 27 行逐一核验 0 异常），错误仅在我自行编写的断言 2 谓词。

修复：改写为只读复核脚本 `_tmp_019s_verify.py`（修正谓词 `capital_source='ths_total' AND is_estimated=0`），复核输出：

```
[断言1] capital_source='ths_total' 残余行数 = 0  (期望 0)
[断言2] is_estimated=0 且曾为 ths_total 顶替口径的残余行数 = 0  (期望 0)
[佐证] is_estimated=0 且 capital_source='sina_main' 行数 = 23  (期望 23，019Q 合法顶替)
[断言3] 08-05/08-06 处置特征行数（est=1, cs=NULL, main=NULL, ths 原值仍在）= 27  (期望 27)
[佐证] is_estimated=1 总行数 = 63  (期望 63 = 原估算36 + 处置27)
[RESULT] 处置复核通过（全部断言符合预期）
```

> 首轮 [断言1]/[断言3] 本已通过；两条脚本执行后均已删除，处置结果以数据库为准（可被 §3.5 只读复核复现）。

### 3.5 处置后只读总核验（生产库，纯只读；输出见 §2.1 T5-7 与下方）

```
=== 核验1: capital_source 分布 = {None: 3063, 'sina_main': 23}  → 只剩 NULL / sina_main，ths_total=0
=== 核验2: ths_total 残余 = 0
=== 核验3: is_estimated 分布 = {0: 3023, 1: 63}  （63 = 原估算36 + 处置27）
=== 核验4: 08-05/08-06 处置特征行数 = 27  （est=1、cs=NULL、main=NULL、ths 原值在）
=== 核验5: 08-05/08-06 计为"已完成"的真实行数 = 21（其余 27 只被处置股票计为缺口，东财恢复可回补）
=== 核验6: 估算行带来源标记 = 0
[RESULT] 生产库只读总核验通过
```

> 核验 5/6 首轮断言标准亦曾写错（核验5 误以为两日应无真实行——实际 21 行为其他股票的正常 EM 真数据；核验6 误把"估算行带 main 值"当异常——按 019E 设计估算行 main 值仅供展示，`capital_source` 恒 NULL 即无来源标记污染）。修正断言标准后复核通过，数据本身自始正确。

---

## 四、缺陷与修复记录

| # | 缺陷 | 根因 | 修复 | 状态 |
|---|---|---|---|---|
| 1 | Task 4 首轮脚本断言 2 误报"未通过" | 断言谓词误含合法 `sina_main` 行（23 行），属自测脚本断言编写错误，**非数据/代码缺陷** | 修正谓词重跑只读复核，三条断言全过 | 已闭环 |
| 2 | 生产库只读总核验首轮核验 5/6 误报 | 断言标准编写错误（见 §3.5 说明），**非数据异常** | 修正断言标准（核验5 期望 21 真实行 + 27 处置行；核验6 只查来源标记污染） | 已闭环 |

无代码功能缺陷发现。代码层 py_compile 通过、ruff 无新增告警（仅既有 2 项：daily_report L30 import 排序=019A 遗留、data_collector L1736 turnover_yuan 未使用=018 遗留，均不在本批次 diff 内）。

---

## 五、遗留项（登记，待裁定/待跟进）

1. **'ths_total' 字面量条件性技术债（评审约束 1 既定）**：3 处 SQL 字面量本批次保留。存量 ths_total 行现已清零（§3 只读断言实证），**待新批次评审确认后**可简化：估算守卫改仅 `'sina_main'`，前置校验/补采清单改 `NOT IN ('sina_main')` 或删除。本批次按红线不擅动。
2. **`scripts/backfill_capital_sina_019q.py` L94 注释**（"可覆盖 THS/估算行"）：评审判定为诊断脚本豁免，本批次未改；若该脚本投入使用须排产于 16:30 核验窗口之外，并建议后续批次顺手更正注释。
3. **`docs/PM_接手提示词_20260809.md` L65-67** 仍描述旧三级阶梯（含 THS）：运维入口文档，评审列为"可选整改，不阻塞"，本批次未改。
4. **EM 覆盖存量顶替行会清空 ths_net_inflow（019K R-7 既有行为）**：T5-4 实证，属既有设计（当日 THS 批量重跑可重建），本批次未变更。
5. **R-2 附加观察（v5 缺位 85 分语义）**：监理已登记为既有设计，本批次未触碰。
6. **生产库备份文件** `stock_analyst_backup_019S_20260809_2209.db` 保留在项目目录，供 QA/监理复现处置前状态；确认无碍后可自行归档/删除（本批次不擅动）。

---

## 六、红线自查

| 红线 | 结论 |
|---|---|
| 不触碰 `advisor.generate_advice`（B24）、评分权重、风控阈值 | ✅ 零改动 |
| 不新增/删除表结构（capital_source、ths_net_inflow、is_estimated 列原样） | ✅ 仅数据行 UPDATE |
| 除 Task 4 处置外禁止生产库写操作 | ✅ 自测全走临时库/备份副本；生产库 mtime 自 22:09:33 起未变 |
| 禁止触发个股详情实时分析（B11） | ✅ 未启动 Flask、未点击任何页面 |
| 08-10 16:30 定时核验窗口前后不做生产库写操作 | ✅ 全部改动与处置在 08-09（周日）完成 |

开发自测完毕，报告输出，等待 PM 接手核验。
