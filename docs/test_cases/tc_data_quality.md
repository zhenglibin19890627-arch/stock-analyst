# 测试用例：数据质量核验（资金面暴跌 + 经典引擎0分）

| 项目 | 内容 |
|---|---|
| **用例编号** | TC-DQ-001 |
| **关联任务** | QA-TASK-20260722 任务A（P0） |
| **关联评审** | REVIEW-CAPITAL-33PCT-20260722（架构师根因分析） |
| **需求基线** | `docs/requirements_v1.1.md` §2.1（数据采集）/ §3.2（可靠性） |
| **触发背景** | 07-22 实测：capital 维度暴跌3.8%、经典引擎10只全0分、data_quality.capital 恒定33% |
| **验收标准** | ① capital采集成功率≥95%（26只中≥25只success）；② HK3690 fundamental=success连续3日；③ 经典引擎0分股票修复后有真实评分；④ 四维数据完整度不得全空；⑤ 无估算值污染 |
| **设计方** | QA（质量保障） |
| **设计日期** | 2026-07-22 |
| **状态** | 测试用例预编制（待架构师修复后执行） |

---

## 一、问题全貌与根因速览

### 1.1 三层问题拆分

架构师评审报告（REVIEW-CAPITAL-33PCT）将07-22暴露的数据质量问题拆为三层，QA 需分别设计核验用例：

| 层级 | 问题 | 根因 | 架构师修复方案 |
|---|---|---|---|
| **L1：采集层** | capital 维度 `data_status` 暴跌至3.8% | 同花顺源当日异常/限流（待排查） | 待架构师定因 |
| **L2：结构层** | `data_quality.capital` 恒定33% | north_net_buy 和 margin_balance_chg **从未有任何采集代码写入**（结构性缺失） | 方案A：补齐北向+融资融券数据源 |
| **L3：引擎层** | 经典引擎10只股票全0分 | 10只股票不在v5白名单→走经典引擎，经典引擎有数据仍输出0分（降级Bug） | 待架构师定因 |

### 1.2 代码锚点（核验定位用）

| 锚点 | 位置 | 说明 |
|---|---|---|
| capital_total=3 | [data_contract.py L230](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_contract.py) | 3个字段：main_net_inflow, north_net_buy, margin_balance_chg |
| north_net_buy 读取 | [data_adapter.py L326](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_adapter.py) | `latest_cap.get('north_holding_change')` → DB 字段从未被写入 → 永远None |
| margin_balance_chg 计算 | [data_adapter.py L329-333](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_adapter.py) | 需 cap_rows≥2 且 margin_balance 非空 → DB 字段从未被写入 → 永远None |
| v5 白名单 | [engine_switcher.py L45-48](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\engine_switcher.py) | 仅12只在白名单：600276/300146/000333/002352/300750/HK3690/600519/601888/300124/688017/688981/002458 |
| 三处 if False 估算源 | data_collector.py L1235/L1274/L1307 | B1红线禁用，不可恢复 |

---

## 二、L1 采集层核验：capital 维度 data_status 暴跌

> **目标**：验证修复后 capital 维度 `data_status` 成功率恢复至≥95%。

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-DQ-L1-01 | 执行产品经理提供的 capital 成功率 SQL（按当日） | success_rate ≥ 95%（26只中≥25只 success） | P0 |
| TC-DQ-L1-02 | 对比全部历史成功率 | 历史 success_rate 应 > 41.9%（07-22暴跌前基线） | P0 |
| TC-DQ-L1-03 | 检查同花顺批量源是否正常调用 | `fetch_capital_flow_batch()` 返回有效数据，日志无超时/限流 | P0 |
| TC-DQ-L1-04 | 检查防覆盖逻辑（[L1219-L1229](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_collector.py)）是否误判跳过 | 前置校验层不应在同花顺数据不完整时跳过逐只补齐 | P1 |
| TC-DQ-L1-05 | 验证同花顺源恢复后的稳定性 | 连续3交易日 success_rate ≥ 95% | P0 |

### 2.1 核验 SQL（产品经理提供）

```sql
-- capital 成功率（当日）
SELECT dimension, COUNT(*) as total,
  SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as success,
  ROUND(SUM(CASE WHEN status='success' THEN 1 ELSE 0 END)*100.0/COUNT(*),1) as rate
FROM data_status WHERE date(fetched_at)=date('now','localtime')
GROUP BY dimension;
```

---

## 三、L2 结构层核验：data_quality.capital 恒定33%

> **目标**：验证修复后 `data_quality.capital` 不再恒定33%（补齐 north_net_buy / margin_balance_chg 后应提升至≥66%）。

### 3.1 north_net_buy 字段写入验证

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-DQ-L2-01 | 修复后查询 `SELECT north_holding_change FROM raw_capital_flow WHERE stock_id=? AND trade_date=?`（A股白名单标的） | **非None**，有真实北向资金持股变化值 | P0 |
| TC-DQ-L2-02 | 港股（HK3690）查询同上 | 非None（港股通持股变化），或有明确的结构性缺失标注 | P1 |
| TC-DQ-L2-03 | 验证 north_holding_change 数据来源 | 来自 `ak.stock_hsgt_*` 系列真实接口，非估算值 | P0 |
| TC-DQ-L2-04 | 验证 data_adapter 读取链路 | [L326](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_adapter.py) `north_net_buy = latest_cap.get('north_holding_change')` 能读到非None值 | P0 |

### 3.2 margin_balance_chg 字段写入验证

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-DQ-L2-05 | 修复后查询 `SELECT margin_balance FROM raw_capital_flow WHERE stock_id=? AND trade_date=?` | **非None**，有真实融资融券余额值 | P0 |
| TC-DQ-L2-06 | 验证 margin_balance 数据来源 | 来自 `ak.stock_margin_detail_*` 系列真实接口，非估算值 | P0 |
| TC-DQ-L2-07 | 验证 margin_balance_chg 差分计算（[data_adapter.py L329-333](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_adapter.py)） | cap_rows≥2 时，chg = 最新 - 上期，值正确 | P1 |
| TC-DQ-L2-08 | 仅有1条 cap_row（无历史数据）时 | margin_balance_chg=None（合理降级，不报错） | P2 |

### 3.3 data_quality.capital 综合验证

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-DQ-L2-09 | 修复后取一只白名单 A 股，调 `analyze_from_db()` 查看 `data_quality.capital` | **≥0.67**（3个字段中≥2个有值，不再恒定0.33） | P0 |
| TC-DQ-L2-10 | 3个字段全部有值时 | `data_quality.capital = 1.0`（100%） | P1 |
| TC-DQ-L2-11 | 仅 main_net_inflow 有值（修复前状态模拟） | `data_quality.capital = 0.33`，确认33%根因=2个字段缺失 | P1 |

---

## 四、L3 引擎层核验：经典引擎10只股票全0分

> **目标**：验证修复后经典引擎在有数据时输出真实评分（非0分）。

### 4.1 0分股票清单（07-22实测）

| 用例ID | 股票代码 | 股票名称 | 修复前得分 | 修复前四维 | 修复后预期 |
|---|---|---|---|---|---|
| TC-DQ-L3-01 | HK9988 | 阿里巴巴 | 0.0 | 全空 | >0，四维有值 |
| TC-DQ-L3-02 | 000858 | 五粮液 | 0.0 | 全空 | >0，四维有值 |
| TC-DQ-L3-03 | HK1810 | 小米 | 0.0 | 全空 | >0，四维有值 |
| TC-DQ-L3-04 | 002714 | 牧原 | 0.0 | 全空 | >0，四维有值 |
| TC-DQ-L3-05 | 002415 | 海康威视 | 0.0 | 全空 | >0，四维有值 |
| TC-DQ-L3-06 | 000977 | 浪潮信息 | 0.0 | 全空 | >0，四维有值 |
| TC-DQ-L3-07 | 688041 | 海光信息 | 0.0 | 全空 | >0，四维有值 |
| TC-DQ-L3-08 | 688795 | 摩尔线程 | 0.0 | 全空 | >0，四维有值 |
| TC-DQ-L3-09 | 688802 | 沐曦股份 | 0.0 | 全空 | >0，四维有值 |
| TC-DQ-L3-10 | 601012 | 隆基绿能 | 0.0 | 全空 | >0，四维有值 |

### 4.2 引擎路由核验

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-DQ-L3-11 | 确认10只0分股票是否在 v5 白名单 | **均不在白名单**（白名单仅12只），走经典引擎 | P0 |
| TC-DQ-L3-12 | 查询这10只的 data_status | kline/sentamental/fundamental 大部分 success，仅 capital failed | P0 |
| TC-DQ-L3-13 | **核心矛盾验证**：经典引擎 `analyze_stock()` 在 kline=success 时输出 | 修复前=0分（Bug），修复后=有真实评分 | P0 |
| TC-DQ-L3-14 | 修复后 `SELECT * FROM analysis_results WHERE total_score=0` | 0分记录数为0（或仅限真正无数据的股票） | P0 |

### 4.3 四维数据完整度验证

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-DQ-L3-15 | 修复后日报查看这10只股票 | 四维评分不为全空，至少有 kline+fundamental 两维有值 | P0 |
| TC-DQ-L3-16 | 修复后日报查看 v5 白名单12只股票 | 四维评分正常，data_quality 各维度合理 | P0 |
| TC-DQ-L3-17 | 全量26只股票日报四维完整度统计 | 无"四维全空"的股票 | P0 |

---

## 五、HK3690 fundamental 稳定性核验

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-DQ-HK-01 | 查询 HK3690 今日 data_status fundamental | status = success | P0 |
| TC-DQ-HK-02 | 连续3交易日查询（07-22/23/24） | 每日均 success | P0 |
| TC-DQ-HK-03 | 验证9字段至少6个有值 | pe_ttm/pb/roe/gross_margin/revenue_yoy/net_profit_yoy/debt_to_asset/current_ratio 至少6个非None | P0 |
| TC-DQ-HK-04 | ocf_to_profit 字段 | 可为None（已知结构性缺失，降级处理），不计入失败 | P2 |

---

## 六、无估算值污染核验（红线③）

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-DQ-RED-01 | 检查 data_collector.py L1235 | `if False` 估算源保持禁用（未恢复） | P0 |
| TC-DQ-RED-02 | 检查 data_collector.py L1274 | `if False` 估算源保持禁用 | P0 |
| TC-DQ-RED-03 | 检查 data_collector.py L1307 | `if False` 估算源保持禁用 | P0 |
| TC-DQ-RED-04 | 修复后 north_holding_change 写入值 | 来自 akshare 真实接口，非估算值 | P0 |
| TC-DQ-RED-05 | 修复后 margin_balance 写入值 | 来自 akshare 真实接口，非估算值 | P0 |

---

## 七、前置校验层修正核验（架构师方案C）

> 架构师建议将 [L1219-L1229](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_collector.py) 从"行级存在性"改为"字段级完整性"。

| 用例ID | 步骤 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-DQ-PRE-01 | 同花顺写入 main_net_inflow 后，东财逐只是否被跳过 | 修复前=跳过（分层缺失），修复后=仅3字段全有值时跳过 | P1 |
| TC-DQ-PRE-02 | 修复后东财逐只补齐 super_large_net 等分层字段 | 经典引擎资金面评分深度改善 | P2 |
| TC-DQ-PRE-03 | 前置校验不影响已有 main_net_inflow 值 | 已写入值不被覆盖（防覆盖红线 L1091/L1225 未动） | P0 |

---

## 八、回归测试

| 用例ID | 场景 | 预期结果 | 优先级 |
|---|---|---|---|
| TC-DQ-RG-01 | 26只全量 batch-analyze | 采集成功率≥95%，无0分股票 | P0 |
| TC-DQ-RG-02 | v5 白名单12只评分一致性 | score_diff=0（同日两次分析一致） | P1 |
| TC-DQ-RG-03 | 经典引擎14只评分正常 | 有数据即有评分，不出现0分 | P0 |
| TC-DQ-RG-04 | 12只白名单 + 贵州茅台(600519) + 美的集团(000333) 回归标杆 | 数据一致性，历史不一致问题彻底消除 | P1 |

---

## 九、执行说明

- **执行前提**：架构师修复方案经监理批准并交付后执行
- **P0 用例**：全部必须通过，任一失败即驳回
- **执行顺序**：L1采集层(二) → L2结构层(三) → L3引擎层(四) → HK3690(五) → 红线(六) → 前置校验(七) → 回归(八)
- **数据快照**：执行前先对 stock_analyst.db 做备份，确保可回滚

**设计人**：QA | **设计日期**：2026-07-22
