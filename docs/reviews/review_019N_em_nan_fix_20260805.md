# 架构评审报告 — 019N EM 资金流 NaN 防护与假成功修正（数据完整性修复）

**评审人**：架构师
**评审日期**：2026-08-05
**任务书版本**：v1 草案（`docs/tasks/dev_tasks_20260805_019N_em_nan_fix.md`）
**评审方式**：独立 Read 代码核验 + 本机 DB 只读实证（不采信 PM 结论，全部关键结论独立复核）
**评审结论**：⚠️ **有条件通过**（M-1~M-4 修订后定稿 v2 移交开发）

---

## 〇、评审范围与独立核验清单

| # | 文件 | 核验位置 | 结论 |
|---|---|---|---|
| 1 | `modules/data_collector.py` | L1900-2366 `fetch_capital_flow` 全函数（pre-check L1926-1943、防覆盖 L1950-1967、Layer1 L1969-2020、Layer2 L2022-2071、Layer3 L2073-2124、THS顶替 L2140-2188、估算 L2190-2311、收口 L2313-2366） | ✅ 与任务书位置基本一致（少量行号漂移，见 M-1） |
| 2 | `modules/data_collector.py` | L1540-1556 `_parse_cn_amount`（NaN 防护参考写法） | ✅ 已含 `pd.isna` 防护，本批次不碰 |
| 3 | `modules/data_collector.py` | L1593-1618 `_fetch_capital_flow_em_individual` 解析层 | ⚠️ **关键发现**（见发现 4 / R-1） |
| 4 | `modules/data_collector.py` | L1634-1654 `_fetch_capital_flow_em`（push2 返回原始 klines 字符串） | ✅ 确认 Layer 2 输入为字符串，需字符串 NaN 判定 |
| 5 | `modules/data_collector.py` | L280-302 `save_data_status`（同日删除+重插） | ✅ 每维度每日仅保留最新一条 |
| 6 | `modules/data_collector.py` | L1326/L2931 调用点、`daily_report.py` L473-480/L536-544 批量流程 | ✅ 每日采集路径确认 |
| 7 | `modules/data_adapter.py` | L273-290 `_read_capital_data`（DESC+reverse 正序）、L385-387 latest 映射 | ✅ 主因子缺失→中性填充，无异常 |
| 8 | `modules/advisor.py` | L1123-1157 资金面因子（is_estimated 过滤 + None 过滤） | ✅ 5 日均/连续性对 None 安全 |
| 9 | `database/db_manager.py` | L238-254 建表（UNIQUE(stock_id,trade_date)）、L961-965 迁移列 | ✅ capital_source 列确认（本批次不碰） |
| 10 | `stock_analyst.db` 只读实证 | 1512 行统计、美的 9 只明细、data_status、ths_total 行 | ⚠️ **关键证据**（见发现 6） |

---

## 一、独立核验的核心发现

### 发现 1（决定性）：防覆盖机制仅锁"当日"，不存在跨日永久锁

核验 `fetch_capital_flow` L1953-1957 防覆盖 SQL：

```sql
SELECT message FROM data_status WHERE stock_id = ? AND dimension = 'capital'
AND fetched_at LIKE today_str + '%' ORDER BY fetched_at DESC LIMIT 1
```

- `fetched_at LIKE 'YYYY-MM-DD%'` **仅匹配当日记录**；昨日假成功记录次日不匹配 → 不拦截。
- `save_data_status`（L288-293）同日先删后插 → 每维度每日仅存最新一条。
- 结论：**"防覆盖锁定导致真实数据永久缺失"表述应修正**。实际损害链为：(a) 假成功掩盖 EM 数据源异常（日志/状态谎报成功）；(b) saved_count>0 使 Layer 2/3 与 THS 顶替全部被跳过（L2023 门控 + L2133 `em_all_failed = (saved_count == 0)`）；(c) 同日二次采集被防覆盖拦截。**跨日重采路径天然通畅**——这是存量修复方案（A-5）成立的前提，也是本评审最重要的发现。

### 发现 2（决定性）：存量 1512 行 NULL 可自动回补，无需任何存量操作

核验链条：

1. 每日采集流程（`daily_report.py` L477 批量预取 + L536 逐只 `collect_stock_data` → L2931 `fetch_capital_flow`）会重拉 EM 120 天全量历史（`lmt=0`，L1571）。
2. pre-check（L1931-1936）仅查"当日 main 非空"：NULL 行不满足 → 不跳过 → 重采放行。
3. Layer 1 对全历史 `INSERT OR REPLACE`（L1992-2009，UNIQUE(stock_id,trade_date) 同键替换）：EM 恢复正常后，历史 NULL 行被真实值**整行替换**。
4. 防覆盖（发现 1）跨日不拦截 → 无阻塞点。

**结论：方案 A（不动存量）成立，方案 B 不必要**（详见 A-5）。

### 发现 3：根因链四环独立核验全部属实

- **L1983**：`main_net = round(float(row.get('主力净流入-净额', 0) or 0) / 1e4, 2)` —— NaN 是 truthy，`or 0` 无效 → `round(nan,2)=nan` 不抛异常 → SQLite 存 NULL（Python sqlite3 驱动将 NaN 绑定为 NULL）。
- **L2010/L2062/L2114**：`saved_count += 1` 无条件计数（假成功源头）。
- **L2023**：`if saved_count == 0:` 门控 Layer 2；L2133 `em_all_failed = (saved_count == 0)` → 假成功使 THS 顶替与估算兜底全部跳过。
- **L2360-2361**：写"东方财富(个股历史)采集成功..." success 状态 → 同日防覆盖（L1960-1967）误锁。

### 发现 4（新）：解析层 L1593-1612 单字段不可解析即整批 120 天丢失（比 NaN 更凶的相邻缺陷）

`_fetch_capital_flow_em_individual` L1599-1610：

```python
'主力净流入-净额': float(parts[1]) if parts[1] else 0,
```

- 若 EM 返回 `'-'`（东财其他接口缺失值常见占位），`float('-')` 抛 ValueError → 外层 except（L1616-1618）→ **整批 120 天返回 None** → 历史数据全丢，仅降级到 Layer 2（push2 仅 10 天）。
- 与本次 NaN 缺陷同源（东财缺失值编码差异），修复成本极低（与三层共用同一安全转换函数），**建议并入本批次**（M-2）。

### 发现 5（新风险）：EM 三层 `INSERT OR REPLACE` 会清除同日期行其他来源字段

- REPLACE = DELETE + INSERT → 同键行上的 `margin_balance` / `ths_net_inflow` / `north_holding_change` 被清空（本批次不涉及 capital_source，019K 语义不受影响）。
- 自愈机制存在：融资余额采集用 UPDATE/INSERT OR IGNORE（L2655-2676），全清时走 159 天窗口回填（L2594）→ 每日"清-回填"循环下 margin 历史 ≈150 条。
- 019N 的"全 NaN 行跳过（不 REPLACE）"语义恰好减轻该副作用；根治（改 UPDATE 合并写入）超出本批次范围 → 登记技术债 R-2。

### 发现 6：本机 DB 只读实证（独立验证 PM 结论）

| 项 | 结果 | 与 PM 对照 |
|---|---|---|
| 全库 main NULL AND margin 有值 | **1512 行** | ✅ 一致 |
| main/margin/ths 全 NULL | **0 行** | ✅ 一致 |
| 9 只股票各 108 行 main NULL | 000333/000858/000977/002352/002415/002714/688017/688041/688047 各 108 | ✅ 一致 |
| 美的 08-05 | main=8303.79（真实）、margin=None、ths=881.0、is_estimated=0、capital_source=None | ✅ 当日有真实值 |
| 美的 08-04~07-30 | main=NULL、margin 有值、ths=NULL | ✅ |
| 美的 data_status 08-05 20:05:59 | "东方财富(push2)采集成功。已写入1天历史数据..." | 说明 08-05 Layer1(push2his) 返回空 → Layer2 写当日 1 行 |
| 全库 capital_source='ths_total' | **6 行** | 019K 顶替已生效 |

---

## 二、逐决策点裁定

### A-1：安全转换函数设计（核心）—— **裁定：采纳（修改 1 处关键细节）**

**核验结果**：三层转换点共 **17 个字段表达式**，独立确认位置：
- Layer 1 历史：L1983-1988 六字段（main/main_pct/super_large/large/medium/small）
- Layer 2 push2：L2037-2041 **五字段**（main/small/medium/large/super_large，无 pct）——任务书写的 L2029-2033 实为 L2037-2041
- Layer 3 akshare：L2087-2092 六字段

**裁定**：
1. **采纳模块级辅助函数**（置于 `_parse_cn_amount` 附近），三层共用：
   - `_safe_num(val) -> float|None`：None / 空串 / 'nan'/'NaN'/'-'/'None'（strip 后）/ 数值 NaN / ±Inf → None；`ValueError/TypeError` → None；其余 → float
   - `_safe_float_wan(val)`：`_safe_num` 后 ÷1e4 并 round 2，None 透传
   - `_safe_float_pct(val)`：`_safe_num` 后 round 2，None 透传
2. **修改点（相对 PM 草案）**：PM 草案判断式（`val is None or val == '' or (isinstance(val, float) and pd.isna(val))`）对 **Layer 2 无效**——push2 的 parts 是**原始字符串**（L1634-1654 返回 klines 字符串，L2033 split），字符串 `'nan'` 不满足 `isinstance(val, float)` 会漏判，`float('nan')` 后依旧写 NULL。**必须增加字符串 'nan'/'-' 判定与 strip 处理**。
3. **NaN 判定用 `pd.isna`**（既有依赖，兼容 np.float64/Layer 3 df 值；对齐 L1545/L2462/L2646 既有写法）+ **`math.isfinite` 拦截 ±Inf**（标准库，新增 `import math`，零新 pip 依赖，不违零代码红线）。
4. **明确移除 `or 0` 与 `row.get(key, 0)` 默认 0**：None 语义下 `None or 0` → 0 会假写 0，必须整体替换表达式。
5. **空串语义变更声明**：现状空串→0（L1599/L2037），修复后空串→None。属防御性变更（0 是伪造值，None 是缺失），QA 需纳入"正常有效值零回归"验证。

### A-2：NaN 行处理粒度（核心）—— **裁定：采纳 ①（六字段全 NaN → 跳过该行；部分 NaN → 置 None）**

**裁定**：采纳 PM 倾向方案 ①，理由：
1. skip 不产生 NULL 占位行（功能红线"不得写 NULL 占位行"直接满足）。
2. skip 不做 REPLACE → 该日期行上的既有真实字段（margin/ths/历史真实 main）**不被清空**（发现 5 的缓解）。
3. 部分 NaN 置 None → 保留有效子字段（超大单等因子/展示仍可用）。
4. **评分影响核验**：`data_adapter` L385-387 取 latest 行 main=None → 资金面主因子缺失 → 中性填充（现有降级机制），无异常；`advisor` L1141-1157 5 日均/连续性对 `None` 过滤（L1141 `if r['main_net_inflow'] is not None`）→ 安全；无占位行后 THS 顶替判定（L2150-2156 查 ths_net_inflow）与 EM 无关 → 顶替链路不受影响。
5. **当日行全 NaN 被跳过 → 当日无行 → 评分走 T-1 策略**（L2352-2355 已文档化"沿用T-1 + 标注截止日"）→ 次交易日自愈。
6. Layer 2 为五字段（无 pct），skip 判定基准同步为"五字段全 None"。

**附注**：主字段 main 为 None 但其余字段有效的行仍写入（保留子字段），且不计入 saved_count（衔接 A-3）。

### A-3：saved_count 语义与假成功判定 —— **裁定：采纳（链路确认）+ 文案细化**

**核验现状**：saved_count 计数点 L2010/L2062/L2114 无条件 +1（需改为"仅当 main_net 非 None 才 +1"）；`em_all_failed = (saved_count == 0)`（L2133）。

**链路确认（全 NaN 场景）**：
- 全 NaN → 三层均 0 有效行 → `em_all_failed=True` → THS 顶替（L2147-2184：查今日 ths_net_inflow 有值 → UPDATE/INSERT OR IGNORE 写 main + is_estimated=0 + capital_source='ths_total' → saved_count=1 → status='fallback' 提前返回 L2184）→ **评分使用真实数据** ✅ 链路顺畅。
- 全 NaN 且 ths 无值 → 估算兜底（is_estimated=1，L2306-2311，仅展示）✅ 可接受（019E 评分纯净红线不变）。
- 全链路失败 → error_logs（L2314-2328）+ status='failed'（L2365）✅。

**data_status 写入时机核验（纠正任务书认知）**：**Layer 1 内部并无 data_status 写入**（L2004-2012 仅为 INSERT + saved_count 累计 + commit）；success 状态收口于 **L2360-2361**（saved_count>0 时统一写入），failed 收口于 L2365，estimated 于 L2309，fallback 于 L2177-2180。因此修复后：全 NaN 日自动落入 fallback/estimated/failed 分支，**天然不产生"东方财富"开头 success**，假成功闭环闭合。

**文案裁定**：成功消息格式建议为 `f'{source}采集成功，写入 {saved_count} 天有效数据（跳过 {skipped} 天异常数据）'`，保留累计条数与日期对齐信息（L2360）；L2015 日志同步含有效/跳过数。

### A-4：防覆盖机制修订（重要）—— **裁定：确认现状即可，无需修改查询逻辑，不增加"确有效值"校验**

**执行顺序核验（PM 重点关切）**：pre-check（L1926-1943）**在前**，防覆盖（L1950-1967）**在后**。两机制均只查"当日"（pre-check 查当日 main 非空；防覆盖查当日 message 开头"东方财富"）。

**分场景裁定**：
1. **存量 NULL 行跨日重采**：防覆盖仅匹配当日记录（发现 1）→ **不拦截**；pre-check 对 NULL 日放行 → 重采放行。两机制均不构成存量阻塞。
2. **同日二次采集（修复前）**：假成功 message → 防覆盖拦截（L1960-1967）——这是"锁"的真实形态（仅当日）。
3. **同日二次采集（修复后）**：全 NaN 日 message 不以"东方财富"开头（A-3 已保证）→ 不拦截 → **同日 EM 恢复可即时重采** ✅ 修复效果达成。
4. **"确有效值"校验裁定：不增加**。理由：success 分支仅在 saved_count>0（至少 1 行 main 非 None）时可达，message 以"东方财富"开头 ⟺ 确有有效数据写入，等价性由 A-3 保证；增加校验徒增复杂度，无收益。
5. **语义边界**：saved_count>0 但当日行 NaN（历史有效 + 当日无效）→ 当日 message 仍以"东方财富"开头 → 同日重采被锁 → 当日无行走 T-1（L2352-2355），次交易日自愈 → 可接受（R-3 文档化）。

**message 格式**：保留 `startswith('东方财富')` 语义（source 前缀不动），仅按 A-3 追加有效/跳过行数。

### A-5：存量数据修复 —— **裁定：方案 A（不动存量），否决方案 B**

**裁定**：存量 1512 行 NULL 由发现 2 的自动回补链修复（EM 恢复后逐次全量 REPLACE 覆盖），**无需任何存量操作**。

**方案 B 否决理由**：
1. 防覆盖无跨日锁（发现 1）→ "清理误锁 data_status"**不解除任何实际阻塞**——存量 NULL 的修复依赖 EM 数据恢复，与 data_status 记录无关。
2. 批量操作触碰红线 8（不得批量清库），且零代码用户无法执行。
3. 修复后继续运行，data_status 记录自然更新（save_data_status 同日重插 + 新 message 语义）。

**边界声明**：若 EM 侧长期不恢复，历史 NULL 将保留（数据源问题，非代码可修复）；评分侧已有 T-1 策略 + 中性填充兜底（019E/019D 验收范围）。

### A-6：与 019K THS 顶替的衔接 —— **裁定：确认闭环，无需补充**

**核验闭环**：
1. 修复后全 NaN → `saved_count=0` → `em_all_failed=True` → THS 顶替（L2147-2184，仅当日；HK 无 ths 行自然跳过）→ 顶替行 is_estimated=0 + capital_source='ths_total'（019K 语义）→ status='fallback'。
2. EM 恢复 → pre-check 显式排除 ths_total 行（L1934 `capital_source != 'ths_total'`）→ 不跳过 → Layer 1 REPLACE 覆盖 → capital_source 归 NULL（L1997）→ 回补闭环成立（019K 已验收）。
3. 估算守卫核验：L2206-2210 / L2243-2247 / L2281-2285 均带 `capital_source != 'ths_total'` 守卫 → 估算不得覆盖顶替行 ✅。
4. 估算行（is_estimated=1）不阻塞 EM 回补（019E M-7，L1929-1933 过滤条件）✅。

**无死锁确认**：顶替行 → EM 恢复 → 回补覆盖 → 无自锁；同日二次采集在 fallback 状态不拦截（A-4 场景 3）。

### A-7：范围与红线确认 —— **裁定：基本完备，需补充 2 项**

**红线核验**：

| 红线 | 核验结果 |
|---|---|
| 功能红线（不假成功） | ✅ A-1/A-2/A-3 覆盖；"不写 NULL 占位行"由 skip 语义保证 |
| 范围红线（仅 data_collector.py） | ✅ 全部改动（含 M-2）均在文件内 |
| 语义红线（正常路径零变化） | △ 有效值路径零变化；空串 0→None 属防御性变更（A-1-5），QA 声明验证 |
| 零代码（无新 pip 依赖） | ✅ `import math` 标准库；pd 已有 |
| 评分纯净（019E 延续） | ✅ 估算过滤（is_estimated=1）零改动；本批次不放松 |
| 降级链路（019K 延续） | ✅ A-6 确认顺序不变 |
| 超时（019I/019J 延续） | ✅ 零新增网络调用（THS 顶替零请求，修复仅本地转换） |
| 存量（不得批量清库） | ✅ 方案 A 零操作 |

**补充项**：
1. `main_net_inflow_pct` NaN 独立处理：**无需独立逻辑**——pct 字段已包含在六字段统一安全转换中（Layer 1/3）。
2. akshare 层（Layer 3）差异：df 行值为 np.float64/str（L2082-2092），`pd.isna` + `_safe_num` 兼容（np.float64 是 float 子类）；df 中 'nan' 字符串同样覆盖。
3. **M-2（解析层安全化）**：发现 4 的 `'-'` 整批丢弃缺陷建议并入本批次（同源、低成本）。
4. **M-4（验收标准细化）**：见下节。

---

## 三、新发现的风险项

| # | 风险 | 等级 | 处置 |
|---|---|---|---|
| **R-1** | 解析层 L1593-1612 单字段 `'-'`/不可解析 → ValueError → 整批 120 天返回 None，历史全丢（降级 Layer 2 仅 10 天） | 中 | **本批次修复**（M-2，共用 `_safe_num`） |
| **R-2** | EM 三层 INSERT OR REPLACE 清空同日期行其他来源字段（margin_balance/ths_net_inflow/north_holding_change）；margin 有 150 条窗口自愈回填 | 中（既有） | 019N 的 skip 语义部分缓解；根治（改 UPDATE 合并写入）登记技术债，超出本批次 |
| **R-3** | saved_count>0 但当日行 NaN（历史有效）→ 不走 THS 顶替 → 当日无行 → T-1 策略（L2352-2355） | 低（可接受） | 文档化；可选增强（当日行无效时也触发顶替）本批次不采纳，避免复杂度 |
| **R-4** | 修复上线当日，盘中已存在的旧假成功记录仍锁同日二次采集 | 低 | 次日自动失效；不建议存量清理（零代码优先） |
| **R-5** | EM 侧长期不恢复时历史 NULL 无法回补；THS 顶替仅覆盖当日 | 低（数据源侧） | 评分 T-1 + 中性填充兜底已存在；EM 恢复后自动回补（A-5） |

---

## 四、任务书修订项（M-x）

| # | 修订项 | 说明 |
|---|---|---|
| **M-1** | 修正 Task 2 位置描述与行号 | data_status success 写入点为**全局收口 L2360-2361**（Layer 1 内 L2004-2012 无 data_status 写入）；行号修正：Layer 2 转换 L2029-2033→**L2037-2041**、saved_count 计数点 L2002/L2053/L2104→**L2010/L2062/L2114**、em_all_failed 判定 L2123→**L2133** |
| **M-2** | 解析层 L1593-1612 安全化纳入范围 | `_fetch_capital_flow_em_individual` 字段转换改用 `_safe_num`（None 语义），修复 `'-'` 整批丢弃（R-1）；QA 增加 mock '-'/空/NaN 字符串用例 |
| **M-3** | 文案与计数语义统一 | 成功消息含"有效行数 + 跳过异常行数"（A-3 文案）；L2015 日志同步；Layer 2 五字段、Layer 3 六字段的 skip 判定与 saved_count 规则与 Layer 1 一致 |
| **M-4** | 验收标准细化 | 在任务书 §四基础上补充：(a) mock 'nan' **字符串**（Layer 2 路径）与 mock '-'（M-2 路径）；(b) **存量自愈断言**：预置历史 NULL 行 → mock EM 正常全量 → REPLACE 后 NULL 消除；(c) **防覆盖三态**：全 NaN 日不锁 / 有效日锁（保持现状）/ fallback·estimated 日不锁；(d) **THS 顶替触发断言**：全 NaN + ths 有值 → 写 main、capital_source='ths_total'、status='fallback'；(e) **消息格式断言**：`startswith('东方财富')` ⟺ saved_count>0；(f) 正常有效值 mock 零回归（行数与值完全一致）；(g) `_safe_num/_safe_float_wan/_safe_float_pct` 单测（并入 tests/test_data_collector.py，参照既有 TestParseCnAmount 风格，覆盖 None/''/'nan'/'-'/float NaN/np.nan/正常值）；(h) `python -m pytest tests/` 全绿 + `python -m py_compile modules/data_collector.py` + 范围外文件哈希不变 |

---

## 五、评审结论

⚠️ **有条件通过**。

- **A-1** 采纳（1 处实现细节修改：字符串 'nan'/'-' 判定）；**A-2** 采纳 ①；**A-3** 采纳（链路确认 + 文案细化）；**A-4** 确认现状（不增加有效值校验；"假成功锁"实为当日锁，修复后消失）；**A-5** 方案 A，否决方案 B；**A-6** 确认闭环；**A-7** 需按 M-1~M-4 补充。
- 存量 1512 行 NULL 自动回补链独立核验成立：**零代码用户无需任何手动操作**。
- 任务书按 M-1~M-4 修订为 v2 后移交开发；开发完成后 QA 按任务书 §四 + M-4 补充项独立验收。

---

**评审人**：架构师 | **日期**：2026-08-05 | **关联**：019E/019I/019J/019K（降级链路延续）
