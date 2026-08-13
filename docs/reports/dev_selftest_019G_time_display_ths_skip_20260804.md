# 开发自验报告 019G — 同花顺交易日校验 + 报告时间展示优化

**批次**：019G（019F 后续）
**开发**：开发工程师（单人）
**自验日期**：2026-08-05
**任务书**：`docs/tasks/dev_tasks_20260804_019G_time_display_ths_skip.md`（v2，含架构评审 M-1/M-2/M-3/M-4/R-2 修订）
**架构评审**：`docs/reviews/review_019G_time_display_ths_skip_20260804.md`
**状态**：自验通过，待监理汇报 → QA 独立验收 → PM+QA 双签

---

## 一、改动清单（严格 3 文件）

### 任务 1：modules/data_collector.py — 同花顺批量预取交易日校验

- **函数**：`fetch_capital_flow_batch(a_stock_symbols)`（L1342，签名不变 ✅）
- **位置**：函数体开头（docstring 之后、`if not a_stock_symbols` 空列表分支之前）
- **改动行**：L1356-1366（新增 11 行）
- **改动内容**：

```python
    # 019G：交易日校验 — 周末（周六/周日）跳过 THS 批量预取（含补采），
    # 避免非交易日 THS 接口返回旧数据被写入。法定节假日落在工作日时
    # 仍执行（THS 返回前一交易日数据，低概率可接受）。
    now = datetime.now(_CN_TZ)
    if now.weekday() >= 5:  # 5=周六, 6=周日
        logger.info(f'[同花顺批量] 非交易日（{now.strftime("%A")}），跳过 THS 批量预取（含补采）')
        return {
            'success_count': 0, 'fail_count': 0,
            'source': '同花顺批量(非交易日跳过)',
            'skipped': True, 'reason': 'non_trading_day'
        }
```

- **实现要点核对**：
  - `_CN_TZ` 为模块内定义（L29）✅，非从 config 导入
  - `datetime` 已在 L24 导入，无新增导入 ✅
  - 仅 `weekday()` 判断（5=周六, 6=周日），无节假日库 ✅
  - 早退在 THS 请求与 DB 写入之前，不产生任何请求/写入 ✅
  - 早退返回值含 `source` 键（M-2 契约统一）✅；`skipped`/`reason` 仅此分支出现（M-2：其余返回点无需补 `skipped: False`）
  - 日志含"含补采"字样 ✅
  - 消费方兼容性：`app.py` L1298-1299（batch-analyze）与 `daily_report.py` L479-480 均仅打印/记录返回值，不读取 `skipped`，无即期风险 ✅

### 任务 2：templates/index.html — 个股详情页删除"评级时间"行

- **位置**：L4203（评分卡内时间信息区）
- **改动**：删除 `评级时间：' + (adviseData.rating_date || '—')` 一行；保留"报告生成于"（分钟级）与"最新收盘"两行
- **改动后 L4203-4208**：

```javascript
html += '<div class="rating-time">报告生成于：' + _fmtGenTime(adviseData.generated_at) + '</div>';
if (adviseData.latest_close != null) {
    html += '<div class="rating-time">最新收盘：' + adviseData.latest_close.toFixed(2) +
            '（' + (adviseData.latest_close_date || '') + '）</div>';
}
```

- 后端 `rating_date` 字段不删（API 返回不动）✅

### 任务 3a：app.py — watchlist-scores 接口数据源扩展（M-1 方案 A）

- **接口**：`/api/portfolio/watchlist-scores`（L1838-1992）
- **改动 3 处**（仅此接口内）：

| 位置 | 改动行 | 改动内容 |
|---|---|---|
| 主查询 SELECT | L1871 | 末尾增加 `, dr.generated_at` |
| 无报告降级分支 | L1896 | `NULL as report_status` 后增加 `NULL as generated_at` |
| stocks[] 字典 | L1972 | 增加 `'generated_at': r.get('generated_at')` |

- **ETag**（L1985-1986）：etag_payload 排除的是顶层 `generated_at`（接口级），新增的 `stocks[].generated_at` 进入 etag_payload 属正确行为（新报告→新时间→刷新缓存），无需排除 ✅（与任务书一致）

### 任务 3b：templates/index.html — 总览看板新增"生成时间"列

- **表头**：L4798（"较昨日"列后、"行业"列前插入）：

```javascript
html += '<th style="padding:10px;border-bottom:2px solid #ddd;">生成时间</th>';
```

- **表格行**：L4845（"较昨日"单元格后、"行业"单元格前插入，M-3 nowrap）：

```javascript
html += '<td style="padding:10px;font-size:12px;color:#666;white-space:nowrap;">' + _fmtGenTime(st.generated_at) + '</td>';
```

---

## 二、自验结果

### V1：编译验证 ✅

```
python -m py_compile modules/data_collector.py   → 成功（$? = True）
python -m py_compile app.py                       → 成功（$? = True）
```

### V2：任务 1 行为验证（mock 拦截 datetime，M-4 方法）✅

临时脚本（`%TEMP%\opencode\selftest_019g.py`，未入仓库）采用 M-4 建议方案：`mock.patch.object(dc, 'datetime', FakeDatetime)`，`FakeDatetime.now()` 返回固定时刻，并 mock `_fetch_capital_flow_ths_batch` / `_em_batch_collect` 隔离网络与 DB：

| 场景 | 构造 | 断言 | 结果 |
|---|---|---|---|
| 周日（weekday=6） | `2026-08-02 10:00` +08:00 | `skipped=True`，`success_count=0`，`fail_count=0`，`source='同花顺批量(非交易日跳过)'`，`reason='non_trading_day'`，`_fetch_capital_flow_ths_batch` 未被调用 | ✅ PASS |
| 周二（weekday=1）回归 | `2026-08-04 10:00` +08:00 | 无 `skipped` 键，进入正常路径（THS 不可用→EM 回退 stub） | ✅ PASS |

注：验证脚本中 `FakeDatetime` 同时影响模块内其他 `datetime.now` 引用（如 `now_cn`、`today_str`），作用域仅在 patch 上下文内（M-4 提示已遵守）。

### V3：前端代码核查 ✅

- `_fmtGenTime`（index.html L5402-5405）：非字符串/空值返回 `'—'`，满足"空值显示—"验收项 ✅
- 全文件"评级时间"剩余 3 处（L1118 筛选选项、L2698/L2745 批量分析结果表）均为无关业务，个股详情页行已删 ✅
- 全文件"生成时间"新增 1 处表头（L4798），其余（L4571/L4643/L4739）为 019D 既有"生成时间："标签，无关 ✅

---

## 三、验收标准逐条核对（任务书第 4 节，7 条）

| # | 验收标准 | 核对方式 | 结果 |
|---|---|---|---|
| 1 | 周日场景 → return，`skipped=True`，不执行 THS | V2 mock 测试 | ✅ PASS |
| 1 | 周二场景 → 正常执行 THS 预取（回归） | V2 mock 测试（进入正常路径） | ✅ PASS |
| 2 | 详情页不显示"评级时间"行 | 代码核查 L4203 删除；全文件确认 | ✅ PASS |
| 2 | "报告生成于"显示 `YYYY-MM-DD HH:MM` | `_fmtGenTime` 分钟级格式化（019D 已固化） | ✅ PASS |
| 2 | "最新收盘"行保留 | L4205-4208 保留未动 | ✅ PASS |
| 3 | 看板表格含"生成时间"列 | L4798 表头插入 | ✅ PASS |
| 3 | 每行显示 `_fmtGenTime(generated_at)` | L4845 单元格；app.py L1871/L1896/L1972 数据源闭环 | ✅ PASS |
| 3 | 空值显示"—" | `_fmtGenTime` L5403 空值分支 | ✅ PASS |
| 4 | 两文件 py_compile 无错误 | V1 | ✅ PASS |
| 5 | 评分链路/依赖/表结构文件零改动 | 见第四节 | ✅ PASS |
| 6 | 前端无 JS 错误 | 见第五节（静态核查，运行时由 QA 浏览器验证） | ✅ PASS（待 QA 复核） |
| 7 | M-4：mock datetime 拦截 now()，返回 dict 含 `skipped=True` 且 THS 未被调用 | V2 已按 M-4 方法执行，两条断言均成立 | ✅ PASS |

---

## 四、范围红线核对（范围外文件零改动）

工作树存在历史批次未提交改动（019B-019F 等），本次自验仅核验**本批次（019G）增量**：

1. **本批次改动文件（3 个）**：`modules/data_collector.py` + `templates/index.html` + `app.py`（仅 watchlist-scores 接口），已逐行列于第一节
2. **019G 特征标记全库检索**：`019G` / `非交易日跳过` 仅出现在 `modules/data_collector.py`（L1356/L1364，本次新增）；`templates/index.html` 的"生成时间"表头仅 L4798 一处新增。其余命中（app.py L1805/L1906"报告生成时间"注释、index.html L4571/L4643/L4739 标签）均为 019D 等历史批次既有内容，非本批次产物
3. **评分链路**：`analysis_engine.py` / `data_adapter.py` / `advisor.py` / `scoring_engine.py` / `daily_report.py` / `db_manager.py` / `config.py` / `config_weights.json` / `requirements.txt`（9 包）零改动（本批次）；工作树中的改动均为历史批次遗留
4. **签名红线**：`fetch_capital_flow_batch(a_stock_symbols)` 签名不变 ✅
5. **数据安全**：本批次零 SQL 增删改查变更（未触碰任何数据表结构与存量数据）；交易日校验仅跳过 THS 预取；周末早退同时跳过 019E 补采触发（R-2 说明），日报逐只路径 `collect_stock_data` 仍执行 EM 采集，主力资金面主链路不受影响，下个交易日批次自动补采 ✅

---

## 五、前端预期渲染效果（静态预期，运行时由 QA 浏览器复核）

1. **个股详情页**（任意股票 → 报告详情）：
   - 评分卡时间信息区仅剩两行：`报告生成于：2026-08-04 17:47`（分钟级）与 `最新收盘：xxx.xx（日期）`
   - "评级时间：2026-08-04"（日级，冗余）不再显示
2. **总览看板**（dashboard 批量评分表）：
   - 表头新增第 6 列"生成时间"（位于"较昨日"与"行业"之间）
   - 每行对应单元格显示该股票报告生成时间 `YYYY-MM-DD HH:MM`（ISO 截取 16 位并替换 T）
   - 无报告/空值时显示"—"；单元格 `white-space:nowrap` 防窄屏换行，`font-size:12px;color:#666` 弱化视觉层级
   - 详情按钮列等既有列位置整体右移一列，列数由 8 → 9

---

## 六、遗留说明

- 未新增/删除任何依赖；`requirements.txt` 维持 9 包 ✅
- 改动后需重启 app.py 生效（PM 备注确认）
- 前端运行时渲染（验收项 6）需 QA 独立验收时浏览器实测确认
