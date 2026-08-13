# 开发提示词 B19-1

**推荐模型：kimi k3（Kimi Plan）**
**任务书：docs/tasks/dev_tasks_20260726_B19.md**

---

## 你的任务

修复 analysis_results 表 analysis_date 与 daily_reports report_date 日期不对齐问题，并清理 B18-Hotfix 遗留临时脚本。

## 项目环境

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（路径含空格）
- 技术栈：Python + Flask + SQLite + akshare
- PowerShell 不支持 `&&`，用 `;` 分隔命令
- Python 多行逻辑必须写临时 .py 文件执行，不可内联 `-c`
- 中文输出需 `sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')`

## 根因

`modules/advisor.py` L448 `_save_analysis_results_for_v5` 函数中：
```python
score_date = analysis.get('score_date', rating_time[:10])
```
v5 引擎返回的 `score_date` 是 K线最新交易日（如周五 07-24），而 daily_reports 的 `report_date` 是当天日期（如周日 07-26）。非交易日时两表日期不对齐。

## 修改要求（严格按任务书执行）

### 修改1：modules/advisor.py

**L651** 函数签名增加 report_date 参数：
```python
# 原：def generate_advice(stock_id):
# 改：def generate_advice(stock_id, report_date=None):
```

**L439** 函数签名增加 report_date 参数：
```python
# 原：def _save_analysis_results_for_v5(stock_id, analysis, operation_suggestion=''):
# 改：def _save_analysis_results_for_v5(stock_id, analysis, operation_suggestion='', report_date=None):
```

**L448** score_date 逻辑修改：
```python
# 原：score_date = analysis.get('score_date', rating_time[:10])
# 改：
if report_date:
    score_date = report_date
else:
    score_date = analysis.get('score_date', rating_time[:10])
```

**L677** 调用处传入 report_date：
```python
# 原：_save_analysis_results_for_v5(stock_id, analysis, '')
# 改：_save_analysis_results_for_v5(stock_id, analysis, '', report_date=report_date)
```

### 修改2：modules/daily_report.py

**L444** 调用 generate_advice 时传入 target_date：
```python
# 原：advice = generate_advice(stock_id)
# 改：advice = generate_advice(stock_id, report_date=target_date)
```

### 修改3：删除临时脚本

删除项目根目录下以下 26 个临时脚本：
```
_batch_rescore.py
_check_002230.py, _check_002230_dims.py, _check_002230_fund.py, _check_002230_tech.py
_check_002415.py, _check_002415_capital.py, _check_002415_dims.py, _check_002415_tech.py
_check_300750.py, _check_300750_capital.py, _check_300750_full.py
_check_65plus_main.py, _check_all_vp.py, _check_db_scores.py, _check_dr_schema.py
_check_ids.py, _check_kdj_range.py, _check_main_range.py, _check_north_range.py
_check_rh_0724.py, _check_rh_dates.py, _check_rh_detail.py, _check_rh_latest.py, _check_rh_status.py
_check_scores.py
```

## 红线（绝对禁止）

1. **data_collector.py** L1645/L1684/L1717 三处 `if False` 不可修改
2. **config_weights.json** rating_mapping 80/65/50/30 不可修改
3. **不引入**新 pip 依赖
4. **不修改** scoring_engine.py / backtest_engine.py / app.py / templates/index.html

## 自验要求

修改完成后，执行以下自验：

1. **force 重跑日报**验证两表对齐：
   ```python
   from modules.daily_report import generate_daily_report

   generate_daily_report(force=True)
   ```

2. **SQL 核验**两表日期+分数一致性（27/27 对齐）

3. **Grep 核验**红线守恒（data_collector 三处 if False）

4. **确认**临时脚本已清零（`Get-ChildItem _*.py` 返回空）

自验报告归档至 `reports/dev_selftest_B19.md`。
