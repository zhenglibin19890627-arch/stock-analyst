"""
引擎灰度切换控制器 (Engine Switcher)

监理前置要求：
- 灰度开关需支持按股票代码白名单控制，不得全量一次性切换
- v5引擎异常时自动降级到旧引擎

三种模式：
  mode=all_legacy   — 全部使用旧引擎（默认安全模式）
  mode=whitelist    — 仅白名单中的股票使用v5引擎，其余使用旧引擎
  mode=all_v5       — 全部使用v5引擎（灰度完成后最终切换）

配置文件: config_engine_switch.json
  {
    "mode": "whitelist",
    "whitelist": ["600276", "300146", "000333", "002352", "300750"]
  }

使用方式:
  from modules.engine_switcher import should_use_v5, get_engine_mode
  if should_use_v5(stock_id):
      result = scoring_engine.analyze_from_db(stock_id)
  else:
      result = analysis_engine.analyze_stock(stock_id)
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone

logger = logging.getLogger(__name__)

_CONFIG_FILE = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'config_engine_switch.json'
)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

# 默认配置（安全模式：全部使用旧引擎）
_DEFAULT_CONFIG = {
    'mode': 'whitelist',
    'whitelist': [
        '600276',
        '300146',
        '000333',
        '002352',
        '300750',
        'HK3690',
        '600519',
        '601888',
        '300124',
        '688017',
        '688981',
        '002458',
    ],
    'blacklist': [],
    'circuit_breaker': {
        'max_consecutive_failures': 2,
        'cooldown_hours': 24,
    },
}

# 配置热加载缓存
_config_cache = None
_config_mtime = 0

# P3-A 熔断状态（进程级内存缓存，以 config 为唯一权威源）
# {stock_id: {"failures": int, "tripped": bool, "tripped_at": iso_string}}
_circuit_breaker_cache = {}


def _load_config() -> dict:
    """加载灰度配置，支持热加载（检测文件修改时间）"""
    global _config_cache, _config_mtime, _circuit_breaker_cache

    try:
        mtime = os.path.getmtime(_CONFIG_FILE)
    except (FileNotFoundError, OSError):
        # 配置文件不存在，使用默认配置
        if _config_cache is None:
            _config_cache = _DEFAULT_CONFIG.copy()
            logger.info(f'引擎切换配置文件不存在，使用默认配置: mode={_config_cache["mode"]}')
        return _config_cache

    # 文件未修改，返回缓存
    if _config_cache is not None and mtime == _config_mtime:
        return _config_cache

    # 重新加载
    try:
        with open(_CONFIG_FILE, encoding='utf-8') as f:
            _config_cache = json.load(f)
        _config_mtime = mtime
        logger.info(
            f'引擎切换配置已加载: mode={_config_cache.get("mode")}, '
            f'whitelist_count={len(_config_cache.get("whitelist", []))}, '
            f'blacklist_count={len(_config_cache.get("blacklist", []))}'
        )

        # P3-A 修正项1：从 config 的 blacklist 重建内存熔断状态
        _rebuild_circuit_breaker_from_config()
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f'引擎切换配置解析失败({e})，使用默认配置')
        _config_cache = _DEFAULT_CONFIG.copy()

    return _config_cache


def _get_stock_symbol(stock_id: int) -> str:
    """根据 stock_id 获取股票代码"""
    try:
        from database.db_manager import get_connection

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT symbol FROM stocks WHERE id = ?', (stock_id,))
        row = cursor.fetchone()
        conn.close()
        return row['symbol'] if row else ''
    except Exception:
        return ''


def should_use_v5(stock_id: int) -> bool:
    """判断指定股票是否应使用 v5 引擎

    P3-A 扩展：叠加熔断黑名单检查（优先级高于 whitelist）

    Args:
        stock_id: 数据库 stocks.id
    Returns:
        True=使用v5引擎, False=使用旧引擎
    """
    config = _load_config()
    mode = config.get('mode', 'all_legacy')

    if mode == 'all_legacy':
        return False

    # P3-A：检查熔断黑名单（优先级最高）
    if _is_circuit_tripped(stock_id):
        return False

    if mode == 'all_v5':
        return True
    elif mode == 'whitelist':
        symbol = _get_stock_symbol(stock_id)
        whitelist = config.get('whitelist', [])
        return symbol in whitelist
    else:
        logger.warning(f'未知引擎切换模式: {mode}，默认使用旧引擎')
        return False


def get_engine_mode() -> str:
    """获取当前引擎切换模式"""
    return _load_config().get('mode', 'all_legacy')


def get_whitelist() -> list:
    """获取当前白名单"""
    return _load_config().get('whitelist', [])


def get_engine_info(stock_id: int) -> dict:
    """获取指定股票的引擎选择信息（用于日志和调试）"""
    config = _load_config()
    mode = config.get('mode', 'all_legacy')
    symbol = _get_stock_symbol(stock_id)
    use_v5 = should_use_v5(stock_id)

    # P3-A：附加熔断信息
    cb_info = _circuit_breaker_cache.get(stock_id, {})

    return {
        'stock_id': stock_id,
        'symbol': symbol,
        'mode': mode,
        'whitelist': config.get('whitelist', []),
        'use_v5': use_v5,
        'engine': 'v5' if use_v5 else 'legacy',
        'circuit_tripped': cb_info.get('tripped', False),
        'circuit_failures': cb_info.get('failures', 0),
        'circuit_tripped_at': cb_info.get('tripped_at'),
    }


# ================================================================
# P3-A：熔断机制（强制修正项1：状态持久化到 config）
# ================================================================


def _rebuild_circuit_breaker_from_config():
    """进程启动/配置热加载时，从 config 的 blacklist 重建内存熔断状态。

    config 的 blacklist 结构：
    [{"symbol": "600519", "tripped_at": "2026-07-18T23:00:00+08:00"}, ...]

    内存缓存结构：
    {stock_id: {"failures": 0, "tripped": True, "tripped_at": iso_string}}
    """
    global _circuit_breaker_cache
    _circuit_breaker_cache = {}

    blacklist = _config_cache.get('blacklist', [])
    if not blacklist:
        return

    for entry in blacklist:
        if isinstance(entry, dict):
            symbol = entry.get('symbol', '')
            tripped_at = entry.get('tripped_at', '')
        else:
            symbol = entry
            tripped_at = ''

        stock_id = _get_stock_id_by_symbol(symbol)
        if stock_id:
            _circuit_breaker_cache[stock_id] = {
                'failures': 0,
                'tripped': True,
                'tripped_at': tripped_at,
            }
            logger.info(
                f'熔断状态恢复: stock_id={stock_id} symbol={symbol} tripped_at={tripped_at}'
            )


def _get_stock_id_by_symbol(symbol: str) -> int:
    """根据股票代码反查 stock_id"""
    try:
        from database.db_manager import get_connection

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT id FROM stocks WHERE symbol = ?', (symbol,))
        row = cursor.fetchone()
        conn.close()
        return row['id'] if row else 0
    except Exception:
        return 0


def _is_circuit_tripped(stock_id: int) -> bool:
    """检查股票是否被熔断（在黑名单中且冷却期未过）"""
    cb_info = _circuit_breaker_cache.get(stock_id)
    if not cb_info or not cb_info.get('tripped'):
        return False

    # 检查冷却期
    tripped_at = cb_info.get('tripped_at')
    if tripped_at:
        try:
            tripped_time = datetime.fromisoformat(tripped_at)
            cooldown_hours = _config_cache.get('circuit_breaker', {}).get('cooldown_hours', 24)
            elapsed = (datetime.now(_CN_TZ) - tripped_time).total_seconds() / 3600
            if elapsed >= cooldown_hours:
                # 冷却期已过，自动解除
                _clear_circuit_breaker(stock_id)
                logger.info(f'熔断冷却期已过，自动解除: stock_id={stock_id}')
                return False
        except (ValueError, TypeError):
            pass

    return True


def record_v5_failure(stock_id: int):
    """advisor 在 v5 生成失败/fallback 时调用。

    递增失败计数，达到阈值时触发熔断并持久化到 config。
    """
    config = _load_config()
    cb_config = config.get('circuit_breaker', {})
    max_failures = cb_config.get('max_consecutive_failures', 2)

    cb_info = _circuit_breaker_cache.get(stock_id, {'failures': 0, 'tripped': False})
    cb_info['failures'] += 1
    _circuit_breaker_cache[stock_id] = cb_info

    if cb_info['failures'] >= max_failures and not cb_info.get('tripped'):
        # 触发熔断
        tripped_at = datetime.now(_CN_TZ).isoformat()
        cb_info['tripped'] = True
        cb_info['tripped_at'] = tripped_at

        # 持久化到 config blacklist
        symbol = _get_stock_symbol(stock_id)
        _add_to_blacklist(symbol, tripped_at)

        logger.warning(
            f'🔴 熔断触发! stock_id={stock_id} symbol={symbol} '
            f'failures={cb_info["failures"]} 已自动切回 legacy 引擎'
        )
    else:
        logger.info(
            f'v5失败记录: stock_id={stock_id} failures={cb_info["failures"]}/{max_failures}'
        )


def record_v5_success(stock_id: int):
    """advisor 在 v5 成功时调用，重置失败计数。"""
    if stock_id in _circuit_breaker_cache:
        old_failures = _circuit_breaker_cache[stock_id].get('failures', 0)
        if old_failures > 0:
            _circuit_breaker_cache[stock_id]['failures'] = 0
            logger.info(f'v5成功，重置失败计数: stock_id={stock_id}')


def _add_to_blacklist(symbol: str, tripped_at: str):
    """将股票添加到 config 的 blacklist（同步写盘）"""
    config = _load_config()
    blacklist = config.get('blacklist', [])

    # 避免重复
    existing_symbols = []
    for entry in blacklist:
        if isinstance(entry, dict):
            existing_symbols.append(entry.get('symbol', ''))
        else:
            existing_symbols.append(entry)

    if symbol not in existing_symbols:
        blacklist.append({'symbol': symbol, 'tripped_at': tripped_at})
        config['blacklist'] = blacklist
        _save_config(config)


def _clear_circuit_breaker(stock_id: int):
    """解除熔断：清除内存状态 + 从 config blacklist 移除"""
    symbol = _get_stock_symbol(stock_id)

    if stock_id in _circuit_breaker_cache:
        del _circuit_breaker_cache[stock_id]

    # 从 config blacklist 移除
    config = _load_config()
    blacklist = config.get('blacklist', [])
    new_blacklist = []
    for entry in blacklist:
        entry_symbol = entry.get('symbol', '') if isinstance(entry, dict) else entry
        if entry_symbol != symbol:
            new_blacklist.append(entry)

    if len(new_blacklist) != len(blacklist):
        config['blacklist'] = new_blacklist
        _save_config(config)


def _save_config(config: dict):
    """保存配置到文件（同步写盘）"""
    global _config_cache, _config_mtime
    try:
        with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
        _config_cache = config
        _config_mtime = os.path.getmtime(_CONFIG_FILE)
    except OSError as e:
        logger.error(f'保存引擎切换配置失败: {e}')


def rollback_all_to_legacy() -> dict:
    """一键全量回退：将 mode 改为 all_legacy"""
    config = _load_config()
    previous_mode = config.get('mode', 'unknown')

    if previous_mode == 'all_legacy':
        return {'success': True, 'message': '已在 all_legacy 模式', 'previous_mode': previous_mode}

    config['mode'] = 'all_legacy'
    _save_config(config)

    logger.warning(f'🔴 一键全量回退触发: {previous_mode} → all_legacy')
    return {'success': True, 'previous_mode': previous_mode, 'new_mode': 'all_legacy'}


def get_grayscale_status() -> dict:
    """获取当前灰度状态概要（供 /api/engine/status 接口使用）"""
    config = _load_config()

    # 收集各股票引擎分配
    from database.db_manager import get_connection

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT id, symbol, name FROM stocks WHERE status != ? ORDER BY id', ('delisted',)
    )
    stocks = [dict(r) for r in cursor.fetchall()]
    conn.close()

    stock_engines = []
    v5_count = 0
    legacy_count = 0
    for s in stocks:
        use_v5 = should_use_v5(s['id'])
        cb_info = _circuit_breaker_cache.get(s['id'], {})
        engine = 'v5' if use_v5 else 'legacy'
        if use_v5:
            v5_count += 1
        else:
            legacy_count += 1

        stock_engines.append(
            {
                'stock_id': s['id'],
                'symbol': s['symbol'],
                'name': s['name'],
                'engine': engine,
                'circuit_tripped': cb_info.get('tripped', False),
                'circuit_failures': cb_info.get('failures', 0),
            }
        )

    return {
        'mode': config.get('mode', 'all_legacy'),
        'whitelist_count': len(config.get('whitelist', [])),
        'blacklist': config.get('blacklist', []),
        'circuit_breaker_config': config.get('circuit_breaker', {}),
        'v5_count': v5_count,
        'legacy_count': legacy_count,
        'total_stocks': len(stocks),
        'stocks': stock_engines,
    }


# ================================================================
# 命令行测试入口
# ================================================================

if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

    print('\n引擎灰度切换控制器测试')
    print('=' * 60)

    # 创建默认配置文件（如果不存在）
    if not os.path.exists(_CONFIG_FILE):
        with open(_CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(_DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        print(f'已创建默认配置文件: {_CONFIG_FILE}')
        print(json.dumps(_DEFAULT_CONFIG, ensure_ascii=False, indent=2))
        print()

    # 测试各股票
    from database.db_manager import get_connection

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, symbol, name FROM stocks ORDER BY id')
    stocks = [dict(r) for r in cursor.fetchall()]
    conn.close()

    print(f'\n{"stock_id":<10} {"symbol":<12} {"name":<12} {"engine":<10}')
    print('-' * 48)
    for s in stocks:
        info = get_engine_info(s['id'])
        print(f'{s["id"]:<10} {s["symbol"]:<12} {s["name"]:<12} {info["engine"]:<10}')

    print(f'\n当前模式: {get_engine_mode()}')
    print(f'白名单: {get_whitelist()}')
    print('=' * 60)
