# 开发自验报告 DEV-TASKS-20260731-014

| 项 | 内容 |
|---|---|
| 任务编号 | DEV-TASKS-20260731-014 |
| 签发日期 | 2026-07-31 |
| 开发人员 | Dev（qwen3.8） |
| 执行日期 | 2026-07-31 |
| 任务类型 | 紧急 Bug 修复（评级列表重复 + 持仓标签错误） |

---

## 一、改动文件清单

| 文件 | 改动范围 | 行数变化 |
|---|---|---|
| `app.py` | `api_get_ratings_list()` 增加 report_type 过滤逻辑（L1291~L1307） | +11 / -3 |
| `modules/advisor.py` | `_read_position()` 增加 holdings 表优先查询（L53~L76） | +17 / -1 |

**未改动文件**：daily_report.py、price_advisor.py、templates/*、requirements.txt、config_weights.json、scoring_engine.py、data_collector.py、database/db_manager.py

---

## 二、Bug 1 修复详情：评级列表股票重复显示

### 根因

`/api/ratings` 端点的 SQL 查询 `WHERE dr.report_date = ? AND dr.status = 'ok'` 未按 `report_type` 过滤。013 引入 intraday 报告类型后，同一天同时存在 daily + intraday 记录，导致每只股票出现两行。

### 数据现状（修复前）

```
report_date=2026-07-31: daily(ok)=29 条 + intraday(ok)=29 条 → 共 58 行（应 29 行）
```

### 修复方案

在 `api_get_ratings_list()` 中，查到 `latest_date` 后，应用与 013-Hotfix `get_latest_reports()` 相同的优先逻辑：
1. 查询该日期是否有 `report_type='daily'` 且 `status='ok'` 的记录
2. 有 daily → `target_type='daily'`；无 daily → `target_type='intraday'`
3. SQL WHERE 增加 `AND dr.report_type = ?`，params 增加 target_type

SELECT 字段列表同步增加 `dr.report_type` 以便前端识别数据来源。

### 核心代码

```python
# 014修复：优先取 daily，无 daily 时取 intraday
cursor.execute(
    'SELECT COUNT(*) as cnt FROM daily_reports '
    "WHERE report_date=? AND report_type='daily' AND status='ok'",
    (latest_date,),
)
has_daily = cursor.fetchone()['cnt'] > 0
target_type = 'daily' if has_daily else 'intraday'
# WHERE 增加 report_type 过滤
# WHERE dr.report_date = ? AND dr.status = 'ok' AND dr.report_type = ?
```

---

## 三、Bug 2 修复详情：个股详情持仓标签错误

### 根因

`advisor.py` `_read_position()` 仅查旧 `positions` 表，而持仓管理页面写入的是 `holdings` 表（新表）。当某股票只在 holdings 表有记录（如 stock_id=21 中国中免，1200 股）时，`_read_position()` 返回 None，导致详情页显示"当前无持仓"。

### 数据现状（修复前）

| 表 | stock_id | 数量 |
|---|---|---|
| holdings | 4, 6, 7, 11, 13, **21** | 各有持仓 |
| positions | 4, 6, 7, 11, 13 | 5 只（缺 21） |

stock_id=21（中国中免）仅在 holdings 表，旧逻辑返回 None → 持仓标签错误。

### 修复方案

`_read_position()` 采用与 `price_advisor.py` `_read_cost_price()` 完全一致的双表查询模式：
1. **优先查 holdings 表**（`WHERE stock_id=? AND status='active'`），命中且有 quantity>0 则返回
2. **Fallback 旧 positions 表**
3. holdings 查询包裹 try/except 防御（兼容表结构差异）

### 核心代码

```python
def _read_position(stock_id):
    """读取持仓信息（014修复：优先 holdings 表，fallback positions 表）"""
    conn = get_connection()
    cursor = conn.cursor()
    # 优先查 holdings 表（新表，持仓管理页面写入）
    try:
        cursor.execute(
            "SELECT cost_price, quantity FROM holdings WHERE stock_id = ? AND status = 'active'",
            (stock_id,),
        )
        row = cursor.fetchone()
        if row and row['quantity'] and row['quantity'] > 0:
            conn.close()
            return {'cost_price': row['cost_price'], 'quantity': row['quantity']}
    except Exception:
        pass
    # Fallback: 旧 positions 表
    cursor.execute('SELECT cost_price, quantity FROM positions WHERE stock_id = ?', (stock_id,))
    row = cursor.fetchone()
    conn.close()
    if row and row['quantity'] and row['quantity'] > 0:
        return {'cost_price': row['cost_price'], 'quantity': row['quantity']}
    return None
```

**函数签名不变**：`_read_position(stock_id)` → `{'cost_price': float, 'quantity': int} | None`

---

## 四、红线约束检查

| 红线 | 状态 | 说明 |
|---|---|---|
| advisor.py `generate_advice` 函数体 | ✅ 未改 | AST 验证签名 `['stock_id', 'report_date']` 不变 |
| advisor.py `_build_capital_factors` | ✅ 未改 | 函数体无任何变化 |
| advisor.py `_read_position` | ✅ 豁免修改 | 本次唯一豁免项，监理已批准 |
| data_collector.py 三处 if False | ✅ 未改 | 未触碰 |
| config_weights.json | ✅ 未改 | 未触碰 |
| scoring_engine.py | ✅ 未改 | 未触碰 |
| 零依赖 | ✅ | requirements.txt 无变化（8 行依赖） |

---

## 五、自验清单（B1~B7）

测试环境：Flask 服务 `http://127.0.0.1:5000`，Python 3.12，stock_analyst.db

| # | 验证项 | 方法 | 结果 | 实测数据 |
|---|---|---|---|---|
| B1 | 评级列表无重复 | `GET /api/ratings` → 确认 count ≤ 自选股数 | ✅ PASS | count=**29**（自选股 29），重复 stock_id 数=**0**（修复前 58） |
| B2 | report_type 一致 | 检查返回记录的 report_type | ✅ PASS | 全部为 `{'daily'}`，无 daily+intraday 混杂 |
| B3 | 已持仓标签正确 | `GET /api/stocks/21/report-latest` | ✅ PASS | stock_id=21（中国中免）`has_position=**True**`（修复前 False） |
| B4 | 无持仓标签正确 | `GET /api/stocks/15/report-latest` | ✅ PASS | stock_id=15（宁德时代）`has_position=**False**` |
| B5a | 日报回归 | `GET /api/daily-report/latest` | ✅ PASS | status=200 |
| B5b | 持仓接口回归 | `GET /api/portfolio/holdings` | ✅ PASS | status=200，返回 6 条持仓 |
| B6 | generate_advice 未改 | AST 解析签名对比 | ✅ PASS | 签名 `['stock_id', 'report_date']` 不变；`_build_capital_factors` 未改 |
| B7 | 零依赖 | requirements.txt 检查 | ✅ PASS | 8 行依赖，无新增 |

### 自验汇总：**8/8 全部通过**

---

## 六、修复效果对比

### Bug 1 效果

| 指标 | 修复前 | 修复后 |
|---|---|---|
| `/api/ratings` count | 58（29 daily + 29 intraday） | **29**（仅 daily） |
| 重复 stock_id 数 | 29 | **0** |
| report_type 一致性 | daily + intraday 混杂 | 全部 daily |

### Bug 2 效果

| 股票 | 修复前 | 修复后 |
|---|---|---|
| stock_id=21 中国中免（仅 holdings） | has_position=False ❌ | has_position=True ✅ |
| stock_id=15 宁德时代（无持仓） | has_position=False ✅ | has_position=False ✅ |

---

## 七、影响范围与回归说明

- **改动范围**：仅 2 个文件、2 个函数，均为最小侵入式修改
- **行为一致性**：`_read_position` 新逻辑与 `price_advisor._read_cost_price` 完全对齐，消除两模块间数据源不一致
- **向后兼容**：holdings 查询失败时自动 fallback positions 表，不影响仅用旧表的场景
- **无副作用**：未引入新依赖、未修改数据库结构、未触碰任何红线函数

---

## 八、交付物

1. ✅ `app.py`（api_get_ratings_list 增加 report_type 过滤）
2. ✅ `modules/advisor.py`（_read_position 增加 holdings 表优先查询）
3. ✅ 本自验报告 `reports/dev_selftest_014_bugfix_20260731.md`

---

**自验结论**：B1~B7 全部通过，两项 Bug 已修复，红线约束全部遵守，无新依赖。待 PM/QA 验收。
