"""
data_collector.py 聚焦单元测试

覆盖范围（纯函数，隔离网络与数据库）：
1. 港股代码归一化 _normalize_hk_symbol
2. 腾讯接口前缀 _get_tencent_prefix（A股分沪深 / 港股5位数字）
3. 东方财富 secid 与 market code 生成 _get_em_secid / _get_em_market_code
4. 中文金额解析 _parse_cn_amount（亿/万/纯数字/空值容错）
5. 019N 安全数值转换 _safe_num/_safe_float_wan/_safe_float_pct（NaN/'-'/±Inf → None）
6. 时间工具 now_cn（北京时间格式校验）
7. UA 池 _random_ua（取值域校验）
8. StockData 数据契约映射校验（复用 MockDataProvider 隔离机制）

测试隔离原则：
- 仅测试 data_collector 中不依赖网络/DB 的纯函数
- StockData 契约映射通过 MockDataProvider 生成纯内存数据验证
- conftest.py 负责把项目根目录加入 sys.path
"""

import re
from datetime import datetime, timezone
from datetime import timedelta as _td

import numpy as np
import pytest

from database import db_manager
from modules import data_collector as dc
from modules.data_contract import StockData
from modules.mock_data_provider import MockDataProvider


class _TradingDayDateTime(datetime):
    """021A：把 dc.datetime.now() 固定为交易日（周五 2026-08-14 15:00），
    规避 fetch_capital_flow_batch 的 019G 周末守卫对测试的影响——
    守卫在真实周末（周六/周日）会让批量接口直接返回 skipped，
    导致"回退 EM 逐只 + progress_cb"路径在周末无法被测试覆盖。
    """
    FIXED = datetime(2026, 8, 14, 15, 0, tzinfo=timezone(_td(hours=8)))

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED


class _WeekendDateTime(datetime):
    """021C：把 dc.datetime.now() 固定为周六（2026-08-15 12:00，weekday()==5），
    用于测试 021C 新增的盘口周末守卫。"""
    FIXED = datetime(2026, 8, 15, 12, 0, tzinfo=timezone(_td(hours=8)))

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED

# ============================================================
# 一、港股代码归一化 _normalize_hk_symbol
# ============================================================


class TestNormalizeHkSymbol:
    """港股代码统一为5位数字格式"""

    def test_hk_prefix(self):
        assert dc._normalize_hk_symbol('HK3690') == '03690'

    def test_already_5_digits(self):
        assert dc._normalize_hk_symbol('00700') == '00700'

    def test_pure_digits(self):
        assert dc._normalize_hk_symbol('3690') == '03690'

    def test_dot_hk_suffix(self):
        assert dc._normalize_hk_symbol('03690.HK') == '03690'

    def test_lowercase_hk(self):
        assert dc._normalize_hk_symbol('hk09988') == '09988'

    def test_with_spaces(self):
        assert dc._normalize_hk_symbol('  HK3690  ') == '03690'

    def test_short_code_pads_to_five(self):
        assert dc._normalize_hk_symbol('1') == '00001'


# ============================================================
# 二、腾讯接口前缀 _get_tencent_prefix
# ============================================================


class TestGetTencentPrefix:
    """根据市场和代码返回腾讯接口前缀和归一化代码"""

    @pytest.mark.parametrize(
        'symbol,prefix,code',
        [
            ('600519', 'sh', '600519'),
            ('601888', 'sh', '601888'),
            ('000001', 'sz', '000001'),
            ('300750', 'sz', '300750'),
            ('002352', 'sz', '002352'),
        ],
    )
    def test_a_stock(self, symbol, prefix, code):
        assert dc._get_tencent_prefix(symbol, 'a_stock') == (prefix, code)

    @pytest.mark.parametrize(
        'symbol,expected_code',
        [
            ('HK3690', '03690'),
            ('00700', '00700'),
            ('09988', '09988'),
        ],
    )
    def test_hk_stock(self, symbol, expected_code):
        prefix, code = dc._get_tencent_prefix(symbol, 'hk_stock')
        assert prefix == 'hk'
        assert code == expected_code

    def test_unknown_market_fallback(self):
        prefix, code = dc._get_tencent_prefix('123456', 'us_stock')
        assert prefix == ''
        assert code == '123456'


# ============================================================
# 三、东方财富 secid 与 market code
# ============================================================


class TestEmSecid:
    """东方财富 secid 格式：A股 1/0.代码，港股 116.5位代码"""

    def test_a_stock_sh(self):
        assert dc._get_em_secid('600519', 'a_stock') == '1.600519'

    def test_a_stock_sz(self):
        assert dc._get_em_secid('000001', 'a_stock') == '0.000001'

    def test_hk_stock(self):
        assert dc._get_em_secid('HK3690', 'hk_stock') == '116.03690'

    def test_unknown_market_defaults_sz(self):
        assert dc._get_em_secid('123456', 'us_stock') == '0.123456'


class TestEmMarketCode:
    """东方财富市场标识：6/9开头=sh，其余=sz"""

    @pytest.mark.parametrize(
        'symbol,expected',
        [
            ('600519', 'sh'),
            ('601888', 'sh'),
            ('900001', 'sh'),
            ('000001', 'sz'),
            ('300750', 'sz'),
            ('002352', 'sz'),
        ],
    )
    def test_market_code(self, symbol, expected):
        assert dc._get_em_market_code(symbol) == expected


# ============================================================
# 四、中文金额解析 _parse_cn_amount
# ============================================================


class TestParseCnAmount:
    """解析 '65.14亿' / '-7200.36万' / 纯数字 / 空值"""

    def test_yi(self):
        assert dc._parse_cn_amount('65.14亿') == 6514000000

    def test_wan_negative(self):
        assert dc._parse_cn_amount('-7200.36万') == -72003600.0

    def test_plain_number(self):
        assert dc._parse_cn_amount('12345.67') == 12345.67

    def test_negative_yi(self):
        assert dc._parse_cn_amount('-6.78亿') == -678000000.0

    def test_none(self):
        assert dc._parse_cn_amount(None) is None

    def test_empty_string(self):
        assert dc._parse_cn_amount('') is None

    def test_nan_float(self):
        import math

        assert dc._parse_cn_amount(math.nan) is None

    def test_invalid_string(self):
        assert dc._parse_cn_amount('abc') is None

    def test_rounding_precision(self):
        # 20.65*1e8 浮点精度问题，应通过 round 解决
        result = dc._parse_cn_amount('20.65亿')
        assert result == 2065000000.0


# ============================================================
# 四·五、019N 安全数值转换 _safe_num / _safe_float_wan / _safe_float_pct
# ============================================================


class TestSafeNum:
    """019N: None/空串/'nan'/'-'/'None'(strip后)/数值NaN/±Inf → None；其余 → float"""

    def test_none(self):
        assert dc._safe_num(None) is None

    def test_empty_string(self):
        assert dc._safe_num('') is None

    def test_whitespace_string(self):
        assert dc._safe_num('   ') is None

    def test_nan_string(self):
        assert dc._safe_num('nan') is None

    def test_nan_string_upper(self):
        assert dc._safe_num('NaN') is None

    def test_nan_string_mixed_case(self):
        assert dc._safe_num('  NaN  ') is None

    def test_dash_string(self):
        assert dc._safe_num('-') is None

    def test_none_string(self):
        assert dc._safe_num('None') is None

    def test_inf_string(self):
        assert dc._safe_num('inf') is None
        assert dc._safe_num('-inf') is None

    def test_nan_float(self):
        import math

        assert dc._safe_num(math.nan) is None

    def test_np_nan(self):
        assert dc._safe_num(np.nan) is None

    def test_np_float64_nan(self):
        assert dc._safe_num(np.float64('nan')) is None

    def test_inf_float(self):
        import math

        assert dc._safe_num(math.inf) is None
        assert dc._safe_num(-math.inf) is None

    def test_normal_string(self):
        assert dc._safe_num('123.45') == 123.45

    def test_negative_string(self):
        assert dc._safe_num('-7200.36') == -7200.36

    def test_normal_float(self):
        assert dc._safe_num(12.5) == 12.5

    def test_zero(self):
        assert dc._safe_num(0) == 0.0
        assert dc._safe_num('0') == 0.0

    def test_np_float64_normal(self):
        assert dc._safe_num(np.float64(12.5)) == 12.5

    def test_invalid_string(self):
        assert dc._safe_num('abc') is None

    def test_invalid_type(self):
        assert dc._safe_num([1, 2]) is None


class TestSafeFloatWan:
    """019N: 元→万元（÷1e4，round 2），None 透传"""

    def test_none(self):
        assert dc._safe_float_wan(None) is None

    def test_nan_string(self):
        assert dc._safe_float_wan('nan') is None

    def test_dash_string(self):
        assert dc._safe_float_wan('-') is None

    def test_normal(self):
        assert dc._safe_float_wan(123450000.0) == 12345.0

    def test_wan_conversion(self):
        assert dc._safe_float_wan('10000') == 1.0

    def test_negative(self):
        assert dc._safe_float_wan('-72003600.0') == -7200.36

    def test_zero(self):
        assert dc._safe_float_wan(0) == 0.0


class TestSafeFloatPct:
    """019N: % 字段（round 2），None 透传"""

    def test_none(self):
        assert dc._safe_float_pct(None) is None

    def test_nan_string(self):
        assert dc._safe_float_pct('nan') is None

    def test_dash_string(self):
        assert dc._safe_float_pct('-') is None

    def test_normal(self):
        assert dc._safe_float_pct(3.14159) == 3.14

    def test_negative(self):
        assert dc._safe_float_pct('-12.3456') == -12.35

    def test_zero(self):
        assert dc._safe_float_pct(0) == 0.0


# ============================================================
# 五、时间工具 now_cn
# ============================================================


class TestNowCn:
    """北京时间字符串格式校验"""

    def test_format(self):
        result = dc.now_cn()
        assert re.match(r'^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$', result)

    def test_beijing_timezone(self):
        """now_cn 返回的时间应在 UTC+8 区间内"""
        result = dc.now_cn()
        parsed = datetime.strptime(result, '%Y-%m-%d %H:%M:%S')
        # 允许 ±5 分钟漂移
        beijing_now = datetime.now(timezone(_td(hours=8))).replace(tzinfo=None)
        diff = abs((beijing_now - parsed).total_seconds())
        assert diff < 300


# ============================================================
# 六、UA 池 _random_ua
# ============================================================


class TestRandomUa:
    """从 UA 池中随机选取"""

    def test_returns_pool_member(self):
        ua = dc._random_ua()
        assert ua in dc._UA_POOL

    def test_pool_not_empty(self):
        assert len(dc._UA_POOL) >= 20

    def test_all_are_strings(self):
        assert all(isinstance(u, str) for u in dc._UA_POOL)

    def test_all_contain_mozilla(self):
        """所有 UA 应为浏览器标识"""
        assert all('Mozilla' in u for u in dc._UA_POOL)


# ============================================================
# 七、StockData 数据契约映射校验（MockDataProvider 隔离）
# ============================================================


class TestStockDataContractMapping:
    """验证数据采集产出的字段能正确映射到 StockData 契约"""

    def test_normal_data_all_optional_present(self, provider: MockDataProvider):
        """normal 场景：所有可选字段应有值"""
        data = provider.generate('normal', code='600519.SH', market='A', seed=42)
        assert isinstance(data, StockData)
        assert data.code == '600519.SH'
        assert data.market == 'A'
        assert data.close > 0
        # 核心可选字段不应缺失
        assert data.pe_ttm is not None
        assert data.roe is not None
        assert data.ma5 is not None
        assert data.rsi_14 is not None
        assert data.volume is not None
        assert data.main_net_inflow is not None

    def test_partial_data_has_missing_fields(self, provider: MockDataProvider):
        """partial 场景：30% 字段缺失但不崩溃"""
        data = provider.generate('partial', code='000001.SZ', market='A', seed=99, missing_rate=0.3)
        missing = data.missing_fields()
        assert len(missing) > 0, 'partial 场景应至少有一个字段缺失'
        # 缺失字段不应超过可选字段总数的 50%（30% 缺失率上限）
        assert len(missing) <= len(MockDataProvider.OPTIONAL_FIELDS)

    def test_boundary_exthaustive_returns_list(self, provider: MockDataProvider):
        """exhaustive 边界模式：返回多条 StockData，每条仅一个字段取极端值"""
        batch = provider.generate(
            'boundary',
            boundary_mode='exhaustive',
            code='600519.SH',
            market='A',
            seed=1,
        )
        assert isinstance(batch, list)
        assert len(batch) > 1
        assert all(isinstance(sd, StockData) for sd in batch)
        # 每条的基础字段应一致
        assert all(sd.code == '600519.SH' for sd in batch)

    def test_trade_date_validation(self):
        """StockData 契约校验：trade_date 必须为 YYYYMMDD"""
        with pytest.raises(ValueError):
            StockData(code='600519.SH', market='A', trade_date='2026-07-16', close=100.0)

    def test_close_must_be_positive(self):
        """StockData 契约校验：close 必须 > 0"""
        with pytest.raises(ValueError):
            StockData(code='600519.SH', market='A', trade_date='20260716', close=0)

    def test_market_literal(self):
        """StockData 契约校验：market 只接受 A/HK"""
        with pytest.raises(ValueError):
            StockData(code='AAPL', market='US', trade_date='20260716', close=100.0)

    def test_compute_data_quality_normal(self, provider: MockDataProvider):
        """normal 场景数据完整度应为 1.0"""
        data = provider.generate('normal', code='600519.SH', market='A', seed=7)
        dq = data.compute_data_quality()
        assert dq.technical == 1.0
        assert dq.fundamental == 1.0

    def test_data_quality_reflects_missing(self, provider: MockDataProvider):
        """partial 场景数据完整度应 < 1.0"""
        data = provider.generate('partial', code='600519.SH', market='A', seed=3, missing_rate=0.5)
        dq = data.compute_data_quality()
        assert dq.technical < 1.0 or dq.fundamental < 1.0


# ============================================================
# 业绩预告采集 collect_forecast（mock 数据源 + 隔离临时库）
# ============================================================


class TestForecastCollection:
    """业绩预告：报告期候选、数值转换、写入与防重、港股跳过"""

    def test_forecast_report_periods(self):
        """候选报告期应为 [今年0630, 今年0331, 去年1231]"""
        ps = dc._forecast_report_periods()
        assert len(ps) == 3
        assert ps[0].endswith('0630')
        assert ps[1].endswith('0331')
        assert ps[2].endswith('1231')

    def test_safe_num(self):
        """安全数值转换：None/NaN/非数值 → None，其余转 float"""
        assert dc._safe_num(None) is None
        assert dc._safe_num(float('nan')) is None
        assert dc._safe_num('abc') is None
        assert dc._safe_num('1.5') == 1.5
        assert dc._safe_num(3) == 3.0

    def test_collect_forecast_writes_and_dedup(self, tmp_path, monkeypatch):
        """写入命中预告行，重复采集不产生重复（UNIQUE + INSERT OR REPLACE）"""
        import pandas as pd

        from database import db_manager

        db_file = tmp_path / 'fc_test.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('002458', 'a_stock', '益生股份')")
        conn.commit()
        conn.close()

        df = pd.DataFrame(
            {
                '股票代码': [2458, 2458],
                '预测指标': ['归属于上市公司股东的净利润', '营业收入'],
                '业绩变动': ['预计盈利2.85亿', '预计营收10.9亿'],
                '预测数值': [2.85e8, 1.09e9],
                '业绩变动幅度': [4530.31, 40.52],
                '业绩变动原因': ['原因A', '原因B'],
                '预告类型': ['预增', '略增'],
                '上年同期值': [None, 7.76e8],
                '公告日期': ['2026-07-01', '2026-07-01'],
                '_code6': ['002458', '002458'],
            }
        )
        monkeypatch.setattr(
            dc, '_get_forecast_df_for_period', lambda p: df if p == '20260630' else None
        )

        status, msg = dc.collect_forecast(1, '002458', 'a_stock')
        assert status == 'success'
        assert '2' in msg

        # 重复采集：防重后行数不变
        dc.collect_forecast(1, '002458', 'a_stock')
        conn = db_manager.get_connection()
        n = conn.execute('SELECT COUNT(*) FROM raw_forecast').fetchone()[0]
        assert n == 2
        # 数值正确入库（元）
        v = conn.execute('SELECT forecast_value FROM raw_forecast WHERE indicator=?', ('营业收入',)).fetchone()[0]
        assert v == 1.09e9
        # data_status 写入
        st = conn.execute("SELECT status FROM data_status WHERE dimension='forecast'").fetchone()[0]
        assert st == 'success'
        conn.close()

    def test_collect_forecast_hk_skipped(self, tmp_path, monkeypatch):
        """港股跳过（无东财业绩预告），状态写 skipped"""
        from database import db_manager

        db_file = tmp_path / 'fc_hk.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()

        status, msg = dc.collect_forecast(1, 'HK3690', 'hk_stock')
        assert status == 'skipped'
        assert '港股' in msg


# ============================================================
# 业绩快报采集 collect_express（mock 数据源 + 隔离临时库）020R-50
# ============================================================


class TestExpressCollection:
    """业绩快报：写入与防重、数值入库、港股跳过"""

    def test_collect_express_writes_and_dedup(self, tmp_path, monkeypatch):
        """写入命中快报行，重复采集不产生重复（UNIQUE + INSERT OR REPLACE）"""
        import pandas as pd

        db_file = tmp_path / 'ex_test.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('601888', 'a_stock', '中国中免')"
        )
        conn.commit()
        conn.close()

        df = pd.DataFrame(
            {
                '股票代码': [601888],
                '每股收益': [1.4983],
                '营业收入-营业收入': [2.759172e10],
                '营业收入-同比增长': [-1.985834],
                '净利润-净利润': [3.106485e9],
                '净利润-同比增长': [19.491537],
                '每股净资产': [27.6335],
                '净资产收益率': [5.46],
                '公告日期': ['2026-07-15'],
                '_code6': ['601888'],
            }
        )
        monkeypatch.setattr(
            dc, '_get_express_df_for_period', lambda p: df if p == '20260630' else None
        )

        status, msg = dc.collect_express(1, '601888', 'a_stock')
        assert status == 'success'
        assert '1' in msg

        # 重复采集：防重后行数不变
        dc.collect_express(1, '601888', 'a_stock')
        conn = db_manager.get_connection()
        n = conn.execute('SELECT COUNT(*) FROM raw_express').fetchone()[0]
        assert n == 1
        # 数值正确入库（元）
        np_yoy = conn.execute('SELECT np_yoy FROM raw_express').fetchone()[0]
        assert np_yoy == pytest.approx(19.491537)
        rev = conn.execute('SELECT revenue FROM raw_express').fetchone()[0]
        assert rev == 2.759172e10
        # data_status 写入
        st = conn.execute("SELECT status FROM data_status WHERE dimension='express'").fetchone()[0]
        assert st == 'success'
        conn.close()

    def test_collect_express_hk_skipped(self, tmp_path, monkeypatch):
        """港股跳过（无东财业绩快报），状态写 skipped"""
        db_file = tmp_path / 'ex_hk.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()

        status, msg = dc.collect_express(1, 'HK3690', 'hk_stock')
        assert status == 'skipped'
        assert '港股' in msg


# ============================================================
# EM 回退逐只采集的进度回调（日报动效逐只更新）
# ============================================================


class TestEmBatchProgressCallback:
    """fetch_capital_flow_batch → _em_batch_collect 的 progress_cb 透传与逐只调用"""

    def test_progress_cb_called_per_symbol(self, monkeypatch):
        """THS 失败回退 EM 时，每只股票开始前回调一次（顺序正确）"""
        calls = []
        # 021A：固定为交易日，规避 019G 周末守卫（真实周末会直接 skipped 不走 EM 回退）
        monkeypatch.setattr(dc, 'datetime', _TradingDayDateTime)
        monkeypatch.setattr(dc, '_fetch_capital_flow_ths_batch', lambda: None)
        monkeypatch.setattr(dc, 'fetch_capital_flow', lambda sym, m: ('success', 'mock'))
        monkeypatch.setattr(dc, '_EM_INTER_DELAY_RANGE', (0.001, 0.002))
        monkeypatch.setattr(dc, '_EM_BATCH_GAP_RANGE', (0.001, 0.002))

        result = dc.fetch_capital_flow_batch(
            ['600276', '300146', '000333'],
            progress_cb=lambda i, t, s: calls.append((i, t, s)),
        )
        assert result['success_count'] == 3
        assert calls == [(0, 3, '600276'), (1, 3, '300146'), (2, 3, '000333')]

    def test_no_progress_cb_ok(self, monkeypatch):
        """不传回调时照常工作（向后兼容）"""
        # 021A：固定为交易日，规避 019G 周末守卫
        monkeypatch.setattr(dc, 'datetime', _TradingDayDateTime)
        monkeypatch.setattr(dc, '_fetch_capital_flow_ths_batch', lambda: None)
        monkeypatch.setattr(dc, 'fetch_capital_flow', lambda sym, m: ('success', 'mock'))
        monkeypatch.setattr(dc, '_EM_INTER_DELAY_RANGE', (0.001, 0.002))
        monkeypatch.setattr(dc, '_EM_BATCH_GAP_RANGE', (0.001, 0.002))

        result = dc.fetch_capital_flow_batch(['600276'])
        assert result['success_count'] == 1


# ============================================================
# 021C：五档盘口周末守卫 + 港股估值腾讯备源
# ============================================================


class TestOrderbookWeekendGuard:
    """fetch_orderbook 非交易日（周末）跳过，防周末脏行"""

    def _make_db(self, tmp_path, monkeypatch):
        db_file = tmp_path / 'ob_weekend.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600276', 'a_stock', '恒瑞医药')")
        conn.commit()
        conn.close()

    def test_weekend_skips(self, tmp_path, monkeypatch):
        self._make_db(tmp_path, monkeypatch)
        monkeypatch.setattr(dc, 'datetime', _WeekendDateTime)

        def _boom(*args, **kwargs):
            raise AssertionError('周末守卫未生效：不应调用 mootdx 实时行情')

        monkeypatch.setattr(dc, '_fetch_realtime_quote_mootdx', _boom)

        status, msg = dc.fetch_orderbook('600276', 'a_stock')
        assert status == 'skipped'
        assert '非交易日' in msg

    def test_trading_day_still_runs(self, tmp_path, monkeypatch):
        self._make_db(tmp_path, monkeypatch)
        monkeypatch.setattr(dc, 'datetime', _TradingDayDateTime)

        def _fake_quote(code):
            return {
                'price': 45.6, 'pct_change': 1.2, 'quote_time': '15:00:00',
                'bid1_price': 45.5, 'bid1_vol': 100, 'bid2_price': None, 'bid2_vol': None,
                'bid3_price': None, 'bid3_vol': None, 'bid4_price': None, 'bid4_vol': None,
                'bid5_price': None, 'bid5_vol': None, 'ask1_price': 45.7, 'ask1_vol': 200,
                'ask2_price': None, 'ask2_vol': None, 'ask3_price': None, 'ask3_vol': None,
                'ask4_price': None, 'ask4_vol': None, 'ask5_price': None, 'ask5_vol': None,
            }

        monkeypatch.setattr(dc, '_fetch_realtime_quote_mootdx', _fake_quote)

        status, msg = dc.fetch_orderbook('600276', 'a_stock')
        assert status == 'success'
        assert '已入库' in msg
        conn = db_manager.get_connection()
        row = conn.execute("SELECT latest_price FROM stock_orderbook WHERE stock_id=1").fetchone()
        conn.close()
        assert row and row['latest_price'] == 45.6


class TestValuationTencentFallback:
    """021C：港股估值 akshare(baidu 已失效) → 腾讯行情 PE/PB 兜底"""

    def test_hk_tencent_fallback(self, tmp_path, monkeypatch):
        db_file = tmp_path / 'val_hk.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('HK3690', 'hk_stock', '美团-W')")
        conn.execute("INSERT INTO raw_kline (stock_id, trade_date, close) VALUES (1, '2026-08-14', 100.0)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(dc, '_fetch_valuation_akshare', lambda s, m: None)
        monkeypatch.setattr(dc, '_fetch_valuation_baostock', lambda s, m: None)
        monkeypatch.setattr(dc, '_fetch_valuation_tencent', lambda s, m: (12.3, 1.5, 1.2e11))

        status, msg = dc.fetch_valuation('HK3690', 'hk_stock', force_full=True)
        assert status == 'success'
        assert 'tencent' in msg

        conn = db_manager.get_connection()
        row = conn.execute(
            'SELECT pe_ttm, pb_mrq, total_mv, source, trade_date FROM stock_valuation WHERE stock_id=1'
        ).fetchone()
        conn.close()
        assert row['pe_ttm'] == 12.3
        assert row['pb_mrq'] == 1.5
        assert row['total_mv'] == pytest.approx(1.2e11)
        assert row['source'] == 'tencent'
        assert row['trade_date'] == '2026-08-14'
        assert row['source'] == 'tencent'
        assert row['trade_date'] == '2026-08-14'  # 交易日戳记 = 最新K线日期，不盖周末


# ============================================================
# 021I：港股股东数据（腾讯 westock shareholder）
# ============================================================

_SHAREHOLDER_MD = """#### hk03690 美团-W (2026-08-17)

**持股股东信息**

| name | shares | pct |
| --- | --- | --- |
| Xing Wang | 563170264 | 9.12 |

**股东分布**

| institution | shares | pct |
| --- | --- | --- |
| 传统投资管理 | 1846897134 | 29.92 |

**机构持仓统计**

| reportingPeriod | holdingPct | instCount | instIncreaseCount | holdingShares | changeShares |
| --- | --- | --- | --- | --- | --- |
| 2026 Q2 | 31.61 | 404 | -19 | 1950818132 | -23088791 |
"""

_SHAREHOLDER_MD_INCREASE = """#### hk03690 美团-W (2026-08-17)

**机构持仓统计**

| reportingPeriod | holdingPct | instCount | instIncreaseCount | holdingShares | changeShares |
| --- | --- | --- | --- | --- | --- |
| 2026 Q2 | 31.61 | 404 | 3 | 1950818132 | 5000000 |
"""


class TestHkShareholder:
    """021I：westock shareholder 解析 + 港股机构持仓/股东行为三态"""

    def _reset_cache(self, monkeypatch):
        monkeypatch.setattr(dc, '_HK_SHAREHOLDER_CACHE', {})

    def test_parse_shareholder(self):
        parsed = dc._parse_westock_shareholder(_SHAREHOLDER_MD)
        assert parsed is not None and parsed.get('inst')
        assert parsed['inst']['holdingPct'] == '31.61'
        assert parsed['inst']['reportingPeriod'] == '2026 Q2'
        assert parsed['distribution'] and parsed['distribution'][0]['institution'] == '传统投资管理'

    def test_quarter_to_date(self):
        assert dc._quarter_to_date('2026 Q2') == '2026-06-30'
        assert dc._quarter_to_date('2025 Q4') == '2025-12-31'
        assert dc._quarter_to_date(' 2026 Q1 ') == '2026-03-31'
        assert dc._quarter_to_date('2026 Q3') == '2026-09-30'
        assert dc._quarter_to_date('未知') == '未知'

    def test_holder_structure_hk(self, monkeypatch):
        self._reset_cache(monkeypatch)
        captured = {}

        def fake_cli(cmd, code, date_str=''):
            captured['code'] = code
            return _SHAREHOLDER_MD

        monkeypatch.setattr(dc, '_westock_cli_query', fake_cli)
        data = dc._fetch_holder_structure_hk('HK3690')
        assert captured['code'] == 'hk03690', '4位库内代码须左补零为5位'
        assert data['inst_ratio'] == 31.61
        assert data['inst_shares'] == 1950818132.0
        assert data['stat_date'] == '2026-06-30'
        assert data['holder_count'] is None
        assert data['holder_count_change_pct'] is None
        assert data['source'] == 'westock'

    def test_holder_increase_hk_three_states(self, monkeypatch):
        # True：机构净增持（changeShares>0 且增持机构数>0）
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(dc, '_westock_cli_query', lambda cmd, code, date_str='': _SHAREHOLDER_MD_INCREASE)
        assert dc._fetch_holder_increase_hk('HK3690') is True
        # False：机构净减持（changeShares<0）
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(dc, '_westock_cli_query', lambda cmd, code, date_str='': _SHAREHOLDER_MD)
        assert dc._fetch_holder_increase_hk('HK3690') is False
        # None：接口失败
        self._reset_cache(monkeypatch)
        monkeypatch.setattr(dc, '_westock_cli_query', lambda cmd, code, date_str='': None)
        assert dc._fetch_holder_increase_hk('HK3690') is None

    def test_save_holder_structure_hk(self, tmp_path, monkeypatch):
        db_file = tmp_path / 'hs_hk.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('HK3690', 'hk_stock', '美团-W')")
        conn.commit()
        conn.close()

        dc._save_holder_structure(
            1,
            {
                'stat_date': '2026-06-30',
                'inst_ratio': 31.61,
                'inst_shares': 1950818132.0,
                'inst_report_date': '2026-06-30',
                'source': 'westock',
            },
        )
        conn = db_manager.get_connection()
        row = conn.execute('SELECT inst_ratio, source FROM holder_structure WHERE stock_id=1').fetchone()
        conn.close()
        assert row['inst_ratio'] == 31.61


# ============================================================
# 021L：资金面链路 westock 提为主源（东财三层降为兜底）
# ============================================================


class _AfterCloseDateTime(datetime):
    """021L：把 dc.datetime.now() 固定为交易日收盘后（周五 2026-08-14 16:30），
    规避周末守卫与盘中刷新旁路（020R-59 的 intraday_refresh 会绕过同日跳过），
    使"同日已有 westock 数据 → 跳过"路径可被稳定测试。"""

    FIXED = datetime(2026, 8, 14, 16, 30, tzinfo=timezone(_td(hours=8)))

    @classmethod
    def now(cls, tz=None):
        return cls.FIXED


_TODAY = '2026-08-14'  # _AfterCloseDateTime 对应交易日


def _westock_row(**overrides):
    """构造 _fetch_capital_flow_westock 的成功返回（主力=超大+大，万元口径）"""
    row = {
        'trade_date': _TODAY,
        'main_net_inflow': 5000.0,
        'main_net_inflow_pct': 3.2,
        'super_large_net': 3200.0,
        'large_net': 1800.0,
        'medium_net': -1200.0,
        'small_net': -3800.0,
        'total_net_inflow': None,
    }
    row.update(overrides)
    return row


def _em_history_rows():
    """构造 _fetch_capital_flow_em_individual 的成功返回（akshare 字典行，单位元）"""
    return [
        {
            '日期': _TODAY,
            '主力净流入-净额': 50000000.0,
            '主力净流入-净占比': 3.2,
            '超大单净流入-净额': 32000000.0,
            '大单净流入-净额': 18000000.0,
            '中单净流入-净额': -12000000.0,
            '小单净流入-净额': -38000000.0,
        }
    ]


class TestCapitalWestockPrimary:
    """021L：westock 主源链路——成功即短路东财；失败落东财兜底；westock 行计为已完成"""

    def _make_db(self, tmp_path, monkeypatch, name='cap_westock.db'):
        monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / name))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600276', 'a_stock', '恒瑞医药')")
        conn.commit()
        conn.close()

    def _patch_downstream(self, monkeypatch):
        """屏蔽 westock/EM 之后的降级层（新浪/估算），测试不应触达"""
        monkeypatch.setattr(dc, '_fetch_capital_flow_sina_main', lambda *a, **k: None)
        monkeypatch.setattr(dc, '_fetch_capital_flow_sina', lambda *a, **k: [])
        monkeypatch.setattr(dc, '_fetch_capital_flow_netease', lambda *a, **k: [])

    def test_westock_primary_short_circuits_em(self, tmp_path, monkeypatch):
        """westock 成功 → 东财三层不被调用，行写为 capital_source='westock'"""
        self._make_db(tmp_path, monkeypatch)
        monkeypatch.setattr(dc, 'datetime', _AfterCloseDateTime)
        self._patch_downstream(monkeypatch)
        monkeypatch.setattr(dc, '_westock_cooldown_active', lambda: False)
        monkeypatch.setattr(dc, '_fetch_capital_flow_westock', lambda *a, **k: _westock_row())

        em_calls = []

        def _em_spy(*a, **k):
            em_calls.append(1)
            raise AssertionError('021L：westock 成功后东财 push2his 不应被调用')

        monkeypatch.setattr(dc, '_fetch_capital_flow_em_individual', _em_spy)
        monkeypatch.setattr(dc, '_em_banned', lambda: True)  # akshare 层直接跳过

        status, msg = dc.fetch_capital_flow('600276', 'a_stock')
        assert status == 'success'
        assert '腾讯自选股' in msg

        conn = db_manager.get_connection()
        row = conn.execute(
            'SELECT main_net_inflow, super_large_net, is_estimated, capital_source '
            'FROM raw_capital_flow WHERE trade_date=?',
            (_TODAY,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row['capital_source'] == 'westock'
        assert row['is_estimated'] == 0
        assert row['main_net_inflow'] == 5000.0  # 参与评分的真实数据
        assert em_calls == []

    def test_westock_fail_falls_back_to_em(self, tmp_path, monkeypatch):
        """westock 失败 → 东财 push2his 兜底写入（capital_source=NULL）"""
        self._make_db(tmp_path, monkeypatch, name='cap_em_fb.db')
        monkeypatch.setattr(dc, 'datetime', _AfterCloseDateTime)
        self._patch_downstream(monkeypatch)
        monkeypatch.setattr(dc, '_fetch_capital_flow_westock', lambda *a, **k: None)
        monkeypatch.setattr(dc, '_fetch_capital_flow_em_individual', lambda *a, **k: _em_history_rows())
        monkeypatch.setattr(dc, '_fetch_capital_flow_em', lambda *a, **k: [])
        monkeypatch.setattr(dc, '_em_banned', lambda: True)

        status, msg = dc.fetch_capital_flow('600276', 'a_stock')
        assert status == 'success'
        assert '东方财富' in msg

        conn = db_manager.get_connection()
        row = conn.execute(
            'SELECT main_net_inflow, is_estimated, capital_source '
            'FROM raw_capital_flow WHERE trade_date=?',
            (_TODAY,),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row['capital_source'] is None  # EM 真实行来源归位
        assert row['is_estimated'] == 0
        assert row['main_net_inflow'] == 5000.0  # 5000万元

    def test_existing_westock_row_skips_all_sources(self, tmp_path, monkeypatch):
        """当日已有 westock 行 → 前置校验直接跳过，任何网络层都不触达（021L 防覆盖语义）"""
        self._make_db(tmp_path, monkeypatch, name='cap_skip.db')
        monkeypatch.setattr(dc, 'datetime', _AfterCloseDateTime)
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow, is_estimated, capital_source) "
            "VALUES (1, ?, 100.0, 0, 'westock')",
            (_TODAY,),
        )
        conn.commit()
        conn.close()

        def _boom(name):
            def _f(*a, **k):
                raise AssertionError(f'同日跳过未生效：{name} 不应被调用')

            return _f

        for fn in (
            '_fetch_capital_flow_westock',
            '_fetch_capital_flow_em_individual',
            '_fetch_capital_flow_em',
            '_fetch_capital_flow_sina_main',
        ):
            monkeypatch.setattr(dc, fn, _boom(fn))
        monkeypatch.setattr(dc, '_em_banned', lambda: True)

        status, msg = dc.fetch_capital_flow('600276', 'a_stock')
        assert status == 'success'
        assert '跳过采集' in msg

    def test_sina_row_still_retried(self, tmp_path, monkeypatch):
        """当日仅新浪顶替行（sina_main）→ 不跳过，westock 主源可覆盖升级"""
        self._make_db(tmp_path, monkeypatch, name='cap_sina.db')
        monkeypatch.setattr(dc, 'datetime', _AfterCloseDateTime)
        self._patch_downstream(monkeypatch)
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow, is_estimated, capital_source) "
            "VALUES (1, ?, 100.0, 0, 'sina_main')",
            (_TODAY,),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(dc, '_fetch_capital_flow_westock', lambda *a, **k: _westock_row())

        def _em_spy(*a, **k):
            raise AssertionError('sina 行应被 westock 主源覆盖，无需东财')

        monkeypatch.setattr(dc, '_fetch_capital_flow_em_individual', _em_spy)
        monkeypatch.setattr(dc, '_em_banned', lambda: True)

        status, msg = dc.fetch_capital_flow('600276', 'a_stock')
        assert status == 'success'
        assert '腾讯自选股' in msg
        conn = db_manager.get_connection()
        row = conn.execute(
            'SELECT capital_source, main_net_inflow FROM raw_capital_flow WHERE trade_date=?',
            (_TODAY,),
        ).fetchone()
        conn.close()
        assert row['capital_source'] == 'westock'  # 覆盖升级为同口径主源数据


class TestCapitalSupplementListWestock:
    """021L：westock 行计为"已完成"——退出资金面补采清单（东财请求密度归零的核心）"""

    def test_westock_row_excluded_from_supplement(self, tmp_path, monkeypatch):
        import pandas as pd

        monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'cap_sup.db'))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600276', 'a_stock', '恒瑞医药')")
        conn.commit()
        # 当日已有 westock 真实行
        conn.execute(
            "INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow, is_estimated, capital_source) "
            "VALUES (1, ?, 100.0, 0, 'westock')",
            (_TODAY,),
        )
        conn.commit()
        conn.close()

        monkeypatch.setattr(dc, 'datetime', _TradingDayDateTime)
        # THS 批量源正常返回（仅辅助指标）
        monkeypatch.setattr(
            dc,
            '_fetch_capital_flow_ths_batch',
            lambda: pd.DataFrame([{'股票代码': '600276', '净额': '1.2亿'}]),
        )
        em_calls = []
        monkeypatch.setattr(
            dc,
            '_em_batch_collect',
            lambda symbols, **k: em_calls.append(list(symbols)) or {'success_count': 0, 'fail_count': 0, 'source': 'mock'},
        )

        result = dc.fetch_capital_flow_batch(['600276'])
        assert result['source'] == '同花顺批量(辅助指标)'
        assert em_calls == []  # westock 已覆盖 → 不触发 EM 逐只补采

    def test_missing_row_still_supplemented(self, tmp_path, monkeypatch):
        """当日无任何真实数据 → 仍进入补采清单（保持回补能力）"""
        import pandas as pd

        monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'cap_sup2.db'))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600276', 'a_stock', '恒瑞医药')")
        conn.commit()
        conn.close()

        monkeypatch.setattr(dc, 'datetime', _TradingDayDateTime)
        monkeypatch.setattr(
            dc,
            '_fetch_capital_flow_ths_batch',
            lambda: pd.DataFrame([{'股票代码': '600276', '净额': '1.2亿'}]),
        )
        em_calls = []
        monkeypatch.setattr(
            dc,
            '_em_batch_collect',
            lambda symbols, **k: em_calls.append(list(symbols)) or {'success_count': 1, 'fail_count': 0, 'source': 'mock'},
        )

        result = dc.fetch_capital_flow_batch(['600276'])
        assert em_calls == [['600276']]  # 无数据 → 正常补采
        assert result['success_count'] == 2  # THS 辅助 1 + 补采 mock 1


# ============================================================
# 021W-2：百度历史估值采集 fetch_valuation_history
# ============================================================


class TestFetchValuationHistory:
    """021W-2：百度股市通历史估值采集（mock akshare，隔离库）。

    覆盖：A股成功写入（PE/PB 双序列合并、UPSERT 幂等）、港股跳过、
    akshare 异常 → failed + data_status、当日成功采集后同日跳过。
    """

    @pytest.fixture()
    def db(self, tmp_path, monkeypatch):
        db_file = tmp_path / 'test_val_hist.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('600276', 'a_stock', '恒瑞医药')")
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('HK3690', 'hk_stock', '美团-W')")
        conn.commit()
        conn.close()

    @staticmethod
    def _mk_fake_baidu(pe_df, pb_df, calls):
        """构造 akshare stock_zh_valuation_baidu 的 fake（记录调用，区分 PE/PB）"""
        import akshare as ak

        def _fake(symbol, indicator, period):
            calls.append((symbol, indicator, period))
            assert symbol == '600276'
            if '市盈率' in indicator:
                return pe_df
            return pb_df

        return _fake

    def test_success_upsert_and_idempotent(self, db, monkeypatch):
        """A股成功：PE/PB 合并入库；重复调用幂等（行数不变、值更新）"""
        import akshare as ak
        import pandas as pd

        calls = []
        pe_df = pd.DataFrame({'date': ['2025-12-20', '2026-01-06'], 'value': [48.5, 56.04]})
        pb_df = pd.DataFrame({'date': ['2025-12-20', '2026-01-06'], 'value': [6.2, 7.0]})
        monkeypatch.setattr(ak, 'stock_zh_valuation_baidu', self._mk_fake_baidu(pe_df, pb_df, calls))

        status, msg = dc.fetch_valuation_history('600276', 'a_stock')
        assert status == 'success'
        assert '2 个交易日快照' in msg

        conn = db_manager.get_connection()
        rows = conn.execute(
            'SELECT trade_date, pe_ttm, pb, source FROM stock_valuation_history ORDER BY trade_date'
        ).fetchall()
        conn.close()
        assert len(rows) == 2
        assert rows[0]['trade_date'] == '2025-12-20'
        assert rows[0]['pe_ttm'] == 48.5
        assert rows[0]['pb'] == 6.2
        assert rows[0]['source'] == 'baidu'
        assert rows[1]['pe_ttm'] == 56.04

        # 幂等：再次采集（值变化后）→ UPSERT 更新而非新增
        pe_df2 = pd.DataFrame({'date': ['2025-12-20', '2026-01-06', '2026-01-21'], 'value': [50.0, 53.41, 51.0]})
        pb_df2 = pd.DataFrame({'date': ['2025-12-20', '2026-01-06', '2026-01-21'], 'value': [6.5, 6.8, 6.6]})
        monkeypatch.setattr(ak, 'stock_zh_valuation_baidu', self._mk_fake_baidu(pe_df2, pb_df2, calls))
        status2, _ = dc.fetch_valuation_history('600276', 'a_stock', force_refresh=True)
        assert status2 == 'success'
        conn = db_manager.get_connection()
        rows2 = conn.execute(
            'SELECT trade_date, pe_ttm, pb FROM stock_valuation_history ORDER BY trade_date'
        ).fetchall()
        conn.close()
        assert len(rows2) == 3, '应 UPSERT 更新旧点并新增新点，而非重复插入'
        assert rows2[0]['pe_ttm'] == 50.0, '旧点应被新值覆盖'
        assert rows2[2]['trade_date'] == '2026-01-21'

    def test_hk_skipped(self, db, monkeypatch):
        """港股：无稳定历史估值源 → skipped（不误报失败）"""
        status, msg = dc.fetch_valuation_history('HK3690', 'hk_stock')
        assert status == 'skipped'
        assert '仅支持 A 股' in msg

    def test_akshare_failure_records_failed(self, db, monkeypatch):
        """akshare 异常 → failed + data_status 留痕"""
        import akshare as ak

        def _boom(symbol, indicator, period):
            raise RuntimeError('network down')

        monkeypatch.setattr(ak, 'stock_zh_valuation_baidu', _boom)
        status, msg = dc.fetch_valuation_history('600276', 'a_stock')
        assert status == 'failed'
        assert '历史估值获取失败' in msg
        conn = db_manager.get_connection()
        row = conn.execute(
            "SELECT status, message FROM data_status WHERE stock_id=1 AND dimension='valuation_history' ORDER BY fetched_at DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row and row['status'] == 'failed'

    def test_same_day_skip(self, db, monkeypatch):
        """成功采集后当日再次调用 → 同日跳过（不再请求网络）"""
        import akshare as ak
        import pandas as pd

        calls = []
        pe_df = pd.DataFrame({'date': ['2026-08-01'], 'value': [45.0]})
        pb_df = pd.DataFrame({'date': ['2026-08-01'], 'value': [5.5]})
        monkeypatch.setattr(ak, 'stock_zh_valuation_baidu', self._mk_fake_baidu(pe_df, pb_df, calls))

        status, _ = dc.fetch_valuation_history('600276', 'a_stock')
        assert status == 'success'
        assert len(calls) == 2

        # 第二次调用：同日跳过，不触网
        status2, msg2 = dc.fetch_valuation_history('600276', 'a_stock')
        assert status2 == 'success'
        assert '同日跳过' in msg2
        assert len(calls) == 2, '同日跳过不应再次请求 akshare'
