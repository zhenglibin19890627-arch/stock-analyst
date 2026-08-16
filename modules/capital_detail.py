"""资金面指标明细：为分析报告「四维评分详情·资金面」卡提供子项快照。

数据源：raw_capital_flow 行列表 + holder_structure 最新一期（调用方传入），
纯函数、无 DB/网络依赖。口径与 modules/scoring_engine.py 的主力资金/互联互通/杠杆资金/
机构持仓/股东人数子项对齐（金额单位：万元），并与 data_adapter 的取值方式一致
（北向取最近非空、两融取最近两个非空值之差）。
"""


def compute_capital_detail(cap_rows, holder_structure=None, south_flow=None):
    """cap_rows: raw_capital_flow 行 dict 列表（升序）；holder_structure: 最新一期快照 dict；
    south_flow: 南向资金大盘快照 dict（仅港股展示参考，020R-47）。

    返回展示明细 dict；三个入参都为空时返回 None。
    """
    if not cap_rows and not holder_structure and not south_flow:
        return None

    d = {'trade_date': str(cap_rows[-1].get('trade_date'))[:10]} if cap_rows else {}

    if not cap_rows:
        # 无资金流数据但可能有股东结构数据
        pass
    else:
        latest = cap_rows[-1]
        main_net = latest.get('main_net_inflow')
        if main_net is not None:
            d['main_net'] = round(main_net, 2)
            if main_net >= 5000:
                d['main_state'] = '大幅净流入'
            elif main_net >= 1000:
                d['main_state'] = '温和净流入'
            elif main_net >= 0:
                d['main_state'] = '小幅净流入'
            elif main_net >= -1000:
                d['main_state'] = '小幅净流出'
            elif main_net >= -5000:
                d['main_state'] = '温和净流出'
            else:
                d['main_state'] = '大幅净流出'

        # 主力 5 日均（辅助展示）
        vals = [r.get('main_net_inflow') for r in cap_rows[-5:] if r.get('main_net_inflow') is not None]
        if vals:
            d['main_avg_5d'] = round(sum(vals) / len(vals), 2)

        # 2) 互联互通（权重 0.10）：北向/港股通净买入，最近非空值（万元）
        for r in reversed(cap_rows):
            v = r.get('north_holding_change')
            if v is not None:
                d['north_net'] = round(v, 2)
                if v >= 3000:
                    d['north_state'] = '北向大幅买入'
                elif v >= 500:
                    d['north_state'] = '北向温和买入'
                elif v >= 0:
                    d['north_state'] = '北向小幅买入'
                elif v >= -500:
                    d['north_state'] = '北向小幅卖出'
                elif v >= -3000:
                    d['north_state'] = '北向温和卖出'
                else:
                    d['north_state'] = '北向大幅卖出'
                break

        # 3) 杠杆资金（权重 0.20）：融资余额最近两个非空值之差（万元）
        margin_vals = [
            r.get('margin_balance') for r in reversed(cap_rows) if r.get('margin_balance') is not None
        ][:2]
        if len(margin_vals) >= 2:
            chg = round(margin_vals[0] - margin_vals[1], 2)
            d['margin_chg'] = chg
            if chg >= 2000:
                d['margin_state'] = '融资余额大幅增加'
            elif chg >= 500:
                d['margin_state'] = '融资余额增加'
            elif chg >= 0:
                d['margin_state'] = '融资余额小幅增加'
            elif chg >= -500:
                d['margin_state'] = '融资余额小幅减少'
            elif chg >= -2000:
                d['margin_state'] = '融资余额减少'
            else:
                d['margin_state'] = '融资余额大幅减少'

    # 4) 机构持仓（020R-45，权重 0.20）
    if holder_structure:
        d['holder_stat_date'] = holder_structure.get('stat_date')
        d['holder_count'] = holder_structure.get('holder_count')
        chg_pct = holder_structure.get('holder_count_change_pct')
        if chg_pct is not None:
            d['holder_count_change_pct'] = round(chg_pct, 2)
            if chg_pct <= -10:
                d['holder_state'] = '户数大幅减少·筹码集中'
            elif chg_pct <= -5:
                d['holder_state'] = '户数减少·筹码集中'
            elif chg_pct <= -1:
                d['holder_state'] = '户数略降'
            elif chg_pct <= 1:
                d['holder_state'] = '户数持平'
            elif chg_pct <= 5:
                d['holder_state'] = '户数略增·筹码分散'
            else:
                d['holder_state'] = '户数大幅增加·筹码分散'
        ratio = holder_structure.get('inst_ratio')
        if ratio is not None:
            d['inst_ratio'] = round(ratio, 2)
            if ratio >= 60:
                d['inst_state'] = '机构重仓'
            elif ratio >= 40:
                d['inst_state'] = '机构高配'
            elif ratio >= 25:
                d['inst_state'] = '机构中等持仓'
            elif ratio >= 10:
                d['inst_state'] = '机构低配'
            else:
                d['inst_state'] = '机构极少关注'
        d['inst_report_date'] = holder_structure.get('inst_report_date')

    # 5) 南向资金参考（020R-47，仅港股展示，不参评）
    if south_flow:
        d['south_date'] = south_flow.get('trade_date')
        d['south_net_buy'] = south_flow.get('net_buy')
        d['south_hold_mv'] = south_flow.get('hold_market_value')
        d['south_cumulative'] = south_flow.get('cumulative_net')

    return d
