"""
v5.0 标准数据契约 (Standard Data Contract)

所有业务逻辑仅依赖此契约，严禁直接耦合具体数据源（如 akshare/tushare）的原始字段。
非必填字段缺失时，按"缺失处理"列定义的降级策略执行，禁止抛出异常中断流程。

汇率处理：v5.0 使用固定汇率 0.92（港币→人民币），由适配器层负责转换，
契约内不保留原始港币价格。未来版本将切换为实时汇率源。

使用方式：
    from modules.data_contract import StockData
    data = StockData(code="600519.SH", market="A", trade_date="20260716", close=1680.0)
    # 分析引擎仅接受 StockData 对象，不耦合任何第三方库类型
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class DataQuality(BaseModel):
    """记录各维度数据完整度(0-1)，用于报告风险提示"""

    model_config = ConfigDict(extra='allow')

    technical: float = Field(default=0.0, ge=0.0, le=1.0, description='技术面数据完整度')
    fundamental: float = Field(default=0.0, ge=0.0, le=1.0, description='基本面数据完整度')
    news: float = Field(default=0.0, ge=0.0, le=1.0, description='消息面数据完整度')
    capital: float = Field(default=0.0, ge=0.0, le=1.0, description='资金面数据完整度')


class StockData(BaseModel):
    """
    v5.0 标准数据契约 —— 唯一数据基准

    必填字段缺失时终止该股票分析；
    非必填字段缺失时按降级策略执行，不抛出异常。

    汇率处理：v5.0 使用固定汇率 0.92（港币→人民币），由适配器层负责转换，
    契约内不保留原始港币价格。

    降级策略与维度内子权重调整：
      每个维度由若干子项组成，各子项有预设权重。当某子项依赖的数据字段缺失时，
      执行"维度内子权重调整"——将该子项权重降低或归零，剩余子项权重按比例重新归一化。

      示例（技术面维度，3个子项）：
        正常状态：均线(40%) + 趋势(35%) + 超买超卖(25%) = 100%
        ma5/ma10/ma20 全缺失 → 均线子项权重调整为0%
        重新归一化：趋势→58.3%(35/60) + 超买超卖→41.7%(25/60) = 100%

      三类调整动作：
        A) 权重归零型 — 子项依赖字段全缺失，权重置0，剩余子项按比例补足
        B) 权重降低型 — 子项依赖字段部分缺失，权重降低指定比例
        C) 默认值填充型 — 用中性默认值填充缺失字段，权重保持不变
    """

    model_config = ConfigDict(extra='allow')

    # ================================================================
    # 一、基础与技术面 (Market & Technical) — 必填4项 + 技术指标可选
    # ================================================================

    code: str = Field(..., description='标准化代码 (A股:600519.SH, 港股:00700.HK)')
    market: Literal['A', 'HK'] = Field(..., description='市场标识')
    trade_date: str = Field(..., description='交易日期 YYYYMMDD 格式')
    close: float = Field(..., gt=0, description='收盘价(统一人民币计价)')

    # 技术指标（可选）
    ma5: float | None = Field(default=None, description='5日均线价格')
    ma10: float | None = Field(default=None, description='10日均线价格')
    ma20: float | None = Field(default=None, description='20日均线价格')
    ma60: float | None = Field(default=None, description='60日均线价格')
    macd_dif: float | None = Field(default=None, description='MACD DIF值')
    macd_dea: float | None = Field(default=None, description='MACD DEA值')
    kdj_k: float | None = Field(default=None, description='KDJ K值')
    rsi_14: float | None = Field(default=None, description='14日RSI')
    volume: int | None = Field(default=None, ge=0, description='成交量(股)')
    volume_ratio: float | None = Field(default=None, description='量比')
    boll_upper: float | None = Field(default=None, description='布林带上轨')
    boll_lower: float | None = Field(default=None, description='布林带下轨')

    # ================================================================
    # 二、基本面 (Fundamental) — 全部可选
    # ================================================================

    pe_ttm: float | None = Field(default=None, description='滚动市盈率')
    pb: float | None = Field(default=None, description='市净率')
    roe: float | None = Field(default=None, description='净资产收益率(%)')
    gross_margin: float | None = Field(default=None, description='销售毛利率(%)')
    revenue_yoy: float | None = Field(default=None, description='营收同比增长率(%)')
    net_profit_yoy: float | None = Field(default=None, description='净利润同比增长率(%)')
    ocf_to_profit: float | None = Field(default=None, description='经营现金流/净利润')
    debt_to_asset: float | None = Field(default=None, description='资产负债率(%)')
    current_ratio: float | None = Field(default=None, description='流动比率')

    # ================================================================
    # 三、消息面与资金面 (News & Capital Flow) — 全部可选
    # ================================================================

    news_sentiment: float | None = Field(
        default=None, ge=-1.0, le=1.0, description='情绪指数(-1.0~1.0)'
    )
    main_net_inflow: float | None = Field(default=None, description='主力净流入(万元)')
    north_net_buy: float | None = Field(default=None, description='北向/港股通净买入(万元)')
    margin_balance_chg: float | None = Field(default=None, description='融资余额变化(万元)')
    holder_increase: bool | None = Field(default=None, description='大股东/高管是否增持')
    # B22 新增：消息面扩展字段（从 news_sentiment 表映射）
    news_count: int | None = Field(default=None, description='新闻总数')
    news_positive_ratio: float | None = Field(default=None, description='正面新闻占比(0~1)')
    news_negative_count: int | None = Field(default=None, description='负面新闻数量')
    # 020R-45 新增：股东人数与机构持仓（资金面-筹码结构）
    holder_count_change_pct: float | None = Field(
        default=None, description='股东户数增减比例(%)，正=户数增加（筹码分散）'
    )
    institution_hold_ratio: float | None = Field(
        default=None, description='机构持仓比例(%)（东财六类机构持股汇总/总股本）'
    )

    # ================================================================
    # 四、扩展与元数据
    # ================================================================

    extra: dict[str, Any] = Field(default_factory=dict, description='预留扩展字段(ST标记/股息率等)')
    data_quality: DataQuality | None = Field(default=None, description='各维度数据完整度')
    update_time: str = Field(
        default_factory=lambda: datetime.now(UTC).isoformat(),
        description='数据更新时间戳(ISO8601)',
    )

    # ================================================================
    # 校验器
    # ================================================================

    @field_validator('trade_date')
    @classmethod
    def validate_trade_date(cls, v: str) -> str:
        """校验交易日期格式 YYYYMMDD"""
        if len(v) != 8 or not v.isdigit():
            raise ValueError(f'trade_date 必须为 YYYYMMDD 格式，得到: {v}')
        return v

    # ================================================================
    # 降级策略表 —— 供分析引擎查询
    # ================================================================

    # 每个非必填字段缺失时的降级动作描述（统一使用"维度内子权重调整"表述）
    DEGRADATION_RULES: ClassVar[dict[str, str]] = {
        # 技术面
        'ma5': '技术面-均线子项：维度内子权重调整为0（权重归零型）',
        'ma10': '技术面-均线子项：维度内子权重调整为0（权重归零型）',
        'ma20': '技术面-均线子项：维度内子权重调整为0（权重归零型）',
        'ma60': '技术面-趋势子项：维度内子权重调整降权30%（权重降低型）',
        'macd_dif': '技术面-趋势子项：维度内子权重调整降权30%（权重降低型）',
        'macd_dea': '技术面-趋势子项：维度内子权重调整降权30%（权重降低型）',
        'kdj_k': '技术面-超买超卖子项：维度内子权重调整降权（权重降低型）',
        'rsi_14': '技术面-超买超卖子项：维度内子权重调整降权（权重降低型）',
        'volume': '技术面-量价分析子项：维度内子权重调整为0（权重归零型）',
        'volume_ratio': '技术面-量比子项：维度内子权重保持，使用默认值1.0填充（默认值填充型）',
        'boll_upper': '技术面-波动率子项：维度内子权重调整降权（权重降低型）',
        'boll_lower': '技术面-波动率子项：维度内子权重调整降权（权重降低型）',
        # 基本面
        'pe_ttm': '基本面-估值子项：维度内子权重调整降权（权重降低型）',
        'pb': '基本面-估值子项：维度内子权重调整降权（权重降低型）',
        'roe': '基本面-盈利能力子项：维度内子权重调整降权（权重降低型）',
        'gross_margin': '基本面-盈利能力子项：维度内子权重调整降权（权重降低型）',
        'revenue_yoy': '基本面-成长性子项：维度内子权重调整降权（权重降低型）',
        'net_profit_yoy': '基本面-成长性子项：维度内子权重调整降权（权重降低型）',
        'ocf_to_profit': '基本面-现金流质量子项：维度内子权重调整为0（权重归零型）',
        'debt_to_asset': '基本面-财务健康度子项：维度内子权重调整降权（权重降低型）',
        'current_ratio': '基本面-财务健康度子项：维度内子权重调整降权（权重降低型）',
        # 消息面
        'news_sentiment': '消息面-情绪子项：维度内子权重保持，使用中性值填充（默认值填充型）',
        'holder_increase': '消息面-股东行为子项：维度内子权重调整为0（权重归零型）',
        # B22 新增：消息面扩展字段降级规则
        'news_count': '消息面-新闻量子项：维度内子权重保持，使用中性值填充（默认值填充型）',
        'news_positive_ratio': '消息面-新闻情绪子项：维度内子权重保持，使用中性值填充（默认值填充型）',
        'news_negative_count': '消息面-新闻情绪子项：维度内子权重保持，使用中性值填充（默认值填充型）',
        # 资金面
        'main_net_inflow': '资金面-主力资金子项：维度内子权重保持，使用中性值填充（默认值填充型）',
        'north_net_buy': '资金面-互联互通子项：维度内子权重调整降权（权重降低型）',
        'margin_balance_chg': '资金面-杠杆资金子项：维度内子权重调整降权（权重降低型）',
        # 020R-45 新增：股东人数/机构持仓（A股专属，缺失时子权重归零）
        'holder_count_change_pct': '资金面-股东人数子项：维度内子权重调整为0（权重归零型）',
        'institution_hold_ratio': '资金面-机构持仓子项：维度内子权重调整为0（权重归零型）',
    }

    def get_degradation(self, field_name: str) -> str:
        """查询某字段的缺失降级策略。返回 None 表示该字段无降级规则（或字段存在）。"""
        return self.DEGRADATION_RULES.get(field_name)

    def has_field(self, field_name: str) -> bool:
        """检查某可选字段是否有值（非 None）"""
        return getattr(self, field_name, None) is not None

    def missing_fields(self, dimension: str | None = None) -> list[str]:
        """
        列出缺失的非必填字段。
        dimension: 'technical' / 'fundamental' / 'news' / 'capital' / 'news_capital'(兼容) / None(全部)
        """
        TECHNICAL = {
            'ma5',
            'ma10',
            'ma20',
            'ma60',
            'macd_dif',
            'macd_dea',
            'kdj_k',
            'rsi_14',
            'volume',
            'volume_ratio',
            'boll_upper',
            'boll_lower',
        }
        FUNDAMENTAL = {
            'pe_ttm',
            'pb',
            'roe',
            'gross_margin',
            'revenue_yoy',
            'net_profit_yoy',
            'ocf_to_profit',
            'debt_to_asset',
            'current_ratio',
        }
        # B22：消息面扩展为 5 个字段（新增 news_count/news_positive_ratio/news_negative_count）
        NEWS = {
            'news_sentiment',
            'holder_increase',
            'news_count',
            'news_positive_ratio',
            'news_negative_count',
        }
        CAPITAL = {
            'main_net_inflow',
            'margin_balance_chg',
            'holder_count_change_pct',
            'institution_hold_ratio',
        }

        if dimension == 'technical':
            scope = TECHNICAL
        elif dimension == 'fundamental':
            scope = FUNDAMENTAL
        elif dimension == 'news':
            scope = NEWS
        elif dimension == 'capital':
            scope = CAPITAL
        elif dimension == 'news_capital':
            # 向后兼容：合并消息面与资金面
            scope = NEWS | CAPITAL
        else:
            scope = TECHNICAL | FUNDAMENTAL | NEWS | CAPITAL

        return [f for f in scope if getattr(self, f, None) is None]

    def compute_data_quality(self) -> DataQuality:
        """根据各维度字段完整度自动计算 data_quality

        四维独立计算（Q02拆分后）：
        - technical: 12个可选字段
        - fundamental: 9个可选字段
        - news: 5个字段 (news_sentiment, holder_increase, news_count, news_positive_ratio, news_negative_count)
        - capital: 4个字段 (main_net_inflow, margin_balance_chg,
          holder_count_change_pct, institution_hold_ratio)  # 020R-45 从3扩展至5；020R-47 移除 north 后为4
        """
        tech_total = 12
        tech_present = 12 - len(self.missing_fields('technical'))

        fund_total = 9
        fund_present = 9 - len(self.missing_fields('fundamental'))

        news_total = 5  # B22: 从2扩展至5
        news_present = 5 - len(self.missing_fields('news'))

        capital_total = 4  # 020R-47: 互联互通子项移除后从5减至4
        capital_present = 4 - len(self.missing_fields('capital'))

        self.data_quality = DataQuality(
            technical=round(tech_present / tech_total, 2),
            fundamental=round(fund_present / fund_total, 2),
            news=round(news_present / news_total, 2),
            capital=round(capital_present / capital_total, 2),
        )
        return self.data_quality

    def to_analysis_dict(self) -> dict[str, Any]:
        """
        转换为分析引擎可直接使用的扁平字典。
        同时附带降级提示列表，供报告风险提示使用。

        注意：volume_ratio 缺失时填充默认值1.0，此为分析适配默认值，非数据修复。
        原始数据中 volume_ratio 仍为 None，可在 degradations 中追溯。
        """
        self.compute_data_quality()
        missing = self.missing_fields()
        degradations = {}
        for f in missing:
            rule = self.get_degradation(f)
            if rule:
                degradations[f] = rule

        return {
            # 基础
            'code': self.code,
            'market': self.market,
            'trade_date': self.trade_date,
            'close': self.close,
            # 技术面
            'ma5': self.ma5,
            'ma10': self.ma10,
            'ma20': self.ma20,
            'ma60': self.ma60,
            'macd_dif': self.macd_dif,
            'macd_dea': self.macd_dea,
            'kdj_k': self.kdj_k,
            'rsi_14': self.rsi_14,
            'volume': self.volume,
            'volume_ratio': self.volume_ratio if self.volume_ratio is not None else 1.0,
            'boll_upper': self.boll_upper,
            'boll_lower': self.boll_lower,
            # 基本面
            'pe_ttm': self.pe_ttm,
            'pb': self.pb,
            'roe': self.roe,
            'gross_margin': self.gross_margin,
            'revenue_yoy': self.revenue_yoy,
            'net_profit_yoy': self.net_profit_yoy,
            'ocf_to_profit': self.ocf_to_profit,
            'debt_to_asset': self.debt_to_asset,
            'current_ratio': self.current_ratio,
            # 消息面与资金面
            'news_sentiment': self.news_sentiment,
            'main_net_inflow': self.main_net_inflow,
            'north_net_buy': self.north_net_buy,
            'margin_balance_chg': self.margin_balance_chg,
            'holder_increase': self.holder_increase,
            # B22 新增：消息面扩展字段输出
            'news_count': self.news_count,
            'news_positive_ratio': self.news_positive_ratio,
            'news_negative_count': self.news_negative_count,
            # 020R-45 新增：股东人数/机构持仓
            'holder_count_change_pct': self.holder_count_change_pct,
            'institution_hold_ratio': self.institution_hold_ratio,
            # 元数据
            'extra': self.extra,
            'data_quality': self.data_quality.model_dump() if self.data_quality else None,
            'update_time': self.update_time,
            'degradations': degradations,
            'missing_fields': missing,
        }


class AnalysisResult(BaseModel):
    """分析引擎输出结果契约"""

    model_config = ConfigDict(extra='allow')

    code: str
    score_date: str
    total_score: float = Field(ge=0, le=100)
    rating: str
    rating_label: str = ''

    # 四维得分
    technical_score: float | None = Field(default=None, ge=0, le=100)
    fundamental_score: float | None = Field(default=None, ge=0, le=100)
    sentiment_score: float | None = Field(default=None, ge=0, le=100)
    capital_score: float | None = Field(default=None, ge=0, le=100)

    # 权重
    technical_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    fundamental_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    sentiment_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    capital_weight: float = Field(default=0.0, ge=0.0, le=1.0)

    # 元数据
    operation_suggestion: str = ''
    data_warnings: list[str] = Field(default_factory=list)
    data_quality: dict[str, float] | None = None
    degradations: dict[str, str] = Field(default_factory=dict)
