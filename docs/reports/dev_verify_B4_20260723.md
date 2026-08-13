# M9-PREFILL 技术面模拟回测回填 — 开发自验报告

| 项目 | 内容 |
|---|---|
| **任务ID** | M9-PREFILL |
| **执行日期** | 2026-07-23 |
| **执行方** | 开发（GLM） |
| **状态** | 自验通过，待产品经理验收 |

---

## 变更文件清单

| 文件 | 变更内容 |
|---|---|
| `database/db_manager.py` | `_migrate_columns` 新增 `backtest_results.is_simulated` 列（INTEGER DEFAULT 0） |
| `modules/backtest_engine.py` | 1. `_ensure_columns()` 追加 is_simulated 列<br>2. 新增 `run_historical_simulation()` 函数<br>3. `compute_market_report()` 新增 `include_simulated` 参数 |
| `app.py` | 1. 新增 `POST /api/backtest/simulate` 路由<br>2. `/api/backtest/market-report` 支持 `include_simulated=true/false` 参数 |

---

## 验收标准逐项核验

| # | 标准 | 结果 | 证据 |
|---|---|---|---|
| 1 | `run_historical_simulation()` 执行成功 | ✅ PASS | 返回 `{total: 324, success: 324, errors: 0, skipped: 0}` |
| 2 | backtest_results 新增 >=300 条 is_simulated=1 记录 | ✅ PASS | 实际 324 条（27股 × 12点） |
| 3 | 模拟评级日覆盖 >=50 天时间跨度 | ✅ PASS | 2026-04-24 ~ 2026-07-16，跨度 83 天 |
| 4 | 无前瞻偏差：模拟日T的评分仅用T及之前K线 | ✅ PASS | 代码审查：`kline_slice = all_kline[:sim_idx + 1]`，严格截取 |
| 5 | ratings_history 无新增模拟记录 | ✅ PASS | COUNT=575，执行前后不变 |
| 6 | 幂等：重复执行不产生重复行 | ✅ PASS | 第二次执行 skipped=324，COUNT 不变 |
| 7 | market-report 可选择是否包含模拟数据 | ✅ PASS | `include_simulated=true/false` 参数，默认 false |
| 8 | 零代码约束不变 | ✅ PASS | `python app.py` 一键启动，无新依赖 |
| 9 | 现有真实回测数据不受影响 | ✅ PASS | is_simulated=0 记录数 535 不变 |

---

## 技术方案要点

### 无前瞻偏差保证
```python
# 模拟评级日 sim_idx 只能看到 sim_idx 及之前的K线
kline_slice = all_kline[: sim_idx + 1]
tech_score = _calc_technical_score_from_kline(kline_slice)
```

### 评级映射（任务书约定）
- >=85 → 强烈推荐买入
- >=70 → 推荐买入
- >=50 → 持有观望
- >=30 → 建议减仓
- <30 → 强烈建议卖出

### 数据隔离
- 模拟记录 `rating_id = -1`（不关联真实评级）
- `is_simulated = 1` 标记隔离
- `ratings_history` 表零写入
- API 默认不展示模拟数据

### 幂等机制
```python
# 写入前检查是否已存在
SELECT id FROM backtest_results
WHERE stock_id=? AND rating_date=? AND is_simulated=1
```

---

## 红线核验

| 红线 | 状态 |
|---|---|
| 零代码约束 | ✅ 无新依赖，仅用现有模块 |
| 不污染真实数据 | ✅ ratings_history 零写入，is_simulated 隔离 |
| 需求基线 | ✅ 为 M9（§2.9）提供数据支撑 |
| 无前瞻偏差 | ✅ 严格使用截止日K线切片 |
| v5数据契约 | ✅ 复用 scoring_engine 技术面子项评分 |

---

**自验结论**：9/9 验收标准全部通过，红线无违反，提交产品经理验收。
