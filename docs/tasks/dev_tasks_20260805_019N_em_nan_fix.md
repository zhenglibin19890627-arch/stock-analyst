# 开发任务书 019N — EM 资金流 NaN 防护与假成功修正（数据完整性修复）

**签发日期**：2026-08-05
**签发人**：PM
**批次编号**：019N
**优先级**：P1（数据完整性：EM 返回 NaN 时假成功写入 NULL 占位，防覆盖锁定导致真实数据永久缺失，美的等 9 只实证）
**关联批次**：019E（估算兜底+评分隔离）、019I（THS 超时）、019J（单只超时）、019K（THS 顶替）
**架构评审**：⚠️ 有条件通过（评审报告：`docs/reviews/review_019N_em_nan_fix_20260805.md`），已按 M-1~M-4 修订定稿 v2

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：开发人员

**职责边界**：
- 按本任务书规格实现 EM 资金流 NaN 防护与假成功修正，完成编码+自验
- 不负责正式验收（QA 独立验收）
- 不修改红线区域（advisor.generate_advice、风控阈值、DB schema）
- 交付物：修改后的 `modules/data_collector.py` + 自验报告 `reports/dev_selftest_019N_em_nan_fix_20260805.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 开发人员仅做编码+自验，不执行正式验收
- 架构师评审结论未出前，本任务书为 v1；评审通过后 PM 修订定稿 v2，开发以定稿为准

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格，命令行需引号） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db` |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前 9 包） |

### 环境约束（硬性，违反将导致执行失败）
1. **项目在 IDE 工区外**：路径含空格，Write 工具直写会报错，须 "工作区 Copy + SearchReplace 编辑 + Copy-Item 覆盖回项目目录"
2. **PowerShell 中文**：追加中文到文件用 `[System.IO.File]::ReadAllText + WriteAllText`（UTF-8），禁止 Add-Content/Out-File（乱码）
3. **PowerShell 内联 Python**：含 `*` 的 SQL 会被通配符解析破坏，须用 `chr(39)` 包裹字符串或写临时 .py 脚本
4. **用户是零代码用户**：所有技术决策以"零代码用户可独立运行"为最高优先级

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 流程路径 | ✅PM 签发 v1 → ✅架构师评审（有条件通过，M-1~M-4 并入 v2） → ✅监理批准 v2（2026-08-05） → ✅开发执行+自验（48/48 功能 + 33 单测） → ✅QA 独立验收（V1~V11 全 PASS，53 断言） → ✅PM+QA 双签（2026-08-05） → ✅监理批准关闭（2026-08-05） |

---

## 一、背景

### 1.1 缺陷现象（2026-08-05 DB 实证）

美的集团（id=11）等 9 只股票近 10 个交易日主力资金数据**全部 NULL**（仅 08-05 有值）：

| 股票 | 02-27~08-04 行数 | main 有值 | margin 有值 | 现象 |
|---|---|---|---|---|
| 美的集团 | 108 行 | **0** | 108 | EM "保存成功 120 天" 但 main 全 NULL |
| 顺丰/绿的谐波/五粮液/牧原/海康/浪潮/海光/龙芯 | 同型 | 0 | 有 | 同型 |

全库统计：`main_net_inflow IS NULL AND margin_balance IS NOT NULL` 共 **1512 行**；`main NULL AND margin NULL AND ths NULL` 共 **0 行**——即所有 NULL 行都有 margin（融资余额采集创建的行），main 从未被 EM 真实写入。

### 1.2 根因链（PM 独立实验实锤）

```
EM 接口返回"主力净流入-净额"= NaN（当日数据异常/字段缺失）
  → L1983: main_net = round(float(row.get('主力净流入-净额', 0) or 0) / 1e4, 2)
  → float(NaN) 不抛异常；round(NaN, 2) = nan（实验实证不抛）
  → SQLite 存 NaN 自动变 NULL（实验实证: INSERT nan → SELECT 得 NULL）
  → saved_count += 1 仍计数（假成功，日志"保存成功 120 天"）
  → data_status 记 "东方财富(个股历史)采集成功"
  → 防覆盖机制（L1953-1960 message.startswith('东方财富')）误判"已有真实数据"
  → 后续批次同日跳过 → NULL 永久滞留，真实数据无法回补
```

**实证实验**（PM 2026-08-05 本机 Python 3.12）：
- `round(float('nan'), 2)` → `nan`（**不抛异常**）
- SQLite `INSERT nan` → `SELECT` 得 `None`（**NaN 自动变 NULL**）
- `float('nan') or 0` → `nan`（NaN 是 truthy，or 0 不生效）

### 1.3 影响面

- **新增股票/数据异常日**：任何股票在 EM 返回 NaN 时都会重演"假成功 + NULL 滞留"（美的等 9 只即加入自选后首次采集即 NaN）
- **评分影响**：main=NULL 时资金面因子缺失 → 中性填充（无区分度）或降级
- **019K 交互影响**：NULL 行不触发 THS 顶替（顶替条件是 ths 有值）→ 双重缺失

---

## 二、执行角色

**开发**（单人）

---

## 三、任务范围（v2 定稿——M-1~M-3）

> **改动范围收敛：仅 `modules/data_collector.py`。** 行号以架构师独立核验为准（M-1）。

### 任务 1：安全转换函数（A-1 裁定——采纳 + 修改）

**位置**：`_parse_cn_amount`（L1540-1556）附近新增模块级辅助函数，EM 三层 17 个字段表达式共用：

```python
def _safe_num(val):
    """019N: 安全数值转换。None/空串/'nan'/'NaN'/'-'/'None'(strip后)/数值NaN/±Inf → None；
    ValueError/TypeError → None；其余 → float"""
    if val is None:
        return None
    if isinstance(val, str):
        s = val.strip()
        if s == '' or s.lower() in ('nan', 'none', '-', 'inf', '-inf'):
            return None
        try:
            return float(s)
        except (ValueError, TypeError):
            return None
    try:
        f = float(val)
    except (ValueError, TypeError):
        return None
    if pd.isna(f) or not math.isfinite(f):
        return None
    return f


def _safe_float_wan(val):
    """安全转换（元→万元，round 2），None 透传"""
    f = _safe_num(val)
    return round(f / 1e4, 2) if f is not None else None


def _safe_float_pct(val):
    """安全转换（% 字段，round 2），None 透传"""
    f = _safe_num(val)
    return round(f, 2) if f is not None else None
```

**M-1 关键修正（相对 v1）**：
1. **字符串 'nan'/'-' 判定必须加入**（A-1 裁定）：Layer 2 push2 的 parts 是**原始字符串**（L1634-1654 返回 klines 字符串，L2033 split），v1 判断式 `isinstance(val, float) and pd.isna(val)` 对字符串 `'nan'` **漏判**，`float('nan')` 后依旧写 NULL
2. 判断用 `pd.isna`（既有依赖，兼容 np.float64/Layer 3 df 值，对齐 L1545/L2462/L2646 写法）+ `math.isfinite` 拦截 ±Inf（标准库，新增 `import math`，零新 pip 依赖）
3. **明确移除 `or 0` 与 `row.get(key, 0)` 默认 0**（None 语义下 `None or 0` → 0 会假写 0）
4. 空串语义变更声明：现状空串→0（L1599/L2037），修复后空串→None——防御性变更（0 是伪造值，None 是缺失），QA 须纳入"正常有效值零回归"验证

### 任务 2：EM 三层转换改造 + 行跳过语义（A-2 裁定——采纳 ①）

**位置与字段数（M-1 修正）**：
- Layer 1 历史：L1983-1988 六字段（main/main_pct/super_large/large/medium/small）
- Layer 2 push2：**L2037-2041 五字段**（main/small/medium/large/super_large，无 pct）
- Layer 3 akshare：L2087-2092 六字段

**行跳过语义（A-2 裁定①）**：
- **六字段全 NaN（Layer 2 为五字段全 NaN）→ 跳过该行（continue）**——不写 NULL 占位行（功能红线直接满足）、不做 REPLACE（不清空该日行上 margin/ths 等既有真实字段）
- **部分字段 NaN → 置 None，保留有效子字段**
- 主字段 main 为 None 但其余有效的行：仍写入（保留子字段），**不计入 saved_count**（衔接 A-3）

### 任务 3：saved_count 语义与假成功修正（A-3 裁定——采纳 + 文案细化）

**计数点（M-1 修正）**：L2010（Layer 1）/ L2062（Layer 2）/ L2114（Layer 3）——原无条件 `+1`，改为**仅当该行 main_net 非 None 才 +1**（主字段有效性为准，与 A-2 附注一致）。

**链路确认（全 NaN 场景）**：
- 全 NaN → 三层均 0 有效行 → `em_all_failed=True`（L2133）→ THS 顶替（L2147-2184：查今日 ths_net_inflow 有值 → 写 main + is_estimated=0 + capital_source='ths_total' → status='fallback' 提前返回）→ **评分使用真实数据** ✅
- 全 NaN 且 ths 无值 → 估算兜底（is_estimated=1，L2306-2311，仅展示）✅
- 全链路失败 → error_logs（L2314-2328）+ status='failed'（L2365）✅

**data_status 写入时机（M-1 修正，纠正 v1 认知）**：**Layer 1 内部无 data_status 写入**（L2004-2012 仅 INSERT + 计数 + commit）；success 收口于 **L2360-2361**（saved_count>0 统一写入），failed 于 L2365，estimated 于 L2309，fallback 于 L2177-2180。修复后全 NaN 日天然落入 fallback/estimated/failed 分支，**不产生"东方财富"开头 success** → 假成功闭环闭合（无需额外"有效值校验"逻辑，A-4 裁定不增加）。

**文案（A-3 裁定）**：成功消息格式 `f'{source}采集成功，写入 {saved_count} 天有效数据（跳过 {skipped} 天异常数据）'`（L2360 保留累计条数/日期对齐）；L2015 日志同步含有效/跳过数。

### 任务 4：解析层安全化（M-2——架构师新发现 R-1，并入本批次）

**位置**：`_fetch_capital_flow_em_individual` 解析层 **L1593-1612**

**缺陷（R-1，比 NaN 更凶的相邻缺陷）**：L1599 `'主力净流入-净额': float(parts[1]) if parts[1] else 0`——若 EM 返回 `'-'`（东财缺失值常见占位），`float('-')` 抛 ValueError → 外层 except（L1616-1618）→ **整批 120 天返回 None** → 历史数据全丢，仅降级 Layer 2（仅 10 天）。

**修复**：该解析层字段转换改用 `_safe_num`（None 语义），`'-'`/空/NaN 不再炸批。

### 任务 5：存量数据修复（A-5 裁定——方案 A，否决方案 B）

**架构师独立发现（发现 1/2，决定性）**：
1. **防覆盖仅锁"当日"**：防覆盖 SQL（L1955）`fetched_at LIKE today_str + '%'` **仅匹配当日记录**；昨日假成功次日不匹配 → 不拦截。**"防覆盖锁定导致永久缺失"表述应修正**——实际损害链为：假成功掩盖 EM 异常 + saved_count>0 跳过 THS 顶替/估算 + 同日二次采集拦截。**跨日重采天然通畅**。
2. **存量 1512 行 NULL 可自动回补**：每日采集重拉 EM 120 天全量（lmt=0）→ pre-check（L1931-1936）仅查当日 main 非空（NULL 日放行）→ Layer 1 对全历史 INSERT OR REPLACE（UNIQUE(stock_id,trade_date) 同键替换）→ EM 恢复后历史 NULL 被真实值整行替换 → 防覆盖跨日不拦截。

**裁定**：**方案 A（不动存量）**——零代码用户无需任何手动操作，EM 恢复后自动回补。**否决方案 B**（清理 data_status 不解除任何实际阻塞；批量操作触碰红线 8）。

**边界声明**：若 EM 侧长期不恢复，历史 NULL 保留（数据源问题）；评分侧已有 T-1 策略 + 中性填充兜底（019E/019D 验收范围）。

### 明确不改范围（v2 定稿）

- `_parse_cn_amount`（L1540-1556）— 已含 NaN 防护，不碰
- 北向（L2461-2464）/融资余额（L2646-2647）— 已有 pd.isna 防护，不碰
- 防覆盖查询逻辑（L1950-1967）— **不修改**（A-4 裁定：等价性由 A-3 保证，不增加"确有效值"校验）
- `modules/advisor.py` / `modules/analysis_engine.py` / `modules/alert_engine.py` / `modules/scoring_engine.py` / `modules/data_adapter.py` — 零改动
- `app.py` / `config.py` / `templates/index.html` / `database/db_manager.py` — 零改动
- `requirements.txt` — 零改动（math 是标准库）

---

## 四、验收标准（v2 定稿——M-4）

1. **代码级核查**：`_safe_num/_safe_float_wan/_safe_float_pct` 存在且被 EM 三层 17 字段全部使用（grep）；无 `or 0` 残留于 EM 三层；行跳过语义（六/五字段全 None → continue）落实；saved_count 仅计 main 非 None 行
2. **编译验证**：`python -m py_compile modules/data_collector.py` 无错误
3. **功能验证（QA mock）**：
   - mock EM 返回全 NaN → 断言 saved_count=0、无"东方财富"开头 success message、落 THS 顶替/估算降级
   - mock EM 返回部分 NaN（当日 NaN、历史正常）→ 断言当日行跳过（无 NULL 占位）、历史有效行写入、saved_count=历史有效数
   - mock EM 返回正常 → 断言行为与现状完全一致（零回归，行数与值一致）
   - **mock 'nan' 字符串（Layer 2 路径）** 与 **mock '-'（M-2 解析层路径）** → 断言不炸批、不写 NULL
4. **防覆盖三态验证（M-4 细化）**：全 NaN 日不锁（message 非"东方财富"开头）/ 有效日锁（保持现状 startswith('东方财富') ⟺ saved_count>0）/ fallback·estimated 日不锁
5. **THS 顶替触发断言（M-4）**：全 NaN + ths 有值 → 写 main、capital_source='ths_total'、status='fallback'（019K 衔接）
6. **存量自愈断言（M-4）**：预置历史 NULL 行 → mock EM 正常全量 → REPLACE 后 NULL 消除（发现 2 验证）
7. **单元测试（M-4）**：`_safe_num/_safe_float_wan/_safe_float_pct` 单测并入 tests/test_data_collector.py（参照既有 TestParseCnAmount 风格），覆盖 None/''/'nan'/'-'/float NaN/np.nan/±Inf/正常值
8. **回归**：`python -m pytest tests/` 全绿；零改动文件哈希不变
9. **评分纯净验证**：估算行（is_estimated=1）过滤行为不变（019E 红线）

---

## 五、红线约束（v2 定稿）

1. **功能红线**：EM 返回 NaN 时不得假成功——不得写 NULL 占位行（行跳过保证）、不得锁定防覆盖（message 语义保证）
2. **范围红线**：改动仅限 `modules/data_collector.py`（含 M-2 解析层 L1593-1612）
3. **语义红线**：正常有效值路径行为零变化；空串 0→None 属防御性变更（A-1-5，QA 声明验证）
4. **零代码约束**：不引入新 pip 依赖（math 标准库）；config.py/DB schema 不碰
5. **评分纯净红线（019E 延续）**：is_estimated=1 估算行永不进评分；本批次不放松过滤
6. **降级链路红线（019K 延续）**：EM 全失败 → THS 顶替 → 估算顺序不变；019N 修复后链路正常衔接
7. **超时红线（019I/019J 延续）**：本批次零新增网络调用（修复仅本地转换）
8. **存量红线**：不得批量清库/改历史数据（方案 A 零操作）

---

## 六、执行顺序

```
Step 1: ✅ PM 签发 v1
Step 2: ✅ 架构师评审（2026-08-05 有条件通过，M-1~M-4 并入 v2）
Step 3: ✅ 监理批准 v2（2026-08-05）
Step 4: ⏳ 开发执行 + 自验
Step 5: ⏳ QA 独立验收 → PM+QA 双签 → 监理批准关闭
```

---

## 七、PM 备注

1. **立项来源**：监理指示核查资金面数据获取情况 → PM 发现美的等 9 只"120 天假成功"实证 → 监理批准 019N 立项。
2. **关键证据链**（PM 独立实验 + DB 实证）：
   - `round(float('nan'), 2)` = nan 不抛异常
   - SQLite INSERT nan → SELECT NULL
   - 美的 108 行 main 全 NULL + margin 全有值（融资余额采集创建的行）
   - 08-04 14:18:15 日志"000333 获取到 120 天资金流向历史数据"+"保存成功: 120天历史数据"——假成功实锤
3. **v2 修订说明（M-1~M-4，架构评审后并入）**：
   - **M-1（位置与行号修正）**：data_status success 收口于全局 L2360-2361（Layer 1 内无写入）；Layer 2 转换 L2037-2041（五字段）；saved_count 计数点 L2010/L2062/L2114；em_all_failed L2133
   - **M-2（解析层安全化，R-1 并入）**：`_fetch_capital_flow_em_individual` L1593-1612 改用 `_safe_num`，修复 `'-'` 整批 120 天丢失缺陷
   - **M-3（文案与计数统一）**：成功消息含"有效行数 + 跳过异常行数"；三层 skip/saved_count 规则一致
   - **M-4（验收细化）**：mock 'nan' 字符串/'-' 用例、存量自愈断言、防覆盖三态、THS 顶替触发断言、单元测试并入 tests/test_data_collector.py
4. **架构师决定性发现（本批次关键认知修正）**：
   - **防覆盖仅锁当日**（L1955 `fetched_at LIKE today+%`）——"永久锁定"表述修正，跨日重采天然通畅
   - **存量 1512 行 NULL 自动回补成立**（发现 2 链条：每日全量重拉 → pre-check 放行 NULL 日 → REPLACE 覆盖）→ 方案 A 零操作
   - **R-2 技术债登记**：EM 三层 REPLACE 清空同日期行其他来源字段（margin/ths/north）——019N skip 语义部分缓解，根治（UPDATE 合并写入）超出本批次
   - **R-3 文档化**：saved_count>0 但当日行 NaN（历史有效）→ 不走 THS 顶替 → T-1 策略（L2352-2355）——可接受
5. **019K 衔接确认（A-6）**：全 NaN → THS 顶替触发 → 顶替行可被 EM 恢复回补（capital_source 归位）——无死锁。
6. **与 019P 并行隔离**：019P 动 data_collector.py **基本面区域**（L542-1000）；本批次动**资金面区域**（L1593-2366）——区域隔离，开发/QA 各自断言行号区间。
