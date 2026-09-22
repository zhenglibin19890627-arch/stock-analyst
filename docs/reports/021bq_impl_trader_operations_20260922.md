# 021BQ t3 实施报告：操盘手建议全面化——双视角操作矩阵 + 短线信号融合

> 日期：2026-09-22 ｜ 实施：builder（任务 t3）｜ 依据：`docs/reports/021bq_sell_side_plan_20260921.md` 项E + 任务书要点①~⑥
> 验收：**fast 995 passed** + ruff 全绿 + mypy 55 文件 0 错 + **红线 28/28** + node --check ×2（全程不触网）

---

## 一、交付概览

| 层 | 交付物 | 说明 |
|---|---|---|
| 输入层 | `trader_advisor._gather_inputs` 三项升级 | ①持仓读数改**账户无关聚合**（SUM(quantity)+加权成本，修复 `ORDER BY id LIMIT 1` 多账户缺口，021W 约定）；②新增 `price_advice` 读取（daily_reports 已存 JSON，零重算）；③新增 `_read_signals` 短线信号层（复用 t2 卖侧平行纯函数，买卖双侧离线复算，失败静默降级） |
| 矩阵层 | 新纯函数 `build_operations_matrix(...)` | 持仓/空仓**双视角行同时输出**（view 标注当前视角）；每行=触发条件→动作（价位+来源+相悖调和注记）；价位三源：成本×0.92 纪律线、MA20、价格建议止损/买入区间 |
| 联动层 | 新纯函数 `signal_stage_linkage(...)` | 阶段×信号白话解读：弱势+买点=超跌反弹（反抽减仓/不接飞刀）、上升+卖点=趋势内回调、底部+买点=启动前兆、震荡=区间噪音；信号×评级相悖显式调和 |
| 输出层 | `generate_trader_advice` 增量键 `operations` | 既有 7 键（stage/capital/playbook/disagreement/rating/total_score/disclaimer）**结构零改动**；矩阵构造异常双层静默降级不阻塞主链路 |
| 日报层 | `daily_report` key_factors['trader'] 增 `top_action` | 当前视角首行动作摘要（如"止损·12.88"）；R9 合规（仅增量键） |
| 看板层 | `_derive_trader_signal` 透传 `top_action` + portfolio.js chip | 无分歧时中性 🎯chip 展示动作摘要；有分歧时并入 ⚡chip 提示 |
| 前端层 | analysis.js `loadTraderAdvice` 新增「④ 操作矩阵」段 | 今日信号 chips（▲买红/▼卖绿）+ 📡联动解读行 + 持仓者/空仓者两组动作行（动作词配色沿用看板 ACTION_COLOR 惯例：红=买、绿=风控、蓝=持有、橙=关注、灰=观望） |
| 测试 | test_trader_advisor.py 23→**37 例** | 双视角/主从契约/评级门控/多账户聚合/端到端信号融合/看板透传 |

## 二、操作矩阵结构（`operations` 键契约，测试锁定）

```
operations: {
  view: 'held'|'empty',                    # 当前视角（holding_qty>0 → held）
  holding: {qty, cost, pnl_pct},           # 聚合持仓（多账户 SUM+加权）
  signals_today: [{signal,label,side,trigger_date}],   # 买卖双侧今日事件
  linkage: ['阶段×信号联动解读', ...],       # 含评级相悖调和行
  held_rows:  [{action,trigger,level,level_value,source,note}, ...],
  empty_rows: [...],
  top_action: '止损·12.88',                 # 当前视角首行摘要（看板增量键）
}
```

**持仓视角行序**（确定性）：止损（双源取高者）→ 持有/减仓检查（MA20 状态）→ 减仓（今日事件引用具体信号与日期 / 窗口共振≥4★ / 无信号时条件触发式）→ 仓位纪律（持仓·成本·浮动盈亏，评级主指令标注）。
**空仓视角行序**：回避（卖出事件/共振）/ 观望（基线）→ 买入触发（评级门控：买入档=支持、持有观望=小仓试错、减仓档=仅观察+相悖注记）→ 等待信号（基线）。

**主从契约**（B24 同源精神，测试锁死）：
- 全部为「条件→动作」式，无信号时减仓行走 `若出现…→减仓检查` 条件式；
- 卖出信号×买入档评级 → 减仓行 note 与 linkage 行均附「卖出信号与评级「X」相悖——评级是动作主指令，信号仅波段参考，以评级为主」（与行动清单 021BP c23f9ee `_CONFLICT_RATINGS` 同思路、方向相反，独立元组勿复用）；
- 买点信号×减仓档评级 → 动作降级为「试仓观察」+「仅观察不买入」注记，绝不输出无条件买入。

## 三、多账户持仓聚合修复（captain 裁定项 3）

```sql
SELECT SUM(quantity) AS total_qty,
       CASE WHEN SUM(quantity) > 0 THEN SUM(quantity*cost_price)/SUM(quantity) END AS avg_cost
FROM holdings WHERE stock_id = ? AND quantity > 0
```
- 账户无关聚合，无 JOIN 无重复行（021W 多行约定天然满足）；
- 修复前：同股双账户分仓（600@10 + 400@20）只读到 600@10；修复后 playbook profile 正确输出「持仓 1,000 股 · 成本 14.00」，止损位按加权成本 14.00×0.92=12.88 计算（测试 `test_multi_account_holding_aggregated` 锁定）。

## 四、红线自检

| 红线 | 结论 |
|---|---|
| B24/R13 generate_advice | advisor.py 零触碰；trader_advisor 为独立只读后处理模块（扩展全在其内部+消费方增量键） |
| R7/D4 评级映射 | 只消费 rating 字符串做门控/相悖比对（_RATING_STRONG/_RATING_WEAK 既有常量），不重映射分数→评级 |
| R9 日报不变量 | key_factors 仅增 `top_action` 可选键；`_save_report`/markdown 写入路径零触碰 |
| R8 数据源解耦 | 信号层输入=库内K线（market_screener 平行纯函数），价位层=已存 price_advice JSON，零新数据源 |
| V8 只读 | `_read_signals` 只读 raw_kline/raw_kline_weekly；全程不写评分/评级/候选表 |
| 主从契约（用户拍板） | classify_stage/playbook/disagreement 判定逻辑零改动（021BP 测试 23 例原样全绿）；矩阵永不输出与评级相反的无条件指令（测试断言 `trigger.startswith('若')` 等） |
| 021BN 裸 `<` | 矩阵文案全部文字化；测试断言 `'<' not in json.dumps(ops)`；前端统一 `_taEsc` 转义 |
| 021W 多账户 | 持仓查询改 SUM+GROUP BY 账户无关聚合（本轮修复） |
| R16/R18/R19/R12/R17 | 未涉风控阈值/并发超时/调试模式/新依赖 |

## 五、验证记录

```
python -m pytest tests/                 → 995 passed, 1 skipped（fast 层，56s）
ruff check .                            → All checks passed!
python -m mypy app.py config.py modules → Success: no issues in 55 source files
python scripts/check_redlines.py        → 28/28 通过
node --check static/js/analysis.js      → OK
node --check static/js/portfolio.js     → OK
```

改动文件：modules/trader_advisor.py ｜ modules/daily_report.py（top_action 增量键）｜ blueprints/portfolio/watchlist_scores.py（top_action 透传）｜ static/js/analysis.js（④ 操作矩阵段）｜ static/js/portfolio.js（🎯chip）｜ tests/test_trader_advisor.py（+14 例）

## 六、遗留与建议

1. **前端 dashboard 之外的新键消费**：`operations.full` 详细行仅在个股页操盘手卡渲染；报告页 markdown 未展开矩阵（key_factors 只有 top_action 摘要，如需 md 全文展开另立批次）。
2. 矩阵价位层当前取「最新日报 price_advice」——日报未生成的股票仅有成本×0.92 纪律线（降级路径已测）。
3. `signal_stage_linkage` 的震荡期语义为按区间执行；若后续要接区间上下沿（低60/高60）动态价位，可在 vs 参数上扩展（已预留）。
