"""
数据采集模块 —— 核心模块
负责获取 A股和港股的全部数据（基本面、技术面、消息面、资金面）。

数据源说明：
- K线数据：腾讯财经接口（稳定可用）
- 基本面数据：新浪财经接口（通过 akshare 调用）
- 资金面数据：东方财富 push2 接口（稳定可用）
- 消息面数据：公告数据有限，标注缺失

设计原则：
1. 每个采集函数都有容错处理，失败不崩溃，返回状态标记
2. 获取到的数据自动存入数据库
3. 数据缺失时记录原因，供报告生成时提示用户
"""

import logging
import os
import sys
import time
import traceback

# 获取本地时区当前时间（北京时间），所有时间戳统一使用此时区
from datetime import datetime, timedelta, timezone
from datetime import timedelta as _td

import pandas as pd

_CN_TZ = timezone(_td(hours=8), name='Asia/Shanghai')


def now_cn():
    """返回北京时间字符串（格式 YYYY-MM-DD HH:MM:SS），用于所有数据库写入"""
    return datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')


# ============================================================
# 解决系统代理干扰问题
# 有些用户电脑装了 Clash/V2Ray 等代理软件，默认会读取系统代理配置。
# 对于腾讯/新浪接口：直连即可，需禁用代理。
# 对于东方财富 push2 接口：直连可能被封锁，需要通过系统代理访问。
# 因此采用分策略处理：akshare 内部统一禁用代理（直连），
# 东方财富资金流向单独使用智能回退逻辑（先直连再走代理）。
# ============================================================
import random as _random
import urllib.request as _urlreq

import requests

# ============================================================
# UA池（≥20个真实浏览器UA，随机选取，降低被风控概率）
# ============================================================
_UA_POOL = [
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:119.0) Gecko/20100101 Firefox/119.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15',
    'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (X11; Linux x86_64; rv:121.0) Gecko/20100101 Firefox/121.0',
    'Mozilla/5.0 (X11; Ubuntu; Linux x86_64; rv:120.0) Gecko/20100101 Firefox/120.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36 Edg/120.0.0.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36 OPR/104.0.0.0',
    'Mozilla/5.0 (iPhone; CPU iPhone OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (iPad; CPU OS 17_2 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Mobile/15E148 Safari/604.1',
    'Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Mobile Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; WOW64; rv:118.0) Gecko/20100101 Firefox/118.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/117.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:118.0) Gecko/20100101 Firefox/118.0',
]


def _random_ua():
    """从UA池中随机选取一个User-Agent"""
    return _random.choice(_UA_POOL)


_original_request = requests.Session.request


def _no_proxy_request(self, *args, **kwargs):
    """
    绕过系统代理，直接连接（适用于 akshare 内部的腾讯/新浪请求）。
    东方财富域名例外：不干预其连接方式（部分接口需要原始行为）。
    """
    # 提取 URL（args[1] 或 kwargs['url']）
    url = ''
    if len(args) > 1:
        url = str(args[1])
    elif 'url' in kwargs:
        url = str(kwargs['url'])

    # 东方财富域名：使用原始请求行为（不强制禁用代理）
    if 'eastmoney.com' in url:
        return _original_request(self, *args, **kwargs)

    # 其他域名（腾讯/新浪等）：强制禁用代理，直连
    self.trust_env = False
    if 'proxies' not in kwargs or kwargs['proxies'] is None:
        kwargs['proxies'] = {'http': None, 'https': None}
    return _original_request(self, *args, **kwargs)


requests.Session.request = _no_proxy_request


# ============================================================
# 代理健康检查：连续失败2次的代理自动禁用30分钟
# ============================================================
class ProxyHealthTracker:
    """跟踪代理健康状态，连续失败后自动禁用"""

    def __init__(self):
        self._fail_count = 0
        self._disabled_until = None

    def is_available(self):
        """代理是否可用（未被禁用或禁用已过期）"""
        if self._disabled_until is None:
            return True
        if datetime.now(_CN_TZ).timestamp() > self._disabled_until:
            self._disabled_until = None
            self._fail_count = 0
            logger.info('代理已恢复可用（禁用期结束）')
            return True
        return False

    def record_failure(self):
        """记录一次失败"""
        self._fail_count += 1
        if self._fail_count >= 2:
            self._disabled_until = datetime.now(_CN_TZ).timestamp() + 1800  # 30分钟
            logger.warning(f'代理连续失败{self._fail_count}次，自动禁用30分钟')

    def record_success(self):
        """记录一次成功"""
        self._fail_count = 0
        self._disabled_until = None


_proxy_health = ProxyHealthTracker()

# 现在才导入 akshare（它内部使用的 requests 已经被 patch 了）
import akshare as ak

# 添加项目根目录到路径，确保能导入 config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import (
    FUNDAMENTAL_REPORT_TTL_DAYS,
    KLINE_DAYS,
    MAX_RETRIES,
    NORTH_CAPITAL_CACHE_DAYS,
    PE_PB_CACHE_TTL_HOURS,
)
from database.db_manager import get_connection

# 日志配置
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ============================================================
# 工具函数
# ============================================================


def _http_get(url, params=None, headers=None, timeout=15):
    """统一的 HTTP GET 请求（直连模式，用于腾讯/新浪接口），使用随机UA"""
    session = requests.Session()
    session.trust_env = False
    session.headers.update({'User-Agent': _random_ua(), 'Referer': 'https://finance.qq.com'})
    if headers:
        session.headers.update(headers)
    resp = session.get(url, params=params, timeout=timeout, proxies={'http': None, 'https': None})
    resp.raise_for_status()
    return resp


def _http_get_em(url, params=None, timeout=15, max_retries=None):
    """
    东方财富专用请求：智能回退 + 多轮重试 + UA池 + 随机延迟。
    策略：有系统代理时优先走代理（EM直连通常被拒），无代理时走直连。
    每轮2次尝试，共max_retries轮（默认MAX_RETRIES）。
    """
    system_proxies = _urlreq.getproxies()
    last_error = None
    # 代理健康检查：被禁用的代理跳过
    proxy_available = bool(system_proxies) and _proxy_health.is_available()
    rounds = max_retries if max_retries else MAX_RETRIES
    # connect_timeout=5, read_timeout=10
    timeout_tuple = (5, 10) if timeout == 15 else timeout

    for attempt in range(rounds):
        order = []
        if proxy_available:
            order = [('proxy', True), ('direct', False)]
        else:
            order = [('direct', False)]

        for label, use_proxy in order:
            try:
                # 请求间随机延迟 1.5~3.5秒
                if attempt > 0 or label == 'direct':
                    _delay = _random.uniform(1.5, 3.5)
                    time.sleep(_delay)
                session = requests.Session()
                session.trust_env = use_proxy
                session.headers.update(
                    {
                        'User-Agent': _random_ua(),
                        'Accept': 'application/json, text/plain, */*',
                        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                    }
                )
                if use_proxy:
                    resp = session.get(
                        url, params=params, timeout=timeout_tuple, proxies=system_proxies
                    )
                else:
                    resp = session.get(
                        url,
                        params=params,
                        timeout=timeout_tuple,
                        proxies={'http': None, 'https': None},
                    )
                resp.raise_for_status()
                logger.info(f'东方财富{label}成功（第{attempt + 1}轮）')
                if use_proxy:
                    _proxy_health.record_success()
                return resp
            except Exception as e:
                last_error = e
                if use_proxy:
                    _proxy_health.record_failure()
                logger.info(f'东方财富{label}失败: ' + str(e)[:80])

        if attempt < rounds - 1:
            wait = _random.uniform(1.5, 3.5)
            logger.info(f'东方财富第{attempt + 1}轮失败，等待{wait:.1f}秒后重试...')
            time.sleep(wait)

    raise ConnectionError(f'东方财富接口无法访问（直连和代理均失败，重试{rounds}轮）: {last_error}')


def retry(func, max_retries=MAX_RETRIES, delay=1):
    """重试装饰器：网络请求失败时自动重试。"""

    def wrapper(*args, **kwargs):
        last_error = None
        for attempt in range(max_retries):
            try:
                return func(*args, **kwargs)
            except Exception as e:
                last_error = e
                logger.warning(f'第{attempt + 1}次尝试失败: {e}')
                if attempt < max_retries - 1:
                    time.sleep(delay)
        logger.error(f'重试{max_retries}次后仍失败: {last_error}')
        return None

    return wrapper


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
    if s.startswith('HK'):
        s = s[2:]
    # 去除可能的.HK后缀
    if s.endswith('.HK'):
        s = s[:-3]
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


@retry
def _fetch_valuation_tencent(symbol, market):
    """
    从腾讯实时行情接口获取 PE/PB 估值数据。
    注意：A股和港股的字段索引不同！
      A股: [39]=PE(TTM), [46]=PB
      港股: [39]=PE(TTM), [43]=PB  (港股[46]是英文股票名而非PB)
    返回: (pe, pb)
    """
    prefix, normalized_code = _get_tencent_prefix(symbol, market)
    url = 'https://qt.gtimg.cn/q=' + prefix + normalized_code

    resp = _http_get(url)
    text = resp.text
    parts = text.split('~')

    if len(parts) < 47:
        raise ValueError('腾讯行情数据字段不足: ' + str(len(parts)))

    # PE 字段 A股和港股都在 [39]
    pe_str = parts[39].strip().strip('"')

    # PB 字段：A股在 [46]，港股在 [43]
    if market == 'hk_stock':
        pb_str = parts[43].strip().strip('"') if len(parts) > 43 else ''
    else:
        pb_str = parts[46].strip().strip(';').strip('"')

    pe = None
    pb = None
    try:
        pe = float(pe_str) if pe_str else None
    except ValueError:
        pass
    try:
        pb = float(pb_str) if pb_str else None
    except ValueError:
        pass

    logger.info(f'腾讯估值 {symbol}: PE={pe}, PB={pb} (market={market})')
    return pe, pb


# ============================================================
# K线数据采集（A股 + 港股统一使用腾讯接口）
# ============================================================


@retry
def _fetch_kline_tencent(symbol, market):
    """
    从腾讯财经接口获取日K线数据（前复权）。
    腾讯接口返回格式：[日期, 开盘, 收盘, 最高, 最低, 成交量]
    """
    prefix, normalized_code = _get_tencent_prefix(symbol, market)
    tencent_code = f'{prefix}{normalized_code}'

    url = 'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
    params = {'param': f'{tencent_code},day,,,{KLINE_DAYS},qfq'}

    resp = _http_get(url, params=params)
    data = resp.json()

    # 解析数据
    stock_data = data.get('data', {}).get(tencent_code, {})
    kline_list = stock_data.get('qfqday') or stock_data.get('day') or []

    if not kline_list:
        return pd.DataFrame()

    # 转为 DataFrame
    rows = []
    for item in kline_list:
        # A股格式: [date, open, close, high, low, volume]
        # 港股格式: [date, open, close, high, low, volume, {extra}]
        if len(item) >= 6:
            rows.append(
                {
                    '日期': item[0],
                    '开盘': float(item[1]),
                    '收盘': float(item[2]),
                    '最高': float(item[3]),
                    '最低': float(item[4]),
                    '成交量': float(item[5]),
                }
            )

    df = pd.DataFrame(rows)
    # 计算涨跌幅
    if not df.empty:
        df['涨跌幅'] = df['收盘'].pct_change() * 100
        df['涨跌幅'] = df['涨跌幅'].fillna(0)

    return df


def fetch_kline(symbol, market, force_full=False):
    """
    采集K线数据并存入数据库。A股和港股统一使用此函数。
    011增量优化：同日跳过（last_date >= 今日 → 跳过），全量覆盖确保复权因子一致。
    返回: (状态字符串, 消息)
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 011增量：同日跳过检查
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                'SELECT MAX(trade_date) as last_date FROM raw_kline WHERE stock_id = ?', (stock_id,)
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['last_date']:
                last_date = str(row['last_date'])[:10]
                today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                if last_date >= today_str:
                    skip_msg = f'同日跳过(K线已有{last_date}数据)'
                    save_data_status(stock_id, 'kline', 'success', skip_msg)
                    logger.info(f'[{symbol}] {skip_msg}')
                    return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[{symbol}] K线增量检查异常(降级为全量): {e}')

    try:
        df = _fetch_kline_tencent(symbol, market)
        if df is None or df.empty:
            save_data_status(stock_id, 'kline', 'failed', '腾讯接口返回空数据')
            return 'failed', '腾讯接口返回空数据'

        conn = get_connection()
        cursor = conn.cursor()

        saved_count = 0
        for _, row in df.iterrows():
            trade_date = str(row['日期']).split(' ')[0]
            try:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_kline
                    (stock_id, trade_date, open, close, high, low, volume, amount, turnover, pct_change)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        trade_date,
                        float(row.get('开盘', 0) or 0),
                        float(row.get('收盘', 0) or 0),
                        float(row.get('最高', 0) or 0),
                        float(row.get('最低', 0) or 0),
                        float(row.get('成交量', 0) or 0),
                        0,  # 成交额（腾讯接口不提供，留空）
                        0,  # 换手率（同上）
                        float(row.get('涨跌幅', 0) or 0),
                    ),
                )
                saved_count += 1
            except Exception:
                continue

        conn.commit()
        conn.close()

        save_data_status(stock_id, 'kline', 'success', f'成功获取{saved_count}条K线数据')
        market_name = 'A股' if market == 'a_stock' else '港股'
        logger.info(f'[{market_name} {symbol}] K线数据采集成功，共{saved_count}条')
        return 'success', f'获取{saved_count}条K线数据'

    except Exception as e:
        save_data_status(stock_id, 'kline', 'failed', str(e))
        logger.error(f'[{symbol}] K线数据采集失败: {e}')
        return 'failed', str(e)


# ============================================================
# A股 —— 基本面数据
# ============================================================


@retry
def _fetch_a_fundamental_sina(symbol):
    """通过akshare从新浪获取A股财务分析指标"""
    df = ak.stock_financial_analysis_indicator(symbol=symbol, start_year='2020')
    return df


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

    if not force_full:
        # 门控A：财报数据TTL（80天）
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
                    skip_financial = True
                    logger.info(f'[A股 {symbol}] 财报数据{days_since}天内，跳过财报采集')

                    # 门控B：PE/PB TTL（24h），仅当财报跳过时检查
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

    # --- 财务分析指标（新浪源）---
    if not skip_financial:
        try:
            df_fin = _fetch_a_fundamental_sina(symbol)
            if df_fin is not None and not df_fin.empty:
                conn = get_connection()
                cursor = conn.cursor()

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

                # 取最近4条财报（从尾部往前取，确保是最新数据）
                total_rows = len(df_fin)
                take_count = min(4, total_rows)
                for idx in range(total_rows - 1, total_rows - 1 - take_count, -1):
                    row = df_fin.iloc[idx]
                    report_date = str(row.get('日期', '')).split(' ')[0]

                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO raw_fundamental
                        (stock_id, report_date,
                         roe, gross_margin, net_margin, debt_ratio,
                         current_ratio, quick_ratio,
                         revenue_growth, profit_growth,
                         ocf_to_net_profit)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            stock_id,
                            report_date,
                            safe_get(row, '净资产收益率(%)', '加权净资产收益率(%)'),
                            safe_get(row, '销售毛利率(%)'),
                            safe_get(row, '销售净利率(%)'),
                            safe_get(row, '资产负债率(%)'),
                            safe_get(row, '流动比率'),
                            safe_get(row, '速动比率'),
                            safe_get(row, '主营业务收入增长率(%)'),
                            safe_get(row, '净利润增长率(%)'),
                            safe_get(
                                row,
                                '经营现金净流量对净利润的比率(%)',
                                '经营现金净流量与净利润的比率(%)',
                            ),
                        ),
                    )
                    saved_count += 1
                    logger.info(
                        f'[A股 {symbol}] 财报: {report_date}, ROE={safe_get(row, "净资产收益率(%)")}'
                    )

                conn.commit()
                conn.close()
            else:
                warnings.append('财务分析指标数据为空')
        except Exception as e:
            warnings.append(f'财务指标获取失败: {e}')
            logger.warning(f'[A股 {symbol}] 财务指标获取失败: {e}')

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
                pe_val, pb_val = result
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
    if skip_financial and not skip_pepb:
        # 仅采集了PE/PB（财报跳过）
        if not warnings:
            save_data_status(stock_id, 'fundamental', 'success', 'PE/PB更新成功(财报跳过)')
            return 'success', 'PE/PB更新成功(财报跳过)'
        else:
            save_data_status(stock_id, 'fundamental', 'partial', '; '.join(warnings))
            return 'partial', '; '.join(warnings)
    elif saved_count > 0 and not warnings:
        save_data_status(stock_id, 'fundamental', 'success', '基本面数据采集成功')
        return 'success', '基本面数据采集成功'
    elif saved_count > 0 and warnings:
        save_data_status(stock_id, 'fundamental', 'partial', '; '.join(warnings))
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


def fetch_holder_increase(symbol: str, preloaded_df=None):
    """B10: 获取近30天大股东/高管增减持信息（仅A股）。
    数据源：雪球内部交易接口 stock_inner_trade_xq()
    列结构: 股票代码/股票名称/变动日期/变动人/变动股数/成交均价/变动后持股数/与董监高关系/董监高职务
    增减持方向通过 '变动股数' 正负判断（正=增持，负=减持）

    B11-API-DEDUP：支持预加载数据(preloaded_df)和模块级缓存，避免批量时重复调用全市场接口。

    Returns:
        True=有增持, False=有减持, None=无记录或接口不可用
    """
    global _holder_cache, _holder_cache_time

    try:
        if preloaded_df is not None:
            df = preloaded_df
        elif (
            _holder_cache is not None
            and _holder_cache_time
            and (time.time() - _holder_cache_time) < 600
        ):
            # 10分钟内缓存有效
            df = _holder_cache
        else:
            df = ak.stock_inner_trade_xq()
            _holder_cache = df
            _holder_cache_time = time.time()

        if df is None or df.empty:
            return None
        # 股票代码列含前缀 SZ/SH，如 'SZ000858'
        code_col = '股票代码'
        if code_col not in df.columns:
            return None
        # 过滤本股票
        sub = df[df[code_col].str.contains(symbol, na=False)]
        if sub.empty:
            return None
        # 过滤近30天
        date_col = '变动日期'
        if date_col not in sub.columns:
            return None
        cutoff = (datetime.now(_CN_TZ) - timedelta(days=30)).strftime('%Y-%m-%d')
        sub = sub[sub[date_col].astype(str) >= cutoff]
        if sub.empty:
            return None
        # 判断方向：通过 '变动股数' 正负判断（正=增持，负=减持）
        shares_col = '变动股数'
        if shares_col not in sub.columns:
            return None
        shares = sub[shares_col].tolist()
        has_increase = any(float(s) > 0 for s in shares if s is not None)
        has_decrease = any(float(s) < 0 for s in shares if s is not None)
        if has_increase and not has_decrease:
            return True
        elif has_decrease and not has_increase:
            return False
        elif has_increase and has_decrease:
            # 既有增持又有减持，按最近一笔判断
            latest_shares = sub.iloc[0][shares_col]
            return float(latest_shares) > 0 if latest_shares is not None else None
        return None
    except Exception as e:
        logger.warning(f'[B10 股东增减持 {symbol}] 接口失败(静默降级): {e}')
        return None


def _save_holder_increase(stock_id: int, holder_increase):
    """B10: 将 holder_increase 写入 raw_fundamental 表（ALTER TABLE 新增列）"""
    conn = get_connection()
    cursor = conn.cursor()
    # 确保列存在（幂等 ALTER TABLE）
    try:
        cursor.execute('ALTER TABLE raw_fundamental ADD COLUMN holder_increase BOOLEAN')
        conn.commit()
        logger.info('[B10] raw_fundamental 表新增 holder_increase 列')
    except Exception:
        pass  # 列已存在
    # 更新最新记录
    if holder_increase is not None:
        cursor.execute(
            'UPDATE raw_fundamental SET holder_increase = ? WHERE stock_id = ? AND report_date = (SELECT MAX(report_date) FROM raw_fundamental WHERE stock_id = ?)',
            (holder_increase, stock_id, stock_id),
        )
        conn.commit()
        logger.info(f'[B10 股东增减持] stock_id={stock_id}, holder_increase={holder_increase}')
    conn.close()


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
                     ocf_to_net_profit)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                saved_count += 1

            conn.commit()
            conn.close()
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
            pe, pb = result
            if pe is not None or pb is not None:
                conn2 = get_connection()
                cursor2 = conn2.cursor()
                # P0-HK-FUND-002：PE/PB 合并到最新财报行（对齐 A 股逻辑），
                # 不再 INSERT 新行，避免 data_adapter 只读到 PE/PB 而丢财报指标。
                cursor2.execute(
                    'SELECT report_date FROM raw_fundamental WHERE stock_id = ? ORDER BY report_date DESC LIMIT 1',
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
    if saved_count > 0 and not warnings:
        save_data_status(stock_id, 'fundamental', 'success', '港股基本面数据采集成功')
        return 'success', '港股基本面数据采集成功'
    elif saved_count > 0 and warnings:
        save_data_status(stock_id, 'fundamental', 'partial', '; '.join(warnings))
        return 'partial', f'获取{saved_count}条财务数据，缺失: ' + '; '.join(warnings)
    else:
        save_data_status(stock_id, 'fundamental', 'failed', '; '.join(warnings))
        return 'failed', '; '.join(warnings)


# ============================================================
# 资金面数据采集
# 主数据源：东方财富 stock_individual_fund_flow（通过akshare调用，直连可用）
#   - 优势：返回120天历史资金流向数据，含主力/超大单/大单/中单/小单
#   - 限制：仅支持A股
# 备用数据源：东方财富 push2 接口（部分网络环境被封，仅作回退）
# ============================================================


# ============================================================
# P0-CAPITAL-001：同花顺全市场批量资金流向源
# 单次调用返回全 A 股当日资金流向（5197+只），从根因消除东方财富逐只限流。
# 1 小时缓存（避免重复下载，单次约 6-12s）。
# 仅含主净额（净额字段），不含分层（超大单/大单/中单/小单），分层由东财逐只补。
# ============================================================
_THS_CAPITAL_CACHE = {'data': None, 'ts': 0.0}  # 模块级缓存：{DataFrame, 时间戳}
_THS_CAPITAL_CACHE_TTL = 3600  # 缓存有效期（秒）= 1 小时
_THS_CONSECUTIVE_FAIL_COUNT = 0  # FIX-B：THS连续失败计数
_THS_FAIL_THRESHOLD = 3  # FIX-B：连续失败阈值，达到后标记降级

# B11-API-DEDUP：股东增减持接口缓存（10分钟TTL，避免批量时重复调用全市场接口）
_holder_cache = None
_holder_cache_time = None


def _fetch_capital_flow_ths_batch():
    """
    调用同花顺 stock_fund_flow_individual() 获取全 A 股当日资金流向。
    带模块级缓存（1小时TTL），避免批量场景重复下载。
    FIX-B：THS失败时重试1次（5秒间隔），并尝试备选接口 stock_individual_fund_flow_rank()。
    返回 pandas.DataFrame 或 None。
    """
    global _THS_CONSECUTIVE_FAIL_COUNT

    now_ts = time.time()
    if (
        _THS_CAPITAL_CACHE['data'] is not None
        and (now_ts - _THS_CAPITAL_CACHE['ts']) < _THS_CAPITAL_CACHE_TTL
    ):
        logger.info('[同花顺批量] 命中内存缓存（1小时TTL内），直接复用')
        return _THS_CAPITAL_CACHE['data']

    # FIX-B：连续失败达阈值时跳过THS，直接返回None（由调用方回退EM逐只）
    if _THS_CONSECUTIVE_FAIL_COUNT >= _THS_FAIL_THRESHOLD:
        logger.warning(
            f'[同花顺批量] 连续失败已达阈值({_THS_CONSECUTIVE_FAIL_COUNT})，跳过THS，回退EM逐只'
        )
        return None

    # FIX-B：主接口 stock_fund_flow_individual()
    df = _try_ths_primary()

    # FIX-B：主接口失败时重试1次（间隔5秒）
    if df is None:
        logger.info('[同花顺批量] 主接口失败，5秒后重试1次...')
        time.sleep(5)
        df = _try_ths_primary()

    # FIX-B：重试仍失败时，尝试备选接口 stock_individual_fund_flow_rank()
    if df is None:
        logger.info('[同花顺批量] 重试仍失败，尝试备选接口 stock_individual_fund_flow_rank()...')
        df = _try_ths_rank_backup()

    if df is not None:
        _THS_CONSECUTIVE_FAIL_COUNT = 0  # 成功则重置计数
        _THS_CAPITAL_CACHE['data'] = df
        _THS_CAPITAL_CACHE['ts'] = time.time()
        logger.info(f'[同花顺批量] 获取成功: {len(df)} 只股票当日资金流向')
        return df

    # 全部失败
    _THS_CONSECUTIVE_FAIL_COUNT += 1
    logger.warning(f'[同花顺批量] 全部接口失败，连续失败计数={_THS_CONSECUTIVE_FAIL_COUNT}')
    return None


def _try_ths_primary():
    """FIX-B：主接口 ak.stock_fund_flow_individual()"""
    try:
        logger.info('[同花顺批量] 请求 stock_fund_flow_individual()（全市场资金流向）...')
        df = ak.stock_fund_flow_individual()
        if df is None or df.empty:
            logger.warning('[同花顺批量] 主接口返回空数据')
            return None
        return df
    except Exception as e:
        logger.warning(f'[同花顺批量] 主接口获取失败: {e}')
        return None


def _try_ths_rank_backup():
    """FIX-B：备选接口 ak.stock_individual_fund_flow_rank(indicator='今日')
    列名与主接口不同，统一映射为 {股票代码, 净额, 成交额} 供后续处理。"""
    try:
        logger.info('[同花顺批量] 请求 stock_individual_fund_flow_rank(indicator=今日)...')
        df = ak.stock_individual_fund_flow_rank(indicator='今日')
        if df is None or df.empty:
            logger.warning('[同花顺批量] 备选接口返回空数据')
            return None
        # 列名映射：备选接口 → 主接口格式
        col_map = {
            '代码': '股票代码',
            '今日主力净流入-净额': '净额',
            '今日成交额': '成交额',
        }
        rename = {k: v for k, v in col_map.items() if k in df.columns}
        if rename:
            df = df.rename(columns=rename)
        # 确保有核心列
        if '股票代码' not in df.columns:
            logger.warning(f'[同花顺批量] 备选接口缺少股票代码列，实际列: {list(df.columns)}')
            return None
        logger.info(f'[同花顺批量] 备选接口获取成功: {len(df)} 行')
        return df
    except Exception as e:
        logger.warning(f'[同花顺批量] 备选接口获取失败: {e}')
        return None


def fetch_capital_flow_batch(a_stock_symbols):
    """
    P0-CAPITAL-001 批量资金面预取入口。
    调用同花顺全市场批量源，一次性写入所有 A 股标的的当日资金流向。
    后续逐只 fetch_capital_flow 调用时，前置校验层会识别到已有数据而跳过东财采集。

    Args:
        a_stock_symbols: list[str]，A 股代码列表（如 ['600276','000333',...]）

    Returns:
        dict: {'success_count': n, 'fail_count': n, 'source': '同花顺批量'}
    """
    if not a_stock_symbols:
        return {'success_count': 0, 'fail_count': 0, 'source': '同花顺批量(空列表)'}

    today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    df = _fetch_capital_flow_ths_batch()
    if df is None:
        # FIX-B：THS不可用时回退EM逐只采集
        logger.warning('[同花顺批量] 批量源不可用（含重试+备选均失败），回退EM逐只采集')
        em_success = 0
        em_fail = 0
        for sym in a_stock_symbols:
            try:
                result = fetch_capital_flow(sym, 'a_stock')
                if result and result[0] == 'success':
                    em_success += 1
                else:
                    em_fail += 1
            except Exception as e:
                em_fail += 1
                logger.warning(f'[同花顺批量] EM回退 {sym} 失败: {e}')
        return {
            'success_count': em_success,
            'fail_count': em_fail,
            'source': f'EM逐只回退(THS连续失败={_THS_CONSECUTIVE_FAIL_COUNT})',
        }

    # 同花顺股票代码列可能为 int64（000333→333）或字符串，统一规整为6位字符串
    def _norm_code(v):
        s = str(v).strip()
        if s.replace('.', '').isdigit():
            return str(int(float(s))).zfill(6)
        return s

    code_col = '股票代码'
    if code_col not in df.columns:
        logger.warning(f'[同花顺批量] 缺少 {code_col} 列，实际列: {list(df.columns)}')
        return {
            'success_count': 0,
            'fail_count': len(a_stock_symbols),
            'source': '同花顺批量(字段异常)',
        }

    df_norm = df.copy()
    df_norm['_code6'] = df_norm[code_col].apply(_norm_code)

    success_count = 0
    fail_count = 0
    conn = get_connection()
    cursor = conn.cursor()

    for symbol in a_stock_symbols:
        stock_id = get_stock_id(symbol, 'a_stock')
        if not stock_id:
            fail_count += 1
            continue

        rows = df_norm[df_norm['_code6'] == symbol]
        if rows.empty:
            fail_count += 1
            logger.info(f'[同花顺批量] {symbol} 未在批量结果中命中')
            continue

        row = rows.iloc[0]
        # 净额为中文金额格式（如"-6.78亿"/"3.19亿"），解析后单位为元，÷1e4 转万元
        main_net_yuan = _parse_cn_amount(row.get('净额'))
        turnover_yuan = _parse_cn_amount(row.get('成交额'))

        if main_net_yuan is None:
            fail_count += 1
            continue

        main_net = round(main_net_yuan / 1e4, 2)  # 元→万元
        main_net_pct = round(main_net_yuan / turnover_yuan * 100, 2) if turnover_yuan else None

        cursor.execute(
            """
            INSERT OR REPLACE INTO raw_capital_flow
            (stock_id, trade_date, main_net_inflow, main_net_inflow_pct)
            VALUES (?, ?, ?, ?)
        """,
            (stock_id, today_str, main_net, main_net_pct),
        )
        success_count += 1

    conn.commit()
    conn.close()
    logger.info(
        f'[同花顺批量] 批量预取完成: 成功 {success_count}/{len(a_stock_symbols)}，source=同花顺批量'
    )
    return {'success_count': success_count, 'fail_count': fail_count, 'source': '同花顺批量'}


def _get_em_market_code(symbol):
    """根据股票代码返回东方财富市场标识（'sh' 或 'sz'）"""
    if symbol.startswith('6') or symbol.startswith('9'):
        return 'sh'
    else:
        return 'sz'


def _parse_cn_amount(val_str):
    """
    解析中文金额格式：'65.14亿' -> 6514000000, '-7200.36万' -> -72003600
    使用 round 解决浮点精度问题（如 20.65*1e8=2064999999.9999998）
    """
    if val_str is None or val_str == '' or (isinstance(val_str, float) and pd.isna(val_str)):
        return None
    val_str = str(val_str).strip()
    try:
        if '亿' in val_str:
            return round(float(val_str.replace('亿', '')) * 1e8, 2)
        elif '万' in val_str:
            return round(float(val_str.replace('万', '')) * 1e4, 2)
        else:
            return round(float(val_str), 2)
    except ValueError:
        return None


def _fetch_capital_flow_em_individual(symbol, market):
    """
    直接请求东方财富个股资金流向接口（不走akshare，避免代理干扰）。
    使用 _http_get_em 实现直连+代理智能回退+多轮重试。
    返回 list[dict]（含120天历史数据）或 None。
    支持A股和港股（通过secid区分）。
    """
    # 获取secid（A股: 0/1.代码, 港股: 116.5位代码）
    secid = _get_em_secid(symbol, market)

    url = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
    params = {
        'lmt': '0',
        'klt': '101',
        'secid': secid,
        'fields1': 'f1,f2,f3,f7',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
    }

    logger.info(f'请求东方财富个股资金流向: {symbol} (market={market}, secid={secid})')

    try:
        resp = _http_get_em(url, params=params, max_retries=1)
        data = resp.json()

        klines = data.get('data', {}).get('klines', [])
        if not klines:
            logger.warning(f'{symbol} 东方财富个股资金流向返回空数据')
            return None

        # 解析逗号分隔的数据行
        # 格式: 日期,主力净额,小单净额,中单净额,大单净额,超大单净额,主力占比,小单占比,中单占比,大单占比,超大单占比,收盘价,涨跌幅,-,-
        results = []
        for line in klines:
            parts = line.split(',')
            if len(parts) < 13:
                continue
            row = {
                '日期': parts[0],
                '主力净流入-净额': float(parts[1]) if parts[1] else 0,
                '小单净流入-净额': float(parts[2]) if parts[2] else 0,
                '中单净流入-净额': float(parts[3]) if parts[3] else 0,
                '大单净流入-净额': float(parts[4]) if parts[4] else 0,
                '超大单净流入-净额': float(parts[5]) if parts[5] else 0,
                '主力净流入-净占比': float(parts[6]) if parts[6] else 0,
                '小单净流入-净占比': float(parts[7]) if parts[7] else 0,
                '中单净流入-净占比': float(parts[8]) if parts[8] else 0,
                '大单净流入-净占比': float(parts[9]) if parts[9] else 0,
                '超大单净流入-净占比': float(parts[10]) if parts[10] else 0,
                '收盘价': float(parts[11]) if parts[11] else 0,
                '涨跌幅': float(parts[12]) if parts[12] else 0,
            }
            results.append(row)

        logger.info(f'{symbol} 获取到 {len(results)} 天资金流向历史数据')
        return results
    except Exception as e:
        logger.error(f'{symbol} 东方财富个股资金流向异常: {e}')
        return None


def _get_em_secid(symbol, market):
    """获取东方财富的 secid 格式"""
    if market == 'hk_stock':
        hk_code = _normalize_hk_symbol(symbol)
        return f'116.{hk_code}'
    elif market == 'a_stock':
        if symbol.startswith('6'):
            return f'1.{symbol}'
        else:
            return f'0.{symbol}'
    return f'0.{symbol}'


def _fetch_capital_flow_em(symbol, market):
    """从东方财富 push2 接口获取资金流向数据"""
    secid = _get_em_secid(symbol, market)
    url = 'https://push2.eastmoney.com/api/qt/stock/fflow/daykline/get'
    params = {
        'secid': secid,
        'lmt': 10,  # 最近10天
        'klt': '101',
        'fields1': 'f1,f2,f3,f7',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
    }
    logger.info(f'请求资金流向: secid={secid}, url={url}')
    resp = _http_get_em(url, params=params, max_retries=1)
    data = resp.json()

    klines = data.get('data', {}).get('klines', [])
    logger.info(f'资金流向响应: 获取到 {len(klines)} 条数据')
    if not klines:
        logger.warning(f'资金流向数据为空, 完整响应: {str(data)[:300]}')
    return klines


def _fetch_capital_flow_tencent_hk(symbol, market):
    """
    港股资金面估算fallback：从腾讯K线数据中提取成交额和涨跌幅，
    估算主力资金流向。仅在东方财富资金流接口不可用时使用。

    估算逻辑：
    - main_net_inflow = 日成交额(万港元) * 涨跌幅 / 100
    - main_net_inflow_pct = 涨跌幅（作为资金净流入占比的近似）
    - 其他分项（超大单/大单/中单/小单）留空

    返回 list[dict] 或 None。
    """
    if market != 'hk_stock':
        return None

    prefix, normalized_code = _get_tencent_prefix(symbol, market)
    tencent_code = f'{prefix}{normalized_code}'

    url = 'https://web.ifzq.gtimg.cn/appstock/app/hkfqkline/get'
    params = {'param': f'{tencent_code},day,,,{KLINE_DAYS},qfq'}

    try:
        resp = _http_get(url, params=params)
        data = resp.json()
        stock_data = data.get('data', {}).get(tencent_code, {})
        kline_list = stock_data.get('qfqday') or stock_data.get('day') or []

        if not kline_list:
            return None

        results = []
        for item in kline_list:
            # 港股格式: [date, open, close, high, low, volume, {extra}, change_pct, turnover_wan, ...]
            if len(item) < 6:
                continue
            trade_date = item[0]
            try:
                close = float(item[2])
                volume = float(item[5])
            except (ValueError, IndexError):
                continue

            # 涨跌幅（优先取腾讯返回的，否则从收盘价计算）
            change_pct = 0.0
            if len(item) > 7:
                try:
                    change_pct = float(item[7])
                except (ValueError, TypeError):
                    pass

            # 成交额（万港元）
            turnover_wan = 0.0
            if len(item) > 8:
                try:
                    turnover_wan = float(item[8])
                except (ValueError, TypeError):
                    pass
            if turnover_wan == 0 and volume > 0 and close > 0:
                # fallback: 成交量 * 收盘价 / 10000
                turnover_wan = volume * close / 1e4

            # 估算主力净流入 = 成交额 * 涨跌幅 / 100
            main_net = round(turnover_wan * change_pct / 100, 2)
            main_net_pct = round(change_pct, 2)

            results.append(
                {
                    '日期': trade_date,
                    '主力净流入-净额': main_net,
                    '主力净流入-净占比': main_net_pct,
                    '小单净流入-净额': 0,
                    '中单净流入-净额': 0,
                    '大单净流入-净额': 0,
                    '超大单净流入-净额': 0,
                }
            )

        logger.info(f'[{symbol}] 腾讯资金面估算: {len(results)}天数据')
        return results if results else None

    except Exception as e:
        logger.warning(f'[{symbol}] 腾讯资金面估算失败: {e}')
        return None


def _fetch_capital_flow_sina(symbol, market):
    """
    新浪财经资金面Fallback：从新浪实时行情中提取大单/中单/小单资金流向。
    数据源：hq.sinajs.cn（直连不需代理）
    返回 list[dict]（仅当日快照）或 None。
    """
    import re

    try:
        # 新浪代码格式：sh600276 / sz000333 / hk03690
        if market == 'hk_stock':
            sina_code = 'hk' + _normalize_hk_symbol(symbol)
        elif symbol.startswith('6'):
            sina_code = 'sh' + symbol
        else:
            sina_code = 'sz' + symbol

        url = f'http://hq.sinajs.cn/list={sina_code}'
        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {'User-Agent': _random_ua(), 'Referer': 'https://finance.sina.com.cn'}
        )
        resp = session.get(url, timeout=(5, 10), proxies={'http': None, 'https': None})
        resp.encoding = 'gbk'
        text = resp.text

        # 新浪行情格式（A股）：var hq_str_sh600276="名称,开盘,昨收,最新价,最高,..."
        # 新浪行情格式（港股）：var hq_str_hk03690="名称,开盘,昨收,..."
        match = re.search(r'="([^"]+)"', text)
        if not match:
            logger.warning(f'[{symbol}] 新浪资金面返回空数据')
            return None

        parts = match.group(1).split(',')
        if len(parts) < 10:
            return None

        trade_date = datetime.now(_CN_TZ).strftime('%Y-%m-%d')

        # 从新浪行情中估算资金流向（涨跌幅 * 成交量 * 收盘价）
        if market == 'hk_stock':
            close = float(parts[6]) if parts[6] else 0
            prev_close = float(parts[3]) if parts[3] else 0
            volume = float(parts[12]) if len(parts) > 12 and parts[12] else 0
        else:
            close = float(parts[3]) if parts[3] else 0
            prev_close = float(parts[2]) if parts[2] else 0
            volume = float(parts[8]) if len(parts) > 8 and parts[8] else 0

        if close <= 0 or prev_close <= 0:
            return None

        change_pct = (close - prev_close) / prev_close * 100
        turnover_wan = volume * close / 1e4 if volume > 0 else 0
        main_net = round(turnover_wan * change_pct / 100, 2)

        results = [
            {
                '日期': trade_date,
                '主力净流入-净额': main_net,
                '主力净流入-净占比': round(change_pct, 2),
                '小单净流入-净额': 0,
                '中单净流入-净额': 0,
                '大单净流入-净额': 0,
                '超大单净流入-净额': 0,
            }
        ]

        logger.info(f'[{symbol}] 新浪资金面快照: 1天数据, main_net={main_net}万')
        return results

    except Exception as e:
        logger.warning(f'[{symbol}] 新浪资金面失败: {e}')
        return None


def _fetch_capital_flow_netease(symbol, market):
    """
    网易财经资金面Fallback：从网易历史行情接口获取历史资金流向。
    数据源：quotes.money.163.com（直连不需代理）
    返回 list[dict]（历史数据）或 None。
    """
    try:
        # 网易代码格式：0600276(上海前缀0) / 1000333(深圳前缀1) / 203690(港股前缀2)
        if market == 'hk_stock':
            ne_code = '2' + _normalize_hk_symbol(symbol)
            base_url = 'http://quotes.money.163.com/service/zhdkline'
            params = {
                'code': ne_code,
                'fields': 'DATE;CLOSE;HIGH;LOW;VOLUME;AMOUNT;CHGP',
                'count': str(KLINE_DAYS),
            }
        else:
            ne_code = ('0' if symbol.startswith('6') else '1') + symbol
            base_url = 'http://quotes.money.163.com/service/zhdkline'
            params = {
                'code': ne_code,
                'fields': 'DATE;CLOSE;HIGH;LOW;VOLUME;AMOUNT;CHGP',
                'count': str(KLINE_DAYS),
            }

        session = requests.Session()
        session.trust_env = False
        session.headers.update(
            {'User-Agent': _random_ua(), 'Referer': 'http://quotes.money.163.com'}
        )
        resp = session.get(
            base_url, params=params, timeout=(5, 10), proxies={'http': None, 'https': None}
        )
        resp.raise_for_status()
        resp.encoding = 'gbk'

        lines = resp.text.strip().split('\n')
        if len(lines) < 2:
            return None

        # 解析CSV（第一行是表头，跳过）
        results = []
        for line in lines[1:]:
            cols = [c.strip() for c in line.split(';')]
            if len(cols) < 7:
                continue
            try:
                trade_date = cols[0]
                close = float(cols[1])
                change_pct = float(cols[6]) if cols[6] else 0
                volume = float(cols[4]) if cols[4] else 0
                amount = float(cols[5]) if cols[5] else 0

                # 网易amount单位为元，转为万元
                turnover_wan = amount / 1e4 if amount > 0 else 0
                if turnover_wan == 0 and volume > 0 and close > 0:
                    turnover_wan = volume * close / 1e4

                main_net = round(turnover_wan * change_pct / 100, 2)
                results.append(
                    {
                        '日期': trade_date,
                        '主力净流入-净额': main_net,
                        '主力净流入-净占比': round(change_pct, 2),
                        '小单净流入-净额': 0,
                        '中单净流入-净额': 0,
                        '大单净流入-净额': 0,
                        '超大单净流入-净额': 0,
                    }
                )
            except (ValueError, IndexError):
                continue

        logger.info(f'[{symbol}] 网易资金面估算: {len(results)}天数据')
        return results if results else None

    except Exception as e:
        logger.warning(f'[{symbol}] 网易资金面失败: {e}')
        return None


def fetch_capital_flow(symbol, market):
    """
    采集资金面数据。
    P3-A验收前置修复后策略：仅使用东方财富真实数据源，禁用所有估算源。
    Layer 1: 东方财富 push2his 个股历史资金流向（A股+港股）
    Layer 2: 东方财富 push2 实时资金流向（A股+港股）
    Layer 3: akshare stock_individual_fund_flow（仅A股，底层仍为东方财富）
    全部失败时返回failed，不降级到估算源（新浪/腾讯/网易已禁用）。
    同日已有真实数据时自动跳过采集（防覆盖机制）。
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    warnings = []
    saved_count = 0
    source = ''

    # ============================================================
    # P0-CAPITAL-001 前置校验层：同花顺批量预取/东财已有的当日真实数据，
    # 一律视为已采集完成，跳过本次逐只采集（从根因消除东方财富批量限流）。
    # 仅作为前置 gate，不修改下方 L1091 防覆盖 / L1225 early return 既有逻辑。
    # ============================================================
    today_str_pre = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    conn_pre = get_connection()
    cursor_pre = conn_pre.cursor()
    cursor_pre.execute(
        'SELECT COUNT(*) AS cnt FROM raw_capital_flow WHERE stock_id = ? AND trade_date = ?',
        (stock_id, today_str_pre),
    )
    pre_cnt = cursor_pre.fetchone()['cnt']
    conn_pre.close()
    if pre_cnt > 0:
        skip_msg = f'同日跳过(已有真实资金流数据,记录数={pre_cnt})'
        logger.info(f'[{symbol}] {skip_msg}（同花顺批量预取或东方财富已写入）')
        save_data_status(stock_id, 'capital', 'success', skip_msg)
        return 'success', f'今日已有真实资金流数据（{pre_cnt}条），跳过采集'

    # ============================================================
    # 同日真实数据防覆盖机制（P3-A验收前置修复）
    # 若今日已通过东方财富成功采集资金流数据，跳过本次采集
    # 防止后续操作触发fallback用估算值覆盖真实数据
    # ============================================================
    today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
    conn_skip = get_connection()
    cursor_skip = conn_skip.cursor()
    cursor_skip.execute(
        'SELECT message FROM data_status WHERE stock_id = ? AND dimension = ? '
        'AND fetched_at LIKE ? ORDER BY fetched_at DESC LIMIT 1',
        (stock_id, 'capital', today_str + '%'),
    )
    skip_row = cursor_skip.fetchone()
    conn_skip.close()
    if skip_row and skip_row['message']:
        _src_msg = skip_row['message']
        if _src_msg.startswith('东方财富'):
            logger.info(f'[{symbol}] 今日已有东方财富真实资金流数据，跳过采集（防覆盖）')
            save_data_status(
                stock_id, 'capital', 'success', f'同日跳过(已有真实数据): {_src_msg[:60]}'
            )
            return 'success', '今日已有东方财富真实资金流数据，跳过采集（防覆盖）'

    # === 主数据源：东方财富个股资金流向历史（A股+港股，secid区分）===
    try:
        rows_data = _fetch_capital_flow_em_individual(symbol, market)
        if rows_data:
            conn = get_connection()
            cursor = conn.cursor()

            for row in rows_data:
                trade_date = str(row.get('日期', '')).strip()
                if not trade_date:
                    continue

                # 东财返回的金额单位为元，写入DB前统一转换为万元（÷10000，保留2位小数）
                # 占比字段（%）不转换
                main_net = round(float(row.get('主力净流入-净额', 0) or 0) / 1e4, 2)
                main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)
                super_large = round(float(row.get('超大单净流入-净额', 0) or 0) / 1e4, 2)
                large = round(float(row.get('大单净流入-净额', 0) or 0) / 1e4, 2)
                medium = round(float(row.get('中单净流入-净额', 0) or 0) / 1e4, 2)
                small = round(float(row.get('小单净流入-净额', 0) or 0) / 1e4, 2)

                cursor.execute(
                    """
                    INSERT OR REPLACE INTO raw_capital_flow
                    (stock_id, trade_date, main_net_inflow, main_net_inflow_pct,
                     super_large_net, large_net, medium_net, small_net)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        trade_date,
                        main_net,
                        main_net_pct,
                        super_large,
                        large,
                        medium,
                        small,
                    ),
                )
                saved_count += 1

            conn.commit()
            conn.close()
            source = '东方财富(个股历史)'
            logger.info(f'[{symbol}] 资金面保存成功: {saved_count}天历史数据')
        else:
            warnings.append('东方财富个股资金流向返回空数据')
    except Exception as e:
        warnings.append(f'东方财富个股资金流向获取失败: {e}')
        logger.warning(f'[{symbol}] 东方财富个股资金流向获取失败: {e}')

    # === 备用数据源1：东方财富 push2（港股必须走这里）===
    if saved_count == 0:
        try:
            klines = _fetch_capital_flow_em(symbol, market)
            if klines is None:
                warnings.append('东方财富push2接口无法访问（直连和代理均失败）')
            elif klines:
                conn = get_connection()
                cursor = conn.cursor()

                for line in klines:
                    parts = line.split(',')
                    if len(parts) >= 6:
                        trade_date = parts[0]
                        # 元转万元，保留2位小数
                        main_net = round(float(parts[1]) / 1e4, 2) if parts[1] else 0
                        small_net = round(float(parts[2]) / 1e4, 2) if parts[2] else 0
                        medium_net = round(float(parts[3]) / 1e4, 2) if parts[3] else 0
                        large_net = round(float(parts[4]) / 1e4, 2) if parts[4] else 0
                        super_large_net = round(float(parts[5]) / 1e4, 2) if parts[5] else 0

                        cursor.execute(
                            """
                            INSERT OR REPLACE INTO raw_capital_flow
                            (stock_id, trade_date, main_net_inflow,
                             super_large_net, large_net, medium_net, small_net)
                            VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                            (
                                stock_id,
                                trade_date,
                                main_net,
                                super_large_net,
                                large_net,
                                medium_net,
                                small_net,
                            ),
                        )
                        saved_count += 1

                conn.commit()
                conn.close()
                source = '东方财富(push2)'
            else:
                warnings.append('东方财富push2资金流向数据为空')
        except Exception as e:
            warnings.append(f'东方财富push2获取失败: {e}')
            logger.warning(f'[{symbol}] 东方财富push2获取失败: {e}')

    # === 备用数据源2：akshare内置接口（最后降级方案，仅A股）===
    if saved_count == 0 and market == 'a_stock':
        try:
            logger.info(f'[{symbol}] 尝试akshare备用数据源...')
            df_ak = ak.stock_individual_fund_flow(stock=symbol, market=_get_em_market_code(symbol))
            if df_ak is not None and not df_ak.empty:
                conn = get_connection()
                cursor = conn.cursor()

                for _, row in df_ak.iterrows():
                    trade_date = str(row.get('日期', '')).strip()
                    if not trade_date:
                        continue

                    main_net = round(float(row.get('主力净流入-净额', 0) or 0) / 1e4, 2)
                    main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)
                    super_large = round(float(row.get('超大单净流入-净额', 0) or 0) / 1e4, 2)
                    large = round(float(row.get('大单净流入-净额', 0) or 0) / 1e4, 2)
                    medium = round(float(row.get('中单净流入-净额', 0) or 0) / 1e4, 2)
                    small = round(float(row.get('小单净流入-净额', 0) or 0) / 1e4, 2)

                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO raw_capital_flow
                        (stock_id, trade_date, main_net_inflow, main_net_inflow_pct,
                         super_large_net, large_net, medium_net, small_net)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (
                            stock_id,
                            trade_date,
                            main_net,
                            main_net_pct,
                            super_large,
                            large,
                            medium,
                            small,
                        ),
                    )
                    saved_count += 1

                conn.commit()
                conn.close()
                source = 'akshare(备用)'
                logger.info(f'[{symbol}] akshare备用源成功: {saved_count}天数据')
            else:
                warnings.append('akshare备用数据源返回空数据')
        except Exception as e:
            warnings.append(f'akshare备用源失败: {e}')
            logger.warning(f'[{symbol}] akshare备用源失败: {e}')

    # ============================================================
    # 估算数据源已禁用（P3-A验收前置修复）
    # 原Layer 3b(腾讯估算)/Layer 4(新浪估算)/Layer 5(网易估算)
    # 使用公式"成交额×涨跌幅/100"近似主力资金流向，
    # 与真实主力资金流向无相关性，不具备评分有效性。
    # 东方财富全部失败时直接返回失败，不再降级到估算源。
    # ============================================================
    if saved_count == 0:
        fail_msg = (
            '资金面数据不可用：东方财富接口全部失败（push2his/push2/akshare）。'
            '估算数据源（新浪/腾讯/网易）已禁用，不再写入估算值作为真实数据。'
            '引擎将使用最近交易日的真实资金流数据（T-1或更早）。'
        )
        save_data_status(stock_id, 'capital', 'failed', fail_msg)
        logger.warning(f'[{symbol}] {fail_msg}')
        return 'failed', fail_msg

    # === 以下估算数据源已禁用，保留代码供参考（已不可达）===
    # === 备用数据源3：腾讯K线估算（港股专用fallback，直连不需代理）===
    if False and saved_count == 0 and market == 'hk_stock':
        try:
            logger.info(f'[{symbol}] 东方财富资金流不可用，尝试腾讯K线估算...')
            tencent_rows = _fetch_capital_flow_tencent_hk(symbol, market)
            if tencent_rows:
                conn = get_connection()
                cursor = conn.cursor()

                for row in tencent_rows:
                    trade_date = str(row.get('日期', '')).strip()
                    if not trade_date:
                        continue

                    # 估算值已在_fetch中计算，单位为万元
                    main_net = round(float(row.get('主力净流入-净额', 0) or 0), 2)
                    main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)

                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO raw_capital_flow
                        (stock_id, trade_date, main_net_inflow, main_net_inflow_pct,
                         super_large_net, large_net, medium_net, small_net)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (stock_id, trade_date, main_net, main_net_pct, 0, 0, 0, 0),
                    )
                    saved_count += 1

                conn.commit()
                conn.close()
                source = '腾讯K线估算'
                logger.info(f'[{symbol}] 腾讯资金面估算成功: {saved_count}天数据')
            else:
                warnings.append('腾讯K线估算返回空数据')
        except Exception as e:
            warnings.append(f'腾讯K线估算失败: {e}')
            logger.warning(f'[{symbol}] 腾讯K线估算失败: {e}')

    # === 备用数据源4：新浪财经资金面（已禁用：估算源，P3-A验收前置修复）===
    # 与腾讯(L1235)保持一致风格，if False 硬禁用。
    # 实际本分支在 L1225 early return 之后已不可达，此为代码卫生性二次保护。
    if False and saved_count == 0:
        try:
            logger.info(f'[{symbol}] 东方财富资金流不可用，尝试新浪财经...')
            sina_rows = _fetch_capital_flow_sina(symbol, market)
            if sina_rows:
                conn = get_connection()
                cursor = conn.cursor()
                for row in sina_rows:
                    trade_date = str(row.get('日期', '')).strip()
                    if not trade_date:
                        continue
                    main_net = round(float(row.get('主力净流入-净额', 0) or 0), 2)
                    main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO raw_capital_flow
                        (stock_id, trade_date, main_net_inflow, main_net_inflow_pct,
                         super_large_net, large_net, medium_net, small_net)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (stock_id, trade_date, main_net, main_net_pct, 0, 0, 0, 0),
                    )
                    saved_count += 1
                conn.commit()
                conn.close()
                source = '新浪财经'
                logger.info(f'[{symbol}] 新浪资金面成功: {saved_count}天数据')
            else:
                warnings.append('新浪财经资金面返回空数据')
        except Exception as e:
            warnings.append(f'新浪财经资金面失败: {e}')
            logger.warning(f'[{symbol}] 新浪财经资金面失败: {e}')

    # === 备用数据源5：网易财经历史资金流向（已禁用：估算源，P3-A验收前置修复）===
    # 与腾讯(L1235)/新浪(L1272)保持一致风格，if False 硬禁用。
    if False and saved_count == 0:
        try:
            logger.info(f'[{symbol}] 尝试网易财经历史资金流向...')
            netease_rows = _fetch_capital_flow_netease(symbol, market)
            if netease_rows:
                conn = get_connection()
                cursor = conn.cursor()
                for row in netease_rows:
                    trade_date = str(row.get('日期', '')).strip()
                    if not trade_date:
                        continue
                    main_net = round(float(row.get('主力净流入-净额', 0) or 0), 2)
                    main_net_pct = round(float(row.get('主力净流入-净占比', 0) or 0), 2)
                    cursor.execute(
                        """
                        INSERT OR REPLACE INTO raw_capital_flow
                        (stock_id, trade_date, main_net_inflow, main_net_inflow_pct,
                         super_large_net, large_net, medium_net, small_net)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                        (stock_id, trade_date, main_net, main_net_pct, 0, 0, 0, 0),
                    )
                    saved_count += 1
                conn.commit()
                conn.close()
                source = '网易财经'
                logger.info(f'[{symbol}] 网易资金面成功: {saved_count}天数据')
            else:
                warnings.append('网易财经资金面返回空数据')
        except Exception as e:
            warnings.append(f'网易财经资金面失败: {e}')
            logger.warning(f'[{symbol}] 网易财经资金面失败: {e}')

    # === 所有数据源均失败时写入error_logs ===
    if saved_count == 0:
        try:
            conn_err = get_connection()
            cursor_err = conn_err.cursor()
            cursor_err.execute(
                """
                INSERT INTO error_logs (stock_id, module, error_type, error_message)
                VALUES (?, ?, ?, ?)
            """,
                (stock_id, 'capital_flow', 'all_sources_failed', '; '.join(warnings)[:500]),
            )
            conn_err.commit()
            conn_err.close()
        except Exception:
            pass

    if saved_count > 0:
        # 检查数据库中已有的历史记录数和最新日期
        conn_chk = get_connection()
        cursor_chk = conn_chk.cursor()
        cursor_chk.execute(
            'SELECT COUNT(*) as cnt, MAX(trade_date) as latest FROM raw_capital_flow WHERE stock_id = ?',
            (stock_id,),
        )
        row_chk = cursor_chk.fetchone()
        total_records = row_chk['cnt']
        latest_cap_date = row_chk['latest']
        conn_chk.close()

        # 检查K线最新日期
        conn_k = get_connection()
        cursor_k = conn_k.cursor()
        cursor_k.execute(
            'SELECT MAX(trade_date) as latest FROM raw_kline WHERE stock_id = ?', (stock_id,)
        )
        latest_kline_date = cursor_k.fetchone()['latest']
        conn_k.close()

        # 日期对齐策略说明（方案B：沿用T-1数据 + 标注截止日）
        date_note = ''
        if latest_kline_date and latest_cap_date and latest_kline_date > latest_cap_date:
            date_note = f'。注意：资金面截止日为{latest_cap_date}（K线最新日为{latest_kline_date}），四维分析时应沿用T-1资金面数据并在报告中标注截止日期'
            logger.info(
                f'[{symbol}] 资金面日期对齐: K线={latest_kline_date}, 资金面={latest_cap_date}, 采用T-1策略'
            )

        msg = f'{source}采集成功。已写入{saved_count}天历史数据，数据库累计{total_records}条记录{date_note}'
        save_data_status(stock_id, 'capital', 'success', msg)
        return 'success', msg
    else:
        all_warnings = '; '.join(warnings) if warnings else '所有数据源均失败'
        save_data_status(stock_id, 'capital', 'failed', all_warnings)
        return 'failed', all_warnings


# ============================================================
# DATASRC-C：北向资金净买入采集（仅沪深港通标的）
# 数据源：akshare stock_hsgt_individual_em（东方财富沪深港通个股）
# 字段映射：当日增持估计净买额(元) -> north_holding_change(万元)
# 覆盖范围：仅沪深港通标的，非标的填 None
# 采集频率：每日1次（T+0 收盘后）
# ============================================================


def fetch_north_capital(symbol, market, force_full=False):
    """
    采集北向资金净买入数据（DATASRC-C 子任务2.1）。
    仅对沪深港通标的有效，非标的直接跳过（填 None）。
    使用 UPDATE 写入 raw_capital_flow.north_holding_change，不破坏已有字段。
    011增量：30天缓存（数据源自2024-08-16停更）。
    失败时不阻塞主流程，仅记录 warning。

    Returns: (status, message)
    """
    if market != 'a_stock':
        # 北向资金仅 A 股，港股不受影响（A/H 双市场独立红线）
        return 'skipped', '北向资金仅A股标的'

    stock_id = get_stock_id(symbol, 'a_stock')
    if not stock_id:
        return 'failed', f'数据库中未找到A股 {symbol}'

    # 011增量：30天缓存检查
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT fetched_at FROM data_status
                   WHERE stock_id = ? AND dimension = ?
                   ORDER BY fetched_at DESC LIMIT 1""",
                (stock_id, 'north_capital'),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['fetched_at']:
                last_fetch = datetime.strptime(str(row['fetched_at'])[:19], '%Y-%m-%d %H:%M:%S')
                days_since = (datetime.now(_CN_TZ).replace(tzinfo=None) - last_fetch).days
                if days_since < NORTH_CAPITAL_CACHE_DAYS:
                    skip_msg = f'北向资金缓存有效({days_since}天/{NORTH_CAPITAL_CACHE_DAYS}天)'
                    logger.info(f'[DATASRC-C] {symbol} {skip_msg}')
                    return 'skipped', skip_msg
        except Exception as e:
            logger.warning(f'[DATASRC-C] {symbol} 北向资金缓存检查异常(降级): {e}')

    try:
        logger.info(f'[DATASRC-C] 北向资金采集: {symbol}')
        df = ak.stock_hsgt_individual_em(symbol=symbol)
        if df is None or df.empty:
            # 非沪深港通标的或接口无数据 -> 填 None（不填0，不估算）
            logger.info(f'[DATASRC-C] {symbol} 非沪深港通标的或无北向数据，跳过')
            return 'skipped', '非沪深港通标的（无北向资金数据）'

        # 取最近一条记录（最新日期）
        # 列名兼容：akshare 版本差异可能导致列名微调
        date_col = None
        net_buy_col = None
        for col in df.columns:
            if '日期' in str(col):
                date_col = col
            if '净买' in str(col) or '增持' in str(col):
                if '额' in str(col):
                    net_buy_col = col

        if date_col is None or net_buy_col is None:
            # 尝试按位置取（列顺序：持股日期,收盘价,涨跌幅,持股股数,持股市值,占比,增持股数,增持净买额,持股市值变化）
            cols = list(df.columns)
            if len(cols) >= 8:
                date_col = cols[0]
                net_buy_col = cols[7]  # 当日增持估计净买额
            else:
                logger.warning(f'[DATASRC-C] {symbol} 北向数据列名无法识别: {cols}')
                return 'failed', '北向数据列名无法识别'

        latest_row = df.iloc[-1]
        trade_date_raw = str(latest_row[date_col]).split(' ')[0]
        # 统一日期格式 YYYY-MM-DD
        trade_date = trade_date_raw.replace('/', '-')
        if len(trade_date) == 8 and trade_date.isdigit():
            trade_date = f'{trade_date[:4]}-{trade_date[4:6]}-{trade_date[6:8]}'

        # B26：北向数据源停更标注（ak.stock_hsgt_individual_em 自2024-08-16起停更，港交所政策变更）
        if trade_date < '2024-08-16':
            logger.info(
                f'[DATASRC-C] {symbol} 北向资金数据源停更，最新数据日期 {trade_date}，不影响评分（B26已降权至0.10）'
            )

        net_buy_yuan = latest_row[net_buy_col]
        if pd.isna(net_buy_yuan):
            logger.info(f'[DATASRC-C] {symbol} 最新北向净买额为NaN，跳过')
            return 'skipped', '北向净买额为NaN'

        # 元 -> 万元（保留2位小数）
        net_buy_wan = round(float(net_buy_yuan) / 1e4, 2)

        # UPDATE 写入（不 INSERT，避免破坏已有 main_net_inflow 等字段）
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE raw_capital_flow SET north_holding_change = ?
            WHERE stock_id = ? AND trade_date = ?
        """,
            (net_buy_wan, stock_id, trade_date),
        )
        updated = cursor.rowcount

        # 如果该日期无记录，INSERT 一条仅含 north 的记录（不覆盖其他字段）
        if updated == 0:
            cursor.execute(
                """
                INSERT OR IGNORE INTO raw_capital_flow
                (stock_id, trade_date, north_holding_change)
                VALUES (?, ?, ?)
            """,
                (stock_id, trade_date, net_buy_wan),
            )
            updated = cursor.rowcount

        conn.commit()
        conn.close()

        logger.info(
            f'[DATASRC-C] {symbol} 北向资金写入成功: {trade_date} net_buy={net_buy_wan}万元'
        )
        save_data_status(
            stock_id, 'north_capital', 'success', f'北向资金净买入 {net_buy_wan}万元 ({trade_date})'
        )
        return 'success', f'北向资金净买入 {net_buy_wan}万元 ({trade_date})'

    except Exception as e:
        logger.warning(f'[DATASRC-C] {symbol} 北向资金采集失败(不阻塞): {e}')
        save_data_status(stock_id, 'north_capital', 'failed', f'北向资金采集失败: {e}')
        return 'failed', f'北向资金采集失败: {e}'


# ============================================================
# DATASRC-C：融资余额采集（仅融资融券标的）
# 数据源：akshare stock_margin_detail_sse / stock_margin_detail_szse
# 字段映射：融资余额(元) -> margin_balance(万元)
# 覆盖范围：仅融资融券标的（约2200只），非标的填 None
# 采集频率：每日1次（T+1 公布）
# ============================================================

# 融资融券数据缓存（按日期缓存全市场数据，避免逐只重复请求）
_MARGIN_CACHE = {'sse': {}, 'szse': {}}  # {date_str: DataFrame}


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


# ============================================================
# 消息面数据采集
# ============================================================


def fetch_sentiment(symbol, market, force_full=False):
    """
    采集消息面数据（模块4接入）。
    调用 news_collector 采集新闻 + 情绪分析。
    011增量：当日已有 → 跳过。
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 011增量：当日跳过
    if not force_full:
        try:
            today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT COUNT(*) as cnt FROM news_sentiment
                   WHERE stock_id = ? AND news_date LIKE ?""",
                (stock_id, today_str + '%'),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['cnt'] > 0:
                skip_msg = f'当日跳过(消息面已有{row["cnt"]}条记录)'
                save_data_status(stock_id, 'sentiment', 'success', skip_msg)
                logger.info(f'[{symbol}] {skip_msg}')
                return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[{symbol}] 消息面增量检查异常(降级为全量): {e}')

    market_name = 'A股' if market == 'a_stock' else '港股'

    try:
        from modules.news_collector import collect_news

        status, msg = collect_news(stock_id, symbol, market)
        save_data_status(stock_id, 'sentiment', status, msg)
        logger.info(f'[{market_name} {symbol}] 消息面采集完成: {status}')
        return status, msg
    except Exception as e:
        error_msg = f'{market_name} {symbol} 消息面采集异常: {str(e)}'
        logger.error(error_msg, exc_info=True)
        save_data_status(stock_id, 'sentiment', 'failed', error_msg)
        return 'failed', error_msg


# ============================================================
# INDUSTRY-DYNAMIC：行业分类动态获取
# ============================================================

# B14: 行业本地映射兜底（akshare API 被封时使用）
# 数据来源：东方财富行业分类（2026-07 手动确认）
_LOCAL_INDUSTRY_MAP = {
    '000333': '家电行业',
    '000858': '酿酒行业',
    '000977': '计算机设备',
    '002230': '通信设备',
    '002352': '物流行业',
    '002415': '安防设备',
    '002458': '禽畜养殖',
    '002714': '食品加工',
    '300015': '医疗服务',
    '300124': '电气设备',
    '300146': '保健食品',
    '300750': '电池',
    '600276': '医药制造',
    '600519': '酿酒行业',
    '601012': '光伏设备',
    '601888': '旅游酒店',
    '688017': '半导体',
    '688041': '半导体',
    '688047': '半导体',
    '688795': '半导体',
    '688802': '半导体',
    '688981': '半导体',
}


def fetch_stock_industry(symbol: str, market: str = 'a_stock') -> str:
    """获取个股行业分类。
    A股：优先 akshare stock_individual_info_em API，失败时使用本地映射兜底。
    港股：无免费行业接口，默认返回“港股”。
    获取失败时返回“未分类”，不阻塞主流程。
    """
    if market == 'hk_stock' or symbol.upper().startswith('HK'):
        return '港股'
    # 优先尝试 API
    try:
        df = ak.stock_individual_info_em(symbol=symbol)
        if df is not None and not df.empty:
            row = df[df['item'] == '行业']
            if not row.empty:
                val = str(row.iloc[0]['value']).strip()
                if val:
                    return val
    except Exception as e:
        logger.warning(f'[{symbol}] 行业API失败，尝试本地映射: {e}')
    # B14: API 失败时兜底本地映射
    local = _LOCAL_INDUSTRY_MAP.get(symbol)
    if local:
        return local
    return '未分类'


# ============================================================
# 统一采集入口
# ============================================================


def collect_stock_data(symbol, market, force_full=False):
    """
    对外统一接口：根据市场类型，自动调用对应的数据采集函数。
    011增加 force_full 参数透传（强制全量采集，绕过所有增量缓存）。

    参数:
        symbol: 股票代码（如 '000001' 或 '00700'）
        market: 市场（'a_stock' 或 'hk_stock'）
        force_full: True时绕过所有增量缓存，全量采集

    返回:
        dict: 各维度采集状态汇总
    """
    results = {}
    market_name = 'A股' if market == 'a_stock' else '港股'
    ff = '[FULL_REFRESH] ' if force_full else ''

    # 012-C: 获取 stock_id 用于错误日志记录
    stock_id = get_stock_id(symbol, market)

    logger.info(f'========== 开始采集{market_name} {symbol} {ff}==========')

    # K线（A股和港股统一用腾讯接口）
    try:
        results['kline'] = fetch_kline(symbol, market, force_full=force_full)
    except Exception as e:
        results['kline'] = ('failed', f'K线采集异常: {e}')
        logger.warning(f'[{symbol}] K线采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='kline',
            traceback_str=traceback.format_exc(),
        )

    # 基本面
    if market == 'a_stock':
        try:
            results['fundamental'] = fetch_a_fundamental(symbol, force_full=force_full)
        except Exception as e:
            results['fundamental'] = ('failed', f'基本面采集异常: {e}')
            logger.warning(f'[{symbol}] 基本面采集失败: {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='fundamental',
                traceback_str=traceback.format_exc(),
            )
        # B10: 基本面字段补全（仅填充NULL字段，不覆盖已有值）
        try:
            if stock_id:
                # B11-API-DEDUP：检查 fetch_a_fundamental 是否已获取足够数据，避免重复调用
                conn_chk = get_connection()
                cursor_chk = conn_chk.cursor()
                cursor_chk.execute(
                    """
                    SELECT roe, gross_margin, revenue_growth, profit_growth,
                           ocf_to_net_profit, debt_ratio, current_ratio
                    FROM raw_fundamental WHERE stock_id=? ORDER BY report_date DESC LIMIT 1
                """,
                    (stock_id,),
                )
                fund_row = cursor_chk.fetchone()
                conn_chk.close()

                need_detail = True
                if fund_row:
                    non_null = sum(
                        1
                        for v in [
                            fund_row['roe'],
                            fund_row['gross_margin'],
                            fund_row['revenue_growth'],
                            fund_row['profit_growth'],
                            fund_row['ocf_to_net_profit'],
                            fund_row['debt_ratio'],
                            fund_row['current_ratio'],
                        ]
                        if v is not None
                    )
                    if non_null >= 5:
                        need_detail = False
                        logger.info(
                            f'[{symbol}] 基本面已有{non_null}个字段，跳过 fetch_fundamental_detail'
                        )

                if need_detail:
                    detail = fetch_fundamental_detail(symbol)
                    _apply_fundamental_detail(stock_id, detail)

                # B10: 股东增减持采集
                holder_val = fetch_holder_increase(symbol)
                _save_holder_increase(stock_id, holder_val)
                results['holder_increase'] = ('success', f'holder_increase={holder_val}')
        except Exception as e:
            logger.warning(f'[{symbol}] B10基本面补全/股东增减持异常(不阻塞): {e}')
    else:
        try:
            results['fundamental'] = fetch_hk_fundamental(symbol, force_full=force_full)
        except Exception as e:
            results['fundamental'] = ('failed', f'港股基本面采集异常: {e}')
            logger.warning(f'[{symbol}] 港股基本面采集失败: {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='fundamental',
                traceback_str=traceback.format_exc(),
            )

    # 资金面（A股和港股统一用东方财富push2接口）
    # 红线：fetch_capital_flow 不加 force_full 参数（同日跳过基于已有真实数据，不重复采集）
    try:
        results['capital'] = fetch_capital_flow(symbol, market)
    except Exception as e:
        results['capital'] = ('failed', f'资金面采集异常: {e}')
        logger.warning(f'[{symbol}] 资金面采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='capital',
            traceback_str=traceback.format_exc(),
        )

    # DATASRC-C：北向资金 + 融资融券补齐（仅 A股，失败不阻塞主流程）
    if market == 'a_stock':
        try:
            results['north_capital'] = fetch_north_capital(symbol, market, force_full=force_full)
        except Exception as e:
            results['north_capital'] = ('failed', f'北向资金采集异常: {e}')
            logger.warning(f'[{symbol}] 北向资金采集异常(不阻塞): {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='north',
                traceback_str=traceback.format_exc(),
            )
        try:
            results['margin_balance'] = fetch_margin_balance(symbol, market, force_full=force_full)
        except Exception as e:
            results['margin_balance'] = ('failed', f'融资余额采集异常: {e}')
            logger.warning(f'[{symbol}] 融资余额采集异常(不阻塞): {e}')
            _log_error_to_db(
                stock_id,
                'data_collector',
                type(e).__name__,
                str(e),
                dimension='margin',
                traceback_str=traceback.format_exc(),
            )

    # 消息面
    try:
        results['sentiment'] = fetch_sentiment(symbol, market, force_full=force_full)
    except Exception as e:
        results['sentiment'] = ('failed', f'消息面采集异常: {e}')
        logger.warning(f'[{symbol}] 消息面采集失败: {e}')
        _log_error_to_db(
            stock_id,
            'data_collector',
            type(e).__name__,
            str(e),
            dimension='sentiment',
            traceback_str=traceback.format_exc(),
        )

    logger.info(f'========== {symbol} 数据采集完成 {ff}==========')
    return results
