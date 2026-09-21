# 021BP 决策闭环全量验证与端到端验收报告

> **执行人**：checker（t6） | **日期**：2026-09-21 | **结论**：**通过**（无保留）
>
> 验证范围：t2（信号巡检→智能预警）/ t3（今日行动清单）/ t4（100 只规模适配）/ t5（扫描→批量分析衔接）
> 全部实施完成后的全量验证 + 只读冒烟 + 逐项验收核对。本报告只验证不修复。

---

## 一、四道验证门（全绿）

| # | 门 | 命令 | 结果 | 耗时 |
|---|---|---|---|---|
| 1 | 全量测试（含 slow 层） | `python -m pytest tests/ -m "slow or not slow" --timeout=1200` | **948 passed, 1 skipped**（exit 0） | 505.74s（8分25秒，完整跑完） |
| 2 | 静态检查 | `ruff check .` | All checks passed | — |
| 3 | 类型检查 | `mypy app.py config.py modules` | **55 文件 0 错** | — |
| 4 | 红线核验 | `python scripts/check_redlines.py` | **28/28 通过**（exit 0） | — |

**skip 说明**：唯一 1 例 skip 为 `test_cascade_integrity.py::test_delete_stock_known_gap_documented[NOTSET]`——级联完整性的**已文档化已知缺口**测试（021BP 之前即存在），与本轮四项改动无关。
**数量勾稽**：942（t4/t5 时点 fast 层）+ 6（slow 层：TestTickBackoff 等真实时钟退避测试）= 948，与 AGENTS.md §5 分层口径一致；t2 +16 / t3 +12 / t4 +4 新增用例全部包含在内。
**slow 层确认**：`TestTickBackoff::test_all_fail_backoff_doubles` / `test_partial_success_resets` 等真实时钟退避用例在本次全量中实际执行且 PASSED（非跳过）。

## 二、运行中服务只读冒烟（全部 GET，零 POST，零写库）

服务：http://127.0.0.1:5000（看门狗守护，实测**已加载 021BP 新代码**——旧代码会 404 的两个新端点均正常响应）。

| 端点 | 结果 | 关键观测 |
|---|---|---|
| `GET /api/health` | ✅ 200 | `status=running, version=v5.0` |
| `GET /api/stocks` | ✅ 200 | `success=True`；**当前实时自选股列表为空（count=0）**，属数据状态非缺陷（见 §四备注） |
| `GET /api/portfolio/summary` | ✅ 200 | 结构完整：`account_scope / accounts_breakdown / rating_distribution / total_*` 等 17 键 |
| `GET /api/alerts/rules` | ✅ 200 | **total=5，含 t2 幂等种子的全局 `tech_signal` 规则（id=14, enabled=1, 无阈值）**——默认规则已落实时库 |
| `GET /api/alerts/unread` | ✅ 200 | `success / unread_count / alerts` 三键齐全 |
| `GET /api/dashboard/action-list`（t3 新端点） | ✅ 200 | `date / items / overview / stats / failed_stocks` 五键齐全（空库下空清单，契约正确） |
| `GET /api/market/scan/watchlist-signals`（t2 新端点） | ✅ 200 | `scope=watchlist_offline, signals=0`（空自选 → 空结果，契约正确） |

冒烟期间未执行任何 POST/PUT/DELETE，未触碰任何写路径。

## 三、t2–t5 验收点逐项核对（代码实证 + 实机观测）

### t2 自选股买点信号巡检 → 智能预警 — ✅ 全部达成

| 验收点 | 证据 |
|---|---|
| 离线复算纯函数链，零网络 | `market_screener.scan_watchlist_signals`（L702-749）：`SELECT id,symbol,name FROM stocks WHERE status='active'` → `_read_watchlist_klines`（读库日K/周K）→ `compute_watchlist_signal_result`（纯函数）；单只异常隔离进 `errors` 不阻塞 |
| 新端点 + scope 标注 | `blueprints/market.py:189` `GET /api/market/scan/watchlist-signals`；实机返回 `scope='watchlist_offline'` ✅ |
| tech_signal 第 4 类规则 | `alert_engine.VALID_RULE_TYPES`（L49）、`_RULE_CHECKERS['tech_signal']`（L357，threshold→`min_stars` 星级门槛）、`blueprints/alerts.py:_VALID_ALERT_TYPES`（L10）、schema 注释+种子（`_db/_schema_alerts.py` L16/L79）、`templates/index.html:83`（规则创建下拉）、`static/css/app.css:621`（预警徽标配色）——**白名单 5 处 + 前端 2 处全同步** |
| "今日出现"口径 | `check_tech_signal`（L249-299）：`hits_today = [h for h in matches if h['trigger_date'] == kline_upto]`——仅认触发日==最新K线日；`min_stars` 过滤共振星级；数据不足（<35 根日K）静默跳过 |
| 调度零新增 | 复用 `daily_report.py` L198-200 既有挂载点：日报生成后 `scan_once()`（双层异常隔离），tech_signal 随既有 15:54 收盘批次执行 |
| 种子落库实证 | 实时库 `alerts/rules` 返回全局 tech_signal 规则（见 §二） |
| 文案无裸 '<' | `_format_message` tech_signal 分支（L331-339）纯文字描述 + `（基于截至…离线复算）` 口径声明，无裸 '<' |

### t3 每日报告顶部"今日行动清单" — ✅ 全部达成

| 验收点 | 证据 |
|---|---|
| 四路只读聚合 + 纯函数装配 | `modules/action_list.py`：`get_action_list()`（L40）+ `build_action_list()`（L98）；`blueprints/dashboard.py` 全文只调 `get_action_list` + `jsonify`，**零写库（R9 合规）** |
| 蓝图注册 | `blueprints/__init__.py` L6/L16 `dashboard_bp` 入 `ALL_BLUEPRINTS`，`app.py` 循环注册；实机端点 200 ✅ |
| 排序契约 | `_sort_key`（L312-324）：P1 评级变动（`rating_downgrade` dir_rank=0 **降级优先**，再按 |评分变动幅度| 降序）> P2 买点信号（`-top_stars` **≥4 星排前**）> P3 预警未读 > P4 缺报补数；类内按 symbol |
| failed 股显式列出 | `failed_stocks` 独立列表 + P4 `report_failed` 行（priority_label='缺报补数'）；实机响应含 `failed_stocks` 键 ✅ |
| 操盘手摘要零重算透出 | overview 消费 `key_factors.trader` 预计算（`_parse_key_factors` 容错解析，失败返回 None 不阻塞清单） |
| 前端消费 | `portfolio.js` L1313-1330 看板三路并行 fetch（action-list 失败静默降级不阻塞看板）→ L1388 `renderActionListCard`（L1522，"🎯 今日行动清单"卡，`escapeHtml` 包日期文案） |

### t4 自选股 100 只规模适配 — ✅ 全部达成

| 验收点 | 证据 |
|---|---|
| 通用分批驱动器 | `core.js` L34-55 `runChunked(items, chunkSize, runChunk, onProgress)`：顺序执行（单批完成才发下一批）、单批异常不阻塞后续、进度回调；顶层函数声明（全局作用域），index.html 加载序 core 最先，**单一定义两处复用** |
| ≤100 自动拆批，R16 不放宽 | `watchlist.js runBatchAnalysis` L343 `BATCH_CHUNK=20`、L344 `Math.ceil(total/20)`；**后端边界回归**：`test_routes.py:341` `>20 只 → 400` 且消息含 '20'（红线 R16 实测保持） |
| 真实进度替代假进度 | `renderProgress`：`已完成 doneChunks×20/total` + "第 x/y 批执行中"，最后一批刷"全部批次执行完成" |
| 跨批合并 + 整批失败可见 | `merged` 三件套累加；整批被拒 → 批内逐只记 `{status:'failed', error:msg}`；网络异常 → `'请求失败：'+err`（不静默） |
| §4.4 超时股可见性 | `daily_report._build_markdown_summary` L1614-1622：`> ⚠️ N 只股票生成失败…明细如下` + `| 股票 | 代码 | 失败原因 |` 表——**仅 markdown 内容，零写库路径**；数据层 failed 行由 t3 卡消费（既有） |
| 完成后联动 | `.finally` 中 `loadStocks()` + `refreshDashboardIfLoaded()`（看板含行动清单同步刷新） |
| JS 语法 | `node --check` core/watchlist/market/portfolio 四文件实测 exit 0 |

### t5 扫描加自选 → 一键衔接批量分析 — ✅ 全部达成

| 验收点 | 证据 |
|---|---|
| 核心公共化 | `batchAnalyze()`（L315-325）仅做勾选收集 → `runBatchAnalysis(ids)`；两个入口复用同一链路，无复制 |
| 一键衔接 | `market.js msAddSelected` L299 收集 `addedIds` → L306-317：`okCount>0` 时 `confirm(…是否立即执行「批量分析+评级」…)` → `runBatchAnalysis(addedIds)` + `navigateTo('#watchlist')`（真实分批进度/结果表在自选页呈现）；拒绝 → 保留原指引文案 |
| 失败原因全程透出 | L336-341 网络异常 `'网络异常：'+message`（7ea701b 教训落实）；加自选失败原因去重汇总进 summary |
| 不阻塞页面 | confirm 为任务书允许形态；批量分析 `runChunked` 异步顺序驱动，期间可浏览其他页 |
| 加自选 ≤20 约束 | L294 `checked.length > 20` 拦截（市场扫描加自选既有上限保持不变） |
| CHANGELOG | 021BP 项1+2 / 项3 / 项4 / 项5 四条齐备（`CHANGELOG.md` L3-29） |

## 四、真实操作路径视角（AGENTS.md §9.6）评估与备注

1. **决策闭环贯通性**：扫描（market.js）→加自选（≤20/次）→一键批量分析（前端 5×20 拆批，R16 不放宽）→分析/评级落库→次日日报+预警扫描（15:54 既有批次）→看板行动清单（P1-P4 排序）→预警提醒（含 tech_signal 第 4 类）→批量分析完成回调 `refreshDashboardIfLoaded()` 同步行动清单。**每一跳均有代码实证，无断点、无重复实现。**
2. **实机闭环的数据局限**：当前实时自选股为空（count=0），因此"带真实持仓数据的完整闭环"只能在契约层验证（端点全通、响应结构正确、种子规则已落库）。带数据的场景由 948 项测试覆盖（t2 合成 K 线形态校准 14 例、t3 装配 11 例、t4/R16 边界 4 例等）。**此为数据状态，非缺陷，不构成保留意见**；用户下次添加自选并跑完收盘批次后即走真实数据闭环。
3. **空库防御**：action-list / watchlist-signals / alerts 系列在空库下均返回 200 + 空集合（不 500、不抛错），真实路径首日体验安全。
4. **红线遵守实证**：`check_redlines.py` 28/28（含 R13 generate_advice 行为锁、R16 五项风控默认值、R19 超时配置）；t3 蓝图纯只读；t4 失败明细仅 markdown 内容；全程未触碰 `advisor.generate_advice`、未放宽任何风控阈值、未启用 FLASK_DEBUG、无新依赖新表。

## 五、结论

**通过（无保留）。**

- 四道验证门全绿：全量 pytest 948 passed（含 6 例 slow 实际执行）/ ruff / mypy 55 文件 0 错 / 红线 28/28；
- t2–t5 四项实施的全部验收点逐项核对达成，无未达成项、无回归项，无需列 findings；
- 运行中服务已加载新代码，7 个冒烟端点全部 200 且契约正确，实时库已含 tech_signal 默认规则；
- 唯一 skip 为既有已文档化级联缺口测试，唯一"空自选"观测为数据状态非缺陷。

> 附：全量测试日志（本轮实证）：`%TEMP%\021bp_full_pytest.log`（948 passed, 1 skipped in 505.74s）。
