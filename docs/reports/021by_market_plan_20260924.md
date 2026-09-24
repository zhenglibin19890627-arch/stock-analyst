# 021BY 市场行情页优化方案：现状全景审计 + 选股能力/便捷性设计（证据驱动）（t1）

> 日期：2026-09-24 ｜ 执行：scout（任务 t1，审计+设计）｜ 批次：021BY
> 方法：**全程只读**——审读 `modules/market_screener.py`、`blueprints/market.py`、
> `static/js/market.js`、`templates/index.html`、`blueprints/portfolio/watchlist_scores.py`、
> `modules/trader_advisor.py`、`modules/backtest_engine.py`、`modules/market_overview.py`、
> `database/_db/_schema_analysis.py`、`blueprints/watchlist.py` 及 021BU/021BW 证据报告；
> 引用证据全部复用既有报告结论，未重跑任何回测/查询。零写库、零网络、零触碰
> `advisor.generate_advice`（B24）、评分引擎（R7）、`classify_stage`（021BQ 锁）。
> **红线基线**：`python scripts/check_redlines.py` → **28/28 通过（EXIT=0）**。

---

## 0. 结论速览

1. **位置分位入选股流是本轮唯一 A 级证据项，且实现成本趋近于零**：第②段精筛
   对每只命中股已拉腾讯 120 日 K 线（>60 根），现算 60 日位置分位**零新增网络请求**；
   证据同源口径现成（`backtest_engine._calc_pos_and_dd20` / `trader_advisor._volume_structure`
   同一公式），021BU 实证低位买入 90%（9/10）vs 高位 42%（5/12），分化 +48pp。
2. **换手率/量比证据按分级判「观察项不入机制」**：021BW 修复换手率断供后 S5a 复活，
   但分桶无单调性（换手小于1% 47% / 1-3% 41% / 3-7% 50%），review 定性「留观察项」；
   量比证据跨档方向翻转。→ 二者**不加新筛选键/不加证据化提示**，现有范围筛选保留。
3. **审计发现一处筛选键不对称**（F1）：前端传单数 `board`/`industry` 键，服务端
   `apply_filters` 只认复数 `boards`/`industries`——板块/行业筛选实际只在前端本地生效；
   副作用是服务端 500 行截断发生在行业筛选之前，小行业候选可能被静默丢弃。修复成本
   极低（前端改传数组，服务端零改动），纳入 C7。
4. **便捷性三项零后端**：预设方案走 localStorage（**不复用 `strategy_params` 表**——其语义
   是评分优化审计日志，`optimizer_engine`/`dynamic_optimizer` 有查询面，混入页面预设会污染）；
   结果导出走前端 CSV（Blob + BOM）；加自选分组直达只需把端点已支持的 `group_id`
   接上前端下拉（`POST /api/stocks` 已收 `group_id`）。
5. **行业资金流 × 候选联动做「软联动」**：读库 `industry_fund_flow`（零网络）+
   复用 `match_board_name` 精确→别名→子串匹配器；新浪行业名与东财板块名不完全对齐，
   匹配不上就不显示，不硬造、不做强过滤。
6. **扫描历史跨会话对比明确不做**：`market_snapshot` 整表替换是既定快照语义
   （零新表约束下无多轮存储基础），降级为「会话内内存对比：重扫后新命中标 🆕」（纯前端）。

---

## 1. 现状审计（只读）

### 1.1 市场行情页功能全景（`templates/index.html` view-market，L214-297）

| # | 卡片 | 内容 | 后端 | 前端 |
|---|---|---|---|---|
| 1 | 🔍 全市场选股扫描（021BI） | 第①步快照粗筛 + 第②步信号精筛 + 加自选 | `POST /api/market/scan`、`POST /api/market/scan-signals`、`GET /api/market/scan/library`、`GET /api/market/scan/status` | `msRunCoarse/msStartSignals/msAddSelected`（market.js L73-345） |
| 2 | 📊 大盘指数 | 主要指数行情+评级+刷新 | `GET /api/index-ratings`、`POST /api/index-ratings/refresh` | `loadMarketIndexSection/renderIndexSection` |
| 3 | 💰 行业资金流向 | 温度计/持续榜单/可排序表/日期回看 | `GET /api/market/industry-fund-flow(?date=)`、`POST .../refresh`（10 分钟冷却 020R-34，缺口后台回补 021BJ） | `loadIndustryFlowFor/refreshIndustryFlow/flowSort`（021T 三态排序） |

另有两组**市场蓝图无前端入口**的巡检端点（审计确认，不在本页 UI 上）：
`GET /api/market/scan/watchlist-signals` 与 `watchlist-sell-signals`（021BP/BQ 离线复算，
服务预警/巡检/行动清单链路）——本方案不为其加入口（避免与预警域重复，留后续批次评审）。

### 1.2 两段漏斗参数全景（`modules/market_screener.py`）

**第①段 快照粗筛**（`run_coarse_scan`，L1145-1194）：

| 环节 | 参数/常量 | 说明 |
|---|---|---|
| 新浪全市场列表 | `_SINA_PAGE_SIZE=100`、`_SINA_MAX_PAGES=80`（实际 ~56 页/5553 只）、`_SINA_MIN_INTERVAL=0.35s` | hs_a 按成交额降序分页；连续 3 页失败中止 |
| 行解析 | 正则 `^(60|00|30|68)`、`price>0` | 仅沪深 A 股；板块=主板/创业板/科创板 |
| 行业映射 | `fetch_industry_map` 缓存 7 天（`sina_industry_map` 表，`_SNAPSHOT_MAX_AGE_DAYS=7`） | newSinaHy 类别表 + 按行业节点取成员（~55 行业 ≈ 70 请求，仅在缓存过期时） |
| 快照落库 | `save_snapshot` **整表替换**、`load_snapshot(max_age_hours=6)` 过期标 stale | `market_snapshot` 表，`snapshot_at` 每轮单一时间戳 |
| 卫生线兜底 | `DEFAULT_HYGIENE`：剔ST / 总市值≥100亿 / 换手≥1% | 蓝图 `filters.setdefault` 服务端兜底 |
| 量比增强 | 腾讯 `qt.gtimg.cn` 60 码/批、`_QQ_MIN_INTERVAL=0.25s`、`enrich_top=2000`（≈34 批 ≈25s） | 覆盖量比筛选前全部存活者 |
| 返回截断 | `rows[:500]` | 市值降序前 500 |

**第②段 技术信号精筛**（`run_signal_chunk`，L1197-1225）：

| 环节 | 参数/常量 | 说明 |
|---|---|---|
| 候选上限 | 前端 `_msLocalFiltered().slice(0,300)`；服务端单批 ≤50 | 前端 25 只/批分批驱动 |
| K 线 | 腾讯 `web.ifzq.gtimg.cn` 120 日前复权日K；**仅对日线已触发金叉的候选补拉周K**（60 根） | `_QQ_MIN_INTERVAL=0.25s` |
| 信号库 | `SIGNAL_LIBRARY` 4 类金叉：macd_golden_above/below、kdj_golden_low、kdj_golden | 口径与 scoring_engine 同源（MACD(12,26,9) 复用 `_ema_list`；KDJ(9,3,3) k=d=50 起步；RSI14） |
| 共振库 | `RESONANCE_LIBRARY` 4 组买点：res_double_golden⭐4 / res_week_daily⭐5 / res_bottom_reverse⭐5 / res_zero_relay⭐5 | 取最高档（先到先得）；2026-09-07 重设计后死叉/超买/超卖已删——**选股器只产买点** |
| 触发窗口 | `window` 1/3/5 交易日（默认 3，服务端钳位 1~10） | 最近 N 日内发生的交叉 |
| 卖侧平行库 | `SELL_SIGNAL_LIBRARY`/`SELL_RESONANCE_LIBRARY` + `detect_sell_*` | 只服务持仓风险链路；**严禁并入买侧库**（021BQ 边界） |

### 1.3 筛选引擎 `apply_filters` 全部键 vs 前端 UI 对照（L1074-1142）

| 服务端键 | 类型/缺省 | 前端 UI（id） | 前端传键 | 生效层 |
|---|---|---|---|---|
| `exclude_st` | bool / True | msExSt ☑ | `exclude_st` | 服务端+前端双份 |
| `mkt_cap_min` | float / 100.0 | msMktMin（无 max UI） | `mkt_cap_min` | 服务端+前端 |
| `mkt_cap_max` | float / None | **无 UI** | 不传 | 仅服务端可用 |
| `nmc_cap_min/max` | float / None | msNmcMin~Max | `nmc_cap_min/max` | 服务端+前端 |
| `turnover_min/max` | float / min=1.0 | msTurnMin~Max | `turnover_min/max` | 服务端+前端 |
| `volume_ratio_min/max` | float / None | msVrMin~Max | `volume_ratio_min/max` | 服务端（增强后补跑）+前端 |
| `change_pct_min/max` | float / None | msChgMin~Max | `change_pct_min/max` | 服务端+前端 |
| `boards` | **list** / None | msBoard 单选 | `board`（单数字符串）⚠ | **服务端不生效**，仅前端本地 |
| `industries` | **list** / None | msIndustry 单选 | `industry`（单数字符串）⚠ | **服务端不生效**，仅前端本地 |
| `sort_key` | str / 'mkt_cap' | **无 UI**（固定市值降序） | 不传 | 仅服务端可用 |
| `sort_desc` | bool / True | 无 UI | 不传 | 仅服务端可用 |

stats 漏斗键：`input/after_st/after_mktcap/after_nmc/after_turnover/after_board/after_industry/after_change/after_vratio`。

### 1.4 结果表列与加自选链路

- **粗筛表**（`msRenderCoarse`）：代码/名称/板块/行业/现价/涨跌%/换手%/量比/市值(亿)；
  展示前 100 只，固定按市值降序，**无排序交互**；条件变更即时收窄（仅收窄，放宽需重扫）。
- **信号结果表**（`msRenderSignals`）：4 组共振置顶（加入☑/代码/名称/共振构成/现价/涨跌%/量比/换手%/市值/行业），
  「只看共振」默认勾选；单一信号组可展开（触发日列）。无位置、无行业资金背景、无排序。
- **加自选链路**（`msAddSelected`，L285-345）：勾选去重 → >20 前端拦截（R16 对应）
  → 逐只 `POST /api/stocks {symbol=code, market:'a_stock', name}`（**未传 group_id**，落入未分组）
  → 成功后 confirm 衔接 `runBatchAnalysis(addedIds)` + `navigateTo('#watchlist')`（021BV t5 一键批量分析），
  失败原因去重透出（021BO）。

### 1.5 操作点击路径长度（现状）

**首次全流程选股**：切「市场行情」页(1) → 调 12 项筛选参数(2~13，若非默认) → 点「扫描全市场」(约60s)
→ 点「开始信号扫描」(12 批 ≈60-100s) → 逐个勾选命中股(n 次) → 点「加入自选」→ confirm「批量分析」
→ 自动跳自选页。**≈5+n 次点击 + 两次长等待（合计 2~3 分钟）**。
主要断点：①12 项参数每次重输、无记忆；②位置分位这一最强证据完全不可见，用户无法按它筛/排；
③结果表不可排序、不可导出，深挖靠肉眼；④加自选后全进未分组，需再去自选页整理。

### 1.6 数据流（market_snapshot 语义）

```
新浪 hs_a 56页(~5553只) ─0.35s/页→ fetch_sina_snapshot
  → _parse_sina_row(仅沪深A股) → fetch_industry_map(缓存7天)
  → save_snapshot（market_snapshot 整表替换；snapshot_at=本轮单一时间戳；不关联 stocks 外键）
  → load_snapshot(6h 过期→stale 标注，不自动重拉)
  → apply_filters（纯本地；卫生线兜底）→ 腾讯批量增强量比(≤2000) → rows[:500]
  → 前端 _msRows → _msLocalFiltered（即时收窄，仅收窄）
第②段: slice(0,300) → 25只/批 POST scan-signals → fetch_kline(120日)[+周K仅触发股]
  → detect_signals(4金叉) → detect_resonances(4买点共振) → results
  → 前端 _msSignalHits → 共振置顶渲染 → 勾选(≤20) → POST /api/stocks 逐只
  → confirm → runBatchAnalysis → #watchlist（021BV t5）
```

**语义约束**：快照 ≠ 评级，结果页标注「快照参考」；扫描器只产候选不自动入库；
`market_snapshot` 每轮整表替换、仅存最新一轮——这是本方案「历史对比不做服务端」的根据。

### 1.7 审计发现（设计输入）

| # | 发现 | 影响 | 处置 |
|---|---|---|---|
| F1 | 前端传单数 `board`/`industry`，服务端只认复数 `boards`/`industries`——板块/行业筛选仅在本地生效；且服务端 500 截断发生在行业筛选之前 | 按小行业筛选时候选可能在 500 名后被静默丢弃；重扫+放宽条件语义与用户直觉不符 | 纳入 C7：前端改传数组键，服务端零改动（`apply_filters` 已支持） |
| F2 | 位置分位缺席：第②段 K 线在手却只用于指标计算，最强条件化证据（021BU +48pp）不进选股流 | 用户无法按证据筛/排候选 | C1（核心项） |
| F3 | 结果表不可排序、不可导出；行业资金流表 021T 已有三态排序，两表交互不对称 | 深挖效率低 | C5/C7 |
| F4 | 加自选不传 `group_id`（端点已支持），全落未分组 | 加入后再整理多一步 | C6 |
| F5 | `strategy_params` 表语义=评分优化审计日志（`optimizer_engine` L448/L605、`dynamic_optimizer` L339 写查同一表） | **不可**复用存筛选预设 | 预设走 localStorage（C4） |
| F6 | 行业资金流（东财板块名）与扫描候选（新浪行业名）口径不一；`match_board_name` 已有精确→别名→子串匹配器（`INDUSTRY_ALIAS` 10 条） | 联动只能做软标注，匹配率不保证 100% | C2 软联动，匹配不上不显示 |
| F7 | `market_snapshot` 无 `pos_pctile` 等衍生列，且整表替换语义不可动 | 位置分位必须现算零落库（021BU O6 同理由：防 stored-vs-live 漂移） | C1 现算、响应透出 |

---

## 2. 证据分级（只引用既有回测证据，本轮零重跑）

| 因子 | 证据 | 样本 | 分级 | 本轮处置 |
|---|---|---|---|---|
| **位置分位（60日）** | 推荐买入·动态窗口：低位 90%（9/10）vs 高位 42%（5/12），**+48pp**；T+5 同向 +22pp；减仓档反向分化 -35pp 同显著 | 021BU E-B，成对两侧 n≥10 + 分化≥15pp（生产 `position_note_for` 同门槛） | **A 级（可入机制）** | C1：入选股流（展示/筛选/排序） |
| 换手率区间（评级日） | 021BW 修复断供后复活：换手小于1% 47%（n=126）/ 1-3% 41%（n=155）/ 3-7% 50%（n=50）/ 大于7% INFO（n=11）——**无单调性** | `021bw_review_query_a_rerun_20260924.md` §S5a | **观察项（不入机制）** | 不加证据化 UI；现有 min/max 范围筛选保留 |
| 量比 | 缩量 48% vs 温和放量 30%（+19pp）但**跨档跨带方向翻转** | `021bw_sentiment_plan_20260924.md` S5 | **观察项（不入机制）** | 同上 |
| 升降档后表现 | 升档后 T+5 上涨占比 47%≈随机 | 021BU E-D | 不入个股域 | 与选股流无关，不涉及 |

**位置分位口径同源声明**（本方案落点必须对齐，不得出现第二公式）：
- `backtest_engine._calc_pos_and_dd20`（L182-206）：评级日近 60 根收盘 `(now-lo)/(hi-lo)`，round 3，**少于 40 根返回 None**；021BU 证据 `pos_pctile` 即此列。
- `trader_advisor._volume_structure`（L110-178）：`position_pctile` 同公式（round 2，近 60 根，要求 ≥20 根才算）。
- 分带阈值（`POS_BANDS` / `position_note_for` L292）：**low <40% / mid 40–70% / high ≥70%**。
- 扫描侧新增计算取：近 60 根**收盘**、≥40 根才算、round 3、分带 0.4/0.7——与回测证据完全同口径。

---

## 3. 设计项（每项：做 / 不做 / 怎么做 + 数据源风控 + 红线标注 + 测试）

### C1【做·证据驱动核心】位置分位接入选股流

- **做**：
  1. `market_screener.py` 新增纯函数 `position_pctile_of_klines(kline_rows, lookback=60, min_bars=40)`
     → `{'pos_pctile': float|None, 'pos_band': 'low'|'mid'|'high'|None}`；
     公式/精度/分带与 `_calc_pos_and_dd20` 完全一致（§2 口径声明）。
  2. `run_signal_chunk` 对每只命中股用**已拉取的日 K** 现算并在 results 项附
     `pos_pctile`/`pos_band`（不新增任何网络请求；K 线不足 40 根为 None）。
  3. 前端信号结果表新增「位置」列：显示「低位 12%」/「中位 55%」/「高位 85%」/「—」（数据不足）；
     分带着色仅作视觉分组（具体色值实施定，**禁裸 '<'**：文案一律用「40%以下」「70%以下」表述）。
  4. 筛选键：信号结果区新增「位置分位」下拉——全部 / 低位（40%以下）/ 中低位（70%以下）；
     纯前端过滤 `_msSignalHits`（与 msResOnly 同型、可叠加）。
  5. 排序键：「低位优先」开关——开启时共振组/信号组内按 `pos_pctile` 升序、None 殿后；
     关闭时维持现状（共振星级/出现序）。默认关闭，不改变既有浏览习惯。
- **不做**：
  - 不在第①段/`apply_filters`/`market_snapshot` 层做位置分位（快照行无 K 线；对 5553 只逐票拉 K 线
    = 约 5553 次腾讯请求，严重违反数据源风控——**一票否决项**）。
  - 不入库（零新列零新表；现算零落库，防冻结快照与现算打架——021BU O6 同理由）。
  - 不把位置分位并入 `SIGNAL_LIBRARY`/`RESONANCE_LIBRARY` 触发判定（会改变扫描器
    「只产买点候选」的命中语义；位置分位只做**展示/筛选/排序**的条件化标注）。
  - 不做「位置→分数/仓位」连续加权公式（021BU O7 同裁定：n=10/12 只支持分带方向性，
    不支持伪精确权重；「低位优先」只做分带序，不做打分）。
- **怎么做**：`run_signal_chunk` 循环体内 `klines` 已在手，`detect_resonances` 之后追加一行现算；
  前端 `_msRowCells` 加一列 + 过滤/排序各一段（与既有本地过滤同型）。
- **数据源风控**：**零新增请求**（复用第②段已拉 120 日 K）。
- **红线标注**：扫描器只产买点边界（不改命中判定、不产卖点）；零新表/零落库；
  B24/R7/classify_stage 零触碰；文案禁裸 '<'；R17 零新依赖。
- **测试**：`tests/test_market_screener_021bi.py` 新增 `TestPositionPctile`——
  ①同输入对拍公式（构造 closes 断言 round((c-lo)/(hi-lo),3)）；②不足 40 根 → None；
  ③hi==lo → None；④分带边界 0.39/0.40/0.69/0.70；⑤`run_signal_chunk` 透传
  （monkeypatch `fetch_kline`/`fetch_kline_weekly`，断言 results[0] 含 pos 字段）。

### C2【做·能力】行业资金流 × 个股候选软联动

- **做**：
  1. `blueprints/market.py` 的 `api_market_scan_signals` 在响应组装时为每只命中股附
     `industry_flow_bg`（读库 `industry_fund_flow` 最新交易日，经 `get_industry_flow_bg_map`
     + `match_board_name` 匹配候选的 `industry`——与 `watchlist_scores.py` L237-243
     看板行业背景完全同型同源，不新造匹配器）。表内每批 25 只、查询为全行业一次读库（~90 行/日，毫秒级）；
     蓝图层按交易日做模块级缓存，避免 12 批重复全查。
  2. 前端信号表行业列附 chip：「行业净流入 +2.3 亿 · 连续3日」（匹配成功才显示；
     含 main_net/main_net_5d/streak_days 三选二即可，实施按数据齐备度定）。
  3. 匹配不上的候选如实不显示（不硬造；可在一批响应附 `industry_match_hit/total` 统计供观察）。
- **不做**：不改行业资金流采集与刷新冷却（东财限频红线不动）；不做「按行业资金流强过滤候选」
  （新浪↔东财行业名匹配率不保证，强过滤会误杀）；不新增网络请求；不动 `INDUSTRY_ALIAS` 表。
- **怎么做**：如上；候选 `industry` 在粗筛响应行已带（新浪映射回填）。
- **数据源风控**：读已落库表，**零网络**。
- **红线标注**：数据源解耦（不碰东财管道）；零新表；文案禁裸 '<'。
- **测试**：蓝图响应字段测试（临时库 seed `industry_fund_flow` + mock screener 函数，
  参照 `test_market_020r54.py`/`test_market_021bn.py` 模式）；匹配不上 → 字段缺失不报错。

### C3【做·能力·轻量】重扫「新命中」标记（会话内内存对比）

- **做**：`msStartSignals` 完成时把本次命中 symbol 集合存入页面变量 `_msPrevHits`；
  同一会话再次扫描后，命中结果中新出现的 symbol 标 🆕（纯前端内存，刷新即失，UI 注明「本次会话内对比」）。
- **不做**：服务端多轮快照/历史对比表（`market_snapshot` 整表替换语义不动 + 零新表 +
  两轮全量扫描 2 分钟×2 的成本收益不匹配）；跨会话持久化（localStorage 存命中集会引入
  「陈旧基线」误标问题，明确不做）。
- **数据源风控**：零网络。**红线标注**：零新表；market_snapshot 快照语义不动。
- **测试**：手工验收（扫两轮对比标记）。

### C4【做·便捷】筛选预设方案保存（localStorage）

- **做**：筛选条件条新增「💾 存为方案」「方案」下拉 + 删除入口；
  保存内容 = `_msFilters()` 全部 12 键 + 信号勾选集 + 触发窗口；localStorage 键
  `ms_filter_presets_v1`（结构 `{name: {filters, signals, window, saved_at}}`），
  上限 10 个方案（超限提示先删）；页面加载时恢复上次使用的方案名（不自动改值，仅记忆）。
- **不做**：**不复用 `strategy_params` 表**（F5：其语义是评分优化审计日志，优化器有查询面，
  混入页面预设属语义污染且违背「表用途单一」；零新表约束下 localStorage 是唯一干净落点）；
  不做云端/跨设备/跨浏览器同步；不做预设分享导入导出（v1 从简，CSV 导出已覆盖数据外带）。
- **数据源风控**：零网络零后端。**红线标注**：零新表 ✅（这正是选 localStorage 的理由）；R17 零依赖。
- **测试**：手工验收清单（保存→刷新页面→加载→覆盖→删除→上限提示）。

### C5【做·便捷】扫描结果导出 CSV

- **做**：粗筛表与信号结果区各加「⬇ 导出CSV」；前端 Blob 生成（**UTF-8 带 \ufeff BOM**，
  防 Excel 中文乱码）；导出**当前过滤后全集**（不是仅展示的前 100/前 300 行）；
  列与页面一致，信号区附信号/共振/位置分位列。
- **不做**：后端 Excel 端点（`export_engine`/openpyxl 无需改动；看板 `/api/export/watchlist`
  是自选股域先例，不混用）；PDF；自动定时导出。
- **数据源风控**：零网络。**红线标注**：R17 零新依赖；文案禁裸 '<'（表头文案检查）。
- **测试**：手工验收（导出→Excel 打开中文正常、行数=过滤后全集）。

### C6【做·便捷】加自选分组直达

- **做**：`msAddBar` 增加分组下拉（数据源 `GET /api/groups` 既有端点，含自选组计数）；
  `msAddSelected` 的 `POST /api/stocks` 请求体附 `group_id`（**端点已支持**，
  `blueprints/watchlist.py` L323/L351——零后端改动）；默认「未分组」。
- **不做**：扫描页内新建分组入口（自选页「管理分组」已覆盖，避免双入口状态不一致）；
  **不改 ≤20 批量上限**（R16 红线，前端拦截保留）；不加「加入后自动采集」开关
  （021BV t5 的 confirm 衔接批量分析已覆盖该意图）。
- **数据源风控**：加自选仍为逐只 POST（现状），新增一次 `GET /api/groups`（本地库，零外部请求）。
- **红线标注**：R16 批量 ≤20 不变；零新表。
- **测试**：`/api/stocks` 已有 group_id 测试面回归 + 手工验收（选分组加入 → 自选页对应组可见）。

### C7【做·审计修复+交互补齐】筛选键对称化 + 粗筛表排序

- **做**：
  1. F1 修复：前端 `_msFilters()` 改传 `boards: [v]` / `industries: [v]`（非空时数组），
     服务端 `apply_filters` **零改动**（已支持复数键）；同时保留本地过滤（二者口径自此一致，
     500 截断发生在服务端筛选**之后**，小行业候选不再被静默丢弃）。
  2. 粗筛表头三态排序（复用 021T `flowSort` 同型交互：现价/涨跌%/换手%/量比/市值），
     纯前端对 `_msLocalFiltered()` 结果排序；说明行「按市值降序」文案随排序状态动态化。
- **不做**：服务端 `apply_filters` 语义变更；多列组合排序；分页/虚拟滚动（100 行上限 + C5 全集导出已够用）。
- **数据源风控**：零网络。**红线标注**：零新表；服务端兜底卫生线不变。
- **测试**：`TestApplyFilters` 回归（boards/industries 数组路径已有覆盖）+ 手工验收
  （小行业扫描：重扫后候选数与本地筛一致）。

### C8【不做 / 暂缓清单】（诚实边界，防范围蔓延）

| 项 | 裁定 | 理由 |
|---|---|---|
| 换手率区间证据化筛选/提示 | **不做** | 021BW 修复后 S5a 分桶无单调性（47%/41%/50%），review 定性「留观察项，不入机制」；数据需随回补继续积累，待月度复核有 A 级结论再评审 |
| 量比证据化 | **不做** | 跨档跨带方向翻转（021BW S5 观察项） |
| 服务端扫描历史对比/多轮快照 | **不做** | market_snapshot 整表替换是既定快照语义（红线级先例）；零新表约束下无存储基础；C3 内存方案承接轻量需求 |
| 行业资金流强过滤候选 | **不做** | 新浪↔东财行业名匹配率不可控，强过滤误杀风险；先经 C2 软联动观察匹配率 |
| 位置分位→打分/仓位映射 | **不做** | 021BU O7 同裁定（证据只支持分带方向性） |
| 自选巡检端点（watchlist-signals/sell-signals）加市场页入口 | **暂缓** | 服务预警/行动清单链路（021BP/BQ），市场页重复入口破坏分域；留后续批次评审 |
| 快照自动定时刷新 | **不做** | 新浪 56 页/轮的请求密度必须由用户显式触发（数据源风控）；6h stale 标注已引导 |

---

## 4. 红线合规标注汇总

| 红线/约束 | 本方案涉及点 | 合规方式 |
|---|---|---|
| 扫描器只产买点边界（021BQ 先例） | C1 | 位置分位仅展示/筛选/排序，不进 SIGNAL_LIBRARY/RESONANCE_LIBRARY、不改 detect_* 命中判定；卖侧平行库零触碰 |
| R16 批量 ≤20 | C6 | 上限与前端拦截原样保留 |
| 零新表 / market_snapshot 语义不动 | C1/C3/C4 | 位置分位现算零落库；历史对比降级为会话内存；预设走 localStorage |
| B24（advisor.generate_advice） | 全案 | 落点仅 market_screener.py / blueprints/market.py / market.js / index.html，零触碰 |
| R7（scoring_engine）/ R8（契约）/ classify_stage（021BQ 锁） | 全案 | 零触碰；不新增数据源字段；证据只展示不回灌评分 |
| 数据源限频（新浪/腾讯/东财风控） | C1/C2/C8 | C1 零新增请求（复用第②段 K 线）；C2 读库零网络；明确否决「全市场逐票拉 K 线」与「快照自动刷新」；全案**零新增**新浪/腾讯/东财请求 |
| R17 零代码约束 | C4/C5 | localStorage + 前端 Blob CSV，零新 pip 依赖 |
| 文案禁裸 '<' | C1/C5/C7 | 新 UI 文案一律「40%以下」「70%以下」等表述 |
| 诚实原则（021BU 批次约束） | C1/C2 | 数据不足显示「—」不硬造；行业匹配不上不显示；会话内对比明确标注时效 |

## 5. 数据源风控总表（扫描请求频率不变量）

| 通道 | 现状 | 本方案后 |
|---|---|---|
| 新浪 hs_a 列表 | 用户显式点击触发，0.35s/页，~56 页/轮 | **不变**（零新增） |
| 新浪行业映射 | 7 天缓存，~70 请求/次 | 不变 |
| 腾讯批量行情增强 | 0.25s/批，≤34 批/轮 | 不变 |
| 腾讯日K/周K（第②段） | 候选 ≤300，25 只/批，触发股补拉周K | 不变（位置分位复用同一份 K 线） |
| 东财（行业资金流） | 刷新冷却 10 分钟 + 缺口后台回补 | 不变（C2 只读已落库数据） |

## 6. 测试策略与验收

1. **Python 单测**（`tests/test_market_screener_021bi.py` 扩展 + 蓝图测试新文件）：
   `TestPositionPctile`（公式对拍/边界/不足门槛）、`run_signal_chunk` 透传（mock K 线）、
   scan-signals 响应 `industry_flow_bg` 字段（临时库 seed）。
2. **门禁**：`python -m pytest tests/`（默认 fast 层）+ `python scripts/check_redlines.py`（28/28）+ `ruff check .`。
3. **前端（无 JS 测试框架，与项目现状一致）**：逐项手工验收清单（C4 保存/加载/删除、
   C5 导出 BOM 中文、C6 分组直达、C7 排序与 boards 数组、C1 筛选/排序/列渲染、C3 🆕 标记）。
4. **收尾**：`python -m pytest tests/ -m "slow or not slow"` 全量绿（实施任务验收步骤）。
5. **一致性守卫**：位置分位公式对拍测试即「同源唯一真相」守卫——若未来
   `_calc_pos_and_dd20` 口径变更，对拍测试会红（不设第二套审计脚本，从简）。

## 7. 实施顺序建议与移交边界

- **建议顺序**：C1（核心证据，含 F7 论证）→ C7（F1 修复，独立小改）→ C4 → C5 → C6（便捷三件，纯前端）
  → C2（联动）→ C3（锦上添花）。C1 与 C7/C4/C5/C6 无依赖可并行；C2/C3 在结果表结构稳定后做。
- **开放问题（不阻塞实施）**：
  1. C2 新浪行业名↔东财板块名匹配率需实施时实测（响应统计字段已设计）；若长期低于五成，
     再评审是否补新浪↔东财别名映射表（新表需豁免，本批次不做）。
  2. 「低位优先」排序默认关闭，是否改为默认开启建议实施后真实使用一周再定（UI/UX 反向校验
     契约层——AGENTS.md §9.6 教训）。
  3. 换手率/量比证据随 021BW 回补推进（36 只余量），月度复核若出现 A 级单调结论，
     可按 C1 同型路径低成本接入（本方案接口设计已预留 `pos_band` 同型的分带字段模式）。

## 8. 验证记录（本任务）

| 验证项 | 命令 | 结果 |
|---|---|---|
| 红线基线 | `python scripts/check_redlines.py` | **28/28 通过（EXIT=0）** |
| 只读性 | 全程仅 read/glob/grep 审读，零写库、零网络、零触碰受保护对象 | 确认 |

> 未跑 pytest：本任务零代码变更（仅新增本方案文档），红线 28/28 已确认基线；
> 全量测试门由实施任务验收步骤覆盖。

**产物**：本方案 `docs/reports/021by_market_plan_20260924.md`。
**移交**：C1-C7 设计项 + F1 审计修复 + C8 不做清单；实施落点文件清单见各设计项「怎么做」。
