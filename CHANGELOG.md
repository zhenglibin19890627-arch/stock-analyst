# 变更日志 (CHANGELOG)

> **历史归档**：2026-08-28 及更早批次（末批 021BI，共 124 条，覆盖 2026-07-15 ~ 2026-08-28）已整体移至 [docs/CHANGELOG_archive_2026H2.md](docs/CHANGELOG_archive_2026H2.md)。
> 编码说明：本文件与归档文件均为 **UTF-8（无 BOM）**；Windows PowerShell 读取需 `-Encoding UTF8`（如 `Get-Content docs/CHANGELOG_archive_2026H2.md -Encoding UTF8`），否则中文乱码。

## [2026-09-25] 021CA 仓库治理批次：CHANGELOG 归档瘦身 + 三大文件拆包（facade）+ 游离脚本收编 + 数据源健康巡检 + 98 提交推送

项目分析报告五项风险/改进建议的整批落地（团队任务 t1~t7+t9，本条目为 t8 全仓终验与治理文档收口；全程遵守 docs/RED_LINES.md 红线，B24/R7/R8 不触碰、行为零变化）。终验四项证据全绿：pytest fast **1316 passed**（基线逐位一致）+ `python scripts/check_redlines.py` **28/28** + `ruff check .` **0 错** + `mypy app.py config.py modules` **74 文件 0 错**。t1 已将本地领先的 **98/98** 个提交（0ca2459..8b31c30，021E→021BZ）快进推送至 origin/master（无 force、无历史改写，推送后 ahead=0），本批次提交收口后同路径推送。分条改动：

- **①CHANGELOG 按年归档瘦身（t2，零丢失）**：主文件 259KB→75KB（-71%），截至 2026-08-31（末批 021BI）的 124 条整体移入 `docs/CHANGELOG_archive_2026H2.md`（185KB，只读历史快照），主文件保留 2026-09 起 30 条并在顶部加归档指引；三数对账 154=30+124，保留+归档条目与 git HEAD 原条目行尾归一后逐字节相等（UTF-8 无 BOM、条目区 CRLF 保持原样）。
- **②modules/backtest_engine.py → backtest_engine/ 包（t3，facade）**：1,887 行按职责域拆 9 子模块（_env 环境基座 / judgement 有效性判定 / schema 表结构迁移 / position 位置注记 / evidence 回测证据展示 / sentiment_note 情绪检验常量 / engine 核心引擎〔BacktestEngine 整类 1,109 行单模块内聚不拆〕/ simulate 模拟回测回填 / weight_experiment 权重实验），`__init__` 逐符号 X as X 全量再导出（37 符号 0 删除 0 变更、37/37 再导出同一性 is 核验）；实现体 AST 纯搬移逐字节保真；tests/ 零改动（该模块原无 monkeypatch）；`python -m modules.backtest_engine` 仍可用。
- **③modules/daily_report.py → daily_report/ 包（t6，facade）**：1,786 行拆 8 子模块（_env / _scheduler 三窗调度 / _generator 报告生成〔R18 daemon 线程+join 超时原样〕/ _progress / _freshness / _store〔R9 锚点宿主〕/ _summary / __main__），`__init__` 全量再导出 + PEP 562 `__getattr__` 动态转发 7 个运行期重赋值标量（保 facade 读标志位实时性；67 符号 0 删除 0 变更）；CLI 由 `python -m modules.daily_report` 等价保留；6 个测试文件 monkeypatch 按 tests 惯例迁指实现/消费方子模块（断言零改动）。**红线脚本读取面适配**（已登记 docs/RED_LINES.md §6）：`scripts/check_redlines.py` 新增 `_read_daily_report_sources()`，R9 顶替 SQL 锚点与 R18 禁 with ThreadPoolExecutor 两处读取切换，断言字面量、判定方向与 28 项总数零变化。
- **④blueprints/analysis.py → analysis/ 包（t7，仿 portfolio 模式）**：1,185 行拆 facade bp + 5 域子模块（_details 四维明细 / _enrich 响应增强 / advice 评级·建议·报告读取路由 / v5_demo 评分演示调试 / cards 只读卡片），9 条 URL 规则与端点名 `analysis.api_*` 拆包前后逐字一致，`blueprints/__init__.py` 聚合注册与 `app.py` 零改动（32 符号 0 删除 0 变更，tests/ 零改动）。
- **⑤游离脚本收编（t4）**：根目录 `test_us11_consistency.py` 经 git mv 迁至 `scripts/verify_us11_consistency.py`（保留历史，RM 识别），仅 docstring 运维定位说明与 sys.path 指向两处适配、其余 256 行逐字保留；根目录零游离 test_*.py；实跑完整校验 exit 0（一致性 9 股四字段全 ✅、key_factors 6/6、fallback=0）。
- **⑥数据源健康巡检（t5，新脚本）**：`scripts/data_source_health.py` 只读限速探测腾讯/东财/新浪/mootdx 四源（全局限速 ≥2s/请求、单请求超时 ≤15s、重试 ≤1），产出 `logs/data_source_health_*.md` 报告，退出码 0=全通 1=降级 2=不可用（mootdx 断供/北向停更等已知项按「预期降级」计入降级）；README.md 同步一行使用说明。
- **⑦selftest_019x 补丁可达性修复（t9）**：`scripts/selftest_019x_request_strategy.py` import 按 tests 同款迁移为 `from modules.daily_report import (_scheduler as dr)`，六补丁目标重新可达并经 offline 自检行为证明（mock 清单 5/5/3、流程顺序含补丁产物、锁忙跳过全 PASS，零真实网络零写库）；同时消除修复前「facade 属性补丁失效致 fetch_capital_flow_batch 走真网络+真读库」的隐患。
- **⑧治理文档同步（t8 收口）**：AGENTS.md §3 路由蓝图补 analysis 包注记、§6 模块地图补 backtest_engine/ 与 daily_report/ 两包行、§8 目录结构更新；docs/PROJECT_STRUCTURE.md「CHANGELOG 根目录唯一副本」表述更新为「主文件唯一写入点 + docs/ 只读历史归档快照（条目零重叠）」并订正 verify_us11 迁移行；docs/RED_LINES.md §6 豁免登记表新增 check_redlines 读取面适配条目（含日期/对象/变更/批准/理由）。
- **遗留登记（不在本批修复，待后续批次同批裁定）**：①`scripts/verify_us11_consistency.py` 断言基线过时——仍预期 3 只 legacy 股与 3000ms/只性能阈值（021AE 起引擎恒 v5、021BT 后采集链与超时股变化所致的脚本基线漂移，脚本自打印 FAIL 项如实保留，非功能回归）；②`scripts/selftest_019x_request_strategy.py` offline 自检仍 6 项 FAIL，系 019X 断言基线与实现演进的历史漂移（三窗钟点 16:10/16:40/17:10→15:30/15:42/15:54；collector 侧 EM 请求路径演进两段不经 dr）；③「补丁失效致 mock 绕过」隐患已由 ⑦（t9）消除，此后同类拆包须同步迁移 scripts/ 下经 facade 打补丁的自检脚本（本批已核无其他同类消费方）。

## [2026-09-25] 021BZ 共振类型与强弱显性化：强弱分级纯标注映射 + 选股/报告双处统一展示

共振库（买侧 4 组/卖侧平行库 3 组）星级与类型显性化落地（方案 docs/reports/021bz_resonance_plan_20260925.md，实施 docs/reports/021bz_impl_20260925.md）：**分级=星级重标的纯标注映射非新增证据，检测器输出契约零变化，零新增请求零写库**；B24 generate_advice 逐行未动、R7/classify_stage 零触碰、在线扫描只产买点边界不动。终验 pytest fast **1316 passed**（基线 1286+新增 30 零回归）+ ruff/mypy 57 文件 0 错 + 红线 **28/28** + 审计 R1/R2 重跑 P0/P1 零新增 + node --check 两 JS 通过。

- **展示层纯函数**（`modules/market_screener.py` 新段）：`RESONANCE_GRADE`（5★强/4★中/3★弱）+ `strength_grade_for`（未知/非整星级 None 不硬造）+ `direction_label_for`（bull→多/bear→空）+ `latest_trigger_date_of`（signals 字串 @date 取 max，单一实现）+ `resonance_view`（浅拷贝 additive grade/direction/trigger_date/timeliness，时效对比 kline_upto：今日/窗口内历史）+ `GRADE_NOTE_KEY`/`resonance_grade_note`（星级重标诚实声明，文案禁裸 '<' 入测试）。契约锁测试锁定 detect_*/compute_watchlist_* 输出键集零变化。
- **选股侧**：`run_signal_chunk` 附 `kline_upto`（K线在手零新增请求）；scan-signals / watchlist-signals / watchlist-sell-signals 三端点共振条目附统一徽标数据（响应组装层 additive）。`static/js/market.js`：**修复 G1 实况不符**——`_MS_RES_META` 硬编码 ⭐ 图标删除，组头星级/强弱改读后端 stars/grade（双金叉跨日 3★ 不再误画 4★，同窗混合星级如实并列）；行内新增方向徽标（多=红/空=绿 A股惯例）+ 强弱徽标（紫/橙/灰色阶，与方向红绿及回测证据徽章分栏）+ 独立触发日列（含时效小字）；CSV 增「共振强弱/方向/触发日」三列；「只看共振」与排序契约零改动。
- **报告侧方案 A**（`blueprints/analysis.py`）：`_attach_resonance_snapshot` 与 021BU `_attach_backtest_evidence` 同位同型——复用 `_read_watchlist_klines`+`compute_watchlist_signal/sell_result` 离线复算（零网络零写库毫秒级），产出 `resonance_snapshot`（scope/window/kline_upto/kline_count/buy/sell/note），实时挂 `_enrich_advice_result`、快照挂 report-latest 组装尾部——**两路径键同构同源（测试硬断言），存量报告读取路径现算即刻带块**；失败静默降级键置 None。`static/js/analysis.js` 新增「⚡ 共振信号」条带（趋势罗盘与操盘手建议卡之间，内联数据零额外请求）：[方向][类型][★级][强弱]+触发日（时效）徽标行、空态诚实「近 N 个交易日内无共振触发」、脚注分级声明+口径行、动态文本转义防守；快照降级不渲染整块。
- **行为等价收敛**（`modules/trader_advisor.py`）：`_res_date_desc` 日期提取改复用 `latest_trigger_date_of`（同一正则收敛单一实现，021BR 三态文案逐字不变，既有用例即回归网）。
- **测试**：新增 `tests/test_resonance_display_021bz.py` 30 例——纯函数映射边界/方向/触发日/视图 additive/声明禁裸 '<'；契约锁（检测器键集恰等+chunk 既有键+kline_upto additive）；三扫描端点徽标数据；/advise 与 /report-latest 快照在场+两路径同构+同源同值硬断言+无K线诚实降级；_res_date_desc 行为等价。

## [2026-09-24] 021BW 第二轮修复（t4）：迟滞标注表缝修复（F1 P1）+ A股回测样本补跑（F2）

按 t3 复审发现全清（docs/reports/021bw_fix_impl_20260924.md）：F1（P1）修复＋F2（P2）零代码补跑清零，终验 **审计 R1/R2 双零 P0/P1** + pytest fast **1269 passed** + ruff/mypy/红线 28/28 全绿。

- **F1（P1）迟滞保持态标注表缝修复**（`modules/rating_hysteresis.py`）：`_tier_for_score` 原按闭区间 [min,max] 归类，档位表整数边界（29/49/64/79）之间存在表缝——小数分数（600519 49.7 实测）返回 None → `score_tier_mismatch_note` 静默返回空 → 三层标注面全缺，且「重生成自愈」假设被 t3 复审实测否定。修复：判定改为与 `_map_rating` 同款连续口径（按 min 降序取首个 score≥min）；**区间展示稳定化**——档表内分数维持 min–max 逐字不变（与 021BS 存量注记一致，防 stored×live 文本漂移），仅表缝分数改用连续换挡边界（49.7 → 「30–50 分」）。仅展示层判定：`apply_hysteresis`（其不消费该函数）与 `_map_rating` 评级判定本体零改动（R7/B24 合规），既有迟滞全部用例原样通过。
- **F1 测试＋审计断言**：`tests/test_rating_hysteresis.py` 新增/修订 6 例（五组表缝分数标注、`_tier_for_score`×`_map_rating` 16 边界值全程一致、区间展示双向锁定：表缝 30–50 / 档表内 50–64 逐字稳定、港股 69–70 表缝、超高分数归最高档）；`scripts/audit_consistency_021bs.py` selftest 新增用例20（表缝标注回归锁），实跑 6/6。
- **F1 实测闭环**：600519 看板 watchlist-scores `score_tier_note` 在场（「总分 49.7 位于「建议减仓」档分数区间（30–50 分）……」），评级本体仍「持有观望」不变；R1 审计 600519 R04 **P1→P2 闭合**。
- **F2（P2）A股回测样本补跑**（运行器 `scripts/backfill_astock_backtest_021bw_t4.py`，备份 `db_backup_20260924_125635`/`_130307`）：生产端点原路径 `batch_backtest(market='a_stock', days=45, force=False)`——**657+1 条评级全部补算 0 错误**，A股回测行 834→**1492**，回测覆盖 **46/46 只**（复审时 41/46，15 只新股全部入样），与 t7 港股补跑合并后 `fill_pending` 未接线的过渡期覆盖缺口双市场清零。过程披露：R2 复跑暴露 N02 P1×1（300456 昨日 20:09 报告 × 今日盘中数据推进的分歧判定翻转，数据时点差非代码矛盾），经生产读路径 report-latest 触发 021K 重评自愈（与 15:54 批次同构，沿 t3 F3 惯例如实披露），二次补跑保持覆盖。
- **过程如实记录**：F1 首轮实现曾因区间展示改动引入 N08 P1×10（存量落库注记与 live 现算文本不一致）——即时收敛为「档表内逐字不变＋表缝分数连续边界」双向锁定展示，复跑清零（详见实施报告 §4 审计时点注）。

## [2026-09-24] 021BX 数据完整度优化：港股回测补跑 + 港股资金面回补 + 盘口断供诊断与健康提示（t7）

按 t6 审计分级实施（docs/reports/021bx_impl_20260924.md）：**可修项全修、回补全成、限制项登记**——13 维度缺口矩阵资金面 4⚠→0⚠，K2 港股回测停更对齐（差 0 天），K6 盘口定案为服务端断供（展示维降级可接受，评分零影响）。

- **K2 港股回测零代码补跑**（运行器 `scripts/backfill_hk_backtest_021bx.py`，备份 `db_backup_20260924_123416`）：走生产端点 `POST /api/backtest/rerun {"market":"hk_stock","days":30}` 原路径——128 只评级全部补算 0 错误，hk 回测行 200→**322（+122）**，最新评级日 09-07→**09-23 与评级链对齐**；连带 020K 设计内孤儿行自愈清理 96 条。**接线方案待批**：`fill_pending_backtests` 每日补算（收盘批次后调用一次，现成函数零新逻辑）属行为级新增，方案写入实施报告 §1.2 供用户批准，本轮未接线。
- **港股资金面缺口回补 38/38**（新 `scripts/backfill_hk_capital_021bx.py`，备份 `db_backup_20260924_123647`）：dry-run 盘点以该股自身 K 线交易日为日历、main_net_inflow 非空才算覆盖——10 只港股共 38 股·天缺口（4 只缺 8 天＋10 只同缺 09-11 系统失败日）；复用生产 `backfill_capital_history`（020I westock --date 链，UPDATE+INSERT OR IGNORE、est=0、R2 合规），限频 2s 克制；**38/38 全部成功**，审计矩阵资金面 4⚠→0⚠。
- **K6 盘口停更根因定案＋健康提示**（`blueprints/system.py` orderbook 说明列）：t7 连通性实测——4 台 TDX 备用池 TCP 全通但 quotes/bars **全部返回空载荷**，bestip 全网扫描（71s）亦空、多股票复测一致 → **TDX 服务端自 09-10 起对 mootdx 0.11.7 停止返回数据**，非本侧代码/服务器池问题。健康说明挂「盘口为展示维度，评分链不消费，停更不影响评级（恢复后自动续采）」降级声明；mootdx 升级适配留待 R17 流程另立任务。
- **审计脚本断言化**（`audit_data_completeness_021bx.py`）：K1/K2/K6 对账项改动态 SQL 实况；**新增 K2 回测新鲜度断言**（回测最新评级日与评级最新评级日差 ≤2 天，超限 ⚠️——fill_pending 未接线的过渡期内停更复发必被捕获）；换手率现状行同步 t2 终态。
- **登记不修（按 t6 分级与队长裁定）**：K4 导入行零佣金（用户交割单自处置）、K5 latest_price 死列、股东户数 3 只滞后（§6 域守卫待批；展示层标注本轮不做）、中芯融资滞后 8d（披露节奏）、capital_source 标记（低优）、market_overview 间歇失败（021AX 自愈在管）。K1 换手率余量 60.4% 维持 t2 边界（t7 再试一轮仍遇东财 WAF 长窗封禁，自愈路径在案）。
- **测试**：新增 `tests/test_data_integrity_021bx.py` 7 例——K2 断言双分支（对齐 ✅/停滞 ⚠️+禁裸 '<'）；find_gaps 缺口检出/估算占位行不算覆盖/无缺口不产条目；K6 健康提示文案契约。终验 fast **1263 passed** + ruff 全仓绿 + mypy 57 文件 0 错 + 红线 **28/28** + 审计重跑 EXIT=0（后快照 docs/reports/021bx_data_audit_after_t7_20260924.md）。

## [2026-09-24] 021BW 情绪维度深化：市场报告「情绪检验现状」解读行 + 换手率字段断供修复回补（O3+O5①）

对「根据情绪交易」的证据回答落地（t1 检验 docs/reports/021bw_sentiment_plan_20260924.md）：**五类情绪代理均不满足入模型门槛，纪律与位置因子优先**——零新增情绪机制、零权重/评分/classify_stage 改动（O1 不做、O2 观察项随月重跑复核、O4 行为机制不新增、O5② staleness 守卫只记录不修属 §6 域）。

- **O3 市场报告「情绪检验现状」解读行**（`modules/backtest_engine.py`）：新常量模板 `SENTIMENT_EVIDENCE_NOTE`（a_stock/hk_stock 各一行，R20 分市场、文案禁裸 '<'）+ 纯函数 `sentiment_evidence_note_for(market)`（未知市场 None 不加行），`_build_interpretation` 末尾 neutral 行内联——回测中心「客观解读」卡直接展示，报告页/看板同源同值；内容为 t1 检验结论冻结值（标注日期+复核脚本指针 `scripts/query_sentiment_evidence_021bw.py`），证据更新只改常量一处。零触碰 generate_advice（B24，市场报告为回测域）、零回灌评分（R7）。
- **O5① 换手率断供根因修复**（`modules/collector/kline.py`）：根因实锤——`fetch_kline` 入库自基线起对 turnover 硬编码 0（腾讯 fqkline 与 mootdx 日K均不返回该字段，全库 25,541 行实测无一非零，**非采集源切换丢失**）。修复：东财日K（push2his kline/get，fields2 末位 f61）作「换手率旁路」按日对齐合并——`_em_secid`/`_parse_em_kline_turnover`/`_merge_turnover` 纯函数 + `fetch_kline_turnover_em`（复用 `_http_get_em` 019Z 全局最小间隔，max_retries=1 单轮封顶不走 30~60s 退避，旁路失败零影响主采集链）；**既有非零换手率只升不降**（旁路失败日历史真值不被 0 覆盖，盘中刷新既有保护不变）；价格五档仍全部来自腾讯/mootdx（不引入新价格源，R2 语义不涉及）。诚实口径：0/负值=缺失（下游 bucket_turnover 同口径）。
- **历史断供段回补**（新 `scripts/backfill_kline_turnover_021bw.py`）：与采集侧同源同函数，只填缺失绝不覆盖非零真值，写库前 `backup_database`（R11，失败即中止）；`--dry-run` 盘点 / `--pause` 防东财 WAF 频控 / 多轮失败重试（WAF 窗口式丢弃如实冷却续跑，幂等）。首轮回补 **15,159/25,541 行**（19/56 只，备份 `db_backup_20260924_113312_backfill_turnover_021bw.db`），WAF 频控致 37 只待续；节流续跑结果与回补后值域核验见实施报告 docs/reports/021bw_impl_20260924.md（回补后 **回测窗口 A股评级日换手率可用率 74.5%（505/678）**——t1 判「不可检验」的 S5a 换手率代理自此可检验；未齐部分随月度复核脚本滚动补齐）。
- **审计断言化**（`scripts/audit_consistency_021bs_r2.py` 新 N10 规则 + selftest 用例5）：①静态面——SENTIMENT_EVIDENCE_NOTE 结构契约（A/H 分行互异=R20、禁裸 '<'、门槛/结论句/复核脚本三锚点在场）；②动态面——有样本市场的 market-report interpretation_parts 恰含一行且与常量同源同值（缺失 P2 应接未接/重复或不同文 P1），零样本市场诚实早退不加行（被绕过 P1）。
- **O5③ 港股回测停更再确认**（只读复核，不修）：`ratings_history` 港股链路存活（最新 2026-09-23）而 `backtest_results` 港股行止于 2026-09-07——断点在**回测补算/触发链**而非评级生成，与 021BU 遗留判断一致，独立任务跟进。
- **测试**：新增 `tests/test_sentiment_evidence_021bw.py` 27 例——O3 纯函数（分市场/未知市场 None/禁裸 '<'/五类代理+结论句+脚本指针锚点）与展示路径（market report 内联同源同值/neutral 色调/零样本早退）；O5① 纯函数（secid 9 参数化/解析缺失规则/合并不改入参）与采集路径（旁路值入库/EM 失败优雅降级不崩溃/既有真值只升不降/0 值可被旁路升级/不支持市场零请求）。终验 fast **1256 passed**（含 021BR 分域层级全绿）+ ruff 绿 + mypy 57 文件 0 错 + 红线 **28/28** + 审计 R1 重跑 P0=0/P1=2（与本轮基线持平，零新增）+ R2 selftest 全过（N10 在场）。

## [2026-09-24] 021BW 录流水费用实时预估：fee-estimate 端点 + 表单分列实时展示 + broker 字段生效（021BV 备忘 F-5）

持仓页录入买卖流水时按 价格×数量×买卖方向×所属账户券商配置 实时估算费用并分列展示（佣金[含最低佣金规则]/印花税[仅卖出]/过户费[双边]/合计），盘中录入立见成本。方案见 docs/reports/021bw_fee_estimate_plan_20260924.md（t1）。**零写库零新表**（端点至多三条 SELECT）、零新依赖；B24/R7/classify_stage/R16 零触碰。

- **分项纯函数**（`modules/trade_fees.py`）：新增 `estimate_trade_fee_items`（同参同口径，返回 commission/stamp_tax/transfer_fee/misc_fee/total/applied_rules/by_rate/broker_label——分项各自取整到分的 021BV 口径不变）；`estimate_trade_fee` 改为其 `total` 透传（单一事实源，既有签名与对外行为零变化，021BK 全部用例即回归网）。`applied_rules` 逐条中文说明按实际计价方式生成（按费率/按最低佣金/免5/仅卖出/双边/港股简化模型），文案禁裸 '<'（测试全口径扫描锁定）。
- **方案 A 落地——broker 字段从此生效**：新增 `resolve_broker_profile_for_account(name, broker)`——broker 非空先按 TRADE_FEE_BROKERS keywords 匹配，未命中或为空回落账户名（= 既有 resolve_broker_profile 语义），双空默认档。预估端点与 POST 落库估算（`trades.api_add_trade`）共用该解析，防「预估免5、落库5元地板」分叉；存量账户 broker 为空串 → 兜底名匹配，行为零变化（021BK 回归锁定）。
- **新端点 `GET /api/portfolio/fee-estimate`**（`blueprints/portfolio/trades.py`）：参数 trade_type（四枚举，非法 400）/ price / quantity（int 截断同 POST）/ amount（直填优先，与 POST L277 落库基数同式）/ account_id（缺省默认账户，不存在 404）/ stock_id（取 stocks.market 判 A股/港股——港股印花税 0.1% 双边 vs A股 0.05% 卖出单边，缺省按 a_stock）。amount≤0 返回 200 全零 + 空态文案「填入成交价和数量后显示预估」（前端防抖高频触发下无错误分支）；响应含分列/total/by_rate/broker_label/applied_rules/estimation_note（估算仅供参考，以次日交割单为准）。
- **前端实时预估条**（`templates/index.html` 占位 div + `static/js/portfolio.js`）：表单 input/change + 300ms 防抖 + seq 自增防慢响应回写旧值；分列渲染「费用预估（券商档）：佣金/印花税/过户费[杂费]/合计」+ 规则小字；三条防误导状态——编辑模式（PUT 无自动估算）隐藏预估条、cancelEditTrade 恢复、手填佣金时顶部提示「落库以手填值为准」；dividend/红利补税直接渲染服务端全零与规则文案；fetch 失败静默隐藏（预估是增强不是闸门，绝不 alert 不阻断录入）；applied_rules/broker_label 均为服务端 config 派生文案（不含用户输入）可安全进 innerHTML，数字一律 toFixed。
- **测试**：新增 `tests/test_fee_estimate_021bw.py` 28 例——分项纯函数（银河免5 分列/买卖差异/东财地板 by_rate=False/港股简化模型/dividend 全零/零金额空态/总额与 estimate_trade_fee 全参数矩阵等价锁/文案禁裸 '<' 扫描）；方案 A 四分支（broker 优先/未命中回落名/空回落名/双空默认档）；端点契约（两档券商/最低佣金触发/缺省默认档/港股/空态 200/0 与负值/price=abc 400/trade_type 非法 400/账户不存在 404/amount 直填优先/quantity 截断）；一致性锁（端点 total==estimate_trade_fee 矩阵、分项和==total、**「名含银河+broker=东财」账户 POST 落库佣金==端点预估 16.00**——任一端仍按名匹配即 19.53，锁死分叉）；零写库守卫（GET 前后 trade_records/holdings 行数不变）。终验 fast **1229 passed**（含 021BK 全绿）+ ruff 绿 + mypy 57 文件 0 错 + 红线 **28/28** + `node --check portfolio.js` 通过。
- **备忘待办（需用户数据，本轮不动代码）**：①银河免5 交割单闭环——预估条（免5 文案）与交割单并排即天然核对面，闭环时顺带截图；②中免东财流水处置意向待用户明确。

## [2026-09-24] 021BV 银河费用修复：免5 裁定落地 + 分项舍入对齐 + 存量重估与持仓重算

用户实测反馈"银河估算偏高"的修复落地（t1 诊断 docs/reports/021bv_fee_diag_20260923.md + 队长按用户预授权裁定情景 A；t4 盘中速览卡 v2 另见上方独立条目区/当日条目）。

- **config 银河档免 5**（`TRADE_FEE_BROKERS` 银河 `commission_min: 5.0→0.0`，费率万1.853 不动）：裁定依据=①09-09「交割单实测」数学上未被任何一笔验证（全部银河流水金额 <26,983 元地板门槛，佣金分量恒 5.00）；②用户"偏高 2~4 元/笔"反馈与地板高估形态唯一自洽（银河"万一免五"类套餐常见）；③东财对照组 8/8 分厘拟合证明 min=5 模型无误、属银河档参数错。**回退路径**：交割单若显示最低 5 元，改回 5.0 后重跑 `scripts/reevaluate_fees_021bv.py --apply` 即完全回退（R11 备份在）。
- **分项舍入对齐交割单**（`modules/trade_fees.py`，t1 §5.3 顺手项）：估算由"总和先加后舍"改为分项（佣金/过户费/印花税）各自 round 到分再求和——消除 id=59 型 1 分差；存量断言全部同值。
- **重估脚本**（新 `scripts/reevaluate_fees_021bv.py`）：默认 dry-run 零写库；`--apply` 先 `db_manager.backup_database`（R11，失败即中止零写入）→ 单事务仅回写 `commission_estimated=1` 行（est=0 用户实填/33 笔 comm=0 导入旧行 SQL 层即排除绝不碰）→ 涉及（股票,账户）逐对 `_recalculate_holding` 全量重算（等值行幂等重算兼作持仓行缺失修复）→ 按 021BK/eaf9369 语义清重算口径持仓 `is_cost_adjusted`（审计记录保留）；est 标记保持 1。
- **真实库执行结果**（2026-09-24，备份 `db_backup_20260924_010021_021bv_fee_reeval.db`）：回写 10 笔——银河 7 笔 56.92→36.94（id=50 因分项舍入 14.97，余与 t1 情景 A 逐分一致：1.00/2.51/1.01/4.88/3.06/9.51）+ 东财 3 笔 ±0.01 分项舍入对齐；合计 372.23→352.24（Δ−19.99）。持仓重算 7 组：银河恒瑞 600 股成本 51.2903→51.2750、中免 1400 股 59.3894→59.3843、五粮液 100 股 74.0058→73.9688；东财拓尔思/无线传媒幂等不变；**数据修复**：东财缺失持仓行自动重建——中免 1000 股 @53.6686（**active**，此前持仓页不可见的真实分仓）、富祥收敛 cleared（realized 538.32）。
- **测试**：`tests/test_trade_fees_021bk.py` 更新至新口径（银河免5 小额/大额走费率/旧门槛 26,983 两侧连续/分项舍入自搜索锁定/东财地板回归；并修正一处历史误绿——银河 API 用例原断言 5.1 实际命中的是 init 预置默认账户档，现显式 account_id 指向银河并补默认档地板回归例）；新增 `tests/test_reevaluate_fees_021bv.py` 5 例（dry-run 零写库/计划对齐 t1/apply 全管线含自动建行与清标记/二次 apply 幂等/备份失败中止零写入）。

## [2026-09-24] 021BV 盘中速览卡 v2（t4）：当日盈亏列 + 排序切换 + 行动作指引 + 交易时段自动刷新 + 当日流水概览

看板盘中速览卡升级（零写库契约不变：新增代码仅 SELECT，写库面仍仅 price_cache 巡检）：

- **①当日浮动盈亏列**（`modules/intraday_patrol.py build_snapshot` 行增量 `total_qty/avg_cost/market_value/unrealized_pnl/unrealized_pnl_pct`）：holdings 账户无关聚合（与持仓列表端点 `unrealized_pnl` 公式同源），无价/零成本显式 None；前端 `_intradayPnlCell` 红盈绿亏、title 载成本×数量，与距止损列并列。
- **②排序切换 chips**：距止损%（默认，破线/最贴近在前）/当日盈亏%（亏损最大在前）/涨跌幅%（跌幅最大在前）——纯前端 `_intradaySortRows` 纯函数（键缺失殿后、同值按严重度+代码），localStorage 记忆（隐私模式静默回默认）；`_intradaySeverity` 收编删除。
- **③行动作指引列**：`_scan_today_top_actions`——stored `key_factors.trader.top_action`（日报预计算）零重算优先；stored 缺失且行∈(触线,逼近) 经 `derive_trader_signal_summary` live 只读兜底（普通行沉默控成本；021BS R2 N01「一面沉默一面报警」在速览卡的复发防线）；前端已触发红色加粗🎯、收盘口径 tooltip 与盘中 state 并列不混同。
- **④交易时段自动刷新开关**（默认关）：90s 间隔（60-120 区间取中），服务端 `session.in_session`（权威）+本地时钟（9:15-11:35/12:55-15:05 工作日）双信号，休市自动停零请求；只 GET 重读快照+行动清单（内存读零网络零写库），不触发 POST 行情请求（021BN-c 风控）；卡片不在 DOM 跳过；开关状态随卡片重渲染恢复。
- **⑤当日已录流水概览行**：`_read_today_trades`（trade_records 只读聚合，buy/sell 笔数+金额，dividend/跨日排除）读取时现查——录流水刷新即见；前端🧾概览行载「金额不含费」注记。
- **契约与文案**：`blueprints/dashboard.py` docstring 增量键契约；文案三层禁裸 '<'（端点副本断言/JS 中文字面量契约测试/动态值 escapeHtml）。
- **测试**：新增 `tests/test_intraday_v2.py` 16 例（盈亏 5：实时/缓存/无价/多账户加权/零成本；流水 4；指引 5：stored/live 门控/普通沉默/异常静默/坏 JSON；端点契约 1；node 排序包装 1）+ `tests/js/intraday_sort_test.js` node 契约 8 断言（三键排序/纯函数/null 殿后/严重度平局/记忆持久化/文案无裸 '<'）；顺手修复 `test_intraday_patrol.py` 6 例日期腐坏（`_seed_snapshot` 写死 2026-09-23 跨日全哑，改真实北京时间今日滚动）。终验 fast **1191 passed** + ruff 绿 + mypy 57 文件 0 错 + 红线 **28/28** + `node --check` 过。

## [2026-09-23] 021BT t5 收尾修复：checker F1（非时段盘中项门控）+ F2（逼近阈值单一来源）

终验（docs/reports/021bt_verify_20260923.md §9）两项低危 findings 修复，提醒语义收紧、展示不受影响。

- **F1 非时段兜底快照不产盘中项**（`modules/intraday_patrol.py get_intraday_alerts`）：终验实测非交易时段打开看板时，`get_snapshot_for_dashboard` 的兜底现算快照（source='fallback'，价格源=price_cache 缓存）被持久化为 `_LAST_SNAPSHOT`，绕过"快照非今日不提醒"防线，行动清单凭缓存价虚构 P0「盘中触线」项。修复加两道门控：①快照来源门——仅 `source∈('auto','manual')`（巡检真实跑过一轮，二者仅在交易时段内产生）产提醒，fallback 仅供速览卡展示；②行级实时门——仅 `quote_ok=True`（本_round 今日实时快照）的行参与提醒，混合市场轮中休市侧缓存兜底行不产提醒。速览卡展示与状态标注完全不变。
- **F2 逼近阈值单一来源**：`INTRADAY_NEAR_STOP_PCT` 曾在行动清单文案（"不足 1%"）与前端逼近高亮（`dv<1`）硬编码。收敛：①行动清单 near_stop 文案改读 `config.INTRADAY_NEAR_STOP_PCT`（`%g` 去尾零，1.0→'1'）；②快照根增 `thresholds:{near_stop_pct,swing_pct}` 透出（端点 docstring 契约同步）；③前端逼近高亮改**消费端点 `state==='near_stop'` 字段**（后端按 config 判定），不再自行算 1%——阈值调整三处联动收敛为 config 单点。
- **测试**：`tests/test_intraday_patrol.py` +6（F1：fallback 快照零盘中项回归锁/行级 quote_ok 门/auto 轮提醒不受影响；F2：文案随 config 生效/默认 1% 基线/thresholds 透出+判定跟随）。终验 fast **1143 passed** + ruff 绿 + mypy 57 文件 0 错 + 红线 **28/28** + `node --check portfolio.js` 通过；真实库复现 F1 场景冒烟：fallback 快照 8 只照常展示（600276/601888 破线如实标注）而 `get_intraday_alerts()`=0。

## [2026-09-23] 021BT 盘中操作便利化 t3（前端）：看板「⏱ 盘中速览」卡 + 一键刷新整合

总览看板新增盘中速览卡：持仓股一屏一行——现价/涨跌%/距止损%（破线红色加粗警示）/距 MA20%/今日信号标记/状态徽标，触及止损行红色置顶；卡标题交易时段标识（🟢 交易时段 / ⚪ 休市，端点透出非前端复刻）；「🔄 盘中刷新」一键原位刷新速览卡+行动清单卡。纯前端改造 + 速览端点两列增量，固定脚注『盘中口径，以收盘确认为准』。

- **速览卡**（`static/js/portfolio.js`）：`loadDashboard` 增**第 4 路并行** `GET /api/dashboard/intraday`（no-store，失败/网络异常双静默降级 null，不阻塞看板——021E 教训）；`renderDashboard` 在行动清单卡之后插入 1.25 盘中速览卡 `renderIntradayCard`；行按严重度排序（触线 > 逼近 > 异动 > 其余），触及止损行 `#fdecea` 红底 + 距止损红色加粗，逼近带内琥珀；状态徽标四色（触线绿系风控/逼近琥珀/正常灰/无止损参考浅灰）；非交易时段显式标注"最近快照"；无持仓/模块不可达时一行收缩不占版面。
- **一键刷新**（`intradayManualRefresh`）：走 **`POST /api/dashboard/intraday/refresh`**（t2 专用端点：仅持仓 8 只批量取价 + 60s 后端权威冷却）——**不触发全自选 refresh-prices**（风控更克制，021BN-c"勿反复刷新"教训；持仓页原刷新按钮职责不变）；节流双层=前端 60s 记忆 + 后端 cooldown 透出提示"稍候"；刷新成功后并行重拉速览+行动清单两卡 **outerHTML 原位刷新**（不动整页，筛选 chips 状态保留）。
- **端点两列增量**（`modules/intraday_patrol.py`，纯读库零网络）：①`ma20`/`ma20_distance_pct`——收盘口径 MA20 参照（今日之前最近 20 根已完成日K，不足 20 根显式 None 不凑数，排除盘中 tencent_intraday 残留行）；②`signal_labels` 今日信号标记——`_scan_today_signal_labels` 读取时 overlay（`scan_watchlist_signals/sell` 仅持仓 id 离线复算，"今日"口径与行动清单路2 一致=命中触发日==最新K线日，失败静默空表）。端点 docstring 契约同步。
- **验证**：新增 `tests/test_intraday_patrol.py` +6 例（MA20 计算/不足 20 根缺失/信号 overlay 最新日门槛/复算失败静默/空持仓短路/读路径行键契约）；`node --check portfolio.js` 通过；文案无裸 `'<'`。终验 fast **1137 passed** + ruff 绿 + mypy 57 文件 0 错 + 红线 **28/28**。真实库只读冒烟：8 只持仓 A/H 全量出 MA20 距离与状态（恒瑞医药距止损 0.35% near_stop + 距 MA20 +1.01%）。

## [2026-09-23] 021BT 盘中操作便利化 t2（后端）：盘中巡检调度器 + 触线/逼近/异动提醒 + 行动清单盘中项

在**收盘确认口径零改动**前提下新增盘中感知层：交易时段每 5 分钟巡检持仓股实时快照（腾讯批量，持仓 8 只=1 请求/轮 ≤48 请求/日），盘中触及止损线/逼近止损/快速异动在看板速览端点与行动清单即时可见，全部标注『盘中口径，以收盘确认为准』。方案：docs/reports/021bt_intraday_plan_20260923.md（t1 产出，四决策点按推荐案落地）。

- **批量取价下沉复用**（新模块 `modules/realtime_quotes.py`）：`_fetch_realtime_price_batch` 自 `blueprints/portfolio/market.py` **逐字平移**（仅加模块级 0.25s 节拍 `_pace()` 防手动连点叠加巡检），market.py 与 facade 再导出面零变化，refresh-prices 行为零变化（仍刷全部非退市股）；新增巡检专用 `_fetch_quote_snapshot_batch`（多解析 快照时间戳[30]/昨收[4]/成交量[6]，零 mootdx 降级零重试）。**2026-09-23 实测核验**：A/H 混合单请求覆盖；快照时间戳 A股 `YYYYMMDDHHMMSS` / 港股 `YYYY/MM/DD HH:MM:SS` 两形态统一去非数字归一。
- **盘中巡检调度器**（新模块 `modules/intraday_patrol.py`）：镜像 backfill 模式——`threading.Timer` 串联（daemon、无重叠、随主进程存亡），`start_intraday_patrol()` 幂等 + 启动即检（Timer(0)），app.py main() 注册（try/except 不阻断启动）；时段门控复用 `kline._is_intraday_session`（A/H 分别判定，任一在盘中才发其批量请求），非时段 tick 空转=一次布尔判断；**节假日盲区守卫**：快照时间戳非今日 → 该股跳过不写库不报警，连续 2 轮全部非今日暂停至时段边界；**失败静默降级**：轮失败保旧数据（price_cache 旧值不覆盖不归零）+ 连败 3 轮暂停至时段边界，force 手动可绕过，时段边界 tick 自动重置；**写库面仅 price_cache**（INSERT OR REPLACE 幂等，与手动刷新同表同口径），raw_kline/alert_history/新表零写入；状态判定纯函数 `evaluate_stock_state`：触线（实时价≤有效止损，双源取高者与 `_stop_level` 同口径）> 逼近（距止损不足 1%）> 正常，无止损线显式 unknown，异动=|涨跌幅|≥3% 或当日量≥近 5 日均量×1.5，阈值全走 config。
- **速览端点**（`blueprints/dashboard.py` 增两端点）：`GET /api/dashboard/intraday`（读内存快照；缺失/跨日零网络兜底现算——price_cache 显示价；响应带 session/patrol/counts/disclaimer 契约，t3 前端消费）+ `POST /api/dashboard/intraday/refresh`（一键刷新，60s 冷却，非时段/停用/暂停返回明确 reason）。巡检内存快照不落盘（决策点3：触线不留痕）。
- **行动清单路0b**（`modules/action_list.py`）：`get_intraday_alerts()` 桥接内存快照 → `kind=intraday_alert` **priority 0 置顶于持仓纪律之前**（`_sort_key` 增 P0 分支，类内触线>逼近>异动），行内固定缀『盘中口径，以收盘确认为准』+ `detail.intraday/disclaimer` 供前端视觉区分；快照缺失/非今日零盘中项不虚构；stats 增量键 `intraday_alerts`。**_scan_stop_discipline 参数化（只增不改）**：`price_map=None`（默认，生产路径不传）收盘判定行为逐字保持，行内不带增量键；传入时判定改用盘中价并增量携带 `price_source='intraday'/as_of`，`build_action_list` 纪律行 reason 按此分支（'止损纪律盘中触发：现价 X（HH:MM 盘中口径，以收盘确认为准）'）。
- **config.py** 增 021BT 段：`INTRADAY_PATROL_ENABLED=True`（总开关回退）/ `INTRADAY_PATROL_INTERVAL_MIN=5` / `INTRADAY_NEAR_STOP_PCT=1.0` / `INTRADAY_SWING_PCT=3.0` / `INTRADAY_VOL_SPIKE_RATIO=1.5` / `INTRADAY_PATROL_PAUSE_AFTER_FAILS=3` / `INTRADAY_MANUAL_REFRESH_COOLDOWN_SEC=60`。
- **收盘确认口径零改动清单**：`fetch_kline` 同日跳过/tencent_intraday 回填、`_refresh_kline_today_bar`、15:54 批次与 scan_once、`_scan_stop_discipline` 默认路径（021BR 全部断言原样通过）、`build_operations_matrix`、`_stop_level`、classify_stage、B24 `generate_advice`——零触碰（git status 核验无相关文件）；操盘手矩阵/预警表零触碰。
- **实测冒烟**（非交易时段，真实库只读）：`run_patrol_round` 零请求跳过；速览兜底 8 只持仓 A/H 全量出状态（恒瑞医药 45.58 距止损 45.42 仅 0.35% → near_stop；中国中免 52.19 低于有效止损 56.16 → below_stop）。
- **测试**：新增 `tests/test_intraday_patrol.py` 41 例（状态判定纯函数/双源取高/时段门控零请求/节假日守卫+两轮全非今日暂停/price_cache 写库面禁归零/连败暂停+force 绕过+边界重置/冷却/行动清单 P0 置顶与无裸 '<'/参数化回归/realtime_quotes 解析与港股时间戳归一/调度器幂等）；`test_routes.py` +2（速览端点契约形态 + 刷新冷却）。终验 fast **1131 passed**（1 skipped）+ ruff 绿 + mypy 57 文件 0 错 + 红线 **28/28**。

## [2026-09-21] 021BP 决策闭环项5：扫描加自选后一键衔接批量分析（消除决策链断点）

全市场扫描「加入自选」完成后，原流程断在"请到自选股页手动批量分析"；现弹窗确认后**一键衔接批量分析+评级**——扫描 → 加自选 → 评分拿真评级全程无断点。纯前端改造（复用既有端点，零新表、零后端路由改动、零新依赖）。

- **批量分析核心公共化**（`static/js/watchlist.js`）：`batchAnalyze()` 拆为勾选框收集 + `runBatchAnalysis(ids)`（显式传入 stock_ids 的批量分析核心，t4 全部逻辑原样收口）——自选页勾选入口与市场页衔接入口复用同一链路；分批驱动继续复用 core.js `runChunked`（t4 产出），单批 ≤20（R16 合规），真实进度、跨批合并、失败行透出全部继承。
- **一键衔接**（`static/js/market.js msAddSelected`）：收集每次加自选成功返回的 `stock_id`；加自选成功数 >0 时 `confirm` 提供「立即批量分析」确认项（说明自动拆批顺序执行、耗时预期，期间可正常浏览其他页面——不阻塞）；确认后调 `runBatchAnalysis(addedIds)` 并 `navigateTo('#watchlist')` 切到自选股视图，真实分批进度与结果表在 collectArea 呈现；用户拒绝则保留原指引文案。**失败原因全程透出**（021BO/7ea701b 教训）：加自选网络异常分支由静默计数改为透出 `网络异常：<message>`（去重收集）。
- **验证**：`node --check` market.js/watchlist.js/core.js 全过；pytest fast 942 passed（前端改动无新增后端面，存量后端契约测试全绿）+ ruff 绿 + mypy 55 文件 0 错 + 红线 **28/28**（本次零 Python 改动）。

## [2026-09-21] 021BP 决策闭环项4：自选股 100 只规模适配——批量分析前端自动拆批 + 超时股可见性补齐

**后端采集/分析链路零改动**（不做并发化、不做后端超时增强——SQLite 单写者 + 采集限频约束不变；R19 两个超时锚定值 90/1800 数值未动；R16 `BATCH_OPERATION_LIMIT=20` 未放宽——拆批只发生在前端多次调用，单次 POST 仍 ≤20）。

- **通用分批驱动器**（`static/js/core.js` 新增 `runChunked(items, chunkSize, runChunk, onProgress)`）：自 market.js `msStartSignals` 的 chunk 顺序驱动模式泛化收口到 core.js，批量分析与后续同型需求（t5）复用，不复制两份；顺序执行（单批完成才发下一批），单批异常吞掉继续（与 msStartSignals 同语义）。
- **批量分析改造**（`static/js/watchlist.js batchAnalyze()`）：删除"≤20 弹窗请分批勾选"限制——全选 ≤100 只自动拆 5×20 顺序 POST `/api/batch-analyze`；假进度动画（固定 30% 条 + "逐只执行中"）改为**真实进度**（已完成批数×批大小/总数，进度条 + "已完成 N/总数 只（第 x/y 批执行中）"）；跨批合并 success_count/fail_count/results 后一次性 `renderBatchResults`；整批被拒/网络失败时批内股票显式记为 failed 行（保留"哪些股没跑成"可见性）。
- **§4.4 超时股可见性补齐**（`daily_report._build_markdown_summary`，仅 markdown 内容、零写库路径改动）：失败/超时股此前只有一行计数（名字埋在 POST 响应 JSON），概览区现显式列出失败明细表（股票/代码/失败原因，`<` 转义 `&lt;` 防 marked 吞字）；看板侧展示由 t3 行动清单卡消费 `daily_reports status='failed'` 行，不重复造轮子。
- **测试**：新增 `tests/test_daily_report_summary.py` 3 例（失败股显式列出/`<` 转义/全成功无失败小节）+ `test_routes.py` +1（batch-analyze >20 → 400 的 R16 契约边界锁定——前端拆批依赖该边界）；前端无 JS 测试设施，`node --check core.js/watchlist.js` 语法验证兜底。终验 fast **942 passed** + ruff 绿 + mypy 55 文件 0 错 + 红线 **28/28**。

## [2026-09-21] 021BP 决策闭环项3：总览看板"今日行动清单"——四路只读聚合 + 30 秒决策入口

新增 `GET /api/dashboard/action-list` 只读聚合端点 + 看板"🎯 今日行动清单"卡，聚合四路现成数据，按"今日应做"排序（**评级升降 > 买点共振≥4星 > 预警未读 > 超时缺报股**），用户打开总览看板 30 秒看完"今天该关注什么"。**R9 合规：纯只读聚合，不写 daily_reports/评分/评级/预警任何表，不动任何写入路径。**

- **聚合层**（新模块 `modules/action_list.py`）：`get_action_list()` DB 四路取数 + `build_action_list()` 纯函数装配（单测友好）。路1 daily_reports 最近两期（`ROW_NUMBER() OVER (PARTITION BY stock_id)` 取 rn=1/2，评级跨档对比消费 `alert_engine.RATING_ORDER` 既有顺序表判升降方向，R7 合规不重实现）+ 操盘手摘要（key_factors.trader 预计算零重算）；路2 t2 信号复算（`scan_watchlist_signals`，只认触发日==最新采集日的"今日出现"命中，失败降级为空不阻塞清单）；路3 alert_history 当日未读。**§4.4 可见性缺口补齐**：今日 `status='failed'` 超时股在 `failed_stocks` 显式列出（此前只进 POST 响应 JSON、概览表看不到）；无今日报告计入 `missing_today` 统计。
- **端点**（新蓝图 `blueprints/dashboard.py`，注册进 ALL_BLUEPRINTS）：只读，异常时 500 + 空 items 不阻塞前端其余卡片。
- **前端**（`portfolio.js` 总览看板）：loadDashboard 并行第三请求（失败静默降级）；新"🎯 今日行动清单"卡置于概览卡片之后、操作建议卡之前——统计行（自选/已报/失败/缺报/评级变动/买点信号/未读预警）+ 行动项列表（徽标红升绿降，遵循看板红涨绿跌惯例；点击行 `viewReport` 直达个股报告）+ 缺报提示引导"生成今日报告"。
- **测试**：新增 `tests/test_action_list.py` 11 例（纯函数排序契约/今日门槛/失败股显式列出/trader 摘要透出/无裸 `'<'` 断言/临时库全链：升级+信号+未读预警三类齐出）；`test_routes.py` +1（行动清单端点冒烟）。终验 fast **938 passed** + ruff 绿 + mypy 55 文件 0 错 + 红线 **28/28**。

## [2026-09-21] 021BP 决策闭环项1+项2：自选股每日买点信号巡检 → 智能预警

让自选股每天收盘后自动跑一遍买点信号巡检，命中写预警——用户打开页面即可看到"你的自选股 X 今日出现 XX 买点信号"。全链路零网络（读库+纯函数）、零新表、零新依赖、零红线触碰。

- **项1 信号离线复算**（`modules/market_screener.py`）：新增 `scan_watchlist_signals()` / `compute_watchlist_signal_result()` / `_read_watchlist_klines()`——复用既有信号纯函数（`detect_signals`/`detect_resonances`，零改动）对自选股已采集K线（`raw_kline`/`raw_kline_weekly`，`trade_date→date` 映射）离线复算。日K自读 **250 根**（与评分路径 limit=60 完全解耦；共振③60根/④50根门槛外还留足指标预热长度）；周K全量（周线共振无需补拉腾讯周K）。新增端点 `GET /api/market/scan/watchlist-signals`（`?window=3&stock_ids=`），响应带 `"scope": "watchlist_offline"` 快照参考口径标注；100 只毫秒级/只完成，对比在线拉K（腾讯 0.25s/只）是净收益。
- **项2 买点信号预警**（`modules/alert_engine.py`）：新增规则类型 `tech_signal`（第4类）。检查器 `check_tech_signal` 判定口径："今日出现"才提醒（信号触发日==最新已采集K线日，前日巡检已覆盖的历史命中不计，天然防重复轰炸）；共振组合由窗口内全部命中推导、随当日信号一并列出（星级标注）；规则阈值语义=**共振星级门槛**（选填 3/4/5，留空=任意买点信号都提醒）。写入走既有 `INSERT OR IGNORE + UNIQUE(rule_id, stock_id, trigger_date)` 幂等路径，**调度零新增**——巡检随 `scan_once()` 既有挂载点每日 15:54 收盘批次（`daily_report._run_full_report_flow`）触发，双层异常隔离复用，021BN-c 实例唯一性守卫不受影响。全局默认规则种子（`database/_db/_schema_alerts.py`）按 WHERE NOT EXISTS 幂等模式新增 `('tech_signal', None)`——存量库下次启动自动生效，可在预警规则 UI 停用。
- **白名单五处同步**（缺一即漏）：`alert_engine.VALID_RULE_TYPES` / `_RULE_CHECKERS` / `_format_message`（文案无裸 `'<'`，021BN 教训）+ `blueprints/alerts.py._VALID_ALERT_TYPES` + `static/js/alerts.js` 两个标签 map + `templates/index.html` 规则类型下拉 + `app.css` 徽标配色。
- **测试**：新增 `tests/test_tech_signal_alert.py` 14 例（合成K线形态实机校准：45根深跌+横盘+缺口大阳，交叉确定落在最新一根；覆盖离线复算全链/幂等写入/新鲜度门槛/星级门槛/数据不足静默降级/suspended 不巡检/种子幂等/白名单同步）；`test_routes.py` +2 例（巡检端点冒烟 + tech_signal 规则创建/白名单外 400）。终验 fast **926 passed** + ruff 绿 + mypy 54 文件 0 错 + 红线 **28/28**。

## [2026-09-08] 021BN-c：行业资金流断连自愈——双实例僵尸根治 + 历史接口自动回补

用户报"今天的行业资金流向没有获取到"。排查结论：**东财对本机 IP 动态风控**——实时 clist 接口自 09-07 16:48 起间歇拒绝（RemoteDisconnected），09-08 全天 16:10/16:27/20:45 多轮 12 连击全灭，09-08 快照缺失（09-07 的 200 条是旧口径落库的截断快照）。参数变体/换主机/直连代理实测均被拒，指数 K 线（push2his kline）同期正常，最终 push2his fflow 历史接口也在高频重试后被临时拉黑——封锁是 IP 级且动态扩大的，等待自愈是唯一正解，硬闯会延长封锁。排查中发现并根治三类系统性缺陷：

- **① 双实例僵尸（当日实测根源级缺陷）**：start.bat 与每分钟巡检的 watchdog 在"杀旧进程→绑定端口"间隙撞车，各拉起一份 app.py（15:24/15:57 两次撞车，日志成对、全部采集双倍触发、加剧东财风控）。修复（app.py）：main() 启动先探测 `127.0.0.1:5000/api/health`，已有健康实例立即退出；`app.run` 绑定失败显式 `sys.exit(1)`，输掉绑定竞争的实例不再带全套调度器苟活。已清理双实例，watchdog 复活单实例（15468 持有 5000）。
- **② 断连日永不回补（021BJ 设计缺口）**：`maybe_backfill_gap_async` 只向前找"最新快照日之前"的缺口，实时接口断连的**当日**永远不在回补清单。修复（market_overview.py）：新增 `_compute_gap_dates` 纯函数——向前 10 工作日历史缺口之外，把 latest→raw_kline 最新日之间的工作日一并纳入；实时刷新失败（`refresh_industry_fund_flow` except 路径）即自动触发历史接口后台回补（实测 20:45 已走通触发链）。
- **③ 回补基准传染**：回补行业清单原取"最新一日快照"——最新快照本身截断（09-07 仅 200 条）则回补跟着残缺。改为 `_backfill_base_codes`：近 10 交易日快照 code **并集**（实测基准恢复至 496）。
- **④ 失败可见化**：实时刷新失败写 error_logs（module=`modules.market_overview`），"数据源健康度"卡从此能看到"东财断连中"而非默默缺数据。
- **⑤ 测试定时炸弹**：`test_health_sources.py` 种子用绝对日期（09-01），7 天滚动窗口隔天必腐烂（09-08 晚间 3 例误红）；全部改相对 now 生成。
- 新增 `tests/test_industry_flow_021bnc.py` 8 例（基准并集/缺口检测向前+向后/周末跳过/空输入/健康度可见）。fast **879 passed** + ruff 绿 + mypy 53 文件 0 错 + 红线 28/28。
- 运维提示：风控期间勿反复手动点"刷新"（一次 = 3 主机×2 通道×2 次 = 12 连击）。明日起两路自动恢复任一通即可自愈：clist 恢复→直接落库；clist 仍封而 push2his 恢复→失败自动转历史接口回补 09-08/09-09。

## [2026-09-09] 银河证券佣金口径校准：万1.8 → 万1.853（用户交割单实测）

- config.TRADE_FEE_BROKERS 银河档 commission_rate 0.00018 → 0.0001853（最低5元不变）；测试断言同步
- 数据重算：银河账户全部估算佣金流水（commission_estimated=1）按新费率重估——3笔均触发最低5元兜底、数值不变但口径已校准；全账户持仓 _recalculate_holding 重算
- 重算口径为原始流水成本（非人工修正）→ 6 笔持仓 is_cost_adjusted 清零，前端不再显示"已修正"标签；历史 cost_adjustments 审计记录保留
- 终验 fast 879 绿 + ruff + mypy 0 + 红线 28/28

## [2026-09-08] 个股"当前股价"错误根治：盘中实时快照污染当日K线 + 收盘后无法回填

用户实测：东山精密、中国中免报告"当前股价都不对"，且非个例。排查发现三层数据时效缺陷：

- **① 盘中实时快照污染当日K线（根因）**：020R-59 盘中刷新机制用腾讯实时接口写当日行（close=采集时刻盘中价），但 `data_source` 写 NULL，与历史行无法区分；收盘后 `fetch_kline` 命中"同日跳过"（last_date >= 今日且非盘中时段），**真实收盘 K 线永远无法回填**——实测东山精密当日行锁死在盘中价 192.48(+1.01%)，真实收盘 186.42(-2.17%)，偏差 3.2%，且周线聚合连带污染。
- **修复**：①盘中快照行打标记 `data_source='tencent_intraday'`；②收盘时段检测到当日行是该标记 → 不再跳过，正常历史采集回填真实收盘（INSERT OR REPLACE 覆盖）；盘中行为不变。回归测试 +1（残留回填/正常跳过/盘中标记 17 例全绿）。
- **② skip_collect 重评的数据时效盲区**：当日多次 force+skip_collect 调试重评跳过采集，22 只日线停在 09-07 而报告照常生成（data_warnings 有"滞后1天"提示但埋在完整度列表里不醒目）。本轮以全库带采集重评恢复数据。
- **③ market_snapshot 语义澄清**：该表是选股扫描器的整表快照（每次扫描 DELETE+INSERT），并非实时行情——不主动扫描就过期，其余场景不得当行情源消费。
- **数据修复**：全库 46 只 `force_full` 重采（覆盖残留 + 保证复权口径一致）→ 周月聚合重跑 → force 重评（fail=0）；东山/中免当日收盘与实时源逐只核验一致（186.42/-2.17%、54.59/-0.44%）。

## [2026-09-08] 趋势罗盘显示截断修复：后端文案 "<" 被 innerHTML 吞掉的系统性根治

用户实测：中免报告页罗盘月线理由显示到"MACD 双线死叉（DIF"半截——`（DIF<DEA）` 的 `<DEA）` 被前端 innerHTML 当 HTML 标签吞掉（与此前月线空头惩罚说明同病根）。全项目扫描 modules/blueprints 所有含 `<` 且会被前端渲染的文案，一次根治：

- **趋势罗盘**：`trend_analyzer.py` 4 处信号文案 `（DIF<DEA）/（DIF<0）/（DIF>DEA）/（DIF>0）` → 文字描述"高于/低于"；`analysis.js` 罗盘渲染加 `_tcEsc` 转义防守（理由/共振标签全走转义）。
- **报告页关键因子**：`advisor.py::_build_kline_factors`（B20 独立因子构建器，非 generate_advice 本体）`ma_trend` 空头文案 `MA5 < MA20` → "低于"，均线趋势行不再吞字。
- **选股扫描器**：`market_screener.py` KDJ低位金叉 note `D<25` → "D低于25"（扫描结果列表直接渲染 note）。
- **评分明细（防御性）**：`scoring_engine.py` cross/macd 两处 detail 文案去 `<`（当前前端未渲染该键，防未来透出）。
- **回测中心 Markdown**：marked.parse 渲染下 `（<30）` 同样被吞——`backtest_engine.py` 2 处、`price_backtest.py` 3 处样本量提示改"不足30/不足20"。
- 剩余 `<` 仅存在于日志/控制台/注释（不进 HTML 渲染）与代码比较运算，无风险。终验 fast 870 绿 + ruff + mypy 0 + 红线 28/28；重启后实测中免趋势 API 文案完整（"MACD 双线死叉（DIF 低于 DEA）"）。

## [2026-09-07] 第二轮批判性审查：算法口径统一（RSI/KDJ）+ 每日自动备份

用户要求再批判性检查一轮，复查未审区域后修复 4 处问题（3 处算法/口径 + 1 处运维缺口）：

- **① 展示层 RSI 序列尾部失真（算法 bug）**：`technical_detail._rsi` 旧"简化版"只用前 14 个数据点初始化后不再递推——**160 根周线序列尾部全部弃用**，实测中免周线 RSI 展示 32.6 vs 评分口径 39.1（日线同样分叉）。改为与 `data_adapter._calc_rsi` 同款 Wilder 全递推（同花顺/通达信口径），展示读数与评分输入一致。**评分不受影响**（评分一直用 Wilder 值），纯展示修正。
- **② 评分输入 KDJ 非标准实现（算法 bug，影响评分）**：`data_adapter._calc_kdj` 旧实现只取最近 9 根 RSV、K 前值硬编码 50 算一步——无动量记忆的"一步 KDJ"，与 `technical_detail._kdj` 全递推实现及行情软件口径三方分叉。改为标准全序列递推（RSV 逐根滚动窗口 + K 初值 50 平滑），评分输入与展示读数统一。**此修复改变全库评分的 KDJ 分量**，已 force 重评（fail=0）；中免总分 55.0→54.9（技术面微降，方向幅度健康），评级分布无漂移（减仓 21/观望 20/买入 3）。
- **③ 每日自动数据库备份（运维缺口）**：此前全库仅有人工备份，服务常驻期间磁盘损坏/误删将丢失全部数据。新增 `db_manager.auto_backup_db()`（sqlite3 backup API，WAL 模式安全；幂等：当日已有 `db_backup_YYYYMMDD_auto.db` 则跳过），双挂载：app 启动时 + 补采调度 tick（每 30 分钟检查，保证常驻期间每日必有）。命名沿用 `db_backup_*` 前缀，自动纳入 `scripts/cleanup_backups.py` 既有保留策略。已验证随启动生成。
- **④ 测试与配套**：`mock_data_provider` 补齐 `main_net_inflow_5day`/`roe_annualized`（端到端测试与生产评分路径一致）；config.py 过时权重注释更新（0.1504→0.08）；顺带确认日志轮转（TimedRotatingFileHandler 保留 7 份）与 `_get_prev_score`（按 report_date 跨日取值，同日多次重评不污染 score_change）均无问题。
- 测试 +6（RSI 一致性/KDJ 递推与记忆性 3 + 自动备份 3）；端到端测试暴露并修正一处断言边界瑕疵（浮点分数 64.9 与整数档位上界 64 的表示缝隙，`_map_rating` 本身正确）。终验 fast 858 绿 + ruff 绿 + mypy 0 + 红线 28/28。

## [2026-09-07] 权重批判性审查落地：主力资金降噪 + 档位零点对称化（三路线之 1+2）

权重审查实测：当日主力净流入 ±1000万 单日噪声 → 全库中位 6.4 分摆动、14/44 评级翻档（迟滞 ±3 无法覆盖）；中免 ±3212万 摆动 12.1 分直接翻档；"估值"全局有效权重 5.4% vs "当日主力净流入"单子项 27%。经用户批准按建议路线执行：

- **① 数据侧降噪**：契约新增派生字段 `main_net_inflow_5day`（最近 5 个交易日主力净流入均值，与 advisor.main_avg_5d 同口径；不入完整度集合、不触发降级）；`score_main_capital` 信号改为 **0.5×当日 + 0.5×5日均**（单方缺失回退另一方，双方缺失仍中性 50）。
- **② 档位零点对称化**：95/87/85/60/42/20 → **92/70/50/30/12**——±1000万 回归中性 50（旧口径零点两侧 85/60 是 B18-Hotfix"激进校准"遗留的系统性偏多偏差，隐含中性点 72）。
- **③ 权重数字与校准锁定区零改动**：维度 43.14%、子项主力 0.50、其余 002 档位、评级边界全部不动——等 dynamic_optimizer 300 纯净样本日后由优化器裁决（已 RED_LINES §6 登记）。
- 中免修复后：当日 -3212 万/5 日均 +7393 万 → 融合 +2090 万 → "温和净流入"70 分（旧口径当日单点 42 分），资金面 41→约 55；前端零改动（当日/5 日均本就双行展示，融合说明透出在子项明细）。
- 测试 +7（主力融合/回退/对称档位 5 + 5 日均口径 3 - 旧参数化调整）；已 RED_LINES §6 豁免登记。

## [2026-09-07] 批判性检查：四维评分读数与口径五连修（中国中免实测触发）

用户要求以批判眼光复查中免四维评分报告，逐项对账后发现 5 处问题并修复：

- **① 月线空头惩罚说明被浏览器吞掉（HTML 转义 bug，最严重）**：惩罚文案含 `MA5<MA10`，前端 innerHTML 拼接时 `<MA10)` 被当作 HTML 标签吞掉，用户只看到"⚠ 月线空头(MA5"半截——**技术面子项加权 47.7 与维度分 41 之间差了 ×0.85 惩罚却完全无从知晓**。修：三处后端文案去 `<`（scoring_engine/analysis 蓝图/technical_backtest）+ 前端渲染加转义防守。
- **② 均线"纠缠"误标**：原逻辑只有严格单调才算多头/空头排列，否则一律"纠缠"——中免月线 MA5=55.42 vs MA10=67.54（差 18%，明确空头）被标"纠缠"。修：加黏合判定（三线各差 ≤2% 才算纠缠，否则按 MA5 vs MA10 定向）。
- **③ 周/月量能结构性"明显缩量"**：当根是进行中的周/月（周一只有 1 天量），与含当根的完整周期均量直接相比必然偏低（中免周线 0.03、月线 0.19）。修：分母剔除当根（改为前 20 个完整周期均量），前端文案改"当期累计量"。
- **④ ROE 未年化（系统性口径缺陷）**：报告期累计 ROE 直接对比年度档位阈值，中报/一季报公司被整体低估（中免中报 5.46% 落"偏低"62 分，年化 10.92% 才是可比口径→"一般"80 分）。修：契约新增 `roe_annualized`（一季×4/中报×2/三季×4÷3/年报不折），data_adapter 计算，盈利能力子项优先采用并标注"(年化)"。
- **⑤ MACD/情绪标签语境**：零轴下"多头"补"反弹"注记、零轴上"空头"补"回落"注记（与趋势罗盘同口径）；情绪"+0.34 显著正面只得 56 分"的观感矛盾补"已定价噪音折价"说明（021BC 非对称映射是有意设计）。
- 对账核实无问题的部分：四维加权算术（40.5/68.2/41.0/49.8 × 用户配置权重 27.15/21.71/43.14/8 = 47.5 ✓）、机构持仓缺失"权重归零型"降级 ✓、资金面子项权重再归一 ✓。
- **明确不改**（口径已知但属校准敏感区，留待用户决策）：主力资金子项只按当日净流入计分（5 日均 +7393 万净流入仅展示）；融资余额档位用绝对额（未按流通市值/余额基数归一）；周线 RSI 读数 32.6 与评分明细 39.1 的口径分叉（technical_detail 与 data_adapter 各自计算，均在同一档位不影响分数）。
- 中免修复后：基本面 65.5→68.2，总分 46.9→47.5，评级"建议减仓"不变。测试 +15（技术读数 7 + ROE 年化 9 - 重叠），终验 fast 832 绿 + ruff 绿 + mypy 0 + 红线 28/28；已 force 重评全库（fail=0）。

## [2026-09-07] 修复：周/月线表日频污染（趋势罗盘"月线上涨"错判根因）

用户实测中国中免反馈趋势罗盘不对（"长期月线上涨·强"与 K 线直觉相反）。排查发现**根因是数据层而非判定层**：

- **根因**：020R-48 初版（08-16，与修复版相隔 23 分钟）曾把日频行写入 `raw_kline_weekly`/`raw_kline_monthly`；修复版 `INSERT OR REPLACE` 只覆盖同名（周末日）行，**非周末的日频行永久滞留**（08-14~09-07 约 17 个交易日）。后果：所谓"月线"实际是近两三周日线、"周线"是近期日线——趋势罗盘的月/周判定与评分引擎的月线方向/周线波段子项全部被扭曲。
- **修复 1（采集器）**：`aggregate_period_klines` 改为**先删后插整表重建**（原行级 REPLACE 语义），任何历史污染在每次采集时自动清除；全库 44 只已重建（周表 4,751 行/月表 1,162 行，均恢复纯周/月频；重建前备份 `db_backup_20260907_213059_period_rebuild_before.db`）。
- **修复 2（趋势罗盘强度诚实化）**：MACD 动能未过零轴时趋势未获动能确认——上涨遇 DIF<0（或下跌遇 DIF>0）强度上限"中"，理由白话标注"属反弹修复阶段，尚非强势上涨"。
- **修复后中免实测**：短期上涨·中（反弹修复）/ 中期震荡·弱 / **长期月线下跌·强**（真实月线 2 月 80→8 月 52），综合震荡·弱——与 K 线直觉一致。
- 测试 +4（聚合器重建清污/幂等 ×2、强度门槛 ×2）；终验 fast 814 绿 + ruff 绿 + mypy 0 + 红线 28/28。评分引擎技术面子项将在下次批量分析时用上正确周/月数据。

## [2026-09-07] 扫描器共振重设计：4 组买点共振，删除死叉/超买/超卖类信号

按用户提供的"共振组合"参考表重构第②步技术信号精筛：

- **删除（判定：对选股器没用）**：`macd_dead` 死叉、`kdj_overbought`/`rsi_overbought` 超买、`kdj_oversold`/`rsi_oversold` 纯超卖状态、`res_bear_confirm` 空头共振预警、`res_oversold_watch` 超卖观察池。理由：选股扫描器只产买入候选——死叉股本就不会出现在金叉结果里；纯超卖=接飞刀半成品；对不持仓的股票报卖出/过热是纯噪声（持仓风险由预警系统负责）。超卖语义由 KDJ低位金叉（D<25）与共振环境注记（RSI超卖环境）承接。
- **新共振库（4 组，买点型取最高档）**：①双金叉共振（MACD+KDJ 同日 ⭐4/同窗 ⭐3）②周线共振波段 ⭐5（周线MACD多头+日线金叉；对触发股按需补拉腾讯周K，新增 `fetch_kline_weekly`）③底部反转共振 ⭐5（底背离：价格创60日新低而DIF未新低 + KDJ低位金叉 + 放量阳线）④零轴上二次金叉 ⭐5（近15日MACD第2次金叉且DIF>0 + KDJ中位D40~70金叉）。信号库收缩为 4 个触发类金叉信号。
- **参考表未落地项**：「日线金叉+60分钟金叉」因 60 分钟K线未采集且全市场扫描噪声大，跨周期位由周线共振承接；「顶背离+高位死叉」为卖出组合，不适合选股器。
- 测试 26 例（删 6 个反向/状态类用例，新增周线共振/底背离/二次金叉/同日升档用例，形态经实证调参）；终验 fast 810 绿 + ruff 绿 + mypy 0。真实链路实测：五粮液触发「周线共振波段 ⭐5」，周K补拉正常。

## [2026-09-07] 趋势罗盘：个股日/周/月三周期趋势判定

新增「🧭 趋势罗盘」——回答"这只股票现在是上涨、下跌还是震荡"，分三个周期看：

- **判定**：短期=日线（MA5/MA20）、中期=周线（10周/20周线）、长期=月线（5月/10月线）。每周期 5 个信号加权打分（均线排列±2、现价 vs 慢线±1、MACD 金叉态±1、DIF 零轴±0.5、现价 vs 快线±0.5），≥+2.5 上涨 / ≤-2.5 下跌 / 其间震荡；|分|≥4 强、≥3 中。数据不足的周期显示灰色"数据不足"，不影响其他周期。
- **综合**：月2:周1.5:日1 加权汇总（长期定方向）；三周期同向时给"⚡三周期共振"高亮。A股色惯例：红涨、绿跌、黄震荡。
- **落点**：`modules/trend_analyzer.py`（只读纯函数）+ `GET /api/stocks/<id>/trend`（analysis 蓝图）+ 个股分析报告页趋势罗盘卡片（四维评分详情之后，异步填充不阻塞报告）。复用 020R-48 周线/月线指标，不新增采集。
- **测试**：`tests/test_trend_analyzer.py` 10 例（三分类边界/置信度/na 隔离/共振/加权综合/端点 200 与 404）。终验：fast 813 passed + ruff 绿 + mypy 0（53 文件）。真实库实测：美团三周期共振下跌·强，混合形态个股日线跌/周线跌中/月线震荡分层正确。

## [2026-09-07] 遗留三事清零：级联缺口修复 + mypy 归零 + 备份治理

用户指示将 OPT 批次登记的三项遗留当日清零：

- **OPT-6-G1 级联缺口修复**：`api_delete_stock` 的 `child_tables` 补齐至全部 29 张含 `stock_id` 子表，并新增安全守卫——任一账户**在仓**（quantity>0）时删除自选股返回 409（需先经持仓页删仓）；**已清算**则连流水/成本修正一并出清。测试：`test_cascade_integrity.py` 白名单收缩为空集 + 新增守卫 2 例（40/1 skipped）。**存量孤儿 1,137 行一次性清零**（15 表 + VACUUM；改动前备份 `db_backup_20260907_142849_opt6g1_cascade_fix_before.db`）。
- **mypy 51→0**（52 文件 Success，零 ignore 豁免）：scoring_engine `available` 字典收窄局部变量（纯类型收窄语义不变）；`_market_to_contract` 返回 `Literal['A','HK']`；**`industry` 转正为 StockData 契约字段**（可选默认 None，原为 `extra='allow'` 动态属性）；implicit-Optional 2 处、mock 边界分支 isinstance 断言、模块缓存注解 4 处、移除失效 `type: ignore` 1 处。
- **backups/ 治理**：`parent_git_backup_20260813.bak/`（490 散文件/12.8MB，拆库前唯一历史副本）转为标准 **git bundle** 单文件 `git_history_parent_20260813.bundle`（11.2MB，22 提交完整历史，克隆演练通过），散件目录删除；backups/ 本就在 gitignore 内，仓库零影响。

终验：fast **803 passed**/1 skipped + ruff 全仓绿 + 红线 28/28 + **mypy 0**；服务重启生效（看门狗自愈）。

## [2026-09-07] 技术债与风险治理批次：OPT-1~OPT-8 全部落地

一次性执行任务书《优化任务_技术债与风险治理_20260907》全部 8 项，每项独立 commit、验收基线（fast pytest + ruff + mypy + check_redlines）全程保持绿：

- **OPT-1（P0）临时产物清理**：根目录与 scripts/ 的临时脚本归档/删除；lint 债务清零（ruff 19 处违规机械修复）。
- **OPT-2（P0）备份保留策略**：`cleanup_backups.py` 增加保留窗口与数量上限，`_prune_old_backups` 防无限增长；排除 2026-08-13 一次性父项目 .git 备份（处置权留用户）。
- **OPT-5（P1）测试分层**：pyproject 默认 `-m "not slow" --timeout=60`，默认层 **14.8s**（基线 431s，提速 29 倍，759 passed）；全量 `-m "slow or not slow"` 保留 slow 层（TickBackoff 真实时钟退避 4 例为主因）；pytest-timeout 走 R17 白名单 + RED_LINES §6 豁免。
- **OPT-7（P2）前端 CDN 本地化**：echarts/marked 落地 `static/vendor/`（SHA256 锁版本），`app.py` 按各自 mtime 独立版本号；断网可用。
- **OPT-3（P1）data_collector 拆分**：6,055 行巨石按数据源拆为 **modules/collector/ 18 子模块**（AST 纯搬移），`data_collector.py` 保留为 facade（192 符号表面逐符号一致，调用方零改动）；`_rotate_em_host`/`_call_ak_with_timeout`/`_num_float` 随调用方迁移断 3 处循环导入；共享可变状态与唯一 global 写入者同模块单宿；check_redlines 10 处源码锚点改扫 facade+全包（28/28 绿）；4 个测试文件约 70 处 monkeypatch 由 facade 重指向实现子模块；mypy 51→50（唯一减少来自死定义删除）。已登记偏差：capital_flow.py 1,154 行（`fetch_capital_flow` 单函数约 700 行不可拆）。
- **OPT-4（P1）前端拆分**：app.js（7,094 行/415KB）按业务域拆为 **8 文件** core/watchlist/analysis/portfolio/backtest/alerts/market/boot（区间平铺校验无缝全覆盖）；顶层执行语句依赖分析确认仅 3 处立即求值引用，加载顺序 core→…→boot 即满足；index.html 按序渲染 8 个 `<script>`，`_ver()` 扩展为 `js_versions` 有序 dict（每文件独立 mtime 版本号，零构建不变）；node --check 8/8 + 离线冒烟 + **真实浏览器回归**（无头 Chrome 实测 UI 完整渲染、window.onload 初始化链路正常）。前端文件结构变更后需服务重启生效（看门狗自愈）。
- **OPT-6（P2）级联完整性测试**：新增 `tests/test_cascade_integrity.py` 37 例——29 张 stock_id 子表清单驱动，`child_tables` 与清单双向一致性断言（**新表不同步级联即红**）；删自选股残留参数化、删分组迁移、删账户 force_confirm 流、删持仓/流水安全锁（T+1/已清算/大额）与重算一致性，含 AGENTS §3 同股多仓断言。**测出真实缺口 OPT-6-G1**：`api_delete_stock` 仅覆盖 10/29 子表，19 表残留孤儿行——已登记任务书"发现的问题"另行立项（本批次零实现改动）。
- **OPT-8（P3）数据源健康度**：新增 `GET /api/health/sources`（只读）——近 7 天按维度聚合 data_status（成功率/最后成功/领先连续失败段）、按模块聚合 error_logs；红/黄/绿分级（红=成功率<50% 或 连败≥3 或模块 24h 内有错）。看板新增 🩺 健康度卡片（异步填充不阻塞主渲染）。真实库实测即时暴露 express/forecast/orderbook/restricted_release 四维 6 连败；真实浏览器 DOM 验证卡片完整渲染。新增 `tests/test_health_sources.py` 5 例。

**终态**：fast 796 passed / 6 deselected（~23s）；全量 765+（6m18s，含 slow）；ruff 全仓绿；check_redlines 28/28；mypy 51→50（既有债务登记待专项）。服务经看门狗两度自愈加载新代码，线上 UI 实测正常。

## [2026-09-03] 看板建议卡补齐买入区间/补仓档位（021BM）

- 用户诉求：持仓股操作建议显示"买入"却看不到买入区间。
- **根因**：操作建议卡片是"评级×持仓状态"静态矩阵，不接价格建议数据；价格建议只在个股页与每日报告里有。
- **方案（零重算）**：每日报告本就落库完整 `price_advice` JSON（区间+网格+止损止盈）——`watchlist-scores` 端点直接从**最新报告行**读出（新增 `lr.price_advice` 列 + `_parse_pa_zone` 提取器），每股附 `pa_zone`（区间标签/上下沿 + 买入侧前两档网格 + 止损）。**语义自动区分**：无持仓档位为"第一/第二买入位"，有持仓即"补仓一档/补仓二档"（10%/15%，锚定止损+0.5ATR）；减仓/离场档被过滤不进看板。
- **前端**：买入/加仓动作的胶囊卡追加蓝色区间标签（`买入区间 10.00~12.50 · 第一买入位 10.00`），悬停显示全部档位+止损+报告日期，点击进个股报告。
- **验证**：`tests/test_pa_zone_021bm.py` 4 例（空仓档/持仓补仓档/纯离场档过滤/坏输入）passed；线上实测 32/39 只自选带区间。
- 改动：`blueprints/portfolio.py` · `static/js/app.js` · `tests/test_pa_zone_021bm.py`（新）。

## [2026-09-03] 盈亏口径对齐券商：持仓盈亏 + 摊薄成本（021BL）

- 用户诉求：已实现/浮动/总盈亏三列用得不顺，券商 APP 只看**持仓盈亏**；成本应是**每笔交易后重算的摊薄成本**，不是停留在买入价。
- **发现的真实偏差**（重算引擎 `_recalculate_holding`）：①买入/卖出按 `价格×数量` 计算，**金额直填（021AM）的实际成交金额被无视**→成本价与券商对不上；②清仓后成本价残留旧值不归零；③展示层"已实现/浮动/总收益"三列并排与券商习惯冲突。
- **重算引擎修正**（保持 021S（股票, 账户）隔离口径不变）：买入/卖出**优先实际成交金额**（缺失回退 价格×数量，旧数据兼容）；卖出仍不改摊薄成本价（东财/银河口径）；已实现盈亏 = 卖出金额 − 费用 − 卖出数量×摊薄成本（超卖钳制按比例折算）；**清仓后成本价归零**。
- **展示重构**（券商列表口径）：持仓表三列 → **持仓盈亏 + 盈亏比例**（现价 vs 摊薄成本，API 新增 `unrealized_pnl_pct`）；已实现盈亏移至持仓盈亏单元格悬停提示 + 汇总卡；汇总卡"总浮动盈亏/总收益"→"**总持仓盈亏**/（含分红与卖出差额）"；看板"浮动盈亏"卡同步改名。
- **验证**：新增 `tests/test_holding_recalc_021bl.py` 3 例（隔离库 API 全流程：买入→部分卖→补仓→清仓，断言摊薄成本 10.005→9.5075、已实现 992.5→980.0、清仓归零；金额直填优先；金额缺失回退）；与 021BK 费用估算 14 例合计 **17 passed**；JS 语法校验通过。
- 改动：`blueprints/portfolio.py`（重算引擎 + 盈亏比例字段）· `static/js/app.js`（持仓表/汇总/看板）· `templates/index.html`（汇总卡标签）· `tests/test_holding_recalc_021bl.py`（新）。

## [2026-09-03] 交易费用自动估算（021BK）

- 用户诉求：交割单隔天才出，佣金没法实时录入——希望系统先自动算，有误差隔天再调。
- **现状**：流水表已有 `commission` 字段（021X：买入计入成本、卖出扣减已实现盈亏），但 44 笔流水中 36 笔为 0；编辑/重算通道现成，T+1 锁恰好允许"次日改实际值"。
- **费率模型**（`config.py` 可随时改一处）：A股 = 佣金（按券商，最低 5 元）+ 印花税 0.05%（卖出单边）+ 过户费 0.001%（双边）；港股简化 = 佣金 + 印花税 0.1%（双边）+ 杂费 0.01%。
- **券商匹配**：用户双券商——银河证券万 1.8 最低 5 元、东方财富万 1.5 最低 5 元。`modules/trade_fees.py` 按**交易账户名关键词**匹配券商档（银河→1.8‱、东财→1.5‱、未命中→默认 1.5‱），纯函数离线可测。
- **行为**：录入流水佣金**留空 → 自动估算入库**并标记 `commission_estimated=1`（列表显示 `≈5.10`，悬停有说明）；明确填写则完全尊重；隔天编辑为实际值 → 标记清除、持仓自动重算（T+1 锁恰好允许次日修改，用户确认历史流水**不**补估）。
- **验证**：单元 11 例（券商匹配/最低佣金/印花税仅卖/过户费/港股/分红零费）+ API 集成 3 例（隔离临时库走 test client：留空自动估算入库带标记、明确值尊重、编辑清标记）= **14 passed**。T+1 锁为红线无旁路，故当日 API 增删验证改由隔离库承担。
- 改动：`config.py` · `modules/trade_fees.py`（新）· `database/db_manager.py`（迁移列）· `blueprints/portfolio.py` · `static/js/app.js` · `templates/index.html` · `tests/test_trade_fees_021bk.py`（新）。

## [2026-09-03] 行业资金流缺口回补（021BJ）

- 用户报告：昨天（09-02）行业资金流向没有数据。根因：09-02 东财断连日未采集到该日快照，而实时接口"错过即丢失"；库里日期序列为 `09-01 → [缺 09-02] → 09-03`（09-03 东财已恢复，刷新正常拿到 496 个行业）。
- **修复（缺口回补机制）**：`modules/market_overview.py` 新增东财 push2his **历史日K资金流接口**接入（`/api/qt/stock/fflow/daykline/get`，按行业 BK 代码逐个回看每日主力净流入）。`backfill_industry_fund_flow(trade_date)`：
  - 复用请求层的**主机轮换 × 直连/代理 × 重试**策略；
  - **探针先行**：第 1 个行业历史中无该日（休市/节假日）直接跳过全部，避免 496 次空跑；
  - **连续失败预算**：连续 5 个行业失败即中止（东财整体不可达时快速放弃）；
  - 字段映射：历史行 f52主力/f53小单/f54中单/f55大单/f56超大单/f57主力占比/f63涨跌幅；领涨股历史接口无此字段，回补行如实置空。
- **自动化**：刷新端点成功后调 `maybe_backfill_gap_async()`——从最新交易日向前扫描近 10 个日历日的**连续工作日缺口**（支持多日断连后逐日回补），有缺口则**后台 daemon 线程**回补（幂等锁防重复，不阻塞响应，休市由探针自愈）。
- **实施结果（当日实测）**：09-02 回补落库 **450/496 个行业**（91%，46 个行业重试穷尽后如实放弃）；三个工程强化：①**成功主机粘性**（探针实证 push2his 当日丢包约 2/3，成功组合优先重试，速度约 3 倍）；②**分块落库**（每 100 条提交，中止最多损失当前块）；③首次运行发现"冷却回放被误当成功、钩子未触发"，确认为正常设计后用真实刷新重触发。
- 解析器与工作日推算做成纯函数，离线测试 7 例（`tests/test_market_overview_021bj.py`）。
- 已知边界：回补行缺领涨股；回补依赖东财历史接口可用性（与实时接口同源，东财整体不可达时冷却后重试）。

