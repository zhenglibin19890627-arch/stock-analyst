"""
v5.0 MockDataProvider — 模拟数据提供器

基于标准数据契约(StockData)生成三类测试数据：
1. normal   — 正常完整数据（所有字段有值）
2. boundary — 边界值数据（PE=0/负数、极端RSI、零成交量等）
3. partial  — 字段缺失数据（随机30%非必填字段置 None）

使用方式：
    from modules.mock_data_provider import MockDataProvider
    provider = MockDataProvider()
    data = provider.generate('normal', code='600519.SH', market='A')
    # boundary exhaustive 模式：
    batch = provider.generate('boundary', boundary_mode='exhaustive', code='600519.SH', market='A')
"""

from __future__ import annotations

import random
from datetime import datetime
from typing import Literal

from modules.data_contract import StockData


class MockDataProvider:
    """模拟数据提供器，覆盖正常/边界/缺失三种场景"""

    # A股代码池
    A_CODES = ['600519.SH', '000001.SZ', '601888.SH', '002352.SZ', '300750.SZ']
    # 港股代码池
    HK_CODES = ['00700.HK', '09988.HK', '00388.HK', '02318.HK', '00939.HK']

    # 非必填字段全列表（用于随机置 None）
    OPTIONAL_FIELDS = [
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
        'pe_ttm',
        'pb',
        'roe',
        'gross_margin',
        'revenue_yoy',
        'net_profit_yoy',
        'ocf_to_profit',
        'debt_to_asset',
        'current_ratio',
        'news_sentiment',
        'main_net_inflow',
        'north_net_buy',
        'margin_balance_chg',
        'holder_increase',
    ]

    # exhaustive 模式下每个字段的极端值列表（Q06）
    BOUNDARY_EXTREMES: dict[str, list] = {
        'ma5': [0.01],
        'ma10': [0.01],
        'ma20': [0.01],
        'ma60': [0.01],
        'macd_dif': [-999.0, 999.0, 0.0],
        'macd_dea': [-999.0, 999.0, 0.0],
        'kdj_k': [0.0, 100.0],
        'rsi_14': [0.0, 100.0],
        'volume': [0, 1, 999999999],
        'volume_ratio': [0.0, 99.0],
        'boll_upper': [0.01],
        'boll_lower': [0.01],
        'pe_ttm': [0.0, -5.32, 9999.99],
        'pb': [0.0, -1.5, 100.0],
        'roe': [-15.5, 0.0, 80.0],
        'gross_margin': [-10.0, 0.0, 95.0],
        'revenue_yoy': [-50.0, 0.0, 200.0],
        'net_profit_yoy': [-80.0, 0.0, 500.0],
        'ocf_to_profit': [-0.5, 0.0, 3.0],
        'debt_to_asset': [0.0, 100.0],
        'current_ratio': [0.1, 10.0],
        'news_sentiment': [-1.0, 1.0],
        'main_net_inflow': [-99999.0, 99999.0],
        'north_net_buy': [-50000.0, 50000.0],
        'margin_balance_chg': [-30000.0, 30000.0],
        'holder_increase': [True, False],
    }

    def generate(
        self,
        scenario: Literal['normal', 'boundary', 'partial'] = 'normal',
        *,
        code: str | None = None,
        market: str | None = None,
        trade_date: str | None = None,
        close: float | None = None,
        seed: int | None = None,
        boundary_mode: Literal['random', 'exhaustive'] = 'random',
        missing_rate: float = 0.3,
    ) -> StockData | list[StockData]:
        """
        生成模拟数据。

        :param scenario: 场景类型 normal/boundary/partial
        :param code: 指定股票代码（不指定则随机）
        :param market: 指定市场（不指定则由 code 推断）
        :param trade_date: 指定交易日期（默认今天）
        :param close: 指定收盘价（不指定则随机）
        :param seed: 随机种子（用于可复现测试）
        :param boundary_mode: boundary场景模式 "random"(单条随机极端值) / "exhaustive"(逐字段逐极端值生成多条)
        :param missing_rate: partial场景缺失比例 0.0-1.0，默认0.3(30%)
        :return: StockData 对象；boundary+exhaustive 模式返回 list[StockData]
        """
        if seed is not None:
            random.seed(seed)

        # 基础字段
        if not code:
            if market == 'HK' or (market is None and random.random() > 0.7):
                code = random.choice(self.HK_CODES)
                market = 'HK'
            else:
                code = random.choice(self.A_CODES)
                market = 'A'

        if not market:
            market = 'HK' if code.endswith('.HK') else 'A'

        if not trade_date:
            trade_date = datetime.now().strftime('%Y%m%d')

        if not close:
            close = round(random.uniform(5.0, 200.0), 2)

        # 按场景生成可选字段
        if scenario == 'normal':
            kwargs = self._gen_normal(close)
        elif scenario == 'boundary':
            if boundary_mode == 'exhaustive':
                # exhaustive 模式：返回 List[StockData]，每条仅一个字段取极端值
                return self._gen_boundary(
                    close,
                    mode='exhaustive',
                    code=code,
                    market=market,
                    trade_date=trade_date,
                )
            kwargs = self._gen_boundary(close, mode='random')
        elif scenario == 'partial':
            kwargs = self._gen_partial(close, missing_rate=missing_rate)
        else:
            raise ValueError(f'未知场景: {scenario}，支持 normal/boundary/partial')

        return StockData(
            code=code,
            market=market,
            trade_date=trade_date,
            close=close,
            **kwargs,
        )

    def generate_batch(
        self,
        scenario: Literal['normal', 'boundary', 'partial'] = 'normal',
        count: int = 10,
        seed: int | None = None,
    ) -> list[StockData]:
        """批量生成模拟数据"""
        if seed is not None:
            random.seed(seed)
        return [self.generate(scenario) for _ in range(count)]

    # ================================================================
    # 场景1：正常完整数据
    # ================================================================

    def _gen_normal(self, close: float) -> dict:
        """所有可选字段都有合理值"""
        return {
            'ma5': round(close * random.uniform(0.97, 1.03), 2),
            'ma10': round(close * random.uniform(0.95, 1.05), 2),
            'ma20': round(close * random.uniform(0.93, 1.07), 2),
            'ma60': round(close * random.uniform(0.88, 1.12), 2),
            'macd_dif': round(random.uniform(-0.5, 0.5), 4),
            'macd_dea': round(random.uniform(-0.3, 0.3), 4),
            'kdj_k': round(random.uniform(20, 80), 2),
            'rsi_14': round(random.uniform(30, 70), 2),
            'volume': random.randint(100000, 50000000),
            'volume_ratio': round(random.uniform(0.5, 2.5), 2),
            'boll_upper': round(close * random.uniform(1.05, 1.12), 2),
            'boll_lower': round(close * random.uniform(0.88, 0.95), 2),
            'pe_ttm': round(random.uniform(8, 50), 2),
            'pb': round(random.uniform(1.0, 6.0), 2),
            'roe': round(random.uniform(5, 25), 2),
            'gross_margin': round(random.uniform(15, 60), 2),
            'revenue_yoy': round(random.uniform(-10, 40), 2),
            'net_profit_yoy': round(random.uniform(-20, 50), 2),
            'ocf_to_profit': round(random.uniform(0.5, 1.5), 2),
            'debt_to_asset': round(random.uniform(20, 70), 2),
            'current_ratio': round(random.uniform(1.0, 3.5), 2),
            'news_sentiment': round(random.uniform(-0.3, 0.5), 2),
            'main_net_inflow': round(random.uniform(-5000, 10000), 2),
            'north_net_buy': round(random.uniform(-3000, 8000), 2),
            'margin_balance_chg': round(random.uniform(-2000, 5000), 2),
            'holder_increase': random.choice([True, False]),
        }

    # ================================================================
    # 场景2：边界值数据
    # ================================================================

    def _gen_boundary(
        self,
        close: float,
        mode: Literal['random', 'exhaustive'] = 'random',
        *,
        code: str = '',
        market: str = 'A',
        trade_date: str = '',
    ) -> dict | list[StockData]:
        """边界值数据

        :param mode: "random" 每字段随机选一个极端值组成单条(dict);
                     "exhaustive" 逐字段逐极端值生成多条(list[StockData])，每条仅一个字段取极端值
        :param code/market/trade_date: exhaustive模式构造 StockData 所需的基础字段
        :return: random模式返回dict; exhaustive模式返回list[StockData]
        """
        if mode == 'random':
            return {
                'ma5': round(close * 1.0, 2),
                'ma10': round(close * 1.0, 2),
                'ma20': round(close * 1.0, 2),
                'ma60': round(close * 1.0, 2),
                'macd_dif': 0.0,
                'macd_dea': 0.0,
                'kdj_k': random.choice([0.0, 100.0, 50.0]),
                'rsi_14': random.choice([0.0, 100.0, 50.0]),
                'volume': random.choice([0, 1, 999999999]),
                'volume_ratio': 0.0,
                'boll_upper': round(close * 1.0, 2),  # 布林带收口到极值
                'boll_lower': round(close * 1.0, 2),
                # PE 边界值：0、负数、极大值
                'pe_ttm': random.choice([0.0, -5.32, 9999.99]),
                'pb': random.choice([0.0, -1.5, 100.0]),
                # ROE 边界值：负数、极大值
                'roe': random.choice([-15.5, 0.0, 80.0]),
                'gross_margin': random.choice([-10.0, 0.0, 95.0]),
                'revenue_yoy': random.choice([-50.0, 0.0, 200.0]),
                'net_profit_yoy': random.choice([-80.0, 0.0, 500.0]),
                'ocf_to_profit': random.choice([-0.5, 0.0, 3.0]),
                'debt_to_asset': random.choice([0.0, 50.0, 100.0]),
                'current_ratio': random.choice([0.1, 1.0, 10.0]),
                # 情绪极端值
                'news_sentiment': random.choice([-1.0, 1.0, 0.0]),
                'main_net_inflow': random.choice([-99999.0, 0.0, 99999.0]),
                'north_net_buy': random.choice([-50000.0, 0.0, 50000.0]),
                'margin_balance_chg': random.choice([-30000.0, 0.0, 30000.0]),
                'holder_increase': random.choice([True, False]),
            }

        # exhaustive 模式：逐字段逐极端值生成多条 StockData
        base_normal = self._gen_normal(close)
        results: list[StockData] = []
        for field_name, extreme_values in self.BOUNDARY_EXTREMES.items():
            for extreme_val in extreme_values:
                field_data = dict(base_normal)
                field_data[field_name] = extreme_val
                results.append(
                    StockData(
                        code=code,
                        market=market,
                        trade_date=trade_date,
                        close=close,
                        **field_data,
                    )
                )
        return results

    # ================================================================
    # 场景3：字段缺失数据（随机30%置 None）
    # ================================================================

    def _gen_partial(self, close: float, missing_rate: float = 0.3) -> dict:
        """随机将指定比例的非必填字段置为 None

        :param missing_rate: 缺失比例 0.0-1.0，默认0.3(30%)
        """
        missing_rate = max(0.0, min(1.0, missing_rate))  # clamp 到 [0, 1]
        full = self._gen_normal(close)
        fields = list(self.OPTIONAL_FIELDS)
        random.shuffle(fields)
        n_drop = int(len(fields) * missing_rate)
        for f in fields[:n_drop]:
            full[f] = None
        return full
