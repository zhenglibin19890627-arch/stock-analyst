# P0 双任务验收报告

| 项目 | 内容 |
|---|---|
| **报告编号** | P0-ACCEPT-20260721 |
| **验收日期** | 2026-07-21 |
| **验收方** | 开发执行方（自验） |
| **关联任务书** | DEV-TASKS-20260721 任务卡1 + 任务卡2 |
| **代码变更** | `modules/data_collector.py`(+177行)、`app.py`(+19行) |

---

## 一、P0-CAPITAL-001 资金面数据完整度提升

### 1.1 验收标准逐项核验

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| ① | 12只白名单资金面T-0覆盖率≥90% | **✅ 通过(91.7%+)** | 同花顺批量预取 11/11 A股成功（100%），港股走东财secid路径；综合≥11/12=91.7% |
| ② | 批量采集不触发限流 | **✅ 通过** | 同花顺1次调用(5198只/5.56s)替代东财逐只36次请求，从根因消除限流 |
| ③ | data_status capital成功率≥95% | **✅ 通过(当日)** | 11/11 A股 capital=success；需连续观察3交易日确认稳定性 |
| ④ | 无估算值污染（B1禁用规则保持） | **✅ 通过** | 三处`if False`估算源(L1235/L1274/L1307)未动；同花顺为真实数据源 |

### 1.2 实现要点

- **同花顺批量源** `fetch_capital_flow_batch()`：调用 `ak.stock_fund_flow_individual()` 全市场1次请求，1小时内存缓存（`_THS_CAPITAL_CACHE`），int64代码坑点处理（`str(int(x)).zfill(6)`），中文金额解析（`_parse_cn_amount`：亿/万→元→÷1e4转万元）。
- **前置校验层**（fetch_capital_flow 开头）：若当日 `raw_capital_flow` 已有真实数据则跳过逐只采集。不修改 L1091防覆盖 / L1225 early return（红线遵守）。
- **app.py batch-analyze 集成**：循环前提取A股symbol批量预取，失败不阻断后续逐只采集。

### 1.3 数据库实证（2026-07-21 20:54）

```
数据库当日A股资金面记录: 11/11 条
  000333: main_net=31900.0万(3.19亿), pct=9.84%
  002352: main_net=-1911.43万, pct=-1.93%
  002458: main_net=2118.61万, pct=6.73%
  300124: main_net=21000.0万(2.1亿), pct=11.06%
  300146: main_net=1802.15万, pct=3.58%
前置校验跳过验证: fetch_capital_flow('600276') → "今日已有真实资金流数据（1条），跳过采集" ✓
```

---

## 二、P0-HK-FUND-002 港股基本面数据源补齐

### 2.1 验收标准逐项核验

| # | 验收标准 | 结果 | 证据 |
|---|---|---|---|
| ① | HK3690 fundamental成功率>0%且稳定3交易日 | **🟡 当日通过，待观察** | 当日 success；根因已修复（列名漂移），接口稳定可用，需连续观察3交易日 |
| ② | 9个基本面字段至少6个有真实值 | **✅ 通过(8/9)** | data_adapter读取：pe_ttm/pb/roe/gross_margin/revenue_yoy/net_profit_yoy/debt_to_asset/current_ratio 共8个有值 |
| ③ | data_status hk_stock/fundamental不再NEVER_SUCCESS | **✅ 通过** | data_status=success，消息="港股基本面数据采集成功" |

### 2.2 根因与修复

- **真正根因**：akshare 升级至 1.18.53 后，`stock_financial_hk_analysis_indicator_em` 返回列名由**中文漂移为英文**（`净资产收益率(%)`→`ROE_AVG`），现有 `safe_get` 用中文key全部取空 → saved_count=0 → failed。**接口本身可用**，非数据源失效。
- **字段映射修复**：`safe_get` 兼容英中双key（英文优先，中文兜底），report_date取`REPORT_DATE`字段。
- **PE/PB合并修复**：港股PE/PB原先INSERT独立行（report_date=today），导致data_adapter只读到PE/PB而丢财报指标。改为UPDATE合并到最新财报行（对齐A股逻辑）。

### 2.3 字段映射对照（v5契约9字段）

| v5契约字段 | 数据源 | DB字段 | akshare英文列名 | 状态 |
|---|---|---|---|---|
| pe_ttm | 腾讯qt.gtimg.cn | pe_ratio | (实时行情[39]) | ✓ |
| pb | 腾讯qt.gtimg.cn | pb_ratio | (实时行情[43]) | ✓ |
| roe | 东财港股财务 | roe | ROE_AVG | ✓ |
| gross_margin | 东财港股财务 | gross_margin | GROSS_PROFIT_RATIO | ✓ |
| revenue_yoy | 东财港股财务 | revenue_growth | OPERATE_INCOME_YOY | ✓ |
| net_profit_yoy | 东财港股财务 | profit_growth | HOLDER_PROFIT_YOY | ✓ |
| debt_to_asset | 东财港股财务 | debt_ratio | DEBT_ASSET_RATIO | ✓ |
| current_ratio | 东财港股财务 | current_ratio | CURRENT_RATIO | ✓ |
| ocf_to_profit | (指标接口无直接字段) | ocf_to_net_profit | — | ✗ 留空降级 |

### 2.4 data_adapter实证（2026-07-21 20:57）

```
v5契约9字段非空: 8/9
  ✓ pe_ttm = -20.35
  ✓ pb = 2.55
  ✓ roe = -14.429641537815
  ✓ gross_margin = 30.425430179275
  ✓ revenue_yoy = 8.0757850427
  ✓ net_profit_yoy = -165.2243925722
  ✗ ocf_to_profit = None
  ✓ debt_to_asset = 56.4763972979
  ✓ current_ratio = 1.821710666589
StockData构建: fundamental数据质量=0.89
```

---

## 三、零代码约束确认

| 约束项 | 状态 |
|---|---|
| `pip install -r requirements.txt` 一键安装不变 | **✅** 未引入新依赖（仅用akshare已有接口） |
| `python app.py` 一键启动不变 | **✅** 启动方式无变化 |
| 数据源切换对用户透明 | **✅** 同花顺/港股财务切换均在内部完成 |

---

## 四、红线遵守确认

| 红线 | 状态 |
|---|---|
| 不修改fetch_capital_flow函数签名 | **✅** 签名未变 |
| 不改动L1091防覆盖/L1225 early return | **✅** 仅在前置插入校验层，未动既有逻辑 |
| 不恢复三处`if False`估算源 | **✅** L1235/L1274/L1307 保持禁用 |
| 不写入任何估算值 | **✅** 同花顺/港股财务均为真实数据 |
| 不修改v5契约StockData | **✅** 契约未变 |

---

## 五、后续观察项

1. **P0-CAPITAL-001 ③**：capital成功率≥95%需连续观察3个交易日（07-22/23/24）确认同花顺源稳定性。
2. **P0-HK-FUND-002 ①**：HK3690 fundamental成功率需连续观察3个交易日确认。
3. **ocf_to_profit字段**：当前留空降级（指标接口无直接对应）。若后续M8回测需要，可从`stock_financial_hk_report_em`现金流量表pivot补齐（B2候选源已实证可用）。
4. **港股资金面**：HK3690走东财secid路径，若东财域名被封可能不稳定（既有问题，非本次改动引入）。

---

**自验结论**：P0-CAPITAL-001 与 P0-HK-FUND-002 编码完成，验收标准逐项达标（②③当日通过，①稳定性项待3交易日观察），零代码约束与红线均遵守。提请监理方验收。

**编制人**：开发执行方 | **编制时间**：2026-07-21
