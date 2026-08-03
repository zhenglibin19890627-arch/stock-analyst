# 开发提示词 B14：行业本地映射兜底

> **任务书：** `docs/tasks/dev_tasks_2026-07-25_B14.md`（含完整代码）

---

## 角色

你是 Stock Analyst 项目开发。本批次修改 2 个文件，添加行业本地映射兜底。

## 项目信息

| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst` |
| DB 路径 | 项目根目录 `stock_analyst.db` |
| 修改文件 | `modules/data_collector.py` + `database/db_manager.py` |

## 环境注意

- PowerShell 不支持 `&&`，用 `;`
- 路径含空格需引号
- Python 多行逻辑写临时 .py 文件执行
- 临时文件用完删除

## 红线

- **data_collector.py L1645/L1684/L1717 三处 `if False` 绝对不可改为 True**
- 不引入新 pip 依赖
- 不修改 config_weights.json
- 修改区域在 L2081 附近（远离红线区域）

---

## T1：data_collector.py — 添加字典 + 修改函数

**位置：** `modules/data_collector.py`，在 `fetch_stock_industry()` 函数（约 L2081）上方

**操作：**
1. 在函数上方添加 `_LOCAL_INDUSTRY_MAP` 字典（22 只 A 股，见任务书）
2. 修改 `fetch_stock_industry()` 函数：API 失败后查本地字典兜底（见任务书代码）

**关键：** 不删除原有 API 调用逻辑，只在 `return '未分类'` 之前插入本地映射查询。

---

## T2：db_manager.py — 启动迁移

**位置：** `database/db_manager.py`，`init_db()` 函数末尾（所有建表和迁移之后）

**操作：** 添加幂等 UPDATE 逻辑，用 `_LOCAL_INDUSTRY_MAP` 补全 industry='未分类' 的记录。

**注意：**
- `from modules.data_collector import _LOCAL_INDUSTRY_MAP` 放在 try 块内（避免循环导入）
- 只更新 `industry IS NULL OR industry = '未分类' OR industry = ''` 的记录
- 已有正确 industry 的不覆盖

---

## 执行顺序

```
T1（data_collector.py）→ T2（db_manager.py）→ 验证
```

## 自验方法

```python
import sys

sys.path.insert(0, r'C:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
from modules.data_collector import fetch_stock_industry, _LOCAL_INDUSTRY_MAP

# T1 验证
print(fetch_stock_industry('600519'))  # 预期：酿酒行业（API失败时兜底）
print(fetch_stock_industry('000333'))  # 预期：家电行业
print(fetch_stock_industry('HK9988', 'hk_stock'))  # 预期：港股
print(fetch_stock_industry('999999'))  # 预期：未分类（不在字典中）

# T2 验证（启动 app 后检查 DB）
import sqlite3

conn = sqlite3.connect(r'C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db')
c = conn.cursor()
c.execute("SELECT COUNT(*) FROM stocks WHERE industry='未分类' AND market='a_stock'")
print(f'A股未分类数: {c.fetchone()[0]}')  # 预期：0
conn.close()
```

## 自验报告格式

```markdown
# B14 开发自验报告

## T1 本地映射
- fetch_stock_industry('600519') = [值]
- fetch_stock_industry('000333') = [值]
- fetch_stock_industry('HK9988','hk_stock') = [值]
- fetch_stock_industry('999999') = [值]

## T2 启动迁移
- A股未分类数: [值]
- 重复启动报错: [是/否]

## 红线
- L1645/L1684/L1717 if False 未触碰: [是]
- 无新依赖: [是]
- config_weights.json 未修改: [是]
```

---

*PM 签发 | 2026-07-25 | B14*
