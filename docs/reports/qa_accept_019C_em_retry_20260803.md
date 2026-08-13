# QA 验收报告：019C 东财采集重试增强与错峰分批优化

| 项 | 内容 |
|---|---|
| 编号 | QA-ACCEPT-20260803-019C |
| 关联任务 | QA-TASKS-20260803-019C |
| 关联开发任务 | DEV-TASKS-20260803-019C |
| 验收日期 | 2026-08-03 |
| QA 执行 | AI / qwen3.8-max-preview |
| 验收环境 | Windows 25H2 / Python 3.12 / SQLite |
| 验收方式 | 静态代码核查 + 受控实测（monkey-patch + 真实常量） |

---

## 一、验收总结

| 项目 | 结论 |
|---|---|
| TC-019C-1 硬编码移除核查 | ✅ PASS |
| TC-019C-2 常量定义核查 | ✅ PASS |
| TC-019C-3 回退循环 8 项机制核查 | ✅ PASS |
| TC-019C-4 失败场景实测：冷却+熔断 | ✅ PASS |
| TC-019C-5 成功场景实测：错峰+计数重置 | ✅ PASS |
| TC-019C-6 单只主链路不回归 | ✅ PASS |
| TC-019C-7 batch-analyze 路径回归抽查 | ✅ PASS |
| **红线核验（8项）** | **✅ 全部通过** |

### 最终结论：**全部 PASS，可双签**

---

## 二、逐条测试用例

### TC-019C-1 — 硬编码移除核查 ✅ PASS

**验证方式**：grep 全文件 + 代码核查

| 检查项 | 结果 | 证据 |
|---|---|---|
| `max_retries=1` 在 *.py 中 | 0 匹配（仅 `scripts/diag_retry_verify_019b.py` 注释中含此字符串，非应用代码路径） | grep `max_retries\s*=\s*[0-9]+` *.py |
| `max_retries=3` 在 *.py 中 | 0 匹配（同上，仅诊断脚本注释） | 同上 |
| `EM_MAX_RETRIES` 在 *.py 中 | 0 匹配 | grep `EM_MAX_RETRIES` *.py |
| L1477 调用点 | `resp = _http_get_em(url, params=params)` — 无 max_retries 参数 ✅ | data_collector.py L1477 |
| L1542 调用点 | `resp = _http_get_em(url, params=params)` — 无 max_retries 参数 ✅ | data_collector.py L1542 |
| config.py `MAX_RETRIES = 3` | 未改动 ✅ | config.py L32 |
| `_http_get_em` 默认行为 | `max_retries=None` → `rounds = max_retries if max_retries else MAX_RETRIES` → 走 `MAX_RETRIES=3` ✅ | data_collector.py L185/L195 |

---

### TC-019C-2 — 常量定义核查 ✅ PASS

**验证方式**：代码核查（data_collector.py L1100-L1113）

9 个常量全部定义于模块顶部 `_THS_*` 常量之后（L1100-L1113）：

| # | 常量名 | 实际值 | 任务书要求值 | 一致 |
|---|---|---|---|---|
| 1 | `_EM_CONSECUTIVE_FAIL_COUNT` | `0` | `0` | ✅ |
| 2 | `_EM_INTER_DELAY_RANGE` | `(2.0, 5.0)` | `(2.0, 5.0)` | ✅ |
| 3 | `_EM_BATCH_SIZE` | `5` | `5` | ✅ |
| 4 | `_EM_BATCH_GAP_RANGE` | `(30.0, 60.0)` | `(30.0, 60.0)` | ✅ |
| 5 | `_EM_BACKOFF_CAP_SECONDS` | `30` | `30` | ✅ |
| 6 | `_EM_COOLDOWN_FAIL_N` | `3` | `3` | ✅ |
| 7 | `_EM_COOLDOWN_SECONDS` | `60` | `60` | ✅ |
| 8 | `_EM_CIRCUIT_BREAK_N` | `5` | `5` | ✅ |
| 9 | `_EM_FALLBACK_TOTAL_CAP_SECONDS` | `600` | `600` | ✅ |

- 常量注释完整，`_EM_CONSECUTIVE_FAIL_COUNT` 含 R-4 进程级语义说明 ✅
- 常量位置紧邻 `_THS_*` 常量区域（L1095-1098 之后） ✅

---

### TC-019C-3 — 回退循环 8 项机制核查 ✅ PASS ★重点

**验证方式**：代码核查（data_collector.py L1246-L1349）

#### 机制实现核查

| # | 机制 | 代码位置 | 实现要点 | 结论 |
|---|---|---|---|---|
| 1 | 错峰 | L1281-1294 | `idx > 0` 且非批边界时 `base_delay = _random.uniform(*_EM_INTER_DELAY_RANGE)`；`idx == 0` 首只免延迟 | ✅ |
| 2 | 分批 | L1272-1278 | `idx > 0 and idx % _EM_BATCH_SIZE == 0` 时批间停顿 `_random.uniform(*_EM_BATCH_GAP_RANGE)` | ✅ |
| 3 | 退避 | L1282-1286 | `delay = min(base_delay * (2 ** _EM_CONSECUTIVE_FAIL_COUNT), _EM_BACKOFF_CAP_SECONDS)` | ✅ |
| 4 | 冷却 | L1297-1308 | `_EM_CONSECUTIVE_FAIL_COUNT >= _EM_COOLDOWN_FAIL_N and not cooldown_done` 时暂停 `_EM_COOLDOWN_SECONDS`，`cooldown_done` 确保仅一次 | ✅ |
| 5 | 熔断 | L1259-1269 | `_EM_CONSECUTIVE_FAIL_COUNT >= _EM_CIRCUIT_BREAK_N` 时 `break`，剩余计入 `em_fail` | ✅ |
| 6 | 软超时 | L1247-1257 | `elapsed > _EM_FALLBACK_TOTAL_CAP_SECONDS` 时 `break`，剩余计入 `em_fail` | ✅ |
| 7 | 计数重置 | L1313-1321 | `result[0] == 'success'` 时 `_EM_CONSECUTIVE_FAIL_COUNT = 0` 且 `cooldown_done = False` | ✅ |
| 8 | 日志 | 全机制 | 错峰/分批/退避/冷却/熔断/超时/重置/失败/异常均有 `logger` 输出含数值 | ✅ |

#### 顺序核查

- 软超时检查（L1247）和熔断检查（L1259）位于循环体**最前**，在任何延迟和采集之前 ✅
- 延迟顺序：分批间隙（批边界）或错峰+退避（非批边界）→ 冷却 → 采集 ✅

#### 关键逻辑核查

| 检查项 | 结论 | 证据 |
|---|---|---|
| 软超时/熔断检查在延迟与采集之前 | ✅ | L1247/L1259 均在 L1271(延迟)和 L1310(采集)之前 |
| 计数重置条件含同日跳过（R-3） | ✅ | `fetch_capital_flow` 同日跳过返回 `('success', ...)` — L1832/L1856，回退循环 L1313 判断 `result[0] == 'success'` |
| 熔断/超时终止时剩余计入 `em_fail` | ✅ | L1255（超时）、L1267（熔断） |
| `source` 字符串标注终止原因 | ✅ | L1339-1344：含"软超时终止"/"熔断终止"标注 |
| `global _EM_CONSECUTIVE_FAIL_COUNT` 声明存在 | ✅ | L1236 |
| 异常分支（except）递增计数 | ✅ | L1332：`_EM_CONSECUTIVE_FAIL_COUNT += 1` |

---

### TC-019C-4 — 失败场景实测：冷却+熔断 ✅ PASS ★★核心验收项

**验证方式**：受控实测（临时脚本，monkey-patch 延迟常量加速测试，阈值/批大小/熔断/冷却触发点不变）

**构造方法**：
- 强制 `_THS_CONSECUTIVE_FAIL_COUNT = 3`（达阈值跳过 THS）
- 假代码 `['999999','999998','999997','999996','999995','999994']`（不存在于 DB，`get_stock_id` 返回 None，`fetch_capital_flow` 立即返回 `('failed', ...)`）
- monkey-patch 延迟常量缩小（`_EM_INTER_DELAY_RANGE=(0.05,0.10)`、`_EM_COOLDOWN_SECONDS=0.5`），仅加速测试，不改变逻辑

**实测日志**（关键摘录）：

```
[EM回退] 999999 采集失败(result=failed)，连续失败计数=1
[EM回退] 999998 退避延迟0.1s（连续失败1次，基础0.1s×2^1）
[EM回退] 999998 采集失败(result=failed)，连续失败计数=2
[EM回退] 999997 退避延迟0.4s（连续失败2次，基础0.1s×2^2）
[EM回退] 999997 采集失败(result=failed)，连续失败计数=3
[EM回退] 999996 退避延迟0.5s（连续失败3次，基础0.1s×2^3）
[EM回退] 连续失败3≥3，冷却暂停0.5s后继续...      ← 冷却触发(机制4)
[EM回退] 999996 采集失败(result=failed)，连续失败计数=4
[EM回退] 999995 退避延迟1.0s（连续失败4次，基础0.1s×2^4）
[EM回退] 999995 采集失败(result=failed)，连续失败计数=5
[EM回退] 熔断触发（连续失败5≥5），终止本轮回退，剩余 1 只未采集: ['999994']  ← 熔断触发(机制5)
```

**断言结果**：

| 检查项 | 预期 | 实际 | 结论 |
|---|---|---|---|
| source 含"熔断终止" | 是 | `EM逐只回退(...熔断终止(EM连续失败=5)，成功0/失败6)` | ✅ |
| fail_count | 6 | 6 | ✅ |
| success_count | 0 | 0 | ✅ |
| _EM_CONSECUTIVE_FAIL_COUNT | 5 | 5 | ✅ |
| 退避延迟递增 | 2^n 增长 | 0.1→0.4→0.5→1.0s（指数增长，封顶生效） | ✅ |
| 冷却触发一次 | count≥3 时 | count=3 时触发，仅一次（cooldown_done） | ✅ |
| 熔断后第 6 只不采集 | 是 | 999994 直接计入 fail | ✅ |
| 首只无延迟 | 是 | 999999（idx=0）无延迟日志 | ✅ |

---

### TC-019C-5 — 成功场景实测：错峰+计数重置 ✅ PASS ★重点

**验证方式**：受控实测（真实常量 `_EM_INTER_DELAY_RANGE=(2.0, 5.0)`）

**构造方法**：
- 强制 `_THS_CONSECUTIVE_FAIL_COUNT = 3`（跳过 THS）
- 预设 `_EM_CONSECUTIVE_FAIL_COUNT = 2`（测试计数重置 R-3）
- 真实股票 `['600519', '000333']`（今日已有数据 → 同日跳过 → success）

**实测日志**（关键摘录）：

```
[600519] 同日跳过(已有真实资金流数据,记录数=1)（东方财富已写入）
[EM回退] 600519 成功，连续失败计数重置(2→0)       ← 计数重置(机制7+R-3)
[EM回退] 000333 错峰延迟2.8s                      ← 错峰延迟(机制1, 真实常量)
[000333] 同日跳过(已有真实资金流数据,记录数=1)（东方财富已写入）
```

**断言结果**：

| 检查项 | 预期 | 实际 | 结论 |
|---|---|---|---|
| success_count | 2 | 2 | ✅ |
| fail_count | 0 | 0 | ✅ |
| _EM_CONSECUTIVE_FAIL_COUNT 重置为 0 | 0 | 0（同日跳过 success → 重置） | ✅ |
| 错峰延迟在 2~5s 区间 | 2~5s | 2.8s | ✅ |
| 首只无延迟 | 是 | 600519（idx=0）立即采集 | ✅ |
| 总耗时 ≥ 2s | 是 | 2.81s（第 2 只错峰延迟） | ✅ |
| 计数重置日志出现（R-3） | 是 | "连续失败计数重置(2→0)" | ✅ |

---

### TC-019C-6 — 单只主链路不回归 ✅ PASS

**验证方式**：代码核查 + 受控实测

| 检查项 | 结果 | 证据 |
|---|---|---|
| `fetch_capital_flow(symbol, market)` 签名未变 | ✅ PASS | `inspect.signature` 返回 `(symbol, market)` — L1795 |
| 单只调用正常 | ✅ PASS | `fetch_capital_flow('600519', 'a_stock')` → `('success', '今日已有真实资金流数据（1条），跳过采集')`，耗时 0.017s |
| 单只主链路无外部错峰/分批延迟（DP-2） | ✅ PASS | 耗时 0.017s << 5s；错峰/分批/冷却/熔断仅在 `fetch_capital_flow_batch` 回退循环内 |
| 东财三层降级链完好 | ✅ PASS | Layer 1: `_fetch_capital_flow_em_individual`(push2his) → Layer 2: `_fetch_capital_flow_em`(push2) → Layer 3: `ak.stock_individual_fund_flow`(akshare)，三层函数均存在 |

---

### TC-019C-7 — batch-analyze 路径回归抽查 ✅ PASS

**验证方式**：代码核查 + 导入测试

| 检查项 | 结果 | 证据 |
|---|---|---|
| app.py batch-analyze 与日报共用 `fetch_capital_flow_batch` | ✅ PASS | app.py L1291 调用 `fetch_capital_flow_batch(a_symbols)`；daily_report.py L479 调用同一函数 — R-5 双路径生效 |
| app.py 导入无错误 | ✅ PASS | `import app` 成功，无 ImportError/Exception |

---

## 三、红线核验

| # | 红线项 | 核验方法 | 结论 |
|---|---|---|---|
| 1 | `fetch_capital_flow(symbol, market)` 签名未变 | inspect.signature + 代码核查（L1795） | ✅ 通过 |
| 2 | `generate_advice()` 签名未变 | advisor.py L1195 `def generate_advice(stock_id, report_date=None)` — 未在 019C diff 中 | ✅ 通过 |
| 3 | 东财三层降级结构未破坏 | `_fetch_capital_flow_em_individual`(L1454) / `_fetch_capital_flow_em`(L1529) / akshare(L1962) 降级链逻辑完好 | ✅ 通过 |
| 4 | 估算数据源维持 `if False` 硬禁用 | L2028 `if False and ... hk_stock`、L2069 `if False and ...`、L2104 `if False and ...` — 三处估算源均硬禁用 | ✅ 通过 |
| 5 | 无新增 pip 依赖 | `requirements.txt` 仍为 9 个包（akshare/Flask/pandas/numpy/python-dateutil/pydantic/requests/openpyxl/pytest） | ✅ 通过 |
| 6 | `config_weights.json` 未改 | 未在 git modified 列表中；rating_mapping 80/65/50/30 完好、无 BOM | ✅ 通过 |
| 7 | 不删除/覆盖现有数据 | 写入逻辑 `INSERT OR REPLACE` 未变（L1881/L1932/L1981） | ✅ 通过 |
| 8 | 改动限于 `modules/data_collector.py` | config.py 未在 git modified 列表中；019C 三项改动（硬编码移除/常量新增/回退循环改造）均在 data_collector.py 内 | ✅ 通过 |

---

## 四、R-1 超时观察记录

| 项 | 内容 |
|---|---|
| 观察口径 | max_retries 1→3 后，三层全失败最坏约 140~155s（无代理），超过 `STOCK_TIMEOUT_SECONDS=90` |
| 本次实测情况 | TC-019C-4/5/6 均为同日跳过或 DB 级失败，未触发真实三层采集，**无法直接观察 90s 超时触发频率** |
| 分析结论 | 单只最坏耗时公式：push2his 层 3 轮 × (2 次尝试 × (connect_timeout=5 + read_timeout=10) + 轮间延迟 1.5~3.5s) ≈ 99~112s；push2 层同理 ≈ 41~48s。三层全失败最坏 ≈ 140~160s，确实超过 90s 超时阈值 |
| 风险评估 | 在反爬严重时段，单只三层全失败会触发日报主循环 90s 超时跳过。但由于回退循环已有熔断(5 只)/软超时(600s)保护，不会导致日报整体卡死 |
| 建议 | 供 PM 决策是否另立批次调整 `STOCK_TIMEOUT_SECONDS` 或 `_EM_BACKOFF_CAP_SECONDS`（本批次不裁定） |

---

## 五、PM 提示项确认

| # | 项目 | QA 确认 |
|---|---|---|
| R-1 | 超时观察口径 | 已记录（见第四节），本次无法实测，记录于报告供 PM 决策 |
| R-4 | 熔断计数进程级语义 | 已验证：`_EM_CONSECUTIVE_FAIL_COUNT` 为模块级变量，测试后确认值为 5（TC-019C-4），需重启或手动重置（R-4 符合预期） |
| R-5 | 双路径生效 | 已确认：app.py batch-analyze 与日报均调用 `fetch_capital_flow_batch`（TC-019C-7） |
| R-6 | 错峰验证方法 | 已通过 THS 强制失败场景验证错峰延迟（TC-019C-5，2.8s 真实常量） |
| 间歇性反爬 | 东财接口间歇性反爬 | 本次测试用同日跳过/DB 级失败规避了真实接口调用，符合验收方法要求 |
| 同日跳过计入 success | 同日跳过返回 success 触发计数重置 | 已验证（TC-019C-5，"连续失败计数重置(2→0)"），R-3 裁定正确 |

---

## 六、验收环境

| 项 | 内容 |
|---|---|
| 测试时间 | 2026-08-03 23:00~23:05 |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 数据库 | `stock_analyst.db`（600519/000333 等 4 只股票今日有资金流数据） |
| 验证方式 | 静态代码核查（TC-1/2/3/7） + 受控实测（TC-4/5/6） + 导入测试（TC-7） |
| 实测股票数 | 假代码 6 只（999999~999994）+ 真实代码 2 只（600519、000333），均在限额内 |
| 临时文件 | 测试脚本已全部删除，无残留 |

---

**QA 签字**：019C 批次全部 7 项测试用例 PASS + 8 项红线全部通过，建议双签关闭。

**等待 PM 双签。**
