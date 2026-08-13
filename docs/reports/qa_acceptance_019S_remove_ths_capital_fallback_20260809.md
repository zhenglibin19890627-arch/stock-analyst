# QA 验收报告 — 019S 主力资金弃用同花顺（移除 ths_total 顶替）

| 项 | 内容 |
|---|---|
| 批次号 | 019S |
| QA | Stock Analyst 项目 QA 角色（独立验收，全程只读为主） |
| 验收日期 | 2026-08-09（周日，非交易日） |
| 任务书 | `docs/tasks/dev_tasks_20260809_019S_remove_ths_capital_fallback.md` |
| 开发自测 | `docs/reports/dev_selftest_019S_remove_ths_capital_fallback_20260809.md` |
| 架构评审 | `docs/reports/review_019S_remove_ths_capital_fallback_20260809.md` |
| 监理裁定 | 主力净流入链路：东财三层 → 新浪 lscjfb 主力口径(sina_main) → 估算兜底（is_estimated=1 仅展示不参评）；ths_net_inflow 辅助指标保留；存量 27 行方案 b 处置 |

---

## 一、结论

**验收通过。**

任务书第三节验收标准 6 条全部独立复核通过；无功能缺陷。QA 过程自身发生 1 项过程异常（场景脚本误连生产库，经全表对比实证**零数据影响**），已如实登记并完成自身产物清理；另登记 2 项低危观察（均为既有行为，非本批次引入）。

---

## 二、验收清单 Q-1 ~ Q-7 逐项结果

### Q-1 不再产生新顶替（标准 1）— **通过**

| 检查项 | 结果 | 证据 |
|---|---|---|
| 1a. 全仓无写 'ths_total' 路径 | ✅ | `grep 'ths_total' *.py`（排除 docs/、scripts/、reports/、logs/）：`modules/data_collector.py` 中 'ths_total' 仅出现在 **5 处 SQL 字面量 L1804 / L2404 / L2729 / L2768 / L2807**（全部为 `NOT IN ('ths_total','sina_main')` 只读防御谓词）+ 注释；全仓无任何 INSERT/UPDATE 将 `capital_source` 写为 'ths_total'（写入点仅 'sina_main'（L2668/L2687）与 EM 的 NULL（L2472/L2538/L2600）） |
| 1b. THS 顶替块已移除 | ✅ | `fetch_capital_flow` 中新浪顶替块（L2653-2705）后**不再存在 THS 写入块**，L2706-2707 仅余 019S 移除注释；新浪失败后直接落回估算源3/4/5（L2709 腾讯K线 / L2750 新浪财经 / L2789 网易），链路不断 |
| 1c. 临时库场景复测（对照自测 T5-1） | ✅ | 见 §三 S1：EM 三层失败 + 新浪顶替失败 → 返回 `('estimated', ...)`，落位行 `is_estimated=1`、`capital_source=NULL`、`main=500.0`、`data_status.status='estimated'`，**不抛异常**。与自测 T5-1 完全一致 |

### Q-2 防覆盖不被破坏（标准 2）— **通过**

对照自测 §2.2 四态表独立复测 **5 个状态**（超出要求的三态）：

| 场景 | 对应四态 | 结果 | 证据（临时库） |
|---|---|---|---|
| S3 EM 恢复重采 → 覆盖 ths_total 顶替行、归位 NULL | 四态1（T5-4） | ✅ | 行变 `cs=NULL / est=0 / main=8888.89`，返回 'success' |
| S2 估算 → 不覆盖同日 sina_main / ths_total 行 | 四态3（T5-2） | ✅ | 两行均原样（222/est=0、111/est=0），无重复行（INSERT OR IGNORE 未插入） |
| S4 新浪 → 覆盖估算行 | 四态4a（019Q E7） | ✅ | 行变 `cs=sina_main / est=0 / main=300 / 四档齐`，返回 'fallback' |
| S5 新浪 → 覆盖存量 ths_total 行 | 四态4b（019Q E6） | ✅ | 行变 `cs=sina_main / est=0 / main=300`，返回 'fallback' |
| S6 EM 真实行存在 → 前置校验跳过（防降级覆盖） | 四态2 | ✅ | 返回 'success 同日跳过'，main 保持 9999 未被覆盖 |
| S7 ths_total 行**不**触发前置跳过（NOT IN 防御） | 标准 2/3 关联 | ✅ | 预置 ths_total 行后 EM 重采仍执行，覆盖归 NULL（019K M-11 回补红线成立） |

### Q-3 估算兜底语义（标准 3）— **通过**

1. **4 条读取路径过滤 is_estimated=1 本批次零改动**（与评审报告行号逐一对照）：

| 路径 | 过滤位置 | 结论 |
|---|---|---|
| `data_adapter._read_capital_data` | L282 `AND (is_estimated = 0 OR is_estimated IS NULL)`（评审 L273-290） | ✅ 未变 |
| `advisor._build_capital_factors` | L1304 同上（评审 L1289-1337） | ✅ 未变 |
| `analysis_engine._read_capital_data` | L132 同上（评审 L123-132） | ✅ 未变 |
| `alert_engine.check_capital_outflow` | L205 同上（评审 L199-226） | ✅ 未变 |

   补充扫描：全仓 `raw_capital_flow` 读取点共 33 处，其余为写入/状态/展示（data_collector 各写入块、daily_report L224 缺口 SQL 已带 `is_estimated` 条件、data_adapter L502 为 `__main__` 调试入口、export_engine L280 为导出展示），均非评分路径。

2. **生产库只读断言**：`is_estimated=1 AND capital_source IS NOT NULL` 行数 = **0**（期望 0，估算行无来源标记污染）✅

### Q-4 存量 27 行处置复核（标准 4，方案 b）— **通过**

全部只读对比备份 `stock_analyst_backup_019S_20260809_2209.db`（8,134,656 字节）与生产库：

| # | 断言 | 结果 |
|---|---|---|
| 1 | 备份中 `capital_source='ths_total'` 行数 = **27**（08-05×4、08-06×23，全部 is_estimated=0） | ✅ 实测 27 |
| 2 | 生产库 ths_total 残余 = 0；capital_source 分布仅 NULL(**3063**) / sina_main(**23**) | ✅ |
| 3 | 被处置 27 行：`is_estimated=1`、`capital_source=NULL`、`main_net_inflow=NULL`，**其余字段（含 ths_net_inflow）与备份逐字段一致**（27/27 核验通过，行 id 保留 → 确认为 UPDATE 非 REPLACE）；抽查：宁德时代 300750 08-06 ths=-91700 ✅、中免 601888 08-06 ths=-15500 ✅（与监理裁定触发案例吻合） | ✅ |
| 4 | 防误伤：备份非 ths_total 行（3059 行）× 13 列 = **39767 单元格全量逐一对比，0 差异**（非抽样，全量比对）；另 29 张表行数备份/生产**全部一致**（daily_reports 481、analysis_results 448、raw_kline 7211 等） | ✅ |

### Q-5 回归面（标准 5）— **通过（附 1 项过程异常登记）**

| # | 检查项 | 结果 | 证据 |
|---|---|---|---|
| 1 | `python -m pytest tests/` | ✅ | **343 passed**（与 019Q 基线一致，零破坏） |
| 2 | 生产库 mtime 自查 | ⚠️ 见登记 D-1 | 当前 mtime=2026-08-09 22:43:29（dev 报告 22:09:33）；变化根因=QA 自身脚本失误触发 WAL checkpoint（详见 D-1），**无数据写入**（Q-4 全表对比实证），处置内容无变化 |
| 3 | 周一 16:10 定时日报链路 | ✅ | app 进程 **PID 13768** 监听 127.0.0.1:5000，`/api/health` 返回 `{"status":"running","success":true}`；`daily_report.py` 中 'ths_total' 仅出现在 docstring L177/180（L172-183 文档区），缺口 SQL L224-228 语义未变；`app.py` 无 019S 痕迹（L1291 为 018 辅助指标注释） |

### Q-6 零数据破坏（标准 6）— **通过（附 1 项过程异常登记）**

- 全部库查询以 `mode=ro` URI 只读执行；场景测试全部在 `%TEMP%\qa_019s\` 临时库；未点击个股详情（未做任何 B11 触发的 HTTP 页面操作，仅 `/api/health` 只读 GET）；未触发任何采集。
- QA 过程异常 D-1 详情见下，已实证零数据影响并清理自身产物。

### Q-7 注释一致性（评审批准前提 ③）— **通过**

- 抽查注释改动点全部对齐"两级阶梯"表述：
  - `data_collector.fetch_capital_flow` docstring L2360-2373（"链路：东财三层 → 新浪 lscjfb 主力口径(sina_main) → 估算兜底（仅展示不参评）"）✅
  - `fetch_capital_flow_batch` docstring L1660-1674（"019S 起不再使用同花顺顶替主力净流入（ths_total 仅为历史存量，不产生新顶替行）"）✅
  - 前置校验注释 L2384-2400、估算守卫旁保留原因注释 L2725/2764/2803、补采清单注释 L1784-1791 ✅
  - `daily_report.py` L172-182、`db_manager.py` L964-965、`index.html` L2500-2501/2519-2520（含"无害死分支"说明）✅
- 全文检索 `同花顺`：代码内残留 26 处均为**辅助指标语境**（THS 批量预取日志/`ths_net_inflow` 列注释）、**前端辅助列标注**（index.html L2508/2513/2523/2528，评审裁定保留）或**无关算法注释**（data_adapter L124 RSI 算法、index.html L2464 K线免责声明），**无任何"顶替主力"误导表述** ✅（前端辅助标注保留属预期，符合评审约束 6 与开放项 A 裁定）

---

## 三、临时库场景复测明细（Q-1/Q-2 独立实证，非复读自测）

方法：临时 SQLite 库（`db_manager.init_database` 建全 schema + 预置 stocks/raw_capital_flow），`mock` 网络函数（EM 三层、新浪 lscjfb、估算源），调用真实 `fetch_capital_flow` 断言落位。**生产库零接触**（v1 打补丁正确；v2 误连详见 D-1）。

| 场景 | 构造 | 断言结果 |
|---|---|---|
| S1（对照 T5-1） | EM 三层 None + sina_main None + 新浪估算源有值 | `('estimated', ...)`；行 est=1 / cs=NULL / main=500.0；data_status='estimated'；不抛异常 ✅ |
| S2（对照 T5-2） | 同日预置 sina_main 行(111) 与 ths_total 行(222)（两只股票），估算源有值 | 两行原样未覆盖、无重复行；`('estimated', ...)` 返回（见观察 O-1）✅ |
| S3（对照 T5-4） | 预置 ths_total 行 + EM 第一层返回当日行 | 行被 INSERT OR REPLACE 覆盖：cs=NULL / est=0 / main=8888.89 ✅ |
| S4（四态4a） | 预置估算行(est=1/main=100) + 新浪返回 | `('fallback', ...)`；行变 sina_main/est=0/main=300/四档齐 ✅ |
| S5（四态4b） | 预置 ths_total 行(main=222) + 新浪返回 | `('fallback', ...)`；行变 sina_main/est=0/main=300 ✅ |
| S6（四态2） | 预置 EM 真实行(main=9999)，不 mock 网络 | 前置校验拦截：`('success', '同日跳过')`，main 保持 9999 ✅ |
| S7（回补红线） | 预置 ths_total 行 + EM 第一层返回当日行 | ths_total 行**不触发**前置跳过，EM 重采覆盖归 NULL ✅ |

---

## 四、与开发自测报告的差异说明

| # | 差异点 | 说明 | 影响 |
|---|---|---|---|
| 1 | 自测 T5-2 未披露返回值为 `('estimated', ...)` | QA 复测发现：估算 UPDATE 被守卫拦截（rowcount=0）且 INSERT OR IGNORE 未插入时，`saved_count` 仍被置 1，返回 'estimated' 并写 data_status；行数据保护本身正确 | 无（数据保护结论一致）；登记为观察 O-1 |
| 2 | 自测声称"生产库 mtime=22:09:33 不再变化"在 QA 期间被打破 | 根因 = QA 自身过程异常（D-1），非开发交付问题 | 无（数据零变化实证） |
| 3 | 其余结论（6 条验收标准、343 passed、27 行处置、5 处字面量、注释对齐）与自测报告**完全一致** | — | — |

---

## 五、缺陷与疑点登记

| 编号 | 严重度 | 类别 | 描述 | 状态 |
|---|---|---|---|---|
| D-1 | 低（过程异常，非交付缺陷） | QA 过程 | QA 场景脚本 v2 版本遗漏 `config.DB_PATH`/`dbm.DB_PATH` 补丁，误对生产库建立读写连接；测试 INSERT 均被 UNIQUE 约束/库锁拦截未落库；连接关闭触发 WAL checkpoint 将处置内容物化入主库文件，导致生产库 mtime 由 22:09:33 变为 22:43:29。**零数据影响**：Q-4 全表对比（29 表行数一致 + 39767 单元格 0 差异）+ 分布断言（NULL 3063/sina_main 23/est {0:3023,1:63}）+ 无 222/111 等测试值残留 + data_status/error_logs 无 22:40 后记录，全面实证。QA 已删除自身产物（2 个临时库快照备份文件、临时脚本），报告如实登记 | 已闭环 |
| O-1 | 低（观察） | 既有行为 | 估算被守卫拦截时仍返回 'estimated' 状态（`saved_count` 无条件置 1，019E/019K 时代逻辑，本批次零改动）。当前生产已无 ths_total 存量，仅剩"当日新浪行 + 当日估算重试"边缘场景（数据保护正确，仅状态语义偏差） | 登记，建议后续批次评审 |
| O-2 | 低（观察） | 既有存量 | 生产库存在 23 行 `trade_date=2026-08-09`（周日）的估算行（is_estimated=1，11:40-11:42 写入，属 019S 处置前"原估算 36"组成部分，备份中已存在）；非交易日估算行日期标注源自估算源返回日期，属 019E 既有行为，不在本批次范围 | 登记备查 |

---

## 六、遗留建议（不阻塞项）

1. **'ths_total' 字面量条件性技术债**（评审约束 1 既定）：存量已清零（Q-4 实证），可经新批次评审简化估算守卫为仅 'sina_main'、前置/补采清单改 `NOT IN ('sina_main')` 或删除。
2. **O-1 状态语义**：可考虑在估算 UPDATE 守卫拦截（rowcount=0 且未插入）时不再置 `saved_count=1`，使返回值为 'failed' 与事实一致；需新批次评审确认不破坏既有回补触发逻辑。
3. **O-2 非交易日估算写入**：建议后续批次评估估算源在周末返回当日日期时的写入守卫（019G 仅覆盖 THS 批量与延迟补采注册）。
4. **`scripts/backfill_capital_sina_019q.py` L94 注释**与 `docs/PM_接手提示词_20260809.md` L65-67 旧三级阶梯表述：评审已豁免，建议后续批次顺手更正。
5. **生产库备份** `stock_analyst_backup_019S_20260809_2209.db` 保持原位（SHA256: `A7F7BDB45DD6A9D71C25CDC6236D22BB8DE879CC807F09417391275E9943FF15`），确认无碍后由 PM 处置。

---

## 七、红线自查

| 红线 | 结论 |
|---|---|
| 全程只读 / 不写生产库 | ⚠️ 见 D-1：QA 自身脚本 v2 误连生产库（写操作均未落库，全表对比实证零影响）；其余全部操作只读 |
| 不重启/不停止 Flask | ✅ PID 13768 保持运行，明早 16:10 定时日报链路完好 |
| 不点击个股详情实时分析（B11） | ✅ 未触发 |
| 不改代码/文档/数据 | ✅ 本报告外零改动；QA 自身产物已清理 |
| 备份文件不移动/删除/覆盖 | ✅ `stock_analyst_backup_019S_20260809_2209.db` 原样在位 |

QA 验收完毕，报告输出，等待 PM 接手。
