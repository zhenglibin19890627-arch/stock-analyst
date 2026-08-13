# 开发任务书 019K — 东财全失败时同花顺（THS）真实资金数据顶替主力净流入（方案一）

**签发日期**：2026-08-05
**签发人**：PM
**批次编号**：019K
**优先级**：P2（数据可用性增强；东财挂停时资金面真实数据缺失，靠估算兜底仅展示）
**关联批次**：019C（EM 回退优化）、019E（估算兜底+评分隔离）、019G/019H/019I（THS 链路）、019J（超时保护）
**架构评审**：⚠️ 有条件通过（评审报告：`docs/reviews/review_019K_ths_capital_fallback_20260805.md`），已按 M-1~M-12 修订定稿 v2

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：开发人员

**职责边界**：
- 按本任务书 v2 定稿规格实现"东财全失败 → 同花顺真实数据顶替主力净流入"（方案一），完成编码+自验
- 不负责正式验收（QA 独立验收）
- 不修改红线区域（advisor.generate_advice、风控阈值）
- 交付物：修改后的代码（data_collector.py + db_manager.py + index.html）+ 自验报告 `reports/dev_selftest_019K_ths_capital_fallback_20260805.md`

### 独立性原则
- 各角色独立不兼职：PM 不兼架构、架构师不编码、开发不验收、QA 独立测试
- 开发人员仅做编码+自验，不执行正式验收
- 本任务书为 v2 定稿（架构师有条件通过 + M-1~M-12 已并入），开发以本稿为准

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
| 流程路径 | ✅PM 签发 v1 → ✅架构师评审（有条件通过，M-1~M-12 已并入 v2） → ✅监理批准 v2（2026-08-05） → ✅开发执行+自验（42/42 PASS） → ✅QA 独立验收（44/44 断言 PASS） → ✅PM+QA 双签（2026-08-05） → ✅监理批准关闭（2026-08-05） |

---

## 一、背景

### 1.1 缺陷现象（2026-08-05 实证）

东方财富资金流接口当日**全部 RemoteDisconnected**（push2his/push2/akshare 三层全失败）。报告批次中资金面补采 23 只仅 7 只成功，连续失败 5 次熔断，剩余股票无真实资金数据 → 评分资金面因子缺失（v5 资金面权重 0.40，主力子项 0.55，缺失即中性填充无区分度）。

**关键事实**：同日 THS 批量预取**成功获取 5199 只真实资金流数据**（16:15:35 与 20:11 均成功，45s 完成），但仅写入辅助字段 `ths_net_inflow`，**未顶替** `main_net_inflow`——评分链路读不到，真实数据闲置。

### 1.2 资金数据链路（v2 事实修订后——M-1）

| 源 | 服务器 | 写字段 | 口径 | 是否参与评分 |
|---|---|---|---|---|
| 东方财富 push2his/push2 | push2.eastmoney.com | main_net_inflow | 主力净流入 | ✅ is_estimated=0 |
| akshare `stock_individual_fund_flow` | **东财（底层）** | main_net_inflow | 主力净流入 | ✅ |
| **同花顺 THS 主接口** `stock_fund_flow_individual` | **data.10jqka.com.cn** | **ths_net_inflow（辅助）** | **全部资金净流入（总主动买-总主动卖），非主力** | ❌ 不进评分 |
| ~~THS 备选接口 `stock_individual_fund_flow_rank`~~ | **~~push2.eastmoney.com（东财！非 THS）~~** | — | 主力口径（但=东财，EM 挂时同步失败） | **本批次不使用（M-1 事实修订）** |
| 新浪/腾讯/网易估算 | — | main_net_inflow + is_estimated=1 | 成交额×涨跌幅 公式 | ❌ 仅展示 |

> **M-1 事实修订（最高优先，架构师 akshare 1.18.53 源码核验 + PM 独立复验确认）**：任务书 v1 所称"THS 备选接口 `stock_individual_fund_flow_rank`"实为**东方财富接口**（URL=`https://push2.eastmoney.com/api/qt/clist/get`，`akshare/stock/stock_fund_em.py`），非同花顺服务器。EM 全挂时该接口必然同步失败（今日实时探针实证 RemoteDisconnected）。**唯一与 EM 独立的源 = THS 主接口（全部资金口径），无口径完全匹配的独立备选。**

### 1.3 方案一（监理裁定）

**东财三层全失败时，用同花顺当日真实资金数据顶替写入 `main_net_inflow`，使资金面因子仍可用真实数据参与评分**（替代现有"估算兜底"）。

**口径偏差知情（监理已批方案一并知情）**：THS 全部资金净流入 vs 主力净流入是不同概念，同日符号可相反（08-05 DB 实证：600276 EM+15335.59 万 vs THS -11800 万；300146 EM+1263.45 vs THS -1203.27）。本方案接受偏差 + **全链路标注** + **可回补**；提供"口径纯净开关"（D-2 附注，监理可随时切换为仅展示不评分）。

---

## 二、执行角色

**开发**（单人）

---

## 三、任务范围（v2 定稿——M-4/M-5/M-6/M-7/M-10）

> **改动范围：3 个文件——`modules/data_collector.py`（必改）+ `database/db_manager.py`（迁移列表）+ `templates/index.html`（标注与状态映射）。其余文件（advisor/analysis_engine/alert_engine/scoring_engine/data_adapter/app.py/daily_report）一律零改动。**

### 任务 1：`raw_capital_flow` 新增 `capital_source` 列（M-3）

**文件**：`database/db_manager.py`
**位置**：迁移列表 L950-964 `_safe_add_columns` 之后追加
**内容**：
```python
# 019K：资金数据来源标记（NULL=东财真实；'ths_total'=同花顺全部资金口径顶替）
'safe_add_columns': ...（在迁移列表追加 capital_source TEXT DEFAULT NULL）
```
**要求**：
- 走既有 `_safe_add_columns` 自动幂等迁移机制（018/019E 先例），启动自动迁移，零代码约束满足
- **否决"第三档 is_estimated 值"方案**（架构师 D-2 裁定）：`is_estimated=0 OR is_estimated IS NULL` 过滤（data_adapter L282/advisor L1126/analysis_engine L132/alert_engine L205 四处）会把第三档值静默排除，所有读路径语义不可控
- 不改表结构其他部分，不动 daily_reports 等表

### 任务 2：THS 顶替写入逻辑（M-4/M-5——插入点 ①）

**文件**：`modules/data_collector.py`
**位置**：`fetch_capital_flow()` L2123 `em_all_failed` 块内、估算源（腾讯/新浪/网易）**之前**插入

**实现规格**：
```python
# === 019K：THS 真实数据顶替（EM 三层全失败时，位于估算兜底之前）===
if em_all_failed:
    try:
        # 从库读当日 ths_net_inflow（零网络请求，THS 批量已在循环前入库）
        conn_ths = get_connection()
        cur_ths = conn_ths.cursor()
        cur_ths.execute(
            'SELECT ths_net_inflow FROM raw_capital_flow WHERE stock_id=? AND trade_date=? LIMIT 1',
            (stock_id, today_str),
        )
        ths_row = cur_ths.fetchone()
        conn_ths.close()
        ths_val = ths_row['ths_net_inflow'] if ths_row else None

        if ths_val is not None:
            # UPDATE（已有占位行）或 INSERT OR IGNORE（无行）；禁止 INSERT OR REPLACE（不清占位行）
            cur = get_connection().cursor()  # 或复用 conn
            cur.execute(
                'UPDATE raw_capital_flow SET main_net_inflow=?, is_estimated=0, capital_source=? '
                'WHERE stock_id=? AND trade_date=?',
                (ths_val, 'ths_total', stock_id, today_str),
            )
            if cur.rowcount == 0:
                cur.execute(
                    'INSERT OR IGNORE INTO raw_capital_flow '
                    '(stock_id, trade_date, main_net_inflow, is_estimated, capital_source) '
                    'VALUES (?, ?, ?, 0, ?)',
                    (stock_id, today_str, ths_val, 'ths_total'),
                )
            conn.commit(); conn.close()
            saved_count = 1
            save_data_status(
                stock_id, 'capital', 'fallback',
                '同花顺顶替(全部资金口径，非主力；东财恢复后自动回补)'
            )
            logger.info(f'[{symbol}] THS 真实数据顶替成功: {ths_val} 万（全部资金口径，is_estimated=0）')
            return 'fallback', '同花顺顶替(全部资金口径，非主力；东财恢复后自动回补)'
    except Exception as e:
        warnings.append(f'THS 顶替失败: {e}')
        logger.warning(f'[{symbol}] THS 顶替失败: {e}')
    # 顶替失败（ths 为 NULL 或异常）→ 落回现有估算兜底，链路不变
```

**硬性要求（M-4）**：
1. 顶替仅写 `main_net_inflow` + `is_estimated=0` + `capital_source='ths_total'`，**不得写** `main_net_inflow_pct/super_large_net/large_net/medium_net/small_net`（THS 主接口无主力分单数据，写入将引入第二个口径错位字段）
2. 仅写**当日 1 行**（trade_date=today_str），不污染历史序列
3. 使用 `UPDATE` + `INSERT OR IGNORE`，**严禁 INSERT OR REPLACE**（避免清除占位行已有字段——019E M-5 同型）
4. 返回语义：成功 → `('fallback', '同花顺顶替(全部资金口径，非主力；东财恢复后自动回补)')`；`data_status.status='fallback'`（M-5）
5. 读取 `ths_net_inflow` 零网络调用（THS 批量在 daily_report L473-480 / batch-analyze L1285-1301 循环前已入库）——超时红线自然满足；**严禁新增实时 THS 网络调用**（如开发认为必须，须复用 `_call_with_timeout`（019I 模式 L1153-1165），严禁裸调用）

### 任务 3：防覆盖闭环（M-6——发现 3 阻塞项）

**文件**：`modules/data_collector.py`，4 处修改：

| # | 位置 | 修改 |
|---|---|---|
| ① | L1925-1929 前置跳过 SQL（fetch_capital_flow 入口） | 追加 `AND (capital_source IS NULL OR capital_source != 'ths_total')`——THS 顶替行不得阻塞 EM 恢复回补 |
| ② | L1498-1505 补采清单 SQL（fetch_capital_flow_batch 019E 补采） | 同步追加 `AND (capital_source IS NULL OR capital_source != 'ths_total')` |
| ③ | EM 三层写入（L1984-2001 / L2036-2052 / L2086-2103，INSERT OR REPLACE） | 显式携带 `capital_source=NULL`（019E M-7 扩展：EM 写入 is_estimated=0 **且 capital_source=NULL**） |
| ④ | 估算三处 UPDATE（L2146 / L2182 / L2218，估算写入） | 追加来源守卫 `AND (capital_source IS NULL OR capital_source != 'ths_total')`（防御性——流程上估算路径在顶替成功（saved_count>0）后不可达） |

**覆盖关系表（裁定确认，开发实现必须满足）**：

| 覆盖方向 | 允许 | 机制 |
|---|---|---|
| EM 真实 → 覆盖 THS 顶替 | ✅ 必须 | ①+② 排除 + ③ EM 写入归位 |
| 估算 → 覆盖 THS 顶替 | ❌ 禁止 | ④ 来源守卫 |
| THS 顶替 → 覆盖 EM 真实 | ❌ 禁止 | 顶替仅在 em_all_failed（saved_count==0）触发 + 入口前置跳过 |
| THS 顶替 → 覆盖估算 | ✅ 允许 | 真实覆盖估算，019E M-7 精神 |

### 任务 4：状态消费方适配（M-7——红线补充）

**文件**：`modules/data_collector.py` + `templates/index.html`

1. `_em_batch_collect` L1327 `result[0]=='success'` 判定：**'fallback' ≠ 'success' → 计失败、EM 熔断计数不重置**（正确：EM 仍不可用，维持熔断保护）——确认无需改动（语义天然正确），但须在自验中验证
2. `templates/index.html`：
   - L2482 资金面表头动态文案：来源动态标注"来源：东方财富 / 同花顺顶替（全部资金口径）"
   - L2491 资金面表格行内 `<sup>` 标注：THS 顶替行显示"同花顺"标记（与现有估算标注并列）
   - L2550 / L2073 status 三元链：增加 `'fallback'` → `'⚠️顶替'` 映射

### 任务 5：docstring 注释一致性（M-10）

**文件**：`modules/data_collector.py`
- L1368-1371 docstring："主力净流入唯一来源为东方财富" → 修订为"主力净流入**主来源**为东方财富；EM 三层全失败时以同花顺全部资金口径顶替（标注，is_estimated=0）作为评分真实数据第二源；估算兜底仅展示"
- L1916-1920 注释同步修订

**明确不改范围**：

- `modules/advisor.py` / `modules/analysis_engine.py` / `modules/alert_engine.py` / `modules/scoring_engine.py` — 零改动（评分过滤按 D-2 附注保持参与，capital_source 随 SELECT * 自动透出）
- `modules/data_adapter.py` — 零改动
- `modules/daily_report.py` — 零改动（批量预取时序已满足）
- `app.py` — 零改动（展示接口 SELECT * 自动透出 capital_source）
- `config.py` — 零改动
- `requirements.txt` — 零改动

---

## 四、验收标准（v2 定稿——M-9）

1. **代码级核查**：
   - `capital_source` 列存在于迁移列表（db_manager.py）且随启动自动迁移（DB 实测列存在）
   - 顶替逻辑位于 `em_all_failed` 块内、估算源之前；UPDATE + INSERT OR IGNORE（无 INSERT OR REPLACE）
   - 顶替写字段仅 main_net_inflow/is_estimated=0/capital_source='ths_total'（无 pct/分单字段）
   - 4 处防覆盖修改全部落地（pre-check ① / 补采清单 ② / EM 写入 ③ / 估算守卫 ④）
   - `_em_batch_collect` L1327 判定确认 'fallback' 不计成功
2. **编译验证**：`python -m py_compile modules/data_collector.py database/db_manager.py` 无错误
3. **功能验证（QA mock）**：
   - mock EM 三层全失败 + 库内已有 ths_net_inflow → 顶替写入 main_net_inflow=ths 值、is_estimated=0、capital_source='ths_total'、status='fallback'、返回 ('fallback', msg)
   - 顶替行可被 data_adapter._read_capital_data 读取并进入 v5 main_capital 评分（断言评分资金面因子非缺失）
4. **防覆盖验证（QA 重点）**：
   - EM 恢复重采 → 可覆盖 THS 顶替行（pre-check 不跳过）且 capital_source 归位 NULL
   - 补采清单（fetch_capital_flow_batch）排除 capital_source='ths_total' 行
   - 估算路径不得覆盖 THS 顶替行（估算 UPDATE 守卫）
   - EM 成功时（正常路径）THS 不顶替，零干扰
5. **状态消费验证**：'fallback' 状态 → 前端三元链映射 '⚠️顶替'；`_em_batch_collect` 对 'fallback' 计失败不重置熔断计数
6. **标注验证**：前端资金面表格 THS 顶替行有"同花顺"标注（截图断言）
7. **零改动确认**：范围外文件（advisor/analysis_engine/alert_engine/scoring_engine/data_adapter/daily_report/app.py/config.py/requirements.txt）哈希不变
8. **回归**：`python -m pytest tests/` 全过；估算兜底链路（THS 也失败时）行为不变

---

## 五、红线约束（v2 定稿——M-8/M-11）

1. **功能红线**：EM 全失败时资金面不得因"无真实数据"缺失因子——THS 真实数据顶替后参与评分
2. **口径红线（M-8 修订）**：顶替数据必须**全链路标注口径差异**（data_status + 前端 + 状态映射）；口径偏差由监理知情接受；监理可选"口径纯净开关"（4 处 WHERE 各追加一行 `AND (capital_source IS NULL OR capital_source != 'ths_total')` 即切换为仅展示）——本批次默认参与评分
3. **来源标注红线（M-11）**：THS 顶替行在资金面表格（index.html L2491 行内标注 + L2482 表头动态文案）与 data_status 必须可见
4. **EM 回补红线（M-11）**：pre-check（L1925）与补采清单（L1498）必须排除 `capital_source='ths_total'` 行（发现 3 阻塞项）
5. **状态消费红线（M-11）**：'fallback' 状态必须同步适配前端两处三元链（L2550/L2073）与 `_em_batch_collect` L1327 判定（不重置 EM 熔断计数）
6. **范围红线（M-7）**：改动仅限 data_collector.py + db_manager.py（迁移列表）+ index.html（标注与状态映射），其余文件一律不碰
7. **零代码约束**：不引入新 pip 依赖；config.py 不碰；DB schema 变更仅限新增 capital_source 列（走 _safe_add_columns 自动迁移）
8. **防覆盖红线**：估算值不得覆盖 THS 顶替值；THS 顶替值不得覆盖 EM 真实值；EM 恢复必须能覆盖 THS 顶替值
9. **评分纯净红线（019E 延续）**：is_estimated=1 估算行始终不参与评分；is_estimated=0 OR IS NULL 过滤语义不得被第三档值破坏
10. **超时红线（019I/019J 延续）**：顶替链路读库零网络调用；严禁新增裸 THS/EM 网络调用（必须时复用 `_call_with_timeout` / daemon 线程模式）

---

## 六、执行顺序

```
Step 1: ✅ PM 签发 v1
Step 2: ✅ 架构师评审（2026-08-05 有条件通过，M-1~M-12 已并入 v2）
Step 3: ✅ 监理批准 v2（2026-08-05）
Step 4: ✅ 开发执行 + 自验（2026-08-05，42/42 PASS）
Step 5: ✅ QA 独立验收（2026-08-05，44/44 断言 PASS）→ ✅ PM+QA 双签（2026-08-05）→ ✅ 监理批准关闭（2026-08-05）
```

---

## 七、PM 备注

1. **立项来源**：监理在 PM 汇报"资金数据源核查"后裁定方案一（东财全失败 → THS 真实数据顶替主力净流入），PM 立项 019K。
2. **v2 修订说明（M-1~M-12，架构评审后并入）**：
   - **M-1（最高优先，事实修订）**：v1"THS 备选接口"认知错误——`stock_individual_fund_flow_rank` 实为东财接口（akshare 源码 `stock_fund_em.py`，URL=push2.eastmoney.com，PM 已独立复验），EM 挂时同步失败，作为顶替备选不存在。唯一独立源 = THS 主接口（全部资金口径）。
   - **M-2（D-1 裁定）**：顶替源 = 甲（库内 ths_net_inflow，零网络）；口径偏差接受 + 全链路标注。
   - **M-3（D-2 裁定）**：新增 capital_source 列（NULL=东财 / 'ths_total'=THS 顶替），否决第三档 is_estimated 值。
   - **M-4（D-4 裁定）**：插入点①（em_all_failed 处、估算前）；UPDATE+INSERT OR IGNORE；仅写当日 1 行 main_net_inflow/is_estimated=0/capital_source；不写 pct/分单字段。
   - **M-5（返回语义）**：返回 ('fallback', msg)，data_status status='fallback'。
   - **M-6（D-5 裁定）**：4 处防覆盖（pre-check ①/补采清单 ②/EM 写入 ③/估算守卫 ④）——发现 3 阻塞项，必须落地否则 THS 顶替值永驻评分链路、EM 无法回补。
   - **M-7（D-8 裁定）**：范围 = 3 文件（data_collector + db_manager 迁移 + index.html 标注）。
   - **M-8（D-8 裁定）**：红线 2 修订为"标注 + 知情 + 可选纯净开关"。
   - **M-9（验收）**：验收标准新增顶替/回补/补采排除/评分进入/标注断言。
   - **M-10（注释）**：L1368-1371/L1916-1920 docstring 修订。
   - **M-11（红线）**：新增来源标注/EM 回补/状态消费三条红线。
   - **M-12（风险登记）**：R-1~R-8 见评审报告第三节；R-2（EM 逐只挂死）维持登记不纳入本批次。
3. **监理决策点（口径偏差知情）**：THS 全部资金口径与主力口径同日符号可相反（DB 实证 600276/300146）。本批次按方案一原意"参与评分 + 标注 + 开关"。监理若改判"评分纯净优先"，可启用口径纯净开关（4 处单行修改），PM 将另发任务书。
4. **历史批次**：今日 16:14/20:11 批次不追溯（D-7 裁定）；用户可用既有 `generate_daily_report(force=True)` 能力手动重跑，无需新增脚本。
5. **港股**：不在本批次（D-6 裁定），维持腾讯 K 线估算兜底。
