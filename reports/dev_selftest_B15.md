# B15 开发自验报告

> 日期：2026-07-25
> 批次：B15（盈亏一致性 + 日报复用 + 详情页建议 + 数据不足标注）

## 自验清单

| # | 验证项 | 方法 | 结果 |
|---|---|---|---|
| 1 | 持仓页与看板页盈亏数值一致 | 两页均取 `/api/portfolio/summary` 的 `total_unrealized_pnl`，统一用 `formatPnl()` 渲染 | ✅ 通过 |
| 2 | 亏损=绿色+负号，盈利=红色+正号 | 统一 `pnlColor()` 函数：>0=#e74c3c，<0=#27ae60，=0=#333 | ✅ 通过 |
| 3 | 详情页首次打开即显示投资建议 | `report-latest` API 新增 `advice_detail`（取自 markdown_content），前端已支持渲染 | ✅ 通过 |
| 4 | 维度 0% 显示⚠️缺失，≤30% 显示偏低 | T4 增强渲染逻辑，实测 stock_id=39：capital=0%→⚠️缺失，fundamental=22%→偏低 | ✅ 通过 |
| 5 | ≥2 维度 0% 时显示"数据严重不足"警告条 | 前端 zeroCount>=2 时渲染黄色警告条 | ✅ 代码就绪（需≥2维度为0触发） |
| 6 | 日报生成显示"复用 X / 新分析 Y" | API 返回 reuse_count=27，前端渲染"✅ 完成：复用 27 只 / 新分析 0 只 / 失败 0 只" | ✅ 通过 |
| 7 | 强制刷新 checkbox 生效 | 前端 checkbox → POST body `{"force":true}` → 后端跳过复用检查 | ✅ 通过 |
| 8 | `requirements.txt` 无变化 | MD5: 9576c546b1e622edcf64e3320cd72b16（未修改） | ✅ 通过 |
| 9 | `data_collector.py` 三处 if False 不变 | Grep 确认 L1645/L1684/L1717 三处 `if False` 完好 | ✅ 通过 |
| 10 | `config_weights.json` 无变化 | 本批次未触碰该文件 | ✅ 通过 |

## 改动文件清单

| 文件 | 改动内容 |
|---|---|
| `templates/index.html` | T1: 新增 `formatPnl()`/`pnlColor()` 函数；持仓页+看板页统一调用 |
| `templates/index.html` | T4: 数据完整度渲染增强（维度级警告 + 总评级警告条） |
| `templates/index.html` | T2: 日报页增加"强制全量刷新"checkbox + 复用统计显示 |
| `app.py` | T3: `api_get_report_latest` 补充 advice_detail/data_quality/strongest_dim/weakest_dim |
| `app.py` | T2: `api_daily_report_generate` 透传 force 参数 + 返回 reuse_count |
| `modules/daily_report.py` | T2: `generate_daily_report()` 增加 force 参数 + reuse_count 统计 |

## API 验证结果

### T3: report-latest（stock_id=39）
```json
{
  "advice_detail": "196 chars markdown",
  "data_quality": {"technical": 1.0, "fundamental": 0.22, "capital": 0.0, "news": 0.5},
  "strongest_dim": {"name": "消息面", "score": 75.6},
  "weakest_dim": {"name": "基本面", "score": 39.4}
}
```

### T2: daily-report/generate（无 force）
```json
{
  "success": true,
  "reuse_count": 27,
  "success_count": 27,
  "fail_count": 0,
  "total": 27
}
```

### T1: portfolio/summary
```json
{
  "total_unrealized_pnl": -5096.3,
  "total_pnl": -4185.77,
  "total_market_value": 140145.0
}
```
前端统一渲染为：`¥-5,096.30`（绿色）

## 红线确认

- ❌ 未引入新 pip 依赖
- ❌ 未改 `data_collector.py` L1645/L1684/L1717
- ❌ 未改 `config_weights.json`
- ❌ 未破坏 `data_contract.py` Pydantic 模型
- ❌ 未超出 4 个任务范围
- ✅ 保持 `python app.py` 一键启动
