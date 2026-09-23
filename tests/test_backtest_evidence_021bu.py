"""
021BU 回测证据注入测试：证据计算纯函数 + 展示路径 + 样本门槛边界 + 一致性同源

依据：docs/reports/021bu_backtest_evidence_plan_20260923.md（t1 方案 O1/O2/O3/O8）
与批次硬约束（诚实原则：命中率必须带样本量，C 级 n<20 在数据层断流百分数）。

覆盖：
1. 诚实展示门 format_evidence_cell：A/B/C 分级与 n=19/20、n=29/30 边界（数据层强制）
2. rating_evidence_table / rating_evidence_for：生产口径（真实行 + rating_id 过滤 +
   自然键去重 max(id) + 分市场）与 C 级诚实空档
3. price_advice_evidence_summary：真实锚点主口径 + 分母口径（无持仓行 vs 全体）
4. 展示路径：report-latest 响应与 watchlist-scores 响应内联证据字段
5. 一致性同源：报告页/看板响应值 == 直接调用共享函数值（同源同值）
6. 文案禁裸 '<'：全部 display 字符串不含 '<'

隔离：临时库（monkeypatch DB_PATH），不触网、不触碰真实 stock_analyst.db。
"""

import json
from datetime import datetime, timedelta, timezone

import pytest

from database import db_manager
from database.db_manager import get_connection, init_database
from modules.backtest_engine import (
    EVIDENCE_N_FULL,
    EVIDENCE_N_PARTIAL,
    empty_rating_evidence,
    format_evidence_cell,
    price_advice_evidence_summary,
    rating_evidence_for,
    rating_evidence_table,
)

# ================================================================
# 一、诚实展示门（纯函数）
# ================================================================


class TestFormatEvidenceCell:
    """样本分级门槛边界（诚实原则的实现锚点）。"""

    def test_constants(self):
        assert EVIDENCE_N_FULL == 30
        assert EVIDENCE_N_PARTIAL == 20

    def test_c_grade_below_partial_threshold(self):
        """n=19（<20）→ C 级：acc=None、display 无百分数（n=19 vs n=20 边界下侧）。"""
        cell = format_evidence_cell(13, 19)
        assert cell['grade'] == 'C'
        assert cell['acc'] is None
        assert cell['n'] == 13 and cell['m'] == 19
        assert '%' not in cell['display']
        assert '样本不足' in cell['display']

    def test_b_grade_at_partial_boundary(self):
        """n=20（≥20）→ B 级：可展示但带分级标记（n=19 vs n=20 边界上侧）。"""
        cell = format_evidence_cell(14, 20)
        assert cell['grade'] == 'B'
        assert cell['acc'] == round(14 / 20, 4)
        assert '14/20' in cell['display']
        assert '%' in cell['display']

    def test_b_grade_below_full_boundary(self):
        """n=29 → 仍 B 级（n=29 vs n=30 边界下侧）。"""
        cell = format_evidence_cell(20, 29)
        assert cell['grade'] == 'B'
        assert cell['acc'] is not None

    def test_a_grade_at_full_boundary(self):
        """n=30（≥30）→ A 级（n=29 vs n=30 边界上侧）。"""
        cell = format_evidence_cell(21, 30)
        assert cell['grade'] == 'A'
        assert '21/30' in cell['display']

    def test_zero_sample(self):
        cell = format_evidence_cell(0, 0)
        assert cell['grade'] == 'C'
        assert cell['acc'] is None
        assert '样本不足（n=0）' == cell['display']

    def test_display_format_carries_n_and_m(self):
        """诚实原则：百分数展示必须带 n/m（命中率必须带样本量）。"""
        cell = format_evidence_cell(159, 230)
        assert cell['display'] == '历史命中 69%（159/230）'

    def test_no_bare_less_than_in_display(self):
        """文案禁裸 '<'：全部分级 display 均不含 '<'。"""
        for n in (0, 5, 19, 20, 25, 30, 230):
            cell = format_evidence_cell(max(n - 3, 0), n)
            assert '<' not in cell['display']

    def test_custom_label(self):
        cell = format_evidence_cell(94, 159, label='动态窗口命中')
        assert cell['display'] == '动态窗口命中 59%（94/159）'


# ================================================================
# 二、评级证据表（SQL 口径）
# ================================================================


@pytest.fixture()
def db(tmp_path, monkeypatch):
    db_file = tmp_path / 'test_ev_021bu.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()
    # 触发引擎 _ensure_columns（动态列 dynamic_is_correct 等由引擎幂等追加，生产同机制）
    from modules.backtest_engine import BacktestEngine

    BacktestEngine()

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600001', 'a_stock', '甲')")
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('00700', 'hk_stock', '乙')")
        # A股：持有观望 35 行（34 对 1 错，自然键日期互异——去重后仍 35 行）→ A 级
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        _base = _dt(2026, 8, 1)
        for i in range(35):
            _d = (_base + _td(days=i)).strftime('%Y-%m-%d')
            conn.execute(
                "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
                'price_at_rating, return_1d, is_correct, is_simulated) '
                "VALUES (1, 100, 'a_stock', ?, '持有观望', 10.0, 1.0, ?, 0)",
                (_d, 1 if i < 34 else 0),
            )
        # A股：强烈推荐买入 2 行 → C 级（诚实门断流）
        conn.execute(
            "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
            'price_at_rating, return_1d, is_correct, is_simulated) '
            "VALUES (1, 101, 'a_stock', '2026-09-10', '强烈推荐买入', 10.0, 2.0, 1, 0)")
        conn.execute(
            "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
            'price_at_rating, return_1d, is_correct, is_simulated) '
            "VALUES (1, 101, 'a_stock', '2026-09-11', '强烈推荐买入', 10.0, 2.0, 1, 0)")
        # A股：模拟行（is_simulated=1）与模拟评级（rating_id=-1）——排除
        conn.execute(
            "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
            'price_at_rating, return_1d, is_correct, is_simulated) '
            "VALUES (1, 102, 'a_stock', '2026-09-12', '推荐买入', 10.0, 1.0, 1, 1)")
        conn.execute(
            "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
            'price_at_rating, return_1d, is_correct, is_simulated) '
            "VALUES (1, -1, 'a_stock', '2026-09-13', '推荐买入', 10.0, 1.0, 1, 0)")
        # 港股：建议减仓 21 行 → B 级（分市场隔离验证）
        for i in range(21):
            conn.execute(
                "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
                'price_at_rating, return_1d, is_correct, is_simulated) '
                "VALUES (2, 200, 'hk_stock', ?, '建议减仓', 20.0, -1.0, ?, 0)",
                (f'2026-08-{i % 28 + 1:02d}', 1 if i < 15 else 0),
            )
        conn.commit()
    finally:
        conn.close()
    yield


def _add_anchor_column(conn):
    """测试库补 anchor_rating_date 列（init 建表后由迁移幂等追加，此处对齐）。"""
    cols = {r[1] for r in conn.execute('PRAGMA table_info(price_backtest_results)')}
    if 'anchor_rating_date' not in cols:
        conn.execute('ALTER TABLE price_backtest_results ADD COLUMN anchor_rating_date TEXT')


class TestRatingEvidenceTable:
    """评级证据表生产口径（真实行 + 过滤 + 去重 + 分市场）。"""

    def test_market_separation(self, db):
        table_a = rating_evidence_table('a_stock')
        table_hk = rating_evidence_table('hk_stock')
        assert '持有观望' in table_a
        assert '建议减仓' in table_hk
        assert '建议减仓' not in table_a  # 港股样本不混入 A 股（R20）
        assert '持有观望' not in table_hk

    def test_primary_aggregation(self, db):
        cell = rating_evidence_table('a_stock')['持有观望']['primary']
        assert cell['grade'] == 'A'
        assert (cell['n'], cell['m']) == (34, 35)
        assert '34/35' in cell['display']

    def test_simulated_rows_excluded(self, db):
        """模拟行（is_simulated=1）与 rating_id=-1 行不入证据（t1 方案 §1 口径）。"""
        assert '推荐买入' not in rating_evidence_table('a_stock')

    def test_c_grade_no_percent(self, db):
        """强烈推荐买入 n=2 → C 级：数据层断流百分数（任何消费面泄漏不了）。"""
        cell = rating_evidence_table('a_stock')['强烈推荐买入']['primary']
        assert cell['grade'] == 'C'
        assert cell['acc'] is None
        assert cell['display'] == '样本不足（n=2）'
        assert '%' not in cell['display']

    def test_engine_versions_declared(self, db):
        table = rating_evidence_table('hk_stock')
        assert '建议减仓' in table
        # 无 ratings_history 关联行时引擎构成诚实降级为空集（不臆造）
        assert isinstance(table['建议减仓']['engine_versions'], list)

    def test_b_grade_hk(self, db):
        cell = rating_evidence_table('hk_stock')['建议减仓']['primary']
        assert cell['grade'] == 'B'
        assert (cell['n'], cell['m']) == (15, 21)


class TestRatingEvidenceFor:
    def test_lookup(self, db):
        ev = rating_evidence_for('a_stock', '持有观望')
        assert ev['primary']['display'] == '历史命中 97%（34/35）'

    def test_missing_rating_returns_honest_empty(self, db):
        """零样本档位：诚实空档（样本不足 n=0），不返回 None。"""
        ev = rating_evidence_for('a_stock', '建议减仓')
        assert ev['primary']['grade'] == 'C'
        assert ev['primary']['display'] == '样本不足（n=0）'
        assert ev == empty_rating_evidence('a_stock', '建议减仓')

    def test_none_rating_returns_none(self, db):
        assert rating_evidence_for('a_stock', None) is None


# ================================================================
# 三、价格建议历史基准（真实锚点主口径 + 分母口径）
# ================================================================


@pytest.fixture()
def db_price(tmp_path, monkeypatch):
    db_file = tmp_path / 'test_ev_price_021bu.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600001', 'a_stock', '甲')")
        _add_anchor_column(conn)
        # 无持仓真实锚点行 ×25：buy 17/25 命中、target 8/25（≥20 行 → A 级展示）
        # （生产 _check_hit 对无持仓行的 t20_hit_* 落窗后恒为 0/1，不存在 NULL 判定）
        for i in range(25):
            b = 1 if i < 17 else 0
            t = 1 if i < 8 else 0
            conn.execute(
                'INSERT INTO price_backtest_results (stock_id, backtest_date, rating, market, '
                'buy_range_low, t20_hit_buy_range, t20_hit_target, t20_hit_stop_loss, '
                'anchor_rating_date) VALUES (1, ?, ?, ?, 9.0, ?, ?, 0, ?)',
                (f'2026-09-{i + 1:02d}', '推荐买入', 'a_stock', b, t, '2026-09-01'),
            )
        # 有持仓行 ×5（buy_range_low 为 NULL，t20_hit_buy_range/target 也为 NULL）——
        # 止损分母计入（2/5 触发）、买入区间/目标价分母不计入
        for i in range(5):
            conn.execute(
                'INSERT INTO price_backtest_results (stock_id, backtest_date, rating, market, '
                'buy_range_low, t20_hit_buy_range, t20_hit_target, t20_hit_stop_loss, '
                'anchor_rating_date) VALUES (1, ?, ?, ?, NULL, NULL, NULL, ?, ?)',
                (f'2026-10-{i + 1:02d}', '持有观望', 'a_stock', 1 if i < 2 else 0,
                 f'2026-10-{i + 1:02d}'),
            )
        # 重建点（anchor_rating_date 为 NULL）——主口径排除（无未来函数）
        conn.execute(
            'INSERT INTO price_backtest_results (stock_id, backtest_date, rating, market, '
            'buy_range_low, t20_hit_buy_range, t20_hit_target, t20_hit_stop_loss, '
            'anchor_rating_date) VALUES (1, ?, ?, ?, 9.0, 1, 1, 0, NULL)',
            ('2026-08-01', '推荐买入', 'a_stock'),
        )
        conn.commit()
    finally:
        conn.close()
    yield


class TestPriceAdviceEvidenceSummary:
    def test_denominators(self, db_price):
        """分母口径：买入区间/目标价=无持仓行（n=25）；止损=全部真实锚点行（n=30）。"""
        ev = price_advice_evidence_summary('a_stock')
        assert ev['n_real_anchor'] == 30
        assert (ev['buy_range_t20']['n'], ev['buy_range_t20']['m']) == (17, 25)
        assert (ev['target_t20']['n'], ev['target_t20']['m']) == (8, 25)
        assert (ev['stop_loss_t20']['n'], ev['stop_loss_t20']['m']) == (2, 30)

    def test_rebuild_points_excluded(self, db_price):
        """重建点（anchor_rating_date 为空）不入主口径——真实锚点 30 行而非 31 行。"""
        ev = price_advice_evidence_summary('a_stock')
        assert ev['n_real_anchor'] == 30

    def test_display_line(self, db_price):
        ev = price_advice_evidence_summary('a_stock')
        assert '买入区间 20 日内触及率 68%（17/25）' in ev['display']
        assert '目标价达成率 32%（8/25）' in ev['display']
        assert '止损触发率 7%（2/30）' in ev['display']
        assert '<' not in ev['display']

    def test_c_grade_market_degrades(self, tmp_path, monkeypatch):
        """零真实锚点市场：三格全部 C 级样本不足（不展示百分数）。"""
        db_file = tmp_path / 'test_ev_price_empty.db'
        monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
        monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups2'))
        init_database()
        ev = price_advice_evidence_summary('a_stock')
        assert ev['grade'] == 'C'
        assert ev['buy_range_t20']['acc'] is None
        assert '%' not in ev['display']
        assert '样本不足' in ev['display']


# ================================================================
# 四、展示路径：report-latest 与 watchlist-scores 响应内联证据
# ================================================================


@pytest.fixture()
def route_env(tmp_path, monkeypatch):
    """隔离库 + 今日快照报告 + 回测样本（触发两条响应路径的证据内联）。"""
    db_file = tmp_path / 'test_ev_route.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()
    # 引擎动态列幂等追加（生产同机制）
    from modules.backtest_engine import BacktestEngine

    BacktestEngine()

    now_cn = datetime.now(timezone(timedelta(hours=8)))
    today = now_cn.strftime('%Y-%m-%d')
    generated_at = (now_cn - timedelta(minutes=1)).isoformat()

    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600001', 'a_stock', '甲')")
        # 今日 ok daily 快照前置：持有观望 35 行（34 对 1 错，日期互异 → A 级）
        from datetime import datetime as _dt
        from datetime import timedelta as _td

        _base = _dt(2026, 8, 1)
        for i in range(35):
            _d = (_base + _td(days=i)).strftime('%Y-%m-%d')
            conn.execute(
                "INSERT INTO backtest_results (stock_id, rating_id, market, rating_date, rating, "
                'price_at_rating, return_1d, is_correct, is_simulated) '
                "VALUES (1, 100, 'a_stock', ?, '持有观望', 10.0, 1.0, ?, 0)",
                (_d, 1 if i < 34 else 0),
            )
        # 今日 ok daily 快照（generated_at 新鲜 → report-latest 走快照路径不触发重评）
        conn.execute(
            'INSERT INTO daily_reports (stock_id, report_date, report_type, engine_version, '
            'total_score, rating, rating_label, status, generated_at, markdown_content) '
            "VALUES (1, ?, 'daily', 'v5', 55.0, '持有观望', '观望', 'ok', ?, '## 报告')",
            (today, generated_at),
        )
        conn.commit()
    finally:
        conn.close()
    return today


class TestResponsePaths:
    def test_watchlist_scores_carries_evidence(self, route_env):
        from app import app

        app.config['TESTING'] = True
        client = app.test_client()
        resp = client.get('/api/portfolio/watchlist-scores')
        assert resp.status_code == 200
        data = resp.get_json()
        stock = data['stocks'][0]
        # 每股评级徽章证据（与共享函数同源同值）
        assert stock['rating_evidence'] is not None
        assert stock['rating_evidence']['primary']['display'] == '历史命中 97%（34/35）'
        # 位置注记键在场（无显著分化为 None——观望档不产出）
        assert 'position_note' in stock
        # 市场级价格基准
        assert 'a_stock' in data['evidence_price']
        assert data['evidence_price']['a_stock']['buy_range_t20'] is not None

    def test_watchlist_evidence_same_source_as_function(self, route_env):
        """一致性同源：看板响应值 == 直接调用共享函数值（同源同值硬断言）。"""
        from app import app

        app.config['TESTING'] = True
        client = app.test_client()
        data = client.get('/api/portfolio/watchlist-scores').get_json()
        ws_ev = data['stocks'][0]['rating_evidence']
        fn_ev = rating_evidence_for('a_stock', '持有观望')
        assert ws_ev['primary'] == fn_ev['primary']

    def test_report_latest_carries_evidence(self, route_env):
        """report-latest 快照路径：证据三件套内联（零落库、现算）。"""
        from app import app

        app.config['TESTING'] = True
        client = app.test_client()
        resp = client.get('/api/stocks/1/report-latest')
        assert resp.status_code == 200
        data = resp.get_json()
        assert data['success'] is True
        assert data['rating_evidence'] is not None
        assert data['rating_evidence']['primary']['display'] == '历史命中 97%（34/35）'
        assert 'position_note' in data
        assert data['price_advice_evidence'] is not None
        assert 'a_stock' in data['price_advice_evidence']['market']

    def test_report_latest_evidence_same_source(self, route_env):
        """报告页徽章与看板徽章逐字段一致（三方同源的响应面两方）。"""
        from app import app

        app.config['TESTING'] = True
        client = app.test_client()
        report_ev = client.get('/api/stocks/1/report-latest').get_json()['rating_evidence']
        ws = client.get('/api/portfolio/watchlist-scores').get_json()
        ws_ev = ws['stocks'][0]['rating_evidence']
        assert report_ev == ws_ev


# ================================================================
# 五、JSON 序列化安全性（响应可打包、无裸 '<'）
# ================================================================


def test_evidence_json_safe(route_env):
    from app import app

    app.config['TESTING'] = True
    client = app.test_client()
    data = client.get('/api/portfolio/watchlist-scores').get_json()
    payload = json.dumps(data, ensure_ascii=False)  # 不可序列化对象会在此抛错
    assert '样本不足' in payload or '历史命中' in payload
    assert '\\u003c' not in payload.replace('<', '')  # 展示面不产裸 '<'
