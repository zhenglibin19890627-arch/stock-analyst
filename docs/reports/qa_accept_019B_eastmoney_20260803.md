# QA 验收报告：DEV-TASKS-20260803-019B 东财接口排查与重试机制

> 任务编号：QA-TASKS-20260803-018_019AB
> 关联开发任务：DEV-TASKS-20260803-019B
> 验收日期：2026-08-03
> 验收人：QA（独立验收）
> 验收方式：代码核查 + 数据库查询 + 排查报告核查

---

## 一、测试用例结果

### TC-019B-1 — 重试机制代码核查（风险：低）

| 检查项 | 结果 | 证据 |
|---|---|---|
| `_http_get_em()` 含多轮重试循环 | **PASS** | `data_collector.py` L199 `for attempt in range(rounds):`，L195 `rounds = max_retries if max_retries else MAX_RETRIES` |
| UA 池（≥2 个 User-Agent） | **PASS** | L53-76 `_UA_POOL` 含 **22 个**真实浏览器 UA（Chrome/Firefox/Safari/Edge/Opera/移动端），L79-81 `_random_ua()` 随机选取 |
| 随机延迟 | **PASS** | L210 `_delay = _random.uniform(1.5, 3.5); time.sleep(_delay)`（请求间延迟），L244 `wait = _random.uniform(1.5, 3.5)`（轮间等待） |
| 重试轮数由 `max_retries` 参数控制 | **PASS** | L185 `def _http_get_em(url, params=None, timeout=15, max_retries=None):`，L195 `rounds = max_retries if max_retries else MAX_RETRIES`。调用处 L1365/1430 传 `max_retries=1`（当前默认值） |

> **注**：任务书说明 max_retries 提升至 3 属后续优化项，本次不要求。当前 `max_retries=1` 为已知状态，QA 确认不作为 FAIL 依据。

**结论：PASS**

---

### TC-019B-2 — 数据恢复验证（风险：低）

| 检查项 | 结果 | 证据 |
|---|---|---|
| 600519 东财 `main_net_inflow` 数据 ≥100 天 | **PASS** | DB 查询：600519(stock_id=18) 有 **132 条** `main_net_inflow` 记录（开发报告称 121 天，DB 实际 132 条），日期范围 2026-01-16 ~ 2026-08-03 |
| 600519 的 `main_net_inflow` 为东财口径（有分单数据） | **PASS** | 抽样 3 条记录全部含 `super_large_net`/`large_net`（如 2026-08-03: main=-2218.04, super=-18577.15, large=16359.1） |

> 开发报告称"121 天历史数据"，DB 实际 132 条（因后续又有新增数据），均满足 ≥100 天要求。

**结论：PASS**

---

### TC-019B-3 — 排查报告完整性（风险：低）

| 检查项 | 结果 | 证据 |
|---|---|---|
| `reports/dev_diag_019B_em_failure_20260803.md` 存在 | **PASS** | 文件存在，144 行 |
| 含根因结论（间歇性反爬阻断） | **PASS** | 报告第一节"结论摘要"明确根因："`push2his`/`push2`/`akshare` 三层接口服务器对当前出口 IP 实施间歇性反爬阻断（`RemoteDisconnected`），非永久封禁、非接口变更、非频率过高导致的永久封IP" |
| 含恢复验证记录 | **PASS** | 报告第五节"Step 4：恢复验证"：贵州茅台第15次成功（2026-08-03 21:16:44），写入 121 天历史数据；第六节"验收标准达成情况"逐条确认 3 项验收标准 |
| `scripts/` 下 11 个诊断脚本存在 | **PASS** | 确认 11 个脚本全部存在：`diag_em_019b.py`、`diag_db_019b.py`、`diag_tsla_019b.py`、`diag_retry_019b.py`、`diag_5stocks_019b.py`、`diag_fetch_600519_019b.py`、`diag_retry_verify_019b.py`、`diag_session_reuse_019b.py`、`diag_periodic_retry_019b.py`、`diag_restore_600519_019b.py`、`diag_verify_600519_019b.py` |

**结论：PASS**

---

## 二、红线核验

| 红线项 | 核验方法 | 结论 |
|---|---|---|
| 东财逐只采集主链路未被破坏 | `_fetch_capital_flow_em_individual`(L1342) → `_fetch_capital_flow_em`(L1417) → akshare 降级(L1847) 三层链路完好；估算源（腾讯/新浪/网易）维持 `if False` 硬禁用 | **PASS** |
| 无新增 pip 依赖 | `requirements.txt` 仍为 9 个包；诊断脚本仅使用 stdlib + 现有模块 | **PASS** |
| `config_weights.json` 未改 | rating_mapping 80/65/50/30 完好，无 BOM | **PASS** |

---

## 三、已知问题记录（不构成 FAIL）

| # | 问题 | QA 备注 |
|---|---|---|
| 1 | 东财接口为间歇性反爬，QA 验收时若现场单只采集失败不代表 FAIL | 本次验收以 DB 已有数据 + 代码机制核查为准，未触发实时采集（避免命中阻断窗口） |
| 2 | 019B 后续优化（max_retries 1→3、采集错峰）未实施 | 属待 PM 决策项，QA 确认不作为本次 FAIL 依据。开发报告第八节已明确列出建议 |
| 3 | 项目根目录存在零散文件（`test_engine_compare.py`、`test_us11_consistency.py`、`_p0_ths_stress_result.json`） | 已记录，PM 后续评估是否归档清理。与 019B 无关 |

---

## 四、最终结论

**全部 PASS，可双签。**

- 3 项测试用例全部 PASS
- 3 项红线核验全部 PASS
- 重试机制代码完整（多轮重试 + UA 池 22 个 + 随机延迟 1.5~3.5s）
- 贵州茅台(600519) 东财数据已恢复 132 天（≥100 天要求）
- 排查报告完整（根因 + 恢复验证 + 11 个诊断脚本）

---

## 五、验收环境

- 测试时间：2026-08-03
- Python：C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe
- 数据库：stock_analyst/stock_analyst.db
- 验证方式：代码核查（data_collector.py L185-248/_UA_POOL）+ DB 查询（600519 main_net_inflow 记录数/日期范围/分单数据）+ 排查报告核查（dev_diag_019B_em_failure_20260803.md）+ 脚本文件清点（scripts/ 11 个 diag_*019b.py）
