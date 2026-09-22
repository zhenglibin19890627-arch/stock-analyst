# 021BR t3 实施报告：持仓数量口径一致性 + 现价源统一 + 异常清单按层级契约处置

> 日期：2026-09-22 ｜ 实施：builder（任务 t3）｜ 依据：t1 诊断 + 队长批示（P1 时序/聚合维持方案、分仓明细注记、realized 脚本只交付不执行、账户二数据用户自处置）
> 验收：**fast 1034 passed, 1 skipped** + ruff 全绿 + mypy 55 文件 0 错 + **红线 28/28** + node --check ×2 ｜ 清理脚本 dry-run 实测通过（只读预览，零写库）

---

## 一、交付概览

| # | 交付物 | 说明 |
|---|---|---|
| 1 | 日报 trader 摘要时序修正（P1，诊断缺陷 C） | `generate_trader_advice(stock_id, price_advice_override=None)` 新增可选参数（None=既有读库路径，向后兼容）；`daily_report` 生成当日 `price_advice` 后传入 → `key_factors.trader.top_action` 与报告行 `price_advice` **同源**，消除「advisor 先插 price_advice=NULL 行 → trader 摘要读到自身行 → top_action 恒纪律线」的每日必现矛盾 |
| 2 | price_advisor 成本聚合口径（P1，诊断缺陷 D/A4/A5） | `_read_cost_price` holdings 分支改 021BQ 同款 `SUM(quantity)+加权成本`（quantity>0），与 trader_advisor/action_list **三处同口径**；positions fallback 保留。中免价格建议成本 61.05→57.53、止损 56.16→52.93（=纪律线，双源合流） |
| 3 | 矩阵分仓明细注记（数量口径可核对） | `_gather_inputs` 新增逐账户行查询（LEFT JOIN accounts 取名）；`operations.holding.accounts` 透出；持仓行（多账户时）附「账户A 600@10.00 + 账户B 400@20.00，成本为加权摊薄口径」，**分仓成本差异 >5% 追加「分仓成本差异大，止损线以各账户实际成本为准」**（队长批示话术）——把「账户一 1,100@61.046 / 账户二 1,000@53.6686」直接摆给用户，正是暴露此类不一致的机制 |
| 4 | 现价源统一标注（latest_price=NULL 处置，诊断 A13/§2.3） | 诊断确认：`price_cache` 56/56 有值（持仓页盘中快照），`holdings.latest_price` 为无读取方死列——**刷新链路无 bug，属口径误解**。处置：`operations.price_source = {close, date, source:'raw_kline 日K收盘', desc}`，前端标注「现价取数：X（日期 日K收盘）· 持仓页为盘中快照口径（price_cache），触发判定以日K收盘为准」；持仓行附「触发判定同源」；**测试锁定 status.close == price_source.close**（触发判定与展示现价同一价格源） |
| 5 | 行动清单补「持仓纪律」类目（P2，诊断缺陷 F/A3） | `action_list` 新增路0：`_scan_stop_discipline`（只读）对每只持仓股计算 `close < max(成本×0.92, 最新日报 price_advice.stop_loss)`（与矩阵 `_stop_level` 双源取高者同口径）→ P1 行动项「止损纪律已触发：现价 X（日期 日K收盘）低于有效止损 Y（成本线 A / 建议止损 B 取高者），持仓 N 股——纪律无条件执行，不等评级、不等反抽」；**排序置顶于一切评级项**（纪律最高产品原则）；stats 增 `stop_discipline_hits`；前端红色徽标「止损纪律·已触发」 |
| 6 | realized 残留清理脚本（P2，诊断 A11，**只交付不执行**） | `scripts/cleanup_realized_residual.py`：逐（股票，账户）摊薄法重放（与 `_recalculate_holding` 2026-09-18 版逐字对齐），**只比对/回写 realized_pnl，严禁触碰 cost_price（用户人工修正）/quantity/流水**；默认 dry-run，`--apply` 先 R11 备份（失败即中止）。**dry-run 实测：10 行 holdings 定位唯一残留行 holding_id=12（中免/银河，-1341.41 → 0.0），零写库**；是否执行待队长请示用户 |

## 二、t1 异常清单 15 项处置对照

| # | 异常 | 处置 | 批次 |
|---|---|---|---|
| A1 | 止损行无「已触发」标注 | ✅ 矩阵行状态化 + status 置顶 | t2 |
| A2 | 共振「持仓者持有」同屏矛盾 + 无日期 | ✅ 止损门控改写 + 触发日期时效 | t2 |
| A3 | 破止损股行动清单零项静默 | ✅ 路0 持仓纪律类目（P1 置顶） | **t3** |
| A4 | 同股同日三个止损数字 | ✅ 时序 override（#1）+ 聚合口径（#2），三处同源 | **t3** |
| A5 | price_advisor 单账户成本双基数 | ✅ SUM 聚合收口（#2） | **t3** |
| A6/A7 | 共振滞留（五粮液/无线传媒） | ✅ 触发日期 + 「窗口内历史，非今日」 | t2 |
| A8 | 拓尔思买点共振×减仓档无调和 | ✅ 共振行相悖调和（独立元组） | t2 |
| A9 | 汤臣下跌期「反抽减仓」×「持仓者持有」互斥双行 | ✅ 共振阶段门控 | t2 |
| A10 | 人工成本修正被新流水重算覆盖 | 📄 口径说明（行为改写涉流水链路，超出本批；语义已向用户说明——修正值会被下笔流水重放打回，建议改补录费用流水） | 告知 |
| A11 | realized -1,341.41 摊薄法切换残留 | ✅ 脚本交付（#6），**未执行**，待用户批准 | **t3** |
| A12 | 恒瑞「顶部出货区(强)」×持有观望无分歧提示 | ✅ 分歧检测补 stage_leads_rating 档（仅强置信防抖动） | t2 |
| A13 | price_cache 盘中快照 vs 日K收盘无对账 | ✅ 现价源显式标注 + 触发判定同源锁定（#4）；持仓页盘中口径属特性非缺陷（快照 TTL 24h） | **t3** |
| A14 | holdings.latest_price 死列 10/10 NULL | 📄 信息性——确认无读取方、无刷新链路 bug；schema 清理另立批次（动表结构须 R11+级联核查，收益低） | 告知 |
| A15 | trade 45 holding_id=NULL 痕迹 | 📄 信息性——重放按 (stock_id, account_id) 不受影响 | 告知 |

**层级契约对异常判定的修正（任务要点③）**：强下跌/破位 × 评级滞后（如中免「下跌期(强)」×「持有观望」）按 021BR 分域层级属**合规行为**——操盘手减仓纪律独立触发、行内标注「评级尚未跟上」，不再作为矛盾上报；方向类矛盾（买点信号×减仓档）保留「以评级为主」调和（021BP 锁定契约）。A12 类「阶段领先」经 `stage_leads_rating` 走分歧提示通道而非矛盾通道。

## 三、设计决策

1. **override 参数落点**：`_gather_inputs` 内替换 price_advice 来源（`_load_price_advice` 已兼容 str/dict），`generate_trader_advice` 与 `_gather_inputs` 均向后兼容；daily_report 传 `price_advice` 内存对象（generate_price_advice 返回 None 时自动回退读库路径）。
2. **分仓明细触发条件**：`len(accounts) >= 2` 才附明细（单账户无信息量）；差异阈值 5%（相对加权成本）触发「以各账户实际成本为准」提示——中免 61.046 vs 53.6686（差 13.7%）命中。
3. **纪律扫描价格源**：现价取 raw_kline 最新日K收盘（与矩阵/触发判定同源），**不用 price_cache 盘中快照**——纪律判定必须可复现，盘中快照会造成「清单说触发、矩阵说未触发」的新矛盾。
4. **realized 脚本不修成本**：账面成本 61.046 含 5 次人工修正（is_cost_adjusted=1，设计内），重放值 61.3455 仅作对照展示，永不回写。
5. **数据问题边界（队长批示）**：用户已确认真实持仓为银河 1,100@61.046；东财账户 601888 1000@53.6686 与真实不符属**用户数据问题**，本轮代码零触碰——分仓明细注记正是让用户自行发现并处置的机制；用户清理后单行/聚合两基数自然收敛（61.046/52.93），当前阶段 52.93/56.16 双数值差异主因是数据而非代码。

## 四、改动文件

```
modules/trader_advisor.py       # price_advice_override + 分仓明细查询/透出 + price_source
modules/daily_report.py         # trader 摘要传入 price_advice_override（时序修正）
modules/price_advisor.py        # _read_cost_price 改账户无关 SUM 聚合
modules/action_list.py          # 路0 持仓纪律扫描 + P1 置顶行动项 + stats 计数
static/js/portfolio.js          # 行动清单：止损纪律红色徽标 + 统计 chip
static/js/analysis.js           # 操作矩阵：现价取数来源标注行
scripts/cleanup_realized_residual.py  # 新增：realized 残留清理（dry-run 默认/--apply + R11 备份）
tests/test_trader_advisor.py    # +4（TestT3Consistency021BR：分仓明细/现价源同源/override 线程）
tests/test_action_list.py       # +6（TestStopDisciplineRoute021BR×4 + 全链×2）
tests/test_price_advisor_021br.py  # 新增×4（多账户加权/清仓行剔除/无持仓/positions fallback）
docs/reports/021br_impl_consistency_20260922.md  # 本报告
```

## 五、验证记录

```
python -m pytest tests/                 → 1034 passed, 1 skipped（fast 层，64s；t2 后 1020 → +14）
ruff check .                            → All checks passed!
python -m mypy app.py config.py modules → Success: no issues found in 55 source files
python scripts/check_redlines.py        → 28/28 通过
node --check static/js/analysis.js      → OK
node --check static/js/portfolio.js     → OK
python scripts/cleanup_realized_residual.py   → dry-run 实测：10 行扫描、唯一定位 holding_id=12
                                          （中免/银河 -1341.41 → 0.0），零写库，exit 0
```

关键新测试锚点：`test_price_advice_override_threading`（NULL 行不再拖累价位层，override 双源取高者）、`test_price_source_same_as_trigger`（触发判定与展示现价同源）、`test_multi_account_breakdown_row`（分仓明细+差异提示）、`TestStopDisciplineRoute021BR`（纪律置顶于评级降级、双源文案、无裸 `<`）、`test_get_action_list_stop_discipline_chain`（临时库全链：holdings+K线+price_advice → P1 置顶项）、`TestCostAggregation021BR`（57.5330 加权口径）。

## 六、红线自检

| 红线 | 结论 |
|---|---|
| B24/R13 | advisor.generate_advice 零触碰；时序修正全部在 daily_report 调用侧与 trader_advisor 参数 |
| R9 | `_save_report` 单点写入不变；override 只影响 trader 摘要的内存计算，不新增写库点 |
| R11 | 清理脚本 --apply 前强制 `backup_database(reason='realized_residual_cleanup')`，失败即中止（脚本内置）；本轮未执行任何写库 |
| R16/R15 | 风控阈值、流水签名/链序零触碰；脚本不改 trade_records/quantity/cost_price |
| V8/R8 | 纪律扫描只读 raw_kline/holdings/daily_reports；零网络零新增采集 |
| 021BN | 行动清单新文案无裸 '<'（测试断言）；前端一律 escape |
| 021W | price_advisor 聚合为最后一个单行取数点收口（holdings 域三处读取方全同口径） |

## 七、遗留与告知用户事项

1. **数据处置（用户操作）**：账户二东财 601888 的 1000@53.66 流水与真实持仓不符——请用户在持仓页/流水页核对后自行补录卖出或删除误录；处置后矩阵聚合、价格建议、纪律线自动收敛为 1,100@61.046 口径（止损 52.93）。
2. **realized 清理待批**：脚本已就绪（dry-run 预览确认唯一残留行）；执行命令 `python scripts/cleanup_realized_residual.py --apply`（自动 R11 备份），等队长请示用户后裁定。
3. **A10 口径提示**：人工成本修正会被下一笔新流水的全量重放覆盖（`_recalculate_holding` 从流水重算）——如需保留修正语义，建议将差异以「费用/分红」类流水补录而非手工改成本；是否改造重算语义（如保留 is_cost_adjusted 标记跳过成本重算）属独立批次。
4. **A14 死列**：`holdings.latest_price`/`price_updated_at` 无读取方，属 021S 前遗留；如需清理列须走表结构变更流程（R11 备份+级联核查），建议随下次 schema 批次一并处理。
