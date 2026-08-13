# 开发自测报告：019T 评分体系校准

| 项 | 内容 |
|---|---|
| 批次 | 019T（IC 体检 + 资金面缺位修复 + 回测基准化） |
| 角色 | 开发 |
| 日期 | 2026-08-10 |
| 任务书 | `docs/tasks/dev_tasks_20260809_019T_scoring_calibration.md` |
| 评审裁定 | `docs/reports/review_019T_scoring_calibration_20260809.md`（第 7 节约束清单 21 条） |
| 状态 | **开发完成，交 QA 独立验收**；2 项待 PM 裁定事项见第八节 |

---

## 一、结论（先结论）

1. **T1 完成**：IC 分析报告 `docs/reports/ic_analysis_019T_20260810.md` 已产出（T+1/T+5、A股逐日横截面+港股池化、资金面填充行敏感性并列披露、ICIR 含 n/std/日序列、R-1 合规声明）；全程只读零写库。
2. **T2 完成**：遗留项⑨修复按方案 b+开放项 A 落地；**函数级零回归断言（约束 10）全绿**（main 实测值子项逐位一致 26/26）；端到端黄金断言归因分解 29/29 吻合，**但"23 只 A 股总分不变"预期经实测不成立**——根因见第八节，已交 PM 裁定（不自行裁决）。
3. **T3 完成**：HK 指数采集 EM→sina 降级（现网实测通过）；定时刷新挂载；`_ensure_columns` 幂等追加 7 列；alpha 判定复用 JUDGEMENT_MATRIX；**A股存量 719 行补算完成**（含模拟行，先备份）；HK 101 行待 HSI 入库后补（同款脚本）。
4. **全量回归**：`pytest 355 passed`（基线 343 + 新增 12）；ruff 对全部改动文件 0 错误（仓库基线 35 处既有错误未触碰）；mypy 改动代码 0 新增错误（基线 51 处既有错误）。
5. **红线合规**：未触碰 advisor.generate_advice（B24）；自测全程未点击前端（B11）；写生产库仅限补算脚本+幂等迁移（先备份）；写库时间 2026-08-10 00:19~00:23（避开 16:10/16:30 与周日 20:00 窗口，见第七节）。

---

## 二、T1 维度 IC 有效性体检（研究型）

### 2.1 交付
报告：`docs/reports/ic_analysis_019T_20260810.md`（136 行，含全部数据表）。

### 2.2 关键结果摘要

| 维度 | A股 T+1 IC均值 (n=18) | ICIR | A股 T+5 IC均值 (n=14) | ICIR |
|---|---:|---:|---:|---:|
| 基本面 | +0.0407 | 0.1608 | **+0.1716** | **0.5087** |
| 技术面 | -0.0960 | -0.2571 | -0.0255 | -0.0582 |
| 消息面 | +0.0034 | 0.0155 | -0.2207 | -0.9630 |
| 资金面(全样本) | +0.0249 | 0.0875 | -0.0720 | -0.1987 |
| 资金面(剔除填充行) | **+0.1150** (n=14) | **0.4748** | -0.0844 (n=11) | -0.2416 |
| 综合分 | -0.0685 | -0.1892 | -0.0890 | -0.2205 |

- **资金面填充行敏感性（P-1 硬性要求）**：剔除填充行后 T+1 IC 由 +0.0249 → +0.1150、ICIR 0.0875→0.4748——"85 分虚高"显著稀释资金面维度有效性，**同时实证 T2 修复必要性**。填充行占 T+1 样本 41.9%（144/344）。
- 港股（池化秩相关，横截面 1~6 行/日）：各维度 |ρ|<0.26、T+1/T+5 符号不稳，仅作参考。
- 样本口径对账：评审/PM 核验的 353（T+1）/241（T+5）为"仅严格相等行"口径（353=382 严格行−08-07 末行 29；241=382−07-31 后 141 行，均后验成立）；本报告按评审 §2.2 与开发提示词裁定采用 `trade_date <= analysis_date` 口径（纳入 66 行周末/补跑行，A股 T+1=344/港股 75=419、T+5=255/52=307），两种口径差异已在报告 §1.4 对账披露。
- **T+20 已删除**（0 可用样本，评审 P-1）；报告含每日 IC 序列表与 n/std/ICIR 披露；声明"仅方向性参考，不宣称统计显著"。
- 权重建议（基本面 T+5 最稳健、资金面证据偏弱等）仅参考，**未改 config_weights.json**。

### 2.3 零侵入证明
- 全程 `mode=ro` 只读连接 + 临时脚本（`%TEMP%\opencode\019T\`）；未写生产库、未改生产代码；临时脚本自测后删除。

---

## 三、T2 遗留项⑨修复（最高风险项）

### 3.1 代码改动（scoring_engine.py，共 3 处）

| 函数/配置 | 改动 | 裁定依据 |
|---|---|---|
| `CAPITAL_SUBITEMS.main_capital` | degradation `keep_default` → `zero`，移除 `default_fills` | 约束 8（方案 b：A 类归零） |
| `score_main_capital` | 缺失分支不再填充 0.0 进档位 → 返回 **50.0 + note**（展示诚实化） | 约束 8（P-2 硬约束） |
| `score_north_capital` | 缺失分支 70 → **50**（degradation 仍 reduce，实测档位未动） | 约束 9（开放项 A） |
| `score_margin_capital` | 缺失分支 68 → **50**（degradation 仍 reduce，实测档位未动） | 约束 9（开放项 A） |

未追溯历史评分、未碰 config_weights.json、未触碰 advisor（B24）。

### 3.2 单元测试（约束 10）
`tests/test_scoring_engine.py` 资金面测试类重写/新增：四类分支（缺失 50 / 实测 0 仍 85 / 实测正 95 / 实测负 42）+ north/margin 缺失 50 + 实测档位逐位断言 + degradation 配置防漂移测试（main='zero' 且无 default_fills；north/margin 保持 'reduce'）。`174 passed`。

### 3.3 端到端黄金断言（约束 11，临时库副本）

**方法**：生产库在线备份 → 临时副本；`load_stockdata_from_db` + `analyze` 对新版；内联复刻修复前三函数与 CAPITAL_SUBITEMS 跑旧版；逐子项分解对比。

**结果 A（真正零回归面，函数级 §3.2.4 定义）**：

```
main 实测值行: 26/26 main 子项逐位一致（score + effective_weight + normalized_weight 全部相等）
资本维度变化归因分解: 29/29 行吻合（Δcapital 与批准修复项理论值误差 < 1e-9）
```

**结果 B（总分对照）**：A 股 23 行全部变化 −0.4~−0.6 分，唯一归因 `north_capital 70→50 @归一权重0.072 → -1.4440`（margin 实测存在、main 实测存在，两子项零变化）；港股 6 行变化 −1.7~−7.3 分，归因 `north -1.6180@0.081 + margin -5.0976@0.283`（main 实测）或三子项齐变（main 缺失 3 行，−22.2530@0.636）。

**结论**：修复对"有实测数据"的零回归性在**子项函数级**完全成立（约束 10、评审 §3.2.4 定义）；但**评审 §3.3"23 只 A 股总分逐位一致/不变"的预期经实测不成立**——原因与证据见第八节（待 PM 裁定）。

### 3.4 港股变化清单（全部属预期修复，非回归）

| 股票 | total 旧→新 | capital 旧→新 | 归因 |
|---|---:|---:|---|
| HK3690 | 55.7→54.0 | 37.6→30.9 | north 70→50 + margin 68→50 |
| HK1810 | 64.4→57.2 | 79.0→50.0 | 三子项缺失（main 归零 + north + margin） |
| HK9988 | 71.8→64.6 | 79.0→50.0 | 同上 |
| HK2513 | 58.8→57.1 | 63.1→56.4 | north + margin |
| HK0100 | 67.9→60.6 | 79.0→50.0 | 三子项缺失 |
| HK1024 | 79.7→78.0 | 80.2→73.5 | north + margin |

---

## 四、T3 回测基准化

### 4.1 代码改动

| 模块 | 改动 | 裁定依据 |
|---|---|---|
| `index_collector.fetch_index_kline` | HK 分支 EM→sina 降级顺序（EM 异常 → log → sina；sina 多余 amount 列被既有列投影忽略） | 约束 14（P-3） |
| `daily_report._scheduler_tick` | generate_daily_report 返回后、`_schedule_next` 前挂 `refresh_all()`，异常隔离仅记日志（与 P3-B 同模式） | 约束 15（P-3/R-5） |
| `backtest_engine._ensure_columns` | 幂等追加 `bench_return_1d/1w/1m`、`alpha_1d/1w/1m`、`is_correct_alpha` 7 列（不重建表） | 约束 16 |
| `backtest_engine.BacktestEngine` | 新增 `BENCH_CODE`（a_stock→000300 / hk_stock→HSI）、`_get_bench_tn`（基准价 = index_kline `trade_date <= rating_date` 最近收盘；T+n = 基准行后严格第 n 行）、`_compute_alpha_block`（alpha=个股收益−基准收益；主 alpha 优先 1d→1w→1m；缺基准全 NULL 不判定） | 约束 17、评审 §2.2/§4.3 |
| `run_fixed_period_backtest` | UPDATE/INSERT 双分支写入 7 新列；`is_correct` 原口径保留不动 | 约束 17 |
| `run_historical_simulation` | 模拟行 INSERT 同补 alpha（与存量口径一致） | 约束 18 口径统一 |

### 4.2 现网实测（纯网络只读）

- `ak.stock_hk_index_daily_em` → `ConnectionError RemoteDisconnected`（复现评审 R-4 结论）
- 降级 `ak.stock_hk_index_daily_sina` → HSI/HSTECH 各 300 行（tail(300) 生效），最后日期 2026-08-07，6 列映射正常 ✓

### 4.3 存量补算（开放项 C 裁定：全部 820 行）

| 项 | 结果 |
|---|---|
| 脚本 | `%TEMP%\opencode\019T\backfill_alpha_019T.py`（幂等可重跑，参数化 market） |
| 备份 | `backups/db_backup_20260810_001918_019T_alpha_backfill_a.db`（backup_database 先备份） |
| A股 719 行（真实 455 + 模拟 264） | **补算完成**：alpha_1d 非 NULL 503、alpha_1w 413、alpha_1m 176；is_correct_alpha 400（正确 242 / 错误 158） |
| 全 NULL 216 行 | 属预期：A 股近末端 55 行（rating_date≥08-05，基准 T+n 超 index_kline 末端 08-05）+ 无个股收益行 |
| HK 101 行（真实 41 + 模拟 60） | **待补**：HSI 入库后执行同款脚本 `--market hk_stock`（脚本就绪） |
| 幂等性 | 临时副本二次重跑结果逐位一致 |
| 人工抽查 | 3 行 bench/alpha 与手工核算逐位一致（如 id=1343：bench=-3.6, alpha=1.91） |
| 运行时路径一致性 | 冒烟重跑 `run_fixed_period_backtest` 两条评级，UPDATE 分支写入值与补算值一致 |
| is_correct 原口径 | 未改动（分布 319 正确 / 未过滤计数，与补算前一致） |

### 4.4 新增单元测试（tests/test_backtest_alpha_019T.py，12 项）
`_ensure_columns` 幂等（追加 7 列不重建表）、`_get_bench_tn` 对齐（≤ 最近收盘 / 周末补跑 / 严格第 n 行 / 缺基准）、`_compute_alpha_block` 全路径（有基准 / 缺基准全 NULL 不判定 / 主 alpha 顺延 1w / 未知市场）、`_judge` 原口径未动。

---

## 五、全量回归

| 项 | 结果 |
|---|---|
| `python -m pytest tests/` | **355 passed**（基线 343 + 019T 新增 12），1 warning（requests 既有 urllib3 版本警告） |
| `ruff check`（改动文件 7 个） | **All checks passed**（0 错误）；仓库整体 35 处既有错误（scripts/diag_*、tests/qa_019f 等）未触碰，属基线存量 |
| `mypy`（改动 4 模块） | 0 新增错误；51 处既有错误（scoring_engine 技术面子项类型标注、data_collector 等）属基线存量，经逐行核对无一条落在 019T diff 内 |

---

## 六、T1 报告一致性抽查

- 基线复现（只读）：analysis_results 448 行（A 367/HK 81、29 股、07-16~08-07）；index_kline 5 指数×309 行止 08-05、HSI/HSTECH 零数据；backtest_results 820=496 真实+324 模拟；raw_capital_flow 3086 行中 main NULL 675（21.9%）、08-06 全日 23/23 NULL——与评审报告逐项一致。
- 评审数字复现：353/241（严格口径）后验成立（见 2.2）；08-06 capital_score 79.0×3 的"缺失→85 热填充"链在黄金测试旧版路径中复现（HK1810 old_cap=79.0 = 85×0.636+70×0.081+68×0.283）。

---

## 七、定时窗口纪律与写库时间点

| 时间点 | 操作 | 窗口合规 |
|---|---|---|
| 2026-08-10 00:19:18 | backup_database 备份（写 backups/） | ✓（非 16:10/16:30、非周日 20:00±30） |
| 2026-08-10 00:19~00:23 | A股 719 行 alpha 补算（写 backtest_results） | ✓ 同上 |
| 其余全部 | 只读核验 / 临时副本 / :memory: | — |

- 08-10 周一 16:10 日报与 16:30 019Q 收尾核验窗口**未触碰**；08-09 周日 20:00 优化窗口已过（本次全部写操作在 00:19~00:23）。
- 幂等迁移（_ensure_columns 7 列）由补算脚本在执行时触发，属同一时间点。

---

## 八、待 PM/监理裁定事项（开发不自行裁决）

### 8.1 约束 9 与约束 11 的实证冲突（黄金断言表述）
- **事实**：`raw_capital_flow.north_holding_change` 全表 3086 行 **0 行非 NULL**（100% 缺失，B26 停更后从未入库）；margin_balance 仅 A 股存在（HK 0 行）。
- **推论**：评审批准的同批修复（约束 9：north 缺失 70→50）会使**任何** north 缺失行总分变化——而 north 对所有市场都缺失，故"A 股 main 非 NULL 行总分逐位一致 / 23 只 A 股不变"（约束 11、评审 §3.3 注）**在现网数据下不可满足**（评审 §3.3"港股行因 north 恒缺失"隐含 A 股 north 存在，经实测不成立）。
- **已证**：函数级零回归（约束 10、§3.2.4 定义）完全成立（26/26 子项逐位一致）；端到端变化 29/29 行归因分解精确吻合批准修复项，无其他漂移。
- **请求裁定**：约束 11 验收表述是否改为"main 实测行 main 子项逐位一致 + 总分变化仅由批准修复项归因（附清单）"，或维持原文字（则本批次端到端断言按"预期变化"口径呈现）。开发暂不宣布该项 PASS/FAIL。

### 8.2 评审 P-5 描述勘误（pandas 3.0.2 Spearman）
- 评审 P-5/约束 4 载明"pandas 原生 corr(method='spearman') 已实测可用"；实测 **pandas 3.0.2 该方法内部 import scipy（未安装 → ModuleNotFoundError）**。
- 开发以"平均秩 + Pearson"手工实现（即 Spearman rho 定义，与 scipy spearmanr 平均秩口径一致，已用经典公式 rho=1−6Σd²/(n(n²−1))=0.6 校验通过），**零新依赖红线未破**。此为机制层适配非方法变更；请 PM 知悉并勘误评审描述。

### 8.3 后续待办（非本批次阻塞）
1. HK 101 行补算：app 重启后（监理裁定时机）16:10 定时刷新或手动指数刷新使 HSI 入库，再执行 `backfill_alpha_019T.py --market hk_stock`（幂等、需先备份）。
2. A 股近末端 55 行：指数刷新至 08-07/08-10 后重跑 A 股脚本自动补全（幂等）。
3. T2 部署时点（改生产代码生效）由监理裁定，避开 16:10/16:30 与周日 20:00 窗口。

---

## 九、交付物清单

| 交付物 | 路径 |
|---|---|
| IC 分析报告 | `docs/reports/ic_analysis_019T_20260810.md` |
| 自测报告（本文件） | `docs/reports/dev_selftest_019T_scoring_calibration_20260810.md` |
| 补算备份 | `backups/db_backup_20260810_001918_019T_alpha_backfill_a.db` |
| 补算脚本（临时，已删） | `%TEMP%\opencode\019T\backfill_alpha_019T.py`（供 HK 重跑，需从备份恢复脚本或重建） |

> 说明：补算脚本位于临时目录、按要求用完即删；HK 重跑时需由 PM/QA 向开发索取脚本文本（或在仓库 scripts/ 登记为运维脚本——本批次按约束 1 未入库）。
