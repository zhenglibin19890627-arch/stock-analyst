# 021BW 方案：录入流水时费用实时预估展示（轻量设计）

> 批次：021BW（2026-09-24）｜来源：021BV 备忘项 F-5｜性质：**方案盘点（t1），本批次未改任何运行代码**
> 原则：复用 `modules/trade_fees.py` 既有口径（021BV 银河免5 min=0 / 东财 min=5 刚修复），零写库、零新表、零新依赖。
> 硬约束核对见 §7；备忘另两项待办（需用户数据）标注见 §8。

---

## 0. 现状盘点（已逐文件核实）

### 0.1 modules/trade_fees.py（62 行，纯函数、零网络、离线可测）

| 函数 | 签名 | 行为要点 |
|---|---|---|
| `resolve_broker_profile` | `(account_name) -> dict` | 账户名对 `config.TRADE_FEE_BROKERS` 逐档 `keywords` 子串命中（先命中先用）；无名/未命中回落 `TRADE_FEE_BROKER_DEFAULT`。返回 `{'keywords','commission_rate','commission_min'}` |
| `estimate_trade_fee` | `(trade_type, amount, account_name=None, market='a_stock') -> float` | 非 buy/sell 返回 0；`amount<=0` 或非法返回 0；佣金 = `round(max(amount×rate, min), 2)`（021BV 分项各自取整到分再求和）；A 股 = 佣金 + 过户费 0.001% 双边 +（卖出时）印花税 0.05% 单边；港股 = 佣金 + 印花税 0.1% 双边 + 杂费 0.01%（简化模型） |

费率单一事实源在 `config.py` L162-187：`TRADE_FEE_A_STOCK` / `TRADE_FEE_BROKERS`（银河 万1.853 免5 min=0.0；东方财富/东财 万1.5 min=5.0）/ `TRADE_FEE_BROKER_DEFAULT`（万1.5 min=5.0）/ `TRADE_FEE_HK`。

**关键缺口（本方案要补的唯一后端能力）**：`estimate_trade_fee` 只返回**总额**，不分项。目标要求「佣金/印花税/过户费/合计」分列展示 → 需在 `trade_fees.py` **新增**一个分项纯函数（纯新增，不改既有函数签名与行为，见 §2.1）。

### 0.2 录流水落库端点（blueprints/portfolio/trades.py `api_add_trade`，L262-366）

- `POST /api/portfolio/holdings/<stock_id>/trades`；`amount = body.amount || price×quantity`（L277）；`account_id` 缺省走默认账户（L298-315，`_scope._get_default_account_id`）。
- **自动估算链路（021BK）**：佣金留空且 trade_type ∈ {buy, sell} 时（L318），`SELECT name FROM accounts WHERE id=?` + `SELECT market FROM stocks WHERE id=?` → `estimate_trade_fee(trade_type, amount, account_name=账户名, market=股票市场)`，入库并打 `commission_estimated=1`（佣金>0 时）。
- ⚠️ **口径事实**：落库估算匹配用的是 **accounts.name（账户名）**，不是 broker 字段——与任务书所述「account_id→accounts.broker→keywords」存在分歧，处理见 §2.2（本方案最重要的一个设计决策）。

### 0.3 账户与券商字段现状

- `accounts` 表含 `broker TEXT DEFAULT ''`（`database/_db/_schema_portfolio.py` L21）。
- 账户 CRUD 完整支持 broker（`blueprints/portfolio/accounts.py` POST/PUT 均持久化；GET `/api/accounts` 返回 broker，前端 `_accountsCache` 已携带）。
- 即：**broker 字段已存在、可填、已下发前端**，只是费用估算从未消费它。

### 0.4 录流水表单（前端挂点盘点）

- 弹窗为静态标记：`templates/index.html` L469-496 `#tradeModal`。表单字段：`tradeAccountSelect`（归属账户）/ `tradeType`（buy/sell/dividend/dividend_tax）/ `tradePrice` / `tradeQty` / `tradeAmount`（可选直填）/ `tradeCommission`（留空=自动估算）/ `tradeDate` / `tradeNotes`。
- `static/js/portfolio.js`（普通脚本、body 尾加载、DOM 已就绪；函数全局供 onclick 使用）：
  - `openTradeModal`（L986）打开时填账户下拉（`_accountOptionsHtml`，当前筛选账户或其默认）；
  - `addTrade`（L1196）提交时**佣金留空则不传字段**（后端估算）；`amount` 直填优先；
  - 编辑模式 `_editingTradeId` → `saveEditTrade` 走 PUT——**PUT 无自动估算**（佣金手填、填了即清除估算标记）；
  - 既有可循先例：document 级委托 change 监听（L840）、null-guard 直接绑定（market.js L31）、搜索防抖 timer 模式（`_searchTimer`，L694-695）。
- `_tradesCache` 区域（文件头 L4-60）是全局流水列表，与本特性无关（预估条只挂在弹窗表单内）。

---

## 1. 目标行为（一句话验收）

用户在持仓页打开「交易流水」弹窗，填**价格 × 数量**、切**买/卖**、换**账户**（或直填金额），约 300ms 内在表单下方看到四行分列：**佣金（含最低佣金规则或免5）/ 印花税（仅卖出）/ 过户费（双边）/ 合计**，口径与该账户实际落库的自动估算**完全一致**。

---

## 2. 后端设计

### 2.1 trade_fees.py：新增分项纯函数（唯一事实源下沉）

纯**新增**，既有两个函数签名与对外行为零改动（既有 `tests/test_trade_fees_021bk.py` 12+ 用例即回归网）：

```python
def estimate_trade_fee_items(trade_type, amount, account_name=None, market='a_stock'):
    """分项估算（与 estimate_trade_fee 同参同口径）。

    Returns: {
        'commission': float,    # 佣金（含最低佣金/免5 规则后，两位小数）
        'stamp_tax': float,     # 印花税（A股仅卖出；港股双边）
        'transfer_fee': float,  # 过户费（A股双边；港股为 0）
        'misc_fee': float,      # 港股杂费（A股恒 0）
        'total': float,         # 分项和（两位小数）
        'applied_rules': [str], # 逐条中文规则说明（禁裸 '<'，见 §4.5）
        'by_rate': bool,        # 佣金是否走纯费率（False=按地板/最低佣金计）
    }
    """
```

实现即把 `estimate_trade_fee` 现有函数体按分项展开（同样的 `round(max(amount*rate, min), 2)` 佣金式、同样的分项取整求和），然后 **`estimate_trade_fee` 改为一行 `return estimate_trade_fee_items(...)['total']`**——单一事实源，杜绝两处口径漂移。总额等价性由既有用例 + 新增一致性用例（§6）双锁。

### 2.2 账户→券商配置映射（口径一致性决策，本方案核心）

任务书口径：`account_id → accounts.broker → TRADE_FEE_BROKERS keywords 匹配`。
落库现状：`api_add_trade` 用 `accounts.name` 匹配。

**两者若不同步，预估条与落库佣金会分叉**（例：账户名「我的主账户」+ broker「银河证券」→ 预估免5、落库按默认档 5 元地板），直接击穿本特性可信度。因此二选一，**推荐 A**：

- **方案 A（推荐）**：`trade_fees.py` 新增映射 helper，broker 优先、name 兜底：

  ```python
  def resolve_broker_profile_for_account(name, broker=None):
      """broker 非空先 keywords 匹配，未命中或为空回落 name，双空 → 默认档。"""
  ```

  新端点与 `api_add_trade` **同步**改走该 helper（trades.py 触点仅 L322-330 一处：`SELECT name` → `SELECT name, broker` + 换调用）。行为影响面：仅「broker 非空且 broker 命中而 name 不命中」的账户，方向是**严格更正确**；存量账户（broker 多为空串）行为零变化。`resolve_broker_profile(account_name)` 原样保留（= name 兜底语义），既有测试不动。
- **方案 B（零触碰备选）**：端点与 POST 一样只用 name，broker 字段本批继续闲置。代码触点最少（trades.py 完全不动），一致性同样成立；代价是「所属账户券商配置」承诺缩水。若实现轮希望 trades.py 零改动可选此路，后续批次再升 A。

以下设计按方案 A 书写（B 仅省去 helper 与 POST 一行改动，其余完全相同）。

### 2.3 新端点：`GET /api/portfolio/fee-estimate`

落点 `blueprints/portfolio/trades.py`（流水域内、与 POST 共享 `_scope` 助手与导入面；`portfolio/__init__.py` facade 无需新增导出——路由经 `@bp.route` 自挂）。

**纯计算零写库**：至多两条 SELECT（accounts 的 name/broker；可选 stocks 的 market），无 INSERT/UPDATE、无事务、无新表。

| 参数 | 必填 | 说明 |
|---|---|---|
| `trade_type` | 是 | `buy/sell/dividend/dividend_tax`，与 POST 同一枚举校验，非法 → 400 |
| `price` / `quantity` | 否 | 缺省或非法数字按 0 处理；`quantity` 按 int 截断（同 POST `int(quantity)`） |
| `amount` | 否 | 金额直填优先（>0 时覆盖 `price×quantity`，与 POST L277 同式） |
| `account_id` | 否 | 缺省 → 默认账户（同 POST 口径）；不存在 → 404 |
| `stock_id` | 否 | 用于取 `stocks.market` 判定 A股/港股；缺省按 `a_stock`。**建议带上**：tradeModal 打开时 `currentTradeStockId` 现成，港股股票的印花税口径差异显著（0.1% 双边 vs 0.05% 卖出单边），缺省会算错 |

**成功响应（200，`amount<=0` 也返回 200 全零——供前端渲染空态，不算错误）**：

```json
{
  "success": true,
  "input": {"account_id": 2, "stock_id": 17, "market": "a_stock",
            "trade_type": "sell", "price": 10.0, "quantity": 1000, "amount": 10000.0},
  "account_name": "银河证券",
  "broker_label": "银河档：万1.853·免5",
  "commission": 1.85,
  "stamp_tax": 5.00,
  "transfer_fee": 0.10,
  "misc_fee": 0.00,
  "total": 6.95,
  "applied_rules": [
    "佣金：按费率万1.853 计（免5，无最低佣金）",
    "印花税：0.05%，仅卖出收取",
    "过户费：0.001%，买卖双边收取"
  ],
  "estimation_note": "估算值仅供参考，以次日交割单为准；可在编辑流水中改为实际值"
}
```

- `broker_label` 规则：命中 `TRADE_FEE_BROKERS` 档 → 「{keywords[0]}档：万{rate×10000 去尾零}·{min==0 ? '免5' : '最低佣金'+min+'元'}」；默认档 → 「默认档：万1.5·最低佣金5元」。
- `applied_rules` 佣金条按实际计价方式二选一：「按费率万X 计」或「按最低佣金 5 元计（金额低、费率不足 5 元）」（由 `by_rate` 区分）；港股追加「港股为简化模型：印花税 0.1% 双边 + 杂费约 0.01%，未含组合费/汇兑等」。
- dividend/dividend_tax：四项全 0，`applied_rules: ["分红/红利补税类型不计交易费用"]`（与 POST 不自动估算同口径）。

**错误**：`trade_type` 非法或数字解析失败 → 400 + `success:false`；`account_id` 不存在 → 404。价格/数量为空或 0 **不是错误**（200 全零 + `applied_rules: ["填入成交价和数量后显示预估"]`）——前端防抖高频触发下保持无错误分支最简。

---

## 3. 前端挂点（static/js/portfolio.js + index.html 一行占位）

### 3.1 触发事件与防抖

```js
var _feeEstTimer = null, _feeEstSeq = 0;

function scheduleFeeEstimate() {            // debounce ~300ms（复用 _searchTimer 同款模式）
    if (_feeEstTimer) clearTimeout(_feeEstTimer);
    _feeEstTimer = setTimeout(fetchFeeEstimate, 300);
}
```

绑定（null-guard 直接绑定即可——脚本 body 尾加载、`#tradeModal` 是静态标记；同 market.js L31 先例）：

| 元素 | 事件 |
|---|---|
| `tradePrice` / `tradeQty` / `tradeAmount` | `input` |
| `tradeType` / `tradeAccountSelect` | `change`（切方向即时重算，印花税差异立现） |
| `tradeCommission` | `input`（仅切换提示态，见 §3.3） |

### 3.2 请求与渲染

- `fetchFeeEstimate()`：从表单取参——`account_id` 同 `addTrade` L1209-1210 取法（`tradeAccountSelect.value`，'all' 视为缺省）；`stock_id = currentTradeStockId`；`amount` 直填优先。seq 自增携带，响应回来 `seq` 不匹配即丢弃（防慢响应回写旧值）。
- 渲染目标：`index.html` 在表单输入行（L488）与备注行（L489）之间插入一行占位：

  ```html
  <div id="feeEstimateBox" style="display:none;margin:8px 0 4px;padding:8px 12px;
       background:var(--surface-2,#f8f9fa);border:1px solid var(--border,#e0e0e0);
       border-radius:6px;font-size:13px;"></div>
  ```

- 分列四行 + 规则说明小字，金额 `toFixed(2)`；`commission/stamp_tax/transfer_fee/misc_fee` 为 0 的行显示 `0.00`（买入时印花税 0.00 本身就是信息：「卖出才有印花税」）。全零空态显示服务端给的 `applied_rules` 提示文案。
- fetch 失败：静默隐藏预估条，**绝不 alert、绝不阻断录入**（预估是增强，不是闸门）。

### 3.3 状态细节（防误导的三条）

1. **编辑模式隐藏**：`_editingTradeId` 非空时隐藏预估条——PUT 不做自动估算、佣金以手填为准，展示会误导；`cancelEditTrade` 恢复。
2. **手填佣金时**：`tradeCommission` 非空 → 预估条顶部加一行「已手填佣金，落库以手填值为准」，分列照常显示（过户费/印花税仍有效参考）。
3. **dividend/dividend_tax**：直接渲染服务端全零响应与规则文案（「分红/红利补税类型不计交易费用」），前端无需特判。

### 3.4 文案红线

前端所有预估文案（含 `applied_rules` 逐条插入 innerHTML）**禁裸 `<`**：服务端保证规则串不含 `<`（如写「金额低、费率不足地板」而非「费率小于地板」）；前端不拼接任何用户输入进 innerHTML，数字一律 `toFixed` 后插入。

---

## 4. 边界与口径说明

| 边界 | 口径 |
|---|---|
| 价格/数量空、0、负数 | 服务端按 `amount<=0` 返回全零 + 空态文案；前端显示空态，不报错 |
| `quantity` 小数 | int 截断（与 POST `int(quantity)` 同式）；`price` 两位内浮点原样参与 |
| 金额直填 vs 价×量 | `amount > 0` 优先（与 POST L277/落库基数同式），预估基数与落库基数永不分叉 |
| 买入 vs 卖出 | A 股印花税仅卖出：买入行 `stamp_tax=0.00` + 规则说明「仅卖出收取」；港股双边都收 |
| 港股（简化模型标注） | market 取自 `stocks.market`（股票属性、非账户属性，故端点要 `stock_id`）；模型 = 佣金（同券商档）+ 印花 0.1% 双边 + 杂费 0.01%，`transfer_fee=0`、`misc_fee>0`；`applied_rules`/`estimation_note` 明示「简化模型，未含组合费/汇兑等，以交割单为准」 |
| 分红/红利补税 | 全零（POST 对这两类本就不估算，口径一致） |
| 账户缺省/不存在 | 缺省 → 默认账户档；不存在 → 404（前端此时隐藏预估条） |
| 佣金手填 | 不影响预估条数学，仅切换提示态（§3.3-2）；落库永远尊重手填值（POST L282-289 既有语义） |
| 免5/地板临界 | 沿用 021BV 分项舍入口径（`round(max(amount×rate, min), 2)`），`by_rate` 如实标注本次走费率还是地板；`tests/test_trade_fees_021bk.py` 的 26,983 连续性用例继续兜底 |

---

## 5. 测试策略

### 5.1 新增 `tests/test_fee_estimate_021bw.py`（fixture 仿 `test_trade_fees_021bk.py`：tmp_path 隔离库 + test client + 预置「银河证券」账户、默认账户、a_stock 与 hk_stock 各一只）

| 组 | 用例要点 |
|---|---|
| 端点·A股 | 银河买 1 万 → 1.85/0.00/0.10/1.95 且规则含「免5」；银河卖 1 万 → 6.95 且含「仅卖出」；东财买 4,674 → 佣金 5.00（地板）+ 规则含「最低佣金 5 元」；缺省账户 → 默认档 5.1 |
| 端点·映射 | 方案 A：name=「主账户」broker=「东方财富」→ 东财档（锁 broker 优先）；broker 空 → name 兜底；双空 → 默认档 |
| 端点·港股 | `stock_id` 指向 hk_stock → 印花双边 + 杂费、`transfer_fee=0`、说明含「简化模型」 |
| 端点·边界 | dividend/dividend_tax 全零；price/qty 缺省、0、负 → 200 全零；`price=abc` → 400；`trade_type=foo` → 400；不存在账户 → 404；`amount` 直填优先于价×量 |
| 一致性 | 参数矩阵循环断言：端点 `total` == `estimate_trade_fee(同参)`、分项和 == total——**防端点与纯函数两处漂移的锁** |
| 零写库 | GET 前后 `trade_records`/`holdings` 行数不变（一条用例即可） |

### 5.2 回归与门禁

- 既有 `tests/test_trade_fees_021bk.py` **必须全绿**（021BV 免5/分项舍入口径未被扰动；若实现方案 A，其「留空佣金自动估算」用例继续锁定 POST 行为）。
- `python -m pytest tests/`（fast 层）全绿；红线 `python scripts/check_redlines.py` 28/28。
- `ruff check .`、`mypy app.py config.py modules`（`trade_fees.py` 在 mypy 范围内，新函数需带类型注解）。
- 前端无 JS 测试设施 → 手工验收清单：盘中路径打开弹窗 → 输价输量见分列 → 切买/卖（印花税 0↔5.00）→ 银河↔东财账户切换（免5↔地板）→ 直填金额 → 空态 → 编辑模式隐藏 → 手填佣金提示。逐项对照 §1 一句话验收。

---

## 6. 实施拆分建议（供排期参考，工作量小）

1. **后端**：`trade_fees.py` 增 `estimate_trade_fee_items` +（方案 A）`resolve_broker_profile_for_account` 并把 `estimate_trade_fee` 改为 total 透传；`trades.py` 增 GET 端点 +（方案 A）POST L322-330 同步 helper。
2. **前端**：`index.html` 一行占位 div；`portfolio.js` 增 debounce/seq/渲染/绑定约 60 行。
3. **测试**：`tests/test_fee_estimate_021bw.py` 一文件。

---

## 7. 硬约束核对表

| 约束 | 结论 |
|---|---|
| B24（`advisor.generate_advice` 禁改） | ✅ 不涉及 advisor 模块 |
| R7（`scoring_engine.py` 核心禁改） | ✅ 不涉及评分引擎 |
| `classify_stage` 零触碰 | ✅ 该函数在 `modules/trader_advisor.py`，不涉及 |
| R16 风控阈值五项不动 | ✅ `TRADE_FEE_*` 不属 R16 五项；本方案不触碰 config 风控段；GET 无风控交互 |
| 零写库、零新表 | ✅ 端点至多两条 SELECT；`trade_fees.py` 纯函数新增 |
| 文案禁裸 `<` | ✅ §3.4 服务端规则串与前端渲染双向约束 |
| 零新依赖（R17） | ✅ 无新 pip 包 |
| 全量测试保持绿 | ✅ §5.2 门禁；既有费用用例即回归网 |

---

## 8. 待办标注（021BV 备忘另两项，需用户数据，本轮不动代码）

1. **银河免5 交割单闭环**：等用户拿到银河真实交割单回验 min=0 口径；若显示最低 5 元，按 `config.py` 注释回退（`commission_min` 改回 5.0 + 重跑 `scripts/reevaluate_fees_021bv.py --apply`，R11 备份在）。**本特性上线后，预估条（免5 文案）与交割单并排即是天然核对面**，建议闭环时顺带截图。
2. **中免东财流水处置**：等用户提供该批流水的处置意向（改实填/保留估算标记）。

---

## 9. 基线记录

- `python scripts/check_redlines.py`：**28/28 全部 PASS**（2026-09-24，本批次开工前实测；控制台中文因 GBK 显示乱码，PASS 标记与计数清晰）。
- 本方案为纯文档产出，未修改任何运行代码；`docs/RED_LINES.md`、`modules/trade_fees.py`、`blueprints/portfolio/trades.py`、`static/js/portfolio.js`、`config.py` 均只读盘点。
