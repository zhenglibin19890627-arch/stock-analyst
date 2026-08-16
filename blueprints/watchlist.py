"""自选股/分组/个股数据/采集/批量分析 API 蓝图(自 app.py 拆分,函数体零改动)。"""

from flask import Blueprint, jsonify, request

from blueprints._utils import (
    _currency_label,
    _currency_unit,
    _derive_obos_signal,
    _fmt_num,
    _fmt_pct,
    _fmt_wan,
    _get_market_by_stock_id,
)
from config import MAX_WATCHLIST_SIZE
from database.db_manager import get_connection, init_database

bp = Blueprint('watchlist', __name__)

@bp.route('/api/init-db', methods=['POST'])
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


@bp.route('/api/groups', methods=['GET'])
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


@bp.route('/api/groups', methods=['POST'])
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


@bp.route('/api/groups/<int:group_id>', methods=['PUT'])
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


@bp.route('/api/groups/<int:group_id>', methods=['DELETE'])
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


@bp.route('/api/stocks', methods=['GET'])
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


@bp.route('/api/stocks', methods=['POST'])
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


@bp.route('/api/stocks/<int:stock_id>', methods=['PUT'])
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


@bp.route('/api/stocks/<int:stock_id>', methods=['DELETE'])
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


@bp.route('/api/stocks/<int:stock_id>/position', methods=['POST'])
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


@bp.route('/api/collect/<int:stock_id>', methods=['POST'])
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
            'forecast': '业绩预告',
            'sentiment': '消息面',
        }
        summary[dim_names.get(dim, dim)] = {'status': status, 'message': msg}

    return jsonify({'success': True, 'symbol': symbol, 'market': market, 'results': summary})


@bp.route('/api/stocks/<int:stock_id>/kline', methods=['GET'])
def api_get_kline(stock_id):
    """查看采集到的K线数据（涨跌幅已格式化为 +4.89% 格式，DB原始值不保留）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT trade_date, open, close, high, low, volume, pct_change, data_source
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


@bp.route('/api/stocks/<int:stock_id>/fundamental', methods=['GET'])
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


@bp.route('/api/stocks/<int:stock_id>/forecast', methods=['GET'])
def api_get_forecast(stock_id):
    """查看采集到的业绩预告数据"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT report_period, indicator, change_desc, forecast_value, change_pct,
               change_reason, forecast_type, last_year_value, announce_date,
               data_source, fetched_at
        FROM raw_forecast WHERE stock_id = ?
        ORDER BY report_period DESC, announce_date DESC
    """,
        (stock_id,),
    )
    rows = [dict(row) for row in cursor.fetchall()]
    conn.close()

    # 金额换算为亿元/万元展示（DB 存元）
    for row in rows:
        row['forecast_value_fmt'] = _fmt_wan(row.get('forecast_value'))
        row['last_year_value_fmt'] = _fmt_wan(row.get('last_year_value'))
        row['change_pct_fmt'] = _fmt_pct(row.get('change_pct'))

    return jsonify({'success': True, 'data': rows, 'count': len(rows)})


@bp.route('/api/stocks/<int:stock_id>/capital', methods=['GET'])
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


@bp.route('/api/stocks/<int:stock_id>/orderbook', methods=['GET'])
def api_get_orderbook(stock_id):
    """019Y：五档盘口（mootdx 实时快照，最近5条快照）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM stock_orderbook WHERE stock_id = ?
        ORDER BY trade_date DESC, id DESC LIMIT 5
    """,
        (stock_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': rows, 'count': len(rows)})


@bp.route('/api/stocks/<int:stock_id>/valuation', methods=['GET'])
def api_get_valuation(stock_id):
    """019Y：估值数据（stock_valuation，最近10条，来源标注 akshare/baostock）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM stock_valuation WHERE stock_id = ?
        ORDER BY trade_date DESC LIMIT 10
    """,
        (stock_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': rows, 'count': len(rows)})


@bp.route('/api/stocks/<int:stock_id>/restricted-release', methods=['GET'])
def api_get_restricted_release(stock_id):
    """019Y：限售解禁明细（风险因子，最近20条，来源标注 akshare）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT * FROM stock_restricted_release WHERE stock_id = ?
        ORDER BY release_date DESC LIMIT 20
    """,
        (stock_id,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'data': rows, 'count': len(rows)})


@bp.route('/api/stocks/<int:stock_id>/status', methods=['GET'])
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


@bp.route('/api/stocks/<int:stock_id>/news', methods=['GET'])
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


@bp.route('/api/batch-analyze', methods=['POST'])
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

    # 018: 同花顺批量预取辅助指标（ths_net_inflow），不写入主力净流入
    # 主力净流入由东方财富逐只采集提供；预取失败不阻断后续逐只采集
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


@bp.route('/api/portfolio/groups', methods=['GET'])
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


@bp.route('/api/portfolio/groups', methods=['POST'])
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
