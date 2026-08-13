# B20 自验报告 — v5 引擎四维因子明细输出补充

**批次**：B20  
**日期**：2026-07-26  
**任务书**：docs/tasks/dev_tasks_20260726_B20.md  
**修改文件**：modules/advisor.py（仅此 1 个文件）

---

## 一、修改摘要

### 改写 `_build_v5_factors`（advisor.py）

旧版仅输出 `dimension_score` + `data_completeness`，导致前端 `_pickTopFactors` 全部落空。
新版签名改为 `_build_v5_factors(stock_id, stock_data, v5_result, dim_key)`：
- 保留旧字段（`dimension_score` / `data_completeness` / `*_status` 降级提示），确保 report-latest 的 data_quality 计算不破坏
- 新增按维度从 `StockData` 契约（含已计算的 MA/RSI/BOLL 等技术指标）+ DB 原始表构建具体因子

### 数据源选择（关键决策）

任务书 L99-109 假设 `raw_kline` 表含 `close_5ma/close_20ma/rsi_14` 等列，**但实测 DB 中 raw_kline 只有 OHLCV 基础列**（open/close/high/low/volume/pct_change），技术指标列不存在。
因此采用 `data_adapter.load_stockdata_from_db(stock_id)` 作为统一数据源——它内部已从 OHLCV 计算 MA/RSI/BOLL/MACD/KDJ，并加载基本面/资金面/消息面，**不触碰任何红线文件**（不改 scoring_engine.py 评分逻辑）。

### 新增 4 个维度因子构建函数

| 函数 | 输出的前端期望 key |
|---|---|
| `_build_kline_factors` | ma_trend / rsi_status / recent_trend / volume / boll_position |
| `_build_fundamental_factors` | pe_ratio / roe / pb_ratio / revenue_growth / debt_ratio / net_margin |
| `_build_capital_factors` | main_trend / main_pct / super_large / main_avg_5d / consecutive |
| `_build_news_factors` | avg_sentiment / positive_ratio / news_activity / top_news |

调用处 `_convert_v5_to_legacy`（L573）改为：加载一次 StockData 后传入。

---

## 二、验收结果

测试股票：海康威视（stock_id=27，002415），v5 引擎

### V1 — `/advise` 各维度 factors 含具体因子（每维度≥2）✅ PASS

| 维度 | 命中因子 key | 数量 |
|---|---|---|
| kline | boll_position, ma_trend, recent_trend, rsi_status, volume | 5 |
| fundamental | debt_ratio, net_margin, pb_ratio, pe_ratio, revenue_growth, roe | 6 |
| capital_flow | consecutive, main_avg_5d, main_pct, main_trend | 4 |
| news | avg_sentiment, news_activity, positive_ratio, top_news | 4 |

因子值示例：`ma_trend=多头排列(MA5=35.26 > MA20=34.26)`、`pe_ratio=19.79`、`main_trend=主力净流入475万元`、`avg_sentiment=+0.70(正面)`

### V2 — `/report-latest` factors 含具体因子 ✅ PASS

重新生成 stock_id=27 今日 daily_reports 行后，report-latest 读取的 top_factors：
- kline: ma_trend, recent_trend, rsi_status（3）
- fundamental: pe_ratio, revenue_growth, roe（3）
- capital_flow: consecutive, main_pct, main_trend（3）
- news: avg_sentiment, positive_ratio, top_news（3）

### V3 — kline 含 ma_trend 或 rsi_status ✅ PASS（两者均有）

### V4 — fundamental 含 pe_ratio 或 roe ✅ PASS（两者均有）

### V5 — 前端四维详情卡片 ⚠️ 后端已就绪，受前端既存 bug 阻塞（见第三节）

### V6 — 红线守恒 ✅ PASS

- data_collector.py L1645/L1684/L1717 三处 `if False` 完好
- config_weights.json rating_mapping（80/65/50/30）完好
- daily_report.py `_pick_top_factors` 优先级表完好
- templates/index.html 未修改
- scoring_engine.py 评分逻辑未修改
- 无新 pip 依赖

### V7 — 评分不变 ✅ PASS

修改前后 total_score=73.7 / rating=推荐买入 完全一致（未触碰评分逻辑）。

---

## 三、附加发现（独立上报）— 前端 `var dims` 变量遮蔽 bug

**严重度**：高（阻断 V5 视觉验证，但非 B20 后端范围）

**现象**：分析报告页（#report）四维详情卡片对 v5 引擎报告显示"无数据/请先采集数据"，而非预期的因子内容。

**根因**：`templates/index.html` L3862 存在变量遮蔽——

```javascript
function renderFullReport(adviseData, klineData, stockId) {
    var dims = adviseData.dimensions || {};   // L3809 真正的维度数据
    ...
    if (adviseData.data_quality) {
        var dq = adviseData.data_quality;
        var dims = [                            // L3862 ⚠️ var 函数作用域，覆盖 L3809！
            {name:'技术', val: dq.technical}, ...
        ];
        ...
    }
    ...
    html += _renderDimensionCard('kline', '技术面', dims.kline || dims.technical);  // L3903 dims.kline → undefined
}
```

由于 JS `var` 是函数作用域，L3862 的 `var dims = [...]` 覆盖了 L3809 的维度对象。v5 报告必有 data_quality，故该块总执行，导致 L3903 `dims.kline` 为 undefined，卡片走"无数据"分支。该 bug 系 B15-T4（数据完整度增强）引入。

**实证（已验证后端正确）**：在浏览器内用 `/advise` 返回的真实 kline 维度（status=ok，含 ma_trend 等 7 因子）直接调用 `_renderDimensionCard('kline','技术面', k)`，输出正确卡片 HTML（"权重 26% 技术面 78 ✅ 健康" + 因子列表）。证明：
1. 后端因子输出正确（B20 修复有效）
2. `_renderDimensionCard` 渲染逻辑本身正确
3. 视觉"无数据"纯由 `var dims` 遮蔽导致

**为何不在 B20 修复**：`templates/index.html` 为 B20 红线（绝对禁止修改）。修复需将 L3862 的 `var dims` 改名为 `var dqDims`（或类似），属于独立前端任务。

**建议**：新开一个前端 hotfix 任务（如 B20-frontend）修此 1 行。

---

## 四、验证过程

1. `python -m py_compile modules/advisor.py` → 通过
2. 直接调用 `_convert_v5_to_legacy(27, v5)` → 四维因子全部输出（PASS）
3. 清理旧 Flask 进程（PID 35796 占用 5000 端口，早于本次修改）后重启
4. POST `/api/stocks/27/advise` → factors 含 ma_trend/pe_ratio/main_trend/avg_sentiment（PASS）
5. 重新生成今日 daily_reports（复用报告内部链路，跳过网络采集）
6. GET `/api/stocks/27/report-latest` → top_factors 含真实因子（PASS）
7. 浏览器 `evaluate_script` 实证 `_renderDimensionCard` 渲染正确
8. Grep 核验红线（V6 PASS）

截图：screenshots/b20_report_page.png（记录 var dims 遮蔽导致的"无数据"现状）

---

## 五、结论

B20 后端任务**完成**：v5 引擎四维因子明细已正确输出，覆盖 /advise 与 /report-latest 两条 API 路径（V1-V4、V6、V7 通过）。
V5（前端卡片视觉）受 index.html 既存 `var dims` 遮蔽 bug 阻塞，已独立上报，建议另开前端任务修复（1 行改动）。
