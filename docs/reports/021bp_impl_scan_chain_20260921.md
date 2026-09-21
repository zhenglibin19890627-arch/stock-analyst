# 021BP 项5 实施报告：扫描加自选后一键衔接批量分析

> 日期：2026-09-21 ｜ 实施：builder（任务 t5）｜ 依据：t1 方案 §八 项4/项5 衔接点 + t4 产出（runChunked / runBatchAnalysis 前身）
> 验收：fast 942 passed + ruff 绿 + mypy 55 文件 0 错 + 红线 28/28 + node --check（market/watchlist/core 三文件）
> 性质：纯前端改造——复用既有端点，零新表、零后端路由改动、零新依赖

---

## 一、交付概览

| 项 | 交付物 | 说明 |
|---|---|---|
| 核心公共化 | `static/js/watchlist.js` `runBatchAnalysis(ids)` | t4 批量分析逻辑收口为显式 ids 入口；`batchAnalyze()` 只做勾选框收集 |
| 一键衔接 | `static/js/market.js` `msAddSelected()` | 加自选成功 >0 → confirm「立即批量分析」→ 调 `runBatchAnalysis` + 切自选视图 |
| 失败透出 | 同上 catch 分支 | 网络异常由静默计数改为 `网络异常：<message>`（021BO/7ea701b 教训延续） |

## 二、衔接链路

```
市场扫描勾选 → msAddSelected 逐只 POST /api/stocks（收集 success 返回的 stock_id）
  → 成功数 >0：confirm「是否立即执行批量分析+评级？」（说明拆批耗时、不阻塞浏览）
      ├─ 确认 → runBatchAnalysis(addedIds) + navigateTo('#watchlist')
      │          → core.js runChunked 5×20 顺序 POST /api/batch-analyze
      │          → collectArea 真实进度 → 跨批合并 renderBatchResults
      │          → 完成后 loadStocks() + refreshDashboardIfLoaded()（看板/行动清单同步）
      └─ 拒绝 → 保留"稍后在自选股页手动批量分析"指引
  → 成功数 = 0：原指引文案（失败原因已去重透出）
```

## 三、设计要点

1. **无两处复制**：分批驱动 = core.js `runChunked`（t4）；批量链路 = watchlist.js `runBatchAnalysis`（本任务公共化）。market.js 只传 ids 并切视图，零重复逻辑。
2. **不阻塞页面**：加自选逐只 fetch 本就异步；批量分析经 confirm 后由 runChunked 异步顺序驱动（单批完成才发下一批），期间用户可正常浏览其他页面；进度/结果落在自选页 collectArea（视图切换仅 classList，DOM 持久）。
3. **stock_id 来源**：`POST /api/stocks` 成功响应自带 `stock_id`（watchlist.py api_add_stock），无需二次查询；"已在自选中"的股票不入衔接清单（其响应 success=false 且无 id，语义为去重跳过）。
4. **R16 合规**：衔接链路完全继承 runBatchAnalysis 的 5×20 拆批，单次 POST ≤20；市场页加自选本身的 ≤20 上限未动。
5. **红线**：零后端改动（无新表/路由/配置）；不触 generate_advice；不动风控阈值与 FLASK_DEBUG。

## 四、验证

- `node --check`：market.js / watchlist.js / core.js 全过（项目无 JS 测试设施，按仓内先例兜底）
- `pytest tests/` fast 942 passed（零 Python 改动，存量后端契约全绿）；ruff / mypy / 红线 28/28 全绿

## 五、建议（后续可选）

- 市场页可在扫描卡片上内嵌一个轻量进度徽标（当前确认后已自动切视图，暂无必要）。
- 真实环境走一遍"扫描 20 只 → 加自选 → 立即批量分析"实测耗时观感。
