# DEV-TASKS-20260803-018 资金面数据源修复与同花顺辅助指标

## 任务概述

修复资金面数据源问题：禁用同花顺批量预取作为主数据源，恢复东方财富逐只采集为唯一主力净流入来源；将同花顺净额降级为辅助指标（新增字段），用于判断主力与散户背离；清理数据库中已有的同花顺口径错误数据。

## 背景与根因

同花顺批量接口 `ak.stock_fund_flow_individual()` 返回的"净额"字段含义为 **全部资金净流入**（总主动买入-总主动卖出），而非"主力净流入"（超大单+大单）。该接口数据与东方财富APP、同花顺APP均不一致，且存在极端异常值。

当前系统中，同花顺批量预取优先执行并写入 `raw_capital_flow.main_net_inflow`，导致东方财富逐只采集的"同日跳过"机制触发，数据库中存储的是错误口径数据。

## 改动范围

### 1. data_collector.py — 禁用同花顺批量预取主数据源

**文件**: `modules/data_collector.py`

**改动A**: 修改 `fetch_capital_flow_batch()` 函数（L1198-L1299）

- 将函数改为 **仅写入辅助字段** `ths_net_inflow`（新增列），不再写入 `main_net_inflow` / `main_net_inflow_pct`
- 函数返回值中 `source` 改为 `'同花顺批量(辅助指标)'`
- 保留批量调用逻辑（1次API调用获取全市场数据，效率高）
- 保留降级逻辑（THS失败时回退EM逐只采集）

**改动B**: 修改 `fetch_capital_flow()` 前置校验注释（L1689-L1706）

- 更新注释，说明前置校验仅检测东方财富已写入的数据
- 同花顺辅助指标不写入 `main_net_inflow`，不会触发跳过

**改动C**: 新增辅助字段写入逻辑

在 `fetch_capital_flow_batch()` 中，将同花顺净额写入新列 `ths_net_inflow`：

```python
# 原写入（禁用）：
# INSERT OR REPLACE INTO raw_capital_flow
# (stock_id, trade_date, main_net_inflow, main_net_inflow_pct)
# VALUES (?, ?, ?, ?)

# 新写入（辅助指标）：
# 使用 UPDATE（如果当天已有东财数据）或 INSERT（如果当天无数据）
# 仅写入 ths_net_inflow 字段，不影响 main_net_inflow
```

### 2. db_manager.py — 新增辅助字段

**文件**: `database/db_manager.py`

在 `raw_capital_flow` 建表语句（L238-L253）中新增列：

```sql
ths_net_inflow REAL,             -- 同花顺全资金净流入(万元)，辅助指标
```

使用 `ALTER TABLE` 兼容已有数据库：

```python
# 在 _migrate_db() 或类似迁移函数中添加
try:
    cursor.execute('ALTER TABLE raw_capital_flow ADD COLUMN ths_net_inflow REAL')
except sqlite3.OperationalError:
    pass  # 列已存在
```

### 3. daily_report.py — 更新注释

**文件**: `modules/daily_report.py`

L619-L626：更新注释，说明批量预取现在仅写入辅助指标，不阻断东财逐只采集。

### 4. app.py — 更新注释

**文件**: `app.py`

L1278-L1294：更新注释，说明批量预取现在仅写入辅助指标。

### 5. index.html — 前端展示辅助指标

**文件**: `templates/index.html`

L2478-L2489 资金面数据区域：

- 表格新增"同花顺净额"列（`ths_net_inflow`）
- 表头标注"辅助指标"
- 更新底部说明文字

### 6. 数据清理 — 删除同花顺口径错误数据

**执行SQL脚本**（开发完成后由PM执行）：

```sql
-- 删除所有 super_large_net 为 NULL 的记录（同花顺批量写入的，无分单数据）
-- 这些记录的 main_net_inflow 是同花顺口径，需要清除后重新采集
DELETE FROM raw_capital_flow
WHERE super_large_net IS NULL AND main_net_inflow IS NOT NULL;

-- 清除对应的 data_status 记录，允许重新采集
DELETE FROM data_status
WHERE dimension = 'capital'
AND message LIKE '%同日跳过%';
```

## 红线约束

1. **不引入新 pip 依赖**
2. **不改变东方财富逐只采集逻辑**（`_fetch_capital_flow_em_individual` / `_fetch_capital_flow_em` / akshare 降级链）
3. **不改变评分引擎逻辑**（`analysis_engine.py` 中 `score_capital_flow()` 仍使用 `main_net_inflow`）
4. **同花顺辅助指标不参与评分**，仅作展示参考
5. **保持零代码约束**，双击 start.bat 即用

## 验收标准

1. `fetch_capital_flow_batch()` 不再写入 `main_net_inflow` / `main_net_inflow_pct`
2. `fetch_capital_flow_batch()` 将同花顺净额写入 `ths_net_inflow` 新列
3. `raw_capital_flow` 表新增 `ths_net_inflow` 列（ALTER TABLE 兼容）
4. 东方财富逐只采集正常执行，不再被同花顺批量预取阻断
5. 前端资金面表格新增"同花顺净额"列
6. 数据清理脚本执行后，同花顺口径错误数据被清除
7. 清除后重新采集，数据库中 `main_net_inflow` 与东方财富APP一致

## 执行顺序

1. 修改 `db_manager.py`（新增列 + ALTER TABLE 迁移）
2. 修改 `data_collector.py`（改造批量预取函数）
3. 修改 `daily_report.py` 和 `app.py`（更新注释）
4. 修改 `index.html`（前端展示）
5. 执行数据清理SQL
6. 验证：重新采集宁德时代数据，确认与东财APP一致

## 推荐执行方式

- **窗口类型**: Chats（当前窗口）
- **推荐模型**: glm5.2（编码任务）
- **专家团**: 不需要（单代理任务）
