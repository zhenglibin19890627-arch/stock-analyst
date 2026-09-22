# 021BQ 全量验证与端到端验收报告（t5）

> 日期：2026-09-22 ｜ 验收：checker（任务 t5）｜ 依赖：t1 盘点 / t2 卖点信号 / t3 操盘手矩阵 / t4 行动清单（全部 completed）
> 结论先行：**通过（无保留）**——四道门全绿、双卖点实证成立、操盘手矩阵双视角实测完整、红线零触碰；2 项观察项 + 1 项收尾事项（见 §8，均不阻塞）。

---

## 一、验证环境与前置确认

| 项 | 状态 |
|---|---|
| 代码状态 | t2（0548294）/ t3（0d7ab6b）已提交；t4 为工作区未提交改动（modules/action_list.py、static/js/portfolio.js、tests/test_action_list.py、tests/test_routes.py + 本批次报告文档）——**t4 功能已全部在工作区生效并纳入本次验证** |
| 运行实例 | PID 27192（pythonw，app.py），**2026-09-22 09:47:52 由队长重启**，晚于全部源码 mtime（最晚 09:40:51）→ **已加载 021BQ 新代码** |
| 时间线说明 | 验收最初一次进程检查（≈09:47 前一刻）抓到的是重启前的旧进程 PID 32676（08:36 启动）；随后冒烟请求已落到 09:47:52 新实例（新端点 200 为证）。已复核监听者唯一性（127.0.0.1:5000 仅 PID 27192），无新旧进程并存 |
| 只读纪律 | 冒烟全部 GET；数据库核查一律 SQLite URI `mode=ro`；形态实证为离线纯函数（零库零网）。全程零写库 |

## 二、四道验证门（AGENTS.md §5）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `python -m pytest tests/ -m "slow or not slow"` | **1010 passed, 1 skipped, 0 failed**（122.25s）。slow 层 6 例（TestTickBackoff ×4 + index_refresh ×2）全部实跑 PASSED（日志逐条核对）；与 t4 报告 fast 基线 1004 对账：1010−1004=6 恰为 slow 层，无用例丢失 |
| 2 | `ruff check .` | All checks passed! |
| 3 | `mypy app.py config.py modules` | Success: no issues in 55 source files（仅 5 条 untyped-def 提示性 note，既有基线） |
| 4 | `python scripts/check_redlines.py` | **28/28 通过**（基线不动） |

> 注：全量实测 2 分 02 秒，快于 AGENTS.md §5 的"约 7~8 分钟"文档基线（slow 层确已实跑）。属文档基线漂移，非验证缺漏，建议后续批次顺手刷新该文档数字。

## 三、运行实例只读冒烟（127.0.0.1:5000）

| 端点 | HTTP | 观测 | 结论 |
|---|---|---|---|
| `/api/health` | 200 | `status=running, version=v5.0` | ✅ |
| `/api/market/scan/watchlist-signals`（旧买侧） | 200 | 56 只巡检 35 命中；顶层键 `scope/stock_count/results/errors/success`，results 键 `matches/resonances/kline_upto/kline_count`，**无 `side`/`sell_*` 键**——买侧契约零回归 | ✅ |
| `/api/market/scan/watchlist-sell-signals`（新卖侧） | 200 | `side='sell'`；56 只巡检 **8 只真实命中**：688017/688981/603501/300456 `ma20_break@09-21`、002230 `macd_dead_below+kdj_dead@09-17 + res_week_bear 5★`、300497 `kdj_dead_high@09-18`、HK0700/600577 `kdj_dead`；单条结构 `{signal,label,note,trigger_date}` | ✅ 双侧结构成立 |
| `/api/dashboard/action-list` | 200 | 16 项=12 买点+4 卖点；卖点项 detail 含 `held/total_qty/avg_cost/rating_conflict`；`stats.sell_hits=4, sell_resonance_hits=0`（新统计键）；买点×「建议减仓」相悖调和话术原样（c23f9ee 未回归）；排序实测：买点组在前、卖出·空仓垫底 | ✅ 卖点项+持仓标记字段在位 |
| `/api/alerts/rules` | 200 | `rule#15 rule_type='sell_signal' enabled=1 scope=全局`（created_at=09:47:55，重启幂等种子即时落库）；`rule#14 tech_signal` 同在 | ✅ 新类型已同步 |
| `/api/stocks/17/trader-advice`、`/api/stocks/4`、`/api/stocks/21` | 200 | 见 §5（operations 增量键 + 既有 7 键并存） | ✅ |
| `/api/portfolio/watchlist-scores` | 200 | `trader_signal` 透传（stage_name/has_disagreement/disagreement_text）正常；`top_action=null`——见 §8 观察项① | ✅（带时序说明） |

## 四、核心验收焦点①：卖点信号在下跌形态上真实触发

**三层实证全部成立：**

1. **合成形态（离线纯函数，逐条执行验证）**
   - 水上双死叉配方（44 稳涨+加速阳+大阴）→ `macd_dead_above` + `kdj_dead_high` 同日命中；
   - 破位配方（上行 40 根+收于 MA20 下方）→ `ma20_break` 当日命中，`compute_watchlist_sell_result` 返回 `side='sell'`；
   - 顶背离形态（急涨→回调→微破峰→加速阳→大阴）→ `kdj_dead_high` + **`res_top_reverse` 5★ bear 共振**；
   - 负例：35 根单边上涨 → 卖点命中 **0**（上涨形态零误报）；
   - `SELL_SIGNAL_LIBRARY` 恰 5 信号（macd_dead_above/macd_dead_below/kdj_dead_high/kdj_dead/ma20_break），与 `SIGNAL_LIBRARY` 平行、买侧库未被污染（在线扫描只产买点边界保持，测试锁定）。
2. **真实数据（运行实例）**：8 只命中均为真下跌——
   - 688017 绿的谐波：9 月全程 MA20 下方，09-18 反抽站上（294.22 > MA20 290.11），**09-21 收 288.88 < MA20×1.01（292.69）** → `ma20_break@09-21` 与实现口径（前收≥MA20 且今收<MA20×1.01 缓冲线）逐字吻合；
   - 002230 科大讯飞：9 月收盘全部位于 MA20 下方（38.3~40.2 区间阴跌）→ 水下死叉×2 + 周线空头 5★，方向判定正确。
3. **"今日出现"闸门（真实数据）**：巡检窗口内 8 只命中，行动清单只收 trigger_date==kline_upto（09-21）的 4 只；09-17/09-18 触发的 4 只（科大讯飞/富祥股份/腾讯控股/精达股份）正确被闸——口径与 t2 报告 §五"今日过滤在消费方做"一致。

## 五、核心验收焦点②：操盘手矩阵双视角完整性（真实持仓/空仓实测）

| 实测 | 视角 | 观测 | 结论 |
|---|---|---|---|
| stock 17 绿的谐波（无持仓+今日破位） | `view='empty'` | `signals_today` 融合真实 `sell:ma20_break@2026-09-21`；linkage"弱势阶段再出卖出信号→趋势走弱确认"；empty_rows=[回避（等放量站回 MA20 289.79）、等待信号]；**held_rows 同时输出**（止损 257.68/减仓检查/减仓引用今日信号）；top_action="回避·等放量站回 MA20（289.79）再确认" | ✅ |
| stock 4 恒瑞医药（900 股@49.37） | `view='held'` | top_action="止损·45.42"=max(49.37×0.92=45.42, 价格建议止损)（src=纪律/价格建议取高者）；held_rows 行序=止损→持有（站稳 MA20 45.16）→**减仓（条件式"若出现…"）**→仓位纪律（持仓 900 股·浮动亏 −6.8%·评级主指令）；empty_rows 并存 2 行；当日无信号 → signals_today 空（闸门正确） | ✅ |
| stock 21（**双账户分仓** 共 2100 股） | `view='held'` | qty=**2100**（两账户 SUM）、cost=**57.53**（加权）、止损 52.93=57.533×0.92 精确吻合——t3 的 LIMIT 1 多账户缺口修复在真实双账户数据上实证通过 | ✅ |

主从契约抽查：无信号时减仓/买入行全部条件式（"若出现…"），卖点×买入档相悖附"以评级为主"注记（行动清单买点×建议减仓调和话术实测在位）；文案无裸 `<`。`operations` 为增量键，`stage/playbook/disagreement/rating/total_score/disclaimer` 既有 7 键原样并存。

## 六、t2/t3/t4 验收点逐项核对

| 任务 | 验收点 | 结果 |
|---|---|---|
| t2 | SELL_SIGNAL_LIBRARY 5 信号 + 3 组 bear 共振纯函数，与买侧逐条件镜像 | ✅（合成实证 §四；平行库零污染） |
| t2 | 新端点 watchlist-sell-signals（side=sell），旧端点零改动 | ✅（§三，旧契约键集逐一比对） |
| t2 | 预警第 5 类 `sell_signal`：同步面（engine×3/blueprint/index.html 下拉/alerts.js 三 map/app.css 徽标）+ 种子幂等 | ✅（rule#15 已落库；前端 5 处 grep 逐一命中：index.html:84、alerts.js:12/107/114、app.css:622） |
| t2 | 评级上下文（相悖调和、detail 增量键） | ✅（测试 33 例全绿；行动清单侧话术实测在位） |
| t3 | `_gather_inputs`：账户无关聚合 + price_advice 零重算 + `_read_signals` 双侧离线复算 | ✅（§五 stock21 双账户实测；stock17/stock4 signals_today 融合实测） |
| t3 | `build_operations_matrix` 双视角行 + 价位三源 + 评级门控 | ✅（§五 held/empty 实测；止损双源取高实测） |
| t3 | `signal_stage_linkage` 阶段×信号解读 | ✅（弱势×卖信号、弱势×买点话术实测在位） |
| t3 | `generate_trader_advice` 增量键 operations、classify_stage/playbook 零改动 | ✅（7 既有键原样；既有 23 例 + 新 14 例全绿） |
| t3 | 看板 `_derive_trader_signal` 透传 top_action + 🎯chip | ✅ 代码与透传在位；`top_action` 值待新日报（§八观察项①） |
| t4 | 行动清单卖侧行（kind=sell_signal，priority=2）+ 持仓标记（held_map SUM 聚合） | ✅（实测 4 项；detail 字段齐全；本轮 8 只持仓无一命中卖点 → 持仓绿系徽标分支由测试锁定，实测数据路径为空仓分支） |
| t4 | 排序契约 卖出·持仓(0) < 买点(1) < 卖出·空仓(2) | ✅（实测买点组→空仓卖点组顺序正确；持仓分支 test_sort_contract 锁定） |
| t4 | 相悖反向调和（`_SELL_CONFLICT_RATINGS` 独立元组） | ✅（测试 24 例全绿；今日 4 只卖点股均无买入档评级 → 未触发 live 分支，属数据状态非缺陷） |
| t4 | stats 增 sell_hits/sell_resonance_hits + 前端三分支徽标 | ✅（stats 实测；portfolio.js:1534/1552-1557/1565 三分支+📍徽标+脚注逐一在位） |
| t4 | 结构向后兼容（新参带默认值、既有键零改名） | ✅（既有 15 例 action_list 测试原样绿；endpoint 响应既有键无删改） |

## 七、红线合规核验

| 红线 | 核验方式 | 结论 |
|---|---|---|
| **B24/R13** `advisor.generate_advice` | `git diff c23f9ee..HEAD -- modules/advisor.py tests/test_advisor.py` = 空；工作区 diff = 空（本批 22 个改动文件逐一列名核对，trader_advisor.py 为独立模块非 advisor.py） | ✅ 零触碰 |
| 平行库边界（2026-09-07 只产买点） | SIGNAL_LIBRARY/RESONANCE_LIBRARY/detect_signals/run_signal_chunk 零改动（测试锁定键集）；在线全市场扫描路径未接入卖侧 | ✅ |
| R7/D4 | 买卖双侧均只消费 rating 字符串做相悖比对，未重实现分数→评级映射；check_redlines R7 锚点 PASS | ✅ |
| V8 预警/新链路只读 | 卖点巡检/行动清单/操盘手矩阵均为 SELECT+纯函数；本次验收全程 GET+mode=ro 实证零写库（alert_history 行数在验收前后一致） | ✅ |
| R9/R10 | 零新表零迁移（alert_type 自由 TEXT；rule#15 幂等单行实测 COUNT=1）；daily_reports 仅增量键 | ✅ |
| 021W 多账户 | t3/t4 统一 SUM+加权聚合，无 JOIN 重复行；真实双账户数据实测 | ✅ |
| 021BN 裸 `<` | 矩阵/话术文字化；测试断言在位；实测文案无裸 `<` | ✅ |
| R16/R18/R19/R12/R17 | 未涉风控阈值/并发模式/调试模式/新依赖；redlines 28/28 | ✅ |

## 八、观察项与收尾事项（不阻塞，移交队长）

1. **看板 `top_action` 暂为 null（数据时序）**：看板读"最新日报 key_factors.trader"，而现存最新日报生成于今日 07:25–07:38（早于 021BQ 全部代码、且该批 key_factors 未带 trader 键——对比 09-21 日报带 `{stage_name,has_disagreement,disagreement_text}`，属批次路径差异，先于本批存在）。今晚 15:54 收盘批次（force=True 重算）后，新日报将带 `top_action`，🎯chip 即有动作摘要。**建议明日复查一眼看板。**
2. **sell_signal/tech_signal 告警历史均为 0 条**：`scan_once` 仅挂载在每日 15:54 收盘批次（daily_report.py:200），两类信号规则种子后（00:53 / 09:47:55）尚未经过任何一次评估窗口——非接线缺陷（33 例测试含 scan_once 幂等全链覆盖；种子/白名单/检查器注册实测在位）。**今日 15:54 批次应为首个生产触发窗口**（当前 4 只 stocks 满足"今日出现"口径），建议明日核对铃铛与 alert_history。
3. **t4 改动未提交**：modules/action_list.py、static/js/portfolio.js、tests/test_action_list.py、tests/test_routes.py 及三份实施报告仍在工作区（t2/t3 已提交）。功能已验证，提交收尾由队长执行。

## 九、结论

**通过（无保留）。**

- 四道门：全量 pytest 1010 passed/0 failed（含 slow 6 例）、ruff、mypy、红线 28/28 全绿；
- 双侧信号：卖点纯函数在合成下跌形态与真实下跌股上均正确触发、上涨负例零误报、买侧契约零回归、"今日出现"闸门真实数据验证；
- 操盘手矩阵：持仓/空仓双视角实测完整，价位三源、评级门控、联动解读、多账户聚合修复全部实证；
- 行动清单：卖点项+持仓标记字段+排序契约+相悖调和实测在位，前端徽标三分支就绪；
- 红线：B24 零触碰、平行库边界、V8 只读、021W 聚合、021BN 文案全部合规；
- 遗留仅 §八三项观察/收尾，均不构成本批验收障碍。
