# 测试提示词：数据质量与完整度全面测试

## 你的角色

你是「智能个股分析与评级系统」的测试工程师，负责对数据获取质量进行全面测试。

## 项目信息

- 项目路径：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`
- 技术栈：Python + Flask + SQLite + akshare
- 数据库：项目根目录 `stock_analyst.db`
- 启动方式：`python app.py`（Flask 服务，默认端口 5000）

## 测试背景

B10 批次完成了数据完整度提升（基本面补全 + 股东增减持接入 + 资金面标注），B10-Hotfix 修复了写入行错位问题。现需全面验证数据获取质量。

## 测试环境准备

```python
import os, sys, sqlite3

os.chdir(r'C:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
sys.path.insert(0, '.')
```

---

## 测试用例

### TC-01：全量股票数据完整度检查

**目的**：验证所有 27 只自选股的四维数据完整度

**步骤**：
```python
from modules import scoring_engine
from database.db_manager import get_connection

conn = get_connection()
cursor = conn.cursor()
cursor.execute("SELECT id, symbol, name, market FROM stocks WHERE status != 'delisted' ORDER BY id")
stocks = [dict(r) for r in cursor.fetchall()]
conn.close()

print(
    f'{"ID":<4} {"代码":<10} {"名称":<12} {"技术":<6} {"基本面":<6} {"资金":<6} {"消息":<6} {"结果"}'
)
print('-' * 70)

fail_list = []
for s in stocks:
    try:
        result = scoring_engine.analyze_from_db(s['id'])
        if result is None:
            print(
                f'{s["id"]:<4} {s["symbol"]:<10} {s["name"]:<12} {"--":<6} {"--":<6} {"--":<6} {"--":<6} FAIL(无数据)'
            )
            fail_list.append((s['symbol'], 'analyze_from_db返回None'))
            continue
        dq = result.data_quality or {}
        tech = dq.get('technical', 0)
        fund = dq.get('fundamental', 0)
        cap = dq.get('capital', 0)
        news = dq.get('news', 0)
        # 判定标准：技术>=100%, 基本面>=78%, 资金>=67%, 消息>=50%
        status = 'PASS'
        if fund < 0.78 and s['market'] == 'a_stock':
            status = 'WARN(基本面<78%)'
        if tech < 1.0:
            status = 'FAIL(技术<100%)'
        print(
            f'{s["id"]:<4} {s["symbol"]:<10} {s["name"]:<12} {tech:<6.0%} {fund:<6.0%} {cap:<6.0%} {news:<6.0%} {status}'
        )
        if 'FAIL' in status:
            fail_list.append((s['symbol'], status))
    except Exception as e:
        print(f'{s["id"]:<4} {s["symbol"]:<10} {s["name"]:<12} {"ERROR":<30} {e}')
        fail_list.append((s['symbol'], str(e)))

print(f'\n总计: {len(stocks)}只, 失败: {len(fail_list)}只')
if fail_list:
    print('失败列表:', fail_list)
```

**预期**：
- 技术面：所有股票 100%
- 基本面：A 股 ≥78%（港股不考核）
- 资金面：≥67%
- 消息面：≥50%
- 无股票返回 None

---

### TC-02：基本面数据重复行检查

**目的**：检查 raw_fundamental 是否存在同一股票同一 report_date 的重复记录

**步骤**：
```python
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 检查重复
c.execute("""
    SELECT stock_id, report_date, COUNT(*) as cnt
    FROM raw_fundamental
    GROUP BY stock_id, report_date
    HAVING cnt > 1
""")
dupes = c.fetchall()
if dupes:
    print(f'[FAIL] 发现 {len(dupes)} 组重复记录:')
    for d in dupes:
        print(f'  stock_id={d["stock_id"]} report_date={d["report_date"]} count={d["cnt"]}')
else:
    print('[PASS] 无重复记录 (stock_id + report_date 唯一)')

# 每只股票的记录数
c.execute("""
    SELECT rf.stock_id, s.symbol, COUNT(*) as cnt
    FROM raw_fundamental rf JOIN stocks s ON rf.stock_id = s.id
    GROUP BY rf.stock_id
    ORDER BY cnt DESC
""")
print('\n每只股票基本面记录数:')
for r in c.fetchall():
    flag = ' !! 过多' if r['cnt'] > 5 else ''
    print(f'  {r["symbol"]:<10} {r["cnt"]}条{flag}')

conn.close()
```

**预期**：
- 无 (stock_id, report_date) 重复
- 每只股票记录数合理（1~5 条，对应不同季度财报）

---

### TC-03：K线数据重复与连续性检查

**目的**：检查 raw_kline 是否有重复日期、数据是否连续

**步骤**：
```python
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 重复检查
c.execute("""
    SELECT stock_id, trade_date, COUNT(*) as cnt
    FROM raw_kline
    GROUP BY stock_id, trade_date
    HAVING cnt > 1
""")
dupes = c.fetchall()
if dupes:
    print(f'[FAIL] K线重复: {len(dupes)} 组')
    for d in dupes[:5]:
        print(f'  stock_id={d["stock_id"]} date={d["trade_date"]} count={d["cnt"]}')
else:
    print('[PASS] K线无重复 (stock_id + trade_date 唯一)')

# 最新日期检查
c.execute("""
    SELECT s.symbol, MAX(k.trade_date) as latest, COUNT(*) as cnt
    FROM raw_kline k JOIN stocks s ON k.stock_id = s.id
    GROUP BY k.stock_id
    ORDER BY latest DESC
""")
print('\nK线最新日期:')
for r in c.fetchall():
    print(f'  {r["symbol"]:<10} latest={r["latest"]} total={r["cnt"]}条')

conn.close()
```

**预期**：
- 无重复 (stock_id, trade_date)
- 最新日期应为最近交易日
- 每只股票 ≥60 条（满足 MA60 计算需求）

---

### TC-04：资金面数据重复与有效性

**目的**：检查 raw_capital_flow 数据质量

**步骤**：
```python
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 重复检查
c.execute("""
    SELECT stock_id, trade_date, COUNT(*) as cnt
    FROM raw_capital_flow
    GROUP BY stock_id, trade_date
    HAVING cnt > 1
""")
dupes = c.fetchall()
if dupes:
    print(f'[FAIL] 资金面重复: {len(dupes)} 组')
    for d in dupes[:5]:
        print(f'  stock_id={d["stock_id"]} date={d["trade_date"]} count={d["cnt"]}')
else:
    print('[PASS] 资金面无重复')

# 空值率
c.execute('SELECT COUNT(*) FROM raw_capital_flow')
total = c.fetchone()[0]
c.execute('SELECT COUNT(*) FROM raw_capital_flow WHERE main_net_inflow IS NOT NULL')
main_cnt = c.fetchone()[0]
c.execute('SELECT COUNT(*) FROM raw_capital_flow WHERE north_holding_change IS NOT NULL')
north_cnt = c.fetchone()[0]
c.execute('SELECT COUNT(*) FROM raw_capital_flow WHERE margin_balance IS NOT NULL')
margin_cnt = c.fetchone()[0]

print(f'\n资金面字段非空率 (总计{total}条):')
print(f'  main_net_inflow: {main_cnt}/{total} = {main_cnt / total:.1%}')
print(f'  north_holding_change: {north_cnt}/{total} = {north_cnt / total:.1%}')
print(f'  margin_balance: {margin_cnt}/{total} = {margin_cnt / total:.1%}')

conn.close()
```

**预期**：
- 无重复
- main_net_inflow 非空率 >90%
- north/margin 记录实际覆盖率（已知偏低，记录现状即可）

---

### TC-05：消息面数据时效性检查

**目的**：检查 news_sentiment 数据是否过期

**步骤**：
```python
import sqlite3
from datetime import datetime, timedelta

conn = sqlite3.connect('stock_analyst.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

c.execute("""
    SELECT ns.stock_id, s.symbol, s.name, ns.news_date, ns.fetched_at, 
           ns.avg_sentiment, ns.total_count
    FROM news_sentiment ns JOIN stocks s ON ns.stock_id = s.id
    ORDER BY ns.fetched_at DESC
""")
rows = c.fetchall()
print(f'消息面记录总数: {len(rows)}')
print(f'\n{"代码":<10} {"名称":<10} {"news_date":<12} {"fetched_at":<20} {"情感":<8} {"条数"}')
print('-' * 70)

stale_count = 0
today = datetime.now().strftime('%Y-%m-%d')
for r in rows[:30]:  # 显示最近30条
    fetched = r['fetched_at'] or ''
    is_stale = fetched < (datetime.now() - timedelta(days=3)).strftime('%Y-%m-%d')
    flag = ' [过期>3天]' if is_stale else ''
    if is_stale:
        stale_count += 1
    print(
        f'  {r["symbol"]:<10} {r["name"]:<10} {r["news_date"] or "N/A":<12} {fetched:<20} {r["avg_sentiment"]:<8} {r["total_count"]}{flag}'
    )

print(f'\n过期记录(>3天): {stale_count}/{len(rows)}')
conn.close()
```

**预期**：
- 活跃股票应有近 3 天内的消息面数据
- avg_sentiment 在 [-1, 1] 范围内
- total_count > 0

---

### TC-06：批量分析接口重复调用检测

**目的**：验证单次批量分析中，同一 API 是否被重复调用

**步骤**：
1. 启动应用 `python app.py`
2. 在浏览器中触发 1 只股票的批量分析（如 000333）
3. 观察控制台日志，检查：
   - `fetch_a_fundamental` 是否只调用 1 次
   - `fetch_fundamental_detail` 是否只调用 1 次
   - `fetch_holder_increase` 是否只调用 1 次
   - `fetch_kline` 是否只调用 1 次
   - 有无重复的 HTTP 请求日志

**预期**：
- 每个采集函数对同一股票单次分析中仅调用 1 次
- 无重复网络请求

---

### TC-07：holder_increase 字段一致性

**目的**：验证 holder_increase 在 DB 和引擎输出间一致

**步骤**：
```python
import sqlite3
from modules.data_adapter import load_stockdata_from_db

conn = sqlite3.connect('stock_analyst.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 检查有 holder_increase 值的记录
c.execute("""
    SELECT rf.stock_id, s.symbol, rf.holder_increase
    FROM raw_fundamental rf JOIN stocks s ON rf.stock_id = s.id
    WHERE rf.holder_increase IS NOT NULL
""")
rows = c.fetchall()
print(f'holder_increase 非空记录: {len(rows)}')
for r in rows:
    print(f'  {r["symbol"]}: holder_increase={r["holder_increase"]}')
    # 验证 adapter 读取一致
    data = load_stockdata_from_db(r['stock_id'])
    if data:
        adapter_val = data.holder_increase
        match = (
            (adapter_val == bool(r['holder_increase']))
            if r['holder_increase'] is not None
            else (adapter_val is None)
        )
        print(f'    adapter读取={adapter_val} 一致={"Y" if match else "N !!"}')

conn.close()
```

**预期**：
- DB 值与 adapter 输出一致
- True/False/None 三态正确

---

### TC-08：降级机制验证

**目的**：确认接口失败时不阻塞分析

**步骤**：
1. 临时断网（或 mock 一个不存在的股票代码）
2. 触发批量分析
3. 验证：
   - 分析流程不中断
   - 返回结果中有 data_warnings 提示
   - 缺失维度使用降级策略（权重归零/默认值）

**预期**：
- 任何单只股票分析失败不影响其他股票
- 用户看到友好提示而非报错

---

### TC-09：UI 界面功能重叠检查

**目的**：检查前端页面是否存在功能重叠、按钮/入口冗余、信息重复展示等用户体验问题

**步骤**：
1. 启动应用 `python app.py`，浏览器打开 `http://127.0.0.1:5000`
2. 逐页检查以下区域：

**导航与页面结构**：
- [ ] 导航栏各 Tab（日报/看板/回测/个股等）是否有功能重叠的页面
- [ ] 是否存在两个入口跳转到相同功能的按钮

**看板页**：
- [ ] 指数评级区域与个股评级区域是否有重复展示的信息
- [ ] 股票列表中的评分/评级与点击进入详情后的评分/评级是否同源一致
- [ ] 是否存在多个"刷新"按钮功能重叠（如指数刷新 vs 全局刷新）

**个股详情页**：
- [ ] "分析"按钮与"批量分析"是否有功能包含关系（批量是否覆盖单只）
- [ ] 评级信息是否在页面内重复出现（如顶部和正文同时显示同一评级）
- [ ] 数据完整度展示是否与报告正文中的 data_warnings 信息重复

**日报页**：
- [ ] 日报列表与看板页的评分数据是否同源（不应出现数值不一致）
- [ ] 日报详情中的操作建议与看板页的建议是否一致

**回测页**：
- [ ] 回测结果展示是否与日报/看板有重叠功能

**通用检查**：
- [ ] 同一数据（如最新评分）在不同页面展示时数值是否一致
- [ ] 是否有已废弃/无功能的按钮或区域残留
- [ ] 移动端/窄屏下是否有布局重叠

3. 截图记录发现的问题

**预期**：
- 各页面功能边界清晰，无冗余入口
- 同一数据在各页面展示一致（同源）
- 无废弃/残留 UI 元素
- 信息层次分明，不重复堆砌

**记录格式**（发现问题时）：
```
[TC-09-问题#]
位置：XX页面 > XX区域
现象：描述重叠/冗余现象
影响：用户体验影响程度（高/中/低）
建议：合并/移除/保留
```

---

### TC-10：跨日重复采集检测

**目的**：验证系统是否会重复采集已成功入库的历史数据（如 23 日已采集的数据，24 日又重复采集）

**步骤**：

**A. K线重复采集检测**：
```python
import sqlite3
from datetime import datetime

conn = sqlite3.connect('stock_analyst.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 记录当前 raw_kline 最新日期和记录数
c.execute(
    'SELECT stock_id, MAX(trade_date) as latest, COUNT(*) as cnt FROM raw_kline GROUP BY stock_id'
)
before = {r['stock_id']: {'latest': r['latest'], 'cnt': r['cnt']} for r in c.fetchall()}
print('采集前 K线状态:')
for sid, info in list(before.items())[:5]:
    print(f'  stock_id={sid}: latest={info["latest"]} count={info["cnt"]}')
conn.close()
```

然后触发一次批量分析（或单独调用 `collect_stock_data`），再检查：
```python
conn = sqlite3.connect('stock_analyst.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()
c.execute(
    'SELECT stock_id, MAX(trade_date) as latest, COUNT(*) as cnt FROM raw_kline GROUP BY stock_id'
)
after = {r['stock_id']: {'latest': r['latest'], 'cnt': r['cnt']} for r in c.fetchall()}

print('\n采集后对比:')
for sid in before:
    b, a = before[sid], after.get(sid, {'latest': 'N/A', 'cnt': 0})
    cnt_diff = a['cnt'] - b['cnt']
    # 如果最新日期未变但记录数增加，说明重复写入了历史数据
    if b['latest'] == a['latest'] and cnt_diff > 0:
        print(
            f'  [WARN] stock_id={sid}: 最新日期未变({a["latest"]}) 但记录+{cnt_diff} → 可能重复采集历史数据'
        )
    elif cnt_diff > 1:
        print(f'  [WARN] stock_id={sid}: 一次采集新增{cnt_diff}条 → 可能拉取了多天历史数据')
    else:
        print(f'  [OK] stock_id={sid}: latest={a["latest"]} +{cnt_diff}条')
conn.close()
```

**B. 基本面重复采集检测**：
```python
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
conn.row_factory = sqlite3.Row
c = conn.cursor()

# 检查同一 stock_id + report_date 是否有多条记录
c.execute("""
    SELECT stock_id, report_date, COUNT(*) as cnt, 
           GROUP_CONCAT(fetched_at) as fetch_times
    FROM raw_fundamental
    GROUP BY stock_id, report_date
    HAVING cnt > 1
""")
dupes = c.fetchall()
if dupes:
    print(f'[FAIL] 基本面重复记录 {len(dupes)} 组:')
    for d in dupes:
        print(f'  stock_id={d["stock_id"]} report_date={d["report_date"]} count={d["cnt"]}')
        print(f'    fetched_at: {d["fetch_times"]}')
else:
    print('[PASS] 基本面无 (stock_id+report_date) 重复')

# 检查同一 report_date 是否被多次 fetch（fetched_at 不同）
c.execute("""
    SELECT stock_id, report_date, fetched_at FROM raw_fundamental
    ORDER BY stock_id, report_date DESC
""")
print('\n基本面采集时间线(每只股票最新2条):')
seen = {}
for r in c.fetchall():
    sid = r['stock_id']
    if sid not in seen:
        seen[sid] = []
    if len(seen[sid]) < 2:
        seen[sid].append(f'report={r["report_date"]} fetched={r["fetched_at"]}')
for sid, records in list(seen.items())[:10]:
    print(f'  stock_id={sid}: {" | ".join(records)}')
conn.close()
```

**C. 资金面/消息面重复检测**：
```python
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
c = conn.cursor()

# 资金面: 同一 stock_id+trade_date 是否多条
c.execute("""
    SELECT stock_id, trade_date, COUNT(*) as cnt
    FROM raw_capital_flow
    GROUP BY stock_id, trade_date
    HAVING cnt > 1
""")
cap_dupes = c.fetchall()
print(f'资金面重复: {len(cap_dupes)} 组 {"[FAIL]" if cap_dupes else "[PASS]"}')

# 消息面: 同一 stock_id+news_date 是否多条
c.execute("""
    SELECT stock_id, news_date, COUNT(*) as cnt
    FROM news_sentiment
    GROUP BY stock_id, news_date
    HAVING cnt > 1
""")
news_dupes = c.fetchall()
print(f'消息面重复: {len(news_dupes)} 组 {"[FAIL]" if news_dupes else "[PASS]"}')
conn.close()
```

**D. 网络请求重复监控**（通过日志）：
1. 启动应用时开启详细日志
2. 触发批量分析 1 只股票
3. 统计日志中 akshare API 调用次数：
   - `stock_zh_a_hist`（K线）应只调用 1 次
   - `stock_individual_info_em`（基本面）应只调用 1 次
   - `stock_financial_analysis_indicator`（B10补全）应只调用 1 次
   - `stock_inner_trade_xq`（股东增减持）应只调用 1 次
   - 如某接口被调用 >1 次 → 存在重复采集

**预期**：
- K线：每次采集仅增量获取最新 1 天数据（或仅获取最新交易日），不重复拉取已入库的历史日期
- 基本面：不产生 (stock_id, report_date) 重复记录
- 资金面/消息面：不产生 (stock_id, trade_date/news_date) 重复记录
- 单次分析中每个 API 仅调用 1 次

**特别关注**：
- K线采集是否每次拉取全量 300 天（浪费）还是增量拉取
- `INSERT OR REPLACE` 是否导致无谓重写（数据相同但仍触发写盘）
- 批量分析 27 只股票时，`stock_inner_trade_xq()`（全市场无参数）是否被调用 27 次（应只调 1 次然后按股票过滤）

---

### TC-11：批量分析与日报生成重复执行检测

**目的**：验证用户先执行「批量分析」后再点击「生成日报」时，系统是否重复执行了数据采集 + 评分计算

**背景**：
- 批量分析（app.py）：对每只股票执行 `collect_stock_data()` + `generate_advice()`
- 生成日报（daily_report.py）：对每只股票执行 `collect_stock_data()` + `generate_advice()`
- 两者流程完全相同，若用户先批量分析再生成日报，所有 API 调用和评分计算会执行两遍

**步骤**：

1. 启动应用 `python app.py`
2. 记录当前数据库状态：
```python
import sqlite3

conn = sqlite3.connect('stock_analyst.db')
c = conn.cursor()
# 记录 raw_kline 总记录数
c.execute('SELECT COUNT(*) FROM raw_kline')
kline_before = c.fetchone()[0]
# 记录 daily_reports 最新日期
c.execute('SELECT MAX(report_date) FROM daily_reports')
report_before = c.fetchone()[0]
# 记录 ratings_history 总数
c.execute('SELECT COUNT(*) FROM ratings_history')
rating_before = c.fetchone()[0]
print(f'执行前: kline={kline_before}, report_date={report_before}, ratings={rating_before}')
conn.close()
```

3. **执行批量分析**（通过浏览器看板页点击"批量分析"按钮，或 API 调用）
4. 批量分析完成后，再次记录数据库状态：
```python
conn = sqlite3.connect('stock_analyst.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM raw_kline')
kline_after_batch = c.fetchone()[0]
c.execute('SELECT COUNT(*) FROM ratings_history')
rating_after_batch = c.fetchone()[0]
print(
    f'批量分析后: kline={kline_after_batch}(+{kline_after_batch - kline_before}), ratings={rating_after_batch}(+{rating_after_batch - rating_before})'
)
conn.close()
```

5. **点击生成日报**（通过浏览器日报页点击"生成日报"按钮）
6. 日报生成完成后，第三次记录：
```python
conn = sqlite3.connect('stock_analyst.db')
c = conn.cursor()
c.execute('SELECT COUNT(*) FROM raw_kline')
kline_after_report = c.fetchone()[0]
c.execute('SELECT COUNT(*) FROM ratings_history')
rating_after_report = c.fetchone()[0]
c.execute("SELECT COUNT(*) FROM daily_reports WHERE report_date = date('now'))")
report_count = c.fetchone()[0]
print(
    f'生成日报后: kline={kline_after_report}(+{kline_after_report - kline_after_batch}), ratings={rating_after_report}(+{rating_after_report - rating_after_batch})'
)
print(f'今日日报记录数: {report_count}')
conn.close()
```

7. 观察服务端日志，统计日报生成过程中是否再次调用了：
   - `collect_stock_data`（数据采集）
   - `stock_zh_a_hist` / `stock_individual_info_em` 等 akshare 接口
   - `scoring_engine.analyze_from_db`（评分计算）

**判定标准**：

| 现象 | 判定 | 说明 |
|------|------|------|
| 日报生成后 kline 记录数增加 | **重复采集** | 批量分析已采集过，日报不应再次采集 |
| 日报生成后 ratings_history 增加 | **重复评分** | 批量分析已评分，日报不应再次评分 |
| 日志中可见 collect_stock_data 被调用 | **重复执行** | 确认日报重新走了完整采集+分析流程 |
| 日报生成后仅 daily_reports 表新增记录 | **正常** | 日报仅格式化已有结果，不重复采集/评分 |

**预期结果**：
- 理想情况：日报生成应复用批量分析的结果，仅做格式化输出 + 写入 daily_reports
- 实际情况：待测试确认（当前代码显示日报会重新执行 collect + analyze）

**如确认重复，记录以下信息供优化参考**：
```
[TC-11 优化建议]
现象：批量分析后生成日报，collect_stock_data 被再次调用 27 次
影响：
  - API 调用量翻倍（27只×2次=54次采集）
  - 评分计算翻倍
  - 用户等待时间加倍
  - akshare 限流风险增大
建议方案：
  A. 日报生成时检查 daily_reports 表当日是否已有记录，有则跳过采集+分析，仅重新格式化
  B. 日报生成时检查 ratings_history 当日是否已有评级，有则跳过 generate_advice
  C. 合并批量分析与日报为一个操作（批量分析完成后自动写入日报）
```

---

## 测试报告模板

| 用例 | 结果 | 备注 |
|------|------|------|
| TC-01 全量完整度 | PASS/FAIL | |
| TC-02 基本面重复 | PASS/FAIL | |
| TC-03 K线重复/连续 | PASS/FAIL | |
| TC-04 资金面质量 | PASS/FAIL | |
| TC-05 消息面时效 | PASS/FAIL | |
| TC-06 重复调用 | PASS/FAIL | |
| TC-07 holder一致性 | PASS/FAIL | |
| TC-08 降级机制 | PASS/FAIL | |
| TC-09 UI功能重叠 | PASS/FAIL | |
| TC-10 跨日重复采集 | PASS/FAIL | |
| TC-11 批量分析vs日报重复 | PASS/FAIL | |

## 注意事项

- 项目路径含空格（`Qoder cn`），PowerShell 中需引号包裹
- PowerShell 不支持 `&&`，用 `;` 分隔
- 多行 Python 写临时 .py 文件执行
- `news_sentiment` 时间字段为 `fetched_at`
- 测试完成后清理所有临时 .py 文件
- 如发现 BUG，记录：复现步骤 + 实际结果 + 预期结果
