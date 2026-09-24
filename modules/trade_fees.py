"""
021BK 交易费用估算（交割单隔日才出，录入流水时自动估算、事后可在编辑中改实际值）

费率口径（2026 现行）：
  A股：佣金（按券商账户匹配）+ 印花税 0.05%（卖出单边）+ 过户费 0.001%（双边）
  港股：佣金（同券商）+ 印花税 0.1%（双边）+ 杂费约 0.01%（简化模型）

021BV（2026-09-24）：①银河档免 5（config 注释载裁定与回退路径，t1 诊断
docs/reports/021bv_fee_diag_20260923.md）；②估算改为分项各自取整到分再求和
——与交割单分项粒度对齐（消除「总和先加后舍」的 1 分差，t1 §2 审计点6）。

021BW（2026-09-24）：新增分项纯函数 estimate_trade_fee_items（佣金/印花税/
过户费/杂费/合计 + 适用规则说明，供录流水实时预估端点与前端分列展示），
estimate_trade_fee 改为其 total 透传（单一事实源，既有签名与对外行为零变化）；
新增 resolve_broker_profile_for_account（方案 A：broker 字段优先、账户名兜底），
落库估算与预估端点共用同一解析，防「预估免5、落库地板」分叉。

原则：纯函数、零网络、离线可测；估算值入库标记 commission_estimated=1，
用户编辑填入实际值后标记清除（021X 口径不变：买入计入成本，卖出扣减已实现盈亏）。
"""

from typing import cast

from config import (
    TRADE_FEE_A_STOCK,
    TRADE_FEE_BROKER_DEFAULT,
    TRADE_FEE_BROKERS,
    TRADE_FEE_HK,
)


def resolve_broker_profile(account_name):
    """账户名 → 券商佣金档（keywords 子串命中；无名/未命中用默认档）。"""
    name = str(account_name or '')
    for prof in TRADE_FEE_BROKERS:
        if any(k in name for k in prof['keywords']):
            return prof
    return TRADE_FEE_BROKER_DEFAULT


def resolve_broker_profile_for_account(name: str | None, broker: str | None = None) -> dict:
    """021BW 方案 A：账户 → 券商佣金档——broker 字段优先、账户名兜底。

    broker 非空先按 keywords 匹配，未命中或为空回落账户名 name 匹配
    （即 resolve_broker_profile 语义），双空/都未命中 → 默认档。
    落库估算（trades.api_add_trade）与预估端点共用本函数，
    保证「预估条」与「落库佣金」永远同一券商档。
    """
    broker_s = str(broker or '').strip()
    if broker_s:
        for prof in TRADE_FEE_BROKERS:
            if any(k in broker_s for k in cast(list[str], prof['keywords'])):
                return prof
    return resolve_broker_profile(name)


def _fmt_wan(rate: float) -> str:
    """费率 → 「万X」数字文案（rate×10000 去尾零：0.0001853→1.853、0.00015→1.5）。"""
    return f'{rate * 10000:g}'


def _broker_label(prof: dict) -> str:
    """佣金档 → 展示标签。纯 config 派生文案（不含任何用户输入，可安全进 innerHTML）。"""
    rate_str = _fmt_wan(prof['commission_rate'])
    if prof.get('keywords'):
        tail = '免5' if prof['commission_min'] == 0 else f"最低佣金{prof['commission_min']:g}元"
        return f"{prof['keywords'][0]}档：万{rate_str}·{tail}"
    return f'默认档：万{rate_str}·最低佣金{prof["commission_min"]:g}元'


def estimate_trade_fee_items(
    trade_type: str | None,
    amount: float | None,
    account_name: str | None = None,
    market: str = 'a_stock',
    broker: str | None = None,
) -> dict:
    """021BW 分项估算（与 estimate_trade_fee 同参同口径，另支持 broker 优先解析）。

    trade_type: buy / sell（dividend / dividend_tax 不计交易费用，全 0）
    amount:     成交金额（price×quantity 或直填）；<=0 返回全 0 空态
    broker:     账户券商字段（非空优先于 account_name 匹配，方案 A 口径）

    Returns: {
        'commission': float,    # 佣金（含最低佣金/免5 规则后，两位小数）
        'stamp_tax': float,     # 印花税（A股仅卖出；港股双边）
        'transfer_fee': float,  # 过户费（A股双边；港股为 0）
        'misc_fee': float,      # 港股杂费（A股恒 0）
        'total': float,         # 分项和（两位小数）
        'applied_rules': [str], # 逐条中文规则说明（禁裸 '<'）
        'by_rate': bool,        # 佣金是否走纯费率（False=按最低佣金/地板计）
        'broker_label': str,    # 佣金档标签（如「银河档：万1.853·免5」）
    }
    """
    empty: dict = {
        'commission': 0.0,
        'stamp_tax': 0.0,
        'transfer_fee': 0.0,
        'misc_fee': 0.0,
        'total': 0.0,
        'applied_rules': [],
        'by_rate': False,
        'broker_label': _broker_label(
            resolve_broker_profile_for_account(account_name, broker)
        ),
    }
    if trade_type not in ('buy', 'sell'):
        empty['applied_rules'] = ['分红/红利补税类型不计交易费用']
        return empty
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        empty['applied_rules'] = ['填入成交价和数量后显示预估']
        return empty
    if amount <= 0:
        empty['applied_rules'] = ['填入成交价和数量后显示预估']
        return empty

    prof = resolve_broker_profile_for_account(account_name, broker)
    rate_fee = amount * prof['commission_rate']
    by_rate = rate_fee >= prof['commission_min']
    # 021BV：分项各自取整到分再求和（交割单为分项口径；非分项和会产生 1 分差）
    commission = round(max(rate_fee, prof['commission_min']), 2)
    rules: list[str] = []
    if by_rate:
        rule = f"佣金：按费率万{_fmt_wan(prof['commission_rate'])} 计"
        if prof['commission_min'] == 0:
            rule += '（免5，无最低佣金）'
        rules.append(rule)
    else:
        rules.append(f"佣金：按最低佣金 {prof['commission_min']:g} 元计（金额低、费率不足地板）")
    if str(market) == 'hk_stock':
        stamp_tax = round(amount * TRADE_FEE_HK['stamp_tax'], 2)
        misc_fee = round(amount * TRADE_FEE_HK['misc'], 2)
        transfer_fee = 0.0
        rules.append('印花税：0.1%，买卖双边收取（港股）')
        rules.append('杂费：约 0.01%（交易费/征费/结算费，简化模型）')
        rules.append('港股为简化模型：未含组合费/汇兑等，以交割单为准')
    else:
        stamp_tax = (
            round(amount * TRADE_FEE_A_STOCK['stamp_tax_sell'], 2) if trade_type == 'sell' else 0.0
        )
        misc_fee = 0.0
        transfer_fee = round(amount * TRADE_FEE_A_STOCK['transfer_fee'], 2)
        rules.append('印花税：0.05%，仅卖出收取')
        rules.append('过户费：0.001%，买卖双边收取')
    total = round(commission + stamp_tax + transfer_fee + misc_fee, 2)
    return {
        'commission': commission,
        'stamp_tax': stamp_tax,
        'transfer_fee': transfer_fee,
        'misc_fee': misc_fee,
        'total': total,
        'applied_rules': rules,
        'by_rate': by_rate,
        'broker_label': _broker_label(prof),
    }


def estimate_trade_fee(trade_type, amount, account_name=None, market='a_stock'):
    """估算单笔交易费用（元）。

    trade_type: buy / sell（dividend / dividend_tax 无佣金，返回 0）
    amount:     成交金额（price×quantity 或直填）；<=0 返回 0
    Returns:    float，两位小数
    """
    return estimate_trade_fee_items(trade_type, amount, account_name, market)['total']
