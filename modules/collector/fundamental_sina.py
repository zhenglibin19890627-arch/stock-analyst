"""基本面：新浪抽象财报/指标 + 详情装配。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
from datetime import datetime

import akshare as ak
import pandas as pd

from config import FUNDAMENTAL_REPORT_TTL_DAYS, PE_PB_CACHE_TTL_HOURS
from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.capital_em import _safe_num
from modules.collector.http_client import _call_ak_with_timeout, retry
from modules.collector.symbols_status import get_stock_id, save_data_status
from modules.collector.valuation import _fetch_valuation_tencent, fetch_fundamental_baostock

# ============================================================
# A股 —— 基本面数据
# ============================================================

# ============================================================
# 019P：新浪关键指标摘要（stock_financial_abstract）主源解析
# 结构（架构师实测 M-2，无需转置）：行=指标（80），列=['选项','指标']+报告期列（最新在前）
# ============================================================
_FUND_ABSTRACT_TIMEOUT = 30  # 019P：abstract 调用超时阈值（秒），实测单次约 1s

# 019P R-1：abstract 同名指标去重优先级（选项组：常用指标优先 + 取第一行）
_ABSTRACT_GROUP_PRIORITY = [
    '常用指标',
    '每股指标',
    '盈利能力',
    '成长能力',
    '收益质量',
    '财务风险',
    '营运能力',
]

# 019P M-2：abstract 指标行名 → raw_fundamental 列 映射表（架构师实测行名）
_ABSTRACT_FIELD_MAP = [
    ('毛利率', 'gross_margin'),
    ('销售净利率', 'net_margin'),
    ('营业总收入增长率', 'revenue_growth'),
    ('归属母公司净利润增长率', 'profit_growth'),
    ('净资产收益率(ROE)', 'roe'),
    ('资产负债率', 'debt_ratio'),
    ('流动比率', 'current_ratio'),
    ('速动比率', 'quick_ratio'),
    ('经营活动净现金/归属母公司的净利润', 'ocf_to_net_profit'),
]


# OPT-3：_call_ak_with_timeout 随 HTTP 工具族归入 http_client.py（valuation/fundamental 两域共用，断循环导入）


def _fetch_a_fundamental_sina(symbol):
    """019P：主源切换——akshare 新浪关键指标摘要 stock_financial_abstract（M-2/A-1）。
    返回原始 DataFrame（行=指标，列=['选项','指标']+报告期列最新在前）。
    P3 超时保护；异常/超时向上抛出，由 fetch_a_fundamental 降级现接口（P2）。
    注：不挂 @retry——abstract 失败即降级（P2），避免批量场景超时重试累积（最坏 3×30s 超批次上限）。
    """
    df, timed_out = _call_ak_with_timeout(
        lambda: ak.stock_financial_abstract(symbol=symbol), f'A股 {symbol} abstract'
    )
    if timed_out:
        raise TimeoutError(f'stock_financial_abstract 超时(>{_FUND_ABSTRACT_TIMEOUT}s)')
    return df


@retry
def _fetch_a_fundamental_sina_indicator(symbol):
    """019P P2：降级层——现接口 stock_financial_analysis_indicator（保留原路径）。
    P3 超时保护；异常/超时向上抛出由 fetch_a_fundamental 处理。
    """
    df, timed_out = _call_ak_with_timeout(
        lambda: ak.stock_financial_analysis_indicator(symbol=symbol, start_year='2020'),
        f'A股 {symbol} analysis_indicator',
    )
    if timed_out:
        raise TimeoutError(f'stock_financial_analysis_indicator 超时(>{_FUND_ABSTRACT_TIMEOUT}s)')
    return df


def _abstract_name_index(df, symbol):
    """019P R-1：按指标名建立去重索引（选项组优先级 + 取第一行）。
    返回 {指标名: 行下标}，仅包含 _ABSTRACT_FIELD_MAP 中的指标名。
    """
    rank = {g: i for i, g in enumerate(_ABSTRACT_GROUP_PRIORITY)}
    wanted = {name for name, _ in _ABSTRACT_FIELD_MAP}
    name_to_idx = {}
    for idx, r in df.iterrows():
        name = str(r.get('指标', '')).strip()
        if name not in wanted:
            continue
        if name not in name_to_idx:
            name_to_idx[name] = idx
        else:
            cur_rank = rank.get(str(df.iloc[name_to_idx[name]].get('选项', '')).strip(), len(rank))
            new_rank = rank.get(str(r.get('选项', '')).strip(), len(rank))
            if new_rank < cur_rank:
                name_to_idx[name] = idx
    return name_to_idx


def _extract_abstract_rows(df, symbol, max_periods=8):
    """019P M-2/R-2：abstract DataFrame → 最近 max_periods 期财报行。
    按报告期列遍历（最新在前，R-2 修正）；20260331→2026-03-31；
    写最近 8 期（2 年，否决全历史 100+ 期防 UI 膨胀 R-8）。
    返回 [(report_date, {db_col: value_or_None}), ...] 最新在前。
    """
    if df is None or df.empty:
        return []
    name_to_idx = _abstract_name_index(df, symbol)
    if not name_to_idx:
        logger.warning(f'[019P {symbol}] abstract 未匹配到指标行，实际列: {list(df.columns)[:5]}')
        return []
    period_cols = [c for c in df.columns if isinstance(c, str) and len(c) == 8 and c.isdigit()][
        :max_periods
    ]
    if not period_cols:
        logger.warning(f'[019P {symbol}] abstract 未识别到报告期列: {list(df.columns)[:5]}')
        return []
    rows = []
    for pcol in period_cols:
        report_date = f'{pcol[:4]}-{pcol[4:6]}-{pcol[6:8]}'
        vals = {}
        for name, db_col in _ABSTRACT_FIELD_MAP:
            idx = name_to_idx.get(name)
            vals[db_col] = _safe_num(df.iloc[idx][pcol]) if idx is not None else None
        rows.append((report_date, vals))
    return rows


def _safe_row_val(r, *keys):
    """019P：DataFrame 行安全取数（沿用原 safe_get 语义，模块级化供降级层复用）"""
    for k in keys:
        if k in r.index:
            val = r[k]
            if pd.notna(val):
                try:
                    return float(val)
                except (ValueError, TypeError):
                    return None
    return None


def _extract_indicator_rows(df, max_periods=4):
    """019P P2：降级层 analysis_indicator DataFrame → 最近 max_periods 期行（旧逻辑保留）。
    返回 [(report_date, {db_col: value_or_None}), ...] 最新在前。
    """
    rows = []
    total_rows = len(df)
    take_count = min(max_periods, total_rows)
    for idx in range(total_rows - 1, total_rows - 1 - take_count, -1):
        row = df.iloc[idx]
        report_date = str(row.get('日期', '')).split(' ')[0]
        vals = {
            'roe': _safe_row_val(row, '净资产收益率(%)', '加权净资产收益率(%)'),
            'gross_margin': _safe_row_val(row, '销售毛利率(%)'),
            'net_margin': _safe_row_val(row, '销售净利率(%)'),
            'debt_ratio': _safe_row_val(row, '资产负债率(%)'),
            'current_ratio': _safe_row_val(row, '流动比率'),
            'quick_ratio': _safe_row_val(row, '速动比率'),
            'revenue_growth': _safe_row_val(row, '主营业务收入增长率(%)'),
            'profit_growth': _safe_row_val(row, '净利润增长率(%)'),
            'ocf_to_net_profit': _safe_row_val(
                row, '经营现金净流量对净利润的比率(%)', '经营现金净流量与净利润的比率(%)'
            ),
        }
        rows.append((report_date, vals))
    return rows


def fetch_a_fundamental(symbol, force_full=False):
    """采集A股基本面数据：财务指标 + PE/PB估值
    011增量：80天财报TTL + 24h PE/PB TTL，双门控独立。
    """
    stock_id = get_stock_id(symbol, 'a_stock')
    if not stock_id:
        return 'failed', f'数据库中未找到A股 {symbol}'

    # 011增量门控
    skip_financial = False  # 是否跳过财报采集
    skip_pepb = False  # 是否跳过PE/PB采集
    # 019P-A6：存量自动回补标记（最新期毛利率缺失触发，message 区分 R-4）
    backfill_triggered = False

    if not force_full:
        # 门控A：财报数据TTL（80天）
        # 019P-A6：TTL 门控内附加完整性检查——最新一期 gross_margin IS NULL → 不跳过财报采集
        # （"不重复获取"= 已有完整数据不重复获取；完整性缺失时获取缺项不构成重复，B10 先例精神）
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                'SELECT MAX(report_date) as last_report FROM raw_fundamental WHERE stock_id = ?',
                (stock_id,),
            )
            row = cursor_chk.fetchone()
            if row and row['last_report']:
                last_report_date = str(row['last_report'])[:10]
                days_since = (
                    datetime.now(_CN_TZ).replace(tzinfo=None)
                    - datetime.strptime(last_report_date, '%Y-%m-%d')
                ).days
                if days_since < FUNDAMENTAL_REPORT_TTL_DAYS:
                    # 019P-A6：完整性检查（最新期毛利率缺失 → 自动回补，不跳过）
                    cursor_chk.execute(
                        'SELECT gross_margin FROM raw_fundamental WHERE stock_id = ? AND report_date = ?',
                        (stock_id, last_report_date),
                    )
                    gm_row = cursor_chk.fetchone()
                    if gm_row is not None and gm_row['gross_margin'] is None:
                        backfill_triggered = True
                        logger.info(
                            f'[A股 {symbol}] 最新期({last_report_date})毛利率缺失，触发财报补全（abstract 重采）'
                        )
                    else:
                        skip_financial = True
                        logger.info(f'[A股 {symbol}] 财报数据{days_since}天内，跳过财报采集')

                    # 门控B：PE/PB TTL（24h），仅当财报跳过时检查
                    if skip_financial:
                        cursor_chk.execute(
                            """SELECT fetched_at FROM data_status
                               WHERE stock_id = ? AND dimension = 'fundamental'
                               ORDER BY fetched_at DESC LIMIT 1""",
                            (stock_id,),
                        )
                        status_row = cursor_chk.fetchone()
                        if status_row and status_row['fetched_at']:
                            last_fetch = datetime.strptime(
                                str(status_row['fetched_at'])[:19], '%Y-%m-%d %H:%M:%S'
                            )
                            hours_since = (
                                datetime.now(_CN_TZ).replace(tzinfo=None) - last_fetch
                            ).total_seconds() / 3600
                            if hours_since < PE_PB_CACHE_TTL_HOURS:
                                skip_pepb = True
            conn_chk.close()
        except Exception as e:
            logger.warning(f'[A股 {symbol}] 基本面增量检查异常(降级为全量): {e}')

    # 两门控都跳过 → 整体跳过
    if skip_financial and skip_pepb:
        skip_msg = '同日跳过(财报80天TTL内+PE/PB 24h内)'
        save_data_status(stock_id, 'fundamental', 'success', skip_msg)
        return 'success', skip_msg

    warnings = []
    saved_count = 0

    # --- 财务分析指标（019P：abstract 主源，P2 失败降级 analysis_indicator）---
    # 019P-A1：主源切换 stock_financial_abstract（新浪关键指标摘要）：
    #   - 结构适配（M-2）：行=指标，列=['选项','指标']+报告期列（最新在前），无需转置
    #   - 同名指标去重（R-1）：选项=常用指标优先 + 取第一行
    #   - 写最近 8 期（2 年，防 UI 膨胀 R-8）；次新股（688795/688802）顺带解决（发现 4）
    #   - 数据源标注：'sina_abstract'；降级路径标 'sina_analysis_indicator'（A-3）
    used_abstract = False
    if not skip_financial:
        fin_rows = None
        try:
            df_abstract = _fetch_a_fundamental_sina(symbol)
            fin_rows = _extract_abstract_rows(df_abstract, symbol)
            if fin_rows:
                used_abstract = True
                logger.info(f'[A股 {symbol}] abstract 解析 {len(fin_rows)} 期财报（最新在前）')
            else:
                logger.warning(f'[A股 {symbol}] abstract 数据为空，降级现接口')
        except Exception as e:
            # P2：abstract 失败仅记日志（降级由 message 前缀标注，不重复写入 warnings 致 partial）
            logger.warning(f'[A股 {symbol}] abstract 获取失败(降级现接口): {e}')

        if fin_rows is None or not fin_rows:
            # P2 降级层：现接口 stock_financial_analysis_indicator（保留原路径）
            try:
                df_ind = _fetch_a_fundamental_sina_indicator(symbol)
                if df_ind is not None and not df_ind.empty:
                    fin_rows = _extract_indicator_rows(df_ind)
                    used_abstract = False
                else:
                    warnings.append('财务分析指标数据为空')
            except Exception as e:
                warnings.append(f'财务指标获取失败: {e}')
                logger.warning(f'[A股 {symbol}] 财务指标获取失败(降级层): {e}')

        # 019Y T2：P3 备用层——baostock 财务数据（仅A股，akshare 两层全失败时降级使用）
        used_baostock = False
        if fin_rows is None or not fin_rows:
            try:
                bs_rows = fetch_fundamental_baostock(symbol)
                if bs_rows:
                    fin_rows = bs_rows
                    used_baostock = True
                    # P3 成功时，移除 P2 层累积的失败警告（数据已由备用源补全，不误报缺失）
                    warnings = [
                        w
                        for w in warnings
                        if not w.startswith('财务指标获取失败') and w != '财务分析指标数据为空'
                    ]
                    logger.info(f'[A股 {symbol}] baostock 财务备用源成功: {len(bs_rows)} 期')
                else:
                    warnings.append('baostock财务备用源也无数据（akshare+baostock均失败）')
            except Exception as e:
                warnings.append(f'baostock财务备用源失败: {e}')
                logger.warning(f'[A股 {symbol}] baostock财务备用源失败: {e}')
        else:
            used_baostock = False

        if fin_rows:
            conn = get_connection()
            cursor = conn.cursor()
            # P1（必需）：读取既有 ocf 值，abstract 该期 ocf 为 NaN 时保留原值（回归红线）
            cursor.execute(
                'SELECT report_date, ocf_to_net_profit FROM raw_fundamental WHERE stock_id = ?',
                (stock_id,),
            )
            existing_ocf = {str(r['report_date']): r['ocf_to_net_profit'] for r in cursor.fetchall()}
            # 019Y T2：数据源标注三通道——sina_abstract / baostock / sina_analysis_indicator
            if used_abstract:
                data_source = 'sina_abstract'
            elif used_baostock:
                data_source = 'baostock'
            else:
                data_source = 'sina_analysis_indicator'
            for report_date, vals in fin_rows:
                ocf = vals.get('ocf_to_net_profit')
                if ocf is None and existing_ocf.get(report_date) is not None:
                    # P1：abstract ocf=NaN 且 DB 已有值 → 保留原值（实测 600276 20260331 场景）
                    ocf = existing_ocf[report_date]
                    logger.info(
                        f'[A股 {symbol}] {report_date} ocf 保留既有值 {ocf}（abstract 该期为 NaN）'
                    )

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_fundamental
                    (stock_id, report_date,
                     roe, gross_margin, net_margin, debt_ratio,
                     current_ratio, quick_ratio,
                     revenue_growth, profit_growth,
                     ocf_to_net_profit, data_source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        report_date,
                        vals.get('roe'),
                        vals.get('gross_margin'),
                        vals.get('net_margin'),
                        vals.get('debt_ratio'),
                        vals.get('current_ratio'),
                        vals.get('quick_ratio'),
                        vals.get('revenue_growth'),
                        vals.get('profit_growth'),
                        ocf,
                        data_source,
                    ),
                )
                saved_count += 1
                logger.info(f'[A股 {symbol}] 财报: {report_date}, ROE={vals.get("roe")}')

            conn.commit()
            conn.close()

    # --- 估值指标 PE/PB（腾讯实时行情接口）---
    # PE/PB 是实时行情数据，不单独创建记录，而是合并到最新的财报记录中
    pe_val = None
    pb_val = None
    if not skip_pepb:
        try:
            result = _fetch_valuation_tencent(symbol, 'a_stock')
            if result is None:
                warnings.append('PE/PB获取失败（腾讯接口无响应）')
            else:
                pe_val, pb_val, _mv = result
                if (pe_val is None or pe_val == 0) and (pb_val is None or pb_val == 0):
                    warnings.append('PE/PB数据为空')
                else:
                    logger.info(f'[A股 {symbol}] 估值数据: PE={pe_val}, PB={pb_val}')
        except Exception as e:
            warnings.append(f'PE/PB获取失败: {e}')
            logger.warning(f'[A股 {symbol}] PE/PB获取失败: {e}')

    # 将 PE/PB 更新到最新的财报记录中（不创建新行）
    if pe_val is not None or pb_val is not None:
        conn2 = get_connection()
        cursor2 = conn2.cursor()
        # 获取最新财报记录的 report_date
        cursor2.execute(
            'SELECT report_date FROM raw_fundamental WHERE stock_id = ? ORDER BY report_date DESC LIMIT 1',
            (stock_id,),
        )
        latest_row = cursor2.fetchone()
        if latest_row:
            cursor2.execute(
                """
                UPDATE raw_fundamental SET pe_ratio = ?, pb_ratio = ?
                WHERE stock_id = ? AND report_date = ?
            """,
                (pe_val, pb_val, stock_id, latest_row['report_date']),
            )
            conn2.commit()
            logger.info(f'[A股 {symbol}] PE/PB 已合并到财报 {latest_row["report_date"]}')
        conn2.close()

    # 011：返回逻辑调整（增加 skip_financial 仅PE/PB更新的场景）
    # 019P-A3：data_status message 前缀标注数据源（'新浪abstract财报+腾讯估值' /
    #          '新浪指标(analysis_indicator降级)+腾讯估值' / 港股见 fetch_hk_fundamental）
    if skip_financial and not skip_pepb:
        # 仅采集了PE/PB（财报跳过）
        if not warnings:
            save_data_status(stock_id, 'fundamental', 'success', '腾讯估值: PE/PB更新成功(财报跳过)')
            return 'success', '腾讯估值: PE/PB更新成功(财报跳过)'
        else:
            save_data_status(stock_id, 'fundamental', 'partial', '腾讯估值: ' + '; '.join(warnings))
            return 'partial', '腾讯估值: ' + '; '.join(warnings)
    elif saved_count > 0 and not warnings:
        # 019Y T2：三通道来源标注（abstract主源 / baostock备用 / analysis_indicator降级）
        if used_abstract:
            src_tag = '新浪abstract财报+腾讯估值'
        elif used_baostock:
            src_tag = 'baostock财务备用+腾讯估值'
        else:
            src_tag = '新浪指标(analysis_indicator降级)+腾讯估值'
        if backfill_triggered:
            # 019P-R4：回补场景 message 区分（与"同日跳过"区分）
            msg = f'{src_tag}: 财报补全(毛利率缺失触发)'
        else:
            msg = f'{src_tag}: 基本面数据采集成功'
        save_data_status(stock_id, 'fundamental', 'success', msg)
        return 'success', msg
    elif saved_count > 0 and warnings:
        if used_abstract:
            src_tag = '新浪abstract财报+腾讯估值'
        elif used_baostock:
            src_tag = 'baostock财务备用+腾讯估值'
        else:
            src_tag = '新浪指标(analysis_indicator降级)+腾讯估值'
        save_data_status(
            stock_id, 'fundamental', 'partial', f'{src_tag}: 获取{saved_count}条财务数据，缺失: ' + '; '.join(warnings)
        )
        return 'partial', f'获取{saved_count}条财务数据，缺失: ' + '; '.join(warnings)
    else:
        save_data_status(stock_id, 'fundamental', 'failed', '; '.join(warnings))
        return 'failed', '; '.join(warnings)


def fetch_fundamental_detail(symbol: str) -> dict:
    """B10: 调用 akshare 财务分析指标接口，补全基本面字段。
    作为 fetch_a_fundamental 的补充：仅更新数据库中为 NULL 的字段，不覆盖已有值。

    Returns:
        dict: {roe, gross_margin, revenue_yoy, net_profit_yoy,
               ocf_to_profit, debt_to_asset, current_ratio}
        失败时返回空 dict
    """
    result = {}
    try:
        df = ak.stock_financial_analysis_indicator(symbol=symbol, start_year='2023')
        if df is None or df.empty:
            return result
        row = df.iloc[-1]  # 取最新一期

        def _safe_float(r, *keys):
            for k in keys:
                if k in r.index:
                    val = r[k]
                    if pd.notna(val):
                        try:
                            return round(float(val), 4)
                        except (ValueError, TypeError):
                            pass
            return None

        result['roe'] = _safe_float(row, '净资产收益率(%)', '加权净资产收益率(%)')
        result['gross_margin'] = _safe_float(row, '销售毛利率(%)')
        result['revenue_yoy'] = _safe_float(row, '主营业务收入增长率(%)')
        result['net_profit_yoy'] = _safe_float(row, '净利润增长率(%)')
        result['ocf_to_profit'] = _safe_float(
            row, '经营现金净流量对净利润的比率(%)', '经营现金净流量与净利润的比率(%)'
        )
        result['debt_to_asset'] = _safe_float(row, '资产负债率(%)')
        result['current_ratio'] = _safe_float(row, '流动比率')
        # 移除 None 值
        result = {k: v for k, v in result.items() if v is not None}
        logger.info(f'[B10 基本面补全 {symbol}] 获取到 {len(result)} 个字段: {list(result.keys())}')
    except Exception as e:
        logger.warning(f'[B10 基本面补全 {symbol}] 接口失败(静默降级): {e}')
    return result


def _apply_fundamental_detail(stock_id: int, detail: dict):
    """B10: 将 fetch_fundamental_detail 的结果写入 raw_fundamental（仅填充 NULL 字段）"""
    if not detail:
        return
    # 字段映射: detail key -> DB column
    col_map = {
        'roe': 'roe',
        'gross_margin': 'gross_margin',
        'revenue_yoy': 'revenue_growth',
        'net_profit_yoy': 'profit_growth',
        'ocf_to_profit': 'ocf_to_net_profit',
        'debt_to_asset': 'debt_ratio',
        'current_ratio': 'current_ratio',
    }
    conn = get_connection()
    cursor = conn.cursor()
    # 检查是否有任何记录
    cursor.execute('SELECT COUNT(*) as cnt FROM raw_fundamental WHERE stock_id = ?', (stock_id,))
    if cursor.fetchone()['cnt'] == 0:
        conn.close()
        return
    # 仅更新当前为 NULL 的字段
    updates = []
    params = []
    for detail_key, db_col in col_map.items():
        if detail_key in detail:
            updates.append(f'{db_col} = COALESCE({db_col}, ?)')
            params.append(detail[detail_key])
    if updates:
        params.append(stock_id)
        sql = f'UPDATE raw_fundamental SET {", ".join(updates)} WHERE stock_id = ?'
        cursor.execute(sql, params)
        conn.commit()
        logger.info(f'[B10 基本面补全] stock_id={stock_id} 更新了 {len(updates)} 个字段')
    conn.close()
