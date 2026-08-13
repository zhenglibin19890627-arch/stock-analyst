# 开发自验报告 019Q — 资金面第三数据源（新浪主力口径）+ 延迟自动补采

**批次**：019Q（P2，数据可用性增强；东财挂停时当前仅有同花顺"全部资金口径"顶替，口径偏差大）
**角色**：开发工程师（单人，内嵌任务书窗口独立执行）
**自验日期**：2026-08-09
**任务书**：`docs/tasks/dev_tasks_20260809_019Q_sina_capital_fallback.md`（v2 定稿，M-1~M-13 已并入，监理已批准）
**架构评审**：`docs/reviews/review_019Q_sina_capital_fallback_20260809.md`（⚠️ 有条件通过，M-1~M-13 修订后定稿 v2）
**状态**：开发执行完成 + 自验通过（40/40 + 343 既有测试全过），待 QA 独立验收 → PM+QA 双签 → 监理批准关闭

---

## 一、改动清单（严格 4 文件，M-7 定稿）

| # | 文件 | 改动内容 | 位置 |
|---|---|---|---|
| 1 | `modules/data_collector.py` | ① 模块级 `_call_with_timeout(fn, label, timeout=_SINA_REQUEST_TIMEOUT=15)`（复制 019I 嵌套版模式提升模块级，新增 timeout 参数；既有 THS 嵌套版零改动）+ `_SINA_REQUEST_TIMEOUT` 常量（任务 1/M-3/M-10）② **新增 `_fetch_capital_flow_sina_main(symbol, market, target_date=None)`**：https 优先+http 回退 1 次、GBK 解码、UA+Referer、禁系统代理（urllib ProxyHandler({})）、全部网络调用走模块级 `_call_with_timeout`、每只请求后 sleep 0.5~1.0s、JSON 健壮性解析（R-8）、**严格 `opendate == target_date` 匹配（M-2 红线）**、写四档+主力不写占比（M-4/D-6）、金额走 `_safe_float_wan`、A股 daima 映射 6→sh / 0、3→sz（任务 1）③ `fetch_capital_flow` 降级阶梯：新浪 lscjfb 顶替块插在 THS 顶替块**之前**（D-1），写 `main=(r0+r1)/1e4` 四档、`is_estimated=0`、`capital_source='sina_main'`、status='fallback'，message 以"新浪"开头不误触 L2269 防覆盖（任务 2/5）④ 防覆盖 SQL 扩展 `NOT IN ('ths_total','sina_main')` 四处：前置跳过（L2240）、补采清单（L1774）、估算守卫三处（L2549/2587/2625）；EM 写入 NULL 不变；sina 顶替 UPDATE 无条件（M-5，任务 3）⑤ docstring 一致性修订（M-11，任务 7） | L17（import json）/ L1387-1408 / L2231-2332 / L2360-2372、L2644-2703 / L2394-2399、L1795-1801、L2771/2809/2847 / L2358-2376、L1658-1670 |
| 2 | `modules/daily_report.py` | 延迟自动补采（D-3 甲+乙融合，任务 5）：① 模块级 `_capital_retry_timer` ② `_schedule_capital_retry(a_symbols)`：工作日（周一~周五，019G 同型）才注册；**缺口 SQL 带 `(is_estimated=0 OR is_estimated IS NULL)` 条件（M-6 红线）**：`len(a_symbols) - COUNT(DISTINCT rc.stock_id) JOIN stocks WHERE symbol IN (...) AND capital_source IS NULL AND (is_estimated=0 OR NULL)`；缺口>0 → `threading.Timer(1800)` daemon=True 一次性 ③ `_capital_retry_once(a_symbols)`：`_generate_lock.acquire(timeout=5)` 拿不到即放弃（R-6）；拿到后调用 `fetch_capital_flow_batch`（019E 补采清单入口：sina_main 行被排除→幂等；ths_total 行在清单→升级机会）；回调内不注册下一次 ④ **注册点：`_scheduler_tick` 内 `generate_daily_report()` 返回后、`_schedule_next()` 前**（不注册在 generate_daily_report 内部，避免手动 API/force 重跑产生 30 分钟延迟副作用）⑤ `stop_scheduler` 防御性 cancel 未触发 Timer | L53 后 / L159-230 / L56-99 |
| 3 | `templates/index.html` | ① 资金面表头动态文案追加 `hasSinaMain` 判定 → "新浪顶替（主力口径）"（与 019K hasThsFallback 并列）② 行内 `sinaTag`="新浪"`<sup>` 标注（含在主力净流入单元格渲染）③ status 三元链 'fallback'→'⚠️顶替' **零改动**（019K 已映射，M-8） | L2500-2506 / L2514-2517 |
| 4 | `scripts/backfill_capital_sina_019q.py` | **新增**一次性历史回补脚本（D-5，先例 `scripts/b26_margin_backfill.py`）：用法 `python scripts/backfill_capital_sina_019q.py 2026-08-07 [--symbols 600519,300750]`；对每个 A 股自选股按 **EM push2his（按日期取）→ 新浪 lscjfb（按 opendate 严格匹配）** 阶梯写回（THS 无历史当日数据不参与）；EM 写沿用主链路 INSERT OR REPLACE + is_estimated=0 + capital_source=NULL 归位；sina 写 UPDATE + INSERT OR IGNORE + is_estimated=0 + capital_source='sina_main'；不改 app.py / 不入调度；幂等可重复运行 | 全文件 |

**其余文件零改动**：`database/db_manager.py`（capital_source 列 019K 已建，架构师 DB 实证存在）、`app.py` / `config.py` / `requirements.txt`、`advisor.py` / `analysis_engine.py` / `alert_engine.py` / `scoring_engine.py` / `data_adapter.py`（grep 实证零引用）；既有估算源 `_fetch_capital_flow_sina`（L2048，019E 链路）**零改动**（M-1 改名规避）。

---

## 二、验证环境与手段

- 解释器：`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`
- 自验脚本：`%TEMP%\opencode\qa_019q_selfcheck.py`（隔离临时 SQLite DB + mock 网络层，不污染生产库；40 项断言全文可复现）
- 真实网络验证：lscjfb 接口实测（探针复测：08-07 数据与 PM/架构师探针分毫不差）
- 回补脚本端到端：temp DB + 真实新浪网络（EM push2his mock 失败强制走新浪阶梯）

---

## 三、自验结果（40/40 全部 PASS）

| # | 场景 | 断言要点 | 结果 |
|---|---|---|---|
| E1-E2 | **模块级 `_call_with_timeout` 语义**（验收 1） | 快速函数返回 `(42, False)`；慢函数 0.05s 超时返回 `(None, True)` | ✅ |
| A1-A9 | **新浪顶替写入**（mock EM 三层全失败 + lscjfb 当日行，验收 3） | 返回 `('fallback', '新浪顶替(主力口径r0+r1；东财恢复后自动回补)')`；`capital_source='sina_main'`、`is_estimated=0`、仅写目标日期 1 行；`main=(r0+r1)/1e4=-11363.03`；自洽 `main==super+large`；四档和==netamount/1e4（容差 0.02）；**不写 `main_net_inflow_pct`（NULL）**；data_status='fallback' 且 message 以"新浪"开头 | ✅ |
| A2-1/2 | **https 失败 → http 回退**（M-9 ③，验收 2 补充） | 请求序列 `['https','http']` 且成功写入；回退仅 1 次 | ✅ |
| B1-1~3 | **lscjfb 无当日行 → 严格日期匹配不写入**（M-9 ①/M-2 红线） | mock 仅返回昨日行 → sina 返回 None 不写入 → 落回 THS 顶替（ths_total 行，main 保持 THS 值） | ✅ |
| B2-1/2 | **sina/THS 均失败 → 估算兜底**（链路不断） | 返回 'estimated'；估算行 is_estimated=1、main=123.45；既有估算源（L2048）回归正常 | ✅ |
| C1-1~3 | **EM 恢复可覆盖 sina 行**（验收 4/M-9 ⑤） | 前置跳过不阻塞 sina_main 行 → EM 重采覆盖 main=9999.0，`capital_source` 归位 NULL，`is_estimated=0` | ✅ |
| C2-1/2 | **估算不得覆盖 sina 行**（防覆盖） | 估算 UPDATE 守卫 NOT IN 生效 → sina_main 行保持原值；链路仍返回 estimated 不炸 | ✅ |
| C3 | **新浪可覆盖 THS 行**（口径更优） | ths_total 行被无条件 UPDATE 覆盖为 sina_main | ✅ |
| C4 | **新浪可覆盖估算行** | is_estimated=1 估算行被覆盖为 is_estimated=0 + sina_main | ✅ |
| D1/D1-2 | **延迟补采：缺口 SQL 带 is_estimated 条件**（M-6 红线/验收 6） | 估算行（capital_source=NULL + is_estimated=1）被正确计为缺口 → 注册 Timer；daemon=True、1800s 一次性 | ✅ |
| D2 | **无缺口不注册** | 全部真实数据（is_estimated=0 + capital_source NULL）→ 不注册 | ✅ |
| D3 | **非交易日不注册**（验收 6） | 周日（019G 同型判定）→ 不注册 | ✅ |
| D4/D5 | **任务体复用与并发防护**（R-6） | `_capital_retry_once` 调用 `fetch_capital_flow_batch` 并释放锁；拿不到 `_generate_lock` 即放弃本轮（不调用） | ✅ |
| D6/D6-2 | **注册点位置**（D-3） | `_scheduler_tick` 内含注册调用且位于 `_schedule_next()` 之前（静态断言）；不注册在 generate_daily_report 内部（代码审查确认） | ✅ |
| F1-F3 | **真实网络严格日期匹配**（验收 2/9 增补） | 周日当日 `target_date=None` → 无 08-09 行 → None（日期错位防护实证）；`target_date='2026-08-07'` → 命中且 main=-11363.03 与探针分毫不差；命中行自洽 | ✅ |
| G1-G3 | **回补脚本端到端**（D-5/验收 3） | temp DB + 真实新浪网络：估算行（2026-08-07）被写回为 sina_main、is_estimated=0、main=-11363.03 | ✅ |

**口径抽验登记**（验收 5，供监理知情）：600519 2026-08-07 新浪主力（r0+r1）=-11363.03 万元（r0=-5843.10，r1=-5519.93，r2=-131.91，r3=0）；与东财同源主力口径概念一致（仅分档阈值各家不同），不设硬阈值。

---

## 四、静态与回归验证

| 项 | 结果 |
|---|---|
| `python -m py_compile modules/data_collector.py modules/daily_report.py scripts/backfill_capital_sina_019q.py` | ✅ 无错误 |
| `python -m pytest tests/` | ✅ **343 passed**（1 warning 为 urllib3 版本提示，既有） |
| index.html 修改区 JS 语法（node --check 提取 `<script>` 块） | ✅ 无语法错误 |
| `ruff check` 三个改动文件 | ⚠️ 2 项**既有**告警：daily_report.py L30 import 排序（019A 遗留）、data_collector.py L1738 `turnover_yuan` 未使用（018 遗留）——均已核验不在本批次 diff 内，非本批次引入 |
| 模块导入冒烟 | ✅ `import modules.data_collector / modules.daily_report` 无异常 |
| 范围红线 | ✅ 本次会话仅编辑任务书列出的 4 文件（git diff 核验：既有估算源 `_fetch_capital_flow_sina` 函数体零改动；app.py/config.py/db_manager.py 等未触碰） |
| 零代码约束 | ✅ 仅新增标准库 json 顶层导入；无新 pip 依赖；无新表无新列 |

---

## 五、红线落实核对

| 红线 | 落实 |
|---|---|
| 命名冲突（M-1） | ✅ 新函数命名 `_fetch_capital_flow_sina_main`；既有估算源 `_fetch_capital_flow_sina`（L2048）零改动（自验 B2 回归实证） |
| 日期匹配（M-2） | ✅ `opendate == target_date` 严格匹配才写，不匹配返回 None 落回 THS；真实网络 F1 实证（周日无当日行→None，无日期错位污染） |
| 缺口统计（M-6） | ✅ 延迟补采缺口 SQL 附加 `(is_estimated=0 OR is_estimated IS NULL)`；D1 实证估算行计为缺口、D2 实证真实行不计缺口 |
| 超时红线（M-10） | ✅ 新浪全部网络调用（含 https 回退第二次请求）走模块级 `_call_with_timeout`（15s）；无裸网络调用 |
| 防覆盖红线（EM>新浪>THS>估算） | ✅ C1（EM 覆盖新浪归位 NULL）、C2（估算不覆盖新浪）、C3（新浪覆盖 THS）、C4（新浪覆盖估算）四向全实证；补采清单排除 sina_main（幂等）、保留 ths_total（升级机会） |
| 口径标注红线 | ✅ data_status message + 前端表头"新浪顶替（主力口径）" + 行内"新浪"标注三通道；不写 main_net_inflow_pct（M-4） |
| 超时/预算红线 | ✅ 29 只串行上限 ~29s，单只 15s+1s < STOCK_TIMEOUT=90s；延迟补采 30 分钟一次性与 24h 周期无交集 |
| 范围红线 | ✅ 仅 4 文件；db_manager/app/config/requirements/评分系五模块零改动 |
| 评分纯净红线 | ✅ sina/THS 顶替 is_estimated=0 参与评分（全链路标注）；估算 is_estimated=1 仅展示 |

---

## 六、开发备注

1. **R-4 双保险**：16:10 时点 lscjfb 当日行未发布 → 严格匹配返回 None → 落回 THS；+30min 延迟补采再试（任务体复用 019E 补采清单，sina_main 行幂等、ths_total 行可升级）。
2. **并发防护（R-6）**：`_capital_retry_once` 先 `_generate_lock.acquire(timeout=5)`，拿不到即放弃——手动批次本身含资金面采集，放弃无害。
3. **回补脚本语义**：EM push2his 命中则 INSERT OR REPLACE + capital_source=NULL（真实数据归位，与主链路 EM 写入一致）；EM 不命中则新浪阶梯 UPDATE + INSERT OR IGNORE + sina_main（可覆盖 THS/估算行，与主链路 sina 顶替一致）。回补后该日行参与后续 5 日均/连续性因子（019K D-3 已接受的"混用"范畴）。
4. **未做事项（交 QA）**：前端浏览器截图断言（验收 7）、交易日双源并存的 3 只口径对比（验收 5，需交易日后）、延迟补采 +30 分钟真实计时触发（验收 6，本自验以注册点+任务体隔离验证替代）。
