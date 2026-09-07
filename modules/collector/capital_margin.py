"""资金面·两融余额（沪深交易所）。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
from datetime import datetime, timedelta
from typing import Any

import akshare as ak
import pandas as pd

from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.symbols_status import get_stock_id

# ============================================================
# DATASRC-C：融资余额采集（仅融资融券标的）
# 数据源：akshare stock_margin_detail_sse / stock_margin_detail_szse
# 字段映射：融资余额(元) -> margin_balance(万元)
# 覆盖范围：仅融资融券标的（约2200只），非标的填 None
# 采集频率：每日1次（T+1 公布）
# ============================================================

# 融资融券数据缓存（按日期缓存全市场数据，避免逐只重复请求）
_MARGIN_CACHE: dict[str, dict[str, Any]] = {'sse': {}, 'szse': {}}  # {date_str: DataFrame}


def _fetch_margin_data_sse(date_str):
    """获取上交所融资融券数据（带缓存）"""
    if date_str in _MARGIN_CACHE['sse']:
        return _MARGIN_CACHE['sse'][date_str]
    try:
        df = ak.stock_margin_detail_sse(date=date_str)
        _MARGIN_CACHE['sse'][date_str] = df
        return df
    except Exception as e:
        logger.debug(f'[DATASRC-C] 上交所融资融券数据获取跳过({date_str}): {e}')
        _MARGIN_CACHE['sse'][date_str] = None
        return None


def _fetch_margin_data_szse(date_str):
    """获取深交所融资融券数据（带缓存）"""
    if date_str in _MARGIN_CACHE['szse']:
        return _MARGIN_CACHE['szse'][date_str]
    try:
        df = ak.stock_margin_detail_szse(date=date_str)
        _MARGIN_CACHE['szse'][date_str] = df
        return df
    except Exception as e:
        logger.debug(f'[DATASRC-C] 深交所融资融券数据获取跳过({date_str}): {e}')
        _MARGIN_CACHE['szse'][date_str] = None
        return None


def fetch_margin_balance(symbol, market, force_full=False):
    """
    采集融资余额数据（DATASRC-C 子任务2.2）。
    仅对融资融券标的有效，非标的直接跳过（填 None）。
    使用 UPDATE 写入 raw_capital_flow.margin_balance，不破坏已有字段。
    融资余额为 T+1 公布，采集最近两个交易日数据以支持 data_adapter 计算日变化。
    011增量：有数据时仅补近期1-15天；无数据时保持全量回填。
    失败时不阻塞主流程，仅记录 warning。

    Returns: (status, message)
    """
    if market != 'a_stock':
        # 融资融券仅 A 股
        return 'skipped', '融资融券仅A股标的'

    stock_id = get_stock_id(symbol, 'a_stock')
    if not stock_id:
        return 'failed', f'数据库中未找到A股 {symbol}'

    try:
        logger.info(f'[DATASRC-C] 融资余额采集: {symbol}')

        # 确定交易所（6开头=上交所，0/3开头=深交所）
        is_sse = symbol.startswith('6') or symbol.startswith('9')

        # 011增量：确定起始日期范围
        today = datetime.now(_CN_TZ).replace(tzinfo=None)
        if not force_full:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT MAX(trade_date) as last_margin_date FROM raw_capital_flow
                   WHERE stock_id = ? AND margin_balance IS NOT NULL""",
                (stock_id,),
            )
            chk_row = cursor_chk.fetchone()
            conn_chk.close()
            if chk_row and chk_row['last_margin_date']:
                last_margin = datetime.strptime(str(chk_row['last_margin_date'])[:10], '%Y-%m-%d')
                # 从上次有数据的次日开始，仅补近期（上限15天防止遗漏）
                days_to_try_max = min(15, (today - last_margin).days + 2)
                if days_to_try_max <= 0:
                    return 'skipped', '融资余额已是最新'
            else:
                days_to_try_max = 159  # 无数据时保持全量
        else:
            days_to_try_max = 159  # force_full时全量

        dates_to_try = []
        for delta in range(1, days_to_try_max + 1):
            d = today - timedelta(days=delta)
            # 跳过周末
            if d.weekday() < 5:
                dates_to_try.append(d.strftime('%Y%m%d'))

        updated_count = 0
        _no_match_count = 0  # B26：连续3个日期无匹配才判定为非标的
        for date_str in dates_to_try:
            if is_sse:
                df = _fetch_margin_data_sse(date_str)
            else:
                df = _fetch_margin_data_szse(date_str)

            if df is None or df.empty:
                continue

            # 在 DataFrame 中查找该股票
            # 上交所列名：证券代码 / 融资余额(元)
            # 深交所列名：证券代码 / 融资余额(元)
            code_col = None
            balance_col = None
            for col in df.columns:
                col_str = str(col)
                if '代码' in col_str:
                    code_col = col
                if '融资余额' in col_str:
                    balance_col = col

            if code_col is None or balance_col is None:
                logger.warning(f'[DATASRC-C] 融资融券列名无法识别: {list(df.columns)}')
                break

            # 匹配股票代码（代码列可能为 int 或 str）
            df_match = df[df[code_col].astype(str).str.zfill(6) == symbol]
            if df_match.empty:
                # B26：连续3个日期均无匹配才判定为非标的（容错单日接口异常）
                _no_match_count += 1
                if _no_match_count >= 3 and updated_count == 0:
                    logger.info(f'[DATASRC-C] {symbol} 非融资融券标的，跳过')
                    return 'skipped', '非融资融券标的（连续3个日期无数据）'
                if _no_match_count >= 3:
                    break
                continue

            row = df_match.iloc[0]
            balance_yuan = row[balance_col]
            if pd.isna(balance_yuan):
                continue

            # 元 -> 万元
            balance_wan = round(float(balance_yuan) / 1e4, 2)

            # 格式化日期 YYYY-MM-DD
            trade_date = f'{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}'

            # UPDATE 写入
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                """
                UPDATE raw_capital_flow SET margin_balance = ?
                WHERE stock_id = ? AND trade_date = ?
            """,
                (balance_wan, stock_id, trade_date),
            )
            updated = cursor.rowcount

            if updated == 0:
                cursor.execute(
                    """
                    INSERT OR IGNORE INTO raw_capital_flow
                    (stock_id, trade_date, margin_balance)
                    VALUES (?, ?, ?)
                """,
                    (stock_id, trade_date, balance_wan),
                )
                updated = cursor.rowcount

            conn.commit()
            conn.close()
            updated_count += updated

            # B26：回填完整历史（上限150条，受日期范围限制；对齐主力资金历史天数）
            if updated_count >= 150:
                break

        if updated_count > 0:
            logger.info(f'[DATASRC-C] {symbol} 融资余额写入成功: {updated_count}条记录')
            return 'success', f'融资余额已更新({updated_count}条记录)'
        else:
            logger.info(f'[DATASRC-C] {symbol} 未找到融资融券数据')
            return 'skipped', '非融资融券标的或数据未公布'

    except Exception as e:
        logger.warning(f'[DATASRC-C] {symbol} 融资余额采集失败(不阻塞): {e}')
        return 'failed', f'融资余额采集失败: {e}'
