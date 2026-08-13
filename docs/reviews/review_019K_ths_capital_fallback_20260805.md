# 架构评审报告 — 019K 东财失败时 THS 真实资金数据顶替主力净流入（方案一）

**评审人**：架构师
**评审日期**：2026-08-05
**任务书版本**：v1 草案（`docs/tasks/dev_tasks_20260805_019K_ths_capital_fallback.md`）
**评审方式**：独立 Read 代码核验 + akshare 1.18.53 源码核验 + 本机 DB 实证 + 实时网络探针（不采信 PM 结论）
**评审结论**：⚠️ **有条件通过**（M-1~M-12 修订后定稿 v2）

---

## 〇、评审范围与独立核验清单

| # | 文件 | 核验位置 | 结论 |
|---|---|---|---|
| 1 | `modules/data_collector.py` | L1095-1099 常量、L1121-1192 `_fetch_capital_flow_ths_batch`、L1195-1235 `_try_ths_primary`/`_try_ths_rank_backup`、L1238-1363 `_em_batch_collect`、L1366-1526 `fetch_capital_flow_batch`、L1897-2300 `fetch_capital_flow` | ✅ 与任务书行号基本一致 |
| 2 | `modules/data_adapter.py` | L273-290 `_read_capital_data`（019E-R1 过滤）、L370-407 资金面映射 | ✅ 过滤已落地 |
| 3 | `modules/advisor.py` | L1111-1159 `_build_capital_factors` | ✅ 过滤已落地 |
| 4 | `modules/analysis_engine.py` | L123-140 `_read_capital_data`、L766-930 `score_capital_flow` | ✅ 过滤已落地 |
| 5 | `modules/alert_engine.py` | L183-234 `check_capital_outflow` | ✅ 019H 过滤已落地 |
| 6 | `modules/scoring_engine.py` | L198-212 CAPITAL_SUBITEMS、L766-788 `score_main_capital` | ✅ 读 StockData 内存对象，不查 DB |
| 7 | `database/db_manager.py` | L238-254 建表、L950-964 `_safe_add_columns` 迁移列表、L378-387 data_status | ✅ 迁移机制完备 |
| 8 | `app.py` | L757-796 `/api/stocks/<id>/capital`（SELECT *）、L1285-1301 batch-analyze 预取 | ✅ 展示接口返回全字段 |
| 9 | `templates/index.html` | L2480-2494 资金面表格（ths 列+口径脚注+估算标注）、L2550/L2073 status 三元链 | ✅ 已读 |
| 10 | `modules/daily_report.py` | L440-480（批量预取在逐只循环**之前**） | ✅ 时序确认 |
| 11 | akshare 1.18.53 源码 | `stock_fund_flow_individual`（THS）/ `stock_individual_fund_flow_rank`（EM） | ⚠️ **关键发现**（见 D-1） |
| 12 | `stock_analyst.db` 实证 | 08-05 资金面行、data_status、ths/main 对照、近 5 日覆盖 | ⚠️ **关键发现**（见 D-1/D-3/D-5） |
| 13 | 实时网络探针 | `ak.stock_individual_fund_flow_rank(indicator='今日')` | ⚠️ RemoteDisconnected（同 EM 故障） |

---

## 一、独立核验的核心发现（评审最重要的三件事）

### 发现 1（D-1 决定性）：任务书所称"THS 备选接口"实为**东方财富接口**，非同花顺

**akshare 1.18.53 源码核验**（`akshare/stock/stock_fund_em.py` `stock_individual_fund_flow_rank`）：

```python
url = "https://push2.eastmoney.com/api/qt/clist/get"   # ← 东方财富 push2 域名
params = {"fid": "f62", ...}                            # f62 = EM 主力净流入字段
```

- 该函数 docstring 明确标注"东方财富网-数据中心-资金流向-排名"。
- 与 THS 主接口 `stock_fund_flow_individual`（`akshare/stock_feature/stock_fund_flow.py`，Host=`data.10jqka.com.cn`）**完全不同的服务器**。
- **今日实时探针实证**：`stock_individual_fund_flow_rank(indicator='今日')` 返回 `RemoteDisconnected` —— EM 三层全失败时，该"备选接口"与 EM 逐只接口**同步不可用**（同一基础设施，同一故障）。
- 019I 评审已登记此事实（review_019I L47/R-5："备选接口实际来自 akshare.stock.stock_fund_em，**东方财富接口，非 THS 服务器**"），019K 任务书重复了该认知错误。

**推论**：任务书 D-1 选项乙（"EM 失败时实时调 THS 备选接口取主力口径"）**作为冗余备份不存在**——乙所谓"口径匹配"是因为它本身就是东财数据；而它在需要顶替的时刻（EM 全挂）必然失败。**D-1 的"核心矛盾"实质与任务书描述不同**：不是"两个 THS 接口口径二选一"，而是"唯一真正独立的源 = THS 主接口（全部资金口径），无口径完全匹配的独立备选"。

### 发现 2（D-1 实证）：THS 全部资金口径与 EM 主力口径同日可能**符号相反**

DB 实证（08-05 同日双口径均存在的仅 2 只）：

| 股票 | EM 主力净流入(main) | THS 全部资金净流入(ths) | 符号 |
|---|---|---|---|
| 600276 | **+15335.59** 万 | **-11800** 万 | **相反** |
| 300146 | **+1263.45** 万 | **-1203.27** 万 | **相反** |

样本 n=2，但方向性警示成立：全部资金净流入与主力净流入是不同概念，散户主导日二者符号相悖属常态。**这是 019K 方案一的主要副作用来源**，必须在标注与监理知情下接受（见 D-1 裁定与 R-5）。

### 发现 3（D-4/D-5 决定性）：THS 顶替行若写 `is_estimated=0`，将**阻塞 EM 恢复回补**

核验 `fetch_capital_flow` L1925-1929 前置跳过 SQL：

```sql
SELECT COUNT(*) FROM raw_capital_flow WHERE stock_id=? AND trade_date=?
AND main_net_inflow IS NOT NULL AND (is_estimated = 0 OR is_estimated IS NULL)
```

THS 顶替行满足此条件（main NOT NULL + is_estimated=0）→ EM 恢复后重采时**被同日跳过**，THS 顶替值永久滞留评分链路。019E M-7 只解决了"估算（is_estimated=1）不阻塞 EM"，**未覆盖 THS 顶替路径**。L1498-1505 补采清单 SQL 同样问题（THS 顶替行会被判为"已有真实数据"而排除出补采清单）。**必须新增来源标记并双向排除**（见 D-5）。

---

## 二、逐决策点裁定

### D-1：顶替数据源与口径（核心）—— **裁定：甲（修改）**，否决乙；否决任务书 1.4 核心矛盾的表述

**裁定**：
1. **顶替源 = 甲：复用库内已入库的 `ths_net_inflow`（THS 主接口全部资金口径），零额外网络请求**。
2. **否决乙**：`stock_individual_fund_flow_rank` 是东方财富接口（发现 1），EM 全挂时必然同样失败（今日实时探针实证），作为"THS 备选"不存在。任务书 D-1 选项乙的前提事实错误。
3. **口径偏差接受并全链路标注**：THS 全部资金净流入 vs 主力净流入是不同概念（发现 2 实证符号可相反），写入 `main_net_inflow` 后必须：(a) data_status message 标注"同花顺顶替(全部资金口径)"；(b) 前端资金面表格行内标注；(c) 提供"口径纯净开关"（见 D-2 附注），供监理在知情后一键切换为"仅展示不评分"。

**理由**：
- 甲是**唯一**满足冗余目标（与 EM 独立）的源：THS 服务器（10jqka）与 EM（eastmoney）不同域，今日实证 THS 批量 5199 只成功而 EM 三层全挂（DB 实证 ths_cnt=20/main_cnt=5）。
- 甲零网络开销：daily_report（L473-480）与 batch-analyze（L1285-1301）两条入口均**先执行 `fetch_capital_flow_batch` 再逐只采集**，顶替时 ths_net_inflow 已在库内（时序核验确认）。
- 超时红线自然满足：顶替链路只读 DB，无新增网络调用（019I/019J 红线无触碰面）；任务书红线 7 通过"不新增调用"满足，若开发坚持实时调用 THS 必须复用 `_call_with_timeout`（data_collector L1153-1165）。
- 口径偏差虽真实存在，但监理方案一目标明确"真实数据参与评分"；且 THS 净额是真实市场资金流（信息含量远高于估算公式"成交额×涨跌幅"——019E 文档已确认估算与真实资金流无相关性），接受偏差 + 标注 + 可回补，优于"缺失数据中性填充"（v5 固定 85 分无区分度）。

**附注（对零代码用户的实际意义评估）**：资金面维度 A 股权重 0.40（v5 最高权重），其中主力资金子项占 0.55。EM 全挂时现状 = 该子项全部股票固定中性分（无区分度）或 legacy 维度缺失；本方案 = 真实方向/量级信号参与（有偏差、有标注）。对普通投资者，偏差来自"全部资金 vs 主力"的语义差异而非伪造数据，方向性参考价值明确存在；代价是 5 日均/连续性混用（见 D-3 实证：今日 21/23 只无历史可混用，实际影响极小）。

### D-2：is_estimated 标记语义 —— **裁定：is_estimated=0（参与评分）+ 新增 `capital_source` 来源列（第三维标记，非第三档 is_estimated 值）**

**裁定**：
1. THS 顶替行写 `is_estimated=0`（真实数据，参与评分）——019E"评分仅用真实数据"语义不变，THS 是真实数据不违反该红线；估算（is_estimated=1）依然永不过滤关卡。
2. **新增列 `raw_capital_flow.capital_source TEXT DEFAULT NULL`**（NULL=东财真实；`'ths_total'`=同花顺全部资金口径顶替）。迁移走 `_safe_add_columns`（db_manager.py L964 之后追加），启动自动幂等迁移，零代码约束满足（018/019E 先例）。
3. **否决"第三档 is_estimated 值"方案**：`is_estimated=0 OR IS NULL` 过滤（data_adapter L282/advisor L1126/analysis_engine L132/alert_engine L205 四处）会将第三档值静默排除，所有读路径语义不可控，危险。
4. **附注（监理可选开关）**：若监理在知情口径偏差后裁定"评分纯净优先"，四处评分/预警读路径 WHERE 各追加一行 `AND (capital_source IS NULL OR capital_source != 'ths_total')` 即切换为仅展示。设计保证开关成本=4 处单行修改，本批次默认**参与评分**（方案一原意）。

**理由**：可追溯性要求（任务书 D-2 第三问）由 `capital_source` 列 + data_status message 双通道满足；is_estimated 布尔语义保持 019E 纯净不变形。

### D-3：历史连续性 —— **裁定：无条件顶替（当日），混用接受 + 数据实证**

**裁定**：不设"仅当无 EM 历史才顶替"的条件，顶替无条件发生在 EM 三层全失败后。5 日均/连续性混用（THS 当日 + EM 历史前 4 日）接受。

**理由（DB 实证）**：
- 08-05 当日 23 只中仅 600276/300146 两只存在近 5 日 EM 历史，且**这两只当日 EM 均成功**（5 只真实行之一），不会触发顶替 → **今日实际场景 21/23 只无历史可混用**，"混用"在现实中是罕见事件。
- 对真正发生混用的场景（EM 某日挂停、前后日正常）：legacy 趋势因子（5 日均，L787-817）与连续性因子（L853-896）引入 1 日口径噪声；对照不顶替时该日数据缺失（5 日均仅 4 日样本、连续性截断），两者同为近似，顶替的信息增量（当日真实方向）为正。
- 拒绝"条件顶替"的工程理由：需在因子计算层按日期区分来源，侵入 data_adapter/analysis_engine/advisor 三处，违反范围红线且收益极小。
- 标注要求：顶替当日行在资金面表格与 data_status 可见（D-1/D-5 裁定），用户可自行识别。

### D-4：写入时机与位置 —— **裁定：插入点 ①（`em_all_failed` 处、估算兜底前）；否决 ②**

**裁定**：在 `fetch_capital_flow` L2123 `em_all_failed` 块内、估算源（腾讯/新浪/网易）**之前**插入 THS 顶替逻辑：
1. 从库读当日行 `ths_net_inflow`（SELECT，零网络）；
2. 有值 → UPDATE（已有占位行）或 INSERT OR IGNORE（无行）写 `main_net_inflow=ths值, is_estimated=0, capital_source='ths_total'`，仅当日 1 行，**不得写** `main_net_inflow_pct/super_large_net/large_net`（THS 主接口无主力分单数据，写入将引入第二个口径错位字段）；
3. `save_data_status(stock_id,'capital','fallback', '同花顺顶替(全部资金口径，非主力；东财恢复后自动回补)')`，返回 `('fallback', msg)`；
4. 顶替失败（ths 为 NULL）→ 落回现有估算兜底，链路不变。

**否决 ② 的理由（致命缺陷）**：THS 批量（16:15）**先于** EM 逐只（20:06+）执行（daily_report L473-480 时序核验）。若批量时同步写 main_net_inflow，则 THS 值抢先落库 → 后续 EM 逐只采集触发 L1925 前置跳过（main NOT NULL + is_estimated=0）→ **EM 真实数据当日永远无法写入**，直接违反 018"主力净流入唯一来源为东方财富"基线（L1917-1919 注释）。② 还破坏 019E 补采语义（补采清单会判"已有数据"）。插入点 ① 与估算兜底同级、仅在 EM 全败路径触发，对正常路径零干扰。

**闭环验证（019E 补采链路不受破坏）**：① 位于逐只 `fetch_capital_flow` 内；019E 补采（batch 内、逐只之前）仍先行尝试 EM，EM 恢复时补采成功即覆盖，THS 顶替不会抢先。两条路径互不干扰。

### D-5：防覆盖机制 —— **裁定：确认三向覆盖关系 + 补充 4 处必需防护（019E M-7 扩展至 THS 路径）**

**覆盖关系表（裁定确认）**：

| 覆盖方向 | 裁定 | 机制核验 |
|---|---|---|
| EM 真实 → 覆盖 THS 顶替 | ✅ **必须允许** | L1925 前置跳过 SQL 追加 `AND (capital_source IS NULL OR capital_source != 'ths_total')`（必需，否则被跳过）；L1498 补采清单 SQL 同步追加（必需）；EM 三层 INSERT OR REPLACE 显式携带 `capital_source=NULL`（019E M-7 同型防御） |
| 估算 → 覆盖 THS 顶替 | ❌ **禁止** | 估算 UPDATE（L2146/L2182/L2218）追加来源守卫 `AND (capital_source IS NULL OR capital_source != 'ths_total')`（流程上估算路径在顶替成功（saved_count>0）后不可达，守卫为防御性） |
| THS 顶替 → 覆盖 EM 真实 | ❌ **禁止** | 顶替仅在 `em_all_failed`（saved_count==0）触发 + 函数入口前置跳过（已排除 EM 真实行）→ 闭环 |
| THS 顶替 → 覆盖估算 | ✅ 允许 | 真实覆盖估算，019E M-7 精神一致；顶替写 UPDATE/INSERT OR IGNORE 不破坏 ths 字段 |

**data_status message 格式裁定**：`'同花顺顶替(全部资金口径，非主力；东财恢复后自动回补)'` —— 前缀"同花顺顶替"≠"东方财富"，L1953-1960 message 防覆盖检查不拦截 EM 重采（正确方向）；019E M-7 扩展为"EM 写入显式 is_estimated=0 **且 capital_source=NULL**"。

**状态值裁定**：新增 `data_status.status='fallback'`（TEXT 无约束，兼容）；不得复用 'estimated'（语义误导）。消费方适配：`_em_batch_collect` L1327 `result[0]=='success'` 判定 —— 'fallback' ≠ 'success' → 计失败、EM 熔断计数不重置（**正确**：EM 仍不可用）；前端三元链 L2550/L2073 增加 'fallback' → '⚠️顶替'。

### D-6：港股范围 —— **裁定：不在本批次，维持估算兜底**

THS 批量源仅 A 股（`fetch_capital_flow_batch` 入参 `a_stock_symbols`，daily_report L474 过滤 market=='a_stock'）；港股维持腾讯 K 线估算兜底（L2131-2166）。港股真实资金第二源的寻找（腾讯 HK 资金接口）超出零代码/范围红线，登记为后续候选，不在本批次。

### D-7：今日已跑批次回填 —— **裁定：不追溯（与 019E E-4 先例一致）**

- 今日 16:14/20:11 批次报告已发布，重写破坏报告一致性；下一批次（T+1）自然使用新链路，无需干预。
- **既有能力即可人工回填**：`generate_daily_report(target_date, force=True)`（daily_report L440）与 `/api/daily-report/generate` 的 force 参数已存在，用户可手动重跑指定日期批次——**无需新增脚本/接口**。
- 监理如需当日回填，可另行指示调用既有 force 能力。

### D-8：范围与红线确认 —— **裁定：需修订（任务书红线 2 不可实现化表述 + 补充 3 条红线 + 范围扩展 2 文件）**

1. **红线 2（口径红线）修订（必需）**：原表述"顶替写入 main_net_inflow 的数据口径必须与'主力净流入'语义匹配"——唯一满足该条件的源是东财自家接口（无冗余价值，D-1 发现 1），红线与方案一目标冲突。修订为："**顶替数据必须全链路标注口径差异（data_status + 前端 + 因子文案），口径偏差由监理知情接受；监理可选择'口径纯净开关'切换为仅展示**"。
2. **补充红线（必需）**：
   - 来源标注红线：THS 顶替行在资金面表格（index.html L2491 行内 `<sup>同花顺</sup>` + L2482 表头动态文案"来源：东方财富/同花顺顶替（全部资金口径）"）与 data_status 必须可见；
   - EM 回补红线：pre-check（L1925）与补采清单（L1498）必须排除 `capital_source='ths_total'` 行（发现 3）；
   - 状态消费红线：新增 'fallback' 状态必须同步适配前端两处三元链（L2550/L2073）与 `_em_batch_collect` L1327 判定（不重置 EM 熔断计数）。
3. **范围红线扩展（必需）**：`modules/data_collector.py`（必改）+ `database/db_manager.py`（迁移列表 L964 后追加 capital_source）+ `templates/index.html`（标注与状态映射）。其余范围不变：advisor/analysis_engine/alert_engine/scoring_engine/data_adapter/app.py/daily_report 均**零改动**（评分过滤按 D-2 附注保持参与，capital_source 随 SELECT * 自动透出）。
4. **注释一致性修订（必需）**：data_collector L1368-1371 docstring"主力净流入唯一来源为东方财富"与 L1916-1920 注释，修订为"主力净流入主来源为东方财富；EM 三层全失败时以同花顺全部资金口径顶替（标注，is_estimated=0）作为评分真实数据第二源；估算兜底仅展示"。
5. **超时红线**：顶替链路读库零网络调用，自然满足；若开发擅自新增实时 THS 调用，必须复用 `_call_with_timeout`（019I 模式），严禁裸调用。
6. **R-2（EM 逐只挂死）维持登记**：019J A-4 已裁定 019K 候选；本批次为 THS 资金顶替主题，与 R-2（`_em_batch_collect` 单只挂死超时包装）不同缺陷面，按"一次变更一个缺陷面"先例**不纳入**。

---

## 三、新发现的风险项

| # | 风险 | 级别 | 说明与对策 |
|---|---|---|---|
| R-1 | 任务书"THS 备选接口"认知错误 | **高** | `stock_individual_fund_flow_rank` 实为东财（发现 1）。按乙开发必然失败（今日探针实证 RemoteDisconnected）。对策：M-1 修订任务书 |
| R-2 | THS 顶替行阻塞 EM 回补 | **高** | pre-check 与补采清单对 is_estimated=0 行放行（发现 3）。对策：M-6 双向排除 + capital_source 列 |
| R-3 | 估算 UPDATE 覆盖 THS 顶替行 | 中 | L2146/L2182/L2218 无来源守卫。对策：M-6 守卫（防御性） |
| R-4 | 前端误导 | 中 | 'fallback' 状态未映射显示 ❌失败；THS 口径值在"主力净流入"列无标注。对策：M-7 |
| R-5 | 口径偏差进入评分（方案一固有代价） | 中 | 实证 2/2 同日符号相反；v5 main_capital 阈值（±1000/±5000 万）与 legacy 连续性因子受偏差影响。接受（监理方案一 + 标注），提供"口径纯净开关"（D-2 附注）；QA 验收须断言标注存在 |
| R-6 | ths_net_inflow 字段源歧义 | 低 | 主接口（全部口径）与备选接口（主力口径，仅主接口失败时写入）落同一字段，顶替时无法区分。标签统一为"同花顺净额口径"表述，可接受 |
| R-7 | EM INSERT OR REPLACE 清空 ths_net_inflow | 低 | 既有 018 行为（EM 覆盖后辅助字段 NULL）。不影响评分与展示主值，当日 THS 批量重跑可重建；接受登记 |
| R-8 | 顶替行参与资本流出预警 | 低 | alert_engine L201-209 读 main_net_inflow（019H 仅隔离估算），THS 顶替行参与连续 3 日判定，属混口径。真实数据参与 + 前端标注，接受 |

---

## 四、对任务书的修订项清单（M-1~M-12，PM 据此修订 v2）

| # | 位置 | 修订内容 | 依据 |
|---|---|---|---|
| M-1 | 任务书 1.2 表格 L71、1.4 核心矛盾、D-1 选项乙 | **事实修订**："THS 备选接口 stock_individual_fund_flow_rank"实为东方财富接口（push2 clist，019I R-5 已发现）；核心矛盾重述为"唯一独立源 = THS 主接口（全部资金口径），无口径匹配的独立备选"；删除乙选项 | 发现 1 |
| M-2 | 任务书 D-1 | 裁定为甲（库内 ths_net_inflow 顶替，零网络）；口径偏差接受 + 全链路标注 | D-1 裁定 |
| M-3 | 任务书红线 4 | 明确裁定：`raw_capital_flow` 新增 `capital_source TEXT DEFAULT NULL`（_safe_add_columns L964 后追加，自动迁移）；否决第三档 is_estimated 值 | D-2 裁定 |
| M-4 | 任务书任务 2（实现规格） | 顶替写入规格：em_all_failed 处估算前；UPDATE + INSERT OR IGNORE（禁 INSERT OR REPLACE）；写 main_net_inflow/is_estimated=0/capital_source='ths_total'；**不写** main_net_inflow_pct/super_large_net/large_net；仅当日 1 行 | D-4 裁定 |
| M-5 | 任务书任务 2（返回语义） | 顶替成功返回 `('fallback', '同花顺顶替(全部资金口径，非主力；东财恢复后自动回补)')`；data_status status='fallback' | D-4/D-5 裁定 |
| M-6 | 任务书任务 3（防覆盖） | ① L1925 pre-check SQL 追加 `AND (capital_source IS NULL OR capital_source != 'ths_total')`；② L1498-1505 补采清单 SQL 同步追加；③ EM 三层 INSERT OR REPLACE 显式 `capital_source=NULL`（019E M-7 扩展）；④ 估算三处 UPDATE 追加来源守卫 | D-5 裁定 + 发现 3 |
| M-7 | 任务书红线 6（范围） | 范围 = data_collector.py + db_manager.py（迁移列表）+ index.html（L2482 表头动态标注、L2491 行内同花顺标注、L2550/L2073 加 'fallback'→'⚠️顶替'）；其余文件零改动 | D-8 裁定 |
| M-8 | 任务书红线 2（口径） | 修订为"顶替数据全链路标注口径差异 + 监理知情；可选口径纯净开关" | D-8 裁定 |
| M-9 | 任务书四（验收标准） | 增加：① mock EM 三层全失败 + 库有 ths → 顶替写入断言（main=ths、is_estimated=0、capital_source='ths_total'、status='fallback'）；② EM 恢复重采 → 覆盖 + capital_source 归位 NULL + ths 字段行为记录；③ 补采清单排除 ths_total 断言；④ data_adapter._read_capital_data 返回 THS 值进入 v5 main_capital 评分断言；⑤ 前端标注截图断言 | M-4~M-7 |
| M-10 | 任务书任务 2 注释 | 修订 data_collector L1368-1371/L1916-1920 docstring（主力唯一来源表述加 THS 顶替例外） | D-8 裁定 |
| M-11 | 任务书五（红线） | 补充三条红线：来源标注红线 / EM 回补红线（pre-check+补采清单排除）/ 状态消费红线（fallback 适配三处消费方） | D-8 裁定 |
| M-12 | 任务书备注 | 增补 R-1~R-8 风险清单与"监理口径纯净开关"说明（4 处 WHERE 单行切换）；R-2 维持登记不纳入本批次 | 三、四节 |

---

## 五、评审结论

### 结论：⚠️ **有条件通过**（需按 M-1~M-12 修订 v2 后交付开发）

**通过项**：监理方案一目标（EM 全挂时资金面用真实数据参与评分）方向正确；插入点 ①、is_estimated=0、无条件顶替、不追溯等 PM 倾向项经独立核验与 DB 实证均成立；019E/019I/019J 已建机制（is_estimated 过滤、_call_with_timeout、daemon 线程）均可直接复用。

**必改项（阻塞开发）**：
1. **M-1 事实修订**（最高优先）：任务书"THS 备选接口"认知错误直接导致 D-1 选项乙不可行——按 v1 开发，EM 全挂时顶替必然失败，方案一目标落空。
2. **M-3 + M-6 防覆盖闭环**（次高优先）：capital_source 列 + pre-check/补采清单双向排除，否则 THS 顶替值永驻评分链路、EM 真实数据无法回补——重演 019E 前的"数据滞留"事故。
3. **M-4/M-5/M-7 实现规格与前端标注**：口径偏差的知情权落在标注（data_status + 前端 + 状态映射），缺失即误导用户。

**监理决策点**：口径偏差（全部资金 vs 主力，实证符号可相反）与"评分参与"的权衡——本评审按方案一原意裁定"参与评分 + 标注 + 开关"，监理有权在知情后启用"口径纯净开关"（D-2 附注，4 处单行修改）。

> **架构师独立评审签名**：本评审由架构师于 2026-08-05 独立完成，未采信 PM 结论。核验文件：modules/data_collector.py（L1095-1526/L1897-2300）、modules/data_adapter.py、modules/advisor.py、modules/analysis_engine.py、modules/alert_engine.py、modules/scoring_engine.py、modules/daily_report.py（L440-480）、database/db_manager.py、app.py（L757-796/L1285-1301）、templates/index.html（L2480-2494/L2550）、akshare 1.18.53 源码（stock_fund_flow.py / stock_fund_em.py）、stock_analyst.db（08-05 资金面行/status/近5日覆盖实证）、实时网络探针 1 项、docs/reviews/review_019I_ths_batch_timeout_20260805.md / review_019J_single_stock_timeout_fix_20260805.md。
