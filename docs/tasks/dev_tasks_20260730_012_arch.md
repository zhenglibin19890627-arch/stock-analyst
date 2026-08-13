# DEV-TASKS-20260730-012-ARCH：012 日志系统建设+日报生成诊断+采集健壮性优化 — 架构方案评审任务书

> **签发人**：PM  | **签发日期**：2026-07-30 | **状态**：待架构师执行

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：架构师

**职责边界**：
- 评审 PM 提出的日志系统+诊断+优化方案，聚焦架构级决策点
- 评估日志架构选型、日报生成健壮性改进方向、采集超时策略
- 对每个决策点给出明确裁定（采纳/修改/否决）+ 理由
- **不编码、不验收、不写功能代码**
- 交付物：`docs/reviews/review_012_logging_diagnosis_20260730.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 架构师仅做方案评审，不执行任何代码修改

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db`（在stock_analyst子目录内！） |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前8包） |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 推荐模型 | **glm5.2** |
| 窗口类型 | **Quests 独立窗口** |
| 执行模式 | 单代理 agent |
| 交付物 | `docs/reviews/review_012_logging_diagnosis_20260730.md` |

---

## 一、需求背景

### 1.1 问题描述

监理反馈：**每日报告生成会卡住，也会出现数据获取失败，需要日志记录用于分析原因**。

### 1.2 PM 调研结果

#### 现状一：日志不留存（核心问题）

| 模块 | 日志配置 | 问题 |
|---|---|---|
| data_collector.py L151 | `logging.basicConfig(level=INFO)` | 仅输出到控制台 |
| advisor.py L26 | `logging.basicConfig(level=INFO)` | 同上 |
| daily_report.py L35 | `logger = logging.getLogger(__name__)` | 无 FileHandler，继承 root logger |
| news_collector.py L25 | `logging.basicConfig(level=INFO)` | 同上 |
| analysis_engine.py L28 | `logging.basicConfig(level=INFO)` | 同上 |
| engine_switcher.py L413 | `logging.basicConfig(level=INFO)` | 同上 |
| data_adapter.py L469 | `logging.basicConfig(level=INFO)` | 同上 |
| alert_engine.py L380 | `logging.basicConfig(level=INFO)` | 同上 |
| app.py L979 | `logging.getLogger(__name__).warning(...)` | 无全局配置 |

**结论**：12个模块都用 `basicConfig` → **仅控制台输出，关窗口即丢失**。`logs/` 目录仅有 `rollback_audit.log`（回滚审计专用）。

#### 现状二：日报生成流程（卡住风险点）

```
generate_daily_report()                     ← daily_report.py L361
├── _generate_lock.acquire(timeout=5)       ← 防抖锁
├── fetch_capital_flow_batch(a_symbols)     ← 批量资金面（同花顺，可能超时）
└── for stock in stocks:                    ← 逐只串行（无超时控制）
    ├── collect_stock_data(symbol, market)  ← 4维数据采集（网络请求，最可能卡住）
    │   ├── fetch_kline()                   ← 腾讯接口（15s超时）
    │   ├── fetch_a_fundamental()           ← 新浪+腾讯接口
    │   ├── fetch_capital_flow()            ← 东方财富接口
    │   ├── fetch_north_capital()           ← akshare接口
    │   ├── fetch_margin_balance()          ← 上交所/深交所API（逐日，可能卡住）
    │   └── fetch_sentiment()               ← akshare新闻接口
    ├── generate_advice(stock_id)           ← 评分分析
    └── _save_report()                      ← 写入 daily_reports 表
        └── 失败时 status='failed' + error_msg
```

**卡住风险点**：
1. 逐只串行无整体超时——单只股票采集卡住，整批等待
2. `fetch_margin_balance` 的 `range(1, days_to_try_max+1)` 逐日请求——011已优化（增量补取），但首次分析仍可能请求多次
3. 网络请求虽有 `REQUEST_TIMEOUT=15`（config.py），但重试 3 次 + 备选源 = 单维度最坏 ~60s

#### 现状三：现有失败记录

| 数据源 | 记录数 | 说明 |
|---|---|---|
| daily_reports status='failed' | **0条** | 无失败记录（可能因为报错时直接跳过） |
| error_logs 表 | 39条 | 主要是 `'code'` KeyError（stock_id=23/25，港股代码解析问题） |
| data_status 表 | 2829条 → 011去重后减少 | 各维度采集状态（success/failed/partial/skipped） |

**关键发现**：`error_logs` 表有 `type='exception'` + `message="'code'"` 的 KeyError，说明**某些港股股票在数据采集时存在代码解析问题**。

---

## 二、PM 拟定的方案（待架构师评审）

### 2.1 方案总表

| # | 方案 | 说明 | PM初步思路 |
|---|---|---|---|
| **012-1** | 文件日志系统 | 所有模块日志输出到文件 | `TimedRotatingFileHandler` 按日轮转 |
| **012-2** | 日报生成进度追踪 | 卡住时可定位到具体股票+维度 | 逐只写入进度文件或DB |
| **012-3** | 采集超时优化 | 降低卡住概率 | 整体超时控制 + 超时降级 |
| **012-4** | 失败日志增强 | 记录完整失败链路 | error_logs 表增强 + 日报失败摘要 |

### 2.2 文件日志系统方案（012-1）

**PM初步方案**：在 `app.py` `main()` 函数启动时配置全局 FileHandler：

```python
import logging
from logging.handlers import TimedRotatingFileHandler

# 在 main() 中 init_database() 之前
log_dir = os.path.join(os.path.dirname(__file__), 'logs')
os.makedirs(log_dir, exist_ok=True)
file_handler = TimedRotatingFileHandler(
    os.path.join(log_dir, 'app.log'),
    when='midnight',  # 每天轮转
    backupCount=7,  # 保留7天
    encoding='utf-8',
)
file_handler.setFormatter(logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s'))
file_handler.setLevel(logging.INFO)
# 同时输出到控制台和文件
root_logger = logging.getLogger()
root_logger.addHandler(file_handler)
root_logger.setLevel(logging.INFO)
```

**优势**：
- 所有模块的 `logging.getLogger(__name__)` 自动继承
- 无需修改任何模块文件
- 标准库实现，无新依赖
- 文件 `logs/app.log` 按日轮转，保留7天

### 2.3 日报生成进度追踪方案（012-2）

**PM初步方案**：在 `generate_daily_report` 的 for 循环中增加进度日志：

```python
# 在 L402 for stock in stocks 循环内
logger.info(f'[日报进度] {idx}/{total} 开始处理 {symbol} {name}')

# 在 collect_stock_data 之后
logger.info(f'[日报进度] {symbol} 数据采集完成')

# 在 generate_advice 之后
logger.info(f'[日报进度] {symbol} 分析完成 score={total_score} rating={rating}')
```

**可选增强**：写入一个进度文件 `logs/report_progress.json`：
```json
{
    "date": "2026-07-30",
    "total": 27,
    "current": 15,
    "current_symbol": "600276",
    "started_at": "2026-07-30 18:00:00",
    "last_update": "2026-07-30 18:03:25"
}
```

### 2.4 采集超时优化方案（012-3）

**PM初步方案**：

| 优化项 | 说明 |
|---|---|
| A. 单只整体超时 | 在 `generate_daily_report` 循环中为每只股票设超时上限（如 120s），超时则 skip |
| B. 超时降级 | 网络请求失败时不阻塞——已有 data_warnings 机制，确保评分引擎有降级数据可用 |
| C. 重试次数调整 | config.py MAX_RETRIES=3 是否合理？可否降为2减少等待时间？ |

### 2.5 失败日志增强方案（012-4）

**PM初步方案**：

| 优化项 | 说明 |
|---|---|
| A. 日报失败摘要 | generate_daily_report 返回值中增加 `failure_summary` 字段（按维度统计） |
| B. error_logs 增强 | 采集失败时写入 error_logs 表（含 stock_id + error_type + 详细 message + traceback） |
| C. 前端可视化 | 日报生成结果中展示"X只成功/Y只失败/失败原因列表" |

---

## 三、架构师需裁定的决策点

### DP-1：日志文件架构 — TimedRotatingFileHandler vs 其他

**PM倾向**：`TimedRotatingFileHandler(when='midnight', backupCount=7)`

**需架构师裁定**：
- 是否在 `app.py main()` 中全局配置？还是在各模块独立配置？
- 备份保留天数：7天是否足够？
- 日志级别策略：全部INFO？还是 production 用 WARNING + 关键路径用 INFO？
- 是否需要分模块日志文件（如 `logs/collector.log` + `logs/advisor.log`）？还是统一 `app.log`？
- `basicConfig` 冲突：多个模块都调了 `basicConfig`，如何确保 root logger 不被覆盖？

### DP-2：日报进度追踪方式 — 日志 vs 进度文件 vs DB

**PM倾向**：日志为主（012-1的文件日志），进度文件为可选增强

**需架构师裁定**：
- 进度文件 `logs/report_progress.json` 是否必要？还是仅靠日志即可？
- 是否需要在 DB 中增加 `report_generation_log` 表记录每次生成？
- 是否需要 API 查询生成进度（`GET /api/daily-report/progress`）？

### DP-3：单只整体超时机制

**PM倾向**：在日报循环中为每只股票设超时上限

**需架构师裁定**：
- 单只超时上限设多少？（PM建议120s = 2分钟，含全部维度采集+分析）
- 实现方式：Python `signal.alarm`（仅Unix）、`threading.Timer`（通用但有开销）、还是 `concurrent.futures.ThreadPoolExecutor`？
- 超时后行为：skip该只 + 记录失败 + 继续下一只？
- 是否需要整体超时（如全部股票最长 30 分钟）？

### DP-4：重试次数调整

**PM倾向**：MAX_RETRIES 从 3 降为 2

**需架构师裁定**：
- 3次重试是否过度？2次是否足够？
- 是否应区分维度（K线3次、基本面2次、资金面1次）？
- 还是保持3次不变，靠整体超时来兜底？

### DP-5：error_logs 表增强

**PM倾向**：采集失败时统一写入 error_logs

**需架构师裁定**：
- 是否需要在 `collect_stock_data` 中统一 catch 并写入 error_logs？
- error_logs 表当前结构（stock_id + error_type + error_message + created_at）是否需要增加字段（如 dimension、traceback）？
- 是否需要增加日报失败摘要 API 供前端展示？

---

## 四、红线清单（架构师评审时需注意不可违反）

| 红线 | 说明 | 位置 |
|---|---|---|
| `data_collector.py` L1645/L1684/L1717 | 三处 `if False` 硬禁用 | 资金面估算源 |
| `advisor.py` `generate_advice` | 函数签名不可修改 | L869 |
| `advisor.py` `_build_capital_factors` | 不可修改 | L785 |
| `config_weights.json` | rating_mapping 80/65/50/30 不可修改 | |
| 零代码约束 | 无新 pip 依赖（当前8包） | |
| `scoring_engine.py` | v5引擎不可修改 | |
| 011 增量逻辑 | 012 不得破坏 011 的增量跳过逻辑 | |

---

## 五、验收标准

架构师评审报告需包含：

1. **每个决策点（DP-1~DP-5）的明确裁定**：采纳/修改/否决 + 理由
2. **日志架构方案确认**：FileHandler 配置方式 + 各模块兼容性评估
3. **超时机制安全性评估**：signal vs threading vs ThreadPoolExecutor 的权衡
4. **改动范围确认**：列出需要修改的文件清单
5. **开发任务书建议**：是否可拆分为独立子任务

---

## 六、参考资料

| 文件 | 用途 |
|---|---|
| `modules/daily_report.py` L361 | `generate_daily_report` 日报生成入口 |
| `modules/daily_report.py` L51 | `_scheduler_tick` 定时器回调 |
| `modules/daily_report.py` L85 | `start_scheduler` 调度器启动 |
| `modules/data_collector.py` L151 | `basicConfig` 日志配置 |
| `modules/data_collector.py` L2332 | `collect_stock_data` 采集入口 |
| `app.py` L3534 | `main()` 启动函数 |
| `app.py` L2887 | 日报生成 API 路由 |
| `config.py` L25 | `MAX_RETRIES = 3` |
| `config.py` L28 | `REQUEST_TIMEOUT = 15` |
| `logs/rollback_audit.log` | 现有日志文件（参考格式） |
| `error_logs` 表 | 39条记录（含 KeyError 诊断线索） |

---

> **PM 备注**：本任务书已内嵌角色定义，监理可直接全文粘贴到 Quests 窗口。架构师评审通过后，PM 将据此签发 012 开发任务书。
