"""
021BK 交易费用估算（交割单隔日才出，录入流水时自动估算、事后可在编辑中改实际值）

费率口径（2026 现行）：
  A股：佣金（按券商账户匹配，最低 5 元）+ 印花税 0.05%（卖出单边）+ 过户费 0.001%（双边）
  港股：佣金（同券商）+ 印花税 0.1%（双边）+ 杂费约 0.01%（简化模型）

原则：纯函数、零网络、离线可测；估算值入库标记 commission_estimated=1，
用户编辑填入实际值后标记清除（021X 口径不变：买入计入成本，卖出扣减已实现盈亏）。
"""

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


def estimate_trade_fee(trade_type, amount, account_name=None, market='a_stock'):
    """估算单笔交易费用（元）。

    trade_type: buy / sell（dividend / dividend_tax 无佣金，返回 0）
    amount:     成交金额（price×quantity 或直填）；<=0 返回 0
    Returns:    float，两位小数
    """
    if trade_type not in ('buy', 'sell'):
        return 0.0
    try:
        amount = float(amount or 0)
    except (TypeError, ValueError):
        return 0.0
    if amount <= 0:
        return 0.0

    prof = resolve_broker_profile(account_name)
    commission = max(amount * prof['commission_rate'], prof['commission_min'])
    if str(market) == 'hk_stock':
        fee = commission + amount * TRADE_FEE_HK['stamp_tax'] + amount * TRADE_FEE_HK['misc']
    else:
        fee = commission + amount * TRADE_FEE_A_STOCK['transfer_fee']
        if trade_type == 'sell':
            fee += amount * TRADE_FEE_A_STOCK['stamp_tax_sell']
    return round(fee, 2)
