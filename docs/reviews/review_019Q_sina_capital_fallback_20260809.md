# 架构评审报告 — 019Q 资金面第三数据源（新浪主力口径）+ 延迟自动补采

**评审人**：架构师
**评审日期**：2026-08-09
**任务书版本**：v1 草案（`docs/tasks/dev_tasks_20260809_019Q_sina_capital_fallback.md`），评审期间 PM 增补 1.2 节"周末/非交易日判别实验"（v1.1，见发现 6，不影响任何 M 项）
**评审方式**：独立 Read 代码核验 + 本机 DB 实证 + **实时网络探针独立复测**（不采信 PM 结论）
**评审结论**：⚠️ **有条件通过**（M-1~M-13 修订后定稿 v2，监理批准后开发）

---

## 〇、评审范围与独立核验清单

| # | 文件 | 核验位置 | 结论 |
|---|---|---|---|
| 1 | `modules/data_collector.py` | L2206-2205 `fetch_capital_flow` 入口、L2238-2243 前置跳过 SQL、L2276-2464 EM 三层、L2473-2475 `em_all_failed`、L2487-2528 THS 顶替块、L2530-2644 估算源3/4/5、L2647-2651 返回语义 | ✅ 与任务书插入点描述一致 |
| 2 | 同上 | L1509-1634 `_em_batch_collect`、L1424-1436 嵌套 `_call_with_timeout`（019I 模式）、L1637-1800 `fetch_capital_flow_batch` 补采清单 SQL（L1771-1777）、L1864-1924 EM push2his | ✅ 已读 |
| 3 | 同上 | L2048-2122 **`_fetch_capital_flow_sina`（既有估算源）** | ⚠️ **关键发现**（见发现1） |
| 4 | `modules/daily_report.py` | L56-76 `_scheduler_tick`、L79-88 `_schedule_next`、L440-559 `generate_daily_report` 批量循环、L53 `_generate_lock`、L190 `_get_all_stocks` | ✅ 已读（D-3 裁定依据） |
| 5 | `templates/index.html` | L2499-2505 资金面表头动态文案（hasThsFallback/sourceNotes）、L2515 行内 `thsTag`、L2069-2075/L2575-2576 'fallback'→'⚠️顶替' 三元链 | ✅ 已读 |
| 6 | `database/db_manager.py` | L961-965 迁移列表（`capital_source TEXT DEFAULT NULL`） | ✅ **capital_source 列已存在，db_manager 零改动成立** |
| 7 | `modules/data_adapter.py` / `advisor.py` / `analysis_engine.py` / `alert_engine.py` / `scoring_engine.py` / `app.py` | grep `capital_source`/`ths_total` 全量检索 | ✅ **零引用**——零改动红线成立，随 SELECT * 透出 |
| 8 | `stock_analyst.db` 实证 | 08-09 当日 23 只 A 股全为 is_estimated=1、capital_source=NULL、ths_net_inflow=NULL；data_status 全 'estimated'（周末 EM/THS 双缺） | ⚠️ **关键发现**（见发现3） |
| 9 | 实时网络探针（独立复测，urllib 直连） | lscjfb（sh600519）https/http 均 200；ssi_ssfx_flzjtj（sz300750）https/http 均 200 | ⚠️ **关键发现**（见发现2/4） |

---

## 一、独立核验的核心发现

### 发现 1（高）：任务书新函数命名与既有估算源**同名冲突**

任务书 Task 1 命名 `_fetch_capital_flow_sina(symbol)`，而 `data_collector.py` L2048 **已存在同名函数** `_fetch_capital_flow_sina(symbol, market)`——这是 019E 估算兜底链路的"新浪财经资金面"（hq.sinajs.cn 实时行情估算，is_estimated=1）。按任务书命名直接开发会**静默覆盖估算链路函数**：轻则估算兜底新浪源行为错乱，重则报错炸链路。必须改名（见 M-1）。

### 发现 2（高）：**严格日期匹配是正确性红线**——"当日采集取最新行"表述危险

独立探针实证（2026-08-09 周日 11:xx）：lscjfb 最新行 `opendate="2026-08-07"`（周五），**当日无 08-09 行**——lscjfb 是历史逐日表，非交易日/当日未发布时"最新行"即上一交易日。任务书"当日采集取最新行"若照字面实现，周末或 16:10 当日行未发布时会把**上一交易日数据写入今日日期**，正是 019G 周末防护（THS 批量跳过）所要防的"日期错位污染"，且污染发生在参与评分的真实口径行上，比 THS 顶替更隐蔽。**必须改为：`opendate == target_date` 严格匹配才写，不匹配即返回 None 落回 THS 顶替**（见 M-2）。

### 发现 3（高）：缺口统计 SQL 若只判 `capital_source IS NULL` 会把估算行误计为"EM 成功"

DB 实证（08-09 当日 23 只 A 股）：估算兜底行 **capital_source=NULL、is_estimated=1**（估算 INSERT 不写 capital_source）。任务书 Task 5 触发条件"东财采集失败数 > 0（或低于满额）"若实现为 `COUNT(main NOT NULL AND capital_source IS NULL) < 满额`，估算行会被误判为 EM 真实行 → 延迟补采**永不触发**，方案三第二项整体失效。缺口统计必须同时要求 `(is_estimated = 0 OR is_estimated IS NULL)`（见 M-6）。

### 发现 4（中）：ssi_ssfx_flzjtj 实时接口**无日期字段**，否决用作当日采集

独立探针实证：实时接口返回 `{r0_in,r0_out,r1_in,r1_out,r2,r3,netamount,...}` **无 opendate/日期字段**——它是"当前交易日快照"。在周末（今日实证）它返回的是周五收盘快照，无法锚定日期；在 16:10 盘中/盘后也无日期可验。用它做当日采集 = 把无日期锚点的数据写入"今天"，与发现 2 同一事故面且无法通过匹配自检。**本批次仅用 lscjfb（带 opendate），实时接口不引入**（见 M-3）。

### 发现 5（低）：PM 数据结论复测通过

- lscjfb 两市场可用（探针复测 600519；PM 已验 300750），https/http 均可（0.2~0.6s）。
- 自洽性复验 600519 2026-08-07：r0+r1+r2+r3 = -58,430,965.89 -55,199,332.41 -1,319,103.74 + 0 = **-114,949,402.04 元 = netamount，分毫不差** ✅；主力口径（r0+r1）=-113,630,298.30 元 = **-11363.03 万元** ✅。
- 字段：`opendate/trade/changeratio/turnover/netamount/ratioamount/r0/r1/r2/r3/r0_net...r3_net`，单位元。**注意**：`ratioamount` 是总净占比（netamount/turnover），**不是**主力净流入占比——不写入 main_net_inflow_pct（见 M-4）。

### 发现 6（v1.1 更新核验，2026-08-09 PM 增补"周末/非交易日判别实验"）

PM 于评审期间增补实验（腾讯普通行情接口 `qt.gtimg.cn/q=sh600519` 周日正常返回 08-07 行情 vs 资金流接口同日返回 `v_pv_none_match`；新浪 lscjfb 周六即返回 08-07 资金流数据），并据此裁定"腾讯资金流接口为服务端下线，非交易日波动可排除，无需工作日复测"。经核验：
- 该实验与**我的独立探针结论完全一致**（我于周日 11:xx 实测 lscjfb 最新行=08-07），双向印证；
- **强化发现 2**：非交易日 lscjfb 最新行恒为上一交易日 → M-2 严格日期匹配红线不变（无论周六/周日/当日未发布）；
- 腾讯停服结论稳健，无复测需求，无新增风险项；
- **v1.1 未修订任务书 Task 1 命名（M-1）与日期匹配表述（M-2）**——二者仍为 v2 必改项。

---

## 二、逐决策点裁定

### D-1：新浪插入次序 —— **裁定：按 PM 建议（EM 之后、THS 之前）**

`fetch_capital_flow` L2473 `em_all_failed` 块内，新浪顶替插在 THS 顶替块（L2487）**之前**、估算兜底（L2530）之前：

```
EM 三层全失败 → ① 新浪 lscjfb 顶替（sina_main，主力口径 r0+r1，is_estimated=0）
              → ② THS 库内顶替（ths_total，全部资金口径，019K 现有）
              → ③ 估算兜底（is_estimated=1，仅展示）
```

**理由**：新浪 r0+r1 与 EM"主力=超大单+大单"为同一概念（仅分档阈值各家不同），口径逼近度高于 THS 全部资金口径（019K 实证同日符号可相反）；把主力口径源排在全部资金口径源之前，正符合防覆盖等级"EM > 新浪 > THS > 估算"。失败静默降级（各层 return None 落下一层），链路不断。

### D-2：口径差异实证 —— **裁定：采纳，并补充 sina 自洽性抽验**

- 开发/验收期做同日双源对比（EM 主力 vs 新浪 r0+r1），偏差知情登记，**不设硬阈值**（各家超大单阈值定义不同）。
- **补充 QA 断言**：sina 行自洽性抽验 `main == super_large + large`（主力=超大+大，与 EM 同定义）与 `r0+r1+r2+r3 == netamount`（新浪恒等式，防解析 bug 的廉价哨兵）。

### D-3：延迟补采实现 —— **裁定：甲+乙融合（调度器注册一次性 daemon Timer，任务体复用 `fetch_capital_flow_batch`）**

**裁定**（三个候选均不原样采纳，采"甲+乙"融合）：

1. **注册点**：`daily_report._scheduler_tick` 内 `generate_daily_report()` 返回后、`scan_once()` 前后均可（建议其后、`_schedule_next()` 前）调用新增模块级函数 `_schedule_capital_retry(a_symbols)`。**不注册在 `generate_daily_report` 内部**——该函数同时被 app.py 手动 API（L3211-3218/L3240-3243）与 force 重跑调用，内部注册会让手动触发产生 30 分钟延迟副作用的意外行为；任务书语义明确为"16:10 批次结束后"。
2. **触发条件**：`len(a_symbols) - COUNT(当日 raw_capital_flow 中 stock_id IN a_symbols AND capital_source IS NULL AND (is_estimated=0 OR IS NULL)) > 0`，且**工作日**（周一~周五，019G 同型判定）才注册。缺口的 SQL 必须带 is_estimated 条件（发现 3）。
3. **任务体** `_capital_retry_once(a_symbols)`：先 `_generate_lock.acquire(timeout=5)`，拿不到即放弃本轮（防与手动批次并发写库；手动批次本身含资金面采集，放弃无害）；拿到后调用 `fetch_capital_flow_batch(a_symbols)`（复用 019E 补采清单入口：仅采"无真实数据"的股票——sina_main 行被 NOT IN 排除 → 幂等；ths_total 行仍在清单 → **顺带获得 sina 升级 ths_total→sina_main 的机会**）；异常隔离仅记日志。
4. **一次性**：Timer 回调内**不**再注册下一次 → 天然满足"仍失败不再重试，等待次日批次"。
5. **不阻塞主线程**：`threading.Timer(1800, ...)`、daemon=True（与 `_schedule_next` 同型，L85-86）。
6. **与次日 16:10 无冲突**：30 分钟一次性任务与 24h 周期任务无交集；`_em_batch_collect` 软上限 600s 保证任务体 ~16:40 触发、~17:00 内结束。
7. 可选：`stop_scheduler` 中取消未触发的 retry Timer（daemon 线程进程退出即亡，此项为防御性收尾）。

**否决丙**（仅登记手动触发）：方案三监理裁定的价值正在"自动"，丙退化为 019N 前的现状。

### D-4：网络细节 —— **裁定：按 PM 实证裁定，另否决实时接口并强制日期匹配**

| 项 | 裁定 |
|---|---|
| 协议 | **https 优先、失败回退 http**（独立探针复测两者均 200；数据参与评分，https 防劫持）。回退仅 1 次，不做代理尝试（sina 直连即可） |
| 编码 | `resp.content.decode('gbk', errors='replace')` |
| UA | 必须带（探针均带 UA 成功；沿用 `_random_ua()` + Referer `https://finance.sina.com.cn`） |
| 直连 | 禁用系统代理（等价 requests `trust_env=False`；urllib 需 `urllib.request.build_opener(ProxyHandler({}))`） |
| 超时 | **模块级提取 `_call_with_timeout`**（复制 L1424-1436 019I 模式为模块级函数，新增 timeout 参数），单次请求超时 **15s**（探针实测 0.2-0.6s）；既有 THS 嵌套版不动（避免回归面扩大）。严禁裸网络调用 |
| 间隔 | 每只请求后 `time.sleep(random.uniform(0.5, 1.0))`；29 只串行上限 ~29s，仅在 EM 失败路径发生，可接受（单只预算 15s+1s < STOCK_TIMEOUT=90s） |
| 实时接口 | **否决**（发现 4：无日期字段，无法锚定） |
| 日期匹配 | **严格 `opendate == target_date` 才写**（发现 2）；`num=2` 当日采集 / `num=5` 回补窗口，按 opendate 精确筛选 |

### D-5：历史回补工具 —— **裁定：部分纳入（采集层 target_date 参数 + scripts 一次性脚本，不动 app.py/调度）**

1. **采集层必须支持 `target_date`**（`_fetch_capital_flow_sina_main(symbol, market, target_date=None)`，None=当日）：这是任务书 Task 1"回补场景按日期匹配"的必然要求，零额外成本，亦为 D-4 日期匹配的落点。
2. **本批次新增** `scripts/backfill_capital_sina_019q.py`（运维侧一次性脚本，先例 `scripts/b26_margin_backfill.py`）：用法 `python scripts/backfill_capital_sina_019q.py 2026-08-07 [--symbols 600519,300750]`。对每个 A 股、每个缺口日期按 **EM push2his（按日期取）→ 新浪 lscjfb（按日期匹配）** 阶梯写回（THS 无历史当日数据，不参与历史回补），写入规则与主链路一致（UPDATE + INSERT OR IGNORE、is_estimated=0、capital_source='sina_main'、严格日期匹配）。可解决 08-07 类缺口（EM 挂停日）的**真实回补**，不再依赖 EM 历史接口是否可达。
3. **不改 app.py / 不入调度**：保持零代码用户一键启动面不变，脚本仅开发者/运维使用。
4. **效果说明**：回补后该日行 is_estimated=0 + sina_main，参与后续 5 日均/连续性因子——期望行为（真实数据），与 019K D-3 已接受的"混用"同范畴。

### D-6：是否同写分单字段 —— **裁定：写四档，不写占比**

1. **写**：`main_net_inflow=(r0_net+r1_net)/1e4`、`super_large_net=r0_net/1e4`、`large_net=r1_net/1e4`、`medium_net=r2_net/1e4`、`small_net=r3_net/1e4`（元→万元，round 2 位）。
2. **不写 `main_net_inflow_pct`**：lscjfb 的 `ratioamount` 是总净占比（netamount/turnover），非主力净流入占比——写入将引入第二个口径错位字段（019K D-4 同型裁定）。留 NULL，前端显示"—"。
3. **自洽性**：行内 `main == super_large + large`（主力=超大+大，与 EM 同定义）✅；四档之和 == 新浪 netamount/1e4 ✅。D-6 写四档**不产生** 019K D-4 所禁的"第二个口径错位"——THS 无分单数据所以禁写，新浪四档与 main 同源同口径，写是自洽的。
4. **消费方影响核验**：legacy 因子2"超大单占比 20 分"（analysis_engine L825-850）与 advisor 因子文案（L1302-1317）在 THS 顶替行下为"缺失/0 分"，新浪行将改用新浪口径真实值——与 main_net_inflow 同源同类偏差，019K"真实数据参与+全链路标注"精神一致，无新风险面。接受。
5. **口径标注方式**：行内"新浪"标记 + 表头"新浪顶替（主力口径）" + data_status message 三通道（019K 同型），见 M-8。

---

## 三、新发现的风险项

| # | 风险 | 级别 | 说明与对策 |
|---|---|---|---|
| R-1 | 新函数命名与既有估算源同名冲突 | **高** | M-1 改名 `_fetch_capital_flow_sina_main`；既有 L2048 估算链路零改动 |
| R-2 | 日期错位污染（"取最新行"实现） | **高** | M-2 严格 `opendate==target_date`；探针实证 08-09（周日）最新行=08-07 |
| R-3 | 缺口统计误判估算行为"EM 成功" | **高** | M-6 SQL 必须带 `(is_estimated=0 OR IS NULL)`（DB 实证估算行 capital_source=NULL） |
| R-4 | 16:10 时点当日行可能未发布 | 中 | lscjfb 通常收盘后更新，未实证 16:10 必达。对策：不匹配→落回 THS；+30min 延迟补采再试（天然双保险）。验收须覆盖"无当日行"用例 |
| R-5 | sina 免费接口限流/改版/停服 | 中 | 与东财同类风险；多层降级对冲（sina→THS→估算），不构成单点依赖；失败静默降级不阻断。知情登记 |
| R-6 | 延迟补采与手动批次并发 | 中 | `_capital_retry_once` 短超时取 `_generate_lock`，拿不到放弃（手动批次已含资金面采集） |
| R-7 | sina 行口径进入 legacy 因子2/文案 | 低 | 与 main_net_inflow 同源偏差，019K 已裁定"真实+标注"；前端/表头/status 三通道标注 |
| R-8 | sina JSON 解析健壮性 | 中 | 接口偶发 `null`/非严格 JSON；解析必须 try/except + 非数组/空 → None；金额统一走 `_safe_float_wan`（019N 模式） |

---

## 四、对任务书的修订项清单（M-1~M-13，PM 据此修订 v2）

| # | 位置 | 修订内容 | 依据 |
|---|---|---|---|
| M-1 | 任务书任务 1 | **新函数命名 `_fetch_capital_flow_sina_main(symbol, market, target_date=None)`**；既有 `_fetch_capital_flow_sina`（L2048 估算源）零改动 | 发现 1 |
| M-2 | 任务书任务 1.1 | 删除"当日采集取最新行"表述，改为**严格日期匹配：`opendate == target_date` 才写，不匹配返回 None 落回 THS**（当日/回补统一规则） | 发现 2 |
| M-3 | 任务书 D-4/任务 1 | 协议 https 优先+http 回退；GBK 解码；UA 必须；禁用系统代理；**模块级 `_call_with_timeout`（15s）**；请求间隔 0.5~1s；**否决 ssi_ssfx_flzjtj 实时接口**（无日期字段） | 发现 4 |
| M-4 | 任务书 D-6/任务 1.2 | 写四档（super/large/medium/small = r0..r3/1e4）+ main=(r0+r1)/1e4；**不写 main_net_inflow_pct**（ratioamount 为总占比，口径错位）；行内自洽 main==super+large | D-6 裁定 |
| M-5 | 任务书任务 3 | 防覆盖四处扩展 `NOT IN ('ths_total','sina_main')`：前置跳过（L2240）、补采清单（L1774）、估算三处守卫（L2549/2587/2625）；EM 写入 NULL 不变；**sina 顶替 UPDATE 无条件（不带来源守卫）**——置于 THS 块前、saved_count==0 时执行，可覆盖 THS/估算行 | D-1 裁定 |
| M-6 | 任务书任务 5 | D-3 裁定：`_scheduler_tick` 内注册一次性 daemon Timer(1800)；任务体 = `_generate_lock` 短超时 + `fetch_capital_flow_batch` 复用；**缺口 SQL 带 `(is_estimated=0 OR IS NULL)`**；工作日才注册；不注册在 generate_daily_report 内 | 发现 3、D-3 裁定 |
| M-7 | 任务书三（范围） | **范围 = data_collector.py + daily_report.py（D-3 必改）+ index.html + 新增 scripts/backfill_capital_sina_019q.py**；db_manager.py 零改动确认（capital_source 列 019K 已建，DB 实证存在）；app.py/config.py/requirements.txt 零改动 | D-3/D-5、核验 6 |
| M-8 | 任务书任务 4 | 前端三处：L2501-2505 表头 `hasSinaMain` 判定追加"新浪顶替（主力口径）"；L2515 行内 `sinaTag`="新浪"；status 三元链 'fallback' 已映射零改动。data_status message `'新浪顶替(主力口径r0+r1；东财恢复后自动回补)'` 不以"东方财富"开头 → L2269 防覆盖跳过不误触发 ✅（登记无需改） | 任务书 4 + 核验 |
| M-9 | 任务书四（验收标准） | 增补 QA 用例：① lscjfb 无当日行（mock 返回昨日）→ 断言不写入、落回 THS/估算；② sina 行自洽 `main==super+large`、四档和==netamount（3 只抽验）；③ https 失败→http 回退；④ 延迟补采：mock EM 失败→+30min 二次触发、幂等（sina_main 行不重采）、ths_total 行被升级为 sina_main、非交易日不注册；⑤ 既有估算链路回归（pytest tests/ 全过 + `_fetch_capital_flow_sina` 未被改动）；⑥ sina 顶替→THS→估算全链 fallback 断言 | M-1~M-6 |
| M-10 | 任务书五（红线 6） | 超时红线表述明确：新浪网络调用必须走模块级 `_call_with_timeout`（禁裸调用，含 https 回退的第二次请求） | D-4 裁定 |
| M-11 | 任务书注释一致性 | `fetch_capital_flow` docstring（L2209-2214 "禁用所有估算源"旧表述）与 `fetch_capital_flow_batch` docstring（L1643）增补：主力净流入第二源=新浪 lscjfb 主力口径（sina_main），第三源=THS 全部资金口径（ths_total），估算仅展示 | D-1 裁定 |
| M-12 | 任务书八（备注） | 增补 R-1~R-8 风险清单（含 sina 免费接口无 SLA、16:10 时点发布不确定性、并发防护） | 三节 |
| M-13 | 任务书 D-5 | 裁定：采集层 target_date 参数（M-1 落点）+ scripts 一次性回补脚本；**不改 app.py/不入调度**；回补阶梯 EM→sina（THS 无历史数据不参与） | D-5 裁定 |

---

## 五、评审结论

### 结论：⚠️ **有条件通过**（需按 M-1~M-13 修订 v2 后交付开发，监理批准后启动）

**通过项**：监理方案三方向正确；PM 调研结论经独立复测成立（lscjfb 可用、自洽性分毫不差、https/http 双通、腾讯停服）；D-1（EM 后 THS 前）、D-2（对比登记不设硬阈值）、D-6（写四档不写占比）PM 建议经独立核验成立；019K/019E/019I 已建机制（capital_source 列、防覆盖 SQL、`_call_with_timeout` 模式、补采清单、'fallback' 状态与前端映射）全部可复用，db_manager 及 5 个评分/展示模块零改动成立（grep 实证）。

**必改项（阻塞开发）**：
1. **M-1 命名冲突**（最高优先）：按 v1 命名开发将静默覆盖既有估算源函数，链路错乱。
2. **M-2 严格日期匹配**（次高优先）：v1"当日采集取最新行"在周末/当日未发布时产生日期错位污染，且污染发生在参与评分的真实行上。
3. **M-3 否决实时接口 + M-4 不写占比**：实时接口无日期字段（探针实证），引入即引入同型污染源；ratioamount 非主力占比。
4. **M-6 延迟补采规格**（缺口 SQL 带 is_estimated 条件、注册点、锁保护）：否则方案三第二项失效或产生并发写库。

**监理决策点**：无新增。sina 顶替与 THS 同属 is_estimated=0 真实数据参与评分（019K 已裁定"真实+标注+可回补"），且口径逼近度优于 THS，不改变既有参与评分默认。sina 免费接口无 SLA 的风险（R-5）由多层降级对冲，监理知情即可。

> **架构师独立评审签名**：本评审由架构师于 2026-08-09 独立完成，未采信 PM 结论。核验文件：modules/data_collector.py（L1424-1436/L1509-1800/L1864-2122/L2206-2684）、modules/daily_report.py（L44-180/L321-559）、templates/index.html（L2499-2516/L2069-2076/L2575-2576）、database/db_manager.py（L961-965）、modules/data_adapter.py、modules/advisor.py、modules/analysis_engine.py（L800-850）、modules/alert_engine.py、modules/scoring_engine.py、app.py、config.py（L115-117）、requirements.txt；stock_analyst.db 实证（raw_capital_flow 列结构、08-09 当日 23 行估算行、data_status）；实时网络探针 4 项（lscjfb/ssi_ssfx_flzjtj × http/https）；docs/reviews/review_019K_ths_capital_fallback_20260805.md。
