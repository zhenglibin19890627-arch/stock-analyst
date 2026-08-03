# 012 日志系统建设+日报生成诊断+采集健壮性优化 — 架构评审报告

> **评审人**：架构师 | **评审日期**：2026-07-30 | **状态**：评审完成

---

## 评审结论总览

| 决策点 | 裁定 | 一句话理由 |
|--------|------|-----------|
| DP-1 文件日志架构 | **修改后采纳** | 方案方向正确，需处理 basicConfig 冲突 + 增加启动 banner |
| DP-2 日报进度追踪 | **修改后采纳** | 日志为主，进度文件采纳，DB表和API否决 |
| DP-3 单只整体超时 | **修改后采纳** | 超时必要，但实现方式改为 ThreadPoolExecutor + 超时值调整 |
| DP-4 重试次数调整 | **否决** | 保持 MAX_RETRIES=3 不变，靠整体超时兜底 |
| DP-5 error_logs 增强 | **修改后采纳** | 增加 dimension + traceback 字段，统一 catch 写入 |

---

## DP-1：文件日志架构

### 裁定：修改后采纳

### 1.1 全局配置位置 — 采纳 PM 方案

**裁定**：在 `app.py main()` 中、`init_database()` 之前配置全局 FileHandler。

**理由**：
- `main()` 是唯一的进程入口，此处配置可确保所有后续模块继承
- 标准库 `logging` 的层级继承机制：所有 `getLogger(__name__)` 创建的子 logger 自动向 root logger 传播
- 无需修改任何模块文件，符合最小改动原则

### 1.2 basicConfig 冲突处理 — 必须修改

**问题发现**：代码中有 **8处** 模块级 `logging.basicConfig()` 调用：

| 文件 | 行号 | 调用位置 |
|------|------|---------|
| data_collector.py | L151 | 模块顶层 |
| advisor.py | L26 | 模块顶层 |
| analysis_engine.py | L28 | 模块顶层 |
| engine_switcher.py | L413 | `if __name__` 块 |
| data_adapter.py | L469 | `if __name__` 块 |
| alert_engine.py | L380 | `if __name__` 块 |
| scoring_engine.py | L1210 | `if __name__` 块 |
| daily_report.py | L795 | `if __name__` 块 |

**关键事实**：Python `logging.basicConfig()` 仅在 root logger 无 handler 时生效（首次调用 wins）。由于 `app.py main()` 在 import 模块之前执行配置，模块级 `basicConfig` 调用时 root logger 已有 handler → **这些调用自动变为 no-op，不会覆盖**。

**裁定**：
- **不需要删除**各模块的 `basicConfig` 调用（它们作为独立运行时的 fallback 仍有价值）
- 但需确保 `app.py main()` 中的配置在 **任何模块 import 之前**执行
- 具体实现：将日志配置代码放在 `main()` 函数体的**第一行**（在 `init_database()` 之前、在 `from modules.daily_report import start_scheduler` 之前）

**注意**：`app.py` 顶部可能有 `from modules.xxx import yyy` 的全局 import。需确认这些 import 不会在 `main()` 执行前触发模块级 `basicConfig`。经核查，`app.py` 顶部的 import 确实会先执行模块级代码，但由于 `basicConfig` 的 "首次 wins" 语义，只要我们在 `main()` 中**先于** `start_scheduler()` 配置即可——因为 `basicConfig` 在模块 import 时已经执行过了（root logger 此时已有 StreamHandler），我们在 `main()` 中应该使用 `logging.getLogger().addHandler(file_handler)` 而非 `basicConfig`。

**最终方案**：
```python
def main():
    # === 012: 全局文件日志配置（必须在业务逻辑之前） ===
    import logging
    from logging.handlers import TimedRotatingFileHandler

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)

    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, 'app.log'), when='midnight', backupCount=7, encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s %(message)s'))
    file_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)  # addHandler 而非 basicConfig，避免冲突
    # === 012 END ===

    # 原有逻辑...
    init_database()
```

### 1.3 备份保留天数

**裁定**：7天 → **采纳**。

**理由**：
- 日报每日18:00生成一次，7天日志足够覆盖一个工作周的排查窗口
- 日志量估算：27只股票 × 6维度 × ~3行/维度 ≈ 500行/次，加上 Flask 请求日志，每日约 2000~5000 行 ≈ 200~500KB
- 7天 × 500KB = 3.5MB，磁盘占用可忽略
- 零代码用户不需要过长保留期，7天足够

### 1.4 日志级别策略

**裁定**：全部 INFO → **采纳**。

**理由**：
- 当前系统处于功能完善期，INFO 级别能提供充分的诊断信息
- 日志量可控（非高并发服务），不存在 INFO 日志爆炸风险
- 零代码用户需要"打开文件就能看到发生了什么"，WARNING 级别信息太少
- 未来如需调整，只需改 `root.setLevel()` 一行

### 1.5 是否分模块日志文件

**裁定**：统一 `app.log` → **采纳 PM 方案，不拆分**。

**理由**：
- 日报流程跨模块调用链：daily_report → data_collector → advisor → scoring_engine
- 统一文件可按时间线完整还原调用链，分文件反而增加排查难度
- 日志量小（<500KB/天），无性能瓶颈
- 零代码用户只需看一个文件

### 1.6 启动 banner 日志

**补充要求**：在 `main()` 日志配置完成后，立即输出一行启动标记：
```python
logging.getLogger(__name__).info(f'===== Stock Analyst 启动 PID={os.getpid()} =====')
```
便于在日志文件中快速定位每次重启的起点。

---

## DP-2：日报进度追踪方式

### 裁定：修改后采纳

### 2.1 进度日志 — 采纳

**裁定**：在 `generate_daily_report` 循环中增加结构化进度日志。

**格式规范**：
```
[日报进度] 3/27 开始 600276 恒瑞医药
[日报进度] 600276 采集完成 kline=ok fundamental=ok capital=ok north=skip margin=ok sentiment=ok
[日报进度] 600276 分析完成 score=72.5 rating=推荐买入
[日报进度] ===== 批次完成 成功25/失败2 耗时185s =====
```

**理由**：
- 配合 DP-1 的文件日志，卡住时查看 `logs/app.log` 最后一条 `[日报进度]` 即可定位
- 无额外复杂度，仅在现有循环中加 3 行 `logger.info`
- 增加批次总耗时统计（`time.time()` 差值），便于判断是否异常

### 2.2 进度文件 `logs/report_progress.json` — 采纳

**裁定**：采纳，但简化为**仅记录最近一次**（非追加）。

**理由**：
- 日志文件需要"搜索"才能找到进度，进度文件提供"一目了然"的当前状态
- 零代码用户可以直接用记事本打开 JSON 查看"现在跑到哪了"
- 写入频率低（每只股票更新一次，27只 = 27次文件写入），无 IO 压力
- 使用 `json.dump` 覆盖写入（非追加），文件始终反映最新状态

**结构确认**（与 PM 方案一致）：
```json
{
    "date": "2026-07-30",
    "total": 27,
    "current": 15,
    "current_symbol": "600276",
    "current_name": "恒瑞医药",
    "status": "running",
    "started_at": "2026-07-30 18:00:00",
    "last_update": "2026-07-30 18:03:25",
    "finished_at": null
}
```

完成后更新 `status: "done"` + `finished_at`。

### 2.3 DB 表 `report_generation_log` — 否决

**裁定**：不新增 DB 表。

**理由**：
- `daily_reports` 表已有 `status='failed'` + `error_msg` 字段，失败信息已入库
- 进度文件 + 文件日志已覆盖"生成过程追踪"需求
- 新增表增加 schema 维护成本，对零代码用户无直接价值
- 如未来需要历史生成记录，可从 `daily_reports.generated_at` 字段反推

### 2.4 API `GET /api/daily-report/progress` — 否决

**裁定**：本期不增加进度查询 API。

**理由**：
- 日报生成为同步阻塞调用（POST 触发 → 等待完成 → 返回结果）
- 前端当前无"进度条"UI，增加 API 无消费方
- 如未来需要，进度文件可作为 API 数据源，扩展成本极低
- 避免过度设计

---

## DP-3：单只整体超时机制

### 裁定：修改后采纳

### 3.1 超时上限

**裁定**：单只 **90秒**（非 PM 建议的 120s）。

**理由**：
- 正常单只采集耗时估算：6维度 × (请求15s + 重试间隔1s) × 最坏2次重试 ≈ 理论上限 ~192s
- 但实际上：K线/基本面/资金面通常 2~5s 完成，仅 margin_balance 可能逐日请求
- 011 增量逻辑已大幅减少重复请求，正常情况单只 <30s
- 90s 已留出 3 倍余量，超过 90s 基本可判定为网络异常/接口卡死
- 27只 × 90s = 最坏总耗时 40.5 分钟（可接受）

### 3.2 实现方式

**裁定**：`concurrent.futures.ThreadPoolExecutor` + `future.result(timeout=90)`

**否决 signal.alarm**：
- 仅 Unix 可用，Windows 不支持 → 违反零代码用户（Windows）约束

**否决 threading.Timer**：
- Timer 只能触发回调，无法中断正在执行的阻塞 IO
- 需要额外协作机制（设置标志位让工作线程检查），侵入性大

**采纳 ThreadPoolExecutor 方案**：
```python
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout

with ThreadPoolExecutor(max_workers=1) as executor:
    future = executor.submit(_process_single_stock, stock, target_date, force)
    try:
        result = future.result(timeout=STOCK_TIMEOUT_SECONDS)  # 90s
    except FuturesTimeout:
        logger.error(f'[日报进度] {symbol} 超时({STOCK_TIMEOUT_SECONDS}s)，跳过')
        # 记录失败，继续下一只
```

**安全性评估**：
- ThreadPoolExecutor 超时后，工作线程**不会被杀死**（Python 限制），但主循环继续
- 被遗弃的线程最终会因网络超时（REQUEST_TIMEOUT=15）自行结束
- 最坏情况：多个超时线程堆积，但由于是串行提交（max_workers=1），同一时刻最多 1 个遗弃线程
- 风险可控，不会导致资源泄漏

### 3.3 超时后行为

**裁定**：skip + 记录失败 + 继续下一只。

具体行为：
1. `logger.error(f'[日报进度] {symbol} 超时跳过')`
2. 写入 `daily_reports` 表 `status='failed', error_msg='采集超时(90s)'`
3. 写入进度文件（标记该只为 timeout）
4. 继续 for 循环下一只

### 3.4 整体超时

**裁定**：增加整体超时 **30分钟**，但实现为**软超时**（检查已用时间，超过则停止处理剩余股票）。

```python
BATCH_TIMEOUT_SECONDS = 1800  # 30分钟
batch_start = time.time()

for stock in stocks:
    if time.time() - batch_start > BATCH_TIMEOUT_SECONDS:
        logger.warning(f'[日报进度] 批次整体超时({BATCH_TIMEOUT_SECONDS}s)，剩余{remaining}只跳过')
        break
    # ... 正常处理
```

**理由**：
- 软超时实现简单（一行 if），无需额外线程
- 27只 × 90s 最坏 = 40.5min > 30min，整体超时作为最后防线
- 正常情况下 27只 × 30s = 13.5min，不会触发

### 3.5 超时配置化

**裁定**：在 `config.py` 中新增两个常量：
```python
# 012: 日报生成超时配置
STOCK_TIMEOUT_SECONDS = 90  # 单只股票处理超时（秒）
BATCH_TIMEOUT_SECONDS = 1800  # 批次整体超时（秒）
```

---

## DP-4：重试次数调整

### 裁定：否决（保持 MAX_RETRIES=3 不变）

### 理由

1. **DP-3 已兜底**：单只 90s 超时 + 批次 30min 超时，即使 3 次重试全部耗尽也不会无限卡住
2. **网络抖动场景**：akshare/东方财富接口偶尔丢包，3 次重试是经验值，降为 2 次会显著增加"假失败"概率
3. **重试间隔短**：当前 `retry` 装饰器 delay=1s，3 次重试额外等待仅 2s，对总耗时影响极小
4. **不区分维度**：
   - 区分维度重试次数（K线3次、资金面1次）增加配置复杂度
   - 各 fetch 函数已有独立的 try/except 降级（失败返回 `('failed', msg)` 而非抛异常）
   - 评分引擎已有 data_warnings 降级机制，单维度失败不影响整体评分
5. **最小改动原则**：改 MAX_RETRIES 影响全局所有网络请求，风险收益比不合理

### 替代建议

如未来观察到特定接口（如 margin_balance）频繁超时，可在该函数内部局部覆盖：
```python
@retry(max_retries=2, delay=0.5)  # 局部覆盖
def fetch_margin_balance(...):
```
而非修改全局配置。

---

## DP-5：error_logs 表增强

### 裁定：修改后采纳

### 5.1 统一 catch 写入 error_logs — 采纳

**裁定**：在 `collect_stock_data` 的各维度异常处理中，统一写入 `error_logs` 表。

**当前问题**：
- `collect_stock_data` 中 north_capital/margin_balance 的异常仅 `logger.warning`，不写 DB
- `daily_report.py` L523 的 catch 仅写 `daily_reports.error_msg`，不写 `error_logs`
- 导致"日志关了就无法追溯"

**改动方案**：在 `data_collector.py` 中增加辅助函数：
```python
def _log_error_to_db(
    stock_id, module, error_type, error_message, dimension=None, traceback_str=None
):
    """统一写入 error_logs 表"""
    try:
        conn = get_connection()
        conn.execute(
            'INSERT INTO error_logs (stock_id, module, error_type, error_message, dimension, traceback) VALUES (?,?,?,?,?,?)',
            (stock_id, module, error_type, error_message, dimension, traceback_str),
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # 写日志失败不阻塞业务
```

### 5.2 表结构增强 — 采纳（增加 2 个字段）

**裁定**：`ALTER TABLE` 增加 `dimension` 和 `traceback` 字段。

```sql
ALTER TABLE error_logs ADD COLUMN dimension TEXT;      -- 失败维度：kline/fundamental/capital/north/margin/sentiment
ALTER TABLE error_logs ADD COLUMN traceback TEXT;      -- 完整堆栈（可选，截断至2000字符）
```

**理由**：
- `dimension` 便于按维度统计失败率（"资金面失败占比多少？"）
- `traceback` 便于定位根因（当前 39 条 `'code'` KeyError 无堆栈，无法定位具体代码行）
- 使用 `ALTER TABLE ADD COLUMN`（SQLite 支持），无需重建表
- 在 `db_manager.py init_database()` 中增加 migration 逻辑（检查列是否存在再 ALTER）

### 5.3 日报失败摘要 — 采纳

**裁定**：`generate_daily_report` 返回的 summary dict 中增加 `failure_summary` 字段。

```python
'failure_summary': {
    'total_failed': 2,
    'by_reason': {
        '采集超时(90s)': ['00700'],
        "'code' KeyError": ['09988', '01810'],
    }
}
```

**理由**：
- 前端 API 已返回 `results` 列表，增加 `failure_summary` 仅多一个聚合字段
- 便于前端展示"X只成功/Y只失败/失败原因"（012-4-C 需求）
- 无额外 DB 查询，从内存 results 列表直接聚合

### 5.4 前端可视化 — 延后

**裁定**：本期仅提供数据（API 返回 failure_summary），前端展示作为后续 UI 任务。

**理由**：
- 012 核心目标是"日志可追溯"，前端展示为锦上添花
- 避免 012 范围膨胀

---

## 改动范围确认

### 需修改的文件清单

| 文件 | 改动内容 | 风险等级 |
|------|---------|---------|
| `app.py` main() | 增加全局 FileHandler 配置（~15行） | 低 |
| `config.py` | 增加 STOCK_TIMEOUT_SECONDS / BATCH_TIMEOUT_SECONDS | 低 |
| `modules/daily_report.py` | 进度日志 + 进度文件 + 超时机制 + failure_summary | **中** |
| `modules/data_collector.py` | 增加 `_log_error_to_db` + 各维度 catch 写入 | 低 |
| `database/db_manager.py` | error_logs 表 ALTER TABLE migration | 低 |

### 不需修改的文件

| 文件 | 理由 |
|------|------|
| `modules/advisor.py` | 红线：generate_advice 签名不可改 |
| `modules/scoring_engine.py` | 红线：v5引擎不可改 |
| `config_weights.json` | 红线：rating_mapping 不可改 |
| 其他模块的 `basicConfig` | 无需删除，自动 no-op |

### 红线合规检查

| 红线 | 合规状态 |
|------|---------|
| data_collector.py 三处 `if False` | ✅ 不触碰 |
| advisor.py generate_advice 签名 | ✅ 不修改 |
| advisor.py _build_capital_factors | ✅ 不修改 |
| config_weights.json rating_mapping | ✅ 不修改 |
| 零代码约束（无新 pip 依赖） | ✅ 全部使用标准库（logging.handlers, concurrent.futures, json, time） |
| scoring_engine.py v5引擎 | ✅ 不修改 |
| 011 增量逻辑 | ✅ 超时机制在 daily_report 层，不影响 data_collector 内部增量跳过 |

---

## 开发任务书建议

### 拆分方案：3 个独立子任务

| 子任务 | 内容 | 依赖 | 预估改动量 |
|--------|------|------|-----------|
| **012-A** | 文件日志系统（DP-1） | 无 | app.py +15行 |
| **012-B** | 日报进度追踪 + 超时机制（DP-2 + DP-3） | 012-A | daily_report.py +60行, config.py +3行 |
| **012-C** | error_logs 增强 + 失败摘要（DP-5） | 012-A | data_collector.py +25行, db_manager.py +15行, daily_report.py +15行 |

### 执行顺序

```
012-A（日志基础）→ 012-B（进度+超时）→ 012-C（错误增强）
```

- 012-A 是基础设施，B/C 依赖其文件日志能力
- B 和 C 理论上可并行，但建议串行（B 改动 daily_report.py 较多，避免合并冲突）

### 自验要点

| 子任务 | 自验方法 |
|--------|---------|
| 012-A | 启动服务 → 触发日报 → 检查 `logs/app.log` 存在且有内容 |
| 012-B | 触发日报 → 检查 `logs/report_progress.json` 实时更新 → 模拟超时（临时设 STOCK_TIMEOUT=5） |
| 012-C | 触发日报（含港股） → 查询 `SELECT * FROM error_logs ORDER BY id DESC LIMIT 5` 确认新字段有值 |

---

## 补充建议（非裁定，供 PM 参考）

1. **日志文件编码**：`encoding='utf-8'` 正确，Windows 记事本可正常打开
2. **Flask 请求日志**：Werkzeug 默认通过 `werkzeug` logger 输出请求日志，会自动被 root logger 捕获写入文件，无需额外配置
3. **磁盘空间**：7天 × ~500KB = 3.5MB，对零代码用户无感知
4. **未来扩展点**：如日志量增长，可考虑 `backupCount=14` 或增加 `RotatingFileHandler`（按大小轮转），但当前无必要

---

> **架构师签署**：评审完成，5 个决策点均已裁定。PM 可据此签发 012 开发任务书。
