# QA 验收报告：009 价格建议增强

| 项目 | 内容 |
|---|---|
| **任务编号** | QA-009-20260729 |
| **验收角色** | QA（质量保障工程师） |
| **验收日期** | 2026-07-29 |
| **任务书** | docs/tasks/qa_task_20260729_009.md |
| **交付物** | price_advisor.py(877行) + app.py(+13行) + index.html(+86/-21行) |
| **验收结论** | **PASS** |

---

## 一、验收总览

| 类别 | 项数 | PASS | FAIL | 备注 |
|---|---|---|---|---|
| 功能测试 TC1-TC8 | 8 | 8 | 0 | TC4数据差异（逻辑正确） |
| 回归测试 R1-R4 | 4 | 4 | 0 | 全部正常 |
| 红线复核 | 6 | 6 | 0 | 独立验证确认 |
| **合计** | **18** | **18** | **0** | |

**独立性声明**：本次验收全部由 QA 独立设计测试脚本、独立执行命令、独立判定结果，未引用开发自验报告结论。开发自验报告仅作为参考对照。

---

## 二、功能测试用例结果

### TC1：中国中免(21) — 有持仓已破止损（核心验证） — **PASS**

**执行方式**：`generate_advice(21)` → `generate_price_advice(21, advice_result)`，独立编写测试脚本。

**关键数据**：
| 字段 | 值 | 说明 |
|---|---|---|
| current_close | 55.40 | 最新收盘价 |
| cost_price | 60.78 | 持仓成本 |
| stop_loss | 56.53 | 止损价 |
| take_profit | 64.43 | 动态止盈价（原72.94） |
| profit_pct | -8.9% | 浮亏 |
| state | **S4** | 已破止损（close < stop_loss） |

**断言检查（8/8 PASS）**：

| # | 验证项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | state == 'S4' | S4 | S4 | ✅ PASS |
| 2 | action 含"止损" | 是 | "资金面偏积极，已破止损，建议止损" | ✅ PASS |
| 3 | action 不含"加仓" | 是 | 无"加仓" | ✅ PASS |
| 4 | take_profit < 72.94 | 是 | 64.43 | ✅ PASS |
| 5 | grid 含减仓位(reduce) | 是 | [reduce, reduce, reduce] | ✅ PASS |
| 6 | grid 不含补仓位(add) | 是 | 无 add | ✅ PASS |
| 7 | capital_signal 有值 | 是 | {strength:1, label:"中流入"} | ✅ PASS |
| 8 | trade_analysis.available | True | True (9笔) | ✅ PASS |

**网格明细**（S4跳过补仓位）：
| 档位 | 价格 | 仓位% | 类型 | 标签 |
|---|---|---|---|---|
| 1 | 60.78 | 30% | reduce | 回本减仓位 |
| 2 | 62.12 | 50% | reduce | 第一止盈位 |
| 3 | 64.43 | 100% | reduce | 最终止盈位 |

**核心修复确认**：close=55.40 < stop_loss=56.53 → state=S4 → 操作建议"已破止损，建议止损"，**不含"加仓"**，网格**无补仓位**。005版本的"跌破止损仍建议加仓"矛盾已修复。

---

### TC2：茅台(18) — 无持仓 — **PASS**

**关键数据**：close=1315.01, rating=推荐买入

**断言检查（4/4 PASS）**：

| # | 验证项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | has_position == False | False | False | ✅ PASS |
| 2 | grid 全 buy | 3档 | 3档全buy (1146.98/1173.50/1254.74) | ✅ PASS |
| 3 | action 有值 | 是 | "资金面偏积极，当前价高于买入区间，建议等待回调" | ✅ PASS |
| 4 | capital_signal 有值 | 是 | {strength:1, label:"中流入"} | ✅ PASS |

---

### TC3：美团(6) — position_advice 覆盖验证 — **PASS**

**执行方式**：`POST /api/stocks/6/advise`（test_client）

**关键数据**：close=90.70, take_profit=89.40, state=S1（已超目标）

**断言检查（2/2 PASS）**：

| # | 验证项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | position_advice == price_advice.action | 一致 | "资金面强支撑，已达目标，建议止盈" == action | ✅ PASS |
| 2 | 不含"加仓" | 是 | 无"加仓" | ✅ PASS |

**覆盖修复确认**：close=90.70 >= take_profit=89.40 → state=S1 → action="已达目标，建议止盈"。position_advice 被正确覆盖为 price_advice.action_suggestion，不再返回旧的"加仓"建议。

---

### TC4：汤臣倍健(7) — 有持仓 — **PASS（数据差异说明）**

**关键数据**：close=9.96, cost_price=9.56, profit_pct=+4.1%, rating=持有观望

| # | 验证项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | state in (S3, S4) | S3/S4 | **S2（浮盈中）** | ⚠️ 数据差异 |
| 2 | action 与 state 一致 | 一致 | "持有观望，注意资金面略有流出" | ✅ 逻辑正确 |

**差异分析**：任务书预期汤臣倍健处于浮亏（S3/S4），但实际数据 close=9.96 > cost=9.56，浮盈4.1%。状态机判定 S2（浮盈中：close > cost_price 且 close < take_profit=10.55）**逻辑正确**。

**判定**：状态机工作正常，action 与 state 一致（S2→ACTION_MATRIX['持有观望']['S2']="持有观望"）。预期差异源于测试用例编写时数据快照过时，非代码缺陷。

---

### TC5：数据不足场景 — **PASS**

**执行方式**：`generate_price_advice(99999, {'latest_close': None, ...})`

| # | 验证项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | available == False | False | `{'available': False, 'reason': '停牌或数据不足'}` | ✅ PASS |

---

### TC6：交易流水分析 — **PASS**

**TC6a：恒瑞医药(4)** — 14条交易记录

| # | 验证项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | available == True | True | True | ✅ PASS |
| 2 | trade_count > 0 | >0 | 14 | ✅ PASS |
| 3 | 含 rhythm | 是 | {pattern:"低频加仓", avg_interval:23.4天} | ✅ PASS |
| 4 | 含 cost_trend | 是 | {trend:"down", 61.17→53.45, "低位补仓有效摊薄成本"} | ✅ PASS |
| 5 | 含 timing | 是 | {4笔, win_rate:0%, avg:-8.1%} | ✅ PASS |
| 6 | summary 有摘要 | 是 | "低频加仓，低位补仓有效摊薄成本，历史胜率0%" | ✅ PASS |

**TC6b：中国中免(21)** — 9条交易记录
- available=True, trade_count=9
- summary="低频加仓，低位补仓有效摊薄成本，历史胜率100%"

---

### TC7：API端点验证 — **PASS**

**执行方式**：`POST /api/stocks/21/advise`（test_client）

| # | 验证项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | position_advice 被覆盖 | 是 | "资金面偏积极，已破止损，建议止损" | ✅ PASS |
| 2 | price_advice 含 grid | 是 | True | ✅ PASS |
| 3 | price_advice 含 capital_signal | 是 | True | ✅ PASS |
| 4 | price_advice 含 trade_analysis | 是 | True | ✅ PASS |

---

### TC8：一键启动 + 零代码约束 — **PASS**

| # | 验证项 | 预期 | 实际 | 结果 |
|---|---|---|---|---|
| 1 | `python app.py` 启动无报错 | 无报错 | "服务就绪，访问地址：http://127.0.0.1:5000" | ✅ PASS |
| 2 | requirements.txt 包数 | 8包无新增 | 8包（akshare/Flask/pandas/numpy/python-dateutil/pydantic/requests/openpyxl） | ✅ PASS |

---

## 三、回归测试结果

| # | 回归项 | 验证方式 | 实际结果 | 结果 |
|---|---|---|---|---|
| R1 | 评级功能正常 | POST /api/stocks/18/advise | rating=推荐买入, total_score=75.3, engine=v5 | ✅ PASS |
| R2 | 日报生成正常 | GET /api/daily-report/latest | report_date=2026-07-28, reports=27只 | ✅ PASS |
| R3 | 回测报告正常 | GET /api/price-backtest/report | HTTP 200, {report, success} | ✅ PASS |
| R4 | 自选股列表正常 | GET /api/stocks | 27只 | ✅ PASS |

---

## 四、红线复核（QA独立验证）

| # | 红线项 | QA验证方式 | 结果 |
|---|---|---|---|
| 1 | generate_advice 零修改 | 通读 advisor.py 全文（1020行），generate_advice 函数定义完整于 L869，无009修改痕迹 | ✅ PASS |
| 2 | _build_capital_factors 未修改 | 函数定义于 L785-831，逻辑与 B20 版本一致，无改动 | ✅ PASS |
| 3 | data_collector 三处 if False | grep 确认 L1645/L1684/L1717 三处 `if False` 硬禁用均存在 | ✅ PASS |
| 4 | 零代码约束 | requirements.txt 确认 8 包无新增（对比基线一致） | ✅ PASS |
| 5 | config_weights.json | 93行，权重结构与 07-27 O2-E 回滚版本一致，无改动 | ✅ PASS |
| 6 | data_contract.py | AST 解析通过，3 个类（DataQuality/StockData/AnalysisResult）完整 | ✅ PASS |

---

## 五、app.py 集成验证（4处 position_advice 覆盖）

QA 独立读取 app.py 确认 4 处覆盖逻辑的代码位置和缩进正确性：

| # | 端点 | 代码行 | 覆盖逻辑 |
|---|---|---|---|
| 1 | `/analyze` | L763-767 | `if result.get('price_advice', {}).get('action_suggestion'): result['position_advice'] = ...` |
| 2 | `report-latest` 自动触发 | L823-827 | `if advice.get('price_advice', {}).get('action_suggestion'): advice['position_advice'] = ...` |
| 3 | `report-latest` 实时计算 | L975-977 | `if result.get('price_advice', {}).get('action_suggestion'): result['position_advice'] = ...` |
| 4 | `/advise` | L991-995 | `if result.get('price_advice', {}).get('action_suggestion'): result['position_advice'] = ...` |

4处逻辑一致，缩进正确，仅在 `price_advice.action_suggestion` 有值时覆盖。

---

## 六、前端变更验证

| 验证项 | 确认 |
|---|---|
| CSS 类定义 | pa-grid-table / pa-buy / pa-reduce / pa-add / pa-warning 已定义（L851-856） |
| JS 状态映射 | `_paStateCls = {'S1':'pa-up','S2':'pa-up-light','S3':'pa-warning','S4':'pa-down'}`（L4134） |
| JS 网格映射 | `_paGridCls = {'buy':'pa-buy','reduce':'pa-reduce','add':'pa-add'}`（L4135） |
| 网格表格渲染 | `<table class="price-advice-table pa-grid-table">`（L4191） |

---

## 七、验收结论

### 总体判定：**PASS** ✅

**核心修复验证**：
1. **S4已破止损禁止加仓** — 中国中免 close=55.40 < stop_loss=56.53 → state=S4 → 建议"止损"，网格无补仓位。**005版本矛盾已修复。**
2. **动态止盈价** — 原固定72.94 → 动态64.43（双约束公式生效：max(最低止盈, min(固定止盈, 阻力位))）。
3. **position_advice 覆盖** — 美团 close=90.70 >= tp=89.40 → S1 → 覆盖为"建议止盈"，不再返回"加仓"。
4. **资金面信号** — 7档修饰词正确应用（中流入 strength=1 前置修饰）。
5. **交易流水分析** — 三维度（rhythm/cost_trend/timing）+ summary 摘要输出正确。
6. **向后兼容** — 005字段全部保留，回归测试无异常。

**备注**：
- TC4 汤臣倍健预期 S3/S4 与实际 S2 存在数据差异，状态机逻辑本身正确（close > cost → S2 浮盈中），属测试数据快照过时，非代码缺陷。
- 无新增 pip 依赖，零代码约束满足。

---

*QA 验收完毕，建议监理批准关闭。*
