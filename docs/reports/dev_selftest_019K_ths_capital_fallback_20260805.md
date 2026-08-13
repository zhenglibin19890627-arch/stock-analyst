# 开发自验报告 019K — 东财全失败时同花顺（THS）真实资金数据顶替主力净流入（方案一）

**批次**：019K（P2，数据可用性增强；东财挂停时资金面真实数据缺失，靠估算兜底仅展示）
**角色**：开发工程师（单人，内嵌任务书窗口独立执行）
**自验日期**：2026-08-05
**任务书**：`docs/tasks/dev_tasks_20260805_019K_ths_capital_fallback.md`（v2 定稿，M-1~M-12 已并入）
**架构评审**：`docs/reviews/review_019K_ths_capital_fallback_20260805.md`（⚠️ 有条件通过，M-1~M-12 修订后定稿 v2）
**状态**：开发执行完成 + 自验通过（42/42），待 QA 独立验收 → PM+QA 双签 → 监理批准关闭

---

## 一、改动清单（严格 3 文件）

| # | 文件 | 改动内容 | 位置 |
|---|---|---|---|
| 1 | `database/db_manager.py` | 迁移列表追加 `raw_capital_flow.capital_source TEXT DEFAULT NULL`（走 `_safe_add_columns` 幂等自动迁移） | `_migrate_columns` 迁移列表 L964 后（018/019E 先例同位置） |
| 2 | `modules/data_collector.py` | ① docstring 修订（Task 5）② 补采清单 SQL 排除 ths_total（Task 3②）③ 前置跳过 SQL 排除 ths_total + 注释修订（Task 3①/5）④ EM 三层写入显式 capital_source=NULL（Task 3③）⑤ THS 顶替写入逻辑（Task 2）⑥ 估算三处 UPDATE 来源守卫（Task 3④） | L1370-1373 / L1501-1504 / L1920-1933 / L1990-2003、L2043-2056、L2094-2107 / L2140-2186 / L2203-2205、L2243-2245、L2281-2283 |
| 3 | `templates/index.html` | ① 资金面表头动态来源标注（含"同花顺顶替（全部资金口径）"）② 资金面表格行内"同花顺"`<sup>` 标注 ③ 两处状态三元链增加 `'fallback' → '⚠️顶替'`（状态表格 + 采集结果卡片，复用 status-partial CSS 类） | L2482-2489 / L2497-2500 / L2549-2550、L2068-2076 |

**其余文件零改动**（advisor/analysis_engine/alert_engine/scoring_engine/data_adapter/app.py/daily_report/config.py/requirements.txt）。

---

## 二、验证环境与手段

- 解释器：`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`
- 自验脚本：`scripts 外临时目录 .dev_019K_work/selftest_019K.py`（隔离临时 SQLite DB + mock 网络层，不入库不污染生产库；全文可复现）
- 真实库：`stock_analyst.db`（仅执行了一次 `init_database()` 启动迁移，新增 capital_source 列，无任何业务数据写入）

---

## 三、自验结果（42/42 全部 PASS）

| # | 场景 | 断言要点 | 结果 |
|---|---|---|---|
| J1 | 迁移 | `init_database()` 后 `capital_source` 列存在 | ✅ |
| J2 | 迁移幂等 | 二次执行无异常、列仍在 | ✅ |
| A1-A7 | **THS 顶替路径**（mock EM 三层全失败 + 库内 ths_net_inflow=-11800） | 返回 `('fallback', '同花顺顶替(全部资金口径，非主力；东财恢复后自动回补)')`；`main_net_inflow=-11800`、`is_estimated=0`、`capital_source='ths_total'`；`data_status.status='fallback'` 且 message 含"同花顺顶替/全部资金口径"；仅当日 1 行 | ✅ |
| B1-B6 | 顶替写字段纯净 | 不写 `main_net_inflow_pct / super_large_net / large_net / medium_net / small_net`；`ths_net_inflow` 保留 | ✅ |
| C1-C4 | EM 成功正常路径零干扰 | 返回 success；EM 值写入；`capital_source` 保持 NULL；顶替未触发 | ✅ |
| D1-D5 | **EM 恢复回补**（防覆盖闭环） | 前置跳过不阻塞 ths_total 行 → EM 重采成功覆盖 `main_net_inflow=12345.67`、`capital_source` 归位 NULL、`is_estimated=0`；记录 R-7 既有行为（EM INSERT OR REPLACE 清空 ths_net_inflow） | ✅ |
| E1-E5 | THS 为 NULL 落回估算兜底 | 返回 `('estimated', ...)`；`is_estimated=1`、`capital_source` NULL；status=estimated | ✅ |
| F1-F3 | **估算 UPDATE 来源守卫** | ths_total 行 UPDATE rowcount=0（禁止覆盖）；对照 NULL 来源行 rowcount=1（可覆盖） | ✅ |
| G1-G2 | **补采清单排除** | 补采 SQL 不命中 ths_total 行（应进补采）；命中 EM 真实行（排除出补采） | ✅ |
| H1-H4 | **状态消费方**（`_em_batch_collect` L1327） | 'fallback' 计 fail_count=1、success_count=0、熔断计数不重置（1）；对照 'success' 计成功并重置计数 | ✅ |
| I1-I4 | **评分进入** | `data_adapter._read_capital_data` 读到顶替行（is_estimated=0 过滤放行）；`score_main_capital` 以 -11800 计分 20.0（大幅净流出，非缺失中性 85），detail 含 -11800.00；对照缺失时 85 | ✅ |

---

## 四、静态与回归验证

| 项 | 结果 |
|---|---|
| `python -m py_compile modules/data_collector.py database/db_manager.py` | ✅ 无错误 |
| `python -m pytest tests/` | ✅ **310 passed**（1 warning 为 urllib3 版本提示，既有） |
| index.html 修改区 JS 语法（node --check 提取片段） | ✅ 无语法错误 |
| `ruff check` 两改动文件 | ⚠️ 1 项既有告警：L1442 `turnover_yuan` 未使用（019K 未触碰该行，原 018 遗留，不在本批次范围） |
| 范围外文件 | ✅ 未修改（git status 变动集与开工前一致，无新增/修改） |

---

## 五、红线落实核对

| 红线 | 落实 |
|---|---|
| 功能红线：EM 全失败资金面因子不缺失 | ✅ I 场景：顶替真实数据参与 v5 main_capital 评分 |
| 口径红线：全链路标注 + 监理知情 + 可选纯净开关 | ✅ data_status message 标注"全部资金口径，非主力"；前端表头/行内双标注；开关=4 处 WHERE 单行修改（评审 D-2 附注，本批次默认参与评分） |
| 来源标注红线 | ✅ 表头动态文案 + 行内"同花顺"`<sup>` + 状态映射"⚠️顶替" |
| EM 回补红线 | ✅ 前置跳过（Task 3①）+ 补采清单（Task 3②）均排除 ths_total；D 场景实证可回补且归位 NULL |
| 状态消费红线 | ✅ 前端两处三元链（L2550/L2073）+ `_em_batch_collect` L1327 语义天然正确（H 场景实证） |
| 范围红线 | ✅ 仅 3 文件 + 迁移列 |
| 零代码约束 | ✅ 无新 pip 依赖；config.py 未碰；schema 变更仅新增列走自动迁移 |
| 防覆盖红线 | ✅ 估算不得覆盖 THS（F 场景）；THS 不得覆盖 EM（仅 em_all_failed 触发，C 场景）；EM 必须能覆盖 THS（D 场景）；THS 可覆盖估算（UPDATE/INSERT OR IGNORE 语义，A/E 场景） |
| 评分纯净红线 | ✅ is_estimated=1 估算行永不进评分；过滤语义未破坏（无第三档值） |
| 超时红线 | ✅ 顶替链路仅读库零网络调用；无新增裸网络调用 |

---

## 六、开发备注

1. **实现规格与任务书代码差异**（1 处必要修正）：任务书示例代码 `cur = get_connection().cursor()` 后再 `conn.commit(); conn.close()` 中 `conn` 未绑定（示例笔误）；开发实现改为 `conn = get_connection()` + `cur = conn.cursor()` + `conn.commit(); conn.close()`，语义一致（有占位行 UPDATE / 无占位行 INSERT OR IGNORE）。
2. **DB 现状**：真实库已随启动迁移新增 `capital_source` 列（验收标准 1 要求"DB 实测列存在"）；无任何测试数据写入生产库。
3. **R-7 登记**：EM 恢复覆盖时 ths_net_inflow 被 INSERT OR REPLACE 清空（既有 018 行为，评审接受登记），自验 D5 记录实证。
4. **既有告警**：ruff L1442 `turnover_yuan` 未使用为 018 遗留，非本批次引入，未处理（一次变更一个缺陷面）。
5. **自验脚本复现**：脚本位于自验临时目录 `.dev_019K_work/selftest_019K.py`（工作区根），QA 可直接运行复现（输出 42 PASS）；脚本使用隔离临时 DB + mock 网络层，不碰生产数据。

---

**开发自验签名**：开发工程师，2026-08-05。以上自验在隔离环境完成，未执行正式验收（由 QA 独立执行）。
