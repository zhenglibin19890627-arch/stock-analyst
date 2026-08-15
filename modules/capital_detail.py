"""资金面指标明细：为分析报告「四维评分详情·资金面」卡提供三个子项快照。

数据源：raw_capital_flow 行列表（调用方传入，按 trade_date 升序），纯函数、无 DB/网络依赖。
口径与 modules/scoring_engine.py 的主力资金/互联互通/杠杆资金子项对齐（单位：万元），
并与 data_adapter 的取值方式一致（北向取最近非空、两融取最近两个非空值之差）。
"""


def compute_capital_detail(cap_rows):
    """cap_rows: raw_capital_flow 行 dict 列表（升序）→ 展示明细 dict；无数据返回 None。"""
    if not cap_rows:
        return None

    d = {'trade_date': str(cap_rows[-1].get('trade_date'))[:10]}

    # 1) 主力资金（权重 0.55）：最新主力净流入（万元）
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

    # 3) 杠杆资金（权重 0.35）：融资余额最近两个非空值之差（万元）
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

    return d
