# DEV-TASKS-20260730-011-ARCH：011 数据采集全链路增量优化 — 架构方案评审任务书

> **签发人**：PM  | **签发日期**：2026-07-30 | **状态**：待架构师执行

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：架构师

**职责边界**：
- 评审 PM 提出的增量优化方案，聚焦架构级决策点
- 评估缓存策略安全性、数据一致性风险、复权因子漂移风险
- 对每个决策点给出明确裁定（采纳/修改/否决）+ 理由
- **不编码、不验收、不写功能代码**
- 交付物：`docs/reviews/review_011_incremental_collection_20260730.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 架构师仅做方案评审，不执行任何代码修改

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db`（在stock_analyst子目录内！） |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前8包） |
| 核心文件 | `modules/data_collector.py`（2230行）、`modules/news_collector.py`（323行） |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 推荐模型 | **glm5.2**（如评估复杂可升级 kimi k3） |
| 窗口类型 | **Quests 独立窗口** |
| 执行模式 | 单代理 agent |
| 交付物 | `docs/reviews/review_011_incremental_collection_20260730.md` |

---

## 一、需求背景

### 1.1 问题发现

PM 对数据采集全链路进行调研后，发现**每次分析都全量重复采集已有数据**，造成大量无效网络请求和等待时间。

**实证数据**（DB 查询结果）：

| 现象 | 证据 |
|---|---|
| 同一只股票同一天采集两次 K线 | `data_status` 表：stock_id=7 的 kline 在 2026-07-29 有两条记录（16:36:00 和 16:48:51），每次都全量下载250天 |
| 消息面权重=0%但仍在采集 | `config.py` L40: `'news': 0.00`，但 `raw_sentiment` 表已有 6830 条记录，每次分析都全量采集+情绪分析 |
| 融资余额每次尝试112个日期 | `fetch_margin_balance` L1964: `range(1, 160)` 逐日请求上交所/深交所API |
| 北向资金数据源已停更但仍在请求 | `fetch_north_capital` L1857: 注释"自2024-08-16起停更"，但每次仍调用 `ak.stock_hsgt_individual_em` |

### 1.2 当前采集链路全貌

```
collect_stock_data(symbol, market)           ← data_collector.py L2147 统一入口
├── fetch_kline(symbol, market)              ← L400，每次全量250天（腾讯接口）
├── fetch_a_fundamental(symbol)             ← L467，每次全量2020年起财报（新浪）+ PE/PB（腾讯）
│   ├── fetch_fundamental_detail(symbol)     ← L581，B10补全（有去重优化）
│   └── fetch_holder_increase(symbol)        ← L663，有10分钟缓存
├── fetch_hk_fundamental(symbol)            ← L761，每次全量（东财）
├── fetch_capital_flow(symbol, market)       ← L1446，✅ 已有同日跳过+批量预取缓存
│   ├── fetch_north_capital(symbol, market)  ← L1803，❌ 每次请求（数据源停更）
│   └── fetch_margin_balance(symbol, market) ← L1937，每次112个日期API调用
└── fetch_sentiment(symbol, market)          ← L2061，每次全量新闻+情绪分析（权重0%）
```

### 1.3 各维度现状与优化空间

| 维度 | 采集函数 | 现有增量机制 | 每次开销 | 优化空间 |
|---|---|---|---|---|
| **K线** | `fetch_kline` (L400) | ❌ 无 | 全量250天 + 逐条写入 | ★★★★★ |
| **基本面(A股)** | `fetch_a_fundamental` (L467) | 部分(B10补全有去重) | 全量2020年起财报 + PE/PB | ★★★★☆ |
| **基本面(港股)** | `fetch_hk_fundamental` (L761) | ❌ 无 | 全量财报 + PE/PB | ★★★★☆ |
| **资金面** | `fetch_capital_flow` (L1446) | ✅ 同日跳过 + 1h缓存 | 已较好 | ★★☆☆☆ |
| **北向资金** | `fetch_north_capital` (L1803) | ❌ 无（停更仍在请求） | 每次akshare请求 | ★★★☆☆ |
| **融资余额** | `fetch_margin_balance` (L1937) | 部分(按日期缓存) | 112个交易日API调用 | ★★★☆☆ |
| **消息面** | `collect_news` (news_collector L234) | ❌ 无（权重0%） | 全量新闻+情绪分析 | ★★★☆☆ |
| **行业** | `fetch_stock_industry` (L2117) | ✅ 仅空时+本地映射 | 已较好 | ★☆☆☆☆ |

### 1.4 数据库现状

| 表 | 记录数 | 说明 |
|---|---|---|
| raw_kline | 6486条 | K线（最新日期 2026-07-29） |
| raw_fundamental | 121条 | 基本面 |
| raw_capital_flow | 3329条 | 资金面 |
| raw_sentiment | 6830条 | 新闻情绪（权重0%但持续采集） |
| data_status | 2829条 | 采集状态（含大量同日重复记录） |
| news_sentiment | 252条 | 新闻聚合 |
| 总表数 | **30张** | |

---

## 二、PM 拟定的初步方案（待架构师评审）

### 2.1 优化方案总表

| # | 优化项 | PM初步方案 | 预期效果 |
|---|---|---|---|
| **011-1** | K线增量化 | 查DB最新日期→仅获取增量部分；7天强制全量刷新 | 日常分析减少 ~98% 请求量 |
| **011-2** | 基本面增量 | 财报数据80天TTL（季度级）；PE/PB 24h TTL | 减少 ~95% |
| **011-3** | 消息面增量 | 当日已有→跳过；权重0%时是否完全跳过 | 当日重复→0次 |
| **011-4** | 北向资金缓存 | 数据源停更后加长缓存（7天） | 从每日→每7天 |
| **011-5** | 融资余额增量 | 查DB最新有数据的日期→仅补近期 | API调用从112→1~5次 |

### 2.2 K线增量方案细节（011-1，最核心）

**现状代码**（`_fetch_kline_tencent` L353-397）：
```python
# 腾讯接口参数：{code},day,,,{KLINE_DAYS},qfq
# 中间两个空位是 from/to 日期，当前留空 = 全量获取最近250天
params = {'param': f'{tencent_code},day,,,{KLINE_DAYS},qfq'}
```

**PM初步方案**：
```
步骤1：查询 raw_kline 中该 stock_id 的 MAX(trade_date) → last_date
步骤2：判断是否需要采集
  - 无数据（首次分析）→ 全量采集（现有逻辑）
  - last_date = 今日 或 最近交易日 → 跳过采集，返回 'skipped'
  - last_date 在7天内 → 增量采集：腾讯接口传入 from=last_date+1
  - last_date 超过7天 → 全量采集（防止复权因子漂移）
步骤3：增量数据追加写入（INSERT OR REPLACE，不删除旧数据）
```

### 2.3 基本面增量方案细节（011-2）

```
步骤1：查询 raw_fundamental 中该 stock_id 的 MAX(report_date) → last_report_date
步骤2：判断
  - 无数据 → 全量采集
  - last_report_date 距今 < 80天（一个季度内）→ 跳过财报采集
  - 距今 ≥ 80天 → 全量采集
步骤3：PE/PB 单独处理（实时估值数据）
  - 查询 data_status 中 fundamental 维度的最新 fetched_at
  - 距今 < 24h → 跳过 PE/PB
  - 距今 ≥ 24h → 仅请求 PE/PB（腾讯接口），更新到最新财报行
```

### 2.4 消息面增量方案细节（011-3）

```
步骤1：查询 news_sentiment 中该 stock_id 当日是否已有数据
步骤2：已有 → 跳过；无 → 正常采集
```

**附加决策点**：消息面权重=0%（`config.py` L40），是否在011中直接跳过消息面采集？

### 2.5 北向资金缓存方案细节（011-4）

```
步骤1：查询 data_status 中 north_capital 维度的最新 fetched_at
步骤2：距今 < 7天 → 跳过（数据源已停更，7天内无新数据）
步骤3：距今 ≥ 7天 → 正常请求（确认是否恢复）
```

### 2.6 融资余额增量方案细节（011-5）

```
步骤1：查询 raw_capital_flow 中该 stock_id 且 margin_balance IS NOT NULL 的 MAX(trade_date)
步骤2：有数据 → 仅获取 last_margin_date+1 到今天的几个交易日
步骤3：无数据 → 保持现有全量回填逻辑（首次分析需要历史数据）
```

---

## 三、架构师需裁定的决策点

### DP-1：K线增量刷新周期 — 7天 vs 3天 vs 动态

**PM倾向**：7天强制全量刷新

**背景**：前复权（qfq）数据会因新增交易日导致历史价格整体偏移。如果只增量补取最近几天的数据，旧数据可能与新数据的复权基准不一致。

**需架构师裁定**：
- 7天是否足够安全？还是应该3天？
- 是否应改为动态判断（如检测到涨跌幅异常时触发全量）？
- 增量补取时，腾讯接口的 `from` 参数格式是否正确支持？（需验证 `{code},day,{from},,{count},qfq` 格式）

### DP-2：基本面增量阈值 — 80天（季度级）是否合理

**PM倾向**：80天

**背景**：A股财报披露周期为季度（Q1报4月、半年报7-8月、Q3报10月、年报4月前），80天可覆盖一个完整季度。

**需架构师裁定**：
- 80天是否合理？港股财报周期是否不同？
- PE/PB 作为实时估值数据，24h TTL 是否合适？
- 是否应区分"财报数据TTL"与"PE/PB TTL"为两个独立门控？

### DP-3：消息面策略 — 权重0%时完全跳过 vs 增量保留

**PM倾向**：增量保留（当日已有→跳过），但不因权重0%而完全禁用

**背景**：
- 当前权重=0%（`config.py` L40），但未来可能恢复
- 完全禁用会导致历史数据断档，恢复权重后需要重新积累
- 增量保留则每日仅采集1次，成本极低

**需架构师裁定**：
- 增量保留还是完全跳过？
- 如果增量保留，TTL 应该是24h还是更长？

### DP-4：北向资金 — 数据源停更后的缓存策略

**PM倾向**：7天缓存

**背景**：`fetch_north_capital` L1857注释"自2024-08-16起停更"（港交所政策变更），B26已将北向资金权重降至0.10。每次请求都返回2024-08-15的旧数据，无意义。

**需架构师裁定**：
- 7天缓存是否合理？还是应该更长（30天）？
- 是否应加配置项 `NORTH_CAPITAL_CACHE_DAYS` 到 `config.py`，便于未来调整？

### DP-5：强制全量刷新机制 — 是否需要手动触发入口

**PM倾向**：增加配置开关 + API入口

**背景**：增量跳过可能导致数据"冻结"在某个状态。如果用户怀疑数据不准，需要有手动触发全量刷新的入口。

**需架构师裁定**：
- 是否需要增加 `/api/stocks/<id>/refresh-full` API端点？
- 还是仅用配置开关（如 `FORCE_FULL_REFRESH=true`）？
- 或者两者都需要？
- 是否需要在 `data_status` 表增加 `last_full_refresh` 字段记录上次全量刷新时间？

---

## 四、红线清单（架构师评审时需注意不可违反）

| 红线 | 说明 | 位置 |
|---|---|---|
| `data_collector.py` L1645/L1684/L1717 | 三处 `if False` 硬禁用，**不可修改** | 资金面估算源禁用 |
| `advisor.py` `generate_advice` | 函数签名和函数体不可修改 | L869 |
| `advisor.py` `_build_capital_factors` | 不可修改 | L785 |
| `config_weights.json` | rating_mapping 80/65/50/30 不可修改 | 评级边界 |
| 零代码约束 | 无新 pip 依赖（当前8包） | requirements.txt |
| `scoring_engine.py` | v5引擎，不可修改 | 评分核心 |

---

## 五、验收标准

架构师评审报告需包含：

1. **每个决策点（DP-1~DP-5）的明确裁定**：采纳/修改/否决 + 理由
2. **K线复权因子安全性评估**：增量补取是否会导致数据不一致
3. **缓存策略一致性评估**：5个维度的TTL是否互相冲突
4. **数据完整性风险评估**：增量跳过是否可能导致评分引擎读不到数据
5. **改动范围确认**：仅限 `data_collector.py` + `news_collector.py` + 可能的 `config.py` 新增配置项
6. **开发任务书建议**：是否可拆分为多个独立子任务并行开发

---

## 六、参考资料

| 文件 | 用途 |
|---|---|
| `modules/data_collector.py` L2147 | `collect_stock_data` 统一采集入口 |
| `modules/data_collector.py` L400 | `fetch_kline` K线采集 |
| `modules/data_collector.py` L353 | `_fetch_kline_tencent` 腾讯K线接口 |
| `modules/data_collector.py` L467 | `fetch_a_fundamental` A股基本面 |
| `modules/data_collector.py` L1446 | `fetch_capital_flow` 资金面（已有增量） |
| `modules/data_collector.py` L1803 | `fetch_north_capital` 北向资金 |
| `modules/data_collector.py` L1937 | `fetch_margin_balance` 融资余额 |
| `modules/data_collector.py` L2061 | `fetch_sentiment` 消息面入口 |
| `modules/news_collector.py` L234 | `collect_news` 新闻采集 |
| `config.py` L22 | `KLINE_DAYS = 250` |
| `config.py` L36-48 | 四维权重配置（news=0.00） |
| `app.py` L1105-1224 | 批量分析路由（采集调用方） |

---

> **PM 备注**：本任务书已内嵌角色定义，监理可直接全文粘贴到 Quests 窗口，无需额外窗口提示词。架构师评审通过后，PM 将据此签发 011 开发任务书。
