# DEV-SELFTEST-019P：基本面数据完善（毛利率补全 + 来源标注 + 趋势分析 + 港股占位行修复）自验报告

> **开发**：Dev Agent | **日期**：2026-08-05 | **任务书**：019P v2 定稿（M-1~M-10 已并入）| **状态**：自验通过（75/75 断言 + 343 回归 + 真实环境联调），待 QA 独立验收

---

## 一、改动文件清单（范围红线：4 文件）

| 文件 | 任务 | 改动区域（函数名区域，M-9） | 内容 |
|------|------|------------------------------|------|
| `modules/data_collector.py` | 1/3/4/5 | `_fetch_a_fundamental_sina` 系 + `fetch_a_fundamental` + `fetch_hk_fundamental` + 新增 019P 辅助函数（基本面区 L537-1330） | abstract 主源 + P1/P2/P3 + data_source + TTL 完整性检查 + 港股占位行修复 |
| `modules/advisor.py` | 2 | `_build_fundamental_factors`（L1068 区域）+ `_describe_dimension`（L212-221）+ `_detect_risks`（L404-406 附近）+ `_pick_top_factors`（L449-458）+ 新增 `_build_fund_trend` | 趋势因子 + 四输出位置（B24 红线外） |
| `templates/index.html` | 2/3 | L2469-2495 基本面展示区 + L5170-5241 因子卡 | 动态来源标注 + fund_trend 因子卡 |
| `database/db_manager.py` | 3 | `_migrate_columns`（L965 后追加一行） | `raw_fundamental.data_source TEXT DEFAULT NULL` |

**范围外文件零改动**：app.py / scoring_engine.py / config_weights.json / data_contract.py / data_adapter.py / analysis_engine.py / alert_engine.py / config.py / requirements.txt / 资金面区（019N 串行隔离，`_safe_num` 等函数未动）。

---

## 二、自验方法

| 项 | 说明 |
|---|---|
| 隔离自验脚本 | `.dev_019P_work/selftest_019P.py`（临时 SQLite + mock 网络层，**不联网不碰生产库**，019N 同型） |
| 回归 | `python -m py_compile` 4 文件 + `python -m pytest tests/`（343 用例） |
| 真实环境联调 | `.dev_019P_work/realrun_019P.py`：**生产库 COPY** + 真实网络（akshare abstract / EM 港股 / 腾讯 PE/PB），零写生产库 |
| 前端校验 | 提取 index.html `<script>` 块 `node --check`（JS 语法 OK） |
| 生产库迁移 | 备份 `backups/db_backup_20260805_235819_019P_migration.db` → `init_database()`（app 启动同款）→ 幂等两次验证，业务行数零变化 |

**自验结果：75 PASS / 0 FAIL**；pytest 343 全绿；真实联调全过。

---

## 三、任务验收自验明细（对照任务书 §四）

### 1. 代码级核查（函数级 grep，M-8①）

| # | 断言 | 结果 |
|---|------|------|
| 1.1 | abstract 主源：`_fetch_a_fundamental_sina` 改调 `ak.stock_financial_abstract` | ✅ |
| 1.2 | `data_source` 列迁移存在（`_migrate_columns` 019K 先例同型） | ✅ 见 §五 |
| 1.3 | P1 ocf 保留（REPLACE 不破坏既有 ocf） | ✅ S2 |
| 1.4 | P2 降级（abstract 异常/空 → analysis_indicator，message 标注降级） | ✅ S3 |
| 1.5 | P3 超时保护（自建 daemon 闭包 `_call_ak_with_timeout`，禁裸调） | ✅ S4 + 代码级 |
| 1.6 | 港股占位行修复（清理 + PE/PB 合并到真实行） | ✅ S6 |
| 1.7 | TTL 完整性检查（任务 5） | ✅ S5 |
| 1.8 | 趋势四输出位置 | ✅ S8 |

### 2. 编译验证（§四②）

```
python -m py_compile modules/data_collector.py modules/advisor.py database/db_manager.py → 无错误
```

### 3. 功能验证（QA mock 等价，§四③）

**S1 abstract 结构适配（行=指标、同名去重、最新在前列序）**：
- 8 期写入（否决全历史 R-8）✅；`report_date` 格式 `YYYY-MM-DD` ✅；最新在前 ✅
- `gross_margin` 8 期全非空（核心修复）✅；ROE=常用指标组口径 4.15 ✅
- data_source='sina_abstract' 全行标注 ✅；PE/PB 合并到最新行 ✅

**S1b 同名去重（R-1）**：'毛利率' 两行（常用 86.60 / 盈利 99.99）→ 取常用指标组 86.60 ✅；ROE 同型 ✅

**S2 P1 ocf 保留**：abstract 该期 ocf=NaN + DB 既有 0.3448 → 保留 0.3448 ✅；abstract 有值期正常写入 ✅

**S3 P2 降级**：abstract 异常 → 现接口写 2 期 + `data_source='sina_analysis_indicator'` + message `'新浪指标(analysis_indicator降级)+腾讯估值: 基本面数据采集成功'` ✅

**S4 P3 超时**：abstract 挂起 3s（超时 1s 注入）→ TimeoutError 抛出、返回 <2s 不挂死 → 端到端降级现接口 ✅

**S8 趋势**（R-5 环比陷阱 / R-9 兜底 / 四输出位置）：
- Q1(4.15) 与年报(14.26) 混排 → **不输出 ROE 环比**（'ROE较上期' 不在明细）✅；ROE 同比正确（2.80→4.15 改善）✅
- 环比指标（毛利率/净利率/负债率/流动/速动）较上期 ✅；负债率下降=改善 ✅
- 增速指标"加快/放缓"表述 ✅；|Δ|<1pct → 平稳 ✅；全平稳 → direction=flat ✅
- 无数据/单期 → '历史数据不足，暂无趋势判断'（不崩溃）✅
- **四输出位置**：① `_describe_dimension` highlights 含 '基本面趋势:'（恶化不入 highlights）✅；② `_detect_risks` 恶化 → '基本面趋势恶化：…' ✅；③ 前端因子卡 `_factorPriority.fundamental` 首位 `fund_trend` + 标签 + tooltip（代码级 grep + node --check）✅；④ `_pick_top_factors` 基本面优先级首位 ✅

### 4. TTL 三态（§四④，M-8）

| 态 | 场景 | 断言 | 结果 |
|----|------|------|------|
| 回补 | 最新期 gross_margin NULL（TTL 内） | 不跳过 + message '财报补全(毛利率缺失触发)' + 回补后收敛（二次同日跳过） | ✅ S5-T1/T1b |
| 回归 | 最新期有值（TTL 内 + PE/PB 24h 内） | '同日跳过(财报80天TTL内+PE/PB 24h内)' | ✅ S5-T2 |
| 80 天外 | 最新期 100 天前 | 正常采集（回补标记不误触发） | ✅ S5-T3 |

### 5. 港股修复断言（§四⑤）

**S6**（生产库同型场景：占位行=今日 PE/PB + 历史占位 + 遗留空日期行）：
- 全指标 NULL 占位行清理 ✅（0 残留；遗留 `''` 空日期行按 spec 晚于判断保留，与生产库 stock_id=6 工件同型）
- 最新真实财报行（2025-12-31）可读、含全部指标 ✅
- PE/PB 合并到最新真实财报行（-22.14/3.74）✅
- data_source='em_hk' ✅；message '港股EM财报+腾讯估值: …' ✅

**S7 收敛性**：占位行遮蔽 → 回补一次 → 清理 → 二次调用'同日跳过'（不每日触发回补，永不收敛已消除）✅

### 6. 来源标注断言（§四⑥）

- `data_source` 迁移幂等（两次执行不报错、列不重复）✅ S9
- data_status message 前缀三通道（abstract / 降级 / 港股 / PE/PB 仅更新）✅ S1/S3/S6/代码级
- 前端动态表头（'来源：新浪关键指标(abstract)…' + '；估值：腾讯行情'）+ 行级 `<sup>` 混合来源标注 ✅ 代码级 + node --check

### 7. 真实环境联调（生产库 COPY + 真实网络）

**A股 600276 恒瑞医药**（原 gross_margin 全 NULL）：
```
report_date=2026-03-31 roe=4.15 gross_margin=86.598607 net_margin=28.020101
debt_ratio=10.024391 revenue_growth=12.975362 profit_growth=21.782316
ocf_to_net_profit=0.344589（P1 保留：abstract 该期 NaN）pe=43.77 pb=5.7
data_source='sina_abstract'；message='新浪abstract财报+腾讯估值: 基本面数据采集成功'
共 8 期写入，毛利率 8 期全有值
```

**港股 HK3690 美团-W**（原最新行为 PE/PB 占位行遮蔽）：
```
message='港股EM财报+腾讯估值: 财报补全(毛利率缺失触发)'（占位行触发回补）
占位行清理 1 条 → 0 残留；PE/PB(-22.22/2.75) 合并到最新真实行 2025-12-31
```

### 8. 回归与零改动确认（§四⑦⑧）

- `python -m pytest tests/` → **343 passed**（含 019N TestSafeNum 等全部既有用例）
- `python -m py_compile` 全过（4 修改文件 + 全模块抽查）
- index.html JS 语法 `node --check` OK
- 本次会话仅编辑上述 4 文件；资金面区（019N）函数体未动（`_safe_num`/`_fetch_capital_flow_ths_batch`/`_em_batch_collect`/`fetch_capital_flow` grep 在位，行号随 019P 区域插入漂移属预期）

---

## 四、红线确认（§五）

| 红线 | 落实 |
|------|------|
| B24（不碰 generate_advice） | ✅ 趋势接入点均在外部既有函数内；`generate_advice` 主体零改动 |
| 范围红线（4 文件 + 区域） | ✅ 基本面区/因子区/前端标注展示/迁移一行；资金面区零改动 |
| 超时红线（P3） | ✅ 自建 `_call_ak_with_timeout`（019I 模式同型，不 import THS 闭包）；abstract 与降级路径均包装 |
| ocf 保留红线（P1） | ✅ REPLACE 前查既有 ocf，NaN 时保留原值（真实联调实证 0.344589 保留） |
| 评分纯净红线 | ✅ scoring_engine / config_weights / data_contract 零改动；fund_trend 仅展示 |
| 零代码约束 | ✅ 无新 pip 依赖（akshare 1.18.53 内置接口）；config.py 不碰 |
| 增量红线（011） | ✅ TTL 门控语义不变（完整数据 80 天跳过）；完整性检查在门控内附加 |
| 真实数据源红线 | ✅ abstract（新浪）/ EM（港股）/ 腾讯（PE/PB）均为真实财务数据；无估算公式 |
| ROE 口径迁移 | ✅ 3.59→4.15（现接口摊薄口径→abstract 常用指标组口径），实测值域合理，QA 建议按 0<v<100 断言 |

---

## 五、生产库迁移执行记录

| 项 | 记录 |
|---|---|
| 备份 | `backups/db_backup_20260805_235819_019P_migration.db` |
| 迁移 | `init_database()`（app 启动同款）：`raw_fundamental.data_source` 列添加成功 |
| 业务数据 | 行数快照（stocks/raw_fundamental/raw_capital_flow/data_status/analysis_results）零变化；gross_margin 非空数未动（业务回补由新代码采集流程自然执行，不手工写库） |
| 幂等 | 二次执行无错误 |
| 部署说明 | 当前 5000 端口运行中的 app 为旧代码；**重启（start.bat）后**新代码生效，存量 21 只 A 股毛利率经"完整性回补 + abstract 重采"自动补全，港股占位行自动清理 |

---

## 六、已知边界与登记事项

1. **港股备用源登记**（A-4）：akshare 港股财务接口均 EM 源，无独立非 EM 源——维持现状登记，与 019K 港股资金面缺口同型处置。
2. **官方报告源登记**（A-5）：`stock_financial_report_sina` 为后续候选（abstract 已覆盖现写全部字段，本批次不纳入）；后续可用利润表对 abstract 毛利率 cross-check。
3. **混合来源比较**（A-2⑤）：趋势计算接受存量旧期（analysis_indicator）+ 新期（abstract）混排，同新浪域可比，已文档化。
4. **技术债登记**（发现 5）：THS 函数内闭包 `_call_with_timeout` 与 019P 自建 `_call_ak_with_timeout` 同型，公共化提取留待后续批次。
5. **遗留空日期行**：港股 stock_id=6 的 `report_date=''` 全 NULL 行属历史工件，按 spec 晚于判断语义保留（不影响展示/评分：DESC 排序恒在末尾，API LIMIT 5 不读取）。
6. **abstract 不挂 @retry**：主源失败即降级（P2），避免批量场景超时重试累积（最坏 3×30s 超批次上限）；降级层保留 @retry（原语义）。
7. **趋势不进评分**：监理若坚持趋势参与评分需另立批次（涉及 data_contract/scoring_engine/权重）。

---

## 七、自验结论

**✅ 自验通过**：75/75 断言 + 343 回归全绿 + 真实环境联调（生产库 COPY）验证 A 股毛利率补全（86.60）、ROE 口径迁移（4.15）、ocf P1 保留（0.344589）、港股占位行清理收敛、来源标注三通道、趋势四输出位置全部落地；红线（B24/范围/超时/ocf/评分纯净/零代码/TTL/真实源）全部满足。待 QA 独立验收。
