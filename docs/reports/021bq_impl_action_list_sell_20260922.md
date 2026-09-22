# 021BQ t4 实施报告：行动清单/看板整合双侧信号与操盘手矩阵（含持仓标记）

> 日期：2026-09-22 ｜ 实施：builder（任务 t4）｜ 依据：`docs/reports/021bq_sell_side_plan_20260921.md` 项D + 任务书要点①~⑤
> 依赖：t2（卖点信号/巡检/预警）+ t3（操盘手 operations 矩阵）
> 验收：**fast 1004 passed** + ruff 全绿 + mypy 55 文件 0 错 + **红线 28/28** + node --check ×2

---

## 一、交付概览

| 层 | 交付物 | 说明 |
|---|---|---|
| 聚合层 | `modules/action_list.py` 路2b | `scan_watchlist_sell_signals()` 独立 try 降级接入；`held_map` 持仓聚合查询（holdings `SUM(quantity)>0 GROUP BY stock_id`，账户无关，021W 多行约定天然满足） |
| 行动项 | 新 kind `sell_signal`（priority=2） | detail 增 `{signals, resonances, top_stars, kline_upto, rating_conflict, held, total_qty, avg_cost}`；reason 分持仓/空仓双话术 |
| 排序契约 | `_sort_key` P2 扩三元 `(p, side_rank, -stars, symbol)` | `side_rank`：**卖出·持仓=0 < 买点=1 < 卖出·空仓=2**（t1 方案裁定：持仓风控优先于他人买点、空仓回避信息垫底，测试锁定） |
| 相悖调和 | `_SELL_CONFLICT_RATINGS = ('推荐买入','强烈推荐买入')` | 021BP `_CONFLICT_RATINGS` 的反向镜像（独立元组勿复用）：卖点信号×买入档评级 → reason 附「短线回调警示，评级未变……以评级为主」，detail.rating_conflict 置档位 |
| 前端徽标 | portfolio.js `renderActionListCard` | 三分支：相悖琥珀 `卖出信号·与评级相悖` / 持仓绿系 `卖出信号·持仓`（#e8f5e9/#2e7d32）+ 📍持仓N股徽标 / 空仓灰蓝 `回避信号`（#e0f2f1/#00695c，A股绿=风控惯例）；统计行增卖点计数 |
| 口径脚注 | 行动清单卡脚注更新 | "买卖点信号均为离线快照参考口径（……卖出信号同为离线快照参考，持仓标记来自持仓账户聚合）" |
| 操盘手卡 | analysis.js ④ 操作矩阵段 | **t3 已交付**（本任务项③核对无新增需求——信号 chips/联动解读/双视角动作行均已渲染 operations 增量键） |
| 测试 | test_action_list.py 15→**24 例** + test_routes.py +1 断言 | 排序契约/持仓标记/相悖反向/今日口径/降级路径/全链集成/端点冒烟 |

## 二、结构向后兼容（零破坏证明）

- `build_action_list(today, stocks, report_rows, alerts_today, signal_result, sell_result=None, held_map=None)`——新参带默认值，既有 5 参调用方（含全部既有测试）零改动；
- stats 仅增 `sell_hits`/`sell_resonance_hits` 两键（八项→十项计数），旧键名与语义零变化；
- 响应既有键（date/items/overview/failed_stocks/stats）无删除无改名；
- 既有 15 例 action_list 测试原样全绿（`test_priority_order_spec` 的 kinds 序列不受影响——无卖点输入时 side_rank 恒为 1）。

## 三、排序契约（t1 方案裁定，测试锁定）

```
P1 评级升降（降级优先，按|评分变动|）
P2 卖出信号·持仓  （side_rank=0 —— 持仓风控优先）
P2 买点信号       （side_rank=1 —— 类内共振≥4星优先）
P2 卖出信号·空仓  （side_rank=2 —— 回避信息垫底）
P3 预警未读
P4 缺报补数
```

收录口径与买侧同纪律：仅"今日出现"（触发日==最新采集K线日）的命中产行动项；窗口内历史命中由前日巡检覆盖（`test_sell_today_gate` 锁定）。

## 四、持仓标记口径

```sql
SELECT stock_id, SUM(quantity) AS total_qty,
       CASE WHEN SUM(quantity)>0 THEN SUM(quantity*cost_price)/SUM(quantity) END AS avg_cost
FROM holdings WHERE quantity > 0 GROUP BY stock_id
```
- 账户无关聚合：同股多账户分仓只产一行标记（AGENTS.md 021W"任何 JOIN holdings 的新代码必须处理同股多仓"的更优解——聚合天然无重复行）；
- reason 话术：持仓 → "持仓 500 股（成本 14.00）——按纪律执行减仓/止损检查"；空仓 → "当前空仓——回避新买入，等待企稳"。

## 五、测试清单（新增 9 例 + 端点断言）

| 用例 | 锁定行为 |
|---|---|
| `test_sell_item_with_held_marker` | 持仓标记 + reason 纪律话术 + stats 双计数 |
| `test_sell_item_empty_position` | 空仓弱相关话术 + detail 缺省值 |
| `test_sell_conflict_with_buy_rating_reversed` | 相悖反向：推荐买入×卖点 → 「短线回调警示，评级未变」+ 以评级为主 + 无裸 `<` |
| `test_sell_strong_buy_rating_also_conflict` | 强烈推荐买入档对称 |
| `test_sell_aligned_reduce_rating_no_conflict` | 方向一致不标注 |
| `test_sort_contract_held_sell_before_buy_before_empty_sell` | **排序契约锁定**：卖出·持仓 < 买点 < 卖出·空仓 |
| `test_sell_today_gate` | 非今日命中不产行动项 |
| `test_sell_degraded_path_none` | 卖出路降级（缺省参数）既有调用零破坏 |
| `test_get_action_list_sell_chain` | 临时库全链：死叉形态K线（t2 校准配方）+ 持仓 + 推荐买入日报 → 卖点项持仓标记 + 相悖调和 + 双死叉 4 星共振计数 |

## 六、红线自检

| 红线 | 结论 |
|---|---|
| R9 只读聚合 | 不写 daily_reports/评分/评级表；holdings/daily_reports/alert_history 全部只读（V8） |
| B24/R13 | advisor.generate_advice 零触碰 |
| R7 | 评级消费沿用既有 _CONFLICT_RATINGS 判定模式（字符串档位比对），不重实现映射 |
| 021BN 裸 `<` | reason/priority_label 文字化；既有 `test_no_bare_lt_in_rendered_text` 继续覆盖 + 新增卖点 reason 断言 |
| 021W 多账户 | held_map 用 SUM+GROUP BY 账户无关聚合（本批统一口径，与 t3 trader_advisor 修复同源） |
| R16/R18/R19/R12/R17 | 未涉 |

## 七、验证记录

```
python -m pytest tests/                 → 1004 passed, 1 skipped（fast 层，55s）
ruff check .                            → All checks passed!
python -m mypy app.py config.py modules → Success: no issues in 55 source files
python scripts/check_redlines.py        → 28/28 通过
node --check static/js/portfolio.js     → OK
node --check static/js/analysis.js      → OK
```

改动文件：modules/action_list.py ｜ static/js/portfolio.js ｜ tests/test_action_list.py ｜ tests/test_routes.py

## 八、021BQ 批次收口状态

- 项A 纯函数库 / 项B 巡检端点 / 项C 预警（t2）✅
- 项E 操盘手操作矩阵 + 多账户修复（t3）✅
- 项D 行动清单卖侧行 + 持仓标记 + 前端徽标（t4，本报告）✅
- 决策闭环双侧链路全通：巡检（sell 端点）→ 预警（sell_signal 铃铛）→ 行动清单（卖点项+持仓标记+相悖调和）→ 操盘手卡（双视角矩阵）——五处消费同一套平行纯函数口径。
