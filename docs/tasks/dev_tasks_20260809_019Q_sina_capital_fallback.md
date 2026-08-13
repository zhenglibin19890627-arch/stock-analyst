# 开发任务书 019Q — 资金面第三数据源（新浪主力口径）+ 延迟自动补采

**签发日期**：2026-08-09
**签发人**：PM
**批次编号**：019Q
**优先级**：P2（数据可用性增强；东财挂停时当前仅有同花顺"全部资金口径"顶替，口径偏差大）
**关联批次**：019E（估算兜底+评分隔离）、019G/019H/019I（THS 链路）、019K（THS 顶替+capital_source 机制）、019N（回补）
**架构评审**：✅ 有条件通过（2026-08-09，`docs/reviews/review_019Q_sina_capital_fallback_20260809.md`，已按 M-1~M-13 修订为 v2）
**监理批准**：✅ 已批准（2026-08-09）
**当前状态**：✅ **已关闭**（2026-08-09，PM+QA 双签 + 监理批准关闭；观察项 1 裁定"保留代码、改说明书"，注释修订移交小任务书 019Q-S1）

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：开发者（本稿 v2 已通过架构评审）

**职责**：按本任务书 v2 + 评审报告裁定实施，改动范围严格限定第三节所列文件；完成后自验（第四节验收标准 1~3），交付 QA 独立验收。

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格，命令行需引号） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db` |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包；本方案仅用标准库 urllib） |

### 独立性原则
- PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 本任务书 v2 已经监理批准（2026-08-09），可按第三节任务范围开始编码

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 流程路径 | ✅PM 数据源实测调研（2026-08-09）→ ✅PM 签发 v1（2026-08-09）→ ✅架构师评审有条件通过+PM 修订 v2（2026-08-09）→ ✅监理批准（2026-08-09）→ ⏳开发执行+自验 → ⏳QA 独立验收 → ⏳PM+QA 双签 → ⏳监理批准关闭 |

---

## 一、背景与立项依据

### 1.1 立项来源（监理裁定，2026-08-09）

08-05 曾发生东财资金流接口全线 RemoteDisconnected（019K 背景），当时只能用同花顺"全部资金口径"顶替（非主力口径，同日符号可相反）。监理 2026-08-09 裁定方案三：**增加第三个数据源（新浪/腾讯，主力口径优先）+ 失败后延迟自动补采**，增强资金面采集健壮性。

### 1.2 PM 实测调研结论（2026-08-09，探针脚本实证，架构师独立复测通过）

**新浪：可用，且提供主力口径（超大单 r0 + 大单 r1 分单），引入。**

| 接口 | 用途 | 实测结果 |
|---|---|---|
| `vip.stock.finance.sina.com.cn/.../MoneyFlow.ssl_qsfx_lscjfb?page=1&num=N&sort=opendate&asc=0&daima=sh600519` | **历史逐日分单**：opendate / r0_net（超大单净额）/ r1_net（大单）/ r2_net（中单）/ r3_net（小单）/ netamount（总净额），单位元 | ✅ 沪深两市验证（sh600519 / sz300750），https/http 均 200（0.2~0.6s） |
| ~~`MoneyFlow.ssi_ssfx_flzjtj` 实时接口~~ | ~~当日实时分单~~ | ❌ **架构师否决（M-3/发现 4）**：返回无 opendate/日期字段，周末返回周五快照，无法锚定日期，引入即引入日期错位污染源。**本批次不引入** |

**数据自洽性实证**（600519，2026-08-07，架构师复验分毫不差）：
- r0_net = -58,430,965.89 元；r1_net = -55,199,332.41 元；r2_net = -1,319,103.74 元；r3_net = 0
- 四档之和 = -114,949,402.04 元 = 接口返回 netamount，**完全吻合**
- 主力净流入（r0+r1）= **-113,630,298.30 元 ≈ -11363.03 万元**——即东财口径对应的"主力净流入"
- ⚠️ `ratioamount` 是**总净占比**（netamount/turnover），**不是**主力净流入占比——不得写入 main_net_inflow_pct（M-4）

**腾讯：免费资金流接口已全线停服，放弃。**

| 探针 | 结果 |
|---|---|
| `qt.gtimg.cn/q=v_ff_sh600519` | `v_pv_none_match`（接口下线） |
| `qt.gtimg.cn/q=ff_sh600519` | 同上 |
| `proxy.finance.qq.com/ifzqgtimg/appstock/app/dayzjlx/query` | `Can't load controller` |

**已排除"周末/非交易日因素"（2026-08-09 周日判别实验，架构师独立探针双向印证）**：同一域名同一时刻，腾讯**普通行情接口** `qt.gtimg.cn/q=sh600519` 正常返回 08-07 完整行情（收 1309.22，时间戳 20260807161437），而资金流接口 `ff_`/`v_ff_` 返回 `v_pv_none_match`。若为周末限流，两者应同生同死；且新浪 `lscjfb` 在周六（08-08）即返回 08-07 完整资金流数据。故腾讯资金流接口为服务端下线，非交易日波动。

**⚠️ 架构师关键发现（v2 已吸收，开发必读）**：
1. **命名冲突（M-1）**：`data_collector.py` L2048 已存在 `_fetch_capital_flow_sina(symbol, market)`（019E 估算兜底链路的新浪财经估算源）。本批次新函数必须命名为 **`_fetch_capital_flow_sina_main`**，既有函数零改动。
2. **严格日期匹配（M-2）**：lscjfb 是历史逐日表，非交易日/当日未发布时"最新行"即上一交易日（探针实证：周日最新行=08-07）。**严禁"取最新行"实现**，必须 `opendate == target_date` 严格匹配才写，不匹配返回 None 落回 THS。
3. **缺口统计（M-6）**：估算兜底行 capital_source=NULL（DB 实证），缺口 SQL 只判 `capital_source IS NULL` 会把估算行误计为"EM 成功"→ 延迟补采永不触发。必须附加 `(is_estimated=0 OR is_estimated IS NULL)`。

### 1.3 引入后的资金数据链路（目标形态，架构师 D-1 裁定）

| 优先级 | 源 | 口径 | 写字段 | 参与评分 |
|---|---|---|---|---|
| 1 | 东方财富（现有三层） | 主力净流入 | main_net_inflow（capital_source=NULL） | ✅ |
| 2 | **新浪 lscjfb（本批次新增）** | **主力净流入（r0+r1 分单）** | main_net_inflow（capital_source='sina_main'） | ✅（标注） |
| 3 | 同花顺顶替（019K 现有） | 全部资金（非主力） | main_net_inflow（capital_source='ths_total'） | ✅（标注，口径偏差最大） |
| 4 | 估算兜底（019E 现有） | 公式估算 | is_estimated=1 | ❌ 仅展示 |

**价值**：新浪口径为主力口径（与东财同概念，仅分档阈值各家不同），比 THS 全资金口径偏差小得多；且 lscjfb 提供历史序列，具备**历史缺口回补能力**（如 08-07 类缺口的真实回补，不再依赖东财历史接口是否可达）。

---

## 二、执行角色

**开发**（单人，监理已批准，可启动）

---

## 三、任务范围（v2 定稿，架构评审已核验全部插入点）

> **改动范围（M-7 定稿，共 4 个文件）**：
> - `modules/data_collector.py`：新浪源函数 + 降级阶梯 + 防覆盖扩展 + 模块级 `_call_with_timeout` + docstring 更新
> - `modules/daily_report.py`：延迟补采注册点（D-3 裁定必改）
> - `templates/index.html`：'sina_main' 标注
> - `scripts/backfill_capital_sina_019q.py`：**新增**一次性历史回补脚本（D-5 裁定，先例 `scripts/b26_margin_backfill.py`）
>
> **零改动确认**：`database/db_manager.py`（capital_source 列 019K 已建，架构师 DB 实证存在）；`app.py` / `config.py` / `requirements.txt`；`advisor.py` / `analysis_engine.py` / `alert_engine.py` / `scoring_engine.py` / `data_adapter.py`（grep 实证 capital_source/ths_total 零引用，随 SELECT * 透出）。

### 任务 1：新浪资金流采集函数（M-1/M-2/M-3/M-4 定稿）

**文件**：`modules/data_collector.py`
**内容**：新增 **`_fetch_capital_flow_sina_main(symbol, market, target_date=None)`**（target_date=None 表示当日）：

1. **严格日期匹配（M-2，正确性红线）**：请求 lscjfb（当日采集 `num=2`，回补窗口 `num=5`），按 `opendate == target_date` 精确筛选；**不匹配一律返回 None 落回下一层**，严禁"取最新行"。
2. **网络规格（M-3/D-4 定稿）**：
   - 协议 **https 优先、失败回退 http**（仅回退 1 次，不做代理尝试）；数据参与评分，https 防劫持
   - `resp.content.decode('gbk', errors='replace')`；必须带 UA（沿用 `_random_ua()` + Referer `https://finance.sina.com.cn`）
   - **禁用系统代理**：urllib 需 `urllib.request.build_opener(urllib.request.ProxyHandler({}))`
   - **模块级 `_call_with_timeout`**：复制 L1424-1436（019I 嵌套版）模式为模块级函数，新增 timeout 参数，单次请求超时 **15s**；既有 THS 嵌套版不动（避免回归面扩大）。**严禁裸网络调用（含 https 回退的第二次请求，M-10）**
   - 每只请求后 `time.sleep(random.uniform(0.5, 1.0))`；29 只串行上限 ~29s，仅在 EM 失败路径发生，单只预算 15s+1s < STOCK_TIMEOUT=90s
   - **JSON 解析健壮性（R-8）**：try/except；接口偶发 null/非严格 JSON；非数组/空 → None；金额统一走 `_safe_float_wan`（019N 模式）
3. **写字段（M-4/D-6 定稿）**：写四档 + 主力，**不写占比**：
   - `main_net_inflow=(r0_net+r1_net)/1e4`、`super_large_net=r0_net/1e4`、`large_net=r1_net/1e4`、`medium_net=r2_net/1e4`、`small_net=r3_net/1e4`（元→万元，round 2 位）
   - **不写 `main_net_inflow_pct`**（ratioamount 为总净占比，口径错位，留 NULL 前端显示"—"）
   - 行内自洽：`main == super_large + large`（主力=超大+大，与 EM 同定义）；四档之和 == netamount/1e4
4. **写入模式完全复用 019K 规格**：`UPDATE` + `INSERT OR IGNORE`，**严禁 INSERT OR REPLACE**；仅写目标 trade_date 1 行；`is_estimated=0`、`capital_source='sina_main'`
5. `save_data_status(stock_id, 'capital', 'fallback', '新浪顶替(主力口径r0+r1；东财恢复后自动回补)')`——message 不以"东方财富"开头，不会误触发 L2269 防覆盖跳过（架构师已核验，无需改）
6. A 股 symbol → 新浪 daima 映射：6 开头→`sh` 前缀，0/3 开头→`sz` 前缀（港股不适用，与 019K D-6 一致）

### 任务 2：降级阶梯调整（D-1 定稿）

**文件**：`modules/data_collector.py`
`fetch_capital_flow()` L2473 `em_all_failed` 块内，新浪顶替插在 THS 顶替块（L2487）**之前**、估算兜底（L2530）之前：

```
EM 三层全失败 → ① 新浪 lscjfb 顶替（sina_main，主力口径 r0+r1，is_estimated=0）
              → ② THS 库内顶替（ths_total，全部资金口径，019K 现有）
              → ③ 估算兜底（is_estimated=1，仅展示）
```

各层失败静默降级（return None 落下一层），链路不断。

### 任务 3：防覆盖闭环扩展（M-5 定稿，019K 教训，必做）

**文件**：`modules/data_collector.py`
防覆盖 SQL 扩展为 `capital_source NOT IN ('ths_total','sina_main')`（或等价写法），落点（架构师已核验行号）：

| 落点 | 位置 | 说明 |
|---|---|---|
| 前置跳过 | L2240 | `capital_source != 'ths_total'` → NOT IN |
| 补采清单 | L1774 | 同上。**语义澄清（监理 2026-08-09 裁定，QA 观察项 1）**：sina_main 行与 ths_total 行均**仍进入**补采清单（只有 EM 真数据才算"已完成"）——这给东财 30 分钟内恢复后覆盖回补的机会，是"东财恢复后自动回补"目标的实现；新浪重采不降级已有数据（QA F9 实证） |
| 估算守卫 | L2549 / L2587 / L2625（三处） | 同上 |
| EM 写入 | NULL 不变 | EM 真实数据照旧无条件写入（覆盖一切顶替/估算） |
| 新浪顶替 UPDATE | **无条件（不带来源守卫）** | 置于 THS 块前、saved_count==0 时执行，可覆盖 THS/估算行 |

覆盖关系总表：

| 覆盖方向 | 允许 |
|---|---|
| EM 真实 → 覆盖新浪/THS 顶替/估算 | ✅ 必须 |
| 新浪顶替 → 覆盖 THS 顶替/估算 | ✅ 允许（口径更优） |
| THS 顶替 → 覆盖新浪顶替 | ❌ 禁止 |
| 估算 → 覆盖任何真实/顶替 | ❌ 禁止 |

### 任务 4：前端标注与状态映射（M-8 定稿）

**文件**：`templates/index.html`
1. 资金面表头动态文案：L2501-2505 追加 `hasSinaMain` 判定 → "新浪顶替（主力口径）"（与 019K 'ths_total' 标注并列逻辑）
2. 行内标注：L2515 新浪顶替行追加 `sinaTag`="新浪"标记
3. status 三元链：'fallback' 已映射"⚠️顶替"（L2069-2075/L2575-2576），**零改动**，无需新状态值

### 任务 5：延迟自动补采（D-3 定稿：甲+乙融合，调度器注册一次性 daemon Timer + 复用既有补采入口）

**文件**：`modules/daily_report.py`

1. **注册点**：`_scheduler_tick` 内 `generate_daily_report()` 返回后、`_schedule_next()` 前，调用新增模块级函数 `_schedule_capital_retry(a_symbols)`。**不注册在 `generate_daily_report` 内部**——该函数同时被 app.py 手动 API 与 force 重跑调用，内部注册会让手动触发产生 30 分钟延迟副作用。
2. **触发条件**：缺口数 > 0 且**工作日**（周一~周五，019G 同型判定）才注册。缺口 SQL（M-6，必须带 is_estimated 条件）：
   ```
   len(a_symbols) - COUNT(当日 raw_capital_flow WHERE stock_id IN a_symbols
     AND capital_source IS NULL AND (is_estimated=0 OR is_estimated IS NULL)) > 0
   ```
3. **任务体** `_capital_retry_once(a_symbols)`：先 `_generate_lock.acquire(timeout=5)`，拿不到即放弃本轮（防与手动批次并发写库，手动批次本身含资金面采集，放弃无害）；拿到后调用 `fetch_capital_flow_batch(a_symbols)`（复用 019E 补采清单入口：仅采"无真实数据"的股票）；异常隔离仅记日志。
4. **一次性**：`threading.Timer(1800, ...)`、daemon=True（与 `_schedule_next` L85-86 同型）；回调内**不**再注册下一次 → 天然满足"仍失败不再重试，等待次日批次"。
5. **与次日 16:10 无冲突**：30 分钟一次性任务与 24h 周期任务无交集；`_em_batch_collect` 软上限 600s 保证任务体 ~16:40 触发、~17:00 内结束。
6. 可选收尾：`stop_scheduler` 中取消未触发的 retry Timer（daemon 线程进程退出即亡，防御性）。
7. **否决丙**（仅登记手动触发）：方案三监理裁定的价值正在"自动"，丙退化为现状。

### 任务 6：历史回补脚本（D-5 定稿：部分纳入）

**文件**：新增 `scripts/backfill_capital_sina_019q.py`（运维侧一次性脚本，先例 `scripts/b26_margin_backfill.py`）
1. 用法：`python scripts/backfill_capital_sina_019q.py 2026-08-07 [--symbols 600519,300750]`
2. 对每个 A 股、每个缺口日期按 **EM push2his（按日期取）→ 新浪 lscjfb（按日期匹配）** 阶梯写回；THS 无历史当日数据，不参与历史回补
3. 写入规则与主链路一致：UPDATE + INSERT OR IGNORE、is_estimated=0、capital_source='sina_main'、严格日期匹配
4. **不改 app.py / 不入调度**：保持零代码用户一键启动面不变，脚本仅开发者/运维使用（可解决 08-07 类缺口的真实回补）
5. 回补后该日行参与后续 5 日均/连续性因子——期望行为（真实数据），与 019K D-3 已接受的"混用"同范畴

### 任务 7：docstring 一致性（M-11）

**文件**：`modules/data_collector.py`
`fetch_capital_flow` docstring（L2209-2214 "禁用所有估算源"旧表述）与 `fetch_capital_flow_batch` docstring（L1643）增补：主力净流入第二源=新浪 lscjfb 主力口径（sina_main），第三源=THS 全部资金口径（ths_total），估算仅展示。

### 明确不改范围
- 既有估算源 `_fetch_capital_flow_sina`（L2048-2122）— **零改动**（M-1，改名避开即可）
- `modules/advisor.py` / `analysis_engine.py` / `alert_engine.py` / `scoring_engine.py` / `data_adapter.py` — 零改动
- `app.py` / `config.py` / `requirements.txt` / `database/db_manager.py` — 零改动

---

## 四、验收标准（v2 定稿，含 M-9 增补 QA 用例）

1. **代码级**：新浪采集走模块级 `_call_with_timeout`（15s）；UPDATE+INSERT OR IGNORE；仅写目标日期 1 行；严格 `opendate==target_date`；防覆盖 SQL 按任务 3 表格全部落地
2. **编译**：`python -m py_compile modules/data_collector.py modules/daily_report.py scripts/backfill_capital_sina_019q.py` 无错误；index.html 语法检查
3. **功能（QA mock）**：mock EM 三层全失败 + 新浪可达 → 写入 main=(r0+r1)/1e4 四档、is_estimated=0、capital_source='sina_main'、status='fallback'；新浪也失败 → 落回 THS → 落回估算，链路不断
4. **防覆盖**：EM 恢复可覆盖新浪行且 capital_source 归位 NULL；THS/估算不得覆盖新浪行；新浪可覆盖 THS/估算行
5. **口径抽验**：交易日双源并存时，抽 3 只股票对比 EM 主力值与新浪（r0+r1）值，偏差登记入验收报告（不设硬性阈值——各家超大单阈值定义不同，结果供监理知情）
6. **延迟补采**：模拟 EM 失败场景，验证 +30 分钟二次触发、不阻塞、幂等（已有真实数据不重采）、ths_total 行被升级为 sina_main、非交易日不注册
7. **标注**：前端资金面表新浪顶替行有"新浪"标注（截图断言）
8. **零改动确认**：范围外文件哈希不变；`python -m pytest tests/` 全过；既有 `_fetch_capital_flow_sina`（估算源）未被改动
9. **M-9 增补 QA 用例**：
   - ① lscjfb 无当日行（mock 返回昨日）→ 断言不写入、落回 THS/估算（R-4 用例）
   - ② sina 行自洽 `main==super+large`、四档和==netamount（3 只抽验）
   - ③ https 失败 → http 回退
   - ④ 新浪顶替 → THS → 估算全链 fallback 断言
   - ⑤ 既有估算链路回归（pytest 全过 + L2048 函数未改动）

---

## 五、决策裁定记录（架构师 2026-08-09 评审，全部已定稿）

| # | 事项 | 裁定结果 |
|---|---|---|
| D-1 | 新浪插入次序 | ✅ 按 PM 建议：EM 之后、THS 之前（主力口径逼近度高于 THS 全资金口径，019K 实证同日符号可相反） |
| D-2 | 口径差异实证 | ✅ 采纳 PM 建议（不设硬阈值），补充 QA 自洽断言（main==super+large、四档和==netamount） |
| D-3 | 延迟补采实现 | ✅ **甲+乙融合**（三个候选均不原样采纳）：调度器注册一次性 daemon Timer(1800)，任务体复用 fetch_capital_flow_batch，缺口 SQL 带 is_estimated 条件，工作日才注册，否决丙 |
| D-4 | 网络细节 | ✅ 按 PM 实证裁定：https 优先+http 回退、GBK、UA、禁代理、模块级 _call_with_timeout 15s、间隔 0.5~1s；**否决实时接口**（无日期字段）；**强制严格日期匹配** |
| D-5 | 历史回补工具 | ✅ 部分纳入：采集层 target_date 参数 + scripts 一次性脚本；不改 app.py/不入调度；回补阶梯 EM→sina（THS 不参与） |
| D-6 | 是否同写分单字段 | ✅ 写四档（super/large/medium/small），**不写占比**（ratioamount 非主力占比）；行内自洽成立，不产生 019K D-4 所禁的口径错位 |

---

## 六、红线约束

1. **零代码约束**：不引入新 pip 依赖（仅标准库 urllib）；config.py 不碰；无新表无新列（capital_source 复用）
2. **评分纯净红线（019E 延续）**：is_estimated=1 始终不评分；新浪/THS 顶替 is_estimated=0 参与评分但全链路标注
3. **防覆盖红线（019K 延续+扩展）**：EM > 新浪 > THS > 估算，高级源恢复必须能覆盖低级源；低级源不得覆盖高级源
4. **超时红线（019I/019J 延续，M-10 明确）**：新浪网络调用必须走模块级 `_call_with_timeout`，**严禁裸调用（含 https 回退的第二次请求）**
5. **口径标注红线（019K 延续）**：'sina_main' 在 data_status + 前端表头/行内必须可见
6. **范围红线**：改动仅限第三节列出文件，其余一律不碰；既有估算源 `_fetch_capital_flow_sina` 零改动
7. **日期匹配红线（M-2，本批次新增）**：`opendate == target_date` 严格匹配才写，严禁"取最新行"实现

---

## 七、执行顺序

```
Step 1: ✅ PM 实测调研（2026-08-09，新浪可用/腾讯停服）
Step 2: ✅ PM 签发 v1（2026-08-09）
Step 3: ✅ 架构师独立评审有条件通过 + PM 修订 v2（2026-08-09，M-1~M-13 全部吸收）
Step 4: ✅ 监理批准（2026-08-09，v2 定稿）
Step 5: ✅ 开发执行 + 自验（2026-08-09，自验报告 docs/reports/dev_selftest_019Q_sina_capital_fallback_20260809.md，PM 抽查关键红线全部落地）
Step 6: ✅ QA 有条件通过（2026-08-09，75 项独立断言 + 343 回归全过）→ 监理裁定观察项 1"保留代码、改说明书"（2026-08-09）→ PM+QA 双签（2026-08-09）→ ✅ 监理批准关闭（2026-08-09）
```

---

## 八、PM 备注与风险清单

1. **立项来源**：2026-08-09 监理就"东财不通时的拟人化取数方案"裁定方案三（多备数据源+延迟重试，放弃截图 OCR 方案）。
2. **调研方法**：PM 于 2026-08-09 用临时探针脚本直接请求新浪/腾讯接口实测（600519 沪市 + 300750 深市），验证字段结构、数据自洽性（四档分单之和=总净额，分毫不差）与两市可用性；腾讯三个候选接口全部实测失效。探针脚本已删除。架构师独立复测全部通过（未采信 PM 结论）。
3. **与 019K 的关系**：本批次完全复用 019K 建立的 capital_source 标注机制与防覆盖模式，是 019K 的"口径升级"——把第二源从"全资金口径"升级为"主力口径"，THS 降为第三源。
4. **港股**：不在本批次范围（与 019K D-6 一致），港股资金面维持现状。

### 风险清单（架构师评审 R-1~R-8，M-12 增补）

| # | 风险 | 级别 | 对策（已写入任务） |
|---|---|---|---|
| R-1 | 新函数与既有估算源同名冲突 | **高** | M-1 改名 `_fetch_capital_flow_sina_main`，既有 L2048 零改动 |
| R-2 | 日期错位污染（"取最新行"实现） | **高** | M-2 严格 `opendate==target_date`；探针实证周日最新行=08-07 |
| R-3 | 缺口统计误判估算行为"EM 成功" | **高** | M-6 缺口 SQL 必须带 `(is_estimated=0 OR IS NULL)`（DB 实证估算行 capital_source=NULL） |
| R-4 | 16:10 时点当日行可能未发布 | 中 | 不匹配→落回 THS；+30min 延迟补采再试（天然双保险）；验收用例①覆盖 |
| R-5 | sina 免费接口限流/改版/停服（无 SLA） | 中 | 与东财同类风险；多层降级对冲（sina→THS→估算），不构成单点依赖；监理知情 |
| R-6 | 延迟补采与手动批次并发 | 中 | `_capital_retry_once` 短超时取 `_generate_lock`，拿不到放弃 |
| R-7 | sina 行口径进入 legacy 因子2/文案 | 低 | 与 main_net_inflow 同源偏差，019K 已裁定"真实+标注"；三通道标注 |
| R-8 | sina JSON 解析健壮性 | 中 | try/except + 非数组/空→None；金额走 `_safe_float_wan`（019N 模式） |

---

## 九、验收收尾记录（2026-08-09）

| 项 | 内容 |
|---|---|
| QA 验收 | ✅ 有条件通过（验收标准 1~9 全满足，75 项独立断言，343 回归全过，生产库零写入实证）；报告 `docs/reports/qa_accept_019Q_sina_capital_fallback_20260809.md` |
| 观察项 1（补采清单语义） | ✅ 监理裁定（2026-08-09）：**保留代码现状、改说明书**——代码行为（sina_main 行仍进补采清单）与"东财恢复后自动回补"目标一致且更安全；误导性文字（本任务书 L1774 行、data_collector.py L1787-1788 注释、daily_report.py L177-178 docstring）由小任务书 019Q-S1 修订 |
| 观察项 2（双源偏差） | ✅ 知情登记：各源分单阈值定义不同所致，架构师 D-2 已裁定不设硬阈值；000001 东财验收当日再次现场挂停，立项场景复现 |
| PM 双签 | ✅ PM 核对 QA 报告覆盖度与红线清单，确认与任务书 9 条验收标准一一对应，签署同意（2026-08-09） |
| QA 双签 | ✅ QA 验收报告末节独立签名（未采信开发自验结论） |
| 监理批准关闭 | ✅ 监理批准关闭（2026-08-09），本批次流程完结 |
| 遗留 | ① ~~019Q-S1 注释修订~~ ✅ 已完成并关闭（2026-08-09）；② ~~08-07 历史回补~~ ✅ 已执行（2026-08-09，PM 代跑，23/23 写回，查库核验 23 行 sina_main 无空值无估算）；③ 下个交易日 16:40 抽查延迟补采日志（真实计时闭环，已纳入 08-10 16:30 定时核验）；④ 新增知情项：回补过程中 688802/688981 原有东财真数据被新浪写回覆盖（脚本设计行为，L94 无条件 UPDATE 与主链路一致，QA G 系列已验收）——东财恢复后重跑 `python scripts/backfill_capital_sina_019q.py 2026-08-07 --symbols 688802,688981` 可由阶梯① push2his 自动归位为东财真数据，是否执行由监理裁定 |

---

## 十、关闭后 PM 复核（2026-08-09 晚，只读查库）

**触发**：PM 独立窗口接续，按工作习惯独立抽查关键证据。

| 项 | 结果 |
|---|---|
| 08-07 回补复核 | ✅ 与尾记录一致：23 行全部 sina_main、无空值、无估算（688802=982.98 万、688981=32756.02 万，均 is_estimated=0） |
| 补采清单口径 | ✅ 08-07 的 23 行 sina_main 按 L1799-1805 语义仍进补采清单（东财恢复可覆盖归位）；08-09 无 EM 真数据行 |
| 新发现：08-09 估算行 | 生产库存在 2026-08-09（周日非交易日）资金行 23 条，全部 is_estimated=1、capital_source=NULL、四档为 NULL（估算兜底特征）。写入时点 ~11:41-11:42（app.log 当时有批量资金采集+估算兜底日志；早于 QA 验收开始，QA 记录验收前 mtime=11:54 与之吻合；12:06 至 17:38 之间无任何采集日志，排除该时段写入） |
| 影响评估 | 无需处置：① 估算行被补采清单 SQL 的 `(is_estimated=0 OR IS NULL)` 条件排除，不影响延迟补采触发；② 评分侧已由 019E 做估算隔离；③ 08-10 批次以当日为目标日期，这些行不参与。**供监理知情**：08-09 日报测试批次在生产库留下了非交易日估算行，属"非交易日补跑无真实数据"已知范畴 |
| 其他 | 应用进程运行中（17:38-17:42 有前端访问日志；17:38:29 的 DB 写入为 UI advise 触发，非资金表）；**08-10 16:10 定时日报依赖 app.py 进程保持运行** |
