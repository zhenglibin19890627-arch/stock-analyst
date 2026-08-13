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
| 数据源 | akshare（公开行情/财务数据） |
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
| **路由蓝图** | `blueprints/` | API 路由按业务域拆分（自 2026-08-13）：`watchlist`（自选股/分组/采集）、`analysis`（分析/评级/v5）、`portfolio`（持仓/流水/成本）、`report`（日报）、`system`（健康/引擎）、`backtest`（回测/优化）、`export`（导出）、`index_ratings`（指数）、`alerts`（预警）；共享展示层工具在 `_utils.py`。 |
| **全局配置** | `config.py` | 路径、采集参数、评分权重、评级档位、风控阈值、Flask 配置。 |
| **权重热加载** | `config_weights.json` | 评分权重，运行时可修改无需重启。 |
| **引擎切换** | `config_engine_switch.json` | 新旧引擎灰度切换控制。 |
| **数据库管理** | `database/db_manager.py` | 建表、连接、WAL/锁配置。 |
| **业务模块** | `modules/` | 采集、评分、建议、报告、回测、预警等。 |

### 关键 API 端点（app.py）

- `GET /` — 首页
- `GET /api/health` — 健康检查
- `POST /api/init-db` — 初始化数据库
- `GET|POST /api/stocks` — 自选股增查
- `POST /api/collect/<stock_id>` — 触发单只股票数据采集
- `POST /api/analyze/<stock_id>` — 四维分析评分
- `GET /api/v5/scoring-demo` — v5.0 评分引擎演示
- `POST /api/daily-report/generate` — 生成每日报告
- `GET /api/daily-report/<date>` — 按日期取报告
- `GET|POST /api/backtest/*` — 评级回测

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

---

## 5. 验证命令

```bash
# 标准验证命令（在项目根目录运行）
python -m pytest tests/
ruff check .
mypy app.py config.py modules
```

> **测试目录说明**：项目根目录下已建立独立 `tests/` 目录，作为标准单元测试入口，包含：
> - `tests/test_scoring_engine.py` — 评分引擎（scoring_engine）单元测试，覆盖子项评分函数、权重应用与降级机制、评级映射及端到端 analyze()
> - `tests/test_routes.py` — 路由层冒烟测试（隔离临时库，不触网），覆盖全部 9 个蓝图的核心端点
> - `tests/conftest.py` — pytest 公共配置，通过 MockDataProvider 生成纯内存数据隔离数据库与网络，并自动把项目根目录注入 sys.path
>
> 补充验证脚本（位于项目根目录，自带 sys.path 注入，需在项目根执行）：
> - `test_engine_compare.py` — 新旧评分引擎并行对比验收脚本
> - `test_us11_consistency.py` — US11 一致性验证脚本
> ```bash
> python test_engine_compare.py
> python test_us11_consistency.py
> ```

服务运行时，健康检查：
```bash
curl http://127.0.0.1:5000/api/health
```

---

## 6. 模块地图（modules/）

| 模块 | 职责 |
|------|------|
| `data_collector.py` | **核心采集**：获取 A股/港股基本面、技术面、消息面、资金面数据（akshare）。 |
| `backfill_scheduler.py` | 数据完整性驱动的持续补采调度器（缺口检测 + 周期重试 + 自动退避，app.py 启动时注册）。 |
| `data_contract.py` | v5.0 标准数据契约（StockData），业务逻辑仅依赖此契约，禁耦合具体数据源。 |
| `data_adapter.py` | SQLite 真实数据 ↔ StockData 契约的适配层。 |
| `analysis_engine.py` | 模块2：旧版四维分析引擎（量化因子打分，输出 0-100）。 |
| `scoring_engine.py` | v5.0 四维评分引擎（新版，基于标准数据契约）。 |
| `engine_switcher.py` | 引擎灰度切换控制器（新旧引擎选择与熔断记录）。 |
| `advisor.py` | 评级与建议生成（⚠️ 见风险边界 B24 红线）。 |
| `price_advisor.py` | 价格建议增强（后处理集成，不改 generate_advice）。 |
| `price_backtest.py` | 价格建议回测验证（T+5/T+20 双周期命中率）。 |
| `alert_engine.py` | P3-B 智能预警（G1-G3 规则）。 |
| `backtest_engine.py` | M8 评级有效性监测（回测）引擎。 |
| `optimizer_engine.py` | M9 自动优化引擎（规则化方案）。 |
| `daily_report.py` | 每日报告生成（ThreadPoolExecutor 超时控制）。 |
| `index_collector.py` | 指数数据采集与评级。 |
| `export_engine.py` | 报告导出（Excel .xlsx）。 |
| `news_collector.py` | 新闻/消息面采集。 |
| `sentiment_dict.py` | 情绪词典。 |
| `mock_data_provider.py` | 模拟数据提供者（开发/测试用）。 |
| `scoring_engine_validation.py` | 评分引擎自验证。 |

---

## 7. 关键风险边界（红线）

> 以下边界涉及数据安全与系统稳定性，修改前必须充分评估，优先向用户确认。

### 7.1 数据库操作（SQLite）

- **单文件库**：所有数据存于 `stock_analyst.db`。任何 `DROP TABLE` / `DELETE` / 清表操作具破坏性，不可逆。
- **WAL 模式 + busy_timeout=10s**：写操作遇锁等待 10 秒。**严禁**开启 `FLASK_DEBUG=True`（会启动 Flask 双进程，导致数据库锁冲突）。
- **外键约束关闭**（`PRAGMA foreign_keys=OFF`）：级联删除由应用层手动管理（见 `db_manager.get_connection`）。改库结构时须同步维护应用层级联逻辑。
- **衍生表同步**：数据纠错后须同步刷新衍生表，存在"安全锁"机制，避免状态不一致。

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
├── config_engine_switch.json
├── requirements.txt
├── start.bat / start.sh    # 一键启动脚本
├── stock_analyst.db        # SQLite 数据库（运行产物）
├── database/               # 数据库管理
│   └── db_manager.py
├── modules/                # 业务模块（见模块地图）
├── blueprints/             # API 路由蓝图（按业务域拆分）
├── templates/              # Flask 页面模板（仅 index.html 骨架）
├── static/                 # 前端静态资源（css/ js/，自 2026-08-13 从 index.html 内联拆出）
├── scripts/                # 运维/迁移脚本
├── tests/                  # pytest 单元/冒烟测试（隔离临时库，不触网）
├── docs/                   # 项目文档（需求/任务书/验收/评审/PM上下文/知识库，见 docs/PROJECT_INDEX.md）
├── reports/                # 每日报告与验收/自验文档
├── backups/                # 数据库备份（db_backup_*.db）
├── logs/                   # 运行日志（app.log 等）
└── test_*.py               # 补充验证脚本
```

---

## 9. 代理工作约定

1. **修改前先读**：动任何模块前，先读其文件头 docstring 与相关 `config.py` 配置。
2. **保留红线**：不碰 `advisor.generate_advice`，不随意放宽风控阈值，不开 `FLASK_DEBUG`。
3. **数据库变更**：涉及表结构/清数据，先备份 `stock_analyst.db`，并同步应用层级联逻辑。
4. **零代码用户优先**：所有方案须保证用户能 `python app.py` 一键启动并浏览器访问，避免引入额外运维负担。
5. **中文交付**：项目文档、报告、注释以中文为准。
