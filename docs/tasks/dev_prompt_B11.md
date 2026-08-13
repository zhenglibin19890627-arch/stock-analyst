# B11 开发提示词：数据一致性 + 流程去重 + 体验修复

## 你的角色

你是「智能个股分析与评级系统」的开发工程师，负责执行 B11 批次（5 个子任务）。

## 项目信息

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`
- 技术栈：Python + Flask + SQLite + akshare + Jinja2
- 数据库：项目根目录 `stock_analyst.db`
- 任务书：`docs/tasks/dev_tasks_20260725_B11.md`

## 按顺序执行以下 5 个子任务

---

### 子任务 1：B11-PNL-SIGN（P0）— 盈亏符号两页矛盾

**现象**：持仓页显示"总浮动盈亏 5116.30"（绿色无负号），看板页显示"¥-5116.30 (-3.62%)"。

**排查步骤**：
1. 找到持仓页的盈亏计算 API（搜索 `app.py` 中 `holdings` 或 `position` 相关路由）
2. 找到看板页的盈亏计算 API（搜索 `portfolio/summary` 或 `watchlist-scores`）
3. 对比两处 SQL/计算公式，找出符号差异
4. 常见原因：
   - 一处用 `latest_price - cost_price`，另一处用 `cost_price - latest_price`
   - 格式化时 `abs()` 丢失了负号
   - 前端颜色判断条件不一致

**修复**：统一计算公式为 `(latest_price - cost_price) * quantity`，前端颜色统一：正数红色(+)、负数绿色(-)、零灰色。

**自验**：浏览器打开持仓页和看板页，确认同一股票的盈亏数值/符号/颜色完全一致。

---

### 子任务 2：B11-SCORE-SYNC（P0）— 跨页面评分同源

**现象**：自选股评级列表读 `analysis_results` 表，看板/日报读 `daily_reports` 表，11/27 只评分不一致。

**修复方案**：

在 `app.py` 中找到 `/api/ratings` 路由（自选股评级列表的数据接口），将其 SQL 查询从 `analysis_results` 改为 `daily_reports`：

```python
# 修改前（大致逻辑）：
SELECT ... FROM analysis_results WHERE stock_id = ...

# 修改后：读 daily_reports 最新一期
SELECT dr.stock_code, dr.stock_name, dr.total_score, dr.rating, dr.rating_label,
       dr.engine_version, dr.generated_at
FROM daily_reports dr
WHERE dr.report_date = (SELECT MAX(report_date) FROM daily_reports)
  AND dr.status = 'ok'
```

注意：`/api/ratings` 返回的字段名可能需要适配（前端期望的 key 名不变，只改数据来源）。

**自验**：
```python
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
c = conn.cursor()
# 对比两表最新评分
c.execute(
    "SELECT stock_code, total_score, rating FROM daily_reports WHERE report_date=(SELECT MAX(report_date) FROM daily_reports) AND status='ok' ORDER BY stock_code"
)
dr = {r[0]: (r[1], r[2]) for r in c.fetchall()}
c.execute(
    'SELECT s.symbol, ar.total_score, ar.rating FROM analysis_results ar JOIN stocks s ON ar.stock_id=s.id ORDER BY s.symbol'
)
ar = {r[0]: (r[1], r[2]) for r in c.fetchall()}
diff = [(k, dr.get(k), ar.get(k)) for k in dr if dr.get(k) != ar.get(k)]
print(f'差异数: {len(diff)}')  # 修复后应为 0（因为 /api/ratings 改读 daily_reports）
conn.close()
```

---

### 子任务 3：B11-REPORT-REUSE（P1）— 日报去重复执行

**现象**：日报生成对 27 只股票重新执行 collect_stock_data + generate_advice（8.5 分钟），与批量分析完全重复。

**修改文件**：`modules/daily_report.py`，`generate_daily_report()` 函数

**修改逻辑**：在每只股票的 `collect_stock_data` + `generate_advice` 之前，检查当日是否已有有效报告：

```python
# 在 for stock in stocks 循环内，collect_stock_data 之前加入：
conn_check = get_connection()
cursor_check = conn_check.cursor()
cursor_check.execute(
    'SELECT total_score, rating, rating_label, engine_version, key_factors, '
    'data_warnings, markdown_content, generated_at '
    'FROM daily_reports WHERE stock_id=? AND report_date=? AND status="ok"',
    (stock_id, target_date),
)
existing = cursor_check.fetchone()
conn_check.close()

if existing:
    # 今日已有有效报告，直接使用已有数据，跳过采集+分析
    logger.info(f'[{symbol}] 今日已有有效报告，跳过采集+分析')
    # 用 existing 数据组装日报条目（与正常路径输出格式一致）
    # ... 组装 report_entry ...
    continue

# 否则走原有流程：collect_stock_data + generate_advice
```

**关键**：跳过时仍需正确组装日报条目（total_score/rating/key_factors/markdown_content 等从 existing 读取），确保日报输出完整。

**自验**：
1. 先执行批量分析（确保 daily_reports 有今日数据）
2. 再点"生成日报"
3. 计时：应 ≤30 秒完成（仅格式化，不重新采集）
4. 日报内容与批量分析结果一致

---

### 子任务 4：B11-API-DEDUP（P1）— 接口去重/缓存

**问题 A**：`stock_financial_analysis_indicator` 单股调 2 次

**修改**：在 `collect_stock_data()` 中，`fetch_fundamental_detail` 调用前检查 `fetch_a_fundamental` 是否已获取到足够数据：

```python
# 在 collect_stock_data() 中，fetch_a_fundamental 之后：
# 检查 raw_fundamental 最新行非空字段数
cursor.execute(
    """
    SELECT roe, gross_margin, revenue_growth, profit_growth, 
           ocf_to_net_profit, debt_ratio, current_ratio
    FROM raw_fundamental WHERE stock_id=? ORDER BY report_date DESC LIMIT 1
""",
    (stock_id,),
)
row = cursor.fetchone()
if row:
    non_null = sum(1 for v in row if v is not None)
    if non_null >= 5:  # 已有足够数据，跳过重复调用
        logger.info(f'[{symbol}] 基本面已有{non_null}个字段，跳过 fetch_fundamental_detail')
    else:
        detail = fetch_fundamental_detail(symbol)
        _apply_fundamental_detail(stock_id, detail)
else:
    detail = fetch_fundamental_detail(symbol)
    _apply_fundamental_detail(stock_id, detail)
```

**问题 B**：`stock_inner_trade_xq()` 批量时调 27 次

**修改**：在批量分析入口（`app.py` 的 `/api/stocks/batch-analyze` 路由）预取一次，通过模块级缓存传递：

```python
# 在 data_collector.py 顶部增加缓存变量
_holder_cache = None
_holder_cache_time = None


def fetch_holder_increase(symbol: str, preloaded_data=None):
    """B11: 支持预加载数据，避免重复调用全市场接口"""
    global _holder_cache, _holder_cache_time
    import time

    if preloaded_data is not None:
        df = preloaded_data
    else:
        # 检查缓存（10分钟内有效）
        if (
            _holder_cache is not None
            and _holder_cache_time
            and (time.time() - _holder_cache_time) < 600
        ):
            df = _holder_cache
        else:
            import akshare as ak

            df = ak.stock_inner_trade_xq()
            _holder_cache = df
            _holder_cache_time = time.time()

    # 从 df 中按 symbol 过滤...（原有逻辑）
```

在批量分析入口：
```python
# batch-analyze 路由开头预取一次
from modules.data_collector import fetch_holder_increase
import akshare as ak

try:
    holder_df = ak.stock_inner_trade_xq()
except:
    holder_df = None
# 后续每只股票传入 holder_df
```

**自验**：批量分析 10 只股票，日志中 `stock_inner_trade_xq` 仅出现 1 次。

---

### 子任务 5：B11-DETAIL-LOAD（P1）— 详情页首次加载

**现象**：详情页首次打开时"投资建议详情"为空，需点"刷新报告"才有内容。

**排查**：找到详情页数据 API（`/api/stocks/<id>/report` 或 `/api/stocks/<id>/analyze`），看首次加载时读什么数据源。

**修复方案**：详情页 API 加载时，如果 `daily_reports` 无当日记录，自动触发 `generate_advice(stock_id)` 并返回结果（对用户透明，无需手动刷新）：

```python
# 在详情页 API 中：
cursor.execute(
    'SELECT * FROM daily_reports WHERE stock_id=? AND report_date=? AND status="ok"',
    (stock_id, today),
)
if not cursor.fetchone():
    # 无今日报告，自动触发分析（静默）
    from modules.advisor import generate_advice

    advice = generate_advice(stock_id)
    # 写入 daily_reports 后返回
```

**自验**：重启应用后，直接点击某只股票进入详情页，投资建议应直接显示（无需点刷新）。

---

## 红线（绝对不可触碰）

1. **不修改** `data_collector.py` L1630/L1669/L1702 三处 `if False`
2. **不新增** pip 依赖
3. **不修改** `config_weights.json`
4. **不修改** `scoring_engine.py` 评分逻辑/权重计算
5. 零代码用户流程不变

## 环境注意事项

- 项目路径含空格（`Qoder cn`），PowerShell 需引号
- PowerShell 不支持 `&&`，用 `;`
- 多行 Python 写临时 .py 文件执行
- `news_sentiment` 时间字段为 `fetched_at`
- DB 路径：项目根目录 `stock_analyst.db`

## 交付物

1. 修改后的代码文件
2. 每个子任务的自验结果
3. 如发现某问题根因与任务书描述不同，说明实际根因和修复方式
