"""核心回测引擎域（t3 拆包）：BacktestEngine 整类。

划分依据：M8 主引擎单类内聚（固定周期 T+1/5/20 + alpha 基准 + 动态周期 +
批量 + 市场级报告 + 客观解读 + 个股明细 + 自动触发/补算），拆类需改继承/委托
结构、违背「逐段搬运、行为零变化」最低风险原则，故整类原样搬移；
跨域依赖（判定矩阵/表结构迁移/情绪常量模板）经兄弟子模块绝对路径导入。
实现体为原文件 L575-1661 逐字节搬移。
"""

from datetime import datetime, timedelta

from modules.backtest_engine._env import _CN_TZ, get_connection, logger, normalize_rating
from modules.backtest_engine.judgement import (
    HK_VOL_SCALE_MAX,
    HK_VOL_SCALE_MIN,
    _judge,
    _sigma_daily,
)
from modules.backtest_engine.schema import _backfill_pos_dd, _ensure_columns
from modules.backtest_engine.sentiment_note import sentiment_evidence_note_for


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
            # 021P：alpha 判定同差异化——港股个股相对基准的偏离日常更大
            block['is_correct_alpha'] = _judge(rating_norm, primary_alpha, market)
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
            is_correct = _judge(rating_norm, return_pct, market)
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
        # 021P：动态口径同差异化——按个股所属市场取判定区间
        # 021AH：动态路径启用观望边界模糊容忍（跑出带外<1%不计入）——
        # 窗口终点=改评日与价格跑出带强相关，硬判错属口径偏置非方向错误
        stock_market = self._get_stock_market(stock_id)
        # 021AK：港股动态窗口波动率标定——带宽按 σ·√t 放大（A股 None=固定带）
        vol_scale = self._calc_dynamic_vol_scale(stock_id, rating_date, end_date, stock_market)
        dyn_correct = _judge(
            rating_norm,
            dyn_return,
            stock_market,
            neutral_borderline=True,
            vol_scale=vol_scale,
        )

        return {
            'dynamic_end_date': end_date,
            'dynamic_return': dyn_return,
            'dynamic_is_correct': dyn_correct,
        }

    def _calc_dynamic_vol_scale(self, stock_id, rating_date, end_date, market):
        """021AK：港股动态窗口波动率标定系数（A股返回 None 不标定）。

        scale = σ(评级日前20日日收益率%) × √(窗口日历天数) / 3.5（港股基础带），
        夹在 [0.5, 4.0]。K线不足/日期异常时返回 None（退回固定带判定）。
        """
        if market != 'hk_stock':
            return None
        try:
            from datetime import date as _date

            days = (
                _date.fromisoformat(end_date[:10]) - _date.fromisoformat(rating_date[:10])
            ).days
        except (ValueError, TypeError):
            return None
        if days < 1:
            return None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                'SELECT close FROM raw_kline WHERE stock_id = ? AND trade_date < ? '
                'ORDER BY trade_date DESC LIMIT 21',
                (stock_id, rating_date),
            )
            closes = [row['close'] for row in cursor.fetchall()][::-1]  # 升序
            conn.close()
        except Exception as e:  # noqa: BLE001
            logger.warning(f'vol_scale 读取K线失败 stock_id={stock_id}: {e}')
            return None
        sigma = _sigma_daily(closes)
        if not sigma or sigma <= 0:
            return None
        scale = sigma * (days**0.5) / 3.5
        return min(max(scale, HK_VOL_SCALE_MIN), HK_VOL_SCALE_MAX)

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

        # 020K：自愈清理孤儿回测行——ratings_history 写入用 INSERT OR REPLACE
        # （advisor.py 红线模块），报告重生成会换掉 rating id，旧 id 的回测结果
        # 失去引用；不清理会重复计数污染市场报告统计。
        # 排除 rating_id=-1（历史模拟回测行，is_simulated=1，无对应评级）。
        cleaned = cursor.execute(
            'DELETE FROM backtest_results WHERE rating_id IS NOT NULL AND rating_id != -1 '
            'AND rating_id NOT IN (SELECT id FROM ratings_history)'
        ).rowcount
        conn.commit()
        if cleaned:
            logger.info(f'[backtest] 清理孤儿回测行: {cleaned} 条')

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

        基于：样本量 / 总体准确率 / 周期趋势 / 动态准确率 / 分档表现 / 引擎构成。
        阈值口径：≥60% 有效、45%~60% 一般/接近随机、<45% 偏弱。
        020R-21：每条观点附色调 tones（good=✓ 提示 / bad=⚠️ 预警 / neutral=中性），
        与 interpretation_parts 等长，前端逐条着色展示。
        020R-52：总体准确率附二项检验显著性（正态近似）；周期趋势条件化（增/减/无单调）；
        新增引擎构成声明（v5 新基线样本不足时明确"尚未验证当前规则"）。
        """
        import math

        def _binomial_p2(n: int, k: int) -> float | None:
            """双尾二项检验 p 值（正态近似）：检验准确率是否偏离 50% 随机水平。"""
            if n <= 0:
                return None
            phat = k / n
            se = math.sqrt(0.25 / n)
            z = abs(phat - 0.5) / se
            return 2.0 * (1.0 - 0.5 * (1.0 + math.erf(z / math.sqrt(2.0))))

        parts = []
        tones = []

        def add(text, tone='neutral'):
            parts.append(text)
            tones.append(tone)

        total = report['total']
        if total == 0:
            add('暂无真实回测数据，无法解读。请先触发评级变更或手动重跑回测（报告仅统计真实评级回测样本，已排除模拟回测）。', 'bad')
            report['interpretation_parts'] = parts
            report['interpretation_tones'] = tones
            return parts[0]

        add(f'本报告基于 {total} 条真实评级回测样本（样本期 {report.get("date_range") or "—"}，已排除模拟回测数据）。')

        # 020R-52：引擎构成声明——v5 样本不足时明示"报告主要反映历史引擎表现"
        es = report.get('engine_stats') or {}
        v5n = es.get('v5', {}).get('total', 0) or 0
        legacy_n = es.get('legacy', {}).get('total', 0) or 0
        unmarked = es.get('未标记(历史)', {}).get('total', 0) or 0
        if v5n or legacy_n or unmarked:
            comp_parts = []
            if v5n:
                comp_parts.append(f'v5（当前规则）{v5n} 条')
            if legacy_n:
                comp_parts.append(f'经典引擎 {legacy_n} 条')
            if unmarked:
                comp_parts.append(f'历史引擎（未标记）{unmarked} 条')
            add(f'引擎构成：{"、".join(comp_parts)}。')
            if v5n < 30 and (legacy_n + unmarked) > 0:
                add(
                    f'当前 v5 规则样本仅 {v5n} 条（不足30），本报告总体结论主要反映历史引擎表现，'
                    '当前评分规则尚未积累足够回测样本、未被验证。',
                    'bad',
                )
            elif v5n >= 30 and es.get('v5', {}).get('accuracy') is not None:
                v5_acc = es['v5']['accuracy']
                add(
                    f'v5 规则样本 {v5n} 条，T+1 日准确率 {v5_acc * 100:.0f}%，已具备初步统计意义。',
                    'good' if v5_acc >= 0.60 else ('neutral' if v5_acc >= 0.45 else 'bad'),
                )

        # 总体准确率（T+1日主口径，020R-52 附二项检验 + 成本提示）
        judged = report.get('correct_count', 0) + report.get('wrong_count', 0)
        acc = report.get('accuracy')
        if judged > 0 and acc is not None:
            p_val = _binomial_p2(judged, report.get('correct_count', 0))
            sig_txt = ''
            if p_val is not None:
                sig_txt = (
                    f'（二项检验 p={p_val:.3f}，{"统计显著" if p_val < 0.05 else "统计不显著"}）'
                )
            if acc >= 0.60:
                add(f'短期方向判断有效：T+1日口径总体准确率 {acc * 100:.0f}%（{judged}条可判定）{sig_txt}，显著高于随机水平。', 'good')
            elif acc >= 0.45:
                add(f'短期方向判断一般：T+1日口径总体准确率 {acc * 100:.0f}%（{judged}条可判定）{sig_txt}，仅略高于随机，优势有限。', 'bad')
            else:
                add(f'短期方向判断偏弱：T+1日口径总体准确率 {acc * 100:.0f}%（{judged}条可判定）{sig_txt}，接近或低于随机水平。', 'bad')
            add('注意：T+1 口径未扣除交易成本，统计显著不等于实际可盈利。', 'neutral')

        # 021P：港股判定口径声明——观望档 ±3.5%（A股 ±2%）
        if report.get('market') == 'hk_stock':
            add(
                '口径说明：本报告为港股差异化判定——「持有观望」档正确区间为 ±3.5%'
                '（A股为 ±2%），适配港股无涨跌停的日常波幅（021P）。',
                'neutral',
            )

        # 周期趋势（020R-52：条件化——增/减/无单调，按实际数据措辞）
        pa = report.get('period_accuracy', {})
        p1d = pa.get('1d', {}).get('accuracy')
        p1w = pa.get('1w', {}).get('accuracy')
        p1m = pa.get('1m', {}).get('accuracy')
        if p1d is not None and p1w is not None and p1m is not None:
            if p1d > p1w > p1m:
                add(
                    f'周期衰减：准确率随持有期拉长递减（T+1日 {p1d * 100:.0f}% → T+1周 {p1w * 100:.0f}% → T+1月 {p1m * 100:.0f}%），'
                    '评级以短线方向参考为主，长期持有参考价值下降。',
                    'bad',
                )
            elif p1d < p1w < p1m:
                add(
                    f'周期增强：准确率随持有期拉长提升（T+1日 {p1d * 100:.0f}% → T+1周 {p1w * 100:.0f}% → T+1月 {p1m * 100:.0f}%），'
                    '评级在中长期更具参考价值。',
                    'good',
                )
            else:
                add(
                    f'周期维度：准确率随持有期无单调趋势（T+1日 {p1d * 100:.0f}% / T+1周 {p1w * 100:.0f}% / T+1月 {p1m * 100:.0f}%），'
                    '不同持有期表现不一。',
                )
        elif p1d is not None:
            add(f'周期维度：T+1日准确率 {p1d * 100:.0f}%，周/月样本不足暂不评估。')

        # 动态准确率（评级有效期）
        dyn_n = report.get('dynamic_count', 0)
        dyn = report.get('dynamic_accuracy')
        if dyn_n > 0 and dyn is not None:
            # 021AK：口径说明按市场差异化——港股动态判定按波动率标定（σ√t）
            is_hk = report.get('market') == 'hk_stock'
            note = (
                '口径说明：港股动态判定带宽按个股波动率×√窗口天数标定（021AK），'
                '并计观望边界模糊不计入（021AH）——无涨跌停+T+0 下窗口越长噪声越大，'
                '带宽随 σ√t 放大；A股用固定带（±2%+1%容忍）。'
                if is_hk
                else '口径说明：观望档跑出带外不足1%的样本不计入（021AH）——'
                '窗口终点为改评日，与价格跑出区带相关，边缘跑出不视为方向错误。'
            )
            if dyn >= 0.60:
                add(f'动态准确率 {dyn * 100:.0f}%（{dyn_n}条）较高：评级有效期内方向判断可信，可参考评级持有至改评。', 'good')
                add(note, 'neutral')
            elif dyn >= 0.45:
                add(f'动态准确率 {dyn * 100:.0f}%（{dyn_n}条）接近随机水平：评级有效期内的持有无明显超额，不建议按评级长期持有。', 'bad')
                add(note, 'neutral')
            else:
                add(f'动态准确率 {dyn * 100:.0f}%（{dyn_n}条）低于随机：评级有效期内的方向判断不可信。', 'bad')
                add(note, 'neutral')

        # 分档表现（样本≥30才纳入点评；020R-21 拆为"最可信"与"最弱"两条，分别提示/预警）
        rs_list = [
            (r, s) for r, s in report.get('rating_stats', {}).items()
            if s.get('total', 0) >= 30 and s.get('accuracy') is not None
        ]
        if rs_list:
            best = max(rs_list, key=lambda x: x[1]['accuracy'])
            worst = min(rs_list, key=lambda x: x[1]['accuracy'])
            best_acc = best[1]['accuracy']
            worst_acc = worst[1]['accuracy']
            add(
                f'分档看：「{best[0]}」最可信（{best[1]["total"]}条，T+1日准确率 {best_acc * 100:.0f}%）。',
                'good' if best_acc >= 0.60 else 'neutral',
            )
            add(
                f'「{worst[0]}」最弱（{worst[1]["total"]}条，T+1日准确率 {worst_acc * 100:.0f}%）。',
                'bad' if worst_acc < 0.45 else 'neutral',
            )

        # 样本不足档位提示
        low = [
            r for r, s in report.get('rating_stats', {}).items()
            if 0 < s.get('total', 0) < 30 and s.get('accuracy') is not None
        ]
        if low:
            add(f'注意：「{'、'.join(sorted(low))}」样本不足（不足30条），其准确率仅供参考，勿单独作为决策依据。', 'bad')

        # 2026-09-18（回测提升①）：位置矩阵条件化解读——仅在分化显著（≥15pp）且子样本≥10 时输出
        pm = report.get('position_matrix') or {}
        for rating, cell_hint in (
            ('推荐买入', '（高位买入=追涨信号，历史易回落；低位买入=底部确认，历史较可信）'),
            ('建议减仓', '（高位减仓=出货识别，历史较可信；低位减仓=错杀嫌疑，历史接近随机）'),
        ):
            bands = pm.get(rating) or {}
            lo_c, lo_t = bands.get('low', {}).get('correct', 0), bands.get('low', {}).get('total', 0)
            hi_c, hi_t = bands.get('high', {}).get('correct', 0), bands.get('high', {}).get('total', 0)
            if lo_t >= 10 and hi_t >= 10:
                lo_acc, hi_acc = lo_c / lo_t, hi_c / hi_t
                if abs(lo_acc - hi_acc) >= 0.15:
                    direction = '低' if lo_acc > hi_acc else '高'
                    add(
                        f'位置分层：「{rating}」在低位票准确率 {lo_acc * 100:.0f}%（{lo_c}/{lo_t}）、'
                        f'高位票 {hi_acc * 100:.0f}%（{hi_c}/{hi_t}），{direction}位更可信{cell_hint}'
                        '。子样本仍偏小，随样本积累复核。',
                        'good' if max(lo_acc, hi_acc) >= 0.6 else 'neutral',
                    )

        # 避损口径：减仓/卖出档的事后回撤若与其他档无显著差，如实说明
        dr = report.get('drawdown_risk') or {}
        rk, ot = dr.get('risk') or {}, dr.get('other') or {}
        if (rk.get('n') or 0) >= 20 and (ot.get('n') or 0) >= 20 and rk.get('mean') is not None:
            if rk['mean'] >= ot['mean'] - 1.0:
                add(
                    f'避损口径：减仓/卖出评级标记的票事后20日回撤（平均 {rk["mean"]:+.1f}%）'
                    f'与其他档（{ot["mean"]:+.1f}%）无明显差异——当前减仓评级暂无额外避损价值，'
                    '其价值更多在纪律触发（止盈止损线），而非预测更弱。',
                    'bad',
                )
            else:
                add(
                    f'避损口径：减仓/卖出评级标记的票事后20日回撤（平均 {rk["mean"]:+.1f}%）'
                    f'小于其他档（{ot["mean"]:+.1f}%）——评级具备一定避损价值。',
                    'good',
                )

        # 021BW O3：情绪代理检验现状（透明说明行；常量模板，证据更新只改
        # SENTIMENT_EVIDENCE_NOTE 一处；R20 分市场成行，未知市场不加行）
        sentiment_note = sentiment_evidence_note_for(report.get('market'))
        if sentiment_note:
            add(sentiment_note, 'neutral')

        add('以上为历史回测统计解读，不构成投资建议。')
        # 020R-20/21：逐条观点 + 色调列表（前端卡片化逐条着色展示）
        report['interpretation_parts'] = parts
        report['interpretation_tones'] = tones
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

        # 021BB：引擎归因改按自然键 (stock_id, rating_date) JOIN——
        # ratings_history 用 INSERT OR REPLACE（同股同日重评换新 id），
        # 旧按 id JOIN 会产生孤儿 rating_id（实测 338 条），被误标"未标记(历史)"；
        # 自然键在 REPLACE 下稳定，当前行即该股当日评级的最新版本（同为 v5 引擎）。
        if include_simulated:
            cursor.execute(
                'SELECT br.*, rh.engine_version FROM backtest_results br '
                'LEFT JOIN ratings_history rh '
                'ON rh.stock_id = br.stock_id AND rh.rating_date = br.rating_date '
                'WHERE br.market = ? ORDER BY br.rating_date',
                (market,),
            )
        else:
            cursor.execute(
                'SELECT br.*, rh.engine_version FROM backtest_results br '
                'LEFT JOIN ratings_history rh '
                'ON rh.stock_id = br.stock_id AND rh.rating_date = br.rating_date '
                'WHERE br.market = ? '
                'AND (br.is_simulated IS NULL OR br.is_simulated = 0) ORDER BY br.rating_date',
                (market,),
            )
        rows = [dict(r) for r in cursor.fetchall()]

        # 2026-09-18（回测提升①）：位置/回撤列自愈式回填（首次增量，其后仅新行）
        cursor.execute(
            'SELECT COUNT(*) FROM backtest_results '
            'WHERE pos_pctile IS NULL AND dd20 IS NULL')
        if cursor.fetchone()[0] > 0:
            conn.close()
            _backfill_pos_dd()
            conn = get_connection()
            cursor = conn.cursor()
            if include_simulated:
                cursor.execute(
                    'SELECT br.*, rh.engine_version FROM backtest_results br '
                    'LEFT JOIN ratings_history rh '
                    'ON rh.stock_id = br.stock_id AND rh.rating_date = br.rating_date '
                    'WHERE br.market = ? ORDER BY br.rating_date',
                    (market,),
                )
            else:
                cursor.execute(
                    'SELECT br.*, rh.engine_version FROM backtest_results br '
                    'LEFT JOIN ratings_history rh '
                    'ON rh.stock_id = br.stock_id AND rh.rating_date = br.rating_date '
                    'WHERE br.market = ? '
                    'AND (br.is_simulated IS NULL OR br.is_simulated = 0) ORDER BY br.rating_date',
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

        # 020R-51：按引擎分层统计（ratings_history.engine_version；历史行为 NULL → 未标记）
        engine_stats = {}
        for r in rows:
            ev = r.get('engine_version') or '未标记(历史)'
            if ev not in engine_stats:
                engine_stats[ev] = {
                    'total': 0,
                    'correct': 0,
                    'wrong': 0,
                    'neutral': 0,
                    'dyn_correct': 0,
                    'dyn_wrong': 0,
                    'returns_1m': [],
                }
            es = engine_stats[ev]
            es['total'] += 1
            if r.get('is_correct') == 1:
                es['correct'] += 1
            elif r.get('is_correct') == 0:
                es['wrong'] += 1
            else:
                es['neutral'] += 1
            if r.get('dynamic_is_correct') == 1:
                es['dyn_correct'] += 1
            elif r.get('dynamic_is_correct') == 0:
                es['dyn_wrong'] += 1
            if r.get('return_1m') is not None:
                es['returns_1m'].append(r['return_1m'])
        for ev, es in engine_stats.items():
            judged = es['correct'] + es['wrong']
            es_dyn_judged = es['dyn_correct'] + es['dyn_wrong']
            es['accuracy'] = round(es['correct'] / judged, 4) if judged > 0 else None
            es['dyn_accuracy'] = (
                round(es['dyn_correct'] / es_dyn_judged, 4) if es_dyn_judged > 0 else None
            )
            es['avg_return_1m'] = (
                round(sum(es['returns_1m']) / len(es['returns_1m']), 2)
                if es['returns_1m']
                else None
            )

        # 小样本警告
        small_sample = total < 30

        # 2026-09-18（回测提升①）：分档×位置矩阵 + 避损口径——
        # 回测验证：低位买入 68% vs 高位买入 33%、高位减仓 88% vs 低位减仓 37%，
        # 方向信息被位置混杂掩盖，矩阵是「条件化使用评级」的数据基础
        POS_BANDS = (('low', 0.0, 0.4, '低位<40%'), ('mid', 0.4, 0.7, '中位40-70%'), ('high', 0.7, 1.01, '高位>70%'))
        position_matrix = {}
        dd_risk = {'risk': [], 'other': []}
        for r in rows:
            pos = r.get('pos_pctile')
            rating = r.get('rating', '?')
            # 分母口径与其他准确率一致：仅可判定行（中性/无法判定不计入，防稀释）
            if r.get('dynamic_is_correct') not in (0, 1):
                continue
            if pos is not None:
                for band, lo, hi, _label in POS_BANDS:
                    if lo <= pos < hi:
                        cell = position_matrix.setdefault(rating, {}).setdefault(
                            band, {'correct': 0, 'total': 0})
                        cell['total'] += 1
                        if r.get('dynamic_is_correct') == 1:
                            cell['correct'] += 1
                        break
            if r.get('dd20') is not None:
                bucket = 'risk' if rating in ('建议减仓', '强烈建议卖出') else 'other'
                dd_risk[bucket].append(r['dd20'])
        for rating, bands in position_matrix.items():
            for band, cell in bands.items():
                cell['accuracy'] = round(cell['correct'] / cell['total'], 4) if cell['total'] else None
        drawdown_risk = {}
        for bucket, vals in dd_risk.items():
            drawdown_risk[bucket] = {
                'n': len(vals),
                'mean': round(sum(vals) / len(vals), 2) if vals else None,
                'median': round(sorted(vals)[len(vals) // 2], 2) if vals else None,
                'worst': round(min(vals), 2) if vals else None,
            }

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
            'engine_stats': engine_stats,
            # 2026-09-18（回测提升①）：分档×位置矩阵 + 避损口径
            'position_matrix': position_matrix,
            'position_bands': [{'key': b, 'lo': lo, 'hi': hi, 'label': lb} for b, lo, hi, lb in POS_BANDS],
            'drawdown_risk': drawdown_risk,
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
