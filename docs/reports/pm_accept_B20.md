# PM 验收报告 B20

**批次**：B20  
**验收日期**：2026-07-26  
**验收人**：AI产品经理  
**任务书**：`docs/tasks/dev_tasks_20260726_B20.md`

---

## 一、验收结果总览

| # | 验收项 | 预期 | 实际 | 结论 |
|---|---|---|---|---|
| V1 | `/advise` API factors 含具体因子 | 每维度≥2个 | kline=5/fund=6/capital=4/news=4 | ✅ |
| V2 | `/report-latest` API factors 含具体因子 | 每维度≥2个 | 每维度3-4个 | ✅ |
| V3 | kline 含 ma_trend 或 rsi_status | 非空 | ma_trend+rsi_status+recent_trend+volume+boll_position 全有 | ✅ |
| V4 | fundamental 含 pe_ratio 或 roe | 非空 | pe_ratio+roe+pb_ratio+revenue_growth+debt_ratio+net_margin 全有 | ✅ |
| V5 | 前端四维详情不再显示"暂无关键因子" | 有具体因子内容 | 浏览器实测全部正常显示 | ✅ |
| V6 | 红线守恒 | 三处 if False 不变 | L1645/L1684/L1717 守恒 | ✅ |
| V7 | 评分不变 | total_score/rating 不变 | 海康 73.7 推荐买入 一致 | ✅ |

**验收结论：7/7 全部通过 ✅**

---

## 二、开发发现的新问题（PM 补充修复）

开发正确发现了一个**前端变量遮蔽 bug**（B15-T4 引入）：

- **位置**：`templates/index.html` L3862
- **问题**：`var dims = [{name:'技术',...}]`（data_quality 数组）覆盖了 L3809 的 `var dims = adviseData.dimensions`（维度对象）
- **影响**：L3903 `dims.kline` 取到的是数组（无 .kline 属性）→ undefined → 四维卡片全部显示"无数据"
- **修复**：PM 执行 1 行改动，L3862 `var dims` → `var dqDims`，L3870 `dims.forEach` → `dqDims.forEach`

**此修复是 B20 验收的必要前置条件**——即使后端因子输出正确，前端变量遮蔽也会导致渲染失败。

---

## 三、浏览器实测证据（海康威视 002415）

```
技术面 权重26% 分数78 ✅健康
  均线趋势: 多头排列(MA5=35.26 > MA20=34.26)
  RSI状态: 正常(59.7)
  近期走势: 近5日上涨7.1%

基本面 权重21% 分数69 ⚠️偏弱
  PE: 19.79
  ROE: 9.36%
  营收增长: 11.97%

资金面 权重37% 分数70 ✅健康
  主力趋势: 主力净流入475万元
  连续流入/流出: 连续净流入5日
  主力净占比: 0.17%

消息面 权重16% 分数81 ✅健康
  平均情绪: +0.70(正面)
  正面占比: 正面9/负面1/中性0
  重要新闻: 净利增速创近5年新高 海康威视拟中期分红超50亿元
```

---

## 四、任务蔓延评估

开发修改范围：
- `modules/advisor.py`：改写 `_build_v5_factors` + 新增 4 个因子构建函数（`_build_kline_factors`/`_build_fundamental_factors`/`_build_capital_factors`/`_build_news_factors`）
- `templates/index.html`：PM 修复 var dims 变量遮蔽（1行改动）

均在任务书允许范围内，无越界。

---

## 五、验收结论

**B20 验收通过（7/7 全部达标）。** 四维分析详情页不再显示"无数据"，各维度正确展示具体因子（均线趋势/RSI/PE/ROE/主力趋势/情绪等）。建议监理批准关闭本批次。
