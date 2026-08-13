"""
P2-8: 新旧引擎并行对照回归测试（pytest 版）

将根目录手动验收脚本 test_engine_compare.py（依赖真实数据库 + 手动运行）的核心
对比逻辑转化为可重复的 pytest 回归测试：临时 SQLite 库 + 模拟数据，不触网。

对比项（与 P1 验收标准一致）：
1. 新旧引擎均能跑通（不崩溃）
2. 总分差异在可控范围内（< 20 分）
3. 评级均为中文5档之一

隔离原则：monkeypatch database.db_manager.DB_PATH 指向 pytest tmp_path 临时库，
init_database() 建表后插入模拟 K线/基本面/资金面数据。
"""

import pytest
from database import db_manager
from database.db_manager import get_connection, init_database
from modules import analysis_engine as old_engine
from modules import scoring_engine as new_engine

# 中文5档评级集合（与 config_weights.json / scoring_engine 对齐）
RATING_SET = {'强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出'}

# P1 验收标准：总分差异可控范围
SCORE_DIFF_LIMIT = 20.0


@pytest.fixture()
def engine_db(tmp_path, monkeypatch):
    """临时库 + 模拟数据（上升趋势K线 / 优质基本面 / 正资金流）。"""
    db_file = tmp_path / 'test_engine_compare.db'
    monkeypatch.setattr(db_manager, 'DB_PATH', str(db_file))
    # init_database 的破坏性迁移会触发 backup_database，重定向备份目录避免污染真实 backups/
    monkeypatch.setattr(db_manager, 'BACKUP_DIR', str(tmp_path / 'backups'))
    init_database()

    conn = get_connection()
    conn.execute(
        "INSERT INTO stocks (symbol, market, name, industry) VALUES ('600519', 'a_stock', '对照测试股', '白酒')"
    )
    stock_id = conn.execute('SELECT id FROM stocks ORDER BY id DESC LIMIT 1').fetchone()[0]

    # K线 60 条：收盘价 10.0 → 13.5 缓慢上行（满足 ma60/macd 等指标计算）
    for i in range(60):
        close = 10.0 + i * 0.06
        conn.execute(
            "INSERT INTO raw_kline (stock_id, trade_date, open, close, high, low, volume, pct_change) "
            "VALUES (?, date('2026-01-01', '+' || ? || ' days'), ?, ?, ?, ?, ?, ?)",
            (stock_id, i, round(close - 0.02, 2), round(close, 2),
             round(close + 0.05, 2), round(close - 0.05, 2), 100000 + i * 1000,
             round(0.6, 2)),
        )

    # 基本面 1 条（盈利稳健）
    conn.execute(
        "INSERT INTO raw_fundamental (stock_id, report_date, roe, roa, pe_ratio, pb_ratio, "
        "gross_margin, net_margin, debt_ratio, current_ratio, quick_ratio, "
        "revenue_growth, profit_growth, ocf_to_net_profit) "
        "VALUES (?, '2026-03-31', 15.2, 8.0, 12.5, 2.1, 40.0, 12.0, 45.0, 1.8, 1.5, 20.0, 18.0, 1.2)",
        (stock_id,),
    )

    # 资金面 10 条：主力净流入为正、北向增持
    for i in range(10):
        conn.execute(
            "INSERT INTO raw_capital_flow (stock_id, trade_date, main_net_inflow, "
            "main_net_inflow_pct, north_holding_change, margin_balance) "
            "VALUES (?, date('2026-03-01', '+' || ? || ' days'), ?, 5.0, ?, ?)",
            (stock_id, i, 5000 + i * 100, 100 + i, 80000 + i * 100),
        )

    conn.commit()
    conn.close()
    return stock_id


class TestEngineCompare:
    def test_both_engines_run_and_scores_close(self, engine_db):
        """新旧引擎并行跑通，总分差异 < 20 分，评级均为中文5档。"""
        stock_id = engine_db

        old_result = old_engine.analyze_stock(stock_id)
        assert old_result is not None, '旧引擎返回 None'
        assert old_result.get('success', True), f"旧引擎失败: {old_result.get('message')}"
        old_score = old_result['total_score']
        assert old_result['rating'] in RATING_SET, f"旧引擎评级非法: {old_result['rating']}"

        new_result = new_engine.analyze_from_db(stock_id)
        assert new_result is not None, '新引擎返回 None'
        new_score = new_result.total_score
        assert new_result.rating in RATING_SET, f"新引擎评级非法: {new_result.rating}"

        diff = abs(old_score - new_score)
        assert diff < SCORE_DIFF_LIMIT, (
            f'新旧引擎总分差异 {diff:.1f} 分超出可控范围 {SCORE_DIFF_LIMIT} 分 '
            f'(旧={old_score:.1f}, 新={new_score:.1f})'
        )

    def test_both_engines_produce_dimension_scores(self, engine_db):
        """新旧引擎均产出有效维度得分（K线/基本面/资金面）。"""
        stock_id = engine_db

        old_result = old_engine.analyze_stock(stock_id)
        new_result = new_engine.analyze_from_db(stock_id)

        # 旧引擎：dimensions 字典含 kline/fundamental/capital_flow
        for dim in ('kline', 'fundamental', 'capital_flow'):
            d = old_result.get('dimensions', {}).get(dim, {})
            assert d.get('score') is not None, f'旧引擎维度 {dim} 无得分'
            assert 0 <= d['score'] <= 100, f'旧引擎维度 {dim} 得分越界: {d["score"]}'

        # 新引擎：AnalysisResult 四维得分字段
        for field in ('technical_score', 'fundamental_score', 'capital_score'):
            v = getattr(new_result, field)
            assert v is not None, f'新引擎维度 {field} 无得分'
            assert 0 <= v <= 100, f'新引擎维度 {field} 得分越界: {v}'
