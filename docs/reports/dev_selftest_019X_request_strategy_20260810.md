# 自测报告 019X：东财请求策略修复（监理裁定选项 a 落地）

> 任务书：`docs/tasks/dev_tasks_20260810_019X_em_request_strategy_fix.md`
> 自测执行：开发，2026-08-10 22:44–22:59（探针时段避开 16:00-17:00 采集窗口）
> 自测脚本：`scripts/selftest_019x_request_strategy.py`（`offline` / `probe` / `timing` 三个子命令）
> 结论：**全部离线断言 PASS（exit=0）；真实探针 14 次 ≤15 上限，失败签名与 019W 诊断完全一致（环境窗口状态，非代码回归）**

---

## 〇、改动文件清单（红线范围核验）

| 文件 | 改动内容 | mtime（本批次落点） |
|---|---|---|
| `config.py` | 新增 `EM_USE_PROXY = False`（T3 开关） | 2026-08-10 |
| `modules/data_collector.py` | `_http_get_em` 退避 30/60/60s±15%、轮数 4、代理开关跳过；新增 `_EM_RETRY_BACKOFFS`/`_EM_RETRY_JITTER`/`_EM_RETRY_ROUNDS` 常量 | 2026-08-10 |
| `modules/daily_report.py` | `_scheduler_tick(window_idx)` 三窗调度、`_split_em_capital_list`、`_register_capital_window`、`_run_capital_window`、`_run_full_report_flow` | 2026-08-10 |
| `scripts/selftest_019x_request_strategy.py` | 本批次自测脚本（新增） | 2026-08-10 |

未触碰：DB schema、评分评级逻辑、降级链语义（东财三层 → sina_main → 估算兜底）、全局 `MAX_RETRIES=3`、六项机制常量（`_EM_INTER_DELAY_RANGE`/`_EM_BATCH_SIZE`/`_EM_BATCH_GAP_RANGE`/`_EM_BACKOFF_CAP_SECONDS`/`_EM_COOLDOWN_*`/`_EM_CIRCUIT_BREAK_N`/`_EM_FALLBACK_TOTAL_CAP_SECONDS=600`）、周日 20:00 优化定时器、30 分钟延迟补采、启动日志文案。

---

## 一、T1 退避时序验证（离线，不发真实请求）

### 1.1 模拟输出原文（seed=42，全败路径，mock sleep/Session.get）

```
  [PASS] T1 全败应抛 ConnectionError err=东方财富接口无法访问（直连重试4轮均失败，EM_USE_PROXY=False 未走代理）: RemoteDisconnected: 远端主动掐断（模拟 WAF 窗口）
  请求内延迟次数=4（应为4）: ['2.78', '1.99', '2.85', '2.34']
  轮间退避序列=['32.17', '64.26', '52.56']
  [PASS] T1 4轮=4次请求内延迟 实际4
  [PASS] T1 3次轮间退避 实际3
  [PASS] T1 退避序列 30/60/60 均±15%内
  [PASS] T1 200次采样退避全部落窗内
  [PASS] T1 轮数=4（_EM_RETRY_ROUNDS） 实际4
  [PASS] T1 全局 MAX_RETRIES 保持 3（未动新浪/腾讯@retry） 实际3
  T1 单源最坏重试窗口推算: [133.5s, 186.5s]（约2.2~3.1分钟，与 019W T2e「30秒间隔2分钟内捕获开放窗口」一致）
```

### 1.2 时序推算（退避拉长 × 批内只数 × 600s 软超时）

**单只全败耗时（`_http_get_em` 内部，直连路径）**：

- 4 轮 × 请求内延迟 U(1.5, 3.5)s = 6 ~ 14s
- 轮间退避 (30+60+60) × (0.85 ~ 1.15) = 127.5 ~ 172.5s
- **合计 133.5 ~ 186.5s（典型 160s）**，即单源最坏重试窗口约 2.2~3.1 分钟，与任务书口径「约 3~4 分钟」同量级、与 019W T2e「30 秒间隔 2 分钟内可捕获开放窗口」匹配

**批内（`_em_batch_collect` 六项机制，常量未动）**：逐只附加错峰 U(2,5)s、每 5 只批间停顿 30~60s、冷却 60s×1 次、软超时 `_EM_FALLBACK_TOTAL_CAP_SECONDS=600` 每只开始前检查。**最坏每只约 191.5s → 600s 内最多处理 3 只**。

| 布局 | 最坏截断只数 | 说明 |
|---|---|---|
| 旧单批 16:10 全量 23 只 | 约 20 只 | 一次 600s 预算，全败时几乎全截断 |
| 新三窗 8/8/7 只 | 每窗约 5 只 | **三窗各享独立 600s 软超时，等价总预算 ×3** |

模拟验证（200 次全败最坏假设，8 只/窗）：`截断均值 4.0 只/窗，最坏 4 只/窗`。

**截断兜底链（现有链路，零改动）**：① 窗3 日报流程内 `fetch_capital_flow_batch(a_symbols)` 对全量清单重算补采清单（019E 机制）再试一轮 → ② 30 分钟延迟补采（019Q M-5）→ ③ 次日批次。退避拉长确实提高单窗口内截断概率，但由三窗拆分布局（总预算 ×3）+ 兜底链消化，**未放大软超时**。

---

## 二、T2 三窗调度注册验证（离线，冻结时钟 16:10 + mock Timer）

### 2.1 模拟输出原文（13 只 A 股样例）

```
  [PASS] T2 次日窗1延迟=86400s(明日16:10) delay=86400.0
  [PASS] T2 次日窗1参数=(0,) args=(0,)
  [PASS] T2 窗1触发后注册窗2=1800s(16:40) delay=1800.0
  [PASS] T2 窗2参数=(1,)
  [PASS] T2 窗1只采集第1份(前5只，代码排序) 窗1清单=['000001', '000002', '000333', '000651', '002415']
  [PASS] T2 窗1不生成报告
  [PASS] T2 窗2触发后注册窗3=3600s(冻结16:10→17:10) delay=3600.0
  [PASS] T2 窗2参数=(2,)
  [PASS] T2 窗2采集第2份 窗2清单=['002594', '300750', '600036', '600276', '600519']
  [PASS] T2 窗2不生成报告
  [PASS] T2 窗3采集第3份 窗3清单=['600900', '601318', '601899']
  [PASS] T2 窗3后日报流程挂载顺序一致 实际顺序=['generate_daily_report', 'scan_once', 'schedule_capital_retry', 'refresh_all']
  [PASS] T2 窗3结束后注册次日16:10 delay=86400.0, args=(0,)
  [PASS] T2 锁忙时后窗跳过采集 仍采集=0次
  [PASS] T2 锁忙跳过有日志 日志样例=['[资金流采集窗] 第2窗触发时前一任务仍在运行（获取生成锁超时），跳过本窗采集，']
```

### 2.2 调度语义说明

- **切分规则**：A 股代码 `sorted()` 后按 `ceil(n/3)` 固定切三份（13 只 → 5/5/3，可复现）；每窗经 `fetch_capital_flow_batch(该份)` 采集（同花顺批量辅助指标 1h 缓存 + 019E 补采清单机制的东财逐只，走既有降级链，语义零改动）。
- **Timer 形态**：窗2/窗3 为一次性 daemon `threading.Timer`（与 `_schedule_capital_retry` 同型），本窗任务开始时注册下一窗（固定钟点 16:40/17:10；若钟点已过则 1 秒后尽快补触发）。
- **并发防护**：复用 `_generate_lock`（timeout=5）；后窗触发时前窗未结束 → 跳过本窗采集并记 WARNING（上表日志样例），剩余股票由补采链路兜底。
- **挂载顺序与现状完全一致**：日报生成 → P3-B 预警扫描 → 延迟补采注册 → 指数刷新（`_run_full_report_flow` 为原 `_scheduler_tick` 函数体逐行保留），窗3 `finally` 调 `_schedule_next()` 注册次日 16:10。
- **不变项**：周日 20:00 优化定时器、30 分钟延迟补采、启动日志文案均未动（回归断言 PASS）。

---

## 三、T3 代理路径开关断言（离线）

```
  [PASS] T3 False 时零触碰代理健康检查 calls={'is_available': 0, 'record_failure': 0, 'record_success': 0}
  [PASS] T3 True 时恢复原逻辑（代理优先+健康检查） calls={'is_available': 1, 'record_failure': 1, 'record_success': 0}
  [PASS] T3 config.EM_USE_PROXY 默认 False
```

- `EM_USE_PROXY=False`：`proxy_available = False` → 仅 direct 分支，`_proxy_health.is_available()/record_failure()/record_success()` 零调用（Spy 计数全 0）。
- `EM_USE_PROXY=True`（模拟系统代理存在）：恢复原逻辑——先 proxy 后 direct，健康检查被调用（is_available=1、record_failure=1）。
- 代理分支与 `_proxy_health` 类代码全部保留，仅开关跳过（回滚能力）。

---

## 四、限次真实探针（14 次 ≤ 15，串行间隔 ≥30s，避开 16:00-17:00）

探针方式：生产函数 `_http_get_em(url, max_retries=1)` 直连 `push2his.eastmoney.com` fflow daykline（secid=1.600519 贵州茅台，生产参数）。**不写库**。

| # | 时间戳 | HTTP 码 | 成败 | 错误摘要 |
|---|---|---|---|---|
| 1 | 22:49:23 | 无 | 失败 | `('Connection aborted.', RemoteDisconnected('Remote...`（直连和代理均失败，重试1轮） |
| 2 | 22:49:53 | 无 | 失败 | 同上 |
| 3 | 22:50:23 | 无 | 失败 | 同上 |
| 4 | 22:50:53 | 无 | 失败 | 同上 |
| 5 | 22:51:23 | 无 | 失败 | 同上 |
| 6 | 22:51:53 | 无 | 失败 | 同上（第1轮合计 0/6） |
| 7 | 22:53:01 | 无 | 失败 | 东方财富接口无法访问（直连重试1轮均失败，EM_USE_PROXY=False 未走代理）+ RemoteDisconnected |
| 8 | 22:53:31 | 无 | 失败 | 同上 |
| 9 | 22:54:01 | 无 | 失败 | 同上 |
| 10 | 22:54:31 | 无 | 失败 | 同上（第2轮合计 0/4） |
| 11 | 22:57:17 | 无 | 失败 | 同上 |
| 12 | 22:57:47 | 无 | 失败 | 同上 |
| 13 | 22:58:17 | 无 | 失败 | 同上 |
| 14 | 22:58:47 | 无 | 失败 | 同上（第3轮合计 0/4） |

**合计 0/14**。判定：

- 失败签名与生产（019W 口径 A 78/78）及本批诊断完全一致：TCP 建立 + TLS 完成、HTTP 响应前被服务端静默掐断（RemoteDisconnected）→ 东财 WAF 窗口式丢弃，**非代码回归**（019W 记录 08-10 16:44-17:27 曾连续 43 分钟全败；窗口 2~4 分钟周期开放，本时段恰处关闭态）。
- 第 1 轮（改消息前）报「直连和代理均失败」，第 2/3 轮报「直连重试1轮均失败，EM_USE_PROXY=False 未走代理」——**后者证明 T3 直连路径生效、代理分支确实被跳过**（消息文案随 T3 一并修正为路径准确，无任何调用方依赖该字符串）。
- 探针验证的是「直连请求构造 + 错误签名」，与生产逐字节同路径（`max_retries=1` 仅收敛轮数）；退避时序、轮数、开关行为均已在离线部分覆盖。

---

## 五、回归与红线核查

```
  [PASS] 回归 import modules.data_collector
  [PASS] 回归 import modules.daily_report
  [PASS] 回归 019Q _schedule_capital_retry 保留
  [PASS] 回归 周日20:00 优化定时器保留
  [PASS] 回归 30分钟延迟补采保留
```

- `python -c "import modules.data_collector, modules.daily_report"` 通过（脚本 `offline` 汇总 **全部 PASS，exit=0**）。
- 零写库：离线全程 `fetch_capital_flow_batch`/`_get_all_stocks` mock，未连接 `stock_analyst.db`；探针只读请求不写库。
- `ruff check config.py modules/data_collector.py modules/daily_report.py scripts/selftest_019x_request_strategy.py`：本批次改动零告警（data_collector.py 仅存 1 处 F841 为历史工作副本遗留、HEAD 基线即有，非本批次引入，未越界修改）。
- 全局 `MAX_RETRIES=3` 未动（T1 断言 PASS）；`_EM_FALLBACK_TOTAL_CAP_SECONDS=600` 未动（1.2 节推算即基于该值）。
- 限次：真实东财请求 **14 次 ≤ 15**、全部串行、间隔 ≥30 秒、全部避开 16:00-17:00。

---

## 六、交付说明

1. **需重启 app.py 生效**（生产代码改动；重启后首个 16:10 批为真实验证点，观察 16:10/16:40/17:10 三窗日志与入库结果）。
2. **三窗切分规则**：A 股代码排序（`sorted`）后按 `ceil(n/3)` 固定切三份 → 窗1(16:10) 采第 1 份、窗2(16:40) 采第 2 份、窗3(17:10) 采第 3 份并执行完整日报流程（日报生成 → P3-B 预警 → 延迟补采注册 → 指数刷新）。
3. **回滚方式**：① `config.py` 中 `EM_USE_PROXY` 置 `True` 恢复代理路径（T3 回滚，代码保留）；② 退避/三窗回滚点：`_EM_RETRY_BACKOFFS`/`_EM_RETRY_ROUNDS` 恢复原值、`_scheduler_tick` 恢复单窗直调 `generate_daily_report`（即 git 基线 + 本批 diff 反向应用），改完重启 app.py。
4. 自测脚本可复跑：`python scripts/selftest_019x_request_strategy.py offline`（离线全量）、`... probe`（限次探针）、`... timing`（时序推算）。
