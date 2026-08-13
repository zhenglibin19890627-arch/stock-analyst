# B22 自验报告 — 消息面数据维度扩展

**任务书**：`docs/tasks/dev_tasks_20260726_B22.md`
**执行日期**：2026-07-26
**推荐模型**：glm5.2

---

## 一、修改清单

| # | 文件 | 修改内容 |
|---|---|---|
| 1 | `modules/data_contract.py` L112-114 | StockData 新增 3 字段：`news_count` / `news_positive_ratio` / `news_negative_count` |
| 2 | `modules/data_contract.py` L200 | NEWS 集合从 2 字段扩展为 5 字段 |
| 3 | `modules/data_contract.py` L226-227 | 注释更新为"news: 5个字段"；`news_total = 5`（原 2） |
| 4 | `modules/data_contract.py` L167-170 | DEGRADATION_RULES 补充 3 条降级规则（默认值填充型） |
| 5 | `modules/data_contract.py` L301-303 | `to_analysis_dict()` 补充 3 个新字段输出 |
| 6 | `modules/data_adapter.py` L378-391 | 消息面映射扩展：从 `news_sentiment` 表读取 `total_count`/`positive_count`/`negative_count` |
| 7 | `modules/data_adapter.py` L427-429 | StockData 构造传参补充 3 个新字段 |

---

## 二、验收结果

### V1~V3 代码审查（PASS）

- StockData 含 `news_count` / `news_positive_ratio` / `news_negative_count` 三字段 ✓
- NEWS 集合 = {news_sentiment, holder_increase, news_count, news_positive_ratio, news_negative_count}（5 个元素）✓
- `news_total = 5` ✓

### V4 数据映射验证（PASS）

stock 27 实测映射结果：
```
news_count=10, news_positive_ratio=0.9, news_negative_count=1
```
对应 news_sentiment 表数据：`positive_count=9, negative_count=1, total_count=10` → `0.9 = round(9/10, 2)` ✓

### V5 news 完整度 ≥ 75%（PASS）

27/27 只股票 news 完整度 = **0.80**（4/5：news_sentiment + news_count + news_positive_ratio + news_negative_count 有值，holder_increase 低频缺失）。

完整度从旧值 **0.50 → 0.80**，提升 60%。

API 核验（POST /api/stocks/27/advise）：
```
data_quality.news = 0.8  ≥ 0.75  ✓
total_score = 73.7
rating = 推荐买入
```

### V6 红线守恒（PASS）

- `data_collector.py` 三处 `if False`（L1645 / L1684 / L1717）不变 ✓
- `config_weights.json` 未修改 ✓
- `scoring_engine.py` 未修改 ✓
- `templates/index.html` 未修改 ✓
- `app.py` 未修改 ✓
- 无新 pip 依赖 ✓

### V7 评分不变（PASS — 控制变量法严格证明）

**方法**：同一数据快照下，对每只股票分别用「有新字段」和「清空新字段」两种 StockData 调用 `analyze()`，对比 total_score 与 sentiment_score。

**原理**：`scoring_engine.py` 的 `NEWS_SUBITEMS`（L179-183）只引用 `["news_sentiment"]` 和 `["holder_increase"]`，`score_dimension` → `adjust_subitem_weight` → `_subitem_completeness` 只检查 `SubItem.fields` 列表中的字段。新增的 `news_count` / `news_positive_ratio` / `news_negative_count` **不在任何 SubItem 的 fields 中**，因此从代码逻辑上不可能影响评分。

**结果**：
```
27/27 只股票 total_score 和 sentiment_score 完全一致 → all_match = True
```
| stock_id | total(有新字段) | total(无新字段) | sent(有) | sent(无) | 一致? |
|---|---|---|---|---|---|
| 4 | 55.6 | 55.6 | 90.6 | 90.6 | YES |
| 27 | 73.7 | 73.7 | 81.4 | 81.4 | YES |
| ... | ... | ... | ... | ... | YES (全27只) |

---

## 三、自验结论

| 验收项 | 结果 |
|---|---|
| V1 StockData 含 3 新字段 | PASS |
| V2 NEWS 集合 5 元素 | PASS |
| V3 news_total = 5 | PASS |
| V4 数据正确映射 | PASS |
| V5 news 完整度 ≥ 0.75 | PASS（0.80，27/27） |
| V6 红线守恒 | PASS |
| V7 评分不变 | PASS（控制变量法 27/27 一致） |

**全部 7 项验收标准通过。**
