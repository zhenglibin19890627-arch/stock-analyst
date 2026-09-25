"""
021BP 项4：日报概览"失败/超时股"可见性补齐（§4.4 缺口）

背景：超时/失败股此前只进 failure_summary 与 "> ⚠️ N 只生成失败" 一行计数，
名字与原因埋在 POST 响应 JSON 里——概览表只列 ok 股，用户看不到"哪些股被跳过"。
修复：_build_markdown_summary 在概览区显式列出失败股（名字/代码/原因），
数据层 daily_reports status='failed' 行不动（看板侧由 t3 行动清单卡消费）。

覆盖（隔离临时库 + markdown 落盘目录隔离，不触网）：
1. failed 股显式列出（名字/代码/原因），ok 股照旧进概览表
2. 失败原因含裸 '<' 时转义为 &lt;（021BN 教训，marked 渲染吞字防护）
3. 无失败股时不输出失败小节（回归既有形态）
"""

import pytest

import modules.daily_report as daily_report
from database import db_manager
from modules.daily_report import _summary


@pytest.fixture()
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(db_manager, 'DB_PATH', str(tmp_path / 'test_report_summary.db'))
    db_manager.init_database()
    # markdown 落盘目录隔离（避免测试写工作区 reports/ 产物目录）
    # t6 拆包迁移：_REPORTS_DIR 随唯一消费方单宿 _summary（补丁打在 facade 对包内调用不可见）
    monkeypatch.setattr(_summary, '_REPORTS_DIR', str(tmp_path / 'reports'))
    return tmp_path


def _result(status, name='贵州茅台', symbol='600519', error=None):
    r = {
        'stock_id': 1, 'symbol': symbol, 'name': name,
        'status': status, 'engine': 'v5', 'score': 70.0, 'rating': '持有观望',
        'score_change': None,
    }
    if error is not None:
        r['error'] = error
    return r


def test_failed_stocks_listed_with_reason(db):
    """失败股在概览区显式列出：名字/代码/原因；ok 股照旧进概览表"""
    results = [
        _result('ok', name='中国中免', symbol='601888'),
        _result('failed', name='东山精密', symbol='002384', error='采集超时(90s)'),
    ]
    md = daily_report._build_markdown_summary('2026-09-21', results)
    assert '1 只股票生成失败' in md
    assert '东山精密' in md
    assert '002384' in md
    assert '采集超时(90s)' in md
    # ok 股照旧进概览表（不受影响）
    assert '中国中免' in md


def test_failed_error_lt_escaped(db):
    """失败原因含裸 '<' → 表格行内转义为 &lt;（防 marked 渲染吞字）"""
    results = [_result('failed', error='评分 55.0 未达标（原文案含<DEA）')]
    md = daily_report._build_markdown_summary('2026-09-21', results)
    failed_row = [line for line in md.splitlines() if '未达标' in line][0]
    assert '&lt;DEA' in failed_row
    assert '<' not in failed_row.replace('&lt;', '')


def test_no_failed_section_when_all_ok(db):
    """全部成功：不输出失败明细小节（回归既有形态；引擎统计的计数行不受影响）"""
    results = [_result('ok')]
    md = daily_report._build_markdown_summary('2026-09-21', results)
    assert '> ⚠️' not in md
    assert '只股票生成失败' not in md
    assert '失败原因' not in md
