"""估值（akshare→baostock 降级）+ baostock 登录生命周期 + 限售解禁。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
from datetime import datetime, timedelta

import akshare as ak
import pandas as pd

from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.capital_em import _safe_num
from modules.collector.http_client import _call_ak_with_timeout, _http_get, retry
from modules.collector.symbols_status import _get_tencent_prefix, get_stock_id, save_data_status


@retry
def _fetch_valuation_tencent(symbol, market):
    """
    从腾讯实时行情接口获取 PE/PB/总市值 估值数据（A股+港股统一）。
    注意：A股和港股的字段索引不同！
      A股: [39]=PE(TTM), [46]=PB, [45]=总市值(亿元)
      港股: [39]=PE(TTM), [43]=PB  (港股[46]是英文股票名而非PB)
           [45]=总市值(亿港元, 优先), [44]=流通市值/港股市值(亿港元, 兜底)
    返回: (pe, pb, total_mv_元)

    021M：A股也返回总市值（[45] 字段，单位亿元→元），与港股统一处理。
    腾讯接口实时返回当天数据，无 T+1 延迟，作为估值核心数据源。
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

    # 021M：总市值（A股+港股统一从 [45] 获取，单位=亿，换算为元）
    # A股 [45]=总市值(亿元)；港股 [45]=总市值(亿港元)，兜底 [44]
    total_mv = None
    if market == 'hk_stock':
        # 港股优先 [45] 总市值，兜底 [44] 流通市值
        for idx in (45, 44):
            if len(parts) > idx:
                mv_str = parts[idx].strip().strip('"').strip(';')
                if mv_str:
                    try:
                        total_mv = float(mv_str) * 1e8
                        break
                    except ValueError:
                        continue
    else:
        # A股 [45]=总市值(亿元)
        if len(parts) > 45:
            mv_str = parts[45].strip().strip('"').strip(';')
            if mv_str:
                try:
                    total_mv = float(mv_str) * 1e8  # 亿元 → 元
                except ValueError:
                    pass

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

    logger.info(f'腾讯估值 {symbol}: PE={pe}, PB={pb}, 总市值={total_mv} (market={market})')
    return pe, pb, total_mv


# ============================================================
# 019Y T2：估值数据 + 限售解禁 + baostock 财务备用源
#
# 大白话说明：
# - 估值（PE/PB/PS/PCF/股息率）是项目此前缺失的数据维度，单独存入新表 stock_valuation。
# - 降级链路：akshare（A股 stock_a_indicator_lg，1.18 版本不存在时自动回退
#   stock_value_em；港股 stock_hk_valuation_baidu）→ baostock（仅A股）→ 标记缺失。
# - baostock 登录/登出成对管理（批次级），登录一次全局复用，不逐只重复登录。
# - baostock 走 TCP socket，不经过 requests 全局 patch，天然隔离。
# - baostock 不支持港股：港股估值仍走 akshare。
# ============================================================
import atexit as _atexit_019y
import threading as _threading_019y_bs

_BS_LOGGED_IN = False
_BS_LOCK = _threading_019y_bs.Lock()


def _bs_ensure_login():
    """019Y：baostock 登录（幂等，全局只登录一次；线程安全）。
    返回 True=已登录/登录成功；False=登录失败。
    """
    global _BS_LOGGED_IN
    if _BS_LOGGED_IN:
        return True
    with _BS_LOCK:
        if _BS_LOGGED_IN:
            return True
        try:
            import baostock as bs
            lg = bs.login()
            if lg.error_code == '0':
                _BS_LOGGED_IN = True
                logger.info('[baostock] 登录成功（生命周期：批次级，全局复用）')
                return True
            logger.warning(f'[baostock] 登录失败: code={lg.error_code} msg={lg.error_msg}')
            return False
        except Exception as e:
            logger.warning(f'[baostock] 登录异常: {e}')
            return False


def _bs_logout():
    """019Y：baostock 登出（与登录成对，进程退出时兜底）"""
    global _BS_LOGGED_IN
    if not _BS_LOGGED_IN:
        return
    try:
        import baostock as bs
        bs.logout()
        _BS_LOGGED_IN = False
        logger.info('[baostock] 已登出（生命周期成对）')
    except Exception as e:
        logger.warning(f'[baostock] 登出异常: {e}')


_atexit_019y.register(_bs_logout)


def _bs_code(symbol, market):
    """A股代码 → baostock 代码（sz.000001 / sh.600276）。港股不支持返回 None。"""
    if market != 'a_stock':
        return None
    if symbol.startswith('6'):
        return 'sh.' + symbol
    if symbol.startswith(('0', '3')):
        return 'sz.' + symbol
    return None


def _pick_val(row, names, frags):
    """019Y：从 DataFrame 行取数——先精确匹配 names，再按 frags 子串匹配（兼容 akshare 列名漂移）。
    找不到或值为空返回 None。
    """
    for n in names:
        if n in row.index:
            v = row[n]
            if pd.notna(v):
                return v
    for f in frags:
        for n in row.index:
            if f in str(n):
                v = row[n]
                if pd.notna(v):
                    return v
    return None


def _fetch_valuation_akshare(symbol, market):
    """019Y T2：akshare 估值主源（A股+港股）。
    任务书指定 stock_a_indicator_lg，但本机 akshare 1.18.53 已无此接口
    （乐咕估值更名），自动回退同源接口 stock_value_em（东财估值，同 PE/PB/PS 口径）。
    返回最近一条 dict 或 None；异常/超时向上抛出由 fetch_valuation 降级 baostock。
    """
    def _ak_call():
        if market == 'a_stock':
            fn = getattr(ak, 'stock_a_indicator_lg', None)
            if fn is not None:
                return fn(symbol=symbol)
            return ak.stock_value_em(symbol=symbol)
        if market == 'hk_stock':
            fn = getattr(ak, 'stock_hk_valuation_baidu', None)
            if fn is not None:
                return fn(symbol=symbol)
            return None
        return None

    df, timed_out = _call_ak_with_timeout(_ak_call, f'{symbol} 估值')
    if timed_out:
        raise TimeoutError('akshare 估值接口超时')
    if df is None or len(df) == 0:
        return None
    row = df.iloc[-1]  # 接口按日期升序，取最新一行
    raw_date = _pick_val(row, ['数据日期', '日期', 'trade_date'], ['数据日期', '日期'])
    val = {
        'trade_date': str(raw_date)[:10] if raw_date is not None else None,
        'pe_ttm': _safe_num(_pick_val(row, ['PE(TTM)', 'pe_ttm'], ['PE(TTM)'])),
        'pe': _safe_num(_pick_val(row, ['PE(静)', 'PE(动)', 'pe'], ['PE(静)', 'PE(动)'])),
        'pb_mrq': _safe_num(_pick_val(row, ['市净率', 'pb_mrq', 'pb'], ['市净率'])),
        'ps_ttm': _safe_num(_pick_val(row, ['市销率', 'ps_ttm'], ['市销率'])),
        'ps': None,
        'pcf_ncf_ttm': _safe_num(_pick_val(row, ['市现率', 'pcf_ncf_ttm'], ['市现率'])),
        'dv_ttm': _safe_num(_pick_val(row, ['股息率'], ['股息率'])),
        'total_mv': _safe_num(_pick_val(row, ['总市值', 'total_mv'], ['总市值'])),
    }
    return val


def _fetch_valuation_baostock(symbol, market):
    """019Y T2：baostock 估值备用源（仅A股，peTTM/pbMRQ/psTTM/pcfNcfTTM）。
    返回最近一条交易日 dict 或 None（失败不抛异常）：
    {'trade_date','pe_ttm','pb_mrq','ps_ttm','pcf_ncf_ttm'}
    """
    code = _bs_code(symbol, market)
    if not code:
        return None
    if not _bs_ensure_login():
        return None
    try:
        import baostock as bs
        now = datetime.now(_CN_TZ).replace(tzinfo=None)
        start_d = (now - timedelta(days=7)).strftime('%Y-%m-%d')
        end_d = now.strftime('%Y-%m-%d')
        rs = bs.query_history_k_data_plus(
            code,
            'date,code,peTTM,pbMRQ,psTTM,pcfNcfTTM',
            start_date=start_d,
            end_date=end_d,
            frequency='d',
        )
        rows = []
        while (rs.error_code == '0') & rs.next():
            rows.append(rs.get_row_data())
        if not rows:
            logger.warning(f'[baostock] {symbol} 估值返回空数据: {rs.error_msg}')
            return None
        last = rows[-1]
        return {
            'trade_date': last[0],
            'pe_ttm': _safe_num(last[2]),
            'pb_mrq': _safe_num(last[3]),
            'ps_ttm': _safe_num(last[4]),
            'pcf_ncf_ttm': _safe_num(last[5]),
        }
    except Exception as e:
        logger.warning(f'[baostock] {symbol} 估值获取异常: {e}')
        return None


def fetch_valuation(symbol, market, force_full=False):
    """采集估值数据（PE/PB/PS/PCF/股息率/总市值）存入 stock_valuation 表。

    021M 新降级链路（腾讯实时优先 + 东财补字段）：
      1. 腾讯行情（A股+港股统一）—— PE/PB/总市值，实时无延迟，核心数据源
      2. 东财 akshare（补充）—— PS/PCF/股息率，T+1 可接受，失败不影响核心
      3. baostock（仅A股兜底）—— PE/PB/PS/PCF，腾讯失败时降级
      4. 全部失败 → 标记缺失

    背景：东财 stock_value_em 估值接口 T+1 更新（当天只能拿到昨天数据），
    而腾讯行情接口实时返回当天 PE/PB/市值，且稳定可用（K线数据一直在用）。
    实测 PE/PB 与东财完全一致，市值差异 <1%。

    估值属低频数据（日级），同日跳过。
    返回: (状态, 消息)
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 日级低频：同日跳过（020R-56：仅最新记录为「真实成功」时跳过——即
    # status='success' 且 message 非「同日跳过」；跳过本身写 'skipped' 状态，
    # 防止"跳过链"自延续（历史上小米/阿里因连续同日跳过从未真正采集）；
    # failed 记录允许当日重试，避免失败后整天不再尝试）
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT fetched_at, status, message FROM data_status
                   WHERE stock_id = ? AND dimension = 'valuation'
                   ORDER BY fetched_at DESC LIMIT 1""",
                (stock_id,),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if (
                row
                and row['fetched_at']
                and (row['status'] or '') == 'success'
                and '同日跳过' not in (row['message'] or '')
            ):
                last_date = str(row['fetched_at'])[:10]
                today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                if last_date >= today_str:
                    skip_msg = '同日跳过(估值当日已采集)'
                    save_data_status(stock_id, 'valuation', 'skipped', skip_msg)
                    logger.info(f'[{symbol}] {skip_msg}')
                    return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[{symbol}] 估值同日检查异常(降级为采集): {e}')

    val = None
    src = None

    # ================================================================
    # 第一步：腾讯行情实时获取 PE/PB/总市值（A股+港股统一，核心数据源）
    # 021M：从仅港股兜底升级为核心数据源，A股+港股统一走腾讯实时。
    # ================================================================
    try:
        pe, pb, total_mv = _fetch_valuation_tencent(symbol, market)
        if pe is not None or pb is not None:
            val = {
                'trade_date': None,  # 下方以最新K线日期为准（腾讯快照无日期字段）
                'pe_ttm': pe,
                'pb_mrq': pb,
                'pe': None,
                'ps_ttm': None,
                'ps': None,
                'pcf_ncf_ttm': None,
                'dv_ttm': None,
                'total_mv': total_mv,
            }
            src = 'tencent'
            logger.info(f'[{symbol}] 腾讯实时估值命中: PE={pe}, PB={pb}, 总市值={total_mv}')
    except Exception as e:
        logger.warning(f'[{symbol}] 腾讯实时估值失败: {e}')

    # ================================================================
    # 第二步：东财 akshare 补充 PS/PCF/股息率（可选，失败不影响核心数据）
    # T+1 数据，用于补充腾讯不提供的次要字段。
    # ================================================================
    if val:
        try:
            ak_val = _fetch_valuation_akshare(symbol, market)
            if ak_val:
                # 仅补充腾讯未提供的字段，不覆盖腾讯的 PE/PB/市值
                for key in ('ps_ttm', 'pcf_ncf_ttm', 'dv_ttm', 'pe'):
                    if ak_val.get(key) is not None and val.get(key) is None:
                        val[key] = ak_val[key]
                # 如果腾讯没拿到 PE/PB，用东财兜底
                if val.get('pe_ttm') is None and ak_val.get('pe_ttm') is not None:
                    val['pe_ttm'] = ak_val['pe_ttm']
                if val.get('pb_mrq') is None and ak_val.get('pb_mrq') is not None:
                    val['pb_mrq'] = ak_val['pb_mrq']
                if val.get('total_mv') is None and ak_val.get('total_mv') is not None:
                    val['total_mv'] = ak_val['total_mv']
                src = 'tencent+akshare'
                logger.info(f'[{symbol}] 东财补充估值字段: PS={ak_val.get("ps_ttm")}, PCF={ak_val.get("pcf_ncf_ttm")}')
        except Exception as e:
            logger.warning(f'[{symbol}] 东财补充估值失败(不影响核心): {e}')

    # ================================================================
    # 第三步：baostock 兜底（仅A股，腾讯失败时降级）
    # ================================================================
    if not val and market == 'a_stock':
        try:
            val = _fetch_valuation_baostock(symbol, market)
            if val and val.get('trade_date'):
                src = 'baostock'
                logger.info(f'[{symbol}] baostock 估值兜底命中: {val["trade_date"]}')
        except Exception as e:
            logger.warning(f'[{symbol}] baostock估值失败: {e}')

    # ================================================================
    # 第四步：确定交易日（腾讯快照无日期字段，取最新K线日期）
    # ================================================================
    if val and not val.get('trade_date'):
        try:
            conn_td = get_connection()
            td_row = conn_td.execute(
                'SELECT MAX(trade_date) d FROM raw_kline WHERE stock_id=?', (stock_id,)
            ).fetchone()
            conn_td.close()
            if td_row and td_row['d']:
                val['trade_date'] = str(td_row['d'])[:10]
                logger.info(f'[{symbol}] 估值交易日取最新K线: {val["trade_date"]}')
        except Exception as e:
            logger.warning(f'[{symbol}] 读取最新K线日期失败: {e}')

    if not val or not val.get('trade_date'):
        fail_msg = 'akshare与baostock估值均失败'
        if market == 'hk_stock':
            # baostock 不支持港股，仅 akshare 一路失败（诚实标注，不误导）
            fail_msg = 'akshare与腾讯估值均失败（港股；baostock不支持港股）'
        save_data_status(stock_id, 'valuation', 'failed', fail_msg)
        return 'failed', fail_msg

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT OR REPLACE INTO stock_valuation
        (stock_id, trade_date, pe_ttm, pe, pb_mrq, ps_ttm, ps, pcf_ncf_ttm, dv_ttm, total_mv, source)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            stock_id,
            val['trade_date'],
            val.get('pe_ttm'),
            val.get('pe'),
            val.get('pb_mrq'),
            val.get('ps_ttm'),
            val.get('ps'),
            val.get('pcf_ncf_ttm'),
            val.get('dv_ttm'),
            val.get('total_mv'),
            src,
        ),
    )
    conn.commit()
    conn.close()
    msg = (
        f'估值已入库({src}): PE_TTM={val.get("pe_ttm")}, '
        f'PB={val.get("pb_mrq")}, PS_TTM={val.get("ps_ttm")}'
    )
    save_data_status(stock_id, 'valuation', 'success', msg)
    logger.info(f'[{symbol}] {msg}')
    return 'success', msg


def fetch_valuation_history(symbol, market, force_refresh=False):
    """采集历史估值（百度股市通，A股）存入 stock_valuation_history 表。

    021W-2：为数据详情"基本面数据历史"表格提供各财报期的**当时真实 PE/PB**
    （区别于 fetch_valuation 的实时估值，本表存历史时点真实估值快照）。

    数据源：akshare stock_zh_valuation_baidu（约每两周一个快照点，覆盖 2000 年至今）。
    - 仅 A 股支持：港股无稳定历史估值源，跳过并提示（不误报失败）
    - 幂等：按 (stock_id, trade_date) UPSERT，重复调用安全
    - 低频数据：成功采集后当日跳过（force_refresh=True 强制重采）
    返回: (状态, 消息)
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'
    if market != 'a_stock':
        return 'skipped', '历史估值采集暂仅支持 A 股（港股无稳定历史估值源）'

    # 低频：当日已成功采集则跳过（与 fetch_valuation 同日跳过同型）
    if not force_refresh:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT fetched_at, status, message FROM data_status
                   WHERE stock_id = ? AND dimension = 'valuation_history'
                   ORDER BY fetched_at DESC LIMIT 1""",
                (stock_id,),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if (
                row
                and row['fetched_at']
                and (row['status'] or '') == 'success'
                and '同日跳过' not in (row['message'] or '')
            ):
                last_date = str(row['fetched_at'])[:10]
                today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                if last_date >= today_str:
                    skip_msg = '同日跳过(历史估值当日已采集)'
                    save_data_status(stock_id, 'valuation_history', 'skipped', skip_msg)
                    logger.info(f'[{symbol}] {skip_msg}')
                    return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[{symbol}] 历史估值同日检查异常(降级为采集): {e}')

    # 百度股市通历史估值：PE(TTM) 与 PB 分两次请求（日期序列一致，约每两周一点）
    try:
        df_pe = ak.stock_zh_valuation_baidu(symbol=symbol, indicator='市盈率(TTM)', period='全部')
        df_pb = ak.stock_zh_valuation_baidu(symbol=symbol, indicator='市净率', period='全部')
    except Exception as e:
        msg = f'历史估值获取失败: {e}'
        save_data_status(stock_id, 'valuation_history', 'failed', msg)
        logger.warning(f'[{symbol}] {msg}')
        return 'failed', msg

    if df_pe is None or df_pe.empty or df_pb is None or df_pb.empty:
        msg = '历史估值数据为空'
        save_data_status(stock_id, 'valuation_history', 'failed', msg)
        logger.warning(f'[{symbol}] {msg}')
        return 'failed', msg

    df = df_pe.rename(columns={'value': 'pe_ttm'}).copy()
    df_pb_r = df_pb.rename(columns={'value': 'pb'})[['date', 'pb']]
    df = df.merge(df_pb_r, on='date', how='left')

    conn = get_connection()
    try:
        saved = 0
        for _, r in df.iterrows():
            trade_date = str(r.get('date'))[:10]
            pe_ttm = _safe_num(r.get('pe_ttm'))
            pb = _safe_num(r.get('pb'))
            if not trade_date:
                continue
            conn.execute(
                """
                INSERT OR REPLACE INTO stock_valuation_history
                (stock_id, trade_date, pe_ttm, pb, source)
                VALUES (?, ?, ?, ?, 'baidu')
            """,
                (stock_id, trade_date, pe_ttm, pb),
            )
            saved += 1
        conn.commit()
    except Exception as e:
        conn.close()
        msg = f'历史估值入库失败: {e}'
        save_data_status(stock_id, 'valuation_history', 'failed', msg)
        logger.warning(f'[{symbol}] {msg}')
        return 'failed', msg
    conn.close()

    msg = f'百度历史估值已入库: {saved} 个交易日快照 (PE_TTM/PB)'
    save_data_status(stock_id, 'valuation_history', 'success', msg)
    logger.info(f'[{symbol}] {msg}')
    return 'success', msg


def fetch_fundamental_baostock(symbol):
    """019Y T2：baostock 财务数据备用源（仅A股）。
    仅在 akshare 财务接口（abstract / analysis_indicator）全部失败时降级使用。
    query_profit_data 逐季度获取，返回 [(report_date, {db_col: value}), ...] 最新在前。
    roeAvg/npMargin/gpMargin 为小数比例，×100 转百分比。
    """
    code = _bs_code(symbol, 'a_stock')
    if not code:
        return []
    if not _bs_ensure_login():
        return []
    try:
        import baostock as bs
        now = datetime.now(_CN_TZ)
        rows_out = []
        # 近 8 个季度（2 年），最新在前
        quarters = []
        for back in range(8):
            total = now.year * 4 + (now.month - 1) // 3 - back
            y, q = divmod(total, 4)
            if q == 0:
                y, q = y - 1, 4
            quarters.append((y, q))
        for y, q in quarters:
            rs = bs.query_profit_data(code=code, year=y, quarter=q)
            lst = []
            while (rs.error_code == '0') & rs.next():
                lst.append(rs.get_row_data())
            if not lst:
                continue
            rec = dict(zip(rs.fields, lst[0]))
            stat_date = str(rec.get('statDate', ''))
            if not stat_date:
                continue
            report_date = stat_date[:10]
            vals = {}
            roe = _safe_num(rec.get('roeAvg'))
            np_m = _safe_num(rec.get('npMargin'))
            gp_m = _safe_num(rec.get('gpMargin'))
            if roe is not None:
                vals['roe'] = round(roe * 100, 2)
            if np_m is not None:
                vals['net_margin'] = round(np_m * 100, 2)
            if gp_m is not None:
                vals['gross_margin'] = round(gp_m * 100, 2)
            if vals:
                rows_out.append((report_date, vals))
        return rows_out
    except Exception as e:
        logger.warning(f'[baostock] {symbol} 财务备用源异常: {e}')
        return []


def fetch_restricted_release(symbol, market='a_stock', force_full=False):
    """019Y T2：采集个股限售解禁明细（风险因子，事件级）存入 stock_restricted_release 表。
    数据源：akshare stock_restricted_release_queue_em（东方财富个股解禁时间表）。
    当日快照语义：每次采集整表按 stock_id 重建（DELETE + INSERT）。
    仅 A股；港股无免费解禁接口返回 skipped。
    返回: (状态, 消息)
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'
    if market != 'a_stock':
        save_data_status(stock_id, 'restricted_release', 'skipped', '限售解禁仅A股')
        return 'skipped', '限售解禁仅A股'

    # 日级低频：同日跳过
    if not force_full:
        try:
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT fetched_at FROM data_status
                   WHERE stock_id = ? AND dimension = 'restricted_release'
                   ORDER BY fetched_at DESC LIMIT 1""",
                (stock_id,),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and row['fetched_at']:
                last_date = str(row['fetched_at'])[:10]
                today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
                if last_date >= today_str:
                    skip_msg = '同日跳过(限售解禁当日已采集)'
                    save_data_status(stock_id, 'restricted_release', 'success', skip_msg)
                    logger.info(f'[{symbol}] {skip_msg}')
                    return 'success', skip_msg
        except Exception as e:
            logger.warning(f'[{symbol}] 限售解禁同日检查异常(降级为采集): {e}')

    try:
        df, timed_out = _call_ak_with_timeout(
            lambda: ak.stock_restricted_release_queue_em(symbol=symbol),
            f'{symbol} 限售解禁',
        )
        if timed_out:
            raise TimeoutError('限售解禁接口超时')
        conn = get_connection()
        cursor = conn.cursor()
        # 当日快照：先清空该股旧记录，再写入本次最新解禁列表
        cursor.execute('DELETE FROM stock_restricted_release WHERE stock_id = ?', (stock_id,))
        saved = 0
        if df is not None and len(df) > 0:
            for _, r in df.iterrows():
                raw_date = _pick_val(r, ['解禁时间'], ['解禁时间'])
                release_date = str(raw_date)[:10] if raw_date is not None else None
                if not release_date:
                    continue
                ratio = _safe_num(_pick_val(r, ['占总市值比例', '占解禁前总股本比例'], ['占总市值比例', '总股本比例']))
                if ratio is not None:
                    ratio = round(ratio * 100, 2)  # 小数比例 → 百分比
                cursor.execute(
                    """
                    INSERT INTO stock_restricted_release
                    (stock_id, release_date, release_type, release_shares,
                     actual_shares, actual_mv, release_ratio, source)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        release_date,
                        str(_pick_val(r, ['限售股类型', '解禁类型'], ['限售股类型', '解禁类型']) or '')
                        or None,
                        _safe_num(_pick_val(r, ['解禁数量'], ['解禁数量'])),
                        _safe_num(_pick_val(r, ['实际解禁数量'], ['实际解禁数量'])),
                        _safe_num(_pick_val(r, ['实际解禁数量市值', '实际解禁市值'], ['解禁市值'])),
                        ratio,
                        'akshare',
                    ),
                )
                saved += 1
        conn.commit()
        conn.close()
        msg = f'限售解禁已入库(akshare): {saved} 条记录'
        save_data_status(stock_id, 'restricted_release', 'success', msg)
        logger.info(f'[{symbol}] {msg}')
        return 'success', msg
    except Exception as e:
        save_data_status(stock_id, 'restricted_release', 'failed', str(e))
        logger.warning(f'[{symbol}] 限售解禁采集失败: {e}')
        return 'failed', str(e)
