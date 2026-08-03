# 开发提示词 B22

**推荐模型：glm5.2（GLM Plan）**
**任务书：docs/tasks/dev_tasks_20260726_B22.md**

---

## 你的任务

将 news_sentiment 表中已采集的丰富数据（新闻数量、正负面比例等）映射到 StockData 模型，扩展消息面完整度从 50% 提升到 ≥75%。

## 项目环境

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（路径含空格）
- PowerShell 不支持 `&&`，用 `;` 分隔命令

## 根因

news_sentiment 表已有 positive_count/negative_count/total_count 等字段（100% 有值），但 StockData 模型只映射了 avg_sentiment（1个字段）。消息面完整度只有 2 个字段（news_sentiment + holder_increase），holder_increase 低频缺失导致完整度硬顶 50%。

## 修改方案

### 修改1：modules/data_contract.py — StockData 新增 3 个字段

在 `holder_increase` 字段（L109）之后新增：

```python
holder_increase: Optional[bool] = Field(default=None, description='大股东/高管是否增持')
# B22 新增
news_count: Optional[int] = Field(default=None, description='新闻总数')
news_positive_ratio: Optional[float] = Field(default=None, description='正面新闻占比(0~1)')
news_negative_count: Optional[int] = Field(default=None, description='负面新闻数量')
```

### 修改2：modules/data_contract.py — NEWS 集合扩展

L193：
```python
# 原：NEWS = {"news_sentiment", "holder_increase"}
# 改：
NEWS = {
    'news_sentiment',
    'holder_increase',
    'news_count',
    'news_positive_ratio',
    'news_negative_count',
}
```

### 修改3：modules/data_contract.py — news_total 改为 5

L227：
```python
# 原：news_total = 2
# 改：news_total = 5
```

同时更新 L218 注释为 `news: 5个字段`。

### 修改4：modules/data_contract.py — DEGRADATION_RULES 补充

L165 `holder_increase` 之后新增 3 条降级规则：
```python
"news_count": "消息面-新闻量子项：维度内子权重保持，使用中性值填充（默认值填充型）",
"news_positive_ratio": "消息面-新闻情绪子项：维度内子权重保持，使用中性值填充（默认值填充型）",
"news_negative_count": "消息面-新闻情绪子项：维度内子权重保持，使用中性值填充（默认值填充型）",
```

### 修改5：modules/data_contract.py — to_analysis_dict 补充输出

L291 `holder_increase` 之后补充：
```python
"news_count": self.news_count,
"news_positive_ratio": self.news_positive_ratio,
"news_negative_count": self.news_negative_count,
```

### 修改6：modules/data_adapter.py — 消息面映射扩展

L377-382 区域，在现有 news_sentiment 映射之后，增加新字段映射：

```python
news_sentiment = None
news_count = None
news_positive_ratio = None
news_negative_count = None
if news:
    avg_sent = news.get('avg_sentiment')
    if avg_sent is not None:
        news_sentiment = float(avg_sent)
    # B22: 扩展消息面字段
    total = news.get('total_count')
    pos = news.get('positive_count')
    neg = news.get('negative_count')
    if total is not None and total > 0:
        news_count = int(total)
        news_negative_count = int(neg) if neg is not None else 0
        if pos is not None:
            news_positive_ratio = round(pos / total, 2)
```

然后在 StockData 构造中（约 L414）增加参数：
```python
news_sentiment = (news_sentiment,)
news_count = (news_count,)
news_positive_ratio = (news_positive_ratio,)
news_negative_count = (news_negative_count,)
main_net_inflow = (main_net_inflow,)
```

## 红线（绝对禁止）

1. **data_collector.py** 不可修改
2. **config_weights.json** 不可修改
3. **scoring_engine.py** 评分逻辑不动（新字段仅用于完整度计算，不参与评分）
4. **templates/index.html** 前端不动
5. **app.py** API 路由不动
6. **不引入**新 pip 依赖

## 自验要求

1. 修改后执行 force 重跑：
   ```python
   from modules.daily_report import generate_daily_report

   generate_daily_report(force=True)
   ```

2. API 调用核验 data_quality.news：
   - `POST http://127.0.0.1:5000/api/stocks/27/advise`
   - 检查返回 data_quality.news ≥ 0.75（从当前 0.50 提升）

3. Grep 核验红线守恒

自验报告归档至 `reports/dev_selftest_B22.md`。
