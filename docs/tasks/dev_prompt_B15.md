# B15 开发提示词（Dev Prompt）

> 本文件供开发窗口（另一个 Quests）直接使用。请严格按任务书 `docs/tasks/dev_tasks_20260725_B15.md` 执行。

## 项目路径

```
C:\Users\zlb19\Desktop\Qoder cn\stock_analyst
```

## 环境约束（必须遵守）

- Python 路径：`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`
- PowerShell 不支持 `&&`，用 `;` 分隔
- 项目路径含空格，PowerShell 中需引号包裹
- 多行 Python 逻辑必须写临时 .py 文件再执行（避免引号转义）
- 不引入新 pip 依赖
- `data_collector.py` L1645/L1684/L1717 三处 `if False` 绝对不可改
- `config_weights.json` 本批次不修改

---

## T1（P0）盈亏显示一致性修复

### 问题根因

持仓页和看板页渲染盈亏的**格式不统一**：

| 位置 | 代码行 | 当前渲染方式 |
|---|---|---|
| 持仓页 `loadPortfolioSummary()` | `index.html` L2507-2535 | `sign + Math.abs(v).toLocaleString(...)` → 显示 `-5,116.30`（无¥前缀） |
| 看板页 `renderDashboard()` | `index.html` L4196-4201 | `(pnl>=0?'+':'') + formatCNY(pnl)` → 显示 `¥-5,116.30`（有¥前缀） |

用户看到两处数字格式不同、颜色可能不一致，产生"到底赚没赚"的困惑。

### 修复要求

1. **统一格式**：两处均使用 `formatPnl(value)` 函数（新建），输出格式：`¥+5,116.30` 或 `¥-5,116.30`
2. **统一颜色**：盈利（>0）= 红色 `#e74c3c`，亏损（<0）= 绿色 `#27ae60`，持平 = `#333`
3. **统一数据源**：两处均取自 `/api/portfolio/summary` 返回的 `total_unrealized_pnl` 字段

### 具体改动

**`templates/index.html`**：

1. 在 `formatCNY` 函数（L1267）附近新增：
```javascript
function formatPnl(value) {
    if (value === null || value === undefined || isNaN(value)) return '--';
    var sign = value > 0 ? '+' : '';
    return '¥' + sign + Number(value).toLocaleString('zh-CN', {
        minimumFractionDigits: 2, maximumFractionDigits: 2
    });
}
function pnlColor(value) {
    if (value === null || value === undefined || isNaN(value)) return '#999';
    return value > 0 ? '#e74c3c' : value < 0 ? '#27ae60' : '#333';
}
```

2. **持仓页** L2507-2535：将 `sumUnrealized` 和 `sumTotalPnl` 的渲染改为：
```javascript
var uEl = document.getElementById('sumUnrealized');
if (uEl) {
    uEl.textContent = formatPnl(data.total_unrealized_pnl);
    uEl.style.color = pnlColor(data.total_unrealized_pnl);
}
```

3. **看板页** L4196-4201：将浮动盈亏卡片改为：
```javascript
var pnl = s.total_unrealized_pnl;
html += '<div class="dash-card"><div class="dash-label">浮动盈亏</div>';
html += '<div class="dash-value" style="color:' + pnlColor(pnl) + ';">' + formatPnl(pnl) + '</div>';
```

4. **持仓列表每行**（L2577 附近的 `_fmtPnl`）：确认也使用统一颜色规则。

### 验证

- 启动服务后，持仓页"总浮动盈亏"与看板页"浮动盈亏"数值完全一致
- 亏损 = 绿色 + 负号，盈利 = 红色 + 正号

---

## T2（P1）日报生成复用批量分析结果

### 现状分析

`daily_report.py` L394-432 已有 B11 复用逻辑：检查 `daily_reports` 表当日是否有 `status="ok"` 的记录。

**但问题是**：前端"批量分析"调用的是 `/api/stocks/{id}/analyze`，该接口触发 `generate_advice()` 后**是否写入 `daily_reports` 表**需要确认。

### 修复要求

1. 确认 `/api/stocks/{id}/analyze`（app.py 中搜索 `analyze`）是否将结果写入 `daily_reports` 表
   - **如果已写入**：复用逻辑已生效，只需在前端显示复用统计
   - **如果未写入**：在 `generate_daily_report()` 中增加第二层检查——查 `analysis_results` 表当日记录

2. **前端显示复用统计**：
   - `daily_report.py` 的 `generate_daily_report()` 返回值中新增 `reuse_count` 字段
   - `app.py` L2718 的 jsonify 中透传 `reuse_count`
   - 前端 `generateDailyReport()`（index.html L3966）在生成完成后显示：`"✅ 完成：复用 X 只 / 新分析 Y 只 / 失败 Z 只"`

3. **强制刷新选项**：
   - 前端"生成今日报告"按钮旁增加 checkbox：`☐ 强制全量刷新（忽略已有结果）`
   - 勾选后 POST body 传 `{"force": true}`
   - `daily_report.py` 接收 `force` 参数，为 True 时跳过复用检查

### 涉及文件

- `modules/daily_report.py`：`generate_daily_report(target_date=None, force=False)` 增加 force 参数 + reuse_count 统计
- `app.py` L2708-2730：透传 force 参数和 reuse_count
- `templates/index.html` L3966-3987：前端 checkbox + 结果统计显示

---

## T3（P1）详情页投资建议默认展示

### 问题根因

详情页加载逻辑（index.html L3743-3760）：
1. 先调 `/api/stocks/{id}/report-latest`（从 daily_reports 读取）
2. 该 API（app.py L736-853）返回的结构**缺少以下字段**：
   - `advice_detail`（综合分析文本）
   - `position_advice`（仓位建议）
   - `strongest_dim` / `weakest_dim`（维度亮点）
   - `data_quality`（数据完整度）

3. 前端 `renderFullReport()`（L3886-3920）检查 `adviseData.advice_detail`，为 undefined 时"投资建议详情"区域为空白。

### 修复方案

**方案 A（推荐）**：在 `daily_reports` 表中存储 `markdown_content` 字段（已有），从中提取或直接在 `report-latest` API 返回时补充建议字段。

**具体改动**：

1. **`app.py` L830-851**（`api_get_report_latest` 返回结构）：
   - 从 `daily_reports` 表的 `markdown_content` 字段解析出 `advice_detail`
   - 或者：在 `generate_advice()` 返回时，将 `advice_detail` 等字段也写入 `daily_reports` 表（新增列或存入 JSON 字段）
   - 最简方案：`report-latest` 返回时，调用 `modules/advisor.py` 中的建议生成函数，基于已有评分数据重建建议文本（不重新采集）

2. **最简实现路径**：
   - `daily_reports` 表已有 `markdown_content` 列（存储完整报告 markdown）
   - 在 `report-latest` API 中，将 `markdown_content` 作为 `advice_detail` 返回
   - 前端 L3890 已能渲染 `adviseData.advice_detail`

3. **补充 `data_quality` 字段**：
   - 在 `report-latest` 返回结构中增加 `data_quality`（从 `daily_reports` 表或重新计算）
   - 若表中无此字段，可从 `key_factors` JSON 中提取各维度 status 推算

### 涉及文件

- `app.py` L736-853：`api_get_report_latest` 补充返回字段
- `templates/index.html`：可能无需改动（前端已支持渲染这些字段）

---

## T4（P2）数据不足醒目标注

### 当前代码

`index.html` L3849-3857：
```javascript
if (adviseData.data_quality) {
    var dq = adviseData.data_quality;
    html += '<div class="rating-time" style="font-size:11px;color:#aaa;">' +
            '数据完整度：技术' + Math.round(dq.technical * 100) + '% ' +
            '基本' + Math.round(dq.fundamental * 100) + '% ' +
            '资金' + Math.round(dq.capital * 100) + '% ' +
            '消息' + Math.round(dq.news * 100) + '%' +
            '</div>';
}
```

### 修复要求

1. **维度级警告**（完整度 = 0%）：在该维度百分比后追加 `⚠️` 标记，颜色改为橙色
2. **维度级提示**（完整度 ≤ 30%）：追加 `偏低` 标记
3. **总评级级警告**（≥2 个维度 = 0%）：在评级 badge 旁追加醒目提示条

### 具体改动

**`templates/index.html`** L3849-3857 替换为：

```javascript
if (adviseData.data_quality) {
    var dq = adviseData.data_quality;
    var dims = [
        {name:'技术', val: dq.technical},
        {name:'基本', val: dq.fundamental},
        {name:'资金', val: dq.capital},
        {name:'消息', val: dq.news}
    ];
    var zeroCount = 0;
    var dqHtml = '数据完整度：';
    dims.forEach(function(d) {
        var pct = Math.round(d.val * 100);
        if (pct === 0) {
            dqHtml += '<span style="color:#e67e22;font-weight:600;">' + d.name + ' 0% ⚠️缺失</span> ';
            zeroCount++;
        } else if (pct <= 30) {
            dqHtml += '<span style="color:#f39c12;">' + d.name + ' ' + pct + '% 偏低</span> ';
        } else {
            dqHtml += d.name + ' ' + pct + '% ';
        }
    });
    html += '<div class="rating-time" style="font-size:11px;color:#aaa;">' + dqHtml + '</div>';

    // 总评级警告（≥2个维度为0%）
    if (zeroCount >= 2) {
        html += '<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:8px 12px;margin-top:8px;font-size:13px;color:#856404;">' +
                '⚠️ 数据严重不足（' + zeroCount + '个维度缺失），评级仅供参考，不建议作为操作依据</div>';
    }
}
```

### 涉及文件

- `templates/index.html`：详情页数据完整度渲染区域
- 前提：T3 修复后 `report-latest` API 需返回 `data_quality` 字段

---

## 执行顺序建议

```
T1（独立，纯前端）→ T4（依赖 T3 的 data_quality）→ T3（后端+前端）→ T2（后端+前端）
```

推荐顺序：**T1 → T3 → T4 → T2**

理由：T3 修复后 report-latest 返回 data_quality，T4 才能生效；T2 相对独立但改动最大，放最后。

---

## 自验清单

完成后请逐项验证并写入 `reports/dev_selftest_B15.md`：

| # | 验证项 | 方法 |
|---|---|---|
| 1 | 持仓页与看板页盈亏数值一致 | 启动服务，对比两页显示 |
| 2 | 亏损=绿色+负号，盈利=红色+正号 | 观察颜色 |
| 3 | 详情页首次打开即显示投资建议 | 刷新页面（非强制刷新） |
| 4 | 维度 0% 显示⚠️缺失，≤30% 显示偏低 | 用港股股票验证 |
| 5 | ≥2 维度 0% 时显示"数据严重不足"警告条 | 同上 |
| 6 | 日报生成显示"复用 X / 新分析 Y" | 先批量分析再生成日报 |
| 7 | 强制刷新 checkbox 生效 | 勾选后生成日报 |
| 8 | `requirements.txt` 无变化 | diff |
| 9 | `data_collector.py` 三处 if False 不变 | Grep |
| 10 | `config_weights.json` 无变化 | 文件时间戳 |

---

## 红线提醒

1. ❌ 不引入新 pip 依赖
2. ❌ 不改 `data_collector.py` L1645/L1684/L1717
3. ❌ 不改 `config_weights.json`
4. ❌ 不破坏 `data_contract.py` Pydantic 模型
5. ❌ 不超出本任务书 4 个任务范围（不做回测修复、不做行业权重）
6. ✅ 保持 `python app.py` 一键启动不变
