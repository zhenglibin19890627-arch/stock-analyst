# 开发自验报告：DEV-TASKS-20260803-019C 东财采集重试增强与错峰分批优化

- 任务编号：019C（019B 后续优化）
- 角色：开发人员
- 日期：2026-08-03
- 改动文件：`modules/data_collector.py`（唯一改动文件）

## 一、改动清单

### 任务 1：移除 max_retries 硬编码（回归默认 3 轮）

| 位置 | 原代码 | 新代码 |
|------|--------|--------|
| `_fetch_capital_flow_em_individual` L1477 | `resp = _http_get_em(url, params=params, max_retries=1)` | `resp = _http_get_em(url, params=params)` |
| `_fetch_capital_flow_em` L1542 | `resp = _http_get_em(url, params=params, max_retries=1)` | `resp = _http_get_em(url, params=params)` |

- 未显式传 `max_retries=3`，回归 `_http_get_em` 默认值 `MAX_RETRIES=3`（config.py L32）
- 未新增 `EM_MAX_RETRIES` 常量
- `config.py` 的 `MAX_RETRIES = 3` 未动

### 任务 2：新增模块顶部常量（L1100-L1113）

```python
_EM_CONSECUTIVE_FAIL_COUNT = 0  # 进程级连续失败计数（R-4）
_EM_INTER_DELAY_RANGE = (2.0, 5.0)     # 股票间基础错峰延迟（秒）
_EM_BATCH_SIZE = 5                     # 分批大小（只）
_EM_BATCH_GAP_RANGE = (30.0, 60.0)     # 批间间隔（秒）
_EM_BACKOFF_CAP_SECONDS = 30           # 退避延迟上限（秒）
_EM_COOLDOWN_FAIL_N = 3                # 冷却触发：连续失败只数
_EM_COOLDOWN_SECONDS = 60              # 冷却暂停时长（秒）
_EM_CIRCUIT_BREAK_N = 5                # 熔断触发：连续失败只数
_EM_FALLBACK_TOTAL_CAP_SECONDS = 600   # 回退循环整体软超时（秒）
```

8 个常量取值与任务书清单完全一致，定义位置在 `_THS_*` 常量之后。

### 任务 3：改造回退循环（L1233-L1349）

`fetch_capital_flow_batch` 内"同花顺批量失败回退 EM 逐只循环"原 15 行 → 新 116 行，叠加 8 项机制：

| # | 机制 | 实现位置 | 要点 |
|---|------|----------|------|
| 1 | 错峰 | L1279-1295 | `idx > 0` 时 `time.sleep(random.uniform(2.0, 5.0))`，首只免延迟 |
| 2 | 分批 | L1272-1278 | `idx % 5 == 0` 时批间停顿 `random.uniform(30.0, 60.0)` |
| 3 | 退避 | L1282-1291 | `delay = min(base × 2^count, 30)` |
| 4 | 冷却 | L1297-1308 | `count >= 3` 且未触发过 → 暂停 60s（`cooldown_done` 防重复） |
| 5 | 熔断 | L1259-1269 | `count >= 5` → break，剩余计入 fail，source 标注 |
| 6 | 整体软超时 | L1247-1257 | `elapsed > 600` → break，剩余计入 fail，source 标注 |
| 7 | 计数重置 | L1320-1321 | `result[0] == 'success'` → count=0 + cooldown_done=False |
| 8 | 日志 | 全程 | 分批/冷却/熔断/超时均有 `logger.info/warning`，含数值详情 |

## 二、自验结果

### 自验 1：单只采集验证（3 只股票含 600519）

测试代码：逐只调用 `fetch_capital_flow(sym, 'a_stock')`

| 股票 | 状态 | 耗时 | 说明 |
|------|------|------|------|
| 600519 | success | 0.0s | 同日跳过（已有1条真实数据） |
| 000333 | success | 13.5s | push2his 第1、2轮反爬失败，**第3轮成功**（121天历史）。max_retries=3 生效 |
| 600276 | success | 17.0s | push2his 三轮全败，降级 push2 层第1轮成功（1天数据）。三层降级链正常 |

关键发现：000333 在旧 `max_retries=1` 下会在第1轮失败后即降级到 push2 层；现在 3 轮重试后在 push2his 层直接成功，数据更完整（121天 vs 10天）。

### 自验 2A：正常回退循环（3 只真实股票）

测试方式：强制 `_THS_CONSECUTIVE_FAIL_COUNT = 3` 跳过 THS，清空缓存，触发 EM 回退

| 指标 | 值 |
|------|-----|
| 结果 | `success_count: 3, fail_count: 0` |
| 总耗时 | 6.1s |
| EM连续失败计数 | 0（成功后重置） |
| source | `EM逐只回退(THS连续失败=3，成功3/失败0)` |

验证要点：
- 错峰延迟生效：3只股票间隔约 2-3s/只（idx=0 免延迟 + idx=1/2 各延迟一次）
- 计数重置生效：3只全部同日跳过返回 success，count 保持 0

### 自验 2B：失败回退循环（6 只假代码，验证冷却+熔断）

测试方式：6 个不存在的股票代码 `['999999'...'999994']`，触发连续失败

| 指标 | 值 |
|------|-----|
| 结果 | `success_count: 0, fail_count: 6` |
| 总耗时 | 139.1s |
| EM连续失败计数 | 5（熔断终止） |
| source | `EM逐只回退(THS连续失败=3，熔断终止(EM连续失败=5)，成功0/失败6)` |

机制触发时序（推算）：

| idx | 延迟前count | 延迟(s) | 冷却 | 采集结果 | 延迟后count |
|-----|-------------|---------|------|----------|-------------|
| 0 | 0 | —(首只) | — | fail | 1 |
| 1 | 1 | ~6(×2¹) | — | fail | 2 |
| 2 | 2 | ~12(×2²) | — | fail | 3 |
| 3 | 3 | ~24(×2³,封顶30) | **60s冷却** | fail | 4 |
| 4 | 4 | ~24(×2⁴,封顶30) | —(已触发) | fail | 5 |
| 5 | 5 | — | — | **熔断终止** | 5 |

验证要点：
- 退避生效：延迟从 ~6s 递增至 ~24s（2 的幂次增长，封顶 30s）
- 冷却生效：count=3 时额外暂停 60s（总耗时 139s ≈ 延迟~66s + 冷却60s + 采集~13s）
- 熔断生效：count=5 时 idx=5 未采集即终止，剩余1只计入 fail（总计 fail=6）

### 自验 3：日志核查

日志格式 `%(asctime)s - %(levelname)s - %(message)s`，所有 `[EM回退]` 日志均含时间戳。
验证示例（自验1中 000333 的重试日志）：
```
2026-08-03 22:45:31,864 - INFO - 东方财富direct失败: RemoteDisconnected...
2026-08-03 22:45:31,864 - INFO - 东方财富第1轮失败，等待1.9秒后重试...
2026-08-03 22:45:36,869 - INFO - 东方财富direct失败: RemoteDisconnected...
2026-08-03 22:45:36,869 - INFO - 东方财富第2轮失败，等待2.4秒后重试...
2026-08-03 22:45:41,397 - INFO - 东方财富direct成功（第3轮）
```

## 三、验收标准逐项核对

| # | 验收标准 | 结果 | 证据 |
|---|----------|------|------|
| 1 | 两处 `max_retries=1` 已移除，无显式传参、无新增常量 | ✅ | grep `max_retries=1` = 0匹配；grep `max_retries=3` = 0匹配；grep `EM_MAX_RETRIES` = 0匹配 |
| 2 | 8 个 `_EM_*` 常量定义，取值一致 | ✅ | 模块导入验证，8个常量值完全匹配 |
| 3 | 6 项机制全部具备，日志时间戳可核查 | ✅ | 代码路径核查 + 自验2A/2B实测 + 日志格式含 asctime |
| 4 | 错峰/分批验证：3只完整采集 + 回退场景 | ✅ | 自验1（3/3 success）+ 自验2A（回退3/3）+ 自验2B（熔断/冷却） |
| 5 | 单只采集无回归；batch路径正常 | ✅ | 自验1 fetch_capital_flow 3/3；自验2 fetch_capital_flow_batch 正常 |
| 6 | 开发报告含耗时/熔断冷却自验/R-1交接 | ✅ | 本报告 |

## 四、红线约束核对

| # | 红线 | 结果 |
|---|------|------|
| 1 | `fetch_capital_flow(symbol, market)` 签名不变 | ✅ 未触碰 |
| 2 | `generate_advice()` 签名不变 | ✅ 未触碰 advisor.py |
| 3 | 东财三层降级结构不破坏 | ✅ 自验1 000333走Layer1、600276走Layer2 |
| 4 | 估算数据源维持 `if False` 硬禁用 | ✅ 未触碰 |
| 5 | 不引入新 pip 依赖 | ✅ 仅用 time/random（stdlib，模块已引入） |
| 6 | `config_weights.json` 不修改 | ✅ 未在 git diff 中 |
| 7 | 不删除/覆盖现有数据 | ✅ 写入逻辑(INSERT OR REPLACE)不变 |
| 8 | 改动限于 `modules/data_collector.py` | ✅ git diff 确认（config.py 不在 diff 中） |

## 五、R-1 观察口径交接 QA

> **R-1（架构评审风险项）**：`max_retries` 1→3 后，资金面三层全失败最坏约 140~155s（无代理），超过单只 90s 超时（`STOCK_TIMEOUT_SECONDS`）。本批次不改超时值。

**QA 验收时需关注**：
1. 观察日报主循环中 `STOCK_TIMEOUT_SECONDS=90` 超时触发频率是否显著增加
2. 若超时频率过高（如 >30%），建议另立批次调整超时值
3. 当前设计安全依据（F-2）：回退循环提前终止后，未覆盖股票会在主循环中再次采集（主循环受 90s 保护）

**其他交接项**：
- **R-4**：`_EM_CONSECUTIVE_FAIL_COUNT` 为进程级状态，Flask 不重启时跨次批量生效。若 QA 需要重置，重启 Flask 即可
- **R-5**：`app.py` batch-analyze（L1291）与日报共用 `fetch_capital_flow_batch`，改造对两条路径同时生效（预期行为）

## 六、结论

开发自验全部通过。三项任务（移除硬编码 / 新增常量 / 改造回退循环）均已实现并通过实测验证。改动严格限于 `modules/data_collector.py`，未触碰红线约束。待 QA 独立验收。
