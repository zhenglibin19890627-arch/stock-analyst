# 架构师评审报告：P3-B 智能预警

**评审编号**：ARCHITECT-REVIEW-20260727-P3B
**评审日期**：2026-07-27
**评审人**：AI 架构师
**评审对象**：`docs/tasks/dev_tasks_20260727_P3B.md`

---

## 评审结论

- **总体结论**：⚠️ 有条件通过
- **条件**：D2 表结构须按本评审草案执行；D3/D4 伪代码须按本评审修正后实现；D1 挂载方式须增加幂等保护。

| 决策点 | 结论 | 说明 |
|:---|:---|:---|
| D1 扫描挂载 | ⚠️ 调整建议 | 同意复用日报调度，但须增加幂等保护与异常隔离增强 |
| D2 表结构 | ⚠️ 调整建议 | is_read 放 history 表合理，但须补充索引与唯一约束 |
| D3 连续净流出算法 | ⚠️ 调整建议 | 缺失数据须"跳过"而非"中断"，窗口边界须明确为"含今天" |
| D4 评级跨档判定 | ⚠️ 调整建议 | 须复用 advisor 已有 normalize_rating 逻辑，避免重复实现 |
| D5 规则配置存储 | ✅ 同意PM倾向 | 新建 alert_rules 独立表正确，但须明确热加载机制 |

---

## D1 扫描挂载

### 结论
⚠️ 调整建议 — 同意复用 `daily_report._scheduler_tick`，但须增加幂等保护与异常隔离增强。

### 理由

1. **无需独立定时器**：G3 已明确"每日1次"，日报 18:00 生成后扫描是天然时序点。独立定时器会增加调度复杂度，且与日报数据就绪时序难以对齐。
2. **异常隔离须增强**：PM 任务书提及 `try/except` 隔离，但未明确隔离粒度。建议采用"双保险"：
   - 外层：`alert_engine.scan_once()` 整体 try/except，异常仅记日志不抛
   - 内层：单只股票扫描失败不阻塞其他股票
3. **幂等性必须显式设计**：同一天重复扫描（如手动触发日报重跑）会产生重复 alert_history，必须防护。

### 实现建议

```python
# modules/daily_report.py 修改点（_scheduler_tick 内）
def _scheduler_tick():
    global _scheduler_timer
    try:
        logger.info('定时调度器触发每日报告生成')
        generate_daily_report()

        # P3-B: 日报生成后挂载预警扫描（异常隔离）
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

**幂等保护设计**（须在 alert_engine.scan_once 内实现）：
- 扫描前检查 `alert_history` 是否已有今日同类型记录
- 采用 `INSERT OR IGNORE` 或先 SELECT 后 INSERT 模式
- 建议增加唯一约束：`(rule_id, stock_id, trigger_date)` 联合唯一

---

## D2 表结构

### 结论
⚠️ 调整建议 — is_read 放 history 表合理，但须补充索引与唯一约束。

### 评审意见

1. **is_read 放 history 表 vs 拆 alert_read_state 独立表**
   - **结论**：放 history 表字段更合理。理由：
     - 预警历史是"事件流"，已读状态是事件的自然属性
     - 拆表会增加 JOIN 复杂度，且零代码用户场景下无并发多设备同步需求
     - 单用户本地系统，无需考虑多用户已读状态隔离

2. **索引设计**
   - `alert_history` 高频查询场景：按 stock_id 查、按 triggered_at 排序、按 is_read 过滤
   - 必须建立复合索引：`(is_read, triggered_at DESC)` 用于未读列表
   - 必须建立索引：`(stock_id, triggered_at DESC)` 用于单只股票预警历史

3. **外键约束**
   - `alert_history.rule_id` 应关联 `alert_rules.id`，但项目全局 `PRAGMA foreign_keys=OFF`
   - 建议：应用层维护关联完整性，删除规则时级联删除或标记历史记录

4. **幂等唯一约束**
   - 防止同一天重复扫描产生重复记录：`(rule_id, stock_id, trigger_date)` 联合唯一

### 完整 CREATE TABLE 语句草案

```sql
-- ============================================================
-- 24. 预警规则表 —— P3-B: 智能预警规则配置
-- ============================================================
CREATE TABLE IF NOT EXISTS alert_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_type TEXT NOT NULL,              -- 规则类型: rating_change / score_below / capital_outflow
    stock_id INTEGER,                     -- 关联股票（NULL=全局默认规则）
    threshold REAL,                       -- 阈值参数（评分阈值/连续天数，按 rule_type 解释）
    enabled INTEGER DEFAULT 1,            -- 是否启用: 1=启用, 0=停用
    created_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    updated_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (stock_id) REFERENCES stocks(id)
);

-- 全局默认规则（stock_id=NULL）+ 个股自定义规则
-- 查询优先级：个股规则 > 全局规则

-- ============================================================
-- 25. 预警历史表 —— P3-B: 预警触发记录
-- ============================================================
CREATE TABLE IF NOT EXISTS alert_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    rule_id INTEGER NOT NULL,             -- 关联触发规则
    stock_id INTEGER NOT NULL,            -- 触发股票
    alert_type TEXT NOT NULL,             -- 预警类型: rating_change / score_below / capital_outflow
    trigger_value TEXT,                   -- 触发值详情（JSON格式，含旧值/新值/阈值等）
    message TEXT NOT NULL,                -- 人类可读预警消息
    is_read INTEGER DEFAULT 0,            -- 是否已读: 0=未读, 1=已读
    triggered_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    trigger_date DATE NOT NULL,           -- 触发日期（用于幂等去重）
    FOREIGN KEY (rule_id) REFERENCES alert_rules(id),
    FOREIGN KEY (stock_id) REFERENCES stocks(id),
    UNIQUE(rule_id, stock_id, trigger_date)  -- 幂等约束：同规则同股票同日不重复
);

-- 索引：未读预警列表（高频查询）
CREATE INDEX IF NOT EXISTS idx_alert_history_unread 
ON alert_history(is_read, triggered_at DESC);

-- 索引：单只股票预警历史
CREATE INDEX IF NOT EXISTS idx_alert_history_stock 
ON alert_history(stock_id, triggered_at DESC);

-- 索引：按日期查询
CREATE INDEX IF NOT EXISTS idx_alert_history_date 
ON alert_history(trigger_date);
```

### 默认规则初始化（建议）

```sql
-- 插入全局默认规则（幂等）
INSERT OR IGNORE INTO alert_rules (rule_type, stock_id, threshold, enabled) VALUES
('rating_change', NULL, NULL, 1),      -- 评级跨档：无阈值
('score_below', NULL, 65.0, 1),        -- 评分跌破：默认65分
('capital_outflow', NULL, 3, 1);       -- 连续净流出：默认3天
```

---

## D3 连续净流出算法

### 结论
⚠️ 调整建议 — 缺失数据须"跳过"而非"中断"，窗口边界须明确为"含今天"。

### 评审意见

1. **缺失数据天数处理**
   - **PM 倾向**："缺失天数不计入连续"
   - **架构师评审**：同意"跳过"策略，但须明确语义：
     - 取最近 N 个**有数据**的交易日，而非最近 N 个自然日
     - 若连续 5 天中第 3 天缺失，则取第 1、2、4、5、6 天判断
     - 避免"中断"语义导致频繁误触发

2. **N 天窗口边界**
   - **结论**：含今天（扫描日）。理由：
     - 预警目的是"当日盘后提醒"，今日数据已采集完成
     - 与日报生成时序一致：日报 18:00 生成后扫描，今日数据已就绪

3. **港股无两融数据、A股部分股票 main_net_inflow 缺失时的降级策略**
   - 港股：`raw_capital_flow` 表无港股数据（数据源限制），应直接跳过该规则检查
   - A股数据缺失：若某股票连续 5 个交易日以上无 `main_net_inflow` 数据，标记为"数据不足不触发"，并在 `trigger_value` 中记录原因

4. **与 advisor 已有逻辑对齐**
   - advisor.py L819-829 已有"连续净流出"计算逻辑（`consecutive` 因子），但实现为"同方向连续计数"
   - 预警模块应独立实现，但语义保持一致：净流出 = `main_net_inflow < 0`

### 核心判定伪代码

```python
def check_capital_outflow(stock_id: int, n_days: int = 3) -> dict | None:
    """
    检查主力资金连续净流出

    Args:
        stock_id: 股票ID
        n_days: 连续天数阈值（默认3）

    Returns:
        None: 未触发或数据不足
        dict: 触发详情 {
            'consecutive_days': int,      # 实际连续净流出天数
            'total_outflow': float,       # 累计净流出金额（万元）
            'latest_date': str,           # 最新数据日期
            'dates': list[str],           # 连续净流出日期列表
        }
    """
    # 1. 检查市场类型（港股无资金面数据，直接跳过）
    market = get_market_by_stock_id(stock_id)
    if market == 'hk_stock':
        return None  # 港股不支持此规则

    # 2. 查询最近 N*2 个交易日的资金面数据（考虑缺失，多取一些）
    rows = query_db(
        """
        SELECT trade_date, main_net_inflow 
        FROM raw_capital_flow 
        WHERE stock_id = ? 
        ORDER BY trade_date DESC 
        LIMIT ?
    """,
        (stock_id, n_days * 2),
    )

    if not rows:
        return None  # 无数据

    # 3. 过滤有效数据（main_net_inflow 非 None），取最近 N 个有数据的交易日
    valid_rows = [r for r in rows if r['main_net_inflow'] is not None]

    if len(valid_rows) < n_days:
        return None  # 数据不足，不触发

    recent_n = valid_rows[:n_days]

    # 4. 判定：最近 N 个有数据的交易日是否全部为净流出
    all_negative = all(r['main_net_inflow'] < 0 for r in recent_n)

    if not all_negative:
        return None

    # 5. 触发：构建返回详情
    total_outflow = sum(abs(r['main_net_inflow']) for r in recent_n)
    dates = [r['trade_date'] for r in recent_n]

    return {
        'consecutive_days': n_days,
        'total_outflow': round(total_outflow, 2),
        'latest_date': recent_n[0]['trade_date'],
        'dates': dates,
    }
```

---

## D4 评级跨档判定

### 结论
⚠️ 调整建议 — 须复用 advisor 已有 `normalize_rating` 逻辑，避免重复实现。

### 评审意见

1. **跨档判定是相邻两次对比，还是与指定基准日对比？**
   - **结论**：相邻两次对比。理由：
     - 与 advisor._save_rating 的 `is_changed` 逻辑保持一致
     - 用户感知的是"评级变动事件"，而非"相对某基准日的偏移"
     - 基准日对比会增加状态管理复杂度，无业务价值

2. **首次评级（无历史记录）如何处理？**
   - **结论**：不触发。理由：
     - 首次评级是"建立认知"，非"变动事件"
     - 与 advisor 逻辑一致：`prev_rating_norm is not None and prev_rating_norm != analysis['rating']`

3. **rating 字段是中文文本，如何定义"档位顺序"做升降级判定？**
   - **关键发现**：advisor.py 已有完整解决方案，预警模块**必须复用**而非重新实现
   - `scoring_engine.normalize_rating(rating_str, total_score)` 处理：
     - 新中文5档直接返回
     - 历史 A/B+/B/C/D 映射到新档位
     - 矛盾时优先使用评级字符串映射
   - 档位顺序定义（与 config_weights.json 对齐）：

   | 档位 | 分数区间 | 顺序值 |
   |:---|:---|:---:|
   | 强烈推荐买入 | 80-100 | 5 |
   | 推荐买入 | 65-79 | 4 |
   | 持有观望 | 50-64 | 3 |
   | 建议减仓 | 30-49 | 2 |
   | 强烈建议卖出 | 0-29 | 1 |

   - **升降级判定**：顺序值差 > 0 为升级，< 0 为降级，= 0 为同档（不触发）

4. **数据源选择**
   - advisor 使用 `ratings_history` 表，预警模块应读取同一数据源
   - 注意：`ratings_history` 已有 `is_change` 字段，但预警模块应独立判定（避免依赖 advisor 的写入时机）

### 核心判定伪代码

```python
# 档位顺序映射（与 config_weights.json rating_mapping 对齐）
RATING_ORDER = {
    '强烈推荐买入': 5,
    '推荐买入': 4,
    '持有观望': 3,
    '建议减仓': 2,
    '强烈建议卖出': 1,
}


def check_rating_change(stock_id: int) -> dict | None:
    """
    检查评级跨档变化

    Args:
        stock_id: 股票ID

    Returns:
        None: 未触发（首次评级或同档）
        dict: 触发详情 {
            'old_rating': str,       # 旧评级
            'new_rating': str,       # 新评级
            'old_score': float,      # 旧评分
            'new_score': float,      # 新评分
            'direction': str,        # 'upgrade' / 'downgrade'
            'level_change': int,     # 档位变化数（正=升级，负=降级）
        }
    """
    # 1. 查询最近两次评级记录
    rows = query_db(
        """
        SELECT rating, total_score, rating_date
        FROM ratings_history
        WHERE stock_id = ?
        ORDER BY rating_date DESC
        LIMIT 2
    """,
        (stock_id,),
    )

    if len(rows) < 2:
        return None  # 首次评级或历史不足，不触发

    latest = rows[0]
    previous = rows[1]

    # 2. 复用 scoring_engine 的归一化逻辑（关键：必须复用，避免重复实现）
    from modules.scoring_engine import normalize_rating

    old_rating_norm = normalize_rating(previous['rating'], previous['total_score'])
    new_rating_norm = normalize_rating(latest['rating'], latest['total_score'])

    # 3. 同档判定
    if old_rating_norm == new_rating_norm:
        return None

    # 4. 档位顺序与升降级判定
    old_order = RATING_ORDER.get(old_rating_norm, 0)
    new_order = RATING_ORDER.get(new_rating_norm, 0)
    level_change = new_order - old_order

    if level_change == 0:
        return None  # 映射后同档（如旧格式映射后与新格式相同）

    return {
        'old_rating': old_rating_norm,
        'new_rating': new_rating_norm,
        'old_score': previous['total_score'],
        'new_score': latest['total_score'],
        'direction': 'upgrade' if level_change > 0 else 'downgrade',
        'level_change': level_change,
    }
```

---

## D5 规则配置存储

### 结论
✅ 同意PM倾向 — 新建 `alert_rules` 独立表正确，但须明确热加载机制。

### 评审意见

1. **是否复用现有 strategy_params 表？**
   - **结论**：不复用，新建独立表正确。理由：
     - `strategy_params` 设计为"市场级参数"（market + param_type + param_key），无 stock_id 维度
     - 预警规则需要"个股级自定义"（某只股票单独设置阈值），strategy_params 结构不支持
     - 职责分离：strategy_params 服务于评分引擎，alert_rules 服务于预警引擎

2. **配置热加载机制**
   - **结论**：无需重启服务，每次扫描时实时读取。理由：
     - 预警扫描每日仅执行 1 次，性能要求极低
     - 规则配置变更后，下次扫描自动生效（无需缓存）
     - 与 config_weights.json 的"修改后无需重启"设计哲学一致

3. **规则优先级设计**
   - 查询顺序：先查个股规则（stock_id 匹配），无则查全局规则（stock_id IS NULL）
   - 伪代码：
   ```python
   def get_active_rule(rule_type: str, stock_id: int) -> dict | None:
       # 先查个股规则
       rule = query_one(
           """
           SELECT * FROM alert_rules 
           WHERE rule_type = ? AND stock_id = ? AND enabled = 1
       """,
           (rule_type, stock_id),
       )
       if rule:
           return rule
       #  fallback 到全局规则
       return query_one(
           """
           SELECT * FROM alert_rules 
           WHERE rule_type = ? AND stock_id IS NULL AND enabled = 1
       """,
           (rule_type,),
       )
   ```

4. **API 设计建议**
   - 规则 CRUD 接口应校验 rule_type 合法性（仅允许 3 种类型）
   - 删除规则时建议软删除（enabled=0）而非物理删除，保留历史关联

---

## 附加风险提示

### R1：日报复用逻辑与预警扫描的时序竞争
- **风险**：`generate_daily_report` 有 `_generate_lock` 防抖，若日报正在生成中，预警扫描可能读取到部分写入的数据
- **建议**：预警扫描应在日报生成**完成后**执行（当前设计已满足），且扫描过程不加锁（只读操作）

### R2：ratings_history 数据延迟
- **风险**：`ratings_history` 由 advisor.generate_advice 写入，若某股票当日未触发分析（如数据不足），则无新评级记录
- **建议**：预警模块应容忍此场景，"无新评级"不等于"评级未变化"，仅当存在两条以上记录时才做跨档判定

### R3：主力资金数据缺失的误报风险
- **风险**：若某股票长期无 `main_net_inflow` 数据（如停牌），"连续净流出"规则可能因数据不足而长期不触发，用户可能误解为"无异常"
- **建议**：预警消息中应明确标注数据覆盖范围，如"基于最近3个有数据交易日"

### R4：前端铃铛的轮询频率
- **风险**：PM 任务书未明确前端未读数更新机制（轮询/WebSocket/手动刷新）
- **建议**：零代码场景下采用"页面可见时轮询"（visibilitychange + setInterval），频率 30-60 秒，避免频繁请求

### R5：alert_history 表膨胀
- **风险**：长期运行后预警历史表可能积累大量记录，影响查询性能
- **建议**：预留清理机制（如保留最近 90 天），或在前端提供"清空历史"功能

---

## 评审通过条件

1. D2 表结构按本评审草案执行（含索引与唯一约束）
2. D3 伪代码按"跳过缺失数据"语义实现
3. D4 必须复用 `scoring_engine.normalize_rating`，不得重新实现评级映射
4. D1 挂载点增加幂等保护（唯一约束 + INSERT OR IGNORE）
5. 所有 API 实现前须通过 QA 的接口契约评审

---

**评审人签字**：AI 架构师
**日期**：2026-07-27
**状态**：待监理批准
