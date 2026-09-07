"""资金面·腾讯 westock 主源（CLI 查询 + 冷却）。

OPT-3（2026-09-07）自 modules/data_collector.py 纯搬移；语义锚点以函数 docstring 为准。
"""
import time

from modules.collector._env import logger
from modules.collector.capital_em import _safe_float_wan
from modules.collector.symbols_status import _get_tencent_prefix, _num_float

# ============================================================
# 020A：腾讯自选股（westock）资金面层；021L 起由备用层提为【主源】
# 数据源为腾讯自选股（社区实测不封 IP），主力净流入口径 = 超大单+大单（与东财同概念，
# 探针实测 600276：MainNetFlow == JumboNetFlow + BlockNetFlow，精确相等）。
# 交付方式：npm CLI（westock-data-clawhub@1.0.4 版本锁定）经腾讯共享签名网关；
# 探针审计结论：CLI 仅访问 proxy.finance.qq.com 单域名，无其他网络行为。
# 位置（021L 起）：腾讯 westock（主源）→ 东财三层（兜底）→ 新浪主力口径 → 估算兜底。
# 提主源依据（021L 实证）：EM 直接成功率仅约 1/3、7 月失败率 66%，westock 同口径
# 顶替成功率最高且稳定；东财高频路径撤下后仅低频唯一点（新闻/预告）继续使用东财。
# 共享通道存在失效可能 → 连续失败进入冷却 + 失败自动降级，不阻塞主链路。
# ============================================================
_WESTOCK_PACKAGE = 'westock-data-clawhub@1.0.4'
_WESTOCK_TIMEOUT_SECONDS = 45    # npx 冷启动较慢，超时放宽
_WESTOCK_COOLDOWN_SECONDS = 1800  # 连续失败后的冷却时长（30 分钟）
_WESTOCK_COOLDOWN_FAIL_N = 3     # 连续失败 N 次进入冷却
_WESTOCK_COOLDOWN_UNTIL = 0.0    # 冷却截止时间戳
_WESTOCK_CONSECUTIVE_FAIL = 0    # 连续失败计数


def _westock_cooldown_active():
    """westock 层是否处于冷却期。"""
    return time.time() < _WESTOCK_COOLDOWN_UNTIL


def _westock_record_failure():
    """记录 westock 层失败；连续失败达阈值进入冷却。"""
    global _WESTOCK_CONSECUTIVE_FAIL, _WESTOCK_COOLDOWN_UNTIL
    _WESTOCK_CONSECUTIVE_FAIL += 1
    if _WESTOCK_CONSECUTIVE_FAIL >= _WESTOCK_COOLDOWN_FAIL_N:
        _WESTOCK_COOLDOWN_UNTIL = time.time() + _WESTOCK_COOLDOWN_SECONDS
        logger.warning(
            f'[westock] 连续失败 {_WESTOCK_CONSECUTIVE_FAIL} 次，'
            f'进入冷却 {_WESTOCK_COOLDOWN_SECONDS // 60} 分钟（期间跳过该层）'
        )
        _WESTOCK_CONSECUTIVE_FAIL = 0


def _westock_reset():
    """westock 层成功后重置连续失败计数。"""
    global _WESTOCK_CONSECUTIVE_FAIL
    _WESTOCK_CONSECUTIVE_FAIL = 0


def _westock_cli_query(command, codes, date_str=''):
    """调用 westock CLI（npx 子进程），返回 Markdown 输出文本；失败返回 None。"""
    import shutil
    import subprocess as _sp

    if not shutil.which('npx') and not shutil.which('npm'):
        logger.warning('[westock] 本机未安装 npx/node，跳过腾讯自选股资金面层')
        return None
    # npx 在 Windows 上是 npx.cmd 批处理，CreateProcess 不能直接执行，须经 cmd /c 包装
    cmd = ['cmd', '/c', 'npx', '-y', _WESTOCK_PACKAGE, command, codes]
    if date_str:
        cmd += ['--date', date_str]
    try:
        proc = _sp.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=_WESTOCK_TIMEOUT_SECONDS,
            creationflags=_sp.CREATE_NO_WINDOW,
        )
    except (_sp.TimeoutExpired, OSError) as e:
        logger.warning(f'[westock] CLI 调用失败: {e}')
        return None
    out = (proc.stdout or '').strip()
    if proc.returncode != 0 or not out:
        logger.warning(
            f'[westock] CLI 返回码={proc.returncode}，无有效输出'
            f'（stderr 摘要: {(proc.stderr or "")[:120]}）'
        )
        return None
    return out


def _parse_westock_markdown(text):
    """解析 westock CLI 的 Markdown 表格输出，返回第一张表第一行数据 {列名: 值}。"""
    lines = [ln.strip() for ln in text.splitlines() if ln.strip().startswith('|')]
    if len(lines) < 2:
        return None
    headers = [h.strip() for h in lines[0].strip('|').split('|')]
    # 过滤分隔行（|---|:--:|---|）
    data_lines = [ln for ln in lines[1:] if not set(ln) <= set('|-: ')]
    if not data_lines:
        return None
    cells = [c.strip() for c in data_lines[0].strip('|').split('|')]
    if len(cells) != len(headers):
        return None
    return dict(zip(headers, cells))


def _fetch_capital_flow_westock(symbol, market, date_str=''):
    """
    020A：腾讯自选股资金面备用层（A股 asfund / 港股 hkfund）。
    020I：date_str 非空时追加 --date 参数逐日查询历史（补采调度器回填用），
    并严格校验返回 EndDate == date_str（与新浪 M-2 同一红线：严禁取错日）；
    不匹配只记录日志、不计入 westock 连续失败（避免回填拖垮实时链路）。
    返回单日 dict {trade_date, main_net_inflow, super_large_net, large_net,
    medium_net, small_net} 或 None。
    - A股返回四档分解（主力=超大+大，与东财同口径）；港股仅主力净额+总额。
    - 金额元→万元（与 raw_capital_flow 全库口径一致）；港股为万港元。
    """
    if _westock_cooldown_active():
        logger.info(f'[{symbol}] westock 冷却期，跳过腾讯自选股资金面层')
        return None
    prefix, code = _get_tencent_prefix(symbol, market)
    command = 'hkfund' if market == 'hk_stock' else 'asfund'
    wcode = f'{prefix}{code}'
    try:
        text = _westock_cli_query(command, wcode, date_str=date_str)
        if not text:
            _westock_record_failure()
            return None
        row = _parse_westock_markdown(text)
        if not row:
            logger.warning(f'[{symbol}] westock 输出无法解析: {text[:150]}')
            _westock_record_failure()
            return None
        if date_str and (row.get('EndDate') or '').strip() != date_str:
            logger.warning(
                f'[{symbol}] westock --date {date_str} 返回 EndDate={row.get("EndDate")} 不匹配，放弃'
            )
            return None
        main_net = _safe_float_wan(row.get('MainNetFlow'))
        jumbo = _safe_float_wan(row.get('JumboNetFlow'))
        block = _safe_float_wan(row.get('BlockNetFlow'))
        mid = _safe_float_wan(row.get('MidNetFlow'))
        small = _safe_float_wan(row.get('SmallNetFlow'))
        if all(v is None for v in (main_net, jumbo, block, mid, small)):
            logger.warning(f'[{symbol}] westock 返回字段全空，不采用')
            _westock_record_failure()
            return None
        # 020O：主力净流入占比 = 主力净额 ÷ 成交额；
        # 成交额 = 主力买入+主力卖出+散户买入+散户卖出（A股 MainInFlow 系 / 港股 MainIn 系）。
        _main_in = _safe_float_wan(
            row.get('MainInFlow') if row.get('MainInFlow') is not None else row.get('MainIn')
        )
        _main_out = _safe_float_wan(
            row.get('MainOutFlow') if row.get('MainOutFlow') is not None else row.get('MainOut')
        )
        _retail_in = _safe_float_wan(
            row.get('RetailInFlow') if row.get('RetailInFlow') is not None else row.get('RetailIn')
        )
        _retail_out = _safe_float_wan(
            row.get('RetailOutFlow') if row.get('RetailOutFlow') is not None else row.get('RetailOut')
        )
        _main_pct = None
        if all(v is not None for v in (_main_in, _main_out, _retail_in, _retail_out)):
            _turnover_wan = _main_in + _main_out + _retail_in + _retail_out
            if _turnover_wan and main_net is not None:
                _main_pct = round(main_net / _turnover_wan * 100, 2)
        # 020O：全资金净流入——仅港股 hkfund 提供（TotalNetFlow=主力+散户主动净额，
        # 有实际意义）；A股 asfund 散户为被动镜像、全口径恒等0，返回 None 不写入。
        _total_net = _safe_float_wan(row.get('TotalNetFlow'))
        # 021Q：港股通(南下)持仓——hkfund 独有的 _lgtHoldInfo 块（仅港股通标的有值）。
        # LgtCapChgDaily=当日持股市值变化(港元)→万港元；LgtHoldRatio=持股占比(%)。
        # 语义：港股无两融汇总，南下增减持是港股最重要的杠杆/聪明钱资金指标。
        _south_net = _safe_float_wan(row.get('_lgtHoldInfo.LgtCapChgDaily'))
        _south_ratio = _num_float(row.get('_lgtHoldInfo.LgtHoldRatio'))
        _westock_reset()
        return {
            'trade_date': row.get('EndDate') or '',
            'main_net_inflow': main_net,
            'main_net_inflow_pct': _main_pct,
            'super_large_net': jumbo,
            'large_net': block,
            'medium_net': mid,
            'small_net': small,
            'total_net_inflow': _total_net,
            'south_net_buy': _south_net,
            'south_hold_ratio': _south_ratio,
        }
    except Exception as e:
        logger.warning(f'[{symbol}] westock 资金面层失败: {e}')
        _westock_record_failure()
        return None
