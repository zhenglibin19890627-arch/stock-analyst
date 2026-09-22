# 021BQ 卖出半边 + 操盘手建议全面化 — 实施方案（盘点产出）

> 日期：2026-09-21 ｜ 编制：scout（盘点任务 t1）｜ 依据：021BP 三份沉淀 + 本轮全量代码盘点
> 红线基线：`python scripts/check_redlines.py` 盘点当日实测 **28/28 全绿**。
> 上轮资产：`021bp_boost_plan_20260921.md`（盘点）/ `021bp_impl_signals_20260921.md`（项1+2 复算+预警）/
> `021bp_impl_action_list_20260921.md`（项3 行动清单）。本轮在其上扩展**卖出半边**与**操盘手建议全面化**。

---

## 一、盘点结论速览（五范围）

| 范围 | 核心结论 |
|---|---|
| ① trader_advisor | 输入里**完全没有短线事件信号**：阶段判定闸门全部是月/周趋势 + 慢均线结构 + 60日量价结构；watch_signals 是"价位触发"（MA20/量比）不是"事件触发"（今日死叉）。"只看长期"是代码结构使然（§二 逐行证据）。改造空间：复用 `_read_watchlist_klines` + 买卖侧纯函数，**增量加 `operations` 操作矩阵段**，阶段判定本身不动 |
| ② 信号库扩展点 | `SIGNAL_LIBRARY`/`detect_signals`/`RESONANCE_LIBRARY` 结构可平行复制；**但 2026-09-07 重设计已明确"死叉已删、选股器只产买点、持仓风险归预警系统"**——卖侧必须走**平行库**（`SELL_SIGNAL_LIBRARY` + `detect_sell_signals` + `detect_sell_resonances`），严禁往 `SIGNAL_LIBRARY` 加 key（会泄漏进在线全市场扫描 `run_signal_chunk`）。共振条目已有 `kind` 字段（'bull'），卖侧 'bear' 结构天然支持 |
| ③ 预警/行动清单接线 | 新 alert_type 的真实同步点是 **8 处**（021BP 报告写"五处"漏计了 alerts.js 的 `_alertTypeLabels` 铃铛 map 与 index.html 下拉选项，实际实现 8 处都同步了）；行动清单排序契约是纯函数 `_sort_key`，021BP 修订的 `_CONFLICT_RATINGS` 相悖调和机制可镜像复用到卖侧 |
| ④ 持仓归属 | `action_list.py` 现在**完全不查 holdings**；正确口径 = `SELECT stock_id, SUM(quantity) FROM holdings WHERE quantity>0 GROUP BY stock_id`（账户无关聚合，天然满足 021W 多行约定）。⚠️ 顺带发现 `trader_advisor._gather_inputs` 持仓读数是 `ORDER BY id LIMIT 1` 单行取——多账户同股分仓时双视角判断会失真，本轮一并修 |
| ⑤ 测试基建 | 021BP 校准形态（45根深跌+1横盘+1缺口大阳 → 交叉必落最新一根）**逐根镜像**即可造死叉形态：45根稳步上涨+1横盘+1缺口大阴 → 水上死叉+KDJ高位死叉同日。顶背离用双峰构造（急涨到峰1→回调→缓涨微创新高），DIF 峰2<峰1。全套离线、不触网，测试基建零缺口 |

---

## 二、范围①（重点）trader_advisor 诊断

### 2.1 现在吃什么输入（modules/trader_advisor.py `_gather_inputs` L152-196）

| 输入 | 来源 | 形态 | 短线事件？ |
|---|---|---|---|
| StockData 契约快照 | `data_adapter.load_stockdata_from_db` | **单点状态值**：ma5/ma10/ma20/ma60/macd_dif/dea/rsi_14/volume_ratio/main_net_inflow(+_5day)/holder_count_change_pct/margin_balance_chg | ❌ 状态，无序列无交叉 |
| raw_kline 近 **60** 根 | 直查 `ORDER BY trade_date DESC LIMIT 60` | 只进 `_volume_structure`：vol_trend(5/20日均量比)、fit_ratio(20日量价配合度)、top_divergence(量价背离)、position_pctile(60日分位)、amplitude_shrink | ❌ 量价结构统计，不算任何指标交叉 |
| 三周期趋势 | `trend_analyzer.analyze_trends(data)` | daily/weekly/monthly 的 trend(up/down/sideways)+strength；**021BN 重设计后月线方向明确时 overall 直接采纳月线**（trend_analyzer.py L17-21） | ❌ 均线/MACD 状态分类 |
| 最新评级 | daily_reports 最新 ok 行 | rating + total_score（分歧检测的"主指令"） | ❌ |
| 持仓 | `holdings WHERE stock_id=? AND quantity>0 ORDER BY id LIMIT 1` | 单行 quantity+cost_price（**多账户缺口**，§五） | ❌ |

### 2.2 阶段判定逻辑为什么"只看长期"（classify_stage L204-309 逐条证据）

1. **下跌期闸门**（L294）：`(monthly_dn or weekly=='down') and close < ma20`——连续 3 天急跌破 MA20/MA60 但周线/月线还多头时，**判不出下跌**（落兜底"震荡无趋势"）。用户看到个股暴跌、操盘手卡却说"震荡"，这是"只看长期"的第一体感来源。
2. **主升/拉升初期闸门**（L270-291）：先要 `bull_stack = MA5>MA20>MA60`（成型需要数周），再叠加 `(weekly_up or daily_up)`——纯日线级别的新启动（周线还没转多头排列）进不了主升/初期的强判定。
3. **顶部出货闸门**（L261-267）：要求 `position>0.7`（60日分位）+ 量价背离/放量滞涨/户数分散——全部是**数周尺度**的顶部结构，单日天量长上影这种短线出货信号完全不在输入里。
4. **trend_analyzer 的月线主导**（021BN）：`analyze_trends` 的 overall 在月线方向明确时整体采纳月线——操盘手卡引用的阶段证据链因此天然月线偏重。
5. **watch_signals 是价位不是事件**（build_playbook L456-466）：输出形如"放量站上 MA20 → 上修判断"、"缩量跌破 MA20 → 下修判断"——用户无法从卡片知道"**今天**发生了什么、要不要动"。价位触发需要用户自己盯盘核对，事件触发才是"今日应做"。
6. **前端无短线面**（analysis.js L1442-1526 `loadTraderAdvice`）：卡片只渲染 ①阶段 ②主力 ③对策 三段 + 收起的判断依据；全应用唯一展示日线信号的地方是预警铃铛与行动清单卡（且 021BP 只接了买侧），**与操盘手卡零耦合**。
7. **日报预计算也只有阶段**（daily_report.py L1122-1133）：`key_factors.trader` 只存 `stage_name/has_disagreement/disagreement_text`——看板摘要层同样没有短线信号位置。

**结论**："只看长期"不是 bug 而是输入层缺失——判定管线里没有任何日线事件信号（金叉/死叉/背离/破位）入口；阶段闸门又全部以月/周趋势为前置。补齐方式不是重调阶段阈值（高风险、021BP 测试已锁行为），而是**并行加一层"短线信号 × 双视角操作矩阵"**。

### 2.3 输出结构（generate_trader_advice L499-522）

```
{ available, stage:{code,name,confidence,evidence[]},
  capital:{summary,tone,details[],blind_spots[]},
  playbook:{profile,headline,actions[],watch_signals[]},
  disagreement:{type,text}|None, rating, total_score, disclaimer }
```

### 2.4 改造空间评估（②操盘手建议全面化）

- **可复用**：`market_screener._read_watchlist_klines(cursor, stock_id)`（021BP 已沉淀的共享读K器：日K 250 根+周K 全量，时间正序，零网络）——操盘手侧无需自写读K；`detect_signals`/新 `detect_sell_signals` 纯函数直接喂入。`generate_trader_advice` 内部已有 connection（`_gather_inputs`），传 cursor 即可，零新增连接。
- **双视角依据**：`holding_qty/cost_price`（聚合修复后）+ 现价 → `view: held|empty`；持仓侧价位三源齐备：成本×0.92（playbook 既有纪律线）、`data.ma20`（StockData）、最新报告 `price_advice` JSON 的 `stop_loss/buy_range`（零重算，参照 `watchlist_scores._parse_pa_zone` 的解析先例）。
- **B24 安全**：trader_advisor 是 2026-09-18 独立只读模块（非 B24 锚定对象，check_redlines 只锚 `advisor.generate_advice` 签名）；扩展走**增量键**（结果 dict 加 `operations` 键，既有键与既有测试零改动）。矩阵必须保持模块 docstring 的主从契约（"评级是唯一动作主指令"，`tests/test_trader_advisor.py::TestPlaybookDisagreement` 锁死）：卖侧行输出为**条件触发式**（"跌破 X → 执行 Y"）+ 与评级相悖时显式调和注记，不输出与评级相反的无条件指令。

---

## 三、范围② market_screener 卖点信号扩展点

### 3.1 现有买侧资产（复用/镜像的模板）

| 资产 | 签名 | 镜像要点 |
|---|---|---|
| `SIGNAL_LIBRARY` | `{key:{label,note}}` | 卖侧平行库同构；**禁止**往里加卖侧 key |
| `detect_signals(kline_rows, wanted=None, window=3)` | → `[{signal,label,trigger_date,note}]` | 交叉判定模板：`dif[i-1]<=dea[i-1] and dif[i]>dea[i]`（反向即死叉）；窗口语义/`<35` 根门槛/低位优先去重（kdj_golden_low break 逻辑）全部照抄反向 |
| `detect_resonances(hits, kline_rows=None, weekly_kline_rows=None)` | → `[{key,label,stars,kind,note,signals}]` | `kind` 字段已有（买侧 'bull'）；环境注记（RSI/MA20 位置）逻辑可复用反写；`_RES_TIER_ORDER` 取最高档机制镜像 |
| `_macd_series(closes)`/`_kdj_series(highs,lows,closes,n=9)`/`_rsi_series(closes,n=14)`/`_ma_series(closes,n)` | 纯函数 | **原样复用，零改动**——与 technical_detail 同源口径不许出现第二实现（R7 精神） |
| `_read_watchlist_klines(cursor, stock_id, daily_limit=250)` | → `(daily_rows, weekly_rows)` | 买卖两侧共用（已在用） |
| `compute_watchlist_signal_result(kline_rows, weekly_rows, wanted, window)` | → `{matches,resonances,kline_upto,kline_count}` | 卖侧镜像 `compute_watchlist_sell_result` |
| `scan_watchlist_signals(stock_ids, signals, window, daily_limit)` | → `{scope,stock_count,results,errors}` | 卖侧镜像 `scan_watchlist_sell_signals`（仅收录有命中股，单只失败不阻塞） |

### 3.2 卖侧信号库设计（`SELL_SIGNAL_LIBRARY`，与买侧同构）

| key | label | 判定（全部事件/窗口口径，window 默认 3） | 镜像 |
|---|---|---|---|
| `macd_dead_above` | MACD水上死叉 | DIF下穿DEA 且交叉时 DIF>0：多头趋势中的派发/回调信号，持仓者减仓警示（比水下死叉更紧迫） | macd_golden_above |
| `macd_dead_below` | MACD水下死叉 | DIF下穿DEA 且 DIF≤0：空头趋势加速信号 | macd_golden_below |
| `kdj_dead_high` | KDJ高位死叉 | K下穿D 且交叉时 D>75：超买区回落 | kdj_golden_low（D<25） |
| `kdj_dead` | KDJ死叉 | K下穿D（一般卖点参考；高位死叉出现时同窗不重复报，优先级逻辑镜像） | kdj_golden |
| `ma20_break` | 破位MA20 | 前收 ≥MA20 且今收 < MA20×1.01（1% 缓冲防毛刺）：事件口径交叉下穿，非状态类 | （无直接镜像，环境注记 MA20 位置的事件化） |

不做：超卖/超买**状态类**信号（2026-09-07 删除理由"状态或反向信号不产候选"对卖侧同样成立——状态由共振环境注记承接）；跌破 MA60/MA120（周期过长，行动清单"今日"语义弱，留待后批）。

### 3.3 卖侧共振库设计（`SELL_RESONANCE_LIBRARY`，kind='bear'）

| key | label | stars | 判定 | 镜像 |
|---|---|---|---|---|
| `res_double_dead` | 双死叉共振 | 同日4/跨日3 | MACD系死叉 + KDJ系死叉同窗 | res_double_golden |
| `res_week_bear` | 周线空头波段卖 | 5 | 周线 MACD 空头（DIF<DEA）+ 日线窗口内死叉：趋势方向的顺势卖点 | res_week_daily（周线多头+日线金叉） |
| `res_top_reverse` | 顶背离反转卖 | 5 | 顶背离（近60日价格创新高而对应 DIF 低于前高，镜像 res_bottom_reverse 的底背离反写）+ 窗口内死叉（KDJ高位死叉优先）；可选放量滞涨注记 | res_bottom_reverse |

不做：卖出侧第 4 组（买侧 res_zero_relay 的镜像"水下二次死叉"信息量低，水下死叉本身已覆盖）；环境注记反写（RSI 超买环境 + MA20 位置）随共振 note 输出。
取最高档顺序：`_SELL_RES_TIER_ORDER = ('res_top_reverse', 'res_week_bear', 'res_double_dead')`（顶背离=最强反转证据 ≥ 周线空头=趋势顺势 ≥ 双死叉）。

### 3.4 关键设计边界（必守）

2026-09-07 重设计在 market_screener.py L83-87/L109 写明："**死叉/超买/超卖类已删——选股器只产买点候选**""空头共振预警（选股器不产卖点，**持仓风险归预警系统**）"。因此：
- `SIGNAL_LIBRARY`/`RESONANCE_LIBRARY`/`detect_signals`/`detect_resonances`/`run_signal_chunk`/`run_coarse_scan` **一律不动**——往 SIGNAL_LIBRARY 加卖侧 key 会让在线全市场扫描第②段（`run_signal_chunk` 的 wanted 缺省=全部 SIGNAL_LIBRARY keys）开始产出卖点候选，直接违反模块边界；
- 卖侧全部走平行函数（§3.2/§3.3/§3.1 镜像清单），在线扫描行为零变化，既有 `tests/test_market_screener_021bi.py` 与 `tests/test_tech_signal_alert.py` 全部断言零破坏。

---

## 四、范围③ 预警与行动清单接线现状

### 4.1 tech_signal 白名单的**真实**同步面（8 处，021BP 文档写五处系漏计）

| # | 位置 | 现状 |
|---|---|---|
| 1 | `modules/alert_engine.py` L49 `VALID_RULE_TYPES` | 4 类元组 |
| 2 | `modules/alert_engine.py` L348 `_RULE_CHECKERS` | 检查器 lambda 注册表（阈值提取在此） |
| 3 | `modules/alert_engine.py` L307 `_format_message` | 按 alert_type 分支出文案（**禁裸 `<`**，021BN） |
| 4 | `blueprints/alerts.py` L10 `_VALID_ALERT_TYPES` | 不同步则创建规则 API 400 |
| 5 | `static/js/alerts.js` L7 `_alertTypeLabels` | **铃铛下拉**类型徽标（漏则显示裸 key） |
| 6 | `static/js/alerts.js` L101 `_alertRuleTypeLabels` | 规则管理列表标签 |
| 7 | `static/js/alerts.js` L107 `_alertRuleTypeHints` | 规则创建 UI 提示（show/hide 阈值输入） |
| 8 | `templates/index.html` L79-83 `#alertRuleType` 下拉 `<option>` | 新建规则入口（021BP 实现同步了此处，方案文档未列） |

### 4.2 预警写入/调度（全部复用，零新增调度）

- 写入：`scan_once()` 的 `INSERT OR IGNORE + UNIQUE(rule_id, stock_id, trigger_date)`（alert_engine L427-437）——同股同日幂等；
- 调度：`daily_report._run_full_report_flow` 15:54 收盘批次挂载，双层异常隔离；021BN-c 实例守卫不受影响；
- "今日出现"口径：`check_tech_signal` L279 `trigger_date == kline_upto` 才提醒——卖侧检查器逐字镜像；
- 阈值语义：共振星级门槛（3/4/5 选填），个股规则 > 全局规则；
- 种子：`database/_db/_schema_alerts.py` `_seed_default_alert_rules`（L75-80 幂等 WHERE NOT EXISTS）——卖侧建议追加 `('sell_signal', None)` 全局默认开启（对称 tech_signal；噪声权衡见 §七 项C）。

### 4.3 行动清单排序契约与相悖调和（modules/action_list.py）

- 排序契约（`_sort_key` L332-344）：P1 评级升降（**降级优先**，再按 |评分变动|）> P2 买点信号（`-top_stars` 降序）> P3 预警未读 > P4 缺报补数；类内按 symbol。P2 内排序键现为 `(p, -stars, symbol)`——**插入卖侧必须扩此元组**（§七 项D 设计：`(p, side_rank, -stars, symbol)`）。
- 相悖调和机制（021BP 修订，commit c23f9ee）：`_CONFLICT_RATINGS = ('建议减仓','强烈建议卖出')`（L43）——买点信号撞上减仓/卖出档评级时，`detail.rating_conflict` 置档位 + reason 追加"超跌反弹、未获趋势确认"调和文案；前端 `renderActionListCard`（portfolio.js L1545-1550）按 `rating_conflict` 把徽标从蓝转琥珀"反弹信号·与评级相悖"。**卖侧镜像**：`_SELL_CONFLICT_RATINGS = ('推荐买入','强烈推荐买入')`——卖出信号撞上买入档评级 → 琥珀"卖出信号·与评级相悖"（短线回调警示，评级未变）。
- 徽标渲染位（portfolio.js L1540-1552）：`it.kind` if-else 链 + `bg/fg/label` 三元组；统计行 L1527-1535 消费 `stats`——卖侧加分支与 `sell_hits` 计数即可。
- 持仓标记缺口：`build_action_list` 输入**无任何持仓信息**——`items/detail/overview` 都不知道"该股我拿着没有"。这是本轮要补的（§五）。

---

## 五、范围④ 持仓归属（卖出信号相关性标注的前提）

- **行动清单侧（新）**：`get_action_list()` 增一条聚合查询（账户无关，满足 AGENTS.md 021W 多行约定——聚合天然无重复行）：
  ```sql
  SELECT stock_id, SUM(quantity) AS total_qty,
         CASE WHEN SUM(quantity)>0 THEN SUM(quantity*cost_price)/SUM(quantity) END AS avg_cost
  FROM holdings WHERE quantity > 0 GROUP BY stock_id
  ```
  → `held_map: {stock_id: {'total_qty': int, 'avg_cost': float|None}}`；`build_action_list` 增参（纯函数保持可测）。卖出行 reason：持仓 → "持仓 N 股（成本 X.XX）——按纪律执行减仓/止损检查"；空仓 → "当前空仓——回避新买入，等待企稳"。
- **操盘手侧（修既有缺口）**：`trader_advisor._gather_inputs` L177-181 现为 `ORDER BY id LIMIT 1` 单行取——同股多账户分仓（021W 明确支持）时 qty/cost 只反映一个账户，双视角（持仓/空仓）判定与持仓 profile 文案失真。修为上同聚合口径（qty=SUM、cost=加权均价，SUM 为 0 时空仓）。改动点在既有私有函数内部，无外部契约。
- 参照先例：`blueprints/portfolio/watchlist_scores.py` L105-111 看板用"最大持仓子查询"（展示口径，不动）；行动清单/操盘手需要的是**存在性+规模**口径，聚合更正确。

---

## 六、范围⑤ 测试基建（死叉/顶背离合成形态配方）

现有模式（全部离线、临时库 `tmp_path + monkeypatch.setattr(db_manager,'DB_PATH',...)`，不触网）：

- 形态校准纪律（test_tech_signal_alert.py `_deep_v_gap_closes` L28-32）：`45根深跌(110-i*1.5) + 1横盘 + 1缺口大阳(+8.0)` → MACD水下金叉+KDJ低位金叉**同日且必落最新一根**（window=3）；断言精确信号集合 + `trigger_date == kline_upto`。若形态误触发/不触发，调斜率/缺口幅度直至交叉落位（实机校准模式）。
- 负例配方现成：追加 1 根横盘 → "今日出现"失效（前日已覆盖）；追加 3 根横盘 → 出窗不报；<35 根静默跳过。

**卖侧镜像配方（本轮新测试直接用）**：

| 形态 | 构造 | 预期 |
|---|---|---|
| 水上死叉+KDJ高位死叉同日 | `base=[60+i*1.2 for i in range(45)]` 稳步上涨 + 1横盘 + 1缺口大阴（今收=昨收-8） | `macd_dead_above`+`kdj_dead_high` 落最新一根（涨势保 DIF>0、D>75，大阴单日翻双交叉） |
| 水下死叉 | 复用 `_deep_v_gap_closes()`（水下金叉落末根）**追加 2-3 根阴跌** | 交叉时 DIF≤0 → `macd_dead_below` |
| KDJ普通死叉 | 温和上涨后 1 根中阴（D 25~75） | `kdj_dead` |
| 破位MA20 | 30 根上行站上 MA20 后，1 根收于 MA20×0.98 | `ma20_break` |
| 顶背离 | 双峰：急涨(+2.0/根×20)到峰1 → 回调5根 → 缓涨(+0.3/根)微破峰1收价 | 峰2处 DIF<峰1 DIF → 顶背离确认（镜像 res_bottom_reverse 反写） |
| res_top_reverse | 顶背离形态 + 窗口内 kdj_dead_high | 5星卖出共振 |
| res_week_bear | 周K `[140-i*1.0 for i in range(40)]` 稳步下行（周线 DIF<DEA）+ 日线死叉 | 5星（镜像 `_WEEKLY_UP`） |
| res_double_dead | 同窗双死叉（同日 4 星/跨日 3 星） | 镜像 res_double_golden 断言法 |

测试文件规划：新增 `tests/test_sell_signal_alert.py`（镜像 test_tech_signal_alert.py 全套：纯函数检测/共振/离线扫描全链/预警幂等/白名单同步 engine↔blueprint/无裸 `<`）；`tests/test_action_list.py` 扩卖侧类（排序 held-first、持仓标记、评级相悖、stats 增键、降级路径）；`tests/test_trader_advisor.py` 扩 operations 矩阵类（双视角/评级主从契约不破/增量键）；`tests/test_routes.py` +1~2 路由冒烟。前端无 JS 测试设施（021BP 先例）：`node --check` 语法验证 + 路由冒烟兜底。

---

## 七、实施项分解（每项 做 / 不做 / 怎么做）

### 项A：卖出信号纯函数库（market_screener.py 平行扩展）——**做，无依赖先行**

- **做**：`SELL_SIGNAL_LIBRARY`（5 信号）+ `detect_sell_signals(kline_rows, wanted=None, window=3)` + `SELL_RESONANCE_LIBRARY`（3 组）+ `detect_sell_resonances(hits, kline_rows=None, weekly_kline_rows=None)`（kind='bear'，含 RSI 超买/MA20 位置环境注记反写）+ `compute_watchlist_sell_result(...)` 镜像包装。模块 docstring 边界段补一句"021BQ：卖侧平行库只服务自选股持仓风险链路（巡检/预警/清单），在线扫描仍只产买点"。
- **不做**：改 `SIGNAL_LIBRARY`/`detect_signals`/`detect_resonances`/`run_signal_chunk`/`run_coarse_scan`；状态类信号；新 pip 依赖；网络函数触碰。
- **红线**：R8（输入=库内K行）；R7 精神（指标口径唯一，复用 `_macd_series` 等，禁第二实现）。

### 项B：自选股卖出信号巡检——**做（依赖 A）**

- **做**：`scan_watchlist_sell_signals(stock_ids=None, signals=None, window=3, daily_limit=250)`（镜像，返回加 `'side': 'sell'` 标注）；端点 `GET /api/market/scan/watchlist-sell-signals`（挂 blueprints/market.py 现有 bp，`scope='watchlist_offline'` 口径标注原样带出）。**选新端点而非给旧端点加 side 参数**：旧端点响应契约已被测试/前端锁定，新端点零契约风险。
- **不做**：改旧端点行为；在线扫描第②段接线。
- **测试**：test_routes.py +1 冒烟；镜像 test_tech_signal_alert 全链用例。

### 项C：预警 sell_signal（第 5 类规则）——**做（依赖 A）**

- **做**：§4.1 的 **8 处同步**全清单执行（`VALID_RULE_TYPES`/`_RULE_CHECKERS`/`_format_message`/`blueprints._VALID_ALERT_TYPES`/alerts.js 三 map/index.html option）；`check_sell_signal(cursor, stock_id, min_stars=None, window=3)` 逐字镜像 `check_tech_signal`（"今日出现"口径、星级门槛、<35 根静默）；种子 `_seed_default_alert_rules` 追加 `('sell_signal', None)` 默认开启；消息文案镜像（"今日出现卖出信号：…；共振组合：…"，禁裸 `<`）。
- **噪声权衡（默认开启的理由）**：与 tech_signal 对称；每日每股至多 1 条（幂等约束）；用户可一键停用全局规则；行动清单侧对非持仓卖出行已降权（项D），铃铛噪声与买侧同级可接受。若实测噪声大，后批可把种子改 disabled 或默认阈值 4 星——不动本轮结构。
- **不做**：新调度器/挂载点（021BN-c）；rating 类映射（不涉评级，D4 锚天然不触碰）；alert_history/alert_rules 表结构变更（alert_type 是自由 TEXT，零迁移）。
- **测试**：镜像幂等/今日口径/星级门槛/白名单同步 engine↔blueprint 用例 + 种子幂等。

### 项D：行动清单卖出行 + 持仓标记——**做（依赖 A；B 非必需）**

- **做**：`get_action_list()` 增 `held_map` 聚合查询（§五）与 `scan_watchlist_sell_signals()` 调用（独立 try 降级，与买侧同型）；`build_action_list(today, stocks, report_rows, alerts_today, signal_result, sell_result=None, held_map=None)`（新参带默认值，既有测试零破坏）；卖侧行 `kind='sell_signal', priority=2`，detail 增 `{signals, resonances, top_stars, kline_upto, rating_conflict, held, total_qty, avg_cost}`；`_sort_key` P2 键扩为 `(p, side_rank, -stars, symbol)`，`side_rank`: 卖出+持仓=0 < 买点=1 < 卖出+空仓=2（持仓风控优先于他人买点、空仓回避信息垫底——"今日应做"语义）；`_SELL_CONFLICT_RATINGS` 相悖调和镜像（reason 注记"短线回调警示，评级未变"）；stats 增 `sell_hits`/`sell_resonance_hits`。
- **不做**：P1/P3/P4 结构重排；kind 命名变更；响应既有键删除/改名（纯增量）。
- **前端**：portfolio.js `renderActionListCard` 增 sell_signal 徽标分支——持仓：绿系（A股绿=风控，与 rating_downgrade 徽标同族）`卖出信号·持仓`；相悖：琥珀 `卖出信号·与评级相悖`；空仓：灰蓝 `回避信号`；统计行加卖出计数；口径脚注文案补"卖出信号同为离线快照参考"。
- **测试**：§六 所列；重点排序用例（held sell > 5星买点 > 空仓 sell）与相悖注记。

### 项E：操盘手建议全面化（operations 操作矩阵）——**做（依赖 A + 项D 持仓口径）**

- **做**（全部在 modules/trader_advisor.py，增量不改旧）：
  1. `_gather_inputs`：持仓读数改聚合（§五）；复用 `_read_watchlist_klines(cursor, stock_id)` 取日K250+周K（cursor 已在手），跑 `detect_signals`+`detect_sell_signals`+两侧 `detect_resonances/detect_sell_resonances`，窗口取 3，产出 `signals_today`（trigger_date==kline_upto 的事件）与窗口内命中；
  2. 新纯函数 `build_operations_matrix(data, stage, rating, holding, close, signals, price_advice) -> {'view': 'held'|'empty', 'signals_today': [...], 'rows': [{action, trigger, level, source}]}`：
     - **持仓视角**行：止损=成本×0.92 与 price_advice.stop_loss 取高者（先到先执行，source=纪律/价格建议）；减仓=窗口内卖侧共振≥4星或水上死叉（trigger 引用具体信号与日期，level 引 MA20/前低）；持有条件=无卖侧今日事件且收盘>MA20；与评级相悖（卖信号×买入档）时行内附调和注记——**主从契约**：全部为"条件→动作"式，不输出与评级相反的无条件指令（TestPlaybookDisagreement 既有断言不破）；
     - **空仓视角**行：回避=卖侧共振≥4星/周线空头+死叉；关注=死叉但共振不足；买入触发=买点信号×评级支持（level 引 price_advice.buy_range，解析参照 `_parse_pa_zone` 先例）；
  3. `generate_trader_advice` 结果增 `'operations': {...}` 键（既有键零改动）；daily_report `key_factors['trader']` 增 `top_action` 一个可选摘要键（增量）；
  4. 前端 analysis.js `loadTraderAdvice` 增"④ 操作矩阵"段（badge 行：动作词配色沿用 dashboard ACTION_COLOR 惯例；含触发条件与价位、来源标注）。
- **不做**：`classify_stage` 阈值/闸门改动（021BP 测试已锁，重调属高风险另立批次）；`advisor.py`/`generate_advice` 任何触碰（B24）；`_determine_action`/price_advisor 矩阵（021BH 已统一过，不动）；预警/清单结构变更（项C/D 已覆盖）。
- **红线**：B24（模块级隔离）；R7（消费 rating 字符串不重映射）；R9（key_factors 仅增量键）；021BN（矩阵文案禁裸 `<`）；R8/R17（无新源耦合/依赖）。
- **测试**：test_trader_advisor.py 扩类——held/empty 双视角、评级主从（买入档+卖信号→输出含调和注记且无反向无条件指令）、无持仓数据降级、增量键不破坏既有结构断言、`generate_trader_advice` 端到端（临时库造K线+holdings 行）。

---

## 八、实施顺序（依赖关系）

```
项A 卖出信号纯函数库 ── 无依赖，先行（纯函数 + 离线测试，零接线风险）
  ├─ 项B 巡检端点        （依赖 A）
  ├─ 项C 预警 sell_signal（依赖 A；与 B 并行可行）
  └─ 项D 行动清单卖出行  （依赖 A + 持仓聚合；与 B/C 并行可行）
项E 操盘手操作矩阵      （依赖 A + D 的持仓聚合口径，最后收口；前端渲染独立可并行）
```

验收命令（全程不变）：
```bash
python -m pytest tests/           # fast 层全绿（不触网）
python scripts/check_redlines.py  # 28/28 基线保持全绿
ruff check . && mypy app.py config.py modules
node --check static/js/alerts.js && node --check static/js/portfolio.js && node --check static/js/analysis.js
```

## 九、红线风险表

| 红线/约束 | 本方案涉及点 | 处置 |
|---|---|---|
| B24/R13 generate_advice | 项E 操盘手扩展 | advisor.py 零触碰；trader_advisor 为独立只读模块，输出仅增量键；矩阵保持"评级是主指令"主从契约（测试锁） |
| R7/D4 评级映射唯一 | 项C/E 消费评级 | 不重实现分数→评级；矩阵只消费 rating 字符串；alert_engine D4 锚（normalize_rating 复用）原样保持 |
| 设计边界（2026-09-07 重设计） | 项A/B 卖侧入库点 | 卖侧走平行库；SIGNAL_LIBRARY/detect_signals/run_signal_chunk 零改动——在线全市场扫描仍只产买点 |
| V8 只读消费 | 项A/B/C/D | 全链只读 raw_kline*/holdings，不写评分/评级/候选表；alert 写入走既有幂等路径 |
| R9 日报不变量 | 项E key_factors | 仅增 `top_action` 可选键；`_save_report`/`_build_markdown_summary` 写入路径零触碰 |
| 021W 多账户多行约定 | 项D/E 持仓查询 | `SUM+GROUP BY` 账户无关聚合，无 JOIN 重复行；不采用单行取（并修复 `_gather_inputs` 既有 LIMIT 1 缺口） |
| 021BN 裸 `<` | 项C/D/E 文案 | 预警消息/清单 reason/矩阵文案全部文字化描述；测试断言保留 |
| R16/R18/R19 | 全部 | 不涉批量/并发/超时；零新增调度器（021BN-c 实例守卫不受影响） |
| R12/R17 | 全部 | 不开 FLASK_DEBUG；零新 pip 依赖 |
| 红线核验锚点 | 全部 | check_redlines 仅锚 alert_engine（D4）与 advisor.generate_advice（B24）签名——改造对象均不在锚区，28/28 基线不动 |
