"""代码规整与状态记录：get_stock_id/save_data_status/_log_error_to_db/港股规范。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
from database.db_manager import get_connection
from modules.collector._env import now_cn


def get_stock_id(symbol, market):
    """根据股票代码和市场，从数据库获取 stock_id"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM stocks WHERE symbol = ? AND market = ?', (symbol, market))
    row = cursor.fetchone()
    conn.close()
    return row['id'] if row else None


def save_data_status(stock_id, dimension, status, message=''):
    """记录数据采集状态到数据库（使用北京时间时间戳）。
    011优化：同维度同日只保留最新一条（先删后插），避免 data_status 无限增长。
    """
    ts = now_cn()
    today_prefix = ts[:10]  # YYYY-MM-DD
    conn = get_connection()
    cursor = conn.cursor()
    # 011：删除同维度同日的旧记录，仅保留最新一条
    cursor.execute(
        """DELETE FROM data_status
           WHERE stock_id = ? AND dimension = ? AND fetched_at LIKE ?""",
        (stock_id, dimension, today_prefix + '%'),
    )
    cursor.execute(
        """
        INSERT INTO data_status (stock_id, dimension, status, message, fetched_at)
        VALUES (?, ?, ?, ?, ?)
    """,
        (stock_id, dimension, status, message, ts),
    )
    conn.commit()
    conn.close()


def _log_error_to_db(
    stock_id, module, error_type, error_message, dimension=None, traceback_str=None
):
    """012-C: 统一写入 error_logs 表"""
    try:
        conn = get_connection()
        conn.execute(
            'INSERT INTO error_logs (stock_id, module, error_type, error_message, dimension, traceback) VALUES (?,?,?,?,?,?)',
            (
                stock_id,
                module,
                error_type,
                error_message,
                dimension,
                traceback_str[:2000] if traceback_str else None,
            ),  # 截断至2000字符
        )
        conn.commit()
        conn.close()
    except Exception:
        pass  # 写日志失败不阻塞业务


def _normalize_hk_symbol(symbol):
    """
    将港股代码统一转换为5位数字格式。
    'HK3690' → '03690', '00700' → '00700', '3690' → '03690'
    """
    s = symbol.strip().upper()
    s = s.removeprefix('HK')
    # 去除可能的.HK后缀
    s = s.removesuffix('.HK')
    # 补全为5位
    s = s.zfill(5)
    return s


def _get_tencent_prefix(symbol, market):
    """根据股票代码和市场，返回腾讯接口需要的前缀和完整代码"""
    if market == 'hk_stock':
        # 港股代码统一为5位数字，腾讯接口格式: hk03690
        hk_code = _normalize_hk_symbol(symbol)
        return 'hk', hk_code
    elif market == 'a_stock':
        # A股：6开头=上海(sh)，0/3开头=深圳(sz)
        if symbol.startswith('6'):
            return 'sh', symbol
        else:
            return 'sz', symbol
    return '', symbol


def _num_float(v):
    """安全数值转换（含 NaN 防护），失败返回 None。

    OPT-3：原位于 holder 段（2223）；capital_westock 亦引用而迁入本模块（断循环导入）。
    """
    try:
        f = float(str(v).replace(',', ''))
        return f if f == f else None
    except (TypeError, ValueError):
        return None
