推荐模型：deepseek-v4-pro（千问 Plan）

# B18 开发提示词：评分引擎校准

## 任务
修改 modules/scoring_engine.py 中各子项评分函数，拉开评分区分度。

## 背景
当前全部 27 只股票评分集中在 34.4~63.3，无一只达到 70（推荐买入阈值）。
四维均分：技术面 59.4 / 基本面 47.8 / 资金面 42.9 / 消息面 65.0。
资金面严重偏低，基本面偏低，消息面偏高。

## 具体修改（严格按任务书执行，不得超出范围）

### T1 资金面（P0）
1. score_main_capital: 中性基准 60→65; 小幅净流入(0~1000万) 60→68; 温和净流入(1000~5000万) 75→80; 大幅净流入(>5000万) 90→92
2. score_north_capital: 缺失默认值 50→55; 小幅买入(0~500万) 58→62
3. score_margin_capital: 缺失默认值 50→55; 小幅增加(0~500万) 58→62

### T2 基本面（P0）
1. score_valuation: PE≤15 90→92; PE≤25 75→78; PB≤1 85→88
2. score_profitability: ROE≥20 95→96; ROE≥15 82→85; ROE≥10 65→70; 毛利率≥50 90→92; 毛利率≥30 72→76
3. score_growth: 营收≥30% 95→96; 营收≥20% 80→83; 营收≥10% 65→70; 净利≥50% 95→96; 净利≥30% 82→85; 净利≥15% 68→72
4. score_cashflow: OCF≥1.2 90→92; OCF≥0.8 80→83
5. score_fin_health: 资产负债率≤30% 90→92; 流动比率≥2.0 85→88

### T3 技术面（P0）
1. score_ma: 金叉基准 70→75; 价格在均线之上加分 +2→+3（每条）
2. score_trend: MACD 多头基准 65→70; 价格在60日均线上方加分上限 15→18
3. score_obos: RSI 健康区域(45~65) 80→82; KDJ 健康区域(40~60) 75→78
4. score_vol_price: 成交量≥2000万 75→78; 500~2000万 65→68
5. score_volatility: 布林带位置 40~70% 78→80

### T4 消息面（P1）
1. score_sentiment: 映射曲线 (sentiment+1)*50 → (sentiment+1)*48; 显著正面(>0.3) 上限 100→95
2. score_holder: 增持 80→82

### T5 验证脚本（P1）
创建 scripts/calibrate_verify.py（或 _b18_verify.py），功能：
1. 对全部 27 只自选股执行最新评分（调用 analyze_from_db）
2. 输出评分分布直方图（0-29/30-49/50-69/70-84/85+）
3. 输出四维均分 + 总分均分
4. 对比校准前后差异（从 ratings_history 读取旧数据）

## 红线
- 不引入新 pip 依赖
- data_collector.py L1645/L1684/L1717 三处 if False 不变
- config_weights.json rating_mapping 85/70/50/30 不变
- 不修改 backtest_engine.py

## 交付物
1. 修改后的 modules/scoring_engine.py
2. 新增 scripts/calibrate_verify.py（或 _b18_verify.py）
3. 开发自验报告 reports/dev_selftest_B18.md（含校准前后对比数据）

## 验收标准
1. 最新一期 70+ 股票占比 ≥ 20%
2. 评分区间 max-min ≥ 40 分
3. 四维均分均在 50~65 区间
4. 评级覆盖 ≥ 3 档
5. python -m modules.scoring_engine 四场景测试通过
