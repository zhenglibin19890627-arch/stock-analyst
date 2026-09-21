# 021BP 项4 实施报告：自选股 100 只规模适配（批量分析前端拆批 + 超时股可见性）

> 日期：2026-09-21 ｜ 实施：builder（任务 t4）｜ 依据：`docs/reports/021bp_boost_plan_20260921.md` §八 项4 + §4.4
> 验收：fast 942 passed + ruff 绿 + mypy 55 文件 0 错 + 红线 28/28 + node --check（core.js / watchlist.js）

---

## 一、交付概览

| 项 | 交付物 | 说明 |
|---|---|---|
| 分批驱动器 | `static/js/core.js` `runChunked()` | msStartSignals chunk 模式泛化收口；**t5 复用同一 helper，不复制两份** |
| 批量分析 | `static/js/watchlist.js` `batchAnalyze()` | ≤100 只自动拆 5×20 顺序 POST；真实进度；跨批合并渲染 |
| 可见性补齐 | `modules/daily_report.py` `_build_markdown_summary` | 失败/超时股显式明细表（仅 markdown 内容，零写库路径改动） |
| 测试 | `tests/test_daily_report_summary.py`（3）+ `test_routes.py`（+1） | 失败明细列出 / `<` 转义 / R16 契约边界锁定 |

## 二、前端拆批设计（后端零改动）

- **入口行为变化**：原 >20 只弹窗"请分批勾选"→ 现自动拆批顺序执行，无上限（100 只 = 5 批，超出亦可）。
- **R16 合规**：`BATCH_CHUNK=20`，单次 POST ≤20；拆批 = 前端多次调用，`BATCH_OPERATION_LIMIT` 数值未动。
- **真实进度**：`已完成 N/总数 只（第 x/y 批执行中）` + 进度条按 `已完成批数×批大小/总数` 推进（替代原固定 30% 假进度）；驱动器在每批开始前回调 `onProgress(done, chunkCount)`，最后一轮回调 `done==chunkCount` 刷满 100%。
- **失败可见性**：单批响应失败（success=false）或网络异常时，该批股票全部显式记为 `status:'failed'` 行进入结果表（错误信息随行显示），不再整块报错丢失"哪些股没跑成"。
- **顺序执行**：单批完成才发下一批——匹配后端串行处理与 SQLite 单写者/采集限频约束；不做并发化。

## 三、§4.4 超时股可见性补齐

- **数据层（既有，已核验可得）**：超时/失败股写 `daily_reports` 行 `status='failed'` + `error_msg`（如"采集超时(90s)"）；t3 行动清单卡已消费该行（`failed_stocks` 显式列出 + P4 行动项）。
- **本任务最小改动**：`_build_markdown_summary` 概览区由一行计数 `"> ⚠️ N 只股票生成失败"` 扩为 **失败明细表（股票/代码/失败原因）**；原因文本 `<` → `&lt;`（marked 渲染吞字防护，021BN 教训）。仅拼接 markdown 字符串，**不触碰任何写库路径**（R9 合规）。

## 四、给 t5 的接口

- `runChunked(items, chunkSize, runChunk, onProgress)`（core.js，全局函数）：
  - `runChunk(chunkItems, chunkIndex, chunkCount) → Promise`：批内请自行降级失败（勿 reject）；
  - `onProgress(doneChunks, chunkCount)`：每批开始前回调，末轮回调 doneChunks==chunkCount；
  - 返回 `Promise<完成批数>`；顺序执行、单批异常吞掉继续。
- 参考消费样例：`watchlist.js batchAnalyze()`（合并 results + 真实进度渲染）。

## 五、红线自检

| 红线 | 结论 |
|---|---|
| R16 | `BATCH_OPERATION_LIMIT=20` 数值未动；单次 POST ≤20；`test_batch_analyze_over_limit_rejected` 锁定 400 契约 |
| R19 | STOCK/BATCH_TIMEOUT 90/1800 数值未动（未做后端超时增强） |
| R18 | 未引入任何 executor/线程改动 |
| R12 | FLASK_DEBUG 未动 |
| R9 | `_build_markdown_summary` 仅拼 markdown 内容，写库路径零改动 |
| R17 | 零新依赖 |
| B24/R13 | 零触碰 generate_advice |

## 六、遗留与建议

- 前端拆批逻辑无 JS 测试设施，靠 `node --check` 语法验证 + R16 后端契约测试兜底（与仓内先例一致）；建议真实环境全选 100 只实测一轮耗时与进度观感。
- 后端 batch-analyze 的单只超时/进度三件套（方案"可选增强"）按任务书裁定不做，留待用户后续决策。
