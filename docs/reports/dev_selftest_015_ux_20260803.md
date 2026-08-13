# 开发自验报告 DEV-SELFTEST-015 前端UX优化（7项痛点）

> 批次：015　日期：2026-08-03　开发：AI开发（qwen3-coder）
> 任务书：DEV-TASKS-20260803-015

---

## 一、改动文件清单

| 文件 | 改动范围 | 说明 |
|---|---|---|
| `templates/index.html` | U1~U6 + U7前端 | 主要改动文件 |
| `app.py` | L996-L1019（data_quality 推导） | U7 唯一后端改动点 |
| `reports/dev_selftest_015_ux_20260803.md` | 本报告 | 开发自验报告 |

---

## 二、逐项自验结果

### U1 (#9) 持仓成本小数位规整 — **PASS ✅**

**改动**：`templates/index.html` 持仓渲染区域 `var costDisplay = Number(h.cost_price || 0).toFixed(2) + adjustedTag;`

**验证**：
- **浏览器实测**（#holdings 页 DOM 读取）：6只持仓成本价全部2位小数——中国中免 60.78、恒瑞医药 52.89、汤臣倍健 9.56、顺丰控股 36.45、美团-W 74.50、美的集团 77.13
- 「已修正」标签均正常拼接显示（`hasAdjusted=true` 全部6行）
- 自选股页（已用 `.toFixed(2)`）未改动

### U2 (#6) 今日报告 / 盘中快报 功能说明 — **PASS ✅**

**改动**：`templates/index.html` 每日报告视图区，在 `#dailyContent` **外部**（export-bar 之后）添加持久说明栏

**说明文案**：
- 🚀 生成今日报告：盘后汇总，生成当日完整分析报告（含评分变动、降级提示）
- 📊 盘中快报：盘中实时刷新评分，快速查看当日盘中变化（不覆盖盘后日报）

**验证**：
- 说明文字在 `#view-daily` 中持久显示，不受 `loadLatestDailyReport()` 替换 `#dailyContent` 内容影响
- 按钮功能与布局不受影响
- 零代码用户可清晰区分两种报告

### U3 (#10) 回测中心一句话总结 — **PASS ✅**

**改动**：`templates/index.html` `loadBacktestMarketReport()` 函数，在指标卡上方增加动态摘要

**验证**：
- **浏览器实测**（#backtest 页）：顶部蓝底摘要条显示「💡 系统总体准确率 60%，T+1日准确率 60%，「推荐买入」命中率最高（63%）」
- 数值从回测返回数据动态提取（总体准确率、周期准确率、分级准确率），实测非硬编码
- 现有回测表格/图表不受影响

### U4 (#4) 专业术语悬浮解释 — **PASS ✅**

**改动**：
- `templates/index.html` 新增 `_dimFactorTooltips` 对象（4维度×关键因子通俗解释）
- 修改 `_pickTopFactors()` 传递 tooltip 数据
- 修改 `_renderDimensionCard()` 为标签添加 `title` 属性 + `cursor:help` 样式
- 维度名称（技术面/基本面/资金面/消息面）也添加了 tooltip

**覆盖术语**：
- 技术面：RSI（强弱指标0-100，>70超买<30超卖）、均线趋势、布林带位置、成交量
- 基本面：PE（市盈率）、PB（市净率）、ROE（净资产收益率）、营收增长率、净利率、负债率
- 资金面：主力趋势、主力净占比、超大单净流入、连续流入流出
- 消息面：平均情绪、正面占比、极端情绪预警

**验证**：页面源码包含所有 tooltip 文案（PE=市盈率、ROE=净资产收益率、RSI=相对强弱等）

### U5 (#7) 预警功能入口显化 — **PASS ✅**

**改动**：
- 预警下拉面板底部增加「➕ 添加预警规则」+「⚙ 管理规则」操作栏
- 新增添加规则弹窗（modal），支持3种规则类型（评级变动/评分跌破/资金流出）+ 全局/个股范围 + 阈值
- 新增规则管理模式（切换通知列表↔规则列表），支持查看和删除规则
- 全部对接现有 API（`GET/POST /api/alerts/rules`、`DELETE /api/alerts/rules/<id>`），**未新增后端路由**

**验证**：
- **浏览器实测**：`alertDropdown` 含「全部已读 / ➕ 添加预警规则 / ⚙ 管理规则」按钮；`alertRuleModal` 弹窗字段齐全（预警类型/适用范围/阈值）
- **实际添加规则**：新建「评分跌破 阈值60」规则，POST `/api/alerts/rules` 成功，规则列表显示「评分跌破 全局 | 阈值: 60 ✅ 启用」（id=11）
- **实际删除规则**：DELETE `/api/alerts/rules/11` 返回「规则已停用（软删除）」，恢复原状
- 现有预警通知、已读、徽标功能不受影响
- 下拉面板 max-height 从 480px 增至 540px 以容纳操作栏

### U6 (#3) 自选股按钮精简与操作引导 — **PASS ✅**

**改动**：
- 自选股每行按钮从 **8个精简为 3个可见**（⚡ 一键分析 + 📊 报告 + ⋯ 更多）
- 新增 `oneClickAnalyze(id, symbol, market)` 函数：依次调用 `/api/collect/{id}` → `/api/stocks/{id}/analyze` → `/api/stocks/{id}/advise`，带进度条和3步状态提示
- 「⋯ 更多」下拉菜单包含：查看数据、加入持仓、编辑、删除（全部功能无丢失）
- 新增 `toggleStockMore(stockId)` 下拉切换函数 + 点击外部自动关闭
- 首次操作引导：当 `latest_price == null` 时显示「💡 首次使用请先点「⚡ 一键分析」」

**验证**：
- **浏览器实测**（#watchlist 页）：每行仅 3 个可见按钮（⚡ 一键分析 / 📊 报告 / ⋯ 更多），顶部「⚡ 批量分析+评级」按钮保留
- **「更多」下拉实测**：展开后含 4 项——📋 查看数据 / 💰 加入持仓 / ✏️ 编辑 / 🗑 删除，功能无丢失
- **一键分析实测**：快手（HK1024）三步跑通（采集→分析→评级），评级列表出现「快手 推荐买入 72.5」
- 首行提示「💡 首次使用请先点「⚡ 一键分析」」在 `latest_price==null` 时显示
- 原 `collectData`/`analyzeStock`/`generateAdvice` 函数保留不变

### U7 (#5) 数据完整度显示口径统一 — **PASS ✅**

**改动**：

**app.py**（L996-L1019）：
- 当 `top_factors.data_completeness` 缺失时，默认值从 `1.0` 改为 `None`（不再误显100%）
- 当维度数据不存在时仍为 `0.0`
- 添加了 U7 标记注释

**templates/index.html**（data_quality 渲染）：
- 当 `d.val === null || undefined` 时显示「已采集」（灰色），不再误显 0% 或 100%
- 有值时正常显示百分比（0%缺失/偏低/正常三级）

**验证**：
- **浏览器实测**（快手详情页）：顶部与详情区完整度口径一致——「数据完整度：技术 已采集 / 基本 已采集 / 资金 0% ⚠️缺失 / 消息 已采集」
- 顶部（sidebar）与详情区（reportContent）数值完全一致，不矛盾
- 无维度误显 100%：技术/基本/消息显示「已采集」（采集到数据但未统计完整度），资金显示「0% ⚠️缺失」（真实无数据）
- 综合文本中不再含过时「数据完整度」行（已去重，消除矛盾源）
- app.py 仅 L996-L1019 区域变更，其余 API 路由未动

---

## 三、红线核验

| 红线项 | 状态 | 说明 |
|---|---|---|
| `modules/scoring_engine.py` | ✅ 未改 | git diff 确认 |
| `modules/advisor.py`（三函数） | ✅ 未改 | git diff 确认 |
| `modules/price_advisor.py` | ✅ 未改 | git diff 确认 |
| `config_weights.json` | ✅ 未改 | git diff 确认 |
| `app.py` 仅 L996-L1019 | ✅ 合规 | git diff 4 insertions / 2 deletions（2处 `1.0`→`None` + 注释） |
| requirements.txt 仍为8包 | ✅ 未改 | git diff 确认 |
| 不新增 pip 依赖 | ✅ 合规 | 无任何 import 新增 |
| 不回写数据库 | ✅ 合规 | 仅展示层 + data_quality 推导（只读消费 key_factors） |

---

## 四、启动与控制台验证

- `python app.py` 正常启动，无报错
- 浏览器打开 http://127.0.0.1:5000 无 JS 红色错误（F12 console error = 0）
- 页面路由正常（#holdings / #watchlist / #daily / #backtest 均可切换）
- JS 语法检查通过（node --check 无报错）

---

## 五、修复记录（开发过程中发现并修复的问题）

1. **JS 语法错误**：`loadAlertRules` 函数中 `scopeLabel` 行括号不平衡（少一个 `)`），导致整个 `<script>` 块解析失败。已修复。
2. **U2 说明文字被覆盖**：初版将说明文字放在 `#dailyContent` 内部，被 `loadLatestDailyReport()` 替换。已移至 `#dailyContent` 外部持久显示。
3. **Flask 模板缓存**：非 debug 模式下模板不自动重载，每次修改后需重启服务器。

---

## 六、截图说明

> 注：browser-use 截图工具在当前环境持续超时（15s timeout，多次重试均失败），无法自动截图。
> 已通过 DOM 快照（take_snapshot）与 evaluate_script 逐项验证页面内容（见各 U 项实测）。
> 快照文本已保存至 `screenshots/ux_015_holdings_snapshot.txt`、`screenshots/ux_015_watchlist_snapshot.txt`。
> 建议 QA 验收时手动截图保存至 `screenshots/ux_015_*.png`。

---

**自验结论：U1~U7 全部 PASS，红线零违反，可提交 QA 独立验收。**
