# 开发提示词 019X：东财请求策略修复

**任务书**：`docs/tasks/dev_tasks_20260810_019X_em_request_strategy_fix.md`（权威依据，与本提示词冲突时以任务书为准）
**批次性质**：**生产代码改动**（区别于 019W 纯诊断），改动完成后须重启 app.py 生效。

---

## 一、你的任务

019W 诊断已收口，监理裁定实施建议 a。你负责落地三项修复（任务书第二节有完整规格）：

1. **T1 拉长失败重试退避**：`_http_get_em`（`modules/data_collector.py` 约 L187-250）轮间重试等待由 `uniform(1.5, 3.5)` 秒改为 **30s → 60s → 60s（各加 ±15% 随机抖动）**，轮数提至 4（仅本函数内部）。
2. **T2 采集错峰拆分**：`modules/daily_report.py` 调度由 16:10 单批全量，改为 **16:10 / 16:40 / 17:10 三窗各采资金流东财清单的 1/3**（按代码排序固定切分）；窗 1/窗 2 只采集，窗 3 结束后执行一次完整日报流程（挂载顺序与现状一致）。
3. **T3 放弃代理路径**：`config.py` 新增 `EM_USE_PROXY = False`，`_http_get_em` 据此只走直连；代理分支与 `_proxy_health` 代码保留、开关跳过。

## 二、动手前先读

- 任务书全文（红线与验收标准都在里面）；
- `modules/data_collector.py` L150-270（`_http_get_em` 与 `@retry` 装饰器）、L1380-1400 与 L1530-1620（EM 批采六项机制常量区）；
- `modules/daily_report.py` L60-185（`_scheduler_tick` / `_schedule_next` / `_schedule_capital_retry` 挂载结构）；
- 019W 诊断报告 §4-a（改动依据）：`docs/reports/diag_019W_em_anti_crawl_20260810.md`。

## 三、实现要点与禁区

1. **禁止修改全局 `MAX_RETRIES = 3`**——它同时服务新浪/腾讯源的 `@retry` 装饰器，改了会误伤其他数据源。轮数调整只在 `_http_get_em` 内部。
2. **六项机制常量一律不动**（`_EM_INTER_DELAY_RANGE`、`_EM_BATCH_SIZE`、冷却、熔断、`_EM_FALLBACK_TOTAL_CAP_SECONDS = 600` 等）。退避拉长后若更容易触发软超时截断，截断部分由现有补采链路（019Q M-5）兜底，**不得放大软超时**。
3. T2 的窗 2/窗 3 用一次性 daemon Timer 注册（与 `_schedule_capital_retry` 同型）；并发防护复用 `_generate_lock`，后窗触发时前窗未结束则跳过并记日志。
4. **不变项**：周日 20:00 权重优化定时器、30 分钟延迟补采机制、启动日志文案、降级链语义（东财三层 → sina_main → 估算兜底）、DB schema、评分评级逻辑。
5. 改动文件仅限：`config.py`、`modules/data_collector.py`、`modules/daily_report.py` 及本批次自测脚本。不得引入新依赖。

## 四、自测要求（写入自测报告，逐项给出证据）

1. **T1 退避时序**：离线模拟（mock/计时断言均可，不发真实请求）验证等待序列 30s/60s/60s±15%、总窗口 3~4 分钟；给出「退避拉长 × 批内只数 × 600s 软超时」的时序推算，说明最坏情况下截断几只、由补采兜底。
2. **T2 调度注册**：离线模拟三窗 Timer 注册与触发顺序（可 monkeypatch 时间或缩短间隔验证），贴出模拟日志；验证窗 3 后日报流程挂载顺序与现状一致。
3. **T3 开关**：断言 `EM_USE_PROXY=False` 时代码路径不触碰代理健康检查；开关置 True 时行为回退到原逻辑。
4. **限次真实探针**：真实东财请求 **≤15 次**、串行、间隔 ≥30 秒、**避开 16:00-17:00**；记录每次时间戳/HTTP 码/成败。能离线验证的绝不用真实请求。
5. **回归**：改动后 `python -c "import modules.data_collector, modules.daily_report"` 通过；零写库（正常采集除外）。

自测报告落位：`docs/reports/dev_selftest_019X_request_strategy_20260810.md`，须含上述全部证据原文（日志/输出/推算）。

## 五、红线（违反即打回）

- 零 DB schema 改动、零评分逻辑改动、零降级链语义改动；
- 真实请求超 15 次或撞 16:00-17:00 窗口即打回；
- 不动任务书范围外的任何文件（PM 用文件 mtime 核验）。

## 六、交付

完成后回报「开发完成」，附交付说明：**需重启 app.py 生效**、三窗切分规则、回滚方式（`EM_USE_PROXY` 开关 + 代码还原点）。PM 将按任务书第五节独立验收。
