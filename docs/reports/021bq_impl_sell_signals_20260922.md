# 021BQ t2 实施报告：卖点信号纯函数 + 巡检双侧化 + 卖点预警（sell_signal）

> 日期：2026-09-22 ｜ 实施：builder（任务 t2）｜ 依据：`docs/reports/021bq_sell_side_plan_20260921.md` 项A/项B/项C + 项④
> 验收：**fast 981 passed** + ruff 全绿 + mypy 55 文件 0 错 + **红线 28/28**（全程不触网）

---

## 一、交付概览

| 层 | 交付物 | 说明 |
|---|---|---|
| 纯函数库（项A） | `modules/market_screener.py` 平行库 | `SELL_SIGNAL_LIBRARY`（5 信号）+ `SELL_RESONANCE_LIBRARY`（3 组 bear）+ `detect_sell_signals` / `detect_sell_resonances`（与买侧 `detect_*` 逐条件同构镜像） |
| 复算层（项A） | `compute_watchlist_sell_result` / `scan_watchlist_sell_signals` | 镜像 021BP 买侧包装；读库口径/收录口径/单只异常隔离全部一致 |
| 端点（项B） | `GET /api/market/scan/watchlist-sell-signals` | `blueprints/market.py`；响应 `scope='watchlist_offline'` + `side='sell'`。**选新端点不动旧端点**（旧契约被测试/前端锁定，零契约风险） |
| 预警层（项C） | `modules/alert_engine.py` 第 5 类规则 `sell_signal` | `check_sell_signal` 检查器（"今日出现"口径/星级门槛/<35 根静默，逐字镜像 `check_tech_signal`）+ 文案分支 + 白名单 |
| 评级上下文（项④） | `_get_current_rating` + `_rating_conflict` + `_rating_context_suffix` | 信号×评级相悖调和（详见 §三） |
| 同步面（项C） | **9 处**（方案 8 处 + app.css 徽标） | 见 §二 逐处清单 |
| 种子 | `database/_db/_schema_alerts.py` | 全局默认规则 `('sell_signal', None)` 幂等插入（对称 tech_signal） |
| 测试 | `tests/test_sell_signal_alert.py`（33 例）+ `test_routes.py`（+2） | 8 组合成形态实机校准（§四）；隔离临时库不触网 |

**命名裁定**：alert_type 定为 **`sell_signal`**（t1 方案 §4.1/§4.2/项C 三处明确命名；任务文中 `tech_signal_sell` 为"如"示例，以方案为准）。

## 二、预警同步面逐处清单（缺一即漏）

| # | 位置 | 状态 |
|---|---|---|
| 1 | `modules/alert_engine.py` `VALID_RULE_TYPES` | ✅ +`'sell_signal'`（5 类元组） |
| 2 | `modules/alert_engine.py` `_RULE_CHECKERS` | ✅ `check_sell_signal` 注册（阈值=bear 共振星级门槛） |
| 3 | `modules/alert_engine.py` `_format_message` | ✅ sell_signal 分支（"今日出现卖点信号：…；共振组合：…（基于截至…离线复算）"，无裸 `<`） |
| 4 | `blueprints/alerts.py` `_VALID_ALERT_TYPES` | ✅ 同步（否则创建规则 API 400） |
| 5 | `static/js/alerts.js` `_alertTypeLabels`（铃铛） | ✅ `'sell_signal': '卖点信号'` |
| 6 | `static/js/alerts.js` `_alertRuleTypeLabels`（规则管理） | ✅ 同上 |
| 7 | `static/js/alerts.js` `_alertRuleTypeHints`（创建提示） | ✅ 无阈值型 hint（show:false） |
| 8 | `templates/index.html` `#alertRuleType` 下拉 | ✅ `<option value="sell_signal">卖点信号（…死叉、破位MA20…）</option>` |
| 9 | `static/css/app.css` 徽标 | ✅ `.alert-item-type.sell_signal`（青绿系 #e0f2f1/#00695c，A股绿=风控语义，与资金流出浅绿区分） |

测试以 `test_static_sync_surfaces` 锁定前端三处（文件文本断言）+ `test_seed_and_whitelist_sync` 锁定 engine↔blueprint↔检查器。

## 三、评级上下文（项④，021BP c23f9ee 相悖调和的预警侧镜像）

- **数据源**：`_get_current_rating(cursor, stock_id)` 只读 `daily_reports` 最新有效行（`status='ok' AND report_type='daily' ORDER BY report_date DESC LIMIT 1`，与 `trader_advisor._gather_inputs` 读"最新评级"同口径）；**V8 合规**（只读，不回写）。
- **判定**：`_rating_conflict`——卖点信号 ×（推荐买入/强烈推荐买入）相悖；买点信号 ×（建议减仓/强烈建议卖出）相悖（`_SELL_CONFLICT_RATINGS`/`_BUY_CONFLICT_RATINGS`）。
- **输出**：检查器 detail 增量键 `current_rating`/`rating_conflict`（随 `trigger_value` JSON 落库，行动清单 t4 可直接消费）；消息尾部追加 **"；注意：当前评级「X」与该信号方向相悖，仅波段参考，以评级为主"**——**评级是唯一动作主指令**，不输出反向无条件指令。无评级/方向一致 → 无注记。
- **双侧覆盖**：`check_tech_signal` 与 `check_sell_signal` 同样附加（买侧 detail 也获得增量键，既有断言零破坏）。

## 四、合成形态实机校准（8 组配方与偏差记录）

| 形态 | 定型配方（`tests/test_sell_signal_alert.py`） | 落位 |
|---|---|---|
| 同日双死叉 | 44×(+2.0) 稳涨 + 加速阳(+8) + 大阴(收138) | `macd_dead_above`+`kdj_dead_high` 同日 == 最新根；双死叉 4★ |
| 跨日双死叉 | 43×(+2.0) + 加速阳 + 中阴(-4) + 大阴(-12) | KDJ 先行、MACD 次日 → 3★ |
| 水下死叉 | 深V(45×-1.5)+横盘+缺口阳(+8)+崩跌(收36) | `macd_dead_below` == 最新根 |
| KDJ普通死叉 | 横盘40 + 中阴(收94)（交叉时 D≈66） | `kdj_dead` == 最新根 |
| 破位MA20 | 上行40 + 收 MA20×0.98 | `ma20_break` == 最新根（全库口径与水上死叉并存） |
| 顶背离 | 峰1(急涨20根到90)→回调5根→缓涨38根微破峰→加速阳→大阴 | `res_top_reverse` 5★；信号全落最新根 |
| 周线空头 | 周K 40×(-1.0)（DIF<DEA）+ 日线死叉 | `res_week_bear` 5★ |
| 负例 | +1横盘（非今日不提醒）/ +3横盘（出窗不报）/ <35根静默 / 深V买点形态卖侧零命中 | 全部按口径收敛 |

**关键校准偏差（记录在案）**：稳步上涨段 KDJ 的 RSV 随滑动窗口缓降，K 全程贴在 D 下方约 0.16——**无加速柱则高位死叉判定永不触发**；配方统一在死叉前加一根加速阳使 K 严格站上 D，再由大阴单日翻转双交叉（021BP"调参直至交叉落位"同款纪律，docstring 已注明）。检测器逻辑零特殊处理，与买侧逐条件镜像。

## 五、接口口径（供 t3/t4 消费）

```python
# modules/market_screener.py
scan_watchlist_sell_signals(stock_ids=None, signals=None, window=3, daily_limit=250)
# → {'scope': 'watchlist_offline', 'side': 'sell', 'stock_count': N,
#    'results': [{stock_id, symbol, name, sell_matches, sell_resonances,
#                 kline_upto, kline_count}], 'errors': [...]}
# 仅收录有命中股票；matches 为窗口内全部命中，"今日出现"过滤在消费方做。

compute_watchlist_sell_result(kline_rows, weekly_rows=None, wanted=None, window=3)
# → {'side': 'sell', 'sell_matches', 'sell_resonances', 'kline_upto', 'kline_count'}

check_sell_signal(cursor, stock_id, min_stars=None, window=3)
# → {'signals', 'resonances', 'kline_upto', 'kline_count',
#    'current_rating', 'rating_conflict'} | None
```

- 共振取最高档：`res_top_reverse` > `res_week_bear` > `res_double_dead`（`_SELL_RES_TIER_ORDER`）；同日双死叉 4★/跨日 3★。
- 环境注记反写：RSI 超买（>70）+ MA20 位置；顶背离可选"·放量滞涨"（末根放量收阴）。

## 六、红线自检

| 红线 | 结论 |
|---|---|
| **平行库边界**（2026-09-07 重设计） | `SIGNAL_LIBRARY`/`RESONANCE_LIBRARY`/`detect_signals`/`detect_resonances`/`run_signal_chunk`/`run_coarse_scan` **零改动**（测试锁定买侧库 4+4 原样）；在线全市场扫描仍只产买点 |
| B24/R13 generate_advice | advisor.py 零触碰 |
| R7/D4 评级映射 | 消费 rating 字符串做相悖比对，不重映射分数→评级；normalize_rating 路径零触碰 |
| V8 预警只读 | 新增读取仅 `daily_reports`（只读）；alert 写入走既有 `INSERT OR IGNORE + UNIQUE` 幂等路径 |
| R9/R10 写库不变量 | 零新表零迁移（alert_type 自由 TEXT）；daily_reports 只读 |
| 021W 多账户 | 本任务未涉持仓查询（t3/t4 范围） |
| 021BN 裸 `<` | 新文案全部文字化；测试断言 `'<' not in message` |
| R16/R18/R19/R12/R17 | 未涉风控阈值/并发超时/调试模式/新依赖 |
| 红线核验锚点 | check_redlines 28/28 基线不动 |

## 七、验证记录

```
python -m pytest tests/            → 981 passed, 1 skipped（fast 层，57s，不触网）
ruff check .                       → All checks passed!
python -m mypy app.py config.py modules → Success: no issues in 55 source files
python scripts/check_redlines.py   → 28/28 通过
node --check static/js/alerts.js   → 语法 OK
```

既有测试零破坏：`test_tech_signal_alert.py` 14 例原样全绿（新种子规则对其深V买点形态零命中，`triggered==1` 等断言不受影响）；`test_market_screener_021bi.py`/`test_action_list.py` 全绿。

## 八、遗留与交接（t3/t4 注意）

1. **t4 行动清单**：卖侧行 reason 如需评级相悖调和，直接消费 alert detail 的 `rating_conflict`/`current_rating` 或镜像 `_SELL_CONFLICT_RATINGS`（与 021BP `_CONFLICT_RATINGS` 并列，勿复用同一元组——方向相反）。
2. **t3 操盘手矩阵**：`_read_watchlist_klines` + `detect_sell_signals`/`detect_sell_resonances` 可直接喂入；detail 评级上下文口径已统一（status='ok' + report_type='daily' 最新行）。
3. **前端看板卖侧展示入口**未做（本任务只落巡检端点 + 预警铃铛链路；行动清单卡片归 t4）。
4. `check_tech_signal`/`check_sell_signal` 的 `window` 固定默认 3（与扫描器一致）。
