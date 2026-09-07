"""
021BJ：行业资金流缺口回补

覆盖（离线）：
1. 历史日K行解析：字段映射 / 目标日期过滤 / 非法值容错
2. 上一个工作日推算（跨周末）
"""

from modules.market_overview import _parse_fflow_hist_row, _previous_weekday


class TestParseFflowHistRow:
    def test_valid_row_mapped(self):
        # f51日期,f52主力,f53小单,f54中单,f55大单,f56超大单,f57主力占比,...,f63涨跌幅
        row = ('2026-09-02,-12345.67,-100.50,2000.30,8000.20,4345.40,'
               '12.34,-1.20,2.10,5.60,6.70,45.60,1.23,0,0')
        item = _parse_fflow_hist_row(row, '2026-09-02', 'BK0420', '航空机场')
        assert item['code'] == 'BK0420' and item['name'] == '航空机场'
        assert item['main_net'] == -12345.67
        assert item['super_net'] == 4345.40
        assert item['big_net'] == 8000.20
        assert item['mid_net'] == 2000.30
        assert item['small_net'] == -100.50
        assert item['main_pct'] == 12.34
        assert item['pct_change'] == 1.23
        assert item['lead_stock'] is None   # 历史接口无领涨股

    def test_date_mismatch_returns_none(self):
        row = '2026-09-03,1,2,3,4,5,6,7,8,9,10,11,12,13,14'
        assert _parse_fflow_hist_row(row, '2026-09-02', 'BK0420', 'x') is None

    def test_dash_values_tolerated(self):
        row = '2026-09-02,-,-,-,-,-,-,-,-,-,-,-,-,-,-'
        item = _parse_fflow_hist_row(row, '2026-09-02', 'BK0420', 'x')
        assert item['main_net'] is None and item['pct_change'] is None

    def test_short_row_returns_none(self):
        assert _parse_fflow_hist_row('2026-09-02,1,2', '2026-09-02', 'BK0420', 'x') is None


class TestPreviousWeekday:
    def test_midweek(self):
        assert _previous_weekday('2026-09-02') == '2026-09-01'   # 周三→周二

    def test_monday_back_to_friday(self):
        assert _previous_weekday('2026-09-07') == '2026-09-04'   # 周一→上周五
