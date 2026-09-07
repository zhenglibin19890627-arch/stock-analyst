"""港股基本面 + 南向资金快照。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
from datetime import datetime

import akshare as ak
import pandas as pd

from config import FUNDAMENTAL_REPORT_TTL_DAYS
from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.http_client import retry
from modules.collector.symbols_status import _normalize_hk_symbol, get_stock_id, save_data_status
from modules.collector.valuation import _fetch_valuation_tencent

# ============================================================
# 020R-47：南向资金（港股通）大盘快照采集（仅展示参考，不参评）
# 数据源：akshare stock_hsgt_hist_em(symbol='南向资金')——实测仍正常更新。
# ============================================================


def _num_or_none_flow(v):
    try:
        if v is None or v == '-' or v == '':
            return None
        if pd.isna(v):
            return None
        return round(float(v), 2)
    except (TypeError, ValueError):
        return None


def fetch_south_flow_snapshot():
    """020R-47：抓取南向资金最新一行 → dict；失败返回 None。

    单位：当日净买/买卖额=亿元；持股市值换算为万亿。
    """
    try:
        df = ak.stock_hsgt_hist_em(symbol='南向资金')
        if df is None or df.empty:
            return None
        row = df.iloc[-1]
        hold_mv = _num_or_none_flow(row.get('持股市值'))
        return {
            'trade_date': str(row.get('日期'))[:10],
            'net_buy': _num_or_none_flow(row.get('当日成交净买额')),
            'buy_amount': _num_or_none_flow(row.get('买入成交额')),
            'sell_amount': _num_or_none_flow(row.get('卖出成交额')),
            'cumulative_net': _num_or_none_flow(row.get('历史累计净买额')),
            'hold_market_value': round(hold_mv / 1e12, 2) if hold_mv is not None else None,
        }
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[020R-47 南向资金] 抓取失败(静默降级): {e}')
        return None


# ============================================================
# 港股 —— 基本面数据
# ============================================================


@retry
def _fetch_hk_fundamental_em(symbol):
    """通过akshare获取港股财务指标，需传入5位数字代码"""
    hk_code = _normalize_hk_symbol(symbol)
    logger.info(f'港股财务指标请求: symbol={symbol} -> hk_code={hk_code}')
    df = ak.stock_financial_hk_analysis_indicator_em(symbol=hk_code, indicator='年度')
    return df


def fetch_hk_fundamental(symbol, force_full=False):
    """采集港股基本面数据
    011增量：80天财报TTL（港股无独立PE/PB门控，与财报一并采集）。
    """
    stock_id = get_stock_id(symbol, 'hk_stock')
    if not stock_id:
        return 'failed', f'数据库中未找到港股 {symbol}'

    # 011增量：80天财报门控
    # 019P-A6：附加完整性检查（最新期毛利率缺失 → 自动回补）；占位行修复（M-5）为收敛前提
    backfill_triggered = False
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                'SELECT MAX(report_date) as last_report FROM raw_fundamental WHERE stock_id = ?',
                (stock_id,),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['last_report']:
                last_report_date = str(row['last_report'])[:10]
                days_since = (
                    datetime.now(_CN_TZ).replace(tzinfo=None)
                    - datetime.strptime(last_report_date, '%Y-%m-%d')
                ).days
                if days_since < FUNDAMENTAL_REPORT_TTL_DAYS:
                    # 019P-A6：完整性检查——最新真实财报行毛利率缺失 → 回补（M-5 清理占位行后收敛）
                    conn_chk2 = get_connection()
                    cur_chk2 = conn_chk2.cursor()
                    cur_chk2.execute(
                        'SELECT gross_margin FROM raw_fundamental WHERE stock_id = ? AND report_date = ?',
                        (stock_id, last_report_date),
                    )
                    gm_row = cur_chk2.fetchone()
                    conn_chk2.close()
                    if gm_row is not None and gm_row['gross_margin'] is None:
                        backfill_triggered = True
                        logger.info(
                            f'[港股 {symbol}] 最新期({last_report_date})毛利率缺失，触发财报补全'
                        )
                    else:
                        skip_msg = f'同日跳过(港股财报{days_since}天内)'
                        save_data_status(stock_id, 'fundamental', 'success', skip_msg)
                        return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[港股 {symbol}] 基本面增量检查异常(降级为全量): {e}')

    warnings = []
    saved_count = 0

    try:
        df_fin = _fetch_hk_fundamental_em(symbol)
        if df_fin is not None and not df_fin.empty:
            conn = get_connection()
            cursor = conn.cursor()

            for idx in range(min(3, len(df_fin))):
                row = df_fin.iloc[idx]

                def safe_get(r, *keys):
                    for k in keys:
                        if k in r.index:
                            val = r[k]
                            if pd.notna(val):
                                try:
                                    return float(val)
                                except (ValueError, TypeError):
                                    return None
                    return None

                # P0-HK-FUND-002：akshare>=1.18 后港股财务指标列名由中文漂移为英文，
                # safe_get 兼容新旧两套 key（英文优先，中文兜底）。
                report_date = str(
                    row.get('REPORT_DATE', row.get('日期', row.get('DATA_DATE', '')))
                ).split(' ')[0]

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_fundamental
                    (stock_id, report_date,
                     roe, gross_margin, net_margin, debt_ratio,
                     current_ratio, revenue_growth, profit_growth,
                     ocf_to_net_profit, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        report_date,
                        safe_get(row, 'ROE_AVG', '净资产收益率(%)', '净资产收益率（%）'),
                        safe_get(row, 'GROSS_PROFIT_RATIO', '销售毛利率(%)', '毛利率（%）'),
                        safe_get(row, 'NET_PROFIT_RATIO', '销售净利率(%)', '净利率（%）'),
                        safe_get(row, 'DEBT_ASSET_RATIO', '资产负债率(%)', '资产负债率（%）'),
                        safe_get(row, 'CURRENT_RATIO', '流动比率', '流动比率（倍）'),
                        safe_get(
                            row, 'OPERATE_INCOME_YOY', '营业收入增长率(%)', '营业收入同比增长（%）'
                        ),
                        safe_get(
                            row, 'HOLDER_PROFIT_YOY', '净利润增长率(%)', '净利润同比增长（%）'
                        ),
                        None,  # ocf_to_net_profit：指标接口无直接对应字段，留空降级（不引入估算）
                        'em_hk',  # 019P-A3：港股财报来源标注（东方财富 EM）
                    ),
                )
                saved_count += 1

            conn.commit()
            conn.close()

            # 019P M-5（R-3 高优先）：清理"全部指标字段 NULL 且 report_date 晚于最新真实财报行"的占位行。
            # 占位行仅含 PE/PB 时点值，真实财报行存在后无信息增量（PE/PB 下次采集即重新合并，零数据损失）。
            # 前提：仅财报写入成功时清理；同时是 A-6 自动回补收敛的前提（否则港股每日触发回补且永不收敛）。
            try:
                conn_cln = get_connection()
                cur_cln = conn_cln.cursor()
                cur_cln.execute(
                    """DELETE FROM raw_fundamental WHERE stock_id = ?
                       AND roe IS NULL AND gross_margin IS NULL AND net_margin IS NULL
                       AND debt_ratio IS NULL AND current_ratio IS NULL AND quick_ratio IS NULL
                       AND revenue_growth IS NULL AND profit_growth IS NULL
                       AND ocf_to_net_profit IS NULL
                       AND report_date > (
                           SELECT MAX(report_date) FROM raw_fundamental WHERE stock_id = ?
                           AND (roe IS NOT NULL OR gross_margin IS NOT NULL
                                OR net_margin IS NOT NULL OR debt_ratio IS NOT NULL
                                OR current_ratio IS NOT NULL OR revenue_growth IS NOT NULL
                                OR profit_growth IS NOT NULL OR ocf_to_net_profit IS NOT NULL)
                       )""",
                    (stock_id, stock_id),
                )
                cleaned = cur_cln.rowcount
                conn_cln.commit()
                conn_cln.close()
                if cleaned:
                    logger.info(f'[港股 {symbol}] 清理 PE/PB 占位行 {cleaned} 条（全指标NULL）')
            except Exception as e:
                logger.warning(f'[港股 {symbol}] 占位行清理异常(不阻塞): {e}')
        else:
            warnings.append('港股财务指标数据为空')
    except Exception as e:
        warnings.append(f'港股财务指标获取失败: {e}')
        logger.warning(f'[港股 {symbol}] 财务指标获取失败: {e}')

    # --- 估值指标 PE/PB（腾讯实时行情接口）---
    try:
        result = _fetch_valuation_tencent(symbol, 'hk_stock')
        if result is None:
            warnings.append('PE/PB获取失败（腾讯接口无响应）')
        else:
            pe, pb, _mv = result
            if pe is not None or pb is not None:
                conn2 = get_connection()
                cursor2 = conn2.cursor()
                # P0-HK-FUND-002：PE/PB 合并到最新财报行（对齐 A 股逻辑），
                # 不再 INSERT 新行，避免 data_adapter 只读到 PE/PB 而丢财报指标。
                # 019P M-5：改为取最新"含指标值"的真实财报行（排除全指标 NULL 的 PE/PB 占位行 R-3）
                cursor2.execute(
                    """SELECT report_date FROM raw_fundamental WHERE stock_id = ?
                       AND (roe IS NOT NULL OR gross_margin IS NOT NULL
                            OR net_margin IS NOT NULL OR debt_ratio IS NOT NULL
                            OR current_ratio IS NOT NULL OR revenue_growth IS NOT NULL
                            OR profit_growth IS NOT NULL OR ocf_to_net_profit IS NOT NULL)
                       ORDER BY report_date DESC LIMIT 1""",
                    (stock_id,),
                )
                latest_fund_row = cursor2.fetchone()
                if latest_fund_row:
                    cursor2.execute(
                        """
                        UPDATE raw_fundamental SET pe_ratio = ?, pb_ratio = ?
                        WHERE stock_id = ? AND report_date = ?
                    """,
                        (pe, pb, stock_id, latest_fund_row['report_date']),
                    )
                    logger.info(
                        f'[港股 {symbol}] PE/PB 已合并到财报 {latest_fund_row["report_date"]}: PE={pe}, PB={pb}'
                    )
                else:
                    # 无财报记录时才创建新行
                    today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                    cursor2.execute(
                        """
                        INSERT OR REPLACE INTO raw_fundamental
                        (stock_id, report_date, pe_ratio, pb_ratio)
                        VALUES (?, ?, ?, ?)
                    """,
                        (stock_id, today, pe, pb),
                    )
                    logger.info(f'[港股 {symbol}] 估值数据(无财报行,新建): PE={pe}, PB={pb}')
                conn2.commit()
                conn2.close()
            else:
                warnings.append('PE/PB数据为空')
    except Exception as e:
        warnings.append(f'PE/PB获取失败: {e}')
        logger.warning(f'[港股 {symbol}] PE/PB获取失败: {e}')

    # P0-HK-FUND-002：返回逻辑对齐 A 股三档（success/partial/failed）
    # 019P-A3：data_status message 前缀标注数据源（'港股EM财报+腾讯估值'）
    if saved_count > 0 and not warnings:
        if backfill_triggered:
            msg = '港股EM财报+腾讯估值: 财报补全(毛利率缺失触发)'
        else:
            msg = '港股EM财报+腾讯估值: 港股基本面数据采集成功'
        save_data_status(stock_id, 'fundamental', 'success', msg)
        return 'success', msg
    elif saved_count > 0 and warnings:
        save_data_status(
            stock_id,
            'fundamental',
            'partial',
            '港股EM财报+腾讯估值: 获取' + str(saved_count) + '条财务数据，缺失: ' + '; '.join(warnings),
        )
        return 'partial', f'获取{saved_count}条财务数据，缺失: ' + '; '.join(warnings)
    else:
        save_data_status(stock_id, 'fundamental', 'failed', '; '.join(warnings))
        return 'failed', '; '.join(warnings)
