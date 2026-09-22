"""操盘手建议（2026-09-18）——股价阶段判定 + 主力行为解读 + 对策与裁决信号。

回答操盘手的三个问题（与综合评级互补，不推翻评级）：
    1. 股价处于哪个阶段？（6 态：底部吸筹/拉升初期/主升/顶部出货/下跌/震荡无趋势）
    2. 主力资金在干嘛？（资金×筹码×杠杆的组合推断，含分歧检测与数据盲区）
    3. 我们该怎么做 + 什么信号出现时改变判断？（对策 + 裁决条件）

021BQ（2026-09-22）操作矩阵升级：输出新增 `operations` 键——
    持仓/空仓双视角操作矩阵（触发条件与价位），融合短线技术信号层：
    - 短线信号：复用 market_screener 平行纯函数对库内K线离线复算
      （买点 detect_signals + 卖点 detect_sell_signals 双侧，零网络零新增采集）
    - 关键价位：成本×0.92 纪律线 + 最新日报 price_advice 的止损/买入区间
      （零重算读取已存数据，不触碰 price_advisor 计算）
    - 主从契约不变：评级是唯一动作主指令，矩阵全部为「条件→动作」式，
      与评级相悖的信号行内附调和注记（021BP 行动清单 c23f9ee 同思路），
      永不输出与评级相反的无条件指令（测试锁死）。
    既有键（stage/capital/playbook/disagreement/rating/total_score/disclaimer）
    结构零改动，纯增量。

021BR（2026-09-22）分域不对称层级（用户拍板，测试锁死）——永不自相矛盾：
    ①风控纪律（止损/破位）无条件最高：纪律已触发时任何行不得与其冲突——
      「持有」类行被纪律行取代，共振行不得输出「持仓者持有」（改写为反抽减仓参考）；
    ②减仓/离场类动作听操盘手：评级门控未到（如评级还在持有观望）而操盘手判
      强下跌/破位/卖点信号时，输出确定性减仓/离场行并标注「操盘手纪律触发，
      评级尚未跟上（当前评级 X）」，不再写「按评级执行风控」式推给评级；
    ③买入/加仓方向仍评级门控（021BQ 三档不变），操盘手信号只能改性（反弹参考）
      不能催促买入。
    配套：矩阵每行增 layer（纪律/战术/战略）与 status（triggered/pending）字段；
    止损/破位已触发时 operations.status 置顶状态行（现价/触发线/触发日期）；
    共振行补触发日期与时效（窗口内历史触发不再读起来像新信号）；
    detect_disagreement 补「持有观望 × 强置信弱势阶段」档（阶段领先于评级）。

与评级的关系（021BR 分域不对称层级，用户拍板）：
    风控纪律无条件最高；减仓/离场听操盘手（独立触发）；买入/加仓仍以评级为
    唯一动作主指令（021BQ 三档门控不变）。分歧时不推翻评级域——动作收窄并
    显式给出裁决信号（什么信号出现时跟随哪一方）。测试锁死该行为。

方法论与诚实边界：
    - 评分是加权平均（表达强度），阶段判定是模式识别（表达结构）——
      分歧是特性不是缺陷（分数丢失结构信息），集中在评级边界档。
    - 阶段判定事前永远模糊（横盘是吸筹还是中继，走出来才知道），
      话术自带概率与确认信号，不假装全知。
    - 主力行为是间接证据链推断（无龙虎榜/大宗/北向明细），盲区明示。

数据来源（全部现成，零新增采集）：
    - StockData 契约（data_adapter 加载）：均线/MACD/RSI/量比/主力资金/户数/杠杆/情绪
    - raw_kline 近 60 根：量能趋势/量价配合度/量价背离/位置分位/振幅（本模块计算）
    - raw_kline 近 250 根 + raw_kline_weekly（021BQ）：买卖侧短线信号离线复算
    - trend_analyzer.analyze_trends：日/周/月三周期趋势
    - daily_reports 最新评级 + price_advice（021BQ 价位层，零重算读取）
    - holdings 持仓成本（对策个性化；021BQ 起按账户聚合，多账户分仓不失真）

只读纯函数：不写库、不发网络请求、不触碰 generate_advice（B24 红线）。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from database.db_manager import get_connection

logger = logging.getLogger(__name__)

# ================================================================
# 阶段常量（6 态）
# ================================================================

STAGE_ACCUMULATION = 'accumulation'  # 底部吸筹区
STAGE_MARKUP_EARLY = 'markup_early'  # 拉升初期
STAGE_MARKUP_FULL = 'markup_full'    # 主升期
STAGE_DISTRIBUTION = 'distribution'  # 顶部出货区
STAGE_DECLINE = 'decline'            # 下跌期
STAGE_RANGE = 'range'                # 震荡无趋势（兜底）

STAGE_NAMES = {
    STAGE_ACCUMULATION: '底部吸筹区',
    STAGE_MARKUP_EARLY: '拉升初期',
    STAGE_MARKUP_FULL: '主升期',
    STAGE_DISTRIBUTION: '顶部出货区',
    STAGE_DECLINE: '下跌期',
    STAGE_RANGE: '震荡无趋势',
}

STAGE_PLAYBOOK = {
    STAGE_ACCUMULATION: '小仓试错或等启动信号，不满仓抄底——吸筹期时间成本高于价格成本',
    STAGE_MARKUP_EARLY: '试仓后持有，跌破启动位（MA20）离场——突破可能是假的，仓位换确认',
    STAGE_MARKUP_FULL: '持有 + 移动止损跟（MA10/MA20），不加仓不跳水——利润是坐出来的',
    STAGE_DISTRIBUTION: '分批兑现，反弹减仓不恋战——高位放量滞涨就是主力在卖',
    STAGE_DECLINE: '现金为王，反弹被均线压制就减——不做下跌中继的抄底',
    STAGE_RANGE: '网格化对待：区间下沿接、上沿减，不判断方向——承认无趋势本身就是结论',
}

CONF_STRONG = '强'
CONF_MID = '中'
CONF_WEAK = '低'

DISAG_REVERSAL = 'reversal_opportunity'  # 评级弱 × 阶段好（跌出恐慌盘）
DISAG_RISK = 'structural_risk'           # 评级强 × 阶段差（冲高派发嫌疑）
DISAG_STAGE_LEAD = 'stage_leads_rating'  # 评级观望 × 强置信弱势阶段（021BR：阶段领先于评级）


# ================================================================
# 量价结构计算（raw_kline 近 60 根）
# ================================================================


def _volume_structure(klines: list[dict[str, Any]]) -> dict[str, Any]:
    """从原始 K 线计算量价结构五件套。

    - vol_trend: 近5日均量/近20日均量（<0.8 缩量，1.2~2.5 温和放量，>2.5 极端放量）
    - fit_ratio: 近20日量价配合度（价涨量增 + 价跌量缩 的天数占比）
    - top_divergence: 近60日出现「价新高但量能显著萎缩」的背离对
    - position_pctile: 现价在近60日高低区间的分位（0~1）
    - amplitude_shrink: 近10日振幅较前20日收窄（吸筹特征）
    """
    out: dict[str, Any] = {
        'vol_trend': None,
        'fit_ratio': None,
        'top_divergence': False,
        'position_pctile': None,
        'amplitude_shrink': False,
        'low60': None,
        'high60': None,
    }
    closes = [float(k['close']) for k in klines if k.get('close') and k.get('volume')]
    vols = [float(k['volume']) for k in klines if k.get('close') and k.get('volume')]
    if len(closes) < 20 or len(vols) < 20 or len(closes) != len(vols):
        return out

    vol5 = sum(vols[-5:]) / 5
    vol20 = sum(vols[-20:]) / 20
    out['vol_trend'] = round(vol5 / vol20, 2) if vol20 > 0 else None

    fit = 0
    n = 0
    for i in range(len(closes) - 20, len(closes)):
        dc = closes[i] - closes[i - 1]
        dv = vols[i] - vols[i - 1]
        if dc == 0:
            continue
        n += 1
        if (dc > 0 and dv > 0) or (dc < 0 and dv < 0):
            fit += 1
    out['fit_ratio'] = round(fit / n, 2) if n else None

    # 背离：近20日内创新高（>之前全部 high），当日量 < 前一个新高日量×0.8
    # highs 与 closes/vols 同源同长（上方过滤保证对齐），故可复用同一索引
    highs = closes[:]  # 用收盘近似新高判定（原始 high 行已因 volume 缺失被过滤）
    if len(highs) >= 40:
        pivot = -1
        pivot_vol = 0.0
        start = max(20, len(highs) - 20)
        for i in range(start, len(highs)):
            if highs[i] > max(highs[:i]):
                if pivot >= 0 and pivot_vol > 0 and vols[i] < pivot_vol * 0.8:
                    out['top_divergence'] = True
                pivot = i
                pivot_vol = vols[i]

    low60 = min(closes)
    high60 = max(closes)
    out['low60'] = low60
    out['high60'] = high60
    if high60 > low60:
        out['position_pctile'] = round((closes[-1] - low60) / (high60 - low60), 2)

    if len(closes) >= 30:
        def _amp(seg: list[float]) -> float:
            return sum(abs(seg[i] - seg[i - 1]) / seg[i - 1] for i in range(1, len(seg))) / (len(seg) - 1)

        amp10 = _amp(closes[-10:])
        amp20 = _amp(closes[-30:-10])
        out['amplitude_shrink'] = amp20 > 0 and amp10 < amp20 * 0.8

    return out


def _load_price_advice(pa_json: Any) -> dict[str, Any]:
    """从最新日报已存 price_advice JSON 提取操作矩阵价位（零重算只读）。

    与看板 _parse_pa_zone 同源口径（buy_range_low/high + stop_loss），
    缺数据/解析失败逐字段降级为 None，不阻塞主链路。
    """
    out: dict[str, Any] = {'stop_loss': None, 'buy_low': None, 'buy_high': None}
    if not pa_json:
        return out
    try:
        pa = json.loads(pa_json) if isinstance(pa_json, str) else pa_json
    except (TypeError, ValueError):
        return out
    if not isinstance(pa, dict):
        return out
    if pa.get('stop_loss') is not None:
        try:
            out['stop_loss'] = float(pa['stop_loss'])
        except (TypeError, ValueError):
            pass
    if pa.get('buy_range_low') is not None and pa.get('buy_range_high') is not None:
        try:
            out['buy_low'] = float(pa['buy_range_low'])
            out['buy_high'] = float(pa['buy_range_high'])
        except (TypeError, ValueError):
            pass
    return out


def _read_signals(cur: Any, stock_id: int, window: int = 3) -> dict[str, Any]:
    """短线信号层：复用 market_screener 平行纯函数对库内K线买卖双侧离线复算。

    只读 raw_kline/raw_kline_weekly（V8 合规），零网络零新增采集；
    失败静默降级为空结果（信号层是增量信息，不阻塞阶段判定主链路）。
    """
    empty: dict[str, Any] = {
        'kline_upto': None,
        'buy_today': [], 'sell_today': [],
        'buy_window': [], 'sell_window': [],
        'buy_resonances': [], 'sell_resonances': [],
    }
    try:
        from modules.market_screener import (
            _read_watchlist_klines,
            compute_watchlist_sell_result,
            compute_watchlist_signal_result,
        )

        daily, weekly = _read_watchlist_klines(cur, stock_id)
        if not daily:
            return empty
        buy = compute_watchlist_signal_result(daily, weekly, window=window)
        sell = compute_watchlist_sell_result(daily, weekly, window=window)
        upto = buy['kline_upto']
        return {
            'kline_upto': upto,
            'buy_today': [h for h in buy['matches'] if h['trigger_date'] == upto],
            'sell_today': [h for h in sell['sell_matches'] if h['trigger_date'] == upto],
            'buy_window': buy['matches'],
            'sell_window': sell['sell_matches'],
            'buy_resonances': buy['resonances'],
            'sell_resonances': sell['sell_resonances'],
        }
    except Exception as e:  # noqa: BLE001 —— 增量层失败不阻塞主链路
        logger.warning(f'[trader-advisor] 信号层复算失败 stock_id={stock_id}: {e}')
        return empty


def _gather_inputs(stock_id: int) -> dict[str, Any] | None:
    """汇总阶段判定所需的全部输入（契约数据 + K线结构 + 趋势 + 评级 + 持仓）。

    021BQ：①持仓读数改为账户无关聚合（SUM(quantity) + 加权平均成本）——
    修复多账户同股分仓时单行取（ORDER BY id LIMIT 1）失真的缺口（021W 约定）；
    ②新增短线信号层（买卖双侧离线复算）与价位层（最新日报 price_advice 零重算）。
    """
    from modules.data_adapter import load_stockdata_from_db
    from modules.trend_analyzer import analyze_trends

    data = load_stockdata_from_db(stock_id)
    if data is None or not data.close:
        return None

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT trade_date, close, high, low, volume FROM raw_kline '
            'WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 60',
            (stock_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT rating, total_score, price_advice FROM daily_reports "
            "WHERE stock_id = ? AND status = 'ok' AND report_type = 'daily' "
            'ORDER BY report_date DESC LIMIT 1',
            (stock_id,),
        )
        rep = cur.fetchone()
        # 021BQ 多账户聚合：同股多账户分仓 → 数量汇总 + 加权平均成本
        # （SUM+GROUP BY 账户无关聚合，天然满足 021W 多行约定无重复行）
        cur.execute(
            'SELECT SUM(quantity) AS total_qty, '
            'CASE WHEN SUM(quantity) > 0 '
            'THEN SUM(quantity * cost_price) / SUM(quantity) END AS avg_cost '
            'FROM holdings WHERE stock_id = ? AND quantity > 0',
            (stock_id,),
        )
        hold = cur.fetchone()
        signals = _read_signals(cur, stock_id)
    finally:
        conn.close()

    vs = _volume_structure(rows)
    trends = analyze_trends(data)
    return {
        'data': data,
        'vs': vs,
        'trends': trends,
        'rating': rep['rating'] if rep else None,
        'total_score': rep['total_score'] if rep else None,
        'price_advice': _load_price_advice(rep['price_advice'] if rep else None),
        'holding_qty': int(hold['total_qty'] or 0) if hold else 0,
        'cost_price': hold['avg_cost'] if hold else None,
        'signals': signals,
        # 021BR：近 60 根 (日期, 收盘) 旧→新——止损/破位触发日期回填用
        'kline_tail': [(str(r['trade_date']), float(r['close'])) for r in reversed(rows)],
    }


# ================================================================
# 阶段判定（6 态，强特征优先）
# ================================================================


def classify_stage(inputs: dict[str, Any]) -> dict[str, Any]:
    """按强特征优先级判定阶段，返回 {code, name, confidence, evidence}。

    优先级：顶部出货 > 主升 > 拉升初期 > 下跌 > 吸筹 > 震荡兜底。
    evidence 为白话依据列表（前端「判断依据」可展开区），空缺数据如实标注。
    """
    data = inputs['data']
    vs = inputs['vs']
    trends = inputs['trends']

    close: float = float(data.close)  # data.close 已确保非空（_gather_inputs 拦截）
    vol_trend = vs['vol_trend']
    fit_ratio = vs['fit_ratio']
    position = vs['position_pctile']
    divergence = vs['top_divergence']
    amp_shrink = vs['amplitude_shrink']
    holder_chg = data.holder_count_change_pct
    ma20 = data.ma20
    ma60 = data.ma60
    rsi = data.rsi_14

    tf = trends.get('timeframes') or {}
    monthly = (tf.get('monthly') or {}).get('trend')
    weekly = (tf.get('weekly') or {}).get('trend')
    daily = (tf.get('daily') or {}).get('trend')
    monthly_dn = monthly == 'down'
    weekly_up = weekly == 'up'
    daily_up = daily == 'up'

    evidence: list[str] = []
    if position is not None:
        evidence.append(f'位置：现价处于近60日区间 {position:.0%} 分位（{vs["low60"]:.2f}~{vs["high60"]:.2f}）')
    if vol_trend is not None:
        tone = '缩量' if vol_trend < 0.8 else ('放量' if vol_trend > 1.5 else '量能平稳')
        evidence.append(f'量能：5日均量为20日均量的 {vol_trend:.2f} 倍（{tone}）')
    if fit_ratio is not None:
        evidence.append(f'量价配合度：近20日 {fit_ratio:.0%}（价涨量增/价跌量缩的健康占比）')
    evidence.append(
        f'筹码：股东户数 {"+" if (holder_chg or 0) >= 0 else ""}{holder_chg:.1f}%'
        + ('（筹码集中）' if (holder_chg or 0) < -3 else ('（筹码分散，警惕派发）' if (holder_chg or 0) > 3 else ''))
        if holder_chg is not None else '筹码：股东户数数据缺失（看不到）'
    )
    if divergence:
        evidence.append('量价背离：近期新高日量能较前高显著萎缩（出货嫌疑的核心证据）')
    if amp_shrink:
        evidence.append('振幅收窄：近10日波动明显小于前期（横盘蓄势特征）')
    if data.ma5 and ma20:
        spread = (data.ma5 - ma20) / ma20 * 100
        evidence.append(f'均线：MA5 领先/落后 MA20 {spread:+.1f}%，MA20={ma20:.2f}，MA60={f"{ma60:.2f}" if ma60 else "缺"}')
    if rsi is not None:
        zone = '超卖(<30)' if rsi < 30 else ('超买(>70)' if rsi > 70 else '中性区间')
        evidence.append(f'动量：RSI14 = {rsi:.1f}（{zone}）')

    def _done(code: str, conf: str) -> dict[str, Any]:
        return {'code': code, 'name': STAGE_NAMES[code], 'confidence': conf, 'evidence': evidence}

    # ---- 1. 顶部出货：高位 + 量价背离 或 高位放量滞涨 ----
    if position is not None and position > 0.7:
        if divergence:
            return _done(STAGE_DISTRIBUTION, CONF_STRONG)
        if vol_trend is not None and vol_trend > 1.3 and daily != 'up' and fit_ratio is not None and fit_ratio < 0.5:
            return _done(STAGE_DISTRIBUTION, CONF_MID)
        if (holder_chg or 0) > 3 and daily != 'up':
            return _done(STAGE_DISTRIBUTION, CONF_MID)

    # ---- 2. 主升期：多头发散 + 量能放大或量价健康 + 脱离底部 ----
    bull_stack = bool(data.ma5 and ma20 and ma60 and data.ma5 > ma20 > ma60)
    if (
        bull_stack
        and data.ma5 and ma20 and (data.ma5 - ma20) / ma20 > 0.02
        and (
            (vol_trend is not None and vol_trend > 1.2)
            or (fit_ratio is not None and fit_ratio > 0.7)
        )
        and position is not None and position > 0.4
        and (weekly_up or daily_up)
    ):
        return _done(STAGE_MARKUP_FULL, CONF_STRONG)

    # ---- 3. 拉升初期：多头排列成型 + 站上中轨 + 量价健康（缩量惜售涨也算）----
    if (
        bull_stack
        and ma20 and close > ma20
        and fit_ratio is not None and fit_ratio >= 0.6
        and (daily_up or (vol_trend is not None and vol_trend > 1.2))
        and not (monthly_dn and weekly == 'down')
    ):
        return _done(STAGE_MARKUP_EARLY, CONF_MID)

    # ---- 4. 下跌期：月/周空头 + 中轨压制 ----
    if (monthly_dn or weekly == 'down') and ma20 and close < ma20:
        conf = CONF_STRONG if monthly_dn and weekly == 'down' else CONF_MID
        return _done(STAGE_DECLINE, conf)

    # ---- 5. 底部吸筹：低位 + 缩量 + 波动收窄（筹码结构加分） ----
    if (
        position is not None and position < 0.35
        and vol_trend is not None and vol_trend < 0.8
        and not (weekly == 'down' and monthly_dn and close < (ma60 or 0))
    ):
        if amp_shrink or (holder_chg or 0) < -3:
            return _done(STAGE_ACCUMULATION, CONF_MID)
        return _done(STAGE_ACCUMULATION, CONF_WEAK)

    # ---- 6. 震荡兜底 ----
    return _done(STAGE_RANGE, CONF_WEAK)


# ================================================================
# 主力行为解读（资金×筹码×杠杆 组合推断）
# ================================================================


def capital_narrative(data: Any) -> dict[str, Any]:
    """组合资金/筹码/杠杆信号推断主力意图，含分歧检测与盲区。

    话术原则：说「数据表明…大概率在…」，不说全知断言；证据不足如实说。
    """
    inflow5 = data.main_net_inflow_5day
    inflow_day = data.main_net_inflow
    holder_chg = data.holder_count_change_pct
    margin = data.margin_balance_chg

    details: list[str] = []
    blind_spots: list[str] = []
    if inflow5 is not None:
        details.append(
            f'主力资金近5日日均净{"流入" if inflow5 > 0 else "流出"} {abs(inflow5):,.0f} 万'
            + (f'（当日{"+" if (inflow_day or 0) >= 0 else "-"}{abs(inflow_day or 0):,.0f} 万）' if inflow_day is not None else '')
        )
    else:
        blind_spots.append('主力资金5日均值缺失')
    if holder_chg is not None:
        if holder_chg < -3:
            details.append(f'股东户数 {holder_chg:.1f}%——筹码向少数人集中（有人在收集）')
        elif holder_chg > 3:
            details.append(f'股东户数 +{holder_chg:.1f}%——筹码分散（主力可能在向散户派发）')
        else:
            details.append(f'股东户数 {holder_chg:+.1f}%——结构稳定')
    else:
        blind_spots.append('股东户数缺失（A股为季度/月度披露，非每日）')
    if margin is not None:
        details.append(
            f'融资余额{"增加" if margin > 0 else "减少"} {abs(margin):,.0f} 万——'
            + ('杠杆资金在进场（追涨盘，不稳定）' if margin > 0 else '杠杆在撤退/降风险')
        )

    # 组合推断
    flow_in = (inflow5 or 0) > 0
    chips_in = (holder_chg or 0) < -3  # 筹码集中=有人收
    if flow_in and chips_in:
        summary = '主力大概率在吸筹锁仓（资金流入 + 筹码集中）'
        tone = 'bullish'
    elif flow_in and not chips_in:
        summary = '资金在进场但筹码未集中——进攻性买入，跟随盘居多，持续性待确认'
        tone = 'bullish'
    elif not flow_in and chips_in:
        summary = '分歧——部分资金撤退，另一部分在收集筹码（户数在降），等待方向确认'
        tone = 'mixed'
    else:
        summary = '资金流出且筹码分散——撤退迹象明确，短期难有像样反弹'
        tone = 'bearish'

    if data.institution_hold_ratio is None:
        blind_spots.append('机构持仓比例缺失')
    blind_spots.append('无龙虎榜/大宗交易/北向明细——以上为间接证据链推断，非事实')

    return {'summary': summary, 'tone': tone, 'details': details, 'blind_spots': blind_spots}


# ================================================================
# 分歧检测 + 对策 + 裁决信号
# ================================================================

_RATING_WEAK = ('建议减仓', '强烈建议卖出')
_RATING_STRONG = ('推荐买入', '强烈推荐买入')


def detect_disagreement(rating: str | None, stage: dict[str, Any]) -> dict[str, Any] | None:
    """评级（动作主指令）与阶段判定的分歧检测。

    仅在边界档产生实质分歧；低置信度阶段判定不硬造分歧（默认跟评级）。
    021BR 补档：持有观望 × 强置信弱势阶段（下跌/顶部出货）→ 阶段领先于评级——
    评级门控未到的空隙档（仅强置信，避免边界抖动）。
    """
    if not rating:
        return None
    conf = stage.get('confidence')
    if conf == CONF_WEAK:
        return None
    code = stage['code']
    if rating in _RATING_WEAK and code in (STAGE_ACCUMULATION, STAGE_RANGE):
        return {'type': DISAG_REVERSAL}
    if rating in _RATING_STRONG and code in (STAGE_DISTRIBUTION, STAGE_DECLINE):
        return {'type': DISAG_RISK}
    if rating == '持有观望' and conf == CONF_STRONG and code in (STAGE_DISTRIBUTION, STAGE_DECLINE):
        return {'type': DISAG_STAGE_LEAD}
    return None


def build_playbook(
    stage: dict[str, Any],
    capital: dict[str, Any],
    rating: str | None,
    holding_qty: int,
    cost_price: float | None,
    close: float,
    disagreement: dict[str, Any] | None,
) -> dict[str, Any]:
    """对策 + 行动清单 + 裁决信号。

    分歧场景动作收窄：主指令仍是评级，但给出「放缓择时」与「裁决条件」，
    永不输出与评级相反的指令（测试锁死）。
    """
    stage_name = stage['name']
    ma20 = None
    actions: list[str] = []
    watch: list[str] = []
    headline = STAGE_PLAYBOOK[stage['code']]

    # MA20 作为通用裁决价位（从 evidence 里取——结构化存 stage 会更好，这里读 evidence 行）
    for line in stage.get('evidence', []):
        if line.startswith('均线：') and 'MA20=' in line:
            try:
                ma20 = float(line.split('MA20=')[1].split('，')[0])
            except (ValueError, IndexError):
                ma20 = None
            break

    prof = f'现价 {close:.2f}'
    if cost_price and holding_qty:
        pnl = (close - cost_price) / cost_price * 100
        prof = f'持仓 {holding_qty:,} 股 · 成本 {cost_price:.2f} · 现价 {close:.2f}（{"浮盈" if pnl >= 0 else "浮亏"} {pnl:+.1f}%）'
    elif holding_qty:
        prof = f'持仓 {holding_qty:,} 股 · 成本缺失'

    actions.append(f'阶段基调（{stage_name}）：{headline}')

    if disagreement and disagreement['type'] == DISAG_REVERSAL:
        actions.append(
            f'⚠️ 与评级分歧（评级「{rating}」但阶段特征偏底部）：减仓仍是主指令，'
            '但不建议恐慌割肉——底部缩量阴跌中，恐慌卖出的正是别人在收集的筹码；'
            '把减仓动作放缓到反弹时执行（回本位/MA20 附近）'
        )
        watch.append(f'转多确认：放量（量比>1.5）站上 MA20（{ma20:.2f}）→ 阶段论胜出，可小仓回补' if ma20
                     else '转多确认：放量（量比>1.5）站上 MA20 → 阶段论胜出，可小仓回补')
        watch.append('防守线：缩量跌破近期低点 → 评级论胜出，剩余仓位立即执行减仓/清仓')
    elif disagreement and disagreement['type'] == DISAG_RISK:
        actions.append(
            f'⚠️ 与评级分歧（评级「{rating}」但结构显示派发嫌疑）：评级动量仍强，'
            '但高位量价背离/筹码分散时，把买入改为「冲高分批兑现」——先卖一半锁定，'
            '剩余仓位跌破 MA20 全部了结'
        )
        watch.append(f'风险确认：跌破 MA20（{ma20:.2f}）→ 结构论胜出，清仓离场' if ma20
                     else '风险确认：跌破 MA20 → 结构论胜出，清仓离场')
        watch.append('评级胜出条件：缩量回踩 MA20 企稳再放量 → 继续按评级持有/加仓')
    elif disagreement and disagreement['type'] == DISAG_STAGE_LEAD:
        actions.append(
            f'⚠️ 阶段领先于评级（评级「{rating}」但阶段特征为{stage_name}）：'
            '评级尚未跟上结构变化——风控纪律无条件执行，减仓/离场听操盘手纪律，'
            '加仓继续看评级'
        )
        watch.append(f'评级跟上确认：放量站回 MA20（{ma20:.2f}）并企稳 → 阶段论让位，恢复按评级执行' if ma20
                     else '评级跟上确认：放量站回 MA20 并企稳 → 恢复按评级执行')
        watch.append('纪律优先：止损线/MA20 破位期间，减仓检查不等待评级')
    else:
        ma20_above = ma20 is not None and close > ma20
        if ma20_above:
            watch.append('上修信号：放量创新高（量比>1.5）→ 上修至主升期，移动止损跟 MA10')
            watch.append(
                f'下修信号：缩量跌破 MA20（{ma20:.2f}）→ 阶段判断回落，按评级执行风控'
            )
        else:
            watch.append(f'上修信号：放量（量比>1.5）站上 MA20（{ma20:.2f}）→ 上修阶段判断' if ma20
                         else '上修信号：放量站上 MA20 → 上修阶段判断')
            watch.append('下修信号：跌破近期低点且放量 → 下修阶段判断，按评级执行风控')

    if cost_price and holding_qty:
        actions.append(
            f'持仓纪律：最大亏损线 = 成本×0.92（{cost_price * 0.92:.2f}，价格建议卡的止损底线），'
            '触发即无条件执行，这是纪律不是观点'
        )

    return {'profile': prof, 'headline': headline, 'actions': actions, 'watch_signals': watch}


# ================================================================
# 021BQ 操作矩阵：短线信号×阶段联动 + 持仓/空仓双视角操作建议
#   主从契约不变：全部为「条件→动作」式行，与评级相悖时行内附调和注记，
#   永不输出与评级相反的无条件指令（测试锁死）。
# 021BR 分域不对称层级：纪律（止损/破位）无条件最高 > 减仓/离场听操盘手
#   （独立触发，评级未跟上时行内标注）> 买入/加仓评级门控（021BQ 三档不变）；
#   行增 layer/status 字段，共振行带触发日期与时效，止损已触发置顶状态行。
# ================================================================

# 021BR 分域不对称层级：矩阵行层级标注
LAYER_DISCIPLINE = '纪律'  # 风控纪律（止损/破位）——无条件最高，任何行不得与之冲突
LAYER_TACTICAL = '战术'    # 减仓/离场/回避——操盘手独立触发，不等评级
LAYER_STRATEGIC = '战略'   # 买入/加仓/持有维持——评级门控
HIERARCHY_NOTE = '纪律无条件执行 · 减仓听操盘手 · 加仓看评级'


def _stop_level(cost: float | None, price_advice: dict[str, Any] | None):
    """止损参考位：成本×0.92 纪律线 与 最新日报价格建议止损 取高者（先到先执行）。

    Returns: (level|None, source_str) —— 双源齐备时 source 标注取值口径。
    """
    disc = cost * 0.92 if cost else None
    pa = (price_advice or {}).get('stop_loss')
    candidates = [(disc, '纪律'), (pa, '价格建议')]
    vals = [(v, s) for v, s in candidates if v]
    if not vals:
        return None, None
    level, source = max(vals, key=lambda x: x[0])
    if len(vals) == 2:
        source = '纪律/价格建议取高者'
    return round(level, 2), source


def _row(action: str, trigger: str, level: str | None = None,
         level_value: float | None = None, source: str = '',
         note: str | None = None, layer: str | None = None,
         status: str | None = None) -> dict[str, Any]:
    """操作矩阵标准行：动作 + 触发条件 + 价位 + 来源（+ 相悖调和注记）。

    021BR：layer ∈ {纪律, 战术, 战略}（分域不对称层级标注）；
    status ∈ {'triggered', 'pending'}（触发状态回填，None=非触发型条件行）。
    """
    return {'action': action, 'trigger': trigger, 'level': level or '—',
            'level_value': level_value, 'source': source, 'note': note,
            'layer': layer, 'status': status}


def _break_trigger_date(kline_tail: list[tuple[str, float]],
                        level: float | None) -> str | None:
    """当前破位段的首日（触发日期，021BR 状态置顶用）。

    kline_tail 为 [(trade_date, close)] 旧→新；从最新一根向前回溯连续收于
    level 之下的 K 线，返回该段最早日期；最新一根未破位 → None。
    """
    if not level or not kline_tail:
        return None
    i = len(kline_tail) - 1
    trig: str | None = None
    while i >= 0 and kline_tail[i][1] < level:
        trig = kline_tail[i][0]
        i -= 1
    return trig


def _res_date_desc(res: dict[str, Any] | None, upto: str | None) -> str:
    """共振触发日期描述（021BR 时效标注）。

    从 resonance['signals']（'label@date + ...'，market_screener 同源）提取最新
    触发日：今日触发→「触发于 X（今日）」；窗口内历史触发→「触发于 X
    （窗口内历史，非今日）」；解析失败→「触发日不详」。历史触发不得读起来像新信号。
    """
    sig = str((res or {}).get('signals') or '')
    dates = re.findall(r'@(\d{4}-\d{2}-\d{2})', sig)
    if not dates:
        return '触发日不详'
    latest = max(dates)
    if upto and latest == upto:
        return f'触发于 {latest}（今日）'
    return f'触发于 {latest}（窗口内历史，非今日）'


def signal_stage_linkage(stage_code: str, rating: str | None,
                         signals: dict[str, Any] | None,
                         close: float | None = None,
                         stop_level: float | None = None) -> list[str]:
    """短线信号 × 阶段联动解读（白话行；操作矩阵的「联动解读」段）。

    语义锚点：弱势阶段的买点=超跌反弹（反抽减仓/不接飞刀）、
    上升阶段的卖点=趋势内回调（跌破 MA20 才执行）、
    底部买点=启动前兆（小仓试错）、震荡期信号=区间噪音（按区间执行）。
    信号×评级相悖时显式调和标注（与行动清单 021BP c23f9ee 同思路）。

    021BR：①共振行带触发日期与时效（_res_date_desc，历史触发不再像新信号）；
    ②止损纪律门控（层级契约①）——纪律已触发（close < stop_level）时，
    买点共振行不得输出「持仓者持有」，改写为「止损纪律已触发，共振仅作
    反抽减仓参考」；③弱势阶段买点共振与 buy_today 同语义（反抽减仓）；
    ④买点共振×减仓档评级附相悖调和（独立注记，勿复用行动清单元组）。
    """
    out: list[str] = []
    signals = signals or {}
    stop_hit = stop_level is not None and close is not None and close < stop_level
    upto = signals.get('kline_upto')
    buy_today = [h['label'] for h in signals.get('buy_today') or []]
    sell_today = [h['label'] for h in signals.get('sell_today') or []]
    if buy_today:
        labels = '、'.join(buy_today)
        if stage_code in (STAGE_DECLINE, STAGE_DISTRIBUTION):
            out.append(f'弱势阶段出现买点信号（{labels}）→ 大概率是超跌反弹而非反转：'
                       '持仓者把它当反抽减仓位，空仓者不接飞刀（等放量站上均线再确认）')
        elif stage_code in (STAGE_MARKUP_FULL, STAGE_MARKUP_EARLY):
            out.append(f'上升阶段出现买点信号（{labels}）→ 趋势内的加速/中继确认：'
                       '持仓者继续持有，空仓者注意不追高（等回踩）')
        elif stage_code == STAGE_ACCUMULATION:
            out.append(f'底部吸筹区出现买点信号（{labels}）→ 可能是启动前兆：'
                       '空仓者小仓试错（错了就走），持仓者继续持有等右侧')
        else:
            out.append(f'震荡期出现买点信号（{labels}）→ 区间内噪音居多：'
                       '按区间下沿试仓、上沿兑现执行，不追单日信号')
    if sell_today:
        labels = '、'.join(sell_today)
        if stage_code in (STAGE_MARKUP_FULL, STAGE_MARKUP_EARLY):
            out.append(f'上升阶段出现卖出信号（{labels}）→ 多头趋势内的回调警示：'
                       '持仓者关注减仓位（跌破 MA20 执行），空仓者不追高等回调结束')
        elif stage_code in (STAGE_DECLINE, STAGE_DISTRIBUTION):
            out.append(f'弱势阶段再出卖出信号（{labels}）→ 趋势走弱确认：'
                       '持仓者严格执行减仓/止损纪律，空仓者继续观望')
        else:
            out.append(f'震荡期出现卖出信号（{labels}）→ 破位预警：'
                       '持仓者收紧防守线（MA20/成本止损），空仓者回避新买入')
    sell_res = signals.get('sell_resonances') or []
    if sell_res:
        top = max(r['stars'] for r in sell_res)
        top_r = max(sell_res, key=lambda r: r['stars'])
        dd = _res_date_desc(top_r, upto)
        out.append(f'卖出侧共振成立（最高 {top} 星，{dd}）→ 离场证据强于单信号：'
                   '持仓者把减仓位提前，空仓者回避')
    buy_res = signals.get('buy_resonances') or []
    if buy_res:
        top = max(r['stars'] for r in buy_res)
        top_r = max(buy_res, key=lambda r: r['stars'])
        dd = _res_date_desc(top_r, upto)
        if stop_hit:
            out.append(f'买点侧共振（最高 {top} 星，{dd}）与止损纪律冲突——'
                       '止损纪律已触发，共振仅作反抽减仓参考，不构成持有理由')
        elif stage_code in (STAGE_DECLINE, STAGE_DISTRIBUTION):
            out.append(f'弱势阶段出现买点共振（最高 {top} 星，{dd}）→ '
                       '大概率是超跌反弹而非反转：持仓者把它当反抽减仓位，空仓者不接飞刀')
        else:
            out.append(f'买点侧共振成立（最高 {top} 星，{dd}）→ 入场证据强于单信号：'
                       '空仓者可按区间分批，持仓者持有')
        if not stop_hit and rating in _RATING_WEAK:
            out.append(f'买点共振与评级「{rating}」相悖：共振只作反抽参考——'
                       '不加仓，以评级为主')
    if sell_today and rating in _RATING_STRONG:
        out.append(f'卖出信号与评级「{rating}」相悖：评级是动作主指令——'
                   '信号仅波段参考，执行节奏放缓，以评级为主')
    if buy_today and rating in _RATING_WEAK:
        out.append(f'买点信号与评级「{rating}」相悖：按超跌反弹对待——'
                   '不加仓、不减仓（既有止损纪律优先），以评级为主')
    return out


def build_operations_matrix(
    data: Any,
    stage: dict[str, Any],
    rating: str | None,
    holding: dict[str, Any] | None,
    close: float,
    signals: dict[str, Any] | None,
    price_advice: dict[str, Any] | None = None,
    vs: dict[str, Any] | None = None,
    kline_tail: list[tuple[str, float]] | None = None,
) -> dict[str, Any]:
    """持仓/空仓双视角操作矩阵（021BQ 增量键 operations 的构造器，纯函数）。

    每行 = 触发条件 → 动作（含价位与来源标注）；持仓/空仓两套行同时输出，
    view 标注当前视角。价位三源：成本×0.92 纪律线、MA20、最新日报
    price_advice 的止损/买入区间（零重算读取）。

    021BR 分域不对称层级：每行带 layer（纪律/战术/战略）与 status
    （triggered/pending）；止损/破位已触发时 status 置顶状态行
    （现价/触发线/触发日期）；「持有」类行在纪律已触发时被纪律行取代，
    任何行不得与风控纪律冲突（层级契约①，测试锁死）。

    Returns: {
        'view': 'held'|'empty',
        'holding': {qty, cost, pnl_pct},
        'status': None | {kind, close, stop_line, trigger_date, ma20_broken, text},
        'hierarchy_note': '纪律无条件执行 · 减仓听操盘手 · 加仓看评级',
        'signals_today': [{signal,label,side,trigger_date}],
        'linkage': [信号×阶段联动解读...],
        'held_rows': [{action,trigger,level,level_value,source,note,layer,status}...],
        'empty_rows': [...],
        'top_action': '动作·价位（已触发）'（当前视角首行摘要，看板增量键）,
    }
    """
    signals = signals or {}
    price_advice = price_advice or {}
    qty = int((holding or {}).get('qty') or 0)
    cost = (holding or {}).get('cost')
    view = 'held' if qty > 0 else 'empty'
    ma20 = getattr(data, 'ma20', None)
    ma20_str = f'{ma20:.2f}' if ma20 else None
    low60 = (vs or {}).get('low60')

    buy_today = signals.get('buy_today') or []
    sell_today = signals.get('sell_today') or []
    sell_res = signals.get('sell_resonances') or []
    buy_res = signals.get('buy_resonances') or []
    upto = signals.get('kline_upto')
    sell_top_stars = max((r['stars'] for r in sell_res), default=0)
    buy_top_stars = max((r['stars'] for r in buy_res), default=0)

    # ---------- 021BR 纪律状态（层级契约①：止损/破位无条件最高） ----------
    stop, stop_source = _stop_level(cost, price_advice)
    stop_hit = stop is not None and close < stop
    stop_trig_date = _break_trigger_date(kline_tail or [], stop) if stop_hit else None
    ma20_broken = bool(ma20 and close < ma20)
    ma20_trig_date = _break_trigger_date(kline_tail or [], ma20) if (ma20 and close < ma20) else None

    status_line: dict[str, Any] | None = None
    if stop_hit:
        txt = f'止损纪律已触发：现价 {close:.2f} 低于触发线 {stop:.2f}'
        if stop_trig_date:
            txt += f'（{stop_trig_date} 起失守）'
        if ma20_broken:
            txt += '；同时已跌破 MA20，破位与止损同向'
        txt += '——无条件执行，不等评级、不等反抽'
        status_line = {
            'kind': 'stop_triggered',
            'close': round(close, 2),
            'stop_line': stop,
            'trigger_date': stop_trig_date,
            'ma20_broken': ma20_broken,
            'text': txt,
        }
    elif ma20_broken and view == 'held':
        txt = f'破位状态：现价 {close:.2f} 已跌破 MA20（{ma20:.2f}'
        if ma20_trig_date:
            txt += f'，{ma20_trig_date} 起失守'
        txt += '）——操盘手破位纪律待命：反抽不收复则降低仓位'
        status_line = {
            'kind': 'breakdown',
            'close': round(close, 2),
            'stop_line': stop,
            'trigger_date': ma20_trig_date,
            'ma20_broken': True,
            'text': txt,
        }

    signals_today = (
        [{'signal': h['signal'], 'label': h['label'], 'side': 'buy',
          'trigger_date': h['trigger_date']} for h in buy_today]
        + [{'signal': h['signal'], 'label': h['label'], 'side': 'sell',
            'trigger_date': h['trigger_date']} for h in sell_today]
    )

    # 信号×评级相悖调和注记（主从契约：显式标注不装一致）
    conflict_note = None
    if sell_today and rating in _RATING_STRONG:
        conflict_note = (f'卖出信号与评级「{rating}」相悖——评级是动作主指令，'
                         '信号仅波段参考，以评级为主')
    buy_conflict_note = None
    if buy_today and rating in _RATING_WEAK:
        buy_conflict_note = (f'买点信号与评级「{rating}」相悖——按超跌反弹对待，'
                             '不加仓，以评级为主')

    # ---------- 持仓视角：纪律置顶 → 止损 → 持有/破位（分域） → 减仓 → 仓位纪律 ----------
    held: list[dict[str, Any]] = []
    if stop is not None:
        trig_txt = f'收盘跌破 {stop:.2f} 触发即无条件执行（这是纪律不是观点）'
        if stop_hit:
            trig_txt += (f'——已触发：现价 {close:.2f} 低于触发线'
                         + (f'（{stop_trig_date} 起失守）' if stop_trig_date else '')
                         + '，无条件执行不等待')
        held.append(_row(
            '止损', trig_txt,
            level=f'{stop:.2f}', level_value=stop, source=stop_source,
            layer=LAYER_DISCIPLINE, status='triggered' if stop_hit else 'pending'))
    # 021BR 分域注记：减仓域评级对齐状态（观望/买入档=评级尚未跟上、减仓档=同向）
    # ——止损/破位行不推给评级（层级契约②）；卖出信号×强档评级另走 021BQ
    # 锁定的相悖调和（conflict_note，见下）
    risk_note: str | None = None
    if rating and rating not in _RATING_WEAK:
        risk_note = f'操盘手纪律触发，评级尚未跟上（当前评级 {rating}）'
    elif rating in _RATING_WEAK:
        risk_note = f'与评级「{rating}」同向'
    if ma20:
        if close >= ma20 and not stop_hit:
            held.append(_row(
                '持有', f'收盘站稳 MA20（{ma20_str}）上方且无卖出信号 → 继续持有',
                level=f'MA20={ma20_str}', level_value=ma20, source='阶段+技术面',
                layer=LAYER_STRATEGIC))
        elif close < ma20:
            held.append(_row(
                '减仓检查', f'收盘已跌破 MA20（{ma20_str}）→ 操盘手破位纪律触发：'
                '反抽不收复 MA20 则降低仓位',
                level=f'MA20={ma20_str}', level_value=ma20, source='阶段+技术面',
                note=risk_note, layer=LAYER_TACTICAL, status='triggered'))
        else:  # 收盘在 MA20 上方但止损纪律已触发——纪律优先，不输出「持有」（层级契约①）
            held.append(_row(
                '减仓检查', f'收盘在 MA20（{ma20_str}）上方，但止损纪律已触发'
                f'（现价 {close:.2f}，低于触发线 {stop:.2f}）→ 纪律优先：先执行止损，'
                'MA20 上方仅作反抽退出参考',
                level=f'MA20={ma20_str}', level_value=ma20, source='阶段+技术面',
                note=risk_note, layer=LAYER_TACTICAL, status='triggered'))
    if sell_today or sell_top_stars >= 4:
        if sell_today:
            trig = ('今日已出现 ' + '、'.join(h['label'] for h in sell_today)
                    + (f'（截至 {upto}）' if upto else '') + ' → 执行减仓检查')
        else:
            top_res = max(sell_res, key=lambda r: r['stars'])
            trig = (f'窗口内出现 {top_res["label"]}（{top_res["stars"]}星，'
                    f'{_res_date_desc(top_res, upto)}）→ 反弹即分批减仓')
        if ma20 and close < ma20:
            level_str = f'反抽 MA20（{ma20_str}）附近分批减'
        elif ma20:
            level_str = f'跌破 MA20（{ma20_str}）即分批离场'
        else:
            level_str = '分批降低仓位'
        if low60 and ma20:
            level_str += f'；前低 {low60:.2f} 为最后防线'
        # 021BR 分域：减仓听操盘手——强档评级保留 021BQ 锁定的相悖调和，
        # 观望/买入档=独立触发标注（层级契约②），减仓档=同向
        note: str | None
        if conflict_note:
            note = conflict_note
        else:
            note = risk_note
        held.append(_row('减仓', trig, level=level_str,
                         level_value=ma20, source='技术信号', note=note,
                         layer=LAYER_TACTICAL,
                         status='triggered' if sell_today else None))
    else:
        held.append(_row(
            '减仓（条件）', f'若出现 MACD/KDJ 死叉或收盘跌破 MA20（{ma20_str or "—"}）'
            '→ 减仓检查',
            level=f'MA20={ma20_str}' if ma20 else '—', level_value=ma20,
            source='技术信号', layer=LAYER_TACTICAL))
    if cost and qty:
        pnl = (close - cost) / cost * 100
        held.append(_row(
            '仓位纪律', f'持仓 {qty:,} 股 · 成本 {cost:.2f} · '
            f'浮动{"盈" if pnl >= 0 else "亏"} {pnl:+.1f}%——{HIERARCHY_NOTE}',
            source='分域层级', layer=LAYER_STRATEGIC))

    # ---------- 空仓视角：回避/观望 → 买入触发/关注 → 等待信号（买入域评级门控不变） ----------
    empty_rows: list[dict[str, Any]] = []
    if sell_today or sell_top_stars >= 4:
        if sell_today:
            trig = ('今日已出现 ' + '、'.join(h['label'] for h in sell_today)
                    + ' → 回避新买入，等企稳')
        else:
            top_res = max(sell_res, key=lambda r: r['stars'])
            trig = (f'窗口内出现 {top_res["label"]}（{top_res["stars"]}星，'
                    f'{_res_date_desc(top_res, upto)}）→ 回避新买入')
        empty_rows.append(_row(
            '回避', trig,
            level=f'等放量站回 MA20（{ma20_str}）再确认' if ma20 else '等右侧确认',
            level_value=ma20, source='技术信号', layer=LAYER_TACTICAL))
    else:
        empty_rows.append(_row(
            '观望', '无卖出信号但左侧未反转 → 观望，等右侧确认信号',
            level=f'关注 MA20（{ma20_str}）方向' if ma20 else '—',
            level_value=ma20, source='阶段+技术面', layer=LAYER_STRATEGIC))
    if buy_today:
        if rating in _RATING_STRONG:
            tone = f'评级「{rating}」支持，可分批执行'
            action = '买入触发'
            note = None
        elif rating == '持有观望':
            tone = f'评级「{rating}」中性——小仓试错，错了就走'
            action = '试仓触发'
            note = None
        elif rating in _RATING_WEAK:
            tone = f'评级「{rating}」不支持新买入——仅观察不买入'
            action = '试仓观察'
            note = buy_conflict_note
        else:
            tone = '无评级——仅观察，不作为新买入依据'
            action = '试仓观察'
            note = None
        if price_advice.get('buy_low') and price_advice.get('buy_high'):
            level_str = (f'买入区间 {price_advice["buy_low"]:.2f}'
                         f'~{price_advice["buy_high"]:.2f}（价格建议）')
            lv = price_advice['buy_low']
        else:
            level_str = '分批小仓（首仓不超过计划仓位 1/3）'
            lv = None
        empty_rows.append(_row(
            action, '今日出现 ' + '、'.join(h['label'] for h in buy_today)
            + ' → 条件触发；' + tone,
            level=level_str, level_value=lv, source='技术信号+价格建议', note=note,
            layer=LAYER_STRATEGIC))
    elif buy_top_stars >= 4:
        top_res = max(buy_res, key=lambda r: r['stars'])
        empty_rows.append(_row(
            '关注', f'窗口内买点共振 {top_res["label"]}（{top_res["stars"]}星，'
            f'{_res_date_desc(top_res, upto)}）'
            '→ 列入观察，等触发日落定（以收盘为准）',
            level='等今日确认', source='技术信号', layer=LAYER_STRATEGIC))
    else:
        empty_rows.append(_row(
            '等待信号', '无买点事件 → 不预判，等信号触发再执行（不抄底不猜顶）',
            source='纪律', layer=LAYER_STRATEGIC))

    cur_rows = held if view == 'held' else empty_rows
    top_action = None
    if cur_rows:
        r0 = cur_rows[0]
        trig_suffix = '（已触发）' if r0.get('status') == 'triggered' else ''
        top_action = (r0['action']
                      + (f'·{r0["level"]}' if r0['level_value'] is not None else '')
                      + trig_suffix)

    return {
        'view': view,
        'holding': {'qty': qty, 'cost': cost,
                    'pnl_pct': round((close - cost) / cost * 100, 1) if cost and qty else None},
        'status': status_line,
        'hierarchy_note': HIERARCHY_NOTE,
        'signals_today': signals_today,
        'linkage': signal_stage_linkage(stage['code'], rating, signals,
                                        close=close, stop_level=stop),
        'held_rows': held,
        'empty_rows': empty_rows,
        'top_action': top_action,
    }


# ================================================================
# 主入口
# ================================================================


def generate_trader_advice(stock_id: int) -> dict[str, Any]:
    """操盘手建议主入口：阶段 + 主力 + 对策 + 分歧（只读，失败返回 available=False）。"""
    try:
        inputs = _gather_inputs(stock_id)
        if inputs is None:
            return {'available': False, 'reason': '数据不足，请先采集数据'}

        data = inputs['data']
        stage = classify_stage(inputs)
        capital = capital_narrative(data)
        disagreement = detect_disagreement(inputs['rating'], stage)
        playbook = build_playbook(
            stage, capital, inputs['rating'],
            inputs['holding_qty'], inputs['cost_price'], float(data.close),
            disagreement,
        )
        # 021BQ 操作矩阵（增量键）：持仓/空仓双视角 + 短线信号联动，
        # 失败静默降级（不阻塞既有阶段/主力/对策主链路）
        # 021BR：kline_tail 供止损/破位触发日期回填
        try:
            operations = build_operations_matrix(
                data, stage, inputs['rating'],
                {'qty': inputs['holding_qty'], 'cost': inputs['cost_price']},
                float(data.close), inputs.get('signals'),
                inputs.get('price_advice'), vs=inputs.get('vs'),
                kline_tail=inputs.get('kline_tail'),
            )
        except Exception as e:  # noqa: BLE001 —— 增量层失败不阻塞主链路
            logger.warning(f'[trader-advisor] 操作矩阵构造失败 stock_id={stock_id}: {e}')
            operations = None

        result: dict[str, Any] = {
            'available': True,
            'stage': stage,
            'capital': capital,
            'playbook': playbook,
            'disagreement': None,
            'rating': inputs['rating'],
            'total_score': inputs['total_score'],
            'disclaimer': '阶段与主力判断为规则化推断（参考非指令），'
                          f'{HIERARCHY_NOTE}；不构成投资建议',
        }
        if operations is not None:
            result['operations'] = operations
        if disagreement:
            if disagreement['type'] == DISAG_REVERSAL:
                result['disagreement'] = {
                    'type': DISAG_REVERSAL,
                    'text': f'与评级「{inputs["rating"]}」分歧：阶段特征更接近{stage["name"]}——'
                            '评分看的是动量走弱，阶段看的是筹码换手结构。主指令不变，执行节奏放缓。',
                }
            elif disagreement['type'] == DISAG_STAGE_LEAD:
                result['disagreement'] = {
                    'type': DISAG_STAGE_LEAD,
                    'text': f'阶段领先于评级：阶段特征为{stage["name"]}'
                            f'（{stage["confidence"]}置信）而评级仍「{inputs["rating"]}」'
                            '——评级尚未跟上结构变化；风控纪律（止损/破位）无条件执行，'
                            '减仓/离场听操盘手纪律，加仓继续看评级。',
                }
            else:
                result['disagreement'] = {
                    'type': DISAG_RISK,
                    'text': f'与评级「{inputs["rating"]}」分歧：结构显示派发嫌疑（量价背离/筹码分散）——'
                            '评级动量仍强，但把加仓动作改为冲高分批兑现。',
                }
        return result
    except Exception as e:  # noqa: BLE001
        logger.warning(f'[trader-advisor] stock_id={stock_id}: {e}', exc_info=True)
        return {'available': False, 'reason': f'计算异常: {e}'}
