"""位置注记域（t3 拆包）：个股 60 日位置分位 + 报告页评级位置分化标注。

划分依据：_current_pos_pctile 与 position_note_for（2026-09-18 回测提升②，
「分档×位置矩阵」自动用法）是只读展示聚合，与判定/引擎计算解耦，
供 blueprints/analysis 消费。实现体为原文件 L244-339 逐字节搬移。
"""

from modules.backtest_engine._env import get_connection

# ============================================================
# 三、核心回测引擎
# ============================================================


def _current_pos_pctile(stock_id):
    """股票当前 60 日位置分位（0~1），数据不足返回 None。"""
    conn = get_connection()
    cursor = conn.cursor()
    ks = [r[0] for r in cursor.execute(
        'SELECT close FROM raw_kline WHERE stock_id = ? ORDER BY trade_date DESC LIMIT 60',
        (stock_id,)).fetchall()]
    conn.close()
    if len(ks) < 40:
        return None
    hi, lo, now = max(ks), min(ks), ks[0]
    if hi <= lo:
        return None
    return round((now - lo) / (hi - lo), 3)


def position_note_for(stock_id, rating):
    """报告页评级位置标注（2026-09-18 回测提升②：矩阵用法自动化）。

    用当前位置分位查「分档×位置矩阵」历史胜率，仅在分化可信时输出：
    - 买入档 + 高位带（>70%）：对面（低位）带 n>=10 且分化>=15pp → 追高警示
    - 减仓档 + 低位带（<40%）：对面（高位）带 n>=10 且分化>=15pp → 错杀提示
    中位带/观望档/样本不足一律返回 None（不硬造结论）。
    """
    if rating not in ('推荐买入', '强烈推荐买入', '建议减仓', '强烈建议卖出'):
        return None
    pos = _current_pos_pctile(stock_id)
    if pos is None:
        return None
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT market FROM stocks WHERE id = ?', (stock_id,))
    row = cursor.fetchone()
    market = row['market'] if row else 'a_stock'
    cursor.execute(
        'SELECT rating, pos_pctile, dynamic_is_correct FROM backtest_results '
        'WHERE market = ? AND (is_simulated IS NULL OR is_simulated = 0) '
        'AND pos_pctile IS NOT NULL AND dynamic_is_correct IN (0, 1)',
        (market,),
    )
    rows = cursor.fetchall()
    conn.close()

    own_key = 'low' if pos < 0.4 else ('high' if pos >= 0.7 else 'mid')
    if own_key == 'mid':
        return None  # 中位带不标（分化不显著）
    n_own = n_opp = c_own = c_opp = 0
    for r in rows:
        if r['rating'] != rating:
            continue
        p = r['pos_pctile']
        if 0.0 <= p < 0.4:
            n_own += 1 if own_key == 'low' else 0
            c_own += (r['dynamic_is_correct'] == 1) if own_key == 'low' else 0
            n_opp += 1 if own_key != 'low' else 0
            c_opp += (r['dynamic_is_correct'] == 1) if own_key != 'low' else 0
        elif 0.7 <= p < 1.01:
            n_own += 1 if own_key == 'high' else 0
            c_own += (r['dynamic_is_correct'] == 1) if own_key == 'high' else 0
            n_opp += 1 if own_key != 'high' else 0
            c_opp += (r['dynamic_is_correct'] == 1) if own_key != 'high' else 0
    if n_own < 10 or n_opp < 10:
        return None
    acc_own, acc_opp = c_own / n_own, c_opp / n_opp
    if abs(acc_own - acc_opp) < 0.15:
        return None

    is_buy = rating in ('推荐买入', '强烈推荐买入')
    if is_buy and own_key == 'high':
        text = (f'⚠️ 追高提示：当前股价处近60日高位（{pos * 100:.0f}%分位），'
                f'历史「推荐买入」在高位票胜率仅 {acc_own * 100:.0f}%（{c_own}/{n_own}），'
                f'低位票 {acc_opp * 100:.0f}%——可等回调，勿重仓追。')
    elif is_buy:
        text = (f'✓ 底部提示：当前股价处近60日低位（{pos * 100:.0f}%分位），'
                f'历史「推荐买入」在低位票胜率 {acc_own * 100:.0f}%（{c_own}/{n_own}），'
                f'高位票仅 {acc_opp * 100:.0f}%。')
    elif own_key == 'low':
        text = (f'ℹ️ 错杀提示：当前股价处近60日低位（{pos * 100:.0f}%分位），'
                f'历史「{rating}」在低位票胜率仅 {acc_own * 100:.0f}%（{c_own}/{n_own}），'
                f'高位票 {acc_opp * 100:.0f}%——低位减仓错杀概率不低，结合持仓成本再定。')
    else:
        text = (f'⚠️ 出货提示：当前股价处近60日高位（{pos * 100:.0f}%分位），'
                f'历史「{rating}」在高位票胜率 {acc_own * 100:.0f}%（{c_own}/{n_own}），'
                f'低位票仅 {acc_opp * 100:.0f}%。')
    return {
        'pos_pctile': pos,
        'band': own_key,
        'history_acc': round(acc_own, 4),
        'history_n': n_own,
        'text': text,
    }
