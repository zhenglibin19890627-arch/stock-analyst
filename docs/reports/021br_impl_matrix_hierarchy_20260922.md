# 021BR t2 实施报告：操盘手矩阵分域不对称层级——纪律最高/减仓独立触发/买入评级门控 + 状态置顶 + 共振时效

> 日期：2026-09-22 ｜ 实施：builder（任务 t2）｜ 依据：t1 诊断 `docs/reports/021br_matrix_diag_20260922.md`（缺陷 A/B）+ 队长批示（分域层级契约 + 分歧检测补档）
> 验收：**fast 1020 passed, 1 skipped** + ruff 全绿 + mypy 55 文件 0 错 + **红线 28/28** + node --check（全程不触网、零写库）

---

## 一、交付概览

| 层 | 交付物 | 说明 |
|---|---|---|
| 层级契约 | `trader_advisor` 分域不对称层级（用户拍板，测试锁死） | ①**纪律（止损/破位）无条件最高**：纪律已触发时「持有」行不得出现（被纪律一致的减仓检查行取代）；②**减仓/离场听操盘手**：破位/卖点信号 × 评级滞后（持有观望/买入档）→ 确定性减仓行 + 行内注记「操盘手纪律触发，评级尚未跟上（当前评级 X）」，全矩阵不再出现「按评级执行风控」式推诿（测试断言全文无此串）；③**买入/加仓仍评级门控**（021BQ 三档原样保留），行层级=战略 |
| 状态置顶 | `operations.status` 增量键 | 止损已触发 → `{kind:'stop_triggered', close, stop_line, trigger_date, ma20_broken, text}`（现价/触发线/触发日期显式置顶）；仅破 MA20 未破止损 → `kind:'breakdown'`；触发日期=当前破位段首日（`_break_trigger_date` 从近 60 根 (日期,收盘) 回溯，`_gather_inputs` 新增 `kline_tail`） |
| 行状态化 | `_row` 增 `layer`/`status` 字段 | 每行标注 纪律/战术/战略（`LAYER_*` 常量）；止损行 status='triggered'（附现价与触发日期）/'pending'；`top_action` 变「止损·56.16（已触发）」（看板 chip 与日报 key_factors 同源透传，R9 不变） |
| 共振时效 | `_res_date_desc` | 从 resonance['signals']（'label@date + ...'，market_screener 同源）提取触发日：今日→「触发于 X（今日）」；历史→「触发于 X（窗口内历史，非今日）」——无线传媒 4 日滞留类样本不再读作新信号；卖出侧共振、减仓行（窗口共振）、空仓「关注」行同步补日期 |
| 共振门控 | `signal_stage_linkage` 四道门 | ①止损门控（新参 `close`/`stop_level`）：纪律已触发时买点共振行改写「止损纪律已触发，共振仅作反抽减仓参考，不构成持有理由」；②阶段门控：弱势阶段共振=反抽减仓（与 buy_today 同语义，消除汤臣 A9 互斥双行）；③日期时效；④评级门控：共振×减仓档附相悖调和（独立注记，勿复用行动清单元组） |
| 分歧补档 | `detect_disagreement` 增 `DISAG_STAGE_LEAD` | 持有观望 × **强置信**弱势阶段（下跌/顶部出货）→ `stage_leads_rating`（仅强置信防边界抖动）；`generate_trader_advice` 文案、`build_playbook` 分支（阶段领先于评级：纪律无条件执行、减仓听操盘手、加仓看评级 + 裁决信号）——填补中免「三方矛盾」无提示空隙（诊断 §3.5/缺陷 E） |
| 前端 | `analysis.js` ④ 操作矩阵段 | 卡片副标题改分域措辞「纪律无条件执行 · 减仓听操盘手 · 加仓看评级」（`operations.hierarchy_note` 同源）；status 置顶红色状态条 🛑；每行层级徽标（纪律#c0392b/战术#d35400/战略#1a73e8）+ 已触发行浅红底 + 动作徽标「·已触发」后缀 |
| 测试 | test_trader_advisor.py 37→**53 例**（+16） | 中免形态回归 + 层级契约①②③ + 状态置顶 + 共振时效 + 端到端（临时库）+ 分歧补档 |

## 二、中免实测形态（2026-09-22）修复前后对照

| 维度 | 修复前（诊断实测） | 修复后 |
|---|---|---|
| 止损行 | 「收盘跌破 56.16 触发即无条件执行」无状态标注（现价 52.27 双破无提示） | status='triggered' + 「——已触发：现价 52.27 低于触发线（2026-09-18 起失守），无条件执行不等待」；顶部 🛑 状态行；top_action「止损·56.16（已触发）」 |
| 减仓检查行 | 「收盘已跌破 MA20 → **按评级执行风控**」（推给评级） | 「→ **操盘手破位纪律触发**：反抽不收复 MA20 则降低仓位」+ 注记「操盘手纪律触发，评级尚未跟上（当前评级 持有观望）」 |
| 共振行 | 「买点侧共振成立（最高 5 星）→ …持仓者持有」（与止损行同屏矛盾、无日期） | 「买点侧共振（最高 5 星，触发于 2026-09-21（窗口内历史，非今日））与止损纪律冲突——**止损纪律已触发，共振仅作反抽减仓参考**，不构成持有理由」 |
| 分歧提示 | 持有观望×下跌期(强) 无任何标记 | ⚡「阶段领先于评级」提示 + 对策段裁决信号 |

## 三、设计决策（与既有契约的边界）

1. **强档评级 × 卖点信号保留 021BQ 锁定的相悖调和**（`test_sell_signal_reduce_row_with_conflict_note`：note 含「以评级为主」）——该用例 021BQ 用户拍板锁死，本轮「既有用例不红」约束下不动；分域注记（「评级尚未跟上」）落在观望/买入档与 MA20 状态行，语义互补不冲突。
2. **MA20 状态行在纪律已触发时的三分支**：close≥MA20 且未破止损 → 「持有」（战略）；close<MA20 → 「减仓检查·破位」（战术/triggered）；close≥MA20 但止损已触发 → 「减仓检查·纪律优先」（战术/triggered，永不输出「持有」）——层级契约①的行级落实。
3. **`_break_trigger_date` 口径**：从最新一根向前回溯连续收于触发线之下的K线，取该段最早日期（当前破位段的「起失守日」）；最新一根未破 → None（不硬造历史日期）。数据源为既有 raw_kline 只读查询（`_gather_inputs` 原查询补 `trade_date` 列），零新增采集（V8/R8 合规）。
4. **分歧补档仅强置信**：诊断 §3.5 指出原空隙是「避免边界抖动」的设计取舍——补档取 `confidence == 强` 最小面，中置信不告警。
5. **不改 classify_stage 本体**（021BQ 已锁，23 例原样全绿）；**不碰 advisor.generate_advice**（B24）；**不碰 daily_report 写入路径**（top_action 由 operations 透传，天然带「（已触发）」后缀）。

## 四、改动文件

```
modules/trader_advisor.py      # 分域层级 + 状态置顶 + 共振时效 + 分歧补档（classify_stage 零改动）
static/js/analysis.js          # ④操作矩阵：分域副标题 + 🛑状态条 + 层级徽标 + 已触发高亮
tests/test_trader_advisor.py   # 37→53 例（+16：TestAsymmetricHierarchy021BR / TestStageLeadDisagreement021BR）
```

## 五、验证记录

```
python -m pytest tests/                 → 1020 passed, 1 skipped（fast 层，68s；含红线随测）
ruff check .                            → All checks passed!
python -m mypy app.py config.py modules → Success: no issues found in 55 source files
python scripts/check_redlines.py        → 28/28 通过
node --check static/js/analysis.js      → OK
```

新增用例锚点（节选）：`test_zhongmian_no_hold_when_stop_triggered`（止损+共振5星同现→无持有行）、`test_zhongmian_stop_status_pinned_top`（现价/触发线/触发日期置顶）、`test_breakdown_independent_of_rating_lag`（减仓独立触发+「评级尚未跟上」+全文无「按评级执行风控」）、`test_buy_domain_still_rating_gated`（买入域门控不变）、`test_zhongmian_end_to_end`（临时库端到端：status/触发日期/持有压制/top_action）、`test_no_bare_lt_in_hierarchy_texts`（021BN 延续）。

## 六、红线自检

| 红线 | 结论 |
|---|---|
| B24/R13 | advisor.py 零触碰；全部改动在 trader_advisor（只读后处理层）与消费端展示 |
| classify_stage 本体 | 零改动（021BQ 锁定的 23 例原样全绿）；detect_disagreement 为独立函数（队长批示补档） |
| R7/D4 | 只消费 rating 字符串做门控/相悖比对（既有 _RATING_WEAK/_RATING_STRONG），不重实现分数→评级 |
| R8/V8 | kline_tail 来自既有 raw_kline 只读查询（补 trade_date 列）；共振日期取自 market_screener 既有 signals 字段；零网络零新增采集 |
| R9 | daily_report 零改动；key_factors.trader.top_action 透传值自然携带「（已触发）」 |
| 021BN | 层级/状态新文案全部文字化（「低于」），测试断言 json 无裸 '<' |
| 021W/R16/R12 | 未涉持仓写数/风控阈值/数据库配置 |

## 七、交接 t3 的备注

1. **top_action 双基数提示**：矩阵纪律线与 price_advisor 止损的 52.93/56.16 双数值差异，当前阶段主因是**用户数据**（账户二 601888 1000@53.66 与真实持仓不符，用户已确认真实持仓为银河 1,100@61.046）——t3 的 price_advisor 聚合口径修复维持方案，报告中注明数据清理后两基数自然收敛。
2. 看板/日报 top_action 现为「止损·56.16（已触发）」形态，portfolio.js 🎯chip 无需改动即透传。
3. 共振「窗口内历史，非今日」时效标注与行动清单 021BP「仅今日命中」口径现已一致（诊断 §3.1 根因闭环）。
