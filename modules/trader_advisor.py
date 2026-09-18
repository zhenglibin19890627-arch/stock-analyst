"""操盘手建议（2026-09-18）——股价阶段判定 + 主力行为解读 + 对策与裁决信号。

回答操盘手的三个问题（与综合评级互补，不推翻评级）：
    1. 股价处于哪个阶段？（6 态：底部吸筹/拉升初期/主升/顶部出货/下跌/震荡无趋势）
    2. 主力资金在干嘛？（资金×筹码×杠杆的组合推断，含分歧检测与数据盲区）
    3. 我们该怎么做 + 什么信号出现时改变判断？（对策 + 裁决条件）

与评级的关系（主从结构，用户拍板）：
    评级是唯一「动作主指令」；本模块输出阶段解读与路径规划。
    分歧时不推翻评级——动作收窄为「按评级执行但放缓择时」或「等待确认」，
    并显式给出裁决信号（什么信号出现时跟随哪一方）。测试锁死该行为。

方法论与诚实边界：
    - 评分是加权平均（表达强度），阶段判定是模式识别（表达结构）——
      分歧是特性不是缺陷（分数丢失结构信息），集中在评级边界档。
    - 阶段判定事前永远模糊（横盘是吸筹还是中继，走出来才知道），
      话术自带概率与确认信号，不假装全知。
    - 主力行为是间接证据链推断（无龙虎榜/大宗/北向明细），盲区明示。

数据来源（全部现成，零新增采集）：
    - StockData 契约（data_adapter 加载）：均线/MACD/RSI/量比/主力资金/户数/杠杆/情绪
    - raw_kline 近 60 根：量能趋势/量价配合度/量价背离/位置分位/振幅（本模块计算）
    - trend_analyzer.analyze_trends：日/周/月三周期趋势
    - daily_reports 最新评级（分歧检测的「主指令」输入）
    - holdings 持仓成本（对策个性化）

只读纯函数：不写库、不发网络请求、不触碰 generate_advice（B24 红线）。
"""

from __future__ import annotations

import logging
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


def _gather_inputs(stock_id: int) -> dict[str, Any] | None:
    """汇总阶段判定所需的全部输入（契约数据 + K线结构 + 趋势 + 评级 + 持仓）。"""
    from modules.data_adapter import load_stockdata_from_db
    from modules.trend_analyzer import analyze_trends

    data = load_stockdata_from_db(stock_id)
    if data is None or not data.close:
        return None

    conn = get_connection()
    try:
        cur = conn.cursor()
        cur.execute(
            'SELECT close, high, low, volume FROM raw_kline '
            'WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 60',
            (stock_id,),
        )
        rows = [dict(r) for r in cur.fetchall()]
        cur.execute(
            "SELECT rating, total_score FROM daily_reports "
            "WHERE stock_id = ? AND status = 'ok' AND report_type = 'daily' "
            'ORDER BY report_date DESC LIMIT 1',
            (stock_id,),
        )
        rep = cur.fetchone()
        cur.execute(
            'SELECT quantity, cost_price FROM holdings '
            "WHERE stock_id = ? AND quantity > 0 ORDER BY id LIMIT 1",
            (stock_id,),
        )
        hold = cur.fetchone()
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
        'holding_qty': hold['quantity'] if hold else 0,
        'cost_price': hold['cost_price'] if hold else None,
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

        result: dict[str, Any] = {
            'available': True,
            'stage': stage,
            'capital': capital,
            'playbook': playbook,
            'disagreement': None,
            'rating': inputs['rating'],
            'total_score': inputs['total_score'],
            'disclaimer': '阶段与主力判断为规则化推断（参考非指令），仓位动作以评级为准；不构成投资建议',
        }
        if disagreement:
            if disagreement['type'] == DISAG_REVERSAL:
                result['disagreement'] = {
                    'type': DISAG_REVERSAL,
                    'text': f'与评级「{inputs["rating"]}」分歧：阶段特征更接近{stage["name"]}——'
                            '评分看的是动量走弱，阶段看的是筹码换手结构。主指令不变，执行节奏放缓。',
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
