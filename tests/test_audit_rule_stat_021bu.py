"""
021BU t4 审计规则定稿测试：r1 审计报告规则统计口径（build_rule_stat 纯函数）

背景（t3 复审 §7-2，P2 展示统计）：r1 审计报告 §3 规则矩阵中全局规则
（R16 静态断言 / R18 / F06——随 findings 返回 OK 条目，不经 oks 通道）
的 OK 计数落不进「OK（一致）」列，R18 行出现「触发 2 / OK 0」的误导性统计。

定稿口径：「触发」列只计非 OK 发现；「OK（一致）」列 = oks 通道条目
（每股规则）+ findings 中 severity='OK' 条目（全局规则）。

隔离：纯函数测试，不触库、不触网。
"""

from scripts.audit_consistency_021bs import build_rule_stat


def _f(rule, sev):
    return {'rule': rule, 'severity': sev}


class TestBuildRuleStat:
    def test_per_stock_ok_via_oks_channel(self):
        """每股规则的 OK（oks 通道）计入 OK 列，不计触发。"""
        stat = build_rule_stat([], [{'rule': 'R16', 'note': 'x'}, {'rule': 'R16', 'note': 'y'}])
        assert stat['R16']['ok'] == 2
        assert stat['R16']['fires'] == 0

    def test_global_rule_ok_via_findings(self):
        """全局规则 OK（随 findings 返回）计入 OK 列——t3 复审 P2 修复面。"""
        findings = [_f('R18', 'OK'), _f('R18', 'OK')]
        stat = build_rule_stat(findings, [])
        assert stat['R18']['ok'] == 2
        assert stat['R18']['fires'] == 0
        assert stat['R18']['sev'] == {}

    def test_fires_counts_only_non_ok(self):
        """触发列只计 P0/P1/P2/INFO，OK 不占触发。"""
        findings = [
            _f('F06', 'OK'), _f('F06', 'OK'), _f('F06', 'OK'),
            _f('F06', 'P1'), _f('F06', 'P2'), _f('F06', 'INFO'),
        ]
        stat = build_rule_stat(findings, [])
        assert stat['F06']['fires'] == 3
        assert stat['F06']['ok'] == 3
        assert stat['F06']['sev'] == {'P1': 1, 'P2': 1, 'INFO': 1}

    def test_mixed_channels_merge(self):
        """两通道并存时 OK 合并累计（同规则既有个股 OK 又有全局 OK）。"""
        findings = [_f('R16', 'OK')]  # 静态同源断言的全局 OK
        oks = [{'rule': 'R16'}, {'rule': 'R16'}, {'rule': 'R16'}]  # 56 股的缩影
        stat = build_rule_stat(findings, oks)
        assert stat['R16']['ok'] == 4
        assert stat['R16']['fires'] == 0

    def test_pre_fix_semantics_would_have_shown_ok_zero(self):
        """回归锁定：旧口径（OK 占触发、OK 列恒 0）对本用例的失真形态不再出现。"""
        findings = [_f('R18', 'OK'), _f('R18', 'OK')]
        stat = build_rule_stat(findings, [])
        # 旧实现：fires=2 且无 ok 键 → 矩阵渲染出「触发 2 / OK 0」误导行
        assert not (stat['R18']['fires'] == 2 and stat['R18'].get('ok', 0) == 0)
