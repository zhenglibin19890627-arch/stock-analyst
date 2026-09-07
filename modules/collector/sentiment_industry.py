"""消息面采集 + 行业分类映射。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
from datetime import datetime

import akshare as ak

from database.db_manager import get_connection
from modules.collector._env import _CN_TZ, logger
from modules.collector.capital_em import _em_banned, _get_em_secid
from modules.collector.http_client import _http_get_em
from modules.collector.kline import _is_intraday_session
from modules.collector.symbols_status import get_stock_id, save_data_status

# ============================================================
# 消息面数据采集
# ============================================================


def fetch_sentiment(symbol, market, force_full=False):
    """
    采集消息面数据（模块4接入）。
    调用 news_collector 采集新闻 + 情绪分析。
    增量逻辑：当日已采集过（无论有无新新闻）→ 跳过。

    状态分类（由 news_collector.collect_news 返回）:
        - success + 有新增新闻: 正常有数据
        - success + 无新增新闻: 正常无数据（数据源今天确实没发新新闻）
        - failed: 真正异常（接口报错/超时/反爬等）
    """
    stock_id = get_stock_id(symbol, market)
    if not stock_id:
        return 'failed', f'数据库中未找到股票 {symbol}'

    # 增量逻辑：当日已采集过 → 跳过
    # 检查 news_sentiment 表是否有今天的记录（包括空标记记录）
    if not force_full:
        try:
            today_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d')
            conn_chk = get_connection()
            cursor_chk = conn_chk.cursor()
            cursor_chk.execute(
                """SELECT total_count FROM news_sentiment
                   WHERE stock_id = ? AND news_date = ?""",
                (stock_id, today_str),
            )
            row = cursor_chk.fetchone()
            conn_chk.close()
            if row and not _is_intraday_session(market):
                # 今天已采集过，跳过
                total_count = row['total_count']
                if total_count > 0:
                    skip_msg = f'当日跳过(今日已采集{total_count}条新闻)'
                else:
                    skip_msg = '当日跳过(今日已采集，无新增新闻)'
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
        error_msg = f'{market_name} {symbol} 消息面采集异常: {e!s}'
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
    '603501': '半导体',
    '688017': '半导体',
    '688041': '半导体',
    '688047': '半导体',
    '688795': '半导体',
    '688802': '半导体',
    '688981': '半导体',
}


def fetch_stock_industry(symbol: str, market: str = 'a_stock', patient: bool = False) -> str:
    """获取个股行业分类。
    021BA：主源改为东财 push2 qt/stock/get 直连（复用项目 _http_get_em 请求层：
    编号子域轮换/轮间退避/UA池，比 akshare 裸调抗 WAF/断连），字段 f127=行业板块名
    （申万 2021 口径，与 market_overview 行业资金流、config_weights
    industry_overrides 同名体系）；akshare stock_individual_info_em 降为备源；
    本地映射最后兜底。
    patient=False（加自选股/批量分析等同步场景）：EM 直连只试 1 轮，快速失败不拖长请求；
    patient=True（补采调度器等后台场景）：EM 直连试 2 轮，容忍轮间退避换更高成功率。
    港股：无免费行业接口，默认返回"港股"。
    获取失败时返回"未分类"，不阻塞主流程；存量"未分类"由补采调度器行业自愈兜底（021BA）。
    """
    if market == 'hk_stock' or symbol.upper().startswith('HK'):
        return '港股'
    # 尊重资金面熔断冷却：EM 整体不可达时不直连（与 _fetch_capital_flow_em 同款判断）
    if not _em_banned():
        try:
            resp = _http_get_em(
                'https://push2.eastmoney.com/api/qt/stock/get',
                params={
                    'secid': _get_em_secid(symbol, market),
                    'invt': '2',
                    'fltt': '2',
                    'fields': 'f127',
                },
                timeout=10,
                max_retries=2 if patient else 1,
            )
            data = (resp.json() or {}).get('data') or {}
            val = str(data.get('f127') or '').strip()
            if val and val != '-':
                return val
        except Exception as e:
            logger.warning(f'[{symbol}] 行业EM直连失败，尝试akshare: {e}')
    # 备源：akshare（东财同系接口，行业命名体系一致）
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
