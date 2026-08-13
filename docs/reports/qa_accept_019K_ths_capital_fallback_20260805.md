# QA 独立验收报告 — 019K：东财失败时 THS 资金数据顶替（方案一）

| 项 | 内容 |
|---|---|
| 批次编号 | 019K |
| 验收对象 | THS 顶替写入（`fetch_capital_flow` em_all_failed 块）+ 防覆盖闭环（4 处）+ `capital_source` 迁移列 + 前端标注与状态映射 |
| 任务书 | `docs/tasks/dev_tasks_20260805_019K_ths_capital_fallback.md`（v2 定稿） |
| 验收日期 | 2026-08-05 |
| QA 执行人 | QA（独立验收，独立构造 mock，不采信开发自验结论） |
| 验收结论 | **✅ 通过（44/44 断言 PASS，1 项观察项不阻塞）** |

> 独立性声明：本验收的 mock 测试脚本由 QA 独立编写（独立于 `.dev_019K_work/selftest_019K.py`），
> 全部网络依赖 mock、数据库使用真实库只读快照副本（%TEMP%），未向真实库写入任何业务行。
> 验收后脚本已删除，不留存仓库。

---

## 〇、验收环境与方法

| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst` |
| Python | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`（3.12） |
| 真实库 | `stock_analyst.db`（仅 sqlite3 在线 .backup() 只读快照 + 2 次只读 SELECT 核对，零写入） |
| mock 库 | `%TEMP%\qa019k_*\qa_temp.db`（真实库副本，测试数据仅写入副本） |
| 方法 | 静态代码核查（Read/grep）+ py_compile + mock 实测（monkeypatch 模块级 `get_connection` 指向副本库，mock EM 三层/估算源/`fetch_capital_flow`/`time.sleep`/`save_data_status` 捕获）+ 哈希比对 |
| 自验报告 | `reports/dev_selftest_019K_ths_capital_fallback_20260805.md` 仅作对照参考，未采信其结论 |

---

## 一、逐用例验收结果

### V1：代码级核查 —— ✅ 全过

| 核查项 | 结果 | 证据（代码位置） |
|---|---|---|
| `capital_source` 列在迁移列表 | ✅ PASS | `database/db_manager.py` L964-965：`('raw_capital_flow', 'capital_source', 'TEXT DEFAULT NULL')`，走 `_safe_add_columns` try-except 幂等迁移 |
| 顶替逻辑位于 em_all_failed 块内、估算源之前 | ✅ PASS | `modules/data_collector.py` L2140-2188：`if em_all_failed:`（L2135）内、腾讯估算源（L2190）之前 |
| 顶替写入仅 3 字段（main_net_inflow/is_estimated=0/capital_source='ths_total'） | ✅ PASS | L2162-2173：UPDATE 仅 SET 3 字段；INSERT OR IGNORE 仅 4 列（stock_id/trade_date/main_net_inflow/is_estimated/capital_source），无 pct/分单字段 |
| UPDATE + INSERT OR IGNORE，无 INSERT OR REPLACE | ✅ PASS | L2161-2173；grep 证实顶替块内无 INSERT OR REPLACE（仅 EM 三层 L1994/2047/2098 有 REPLACE，且显式携带 capital_source=NULL） |
| 4 处防覆盖全部落地 | ✅ PASS | ① 前置跳过 L1931-1936 `AND (capital_source IS NULL OR capital_source != ?)`；② 补采清单 L1501-1505 同型；③ EM 三层 L1996-1997/L2049-2050/L2100-2101 显式 `capital_source` + VALUES 0, NULL；④ 估算三处守卫 L2209/L2247/L2285 `AND (capital_source IS NULL OR capital_source != 'ths_total')` |
| `_em_batch_collect` L1326-1327 判定语义 | ✅ PASS | `if result and result[0] == 'success'` —— 'fallback' ≠ 'success'，计失败、不重置熔断计数（语义天然正确，实测见 V8） |
| docstring 修订（M-10） | ✅ PASS | L1368-1373（"主力净流入主来源为东方财富…顶替…评分真实数据第二源"）、L1918-1924（019K 注释） |
| index.html 标注与 fallback 映射 3 处 | ✅ PASS | L2069-2076 状态三元链 `'fallback' → '⚠️ 顶替'`（复用 status-partial）；L2483-2489 动态表头 `hasThsFallback → '同花顺顶替（全部资金口径）'`；L2497-2500 行内 `<sup>同花顺</sup>` 标注；L2558-2560 采集状态表三元链 `'fallback' → '⚠️顶替'` |
| 超时红线（零网络调用） | ✅ PASS | 顶替源 = 库读 `SELECT ths_net_inflow … LIMIT 1`（L2150-2153），无新增网络调用；THS 批量数据由 `fetch_capital_flow_batch` 循环前入库（L1398、L1451-1464） |

### V2：编译验证 —— ✅ PASS

```
python -m py_compile modules/data_collector.py database/db_manager.py
→ 无错误（PY_COMPILE_PASS）
```

### V3：THS 顶替路径（核心功能）—— ✅ 全过（9/9）

**mock 组合**：临时库（真实库副本）预置当日占位行 `ths_net_inflow=-11800`；mock EM 三层全失败（`_fetch_capital_flow_em_individual`/`_fetch_capital_flow_em`/`ak.stock_individual_fund_flow` 全 None）。

| 断言 | 结果 | 实测证据 |
|---|---|---|
| 返回 ('fallback', '同花顺顶替…') | ✅ PASS | `('fallback', '同花顺顶替(全部资金口径，非主力；东财恢复后自动回补)')` |
| DB 写入 main_net_inflow=-11800 | ✅ PASS | `main_net_inflow=-11800.0` |
| is_estimated=0 | ✅ PASS | `is_estimated=0` |
| capital_source='ths_total' | ✅ PASS | `capital_source='ths_total'` |
| data_status.status='fallback'、message 含"同花顺顶替/全部资金口径" | ✅ PASS | 捕获：`{status: 'fallback', message: '同花顺顶替(全部资金口径，非主力；东财恢复后自动回补)'}` |
| 仅当日 1 行 | ✅ PASS | `rows=1, dates=['2026-08-05']` |
| 不写 pct/分单字段 | ✅ PASS | `main_net_inflow_pct=None, super_large_net=None, large_net=None, medium_net=None, small_net=None` |
| ths_net_inflow 保留原值 | ✅ PASS | `ths_net_inflow=-11800.0` |
| INSERT OR IGNORE 分支（无行场景，SQL 级） | ✅ PASS | 副本库无行时 INSERT OR IGNORE 写入 4 列（stock_id/trade_date/main_net_inflow/is_estimated/capital_source），pct/分单/ths 均 NULL |

> 说明：THS 块代码中 INSERT OR IGNORE 分支在单进程真实流程中为防御性分支（ths_val 非空必有行可 UPDATE，
> 无行则 ths_val=None 直接跳过）——无害，符合"严禁 INSERT OR REPLACE"要求，不构成缺陷。

### V4：EM 成功正常路径零干扰 —— ✅ 全过（6/6）

**mock 组合**：`_fetch_capital_flow_em_individual` 返回 2 天数据（今日主力净额 123456700 元 → 12345.67 万元）。

| 断言 | 结果 | 实测证据 |
|---|---|---|
| 返回 success | ✅ PASS | `('success', '东方财富(个股历史)采集成功。已写入2天历史数据…')` |
| EM 值写入 main_net_inflow | ✅ PASS | `main_net_inflow=12345.67` |
| capital_source 保持 NULL | ✅ PASS | `capital_source=None` |
| is_estimated=0 | ✅ PASS | `is_estimated=0` |
| THS 顶替未触发 | ✅ PASS | 无 fallback 返回；data_status 捕获仅 `status:'success'` |
| 写入 2 天历史 | ✅ PASS | `count=2` |

### V5：EM 恢复回补（防覆盖闭环 —— QA 重点）—— ✅ 全过（8/8）

**mock 组合**：副本库预置 THS 顶替行（main=-11800, is_estimated=0, capital_source='ths_total', ths=-11800）→ mock EM 恢复成功 → 再次调用 fetch_capital_flow。

| 断言 | 结果 | 实测证据 |
|---|---|---|
| 前置跳过不阻塞 ths_total 行 | ✅ PASS | 重采返回 `('success', …)`, 前置跳过 SQL（L1931-1936 原文）双向断言：ths_total 行 `cnt=0`（不跳过）/ EM 行 `cnt=1`（可跳过） |
| EM 重采覆盖 main_net_inflow=12345.67 | ✅ PASS | `main_net_inflow=12345.67` |
| capital_source 归位 NULL | ✅ PASS | `capital_source=None` |
| is_estimated=0 | ✅ PASS | `is_estimated=0` |
| 补采清单 SQL 不命中 ths_total 行（应进补采） | ✅ PASS | 顶替行阶段 SQL（L1501-1505 原文）`hit=False` |
| 补采清单 SQL 命中 EM 真实行（排除出补采） | ✅ PASS | EM 覆盖后 SQL `hit=True` |
| R-7 登记实测记录 | ✅ PASS | EM 恢复覆盖时 `ths_net_inflow=None`（被 INSERT OR REPLACE 清空，既有 018 行为，评审接受登记，非缺陷） |

### V6：THS 为 NULL 落回估算兜底 —— ✅ 全过（4/4）

**mock 组合**：EM 全失败 + 副本库当日行 ths_net_inflow=NULL + 新浪估算源 mock 返回（-888）。

| 断言 | 结果 | 实测证据 |
|---|---|---|
| 返回 ('estimated', …) | ✅ PASS | `('estimated', '估算兜底(新浪财经)，仅展示用，待东方财富恢复后覆盖')` |
| 写入 is_estimated=1、capital_source=NULL | ✅ PASS | `is_estimated=1, capital_source=None` |
| main_net_inflow=估算值 | ✅ PASS | `main_net_inflow=-888.0` |
| 状态为 estimated | ✅ PASS | 捕获 `status:'estimated'` |

### V7：估算不覆盖 THS 顶替（来源守卫）—— ✅ 全过（任务书断言 2/2 + QA 扩展 3/3）

**mock 组合**：副本库预置 ths_total 行与 NULL 来源行 → 守卫 UPDATE 执行（SQL 原文）。

| 断言 | 结果 | 实测证据 |
|---|---|---|
| ths_total 行 UPDATE rowcount=0（守卫禁止覆盖） | ✅ PASS | `rowcount=0` |
| 对照 NULL 来源行 UPDATE rowcount=1（可覆盖） | ✅ PASS | `rowcount=1`；ths_total 行主值保持 -11800/is_estimated=0 |
| 流程级：顶替行 + ths=NULL + EM 全失败 → 估算路径被守卫阻止 | ✅ PASS | 行保持 `main=-11800, capital_source='ths_total', is_estimated=0`（DB 层保护生效） |
| 流程级：顶替行 + ths 有效 + EM 全失败 → THS 短路返回 fallback，估算不可达 | ✅ PASS | `('fallback', …)`，行保持 ths_total 标记 |
| 观察项（非断言） | 记录 | 守卫拦截场景返回值仍为 'estimated'，详见第三节 |

### V8：状态消费方（_em_batch_collect）—— ✅ 全过（4/4）

**mock 组合**：mock `fetch_capital_flow` 返回 ('fallback', …) / ('success', …)，mock time.sleep。

| 断言 | 结果 | 实测证据 |
|---|---|---|
| 'fallback' 计 fail_count=1、success_count=0 | ✅ PASS | `{'success_count': 0, 'fail_count': 1, 'source': 'EM逐只(QA-EM回退，成功0/失败1)'}` |
| 'fallback' 不重置熔断计数（0→1） | ✅ PASS | `_EM_CONSECUTIVE_FAIL_COUNT=1` |
| 对照 'success' → 计成功并重置计数（2→0） | ✅ PASS | `{'success_count': 1, 'fail_count': 0}`；`_EM_CONSECUTIVE_FAIL_COUNT=0` |
| 熔断阈值行为正确（预置 5≥5 触发熔断，属预期机制） | ✅ PASS | 计数 5 时熔断终止，剩余股票计 fail，未误计成功 |

### V9：评分进入验证 —— ✅ 全过（5/5）

**mock 组合**：副本库预置 THS 顶替行（main=-11800）+ 对照估算行（is_estimated=1）→ `data_adapter._read_capital_data` + `scoring_engine.score_main_capital`。

| 断言 | 结果 | 实测证据 |
|---|---|---|
| `_read_capital_data` 读到顶替行（is_estimated=0 过滤放行） | ✅ PASS | rows 含 2026-08-05 / main=-11800 / is_estimated=0 |
| `_read_capital_data` 过滤 is_estimated=1 估算行（纯净红线） | ✅ PASS | 返回行 is_estimated 均为 0（估算对照行被过滤） |
| `score_main_capital` 以 -11800 计分（大幅净流出 20 分，非中性 85） | ✅ PASS | `score=20.0, {'main_net_inflow': '-11800.00万元', 'note': '大幅净流出'}` |
| 对照无数据行 → 中性 85 | ✅ PASS | `score=85.0`（缺失填充 NEUTRAL_INFLOW=0.0） |
| 顶替真实数据参与评分生效（20 ≠ 85） | ✅ PASS | `20.0 vs 85.0` |

### V10：零改动确认 —— ✅ 全过（12/12 哈希与 019J 基线一致）

SHA256 前 16 位实测：

| 文件 | 实测哈希 | 019J 基线 | 结果 |
|---|---|---|---|
| modules/data_collector.py | `4C847FAD888F20BA` | `4C847FAD888F20BA`（本批次改动） | ✅ |
| database/db_manager.py | `2D222BE42F298258` | `2D222BE42F298258`（本批次改动） | ✅ |
| templates/index.html | `79F3F330F7148D49` | `79F3F330F7148D49`（本批次改动） | ✅ |
| modules/advisor.py | `CA1857B0F6452B20` | `CA1857B0F6452B20` | ✅ |
| modules/analysis_engine.py | `DF71A6FE4FD7685D` | `DF71A6FE4FD7685D` | ✅ |
| modules/alert_engine.py | `053F0CDB4DA62385` | `053F0CDB4DA62385` | ✅ |
| modules/scoring_engine.py | `DD9DBFBBD005B35D` | `DD9DBFBBD005B35D` | ✅ |
| modules/data_adapter.py | `0792E5006D7DCED9` | `0792E5006D7DCED9` | ✅ |
| modules/daily_report.py | `94C20A5CB7C78A7C` | `94C20A5CB7C78A7C` | ✅ |
| app.py | `8F8373C029E76390` | `8F8373C029E76390` | ✅ |
| config.py | `F6CE1F84B8DDACDA` | `F6CE1F84B8DDACDA` | ✅ |
| requirements.txt | `DBE076A7458C5788` | `DBE076A7458C5788` | ✅ |

### 回归验证（任务书验收标准 8）

```
python -m pytest tests/ -q → 310 passed, 1 warning（urllib3 依赖版本警告，非本批次引入）
```

真实库只读核查（验收后）：`PRAGMA table_info(raw_capital_flow)` 含 `capital_source`（启动迁移已执行，属预期 schema 变更）；
`raw_capital_flow` 中 `capital_source='ths_total'` 行数 = **0**；`daily_reports` 行数 = **387**（基线不变）——QA 未向真实库写入任何业务行。

---

## 二、红线核查清单

| # | 红线 | 核查方法 | 结果 |
|---|---|---|---|
| 1 | 功能红线：EM 全失败资金面因子不缺失 | V3 + V9（顶替真实数据参与评分，-11800 → 20 分非中性 85） | ✅ |
| 2 | 口径红线：全链路标注 + 监理知情 | V3 message 断言（含"全部资金口径，非主力"）+ V1 index.html 表头/行内/状态映射 + data_status='fallback' | ✅ |
| 3 | 来源标注红线 | V1 index.html L2483-2500 表头动态文案 + 行内 `<sup>同花顺</sup>` + V3 message | ✅ |
| 4 | EM 回补红线：pre-check + 补采清单排除 ths_total | V5 双向断言（ths_total 行 cnt=0/不命中，EM 行 cnt=1/命中） | ✅ |
| 5 | 状态消费红线：'fallback' 适配前端 + 不重置熔断 | V1（L2069-2076/L2558-2560 三元链）+ V8（fail_count=1、计数不重置） | ✅ |
| 6 | 范围红线：仅 3 文件 | V10 哈希 12/12 一致 | ✅ |
| 7 | 零代码约束：无新依赖、config.py 不碰、schema 仅新增列 | V10 + V1 迁移列表（仅 capital_source 1 列，走 _safe_add_columns） | ✅ |
| 8 | 防覆盖红线：估算↛THS、THS↛EM、EM→THS 可回补 | V7（守卫 rowcount=0/1 双向）+ V4（EM 成功时 THS 不顶替）+ V5（EM 回补成功、来源归位） | ✅ |
| 9 | 评分纯净红线：is_estimated=1 永不进评分 | V6（估算写 is_estimated=1）+ V9（估算行被 _read_capital_data 过滤；无数据对照中性 85） | ✅ |
| 10 | 超时红线：顶替链路零网络调用 | V1 代码核查（L2150-2153 库内读取，无新增网络调用，未引入裸调用） | ✅ |

---

## 三、新发现问题（观察项，不阻塞验收）

### 观察项 O-1（低风险）：估算源在守卫拦截场景下仍返回 'estimated'

- **现象**：当 `fetch_capital_flow` 走到估算路径且目标行恰为 `capital_source='ths_total'`（防御性场景，如外部改库或 ths 值为 NULL 的顶替行）时，守卫 UPDATE rowcount=0 + INSERT OR IGNORE 空操作后，三个估算源（腾讯 L2221 / 新浪 L2257 / 网易 L2295）仍**无条件** `saved_count = 1`，返回 `('estimated', '估算兜底(…)')` 并写 `data_status='estimated'`。
- **影响评估**：DB 层保护完全生效（顶替行主值与标记均未被改写，实测确认）；该场景在真实流程不可达（本代码写入的 ths_total 行必有非 NULL ths 值 → THS 块先短路返回 'fallback'，估算不可达）。用户可见影响仅：极端防御场景下状态记录显示"估算"而实际未写入——语义偏差，无数据安全影响。
- **对照任务书**：任务书 V7 验收断言（rowcount 双向）全部命中；本观察项不违反任何验收标准。
- **建议（可另立小批次）**：估算三处改为 `if cursor.rowcount > 0: saved_count = 1`（1 行修改 × 3 处），使返回值与真实写入一致。

### 观察项 O-2（信息性）：INSERT OR IGNORE 分支为防御性代码

THS 顶替块 L2167-2173 的 INSERT OR IGNORE 分支在单进程真实流程中不可达（ths_val 非 NULL ⇒ 必有一行可 UPDATE），无害且符合"严禁 INSERT OR REPLACE"要求，无需处理。

### 记录项 R-7（评审已登记，非缺陷）

EM 恢复覆盖时 `ths_net_inflow` 被既有 INSERT OR REPLACE 语义清空（实测 V5：覆盖后 ths_net_inflow=NULL），与评审登记一致，不构成缺陷。

---

## 四、验收结论

**✅ 通过（PASS）**

- 静态核查 9/9、编译 PASS、mock 实测 44/44 断言 PASS、pytest 回归 310 passed、哈希 12/12 一致、真实库零污染（ths_total 行=0、daily_reports=387）。
- 核心风险点 V5（防覆盖闭环）实测通过：EM 恢复可回补 THS 顶替行且来源归位，补采清单/前置跳过双向断言命中。
- 1 项观察项（O-1）不阻塞验收，建议后续小批次优化。

**QA 签署**：QA（独立验收）　日期：2026-08-05

---

*附：验收证据文件（已随临时目录清理）：mock 脚本与断言证据 JSON 存于 %TEMP%\qa019k_*（验收后已删除，不留存仓库）。*

---

## PM+QA 双签块（019K）

**双签日期**：2026-08-05

### PM 独立核验结论

**PM 独立复跑（2026-08-05，不采信 QA 结论）**：

| 核验项 | 方法 | 结果 |
|---|---|---|
| V1 代码级核查 | Read 顶替逻辑（L2140-2188，em_all_failed 估算前、3 字段写入、UPDATE+INSERT OR IGNORE）+ 4 处防覆盖 grep | ✅ 与任务书 v2 一致；INSERT OR REPLACE 仅 EM 三层 3 处（预期） |
| V2 编译 | `python -m py_compile` data_collector.py + db_manager.py | ✅ PASS |
| V10 哈希（9 个非改动文件） | SHA256 复算 | ✅ 9/9 与 019J 基线一致（advisor/analysis_engine/alert_engine/scoring_engine/data_adapter/daily_report/app/config/requirements） |
| 生产库零污染 | SELECT 复算 | ✅ capital_source='ths_total' 行=0；daily_reports=387（基线不变） |
| **核心功能独立实验**（PM 自建临时库 mock）：EM 三层全失败 + ths=-11800 预置 | 调用 `fetch_capital_flow('600276','a_stock')` | ✅ 返回 `('fallback', 同花顺顶替...)`；DB 写 main=-11800 / is_estimated=0 / capital_source='ths_total' / pct=NULL / ths 保留；data_status='fallback' |

**PM 核验结论**：QA 报告结论与 PM 独立复跑方向一致（顶替路径实测、防覆盖闭环代码级确认、零改动哈希、生产库零污染）。QA 44/44 断言基于独立构造的 mock 证据（临时库副本、零网络、真实库只读），可信。观察项 O-1（估算守卫拦截场景返回值语义偏差，防御性代码路径，真实流程不可达）评估为非阻塞。**PM 同意 QA 验收结论：通过。**

### 双签签署

| 角色 | 签署人 | 日期 | 结论 |
|---|---|---|---|
| QA | QA（独立验收） | 2026-08-05 | ✅ 通过（44/44 断言 PASS，1 观察项不阻塞） |
| PM | PM（独立核验） | 2026-08-05 | ✅ 同意（独立复跑 5/5 项通过，含核心功能实验） |

### 关闭前提醒

1. **运行实例重启**：当前运行中 app.py（PID 16764）为 019J 代码，019K 改动须重启 `python app.py` 后生效（含 capital_source 迁移自动执行）
2. **观察项 O-1（低风险，建议后续小批次）**：估算三处可改为 `if cursor.rowcount > 0: saved_count = 1`（1 行×3 处），使返回值与真实写入一致——不阻塞本批次
3. **登记项**：R-7（EM 覆盖清空 ths_net_inflow，既有行为）；R-2（EM 逐只挂死，019K 原候选，维持登记）

---

> **状态**：✅ QA 独立验收通过（2026-08-05）→ ✅ PM+QA 双签（2026-08-05）→ ✅ 监理批准关闭（2026-08-05）

---

## 关闭块（019K）

**监理批准关闭日期**：2026-08-05

**关闭结论**：✅ **019K 批次正式关闭**

| 流程节点 | 日期 | 状态 |
|---|---|---|
| PM 签发任务书 v1 | 2026-08-05 | ✅ |
| 架构师评审（有条件通过，M-1~M-12 并入 v2） | 2026-08-05 | ✅ |
| 监理批准 v2 | 2026-08-05 | ✅ |
| 开发执行 + 自验（42/42 PASS） | 2026-08-05 | ✅ |
| QA 独立验收（44/44 断言 PASS） | 2026-08-05 | ✅ |
| PM+QA 双签 | 2026-08-05 | ✅ |
| 监理批准关闭 | 2026-08-05 | ✅ |

**关闭时遗留事项（登记，不阻塞关闭）**：
1. 运行实例重启：用户须重启 `python app.py` 后 019K 生效（含 capital_source 迁移自动执行；当前 PID 16764 为 019J 代码）
2. 观察项 O-1（低风险）：估算三处 `saved_count = 1` 改为 `if cursor.rowcount > 0`（1 行×3 处）——建议后续小批次
3. 登记项：R-7（EM 覆盖清空 ths_net_inflow，既有行为）；R-2（EM 逐只挂死 `_em_batch_collect` 无超时包装，维持登记）

> **PM 签署**：019K 已按流程完成全部节点并经监理批准，正式关闭。归档完毕。
