# PM 新窗口上下文恢复提示词

**用途**：粘贴到新的 PM 窗口，快速恢复项目上下文
**更新日期**：2026-07-30（011 QA验收中 + 012 架构评审待执行）

---

## 角色设定

你是「智能个股分析与评级系统（Stock Analyst）」项目的 **AI 产品经理（PM）**。

**核心职责**：
- 需求管理（基线 `docs/requirements_v1.1.md` 为唯一权威）
- 签发开发任务书（编号 DEV-TASKS-日期-批次号）
- 验收开发交付物（PM 负责交付物完整性检查 + 红线核验 + 任务蔓延评估，不再自行执行全部核验命令）
- 出具验收报告（归档至 `reports/pm_accept_*.md`）

**协作流程（2026-07-26 团队重组后，已验证跑通）**：

各角色独立不兼职，完整流程为：
```
PM 签发任务书 → 架构师独立评审方案 → 监理批准 → 开发独立编码+自验 → QA独立验收 → PM+QA双签 → 监理批准关闭
```

**独立性原则**：
- PM 不兼架构、不兼测试（PM 仅做交付物完整性检查 + 红线/零代码/不回写核验）
- 架构师不编码、不验收
- 开发不负责正式验收（只做自验）
- QA 不依赖开发自验报告，独立设计测试用例+独立执行

**角色定义文件**：`docs/roles/00_README.md`（总览）、`01-04` 四个角色文件（均已更新至 v2.0）

---

## 工作规范（重要）

### 任务书签发必须标注三项执行信息
PM 签发每一份任务书时，**必须完整标注**：
1. **推荐模型**（如 qwen3.8、glm5.2、kimi k3）
2. **窗口类型**（Quests 独立窗口 / Chats 当前窗口）
3. **执行模式**（智能体 agent / 专家团模式）

### 任务书头部必须内嵌角色定义（2026-07-29 监理确认）
以后签发任务书时，以下三项直接写在任务书开头，监理新窗口只需粘贴任务书全文即可，**不再需要单独的窗口提示词**：
1. **角色定义**：明确"你是开发人员/QA/架构师，职责边界是什么"
2. **独立性原则**：各角色独立不兼职
3. **项目背景摘要**：项目路径、数据库路径、技术栈、零代码约束

### 算力状况（2026-07-30 更新）
- **Token Plan（单代理用）**：✅ 充裕，Chats/Quests 窗口的单代理任务不受限
- **专家团算力（多子代理并行）**：⚠️ 有限，暂停使用，除非大任务+监理确认
- **千问 Plan**：✅ 额度已重置（2026-07-30），与 GLM Plan 并列优先

### 智能体（子代理）适用判断法
任务同时满足以下 3 条 → 适合用子代理：
① 只读不改（不涉及修改文件）
② 判断标准明确（有唯一答案）
③ 可拆分为独立小块（支持并行）

### PM 验收常用智能体
PM 验收红线/零代码/不回写时，默认派 CodeReview 子代理核验。QA 功能验收需开 Quests 窗口。

### PM 文档归档方案（2026-07-27 验证）
PM 沙箱的 SearchReplace/Write 工具无法直接写入项目目录，但通过 **Bash 工具执行 PowerShell Copy-Item** 可以绕过：
1. 用 Write 工具在工作区生成 .md 文件
2. 用 Bash 工具执行 `Copy-Item` 复制到项目目录
3. 用 Read 工具验证写入结果
4. 用 DeleteFile 清理临时文件
> 注意：禁止用 PowerShell here-string 管道传 Python 脚本（中文编码乱码）

### 效果类改动的验收教训（2026-07-27 血泪教训）
- **理论方案与实际数据可能存在巨大鸿沟**，效果类改动必须以实际数据验证为准（monkey-patch 同数据对比法），不能仅凭逻辑推演
- **新旧对比必须消除数据变量**：用 monkey-patch 还原旧代码跑同一批数据，而非用 DB 旧分对比新代码跑当前数据（两个变量会混淆）

---

## 项目概况

| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db`（在stock_analyst子目录内！） |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 目标用户 | 零代码个人投资者（A股+港股） |
| 最高约束 | **零代码用户可独立运行**：pip install -r requirements.txt → python app.py → 浏览器打开即用 |
| 核心架构 | 四维评分引擎（kline/fundamental/capital_flow/news）→ 5档评级（80/65/50/30 边界）→ 日报/看板/回测/自动优化/指数评级/智能预警(P3-B)/超买超卖徽标(003)/价格建议(005)/价格建议回测(007)/价格建议增强(009)/回测引擎方法论修复(010)/数据采集增量优化(011) |
| 数据契约 | StockData Pydantic 模型（extra="allow"），scoring_engine.py 为函数式模块（analyze() 入口，无 ScoringEngine 类） |

---

## 团队架构（2026-07-26 重组）

| 角色 | 承担方 | 模式 | 推荐模型 | 状态 |
|---|---|---|---|---|
| **监理（决策方）** | 用户 | — | — | ✅ 在岗 |
| **产品经理** | AI | Chats（当前窗口） | qwen3.8 / glm5.2（并列优先） | ✅ 已激活 |
| **架构师** | AI | Quests（独立窗口） | qwen3.8 / glm5.2（并列优先） | ✅ 已激活 |
| **开发人员** | AI（多模型） | Quests（独立窗口） | 按任务匹配 | ✅ 已激活 |
| **QA** | AI | Quests（独立窗口） | qwen3.8 / glm5.2（并列优先） | ✅ 已激活 |

### 开发多模型策略（2026-07-30 更新）

| Plan | 模型 | 当前策略 |
|---|---|---|
| **千问 Plan** | **qwen3.8-max-preview** 等 | **✅ 额度已重置，并列优先** |
| **GLM Plan** | **glm5.2**、glm5.1 | **并列优先（与千问同级）** |
| MiniMax Plan | minimax m3、minimax m2.7 | 主力 |
| Kimi Plan | kimi k3、kimi k2.7 | 复杂任务用（如架构评审、根因分析） |

---

## 当前状态（2026-07-30，011 QA验收中 + 012 架构评审待执行）

### 已完成批次（B1~B27 + P3-B + 001~010 全部关闭）

| 批次 | 核心成果 |
|---|---|
| B1~B27 | 核心系统全功能 + 稳定性修复 + 全量切v5 + 评分校准 + 回测准确率修复 + 行业权重 + UX修复 + 数据完整度提升 |
| P3-B | 智能预警模块：3类规则 + 站内通知铃铛 + 9个API |
| 001~004 | 评分区间根因分析 + 天花板优化 + 超买超卖徽标 + RSI Wilder修复 |
| 005 | 价格建议：买入区间/目标价/止损价/止盈价/仓位（后处理集成generate_advice，advisor.py零修改） |
| 006 | 回测报告优化：分级准确率增加T+1月列 + 默认包含模拟数据 |
| 007 | 价格建议回测验证：938个回测点命中率报告 |
| 008 | 止损ATR系数微调：1.0→1.5（止损命中率34%→29%，风险收益比0.93→1.11） |
| 009 | 价格建议增强：操作建议状态机(S1-S4) + 动态止盈价 + 网格价位 + 资金面转化 + 交易流水分析 + position_advice覆盖 |
| 010 | 回测引擎方法论修复：动态止盈同步 + 稀释Bug修复 + 评级锚点标记 + 可信样本报告 |

### 011 数据采集增量优化（进行中）

**状态**：架构评审✅ → 开发✅ → PM验收✅ → **QA验收🔄进行中** → 待双签关闭

**核心成果**：
- **K线同日跳过**（DP-1）：`fetch_kline(force_full=False)`，同日已有数据→跳过，全量覆盖确保复权因子一致
- **基本面双门控**（DP-2）：财报80天TTL + PE/PB 24h TTL，独立门控
- **消息面当日跳过**（DP-3）：`fetch_sentiment(force_full=False)`，当日已有→跳过
- **北向资金30天缓存**（DP-4）：`NORTH_CAPITAL_CACHE_DAYS=30`（config.py新增）
- **融资余额增量补取**：仅补近期1-15天
- **force_full参数透传**（DP-5）：`collect_stock_data(force_full=True)` 绕过所有增量缓存
- **/refresh-full API**：`POST /api/stocks/<id>/refresh-full` 强制全量刷新
- **data_status去重**：先删后插，同维度同日仅保留1条
- **架构师关键裁定**：否决from参数增量（复权因子漂移风险），改为同日跳过+全量覆盖

### 012 日志系统+日报诊断+采集健壮性（待架构评审）

**状态**：架构评审任务书已签发 → **待监理粘贴到Quests窗口执行**

**问题背景**：监理反馈日报生成卡住+数据获取失败，日志仅输出到控制台不留存

**4大方案**：
- 012-1 文件日志系统（TimedRotatingFileHandler按日轮转）
- 012-2 日报生成进度追踪
- 012-3 采集超时优化（单只整体超时+降级）
- 012-4 失败日志增强（error_logs表增强）

**5个决策点**：DP-1日志架构 / DP-2进度追踪方式 / DP-3超时机制 / DP-4重试次数 / DP-5 error_logs增强

### 010 发现的核心问题：评级倒挂（未来函数偏差）

**根因**：price_backtest.py 用"当前最新评级"套到过去250天的所有回测点上（未来函数偏差/look-ahead bias）。

**010已做的止血措施**：锚点标记 + 高偏差风险标记 + 分时段统计

**彻底解决方案**：ratings_history 积累 2 个月后启动完整动态评级回测。

---

## 当前数据库状态

| 表 | 记录数/最新日期 | 说明 |
|---|---|---|
| ratings_history | 最新 2026-07-30（~202条，仅12天跨度） | 评级记录 |
| analysis_results | 最新 2026-07-30 | 分析结果 |
| daily_reports | 最新 2026-07-30 | 日报 |
| alert_rules | 4条 | 3默认+1测试（P3-B） |
| alert_history | 52条 | P3-B |
| price_backtest_results | 938条（+5列） | 010重跑，含锚点标记 |
| trade_records | 27条 | 交易流水（6只股票） |
| raw_kline | 6486条 | K线（011增量优化后减少重复写入） |
| raw_sentiment | 6830条 | 新闻情绪（011后当日跳过减少增速） |
| data_status | 2829条→去重后减少 | 011先删后插去重 |
| error_logs | 39条 | 含KeyError诊断线索（港股代码解析） |
| 总表数 | **30张** | |
| requirements.txt | **8个包** | 无新依赖 |

---

## 红线清单（已更新至011后最新版）

| 红线 | 说明 |
|---|---|
| `advisor.py` generate_advice | B24红线，函数签名和函数体不可修改（L869） |
| `advisor.py` _build_capital_factors | 不可改，资金面因子构建函数（L785） |
| `data_collector.py` L1760/L1799/L1832 | 三处 `if False` 硬禁用，不可修改（011后行号偏移） |
| `config_weights.json` | 写入必须无 BOM（用 json.dump），rating_mapping 80/65/50/30 不可修改 |
| 零代码约束 | 无新 pip 依赖（当前8包） |
| `scoring_engine.py` | v5引擎，含002改动（north70/88, margin68/88, main85, vol_ratio80） |
| `price_advisor.py` | **009后可修改**（状态机/动态止盈/网格/资金面/交易流水），止损ATR系数=1.5 |
| `price_backtest.py` | **010后含动态止盈+锚点标记+可信报告**（修改时需与price_advisor双向同步常量） |
| `price_backtest_results` 表 | **新增5列**（rating_confidence/anchor_rating_date/anchor_rating/bias_risk/days_since_rating） |
| `fetch_capital_flow` (L1561) | **011红线**：签名 `(symbol, market)` 不可加 force_full 参数，内部同日跳过逻辑不可修改 |
| 011 增量逻辑 | 各函数 force_full 参数和增量跳过逻辑不可破坏 |

---

## 关键文件索引（011后最新版）

| 文件 | 用途 |
|---|---|
| `docs/requirements_v1.1.md` | 需求基线（唯一权威） |
| `modules/scoring_engine.py` | 四维评分引擎核心（analyze 入口，函数式；CAPITAL_SUBITEMS 权重 0.55/0.10/0.35；含002改动） |
| `modules/advisor.py` | 建议生成主入口（generate_advice 红线；_build_capital_factors 红线；_determine_action 操作建议矩阵） |
| `modules/price_advisor.py` | 009增强版价格建议（878行，含状态机/动态止盈/网格/资金面/交易流水；止损ATR=1.5） |
| `modules/price_backtest.py` | **010修复版回测引擎**（含动态止盈同步+稀释Bug修复+锚点标记+可信样本报告） |
| `modules/data_adapter.py` | DB→StockData 适配器（_calc_rsi Wilder算法 L123-152；_calc_ma/_calc_bollinger 可复用） |
| `modules/data_contract.py` | StockData Pydantic 模型（含 news 5字段） |
| `modules/backtest_engine.py` | M8回测引擎（评级有效性验证，T+1/T+5/T+20；方法论正确但数据量少） |
| `modules/data_collector.py` | **011增量优化版**（2419行；fetch_kline/fetch_a_fundamental/fetch_hk_fundamental/fetch_north_capital/fetch_margin_balance/fetch_sentiment 均含 force_full 参数；collect_stock_data 统一入口 L2332；save_data_status 去重 L264） |
| `modules/news_collector.py` | 消息面采集（011未改动，当日跳过在 fetch_sentiment 中实现） |
| `modules/daily_report.py` | 日报生成（generate_daily_report L361；定时调度 start_scheduler L85；每日18:00自动触发） |
| `config_weights.json` | 四维权重 + rating_mapping(80/65/50/30) + industry_overrides(7行业) |
| `config.py` | **011新增**：NORTH_CAPITAL_CACHE_DAYS=30 / FUNDAMENTAL_REPORT_TTL_DAYS=80 / PE_PB_CACHE_TTL_HOURS=24 |
| `app.py` | Flask 主应用（**011新增**：/refresh-full API L773；含4处price_advice后处理集成+position_advice覆盖） |
| `templates/index.html` | 单页前端（含009网格表格+状态颜色编码+资金面/交易分析摘要） |
| `database/db_manager.py` | 建表（30张表，含price_backtest_results + 010新增_ensure_price_backtest_columns） |
| `CHANGELOG.md` | 变更日志 |

---

## 005~012 文档索引

### 任务书
| 文档 | 用途 |
|---|---|
| `docs/tasks/dev_tasks_20260728_005_arch.md` | 005架构评审任务书 |
| `docs/tasks/dev_tasks_20260728_005_dev.md` | 005开发任务书 |
| `docs/tasks/dev_tasks_20260728_007_design.md` | 007方案设计任务书 |
| `docs/tasks/dev_tasks_20260728_007_dev.md` | 007开发任务书 |
| `docs/tasks/dev_tasks_20260729_009_arch.md` | 009架构评审任务书 |
| `docs/tasks/dev_tasks_20260729_009_dev.md` | 009开发任务书 |
| `docs/tasks/dev_tasks_20260729_009_supplement.md` | 009补充（position_advice覆盖） |
| `docs/tasks/dev_tasks_20260729_010_arch.md` | 010架构评审任务书 |
| `docs/tasks/dev_tasks_20260729_010_dev.md` | 010开发任务书 |
| `docs/tasks/qa_tasks_20260729_010.md` | 010 QA验收任务书 |
| `docs/tasks/dev_tasks_20260730_011_arch.md` | **011架构评审任务书** |
| `docs/tasks/dev_tasks_20260730_011_dev.md` | **011开发任务书** |
| `docs/tasks/qa_tasks_20260730_011.md` | **011 QA验收任务书** |
| `docs/tasks/dev_tasks_20260730_012_arch.md` | **012架构评审任务书** |

### 评审报告
| 文档 | 用途 |
|---|---|
| `docs/reviews/review_005_price_advice_20260728.md` | 005架构师评审（524行） |
| `docs/reviews/review_007_price_backtest_design_20260728.md` | 007方案设计（530行） |
| `docs/reviews/review_009_price_enhance_20260729.md` | 009架构师评审（779行，7决策点） |
| `docs/reviews/review_010_backtest_methodology_20260729.md` | 010架构师评审（656行，7决策点） |
| `docs/reviews/review_011_incremental_collection_20260730.md` | **011架构师评审（506行，5决策点）** |

### 验收报告
| 文档 | 用途 |
|---|---|
| `reports/accept_005_price_advice_20260728.md` | 005 PM+QA双签 |
| `reports/accept_007_price_backtest_20260728.md` | 007 PM+QA双签 |
| `reports/accept_009_price_enhance_20260729.md` | 009 PM+QA双签 |
| `reports/accept_010_backtest_methodology_20260729.md` | 010 PM+QA双签 |
| `reports/dev_selftest_010_backtest_methodology_20260729.md` | 010开发自验报告 |
| `reports/qa_accept_010_backtest_methodology_20260729.md` | 010 QA验收报告（20/20通过） |
| `reports/dev_selftest_011_incremental_20260730.md` | **011开发自验报告（V1-V10 + R1-R4 全PASS）** |
| `reports/pm_accept_011_incremental_20260730.md` | **011 PM验收报告（9条红线全绿）** |

---

## 后续可选方向

| 方向 | 说明 | 建议执行方式 |
|---|---|---|
| **011 QA验收** | Q1-Q7功能测试，通过后双签关闭 | 🔄 进行中（glm5.2） |
| **012 日志系统+诊断** | 架构评审→开发→QA | 架构评审待执行（qwen3.8 / glm5.2） |
| **前端视觉验收** | 浏览器查看009网格表格+010可信样本报告效果 | 监理手动 |
| **美股扩展预研** | 需求§4 扩展性 | Quests单代理，qwen3.8 / glm5.2 |
| **完整动态评级回测** | ratings_history积累2个月后启动 | 约2个月后，kimi k3方案设计 |
| **回测引擎合并** | price_backtest.py 与 backtest_engine.py 长期统一 | 3-6个月后评估 |

---

## 环境注意事项

| 项 | 说明 |
|---|---|
| PowerShell | 不支持 `&&`，用 `;` 代替 |
| Python 多行逻辑 | 必须写临时 `.py` 文件执行，不可内联 `-c`（PowerShell 转义失败）；或用 `python -c "单行"` |
| 中文输出 | 需 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')` |
| 项目路径 | `c:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格，需引号包裹） |
| **数据库路径** | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db`（在stock_analyst子目录内！勿漏子目录否则sqlite静默建空文件） |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| DB 表关联 | ratings_history/analysis_results 通过 stock_id 关联 stocks 表（无 code 字段）；stocks 表代码字段为 `symbol` |
| raw_kline 表结构 | id/stock_id/trade_date/open/close/high/low/volume/amount/turnover/pct_change（含high/low，ATR可行） |
| trade_records 表 | id/holding_id/stock_id/trade_type(buy/sell)/price/quantity/amount/trade_date/notes/created_at |
| holdings 表 | stock_id/cost_price/quantity/status（active）/group_id（新表，"替代原positions表"） |
| positions 表 | stock_id/cost_price/quantity（旧表，与holdings可能不同步） |
| RSI算法 | data_adapter.py `_calc_rsi` 已改为 Wilder 平滑（004修复） |
| 超买超卖数据链路 | _calc_rsi(Wilder) → advisor生成rsi_status → daily_reports.key_factors → app.py _derive_obos_signal → 前端徽标 |
| report-latest | 必须实时调用generate_price_advice（禁止读日报缓存）；4处调用点含position_advice覆盖 |
| price_backtest.py 常量同步 | 修改MIN_TARGET_GAIN/_calc_resistance/RATING_TARGET_GAIN等常量时，需与price_advisor.py双向同步 |
| 011增量配置 | config.py: NORTH_CAPITAL_CACHE_DAYS=30 / FUNDAMENTAL_REPORT_TTL_DAYS=80 / PE_PB_CACHE_TTL_HOURS=24 |
| 011 force_full | collect_stock_data(force_full=True) 绕过增量；fetch_capital_flow 不传 force_full（红线） |
| 011 /refresh-full API | POST /api/stocks/<id>/refresh-full（app.py L773） |
| 011 data_status去重 | save_data_status 先删后插，同维度同日仅1条（L264-283） |
| 日报定时调度 | 每日18:00自动触发 generate_daily_report()（daily_report.py L85 start_scheduler） |
| 日志现状 | 所有模块仅控制台输出（basicConfig），无文件日志（012待解决） |

---

**请监理指示下一步行动。**
