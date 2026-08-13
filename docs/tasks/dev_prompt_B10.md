# B10 开发提示词：数据完整度提升

## 你的角色

你是「智能个股分析与评级系统」的开发工程师，负责执行 B10 批次任务（数据完整度提升）。

## 项目信息

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`
- 技术栈：Python + Flask + SQLite + akshare + Jinja2
- 任务书：`docs/tasks/dev_tasks_20260724_B10.md`
- 数据库：项目根目录 `stock_analyst.db`（SQLite）

## 当前问题

v5 评分引擎数据完整度不足：
- 技术面 100%（无需处理）
- **基本面 22%~78%**：多数股票仅有 PE/PB，缺少 gross_margin、ocf_to_profit 等
- **资金面 67%**：北向资金和两融数据近乎失效
- **消息面 50%（硬顶）**：`holder_increase` 在 data_adapter.py L388 硬编码为 None

## 任务分解（按顺序执行）

---

### 子任务 1：B10-FUNDAMENTAL（P0）— 基本面字段补全

**目标**：A 股基本面完整度从 22% 提升至 ≥78%

**步骤**：

1. **验证 akshare 接口可用性**（先测试再写代码）：
```python
import akshare as ak

# 测试财务分析指标接口
df = ak.stock_financial_analysis_indicator(symbol='000858', start_year='2024')
print(df.columns.tolist())
print(df.head(2))
```
确认能获取：净资产收益率、销售毛利率、主营业务收入增长率、净利润增长率、资产负债率、流动比率、经营现金流/净利润 等字段。

如果该接口不可用，尝试：
```python
df = ak.stock_financial_abstract_ths(symbol='000858')
```

2. **在 `modules/data_collector.py` 新增函数**：
```python
def fetch_fundamental_detail(symbol: str) -> dict:
    """调用 akshare 财务分析指标接口，补全基本面字段

    Returns:
        dict: {roe, gross_margin, revenue_yoy, net_profit_yoy,
               ocf_to_profit, debt_to_asset, current_ratio}
        失败时返回空 dict
    """
```

3. **在 `collect_stock_data()` 的基本面采集流程中集成**：
   - 先执行现有的 `stock_individual_info_em` 获取 PE/PB
   - 再调用 `fetch_fundamental_detail()` 补全其余字段
   - 写入 `raw_fundamental` 表（表结构已有这些列，无需改表）
   - **关键**：接口失败时保留已有值，不覆盖为 None

4. **自验**：
```python
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
c = conn.cursor()
c.execute(
    'SELECT pe_ratio, pb_ratio, roe, gross_margin, revenue_growth, profit_growth, ocf_to_net_profit, debt_ratio, current_ratio FROM raw_fundamental WHERE stock_id=24 ORDER BY report_date DESC LIMIT 1'
)
print(c.fetchone())
# 期望：至少 7/9 个字段非 None
conn.close()
```

---

### 子任务 2：B10-HOLDER（P1）— 股东增减持接入

**目标**：消息面完整度从 50% 提升至 100%（有增减持数据时）

**步骤**：

1. **验证 akshare 接口可用性**：
```python
import akshare as ak

# 方案1：雪球内部交易
df = ak.stock_inner_trade_xq(symbol='000858')
print(df.columns.tolist())
print(df.head())

# 方案2（备选）：东方财富股东增减持
# df = ak.stock_ggcg_em(symbol="000858")
```

2. **在 `modules/data_collector.py` 新增函数**：
```python
def fetch_holder_increase(symbol: str) -> bool | None:
    """获取近30天大股东/高管增减持信息
    
    Returns:
        True=有增持, False=有减持, None=无记录或接口不可用
    """
```

3. **存储方案**（选最简方案）：
   - 方案 A：在 `raw_fundamental` 表新增 `holder_increase` 列（ALTER TABLE）
   - 方案 B：新建 `raw_holder_change` 表
   - 推荐方案 A（改动最小）

4. **修改 `modules/data_adapter.py`**：
   - 找到 L388 附近的 `holder_increase=None,  # 数据库暂无此字段`
   - 改为从数据库读取实际值：
```python
# 读取 holder_increase
holder_increase = None
if fund and 'holder_increase' in fund:
    holder_increase = fund.get('holder_increase')  # True/False/None
```

5. **在 `collect_stock_data()` 中集成调用**（仅 A 股）

6. **自验**：
```python
# 批量分析一只A股后检查
from modules.data_adapter import load_stockdata_from_db

data = load_stockdata_from_db(24)  # 000858
print(f'holder_increase = {data.holder_increase}')
print(f'news completeness = {data.data_quality.news}')
# 期望：holder_increase 为 True/False/None，news 完整度 > 50%
```

---

### 子任务 3：B10-CAPITAL-LABEL（P2）— 资金面受限标注

**目标**：用户能看到资金面数据受限的说明

**推荐方案 B**（改动最小）：

在 `modules/scoring_engine.py` 的 `analyze()` 函数中，当检测到 `north_net_buy` 和 `margin_balance_chg` 均为 None 时，在 `data_warnings` 列表中追加：
```python
"资金面提示：北向资金/两融数据源暂不可用，当前评分仅基于主力资金流向"
```

**注意**：`data_warnings` 已有展示通道（前端报告页已渲染此字段），无需改前端。

**自验**：分析一只股票后检查返回结果的 `data_warnings` 是否包含该提示。

---

## 红线（绝对不可触碰）

1. **不修改** `data_collector.py` L1474/L1513/L1546 三处 `if False` 块
2. **不新增** pip 依赖（仅使用 akshare 已有接口）
3. **不修改** `config_weights.json`
4. **不修改** `scoring_engine.py` 的评分逻辑/权重计算（仅允许在 data_warnings 追加文字）
5. 零代码用户流程不变：`pip install -r requirements.txt → python app.py → 浏览器打开即用`

## 环境注意事项

- 项目路径含空格（`Qoder cn`），PowerShell 中需引号包裹
- PowerShell 不支持 `&&`，用 `;` 分隔命令
- 多行 Python 逻辑写临时 .py 文件再执行（避免引号转义）
- `news_sentiment` 表时间字段为 `fetched_at`（非 created_at）
- `scoring_engine.py` 无 `ScoringEngine` 类（函数式模块，`analyze()` 为入口）
- akshare 接口可能因网络/版本变化不可用，**先验证再编码**，不可用时静默降级

## 交付物

1. 修改后的代码文件
2. 自验报告：包含每个子任务的验证结果（基本面完整度数值、holder_increase 值、data_warnings 内容）
3. 如发现 akshare 接口不可用，说明替代方案或降级处理
