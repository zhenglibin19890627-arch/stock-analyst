# 011 数据采集全链路增量优化 — 开发自验报告

> **开发人员**：DEV | **自验日期**：2026-07-30 | **任务编号**：DEV-TASKS-20260730-011-DEV

---

## 一、修改文件清单

| 文件 | 改动内容 | 实际改动量 |
|---|---|---|
| `config.py` | 新增3个增量采集配置项（NORTH_CAPITAL_CACHE_DAYS, FUNDAMENTAL_REPORT_TTL_DAYS, PE_PB_CACHE_TTL_HOURS） | +8行 |
| `modules/data_collector.py` | import更新 + save_data_status去重 + 7个函数增量检查 + force_full参数透传 | +225行/-88行 |
| `app.py` | 新增 `/api/stocks/<id>/refresh-full` 路由 | +37行 |
| **合计** | | ~270行净增 |

---

## 二、功能自验结果（V1-V10）

### V1: K线同日跳过 — PASS

- **测试方法**：对 stock_id=4 (600276 恒瑞医药) 调用 `fetch_kline('600276', 'a_stock')`
- **测试结果**：K线最新日期=2026-07-30（今天），第二次调用返回 `('success', '同日跳过(K线已有2026-07-30数据)')`
- **结论**：同日跳过逻辑生效 ✓

### V2: 基本面80天门控 — PASS

- **测试方法**：检查数据库中 raw_fundamental 表各股票 report_date 距今天数 + 源码逻辑验证
- **数据样例**：
  - 000333 格力电器: report_date=2026-07-15, 距今15天 → **会跳过**（< 80天）
  - 300750 宁德时代: report_date=2026-06-30, 距今30天 → **会跳过**（< 80天）
  - 600276 恒瑞医药: report_date=2026-03-31, 距今121天 → **不会跳过**（> 80天）
- **代码验证**：`fetch_a_fundamental` 签名 `(symbol, force_full=False)`，含 `skip_financial` 变量 + `FUNDAMENTAL_REPORT_TTL_DAYS` 检查
- **结论**：80天财报TTL门控逻辑正确 ✓

### V3: PE/PB 24h门控 — PASS

- **测试方法**：源码逻辑验证
- **代码验证**：`skip_pepb` 变量 + `PE_PB_CACHE_TTL_HOURS` 检查 + 仅当 `skip_financial=True` 时才检查PE/PB门控
- **双门控逻辑**：两门控都跳过 → 整体跳过；仅PE/PB过期 → 仅更新PE/PB（返回partial）
- **结论**：PE/PB 24h门控逻辑正确 ✓

### V4: 消息面当日跳过 — PASS

- **测试方法**：向 news_sentiment 表插入当日测试记录 → 调用 `fetch_sentiment('600276', 'a_stock')`
- **测试结果**：返回 `('success', '当日跳过(消息面已有1条记录)')`
- **结论**：当日跳过逻辑生效 ✓

### V5: 北向资金30天缓存 — PASS

- **测试方法**：配置项验证 + data_status 表检查
- **配置验证**：`NORTH_CAPITAL_CACHE_DAYS = 30`（config.py已生效）
- **补充**：`fetch_north_capital` 成功/失败时新增 `save_data_status` 记录（原代码缺失）
- **结论**：30天缓存配置项已生效 ✓

### V6: 融资余额增量 — PASS

- **测试方法**：源码逻辑验证 + 数据库实际数据校验
- **数据样例**：
  - 600276: 最新融资余额日期=2026-07-29, 距今1天 → 预期 days_to_try_max=3
  - 000333: 最新融资余额日期=2026-07-27, 距今3天 → 预期 days_to_try_max=5
- **代码验证**：`days_to_try_max = min(15, gap+2)`，无数据时 `days_to_try_max=159`（全量）
- **结论**：增量补取逻辑正确 ✓

### V7: force_full 参数 — PASS

- **测试方法**：对 600276 调用 `fetch_kline('600276', 'a_stock', force_full=True)`
- **测试结果**：返回 `('success', '获取251条K线数据')`（全量采集，未跳过）
- **结论**：force_full=True 绕过增量缓存 ✓

### V8: /refresh-full API — PASS

- **测试方法**：源码检查 + Flask服务启动验证
- **路由验证**：`/api/stocks/<int:stock_id>/refresh-full` 已注册
- **代码路径**：POST → collect_stock_data(force_full=True) → generate_advice → generate_price_advice
- **Flask启动**：服务正常启动，监听127.0.0.1:5000
- **说明**：全量刷新API因涉及多维度数据采集（K线+基本面+资金面+北向+融资+消息面），响应时间较长（分钟级），属预期行为
- **结论**：API路由正确注册 ✓

### V9: data_status 去重 — PASS

- **测试方法**：连续3次 `save_data_status(1, 'kline', 'success', msg)` → 查询 data_status
- **测试结果**：同维度同日记录数=1（最新消息为 `test_msg_3`）
- **去重效果**：原每次INSERT新增一行 → 现先删后插，同维度同日仅保留1条
- **结论**：data_status去重逻辑生效 ✓

### V10: 首次分析兜底 — PASS

- **测试方法**：源码验证所有增量检查均含 `if row and row['xxx']:` None检查
- **结论**：无数据时 last_date/last_report/cnt/fetched_at 均为 None → skip标志保持False → 全量采集 ✓

---

## 三、不回归验证（R1-R4）

### R1: 评分结果不变 — PASS

- **验证方法**：增量优化仅影响数据采集频率，不修改任何评分逻辑/数据计算
- **红线文件**：`scoring_engine.py`、`advisor.py`、`config_weights.json` 均未修改
- **结论**：评分结果不受影响 ✓

### R2: 红线文件未修改 — PASS

- **验证方法**：`git diff --name-only` 检查
- **结果**：`scoring_engine.py` / `advisor.py` / `config_weights.json` 不在修改清单中
- **结论**：红线区域完整 ✓

### R3: 无新 pip 依赖 — PASS

- **验证方法**：检查 requirements.txt
- **结果**：行数=8（未增加），包列表：akshare, Flask, pandas, numpy, python-dateutil, pydantic, requests, openpyxl
- **结论**：零代码用户约束满足 ✓

### R4: 资金面采集逻辑不变 — PASS

- **验证方法**：`inspect.signature(fetch_capital_flow)` 检查
- **结果**：签名仍为 `(symbol, market)`，未添加 force_full 参数
- **结论**：资金面同日跳过逻辑保持不变 ✓

---

## 四、架构师5决策点实现对照

| 决策点 | 裁定 | 实现状态 |
|---|---|---|
| DP-1 K线 | 同日跳过 + 全量覆盖 | ✓ `fetch_kline(force_full)` |
| DP-2 基本面 | 80天财报TTL + 24h PE/PB TTL，双门控独立 | ✓ `fetch_a_fundamental` 双门控 + `fetch_hk_fundamental` 80天门控 |
| DP-3 消息面 | 当日跳过（增量保留） | ✓ `fetch_sentiment(force_full)` |
| DP-4 北向资金 | 30天缓存 + config.py配置项 | ✓ `fetch_north_capital(force_full)` + `NORTH_CAPITAL_CACHE_DAYS` |
| DP-5 全量刷新 | 仅API入口 + force_full参数透传 | ✓ `collect_stock_data(force_full)` + `/refresh-full` API |

---

## 五、实现细节与发现

### 5.1 news_sentiment 表列名修正

任务书原文引用 `analysis_date` 列名，但实际表结构为 `news_date`。已修正为正确的列名。

### 5.2 north_capital 补充 save_data_status

原 `fetch_north_capital` 未调用 `save_data_status`，导致增量缓存检查无法读取上次采集时间。已补充成功/失败时的 `save_data_status` 调用。

### 5.3 data_status 去重方案

采用"先删后插"模式（而非 ALTER TABLE 加唯一约束），符合零代码用户友好原则（无需数据库迁移）。

---

## 六、已知问题/待确认项

1. **V8 API响应时间**：全量刷新涉及多维度网络采集，响应时间在分钟级。如需前端体验优化，建议改为异步任务模式（当前任务书未要求）。
2. **资金面 force_full 不透传**：按PM裁定（红线），`fetch_capital_flow` 保持原有同日跳过逻辑不变，即使 force_full=True 也不绕过（资金面同日数据不会变化）。
