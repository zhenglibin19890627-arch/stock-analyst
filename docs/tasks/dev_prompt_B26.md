# 开发提示词 B26

**推荐模型：glm5.2（GLM Plan）— 资金面数据完整度提升，涉及采集逻辑+评分权重**
**任务书：docs/tasks/dev_tasks_20260726_B26.md**

---

## 你的任务

修复资金面维度完整度（当前67%→目标≥95%），包含 4 项子任务：

1. **T1**：两融采集函数增强（日期范围扩大+非交易日异常容错）
2. **T2**：北向资金子项降权（0.30→0.10，释放权重给主力+两融）
3. **T3**：新建两融历史数据回填脚本并执行
4. **T4**：北向数据源停更日志标注

## 项目环境

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（路径含空格）
- 技术栈：Python + Flask + SQLite + akshare
- PowerShell 不支持 `&&`，用 `;` 分隔命令
- Python 多行逻辑必须写临时 .py 文件执行（内联 `-c` 在 PowerShell 会转义失败）
- 中文输出需设置：`sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')`

## 根因背景（PM 已实测确认，无需重新诊断）

**建表/适配/模型/评分函数代码已全部就绪，本批次不涉及建表。** 真实问题在采集层：

| 子项 | DB 填充率 | 根因 |
|---|---|---|
| 主力资金 main_net_inflow | 98.4% | ✅ 正常 |
| 互联互通 north_net_buy | 0.9% | ❌ `ak.stock_hsgt_individual_em` 数据源自 2024-08-16 起停更（港交所政策变更，不可修复） |
| 杠杆资金 margin_balance | 3.7% | ⚠️ 接口可用，但采集仅尝试 T-1~T-5 且 break 太早 |

---

## 详细修改方案

### T1：两融采集增强（`modules/data_collector.py`）

#### T1.1 修改 `fetch_margin_balance`（L1933-2045）

**修改点1**：日期范围扩大
```python
# 当前（L1960）：
for delta in range(1, 6):  # 尝试 T-1 到 T-5

# 改为：
for delta in range(1, 35):  # B26：扩大到 T-1 到 T-34（覆盖最近30个交易日+周末）
```

**修改点2**：break 阈值扩大
```python
# 当前（L2034）：
if updated_count >= 2:
    break

# 改为：
if updated_count >= 30:  # B26：回填完整历史
    break
```

**修改点3**：非标的判断容错（当前连续一个日期无匹配就 return skipped，改为累计3个日期无匹配才判定）
```python
# 当前（L1994-1999）：
df_match = df[df[code_col].astype(str).str.zfill(6) == symbol]
if df_match.empty:
    if updated_count == 0:
        return 'skipped', '非融资融券标的（无融资余额数据）'
    break

# 改为（B26：连续3个日期均无匹配才判定为非标的）：
df_match = df[df[code_col].astype(str).str.zfill(6) == symbol]
if df_match.empty:
    _no_match_count += 1  # 需在循环前初始化 _no_match_count = 0
    if _no_match_count >= 3 and updated_count == 0:
        return 'skipped', '非融资融券标的（连续3个日期无数据）'
    if _no_match_count >= 3:
        break
    continue
```

#### T1.2 修改 `_fetch_margin_data_sse`（L1905）和 `_fetch_margin_data_szse`（L1919）

当前已用通用 `except Exception` 兜底，但需确保：
- 非交易日返回空 DataFrame（0行0列）时，`ak.stock_margin_detail_sse` 会抛 `Length mismatch` 异常 —— 这个已被捕获，**保持现状即可**
- 建议将 `logger.warning` 降级为 `logger.debug`，因为非交易日无数据是正常情况，不需要 warning 级别：

```python
# _fetch_margin_data_sse（L1914）和 _fetch_margin_data_szse（L1928）：
# 当前：
logger.warning(f'[DATASRC-C] 上交所融资融券数据获取失败({date_str}): {e}')

# 改为：
logger.debug(f'[DATASRC-C] 上交所融资融券数据获取跳过({date_str}): {e}')
```

### T2：北向资金子项降权（`modules/scoring_engine.py`）

**位置**：L186-191，替换整个 `CAPITAL_SUBITEMS` 定义：

```python
# --- 资金面 3 子项 ---
# B26：北向资金数据源自2024-08-16起停更（港交所政策变更），降权0.30→0.10
# 释放权重按主力:两融=45:25比例分配给主力(+0.10)和两融(+0.10)
CAPITAL_SUBITEMS: list[SubItem] = [
    SubItem(
        '主力资金',
        'main_capital',
        ['main_net_inflow'],
        0.55,
        'keep_default',
        default_fills={'main_net_inflow': NEUTRAL_INFLOW},
    ),
    SubItem('互联互通', 'north_capital', ['north_net_buy'], 0.10, 'reduce'),
    SubItem('杠杆资金', 'margin_capital', ['margin_balance_chg'], 0.35, 'reduce'),
]
```

### T3：新建回填脚本（`scripts/b26_margin_backfill.py`）

```python
# -*- coding: utf-8 -*-
"""B26：两融历史数据回填脚本"""

import sys, os, io

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 设置项目根目录到 sys.path
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
os.chdir(project_root)

from modules.data_collector import fetch_margin_balance
from database.db_manager import get_connection

# 读取所有 A 股自选股
conn = get_connection()
cursor = conn.cursor()
cursor.execute("SELECT symbol, name FROM stocks WHERE market = 'a_stock'")
stocks = cursor.fetchall()
conn.close()

print(f'===== B26 两融历史数据回填 =====')
print(f'待回填股票数: {len(stocks)}')

success = 0
skipped = 0
failed = 0
for i, (symbol, name) in enumerate(stocks, 1):
    print(f'[{i}/{len(stocks)}] {symbol} {name} ...', end=' ')
    try:
        status, msg = fetch_margin_balance(symbol, 'a_stock')
        print(f'{status}: {msg}')
        if status == 'success':
            success += 1
        elif status == 'skipped':
            skipped += 1
        else:
            failed += 1
    except Exception as e:
        print(f'异常: {e}')
        failed += 1

# 验证填充率
conn = get_connection()
cursor = conn.cursor()
cursor.execute(
    'SELECT COUNT(*) AS total, SUM(CASE WHEN margin_balance IS NOT NULL THEN 1 ELSE 0 END) AS filled FROM raw_capital_flow'
)
row = cursor.fetchone()
conn.close()
total = row[0]
filled = row[1] or 0
pct = round(filled / total * 100, 1) if total else 0

print(f'\n===== 回填结果 =====')
print(f'成功: {success}  跳过: {skipped}  失败: {failed}')
print(f'\n===== margin_balance 填充率 =====')
print(f'{filled}/{total} 行非空 ({pct}%)')
if pct >= 80:
    print('✅ 达标（≥80%）')
else:
    print(f'⚠️ 未达标（{pct}% < 80%），请检查失败原因')
```

**执行命令**：
```powershell
cd "c:\Users\zlb19\Desktop\Qoder cn\stock_analyst"; & "C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe" scripts\b26_margin_backfill.py
```

### T4：北向资金停更标注（`modules/data_collector.py`）

**位置**：`fetch_north_capital`（L1803），在 `latest_row = df.iloc[-1]`（L1849）之后增加日期检查：

```python
latest_row = df.iloc[-1]
trade_date_raw = str(latest_row[date_col]).split(' ')[0]

# B26：北向数据源停更标注
if '2024' in trade_date_raw and trade_date_raw < '2024-08-16':
    logger.info(
        f'[DATASRC-C] {symbol} 北向资金数据源停更，最新数据日期 {trade_date_raw}，不影响评分（B26已降权至0.10）'
    )
```

---

## 红线清单（绝对不可违反）

| # | 红线 | 说明 |
|---|---|---|
| R1 | `data_collector.py` L1645/L1684/L1717 | 三处 `if False` 硬禁用，**绝对不可修改** |
| R2 | 无新 pip 依赖 | 零代码约束 |
| R3 | rating_mapping 80/65/50/30 | 不变（仅调子项权重，不动评级档位） |
| R4 | config_weights.json | 本批次**不修改**此文件 |
| R5 | 北向接口调用保留 | `ak.stock_hsgt_individual_em` 调用保留，仅加注释标注停更 |

---

## 自验清单（完成所有修改后逐项执行）

将自验结果写入 `reports/dev_selftest_B26.md`。

| # | 验证项 | 验证命令 | 通过标准 |
|---|---|---|---|
| V1 | margin_balance 填充率 | 见上方回填脚本末尾输出 | **≥80%** |
| V2 | 非交易日不崩溃 | 检查回填脚本日志无 traceback | 无未捕获异常 |
| V3 | 权重验证 | 临时 .py：`from modules.scoring_engine import CAPITAL_SUBITEMS; [print(s.key, s.base_weight) for s in CAPITAL_SUBITEMS]` | main=0.55, north=0.10, margin=0.35 |
| V4 | 评分不回归 | 对 600276/000333/002415 执行评分 | 评级不跨档突变（80/65/50/30 不变） |
| V5 | 红线全守 | Grep `if False` 确认3处未变；检查 requirements.txt 无新增 | 全部通过 |
| V6 | 回填脚本幂等 | 二次运行 b26_margin_backfill.py | 无重复写入异常 |

---

## 修改文件清单

| 文件 | 操作 |
|---|---|
| `modules/scoring_engine.py` | 修改 L186-191 |
| `modules/data_collector.py` | 修改 fetch_margin_balance / _fetch_margin_data_sse / _fetch_margin_data_szse / fetch_north_capital |
| `scripts/b26_margin_backfill.py` | 新建 |
| `reports/dev_selftest_B26.md` | 新建（自验报告） |
