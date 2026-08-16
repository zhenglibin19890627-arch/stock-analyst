# 红线清单（RED LINES）— 单一事实来源

> **版本**：v1.1 | **编制日期**：2026-08-16 | **性质**：红线治理（021A 建立 / 021B 二期）
>
> 本文件是项目**红线定义的唯一权威来源**。AGENTS.md §7、PM 上下文、架构师角色
> 定义等文档中的红线清单一律视为历史快照，如有冲突以本文件为准。
>
> 所有锚点均为**语义锚点**（函数签名 / 唯一约束 / 过滤表达式），不使用行号，
> 避免行号漂移导致清单失效。

---

## 0. 治理原则

1. **红线 = 不可破坏的行为不变量（契约），不是"禁止改某行代码"**。判断违规的
   标准是"行为是否改变"，而非"文本是否变动"。
2. **行为锁优先**：凡可机械核验的不变量，必须进入 `scripts/check_redlines.py`
   （经 `tests/test_redlines.py` 随 pytest 执行）。文本锁（禁改函数）只保留在
   无法用行为锁表达的少数对象上。
3. **豁免流程**：对受保护对象的任何变更，必须在 §6 豁免登记表登记
   （日期 / 对象 / 变更内容 / 批准人 / 理由），否则视为违规。
4. **新增红线**：踩坑后固化的新约束必须（a）写入本文件、（b）若可核验则同步
   更新 `scripts/check_redlines.py`、（c）在 CHANGELOG 记录。

---

## 1. 数据可信度（P0）

| 编号 | 红线 | 锚点（当前实况） | 自动核验 |
|---|---|---|---|
| R1 | 估算/顶替数据必须带标记且**不参与评分**：估算兜底写入 `is_estimated=1`（仅展示）；评分读取必须过滤 `(is_estimated = 0 OR is_estimated IS NULL)` | 写入：`data_collector.py` 估算兜底路径；读取：`data_adapter.py` `_load_capital_*` | ✅ |
| R2 | 资金面链序与防覆盖：东财三层 → 腾讯 westock → 新浪 lscjfb(`sina_main`) → 估算兜底；真实数据不可被降级源覆盖；写入模式 `UPDATE + INSERT OR IGNORE`，**严禁 `INSERT OR REPLACE INTO raw_capital_flow`**（会清除已有字段） | `data_collector.py` `fetch_capital_flow` / `_fetch_capital_flow_westock` / 新浪分支 | ✅ |
| R3 | **M-2 日期严格匹配**：新浪 lscjfb / 腾讯 westock 逐日历史必须精确匹配目标日期（`opendate != target_date` 即放弃 / `EndDate != date_str` 即放弃），**严禁"取最新行"**；不匹配落回下一层 | `data_collector.py` 新浪分支 / `_fetch_capital_flow_westock` | ✅ |
| R4 | 周末守卫：非交易日（周六/周日）资金面全链路跳过（019G/020L）；五档盘口 mootdx 同步跳过（021C 起，防周末脏行） | `fetch_capital_flow` / `fetch_orderbook` 开头 `weekday() >= 5` 校验 | ✅ |
| R5 | 新浪/腾讯网络调用必须经模块级 `_call_with_timeout`，**严禁裸调用**（含 https 回退的第二次请求） | `data_collector.py` | ✅ |
| — | ~~三处 `if False` 硬禁用估算源~~ | **已作废**：2026-08-16 复核，代码中已无 `if False`；该机制自 019E 起被 R1 的 `is_estimated` 标记机制取代（见附录 B） | — |

## 2. 评分与评级（P0）

| 编号 | 红线 | 锚点 | 自动核验 |
|---|---|---|---|
| R6 | 评级边界 **80/65/50/30** 三处一致且区间连续：`config.py` / `config_weights.json` / `scoring_engine.py`；`config_weights.json` 写入必须**无 BOM**（`json.dump`） | 启动自检 `validate_rating_config()` | ✅ |
| R7 | `scoring_engine.py` v5 引擎核心不可改（002 校准档位 margin 68/88、main 85、vol_ratio 80 保持不变；020R-47 起互联互通子项已移除，资金面 4 子项：主力 0.50/机构持仓 0.20/杠杆 0.20/股东人数 0.10）；预警/回测等模块必须**复用 `normalize_rating`，不得重实现"分数→评级"边界映射**（D4）。`alert_engine.RATING_ORDER` 档位顺序表属允许范围 | `scoring_engine.py` / `alert_engine.py` | ✅ |
| R8 | `data_contract.py` StockData 契约不可破坏；业务逻辑**严禁耦合具体数据源原始字段**（akshare/tushare 等），必须经标准契约。字段集以 `data_contract.py` 定义为准（不硬编码数量——020R-45 +2 字段、020R-47 移除 north_net_buy，资金面完整度集合 capital 现为 4 字段）；新增/删改字段须走 §6 豁免登记 | `modules/data_contract.py` | ✅ |

## 3. 写库不变量（P0）

| 编号 | 红线 | 锚点 | 自动核验 |
|---|---|---|---|
| R9 | 每日报告不变量：`daily_reports` 三列唯一约束 `UNIQUE(report_date, stock_id, report_type)`；每股每天至多一份有效报告；**daily 生成时顶替当天 intraday**（快报仅覆盖快报，daily 删除全部后插入） | `db_manager.py` 013 迁移 / `daily_report._save_report` | ✅ |
| R10 | 评级历史不变量：`ratings_history` `UNIQUE(stock_id, rating_date)` + `INSERT OR REPLACE`，每股每天一条——回测中心"评级有效性/价格建议命中率"的依据 | `advisor.py` 评级写入 | ✅ |
| R11 | 破坏性操作（DROP/DELETE/清表）前必须自动备份（`db_manager.backup_database`，SQLite 在线热备份）；**备份失败必须中止破坏性操作**（021B 起：013 迁移/B12 迁移调用点均检查返回值，失败抛错保护数据） | `database/db_manager.py` | ✅ |
| R12 | 数据库运行配置：`PRAGMA journal_mode=WAL` + `busy_timeout=10000` + `foreign_keys=OFF`（级联由应用层手动管理，改表结构须同步维护级联逻辑）；**严禁 `FLASK_DEBUG=True`**（双进程锁冲突） | `db_manager.get_connection` / `config.py` | ✅ |

## 4. 受保护函数（P0，021B 起为「行为锁」）

> P1 已落地（2026-08-16 021B）：本节从"文本锁"迁移为**行为锁**——
> 签名由 `tests/test_redlines.py` 的签名锁定测试 + `check_redlines.py` 锚点双重守护；
> 写库不变量与输出契约由 `tests/test_advisor.py` / `tests/test_scoring_engine.py` /
> `tests/test_data_collector.py` 覆盖。**行为级变更（写库语义、输出契约、同日跳过逻辑）
> 仍须 §6 豁免登记**；纯实现细节微调无需豁免，但不得改变上述行为。

| 编号 | 红线 | 行为锁锚点 |
|---|---|---|
| R13 | **B24**：`advisor.generate_advice(stock_id, report_date=None)` 签名与行为契约（返回结构、`ratings_history`/`analysis_results`/`daily_reports` 写库不变量）不可破坏；扩展只能走后处理集成（`price_advisor.py` 模式） | 签名锁定测试 + `tests/test_advisor.py` 契约覆盖；历史豁免见 §6 |
| R14 | `advisor._build_capital_factors(factors, stock_data, stock_id)` 签名与资金面因子构建行为不可破坏 | 签名锁定测试 + `tests/test_advisor.py` |
| R15 | **011**：`fetch_capital_flow(symbol, market)` 签名不可加参数（含 force_full）、内部同日跳过逻辑与 `force_full` 增量逻辑不可破坏 | 签名锁定测试 + `check_redlines.py` R15 锚点 |

## 5. 风控与工程约束（P1）

| 编号 | 红线 | 锚点 |
|---|---|---|
| R16 | 风控阈值 5 项默认值（资金安全相关，变更必须在本文件登记理由）：成本修正偏离 ±30% 二次确认、冷却 24h、T+1 流水锁、单笔 5 万二次验证、批量上限 20 | `config.py` |
| R17 | **零代码约束**：依赖必须落在白名单（附录 A），`pip install -r requirements.txt` + `python app.py` 一键启动不变，禁止引入需用户手动配置的依赖；**westock npm CLI 依赖 Node 环境**——已落地优雅降级（`_westock_cli_query` 检测 `npx`/`npm` 不可用即跳过该层，落回新浪/估算），纳入自动核验 | `requirements.txt` / `data_collector.py` |
| R18 | **M-1**：严禁 `with ThreadPoolExecutor` 实现超时保护（`__exit__` 的 `shutdown(wait=True)` 会 join 挂死 worker）；正确模式 = daemon 线程 + `join(timeout=N)` | `daily_report.py` |
| R19 | 日报超时配置：`STOCK_TIMEOUT_SECONDS=90` / `BATCH_TIMEOUT_SECONDS=1800` | `config.py` |
| R20 | **M8→M9 顺序**：评级回测必须先于自动优化启动；**A/H 双市场独立**：回测/优化/权重均需 A股/港股分开 | `backtest_engine` / `optimizer_engine` |
| R21 | PowerShell 脚本**禁止写中文**（ParserError 编码乱码） | 工程约定 |
| R22 | 日志配置：`app.py` main() 中 `root.addHandler(file_handler)` 不可改为 `basicConfig` | `app.py` |

---

## 6. 豁免登记表

| 日期 | 对象 | 豁免内容 | 批准 |
|---|---|---|---|
| 019A | `generate_advice` | 函数体末尾新增 `_save_daily_report_for_advice()` 调用 | PM 特批 |
| 014 | `advisor._read_position` | 改为 holdings 优先 + positions fallback（豁免关闭，现为定型实现） | PM |
| 009 | `price_advisor.py` | 从"不可改"转为"可修改（仅限后处理集成方式）" | 评审 009 |
| 2026-08-16 | `tests/`（3 文件） | 测试按 020H/020R-19/019G 新语义修复；`scripts/check_redlines.py` + `tests/test_redlines.py` 新增 | 用户（021A 治理） |
| 2026-08-16 | `data_contract.py` / `data_adapter.py` / `data_collector.py` / `db_manager.py` / **R7 `scoring_engine.py`** | 020R-45：StockData 新增 2 个资金面-筹码结构字段（holder_count_change_pct / institution_hold_ratio，缺失=权重归零型）+ `holder_structure` 新表 + 采集/适配；**资金面新增「股东人数(0.10)」「机构持仓(0.20)」两子项并重排权重（主力 0.55→0.40、两融 0.35→0.20、北向 0.10 不变）；002 校准档位（north 70/88、margin 68/88、main 85、vol_ratio 80）保持不变** | 用户（020R-45 批准） |
| 2026-08-16 | **R7 `scoring_engine.py`** / **R8 `data_contract.py`** / `advisor.py` / `tests/` | 020R-47：**删除互联互通子项**（北向数据 2024-08 政策性断供，调研确认无替代源）——主力资金 0.40→0.50、A/H 两市场资金面统一为 4 子项（主力 0.50/机构持仓 0.20/杠杆 0.20/股东人数 0.10）；删除 `score_north_capital` 及注册；`north_net_buy` 移出资金面完整度集合（capital 5→4 字段）；南向资金仅展示不参评 | 用户（020R-47 批准） |

---

## 7. 自动核验（行为锁）

- **脚本**：`scripts/check_redlines.py` —— 只读源码与配置（不触网、不写库），可直接运行：`python scripts/check_redlines.py`
- **测试门禁**：`tests/test_redlines.py` —— 随 `pytest tests/` 自动执行（021B 起含 R13/R14/R15 签名锁定测试）
- **CI**：`.github/workflows/tests.yml` 在 pytest 之后执行红线核验（021B 起）
- 覆盖：R1、R2（写入模式）、R3（westock/lscjfb 日期匹配）、R4（周末守卫）、R5、R6、R7、R8（业务模块无 akshare 耦合 + 契约存在）、R9、R10、R11（机制 + 备份失败中止）、R12、R13~R15（锚点 + 签名锁）、R16、R17（白名单 + westock Node 守卫）、R18、R19

---

## 8. 修订提案（2026-08-16 021B 状态更新）

| 编号 | 提案 | 状态 |
|---|---|---|
| P1 | R13~R15 文本锁迁移为行为锁：签名 + 写库不变量由测试锁定，函数体微调走豁免流程 | ✅ **已落地**（021B）：§4 行为锁化 + `tests/test_redlines.py` 签名锁定测试 |
| P2 | R11 补强：DROP/DELETE 调用点检查 `backup_database` 返回值，备份失败时中止 | ✅ **已落地**（021B）：013/B12 两处调用点已加守卫，`check_redlines.py` 增 `backup_failure_aborts_destructive` |
| P3 | 依赖治理：白名单 11 项更新；westock npm CLI 纳入零代码红线管辖（检测 Node 可用性并优雅降级） | ✅ **已落地**：附录 A 11 项白名单 + `_westock_cli_query` Node 守卫（代码已存在）+ `check_redlines.py` 增 `westock_node_guard` |
| P4 | 红线核验自动化扩充：R3 日期匹配、R4 周末守卫、R8 契约解耦 | ✅ **已落地**（021B）：新增 5 项检查（westock/lscjfb 日期匹配、周末守卫、业务模块无 akshare 耦合、契约存在） |
| P5 | CI 门禁：pytest + check_redlines 全绿才可合并 | ✅ **已落地**（021B）：`.github/workflows/tests.yml` 增加红线核验步骤 |

---

## 附录 A：依赖白名单（2026-08-16 快照）

| 包 | 用途 |
|---|---|
| akshare | 数据源（行情/财务/资金面） |
| Flask | Web 框架 |
| pandas / numpy | 数据处理 |
| python-dateutil | 日期工具 |
| pydantic | 数据契约校验 |
| requests | HTTP 客户端 |
| openpyxl | Excel 导出 |
| pytest | 测试框架 |
| pystray / Pillow | 系统托盘 |

> 新增依赖须：可 pip 自动安装（无需用户手动配置）→ 评审 → 更新本表 + `scripts/check_redlines.py` 白名单。

## 附录 B：已作废/过时红线

| 原红线 | 处置 |
|---|---|
| data_collector.py 三处 `if False` 硬禁用估算源 | 019E 起由 R1 的 `is_estimated=1` 标记机制取代；2026-08-16 复核代码中已无 `if False` |
| "无新 pip 依赖（当前 8 包）" | 过时：现为 11 项白名单（附录 A），约束对象改为白名单机制（R17） |
| PM 上下文/架构师角色文档中带行号的红线清单 | 行号已漂移失效；以本文件语义锚点为准 |
