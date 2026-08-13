# 开发提示词 B13：P2 体验优化

> **用途：** 在独立 Quests 窗口中粘贴给开发 AI 执行。
> **任务书：** `docs/tasks/dev_tasks_2026-07-25_B13.md`

---

## 你的角色

你是 Stock Analyst 项目的前端开发。本批次 **仅修改 `templates/index.html`**，无后端改动。

## 项目信息

| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst` |
| 唯一修改文件 | `templates/index.html`（~5015行，单页应用） |
| 技术栈 | 原生 JS + 内联 CSS（无框架） |
| 约束 | 不引入任何新依赖、不修改后端 .py 文件 |

## 环境注意

- PowerShell 不支持 `&&`，用 `;`
- 路径含空格需引号
- 修改后无需重启服务（Flask 模板热加载）

## 红线

- 不修改任何 `.py` 文件
- 不引入外部 CDN/JS 库
- 不修改 `config_weights.json`

---

## T1：修复日报/看板评级颜色

### 问题
日报和看板中评级 badge 可能无颜色背景。

### 诊断步骤（先做）
1. 找到 `getRatingClass()` 函数（约 L4999-5009）
2. 找到 CSS 中 `.rating-strong-buy` 等定义（约 L532-536）
3. 找到日报渲染代码中 `class="rating-badge ' + getRatingClass(r.rating) + '"`（约 L4032）
4. 确认：`getRatingClass('推荐买入')` 返回什么？CSS 选择器能否匹配？

### 修复
确保以下 CSS 类名与 `getRatingClass()` 返回值完全匹配：
```css
.rating-strong-buy  { background: #c8e6c9; color: #1b5e20; }  /* 强烈推荐买入 - 绿 */
.rating-buy         { background: #dcedc8; color: #33691e; }  /* 推荐买入 - 浅绿 */
.rating-hold        { background: #fff9c4; color: #f57f17; }  /* 持有观望 - 黄 */
.rating-reduce      { background: #ffe0b2; color: #e65100; }  /* 建议减仓 - 橙 */
.rating-strong-sell { background: #ffcdd2; color: #b71c1c; }  /* 强烈建议卖出 - 红 */
```

如果 `getRatingClass()` 返回的类名与上述不匹配，修正函数或 CSS。

### 验证
在浏览器中打开日报/看板页面，确认 5 档评级有对应颜色背景。

---

## T2：批量分析进度条（核心）

### 问题
批量分析 20 只股票需 ~5 分钟，用户只看到静态"正在分析..."文字，无进度反馈。

### 方案
将 `batchAnalyze()` 函数（约 L2185-2217）从"单次 POST 等全部完成"改为"逐只调用 + 实时进度条"。

### 已有 API
- 单股分析：`POST /api/stocks/<stock_id>/analyze` → 返回 JSON（含 rating, total_score, stock_code, stock_name 等）
- 无需修改后端

### 实现要求
1. 点击"批量分析"后，显示：
   - 绿色进度条（从 0% 到 100%）
   - 文字："正在分析第 X/Y 只股票（代码：XXXXXX）..."
2. 每完成一只，进度条前进一格
3. 单只失败不中断，记录错误继续下一只
4. 全部完成后：
   - 进度条 100%
   - 显示结果表格（复用现有 `renderBatchResults()` 的表格格式）
   - 自动调用 `loadRatings()` 刷新评级列表
5. 显示总耗时

### 代码框架
```javascript
function batchAnalyze() {
    var ids = [];
    document.querySelectorAll('.stock-cb:checked').forEach(function(cb) { ids.push(parseInt(cb.value)); });
    if (ids.length === 0) { alert('请先勾选要分析的股票'); return; }

    var area = document.getElementById('collectArea');
    var total = ids.length;
    var done = 0;
    var results = [];
    var startTime = Date.now();

    // 渲染进度条 UI
    area.innerHTML = '...进度条HTML...';
    area.scrollIntoView({ behavior: 'smooth' });

    function processNext(idx) {
        if (idx >= total) { finishBatch(); return; }
        // 更新状态文字
        // 调用 POST /api/stocks/{ids[idx]}/analyze
        // 成功/失败都 push 到 results
        // done++, 更新进度条宽度
        // processNext(idx + 1)
    }

    function finishBatch() {
        // 100% + 结果表格 + loadRatings()
    }

    processNext(0);
}
```

### 注意
- 保留原有 `renderBatchResults()` 函数不删除（进度条完成后调用它渲染表格）
- 进度条样式：圆角、绿色填充、灰色背景、transition 动画
- 错误处理：fetch 失败时 catch 并继续

### 验证
勾选 2-3 只股票 → 点击批量分析 → 确认进度条实时推进 → 完成后表格正常。

---

## T3：术语帮助提示

### 方案
在关键术语处添加 `title` 属性（鼠标悬停显示解释），不引入新组件。

### 需添加 title 的位置

1. **看板页面** 的"综合评分"表头 → `title="四维加权总分（满分100）：技术面+基本面+资金面+消息面"`
2. **回测页面** 的"T+1/T+5/T+20" → `title="评级发出后第1/5/20个交易日的股价涨跌幅"`
3. **回测页面** 的"准确率" → `title="评级方向正确的比例（排除中性无法判定的记录）"`
4. **评级 badge** 的 5 档文字 → 各自 title 说明分数区间：
   - 强烈推荐买入 → `title="综合评分≥85"`
   - 推荐买入 → `title="综合评分70-84"`
   - 持有观望 → `title="综合评分50-69"`
   - 建议减仓 → `title="综合评分30-49"`
   - 强烈建议卖出 → `title="综合评分<30"`

### 实现
在渲染 HTML 时，给对应 `<th>` 或 `<span>` 添加 `title="..."` 属性即可。

### 验证
鼠标悬停在术语上，浏览器原生 tooltip 显示解释文字。

---

## 自验报告格式

```markdown
# B13 开发自验报告

## T1 评级颜色
- getRatingClass('推荐买入') 返回: [值]
- CSS 匹配: [是/否]
- 日报页面颜色: [截图或描述]
- 看板页面颜色: [截图或描述]

## T2 进度条
- 批量分析 3 只股票: [成功/失败]
- 进度条实时推进: [是/否]
- 单只失败不中断: [是/否]
- 完成后表格正常: [是/否]
- 评级列表自动刷新: [是/否]

## T3 术语提示
- 看板"综合评分" tooltip: [有/无]
- 回测"T+1" tooltip: [有/无]
- 评级 badge tooltip: [有/无]

## 红线
- 未修改 .py 文件: [是]
- 未引入外部库: [是]
- 未修改 config_weights.json: [是]
```

---

*PM 签发 | 2026-07-25 | B13*
