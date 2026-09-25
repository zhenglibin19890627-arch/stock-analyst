"""回测证据展示域（t3 拆包，021BU）：分级门槛 + 诚实门 + 证据表聚合。

划分依据：021BU「报告页↔看板同源唯一真相」读取面——样本分级门槛常量
（EVIDENCE_N_FULL/PARTIAL，审计脚本 import 同一常量）、诚实门 format_evidence_cell
（数据层断流小样本百分数）、rating_evidence_* 与 price_advice_evidence_summary
（只读 SELECT 零写库）。实现体为原文件 L342-528 逐字节搬移。
"""

from modules.backtest_engine._env import get_connection

# ============================================================
# 二·五、回测证据展示（021BU：报告页↔看板同源唯一真相，读取面现算零落库）
# ============================================================
# 依据：docs/reports/021bu_backtest_evidence_plan_20260923.md（t1 方案 O1/O3/O8）。
# 红线标注：B24（只读展示函数，不触碰 generate_advice）；R7（证据只展示不回灌，
# 不参与评级判定）；R20（A/H 分市场查询）；诚实原则（C 级在数据层断流百分数）。

# 样本分级门槛（诚实原则，t1 方案 §1；审计脚本 import 同一常量，不另设第二真相）：
# A 级 n≥30 全展示；B 级 20≤n<30 展示带「样本偏小」标注；C 级 n<20 不展示百分数。
EVIDENCE_N_FULL = 30
EVIDENCE_N_PARTIAL = 20


def grade_sample_class(n):
    """样本量分级（021BU 诚实原则）：返回 'A'/'B'/'C'。"""
    if n >= EVIDENCE_N_FULL:
        return 'A'
    if n >= EVIDENCE_N_PARTIAL:
        return 'B'
    return 'C'


def format_evidence_cell(correct, total, label='历史命中'):
    """诚实展示门（021BU，数据层强制——任何消费面都无法泄漏小样本命中率）。

    - A 级（n≥30）/B 级（20≤n<30）：返回 acc + display「{label} xx%（c/n）」；
    - C 级（n<20，含 n=0）：acc=None、display「样本不足（n=…）」——数据层就不给
      百分数（诚实原则的实现锚点；B 级「样本偏小」⚠ 由前端按 grade 渲染）。
    纯函数（不触库），测试锁定 n=19/20/29/30 边界。文案无裸 '<'。
    """
    total = int(total or 0)
    correct = int(correct or 0)
    grade = grade_sample_class(total)
    if total <= 0 or grade == 'C':
        return {
            'grade': 'C', 'n': correct, 'm': total, 'acc': None,
            'display': f'样本不足（n={total}）',
        }
    acc = round(correct / total, 4)
    return {
        'grade': grade, 'n': correct, 'm': total, 'acc': acc,
        'display': f'{label} {acc * 100:.0f}%（{correct}/{total}）',
    }


def empty_rating_evidence(market, rating):
    """零样本档位的诚实证据（与 rating_evidence_for 缺档回退同构，供看板查表复用）。"""
    return {
        'rating': rating,
        'market': market,
        'primary': format_evidence_cell(0, 0),
        'dynamic': None,
        'engine_versions': [],
    }


def rating_evidence_table(market='a_stock'):
    """分档评级证据表（021BU O1 唯一数据源）：真实样本一次聚合，{rating: evidence}。

    口径与回测中心市场报告同源（t1 方案 §1）：
    - 排除模拟行（is_simulated）与 rating_id=-1；自然键 (stock_id, rating_date)
      去重取 max(id)（021BB：ratings_history INSERT OR REPLACE 下 id 不稳定）；
    - 主口径 = is_correct 列（市场报告「分档表现」同列同源）；次行 = 动态窗口
      （dynamic_is_correct 列）；引擎版本构成声明（E-F：只声明不跨引擎对比）。
    只读 SELECT，零写库。同市场调用一次后查表（看板每股消费勿重复调用）。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT br.id, br.stock_id, br.rating_date, br.rating, br.is_correct, '
        'br.dynamic_is_correct, rh.engine_version '
        'FROM backtest_results br '
        'LEFT JOIN ratings_history rh '
        'ON rh.stock_id = br.stock_id AND rh.rating_date = br.rating_date '
        'WHERE br.market = ? '
        'AND (br.is_simulated IS NULL OR br.is_simulated = 0) '
        'AND br.rating_id IS NOT NULL AND br.rating_id != -1 '
        'ORDER BY br.id',
        (market,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    best = {}
    for r in rows:
        key = (r['stock_id'], r['rating_date'])
        if key not in best or r['id'] > best[key]['id']:
            best[key] = r

    agg = {}
    engines = set()
    for r in best.values():
        rating = r['rating']
        cell = agg.setdefault(rating, {'n': 0, 'c': 0, 'dyn_n': 0, 'dyn_c': 0})
        if r['is_correct'] is not None:
            cell['n'] += 1
            if r['is_correct'] == 1:
                cell['c'] += 1
        if r['dynamic_is_correct'] in (0, 1):
            cell['dyn_n'] += 1
            if r['dynamic_is_correct'] == 1:
                cell['dyn_c'] += 1
        if r['engine_version']:
            engines.add(r['engine_version'])

    table = {}
    for rating, cell in agg.items():
        table[rating] = {
            'rating': rating,
            'market': market,
            'primary': format_evidence_cell(cell['c'], cell['n']),
            'dynamic': (
                format_evidence_cell(cell['dyn_c'], cell['dyn_n'], label='动态窗口命中')
                if cell['dyn_n'] else None
            ),
            'engine_versions': sorted(engines),
        }
    return table


def rating_evidence_for(market, rating):
    """评级旁「历史命中徽章」数据源（021BU O1：rating_evidence_for(market, rating)）。

    报告页与看板消费同一函数（同源同值）；缺档/零样本返回诚实空档
    （C 级「样本不足（n=0）」，不返回 None——展示面始终可说明样本现状）。
    rating 为空返回 None（无评级无徽章）。
    """
    if not rating:
        return None
    return rating_evidence_table(market).get(rating) or empty_rating_evidence(market, rating)


def price_advice_evidence_summary(market='a_stock'):
    """价格建议「历史基准」注记（021BU O3）：真实锚点主口径的市场级聚合。

    口径（t1 方案 E-C，全部真实锚点 anchor_rating_date 非空——无未来函数）：
    - 买入区间/目标价 T+20：分母=无持仓建议行（buy_range_low 非空）；
    - 止损 T+20：分母=全部真实锚点行；
    命中判定复用 price_backtest 落库的 t20_hit_* 三值列（1 命中/0 未中/NULL 不可判定）。
    样本门槛与诚实门同 rating_evidence（C 级只显 n）。只读 SELECT，零写库。
    """
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        'SELECT buy_range_low, t20_hit_buy_range, t20_hit_target, t20_hit_stop_loss '
        'FROM price_backtest_results '
        'WHERE market = ? AND anchor_rating_date IS NOT NULL',
        (market,),
    )
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()

    no_position_rows = [r for r in rows if r['buy_range_low'] is not None]

    def _cnt(rs, field):
        n = sum(1 for r in rs if r[field] is not None)
        c = sum(1 for r in rs if r[field] == 1)
        return c, n

    buy = format_evidence_cell(*_cnt(no_position_rows, 't20_hit_buy_range'),
                               label='买入区间 20 日内触及')
    target = format_evidence_cell(*_cnt(no_position_rows, 't20_hit_target'),
                                  label='目标价 20 日内达成')
    stop = format_evidence_cell(*_cnt(rows, 't20_hit_stop_loss'),
                                label='止损 20 日内触发')

    # 组合展示行：C 级段只显样本量（诚实门数据层强制），无裸 '<'
    parts = []
    for cell, name in (
        (buy, '买入区间 20 日内触及率'),
        (target, '目标价达成率'),
        (stop, '止损触发率'),
    ):
        if cell['grade'] == 'C':
            parts.append(f'{name}样本不足（n={cell["m"]}）')
        else:
            parts.append(f'{name} {cell["acc"] * 100:.0f}%（{cell["n"]}/{cell["m"]}）')
    grades = {buy['grade'], target['grade'], stop['grade']}
    return {
        'market': market,
        'grade': 'C' if 'C' in grades else ('B' if 'B' in grades else 'A'),
        'n_real_anchor': len(rows),
        'buy_range_t20': buy,
        'target_t20': target,
        'stop_loss_t20': stop,
        'display': '历史基准（全市场真实评级样本）：' + ' · '.join(parts),
    }
