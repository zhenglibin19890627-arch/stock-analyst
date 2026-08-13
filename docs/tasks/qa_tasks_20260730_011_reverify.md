# QA-TASKS-20260730-011-REVERIFY：011 时区Bug修复复验

> **签发人**：PM  | **签发日期**：2026-07-30 | **状态**：待QA执行
> **关联**：QA首验报告 `reports/qa_accept_011_incremental_20260730.md`（Q2/Q3 FAIL）
> **关联**：开发自验 `reports/dev_selftest_011_hotfix_20260730.md`（V1-V4 PASS）

---

## 角色定义（内嵌，无需额外窗口提示词）

### 你的角色：QA

**职责边界**：
- 独立复验 011-Hotfix 修复的 3 个时区 Bug
- 独立设计验证方法（不依赖开发自验报告结论）
- 交付复验报告 `reports/qa_reverify_011_hotfix_20260730.md`
- **不修改代码**

### 独立性原则
- QA 不依赖开发自验报告，独立设计测试+独立执行
- QA 不修改代码，仅做验证

### 项目背景摘要
| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（含空格） |
| 数据库路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db`（在stock_analyst子目录内！） |
| Python 解释器 | `C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 单页应用 |
| 最高约束 | **零代码用户可独立运行**：无新 pip 依赖（当前8包） |

---

## 执行信息（PM 标注）

| 项 | 内容 |
|---|---|
| 任务类型 | QA复验（只读验证，不改代码） |
| 推荐模型 | **glm5.2 / qwen3.8**（并列优先） |
| 窗口类型 | **Quests 独立窗口** |
| 执行模式 | 单代理 agent |
| 预计耗时 | 15分钟 |
| 交付物 | `reports/qa_reverify_011_hotfix_20260730.md` |

---

## 一、复验范围

首验中 3 个 FAIL 项 + 2 个附带发现的 BUG，共需复验 **4 项**：

| 复验项 | 对应首验 | 验证目标 |
|--------|---------|---------|
| R1 | Q2 (BUG-1) | A股基本面80天财报门控生效 |
| R2 | Q3 (BUG-1连锁) | PE/PB 24h门控生效（双门控联合跳过） |
| R3 | BUG-2 | 港股基本面80天门控生效 |
| R4 | BUG-3 | 融资余额增量补取正常（不抛TypeError） |

---

## 二、复验方法建议（QA可自主调整）

### R1：A股80天财报门控

**测试对象**：000333 美的集团（DB中 report_date 在80天内）

**验证步骤**：
1. 查询 DB 确认 000333 最新 report_date（应 < 80天前）
2. 调用 `fetch_a_fundamental('000333')`（force_full=False）
3. 检查返回值含"跳过"字样
4. 检查日志**无** WARNING "基本面增量检查异常(降级为全量)"

**通过标准**：返回 `('success', '同日跳过(财报80天TTL内+PE/PB 24h内)')` 或含"跳过"

### R2：PE/PB 24h门控

**验证步骤**：
1. 确认 data_status 表中 000333 fundamental 的 fetched_at 在24h内（R1执行后会更新）
2. 再次调用 `fetch_a_fundamental('000333')`
3. 检查返回含双门控跳过字样

**通过标准**：返回含"财报80天TTL内+PE/PB 24h内"

### R3：港股80天门控

**测试对象**：HK3690 美团-W（DB中 report_date 在80天内）

**验证步骤**：
1. 调用 `fetch_hk_fundamental('HK3690')`（force_full=False）
2. 检查返回值含"跳过"字样
3. 检查日志**无** WARNING "基本面增量检查异常"

**通过标准**：返回 `('success', '同日跳过(港股财报X天内)')`

### R4：融资余额增量

**测试对象**：600276 恒瑞医药（DB中已有 margin 数据）

**验证步骤**：
1. 调用 `fetch_margin_balance('600276', 'a_stock')`（force_full=False）
2. 确认不抛 TypeError
3. 确认返回 success 或 skipped（非 failed）

**通过标准**：返回 `('success', ...)` 或 `('skipped', ...)`，不含"失败"

---

## 三、附加验证（可选）

| 项 | 说明 |
|---|---|
| force_full 仍可绕过 | 调用 `fetch_a_fundamental('000333', force_full=True)` 应全量采集 |
| 首验PASS项不受影响 | 抽检 Q1(K线跳过) 或 Q4(消息面跳过) 仍正常 |

---

## 四、复验报告格式

```markdown
# QA复验报告：011-Hotfix 时区Bug修复

> 验收人：QA | 日期：2026-07-30 | 状态：✅通过 / ❌不通过

## 复验结果

| 项 | 结论 | 证据摘要 |
|---|---|---|
| R1 A股80天门控 | PASS/FAIL | ... |
| R2 PE/PB 24h门控 | PASS/FAIL | ... |
| R3 港股80天门控 | PASS/FAIL | ... |
| R4 融资余额增量 | PASS/FAIL | ... |

## 总结论
（全部PASS → 建议PM+QA双签关闭011）
```

---

## 五、后续流程

```
[当前] QA复验 → 全PASS → PM+QA双签 → 监理批准关闭011
                  → 有FAIL → 退回开发重修
```

---

> **PM 备注**：本任务书已内嵌角色定义，监理可直接全文粘贴到 Quests 窗口执行。
