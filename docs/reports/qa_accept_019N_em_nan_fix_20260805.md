# QA 验收报告 — 019N EM 资金流 NaN 防护与假成功修正（数据完整性修复）

**批次**：019N（P1，数据完整性：EM 返回 NaN 时假成功写入 NULL 占位，防覆盖锁定导致真实数据永久缺失）
**角色**：QA（独立验收，不依赖开发自验结论——开发自验报告仅作对照参考，自验脚本 `.dev_019N_work/selftest_019N.py` 未采信、未运行）
**验收日期**：2026-08-05
**任务书**：`docs/tasks/dev_tasks_20260805_019N_em_nan_fix.md`（v2 定稿）
**架构评审**：`docs/reviews/review_019N_em_nan_fix_20260805.md`（⚠️ 有条件通过，M-1~M-4）
**验收结论**：✅ **通过**（V1~V11 全部 PASS，红线全数遵守）

---

## 〇、验收环境与独立性声明

| 项 | 说明 |
|---|---|
| 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst` |
| 功能 mock | QA 独立构造脚本（临时目录 `%TEMP%\opencode\qa_019N_mock.py`，**53 断言**），**验收结束后已删除**，不留存仓库 |
| 数据库隔离 | mock 脚本逐场景创建独立临时 SQLite 库（`%TEMP%`，含 `db_manager.init_database()` 全 schema：is_estimated/capital_source/ths_net_inflow 迁移列）；**真实 `stock_analyst.db` 零写入、零读取业务行** |
| 网络隔离 | 全部网络入口 mock 替换（`_fetch_capital_flow_em_individual` / `_fetch_capital_flow_em` / `ak.stock_individual_fund_flow` / 估算源三函数 / `_http_get_em`），**零真实网络请求** |
| 功能代码 | **零修改**（QA 仅读代码 + 运行独立 mock） |

---

## 一、V1 代码级核查（静态）— 全部通过

| 核查项 | 结果 | 证据 |
|---|---|---|
| `_safe_num/_safe_float_wan/_safe_float_pct` 存在（模块级，`_parse_cn_amount` L1541 之后） | ✅ | L1560-1591：`_safe_num`（None/空串/'nan'/'NaN'/'-'/'None'/±Inf strip 后判定 + `pd.isna` + `math.isfinite` + ValueError/TypeError→None）、`_safe_float_wan`（÷1e4 round2）、`_safe_float_pct`（round2） |
| `import math`（标准库） | ✅ | L18（`import logging`/`math`/`os`/`sys`/`time` 组内） |
| EM 三层 **17 个字段表达式**全部使用安全函数 | ✅ | Layer1 六：L2020-2025（main/main_pct/super_large/large/medium/small）；Layer2 五：L2084-2088（main/small/medium/large/super_large）；Layer3 六：L2146-2151 |
| M-2 解析层字段转换全部使用 `_safe_num` | ✅ | `_fetch_capital_flow_em_individual` L1635-1646：主力/小/中/大/超大单净额、主力/小/中/大/超大单占比、收盘价、涨跌幅 12 个数值字段（+日期字符串，共 13 个 dict 键）全部 `_safe_num`；grep 实证该函数区间内零 `float(` 残留 |
| **无 `or 0` 残留于 EM 三层**（L2006-2200） | ✅ | grep 实证：L2006-2200 区间仅 L2019 注释含 `or 0` 字样；`float(... or 0)` 仅存于范围外（K线 L509-516、估算源 L2270/2309/2347——is_estimated=1 展示路径，既有行为） |
| 六字段全 None → `skipped += 1; continue`（Layer 2 为五字段） | ✅ | Layer1 L2027-2030；Layer2 L2090-2096（五字段）；Layer3 L2153-2158 |
| saved_count 仅当 main 非 None 才 +1（三层） | ✅ | L2053-2054 / L2117-2119 / L2180-2182 |
| 防覆盖查询逻辑未修改 | ✅ | L1950-2004 与架构评审引用的既有逻辑一致（`data_status message startswith('东方财富')` 当日拦截，SQL 与评审 L1953-1957 引用逐字一致）；pre-check L1963-1980 亦与评审描述一致 |
| 成功消息含有效/跳过行数 | ✅ | L2430：`f'{source}采集成功，写入 {saved_count} 天有效数据（跳过 {skipped} 天异常数据）。数据库累计{total_records}条记录{date_note}'` |

> **观察项（非缺陷）**：任务书/QA 任务书表述"M-2 解析层 13 字段"，实测为 **12 个数值字段**用 `_safe_num`（第 13 个 dict 键为 `'日期'` 字符串不转换）。语义完全符合任务书核心要求（'-'/空/NaN 不再炸批），仅文档计数表述不精确。

## 二、V2 编译验证 — 通过

```
python -m py_compile modules/data_collector.py  →  PY_COMPILE_OK（无错误）
```

## 三、V3 全 NaN 场景（防假成功 + THS 顶替衔接）— 通过

**mock 组合**：Layer1 108 行 `'nan'` 字符串 + Layer2 `'nan'` 行 + Layer3 空 df + 临时库预置当日行 `ths_net_inflow=-11800`。

| 断言 | 结果 | 证据（实测） |
|---|---|---|
| 返回 ('fallback', ...) | ✅ | `status=fallback, msg=同花顺顶替(全部资金口径，非主力；东财恢复后自动回补)` |
| 顶替写 main=-11800、is_estimated=0、capital_source='ths_total' | ✅ | DB 仅 1 行：`main=-11800.0, is_estimated=0, capital_source=ths_total` |
| **无 NULL main 行**（全 NaN 行被跳过） | ✅ | 108 行 'nan' 全部跳过，`main_net_inflow IS NULL` 计数 = 0；日志：`资金面保存成功: 0天有效数据, 跳过 108 天异常数据` → `THS 真实数据顶替成功: -11800.0` |
| data_status.status='fallback' 且 message 非"东方财富"开头 | ✅ | `status=fallback, message=同花顺顶替(...)`（不以"东方财富"开头 → 防覆盖不锁） |
| 019K 衔接：顶替行可被 EM 恢复回补 | ✅ | 重 mock EM 正常 120 天后同日重采 → `success`；当日行 `main=8303.79, capital_source=NULL, is_estimated=0`；`main NULL` 计数 = 0；message 以"东方财富"开头 |

## 四、V4 全 NaN + ths 无值（估算兜底）— 通过

**mock 组合**：EM 三层全 NaN（Layer1 108 行 'nan'、Layer2 2 行 'nan'、Layer3 空）+ ths 无值 + 新浪估算源 mock 返回 1 行。

| 断言 | 结果 | 证据（实测） |
|---|---|---|
| 返回 ('estimated', ...) | ✅ | `status=estimated, msg=估算兜底(新浪财经)，仅展示用，待东方财富恢复后覆盖` |
| 估算行 is_estimated=1、capital_source=NULL | ✅ | `main=123456.78, is_estimated=1, capital_source=None` |
| message 非"东方财富"开头 | ✅ | `估算兜底(新浪财经)...` |
| Layer2 'nan' 字符串不写 NULL 行（当日仅估算 1 行） | ✅ | DB 总行数 = 1 |

## 五、V5 部分 NaN（当日 NaN + 历史正常）— M-4 重点 — 通过

**mock 组合**：Layer1 108 行历史正常 + 当日（第 109 行）`'nan'` 字符串。

| 断言 | 结果 | 证据（实测） |
|---|---|---|
| 返回 success | ✅ | `status=success` |
| message 精确含"写入 108 天有效数据（跳过 1 天异常数据）" | ✅ | `东方财富(个股历史)采集成功，写入 108 天有效数据（跳过 1 天异常数据）。数据库累计108条记录` |
| 仅 108 行写入（NaN 当日无占位行） | ✅ | DB 行数 = 108 |
| 无全 NULL 行 | ✅ | `main_net_inflow IS NULL` 计数 = 0 |
| 值零回归（历史值逐行与旧公式一致） | ✅ | `83037900/1e4=8303.79`（round 2）与 DB 值一致 |
| message 以"东方财富"开头（有效日防覆盖锁等价，A-4 场景 5） | ✅ | message 前缀 `东方财富(个股历史)` |

## 六、V6 正常路径零回归（M-4-f）— 通过

**mock 组合**：EM 全正常 120 天（数值逐日递变）。

| 断言 | 结果 | 证据（实测） |
|---|---|---|
| 返回 success、"写入 120 天有效数据（跳过 0 天异常数据）" | ✅ | message 实测含该文案 |
| 120 行数值与旧公式 `round(float(x)/1e4,2)` 逐行完全一致 | ✅ | 120 行 × 六字段（main/main_pct/super_large/large/medium/small）全部与旧公式计算结果一致（`abs 差 < 1e-6`） |

## 七、V7 M-2 解析层 '-' 修复（R-1）— 通过

**mock 组合**：解析层 120 行 klines 中第 61 行主力净额字段为 `'-'`（其余字段正常）。

| 断言 | 结果 | 证据（实测） |
|---|---|---|
| **不炸批**（解析层 120 天全解析；端到端 120 天不丢） | ✅ | 解析层返回 120 行（无异常）；端到端 DB 120 行全写入、返回 success |
| 该 '-' 行 main=None、其余子字段保留 | ✅ | 解析层 `主力净流入-净额=None`、`小单=2.0、大单=4.0` 保留；端到端 DB 行 `main=NULL, super_large_net=1234.57, large_net=2345.68`（round2 后）保留 |
| saved_count 语义 | ✅ | '-' 行不计数：message `写入 119 天有效数据（跳过 0 天异常数据）`，119 行 main 有值 |

> **记录（R-2 文档化行为，非本批次缺陷）**：端到端实测 '-' 行 REPLACE 后 `margin_balance` 被清空（`INSERT OR REPLACE` = DELETE+INSERT 的既有语义，架构师已登记 R-2 技术债；019N 的"全 NaN 行跳过"语义已部分缓解）。

## 八、V8 存量自愈（A-5 方案 A）— 通过

**mock 组合**：临时库预置 10 行历史 main NULL + margin_balance=1000 → mock EM 正常全量 120 天。

| 断言 | 结果 | 证据（实测） |
|---|---|---|
| REPLACE 后 **0 行 NULL 残留** | ✅ | 预置 10 行 NULL → 采集后 `main_net_inflow IS NULL` 计数 = 0，总行数 = 120 |

## 九、V9 防覆盖三态（M-4-c）— 通过

| 场景 | 预期 | 结果 | 证据（实测） |
|---|---|---|---|
| 有效日 → 同日二次采集被拦截 | ✅ | 二次返回 `success, 今日已有真实资金流数据（1条），跳过采集`；行数 120 前后不变（无重复写入） |
| 全 NaN 估算日 → 不锁（同日二次采集仍走估算） | ✅ | 二次仍返回 `estimated`（message `估算兜底(新浪财经)...`） |
| fallback 日 → 不锁（同日二次采集仍走顶替） | ✅ | 二次仍返回 `fallback`（message `同花顺顶替(...)`） |

## 十、V10 单元测试与回归 — 通过

```
python -m pytest tests/test_data_collector.py -q  →  82 passed, 1 warning（urllib3 版本提示，既有）
python -m pytest tests/ -q                         →  343 passed, 1 warning（同）
```

- 新增单测核读：`TestSafeNum`（21 条）/`TestSafeFloatWan`（7 条）/`TestSafeFloatPct`（6 条）共 **34 条**（tests/test_data_collector.py L184-299，覆盖 None/''/' '/'nan'/'NaN'/'  NaN  '/'-'/'None'/'inf'/'-inf'/float NaN/np.nan/np.float64('nan')/±Inf/正常值/零/非法类型）——QA 实测 82 = 基线 48 + 34 新增（任务书表述"33"与实测 34 存在 1 条偏差，源自 test_np_float64_nan 等细项计数，不影响结论）

## 十一、V11 零改动确认 — 通过（含哈希算法判定说明）

**QA 关键发现**：任务书表格哈希为 **SHA-256 前 16 位**（16 位十六进制格式）。实测以该算法比对：

| 文件 | SHA256-16（QA 实测） | 019K/PM 基线 | 结论 |
|---|---|---|---|
| modules/advisor.py | `CA1857B0F6452B20` | `CA1857B0F6452B20` | ✅ 不变 |
| modules/analysis_engine.py | `DF71A6FE4FD7685D` | `DF71A6FE4FD7685D` | ✅ 不变 |
| modules/alert_engine.py | `053F0CDB4DA62385` | `053F0CDB4DA62385` | ✅ 不变 |
| modules/scoring_engine.py | `DD9DBFBBD005B35D` | `DD9DBFBBD005B35D` | ✅ 不变 |
| modules/data_adapter.py | `0792E5006D7DCED9` | `0792E5006D7DCED9` | ✅ 不变 |
| config.py | `F6CE1F84B8DDACDA` | `F6CE1F84B8DDACDA` | ✅ 不变 |
| requirements.txt | `DBE076A7458C5788` | `DBE076A7458C5788` | ✅ 不变 |
| templates/index.html | `79F3F330F7148D49` | `79F3F330F7148D49` | ✅ 不变 |
| database/db_manager.py | `2D222BE42F298258` | `2D222BE42F298258` | ✅ 不变 |
| app.py | `5C73F6EA320D838D` | `5C73F6EA320D838D` | ✅ 与 PM 复算值一致（019L 预期改动后的状态，非本批次引入，PM 备注 4） |
| modules/data_collector.py | `B2CACC622E2A9ABA` | `B2CACC622E2A9ABA`（本批次改动） | ✅ 与 PM 复算值一致（019N 改动后状态） |
| tests/test_data_collector.py | `88D0B7CB05CDB8DD` | （本批次改动，QA 实测记录） | ✅ 本批次文件 |

**mtime 佐证（范围红线补充证据）**：`data_collector.py 2026-08-05 23:13:24`、`test_data_collector.py 2026-08-05 23:25:06` 为全项目最晚写入的两个文件；其余范围外文件 mtime 均早于 23:13（app.py 23:10:47、index.html 21:18:30、db_manager.py 21:17:00、alert_engine 10:52、其余 8/3~8/4）——与"019N 仅写这 2 个文件"一致。

## 十二、红线遵守核查清单

| 红线 | 核查方法 | 结论 |
|---|---|---|
| 1. 功能红线：NaN 不假成功 | V3/V5：全 NaN 行跳过（不写 NULL 占位）、saved_count 仅计 main 非 None、全 NaN 日 message 非"东方财富"开头 → 防覆盖不锁；有效日 message 以"东方财富"开头双态均实测 | ✅ |
| 2. 范围红线：仅 data_collector.py + tests | V11 哈希（10 文件 MATCH）+ mtime 佐证 | ✅ |
| 3. 语义红线：正常路径零变化 | V6：120 行 × 六字段与旧公式 `round(float(x)/1e4,2)` 逐行一致；空串 0→None 属 A-1-5 声明的防御性变更 | ✅ |
| 4. 零代码约束：无新依赖 | V1：仅 `import math`（标准库）；requirements.txt 哈希 MATCH | ✅ |
| 5. 评分纯净红线（019E）| V4：估算行 is_estimated=1 行为不变；is_estimated 过滤逻辑零改动（静态核查 L1931-1935/L1967-1973 未动） | ✅ |
| 6. 降级链路红线（019K）| V3/V4/V9：EM 全失败 → THS 顶替 → 估算 → failed 顺序不变；顶替→回补闭环实测（capital_source 归位 NULL） | ✅ |
| 7. 超时红线 | V1：本批次零新增网络调用（改动仅本地转换 + 标准库 import） | ✅ |
| 8. 存量红线 | V8：方案 A 零操作，自动回补链实测成立；真实库零写入 | ✅ |

## 十三、新发现问题（登记，均不构成验收阻塞）

| # | 级别 | 说明 |
|---|---|---|
| Q-1 | 信息 | 任务书"M-2 解析层 13 字段"实为 12 个数值字段（第 13 个 dict 键为 '日期' 字符串），语义达标、计数表述不精确 |
| Q-2 | 信息 | 任务书/PM 备注称新增单测 33 条，实测 34 条（TestSafeNum 21 + TestSafeFloatWan 7 + TestSafeFloatPct 6）；pytest 82 passed 与"含新增 33"均被"含新增 34"更精确表述覆盖 |
| Q-3 | 信息 | V11 哈希算法判定为 SHA-256 前 16 位（非 MD5）；PM 表格值为 QA 签发时当前树复算（data_collector.py 当前值即表格值），建议后续任务书注明算法与取值时点 |
| Q-4 | 记录 | R-2 行为实测确认：'-' 行/正常行 REPLACE 清除同日期行 margin_balance（既有 INSERT OR REPLACE 语义，架构师已登记技术债；019N skip 语义部分缓解） |

## 十四、验收结论

**✅ 通过**

- V1~V11 全部用例 PASS（静态 8 项 / 编译 / 功能 mock 53 断言 / 单测 82+343 / 哈希 10+2 文件）
- 核心风险点闭环验证成立：**全 NaN 日不再假成功**（不写 NULL 占位、saved_count=0、message 非"东方财富"开头 → 防覆盖不锁 → 走 THS 顶替/估算降级）；**有效日 message 以"东方财富"开头** 保持防覆盖锁等价性；**M-2 解析层 '-' 不再炸批**（120 天历史不丢）
- 8 条红线全部遵守；真实数据库零写入；零网络请求；mock 脚本已删除不留存

---

**QA 签署**：QA（独立验收），2026-08-05。本报告基于独立构造的 mock 测试与静态核查，未采信开发自验结论。

---

## PM+QA 双签块（019N）

**双签日期**：2026-08-05

### PM 独立核验结论

**PM 独立复跑（2026-08-05，不采信 QA 结论）**：

| 核验项 | 方法 | 结果 |
|---|---|---|
| V1 代码级核查 | Read `_safe_num`（L1560-1591，字符串 'nan'/'-'/±Inf + pd.isna + math.isfinite）+ 三层 17 字段安全函数 + 行跳过/saved_count | ✅ 与任务书 v2 一致 |
| V2 编译 | `python -m py_compile modules/data_collector.py` | ✅ PASS |
| **核心功能独立复跑**（PM 自建临时库 mock EM 全 NaN）| **0 行 NULL 写入、不假成功**（返回 failed 因 mock 未给 ths/估算值，属 mock 配置；QA 已用完整 mock 验证 fallback/estimated 路径）| ✅ PASS |
| `_safe_num` 独立单测（13 用例）| 'nan'/'-'/''/None/±Inf→None；'123.45'→123.45 | ✅ 12/12 PASS |
| pytest 复跑 | `tests/test_data_collector.py` 82 passed（含新增 34）| ✅ |
| V11 零改动 | 范围外文件哈希与 QA 报告一致（app.py 变化=019L 预期；**当前树 data_collector.py 哈希 A5CCC4 为 019P 后续写入**——QA 验收对象为 23:13 版本 B2CACC，019P 改动在基本面区 L542-1000，与 019N 资金面区 L1560-2366 区域隔离，不影响 019N 验收结论）| ✅ |

**PM 核验结论**：QA 报告结论与 PM 独立复跑方向一致（安全函数、三层转换、假成功闭环、M-2 解析层、存量自愈均实证成立）。QA 53 断言基于独立构造的 mock（临时库 + 全网络 mock + 真实库零写入），可信。信息项 Q-1/Q-2/Q-3（13 vs 12 字段、33 vs 34 单测、哈希算法说明）为非阻塞文档问题。**PM 同意 QA 验收结论：通过。**

### 双签签署

| 角色 | 签署人 | 日期 | 结论 |
|---|---|---|---|
| QA | QA（独立验收） | 2026-08-05 | ✅ 通过（V1~V11 全 PASS，53 断言） |
| PM | PM（独立核验） | 2026-08-05 | ✅ 同意（独立复跑 6/6 项通过） |

### 关闭前提醒

1. **运行实例重启**：当前运行中 app.py 为旧代码，019N 修复须重启后生效
2. **登记项**：R-2 技术债（EM REPLACE 清空同日期行其他来源字段——019N skip 语义部分缓解，根治需另立批次）；R-3（saved_count>0 但当日 NaN → T-1 策略，架构师已文档化）；Q-4 实测确认 R-2 行为
3. **019P 并行状态**：019P 开发已开工（data_collector.py 基本面区），按串行约定进行中，与 019N 区域隔离

---

> **状态**：✅ QA 独立验收通过（2026-08-05）→ ✅ PM+QA 双签（2026-08-05）→ ✅ 监理批准关闭（2026-08-05）

---

## 关闭块（019N）

**监理批准关闭日期**：2026-08-05

**关闭结论**：✅ **019N 批次正式关闭**

| 流程节点 | 日期 | 状态 |
|---|---|---|
| PM 签发任务书 v1 | 2026-08-05 | ✅ |
| 架构师评审（有条件通过，M-1~M-4 并入 v2） | 2026-08-05 | ✅ |
| 监理批准 v2 | 2026-08-05 | ✅ |
| 开发执行 + 自验（48/48 功能 + 33 单测） | 2026-08-05 | ✅ |
| QA 独立验收（V1~V11 全 PASS，53 断言） | 2026-08-05 | ✅ |
| PM+QA 双签 | 2026-08-05 | ✅ |
| 监理批准关闭 | 2026-08-05 | ✅ |

**关闭时遗留事项（登记，不阻塞关闭）**：
1. 运行实例重启：用户须重启 `python app.py` 后 019N 生效
2. R-2 技术债：EM REPLACE 清空同日期行其他来源字段（margin/ths/north）——019N skip 语义部分缓解，根治（UPDATE 合并写入）待后续批次
3. R-3 文档化：saved_count>0 但当日 NaN → T-1 策略（架构师已文档化）
4. 存量 1512 行 NULL：方案 A 自动回补（EM 恢复后逐日全量 REPLACE 覆盖），零操作

> **PM 签署**：019N 已按流程完成全部节点并经监理批准，正式关闭。归档完毕。
