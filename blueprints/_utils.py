"""共享展示层工具函数(自 app.py 拆分,逻辑零改动)。"""
import json


def _fmt_pct(val, decimals=2):
    """格式化百分比值：4.888726 -> '+4.89%'，负数自动加负号"""
    if val is None:
        return None
    try:
        v = float(val)
        sign = '+' if v >= 0 else ''
        return '{}{:.{}f}%'.format(sign, v, decimals)
    except (ValueError, TypeError):
        return None


def _fmt_num(val, decimals=2):
    """格式化数值：47.0 -> '47.00'"""
    if val is None:
        return None
    try:
        return '{:.{}f}'.format(float(val), decimals)
    except (ValueError, TypeError):
        return None


def _fmt_wan(val):
    """元 -> 万元：2065000000.0 -> 206500.00"""
    if val is None:
        return None
    try:
        return round(float(val) / 1e4, 2)
    except (ValueError, TypeError):
        return None


def _get_market_by_stock_id(cursor, stock_id):
    """根据 stock_id 查询市场类型（a_stock / hk_stock）"""
    cursor.execute('SELECT market FROM stocks WHERE id = ?', (stock_id,))
    row = cursor.fetchone()
    return row['market'] if row else None


def _currency_label(market):
    """返回货币标签"""
    return 'HKD' if market == 'hk_stock' else 'CNY'


def _currency_unit(market):
    """返回货币单位名称"""
    return '万港元' if market == 'hk_stock' else '万元'


def _derive_obos_signal(key_factors_raw):
    """从 daily_reports.key_factors 解析超买超卖信号（DEV-TASKS-20260727-003）。

    数据来源：advisor._build_kline_factors 已计算 rsi_status / boll_position 因子，
    存储于 key_factors.kline.top_factors 中。本函数仅做展示层派生，不改任何引擎逻辑。

    规则（与任务书一致）：
      - RSI>70（rsi_status 含'超买'）或 布林带触及上轨（boll_position 含'上轨'）→ 'overbought'
      - RSI<30（rsi_status 含'超卖'）或 布林带触及下轨（boll_position 含'下轨'）→ 'oversold'
      - 其他 → None（不显示徽标）

    Args:
        key_factors_raw: daily_reports.key_factors 字段值（JSON 字符串 / dict / None）

    Returns:
        'overbought' | 'oversold' | None
    """
    if not key_factors_raw:
        return None
    try:
        kf = json.loads(key_factors_raw) if isinstance(key_factors_raw, str) else key_factors_raw
    except (ValueError, TypeError):
        return None
    if not isinstance(kf, dict):
        return None
    kline = kf.get('kline') or {}
    top_factors = kline.get('top_factors') or {}
    rsi_status = str(top_factors.get('rsi_status', ''))
    boll_position = str(top_factors.get('boll_position', ''))
    # 兼容新旧两套因子文案（advisor/analysis_engine 均输出'超买'/'超卖'子串）
    is_overbought = ('超买' in rsi_status) or ('上轨' in boll_position)
    is_oversold = ('超卖' in rsi_status) or ('下轨' in boll_position)
    if is_overbought:
        return 'overbought'
    if is_oversold:
        return 'oversold'
    return None


def _resolve_report_type(cursor, report_date):
    """019D 统一口径：判定当日应取 daily 还是 intraday（daily 优先）。

    与看板评分表原有逻辑完全一致（原 /api/ratings 口径，该端点已随 021D 删除），
    提取为共享辅助函数供所有读取入口复用，
    防止未来新入口遗漏 daily-优先 / status='ok' 口径。
    """
    cursor.execute(
        "SELECT COUNT(*) as cnt FROM daily_reports "
        "WHERE report_date=? AND report_type='daily' AND status='ok'",
        (report_date,),
    )
    return 'daily' if cursor.fetchone()['cnt'] > 0 else 'intraday'


def _latest_report_join_sql():
    """019R 共享口径：每股最新一份有效报告派生表的 SQL 片段（看板/汇总两接口共用）。

    口径：status='ok' 前置过滤；ROW_NUMBER 窗口 PARTITION BY stock_id，
    ORDER BY report_date DESC、daily 优先（CASE report_type='daily' THEN 0 ELSE 1），
    取 rn=1 —— 即每股在自身最新 report_date 上 daily 优先的有效报告。
    全库同日时与 019D 全局判定（_resolve_report_type）完全等价。
    返回形如 `(... ) AS lr` 的片段；调用方以 LEFT JOIN 本片段 ON lr.stock_id = s.id
    使用（股票表别名须为 s）。杜绝两处口径漂移。
    """
    return (
        "(SELECT * FROM ("
        " SELECT dr.*, ROW_NUMBER() OVER ("
        "  PARTITION BY dr.stock_id"
        "  ORDER BY dr.report_date DESC,"
        "   CASE WHEN dr.report_type='daily' THEN 0 ELSE 1 END"
        " ) AS rn"
        " FROM daily_reports dr"
        " WHERE dr.status='ok'"
        ") WHERE rn=1) AS lr"
    )
