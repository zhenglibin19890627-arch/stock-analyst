# 开发任务书：019Y 数据源扩展（mootdx + baostock + akshare 维度增强）

> 签发：PM，2026-08-11
> 批次：019Y
> 前置：PM 探针实测已通过（`scripts/probe_019y_mootdx_baostock.py`），mootdx=0.11.7、baostock=0.9.3 已安装

---

## 一、项目环境

- **项目路径**：`C:\Users\zlb19\Desktop\Qoder cn\stock_analyst`（路径含空格，PowerShell 须加引号）
- **Python**：`C:\Users\zlb19\AppData\Local\Programs\Python\Python312\python.exe`（3.12.9）
- **已安装依赖**：`mootdx==0.11.7`（含 tdxpy）、`baostock==0.9.3`
- **依赖注意**：mootdx 声明需要 `httpx<0.26`，但本机 httpx=0.28.1。实测 mootdx 核心行情功能（Quotes/bars/index）走 tdxpy TCP socket，不依赖 httpx，**功能正常**。不得降级 httpx。
- **SQLite**：`stock_analyst.db`（只读访问核验：`sqlite3.connect(r"file:...?mode=ro", uri=True)`）
- **五步中转法**：项目目录文件操作须 Write 到工作区 → Copy-Item 回写 → Select-String 锚点核验 → DeleteFile 删临时副本 → 呈报

---

## 二、任务范围

### T1：mootdx 行情/K线备用源接入

**目标**：在 `data_collector.py` 新增 mootdx 适配层，作为 K线/实时行情的**降级备用源**（不替换主源）。

**具体要求**：

1. **K线降级备用**：现有 K线采集（腾讯野接口为主源）失败时，降级到 mootdx `client.bars(symbol, frequency=9, offset=N)` 获取日K线
   - mootdx 代码格式：沪市股票 `6xxxxx`，深市股票 `0/3xxxxx`（不带 sh/sz 前缀，与 akshare 不同，需做映射）
   - 返回字段：open/close/high/low/vol/amount/datetime，需映射到项目现有 K线表字段
   - 降级标记：在日志和数据库中标注数据来源为 `mootdx`

2. **实时行情备用**：现有实时行情采集失败时，降级到 mootdx `client.quotes(symbol)` 获取实时报价

3. **五档盘口（增量数据）**：mootdx `client.quotes()` 返回 bid1-5/ask1-5 及对应量，这是项目目前没有的新数据维度
   - 新建采集函数，盘口数据存入数据库（新表或新字段，见第四节 schema 约束）
   - 前端展示标注"五档盘口"（数据来源 mootdx）

4. **mootdx 客户端管理**：
   - `Quotes.factory(market='std', bestip=True)` 首次调用需 ~5 秒选服务器，应做**全局单例缓存**，避免每次采集重复初始化
   - 心跳保活（`heartbeat=True`），避免长时间空闲断线

### T2：baostock 估值 + akshare 维度增强

**目标**：引入项目缺失的估值数据（PE/PB/PS）和风险因子（限售解禁），并增加 baostock 作为财务数据备用源。

**具体要求**：

1. **baostock 估值数据（PE/PB/PS）**：
   - `bs.query_history_k_data_plus(code, "date,code,peTTM,pbMRQ,psTTM,pcfNcfTTM", ...)` 获取历史估值
   - code 格式：`sz.000001` / `sh.600276`（需做映射）
   - 需 `bs.login()` / `bs.logout()` 生命周期管理
   - 估值数据存入数据库（新表 `stock_valuation` 或现有表加字段，见第四节）

2. **baostock 财务备用源**：
   - `bs.query_profit_data(code, year, quarter)` 获取 ROE/净利率/毛利率/每股收益等
   - 仅在 akshare 财务接口失败时降级使用，标注来源 `baostock`

3. **akshare 新增接口**：
   - `stock_a_indicator_lg(symbol)` —— 估值指标（PE/PB/PS/股息率/UVOL），作为 baostock 估值的交叉验证源
   - `stock_restricted_release_summary_em(symbol)` —— 限售解禁明细（风险因子）

4. **baostock 客户端管理**：
   - 登录/登出成对管理，建议在采集批次开始时 login、结束时 logout
   - 不可在每只股票采集时重复 login/logout（实测 login 0.04s 但仍应避免）

---

## 三、架构约束（红线）

1. **不动评分逻辑**：本批次只做数据采集 + 入库 + 前端展示标注。**严禁修改评分计算模块**（评分权重调整另立批次）
2. **不改现有接口行为**：现有 akshare 接口、野接口（腾讯/东财/新浪）的主源地位和调用方式不变，新数据源只作降级备用
3. **不动现有数据库已有字段**：新数据维度一律建新表或 `ALTER TABLE ADD COLUMN`，禁止修改/删除已有字段
4. **不降级 httpx/tenacity**：保持 httpx=0.28.1、tenacity=9.1.4，mootdx 核心功能不依赖它们
5. **mootdx/baostock 不经过 requests.Session patch**：两者走 TCP socket，天然隔离，但开发须验证不在 import 阶段触发 requests patch 的副作用

---

## 四、数据库 Schema 约束

开发需新增表/字段来存储：五档盘口、估值数据（PE/PB/PS）、限售解禁。具体 schema 由开发设计，但必须：

1. **新建表**（建议）：
   - `stock_orderbook`（五档盘口）：symbol, trade_date/time, bid1-5_price, bid1-5_vol, ask1-5_price, ask1-5_vol, source
   - `stock_valuation`（估值）：symbol, trade_date, pe_ttm, pb_mrq, ps_ttm, pcf_ncf_ttm, source
   - `stock_restricted_release`（限售解禁）：symbol, release_date, release_shares, release_ratio, source

2. **字段命名**：遵循现有项目命名风格（snake_case，参考 `raw_capital_flow` 表）

3. **source 字段**：每张新表必须有 `source` 字段标注数据来源（`mootdx`/`baostock`/`akshare`），与 `raw_capital_flow.capital_source` 设计一致

4. **兼容性**：新表/新字段不得影响现有表的查询和写入

---

## 五、验收标准

### 5.1 功能验收

| 验收项 | 标准 |
|---|---|
| mootdx K线降级 | 主源失败时自动降级到 mootdx，数据正确入库 |
| mootdx 实时行情降级 | 主源失败时自动降级，五档盘口正确入库 |
| baostock 估值采集 | PE/PB/PS 正确入库，数值合理（非0非空） |
| akshare 新接口 | stock_a_indicator_lg + 限售解禁数据正确入库 |
| baostock 财务备用 | akshare 失败时降级可用 |
| 数据来源标注 | 所有新数据正确标注 source 字段 |
| 现有功能不受影响 | 全量日报流程（不启用新源时）行为不变 |

### 5.2 降级链路（K线为例）

```
腾讯野接口（主源）→ mootdx（备用源）→ 标记缺失
```

### 5.3 降级链路（估值为单向采集）

```
akshare stock_a_indicator_lg（主源）→ baostock（备用源）→ 标记缺失
```

---

## 六、自测报告要求（必须随回件提交）

开发完成后，自测报告必须包含以下内容，缺一不可：

1. **改动文件清单**：列出所有修改/新增的文件（含行数变化）
2. **数据库变更说明**：新建了哪些表、加了哪些字段、是否影响已有数据（附建表 SQL）
3. **接口实测截图/日志**：
   - mootdx：K线降级实测、五档盘口数据样本、客户端单例缓存验证
   - baostock：估值数据样本、财务备用实测、login/logout 生命周期验证
   - akshare：新接口返回数据样本
4. **降级链路验证**：模拟主源失败，验证降级到备用源的全链路日志
5. **回归测试**：跑一次完整日报流程（或核心流程），确认现有功能不受影响，附日报评分结果对比
6. **前端展示**：五档盘口、估值数据的展示效果截图（如有前端改动）
7. **文件 mtime 锚点**：所有改动文件的 mtime 时间戳（用于红线核验，git 不可用）

---

## 七、注意事项

1. **mootdx bestip 耗时**：首次 `Quotes.factory(bestip=True)` 需 ~5 秒，须做全局单例
2. **baostock 代码格式**：`sz.000001` / `sh.600276`，与 akshare（`000001`）、mootdx（`000001`）均不同，需统一映射函数
3. **baostock 港股**：baostock 不支持港股，港股估值仍走 akshare
4. **mootdx 港股**：mootdx 对港股支持有限，港股 K线仍走现有源（腾讯/akshare）
5. **采集频率**：估值/解禁属低频数据（日级/事件级），盘口属高频（盘中实时），注意采集策略区分

---

## 八、交付

- 改动文件 + 自测报告提交给 PM
- PM 独立核验（数字、mtime、查库）后呈报监理验收
- 验收通过后需重启 app.py 生效

---

> 签发：PM，2026-08-11
> 批次：019Y
> 状态：待开发
