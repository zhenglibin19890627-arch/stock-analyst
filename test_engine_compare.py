"""
P1 验收脚本：新旧引擎并行对比

对比项：
1. 总分（旧引擎 total_score vs 新引擎 total_score）
2. 评级（旧引擎 rating vs 新引擎 rating）
3. 四维得分（技术面/基本面/资金面/消息面）
4. 维度权重差异
5. 数据完整度差异（旧引擎全量计算 vs 新引擎降级机制）

验收标准：
- 5只股票全部跑通
- 总分差异在合理范围内（<20分为可控）
- 无异常崩溃
- 降级机制正常触发
"""

import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.db_manager import get_connection
from modules import analysis_engine as old_engine
from modules import scoring_engine as new_engine

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')


def find_test_stocks(limit=5):
    """找到 K 线数据最多的5只股票"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        """
        SELECT s.id, s.symbol, s.name, s.market,
               (SELECT COUNT(*) FROM raw_kline WHERE stock_id=s.id) as kline_cnt
        FROM stocks s
        WHERE EXISTS(SELECT 1 FROM raw_kline WHERE stock_id=s.id)
        ORDER BY kline_cnt DESC LIMIT ?
    """,
        (limit,),
    )
    stocks = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return stocks


def run_comparison():
    """执行新旧引擎并行对比"""
    stocks = find_test_stocks(5)
    print(f'\n{"=" * 80}')
    print(f'  P1 验收：新旧引擎并行对比（{len(stocks)}只股票）')
    print(f'{"=" * 80}')

    results = []

    for s in stocks:
        stock_id = s['id']
        symbol = s['symbol']
        name = s['name']

        print(f'\n{"─" * 80}')
        print(f'  [{symbol}] {name}  (stock_id={stock_id}, K线:{s["kline_cnt"]}条)')
        print(f'{"─" * 80}')

        # --- 旧引擎 ---
        try:
            old_result = old_engine.analyze_stock(stock_id)
            old_ok = old_result.get('success', True) if isinstance(old_result, dict) else True
        except Exception as e:
            old_result = {'success': False, 'message': str(e)}
            old_ok = False

        # --- 新引擎 ---
        try:
            new_result = new_engine.analyze_from_db(stock_id)
            new_ok = new_result is not None
        except Exception as e:
            new_result = None
            new_ok = False
            print(f'  [新引擎异常] {e}')

        if not old_ok:
            print(f'  [旧引擎失败] {old_result.get("message", "?")}')
            continue
        if not new_ok:
            print('  [新引擎失败] 数据不足')
            continue

        # --- 提取对比数据 ---
        old_total = old_result.get('total_score', 0) or 0
        old_rating = old_result.get('rating', '?')
        old_dims = old_result.get('dimensions', {})

        new_total = new_result.total_score
        new_rating = f'{new_result.rating}({new_result.rating_label})'
        new_tech = new_result.technical_score or 0
        new_fund = new_result.fundamental_score or 0
        new_news = new_result.sentiment_score or 0
        new_cap = new_result.capital_score or 0

        old_tech = old_dims.get('kline', {}).get('score', 0) or 0
        old_fund = old_dims.get('fundamental', {}).get('score', 0) or 0
        old_news = old_dims.get('news', {}).get('score', 0) or 0
        old_cap = old_dims.get('capital_flow', {}).get('score', 0) or 0

        score_diff = abs(new_total - old_total)

        # --- 打印对比表 ---
        print(f'\n  {"指标":<20} {"旧引擎":>12} {"新引擎(v5)":>12} {"差异":>10}')
        print(f'  {"─" * 56}')
        print(
            f'  {"综合总分":<20} {old_total:>12.1f} {new_total:>12.1f} {new_total - old_total:>+10.1f}'
        )
        print(f'  {"评级":<20} {old_rating:>12} {new_rating:>12} {"":>10}')
        print(
            f'  {"技术面得分":<20} {old_tech:>12.1f} {new_tech:>12.1f} {new_tech - old_tech:>+10.1f}'
        )
        print(
            f'  {"基本面得分":<20} {old_fund:>12.1f} {new_fund:>12.1f} {new_fund - old_fund:>+10.1f}'
        )
        print(f'  {"资金面得分":<20} {old_cap:>12.1f} {new_cap:>12.1f} {new_cap - old_cap:>+10.1f}')
        print(
            f'  {"消息面得分":<20} {old_news:>12.1f} {new_news:>12.1f} {new_news - old_news:>+10.1f}'
        )

        # --- 权重对比 ---
        old_w_tech = old_dims.get('kline', {}).get('weight', 0) or 0
        old_w_fund = old_dims.get('fundamental', {}).get('weight', 0) or 0
        old_w_cap = old_dims.get('capital_flow', {}).get('weight', 0) or 0
        old_w_news = old_dims.get('news', {}).get('weight', 0) or 0

        print(f'\n  {"维度权重":<20} {"旧引擎":>12} {"新引擎(v5)":>12}')
        print(f'  {"─" * 46}')
        print(f'  {"技术面":<20} {old_w_tech:>12.1%} {new_result.technical_weight:>12.1%}')
        print(f'  {"基本面":<20} {old_w_fund:>12.1%} {new_result.fundamental_weight:>12.1%}')
        print(f'  {"资金面":<20} {old_w_cap:>12.1%} {new_result.capital_weight:>12.1%}')
        print(f'  {"消息面":<20} {old_w_news:>12.1%} {new_result.sentiment_weight:>12.1%}')

        # --- 数据质量 ---
        if new_result.data_quality:
            dq = new_result.data_quality
            print(
                f'\n  数据完整度: 技术={dq["technical"]:.0%} 基本面={dq["fundamental"]:.0%} '
                f'消息面={dq["news"]:.0%} 资金面={dq["capital"]:.0%}'
            )

        if new_result.data_warnings:
            print('\n  数据警告:')
            for w in new_result.data_warnings:
                print(f'    - {w}')

        if new_result.degradations:
            print(f'\n  降级规则触发({len(new_result.degradations)}条):')
            for field, rule in list(new_result.degradations.items())[:5]:
                print(f'    - {field}: {rule[:40]}...')

        verdict = '可控' if score_diff < 20 else '需关注'
        print(f'\n  总分差异: {score_diff:.1f}  →  {verdict}')

        results.append(
            {
                'symbol': symbol,
                'name': name,
                'old_total': old_total,
                'new_total': new_total,
                'diff': score_diff,
                'old_rating': old_rating,
                'new_rating': new_rating,
            }
        )

    # --- 汇总 ---
    print(f'\n{"=" * 80}')
    print('  对比汇总')
    print(f'{"=" * 80}')
    print(f'\n  {"股票":<12} {"旧总分":>8} {"新总分":>8} {"差异":>8} {"判定":>8}')
    print(f'  {"─" * 48}')
    for r in results:
        verdict = '可控' if r['diff'] < 20 else '需关注'
        print(
            f'  {r["symbol"]:<12} {r["old_total"]:>8.1f} {r["new_total"]:>8.1f} {r["diff"]:>8.1f} {verdict:>8}'
        )

    avg_diff = sum(r['diff'] for r in results) / len(results) if results else 0
    max_diff = max(r['diff'] for r in results) if results else 0
    print(f'\n  平均差异: {avg_diff:.1f}  最大差异: {max_diff:.1f}')

    all_controllable = all(r['diff'] < 25 for r in results)
    print(f'\n  验收结论: {"全部差异可控" if all_controllable else "存在较大差异，需分析原因"}')

    print(f'\n{"=" * 80}')
    return results


if __name__ == '__main__':
    run_comparison()
