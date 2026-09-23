"""腾讯实时行情批量取价/快照（021BT 自 blueprints/portfolio/market.py 下沉复用）。

背景（021BT 方案 §二.2）：`_fetch_realtime_price_batch` 原位于蓝图层
（blueprints/portfolio/market.py），手动"刷新价格"端点与盘中巡检调度器都需要它；
modules 层反向 import 蓝图会形成架构异味，故逐字平移至本模块（纯 HTTP 无 Flask
依赖），market.py 改为本模块再导出（facade 引用面零变化，refresh-prices 行为零变化）。

模块组成：
- `_fetch_realtime_price_batch`：既有公开契约（逐字平移，仅加模块级 0.25s 节拍），
  refresh-prices 端点消费；返回 {stock_id: {'price','pct_change'}}；
- `_fetch_quote_snapshot_batch`：021BT 盘中巡检专用快照（比取价多解析 快照时间戳
  [30]/昨收[4]/成交量[6]），零 mootdx 降级、零重试（失败由调用方静默降级）；
  返回 {stock_id: {'price','pct_change','prev_close','volume','quote_ts'}}。

腾讯字段下标（2026-09-23 实测核验，与 020R-60 `_refresh_kline_today_bar` 同源）：
[3]现价 [4]昨收 [6]成交量（A股=手/港股=股，与 fqkline 同单位）[30]快照时间戳
（A股 YYYYMMDDHHMMSS / 港股 YYYY/MM/DD HH:MM:SS，统一去非数字归一）
[32]涨跌幅(%) [33]最高 [34]最低。
"""

import logging
import re
import time

logger = logging.getLogger(__name__)

# 通道级最小间隔（秒）：与 market_screener._QQ_MIN_INTERVAL 同级克制值。
# 分钟级巡检/手动刷新几乎不等待，仅防"手动连点叠加巡检"的同源请求堆积。
_REALTIME_MIN_INTERVAL = 0.25
_last_request_ts = 0.0


def _pace():
    """模块级请求节拍：距上次请求不足最小间隔时短暂等待（阻塞式，最长 0.25s）。"""
    global _last_request_ts
    now = time.monotonic()
    wait = _REALTIME_MIN_INTERVAL - (now - _last_request_ts)
    if wait > 0:
        time.sleep(wait)
    _last_request_ts = time.monotonic()


def _fetch_realtime_price_batch(symbols_markets):
    """批量获取实时行情价格（腾讯接口）。
    symbols_markets: [(stock_id, symbol, market), ...]
    返回: {stock_id: {'price': float, 'pct_change': float}}
    019Y T1：腾讯主源缺失的 A股 自动降级 mootdx（通达信行情，TCP socket，不经过 requests patch）。
    021BT：自 blueprints/portfolio/market.py 逐字平移至本模块（行为零变化）。
    """
    import logging as _logging019y

    import requests as _requests

    from modules.data_collector import _normalize_hk_symbol

    _log019y = _logging019y.getLogger(__name__)

    result = {}
    if not symbols_markets:
        return result

    # 构造腾讯批量请求代码：sh600000,sz000001,hk00700
    tencent_codes = []
    stock_id_map = {}
    for stock_id, symbol, market in symbols_markets:
        if market == 'hk_stock':
            # 港股：库内 HK3690 形态 → 腾讯 hk03690（剥离 HK 前缀+左补零至5位）
            # 021M 修复：原 symbol.zfill(5) 对带前缀代码（'HK3690' 已6字符）无效，
            # 生成 hkHK3690 错误代码 → 腾讯返回空 → 港股全部降级写入昨收（实测
            # 2026-08-18：cache 价=08-17 收盘 88.0，实时价实为 84.95）
            hk_code = _normalize_hk_symbol(symbol)
            tc = 'hk' + hk_code
        elif market == 'a_stock':
            if symbol.startswith('6'):
                tc = 'sh' + symbol
            else:
                tc = 'sz' + symbol
        else:
            continue
        tencent_codes.append(tc)
        stock_id_map[tc] = stock_id

    if not tencent_codes:
        return result

    # 腾讯批量行情接口（逗号分隔，最多约50只一次）
    batch_size = 40
    for i in range(0, len(tencent_codes), batch_size):
        batch = tencent_codes[i : i + batch_size]
        url = 'https://qt.gtimg.cn/q=' + ','.join(batch)
        try:
            _pace()
            resp = _requests.get(url, timeout=8)
            lines = resp.text.strip().split(';')
            for line in lines:
                line = line.strip()
                if not line or '~' not in line:
                    continue
                parts = line.split('~')
                if len(parts) < 5:
                    continue
                # 从原始行中提取代码
                var_match = line.split('=')[0].strip()
                tc_code = var_match.replace('v_', '').strip()
                sid = stock_id_map.get(tc_code)
                if not sid:
                    continue
                # parts[1]=名称, parts[3]=最新价, parts[32]=涨跌幅(%)
                try:
                    price = float(parts[3]) if parts[3] else 0
                    pct = float(parts[32]) if len(parts) > 32 and parts[32] else 0
                    if price > 0:
                        result[sid] = {'price': price, 'pct_change': pct}
                except (ValueError, IndexError):
                    continue
        except Exception:
            continue

    # 019Y T1：腾讯主源缺失的 A股 → mootdx 降级（每只一次 socket 查询，量小）
    missing_a = [
        (stock_id, symbol)
        for stock_id, symbol, market in symbols_markets
        if market == 'a_stock' and stock_id not in result
    ]
    if missing_a:
        try:
            from modules.data_collector import get_realtime_quote_mootdx

            for stock_id, symbol in missing_a:
                q = get_realtime_quote_mootdx(symbol)
                if q and q.get('price') and q['price'] > 0:
                    result[stock_id] = {'price': q['price'], 'pct_change': q.get('pct_change')}
                    _log019y.info(
                        f'[019Y] {symbol} 实时价格走 mootdx 降级: price={q["price"]}'
                    )
        except Exception as e:
            _log019y.warning(f'[019Y] mootdx 实时价格降级失败: {e}')

    return result


def _parse_quote_row(line):
    """解析单行腾讯行情 → (tencent_code, 快照 dict)；无效行返回 (None, None)。

    快照 dict：{'price','pct_change','prev_close','volume','quote_ts'}；
    quote_ts 为归一形态 'YYYYMMDDHHMMSS'（A股/港股两种源格式统一去非数字，
    2026-09-23 实测：A股 20260923161450 / 港股 2026/09/23 16:08:14）；时间戳
    缺失/不可解析时为 None（调用方据此守卫跳过，节假日盲区防线）。
    """
    line = line.strip()
    if not line or '~' not in line or '=' not in line:
        return None, None
    parts = line.split('~')
    if len(parts) < 35:
        return None, None
    code = line.split('=')[0].strip().replace('v_', '').strip()

    def _f(idx):
        try:
            s = parts[idx].strip().strip('"').strip(';')
            return float(s) if s else None
        except (ValueError, IndexError):
            return None

    price = _f(3)
    if not price or price <= 0:
        return code, None
    pct = _f(32)
    digits = re.sub(r'\D', '', parts[30])
    quote_ts = digits[:14] if len(digits) >= 14 else None
    return code, {
        'price': price,
        'pct_change': pct if pct is not None else 0.0,
        'prev_close': _f(4),
        'volume': _f(6),
        'quote_ts': quote_ts,
    }


def _fetch_quote_snapshot_batch(symbols_markets):
    """021BT 盘中巡检专用：腾讯批量行情快照（现价/涨跌幅/昨收/成交量/快照时间戳）。

    与 `_fetch_realtime_price_batch` 同源同下标，但契约不同：
    - 零 mootdx 降级、零重试（巡检失败静默降级由调用方处理，防重试风暴）；
    - 携带快照时间戳（节假日守卫）与成交量（量能异动）；
    - 全部批次请求均失败时抛 RuntimeError（部分成功照常返回）。
    """
    import requests as _requests

    from modules.data_collector import _normalize_hk_symbol

    result = {}
    if not symbols_markets:
        return result

    tencent_codes = []
    stock_id_map = {}
    for stock_id, symbol, market in symbols_markets:
        if market == 'hk_stock':
            tc = 'hk' + _normalize_hk_symbol(symbol)
        elif market == 'a_stock':
            tc = ('sh' if symbol.startswith('6') else 'sz') + symbol
        else:
            continue
        tencent_codes.append(tc)
        stock_id_map[tc] = stock_id

    if not tencent_codes:
        return result

    batch_size = 40
    fetched_any_batch = False
    for i in range(0, len(tencent_codes), batch_size):
        batch = tencent_codes[i : i + batch_size]
        url = 'https://qt.gtimg.cn/q=' + ','.join(batch)
        try:
            _pace()
            resp = _requests.get(url, timeout=8)
            lines = resp.text.strip().split(';')
        except Exception as e:
            logger.warning(f'[盘中巡检] 腾讯批量行情批次失败: {e}')
            continue
        fetched_any_batch = True
        for line in lines:
            code, quote = _parse_quote_row(line)
            if not code or quote is None:
                continue
            sid = stock_id_map.get(code)
            if sid:
                result[sid] = quote

    if not fetched_any_batch:
        raise RuntimeError('腾讯批量行情全部批次请求失败')
    return result
