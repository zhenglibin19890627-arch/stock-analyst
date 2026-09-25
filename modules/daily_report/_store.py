"""保存与不变量域（t6 拆包）：_save_report 写库唯一实现。

R9 红线不变量宿主（docs/RED_LINES.md §3）：
- `daily_reports` 三列唯一约束 UNIQUE(report_date, stock_id, report_type)；
- daily 生成时顶替当天 intraday（DELETE 全部后插入），intraday 仅覆盖 intraday。
013: report_type 区分 daily / intraday 的语义在此逐字保持，行为零变化。
"""

import json
from datetime import datetime

from modules.daily_report._env import _CN_TZ, get_connection


def _save_report(
    report_date,
    stock_id,
    stock_code,
    stock_name,
    engine_version,
    total_score,
    rating,
    rating_label,
    prev_score,
    score_change,
    key_factors,
    data_warnings,
    markdown_content,
    status='ok',
    error_msg=None,
    price_advice=None,
    report_type='daily',
):
    """写入 daily_reports 表（DELETE + INSERT 幂等操作）

    013: report_type 区分 daily / intraday
      - daily: 删除当天该股票所有记录（含 intraday），插入新 daily（最终版）
      - intraday: 仅删除之前的 intraday，不动 daily
    """
    conn = get_connection()
    cursor = conn.cursor()
    generated_at = datetime.now(_CN_TZ).isoformat()

    if report_type == 'daily':
        # 盘后日报：删除当天该股票所有记录（含 intraday），插入新 daily
        cursor.execute(
            'DELETE FROM daily_reports WHERE report_date=? AND stock_id=?', (report_date, stock_id)
        )
    else:
        # 盘中快报：仅删除之前的 intraday，不动 daily
        cursor.execute(
            "DELETE FROM daily_reports WHERE report_date=? AND stock_id=? AND report_type='intraday'",
            (report_date, stock_id),
        )

    cursor.execute(
        """
        INSERT INTO daily_reports
            (report_date, stock_id, stock_code, stock_name, engine_version,
             total_score, rating, rating_label, prev_score, score_change,
             key_factors, data_warnings, status, error_msg, markdown_content,
             generated_at, price_advice, report_type)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """,
        (
            report_date,
            stock_id,
            stock_code,
            stock_name,
            engine_version,
            total_score,
            rating,
            rating_label,
            prev_score,
            score_change,
            json.dumps(key_factors, ensure_ascii=False) if key_factors else None,
            json.dumps(data_warnings, ensure_ascii=False) if data_warnings else None,
            status,
            error_msg,
            markdown_content,
            generated_at,
            json.dumps(price_advice, ensure_ascii=False) if price_advice else None,
            report_type,
        ),
    )
    conn.commit()
    conn.close()
