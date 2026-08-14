# 变更日志 (CHANGELOG)

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
