# 开发任务：DEV-TASKS-20260723-B6（M9 优化引擎缺陷修复）

## 你的角色

你是「智能个股分析与评级系统（Stock Analyst）」项目的**开发工程师**，负责执行已批准的开发任务书。

## 项目基本信息

| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 |
| 最高约束 | **零代码用户可独立运行**：pip install -r requirements.txt → python app.py → 浏览器打开即用 |
| 任务书 | `docs/tasks/dev_tasks_20260723_B6.md`（已批准，必须完整阅读） |

## 绝对红线（违反即验收不通过）

1. **不引入新 pip 依赖**，`requirements.txt` 不可修改
2. **`modules/data_collector.py` L1474/L1513/L1546 三处 `if False` 绝对不可改为 True**，最好完全不碰该文件
3. **仅修改 `modules/optimizer_engine.py`**，不触碰 app.py / templates / daily_report.py / scoring_engine.py
4. **config_weights.json 写入必须用 `json.dump()` + `encoding='utf-8'`**，禁用 PowerShell Set-Content（防 BOM）
5. **不超出任务书范围**（任务蔓延 = 验收不通过）

## 任务内容（2个子任务）

### 任务1：M9-COOLDOWN 冷却期执行逻辑

**缺陷**：`MIN_INTERVAL_DAYS=7`（L44）已定义但无执行逻辑，手动触发可无限频繁执行。

**要求**：
- 在 `run_weekly_optimization()` 中实现冷却期检查
- 新增 `_get_last_optimization(market)`：从 strategy_params 查最近 optimization_log，解析时间和方向
- 新增 `_is_same_direction(last_direction, current_delta)`：各维度 delta 符号相同 = 同方向
- **仅约束同方向调整**，反向调整不受限
- 代码顺序：先生成权重建议（获知方向）→ 再检查冷却期 → 再写入
- 被拒绝时返回 `{'adjusted': False, 'reason': '冷却期未满...', 'cooldown_remaining': N}`

### 任务2：M9-THRESHOLD-APPLY 阈值自动写入 + 安全阀

**缺陷**：`suggest_threshold_adjustment()` 生成建议但从未写入 config_weights.json 的 rating_mapping，需求 §2.9.1 未完成。

**要求**：
- 新增 `_write_thresholds(market, adjustments)`：
  - narrow → min += 2, max -= 2（收窄）
  - expand → min -= 2, max += 2（扩大）
  - 边界约束：不偏离 THRESHOLD_BASELINES ± 5 分
  - 相邻档位不重叠（重叠则跳过该档位）
- 新增 `_validate_threshold_safety(market, old_mapping, new_mapping)`：
  - 用新旧 mapping 分别对 backtest_results 的 total_score 重映射评级
  - 新准确率 >= 旧准确率 - 0.01 才通过
- 新增 `_calc_accuracy_with_mapping(market, mapping)`：用指定 mapping 计算准确率
- 新增 `_check_overlap(new_mapping)`：校验相邻档位区间无交叉
- **安全阀独立**：权重回滚不连带阈值回滚，反之亦然
- 阈值变更也记录到 strategy_params 的 optimization_log 中

## 现有代码结构（optimizer_engine.py，496行）

```
L40-56: 安全约束常量（MAX_WEIGHT_STEP=0.05, MIN_INTERVAL_DAYS=7, THRESHOLD_BASELINES 等）
L59-160: run_weekly_optimization() 主入口
L162-222: analyze_dimension_accuracy()
L224-277: suggest_weight_adjustment()
L279-353: suggest_threshold_adjustment()  ← 生成建议但未写入
L355-378: get_optimization_history()
L380-389: get_current_params()
L395-496: 内部辅助方法（_get_sample_count, _calc_overall_accuracy, _read_weights, _write_weights, _record_optimization 等）
```

## config_weights.json 当前结构

```json
{
  "a_stock": {"weights": {"kline": 0.2632, "fundamental": 0.2105, "capital_flow": 0.3684, "news": 0.1579}},
  "hk_stock": {"weights": {"kline": 0.2739, "fundamental": 0.396, "capital_flow": 0.2739, "news": 0.0561}},
  "rating_mapping": {
    "强烈推荐买入": {"min": 85, "max": 100, "label": "强烈推荐买入"},
    "推荐买入": {"min": 70, "max": 84, "label": "推荐买入"},
    "持有观望": {"min": 50, "max": 69, "label": "持有观望"},
    "建议减仓": {"min": 30, "max": 49, "label": "建议减仓"},
    "强烈建议卖出": {"min": 0, "max": 29, "label": "强烈建议卖出"}
  }
}
```

## 验收标准（14项，PM将逐项核验）

1. 冷却期：7天内同方向手动触发被拒绝（adjusted=False + cooldown_remaining>0）
2. 冷却期不阻止反向调整
3. 冷却期从 strategy_params 读取最近 optimization_log
4. 阈值写入后 config_weights.json 的 rating_mapping 变化
5. 阈值单次调整 <=2分
6. 阈值不偏离基准超过5分
7. 相邻档位不重叠
8. 阈值安全阀：准确率不降低
9. 安全阀触发时阈值回滚
10. 权重与阈值安全阀独立
11. config_weights.json 无 BOM
12. strategy_params 记录含 type=thresholds
13. python app.py 一键启动正常
14. 回归：现有功能不受影响

## 交付物

1. 修改后的 `modules/optimizer_engine.py`
2. 自验报告 `reports/dev_selftest_B6.md`（逐项对照14条验收标准，附执行截图/输出）

## 执行步骤建议

1. 先完整阅读 `modules/optimizer_engine.py` 理解现有逻辑
2. 实现 M9-COOLDOWN（冷却期）
3. 实现 M9-THRESHOLD-APPLY（阈值写入+安全阀）
4. 本地运行 `python app.py` 确认启动正常
5. 调用 `POST /api/optimizer/run` 测试冷却期和阈值写入
6. 编写自验报告

## 重要提醒

- **先读任务书原文**：`docs/tasks/dev_tasks_20260723_B6.md` 有更详细的伪代码和技术方案
- **不要修改任何其他文件**
- **不要添加新的 pip 依赖**
- **不要触碰 data_collector.py**
- 完成后生成自验报告到 `reports/dev_selftest_B6.md`