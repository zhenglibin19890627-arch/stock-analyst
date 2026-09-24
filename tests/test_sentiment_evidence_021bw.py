"""
021BW 情绪维度实施测试：O3 市场报告「情绪检验现状」解读行 + O5① 换手率旁路修复

依据：docs/reports/021bw_sentiment_plan_20260924.md（t1 检验方案）与实施范围
（captain 收敛：O3 透明说明 + O5① 换手率断供修复；O1 权重/评分不动、O2/O4 不做）。

覆盖：
1. O3 纯函数 sentiment_evidence_note_for：分市场成行（R20）、未知市场不加行、
   文案禁裸 '<'、结构性锚点（门槛/结论句/复核脚本/五类代理关键词）
2. O3 展示路径：compute_market_report interpretation_parts 内联（报告页/
   看板同源同值），零样本市场诚实不加行
3. O5① 纯函数 _em_secid / _parse_em_kline_turnover / _merge_turnover
4. O5① 采集路径 fetch_kline：旁路值入库、旁路失败优雅降级（0=缺失不崩溃）、
   既有非零换手率只升不降（旁路失败日历史真值不被覆盖）

隔离：临时库（monkeypatch DB_PATH），不触网（EM 请求全 mock）、不触碰真实库。
"""

import datetime as _dt

import pandas as pd
import pytest

from database import db_manager
from database.db_manager import get_connection, init_database
from modules.backtest_engine import (
    SENTIMENT_EVIDENCE_NOTE,
    sentiment_evidence_note_for,
)
from modules.collector import kline as _kline

# ================================================================
# 一、O3：情绪检验现状解读行（纯函数）
# ================================================================


class TestSentimentEvidenceNotePure:
    def test_markets_have_notes(self):
        assert SENTIMENT_EVIDENCE_NOTE['a_stock']
        assert SENTIMENT_EVIDENCE_NOTE['hk_stock']

    def test_unknown_market_returns_none(self):
        """未知/缺省市场不加行（A股数字不得代表其他市场，R20）。"""
        assert sentiment_evidence_note_for('us_stock') is None
        assert sentiment_evidence_note_for(None) is None
        assert sentiment_evidence_note_for('') is None

    def test_r20_market_separation(self):
        """A/H 两行互不相同且不含对方市场样本口径（R20 分市场、数字严禁互推）。"""
        a = SENTIMENT_EVIDENCE_NOTE['a_stock']
        hk = SENTIMENT_EVIDENCE_NOTE['hk_stock']
        assert a != hk
        assert 'A股' in a and '140条' not in a
        assert '港股' in hk and '670条' not in hk

    def test_no_bare_less_than(self):
        """文案禁裸 '<'（backtest.js interpLi innerHTML 直插，渲染安全）。"""
        for market, note in SENTIMENT_EVIDENCE_NOTE.items():
            assert '<' not in note, f'{market} 行含裸 <'
            assert sentiment_evidence_note_for(market) == note

    def test_structural_anchors(self):
        """结构性锚点：门槛定义、结论句、复核脚本、五类代理逐一在场。"""
        a = SENTIMENT_EVIDENCE_NOTE['a_stock']
        assert '入模型门槛' in a
        assert '纪律与位置因子优先' in a  # 结论句（captain 口径）
        assert 'query_sentiment_evidence_021bw.py' in a  # 复核脚本指针
        for keyword in ('新闻情绪', '主力', '散户', '杠杆情绪', '市场热度'):
            assert keyword in a, f'五类代理缺 {keyword}'
        assert '021BC' in a  # 降权证据引用（消息面 0.15→0.08 已计价）
        assert 'n≥20' in a and '15pp' in a  # 门槛定义（诚实展示门槛在场）
        hk = SENTIMENT_EVIDENCE_NOTE['hk_stock']
        assert '暂缓' in hk
        assert '--market hk_stock' in hk


# ================================================================
# 二、O3：展示路径（compute_market_report 内联）
# ================================================================


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_file = tmp_path / 'test_sent_021bw.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()
    from modules.backtest_engine import BacktestEngine

    BacktestEngine()  # 引擎动态列幂等追加（生产同机制）

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600001', 'a_stock', '甲')")
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('00700', 'hk_stock', '乙')")
        # A股：持有观望 35 行真实样本（让市场报告走完整解读路径）
        _base = _dt.datetime(2026, 8, 1)
        for i in range(35):
            _d = (_base + _dt.timedelta(days=i)).strftime('%Y-%m-%d')
            conn.execute(
                "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
                'price_at_rating, return_1d, is_correct, is_simulated) '
                "VALUES (1, 100, 'a_stock', ?, '持有观望', 10.0, 1.0, ?, 0)",
                (_d, 1 if i < 34 else 0),
            )
        # 港股：建议减仓 21 行
        for i in range(21):
            conn.execute(
                "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
                'price_at_rating, return_1d, is_correct, is_simulated) '
                "VALUES (2, 200, 'hk_stock', ?, '建议减仓', 20.0, -1.0, ?, 0)",
                (f'2026-08-{i % 28 + 1:02d}', 1 if i < 15 else 0),
            )
        conn.commit()
    finally:
        conn.close()
    yield


class TestMarketReportCarriesNote:
    def test_a_stock_note_inline(self, db):
        from modules.backtest_engine import BacktestEngine

        report = BacktestEngine().compute_market_report('a_stock')
        notes = [p for p in report['interpretation_parts'] if p.startswith('情绪检验现状')]
        assert len(notes) == 1
        assert notes[0] == sentiment_evidence_note_for('a_stock')  # 同源同值
        idx = report['interpretation_parts'].index(notes[0])
        assert report['interpretation_tones'][idx] == 'neutral'
        assert '不构成投资建议' in report['interpretation_parts'][-1]

    def test_hk_note_inline_r20(self, db):
        from modules.backtest_engine import BacktestEngine

        report = BacktestEngine().compute_market_report('hk_stock')
        notes = [p for p in report['interpretation_parts'] if p.startswith('情绪检验现状')]
        assert len(notes) == 1
        assert notes[0] == sentiment_evidence_note_for('hk_stock')
        assert notes[0] != sentiment_evidence_note_for('a_stock')

    def test_empty_market_no_note(self, tmp_path, monkeypatch):
        """零样本市场：诚实早退（暂无数据），不强加情绪行。"""
        db_file = tmp_path / 'test_sent_empty.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups2'))
        init_database()
        from modules.backtest_engine import BacktestEngine

        report = BacktestEngine().compute_market_report('a_stock')
        assert report['total'] == 0
        assert not any(
            p.startswith('情绪检验现状') for p in report.get('interpretation_parts', []))


# ================================================================
# 三、O5①：换手率旁路（纯函数）
# ================================================================


class TestEmSecid:
    @pytest.mark.parametrize(
        'symbol,market,expected',
        [
            ('600276', 'a_stock', '1.600276'),
            ('000001', 'a_stock', '0.000001'),
            ('300750', 'a_stock', '0.300750'),
            ('00700', 'hk_stock', '116.00700'),
            ('', 'a_stock', None),
            (None, 'a_stock', None),
            ('60027', 'a_stock', None),   # 非 6 位
            ('abc123', 'a_stock', None),  # 非数字
            ('123456', 'us_stock', None),  # 不支持的市场
        ],
    )
    def test_secid(self, symbol, market, expected):
        assert _kline._em_secid(symbol, market) == expected


class TestParseEmKlineTurnover:
    def test_parse_and_missing_rules(self):
        payload = {
            'data': {
                'klines': [
                    '2026-09-22,46.04,45.72,46.14,45.45,722754,3307838672.00,1.50,-0.70,-0.32,1.13',
                    '2026-09-23,45.59,45.58,46.09,45.41,434384,1983663514.00,1.49,-0.31,-0.14,0.68',
                    '2026-09-24,45.38,44.79,45.77,44.60,253788,1140028697.00,2.57,-1.73,-0.79,0',  # 0=缺失
                    '2026-09-25,bad,row',  # 列数不足跳过
                    '2026-09-26,1,2,3,4,5,6,7,8,9,x',  # 非数字跳过
                ]
            }
        }
        out = _kline._parse_em_kline_turnover(payload)
        assert out == {'2026-09-22': 1.13, '2026-09-23': 0.68}

    def test_none_and_empty_payload(self):
        assert _kline._parse_em_kline_turnover(None) == {}
        assert _kline._parse_em_kline_turnover({}) == {}
        assert _kline._parse_em_kline_turnover({'data': None}) == {}
        assert _kline._parse_em_kline_turnover({'data': {'klines': []}}) == {}


class TestMergeTurnover:
    def _df(self):
        return pd.DataFrame(
            [
                {'日期': '2026-08-12', '开盘': 9.0, '收盘': 9.5, '成交量': 1000},
                {'日期': '2026-08-13', '开盘': 9.6, '收盘': 10.0, '成交量': 800},
            ]
        )

    def test_merge_by_date(self):
        df = self._df()
        out = _kline._merge_turnover(df, {'2026-08-12': 2.5})
        assert list(out['换手率']) == [2.5, 0.0]  # 未命中行=0（缺失语义）
        assert '换手率' not in df.columns  # 入参 df 不被修改（纯函数）

    def test_nonpositive_treated_missing(self):
        out = _kline._merge_turnover(self._df(), {'2026-08-12': -1.0, '2026-08-13': 0.0})
        assert list(out['换手率']) == [0.0, 0.0]

    def test_noop_paths(self):
        df = self._df()
        assert _kline._merge_turnover(df, {}) is df  # 空映射原样返回
        assert _kline._merge_turnover(None, {'2026-08-12': 1.0}) is None
        assert _kline._merge_turnover(pd.DataFrame(), {'2026-08-12': 1.0}).empty


# ================================================================
# 四、O5①：采集路径（fetch_kline 入库/降级/只升不降）
# ================================================================


class _FakeDT:
    """伪造 datetime：now() 返回固定时刻（避开盘中刷新分支）。"""

    def __init__(self, real):
        self._real = real

    def now(self, tz=None):
        return self._real


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


def _patch_now(monkeypatch, year=2026, month=8, day=14, hour=20, minute=0):
    monkeypatch.setattr(
        _kline, 'datetime', _FakeDT(_dt.datetime(year, month, day, hour, minute)))


@pytest.fixture()
def kdb(tmp_path, monkeypatch):
    db_file = tmp_path / 'test_kturn_021bw.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()
    conn = get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600276', 'a_stock', '恒瑞医药')")
    conn.commit()
    conn.close()
    yield db_manager


def _kline_df():
    """腾讯主源返回形态（无换手率列——与真实接口一致）。"""
    return pd.DataFrame(
        [
            {'日期': '2026-08-12', '开盘': 9.0, '收盘': 9.5, '最高': 9.8,
             '最低': 8.9, '成交量': 1000, '成交额': 0.0, '涨跌幅': 0.0},
            {'日期': '2026-08-13', '开盘': 9.6, '收盘': 10.0, '最高': 10.2,
             '最低': 9.5, '成交量': 800, '成交额': 0.0, '涨跌幅': 5.26},
            {'日期': '2026-08-14', '开盘': 10.0, '收盘': 10.4, '最高': 10.5,
             '最低': 9.9, '成交量': 900, '成交额': 0.0, '涨跌幅': 4.0},
        ]
    )


_EM_PAYLOAD = {
    'data': {
        'klines': [
            '2026-08-12,9.0,9.5,9.8,8.9,1000,950000.0,1.0,1.0,0.09,2.5',
            '2026-08-13,9.6,10.0,10.2,9.5,800,800000.0,1.0,5.26,0.5,0',
            '2026-08-14,10.0,10.4,10.5,9.9,900,936000.0,1.0,4.0,0.4,1.8',
        ]
    }
}


class TestFetchKlineTurnover:
    def test_bypass_values_written(self, kdb, monkeypatch):
        """旁路值按日对齐入库；旁路缺失日（EM 0 值）保持 0=缺失。"""
        _patch_now(monkeypatch)
        monkeypatch.setattr(_kline, '_fetch_kline_tencent', lambda *a, **k: _kline_df())
        monkeypatch.setattr(
            _kline, '_http_get_em', lambda *a, **k: _FakeResp(_EM_PAYLOAD))

        status, msg = _kline.fetch_kline('600276', 'a_stock')
        assert status == 'success'
        conn = kdb.get_connection()
        rows = {
            r['trade_date']: r['turnover']
            for r in conn.execute(
                'SELECT trade_date, turnover FROM raw_kline WHERE stock_id=1').fetchall()
        }
        conn.close()
        assert rows['2026-08-12'] == pytest.approx(2.5)
        assert rows['2026-08-13'] == 0.0  # EM 0 值=缺失，不入桶不臆造
        assert rows['2026-08-14'] == pytest.approx(1.8)

    def test_em_failure_graceful_degrade(self, kdb, monkeypatch):
        """旁路失败：主采集照常成功（价格五档来自腾讯），turnover 保持缺失不崩溃。"""
        _patch_now(monkeypatch)
        monkeypatch.setattr(_kline, '_fetch_kline_tencent', lambda *a, **k: _kline_df())

        def _boom(*a, **k):
            raise ConnectionError('东方财富接口无法访问')

        monkeypatch.setattr(_kline, '_http_get_em', _boom)
        status, msg = _kline.fetch_kline('600276', 'a_stock')
        assert status == 'success'
        conn = kdb.get_connection()
        vals = [r['turnover'] for r in conn.execute(
            'SELECT turnover FROM raw_kline WHERE stock_id=1').fetchall()]
        conn.close()
        assert vals == [0.0, 0.0, 0.0]

    def test_existing_nonzero_never_zeroed(self, kdb, monkeypatch):
        """只升不降：库内既有非零换手率在旁路失败日不被 0 覆盖（历史真值保护）。"""
        _patch_now(monkeypatch)
        conn = kdb.get_connection()
        conn.execute(
            'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume, '
            "turnover, pct_change) VALUES (1, '2026-08-12', 9.0, 9.5, 9.8, 8.9, 1000, 3.3, 0)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(_kline, '_fetch_kline_tencent', lambda *a, **k: _kline_df())

        def _boom(*a, **k):
            raise ConnectionError('EM 旁路失败')

        monkeypatch.setattr(_kline, '_http_get_em', _boom)
        status, _msg = _kline.fetch_kline('600276', 'a_stock')
        assert status == 'success'
        conn = kdb.get_connection()
        rows = {
            r['trade_date']: r['turnover']
            for r in conn.execute(
                'SELECT trade_date, turnover FROM raw_kline WHERE stock_id=1').fetchall()
        }
        conn.close()
        assert rows['2026-08-12'] == pytest.approx(3.3)  # 既有真值保留
        assert rows['2026-08-13'] == 0.0

    def test_bypass_upgrade_over_legacy_zero(self, kdb, monkeypatch):
        """旁路可用时覆盖历史 0 值（0=缺失可被真值升级，非真值互相覆盖）。"""
        _patch_now(monkeypatch)
        conn = kdb.get_connection()
        conn.execute(
            'INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume, '
            "turnover, pct_change) VALUES (1, '2026-08-12', 9.0, 9.5, 9.8, 8.9, 1000, 0, 0)")
        conn.commit()
        conn.close()

        monkeypatch.setattr(_kline, '_fetch_kline_tencent', lambda *a, **k: _kline_df())
        monkeypatch.setattr(
            _kline, '_http_get_em', lambda *a, **k: _FakeResp(_EM_PAYLOAD))
        status, _msg = _kline.fetch_kline('600276', 'a_stock')
        assert status == 'success'
        conn = kdb.get_connection()
        row = conn.execute(
            "SELECT turnover FROM raw_kline WHERE stock_id=1 AND trade_date='2026-08-12'"
        ).fetchone()
        conn.close()
        assert row['turnover'] == pytest.approx(2.5)  # 0（缺失）被旁路真值升级

    def test_unsupported_market_no_em_call(self, kdb, monkeypatch):
        """secid 不支持（如非数字代码）：不发起 EM 请求，直接缺失。"""

        def _boom(*a, **k):
            raise AssertionError('不支持的市场不应发起换手率旁路请求')

        monkeypatch.setattr(_kline, '_http_get_em', _boom)
        assert _kline.fetch_kline_turnover_em('abc123', 'a_stock') == {}
