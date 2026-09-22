"""市场行情 API 蓝图：大盘指数复用 index_ratings，本蓝图提供行业资金流向。"""

import logging

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

bp = Blueprint('market', __name__)


@bp.route('/api/market/industry-fund-flow', methods=['GET'])
def api_market_industry_fund_flow():
    """获取行业资金流向快照（默认最新交易日；?date=YYYY-MM-DD 查看历史）。

    020R-53：响应含 dates（可用交易日列表，供时间维度选择），
    items 每行含 main_net_5d（截至该日的前 5 个交易日主力净流入累计）。
    """
    try:
        from modules.market_overview import (
            get_industry_flow_summary,
            get_industry_fund_flow_dates,
            get_industry_fund_flow_for_date,
        )

        dates = get_industry_fund_flow_dates()
        if not dates:
            return jsonify(
                {'success': True, 'trade_date': None, 'updated_at': None, 'items': [], 'dates': [], 'count': 0}
            )
        date_arg = (request.args.get('date') or '').strip()
        trade_date = date_arg if date_arg in dates else dates[0]
        items, updated_at = get_industry_fund_flow_for_date(trade_date)
        # 020R-54：市场资金温度计（全行业合计 + 流入/流出家数）
        summary = get_industry_flow_summary(trade_date)
        return jsonify(
            {
                'success': True,
                'trade_date': trade_date,
                'updated_at': updated_at,
                'items': items,
                'dates': dates,
                'count': len(items),
                'summary': summary,
            }
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({'success': False, 'error': f'{e!s}', 'items': [], 'count': 0}), 500


@bp.route('/api/market/industry-fund-flow/refresh', methods=['POST'])
def api_market_industry_fund_flow_refresh():
    """触发东财行业资金流实时抓取并落库；冷却期内直接回放上次快照。"""
    from modules.market_overview import (
        get_industry_fund_flow_dates,
        get_industry_fund_flow_for_date,
        refresh_in_cooldown,
        refresh_industry_fund_flow,
    )

    # 020R-34：刷新失败后 10 分钟冷却——不再硬闯东财，回放上次快照并提示
    cooldown_left = refresh_in_cooldown()
    if cooldown_left is not None:
        dates = get_industry_fund_flow_dates()
        if dates:
            items, updated_at = get_industry_fund_flow_for_date(dates[0])
            trade_date = dates[0]
        else:
            items, updated_at, trade_date = [], None, None
        minutes = max(1, cooldown_left // 60 + 1)
        return jsonify(
            {
                'success': True,
                'trade_date': trade_date,
                'updated_at': updated_at,
                'items': items,
                'dates': dates,
                'count': len(items),
                'cooldown': True,
                'note': f'东财接口限流冷却中，约 {minutes} 分钟后可重试，当前显示上次快照',
            }
        )

    try:
        items, trade_date, updated_at = refresh_industry_fund_flow()
        # 021BJ：刷新成功后后台回补"上一工作日缺口"（断连日丢失的快照），不阻塞响应
        try:
            from modules.market_overview import maybe_backfill_gap_async

            gap = maybe_backfill_gap_async()
            if gap:
                logger.info('[行业资金流] 检测到缺口 %s，已启动后台回补', gap)
        except Exception:  # noqa: BLE001
            pass
        dates = get_industry_fund_flow_dates()
        # 刷新落库后重新读取（附带 5 日累计列）
        items, updated_at = get_industry_fund_flow_for_date(trade_date)
        return jsonify(
            {
                'success': True,
                'trade_date': trade_date,
                'updated_at': updated_at,
                'items': items,
                'dates': dates,
                'count': len(items),
                'cooldown': False,
                'note': None,
            }
        )
    except Exception as e:  # noqa: BLE001
        return jsonify({'success': False, 'error': f'{e!s}'}), 500


# ============================================================
# 021BI: 全市场选股扫描
# ============================================================


@bp.route('/api/market/scan', methods=['POST'])
def api_market_scan():
    """第①段 快照粗筛：快照（缓存或重拉）→ 行业回填 → 筛选 → 量比增强。

    Body: {filters: {...}, refresh: bool}
    filters 缺省项按 DEFAULT_HYGIENE 兜底（剔ST/市值≥100亿/换手≥1%）。
    耗时约 1 分钟（首次/强制刷新时，新浪 ~56 页）。
    """
    try:
        from modules.market_screener import DEFAULT_HYGIENE, run_coarse_scan

        body = request.get_json(silent=True) or {}
        filters = body.get('filters') or {}
        refresh = bool(body.get('refresh'))
        # 服务端兜底卫生线（前端不传时生效）
        for k, v in DEFAULT_HYGIENE.items():
            filters.setdefault(k, v)
        result = run_coarse_scan(filters=filters, refresh=refresh)
        return jsonify(result)
    except Exception as e:  # noqa: BLE001
        return jsonify({'available': False, 'error': f'{e!s}'}), 500


@bp.route('/api/market/scan-signals', methods=['POST'])
def api_market_scan_signals():
    """第②段 技术信号精筛（单批 ≤50 只）：逐票拉腾讯K线 → 检测信号。

    Body: {entries: [{symbol, name}], signals: [key...], window: 3}
    前端分批驱动 + 进度条（避免单请求超长阻塞）。
    """
    try:
        from modules.market_screener import run_signal_chunk

        body = request.get_json(silent=True) or {}
        entries = body.get('entries') or []
        signals = body.get('signals') or None
        window = int(body.get('window') or 3)
        window = min(max(window, 1), 10)
        result = run_signal_chunk(entries, signals=signals, window=window)
        return jsonify({'success': True, **result})
    except Exception as e:  # noqa: BLE001
        return jsonify({'success': False, 'error': f'{e!s}'}), 500


@bp.route('/api/market/scan/library', methods=['GET'])
def api_market_scan_library():
    """信号库元数据（label/note），供前端渲染信号说明。"""
    from modules.market_screener import SIGNAL_LIBRARY

    return jsonify({'success': True, 'signals': SIGNAL_LIBRARY})


@bp.route('/api/market/scan/status', methods=['GET'])
def api_market_scan_status():
    """扫描器卡片初始化：只查快照状态，不触发拉取（首次扫描须用户手动点）。"""
    from modules.market_screener import load_snapshot

    snap = load_snapshot()
    if snap is None:
        return jsonify({'success': True, 'has_snapshot': False})
    return jsonify({'success': True, 'has_snapshot': True,
                    'snapshot_at': snap['snapshot_at'], 'stale': snap['stale'],
                    'universe': len(snap['rows'])})


# ============================================================
# 021BP 决策闭环 项1: 自选股买点信号巡检（离线复算，零网络）
# ============================================================


@bp.route('/api/market/scan/watchlist-signals', methods=['GET'])
def api_market_watchlist_signals():
    """自选股买点信号巡检：对已采集K线（raw_kline/raw_kline_weekly）离线复算。

    零网络——复用 market_screener 信号纯函数读库计算，100 只毫秒级完成。
    Query: ?window=3（触发窗口1~10）&stock_ids=1,2,3（缺省=全部 active 自选股）
    响应 scope=watchlist_offline：结果属"快照参考"口径，截止最新已采集K线
    （kline_upto），与第②段在线扫描（腾讯K线）存在 EMA 预热长度差异。
    """
    try:
        from modules.market_screener import scan_watchlist_signals

        try:
            window = int(request.args.get('window') or 3)
        except (TypeError, ValueError):
            window = 3
        window = min(max(window, 1), 10)

        raw_ids = (request.args.get('stock_ids') or '').strip()
        stock_ids = None
        if raw_ids:
            stock_ids = []
            for part in raw_ids.split(','):
                try:
                    stock_ids.append(int(part))
                except ValueError:
                    continue
            if not stock_ids:
                stock_ids = None

        result = scan_watchlist_signals(stock_ids=stock_ids, window=window)
        return jsonify({'success': True, **result})
    except Exception as e:  # noqa: BLE001
        return jsonify({'success': False, 'error': f'{e!s}',
                        'scope': 'watchlist_offline', 'results': [], 'errors': []}), 500


# ============================================================
# 021BQ 决策闭环: 自选股卖点信号巡检（离线复算，零网络）
# ============================================================


@bp.route('/api/market/scan/watchlist-sell-signals', methods=['GET'])
def api_market_watchlist_sell_signals():
    """自选股卖点信号巡检：对已采集K线（raw_kline/raw_kline_weekly）离线复算。

    与 watchlist-signals 同构镜像，走卖侧平行库（SELL_SIGNAL_LIBRARY/
    SELL_RESONANCE_LIBRARY，kind='bear'）——在线全市场扫描不受影响（仍只产买点）。
    零网络——100 只毫秒级完成。Query: ?window=3（触发窗口1~10）&stock_ids=1,2,3
    （缺省=全部 active 自选股）。响应 scope=watchlist_offline + side=sell：
    快照参考口径，截止最新已采集K线（kline_upto）。
    """
    try:
        from modules.market_screener import scan_watchlist_sell_signals

        try:
            window = int(request.args.get('window') or 3)
        except (TypeError, ValueError):
            window = 3
        window = min(max(window, 1), 10)

        raw_ids = (request.args.get('stock_ids') or '').strip()
        stock_ids = None
        if raw_ids:
            stock_ids = []
            for part in raw_ids.split(','):
                try:
                    stock_ids.append(int(part))
                except ValueError:
                    continue
            if not stock_ids:
                stock_ids = None

        result = scan_watchlist_sell_signals(stock_ids=stock_ids, window=window)
        return jsonify({'success': True, **result})
    except Exception as e:  # noqa: BLE001
        return jsonify({'success': False, 'error': f'{e!s}', 'side': 'sell',
                        'scope': 'watchlist_offline', 'results': [], 'errors': []}), 500
