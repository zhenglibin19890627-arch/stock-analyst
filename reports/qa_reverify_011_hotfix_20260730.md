# QA复验报告：011-Hotfix 时区Bug修复

> **验收人**：QA（独立复验） | **日期**：2026-07-30 | **状态**：✅ 通过
> **关联首验**：`reports/qa_accept_011_incremental_20260730.md`（Q2/Q3 FAIL，BUG-1/2/3）
> **关联开发自验**：`reports/dev_selftest_011_hotfix_20260730.md`（V1-V4 PASS）

---

## 一、复验范围

针对首验发现的 3 个时区不匹配 Bug（同一根因：`datetime.now(_CN_TZ)` tz-aware 与 `datetime.strptime()` tz-naive 直接相减 → TypeError），复验 4 项：

| 复验项 | 对应首验 | 修复位置 | 验证目标 |
|--------|---------|---------|---------|
| R1 | Q2 (BUG-1) | `data_collector.py` L526 | A股基本面80天财报门控生效 |
| R2 | Q3 (BUG-1连锁) | `data_collector.py` L541 | PE/PB 24h门控生效（双门控联合跳过） |
| R3 | BUG-2 | `data_collector.py` L874 | 港股基本面80天门控生效 |
| R4 | BUG-3 | `data_collector.py` L2104 | 融资余额增量补取正常（不抛TypeError） |

---

## 二、代码修复确认（复验前置）

复验前先审查 3 处修复代码，确认均采用 `.replace(tzinfo=None)` 对齐为 tz-naive：

| 位置 | 修复前（首验Bug） | 修复后 | 状态 |
|------|------------------|--------|------|
| L526 | `datetime.now(_CN_TZ) - datetime.strptime(...)` | `datetime.now(_CN_TZ).replace(tzinfo=None) - datetime.strptime(...)` | ✅ 已修复 |
| L874 | `datetime.now(_CN_TZ) - datetime.strptime(...)` | `datetime.now(_CN_TZ).replace(tzinfo=None) - datetime.strptime(...)` | ✅ 已修复 |
| L2104 | `today = datetime.now(_CN_TZ)` | `today = datetime.now(_CN_TZ).replace(tzinfo=None)` | ✅ 已修复 |

修复方式与项目中已有的正确范例 `fetch_north_capital` L1951 一致。

---

## 三、复验结果汇总

| 项 | 结论 | 证据摘要 |
|---|---|---|
| R1 A股80天门控 | ✅ PASS | 返回 `('success', '同日跳过(财报80天TTL内+PE/PB 24h内)')`，0.011s，财报日期未变 |
| R2 PE/PB 24h门控 | ✅ PASS | 返回 `('success', '同日跳过(财报80天TTL内+PE/PB 24h内)')`，0.010s，双门控联合跳过 |
| R3 港股80天门控 | ✅ PASS | 返回 `('success', '同日跳过(港股财报8天内)')`，0.011s，财报日期未变 |
| R4 融资余额增量 | ✅ PASS | 返回 `('success', '融资余额已更新(3条记录)')`，2.331s，无TypeError |
| 附加A force_full绕过 | ✅ PASS | force_full=True 返回 `('success', '基本面数据采集成功')`，4.5s，不含"跳过" |
| 附加B Q1抽检 | ✅ PASS | K线返回 `('success', '同日跳过(K线已有2026-07-30数据)')` |
| 附加B Q4抽检 | ✅ PASS | 消息面返回 `('success', '当日跳过(消息面已有1条记录)')` |

---

## 四、逐项复验详情

### R1：A股80天财报门控 — ✅ PASS

**测试对象**：000333 美的集团（id=11，report_date=2026-07-15，days_ago=15 < 80）

**执行证据**：
```
[当前时间] 2026-07-30 15:40:46
[测前] 最新财报日期: 2026-07-15
[测前] fundamental状态: fetched_at=2026-07-30 15:20:47 (<24h)

[R1调用结果] status=success, msg=同日跳过(财报80天TTL内+PE/PB 24h内), 耗时=0.011s
[测后] 最新财报日期: 2026-07-15 (未变 → 财报未重新采集)
```

**关键日志**（证明门控生效、无降级）：
```
INFO - [A股 000333] 财报数据15天内，跳过财报采集
```
**无** 首验中的 `WARNING - 基本面增量检查异常(降级为全量)` → 时区 Bug 已消除。

**判定**：返回 success + 含"跳过" + 财报日期未变 + 无降级WARNING → **PASS**

> 说明：因测前 fundamental 状态已在24h内（开发自验于15:20执行），R1 直接命中双门控联合跳过。80天门控生效的核心证据是日志"财报数据15天内，跳过财报采集"（days_since=15 正确计算）且无 TypeError 降级。

---

### R2：PE/PB 24h门控 — ✅ PASS

**测试对象**：000333 美的集团（R1 执行后立即调用，fundamental 状态 < 24h）

**执行证据**：
```
[R2调用结果] status=success, msg=同日跳过(财报80天TTL内+PE/PB 24h内), 耗时=0.010s
```

**判定**：返回含"财报80天TTL内+PE/PB 24h内"双门控跳过字样 → **PASS**

> 首验中此路径因 L526 异常而**永远不可达**（PE/PB门控代码在80天门控的 if 块内）。修复后两门控均可正常评估并联合跳过。

---

### R3：港股80天门控 — ✅ PASS

**测试对象**：HK3690 美团-W（id=6，report_date=2026-07-22，days_ago=8 < 80）

**执行证据**：
```
[测前] 港股最新财报日期: 2026-07-22
[R3调用结果] status=success, msg=同日跳过(港股财报8天内), 耗时=0.011s
[测后] 港股最新财报日期: 2026-07-22 (未变)
```

**判定**：返回 success + "同日跳过(港股财报8天内)" + 财报日期未变 → **PASS**

> 首验中此处返回 `('success', '港股基本面数据采集成功')`（降级全量）。修复后正确跳过。

---

### R4：融资余额增量 — ✅ PASS

**测试对象**：600276 恒瑞医药（id=4，DB已有107条 margin 数据，最新日期2026-07-29）

**执行证据**：
```
[R4调用结果] status=success, msg=融资余额已更新(3条记录), 耗时=2.331s
```

**关键日志**：
```
INFO - [DATASRC-C] 融资余额采集: 600276
INFO - [DATASRC-C] 600276 融资余额写入成功: 3条记录
```

**判定**：返回 success（非 failed）+ 无 TypeError + 成功增量补取3条 → **PASS**

> 首验中此处因 L2118 `(today - last_margin).days` 抛 TypeError，整个函数返回 `('failed', ...)`。修复后 `today` 已去除时区（L2104 `.replace(tzinfo=None)`），增量"补近期"逻辑正常工作，仅补取3条近期记录（而非全量159天），验证增量效率提升。

---

## 五、附加验证

### 附加A：force_full 仍可绕过 — ✅ PASS

```
force_full=True: status=success, msg=基本面数据采集成功, 耗时=4.5s
```
不含"跳过"字样，全量采集正常（重新请求新浪财报4条 + 腾讯PE/PB）。force_full 参数透传未受修复影响。

### 附加B：首验 PASS 项抽检 — ✅ PASS

| 抽检项 | 结果 |
|--------|------|
| Q1 K线同日跳过 | `('success', '同日跳过(K线已有2026-07-30数据)')` ✅ |
| Q4 消息面当日跳过 | `('success', '当日跳过(消息面已有1条记录)')` ✅ |

首验已通过的增量逻辑（K线字符串比较、消息面COUNT比较）不受本次时区修复影响，回归正常。

---

## 六、总结论

### ✅ 复验通过

**R1-R4 全部 PASS + 附加验证全部 PASS**

首验发现的 3 个时区不匹配 Bug（BUG-1/2/3）已全部修复并验证生效：
- A股80天财报门控 + PE/PB 24h门控：双门控联合跳过正常（R1/R2）
- 港股80天门控：跳过正常（R3）
- 融资余额增量补取：无 TypeError，增量效率提升（R4，仅补3条而非全量）
- force_full 强制全量：仍可正常绕过（附加A）
- 首验 PASS 项无回归（附加B）

**建议**：QA 复验通过，011 数据采集全链路增量优化（含 Hotfix）可进入 **PM+QA 双签关闭** 流程，报监理批准。

---

*QA 独立复验报告，不依赖开发自验结论。测试脚本执行后已清理，未修改任何项目代码。*
