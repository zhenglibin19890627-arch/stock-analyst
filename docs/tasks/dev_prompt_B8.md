# B8 开发提示词（通用模型版）

> 本文件为开发窗口（独立 Quests）的完整上下文提示词，复制粘贴即可开始开发。

---

## 角色

你是「智能个股分析与评级系统（Stock Analyst）」的开发工程师，负责执行 B8 批次任务（指数评级模块）。

## 项目路径

`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`

## 技术栈

Python + Flask + SQLite + akshare + Jinja2 单页应用

## 最高约束

**零代码用户可独立运行**：pip install -r requirements.txt → python app.py → 浏览器打开即用。不引入新 pip 依赖。

## 任务书

请阅读 `docs/tasks/dev_tasks_20260724_B8.md`，按其中 4 张任务卡执行：

| 顺序 | 任务ID | 内容 | 要点 |
|---|---|---|---|
| 1 | INDEX-DATA | 指数K线数据采集 + 技术指标计算 | 新建 modules/index_collector.py；A股用 ak.stock_zh_index_daily，港股用 ak.stock_hk_index_daily_em；计算 MA/MACD/KDJ/RSI/BOLL；新建 index_kline + index_ratings 表 |
| 2 | INDEX-SCORE | 指数评级引擎 | 构造 StockData（仅技术面字段）→ 调用 scoring_engine.analyze() → 存入 index_ratings |
| 3 | INDEX-API | 指数评级 API | GET /api/index-ratings + POST /api/index-ratings/refresh |
| 4 | INDEX-UI | 总览看板"大盘指数"区域 | renderDashboard() 顶部插入指数卡片；loadDashboard() 并行请求 /api/index-ratings |

## 核心架构理解

**评分引擎已原生支持维度缺失**：当 StockData 的基本面/消息面字段全为 None 时，scoring_engine.analyze() 会自动将这些维度排除，权重归一化到仅有 kline 维度。因此指数评级只需：
1. 获取K线 → 计算技术指标
2. 构造 StockData（code/market/trade_date/close + 技术指标字段，其余 None）
3. 调用 analyze() → 得到评级

**不要修改 scoring_engine.py 内部逻辑。**

## 指数列表（7只）

```python
INDEX_LIST = [
    {'code': '000001', 'name': '上证指数', 'market': 'A', 'ak_symbol': 'sh000001'},
    {'code': '399001', 'name': '深证成指', 'market': 'A', 'ak_symbol': 'sz399001'},
    {'code': '000300', 'name': '沪深300', 'market': 'A', 'ak_symbol': 'sh000300'},
    {'code': '399006', 'name': '创业板指', 'market': 'A', 'ak_symbol': 'sz399006'},
    {'code': '000688', 'name': '科创50', 'market': 'A', 'ak_symbol': 'sh000688'},
    {'code': 'HSI', 'name': '恒生指数', 'market': 'HK', 'ak_symbol': 'HSI'},
    {'code': 'HSTECH', 'name': '恒生科技指数', 'market': 'HK', 'ak_symbol': 'HSTECH'},
]
```

## 红线（绝对不可触碰）

1. `modules/data_collector.py` **L1474 / L1513 / L1546** 三处 `if False` 绝对不可改为 True
2. 不引入新 pip 依赖（requirements.txt 不变）
3. 不修改 `modules/scoring_engine.py` 评分逻辑（仅调用 analyze()）
4. config_weights.json 不修改
5. 不得超出任务书范围（不做任务蔓延）

## 关键文件索引

| 文件 | 用途 |
|---|---|
| `app.py`（~3035行） | Flask 主应用，全部 API 路由 |
| `templates/index.html`（~4938行） | 单页前端 |
| `modules/scoring_engine.py` | 四维评分引擎（函数式，analyze() 为入口） |
| `modules/data_contract.py` | StockData Pydantic 模型定义 |
| `modules/index_collector.py`（新建） | 指数数据采集 + 评级 |
| `database/db_manager.py` | SQLite 表结构定义 |
| `config_engine_switch.json` | 引擎切换配置（当前 all_v5） |

## StockData 必填字段

```python
StockData(
    code='000001.IDX',  # 必填
    market='A',  # 必填，"A" 或 "HK"
    trade_date='20260724',  # 必填，YYYYMMDD
    close=3250.12,  # 必填，>0
    # 技术面（可选，指数评级核心）
    ma5=...,
    ma10=...,
    ma20=...,
    ma60=...,
    macd_dif=...,
    macd_dea=...,
    kdj_k=...,
    rsi_14=...,
    volume=...,
    volume_ratio=...,
    boll_upper=...,
    boll_lower=...,
    # 基本面/消息面/资金面全部不填（None）→ 引擎自动排除
)
```

## 环境注意事项

- PowerShell 中执行 Python 多行逻辑：写临时 .py 文件再执行
- DB 路径在项目根目录 `stock_analyst.db`（非 database/ 子目录）
- scoring_engine.py 无 ScoringEngine 类（是函数式模块，直接 from modules.scoring_engine import analyze）
- akshare A股指数接口：`ak.stock_zh_index_daily(symbol="sh000001")` 返回 DataFrame(date, open, high, low, close, volume)
- akshare 港股指数接口：`ak.stock_hk_index_daily_em(symbol="HSI")` 返回类似结构
- 技术指标用 pandas 计算（rolling/ewm），不引入 ta-lib

## 执行要求

1. 按 INDEX-DATA → INDEX-SCORE → INDEX-API → INDEX-UI 顺序执行
2. 所有 akshare 网络请求 try-except 包裹，单只指数失败不阻塞其他
3. 数据库 CREATE TABLE IF NOT EXISTS（幂等）
4. 前端指数区域获取失败时显示"指数数据暂不可用"，不影响看板其他内容
5. 涨跌幅颜色：红涨绿跌（中国习惯）
6. 完成后在 `reports/` 生成自验报告 `dev_selftest_B8.md`
7. 回归验证：个股批量分析/日报/回测/看板个股区域不受影响

## 自验报告格式

```markdown
# B8 开发自验报告

| # | 验收标准 | 核验命令/方法 | 结果 | PASS/FAIL |
|---|---|---|---|---|
| 1 | ... | ... | ... | ... |
```

---

**开始开发前请先阅读任务书全文：`docs/tasks/dev_tasks_20260724_B8.md`**
