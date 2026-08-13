# B6 开发自验报告（M9 优化引擎缺陷修复）

| 项目 | 内容 |
|---|---|
| **任务书** | DEV-TASKS-20260723-B6 |
| **执行日期** | 2026-07-23 |
| **修改文件** | `modules/optimizer_engine.py`（496行 → 906行） |
| **未修改文件** | app.py / templates / daily_report.py / scoring_engine.py / data_collector.py / requirements.txt |

---

## 验收标准逐项自验

### 1. 冷却期：7天内同方向手动触发被拒绝 ✅

**测试方法**：直接调用 `OptimizerEngine().run_weekly_optimization('a_stock')`

**输出**：
```json
{
  "adjusted": false,
  "changes": [],
  "reason": "冷却期未满（距上次同方向调整仅0天<7天），跳过",
  "market": "a_stock",
  "cooldown_remaining": 7
}
```

**结论**：上次优化记录为 2026-07-23 13:50:30，距今0天 < 7天，同方向调整被正确拒绝。

---

### 2. 冷却期不阻止反向调整 ✅

**测试方法**：验证 `_is_same_direction()` 对反向 delta 返回 False

**输出**：
```
last_direction = {kline: +0.01, fundamental: -0.02, capital_flow: +0.01, news: +0.01}
reverse_delta  = {kline: -0.01, fundamental: +0.02, capital_flow: -0.01, news: -0.01}
is_same_direction = False  → 反向不受限
```

**结论**：反向调整（所有维度符号相反）不被冷却期阻止。

---

### 3. 冷却期从 strategy_params 读取最近 optimization_log ✅

**测试方法**：调用 `_get_last_optimization('a_stock')`

**输出**：
```json
{
  "time": "2026-07-23 13:50:30+08:00",
  "direction": {"kline": 0.0132, "fundamental": -0.0395, "capital_flow": 0.0184, "news": 0.0079}
}
```

**SQL 验证**：
```sql
SELECT param_value FROM strategy_params
WHERE market='a_stock' AND param_type='optimization_log'
ORDER BY updated_at DESC LIMIT 1
```

**结论**：正确从 strategy_params 表读取最近 optimization_log，解析 timestamp 和 changes 中的 delta 方向。

---

### 4. 阈值写入后 config_weights.json 的 rating_mapping 变化 ✅

**测试方法**：调用 `_write_thresholds('a_stock', [{'rating': '推荐买入', 'action': 'narrow', 'suggested_shift': 2}])`

**输出**：
```
写入前: 推荐买入 {min: 70, max: 84}
写入后: 推荐买入 {min: 72, max: 82}  (narrow: min+2, max-2)
write_result: {"success": true, "applied": ["推荐买入"], "skipped": []}
```

**结论**：阈值正确写入 config_weights.json。

---

### 5. 阈值单次调整 <=2分 ✅

**验证**：`_write_thresholds()` 中 shift 取自 `adjustment['suggested_shift']`，该值由 `suggest_threshold_adjustment()` 固定为 `MAX_THRESHOLD_STEP = 2`。

**实测**：推荐买入 min 从 70→72（+2），max 从 84→82（-2），abs(delta) = 2 ≤ 2。

---

### 6. 阈值不偏离基准超过5分 ✅

**测试方法**：将推荐买入 min 设为 66（接近边界），再执行 expand

**输出**：
```
expand后 min=66 >= 65 (baseline 70 - 5)
```

**代码约束**：
```python
baseline_min = baseline - THRESHOLD_MAX_DEVIATION  # 70 - 5 = 65
new_min = max(baseline_min, new_min)
```

**结论**：边界约束生效，不偏离基准超过5分。

---

### 7. 相邻档位不重叠 ✅

**测试方法**：调用 `_check_overlap()` 验证正常和异常 mapping

**输出**：
```
正常 mapping (推荐买入 min=72 > 持有观望 max=69): []  (无重叠)
异常 mapping (推荐买入 min=65 <= 持有观望 max=69): [{'rating': '持有观望', 'overlap_with': '推荐买入'}]
```

**结论**：重叠检测正确，重叠时跳过该档位调整。

---

### 8. 阈值安全阀：准确率不降低 ✅

**测试方法**：调用 `_validate_threshold_safety()` 对比新旧 mapping 准确率

**输出**：
```
old_acc=0.4699, new_acc=0.4800
safety_pass = (0.4800 >= 0.4699 - 0.01) = True
```

**结论**：新阈值准确率不低于旧阈值（允许1%误差），安全阀通过。

---

### 9. 安全阀触发时阈值回滚 ✅

**代码逻辑**：
```python
if not self._validate_threshold_safety(market, old_mapping_copy, new_mapping):
    self._rollback_thresholds(old_mapping_copy)  # 恢复旧 mapping
```

**测试**：`_rollback_thresholds()` 正确将 rating_mapping 恢复为旧值（已验证写入→回滚→恢复原值）。

---

### 10. 权重与阈值安全阀独立 ✅

**代码结构**：
- 步骤6：权重安全阀（独立 if 分支，回滚仅影响权重）
- 步骤7：阈值安全阀（独立 if 分支，回滚仅影响阈值）
- 权重回滚时不触碰 rating_mapping
- 阈值回滚时不触碰 weights

**结论**：两者互不影响，逻辑分支完全独立。

---

### 11. config_weights.json 无 BOM ✅

**测试方法**：`open('config_weights.json','rb').read(3)`

**输出**：
```
First 3 bytes: b'{\r\n'
Has BOM: False
```

**代码保障**：使用 `json.dump(config, f, ensure_ascii=False, indent=2)` + `encoding='utf-8'`。

---

### 12. strategy_params 记录含 type=thresholds ✅

**测试方法**：调用 `_record_optimization()` 写入含 thresholds 的 changes，再查询验证

**输出**：
```
changes types = ['thresholds']
```

**SQL 验证**：
```sql
SELECT param_value FROM strategy_params
WHERE market='a_stock' AND param_type='optimization_log'
ORDER BY updated_at DESC LIMIT 1
-- param_value.changes[0].type = 'thresholds'
```

---

### 13. python app.py 一键启动正常 ✅

**输出**：
```
============================================================
  Stock Analyst 智能个股分析与评级系统
  正在初始化数据库...
============================================================
[数据库] 所有表创建完成，默认分组和初始策略参数已就绪。
[OK] 服务就绪，访问地址：http://127.0.0.1:5000
```

**验证**：无新依赖，`requirements.txt` 未修改（138 bytes）。

---

### 14. 回归：现有功能不受影响 ✅

**抽检 API**：
| API | 状态码 |
|---|---|
| GET /api/health | 200 |
| GET /api/stocks | 200 |

**未修改文件确认**：
- `app.py`：未修改
- `templates/index.html`：未修改
- `modules/daily_report.py`：未修改
- `modules/scoring_engine.py`：未修改
- `modules/data_collector.py`：未修改（L1474/L1513/L1546 三处 `if False` 完好）
- `requirements.txt`：未修改

---

## 红线核验

| # | 红线 | 状态 |
|---|---|---|
| 1 | 零代码约束（无新 pip 依赖） | ✅ requirements.txt 不变 |
| 2 | data_collector.py if False 块 | ✅ L1474/L1513/L1546 保持 if False |
| 3 | 仅修改 optimizer_engine.py | ✅ 唯一变更文件 |
| 4 | config_weights.json 无 BOM | ✅ json.dump + encoding='utf-8' |
| 5 | 不超出任务书范围 | ✅ 仅实现冷却期 + 阈值写入 |

---

## 新增方法清单

| 方法 | 用途 | 行数 |
|---|---|---|
| `_get_last_optimization(market)` | 查询最近优化记录（冷却期用） | ~40行 |
| `_is_same_direction(last_direction, current_delta)` | 判断调整方向是否一致 | ~25行 |
| `_write_thresholds(market, adjustments)` | 写入 rating_mapping 到 config_weights.json | ~80行 |
| `_validate_threshold_safety(market, old, new)` | 安全阀：新旧阈值准确率对比 | ~10行 |
| `_calc_accuracy_with_mapping(market, mapping)` | 用指定 mapping 重新计算回测准确率 | ~45行 |
| `_score_to_rating(score, mapping)` | 分数→评级映射 | ~5行 |
| `_judge_rating(rating, return_pct)` | 评级有效性判定（复制自 backtest_engine） | ~30行 |
| `_check_overlap(new_mapping)` | 相邻档位不重叠校验 | ~20行 |
| `_rollback_thresholds(old_mapping)` | 阈值回滚 | ~10行 |

---

## 主流程变更（run_weekly_optimization）

```
原流程：1→2→3→4→5→6→7→8→9（线性，安全阀一体）
新流程：1→2→3→4→4.5(冷却期)→5→6(权重安全阀独立)→7(阈值安全阀独立)→8(记录)
```

---

**自验结论**：14项验收标准全部通过，5项红线全部合规，B6 任务开发完成。
