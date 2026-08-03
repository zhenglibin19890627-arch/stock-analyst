# 开发提示词 P3-B：智能预警模块

**推荐模型：glm5.2（GLM Plan）— 中等后端+前端，含 DB建表/定时器/Flask API/前端铃铛**
**任务书：docs/tasks/dev_tasks_20260727_P3B.md**
**架构师评审：docs/reviews/review_alert_P3B_20260727.md（⚠️ 有条件通过，技术方案以本评审为准）**
**监理批准日期：2026-07-27**

---

## 项目环境

- **项目路径**：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（路径含空格，PowerShell 需引号）
- **技术栈**：Python + Flask + SQLite + akshare + Jinja2 单页应用
- **Python 解释器**：`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`
- **最高约束**：零代码用户可独立运行（pip install → python app.py → 浏览器打开即用）

---

## 监理已决策项（G1-G3，不可更改）

| # | 决策项 | 监理选择 |
|---|---|---|
| G1 | 预警范围 | **3种规则**：评级跨档变化 / 评分跌破65 / 主力连续3日净流出 |
| G2A | 评分跌破默认阈值 | **65分**（可配置） |
| G2B | 连续净流出默认天数 | **3天**（可配置） |
| G3 | 扫描频率 | **每日1次**（挂载日报调度，日报后执行） |

---

## 你的任务（F1-F5）

### F1：新建 modules/alert_engine.py（规则引擎 + 扫描器）

实现 `scan_once()` 入口函数 + 3个规则检查函数。**以下技术方案来自架构师评审，必须严格遵循。**

#### F1.1 scan_once() 主流程

```python
def scan_once():
    """预警扫描入口（每日日报后调用1次）
    
    要求：
    1. 扫描所有 enabled=1 的规则
    2. 规则查询优先级：个股规则(stock_id匹配) > 全局规则(stock_id IS NULL)
    3. 幂等：同规则同股票同日已触发则跳过（依赖 alert_history 唯一约束）
    4. 单只股票失败不阻塞其他股票（双层异常隔离）
    """
```

#### F1.2 规则1：评级跨档变化（rating_change）

**必须复用** `scoring_engine.normalize_rating()`（位于 scoring_engine.py L77，已验证存在），**不得重新实现评级映射逻辑**。

```python
from modules.scoring_engine import normalize_rating

# 档位顺序映射（与 config_weights.json rating_mapping 80/65/50/30 对齐）
RATING_ORDER = {
    '强烈推荐买入': 5,
    '推荐买入': 4,
    '持有观望': 3,
    '建议减仓': 2,
    '强烈建议卖出': 1,
}

def check_rating_change(stock_id):
    # 1. 查询 ratings_history 最近2条记录（ORDER BY rating_date DESC LIMIT 2）
    # 2. 不足2条 → 首次评级，不触发（return None）
    # 3. normalize_rating(old.rating, old.total_score) vs normalize_rating(new.rating, new.total_score)
    # 4. 同档（映射后相等）→ 不触发
    # 5. 不同档 → 触发，direction='upgrade'/'downgrade'，level_change=新顺序-旧顺序
    # 返回 dict: {old_rating, new_rating, old_score, new_score, direction, level_change}
```

#### F1.3 规则2：评分跌破阈值（score_below，默认65）

```python
def check_score_below(stock_id, threshold=65):
    # 1. 查询 analysis_results 最新一条（ORDER BY analysis_date DESC LIMIT 1）
    # 2. 无记录 → 不触发
    # 3. total_score < threshold → 触发
    # 返回 dict: {score, threshold} 或 None
```

#### F1.4 规则3：主力连续净流出（capital_outflow，默认3天）

**架构师明确**：取最近 N 个**有数据**的交易日（非自然日），缺失数据"跳过"不"中断"。窗口含今天。

```python
def check_capital_outflow(stock_id, n_days=3):
    # 1. 港股(stock_id对应symbol以HK开头) → 直接return None（无两融数据）
    # 2. 查询 raw_capital_flow 最近 n_days*2 个交易日，ORDER BY trade_date DESC
    # 3. 过滤 main_net_inflow IS NOT NULL 的行，取前 n_days 个
    # 4. 不足 n_days → 数据不足，不触发
    # 5. 全部 < 0 → 触发
    # 返回 dict: {consecutive_days, total_outflow, latest_date, dates} 或 None
```

---

### F2：改 database/db_manager.py（新增2张表）

**在 create_tables 函数末尾追加**（不改已有表），使用架构师评审的完整 SQL：

```sql
-- 24. 预警规则表
CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type TEXT NOT NULL,              -- rating_change / score_below / capital_outflow
    stock_id INTEGER,                     -- NULL=全局默认规则
    threshold REAL,                       -- 阈值（评分阈值/连续天数，按 rule_type 解释）
    enabled INTEGER DEFAULT 1,            -- 1=启用, 0=停用
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (stock_id) REFERENCES stocks(id)
);

-- 25. 预警历史表
CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,
    stock_id INTEGER NOT NULL,
    alert_type TEXT NOT NULL,             -- rating_change / score_below / capital_outflow
    trigger_value TEXT,                   -- JSON格式详情
    message TEXT NOT NULL,                -- 人类可读消息
    is_read INTEGER DEFAULT 0,            -- 0=未读, 1=已读
    triggered_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    trigger_date DATE NOT NULL,           -- 幂等去重用
    FOREIGN KEY (rule_id) REFERENCES alert_rules(id),
    FOREIGN KEY (stock_id) REFERENCES stocks(id),
    UNIQUE(rule_id, stock_id, trigger_date)  -- 幂等约束
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_alert_history_unread ON alert_history(is_read, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_history_stock ON alert_history(stock_id, triggered_at DESC);
CREATE INDEX IF NOT EXISTS idx_alert_history_date ON alert_history(trigger_date);
```

**建表后插入全局默认规则（幂等，用 INSERT OR IGNORE）**：
```sql
INSERT OR IGNORE INTO alert_rules (rule_type, stock_id, threshold, enabled) VALUES
('rating_change', NULL, NULL, 1),
('score_below', NULL, 65.0, 1),
('capital_outflow', NULL, 3, 1);
```

---

### F3：改 modules/daily_report.py（挂载预警扫描钩子）

在 `_scheduler_tick` 函数内，`generate_daily_report()` 调用**之后**追加（双层异常隔离）：

```python
def _scheduler_tick():
    global _scheduler_timer
    try:
        logger.info('定时调度器触发每日报告生成')
        generate_daily_report()

        # P3-B: 日报生成后挂载预警扫描（异常隔离，不阻塞日报）
        try:
            from modules.alert_engine import scan_once

            scan_once()
        except Exception as e:
            logger.error(f'P3-B 预警扫描异常（不阻塞日报）: {e}', exc_info=True)

    except Exception as e:
        logger.error(f'定时调度器执行异常: {e}', exc_info=True)
    finally:
        _schedule_next()
```

**注意**：仅追加预警扫描调用，不修改已有日报逻辑。

---

### F4：改 app.py（新增 /api/alerts/* 路由组）

在现有路由**之后追加**（不改已有路由），实现6个接口：

| 接口 | 方法 | 功能 |
|---|---|---|
| `/api/alerts/rules` | GET | 查询全部规则列表 |
| `/api/alerts/rules` | POST | 新增规则（校验 rule_type 仅3种） |
| `/api/alerts/rules/<id>` | PUT | 修改规则（threshold/enabled） |
| `/api/alerts/rules/<id>` | DELETE | 删除规则（建议软删除 enabled=0） |
| `/api/alerts/unread` | GET | 查询未读预警列表（is_read=0，按 triggered_at DESC） |
| `/api/alerts/<id>/read` | POST | 标记单条已读 |
| `/api/alerts/read-all` | POST | 全部标记已读 |

所有接口返回 JSON，遵循 app.py 现有路由的返回格式风格。

---

### F5：改 templates/index.html（导航栏铃铛）

在导航栏右上角追加通知铃铛：
- 铃铛图标 + 未读数红点徽标（无未读时隐藏）
- 点击展开下拉通知列表（显示最近20条）
- 每条通知：股票名 + 预警类型 + 触发详情 + 时间
- 单条点击 → 标记已读并移除红点
- "全部已读"按钮 → 批量标记

**前端刷新机制**（架构师R4建议）：页面可见时轮询（visibilitychange + setInterval 60秒），页面隐藏时停止轮询。纯 JS/CSS 实现，不引外部组件。

---

## 红线清单（绝对遵守）

| 红线 | 要求 |
|---|---|
| `modules/scoring_engine.py` | **不改代码**。仅 `import normalize_rating` 使用（D4要求复用） |
| `modules/advisor.py` | **不改**（B24红线） |
| `config_weights.json` | **不改** |
| `modules/data_collector.py` | **不改**（L1645/L1684/L1717 if False 红线） |
| `requirements.txt` | **不改**（零代码约束，无新依赖） |
| `rating_mapping 80/65/50/30` | **不改** |

---

## 验收标准（V1-V8）

| # | 验收项 | 标准 | 验收方 |
|---|---|---|---|
| V1 | 建表成功 | alert_rules/alert_history 表存在，字段完整，重复执行不报错 | QA |
| V2 | 3类规则可触发 | 构造测试数据分别触发3类规则，均产生 alert_history 记录 | QA |
| V3 | 扫描不破坏日报 | 挂载预警后日报仍正常生成；扫描异常不阻塞日报 | QA |
| V4 | API 全通 | 7个接口返回正确状态码与JSON | QA |
| V5 | 前端铃铛交互 | 红点/列表/已读/全部已读均生效 | QA |
| V6 | 红线全守 | scoring_engine/advisor/config_weights/data_collector 零修改 | PM |
| V7 | 零代码约束 | requirements.txt 无新依赖；python app.py 一键启动 | PM |
| V8 | 不回写引擎 | 预警仅 SELECT 源表，无 INSERT/UPDATE 到 ratings_history/analysis_results | PM |

---

## 自验要求

开发完成后，出具自验报告 `reports/dev_selftest_P3B.md`，覆盖：
1. V1-V8 逐项自验结果（含执行命令与输出）
2. 3类规则触发的测试数据构造说明 + 前端铃铛截图
3. 红线核验（用 Grep 证明 scoring_engine/advisor/config_weights/data_collector 未改）
4. 任务蔓延自评（是否超出 F1-F5 范围）

---

## 关键文件参考

| 文件 | 用途 |
|---|---|
| `database/db_manager.py` | 建表位置（现有15张表，追加2张） |
| `modules/daily_report.py` | 调度挂载点（_scheduler_tick 函数） |
| `modules/scoring_engine.py` L77 | normalize_rating 函数（import 使用，不改） |
| `app.py` | 路由追加位置（~3100行） |
| `templates/index.html` | 铃铛追加位置（导航栏，~5150行） |

---

## 完成后

1. 自验报告归档 `reports/dev_selftest_P3B.md`
2. 通知 PM 与 QA 进行验收
