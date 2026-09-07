# AGENTS.md — Stock Analyst 项目导航入口

> 本文件为 AI 代理（Agent）提供标准导航入口。任何代理在本仓库开始工作前，应先阅读本文件。
> 所有命令与相对路径均以本项目根目录（即本文件所在目录 `stock_analyst/`）为基准。

---

## 1. 项目概述

**Stock Analyst（智能个股分析与评级系统）** 是一款面向 A 股（及港股）个人投资者的价值投资分析工具。

- **形态**：单机运行的 Flask Web 应用（本地服务，浏览器访问）。
- **核心能力**：自选股管理 → 数据采集（akshare）→ 四维量化评分（K线技术面 / 基本面 / 资金面 / 消息面）→ 评级与建议生成 → 每日报告 → 价格建议 → 智能预警 → 评级回测验证。
- **数据存储**：单文件 SQLite（`stock_analyst.db`），WAL 模式。
- **目标用户**：零代码个人投资者，强调"一键启动、浏览器即用"。

---

## 2. 技术栈

| 类别 | 技术 |
|------|------|
| 语言 | Python 3.12+ |
| Web 框架 | Flask（单进程，`FLASK_DEBUG=False`） |
| 数据源 | akshare + 腾讯自选股(westock)/新浪/东财多源容错（公开行情/财务/资金面） |
| 数据存储 | SQLite3（WAL 模式，应用层管理级联） |
| 数据校验 | pydantic |
| 数据处理 | pandas / numpy |
| 导出 | openpyxl（Excel） |

依赖详见 `requirements.txt`。

---

## 3. 入口点

| 入口 | 路径 | 说明 |
|------|------|------|
| **Flask 主应用** | `app.py` | 应用入口（约 130 行）：环境初始化、蓝图注册、首页路由、启动逻辑。启动后监听 `127.0.0.1:5000`。 |
| **路由蓝图** | `blueprints/` | API 路由按业务域拆分（自 2026-08-13）：`watchlist`（自选股/分组/采集）、`analysis`（分析/评级/v5）、`portfolio`（持仓/流水/成本）、`report`（日报）、`system`（健康/统计）、`backtest`（回测/优化）、`export`（导出）、`index_ratings`（指数）、`alerts`（预警）；共享展示层工具在 `_utils.py`。 |
| **全局配置** | `config.py` | 路径、采集参数、评分权重、评级档位、风控阈值、Flask 配置。 |
| **权重热加载** | `config_weights.json` | 评分权重，运行时可修改无需重启。 |
| **数据库管理** | `database/db_manager.py` | 建表、连接、WAL/锁配置。 |
| **业务模块** | `modules/` | 采集、评分、建议、报告、回测、预警等。 |

### 关键 API 端点（app.py）

- `GET /` — 首页
- `GET /api/health` — 健康检查（仅运维：watchdog/start.bat 依赖）
- `POST /api/init-db` — 初始化数据库
- `GET|POST /api/stocks` — 自选股增查
- `POST /api/collect/<stock_id>` — 触发单只股票数据采集
- `POST /api/stocks/<stock_id>/analyze` — 四维分析评分
- `GET /api/v5/scoring-demo` — v5.0 评分引擎演示（仅调试）
- `POST /api/daily-report/generate` — 生成每日报告
- `GET /api/daily-report/latest` — 取最新报告
- `GET|POST /api/backtest/*` — 评级回测

> 021D 起已删除的旧端点：`/api/stocks/<id>/refresh-full`、`/api/stocks/<id>/analysis`、`/api/stocks/<id>/ratings`、`/api/ratings`、`/api/backtest/simulate`、`/api/backtest/status`、`/api/daily-report/<date>`、`/api/daily-report/history`、`/api/watchlist/groups`、`/api/portfolio/groups/<id>`（PUT/DELETE）、`/api/positions/<id>/cost-adjustments`、`/api/portfolio/realized-pnl`、`/api/alerts/rules/<id>`（PUT）、`/api/alerts/scan`——详见 CHANGELOG 021D。

### 多交易账户（021W，2026-08-21）

- **隔离范围**：仅持仓域——`holdings`/`trade_records` 经 `account_id` 归属账户；自选股/分析/预警全局共享。
- **关键约束**：`holdings` 唯一键为 `UNIQUE(account_id, stock_id)`（同股可多账户分仓）；默认账户（is_default=1）禁止删除；账户删除需 `force_confirm` 且流水保留归入默认账户。
- **端点**：`GET|POST /api/accounts`、`PUT|DELETE /api/accounts/<id>`；持仓/流水/summary 端点均支持 `?account_id=` 过滤（缺省/'all'=全部聚合）。
- **重算口径**：`_recalculate_holding(cursor, stock_id, account_id)` 按（股票, 账户）维度隔离，改流水逻辑时必须保持该口径。
- **多行兼容约定**：任何 `JOIN holdings ON stock_id` 的新代码必须处理同股多仓（用「最大持仓子查询」或按账户过滤），否则会产生重复行。

---

## 4. 启动命令

在项目根目录（`stock_analyst/`）下执行：

```bash
# 方式一：Windows 一键启动（推荐，含依赖检查/端口释放/健康检查）
start.bat

# 方式二：直接运行
python app.py
```

启动成功后浏览器访问：http://127.0.0.1:5000

> 注：`start.bat` 会优先使用 `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`，缺失时回退到系统 PATH 中的 python。

### 服务自愈（Watchdog）

- Windows 计划任务 **`StockAnalyst Watchdog`**（schtasks，`/SC MINUTE /MO 1`）**每分钟**静默检查一次 `127.0.0.1:5000`，服务不在则用 pythonw 无窗口方式自动拉起（`scripts/watchdog.py`，带端口守卫，幂等）。
- 2026-08-17（021H）：修复任务电源条件——原任务带「仅接通电源时启动」，笔记本用电池供电时巡检被整体跳过（实测 18:08 后任务停摆、杀进程不复活）；已导出 XML 将 `DisallowStartIfOnBatteries=false` / `StopIfGoingOnBatteries=false` 后 `schtasks /create /xml /f` 重建，电池供电下自愈实测通过。
- 效果：注销/关机后重新登录、服务被误杀、窗口被误关，均会在 **1 分钟内**自动恢复，无需人工干预；托盘图标仅作状态显示，服务存续不依赖它。
- 2026-08-14 实测：注销再登录后由下一巡检刻度自动复活（实测演练：杀进程 → 1 分钟内 health 恢复 200）。
- 注册命令（无空格路径问题，经 cmd 中转）：`schtasks /Create /TN "StockAnalyst Watchdog" /SC MINUTE /MO 1 /TR "\"<pythonw绝对路径>\" \"<项目绝对路径>\\scripts\\watchdog.py\"" /F`；查询/删除：`schtasks /query /tn "StockAnalyst Watchdog"` / `schtasks /delete /tn "StockAnalyst Watchdog" /f`。
- 注意：该任务运行在登录会话内（ONLOGON 触发器被本机策略拒绝，改用 1 分钟巡检覆盖登录场景），注销后服务随会话停止、下次登录 1 分钟内恢复；如需注销后仍常驻，需升级为 SYSTEM 级任务（管理员权限），但会与托盘/start.bat 的端口释放逻辑冲突，默认不启用。

---

## 5. 验证命令

```bash
# 标准验证命令（在项目根目录运行）
python -m pytest tests/            # 默认 = fast 层（实测 ~15s；跳过 slow 标记的真实时钟退避测试，OPT-5 分层）
python -m pytest tests/ -m "slow or not slow"   # 全量（含 slow 层，约 7~8 分钟）
python scripts/check_redlines.py   # 红线自动核验（021A，随 pytest 执行）
ruff check .
mypy app.py config.py modules
```

> **测试分层（OPT-5，2026-09-07）**：`pyproject.toml` 设默认 `addopts = -m "not slow" --timeout=60`。
> `slow` 标记（`TestTickBackoff` 等 6 例，真实时钟退避验证）默认跳过；CI 与全量验证用 `-m "slow or not slow"`。

> **测试目录说明**：项目根目录下已建立独立 `tests/` 目录，作为标准单元测试入口，包含：
> - `tests/test_scoring_engine.py` — 评分引擎（scoring_engine）单元测试，覆盖子项评分函数、权重应用与降级机制、评级映射及端到端 analyze()
> - `tests/test_routes.py` — 路由层冒烟测试（隔离临时库，不触网），覆盖全部 9 个蓝图的核心端点
> - `tests/conftest.py` — pytest 公共配置，通过 MockDataProvider 生成纯内存数据隔离数据库与网络，并自动把项目根目录注入 sys.path
>
> 补充验证脚本（位于项目根目录，自带 sys.path 注入，需在项目根执行）：
> - `test_us11_consistency.py` — US11 一致性验证脚本
> ```bash
> python test_us11_consistency.py
> ```
>
> 021AE 起已删除：`test_engine_compare.py` / `tests/test_engine_compare.py`（新旧引擎对比脚本，随经典引擎一并退役）。

服务运行时，健康检查：
```bash
curl http://127.0.0.1:5000/api/health
```

---

## 6. 模块地图（modules/）

| 模块 | 职责 |
|------|------|
| `modules/collector/` | **核心采集包（OPT-3，2026-09-07 拆分）**：原 `data_collector.py`（6,055 行）按数据源拆为 18 子模块（`_env` 环境基座 → `http_client` 请求层 → 各数据源采集器 → `collect` 编排），`data_collector.py` 保留为 **facade 全量再导出**（192 符号表面兼容，调用方零改动）。⚠️ 约定：①测试 monkeypatch 必须指向**实现/消费方子模块**（补丁打在 facade 对包内调用不可见）；②跨模块共享可变状态与其唯一 global 写入者同模块；③包 `__init__` 强制 requests 补丁先于 akshare 加载，勿调整子模块导入顺序。 |
| `backfill_scheduler.py` | 数据完整性驱动的持续补采调度器（缺口检测 + 周期重试 + 自动退避，app.py 启动时注册；021BA 起含行业分类自愈——每轮限量补取"未分类"A股行业）。 |
| `data_contract.py` | v5.0 标准数据契约（StockData），业务逻辑仅依赖此契约，禁耦合具体数据源。 |
| `data_adapter.py` | SQLite 真实数据 ↔ StockData 契约的适配层。 |
| `scoring_engine.py` | v5.0 四维评分引擎（唯一引擎，基于标准数据契约；经典引擎已于 021AE 删除）。 |
| `rating_config.py` | 评级档位三处一致性自检（启动自检 + 红线 R6 核验复用）。 |
| `advisor.py` | 评级与建议生成（⚠️ 见风险边界 B24 红线）。 |
| `rating_hysteresis.py` | 评级变更迟滞（021AG）：分数跨线 ±3 分才换挡，抗边界抖动；`config.RATING_HYSTERESIS_ENABLED=False` 可回退。 |
| `price_advisor.py` | 价格建议增强（后处理集成，不改 generate_advice）。 |
| `price_backtest.py` | 价格建议回测验证（T+5/T+20 双周期命中率）。 |
| `alert_engine.py` | P3-B 智能预警（G1-G3 规则）。 |
| `backtest_engine.py` | M8 评级有效性监测（回测）引擎。 |
| `optimizer_engine.py` | M9 自动优化引擎（规则化方案；以 T+1 日准确率为代理，安全阀已知空转，021AI 起动态目标走 dynamic_optimizer）。 |
| `dynamic_optimizer.py` | 021AI 动态窗口权重优化器：维度分重放+网格+前向验证+数据门槛（v5 纯净样本不足不改权）；入口 `scripts/run_dynamic_optimizer.py`。 |
| `daily_report.py` | 每日报告生成（ThreadPoolExecutor 超时控制）。 |
| `market_screener.py` | 021BI 全市场选股扫描器：两段漏斗（新浪快照粗筛 ~5553 只 → 腾讯K线 8 类技术信号精筛）。数据源新浪/腾讯，与东财断连解耦；只产候选不自动入库，加自选 ≤20。端点 `POST /api/market/scan`、`/api/market/scan-signals`。 |
| `index_collector.py` | 指数数据采集与评级。 |
| `export_engine.py` | 报告导出（Excel .xlsx）。 |
| `news_collector.py` | 新闻/消息面采集。 |
| `sentiment_dict.py` | 情绪词典。 |
| `mock_data_provider.py` | 模拟数据提供者（开发/测试用）。 |
| `scoring_engine_validation.py` | 评分引擎自验证。 |
| `technical_backtest.py` | 020R-51：技术面专项历史回测（当前技术规则按历史时点逐日重算，T+5/T+20 方向命中率，一键脚本 `scripts/run_technical_backtest.py`）。 |

---

## 7. 关键风险边界（红线）

> ⚠️ **红线定义的单一事实来源是 [docs/RED_LINES.md](docs/RED_LINES.md)**（2026-08-16 红线治理 021A 起生效）。
> 以下为精简摘要；冲突时以 RED_LINES.md 为准。自动核验：`python scripts/check_redlines.py`（随 pytest 执行）。
> 红线变更须走 RED_LINES.md §6 豁免登记流程。以下边界涉及数据安全与系统稳定性，修改前必须充分评估，优先向用户确认。

### 7.1 数据库操作（SQLite）

- **单文件库**：所有数据存于 `stock_analyst.db`。任何 `DROP TABLE` / `DELETE` / 清表操作具破坏性，不可逆。
- **WAL 模式 + busy_timeout=10s**：写操作遇锁等待 10 秒。**严禁**开启 `FLASK_DEBUG=True`（会启动 Flask 双进程，导致数据库锁冲突）。
- **外键约束关闭**（`PRAGMA foreign_keys=OFF`）：级联删除由应用层手动管理（见 `db_manager.get_connection`）。改库结构时须同步维护应用层级联逻辑。
- **衍生表同步**：数据纠错后须同步刷新衍生表，存在"安全锁"机制，避免状态不一致。
- **每日报告不变量**：`daily_reports` 每股每天至多一份有效报告（`daily` 顶替 `intraday`，`UNIQUE(report_date, stock_id, report_type)`）；回测中心"评级有效性/价格建议命中率"的依据是 `ratings_history`（`UNIQUE(stock_id, rating_date)` + `INSERT OR REPLACE`，每股每天一条），改动报告或评级写入逻辑时须保持该不变量。

### 7.2 持仓风控阈值（config.py，禁止随意放宽）

| 配置项 | 默认值 | 含义 |
|--------|--------|------|
| `COST_ADJUSTMENT_DEVIATION_THRESHOLD` | `0.30`（±30%） | 成本修正偏离超此比例需二次确认（`force_confirm`）。 |
| `COST_ADJUSTMENT_COOLDOWN_HOURS` | `24` | 同一持仓修正冷却时间，防频繁篡改。 |
| `TRADE_T1_LOCK_ENABLED` | `True` | T+1 锁定：当日流水次日才可改。 |
| `TRADE_AMOUNT_VERIFY_THRESHOLD` | `50000`（元） | 单笔流水超此值，编辑/删除需二次验证。 |
| `BATCH_OPERATION_LIMIT` | `20` | 单次批量操作上限。 |

> 调整上述阈值会直接影响用户资金安全与风控强度，属高风险变更。

### 7.3 代码红线

- **B24 红线**：[advisor.py](modules/advisor.py) 的 `generate_advice` 禁止修改。价格增强等扩展只能以**后处理**方式集成（见 `price_advisor.py`）。
- **数据源解耦**：业务逻辑严禁直接耦合 akshare/tushare 原始字段，必须经 `data_contract.py` 标准契约。
- **展示层格式化**：API 返回统一格式化（`_fmt_pct`/`_fmt_num`/`_fmt_wan`），但内部计算模块读数据库原始值，不受格式化影响。

---

## 8. 目录结构概览

```
stock_analyst/
├── app.py                  # Flask 主应用（入口）
├── config.py               # 全局配置
├── config_weights.json     # 评分权重（热加载）
├── requirements.txt
├── start.bat / start.sh    # 一键启动脚本
├── stock_analyst.db        # SQLite 数据库（运行产物）
├── database/               # 数据库管理
│   └── db_manager.py
├── modules/                # 业务模块（见模块地图）
├── blueprints/             # API 路由蓝图（按业务域拆分）
├── templates/              # Flask 页面模板（仅 index.html 骨架）
├── static/                 # 前端静态资源（css/ + js/ 八文件按业务域加载：core→watchlist→analysis→portfolio→backtest→alerts→market→boot，OPT-4；vendor/ 本地化三方库，OPT-7）
├── scripts/                # 运维脚本（托盘 tray.py / 服务安装 / 看门狗 watchdog.py / check_redlines.py / cleanup_backups.py）
├── tests/                  # pytest 单元/冒烟测试（隔离临时库，不触网；含级联完整性清单 test_cascade_integrity.py、健康度 test_health_sources.py）
├── docs/                   # 项目文档（需求/任务书/验收/评审/PM上下文/知识库，见 docs/PROJECT_INDEX.md）
├── reports/                # 每日分析报告（运行产物，不入库；验收报告见 docs/reports/）
├── backups/                # 数据库备份（db_backup_*.db）
├── logs/                   # 运行日志（app.log 等）
└── test_*.py               # 补充验证脚本
```

---

## 9. 代理工作约定

1. **修改前先读**：动任何模块前，先读其文件头 docstring 与相关 `config.py` 配置。
2. **保留红线**：以 [docs/RED_LINES.md](docs/RED_LINES.md) 为准——不碰 `advisor.generate_advice`（B24），不随意放宽风控阈值，不开 `FLASK_DEBUG`；任何触碰受保护对象的变更须走 RED_LINES.md §6 豁免登记流程。
3. **数据库变更**：涉及表结构/清数据，先备份 `stock_analyst.db`，并同步应用层级联逻辑。
4. **零代码用户优先**：所有方案须保证用户能 `python app.py` 一键启动并浏览器访问，避免引入额外运维负担。
5. **中文交付**：项目文档、报告、注释以中文为准。
6. **UI/UX 反向校验契约层**（2026-08-17，021E/F/G 三连修教训）：为支撑 UI/UX 而设计的契约层——数据建模、接口口径、缓存策略、前端状态管理——**不是开工前不可动摇的终点，必须接受真实 UI/UX 端到端验收的反向校验**。必要时优先实现最小可用 UI/UX 用于真实使用验证，并接受反馈随时调整契约层。反例：P3-A"报告页读快照与列表同源"、看板 ETag 304、路由 `_viewLoaded` 每会话仅首次加载，三项契约各自"正确"，叠加结果却是用户看到的全是旧数据、须按特定顺序手动刷新两处才生效（021E/021F/021G 修复）。契约层自洽 ≠ 用户体验正确；判定标准是用户真实操作路径下的端到端表现。
