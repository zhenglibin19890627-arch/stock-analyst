# 021BP 项3 实施报告：总览看板"今日行动清单"

> 日期：2026-09-21 ｜ 实施：builder（任务 t3）｜ 依据：`docs/reports/021bp_boost_plan_20260921.md` §八 项3
> 验收：fast 938 passed + ruff 绿 + mypy 55 文件 0 错 + 红线 28/28（全程不触网，只读聚合）

---

## 一、交付概览

| 层 | 交付物 | 说明 |
|---|---|---|
| 聚合层 | `modules/action_list.py`（新） | `get_action_list()` DB 四路取数；`build_action_list()` 纯函数装配（单测友好） |
| 端点 | `GET /api/dashboard/action-list` | 新蓝图 `blueprints/dashboard.py`，已注册 ALL_BLUEPRINTS；只读 |
| 前端 | `static/js/portfolio.js` 看板卡 | "🎯 今日行动清单"卡：统计行 + 排序行动项 + 点击行 `viewReport` 直达 |
| 测试 | `tests/test_action_list.py`（11 例）+ `test_routes.py`（+1） | 纯函数契约 + 临时库全链 + 路由冒烟 |

## 二、响应结构（前端/后续任务消费参考）

```json
{
  "success": true,
  "date": "YYYY-MM-DD",
  "items": [{ "priority": 1, "priority_label": "评级变动", "kind": "rating_upgrade|rating_downgrade|rating_change|tech_signal|alert_unread|report_failed",
              "stock_id", "symbol", "name", "reason", "detail": {} }],
  "overview": [{ "stock_id", "symbol", "name", "total_score", "rating", "rating_label", "score_change",
                 "trader_stage", "has_disagreement" }],
  "failed_stocks": [{ "stock_id", "symbol", "name", "error" }],
  "stats": { "active_count", "reported_ok_today", "failed_today", "missing_today",
             "rating_moves", "signal_hits", "resonance_hits", "unread_alerts_today" }
}
```

排序契约（"今日应做"）：P1 评级升降（降级风控优先，再按 |评分变动|）> P2 买点信号（共振≥4星优先）> P3 预警未读 > P4 超时缺报股。

## 三、四路数据与口径要点

1. **daily_reports**：`ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY report_date DESC)` 取最近两期 `report_type='daily'` 行；评级升降 = 今日 rating 与上期不同，方向消费 `alert_engine.RATING_ORDER`（R7 允许范围，不重实现映射）；今日 `status='failed'` 股**显式列出**（`failed_stocks` + P4 行动项，补 §4.4"超时股从概览表消失"缺口）；无今日报告计入 `missing_today` 统计。
2. **t2 信号复算**：`scan_watchlist_signals()` 复用；只认 `trigger_date == kline_upto` 的"今日出现"命中；共振 `top_stars` 驱动类内排序与 `resonance_hits` 统计；失败降级为空（不阻塞清单）。
3. **alert_history**：`trigger_date = 今天`，仅未读（is_read=0）产行动项；同股多条合并为一条（count + 首条 message）。
4. **key_factors.trader**：从最新报告行 JSON 解析（预计算零重算），`trader_stage`/`has_disagreement` 透出 overview；评级变动项的 reason 附加分歧提示。

## 四、键名陷阱处置（方案 §4.2）

- 趋势罗盘**不在本聚合**（独立端点 `/api/stocks/<id>/trend`，报告页异步卡消费）——行动清单不重复拉取；
- `trader position_pctile`（60日分位）与 `price_advisor position_pct`（仓位%）均未在本聚合使用，无混淆点。

## 五、红线自检

| 红线 | 结论 |
|---|---|
| R9 | 零写入：daily_reports/评分/评级/预警表全部只读；未改 `_build_markdown_summary`（可选小节未做，看板卡为唯一形态） |
| B24/R13 | 零触碰 generate_advice |
| R7 | 升降方向仅消费 alert_engine.RATING_ORDER 既有顺序表 |
| V8 | 只读消费源表 |
| 021BN | 渲染文案（reason 等）无裸 `'<'`（测试断言） |
| R16/R18/R19 | 未触碰（无批量/并发/超时改动） |

## 六、遗留与建议

- 前端无 JS 测试设施：卡片渲染靠路由冒烟 + `node --check` 语法验证兜底（与本仓先例一致）。
- `_build_markdown_summary` 追加"行动清单" markdown 小节（方案备选形态）未做——看板卡已覆盖"30 秒"诉求，避免触碰 R9 邻接区；如需报告页静态留痕可后补。
