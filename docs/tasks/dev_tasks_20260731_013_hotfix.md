# 开发任务书 DEV-TASKS-20260731-013-Hotfix

## 任务：盘中快报 Hotfix（按钮可见性 + 列表去重）

| 项 | 内容 |
|---|---|
| 编号 | DEV-TASKS-20260731-013-Hotfix |
| 关联 | DEV-TASKS-20260731-013 / QA-TASKS-20260731-013 |
| 签发日期 | 2026-07-31 |
| 签发人 | PM |
| 推荐模型 | qwen3.8 / glm5.2（并列优先） |
| 窗口类型 | Quests 独立窗口 |
| 执行模式 | 智能体（单代理） |
| 来源 | QA 验收 Q20 FAIL + 附注问题 |

---

## 角色定义

你是本项目的 **开发人员**，职责为：独立编码 + 自验。

**独立性原则**：开发不负责正式验收（只做自验），不修改需求基线，不触碰红线。

---

## 项目背景摘要

| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db` |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 最高约束 | 零代码用户可独立运行，无新依赖 |

---

## 问题描述

### 问题 1：盘中快报按钮在报告列表视图中不可见（Q20 FAIL）

**现象**：当有报告数据时，切换到"每日报告"标签会调用 `loadLatestDailyReport()` → `renderDailyReportList()`，该函数重建 `dailyContent.innerHTML` 时仅恢复了"🚀 生成今日报告"按钮，**遗漏了"📊 盘中快报"按钮**。用户看不到快报入口。

**定位**：`templates/index.html` L4458-4461

```javascript
// 当前代码（缺少盘中快报按钮）
html += '<div class="report-actions">';
html += '<button class="report-back-btn" onclick="generateDailyReport()">🚀 生成今日报告</button>';
html += '<span style="color:#888;font-size:13px;">最新报告日期：' + reportDate + '</span>';
html += '</div>';
```

**修复**：在"生成今日报告"按钮之后增加盘中快报按钮：

```javascript
html += '<div class="report-actions">';
html += '<button class="report-back-btn" onclick="generateDailyReport()">🚀 生成今日报告</button>';
html += '<button class="report-back-btn" onclick="generateIntradayReport()" style="background:#f39c12;color:#fff;margin-left:10px;">📊 盘中快报</button>';
html += '<span style="color:#888;font-size:13px;margin-left:15px;">最新报告日期：' + reportDate + '</span>';
html += '</div>';
```

### 问题 2：latest 列表返回 daily+intraday 混合记录

**现象**：`GET /api/daily-report/latest` 调用 `get_latest_reports()`，当同一天同时存在 daily 和 intraday 记录时，返回 54 条（27 daily + 27 intraday），前端列表每只股票显示两次。

**定位**：`modules/daily_report.py` L851-869 `get_latest_reports()`

**修复逻辑**：优先返回 daily；若当天无 daily 仅有 intraday，则返回 intraday。

```python
def get_latest_reports():
    """获取最新一期报告列表（013-Hotfix：优先 daily，无 daily 时取 intraday）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT MAX(report_date) as latest_date FROM daily_reports
    """)
    row = cursor.fetchone()
    if not row or not row['latest_date']:
        conn.close()
        return {'success': True, 'report_date': None, 'reports': []}

    latest_date = row['latest_date']

    # 013-Hotfix: 优先取 daily，无 daily 时取 intraday
    cursor.execute(
        """
        SELECT * FROM daily_reports
        WHERE report_date = ? AND report_type = 'daily'
        ORDER BY total_score DESC
    """,
        (latest_date,),
    )
    reports = [dict(r) for r in cursor.fetchall()]

    if not reports:
        cursor.execute(
            """
            SELECT * FROM daily_reports
            WHERE report_date = ? AND report_type = 'intraday'
            ORDER BY total_score DESC
        """,
            (latest_date,),
        )
        reports = [dict(r) for r in cursor.fetchall()]

    conn.close()
    return {'success': True, 'report_date': latest_date, 'reports': reports}
```

**同步修复** `get_reports_by_date()`（L872-881）：应用相同逻辑（优先 daily，无则 intraday）。

---

## 改动范围

| 文件 | 改动 |
|---|---|
| `templates/index.html` | `renderDailyReportList` 增加盘中快报按钮（1行） |
| `modules/daily_report.py` | `get_latest_reports()` + `get_reports_by_date()` 增加 report_type 过滤 |

**不涉及**：DB 迁移、app.py、红线文件、requirements.txt

---

## 红线约束

与 013 主任务书一致，此处不重复。特别注意：
- 不修改 `advisor.py`、`scoring_engine.py`
- 不新增依赖
- 不改动 012 日志/超时配置

---

## 自验清单

| # | 验证项 | 方法 |
|---|---|---|
| H1 | 按钮可见 | 启动 Flask → 浏览器打开 → 点击"每日报告"标签 → 有报告数据时可见"📊 盘中快报"按钮 |
| H2 | 按钮可触发 | 点击"盘中快报"按钮 → 正常调用 API 并展示结果 |
| H3 | latest 去重 | 确保当天同时有 daily+intraday 时，`GET /api/daily-report/latest` 仅返回 27 条（daily） |
| H4 | 仅 intraday 时正常 | 删除当天 daily（或在新日期仅生成 intraday），latest 返回 intraday 27 条 |
| H5 | 现有功能回归 | `GET /api/ratings`、`GET /api/stocks/<id>/report-latest` 正常 |
| H6 | 零依赖 | requirements.txt 无变化 |

---

## 交付物

1. 修改后的代码文件
2. 自验报告 `reports/dev_selftest_013_hotfix_20260731.md`（含 H1~H6 逐项结果）

---

## 环境注意事项

- PowerShell 不支持 `&&`，用 `;` 代替
- 项目路径含空格，需引号包裹
- Python 多行逻辑写临时 .py 文件执行
