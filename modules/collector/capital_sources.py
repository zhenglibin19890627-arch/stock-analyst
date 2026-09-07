"""资金面·其余源（腾讯HK/新浪/网易/新浪主口径 lscjfb）+ 北向（已停更保留兼容）。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
import json
import random as _random
import time
import urllib.request as _urlreq
from datetime import datetime

import akshare as ak
import pandas as pd
import requests

from config import KLINE_DAYS, NORTH_CAPITAL_CACHE_DAYS
from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.capital_em import _safe_float_wan
from modules.collector.http_client import (
    _SINA_REQUEST_TIMEOUT,
    _call_with_timeout,
    _http_get,
    _random_ua,
)
from modules.collector.symbols_status import (
    _get_tencent_prefix,
    _normalize_hk_symbol,
    get_stock_id,
    save_data_status,
)


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


def _fetch_capital_flow_sina_main(symbol, market, target_date=None):
    """
    019Q Task 1：新浪资金流主力口径采集（lscjfb 历史逐日分单接口，A股）。

    ⚠️ 命名规避（M-1）：既有估算源 _fetch_capital_flow_sina（hq.sinajs.cn 实时行情估算，
    019E 链路）零改动；本函数为新增主力口径源，命名为 _fetch_capital_flow_sina_main。

    网络规格（M-3/D-4/M-10）：
    - 协议 https 优先、失败回退 http（仅回退 1 次，不做代理尝试）
    - GBK 解码（errors='replace'）；必须带 UA（_random_ua()）+ Referer https://finance.sina.com.cn
    - 禁用系统代理：urllib.build_opener(ProxyHandler({}))
    - 全部网络调用（含 https 回退的第二次请求）走模块级 _call_with_timeout（单次 15s）
    - 每只请求后间隔 0.5~1.0s（防限流；29 只串行上限 ~29s，仅在 EM 失败路径发生）

    严格日期匹配（M-2，正确性红线）：lscjfb 是历史逐日表，非交易日/当日未发布时
    "最新行"即上一交易日（探针实证：周日最新行=08-07）。必须 opendate == target_date
    精确匹配才返回，不匹配一律返回 None 落回下一层，严禁"取最新行"实现。

    Args:
        symbol: str 股票代码（A股，6开头→sh 前缀，0/3开头→sz 前缀；港股不适用返回 None）
        market: str 市场（'a_stock'）
        target_date: str YYYY-MM-DD；None 表示当日（当日采集 num=2，回补窗口 num=15）

    Returns:
        dict 或 None：
        {'trade_date', 'main_net_inflow', 'super_large_net', 'large_net',
         'medium_net', 'small_net'}（万元，round 2）
        main_net_inflow = (r0_net + r1_net) / 1e4（主力 = 超大单 + 大单，与 EM 同定义，
        行内自洽 main == super_large + large）；四档之和 == netamount/1e4（新浪恒等式）。
        不写 main_net_inflow_pct（lscjfb ratioamount 为总净占比 netamount/turnover，
        非主力净流入占比，口径错位，M-4/D-6）。
    """
    try:
        if market != 'a_stock' or not symbol:
            return None

        # A股 symbol → 新浪 daima 映射（与 019K D-6 一致，港股不适用）
        daima = ('sh' + symbol) if symbol.startswith('6') else ('sz' + symbol)

        if target_date is None:
            target_date = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
            num = 2  # 当日采集：覆盖今日+上一交易日
        else:
            num = 15  # 回补窗口：覆盖目标日期（15 个交易日窗口，覆盖近 10 交易日完整性口径）

        def _request_once(proto):
            url = (
                f'{proto}://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/'
                f'MoneyFlow.ssl_qsfx_lscjfb?page=1&num={num}&sort=opendate&asc=0&daima={daima}'
            )
            req = _urlreq.Request(
                url,
                headers={'User-Agent': _random_ua(), 'Referer': 'https://finance.sina.com.cn'},
            )
            opener = _urlreq.build_opener(_urlreq.ProxyHandler({}))  # 禁用系统代理
            resp = opener.open(req, timeout=_SINA_REQUEST_TIMEOUT)
            return resp.read().decode('gbk', errors='replace')

        # https 优先、失败回退 http（仅回退 1 次）；两次均走 _call_with_timeout（M-10）
        text, _timed_out = _call_with_timeout(
            lambda: _request_once('https'), f'新浪lscjfb https({symbol})'
        )
        if text is None:
            text, _ = _call_with_timeout(
                lambda: _request_once('http'), f'新浪lscjfb http回退({symbol})'
            )
        if text is None:
            logger.warning(f'[{symbol}] 新浪lscjfb https/http 均失败或超时')
            return None

        # JSON 解析健壮性（R-8）：接口偶发 null/非严格 JSON；非数组/空 → None
        data = None
        try:
            data = json.loads(text)
        except Exception:
            try:
                start = text.find('[')
                end = text.rfind(']')
                if start >= 0 and end > start:
                    data = json.loads(text[start:end + 1])
            except Exception:
                data = None
        if not isinstance(data, list) or not data:
            logger.warning(f'[{symbol}] 新浪lscjfb 返回非数组或为空')
            return None

        # 严格日期匹配：opendate == target_date 才写，严禁"取最新行"（M-2）
        for row in data:
            if not isinstance(row, dict):
                continue
            opendate = str(row.get('opendate', '') or '').strip()
            if opendate != target_date:
                continue
            # 金额统一走 _safe_float_wan（019N 模式，元→万元，None 语义）
            r0_wan = _safe_float_wan(row.get('r0_net'))  # 超大单净额
            r1_wan = _safe_float_wan(row.get('r1_net'))  # 大单净额
            r2_wan = _safe_float_wan(row.get('r2_net'))  # 中单净额
            r3_wan = _safe_float_wan(row.get('r3_net'))  # 小单净额
            if any(v is None for v in (r0_wan, r1_wan, r2_wan, r3_wan)):
                logger.warning(f'[{symbol}] 新浪lscjfb {opendate} 分单字段缺失，放弃')
                return None
            main_wan = round(r0_wan + r1_wan, 2)  # 主力 = 超大单 + 大单（行内自洽）
            logger.info(
                f'[{symbol}] 新浪lscjfb 命中 {opendate}: main={main_wan} 万'
                f'（r0={r0_wan}, r1={r1_wan}, r2={r2_wan}, r3={r3_wan}）'
            )
            return {
                'trade_date': opendate,
                'main_net_inflow': main_wan,
                'super_large_net': r0_wan,
                'large_net': r1_wan,
                'medium_net': r2_wan,
                'small_net': r3_wan,
            }
        # 无当日行（如非交易日最新行=上一交易日）→ 不写入，落回下一层
        logger.info(f'[{symbol}] 新浪lscjfb 无 {target_date} 行（最新行=上一交易日），返回 None')
        return None
    except Exception as e:
        logger.warning(f'[{symbol}] 新浪lscjfb 顶替采集异常: {e}')
        return None
    finally:
        # 每只请求后间隔 0.5~1.0s（防限流）
        time.sleep(_random.uniform(0.5, 1.0))


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
