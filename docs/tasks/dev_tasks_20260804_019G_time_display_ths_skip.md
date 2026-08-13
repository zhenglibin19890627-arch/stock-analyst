# 开发任务书 019G — 同花顺交易日校验 + 报告时间展示优化

**签发日期**：2026-08-04
**签发人**：PM
**批次编号**：019G（019F 后续）
**优先级**：P2
**关联批次**：019E（资金面估算兜底，已关闭）、019F（评分纯净修复，已关闭 2026-08-04）

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 推荐窗口类型 | Quests 独立窗口（单代理执行） |
| 推荐模型 | 开发：glm5.2 → QA：kimi k3（验收类任务） |
| 执行模式 | 已关闭 |
| 流程路径 | ✅PM 签发 v1 → ✅架构师评审（有条件通过，M-1/M-2/M-3/M-4 已修订 v2） → ✅监理批准 → ✅开发执行+自验 → ✅QA 独立验收（7/7 PASS）→ ✅PM+QA 双签 → ✅监理批准关闭（2026-08-05） |

---

## 一、背景

补采完成后监理指出四项问题，本批次统一处理：

### 问题清单

| # | 问题 | 根因 | PM 定性 |
|---|---|---|---|
| ① | 同花顺净额缺失 + 周末/非交易日异常数据 | 调度器每日触发，非交易日 THS 接口返回旧数据被写入 | 数据采集层 |
| ② | 分析报告页"评级时间"未精确到分钟 | `rating_date` = `report_date`（日级 `YYYY-MM-DD`），前端显示无时分 | 前端展示 |
| ③ | 个股详情页多个时间字段冗余 | "评级时间"(日级)+"报告生成于"(分钟级) 日期信息重复 | 前端 UX |
| ④ | 总览看板表格无"报告生成时间"列 | `dashRenderTable` 未渲染 `generated_at` 字段 | 前端展示 |

### 问题②根因定位（PM 代码级核实）

- **数据来源**：`app.py` L1091 `'rating_date': latest_date` — `latest_date` 是 `report_date` 字段（日级）
- **前端展示**：`index.html` L4203 `评级时间：' + (adviseData.rating_date || '—')` → 显示 `2026-08-04`（无时分）
- **DB 验证**：`daily_reports.generated_at` 含完整时分秒（`2026-08-04T17:47:25.864419+08:00`），前端 `_fmtGenTime` 已正确格式化为分钟级
- **结论**：问题②的本质是"评级时间"字段本身只有日级精度。监理已同意删除该行，保留"报告生成于"（分钟级），问题②③合并解决

---

## 二、执行角色

**开发**（单人）

---

## 三、任务范围

> 改动涉及 3 个文件：`modules/data_collector.py`（任务 1）+ `templates/index.html`（任务 2/3）+ `app.py`（任务 3 数据源扩展）
> 
> **v2 修订（架构评审 M-1 方案 A）**：原 v1 任务 3 声称看板数据源 `watchlist-scores` 已含 `stocks[].generated_at`，经架构师核验不属实（引用行号属评级列表接口）。v2 将 app.py 纳入范围，补充 SELECT + stocks 字典扩展。同步采纳 M-2（返回值契约统一）、M-3（nowrap）、M-4（QA 测试建议）、R-2（数据安全补充说明）。

### 任务 1：同花顺批量预取交易日校验（问题①）

**文件**：`modules/data_collector.py`
**位置**：`fetch_capital_flow_batch` 函数入口（约 L1342）
**改动**：在函数开头加交易日判断，非交易日（周末）直接 return，不执行 THS 批量预采

**实现方案**（零代码用户友好，无新依赖）：

```python
# _CN_TZ 为 data_collector.py 模块内定义（L29），非从 config 导入
# datetime 已在 L24 导入，无需新增导入

# 在 fetch_capital_flow_batch 函数体开头：
now = datetime.now(_CN_TZ)
if now.weekday() >= 5:  # 5=周六, 6=周日
    logger.info(f'[同花顺批量] 非交易日（{now.strftime("%A")}），跳过 THS 批量预取（含补采）')
    return {
        'success_count': 0, 'fail_count': 0,
        'source': '同花顺批量(非交易日跳过)',
        'skipped': True, 'reason': 'non_trading_day'
    }
```

**M-2 返回值契约统一**：早退分支补 `source` 键与其余返回点形状对齐；`skipped` 仅在此分支出现，其余返回点无需补 `skipped: False`（消费方不读取该键，架构评审确认无即期风险）。

**约束**：
- 仅用 `weekday()` 判断周末（5=周六, 6=周日），不引入节假日库
- 法定节假日落在工作日时 THS 仍会执行（接口返回前一交易日数据，但概率低，可接受）
- 函数签名 `fetch_capital_flow_batch(a_stock_symbols)` 不变
- 返回值新增 `skipped` 标志位（消费方需兼容：`skipped=True` 时不报错）

### 任务 2：个股详情页删除"评级时间"行（问题②③）

**文件**：`templates/index.html`
**位置**：约 L4203
**改动**：删除"评级时间"行，保留"报告生成于"+"最新收盘"

**改动前**（L4203-4207）：
```javascript
html += '<div class="rating-time">评级时间：' + (adviseData.rating_date || '—') + '</div>';
html += '<div class="rating-time">报告生成于：' + _fmtGenTime(adviseData.generated_at) + '</div>';
if (adviseData.latest_close != null) {
    html += '<div class="rating-time">最新收盘：' + adviseData.latest_close.toFixed(2) +
            '（' + (adviseData.latest_close_date || '') + '）</div>';
}
```

**改动后**：
```javascript
html += '<div class="rating-time">报告生成于：' + _fmtGenTime(adviseData.generated_at) + '</div>';
if (adviseData.latest_close != null) {
    html += '<div class="rating-time">最新收盘：' + adviseData.latest_close.toFixed(2) +
            '（' + (adviseData.latest_close_date || '') + '）</div>';
}
```

### 任务 3：总览看板表格新增"生成时间"列（问题④）

**文件**：`templates/index.html`
**位置**：`renderDashboard` 函数的表格表头（约 L4793-4801）+ `dashRenderTable` 函数的表格行（约 L4839-4848）
**改动**：表头加一列"生成时间"，表格行渲染 `_fmtGenTime(st.generated_at)`

**表头改动**（在"较昨日"列后插入）：
```javascript
// 改动前：
html += '<th ... onclick="dashSort(\'change\')">较昨日 ↕</th>';
html += '<th ...>行业</th>';

// 改动后：
html += '<th ... onclick="dashSort(\'change\')">较昨日 ↕</th>';
html += '<th style="padding:10px;border-bottom:2px solid #ddd;">生成时间</th>';
html += '<th ...>行业</th>';
```

**表格行改动**（在"较昨日"单元格后插入）：
```javascript
// 改动前：
html += '<td style="padding:10px;">' + changeStr + '</td>';
html += '<td style="padding:10px;font-size:13px;">' + industryTag + '</td>';

// 改动后：
html += '<td style="padding:10px;">' + changeStr + '</td>';
html += '<td style="padding:10px;font-size:12px;color:#666;white-space:nowrap;">' + _fmtGenTime(st.generated_at) + '</td>';
html += '<td style="padding:10px;font-size:13px;">' + industryTag + '</td>';
```

**数据来源扩展（M-1 方案 A，架构评审强制修订）**：

原 v1 声称数据源已就位，经架构师核验不属实——`watchlist-scores` 接口（`app.py` L1838-1991）的 SELECT 和 `stocks[]` 字典均无 `generated_at`。需在 `app.py` 中扩展：

1. **主查询 SELECT**（约 L1866-1871）：增加 `dr.generated_at`
2. **`stocks[]` 字典**（约 L1948-1974）：增加 `'generated_at': r.get('generated_at')`
3. **无报告降级分支**（约 L1888-1901）：SELECT 置 `NULL as generated_at`
4. **ETag**（L1985）：`generated_at` 进入 etag_payload 属正确行为（新报告→新时间→刷新缓存），无需排除

**app.py 具体改动指引**：
```python
# 主查询 SELECT（约 L1866-1871），改动前：
SELECT s.id, s.symbol, s.name, dr.engine_version, dr.total_score, ...
# 改动后：
SELECT s.id, s.symbol, s.name, dr.engine_version, dr.total_score, ..., dr.generated_at

# 无报告降级分支（约 L1888-1901），改动前：
SELECT s.id, s.symbol, s.name, NULL as engine_version, ...
# 改动后：
SELECT s.id, s.symbol, s.name, NULL as engine_version, ..., NULL as generated_at

# stocks[] 字典（约 L1948-1974），增加一行：
'generated_at': r.get('generated_at'),
```

**M-3 渲染细节**：新单元格追加 `white-space:nowrap;` 防窄屏换行。

### 明确不改范围

- `modules/analysis_engine.py` — 不碰（019F 已修复）
- `modules/data_adapter.py` — 不碰
- `modules/advisor.py` — 不碰
- `modules/scoring_engine.py` — 不碰
- `modules/daily_report.py` — 不碰（调度时间 16:10 已改）
- `database/db_manager.py` — 不碰
- `config_weights.json` / `config.py` — 不碰
- `requirements.txt` — 不碰（维持 9 包，无新依赖）
- `app.py` — **仅限 watchlist-scores 接口的 SELECT + stocks 字典扩展（任务 3 数据源）**，其余代码不碰
- `index.html` 中除 L4203（任务2）和 L4793-4848（任务3）外的代码 — 不碰

---

## 四、验收标准

1. **交易日校验**（任务1）：
   - 构造 `weekday()=6`（周日）场景 → `fetch_capital_flow_batch` 直接 return，`skipped=True`，不执行 THS 请求
   - 构造 `weekday()=1`（周二）场景 → 正常执行 THS 预取（回归）
2. **个股详情页时间展示**（任务2）：
   - 打开任意个股详情页 → 不显示"评级时间"行
   - "报告生成于"显示 `YYYY-MM-DD HH:MM` 格式（分钟级）
   - "最新收盘"行保留
3. **总览看板生成时间列**（任务3）：
   - 打开总览看板 → 表格含"生成时间"列
   - 每行显示对应股票的 `_fmtGenTime(generated_at)` 值（`YYYY-MM-DD HH:MM`）
   - 空值显示"—"
4. **编译验证**：`python -m py_compile modules/data_collector.py` + `python -m py_compile app.py` 无错误
5. **零改动确认**：`analysis_engine.py` / `data_adapter.py` / `advisor.py` / `scoring_engine.py` / `db_manager.py` / `daily_report.py` / `requirements.txt` 零改动；`app.py` 仅改 watchlist-scores 接口的 SELECT + stocks 字典（任务 3 数据源）
6. **前端无 JS 错误**：总览看板和个股详情页正常渲染，浏览器控制台无报错
7. **M-4 QA 测试建议**：构造周末场景时用 `monkeypatch.setattr('modules.data_collector.datetime', Fake)` 拦截 `now()`，注意控制作用域（该模块其余函数如 `now_cn`、L1359 `today_str` 也引用同一 `datetime`）；新增断言：返回 dict 含 `skipped=True` 且 `_fetch_capital_flow_ths_batch` 未被调用

---

## 五、红线约束

1. **范围红线**：改动仅限 `modules/data_collector.py`（任务1，交易日校验）+ `templates/index.html`（任务2/3，时间展示优化）+ `app.py`（任务3，watchlist-scores 接口 SELECT + stocks 字典扩展 generated_at），其余文件一律不碰
2. **签名红线**：`fetch_capital_flow_batch(a_stock_symbols)` 签名不变
3. **评分纯净红线**：本批次不涉及评分链路，不得修改任何过滤点
4. **零代码约束**：不引入新 pip 依赖（requirements.txt 维持 9 包）；交易日判断用 `weekday()` 内置方法，不引入节假日库
5. **数据安全红线**：交易日校验仅跳过 THS 预取，不影响已有数据；不删除/覆盖存量数据。R-2 补充说明：周末早退同时跳过 019E 补采触发（补采逻辑内嵌于 THS 成功路径之后），但日报逐只路径 `collect_stock_data` 仍执行 EM 采集，主力资金面主链路不受影响；下个交易日批次自动补采

---

## 六、执行顺序

```
Step 1: ✅ PM 签发 v1
Step 2: ✅ 架构师评审（有条件通过，M-1/M-2/M-3/M-4 已修订为 v2）
Step 3: ✅ 监理批准
Step 4: ✅ 开发执行 + 自验
Step 5: ✅ QA 独立验收（7/7 PASS）→ ✅ PM+QA 双签 → ✅ 监理批准关闭（2026-08-05）
```

---

> **PM 备注**：本批次源于补采后监理指出的四项展示/采集优化。问题②③合并解决（删除"评级时间"行即消除日级字段，保留分钟级"报告生成于"）。问题④经架构评审发现数据源不就位，v2 已将 app.py 纳入范围补充 generated_at 字段。问题①交易日校验用 `weekday()` 简单判断，零代码用户无需维护节假日表，法定节假日落在工作日的低概率情况可接受（后续如需精确可升级）。改动后需重启 app.py 生效。
>
> **v2 修订摘要**：采纳架构评审 M-1（方案 A，app.py 纳入范围）、M-2（返回值契约统一 + 注释更正）、M-3（nowrap）、M-4（QA 测试建议）、R-2（数据安全补充说明）。原 2 文件扩至 3 文件。
