# 开发任务书 019H — is_estimated 过滤补全（预警层）【v2 架构评审定稿】

**签发日期**：2026-08-05
**签发人**：PM
**版本**：v2（按架构师评审 M-1/M-2/M-3/R-1/R-2/R-3 修订）
**批次编号**：019H（019F 备查项后续，小批次修复）
**优先级**：P2（预警准确性）
**关联批次**：019E（资金面估算兜底与 EM 覆盖，已关闭）、019F（评分纯净修复，已关闭）、019G（同花顺交易日校验+时间展示优化，已关闭）
**关联评审项**：019F 架构评审 R-1 备查（alert_engine.py）、019F 验收标准第 5 条备查（app.py）
**架构评审**：✅ 有条件通过（评审报告：`docs/reviews/review_019H_is_estimated_20260805.md`，架构师独立复核定稿）

---

## 〇、执行窗口与流程说明

| 项目 | 说明 |
|---|---|
| 推荐窗口类型 | Quests 独立窗口（单代理执行） |
| 推荐模型 | 开发：glm5.2 → QA：kimi k3（验收类任务） |
| 执行模式 | ✅ 已关闭 |
| 流程路径 | ✅PM 签发 v1 → ✅架构师独立复核定稿 → ✅PM 修订 v2 → ✅监理批准 → ✅开发执行+自验 → ✅QA 独立验收（7/7 PASS）→ ✅PM+QA 双签 → ✅监理批准关闭（2026-08-05） |

---

## 一、背景

### 缺陷溯源

019E 批次建立了资金面估算兜底机制：东方财富采集失败时，降级使用新浪/腾讯/网易数据（基于成交额×涨跌幅估算）作为兜底，写入 `raw_capital_flow` 表并标记 `is_estimated=1`。估算值**仅供展示**，不得进入评分/评级计算。

019E / 019F 已完成**评分链路** 4 处 `is_estimated` 过滤闭合点：

| # | 文件 | 位置 | 用途 | 过滤状态 |
|---|---|---|---|---|
| 1 | `data_adapter.py` `_read_capital_data` | L282 | 主评分链路（StockData 构建） | ✅ 已过滤（019E） |
| 2 | `advisor.py` `_build_capital_factors` | L1126 | 顾问链路（资金因子构建） | ✅ 已过滤（019E） |
| 3 | `data_collector.py` 补采清单 | L1477 | 补采去重校验 | ✅ 已过滤（019E） |
| 4 | `analysis_engine.py` `_read_capital_data` | L132 | legacy v4 降级路径 | ✅ 已过滤（019F） |

以下备查项在 019F 评审时因"范围外"暂缓，现由监理指示正式立项处理。

#### 备查项 1（中风险）：alert_engine.py 预警缺 is_estimated 过滤

**文件**：`modules/alert_engine.py`
**函数**：`check_capital_outflow(cursor, stock_id, n_days=3)`（L183-232，**无下划线前缀**）
**问题**：连续净流出预警查询 `raw_capital_flow` 时无 `is_estimated` 过滤，估算值（基于涨跌幅×成交额的粗略推算）可能参与预警判定，导致**误报或漏报**。

```python
# 当前代码（L200-207）
cursor.execute(
    """SELECT trade_date, main_net_inflow
       FROM raw_capital_flow
       WHERE stock_id=?
       ORDER BY trade_date DESC
       LIMIT ?""",
    (stock_id, n_days * 2),
)
```

**风险分析**：
- 估算值精度远低于真实数据（成交额×涨跌幅是粗略代理），用于预警连续净流出判定时可能产生假信号
- 用户收到基于估算值的预警后可能做出错误交易决策
- 019F 架构评审 R-1 明确标注为"中风险，建议后续单独立项评估"

#### 备查项 2（低风险）：app.py 展示层 — 架构评审裁定"不改"

**文件**：`app.py`
**路由**：`/api/stocks/<int:stock_id>/capital`（L757-796）

**v1 任务书原文（已更正）**：
> ~~"当前 `templates/index.html` 资金面展示区域**未对估算行做任何视觉区分**"~~

**架构师独立核验更正（M-1/M-3）**：前端 `templates/index.html` 在 019E Task 4.1 中**已实现**估算行双层标注（表头级"含估算兜底数据"提示 L2481-2482 + 行级橙色"估算"上标标签 L2490）。展示层数据标注已完成，无需额外处理。且 019E 架构评审已裁定展示层"不过滤、前端标注"——直接过滤（方案 A）会导致时间序列缺口且推翻 019E 既定设计。**本项经架构评审裁定为"不改"**。

---

## 二、执行角色

**开发**（单人）

---

## 三、任务范围

> **v2 修订（M-1）：改动范围从 v1 的"两文件两处"收敛为"一文件一处"。**

### 任务 1：alert_engine.py 预警查询过滤补全（中风险）

**文件**：`modules/alert_engine.py`
**函数**：`check_capital_outflow(cursor, stock_id, n_days=3)`（L183-232）
**改动**：SQL 补一行过滤条件 + 注释

**改动前**（L200-207）：
```python
    cursor.execute(
        """SELECT trade_date, main_net_inflow
           FROM raw_capital_flow
           WHERE stock_id=?
           ORDER BY trade_date DESC
           LIMIT ?""",
        (stock_id, n_days * 2),
    )
```

**改动后**：
```python
    # 019H：过滤估算行（is_estimated=1），确保预警判定仅使用真实资金流数据
    cursor.execute(
        """SELECT trade_date, main_net_inflow
           FROM raw_capital_flow
           WHERE stock_id=?
           AND (is_estimated = 0 OR is_estimated IS NULL)
           ORDER BY trade_date DESC
           LIMIT ?""",
        (stock_id, n_days * 2),
    )
```

**约束**：
- 过滤条件必须与评分链路 4 处闭合点**逐字符一致**（同一表达式 `AND (is_estimated = 0 OR is_estimated IS NULL)`）
- **不得附加额外 SQL 条件**（如 `main_net_inflow IS NOT NULL`——NULL 行维持由 Python L214 `main_net_inflow is not None` 过滤处理）（红线补充 7）
- 函数签名 `check_capital_outflow(cursor, stock_id, n_days=3)` 不变
- 参数绑定 `(stock_id, n_days * 2)` 顺序不变；LIMIT 语义不变（红线补充 8）
- 不改动该函数其余任何代码

### 任务 2：app.py 展示层 —— 不改（架构评审 M-1 否决方案 A）

经架构师独立核验，前端 `index.html` L2481-2490 在 019E Task 4.1 中已实现估算行双层标注（表头"含估算兜底数据"+ 行级橙色"估算"上标），且 019E 架构评审已裁定展示层"不过滤、前端标注"。方案 A（直接过滤估算行）会导致时间序列缺口且推翻既定设计。**裁定：不修改 app.py 和 index.html**。

### 明确不改范围

- **`app.py`** — 不碰（展示层，前端已有标注，M-1）
- **`templates/index.html`** — 不碰（已有估算标注，M-1）
- **`modules/export_engine.py`** — 不碰（范围外备查，M-2，建议 019I 处理）
- **`modules/data_adapter.py`** — 不碰（019E 已完成过滤）
- **`modules/advisor.py`** — 不碰（019E 已完成过滤）
- **`modules/data_collector.py`** — 不碰（019E 已完成过滤，019F 已加 inspect.stack 保护）
- **`modules/analysis_engine.py`** — 不碰（019F 已完成过滤）
- **`modules/scoring_engine.py`** — 不碰
- **`modules/daily_report.py`** — 不碰
- **`database/db_manager.py`** — 不碰（`is_estimated` 列已在 019E 迁移就位，L963）
- **`config_weights.json` / `config_engine_switch.json` / `config.py`** — 不碰
- **`requirements.txt`** — 不碰（维持 9 包）

---

## 四、验收标准

1. **代码级核查（PM 独立核验，不采信开发自验）**：
   - `alert_engine.py` `check_capital_outflow` SQL 含 `AND (is_estimated = 0 OR is_estimated IS NULL)`（grep 该文件恰 1 处）
   - 过滤表达式与 `data_adapter.py` L282、`advisor.py` L1126、`analysis_engine.py` L132 **逐字符一致**（grep 比对）
2. **编译验证**：`python -m py_compile modules/alert_engine.py` 无错误
3. **预警纯净验证**（QA 重点，R-3 补正路径场景）：
   - **负路径**：写入 2 行真实数据（连续净流出但不足 3 日）+ 1 行 `is_estimated=1`（净流出）→ 断言**不触发**预警（估算行不计入窗口）
   - **正路径**：写入 3 行 `is_estimated=0` 连续净流出 + 其间穿插 1 行 `is_estimated=1` → 断言**正常触发**预警且 `total_outflow` 仅统计真实行（过滤未破坏真实路径）
4. **展示层回归验证**：调用 `/api/stocks/<id>/capital` → 确认估算行仍正常返回（`is_estimated=1` 行存在），前端 index.html L2490 估算标注正常显示（QA 截图核查）
5. **全仓 grep（闭合确认，R-2 口径修正）**：canonical 子串 `AND (is_estimated = 0 OR is_estimated IS NULL)` 命中 **6 处** = 4 处评分链路 + data_collector L1903（019E 既有 EM 前置校验变体）+ alert_engine L202（本批次新增）；展示层有意不过滤，不计入
6. **回归验证**：019F 隔离测试 `tests/qa_019f_isolation_test.py` 全通过（T8/T9 过滤表达式一致性 + 已有 4 处过滤未被破坏）
7. **零改动确认**：app.py、index.html、export_engine.py 及所有评分链路文件内容不变（QA 用文件哈希核查）

---

## 五、红线约束

1. **过滤表达式一致性红线**：新增过滤点必须与已有 4 处表达式 `AND (is_estimated = 0 OR is_estimated IS NULL)` **逐字符一致**，不得自创变体（如 `is_estimated != 1`）
2. **范围红线**：改动仅限 `modules/alert_engine.py`（1 处 SQL）
3. **签名红线**：`check_capital_outflow(cursor, stock_id, n_days=3)` 签名不变；`/api/stocks/<int:stock_id>/capital` 路由不碰
4. **零代码约束**：不引入新 pip 依赖（requirements.txt 维持 9 包）；无 schema 迁移
5. **评分纯净红线（不可回退）**：本批次改动不得影响已有 4 处评分链路过滤，QA 须跑 019F 回归测试确认
6. **展示层不动红线**：app.py / index.html / export_engine.py 一律不碰
7. **SQL 变体红线**（架构师补充）：Task 1 不得在 canonical 表达式外附加 `main_net_inflow IS NOT NULL` 等额外 SQL 条件（NULL 行维持由 Python L214 `main_net_inflow is not None` 过滤处理）
8. **参数绑定红线**（架构师补充）：`LIMIT ?` 位置不变，参数元组 `(stock_id, n_days * 2)` 顺序不变

---

## 六、执行顺序

```
Step 1: ✅ PM 签发 v1（2026-08-05）
Step 2: ✅ PM 越权初评（已声明）→ 架构师任务书签发
Step 3: ✅ 架构师独立复核定稿（有条件通过，M-1/M-2/M-3/R-1/R-2/R-3）
Step 4: ✅ PM 修订 v2（本次，按评审意见定稿）
Step 5: ✅ 监理批准（2026-08-05）
Step 6: ✅ 开发执行 + 自验（Quests 独立窗口）
Step 7: ✅ QA 独立验收（7/7 PASS，8/8 红线） → ✅ PM+QA 双签 → ✅ 监理批准关闭（2026-08-05）
```

---

> **PM 备注（v2）**：本批次经架构师独立复核，实际改动从 v1 的"两文件两处"收敛为"一文件一处"（仅 alert_engine.py L200-207）。v1 中 app.py 展示层方案 A 被否决的核心原因：前端已在 019E 实现估算标注（v1 任务书事实错误，M-1/M-3），且方案 A 违背 019E 已批准的"展示层不过滤、前端标注"架构裁定。架构师另发现函数名错误（v1 写 `_check_capital_outflow`，实际为 `check_capital_outflow`，R-1）、闭合数口径需精确化（6 处非 5 处，R-2）、验收需补正路径场景（R-3），均已纳入 v2。export_engine.py 导出层缺过滤为评审新发现（M-2），风险低，建议 019I 处理。
