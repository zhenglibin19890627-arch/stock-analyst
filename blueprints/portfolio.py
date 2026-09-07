"""持仓/组合/流水/成本修正/价格刷新 API 蓝图(自 app.py 拆分,函数体零改动)。"""

import json
import logging

from flask import Blueprint, jsonify, request

from blueprints._utils import _derive_obos_signal, _latest_report_join_sql
from config import (
    COST_ADJUSTMENT_COOLDOWN_HOURS,
    COST_ADJUSTMENT_DEVIATION_THRESHOLD,
    PRICE_CACHE_TTL_HOURS,
    TRADE_AMOUNT_VERIFY_THRESHOLD,
    TRADE_T1_LOCK_ENABLED,
)
from database.db_manager import get_connection

bp = Blueprint('portfolio', __name__)


# ============================================================
# 021S 多交易账户：辅助函数 + 账户 CRUD
# 持仓域隔离维度 = account_id；'all'/缺省 = 全部账户（聚合视图）。
# 自选股/分析/预警不区分账户，保持全局共享。
# ============================================================


def _get_default_account_id(cursor):
    """取默认账户 id（is_default=1 优先，否则最早创建的账户）。"""
    cursor.execute('SELECT id FROM accounts WHERE is_default = 1 ORDER BY id LIMIT 1')
    row = cursor.fetchone()
    if not row:
        cursor.execute('SELECT id FROM accounts ORDER BY id LIMIT 1')
        row = cursor.fetchone()
    return row['id'] if row else None


def _parse_account_scope(raw):
    """解析账户范围参数：''/'all'/None → None(全部账户)；数字字符串 → int。

    非法值抛 ValueError，由调用方转 400。
    """
    if raw is None or raw == '' or raw == 'all':
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise ValueError(f'非法 account_id: {raw}')


def _account_exists(cursor, account_id):
    cursor.execute('SELECT 1 FROM accounts WHERE id = ?', (account_id,))
    return cursor.fetchone() is not None


@bp.route('/api/accounts', methods=['GET'])
def api_list_accounts():
    """交易账户列表（含持仓/流水统计，供前端切换器与管理弹窗使用）"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT a.id, a.name, a.broker, a.notes, a.is_default,
               a.display_order, a.created_at,
               (SELECT COUNT(*) FROM holdings h
                 WHERE h.account_id = a.id AND h.quantity > 0) AS holding_count,
               (SELECT COUNT(*) FROM trade_records tr
                 WHERE tr.account_id = a.id) AS trade_count
        FROM accounts a
        ORDER BY a.is_default DESC, a.display_order ASC, a.id ASC
    """
    )
    accounts = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'accounts': accounts, 'count': len(accounts)})


@bp.route('/api/accounts', methods=['POST'])
def api_create_account():
    """新建交易账户。Body: {name(必填), broker(可选), notes(可选)}"""
    import sqlite3 as _sqlite3

    data = request.get_json(silent=True) or {}
    name = (data.get('name') or '').strip()
    if not name:
        return jsonify({'success': False, 'message': '账户名称不能为空'}), 400
    if len(name) > 50:
        return jsonify({'success': False, 'message': '账户名称不能超过 50 字符'}), 400

    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            'INSERT INTO accounts (name, broker, notes) VALUES (?, ?, ?)',
            (name, (data.get('broker') or '').strip(), data.get('notes') or ''),
        )
        account_id = cursor.lastrowid
        conn.commit()
        conn.close()
        return jsonify({'success': True, 'account_id': account_id, 'message': f'账户「{name}」已创建'})
    except _sqlite3.IntegrityError:
        conn.close()
        return jsonify({'success': False, 'message': f'账户名称「{name}」已存在'}), 409


@bp.route('/api/accounts/<int:account_id>', methods=['PUT'])
def api_update_account(account_id):
    """编辑交易账户。Body 可含: name / broker / notes / display_order"""
    import sqlite3 as _sqlite3

    data = request.get_json(silent=True) or {}
    conn = get_connection()
    cursor = conn.cursor()
    if not _account_exists(cursor, account_id):
        conn.close()
        return jsonify({'success': False, 'message': '账户不存在'}), 404

    fields, params = [], []
    if 'name' in data:
        name = (data.get('name') or '').strip()
        if not name:
            conn.close()
            return jsonify({'success': False, 'message': '账户名称不能为空'}), 400
        if len(name) > 50:
            conn.close()
            return jsonify({'success': False, 'message': '账户名称不能超过 50 字符'}), 400
        fields.append('name = ?')
        params.append(name)
    for col in ('broker', 'notes', 'display_order'):
        if col in data:
            fields.append(f'{col} = ?')
            params.append(data[col])

    if fields:
        try:
            cursor.execute(
                f'UPDATE accounts SET {", ".join(fields)} WHERE id = ?',
                (*params, account_id),
            )
            conn.commit()
        except _sqlite3.IntegrityError:
            conn.close()
            return jsonify({'success': False, 'message': '账户名称已被其他账户使用'}), 409
    conn.close()
    return jsonify({'success': True, 'message': '账户已更新'})


@bp.route('/api/accounts/<int:account_id>', methods=['DELETE'])
def api_delete_account(account_id):
    """删除交易账户（风控约束）：
    1. 默认账户禁止删除（存量数据归属锚点）
    2. 最后一个账户禁止删除
    3. 账户下仍有持仓时需 force_confirm=true 二次确认；
       删除时持仓移除、流水保留并归入默认账户（审计可追溯）。
    """
    data = request.get_json(silent=True) or {}
    force_confirm = data.get('force_confirm', False)

    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute('SELECT * FROM accounts WHERE id = ?', (account_id,))
    account = cursor.fetchone()
    if not account:
        conn.close()
        return jsonify({'success': False, 'message': '账户不存在'}), 404
    if account['is_default']:
        conn.close()
        return jsonify({'success': False, 'message': '默认账户不可删除'}), 403

    total = cursor.execute('SELECT COUNT(*) FROM accounts').fetchone()[0]
    if total <= 1:
        conn.close()
        return jsonify({'success': False, 'message': '至少保留一个账户，不可删除'}), 400

    holding_cnt = cursor.execute(
        'SELECT COUNT(*) FROM holdings WHERE account_id = ?', (account_id,)
    ).fetchone()[0]
    active_cnt = cursor.execute(
        'SELECT COUNT(*) FROM holdings WHERE account_id = ? AND quantity > 0', (account_id,)
    ).fetchone()[0]
    trade_cnt = cursor.execute(
        'SELECT COUNT(*) FROM trade_records WHERE account_id = ?', (account_id,)
    ).fetchone()[0]

    if holding_cnt > 0 and not force_confirm:
        conn.close()
        return jsonify(
            {
                'success': False,
                'message': (
                    f'账户「{account["name"]}」下有 {holding_cnt} 条持仓记录'
                    f'（{active_cnt} 条在仓），删除后这些持仓将一并移除、'
                    f'{trade_cnt} 条流水保留并归入默认账户。确认请传 force_confirm=true'
                ),
                'holding_count': holding_cnt,
                'active_count': active_cnt,
                'trade_count': trade_cnt,
                'need_force_confirm': True,
            }
        ), 409

    default_id = _get_default_account_id(cursor)
    try:
        conn.execute('BEGIN IMMEDIATE')
        # 流水断链 + 归入默认账户（保留备查）
        cursor.execute(
            'UPDATE trade_records SET holding_id = NULL '
            'WHERE holding_id IN (SELECT id FROM holdings WHERE account_id = ?)',
            (account_id,),
        )
        cursor.execute(
            'UPDATE trade_records SET account_id = ? WHERE account_id = ?',
            (default_id, account_id),
        )
        cursor.execute('DELETE FROM holdings WHERE account_id = ?', (account_id,))
        cursor.execute('DELETE FROM accounts WHERE id = ?', (account_id,))
        conn.commit()
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'删除账户失败（已回滚）：{e}'}), 500
    conn.close()
    return jsonify(
        {'success': True, 'message': f'账户「{account["name"]}」已删除，{trade_cnt} 条流水归入默认账户'}
    )

@bp.route('/api/portfolio/holdings', methods=['GET'])
def api_get_holdings():
    """获取持仓列表（含股票信息 + 最新价格缓存）。

    021S 多账户：?account_id=<id> 按账户过滤；缺省/'all' 返回全部账户持仓，
    每行附带 account_id / account_name 供前端区分展示。
    """
    group_id = request.args.get('group_id', '')
    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        SELECT h.id, h.account_id, h.stock_id, h.group_id, h.cost_price, h.quantity,
               h.notes, h.created_at, h.updated_at,
               h.realized_pnl, h.status, h.is_cost_adjusted,
               s.symbol, s.name, s.market,
               a.name as account_name,
               pg.name as group_name,
               pc.latest_price as latest_price, pc.pct_change as price_pct_change,
               pc.updated_at as price_cache_time
        FROM holdings h
        INNER JOIN stocks s ON h.stock_id = s.id
        LEFT JOIN accounts a ON h.account_id = a.id
        LEFT JOIN groups pg ON h.group_id = pg.id AND pg.type='portfolio'
        LEFT JOIN price_cache pc ON h.stock_id = pc.stock_id
        WHERE 1=1
    """
    params = []
    if group_id:
        sql += ' AND h.group_id = ?'
        params.append(group_id)
    if account_id is not None:
        sql += ' AND h.account_id = ?'
        params.append(account_id)
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

        # ---- 021BL：持仓盈亏比例（券商口径的 盈亏% 列） ----
        if price is not None and qty > 0 and cost_price > 0:
            h['unrealized_pnl_pct'] = round((price - cost_price) / cost_price * 100, 2)
        else:
            h['unrealized_pnl_pct'] = None

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


@bp.route('/api/portfolio/summary')
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
    try:
        account_scope = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400
    conn = get_connection()
    cursor = conn.cursor()

    # ---------- 1. 持仓汇总（021S：支持按账户过滤） ----------
    sql = """
        SELECT h.id, h.account_id, h.stock_id, h.group_id, h.cost_price, h.quantity,
               h.notes, h.created_at, h.updated_at,
               h.realized_pnl, h.status, h.is_cost_adjusted,
               s.symbol, s.name, s.market,
               a.name as account_name,
               pg.name as group_name,
               pc.latest_price as latest_price, pc.pct_change as price_pct_change,
               pc.updated_at as price_cache_time
        FROM holdings h
        INNER JOIN stocks s ON h.stock_id = s.id
        LEFT JOIN accounts a ON h.account_id = a.id
        LEFT JOIN groups pg ON h.group_id = pg.id AND pg.type='portfolio'
        LEFT JOIN price_cache pc ON h.stock_id = pc.stock_id
        WHERE 1=1
    """
    params = []
    if group_id:
        sql += ' AND h.group_id = ?'
        params.append(group_id)
    if account_scope is not None:
        sql += ' AND h.account_id = ?'
        params.append(account_scope)

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

    # ---------- 1.5 021S：全账户视图时输出分账户汇总 ----------
    accounts_breakdown = None
    if account_scope is None:
        _acc_map = {}
        for h in holdings:
            aid = h.get('account_id')
            if aid is None:
                continue
            m = _acc_map.setdefault(
                aid,
                {
                    'account_id': aid,
                    'account_name': h.get('account_name') or f'账户{aid}',
                    'total_market_value': 0.0,
                    'total_unrealized_pnl': 0.0,
                    'total_realized_pnl': 0.0,
                    'holding_count': 0,
                    'active_count': 0,
                    '_has_mv': False,
                    '_has_upnl': False,
                },
            )
            qty = h.get('quantity') or 0
            price = h.get('latest_price')
            cost_price = h.get('cost_price') or 0
            realized = h.get('realized_pnl') or 0
            m['holding_count'] += 1
            m['total_realized_pnl'] += realized
            if qty > 0:
                m['active_count'] += 1
            if price is not None and price > 0 and qty > 0:
                m['total_market_value'] += float(
                    decimal.Decimal(str(qty * price)).quantize(
                        decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                    )
                )
                m['_has_mv'] = True
                m['total_unrealized_pnl'] += float(
                    decimal.Decimal(str((price - cost_price) * qty)).quantize(
                        decimal.Decimal('0.01'), rounding=decimal.ROUND_HALF_EVEN
                    )
                )
                m['_has_upnl'] = True
        accounts_breakdown = []
        for m in sorted(_acc_map.values(), key=lambda x: x['account_id']):
            _has_mv = m.pop('_has_mv')
            _has_upnl = m.pop('_has_upnl')
            m['total_market_value'] = round(m['total_market_value'], 2) if _has_mv else None
            m['total_unrealized_pnl'] = round(m['total_unrealized_pnl'], 2) if _has_upnl else None
            m['total_realized_pnl'] = round(m['total_realized_pnl'], 2)
            accounts_breakdown.append(m)

    result = {
        'success': True,
        'account_scope': account_scope if account_scope is not None else 'all',
        'accounts_breakdown': accounts_breakdown,
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
    # 019R: 与看板同口径——每股最新一份有效报告（status='ok'、daily 优先），
    # 聚合范围限定非退市自选股（与看板行集自洽），不再按全局单一 MAX(report_date) 聚合
    cursor.execute(
        'SELECT MAX(lr.report_date) as latest_date, MIN(lr.report_date) as min_date '
        'FROM stocks s JOIN ' + _latest_report_join_sql() + ' ON lr.stock_id = s.id '
        "WHERE s.status != 'delisted'"
    )
    date_row = cursor.fetchone()
    latest_report_date = date_row['latest_date'] if date_row else None
    report_date_min = date_row['min_date'] if date_row else None

    avg_score = None
    rating_dist = {}
    # 021AE：v5 单引擎——'history' 统计历史行（engine_version 为 NULL/旧值）
    engine_stats = {'v5': 0, 'history': 0}
    scores_list = []
    report_generated_at = None

    if latest_report_date:
        cursor.execute(
            'SELECT lr.total_score, lr.rating, lr.engine_version, lr.generated_at '
            'FROM stocks s JOIN ' + _latest_report_join_sql() + ' ON lr.stock_id = s.id '
            "WHERE s.status != 'delisted'"
        )
        for r in cursor.fetchall():
            sc = r['total_score']
            if sc is not None:
                scores_list.append(sc)
            rt = r['rating']
            if rt:
                rating_dist[rt] = rating_dist.get(rt, 0) + 1
            ev = r['engine_version']
            if ev == 'v5':
                engine_stats['v5'] += 1
            else:
                engine_stats['history'] += 1
            ga = r['generated_at']
            if ga and (report_generated_at is None or ga > report_generated_at):
                report_generated_at = ga

    if scores_list:
        avg_score = round(sum(scores_list) / len(scores_list), 1)

    result['report_date'] = latest_report_date
    result['report_date_min'] = report_date_min
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


def _parse_pa_zone(pa_json):
    """021BM：从最新报告已存 price_advice JSON 提取看板建议卡所需区间摘要。

    只读展示层（零重算）：区间标签/上下沿 + 买入侧前两档网格
    （无持仓即"第一/第二买入位"，有持仓即"补仓一档/二档"）。
    无数据或解析失败返回 None（前端退化为不显示区间，不影响主卡片）。
    """
    if not pa_json:
        return None
    try:
        pa = json.loads(pa_json) if isinstance(pa_json, str) else pa_json
    except (TypeError, ValueError):
        return None
    if not isinstance(pa, dict):
        return None
    low, high = pa.get('buy_range_low'), pa.get('buy_range_high')
    if low is None or high is None:
        return None
    levels = []
    for g in pa.get('grid') or []:
        if isinstance(g, dict) and g.get('type') == 'buy' and g.get('price') is not None:
            levels.append(
                {'price': g.get('price'), 'pct': g.get('pct'), 'label': g.get('label') or ''}
            )
        if len(levels) >= 2:
            break
    return {
        'label': pa.get('zone_label') or '买入区间',
        'low': round(low, 2),
        'high': round(high, 2),
        'stop_loss': pa.get('stop_loss'),
        'levels': levels,
    }


@bp.route('/api/portfolio/watchlist-scores')
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

    # 019R: 每股最新一份有效报告（ROW_NUMBER 派生表 LEFT JOIN），不再依赖
    # 全局单一 MAX(report_date) JOIN；无报告股票由 LEFT JOIN 兜底（评分 NULL 排末尾）。
    # 019D 口径（daily 优先 + status='ok'）逐股化后内置于 _latest_report_join_sql()。
    cursor.execute(
        """
        SELECT s.id, s.symbol, s.name, s.market, s.status, s.industry,
               h.cost_price, h.quantity, h.realized_pnl,
               pc.latest_price, pc.pct_change as price_pct_change,
               lr.engine_version, lr.total_score, lr.rating,
               lr.rating_label, lr.score_change, lr.prev_score,
               lr.key_factors, lr.data_warnings, lr.status as report_status, lr.generated_at,
               lr.report_date, lr.price_advice
        FROM stocks s
        LEFT JOIN holdings h ON h.id = (
            -- 021S 多账户：同一股票可能有多条分账户持仓，
            -- 看板每股仅一行，取持仓数量最大的一条展示
            SELECT h2.id FROM holdings h2
            WHERE h2.stock_id = s.id
            ORDER BY h2.quantity DESC LIMIT 1
        )
        LEFT JOIN price_cache pc ON s.id = pc.stock_id
        LEFT JOIN """ + _latest_report_join_sql() + """
        ON lr.stock_id = s.id
        WHERE s.status != 'delisted'
        ORDER BY
            CASE WHEN lr.total_score IS NULL THEN 1 ELSE 0 END,
            lr.total_score DESC
    """
    )

    rows = [dict(row) for row in cursor.fetchall()]

    # 顶层日期：入选报告（每股最新有效报告）的最新/最早日期
    report_dates = sorted({r['report_date'] for r in rows if r.get('report_date')})
    latest_report_date = report_dates[-1] if report_dates else None
    report_date_min = report_dates[0] if report_dates else None

    # 每股最新报告集合中的 MAX(generated_at)（稳定值，用于ETag；口径随多日期同步）
    report_generated_at = None
    if latest_report_date:
        cursor.execute(
            'SELECT MAX(lr.generated_at) as gen_at FROM ' + _latest_report_join_sql()
        )
        gen_row = cursor.fetchone()
        report_generated_at = gen_row['gen_at'] if gen_row else None

    conn.close()

    # 020R-54：行业资金背景——最新交易日全板块一次计算，逐股按行业名匹配（港股/未匹配为 None）
    industry_bg_map = {}
    match_board_name = None
    try:
        from modules.market_overview import get_industry_flow_bg_map
        from modules.market_overview import match_board_name as _mbn

        industry_bg_map = get_industry_flow_bg_map()
        match_board_name = _mbn
    except Exception as e:  # noqa: BLE001
        logging.getLogger(__name__).warning(f'[020R-54] 行业资金背景加载失败: {e}')

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

        # 020R-54：行业资金背景（展示层关联，不影响评分）
        industry_flow_bg = None
        if r.get('market') != 'hk_stock' and industry != '未分类' and industry_bg_map and match_board_name:
            try:
                board = match_board_name(industry, list(industry_bg_map.keys()))
                industry_flow_bg = industry_bg_map.get(board) if board else None
            except Exception:  # noqa: BLE001
                industry_flow_bg = None

        stocks.append(
            {
                'id': r['id'],
                'symbol': r['symbol'],
                'name': r['name'],
                'market': r['market'],
                'industry': industry,
                'industry_flow_bg': industry_flow_bg,
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
                'generated_at': r.get('generated_at'),
                'report_date': r.get('report_date'),
                'data_warnings': r.get('data_warnings'),
                # DEV-TASKS-20260727-003：超买超卖信号（从 key_factors 派生，不暴露原始因子）
                'obos_signal': _derive_obos_signal(r.get('key_factors')),
                # 021BM：价格建议区间（最新报告已存 JSON，零重算；建议卡买入侧展示）
                'pa_zone': _parse_pa_zone(r.get('price_advice')),
            }
        )

    result = {
        'success': True,
        'report_date': latest_report_date,
        'report_date_min': report_date_min,
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


@bp.route('/api/portfolio/holdings/<int:stock_id>', methods=['POST'])
def api_upsert_holding(stock_id):
    """创建或更新持仓（成本价/数量/分组/账户）。

    021S 多账户：body.account_id 指定归属账户（缺省=默认账户）；
    同一股票可在不同账户各有一条持仓，冲突键为 (account_id, stock_id)。
    """
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

    # 解析目标账户：显式指定 > 默认账户
    account_id = data.get('account_id')
    if account_id is not None:
        try:
            account_id = int(account_id)
        except (TypeError, ValueError):
            conn.close()
            return jsonify({'success': False, 'message': f'非法 account_id: {account_id}'}), 400
        if not _account_exists(cursor, account_id):
            conn.close()
            return jsonify({'success': False, 'message': '账户不存在'}), 404
    else:
        account_id = _get_default_account_id(cursor)
        if account_id is None:
            conn.close()
            return jsonify({'success': False, 'message': '系统无可用账户，请先创建'}), 500

    cursor.execute(
        """
        INSERT INTO holdings (account_id, stock_id, group_id, cost_price, quantity, notes, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, datetime('now', 'localtime'))
        ON CONFLICT(account_id, stock_id) DO UPDATE SET
            group_id = excluded.group_id,
            cost_price = excluded.cost_price,
            quantity = excluded.quantity,
            notes = excluded.notes,
            updated_at = datetime('now', 'localtime')
    """,
        (account_id, stock_id, group_id, cost_price, quantity, notes),
    )

    holding_id = cursor.execute(
        'SELECT id FROM holdings WHERE stock_id = ? AND account_id = ?',
        (stock_id, account_id),
    ).fetchone()['id']
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'holding_id': holding_id, 'account_id': account_id})


@bp.route('/api/portfolio/holdings/<int:stock_id>', methods=['DELETE'])
def api_delete_holding(stock_id):
    """删除持仓：交易流水保留（holding_id 置 NULL）。

    021S 多账户：同一股票可能存在多条（分账户）持仓。
    - ?account_id=<id> 删除指定账户的持仓
    - 缺省时若该股票仅一条持仓则直接删除；多条则返回 409 要求指定账户
    """
    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    conn = get_connection()
    cursor = conn.cursor()
    if account_id is None:
        cursor.execute('SELECT id, account_id FROM holdings WHERE stock_id = ?', (stock_id,))
        rows = cursor.fetchall()
        if not rows:
            conn.close()
            return jsonify({'success': False, 'message': '持仓不存在'}), 404
        if len(rows) > 1:
            conn.close()
            return jsonify(
                {
                    'success': False,
                    'message': '该股票在多个账户均有持仓，请指定 account_id 后删除',
                    'holdings': [dict(r) for r in rows],
                }
            ), 409
        holding_id = rows[0]['id']
    else:
        row = cursor.execute(
            'SELECT id FROM holdings WHERE stock_id = ? AND account_id = ?',
            (stock_id, account_id),
        ).fetchone()
        if not row:
            conn.close()
            return jsonify({'success': False, 'message': '该账户下不存在此股票的持仓'}), 404
        holding_id = row['id']

    # 交易流水的 holding_id 置 NULL（流水保留，账户归属不变）
    cursor.execute('UPDATE trade_records SET holding_id = NULL WHERE holding_id = ?', (holding_id,))
    cursor.execute('DELETE FROM holdings WHERE id = ?', (holding_id,))
    conn.commit()
    conn.close()
    return jsonify({'success': True, 'message': '持仓已删除，交易流水已保留'})


# ============================================================
# 交易流水 CRUD + 持仓重算联动
# ============================================================


def _recalculate_holding(cursor, stock_id, account_id):
    """根据该（股票, 账户）下所有有效流水（按时间顺序）重新计算持仓。
    021S 多账户：重算范围严格限定在单一账户内，账户间成本/盈亏互不影响。
    计算规则：
    - 买入(buy)：增加数量，加权平均成本 = (旧持仓成本 + 新买入金额) / 新数量
    - 卖出(sell)：减少数量，已实现盈亏 += (卖出价 - 持仓均价) * 卖出数量
    - 分红(dividend)：数量不变，已实现盈亏 += 分红金额(amount)
    - 红利补税(dividend_tax)：数量不变，已实现盈亏 -= 补税金额(amount)——
      021AM：股息红利差异扣税（A股按持股期限补扣/港股通代扣20%）与派息日
      不同天发生，作独立流水记录，金额为补扣税款（正数存储）
    - 若重算后数量 ≤ 0，标记 status='cleared'（保留记录，不物理删除）
    返回重算后的持仓快照 dict。
    """
    # 确保持仓记录存在
    cursor.execute(
        'SELECT id FROM holdings WHERE stock_id=? AND account_id=?',
        (stock_id, account_id),
    )
    h_row = cursor.fetchone()
    if not h_row:
        # 自动创建持仓记录
        cursor.execute(
            'INSERT OR IGNORE INTO holdings (account_id, stock_id, cost_price, quantity, realized_pnl, status) '
            'VALUES (?, ?, 0, 0, 0, "active")',
            (account_id, stock_id),
        )
        cursor.execute(
            'SELECT id FROM holdings WHERE stock_id=? AND account_id=?',
            (stock_id, account_id),
        )
        h_row = cursor.fetchone()

    holding_id = h_row['id']

    # 按时间顺序获取本账户内所有有效流水
    cursor.execute(
        """
        SELECT trade_type, price, quantity, amount, commission, trade_date, created_at
        FROM trade_records
        WHERE stock_id=? AND account_id=?
        ORDER BY trade_date ASC, created_at ASC
    """,
        (stock_id, account_id),
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
        # 021X：手续费口径——买入计入成本基数，卖出/分红扣减已实现盈亏
        commission = float(t['commission'] or 0)

        if t['trade_type'] == 'buy':
            if qty > 0:
                # 021BL：优先实际成交金额（021AM 金额直填），缺失回退 价格×数量——与券商口径一致
                buy_amount = amount if amount > 0 else qty * price
                total_cost += buy_amount + commission
                total_qty += qty
                avg_cost = total_cost / total_qty if total_qty > 0 else 0
        elif t['trade_type'] == 'sell':
            if qty > 0:
                sell_qty = min(qty, total_qty) if total_qty > 0 else qty
                # 021BL：已实现盈亏按实际成交金额口径（券商一致）：卖出金额-费用-卖出数量×摊薄成本
                sell_amount = amount if amount > 0 else price * sell_qty
                if qty > sell_qty:
                    sell_amount = sell_amount * sell_qty / qty  # 超卖钳制时按比例折算
                realized_pnl += sell_amount - commission - sell_qty * avg_cost
                total_qty -= qty
                if total_qty <= 0:
                    total_qty = 0
                    total_cost = 0
                    avg_cost = 0   # 021BL：清仓后成本价归零（券商口径），不再残留旧成本
                else:
                    total_cost = avg_cost * total_qty
        elif t['trade_type'] == 'dividend':
            # 分红：金额计入已实现盈亏（手续费/划扣费一并扣除）
            realized_pnl += max(0, amount) - commission
        elif t['trade_type'] == 'dividend_tax':
            # 021AM：红利补税——金额从已实现盈亏中扣减（amount 正数=补扣税款）
            realized_pnl -= max(0, amount) + commission

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
        WHERE id = ?
    """,
        (total_qty, round(avg_cost, 4), round(realized_pnl, 2), status, holding_id),
    )

    return {
        'stock_id': stock_id,
        'account_id': account_id,
        'holding_id': holding_id,
        'quantity': total_qty,
        'avg_cost': round(avg_cost, 4),
        'realized_pnl': round(realized_pnl, 2),
        'total_value': round(avg_cost * total_qty, 2),
        'status': status,
    }


@bp.route('/api/portfolio/holdings/<int:stock_id>/trades', methods=['GET'])
def api_get_trades(stock_id):
    """查看某只股票的交易流水（021S：可选 ?account_id= 按账户过滤）"""
    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        SELECT tr.*, s.symbol, s.name, a.name as account_name
        FROM trade_records tr
        INNER JOIN stocks s ON tr.stock_id = s.id
        LEFT JOIN accounts a ON tr.account_id = a.id
        WHERE tr.stock_id = ?
    """
    params = [stock_id]
    if account_id is not None:
        sql += ' AND tr.account_id = ?'
        params.append(account_id)
    sql += ' ORDER BY tr.trade_date DESC, tr.created_at DESC'
    cursor.execute(sql, params)
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()
    return jsonify({'success': True, 'trades': trades, 'count': len(trades)})


@bp.route('/api/portfolio/trades', methods=['GET'])
def api_get_all_trades():
    """全局交易流水列表（所有股票，支持分页与筛选；021S 支持按账户过滤）"""
    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    conn = get_connection()
    cursor = conn.cursor()
    trade_type = request.args.get('type', '')
    sql = """
        SELECT tr.*, s.symbol, s.name, s.market, a.name as account_name
        FROM trade_records tr
        INNER JOIN stocks s ON tr.stock_id = s.id
        LEFT JOIN accounts a ON tr.account_id = a.id
        WHERE 1=1
    """
    params = []
    if trade_type in ('buy', 'sell', 'dividend', 'dividend_tax'):
        sql += ' AND tr.trade_type = ?'
        params.append(trade_type)
    if account_id is not None:
        sql += ' AND tr.account_id = ?'
        params.append(account_id)
    sql += ' ORDER BY tr.trade_date DESC, tr.created_at DESC'
    cursor.execute(sql, params)
    trades = [dict(row) for row in cursor.fetchall()]
    conn.close()
    # 汇总统计（021AM：红利补税单列并计入净流入）
    total_buy = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'buy')
    total_sell = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'sell')
    total_dividend = sum(t['amount'] or 0 for t in trades if t.get('trade_type') == 'dividend')
    total_dividend_tax = sum(
        t['amount'] or 0 for t in trades if t.get('trade_type') == 'dividend_tax'
    )
    return jsonify(
        {
            'success': True,
            'trades': trades,
            'count': len(trades),
            'summary': {
                'total_buy_amount': round(total_buy, 2),
                'total_sell_amount': round(total_sell, 2),
                'total_dividend': round(total_dividend, 2),
                'total_dividend_tax': round(total_dividend_tax, 2),
                'net_amount': round(
                    total_sell + total_dividend - total_dividend_tax - total_buy, 2
                ),
            },
        }
    )


@bp.route('/api/portfolio/cost-adjustments', methods=['GET'])
def api_get_all_cost_adjustments():
    """全局成本修正历史列表（所有持仓，审计追溯）"""
    conn = get_connection()
    cursor = conn.cursor()
    # 020R-22：直接用修正记录自带的 stock_id 关联股票——
    # 此前经 holdings 关联，持仓被删后 holding 断链，h.stock_id(NULL) 还会覆盖
    # pca.stock_id 导致股票代码/名称丢失。
    # 021S：经 holding_id 弱关联带出账户名（断链时显示 '--'，不影响主字段）
    cursor.execute("""
        SELECT pca.*, s.symbol, s.name, a.name as account_name
        FROM position_cost_adjustments pca
        LEFT JOIN stocks s ON pca.stock_id = s.id
        LEFT JOIN holdings h ON pca.holding_id = h.id
        LEFT JOIN accounts a ON h.account_id = a.id
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


@bp.route('/api/portfolio/holdings/<int:stock_id>/trades', methods=['POST'])
def api_add_trade(stock_id):
    """新增交易流水记录（自动触发持仓重算）。

    021S 多账户：body.account_id 指定归属账户（缺省=默认账户），
    重算仅影响该账户内该股票的持仓。
    """
    data = request.get_json(silent=True) or {}
    trade_type = data.get('trade_type', '').strip()
    # 021AM：dividend_tax=红利补税（差异扣税独立流水，日期与派息日不同）
    if trade_type not in ('buy', 'sell', 'dividend', 'dividend_tax'):
        return jsonify({'success': False, 'message': 'trade_type 必须为 buy/sell/dividend/dividend_tax'}), 400

    price = data.get('price', 0)
    quantity = data.get('quantity', 0)
    amount = data.get('amount') or (float(price) * int(quantity) if quantity else 0)
    trade_date = data.get('trade_date', '')
    notes = data.get('notes', '')
    # 021X：手续费（可选，默认0；买入计入成本，卖出/分红扣减已实现盈亏）
    # 021BK：佣金留空 → 自动估算（按账户匹配券商费率，隔天交割单出来后可改实际值）
    raw_commission = data.get('commission')
    commission_explicit = raw_commission is not None and str(raw_commission).strip() != ''
    try:
        commission = float(raw_commission or 0)
    except (TypeError, ValueError):
        return jsonify({'success': False, 'message': 'commission 必须是数字'}), 400
    if commission < 0:
        return jsonify({'success': False, 'message': '手续费不能为负数'}), 400
    commission_estimated = 0

    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')  # 加锁，防止并发冲突

        # 解析目标账户（显式指定 > 默认账户），并校验存在性
        account_id = data.get('account_id')
        if account_id is not None:
            try:
                account_id = int(account_id)
            except (TypeError, ValueError):
                conn.rollback()
                conn.close()
                return jsonify({'success': False, 'message': f'非法 account_id: {account_id}'}), 400
            if not _account_exists(cursor, account_id):
                conn.rollback()
                conn.close()
                return jsonify({'success': False, 'message': '账户不存在'}), 404
        else:
            account_id = _get_default_account_id(cursor)
            if account_id is None:
                conn.rollback()
                conn.close()
                return jsonify({'success': False, 'message': '系统无可用账户，请先创建'}), 500

        # 021BK：佣金留空 → 按账户券商费率自动估算（A股：佣金+印花税(卖)+过户费）
        if not commission_explicit and trade_type in ('buy', 'sell'):
            try:
                from modules.trade_fees import estimate_trade_fee

                cursor.execute('SELECT name FROM accounts WHERE id = ?', (account_id,))
                acc_row = cursor.fetchone()
                cursor.execute('SELECT market FROM stocks WHERE id = ?', (stock_id,))
                stock_row = cursor.fetchone()
                commission = estimate_trade_fee(
                    trade_type, amount,
                    account_name=(acc_row['name'] if acc_row else None),
                    market=(stock_row['market'] if stock_row else 'a_stock'),
                )
                commission_estimated = 1 if commission > 0 else 0
            except Exception:  # noqa: BLE001 — 估算失败不影响录入（回落为 0）
                commission = 0.0
                commission_estimated = 0

        # 获取本账户内该股票的 holding_id（如果持仓存在）
        cursor.execute(
            'SELECT id FROM holdings WHERE stock_id = ? AND account_id = ?',
            (stock_id, account_id),
        )
        h = cursor.fetchone()
        holding_id = h['id'] if h else None

        cursor.execute(
            """
            INSERT INTO trade_records (holding_id, account_id, stock_id, trade_type, price, quantity, amount, commission, commission_estimated, trade_date, notes)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (holding_id, account_id, stock_id, trade_type, price, quantity, amount, commission, commission_estimated, trade_date, notes),
        )

        trade_id = cursor.lastrowid

        # 触发持仓重算（仅本账户）
        recalculated = _recalculate_holding(cursor, stock_id, account_id)

        conn.commit()
        conn.close()
        return jsonify(
            {'success': True, 'trade_id': trade_id, 'recalculated_position': recalculated,
             'commission': commission, 'commission_estimated': bool(commission_estimated)}
        )
    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'新增流水失败（已回滚）：{e}'}), 500


@bp.route('/api/portfolio/trades/<int:trade_id>', methods=['PUT'])
def api_update_trade(trade_id):
    """编辑交易流水（触发持仓重算，事务保护）。
    Body 可包含：trade_type, price, quantity, amount, trade_date, notes, stock_id(跨股票编辑)
    """
    data = request.get_json(silent=True) or {}

    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')

        # 操作限制检查（已清算 / T+1锁定 / 大额二次验证）
        allowed, err_msg, status_code = _check_trade_edit_restriction(
            cursor, trade_id, 'edit', force_confirm=data.get('force_confirm', False)
        )
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
        # 021S：账户归属变更（旧流水 account_id 为 NULL 时归入默认账户）
        old_account_id = (
            trade['account_id']
            if trade['account_id'] is not None
            else _get_default_account_id(cursor)
        )
        new_account_id = data.get('account_id', old_account_id)
        if new_account_id is not None and int(new_account_id) != int(old_account_id):
            if not _account_exists(cursor, int(new_account_id)):
                conn.rollback()
                conn.close()
                return jsonify({'success': False, 'message': '目标账户不存在'}), 404
            new_account_id = int(new_account_id)
        elif new_account_id is not None:
            new_account_id = int(new_account_id)

        # 动态构建 UPDATE 语句（account_id 单独处理，不走通用列循环）
        fields = []
        params = []
        for col in ['trade_type', 'price', 'quantity', 'amount', 'commission', 'trade_date', 'notes', 'stock_id']:
            if col in data:
                if col == 'commission':
                    # 021X：手续费校验（非负数字）
                    try:
                        c = float(data['commission'] or 0)
                    except (TypeError, ValueError):
                        conn.rollback()
                        conn.close()
                        return jsonify({'success': False, 'message': 'commission 必须是数字'}), 400
                    if c < 0:
                        conn.rollback()
                        conn.close()
                        return jsonify({'success': False, 'message': '手续费不能为负数'}), 400
                fields.append(f'{col} = ?')
                params.append(data[col])
                if col == 'commission':
                    # 021BK：手工填写佣金即视为实际值，清除估算标记
                    fields.append('commission_estimated = 0')

        if fields:
            # 校验 trade_type
            if 'trade_type' in data and data['trade_type'] not in (
                'buy', 'sell', 'dividend', 'dividend_tax',
            ):
                conn.rollback()
                conn.close()
                return jsonify(
                    {'success': False, 'message': 'trade_type 必须为 buy/sell/dividend'}
                ), 400

            # 如果股票或账户变了，更新 holding_id 指向（021S：按 账户+股票 定位）
            if new_stock_id != old_stock_id or new_account_id != old_account_id:
                cursor.execute(
                    'SELECT id FROM holdings WHERE stock_id=? AND account_id=?',
                    (new_stock_id, new_account_id),
                )
                new_h = cursor.fetchone()
                new_holding_id = new_h['id'] if new_h else None
                fields.append('holding_id = ?')
                params.append(new_holding_id)
                fields.append('account_id = ?')
                params.append(new_account_id)

            params.append(trade_id)
            cursor.execute(f'UPDATE trade_records SET {", ".join(fields)} WHERE id=?', params)

            # 重算新归属（股票, 账户）持仓
            recalculated = _recalculate_holding(cursor, new_stock_id, new_account_id)

            # 如果跨股票/跨账户编辑，还要重算原归属持仓
            if new_stock_id != old_stock_id or new_account_id != old_account_id:
                recalculated_old = _recalculate_holding(cursor, old_stock_id, old_account_id)
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
            recalculated = _recalculate_holding(cursor, old_stock_id, old_account_id)

        conn.commit()
        conn.close()
        return jsonify({'success': True, 'recalculated_position': recalculated})

    except Exception as e:
        conn.rollback()
        conn.close()
        return jsonify({'success': False, 'message': f'编辑流水失败（已回滚）：{e}'}), 500


@bp.route('/api/portfolio/trades/<int:trade_id>', methods=['DELETE'])
def api_delete_trade(trade_id):
    """删除交易流水（触发持仓重算，事务保护）。
    Body 可包含：force_confirm(bool) —— 单笔超 5 万流水删除需二次验证
    """
    data = request.get_json(silent=True) or {}
    conn = get_connection()
    cursor = conn.cursor()
    try:
        conn.execute('BEGIN IMMEDIATE')

        # 操作限制检查（已清算 / T+1锁定 / 大额二次验证）
        allowed, err_msg, status_code = _check_trade_edit_restriction(
            cursor, trade_id, 'delete', force_confirm=data.get('force_confirm', False)
        )
        if not allowed:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': err_msg}), status_code

        cursor.execute('SELECT stock_id, account_id FROM trade_records WHERE id=?', (trade_id,))
        trade = cursor.fetchone()
        if not trade:
            conn.rollback()
            conn.close()
            return jsonify({'success': False, 'message': '流水记录不存在'}), 404

        stock_id = trade['stock_id']
        # 021S：旧流水 account_id 为 NULL 时按默认账户重算
        account_id = (
            trade['account_id']
            if trade['account_id'] is not None
            else _get_default_account_id(cursor)
        )
        cursor.execute('DELETE FROM trade_records WHERE id=?', (trade_id,))

        recalculated = _recalculate_holding(cursor, stock_id, account_id)

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


def _check_trade_edit_restriction(cursor, trade_id, operation='edit', force_confirm=False):
    """检查流水编辑/删除限制，返回 (allowed, error_msg, status_code)。
    限制规则：
    1. 已清算（持仓数量=0）的股票，禁止编辑/删除其历史流水
    2. T+1锁定：当日提交的流水次日才允许修改
    3. 单笔流水金额超过 TRADE_AMOUNT_VERIFY_THRESHOLD 时需二次验证
       （force_confirm=true 放行，与成本调整二次确认模式一致）
    """
    cursor.execute('SELECT * FROM trade_records WHERE id=?', (trade_id,))
    trade = cursor.fetchone()
    if not trade:
        return False, '流水记录不存在', 404

    stock_id = trade['stock_id']
    # 021S：清算状态按（股票, 账户）维度判断；旧流水无账户归属时按默认账户
    account_id = (
        trade['account_id']
        if trade['account_id'] is not None
        else _get_default_account_id(cursor)
    )

    # 1. 检查持仓是否已清算（仅本账户）
    cursor.execute(
        'SELECT quantity, status FROM holdings WHERE stock_id=? AND account_id=?',
        (stock_id, account_id),
    )
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

    # 3. 单笔大额流水二次验证（force_confirm 放行）
    amount = trade['amount'] or 0
    if amount > TRADE_AMOUNT_VERIFY_THRESHOLD:
        if not force_confirm:
            return (
                False,
                f'单笔流水金额 {amount:,.0f} 元超过 {TRADE_AMOUNT_VERIFY_THRESHOLD:,.0f} 元阈值，'
                '需二次验证（force_confirm=true 确认后放行）',
                403,
            )

    return True, None, 200


@bp.route('/api/positions/<int:holding_id>/cost-adjustment', methods=['POST'])
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


@bp.route('/api/portfolio/holdings/<int:stock_id>/trade-suggestion', methods=['GET'])
def api_trade_suggestion(stock_id):
    """获取近3次同股票买入记录，用于预填推荐。
    021S：可选 ?account_id= 按账户过滤（缺省=全部账户）。
    返回 avg_price, avg_quantity, count, latest_trade_date
    """
    try:
        account_id = _parse_account_scope(request.args.get('account_id'))
    except ValueError as e:
        return jsonify({'success': False, 'message': str(e)}), 400

    conn = get_connection()
    cursor = conn.cursor()
    sql = """
        SELECT price, quantity, trade_date
        FROM trade_records
        WHERE stock_id=? AND trade_type='buy'
    """
    params = [stock_id]
    if account_id is not None:
        sql += ' AND account_id = ?'
        params.append(account_id)
    sql += ' ORDER BY trade_date DESC, created_at DESC LIMIT 3'
    cursor.execute(sql, params)
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


@bp.route('/api/analytics/prefill', methods=['POST'])
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


def _fetch_realtime_price_batch(symbols_markets):
    """批量获取实时行情价格（腾讯接口）。
    symbols_markets: [(stock_id, symbol, market), ...]
    返回: {stock_id: {'price': float, 'pct_change': float}}
    019Y T1：腾讯主源缺失的 A股 自动降级 mootdx（通达信行情，TCP socket，不经过 requests patch）。
    """
    import logging as _logging019y

    import requests as _requests

    from modules.data_collector import _normalize_hk_symbol

    _log019y = _logging019y.getLogger(__name__)

    result = {}
    if not symbols_markets:
        return result

    # 构造腾讯批量请求代码：sh600000,sz000001,hk00700
    tencent_codes = []
    stock_id_map = {}
    for stock_id, symbol, market in symbols_markets:
        if market == 'hk_stock':
            # 港股：库内 HK3690 形态 → 腾讯 hk03690（剥离 HK 前缀+左补零至5位）
            # 021M 修复：原 symbol.zfill(5) 对带前缀代码（'HK3690' 已6字符）无效，
            # 生成 hkHK3690 错误代码 → 腾讯返回空 → 港股全部降级写入昨收（实测
            # 2026-08-18：cache 价=08-17 收盘 88.0，实时价实为 84.95）
            hk_code = _normalize_hk_symbol(symbol)
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

    # 019Y T1：腾讯主源缺失的 A股 → mootdx 降级（每只一次 socket 查询，量小）
    missing_a = [
        (stock_id, symbol)
        for stock_id, symbol, market in symbols_markets
        if market == 'a_stock' and stock_id not in result
    ]
    if missing_a:
        try:
            from modules.data_collector import get_realtime_quote_mootdx

            for stock_id, symbol in missing_a:
                q = get_realtime_quote_mootdx(symbol)
                if q and q.get('price') and q['price'] > 0:
                    result[stock_id] = {'price': q['price'], 'pct_change': q.get('pct_change')}
                    _log019y.info(
                        f'[019Y] {symbol} 实时价格走 mootdx 降级: price={q["price"]}'
                    )
        except Exception as e:
            _log019y.warning(f'[019Y] mootdx 实时价格降级失败: {e}')

    return result


@bp.route('/api/portfolio/refresh-prices', methods=['POST'])
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
