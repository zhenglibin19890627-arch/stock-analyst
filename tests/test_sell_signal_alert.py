"""
021BQ 决策闭环 项A+项B+项C（+项④评级上下文）：卖点信号纯函数 → 双侧巡检 → 卖点预警

覆盖（隔离临时库 + 合成K线，不触网）：
1. 卖侧平行库：detect_sell_signals / detect_sell_resonances（与买侧 detect_*
   同构镜像；配方=021BQ 方案 §六 镜像形态表，实机校准落位最新一根）
   ——平行边界：SIGNAL_LIBRARY/RESONANCE_LIBRARY 零泄漏（在线扫描仍只产买点）
2. 卖侧巡检：scan_watchlist_sell_signals / compute_watchlist_sell_result 全链
3. 预警链路：scan_once 经 _RULE_CHECKERS 写入 sell_signal 预警——幂等、
   "今日出现"口径、共振星级门槛、白名单/种子/前端静态面 8 处同步、
   文案无裸 '<'（021BN 教训）
4. 评级上下文（项④）：卖点信号撞买入档评级 / 买点信号撞减仓档评级 →
   消息附"仅波段参考，以评级为主"调和注记（daily_reports 最新行）
"""

import datetime as dt
import json
from pathlib import Path

import pytest

from blueprints import alerts as alerts_bp
from database import db_manager
from modules import alert_engine
from modules.market_screener import (
    RESONANCE_LIBRARY,
    SELL_RESONANCE_LIBRARY,
    SELL_SIGNAL_LIBRARY,
    SIGNAL_LIBRARY,
    compute_watchlist_sell_result,
    detect_sell_resonances,
    detect_sell_signals,
    detect_signals,
    scan_watchlist_sell_signals,
    scan_watchlist_signals,
)

# ---------------- 合成K线构造（形态已实机校准，021BQ 方案 §六 镜像配方） ----------------

def _rally_gap_down_closes():
    """44 根稳步上涨(+2.0) + 1 根加速阳(+8) + 1 根缺口大阴(-18) → MACD水上死叉 +
    KDJ高位死叉 同日触发，且触发日 == 最新一根（window=3）。

    实机校准注记：稳步上涨段 KDJ 的 RSV 随窗口抬升缓降，K 全程贴在 D 下方
    约 0.16——无加速柱则高位死叉判定永不触发；加速阳(+8)使 K 严格站上 D 后，
    大阴单日翻转双交叉（021BP 同款"调参直至交叉落位"纪律）。"""
    return [60 + i * 2.0 for i in range(44)] + [156.0, 138.0]


def _cross_day_double_dead_closes():
    """43 根稳步上涨 + 加速阳 + 中阴(-4，KDJ高位死叉先行) + 大阴(-12，MACD次日确认)
    → 双指标系异日同窗触发 → 双死叉共振 3 星（同日版 4 星的对照形态）。"""
    return [60 + i * 2.0 for i in range(43)] + [154.0, 150.0, 138.0]


def _underwater_dead_closes():
    """45 根深跌(-1.5) + 横盘 + 缺口大阳(+8) + 单根崩跌(收36) → MACD水下死叉
    落最新一根（崩跌使 DIF 回落击穿 DEA 且交叉时 DIF<0）。"""
    return [110 - i * 1.5 for i in range(45)] + [44.0, 52.0, 36.0]


def _kdj_dead_closes():
    """40 根横盘 + 1 根中阴(-6) → KDJ普通死叉（交叉时 D≈66，25~75 区间，
    不属高位死叉）。"""
    return [100.0] * 40 + [94.0]


def _ma20_break_closes():
    """40 根上行(+1.0) + 1 根收于 MA20×0.98 → 破位 MA20（前收在 MA20 上方、
    今收跌破 1% 缓冲线）。"""
    rise = [60 + i * 1.0 for i in range(40)]
    ma20 = sum(rise[-20:]) / 20
    return rise + [round(ma20 * 0.98, 2)]


def _top_divergence_closes():
    """双峰顶背离：急涨20根到峰1(90) → 回调5根(80) → 缓涨38根微破峰1(91.4)
    → 加速阳(95.4) → 大阴(88.4)：价格创 60 日新高而对应 DIF 远低于峰1 →
    顶背离 + KDJ高位死叉（+MACD水上死叉/破位MA20）同日落最新一根。"""
    shape = ([50 + 2.0 * (i + 1) for i in range(20)]
             + [90.0 - 2.0 * (i + 1) for i in range(5)]
             + [80.0 + 0.3 * (i + 1) for i in range(38)])
    return shape + [shape[-1] + 4.0, shape[-1] - 3.0]


def _bull_gap_up_closes():
    """买侧对照形态（021BP 深V缺口反弹）：45 根深跌 + 横盘 + 缺口大阳 →
    买点金叉落最新一根，卖侧零命中（买点结果不受卖侧扩展影响的保证）。"""
    return [110 - i * 1.5 for i in range(45)] + [44.0, 52.0]


_WEEKLY_UP = [100 + i * 1.0 for i in range(40)]     # 周线稳步上行 → 周线 DIF>DEA
_WEEKLY_DOWN = [140 - i * 1.0 for i in range(40)]   # 周线稳步下行 → 周线 DIF<DEA


def _dates(n, start=(2026, 9, 1)):
    d0 = dt.date(*start)
    return [(d0 + dt.timedelta(days=i)).isoformat() for i in range(n)]


def _mk_rows(closes, volumes=None):
    vols = volumes or [1000.0] * len(closes)
    return [
        {'date': d, 'open': c * 0.99, 'close': c, 'high': c * 1.01,
         'low': c * 0.98, 'volume': v}
        for d, c, v in zip(_dates(len(closes)), closes, vols)
    ]


def _insert_klines(conn, stock_id, closes, table='raw_kline'):
    conn.executemany(
        f'INSERT OR IGNORE INTO {table} '
        '(stock_id, trade_date, open, close, high, low, volume) '
        'VALUES (?, ?, ?, ?, ?, ?, ?)',
        [(stock_id, d, c * 0.99, c, c * 1.01, c * 0.98, 1000.0)
         for d, c in zip(_dates(len(closes)), closes)],
    )
    conn.commit()


def _add_stock_rule(stock_id, rule_type, threshold):
    """插入个股级规则（优先级高于全局规则）。"""
    conn = db_manager.get_connection()
    conn.execute(
        'INSERT INTO alert_rules (rule_type, stock_id, threshold, enabled) '
        'VALUES (?, ?, ?, 1)',
        (rule_type, stock_id, threshold),
    )
    conn.commit()
    conn.close()


def _seed_rating(stock_id, rating, score=72.0, report_date='2026-09-18'):
    """插入最新有效日报（评级上下文注记的数据源，021BQ 项④）。"""
    conn = db_manager.get_connection()
    conn.execute(
        'INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name, '
        'engine_version, total_score, rating, key_factors, status, report_type) '
        "VALUES (?, ?, '600519', '贵州茅台', 'v5', ?, ?, '{}', 'ok', 'daily')",
        (report_date, stock_id, score, rating),
    )
    conn.commit()
    conn.close()


def _alert_rows(alert_type):
    conn = db_manager.get_connection()
    rows = [dict(r) for r in conn.execute(
        'SELECT * FROM alert_history WHERE alert_type=?', (alert_type,)).fetchall()]
    conn.close()
    return rows


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """隔离临时库（含默认规则种子 + 1 只活跃自选股），返回 stock_id"""
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'test_sell_signal.db'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600519', 'a_stock', '贵州茅台')"
    )
    conn.commit()
    stock_id = conn.execute('SELECT id FROM stocks LIMIT 1').fetchone()['id']
    conn.close()
    return stock_id


# ---------------- 项A：平行库边界（在线扫描不泄漏卖点） ----------------

class TestParallelLibraryBoundary:
    def test_sell_keys_not_in_buy_library(self):
        """卖侧 key 严禁混入买侧库（否则泄漏进 run_signal_chunk 在线扫描）"""
        assert not set(SIGNAL_LIBRARY) & set(SELL_SIGNAL_LIBRARY)
        assert not set(RESONANCE_LIBRARY) & set(SELL_RESONANCE_LIBRARY)
        # 买侧库保持 2026-09-07 重设计后的 4 信号 4 共振原样
        assert set(SIGNAL_LIBRARY) == {
            'macd_golden_above', 'macd_golden_below', 'kdj_golden_low', 'kdj_golden'}
        assert set(RESONANCE_LIBRARY) == {
            'res_double_golden', 'res_week_daily', 'res_bottom_reverse', 'res_zero_relay'}

    def test_bear_kind_and_structure(self):
        """卖侧共振全部 kind='bear'；条目结构与买侧同构（label/stars/kind/note）"""
        assert SELL_RESONANCE_LIBRARY and SELL_SIGNAL_LIBRARY
        for lib in SELL_RESONANCE_LIBRARY.values():
            assert lib['kind'] == 'bear'
            assert {'label', 'stars', 'kind', 'note'} <= set(lib)
        for lib in SELL_SIGNAL_LIBRARY.values():
            assert {'label', 'note'} <= set(lib)

    def test_buy_detector_pure_on_sell_shape(self):
        """买侧 detect_signals 对死叉形态不产任何卖侧 key（选股器只产买点）"""
        rows = _mk_rows(_rally_gap_down_closes())
        buy_hits = detect_signals(rows)
        sell_keys = set(SELL_SIGNAL_LIBRARY)
        assert all(h['signal'] not in sell_keys for h in buy_hits)


# ---------------- 项A：卖出信号纯函数 ----------------

class TestSellSignalDetection:
    def test_same_day_double_dead(self):
        """水上死叉 + KDJ高位死叉同日落最新一根；双死叉共振 4 星同日升级"""
        rows = _mk_rows(_rally_gap_down_closes())
        hits = detect_sell_signals(rows)
        assert {h['signal'] for h in hits} == {'macd_dead_above', 'kdj_dead_high'}
        upto = rows[-1]['date']
        assert {h['trigger_date'] for h in hits} == {upto}  # 同日且 == 最新一根
        res = detect_sell_resonances(hits, rows)
        assert [r['key'] for r in res] == ['res_double_dead']
        assert res[0]['stars'] == 4
        assert '同日' in res[0]['note']

    def test_cross_day_double_dead_three_stars(self):
        """异日同窗双死叉 → 共振降档 3 星；MACD 死叉落最新一根"""
        rows = _mk_rows(_cross_day_double_dead_closes())
        hits = detect_sell_signals(rows)
        by_key = {h['signal']: h for h in hits}
        assert set(by_key) == {'macd_dead_above', 'kdj_dead_high'}
        assert by_key['macd_dead_above']['trigger_date'] == rows[-1]['date']
        assert by_key['kdj_dead_high']['trigger_date'] == rows[-2]['date']
        res = detect_sell_resonances(hits, rows)
        assert res[0]['key'] == 'res_double_dead'
        assert res[0]['stars'] == 3

    def test_underwater_dead(self):
        """水下死叉：崩跌收 36 → macd_dead_below 落最新一根；无 KDJ 死叉 → 无共振"""
        rows = _mk_rows(_underwater_dead_closes())
        hits = detect_sell_signals(rows)
        assert [h['signal'] for h in hits] == ['macd_dead_below']
        assert hits[0]['trigger_date'] == rows[-1]['date']
        assert detect_sell_resonances(hits, rows) == []

    def test_kdj_dead_normal_range(self):
        """KDJ普通死叉（交叉时 D≈66，非高位）：wanted 过滤下精确命中"""
        rows = _mk_rows(_kdj_dead_closes())
        hits = detect_sell_signals(rows, wanted=['kdj_dead_high', 'kdj_dead'])
        assert [h['signal'] for h in hits] == ['kdj_dead']
        assert hits[0]['trigger_date'] == rows[-1]['date']

    def test_ma20_break_event_semantics(self):
        """破位MA20：事件口径（前收在上方、今收跌破缓冲线）落最新一根"""
        rows = _mk_rows(_ma20_break_closes())
        hits = detect_sell_signals(rows, wanted=['ma20_break'])
        assert [h['signal'] for h in hits] == ['ma20_break']
        assert hits[0]['trigger_date'] == rows[-1]['date']
        # 全库口径下与水上死叉同日并存（涨势后破位的真实形态）
        full = detect_sell_signals(rows)
        assert {h['signal'] for h in full} == {'macd_dead_above', 'ma20_break'}

    def test_wanted_filter_excludes_other_signals(self):
        """wanted 传入单一 key 时其余信号不检测（预警/清单定向复算入口）"""
        rows = _mk_rows(_rally_gap_down_closes())
        assert detect_sell_signals(rows, wanted=['ma20_break']) == []

    def test_too_few_bars_returns_empty(self):
        """<35 根：门槛静默（与买侧 detect_signals 同门槛）"""
        rows = _mk_rows([100 - i for i in range(30)])
        assert detect_sell_signals(rows) == []
        result = compute_watchlist_sell_result(rows)
        assert result['sell_matches'] == []
        assert result['sell_resonances'] == []
        assert result['kline_count'] == 30
        assert result['kline_upto'] == rows[-1]['date']  # 数据新鲜度仍回报
        assert result['side'] == 'sell'

    def test_empty_rows(self):
        result = compute_watchlist_sell_result([])
        assert result['sell_matches'] == []
        assert result['kline_upto'] is None
        assert result['kline_count'] == 0


# ---------------- 项A：bear 共振 ----------------

class TestSellResonances:
    def test_week_bear_resonance(self):
        """周线空头(DIF<DEA) + 日线水下死叉 → 周线空头波段卖 5 星"""
        rows = _mk_rows(_underwater_dead_closes())
        weekly = _mk_rows(_WEEKLY_DOWN)
        hits = detect_sell_signals(rows)
        res = detect_sell_resonances(hits, rows, weekly)
        assert [r['key'] for r in res] == ['res_week_bear']
        assert res[0]['stars'] == 5
        assert res[0]['kind'] == 'bear'

    def test_top_reverse_resonance(self):
        """顶背离（价格新高而 DIF 未新高）+ KDJ高位死叉 → 顶背离反转卖 5 星"""
        rows = _mk_rows(_top_divergence_closes())
        weekly = _mk_rows(_WEEKLY_UP)
        hits = detect_sell_signals(rows)
        assert {h['trigger_date'] for h in hits} == {rows[-1]['date']}
        res = detect_sell_resonances(hits, rows, weekly)
        assert [r['key'] for r in res] == ['res_top_reverse']
        assert res[0]['stars'] == 5

    def test_tier_top_reverse_wins_over_week_bear(self):
        """取最高档：顶背离反转 > 周线空头 > 双死叉（先到先得，仅返回一档）"""
        rows = _mk_rows(_top_divergence_closes())
        weekly = _mk_rows(_WEEKLY_DOWN)  # 周线同样空头 → 两档同时成立
        hits = detect_sell_signals(rows)
        res = detect_sell_resonances(hits, rows, weekly)
        assert [r['key'] for r in res] == ['res_top_reverse']

    def test_volume_stall_annotation(self):
        """可选放量滞涨注记：末根放量收阴 → note 追加·放量滞涨"""
        closes = _top_divergence_closes()
        volumes = [1000.0] * (len(closes) - 1) + [3000.0]
        rows = _mk_rows(closes, volumes)
        # 末根改为真实阴线（_mk_rows 缺省 open=close*0.99 恒为阳线）
        rows[-1]['open'] = rows[-1]['close'] * 1.01
        hits = detect_sell_signals(rows)
        res = detect_sell_resonances(hits, rows)
        assert res[0]['key'] == 'res_top_reverse'
        assert res[0]['note'].endswith('·放量滞涨')

    def test_no_resonance_on_single_system(self):
        """单一指标系（仅 MACD 死叉）不产共振——与买侧 single_system 同语义"""
        rows = _mk_rows(_underwater_dead_closes())
        hits = detect_sell_signals(rows)
        assert detect_sell_resonances(hits, rows, _mk_rows(_WEEKLY_UP)) == []


# ---------------- 项B：卖侧巡检全链 ----------------

class TestScanSellChain:
    def test_scan_sell_full_chain(self, db):
        """库内日K+周K → 卖点信号 + bear 共振；口径标注 scope/side；交叉落最新一根"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _rally_gap_down_closes())
        _insert_klines(conn, db, _WEEKLY_DOWN, table='raw_kline_weekly')
        conn.close()

        result = scan_watchlist_sell_signals()
        assert result['scope'] == 'watchlist_offline'
        assert result['side'] == 'sell'
        assert result['stock_count'] == 1
        assert result['errors'] == []
        assert len(result['results']) == 1

        item = result['results'][0]
        assert item['stock_id'] == db
        assert item['symbol'] == '600519'
        assert {h['signal'] for h in item['sell_matches']} == {
            'macd_dead_above', 'kdj_dead_high'}
        assert {h['trigger_date'] for h in item['sell_matches']} == {item['kline_upto']}
        # 周线空头(5星) 取最高档（双死叉 4 星同成立但不返回）
        assert [r['key'] for r in item['sell_resonances']] == ['res_week_bear']
        assert item['kline_count'] == 46

    def test_scan_sell_stock_ids_filter(self, db):
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _rally_gap_down_closes())
        conn.close()

        hit = scan_watchlist_sell_signals(stock_ids=[db])
        assert hit['stock_count'] == 1 and len(hit['results']) == 1
        miss = scan_watchlist_sell_signals(stock_ids=[99999])
        assert miss['stock_count'] == 0 and miss['results'] == []

    def test_scan_sell_suspended_not_scanned(self, db):
        """巡检范围 = active 自选股（suspended 不参与）"""
        conn = db_manager.get_connection()
        conn.execute(
            "INSERT INTO stocks (symbol, market, name, status) "
            "VALUES ('000001', 'a_stock', '平安银行', 'suspended')"
        )
        conn.commit()
        sid2 = conn.execute(
            "SELECT id FROM stocks WHERE symbol='000001'").fetchone()['id']
        _insert_klines(conn, sid2, _rally_gap_down_closes())
        conn.close()

        result = scan_watchlist_sell_signals()
        assert result['stock_count'] == 1  # 仅 600519
        assert result['results'] == []

    def test_buy_scan_structure_unchanged(self, db):
        """向后兼容：买侧巡检响应结构不变（matches/resonances 键名原样）"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _bull_gap_up_closes())
        _insert_klines(conn, db, _WEEKLY_UP, table='raw_kline_weekly')
        conn.close()

        result = scan_watchlist_signals(stock_ids=[db])
        assert result['scope'] == 'watchlist_offline'
        assert len(result['results']) == 1
        item = result['results'][0]
        assert {h['signal'] for h in item['matches']} == {
            'macd_golden_below', 'kdj_golden_low'}
        assert [r['key'] for r in item['resonances']] == ['res_week_daily']
        assert 'side' not in result  # 买侧响应不带卖侧标注（结构零变化）
        # 买侧形态在卖侧巡检下零命中（双侧互不污染）
        sell_result = scan_watchlist_sell_signals(stock_ids=[db])
        assert sell_result['results'] == []


# ---------------- 项C：预警链路（sell_signal 第 5 类规则） ----------------

class TestSellAlertChain:
    def test_seed_and_whitelist_sync(self, db):
        """默认规则种子含全局 sell_signal（enabled=1 无阈值）+ 二次 init 幂等 +
        engine/blueprint 白名单同步 + 检查器注册"""
        conn = db_manager.get_connection()
        row = conn.execute(
            'SELECT threshold, enabled FROM alert_rules '
            "WHERE rule_type='sell_signal' AND stock_id IS NULL"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row['enabled'] == 1
        assert row['threshold'] is None

        # 二次 init 不重复种子
        db_manager.init_database()
        conn = db_manager.get_connection()
        n = conn.execute(
            "SELECT COUNT(*) AS c FROM alert_rules WHERE rule_type='sell_signal'"
        ).fetchone()['c']
        conn.close()
        assert n == 1

        # 白名单与检查器
        assert set(alerts_bp._VALID_ALERT_TYPES) == set(alert_engine.VALID_RULE_TYPES)
        assert 'sell_signal' in alert_engine.VALID_RULE_TYPES
        assert 'sell_signal' in alert_engine._RULE_CHECKERS

    def test_static_sync_surfaces(self):
        """前端静态面同步：alerts.js 三 map + index.html 下拉 + app.css 徽标
        （缺一即 UI 裸 key / 无样式徽标——021BP 八处同步清单的前端半边）"""
        root = Path(__file__).resolve().parents[1]
        js = (root / 'static' / 'js' / 'alerts.js').read_text(encoding='utf-8')
        assert js.count("'sell_signal'") >= 3  # 铃铛/规则管理标签 map + 规则提示 map
        html = (root / 'templates' / 'index.html').read_text(encoding='utf-8')
        assert 'value="sell_signal"' in html
        css = (root / 'static' / 'css' / 'app.css').read_text(encoding='utf-8')
        assert '.alert-item-type.sell_signal' in css

    def test_sell_alert_written_and_idempotent(self, db):
        """命中写 alert_history（走既有写入路径）；同日二次扫描幂等跳过"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _rally_gap_down_closes())
        _insert_klines(conn, db, _WEEKLY_DOWN, table='raw_kline_weekly')
        conn.close()

        first = alert_engine.scan_once()
        assert first['success'] is True
        assert first['triggered'] == 1  # 仅卖侧触发；买侧"今日出现"口径不命中
        assert first['errors'] == 0

        rows = _alert_rows('sell_signal')
        assert len(rows) == 1
        row = rows[0]
        assert '贵州茅台(600519)' in row['message']
        assert '今日出现卖点信号' in row['message']
        assert 'MACD水上死叉' in row['message']
        assert 'KDJ高位死叉' in row['message']
        assert '周线空头波段卖' in row['message']
        assert '<' not in row['message']  # 021BN 教训：渲染文案禁止裸 '<'
        detail = json.loads(row['trigger_value'])
        assert {s['signal'] for s in detail['signals']} == {
            'macd_dead_above', 'kdj_dead_high'}
        assert detail['resonances'][0]['stars'] == 5
        assert 'kline_upto' in detail
        # 无日报评级行 → 无上下文注记（current_rating=None）
        assert detail['current_rating'] is None
        assert detail['rating_conflict'] is False

        # 同日二次扫描：命中照旧，UNIQUE(rule_id, stock_id, trigger_date) 幂等
        second = alert_engine.scan_once()
        assert second['success'] is True
        assert second['triggered'] == 0
        assert second['skipped_idempotent'] == 1
        assert len(_alert_rows('sell_signal')) == 1

    def test_no_alert_when_signal_not_on_latest_bar(self, db):
        """新鲜度门槛：窗口内命中但触发日非最新一根 → 前日巡检已覆盖，不提醒"""
        closes = _rally_gap_down_closes() + [138.0]  # 追加1根横盘
        conn = db_manager.get_connection()
        _insert_klines(conn, db, closes)
        conn.close()

        # 巡检层（候选展示口径）仍回报窗口命中；预警层按"今日出现"收敛
        result = scan_watchlist_sell_signals(stock_ids=[db])
        assert len(result['results']) == 1
        assert alert_engine.scan_once()['triggered'] == 0
        assert _alert_rows('sell_signal') == []

    def test_no_alert_when_cross_out_of_window(self, db):
        """交叉发生在窗口外（追加3根横盘隔开）→ 巡检与预警都不报"""
        closes = _rally_gap_down_closes() + [138.0] * 3
        conn = db_manager.get_connection()
        _insert_klines(conn, db, closes)
        conn.close()

        assert scan_watchlist_sell_signals(stock_ids=[db])['results'] == []
        assert alert_engine.scan_once()['triggered'] == 0

    def test_min_stars_gate_suppresses_below_threshold(self, db):
        """星级门槛：无周K → 最高共振=同日双死叉4星；个股规则门槛5星 → 不提醒"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _rally_gap_down_closes())
        conn.close()
        _add_stock_rule(db, 'sell_signal', 5)

        result = alert_engine.scan_once()
        assert result['success'] is True
        assert result['triggered'] == 0
        assert _alert_rows('sell_signal') == []

    def test_min_stars_gate_passes_on_weekly_resonance(self, db):
        """星级门槛：周线空头5星 ≥ 门槛 → 提醒且个股规则优先于全局规则"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _rally_gap_down_closes())
        _insert_klines(conn, db, _WEEKLY_DOWN, table='raw_kline_weekly')
        conn.close()
        _add_stock_rule(db, 'sell_signal', 5)

        result = alert_engine.scan_once()
        assert result['triggered'] == 1
        rows = _alert_rows('sell_signal')
        assert len(rows) == 1
        assert '周线空头波段卖' in rows[0]['message']

    def test_insufficient_klines_skip_silently(self, db):
        """K线不足35根：静默跳过，不报错不提醒"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, [100 - i for i in range(20)])
        conn.close()

        result = alert_engine.scan_once()
        assert result['success'] is True
        assert result['triggered'] == 0
        assert result['errors'] == 0

    def test_no_klines_no_alert_no_error(self, db):
        """无任何K线：静默跳过"""
        result = alert_engine.scan_once()
        assert result['success'] is True
        assert result['triggered'] == 0
        assert result['errors'] == 0
        assert _alert_rows('sell_signal') == []


# ---------------- 项④：评级上下文（相悖调和注记） ----------------

class TestRatingContext:
    def test_sell_signal_conflict_with_buy_rating(self, db):
        """卖点信号 × 买入档评级 → 消息附调和注记（评级为主，信号仅波段参考）"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _rally_gap_down_closes())
        conn.close()
        _seed_rating(db, '推荐买入')

        assert alert_engine.scan_once()['triggered'] == 1
        rows = _alert_rows('sell_signal')
        assert len(rows) == 1
        assert '今日出现卖点信号' in rows[0]['message']
        assert '当前评级「推荐买入」' in rows[0]['message']
        assert '仅波段参考，以评级为主' in rows[0]['message']
        assert '<' not in rows[0]['message']
        detail = json.loads(rows[0]['trigger_value'])
        assert detail['current_rating'] == '推荐买入'
        assert detail['rating_conflict'] is True

    def test_buy_signal_conflict_with_reduce_rating(self, db):
        """买点信号 × 减仓档评级 → 买侧消息同样附调和注记（镜像对称）"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _bull_gap_up_closes())
        conn.close()
        _seed_rating(db, '建议减仓')

        result = alert_engine.scan_once()
        assert result['triggered'] == 1  # 仅 tech_signal；卖侧对买点形态零命中
        tech_rows = _alert_rows('tech_signal')
        assert len(tech_rows) == 1
        assert '今日出现买点信号' in tech_rows[0]['message']
        assert '当前评级「建议减仓」' in tech_rows[0]['message']
        assert '仅波段参考，以评级为主' in tech_rows[0]['message']
        assert _alert_rows('sell_signal') == []

    def test_aligned_rating_no_note(self, db):
        """卖点信号 × 减仓档评级（方向一致）→ 不附注记"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _rally_gap_down_closes())
        conn.close()
        _seed_rating(db, '强烈建议卖出')

        assert alert_engine.scan_once()['triggered'] == 1
        rows = _alert_rows('sell_signal')
        assert '仅波段参考' not in rows[0]['message']
        detail = json.loads(rows[0]['trigger_value'])
        assert detail['current_rating'] == '强烈建议卖出'
        assert detail['rating_conflict'] is False

    def test_stale_rating_row_ignored(self, db):
        """仅取最新有效日报：更晚的 ok 行覆盖更早行；status 非 ok 行不计"""
        conn = db_manager.get_connection()
        _insert_klines(conn, db, _rally_gap_down_closes())
        conn.close()
        _seed_rating(db, '持有观望', report_date='2026-09-10')
        _seed_rating(db, '推荐买入', report_date='2026-09-18')
        # status='error' 的更晚行不参与（status 过滤）
        conn = db_manager.get_connection()
        conn.execute(
            'INSERT INTO daily_reports (report_date, stock_id, stock_code, stock_name, '
            'rating, status, report_type) '
            "VALUES ('2026-09-19', ?, '600519', '贵州茅台', '强烈推荐买入', 'error', 'daily')",
            (db,),
        )
        conn.commit()
        conn.close()

        assert alert_engine.scan_once()['triggered'] == 1
        rows = _alert_rows('sell_signal')
        # 取最新 ok 行（09-18 推荐买入 → 相悖注记），而非 error 行的强烈推荐买入
        assert '当前评级「推荐买入」' in rows[0]['message']
        assert '强烈推荐买入' not in rows[0]['message']
        detail = json.loads(rows[0]['trigger_value'])
        assert detail['current_rating'] == '推荐买入'
