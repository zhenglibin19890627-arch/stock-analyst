"""021BN：行业资金流快照完整性校验 + 同级别排名测试

背景（实测缺陷）：
- 部分页失败的截断快照曾直接落库，industry_fund_flow 各日行数在 200/300/496 间漂移，
  "第x/y名"的分母逐日不可比；
- 快照内一/二/三级行业混排（银行 与 银行Ⅱ/证券Ⅲ 同场），跨级比主力净流入口径错配。
"""

import pytest

from database import db_manager
from modules.market_overview import (
    _board_level,
    get_industry_flow_bg_map,
    get_industry_fund_flow_for_date,
    save_industry_fund_flow,
)


@pytest.fixture
def iff_db(tmp_path, monkeypatch):
    db_file = tmp_path / 'iff021bm.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    return db_manager


def _item(code, name, main_net):
    return {
        'code': code,
        'name': name,
        'pct_change': 0.0,
        'main_net': main_net,
        'main_pct': 0.0,
        'super_net': 0.0,
        'big_net': 0.0,
        'mid_net': 0.0,
        'small_net': 0.0,
        'lead_stock': None,
    }


class TestBoardLevel:
    def test_suffix_levels(self):
        assert _board_level('银行Ⅱ') == 'Ⅱ'
        assert _board_level('证券Ⅲ') == 'Ⅲ'
        assert _board_level('电子') == 'Ⅰ'

    def test_none_and_empty(self):
        assert _board_level(None) == 'Ⅰ'
        assert _board_level('') == 'Ⅰ'


class TestSnapshotCompleteness:
    def test_partial_snapshot_rejected(self, iff_db):
        """截断快照（len(items) < expected_total）拒绝落库，当日旧快照保持原状"""
        ok1 = save_industry_fund_flow([_item('BK9', '半导体', 300.0)], '2026-09-07')
        assert ok1 is True
        # 模拟部分页失败：全集 5 个行业，只抓到 2 个
        ok2 = save_industry_fund_flow(
            [_item('BK1', '电子', 100.0), _item('BK2', '白酒', -50.0)],
            '2026-09-07',
            expected_total=5,
        )
        assert ok2 is False
        items, _ = get_industry_fund_flow_for_date('2026-09-07')
        assert len(items) == 1
        assert items[0]['name'] == '半导体'
        assert items[0]['main_net'] == 300.0

    def test_full_snapshot_saved(self, iff_db):
        """len(items) >= expected_total 正常落库"""
        ok = save_industry_fund_flow(
            [_item('BK1', '电子', 100.0), _item('BK2', '白酒', -50.0)],
            '2026-09-07',
            expected_total=2,
        )
        assert ok is True
        items, _ = get_industry_fund_flow_for_date('2026-09-07')
        assert len(items) == 2

    def test_no_expected_total_keeps_old_behavior(self, iff_db):
        """expected_total=None（历史调用方/回补路径）不做校验"""
        ok = save_industry_fund_flow([_item('BK1', '电子', 100.0)], '2026-09-07')
        assert ok is True
        items, _ = get_industry_fund_flow_for_date('2026-09-07')
        assert len(items) == 1


class TestSameLevelRank:
    def test_rank_computed_within_level(self, iff_db):
        """一/二级板块分开排名：银行Ⅱ(二级) 的排名不再稀释一级板块分母"""
        save_industry_fund_flow(
            [
                _item('BK1', '半导体', 100.0),
                _item('BK2', '银行', 50.0),
                _item('BK3', '银行Ⅱ', 80.0),  # 净流入介于两者之间，但属于二级
            ],
            '2026-09-07',
        )
        m = get_industry_flow_bg_map('2026-09-07')
        # 一级：半导体(100) > 银行(50)
        assert m['半导体']['rank'] == 1
        assert m['半导体']['total'] == 2
        assert m['半导体']['level'] == 'Ⅰ'
        assert m['银行']['rank'] == 2
        assert m['银行']['total'] == 2
        # 二级：银行Ⅱ 独占一级之外的名次空间
        assert m['银行Ⅱ']['rank'] == 1
        assert m['银行Ⅱ']['total'] == 1
        assert m['银行Ⅱ']['level'] == 'Ⅱ'

    def test_legacy_flat_rank_regression(self, iff_db):
        """同名同级两板块保持整体有序性（回归 020R-54 原断言口径）"""
        save_industry_fund_flow(
            [_item('BK1', '半导体', 300.0), _item('BK2', '白酒', -150.0)],
            '2026-08-14',
        )
        m = get_industry_flow_bg_map('2026-08-14')
        assert m['半导体']['rank'] == 1 and m['半导体']['total'] == 2
        assert m['白酒']['rank'] == 2 and m['白酒']['total'] == 2
