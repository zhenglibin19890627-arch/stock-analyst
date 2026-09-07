"""
021Q：港股资金面/股东数据补强单元测试

覆盖：
1. westock hkfund _lgtHoldInfo 解析 → south_net_buy（港元→万港元）/ south_hold_ratio
2. westock shareholder 机构持仓统计 inst_prev 保留 + instCount 环比计算
3. _save_holder_structure 新列落库 roundtrip
4. compute_capital_detail 南下个股行 + 机构股东数量状态档位
5. 契约字段存在且【不在】资金面完整度集合（评分零影响——021R 校准后再接入）
"""

import database.db_manager as db_manager
import modules.data_collector as dc
from modules.capital_detail import compute_capital_detail
from modules.data_contract import StockData

# ============================================================
# 021Q：westock hkfund 带南下持仓块的真实输出样例（2026-08-19 hk00700 实测裁剪）
# ============================================================

_HKFUND_MD_LGT = """| code | AvgDealPrice | ClosePrice | EndDate | LgtHoldInfo | MainAvgDealPrice | MainIn | MainNetFlow | MainOut | RetailAvgDealPrice | RetailIn | RetailNetFlow | RetailOut | SecuCode | TotalNetFlow | _lgtHoldInfo.LgtCapChgDaily | _lgtHoldInfo.LgtCapChgQuarterly | _lgtHoldInfo.LgtHoldRatio | _lgtHoldInfo.LgtHoldShares | _lgtHoldInfo.LgtShareChgDaily | _lgtHoldInfo.LgtShareChgQuarterly |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hk00700 | 445.486 | 447.20 | 2026-08-19 | {"LgtCapChgDaily": "1110029191.83"} | 446.59 | 1179982380 | 402112140 | 777870240 | 445.56 | 2928215540 | 533781140 | 2394434400 | hk00700 | 935893280 | 1110029191.83 | 4663151482.24 | 11.78 | 1072300854.00 | 2516255.00 | 7605279.00 |
"""

_HKFUND_MD_NO_LGT = """| code | AvgDealPrice | ClosePrice | EndDate | MainAvgDealPrice | MainIn | MainNetFlow | MainOut | RetailIn | RetailOut | SecuCode | TotalNetFlow |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| hk00700 | 445.486 | 447.20 | 2026-08-19 | 446.59 | 1179982380 | 402112140 | 777870240 | 2928215540 | 2394434400 | hk00700 | 935893280 |
"""

# 机构持仓统计两期（021Q 环比计算依赖第二行）
_SHAREHOLDER_MD_2Q = """#### hk00700 腾讯控股 (2026-08-19)

**机构持仓统计**

| reportingPeriod | holdingPct | instCount | instIncreaseCount | holdingShares | changeShares |
| --- | --- | --- | --- | --- | --- |
| 2026 Q2 | 46.67 | 805 | -14 | 4189745967 | -25289533 |
| 2026 Q1 | 46.56 | 819 | -3 | 4201035924 | -8163304 |
"""


class TestWestockSouthbound:
    """hkfund _lgtHoldInfo → 南下资金字段解析。"""

    def test_lgt_fields_parsed(self, monkeypatch):
        monkeypatch.setattr(dc, '_westock_cooldown_active', lambda: False)

        def fake_cli(cmd, code, date_str=''):
            assert cmd == 'hkfund'
            return _HKFUND_MD_LGT

        monkeypatch.setattr(dc, '_westock_cli_query', fake_cli)
        row = dc._fetch_capital_flow_westock('HK0700', 'hk_stock')
        assert row is not None
        # LgtCapChgDaily=1110029191.83 港元 → 111002.92 万港元
        assert row['south_net_buy'] == 111002.92
        assert row['south_hold_ratio'] == 11.78
        # 主链字段不受影响
        assert row['main_net_inflow'] == 40211.21
        assert row['trade_date'] == '2026-08-19'

    def test_no_lgt_fields_none(self, monkeypatch):
        """非港股通标的（无 _lgtHoldInfo 列）→ 南下两字段 None，不报错。"""
        monkeypatch.setattr(dc, '_westock_cooldown_active', lambda: False)
        monkeypatch.setattr(dc, '_westock_cli_query', lambda cmd, code, date_str='': _HKFUND_MD_NO_LGT)
        row = dc._fetch_capital_flow_westock('HK0700', 'hk_stock')
        assert row is not None
        assert row['south_net_buy'] is None
        assert row['south_hold_ratio'] is None


class TestInstCount:
    """shareholder 机构股东数量 + 环比。"""

    def test_parse_keeps_prev_row(self):
        parsed = dc._parse_westock_shareholder(_SHAREHOLDER_MD_2Q)
        assert parsed['inst']['instCount'] == '805'
        assert parsed['inst_prev']['instCount'] == '819'

    def test_fetch_maps_inst_count(self, monkeypatch):
        monkeypatch.setattr(dc, '_HK_SHAREHOLDER_CACHE', {})
        monkeypatch.setattr(dc, '_westock_cli_query', lambda cmd, code, date_str='': _SHAREHOLDER_MD_2Q)
        data = dc._fetch_holder_structure_hk('HK0700')
        assert data['inst_count'] == 805
        # (805-819)/819*100 = -1.71（机构数量下降）
        assert data['inst_count_change_pct'] == -1.71
        # A股口径字段仍恒 None（港股无户数披露源）
        assert data['holder_count'] is None
        assert data['holder_count_change_pct'] is None
        assert data['source'] == 'westock'

    def test_single_quarter_no_chg(self, monkeypatch):
        """只有一期数据时环比为 None，机构数量仍可存。"""
        monkeypatch.setattr(dc, '_HK_SHAREHOLDER_CACHE', {})
        monkeypatch.setattr(
            dc, '_westock_cli_query', lambda cmd, code, date_str='': _SHAREHOLDER_MD_2Q.split('2026 Q1')[0] + '\n'
        )
        data = dc._fetch_holder_structure_hk('HK0700')
        assert data['inst_count'] == 805
        assert data['inst_count_change_pct'] is None

    def test_save_roundtrip(self, tmp_path, monkeypatch):
        db_file = tmp_path / 'hs_021q.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
        db_manager.init_database()
        conn = db_manager.get_connection()
        conn.execute("INSERT INTO stocks (symbol, market, name) VALUES ('HK0700', 'hk_stock', '腾讯控股')")
        conn.commit()
        conn.close()

        dc._save_holder_structure(
            1,
            {
                'stat_date': '2026-06-30',
                'inst_ratio': 46.67,
                'inst_shares': 4189745967.0,
                'inst_report_date': '2026-06-30',
                'inst_count': 805,
                'inst_count_change_pct': -1.71,
                'source': 'westock',
            },
        )
        conn = db_manager.get_connection()
        row = conn.execute(
            'SELECT inst_count, inst_count_change_pct, source FROM holder_structure WHERE stock_id=1'
        ).fetchone()
        conn.close()
        assert row['inst_count'] == 805
        assert row['inst_count_change_pct'] == -1.71
        assert row['source'] == 'westock'


class TestCapitalDetailHk:
    """compute_capital_detail 的港股新行。"""

    def test_south_rows(self):
        cap_rows = [
            {'trade_date': '2026-08-18', 'main_net_inflow': 100.0, 'south_net_buy': None, 'south_hold_ratio': 11.5},
            {'trade_date': '2026-08-19', 'main_net_inflow': 200.0, 'south_net_buy': 111002.92, 'south_hold_ratio': 11.78},
        ]
        d = compute_capital_detail(cap_rows)
        assert d['south_stock_net'] == 111002.92
        assert d['south_stock_state'] == '南下大幅增持'
        assert d['south_hold_ratio'] == 11.78

    def test_south_state_bands(self):
        bands = [
            (2500, '南下大幅增持'), (600, '南下温和增持'), (100, '南下小幅增持'),
            (-100, '南下小幅减持'), (-600, '南下温和减持'), (-2500, '南下大幅减持'),
        ]
        for v, expect in bands:
            d = compute_capital_detail([{'trade_date': '2026-08-19', 'south_net_buy': v}])
            assert d['south_stock_state'] == expect, f'{v} 应为 {expect}'

    def test_inst_count_states(self):
        hs = {'stat_date': '2026-06-30', 'inst_count': 805, 'inst_count_change_pct': -1.71}
        d = compute_capital_detail([], holder_structure=hs)
        assert d['inst_count'] == 805
        assert d['inst_count_change_pct'] == -1.71
        assert d['inst_count_state'] == '机构数量持平'
        hs2 = {'stat_date': '2026-06-30', 'inst_count': 900, 'inst_count_change_pct': 12.0}
        assert compute_capital_detail([], holder_structure=hs2)['inst_count_state'] == '机构数量大增'
        hs3 = {'stat_date': '2026-06-30', 'inst_count': 700, 'inst_count_change_pct': -15.0}
        assert compute_capital_detail([], holder_structure=hs3)['inst_count_state'] == '机构数量大减'

    def test_a_stock_rows_unaffected(self):
        """A股行（无南下字段）不产生南下键。"""
        d = compute_capital_detail([{'trade_date': '2026-08-19', 'main_net_inflow': 100.0}])
        assert 'south_stock_net' not in d
        assert 'south_hold_ratio' not in d


class TestContract021Q:
    """契约新字段：存在、可赋值、且不在资金面完整度集合（评分零影响）。"""

    def test_fields_exist_and_settable(self):
        data = StockData(
            code='HK0700', market='HK', trade_date='20260819', close=447.2,
            south_net_buy=111002.92, south_hold_ratio=11.78, inst_count_change_pct=-1.71,
        )
        assert data.south_net_buy == 111002.92
        assert data.south_hold_ratio == 11.78
        assert data.inst_count_change_pct == -1.71
        dumped = data.to_analysis_dict()
        assert dumped['south_net_buy'] == 111002.92
        assert dumped['inst_count_change_pct'] == -1.71

    def test_not_in_capital_completeness(self):
        """021Q 字段不入 CAPITAL 集合：missing_fields('capital') 不含新字段，
        data_quality.capital 分母仍为 4——评分与归一化零影响。"""
        data = StockData(code='HK0700', market='HK', trade_date='20260819', close=447.2)
        missing = data.missing_fields('capital')
        for f in ('south_net_buy', 'south_hold_ratio', 'inst_count_change_pct'):
            assert f not in missing
        dq = data.compute_data_quality()
        assert dq.capital == 0.0  # 4 个全缺 → 0.0（分母仍是 4，未被新字段稀释）

    def test_degradation_rules_registered(self):
        data = StockData(code='HK0700', market='HK', trade_date='20260819', close=447.2)
        for f in ('south_net_buy', 'south_hold_ratio', 'inst_count_change_pct'):
            assert data.get_degradation(f) is not None
