# 开发提示词 B24

**推荐模型：glm5.2（GLM Plan）**
**任务书：docs/tasks/dev_tasks_20260726_B24.md**

---

## 你的任务

在前端 `templates/index.html` 的消息面因子配置中补充 `news_count` 字段，使四维详情卡片能展示新闻数量。

## 修改方案

**文件**：`templates/index.html`

### 修改1：L4630 `_factorPriority.news`

```javascript
// 原：
news: ['avg_sentiment', 'positive_ratio', 'top_news', 'news_activity', 'extreme_warning']

// 改：
news: ['avg_sentiment', 'positive_ratio', 'news_count', 'top_news', 'news_activity', 'extreme_warning']
```

### 修改2：L4651-4655 `_dimFactorLabels.news`

在现有对象中新增一行 `news_count: '新闻数量'`。

## 红线

仅修改这两处，其他任何文件不可动。

## 自验

启动 Flask，浏览器查看海康威视报告页，确认消息面卡片显示"新闻数量"。
