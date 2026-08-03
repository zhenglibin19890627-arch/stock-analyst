# 评审意见：011 数据采集全链路增量优化 — 架构方案评审

| 项目 | 内容 |
|---|---|
| **文档编号** | REVIEW-011-INCREMENTAL-COLLECTION-20260730 |
| **评审类型** | 架构方案评审（架构师，响应 DEV-TASKS-20260730-011-ARCH） |
| **评审日期** | 2026-07-30 |
| **评审人** | 架构师（AI） |
| **关联需求** | PM 调研发现每次分析全量重复采集已有数据，造成大量无效网络请求和等待时间 |
| **评审对象** | data_collector.py + news_collector.py + config.py 增量采集策略 |
| **总体结论** | **DP-1 修改（否决 from 参数增量，改为同日跳过+全量覆盖）；DP-2/DP-3 采纳；DP-4 修改（7 天→30 天）；DP-5 修改（采纳 API 入口，否决全局开关+新字段）。建议拆分为 5 个可并行子任务。** |

---

## 〇、评审基础

### 0.1 评审范围

本次评审覆盖 `data_collector.py`（2230 行）统一采集入口 `collect_stock_data`（L2147）及其调用的全部子函数，以及 `news_collector.py`（323 行）消息面采集链路。

### 0.2 架构师独立验证

架构师逐行审读了以下代码段以验证 PM 调研结论：

| 验证项 | 代码位置 | 验证结果 | 影响 |
|---|---|---|---|
| K 线无增量机制 | `_fetch_kline_tencent` L353-397 | ✅ 确认：每次全量 250 天，param 格式 `{code},day,,,{count},qfq`，from/to 留空 | PM 结论正确 |
| K 线逐条 INSERT OR REPLACE | `fetch_kline` L419-440 | ✅ 确认：250 条逐条写入，幂等安全 | 关键：复权因子可通过全量覆盖修正 |
| A 股基本面每次全量获取 | `fetch_a_fundamental` L467-578 | ✅ 确认：每次调 `ak.stock_financial_analysis_indicator(start_year='2020')` + PE/PB 腾讯接口 | PM 结论正确 |
| PE/PB 合并到最新财报行 | `fetch_a_fundamental` L551-568 | ✅ 确认：UPDATE 到 MAX(report_date) 行，不创建新行 | 关键约束：PE/PB 独立更新时不能触发财报重新获取 |
| 资金面已有同日跳过 | `fetch_capital_flow` L1464-1505 | ✅ 确认：已有"同日跳过"+ 防覆盖机制（两层 gate） | 资金面不需要改动 |
| 北向资金每次请求 | `fetch_north_capital` L1803-1894 | ✅ 确认：每次调 `ak.stock_hsgt_individual_em`，L1856 注释"自 2024-08-16 起停更" | 停更近 2 年仍在每次请求 |
| 融资余额逐日 API | `fetch_margin_balance` L1937-2054 | ✅ 确认：`range(1, 160)` 逐日请求，但有模块级缓存 `_MARGIN_CACHE` | 缓存仅限单次进程，重启失效 |
| 消息面无增量 | `fetch_sentiment` L2061-2080 → `collect_news` L234 | ✅ 确认：每次全量采集+情绪分析，无当日跳过检查 | PM 结论正确 |
| holder_increase 已有 10 分钟缓存 | `fetch_holder_increase` L679 | ✅ 确认：`_holder_cache` 10 分钟有效期 | 不在本次优化范围 |
| data_status 无唯一约束 | `db_manager.py` L289-299 | ✅ 确认：data_status 表无 UNIQUE 约束，每次 INSERT 新行 | 解释了 2829 条记录的成因 |

### 0.3 红线合规确认

| 红线 | 状态 | 说明 |
|---|---|---|
| L1645/L1684/L1717 `if False` 硬禁用 | ✅ 不触碰 | 本次改动不涉及资金面估算源 |
| `advisor.py` `generate_advice` | ✅ 不触碰 | 本次改动不涉及 advisor 模块 |
| `advisor.py` `_build_capital_factors` | ✅ 不触碰 | 同上 |
| `config_weights.json` rating_mapping | ✅ 不触碰 | 本次改动不涉及评级边界 |
| 零代码约束（无新 pip 依赖） | ✅ 不触碰 | 所有改动仅使用已有 stdlib + akshare + requests |
| `scoring_engine.py` | ✅ 不触碰 | 本次改动不涉及评分引擎 |

---

## 一、决策点裁定

### DP-1：K 线增量刷新周期 — 7 天 vs 3 天 vs 动态

#### 裁定：**修改** — 否决 from 参数增量方案，改为「同日跳过 + 全量覆盖」

#### 1.1 PM 方案回顾

PM 提出：
- `last_date` = 今日/最近交易日 → 跳过采集
- `last_date` 在 7 天内 → **增量采集**：腾讯接口传入 `from=last_date+1`
- `last_date` 超过 7 天 → 全量采集

#### 1.2 否决 from 参数增量方案的理由

**核心风险：前复权（qfq）因子漂移导致数据不一致。**

腾讯 `fqkline/get` 接口返回的 qfq 数据以**最新交易日为基准**计算前复权价格。当发生除权除息事件（分红、送股、配股等）时，所有历史价格都会整体偏移。

PM 的增量方案只获取 `last_date+1` 到今天的新数据并追加写入（INSERT OR REPLACE 仅覆盖新增行），这会导致：

```
DB 中已有：  [旧复权基准的 2026-01-01 ~ 2026-07-28 数据]
增量补取：  [新复权基准的 2026-07-29 ~ 2026-07-30 数据]
                ↑ 基准不一致，技术指标计算（MA、MACD、RSI 等）在拼接处出现断裂
```

**具体场景**：假设某股票在 2026-07-29 除权除息（10 转 5），则 07-28 及之前的收盘价在旧基准和新基准下不同。增量补取只取了 07-29 以后的数据，DB 中 07-28 及之前的数据仍是旧复权基准——**技术指标计算会出错**。

**7 天窗口不能消除此风险**：除权除息事件可能在任何一天发生，7 天窗口内如果有除权除息，增量补取的数据与旧数据的复权基准就不一致。

#### 1.3 架构师推荐方案

```
fetch_kline(symbol, market):
  步骤1：查询 raw_kline 中该 stock_id 的 MAX(trade_date) → last_date
  步骤2：获取最近交易日（today 或 today-1 取决于收盘时间）
  步骤3：判断
    - last_date >= 最近交易日 → 跳过采集，返回 ('skipped', '当日已有K线数据')
    - last_date < 最近交易日 或 无数据 → 全量采集（保持现有 _fetch_kline_tencent 不变）
  步骤4：全量采集返回的 250 条全部 INSERT OR REPLACE（现有逻辑），确保复权因子一致
```

**方案优势**：

| 维度 | PM 方案（from 增量） | 架构师方案（全量覆盖） |
|---|---|---|
| 同日重复请求 | ✅ 减少（跳过） | ✅ 减少（跳过） |
| 复权因子一致性 | ❌ 有断裂风险 | ✅ 每次全量覆盖，基准始终一致 |
| 接口参数改动 | 需验证 from 格式 | ✅ 零改动（不改变 param 格式） |
| 实现复杂度 | 高（需处理 from 格式+增量拼接） | 低（仅增加 DB 查询判断） |
| 网络请求数据量 | 增量时减少（少量行） | 不变（仍 250 条约 50KB） |

**关键判断**：腾讯接口每次返回 250 条 K 线约 50KB，网络传输成本极低（单次 <0.5s）。真正的高成本操作是"同一只股票同一天重复请求"（如批量分析后重新分析、添加持仓后重新分析等场景），**同日跳过**即可消除 100% 的冗余请求。不需要为节省 50KB 数据量而承担复权因子不一致风险。

#### 1.4 关于"最近交易日"判断

需注意：交易日 ≠ 自然日。周六/周日/节假日不是交易日。判断逻辑建议：

```python
# 简化方案：last_date >= 今日日期 → 跳过（非交易日时今日无新数据，跳过安全）
# 严格方案：计算上一个交易日，last_date >= 上一个交易日 → 跳过
```

**推荐简化方案**：用自然日判断即可。如果今天是周六，last_date 可能是周五的数据，`last_date < 今日(Saturday)` 会触发全量采集——虽然多了一次请求，但保证了数据新鲜度，且仅在周末分析时发生一次，成本可接受。

#### 1.5 关于 save_data_status 同日重复记录

当前 `save_data_status`（L261-271）每次 INSERT 新行，导致 data_status 表 2829 条记录中大量同日重复。建议在本次改动中一并修复：

```
方案：将 save_data_status 改为 INSERT OR REPLACE
前提：data_status 表需增加 UNIQUE(stock_id, dimension, date(fetched_at)) 约束
```

**但**：此改动涉及 DB schema 变更（需要 ALTER TABLE + 数据迁移），且 data_status 表被全系统广泛引用。**架构师建议将此项作为 011 的可选项，不列为必须项**，由 PM 决定是否纳入。

---

### DP-2：基本面增量阈值 — 80 天（季度级）

#### 裁定：**采纳** — 80 天财报 TTL + 24h PE/PB TTL，双门控独立

#### 2.1 80 天财报 TTL 评估

A股财报披露周期：

| 财报类型 | 法定披露截止日 | 距上一期约 |
|---|---|---|
| Q1 报 | 4 月 30 日 | — |
| 半年报 | 8 月 31 日 | ~120 天 |
| Q3 报 | 10 月 31 日 | ~90 天 |
| 年报 | 4 月 30 日 | ~180 天 |

80 天 TTL 可以确保：
- 在同一财报季度内不会重复采集（任何两个相邻财报披露日之间最短约 90 天）
- 新财报发布后 80 天内跳过，第 81 天触发采集获取新财报

**80 天阈值合理，采纳。**

#### 2.2 港股财报周期差异

港股财报为半年报+年报（约 180 天周期），80 天 TTL 对港股过于频繁（每 80 天采集一次，但港股半年才更新一次）。但考虑当前仅 1 只港股（HK3690），影响极小。

**建议**：当前 80 天统一使用。若未来港股增多，可通过 config.py 配置项区分 A 股/港股 TTL。不在本次实现。

#### 2.3 PE/PB 24h TTL 评估

PE/PB 是实时估值数据（腾讯接口返回最新行情），每个交易日都会变化。24h TTL 合理：
- 当天分析过 → 24h 内跳过 PE/PB
- 次日分析 → 重新获取 PE/PB

**24h TTL 合理，采纳。**

#### 2.4 双门控独立性约束（关键）

PM 方案中财报数据 TTL 和 PE/PB TTL 是**两个独立门控**，架构师确认此设计正确：

```
fetch_a_fundamental(symbol):
  门控A（财报数据）：
    查 raw_fundamental 中 MAX(report_date) → last_report_date
    - 无数据 → 全量采集（财报+PE/PB）
    - 距今 < 80 天 → 跳过财报采集，进入门控B
    - 距今 ≥ 80 天 → 全量采集（财报+PE/PB）

  门控B（PE/PB，仅当门控A跳过时执行）：
    查 data_status 中 fundamental 维度的最新 fetched_at
    - 距今 < 24h → 跳过 PE/PB，整体返回 'skipped'
    - 距今 ≥ 24h → 仅请求 PE/PB（腾讯接口），UPDATE 到最新财报行
```

**关键代码约束**：当门控B执行"仅请求 PE/PB"时，**不得**调用 `ak.stock_financial_analysis_indicator`（即跳过 `_fetch_a_fundamental_sina`），只调用 `_fetch_valuation_tencent`。当前代码中这两步是串行耦合的（L477-568），开发时需要拆分为两个独立函数调用。

#### 2.5 fetch_fundamental_detail 去重检查

`collect_stock_data` L2184-2198 已有 B11 去重逻辑（检查已有字段数 ≥5 则跳过 `fetch_fundamental_detail`）。此逻辑与 011 增量方案不冲突，应保留。但注意：当门控A跳过财报采集时，整个 B10 补全块（L2170-2205）也应跳过，因为财报数据未变。

---

### DP-3：消息面策略 — 权重 0% 时完全跳过 vs 增量保留

#### 裁定：**采纳增量保留** — 当日已有 → 跳过，不因权重 0% 完全禁用

#### 3.1 权重 0% 时的采集策略

当前状态：`config.py` L40 `news: 0.00`，但 `raw_sentiment` 已有 6830 条记录，每次分析仍全量采集。

**同意 PM 判断**：不因权重 0% 而完全禁用消息面采集。理由：
1. 未来可能恢复权重（数据源改善后）
2. 完全禁用会导致历史数据断档，恢复后需重新积累
3. 增量保留成本极低（当日仅采集 1 次）

#### 3.2 TTL 选择

**推荐当日 TTL**（当日已有 → 跳过），不需要更长。

实现方式：在 `fetch_sentiment`（L2061）中增加前置检查：

```
fetch_sentiment(symbol, market):
  步骤1：查 news_sentiment 中 stock_id + 今日 是否已有记录
  步骤2：已有 → 返回 ('skipped', '当日已有消息面数据')
  步骤3：无 → 调用 collect_news（现有逻辑）
```

注意：检查应放在 `fetch_sentiment` 中（而非 `collect_news` 中），因为 `collect_news` 可能有其他调用路径（如命令行独立调用），不宜在内部增加跳过逻辑。

#### 3.3 raw_sentiment 表数据膨胀

`raw_sentiment` 表已有 6830 条记录，每次采集约 20-40 条新闻。实施当日 TTL 后，增速将从"每次分析新增 20-40 条"降至"每日新增 20-40 条"。

**建议（可选项）**：可在 011 或后续任务中增加 raw_sentiment 清理逻辑（保留最近 90 天），但不作为 011 必须项。

---

### DP-4：北向资金 — 数据源停更后的缓存策略

#### 裁定：**修改** — 7 天 → 30 天，增加 config.py 配置项

#### 4.1 缓存周期裁定

PM 倾向 7 天缓存。架构师认为 **30 天更合理**：

| 维度 | 7 天 | 30 天 |
|---|---|---|
| 无效请求频率 | 每月约 4 次 | 每月 1 次 |
| 数据恢复检测延迟 | 7 天 | 30 天 |
| 评分影响 | B26 已降权至 0.10 | B26 已降权至 0.10 |
| 数据现状 | 停更近 2 年（2024-08-16 → 2026-07-30） | 同左 |

数据源已停更近 2 年，恢复概率极低。30 天缓存意味着每月仅 1 次确认请求（确认是否恢复），既避免频繁无效请求，又不会永久冻结。

**裁定：30 天。**

#### 4.2 配置项建议

**采纳**增加配置项到 config.py：

```python
# 北向资金数据源已停更（2024-08-16），缓存有效期（天）
NORTH_CAPITAL_CACHE_DAYS = 30
```

便于未来调整，无需修改代码。

#### 4.3 实现位置

在 `fetch_north_capital`（L1803）函数开头增加前置检查：

```
fetch_north_capital(symbol, market):
  现有逻辑：market != 'a_stock' → return skipped

  新增前置检查：
    查 data_status 中 north_capital 维度的最新 fetched_at
    - 距今 < NORTH_CAPITAL_CACHE_DAYS → return ('skipped', '北向资金缓存有效(30天)')
    - 距今 ≥ NORTH_CAPITAL_CACHE_DAYS → 继续现有逻辑
```

注意：`save_data_status(stock_id, 'north_capital', ...)` 的 dimension 值需要统一。当前代码中 north_capital 的采集状态是否已通过 save_data_status 记录需要确认——若未记录，需要补充。

---

### DP-5：强制全量刷新机制

#### 裁定：**修改** — 采纳 API 入口，否决全局配置开关，否决 last_full_refresh 字段

#### 5.1 API 入口

**采纳**增加 `/api/stocks/<id>/refresh-full` API 端点。

理由：用户通过界面触发单只股票全量刷新，比全局配置更直观、更精确。

实现方式：
1. `collect_stock_data(symbol, market, force_full=False)` 增加可选参数
2. `force_full=True` 时，所有子函数的增量检查被绕过（直接执行采集）
3. `app.py` 新增路由：

```python
@app.route('/api/stocks/<int:sid>/refresh-full', methods=['POST'])
def api_refresh_full(sid):
    # 查股票信息 → collect_stock_data(symbol, market, force_full=True)
    ...
```

**关键约束**：force_full 参数需要传递到每个子函数：
- `fetch_kline(symbol, market, force_full)` → 跳过 last_date 检查
- `fetch_a_fundamental(symbol, force_full)` → 跳过 80 天/24h 检查
- `fetch_sentiment(symbol, market, force_full)` → 跳过当日检查
- `fetch_north_capital(symbol, market, force_full)` → 跳过 30 天检查
- `fetch_capital_flow(symbol, market)` → 已有同日跳过，force_full 时也需绕过
- `fetch_margin_balance(symbol, market, force_full)` → 跳过增量补取

#### 5.2 否决全局配置开关

**否决** `FORCE_FULL_REFRESH=true` 全局配置。

理由：
1. 全局开关会导致所有股票都强制全量刷新，违背"增量优化"的初衷
2. 用户通常只需刷新特定股票（怀疑数据不准的那只）
3. force_full 参数级控制已足够灵活

#### 5.3 否决 last_full_refresh 字段

**否决**在 data_status 表增加 `last_full_refresh` 字段。

理由：
1. data_status 表已记录每次采集的 `fetched_at` 和 `message`
2. force_full 触发时在 message 中标记 `'[FULL_REFRESH]'` 前缀即可追溯
3. 增加 DB schema 字段需要 ALTER TABLE + 迁移，成本高于收益
4. 查询"上次全量刷新时间"的需求极低，非高频操作

**替代方案**：force_full 触发的采集调用 `save_data_status` 时，message 字段统一加 `[FULL_REFRESH]` 前缀：

```python
save_data_status(stock_id, 'kline', 'success', '[FULL_REFRESH] 成功获取250条K线数据')
```

---

## 二、K 线复权因子安全性评估

### 2.1 风险定义

前复权（qfq）数据以最新交易日为基准，当新增交易日且发生除权除息事件时，所有历史价格整体偏移。如果 DB 中存储的是旧基准的历史价格，而新增数据是新基准的，技术指标计算（MA、MACD、RSI、布林带等）在拼接处会出现断裂。

### 2.2 风险评估

| 方案 | 复权因子一致性风险 | 严重程度 |
|---|---|---|
| PM 方案（from 增量+7 天全量刷新） | 有：7 天窗口内除权除息日导致拼接断裂 | 🟡 中风险 |
| 架构师方案（同日跳过+全量覆盖） | 无：每次采集都全量 250 条 INSERT OR REPLACE | 🟢 无风险 |

### 2.3 安全保障机制

架构师方案中，当 `last_date < 最近交易日` 时触发全量采集：
- 腾讯接口返回的 250 条全部是最新复权基准的 qfq 数据
- `fetch_kline` 现有逻辑（L419-440）对 250 条逐条 INSERT OR REPLACE
- INSERT OR REPLACE 覆盖所有已有行，确保 DB 中所有数据都是最新复权基准
- **复权因子一致性由"全量获取+幂等覆盖"天然保证**

### 2.4 结论

**架构师方案在复权因子安全性方面无风险。PM 的 from 参数方案存在不可控的中等风险。**

---

## 三、缓存策略一致性评估

### 3.1 各维度 TTL 汇总

| 维度 | TTL | 门控依据 | 评分权重(A股) | 影响评估 |
|---|---|---|---|---|
| **K 线** | 同日（last_date ≥ 今日 → 跳过） | raw_kline.MAX(trade_date) | 0.30 | 无影响：当日数据不变 |
| **基本面-财报** | 80 天 | raw_fundamental.MAX(report_date) | 0.30 | 无影响：季度级数据 |
| **基本面-PE/PB** | 24 小时 | data_status.fetched_at | 0.30(含在基本面) | 极低：1 日内估值变化微小 |
| **资金面** | 同日（已有，不改） | raw_capital_flow + data_status | 0.40 | 无影响：已有完善机制 |
| **北向资金** | 30 天 | data_status.fetched_at | 0.10(B26降权) | 极低：停更数据+降权 |
| **融资余额** | 增量补取（有数据→只补近期） | raw_capital_flow.MAX(trade_date) WHERE margin IS NOT NULL | 0.10(含在资金面) | 极低：增量补取保证数据连续 |
| **消息面** | 同日 | news_sentiment(stock_id, 今日) | 0.00 | 无影响：权重为 0 |

### 3.2 一致性结论

- 各维度 TTL **互不冲突**，各自服务于不同数据类型的更新频率特性
- 实时性要求高的数据（PE/PB 24h）和实时性要求低的数据（财报 80 天）正确分离
- 资金面已有完善的同日跳过机制（L1464-1505 双层 gate），不需要改动
- **无 TTL 冲突风险**

---

## 四、数据完整性风险评估

### 4.1 增量跳过是否可能导致评分引擎读不到数据

| 场景 | 评分引擎数据来源 | 增量跳过后影响 |
|---|---|---|
| K 线跳过 | raw_kline 表已有数据 | ✅ 无影响：评分引擎从 DB 读取，跳过只是不重新写入 |
| 财报跳过 | raw_fundamental 表已有数据 | ✅ 无影响：季度级数据在 80 天内不变 |
| PE/PB 跳过 | raw_fundamental 最新行的 pe_ratio/pb_ratio | ✅ 无影响：24h 内估值变化极小 |
| 北向资金跳过 | raw_capital_flow.north_holding_change | ✅ 极低影响：停更数据+B26 降权 0.10 |
| 融资余额跳过 | raw_capital_flow.margin_balance | ✅ 无影响：增量补取保证数据连续性 |
| 消息面跳过 | news_sentiment 表已有数据 | ✅ 无影响：权重 0% |

### 4.2 首次分析保护

所有增量逻辑都有"无数据 → 全量采集"的兜底分支，确保首次分析时不会因为空 DB 而跳过：

| 维度 | 兜底条件 | 行为 |
|---|---|---|
| K 线 | raw_kline 无该 stock_id 记录 | 全量采集 |
| 财报 | raw_fundamental 无该 stock_id 记录 | 全量采集 |
| PE/PB | 仅当财报门控通过时检查 | 随财报一起采集 |
| 北向资金 | data_status 无 north_capital 记录 | 正常请求 |
| 融资余额 | raw_capital_flow 无 margin_balance 记录 | 保持现有全量回填 |
| 消息面 | news_sentiment 无该 stock_id 当日记录 | 正常采集 |

### 4.3 结论

**增量跳过不会导致评分引擎读不到数据。所有维度都有首次分析兜底和数据连续性保障。**

---

## 五、改动范围确认

### 5.1 必须修改的文件

| 文件 | 改动内容 | 改动量预估 |
|---|---|---|
| `modules/data_collector.py` | fetch_kline 增加同日跳过；fetch_a_fundamental 增加 80 天/24h 双门控；fetch_hk_fundamental 增加 80 天门控；fetch_north_capital 增加 30 天缓存检查；fetch_margin_balance 增加增量补取；fetch_sentiment 增加当日检查；collect_stock_data 增加 force_full 参数透传 | ~150 行 |
| `modules/news_collector.py` | 无需改动（当日检查在 fetch_sentiment 中实现） | 0 行 |
| `config.py` | 新增 `NORTH_CAPITAL_CACHE_DAYS = 30` | ~3 行 |
| `app.py` | 新增 `/api/stocks/<id>/refresh-full` 路由 | ~30 行 |

### 5.2 不需要修改的文件

| 文件 | 理由 |
|---|---|
| `database/db_manager.py` | 不需要修改表结构（不增加 last_full_refresh 字段） |
| `modules/scoring_engine.py` | 红线，不触碰 |
| `modules/advisor.py` | 红线，不触碰 |
| `config_weights.json` | 红线，不触碰 |
| `modules/data_adapter.py` | 数据读取层不变，从 DB 读取逻辑不变 |

### 5.3 可选改动（PM 决定）

| 文件 | 改动内容 | 建议 |
|---|---|---|
| `modules/data_collector.py` | save_data_status 改为 INSERT OR REPLACE + data_status 加唯一约束 | 可选项，减少 data_status 膨胀 |

---

## 六、开发任务书建议

### 6.1 子任务拆分

建议将 011 拆分为 5 个独立子任务，可部分并行：

| 子任务 | 内容 | 依赖 | 并行度 |
|---|---|---|---|
| **011-A** | K 线同日跳过 + collect_stock_data force_full 参数框架 | 无 | ✅ 可独立开发 |
| **011-B** | 基本面增量（A 股+港股）双门控 | 无 | ✅ 可独立开发 |
| **011-C** | 消息面当日跳过 + 北向资金 30 天缓存 + 融资余额增量 | 无 | ✅ 可独立开发 |
| **011-D** | app.py 新增 /refresh-full API 端点 | 依赖 011-A 的 force_full 参数 | ⏳ 依赖 011-A |
| **011-E** | config.py 新增配置项 + 集成自验 | 依赖 011-B/C | ⏳ 依赖 011-B/C |

### 6.2 依赖关系图

```
011-A ──────────┐
                ├──→ 011-D (API 端点)
011-B ──┐       │
        ├──→ 011-E (配置+集成)
011-C ──┘
```

### 6.3 并行开发建议

- **第一批并行**：011-A + 011-B + 011-C（三个子任务互不依赖）
- **第二批**：011-D（依赖 011-A）、011-E（依赖 011-B + 011-C）
- 如果只有 1 个开发者，按 011-A → 011-B → 011-C → 011-D → 011-E 顺序执行

### 6.4 预期效果

| 优化项 | 当前 | 优化后 | 改善 |
|---|---|---|---|
| 同日重复分析 K 线请求 | 每次全量 250 天 | 当日跳过，0 请求 | -100% |
| 同日重复分析基本面请求 | 每次全量 2020 年起财报 | 80 天内跳过 | -100% |
| PE/PB 重复请求 | 每次腾讯接口 | 24h 内跳过 | -95% |
| 北向资金无效请求 | 每次 akshare 请求 | 30 天 1 次 | -97% |
| 融资余额 API 调用 | 每次 112 个日期 | 仅补近期 1-5 个 | -95% |
| 消息面重复采集 | 每次全量新闻+情绪分析 | 当日跳过 | -100% |

---

## 七、裁定总结

| 决策点 | PM 方案 | 架构师裁定 | 关键理由 |
|---|---|---|---|
| **DP-1** K 线 | from 参数增量+7 天全量 | **修改**：同日跳过+全量覆盖 | 复权因子一致性风险；50KB 数据量不值得增量 |
| **DP-2** 基本面 | 80 天财报+24h PE/PB | **采纳** | 符合财报周期；双门控正确分离 |
| **DP-3** 消息面 | 增量保留（当日跳过） | **采纳** | 保留未来恢复可能；成本极低 |
| **DP-4** 北向资金 | 7 天缓存 | **修改**：30 天缓存+config 配置项 | 停更 2 年，7 天仍频繁；30 天足够检测恢复 |
| **DP-5** 全量刷新 | 配置开关+API 入口 | **修改**：仅 API 入口+force_full 参数 | 全局开关粒度过粗；message 标记替代新字段 |

### 总体评审结论

**PM 增量优化方案整体方向正确**，5 个维度均发现了合理的优化空间。架构师在 DP-1（复权因子安全）和 DP-4（缓存周期）上提出修改建议，其余采纳。方案可安全实施，不触碰任何红线，不引入新依赖。

**建议 PM 据此裁定结果签发 011 开发任务书，按 6.1 子任务拆分并行开发。**

---

> **架构师签字**：2026-07-30
> **状态**：评审完成，待 PM 签发开发任务书
