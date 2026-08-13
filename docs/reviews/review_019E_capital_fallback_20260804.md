# 架构评审报告 — 019E 资金面批量补采正向触发 + 估算兜底展示与 EM 覆盖重写

**评审人**：架构师
**评审日期**：2026-08-04
**任务书版本**：v1 草案（`docs/tasks/dev_tasks_20260804_019E_capital_fallback.md`）
**评审结论**：**有条件通过**（需按本报告修订点修订后定稿）

---

## 〇、评审范围与依据

本评审基于以下代码快照（行号为评审时实际行号）：

| 文件 | 关键区域 | 说明 |
|---|---|---|
| `modules/data_collector.py` | L1095-1113 EM 常量、L1120-1212 THS 批量源、L1214-1424 `fetch_capital_flow_batch`、L1795-2150 `fetch_capital_flow`（含三处 `if False` 死代码 L2028/L2069/L2104） | 采集主链路 |
| `modules/data_adapter.py` | L273-287 `_read_capital_data()`、L324-482 `load_stockdata_from_db()` | **评分数据加载入口** |
| `modules/scoring_engine.py` | L766-772 `score_main_capital()` | 评分计算（读 StockData 内存对象，**不直接查 DB**） |
| `modules/advisor.py` | L1111-1156 `_build_capital_factors()` | **评分子项因子构建入口** |
| `app.py` | L768-796 `/api/stocks/<id>/capital` 展示接口、L3949 `init_database()` 启动调用 | 展示接口 + DB 初始化 |
| `database/db_manager.py` | L238-253 `raw_capital_flow` 建表、L940-967 `_safe_add_columns` 迁移列表 | Schema 定义与迁移 |
| `templates/index.html` | L2477-2490 资金面表格、L2541-2542 采集状态 status 映射 | 前端展示 |
| `modules/daily_report.py` | L479-480 调用 `fetch_capital_flow_batch` | 日报入口（间接，无直接 DB 读取） |

---

## 一、逐决策点裁定

### E-1：估算数据标记方案

**裁定：采纳 (a)，新增 `is_estimated INTEGER NOT NULL DEFAULT 0`（ALTER TABLE）**

**理由**：

1. **存量数据兼容**：`DEFAULT 0` 保证所有存量行自动标记为真实数据，零迁移风险。`NOT NULL` 约束在 SQLite ALTER TABLE ADD COLUMN 中要求必须提供 DEFAULT 值（已满足 `DEFAULT 0`），语法合法。

2. **优于文本标记方案 (b)**：`data_source` 文本字段（如 `'estimated_sina'`）存在拼写不一致风险（开发可能写 `'estimate'`/`'est'`/`'估算'`），布尔整数过滤最简洁且不容错：`WHERE is_estimated = 0`。

3. **迁移机制已有先例**：`db_manager.py` L960-961 已用 `_safe_add_columns` 为 018 添加 `ths_net_inflow`，同一列表追加 `('raw_capital_flow', 'is_estimated', 'INTEGER NOT NULL DEFAULT 0')` 即可，模式幂等（try-except 跳过已存在列）。

4. **读取侧过滤复杂度低**：`is_estimated = 0` 单条件 WHERE 子句即可过滤，无需 LIKE/正则匹配。

**附加约束（必须在任务书中明确）**：

- 迁移列表位置：`db_manager.py` L961 `('raw_capital_flow', 'ths_net_inflow', 'REAL')` 之后追加，**不可遗漏**。
- 估算写入的 INSERT 语句**必须携带 `is_estimated=1`**，否则标记形同虚设。

---

### E-2：补采正向触发的插入位置与去重

**裁定：采纳任务书方案，补充以下三点裁定**

#### ① THS 失败路径与补采路径的执行关系

**天然去重有效，无需显式去重**。

分析执行流：
- THS 批量失败（`df is None`）→ 进入 L1246-1336 的 019C EM 回退循环
- 回退循环中 `fetch_capital_flow(sym, 'a_stock')` 成功 → `main_net_inflow` 已写入 NOT NULL
- 回退循环中失败/软超时/熔断终止 → `main_net_inflow` 仍为 NULL
- 补采清单查询 `WHERE main_net_inflow IS NULL OR is_estimated = 1` → 只命中回退失败的股票

结论：回退循环已成功的股票天然不进补采清单（NULL 判定过滤），**无需显式去重**。补采实际上是对回退循环中因熔断/软超时被跳过的股票的**第二机会**，行为正确。

但需注意：补采循环必须**复用同一组 `_EM_*` 模块级常量和计数器**（`_EM_CONSECUTIVE_FAIL_COUNT` 等）。如果回退循环已触发熔断（`_EM_CONSECUTIVE_FAIL_COUNT >= _EM_CIRCUIT_BREAK_N`），补采循环第一只就会熔断退出——这是**预期行为**（EM 接口确实不可用），不应绕过。

#### ② 补采清单查询条件

**合并为一条 SQL**：

```sql
SELECT stock_id FROM raw_capital_flow
WHERE trade_date = ?
  AND (main_net_inflow IS NULL OR is_estimated = 1)
```

- `main_net_inflow IS NULL`：覆盖 THS 成功时写入的占位行（仅有 `ths_net_inflow`）
- `is_estimated = 1`：覆盖估算兜底写入行（EM 恢复后需重写）
- 两个条件合并为一条 SQL，无需分两次查询

**补采清单还需覆盖"当日无行"的股票**：THS 批量失败时不会写入占位行，EM 回退循环失败的股票可能完全没有行。补采循环应基于**输入的 `a_stock_symbols` 列表**与查询结果取并集：

```python
# 补采清单 = 输入列表中去掉已有真实数据的
already_real = set()  # 查询 main_net_inflow IS NOT NULL AND is_estimated = 0 的 stock_id
补采清单 = [sym for sym in a_stock_symbols if get_stock_id(sym, 'a_stock') not in already_real]
```

#### ③ 对 app.py batch-analyze 路径的兼容性

**可接受**。`fetch_capital_flow_batch` 被 `daily_report.py` L479 和 `app.py` batch-analyze 共用（019C 评审 R-5 已确认）。补采机制对两条路径统一生效是正确行为——确保数据完整性不应因入口不同而异。

但需在补采循环入口加一条 INFO 日志标注触发来源（日报 vs batch-analyze），便于 QA 日志核查。

---

### E-3：评分纯净隔离的读取入口盘点（全量）

**裁定：任务书 2.5 指向的 `scoring_engine.py` 是错误的过滤位置。实际过滤必须在 `data_adapter.py` 和 `advisor.py` 的 DB 查询层。**

#### 全量读取入口清单

| # | 文件 | 函数/位置 | SQL | 用途 | 是否过滤 `is_estimated=1` |
|---|---|---|---|---|---|
| R-1 | `data_adapter.py` L273-287 | `_read_capital_data()` | `SELECT * FROM raw_capital_flow WHERE stock_id=? ORDER BY trade_date DESC LIMIT ?` | **评分主输入**：`latest_cap.get('main_net_inflow')` → StockData → `score_main_capital()` | **必须过滤** |
| R-2 | `advisor.py` L1122-1126 | `_build_capital_factors()` | `SELECT trade_date, main_net_inflow, ... FROM raw_capital_flow WHERE stock_id=? ORDER BY trade_date DESC LIMIT 5` | **评分子项因子**：main_trend/main_pct/super_large/main_avg_5d/consecutive → 影响评级文本与风险提示 | **必须过滤** |
| R-3 | `app.py` L768-774 | `/api/stocks/<id>/capital` | `SELECT * FROM raw_capital_flow WHERE stock_id=? ORDER BY trade_date DESC LIMIT 10` | **展示**：报告详情弹窗资金面表格 | **允许读取估算行**（需标注） |
| R-4 | `data_collector.py` L2159 | `fetch_capital_flow` 内部校验 | `SELECT COUNT(*) ... FROM raw_capital_flow WHERE stock_id=?` | 采集后行数校验（非评分） | 不影响评分，不需过滤 |
| R-5 | `data_collector.py` L1822-1826 | `fetch_capital_flow` 前置跳过校验 | `SELECT COUNT(*) ... WHERE main_net_inflow IS NOT NULL` | **防覆盖机制**（见 E-7③） | **必须修改判定条件** |
| — | `daily_report.py` L479 | 调用 `fetch_capital_flow_batch` | 间接（不直接查 raw_capital_flow） | 日报批次入口 | 不需过滤（依赖 R-1 过滤） |
| — | `scoring_engine.py` L766-772 | `score_main_capital(data)` | 读 `data.main_net_inflow`（StockData 内存对象） | 评分计算 | **不需修改**（过滤在上游 R-1 完成） |

#### 关键发现：任务书范围遗漏

任务书第五节红线 6 限定的改动范围：
> 改动限于 `modules/data_collector.py`（必改）、`modules/scoring_engine.py`（读取过滤，若评审确认必要）、`templates/index.html`（估算标注）

**实际过滤点在 `data_adapter.py` 和 `advisor.py`，不在 `scoring_engine.py`**。`scoring_engine.py` 的 `score_main_capital()` 读取的是 `StockData.main_net_inflow` 内存属性，不直接查询数据库。如果仅在 scoring_engine 过滤，估算值已经通过 data_adapter 的 `SELECT *` 进入 StockData 对象，为时已晚。

**必须修订范围**：增加 `modules/data_adapter.py`（R-1 过滤）和 `modules/advisor.py`（R-2 过滤），`scoring_engine.py` 无需改动。

#### 两类入口边界清单

| 类型 | 入口 | 过滤规则 |
|---|---|---|
| **评分类（必须过滤）** | R-1 `data_adapter._read_capital_data()`、R-2 `advisor._build_capital_factors()` | SQL 增加 `AND (is_estimated = 0 OR is_estimated IS NULL)` 条件 |
| **展示类（允许读取）** | R-3 `app.py /api/stocks/<id>/capital` | 不过滤，前端按 `is_estimated` 字段标注"估算"（见 E-6） |

---

### E-4：EM 恢复后是否 force 重生成当日日报

**裁定：(a) 不自动重生成（采纳 PM 倾向）**

**理由**：

1. **评分口径一致性**：当日日报已用 T-1（或更早）真实数据评分，属可接受口径。估算行在 R-1/R-2 已被过滤，评分不受估算数据影响——即使日报不重生成，评分结果与"无估算行"场景完全一致。

2. **避免 B11-REPORT-REUSE 语义变更**：自动 force 重生成涉及日报复用判定逻辑修改（B11 任务定义的 "同日已生成则跳过" 规则），影响面超出本批次范围。

3. **用户体验**：零代码用户可能在非交易时间查看报告，自动重生成会引发不可预期的延迟。用户可通过"重新分析"按钮手动触发。

4. **数据一致性自然恢复**：下一个日报批次（T+1）会自然使用最新真实数据，无需干预。

**但需补充一条**：在日报日志中记录"本批次 N 只股票资金面仍为估算数据，待 EM 恢复后自动覆盖"，供用户知晓。

---

### E-5：data_status 新增 'estimated' 状态的兼容性

**裁定：采纳新增 'estimated' 状态，但需适配 3 处消费方**

#### 现有 status 消费方逐项核查

| # | 位置 | 当前逻辑 | 'estimated' 兼容性 | 需适配 |
|---|---|---|---|---|
| C-1 | `data_collector.py` L1313 | `result[0] == 'success'` 判定 EM 回退成功 | `fetch_capital_flow` 返回 `'estimated'` ≠ `'success'` → 计为失败 → **正确**（估算不应重置连续失败计数） | ❌ 不需改 |
| C-2 | `data_collector.py` L1849-1856 | `skip_row['message'].startswith('东方财富')` 防覆盖跳过 | estimated 行 message 为 `'估算兜底...'`，不以"东方财富"开头 → **不跳过** → **正确**（EM 恢复后允许重写） | ❌ 不需改 |
| C-3 | `data_collector.py` L1822-1826 | `main_net_inflow IS NOT NULL` 前置跳过 | estimated 行 `main_net_inflow` NOT NULL → **会误跳过！** | ✅ **必须改**（见 E-7③） |
| C-4 | 前端 `index.html` L2541-2542 | 三元链：`success → ✅成功` / `partial → ⚠️部分` / `else → ❌失败` | `'estimated'` 落入 else → 显示"❌失败" → **语义错误** | ✅ **必须改** |
| C-5 | 前端 `index.html` L2067-2072 | 同上三元链（采集结果摘要） | 同 C-4 | ✅ **必须改** |

#### 需适配的修改点

**C-3（`data_collector.py` L1822-1826）**：任务书 2.4 已正确指出，前置校验 SQL 改为：
```sql
SELECT COUNT(*) AS cnt FROM raw_capital_flow
WHERE stock_id = ? AND trade_date = ?
  AND main_net_inflow IS NOT NULL
  AND (is_estimated = 0 OR is_estimated IS NULL)
```

**C-4/C-5（前端 `index.html` L2541-2542 和 L2067-2072）**：两处三元链需增加 `estimated` 分支：
```javascript
// L2541-2542 修改为：
const sc = d.status === 'success' ? 'status-success'
         : d.status === 'estimated' ? 'status-partial'
         : d.status === 'partial' ? 'status-partial'
         : 'status-failed';
const st = d.status === 'success' ? '✅成功'
         : d.status === 'estimated' ? '⚠️估算'
         : d.status === 'partial' ? '⚠️部分'
         : '❌失败';
```

复用现有 `status-partial` CSS 类（L127，橙色 `#e65100`），语义"估算"与"部分"同属降级态，颜色一致合理。

---

### E-6：前端"估算"标注范围

**裁定：仅标注资金面数据表格中的估算行，最小改动**

#### 标注展示点

**唯一标注点**：`templates/index.html` L2477-2490 的资金面数据表格。

当前代码（L2483-2484）：
```javascript
const color = d.main_net_inflow > 0 ? '#e74c3c' : '#27ae60';
html += `<tr><td>${d.trade_date}</td><td style="color:${color}">${d.main_net_inflow ?? '—'}</td>...`;
```

修改为：对 `d.is_estimated === 1` 的行，在 `main_net_inflow` 值后追加 `<sup style="color:#e67e22;font-size:11px">估算</sup>` 标记：

```javascript
const estTag = d.is_estimated === 1 ? '<sup style="color:#e67e22;font-size:11px">估算</sup>' : '';
html += `<tr><td>${d.trade_date}</td><td style="color:${color}">${d.main_net_inflow ?? '—'}${estTag}</td>...`;
```

同时修改表头说明文案（L2478），将固定的"来源：东方财富"改为动态：
```javascript
const hasEstimated = capital.data.some(d => d.is_estimated === 1);
const sourceLabel = hasEstimated ? '来源：东方财富（含估算兜底数据）' : '来源：东方财富';
```

#### 不标注的位置

- **评分卡片**（L2149-2151 `_renderDimensionCard`）：评分已过滤估算值，卡片显示的是真实数据得分，无需标注。
- **价格建议资金面色块**（L4300-4303）：同上，基于过滤后数据，无需标注。

**理由**：标注仅在原始数据展示层有意义，评分层已纯净隔离，标注反而引起用户困惑。

---

### E-7：范围与红线确认

#### ① 估算源死代码复活后的异常处理

**当前状态**：三处 `if False` 块内已有 `try-except`，网络失败仅 append warnings 列表，不抛异常。

**裁定：可接受，但需补充约束**

- 估算源 `_fetch_capital_flow_sina/_tencent/_netease` 三函数本身已有异常捕获（返回 None），复活后网络失败不阻塞主流程——**机制已具备**。
- **但必须注意**：当前 `fetch_capital_flow` L2016-2024 有一个**提前 return**（`if saved_count == 0: return 'failed'`），使得三处 `if False` 块即使在解除 `if False` 后仍**不可达**（L2026 注释已标注"已不可达"）。

**关键修改要求**：开发必须将 L2016-2024 的提前 return 改为**不 return 的标志位**（如 `em_all_failed = True`），然后继续执行估算降级链路。具体：

```python
# 修改前（L2016-2024）：
if saved_count == 0:
    fail_msg = '...'
    save_data_status(stock_id, 'capital', 'failed', fail_msg)
    return 'failed', fail_msg   # ← 提前退出，估算代码不可达

# 修改后：
em_all_failed = (saved_count == 0)
if em_all_failed:
    logger.warning(f'[{symbol}] 东方财富三层全失败，尝试估算兜底...')
# 不 return，继续执行估算降级
```

估算全部成功后返回 `('estimated', msg)`；估算也全部失败时，在 L2138 `if saved_count == 0` 处返回 `('failed', fail_msg)`。

#### ② ALTER TABLE 执行时机

**裁定：已满足零代码约束，自动迁移**

- `db_manager.py` 的 `_safe_add_columns`（L940-967）在 `init_database()` 中被调用
- `app.py` L3949 在 `main()` 中调用 `init_database()`（即 `python app.py` 启动时自动执行）
- 迁移列表 L961 追加 `('raw_capital_flow', 'is_estimated', 'INTEGER NOT NULL DEFAULT 0')` 后，**零代码用户下次启动即自动迁移**
- try-except 模式幂等，已有列不会报错

**无需手工执行 ALTER TABLE**。红线 4"ALTER TABLE 仅新增列且带默认值"——已满足。

#### ③ 估算只写当日 1 行与 INSERT OR REPLACE 的语义冲突

**裁定：存在冲突，估算写入必须改用 UPDATE + INSERT OR IGNORE 模式**

**冲突分析**：

`raw_capital_flow` 表有 `UNIQUE(stock_id, trade_date)` 约束。当前估算代码（死代码 L2047/L2084/L2119）使用：
```sql
INSERT OR REPLACE INTO raw_capital_flow
(stock_id, trade_date, main_net_inflow, main_net_inflow_pct,
 super_large_net, large_net, medium_net, small_net)
VALUES (?, ?, ?, ?, ?, ?, ?, ?)
```

**问题**：INSERT OR REPLACE 在 SQLite 中实际是 DELETE + INSERT，会**清除该行所有其他列的值**。如果 THS 批量已写入 `ths_net_inflow`（占位行），估算 INSERT OR REPLACE 会将 `ths_net_inflow` 置为 NULL——**数据丢失**。

**修改要求**：估算写入改用与 `north_holding_change`（L2298-2314）和 `margin_balance`（L2378+）相同的 UPDATE + INSERT OR IGNORE 模式：

```python
# 先 UPDATE（已有行 → 补字段，不破坏 ths_net_inflow 等）
cursor.execute("""
    UPDATE raw_capital_flow
    SET main_net_inflow = ?, main_net_inflow_pct = ?, is_estimated = 1
    WHERE stock_id = ? AND trade_date = ?
""", (main_net, main_net_pct, stock_id, today_str))
if cursor.rowcount == 0:
    # 无行 → INSERT（is_estimated = 1）
    cursor.execute("""
        INSERT OR IGNORE INTO raw_capital_flow
        (stock_id, trade_date, main_net_inflow, main_net_inflow_pct, is_estimated)
        VALUES (?, ?, ?, ?, 1)
    """, (stock_id, today_str, main_net, main_net_pct))
```

**附加约束**：
- 估算写入**只写当日 1 行**（trade_date = today_str），不写历史多日——任务书 2.2 已约束，评审确认。
- EM 恢复后覆盖重写时（任务 3），INSERT OR REPLACE 可以正常使用（EM 写入完整 8 字段 + `is_estimated=0`），因为此时目的是覆盖估算行。但为安全起见，EM 写入也应携带 `is_estimated=0` 以确保标记归位。

#### ④-⑥ 其他红线确认

| 红线# | 内容 | 评审结论 |
|---|---|---|
| 1 | 评分纯净红线 | ✅ 完备。R-1/R-2 过滤点已明确，验收标准 5 的全仓 grep 要求正确 |
| 2 | 签名红线 | ✅ 完备。`fetch_capital_flow`/`generate_advice()`/`fetch_capital_flow_batch` 签名不变 |
| 3 | 主链路红线 | ✅ 完备。EM 三层降级结构不变，估算仅在三层全失败后兜底 |
| 4 | 数据安全 | ✅ 完备。ALTER TABLE 仅新增列 + DEFAULT 0 |
| 5 | 零代码约束 | ✅ 完备。无新 pip 依赖 |
| 6 | 范围约束 | ❌ **必须修订**（见下方修订点 M-3） |

---

## 二、新发现的风险项

### R-1：估算源复活需拆除 L2016 提前 return（高优先级）

**风险描述**：`fetch_capital_flow` L2016-2024 有 `if saved_count == 0: return 'failed'` 提前退出，使得后续三处 `if False` 估算代码即使解除 `if False` 仍**不可达**。开发如果只删除 `if False` 而未处理提前 return，估算兜底将静默失效（不报错但永远不执行）。

**影响**：估算兜底功能完全失效，与任务目标矛盾。

**对策**：任务书须明确要求将提前 return 改为标志位，并在验收标准中增加"构造 EM 全失败场景 → 验证估算代码实际执行（日志含 `[估算兜底]` 字样 + DB 中 is_estimated=1 行存在）"。

### R-2：EM 写入未携带 is_estimated=0 标记（中优先级）

**风险描述**：当前 EM 三层成功的 INSERT OR REPLACE 语句（L1881/L1932/L1981）不包含 `is_estimated` 字段。INSERT OR REPLACE 后 `is_estimated` 将取 DEFAULT 0——**对新增列是正确的**。但如果某行曾被估算写入（`is_estimated=1`），EM 恢复后用 INSERT OR REPLACE 覆盖时，因未显式设置 `is_estimated=0`，虽然 DEFAULT 0 会生效（INSERT OR REPLACE = DELETE + INSERT），**但存在隐性依赖**：依赖 DEFAULT 定义不被未来误改。

**影响**：低概率，但为防御性编程应显式写入。

**对策**：任务书增加要求——EM 三层 INSERT OR REPLACE 语句显式包含 `is_estimated` 字段并赋值 0。

### R-3：advisor._build_capital_factors 读取 LIMIT 5 可能全被估算行占据（中优先级）

**风险描述**：`advisor.py` L1122-1126 查询 `LIMIT 5` 行。如果某股票近期多日 EM 失败且估算源连续多日写入（虽然任务书约束"估算只写当日 1 行"，但跨日累积可能有多行估算），过滤 `is_estimated=0` 后返回行数可能不足 5 行甚至为 0 行。

**影响**：因子计算数据不足，`main_avg_5d`/`consecutive` 等因子降级。

**对策**：可接受——与当前 EM 失败时 `main_net_inflow = None` → NEUTRAL_INFLOW 填充的行为一致（评分降级而非错误）。不需额外处理，但需在验收标准中记录此预期行为。

### R-4：补采循环与 019C 回退循环的熔断计数器共享（低优先级）

**风险描述**：补采循环复用 019C 的 `_EM_CONSECUTIVE_FAIL_COUNT` 模块级计数器。如果 THS 失败 → 回退循环熔断退出（计数器 ≥ 5）→ 补采循环立即熔断（第一只就退出）。

**影响**：补采循环在 EM 接口持续不可用时无效（符合预期）。但如果 EM 是间歇性恢复（回退循环熔断后恢复），补采循环不会重置计数器。

**对策**：可接受——补采是"额外机会"而非"保证成功"。如果需要更积极的重试，可在补采循环开始时将 `_EM_CONSECUTIVE_FAIL_COUNT` 减半（非清零，保留风控记忆）。**本批次不建议引入此优化，保持简单**。

### R-5：前端资金面表格表头"来源：东方财富"硬编码（低优先级）

**风险描述**：`index.html` L2478 表头固定文案 `来源：东方财富`。当存在估算行时，文案不准确。

**影响**：用户看到"来源：东方财富"但数据实际来自新浪/腾讯估算，产生困惑。

**对策**：已在 E-6 裁定中给出动态文案方案。

---

## 三、对任务书的具体修订点清单

| 编号 | 位置 | 修订内容 | 原因 |
|---|---|---|---|
| **M-1** | 任务书 2.5 | 将"scoring_engine.py 读取资金面处必须过滤"改为"data_adapter.py `_read_capital_data()` SQL 增加 `AND (is_estimated = 0 OR is_estimated IS NULL)` 条件" | scoring_engine 读内存对象不查 DB，过滤点在 data_adapter（E-3） |
| **M-2** | 任务书 2.5 | 新增"advisor.py `_build_capital_factors()` SQL 同步增加 `AND (is_estimated = 0 OR is_estimated IS NULL)` 条件" | advisor 直接查 DB 构建因子，影响评级（E-3） |
| **M-3** | 任务书红线 6 | 改动范围增加 `modules/data_adapter.py`（必改）、`modules/advisor.py`（必改）；`modules/scoring_engine.py` 从范围中移除（无需改动） | 实际过滤点位置修正（E-3） |
| **M-4** | 任务书任务 2 | 新增约束："开发必须将 `fetch_capital_flow` L2016-2024 的 `return 'failed'` 提前退出改为标志位（如 `em_all_failed = True`），否则估算代码不可达" | 提前 return 阻断估算降级链路（R-1） |
| **M-5** | 任务书任务 2 | 新增约束："估算写入必须使用 UPDATE + INSERT OR IGNORE 模式（参考 north_holding_change L2298-2314 写法），禁止使用 INSERT OR REPLACE（会清除 ths_net_inflow 等已有字段）" | UNIQUE 约束 + INSERT OR REPLACE = DELETE+INSERT 数据丢失（E-7③） |
| **M-6** | 任务书任务 2 | 新增约束："估算成功时 `fetch_capital_flow` 返回值改为 `('estimated', msg)` 而非 `('success', msg)`，确保 019C 回退循环不将估算计为成功" | 防止估算误重置连续失败计数（E-5 C-1） |
| **M-7** | 任务书任务 3 | 新增约束："EM 三层 INSERT OR REPLACE 语句显式包含 `is_estimated` 字段并赋值 0" | 防御性编程，确保估算→真实覆盖时标记归位（R-2） |
| **M-8** | 任务书任务 2.6 / 红线 6 | 前端改动范围明确为两处：(1) `index.html` L2477-2490 资金面表格行内估算标注 + 表头动态文案；(2) `index.html` L2541-2542 和 L2067-2072 采集状态 status 映射增加 `estimated` 分支 | E-5 C-4/C-5 + E-6 标注 |
| **M-9** | 任务书验收标准 | 增加验收点："构造 EM 全失败 → 估算兜底成功 → 验证 `data_adapter._read_capital_data()` 返回的 main_net_inflow 为 T-1 真实值（非估算值），通过断言 `is_estimated=0` 行的 trade_date ≠ today 确认" | 评分纯净隔离验证（E-3） |
| **M-10** | 任务书验收标准 | 增加验收点："构造估算行 → 再次触发 EM 采集成功 → 验证 ths_net_inflow 字段未被清除（INSERT OR REPLACE 陷阱检查）" | 数据安全验证（E-7③） |

---

## 四、评审结论

### 结论：**有条件通过**

任务书 v1 草案的总体设计方向正确（补采正向触发 + 估算兜底展示 + EM 覆盖重写），但存在以下**必须在定稿前修订**的问题：

1. **评分过滤位置错误**（M-1/M-2/M-3）：过滤点在 `data_adapter.py` 和 `advisor.py`，非 `scoring_engine.py`。这是本批次**最高风险点**——如果过滤位置错误，估算值将泄漏到评分。
2. **估算代码不可达**（M-4/R-1）：L2016 提前 return 阻断估算降级链路，必须改为标志位。
3. **INSERT OR REPLACE 数据丢失**（M-5/E-7③）：估算写入必须改用 UPDATE + INSERT OR IGNORE 模式。
4. **fetch_capital_flow 返回值**（M-6）：估算成功应返回 `'estimated'`，防止熔断计数误重置。
5. **前端 status 映射**（M-8）：需增加 `estimated` 分支，否则显示"❌失败"。

PM 按以上 M-1~M-10 修订任务书后报监理批准，即可进入开发执行阶段。

---

> **架构师备注**：本批次风险面确实高于 019D，核心矛盾在于"估算数据既要写入 DB 供展示，又要被评分链路完全隔离"。R-1~R-5 风险项建议开发在自验报告中逐项确认。重点提醒开发：**先改过滤点（data_adapter + advisor），再改采集端（data_collector），最后改前端**——确保评分隔离在估算数据写入前就已就位。
