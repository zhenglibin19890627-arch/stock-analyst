# QA-TASKS-20260730-011 验收报告：数据采集全链路增量优化

> **验收人**：QA（独立设计+独立执行） | **验收日期**：2026-07-30 | **状态**：❌ 验收不通过（4 PASS / 3 FAIL）

---

## 一、验收环境

| 项 | 内容 |
|---|---|
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 数据库 | `stock_analyst/stock_analyst.db` |
| 验收方法 | Python 直接调用采集函数 + DB 查询验证 + 源码逻辑审查 |
| 测试股票 | 600276 恒瑞医药(id=4,a_stock)、000333 美的集团(id=11,a_stock)、HK3690 美团-W(id=6,hk_stock) |

### 测试前 DB 状态快照

| 股票 | 最新K线 | 最新财报(days_ago) | 消息面(当日) | fundamental状态 |
|---|---|---|---|---|
| 600276 | 2026-07-30 | 2026-03-31 (121天) | 10条 | 2026-07-30 13:55 (24h内) |
| 000333 | 2026-07-28 | 2026-07-15 (15天) | — | 2026-07-28 12:24 (>24h) |
| HK3690 | 2026-07-30 | 2026-07-22 (8天) | 10条 | 2026-07-30 13:57 |

---

## 二、测试结果汇总表

| 测试项 | 结论 | 说明 |
|---|---|---|
| Q1 K线同日跳过 | ✅ PASS | 两次调用均返回 `('success', '同日跳过(K线已有2026-07-30数据)')` |
| Q2 基本面80天门控 | ❌ FAIL | BUG-1: 时区不匹配 TypeError 导致门控失效，财报始终全量采集 |
| Q3 PE/PB 24h门控 | ❌ FAIL | BUG-1 连锁影响: 80天门控异常导致 PE/PB 门控代码路径不可达 |
| Q4 消息面当日跳过 | ✅ PASS | 返回 `('success', '当日跳过(消息面已有1条记录)')` |
| Q5 force_full全量刷新 | ✅ PASS | force_full=False 跳过 / force_full=True 全量采集，K线+消息面双向验证 |
| Q6 首次分析兜底 | ✅ PASS | 6个增量检查点均有 None 兜底，无数据时不跳过 |
| Q7 data_status去重 | ✅ PASS | 3次调用后仅保留1条最新记录，message='qa_test_3' |

**总计：4 PASS / 3 FAIL → 验收不通过**

---

## 三、逐项测试详情

### Q1：K线同日跳过 — ✅ PASS

**执行方法**：Python 直接调用 `fetch_kline`

**测试对象**：600276 恒瑞医药（DB 已有 2026-07-30 K线数据）

**执行证据**：
```
第1次调用: status=success, msg=同日跳过(K线已有2026-07-30数据), 耗时=0.009s
第2次调用: status=success, msg=同日跳过(K线已有2026-07-30数据), 耗时=0.009s
```

**判定**：第2次调用返回 `success` + 含"同日跳过" → **PASS**

**验证代码位置**：`data_collector.py` L423-440，`if not force_full:` → `if row and row['last_date']:` → `last_date >= today_str` → 跳过。逻辑正确。

---

### Q2：基本面80天财报门控 — ❌ FAIL

**执行方法**：Python 直接调用 `fetch_a_fundamental` + DB 前后对比

**测试对象**：000333 美的集团（report_date=2026-07-15, days_ago=15 < 80 → 预期跳过财报）

**执行证据**：
```
[测前] 最新财报日期: 2026-07-15
[测前] fundamental状态: fetched_at=2026-07-28 12:24:58 (>24h)

[Q2调用结果] status=success, msg=基本面数据采集成功, 耗时=3.091s
[测后] 最新财报日期: 2026-07-15 (未变)
```

**关键日志（证明门控失效）**：
```
WARNING - [A股 000333] 基本面增量检查异常(降级为全量): can't subtract offset-naive and offset-aware datetimes
INFO - [A股 000333] 财报: 2026-03-31, ROE=5.46  ← 财报被重新采集
INFO - [A股 000333] 财报: 2025-12-31, ROE=19.69
INFO - [A股 000333] 财报: 2025-09-30, ROE=17.18
INFO - [A股 000333] 财报: 2025-06-30, ROE=12.04
INFO - 腾讯估值 000333: PE=15.35, PB=3.33  ← PE/PB也被重新采集
```

**根因分析**：

`data_collector.py` L526 的日期计算存在**时区类型不匹配 Bug**：

```python
# L526: BUG 代码
days_since = (datetime.now(_CN_TZ) - datetime.strptime(last_report_date, '%Y-%m-%d')).days
#           ^^^^^^^^^^^^^^^^^^^^^^^^   ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
#           tz-aware (有时区)            tz-naive (无时区)
#           → Python 抛出 TypeError: can't subtract offset-naive and offset-aware datetimes
```

异常被 L545 的 `except Exception as e:` 捕获，打印 warning 后**静默降级为全量采集**。`skip_financial` 永远不会被设为 `True`，80天门控形同虚设。

**影响**：每次分析都重新请求新浪财报接口（3秒级网络开销），增量优化完全失效。

**判定**：财报未被跳过（日志显示重新采集了4条财报） → **FAIL**

---

### Q3：PE/PB 24h门控 — ❌ FAIL

**执行方法**：Python 直接调用 `fetch_a_fundamental`（Q2 执行后立即调用）

**测试对象**：000333 美的集团（Q2 执行后 fundamental 状态更新为 2026-07-30 14:54:50，<24h）

**预期**：两门控都跳过 → `('success', '同日跳过(财报80天TTL内+PE/PB 24h内)')`

**执行证据**：
```
[Q3调用结果] status=success, msg=基本面数据采集成功, 耗时=3.191s
```

**关键日志**：
```
WARNING - [A股 000333] 基本面增量检查异常(降级为全量): can't subtract offset-naive and offset-aware datetimes
INFO - [A股 000333] 财报: 2026-03-31, ROE=5.46  ← 再次全量采集
INFO - 腾讯估值 000333: PE=15.36, PB=3.33        ← PE/PB也重新采集
```

**根因分析**：

Q3 的失败是 BUG-1 的**连锁后果**。PE/PB 24h 门控代码位于 L531-543：

```python
if days_since < FUNDAMENTAL_REPORT_TTL_DAYS:  # L527 ← 因 L526 异常，此行不可达
    skip_financial = True  # L528
    # 门控B：PE/PB TTL（24h）                   # L531
    cursor_chk.execute(...)  # L532-537 ← 不可达
    if hours_since < PE_PB_CACHE_TTL_HOURS:  # L542 ← 不可达
        skip_pepb = True  # L543
```

由于 L526 的 `days_since` 计算在到达 L527 之前就抛出异常，PE/PB 门控的检查代码（L531-543）**永远不可达**。`skip_pepb` 永远为 `False`。

**判定**：PE/PB 未被跳过，消息不含双门控跳过字样 → **FAIL**

---

### Q4：消息面当日跳过 — ✅ PASS

**执行方法**：Python 直接调用 `fetch_sentiment`

**测试对象**：600276 恒瑞医药（DB 已有 2026-07-30 消息面 1 条记录）

**执行证据**：
```
调用结果: status=success, msg=当日跳过(消息面已有1条记录), 耗时=0.009s
```

**判定**：返回 `success` + 含"当日跳过" → **PASS**

**验证代码位置**：`data_collector.py` L2234-2251，使用 `COUNT(*)` + 字符串日期比较（`news_date LIKE today_str%`），逻辑正确。

---

### Q5：force_full 全量刷新 — ✅ PASS

**执行方法**：Python 直接调用，双向对比验证（force_full=False vs True）

**验证策略**：选用增量逻辑正常工作的 K线 和 消息面 进行验证（这两个函数的增量检查不受 BUG-1 影响）。

**K线 force_full 测试**：
```
force_full=False: status=success, msg=同日跳过(K线已有2026-07-30数据)
force_full=True:  status=success, msg=获取251条K线数据, 耗时=0.4s  ← 全量采集
```

**消息面 force_full 测试**：
```
force_full=False: status=success, msg=当日跳过(消息面已有1条记录)
force_full=True:  status=success, msg=A股 600276 消息面采集成功：10条新闻，情绪得分0.94, 耗时=1.9s  ← 全量采集
```

**force_full 参数透传链路验证**（源码级）：

| 调用链 | 代码位置 | 透传 |
|---|---|---|
| collect_stock_data → fetch_kline | L2352 `fetch_kline(symbol, market, force_full=force_full)` | ✅ |
| collect_stock_data → fetch_a_fundamental | L2356 `fetch_a_fundamental(symbol, force_full=force_full)` | ✅ |
| collect_stock_data → fetch_hk_fundamental | L2395 `fetch_hk_fundamental(symbol, force_full=force_full)` | ✅ |
| collect_stock_data → fetch_north_capital | L2404 `fetch_north_capital(symbol, market, force_full=force_full)` | ✅ |
| collect_stock_data → fetch_margin_balance | L2409 `fetch_margin_balance(symbol, market, force_full=force_full)` | ✅ |
| collect_stock_data → fetch_sentiment | L2415 `fetch_sentiment(symbol, market, force_full=force_full)` | ✅ |
| /refresh-full API → collect_stock_data | app.py L795 `collect_stock_data(symbol, market, force_full=True)` | ✅ |

**判定**：force_full=False 时跳过，force_full=True 时全量采集 → **PASS**

---

### Q6：首次分析兜底 — ✅ PASS

**执行方法**：源码逻辑审查（6个增量检查点的 None 兜底）

| 函数 | 代码位置 | 条件 | None 兜底分析 |
|---|---|---|---|
| fetch_kline | L433 | `if row and row['last_date']:` | MAX(trade_date)=None → 不跳过 ✅ |
| fetch_a_fundamental | L524 | `if row and row['last_report']:` | MAX(report_date)=None → skip_financial=False ✅ |
| fetch_hk_fundamental | L872 | `if row and row['last_report']:` | MAX(report_date)=None → 不跳过 ✅ |
| fetch_sentiment | L2247 | `if row and row['cnt'] > 0:` | COUNT(*)=0 → 不跳过 ✅ |
| fetch_north_capital | L1949 | `if row and row['fetched_at']:` | 无 data_status 记录→row=None → 不跳过 ✅ |
| fetch_margin_balance | L2115 | `if chk_row and chk_row['last_margin_date']:` | MAX=None → 走 else 全量159天 ✅ |

**判定**：所有6个检查点均有 None 兜底，首次分析时正确落入全量采集路径 → **PASS**

---

### Q7：data_status 同日去重 — ✅ PASS

**执行方法**：Python 直接调用 `save_data_status` 3次 + DB 验证

**执行证据**：
```
已连续调用 save_data_status 3次 (qa_test_1/qa_test_2/qa_test_3)

[查询结果] 总记录数: 1
[明细]:
  message=qa_test_3, fetched_at=2026-07-30 14:54:36, cnt=1

>>> Q7 结论: PASS
>>> 同维度同日记录数: 1 (预期1)
>>> 最新message: qa_test_3 (预期qa_test_3)
```

**验证代码位置**：`data_collector.py` L264-283，先 DELETE 同维度同日记录再 INSERT（先删后插），逻辑正确。

**清理**：测试后已 DELETE 恢复 DB。

**判定**：同维度同日仅保留1条最新记录 → **PASS**

---

## 四、发现的问题

### BUG-1（Critical）：fetch_a_fundamental 时区不匹配导致80天门控完全失效

| 项 | 内容 |
|---|---|
| **位置** | `data_collector.py` L526 |
| **严重级别** | Critical |
| **影响测试项** | Q2、Q3 |
| **现象** | `datetime.now(_CN_TZ)` (tz-aware) 与 `datetime.strptime(last_report_date, '%Y-%m-%d')` (tz-naive) 相减，抛出 `TypeError: can't subtract offset-naive and offset-aware datetimes` |
| **后果** | 异常被 L545 `except` 静默捕获并降级为全量采集。80天财报 TTL 门控和 24h PE/PB 门控均**形同虚设**，每次分析都重复请求新浪财报接口（3秒级开销） |
| **复现步骤** | 调用 `fetch_a_fundamental('000333')`，查看日志中 WARNING "基本面增量检查异常(降级为全量)" |
| **修复建议** | 方案A（推荐）：`days_since = (datetime.now(_CN_TZ).replace(tzinfo=None) - datetime.strptime(last_report_date, '%Y-%m-%d')).days`（对齐 fetch_north_capital L1951 的正确写法）<br>方案B：`datetime.now()` 去掉时区参数 |

### BUG-2（Critical）：fetch_hk_fundamental 时区不匹配导致港股80天门控失效

| 项 | 内容 |
|---|---|
| **位置** | `data_collector.py` L874 |
| **严重级别** | Critical |
| **现象** | 与 BUG-1 完全相同的代码模式，同样的 TypeError |
| **后果** | 港股80天财报门控完全失效，每次分析重复请求 akshare 港股财务指标接口 |
| **执行证据** | 调用 `fetch_hk_fundamental('HK3690')`（report_date=2026-07-22, 8天 < 80），返回 `('success', '港股基本面数据采集成功')` 而非跳过；日志显示 "基本面增量检查异常(降级为全量)" |
| **修复建议** | 同 BUG-1 |

### BUG-3（Critical）：fetch_margin_balance 时区不匹配导致增量逻辑异常

| 项 | 内容 |
|---|---|
| **位置** | `data_collector.py` L2118 |
| **严重级别** | Critical |
| **现象** | `today = datetime.now(_CN_TZ)` (L2104, tz-aware) 与 `last_margin = datetime.strptime(...)` (L2116, tz-naive) 相减，抛出 TypeError |
| **后果** | 当已有 margin 数据时，L2118 的 `(today - last_margin).days` 抛异常，由于没有独立 try/except，异常传播到 L2215 外层 except，整个函数返回 `('failed', '融资余额采集失败: ...')`。增量"补近期"逻辑完全不可用 |
| **影响范围** | DB 中已有 107 条 margin 数据（600276 等5只股票），BUG 会被实际触发 |
| **修复建议** | L2104 改为 `today = datetime.now(_CN_TZ).replace(tzinfo=None)` 或 L2116 增加时区对齐 |

### 根因总结

三个 BUG 是**同一编码错误的重复出现**：`datetime.now(_CN_TZ)` 返回 tz-aware 对象，而 `datetime.strptime()` 返回 tz-naive 对象，两者不能直接相减。

项目中已有正确范例：`fetch_north_capital` L1951 使用了 `.replace(tzinfo=None)` 正确处理。`fetch_kline`（L435 用 strftime 字符串比较）和 `fetch_sentiment`（L2237 同上）因使用字符串比较而不受影响。

---

## 五、附加发现

| 项 | 说明 |
|---|---|
| K线跳过逻辑 | ✅ 使用字符串比较（`last_date >= today_str`），无时区问题，设计正确 |
| 消息面跳过逻辑 | ✅ 使用 `LIKE today_str%` + COUNT，无时区问题，设计正确 |
| 北向资金缓存 | ✅ 使用 `.replace(tzinfo=None)` 正确处理时区，30天缓存逻辑正常 |
| force_full 透传 | ✅ 所有6个采集函数均正确接收并传递 force_full 参数 |
| /refresh-full API | ✅ app.py L773-807 代码审查通过，正确调用 collect_stock_data(force_full=True) |

---

## 六、总体验收结论

### ❌ 验收不通过

**4 PASS / 3 FAIL**

Q2、Q3 因 BUG-1（时区不匹配）直接失败，BUG-2（港股）和 BUG-3（融资余额）为同一根因的附带发现。三个 Critical 级 BUG 导致 011 任务的核心目标——**基本面增量门控**——完全失效。

### 阻塞条件

修复 BUG-1/2/3 后，需 QA 复验 Q2、Q3（预期修复后均 PASS），方可 PM+QA 双签关闭。

### 修复优先级

| 优先级 | BUG | 影响面 | 修复复杂度 |
|---|---|---|---|
| P0 | BUG-1 (L526) | A股基本面增量（Q2/Q3） | 低（1行改动） |
| P0 | BUG-2 (L874) | 港股基本面增量 | 低（1行改动） |
| P1 | BUG-3 (L2118) | 融资余额增量采集 | 低（1行改动） |

> 修复后 QA 复验预计可在 15 分钟内完成。

---

*QA 独立验收报告，不依赖开发自验结论。*
