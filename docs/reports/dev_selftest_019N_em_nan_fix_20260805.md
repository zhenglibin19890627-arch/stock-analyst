# 开发自验报告 019N — EM 资金流 NaN 防护与假成功修正（数据完整性修复）

**批次**：019N（P1，数据完整性：EM 返回 NaN 时假成功写入 NULL 占位，防覆盖锁定导致真实数据永久缺失，美的等 9 只实证）
**角色**：开发工程师（单人，内嵌任务书窗口独立执行）
**自验日期**：2026-08-05
**任务书**：`docs/tasks/dev_tasks_20260805_019N_em_nan_fix.md`（v2 定稿，M-1~M-4 已并入）
**架构评审**：`docs/reviews/review_019N_em_nan_fix_20260805.md`（⚠️ 有条件通过，M-1~M-4 修订后定稿 v2）
**状态**：开发执行完成 + 自验通过（48/48 功能 + 33 单测），待 QA 独立验收 → PM+QA 双签 → 监理批准关闭

---

## 一、改动清单（严格 2 文件）

| # | 文件 | 改动内容 | 位置（v2 定稿后漂移） |
|---|---|---|---|
| 1 | `modules/data_collector.py` | ① `import math`（标准库，零新 pip 依赖）② 新增 `_safe_num/_safe_float_wan/_safe_float_pct` 模块级辅助函数（A-1 定稿版，含字符串 'nan'/'-'/'None'/±Inf strip 判定 + `pd.isna` + `math.isfinite`）③ **M-2** 解析层 `_fetch_capital_flow_em_individual` 13 个字段转换改用 `_safe_num`（修复 '-' 炸整批 R-1）④ Layer 1 六字段安全转换 + 六字段全 None 行跳过 + saved_count 仅计 main 非 None + skipped 计数 + 日志含有效/跳过数 ⑤ Layer 2 五字段同语义 ⑥ Layer 3 六字段同语义 ⑦ 成功消息含"有效/跳过"行数 | ① L21 ② L1560-1591 ③ L1634-1646 ④ L2019-2061 ⑤ L2083-2119 ⑥ L2145-2163、L2173-2176 ⑦ L2430 |
| 2 | `tests/test_data_collector.py` | 新增 `TestSafeNum/TestSafeFloatWan/TestSafeFloatPct` 单测（M-4-g，参照 TestParseCnAmount 风格），覆盖 None/''/' '/'nan'/'NaN'/'-'/'None'/inf 字符串/float NaN/np.nan/np.float64 NaN/±Inf/正常值/零/非法类型；docstring 覆盖清单同步 | L179-283 区间 |

**其余文件零改动**（advisor/analysis_engine/alert_engine/scoring_engine/data_adapter/app.py/config.py/templates/index.html/database/db_manager.py/requirements.txt，mtime 实证均早于本会话改动时间）。

---

## 二、验证环境与手段

- 解释器：`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`
- 自验脚本：`.dev_019N_work/selftest_019N.py`（隔离临时 SQLite DB（含迁移后 schema：is_estimated/capital_source/ths_net_inflow 列）+ mock 网络层，不联网不入生产库；全文可复现，48 断言）
- 真实库：未做任何写入（只读前提成立，本批次无存量操作，A-5 方案 A）

---

## 三、自验结果（功能 mock 48/48 PASS）

| # | 场景 | 断言要点 | 结果 |
|---|---|---|---|
| S1 | **全 NaN（108 行 'nan' 字符串）+ THS 今日有值** | 返回 `('fallback', ...)`；THS 顶替写 main=-11800、is_estimated=0、capital_source='ths_total'；**无 NULL main 行**；data_status.status='fallback' 且 message 非"东方财富"开头（防覆盖不锁） | ✅ 8/8 |
| S2 | **全 NaN + ths 无值（float NaN + Layer2 'nan' 字符串混合）** | 返回 `('estimated', ...)`；估算行 is_estimated=1；message 非"东方财富"开头；Layer2 'nan' 字符串不写 NULL 行（当日仅估算 1 行） | ✅ 6/6 |
| S2b | **全链路失败** | 返回 `('failed', ...)`；error_logs 写入；status=failed | ✅ 3/3 |
| S3 | **部分 NaN（当日 NaN + 历史 108 行正常）** | 返回 success；message 精确含"写入 108 天有效数据（跳过 1 天异常数据）"；仅 108 行写入（NaN 当日无占位行）；无全 NULL 行；**值零回归**（83037900/1e4=8303.79）；message 以"东方财富"开头（A-4 有效日锁等价） | ✅ 7/7 |
| S4 | **正常 120 天零回归** | success；"写入 120 天有效数据（跳过 0 天异常数据）"；120 行数值与旧公式 `round(float(x)/1e4,2)` **逐行完全一致** | ✅ 3/3 |
| S5 | **Layer 2 'nan' 字符串 + 有效行混合** | 仅 2 行有效写入（'nan' 行跳过）；main 零回归（12345.0/-10000.0）；无全 NULL 行 | ✅ 4/4 |
| S6 | **M-2 解析层 '-' 占位** | 解析层直接验证：'-' 不炸批（2 行全解析成功）、'-' 字段→None、正常值保留；端到端：120 天中 1 行含 '-' → 整批不丢，该行 main=None 其余子字段保留（REPLACE 未清空） | ✅ 6/6 |
| S7 | **存量自愈（A-5 方案 A 验证）** | 预置 10 行历史 main NULL + margin 有值 → mock EM 正常全量 → REPLACE 后 **0 行 NULL 残留** | ✅ 2/2 |
| S8 | **防覆盖三态（M-4-c）** | T1 有效日锁（message"东方财富"开头 → 拦截且不重复写入）；T2 全 NaN 估算日不锁（同日二次采集仍走估算）；T3 fallback 日不锁（同日二次采集仍走顶替） | ✅ 5/5 |
| S9 | **019K 衔接（顶替→回补闭环，A-6）** | 全 NaN+ths → fallback；EM 恢复同日重采 → success、顶替行被真实值覆盖（8303.79）、capital_source 归位 NULL、is_estimated=0、message 以"东方财富"开头 | ✅ 7/7 |

**注意**：S3/S4 中 Layer 1 成功路径仍查询 raw_kline（日期对齐 T-1 检查），隔离库已补建 raw_kline 空表；这是既有行为，非本批次引入。

---

## 四、单元测试（M-4-g，33 条新增）

| 类 | 覆盖 |
|---|---|
| `TestSafeNum`（21 条） | None / '' / '  ' / 'nan' / 'NaN' / '  NaN  ' / '-' / 'None' / 'inf' / '-inf' / `math.nan` / `np.nan` / `np.float64('nan')` / ±inf / '123.45' / '-7200.36' / 12.5 / 0 / '0' / np.float64(12.5) / 'abc' / `[1,2]` |
| `TestSafeFloatWan`（7 条） | None / 'nan' / '-' / 123450000.0→12345.0 / '10000'→1.0 / '-72003600.0'→-7200.36 / 0→0.0 |
| `TestSafeFloatPct`（6 条） | None / 'nan' / '-' / 3.14159→3.14 / '-12.3456'→-12.35 / 0→0.0 |

**总计 pytest：343 passed（基线 310 + 新增 33），1 warning 为既有 urllib3 版本提示。**

---

## 五、静态与回归验证

| 项 | 结果 |
|---|---|
| `python -m py_compile modules/data_collector.py` | ✅ 无错误 |
| `python -m pytest tests/` | ✅ **343 passed**（1 warning 为 urllib3 版本提示，既有） |
| 代码级核查（验收标准 1） | ✅ `_safe_num/_safe_float_wan/_safe_float_pct` 存在且被 EM 三层 **17 个字段表达式全部使用**（Layer1 六 / Layer2 五 / Layer3 六，grep 实证 L2020-2025/L2084-2088/L2146-2151）；解析层 13 字段用 `_safe_num`（L1635-1646） |
| 无 `or 0` 残留于 EM 三层 | ✅ grep 实证：EM 三层区间（L2006-2200）零残留；残余 `or 0` 仅存于范围外区域（K线 L509-516、估算源 L2270/2309/2347，is_estimated=1 展示路径） |
| 行跳过语义 | ✅ 六字段全 None（Layer 2 为五字段）→ `skipped += 1; continue`，不写 NULL 占位、不 REPLACE |
| saved_count 语义 | ✅ 三层均"仅当 main 非 None 才 +1"（L2053-2054/L2117-2119/L2166-2167） |
| `ruff check` 两改动文件 | ✅ tests 全过；data_collector.py 仅 1 项既有告警（L1444 `turnover_yuan` 未使用，018 遗留，019K 已登记，非本批次引入） |
| 范围外文件 | ✅ 零改动（mtime 实证：advisor/analysis_engine/alert_engine/scoring_engine/data_adapter/app.py/config.py/index.html/db_manager.py/requirements.txt 均早于本会话 23:13 改动时间；本会话仅写 data_collector.py 23:13 与 test_data_collector.py 23:14） |
| 评分纯净红线（019E） | ✅ is_estimated 过滤逻辑零改动；估算行（is_estimated=1）行为不变（S2 实证仅展示标记） |
| 超时红线（019I/019J） | ✅ 零新增网络调用，改动仅本地转换（`import math` 标准库） |
| 降级链路红线（019K） | ✅ S1/S9 实证：EM 全失败 → THS 顶替 → 估算 → failed 顺序不变 |

---

## 六、红线落实核对

| 红线 | 落实 |
|---|---|
| 功能红线：NaN 不假成功 | ✅ S1/S3：全 NaN 行跳过（不写 NULL 占位）；saved_count 仅计 main 非 None；全 NaN 日 message 非"东方财富"开头 → 防覆盖不锁 |
| 范围红线：仅 data_collector.py（+M-4 要求的 tests） | ✅ 严格 2 文件 |
| 语义红线：正常路径零变化 | ✅ S4 逐行数值与旧公式一致；空串 0→None 属 A-1-5 声明的防御性变更 |
| 零代码约束 | ✅ 仅 `import math`（标准库）；config.py/DB schema 未碰 |
| 评分纯净红线（019E 延续） | ✅ 估算过滤零改动（S2 实证） |
| 降级链路红线（019K 延续） | ✅ S1/S9 实证链路顺序不变、回补闭环无死锁 |
| 超时红线（019I/019J 延续） | ✅ 零新增网络调用 |
| 存量红线 | ✅ 方案 A 零操作（S7 实证自动回补链成立） |

---

## 七、开发备注

1. **实现与任务书/评审差异（0 处必要修正）**：任务书 A-1 代码样例原样落地；M-2 解析层 13 字段全部替换（任务书表述"字段转换改用 _safe_num"）；Layer 2/3 skip 判定与计数语义与 Layer 1 完全一致（M-3）。
2. **`skipped` 变量作用域说明**：每层进入处理块时重置 `skipped = 0` 并累计本层跳过数；成功消息中的 `skipped` 恒为最后执行的层（与 `source` 同源），全 NaN 日三层均 0 有效行时消息不产生（走 fallback/estimated/failed 分支）。
3. **R-3 边界**（架构师已文档化，本批次不采纳增强）：saved_count>0 但当日行 NaN（历史有效）→ 当日无行走 T-1 策略——S3 场景即此形态，行为符合 v2 定稿。
4. **R-2 技术债**（架构师登记）：EM REPLACE 清空同日期行其他来源字段——S7 自愈验证中 margin_balance 被清空属既有行为（019K 报告 R-7 同源），根治超出本批次。
5. **019P 并行隔离**：本批次仅动资金面区域（L1560-2436）；019P 基本面区域（L542-1000）未触碰。
6. **自验脚本复现**：`.dev_019N_work/selftest_019N.py`（工作区根，与 019K 先例同位置），运行输出 48 PASS / 0 FAIL，隔离临时 DB 不碰生产数据。

---

**开发自验签名**：开发工程师，2026-08-05。以上自验在隔离环境完成，未执行正式验收（由 QA 独立执行）。
