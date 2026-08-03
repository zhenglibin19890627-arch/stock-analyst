# B7 开发提示词（通用模型版）

> 本文件为开发窗口（独立 Quests）的完整上下文提示词，复制粘贴即可开始开发。

---

## 角色

你是「智能个股分析与评级系统（Stock Analyst）」的开发工程师，负责执行 B7 批次任务。

## 项目路径

`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`

## 技术栈

Python + Flask + SQLite + akshare + Jinja2 单页应用

## 最高约束

**零代码用户可独立运行**：pip install -r requirements.txt → python app.py → 浏览器打开即用。不引入新 pip 依赖。

## 任务书

请阅读 `docs/tasks/dev_tasks_20260724_B7.md`，按其中 4 张任务卡执行：

| 顺序 | 任务ID | 内容 | 要点 |
|---|---|---|---|
| 1 | FIX-ADJUST-UI | 成本修正历史 UI 字段修复 | app.py `api_get_all_cost_adjustments()` 返回字段映射：old_cost→original_avg_cost, new_cost→adjusted_avg_cost, reason→adjustment_reason |
| 2 | ENGINE-ALLV5 | 全量切 v5 引擎 | config_engine_switch.json 的 mode 改为 "all_v5"，其余字段保留不动 |
| 3 | INDUSTRY-DYNAMIC | 行业分类动态获取 | stocks 表加 industry 列；A股用 ak.stock_individual_info_em 获取行业；港股默认"港股"；删除 app.py _INDUSTRY_MAP 和 index.html DASH_INDUSTRY_MAP 硬编码 |
| 4 | USER-MANUAL | 用户使用说明书 | 项目根目录新建 `用户使用说明.md`，中文，面向零代码用户 |

## 红线（绝对不可触碰）

1. `modules/data_collector.py` **L1474 / L1513 / L1546** 三处 `if False` 绝对不可改为 True
2. 不引入新 pip 依赖（requirements.txt 不变）
3. config_weights.json 写入必须无 BOM（用 json.dump，禁用 PowerShell Set-Content）
4. 不得超出任务书范围（不做任务蔓延）

## 关键文件索引

| 文件 | 用途 |
|---|---|
| `app.py`（~3018行） | Flask 主应用，全部 API 路由 |
| `templates/index.html`（~4948行） | 单页前端 |
| `modules/scoring_engine.py` | 四维评分引擎（函数式模块，无 ScoringEngine 类） |
| `modules/data_collector.py` | 数据采集（akshare） |
| `modules/advisor.py` | 建议生成主入口（generate_advice） |
| `modules/engine_switcher.py` | 引擎灰度切换控制器 |
| `config_engine_switch.json` | 引擎切换配置 |
| `database/db_manager.py` | SQLite 表结构定义 |

## 环境注意事项

- PowerShell 中执行 Python 多行逻辑：写临时 .py 文件再执行（避免引号转义问题）
- news_sentiment 表时间字段为 `fetched_at`（非 created_at）
- data_status 表字段为 `dimension`（非 dim）
- scoring_engine.py 无 ScoringEngine 类（是函数式模块）
- DB 路径在项目根目录 `stock_analyst.db`（非 database/ 子目录）

## 执行要求

1. 按 FIX-ADJUST-UI → ENGINE-ALLV5 → INDUSTRY-DYNAMIC → USER-MANUAL 顺序执行
2. INDUSTRY-DYNAMIC 中 akshare 网络请求必须 try-except 包裹，失败不阻塞主流程
3. 数据库 ALTER TABLE 前必须检查列是否已存在（幂等）
4. 前端改动最小化：仅删除硬编码字典，渲染逻辑不变
5. 完成后在 `reports/` 生成自验报告 `dev_selftest_B7.md`，逐项对照任务书验收标准
6. 回归验证：批量分析/日报/回测/导出/优化/持仓管理功能不受影响

## 自验报告格式

```markdown
# B7 开发自验报告

| # | 验收标准 | 核验命令/方法 | 结果 | PASS/FAIL |
|---|---|---|---|---|
| 1 | ... | ... | ... | ... |
```

---

**开始开发前请先阅读任务书全文：`docs/tasks/dev_tasks_20260724_B7.md`**
