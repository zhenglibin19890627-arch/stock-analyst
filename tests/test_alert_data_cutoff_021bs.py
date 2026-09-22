"""
021BS P2② 预警消息数据时点标注单元测试。

锁定契约（021BS 审计 F04：消除「预警说卖出、报告分数已另一口径」式时点差困惑）：
- _format_message 五类消息尾部自带数据时点（如「（数据截至 09-21 收盘）」）
- 时点源与各 check_* 判定面同源（rating_date / analysis_date / 最新交易日 / kline_upto）
- 日期解析失败返回 ''（不阻塞消息），文案禁裸 '<'（021BN 教训）
- 信号×评级相悖调和注记（021BQ 项④）与时点注记并存
"""

from modules.alert_engine import _data_cutoff_note, _fmt_mmdd, _format_message

_STOCK = {'symbol': '300229', 'name': '拓尔思', 'market': 'a_stock'}


class TestFmtMmdd:
    def test_full_date(self):
        assert _fmt_mmdd('2026-09-21') == '09-21'

    def test_datetime_suffix_tolerated(self):
        assert _fmt_mmdd('2026-09-21 15:54:00') == '09-21'
        assert _fmt_mmdd('2026-09-21T15:54:00') == '09-21'

    def test_invalid_returns_none(self):
        assert _fmt_mmdd(None) is None
        assert _fmt_mmdd('') is None
        assert _fmt_mmdd('bad-date') is None


class TestDataCutoffNote:
    def test_standard_shape(self):
        assert _data_cutoff_note('2026-09-21') == '（数据截至 09-21 收盘）'

    def test_label_override(self):
        note = _data_cutoff_note('2026-09-22', label='评级数据')
        assert note == '（评级数据截至 09-22 收盘）'

    def test_extra_clause(self):
        note = _data_cutoff_note('2026-09-22', extra='基于已采集K线离线复算')
        assert note == '（数据截至 09-22 收盘，基于已采集K线离线复算）'

    def test_unparseable_returns_empty(self):
        assert _data_cutoff_note(None) == ''
        assert _data_cutoff_note('') == ''
        assert _data_cutoff_note('bad') == ''


class TestFormatMessageCutoff:
    """五类消息尾部必须自带数据时点"""

    def test_rating_change(self):
        msg = _format_message('rating_change', _STOCK, {
            'direction': 'downgrade', 'old_rating': '持有观望', 'new_rating': '建议减仓',
            'old_score': 49.7, 'new_score': 52.0, 'latest_date': '2026-09-22',
        })
        assert '持有观望 → 建议减仓' in msg
        assert '（评级数据截至 09-22 收盘）' in msg
        assert '<' not in msg

    def test_score_below(self):
        msg = _format_message('score_below', _STOCK, {
            'score': 63.9, 'threshold': 65, 'analysis_date': '2026-09-21',
        })
        assert '63.9 分 < 阈值 65 分' in msg
        assert msg.endswith('（数据截至 09-21 收盘）')
        assert '<' not in msg.replace('分 < 阈值', '')  # 既有比较符除外，新增文案无裸 '<'

    def test_capital_outflow(self):
        msg = _format_message('capital_outflow', _STOCK, {
            'consecutive_days': 3, 'total_outflow': 1234.5,
            'latest_date': '2026-09-22', 'dates': ['2026-09-22', '2026-09-21', '2026-09-20'],
        })
        assert '连续3日净流出' in msg
        assert '（数据截至 09-22 收盘）' in msg
        assert '<' not in msg

    def test_tech_signal(self):
        msg = _format_message('tech_signal', _STOCK, {
            'signals': [{'signal': 'macd_golden_cross', 'label': 'MACD水下金叉',
                         'trigger_date': '2026-09-22'}],
            'resonances': [{'key': 'dual_golden', 'label': '双金叉共振', 'stars': 4}],
            'kline_upto': '2026-09-22', 'kline_count': 120,
            'current_rating': '建议减仓', 'rating_conflict': True,
        })
        assert '今日出现买点信号' in msg
        assert '（数据截至 09-22 收盘，基于已采集K线离线复算）' in msg
        assert '仅波段参考，以评级为主' in msg  # 021BQ 项④调和注记仍在
        assert '<' not in msg

    def test_sell_signal(self):
        msg = _format_message('sell_signal', _STOCK, {
            'signals': [{'signal': 'macd_death_cross', 'label': 'MACD水上死叉',
                         'trigger_date': '2026-09-22'}],
            'resonances': [],
            'kline_upto': '2026-09-22', 'kline_count': 120,
            'current_rating': None, 'rating_conflict': False,
        })
        assert '今日出现卖点信号' in msg
        assert '（数据截至 09-22 收盘，基于已采集K线离线复算）' in msg
        assert '<' not in msg

    def test_missing_date_does_not_block_message(self):
        """时点字段缺失：消息主体保留，注记为空（不阻塞）"""
        msg = _format_message('score_below', _STOCK, {
            'score': 63.9, 'threshold': 65, 'analysis_date': None,
        })
        assert '评分跌破阈值' in msg
        assert '数据截至' not in msg
