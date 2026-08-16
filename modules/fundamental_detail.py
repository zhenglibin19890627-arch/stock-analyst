"""基本面指标明细：为分析报告「四维评分详情·基本面」卡提供五类子项快照。

数据源：raw_fundamental 最新一期（调用方传入行 dict），纯函数、无 DB/网络依赖。
阈值口径与 modules/scoring_engine.py 的估值/盈利/成长/现金流/财务健康子项逐条对齐
（仅作展示标注，不参与评分计算）。
"""


def compute_fundamental_detail(f, forecast=None):
    """f: raw_fundamental 行 dict；forecast: 最新归母净利润预告行 dict（020R-49，可 None）→ 展示明细 dict。"""
    d = {}
    if f.get('report_date'):
        d['report_date'] = str(f['report_date'])[:10]

    # 1) 估值：PE(TTM) + PB
    pe = f.get('pe_ratio')
    if pe is not None:
        d['pe'] = round(pe, 2)
        if pe <= 0:
            d['pe_state'] = '亏损'
        elif pe <= 15:
            d['pe_state'] = '低估'
        elif pe <= 25:
            d['pe_state'] = '合理'
        elif pe <= 40:
            d['pe_state'] = '偏高'
        elif pe <= 60:
            d['pe_state'] = '高估'
        else:
            d['pe_state'] = '严重高估'
    pb = f.get('pb_ratio')
    if pb is not None:
        d['pb'] = round(pb, 2)
        if pb <= 0:
            d['pb_state'] = '负值'
        elif pb <= 1:
            d['pb_state'] = '破净'
        elif pb <= 2:
            d['pb_state'] = '合理偏低'
        elif pb <= 4:
            d['pb_state'] = '合理'
        elif pb <= 6:
            d['pb_state'] = '偏高'
        else:
            d['pb_state'] = '高估'

    # 2) 盈利能力：ROE + 毛利率
    roe = f.get('roe')
    if roe is not None:
        d['roe'] = round(roe, 2)
        if roe >= 20:
            d['roe_state'] = '优秀'
        elif roe >= 15:
            d['roe_state'] = '良好'
        elif roe >= 10:
            d['roe_state'] = '一般'
        elif roe >= 5:
            d['roe_state'] = '偏低'
        elif roe >= 0:
            d['roe_state'] = '较差'
        else:
            d['roe_state'] = '亏损'
    gm = f.get('gross_margin')
    if gm is not None:
        d['gross_margin'] = round(gm, 2)
        if gm >= 50:
            d['gm_state'] = '高'
        elif gm >= 30:
            d['gm_state'] = '中高'
        elif gm >= 15:
            d['gm_state'] = '中'
        elif gm >= 0:
            d['gm_state'] = '低'
        else:
            d['gm_state'] = '负值'

    # 3) 成长性：营收同比 + 净利润同比
    rg = f.get('revenue_growth')
    if rg is not None:
        d['revenue_growth'] = round(rg, 2)
        if rg >= 30:
            d['rg_state'] = '高增长'
        elif rg >= 20:
            d['rg_state'] = '较快增长'
        elif rg >= 10:
            d['rg_state'] = '稳步增长'
        elif rg >= 0:
            d['rg_state'] = '低速增长'
        elif rg >= -10:
            d['rg_state'] = '小幅下滑'
        else:
            d['rg_state'] = '明显下滑'
    pg = f.get('profit_growth')
    if pg is not None:
        d['profit_growth'] = round(pg, 2)
        if pg >= 50:
            d['pg_state'] = '高增长'
        elif pg >= 30:
            d['pg_state'] = '较快增长'
        elif pg >= 15:
            d['pg_state'] = '稳步增长'
        elif pg >= 0:
            d['pg_state'] = '低速增长'
        elif pg >= -20:
            d['pg_state'] = '小幅下滑'
        else:
            d['pg_state'] = '明显下滑'

    # 4) 现金流质量：经营现金流/净利润
    ocf = f.get('ocf_to_net_profit')
    if ocf is not None:
        d['ocf_to_profit'] = round(ocf, 2)
        if ocf >= 1.2:
            d['ocf_state'] = '充裕'
        elif ocf >= 0.8:
            d['ocf_state'] = '健康'
        elif ocf >= 0.5:
            d['ocf_state'] = '一般'
        elif ocf >= 0:
            d['ocf_state'] = '偏弱'
        else:
            d['ocf_state'] = '为负·警惕'

    # 5) 财务健康度：资产负债率 + 流动比率
    dr = f.get('debt_ratio')
    if dr is not None:
        d['debt_ratio'] = round(dr, 2)
        if dr <= 30:
            d['dr_state'] = '低杠杆'
        elif dr <= 50:
            d['dr_state'] = '适中'
        elif dr <= 60:
            d['dr_state'] = '偏高'
        elif dr <= 70:
            d['dr_state'] = '高杠杆'
        else:
            d['dr_state'] = '极高杠杆'
    cr = f.get('current_ratio')
    if cr is not None:
        d['current_ratio'] = round(cr, 2)
        if cr >= 2:
            d['cr_state'] = '充足'
        elif cr >= 1.5:
            d['cr_state'] = '良好'
        elif cr >= 1:
            d['cr_state'] = '正常'
        elif cr >= 0.5:
            d['cr_state'] = '偏紧'
        else:
            d['cr_state'] = '紧张'

    # 6) 业绩预告（020R-49：展示 + 成长性评分折价融合）
    if forecast:
        d['forecast_type'] = forecast.get('forecast_type')
        d['forecast_period'] = str(forecast.get('report_period') or '')[:8]
        d['forecast_change_pct'] = forecast.get('change_pct')

    return d
