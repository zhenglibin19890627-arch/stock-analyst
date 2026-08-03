"""
B18-Hotfix 评分引擎校准验证脚本

功能：
1. 对全部自选股执行最新 v5.0 评分
2. 输出评分分布直方图（0-29/30-49/50-64/65-79/80+）
3. 输出四维均分 + 总分均分
4. 对比校准前后差异（从 ratings_history 读取旧数据）

B18-Hotfix 变更：
- rating_mapping: strong_buy 85→80, buy 70→65
- 分箱边界同步调整为 80/65/50/30

验收标准：
- 70+ 股票占比 ≥ 20%
- 评分区间跨度 ≥ 40 分
- 四维均分在 50~65 区间
"""

import logging
import os
import sys
from collections import Counter

# 确保项目根目录在 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from database.db_manager import get_connection, init_database
from modules.scoring_engine import analyze_from_db

logging.basicConfig(level=logging.WARNING, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def get_all_stocks():
    """获取所有自选股"""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('SELECT id, symbol, name, market FROM stocks WHERE status="active" ORDER BY id')
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def get_old_ratings():
    """从 ratings_history 获取最近一次评分（校准前数据）"""
    conn = get_connection()
    cursor = conn.cursor()
    # 获取每只股票最近一次评分
    cursor.execute("""
        SELECT rh.stock_id, s.symbol, s.name, rh.total_score, rh.rating, rh.rating_date
        FROM ratings_history rh
        JOIN stocks s ON s.id = rh.stock_id
        WHERE rh.rating_date = (
            SELECT MAX(rh2.rating_date) FROM ratings_history rh2 WHERE rh2.stock_id = rh.stock_id
        )
        ORDER BY rh.stock_id
    """)
    rows = [dict(r) for r in cursor.fetchall()]
    conn.close()
    return rows


def print_separator(title):
    print(f'\n{"=" * 70}')
    print(f'  {title}')
    print(f'{"=" * 70}')


def print_distribution(scores, title):
    """打印评分分布直方图（B18-Hotfix: 80/65/50/30 边界）"""
    bins = {
        '0-29 (强烈建议卖出)': 0,
        '30-49 (建议减仓)': 0,
        '50-64 (持有观望)': 0,
        '65-79 (推荐买入)': 0,
        '80+ (强烈推荐买入)': 0,
    }
    for s in scores:
        if s < 30:
            bins['0-29 (强烈建议卖出)'] += 1
        elif s < 50:
            bins['30-49 (建议减仓)'] += 1
        elif s < 65:
            bins['50-64 (持有观望)'] += 1
        elif s < 80:
            bins['65-79 (推荐买入)'] += 1
        else:
            bins['80+ (强烈推荐买入)'] += 1

    print(f'\n--- {title} ---')
    total = len(scores)
    max_bar_width = 30
    for label, count in bins.items():
        pct = count / total * 100 if total > 0 else 0
        bar = '█' * min(int(pct * max_bar_width / 100), max_bar_width)
        print(f'  {label:<28s}: {count:>3d} ({pct:>5.1f}%)  {bar}')
    print(f'  {"总计":<28s}: {total:>3d}')


def main():
    init_database()

    # ================================================================
    # 1. 校准后评分（v5.0 引擎）
    # ================================================================
    stocks = get_all_stocks()
    print_separator(f'B18-Hotfix 评分引擎校准验证 — 共 {len(stocks)} 只自选股')

    new_scores = []
    new_dim_scores = {'kline': [], 'fundamental': [], 'news': [], 'capital_flow': []}
    new_ratings = []
    failures = []
    stock_details = []

    for i, stock in enumerate(stocks):
        stock_id = stock['id']
        symbol = stock['symbol']
        name = stock['name']
        try:
            result = analyze_from_db(stock_id)
            if result is None:
                failures.append(f'{symbol} {name}: 数据不足')
                continue

            ts = result.total_score
            new_scores.append(ts)
            new_ratings.append(result.rating)

            # 收集四维得分
            for dim_key, attr in [
                ('kline', 'technical_score'),
                ('fundamental', 'fundamental_score'),
                ('news', 'sentiment_score'),
                ('capital_flow', 'capital_score'),
            ]:
                val = getattr(result, attr, None)
                if val is not None:
                    new_dim_scores[dim_key].append(val)

            stock_details.append(
                {
                    'symbol': symbol,
                    'name': name,
                    'total_score': ts,
                    'rating': result.rating,
                    'technical': result.technical_score,
                    'fundamental': result.fundamental_score,
                    'news': result.sentiment_score,
                    'capital': result.capital_score,
                }
            )
            print(
                f'  [{i + 1:>2}/{len(stocks)}] {symbol} {name:<8s} 总分={ts:>5.1f}  评级={result.rating}'
            )

        except Exception as e:
            failures.append(f'{symbol} {name}: {e}')
            logger.error(f'评分失败: {symbol} {name}: {e}')

    # ================================================================
    # 2. 校准后统计
    # ================================================================
    if new_scores:
        print_separator('校准后评分分布')
        print_distribution(new_scores, '最新评分分布')

        print('\n--- 校准后关键指标 ---')
        print(
            f'  评分区间: {min(new_scores):.1f} ~ {max(new_scores):.1f} '
            f'(跨度={max(new_scores) - min(new_scores):.1f}分)'
        )
        print(f'  总分均分: {sum(new_scores) / len(new_scores):.1f}')

        dim_cn = {
            'kline': '技术面',
            'fundamental': '基本面',
            'news': '消息面',
            'capital_flow': '资金面',
        }
        print('  四维均分:')
        for dk in ['kline', 'fundamental', 'capital_flow', 'news']:
            vals = new_dim_scores[dk]
            if vals:
                avg = sum(vals) / len(vals)
                print(f'    {dim_cn[dk]:<6s}: {avg:>5.1f}  (n={len(vals)})')

        # 评级覆盖
        rating_counts = Counter(new_ratings)
        print(f'\n  评级覆盖: {len(rating_counts)} 档')
        for rating in ['强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出']:
            cnt = rating_counts.get(rating, 0)
            print(f'    {rating}: {cnt}')

        over_65 = sum(1 for s in new_scores if s >= 65)
        print(f'\n  65+ 股票: {over_65}/{len(new_scores)} ({over_65 / len(new_scores) * 100:.1f}%)')

    # ================================================================
    # 3. 校准前数据（ratings_history）
    # ================================================================
    old_ratings = get_old_ratings()
    if old_ratings:
        old_scores = [r['total_score'] for r in old_ratings if r['total_score'] is not None]
        print_separator('校准前评分分布（ratings_history 最新记录）')
        print_distribution(old_scores, '校准前评分分布')

        if old_scores:
            print('\n--- 校准前关键指标 ---')
            print(
                f'  评分区间: {min(old_scores):.1f} ~ {max(old_scores):.1f} '
                f'(跨度={max(old_scores) - min(old_scores):.1f}分)'
            )
            print(f'  总分均分: {sum(old_scores) / len(old_scores):.1f}')
            old_over_65 = sum(1 for s in old_scores if s >= 65)
            print(
                f'  65+ 股票: {old_over_65}/{len(old_scores)} ({old_over_65 / len(old_scores) * 100:.1f}%)'
            )

    # ================================================================
    # 4. 校准前后对比
    # ================================================================
    if new_scores and old_scores:
        print_separator('校准前后对比')
        print(f'  {"指标":<20s} {"校准前":>10s} {"校准后":>10s} {"变化":>10s}')
        print(f'  {"-" * 50}')
        print(
            f'  {"评分区间跨度":<20s} {max(old_scores) - min(old_scores):>9.1f}分 '
            f'{max(new_scores) - min(new_scores):>9.1f}分 '
            f'{(max(new_scores) - min(new_scores)) - (max(old_scores) - min(old_scores)):>+9.1f}分'
        )
        print(
            f'  {"总分均分":<20s} {sum(old_scores) / len(old_scores):>10.1f} '
            f'{sum(new_scores) / len(new_scores):>10.1f} '
            f'{sum(new_scores) / len(new_scores) - sum(old_scores) / len(old_scores):>+10.1f}'
        )
        old_65 = sum(1 for s in old_scores if s >= 65) / len(old_scores) * 100
        new_65 = sum(1 for s in new_scores if s >= 65) / len(new_scores) * 100
        print(f'  {"65+占比":<20s} {old_65:>9.1f}% {new_65:>9.1f}% {new_65 - old_65:>+9.1f}%')

    # ================================================================
    # 5. 验收判定
    # ================================================================
    print_separator('验收判定')
    checks = []

    if new_scores:
        span = max(new_scores) - min(new_scores)
        check1 = span >= 40
        checks.append(('评分区间跨度 ≥ 40分', check1, f'{span:.1f}分'))

        over_65_pct = sum(1 for s in new_scores if s >= 65) / len(new_scores) * 100
        check2 = over_65_pct >= 20
        checks.append(('65+股票占比 ≥ 20%', check2, f'{over_65_pct:.1f}%'))

        dim_ok = True
        for dk in ['kline', 'fundamental', 'capital_flow', 'news']:
            vals = new_dim_scores[dk]
            if vals:
                avg = sum(vals) / len(vals)
                in_range = 50 <= avg <= 65
                if not in_range:
                    dim_ok = False
                checks.append((f'{dim_cn[dk]}均分 50~65', in_range, f'{avg:.1f}'))

        check3 = dim_ok
        checks.append(('四维均分 50~65', check3, ''))

        rating_coverage = len(set(new_ratings))
        check4 = rating_coverage >= 3
        checks.append(('评级覆盖 ≥ 3档', check4, f'{rating_coverage}档'))

    for label, passed, detail in checks:
        status = 'PASS' if passed else 'FAIL'
        info = f' ({detail})' if detail else ''
        print(f'  [{status}] {label}{info}')

    all_pass = all(c[1] for c in checks) if checks else False
    print(f'\n  综合判定: {"PASS - 全部通过" if all_pass else "FAIL - 存在未达标项"}')

    # ================================================================
    # 6. 失败列表
    # ================================================================
    if failures:
        print_separator('评分失败')
        for f in failures:
            print(f'  - {f}')

    return all_pass


if __name__ == '__main__':
    success = main()
    sys.exit(0 if success else 1)
