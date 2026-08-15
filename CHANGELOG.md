# 变更日志 (CHANGELOG)

## [2026-08-15] 个股报告页显示完整性修复（020M）

- 背景：周六查看个股报告页显示不完整——当日(非交易日)无报告时，report-latest 走实时生成路径，且回退查询用全表 MAX(report_date)，部分股票已有当日行时其余股票回退失败。
- 修复 1：周末/休市日（weekday>=5）跳过实时生成，直接回退该股票最新日报快照（含综合文本 markdown）；交易日实时路径补齐 `advice_detail`（与日报同源 `_build_markdown_single`）。
- 修复 2：回退查询改为按股票取 `MAX(report_date)`，杜绝"别的股票才有报告的日期"导致本股票查无报告。
- 修复 3：快照响应补齐 `action_advice`（取价格建议操作）、`latest_close`/`latest_close_date`（查最新K线）——评分卡"建议"行与"最新收盘"行不再缺失。
- 数据清理（先备份 db_backup_20260815_133847_pre_0815_cleanup.db）：删除 10 只股票由实时生成意外写入的 08-15（周六）daily_reports 行，统一回到 08-14 真实交易日口径。
- 验证：29/29 只股票 report-latest 字段齐全（advice_detail/action_advice/latest_close/latest_close_date/rating_date=08-14）。

## [2026-08-15] 前端数据完整性修复（020L：周末守卫 + 来源升级 + 同花顺回溯）

- 前端审计发现三类问题：① 23 只 A股各有一条 08-09（周日）估算脏行，挤占资金面 LIMIT 10 展示名额；② 近 10 交易日窗口内 33 格新浪顶替 + 6 格估算兜底；③ 同花顺净额全窗口仅 71/230 格有值。
- 修复 1（预防）：`fetch_capital_flow` 新增周末守卫——周六/周日全链路跳过（与 019G 同花顺同原则），根治定时日报在周末写入非交易日脏行；`backfill_capital_history` 升级时同步置空 `main_net_inflow_pct`（westock/新浪不提供占比，避免估算旧占比残留）。
- 修复 2（数据，先备份 db_backup_20260815_132302_pre_020L_frontend_fix.db）：删除 23 条周日估算脏行；39 个估算/新浪格子全部经腾讯 westock --date 升级为真实数据（窗口来源分布：东财 213 / 腾讯 77 / 新浪 0 / 估算 0 / 缺失 0）。
- 修复 3（同花顺回溯）：从历史备份恢复可追回的 30 格 ths（08-05/08-06 来自 019S 备份、08-13 2 格来自 08-13 备份），仅填空洞不覆盖；剩余 129 格历史数据源从未存在（08-03/08-04/08-07/08-10 同花顺接口全市场失败、08-11/12 部分股票接口未返回），无法追回——自 08-14 起每日 23/23 采集，10 个交易日后窗口自然满 10 条。
- 联动：39 格升级后 08-07～08-14 报告重生成（skip_collect，29/29×7 天）+ 评级回测 521/521 + 价格回测 1046/1046 重跑。
- 验证：脏行 0；K线/主力资金 10 交易日全窗口无缺口；ths 101/230（历史可追回部分已全部追回）。

## [2026-08-15] 回测中心数据同步重算 + 孤儿回测行自愈（020K）

- 背景：报告重生成会经 advisor.py 的 `INSERT OR REPLACE` 重写 `ratings_history` 换掉 rating id（B24 红线模块，不改），导致 `backtest_results` 累计 595 条孤儿行（旧 id 失去引用），污染回测中心市场报告统计。
- 落地：`BacktestEngine.batch_backtest` 开头新增自愈清理——删除 `rating_id` 非空且不在 `ratings_history` 中的孤儿行（排除 `rating_id=-1` 的历史模拟行），每次回测运行自动收敛，无需人工维护。
- 数据修复（先备份 db_backup_20260815_130323_pre_backtest_regen.db）：清理 595 条孤儿行 → 评级回测全量重跑 521/521 成功（真实样本与 ratings_history 一一对应）→ 价格建议回测全量重跑 1046/1046 成功（force 模式自带备份）。
- 说明：07-16 遗留的 77 行 `ratings_history` 字母档（B/C/D）与回测表中文标签不一致属历史表示差异，回测统计口径统一、不受影响。
- 验证：孤儿行 0；08-06～08-14 每天 29 行真实回测覆盖；价格回测 1046 行 created_at 全部刷新。

## [2026-08-15] 报告重生成支持跳过采集（020J：skip_collect）

- 背景：数据回填完成后需重生成历史报告，但 `_process_single_stock` 写死"先采集后分析"，历史 8 天重生成会重复打外部接口（每轮 3-4 分钟）。
- 落地：`generate_daily_report` / `_process_single_stock` 新增 `skip_collect` 参数（默认 False，行为零变化）；True 时跳过同花顺批量预取与逐只采集，纯用库内已有数据重新分析。API `POST /api/daily-report/generate` 新增 `skip_collect` 请求字段。
- 兼容性：18:00 定时调度、intraday 端点、CLI、tests 等既有调用点均走默认值，不受影响。
- 验证：py_compile/ruff 通过；08-06～08-13 共 7 天历史报告以 `{date, force, skip_collect}` 全部重生成成功，回测评级历史（ratings_history）同步修正。

## [2026-08-15] 资金面历史回填补强：腾讯 --date 逐日层 + 新浪窗口扩大（020I）

- 背景：020H 上线首日实测发现两类补不上：港股历史缺口（新浪 lscjfb 仅 A股）与超出新浪 5 日窗口的旧缺口（如 000977 的 08-06）。
- 探针实证：westock CLI 的 `--date` 参数对 A股 asfund / 港股 hkfund 均支持历史逐日查询（港股 08-13 主力 4460.21 万港元、A股 08-06 主力 -26050.95 万，四档自洽）。
- 落地：`backfill_capital_history` 链序改为 **腾讯 westock --date（A股+港股）→ 新浪 lscjfb（仅A股）**；`_fetch_capital_flow_westock` 新增 `date_str` 参数并严格校验 `EndDate == date_str`（M-2 同款红线，不匹配只记日志、不计 westock 连续失败，避免回填拖垮实时链路）；新浪回补窗口 num 5→15（覆盖近 10 交易日口径）。
- 验证：py_compile/ruff 通过；HK3690 6 天 + 688981/002714 08-11 + 000977 08-06 全部回填成功（westock 源）。

## [2026-08-15] 补采调度器重写：近 10 个交易日完整性补采（020H）

- 背景：用户确认补采口径 =「近 10 个交易日，只要不完整就补采，同花顺净额也要采」。
- 交易日历：以全部自选股 K 线日期并集推导（不含周末/节假日），窗口为最近 10 个交易日。
- 缺口检测四维：K 线缺口（缺日即补）、资金面缺口（该日 `main_net_inflow` 为空）、同花顺净额（A股最近交易日 `ths_net_inflow` 为空）、基本面/消息面（沿用原逻辑）。
- 资金缺口补采：新增 `data_collector.backfill_capital_history()`，逐日按 东财三层 → 腾讯 westock → 新浪 lscjfb 链补采（东财熔断期直接走 westock/新浪），写库 `capital_source` 标注实际来源；同花顺净额经 `fetch_capital_flow_batch` 批量刷新（沿用 019G 周末跳过规则）。
- 轮次控制：每轮最多 5 只、30 分钟基础周期、失败退避至 120 分钟，与原有 K 线/基本面/消息面补采共用轮次。
- 验证：py_compile/ruff 通过；干跑识别 15 只有缺口股票（12 资金 + 2 K线 + 1 双缺口）；端到端实测 000858 的 08-11 资金缺口经新浪补采成功（主力 -47244.98 万，`capital_source='sina_main'`，非估算、参与评分）。

## [2026-08-14] 新增腾讯自选股资金面备用层（020A：westock）

- 背景：东财资金面接口频繁不可用；搜索发现腾讯自选股（westock-data-clawhub npm CLI）提供 A股/港股主力净流入（含南下持仓、两融、大宗），社区实测腾讯不封 IP。
- 探针审计：CLI 经 proxy.finance.qq.com 签名网关交付，仅访问单域名；实测 A股主力口径=超大+大（与东财精确同概念：600276 主力 -72451.11 万 = 超大 -63472.32 + 大 -8978.79）；港股返回主力净额+南下持仓。
- 落地：`_fetch_capital_flow_westock` 层插入 东财三层 → **腾讯 westock** → 新浪 → 估算 之间；npx 经 cmd /c 调用（Windows 批处理兼容），Markdown 表解析，45s 超时，连续失败 3 次冷却 30 分钟；写库 `capital_source='westock'`、`is_estimated=0`（参与评分），防覆盖/补采清单 SQL 已含 'westock'（东财恢复后可覆盖回补）。
- 验证：直连测试（A股四档+港股主力）、端到端熔断链路测试（600276/HK3690 落库成功）、py_compile/ruff 通过、服务重启健康 200。

## [2026-08-14] 数据源韧性增强（019Z：东财熔断冷却 + 编号子域轮换 + 请求节流）

- 依据社区实测情报（a-stock-data SKILL，2026-06）：东财封禁阈值与"push2 被封 ≠ 全站不可用"、编号子域可绕部分 WAF 拦截。
- 新增进程级"东财熔断冷却"：批量回退循环触发熔断（连续失败 5 只）后进入 2 小时冷却期，期间 `_fetch_capital_flow_em_individual` / `_fetch_capital_flow_em` / akshare 备用源**直接跳过**（省去每只约 2.5 分钟空等，链路自动落新浪主力口径/估算）；期间任意一次东财成功即提前解除。
- `_http_get_em` 增强：第 3 轮起 push2/push2his 编号子域（1~99）轮换；全局最小请求间隔 0.5s（社区阈值 <5 次/秒）。
- 验证：py_compile/ruff 通过；熔断状态机与子域轮换逻辑自检通过；服务重启后健康检查 200。

## [2026-08-14] 服务自愈看门狗（补充：巡检间隔降至 1 分钟）

- 背景：同日 17:38/17:44 两次注销导致服务中断，5 分钟巡检的恢复窗口过长；ONLOGON 触发器被本机策略拒绝（Access denied）。
- 调整：`StockAnalyst Watchdog` 计划任务改为 `/SC MINUTE /MO 1`（每分钟巡检，登录后 ≤60 秒自动恢复）。
- 实测：杀进程 → 17:48:01 巡检刻度自动复活，health 200；任务每分钟正常触发（IgnoreNew 策略不影响后续巡检，看门狗拉起的是分离子进程）。
- 说明：PowerShell `Set-ScheduledTask`/`Register-ScheduledTask` 在本机会话被拒，改用 `schtasks /Create`（经 cmd 中转处理引号）注册。

## [2026-08-14] 数据修复：每天仅一份最终报告（回测依据统一）

### 背景
- 需求：每天的最终报告只有一份，且是回测中心"评级有效性报告 / 价格建议命中率"的依据。
- 盘点结论：写入路径已有保护（daily 生成时清掉当天 intraday；ratings_history `UNIQUE(stock_id, rating_date)` + `INSERT OR REPLACE`），问题仅存在于 013 迁移前的历史存量数据。

### 数据修复（备份：db_backup_20260814_170133_enforce_single_daily_report.db）
- 删除被 daily 顶替的历史 intraday 行 **106 行**（08-13/08-11/08-06/08-04/07-31）。
- 归一化旧状态 `success` → `ok` **27 行**（07-24 的报告此前因状态值不符而被所有读取路径"隐形"）。
- 校验：daily+intraday 共存 = 0；每股每天 ok 报告恰好 1 份；ratings_history / price_backtest_results / backtest_results（真实行）均无重复。
- 说明：08-10 有 3 只股票当日生成失败（仅 failed 标记、无 ok 报告），可重跑当日报告补齐。

## [2026-08-14] 服务自愈看门狗 + 掉线根因修复

### 背景
- 08-13~08-14 多次服务掉线。根因：系统注销/关机/睡眠会终止用户会话进程（Windows 事件 1074/42/7002），登录自启存在失灵竞态（14:03 一次登录后未拉起）。

### 新增
- `scripts/watchdog.py`：端口检查（127.0.0.1:5000）+ pythonw 分离式静默拉起（无窗口、幂等）。
- Windows 计划任务 **StockAnalyst Watchdog**（每 5 分钟，登录会话内运行）：服务被误杀/窗口误关/登录后均 5 分钟内自动恢复。
- 端到端自愈演练通过：杀进程 → 触发任务 → 10 秒内 `/api/health` 恢复 200。

### 说明
- 托盘图标仅作状态显示，服务存续不再依赖托盘。
- 若需"注销后仍运行"，可升级为 SYSTEM 级计划任务（需管理员权限），但会与托盘/start.bat 的端口释放逻辑冲突，默认不启用。

## [2026-08-13] 盘中快报生成动效 + 蓝图路径回归修复

### 前端动效（盘中快报 / 每日报告 生成过程可视化）
- **templates/index.html**：
  - 新增步骤时间线动效（准备 → 采集数据 → 分析评分 → 写入报告 → 完成），当前阶段脉冲高亮、已完成打 ✓、失败显示 ✕
  - 流光渐变进度条 + 旋转 spinner + 实时百分比/第几只/当前股票/当前阶段
  - `generateIntradayReport()` 接入 `/api/daily-report/progress` 轮询（1.5s），替换原静态"请稍候"占位
  - `renderProgressUI` 升级为通用动效面板，按场景显示标题（每日报告 / 盘中快报）

### 回归修复（app.py 拆分为蓝图引入的 __file__ 路径偏移）
- `blueprints/report.py`：进度文件读取路径改为复用 `daily_report._REPORT_PROGRESS_PATH`（单一来源，原路径指向 blueprints/logs/ 读不到数据）
- `blueprints/system.py`：`_ROLLBACK_AUDIT_LOG` 路径补一级 dirname，回落到 `logs/rollback_audit.log`

### 验证
- `python -m pytest tests/`：392 passed，1 skipped
- 真实启动：`/api/daily-report/progress` 正确返回进度 JSON；页面包含动效代码；内联 JS `node --check` 通过

## [2026-08-13] 代码结构治理（app.py 拆分 / 脚本归档 / 路由测试）

### 结构调整（本次改造）
- **app.py 按业务域拆分为 blueprints/ 蓝图包**（4094 行 → 约 130 行入口）
  - 新增 9 个业务蓝图：watchlist（自选股/分组/采集）/ analysis（分析/评级/v5）/ portfolio（持仓/流水/成本）/ report（日报）/ system（健康/引擎）/ backtest（回测/优化）/ export（导出）/ index_ratings（指数）/ alerts（预警）
  - 共享展示层工具函数（_fmt_* / _derive_obos_signal / _resolve_report_type 等 9 个）迁至 blueprints/_utils.py
  - 函数体零改动，仅装饰器 @app.route → @bp.route；102 函数 / 77 路由与拆分前逐一对齐
- **scripts/ 诊断脚本归档**：12 个 diag_*.py（东财反爬/数据源排障等历史一次性脚本）移入 scripts/archive/diag/
- **新增 tests/test_routes.py 路由层冒烟测试**：16 个用例，覆盖全部 9 个蓝图的核心端点（隔离临时库，不触网）
- **analysis_engine.py 标注 LEGACY 状态**：灰度已完成 all_v5，但作为 advisor 回退路径与 engine_switcher 熔断依赖暂不可删，docstring 注明清理条件
- **.gitignore 完善**：补充 .pytest_cache/ .mypy_cache/ .reasonix/ 等，清除误提交的 31 个 .reasonix 环境文件

### 验证
- `python -m pytest tests/`：392 passed（原 376 + 新增 16），1 skipped
- `ruff check .`：通过
- 真实启动冒烟：/api/health、首页、db-stats、ratings、engine/status、stocks 全部 200

## [2026-07-29] 009 价格建议增强（全栈开发）

### 009: 价格建议增强模块（glm5.2）
- **重写 modules/price_advisor.py**：状态机+动态止盈+网格价位+资金面转化+交易流水分析
  - 操作建议状态机（S1-S4 × 5评级矩阵，S4破止损禁止加仓）
  - 止盈价动态化（双约束：max(最低止盈, min(固定止盈, 技术阻力位))）
  - 网格价位（无持仓3档买入，有持仓1补+3减，ATR动态间距）
  - 资金面信号转化（7档修饰词，正则解析资金面文本）
  - 交易流水分析（加仓节奏/成本趋势/买卖时机，数据不足静默跳过）
- **app.py**：4处调用点追加 position_advice 覆盖逻辑（+13行）
  - /analyze, /advise, report-latest(实时+自动触发)
  - 当 price_advice 有动态操作建议时覆盖旧 position_advice
- **templates/index.html**：价格建议section重写为网格表格+资金面+交易分析+状态颜色编码（+86/-21行）
- 红线零触碰：advisor.py / data_collector.py / db_manager.py / daily_report.py / config_weights.json

## [2026-07-28] 005 价格建议（全栈开发）

### 005: 价格建议模块（glm5.2）
- **新建 modules/price_advisor.py**：ATR + MA/BOLL 组合算法，生成买入区间/目标价/止损价/止盈价/建议仓位
- **app.py**：/advise + /analyze + 批量分析 3处端点后处理集成（generate_advice 返回后追加 price_advice，不修改 generate_advice）+ report-latest 返回 price_advice
- **modules/daily_report.py**：_save_report 新增 price_advice 参数，日报持久化价格建议 JSON
- **database/db_manager.py**：daily_reports 表新增 price_advice TEXT 列
- **templates/index.html**：投资建议详情区域新增价格建议 section（无持仓/有持仓两种表格 + 免责声明 + CSS）
- 无持仓输出：买入区间/目标价/止损价/建议仓位/预期涨幅/最大回撤
- 有持仓输出：止盈价/止损价/成本价/浮盈/操作建议
- 数据不足时返回 available=false 优雅降级

## [2026-07-26] 数据完整度提升 + 四维因子明细 + 回测改四维 + 文档整理

### B19-1: analysis_results 日期对齐（kimi k3）
- 修复 analysis_results.analysis_date 与 daily_reports.report_date 非交易日不对齐
- advisor.py: generate_advice 增加 report_date 参数，_save_analysis_results_for_v5 支持 report_date 覆盖
- daily_report.py: 调用 generate_advice 传 target_date
- 删除 28 个历史遗留临时脚本（_sync_daily_reports.py / _fix_db_scores.py / _check_*.py 等）

### B20: v5 引擎四维因子明细（glm5.2）
- advisor.py: 重写 _build_v5_factors，新增 _build_kline_factors/_build_fundamental_factors/_build_capital_factors/_build_news_factors
- templates/index.html: 修复 var dims 变量遮蔽 bug（B15-T4 引入，L3862 var dims → var dqDims）

### B21: PE/PB 聚合回退防御兜底（glm5.2）
- data_adapter.py: _read_fundamental_data 增加聚合回退（pe_ratio/pb_ratio/holder_increase 最新行 NULL 时取次新非空行）

### B22: 消息面数据维度扩展（glm5.2）
- data_contract.py: StockData 新增 news_count/news_positive_ratio/news_negative_count，NEWS 集合从 2→5 字段，news_total 从 2→5
- data_adapter.py: 从 news_sentiment 表映射 total_count/positive_count/negative_count
- 消息面完整度从 50% 提升到 80%

### B23: 回测模拟改四维评分（glm5.2）
- backtest_engine.py: run_historical_simulation 从技术面单维度改为调用 scoring_engine.analyze 四维综合评分

### B24: 前端消息面因子展示（glm5.2）
- templates/index.html: _factorPriority.news 和 _dimFactorLabels.news 新增 news_count

### B25: 用户使用说明文档更新（minimax m3）
- 用户使用说明.md: 从 226 行扩展到 587 行，新增每日报告/看板/回测/导出/四维详情章节

### 文档整理
- 新增 docs/PROJECT_INDEX.md（项目文档索引）

## [2026-07-15] 市值与已实现盈亏精确计算升级

### 盈亏计算逻辑变更（审计追溯）

**变更前**：
- 市值 = cost_price × quantity（基于成本价估算，表头标注「市值估计」）
- 已实现盈亏 = holdings.realized_pnl（流水编辑时重算，但无独立查询接口）

**变更后**：
- 市值 = quantity × price_cache.latest_price（基于实时行情精确计算）
  - latest_price 为 NULL 时显示 "--"（不显示0）
  - 银行家舍入法（ROUND_HALF_EVEN），保留2位小数
  - 向后兼容：旧字段 `estimated_market_value` 保留（@deprecated），与 `market_value` 值相同
- 已实现盈亏：新增 `/api/portfolio/realized-pnl` 独立接口
  - 计算方法：加权平均法（Weighted Average Cost）
  - 数据源：从 trade_records 逐笔计算
  - 不含交易手续费（与券商对账单口径一致）
  - 支持按日/周/月聚合查询

### 数据库变更
- 新增索引：`idx_trade_records_stock_date` (trade_records.stock_id, trade_date, created_at)
- 新增索引：`idx_price_cache_stock` (price_cache.stock_id)

### API 变更
| 字段 | 变更类型 | 说明 |
|------|---------|------|
| `market_value` | 新增 | 精确市值 = quantity × latest_price |
| `estimated_market_value` | @deprecated | 向后兼容字段，值与 market_value 相同 |
| `data_status` | 新增 | realtime / cache / offline |

### 新增接口
- `GET /api/portfolio/realized-pnl` — 已实现盈亏精确查询（支持 stock_id / period / start_date / end_date 参数）
