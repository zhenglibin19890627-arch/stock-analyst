# QA 验收报告：019P 基本面数据完善（毛利率补全 + 来源标注 + 趋势分析 + 港股占位行修复）

> 任务书：`docs/tasks/dev_tasks_20260805_019P_fundamental_enhance.md`（v2 定稿）
> 评审报告：`docs/reviews/review_019P_fundamental_enhance_20260805.md`（架构师 ⚠️ 有条件通过）
> QA 签发：2026-08-06 | 验收基线：当前工作树（00:06 git reset 后状态，PM 已核验）
> 参考（仅对照，不采信结论）：`reports/dev_selftest_019P_fundamental_enhance_20260805.md`

---

## 〇、验收方式与独立性声明

- 全部 mock 场景由 QA 独立构造（`%TEMP%\opencode\qa_019p_mock.py`，46 断言），**未复用** `.dev_019P_work/selftest_019P.py`，未触发任何真实网络（akshare 三个接口与腾讯估值接口全量 mock 替换）。
- 数据库全程使用临时库（`%TEMP%\qa019p_*\qa.db`），真实 `stock_analyst.db` 仅只读访问（mode=ro）。
- 验收结论仅依据本文档 V1~V11 的实测证据。

---

## 一、V1 代码级核查 — 全部 PASS

| # | 核查项 | 方法 | 证据 | 结论 |
|---|---|---|---|---|
| 1 | abstract 主源调 `ak.stock_financial_abstract` | Read | `data_collector.py` L589-600 `_fetch_a_fundamental_sina`：`_call_ak_with_timeout(lambda: ak.stock_financial_abstract(symbol=symbol), ...)`（L596），不挂 `@retry`（L593 注释） | ✅ |
| 2 | `data_source` 迁移存在（db_manager.py）+ 主库列存在 | Read + DB PRAGMA | `db_manager.py` L966-967 `('raw_fundamental', 'data_source', 'TEXT DEFAULT NULL')`；主库 `PRAGMA table_info(raw_fundamental)` 实测含 `data_source` 列 | ✅ |
| 3 | P1 ocf 保留实现 | Read | L823-837：REPLACE 前 `SELECT report_date, ocf_to_net_profit` 预读既有值；abstract 该期 ocf=None 且 DB 有值 → 保留原值 | ✅ |
| 4 | P2 降级实现（abstract 异常→现接口 + message 标注） | Read | L803-818：abstract 异常仅记日志，降级 `stock_financial_analysis_indicator`；message 前缀 `新浪指标(analysis_indicator降级)+腾讯估值`（L924/L935） | ✅ |
| 5 | P3 超时闭包 `_call_ak_with_timeout`（daemon 线程，不 import THS 闭包） | Read | L572-586：函数内 `import threading as _threading_019p`（L577，非 THS 的 `_call_with_timeout`）、`daemon=True`、`join(timeout)`、超时返回 `(None, True)`；模块常量 `_FUND_ABSTRACT_TIMEOUT = 30`（L545） | ✅ |
| 6 | 港股占位行修复（清理 + PE/PB 合并真实行） | Read | L1240-1267：财报写入成功后 DELETE 全指标 NULL 且 report_date 晚于最新真实财报行的占位行；L1287-1307：PE/PB 取"最新含指标值真实行"合并，无真实行才新建 | ✅ |
| 7 | TTL 完整性检查（最新期 gross_margin NULL → 不跳过） | Read | L723-753（A股）/ L1141-1177（港股）：TTL 门控内附加 `gross_margin IS NULL` 检查 → `backfill_triggered=True` 不跳过；message 区分 `财报补全(毛利率缺失触发)`（L928/L1332） | ✅ |
| 8 | 趋势四输出位置 | Read + grep | highlights：`advisor.py` L217-219（改善/平稳入，恶化不入）；risks：L413-415（恶化入）；`_pick_top_factors` 首位：L459-460；`_build_fundamental_factors` 追加：L1277-1284；前端因子卡：`index.html` L5188-5189（fund_trend 首位）、L5204（标签）、L5233/L5241（tooltip 口径说明） | ✅ |
| 9 | 范围隔离：019N 资金面函数未动 | grep | `_safe_num`(L1830)/`fetch_capital_flow`(L2206)/`_em_batch_collect`(L1509)/`fetch_capital_flow_batch`(L1637) 签名在位；L1350 之后区域 grep `019P` = **0 命中** | ✅ |
| 10 | B24 红线：generate_advice 未触碰 | grep | `advisor.py` 全部 019P 标记位于 L216/412/459/1080-1286；`generate_advice`（L1376-1549）区内 019P 标记 = 0 | ✅ |
| 11 | 趋势不进评分 | grep | `scoring_engine.py` grep `fund_trend` = **0 命中**；`config_weights.json` / `data_contract.py` 内容与 08-03 git 基线一致（git diff 为空） | ✅ |
| 12 | 零代码约束：无新依赖、无估算公式 | Read | 仅 `import math`（标准库，L18）+ akshare 内置接口（L596/609/1126）；港股 ocf 无对应字段时写 None 不引入估算（L1231 注释） | ✅ |

---

## 二、V2 编译验证 — PASS

```
python -m py_compile modules/data_collector.py modules/advisor.py database/db_manager.py
exit code = 0
```

---

## 三、V3 abstract 结构适配 + 毛利率补全（M-8①）— PASS（10/10）

**mock 组合**：`ak.stock_financial_abstract` 返回行=指标、列=`['选项','指标']+10 个报告期列（最新在前）`、`毛利率` 同名两行（常用指标=45.0 / 每股指标=44.0）、`净资产收益率(ROE)` 同名两行（常用指标=4.15 / 每股指标=3.00，每股组行在前）。

| 断言 | 实测证据 | 结论 |
|---|---|---|
| 写 8 期（非全历史 10 期截断） | 入库 8 行（2026-06-30 … 2024-09-30） | ✅ |
| report_date 格式 `YYYY-MM-DD` | 8/8 行匹配 `^\d{4}-\d{2}-\d{2}$` | ✅ |
| **gross_margin 8 期全非空**（核心修复） | `[45.0, 44.5, 44.0, 43.5, 43.0, 42.5, 42.0, 41.5]` 全非 None | ✅ |
| 同名去重（R-1）：'毛利率' 取常用指标组 | 最新行 gm=45.0（非每股指标 44.0） | ✅ |
| ROE=常用指标组口径（4.15 量级，0<v<100） | 最新行 roe=4.15，8 行值域全在 (0,100) | ✅ |
| data_source='sina_abstract' 全行标注 | 8/8 行 `data_source='sina_abstract'` | ✅ |
| PE/PB 合并到最新行 | 最新行（2026-06-30）pe=10.5、pb=1.2 | ✅ |
| 返回 message 来源标注 | `新浪abstract财报+腾讯估值: 基本面数据采集成功` | ✅ |

日志证据：`[A股 600276] abstract 解析 8 期财报（最新在前）`；`[A股 600276] 财报: 2026-06-30, ROE=4.15`；`[A股 600276] PE/PB 已合并到财报 2026-06-30`。

---

## 四、V4 P1 ocf 保留 — PASS（2/2）

**mock 组合**：临时库预置 `(stock_id=1, '2026-06-30', ocf_to_net_profit=0.3448)`；abstract 该期 `经营活动净现金/归属母公司的净利润=NaN`，其余 7 期 = 2.0。

| 断言 | 实测证据 | 结论 |
|---|---|---|
| ocf 保留 0.3448（不被 NaN 覆盖） | 2026-06-30 行 ocf=0.3448 | ✅ |
| abstract 有值期正常写入 | 2026-03-31 行 ocf=2.0 | ✅ |

日志证据：`[A股 600276] 2026-06-30 ocf 保留既有值 0.3448（abstract 该期为 NaN）`。

---

## 五、V5 P2 降级 + P3 超时（M-8②）— PASS（4/4）

### 场景 A：abstract 抛异常
**mock**：`ak.stock_financial_abstract` 抛 RuntimeError；现接口返回 2 期（2026-03-31/2025-12-31）。

| 断言 | 实测证据 | 结论 |
|---|---|---|
| 降级现接口写 2 期 | 入库 2 行 | ✅ |
| data_source='sina_analysis_indicator' | 2/2 行 | ✅ |
| message 含"降级" | `新浪指标(analysis_indicator降级)+腾讯估值: 基本面数据采集成功` | ✅ |

### 场景 B：abstract 挂起超时（注入超时 1s）
**mock**：`dc._call_ak_with_timeout.__defaults__ = (1,)`；abstract 挂起 sleep(5s)；现接口正常返回 2 期。

| 断言 | 实测证据 | 结论 |
|---|---|---|
| 返回 <2s 不挂死 | **elapsed = 1.06s**（join(1) 超时即返回） | ✅ |
| 超时后降级现接口 | 2 行 `data_source='sina_analysis_indicator'` | ✅ |

---

## 六、V6 TTL 三态（M-8③）— PASS（4/4）

| 场景 | 断言 | 实测证据 | 结论 |
|---|---|---|---|
| ① 最新期 gm=NULL（TTL 内，种子=2026-06-30 标准报告期，被 abstract 覆盖） | 不跳过 + '财报补全(毛利率缺失触发)' | 首次调用 message=`新浪abstract财报+腾讯估值: 财报补全(毛利率缺失触发)` | ✅ |
| ① 收敛性：回补后二次调用 | '同日跳过(财报80天TTL内+PE/PB 24h内)' | 二次调用 message 完全一致（abstract 已写 2026-06-30 行 gm 非空，data_status 24h 内） | ✅ |
| ② 最新期有值（TTL 内 + data_status 24h 内） | '同日跳过(财报80天TTL内+PE/PB 24h内)'（回归） | 实测一致 | ✅ |
| ③ 最新期 108 天前（有值） | 正常采集（回补标记不误触发） | 返回 `基本面数据采集成功`（不含"补全"） | ✅ |

> 注：① 的收敛性成立前提为"最新期日期为 abstract 覆盖的标准报告期"。真实库 21 只 A 股中 20 只满足（最新期为 2026-06-30/2026-03-31），1 只例外（见 Q-2）。

---

## 七、V7 趋势分析（M-8④，R-5 环比陷阱重点）— PASS（13/13）

**mock 组合**：Q1（2026-03-31 ROE=4.15）与年报（2025-12-31 ROE=14.26）混排 + 5 期毛利率/净利率/负债率 + 2025-03-31（Q1 同期，ROE=3.00）供同比。

| 断言 | 实测证据 | 结论 |
|---|---|---|
| **不输出 ROE 环比**（'ROE较上期' 不在明细） | 6 条明细中 0 条含"ROE较上期" | ✅ |
| ROE 同比正确（有同期数据时） | 明细含 `ROE同比改善(3.00%→4.15%)` | ✅ |
| 环比指标（毛利率/净利率/负债率）较上期 | 含 `毛利率较上期改善(43.50%→45.00%)`、`净利率较上期平稳(19.00%→19.20%)`、`负债率较上期改善(51.00%→50.00%)` | ✅ |
| 负债率下降=改善 | 见上行（lower_better=True，50.0<51.0 → 改善） | ✅ |
| 增速指标"加快/放缓"表述 | 含 `营收增速加快(12.00%→15.00%)`、`净利增速加快(6.00%→8.00%)` | ✅ |
| \|Δ\|<1pct → 平稳 | `净利率较上期平稳(19.00%→19.20%)`（Δ=0.2） | ✅ |
| 方向汇总 | `基本面较上期改善（…）`，direction='improve' | ✅ |
| 单期 → 兜底 | `历史数据不足，暂无趋势判断` + 'insufficient'（不崩溃） | ✅ |
| 无数据 → 兜底 | 同上 | ✅ |
| highlights 改善趋势入 / 恶化不入 | `_describe_dimension`：改善 → 输出 `基本面趋势:…`；恶化 → 不输出 | ✅ |
| risks 恶化入 / 改善不入 | `_detect_risks`：恶化 → `基本面趋势恶化：…`；改善 → 不输出 | ✅ |
| 因子卡首位 fund_trend | `_build_fundamental_factors` 产出 `factors['fund_trend']` | ✅ |
| `_pick_top_factors` 首位 | 返回键序 `['fund_trend', 'net_margin', 'gross_margin', 'fund_trend_detail']` | ✅ |

---

## 八、V8 港股占位行修复（M-8⑤）— PASS（6/6）

**mock 组合**：临时库预置占位行（report_date=今日，全指标 NULL，仅 pe/pb=99）+ 真实财报行（2025-12-31 全指标有值）；EM 接口返回 3 期（2026-06-30/2025-12-31/2025-06-30 最新在前）；腾讯估值 mock=(15.0, 2.0)。

| 断言 | 实测证据 | 结论 |
|---|---|---|
| 全指标 NULL 占位行清理（0 残留） | 清理后全指标 NULL 行 count=0（含今日占位行） | ✅ |
| 最新真实财报行可读、含全部指标 | 2026-06-30 行：roe=12.0/gm=42.0/nm=21.0/debt=46.0/rg=11.0/pg=13.0 全非空 | ✅ |
| PE/PB 合并到最新真实财报行 | 2026-06-30 行 pe=15.0、pb=2.0（占位行的 99 未残留） | ✅ |
| data_source='em_hk' | 2026-06-30 行 `data_source='em_hk'` | ✅ |
| message '港股EM财报+腾讯估值: …' | `港股EM财报+腾讯估值: 财报补全(毛利率缺失触发)` | ✅ |
| 收敛性：二次调用'同日跳过' | `同日跳过(港股财报37天内)` | ✅ |

---

## 九、V9 来源标注（M-8⑥）— PASS

| 断言 | 实测证据 | 结论 |
|---|---|---|
| data_source 迁移幂等（重复启动不报错） | 临时库 `init_database()` ×2 + `_migrate_columns()` ×2 无异常；主库列已存在（PRAGMA 实测） | ✅ |
| data_status message 前缀四态 | abstract：`新浪abstract财报+腾讯估值`；降级：`新浪指标(analysis_indicator降级)+腾讯估值`；港股：`港股EM财报+腾讯估值`；PE/PB 仅更新：`腾讯估值: PE/PB更新成功(财报跳过)`（代码级 L914-943）+ 回补态 `财报补全(毛利率缺失触发)` 实测 | ✅ |
| 前端动态表头 + 行级 sup 标注 | `index.html` L2472-2479：按 `data_source` 去重生成 `来源：新浪关键指标(abstract)…`/`来源：未标注` + `；估值：腾讯行情`；L2483-2489 行级 `<sup>`（新浪/新浪降级/东财）；grep 全部命中 | ✅ |

---

## 十、V10 评分纯净 + 零改动 — PASS

### 哈希比对（算法：SHA-256 前 16 位；00:06 事件将部分文件行尾 CRLF→LF，raw 哈希变化但**内容逐字节一致**——CRLF 还原后与 PM 基线完全相等）

| 文件 | 当前 raw（LF 化后） | CRLF 归一化后 | 019I-019N PM 基线 | 判定 |
|---|---|---|---|---|
| modules/scoring_engine.py | 32407E61A8471805 | **DD9DBFBBD005B35D** | DD9DBFBBD005B35D | ✅ 内容零改动 |
| config.py | 0A0DDD9EA1A397FD | **F6CE1F84B8DDACDA** | F6CE1F84B8DDACDA | ✅ 内容零改动 |
| requirements.txt | 822121CF1DA6DFA5 | **DBE076A7458C5788** | DBE076A7458C5788 | ✅ 内容零改动 |
| app.py | 5C73F6EA320D838D | 610A6537269CB735 | 5C73F6EA320D838D（019L 预期后） | ✅ raw 相等 |
| modules/data_adapter.py | 0792E5006D7DCED9 | EEE994C24F778583 | 0792E5006D7DCED9 | ✅ raw 相等 |
| modules/analysis_engine.py | DF71A6FE4FD7685D | 0D24C961B19F4B7F | DF71A6FE4FD7685D | ✅ raw 相等 |
| modules/alert_engine.py | 053F0CDB4DA62385 | 7255701745788304 | 053F0CDB4DA62385 | ✅ raw 相等 |
| modules/data_contract.py | 1497B109CB970FBD | BE92B8B222D9C23E | （历史 QA 以 git diff 核零改动） | ✅ git diff HEAD 为空 |
| config_weights.json | 9AA697FE39A51DF6 | 29464E0FC0DC1F7F | （同上） | ✅ git diff HEAD 为空 |
| modules/daily_report.py | 94C20A5CB7C78A7C | CD99A5DDAB3B00B5 | （019J 预期后，非本批次） | ✅ raw 与 019J 快照一致 |

补充证据：
- `scoring_engine.py` grep `fund_trend` = 0 命中（趋势不进评分）✅
- 资金面区（019N）函数体未动（V1-9）✅
- B24：`generate_advice` 区内无 019P 标记（V1-10）✅
- 回归：`python -m pytest tests/ -q` → **343 passed, 1 warning**（与 PM 基线一致）✅

---

## 十一、V11 真实库保护 — PASS

| 项 | 验收前 | 验收后 | 判定 |
|---|---|---|---|
| stock_analyst.db size | 7,241,728 字节 | 7,241,728 字节 | ✅ 不变 |
| stock_analyst.db mtime | 2026-08-05 23:58:37 | 2026-08-05 23:58:37 | ✅ 零写入 |
| daily_reports | 387 | 387 | ✅ 基线不变 |
| ratings_history | 315 | 315 | ✅ 基线不变 |
| raw_fundamental | 128 | 128 | ✅ 零写入 |
| backups/ 新文件 | 无 | 无（QA 期间未产生） | ✅ |
| 真实网络请求 | — | 0 次（akshare 3 接口 + 腾讯估值全量 mock） | ✅ |

---

## 十二、红线遵守核查清单

| 红线 | 核查方法 | 结论 |
|---|---|---|
| B24（不碰 generate_advice） | V1-10 + V10 | ✅ |
| 范围（4 文件 + 区域隔离） | V1（019N 区 0 个 019P 标记；零改动文件内容级比对） | ✅ |
| 超时红线（P3 自建闭包） | V1-5 + V5-B（1.06s 返回） | ✅ |
| ocf 保留红线（P1） | V4（0.3448 保留） | ✅ |
| 评分纯净红线（趋势不进评分） | V10（scoring_engine 0 命中 fund_trend + 哈希） | ✅ |
| 零代码约束 | V1-12（标准库 math + akshare 内置接口） | ✅ |
| 增量红线（TTL 门控不变） | V6（三态全通过，同日跳过 message 与 011 回归一致） | ✅ |
| 真实数据源红线（无估算公式） | V1-12（港股 ocf 写 None 留空不估算） | ✅ |
| ROE 口径迁移文档化 | V3（值域断言 0<v<100 而非精确值）+ 前端 tooltip L5233/5241 口径说明 | ✅ |

---

## 十三、新发现问题（登记，均不构成验收阻塞）

| # | 级别 | 说明 | 证据 |
|---|---|---|---|
| Q-1 | 信息 | **00:06 事件附带全文件行尾转换**（部分文件 CRLF→LF，如 scoring_engine/config/requirements/data_contract/config_weights），导致零改动文件 raw SHA-256 变化；CRLF 归一化后与 019I-019N PM 基线**逐字节相等**，判定为环境事件非代码改动。建议后续任务书注明"哈希比对须先行尾归一化"及取值时点 | V10 表 |
| Q-2 | 建议 | **A 股回补收敛性对"非标准报告期"最新行不成立**：真实库 stock 11 最新期 report_date=2026-07-15（非季末日，gm=NULL，含 roe/debt/pe/pb 部分值），不在 abstract 报告期覆盖内 → 每次 TTL 内采集重复触发完整回补（abstract 重采+8 期重写），但该行 gm 永不补全；其余 20 只 A 股最新期均为标准报告期（2026-06-30/03-31）可收敛。无数据损坏、无功能阻断，仅重复采集开销；源自存量数据，非 019P 引入（HK 已有 M-5 清理，A 股无同类清理） | 真实库只读查询（stock 11 行详情） |
| Q-3 | 信息 | `_call_ak_with_timeout` box 模式会吞掉线程内异常：abstract **立即抛异常**时（非超时），`box.get('r')`=None 走"数据为空"降级分支而非异常分支，异常文本仅打印 stderr 不落 warning 日志；行为等价（P2 降级仍触发），属可观测性小瑕疵 | T3 实测（stderr 出现 `Exception in thread` + RuntimeError） |
| Q-4 | 信息 | 任务书 V3 断言"写 8 期（非全历史）"以 8 期 mock 表述；QA 以 10 期 mock 实测截断为 8 期（更强验证），语义达标 | T1 |

---

## 十四、验收结论

# ✅ 通过

- **V1~V11 全部 PASS**：静态核查 12 项 / py_compile / mock 实测 46 断言（V3:10、V4:2、V5:4、V6:4、V7:13、V8:6、V9:2、V12:2、V11:3）/ 前端标注 grep / 哈希归一化比对 / pytest 343 passed。
- 核心风险点全部落证：V7 R-5 ROE 环比陷阱（明细 0 条 ROE 环比 + ROE 同比正确）、V3 R-1 同名去重（45.0 取常用指标组）+ R-2 最新在前列序、V8 R-3 占位行清理（0 残留）+ PE/PB 合真实行 + 收敛。
- 趋势仅展示不进评分（scoring_engine 0 命中 fund_trend + config_weights/data_contract 内容零改动）。
- 真实库零写入、零网络；387/315/128 基线不变。
- 新发现问题 Q-1~Q-4 均为信息/建议级，不构成阻塞，建议 PM 登记并转交架构师评估 Q-2（A 股非标准期行清理）与 Q-3（线程异常可观测性）。

---

**QA 签署**：QA（独立验收）｜2026-08-06

验收后清理：mock 脚本 `%TEMP%\opencode\qa_019p_mock.py`、`hashcheck.py`、`crlfcheck.py`、`headcmp.py`、`hash_final.py`、`db_baseline.py`、`db_after.py`、`db_check*.py`、`fix1.py` 及临时库 `%TEMP%\qa019p_*` 已全部删除，仓库内无残留临时文件。

---

## PM+QA 双签块（019P）

**双签日期**：2026-08-06

### PM 独立核验结论

**PM 独立复跑（2026-08-06，不采信 QA 结论）**：

| 核验项 | 方法 | 结果 |
|---|---|---|
| V1 代码级核查 | Read abstract 主源/P1/P2/P3/占位行修复/TTL 完整性检查/趋势四输出 | ✅ 与任务书 v2 一致 |
| V2 编译 | `python -m py_compile` 3 文件 | ✅ PASS |
| **Q-1 行尾转换独立确认**（PM 反向核验）| 当前文件 LF 行尾 → LF→CRLF 还原后哈希：scoring_engine=`DD9DBFBBD005B35D`、config=`F6CE1F84B8DDACDA`、requirements=`DBE076A7458C5788` **与 PM 基线逐字节匹配** | ✅ **00:06 事件=CRLF→LF 格式转换，内容零改动**（QA Q-1 结论成立）|
| **核心功能独立复跑**（PM 自建临时库 4 期 mock）| `_build_fund_trend`：毛利率较上期改善(43.50→45.00)、净利率/负债率平稳、**ROE 同比改善(3.00→4.15) 且无 ROE 环比（R-5 陷阱防护成立）**、营收/净利增速加快、direction=improve | ✅ PASS |
| 主库基线 | daily_reports=387 / ratings_history=315 / raw_fundamental=128 | ✅ 与 QA 报告一致，零写入 |

**PM 核验结论**：QA 报告结论与 PM 独立复跑方向一致。QA 46 断言基于独立构造的 mock（临时库 + 全网络 mock + 真实库只读），可信。Q-1（行尾转换）经 PM 反向核验**逐字节确认**非代码改动；Q-2（A 股非标准期回补收敛性，stock 11）为存量数据建议项；Q-3（线程异常可观测性）为小瑕疵。**PM 同意 QA 验收结论：通过。**

### 双签签署

| 角色 | 签署人 | 日期 | 结论 |
|---|---|---|---|
| QA | QA（独立验收） | 2026-08-06 | ✅ 通过（V1~V11 全 PASS，46 断言）|
| PM | PM（独立核验） | 2026-08-06 | ✅ 同意（独立复跑 6/6 项通过）|

### 关闭前提醒

1. **运行实例重启**：当前 5000 端口 app 为旧代码，019P 须重启（start.bat）后生效；存量 21 只 A 股毛利率经"完整性回补 + abstract 重采"自动补全（零代码），港股占位行自动清理
2. **Q-2（建议转架构师评估）**：A 股非标准报告期最新行（stock 11，2026-07-15）gm 永不补全 → 每次 TTL 内重复完整回补（无数据损坏，仅重复采集开销；源自存量非 019P 引入）
3. **Q-3（信息）**：`_call_ak_with_timeout` 线程内异常被 box 吞（行为等价 P2 降级，可观测性小瑕疵）
4. **登记**：港股备用源（无独立非 EM 源）、官方报告 cross-check（stock_financial_report_sina）后续候选；技术债：`_call_with_timeout`/`_call_ak_with_timeout` 公共化提取
5. **异常事件说明**：23:35 backups 出现 drop/delete 备份文件（空表快照，主库无损失，来源未明已登记）；00:06 git reset + CRLF→LF（内容零改动，Q-1 确认）

---

> **状态**：✅ QA 独立验收通过（2026-08-06）→ ✅ PM+QA 双签（2026-08-06）→ ✅ 监理批准关闭（2026-08-06）

---

## 关闭块（019P）

**监理批准关闭日期**：2026-08-06

**关闭结论**：✅ **019P 批次正式关闭**

| 流程节点 | 日期 | 状态 |
|---|---|---|
| PM 签发任务书 v1 | 2026-08-05 | ✅ |
| 架构师评审（有条件通过，M-1~M-10 并入 v2） | 2026-08-05 | ✅ |
| 监理批准 v2 | 2026-08-05 | ✅ |
| 开发执行 + 自验（75/75 断言 + 343 回归 + 真实联调） | 2026-08-05 | ✅ |
| QA 独立验收（V1~V11 全 PASS，46 断言） | 2026-08-06 | ✅ |
| PM+QA 双签 | 2026-08-06 | ✅ |
| 监理批准关闭 | 2026-08-06 | ✅ |

**关闭时遗留事项（登记，不阻塞关闭）**：
1. 运行实例重启：用户须重启 `python app.py`（start.bat）后 019P 生效；存量 21 只 A 股毛利率自动补全（完整性回补 + abstract 重采），港股占位行自动清理
2. Q-2（建议）：A 股非标准报告期最新行（stock 11，2026-07-15）gm 永不补全 → 每次 TTL 内重复完整回补——建议转架构师评估（源自存量数据，非 019P 引入）
3. Q-3（信息）：`_call_ak_with_timeout` 线程内异常可观测性小瑕疵（行为等价 P2 降级）
4. 登记候选：港股备用源（无独立非 EM 源）、官方报告 cross-check（stock_financial_report_sina）、`_call_with_timeout`/`_call_ak_with_timeout` 公共化提取
5. 异常事件存档：23:35 backups drop/delete 备份文件（空表快照，主库无损失，来源未明）；00:06 git reset + CRLF→LF（内容零改动，Q-1 确认）——后续批次哈希比对须先做行尾归一化

> **PM 签署**：019P 已按流程完成全部节点并经监理批准，正式关闭。归档完毕。
