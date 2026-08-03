# B9 开发提示词：行业分类补取修复

## 你的角色

你是「智能个股分析与评级系统」的开发工程师，负责执行 B9 批次任务。

## 项目信息

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`
- 技术栈：Python + Flask + SQLite + akshare
- 任务书：`docs/tasks/dev_tasks_20260724_B9.md`

## 任务内容（仅 1 处修改）

**文件**：`app.py`
**定位**：搜索注释 `INDUSTRY-DYNAMIC：批量分析时若 industry 为空则补取`（约 L1002-1011）

**修改前**（约 L1004）：
```python
if not (stock['industry'] or '').strip():
```

**修改后**：
```python
if not (stock['industry'] or '').strip() or stock['industry'] in ('未分类',):
```

## 修改原因

添加股票时 `fetch_stock_industry()` 失败会写入 `"未分类"`（非空字符串），导致批量分析时补取条件永远为 False。修改后，industry 为"未分类"时也会触发重新获取。

## 红线（绝对不可触碰）

1. **不修改** `data_collector.py` L1474/L1513/L1546 三处 `if False`
2. **不新增** pip 依赖
3. **不修改** `config_weights.json`
4. **不超出** 本任务范围（仅改上述 1 行条件）

## 自验步骤

修改完成后，请执行以下验证：

```python
# 在项目根目录执行
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
c = conn.cursor()
# 确认修改前状态
c.execute("SELECT symbol, industry FROM stocks WHERE market='a_stock' LIMIT 5")
print(c.fetchall())
conn.close()
```

然后启动应用 `python app.py`，通过浏览器或 curl 触发批量分析 1 只 A 股（如 stock_id=24 即 000858），分析完成后检查：

```python
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
c = conn.cursor()
c.execute("SELECT symbol, industry FROM stocks WHERE symbol='000858'")
print(c.fetchone())  # 应显示真实行业（如"白酒"），不再是"未分类"
conn.close()
```

## 交付物

1. 修改后的 `app.py`（仅 1 行变化）
2. 自验报告（截图或文字说明验证结果）
