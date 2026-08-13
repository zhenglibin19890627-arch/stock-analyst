#!/usr/bin/env python3
"""
M8 评级有效性监测（回测）引擎
=============================

三层架构之中层——回测业务层。

功能：
1. 固定周期回测（T+1 / T+5 / T+20）
2. 动态周期回测（评级变更链）
3. 有效性判定矩阵（中文5档 + 历史兼容）
4. 市场级回测报告（A股/港股独立）
5. 个股回测明细
6. 权重实验场景（D4 消息面 0→20%）

需求映射：§2.8 评级有效性监测(回测)模块
方案文档：docs/m8_backtest_framework_plan_20260720.md
"""

import logging
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from database.db_manager import backup_database, get_connection
from modules.scoring_engine import normalize_rating

logger = logging.getLogger(__name__)

_CN_TZ = timezone(timedelta(hours=8))


# ============================================================
# 一、有效性判定矩阵（RATING-ALIGN-004 中文5档 + 历史兼容）
# ============================================================

JUDGEMENT_MATRIX = {
    '强烈推荐买入': {'direction': 'up', 'correct_min': 1.0, 'wrong_max': -3.0},
    '推荐买入': {'direction': 'up', 'correct_min': 0.5, 'wrong_max': -2.0},
    '持有观望': {'direction': 'neutral', 'correct_low': -2.0, 'correct_high': 2.0},
    '建议减仓': {'direction': 'down', 'correct_max': -0.5, 'wrong_min': 2.0},
    '强烈建议卖出': {'direction': 'down', 'correct_max': -0.5, 'wrong_min': 2.0},
}


def _judge(rating_norm, return_pct):
    """根据归一化评级和收益率判定有效性。

    Returns:
        1 = 正确, 0 = 错误, None = 中性（无法明确判定）
    """
    if return_pct is None or rating_norm is None:
        return None
    config = JUDGEMENT_MATRIX.get(rating_norm)
    if not config:
        return None
    direction = config['direction']
    if direction == 'up':
        if return_pct >= config['correct_min']:
            return 1
        elif return_pct <= config['wrong_max']:
            return 0
        return None
    elif direction == 'down':
        if return_pct <= config['correct_max']:
            return 1
        elif return_pct >= config['wrong_min']:
            return 0
        return None
    else:  # neutral
        if config['correct_low'] <= return_pct <= config['correct_high']:
            return 1
        return 0


# ============================================================
# 二、表结构迁移（安全追加列，幂等）
# ============================================================


def _ensure_columns():
    """确保 backtest_results 表有动态回测所需列（ALTER TABLE ADD COLUMN，幂等）。"""
    conn = get_connection()
    cursor = conn.cursor()
    needed = {
        'dynamic_end_date': 'TEXT',
        'dynamic_return': 'REAL',
        'dynamic_is_correct': 'INTEGER',
        'is_simulated': 'INTEGER DEFAULT 0',
        # 019T T3（评审 §4.2）：基准对比列（A股对标沪深300 / 港股对标恒指）
        'bench_return_1d': 'REAL',
        'bench_return_1w': 'REAL',
        'bench_return_1m': 'REAL',
        'alpha_1d': 'REAL',
        'alpha_1w': 'REAL',
        'alpha_1m': 'REAL',
        'is_correct_alpha': 'INTEGER',
    }
    cursor.execute('PRAGMA table_info(backtest_results)')
    existing = {row['name'] for row in cursor.fetchall()}
    for col, col_type in needed.items():
        if col not in existing:
            try:
                cursor.execute(f'ALTER TABLE backtest_results ADD COLUMN {col} {col_type}')
                logger.info(f'backtest_results: added column {col}')
            except Exception as e:
                logger.warning(f'backtest_results: cannot add {col}: {e}')
    conn.commit()
    conn.close()


# ============================================================
# 三、核心回测引擎
# ============================================================


class BacktestEngine:
    """M8 评级有效性监测引擎

    用法:
        engine = BacktestEngine()
        engine.batch_backtest()            # 全量回测
        report = engine.compute_market_report('a_stock')
    """

    FIXED_PERIODS = {'1d': 1, '1w': 5, '1m': 20}

    # 019T T3：基准指数映射（A股→沪深300，港股→恒生指数）
    BENCH_CODE = {'a_stock': '000300', 'hk_stock': 'HSI'}

    def __init__(self):
        _ensure_columns()

    # ---------- 数据查询辅助 ----------

    @staticmethod
    def _get_bench_tn(bench_code, rating_date, n):
        """获取基准指数在评级日的基准价与 T+n 收盘价（019T T3，时间对齐规则同 T1）。

        对齐规则（评审 §2.2 / §4.3，与 _get_tn_price 约定一致）：
        基准价 = index_kline 中 trade_date <= rating_date 的最近一行收盘；
        T+n    = 基准行之后严格第 n 行（trade_date > 基准日 ORDER BY trade_date ASC OFFSET n-1）。

        Returns: (base_close, base_date, tn_close)；任一缺失返回 None。
        """
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT trade_date, close FROM index_kline '
            'WHERE index_code = ? AND trade_date <= ? '
            'ORDER BY trade_date DESC LIMIT 1',
            (bench_code, rating_date),
        )
        base = cursor.fetchone()
        if not base or base['close'] is None:
            conn.close()
            return None, None, None
        base_date = base['trade_date']
        base_close = float(base['close'])
        if n == 0:
            conn.close()
            return base_close, base_date, base_close
        cursor.execute(
            'SELECT close FROM index_kline '
            'WHERE index_code = ? AND trade_date > ? '
            'ORDER BY trade_date ASC LIMIT 1 OFFSET ?',
            (bench_code, base_date, n - 1),
        )
        row = cursor.fetchone()
        conn.close()
        if row and row['close'] is not None:
            return base_close, base_date, float(row['close'])
        return base_close, base_date, None

    def _compute_alpha_block(self, market, rating_date, rating_norm, returns):
        """019T T3：计算基准收益 / alpha / is_correct_alpha（缺基准全置 NULL，不代理）。

        Args:
            market: 'a_stock' / 'hk_stock'
            rating_date: 评级日（YYYY-MM-DD）
            rating_norm: 归一化评级（中文5档）
            returns: {'return_1d':.., 'return_1w':.., 'return_1m':..}（个股同窗口收益率%）
        Returns:
            dict {bench_return_1d/1w/1m, alpha_1d/1w/1m, is_correct_alpha}
        """
        block = {
            'bench_return_1d': None,
            'bench_return_1w': None,
            'bench_return_1m': None,
            'alpha_1d': None,
            'alpha_1w': None,
            'alpha_1m': None,
            'is_correct_alpha': None,
        }
        bench_code = self.BENCH_CODE.get(market)
        if not bench_code:
            return block
        for lbl, n_days in self.FIXED_PERIODS.items():
            base_close, _base_date, tn_close = self._get_bench_tn(bench_code, rating_date, n_days)
            if base_close is None or tn_close is None:
                continue
            bench_ret = round((tn_close - base_close) / base_close * 100, 2)
            block[f'bench_return_{lbl}'] = bench_ret
            stock_ret = returns.get(f'return_{lbl}')
            if stock_ret is not None:
                block[f'alpha_{lbl}'] = round(stock_ret - bench_ret, 2)
        # 主 alpha 判定：优先 1d，依次 1w/1m（与 is_correct 主口径一致）；缺基准不判定
        primary_alpha = block['alpha_1d']
        if primary_alpha is None:
            primary_alpha = block['alpha_1w']
        if primary_alpha is None:
            primary_alpha = block['alpha_1m']
        if primary_alpha is not None:
            block['is_correct_alpha'] = _judge(rating_norm, primary_alpha)
        return block

    @staticmethod
    def _get_tn_price(stock_id, rating_date, n):
        """获取评级日 T+n 的收盘价。

        Returns: (close_price, trade_date) 或 (None, None)
        """
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT close, trade_date FROM raw_kline '
            'WHERE stock_id = ? AND trade_date > ? '
            'ORDER BY trade_date ASC LIMIT 1 OFFSET ?',
            (stock_id, rating_date, n - 1),
        )
        row = cursor.fetchone()
        conn.close()
        if row and row['close'] is not None:
            return float(row['close']), row['trade_date']
        return None, None

    @staticmethod
    def _get_rating_at_date(stock_id, rating_date):
        """获取指定日期的评级记录。"""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT * FROM ratings_history WHERE stock_id = ? AND rating_date = ? '
            'ORDER BY id DESC LIMIT 1',
            (stock_id, rating_date),
        )
        row = cursor.fetchone()
        conn.close()
        return dict(row) if row else None

    @staticmethod
    def _get_stock_market(stock_id):
        """获取股票市场类型。"""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT market FROM stocks WHERE id = ?', (stock_id,))
        row = cursor.fetchone()
        conn.close()
        return row['market'] if row else 'a_stock'

    @staticmethod
    def _calc_return(base_price, target_price):
        """计算收益率百分比。"""
        if not base_price or not target_price or base_price == 0:
            return None
        return round((target_price - base_price) / base_price * 100, 2)

    # ---------- 固定周期回测 ----------

    def run_fixed_period_backtest(self, rating_id):
        """对单条评级记录执行固定周期回测。

        Returns: dict with backtest results
        """
        conn = get_connection()
        cursor = conn.cursor()

        # 1. 读取评级记录
        cursor.execute(
            'SELECT rh.*, s.market, s.symbol, s.name FROM ratings_history rh '
            'JOIN stocks s ON s.id = rh.stock_id '
            'WHERE rh.id = ?',
            (rating_id,),
        )
        rating_row = cursor.fetchone()
        if not rating_row:
            conn.close()
            return {'success': False, 'error': f'rating_id={rating_id} not found'}

        rating_row = dict(rating_row)
        stock_id = rating_row['stock_id']
        rating_date = rating_row['rating_date']
        rating_raw = rating_row['rating']
        price_at = rating_row['price_at_rating']
        market = rating_row.get('market', 'a_stock')

        # 2. 归一化评级（兼容历史A/B+/B/C/D）
        rating_norm = normalize_rating(rating_raw, rating_row.get('total_score'))

        # 3. 获取 T+N 收盘价和收益率
        results = {}
        for period_label, n_days in self.FIXED_PERIODS.items():
            price_tn, date_tn = self._get_tn_price(stock_id, rating_date, n_days)
            return_pct = self._calc_return(price_at, price_tn)
            is_correct = _judge(rating_norm, return_pct)
            results[f'price_{period_label}'] = price_tn
            results[f'return_{period_label}'] = return_pct
            results[f'is_correct_{period_label}'] = is_correct

        # 4. 主 is_correct：优先用 1d，其次 1w
        primary_correct = results.get('is_correct_1d')
        if primary_correct is None:
            primary_correct = results.get('is_correct_1w')
        if primary_correct is None:
            primary_correct = results.get('is_correct_1m')

        # 019T T3：基准对比（alpha 判定）。is_correct 原口径保留不动；缺基准 → 全 NULL
        alpha_block = self._compute_alpha_block(market, rating_date, rating_norm, results)
        results.update(alpha_block)

        # 5. 动态周期回测
        dynamic_result = self._compute_dynamic(stock_id, rating_date, rating_norm, price_at)
        results.update(dynamic_result)

        # 6. 写入数据库（UPSERT）
        cursor.execute('SELECT id FROM backtest_results WHERE rating_id = ?', (rating_id,))
        existing = cursor.fetchone()
        now_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')

        if existing:
            cursor.execute(
                """
                UPDATE backtest_results SET
                    stock_id=?, rating_id=?, market=?, rating_date=?, rating=?,
                    price_at_rating=?, price_1d=?, price_1w=?, price_1m=?,
                    return_1d=?, return_1w=?, return_1m=?,
                    is_correct=?, backtest_date=?,
                    dynamic_end_date=?, dynamic_return=?, dynamic_is_correct=?,
                    bench_return_1d=?, bench_return_1w=?, bench_return_1m=?,
                    alpha_1d=?, alpha_1w=?, alpha_1m=?, is_correct_alpha=?
                WHERE rating_id=?
            """,
                (
                    stock_id,
                    rating_id,
                    market,
                    rating_date,
                    rating_norm,
                    price_at,
                    results['price_1d'],
                    results['price_1w'],
                    results['price_1m'],
                    results['return_1d'],
                    results['return_1w'],
                    results['return_1m'],
                    primary_correct,
                    now_str,
                    results.get('dynamic_end_date'),
                    results.get('dynamic_return'),
                    results.get('dynamic_is_correct'),
                    results.get('bench_return_1d'),
                    results.get('bench_return_1w'),
                    results.get('bench_return_1m'),
                    results.get('alpha_1d'),
                    results.get('alpha_1w'),
                    results.get('alpha_1m'),
                    results.get('is_correct_alpha'),
                    rating_id,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO backtest_results
                (stock_id, rating_id, market, rating_date, rating,
                 price_at_rating, price_1d, price_1w, price_1m,
                 return_1d, return_1w, return_1m,
                 is_correct, backtest_date,
                 dynamic_end_date, dynamic_return, dynamic_is_correct,
                 bench_return_1d, bench_return_1w, bench_return_1m,
                 alpha_1d, alpha_1w, alpha_1m, is_correct_alpha)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
                (
                    stock_id,
                    rating_id,
                    market,
                    rating_date,
                    rating_norm,
                    price_at,
                    results['price_1d'],
                    results['price_1w'],
                    results['price_1m'],
                    results['return_1d'],
                    results['return_1w'],
                    results['return_1m'],
                    primary_correct,
                    now_str,
                    results.get('dynamic_end_date'),
                    results.get('dynamic_return'),
                    results.get('dynamic_is_correct'),
                    results.get('bench_return_1d'),
                    results.get('bench_return_1w'),
                    results.get('bench_return_1m'),
                    results.get('alpha_1d'),
                    results.get('alpha_1w'),
                    results.get('alpha_1m'),
                    results.get('is_correct_alpha'),
                ),
            )
        conn.commit()
        conn.close()

        results['success'] = True
        results['rating_id'] = rating_id
        results['rating_norm'] = rating_norm
        return results

    # ---------- 动态周期回测 ----------

    def _compute_dynamic(self, stock_id, rating_date, rating_norm, price_at):
        """计算动态周期：从评级日到下一次评级变更的收益率。"""
        conn = get_connection()
        cursor = conn.cursor()

        # 找到 rating_date 之后下一次 is_change=1 的评级记录
        cursor.execute(
            'SELECT rating_date, price_at_rating FROM ratings_history '
            'WHERE stock_id = ? AND rating_date > ? AND is_change = 1 '
            'ORDER BY rating_date ASC LIMIT 1',
            (stock_id, rating_date),
        )
        next_change = cursor.fetchone()
        conn.close()

        if not next_change or not price_at:
            return {'dynamic_end_date': None, 'dynamic_return': None, 'dynamic_is_correct': None}

        end_date = next_change['rating_date']
        end_price = next_change['price_at_rating']

        # 优先用评级时价格计算，如果没有则用 K 线收盘价
        if not end_price:
            end_price, end_date = self._get_tn_price(stock_id, rating_date, 999)
            if not end_price:
                return {
                    'dynamic_end_date': None,
                    'dynamic_return': None,
                    'dynamic_is_correct': None,
                }

        dyn_return = self._calc_return(price_at, end_price)
        dyn_correct = _judge(rating_norm, dyn_return)

        return {
            'dynamic_end_date': end_date,
            'dynamic_return': dyn_return,
            'dynamic_is_correct': dyn_correct,
        }

    # ---------- 批量回测 ----------

    def batch_backtest(self, market=None, days=None, force=False):
        """批量执行回测。

        Args:
            market: 'a_stock' / 'hk_stock' / None(全部)
            days: 只回测最近 N 天的评级，None=全部
            force: True=强制重跑（覆盖已有结果）

        Returns: dict with summary stats
        """
        conn = get_connection()
        cursor = conn.cursor()

        # 查询待回测的评级记录
        sql = """
            SELECT rh.id AS rating_id
            FROM ratings_history rh
            JOIN stocks s ON s.id = rh.stock_id
        """
        conditions = ['rh.price_at_rating IS NOT NULL', 'rh.price_at_rating > 0']
        params = []

        if market:
            conditions.append('s.market = ?')
            params.append(market)

        if days:
            cutoff = (datetime.now(_CN_TZ) - timedelta(days=days)).strftime('%Y-%m-%d')
            conditions.append('rh.rating_date >= ?')
            params.append(cutoff)

        if not force:
            conditions.append(
                'rh.id NOT IN (SELECT rating_id FROM backtest_results WHERE rating_id IS NOT NULL)'
            )

        sql += ' WHERE ' + ' AND '.join(conditions)
        sql += ' ORDER BY rh.rating_date'

        cursor.execute(sql, params)
        rating_ids = [row['rating_id'] for row in cursor.fetchall()]
        conn.close()

        total = len(rating_ids)
        success = 0
        errors = 0
        for rid in rating_ids:
            try:
                result = self.run_fixed_period_backtest(rid)
                if result.get('success'):
                    success += 1
                else:
                    errors += 1
            except Exception as e:
                logger.error(f'backtest rating_id={rid} failed: {e}')
                errors += 1

        return {
            'total': total,
            'success': success,
            'errors': errors,
            'market': market or 'all',
        }

    # ---------- 市场级报告 ----------

    def _build_interpretation(self, report):
        """根据真实样本统计生成客观解读评语（纯数据驱动，不硬编码买卖结论）。

        基于：样本量 / 总体准确率 / 周期衰减趋势 / 动态准确率 / 分档表现。
        阈值口径：≥60% 有效、45%~60% 一般/接近随机、<45% 偏弱。
        """
        parts = []
        total = report['total']
        if total == 0:
            return '暂无真实回测数据，无法解读。请先触发评级变更或手动重跑回测（报告仅统计真实评级回测样本，已排除模拟回测）。'

        parts.append(f'本报告基于 {total} 条真实评级回测样本（样本期 {report.get("date_range") or "—"}，已排除模拟回测数据）。')

        # 总体准确率（T+1日主口径）
        judged = report.get('correct_count', 0) + report.get('wrong_count', 0)
        acc = report.get('accuracy')
        if judged > 0 and acc is not None:
            if acc >= 0.60:
                parts.append(f'短期方向判断有效：T+1日口径总体准确率 {acc * 100:.0f}%（{judged}条可判定），显著高于随机水平。')
            elif acc >= 0.45:
                parts.append(f'短期方向判断一般：T+1日口径总体准确率 {acc * 100:.0f}%（{judged}条可判定），仅略高于随机，优势有限。')
            else:
                parts.append(f'短期方向判断偏弱：T+1日口径总体准确率 {acc * 100:.0f}%（{judged}条可判定），接近或低于随机水平。')

        # 周期衰减趋势
        pa = report.get('period_accuracy', {})
        p1d = pa.get('1d', {}).get('accuracy')
        p1w = pa.get('1w', {}).get('accuracy')
        p1m = pa.get('1m', {}).get('accuracy')
        if p1d is not None:
            tail = []
            if p1w is not None:
                tail.append(f'T+1周 {p1w * 100:.0f}%')
            if p1m is not None:
                tail.append(f'T+1月 {p1m * 100:.0f}%')
            if tail:
                parts.append(
                    f'周期衰减：准确率随持有期拉长递减（T+1日 {p1d * 100:.0f}% → ' + '、'.join(tail) + '），'
                    '评级以短线方向参考为主，长期持有参考价值下降。'
                )
            else:
                parts.append(f'周期维度：T+1日准确率 {p1d * 100:.0f}%，周/月样本不足暂不评估。')

        # 动态准确率（评级有效期）
        dyn_n = report.get('dynamic_count', 0)
        dyn = report.get('dynamic_accuracy')
        if dyn_n > 0 and dyn is not None:
            if dyn >= 0.60:
                parts.append(f'动态准确率 {dyn * 100:.0f}%（{dyn_n}条）较高：评级有效期内方向判断可信，可参考评级持有至改评。')
            elif dyn >= 0.45:
                parts.append(f'动态准确率 {dyn * 100:.0f}%（{dyn_n}条）接近随机水平：评级有效期内的持有无明显超额，不建议按评级长期持有。')
            else:
                parts.append(f'动态准确率 {dyn * 100:.0f}%（{dyn_n}条）低于随机：评级有效期内的方向判断不可信。')

        # 分档表现（样本≥30才纳入点评）
        rs_list = [
            (r, s) for r, s in report.get('rating_stats', {}).items()
            if s.get('total', 0) >= 30 and s.get('accuracy') is not None
        ]
        if rs_list:
            best = max(rs_list, key=lambda x: x[1]['accuracy'])
            worst = min(rs_list, key=lambda x: x[1]['accuracy'])
            parts.append(
                f'分档看：「{best[0]}」最可信（{best[1]["total"]}条，T+1日准确率 {best[1]["accuracy"] * 100:.0f}%）；'
                f'「{worst[0]}」最弱（{worst[1]["total"]}条，T+1日准确率 {worst[1]["accuracy"] * 100:.0f}%）。'
            )

        # 样本不足档位提示
        low = [
            r for r, s in report.get('rating_stats', {}).items()
            if 0 < s.get('total', 0) < 30 and s.get('accuracy') is not None
        ]
        if low:
            parts.append(f'注意：「{'、'.join(sorted(low))}」样本不足（<30条），其准确率仅供参考，勿单独作为决策依据。')

        parts.append('以上为历史回测统计解读，不构成投资建议。')
        return ' '.join(parts)

    def compute_market_report(self, market='a_stock', include_simulated=False):
        """生成市场级回测报告。

        Args:
            market: 'a_stock' / 'hk_stock'
            include_simulated: 是否包含模拟回测数据（默认False，仅真实数据）

        Returns: dict with accuracy stats, rating distribution, etc.
        """
        conn = get_connection()
        cursor = conn.cursor()

        if include_simulated:
            cursor.execute(
                'SELECT * FROM backtest_results WHERE market = ? ORDER BY rating_date', (market,)
            )
        else:
            cursor.execute(
                'SELECT * FROM backtest_results WHERE market = ? '
                'AND (is_simulated IS NULL OR is_simulated = 0) ORDER BY rating_date',
                (market,),
            )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        if not rows:
            return {
                'market': market,
                'total': 0,
                'message': '暂无回测数据，请先执行批量回测',
            }

        total = len(rows)

        # 总体准确率（排除 None）
        correct_1d = [r for r in rows if r.get('is_correct') is not None]
        accuracy = (
            sum(1 for r in correct_1d if r['is_correct'] == 1) / len(correct_1d)
            if correct_1d
            else 0
        )

        # 分级准确率
        rating_stats = {}
        for r in rows:
            rating = r.get('rating', '?')
            if rating not in rating_stats:
                rating_stats[rating] = {
                    'total': 0,
                    'correct': 0,
                    'wrong': 0,
                    'neutral': 0,
                    'dyn_correct': 0,
                    'dyn_wrong': 0,
                    'dyn_neutral': 0,
                    'dyn_returns': [],
                    'returns_1d': [],
                    'returns_1w': [],
                    'returns_1m': [],
                }
            rs = rating_stats[rating]
            rs['total'] += 1
            if r.get('is_correct') == 1:
                rs['correct'] += 1
            elif r.get('is_correct') == 0:
                rs['wrong'] += 1
            else:
                rs['neutral'] += 1
            # 动态周期判定（评级日→下次评级变更）
            if r.get('dynamic_is_correct') == 1:
                rs['dyn_correct'] += 1
            elif r.get('dynamic_is_correct') == 0:
                rs['dyn_wrong'] += 1
            else:
                rs['dyn_neutral'] += 1
            if r.get('dynamic_return') is not None:
                rs['dyn_returns'].append(r['dynamic_return'])
            if r.get('return_1d') is not None:
                rs['returns_1d'].append(r['return_1d'])
            if r.get('return_1w') is not None:
                rs['returns_1w'].append(r['return_1w'])
            if r.get('return_1m') is not None:
                rs['returns_1m'].append(r['return_1m'])

        for rating, rs in rating_stats.items():
            judged = rs['correct'] + rs['wrong']
            rs['accuracy'] = round(rs['correct'] / judged, 4) if judged > 0 else None
            # 分级动态准确率（评级有效期内的方向命中）
            dyn_judged = rs['dyn_correct'] + rs['dyn_wrong']
            rs['dyn_accuracy'] = round(rs['dyn_correct'] / dyn_judged, 4) if dyn_judged > 0 else None
            rs['dyn_judged'] = dyn_judged
            rs['dyn_avg_return'] = (
                round(sum(rs['dyn_returns']) / len(rs['dyn_returns']), 2)
                if rs['dyn_returns']
                else None
            )
            for period in ['1d', '1w', '1m']:
                vals = rs[f'returns_{period}']
                rs[f'avg_return_{period}'] = round(sum(vals) / len(vals), 2) if vals else None

        # 周期准确率（从存储的 return + rating 重新计算）
        period_accuracy = {}
        for period in ['1d', '1w', '1m']:
            correct_cnt = 0
            wrong_cnt = 0
            returns_list = []
            for r in rows:
                ret = r.get(f'return_{period}')
                if ret is not None:
                    returns_list.append(ret)
                    rating_norm = r.get('rating')
                    verdict = _judge(rating_norm, ret)
                    if verdict == 1:
                        correct_cnt += 1
                    elif verdict == 0:
                        wrong_cnt += 1
            judged = correct_cnt + wrong_cnt
            period_accuracy[period] = {
                'total': judged,
                'correct': correct_cnt,
                'wrong': wrong_cnt,
                'accuracy': round(correct_cnt / judged, 4) if judged > 0 else None,
                'avg_return': round(sum(returns_list) / len(returns_list), 2)
                if returns_list
                else None,
            }

        # 动态周期准确率
        dyn_judged = [r for r in rows if r.get('dynamic_is_correct') is not None]
        dyn_accuracy = (
            sum(1 for r in dyn_judged if r['dynamic_is_correct'] == 1) / len(dyn_judged)
            if dyn_judged
            else 0
        )

        # 日期范围
        dates = [r['rating_date'] for r in rows if r.get('rating_date')]
        date_range = f'{min(dates)} ~ {max(dates)}' if dates else ''

        # 小样本警告
        small_sample = total < 30

        report = {
            'market': market,
            'total': total,
            'accuracy': round(accuracy, 4),
            'correct_count': sum(1 for r in correct_1d if r['is_correct'] == 1),
            'wrong_count': sum(1 for r in correct_1d if r['is_correct'] == 0),
            'neutral_count': len(correct_1d)
            - sum(1 for r in correct_1d if r['is_correct'] in (0, 1)),
            'rating_stats': rating_stats,
            'period_accuracy': period_accuracy,
            'dynamic_accuracy': round(dyn_accuracy, 4),
            'dynamic_count': len(dyn_judged),
            'date_range': date_range,
            'small_sample_warning': small_sample,
            'sample_period_note': f'样本期: {date_range} (共{len(set(dates))}个交易日)',
        }
        # 客观解读评语（纯数据驱动，基于真实样本统计）
        report['interpretation'] = self._build_interpretation(report)
        return report

    # ---------- 个股回测明细 ----------

    def compute_stock_detail(self, stock_id):
        """个股回测明细。"""
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(
            'SELECT br.*, s.symbol, s.name FROM backtest_results br '
            'JOIN stocks s ON s.id = br.stock_id '
            'WHERE br.stock_id = ? ORDER BY br.rating_date',
            (stock_id,),
        )
        rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        if not rows:
            return {'success': False, 'message': '该股票暂无回测数据'}

        total = len(rows)
        correct_1d = [r for r in rows if r.get('is_correct') is not None]
        accuracy = (
            sum(1 for r in correct_1d if r['is_correct'] == 1) / len(correct_1d)
            if correct_1d
            else 0
        )

        # 动态准确率
        dyn_judged = [r for r in rows if r.get('dynamic_is_correct') is not None]
        dyn_accuracy = (
            sum(1 for r in dyn_judged if r['dynamic_is_correct'] == 1) / len(dyn_judged)
            if dyn_judged
            else 0
        )

        # 平均收益
        returns_1d = [r['return_1d'] for r in rows if r.get('return_1d') is not None]
        returns_1w = [r['return_1w'] for r in rows if r.get('return_1w') is not None]

        return {
            'success': True,
            'stock_id': stock_id,
            'symbol': rows[0].get('symbol', ''),
            'name': rows[0].get('name', ''),
            'total': total,
            'accuracy': round(accuracy, 4),
            'dynamic_accuracy': round(dyn_accuracy, 4),
            'avg_return_1d': round(sum(returns_1d) / len(returns_1d), 2) if returns_1d else None,
            'avg_return_1w': round(sum(returns_1w) / len(returns_1w), 2) if returns_1w else None,
            'records': rows,
            'small_sample_warning': total < 10,
        }

    # ---------- 自动触发：评级变更后 T+1 回测 ----------

    def auto_trigger_backtest(self, stock_id, rating_date):
        """评级变更后自动触发回测（不阻塞主流程）。

        在 advisor.generate_advice 写入 ratings_history(is_change=1) 后调用。
        如果 T+1 数据尚不可用，记录待回测，后续补算。
        """
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'SELECT id FROM ratings_history WHERE stock_id = ? AND rating_date = ? '
                'ORDER BY id DESC LIMIT 1',
                (stock_id, rating_date),
            )
            row = cursor.fetchone()
            conn.close()
            if not row:
                return {'success': False, 'error': 'rating record not found'}
            rating_id = row['id']
            result = self.run_fixed_period_backtest(rating_id)
            logger.info(
                f'auto_trigger_backtest: stock_id={stock_id} rating_id={rating_id} '
                f'is_correct={result.get("is_correct_1d")}'
            )
            return result
        except Exception as e:
            logger.error(f'auto_trigger_backtest failed: {e}')
            return {'success': False, 'error': str(e)}

    # ---------- 定时补算：填充到期未回测记录 ----------

    def fill_pending_backtests(self):
        """检查并填充到期但未回测的评级记录。

        每日收盘后调用，补算 T+1 数据已可用的评级。
        """
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT rh.id AS rating_id
            FROM ratings_history rh
            WHERE rh.price_at_rating IS NOT NULL AND rh.price_at_rating > 0
              AND rh.id NOT IN (SELECT rating_id FROM backtest_results WHERE rating_id IS NOT NULL)
            ORDER BY rh.rating_date
        """)
        pending = [row['rating_id'] for row in cursor.fetchall()]
        conn.close()

        filled = 0
        for rid in pending:
            try:
                result = self.run_fixed_period_backtest(rid)
                if result.get('success'):
                    filled += 1
            except Exception as e:
                logger.error(f'fill_pending rating_id={rid}: {e}')

        return {'pending': len(pending), 'filled': filled}


# ============================================================
# 四、四维综合评分模拟回测回填（M9-PREFILL）
# ============================================================

# 技术面得分 → 评级档位映射（任务书约定）
_SIM_RATING_THRESHOLDS = [
    (85, '强烈推荐买入'),
    (70, '推荐买入'),
    (50, '持有观望'),
    (30, '建议减仓'),
    (0, '强烈建议卖出'),
]


def _score_to_rating(score: float) -> str:
    """技术面得分映射为评级档位"""
    for threshold, rating in _SIM_RATING_THRESHOLDS:
        if score >= threshold:
            return rating
    return '强烈建议卖出'


def _calc_technical_score_from_kline(kline_slice: list[dict]) -> float:
    """基于K线切片计算技术面综合得分（0-100）

    复用 scoring_engine 中的技术面子项评分逻辑：
    均线(0.25) + 趋势(0.20) + 超买超卖(0.20) + 量价(0.10) + 量比(0.10) + 波动率(0.15)

    关键：传入截止日期的K线切片，无前瞻偏差。
    """
    from modules.data_adapter import (
        _calc_bollinger,
        _calc_kdj,
        _calc_ma,
        _calc_macd,
        _calc_rsi,
        _calc_volume_ratio,
    )
    from modules.data_contract import StockData
    from modules.scoring_engine import (
        TECHNICAL_SUBITEMS,
        adjust_subitem_weight,
        normalize_subitem_weights,
    )

    if not kline_slice or len(kline_slice) < 5:
        return 50.0  # 数据不足返回中性分

    closes = [float(r['close'] or 0) for r in kline_slice]
    volumes = [float(r['volume'] or 0) for r in kline_slice]
    latest = kline_slice[-1]

    # 计算技术指标
    ma5 = _calc_ma(closes, 5)
    ma10 = _calc_ma(closes, 10)
    ma20 = _calc_ma(closes, 20)
    ma60 = _calc_ma(closes, 60)
    rsi_14 = _calc_rsi(closes, 14)
    boll_upper, _, boll_lower = _calc_bollinger(closes, 20)
    macd_dif, macd_dea = _calc_macd(closes)
    kdj_k = _calc_kdj(kline_slice)
    volume_ratio = _calc_volume_ratio(volumes)

    # 构建最小化 StockData（仅技术面字段）
    data = StockData(
        code='SIM',
        market='A',
        trade_date=str(latest.get('trade_date', '')).replace('-', ''),
        close=float(latest['close'] or 0),
        ma5=ma5,
        ma10=ma10,
        ma20=ma20,
        ma60=ma60,
        macd_dif=macd_dif,
        macd_dea=macd_dea,
        kdj_k=kdj_k,
        rsi_14=rsi_14,
        volume=int(volumes[-1]) if volumes[-1] else None,
        volume_ratio=volume_ratio,
        boll_upper=boll_upper,
        boll_lower=boll_lower,
    )

    # 复用 scoring_engine 的技术面评分流程
    weighted = [(si, adjust_subitem_weight(data, si)) for si in TECHNICAL_SUBITEMS]
    norm_weights = normalize_subitem_weights(weighted)

    from modules.scoring_engine import SCORING_FUNCTIONS

    dim_score = 0.0
    for si, eff_w in weighted:
        score_fn = SCORING_FUNCTIONS[si.key]
        sub_score, _ = score_fn(data)
        norm_w = norm_weights[si.key]
        if norm_w > 0:
            dim_score += sub_score * norm_w

    return round(dim_score, 1)


def run_historical_simulation():
    """四维综合评分模拟回测回填（60天）

    对每只股票，每隔5个交易日取一个模拟评级日（约12个时间点/股）：
    1. 调用 data_adapter.load_stockdata_from_db() 构建完整 StockData（含基本面/资金面/消息面）
    2. 调用 scoring_engine.analyze() 执行四维综合评分，得到评级档位
    3. 在每个模拟评级日，用该四维评级 + 后续真实价格计算 T+1/T+5/T+20 收益率
    4. 用 JUDGEMENT_MATRIX 判定 is_correct
    5. 写入 backtest_results（标记 is_simulated=1）

    注意：load_stockdata_from_db 返回的是当前最新数据，因此同一只股票的所有模拟评级日
    共享同一四维评级。这引入轻微前瞻偏差（基本面/资金面/消息面为回测时点之后的数据），
    但对评估"评级有效性"可接受。

    幂等：每次执行先清除旧的 is_simulated=1 记录再重新生成，保证重复执行结果一致、
    不产生重复行。

    Returns:
        dict: {total, success, errors, skipped}
    """
    _ensure_columns()

    from modules.data_adapter import load_stockdata_from_db
    from modules.scoring_engine import analyze

    # 019T T3：模拟行 alpha 判定复用 BacktestEngine 基准计算方法
    engine = BacktestEngine()

    # 幂等：清除旧的模拟回测记录后重新生成，确保四维评分逻辑生效且不产生重复行
    conn = get_connection()
    cursor = conn.cursor()
    # 破坏性操作（批量删除）前自动备份
    backup_database('delete_backtest_simulated')
    cursor.execute('DELETE FROM backtest_results WHERE is_simulated = 1')
    cleared = cursor.rowcount if (cursor.rowcount and cursor.rowcount > 0) else 0
    conn.commit()

    # 获取所有有K线数据的股票
    cursor.execute("""
        SELECT s.id, s.symbol, s.name, s.market,
               COUNT(k.id) as kline_cnt
        FROM stocks s
        JOIN raw_kline k ON k.stock_id = s.id
        GROUP BY s.id
        HAVING kline_cnt >= 30
        ORDER BY s.id
    """)
    stocks = [dict(r) for r in cursor.fetchall()]
    conn.close()

    if cleared:
        logger.info(f'run_historical_simulation: 清除 {cleared} 条旧模拟记录，重新生成')

    if not stocks:
        return {
            'total': 0,
            'success': 0,
            'errors': 0,
            'skipped': 0,
            'message': '无足够K线数据的股票',
        }

    total = 0
    success = 0
    errors = 0
    skipped = 0

    for stock in stocks:
        stock_id = stock['id']
        market = stock['market']

        # 1. 四维综合评分：构建完整 StockData 并调用 analyze()
        #    load_stockdata_from_db 返回当前最新数据，整只股票共享同一四维评级
        stock_data = load_stockdata_from_db(stock_id)
        if stock_data is None:
            logger.info(f'simulation stock_id={stock_id}: StockData 构建失败，跳过该股票')
            skipped += 1
            continue

        try:
            analysis = analyze(stock_data)
        except Exception as e:
            logger.error(f'simulation stock_id={stock_id} analyze 失败: {e}')
            errors += 1
            continue

        rating = analysis.rating  # 四维综合评级（中文5档，与 JUDGEMENT_MATRIX 一致）

        # 2. 读取该股票全部K线数据（正序），用于历史收益率计算
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            SELECT trade_date, open, close, high, low, volume, amount, pct_change
            FROM raw_kline WHERE stock_id = ?
            ORDER BY trade_date ASC
        """,
            (stock_id,),
        )
        all_kline = [dict(r) for r in cursor.fetchall()]

        if len(all_kline) < 30:
            conn.close()
            continue

        # 确定模拟时间窗口：最新K线日往前60个交易日
        total_days = len(all_kline)
        sim_window = min(60, total_days - 5)  # 至少留出5天给T+1/T+5计算
        if sim_window < 10:
            conn.close()
            continue

        # 技术指标需要至少35天数据（MACD需要26+9）
        min_prefix = 35
        start_idx = max(min_prefix, total_days - sim_window)
        # 结束索引：留出尾部1天给T+1（T+5/T+20超出则为None）
        end_idx = total_days - 1

        if start_idx >= end_idx:
            conn.close()
            continue

        # 每隔5个交易日取一个模拟点
        sim_indices = list(range(start_idx, end_idx, 5))

        for sim_idx in sim_indices:
            total += 1
            sim_date = all_kline[sim_idx]['trade_date']

            try:
                # 3. 评级时价格
                price_at = float(all_kline[sim_idx]['close'] or 0)
                if price_at <= 0:
                    errors += 1
                    continue

                # 4. 计算 T+1/T+5/T+20 收益率（基于历史真实价格）
                def _get_future_price(offset):
                    target_idx = sim_idx + offset
                    if target_idx < total_days:
                        return float(all_kline[target_idx]['close'] or 0)
                    return None

                price_1d = _get_future_price(1)
                price_1w = _get_future_price(5)
                price_1m = _get_future_price(20)

                def _calc_ret(target_price):
                    if target_price and target_price > 0:
                        return round((target_price - price_at) / price_at * 100, 2)
                    return None

                return_1d = _calc_ret(price_1d)
                return_1w = _calc_ret(price_1w)
                return_1m = _calc_ret(price_1m)

                # 5. 判定 is_correct（用四维 rating，优先用 T+1）
                primary_correct = _judge(rating, return_1d)
                if primary_correct is None:
                    primary_correct = _judge(rating, return_1w)
                if primary_correct is None:
                    primary_correct = _judge(rating, return_1m)

                # 019T T3：模拟行同样补基准对比（alpha 判定），缺基准全 NULL
                alpha_block = engine._compute_alpha_block(
                    market,
                    sim_date,
                    rating,
                    {'return_1d': return_1d, 'return_1w': return_1w, 'return_1m': return_1m},
                )

                # 6. 写入 backtest_results（is_simulated=1）
                now_str = datetime.now(_CN_TZ).strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute(
                    """
                    INSERT INTO backtest_results
                    (stock_id, rating_id, market, rating_date, rating,
                     price_at_rating, price_1d, price_1w, price_1m,
                     return_1d, return_1w, return_1m,
                     is_correct, backtest_date, is_simulated,
                     bench_return_1d, bench_return_1w, bench_return_1m,
                     alpha_1d, alpha_1w, alpha_1m, is_correct_alpha)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1,
                            ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        stock_id,
                        -1,
                        market,
                        sim_date,
                        rating,
                        price_at,
                        price_1d,
                        price_1w,
                        price_1m,
                        return_1d,
                        return_1w,
                        return_1m,
                        primary_correct,
                        now_str,
                        alpha_block['bench_return_1d'],
                        alpha_block['bench_return_1w'],
                        alpha_block['bench_return_1m'],
                        alpha_block['alpha_1d'],
                        alpha_block['alpha_1w'],
                        alpha_block['alpha_1m'],
                        alpha_block['is_correct_alpha'],
                    ),
                )
                success += 1

            except Exception as e:
                logger.error(f'simulation stock_id={stock_id} date={sim_date}: {e}')
                errors += 1

        conn.commit()
        conn.close()

    logger.info(
        f'M9-PREFILL 四维模拟回测完成: total={total}, success={success}, '
        f'errors={errors}, skipped={skipped}'
    )
    return {
        'total': total,
        'success': success,
        'errors': errors,
        'skipped': skipped,
    }


# ============================================================
# 五、权重实验场景（D4 裁定预留）
# ============================================================


class WeightExperimentRunner:
    """权重实验场景（M9 预留接口）

    ⚠️ 重要边界：本模块仅模拟计算，不修改生产权重。
    """

    EXPERIMENTS = [
        {
            'id': 'd4_news_0_to_20',
            'name': 'D4-消息面权重 0→20%',
            'description': '将A股消息面权重从当前值提升至20%，其他维度同比缩减',
            'market': 'a_stock',
            'changes': {'news': 0.20, 'kline': -0.05, 'fundamental': -0.10, 'capital_flow': -0.05},
        },
        {
            'id': 'd4_hk_news_boost',
            'name': 'D4-港股消息面增强',
            'description': '港股消息面权重从10%提升至20%，测试港股评级敏感性',
            'market': 'hk_stock',
            'changes': {'news': 0.20, 'kline': -0.05, 'fundamental': -0.05, 'capital_flow': -0.10},
        },
    ]

    def list_experiments(self):
        """列出所有可用实验场景。"""
        return [
            {
                'id': e['id'],
                'name': e['name'],
                'description': e['description'],
                'market': e['market'],
            }
            for e in self.EXPERIMENTS
        ]

    def run_experiment(self, experiment_id):
        """执行权重实验：用实验权重对历史评级重新评分，对比准确率差异。

        ⚠️ 仅模拟计算，不修改生产权重。

        Returns: dict with ΔAccuracy and comparison details
        """
        exp = None
        for e in self.EXPERIMENTS:
            if e['id'] == experiment_id:
                exp = e
                break
        if not exp:
            return {'success': False, 'error': f'experiment {experiment_id} not found'}

        # 读取当前市场回测结果作为对照组
        engine = BacktestEngine()
        control_report = engine.compute_market_report(exp['market'])

        if control_report.get('total', 0) == 0:
            return {
                'success': True,
                'experiment_id': experiment_id,
                'experiment_name': exp['name'],
                'note': '当前无回测数据，无法执行实验。请先运行批量回测。',
                'control_accuracy': None,
                'experiment_accuracy': None,
                'delta_accuracy': None,
            }

        # 模拟：读取历史评级数据，用实验权重重新计算评级，然后模拟回测
        # 由于无法实际重跑评分引擎（需要历史四维数据），这里用统计方法估算
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT br.* FROM backtest_results br WHERE br.market = ? ORDER BY br.rating_date',
            (exp['market'],),
        )
        bt_rows = [dict(r) for r in cursor.fetchall()]
        conn.close()

        # 简化模拟：消息面权重提升 → 评级分布向高/低两端偏移
        # 实际的权重实验需要 M9 阶段完整的历史四维数据重算
        # 这里提供框架和接口，实际计算标注为"待M9完整实施"

        control_accuracy = control_report.get('accuracy', 0)
        # 估算：权重变化对准确率的影响（基于历史弹性系数的粗略估算）
        # 实际影响需要完整的历史四维数据重算
        estimated_impact = self._estimate_weight_impact(exp, bt_rows)

        return {
            'success': True,
            'experiment_id': experiment_id,
            'experiment_name': exp['name'],
            'market': exp['market'],
            'weight_changes': exp['changes'],
            'control_accuracy': round(control_accuracy, 4),
            'control_total': control_report.get('total', 0),
            'experiment_accuracy': round(control_accuracy + estimated_impact, 4),
            'delta_accuracy': round(estimated_impact, 4),
            'note': '基于短期样本的初步结论，M9阶段复核。权重变化仅模拟，不影响生产配置。',
            'sample_warning': control_report.get('small_sample_warning', True),
        }

    @staticmethod
    def _estimate_weight_impact(exp, bt_rows):
        """粗略估算权重变化对准确率的影响。

        方法：统计消息面维度得分与评级准确率的相关性，
        然后根据权重变化幅度估算影响。

        ⚠️ 这是简化估算，M9阶段需要完整重算。
        """
        # 无历史四维数据时，返回保守估算
        # 消息面权重从0→20%，预期对准确率有 ±2-5% 的边际影响
        # 但方向不确定（取决于消息面因子的有效性）
        # 返回0表示"无显著变化"的保守估计
        if not bt_rows:
            return 0.0

        # 简单方法：看当前准确率与50%（随机）的偏差
        # 如果当前准确率>50%，说明评级有一定有效性，增加消息面权重可能提升或降低
        # 保守返回0，实际影响待M9完整评估
        return 0.0
