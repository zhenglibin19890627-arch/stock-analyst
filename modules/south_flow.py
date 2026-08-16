"""南向资金（港股通）大盘快照模块（020R-47，仅展示参考，不参与评分）。

数据获取在 data_collector.fetch_south_flow_snapshot（akshare 调用，符合 R8 数据源解耦），
本模块只做 SQLite 缓存与新鲜度管理：超过 REFRESH_INTERVAL_HOURS 时尝试实时刷新。
用途：港股个股资金面卡片「南向资金（参考）」行。
"""

import logging
from datetime import datetime, timedelta, timezone

from database.db_manager import get_connection

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

# 快照新鲜度阈值（小时）：超过则下次读取时尝试实时刷新
REFRESH_INTERVAL_HOURS = 2


def _save(snap):
    conn = get_connection()
    try:
        conn.execute(
            'INSERT OR REPLACE INTO south_fund_flow '
            '(trade_date, net_buy, buy_amount, sell_amount, cumulative_net, hold_market_value) '
            'VALUES (?,?,?,?,?,?)',
            (
                snap['trade_date'], snap.get('net_buy'), snap.get('buy_amount'),
                snap.get('sell_amount'), snap.get('cumulative_net'), snap.get('hold_market_value'),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def get_latest_south_flow(force=False):
    """读库最新快照；超过 REFRESH_INTERVAL_HOURS 或 force 时尝试实时刷新。

    返回 dict；全程无数据且刷新失败时返回 None。
    """
    conn = get_connection()
    row = conn.execute(
        'SELECT trade_date, net_buy, buy_amount, sell_amount, cumulative_net, '
        'hold_market_value, fetched_at FROM south_fund_flow '
        'ORDER BY trade_date DESC LIMIT 1'
    ).fetchone()
    conn.close()

    need_refresh = force or row is None
    if row is not None:
        try:
            fetched = datetime.strptime(str(row['fetched_at'])[:19], '%Y-%m-%d %H:%M:%S')
            age_hours = (datetime.now() - fetched).total_seconds() / 3600
            if age_hours >= REFRESH_INTERVAL_HOURS:
                need_refresh = True
        except (ValueError, TypeError):
            need_refresh = True

    if need_refresh:
        try:
            from modules.data_collector import fetch_south_flow_snapshot
        except Exception as e:  # noqa: BLE001
            logger.warning(f'[020R-47 南向资金] 导入采集函数失败: {e}')
            fetch_south_flow_snapshot = None
        if fetch_south_flow_snapshot is not None:
            snap = fetch_south_flow_snapshot()
            if snap and snap.get('trade_date'):
                _save(snap)
                return snap
        # 刷新失败：回退已有快照
        if row is not None:
            return dict(row)

    if row is not None:
        return dict(row)
    return None
