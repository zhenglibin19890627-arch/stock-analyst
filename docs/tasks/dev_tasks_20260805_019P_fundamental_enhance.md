# 开发任务书 019P — 基本面数据完善（毛利率补全 + 来源标注 + 趋势分析 + 港股占位行修复）

**签发日期**：2026-08-05
**签发人**：PM
**批次编号**：019P
**优先级**：P2（数据完善：毛利率全空、来源未标注、缺趋势维度、港股展示空值；不影响评分主流程但有损分析价值）
**关联批次**：011（增量 TTL 机制）、B10（字段补全）、019K（来源标注先例）、019L/019N（并行批次）
**架构评审**：⚠️ 有条件通过（评审报告：`docs/reviews/review_019P_fundamental_enhance_20260805.md`），已按 M-1~M-10 修订定稿 v2

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：开发人员

**职责边界**：
- 按本任务书 v2 定稿规格实现基本面数据完善（毛利率补全 + 来源标注 + 趋势分析 + 港股占位行修复），完成编码+自验
- 不负责正式验收（QA 独立验收）
- 不修改红线区域（advisor.generate_advice 主体、风控阈值）
- 交付物：修改后的代码（data_collector.py + advisor.py + index.html + db_manager.py）+ 自验报告 `reports/dev_selftest_019P_fundamental_enhance_20260805.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 开发人员仅做编码+自验，不执行正式验收
- 本任务书为 v2 定稿（架构师有条件通过 + M-1~M-10 已并入），开发以本稿为准

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格，命令行需引号） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db` |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包） |

### 环境约束（硬性，违反将导致执行失败）
1. **项目在 IDE 工区外**：路径含空格，Write 工具直写会报错，须 "工作区 Copy + SearchReplace 编辑 + Copy-Item 覆盖回项目目录"
2. **PowerShell 中文**：追加中文到文件用 `[System.IO.File]::ReadAllText + WriteAllText`（UTF-8），禁止 Add-Content/Out-File（乱码）
3. **PowerShell 内联 Python**：含 `*` 的 SQL 会被通配符解析破坏，须用 `chr(39)` 包裹字符串或写临时 .py 脚本
4. **用户是零代码用户**：所有技术决策以"零代码用户可独立运行"为最高优先级

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 流程路径 | ✅PM 签发 v1 → ✅架构师评审（有条件通过，M-1~M-10 并入 v2） → ✅监理批准 v2（2026-08-05） → ✅开发执行+自验（75/75 断言 + 343 回归 + 真实联调） → ✅QA 独立验收（V1~V11 全 PASS，46 断言） → ✅PM+QA 双签（2026-08-06） → ✅监理批准关闭（2026-08-06） |
| **开发串行约定（M-9）** | **019N → 019P** 串行开发（同文件不同区域，避免并发编辑同一工作树）；范围以**函数名区域**定义（免疫行号漂移）；QA 双方按函数名断言各自区域 + 对方区域函数体未变 |

---

## 一、背景（M-1 事实修订后）

### 1.1 问题（监理指示 + PM 核查 + 架构师独立实证）

| # | 问题 | 实证（修正后） |
|---|---|---|
| 1 | **毛利率全空** | **21 只 A 股（有基本面行）** gross_margin 全 NULL（非"29 只"——29 为自选股总数；另有 2 只次新股 688795/688802 无任何基本面行）；现接口 `stock_financial_analysis_indicator` 毛利率列本身 NaN |
| 2 | **来源未标注** | data_status 仅"基本面数据采集成功"，无数据源信息；前端静态文案 |
| 3 | **缺趋势维度** | `_build_fundamental_factors` 只用最新一期，无改善/恶化判断 |
| 4 | **港股展示空值（根因修正）** | **根因 = PE/PB 占位行遮蔽真实财报行**（5/6 只最新行为全 NULL 占位行，非"EM 源字段限制"）；快手（HK1024）无占位行故正常；港股 TTL 为 **80 天共用常量**（非"14 天"——"14 天"是 data_status 中 elapsed 天数） |

### 1.2 核心实证（PM + 架构师独立实测）

**`ak.stock_financial_abstract('600276')`（新浪-财务报表-关键指标）**：
- 结构：shape=(80, 106)——**行=80 个指标，列=['选项','指标']+104 个报告期列**（**无需转置**，M-2 修正）
- 同一指标名出现多次（'毛利率'×2 等，按"选项=常用指标优先+取第一行"去重——R-1）
- 报告期列最新在前（20260331→19971231，格式需转 `2026-03-31`）——**遍历方向与现接口相反**（R-2）
- **毛利率有值**（20260331=86.60）；销售净利率/营收增长与现接口一致（同源）；ROE 常用指标组=4.15（现接口摊薄口径=3.59，口径迁移文档化）；**ocf 该期为 NaN**（现接口有值 0.3448——P1 保护依据）
- **次新股顺带解决**：688795/688802 现接口返回空，abstract 返回 9 期有值（毛利率 67.35/60.05）——发现 4

### 1.3 增量机制现状（监理要求"不重复获取"已有保障）

011 批次双 TTL 门控（A股 80 天财报 + 24h PE/PB；港股共用 80 天常量）。本批次在 TTL 门控内**附加完整性检查**（A-6，见任务 5），不破坏既有机制。

---

## 二、执行角色

**开发**（单人）

---

## 三、任务范围（v2 定稿——M-2~M-6）

> **改动范围：4 文件——`modules/data_collector.py`（基本面区：fetch_a_fundamental 系 + fetch_hk_fundamental 系）+ `modules/advisor.py`（因子函数区）+ `templates/index.html`（标注/趋势展示）+ `database/db_manager.py`（迁移一行）。** 以**函数名区域**定义范围（M-9）。

### 任务 1：毛利率补全（M-2——方案 A'：abstract 主源 + 3 项保护）

**文件**：`modules/data_collector.py`
**函数**：`_fetch_a_fundamental_sina`（L542-545）/ `fetch_a_fundamental`（财报写入 L610-675）

**A' 方案**：
1. **主源切换**：`_fetch_a_fundamental_sina` 改调 `ak.stock_financial_abstract(symbol=symbol)`
2. **结构适配**（修正 v1"需转置"表述）：按 `指标` 列匹配取行（同名去重：选项=常用指标优先 + 取第一行）；按报告期列遍历（**最新在前**）；`20260331`→`2026-03-31`；**写最近 8 期（2 年）**（否决全历史 100+ 期，防 UI 膨胀 R-8）
3. **字段映射表（架构师实测行名）**：

| DB 列 | abstract 指标行 | 说明 |
|---|---|---|
| gross_margin | '毛利率' | 核心修复 |
| net_margin | '销售净利率' | 与现值一致 |
| revenue_growth | '营业总收入增长率' | 与现值一致 |
| profit_growth | '归属母公司净利润增长率' | 归母口径更标准，接受迁移 |
| roe | '净资产收益率(ROE)'（常用指标组） | **值迁移 3.59→4.15**（现接口摊薄口径→常用口径），接受并文档化 |
| debt_ratio / current_ratio / quick_ratio | '资产负债率' / '流动比率' / '速动比率' | 全有值 |
| ocf_to_net_profit | '经营活动净现金/归属母公司的净利润' | **可为 NaN，见 P1** |

4. **三项衔接保护（A' 核心，缺一即返工）**：
   - **P1（必需）ocf 保留**：abstract 该期 ocf 为 NaN 且 DB 已有同 (stock_id, report_date) 行 ocf 非 NULL → **保留原值**（实测 600276 20260331：abstract NaN vs DB 0.3448，不保留即数据回退，违反回归红线）
   - **P2（必需）降级**：abstract 异常/超时 → 落回现接口 `stock_financial_analysis_indicator`（保留现路径为降级层），data_status 标注'新浪指标(analysis_indicator 降级)'
   - **P3（必需）超时保护**：abstract 调用须 daemon 线程 join(timeout) 包装（019I 模式同型，**自建闭包于基本面区域**——`_call_with_timeout` 是 THS 函数内闭包不可 import，发现 5），严禁裸调用
5. **B10 链路（fetch_fundamental_detail / 调用点 L2865-2910）不改**：abstract 全字段写入后 dedup 自然跳过
6. **次新股顺带解决**（688795/688802 现接口空 → abstract 有值）
7. **PE/PB 腾讯源流程不变**（财报重采时 PE/PB 必重取并合并至最新真实行）

### 任务 2：趋势分析（M-3——仅展示不进评分，口径双轨制）

**文件**：`modules/advisor.py`
**函数**：`_build_fundamental_factors`（L1068-1108 区域，**B24 红线外**）+ `_describe_dimension`（L212-221）+ `_detect_risks`（L404-406 附近）+ `_pick_top_factors`（L449-458）

**规格**：
1. **不进 v5 评分**（架构师独立裁定）：scoring_engine / config_weights / data_contract **零改动**——评分反映当前快照，趋势是派生信息（019E 纯净精神 + 防季度性跳变）
2. **口径双轨制**：
   - **环比仅限期间/时点型指标**：毛利率、净利率、资产负债率、流动比率、速动比率（文案"较上期"）
   - **累计型 ROE 禁止环比**（Q1=4.15 vs 年报=14.26 不可比）→ **仅同比**（report_date 前推 1 年匹配同类型报告期，无同期数据跳过该指标）
   - **增速指标（营收/净利增长）**：不做环比，表述"增速加快/放缓"（比较相邻两期增速）
   - **变化阈值**：|Δ| < 1pct 视为"平稳"（防噪声）
3. **粒度**：`fund_trend` 汇总（基本面环比改善/恶化/平稳，多数投票 + 阈值）+ 单指标趋势串 2~3 条（ROE 同比、毛利率环比等）
4. **输出四位置**：
   - 改善 → `_describe_dimension` 基本面分支 highlights 追加趋势文案（评分理由）
   - 恶化 → `_detect_risks` 基本面分支 risks 追加恶化提示（风险提示）
   - 前端因子卡：`_factorPriority.fundamental`（index.html L5172）首位插入 `fund_trend` + `_dimFactorLabels`（L5185-5189）+ tooltips（L5211-5218）
   - 日报关键因子：`_pick_top_factors` 基本面优先级（L449-458）插入 `fund_trend`
5. **数据源**：raw_fundamental 多期行（`SELECT ... ORDER BY report_date DESC LIMIT 8`）；混合来源比较接受（存量旧期 analysis_indicator + 新期 abstract，同新浪域可比，文档化）
6. **数据不足兜底**：缺期时跳过对应指标，文案"历史数据不足，暂无趋势判断"（R-9，不崩溃）

### 任务 3：来源标注（M-4——三通道）

**文件**：`database/db_manager.py` + `modules/data_collector.py` + `templates/index.html`

1. **加列**：`raw_fundamental.data_source TEXT DEFAULT NULL`（`_migrate_columns` L965 后追加一行，019K capital_source 同型；app.py L724 SELECT * 自动透出，**app.py 零改动**）
   - 值域：`'sina_abstract'` / `'sina_analysis_indicator'`（降级）/ `'em_hk'`（港股 EM）
   - 估值（PE/PB 腾讯时点值）不落列，随最新行共存，表头注明
2. **data_status message 前缀**：`'新浪abstract财报+腾讯估值: 基本面数据采集成功'` / `'新浪指标(analysis_indicator降级)+腾讯估值: ...'` / `'港股EM财报+腾讯估值: ...'`（收口处 L728/L731/L735/L1069/L1072）
3. **前端**：L2470 静态来源文案改动态（019K L2484-2490 同型）：读 `/api/stocks/<id>/fundamental` → 按 data_source 去重生成表头"来源：新浪关键指标(abstract)/港股东方财富"；行级 `<sup>` 标注混合来源行；估值表头注"估值：腾讯行情"

### 任务 4：港股占位行修复（M-5——A-4 裁定）

**文件**：`modules/data_collector.py`
**函数**：`fetch_hk_fundamental`（写入端）

**规格**（根因=PE/PB 占位行遮蔽真实财报行，R-3 高优先级）：
1. PE/PB 合并 SELECT（L1032-1035）改为"取最新**含指标值**的真实财报行"（排除全指标 NULL 的占位行）
2. 财报写入成功后，清理本股票"全部指标字段 NULL 且 report_date 晚于最新真实财报行"的占位行（占位行仅含 PE/PB 时点值，真实财报行存在后无信息增量，PE/PB 下次采集即重新合并，零数据损失）
3. 该修复同时是任务 5 自动回补**收敛的前提**（否则港股每日触发回补且永不收敛）
4. 港股备用源：**维持登记**（akshare 港股财务接口均为 EM 源，无独立非 EM 源——架构师核验）

### 任务 5：存量自动回补（M-6——A-6 裁定）

**文件**：`modules/data_collector.py`
**函数**：`fetch_a_fundamental`（TTL 门控 L560-605）

**规格**：
1. **机制**：`skip_financial` 判定为 True 前，查最新一期行 `gross_margin IS NULL` → **不跳过财报采集**（走 abstract 全字段重采）
2. **收敛性**：abstract 一次重采写入 8 期含毛利率 → 完整性检查通过 → 恢复"同日跳过"（前提：任务 4 港股占位行修复）
3. **TTL 兼容（红线 4）**：门控语义不变（完整数据仍 80 天跳过）；"不重复获取"= "已有完整数据不重复获取"，完整性缺失时获取缺项不构成重复（B10 先例精神）
4. **message 区分**：回补场景标注 `'财报补全(毛利率缺失触发)'`（R-4，与"同日跳过"区分）
5. **否决手动 force_full**（零代码）；**否决不处理**（80 天等待不可接受，监理"完善数据"隐含存量诉求）
6. **回补范围**：全部 A 股（21 只有行股票 + 2 只次新股自然采集）；港股经任务 4 修复后同机制收敛

### 明确不改范围（v2 定稿）

- `modules/advisor.py` 的 `generate_advice` 主体（L1198-1297，**B24 红线**）— 零改动
- `modules/scoring_engine.py` / `config_weights.json` / `modules/data_contract.py` — 零改动（趋势不进评分）
- `modules/data_adapter.py` — 零改动（读取路径不变）
- `modules/alert_engine.py` / `modules/analysis_engine.py` — 零改动
- `app.py` — 零改动（SELECT * 自动透出 data_source）
- `config.py` / `requirements.txt` — 零改动
- 资金面区域（fetch_capital_flow 系）— 零改动（019N 区域，串行隔离）
- B10 链路（fetch_fundamental_detail / 调用点）— 零改动

---

## 四、验收标准（v2 定稿——M-8）

1. **代码级核查**：abstract 主源（函数级 grep）；`data_source` 列迁移存在；P1/P2/P3 保护实现；港股占位行修复；TTL 完整性检查
2. **编译验证**：`python -m py_compile modules/data_collector.py modules/advisor.py database/db_manager.py` 无错误
3. **功能验证（QA mock）**：
   - mock abstract（行=指标结构、**同名去重**、**最新在前列序**）→ 断言 8 期写入 + gross_margin 非空 + report_date 格式 `YYYY-MM-DD`
   - mock abstract 异常/超时 → 降级现接口 + data_status 标注'analysis_indicator 降级'
   - **ROE 环比用例（R-5）**：Q1 与年报混排 → 不输出 ROE 环比、同比正确
   - 趋势输出四位置断言（highlights / risks / 因子卡 / 日报关键因子）
   - **ocf 保留断言（P1）**：abstract NaN + DB 有值 → 值保留
4. **TTL 三态（M-8）**：最新期 gross_margin NULL → 回补触发（message '财报补全'）；有值 → 同日跳过（回归）；80 天 TTL 行为不变
5. **港股修复断言（M-8）**：占位行清理（全指标 NULL 行删除、最新真实行可读）；PE/PB 合并到最新真实财报行
6. **来源标注断言（M-8）**：data_source 迁移幂等（重复启动不报错）；data_status message 前缀；前端表头/行级标注
7. **零改动确认**：范围外文件哈希不变；区域函数断言（019N 串行后：资金面函数链未变）
8. **回归**：`python -m pytest tests/` 全绿；`python -m py_compile` 全过

---

## 五、红线约束（v2 定稿——M-7）

1. **B24 红线**：`advisor.generate_advice`（L1198-1297）禁止修改——趋势接入点均在外部既有函数内
2. **范围红线（M-7 扩展）**：改动限 data_collector.py（基本面区）+ advisor.py（因子区）+ index.html（标注/展示）+ **db_manager.py（迁移一行）**；资金面区零改动（019N 串行隔离）
3. **超时红线（M-7 补充）**：abstract 调用必须 daemon 闭包超时包装（**自建**，不可 import THS 闭包），严禁裸调用；降级路径同
4. **ocf 保留红线（M-7 补充）**：REPLACE 多期写入不得破坏既有 ocf 值（P1）
5. **评分纯净红线（M-7 补充）**：趋势不进评分；scoring_engine / config_weights / data_contract **零改动**
6. **零代码约束**：不引入新 pip 依赖（akshare 内置）；config.py 不碰
7. **增量红线（011 延续）**：TTL 门控语义不变（完整性检查在门控内附加）
8. **真实数据源红线**：数据必须来自真实财务数据（abstract/EM/腾讯），不得引入估算公式
9. **ROE 口径迁移文档化**：3.59→4.15（常用指标组口径），QA 断言值域合理（0<v<100）而非精确值

---

## 六、执行顺序

```
Step 1: ✅ PM 签发 v1
Step 2: ✅ 架构师评审（2026-08-05 有条件通过，M-1~M-10 并入 v2）
Step 3: ✅ 监理批准 v2（2026-08-05）
Step 4: ⏳ 开发执行 + 自验（串行：019N → 019P）
Step 5: ⏳ QA 独立验收 → PM+QA 双签 → 监理批准关闭
```

---

## 七、PM 备注（M-10——风险与认知修正）

1. **立项来源**：监理指示"基本面只有一条不够，要有恶化/改善说法"+"完善数据（其他接口/官方报告）+ 不重复获取 + 标注来源"→ PM 核查 → 监理批准 019P 立项。
2. **三项 PM 认知修正（M-1，架构师独立实证）**：
   - "29 只毛利率全空" → **21 只 A 股**（另有 2 只次新股无基本面行）
   - "港股 14 天 TTL" → **80 天共用常量**（'14天'为 elapsed 天数）
   - "港股字段不全=EM 源限制" → **根因=PE/PB 占位行遮蔽真实财报行**（5/6 只，快手无占位行故正常）
3. **架构师新发现风险（本批次内处理）**：R-1（指标同名去重）、R-2（列序最新在前）、**R-3（港股占位行遮蔽，高——本批次修复）**、R-4（回补 message 语义）、R-5（ROE 环比陷阱）、R-6（abstract 超时降级）、R-7（REPLACE 覆盖既有值——P1 保护）、R-8（全历史 UI 膨胀——写 8 期）、R-9（趋势数据不足兜底）
4. **监理决策点**：本批次按"趋势仅展示不进评分"裁定（可见性由四输出位置保证）；若监理坚持趋势参与评分需另立批次（涉及 data_contract/scoring_engine/权重，范围远超本批次）。
5. **串行开发**：019N → 019P（同文件不同区域）；任务书范围以函数名定义，免疫 019N 落地后的行号漂移。
6. **港股备用源登记**：akshare 港股财务接口均 EM 源，无独立非 EM 源——维持现状登记（与 019K 港股资金面缺口同型处置）。
7. **官方报告（stock_financial_report_sina）登记后续候选**：abstract 已覆盖现写全部字段（A-5 裁定不纳入）；后续可单独立项用利润表做 cross-check。
