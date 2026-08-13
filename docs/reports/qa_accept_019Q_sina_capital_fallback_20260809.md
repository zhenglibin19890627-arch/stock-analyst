# QA 独立验收报告 019Q — 资金面第三数据源（新浪主力口径）+ 延迟自动补采

**批次**：019Q（P2，数据可用性增强）
**验收角色**：QA 独立验收工程师（不采信开发自验结论，全部重跑实证）
**验收日期**：2026-08-09（周日，非交易日）
**任务书**：`docs/tasks/dev_tasks_20260809_019Q_sina_capital_fallback.md`（v2 定稿，验收标准 1~9）
**开发自验**：`docs/reports/dev_selftest_019Q_sina_capital_fallback_20260809.md`（第六节三项未做事项已补验：前端截图断言 ✅、延迟补采真实计时（替代证据）✅、交易日口径对比 ✅（用最近交易日 08-07 数据））
**架构评审**：`docs/reviews/review_019Q_sina_capital_fallback_20260809.md`（M-9 增补用例 ①~⑥ 全部覆盖）
**验收结论**：✅ **有条件通过**（附 2 项登记观察项，建议监理知情裁定，不阻塞收尾）

---

## 一、验收方法与隔离性声明

| 项 | 说明 |
|---|---|
| 数据库隔离 | 全部用例在 `%TEMP%\opencode\qa_019q_*.db` 隔离临时库执行（独立建表复刻生产 schema，含 `UNIQUE(stock_id, trade_date)` 与 `raw_kline`）；UI 验收用生产库**只读复制**；生产库 `stock_analyst.db` 全程零写入——验收后 mtime=2026-08-09 11:54（验收开始前）、大小 8,122,368B 不变，实证未污染 |
| 网络隔离 | 功能性用例全 mock（EM 三层/新浪/THS/估算源）；网络层专项用伪造 urllib 传输（`_urlreq` 桩）验证 https→http 序列；真实网络仅用于验收 5 双源抽验与回补探针（只读） |
| 时间隔离 | `datetime` 打桩固定周一 2026-08-10 模拟交易日；周日（真实 08-09）用于非交易日用例 |
| 独立断言 | 63（主链路）+ 7（回补脚本）+ 5（UI 浏览器）共 75 项断言全部独立编写复现，未复用开发自验脚本 |

**测试脚本（可全文复现）**：`%TEMP%\opencode\qa_019q_setup.py` / `qa_019q_main.py` / `qa_019q_backfill.py` / `qa_019q_probe.py` / `qa_019q_ui_seed.py` / `qa_019q_ui_run.py` / `qa_019q_ui_test.py` / `qa_019q_jscheck.js`

---

## 二、逐验收项结果（任务书第四节 1~9）

### 验收 1：代码级 ✅
| 断言点 | 实证 |
|---|---|
| 新浪采集走模块级 `_call_with_timeout`（15s） | ✅ 静态核验 L1397-1408（`_SINA_REQUEST_TIMEOUT=15` 默认值断言 `__defaults__==(15,)`）；E1 快速函数 `(42,False)`、E2 0.05s 超时 `(None,True)`；https 回退第二次请求同样走 `_call_with_timeout`（L2291-2297 核验） |
| UPDATE + INSERT OR IGNORE，严禁 REPLACE | ✅ 静态核验 L2652-2683（sina 块）；写入模式与 019K 同规格 |
| 仅写目标日期 1 行 | ✅ A 系列 3 只 `COUNT=1`；UNIQUE 约束下幂等 |
| 严格 `opendate==target_date` | ✅ N5（命中 08-07 且 trade_date 严格相等）、N7（仅 08-06 行→None）、C1/C2 主链路（无当日行→不写 sina→落 THS） |
| 防覆盖 SQL 四处落点（L2398/L1801/L2771/L2809/L2847） | ✅ 静态核验 5 处 `NOT IN ('ths_total','sina_main')` 全落地；EM 写入保持 NULL（L2466/L2532/L2594） |

### 验收 2：编译 ✅
- `python -m py_compile modules/data_collector.py modules/daily_report.py scripts/backfill_capital_sina_019q.py` → **无错误**
- `index.html` 内联脚本 node 语法检查（新 Function 编译 246,684 字符 script 块）→ **0 错误**

### 验收 3：功能（QA mock，EM 三层全失败）✅
A 系列 3 只（600519/000001/300750）抽验全部通过：
- `status='fallback'`、`capital_source='sina_main'`、`is_estimated=0`、仅写当日 1 行
- `main=(r0+r1)/1e4` 精确（600519 = -11363.03 万，与 PM/架构师探针分毫不差）
- `main_net_inflow_pct` 不写（NULL，M-4/D-6）✅
- data_status `'fallback'` 且 message 以"新浪"开头、不以"东方财富"开头（不误触 L2269 防覆盖）✅
- 新浪失败→THS（B1-B3，ths_total/1234.5/est=0）✅；全链失败→估算（D1，estimated/est=1）；全链全失败→failed+error_logs 不炸（D2）✅

### 验收 4：防覆盖四向 ✅（E 系列）
| 方向 | 断言 | 结果 |
|---|---|---|
| EM 恢复 → 覆盖 sina 行且归位 NULL | E1 main=9999.0（EM 元→万元）、`capital_source=NULL`、`is_estimated=0` | ✅ |
| EM 真实行存在 → 前置跳过（防降级覆盖） | E3 `'success' 同日跳过`，main 保持 9999.0 | ✅ |
| 估算 → 不得覆盖 sina 行 | E5 NOT IN 守卫生效，sina_main 行保持原值 | ✅ |
| 新浪 → 覆盖 THS 行 | E6 ths_total→sina_main（无条件 UPDATE，main=-150.0） | ✅ |
| 新浪 → 覆盖估算行 | E7 is_estimated=1→0 + sina_main | ✅ |
| 前置跳过不阻塞 sina_main 行（EM 恢复可重采） | E4 fallback 可重跑 | ✅ |

### 验收 5：口径抽验（交易日双源对比）✅（偏差登记，不设硬阈值）
08-09 为周日、当日无双源数据；**已用最近交易日 2026-08-07（周五）双源已发布数据完成 3 只对比**（满足"交易日双源并存"条件）：

| 代码 | EM 主力（万） | 新浪 r0+r1（万） | 偏差（万） | 新浪四档自洽 |
|---|---|---|---|---|
| 600519 | -11,606.26 | -11,363.03 | +243.23（≈2.1%） | ✅ main==super+large |
| 300750 | -77,739.60 | -10,194.12 | +67,545.48 | ✅ |
| 000001 | **EM 接口挂停**（RemoteDisconnected，直连+代理均失败） | -13,795.79 | 无法对比 | ✅ |

- 600519 新浪值与 PM/架构师探针完全一致（-11363.03 万），三方交叉印证 ✅
- 300750 同向但量级差异大（各家超大单/大单阈值定义不同，架构师 D-2 已裁定不设硬阈值，登记供监理知情）
- **000001 东财接口现场挂停（08-05 同款 RemoteDisconnected）——本批次立项场景的真实复现**，新浪源可用性现场实证

### 验收 6：延迟自动补采 ✅（+30min 真实计时受限登记）
- 缺口 SQL 带 is_estimated 条件（M-6）：F1 估算行（cs=NULL+est=1）计为缺口→注册；F3 真实数据无缺口→不注册；F4 周日（非交易日）→不注册
- 注册参数：F2 `Timer(1800)`、daemon=True、一次性（回调内不再注册，静态核验）
- 注册点：F10 静态断言 `_scheduler_tick` 内 `generate_daily_report()` 返回后、`_schedule_next()` 前（rfind 匹配真实调用行）；F11 不注册在 generate_daily_report 内部（避免手动 API/force 重跑产生 30 分钟副作用）
- 任务体：F5 调用 `fetch_capital_flow_batch`；F6 锁占用（timeout=5s 拿不到）→ 放弃本轮不调用（R-6）
- ths_total→sina_main 升级：F8 ✅；sina_main 行重跑数据幂等（不降级 THS/估算）：F9 ✅
- **⏳ 真实 +30 分钟计时未做（客观限制：单次验收会话不宜空等 30 分钟），按约束以"注册点（F10）+ 任务体（F5/F6）+ Timer(1800) 参数（F2）"证据链替代**，登记如上

### 验收 7：前端标注 ✅（截图断言，开发未做项补验）
playwright + Edge headless 真实浏览器 + Flask（临时库，端口 5099）：
- API 层：`/api/stocks/{id}/capital` 返回 `capital_source='sina_main'`、main=-11363.03、main_net_inflow_pct=null ✅
- 渲染层（DOM 文本断言，等价截图断言）：表头含 **"新浪顶替（主力口径）"** ✅、"来源：东方财富" ✅、行内含 **"新浪"** 标注 ✅、口径值 -11363.03 ✅
- 截图留档：`screenshots/qa_019q_sina_tag.png`（供监理人工查看）
- status 三元链 'fallback'→'⚠️顶替' 019K 已映射零改动（静态核验）

### 验收 8：零改动确认 ✅
- grep 全仓实证：`019Q/sina_main/hasSinaMain/sinaTag` 标识**仅存在于 4 个范围内文件**（data_collector.py、daily_report.py、templates/index.html、scripts/backfill_capital_sina_019q.py）；app.py/config.py/db_manager.py/advisor/analysis_engine/alert_engine/scoring_engine/data_adapter/requirements.txt 零 019Q 内容（注：git 基线仅 1 个初始提交，无法 diff 隔离批次差异，故以标识符全仓检索 + 关键函数体阅读作为替代证据）
- 既有估算源 `_fetch_capital_flow_sina`（L2074，hq.sinajs.cn 实时行情估算链路）：函数体完整未动（阅读核验），D1 估算兜底回归正常 ✅
- `python -m pytest tests/` → **343 passed**（1 个 urllib3 版本告警，既有）✅
- ruff：2 项既有告警（daily_report.py L30 import 排序=019A 遗留、data_collector.py L1738 `turnover_yuan` 未使用=018 遗留），均不在 019Q diff 内，与开发自验报告一致 ✅
- 生产库零写入实证（见第一节）✅

### 验收 9：M-9 增补用例全覆 ✅
| 用例 | 实证 | 结果 |
|---|---|---|
| ① lscjfb 无当日行→不写入、落回 THS/估算 | N7（函数级，仅 08-06 行→None）+ C1/C2（主链路落 THS） | ✅ |
| ② sina 行自洽 main==super+large、四档和==netamount | A 系列 3 只 + N3/N4（容差 0.02） | ✅ |
| ③ https 失败→http 回退 | N6 请求序列 `['https','http']` 且仅回退 1 次；N1 直连不回退 | ✅ |
| ④ 新浪顶替→THS→估算全链 fallback | B1-B3、D1、D2（链路不断） | ✅ |
| ⑤ 既有估算链路回归 | pytest 343 全过 + `_fetch_capital_flow_sina` 零改动 + B2/D1 估算源正常 | ✅ |
| ⑥ （评审增补）回补脚本端到端 | G1-G7（EM 写回归位 NULL/新浪阶梯写回/幂等/无当日行不写） | ✅ |

---

## 三、登记观察项（建议监理知情/裁定，不构成验收阻塞）

### 观察项 1（中低）：补采清单谓词语义与任务书 M-5 文字描述相反
- **实证**（F7/F7b）：`fetch_capital_flow_batch` 补采清单 SQL（data_collector.py L1801）按任务书 M-5 原样落地为 `NOT IN ('ths_total','sina_main')`，其语义为"sina_main 行**不**计入已有真实数据"→ sina_main 行**仍进入**补采清单（与 ths_total 行行为相同），而任务书文字称"sina_main 行被排除→幂等"。
- **后果评估**：+30min 任务体对 sina_main 行会再发起一次采集（每只 +1 新浪请求、约 1s）。数据无害：新浪失败则保持原值、成功则无条件 UPDATE 重写（F9 实证不降级）；**若东财已恢复，该行反而在 30 分钟内被真实数据覆盖回补**——与"东财恢复后自动回补"目标一致。
- **建议**：① 接受现状（数据安全，且加速回补）；② 或按 M-5 文字意图将补采清单谓词改为 `capital_source IS NULL OR capital_source NOT IN ('ths_total')`（sina_main 视为真实，杜绝重复请求）。请监理裁定；代码注释（L1787-1788）与 docstring（daily_report L177）同步修订以免误导。

### 观察项 2（登记）：验收 5 双源偏差 + 000001 东财现场挂停
- 600519 偏差 243.23 万（≈2.1%）、300750 偏差 67,545.48 万（同向），各家分档阈值定义不同所致，架构师 D-2 已裁定不设硬阈值——登记供监理知情。
- 验收当日 000001 东财 push2his 直连+代理双路径 RemoteDisconnected（08-05 同款故障），恰好现场实证本批次立项场景：新浪源独立可用（-13,795.79 万），降级链路生效价值成立。

---

## 四、受限项登记（任务书约束允许的替代证据）

| 受限项 | 任务书约束 | 本次处理 |
|---|---|---|
| 验收 5 交易日双源对比 | "需等交易日" | 08-09 周日当日无双源数据；**已用最近交易日 08-07（周五）双源已发布数据完成 3 只对比**（满足双源并存条件），无需顺延 |
| 验收 6 +30min 真实计时 | "若耗时过长可用注册点+任务体证据替代并在报告中登记" | 以注册点静态断言（F10/F11）+ 任务体隔离验证（F5/F6）+ Timer(1800) 参数（F2）证据链替代，已登记。建议下个交易日 16:10 批次后由 PM 抽查日志确认真实触发（`[资金面补采] 检测到 N/29 只缺口，30分钟后自动补采` 与 `[资金面补采] 延迟补采开始`） |

---

## 五、红线核对（独立核验）

| 红线 | 结论 |
|---|---|
| 命名冲突（M-1）：`_fetch_capital_flow_sina_main` 命名规避，既有估算源零改动 | ✅ |
| 日期匹配（M-2）：严格 `opendate==target_date`，严禁取最新行 | ✅ N5/N7/C1/C2 实证 |
| 缺口统计（M-6）：`(is_estimated=0 OR IS NULL)` 附加 | ✅ F1/F3 实证 |
| 超时红线（M-10）：全部新浪网络调用（含 https 回退第 2 次）走模块级 `_call_with_timeout` 15s | ✅ 静态 + E1-E3 |
| 防覆盖红线：EM>新浪>THS>估算 | ✅ E1-E7 四向实证 |
| 口径标注红线：data_status + 表头 + 行内三通道 | ✅ A/B 系列 + 验收 7 |
| 零代码红线：无新 pip 依赖（仅 stdlib json 顶层导入）、无新表无新列 | ✅ |
| 范围红线：仅 4 文件 | ✅ grep 全仓实证 |

---

## 六、验收结论与建议

1. **验收标准 1~9 全部满足**（75 项独立断言 + 343 既有回归全过），开发自验第六节三项未做事项已全部补验完成。
2. **有条件通过**：观察项 1（补采清单谓词语义 vs M-5 文字描述）建议监理在收尾时裁定"接受现状"或"调整谓词+修订注释"；观察项 2 为知情登记。二者均不构成数据正确性缺陷。
3. **遗留建议**：下个交易日由 PM/运维按第四节提示抽查 16:40 延迟补采日志，完成真实计时闭环登记。

**QA 验收签名**：本报告由 QA 独立完成，未采信开发自验结论；隔离临时库 + mock + 真实网络探针 + 真实浏览器渲染全流程实证。

（证据文件：`%TEMP%\opencode\qa_019q_*.py`、`screenshots/qa_019q_sina_tag.png`）
