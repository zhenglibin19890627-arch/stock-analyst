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
| R2 | 资金面链序与防覆盖（021L 起）：**腾讯 westock（主源）** → 东财三层（兜底）→ 新浪 lscjfb(`sina_main`) → 估算兜底；westock/东财真实行计为"当日已完成"（同日跳过 + 补采清单 + 延迟补采缺口 SQL 同口径），`sina_main` 行仍可被主源覆盖升级；真实数据不可被降级源覆盖；写入模式 `UPDATE + INSERT OR IGNORE`，**严禁 `INSERT OR REPLACE INTO raw_capital_flow`**（会清除已有字段） | `data_collector.py` `fetch_capital_flow` / `_fetch_capital_flow_westock` / 新浪分支 | ✅ |
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
| 2026-08-16 | **R7 `scoring_engine.py`** / **R8 `data_contract.py`** / `data_adapter.py` / `mock_data_provider.py` / `tests/` | 020R-48 二期：技术面多周期重构——月线方向层 0.25（空头时技术面 ×0.85 惩罚）+ 周线波段层 0.45（趋势0.25/超买超卖0.10/波动0.10）+ 日线择时层 0.30（超买超卖0.10/量价0.10/量比0.10），日线均线/趋势/波动率三子项移除；契约新增 10 个周/月线字段（32→42） | 用户（020R-48 批准） |
| 2026-08-16 | **R7 `scoring_engine.py`** / **R8 `data_contract.py`** / `data_adapter.py` / `tests/` | 020R-49：业绩预告纳入基本面成长性评分（方案 A 折价融合）——契约新增 `forecast_np_yoy`/`forecast_confidence` 两字段（42→44）；`score_growth` 净利同比按「预告×0.6×置信度(明确0.8/模糊0.6) + 正式×(1-…)」融合，预告期晚于最新财报期才生效，首亏/续亏按 -100% 处理，扭亏幅度未知不采信；不新增子项、权重不变 | 用户（020R-49 批准） |
| 2026-08-16 | **R7 `scoring_engine.py`** / **R8 `data_contract.py`** / `data_adapter.py` / `data_collector.py` / `db_manager.py` / `blueprints/` / `daily_report.py` / `tests/` | 020R-50：业绩快报并入业绩预期——契约新增 `forecast_type` 字段（44→45）；适配层统一 `get_latest_forecast_info`（快报优先于预告、同报告期快报胜出、仅报告期晚于最新正式财报期生效）；快报置信度 0.9（预告 0.8/0.6 不变）；新增 `raw_express` 表与东财 `stock_yjkb_em` 采集；`score_growth` 仅展示文案按 `forecast_type` 区分「含快报/预告折价」，**不新增子项、权重不变、融合公式不变** | 用户（020R-50 批准） |
| 2026-08-17 | **R7 `scoring_engine.py`** / **R8 `data_contract.py`**（仅注释/描述文案） / `data_collector.py` / `db_manager.py` / `tests/` | 021I：港股股东数据接入——腾讯 westock `shareholder` 命令提供港股机构持仓（机构持仓统计块 holdingPct/holdingShares）+ 股东分布 + 机构增减持方向（changeShares/instIncreaseCount）；`holder_structure` 表新增 `source` 列（'em'/'westock'，ALTER ADD COLUMN 带默认值）；港股股东行为语义=季度级机构增减持三态（True 增持/False 无净增持/None 缺失）；股东人数港股无披露源保持缺失归零；**评分逻辑、子项权重、校准档位零改动** | 用户（021I 批准） |
| 2026-08-18 | **R2 `data_collector.py`** / `daily_report.py` / `tests/` | 021L：资金面链序重构——腾讯 westock 由备用层提为**主源**（Layer 1），东财三层降为兜底（Layer 2-4）；依据：EM 直接成功率实测仅约 1/3（7 月失败率 66%），westock 同口径（主力=超大+大，探针实证相等）且稳定不封 IP。westock 行自此计为"当日已完成"（前置校验/补采清单/延迟补采缺口 SQL 三处同步移出 NOT IN 排除列表）——东财日常请求密度归零，仅低频唯一点（个股新闻/业绩预告）继续使用东财；历史缺口由补采调度器 020I 链（westock --date → 新浪）回补；估算层来源守卫、R1/R3/R4/R15 不变量全部保持 | 用户（021L 批准） |
| 2026-08-19 | `backtest_engine.py` / `tests/` | 021P：回测判定口径市场差异化——`_judge` 新增 market 参数，港股「持有观望」档正确区间 ±2%→**±3.5%**（A股不变）；三个调用点（固定周期/alpha/动态）同步传市场；港股市场报告解读附口径声明。依据：港股观望档 1 周落点仅 12% 留在 ±2% 区间（A股 34%）——无涨跌停/T+0 日常波幅装不下 A股标定窗口，属判定口径问题非选股能力问题。`normalize_rating`/JUDGEMENT_MATRIX 档位本身零改动 | 用户（021P 批准） |
| 2026-08-19 | **R8 `data_contract.py`** / `data_adapter.py` / `data_collector.py` / `db_manager.py` / `capital_detail.py` / `static/js/app.js` / `tests/` | 021Q：港股资金面数据补强——契约新增 3 个**展示字段**（south_net_buy/south_hold_ratio/inst_count_change_pct，不入 CAPITAL 完整度集合、不参与评分与归一化）；`raw_capital_flow` 新增南下两列（westock hkfund `_lgtHoldInfo`：当日净增持市值万港元+持股占比）、`holder_structure` 新增机构股东数量两列（westock shareholder `instCount`+环比，ALTER ADD COLUMN 幂等）；R2 写入模式保持 UPDATE+INSERT OR IGNORE（南下列 COALESCE 保旧值防重跑降级）；前端资金面明细卡新增「南下资金·个股」「机构股东数(港股)」两行。**评分逻辑、子项权重、档位零改动** | 用户（021Q 批准） |
| 2026-08-19 | **R7 `scoring_engine.py`** / `config_weights.json` / `tests/` | 021R：港股评级门槛差异化 + 资金面置信收缩——`_map_rating` 新增 market 参数，港股走 `config_weights.json hk_stock.rating_overrides`（推荐买入 65→70、持有观望上限 64→69，热加载）；`normalize_rating` 与全局 `rating_mapping`（80/65/50/30，红线核验锁定）零改动。analyze() 港股资金面完整度 <75% 时维度分向中性 50 收缩（系数=完整度/0.75），A股不启用（002 校准基线锁定）。依据：港股推荐买入占比 39%（A股 16%）系统性偏多，资金面 4 子项缺 2 时归一化放大剩余子项（实测 >70 分占比 63% vs 完整度 50%）。002 校准档位（margin 68/88、main 85、vol_ratio 80）保持不变 | 用户（021R 批准） |
| 2026-08-22 | **R13 `advisor.generate_advice`** / `daily_report.py` / `blueprints/system.py` / `blueprints/portfolio.py` / `app.py` / `check_redlines.py` / `tests/` | 021AE：经典引擎整体退役——删除 `modules/analysis_engine.py`、`modules/engine_switcher.py`、`config_engine_switch.json` 与新旧引擎对比脚本。`generate_advice` 内灰度分流/legacy 回退/熔断记录块移除：v5 失败（返回 None/异常）直接返回 `success=False`，**不再降级经典引擎**；签名、返回结构键集、`ratings_history`/`analysis_results`/`daily_reports` 写库不变量（R9/R10）全部不变，`engine_version` 恒为 'v5'。依据：all_v5 稳定运行超一个月（ratings_history 153 行 v5 / 0 行 legacy，期间唯一一次降级 2026-08-20 次日批次自愈）；回退目标已删除，rollback-all 端点无处可退且误触发有害，`/api/engine/status`、`/api/engine/rollback-all` 一并删除。`validate_rating_config` 迁至新模块 `rating_config.py`（R6 核验锚点同步），`_dedup_news` 迁至 `news_detail.py`，019F 隔离验收对象收敛为 data_adapter | 用户（021AE 批准） |
| 2026-08-22 | **R13 `advisor.generate_advice`** / `config.py` / 新模块 `modules/rating_hysteresis.py` / `tests/` | 021AG：评级变更迟滞（抗抖动）——`generate_advice` 步骤 2 与 3 之间新增 2b：分数跨过档位边界但未达 **±3 分迟滞带** 时维持原档（升档需 ≥目标档 min+3；降档需 <原档 min−3；多档跳变天然满足立即换挡；首次评级不受迟滞影响）。迟滞后评级贯穿全链路（操作建议/风险提示/is_change/写库/日报同一口径）；`_save_analysis_results_for_v5` 移至迟滞之后（analysis_results 与 ratings_history 必须同档）；结果新增 `rating_hysteresis` 字段、markdown 附迟滞说明。边界取数与评分引擎同源（A股 RATING_THRESHOLDS / 港股 hk_stock.rating_overrides 热加载），**评级档位定义本身零改动**。依据：实测 67% 改评间隔 ≤3 天、42% 评级日分数距边界 ≤3 分，历史回放仿真改评 263→162（−38%）。回退：`config.RATING_HYSTERESIS_ENABLED=False` 即恢复旧行为。签名、返回结构（仅新增键）、R9/R10 写库不变量不变 | 用户（021AG 批准） |
| 2026-08-28 | **R7 `scoring_engine.py`**（仅 `score_sentiment` 映射曲线）/ `config_weights.json`（a_stock 权重 + 7 行业覆盖）/ `tests/` | 021BC：消息面非对称映射 + 降权——①`score_sentiment` 改非对称曲线：s≤0 保持 (s+1)×48 全额惩罚，s>0 斜率减半 48+s×24 封顶 72（原对称曲线极多 95→72）；②A股 news 权重 0.1504→0.08，腾出 0.0704 按现值比例回填 k/f/c；7 个行业覆盖的 news 等比降至 0.08 同口径。**002 校准档位（margin 68/88、main 85、vol_ratio 80）、子项结构与权重、评级边界 80/65/50/30 全部零改动**。依据：699 条 v5 回测样本维度归因——消息面为四维中唯一与 1w 收益负相关（-0.098，n=410），高分错误样本消息分 76.8 vs 正确样本 67.1（正面情绪=已定价噪音）。回退：config_weights.json 恢复原值即回退权重；映射曲线还原 `score=(s+1)*48` 即回退引擎 | 用户（021BC 批准） |
| 2026-08-28 | **B24 `advisor.py`**（仅 `_determine_action` 文案 + 持仓建议 prose 措辞，逻辑零改动）/ `price_advisor.py`（ACTION_MATRIX / RATING_ACTION_SUGGESTION / 状态机兜底词同步）/ 前端看板 MATRIX / `tests/` | 021BH：操作建议动作词统一——"持有"与"持有观望"两个动作词并存，且后者与评级档位名同名，用户无法区分"在说评级"还是"在说动作"。统一规则：**"持有观望"仅保留为评级档位名（R6 档位定义与判定零改动），动作词一律"持有"词根**（浮亏语境保留"继续持有"）。改动点：`_determine_action` 持有观望·浮亏格与建议减仓·浮盈格 '持有观望'→'持有'；持仓 prose 2 处 '建议持有观望'→'建议持有'；price_advisor ACTION_MATRIX 3 格 + RATING_ACTION_SUGGESTION 基线 + 状态机兜底词同步；看板 MATRIX 2 格同步。决策逻辑、矩阵结构零改动（纯字符串替换）。历史数据兼容：前端 ACTION_COLOR / ACTION_LEVEL 保留'持有观望'映射 | 用户（021BH 批准，本轮直接请求） |
| 2026-09-07 | `requirements.txt` / **R17 白名单** / `pyproject.toml` / `tests/`（2 文件） | OPT-5 测试分层：新增 dev-only 依赖 `pytest-timeout`（单测防挂死看门狗，仅测试期使用，运行时不导入）；`pyproject.toml` 新增 pytest 配置（默认 `-m "not slow" --timeout=60`）；`test_backfill_scheduler.py::TestTickBackoff` 与 `test_index_refresh_021aw` 2 例打 `slow` 标记（真实时钟退避验证，基线实测占全量 431s 的 96%）。运行时零代码约束不受影响（`python app.py` 不导入该包）。附录 A 同步更新 | 用户（"开始执行"优化任务批准，OPT-5） |
| 2026-09-07 | **R7 `scoring_engine.py`**（仅 `score_main_capital` 信号口径与档位）/ **R8 `data_contract.py`**（新增派生字段）/ `data_adapter.py` / `tests/`（2 文件） | 权重批判审查三路线（1+2）：①数据侧降噪——契约新增派生字段 `main_net_inflow_5day`（最近 5 个交易日主力净流入均值，与 advisor.main_avg_5d 同口径，不入 capital 完整度集合、不触发降级）；`score_main_capital` 信号改为 0.5×当日+0.5×5日均（单方缺失回退另一方）；②档位零点对称化——95/87/85/60/42/20 → 92/70/50/30/12（±1000万 噪声区回归中性 50，移除 B18-Hotfix 零点正偏 85/60）。依据：实测当日 ±1000万 单日噪声导致全库中位 6.4 分摆动、14/44 评级翻档（迟滞 ±3 无法覆盖），中免 ±3212万 摆动 12.1 分翻档。**维度权重（43.14%）、子项权重（主力 0.50）、其余 002 校准档位（margin/vol_ratio）、评级边界全部零改动**——路线 3：权重数字等 dynamic_optimizer 300 纯净样本日裁决。回退：`score_main_capital` 还原旧档位、适配层停传 `main_net_inflow_5day` 即回退 | 用户（权重审查"均按你的建议执行"批准） |
| 2026-09-07 | **R15/R1/R2/R3×2/R4×2/R5/R17 锚点对象**（`modules/data_collector.py` → `modules/collector/` 包）/ `scripts/check_redlines.py` / `tests/`（4 文件） | OPT-3 采集模块拆分：`modules/data_collector.py`（6,055 行）按数据源 AST 纯搬移拆分为 18 个子模块（_env/http_client/symbols_status/mootdx/kline/fundamental_sina/forecast_express/holder_structure/hk_fundamental/valuation/capital_ths/capital_em/capital_westock/capital_sources/capital_flow/capital_margin/sentiment_industry/collect），原文件转为**全量再导出 facade**（192 符号表面逐符号一致，vars 快照断言）；全部既有调用方（backfill_scheduler/daily_report/blueprints/tests）零改动。**红线核验适配**：10 处源码内容锚点检查改扫 facade+全包子源（`_read_collector_all`）；R8 akshare 扫描扩展为 modules/ 全树递归（collector 包=采集层白名单）。**语义保持决策**：跨模块共享可变状态与唯一 global 写入者同模块单宿（`_EM_LAST_REQUEST_TS`/`_EM_MIN_INTERVAL_SECONDS`→http_client、`_holder_cache`×2→holder_structure、`_EM_CONSECUTIVE_FAIL_COUNT`+`_em_batch_collect`→capital_flow）；`_safe_num` v1（被 v2 全模块覆盖的死定义）删除；`_rotate_em_host`/`_call_ak_with_timeout`/`_num_float` 随唯一/共用调用方迁入 http_client/symbols_status（断 3 处循环导入）；`_env` 路径守卫与 `_EM_BAN_STATE_FILE` 的 `__file__` 目录层级 ×2→×3、logger 名保持 `modules.data_collector`；requests 无代理补丁先于 akshare 导入的顺序由包 `__init__` 强制加载序保证。测试适配：4 个测试文件 monkeypatch 由 facade 改指实现/消费方子模块（facade 再导出与实现为不同模块命名空间，补丁对包内调用不可见）。**行为不变量全部保持**：R15 签名、周末守卫、日期精确匹配、估算行标记、超时包装、westock Node 降级。已知偏差：`capital_flow.py` 1,154 行（`fetch_capital_flow` 单函数约 700 行为多源链序编排，不可再拆，记录为任务书偏差） | 用户（"开始执行"优化任务批准，OPT-3） |
| 2026-09-22 | **B24 `advisor.py`**（仅 `_build_markdown_single` / `_build_key_factors` 两个构建器增注，`generate_advice` 本体零改动）/ `rating_hysteresis.py`（新增只产说明不改评级的共享纯函数）/ `blueprints/analysis.py` / `blueprints/portfolio/watchlist_scores.py` / `static/js/analysis.js` / `scripts/audit_consistency_021bs.py`（新）/ `tests/`（4 文件） | 021BS 报告一致性审计第一轮 P1 修复：**迟滞保持态持续标注**——021AG 迟滞保持原档后，分数回到另一档区间时报告无任何持续说明（拓尔思实测：52.0 分在持有观望区间但评级仍建议减仓且无说明；且单股刷新路径组 markdown 取 `dict(analysis)` 时迟滞说明静默丢失）。修复走 B24 外层：`rating_hysteresis.score_tier_mismatch_note` 共享纯函数（与 apply_hysteresis 同阈值表，**只产说明字符串、不参与评级判定**）落点四处——advisor 两个构建器（增注）、analysis.py 三读取路径（存量报告即刻带注记）、watchlist_scores 每股 `score_tier_note`（审计纯 GET 断言面）+ 前端评分卡横幅。连带加固：analysis.py 快照路径 key_factors 遍历收敛四维规范键（原样遍历字符串键会 500、trader 字典键会伪造 0 分维度）。**generate_advice 本体逐行未动（git diff 核验）**，评级判定/档位/迟滞行为零改动 | 用户（021BS 计划 Web 批准，"分域不对称层级"+一致性审计批准链） |

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
| pytest-timeout | 单测超时看门狗（dev-only，OPT-5 2026-09-07 增补；运行时不依赖） |
| pystray / Pillow | 系统托盘 |

> 新增依赖须：可 pip 自动安装（无需用户手动配置）→ 评审 → 更新本表 + `scripts/check_redlines.py` 白名单。

## 附录 B：已作废/过时红线

| 原红线 | 处置 |
|---|---|
| data_collector.py 三处 `if False` 硬禁用估算源 | 019E 起由 R1 的 `is_estimated=1` 标记机制取代；2026-08-16 复核代码中已无 `if False` |
| "无新 pip 依赖（当前 8 包）" | 过时：现为 11 项白名单（附录 A），约束对象改为白名单机制（R17） |
| PM 上下文/架构师角色文档中带行号的红线清单 | 行号已漂移失效；以本文件语义锚点为准 |
