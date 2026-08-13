# 开发任务书 P3-B：智能预警模块

**编号**：DEV-TASKS-20260727-P3B
**批次**：P3-B（团队重组后首个编码批次）
**流程**：PM签发 → 架构师独立评审 → 监理批准 → 开发编码+自验 → QA独立验收 → PM+QA双签 → 监理关闭
**签发日期**：2026-07-27
**签发人**：AI 产品经理
**监理批准**：2026-07-27（G1-G3 三项决策已确认）

---

## 一、任务卡

| 项 | 内容 |
|---|---|
| **任务名** | 智能预警模块（站内通知铃铛 + 3类预警规则） |
| **需求映射** | `requirements_v1.1.md` §2.7.1 第198行「评级变化预警」+ 监理2026-07-26 批准方案A（含评分跌破阈值/连续资金净流出增强） |
| **推荐开发模型** | glm5.2（GLM Plan）— 中等后端+前端，含 DB建表/定时器/Flask API/前端铃铛 |
| **目标用户** | 零代码个人投资者（A股+港股），低频交易 |

---

## 二、监理已决策项（G1-G3）

| # | 决策项 | 监理选择 | 说明 |
|---|---|---|---|
| **G1** | 预警范围 | **3种规则** | ①评级跨档变化 ②评分跌破阈值 ③主力资金连续净流出 |
| **G2A** | 评分跌破默认阈值 | **65分** | 跌破"推荐买入"下沿即触发；用户可在规则配置中修改 |
| **G2B** | 连续净流出默认天数 | **3天** | 主力资金连续3日净流出即触发；用户可修改 |
| **G3** | 扫描频率 | **每日1次** | 挂载于日报调度，日报生成后扫描，无需独立高频定时器 |

> 以上为最终决策，开发以本表为准。架构师评审的 D1-D5 技术实现方案以架构师评审结论为准（见 §五）。

---

## 三、功能拆解（F1-F5）

| # | 子功能 | 详细说明 |
|---|---|---|
| **F1** | 预警规则引擎 | 3类规则：<br>①**评级跨档变化**：对比最新 ratings_history 与上次，评级档位变化（升级/降级）即触发<br>②**评分跌破阈值**：latest analysis_results.total_score < 规则配置的阈值（默认65）即触发<br>③**主力连续净流出**：raw_capital_flow.main_net_inflow 连续N天（默认3）为负即触发<br>每条规则支持启用/停用、阈值可配置 |
| **F2** | 数据流（定时扫描） | 复用 `daily_report.py` 的 `threading.Timer` 调度链，在日报生成完成后触发一次预警扫描。**只读消费** ratings_history / analysis_results / raw_capital_flow，扫描失败用 try/except 隔离，不阻塞日报主流程 |
| **F3** | 存储（2张新表） | `alert_rules`（规则配置表：rule_type/stock_id/threshold/enabled/created_at）<br>`alert_history`（触发历史表：rule_id/stock_id/alert_type/trigger_value/message/is_read/triggered_at）<br>表结构以架构师评审结论为准（见D2） |
| **F4** | 后端 API | `/api/alerts/rules` GET/POST（规则列表/新增）、`/api/alerts/rules/<id>` PUT/DELETE（修改/删除）、`/api/alerts/unread` GET（未读列表）、`/api/alerts/<id>/read` POST（标记已读）、`/api/alerts/read-all` POST（全部已读） |
| **F5** | 前端铃铛 | `templates/index.html` 导航栏右上角加通知铃铛图标：未读数红点徽标 + 点击下拉通知列表 + 单条点击已读 + "全部已读"按钮。纯 JS/CSS 实现，不引外部组件 |

---

## 四、涉及文件

| 文件 | 改动类型 | 红线状态 |
|---|---|---|
| `modules/alert_engine.py` | **新建**（规则引擎 + 扫描器） | — |
| `database/db_manager.py` | **改**（create_tables 末尾追加2张新表 CREATE） | 不改已有表结构 |
| `modules/daily_report.py` | **改**（_scheduler_tick 日报后挂载预警扫描钩子） | 仅追加调用，不破坏现有日报逻辑 |
| `app.py` | **改**（新增 /api/alerts/* 路由组） | 追加路由，不改已有路由 |
| `templates/index.html` | **改**（导航栏铃铛 + JS/CSS） | 追加内容，不改已有功能区 |
| `modules/scoring_engine.py` | **不改** | 🛑 v5引擎红线 |
| `modules/advisor.py` | **不改** | 🛑 B24红线 |
| `config_weights.json` | **不改** | 🛑 红线 |
| `modules/data_collector.py` | **不改** | 🛑 L1645/L1684/L1717 红线 |
| `requirements.txt` | **不改** | 🛑 零代码约束（无新依赖） |

---

## 五、待架构师评审决策点（D1-D5）

> ⚠️ 开发编码以架构师评审结论为准，PM倾向仅供参考。

| # | 决策点 | PM倾向 | 架构师需评审 |
|---|---|---|---|
| **D1** | 扫描挂载方式 | 复用 daily_report._scheduler_tick，日报后调用 alert_engine.scan_once() | 是否需独立定时器、是否需异常隔离增强、幂等性设计 |
| **D2** | 表结构设计 | 新建 alert_rules + alert_history 两表，is_read 放 history 字段 | 是否拆 alert_read_state 独立表、索引设计、外键约束 |
| **D3** | "连续净流出"算法 | 取最近N天 main_net_inflow，全为负则触发；缺失天数不计入连续 | 缺失数据处理、N天窗口边界、港股无两融数据的处理 |
| **D4** | 评级跨档判定 | 对比 latest ratings_history.rating 与同 stock_id 上一条记录的 rating，档位不同即触发 | 跨档判定严格性（相邻两次 vs 指定基准日）、首次无历史记录的处理 |
| **D5** | 规则配置存储 | 新建 alert_rules 独立表（职责清晰） | 是否复用 strategy_params、配置热加载机制 |

---

## 六、验收标准（V1-V8）

| # | 验收项 | 标准 | 验收方 |
|---|---|---|---|
| V1 | 建表成功 | alert_rules / alert_history 表存在，字段完整，重复执行 create 不报错（IF NOT EXISTS） | QA |
| V2 | 3类规则可触发 | 构造测试数据分别触发3类规则，均产生 alert_history 记录，message 含触发详情 | QA |
| V3 | 扫描不破坏日报 | 挂载预警扫描后，日报18:00仍正常生成；扫描异常不阻塞日报（try/except 隔离验证） | QA |
| V4 | API 全通 | 6个接口（规则CRUD + 未读查询 + 标记已读 + 全部已读）返回正确状态码与JSON | QA |
| V5 | 前端铃铛交互 | 未读数红点显示/消失、下拉列表、单条已读、全部已读均生效 | QA |
| V6 | 红线全守 | scoring_engine/advisor/config_weights/data_collector 零修改（Grep 核验） | PM |
| V7 | 零代码约束 | requirements.txt 无新依赖；python app.py 一键启动可用 | PM |
| V8 | 不回写引擎 | 预警扫描仅 SELECT 源表，无 INSERT/UPDATE 到 ratings_history/analysis_results（代码审查） | PM |

---

## 七、红线清单

| 红线 | 说明 |
|---|---|
| data_collector.py L1645/L1684/L1717 | 三处 if False 硬禁用，绝对不改 |
| config_weights.json | 写入必须无 BOM；本批次不改此文件 |
| 零代码约束 | 无新 pip 依赖（requirements.txt 不变） |
| rating_mapping 80/65/50/30 | 已确定，不改 |
| advisor.py | B24 禁止修改红线（generate_advice 主入口） |
| scoring_engine.py | v5引擎，不改（预警只读消费其产出的数据） |

---

## 八、自验要求

开发完成后，开发人员须出具自验报告（归档 `reports/dev_selftest_P3B.md`），覆盖：
1. V1-V8 逐项自验结果（含执行命令与输出）
2. 3类规则触发的测试数据构造说明与截图（前端铃铛）
3. 红线核验（Grep 证明关键文件未改）
4. 任务蔓延自评（是否超出 F1-F5 范围）

---

## 九、文档流转

```
本任务书(dev_tasks_20260727_P3B.md)
  → 架构师评审(architect_review_P3B.md)
  → 监理批准编码
  → 开发提示词(dev_prompt_P3B.md)
  → 开发自验(dev_selftest_P3B.md)
  → QA验收(qa_accept_P3B.md)
  → PM验收(pm_accept_P3B.md)
  → 监理批准关闭
```

**当前状态：任务书已签发，待架构师独立评审。**
