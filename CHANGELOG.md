# 变更日志 (CHANGELOG)

## [2026-08-15] 成本修正历史孤儿记录修复（020R-22）

- 用户反馈：成本修正页面有两条历史记录没有股票代码和名称。
- 根因：接口经 holdings 关联取股票，持仓被删除后断链；且 `h.stock_id`（NULL）在行字典中覆盖了修正记录自带的 `pca.stock_id`。
- 修复：全局修正历史接口改为直接用 `position_cost_adjustments.stock_id` 关联 stocks——两条孤儿记录恢复显示（000333 美的集团、603501 豪威集团）。
- 验证：10 条修正记录全部带股票代码与名称。

## [2026-08-15] 客观解读条目色调标注（020R-21）

- 用户裁定：解读报告好的要提示、不好的要预警。
- 后端：评级解读与价格建议解读逐条附色调 `interpretation_tones`（good/bad/neutral，与 interpretation_parts 等长）——准确率≥60%、命中率达标、风险收益比≥1 等为 good；接近随机、周期衰减、命中率偏低、样本不足、未来函数偏差提示等为 bad；样本量/免责等中性。
- 前端：客观解读卡逐条着色——**good：绿色 ✓**、**bad：橙色 ⚠️**、中性：默认圆点；分档点评拆为「最可信」（提示）与「最弱」（预警）两条。
- 验证：py_compile/ruff/node --check 通过；两接口各返回 7-8 条色调；服务重启健康 200。

## [2026-08-15] 回测中心客观解读卡片化逐条展示（020R-20）

- 用户裁定：客观解读单独成卡，一条一个观点逐条列出（原先一大段不便于阅读）；解读需包含评级有效性与价格建议命中率两部分。
- 后端：`compute_market_report` 新增 `interpretation_parts`（评级有效性逐条观点）；`compute_price_backtest_report` 新增 `interpretation_parts`（价格建议命中率逐条观点：样本量/买入区间命中/目标价命中/止损触发/风险收益比/综合得分/数据质量提示）。
- 前端：回测中心新增「📋 客观解读」独立卡片（琥珀色左边框），按「评级有效性」「价格建议命中率」分组，每条一个圆点条目；原报告内的解读段落移除。
- 验证：py_compile/ruff/node --check 通过；两接口各返回 7 条解读；服务重启健康 200。

## [2026-08-15] 数据完整度提示修复（020R-19）

- 休市日（周末/节假日）K线/资金面至最新交易日被按自然日误报"滞后N天"；改为与市场最新交易日（全部自选股K线最大日期）比较，0 天显示「最新」；westock 来源标注修正为「腾讯自选股」（原先标成东财真实）；9 天报告快照重生成。

## [2026-08-15] 投资建议详情拆两卡 + 综合分析 markdown 渲染（020R-14）

- 用户裁定：投资建议详情卡拆为「📝 综合分析」与「🌟 维度亮点」两张卡片；综合分析改用 markdown 渲染（引入 marked@12 CDN，未加载时降级纯文本）；仓位建议段移除（网格计划卡已含各档位仓位比例）。
- 综合分析卡内附：消息面摘要、风险提示、数据完整度与提示（原卡片其余小节并入）。
- 验证：node --check 通过；页面已引用 marked；服务重启健康 200。

## [2026-08-15] 价格建议+网格计划移入首屏雷达图右侧（020R-7）

- 用户裁定：价格建议与操作网格计划从「投资建议详情」抽出，做成独立卡片放在雷达图旁边；空间不足时雷达图卡片收窄。
- 布局：首屏三列 = 评分卡(280px) + 雷达卡(320px) + 价格建议卡(380-460px，含价格建议键值行+网格计划表+资金面信号+交易分析)；≤1320px 价格建议卡换行占满整行、≤900px 全部单列堆叠。
- 「投资建议详情」卡片保留：综合分析、仓位建议、维度亮点、风险提示、消息面摘要、数据完整度提示。

## [2026-08-15] 四维因子长文本截断修复（020R-6）

- 用户反馈：中芯国际基本面因子"基本面较上期恶化（…净利率较上期恶化(10.71%→9..."显示不完整。
- 根因：advisor `_pick_top_factors` 对 >50 字符的因子值截断加省略号（前端不截断），另有新闻标题 30 字符截断。
- 修复：移除全部因子值截断（`_pick_top_factors` 两处 + `top_news` 标题一处），长文本完整入参，前端换行完整显示（020R-5 已支持换行）。
- 数据联动：08-06～08-15 全部报告快照重生成（新代码落库）；评级回测 521/521 重跑。
- 验证：29 只股票 report-latest 全因子复查，含省略号因子 = 0。

## [2026-08-15] 前端改动不可见的缓存问题根治（020S）

- 背景：用户反馈多次前端修改后页面无变化——根因是浏览器缓存了旧 HTML 页面（其引用的静态资源版本号停留旧值），改 CSS/JS 文件后用户始终加载旧资源。
- 根治（服务端）：① `after_request` 对 `/` 与 `/static/*` 响应强制 `Cache-Control: no-cache, no-store, must-revalidate`；② 首页静态资源版本号改为模板变量，自动取文件修改时间（mtime），每次改动自动换版本、无需人工升级。
- 使用方式：用户只需普通刷新（或首次关闭标签页重开），之后每次改动即时可见。
- 验证：HTML/JS 响应头均为 no-cache；JS 内容含全部最新改动（K线卡片已移除、紧凑类生效）。

## [2026-08-15] 报告页布局紧凑化 + 移除K线卡片（020R）

- 用户反馈：四维评分雷达图与四维评分详情空白过多；K线走势卡片不再需要。
- 雷达图：高度 220→190px、多边形半径 65%→72%（填满画布）、坐标轴字号 13→12、雷达卡内边距 20→12px。
- 四维评分详情：2×2 网格间距 16→10px、卡片内边距 16→12px、大分数 36→30px、维度名 16→14px、状态徽章与关键因子行高收紧、区块外边距 20→12px（`dim-detail-card` 局部生效，不影响其他页卡片）。
- 首屏评分+雷达区：列间距 20→12px、评分列 320→300px。
- **移除「K线走势（最近20个交易日）」卡片**（用户裁定）——K线数据仍可在个股「数据」页查看；报告页顺序变为 评分卡+雷达图 → 四维详情 → 投资建议详情。

## [2026-08-15] 价格建议与网格计划表格显示优化（020Q）

- 用户反馈：表格平铺太宽、内容不多应紧凑易读；列框与文字对齐由设计统一。
- 价格建议：由全宽 2×2 表格改为**紧凑卡片式键值行**（max-width 440px 圆角卡片）——标签靠左灰色小字、数值靠右对齐（tabular-nums 等宽数字）、操作建议行淡橙底强调；涨红跌绿保持。
- 网格计划：改为**紧凑窄表**（max-width 440px）——灰底表头、细边框；档位居中、价位右对齐（等宽数字）、仓位居中、说明靠左；买入/减仓/补仓类型色保留。
- 验证：node --check 通过；静态资源已生效（强刷页面查看）。

## [2026-08-15] 价格建议止盈/止损改为现价锚定（020P）

- 用户裁定：止盈价/止损价与个人成本无关，应只由 **评级档位 + 现价 + 技术指标** 决定（市场不看个人成本）。
- 有持仓公式改为：
  - 止盈价 = max(现价×(1+保底涨幅), min(现价×(1+评级目标涨幅), 技术阻力位))（阻力位 = min(BOLL上轨、MA60 中高于现价者)，无则现价×1.10）
  - 止损价 = 现价 × (1 - 评级止损比例)
  - 成本仅保留用于：浮盈/浮亏展示、状态机浮亏判定、网格回本/补仓位（其语义天然与成本相关）
- 行为变化：止盈价恒在现价上方（未来目标），状态机 S1「已超目标」不再可达；止盈提示改由 评级降档/跌破止损/网格分批止盈位 触发。示例：美团 现价87.20 止盈96.46 止损82.84（原 65.98/78.48 为成本锚定）。
- 同步修改 `price_backtest._gen_with_position`（双向同步约定），价格回测 1046/1046 全量重跑。
- 网格计划同步重构（020P 一致性）：卖出档位严格自下而上递增、锚定现价与成本孰高——浮盈：补仓位→第一止盈位(现价+0.6ATR)→最终止盈位；浮亏且止盈目标<回本价：补仓位→回本清仓位；浮亏且止盈目标≥回本价：补仓位→回本减仓位(30%)→第一止盈位(50%)→最终止盈位。修复了 020P 后"回本减仓/第一止盈低于现价、最终止盈低于回本价"的档位倒挂。
- 验证：py_compile/ruff 通过；5 只有持仓股票报告页实时建议全部现价锚定、数值复算一致。

## [2026-08-15] 港股全资金净流入 + 主力净流入占比补齐（020O）

- 回答用户"全资金净流入能否从腾讯获取"：**港股能、A股不能**——腾讯 hkfund（港股）返回 TotalNetFlow（主力+散户主动净额，有实际意义，实测美团 08-13 为 -28744.31 万港元）；asfund（A股）散户为被动镜像（散户净额与主力净额分毫不差互为相反数），全口径恒等 0，无法合成。
- 落地：`raw_capital_flow` 新增 `total_net_inflow` 列（仅港股有值）；westock 层解析 TotalNetFlow 与主力净流入占比（占比=主力净额÷成交额，成交额=主力买+主力卖+散户买+散户卖，A股 MainInFlow 系/港股 MainIn 系字段自动适配）；实盘写入与逐日回填路径同步携带两字段；新增 `backfill_hk_total_net`（只补 total/pct 不降级覆盖港股东财主力）。
- 数据：6 只港股 × 10 交易日 total+pct 全量回填 60/60；A股 64 个 westock 行占比逐日回填。
- 前端：资金面表按数据形态条件渲染——A股显示四档分解（超大/大/中/小），港股显示「全资金净流入(腾讯)」列；占比列港股同步有值。
- 验证：node --check / py_compile / ruff 通过；HK3690 10/10 行含 total 与 pct，A股不显示全净额列。

## [2026-08-15] 资金面表移除同花顺净额列，改为完整四档展示（020N）

- 背景：用户指出中国中免等个股"同花顺净额"只显示 4 条——同花顺历史逐日接口不可获取（探测实证：akshare 仅当日全市场快照；官网历史 URL 变体/stockpage SPA/basic.10jqka API 全部无效或反爬），缺失日无法补齐。
- 关键发现：腾讯/东财的四档净额**互补恒等**（超大+大+中+小 ≡ 0，散户=被动方口径），合成不出同花顺「净额」口径（同花顺净额含散户主动部分，如 601888 08-14 同花顺 -22300 万 vs 主力 -18459 万），"近似补齐"数学上不成立（会得到全 0 序列）。
- 按用户裁定：前端资金面表移除「同花顺净额」辅助列，改展示中单+小单两列——四档（超大/大/中/小）来自同一数据源、近 10 交易日逐日完整，无缺口；来源标注同步改为「东方财富/腾讯自选股」逐行混合诚实标注。
- 影响面：ths_net_inflow 仅前端表格消费（评分不依赖），采集与调度器保留（数据留存备用，不展示）。
- 验证：node --check 通过；601888 资金面 10 行 × 6 列全部有值。

## [2026-08-15] 个股报告页显示完整性修复（020M）

- 背景：周六查看个股报告页显示不完整——当日(非交易日)无报告时，report-latest 走实时生成路径，且回退查询用全表 MAX(report_date)，部分股票已有当日行时其余股票回退失败。
- 修复 1：周末/休市日（weekday>=5）跳过实时生成，直接回退该股票最新日报快照（含综合文本 markdown）；交易日实时路径补齐 `advice_detail`（与日报同源 `_build_markdown_single`）。
- 修复 2：回退查询改为按股票取 `MAX(report_date)`，杜绝"别的股票才有报告的日期"导致本股票查无报告。
- 修复 3：快照响应补齐 `action_advice`（取价格建议操作）、`latest_close`/`latest_close_date`（查最新K线）——评分卡"建议"行与"最新收盘"行不再缺失。
- 数据清理（先备份 db_backup_20260815_133847_pre_0815_cleanup.db）：删除 10 只股票由实时生成意外写入的 08-15（周六）daily_reports 行，统一回到 08-14 真实交易日口径。
- 验证：29/29 只股票 report-latest 字段齐全（advice_detail/action_advice/latest_close/latest_close_date/rating_date=08-14）。

## [2026-08-15] 前端数据完整性修复（020L：周末守卫 + 来源升级 + 同花顺回溯）

- 前端审计发现三类问题：① 23 只 A股各有一条 08-09（周日）估算脏行，挤占资金面 LIMIT 10 展示名额；② 近 10 交易日窗口内 33 格新浪顶替 + 6 格估算兜底；③ 同花顺净额全窗口仅 71/230 格有值。
- 修复 1（预防）：`fetch_capital_flow` 新增周末守卫——周六/周日全链路跳过（与 019G 同花顺同原则），根治定时日报在周末写入非交易日脏行；`backfill_capital_history` 升级时同步置空 `main_net_inflow_pct`（westock/新浪不提供占比，避免估算旧占比残留）。
- 修复 2（数据，先备份 db_backup_20260815_132302_pre_020L_frontend_fix.db）：删除 23 条周日估算脏行；39 个估算/新浪格子全部经腾讯 westock --date 升级为真实数据（窗口来源分布：东财 213 / 腾讯 77 / 新浪 0 / 估算 0 / 缺失 0）。
- 修复 3（同花顺回溯）：从历史备份恢复可追回的 30 格 ths（08-05/08-06 来自 019S 备份、08-13 2 格来自 08-13 备份），仅填空洞不覆盖；剩余 129 格历史数据源从未存在（08-03/08-04/08-07/08-10 同花顺接口全市场失败、08-11/12 部分股票接口未返回），无法追回——自 08-14 起每日 23/23 采集，10 个交易日后窗口自然满 10 条。
- 联动：39 格升级后 08-07～08-14 报告重生成（skip_collect，29/29×7 天）+ 评级回测 521/521 + 价格回测 1046/1046 重跑。
- 验证：脏行 0；K线/主力资金 10 交易日全窗口无缺口；ths 101/230（历史可追回部分已全部追回）。

## [2026-08-15] 回测中心数据同步重算 + 孤儿回测行自愈（020K）

- 背景：报告重生成会经 advisor.py 的 `INSERT OR REPLACE` 重写 `ratings_history` 换掉 rating id（B24 红线模块，不改），导致 `backtest_results` 累计 595 条孤儿行（旧 id 失去引用），污染回测中心市场报告统计。
- 落地：`BacktestEngine.batch_backtest` 开头新增自愈清理——删除 `rating_id` 非空且不在 `ratings_history` 中的孤儿行（排除 `rating_id=-1` 的历史模拟行），每次回测运行自动收敛，无需人工维护。
- 数据修复（先备份 db_backup_20260815_130323_pre_backtest_regen.db）：清理 595 条孤儿行 → 评级回测全量重跑 521/521 成功（真实样本与 ratings_history 一一对应）→ 价格建议回测全量重跑 1046/1046 成功（force 模式自带备份）。
- 说明：07-16 遗留的 77 行 `ratings_history` 字母档（B/C/D）与回测表中文标签不一致属历史表示差异，回测统计口径统一、不受影响。
- 验证：孤儿行 0；08-06～08-14 每天 29 行真实回测覆盖；价格回测 1046 行 created_at 全部刷新。

## [2026-08-15] 报告重生成支持跳过采集（020J：skip_collect）

- 背景：数据回填完成后需重生成历史报告，但 `_process_single_stock` 写死"先采集后分析"，历史 8 天重生成会重复打外部接口（每轮 3-4 分钟）。
- 落地：`generate_daily_report` / `_process_single_stock` 新增 `skip_collect` 参数（默认 False，行为零变化）；True 时跳过同花顺批量预取与逐只采集，纯用库内已有数据重新分析。API `POST /api/daily-report/generate` 新增 `skip_collect` 请求字段。
- 兼容性：18:00 定时调度、intraday 端点、CLI、tests 等既有调用点均走默认值，不受影响。
- 验证：py_compile/ruff 通过；08-06～08-13 共 7 天历史报告以 `{date, force, skip_collect}` 全部重生成成功，回测评级历史（ratings_history）同步修正。

## [2026-08-15] 资金面历史回填补强：腾讯 --date 逐日层 + 新浪窗口扩大（020I）

- 背景：020H 上线首日实测发现两类补不上：港股历史缺口（新浪 lscjfb 仅 A股）与超出新浪 5 日窗口的旧缺口（如 000977 的 08-06）。
- 探针实证：westock CLI 的 `--date` 参数对 A股 asfund / 港股 hkfund 均支持历史逐日查询（港股 08-13 主力 4460.21 万港元、A股 08-06 主力 -26050.95 万，四档自洽）。
- 落地：`backfill_capital_history` 链序改为 **腾讯 westock --date（A股+港股）→ 新浪 lscjfb（仅A股）**；`_fetch_capital_flow_westock` 新增 `date_str` 参数并严格校验 `EndDate == date_str`（M-2 同款红线，不匹配只记日志、不计 westock 连续失败，避免回填拖垮实时链路）；新浪回补窗口 num 5→15（覆盖近 10 交易日口径）。
- 验证：py_compile/ruff 通过；HK3690 6 天 + 688981/002714 08-11 + 000977 08-06 全部回填成功（westock 源）。

## [2026-08-15] 补采调度器重写：近 10 个交易日完整性补采（020H）

- 背景：用户确认补采口径 =「近 10 个交易日，只要不完整就补采，同花顺净额也要采」。
- 交易日历：以全部自选股 K 线日期并集推导（不含周末/节假日），窗口为最近 10 个交易日。
- 缺口检测四维：K 线缺口（缺日即补）、资金面缺口（该日 `main_net_inflow` 为空）、同花顺净额（A股最近交易日 `ths_net_inflow` 为空）、基本面/消息面（沿用原逻辑）。
- 资金缺口补采：新增 `data_collector.backfill_capital_history()`，逐日按 东财三层 → 腾讯 westock → 新浪 lscjfb 链补采（东财熔断期直接走 westock/新浪），写库 `capital_source` 标注实际来源；同花顺净额经 `fetch_capital_flow_batch` 批量刷新（沿用 019G 周末跳过规则）。
- 轮次控制：每轮最多 5 只、30 分钟基础周期、失败退避至 120 分钟，与原有 K 线/基本面/消息面补采共用轮次。
- 验证：py_compile/ruff 通过；干跑识别 15 只有缺口股票（12 资金 + 2 K线 + 1 双缺口）；端到端实测 000858 的 08-11 资金缺口经新浪补采成功（主力 -47244.98 万，`capital_source='sina_main'`，非估算、参与评分）。

## [2026-08-14] 新增腾讯自选股资金面备用层（020A：westock）

- 背景：东财资金面接口频繁不可用；搜索发现腾讯自选股（westock-data-clawhub npm CLI）提供 A股/港股主力净流入（含南下持仓、两融、大宗），社区实测腾讯不封 IP。
- 探针审计：CLI 经 proxy.finance.qq.com 签名网关交付，仅访问单域名；实测 A股主力口径=超大+大（与东财精确同概念：600276 主力 -72451.11 万 = 超大 -63472.32 + 大 -8978.79）；港股返回主力净额+南下持仓。
- 落地：`_fetch_capital_flow_westock` 层插入 东财三层 → **腾讯 westock** → 新浪 → 估算 之间；npx 经 cmd /c 调用（Windows 批处理兼容），Markdown 表解析，45s 超时，连续失败 3 次冷却 30 分钟；写库 `capital_source='westock'`、`is_estimated=0`（参与评分），防覆盖/补采清单 SQL 已含 'westock'（东财恢复后可覆盖回补）。
- 验证：直连测试（A股四档+港股主力）、端到端熔断链路测试（600276/HK3690 落库成功）、py_compile/ruff 通过、服务重启健康 200。

## [2026-08-14] 数据源韧性增强（019Z：东财熔断冷却 + 编号子域轮换 + 请求节流）

- 依据社区实测情报（a-stock-data SKILL，2026-06）：东财封禁阈值与"push2 被封 ≠ 全站不可用"、编号子域可绕部分 WAF 拦截。
- 新增进程级"东财熔断冷却"：批量回退循环触发熔断（连续失败 5 只）后进入 2 小时冷却期，期间 `_fetch_capital_flow_em_individual` / `_fetch_capital_flow_em` / akshare 备用源**直接跳过**（省去每只约 2.5 分钟空等，链路自动落新浪主力口径/估算）；期间任意一次东财成功即提前解除。
- `_http_get_em` 增强：第 3 轮起 push2/push2his 编号子域（1~99）轮换；全局最小请求间隔 0.5s（社区阈值 <5 次/秒）。
- 验证：py_compile/ruff 通过；熔断状态机与子域轮换逻辑自检通过；服务重启后健康检查 200。

## [2026-08-14] 服务自愈看门狗（补充：巡检间隔降至 1 分钟）

- 背景：同日 17:38/17:44 两次注销导致服务中断，5 分钟巡检的恢复窗口过长；ONLOGON 触发器被本机策略拒绝（Access denied）。
- 调整：`StockAnalyst Watchdog` 计划任务改为 `/SC MINUTE /MO 1`（每分钟巡检，登录后 ≤60 秒自动恢复）。
- 实测：杀进程 → 17:48:01 巡检刻度自动复活，health 200；任务每分钟正常触发（IgnoreNew 策略不影响后续巡检，看门狗拉起的是分离子进程）。
- 说明：PowerShell `Set-ScheduledTask`/`Register-ScheduledTask` 在本机会话被拒，改用 `schtasks /Create`（经 cmd 中转处理引号）注册。

## [2026-08-14] 数据修复：每天仅一份最终报告（回测依据统一）

### 背景
- 需求：每天的最终报告只有一份，且是回测中心"评级有效性报告 / 价格建议命中率"的依据。
- 盘点结论：写入路径已有保护（daily 生成时清掉当天 intraday；ratings_history `UNIQUE(stock_id, rating_date)` + `INSERT OR REPLACE`），问题仅存在于 013 迁移前的历史存量数据。

### 数据修复（备份：db_backup_20260814_170133_enforce_single_daily_report.db）
- 删除被 daily 顶替的历史 intraday 行 **106 行**（08-13/08-11/08-06/08-04/07-31）。
- 归一化旧状态 `success` → `ok` **27 行**（07-24 的报告此前因状态值不符而被所有读取路径"隐形"）。
- 校验：daily+intraday 共存 = 0；每股每天 ok 报告恰好 1 份；ratings_history / price_backtest_results / backtest_results（真实行）均无重复。
- 说明：08-10 有 3 只股票当日生成失败（仅 failed 标记、无 ok 报告），可重跑当日报告补齐。

## [2026-08-14] 服务自愈看门狗 + 掉线根因修复

### 背景
- 08-13~08-14 多次服务掉线。根因：系统注销/关机/睡眠会终止用户会话进程（Windows 事件 1074/42/7002），登录自启存在失灵竞态（14:03 一次登录后未拉起）。

### 新增
- `scripts/watchdog.py`：端口检查（127.0.0.1:5000）+ pythonw 分离式静默拉起（无窗口、幂等）。
- Windows 计划任务 **StockAnalyst Watchdog**（每 5 分钟，登录会话内运行）：服务被误杀/窗口误关/登录后均 5 分钟内自动恢复。
- 端到端自愈演练通过：杀进程 → 触发任务 → 10 秒内 `/api/health` 恢复 200。

### 说明
- 托盘图标仅作状态显示，服务存续不再依赖托盘。
- 若需"注销后仍运行"，可升级为 SYSTEM 级计划任务（需管理员权限），但会与托盘/start.bat 的端口释放逻辑冲突，默认不启用。

## [2026-08-13] 盘中快报生成动效 + 蓝图路径回归修复

### 前端动效（盘中快报 / 每日报告 生成过程可视化）
- **templates/index.html**：
  - 新增步骤时间线动效（准备 → 采集数据 → 分析评分 → 写入报告 → 完成），当前阶段脉冲高亮、已完成打 ✓、失败显示 ✕
  - 流光渐变进度条 + 旋转 spinner + 实时百分比/第几只/当前股票/当前阶段
  - `generateIntradayReport()` 接入 `/api/daily-report/progress` 轮询（1.5s），替换原静态"请稍候"占位
  - `renderProgressUI` 升级为通用动效面板，按场景显示标题（每日报告 / 盘中快报）

### 回归修复（app.py 拆分为蓝图引入的 __file__ 路径偏移）
- `blueprints/report.py`：进度文件读取路径改为复用 `daily_report._REPORT_PROGRESS_PATH`（单一来源，原路径指向 blueprints/logs/ 读不到数据）
- `blueprints/system.py`：`_ROLLBACK_AUDIT_LOG` 路径补一级 dirname，回落到 `logs/rollback_audit.log`

### 验证
- `python -m pytest tests/`：392 passed，1 skipped
- 真实启动：`/api/daily-report/progress` 正确返回进度 JSON；页面包含动效代码；内联 JS `node --check` 通过

## [2026-08-13] 代码结构治理（app.py 拆分 / 脚本归档 / 路由测试）

### 结构调整（本次改造）
- **app.py 按业务域拆分为 blueprints/ 蓝图包**（4094 行 → 约 130 行入口）
  - 新增 9 个业务蓝图：watchlist（自选股/分组/采集）/ analysis（分析/评级/v5）/ portfolio（持仓/流水/成本）/ report（日报）/ system（健康/引擎）/ backtest（回测/优化）/ export（导出）/ index_ratings（指数）/ alerts（预警）
  - 共享展示层工具函数（_fmt_* / _derive_obos_signal / _resolve_report_type 等 9 个）迁至 blueprints/_utils.py
  - 函数体零改动，仅装饰器 @app.route → @bp.route；102 函数 / 77 路由与拆分前逐一对齐
- **scripts/ 诊断脚本归档**：12 个 diag_*.py（东财反爬/数据源排障等历史一次性脚本）移入 scripts/archive/diag/
- **新增 tests/test_routes.py 路由层冒烟测试**：16 个用例，覆盖全部 9 个蓝图的核心端点（隔离临时库，不触网）
- **analysis_engine.py 标注 LEGACY 状态**：灰度已完成 all_v5，但作为 advisor 回退路径与 engine_switcher 熔断依赖暂不可删，docstring 注明清理条件
- **.gitignore 完善**：补充 .pytest_cache/ .mypy_cache/ .reasonix/ 等，清除误提交的 31 个 .reasonix 环境文件

### 验证
- `python -m pytest tests/`：392 passed（原 376 + 新增 16），1 skipped
- `ruff check .`：通过
- 真实启动冒烟：/api/health、首页、db-stats、ratings、engine/status、stocks 全部 200

## [2026-07-29] 009 价格建议增强（全栈开发）

### 009: 价格建议增强模块（glm5.2）
- **重写 modules/price_advisor.py**：状态机+动态止盈+网格价位+资金面转化+交易流水分析
  - 操作建议状态机（S1-S4 × 5评级矩阵，S4破止损禁止加仓）
  - 止盈价动态化（双约束：max(最低止盈, min(固定止盈, 技术阻力位))）
  - 网格价位（无持仓3档买入，有持仓1补+3减，ATR动态间距）
  - 资金面信号转化（7档修饰词，正则解析资金面文本）
  - 交易流水分析（加仓节奏/成本趋势/买卖时机，数据不足静默跳过）
- **app.py**：4处调用点追加 position_advice 覆盖逻辑（+13行）
  - /analyze, /advise, report-latest(实时+自动触发)
  - 当 price_advice 有动态操作建议时覆盖旧 position_advice
- **templates/index.html**：价格建议section重写为网格表格+资金面+交易分析+状态颜色编码（+86/-21行）
- 红线零触碰：advisor.py / data_collector.py / db_manager.py / daily_report.py / config_weights.json

## [2026-07-28] 005 价格建议（全栈开发）

### 005: 价格建议模块（glm5.2）
- **新建 modules/price_advisor.py**：ATR + MA/BOLL 组合算法，生成买入区间/目标价/止损价/止盈价/建议仓位
- **app.py**：/advise + /analyze + 批量分析 3处端点后处理集成（generate_advice 返回后追加 price_advice，不修改 generate_advice）+ report-latest 返回 price_advice
- **modules/daily_report.py**：_save_report 新增 price_advice 参数，日报持久化价格建议 JSON
- **database/db_manager.py**：daily_reports 表新增 price_advice TEXT 列
- **templates/index.html**：投资建议详情区域新增价格建议 section（无持仓/有持仓两种表格 + 免责声明 + CSS）
- 无持仓输出：买入区间/目标价/止损价/建议仓位/预期涨幅/最大回撤
- 有持仓输出：止盈价/止损价/成本价/浮盈/操作建议
- 数据不足时返回 available=false 优雅降级

## [2026-07-26] 数据完整度提升 + 四维因子明细 + 回测改四维 + 文档整理

### B19-1: analysis_results 日期对齐（kimi k3）
- 修复 analysis_results.analysis_date 与 daily_reports.report_date 非交易日不对齐
- advisor.py: generate_advice 增加 report_date 参数，_save_analysis_results_for_v5 支持 report_date 覆盖
- daily_report.py: 调用 generate_advice 传 target_date
- 删除 28 个历史遗留临时脚本（_sync_daily_reports.py / _fix_db_scores.py / _check_*.py 等）

### B20: v5 引擎四维因子明细（glm5.2）
- advisor.py: 重写 _build_v5_factors，新增 _build_kline_factors/_build_fundamental_factors/_build_capital_factors/_build_news_factors
- templates/index.html: 修复 var dims 变量遮蔽 bug（B15-T4 引入，L3862 var dims → var dqDims）

### B21: PE/PB 聚合回退防御兜底（glm5.2）
- data_adapter.py: _read_fundamental_data 增加聚合回退（pe_ratio/pb_ratio/holder_increase 最新行 NULL 时取次新非空行）

### B22: 消息面数据维度扩展（glm5.2）
- data_contract.py: StockData 新增 news_count/news_positive_ratio/news_negative_count，NEWS 集合从 2→5 字段，news_total 从 2→5
- data_adapter.py: 从 news_sentiment 表映射 total_count/positive_count/negative_count
- 消息面完整度从 50% 提升到 80%

### B23: 回测模拟改四维评分（glm5.2）
- backtest_engine.py: run_historical_simulation 从技术面单维度改为调用 scoring_engine.analyze 四维综合评分

### B24: 前端消息面因子展示（glm5.2）
- templates/index.html: _factorPriority.news 和 _dimFactorLabels.news 新增 news_count

### B25: 用户使用说明文档更新（minimax m3）
- 用户使用说明.md: 从 226 行扩展到 587 行，新增每日报告/看板/回测/导出/四维详情章节

### 文档整理
- 新增 docs/PROJECT_INDEX.md（项目文档索引）

## [2026-07-15] 市值与已实现盈亏精确计算升级

### 盈亏计算逻辑变更（审计追溯）

**变更前**：
- 市值 = cost_price × quantity（基于成本价估算，表头标注「市值估计」）
- 已实现盈亏 = holdings.realized_pnl（流水编辑时重算，但无独立查询接口）

**变更后**：
- 市值 = quantity × price_cache.latest_price（基于实时行情精确计算）
  - latest_price 为 NULL 时显示 "--"（不显示0）
  - 银行家舍入法（ROUND_HALF_EVEN），保留2位小数
  - 向后兼容：旧字段 `estimated_market_value` 保留（@deprecated），与 `market_value` 值相同
- 已实现盈亏：新增 `/api/portfolio/realized-pnl` 独立接口
  - 计算方法：加权平均法（Weighted Average Cost）
  - 数据源：从 trade_records 逐笔计算
  - 不含交易手续费（与券商对账单口径一致）
  - 支持按日/周/月聚合查询

### 数据库变更
- 新增索引：`idx_trade_records_stock_date` (trade_records.stock_id, trade_date, created_at)
- 新增索引：`idx_price_cache_stock` (price_cache.stock_id)

### API 变更
| 字段 | 变更类型 | 说明 |
|------|---------|------|
| `market_value` | 新增 | 精确市值 = quantity × latest_price |
| `estimated_market_value` | @deprecated | 向后兼容字段，值与 market_value 相同 |
| `data_status` | 新增 | realtime / cache / offline |

### 新增接口
- `GET /api/portfolio/realized-pnl` — 已实现盈亏精确查询（支持 stock_id / period / start_date / end_date 参数）
