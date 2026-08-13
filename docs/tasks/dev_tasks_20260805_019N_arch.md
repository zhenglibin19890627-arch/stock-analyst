# DEV-TASKS-20260805-019N-ARCH：019N EM 资金流 NaN 防护与假成功修正 — 架构方案评审任务书

> **签发人**：PM  | **签发日期**：2026-08-05 | **状态**：待架构师执行

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：架构师

**职责边界**：
- 复核 PM 签发的 019N 开发任务书（`docs/tasks/dev_tasks_20260805_019N_em_nan_fix.md`）
- 对每个决策点（A-1~A-7）给出明确裁定 + 理由
- **不编码、不验收、不写功能代码**
- 交付物：`docs/reviews/review_019N_em_nan_fix_20260805.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 架构师仅做方案评审，不执行任何代码修改
- PM 产出的任务书仅供参考，架构师须独立 Read 代码核验，不采信 PM 结论

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包） |
| 前序批次 | 019E/019F/019G/019H/019I/019J/019K 均 ✅ 关闭；019N 为数据完整性修复 |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | 架构方案评审（只读不改，不写功能代码） |
| 交付物 | `docs/reviews/review_019N_em_nan_fix_20260805.md` |

---

## 一、需求背景

### 1.1 缺陷描述（2026-08-05 DB 实证）

美的集团（id=11）等 9 只股票 02-27~08-04 共 108 行 main_net_inflow **全 NULL**（仅 08-05 有值）。全库 `main NULL AND margin 有值` 共 1512 行。08-04 14:18:15 日志却显示"000333 获取到 120 天资金流向历史数据"+"资金面保存成功: 120天历史数据"——**假成功**。

### 1.2 根因链（PM 独立实验实锤）

```
EM 返回"主力净流入-净额"= NaN
  → L1983: round(float(NaN or 0) / 1e4, 2) = nan（不抛异常，PM 实验实证）
  → SQLite 存 NaN 自动变 NULL（PM 实验实证）
  → saved_count 仍计数 → 日志假成功
  → data_status 记"东方财富...成功" → 防覆盖锁定（L1953-1960）
  → NULL 永久滞留，真实数据无法回补
```

### 1.3 关键代码位置（评审必读，请独立 Read 核验）

| 位置 | 说明 |
|---|---|
| `modules/data_collector.py` L1976-2012 | **Layer 1**：EM push2his 历史写入（六字段转换 L1983-1988 + saved_count + data_status） |
| `modules/data_collector.py` L2024-2062 | **Layer 2**：EM push2 实时写入（五字段转换 L2029-2033） |
| `modules/data_collector.py` L2078-2114 | **Layer 3**：akshare 备用写入（六字段转换 L2078-2083） |
| `modules/data_collector.py` L1925-1936 | **pre-check**：查库判断 main 非空（NULL 行不满足 → 不跳过 → 可重采）— 存量修复关键 |
| `modules/data_collector.py` L1944-1967 | **防覆盖**：查 data_status message 开头"东方财富"（假成功会锁定） |
| `modules/data_collector.py` L1537-1553 | `_parse_cn_amount` — 已有 None/空/NaN 防护（L1545），可作参考写法 |
| `modules/data_collector.py` L2123-2188 | `em_all_failed` → THS 顶替（019K）→ 估算兜底（019E）— 降级链路 |
| `database/db_manager.py` L965 | capital_source 迁移列（019K，本批次不碰） |

---

## 二、评审决策点（请逐项裁定）

### A-1：安全转换函数设计（核心）

PM 草案：新增 `_safe_float_wan(val)` / `_safe_float_pct(val)` 辅助函数，None/空串/NaN 返回 None。

**架构师请核验**：
- Read L1976-2114 三层转换代码，确认六字段转换点数量与位置
- 裁定：函数签名/返回值设计（None 语义）；是否复用 L1545 判断模式；模块级 vs 函数内定义
- 注意：`float('nan') or 0` 的 truthy 陷阱（NaN or 0 仍是 NaN）——现有 `or 0` 写法对 NaN 无效，需显式 isna 判断
- 是否可用 `math.isnan`（标准库）还是 pandas `pd.isna`（已在依赖中）

**裁定**：采纳/修改/否决 + 理由

### A-2：NaN 行的处理粒度（核心）

**背景**：EM 返回 NaN 时，该行（交易日）数据异常。

**架构师请核验**：
- 裁定三选一：① 六字段全 NaN → 跳过该行（不写占位），部分 NaN → 置 None 保留有效值；② 任何字段 NaN → 跳过该行；③ 其他
- 影响评估：skip 后当日无行 vs NULL 行，对评分读取（data_adapter L385-387 取 latest）、5 日均/连续性因子（advisor L1141-1157）、THS 顶替触发（L2150-2156 查 ths）的影响
- 注意：THS 顶替查的是 ths_net_inflow 字段（L2151），与 main NULL 无关——EM 全 NaN 时顶替是否应触发（若当日 ths 有值，顶替可覆盖 NULL）

**裁定**：①/②/③ + 理由

### A-3：saved_count 语义与假成功判定

PM 草案：saved_count 仅计有效行（main 非 None），全无效 → 0 → 走降级。

**架构师请核验**：
- 现状 saved_count 计数点（L2002/L2053/L2104）与 em_all_failed 判定（L2123）关系
- 全 NaN 时：saved_count=0 → THS 顶替触发（若 ths 有值）→ 顶替写 main → 评分有真实数据 ✅ 链路是否顺畅
- 全 NaN 且 ths 无值 → 估算兜底（is_estimated=1）→ 仅展示——是否可接受
- data_status 写入时机与 message 格式（含有效行数）裁定

**裁定**：确认链路/需修改 + 理由

### A-4：防覆盖机制修订（重要）

**背景**：假成功写入"东方财富"开头 message → L1953-1960 误锁定。

**架构师请核验**：
- Read L1944-1967 与 L1925-1936 两机制关系：pre-check（查库 main 非空）是否已能防"NULL 行锁定"（NULL 行不满足 pre-check → 不跳过 → 可重采）
- 若 pre-check 已覆盖：存量 NULL 是否自动回补（无需额外处理）？data_status 防覆盖的 message 检查是否会拦截（message 是"东方财富..."但行是 NULL → pre-check 查库通过还是防覆盖先拦截？执行顺序 L1925 pre-check 在前，L1944 防覆盖在后——pre-check 通过后防覆盖仍可能拦截！需确认顺序）
- 裁定：防覆盖判断是否需增加"确有效值"校验；message 格式修订

**裁定**：确认现状/需修订 + 详情

### A-5：存量数据修复

PM 草案：方案 A 不动存量（依赖 pre-check 自动回补）；方案 B 清理误锁 data_status。

**架构师请核验**：
- 结合 A-4 裁定：存量 1512 行 NULL 是否下个交易日自动回补
- 若自动回补成立：无需存量操作（零代码用户优先）；若不成立：裁定方案 B 的具体范围（哪些 data_status 记录、如何判定误锁）
- 注意红线 8（不得批量清库）——任何存量操作须明确 SQL 范围与备份

**裁定**：方案 A / 方案 B（含精确范围）/ 其他 + 理由

### A-6：与 019K THS 顶替的衔接

**背景**：EM 全 NaN（假失败）与 EM 全失败（真失败）在修复后应同样触发 THS 顶替。

**架构师请核验**：
- 修复后 saved_count=0（全 NaN）→ em_all_failed=True → THS 顶替读取 ths_net_inflow（L2150-2156）→ 有值则顶替写 main（is_estimated=0）
- 顶替后该行 main=ths 值（非 NaN）→ 后续 EM 恢复 pre-check 判断 main 非空 → **但 capital_source='ths_total' 被排除（019K L1934）→ 可回补** ✅
- 确认无死锁：THS 顶替行 → EM 恢复 → 回补覆盖（019K 已验收）

**裁定**：确认闭环/需补充 + 理由

### A-7：范围与红线确认

任务书 v1 红线：功能红线（不得假成功）、范围（仅 data_collector.py）、语义（正常路径零变化）、零代码、评分纯净（019E）、降级链路（019K）、超时（019I/019J）、存量（不得批量清库）。

**架构师请核验**：
- 红线是否完备；是否有遗漏（如 NaN 防护对 `main_net_inflow_pct` 为 NaN 的独立处理）
- 是否需要考虑 akshare 层（L2068）返回 DataFrame 含 NaN 的差异（df 结构 vs list[dict]）
- 验收标准是否充分（mock 全 NaN/部分 NaN/正常三态）

**裁定**：完备/需补充 + 详情

---

## 三、交付物要求

`docs/reviews/review_019N_em_nan_fix_20260805.md`，含：

1. **逐决策点裁定**（A-1 ~ A-7，每项采纳/修改/否决 + 理由）
2. **独立核验的代码证据**（关键结论须附 Read 到的代码行号和内容）
3. **新发现的风险项**（R-x 编号，如有）
4. **评审结论**（通过 / 有条件通过 / 不通过）
5. **若裁定需修订任务书**，明确列出修订项（M-x 编号），PM 将据此修订任务书后交付开发

---

## 四、PM 备注

1. **本批次 PM 未越权评审**：PM 仅完成 DB 实证、根因定位与 PM 独立实验（NaN→NULL 机制验证），未自行产出 review 文档。架构师请以完全独立视角评审。
2. **根因已实锤**：round(NaN,2)=nan 不抛 + SQLite 存 NaN 变 NULL + saved_count 假计数 + 防覆盖锁定，四环证据链完整。评审重点是**方案设计**（A-1~A-3）与**存量/防覆盖闭环**（A-4~A-6），而非根因复核。
3. **关键顺序问题（A-4）**：L1925 pre-check（查库 main 非空）在 L1944 防覆盖（查 message）之前执行——NULL 行通过 pre-check 后，防覆盖是否仍会拦截？请架构师重点核验执行顺序，这决定存量修复方案（A-5）。
4. **零代码用户视角**：修复后用户无需任何操作——EM 正常日数据自动写入；EM 异常日自动走 THS 顶替/估算；EM 恢复后自动回补。
5. **紧迫性**：美的等 9 只数据已缺失多日，若存量无法自动回补将影响资金面评分（v5 权重 0.40）。建议尽快评审。
