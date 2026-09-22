# 021BR t4 全量验收报告：分域层级契约 + 矩阵无矛盾 + 数量口径一致

> 日期：2026-09-22 ｜ 验收：checker（任务 t4）｜ 依据：AGENTS.md §5、t1 诊断 `021br_matrix_diag_20260922.md`、t2 报告 `021br_impl_matrix_hierarchy_20260922.md`、t3 报告 `021br_impl_consistency_20260922.md`
> 方法：静态四道门全量重跑 + **运行实例只读冒烟**（GET 端点，零写库、零采集）+ 单元契约测试逐项核对 + t1 异常清单 15 项逐项对照 + 独立复跑 realized 清理脚本 dry-run。

---

## 0. 结论速览

**验收结论：通过（无保留）。** 三类验收目标全部达成：

| 验收目标 | 结果 |
|---|---|
| ① 分域层级契约（纪律最高/减仓独立触发/买入评级门控 + 状态置顶 + 共振时效） | ✅ 达成（live 实测 + 20 例契约测试全绿） |
| ② 矩阵无矛盾（止损已触发状态下无"持有"类行） | ✅ 达成（中免 live 矩阵全文 0 处「持仓者持有」、0 处「按评级执行风控」） |
| ③ 持仓数量口径一致（2,100@57.53 可核对） | ✅ 达成（矩阵分仓明细逐账户透出 1,100@61.05 + 1,000@53.67，与 holdings/重放三口径一致） |

**2 条非阻塞观察**（同日切换的旧数据残留，均自愈于下次日报生成，非代码回归，见 §7）。

---

## 1. 依赖与环境确认

| 项 | 结果 |
|---|---|
| t1（诊断） | completed，报告在档 |
| t2（矩阵分域层级） | completed，已提交 `ef7fde5`（19:52:22） |
| t3（口径一致性） | completed，改动在工作区未提交（8 文件 + 3 新增，最后写入 20:03:19）——**提交由队长裁定，本任务不代办** |
| 服务重启 | ✅ stock_analyst 服务 PID 39624 **20:07:27 启动**，晚于 t3 最后文件写入（20:03:19），`FLASK_DEBUG=False` 无热重载 → 运行实例确含 t2+t3 全部代码 |
| `/api/health` | running（v5.0） |
| 本任务写库 | 零（全程 `mode=ro` 语义 + GET 端点；cleanup 脚本以默认 dry-run 复跑） |

---

## 2. 四道门验证（全量重跑）

| 门 | 命令 | 结果 |
|---|---|---|
| pytest 全量含 slow | `python -m pytest tests/ -m "slow or not slow" --timeout=600 -q` | **1040 passed, 1 skipped，663.77s（11:03），exit 0**（t2 后 fast 1020 → t3 后 fast 1034 → 全量 1040+1，fast+6 slow 例吻合） |
| ruff | `ruff check .` | All checks passed! |
| mypy | `python -m mypy app.py config.py modules` | Success: no issues found in 55 source files |
| 红线 | `python scripts/check_redlines.py` | **28/28 通过**（另随 pytest 执行亦通过） |
| JS 语法 | `node --check` analysis.js / portfolio.js | 双 OK |

专项契约测试（t2/t3 新增 20 例 + 全链 1 例）逐例通过，锚点名实录：

- `TestAsymmetricHierarchy021BR`（12/12）：`test_zhongmian_no_hold_when_stop_triggered`、`test_zhongmian_stop_status_pinned_top`、`test_layer_annotations_on_all_rows`、`test_breakdown_independent_of_rating_lag`、`test_weak_rating_aligned_note`、`test_buy_domain_still_rating_gated`、`test_resonance_normal_wording_when_stop_not_triggered`、`test_linkage_direct_stop_gate`、`test_weak_stage_resonance_no_hold`、`test_hold_row_survives_when_stop_not_triggered`、`test_no_bare_lt_in_hierarchy_texts`、`test_zhongmian_end_to_end`
- `TestStageLeadDisagreement021BR`（4/4）：强置信下跌才告警 / 中弱置信不告警 / 牛市不告警 / playbook 分支
- `TestT3Consistency021BR`（4/4）：分仓明细行 / 单账户不出明细 / `test_price_source_same_as_trigger`（触发判定与展示现价同源）/ `test_price_advice_override_threading`
- `TestStopDisciplineRoute021BR`（4/4）+ `test_get_action_list_stop_discipline_chain`（临时库全链：纪律项置顶于评级降档）
- `TestCostAggregation021BR`（4/4）：多账户加权 57.5330 口径 / 清仓行剔除 / 无持仓返回 None / positions fallback 保留

---

## 3. 中国中免（stock_id=21）实例复验（live 只读冒烟，20:08–20:14）

### 3.1 `/api/stocks/21/trader-advice` —— 矩阵形态逐项核验

| 任务要点 | live 实测 | 判定 |
|---|---|---|
| 顶部状态行存在 | `operations.status = {kind:'stop_triggered', close:52.27, stop_line:56.16, trigger_date:'2026-08-11', ma20_broken:true, text:'止损纪律已触发：现价 52.27 低于触发线 56.16（2026-08-11 起失守）；同时已跌破 MA20…无条件执行，不等评级、不等反抽'}` | ✅ |
| 止损已触发状态下不得再有"持有"类行 | held_rows 共 4 行：**止损（纪律/triggered）· 减仓检查（战术/triggered）· 减仓（条件）（战术）· 仓位纪律（战略）**——无任何"持有"动作行；全文检索 `持仓者持有` 0 处、`按评级执行风控` 0 处 | ✅ |
| 止损行带"已触发"标注 | `status='triggered'`，trigger 文案"——已触发：现价 52.27 低于触发线（2026-08-11 起失守），无条件执行不等待"；`top_action='止损·56.16（已触发）'` | ✅ |
| 减仓行带层级标注 | 全部行带 `layer`（纪律/战术/战略）；减仓检查行 note=**「操盘手纪律触发，评级尚未跟上（当前评级 持有观望）」**，trigger=「操盘手破位纪律触发：反抽不收复 MA20 则降低仓位」（不再推给评级） | ✅ |
| 共振带日期 | linkage=「买点侧共振（最高 5 星，**触发于 2026-09-21（窗口内历史，非今日）**）与止损纪律冲突——**止损纪律已触发，共振仅作反抽减仓参考，不构成持有理由**」（日期+止损门控双达标）；空仓侧「关注」行同样带日期 | ✅ |
| 分歧补档（t2 附加） | `disagreement={type:'stage_leads_rating', text:'阶段领先于评级…'}`（下跌期·强置信 × 持有观望）；playbook 含 ⚠️ 对策分支 | ✅ |
| 现价源统一（t3） | `price_source={close:52.27, date:'2026-09-22', source:'raw_kline 日K收盘'}`；仓位纪律行注「触发判定同源」 | ✅ |
| 数量口径可核对（t3） | `holding={qty:2100, cost:57.533, accounts:[银河证券 1100@61.046, 东方财富 1000@53.6686]}`；仓位纪律行逐账户列出并注「分仓成本差异大，止损线以各账户实际成本为准」（差异 13.7% > 5% 阈值命中） | ✅ |

### 3.2 `/api/dashboard/action-list` —— 持仓纪律类目（t3 路0）

- items[0]（置顶于全部 27 项之前）：`kind='stop_discipline'`、`priority=1`、`priority_label='持仓纪律'`、stock 21 中国中免，reason=「止损纪律已触发：现价 52.27（2026-09-22 日K收盘）低于有效止损 56.16（成本线 52.93 / 建议止损 56.16 取高者），持仓 2,100 股——纪律无条件执行，不等评级、不等反抽」；detail 双源明细 `{discipline_stop:52.93, pa_stop:56.16, stop_line:56.16, close:52.27, total_qty:2100, avg_cost:57.533}`。
- `stats.stop_discipline_hits=1`（全持仓唯一命中=中免，与 t1 诊断「双破唯一股」一致）；排序契约「纪律 > 评级升降 > 卖出信号」成立（其后才是 5 项 P1 评级项）。
- A3 静默缺口关闭：破止损股今日行动清单从 **0 项 → 1 项置顶**。

### 3.3 `/api/stocks/21/trend` —— 趋势罗盘回归

200 正常：close=52.27，overall=down，日/周/月三周期均 down（与操盘手「下跌期(强)」方向一致，无跨模块矛盾）。

### 3.4 契约相关股 live 抽样（A6/A7/A8/A9 处置复验）

| 股 | live 形态 | 判定 |
|---|---|---|
| 24 五粮液（A6） | 共振「触发于 2026-09-21（窗口内历史，非今日）」+ 弱势阶段门控→反抽减仓；disagreement=stage_leads_rating | ✅ |
| 100028 无线传媒（A7） | 共振「触发于 2026-09-18（窗口内历史，非今日）」——4 日滞留不再读作新信号 | ✅ |
| 100027 拓尔思（A8） | 共振行「触发于 2026-09-22（今日）」+ **相悖调和**「买点共振与评级「建议减仓」相悖：共振只作反抽参考——不加仓，以评级为主」；未破止损/未破 MA20 →「持有」行合法保留（止损未触发时层级契约不压制） | ✅ |
| 7 汤臣倍健（A9） | 下跌期(中)×今日共振：「弱势阶段出现买点共振（…今日）→ 反抽减仓」——「反抽减仓」×「持仓者持有」互斥双行消除 | ✅ |

---

## 4. 分域层级契约专项核验（任务要点④）

**真实数据强下跌×评级滞后场景 = 中免（下跌期·强置信 × 持有观望）**：

- 减仓/离场独立触发：减仓检查行由操盘手破位纪律直接触发（status=triggered），**不出现**「按评级执行风控」式推诿（全文 0 处）；行内注记「操盘手纪律触发，评级尚未跟上（当前评级 持有观望）」——契约②逐字达标。
- 纪律最高：止损 triggered 时共振行被改写为「不构成持有理由」，「持有」行整体消失——契约①达标。
- 分歧通道：stage_leads_rating 提示 + playbook「减仓/离场听操盘手纪律，加仓继续看评级」裁决信号。

**构造场景（单元测试临时库）**：`test_zhongmian_no_hold_when_stop_triggered`（止损+5星共振同现→无持有行）、`test_breakdown_independent_of_rating_lag`（评级滞后×破位→减仓行+注记+全文无推诿串）、`test_weak_stage_resonance_no_hold`、`test_linkage_direct_stop_gate`、`test_hold_row_survives_when_stop_not_triggered`（止损未触发时「持有」行保留——防止过度压制）全绿。

**买入域仍评级门控（契约③）**：`test_buy_domain_still_rating_gated` 通过；live 拓尔思（建议减仓×今日买点共振）仍走 021BP「以评级为主」调和，未出现催促买入。

---

## 5. 持仓数量口径一致性（任务要点②复验）

| 口径 | 数值 | 一致性 |
|---|---|---|
| holdings 两行 | 银河 1,100@61.046 + 东财 1,000@53.6686 | 基准（t1 已重放流水验证数量自洽） |
| live 矩阵聚合 | 2,100@57.533（-9.1%） | ✅ |
| live 矩阵分仓明细 | 逐账户 1100@61.046 / 1000@53.6686 透出 | ✅ 用户可对照券商 App 逐账户核对 |
| 行动清单纪律项 detail | total_qty=2100, avg_cost=57.533 | ✅ 同源 |
| price_advisor 聚合（t3 修复） | SUM 加权口径（`price_advisor.py` L344-358）+ 4 例测试锁定 | ✅ 代码面收口；存量日报行内旧 price_advice 见 §7-观察2 |

给用户的核对口径（t1 §6 清单）继续有效，且现已在矩阵页**直接可见**逐账户数量成本，无需查表。

---

## 6. t1 异常清单 15 项处置对照（逐项）

| # | 异常 | 处置声明（t2/t3 报告） | t4 独立核验 | 判定 |
|---|---|---|---|---|
| A1 | 止损行无「已触发」标注 | t2 状态化 | live：status=triggered + 现价 + 失守日 + top_action 后缀 | ✅ |
| A2 | 共振「持仓者持有」矛盾 + 无日期 | t2 门控+日期 | live：改写文案 + 触发日期；全文无「持仓者持有」 | ✅ |
| A3 | 破止损股行动清单静默 | t3 路0 | live：items[0] 持仓纪律 P1 置顶，hits=1 | ✅ |
| A4 | 同股同日三个止损数字 | t3 时序 override + 聚合 | 代码核验（daily_report L1123-1130 传 override）+ 测试锁定；**存量报告残留见 §7-观察2** | ✅（代码面） |
| A5 | price_advisor 单账户成本 | t3 SUM 聚合 | 代码核验 + TestCostAggregation021BR×4 | ✅ |
| A6 | 五粮液共振滞留无日期 | t2 | live：日期+「窗口内历史，非今日」 | ✅ |
| A7 | 无线传媒 4 日滞留 | t2 | live：同上 | ✅ |
| A8 | 拓尔思共振×减仓档无调和 | t2 独立注记 | live：共振行相悖调和在位 | ✅ |
| A9 | 汤臣互斥双行 | t2 阶段门控 | live：弱势共振→反抽减仓单行 | ✅ |
| A10 | 人工成本修正被重算覆盖 | 告知（口径说明） | t3 报告 §七.3 已说明；行为改写列独立批次 | 📄 告知（符合批次边界） |
| A11 | realized -1,341.41 残留 | t3 脚本交付不执行 | **本任务独立复跑 dry-run**：扫描 10 行、唯一定位 holding_id=12（-1341.41→0.0，数量 1100=1100，成本 61.046 不触碰），零写库 exit 0 | ✅（待用户批准 --apply） |
| A12 | 恒瑞类「阶段领先」无提示 | t2 分歧补档 | live 中免/五粮液 stage_leads_rating；测试 4/4；**行动清单侧旧数据观察见 §7-观察1** | ✅ |
| A13 | 两价格源无对账标注 | t3 price_source 标注 | live price_source + 「触发判定同源」+ 同源测试 | ✅ |
| A14 | holdings.latest_price 死列 | 告知（信息性） | 确认无读取方；schema 清理列独立批次 | 📄 告知（符合） |
| A15 | trade 45 holding_id=NULL 痕迹 | 告知（信息性） | 重放按 (stock_id, account_id) 不受影响 | 📄 告知（符合） |

---

## 7. Findings（非阻塞观察与信息，不自行修复）

| # | 级别 | 内容 | 建议 |
|---|---|---|---|
| 观察1 | 低（自愈型） | 行动清单 overview `has_disagreement` 对中免/五粮液=false，而 live trader-advice 已报 stage_leads_rating——根因是 action_list 读**日报期预计算**的 stored key_factors（action_list.py L324 注释「日报期预计算；零重算」），今日报告为 11:38 旧代码产物。属同日切换数据残留，非本批回归 | 明日日报生成后自动一致；如需当日闭环可在下次批次评估 overview 侧是否补实时回退（非必须） |
| 观察2 | 低（自愈型） | live 矩阵止损线 56.16 取自今日 stored price_advice（旧单账户成本 61.05 口径）；t3 聚合修复生效于**下次**日报生成，届时 price_advice.stop_loss 收敛 52.93=纪律线，三数字合流。当前「56.16（已触发）」与 playbook「52.93 价格建议卡止损底线」并存的旧数据双数值仍可见 | 同上，随下次日报自愈；无需代码动作 |
| 信息1 | - | t3 改动（8 文件修改 + 3 新增含本报告系列）仍在工作区**未提交**（t2 已提交 ef7fde5） | 由队长裁定提交时机 |
| 信息2 | - | 验收过程中 checker 曾粗比对 classify_stage 报「有差异」，经查为 PowerShell 管道编码伪影；已用 `git diff -U0` 字节级核验推翻（见 §8） | 无需行动，记录避免误传 |

---

## 8. 红线核验

| 红线 | 核验方法 | 结论 |
|---|---|---|
| B24：advisor.generate_advice 禁改 | `git diff 5bb2b8f -- modules/advisor.py` 为空 | ✅ 零触碰 |
| classify_stage 本体零改动（021BQ 锁） | `git diff -U0 5bb2b8f -- modules/trader_advisor.py` 全部 61 个 hunk 与 classify_stage 新行域 L340–452 **零交集**（改动落在 _read_signals/_gather_inputs/detect_disagreement/build_playbook/_row/signal_stage_linkage/build_operations_matrix/generate_trader_advice，与 t2/t3 报告声明一致） | ✅ 本体零改动 |
| R9 报告写入 | `_save_report` 单点写入不变；override 仅影响内存计算路径 | ✅ |
| R16 风控阈值 | config 阈值零改动（git 状态无 config.py） | ✅ |
| V8/R8 只读零采集 | 本任务全程 GET + ro；新增纪律扫描仅读 raw_kline/holdings/daily_reports | ✅ |
| 021BN 无裸 `<` | `test_no_bare_lt_in_hierarchy_texts` 等通过 | ✅ |
| R11 | cleanup 脚本 --apply 前强制备份；本轮仅 dry-run | ✅ |

---

## 9. 结论

021BR 批次三类验收目标（分域层级契约 / 矩阵无矛盾 / 数量口径一致）在**代码面、测试面、运行实例面**三线全部达成：全量 1040+1 通过、四道门全绿、中免 live 矩阵「止损已触发→无持有行→共振带日期改写→减仓独立触发带评级滞后注记→状态置顶→分仓明细可核对」全链成立、行动清单纪律项置顶、t1 异常清单 15 项处置与声明一致（11 项代码/测试核验通过，4 项按批次边界告知）。2 条自愈型旧数据观察随下次日报生成消除，无阻塞、无新回归。**建议整批通过；t3 提交与 realized 清理脚本 --apply（需用户批准+R11 备份）由队长裁定推进。**
