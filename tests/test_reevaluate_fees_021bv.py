"""021BV 重估脚本回归：scripts/reevaluate_fees_021bv.py

覆盖（隔离临时库，零真实数据触碰）：
1. dry-run：零写库（无备份文件产生、commission 不变）、计划包含预期变更行；
2. --apply：R11 备份先行 → 仅 est=1 行回写（est=0 用户实填/旧行绝不碰）
   → _recalculate_holding 全量重算（含自动建行路径）→ is_cost_adjusted 清零
   → commission_estimated 保持 1；
3. 备份失败（backup_database 返回 None）→ 中止零写入（红线 R11）；
4. 东财档未变更：重估等值幂等（delta=0 不回写）。
"""

import importlib.util
import sqlite3
from pathlib import Path

import pytest

from database import db_manager

_SCRIPT = Path(__file__).resolve().parent.parent / 'scripts' / 'reevaluate_fees_021bv.py'
_spec = importlib.util.spec_from_file_location('reevaluate_fees_021bv', str(_SCRIPT))
reev = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(reev)


@pytest.fixture()
def fdb(tmp_path, monkeypatch):
    """隔离库：银河/东财账户 + 1 只股票 + 五笔流水（覆盖四类场景）+ 预置持仓。

    流水（commission 均为 021BV 前旧口径 stored 值）：
      t1 est=1 银河 buy  5100（旧 5.05 → 新 1.00，t1 情景A id=51 同款）
      t2 est=1 银河 sell 13674（旧 11.97 → 新 9.51，t1 情景A id=59 同款）
      t3 est=0 银河 buy  9999（旧批量导入 comm=0 行——绝不碰）
      t4 est=0 银河 buy  commission=6.66（模拟用户实填——绝不碰）
      t5 est=1 东财 buy  10000（旧 5.10 → 新 5.10，东财档未变更幂等）
    """
    db_file = tmp_path / 'reev.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    db_manager.init_database()
    conn = db_manager.get_connection()
    try:
        # init 预置「默认账户」id=1 → 银河=2、东财=3（脚本按账户名匹配费率，
        # 不依赖 id，恰好同时覆盖「默认账户之外的账户」路径）
        conn.execute(
            "INSERT INTO accounts (name, is_default) VALUES ('银河证券', 0)")
        conn.execute(
            "INSERT INTO accounts (name, is_default) VALUES ('东方财富', 0)")
        conn.execute(
            "INSERT INTO stocks (symbol, market, name) VALUES ('600276', 'a_stock', '恒瑞医药')")
        conn.commit()
        acc_galaxy = conn.execute(
            "SELECT id FROM accounts WHERE name='银河证券'").fetchone()['id']
        acc_east = conn.execute(
            "SELECT id FROM accounts WHERE name='东方财富'").fetchone()['id']
        sid = conn.execute("SELECT id FROM stocks WHERE symbol='600276'").fetchone()['id']
        rows = [
            # (account, type, price, qty, amount, commission, est)
            (acc_galaxy, 'buy', 10.2, 500, 5100.0, 5.05, 1),
            (acc_galaxy, 'sell', 13.674, 300, 4102.2, 11.97, 1),
            (acc_galaxy, 'buy', 9.999, 1000, 9999.0, 0.0, 0),
            (acc_galaxy, 'buy', 6.66, 100, 666.0, 6.66, 0),
            (acc_east, 'buy', 10.0, 1000, 10000.0, 5.10, 1),
        ]
        for acc, ttype, price, qty, amount, comm, est in rows:
            conn.execute(
                'INSERT INTO trade_records (account_id, stock_id, trade_type, price, '
                'quantity, amount, commission, commission_estimated, trade_date, '
                "created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, '2026-09-2', '2026-09-23 10:00:00')",
                (acc, sid, ttype, price, qty, amount, comm, est),
            )
        # 预置银河持仓：人工修正标记 + 旧口径成本（重算后应回归流水口径并清标记）
        conn.execute(
            'INSERT INTO holdings (account_id, stock_id, cost_price, quantity, '
            "realized_pnl, status, is_cost_adjusted) VALUES (?, ?, 15.0, 200, 0, "
            "'active', 1)", (acc_galaxy, sid),
        )
        conn.commit()
        fdb.galaxy_id = acc_galaxy
        fdb.east_id = acc_east
        fdb.sid = sid
    finally:
        conn.close()
    return fdb


def _commission(conn, row_id):
    return conn.execute(
        'SELECT commission, commission_estimated FROM trade_records WHERE id=?',
        (row_id,)).fetchone()


class TestDryRun:
    def test_dry_run_writes_nothing(self, fdb, capsys):
        code = reev.main([])
        out = capsys.readouterr().out
        assert code == 0
        assert 'dry-run' in out
        # 零脚本备份（init_database 迁移备份不算——只认本脚本 reason 标识）
        backups_dir = Path(db_manager.BACKUP_DIR)
        assert not list(backups_dir.glob('*021bv_fee_reeval*.db')) if backups_dir.exists() else True
        # 佣金原样（旧值仍在库）
        conn = db_manager.get_connection()
        try:
            first_id = conn.execute(
                'SELECT MIN(id) AS m FROM trade_records').fetchone()['m']
            assert _commission(conn, first_id)['commission'] == pytest.approx(5.05)
        finally:
            conn.close()

    def test_plan_values_match_t1_scenario(self, fdb, capsys):
        """计划数值：buy 5100→1.00（t1 情景A id=51 同款）、
        sell 4102.2→2.85（同公式）；东财 5.10 等值幂等。"""
        code = reev.main([])
        out = capsys.readouterr().out
        assert code == 0
        assert '1.00' in out and '2.85' in out
        conn = db_manager.get_connection()
        try:
            rows = reev.collect_estimated_rows(conn.cursor())
        finally:
            conn.close()
        plan, affected = reev.build_plan(rows)
        by_amount = {p['fee_amount']: p for p in plan}
        assert by_amount[5100.0]['new_commission'] == pytest.approx(1.00)
        assert by_amount[5100.0]['delta'] == pytest.approx(-4.05)
        assert by_amount[4102.2]['new_commission'] == pytest.approx(2.85)
        east = [p for p in plan if p['account_id'] == fdb.east_id]
        assert len(east) == 1 and east[0]['delta'] == 0  # 东财档未变更 → 幂等
        # affected 对含银河+东财两组（等值行也进入重算集合——持仓行缺失修复路径）
        assert (fdb.sid, fdb.galaxy_id) in affected
        assert (fdb.sid, fdb.east_id) in affected


class TestApply:
    def test_apply_full_pipeline(self, fdb, capsys):
        code = reev.main(['--apply'])
        out = capsys.readouterr().out
        assert code == 0
        # R11 备份已产生且带 reason 标识
        backups = list(Path(db_manager.BACKUP_DIR).glob('*021bv_fee_reeval*.db'))
        assert len(backups) == 1
        conn = db_manager.get_connection()
        try:
            ids = [r['id'] for r in conn.execute(
                'SELECT id FROM trade_records ORDER BY id')]
            t1, t2, t3, t4, t5 = ids
            # est=1 银河两笔回写新值，est 标记保持 1
            r1 = _commission(conn, t1)
            assert r1['commission'] == pytest.approx(1.00)
            assert r1['commission_estimated'] == 1
            r2 = _commission(conn, t2)
            assert r2['commission'] == pytest.approx(2.85)
            assert r2['commission_estimated'] == 1
            # est=0 两笔（旧行 comm=0 / 用户实填 6.66）分毫未动
            assert _commission(conn, t3)['commission'] == pytest.approx(0.0)
            assert _commission(conn, t3)['commission_estimated'] == 0
            assert _commission(conn, t4)['commission'] == pytest.approx(6.66)
            assert _commission(conn, t4)['commission_estimated'] == 0
            # 东财等值行不回写（仍是 5.10，本就等值）
            assert _commission(conn, t5)['commission'] == pytest.approx(5.10)

            # 银河持仓重算（重算吃全部流水，est=0 行也是账本）：
            # buy 5100+1.00 → sell 净得 4099.35 部分卖出 → buy 9999(comm 0) → buy 666+6.66
            # → total_cost≈11673.31, qty=1300 → cost≈8.9795，active；is_cost_adjusted 清零
            h = conn.execute(
                'SELECT * FROM holdings WHERE account_id=? AND stock_id=?',
                (fdb.galaxy_id, fdb.sid)).fetchone()
            assert h['quantity'] == 1300
            assert h['cost_price'] == pytest.approx(8.9795, abs=0.001)
            assert h['is_cost_adjusted'] == 0
            assert h['status'] == 'active'
            # 东财持仓行原本缺失 → 重算自动建行（买入 10000+5.10/1000 股）
            h2 = conn.execute(
                'SELECT * FROM holdings WHERE account_id=? AND stock_id=?',
                (fdb.east_id, fdb.sid)).fetchone()
            assert h2 is not None
            assert h2['quantity'] == 1000
            assert h2['cost_price'] == pytest.approx(10.0051)
        finally:
            conn.close()
        # apply 输出含回退路径提示
        assert '回退' in out

    def test_apply_is_idempotent(self, fdb):
        """二次 --apply：数值已收敛 → 零回写、持仓重算幂等。"""
        assert reev.main(['--apply']) == 0
        conn = db_manager.get_connection()
        try:
            before = conn.execute(
                'SELECT id, commission FROM trade_records ORDER BY id').fetchall()
            holdings_before = conn.execute(
                'SELECT account_id, quantity, cost_price, realized_pnl, status '
                'FROM holdings ORDER BY account_id').fetchall()
        finally:
            conn.close()
        assert reev.main(['--apply']) == 0
        conn = db_manager.get_connection()
        try:
            after = conn.execute(
                'SELECT id, commission FROM trade_records ORDER BY id').fetchall()
            holdings_after = conn.execute(
                'SELECT account_id, quantity, cost_price, realized_pnl, status '
                'FROM holdings ORDER BY account_id').fetchall()
        finally:
            conn.close()
        assert [(r['id'], r['commission']) for r in before] == \
            [(r['id'], r['commission']) for r in after]
        assert [(r['account_id'], r['quantity'], r['cost_price'],
                 r['realized_pnl'], r['status']) for r in holdings_before] == \
            [(r['account_id'], r['quantity'], r['cost_price'],
              r['realized_pnl'], r['status']) for r in holdings_after]


class TestBackupFailureAborts:
    def test_backup_none_aborts_with_zero_writes(self, fdb, monkeypatch, capsys):
        """R11：备份失败（返回 None）→ 中止零写入（红线契约）。"""
        monkeypatch.setattr(reev, 'backup_database', lambda reason: None)
        code = reev.main(['--apply'])
        out = capsys.readouterr().out
        assert code == 1
        assert '中止' in out
        conn = db_manager.get_connection()
        try:
            rows = conn.execute(
                'SELECT commission FROM trade_records ORDER BY id').fetchall()
            holdings = conn.execute('SELECT COUNT(*) AS n FROM holdings').fetchone()
        finally:
            conn.close()
        assert rows[0]['commission'] == pytest.approx(5.05)  # 未回写
        assert holdings['n'] == 1  # 未新增持仓行（东财未触发重算）
