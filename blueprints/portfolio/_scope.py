"""021S 多交易账户：账户域辅助函数（原 portfolio.py 区段逐字搬运）。

持仓域隔离维度 = account_id；'all'/缺省 = 全部账户（聚合视图）。
自选股/分析/预警不区分账户，保持全局共享。
"""


# ============================================================
# 021S 多交易账户：辅助函数 + 账户 CRUD
# 持仓域隔离维度 = account_id；'all'/缺省 = 全部账户（聚合视图）。
# 自选股/分析/预警不区分账户，保持全局共享。
# ============================================================


def _get_default_account_id(cursor):
    """取默认账户 id（is_default=1 优先，否则最早创建的账户）。"""
    cursor.execute('SELECT id FROM accounts WHERE is_default = 1 ORDER BY id LIMIT 1')
    row = cursor.fetchone()
    if not row:
        cursor.execute('SELECT id FROM accounts ORDER BY id LIMIT 1')
        row = cursor.fetchone()
    return row['id'] if row else None


def _parse_account_scope(raw):
    """解析账户范围参数：''/'all'/None → None(全部账户)；数字字符串 → int。

    非法值抛 ValueError，由调用方转 400。
    """
    if raw is None or raw == '' or raw == 'all':
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f'非法 account_id: {raw}')


def _account_exists(cursor, account_id):
    cursor.execute('SELECT 1 FROM accounts WHERE id = ?', (account_id,))
    return cursor.fetchone() is not None

