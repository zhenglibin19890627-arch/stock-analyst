# 开发提示词 B21

**推荐模型：glm5.2（GLM Plan）**
**任务书：docs/tasks/dev_tasks_20260726_B21.md**

---

## 你的任务

修复 PE/PB 估值数据和 holder_increase 的行错位问题，使 data_adapter 能正确读取这些字段，提升 v5 引擎数据完整度。

## 项目环境

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（路径含空格）
- 技术栈：Python + Flask + SQLite + akshare
- PowerShell 不支持 `&&`，用 `;` 分隔命令

## 根因

`modules/data_adapter.py` 读取基本面数据时，用 `ORDER BY report_date DESC LIMIT 1` 取最新行。但 PE/PB 和 holder_increase 写入时 UPDATE 到的是当时的 `MAX(report_date)` 行，后续 force 重跑会 INSERT 新行，导致 adapter 读取的最新行没有 PE/PB。

**实测数据**：PE/PB 填充率 0%（DB 有值但残留在旧行），holder_increase 填充率 0%。

## 修复方案

### 修改文件：modules/data_adapter.py

在读取 raw_fundamental 的地方（约 L370-390），增加**聚合回退逻辑**：

**修改前**（现有逻辑）：
```python
cursor.execute(
    'SELECT * FROM raw_fundamental WHERE stock_id=? ORDER BY report_date DESC LIMIT 1', (stock_id,)
)
fund = cursor.fetchone()
```

**修改后**（增加回退）：
```python
cursor.execute(
    'SELECT * FROM raw_fundamental WHERE stock_id=? ORDER BY report_date DESC LIMIT 1', (stock_id,)
)
fund = cursor.fetchone()

# B21: PE/PB 行错位修复 — 最新行为 NULL 时从其他行回退
if fund:
    fund = dict(fund)  # sqlite3.Row 转可变 dict

    # PE/PB 回退
    if fund.get('pe_ratio') is None or fund.get('pb_ratio') is None:
        pe_row = cursor.execute(
            'SELECT pe_ratio, pb_ratio FROM raw_fundamental '
            'WHERE stock_id=? AND (pe_ratio IS NOT NULL OR pb_ratio IS NOT NULL) '
            'ORDER BY report_date DESC LIMIT 1',
            (stock_id,),
        ).fetchone()
        if pe_row:
            if fund.get('pe_ratio') is None:
                fund['pe_ratio'] = pe_row['pe_ratio']
            if fund.get('pb_ratio') is None:
                fund['pb_ratio'] = pe_row['pb_ratio']

    # holder_increase 回退
    if fund.get('holder_increase') is None:
        hi_row = cursor.execute(
            'SELECT holder_increase FROM raw_fundamental '
            'WHERE stock_id=? AND holder_increase IS NOT NULL '
            'ORDER BY report_date DESC LIMIT 1',
            (stock_id,),
        ).fetchone()
        if hi_row:
            fund['holder_increase'] = hi_row['holder_increase']
```

**注意**：
1. `fund = dict(fund)` 很重要——sqlite3.Row 是只读的，需要转为可变 dict
2. 回退查询必须用 `WHERE stock_id=?` 和 `ORDER BY report_date DESC`，取最新有值的行
3. 不要修改下游 `fund.get('pe_ratio')` 的字段名——data_adapter L375 已经是 `fund.get('pe_ratio')`

## 红线（绝对禁止）

1. **data_collector.py** 不可修改（含采集逻辑 + L1645/L1684/L1717 三处 if False）
2. **config_weights.json** 不可修改
3. **scoring_engine.py** 评分逻辑不动
4. **data_contract.py** StockData 模型不动
5. **templates/index.html** 前端不动
6. **app.py** API 路由不动
7. **不引入**新 pip 依赖

## 自验要求

1. 修改后执行 force 重跑：
   ```python
   from modules.daily_report import generate_daily_report

   generate_daily_report(force=True)
   ```

2. SQL 核验 PE/PB 填充率（写临时 .py 文件）：
   ```python
   # 检查 adapter 读取后 PE/PB 是否有值
   # 检查 data_quality fundamental 完整度
   ```

3. API 调用 `/api/stocks/27/advise`，检查 data_quality.fundamental 是否从 0% 提升

4. Grep 核验红线守恒（data_collector if False）

自验报告归档至 `reports/dev_selftest_B21.md`。
