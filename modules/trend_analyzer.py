"""趋势罗盘——个股多周期趋势判定（2026-09-07，随遗留三事批次新增）。

回答一个问题：这只股票现在处于 上涨 / 下跌 / 震荡 哪种趋势？——分三个周期看：
    短期 = 日线（快线 MA5 vs 慢线 MA20）
    中期 = 周线（快线 10周线 vs 慢线 20周线）
    长期 = 月线（快线 5月线 vs 慢线 10月线）

判定规则（每周期 5 个信号加权打分，总分 ±5，阈值 ±2.5）：
    1. 均线排列（快线 > 慢线）        ±2.0   （多头/空头排列是趋势的骨架，权重最高）
    2. 现价站上/跌破 慢线             ±1.0
    3. MACD：DIF > DEA（金叉态）      ±1.0
    4. MACD：DIF 零轴上/下            ±0.5
    5. 现价在 快线 上/下              ±0.5
    score >= +2.5 → up（上涨）；score <= -2.5 → down（下跌）；其间 → sideways（震荡）
    置信度：|score| >= 4 强；>= 3 中；>= 2.5 弱。

综合（overall，021BN 重设计：月线定方向，分歧做注记）：
    月线方向明确（up/down）时，综合方向整体采纳月线判定（trend/score/strength，
    强度已含 MACD 零轴诚实化封顶），分歧周期给白话注记；
    月线震荡或数据不足时，退回 月2:周1.5:日1 加权平均，同一阈值三分类。
    三个周期同为 up/down 时给 resonance（多周期共振）提示。

    旧版为纯加权平均，存在结构性矛盾：月线满分贡献 2×5/4.5=2.22 < 阈值2.5，
    数学上月线独自再空也判不出"下跌"，与文案"以月线方向为主参考"互相矛盾
    （实测中国中免：月线下跌·强、日线上涨 → 综合"震荡"，用户无所适从）。

颜色惯例（A股习惯，前端映射）：上涨=红、下跌=绿、震荡=黄、数据不足=灰。
纯函数、只读：不写库、不发网络请求；字段缺失的周期返回 na（数据不足），不影响其他周期。
"""

from modules.data_contract import StockData

UP = 'up'
DOWN = 'down'
SIDEWAYS = 'sideways'
NA = 'na'

_TREND_LABEL = {UP: '上涨', DOWN: '下跌', SIDEWAYS: '震荡', NA: '数据不足'}

# 周期定义：key → (中文名, 周期字段说明)
TIMEFRAME_LABELS = {
    'daily': '短期（日线）',
    'weekly': '中期（周线）',
    'monthly': '长期（月线）',
}

# 综合判定的周期权重：仅在月线方向不明（震荡/数据不足）时用于加权回退；
# 月线方向明确时综合直接采纳月线判定（021BN：月线定方向，见 analyze_trends 注释）
_OVERALL_WEIGHTS = {'daily': 1.0, 'weekly': 1.5, 'monthly': 2.0}


def _classify(close: float, ma_fast, ma_slow, dif, dea, fast_label: str = '快线',
              slow_label: str = '慢线') -> dict:
    """单周期趋势判定：返回 {trend, score, strength, reasons}。

    任意核心字段缺失（均线对或 MACD 对不齐）时返回 na——宁可说数据不足，不硬猜。
    理由在判定后分配：与最终趋势同向的信号作主因，反向信号以「但」提示分歧。
    fast_label/slow_label：该周期快/慢均线的中文名（用于白话理由）。
    """
    if close is None or ma_fast is None or ma_slow is None or dif is None or dea is None:
        return {
            'trend': NA,
            'score': 0.0,
            'strength': '',
            'reasons': ['该周期均线/MACD 数据不足'],
        }

    fast_name, slow_name = fast_label, slow_label

    # 信号清单：(是否偏多, 权重, 白话)
    signals: list[tuple[bool, float, str]] = []
    if ma_fast > ma_slow:
        signals.append((True, 2.0, f'{fast_name} 在 {slow_name} 上方（多头排列）'))
    elif ma_fast < ma_slow:
        signals.append((False, 2.0, f'{fast_name} 在 {slow_name} 下方（空头排列）'))
    else:
        signals.append((True, 0.0, f'{fast_name} 与 {slow_name} 黏合走平'))
    if close > ma_slow:
        signals.append((True, 1.0, f'现价站上 {slow_name}'))
    else:
        signals.append((False, 1.0, f'现价跌破 {slow_name}'))
    if dif > dea:
        signals.append((True, 1.0, 'MACD 双线金叉（DIF 高于 DEA）'))
    else:
        # 2026-09-07：文案含 "<DEA)" 会被前端 innerHTML 当 HTML 标签吞掉
        # （实测罗盘月线理由显示到 "MACD 双线死叉（DIF" 截断），改文字描述
        signals.append((False, 1.0, 'MACD 双线死叉（DIF 低于 DEA）'))
    if dif > 0:
        signals.append((True, 0.5, 'MACD 红柱区（DIF 高于 0）'))
    else:
        signals.append((False, 0.5, 'MACD 绿柱区（DIF 低于 0）'))
    if close > ma_fast:
        signals.append((True, 0.5, f'现价在 {fast_name} 上方'))
    else:
        signals.append((False, 0.5, f'现价在 {fast_name} 下方'))

    score = round(sum(w if up else -w for up, w, _ in signals), 2)
    trend = UP if score >= 2.5 else (DOWN if score <= -2.5 else SIDEWAYS)
    raw_strength = '强' if abs(score) >= 4 else ('中' if abs(score) >= 3 else '弱')
    # 强度诚实化（2026-09-07，用户实测中国中免反馈）：MACD 动能未过零轴时，
    # 趋势只是"反弹/回落修复"阶段，未获动能确认——强度上限"中"，不再标"强"。
    if (trend == UP and dif <= 0) or (trend == DOWN and dif >= 0):
        strength = '中' if raw_strength == '强' else raw_strength
    else:
        strength = raw_strength

    # 理由分配：与最终趋势同向 → 主因（按权重取前3）；反向 → 「但」提示（最多1条）
    if trend == SIDEWAYS:
        ups = sorted(((w, t) for up, w, t in signals if up and w > 0), reverse=True)
        downs = sorted(((w, t) for up, w, t in signals if not up), reverse=True)
        reasons = ['多空信号交织、方向待选择']
        if downs:
            reasons.append(downs[0][1])
        if ups:
            reasons.append('但 ' + ups[0][1])
    else:
        want_up = trend == UP
        matched = [t for up, _, t in signals if up == want_up]
        reasons = matched[:3]
        if want_up and dif <= 0:
            reasons.append('但 MACD 仍在零轴下方：属反弹修复阶段，尚非强势上涨')
        elif (not want_up) and dif >= 0:
            reasons.append('但 MACD 已在零轴上方：属回落中的强势整理，留意企稳信号')
        else:
            opposed = [t for up, _, t in signals if up != want_up]
            if opposed and len(reasons) < 4:
                reasons.append('但 ' + opposed[0])
    return {'trend': trend, 'score': score, 'strength': strength, 'reasons': reasons[:4]}


def analyze_trends(data: StockData) -> dict:
    """三周期趋势判定 + 综合共振。

    Returns:
        {
          'timeframes': {'daily': {...}, 'weekly': {...}, 'monthly': {...}},
          'overall': {'trend', 'score', 'strength', 'reasons', 'resonance'},
        }
        其中每周期：{trend: up/down/sideways/na, score: ±5, strength: 强/中/弱/空, reasons: [白话]}
    """
    close = data.close
    timeframes = {
        'daily': _classify(close, data.ma5, data.ma20, data.macd_dif, data.macd_dea,
                           fast_label='5日线', slow_label='20日线'),
        'weekly': _classify(
            close, data.weekly_ma10, data.weekly_ma20, data.weekly_macd_dif, data.weekly_macd_dea,
            fast_label='10周线', slow_label='20周线',
        ),
        'monthly': _classify(
            close, data.monthly_ma5, data.monthly_ma10, data.monthly_macd_dif, data.monthly_macd_dea,
            fast_label='5月线', slow_label='10月线',
        ),
    }

    # 综合（021BN：月线定方向，分歧做注记；规则见文件头 docstring）
    weighted_sum = 0.0
    weight_total = 0.0
    for key, r in timeframes.items():
        if r['trend'] != NA:
            w = _OVERALL_WEIGHTS[key]
            weighted_sum += r['score'] * w
            weight_total += w

    if weight_total <= 0:
        overall = {
            'trend': NA,
            'score': 0.0,
            'strength': '',
            'reasons': ['三个周期数据都不足，请先采集数据'],
            'resonance': '',
        }
    else:
        monthly = timeframes['monthly']
        trender = {UP: '上涨', DOWN: '下跌', SIDEWAYS: '震荡'}
        parts_str = '、'.join(
            f"{TIMEFRAME_LABELS[k].split('（')[0]}{trender[r['trend']]}"
            for k, r in timeframes.items()
            if r['trend'] != NA
        )

        if monthly['trend'] in (UP, DOWN):
            # 规则1：月线定方向——综合直接采纳月线判定，分歧周期白话注记
            otrend = monthly['trend']
            oscore = monthly['score']
            strength = monthly['strength']
            known = [t for t in timeframes.values() if t['trend'] != NA]
            all_same = all(r['trend'] == otrend for r in known) and len(known) >= 2
            if all_same and len(known) == 3:
                reasons = [f'短/中/长三个周期共振{trender[otrend]}，{trender[otrend]}方向较为扎实']
                resonance = f'三周期共振{trender[otrend]}'
            else:
                reasons = [parts_str + f'，综合方向随月线（{trender[otrend]}，长期权重最高）']
                if timeframes['daily']['trend'] not in (NA, otrend):
                    reasons.append(
                        '日线反弹属修复，未获月线确认' if otrend == DOWN else '日线走弱未改月线上行'
                    )
                if timeframes['weekly']['trend'] == SIDEWAYS:
                    reasons.append('周线方向待选择')
                elif timeframes['weekly']['trend'] not in (NA, otrend):
                    reasons.append('周线走强与月线相悖' if otrend == DOWN else '周线走弱与月线相悖')
                resonance = ''
        else:
            # 规则2：月线方向不明（震荡/缺数据）→ 加权平均三分类，文案如实描述口径
            oscore = round(weighted_sum / weight_total, 2)
            otrend = UP if oscore >= 2.5 else (DOWN if oscore <= -2.5 else SIDEWAYS)
            strength = '强' if abs(oscore) >= 4 else ('中' if abs(oscore) >= 3 else '弱')
            known = [t for t in timeframes.values() if t['trend'] != NA]
            all_same = all(r['trend'] == otrend for r in known) and len(known) >= 2
            if all_same and len(known) == 3:
                reasons = [f'短/中/长三个周期共振{trender[otrend]}，{trender[otrend]}方向较为扎实']
                resonance = f'三周期共振{trender[otrend]}'
            else:
                reasons = [parts_str + '，各周期方向不一致，按月2:周1.5:日1加权取综合']
                resonance = ''

        overall = {
            'trend': otrend,
            'score': oscore,
            'strength': strength,
            'reasons': reasons,
            'resonance': resonance,
        }

    return {'timeframes': timeframes, 'overall': overall}
