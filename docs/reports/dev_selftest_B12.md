# B12 开发自验报告

> 批次：B12 | 回测评级准确率专项修复 | 日期：2026-07-25

## T1 去重 + UNIQUE

- 唯一索引 `idx_ratings_unique`: **存在**（unique=1）
- 重复记录组数: **0**（迁移清理 675 条重复记录）
- 600276 07-16 记录数: **1**（原 32 条）
- ratings_history 总记录数: 173

修改文件：`database/db_manager.py`（DDL 添加 `UNIQUE(stock_id, rating_date)` + init_db 末尾幂等迁移逻辑）

## T2 price_at_rating

- 修复后已有数据 price_at_rating 与 K 线收盘价不一致记录数: **0**（修正 173 条历史数据）
- backtest_results vs ratings_history price_at_rating 不一致: **0**
- 新写入逻辑：`_save_rating()` 根据 `rating_date` 查询 `raw_kline` 中 `trade_date <= rating_date` 的最近收盘价

修改文件：`modules/advisor.py`（`_save_rating()` 函数）

## T3 score_date

- `analyze_from_db(4).score_date` = **2026-07-24**（周五，最近交易日）
- raw_kline 最新 trade_date = 2026-07-24
- 是否为最近交易日: **是**（非今天 2026-07-25 周六）

修改文件：`modules/scoring_engine.py`（`analyze()` 函数 score_date 赋值逻辑）

## T4 normalize_rating

- `normalize_rating('D', 74.8)` = **强烈建议卖出** ✅
- `normalize_rating('B', 71.9)` = **持有观望** ✅
- `normalize_rating('B+', 78.9)` = **推荐买入** ✅
- `normalize_rating('推荐买入', 70.1)` = **推荐买入** ✅（新格式不受影响）
- `normalize_rating('持有观望', 55.0)` = **持有观望** ✅（新格式不受影响）
- `normalize_rating('D')` = **强烈建议卖出** ✅（无 score）
- `normalize_rating('B+')` = **推荐买入** ✅（无 score）
- 全部断言: **通过**
- 矛盾检测 WARNING 日志: 正常输出（回测重跑时产生多条矛盾告警）

修改文件：`modules/scoring_engine.py`（`normalize_rating()` 函数）

## T5 回测重跑

| 评级 | 修复前 total/acc | 修复后 total/acc |
|---|---|---|
| 推荐买入 | 22 / 0.0% | 2 / 0.0% |
| 持有观望 | 499 / 58.8% | 70 / 73.7% |
| 建议减仓 | 275 / 46.4% | 92 / 63.0% |
| 强烈建议卖出 | 6 / N/A | 9 / 60.0% |

- 重跑结果: total=173, success=173, errors=0
- "推荐买入"记录数 = 2 ≤ 唯一(stock+date)组合数 = 2 ✅
- price_at_rating 与 K 线收盘价一致 ✅

## T6 评估结论（可选）

### 模拟回测（技术面单维度映射）vs 真实回测（四维加权）

| 数据源 | 建议减仓 acc | 持有观望 acc | 推荐买入 acc |
|---|---|---|---|
| 模拟（技术面单维） | 79.1% | 68.4% | 43.3% |
| 真实（四维加权） | 63.0% | 73.7% | 0.0%(n=2) |

### 评估结论

1. **技术面单维度映射 vs 四维加权映射差异**：模拟回测的"建议减仓"准确率(79.1%)高于真实回测(63.0%)，因为技术面指标对下行趋势捕捉更敏感。但"推荐买入"两者都偏低（模拟43.3%，真实0%仅2条样本不可靠），技术指标滞后性导致买入信号出现在局部高点。

2. **是否建议模拟回测使用四维评分**：**不建议短期改造**。模拟回测依赖历史K线重建评级，而历史四维数据（基本面/资金面/消息面）无法回溯。若要改造需引入历史数据快照机制，成本高、收益不明确。当前模拟数据(is_simulated=1)已与真实数据分离统计，不影响真实回测结论。

3. **JUDGEMENT_MATRIX 阈值**：当前"推荐买入" `correct_min=1.0%` 不建议调低。真实回测仅2条"推荐买入"样本（均为07-23，T+1分别为-2.66%和-9.84%），样本量不足以支撑阈值调整。调低阈值会降低评级可信度。建议持续累积样本后再评估。

## 红线核验

| # | 红线 | 核验结果 |
|---|---|---|
| 1 | data_collector.py L1645/L1684/L1717 `if False` 未触碰 | ✅ 未触碰 |
| 2 | 无新 pip 依赖 | ✅ 未触碰 requirements.txt |
| 3 | config_weights.json 未修改 | ✅ 未触碰 |
| 4 | backtest_engine `_judge()`/`JUDGEMENT_MATRIX` 未修改 | ✅ 未触碰 |
| 5 | news_collector.py 未修改 | ✅ 未触碰 |

## 涉及文件清单

| 文件 | 修改类型 | 任务 |
|---|---|---|
| `database/db_manager.py` | DDL 添加 UNIQUE + 幂等迁移逻辑 | T1 |
| `modules/advisor.py` | `_save_rating()` price_at_rating 来源改为 K线查询 | T2 |
| `modules/scoring_engine.py` | `analyze()` score_date 用 trade_date | T3 |
| `modules/scoring_engine.py` | `normalize_rating()` 矛盾时优先评级字符串 | T4 |

*开发工程师 | 2026-07-25 | B12*
