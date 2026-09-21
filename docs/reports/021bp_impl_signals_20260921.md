# 021BP 项1+项2 实施报告：自选股买点信号离线巡检 → 智能预警

> 日期：2026-09-21 ｜ 实施：builder（任务 t2）｜ 依据：`docs/reports/021bp_boost_plan_20260921.md` §八 项1+项2
> 验收：fast 926 passed + ruff 绿 + mypy 54 文件 0 错 + 红线 28/28（全程不触网）

---

## 一、交付概览

| 层 | 交付物 | 说明 |
|---|---|---|
| 复算层（项1） | `modules/market_screener.py` 新增 3 函数 | 信号纯函数复用，库内K线离线复算，零网络 |
| 端点（项1） | `GET /api/market/scan/watchlist-signals` | `blueprints/market.py`；`scope=watchlist_offline` |
| 预警层（项2） | `modules/alert_engine.py` 新增 `tech_signal` 第4类规则 | 检查器+文案+白名单；写入走既有幂等路径 |
| 调度 | **零新增** | 随 `scan_once()` 既有挂载点（15:54 收盘批次）自动触发 |
| 种子 | `database/_db/_schema_alerts.py` | 全局默认规则 `('tech_signal', None)` 幂等插入 |
| 前端 | alerts.js（2 map）/ index.html（下拉）/ app.css（徽标） | 五处白名单/标签同步齐全 |
| 测试 | `tests/test_tech_signal_alert.py`（14 例）+ `test_routes.py`（+2） | 隔离临时库，合成K线不触网 |

## 二、复算层接口（供 t3 行动清单聚合消费）

```python
# modules/market_screener.py
scan_watchlist_signals(stock_ids=None, signals=None, window=3, daily_limit=250)
# → {'scope': 'watchlist_offline', 'stock_count': N,
#    'results': [{stock_id, symbol, name, matches, resonances, kline_upto, kline_count}],
#    'errors': [{stock_id, error}]}
# 仅收录有命中（matches 或 resonances 非空）的股票；matches 为窗口内全部命中。

compute_watchlist_signal_result(kline_rows, weekly_rows=None, wanted=None, window=3)
# → {'matches', 'resonances', 'kline_upto', 'kline_count'}（纯函数，不触库）

_read_watchlist_klines(cursor, stock_id, daily_limit=250)
# → (daily_rows, weekly_rows)（需外部 cursor；raw_kline/raw_kline_weekly → screener 行格式）
```

**口径要点**（t3 聚合时注意）：
- `scope='watchlist_offline'` = 快照参考口径，数据截止 `kline_upto`（最新已采集K线日）；
- `matches` 含检测窗口（默认3根）内全部命中；**"今日出现"过滤在预警层做**（`trigger_date == kline_upto`），行动清单如需"今日"口径请同样过滤；
- 日K自读 250 根，与评分路径（limit=60）解耦；周K 全量参与 `res_week_daily` 共振；
- 只读：不写 ratings/analysis/候选表（与扫描器"只产候选"同一边界，V8 合规）。

## 三、预警层语义（tech_signal，第4类规则）

- **触发口径**：信号触发日 == 最新已采集K线日（"今日出现"）。窗口内历史命中不计——前一日巡检已覆盖，配合 `UNIQUE(rule_id, stock_id, trigger_date)` 幂等，同信号不重复轰炸。
- **消息形态**：`{name}({symbol}) 今日出现买点信号：MACD水下金叉、KDJ低位金叉；共振组合：周线共振波段（5星）（基于截至YYYY-MM-DD的已采集K线离线复算）`——**无裸 `<` 字符**（021BN 教训）。
- **阈值语义**：共振星级门槛（选填 3/4/5）——设置后仅出现 ≥ 该星级的共振才提醒；留空 = 任意买点信号都提醒。个股规则 > 全局规则（既有优先级）。
- **写入路径**：完全复用 `scan_once()` 的 `INSERT OR IGNORE + UNIQUE(rule_id, stock_id, trigger_date)`；调度零新增——巡检随 15:54 收盘批次（`daily_report._run_full_report_flow` → `scan_once`）每日一次，双层异常隔离复用，021BN-c 实例守卫不受影响。
- **数据不足静默降级**：<35 根日K / 无K线 / 无数据 → 不提醒不报错。

## 四、红线自检

| 红线 | 结论 |
|---|---|
| B24/R13 generate_advice | 零触碰 |
| R7 评分/评级映射 | 不涉评级（天然规避）；信号口径复用扫描器既有纯函数 |
| R8 数据源解耦 | 输入为本地 DB 行，无 akshare 字段耦合 |
| R16 风控阈值 | 未触碰（不涉批量） |
| R18/R19 超时 | 未引入并发/超时改动 |
| R9/R10 写库不变量 | 零新表；alert 走既有幂等约束 |
| 021BN-c 实例守卫 | 无新增调度器，app.py 未改动 |

## 五、遗留与建议

- 前端看板/报告页**展示**巡检结果入口未做（项2 只落预警铃铛链路，用户打开页面即可见未读预警）；行动清单卡片由 t3 聚合本报告 §二 接口产出。
- `check_tech_signal` 的 `window` 固定默认 3（与扫描器一致），如需可配置再议。
