# 021BT 盘中操作便利化 — 实施方案（盘点产出）

> 日期：2026-09-23 ｜ 编制：scout（盘点任务 t1）｜ 依据：AGENTS.md / docs/RED_LINES.md /
> CHANGELOG 020R-59/020R-60/2026-09-08 修复/021BN-c / docs/reports/021bq_sell_side_plan_20260921.md / 本轮全量代码盘点
> 硬约束重申：①数据源风控（腾讯克制频率、只交易时段运行、失败静默降级）；②**收盘确认口径零改动**
> （盘中提示一律标注"盘中口径，以收盘确认为准"）；③B24 / R7 / classify_stage 零触碰。

---

## 一、盘点结论速览（七范围）

| 范围 | 核心结论 |
|---|---|
| ① 盘中实时快照链路 | **有现成轮子**：`kline._refresh_kline_today_bar`（020R-60，腾讯行情 1 请求刷当日 bar，`data_source='tencent_intraday'` 标记 + 收盘回填闭环已修复验证）。但巡检**推荐不写 raw_kline**（走 price_cache），把收盘口径隔离风险降到零（§二.1） |
| ② price_cache 刷新链路 | `_fetch_realtime_price_batch(symbols_markets)` 腾讯批量（≤40 只/请求），单轮打点=1 个 HTTP 请求；**该链路零限频**（无最小间隔/UA 池/超时包装）；refresh-prices 刷的是**全部非退市股**而非仅持仓（§二.2） |
| ③ mootdx 五档盘口 | 链路可用（TCP socket 不经 requests，天然隔离）但**非交易时段返回上一交易日快照**（021C 周末脏行教训）；触线判定只需现价，021BT **不建议引入盘口**（§二.3） |
| ④ 调度挂载点 | backfill tick 是"Timer 串联 + 动态间隔 30~240min"，**不适合盘中巡检主时钟**（间隔不可控）；应**新建独立巡检调度器**（镜像同款模式）；交易时段判定**有现成函数** `_is_intraday_session`（含收盘竞价、不含开盘竞价、节假日无感知）（§二.4） |
| ⑤ 止损判定参数化 | `_scan_stop_discipline(cursor, held_map)` / `build_operations_matrix(...)` 均为纯函数、收盘价来源集中（raw_kline 最新收盘）；**参数化可行且低风险**：增可选参默认 None=现行为逐字保持；更保守的选项是 021BT 完全不碰这两处、速览卡独立计算（§二.5，推荐） |
| ⑥ 前端看板 | `loadDashboard` 并行 3 请求（summary / watchlist-scores / dashboard/action-list，`cache:'no-store'` 021E 教训）→ 速览卡走**第 4 路并行 + 独立局部刷新**，失败静默降级先例现成（§二.6） |
| ⑦ 风控测算 | 腾讯侧 collector `_http_get` 无节流；market_screener `_QQ_MIN_INTERVAL=0.25s`（自管管道）；东财 `_EM_MIN_INTERVAL_SECONDS=0.5s`（巡检不碰东财）。8 只持仓 1 批量请求/轮：5 分钟间隔=**≤48 请求/日**，平均 ≤0.008 req/s，余量极大（§五） |

---

## 二、逐项盘点（签名 / 调用方式 / 锚点）

### ① 盘中实时快照链路（020R-59 → 2026-09-08 闭环）

**文件：`modules/collector/kline.py`**

| 资产 | 签名 | 要点 |
|---|---|---|
| 时段判定 | `_is_intraday_session(market='a_stock') -> bool`（L73-85） | 周一~五（`weekday()>=5` False）；A股 `[9:30,11:30] ∪ [13:00,15:00]` **闭区间**；港股 `[9:30,12:00] ∪ [13:00,16:00]`；北京时间 `_CN_TZ`；**节假日无感知**（命中时段拿不到当日数据→安全回落） |
| 当日 bar 刷新 | `_refresh_kline_today_bar(symbol, market, stock_id, today_str) -> bool`（L88-158，`@retry` MAX_RETRIES=3） | 腾讯 `https://qt.gtimg.cn/q={prefix}{code}` **一个请求**取 `[3]现价 [4]昨收 [5]今开 [6]成交量 [33]最高 [34]最低`；`INSERT OR REPLACE raw_kline` 当日行，**保留既有 amount/turnover**，`data_source='tencent_intraday'`；失败返回 False 保持旧 bar |
| 收盘回填闭环 | `fetch_kline` 同日跳过分支（L184-223） | 盘中：跳过前刷今日 bar；**收盘时段检测当日行 `data_source='tencent_intraday'` → 不跳过、重采回填真实收盘**（2026-09-08 修复，东山精密错 3.2% 案例；测试 `tests/test_intraday_020r59.py` 17 例锁行为） |

**复用判定**：轮子本身可直接复用（不需要新行情解析）。但巡检**默认不调用它**——它写 `raw_kline` 当日行，而评分/K线/趋势全链路消费 raw_kline；虽然 `tencent_intraday` 标记 + 收盘回填机制已闭环，盘中巡检若 5 分钟一轮写当日行，等于让"K线库"承载高频快照职责，一旦未来任何链路漏看标记，风险面放大。**021BT 推荐形态（模式 B）**：

- 巡检取价 → 写 `price_cache`（与手动"刷新价格"同表同语义，持仓页现价口径天然一致）→ 触线判定在内存纯函数完成 → 结果仅呈现于速览卡；
- `raw_kline` 全程只读（读最新收盘做"收盘参照价"展示）；用户盘中点"一键分析/盘中快报"走 020R-59 既有链路（行为不变，不在本批范围）。

> 备选模式 A（巡检调 `_refresh_kline_today_bar`）收益仅是"行动清单/矩阵若盘中重算可见盘中价"，但两处矩阵/清单 021BT 都不动（§二.5），收益为零，风险为正——不采用。

### ② price_cache 刷新链路

**文件：`blueprints/portfolio/market.py`（facade `blueprints/portfolio/__init__.py` L49 再导出，测试引用 `tests/test_routes.py` L212-226）**

| 资产 | 签名 | 要点 |
|---|---|---|
| 批量取价 | `_fetch_realtime_price_batch(symbols_markets) -> {stock_id: {'price','pct_change'}}`（L39-133） | 入参 `[(stock_id, symbol, market),...]`；腾讯 `qt.gtimg.cn/q=sh600000,sz000001,hk00700` **逗号批量 ≤40 只/请求**（timeout=8，裸 `requests.get`）；`parts[3]`现价/`parts[32]`涨跌幅；A股缺失逐只 mootdx 降级（`get_realtime_quote_mootdx`）；港股 `HK3690→hk03690` 归一（021M 修复） |
| 刷新端点 | `POST /api/portfolio/refresh-prices` → `api_refresh_prices`（L136-218） | 取 `stocks WHERE status!='delisted'` **全部非退市股**（持仓+自选全集，当前约 40+ 只）→ 批量取价 → `INSERT OR REPLACE price_cache`；缺失股降级 raw_kline 最新收盘（**保留旧缓存，禁止价格归零**）；响应含 realtime/fallback 计数与 `fetch_duration_ms` |
| 限频现状 | —— | **零限频**：无最小间隔、无 UA 池、无 `_call_with_timeout` 包装；批量接口天然 1 请求/次触发；前端两个入口（portfolio.js `refreshPrices` L903、watchlist.js L90）均可连点无节流（021BN-c 运维教训："风控期间勿反复手动点刷新"） |
| price_cache 消费方 | holdings.py L44-49 / watchlist_scores.py L127 / export_engine.py | `PRICE_CACHE_TTL_HOURS=24`（config.py L125）→ `price_expired` 标记 + `data_status` realtime/cache/offline 三态（portfolio.js `_updateDataStatus` L940） |

**含义**：①巡检复用批量取价的"1 请求覆盖 8 只"形态即可，频率设计与只数解耦；②巡检若写 price_cache，与手动刷新幂等同行（INSERT OR REPLACE，WAL 下安全），持仓页现价自动同源；③速览卡新鲜度直接用 `updated_at`，无需新表。

**双实现风险提示**：`_fetch_realtime_price_batch` 位于蓝图层且被 facade/测试锁定签名；modules 层若反向 import 蓝图会形成 `modules→blueprints→modules` 架构异味。**推荐**：把该函数平移至 `modules/realtime_quotes.py`（纯 HTTP 无 Flask 依赖，逐字搬移），蓝图与巡检模块同源 import，facade 处保留一行再导出（`blueprints.portfolio._fetch_realtime_price_batch` 引用面零破坏）。备选：巡检模块内复制同构实现（标注"与 market.py 同源口径，改动双侧同步"）——不推荐（违反口径唯一精神）。

### ③ mootdx 五档盘口

**文件：`modules/collector/mootdx.py`**

- `fetch_orderbook(symbol, market, force_full=False) -> (status, message)`（L274-354）：周末守卫（021C：非交易日 mootdx 返回上一交易日快照，盖当日日期=脏行，实测 23 行）；仅 A股；写 `stock_orderbook`（UNIQUE(stock_id, trade_date) 每日覆盖）。
- `_fetch_realtime_quote_mootdx(symbol)`（L220-252）：TCP socket 直连（不经 requests 全局补丁，与 HTTP 风控天然隔离），返回 price/pct_change/五档 bid/ask + quote_time；失败 None 不抛。
- 客户端单例 `_mootdx_client()`（L53-102）：备用服务器池健康检查（~0.1s/台）→ 全败 bestip 全网扫描（~70s）→ 本进程不再重试。

**021BT 判定：不引入**。理由：①触线/逼近判定只需现价，腾讯批量行情已覆盖（A/H 混合持仓 mootdx 还不支持港股）；②非交易时段拿到陈旧快照，需自建时段+日期双重守卫，复杂度不值；③TCP 长连接 + 首次初始化 70s 尾巴，与"静默轻量巡检"定位冲突。留待未来"盘中五档"独立批次再议。

### ④ 调度挂载点

**文件：`modules/backfill_scheduler.py`**

- tick 结构（L383-479）：`_tick()` try/except 全包 → 末尾 `_schedule_next(_backoff_min)` `threading.Timer`（daemon）**串联注册，天然无重叠**；间隔动态：`BASE_INTERVAL_MIN=30` → 失败率≥80% 逐轮 ×2 上限 `MAX_INTERVAL_MIN=120` → 数据完整降 `IDLE_INTERVAL_MIN=240`。
- 子任务挂载先例（021AW/021AX/021BA）：`_tick()` 顶部依次 `_maybe_refresh_stale_indexes(now=None)` / `_maybe_backfill_industry_flow(now=None)` / `_maybe_backfill_industry()`，各自独立 try/except（"异常（不影响补采）"）+ 各自时间窗守卫（`INDEX_STALE_CHECK_AFTER=(16,15)`）+ `now` 注入参数便于测试。
- 启动注册：`app.py main()` L192-200 `start_scheduler()`（daily_report）→ `start_backfill_scheduler()`，均幂等 + atexit。
- 定时钟点先例（daily_report.py）：三窗 `(15,30)/(15,42)/(15,54)` + 港股 `(16,10)` 一次性 daemon Timer + 021AY 启动补跑（错过钟点补触发）。

**挂载决策**：
1. **不挂 backfill tick**——tick 间隔动态 30~240min，盘中粒度完全不可控（数据完整日巡检可能整日只跑 1~2 轮）；且 tick 内跑的是全维度补采（东财/westock 重活），不应被盘中节奏牵引。
2. **新建独立轻量调度器** `modules/intraday_patrol.py`，镜像 backfill 模式：`start_intraday_patrol()`（幂等 + `_scheduler_started` 守卫 + atexit 注册 stop）→ `_patrol_tick()` try/except 全包 → `_schedule_next()` 固定间隔 Timer 串联。app.py main() 追加一行注册。
3. 启动即检：`start_intraday_patrol()` 先立即跑一轮 `_patrol_tick()`（若正处交易时段），避免服务午间重启后空等一个间隔。
4. 无需 021AY 式补跑——巡检错过即错过，下一 tick 自然恢复（时钟型断点无数据一致性后果）。
5. 单实例守卫：021BN-c ①已根治双实例僵尸（health 探测 + 绑定失败 `sys.exit(1)`），巡检 Timer 随主进程存亡，无需额外守卫。

**交易时段判定答疑（任务问）**：现成函数 `_is_intraday_session` **直接复用**；A股口径 `[9:30,11:30]∪[13:00,15:00]` 闭区间 → **含 14:57-15:00 收盘集合竞价，不含 9:15-9:25 开盘集合竞价与 9:25-9:30 静默期**；午休 11:30-13:00 自动排除；周末守卫有、**节假日无感知** → 巡检需自加节假日守卫：腾讯批量响应每行快照时间戳字段 `parts[30]`（YYYYMMDDHHMMSS 形态）≠ 今日 → 判定非交易日/停牌，静默跳过该股**不写库不报警**（实施时先用真实响应核验该下标，020R-60 只锁了 3/4/5/6/32/33/34）。A/H 混合持仓按市场分别判定，任一市场在盘中即执行该市场代码的批量请求。

### ⑤ 止损/纪律判定纯函数与参数化可行性

| 位置 | 现口径 | 参数化评估 |
|---|---|---|
| `modules/action_list.py::_scan_stop_discipline(cursor, held_map)`（L143-202，021BR t3 路0） | 现价=**raw_kline 最新日K收盘**（内部直查 L160-168）；有效止损=`max(聚合成本×0.92, 最新ok日报 price_advice.stop_loss)`（与 `trader_advisor._stop_level` 同口径）；`close < eff` 才列出；单股异常跳过 | **可参数化**：`def _scan_stop_discipline(cursor, held_map, price_map=None)`，`price_map={stock_id:{'price','as_of'}}`；None → 现行为逐字保持（默认参数向后兼容，021BR 既有测试零破坏）；传入时用其价判定并在行 dict 增 `price_source/as_of` **增量键**。`build_action_list` 已收 `discipline_rows` 透传，纪律行 reason 文案（L276-279 硬编码"日K收盘"字样）需按行内 source 分支标注盘中口径 |
| `modules/trader_advisor.py::build_operations_matrix(...)`（L793+ 纯函数） | close 为入参（调用方传 raw_kline 收盘）；`_stop_level(cost, price_advice)`（L642-656）双源取高；021BR `status_line`（`stop_triggered`/`breakdown` 置顶状态行 L840-875）；`price_source` desc 硬编码"raw_kline 日K收盘"（L880-885） | 技术上可加 `price_source_label` 可选参，但 **021BR 测试锁定 + 分域不对称层级契约复杂，021BT 不动它**（推荐）：操盘手矩阵保持纯收盘口径；盘中触线由速览卡独立计算呈现，两口径并存且各自显式标注（现状本就如此：audit_consistency_021bs_r2 N06 记录"price_cache 盘中 vs raw_kline 收盘双口径，特性非缺陷，页面已有口径标注"） |
| 预警表 | `alert_engine.scan_once()` 收盘批次 15:54 挂载，`UNIQUE(rule_id, stock_id, trigger_date)` 每股每日一条 | **盘中触线不写 alert_history**（推荐）：预警语义=收盘口径巡检产物；盘中提醒走速览卡实时状态（内存/读取时计算），避免污染幂等语义与已读管理。备选：复用 error_logs 通道做"盘中事件"留痕（先例：021BN-c ④失败可见化），仅记不弹 |

**结论**：`_scan_stop_discipline` 参数化是低风险小改动（默认不变）；矩阵/预警零触碰；止损线计算本身零新代码（读 holdings 聚合 + `daily_reports.price_advice` JSON 的 `stop_loss`，解析先例 `_parse_pa_zone` / `trader_advisor._parse_price_advice`）。

### ⑥ 前端看板卡片结构与刷新机制

**文件：`static/js/portfolio.js`**

- `loadDashboard()`（L1309-1336）：**并行 3 请求** `/api/portfolio/summary` + `/api/portfolio/watchlist-scores` + `/api/dashboard/action-list`（全部 `cache:'no-store'`，021E ETag-304 教训）→ `Promise.all` → `_dashData` → `renderDashboard`；action-list 失败静默降级为 null（L1325-1330），看板不阻塞。
- 卡片序（renderDashboard L1338-1461）：标题栏（🔄刷新=`loadDashboard()` 整页重载）→ 1. 概览 dash-grid（总资产/持仓盈亏/持仓自选/平均评分）→ 1.2 行动清单卡 `renderActionListCard` → 1.5 操作建议卡 → 2. 筛选器 → 3. 批量评分表（含 🚀生成今日报告 / 📊盘中快报 按钮）→ 4. 图表区 → 5. 数据源健康度卡（**异步局部填充先例** loadSourceHealth）。
- 现有"看现价"路径：持仓页刷新按钮 `refreshPrices()`（L903-937）→ POST refresh-prices → 级联 `loadPortfolioGroups()`+`loadStocks()` 整页重载——这就是痛点②"操作分散"的实体。

**021BT 前端设计**：
- `loadDashboard` 增**第 4 路并行** `/api/dashboard/intraday`（no-store，失败降级 null 跳过渲染——actionList 同款）；
- 1.2 行动清单卡之后插入 **1.25 盘中速览卡** `renderIntradayCard(data.intraday)`：仅交易时段且有持仓时展示完整态；非时段显示一行"休市中 · 收盘口径见批量评分表"（不占版面）；
- 卡内 **一键刷新** 按钮 → `POST /api/dashboard/intraday/refresh`（后端带冷却）→ **只局部重渲染速览卡**（loadSourceHealth 局部填充先例），不动整页；前端按钮节流（冷却期内 disabled）；
- 每行固定缀"盘中口径，以收盘确认为准"（卡级脚注 + `below_stop` 行内缀），徽标配色沿用 A股惯例：跌破=绿系风控色（与 rating_downgrade/纪律行同族）、逼近=琥珀、正常=灰；
- `node --check static/js/portfolio.js` 验证（无 JS 测试设施先例，021BQ）。

### ⑦ 风控设计约束（常量与现状）

| 常量 | 值 | 位置 | 与巡检关系 |
|---|---|---|---|
| `_QQ_MIN_INTERVAL` | **0.25s** | modules/market_screener.py L53（`_pace()` 通道级，L61-68） | market_screener 自管管道；巡检不经过它，**自带节拍**（分钟级 >> 0.25s） |
| `_SINA_MIN_INTERVAL` | 0.35s | 同上 L52 | 巡检不用新浪 |
| `_EM_MIN_INTERVAL_SECONDS` | 0.5s | modules/collector/http_client.py L273（东财全局最小间隔） | **巡检零东财请求** |
| collector `_http_get` | 无节流 | http_client.py L130-139（直连+随机UA+raise_for_status） | 腾讯侧本就无 collector 级限频 → 巡检模块自持间隔常量 |
| `MAX_RETRIES` | 3 | config.py L32（`@retry` 服务新浪/腾讯） | 巡检**不用 @retry**（单轮失败直接静默降级，防重试风暴；与 020R-60 `@retry` 语义区分） |
| `PRICE_CACHE_TTL_HOURS` | 24 | config.py L125 | 速览卡新鲜度用 updated_at 展示，不改 TTL |
| `PRICE_CACHE` 降级原则 | 保旧值 | market.py L180-199"保留旧缓存，禁止价格归零" | 巡检失败同样不写 price_cache |

**频率上限测算（持仓 8 只，1 批量请求/轮）**：

| 间隔 | 交易时段请求数/日（A股 240min） | 平均速率 | 评价 |
|---|---|---|---|
| 1 min | ≤240 | ≤0.017 req/s | 无必要 |
| **3~5 min（推荐 5）** | **≤80/48** | **≤0.006~0.009 req/s** | 感知延迟 ≤5min，足够"盘中无感知→有感知"的目标；为 market_screener 全市场粗筛单轮（56 页新浪+逐票腾讯）请求量的零头 |
| 10 min | ≤24 | ≤0.0017 req/s | 保守可选（config 可调） |

对照：`_QQ_MIN_INTERVAL=0.25s` 约束相当于逐票 4 req/s 通道上限；巡检批量 1 请求/5min = 平均速率的 1/75，余量两个数量级。**结论：5 分钟默认间隔（`config.INTRADAY_PATROL_INTERVAL_MIN=5`）风控余量极大，且与"持仓 8 只极度克制"完全相容。**

**失败静默降级现状（沿例）**：`_refresh_kline_today_bar` 失败保持旧 bar（L100-102）；refresh-prices 失败保旧缓存不归零；mootdx 失败 None 不抛；021BN-c ④失败可见化=写 error_logs/健康卡。巡检沿用：**单轮失败 → 保留上次快照 + 速览卡状态行"快照时间 HH:MM（数据源暂不可达）" + logger.warning**；**连败 3 轮 → 自动暂停至下一时段边界**（防打爆退避，镜像 backfill FAIL_RATE_TO_BACKOFF 思想）；恢复后自动续跑；全程无 UI 弹窗。

---

## 三、实施项分解（每项 做 / 不做 / 怎么做）

### 项1：批量取价下沉复用（modules/realtime_quotes.py）——无依赖先行

- **做**：`blueprints/portfolio/market.py::_fetch_realtime_price_batch` **逐字平移**至新模块 `modules/realtime_quotes.py`（无 Flask 依赖）；market.py 改 `from modules.realtime_quotes import _fetch_realtime_price_batch`；facade `blueprints/portfolio/__init__.py` L49 导入行同步改源（再导出名字零变化，`tests/test_routes.py` L212 引用零破坏）；顺带补最小间隔常量 `_REALTIME_MIN_INTERVAL=0.25` + 模块级 `_last_ts` 节拍（批量场景几乎不等待，仅防手动连点叠加巡检）。
- **不做**：改函数签名/解析下标/降级链；动 refresh-prices 端点行为。
- **测试**：test_routes 既有 `_fetch_realtime_price_batch` 用例零改动通过 = 平移正确性证明。

### 项2：盘中巡检调度器 + 快照与触线判定（modules/intraday_patrol.py）——核心

- **做**：
  - `start_intraday_patrol()` / `stop_intraday_patrol()`（幂等 + atexit，镜像 backfill）；`_patrol_tick()` 全 try/except；`_schedule_next()` 固定 `INTRADAY_PATROL_INTERVAL_MIN` Timer 串联；启动时正处时段立即跑一轮。
  - 时段门控：`_is_intraday_session(market)` 复用（kline.py，零复制）；持仓市场集合=`SELECT DISTINCT market FROM holdings JOIN stocks ... WHERE quantity>0`；任一市场在盘中才发对应批量请求。
  - `run_patrol_round(force=False) -> dict`：取持仓聚合（021BQ held_map 同款 SQL，L98-107 先例）→ 止损线（成本×0.92 与 price_advice.stop_loss 取高，`_stop_level` 同口径内联小纯函数）→ `realtime_quotes` 批量取价（**仅持仓代码**，非全自选）→ 节假日守卫（快照时间戳≠今日 → 该股跳过）→ **写 price_cache**（成功股）→ 内存快照 `_LAST_SNAPSHOT`（含 updated_at/连败计数）→ 触线判定纯函数 `evaluate_intraday_states(rows, near_pct)` → 返回快照 dict。
  - 状态机：`below_stop`（price < 有效止损，红/绿风控色置顶）> `near_stop`（有效止损 ≤ price < ×(1+`INTRADAY_NEAR_STOP_PCT/100`)，琥珀）> `normal`；止损线缺失 → `unknown` 灰（显式"无止损参考"，不静默）。
  - 降级：单轮失败保旧快照 + 连败 ≥3 暂停至时段边界；`force=True`（手动）绕过暂停但仍受 60s 冷却；**零 alert_history 写入**；不获取 `_generate_lock`（只写 price_cache，与 15:54 批次无写竞争；INSERT OR REPLACE 幂等）。
  - config.py：`INTRADAY_PATROL_ENABLED=True`（总开关回退）/ `INTRADAY_PATROL_INTERVAL_MIN=5` / `INTRADAY_NEAR_STOP_PCT=3.0` / `INTRADAY_PATROL_PAUSE_AFTER_FAILS=3`。
- **不做**：写 raw_kline；mootdx 盘口；东财任何接口；@retry 装饰；新表/迁移；021AY 式补跑；写 alert_history。
- **红线**：R12（不开 DEBUG）；R17（零新依赖）；数据源风控（时段门控+批量+节拍+连败暂停四层）。

### 项3：速览端点（blueprints/dashboard.py 扩展）

- **做**：`GET /api/dashboard/intraday`——聚合 `_LAST_SNAPSHOT` + holdings/止损线（快照缺失时读取时现算兜底，零网络）→ `{success, session:{in_session, markets}, updated_at, stocks:[{stock_id,symbol,name,price,pct_change,as_of,stop_line,stop_source,distance_pct,state}], patrol:{enabled,last_round_at,consecutive_failures,paused}, disclaimer:'盘中口径，以收盘确认为准'}`；`POST /api/dashboard/intraday/refresh`——调 `run_patrol_round(force=True)`，60s 冷却（ market_overview `refresh_in_cooldown` 同模式）+ 巡检停用/非时段时返回明确 message；两个端点全 try/except（dashboard.py L30-31 先例）。
- **不做**：动既有 action-list 端点；建新蓝图（挂既有 dashboard bp）。

### 项4：止损纪律扫描参数化（modules/action_list.py）——可选增量

- **做**：`_scan_stop_discipline(cursor, held_map, price_map=None)`；`get_action_list()` **不传** price_map（收盘口径现行为零变化）；纪律行 reason 按 `d.get('price_source')` 分支（None=现文案逐字保持；'intraday'="现价 X（HH:MM 盘中口径，以收盘确认为准）"）。
- **不做**：改默认路径任何行为；改 `build_action_list` 既有参数语义（仅消费行内增量键）。
- **测试**：021BR 既有断言全数保持 + price_map 新用例 2~3 例。
- **决策点**：若 021BT 首版追求最小触碰面，本项可延后（速览卡已覆盖盘中触线呈现；行动清单保持纯收盘口径亦自洽）。**推荐首版即做**（改动 <20 行，让行动清单未来可无缝接盘中档）。

### 项5：前端盘中速览卡（static/js/portfolio.js）

- **做**：`loadDashboard` 第 4 路并行 `/api/dashboard/intraday`（no-store，失败 null 降级）；`renderIntradayCard(data)` 插入 1.2 行动清单卡之后；卡内一键刷新（POST refresh 端点 → 局部重渲染，60s 前端节流 disabled）；卡级固定脚注"盘中口径，以收盘确认为准"；非时段收缩为一行休市提示；状态徽标配色（below_stop 绿系风控 / near_stop 琥珀 / normal 灰 / unknown 浅灰）。
- **不做**：改 renderDashboard 既有卡片；自动轮询前端定时器（刷新由后端巡检驱动，页面重开/手动刷新即最新——避免多标签页轮询放大请求）；`node --check` 验证。

### 项6：app.py 注册 + 文档

- **做**：app.py main() `start_backfill_scheduler()` 后追加 `start_intraday_patrol()`（try/except 不阻断启动）；CHANGELOG 021BT 条目（含"收盘确认口径零改动"验证清单：fetch_kline 同日跳过/回填、15:54 批次、_scan_stop_discipline 默认路径、build_operations_matrix、alert scan_once 全部零 diff）。
- **不做**：watchdog/start.bat 改动（巡检随主进程，自愈机制天然覆盖）。

---

## 四、频率与风控设计专节

1. **数据源选择**：腾讯 `qt.gtimg.cn` 批量行情（与 refresh-prices/020R-60 同源，2026-08-17 实测 25/25 稳，免费无密钥）；A/H 混合 1 请求覆盖；**零东财、零新浪、零 mootdx**（东财熔断教训 021BN-c 直接归避）。
2. **巡检间隔**：默认 5 分钟（config 可调 1~30）；仅交易时段活动（`_is_intraday_session` 门控）；午休/周末自动停；非时段 tick 空转代价=一次 bool 判断。
3. **请求预算**：8 只持仓 1 批量请求/轮 → ≤48 请求/日（5min）或 ≤80（3min）；平均速率 ≤0.009 req/s，对 0.25s 通道约束余量 ~75 倍；手动一键刷新独立计数（60s 冷却），最坏叠加仍 ≤1 请求/分钟。
4. **静默降级链**：轮内异常 → 保旧快照 + warning 日志 → 连败 3 轮暂停至时段边界 → 下时段自动恢复；快照失败**不写 price_cache**（保旧值禁归零，与手动刷新同原则）；UI 只见"快照时间 + 数据源暂不可达"状态行，无弹窗无红错。
5. **节假日/停牌守卫**：快照时间戳 ≠ 今日 → 该股本轮跳过（不写不警）；停牌股价格不变、涨跌额 0，速览卡正常显示（状态自然为 normal）。
6. **写库面**：仅 `price_cache`（既有表、既有语义、幂等 REPLACE、WAL 安全、与 15:54 批次零锁竞争）；不写 raw_kline/alert_history/新表。
7. **收盘确认口径零改动清单（验收对照）**：`fetch_kline` 同日跳过与 `tencent_intraday` 回填 / `_refresh_kline_today_bar` / 15:54 三窗批次与 scan_once / `_scan_stop_discipline` 默认路径 / `build_operations_matrix` 全函数 / `_stop_level` / classify_stage / B24 `generate_advice` —— 全部零 diff（git diff 核验 + 既有测试全绿）。
8. **单实例**：021BN-c 守卫（health 探测 + 绑定失败退出）已保证唯一 app 实例；巡检 Timer 为 daemon 线程随主进程存亡；Timer 串联无重叠。

---

## 五、测试策略专节（非交易时段验收：合成/mock）

**既有模式完全够用，零缺口**（`tests/test_intraday_020r59.py` 提供全套模板）：

- **时钟**：`_FakeDT` 逐模块固定 `datetime.now`（巡检模块为独立命名空间，补丁只打 `modules.intraday_patrol`，比 020R-59 的三模块并打更简单）；
- **网络**：`monkeypatch.setattr(巡检模块, '_fetch_quotes_batch', lambda ...)` 返回合成批量响应文本（`'~'.join` 构造 parts，参照 test L80-90：`[1]名称 [3]现价 [4]昨收 [32]涨跌幅 [30]快照时间戳`）；未触网断言用 `AssertionError` 替身（test L120-123 模式）；
- **库**：`tmp_path + monkeypatch.setattr(db_manager,'DB_PATH',...)` 临时库造 stocks/holdings/daily_reports(price_advice JSON)/raw_kline。

**用例清单（新增 `tests/test_intraday_patrol.py`，估 16~20 例）**：

| 组 | 用例 |
|---|---|
| 时段门控 | 盘中→跑一轮；午休/盘后/周末→零请求（替身抛错证明）；`INTRADAY_PATROL_ENABLED=False` → 永不跑 |
| 快照解析 | 8 只合成响应（含港股 hk 前缀、缺字段行、停牌零成交）；快照时间戳=昨日（节假日/停牌）→ 跳过不写 |
| 触线判定 | 跌破/逼近带内/正常/止损线缺失四态；双源取高（成本线 vs pa_stop）；边界值=线价不触发 |
| 降级 | 取价异常→保旧快照+连败+1；连败 3→暂停；暂停后 force=True 仍可手动；恢复清零 |
| 写库 | 成功轮 price_cache 8 行更新且旧值不归零；失败轮 price_cache 零写入 |
| 端点冒烟 | test_routes.py +2：GET intraday 200 空快照形态（disclaimer 在）；POST refresh 冷却二次 429/success+cooldown 标注 |
| 参数化回归 | `_scan_stop_discipline` 不传 price_map → 021BR 既有断言逐条保持；传 price_map → 盘中行 + price_source 键 |
| 静态 | `node --check static/js/portfolio.js` |

**终验命令（全程不变）**：`python -m pytest tests/`（fast 全绿不触网）+ `python scripts/check_redlines.py`（28/28 基线）+ `ruff check .` + `mypy app.py config.py modules`（新模块全注解）。

**实盘验收（交易时段人工，可选）**：速览卡价与行情软件逐只对账；11:31/15:01 边界各观察一轮自动停；断网 10 分钟观察连败暂停与恢复；盘后核对 price_cache 与收盘 K 线收敛（差值=15:00 后波动，归零于次日回填）。

---

## 六、实施顺序与依赖

```
项1 批量取价下沉（无依赖，先行）
  └─ 项2 巡检调度器+判定（依赖 1）
       ├─ 项3 速览端点（依赖 2）
       │    └─ 项5 前端速览卡（依赖 3）
       └─ 项4 止损扫描参数化（独立可并行；推荐首版同批）
项6 app 注册+文档（收口）
```

## 七、红线风险表

| 红线/约束 | 涉及点 | 处置 |
|---|---|---|
| B24/R7/classify_stage | 全部 | 零触碰（git diff 核验；速览卡不消费评级、不重映射） |
| 收盘确认口径 | 项2/3/4/5 | raw_kline 零写入；矩阵/预警/批次零触碰；盘中提示 100% 带"以收盘确认为准" |
| 数据源风控（021BN-c） | 项2 | 零东财；腾讯批量+时段门控+节拍+连败暂停四层克制；单实例守卫既有 |
| R12/R17/R18 | 全部 | 不开 DEBUG；零新依赖；Timer daemon 串联（非 ThreadPoolExecutor） |
| R16 | 全部 | 止盈止损线只读展示，不触风控阈值五项 |
| 021W 多账户 | 项2/3 | held 聚合 SUM+GROUP BY 账户无关（021BQ 先例），无 JOIN 重复行 |
| 红线核验锚点 | 全部 | 改造对象均不在 check_redlines 锚区，28/28 基线不动 |

## 八、开放决策点（供队长/用户拍板）

1. **巡检间隔默认值**：5 分钟（推荐）vs 3 分钟（更灵敏）vs 10 分钟（更克制）——config 可调，首版取 5。
2. **项4 参数化是否首版做**：推荐做（<20 行）；保守选项=延后。
3. **盘中触线是否留痕**：推荐不写库（速览卡即焚）；备选 error_logs 记"盘中触线事件"供事后回看。
4. **速览卡是否含自选股（非持仓）**：推荐仅持仓 8 只（目标即"持仓盘中状态一屏"）；扩自选属后续批次，频率需重算。
