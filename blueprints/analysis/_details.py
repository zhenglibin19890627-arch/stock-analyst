"""四维明细域（t7 拆包）：技术面/基本面/资金面/消息面/行业资金背景 展示明细计算。

原 blueprints/analysis.py 区段逐字搬运（020R-35/37/38/39/54）；纯展示层增强，
失败或数据不足时返回 None，不影响报告主流程。无路由；供 advice 域与 facade 复用。
"""

import logging

from database.db_manager import get_connection


def _technical_detail_for_stock(stock_id):
    """020R-35/48B：计算技术指标明细（日线六类 + 周线/月线多周期，与评分引擎同口径）。

    注：本函数为纯展示层；周线/月线数据在评分引擎中已参评
    （技术面 7 子项：月线方向 25% + 周线波段 45% + 日线择时 30%）。
    失败或数据不足时返回 None，不影响报告主流程。
    """
    try:
        from modules.technical_detail import compute_technical_detail

        conn = get_connection()
        cursor = conn.cursor()
        merged = {}
        # 日线（全量）
        cursor.execute(
            'SELECT trade_date, close, high, low, volume FROM raw_kline '
            'WHERE stock_id = ? ORDER BY trade_date ASC',
            (stock_id,),
        )
        rows = cursor.fetchall()
        if len(rows) >= 20:
            merged.update(
                compute_technical_detail(
                    [float(r['close'] or 0) for r in rows],
                    [float(r['high'] or 0) for r in rows],
                    [float(r['low'] or 0) for r in rows],
                    [float(r['volume'] or 0) for r in rows],
                    latest_date=str(rows[-1]['trade_date'])[:10],
                    key_prefix='',
                    min_bars=20,
                ) or {}
            )
        # 020R-48B：周线/月线多周期（评分同口径）
        for table, prefix, min_bars in (
            ('raw_kline_weekly', 'weekly_', 20),
            ('raw_kline_monthly', 'monthly_', 5),
        ):
            try:
                cursor.execute(
                    f'SELECT trade_date, close, high, low, volume FROM {table} '
                    'WHERE stock_id = ? ORDER BY trade_date ASC',
                    (stock_id,),
                )
                prows = cursor.fetchall()
                if len(prows) >= min_bars:
                    merged.update(
                        compute_technical_detail(
                            [float(r['close'] or 0) for r in prows],
                            [float(r['high'] or 0) for r in prows],
                            [float(r['low'] or 0) for r in prows],
                            [float(r['volume'] or 0) for r in prows],
                            latest_date=str(prows[-1]['trade_date'])[:10],
                            key_prefix=prefix,
                            min_bars=min_bars,
                        ) or {}
                    )
            except Exception as e:  # noqa: BLE001 —— 表缺失/字段漂移时跳过该周期
                logging.getLogger(__name__).warning(
                    f'多周期指标计算失败 stock_id={stock_id} table={table}: {e}'
                )
        conn.close()

        # 021S：技术面打分子项（月线方向/周线波段/日线择时 7 子项得分+权重+明细）。
        # 复用评分引擎同口径计算（只读调用，不改引擎）；失败静默降级——
        # 前端退回仅显示指标读数（旧形态），不影响报告主流程。
        try:
            from modules.data_adapter import load_stockdata_from_db
            from modules.scoring_engine import TECHNICAL_SUBITEMS, score_dimension

            sd = load_stockdata_from_db(stock_id)
            if sd is not None:
                sub_score, sub_detail = score_dimension(sd, TECHNICAL_SUBITEMS, 'technical')
                if sub_detail.get('subitems'):
                    # 与 analyze() 同口径的月线空头惩罚标记（仅展示文案，分值不重复收缩）
                    if (
                        sub_score is not None
                        and sd.monthly_ma5 is not None
                        and sd.monthly_ma10 is not None
                        and sd.monthly_ma5 < sd.monthly_ma10
                    ):
                        sub_detail['monthly_penalty'] = '月线空头（MA5 低于 MA10），技术面得分 ×0.85'
                    merged['scoring_score'] = sub_score
                    merged['scoring_subitems'] = sub_detail['subitems']
                    if sub_detail.get('monthly_penalty'):
                        merged['monthly_penalty'] = sub_detail['monthly_penalty']
        except Exception as e:  # noqa: BLE001 —— 子项得分属增强展示，失败不阻塞
            logging.getLogger(__name__).warning(
                f'技术面打分子项计算失败 stock_id={stock_id}: {e}'
            )

        return merged if merged else None
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'技术指标明细计算失败 stock_id={stock_id}: {e}')
        return None


def _fundamental_detail_for_stock(stock_id):
    """020R-37/49/50：读取 raw_fundamental 最新一期 + 最新业绩预期（快报优先于预告），
    计算基本面五类子项展示明细。

    纯展示层增强：失败或数据不足时返回 None，不影响报告主流程。
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT report_date, pe_ratio, pb_ratio, roe, gross_margin, '
            'revenue_growth, profit_growth, ocf_to_net_profit, debt_ratio, current_ratio '
            'FROM raw_fundamental WHERE stock_id = ? ORDER BY report_date DESC LIMIT 1',
            (stock_id,),
        )
        row = cursor.fetchone()
        conn.close()
        # 020R-49/50：与评分同一套取用逻辑（get_latest_forecast_info），保证展示与打分一致
        fund_period = (
            str(row['report_date'])[:10].replace('-', '') if row and row['report_date'] else None
        )
        from modules.data_adapter import get_latest_forecast_info

        fc_info = get_latest_forecast_info(stock_id, fund_period)
        if not row and not fc_info:
            return None

        from modules.fundamental_detail import compute_fundamental_detail

        return compute_fundamental_detail(dict(row) if row else None, fc_info)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'基本面指标明细计算失败 stock_id={stock_id}: {e}')
        return None


def _capital_detail_for_stock(stock_id):
    """020R-38/45/47：读取 raw_capital_flow + holder_structure（+港股南向参考），计算资金面子项展示明细。

    纯展示层增强：失败或数据不足时返回 None，不影响报告主流程。
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT market FROM stocks WHERE id = ?', (stock_id,))
        mrow = cursor.fetchone()
        is_hk = bool(mrow and mrow['market'] == 'hk_stock')
        cursor.execute(
            'SELECT trade_date, main_net_inflow, north_holding_change, margin_balance, '
            'south_net_buy, south_hold_ratio '
            'FROM raw_capital_flow WHERE stock_id = ? ORDER BY trade_date ASC',
            (stock_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        # 020R-45：股东人数/机构持仓最新一期（021Q：含港股机构股东数量）
        cursor.execute(
            'SELECT stat_date, holder_count, holder_count_change_pct, total_shares, '
            'inst_shares, inst_ratio, inst_report_date, inst_count, inst_count_change_pct '
            'FROM holder_structure '
            'WHERE stock_id = ? ORDER BY stat_date DESC LIMIT 1',
            (stock_id,),
        )
        hs_row = cursor.fetchone()
        conn.close()

        south_flow = None
        if is_hk:
            # 020R-47：港股展示南向资金大盘参考（不参评）
            try:
                from modules.south_flow import get_latest_south_flow

                south_flow = get_latest_south_flow()
            except Exception as e:  # noqa: BLE001
                logging.getLogger(__name__).warning(f'南向资金快照读取失败 stock_id={stock_id}: {e}')

        if not rows and not hs_row and not south_flow:
            return None

        from modules.capital_detail import compute_capital_detail

        return compute_capital_detail(rows, dict(hs_row) if hs_row else None, south_flow)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'资金面指标明细计算失败 stock_id={stock_id}: {e}')
        return None


def _industry_flow_bg_for_stock(stock_id):
    """020R-54：个股所属行业资金背景（市场行情数据关联）。

    纯展示层增强：港股/无行业/板块未匹配时返回 None，不影响建议主流程。
    """
    try:
        conn = get_connection()
        row = conn.execute('SELECT industry, market FROM stocks WHERE id = ?', (stock_id,)).fetchone()
        conn.close()
        if not row or not row['industry'] or row['market'] == 'hk_stock':
            return None
        from modules.market_overview import get_industry_flow_bg

        return get_industry_flow_bg(row['industry'])
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'行业资金背景读取失败 stock_id={stock_id}: {e}')
        return None


def _news_detail_for_stock(stock_id):
    """020R-39：读取 news_sentiment 最新聚合 + 股东增持标志，计算消息面两个子项展示明细。

    纯展示层增强：失败或数据不足时返回 None，不影响报告主流程。
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT news_date, avg_sentiment, positive_count, negative_count, neutral_count, '
            'total_count, top_news_title FROM news_sentiment WHERE stock_id = ? '
            'ORDER BY news_date DESC LIMIT 1',
            (stock_id,),
        )
        news_row = cursor.fetchone()
        # 股东增持标志：与 data_adapter 一致，向后搜索最近非空值
        cursor.execute(
            'SELECT holder_increase FROM raw_fundamental WHERE stock_id = ? '
            'ORDER BY report_date DESC LIMIT 20',
            (stock_id,),
        )
        holder = None
        for r in cursor.fetchall():
            if r['holder_increase'] is not None:
                holder = bool(r['holder_increase'])
                break
        conn.close()
        if not news_row and holder is None:
            return None

        from modules.news_detail import compute_news_detail

        return compute_news_detail(dict(news_row) if news_row else None, holder)
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'消息面指标明细计算失败 stock_id={stock_id}: {e}')
        return None
