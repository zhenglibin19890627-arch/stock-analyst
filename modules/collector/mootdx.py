"""通达信 mootdx 源：K线回补、实时行情、五档盘口（周末守卫）。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
# ============================================================
# 019Y T1：mootdx 行情适配层
# （K线/实时行情降级备用源 + 五档盘口增量数据维度）
#
# 大白话说明：
# - mootdx 走通达信 TCP socket 协议，不经过 requests/httpx，
#   因此不受本项目 requests.Session.request 全局 patch 影响（天然隔离）。
# - mootdx 仅支持 A股（沪市 6 开头、深市 0/3 开头，不带 sh/sz 前缀）。
#   港股 K线/盘口仍走现有源（腾讯/akshare），估值港股走 akshare → 腾讯行情兜底（021C/020R-56）。
# - 首次初始化会挑选最快的行情服务器（约 5 秒），之后全局复用（单例缓存）。
# ============================================================
import threading as _threading_019y
from datetime import datetime

import pandas as pd

from config import KLINE_DAYS
from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.capital_em import _safe_num
from modules.collector.symbols_status import get_stock_id, save_data_status

_MOOTDX_CLIENT = None  # 全局单例客户端（首次初始化约 0.5 秒，之后复用）
_MOOTDX_INIT_DONE = False  # 首次初始化是否已尝试过（成功或失败后不再重复扫描服务器）
_MOOTDX_LOCK = _threading_019y.Lock()

# 019Y M-2：备用服务器池（2026-08-11 实测可用，返回完整行情/K线）。
# 通达信服务器存在区域性故障（部分服务器 TCP 连接成功但返回空数据），
# 因此先逐个健康检查备用池，全部失败才走 bestip 全网扫描（约 70 秒）。
_MOOTDX_FALLBACK_SERVERS = [
    ('115.238.56.198', 7709),  # 浙江电信（实测可用）
    ('115.238.90.165', 7709),  # 浙江电信（实测可用）
    ('218.75.126.9', 7709),    # 浙江电信（实测可用）
    ('180.153.18.170', 7709),  # 上海电信（实测可用）
]


def _mootdx_verify(client):
    """019Y：mootdx 客户端健康检查——发一次实时行情请求，判断服务器是否真的返回数据。
    有些服务器 TCP 连接成功但返回空数据（实测 110.41.147.114 / 218.6.170.47），必须实测验证。
    """
    try:
        df = client.quotes(symbol='000001')
        return df is not None and len(df) > 0
    except Exception:
        return False


def _mootdx_client():
    """获取 mootdx 全局单例客户端（线程安全）。
    初始化顺序（019Y M-2）：
    1) 备用服务器池逐个健康检查（快，实测约 0.1 秒/个）；
    2) 全部失败才走 bestip 全网扫描（慢，约 70 秒，且可能选中故障服务器）；
    首次初始化后全局复用（单例缓存），避免每只股票都重新初始化。
    """
    global _MOOTDX_CLIENT, _MOOTDX_INIT_DONE
    if _MOOTDX_CLIENT is not None:
        return _MOOTDX_CLIENT
    if _MOOTDX_INIT_DONE:
        return None  # 首次初始化已失败（全部服务器不可用），不再重复扫描
    with _MOOTDX_LOCK:
        if _MOOTDX_CLIENT is not None or _MOOTDX_INIT_DONE:
            return _MOOTDX_CLIENT
        from mootdx.quotes import Quotes
        try:
            # 1) 备用服务器池（已知可用，优先）
            for host, port in _MOOTDX_FALLBACK_SERVERS:
                try:
                    logger.info(f'[mootdx] 尝试备用服务器 {host}:{port}（健康检查中）...')
                    client = Quotes.factory(market='std', server=(host, port), timeout=10, heartbeat=True)
                    if _mootdx_verify(client):
                        _MOOTDX_CLIENT = client
                        logger.info(f'[mootdx] 备用服务器 {host}:{port} 健康检查通过，全局单例缓存')
                        return client
                    logger.warning(f'[mootdx] 备用服务器 {host}:{port} 返回空数据，换下一台')
                    try:
                        client.close()
                    except Exception:
                        pass
                except Exception as e:
                    logger.warning(f'[mootdx] 备用服务器 {host}:{port} 连接失败: {e}')
            # 2) bestip 全网扫描（兜底，首次约 70 秒）
            logger.info('[mootdx] 备用服务器池全部不可用，走 bestip 全网扫描（约需 1 分钟）...')
            client = Quotes.factory(market='std', bestip=True, timeout=15, heartbeat=True)
            if _mootdx_verify(client):
                _MOOTDX_CLIENT = client
                logger.info('[mootdx] bestip 服务器健康检查通过，全局单例缓存')
                return client
            logger.warning('[mootdx] bestip 选中的服务器返回空数据（服务器区域性故障）')
            try:
                client.close()
            except Exception:
                pass
        except Exception as e:
            logger.error(f'[mootdx] 客户端初始化异常: {e}')
        _MOOTDX_INIT_DONE = True
        logger.error('[mootdx] 全部服务器均不可用，本次运行不再重试（K线/盘口降级将标记失败）')
        return None


def _mootdx_symbol(symbol, market):
    """A股代码 → mootdx 代码（不带前缀）。港股/非A股返回 None（mootdx 支持有限）。"""
    if market != 'a_stock':
        return None
    if symbol.startswith(('6', '0', '3')):
        return symbol
    return None


def _fetch_kline_mootdx(symbol):
    """019Y T1：mootdx 日K线（frequency=9 表示日线）。
    返回与腾讯接口同格式的 DataFrame（日期/开盘/收盘/最高/最低/成交量/成交额/涨跌幅），
    供 fetch_kline 统一入库。失败抛异常由调用方处理。
    """
    client = _mootdx_client()
    if client is None:
        raise ValueError('mootdx 客户端不可用（初始化失败）')
    bars = client.bars(symbol=symbol, frequency=9, offset=KLINE_DAYS)
    if bars is None or len(bars) == 0:
        raise ValueError('mootdx K线返回空数据')
    rows = []
    for _, r in bars.iterrows():
        dt = str(r.get('datetime', ''))
        try:
            rows.append(
                {
                    '日期': dt.split(' ')[0] if ' ' in dt else dt[:10],
                    '开盘': float(r.get('open', 0) or 0),
                    '收盘': float(r.get('close', 0) or 0),
                    '最高': float(r.get('high', 0) or 0),
                    '最低': float(r.get('low', 0) or 0),
                    '成交量': float(r.get('vol', 0) or 0),
                    '成交额': float(r.get('amount', 0) or 0),
                }
            )
        except (ValueError, TypeError):
            continue
    out = pd.DataFrame(rows)
    if not out.empty:
        out['涨跌幅'] = out['收盘'].pct_change() * 100
        out['涨跌幅'] = out['涨跌幅'].fillna(0)
    return out


def backfill_kline_history_mootdx(stock_id, symbol, market, min_bars=600):
    """020R-48 二期：A股用 mootdx 前复权全量历史补齐日线历史（仅补缺口，不覆盖已有行）。

    用途：月线 MACD（需 26 个月）等多周期指标需要 2 年以上日线；腾讯接口深度受限。
    raw_kline 有 UNIQUE(stock_id, trade_date)，INSERT OR IGNORE 保证已有行（腾讯主源）不被覆盖。
    返回 (status, message)。
    """
    if market != 'a_stock':
        return 'skipped', '仅A股支持 mootdx 历史补采'
    code = _mootdx_symbol(symbol, market)
    if not code:
        return 'skipped', 'mootdx 不支持该代码'

    conn = get_connection()
    cur = conn.cursor()
    cur.execute(
        'SELECT MIN(trade_date) AS m, COUNT(*) AS c FROM raw_kline WHERE stock_id = ?',
        (stock_id,),
    )
    row = cur.fetchone()
    if row and row['c'] and int(row['c']) >= min_bars:
        conn.close()
        return 'skipped', f'日线已有{row["c"]}根(≥{min_bars})，无需补采'

    client = _mootdx_client()
    if client is None:
        conn.close()
        return 'failed', 'mootdx 客户端不可用'

    try:
        try:
            bars = client.bars(symbol=code, frequency=9, offset=800, adjust='qfq')
        except Exception:  # noqa: BLE001 —— adjust 参数不兼容时回退
            bars = client.bars(symbol=code, frequency=9, offset=800)
        if bars is None or len(bars) == 0:
            conn.close()
            return 'failed', 'mootdx 全量历史返回空数据'

        inserted = 0
        for _, r in bars.iterrows():
            dt = str(r.get('datetime', ''))
            trade_date = dt.split(' ')[0] if ' ' in dt else dt[:10]
            try:
                cur.execute(
                    'INSERT OR IGNORE INTO raw_kline '
                    '(stock_id, trade_date, open, close, high, low, volume, amount, pct_change, data_source) '
                    "VALUES (?,?,?,?,?,?,?,?,?,'mootdx')",
                    (
                        stock_id, trade_date,
                        float(r.get('open', 0) or 0), float(r.get('close', 0) or 0),
                        float(r.get('high', 0) or 0), float(r.get('low', 0) or 0),
                        float(r.get('vol', 0) or 0), float(r.get('amount', 0) or 0),
                        None,
                    ),
                )
                if cur.rowcount > 0:
                    inserted += 1
            except (ValueError, TypeError):
                continue
        conn.commit()
        cur.execute('SELECT COUNT(*) AS c FROM raw_kline WHERE stock_id = ?', (stock_id,))
        total = cur.fetchone()['c']
        conn.close()
        logger.info(f'[020R-48 历史补采] {symbol}: 新增{inserted}根，共{total}根日线')
        return 'success', f'新增{inserted}根，共{total}根日线'
    except Exception as e:  # noqa: BLE001
        conn.close()
        logger.warning(f'[020R-48 历史补采] {symbol} 失败: {e}')
        return 'failed', f'历史补采失败: {e}'


def _fetch_realtime_quote_mootdx(symbol):
    """019Y T1：mootdx 实时行情（含五档买卖盘）。
    返回 dict：{'price','pct_change','bid1_price'..'bid5_price','bid1_vol'..'bid5_vol',
               'ask1_price'..'ask5_price','ask1_vol'..'ask5_vol','quote_time'}
    失败返回 None（不抛异常，不阻塞主流程）。
    """
    try:
        client = _mootdx_client()
        if client is None:
            logger.warning(f'[mootdx] {symbol} 客户端不可用，跳过实时行情')
            return None
        df = client.quotes(symbol=symbol)
        if df is None or len(df) == 0:
            logger.warning(f'[mootdx] {symbol} 实时行情返回空数据')
            return None
        row = df.iloc[0]
        price = _safe_num(row.get('price'))
        last_close = _safe_num(row.get('last_close'))
        if price is None or price <= 0:
            logger.warning(f'[mootdx] {symbol} 实时行情价格异常: price={price}')
            return None
        pct = round((price - last_close) / last_close * 100, 2) if last_close else None
        quote = {'price': price, 'pct_change': pct}
        for lvl in range(1, 6):
            quote[f'bid{lvl}_price'] = _safe_num(row.get(f'bid{lvl}'))
            quote[f'bid{lvl}_vol'] = _safe_num(row.get(f'bid_vol{lvl}'))
            quote[f'ask{lvl}_price'] = _safe_num(row.get(f'ask{lvl}'))
            quote[f'ask{lvl}_vol'] = _safe_num(row.get(f'ask_vol{lvl}'))
        quote['quote_time'] = str(row.get('servertime', ''))[:8]
        return quote
    except Exception as e:
        logger.warning(f'[mootdx] {symbol} 实时行情获取失败: {e}')
        return None


def get_realtime_quote_mootdx(symbol):
    """019Y T1：对外只读接口——供 app.py 实时价格刷新降级使用（只取价格，不写库）。"""
    q = _fetch_realtime_quote_mootdx(symbol)
    if q is None or q.get('price') is None:
        return None
    return {'price': q['price'], 'pct_change': q.get('pct_change')}


def _ensure_kline_source_column():
    """019Y：确保 raw_kline.data_source 列存在（幂等，兼容未迁移的旧库）"""
    try:
        conn = get_connection()
        conn.execute('ALTER TABLE raw_kline ADD COLUMN data_source TEXT DEFAULT NULL')
        conn.commit()
        conn.close()
    except Exception:
        pass  # 列已存在


def fetch_orderbook(symbol, market, force_full=False):
    """019Y T1：采集五档盘口快照（mootdx 实时行情）。
    每只股票每天仅保留最新一条快照（UNIQUE(stock_id, trade_date)，重复采集覆盖当日）。
    仅支持 A股（mootdx 港股支持有限）。失败不阻塞主流程。
    021C：非交易日（周末）跳过——mootdx 周末返回上一交易日快照，
    若盖当日日期会形成周末脏行（2026-08-15 实测 23 行），与资金面 020L 同原则。
    返回: (状态, 消息)
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 021C：周末守卫（与 fetch_capital_flow 020L 同原则）
    if datetime.now(_CN_TZ).weekday() >= 5:
        save_data_status(stock_id, 'orderbook', 'skipped', '非交易日跳过（mootdx 盘口）')
        logger.info(f'[{symbol}] 非交易日（周末），跳过五档盘口采集')
        return 'skipped', '非交易日跳过（mootdx 盘口）'

    mootdx_code = _mootdx_symbol(symbol, market)
    if not mootdx_code:
        save_data_status(stock_id, 'orderbook', 'skipped', 'mootdx暂不支持港股盘口')
        return 'skipped', 'mootdx暂不支持港股盘口'

    try:
        quote = _fetch_realtime_quote_mootdx(mootdx_code)
        if not quote or quote.get('price') is None:
            save_data_status(stock_id, 'orderbook', 'failed', 'mootdx实时行情获取失败')
            return 'failed', 'mootdx实时行情获取失败'

        today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO stock_orderbook
            (stock_id, trade_date, quote_time, latest_price, pct_change,
             bid1_price, bid1_vol, bid2_price, bid2_vol, bid3_price, bid3_vol,
             bid4_price, bid4_vol, bid5_price, bid5_vol,
             ask1_price, ask1_vol, ask2_price, ask2_vol, ask3_price, ask3_vol,
             ask4_price, ask4_vol, ask5_price, ask5_vol, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                stock_id,
                today_str,
                quote.get('quote_time'),
                quote['price'],
                quote.get('pct_change'),
                quote.get('bid1_price'),
                quote.get('bid1_vol'),
                quote.get('bid2_price'),
                quote.get('bid2_vol'),
                quote.get('bid3_price'),
                quote.get('bid3_vol'),
                quote.get('bid4_price'),
                quote.get('bid4_vol'),
                quote.get('bid5_price'),
                quote.get('bid5_vol'),
                quote.get('ask1_price'),
                quote.get('ask1_vol'),
                quote.get('ask2_price'),
                quote.get('ask2_vol'),
                quote.get('ask3_price'),
                quote.get('ask3_vol'),
                quote.get('ask4_price'),
                quote.get('ask4_vol'),
                quote.get('ask5_price'),
                quote.get('ask5_vol'),
                'mootdx',
            ),
        )
        conn.commit()
        conn.close()
        msg = f'五档盘口已入库（mootdx，快照{quote.get("quote_time")}，最新价{quote["price"]}）'
        save_data_status(stock_id, 'orderbook', 'success', msg)
        logger.info(f'[{symbol}] {msg}')
        return 'success', msg
    except Exception as e:
        save_data_status(stock_id, 'orderbook', 'failed', str(e))
        logger.warning(f'[{symbol}] 五档盘口采集失败: {e}')
        return 'failed', str(e)
