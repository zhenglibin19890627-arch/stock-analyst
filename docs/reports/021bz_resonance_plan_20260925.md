# 021BZ 共振类型与强弱显性化：盘点与设计方案（t1）

> 日期：2026-09-25 ｜ 执行：scout（任务 t1，盘点与设计）｜ 批次：021BZ
> 输入：AGENTS.md、docs/RED_LINES.md、docs/reports/021bw_impl_20260924.md、
> modules/market_screener.py、blueprints/market.py、blueprints/analysis.py、
> static/js/market.js、static/js/analysis.js、static/js/portfolio.js、
> modules/alert_engine.py、modules/action_list.py、modules/trader_advisor.py、
> modules/intraday_patrol.py、tests/（021bi/021by/alert/action/routes）
> 硬约束对齐：扫描器只产买点边界不动（卖侧在平行库）；B24 `advisor.generate_advice`
> 冻结（报告侧落点=组装层/展示层）；R7/classify_stage 零改动；文案禁裸 '<'；
> 全量测试保持绿；一致性审计 P0/P1 零新增（基线双零）。

---

## 0. 结论速览

| 项 | 结论 |
|---|---|
| 数据链 | 共振数据已双侧齐备（买侧 4 组/卖侧 3 组，均带 stars+kind+label），**缺口纯在展示层**：触发日期内嵌在 `signals` 字符串、前端星级图标硬编码不读后端 `stars`、报告页无任何共振区块 |
| 强弱分级 | 星级→强/中/弱 纯标注映射（5★强/4★中/3★弱），落地为 market_screener 展示层纯函数；**检测器输出契约零改动**（detect_*/scan_* 不加键） |
| 选股结果表落点 | market.js 共振组渲染改「后端数据驱动徽标」：星级数字+强弱徽标+方向「多」+独立触发日列；修复双金叉跨日 3★ 仍画 4★ 图标的实况不符 |
| 报告页落点 | **推荐方案 A**：advise/report-latest 响应内联（组装层 `_attach_*` 先例，读库离线复算毫秒级），弃方案 B（前端复用两个 watchlist 巡检端点）；权衡见 §4.2 |
| 诚实性 | 分级=星级重标声明文案两处随行；快照口径（kline_upto/窗口/scope）随行；动态星（同日4★/跨日3★）语义随行；全部文案禁裸 '<' |
| 基线 | 红线 **28/28**（本轮实跑）；pytest fast **1286 passed**（本轮实跑）；审计 R1/R2 双零 P0/P1（021BW t4 终态，021BZ 要求零新增） |

---

## 1. 共振数据结构全链盘点

### 1.1 库定义（modules/market_screener.py）

**RESONANCE_LIBRARY（买侧，4 组）**：

| key | label | stars | kind | 备注 |
|---|---|---|---|---|
| res_double_golden | 双金叉共振 | **4★（同日）/3★（跨日同窗，detect 时降级）** | bull | 唯一动态星组（买侧） |
| res_week_daily | 周线共振波段 | 5★ | bull | 周线MACD多头（状态）+日线窗口金叉 |
| res_bottom_reverse | 底部反转共振 | 5★ | bull | 底背离+低位金叉+放量阳线 |
| res_zero_relay | 零轴上二次金叉 | 5★ | bull | 近15日二次金叉且DIF大于0+中位金叉 |

档序 `_RES_TIER_ORDER`：周线共振 ＞ 底部反转 ＞ 二次金叉 ＞ 双金叉——`detect_resonances` **只返回最高档单条**。

**SELL_RESONANCE_LIBRARY（卖侧平行库，3 组，kind=bear）**：

| key | label | stars | kind |
|---|---|---|---|
| res_double_dead | 双死叉共振 | 4★（同日）/3★（跨日同窗） | bear |
| res_week_bear | 周线空头波段卖 | 5★ | bear |
| res_top_reverse | 顶背离反转卖 | 5★ | bear |

档序 `_SELL_RES_TIER_ORDER`：顶背离 ＞ 周线空头 ＞ 双死叉。边界（021BQ 锁定）：卖侧库**严禁**并入买侧库（会泄漏进在线扫描的缺省 wanted）。

### 1.2 detect 层输出契约（两侧同构）

```
detect_signals / detect_sell_signals → [{'signal','label','trigger_date','note'}]
detect_resonances / detect_sell_resonances →
  [{'key','label','stars','kind','note','signals'}]   # 至多一条（最高档）
```

- `note`：库 note + 环境注记尾缀「（RSI超卖环境·MA20下方）」等（不定级）。
- **`signals` 为字符串**：`'KDJ低位金叉@2026-09-24 + MACD水下金叉@2026-09-22'`——
  **触发日期没有独立字段**。`trader_advisor._res_date_desc` 已用正则
  `@(\d{4}-\d{2}-\d{2})` 提取最新日期做时效文案（今日/窗口内历史）——同一解析规则
  目前只有它一处实现，本批应提炼为共享纯函数（单一事实源）。
- 周线共振的周线侧是**状态**（DIF＞DEA）而非事件，无日期语义；日期取日线侧命中。

### 1.3 复算链与端点

| 链路 | 函数 | 网络 | 落库 | 端点 | 输出要点 |
|---|---|---|---|---|---|
| 在线第②段（仅买侧） | `run_signal_chunk` | 腾讯K线逐票 | 无 | POST `/api/market/scan-signals` | results[{symbol,name,matches,pos_pctile,pos_band,resonances}]（blueprint 层另附 industry_flow_bg） |
| 离线买侧 | `compute_watchlist_signal_result` / `scan_watchlist_signals` | **零** | **零** | GET `/api/market/scan/watchlist-signals` | scope='watchlist_offline'，results[{stock_id,symbol,name,matches,resonances,kline_upto,kline_count}] |
| 离线卖侧 | `compute_watchlist_sell_result` / `scan_watchlist_sell_signals` | **零** | **零** | GET `/api/market/scan/watchlist-sell-signals` | +side='sell'，sell_matches/sell_resonances |

读数口径：`_read_watchlist_klines` 读 raw_kline 最近 **250 根**（与评分路径 limit=60 解耦，留足共振③60根/④50根门槛+指标预热）+ raw_kline_weekly 全量。**零新增请求、零写库**——本批报告页方案即复用这条链（§4.2）。
⚠️ 已核实：两个离线巡检端点**当前无任何前端消费方**（static/js 全目录零命中），仅 tests 与服务端函数直调（action_list/patrol）在用。

### 1.4 服务端消费面（口径注意点）

| 消费方 | 用法 | 对本批的约束 |
|---|---|---|
| alert_engine（规则4/5） | `stars >= min_stars` 过滤；detail **显式白名单拷贝** {key,label,stars} | detect 输出加新键**不会**自动进预警消息（键白名单隔离） |
| action_list（行动清单） | reason 文案「{label}（{stars}星）」；detail.resonances 白名单拷贝；top_stars 排序 | 同上；本批不动（§4.4） |
| intraday_patrol | 买卖双侧巡检聚合 | 零改动 |
| trader_advisor | `_read_signals` → buy/sell_resonances → 联动解读 prose（「最高 N 星」+`_res_date_desc` 时效）+ 操作矩阵行 | prose 不动；其日期正则规则提炼共享后可同源（可选，见 §3.2） |

### 1.5 前端消费现状

- **market.js（选股结果表）**：`_MS_RES_META` 按 key **硬编码 ⭐ 图标**（res_week_daily/bottom_reverse/zero_relay 固定5颗、res_double_golden 固定4颗）——**不读后端 `stars` 字段**；组头 label 由 `meta.note.replace(/：.*/, '')` 间接截取。行内「共振构成」列渲染 `res.signals` 字符串+环境注记尾缀。CSV 导出是唯一读 `x.stars` 的地方（'(N星)'）。
- **analysis.js（报告页）**：**无任何共振区块**。唯一的 "resonance" 字样是趋势罗盘 `o.resonance`（trend_analyzer 的「三周期共振上涨」，**另一概念，勿混淆**）。共振信息仅经 /trader-advice 联动解读 prose 间接出现。
- **portfolio.js**：行动清单统计行「共振4星以上 N」计数与 reason 字串渲染，无徽标。

---

## 2. 两处展示缺口定位

### 2.1 选股结果表（market.js msRenderSignals）

| # | 缺口 | 实证 |
|---|---|---|
| G1 | **星级图标与后端数据可不符** | res_double_golden 跨日同窗时后端 stars=3，前端 `_MS_RES_META` 仍画 4 颗星（硬编码）——实况不符 |
| G2 | 无方向标注 | 在线扫描仅买侧，「多」未显性标注 |
| G3 | 无强弱分级徽标 | 全无 强/中/弱 概念 |
| G4 | 无独立触发日列 | 日期埋在 `signals` 字串里；对照组：单一信号分组反而有「触发日」列 |
| G5 | CSV 无强弱/方向列 | 仅 '(N星)' 后缀 |

### 2.2 个股分析报告页（analysis.js renderFullReport）

| # | 缺口 | 实证 |
|---|---|---|
| R1 | 响应无共振数据 | /advise 与 /report-latest 响应均不含共振字段 |
| R2 | 页面无共振区块 | 仅有操盘手卡联动解读 prose（「最高 5 星，触发于…（今日）」），非结构化、无类型/方向/强弱徽标 |
| R3 | 实时/快照两路径无同源保证 | 021K 教训：两路口径漂移=UX 不一致；本批新增字段必须在两路径同源附着 |

---

## 3. 强弱分级设计（纯标注映射，非新增证据）

### 3.1 映射规则

| 维度 | 规则 |
|---|---|
| 强弱 grade | stars→grade：**5★→强、4★→中、3★→弱**；其余/缺失→None（**不硬造**）。双金叉/双死叉的动态星（同日4★/跨日3★）映射后自然得 中/弱 |
| 方向 direction | kind→label：bull→**多**、bear→**空**；未知→None |
| 类型 type | **直接用 library label**（不加字、不改字——单一事实源在库定义） |
| 触发日期 trigger_date | `signals` 字串内最新 `@date`（与 `_res_date_desc` 同一提取规则，提炼为共享纯函数） |
| 时效 timeliness | 渲染侧对比 kline_upto：等于→「今日」，早于→「窗口内历史，非今日」（历史触发不得读起来像新信号——021BR 既有语义） |

### 3.2 落地形态（检测器输出契约零改动）

`modules/market_screener.py` 新增**展示层纯函数**（与库定义同文件同源）：

```text
RESONANCE_GRADE = {5: '强', 4: '中', 3: '弱'}
strength_grade_for(stars)        # 未知/缺失 → None
direction_label_for(kind)        # bull→'多' / bear→'空' / 其他 → None
latest_trigger_date_of(signals_str)  # 正则取 max @date；空/无日期 → None
resonance_view(res, kline_upto=None) # res 的浅拷贝 + {grade,direction,trigger_date,timeliness}（additive，不改原键）
```

要点：
- **detect_resonances/detect_sell_resonances/scan_*/run_signal_chunk 的既有输出键集零变化**（契约锁测试随批新增）；新增键只出现在**端点响应组装层**（additive）。
- alert_engine/action_list 的键白名单拷贝不受影响（自动保持既有行为，符合「防扩散」）。
- 在线 `run_signal_chunk` 给每个 result 附 `kline_upto = klines[-1]['date']`（K线在手零新增请求，additive）——供前端时效徽标与口径随行；离线链已有该字段。
- `trader_advisor._res_date_desc` 可改为复用 `latest_trigger_date_of`（行为等价替换，测试回归网在；若评审裁定不动它也不阻塞——两处规则文档声明同源即可，实施时按最小改动取舍）。

---

## 4. 两处落点设计

### 4.1 选股结果表（market.js + /api/market/scan-signals）

- blueprints/market.py `api_market_scan_signals`：对每条 `result['resonances']` 附 `resonance_view`（additive；kline_upto 已由 §3.2 附上）。
- market.js：
  - 组头：'▸ {label}（N 只）' + 数据驱动星级（'★'×stars）+ 强弱徽标（强/中/弱）——`_MS_RES_META` 降级为纯 note 说明表，**图标不再硬编码**（修复 G1）；
  - 行内：新增「触发日」列（trigger_date + 时效小字，修复 G4）、方向徽标「多」（修复 G2）、强弱徽标（修复 G3）；
  - CSV：加「共振强弱」「方向」「触发日」（修复 G5）。

### 4.2 报告页落点选型（权衡与推荐）

**方案 A（推荐）：advise/report-latest 响应内联（组装层附着）**
- `blueprints/analysis.py` 新增 `_attach_resonance_snapshot(result, stock_id)`：内部复用
  `_read_watchlist_klines` + `compute_watchlist_signal_result` + `compute_watchlist_sell_result`
  （**零网络、零写库、毫秒级**），产出：
  ```text
  result['resonance_snapshot'] = {
    'scope': 'watchlist_offline', 'window': 3,
    'kline_upto': ..., 'kline_count': ...,
    'buy':  [resonance_view(r, kline_upto), ...],   # 0~1 条
    'sell': [resonance_view(r, kline_upto), ...],   # 0~1 条
    'note': GRADE_NOTE_KEY,                         # §5 诚实性文案随响应
  }
  ```
- 附着点两处（与 021BU `_attach_backtest_evidence` 完全同位）：实时路径挂 `_enrich_advice_result`；快照路径挂 report-latest 组装尾部——**存量报告由读取路径现算即刻带块**（021BS 三读取路径先例）。
- B24 合规：`generate_advice` 逐行不动，纯外层组装；失败静默降级（键置 None 不阻塞报告）。

**方案 B：前端复用 watchlist 巡检端点**——打开报告时另发 2 个 GET
（`/api/market/scan/watchlist-signals?stock_ids=id` + `watchlist-sell-signals?stock_ids=id`）。

| 维度 | 方案 A（内联） | 方案 B（前端复用） |
|---|---|---|
| 请求数 | 0 新增（并入既有响应） | 每次开报告 +2 请求 |
| 实时/快照同构 | 天然同构（同一 attach 函数挂两路径） | 前端自行保证，两路径渲染分叉风险 |
| 分级映射实现位置 | **后端单一事实源**（前端纯渲染） | 前端需 JS 版映射+日期正则=**第二实现**（漂移风险） |
| 口径字段（scope/kline_upto/window） | 随响应内联一致 | 分散两端点，前端拼装 |
| 存量旧报告 | 读取路径现算即刻带块 | 同样可得但前端逻辑重 |
| 服务端成本 | +约毫秒/次（250 根K线两次纯计算） | 0（转移到客户端与请求数） |
| 契约耦合 | additive 新键，本域内闭环 | 报告页跨域耦合扫描器巡检端点契约 |
| 先例 | 021BU `_attach_backtest_evidence` / 021BS 读取路径现算（同型同源） | 无直接先例 |

**推荐 A**：单请求、两路径同构、映射单一事实源在后端、021BU/021BS 成熟先例、B24 合规落点与团队约束（报告侧落点=组装层/展示层）逐字对齐。B 的唯一优势（零后端改动）在同源性与口径一致性面前不成立。

### 4.3 报告页展示形态

- 「⚡ 共振信号」条带：置于**操盘手建议卡之前**（与联动解读 prose 相邻互证；独立条带不新开整卡，避免首屏膨胀）。备选位：趋势罗盘卡尾部（若评审偏好合并）。
- 每行徽标组：`[多/空]`（多=红系/空=绿系，红涨绿跌惯例）+ `[类型 label]` + `[强/中/弱]`（强弱色阶）+ `触发 YYYY-MM-DD（今日/窗口内历史）`。多侧在上、空侧在下。
- 空态（诚实，不硬造）：「近 {window} 个交易日内无共振触发」。
- 脚注：§5 说明文案 + kline_upto 口径行。

### 4.4 边界与不做清单（防扩散）

1. 在线扫描**仍只产买点**；卖侧 key 严禁混入买侧库（021BQ 边界原样）。
2. 检测逻辑/星级判定/档序**零改动**；分级是展示映射，**不回写 library、不新增证据**。
3. 行动清单/预警消息/操盘手 prose **不在本批两处范围**（其已有「N星」表述；后续批次可选接 grade，登记不动）。
4. R7 scoring_engine / classify_stage / B24 generate_advice 零触碰；R16 风控阈值不涉及；零新增依赖（R17）。
5. 021BY 遗留观察（离线巡检无 pos_pctile/pos_band）维持原登记，本批不带过界。

---

## 5. 诚实性设计（文案随行）

两处共用的说明文案（后端常量 `GRADE_NOTE_KEY`，随响应携带/前端渲染同一份）：

> 「强弱分级说明：强/中/弱为共振星级（5★/4★/3★）的直接重标，仅统一表述口径，
> 不构成对信号胜率的回测验证。信号为快照参考口径：基于已采集K线离线复算，
> 截止 {kline_upto}（触发窗口 {window} 个交易日）；在线扫描为腾讯K线现拉口径。
> 双金叉/双死叉为动态星级：同日触发 4★、跨日同窗 3★。不构成投资建议。」

规则：
- **不暗示回测验证过强弱**——分级与回测证据（021BU 徽章）严格分栏，不共用色彩语言；
- 样本/窗口口径随行：kline_upto / kline_count / window / scope 字段随响应，文案引用；
- **文案禁裸 '<'**：一律用 ≥/≤/「低于」表述（渲染层 innerHTML 直插处沿 021BN/021BS 先例做转义防守）；本批新增全部文案入测试扫描（§6）。

---

## 6. 测试策略

新增 `tests/test_resonance_display_021bz.py`：

1. **纯函数**：映射全表（5/4/3→强/中/弱；2、None、非 int→None）；direction（bull/bear/未知）；trigger_date 提取（多日期取 max、空串、无 @date→None）；`resonance_view` additive 不改原键、kline_upto 缺失时无时效徽标；**detect_*/scan_*/run_signal_chunk 既有输出键集零变化**（契约锁）。
2. **文案守卫**：`GRADE_NOTE_KEY` 等全部新增文案常量禁裸 '<'（全量扫描，021BW N10 同款手法）。
3. **端点**：/api/market/scan-signals（mock run_signal_chunk）响应共振带 grade/direction/trigger_date/kline_upto；watchlist-signals / watchlist-sell-signals additive 键在场（test_routes 既有断言零回归）；/advise 与 /report-latest 的 `resonance_snapshot` 在场且**实时/快照键同构**；无K线→snapshot 为 None 且 success=true（降级不崩）；B24 锚点：advisor.generate_advice 零 diff（红线核验覆盖）。
4. **回归网**：test_market_screener_021bi/021by、test_tech_signal_alert、test_sell_signal_alert、test_action_list、test_intraday_patrol、test_routes 全绿即零回归。

门禁：fast 全量绿（基线 1286 passed）＋ ruff ＋ mypy ＋ 红线 28/28 ＋ 审计 R1/R2 重跑 P0/P1 **零新增**（基线双零）。

---

## 7. 本轮基线验证记录（t1 实跑，2026-09-25）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 红线 | `python scripts/check_redlines.py` | **28/28 通过** |
| 测试基线（fast 层） | `python -m pytest tests/ -q` | **1286 passed, 1 skipped, 6 deselected**，exit 0 |
| 审计基线 | 021BW t4 终态（CHANGELOG 2026-09-24） | R1/R2 双零 P0/P1——021BZ 验收要求零新增 |

## 8. 实施移交要点（供队长拆分 t2+）

1. 后端一件：market_screener 展示纯函数 + run_signal_chunk 附 kline_upto + market.py/analysis.py 响应附键 + `_attach_resonance_snapshot` 两路径同挂；
2. 前端一件：market.js 数据驱动徽标/触发日列/CSV + analysis.js「⚡ 共振信号」条带（含脚注与空态）；
3. 全程不动清单见 §4.4；文案以 §5 为唯一来源（禁裸 '<' 入测试）。
