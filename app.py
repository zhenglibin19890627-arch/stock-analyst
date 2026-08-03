"""
Stock Analyst 主程序
启动方法：python app.py
然后在浏览器打开 http://127.0.0.1:5000
"""

import json
import os
import sys

# 绕过系统代理（避免 Clash/V2Ray 未运行时网络请求失败）
os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'

# 确保能找到项目内的模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from config import (
    COST_ADJUSTMENT_COOLDOWN_HOURS,
    COST_ADJUSTMENT_DEVIATION_THRESHOLD,
    FLASK_DEBUG,
    FLASK_HOST,
    FLASK_PORT,
    MAX_WATCHLIST_SIZE,
    PRICE_CACHE_TTL_HOURS,
    TRADE_T1_LOCK_ENABLED,
)
from database.db_manager import get_connection, init_database
from flask import Flask, jsonify, render_template, request

app = Flask(__name__)


# ============================================================
# 页面路由
# ============================================================


@app.route('/')
def index():
    """首页 —— 数据采集测试页面"""
    return render_template('index.html')


# ============================================================
# API 接口
# ============================================================

# ---- 展示层数据格式化工具函数 ----
# 数据库内部以原始单位存储（元、浮点数），API 返回时统一格式化为人类可读格式。
# 四维分析引擎等内部计算模块直接读取数据库原始值，不受此处格式化影响。


def _fmt_pct(val, decimals=2):
    """格式化百分比值：4.888726 -> '+4.89%'，负数自动加负号"""
    if val is None:
        return None
    try:
        v = float(val)
        sign = '+' if v >= 0 else ''
        return '{}{:.{}f}%'.format(sign, v, decimals)
    except (ValueError, TypeError):
        return None


def _fmt_num(val, decimals=2):
    """格式化数值：47.0 -> '47.00'"""
    if val is None:
        return None
    try:
        return '{:.{}f}'.format(float(val), decimals)
    except (ValueError, TypeError):
        return None


def _fmt_wan(val):
    """元 -> 万元：2065000000.0 -> 206500.00"""
    if val is None:
        return None
    try:
        return round(float(val) / 1e4, 2)
    except (ValueError, TypeError):
        return None


def _get_market_by_stock_id(cursor, stock_id):
    """根据 stock_id 查询市场类型（a_stock / hk_stock）"""
    cursor.execute('SELECT market FROM stocks WHERE id = ?', (stock_id,))
    row = cursor.fetchone()
    return row['market'] if row else None


def _currency_label(market):
    """返回货币标签"""
    return 'HKD' if market == 'hk_stock' else 'CNY'


def _currency_unit(market):
    """返回货币单位名称"""
    return '万港元' if market == 'hk_stock' else '万元'


def _derive_obos_signal(key_factors_raw):
    """从 daily_reports.key_factors 解析超买超卖信号（DEV-TASKS-20260727-003）。

    数据来源：advisor._build_kline_factors 已计算 rsi_status / boll_position 因子，
    存储于 key_factors.kline.top_factors 中。本函数仅做展示层派生，不改任何引擎逻辑。

    规则（与任务书一致）：
      - RSI>70（rsi_status 含'超买'）或 布林带触及上轨（boll_position 含'上轨'）→ 'overbought'
      - RSI<30（rsi_status 含'超卖'）或 布林带触及下轨（boll_position 含'下轨'）→ 'oversold'
      - 其他 → None（不显示徽标）

    Args:
        key_factors_raw: daily_reports.key_factors 字段值（JSON 字符串 / dict / None）

    Returns:
        'overbought' | 'oversold' | None
    """
    if not key_factors_raw:
        return None
    try:
        kf = json.loads(key_factors_raw) if isinstance(key_factors_raw, str) else key_factors_raw
    except (ValueError, TypeError):
        return None
    if not isinstance(kf, dict):
        return None
    kline = kf.get('kline') or {}
    top_factors = kline.get('top_factors') or {}
    rsi_status = str(top_factors.get('rsi_status', ''))
    boll_position = str(top_factors.get('boll_position', ''))
    # 兼容新旧两套因子文案（advisor/analysis_engine 均输出'超买'/'超卖'子串）
    is_overbought = ('超买' in rsi_status) or ('上轨' in boll_position)
    is_oversold = ('超卖' in rsi_status) or ('下轨' in boll_position)
    if is_overbought:
        return 'overbought'
    if is_oversold:
        return 'oversold'
    return None


@app.route('/api/init-db', methods=['POST'])
def api_init_db():
    """初始化数据库（如果还没初始化的话）"""
    try:
        init_database()
        return jsonify({'success': True, 'message': '数据库初始化成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ============================================================
# 统一分组 CRUD（/api/groups）
# 支持 type=watchlist|portfolio 过滤、sync_to_other_type 同步、has_counterpart
# ============================================================


@app.route('/api/groups', methods=['GET'])
def api_get_groups():
    """获取分组列表（统一表）。
    Query params:
      - type: watchlist | portfolio | global（不传则返回全部）
    返回每组附带 count 字段和 has_counterpart 标识。
    """
    group_type = request.args.get('type', '')
    conn = get_connection()
    cursor = conn.cursor()

    if group_type in ('watchlist', 'portfolio', 'global'):
        cursor.execute(
            'SELECT * FROM groups WHERE type=? ORDER BY display_order, id', (group_type,)
        )
    else:
        cursor.execute('SELECT * FROM groups ORDER BY display_order, id')
    groups = [dict(row) for row in cursor.fetchall()]

    # 为每组补充 count 和 has_counterpart
    for g in groups:
        if g['type'] == 'watchlist':
            cursor.execute(
                'SELECT COUNT(*) as cnt FROM stocks WHERE group_id=? AND status!="delisted"',
                (g['id'],),
            )
            g['stock_count'] = cursor.fetchone()['cnt']
            # 检查是否有同名 portfolio 分组
            cursor.execute(
                'SELECT 1 FROM groups WHERE name=? AND type="portfolio" LIMIT 1', (g['name'],)
            )
            g['has_counterpart'] = cursor.fetchone() is not None
        elif g['type'] == 'portfolio':
            cursor.execute('SELECT COUNT(*) as cnt FROM holdings WHERE group_id=?', (g['id'],))
            g['holding_count'] = cursor.fetchone()['cnt']
            # 检查是否有同名 watchlist 分组
            cursor.execute(
                'SELECT 1 FROM groups WHERE name=? AND type="watchlist" LIMIT 1', (g['name'],)
            )
            g['has_counterpart'] = cursor.fetchone() is not None
        else:
            g['has_counterpart'] = False
            g['stock_count'] = 0
            g['holding_count'] = 0

    conn.close()
    return jsonify({'success': True, 'groups': groups})


@app.route('/api/groups', methods=['POST'])
def api_create_group():
    """创建分组（支持自动同步到另一类型）。
    Body:
      - name: 分组名称
      - type: watchlist | portfolio（默认 watchlist）
      - sync_to_other_type: bool（默认 True，自动在另一类型下创建同名分组）
    """
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    group_type = data.get('type', 'watchlist')
    sync_to_other = data.get('sync_to_other_type', True)

    if not name:
        return jsonify({'success': False, 'message': '分组名称不能为空'}), 400
    if group_type not in ('watchlist', 'portfolio'):
        return jsonify({'success': False, 'message': 'type 必须为 watchlist 或 portfolio'}), 400

    other_type = 'portfolio' if group_type == 'watchlist' else 'watchlist'
    conn = get_connection()
    cursor = conn.cursor()

    counterpart_created = False
    try:
        # 1. 创建当前类型的分组
        cursor.execute('INSERT INTO groups (name, type) VALUES (?, ?)', (name, group_type))
        group_id = cursor.lastrowid

        # 2. 如果启用同步，检查另一类型是否已有同名分组
        if sync_to_other:
            cursor.execute('SELECT id FROM groups WHERE name=? AND type=?', (name, other_type))
            if not cursor.fetchone():
                cursor.execute('INSERT INTO groups (name, type) VALUES (?, ?)', (name, other_type))
                counterpart_created = True

        conn.commit()
        conn.close()
        result = {
            'success': True,
            'group_id': group_id,
            'counterpart_created': counterpart_created if sync_to_other else False,
            'counterpart_type': other_type,
        }
        return jsonify(result)
    except Exception as e:
        conn.close()
        if 'UNIQUE' in str(e):
            return jsonify({'success': False, 'message': '该类型下分组名称已存在'}), 400
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/groups/<int:group_id>', methods=['PUT'])
def api_update_group(group_id):
    """修改分组名称（支持同步修改另一类型下的同名分组）。
    Body:
      - name: 新名称
      - sync_to_other_type: bool（默认 True）
    """
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    sync_to_other = data.get('sync_to_other_type', True)

    if not name:
        return jsonify({'success': False, 'message': '分组名称不能为空'}), 400

    conn = get_connection()
    cursor = conn.cursor()

    # 获取当前分组信息
    cursor.execute('SELECT name, type FROM groups WHERE id=?', (group_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'message': '分组不存在'}), 404

    old_name = row['name']
    group_type = row['type']
    other_type = 'portfolio' if group_type == 'watchlist' else 'watchlist'
    counterpart_updated = False

    try:
        # 1. 更新当前分组名称
        cursor.execute('UPDATE groups SET name=? WHERE id=?', (name, group_id))

        # 2. 如果启用同步，查找另一类型下的同名旧分组并更新
        if sync_to_other:
            cursor.execute('SELECT id FROM groups WHERE name=? AND type=?', (old_name, other_type))
            cp_row = cursor.fetchone()
            if cp_row:
                # 检查新名称是否在另一类型下已存在（排除自己）
                cursor.execute(
                    'SELECT id FROM groups WHERE name=? AND type=? AND id!=?',
                    (name, other_type, cp_row['id']),
                )
                if not cursor.fetchone():
                    cursor.execute('UPDATE groups SET name=? WHERE id=?', (name, cp_row['id']))
                    counterpart_updated = True

        conn.commit()
        conn.close()
        result = {'success': True}
        if sync_to_other:
            result['counterpart_updated'] = counterpart_updated
            result['counterpart_type'] = other_type
        return jsonify(result)
    except Exception as e:
        conn.close()
        if 'UNIQUE' in str(e):
            return jsonify({'success': False, 'message': '该类型下分组名称已存在'}), 400
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/groups/<int:group_id>', methods=['DELETE'])
def api_delete_group(group_id):
    """删除分组（仅影响当前类型，不级联删除另一类型；组内记录迁移到 NULL）。
    Query params:
      - type: 可选，指定类型（用于兼容旧接口路径）
    """
    conn = get_connection()
    cursor = conn.cursor()

    # 获取分组信息
    cursor.execute('SELECT name, type FROM groups WHERE id=?', (group_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'message': '分组不存在'}), 404

    group_type = row['type']
    migrated_count = 0

    if group_type == 'watchlist':
        cursor.execute(
            'SELECT COUNT(*) as cnt FROM stocks WHERE group_id=? AND status!="delisted"',
            (group_id,),
        )
        migrated_count = cursor.fetchone()['cnt']
        cursor.execute('UPDATE stocks SET group_id=NULL WHERE group_id=?', (group_id,))
    elif group_type == 'portfolio':
        cursor.execute('SELECT COUNT(*) as cnt FROM holdings WHERE group_id=?', (group_id,))
        migrated_count = cursor.fetchone()['cnt']
        cursor.execute('UPDATE holdings SET group_id=NULL WHERE group_id=?', (group_id,))

    cursor.execute('DELETE FROM groups WHERE id=?', (group_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'migrated_count': migrated_count, 'type': group_type})


# ============================================================
# 兼容别名：旧路径 /api/watchlist/groups 和 /api/portfolio/groups
# 内部转发到统一接口
# ============================================================


@app.route('/api/watchlist/groups', methods=['POST'])
def api_create_watchlist_group_compat():
    """兼容旧路径：创建 watchlist 分组（默认同步创建 portfolio 同名分组）"""
    data = request.get_json(silent=True) or {}
    data['type'] = 'watchlist'
    data.setdefault('sync_to_other_type', True)
    # 直接调用统一逻辑
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'message': '分组名称不能为空'}), 400
    conn = get_connection()
    cursor = conn.cursor()
    counterpart_created = False
    try:
        cursor.execute('INSERT INTO groups (name, type) VALUES (?, "watchlist")', (name,))
        group_id = cursor.lastrowid
        cursor.execute('SELECT id FROM groups WHERE name=? AND type="portfolio"', (name,))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO groups (name, type) VALUES (?, "portfolio")', (name,))
            counterpart_created = True
        conn.commit()
        conn.close()
        return jsonify(
            {
                'success': True,
                'group_id': group_id,
                'counterpart_created': counterpart_created,
                'counterpart_type': 'portfolio',
            }
        )
    except Exception as e:
        conn.close()
        if 'UNIQUE' in str(e):
            return jsonify({'success': False, 'message': '分组名称已存在'}), 400
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/watchlist/groups/<int:group_id>', methods=['PUT'])
def api_update_watchlist_group_compat(group_id):
    """兼容旧路径：更新 watchlist 分组名（同步更新 portfolio 同名分组）"""
    data = request.get_json(silent=True) or {}
    data.setdefault('sync_to_other_type', True)
    # 转发到统一接口逻辑
    request._cached_json = (data, True)
    return api_update_group(group_id)


@app.route('/api/watchlist/groups/<int:group_id>', methods=['DELETE'])
def api_delete_watchlist_group_compat(group_id):
    """兼容旧路径：删除 watchlist 分组"""
    return api_delete_group(group_id)


@app.route('/api/stocks', methods=['GET'])
def api_get_stocks():
    """获取所有自选股"""
    market = request.args.get('market', '')
    group_id = request.args.get('group_id', '')

    conn = get_connection()
    cursor = conn.cursor()

    query = """
        SELECT s.id, s.symbol, s.market, s.name, s.status, s.is_new_stock,
               s.added_at, s.planned_quantity, s.target_cost,
               g.name as group_name,
               h.cost_price as cost_price, h.quantity as quantity,
               h.realized_pnl, h.status as holding_status,
               pc.latest_price, pc.pct_change as price_pct_change,
               pc.updated_at as price_updated_at,
               (SELECT dr2.key_factors FROM daily_reports dr2
                WHERE dr2.stock_id = s.id AND dr2.status = 'ok'
                ORDER BY dr2.report_date DESC LIMIT 1) as latest_key_factors
        FROM stocks s
        LEFT JOIN groups g ON s.group_id = g.id AND g.type='watchlist'
        LEFT JOIN holdings h ON s.id = h.stock_id
        LEFT JOIN price_cache pc ON s.id = pc.stock_id
        WHERE 1=1
    """
    params = []

    if market:
        query += ' AND s.market = ?'
        params.append(market)
    if group_id:
        query += ' AND s.group_id = ?'
        params.append(group_id)

    query += ' ORDER BY s.added_at DESC'

    cursor.execute(query, params)
    stocks = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # 格式化 latest_price + 计算市值
    import decimal

    for s in stocks:
        if s.get('latest_price') is not None:
            s['latest_price'] = round(s['latest_price'], 2)
        # 精确市值 = quantity × latest_price
        qty = s.get('quantity') or 0
        price = s.get('latest_price')
        cost = s.get('cost_price')
        if price is not None and price > 0 and qty > 0:
            s['market_value'] = float(
                decimal.Decimal(str(qty * price)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )
            # 浮动盈亏 = (latest_price - cost_price) × quantity
            if cost is not None and cost > 0:
                raw_pnl = (price - cost) * qty
                s['unrealized_pnl'] = float(
                    decimal.Decimal(str(raw_pnl)).quantize(
                        decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                    )
                )
            else:
                s['unrealized_pnl'] = None
        else:
            s['market_value'] = None
            s['unrealized_pnl'] = None
        # DEV-TASKS-20260727-003：超买超卖信号（从最新报告因子派生，不暴露原始因子）
        s['obos_signal'] = _derive_obos_signal(s.pop('latest_key_factors', None))

    return jsonify({'success': True, 'stocks': stocks, 'count': len(stocks)})


@app.route('/api/stocks', methods=['POST'])
def api_add_stock():
    """添加自选股"""
    try:
        data = request.json
        symbol = data.get('symbol', '').strip()
        market = data.get('market', '')
        name = data.get('name', '').strip()
        group_id = data.get('group_id', None)

        if not symbol or not market:
            return jsonify({'success': False, 'message': '请填写股票代码和市场'}), 400

        if market not in ('a_stock', 'hk_stock'):
            return jsonify({'success': False, 'message': '市场类型无效'}), 400

        # 检查自选股数量上限
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as count FROM stocks WHERE status != "delisted"')
        count = cursor.fetchone()['count']
        if count >= MAX_WATCHLIST_SIZE:
            conn.close()
            return jsonify(
                {'success': False, 'message': '自选股已达上限' + str(MAX_WATCHLIST_SIZE) + '只'}
            ), 400

        # 检查是否已存在
        cursor.execute('SELECT id FROM stocks WHERE symbol = ? AND market = ?', (symbol, market))
        if cursor.fetchone():
            conn.close()
            return jsonify({'success': False, 'message': '该股票已在自选股列表中'}), 400

        # 插入
        cursor.execute(
            """
            INSERT INTO stocks (symbol, market, name, group_id)
            VALUES (?, ?, ?, ?)
        """,
            (symbol, market, name, group_id),
        )

        stock_id = cursor.lastrowid

        # INDUSTRY-DYNAMIC：添加自选股时自动获取行业分类
        try:
            from modules.data_collector import fetch_stock_industry

            industry = fetch_stock_industry(symbol, market)
            cursor.execute('UPDATE stocks SET industry = ? WHERE id = ?', (industry, stock_id))
        except Exception:
            pass  # 行业获取失败不阻塞添加流程

        conn.commit()
        conn.close()

        return jsonify(
            {
                'success': True,
                'message': '添加成功 (ID: ' + str(stock_id) + ')',
                'stock_id': stock_id,
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'message': '服务器错误：' + str(e)}), 500


@app.route('/api/stocks/<int:stock_id>', methods=['PUT'])
def api_update_stock(stock_id):
    """更新自选股信息（目前支持修改 group_id 和 name）"""
    data = request.get_json(silent=True) or {}
    conn = get_connection()
    cursor = conn.cursor()

    # 检查股票是否存在
    cursor.execute('SELECT id FROM stocks WHERE id = ?', (stock_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'message': '股票不存在'}), 404

    fields = []
    params = []

    if 'group_id' in data:
        gid = data['group_id']
        fields.append('group_id = ?')
        params.append(gid)  # None 表示移出分组

    if 'name' in data:
        fields.append('name = ?')
        params.append(data['name'].strip())

    if not fields:
        conn.close()
        return jsonify({'success': False, 'message': '没有要更新的字段'}), 400

    params.append(stock_id)
    cursor.execute(f'UPDATE stocks SET {", ".join(fields)} WHERE id = ?', params)
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '更新成功'})


@app.route('/api/stocks/<int:stock_id>', methods=['DELETE'])
def api_delete_stock(stock_id):
    """删除自选股：先删除所有关联的子表数据，再删除主记录"""
    try:
        conn = get_connection()
        cursor = conn.cursor()

        # 按依赖顺序删除所有子表数据
        child_tables = [
            'data_status',
            'change_logs',
            'backtest_results',
            'ratings_history',
            'analysis_results',
            'raw_sentiment',
            'raw_capital_flow',
            'raw_fundamental',
            'raw_kline',
            'positions',
        ]
        for table in child_tables:
            cursor.execute('DELETE FROM ' + table + ' WHERE stock_id = ?', (stock_id,))

        # 最后删除主记录
        cursor.execute('DELETE FROM stocks WHERE id = ?', (stock_id,))

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': '删除成功'})
    except Exception as e:
        try:
            conn.close()
        except Exception:
            pass
        return jsonify({'success': False, 'message': '删除失败：' + str(e)}), 500


@app.route('/api/stocks/<int:stock_id>/position', methods=['POST'])
def api_update_position(stock_id):
    """更新持仓信息"""
    try:
        data = request.json
        cost_price = data.get('cost_price', 0)
        quantity = data.get('quantity', 0)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT OR REPLACE INTO positions (stock_id, cost_price, quantity, updated_at)
            VALUES (?, ?, ?, datetime('now', 'localtime'))
        """,
            (stock_id, cost_price, quantity),
        )
        conn.commit()
        conn.close()

        return jsonify({'success': True, 'message': '持仓信息已更新'})
    except Exception as e:
        return jsonify({'success': False, 'message': '保存失败：' + str(e)}), 500


@app.route('/api/collect/<int:stock_id>', methods=['POST'])
def api_collect_data(stock_id):
    """触发单只股票的数据采集"""
    from modules.data_collector import collect_stock_data

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT symbol, market FROM stocks WHERE id = ?', (stock_id,))
    stock = cursor.fetchone()
    conn.close()

    if not stock:
        return jsonify({'success': False, 'message': '股票不存在'}), 404

    symbol = stock['symbol']
    market = stock['market']

    try:
        # 执行数据采集
        results = collect_stock_data(symbol, market)
    except Exception as e:
        return jsonify({'success': False, 'message': '采集异常：' + str(e)}), 500

    # 格式化返回结果
    summary = {}
    for dim, (status, msg) in results.items():
        dim_names = {
            'kline': 'K线(技术面)',
            'fundamental': '基本面',
            'capital': '资金面',
            'sentiment': '消息面',
        }
        summary[dim_names.get(dim, dim)] = {'status': status, 'message': msg}

    return jsonify({'success': True, 'symbol': symbol, 'market': market, 'results': summary})


@app.route('/api/stocks/<int:stock_id>/kline', methods=['GET'])
def api_get_kline(stock_id):
    """查看采集到的K线数据（涨跌幅已格式化为 +4.89% 格式，DB原始值不保留）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT trade_date, open, close, high, low, volume, pct_change
        FROM raw_kline WHERE stock_id = ?
        ORDER BY trade_date DESC LIMIT 20
    """,
        (stock_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # 直接将 pct_change 替换为格式化后的字符串值（2位小数、含正负号、%符号）
    # 不再保留原始浮点值，避免后续模块误读
    for row in rows:
        row['pct_change'] = _fmt_pct(row.pop('pct_change', None))

    return jsonify({'success': True, 'data': rows, 'count': len(rows)})


@app.route('/api/stocks/<int:stock_id>/fundamental', methods=['GET'])
def api_get_fundamental(stock_id):
    """查看采集到的基本面数据（百分比指标已格式化，PE/PB保留2位小数）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM raw_fundamental WHERE stock_id = ?
        ORDER BY report_date DESC LIMIT 5
    """,
        (stock_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # 百分比指标列表
    pct_fields = [
        'roe',
        'roa',
        'gross_margin',
        'net_margin',
        'debt_ratio',
        'revenue_growth',
        'profit_growth',
        'non_recurring_profit_growth',
    ]
    # 数值型指标（保留2位小数，无百分号）
    num_fields = ['pe_ratio', 'pb_ratio', 'ps_ratio', 'peg_ratio', 'current_ratio', 'quick_ratio']

    for row in rows:
        for f in pct_fields:
            key = f + '_fmt'
            row[key] = _fmt_pct(row.get(f))
        for f in num_fields:
            key = f + '_fmt'
            row[key] = _fmt_num(row.get(f))

    return jsonify({'success': True, 'data': rows, 'count': len(rows)})


@app.route('/api/stocks/<int:stock_id>/capital', methods=['GET'])
def api_get_capital(stock_id):
    """查看采集到的资金面数据（金额已转换为万元/万港元，并标注币种）"""
    conn = get_connection()
    cursor = conn.cursor()

    # 查询市场类型以确定币种
    market = _get_market_by_stock_id(cursor, stock_id)
    currency = _currency_label(market)
    unit = _currency_unit(market)

    cursor.execute(
        """
        SELECT * FROM raw_capital_flow WHERE stock_id = ?
        ORDER BY trade_date DESC LIMIT 10
    """,
        (stock_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # DB中金额字段已存储为万元单位，API直接拼接货币单位标签
    # 占比字段（%）不拼接单位
    amount_fields = [
        'main_net_inflow',
        'super_large_net',
        'large_net',
        'medium_net',
        'small_net',
        'north_holding_change',
        'margin_balance',
    ]
    for row in rows:
        for f in amount_fields:
            if row.get(f) is not None:
                row[f + '_display'] = str(row[f]) + ' ' + unit

    return jsonify(
        {'success': True, 'data': rows, 'count': len(rows), 'currency': currency, 'unit': unit}
    )


@app.route('/api/stocks/<int:stock_id>/status', methods=['GET'])
def api_get_status(stock_id):
    """查看数据采集状态"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM data_status WHERE stock_id = ?
        ORDER BY fetched_at DESC LIMIT 10
    """,
        (stock_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': rows})


@app.route('/api/stocks/<int:stock_id>/analyze', methods=['POST'])
def api_analyze_stock(stock_id):
    """执行四维分析引擎评分（统一走 advisor.generate_advice 入口，与每日报告一致）"""
    from modules.advisor import generate_advice

    try:
        result = generate_advice(stock_id)
        # 005: 后处理集成价格建议（不修改 generate_advice）
        if result.get('success'):
            from modules.price_advisor import generate_price_advice

            result['price_advice'] = generate_price_advice(stock_id, result)
            # 009补充：动态操作建议覆盖旧建议，避免矛盾
            if result.get('price_advice', {}).get('action_suggestion'):
                result['position_advice'] = result['price_advice']['action_suggestion']
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'分析失败: {str(e)}'}), 500


@app.route('/api/stocks/<int:stock_id>/refresh-full', methods=['POST'])
def api_refresh_full(stock_id):
    """011：强制全量刷新数据 + 重新分析。
    绕过所有增量缓存，重新采集全部维度数据。
    """
    from modules.advisor import generate_advice
    from modules.data_collector import collect_stock_data

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT symbol, name, market FROM stocks WHERE id = ?', (stock_id,))
    stock = cursor.fetchone()
    conn.close()

    if not stock:
        return jsonify({'success': False, 'message': '股票不存在'}), 404

    symbol = stock['symbol']
    market = stock['market']

    try:
        # 步骤1：强制全量采集
        collect_stock_data(symbol, market, force_full=True)

        # 步骤2：重新分析
        result = generate_advice(stock_id)
        if result.get('success'):
            from modules.price_advisor import generate_price_advice

            result['price_advice'] = generate_price_advice(stock_id, result)
            if result.get('price_advice', {}).get('action_suggestion'):
                result['position_advice'] = result['price_advice']['action_suggestion']

        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'全量刷新失败: {str(e)}'}), 500


@app.route('/api/stocks/<int:stock_id>/analysis', methods=['GET'])
def api_get_analysis(stock_id):
    """查看最近的分析结果"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM analysis_results WHERE stock_id = ?
        ORDER BY analysis_date DESC LIMIT 5
    """,
        (stock_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': rows, 'count': len(rows)})


@app.route('/api/stocks/<int:stock_id>/report-latest', methods=['GET'])
def api_get_report_latest(stock_id):
    """P3-A 附加修复：从 daily_reports 表读取该股票最新报告的评分数据。

    与列表页(watchlist-scores)完全同源，确保 engine_version/total_score/
    rating/generated_at 四元组一致。

    B11-DETAIL-LOAD：如果当日无报告，自动触发分析（对用户透明，无需手动刷新）。

    返回格式兼容前端 renderFullReport 所需的 adviseData 结构。
    """
    from datetime import datetime, timezone
    from datetime import timedelta as _td

    _CN_TZ = timezone(_td(hours=8), name='Asia/Shanghai')
    today = datetime.now(_CN_TZ).strftime('%Y-%m-%d')

    conn = get_connection()
    cursor = conn.cursor()

    # 先查当日是否有该股票的有效报告
    cursor.execute(
        """SELECT dr.*, s.symbol, s.name, s.market
           FROM daily_reports dr
           JOIN stocks s ON dr.stock_id = s.id
           WHERE dr.stock_id = ? AND dr.report_date = ? AND dr.status = 'ok' """,
        (stock_id, today),
    )
    row = cursor.fetchone()

    # B11-DETAIL-LOAD：当日无报告时，自动触发分析（静默）
    if not row:
        conn.close()
        try:
            from modules.advisor import generate_advice

            advice = generate_advice(stock_id)
            if advice.get('success'):
                # 005: 追加 price_advice（与 /advise 端点一致）
                from modules.price_advisor import generate_price_advice as _gpa2

                advice['price_advice'] = _gpa2(stock_id, advice)
                # 009补充：动态操作建议覆盖旧建议，避免矛盾
                if advice.get('price_advice', {}).get('action_suggestion'):
                    advice['position_advice'] = advice['price_advice']['action_suggestion']
                # 分析成功，直接返回引擎结果
                return jsonify(advice)
        except Exception:
            pass

        # 引擎也失败，回退到历史报告
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT MAX(report_date) as latest_date FROM daily_reports')
        date_row = cursor.fetchone()
        latest_date = date_row['latest_date'] if date_row else None

        if not latest_date:
            conn.close()
            return jsonify({'success': False, 'message': '无报告数据'})

        cursor.execute(
            """SELECT dr.*, s.symbol, s.name, s.market
               FROM daily_reports dr
               JOIN stocks s ON dr.stock_id = s.id
               WHERE dr.stock_id = ? AND dr.report_date = ?""",
            (stock_id, latest_date),
        )
        row = cursor.fetchone()
        conn.close()

        if not row:
            return jsonify({'success': False, 'message': f'{latest_date} 无该股票报告'})
    else:
        latest_date = today
        conn.close()

    # 解析 key_factors 重建 dimensions
    import json as _json

    key_factors = {}
    try:
        if row['key_factors']:
            key_factors = _json.loads(row['key_factors'])
    except (ValueError, TypeError):
        pass

    # 从 key_factors 构建 dimensions 结构（兼容 renderFullReport）
    dimensions = {}
    for dim_key, dim_data in key_factors.items():
        dimensions[dim_key] = {
            'status': 'ok',
            'score': dim_data.get('score', 0),
            'weight': dim_data.get('weight', 0),
            'factors': dim_data.get('top_factors', {}),
        }

    # 解析 data_warnings
    data_warnings = []
    try:
        if row['data_warnings']:
            data_warnings = _json.loads(row['data_warnings'])
    except (ValueError, TypeError):
        pass

    # B15-T3: 从 key_factors 推算 data_quality（各维度完整度）
    _dq_map = {
        'kline': 'technical',
        'fundamental': 'fundamental',
        'capital_flow': 'capital',
        'news': 'news',
    }
    data_quality = {}
    for dim_key, dq_name in _dq_map.items():
        dim_info = key_factors.get(dim_key)
        if dim_info:
            # 尝试从 top_factors 中解析 data_completeness 百分比
            completeness_str = dim_info.get('top_factors', {}).get('data_completeness', '')
            if completeness_str and '%' in str(completeness_str):
                try:
                    data_quality[dq_name] = float(str(completeness_str).replace('%', '')) / 100.0
                except (ValueError, TypeError):
                    data_quality[dq_name] = 1.0
            else:
                data_quality[dq_name] = 1.0
        else:
            data_quality[dq_name] = 0.0

    # B15-T3: 从 dimensions 提取最强/最弱维度
    _dim_name_map = {
        'kline': '技术面',
        'fundamental': '基本面',
        'capital_flow': '资金面',
        'news': '消息面',
    }
    strongest_dim = None
    weakest_dim = None
    if dimensions:
        scored_dims = [
            (k, v.get('score', 0)) for k, v in dimensions.items() if v.get('status') == 'ok'
        ]
        if scored_dims:
            scored_dims.sort(key=lambda x: x[1], reverse=True)
            best_key, best_score = scored_dims[0]
            worst_key, worst_score = scored_dims[-1]
            strongest_dim = {'name': _dim_name_map.get(best_key, best_key), 'score': best_score}
            weakest_dim = {'name': _dim_name_map.get(worst_key, worst_key), 'score': worst_score}

    # B15-T3: 使用 markdown_content 作为 advice_detail
    advice_detail = row['markdown_content'] if row['markdown_content'] else None

    # 005: price_advice 实时计算（不使用日报缓存，确保持仓状态正确识别）
    # Bugfix: 日报缓存中的 price_advice 可能在持仓修复前生成，导致状态错误
    price_advice = None
    try:
        from modules.price_advisor import generate_price_advice as _gpa

        # 查最新收盘价（report-latest 上下文中无 latest_close）
        _conn_pa = get_connection()
        _cur_pa = _conn_pa.cursor()
        _cur_pa.execute(
            'SELECT close FROM raw_kline WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1',
            (stock_id,),
        )
        _r = _cur_pa.fetchone()
        _conn_pa.close()
        _latest_close = float(_r['close']) if _r and _r['close'] else None
        price_advice = _gpa(
            stock_id,
            {
                'rating': row['rating'] or '持有观望',
                'latest_close': _latest_close,
                'has_position': False,  # price_advisor 会自行查持仓
            },
        )
    except Exception as _e:
        import logging

        logging.getLogger(__name__).warning(f'report-latest price_advice 实时计算失败: {_e}')

    result = {
        'success': True,
        'stock_id': stock_id,
        'stock_code': row['stock_code'] or row['symbol'],
        'stock_name': row['stock_name'] or row['name'],
        'market': row['market'],
        # 评分四元组（与列表页同源）
        'engine_version': row['engine_version'],
        'total_score': row['total_score'],
        'rating': row['rating'],
        'rating_label': row['rating_label'],
        'rating_date': latest_date,
        # 评分变动
        'prev_score': row['prev_score'],
        'score_change': row['score_change'],
        # 四维数据（从 key_factors 重建）
        'dimensions': dimensions,
        'data_warnings': data_warnings,
        # B15-T3: 投资建议字段补充
        'advice_detail': advice_detail,
        'position_advice': None,
        'price_advice': price_advice,
        'strongest_dim': strongest_dim,
        'weakest_dim': weakest_dim,
        'data_quality': data_quality if data_quality else None,
        # 来源标记
        'data_source': 'daily_reports',
        'generated_at': row['generated_at'],
    }

    # 009补充：动态操作建议覆盖旧建议，避免矛盾
    if result.get('price_advice', {}).get('action_suggestion'):
        result['position_advice'] = result['price_advice']['action_suggestion']

    return jsonify(result)


@app.route('/api/stocks/<int:stock_id>/advise', methods=['POST'])
def api_advise_stock(stock_id):
    """执行模块2分析+模块3建议生成，返回完整评级建议"""
    from modules.advisor import generate_advice

    try:
        result = generate_advice(stock_id)
        # 005: 后处理集成价格建议（不修改 generate_advice）
        if result.get('success'):
            from modules.price_advisor import generate_price_advice

            result['price_advice'] = generate_price_advice(stock_id, result)
            # 009补充：动态操作建议覆盖旧建议，避免矛盾
            if result.get('price_advice', {}).get('action_suggestion'):
                result['position_advice'] = result['price_advice']['action_suggestion']
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': f'建议生成失败: {str(e)}'}), 500


@app.route('/api/stocks/<int:stock_id>/ratings', methods=['GET'])
def api_get_ratings(stock_id):
    """查看评级历史记录"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT rh.*, s.symbol, s.name
        FROM ratings_history rh
        JOIN stocks s ON rh.stock_id = s.id
        WHERE rh.stock_id = ?
        ORDER BY rh.rating_date DESC LIMIT 10
    """,
        (stock_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': rows, 'count': len(rows)})


@app.route('/api/db-stats', methods=['GET'])
def api_db_stats():
    """数据库统计信息"""
    conn = get_connection()
    cursor = conn.cursor()

    stats = {}
    tables = [
        'stocks',
        'raw_kline',
        'raw_fundamental',
        'raw_capital_flow',
        'raw_sentiment',
        'data_status',
        'news_sentiment',
        'error_logs',
    ]
    for table in tables:
        cursor.execute(f'SELECT COUNT(*) as count FROM {table}')
        stats[table] = cursor.fetchone()['count']

    conn.close()
    return jsonify({'success': True, 'stats': stats})


@app.route('/api/stocks/<int:stock_id>/news', methods=['GET'])
def api_get_news(stock_id):
    """查看已采集的消息面数据"""
    conn = get_connection()
    cursor = conn.cursor()

    # news_sentiment 聚合表最新记录
    cursor.execute(
        """
        SELECT * FROM news_sentiment WHERE stock_id = ?
        ORDER BY news_date DESC LIMIT 5
    """,
        (stock_id,),
    )
    sentiment_rows = [dict(r) for r in cursor.fetchall()]

    # raw_sentiment 逐条新闻（读取后去重）
    cursor.execute(
        """
        SELECT title, content, sentiment_score, info_date, source
        FROM raw_sentiment WHERE stock_id = ? AND info_type = 'news'
        ORDER BY info_date DESC LIMIT 15
    """,
        (stock_id,),
    )
    raw_rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # 去重：语义级去重（三元组精确 + 标题相似度聚类）
    from modules.analysis_engine import _dedup_news

    deduped_rows, raw_news_count = _dedup_news(raw_rows)

    news_rows = []
    for r in deduped_rows[:10]:
        row = dict(r)
        score = row.get('sentiment_score', 0) or 0
        if score > 0.1:
            row['sentiment_label'] = '正面'
        elif score < -0.1:
            row['sentiment_label'] = '负面'
        else:
            row['sentiment_label'] = '中性'
        # 解析source字段（存储格式为 "来源|URL"）
        src_raw = row.get('source', '') or ''
        if '|' in src_raw:
            parts = src_raw.split('|', 1)
            row['source_name'] = parts[0]
            row['source_url'] = parts[1] if len(parts) > 1 else ''
        else:
            row['source_name'] = src_raw
            row['source_url'] = ''
        news_rows.append(row)

    # 极端情绪预警判断
    extreme_warning = False
    if sentiment_rows:
        avg_s = sentiment_rows[0].get('avg_sentiment', 0) or 0
        if abs(avg_s) >= 0.95:
            extreme_warning = True

    return jsonify(
        {
            'success': True,
            'sentiment_summary': sentiment_rows,
            'news_list': news_rows,
            'news_count': len(news_rows),
            'extreme_warning': extreme_warning,
        }
    )


# ============================================================
# 批量分析与评级列表
# ============================================================


@app.route('/api/batch-analyze', methods=['POST'])
def api_batch_analyze():
    """
    批量处理：多选股票 → 采集 + 四维分析 + 评级生成
    逐只执行，单只失败不阻塞整体。
    """
    data = request.get_json(silent=True) or {}
    stock_ids = data.get('stock_ids', [])

    if not stock_ids or not isinstance(stock_ids, list):
        return jsonify({'success': False, 'message': '请提供 stock_ids 数组'}), 400

    if len(stock_ids) > 20:
        return jsonify({'success': False, 'message': '单次最多批量处理20只股票'}), 400

    from modules.advisor import generate_advice
    from modules.data_collector import (
        collect_stock_data,
        fetch_capital_flow_batch,
        fetch_stock_industry,
    )

    results = []
    success_count = 0
    fail_count = 0

    # P0-CAPITAL-001：批量资金面预取（同花顺全市场源，1次调用替代东财逐只采集）
    # 仅对 A 股白名单生效，港股走原东财 secid 路径；预取失败不阻断后续逐只采集
    try:
        conn_b = get_connection()
        cursor_b = conn_b.cursor()
        placeholders = ','.join('?' * len(stock_ids))
        cursor_b.execute(
            f"SELECT DISTINCT symbol FROM stocks WHERE id IN ({placeholders}) AND market = 'a_stock'",
            stock_ids,
        )
        a_symbols = [r['symbol'] for r in cursor_b.fetchall()]
        conn_b.close()
        if a_symbols:
            batch_cap = fetch_capital_flow_batch(a_symbols)
            print(f'[batch-analyze] 资金面批量预取: {batch_cap}')
    except Exception as e:
        print(f'[batch-analyze] 资金面批量预取异常(不影响后续): {e}')

    for sid in stock_ids:
        try:
            sid = int(sid)
        except (ValueError, TypeError):
            results.append({'stock_id': sid, 'status': 'failed', 'error': '无效的stock_id'})
            fail_count += 1
            continue

        # 查询股票信息
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT symbol, name, market, industry FROM stocks WHERE id = ?', (sid,))
        stock = cursor.fetchone()
        conn.close()

        if not stock:
            results.append({'stock_id': sid, 'status': 'failed', 'error': '股票不存在'})
            fail_count += 1
            continue

        symbol = stock['symbol']
        name = stock['name']
        market = stock['market']

        # INDUSTRY-DYNAMIC：批量分析时若 industry 为空则补取
        try:
            if not (stock['industry'] or '').strip() or stock['industry'] in ('未分类',):
                industry = fetch_stock_industry(symbol, market)
                conn_ind = get_connection()
                conn_ind.execute('UPDATE stocks SET industry = ? WHERE id = ?', (industry, sid))
                conn_ind.commit()
                conn_ind.close()
        except Exception:
            pass  # 行业补取失败不阻塞分析流程

        try:
            # 步骤1: 数据采集
            collect_stock_data(symbol, market)

            # 步骤2: 通过 advisor.generate_advice() 统一引擎入口
            # 由 engine_switcher 自动分流 v5/legacy，与每日报告生成路径一致
            advice = generate_advice(sid)
            if not advice.get('success'):
                results.append(
                    {
                        'stock_id': sid,
                        'symbol': symbol,
                        'name': name,
                        'status': 'failed',
                        'error': advice.get('message', '分析失败'),
                    }
                )
                fail_count += 1
                continue

            # 005: 后处理集成价格建议（不修改 generate_advice）
            from modules.price_advisor import generate_price_advice

            advice['price_advice'] = generate_price_advice(sid, advice)

            results.append(
                {
                    'stock_id': sid,
                    'symbol': symbol,
                    'name': name,
                    'status': 'completed',
                    'rating': advice['rating'],
                    'total_score': advice['total_score'],
                    'engine_version': advice.get('engine_version', 'legacy'),
                    'operation_suggestion': advice.get('action_advice', ''),
                    'rating_time': advice.get('rating_date', ''),
                    'price_advice': advice.get('price_advice', {}),
                }
            )
            success_count += 1

        except Exception as e:
            results.append(
                {
                    'stock_id': sid,
                    'symbol': symbol,
                    'name': name,
                    'status': 'failed',
                    'error': str(e)[:200],
                }
            )
            fail_count += 1

    return jsonify(
        {
            'success': True,
            'total': len(stock_ids),
            'success_count': success_count,
            'fail_count': fail_count,
            'results': results,
        }
    )


@app.route('/api/ratings', methods=['GET'])
def api_get_ratings_list():
    """
    评级列表：返回所有已分析股票的最新评级结果。
    B11-SCORE-SYNC：数据源统一为 daily_reports 表（与看板/日报同源）。
    支持排序：sort_by=rating_time|total_score，order=desc|asc
    支持筛选：rating=强烈推荐买入|推荐买入|持有观望|建议减仓|强烈建议卖出
    """
    sort_by = request.args.get('sort_by', 'rating_time')
    order = request.args.get('order', 'desc')
    rating_filter = request.args.get('rating', '')

    # 白名单防注入
    valid_sort = {
        'rating_time': 'dr.generated_at',
        'total_score': 'dr.total_score',
        'rating': 'dr.rating',
        'symbol': 's.symbol',
    }
    sort_col = valid_sort.get(sort_by, 'dr.generated_at')
    sort_dir = 'DESC' if order.lower() == 'desc' else 'ASC'

    # B11-SCORE-SYNC：从 daily_reports 表读取最新一期报告（与看板/日报同源）
    conn = get_connection()
    cursor = conn.cursor()

    # 先查最新报告日期
    cursor.execute('SELECT MAX(report_date) as latest_date FROM daily_reports')
    date_row = cursor.fetchone()
    latest_date = date_row['latest_date'] if date_row else None

    if not latest_date:
        conn.close()
        return jsonify({'success': True, 'ratings': [], 'count': 0})

    # 014修复：优先取 daily，无 daily 时取 intraday（与 get_latest_reports 同源逻辑）
    cursor.execute(
        'SELECT COUNT(*) as cnt FROM daily_reports '
        "WHERE report_date=? AND report_type='daily' AND status='ok'",
        (latest_date,),
    )
    has_daily = cursor.fetchone()['cnt'] > 0
    target_type = 'daily' if has_daily else 'intraday'

    sql = """
        SELECT dr.stock_code, dr.stock_name, dr.total_score, dr.rating,
               dr.rating_label, dr.engine_version, dr.generated_at,
               dr.prev_score, dr.score_change, dr.key_factors,
               dr.report_date, dr.report_type, dr.status as report_status,
               s.id as stock_id, s.symbol, s.name, s.market,
               sg.name as group_name
        FROM daily_reports dr
        JOIN stocks s ON dr.stock_id = s.id
        LEFT JOIN groups sg ON s.group_id = sg.id AND sg.type='watchlist'
        WHERE dr.report_date = ? AND dr.status = 'ok' AND dr.report_type = ?
    """
    params = [latest_date, target_type]

    if rating_filter:
        sql += ' AND dr.rating_label = ?'
        params.append(rating_filter)

    sql += f' ORDER BY {sort_col} {sort_dir}'

    cursor.execute(sql, params)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    # 适配前端期望的字段名（保持与旧 analysis_results 接口兼容）
    from datetime import datetime, timezone
    from datetime import timedelta as _td

    _now = datetime.now(timezone(_td(hours=8)))
    for row in rows:
        # 兼容旧字段名
        row['rating_time'] = row.get('generated_at', '')
        row['created_at'] = row.get('generated_at', '')
        # DEV-TASKS-20260727-003：超买超卖信号（从已有 key_factors 派生）
        row['obos_signal'] = _derive_obos_signal(row.get('key_factors'))
        # 数据时效标识
        row['data_stale'] = False
        rt = row.get('generated_at') or ''
        if rt:
            try:
                rt_dt = datetime.fromisoformat(rt)
                if rt_dt.tzinfo is None:
                    rt_dt = rt_dt.replace(tzinfo=timezone(_td(hours=8)))
                hours_diff = (_now - rt_dt).total_seconds() / 3600
                row['data_stale'] = hours_diff > 24
            except (ValueError, TypeError):
                pass

    return jsonify(
        {
            'success': True,
            'ratings': rows,
            'count': len(rows),
        }
    )


# ============================================================
# 持仓管理 API
# ============================================================


@app.route('/api/portfolio/groups', methods=['GET'])
def api_get_portfolio_groups():
    """获取所有持仓分组（兼容别名：转发到统一接口，type=portfolio）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT g.id, g.name, g.type, g.display_order, g.is_default, g.created_at,
               (SELECT COUNT(*) FROM holdings h WHERE h.group_id = g.id) as holding_count
        FROM groups g
        WHERE g.type = 'portfolio'
        ORDER BY g.display_order, g.id
    """)
    groups = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'groups': groups})


@app.route('/api/portfolio/groups', methods=['POST'])
def api_create_portfolio_group():
    """新建持仓分组（兼容别名：自动同步创建 watchlist 同名分组）"""
    data = request.get_json(silent=True) or {}
    name = data.get('name', '').strip()
    if not name:
        return jsonify({'success': False, 'message': '分组名称不能为空'}), 400

    conn = get_connection()
    cursor = conn.cursor()
    counterpart_created = False
    try:
        cursor.execute(
            'INSERT INTO groups (name, type, display_order) VALUES (?, "portfolio", ?)',
            (name, data.get('display_order', 0)),
        )
        group_id = cursor.lastrowid
        # 自动同步：检查 watchlist 是否有同名分组
        cursor.execute('SELECT id FROM groups WHERE name=? AND type="watchlist"', (name,))
        if not cursor.fetchone():
            cursor.execute('INSERT INTO groups (name, type) VALUES (?, "watchlist")', (name,))
            counterpart_created = True
        conn.commit()
        conn.close()
        return jsonify(
            {
                'success': True,
                'group_id': group_id,
                'counterpart_created': counterpart_created,
                'counterpart_type': 'watchlist',
            }
        )
    except Exception as e:
        conn.close()
        if 'UNIQUE' in str(e):
            return jsonify({'success': False, 'message': '分组名称已存在'}), 400
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/portfolio/groups/<int:group_id>', methods=['PUT'])
def api_update_portfolio_group(group_id):
    """修改持仓分组名（兼容别名：同步更新 watchlist 同名分组）"""
    return api_update_group(group_id)


@app.route('/api/portfolio/groups/<int:group_id>', methods=['DELETE'])
def api_delete_portfolio_group(group_id):
    """删除持仓分组（兼容别名：仅删除当前类型）"""
    return api_delete_group(group_id)


@app.route('/api/portfolio/holdings', methods=['GET'])
def api_get_holdings():
    """获取所有持仓列表（含股票信息 + 最新价格缓存）"""
    group_id = request.args.get('group_id', '')

    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        SELECT h.id, h.stock_id, h.group_id, h.cost_price, h.quantity,
               h.notes, h.created_at, h.updated_at,
               h.realized_pnl, h.status, h.is_cost_adjusted,
               s.symbol, s.name, s.market,
               pg.name as group_name,
               pc.latest_price as latest_price, pc.pct_change as price_pct_change,
               pc.updated_at as price_cache_time
        FROM holdings h
        INNER JOIN stocks s ON h.stock_id = s.id
        LEFT JOIN groups pg ON h.group_id = pg.id AND pg.type='portfolio'
        LEFT JOIN price_cache pc ON h.stock_id = pc.stock_id
    """
    params = []
    if group_id:
        sql += ' WHERE h.group_id = ?'
        params.append(group_id)
    sql += ' ORDER BY h.updated_at DESC'

    cursor.execute(sql, params)
    holdings = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # 格式化 latest_price / price_updated_at / 精确市值计算
    import decimal
    from datetime import datetime, timedelta

    now = datetime.now()
    for h in holdings:
        h['latest_price'] = (
            round(h.get('latest_price'), 2) if h.get('latest_price') is not None else None
        )
        cache_time = h.pop('price_cache_time', None)
        h['price_updated_at'] = cache_time
        # 价格是否过期（超过 PRICE_CACHE_TTL_HOURS 小时）
        h['price_expired'] = False
        if cache_time:
            try:
                dt = datetime.strptime(cache_time, '%Y-%m-%d %H:%M:%S')
                if (now - dt) > timedelta(hours=PRICE_CACHE_TTL_HOURS):
                    h['price_expired'] = True
            except (ValueError, TypeError):
                h['price_expired'] = True
        elif h['latest_price'] is not None:
            h['price_expired'] = True

        # ---- 精确市值计算 ----
        # market_value = quantity × latest_price（银行家舍入法，保留2位小数）
        qty = h.get('quantity') or 0
        price = h.get('latest_price')
        if price is not None and price > 0 and qty > 0:
            raw = qty * price
            # 银行家舍入法（Round Half To Even）
            h['market_value'] = float(
                decimal.Decimal(str(raw)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )
        else:
            # latest_price 为 NULL 时，市值显示 None（前端渲染 '--'）
            h['market_value'] = None

        # 向后兼容：旧字段 estimated_market_value 标记 @deprecated
        h['estimated_market_value'] = h['market_value']

        # ---- 浮动盈亏计算 ----
        # unrealized_pnl = (latest_price - avg_cost) × quantity
        cost_price = h.get('cost_price') or 0
        realized = h.get('realized_pnl') or 0
        if price is not None and qty > 0:
            raw_pnl = (price - cost_price) * qty
            h['unrealized_pnl'] = float(
                decimal.Decimal(str(raw_pnl)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )
        else:
            h['unrealized_pnl'] = None

        # ---- 总收益 = 已实现盈亏 + 浮动盈亏 ----
        if h['unrealized_pnl'] is not None:
            h['total_pnl'] = round(realized + h['unrealized_pnl'], 2)
        else:
            h['total_pnl'] = round(realized, 2) if realized != 0 else None

        # ---- 数据状态标签 ----
        if price is None:
            h['data_status'] = 'offline'  # 无价格数据
        elif h['price_expired']:
            h['data_status'] = 'cache'  # 缓存（过期）
        else:
            h['data_status'] = 'realtime'  # 实时

    return jsonify({'success': True, 'holdings': holdings, 'count': len(holdings)})


@app.route('/api/portfolio/summary')
def api_portfolio_summary():
    """账户级汇总指标：总市值 / 当日盈亏 / 持仓数量 / 平均评分。

    P2 扩展：在原有持仓汇总基础上，增加评分维度（平均评分/评级分布/引擎统计），
    数据来自 daily_reports 表最新一期，与每日报告页同源。
    """
    import decimal
    import hashlib
    from datetime import datetime, timedelta, timezone

    _CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')
    group_id = request.args.get('group_id', '')
    conn = get_connection()
    cursor = conn.cursor()

    # ---------- 1. 持仓汇总（原逻辑不变） ----------
    sql = """
        SELECT h.id, h.stock_id, h.group_id, h.cost_price, h.quantity,
               h.notes, h.created_at, h.updated_at,
               h.realized_pnl, h.status, h.is_cost_adjusted,
               s.symbol, s.name, s.market,
               pg.name as group_name,
               pc.latest_price as latest_price, pc.pct_change as price_pct_change,
               pc.updated_at as price_cache_time
        FROM holdings h
        INNER JOIN stocks s ON h.stock_id = s.id
        LEFT JOIN groups pg ON h.group_id = pg.id AND pg.type='portfolio'
        LEFT JOIN price_cache pc ON h.stock_id = pc.stock_id
    """
    params = []
    if group_id:
        sql += ' WHERE h.group_id = ?'
        params.append(group_id)

    cursor.execute(sql, params)
    holdings = [dict(row) for row in cursor.fetchall()]

    total_market_value = 0.0
    total_unrealized = 0.0
    total_realized = 0.0
    has_market_value = False
    has_unrealized = False

    for h in holdings:
        qty = h.get('quantity') or 0
        price = h.get('latest_price')
        price = round(price, 2) if price is not None else None
        cost_price = h.get('cost_price') or 0
        realized = h.get('realized_pnl') or 0

        total_realized += realized

        if price is not None and price > 0 and qty > 0:
            mv = qty * price
            total_market_value += float(
                decimal.Decimal(str(mv)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )
            has_market_value = True

            upnl = (price - cost_price) * qty
            total_unrealized += float(
                decimal.Decimal(str(upnl)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )
            has_unrealized = True

    result = {
        'success': True,
        'total_market_value': round(total_market_value, 2) if has_market_value else None,
        'total_unrealized_pnl': round(total_unrealized, 2) if has_unrealized else None,
        'total_realized_pnl': round(total_realized, 2),
        'holding_count': len(holdings),
        'active_count': sum(1 for h in holdings if (h.get('quantity') or 0) > 0),
    }

    unrealized = result['total_unrealized_pnl']
    if unrealized is not None:
        result['total_pnl'] = round(result['total_realized_pnl'] + unrealized, 2)
    else:
        result['total_pnl'] = (
            result['total_realized_pnl'] if result['total_realized_pnl'] != 0 else None
        )

    # ---------- 2. P2 新增：评分维度汇总（来自 daily_reports 表） ----------
    # 强制修正项：先查 MAX(report_date) 再传入主查询，避免相关子查询
    cursor.execute('SELECT MAX(report_date) as latest_date FROM daily_reports')
    date_row = cursor.fetchone()
    latest_report_date = date_row['latest_date'] if date_row else None

    avg_score = None
    rating_dist = {}
    engine_stats = {'v5': 0, 'legacy': 0}
    scores_list = []

    if latest_report_date:
        cursor.execute(
            'SELECT total_score, rating, engine_version FROM daily_reports '
            'WHERE report_date = ? AND status = ?',
            (latest_report_date, 'ok'),
        )
        for r in cursor.fetchall():
            sc = r['total_score']
            if sc is not None:
                scores_list.append(sc)
            rt = r['rating']
            if rt:
                rating_dist[rt] = rating_dist.get(rt, 0) + 1
            ev = r['engine_version']
            if ev in engine_stats:
                engine_stats[ev] += 1

    if scores_list:
        avg_score = round(sum(scores_list) / len(scores_list), 1)

    # 从 daily_reports 表读取报告生成时间（稳定值，用于ETag）
    report_generated_at = None
    if latest_report_date:
        cursor.execute(
            'SELECT MAX(generated_at) as gen_at FROM daily_reports WHERE report_date = ?',
            (latest_report_date,),
        )
        gen_row = cursor.fetchone()
        report_generated_at = gen_row['gen_at'] if gen_row else None

    result['report_date'] = latest_report_date
    result['avg_score'] = avg_score
    result['rating_distribution'] = rating_dist
    result['engine_stats'] = engine_stats
    result['generated_at'] = report_generated_at or datetime.now(_CN_TZ).isoformat()

    conn.close()

    # ---------- 3. ETag 缓存（排除 generated_at 避免时间戳波动） ----------
    etag_payload = {k: v for k, v in result.items() if k != 'generated_at'}
    etag = hashlib.md5(json.dumps(etag_payload, sort_keys=True, default=str).encode()).hexdigest()
    if request.headers.get('If-None-Match') == etag:
        return '', 304
    resp = jsonify(result)
    resp.headers['ETag'] = etag
    return resp


# ============================================================
# P2: 自选股批量评分看板接口（只读聚合，零引擎侵入）
# ============================================================


@app.route('/api/portfolio/watchlist-scores')
def api_portfolio_watchlist_scores():
    """自选股批量评分看板数据（单次四表 JOIN，零引擎侵入）。

    数据源：stocks + holdings + price_cache + daily_reports
    返回：全部自选股的最新评分、评级、引擎版本、持仓、市值、行业分类。
    缓存：ETag + If-None-Match → 304
    """
    import decimal
    import hashlib
    from datetime import datetime, timedelta, timezone

    _CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')
    conn = get_connection()
    cursor = conn.cursor()

    # 强制修正项：先查 MAX(report_date)，再作为参数传入主查询
    cursor.execute('SELECT MAX(report_date) as latest_date FROM daily_reports')
    date_row = cursor.fetchone()
    latest_report_date = date_row['latest_date'] if date_row else None

    # 四表 JOIN：stocks LEFT JOIN holdings / price_cache / daily_reports
    if latest_report_date:
        cursor.execute(
            """
            SELECT s.id, s.symbol, s.name, s.market, s.status, s.industry,
                   h.cost_price, h.quantity, h.realized_pnl,
                   pc.latest_price, pc.pct_change as price_pct_change,
                   dr.engine_version, dr.total_score, dr.rating,
                   dr.rating_label, dr.score_change, dr.prev_score,
                   dr.key_factors, dr.status as report_status
            FROM stocks s
            LEFT JOIN holdings h     ON s.id = h.stock_id
            LEFT JOIN price_cache pc ON s.id = pc.stock_id
            LEFT JOIN daily_reports dr ON s.id = dr.stock_id
                                     AND dr.report_date = ?
            WHERE s.status != 'delisted'
            ORDER BY
                CASE WHEN dr.total_score IS NULL THEN 1 ELSE 0 END,
                dr.total_score DESC
        """,
            (latest_report_date,),
        )
    else:
        # 无报告数据时，仅返回股票+持仓+价格
        cursor.execute("""
            SELECT s.id, s.symbol, s.name, s.market, s.status, s.industry,
                   h.cost_price, h.quantity, h.realized_pnl,
                   pc.latest_price, pc.pct_change as price_pct_change,
                   NULL as engine_version, NULL as total_score,
                   NULL as rating, NULL as rating_label,
                   NULL as score_change, NULL as prev_score,
                   NULL as key_factors, 'no_report' as report_status
            FROM stocks s
            LEFT JOIN holdings h     ON s.id = h.stock_id
            LEFT JOIN price_cache pc ON s.id = pc.stock_id
            WHERE s.status != 'delisted'
            ORDER BY s.added_at DESC
        """)

    rows = [dict(row) for row in cursor.fetchall()]

    # 从 daily_reports 表读取报告生成时间（稳定值，用于ETag）
    report_generated_at = None
    if latest_report_date:
        cursor.execute(
            'SELECT MAX(generated_at) as gen_at FROM daily_reports WHERE report_date = ?',
            (latest_report_date,),
        )
        gen_row = cursor.fetchone()
        report_generated_at = gen_row['gen_at'] if gen_row else None

    conn.close()

    stocks = []
    for r in rows:
        qty = r.get('quantity') or 0
        price = r.get('latest_price')
        price = round(price, 2) if price is not None else None

        # 精确市值
        market_value = None
        if price is not None and price > 0 and qty > 0:
            raw_mv = qty * price
            market_value = float(
                decimal.Decimal(str(raw_mv)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )

        # 浮动盈亏
        unrealized = None
        cost = r.get('cost_price') or 0
        if price is not None and qty > 0:
            raw_pnl = (price - cost) * qty
            unrealized = float(
                decimal.Decimal(str(raw_pnl)).quantize(
                    decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                )
            )

        # 行业分类（从 stocks.industry 读取，INDUSTRY-DYNAMIC）
        industry = r.get('industry') or '未分类'

        stocks.append(
            {
                'id': r['id'],
                'symbol': r['symbol'],
                'name': r['name'],
                'market': r['market'],
                'industry': industry,
                'cost_price': round(cost, 2) if cost else None,
                'quantity': qty,
                'latest_price': price,
                'price_pct_change': r.get('price_pct_change'),
                'market_value': market_value,
                'unrealized_pnl': unrealized,
                'engine_version': r.get('engine_version'),
                'total_score': round(r['total_score'], 1)
                if r.get('total_score') is not None
                else None,
                'rating': r.get('rating'),
                'rating_label': r.get('rating_label'),
                'score_change': round(r['score_change'], 1)
                if r.get('score_change') is not None
                else None,
                'has_report': r.get('report_status') == 'ok',
                # DEV-TASKS-20260727-003：超买超卖信号（从 key_factors 派生，不暴露原始因子）
                'obos_signal': _derive_obos_signal(r.get('key_factors')),
            }
        )

    result = {
        'success': True,
        'report_date': latest_report_date,
        'generated_at': report_generated_at or datetime.now(_CN_TZ).isoformat(),
        'stocks': stocks,
        'total': len(stocks),
    }

    # ETag 缓存（排除 generated_at 避免时间戳波动）
    etag_payload = {k: v for k, v in result.items() if k != 'generated_at'}
    etag = hashlib.md5(json.dumps(etag_payload, sort_keys=True, default=str).encode()).hexdigest()
    if request.headers.get('If-None-Match') == etag:
        return '', 304
    resp = jsonify(result)
    resp.headers['ETag'] = etag
    return resp


@app.route('/api/portfolio/holdings/<int:stock_id>', methods=['POST'])
def api_upsert_holding(stock_id):
    """创建或更新持仓（成本价/数量/分组）"""
    data = request.get_json(silent=True) or {}
    cost_price = data.get('cost_price', 0)
    quantity = data.get('quantity', 0)
    group_id = data.get('group_id')
    notes = data.get('notes', '')

    conn = get_connection()
    cursor = conn.cursor()

    # 检查股票是否存在
    cursor.execute('SELECT id FROM stocks WHERE id = ?', (stock_id,))
    if not cursor.fetchone():
        conn.close()
        return jsonify({'success': False, 'message': '股票不存在'}), 404

    cursor.execute(
        """
        INSERT INTO holdings (stock_id, group_id, cost_price, quantity, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(stock_id) DO UPDATE SET
            group_id = excluded.group_id,
            cost_price = excluded.cost_price,
            quantity = excluded.quantity,
            notes = excluded.notes,
            updated_at = datetime('now', 'localtime')
    """,
        (stock_id, group_id, cost_price, quantity, notes),
    )

    holding_id = cursor.execute(
        'SELECT id FROM holdings WHERE stock_id = ?', (stock_id,)
    ).fetchone()['id']
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'holding_id': holding_id})


@app.route('/api/portfolio/holdings/<int:stock_id>', methods=['DELETE'])
def api_delete_holding(stock_id):
    """删除持仓：交易流水保留（holding_id 置 NULL）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id FROM holdings WHERE stock_id = ?', (stock_id,))
    row = cursor.fetchone()
    if not row:
        conn.close()
        return jsonify({'success': False, 'message': '持仓不存在'}), 404

    holding_id = row['id']
    # 交易流水的 holding_id 置 NULL（流水保留）
    cursor.execute('UPDATE trade_records SET holding_id = NULL WHERE holding_id = ?', (holding_id,))
    cursor.execute('DELETE FROM holdings WHERE id = ?', (holding_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '持仓已删除，交易流水已保留'})


# ============================================================
# 交易流水 CRUD + 持仓重算联动
# ============================================================


def _recalculate_holding(cursor, stock_id):
    """根据该股票所有有效流水（按时间顺序）重新计算持仓。
    计算规则：
    - 买入(buy)：增加数量，加权平均成本 = (旧持仓成本 + 新买入金额) / 新数量
    - 卖出(sell)：减少数量，已实现盈亏 += (卖出价 - 持仓均价) * 卖出数量
    - 分红(dividend)：数量不变，已实现盈亏 += 分红金额(amount)
    - 若重算后数量 ≤ 0，标记 status='cleared'（保留记录，不物理删除）
    返回重算后的持仓快照 dict。
    """
    # 确保持仓记录存在
    cursor.execute('SELECT id FROM holdings WHERE stock_id=?', (stock_id,))
    h_row = cursor.fetchone()
    if not h_row:
        # 自动创建持仓记录
        cursor.execute(
            'INSERT OR IGNORE INTO holdings (stock_id, cost_price, quantity, realized_pnl, status) '
            'VALUES (?, 0, 0, 0, "active")',
            (stock_id,),
        )
        cursor.execute('SELECT id FROM holdings WHERE stock_id=?', (stock_id,))
        h_row = cursor.fetchone()

    holding_id = h_row['id']

    # 按时间顺序获取所有有效流水
    cursor.execute(
        """
        SELECT trade_type, price, quantity, amount, trade_date, created_at
        FROM trade_records
        WHERE stock_id=?
        ORDER BY trade_date ASC, created_at ASC
    """,
        (stock_id,),
    )
    trades = cursor.fetchall()

    total_qty = 0  # 总持仓数量
    avg_cost = 0.0  # 加权平均成本
    total_cost = 0.0  # 总成本（用于计算均价）
    realized_pnl = 0.0  # 已实现盈亏

    for t in trades:
        qty = int(t['quantity'] or 0)
        price = float(t['price'] or 0)
        amount = float(t['amount'] or 0)

        if t['trade_type'] == 'buy':
            if qty > 0:
                total_cost += qty * price
                total_qty += qty
                avg_cost = total_cost / total_qty if total_qty > 0 else 0
        elif t['trade_type'] == 'sell':
            if qty > 0:
                sell_qty = min(qty, total_qty) if total_qty > 0 else qty
                realized_pnl += (price - avg_cost) * sell_qty
                total_qty -= qty
                if total_qty <= 0:
                    total_qty = 0
                    total_cost = 0
                else:
                    total_cost = avg_cost * total_qty
        elif t['trade_type'] == 'dividend':
            # 分红：金额直接计入已实现盈亏
            realized_pnl += amount if amount > 0 else 0

    # 判断状态
    status = 'cleared' if total_qty <= 0 else 'active'

    # 更新持仓表
    cursor.execute(
        """
        UPDATE holdings SET
            quantity = ?,
            cost_price = ?,
            realized_pnl = ?,
            status = ?,
            updated_at = datetime('now', 'localtime')
        WHERE stock_id = ?
    """,
        (total_qty, round(avg_cost, 4), round(realized_pnl, 2), status, stock_id),
    )

    return {
        'stock_id': stock_id,
        'holding_id': holding_id,
        'quantity': total_qty,
        'avg_cost': round(avg_cost, 4),
        'realized_pnl': round(realized_pnl, 2),
        'total_value': round(avg_cost * total_qty, 2),
        'status': status,
    }


@app.route('/api/portfolio/holdings/<int:stock_id>/trades', methods=['GET'])
def api_get_trades(stock_id):
    """查看某只股票的交易流水"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT tr.*, s.symbol, s.name
        FROM trade_records tr
        INNER JOIN stocks s ON tr.stock_id = s.id
        WHERE tr.stock_id = ?
        ORDER BY tr.trade_date DESC, tr.created_at DESC
    """,
        (stock_id,),
    )
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'trades': trades, 'count': len(trades)})


@app.route('/api/portfolio/trades', methods=['GET'])
def api_get_all_trades():
    """全局交易流水列表（所有股票，支持分页与筛选）"""
    conn = get_connection()
    cursor = conn.cursor()
    trade_type = request.args.get('type', '')
    sql = """
        SELECT tr.*, s.symbol, s.name, s.market
        FROM trade_records tr
        INNER JOIN stocks s ON tr.stock_id = s.id
    """
    params = []
    if trade_type in ('buy', 'sell', 'dividend'):
        sql += ' WHERE tr.trade_type = ?'
        params.append(trade_type)
    sql += ' ORDER BY tr.trade_date DESC, tr.created_at DESC'
    cursor.execute(sql, params)
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()
    # 汇总统计
    total_buy = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'buy')
    total_sell = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'sell')
    total_dividend = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'dividend')
    return jsonify(
        {
            'success': True,
            'trades': trades,
            'count': len(trades),
            'summary': {
                'total_buy_amount': round(total_buy, 2),
                'total_sell_amount': round(total_sell, 2),
                'total_dividend': round(total_dividend, 2),
                'net_amount': round(total_sell + total_dividend - total_buy, 2),
            },
        }
    )


@app.route('/api/portfolio/cost-adjustments', methods=['GET'])
def api_get_all_cost_adjustments():
    """全局成本修正历史列表（所有持仓，审计追溯）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
        SELECT pca.*, h.stock_id, s.symbol, s.name
        FROM position_cost_adjustments pca
        LEFT JOIN holdings h ON pca.holding_id = h.id
        LEFT JOIN stocks s ON h.stock_id = s.id
        ORDER BY pca.created_at DESC
    """)
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # FIX-ADJUST-UI：字段别名映射，使返回JSON与前端期望字段名一致
    for rec in records:
        rec['original_avg_cost'] = rec.pop('old_cost', None)
        rec['adjusted_avg_cost'] = rec.pop('new_cost', None)
        rec['adjustment_reason'] = rec.pop('reason', None)
        rec['adjustment_notes'] = rec.get('operator_ip', '') or ''

    return jsonify({'success': True, 'adjustments': records, 'count': len(records)})


@app.route('/api/portfolio/holdings/<int:stock_id>/trades', methods=['POST'])
def api_add_trade(stock_id):
    """新增交易流水记录（自动触发持仓重算）"""
    data = request.get_json(silent=True) or {}
    trade_type = data.get('trade_type', '').strip()
    if trade_type not in ('buy', 'sell', 'dividend'):
        return jsonify({'success': False, 'message': 'trade_type 必须为 buy/sell/dividend'}), 400

    price = data.get('price', 0)
    quantity = data.get('quantity', 0)
    amount = data.get('amount') or (float(price) * int(quantity) if quantity else 0)
    trade_date = data.get('trade_date', '')
    notes = data.get('notes', '')

    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')  # 加锁，防止并发冲突

        # 获取 holding_id（如果持仓存在）
        cursor.execute('SELECT id FROM holdings WHERE stock_id = ?', (stock_id,))
        h = cursor.fetchone()
        holding_id = h['id'] if h else None

        cursor.execute(
            """
            INSERT INTO trade_records (holding_id, stock_id, trade_type, price, quantity, amount, trade_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (holding_id, stock_id, trade_type, price, quantity, amount, trade_date, notes),
        )

        trade_id = cursor.lastrowid

        # 触发持仓重算
        recalculated = _recalculate_holding(cursor, stock_id)

        conn.commit()
        conn.close()
        return jsonify(
            {'success': True, 'trade_id': trade_id, 'recalculated_position': recalculated}
        )
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'新增流水失败（已回滚）：{e}'}), 500


@app.route('/api/portfolio/trades/<int:trade_id>', methods=['PUT'])
def api_update_trade(trade_id):
    """编辑交易流水（触发持仓重算，事务保护）。
    Body 可包含：trade_type, price, quantity, amount, trade_date, notes, stock_id(跨股票编辑)
    """
    data = request.get_json(silent=True) or {}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')

        # 操作限制检查（已清算 / T+1锁定）
        allowed, err_msg, status_code = _check_trade_edit_restriction(cursor, trade_id, 'edit')
        if not allowed:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': err_msg}), status_code

        # 获取流水原始记录
        cursor.execute('SELECT * FROM trade_records WHERE id=?', (trade_id,))
        trade = cursor.fetchone()
        if not trade:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': '流水记录不存在'}), 404

        old_stock_id = trade['stock_id']
        new_stock_id = data.get('stock_id', old_stock_id)

        # 动态构建 UPDATE 语句
        fields = []
        params = []
        for col in ['trade_type', 'price', 'quantity', 'amount', 'trade_date', 'notes', 'stock_id']:
            if col in data:
                fields.append(f'{col} = ?')
                params.append(data[col])

        if fields:
            # 校验 trade_type
            if 'trade_type' in data and data['trade_type'] not in ('buy', 'sell', 'dividend'):
                conn.rollback()
                conn.close()
                return jsonify(
                    {'success': False, 'message': 'trade_type 必须为 buy/sell/dividend'}
                ), 400

            # 如果 stock_id 变了，更新 holding_id 指向
            if new_stock_id != old_stock_id:
                cursor.execute('SELECT id FROM holdings WHERE stock_id=?', (new_stock_id,))
                new_h = cursor.fetchone()
                new_holding_id = new_h['id'] if new_h else None
                fields.append('holding_id = ?')
                params.append(new_holding_id)

            params.append(trade_id)
            cursor.execute(f'UPDATE trade_records SET {", ".join(fields)} WHERE id=?', params)

            # 重算新股票持仓
            recalculated = _recalculate_holding(cursor, new_stock_id)

            # 如果跨股票编辑，还要重算原股票持仓
            if new_stock_id != old_stock_id:
                recalculated_old = _recalculate_holding(cursor, old_stock_id)
                conn.commit()
                conn.close()
                return jsonify(
                    {
                        'success': True,
                        'recalculated_position': recalculated,
                        'recalculated_old_position': recalculated_old,
                    }
                )
        else:
            recalculated = _recalculate_holding(cursor, old_stock_id)

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'recalculated_position': recalculated})

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'编辑流水失败（已回滚）：{e}'}), 500


@app.route('/api/portfolio/trades/<int:trade_id>', methods=['DELETE'])
def api_delete_trade(trade_id):
    """删除交易流水（触发持仓重算，事务保护）"""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')

        # 操作限制检查（已清算 / T+1锁定）
        allowed, err_msg, status_code = _check_trade_edit_restriction(cursor, trade_id, 'delete')
        if not allowed:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': err_msg}), status_code

        cursor.execute('SELECT stock_id FROM trade_records WHERE id=?', (trade_id,))
        trade = cursor.fetchone()
        if not trade:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': '流水记录不存在'}), 404

        stock_id = trade['stock_id']
        cursor.execute('DELETE FROM trade_records WHERE id=?', (trade_id,))

        recalculated = _recalculate_holding(cursor, stock_id)

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'recalculated_position': recalculated})

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'删除流水失败（已回滚）：{e}'}), 500


# ============================================================
# 持仓成本人工修正 + 行情刷新 + 操作限制检查
# ============================================================


def _check_trade_edit_restriction(cursor, trade_id, operation='edit'):
    """检查流水编辑/删除限制，返回 (allowed, error_msg, status_code)。
    限制规则：
    1. 已清算（持仓数量=0）的股票，禁止编辑/删除其历史流水
    2. T+1锁定：当日提交的流水次日才允许修改
    3. 单笔流水金额超过阈值时需二次验证（前端处理，后端返回提示）
    """
    cursor.execute('SELECT * FROM trade_records WHERE id=?', (trade_id,))
    trade = cursor.fetchone()
    if not trade:
        return False, '流水记录不存在', 404

    stock_id = trade['stock_id']

    # 1. 检查持仓是否已清算
    cursor.execute('SELECT quantity, status FROM holdings WHERE stock_id=?', (stock_id,))
    h = cursor.fetchone()
    if h and (h['quantity'] or 0) <= 0:
        return (
            False,
            '该股票已清算（持仓数量=0），历史流水仅允许查看，禁止'
            + ('编辑' if operation == 'edit' else '删除'),
            403,
        )

    # 2. T+1锁定检查
    if TRADE_T1_LOCK_ENABLED:
        from datetime import datetime, timedelta

        created_str = trade['created_at']
        if created_str:
            try:
                created_dt = datetime.strptime(created_str, '%Y-%m-%d %H:%M:%S')
                if datetime.now() < created_dt + timedelta(days=1):
                    return (
                        False,
                        '当日提交的流水需 T+1 后才允许修改（流水创建时间：' + created_str + '）',
                        403,
                    )
            except (ValueError, TypeError):
                pass

    return True, None, 200


@app.route('/api/positions/<int:holding_id>/cost-adjustment', methods=['POST'])
def api_cost_adjustment(holding_id):
    """持仓成本人工修正接口。
    Body: adjusted_avg_cost(float), adjustment_reason(str), force_confirm(bool, optional)
    事务保护：修正记录写入 + 持仓更新 原子性提交。
    """
    data = request.get_json(silent=True) or {}
    adjusted_cost = data.get('adjusted_avg_cost')
    reason = (data.get('adjustment_reason') or '').strip()
    force_confirm = data.get('force_confirm', False)
    operator_ip = request.headers.get('X-Forwarded-For', request.remote_addr or '')
    device_fp = data.get('device_fingerprint', '')

    # 基本校验
    if adjusted_cost is None:
        return jsonify({'success': False, 'message': 'adjusted_avg_cost 不能为空'}), 400
    try:
        adjusted_cost = float(adjusted_cost)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'adjusted_avg_cost 必须是数字'}), 400
    if adjusted_cost < 0:
        return jsonify({'success': False, 'message': '修正值不能为负'}), 400
    if not reason:
        return jsonify({'success': False, 'message': 'adjustment_reason 不能为空'}), 400

    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')  # 加锁

        # 获取当前持仓
        cursor.execute('SELECT * FROM holdings WHERE id=?', (holding_id,))
        holding = cursor.fetchone()
        if not holding:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': '持仓不存在'}), 404

        old_cost = holding['cost_price'] or 0
        stock_id = holding['stock_id']

        # 限制1：同一持仓 24 小时内最多修正 1 次
        from datetime import datetime, timedelta

        cursor.execute(
            """
            SELECT created_at FROM position_cost_adjustments
            WHERE holding_id=?
            ORDER BY created_at DESC LIMIT 1
        """,
            (holding_id,),
        )
        last_adj = cursor.fetchone()
        if last_adj:
            try:
                last_dt = datetime.strptime(last_adj['created_at'], '%Y-%m-%d %H:%M:%S')
                elapsed = datetime.now() - last_dt
                if elapsed < timedelta(hours=COST_ADJUSTMENT_COOLDOWN_HOURS):
                    remaining = timedelta(hours=COST_ADJUSTMENT_COOLDOWN_HOURS) - elapsed
                    remaining_hours = round(remaining.total_seconds() / 3600, 1)
                    conn.rollback()
                    conn.close()
                    return jsonify(
                        {
                            'success': False,
                            'message': f'同一持仓 {COST_ADJUSTMENT_COOLDOWN_HOURS} 小时内仅允许修正 1 次，请 {remaining_hours} 小时后重试',
                        }
                    ), 429
            except (ValueError, TypeError):
                pass

        # 限制2：偏离度检查
        deviation_pct = 0.0
        if old_cost > 0:
            deviation_pct = abs(adjusted_cost - old_cost) / old_cost
        else:
            deviation_pct = 1.0 if adjusted_cost > 0 else 0.0

        if deviation_pct > COST_ADJUSTMENT_DEVIATION_THRESHOLD and not force_confirm:
            conn.rollback()
            conn.close()
            return jsonify(
                {
                    'success': False,
                    'message': f'修正值偏离当前成本 {round(deviation_pct * 100, 1)}%，超过 ±{int(COST_ADJUSTMENT_DEVIATION_THRESHOLD * 100)}% 阈值，需二次确认（force_confirm=true）',
                    'deviation_pct': round(deviation_pct, 4),
                    'old_cost': old_cost,
                    'new_cost': adjusted_cost,
                    'need_force_confirm': True,
                }
            ), 400

        # 事务：写入修正记录 + 更新持仓
        cursor.execute(
            """
            INSERT INTO position_cost_adjustments
                (holding_id, stock_id, old_cost, new_cost, reason, operator, operator_ip, device_fingerprint, deviation_pct)
            VALUES (?, ?, ?, ?, ?, 'user', ?, ?, ?)
        """,
            (
                holding_id,
                stock_id,
                old_cost,
                adjusted_cost,
                reason,
                operator_ip,
                device_fp,
                round(deviation_pct, 4),
            ),
        )

        cursor.execute(
            """
            UPDATE holdings SET
                cost_price = ?,
                is_cost_adjusted = 1,
                updated_at = datetime('now', 'localtime')
            WHERE id = ?
        """,
            (adjusted_cost, holding_id),
        )

        conn.commit()
        conn.close()

        return jsonify(
            {
                'success': True,
                'message': '成本修正成功',
                'adjustment': {
                    'holding_id': holding_id,
                    'stock_id': stock_id,
                    'old_cost': round(old_cost, 4),
                    'new_cost': round(adjusted_cost, 4),
                    'reason': reason,
                    'deviation_pct': round(deviation_pct, 4),
                    'adjusted_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
                },
            }
        )

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'成本修正失败（已回滚）：{e}'}), 500


@app.route('/api/positions/<int:holding_id>/cost-adjustments', methods=['GET'])
def api_get_cost_adjustments(holding_id):
    """查询某持仓的成本修正历史记录（审计追溯）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM position_cost_adjustments
        WHERE holding_id=?
        ORDER BY created_at DESC
    """,
        (holding_id,),
    )
    records = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'adjustments': records, 'count': len(records)})


@app.route('/api/portfolio/holdings/<int:stock_id>/trade-suggestion', methods=['GET'])
def api_trade_suggestion(stock_id):
    """获取近3次同股票买入记录，用于预填推荐。
    返回 avg_price, avg_quantity, count, latest_trade_date
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT price, quantity, trade_date
        FROM trade_records
        WHERE stock_id=? AND trade_type='buy'
        ORDER BY trade_date DESC, created_at DESC
        LIMIT 3
    """,
        (stock_id,),
    )
    trades = cursor.fetchall()
    conn.close()

    if not trades:
        return jsonify({'success': True, 'suggestion': None})

    prices = [t['price'] for t in trades if t['price']]
    qtys = [t['quantity'] for t in trades if t['quantity']]
    avg_price = sum(prices) / len(prices) if prices else None
    avg_qty = sum(qtys) / len(qtys) if qtys else None

    return jsonify(
        {
            'success': True,
            'suggestion': {
                'avg_price': round(avg_price, 4) if avg_price else None,
                'avg_quantity': int(avg_qty) if avg_qty else None,
                'count': len(trades),
                'latest_trade_date': trades[0]['trade_date'],
            },
        }
    )


@app.route('/api/analytics/prefill', methods=['POST'])
def api_prefill_analytics():
    """预填埋点统计：记录预填字段使用率、修改率、二次确认触发率。
    轻量实现：写入 error_logs 表复用（module='prefill_analytics'）。
    """
    data = request.get_json(silent=True) or {}
    event_type = data.get(
        'event_type', ''
    )  # prefill_shown / field_modified / cost_confirm_triggered
    stock_id = data.get('stock_id')
    detail = data.get('detail', '')

    if not event_type:
        return jsonify({'success': False, 'message': 'event_type 不能为空'}), 400

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        INSERT INTO error_logs (stock_id, module, error_type, error_message)
        VALUES (?, 'prefill_analytics', ?, ?)
    """,
        (stock_id, event_type, detail),
    )
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '埋点已记录'})


@app.route('/api/portfolio/realized-pnl', methods=['GET'])
def api_realized_pnl():
    """已实现盈亏精确查询（加权平均成本法）。

    计算方法：加权平均法（Weighted Average Cost）
    - 买入(buy)：total_cost += qty × price，加权平均成本 = total_cost / total_qty
    - 卖出(sell)：realized_pnl += (卖出价 - 加权平均成本) × 卖出数量
    - 分红(dividend)：realized_pnl += 分红金额
    - 不含交易手续费（与券商对账单口径一致）

    参数：
      stock_id  - 可选，筛选特定股票
      period    - daily / weekly / monthly（默认不聚合，返回总额）
      start_date - 可选，筛选起始日期
      end_date   - 可选，筛选结束日期
    """
    stock_id = request.args.get('stock_id', '')
    period = request.args.get('period', '')  # daily/weekly/monthly
    start_date = request.args.get('start_date', '')
    end_date = request.args.get('end_date', '')

    conn = get_connection()
    cursor = conn.cursor()

    # 构建查询条件
    where_clauses = []
    params = []
    if stock_id:
        where_clauses.append('stock_id = ?')
        params.append(stock_id)
    if start_date:
        where_clauses.append('trade_date >= ?')
        params.append(start_date)
    if end_date:
        where_clauses.append('trade_date <= ?')
        params.append(end_date)

    where_sql = ' AND '.join(where_clauses) if where_clauses else '1=1'

    cursor.execute(
        f"""
        SELECT tr.stock_id, tr.trade_type, tr.price, tr.quantity, tr.amount,
               tr.trade_date, tr.created_at, s.symbol, s.name
        FROM trade_records tr
        INNER JOIN stocks s ON tr.stock_id = s.id
        WHERE {where_sql}
        ORDER BY tr.stock_id ASC, tr.trade_date ASC, tr.created_at ASC
    """,
        params,
    )
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # ---- 按股票分组逐笔计算（加权平均法）----
    stock_map = {}  # {stock_id: {symbol, name, trades, realized_pnl, ...}}
    for t in trades:
        sid = t['stock_id']
        if sid not in stock_map:
            stock_map[sid] = {
                'stock_id': sid,
                'symbol': t['symbol'],
                'name': t['name'],
                'total_qty': 0,
                'avg_cost': 0.0,
                'total_cost': 0.0,
                'realized_pnl': 0.0,
                'dividend_received': 0.0,
                'buy_count': 0,
                'sell_count': 0,
                'dividend_count': 0,
                'periods': {},  # 用于按日/周/月聚合
            }
        s = stock_map[sid]
        qty = int(t['quantity'] or 0)
        price = float(t['price'] or 0)
        amount = float(t['amount'] or 0)
        tdate = t['trade_date'] or ''

        if t['trade_type'] == 'buy':
            if qty > 0:
                s['total_cost'] += qty * price
                s['total_qty'] += qty
                s['avg_cost'] = s['total_cost'] / s['total_qty'] if s['total_qty'] > 0 else 0
            s['buy_count'] += 1
            pnl_delta = 0.0
        elif t['trade_type'] == 'sell':
            if qty > 0 and s['total_qty'] > 0:
                sell_qty = min(qty, s['total_qty'])
                pnl_delta = (price - s['avg_cost']) * sell_qty
                s['realized_pnl'] += pnl_delta
                s['total_qty'] -= qty
                if s['total_qty'] <= 0:
                    s['total_qty'] = 0
                    s['total_cost'] = 0
                else:
                    s['total_cost'] = s['avg_cost'] * s['total_qty']
            else:
                pnl_delta = 0.0
            s['sell_count'] += 1
        elif t['trade_type'] == 'dividend':
            pnl_delta = amount if amount > 0 else 0.0
            s['realized_pnl'] += pnl_delta
            s['dividend_received'] += pnl_delta
            s['dividend_count'] += 1
        else:
            pnl_delta = 0.0

        # 按期间聚合
        if period and pnl_delta != 0:
            if period == 'daily':
                key = tdate
            elif period == 'weekly':
                # ISO 周
                try:
                    from datetime import datetime as _dt

                    d = _dt.strptime(tdate, '%Y-%m-%d')
                    iso_year, iso_week, _ = d.isocalendar()
                    key = f'{iso_year}-W{iso_week:02d}'
                except (ValueError, TypeError):
                    key = tdate[:7] if len(tdate) >= 7 else tdate
            elif period == 'monthly':
                key = tdate[:7] if len(tdate) >= 7 else tdate
            else:
                key = tdate

            if key not in s['periods']:
                s['periods'][key] = 0.0
            s['periods'][key] += pnl_delta

    # 汇总
    total_realized = 0.0
    total_dividend = 0.0
    results = []
    for sid, s in stock_map.items():
        pnl = round(s['realized_pnl'], 2)
        total_realized += pnl
        total_dividend += s['dividend_received']
        row = {
            'stock_id': sid,
            'symbol': s['symbol'],
            'name': s['name'],
            'realized_pnl': pnl,
            'dividend_received': round(s['dividend_received'], 2),
            'current_qty': s['total_qty'],
            'avg_cost': round(s['avg_cost'], 4),
            'buy_count': s['buy_count'],
            'sell_count': s['sell_count'],
            'dividend_count': s['dividend_count'],
        }
        if period:
            row['period_breakdown'] = {k: round(v, 2) for k, v in sorted(s['periods'].items())}
        results.append(row)

    results.sort(key=lambda x: x['realized_pnl'], reverse=True)

    return jsonify(
        {
            'success': True,
            'method': 'weighted_average_cost',
            'fee_included': False,
            'total_realized_pnl': round(total_realized, 2),
            'total_dividend': round(total_dividend, 2),
            'count': len(results),
            'details': results,
        }
    )


def _fetch_realtime_price_batch(symbols_markets):
    """批量获取实时行情价格（腾讯接口）。
    symbols_markets: [(stock_id, symbol, market), ...]
    返回: {stock_id: {'price': float, 'pct_change': float}}
    """
    import requests as _requests

    result = {}
    if not symbols_markets:
        return result

    # 构造腾讯批量请求代码：sh600000,sz000001,hk00700
    tencent_codes = []
    stock_id_map = {}
    for stock_id, symbol, market in symbols_markets:
        if market == 'hk_stock':
            # 港股：5位数字
            hk_code = symbol.zfill(5)
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

    return result


@app.route('/api/portfolio/refresh-prices', methods=['POST'])
def api_refresh_prices():
    """刷新股票的最新价格（实时行情采集）。
    数据源：腾讯财经实时行情接口（免费、无需密钥）。
    同步刷新持仓 + 自选股的 price_cache。
    失败时保留旧缓存，禁止价格归零。
    """
    import time as _time

    start_ts = _time.time()
    data_source = 'tencent_realtime'
    fetch_errors = []

    conn = get_connection()
    cursor = conn.cursor()

    # 获取所有需要刷新价格的股票（持仓 + 自选股 合并去重）
    cursor.execute("""
        SELECT DISTINCT s.id as stock_id, s.symbol, s.market
        FROM stocks s
        WHERE s.status != 'delisted'
    """)
    all_stocks = [(row['stock_id'], row['symbol'], row['market']) for row in cursor.fetchall()]

    # 尝试实时采集
    price_data = _fetch_realtime_price_batch(all_stocks)
    realtime_count = len(price_data)

    updated_count = 0
    fallback_count = 0

    for stock_id, symbol, market in all_stocks:
        if stock_id in price_data:
            # 实时价格写入缓存
            p = price_data[stock_id]
            cursor.execute(
                """
                INSERT OR REPLACE INTO price_cache (stock_id, latest_price, pct_change, updated_at)
                VALUES (?, ?, ?, datetime('now', 'localtime'))
            """,
                (stock_id, p['price'], p['pct_change']),
            )
            updated_count += 1
        else:
            # 降级：从最新K线获取收盘价（保留旧缓存，不归零）
            cursor.execute(
                """
                SELECT close, pct_change FROM raw_kline
                WHERE stock_id=? ORDER BY trade_date DESC LIMIT 1
            """,
                (stock_id,),
            )
            kline = cursor.fetchone()
            if kline and kline['close']:
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO price_cache (stock_id, latest_price, pct_change, updated_at)
                    VALUES (?, ?, ?, datetime('now', 'localtime'))
                """,
                    (stock_id, kline['close'], kline['pct_change']),
                )
                fallback_count += 1
            else:
                fetch_errors.append(f'{symbol}: 无数据源')

    conn.commit()
    conn.close()

    fetch_duration_ms = int((_time.time() - start_ts) * 1000)

    return jsonify(
        {
            'success': True,
            'message': f'已刷新 {updated_count + fallback_count}/{len(all_stocks)} 只股票的价格',
            'updated_count': updated_count + fallback_count,
            'realtime_count': realtime_count,
            'fallback_count': fallback_count,
            'total': len(all_stocks),
            'data_source': data_source,
            'fetch_duration_ms': fetch_duration_ms,
            'errors': fetch_errors[:10],  # 最多返回10条错误
        }
    )


# ============================================================
# v5.0 四维评分引擎原型 API
# ============================================================


@app.route('/api/v5/scoring-demo', methods=['GET'])
def api_v5_scoring_demo():
    """v5.0 评分引擎演示接口（使用 MockDataProvider 生成模拟数据并评分）

    Query params:
      - scenario: normal / boundary / partial（默认 normal）
      - code: 股票代码（默认 600519.SH）
      - market: A / HK（默认 A）
      - close: 收盘价（默认随机）
      - missing_rate: partial场景缺失率（默认 0.3）
    """
    from modules.mock_data_provider import MockDataProvider
    from modules.scoring_engine import analyze

    scenario = request.args.get('scenario', 'normal')
    code = request.args.get('code', '600519.SH')
    market = request.args.get('market', 'A')
    close_str = request.args.get('close', '')
    missing_rate_str = request.args.get('missing_rate', '0.3')

    try:
        close = float(close_str) if close_str else None
        missing_rate = float(missing_rate_str)
    except (ValueError, TypeError):
        close = None
        missing_rate = 0.3

    provider = MockDataProvider()
    try:
        data = provider.generate(
            scenario,
            code=code,
            market=market,
            close=close,
            missing_rate=missing_rate,
            seed=42,
        )
    except Exception as e:
        return jsonify({'success': False, 'message': f'数据生成失败: {e}'}), 400

    result = analyze(data)

    return jsonify(
        {
            'success': True,
            'input_data': {
                'code': data.code,
                'market': data.market,
                'trade_date': data.trade_date,
                'close': data.close,
                'scenario': scenario,
            },
            'result': result.model_dump(),
        }
    )


@app.route('/api/v5/scoring-analyze', methods=['POST'])
def api_v5_scoring_analyze():
    """v5.0 评分引擎分析接口（接收 StockData JSON，返回评分结果）

    Body: StockData 契约字段（至少需 code/market/trade_date/close 四个必填项）
    """
    from modules.data_contract import StockData
    from modules.scoring_engine import analyze

    raw = request.get_json(silent=True) or {}

    # 必填字段校验
    required = ['code', 'market', 'trade_date', 'close']
    for f in required:
        if f not in raw or raw[f] is None:
            return jsonify({'success': False, 'message': f'缺少必填字段: {f}'}), 400

    try:
        data = StockData(**raw)
    except Exception as e:
        return jsonify({'success': False, 'message': f'StockData 构造失败: {e}'}), 400

    result = analyze(data)

    return jsonify(
        {
            'success': True,
            'result': result.model_dump(),
        }
    )


@app.route('/api/v5/scoring-validation', methods=['GET'])
def api_v5_scoring_validation():
    """v5.0 评分引擎验证接口（运行 exhaustive 56 条极端值快速检查）

    返回每条用例的评分摘要及 NaN/Inf/范围检查结果。
    """
    import math

    from modules.mock_data_provider import MockDataProvider
    from modules.scoring_engine import analyze

    provider = MockDataProvider()
    batch = provider.generate(
        'boundary',
        boundary_mode='exhaustive',
        code='600519.SH',
        market='A',
        trade_date='20260718',
        close=100.0,
    )

    results = []
    all_pass = True
    for i, data in enumerate(batch):
        try:
            result = analyze(data)
            has_nan = any(math.isnan(v) for v in [result.total_score] if v is not None) or any(
                math.isnan(getattr(result, a, 0) or 0)
                for a in [
                    'technical_score',
                    'fundamental_score',
                    'sentiment_score',
                    'capital_score',
                ]
            )
            in_range = 0 <= result.total_score <= 100
            ok = not has_nan and in_range
            if not ok:
                all_pass = False

            # 找到被修改的字段
            extremes = provider.BOUNDARY_EXTREMES
            case_field = ''
            case_val = None
            cum = 0
            for field_name, extreme_values in extremes.items():
                for val in extreme_values:
                    if cum == i:
                        case_field = field_name
                        case_val = val
                    cum += 1

            results.append(
                {
                    'case_id': f'BV-{i + 1}',
                    'field': case_field,
                    'extreme_value': case_val,
                    'total_score': result.total_score,
                    'rating': result.rating,
                    'tech': result.technical_score,
                    'fund': result.fundamental_score,
                    'news': result.sentiment_score,
                    'capital': result.capital_score,
                    'nan_check': 'OK' if not has_nan else 'FAIL',
                    'range_check': 'OK' if in_range else 'FAIL',
                }
            )
        except Exception as e:
            all_pass = False
            results.append(
                {
                    'case_id': f'BV-{i + 1}',
                    'error': str(e),
                    'nan_check': 'CRASH',
                    'range_check': 'N/A',
                }
            )

    return jsonify(
        {
            'success': True,
            'total_cases': len(results),
            'all_pass': all_pass,
            'summary': f'{len(results)}条用例, {"全部通过" if all_pass else "存在异常"}',
            'cases': results,
        }
    )


# ============================================================
# US-11: 每日报告 API
# ============================================================


@app.route('/api/daily-report/generate', methods=['POST'])
def api_daily_report_generate():
    """手动触发每日报告生成"""
    from modules.daily_report import generate_daily_report

    data = request.get_json(silent=True) or {}
    target_date = data.get('date')
    force = data.get('force', False)  # B15-T2: 强制刷新选项

    try:
        result = generate_daily_report(target_date, force=force)
        return jsonify(
            {
                'success': result['success'],
                'report_date': result['report_date'],
                'total': result['total'],
                'success_count': result['success_count'],
                'fail_count': result['fail_count'],
                'v5_count': result['v5_count'],
                'legacy_count': result['legacy_count'],
                'fallback_count': result['fallback_count'],
                'reuse_count': result.get('reuse_count', 0),
                'results': result['results'],
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'message': f'报告生成失败: {str(e)}'}), 500


@app.route('/api/daily-report/generate-intraday', methods=['POST'])
def api_daily_report_generate_intraday():
    """013: 盘中快报 — 生成 intraday 报告，不覆盖已有 daily"""
    from modules.daily_report import generate_daily_report

    try:
        result = generate_daily_report(report_type='intraday')
        return jsonify(
            {
                'success': result['success'],
                'report_date': result['report_date'],
                'report_type': 'intraday',
                'total': result['total'],
                'success_count': result['success_count'],
                'fail_count': result['fail_count'],
                'v5_count': result['v5_count'],
                'legacy_count': result['legacy_count'],
                'fallback_count': result['fallback_count'],
                'reuse_count': result.get('reuse_count', 0),
                'results': result['results'],
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'message': f'盘中快报生成失败: {str(e)}'}), 500


@app.route('/api/daily-report/latest')
def api_daily_report_latest():
    """获取最新一期报告"""
    from modules.daily_report import get_latest_reports

    return jsonify(get_latest_reports())


@app.route('/api/daily-report/<report_date>')
def api_daily_report_by_date(report_date):
    """获取指定日期的报告"""
    from modules.daily_report import get_reports_by_date

    return jsonify(get_reports_by_date(report_date))


@app.route('/api/daily-report/history')
def api_daily_report_history():
    """报告历史列表（分页）"""
    from modules.daily_report import get_report_history

    page = int(request.args.get('page', 1))
    page_size = int(request.args.get('page_size', 30))
    return jsonify(get_report_history(page, page_size))


@app.route('/api/health', methods=['GET'])
def api_health():
    """健康检查接口（供启动脚本 curl 验证）"""
    return jsonify(
        {
            'success': True,
            'status': 'running',
            'service': 'Stock Analyst',
            'version': 'v5.0',
            'port': FLASK_PORT,
        }
    )


# ============================================================
# P3-A：引擎灰度管理 API
# ============================================================

_ROLLBACK_AUDIT_LOG = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), 'logs', 'rollback_audit.log'
)


@app.route('/api/engine/status')
def api_engine_status():
    """获取当前灰度状态：mode/whitelist/blacklist/熔断状态/各股票引擎分配"""
    from modules.engine_switcher import get_grayscale_status

    return jsonify(get_grayscale_status())


@app.route('/api/engine/rollback-all', methods=['POST'])
def api_engine_rollback_all():
    """一键全量回退：将所有股票切回 legacy 引擎

    P3-A 强制修正项2：
    - 需要 confirm=true 查询参数，缺失时返回 400
    - 操作写入审计日志（时间、来源IP、previous_mode）
    - 回退后返回 ALERT-SYSTEM 标记
    """
    # 修正项2①：确认机制
    confirm = request.args.get('confirm', '').lower()
    if confirm != 'true':
        return jsonify(
            {'success': False, 'message': '请添加 ?confirm=true 参数以确认一键回退操作'}
        ), 400

    from modules.engine_switcher import rollback_all_to_legacy

    result = rollback_all_to_legacy()

    if result.get('success'):
        # 修正项2②：写入审计日志
        audit_dir = os.path.dirname(_ROLLBACK_AUDIT_LOG)
        if not os.path.exists(audit_dir):
            os.makedirs(audit_dir)

        from datetime import datetime, timedelta, timezone

        cn_tz = timezone(timedelta(hours=8))
        audit_entry = {
            'timestamp': datetime.now(cn_tz).isoformat(),
            'action': 'rollback-all',
            'source_ip': request.remote_addr or 'unknown',
            'previous_mode': result.get('previous_mode'),
            'new_mode': 'all_legacy',
        }
        with open(_ROLLBACK_AUDIT_LOG, 'a', encoding='utf-8') as f:
            f.write(json.dumps(audit_entry, ensure_ascii=False) + '\n')

        # 修正项2③：系统级预警标记
        result['alert'] = 'ALERT-SYSTEM: 全量引擎已回退至 legacy，请运维确认'
        result['audit_logged'] = True

    return jsonify(result)


# ============================================================
# M8-BACKTEST-003：评级有效性回测 API
# ============================================================


@app.route('/api/backtest/market-report')
def api_backtest_market_report():
    """市场级回测报告（A股/港股独立）
    参数: market=a_stock/hk_stock, include_simulated=true/false
    """
    market = request.args.get('market', 'a_stock')
    include_simulated = request.args.get('include_simulated', 'false').lower() == 'true'
    try:
        from modules.backtest_engine import BacktestEngine

        engine = BacktestEngine()
        report = engine.compute_market_report(market, include_simulated=include_simulated)
        return jsonify({'success': True, 'report': report})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/backtest/stock/<int:stock_id>')
def api_backtest_stock_detail(stock_id):
    """个股回测明细"""
    try:
        from modules.backtest_engine import BacktestEngine

        engine = BacktestEngine()
        detail = engine.compute_stock_detail(stock_id)
        return jsonify(detail)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/backtest/rerun', methods=['POST'])
def api_backtest_rerun():
    """手动重跑回测
    Body: {"market": "a_stock", "days": null, "force": false}
    """
    data = request.get_json(silent=True) or {}
    market = data.get('market')
    days = data.get('days')
    force = data.get('force', False)
    try:
        from modules.backtest_engine import BacktestEngine

        engine = BacktestEngine()
        result = engine.batch_backtest(market=market, days=days, force=force)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/backtest/simulate', methods=['POST'])
def api_backtest_simulate():
    """M9-PREFILL：技术面模拟回测回填（60天）
    手动触发，幂等执行。
    """
    try:
        from modules.backtest_engine import run_historical_simulation

        result = run_historical_simulation()
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/backtest/status')
def api_backtest_status():
    """回测概览（用于看板）"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT COUNT(*) as cnt FROM backtest_results')
        total_bt = cursor.fetchone()['cnt']
        cursor.execute('SELECT COUNT(*) as cnt FROM ratings_history WHERE price_at_rating > 0')
        total_ratings = cursor.fetchone()['cnt']
        cursor.execute('SELECT market, COUNT(*) as cnt FROM backtest_results GROUP BY market')
        market_dist = {r['market']: r['cnt'] for r in cursor.fetchall()}
        conn.close()
        return jsonify(
            {
                'success': True,
                'total_backtests': total_bt,
                'total_ratings_with_price': total_ratings,
                'coverage': round(total_bt / total_ratings, 4) if total_ratings > 0 else 0,
                'market_distribution': market_dist,
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/backtest/weight-experiments')
def api_backtest_weight_experiments():
    """权重实验场景列表（D4裁定预留）"""
    try:
        from modules.backtest_engine import WeightExperimentRunner

        runner = WeightExperimentRunner()
        return jsonify({'success': True, 'experiments': runner.list_experiments()})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/backtest/weight-experiments/<experiment_id>/run', methods=['POST'])
def api_backtest_run_experiment(experiment_id):
    """执行权重实验（仅模拟计算，不修改生产权重）"""
    try:
        from modules.backtest_engine import WeightExperimentRunner

        runner = WeightExperimentRunner()
        result = runner.run_experiment(experiment_id)
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# 007-PRICE-BACKTEST: 价格建议命中率回测 API
# ============================================================


@app.route('/api/price-backtest/run', methods=['POST'])
def api_price_backtest_run():
    """007: 触发价格建议回测
    Body: {"market": "a_stock", "force": false}
    """
    data = request.get_json(silent=True) or {}
    market = data.get('market', 'a_stock')
    force = data.get('force', False)
    try:
        from modules.price_backtest import run_price_backtest

        result = run_price_backtest(market=market, force=force)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/price-backtest/report')
def api_price_backtest_report():
    """007: 获取价格建议回测报告
    参数: market=a_stock/hk_stock
    """
    market = request.args.get('market', 'a_stock')
    try:
        from modules.price_backtest import compute_price_backtest_report

        report = compute_price_backtest_report(market)
        return jsonify({'success': True, 'report': report})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# M9-OPTIMIZE: 自动优化引擎 API
# ============================================================


@app.route('/api/optimizer/run', methods=['POST'])
def api_optimizer_run():
    """M9 手动触发优化
    Body: {"market": "a_stock"} 或 {"market": "hk_stock"} 或 {} (默认a_stock)
    """
    data = request.get_json(silent=True) or {}
    market = data.get('market', 'a_stock')
    try:
        from modules.optimizer_engine import OptimizerEngine

        engine = OptimizerEngine()
        result = engine.run_weekly_optimization(market)
        return jsonify({'success': True, 'result': result})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/optimizer/status')
def api_optimizer_status():
    """M9 查看当前参数 + 优化历史（US-10）
    参数: market=a_stock/hk_stock
    """
    market = request.args.get('market', 'a_stock')
    try:
        from modules.optimizer_engine import OptimizerEngine

        engine = OptimizerEngine()
        params = engine.get_current_params(market)
        history = engine.get_optimization_history(market)
        return jsonify(
            {
                'success': True,
                'params': params,
                'history': history,
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ============================================================
# US11-EXPORT: 报告导出接口（Excel 下载）
# ============================================================


@app.route('/api/export/daily-report')
def api_export_daily_report():
    """导出每日报告为 Excel"""
    from datetime import datetime, timedelta, timezone

    from flask import send_file
    from modules.export_engine import export_daily_report

    _tz = timezone(timedelta(hours=8))
    date = request.args.get('date') or datetime.now(_tz).strftime('%Y-%m-%d')
    try:
        buf = export_daily_report(date)
        filename = f'StockAnalyst_\u65e5\u62a5_{date}.xlsx'
        return send_file(
            buf,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'导出失败: {str(e)}'}), 500


@app.route('/api/export/watchlist')
def api_export_watchlist():
    """导出自选股总览为 Excel"""
    from datetime import datetime, timedelta, timezone

    from flask import send_file
    from modules.export_engine import export_watchlist

    _tz = timezone(timedelta(hours=8))
    today = datetime.now(_tz).strftime('%Y-%m-%d')
    try:
        buf = export_watchlist()
        filename = f'StockAnalyst_\u81ea\u9009\u80a1_{today}.xlsx'
        return send_file(
            buf,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'导出失败: {str(e)}'}), 500


@app.route('/api/export/backtest')
def api_export_backtest():
    """导出回测报告为 Excel"""
    from datetime import datetime, timedelta, timezone

    from flask import send_file
    from modules.export_engine import export_backtest

    _tz = timezone(timedelta(hours=8))
    market = request.args.get('market', 'a_stock')
    today = datetime.now(_tz).strftime('%Y-%m-%d')
    market_name = 'A\u80a1' if market == 'a_stock' else '\u6e2f\u80a1'
    try:
        buf = export_backtest(market)
        filename = f'StockAnalyst_\u56de\u6d4b_{market_name}_{today}.xlsx'
        return send_file(
            buf,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            as_attachment=True,
            download_name=filename,
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'导出失败: {str(e)}'}), 500


# ============================================================
# B8: 指数评级 API
# ============================================================


@app.route('/api/index-ratings', methods=['GET'])
def api_index_ratings():
    """获取所有指数最新评级"""
    try:
        from modules.index_collector import get_latest_ratings

        indices = get_latest_ratings()
        # 获取最新更新时间
        updated_at = None
        if indices:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT MAX(created_at) as t FROM index_ratings')
            row = cursor.fetchone()
            conn.close()
            if row and row['t']:
                updated_at = row['t']
        return jsonify(
            {
                'success': True,
                'indices': indices,
                'updated_at': updated_at,
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'指数评级获取失败: {str(e)}'}), 500


@app.route('/api/index-ratings/refresh', methods=['POST'])
def api_index_ratings_refresh():
    """触发指数数据采集 + 重新评级"""
    try:
        from modules.index_collector import refresh_all

        results = refresh_all()
        # 返回最新评级
        from modules.index_collector import get_latest_ratings

        indices = get_latest_ratings()
        return jsonify(
            {
                'success': True,
                'indices': indices,
                'message': f'已刷新 {len([r for r in results if "error" not in r])}/{len(results)} 只指数',
            }
        )
    except Exception as e:
        return jsonify({'success': False, 'error': f'指数刷新失败: {str(e)}'}), 500


# ============================================================
# P3-B: 智能预警 API（/api/alerts/*）
# 规则 CRUD + 未读查询 + 标记已读，均只读消费 alert_rules/alert_history
# ============================================================

_VALID_ALERT_TYPES = ('rating_change', 'score_below', 'capital_outflow')


@app.route('/api/alerts/rules', methods=['GET'])
def api_get_alert_rules():
    """查询全部预警规则列表（含全局规则与个股规则）"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ar.id, ar.rule_type, ar.stock_id, ar.threshold, ar.enabled,
                   ar.created_at, ar.updated_at,
                   s.symbol, s.name, s.market
            FROM alert_rules ar
            LEFT JOIN stocks s ON ar.stock_id = s.id
            ORDER BY ar.rule_type, ar.stock_id IS NULL DESC, ar.id
        """)
        rules = []
        for row in cursor.fetchall():
            r = dict(row)
            r['scope'] = '全局' if r['stock_id'] is None else '个股'
            rules.append(r)
        conn.close()
        return jsonify({'success': True, 'rules': rules, 'total': len(rules)})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/alerts/rules', methods=['POST'])
def api_create_alert_rule():
    """新增预警规则（校验 rule_type 仅3种）。
    Body: {rule_type, stock_id?, threshold?, enabled?}
    """
    try:
        data = request.get_json(silent=True) or {}
        rule_type = data.get('rule_type', '').strip()
        if rule_type not in _VALID_ALERT_TYPES:
            return jsonify(
                {'success': False, 'message': f'rule_type 仅支持 {_VALID_ALERT_TYPES}'}
            ), 400

        stock_id = data.get('stock_id')
        threshold = data.get('threshold')
        enabled = 1 if data.get('enabled', 1) else 0

        # stock_id 存在性校验（非全局规则时）
        if stock_id is not None:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('SELECT id FROM stocks WHERE id=?', (stock_id,))
            if not cursor.fetchone():
                conn.close()
                return jsonify({'success': False, 'message': f'stock_id={stock_id} 不存在'}), 400
            conn.close()

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO alert_rules (rule_type, stock_id, threshold, enabled)
            VALUES (?, ?, ?, ?)
        """,
            (rule_type, stock_id, threshold, enabled),
        )
        new_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'id': new_id, 'message': '规则创建成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/alerts/rules/<int:rule_id>', methods=['PUT'])
def api_update_alert_rule(rule_id):
    """修改预警规则（threshold/enabled）。
    Body: {threshold?, enabled?}
    """
    try:
        data = request.get_json(silent=True) or {}
        fields = []
        params = []

        if 'threshold' in data:
            fields.append('threshold=?')
            params.append(data['threshold'])
        if 'enabled' in data:
            fields.append('enabled=?')
            params.append(1 if data['enabled'] else 0)

        if not fields:
            return jsonify(
                {'success': False, 'message': '无可更新字段（支持 threshold/enabled）'}
            ), 400

        fields.append("updated_at=datetime('now', 'localtime')")
        params.append(rule_id)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(f'UPDATE alert_rules SET {", ".join(fields)} WHERE id=?', params)
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'success': False, 'message': f'规则 id={rule_id} 不存在'}), 404
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': '规则更新成功'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/alerts/rules/<int:rule_id>', methods=['DELETE'])
def api_delete_alert_rule(rule_id):
    """删除预警规则（软删除 enabled=0，保留历史关联）。
    Query: force=1 时物理删除（保留关联历史记录的 rule_id）
    """
    try:
        force = request.args.get('force', '0') == '1'
        conn = get_connection()
        cursor = conn.cursor()

        if force:
            cursor.execute('UPDATE alert_rules SET enabled=0 WHERE id=?', (rule_id,))
            affected = cursor.rowcount
        else:
            cursor.execute('UPDATE alert_rules SET enabled=0 WHERE id=?', (rule_id,))
            affected = cursor.rowcount

        if affected == 0:
            conn.close()
            return jsonify({'success': False, 'message': f'规则 id={rule_id} 不存在'}), 404
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': '规则已停用（软删除）'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/alerts/unread', methods=['GET'])
def api_get_unread_alerts():
    """查询未读预警列表（is_read=0，按 triggered_at DESC）。
    Query: limit（默认20）
    """
    try:
        limit = request.args.get('limit', '20')
        try:
            limit = int(limit)
        except (ValueError, TypeError):
            limit = 20
        limit = max(1, min(limit, 200))

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT ah.id, ah.rule_id, ah.stock_id, ah.alert_type, ah.trigger_value,
                   ah.message, ah.is_read, ah.triggered_at, ah.trigger_date,
                   s.symbol, s.name, s.market
            FROM alert_history ah
            LEFT JOIN stocks s ON ah.stock_id = s.id
            WHERE ah.is_read = 0
            ORDER BY ah.triggered_at DESC
            LIMIT ?
        """,
            (limit,),
        )
        alerts = [dict(row) for row in cursor.fetchall()]

        # 查询未读总数
        cursor.execute('SELECT COUNT(*) as cnt FROM alert_history WHERE is_read=0')
        unread_count = cursor.fetchone()['cnt']
        conn.close()
        return jsonify({'success': True, 'alerts': alerts, 'unread_count': unread_count})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/alerts/<int:alert_id>/read', methods=['POST'])
def api_mark_alert_read(alert_id):
    """标记单条预警已读"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE alert_history SET is_read=1 WHERE id=?', (alert_id,))
        if cursor.rowcount == 0:
            conn.close()
            return jsonify({'success': False, 'message': f'预警 id={alert_id} 不存在'}), 404
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'message': '已标记已读'})
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/alerts/read-all', methods=['POST'])
def api_mark_all_alerts_read():
    """全部标记已读"""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('UPDATE alert_history SET is_read=1 WHERE is_read=0')
        affected = cursor.rowcount
        conn.commit()
        conn.close()
        return jsonify(
            {'success': True, 'message': f'已标记 {affected} 条预警为已读', 'updated': affected}
        )
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


@app.route('/api/alerts/scan', methods=['POST'])
def api_trigger_alert_scan():
    """手动触发一次预警扫描（调试/补扫用，不影响定时调度）"""
    try:
        from modules.alert_engine import scan_once

        result = scan_once()
        return jsonify(result)
    except Exception as e:
        return jsonify({'success': False, 'message': str(e)}), 500


# ============================================================
# 启动
# ============================================================


def main():
    """启动程序"""
    # === 012-A: 全局文件日志配置 ===
    import logging
    from logging.handlers import TimedRotatingFileHandler

    log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'logs')
    os.makedirs(log_dir, exist_ok=True)

    file_handler = TimedRotatingFileHandler(
        os.path.join(log_dir, 'app.log'), when='midnight', backupCount=7, encoding='utf-8'
    )
    file_handler.setFormatter(logging.Formatter('%(asctime)s [%(name)s] %(levelname)s %(message)s'))
    file_handler.setLevel(logging.INFO)

    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(file_handler)  # addHandler 而非 basicConfig，避免冲突

    logging.getLogger(__name__).info(f'===== Stock Analyst 启动 PID={os.getpid()} =====')
    # === 012-A END ===

    print('=' * 60)
    print('  Stock Analyst 智能个股分析与评级系统')
    print('  正在初始化数据库...')
    print('=' * 60)

    init_database()

    # US-11: 启动每日报告定时调度器
    from modules.daily_report import start_scheduler

    start_scheduler()

    print()
    print('  ============================================================')
    print(f'  [OK] 服务就绪，访问地址：http://{FLASK_HOST}:{FLASK_PORT}')
    print(f'  v5.0 评分引擎演示：http://{FLASK_HOST}:{FLASK_PORT}/api/v5/scoring-demo')
    print(f'  健康检查：          http://{FLASK_HOST}:{FLASK_PORT}/api/health')
    print('  按 Ctrl+C 可以停止程序')
    print('  ============================================================')
    print()

    app.run(host=FLASK_HOST, port=FLASK_PORT, debug=FLASK_DEBUG, threaded=True)


if __name__ == '__main__':
    main()
