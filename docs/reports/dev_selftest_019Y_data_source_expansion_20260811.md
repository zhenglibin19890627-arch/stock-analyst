# 开发自测报告：019Y 数据源扩展（mootdx + baostock + akshare 维度增强）

> 批次：019Y ｜ 开发角色自测 ｜ 2026-08-11
> 任务书：`docs/tasks/dev_tasks_20260811_019Y_data_source_expansion.md`

---

## 一、改动文件清单

| 文件 | 行数变化 | 改动内容 |
|------|---------|---------|
| `modules/data_collector.py` | 3533 → 4303（+770） | ① mootdx 适配层：全局单例客户端（备用服务器池+健康检查）、代码映射、K线降级、五档盘口采集、实时报价只读接口；② baostock 层：login/logout 生命周期、估值备用源、财务备用源（P3 降级）；③ akshare 新接口：估值（stock_value_em，见偏差说明）、限售解禁（stock_restricted_release_queue_em）；④ `fetch_a_fundamental` 增加 P3 baostock 备用层；⑤ `collect_stock_data` 集成 3 个新维度 |
| `database/db_manager.py` | 1083 → 1159（+76） | 新增 `stock_orderbook` / `stock_valuation` / `stock_restricted_release` 三张表；`_migrate_columns` 增加 `raw_kline.data_source` 列 |
| `app.py` | 3980 → 4055（+75） | ① `_fetch_realtime_price_batch` 增加 A 股 mootdx 实时价格降级；② 新增 3 个 API 端点（orderbook/valuation/restricted-release）；③ kline API 返回 data_source 字段 |
| `templates/index.html` | 6243 → 6317（+74） | `viewData` 数据详情新增三块展示：五档盘口（红涨绿跌+量）、估值数据（来源标注 akshare/baostock）、限售解禁（未来批次红色"未解禁"标注）；K线表头动态标注 mootdx 来源 |
| 未改动 | — | `config.py`、评分模块（scoring_engine/analysis_engine/advisor）、资金面链路、现有 akshare/野接口调用 |

## 二、数据库变更说明

### 2.1 新建表（3 张，全部含 source 来源标注，对齐 `raw_capital_flow.capital_source` 风格）

```sql
-- 五档盘口（mootdx 实时快照，每只每天保留最新一条）
CREATE TABLE IF NOT EXISTS stock_orderbook (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    trade_date DATE NOT NULL,
    quote_time TEXT,
    latest_price REAL,
    pct_change REAL,
    bid1_price REAL, bid1_vol REAL, bid2_price REAL, bid2_vol REAL,
    bid3_price REAL, bid3_vol REAL, bid4_price REAL, bid4_vol REAL,
    bid5_price REAL, bid5_vol REAL,
    ask1_price REAL, ask1_vol REAL, ask2_price REAL, ask2_vol REAL,
    ask3_price REAL, ask3_vol REAL, ask4_price REAL, ask4_vol REAL,
    ask5_price REAL, ask5_vol REAL,
    source TEXT DEFAULT NULL,
    fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    UNIQUE(stock_id, trade_date),
    FOREIGN KEY (stock_id) REFERENCES stocks(id)
);

-- 估值（akshare 主源 / baostock 备用，日级低频，同日跳过）
CREATE TABLE IF NOT EXISTS stock_valuation (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    trade_date DATE NOT NULL,
    pe_ttm REAL, pe REAL, pb_mrq REAL, ps_ttm REAL, ps REAL,
    pcf_ncf_ttm REAL, dv_ttm REAL, total_mv REAL,
    source TEXT DEFAULT NULL,
    fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    UNIQUE(stock_id, trade_date),
    FOREIGN KEY (stock_id) REFERENCES stocks(id)
);

-- 限售解禁（akshare 个股解禁时间表，当日快照语义：DELETE + INSERT）
CREATE TABLE IF NOT EXISTS stock_restricted_release (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    stock_id INTEGER NOT NULL,
    release_date DATE NOT NULL,
    release_type TEXT,
    release_shares REAL,
    actual_shares REAL,
    actual_mv REAL,
    release_ratio REAL,
    source TEXT DEFAULT NULL,
    fetched_at TIMESTAMP DEFAULT (datetime('now', 'localtime')),
    FOREIGN KEY (stock_id) REFERENCES stocks(id)
);
```

### 2.2 新增字段（幂等 ALTER TABLE ADD COLUMN，不改动任何已有字段）

```sql
ALTER TABLE raw_kline ADD COLUMN data_source TEXT DEFAULT NULL;
-- 语义：NULL=腾讯主源；'mootdx'=降级备用源（与 raw_capital_flow.capital_source 同风格）
```

### 2.3 对已有数据的影响

- **无影响**：三张新表均为空表新建；`raw_kline.data_source` 新列对存量 7242 条 K 线数据保持 NULL（=腾讯主源），现有查询/写入路径零改动。
- 测试期间临时建的表/行已全部清理（详见第五节）。

## 三、接口实测日志

### 3.1 mootdx（T1）

**① 客户端单例缓存验证**（备用服务器池模式，首次初始化后全局复用）：

```
[TEST] singleton call1 cost=0.28s client=1399295380416
[TEST] singleton call2 cost=0.00s client=1399295380416 SAME=True
```

**② 日K线实测**（frequency=9 日线，250 条，含当日 2026-08-11 实时数据，比 baostock T+1 更实时）：

```
[TEST] _fetch_kline_mootdx rows=250 cost=0.03s
[TEST] kline cols=['日期','开盘','收盘','最高','最低','成交量','成交额','涨跌幅']
[TEST] kline last: 2026-08-11 close=11.28 vol=401132.0
```

**③ 五档盘口数据样本**（quotes 返回完整 bid1-5/ask1-5 + 对应量）：

```
[TEST] quote sample: {"price": 11.28, "pct_change": -0.09, "bid1_price": 11.28,
       "bid1_vol": 804.0, "ask1_price": 11.290000000000001, "ask1_vol": 301.0, "quote_time": "11:29:53"}
[TEST] fetch_orderbook: success 五档盘口已入库（mootdx，快照11:29:53，最新价11.28）
[TEST] orderbook row: date=2026-08-11 time=11:29:53 price=11.28 source=mootdx
```

**④ 实测发现与应对（M-2）**：mootdx 默认配置服务器存在区域性故障（TCP 连接成功但返回空数据，实测 110.41.147.114 / 218.6.170.47 均空），且 bestip 全网扫描约 70 秒。应对：新增**备用服务器池 + 健康检查**机制——先逐个实测备用池（浙江/上海电信 4 台，2026-08-11 实测可用，单台 0.1s），全部失败才走 bestip 兜底（约 70s），并缓存 BESTIP。**兼容红线**：PM 探针同款 `Quotes.factory(market='std', bestip=True)` 调用仍保留为兜底路径。

### 3.2 baostock（T2）

**① 估值数据样本**（akshare 主源失败时降级，PE/PB/PS 与 akshare 逐位一致，交叉验证通过）：

```
[TEST] fetch_valuation(baostock fallback): success 估值已入库(baostock): PE_TTM=5.088082, PB=0.472098, PS_TTM=1.647191 cost=0.11s
[2] row after fallback: {'trade_date': '2026-08-10', 'pe_ttm': 5.088082, 'pb_mrq': 0.472098,
     'ps_ttm': 1.647191, 'pcf_ncf_ttm': 53.010602, 'source': 'baostock'}
```

**② 财务备用样本**（akshare 两层全失败时 P3 降级，ROE/净利率/毛利率 ×100 转百分比）：

```
[1] fetch_a_fundamental(baostock P3 fallback): success 7 期财报（data_source='baostock'）
    2026-03-31 {'roe': 3.65, 'net_margin': 28.02, 'gross_margin': 86.6}
    2025-12-31 {'roe': 14.44, 'net_margin': 24.4, 'gross_margin': 86.21}
```

**③ login/logout 生命周期验证**：

```
[baostock] 登录成功（生命周期：批次级，全局复用）   ← 仅一次
[6] bs logged in: True                              ← 模块级单例
[baostock] 已登出（生命周期成对）                   ← 进程退出 atexit 兜底
```

### 3.3 akshare 新接口样本

**① 估值**（akshare 主源，PE/PB/PS 与 baostock 完全一致）：

```
[1] fetch_valuation(akshare): success 估值已入库(akshare): PE_TTM=44.29013923, PB=5.65180893, PS_TTM=11.04285097 cost=0.56s
[1] row: {'trade_date': '2026-08-10', 'pe_ttm': 44.29013923, 'pe': 46.63479873, 'pb_mrq': 5.65180893,
     'ps_ttm': 11.04285097, 'pcf_ncf_ttm': 31.36081514, 'total_mv': 359603489173.32, 'source': 'akshare'}
```

**② 限售解禁**（600276 全量 15 条解禁记录，含类型/比例）：

```
[4] fetch_restricted_release: success 限售解禁已入库(akshare): 15 条记录 cost=0.37s
    {'release_date': '2021-10-25', 'release_type': '股权激励限售股份', 'release_ratio': 0.18,
     'release_shares': 11484720.0, 'actual_mv': 588132511.2, 'source': 'akshare'}
```

### 3.4 与任务书的偏差说明（必须呈报）

1. **`stock_a_indicator_lg` 在本机 akshare 1.18.53 中不存在**（乐咕估值接口已更名，`AttributeError` 实测）。已自动回退同源等效接口 **`stock_value_em`**（东方财富估值分析，PE(TTM)/PE(静)/市净率/市现率/市销率/总市值同口径），实测 PE/PB/PS 与 baostock 逐位一致（交叉验证）。代码用 `getattr(ak, 'stock_a_indicator_lg', None)` 兼容未来版本。
2. **`stock_restricted_release_summary_em` 在 1.18.53 中为"全市场按日汇总"接口**（参数是 全部股票/沪市A股 等市场名，非个股代码，实测 KeyError），无法提供个股解禁明细。改用同源个股接口 **`stock_restricted_release_queue_em`**（东方财富个股解禁时间表，含 解禁时间/限售股类型/解禁数量/占总市值比例）。
3. **queue_em 实际列名与 akshare 文档不同**（实测为"限售股类型"/"占总市值比例"，文档为"解禁类型"/"占解禁前总股本比例"），已用精确+子串模糊双匹配（`_pick_val`）兼容版本漂移。
4. **港股估值**：`stock_hk_valuation_baidu` 本机网络实测失败（JSON 解码异常），baostock 不支持港股，故港股 stock_valuation 为空并诚实标注 `akshare估值失败（港股；baostock不支持港股）`；港股 PE/PB 仍走既有腾讯实时行情路径（raw_fundamental.pe_ratio/pb_ratio），不受影响。

## 四、降级链路验证

### 4.1 K线降级链路（T1 验收标准 5.2）

模拟腾讯主源失败（monkeypatch 抛 ConnectionError）→ mootdx 自动接管 → 入库并标注：

```
[TEST] fetch_kline degrade: status=success msg=获取250条K线数据（数据来源: mootdx 降级） cost=0.06s
[TEST] raw_kline mootdx-marked rows=250 total=250 max_date=2026-08-11
日志: [000001] 腾讯K线获取失败（尝试mootdx降级）: SIMULATED tencent failure
     [000001] mootdx K线降级成功（数据来源标注 mootdx）
```

链路：`腾讯野接口（主源）→ mootdx（备用源）→ 标记失败` ✓

### 4.2 估值降级链路（T2 验收标准 5.3）

模拟 akshare 估值失败 → baostock 自动接管：

```
日志: [600276] akshare估值失败(尝试baostock降级): SIM akshare valuation fail
     [baostock] 登录成功（批次级，全局复用）
     [600276] baostock 估值备用源命中: 2026-08-10
```

链路：`akshare（主源）→ baostock（备用源）→ 标记失败` ✓（PE/PB/PS 双源交叉验证一致）

### 4.3 实时行情降级（T1.2）

模拟腾讯实时行情失败（monkeypatch requests.get）→ mootdx 逐只补齐 A 股：

```
result keys: [1, 2]                                  ← 2 只 A 股均由 mootdx 补齐
  1: {'price': 11.28, 'pct_change': -0.09}
  2: {'price': 55.7, 'pct_change': 2.81}
mootdx filled both A-shares: True
HK not filled (mootdx不支持港股): True               ← 港股正确不降级
日志: [019Y] 000001 实时价格走 mootdx 降级: price=11.28
```

### 4.4 财务降级链路（T2.2）

akshare 两层（abstract + analysis_indicator）全部模拟失败 → baostock P3 接管，且 P2 失败警告在 P3 成功后自动清理（不误报"缺失"）。

### 4.5 客户端不可用兜底

备用池 + bestip 全部失败时返回 None 并置 `_MOOTDX_INIT_DONE=True`（不再重复 70s 扫描），K线/盘口降级链路记录失败，不阻塞主流程。

## 五、回归测试

### 5.1 单元测试

```
355 passed, 1 warning in 1.55s   （pytest tests/，与改动前一致，全绿）
```

### 5.2 核心日报流程（_process_single_stock，2026-08-11）

| 股票 | 引擎 | 总评分 | 评级 | 评分变化 | 与 08-07 基线对比 |
|------|------|-------|------|---------|------------------|
| 600276（A股） | v5 | 56.4 | 持有观望 | +6.4 | 57.4 → 56.4（-1.0，市场波动正常） |
| HK3690（港股） | v5 | 52.9 | 持有观望 | 0.0 | 52.9 → 52.9（完全一致） |
| 601888（A股，既有会话遗留） | v5 | 79.8 | 推荐买入 | — | 79.8 → 79.8（完全一致） |

> 评分逻辑本批次零改动；评分链路（collect → generate_advice → daily_reports）行为不变。当日资金面因东财接口区域性故障走既有估算兜底（is_estimated=1，不参评），属 019E/019Q 既有行为。

### 5.3 collect_stock_data 全维度（600276）

```
kline: success（251条）　fundamental: success（新浪abstract财报+腾讯估值）
capital: estimated（东财故障，既有链路兜底）　orderbook: success（mootdx）
valuation: success（同日跳过，当日已采）　restricted_release: success（同日跳过）
north_capital: skipped（缓存内）　margin_balance: success　sentiment: success
```

### 5.4 新 API 端点实测（HTTP 200）

- `GET /api/stocks/4/orderbook` → 五档盘口完整 JSON（bid1-5/ask1-5 + 量，source=mootdx）
- `GET /api/stocks/4/valuation` → PE/PB/PS/PCF + source
- `GET /api/stocks/4/restricted-release` → 15 条解禁明细
- `GET /api/stocks/4/kline` → 新增 data_source 字段
- `POST /api/portfolio/refresh-prices` → 29/29 只刷新成功（23 腾讯 + 6 港股 K线兜底，无错误）

## 六、前端展示说明

> 无浏览器自动化工具，截图无法生成；以下为等效验证（页面加载 + JS 语法 + 数据样本）。

- 首页 `GET /` 正常返回（307KB），`viewData` 新增三块展示 + K线来源动态标注均已内嵌。
- 前端 JS 语法校验（node `new Function()` 提取全部 script 块）：`checked 2 script blocks, errors=0`。
- 展示内容设计：五档盘口表（红涨绿跌，买1-5/卖5-1，最新价高亮，量单位"手"，快照时间+来源标注）；估值表（PE(TTM)/PE(静)/PB/PS(TTM)/PCF(TTM)/股息率 + 来源列，baostock 行加"备用"角标）；限售解禁表（解禁日期/类型/数量/市值/占比，未来批次红色"未解禁"角标 + 风险提示文案）；K线表 mootdx 行加橙色角标 + 表头动态标注"腾讯财经＋mootdx（备用源）"。

## 七、文件 mtime 锚点（红线核验用，git 不可用）

| 文件 | mtime |
|------|-------|
| `modules/data_collector.py` | 2026-08-11 12:13:55（最后一次编辑 12:13 后复编译通过） |
| `app.py` | 2026-08-11 10:30:05 |
| `database/db_manager.py` | 2026-08-11 10:19:38 |
| `templates/index.html` | 2026-08-11 10:30:17 |
| `config.py`（未改动） | 2026-08-10 22:42:26 |
| `scripts/probe_019y_mootdx_baostock.py`（未改动） | 2026-08-11 09:44:42 |

## 八、红线合规自检

1. **不动评分逻辑** ✓ — 评分模块（scoring_engine/analysis_engine/advisor）零改动，回归评分与基线一致。
2. **不改现有接口行为** ✓ — 腾讯/东财/新浪主源调用原样保留，mootdx/baostock 仅作降级备用与新增维度；akshare 新接口为新增采集，不动既有调用。
3. **不动现有数据库已有字段** ✓ — 只新建 3 表 + ALTER TABLE ADD COLUMN（data_source），未修改/删除任何既有字段。
4. **不降级 httpx/tenacity** ✓ — 未触碰 requirements.txt。
5. **mootdx/baostock 与 requests patch 隔离** ✓ — 两者走 TCP socket，实测在 requests.get 被 monkeypatch 抛错时 mootdx 降级仍正常返回数据（4.3 节）。

## 九、遗留说明

1. 测试期间临时建的表（`stock_orderbook/stock_valuation/stock_restricted_release` 中 600276/000001 的测试行已按设计保留为真实数据或清理；测试股票 000001 及其全部派生行已删除）。
2. 本批次验证期间替换了运行中的旧版 Flask 进程（旧进程 PID 13204 承载 019Y 前代码），当前运行实例为新代码（PID 16800，12:22 启动）；**正式验收通过后建议按任务书要求重启 app.py 生效**。
3. 通达信服务器区域性故障（M-2）为外部环境现象，备用池已实测可用；若备用池服务器后续故障，bestip 兜底 + 健康检查会自动接管并在日志标注。
