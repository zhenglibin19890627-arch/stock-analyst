# 《通用数据完整度提升技术方案》

| 项目 | 内容 |
|---|---|
| **文档版本** | v1.0 |
| **编制时点** | 2026-07-20 |
| **编制方** | 开发执行方 (GLM) |
| **状态** | 待监理审核 |
| **前置任务** | P3-A 引擎对齐修复(已验收通过) |
| **下游依赖** | M8 回测框架(数据基础设施输入) |
| **方法论** | 全量统计(禁止抽样) + 严格只读排查 + 设计未实施 |

---

## 一、现状诊断(全量统计)

### 1.1 股票池范围

| 市场 | 标的数 | 备注 |
|---|---|---|
| a_stock | 11 | 自选股 |
| hk_stock | 1 | 港股通样板(HK3690 美团-W) |
| **合计** | **12** | 当前 P3-A 灰度白名单 |

> **范围说明**: 当前股票池仅 12 只,非全市场。但本方案所有改进面向"通用生效",即未来扩展至 5000+ 全 A 股 + 港股通 500+ 时无需返工。完整率统计采用全量(12/12)而非抽样。

### 1.2 四维度采集完整率分布(近30日,基于 data_status 表)

| 市场 | 维度 | 总采集次数 | 成功 | 成功率 | 失败次数 |
|---|---|---|---|---|---|
| a_stock | fundamental | 154 | 154 | **100.0%** ✅ | 0 |
| a_stock | kline | 154 | 154 | **100.0%** ✅ | 0 |
| a_stock | sentiment | 154 | 134 | 87.0% 🟡 | 20 |
| a_stock | **capital** | 157 | 79 | **50.3%** 🔴 | 78 |
| hk_stock | fundamental | 17 | 0 | **0.0%** 🔴🔴 | 17 (NEVER_SUCCESS) |
| hk_stock | kline | 17 | 12 | 70.6% 🟡 | 5 |
| hk_stock | **capital** | 19 | 9 | **47.4%** 🔴 | 10 |
| hk_stock | sentiment | 17 | 15 | 88.2% 🟢 | 2 |

**关键发现**: capital 维度在 A/H 双市场均 ≤50%,为系统性短板;港股 fundamental 从未成功。

### 1.3 标的级缺失模式分类(近30日)

| 市场\|维度\|模式 | 标的数 | 含义 |
|---|---|---|
| a_stock\|capital\|INTERMITTENT | **11** | 全部为间歇失败(批量限流) |
| a_stock\|sentiment\|INTERMITTENT | 9 | 间歇失败 |
| a_stock\|sentiment\|ALWAYS_SUCCESS | 2 | 稳定 |
| a_stock\|kline\|ALWAYS_SUCCESS | 11 | 稳定 |
| a_stock\|fundamental\|ALWAYS_SUCCESS | 11 | 稳定 |
| hk_stock\|capital\|INTERMITTENT | 1 | 间歇失败 |
| **hk_stock\|fundamental\|NEVER_SUCCESS** | **1** | **结构性缺失** |
| hk_stock\|kline\|INTERMITTENT | 1 | 间歇失败 |

**分类结论**:
- **间歇失败**(13标的×维度组合):根因采集链路鲁棒性 → 本方案可治
- **从未成功**(1组合,港股财务):根因数据源覆盖 → 需新增源

### 1.4 实际数据覆盖断层(raw_capital_flow 表)

| 交易日 | A股写入数 | 港股写入数 | 备注 |
|---|---|---|---|
| 2026-07-13 ~ 07-17 | 11/11 | 1/1 | 全市场稳定覆盖 ✅ |
| 2026-07-18(周四) | 9/11 | **0/1** | 港股资金面断层 |
| 2026-07-20(周一) | **1/11** | **0/1** | 批量限流重灾日(仅601888成功) |

### 1.5 失败原因聚类(基于 data_status 错误消息)

| 排名 | 错误特征 | 出现次数 | 根因分类 |
|---|---|---|---|
| 1 | "东方财富接口全部失败(push2his/push2/akshare)" | **34** | C1 实证的批量限流 |
| 2 | "A股消息面数据暂不可用,公告/研报/情绪因子" | 20 | 消息面源缺失 |
| 3 | "东方财富push2接口无法访问(直连和代理均失败)" | 11 | 限流 + 部分 IP 封禁 |
| 4 | "东方财富push2接口无法访问...Connection aborted" | 15 | TCP 被动断开(限流典型) |
| 5 | "港股财务指标数据为空; PE/PB获取失败(腾讯接口无响应)" | 5 | 港股财务双重缺失 |

### 1.6 采集源代码梳理(基于 [data_collector.py](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_collector.py))

| 维度 | 主源 | 备用源 | 已禁用估算源 |
|---|---|---|---|
| kline | 腾讯 qt.gtimg.cn | - | - |
| fundamental(A) | akshare 财务指标 | - | - |
| **fundamental(HK)** | **akstock_hk_analysis_indicator** | **腾讯 PE/PB** | - |
| sentiment | 公告/研报爬取 | - | - |
| **capital** | 东方财富 push2his | 东方财富 push2 → akshare | 腾讯K线估算/新浪/网易(均 if False 禁用) |

**反风控机制现状**(从记忆 "持仓管理MVP与资金面6层Fallback策略" 引用):
- 22 个真实 UA 随机轮换 ✅
- random.uniform(1.5, 3.5) 秒请求延迟 ✅
- 代理健康检查(连续失败2次禁用30分钟)✅
- 超时控制(connect=5s, read=10s)✅

**反风控缺口**: 缺少**批量请求间速率控制**(批量12只顺序请求总间隔≈36s,但东方财富对短时间内的多账户请求仍触发限流)。

---

## 二、数据源扩展方案(P0-P1级)

### 2.1 P0 候选源(立即落地,实证可用)

#### 候选源 #1: 同花顺全市场资金流向批量接口(✅ 实证已完成)

- **接口**: `ak.stock_fund_flow_individual()` (来源: `data.10jqka.com.cn`)
- **核心优势**: **单次调用返回全市场 5197 只 A 股当日资金流向**(非逐只)
- **实证结果(2026-07-20 21:40,3次连续调用)**:
  - ✅ **成功率 3/3 = 100%**,无限流迹象
  - ✅ **平均耗时 11.37s**(首调 15.05s,后续 9-10s,含全市场 5197 只)
  - ✅ **行数稳定性 100%**(三次均返回 5197 行)
  - ⚠️ **实际字段与记忆不符**(见下方修正)
  - ✅ int64 坑点确认: 股票代码 dtype 为 int64,需 `int(symbol)` 转换
- **实际字段(实证测得,2026-07-20 修正)**:
  ```
  ['序号', '股票代码', '股票简称', '最新价', '涨跌幅',
   '换手率', '流入资金', '流出资金', '净额', '成交额']
  ```
- **⚠️ 设计修正(重要)**:
  - 原记忆称"含主力/超大单/大单/中单/小单净额及占比" → **实证为不实**
  - 实际只有 `净额` 字段(总净额) + `流入资金` + `流出资金` + `成交额`
  - **缺失**: 超大单/大单/中单/小单的分级拆分
  - **影响评估**: v5引擎 `raw_capital_flow.main_net_inflow` 字段可由同花顺 `净额` 填充(A股 capital 完整率仍可从 50.3% 提升至 95%+),但 `super_large_net/large_net/medium_net/small_net` 四个分层字段需保留东方财富逐只采集
  - **双源协同策略**: 同花顺批量先填主字段(防限流)+ 东方财富逐只补分层(允许失败,失败时分层为空但主字段已有数据)
- **频率约束**: 1 小时缓存(避免重复下载,符合实证中单次 11s 的成本)
- **实证日志附件**: `_p0_ths_stress_result.json`(实施时复用)
- **拟落地路径**: 在 [fetch_capital_flow](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_collector.py#L1056) 上层新增**批量采集入口** `fetch_capital_flow_batch(stock_ids)`,优先走同花顺批量源,失败再降级逐只东方财富
- **覆盖率提升预估**: A股 capital 完整率 50% → **95%+**(主字段),分层字段 50% → 70%(允许降级)

#### 候选源 #2: 港股资金面东方财富历史专用接口(已存在但未充分利用)

- **接口**: `_fetch_capital_flow_em_individual` ([L730](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_collector.py#L730) push2his.eastmoney.com)
- **问题诊断**: 港股 secid 计算可能存在问题(需核对 `_get_em_secid` 对港股的处理)
- **改进**: 复核 secid 映射,确保港股走对路径
- **实证要求**: 改造后单独对 HK3690/HK00700/HK09988 三只港股实测

### 2.2 P1 候选源(观察期后落地)

| 候选源 | 用途 | 频率 | 实证要求 |
|---|---|---|---|
| 腾讯 qt.gtimg.cn 实时行情 | 港股 PE/PB 补齐 | 实时 | 当前已用于估值,可扩展用于港股财务 |
| akshare `stock_hk_hot_rank_em` | 港股情绪指标 | 日级 | 替代失效的港股公告源 |
| 雪球财经 API(需评估) | 港股财务补齐 | 季度 | 待调研 |

### 2.3 红线约束遵守

- ❌ **严禁恢复任何估算 fallback**(腾讯/新浪/网易保持 `if False` 禁用)
- ❌ **不修改 [fetch_capital_flow](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_collector.py#L1056) 函数签名**
- ❌ **不修改 [L1092](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_collector.py#L1092) 防覆盖 / [L1225](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\modules\data_collector.py#L1225) early return**(仅可前置校验层)
- ✅ 每新增源必须附可用性实证(SQL查询 + 单次调用日志)

---

## 三、采集鲁棒性方案(P0级,C1 根因直接输入)

### 3.1 批量速率控制器(新增模块)

**新增文件**: `modules/rate_limiter.py`

```python
# 设计草案(未实施)
class BatchRateLimiter:
    """批量采集速率控制器

    策略:
    1. 令牌桶: 6 QPS(初始保守值,见下方依据)
    2. 指数退避: 首次失败 sleep 2s, 第二次 4s, 第三次 8s
    3. 熔断: 连续 5 只失败 → 暂停 60s
    4. 多IP轮换: 主IP失败后切换代理池(代理健康检查已有)
    """

    def __init__(self, name: str, qps: int = 6, max_retries: int = 3): ...
    def acquire(self) -> bool: ...  # 获取令牌
    def report_failure(self, error_type: str): ...
    def report_success(self): ...
```

**6 QPS 参数依据(补强说明)**:
- ❌ 无官方文档依据(东方财富/同花顺均未公开 QPS 限制)
- ✅ **基于实测反推**: C1 实证中 batch-analyze 12只顺序请求总耗时 ≈ 36-48s,单只 3-4s,符合 0.3 QPS 节奏,但仍触发限流 → 推断东方财富对**单IP批量多账户请求**有更严格阈值(可能低于 1 QPS)
- ✅ **6 QPS 为"反向加严"初始值**: 实施阶段将根据压力测试动态校准(预计首周调整为 1-2 QPS)
- ✅ **与同花顺批量源协同**: 同花顺批量1次请求替代东方财富 12×3=36 次,从根本上不触发东方财富限流

**集成点**: [app.py:977](file://c:\Users\zlb19\Desktop\Qoder%20cn\stock_analyst\app.py#L977) batch-analyze 循环内,在 `collect_stock_data` 调用前后包裹速率控制。

### 3.2 批量采集优化路径

```
batch-analyze(stock_ids):
    # 新增步骤0: 批量预取资金面(同花顺单次调用)
    capital_batch = fetch_capital_flow_batch()  # 5000+ 只 1次请求
    upsert_raw_capital_flow(capital_batch)     # 全市场一次写入
    
    # 现有步骤1-2: 逐只采集其他维度(资金面已批量预填,fetch_capital_flow内部走防覆盖跳过)
    for sid in stock_ids:
        rate_limiter.acquire()                 # 等待令牌
        collect_stock_data(sid)                # 资金面防覆盖skip,kline/基本面/消息面正常采集
        generate_advice(sid)
```

**效果**: batch-analyze 12只时,东方财富请求从 12×3层=36次 → **1次同花顺批量调用 + 0次东财**,从根因消除限流。

### 3.3 重试退避矩阵

| 失败类型 | 重试次数 | 退避策略 | 数据源切换 |
|---|---|---|---|
| ConnectionAbortedError | 2 | 2s/4s 指数退避 | 切代理/切备用源 |
| HTTP 429/503 | 3 | 5s/10s/20s | 强制切备用源 |
| 空数据(200但无内容) | 1 | 直接切备用源 | 不重试同源 |
| Timeout | 2 | 5s/10s | 切代理 |
| 解析异常 | 0 | 不重试 | 上报错误日志 |

---

## 四、防覆盖前置校验层(P1级)

### 4.1 字段级完整性校验

**新增模块**: `modules/data_validator.py`

```python
# 设计草案(未实施)
FIELD_RULES = {
    'raw_capital_flow': {
        'required': ['stock_id', 'trade_date', 'main_net_inflow'],
        'not_null_when': {'main_net_inflow_pct': 'main_net_inflow != 0'},
    },
    'raw_fundamental': {
        'required_one_of': [['roe', 'gross_margin', 'net_margin', 'pe_ratio', 'pb_ratio']],
    },
    # ... 其他表
}


def validate_before_insert(table: str, row: dict) -> tuple[bool, str]:
    """写入前字段级校验,拦截空数据/无效数据"""
    # 不修改现有 fetch_capital_flow / save_data_status 逻辑
    # 仅作为前置 gate,validate 失败直接返回 'failed' 不进入 INSERT
```

**集成点**: 在所有 `INSERT INTO raw_*` 语句前包裹 `validate_before_insert` 调用。

### 4.2 与现有防覆盖机制关系

- **不替换** L1092 同日防覆盖 / L1225 early return(保持现状)
- **前置**: 字段校验在数据源解析后、写入前执行,作为第三层保护
- **失败处理**: 校验失败 → 记录 `data_status.status='invalid_data'`(新枚举) + 错误日志

---

## 五、监控告警 + 完整度看板(P0级)

### 5.1 系统级完整度实时看板

**新增 API**: `/api/data-health/dashboard`

```python
# 设计草案(未实施)
@app.route('/api/data-health/dashboard')
def data_health_dashboard():
    """返回四维度完整度实时快照"""
    return jsonify(
        {
            'by_market_dim': {
                'a_stock|capital': {'today_rate': 0.95, 't-1_rate': 0.91, 'trend': '↑'},
                # ...
            },
            'stale_stocks': [...],  # 最新数据早于 T-2 的标的
            'never_success': [...],  # 港股 fundamental 等
            'last_batch_status': {...},
        }
    )
```

### 5.2 阈值告警(接入 Flask 日志体系)

| 指标 | 阈值 | 动作 |
|---|---|---|
| 单维度当日完整率 | < 80% | ⚠️ WARNING 日志 |
| 单维度当日完整率 | < 50% | 🔴 ERROR 日志 + 前端红色提示 |
| 单标的连续失败 | ≥ 3 日 | 🔴 ERROR 日志 + 加入 stale_stocks |
| 批量采集熔断触发 | 任意 | 🔴 ERROR + 推送 /api/engine/status |

### 5.3 与现有日志体系集成

- 复用 Flask `logger`(避免新引入 logging 框架)
- 复用 `data_status` 表(不新增表)
- 复用 `error_logs` 表(扩充 error_type 枚举)

---

## 六、评分弹性系数量化方案(只读模拟,P0级)

### 6.1 弹性系数定义

> **完整度-评分弹性系数 ε** = |ΔScore| / ΔCompleteness
>
> 即:数据完整度提升 1% 时,评分变化绝对值的均值。

### 6.2 只读模拟方法(不修改 v5 权重)

**新增只读脚本**: `scripts/simulate_completeness_elasticity.py`

```
算法:
1. 选取 12 只白名单的 07-17(全数据)评分作为基线 Score_full
2. 模拟移除单一维度(如 capital)→ 用 v5 引擎评分 Score_no_capital
3. 计算单维度弹性: ε_dim = |Score_full - Score_no_capital| / 1.0
4. 输出: {'capital': 8.3, 'sentiment': 2.1, 'fundamental': 5.7, 'kline': 4.2}
5. 解读: capital 弹性最高,优先提升其完整度
```

### 6.3 M8 回测兼容性

- 本方案输出的弹性系数 → 作为 M8 回测框架的"数据完整性扰动因子"输入
- M8 可用此系数生成合成缺失场景,验证评分引擎鲁棒性

---

## 七、分级标注 + 成本估算

### 7.1 P0(立即落地,观察期内不引入评分变量)

| 改进项 | 预期完整率增量 | 成本(人天) | M8 兼容 |
|---|---|---|---|
| 同花顺批量资金面源(2.1#1) | A股 capital +45% | 1.0 | ✅ 全市场数据基础 |
| 批量速率控制器(3.1) | 批量场景 +20% | 1.5 | ✅ 提供稳定历史数据 |
| 批量预取路径重构(3.2) | capital 整体 +30% | 0.5 | ✅ |
| 监控告警 + 看板(5) | 可观测性大幅提升 | 1.0 | ✅ 回测质量监控 |
| 弹性系数模拟(6) | 量化输入 | 0.5 | ✅ M8 输入 |
| **小计** | **+50%+** | **4.5 人天** | |

### 7.2 P1(观察期后,与 Batch-2 同期)

| 改进项 | 预期完整率增量 | 成本(人天) | M8 兼容 |
|---|---|---|---|
| 港股财务源扩展(2.1#2 + 2.2) | 港股 fundamental 0→80% | 2.0 | ✅ |
| 字段级前置校验层(4) | 数据质量 +10% | 1.5 | ✅ |
| **小计** | | **3.5 人天** | |

### 7.3 P2(长期,M8/M9 之后)

| 改进项 | 备注 |
|---|---|
| 多 IP 代理池接入 | 需评估付费代理 ROI |
| 雪球/雪球 API 评估 | 待调研 |
| 全 A 5000+ 覆盖实证 | 灰度至 Batch-3 |

### 7.4 结构性不可解维度

| 维度 | 现状 | 建议权重调整方向 |
|---|---|---|
| 港股 fundamental | 0%(akshare 港股财务覆盖不全) | v5 港股权重:降低 fundamental 至 10%,提升技术面至 40% |

---

## 八、实施计划(待监理授权)

### 8.1 排期(以观察期后启动为前置)

```
T+0(观察期结束 07-21 18:30 后): 监理审核本方案 → 授权实施
T+1: 实施 P0-1(同花顺批量源,1天)
T+2: 实施 P0-2(速率控制器,1.5天)
T+3: 实施 P0-3(批量预取路径,0.5天)
T+4: 实施 P0-4(监控告警看板,1天)
T+5: 实施 P0-5(弹性系数模拟,0.5天)
T+5 当日: P0 联调验收
T+6 起: 进入 M8 回测框架方案设计(以本方案数据基础设施为输入)
```

### 8.2 风险与回退

| 风险 | 概率 | 影响 | 回退方案 |
|---|---|---|---|
| 同花顺接口被封/限流 | 中 | P0 主方案失效 | 保留东方财富逐只采集,仅速率控制生效 |
| 速率控制器误判 | 低 | 单次 batch-analyze 慢 | 配置可热更新,降级 QPS 阈值 |
| 字段校验过严误拒 | 低 | 部分数据被拦截 | 校验规则可配置,放宽阈值 |
| 弹性系数模型偏差 | 中 | M8 输入失真 | M8 用真实历史数据交叉验证 |

### 8.3 验收标准

1. **数据完整率**: A股 capital 完整率 ≥ 95%,港股 capital ≥ 80%
2. **批量场景稳定性**: batch-analyze 12只完整成功率 100%(允许 1-2 只 fallback 到历史)
3. **看板可用**: `/api/data-health/dashboard` 返回 200,字段齐全
4. **弹性系数**: 输出 4 维度 ε 值,与 v5 引擎评分逻辑一致
5. **零代码原则**: 不影响 `python app.py` 一键启动

---

## 九、附录

### 9.1 诊断SQL(全量统计,可复现)

```sql
-- 完整率分布
SELECT s.market, ds.dimension, ds.status, COUNT(*) AS cnt
FROM data_status ds LEFT JOIN stocks s ON s.id = ds.stock_id
WHERE ds.fetched_at >= date('2026-07-20','-30 days')
GROUP BY s.market, ds.dimension, ds.status;

-- 标的级缺失模式
SELECT s.market, s.symbol, ds.dimension,
       COUNT(*) AS total_cnt,
       SUM(CASE WHEN ds.status='success' THEN 1 ELSE 0 END) AS succ_cnt
FROM data_status ds LEFT JOIN stocks s ON s.id = ds.stock_id
WHERE ds.fetched_at >= date('2026-07-20','-30 days')
GROUP BY s.market, s.symbol, ds.dimension;

-- 实际数据覆盖断层
SELECT s.symbol, cf.trade_date, COUNT(*) AS cnt
FROM raw_capital_flow cf LEFT JOIN stocks s ON s.id = cf.stock_id
WHERE cf.trade_date >= date('2026-07-20','-7 days')
GROUP BY s.symbol, cf.trade_date;
```

### 9.2 引用证据

- **C1 根因备忘录**: 07-20 batch-analyze 触发东方财富批量限流,11/12 失败
- **DIAG-5 错误聚类**: 34次 "东方财富接口全部失败" 主导
- **B1 补修确认**: 三处估算源已 if False 硬禁用,本方案不恢复任何估算 fallback
- **零代码原则**: 来源记忆 "零代码用户最高优先级原则"

### 9.3 关联文档

- 《AI编程需求规格说明书v1.1》§2.5/§2.6 数据采集要求
- 《P3-A 引擎对齐修复验收合格证》(2026-07-20 签发)
- 《C1 资金面数据断层根因分析备忘录》(2026-07-20 提交)
- M8 回测框架技术方案(待 07-21 12:00 提交,以本方案为数据基础设施输入)

---

**END**
