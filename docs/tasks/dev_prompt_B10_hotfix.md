# B10-Hotfix 开发提示词：基本面补全行级写入修复

## 你的角色

你是「智能个股分析与评级系统」的开发工程师，负责执行 B10 热修复。

## 项目信息

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`
- 技术栈：Python + Flask + SQLite + akshare

## Bug 描述

`_apply_fundamental_detail()` 将补全数据写入了 `report_date` 较旧的行，而 `data_adapter._read_fundamental_data()` 用 `ORDER BY report_date DESC LIMIT 1` 读取最新行。两行不匹配导致 v5 引擎仍读到仅含 PE/PB 的旧数据（22%）。

**实例**：000333 美的集团
- Row A（report_date=2026-07-15）：仅 pe_ratio=14.58, pb_ratio=3.16 → **adapter 读这行**
- Row B（report_date=2026-03-31）：B10 补全数据写入了这里 → **引擎永远读不到**

## 修复方案（改 1 处）

**文件**：`modules/data_collector.py`
**函数**：`_apply_fundamental_detail()`（约 L623-660）

**修改前**（L639-648）：
```python
# 获取最新财报记录
cursor.execute(
    'SELECT id, report_date FROM raw_fundamental WHERE stock_id = ? ORDER BY report_date DESC LIMIT 1',
    (stock_id,),
)
row = cursor.fetchone()
if not row:
    conn.close()
    return
rec_id = row['id']
```

**修改后**：
```python
# 检查是否有任何记录
cursor.execute('SELECT COUNT(*) as cnt FROM raw_fundamental WHERE stock_id = ?', (stock_id,))
if cursor.fetchone()['cnt'] == 0:
    conn.close()
    return
```

**同时修改 UPDATE 语句**（约 L656-658）：

修改前：
```python
if updates:
    params.append(rec_id)
    sql = f'UPDATE raw_fundamental SET {", ".join(updates)} WHERE id = ?'
```

修改后：
```python
if updates:
    params.append(stock_id)
    sql = f'UPDATE raw_fundamental SET {", ".join(updates)} WHERE stock_id = ?'
```

**效果**：COALESCE 会更新该股票 **所有行** 中为 NULL 的字段，无论 adapter 读哪行都能获得补全数据。已有值的字段不受影响（COALESCE 保护）。

## 红线

1. **不修改** `if False` 块（当前在 L1630/L1669/L1702）
2. **不新增** pip 依赖
3. **不修改** `config_weights.json`
4. **不修改** 评分逻辑
5. 仅改 `_apply_fundamental_detail` 函数内部（约 4 行变化）

## 自验步骤

修改完成后执行：

```python
import os, sys

os.chdir(r'C:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
sys.path.insert(0, '.')

from modules.data_collector import fetch_fundamental_detail, _apply_fundamental_detail

# 对 000333 重新执行补全
detail = fetch_fundamental_detail('000333')
print(f'获取到: {detail}')
_apply_fundamental_detail(11, detail)  # stock_id=11 是 000333

# 验证：最新行是否有补全数据
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute('SELECT * FROM raw_fundamental WHERE stock_id=11 ORDER BY report_date DESC LIMIT 1')
row = c.fetchone()
fields = [
    'pe_ratio',
    'pb_ratio',
    'roe',
    'gross_margin',
    'revenue_growth',
    'profit_growth',
    'ocf_to_net_profit',
    'debt_ratio',
    'current_ratio',
]
non_null = sum(1 for f in fields if row[f] is not None)
print(f'最新行 non_null: {non_null}/9 = {non_null / 9:.0%}')
for f in fields:
    print(f'  {f} = {row[f]}')
conn.close()
# 期望：最新行 non_null >= 7/9 (78%)
```

## 交付物

1. 修改后的 `data_collector.py`（`_apply_fundamental_detail` 函数内约 4 行变化）
2. 自验结果（000333 最新行完整度 ≥78%）
