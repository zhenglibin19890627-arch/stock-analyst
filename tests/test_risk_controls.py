"""
风控敏感逻辑单元测试（P1-4 补充）

覆盖三个风控分支（config.py 中的红线配置）：
1. 成本调整二次确认 —— COST_ADJUSTMENT_DEVIATION_THRESHOLD=±30%
   - 小偏离直接成功 / 大偏离需 force_confirm / force_confirm=true 放行
   - 24h 冷却期内禁止二次修正（COST_ADJUSTMENT_COOLDOWN_HOURS=24）
   - 非法输入：负值 / 缺 reason / 持仓不存在
2. 流水 T+1 锁定 —— TRADE_T1_LOCK_ENABLED=True
   - 当日提交的流水编辑/删除被拒（403）
   - 隔日流水可编辑/删除（200）
   - 已清算（quantity=0）禁止编辑/删除历史流水（403）
3. 5 万二次验证 —— TRADE_AMOUNT_VERIFY_THRESHOLD=50000
   - [SKIP] 后端当前未实现强制校验（_check_trade_edit_restriction 仅有注释，
     前端亦无对应交互，2026-08-12 确认）；实现后移除 skip 启用本测试。

隔离原则：
- monkeypatch database.db_manager.DB_PATH 指向 pytest tmp_path 临时库
- init_database() 在临时库建表（含 ALTER 迁移列），不触网、不依赖现有 stock_analyst.db
- 通过 Flask test_client 调用真实 API 路由，覆盖完整风控分支
"""

from datetime import datetime, timedelta

import pytest
from database import db_manager
from database.db_manager import get_connection, init_database

# 与 config.py 保持一致（测试内显式引用，便于将来阈值调整时同步修改）
DEVIATION_THRESHOLD = 0.30
COOLDOWN_HOURS = 24
T1_LOCK_ENABLED = True
AMOUNT_VERIFY_THRESHOLD = 50000


@pytest.fixture()
def db(tmp_path, monkeypatch):
    """临时 SQLite 库：monkeypatch DB_PATH → tmp_path 后 init_database 建表并插入基础数据。"""
    db_file = tmp_path / 'test_risk_controls.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    # init_database 的破坏性迁移会触发 backup_database，重定向备份目录避免污染真实 backups/
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()

    conn = get_connection()
    # 股票 1：正常持仓（成本 10.0、数量 100）；股票 2：已清算（数量 0）
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600001', 'a_stock', '风控测试股A')"
    )
    conn.execute(
        "INSERT INTO stocks (symbol, market, name) VALUES ('600002', 'a_stock', '风控测试股B')"
    )
    conn.execute(
        "INSERT INTO holdings (stock_id, cost_price, quantity) VALUES (1, 10.0, 100)"
    )
    conn.execute(
        "INSERT INTO holdings (stock_id, cost_price, quantity) VALUES (2, 20.0, 0)"
    )
    conn.commit()
    conn.close()
    return db_file


@pytest.fixture()
def client(db):
    """Flask test_client（导入 app 模块并启用 TESTING）。"""
    from app import app

    app.config['TESTING'] = True
    return app.test_client()


def _insert_trade(conn, stock_id, holding_id, created_at, amount=1000.0):
    """插入一条 buy 流水，created_at 可指定（用于 T+1 锁定测试）。"""
    conn.execute(
        "INSERT INTO trade_records (holding_id, stock_id, trade_type, price, quantity, amount, trade_date, created_at) "
        "VALUES (?, ?, 'buy', 10.0, 10, ?, date('now', 'localtime'), ?)",
        (holding_id, stock_id, amount, created_at),
    )
    conn.commit()


def _last_trade_id(conn):
    row = conn.execute('SELECT id FROM trade_records ORDER BY id DESC LIMIT 1').fetchone()
    return row[0]


# ============================================================
# 1. 成本调整二次确认（±30% 偏离阈值）
# ============================================================


class TestCostAdjustment:
    def test_small_deviation_ok_without_force(self, client):
        """10.0 → 11.0：偏离 10% < 30%，无需二次确认直接成功。"""
        resp = client.post(
            '/api/positions/1/cost-adjustment',
            json={'adjusted_avg_cost': 11.0, 'adjustment_reason': '分红除权'},
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['adjustment']['old_cost'] == 10.0
        assert body['adjustment']['new_cost'] == 11.0

    def test_large_deviation_blocked_without_force(self, client):
        """10.0 → 15.0：偏离 50% > 30%，拒绝且不落库。"""
        resp = client.post(
            '/api/positions/1/cost-adjustment',
            json={'adjusted_avg_cost': 15.0, 'adjustment_reason': '系统计算偏差'},
        )
        assert resp.status_code == 400
        body = resp.get_json()
        assert body['success'] is False
        assert body['need_force_confirm'] is True
        assert body['deviation_pct'] == pytest.approx(0.5)
        # 拒绝时不得写入修正记录
        conn = get_connection()
        n = conn.execute('SELECT COUNT(*) FROM position_cost_adjustments').fetchone()[0]
        conn.close()
        assert n == 0

    def test_large_deviation_ok_with_force_confirm(self, client):
        """大偏离 + force_confirm=true：放行并留痕。"""
        resp = client.post(
            '/api/positions/1/cost-adjustment',
            json={
                'adjusted_avg_cost': 15.0,
                'adjustment_reason': '拆股合股',
                'force_confirm': True,
            },
        )
        assert resp.status_code == 200
        body = resp.get_json()
        assert body['success'] is True
        assert body['adjustment']['old_cost'] == 10.0
        assert body['adjustment']['new_cost'] == 15.0
        conn = get_connection()
        row = conn.execute(
            'SELECT deviation_pct FROM position_cost_adjustments ORDER BY id DESC LIMIT 1'
        ).fetchone()
        conn.close()
        assert row[0] == pytest.approx(0.5)

    def test_cooldown_blocks_second_adjustment(self, client):
        """24h 冷却：第一次成功后，冷却期内第二次修正返回 429。"""
        assert (
            client.post(
                '/api/positions/1/cost-adjustment',
                json={'adjusted_avg_cost': 11.0, 'adjustment_reason': '分红除权'},
            ).status_code
            == 200
        )
        resp = client.post(
            '/api/positions/1/cost-adjustment',
            json={'adjusted_avg_cost': 12.0, 'adjustment_reason': '手续费补录'},
        )
        assert resp.status_code == 429
        assert str(COOLDOWN_HOURS) in resp.get_json()['message']

    def test_negative_cost_rejected(self, client):
        resp = client.post(
            '/api/positions/1/cost-adjustment',
            json={'adjusted_avg_cost': -5.0, 'adjustment_reason': '测试'},
        )
        assert resp.status_code == 400

    def test_missing_reason_rejected(self, client):
        resp = client.post(
            '/api/positions/1/cost-adjustment',
            json={'adjusted_avg_cost': 11.0},
        )
        assert resp.status_code == 400

    def test_holding_not_found(self, client):
        resp = client.post(
            '/api/positions/999/cost-adjustment',
            json={'adjusted_avg_cost': 11.0, 'adjustment_reason': '测试'},
        )
        assert resp.status_code == 404


# ============================================================
# 2. 流水 T+1 锁定
# ============================================================


class TestTradeT1Lock:
    def test_today_trade_edit_blocked(self, client, db):
        """当日提交的流水：编辑被拒（403 T+1）。"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        _insert_trade(conn, 1, 1, now)
        trade_id = _last_trade_id(conn)
        conn.close()

        resp = client.put(f'/api/portfolio/trades/{trade_id}', json={'notes': '改备注'})
        assert resp.status_code == 403
        assert 'T+1' in resp.get_json()['message']

    def test_today_trade_delete_blocked(self, client, db):
        """当日提交的流水：删除被拒（403 T+1）。"""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        _insert_trade(conn, 1, 1, now)
        trade_id = _last_trade_id(conn)
        conn.close()

        resp = client.delete(f'/api/portfolio/trades/{trade_id}')
        assert resp.status_code == 403
        assert 'T+1' in resp.get_json()['message']

    def test_previous_day_trade_edit_allowed(self, client, db):
        """隔日流水：可编辑（200）。"""
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        _insert_trade(conn, 1, 1, two_days_ago)
        trade_id = _last_trade_id(conn)
        conn.close()

        resp = client.put(f'/api/portfolio/trades/{trade_id}', json={'notes': '改备注'})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_previous_day_trade_delete_allowed(self, client, db):
        """隔日流水：可删除（200）。"""
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        _insert_trade(conn, 1, 1, two_days_ago)
        trade_id = _last_trade_id(conn)
        conn.close()

        resp = client.delete(f'/api/portfolio/trades/{trade_id}')
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_cleared_position_blocks_trade_edit(self, client, db):
        """已清算（quantity=0）：历史流水禁止编辑（403）。"""
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        _insert_trade(conn, 2, 2, two_days_ago)  # 股票2 持仓数量=0
        trade_id = _last_trade_id(conn)
        conn.close()

        resp = client.put(f'/api/portfolio/trades/{trade_id}', json={'notes': '改备注'})
        assert resp.status_code == 403
        assert '已清算' in resp.get_json()['message']


# ============================================================
# 3. 5 万二次验证 —— 大额流水编辑/删除需 force_confirm=true 放行
# ============================================================


class TestTradeAmountVerify:
    """单笔流水金额 > 50000 元时，编辑/删除需二次验证（force_confirm=true 放行）。"""

    def _insert_large_trade(self, db, amount=AMOUNT_VERIFY_THRESHOLD + 10000):
        """插入一条两天前的大额流水，返回 trade_id。"""
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        _insert_trade(conn, 1, 1, two_days_ago, amount=amount)
        trade_id = _last_trade_id(conn)
        conn.close()
        return trade_id

    def test_large_amount_edit_blocked_without_force(self, client, db):
        """超 5 万流水编辑：未确认时被拒（403 需二次验证）。"""
        trade_id = self._insert_large_trade(db)

        resp = client.put(f'/api/portfolio/trades/{trade_id}', json={'notes': '改备注'})
        assert resp.status_code == 403
        assert '二次验证' in resp.get_json()['message']

    def test_large_amount_edit_ok_with_force_confirm(self, client, db):
        """超 5 万流水编辑：force_confirm=true 放行（200）。"""
        trade_id = self._insert_large_trade(db)

        resp = client.put(
            f'/api/portfolio/trades/{trade_id}',
            json={'notes': '改备注', 'force_confirm': True},
        )
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_large_amount_delete_blocked_without_force(self, client, db):
        """超 5 万流水删除：未确认时被拒（403 需二次验证）。"""
        trade_id = self._insert_large_trade(db)

        resp = client.delete(f'/api/portfolio/trades/{trade_id}')
        assert resp.status_code == 403
        assert '二次验证' in resp.get_json()['message']

    def test_large_amount_delete_ok_with_force_confirm(self, client, db):
        """超 5 万流水删除：force_confirm=true 放行（200）。"""
        trade_id = self._insert_large_trade(db)

        resp = client.delete(f'/api/portfolio/trades/{trade_id}', json={'force_confirm': True})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True

    def test_small_amount_edit_ok_without_force(self, client, db):
        """低于阈值（默认 1000 元）的流水：无需确认直接可编辑（200）。"""
        two_days_ago = (datetime.now() - timedelta(days=2)).strftime('%Y-%m-%d %H:%M:%S')
        conn = get_connection()
        _insert_trade(conn, 1, 1, two_days_ago, amount=1000.0)
        trade_id = _last_trade_id(conn)
        conn.close()

        resp = client.put(f'/api/portfolio/trades/{trade_id}', json={'notes': '改备注'})
        assert resp.status_code == 200
        assert resp.get_json()['success'] is True
