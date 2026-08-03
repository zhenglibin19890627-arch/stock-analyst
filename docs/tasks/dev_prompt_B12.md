# 开发提示词 B12：回测评级准确率修复

> **用途：** 在独立 Quests 窗口中粘贴给开发 AI，执行 B12 任务书。
> **任务书：** `docs/tasks/dev_tasks_2026-07-25_B12.md`
> **监理已批准：** 2026-07-25，3 项决策均按 PM 建议执行。

---

## 你的角色

你是 Stock Analyst 项目的开发工程师。请严格按照下方任务卡执行，**不得超出范围**（任务蔓延红线）。完成后输出自验报告。

## 项目基本信息

| 项 | 内容 |
|---|---|
| 项目路径 | `C:\Users\zlb19\Desktop\Qoder cn\stock_analyst` |
| 技术栈 | Python + Flask + SQLite + akshare + Jinja2 |
| DB 路径 | 项目根目录 `stock_analyst.db`（非 database/ 子目录） |
| 最高约束 | 零代码用户可独立运行：不引入新 pip 依赖 |

## 环境注意事项

- PowerShell 不支持 `&&`，用 `;` 分隔命令
- 项目路径含空格（`Qoder cn`），PowerShell 中需引号包裹
- 执行 Python 多行逻辑：**必须写临时 .py 文件再执行**（避免 PowerShell 引号转义）
- 临时测试文件用完后删除（`_dev_*.py`）
- `config_weights.json` 写入必须无 BOM（用 json.dump）

## 绝对红线（不可触碰）

1. `modules/data_collector.py` **L1645 / L1684 / L1717** 三处 `if False` 块，**绝对不可改为 True**
2. 不引入新 pip 依赖
3. 不修改 `config_weights.json` 的权重值
4. 不修改 `modules/backtest_engine.py` 的 `_judge()` 函数和 `JUDGEMENT_MATRIX`
5. 不修改 `modules/news_collector.py`

---

## 执行顺序（必须严格按序）

```
T1 → T2 → T3 → T4 → T5 → T6(可选)
```

---

## T1：ratings_history 去重 + UNIQUE 约束（P0）

### 问题
`ratings_history` 表无 UNIQUE 约束，`INSERT OR REPLACE` 退化为纯 INSERT，导致同一 stock_id+rating_date 出现大量重复记录（如 600276 在 07-16 有 32 条）。

### 修改文件
- `database/db_manager.py`（L214-226，ratings_history 建表 DDL）
- `modules/advisor.py`（L400-423，`_save_rating()` 函数）

### 具体操作

**步骤 1：修改建表 DDL**

在 `database/db_manager.py` 的 `ratings_history` 建表语句中，在 `FOREIGN KEY` 之前添加 UNIQUE 约束：

```python
# 修改前（L214-226）：
cursor.execute("""
    CREATE TABLE IF NOT EXISTS ratings_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER NOT NULL,
        rating_date DATE NOT NULL,
        rating TEXT NOT NULL,
        total_score REAL NOT NULL,
        action_advice TEXT,
        is_change INTEGER DEFAULT 0,
        price_at_rating REAL,
        FOREIGN KEY (stock_id) REFERENCES stocks(id)
    )
""")

# 修改后：添加 UNIQUE(stock_id, rating_date)
cursor.execute("""
    CREATE TABLE IF NOT EXISTS ratings_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        stock_id INTEGER NOT NULL,
        rating_date DATE NOT NULL,
        rating TEXT NOT NULL,
        total_score REAL NOT NULL,
        action_advice TEXT,
        is_change INTEGER DEFAULT 0,
        price_at_rating REAL,
        UNIQUE(stock_id, rating_date),
        FOREIGN KEY (stock_id) REFERENCES stocks(id)
    )
""")
```

**步骤 2：添加迁移逻辑**

在 `db_manager.py` 的 `init_db()` 函数末尾（所有建表语句之后），添加迁移代码：

```python
# B12-T1: ratings_history 去重迁移（幂等）
try:
    # 检查是否已有唯一索引
    cursor.execute('PRAGMA index_list(ratings_history)')
    indexes = [row[1] for row in cursor.fetchall()]  # row[1] = index name
    if 'idx_ratings_unique' not in indexes:
        # 先清理重复数据：每组 (stock_id, rating_date) 保留 id 最大的
        cursor.execute("""
            DELETE FROM ratings_history
            WHERE id NOT IN (
                SELECT MAX(id) FROM ratings_history
                GROUP BY stock_id, rating_date
            )
        """)
        deleted = cursor.rowcount
        # 创建唯一索引
        cursor.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS idx_ratings_unique
            ON ratings_history(stock_id, rating_date)
        """)
        if deleted > 0:
            print(f'[B12迁移] ratings_history 清理 {deleted} 条重复记录')
except Exception as e:
    print(f'[B12迁移] ratings_history 迁移警告: {e}')
```

**注意：** `CREATE TABLE IF NOT EXISTS` 不会修改已有表结构，所以必须用 `CREATE UNIQUE INDEX` 来给已有数据库添加约束。

**步骤 3：验证 `_save_rating()` 的 INSERT OR REPLACE**

`modules/advisor.py` L408-411 的 `INSERT OR REPLACE INTO ratings_history` 在有 UNIQUE 约束后会自动生效（UNIQUE 冲突时 REPLACE 旧记录）。**无需修改 SQL 语句本身**，但需验证行为正确。

### 自验方法
写一个临时脚本 `_dev_t1_test.py` 执行：
```python
import sqlite3

conn = sqlite3.connect(r'C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db')
c = conn.cursor()
# 1. 检查唯一索引
c.execute('PRAGMA index_list(ratings_history)')
print('索引:', c.fetchall())
# 2. 检查重复
c.execute(
    'SELECT stock_id, rating_date, COUNT(*) as cnt FROM ratings_history GROUP BY stock_id, rating_date HAVING cnt > 1'
)
dups = c.fetchall()
print(f'重复记录组数: {len(dups)}')
# 3. 检查 600276 在 07-16 的记录数
c.execute(
    "SELECT COUNT(*) FROM ratings_history WHERE stock_id=(SELECT id FROM stocks WHERE symbol='600276') AND rating_date='2026-07-16'"
)
print(f'600276 07-16 记录数: {c.fetchone()[0]}')
conn.close()
```
预期：索引存在、重复=0、600276 记录数=1。

---

## T2：修复 price_at_rating 来源（P0）

### 问题
`_save_rating()` 使用 `_read_latest_close()`（最新 K 线）获取 price_at_rating。若分析在当日 K 线采集前运行，price_at_rating 为前一日价格（如 56.85 vs 正确的 55.99）。

### 修改文件
- `modules/advisor.py`（`_save_rating()` 函数，L400-423）

### 具体操作

修改 `_save_rating()` 函数，不再使用传入的 `latest_close` 参数获取价格，改为根据 `rating_date` 查询对应的 K 线收盘价：

```python
def _save_rating(stock_id, analysis, action_advice, is_changed, latest_close):
    """将评级记录写入 ratings_history 表"""
    rating_date = analysis.get('score_date', datetime.now(_CN_TZ).strftime('%Y-%m-%d'))

    # B12-T2: price_at_rating 使用 rating_date 对应的 K 线收盘价
    # 而非分析运行时的最新 K 线（避免 K 线未采集时取到前一日价格）
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT close FROM raw_kline
        WHERE stock_id = ? AND trade_date <= ?
        ORDER BY trade_date DESC LIMIT 1
    """,
        (stock_id, rating_date),
    )
    row = cursor.fetchone()
    price = float(row['close']) if row and row['close'] is not None else None

    cursor.execute(
        """
        INSERT OR REPLACE INTO ratings_history
        (stock_id, rating_date, rating, total_score, action_advice, is_change, price_at_rating)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """,
        (
            stock_id,
            rating_date,
            analysis['rating'],
            analysis['total_score'],
            action_advice,
            1 if is_changed else 0,
            price,
        ),
    )

    conn.commit()
    conn.close()
    logger.info(f'stock_id={stock_id} 评级记录已写入 ratings_history (price={price})')
```

**关键变化：**
- 删除 `price = latest_close['close'] if latest_close else None`
- 新增：根据 `rating_date` 查询 `raw_kline` 中 `trade_date <= rating_date` 的最近收盘价
- `latest_close` 参数保留（不破坏调用方签名），但不再用于 price_at_rating

**注意：** `_read_latest_close()` 函数本身不删除（其他地方可能用到），只是 `_save_rating` 不再依赖它获取价格。

### 自验方法
运行一次分析后检查：
```sql
SELECT rh.rating_date, rh.price_at_rating,
       (SELECT close FROM raw_kline WHERE stock_id=rh.stock_id AND trade_date <= rh.rating_date ORDER BY trade_date DESC LIMIT 1) as kline_close
FROM ratings_history rh ORDER BY rh.id DESC LIMIT 5;
```
预期：price_at_rating 与 kline_close 一致。

---

## T3：修复 score_date 来源（P1）

### 问题
`scoring_engine.py` L1067-1068 使用 `datetime.now()` 作为 score_date。周六运行时 score_date=周六，但 K 线数据是周五的，导致回测 T+N 查找偏移。

### 修改文件
- `modules/scoring_engine.py`（`analyze()` 函数，L1066-1068 附近）

### 具体操作

将 L1066-1068：
```python
    # 9. 组装结果
    rating_time = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M')
    score_date = rating_time[:10]
```

替换为：
```python
    # 9. 组装结果
    # B12-T3: score_date 使用 K 线数据的 trade_date（而非 datetime.now()）
    # data.trade_date 格式为 YYYYMMDD，需转换为 YYYY-MM-DD
    _td = str(data.trade_date or '').strip()
    if len(_td) == 8 and _td.isdigit():
        score_date = f'{_td[:4]}-{_td[4:6]}-{_td[6:8]}'
    else:
        # 兜底：trade_date 无效时使用当前日期
        score_date = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
        logger.warning(f'score_date 回退到 datetime.now(): trade_date={data.trade_date!r}')
```

**注意：**
- `data` 是 `StockData` 对象，`data.trade_date` 是 K 线最新交易日（格式 YYYYMMDD，如 `'20260724'`）
- 不要删除 `rating_time` 变量（如果后续代码有用到的话），只修改 `score_date` 的来源
- 检查 `AnalysisResult` 中 `score_date` 是否被其他地方引用，确保格式一致（`YYYY-MM-DD`）

### 自验方法
```python
# 在项目中执行
from modules.scoring_engine import analyze_from_db

result = analyze_from_db(4)  # stock_id=4 (600276)
print(f'score_date={result.score_date}')
# 预期：score_date 应为最近交易日（如 2026-07-24），而非今天（2026-07-25 周六）
```

---

## T4：修复 normalize_rating 旧格式兼容（P1）

### 问题
`normalize_rating('D', 74.8)` 返回 `'推荐买入'`（因为优先用 score 映射），但 D 应映射为 `'强烈建议卖出'`。旧引擎的 rating 与 score 可能矛盾。

### 修改文件
- `modules/scoring_engine.py`（`normalize_rating()` 函数，L76-96）

### 具体操作

将 L76-96 的 `normalize_rating` 函数替换为：

```python
def normalize_rating(rating_str, total_score=None):
    """将任意评级（新旧）统一映射到新中文5档。

    RATING-ALIGN-004 历史兼容：历史 ratings_history 中 rating 为 A/B+/B/C/D，
    本函数将其映射到新中文5档。

    B12-T4 修复：旧格式评级与 total_score 矛盾时，优先使用评级字符串映射。
    （旧引擎存在 rating 与 score 不一致的 bug，score 不可信）
    """
    if rating_str is None:
        return None
    # 新档位直接返回
    if rating_str in RATING_THRESHOLDS:
        return rating_str
    # 旧格式：检查 rating 与 score 是否一致
    if rating_str in RATING_LEGACY_MAP:
        legacy_mapped = RATING_LEGACY_MAP[rating_str]
        if total_score is not None:
            try:
                score_mapped, _ = _map_rating(float(total_score))
                if score_mapped == legacy_mapped:
                    # 一致：使用 score 精确映射（原逻辑）
                    return score_mapped
                else:
                    # B12-T4: 矛盾时优先使用评级字符串映射
                    logger.warning(
                        f'normalize_rating 矛盾: rating={rating_str}→{legacy_mapped}, '
                        f'score={total_score}→{score_mapped}, 采用评级映射={legacy_mapped}'
                    )
                    return legacy_mapped
            except (ValueError, TypeError):
                pass
        return legacy_mapped
    # 未知格式：有 score 时按 score 映射
    if total_score is not None:
        try:
            grade, _ = _map_rating(float(total_score))
            return grade
        except (ValueError, TypeError):
            pass
    return rating_str
```

### 自验方法
```python
from modules.scoring_engine import normalize_rating

# 矛盾用例：D + 高分 → 应返回 '强烈建议卖出'
assert normalize_rating('D', 74.8) == '强烈建议卖出', f'Got: {normalize_rating("D", 74.8)}'
# 矛盾用例：B + 71.9 → B映射'持有观望', score映射'推荐买入', 矛盾取B
assert normalize_rating('B', 71.9) == '持有观望', f'Got: {normalize_rating("B", 71.9)}'
# 一致用例：B+ + 78.9 → B+映射'推荐买入', score映射'推荐买入', 一致
assert normalize_rating('B+', 78.9) == '推荐买入', f'Got: {normalize_rating("B+", 78.9)}'
# 新格式不受影响
assert normalize_rating('推荐买入', 70.1) == '推荐买入'
assert normalize_rating('持有观望', 55.0) == '持有观望'
# 无 score
assert normalize_rating('D') == '强烈建议卖出'
assert normalize_rating('B+') == '推荐买入'
print('T4 全部断言通过!')
```

---

## T5：清理回测数据 + 重跑（P0）

### 前置条件
T1~T4 全部完成且自验通过。

### 具体操作

写一个临时脚本 `_dev_t5_rerun.py`：

```python
import sqlite3
import sys
import os

sys.path.insert(0, r'C:\Users\zlb19\Desktop\Qoder cn\stock_analyst')

DB = r'C:\Users\zlb19\Desktop\Qoder cn\stock_analyst\stock_analyst.db'

# 1. 记录修复前数据
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute("""
    SELECT rating, COUNT(*) as cnt,
           SUM(CASE WHEN is_correct=1 THEN 1 ELSE 0 END) as correct,
           SUM(CASE WHEN is_correct=0 THEN 1 ELSE 0 END) as wrong
    FROM backtest_results WHERE (is_simulated IS NULL OR is_simulated=0)
    GROUP BY rating
""")
print('=== 修复前 ===')
for r in c.fetchall():
    judged = r['correct'] + r['wrong']
    acc = f'{r["correct"] / judged * 100:.1f}%' if judged > 0 else 'N/A'
    print(
        f'  {r["rating"]}: total={r["cnt"]}, correct={r["correct"]}, wrong={r["wrong"]}, acc={acc}'
    )

# 2. 清空真实回测数据（保留模拟数据）
c.execute('DELETE FROM backtest_results WHERE is_simulated IS NULL OR is_simulated = 0')
print(f'\n已清空真实回测数据: {c.rowcount} 条')
conn.commit()
conn.close()

# 3. 重跑回测
from modules.backtest_engine import BacktestEngine

engine = BacktestEngine()
result = engine.batch_backtest(force=True)
print(f'\n回测完成: {result}')

# 4. 记录修复后数据
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute("""
    SELECT rating, COUNT(*) as cnt,
           SUM(CASE WHEN is_correct=1 THEN 1 ELSE 0 END) as correct,
           SUM(CASE WHEN is_correct=0 THEN 1 ELSE 0 END) as wrong
    FROM backtest_results WHERE (is_simulated IS NULL OR is_simulated=0)
    GROUP BY rating
""")
print('\n=== 修复后 ===')
for r in c.fetchall():
    judged = r['correct'] + r['wrong']
    acc = f'{r["correct"] / judged * 100:.1f}%' if judged > 0 else 'N/A'
    print(
        f'  {r["rating"]}: total={r["cnt"]}, correct={r["correct"]}, wrong={r["wrong"]}, acc={acc}'
    )
conn.close()
```

### 自验标准
- 重跑后"推荐买入"记录数 ≤ 7（唯一 stock+date 组合数）
- 提供修复前后准确率对比表（写入自验报告）

---

## T6（可选）：模拟回测评估

仅输出文字评估结论，不修改代码：
1. 技术面单维度映射 vs 四维加权映射的准确率差异
2. 是否建议模拟回测使用四维评分
3. JUDGEMENT_MATRIX 阈值是否需要调整

---

## 自验报告格式

完成所有任务后，输出以下格式的自验报告：

```markdown
# B12 开发自验报告

## T1 去重+UNIQUE
- 唯一索引: [存在/不存在]
- 重复记录组数: [数字]
- 600276 07-16 记录数: [数字]

## T2 price_at_rating
- 最新5条记录 price_at_rating vs kline_close: [一致/不一致]

## T3 score_date
- analyze_from_db(4).score_date = [值]
- 是否为最近交易日: [是/否]

## T4 normalize_rating
- normalize_rating('D', 74.8) = [值]
- normalize_rating('B', 71.9) = [值]
- normalize_rating('B+', 78.9) = [值]
- 全部断言: [通过/失败]

## T5 回测重跑
| 评级 | 修复前 total/acc | 修复后 total/acc |
|---|---|---|
| 推荐买入 | | |
| 持有观望 | | |
| 建议减仓 | | |

## T6 评估结论（可选）
[文字结论]

## 红线核验
- data_collector.py if False 未触碰: [是]
- 无新 pip 依赖: [是]
- config_weights.json 未修改: [是]
```

---

*PM 签发 | 2026-07-25 | B12*
