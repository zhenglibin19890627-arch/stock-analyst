"""数据完整度域（t6 拆包）：报告生成前的各维度数据新鲜度/来源检查。

_build_data_freshness 的结果随报告输出（data_warnings + markdown「数据完整度」小节），
并被 advisor（019A 刷新路径）与 blueprints/analysis（快照路径）经 facade 复用，
保证三条路径数据完整度口径一致。消费方 monkeypatch 一律指向本模块。
"""

from datetime import datetime

from modules.daily_report._env import _CN_TZ, get_connection, logger


def _days_between(d1, d2):
    """两个 YYYY-MM-DD 字符串的自然日差（d2 - d1），解析失败返回 None"""
    try:
        a = datetime.strptime(str(d1)[:10], '%Y-%m-%d')
        b = datetime.strptime(str(d2)[:10], '%Y-%m-%d')
        return (b - a).days
    except (ValueError, TypeError):
        return None


def _build_data_freshness(stock_id):
    """报告生成前的数据完整度检查：各维度数据新鲜度与来源。

    返回 dict：
      {'lines': [str, ...],      # 逐维度的完整度说明行（含 ⚠️ 标记）
       'has_issue': bool}        # 是否存在滞后/替代源等需要注意的问题
    """
    today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    lines: list = []
    has_issue = False
    conn = get_connection()

    # 020R-19：市场最新交易日 = 全部自选股K线日期最大值。
    # 休市日（周末/节假日）数据至最新交易日即为"最新"，不再按自然日误报"滞后N天"。
    _td_row = conn.execute(
        "SELECT MAX(substr(trade_date, 1, 10)) d FROM raw_kline"
    ).fetchone()
    latest_td = _td_row['d'] if (_td_row and _td_row['d']) else today

    # 1. K线（技术面）
    row = conn.execute(
        'SELECT MAX(trade_date) d FROM raw_kline WHERE stock_id=?', (stock_id,)
    ).fetchone()
    if row and row['d']:
        lag = _days_between(row['d'], latest_td)
        if lag is not None and lag <= 0:
            lines.append(f"K线：至 {row['d']}（最新）")
        else:
            flag = ' ⚠️' if (lag is not None and lag > 3) else ''
            if flag:
                has_issue = True
            lines.append(f"K线：至 {row['d']}（滞后{lag}天{flag}）")
    else:
        lines.append('K线：缺失 ⚠️')
        has_issue = True

    # 2. 基本面（财报期为自然滞后，只展示报告期；020R-57-HF1：缺失时与其它维度一致标 ⚠️）
    row = conn.execute(
        'SELECT MAX(report_date) d FROM raw_fundamental WHERE stock_id=?', (stock_id,)
    ).fetchone()
    if row and row['d']:
        lines.append(f"基本面：最新财报期 {row['d']}")
    else:
        lines.append('基本面：缺失 ⚠️')
        has_issue = True

    # 3. 资金面（来源：东财真实 / 新浪顶替 / 估算兜底）
    row = conn.execute(
        'SELECT trade_date d, capital_source s, is_estimated e '
        'FROM raw_capital_flow WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1',
        (stock_id,),
    ).fetchone()
    if row:
        if row['s'] == 'sina_main':
            src, issue = '新浪顶替(主力口径)', True
        elif row['s'] == 'westock':
            src, issue = '腾讯自选股', False
        elif row['e'] == 1:
            src, issue = '估算兜底(不参评)', True
        elif row['s'] == 'ths_total':
            src, issue = '同花顺顶替', True
        else:
            src, issue = '东财真实', False
        lag = _days_between(row['d'], latest_td)
        if lag is not None and lag <= 0:
            if issue:
                has_issue = True
            lines.append(f"资金面：至 {row['d']}（来源：{src}，最新{' ⚠️' if issue else ''}）")
        else:
            flag = ' ⚠️' if (issue or (lag is not None and lag > 2)) else ''
            if flag:
                has_issue = True
            lines.append(f"资金面：至 {row['d']}（来源：{src}，滞后{lag}天{flag}）")
    else:
        lines.append('资金面：缺失 ⚠️')
        has_issue = True

    # 4. 消息面（情绪聚合 + 新闻原文新鲜度）
    # 020R-57：区分「采集滞后」与「无新消息」——情绪表覆盖最新交易日=采集系统正常，
    # 此时原文无更新只做中性提示（不标 ⚠️，如顺丰个股新闻稀疏属正常现象）；
    # 只有情绪表本身停更（真·采集故障）才标 ⚠️。
    # 021M：增加对空标记记录的检查——如果最新聚合记录是空标记（total_count=0），说明是"正常无数据"，
    # 不显示滞后警告。如果最新聚合记录有数据（total_count>0），即使新闻日期是昨天，也不算滞后。
    sent_row_latest = conn.execute(
        'SELECT news_date, total_count FROM news_sentiment WHERE stock_id=? ORDER BY news_date DESC LIMIT 1',
        (stock_id,),
    ).fetchone()
    news_row = conn.execute(
        "SELECT MAX(info_date) d FROM raw_sentiment WHERE stock_id=? AND info_type='news'",
        (stock_id,),
    ).fetchone()
    sent_fresh = bool(sent_row_latest and sent_row_latest['news_date'] and str(sent_row_latest['news_date']) >= str(latest_td))

    # 检查最新聚合记录的状态
    latest_has_data = bool(sent_row_latest and sent_row_latest['total_count'] > 0)
    latest_empty = bool(sent_row_latest and sent_row_latest['total_count'] == 0)

    if latest_empty:
        # 最新聚合记录是空标记，说明是"正常无数据"，不显示滞后警告
        lines.append("消息面：今日无新增新闻（采集正常）")
    elif latest_has_data:
        # 最新聚合记录有数据，即使新闻日期是昨天，也不算滞后
        if news_row and news_row['d']:
            lag = _days_between(news_row['d'], today)
            if lag is not None and lag > 7:
                # 021N：恢复 020R-57 核心语义——原文长期无更新时，
                # 仍需检查情绪表是否覆盖最新交易日（sent_fresh）：
                # 情绪新鲜 = 采集系统正常（新闻稀疏股如顺丰）→ 中性提示；
                # 情绪停更 = 真采集故障 → ⚠️（test_news_collection_stall_flag 锁定）。
                if sent_fresh:
                    lines.append(f"消息面：最近个股新闻 {news_row['d']}（近{lag}日无新消息，情绪每日更新）")
                else:
                    has_issue = True
                    lines.append(f"消息面：最新新闻 {news_row['d']}（滞后{lag}天 ⚠️）")
            else:
                lines.append(f"消息面：最新新闻 {news_row['d']}（采集正常）")
        else:
            lines.append(f"消息面：情绪至 {sent_row_latest['news_date']}（无新闻原文，情绪每日更新）")
    elif news_row and news_row['d']:
        # 没有聚合记录，检查历史新闻数据
        lag = _days_between(news_row['d'], today)
        if lag is not None and lag > 7:
            if sent_fresh:
                lines.append(f"消息面：最近个股新闻 {news_row['d']}（近{lag}日无新消息，情绪每日更新）")
            else:
                has_issue = True
                lines.append(f"消息面：最新新闻 {news_row['d']}（滞后{lag}天 ⚠️）")
        else:
            lines.append(f"消息面：最新新闻 {news_row['d']}（滞后{lag}天）")
    else:
        lines.append('消息面：缺失 ⚠️')
        has_issue = True

    # 5. 业绩预告/业绩快报（020R-50 快报并入）
    fc = conn.execute(
        'SELECT COUNT(*) n, MAX(report_period) p FROM raw_forecast WHERE stock_id=?',
        (stock_id,),
    ).fetchone()
    ex = conn.execute(
        'SELECT COUNT(*) n, MAX(report_period) p FROM raw_express WHERE stock_id=?',
        (stock_id,),
    ).fetchone()
    fc_parts = []
    if fc and fc['n']:
        fc_parts.append(f"预告 {fc['n']} 条（最新报告期 {fc['p']}）")
    if ex and ex['n']:
        fc_parts.append(f"快报 {ex['n']} 条（最新报告期 {ex['p']}）")
    if fc_parts:
        lines.append(f"业绩预期：{'；'.join(fc_parts)}")
    else:
        lines.append('业绩预期：最近三期暂无（预告/快报）')

    # 6. 020R-54：行业资金背景（所属行业当日主力资金 + 排名 + 连续方向；无匹配时静默跳过）
    try:
        irow = conn.execute('SELECT industry, market FROM stocks WHERE id=?', (stock_id,)).fetchone()
        if irow and irow['industry'] and irow['market'] != 'hk_stock':
            from modules.market_overview import get_industry_flow_bg

            bg = get_industry_flow_bg(irow['industry'])
            if bg and bg.get('main_net') is not None:
                yi = abs(bg['main_net']) / 1e8
                direction = '流入' if bg['main_net'] > 0 else '流出'
                # 021BN：streak=±1 时"连续"语义不成立，改为"今日转入"
                streak_txt = ''
                sd = bg.get('streak_days', 0)
                if sd > 1:
                    streak_txt = f"，连续流入{sd}日"
                elif sd == 1:
                    streak_txt = "，今日转入净流入"
                elif sd < -1:
                    streak_txt = f"，连续流出{abs(sd)}日"
                elif sd == -1:
                    streak_txt = "，今日转入净流出"
                # 021BN：排名为同级别板块内口径（一/二/三级分开排），二/三级显式标注，
                # 避免"一级社会服务"与"三级子行业"跨级对比的误导
                lv = bg.get('level')
                rank_txt = f"第{bg['rank']}/{bg['total']}名"
                if lv in ('Ⅱ', 'Ⅲ'):
                    rank_txt = f"{'二级' if lv == 'Ⅱ' else '三级'}板块内 {rank_txt}"
                lines.append(
                    f"行业资金背景：{bg['board']} 主力净{direction} {yi:.1f}亿"
                    f"（{rank_txt}{streak_txt}）"
                )
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[020R-54] 行业资金背景读取失败 stock_id={stock_id}: {e}')

    conn.close()
    return {'lines': lines, 'has_issue': has_issue}


def _has_collection_today(stock_id, target_date):
    """020R-58：该股当日是否有任何采集记录（data_status 当日行）。

    异常时按 False（无采集）处理——宁可多采集一次也不静默缺数据。
    """
    try:
        conn = get_connection()
        row = conn.execute(
            'SELECT 1 FROM data_status WHERE stock_id=? AND fetched_at LIKE ? LIMIT 1',
            (stock_id, f'{target_date}%'),
        ).fetchone()
        conn.close()
        return bool(row)
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[020R-58] 采集记录检查异常 stock_id={stock_id}: {e}')
        return False
