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

from modules import data_collector as dc
from modules.data_contract import StockData
from modules.mock_data_provider import MockDataProvider

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
# EM 回退逐只采集的进度回调（日报动效逐只更新）
# ============================================================


class TestEmBatchProgressCallback:
    """fetch_capital_flow_batch → _em_batch_collect 的 progress_cb 透传与逐只调用"""

    def test_progress_cb_called_per_symbol(self, monkeypatch):
        """THS 失败回退 EM 时，每只股票开始前回调一次（顺序正确）"""
        calls = []
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
        monkeypatch.setattr(dc, '_fetch_capital_flow_ths_batch', lambda: None)
        monkeypatch.setattr(dc, 'fetch_capital_flow', lambda sym, m: ('success', 'mock'))
        monkeypatch.setattr(dc, '_EM_INTER_DELAY_RANGE', (0.001, 0.002))
        monkeypatch.setattr(dc, '_EM_BATCH_GAP_RANGE', (0.001, 0.002))

        result = dc.fetch_capital_flow_batch(['600276'])
        assert result['success_count'] == 1
