"""021BS 一致性审计脚本（只读，可重复执行）——报告内七要素两两核对 + 报告↔看板五数据流。

=====================================================================
定位与批次背景（021BS 第一轮审计，2026-09-22）
=====================================================================
用户已实测暴露两类矛盾：①分析报告自身内部矛盾（五粮液买点信号 vs 减仓、
中免止损触发 vs 持有等）；②分析报告与总览看板的矛盾（行动清单/看板卡/
持仓页/预警铃铛与报告间的数据流与口径）。021BP-021BR 已修矩阵与行动清单
侧的分域层级与口径同源；本批次把「报告自身」与「报告↔看板」的矛盾穷尽。

本脚本是 **只读工具**：
  - 数据库一律 ``sqlite3.connect('file:...?mode=ro', uri=True)`` 只读连接；
  - 服务侧仅通过 Flask test_client 调用 **纯只读 GET 端点**（trend /
    trader-advice / dashboard/action-list / portfolio/watchlist-scores /
    portfolio/holdings），零写库、零网络采集；
  - 刻意避开会触发写库的端点（如 report-latest 的 15 分钟过期实时重评会
    写 daily_reports/ratings_history）——报告本体改由 ro 连接直读
    daily_reports 行，即「存量报告」这一审计基准面。

=====================================================================
七要素定义（对每只自选股提取）
=====================================================================
  A 总评级/总分     daily_reports.rating / total_score / rating_label
  B 评分明细四维     key_factors.kline/fundamental/capital_flow/news（分数+权重+要点）
  C 趋势罗盘        GET /api/stocks/<id>/trend（trend_analyzer 纯函数现算，
                    日/周/月三周期 + overall）
  D 操盘手          存量 key_factors.trader（阶段/分歧/top_action 预计算）
                    + GET /api/stocks/<id>/trader-advice 现算（stage/operations/
                    disagreement/linkage/矩阵行）
  E 价格建议        daily_reports.price_advice JSON（区间/止损/状态机/网格）
  F 风险提示与建议文字 markdown_content（综合评分行/操作建议行/四维行/风险提示）
  G 裁决信号        trader-advice 的 disagreement（reversal_opportunity /
                    structural_risk / stage_leads_rating）+ operations.linkage

=====================================================================
一致性规则集（两两核对；分级 P0/P1/P2/INFO/OK，规则即注释、注释即规则）
=====================================================================
分级口径：
  P0  指令矛盾 —— 两处同时给出相反动作指令（用户无所适从）
  P1  表述误导 —— 一处可能被误读 / 同数据日数字打架 / 应标注未标注
  P2  口径差异 —— 两处口径不同但各有其理，需标注说明（或报告生成后的
      数据/配置时点差，随下次报告生成自愈）
  INFO 信息性观察（不构成矛盾）
  OK   核对一致（正常分层基线，计入统计）

报告内（①）：
  R01 A×E 区间语义契约（021BF）：减仓/卖出档 price_advice.zone_label 必须
      为「支撑参考区间」且 action_suggestion 不含买入话术；买入档为
      「买入区间」；观望档为「参考区间」。违反 = P0。
  R02 E内 状态机自洽：has_position 时 S4 ⇔ close<stop_loss、S1 ⇔
      close>=take_profit、其余按浮盈亏分 S2/S3；action_suggestion 必须包含
      ACTION_MATRIX[评级][状态] 基准词（允许资金面修饰词前后缀）。违反 = P0。
  R03 E内 数字自洽：profit_pct == (close-cost)/cost（±0.15pp）；current_close
      与 raw_kline 最新收盘一致（021BS t4 断言化：同数据日不一致按「生成时点
      双存储见证」分级——ratings_history.price_at_rating（与 pa.current_close
      同一 K 线时点写入）== current_close 而 kline 不同 = K 线事后修订型 P2
      （020I 补采/数据修订覆盖了生成时点 K 线，报告彼时自洽，重生成自愈）；
      见证不在场或见证亦不符 = P1 采集竞态需排查。跨数据日 = P2 时点差）。
  R04 A×B 分数-档位边界：rating 与 total_score 按市场阈值（A股 80/65/50/30；
      港股 021R overrides 推荐买入≥70/持有观望≤69）核对；不一致时按三层标注面
      断言（021BS 修复契约：失配必须被持续标注）——①markdown 含「迟滞」标注
      ②key_factors.score_tier_note 落库注记 ③读取面 score_tier_note（看板
      watchlist-scores / 报告页响应，读取路径按同源纯函数现算）：任一在 = P2
      （设计内·已标注）；三层全缺 = P1（应标注未标注）。
  R05 B×C 技术面子分 vs 罗盘方向：kline≥75 且 overall=down = P1；kline≥65
      且 overall=down（或 kline<40 且 overall=up）= P2（口径差异：动量加权
      vs 三周期方向，需标注）。
  R06 A×C 评级方向 vs 罗盘综合方向：买入档 × overall=down = P1（有 G 分歧
      标注则降 P2）；减仓档 × overall=up = P2。
  R07 A×D/G 分歧通道覆盖：detect_disagreement 触发条件成立（弱评级×好阶段/
      强评级×差阶段/观望×强置信弱势阶段）而 live disagreement 缺失 = P1
      （应标注未标注）；已标注 = OK（正常分层·已调和）。
  R08 D内 层级契约①（021BR 测试锁）：operations.status.kind='stop_triggered'
      时 held_rows 不得含「持有」行、linkage 不得含「持仓者持有」措辞。
      违反 = P0。
  R09 D×E 止损三数字：top_action 的止损数字、price_advice.stop_loss、纪律线
      （聚合成本×0.92）三者互异 = P1（同日双基数/时序残留）；其一相等 = OK。
  R10 D×E 触发状态互证：矩阵止损已触发而 close >= pa.stop_loss = P2（矩阵
      双源取高者口径，需标注）；矩阵已触发且 close < pa.stop_loss 而
      pa.state != 'S4' = P0（价格建议状态机失真）。一致 = OK。
  R11 C×D 罗盘 vs 阶段：弱势阶段×overall=up / 强势阶段×overall=down = P2
      （罗盘月线定方向 vs 阶段量价结构，口径不同需标注）。
  R12 A×F markdown 自洽：综合评分行的评级 != rating 列 = P0；「操作建议」行
      动作词与评级档位按 _determine_action 021BH 对齐表核对，不符 = P1。
  R13 D存量×D现算 摘要时点：key_factors.trader.stage_name/top_action 与 live
      现算不一致：kline_upto == report_date（同数据日）= P1（真矛盾），
      跨数据日 = P2（预计算时点差）。
  R14 E存储×E现算 成本/止损基数：stored price_advice.cost_price/stop_loss vs
      按【当前持仓聚合】与 stored rating 重算：成本不同 = P2（持仓数据在
      报告生成后变更，重生成报告自愈）；成本相同而止损不同 = P1。
  R15 B内 加权核对：Σ(维度分×权重) ≈ total_score（容差 3 分，维度缺失跳过；
      港股资金面收缩/权重热加载/迟滞均可造成合法偏差）不符 = P2。

报告↔看板（②）：
  F01 行动清单 vs 报告：overview 行 rating/total_score == 最新报告行（不一致
      = P0）；评级变动项方向与 DB 前后两期一致（不一致 = P0）；持仓纪律项
      的 close/有效止损 与现算一致（不一致 = P0）；overview.has_disagreement
      与 stored trader 一致（021BS t4：stored 缺失时与 live 兜底一致；不一致
      = P1）；缺报股显式列出（INFO）。
  F02 看板 top_action chip vs 报告 price_advice：trader_signal.top_action ==
      key_factors.trader.top_action（同源，不一致 = P0）；chip 止损数字 vs
      存量 pa.stop_loss vs 报告期纪律线：互异 = P1（同数据）/ P2（持仓变更
      时点差）；pa_zone.low/high/stop_loss 与存量 price_advice 对应字段一致
      （不一致 = P1）。
  F03 持仓页价格 vs 矩阵现价（双口径）：price_cache.latest_price vs raw_kline
      收盘：|差|>3% = P1；其余差异 = P2（盘中快照 vs 日K收盘，页面已有口径
      标注，核对 price_expired 标记）；持仓行数量/成本与 live 矩阵聚合不一致
      = P2（数据时点差）。
  F04 预警铃铛 vs 报告：rating_change 预警的新评级 vs 报告评级（不一致 = P1，
      注：预警读 ratings_history、报告读 daily_reports，两表同日不同源时点
      差降 P2）；score_below 今日触发而报告分≥阈值 = P1；报告分<阈值而无
      今日预警 = P2（幂等/扫描时点）；未读计数与行动清单统计一致性 = P1。
  F05 watchlist-scores 评级 vs 报告：rating/total_score/report_date/engine_version
      与最新 ok daily 行一致（不一致 = P0 数据流断裂）。

021BU 扩展（2026-09-23；⚠ 以下规则编号属审计脚本命名空间，
与 docs/RED_LINES.md 的红线 R16-R19 无关）：
  R16 评级徽章三方同源（021BU O1）：①watchlist-scores 响应 rating_evidence
      ×②审计按生产同款 SQL（ro 直读，真实行 + rating_id 过滤 + 自然键去重）
      现算基准。同数据日 primary 的 n/m/display 不一致 = P1（数字打架）；
      有评级有报告而徽章字段缺失 = P2（应接未接，降级展示）。报告页第三面
      由 check_static_same_source 静态断言消费同一共享函数（report-latest
      可能触发实时重评写库，审计刻意不调——V8 只读原则优先）。
  R17 诚实原则展示门（021BU）：响应证据对象中 grade=C（n<20）却含非空 acc
      或 display 带百分数 = P1（小样本误导）；acc 非空而 n/m 缺失 = P1；
      display 含百分数而 n/m 缺失 = P1。门槛常量 import 自
      backtest_engine（EVIDENCE_N_PARTIAL，单一真相，不另设第二套）。
  R18 价格基准注记同源（021BU O3）：watchlist-scores 顶层 evidence_price
      ×审计 ro 现算基准（price_backtest_results 真实锚点口径）：三格
      n/m/display 不一致 = P1；字段缺失（有基准样本时）= P2；证据格
      同步过 R17 诚实门。非真实锚点口径（重建点/全体）冒充主口径 = P2。
  F06 看板评分卡证据流（021BU O2）：每股 position_note 与 position-note
      端点（同一 position_note_for 函数现算）文本一致（不一致 = P1）；
      端点有显著分化而看板缺失 = P2（应接未接）；看板有而端点无显著
      分化 = P1（同函数不应异判）。附带：行动清单 items 含 rating_evidence
      键 = INFO（统计面泄漏进指令面观察，不设门禁——021BR 分域契约）。

=====================================================================
用法
=====================================================================
  python scripts/audit_consistency_021bs.py                # 全量审计并写报告
  python scripts/audit_consistency_021bs.py --selftest     # 合成用例自检（不触库）
  python scripts/audit_consistency_021bs.py --stock 21 24  # 只审计指定 stock_id
  python scripts/audit_consistency_021bs.py --out PATH     # 指定报告输出路径
  python scripts/audit_consistency_021bs.py --no-report    # 只跑核对，不写 md

红线合规：全程只读（V8）；不触碰 advisor.generate_advice（B24——涉及其内
的根因在报告中只给外层调和建议）；不重实现分数→评级映射（R7：仅消费
config 阈值与 alert_engine.RATING_ORDER）；零网络；零 pip 新依赖。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

# ---- 项目根目录注入（scripts/ 的父目录）----
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

from config import DB_PATH  # noqa: E402

CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')

# 审计进程内静音 INFO 日志（模块读库的常规 INFO 不属于审计输出；WARNING+ 保留）
logging.disable(logging.INFO)

SEV_ORDER = {'P0': 0, 'P1': 1, 'P2': 2, 'INFO': 3, 'OK': 4}
SEV_DESC = {
    'P0': 'P0 指令矛盾（两处给出相反动作）',
    'P1': 'P1 表述误导（一处可能被误读）',
    'P2': 'P2 口径差异（需标注说明/时点差自愈）',
    'INFO': '信息性观察',
    'OK': '核对一致（正常分层）',
}

BUY_RATINGS = ('强烈推荐买入', '推荐买入')
HOLD_RATING = '持有观望'
REDUCE_RATINGS = ('建议减仓', '强烈建议卖出')

# 021BH 动作词对齐表（advisor._determine_action，键=(有持仓,浮盈)）
ACTION_ALIGN = {
    '强烈推荐买入': {(False, False): '买入', (True, True): '加仓', (True, False): '继续持有'},
    '推荐买入': {(False, False): '买入', (True, True): '持有', (True, False): '继续持有'},
    '持有观望': {(False, False): '关注', (True, True): '持有', (True, False): '持有'},
    '建议减仓': {(False, False): '观望', (True, True): '持有', (True, False): '考虑减仓'},
    '强烈建议卖出': {(False, False): '回避', (True, True): '减仓', (True, False): '建议止损'},
}


# ================================================================
# 数据加载（全部只读）
# ================================================================


def open_ro_db(db_path: str | None = None) -> sqlite3.Connection:
    """以 mode=ro 打开 SQLite（零写库的硬保证）。"""
    path = db_path or DB_PATH
    conn = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def make_client():
    """构建 Flask test_client（只注册蓝图，不启动调度器/服务）。

    仅调用只读 GET 端点；report-latest 等可能触发写库的端点一律不调。
    """
    from flask import Flask

    from blueprints import ALL_BLUEPRINTS

    app = Flask(__name__)
    for bp in ALL_BLUEPRINTS:
        app.register_blueprint(bp)
    app.config['TESTING'] = True
    return app.test_client()


def _get_json(client, path):
    """GET 一个只读端点并解析 JSON；失败返回 None（不中断全量审计）。"""
    try:
        resp = client.get(path)
        if resp.status_code != 200:
            return None
        return resp.get_json()
    except Exception:  # noqa: BLE001 —— 单端点失败不阻塞审计
        return None


def q1(cur, sql, args=()):
    row = cur.execute(sql, args).fetchone()
    return dict(row) if row else None


def load_watchlist(cur):
    rows = cur.execute(
        "SELECT id, symbol, name, market FROM stocks WHERE status='active' ORDER BY id"
    ).fetchall()
    return [dict(r) for r in rows]


def load_reports(cur):
    """每股最近两期 ok daily 报告（rn=1 最新 / rn=2 上期）。"""
    rows = cur.execute(
        """
        SELECT report_date, stock_id, stock_code, stock_name, engine_version,
               total_score, rating, rating_label, prev_score, score_change,
               key_factors, price_advice, markdown_content, data_warnings,
               generated_at,
               ROW_NUMBER() OVER (PARTITION BY stock_id ORDER BY report_date DESC) AS rn
        FROM daily_reports
        WHERE status='ok' AND report_type='daily'
        """
    ).fetchall()
    latest, prev = {}, {}
    for r in rows:
        d = dict(r)
        if d['rn'] == 1:
            latest[d['stock_id']] = d
        elif d['rn'] == 2:
            prev[d['stock_id']] = d
    return latest, prev


def load_holdings_agg(cur):
    rows = cur.execute(
        'SELECT stock_id, SUM(quantity) AS total_qty, '
        'CASE WHEN SUM(quantity) > 0 THEN SUM(quantity*cost_price)/SUM(quantity) END AS avg_cost '
        'FROM holdings WHERE quantity > 0 GROUP BY stock_id'
    ).fetchall()
    return {
        r['stock_id']: {'total_qty': int(r['total_qty'] or 0), 'avg_cost': r['avg_cost']}
        for r in rows
    }


def load_price_cache(cur):
    rows = cur.execute('SELECT stock_id, latest_price, pct_change, updated_at FROM price_cache')
    return {r['stock_id']: dict(r) for r in rows}


def load_kline_head(cur, stock_id):
    return q1(
        cur,
        'SELECT trade_date, close FROM raw_kline WHERE stock_id=? '
        'ORDER BY trade_date DESC LIMIT 1',
        (stock_id,),
    )


def load_alerts_today(cur, today):
    rows = cur.execute(
        'SELECT stock_id, alert_type, message, is_read, trigger_date, triggered_at '
        'FROM alert_history WHERE trigger_date=?',
        (today,),
    ).fetchall()
    return [dict(r) for r in rows]


def load_alert_rules(cur):
    rows = cur.execute('SELECT rule_type, stock_id, threshold, enabled FROM alert_rules').fetchall()
    return [dict(r) for r in rows]


def load_ratings_tail(cur, stock_id, n=2):
    rows = cur.execute(
        'SELECT rating, total_score, rating_date FROM ratings_history '
        'WHERE stock_id=? ORDER BY rating_date DESC LIMIT ?',
        (stock_id, n),
    ).fetchall()
    return [dict(r) for r in rows]


def load_rating_witness(cur, stock_id):
    """生成时点见证（021BS t4 R03 断言化）：ratings_history.price_at_rating 按
    B12-T2 契约取 rating_date 当日 K 线收盘写入，与 daily_reports.price_advice.
    current_close 构成同一时点的双存储快照。二者一致而现 K 线不符 → K 线事后
    被补采/修订覆盖（良性时点差）；见证缺失/不符 → 生成即错位（采集竞态）。"""
    return q1(
        cur,
        'SELECT rating_date, price_at_rating FROM ratings_history '
        'WHERE stock_id=? ORDER BY rating_date DESC LIMIT 1',
        (stock_id,),
    )


# ================================================================
# 七要素提取与解析
# ================================================================


def parse_json_field(raw):
    if not raw:
        return None
    if isinstance(raw, dict):
        return raw
    try:
        v = json.loads(raw)
        return v if isinstance(v, dict) else None
    except (TypeError, ValueError):
        return None


MD_SCORE_RE = re.compile(r'综合评分\*\*：([\d.]+)（(.+?)级')
MD_ACTION_RE = re.compile(r'- \*\*操作建议\*\*：(.+)')
MD_DIMS_RE = re.compile(r'四维\*\*：技术面 ([\d.]+) ?\| ?基本面 ([\d.]+) ?\| ?资金面 ([\d.]+) ?\| ?消息面 ([\d.—]+)')
RISK_LINE_RE = re.compile(r'^  - (.+)$', re.M)
STOP_NUM_RE = re.compile(r'止损·([\d.]+)')


def parse_markdown(md: str | None) -> dict:
    """从 markdown_content 提取建议文字要素（F 要素）。"""
    out = {
        'score': None,
        'rating': None,
        'action_line': None,
        'dims': None,
        'risks': [],
        'hysteresis': False,
        'raw': md or '',
    }
    if not md:
        return out
    m = MD_SCORE_RE.search(md)
    if m:
        try:
            out['score'] = float(m.group(1))
        except ValueError:
            pass
        out['rating'] = m.group(2)
    ma = MD_ACTION_RE.search(md)
    if ma:
        out['action_line'] = ma.group(1).strip()
    mdims = MD_DIMS_RE.search(md)
    if mdims:
        out['dims'] = mdims.groups()
    if '迟滞' in md:
        out['hysteresis'] = True
    risk_block = re.search(r'- \*\*风险提示\*\*：\n((?:  - .+\n?)+)', md)
    if risk_block:
        out['risks'] = [ln.strip('- ').strip() for ln in risk_block.group(1).strip().splitlines()]
    return out


def expected_tier(score: float, market: str) -> str | None:
    """分数→期望档位（消费 config/config_weights 既有口径，不重实现边界映射逻辑：

    A股全局 80/65/50/30；港股 021R overrides 仅覆盖 推荐买入(≥70)/持有观望(≤69)。
    """
    if score is None:
        return None
    if market == 'hk_stock':
        if score >= 80:
            return '强烈推荐买入'
        if score >= 70:
            return '推荐买入'
        if score >= 50:
            return HOLD_RATING
        if score >= 30:
            return '建议减仓'
        return '强烈建议卖出'
    if score >= 80:
        return '强烈推荐买入'
    if score >= 65:
        return '推荐买入'
    if score >= 50:
        return HOLD_RATING
    if score >= 30:
        return '建议减仓'
    return '强烈建议卖出'


def rating_direction(rating: str | None) -> int:
    if rating in BUY_RATINGS:
        return 1
    if rating in REDUCE_RATINGS:
        return -1
    return 0


def discipline_stop(cost) -> float | None:
    return round(float(cost) * 0.92, 2) if cost else None


def _classify_stop_number_mismatch(chip_stop: float, pa_stop, disc_report):
    """止损数字不一致的两个成因分级。

    021BR t3 修复前生成的存量报告（当日 11:38 批次早于修复提交）具有可识别
    特征：top_action 止损 == 报告期纪律线（价格建议未进入 top_action 计算，
    t3 时序缺陷形态），而 price_advice.stop_loss 更高——属旧数据残留，重生成
    报告自愈（P2）；其余同日双数字为真矛盾（P1）。
    """
    if disc_report is not None and pa_stop is not None \
            and abs(chip_stop - disc_report) <= 0.01 and pa_stop > disc_report + 0.01:
        return 'P2', ('021BR t3 时序修复前的存量报告特征：top_action 止损=纪律线'
                      '（生成时 price_advice 未进入计算），与报告行内 pa.stop_loss 并存——'
                      '重生成报告即合流')
    return 'P1', '看板 chip 摘要数字与报告价格建议止损同数据双数字（同源链路断裂特征）'


def extract_elements(report: dict, live_trend: dict | None, live_trader: dict | None,
                     held: dict | None, kline_head: dict | None) -> dict:
    """汇总单只股票七要素上下文（ctx）。"""
    kf = parse_json_field(report.get('key_factors')) or {}
    pa = parse_json_field(report.get('price_advice'))
    md = parse_markdown(report.get('markdown_content'))
    trader_stored = kf.get('trader') or {}
    live_ops = (live_trader or {}).get('operations') or {}
    close = float(kline_head['close']) if kline_head and kline_head['close'] else None
    kline_date = str(kline_head['trade_date']) if kline_head else None
    return {
        'report': report,
        'report_date': report.get('report_date'),
        'generated_at': report.get('generated_at'),
        'rating': report.get('rating'),
        'rating_label': report.get('rating_label'),
        'total_score': report.get('total_score'),
        'market': None,  # 由调用方按 stocks 表补充
        'dims': kf,
        # 021BS：key_factors 结构化失配注记（报告落库标注面②）
        'score_tier_note_stored': kf.get('score_tier_note'),
        'pa': pa or {},
        'pa_available': bool(pa and pa.get('available', True)),
        'md': md,
        'trader_stored': trader_stored,
        'live_trend': live_trend or {},
        'live_trader': live_trader or {},
        'stage': (live_trader or {}).get('stage') or {},
        'ops': live_ops,
        'disagreement': (live_trader or {}).get('disagreement'),
        'linkage': live_ops.get('linkage') or [],
        'top_action_live': live_ops.get('top_action'),
        'top_action_stored': trader_stored.get('top_action'),
        'status_line': live_ops.get('status'),
        'held_rows': live_ops.get('held_rows') or [],
        'held': held or {},
        'close': close,
        'kline_date': kline_date,
    }


# ================================================================
# 规则实现（报告内 R01-R15）
# ================================================================


def _finding(rule, pair, ctx, severity, surface_a, surface_b, phenomenon,
             root_cause, fix_do, fix_how, b24=False):
    rep = ctx['report']
    return {
        'rule': rule,
        'pair': pair,
        'stock_id': rep.get('stock_id'),
        'stock': f"{rep.get('stock_code') or ''} {rep.get('stock_name') or ''}".strip(),
        'severity': severity,
        'surface_a': surface_a,
        'surface_b': surface_b,
        'phenomenon': phenomenon,
        'root_cause': root_cause,
        'fix_do': fix_do,
        'fix_how': fix_how,
        'b24': b24,
    }


def _ok(rule, ctx, note):
    return {'rule': rule, 'stock': f"{ctx['report'].get('stock_code')} {ctx['report'].get('stock_name')}",
            'severity': 'OK', 'note': note}


def rule_r01_zone_semantics(ctx):
    """R01 A×E：区间语义契约（021BF）。"""
    f = []
    pa, rating = ctx['pa'], ctx['rating']
    if not ctx['pa_available'] or not pa:
        return f
    zone = pa.get('zone_label')
    if rating in REDUCE_RATINGS:
        if zone and zone != '支撑参考区间':
            f.append(_finding(
                'R01', '总评级×价格建议', ctx, 'P0',
                f'评级「{rating}」（{ctx["total_score"]}分）',
                f'价格建议 zone_label=「{zone}」',
                '减仓/卖出档评级下价格建议仍以买入侧区间语义呈现（021BF 已修复的契约被违反）',
                'modules/price_advisor.py _gen_no_position 区间语义分支',
                True, '核对 price_advisor 版本/该报告是否为 021BF 前存量；重生成报告自愈，若复现则按 021BF 契约补齐分支'))
        act = str(pa.get('action_suggestion') or '')
        if ('买入' in act) and ('不建议买入' not in act):
            f.append(_finding(
                'R01', '总评级×价格建议', ctx, 'P0',
                f'评级「{rating}」= 减仓指令',
                f'价格建议操作词「{act}」含买入动作',
                '两处同时给出相反动作：评级要求减仓、价格建议引导买入',
                'modules/price_advisor.py _gen_no_position action_suggestion 分支 / _apply_capital_modifier',
                True, '外层调和：展示层对减仓档强制覆盖话术；若根因在 price_advisor 分支缺失，按 021BF 契约修复（非 B24 对象）'))
    elif rating in BUY_RATINGS and zone and zone not in ('买入区间',):
        f.append(_finding(
            'R01', '总评级×价格建议', ctx, 'P1',
            f'评级「{rating}」（买入档）',
            f'价格建议 zone_label=「{zone}」',
            '买入档评级但价格建议未呈现买入区间语义（可能被误读为不支持买入）',
            'modules/price_advisor.py _gen_no_position',
            True, '核对 BUY_SIDE_RATINGS 常量与该报告生成时点的代码版本'))
    return f


def rule_r02_state_machine(ctx):
    """R02 E内：价格建议状态机自洽。"""
    f = []
    pa = ctx['pa']
    if not ctx['pa_available'] or not pa or not pa.get('has_position'):
        return f
    close = pa.get('current_close')
    stop, tp = pa.get('stop_loss'), pa.get('take_profit')
    cost, state = pa.get('cost_price'), pa.get('state')
    if None in (close, stop, tp, state):
        return f
    expect = None
    if close < stop:
        expect = 'S4'
    elif close >= tp:
        expect = 'S1'
    elif cost and close >= cost:
        expect = 'S2'
    else:
        expect = 'S3'
    if state != expect:
        f.append(_finding(
            'R02', '价格建议×状态机', ctx, 'P0',
            f'状态 state={state}（{pa.get("state_name")}）',
            f'数字反推状态应为 {expect}（close={close}, stop={stop}, tp={tp}, cost={cost}）',
            '价格建议卡状态字段与自身价位数字矛盾，操作建议随状态失真',
            'modules/price_advisor.py _determine_action_by_state / _gen_with_position',
            True, '用库内该行 price_advice 复现 _determine_action_by_state 输入，定位字段错位；状态机本身为冻结语义，只修数据/入参'))
    base = None
    try:
        from modules.price_advisor import ACTION_MATRIX

        base = ACTION_MATRIX.get(ctx['rating'], {}).get(state)
    except Exception:  # noqa: BLE001
        base = None
    act = str(pa.get('action_suggestion') or '')
    if base and base not in act:
        f.append(_finding(
            'R02', '价格建议×状态机', ctx, 'P1',
            f'期望操作词基准「{base}」（ACTION_MATRIX[{ctx["rating"]}][{state}]）',
            f'实际 action_suggestion=「{act}」',
            '操作建议与状态×评级矩阵不一致（资金面修饰词之外的实际偏移），用户按卡执行会拿到与状态不符的指令',
            'modules/price_advisor.py ACTION_MATRIX / _apply_capital_modifier',
            True, '核对修饰词拼接逻辑是否吞并/替换基准词；矩阵本身勿动（021BH 已统一动作词）'))
    return f


def rule_r03_pa_numbers(ctx):
    """R03 E内：价格建议数字自洽 + 现价时点。"""
    f = []
    pa = ctx['pa']
    if not ctx['pa_available'] or not pa:
        return f
    close, cost = pa.get('current_close'), pa.get('cost_price')
    pp = pa.get('profit_pct')
    if pa.get('has_position') and None not in (close, cost, pp) and cost:
        calc = (close - cost) / cost * 100
        if abs(calc - pp) > 0.15:
            f.append(_finding(
                'R03', '价格建议内部', ctx, 'P1',
                f'profit_pct={pp}%',
                f'按 close/cost 反算 {calc:.1f}%',
                '浮盈数字与成本/现价不自洽（成本基数混用残留特征）',
                'modules/price_advisor.py _gen_with_position profit_pct',
                True, '确认该报告生成时 _read_cost_price 口径；021BR t3 已收口为聚合口径，存量报告重生成自愈'))
    if ctx['close'] is not None and close is not None and abs(close - ctx['close']) > 1e-6:
        same_day = ctx['kline_date'] == ctx['report_date']
        # 021BS t4：同数据日不一致按「生成时点双存储见证」分级——
        # price_at_rating（B12-T2：rating_date 当日 K 线收盘）== pa.current_close
        # 而现 K 线不同 → K 线事后修订（020I 补采覆盖），报告彼时自洽 = P2；
        # 见证不在/见证亦不符 → 生成即错位 = P1。
        witness_ok = False
        if same_day:
            wit = ctx.get('price_at_rating_witness') or {}
            w_price = wit.get('price_at_rating')
            witness_ok = bool(
                wit.get('rating_date') == ctx['report_date']
                and w_price is not None and close is not None
                and abs(float(close) - float(w_price)) <= 1e-6)
        if not same_day:
            sev = 'P2'
            phenomenon = '价格建议的现价与最新日K收盘不一致（K线晚于报告，时点差）'
            fix_how = '跨日差异属正常时点差，报告页标注即可'
        elif witness_ok:
            sev = 'P2'
            phenomenon = (
                '价格建议的现价与最新日K收盘不一致（同数据日·K线事后修订：'
                f'生成时点双存储见证一致——price_at_rating 与 current_close 同为 {close}，'
                f'现 K 线已被补采/修订覆盖为 {ctx["close"]}；报告彼时自洽，重生成自愈）')
            fix_how = 'K线事后修订型：无需修复（数据修订时点差），重生成报告自愈'
        else:
            sev = 'P1'
            phenomenon = ('价格建议的现价与最新日K收盘不一致'
                          '（同数据日且无生成时点见证——疑似采集竞态，需排查）')
            fix_how = '同数据日不一致且无生成时点见证：核对采集链时序与数据修订记录'
        f.append(_finding(
            'R03', '价格建议×K线', ctx, sev,
            f'price_advice.current_close={close}',
            f'raw_kline 最新收盘={ctx["close"]}（{ctx["kline_date"]}）',
            phenomenon,
            'daily_report 生成时点的 close 与当前 raw_kline 差异'
            + ('；020I 补采/数据修订链覆盖了生成时点 K 线' if witness_ok and same_day else ''),
            sev == 'P1',
            fix_how))
    return f


def rule_r04_score_tier(ctx):
    """R04 A×B：总分与档位边界（021BS 断言化：失配必须有三层标注面之一）。"""
    f = []
    score, rating = ctx['total_score'], ctx['rating']
    market = ctx.get('market') or 'a_stock'
    if score is None or not rating:
        return f
    exp = expected_tier(float(score), market)
    if exp and exp != rating:
        md = ctx['md']
        note_stored = ctx.get('score_tier_note_stored')
        note_live = ctx.get('score_tier_note_live')
        if md.get('hysteresis'):
            f.append(_finding(
                'R04', '总评级×评分', ctx, 'P2',
                f'总分 {score} → 期望档「{exp}」',
                f'实际评级「{rating}」（markdown 含迟滞标注）',
                '分数已跨回档位线但评级受 021AG 迟滞带保护（设计内，需口径标注而非修复）',
                'modules/rating_hysteresis.py / config.RATING_HYSTERESIS_MARGIN',
                False, '无需修复：维持迟滞标注可见性即可（markdown 已带说明行）'))
        elif note_stored:
            f.append(_finding(
                'R04', '总评级×评分', ctx, 'P2',
                f'总分 {score} → 期望档「{exp}」',
                f'实际评级「{rating}」（key_factors.score_tier_note 已落库）',
                '分数区间与评级不一致，已由报告组装层落库持续注记（021BS 修复面①）',
                'modules/advisor.py _build_key_factors / _build_markdown_single',
                False, '无需修复：注记随每次报告落库（非仅压制当日）'))
        elif note_live:
            f.append(_finding(
                'R04', '总评级×评分', ctx, 'P2',
                f'总分 {score} → 期望档「{exp}」',
                f'实际评级「{rating}」（读取面 score_tier_note 在场）',
                '存量报告未落注记，但读取路径/看板评分卡已按同源纯函数现算标注'
                '（021BS 修复面②③：消费方门控，B24 合规）',
                'blueprints/analysis.py 快照响应 / blueprints/portfolio/watchlist_scores.py',
                False, '无需修复：读取面标注覆盖存量报告；下次报告生成后转为落库注记'))
        else:
            f.append(_finding(
                'R04', '总评级×评分', ctx, 'P1',
                f'总分 {score} → 期望档「{exp}」（{market}）',
                f'实际评级「{rating}」（markdown/落库注记/读取面三层标注全缺）',
                '评级档位与分数区间不符且无任何持续说明——违反 021BS 标注契约'
                '（迟滞保持态/存量口径必须被标注，否则用户按分数区间理解会得到另一档指令）',
                'modules/rating_hysteresis.py score_tier_mismatch_note / advisor 构建器 / 读取路径',
                True,
                '外层调和（B24 边界）：核对 021BS 修复是否回归——报告组装层落注'
                '（_build_key_factors/_build_markdown_single）与读取面标注'
                '（analysis 快照 / watchlist_scores）三处任一应覆盖',
                b24=True))
    return f


def rule_r05_kline_vs_compass(ctx):
    """R05 B×C：技术面子分 vs 罗盘方向。"""
    f = []
    kscore = ((ctx['dims'].get('kline') or {}).get('score'))
    overall = ((ctx['live_trend'].get('overall') or {}).get('trend'))
    if kscore is None or not overall:
        return f
    if overall == 'down' and kscore >= 75:
        f.append(_finding(
            'R05', '评分明细×趋势罗盘', ctx, 'P1',
            f'技术面 {kscore} 分（强势）',
            '罗盘综合=下跌',
            '技术面高分与罗盘下跌同屏，用户难以理解「技术面强为何罗盘说跌」',
            '技术面子分=多因子动量加权和 vs 罗盘=三周期方向判定（月线定方向），口径不同',
            True, '报告页技术面卡加口径脚注（动量强度≠趋势方向）；不改评分引擎（R7 锁）'))
    elif overall == 'down' and kscore >= 65:
        f.append(_finding(
            'R05', '评分明细×趋势罗盘', ctx, 'P2',
            f'技术面 {kscore} 分',
            '罗盘综合=下跌',
            '技术面子分偏强与罗盘下跌并存，属动量/方向口径差异，需标注',
            '同上（口径不同）',
            False, '在报告页两卡间加一句口径说明即可'))
    elif overall == 'up' and kscore < 40:
        f.append(_finding(
            'R05', '评分明细×趋势罗盘', ctx, 'P2',
            f'技术面 {kscore} 分（弱势）',
            '罗盘综合=上涨',
            '技术面低分与罗盘上涨并存，属口径差异（动量弱 vs 周期方向多），需标注',
            '同上（口径不同）',
            False, '同上'))
    else:
        f.append(_ok('R05', ctx, f'技术面 {kscore} × 罗盘 {overall} 方向相容'))
    return f


def rule_r06_rating_vs_compass(ctx):
    """R06 A×C：评级方向 vs 罗盘综合方向。"""
    f = []
    overall = ((ctx['live_trend'].get('overall') or {}).get('trend'))
    strength = ((ctx['live_trend'].get('overall') or {}).get('strength'))
    rating = ctx['rating']
    if not overall or not rating:
        return f
    has_verdict = bool(ctx['disagreement'])
    if rating in BUY_RATINGS and overall == 'down':
        f.append(_finding(
            'R06', '总评级×趋势罗盘', ctx, 'P2' if has_verdict else 'P1',
            f'评级「{rating}」（买入方向）',
            f'罗盘综合=下跌（{strength}）',
            '评级要求买入而罗盘判定下跌' + ('；已由裁决信号标注（结构性风险/阶段领先通道）' if has_verdict else '；无任何分歧标注，用户无从判断听谁'),
            '评分动量 vs 罗盘月线方向的天然分歧面；标注通道在 trader_advisor.detect_disagreement',
            not has_verdict,
            '已有标注→维持；无标注→确认分歧检测输入（阶段置信/评级档）是否漏触发，在 trader_advisor 外层补档（勿动 B24）'))
    elif rating in REDUCE_RATINGS and overall == 'up':
        f.append(_finding(
            'R06', '总评级×趋势罗盘', ctx, 'P2',
            f'评级「{rating}」（减仓方向）',
            f'罗盘综合=上涨（{strength}）',
            '减仓档评级与罗盘上涨并存，属评分动量与周期方向的口径差，需标注',
            '同上',
            False, '报告页口径脚注即可'))
    else:
        f.append(_ok('R06', ctx, f'评级「{rating}」× 罗盘 {overall} 相容'))
    return f


def rule_r07_disagreement_channel(ctx):
    """R07 A×D/G：分歧触发条件成立时裁决信号必须在场。

    对齐 detect_disagreement 的真实触发表（含置信度门控）：
      - 低置信阶段（conf='低'）一律不硬造分歧（早退分支，设计内默认跟评级）；
      - reversal_opportunity：减仓档 × 底部吸筹/震荡（非低置信）；
      - structural_risk：买入档 × 顶部出货/下跌（非低置信）；
      - stage_leads_rating：持有观望 × 顶部出货/下跌 且 conf='强'（021BR 补档）。
    """
    f = []
    rating, stage = ctx['rating'], ctx['stage']
    if not rating or not stage:
        return f
    code, conf = stage.get('code'), stage.get('confidence')
    expect_type = None
    gated_by_low_conf = False
    if rating in REDUCE_RATINGS and code in ('accumulation', 'range'):
        if conf == '低':
            gated_by_low_conf = True
        else:
            expect_type = 'reversal_opportunity'
    elif rating in BUY_RATINGS and code in ('distribution', 'decline'):
        if conf == '低':
            gated_by_low_conf = True
        else:
            expect_type = 'structural_risk'
    elif rating == HOLD_RATING and conf == '强' and code in ('distribution', 'decline'):
        expect_type = 'stage_leads_rating'
    dis = ctx['disagreement']
    if gated_by_low_conf:
        f.append(_ok('R07', ctx, f'评级「{rating}」× 阶段「{stage.get("name")}（低置信）」——'
                                 '设计内不硬造分歧（detect_disagreement 低置信早退，默认跟评级）'))
    elif expect_type:
        if not dis:
            f.append(_finding(
                'R07', '总评级×操盘手（裁决信号）', ctx, 'P1',
                f'评级「{rating}」× 阶段「{stage.get("name")}（{conf}）」满足分歧条件',
                'disagreement 缺失',
                '分歧检测条件成立但裁决信号未产出，矛盾场景无任何标注（用户实测三方矛盾的空隙档）',
                'modules/trader_advisor.py detect_disagreement 触发表',
                True, '在 detect_disagreement 触发表补档（021BR 已补 stage_leads_rating，核对剩余组合）；零触碰 B24'))
        elif dis.get('type') != expect_type:
            f.append(_finding(
                'R07', '总评级×操盘手（裁决信号）', ctx, 'P2',
                f'期望分歧类型 {expect_type}',
                f'实际 {dis.get("type")}',
                '分歧已标注但类型与触发表预期不一致（多条件并存时的取舍），需核对优先级',
                'modules/trader_advisor.py detect_disagreement',
                False, '核对触发表优先级设计即可，一般无需修复'))
        else:
            f.append(_ok('R07', ctx, f'分歧条件成立且已标注（{expect_type}）——正常分层·已调和'))
    else:
        f.append(_ok('R07', ctx, '无分歧条件（评级×阶段相容）'))
    return f


def rule_r08_hierarchy(ctx):
    """R08 D内：层级契约①——止损已触发时不得有「持有」行/「持仓者持有」措辞。"""
    f = []
    status = ctx['status_line']
    if not status or status.get('kind') != 'stop_triggered':
        return f
    hold_rows = [r for r in ctx['held_rows'] if str(r.get('action')) == '持有']
    if hold_rows:
        f.append(_finding(
            'R08', '操盘手矩阵内部', ctx, 'P0',
            '状态行：止损纪律已触发',
            '矩阵存在「持有」动作行',
            '纪律已触发与「持有」指令同屏（层级契约①被违反——021BR 测试锁定的行为）',
            'modules/trader_advisor.py build_operations_matrix 层级门控',
            True, '该形态即 021BR 修复目标；若复现说明回归，按 test_zhongmian_no_hold_when_stop_triggered 契约修复'))
    bad_link = [ln for ln in ctx['linkage'] if '持仓者持有' in str(ln)]
    if bad_link:
        f.append(_finding(
            'R08', '操盘手矩阵内部', ctx, 'P0',
            '状态行：止损纪律已触发',
            f'联动行：「{bad_link[0]}」',
            '止损已触发时联动行仍输出「持仓者持有」（矩阵内部指令互斥）',
            'modules/trader_advisor.py signal_stage_linkage 止损门控',
            True, '同上——021BR 止损门控回归检查'))
    if not hold_rows and not bad_link:
        f.append(_ok('R08', ctx, '止损已触发状态下无持有类行/措辞（层级契约①成立）'))
    return f


def rule_r09_three_stop_numbers(ctx):
    """R09 D×E：top_action 止损数字 × pa.stop_loss × 纪律线。"""
    f = []
    pa = ctx['pa']
    ta = ctx['top_action_live'] or ''
    m = STOP_NUM_RE.search(str(ta))
    if not m:
        return f
    matrix_stop = float(m.group(1))
    pa_stop = pa.get('stop_loss') if ctx['pa_available'] else None
    cost = (ctx['held'] or {}).get('avg_cost')
    disc = discipline_stop(cost)
    vals = {'矩阵止损': matrix_stop, '价格建议止损': pa_stop, '纪律线(现成本)': disc}
    known = {k: v for k, v in vals.items() if v is not None}
    distinct = round(len({round(v, 2) for v in known.values()}), 2)
    if distinct >= 3:
        cost_now = cost
        cost_at_report = (pa or {}).get('cost_price')
        data_changed = cost_now and cost_at_report and abs(cost_now - cost_at_report) > 0.005
        sev = 'P2' if data_changed else 'P1'
        f.append(_finding(
            'R09', '操盘手×价格建议', ctx, sev,
            ' / '.join(f'{k} {v}' for k, v in known.items()),
            f'同页同日三个止损数字（现成本 {cost_now} vs 报告期成本 {cost_at_report}）',
            ('持仓数据在报告生成后变更：报告期双基数残留 + 现算新基数并存（重生成报告自愈）'
             if data_changed else '同一数据日三个止损数字并存（时序/口径同源化未生效或回归）'),
            'daily_report 生成时序 + price_advisor 成本基数（021BR 缺陷 A4 修复面）',
            not data_changed,
            '数据变更型：重生成当日报告即合流；代码型：核对 daily_report 的 price_advice_override 链与 _stop_level 双源口径'))
    elif distinct == 2:
        f.append(_ok('R09', ctx, f'两个止损口径（{vals}）——双源取高者设计内，页面已标注'))
    else:
        f.append(_ok('R09', ctx, f'止损数字同源（{known}）'))
    return f


def rule_r10_trigger_crosscheck(ctx):
    """R10 D×E：矩阵触发状态与价格建议状态机互证。"""
    f = []
    status = ctx['status_line']
    pa = ctx['pa']
    if not status or status.get('kind') != 'stop_triggered' or not ctx['pa_available'] or not pa:
        return f
    close, pa_stop = status.get('close'), pa.get('stop_loss')
    if pa_stop is None or close is None:
        return f
    if close < pa_stop:
        if pa.get('state') != 'S4' and pa.get('has_position'):
            f.append(_finding(
                'R10', '操盘手×价格建议', ctx, 'P0',
                f'矩阵：现价 {close} 已破价格建议止损 {pa_stop}',
                f'价格建议 state={pa.get("state")}（非已破止损）',
                '同一价格事实下两模块给出相反状态判定（矩阵说已破止损、价格建议卡说未破）',
                'modules/price_advisor.py _determine_action_by_state 输入与矩阵 close 不同源',
                True, '核对报告期 pa 的 current_close 与矩阵 close 是否同源；状态机判定逻辑本身冻结语义，只修数据源'))
        else:
            f.append(_ok('R10', ctx, f'矩阵与价格建议一致判定破止损（{close} < {pa_stop}, S{pa.get("state")}）'))
    else:
        f.append(_finding(
            'R10', '操盘手×价格建议', ctx, 'P2',
            f'矩阵止损已触发（触发线={status.get("stop_line")}，双源取高者）',
            f'现价 {close} ≥ 价格建议止损 {pa_stop}（state={pa.get("state")}）',
            '矩阵按「纪律线/价格建议止损取高者」判定触发，价格建议按自身止损线未触发——口径差异需页面标注',
            'modules/trader_advisor.py _stop_level 双源口径',
            False, '维持双源口径，矩阵 status.text 已含触发线数字即满足标注；可在价格建议卡补一句「矩阵止损取纪律线/建议止损较高者」'))
    return f


def rule_r11_compass_vs_stage(ctx):
    """R11 C×D：罗盘方向 vs 操盘手阶段。"""
    f = []
    overall = ((ctx['live_trend'].get('overall') or {}).get('trend'))
    stage_code = (ctx['stage'] or {}).get('code')
    if not overall or not stage_code:
        return f
    weak_stage = stage_code in ('decline', 'distribution')
    strong_stage = stage_code in ('markup_full', 'markup_early')
    if weak_stage and overall == 'up':
        f.append(_finding(
            'R11', '趋势罗盘×操盘手阶段', ctx, 'P2',
            '罗盘综合=上涨',
            f'操盘手阶段={ (ctx["stage"] or {}).get("name") }',
            '罗盘上涨与弱势阶段并存：罗盘月线定方向、阶段看量价结构（高位派发可在月线上行中出现），口径差异需标注',
            'trend_analyzer（月线定方向）vs trader_advisor.classify_stage（量价结构）',
            False, '报告页两卡口径脚注；不得为消除差异改任一判定逻辑'))
    elif strong_stage and overall == 'down':
        f.append(_finding(
            'R11', '趋势罗盘×操盘手阶段', ctx, 'P2',
            '罗盘综合=下跌',
            f'操盘手阶段={ (ctx["stage"] or {}).get("name") }',
            '罗盘下跌与强势阶段并存，同上口径差异需标注',
            '同上',
            False, '同上'))
    else:
        f.append(_ok('R11', ctx, f'罗盘 {overall} × 阶段 {stage_code} 方向相容'))
    return f


def rule_r12_markdown(ctx):
    """R12 A×F：markdown 综合评分行/操作建议行 与 rating 列自洽。"""
    f = []
    md = ctx['md']
    rating = ctx['rating']
    if not md or not md.get('raw'):
        return f
    if md.get('rating') and rating and md['rating'] != rating:
        f.append(_finding(
            'R12', '总评级×建议文字', ctx, 'P0',
            f'rating 列=「{rating}」',
            f'markdown 综合评分行=「{md["rating"]}级」',
            '同一报告内评级字段与建议文字档位矛盾（页面正文与摘要指令不一致）',
            'advisor._build_markdown_single 与 _save_report 写入路径的字段错位',
            True, '用该行 markdown 与 rating 列直接复现；若为迟滞文案拼接错位，修 advisor 模块内写库辅助函数（非 generate_advice 本体，仍需谨慎评估 B24 边界）',
            b24=True))
    if md.get('action_line') and rating:
        # markdown 生成时的持仓/浮盈状态无法精确回放，只按当前持仓核对方向大类
        expect_words = set()
        for key in ACTION_ALIGN.get(rating, {}):
            expect_words.add(ACTION_ALIGN[rating][key])
        act = md['action_line'].strip()
        if expect_words and act not in expect_words:
            f.append(_finding(
                'R12', '总评级×建议文字', ctx, 'P2',
                f'评级「{rating}」的合法动作词 {sorted(expect_words)}',
                f'markdown 操作建议行=「{act}」',
                '操作建议行不在当前评级×持仓状态的合法动作词集合内（可能为报告期持仓状态与现不同，或 021BH 动作词统一前的存量文案）',
                'advisor._determine_action（021BH 对齐表）/ 报告期持仓快照',
                False, '先重生成报告观察是否自愈；持续复现再核对 _determine_action 调用入参'))
    return f


def rule_r13_trader_stored_vs_live(ctx):
    """R13 D存量×D现算：预计算摘要 vs 现算（时点分级）。"""
    f = []
    st, live = ctx['trader_stored'], ctx['live_trader']
    if not st or not live:
        return f
    same_day = ctx['kline_date'] == ctx['report_date']
    stage_st = st.get('stage_name')
    stage_live = ((live.get('stage') or {}).get('name'))
    if stage_st and stage_live and stage_st != stage_live:
        weak = {'下跌期', '顶部出货区'}
        strong = {'主升期', '拉升初期'}
        direction_conflict = (stage_st in weak and stage_live in strong) or (
            stage_st in strong and stage_live in weak)
        f.append(_finding(
            'R13', '操盘手摘要×操盘手现算', ctx,
            'P1' if (same_day and direction_conflict) else 'P2',
            f'报告 key_factors.trader.stage_name=「{stage_st}」',
            f'现算阶段=「{stage_live}」',
            '看板/报告读预计算阶段与个股页现算阶段不一致'
            + ('（同数据日且方向相反——真矛盾）' if (same_day and direction_conflict) else '（时点差/非方向性差异）'),
            'daily_report 预计算时点 vs live 重算的数据演进',
            same_day and direction_conflict,
            '时点差型：重生成报告自愈；同日矛盾型：核对 _gather_inputs 输入稳定性（读库竞态）'))
    ta_st, ta_live = ctx['top_action_stored'], ctx['top_action_live']
    if ta_st and ta_live and ta_st != ta_live:
        num_st = STOP_NUM_RE.search(str(ta_st))
        num_live = STOP_NUM_RE.search(str(ta_live))
        if num_st and num_live and num_st.group(1) != num_live.group(1):
            pa = ctx['pa'] or {}
            disc_report = discipline_stop(pa.get('cost_price'))
            sev, why = _classify_stop_number_mismatch(
                float(num_st.group(1)), pa.get('stop_loss') if ctx['pa_available'] else None,
                disc_report)
            f.append(_finding(
                'R13', '操盘手摘要×操盘手现算', ctx, sev,
                f'摘要 top_action=「{ta_st}」',
                f'现算 top_action=「{ta_live}」',
                '看板 chip 与个股页矩阵的止损数字不一致——' + why,
                'daily_report 生成时 price_advice_override 链 / 持仓变更时点',
                sev == 'P1', '重生成报告自愈；同日复现（非 t3 前特征）需查生成链时序'))
    return f


def rule_r14_pa_stored_vs_recomputed(ctx):
    """R14 E存储×E现算：成本/止损基数与当前持仓聚合核对。"""
    f = []
    pa = ctx['pa']
    if not ctx['pa_available'] or not pa or not pa.get('has_position'):
        return f
    cost_now = (ctx['held'] or {}).get('avg_cost')
    cost_stored = pa.get('cost_price')
    if cost_now and cost_stored and abs(float(cost_now) - float(cost_stored)) > 0.005:
        f.append(_finding(
            'R14', '价格建议×持仓口径', ctx, 'P2',
            f'存量 price_advice.cost_price={cost_stored}',
            f'当前持仓聚合成本={float(cost_now):.4f}',
            '价格建议成本基数与当前持仓不一致（持仓在报告生成后变更/或旧单账户口径残留）——报告重生成自愈',
            'modules/price_advisor.py _read_cost_price（021BR t3 已聚合收口）/ 持仓数据时点',
            False, '重生成当日报告；若重生成后仍不一致则为聚合收口回归，按 TestCostAggregation021BR 契约排查'))
    return f


def rule_r15_weighted_sum(ctx):
    """R15 B内：四维加权 ≈ 总分（宽容差）。"""
    f = []
    dims = ctx['dims']
    score = ctx['total_score']
    if not dims or score is None:
        return f
    total_w = 0.0
    acc = 0.0
    for k in ('kline', 'fundamental', 'capital_flow', 'news'):
        d = dims.get(k) or {}
        s, w = d.get('score'), d.get('weight')
        if s is None or w is None:
            continue
        acc += float(s) * float(w)
        total_w += float(w)
    if total_w <= 0.5:  # 维度缺失过半，跳过
        return f
    if abs(total_w - 1.0) > 0.05:
        # 港股收缩/行业覆盖等合法偏差，仅 INFO
        f.append(_finding(
            'R15', '评分明细内部', ctx, 'INFO',
            f'key_factors 权重合计 {total_w:.4f}',
            f'总分 {score} vs 加权 {acc / total_w:.1f}',
            '维度权重合计偏离 1（行业覆盖/热加载/港股收缩所致），核对即可',
            'config_weights.json industry_overrides / scoring_engine 归一化',
            False, '无需修复：口径已由引擎归一化处理'))
        return f
    normalized = acc / total_w
    if abs(normalized - float(score)) > 3.0:
        f.append(_finding(
            'R15', '评分明细×总分', ctx, 'P2',
            f'Σ(维分×权重)/Σ权重={normalized:.1f}',
            f'total_score={score}',
            '四维加权与总分偏差超 3 分（引擎归一化/迟滞快照/子项降级等合法路径或存量口径）',
            'modules/scoring_engine.analyze 归一化 + 021AG 迟滞',
            False, '抽样核对 analyze() 重放；确认为归一化路径则维持标注'))
    return f


# ================================================================
# 规则实现（报告↔看板 F01-F05）
# ================================================================


def flow_f01_action_list(ctx_list, action_list, stocks, prev_reports, cur):
    """F01 行动清单 vs 报告评级/操盘手。"""
    from modules.alert_engine import RATING_ORDER

    f = []
    latest_by_id = {c['report']['stock_id']: c for c in ctx_list}
    overview = {o['stock_id']: o for o in action_list.get('overview') or []}
    for sid, ov in overview.items():
        ctx = latest_by_id.get(sid)
        if not ctx:
            f.append(_finding(
                'F01', '行动清单×报告', _fake_ctx(stocks, sid), 'P1',
                f'行动清单 overview 含 stock_id={sid}',
                '自选股/报告集中无此股',
                '行动清单出现非活跃自选股的概览行',
                'modules/action_list.py stocks 查询口径', True, '核对 stocks.status 过滤条件'))
            continue
        rep = ctx['report']
        if (ov.get('rating') or '') != (rep.get('rating') or ''):
            f.append(_finding(
                'F01', '行动清单×报告', ctx, 'P0',
                f'报告 rating=「{rep.get("rating")}」',
                f'行动清单 overview rating=「{ov.get("rating")}」',
                '看板行动清单与报告评级不一致（同一张表两查询结果分叉）',
                'modules/action_list.py 路1 查询 vs blueprints 019D 口径', True, '排查两个最新行选取口径（report_type/status/日期）差异'))
        if ov.get('total_score') is not None and rep.get('total_score') is not None \
                and abs(float(ov['total_score']) - float(rep['total_score'])) > 0.05:
            f.append(_finding(
                'F01', '行动清单×报告', ctx, 'P0',
                f'报告 total_score={rep.get("total_score")}',
                f'行动清单 total_score={ov.get("total_score")}',
                '看板行动清单与报告总分不一致', '同上', True, '同上'))
        # 021BS t4 N01 兜底形态：stored 摘要缺失（批次后刷新覆盖）时，
        # 行动清单 overview 走 live 兜底 → 以 live 现算为核对基准（一致=兜底调和成立）
        st = ctx['trader_stored'] or {}
        if st.get('stage_name'):
            base_dis, base_src = bool(st.get('has_disagreement')), 'stored'
        else:
            base_dis = bool((ctx.get('live_trader') or {}).get('disagreement'))
            base_src = 'live'
        if bool(ov.get('has_disagreement')) != base_dis:
            f.append(_finding(
                'F01', '行动清单×报告', ctx, 'P1',
                f'{base_src} 面 has_disagreement={base_dis}',
                f'行动清单 overview.has_disagreement={bool(ov.get("has_disagreement"))}',
                '分歧标记在两个看板读取面不一致（stored 在场时应同读一 JSON；'
                'stored 缺失时行动清单走 live 兜底，应与 live 现算一致）',
                'modules/action_list.py trader 摘要读取面（021BS N01 兜底）', True,
                '核对 trader 摘要读取面是否统一走 derive_trader_signal_summary'))
    # 评级变动项方向核对
    for it in action_list.get('items') or []:
        if it.get('kind') not in ('rating_upgrade', 'rating_downgrade', 'rating_change'):
            continue
        sid = it.get('stock_id')
        latest = latest_by_id.get(sid)
        prev = prev_reports.get(sid)
        if not latest or not prev:
            continue
        new_r, old_r = latest['report'].get('rating'), prev.get('rating')
        o_new, o_old = RATING_ORDER.get(new_r), RATING_ORDER.get(old_r)
        detail = it.get('detail') or {}
        if detail.get('new_rating') and new_r and detail['new_rating'] != new_r:
            f.append(_finding(
                'F01', '行动清单×报告', latest, 'P0',
                f'清单变动项 new_rating=「{detail["new_rating"]}」',
                f'报告最新 rating=「{new_r}」',
                '评级变动项的新评级与报告不符（清单读了别期报告）',
                'modules/action_list.py _rating_move_item 输入行序', True, '核对 rn=1/rn=2 装配'))
        if o_new is not None and o_old is not None and it.get('kind') != 'rating_change':
            expect_kind = 'rating_upgrade' if o_new > o_old else 'rating_downgrade'
            if it.get('kind') != expect_kind:
                f.append(_finding(
                    'F01', '行动清单×报告', latest, 'P0',
                    f'清单方向 kind={it.get("kind")}',
                    f'按 RATING_ORDER 应为 {expect_kind}（{old_r}→{new_r}）',
                    '评级升降方向判定与档位顺序表相反',
                    'modules/action_list.py 方向判定', True, '按 RATING_ORDER 顺序修正方向判定'))
    # 持仓纪律项数字核对
    for it in action_list.get('items') or []:
        if it.get('kind') != 'stop_discipline':
            continue
        sid = it.get('stock_id')
        ctx = latest_by_id.get(sid)
        if not ctx:
            continue
        k = load_kline_head(cur, sid)
        held = ctx['held'] or {}
        close = float(k['close']) if k and k['close'] else None
        disc = discipline_stop(held.get('avg_cost'))
        pa_stop = (ctx['pa'] or {}).get('stop_loss')
        eff = max([v for v in (disc, pa_stop) if v], default=None)
        d = it.get('detail') or {}
        if close is not None and d.get('close') is not None and abs(close - float(d['close'])) > 1e-6:
            f.append(_finding(
                'F01', '行动清单×报告', ctx, 'P1',
                f'清单纪律项 close={d.get("close")}',
                f'raw_kline 最新收盘={close}',
                '持仓纪律项现价与 K 线收盘不一致（触发判定价格源漂移）',
                'modules/action_list.py _scan_stop_discipline', True, '核对扫描时点与 K 线时点'))
        if eff is not None and d.get('stop_line') is not None and abs(eff - float(d['stop_line'])) > 0.01:
            f.append(_finding(
                'F01', '行动清单×报告', ctx, 'P0',
                f'清单有效止损={d.get("stop_line")}',
                f'现算有效止损={eff}（纪律线 {disc} / 建议止损 {pa_stop}）',
                '持仓纪律项止损数字与双源取高者现算不一致（会给出错误的风控数字）',
                '同上', True, '重算该股双源止损并核对清单 detail'))
    return f


def _fake_ctx(stocks, sid):
    s = next((s for s in stocks if s['id'] == sid), {})
    return {'report': {'stock_id': sid, 'stock_code': s.get('symbol'), 'stock_name': s.get('name')}}


def flow_f02_chip_vs_pa(ctx_list, ws_by_id):
    """F02 看板 top_action chip vs 报告 price_advice。"""
    f = []
    for ctx in ctx_list:
        sid = ctx['report']['stock_id']
        ws = ws_by_id.get(sid) or {}
        chip = ((ws.get('trader_signal') or {}).get('top_action'))
        stored_ta = ctx['top_action_stored']
        if chip and stored_ta and chip != stored_ta:
            f.append(_finding(
                'F02', '看板chip×报告', ctx, 'P0',
                f'看板 chip top_action=「{chip}」',
                f'报告 key_factors.trader.top_action=「{stored_ta}」',
                '看板卡直接读取报告 JSON，两处却不同（数据流断裂特征）',
                'blueprints/portfolio/watchlist_scores.py _derive_trader_signal', True, '核对是否读到不同报告行（report_date 差异）'))
        m = STOP_NUM_RE.search(str(chip or ''))
        if m:
            chip_stop = float(m.group(1))
            pa = ctx['pa'] or {}
            pa_stop = pa.get('stop_loss') if ctx['pa_available'] else None
            cost_at_report = pa.get('cost_price')
            disc_report = discipline_stop(cost_at_report)
            known = {'chip止损': chip_stop, '报告pa止损': pa_stop, '报告期纪律线': disc_report}
            known = {k: v for k, v in known.items() if v is not None}
            if len({round(v, 2) for v in known.values()}) >= 3:
                f.append(_finding(
                    'F02', '看板chip×报告', ctx, 'P1',
                    ' / '.join(f'{k} {v}' for k, v in known.items()),
                    '看板卡与报告页同日三个止损数字并存',
                    '021BR 缺陷 A4 的残留形态：chip 为报告期口径、报告内 pa 为另一口径（或旧单账户成本残留）',
                    'daily_report 生成时序（override 链）+ price_advisor 成本口径',
                    True, '确认该报告 generated_at 是否早于 021BR t3 修复；早则重生成自愈，晚则按 override 链排查'))
            elif pa_stop is not None and chip_stop is not None and abs(chip_stop - pa_stop) > 0.01:
                cost_now = ((ctx['held'] or {}).get('avg_cost') or 0)
                data_changed = bool(cost_at_report) and bool(cost_now) and \
                    abs(float(cost_now) - float(cost_at_report)) > 0.005
                disc_report = discipline_stop(cost_at_report)
                if data_changed:
                    sev, why = 'P2', '持仓数据在报告生成后变更（时点差，重生成自愈）'
                else:
                    sev, why = _classify_stop_number_mismatch(
                        chip_stop, pa_stop, disc_report)
                f.append(_finding(
                    'F02', '看板chip×报告', ctx, sev,
                    f'chip 止损 {chip_stop} vs 报告 pa.stop_loss {pa_stop}',
                    f'chip 与报告止损数字不同（报告期纪律线 {disc_report}；'
                    f'现成本纪律线 {discipline_stop(cost_now) if cost_now else None}）',
                    '看板 chip 摘要数字与报告价格建议止损不一致——' + why,
                    'key_factors.top_action 与 price_advice 的同源链路（daily_report 生成时序）',
                    sev == 'P1', '重生成报告合流；同日复现（非 t3 前特征）查 override 链'))
        pa_zone = ws.get('pa_zone')
        pa = ctx['pa'] or {}
        if pa_zone and ctx['pa_available']:
            for zk, pk in (('low', 'buy_range_low'), ('high', 'buy_range_high'), ('stop_loss', 'stop_loss')):
                zv, pv = pa_zone.get(zk), pa.get(pk)
                if zv is not None and pv is not None and abs(float(zv) - float(pv)) > 0.01:
                    f.append(_finding(
                        'F02', '看板chip×报告', ctx, 'P1',
                        f'看板 pa_zone.{zk}={zv}',
                        f'报告 price_advice.{pk}={pv}',
                        '看板建议卡区间数字与报告价格建议不一致（应同读一份 JSON）',
                        'watchlist_scores._parse_pa_zone 读取面', True, '核对是否读到不同报告行'))
    return f


def flow_f03_holdings_vs_matrix(ctx_list, holdings_rows, ws_by_id):
    """F03 持仓页价格 vs 矩阵现价（盘中 vs 收盘双口径）。"""
    f = []
    px_by_sid = {}
    for h in holdings_rows:
        px_by_sid.setdefault(h['stock_id'], h)
    for ctx in ctx_list:
        sid = ctx['report']['stock_id']
        h = px_by_sid.get(sid)
        if not h:
            continue
        pc_price = h.get('latest_price')
        close = ctx['close']
        if pc_price is not None and close is not None:
            pct = (float(pc_price) - float(close)) / float(close) * 100
            expired = bool(h.get('price_expired'))
            if abs(pct) > 3.0:
                f.append(_finding(
                    'F03', '持仓页×矩阵现价', ctx, 'P1',
                    f'持仓页 price_cache={pc_price}',
                    f'矩阵现价（日K收盘）={close}（{ctx["kline_date"]}）',
                    f'两价格源偏离 {pct:+.1f}%，超出盘中快照合理范围（疑似过期缓存或坏快照）',
                    'price_cache 刷新链路 / TTL 标记', True, '触发价格刷新并核对 price_expired；持续偏离查刷新任务'))
            elif abs(pct) > 0:
                f.append(_finding(
                    'F03', '持仓页×矩阵现价', ctx, 'P2',
                    f'持仓页 {pc_price}（{h.get("price_updated_at")} 盘中快照）',
                    f'矩阵 {close}（{ctx["kline_date"]} 日K收盘）',
                    f'双口径差异 {pct:+.1f}%——页面已标注「触发判定以日K收盘为准」，属特性非缺陷'
                    + ('；price_cache 已过期标记' if expired else ''),
                    'price_cache（盘中）vs raw_kline（收盘）双口径',
                    False, '无需修复：维持现有口径标注；过期标记如实展示'))
        # 持仓数量/成本 vs 现算矩阵聚合
        ops = ctx['ops'] or {}
        hold_live = ops.get('holding') or {}
        ws = ws_by_id.get(sid) or {}
        if hold_live and ws.get('quantity') is not None:
            if int(ws['quantity'] or 0) != int(hold_live.get('qty') or 0):
                f.append(_finding(
                    'F03', '持仓页×矩阵', ctx, 'P2',
                    f'看板/持仓页展示数量={ws["quantity"]}（最大单账户口径）',
                    f'矩阵聚合数量={hold_live.get("qty")}（账户无关 SUM 口径）',
                    '多账户分仓时看板展示最大单账户、矩阵为聚合口径——021W 下两口径并存，需标注',
                    'watchlist_scores 最大持仓子查询 vs trader_advisor SUM 聚合',
                    False, '维持两口径，看板卡已有分账户明细可核对；如需统一须改展示口径（涉及面大，另立批次）'))
    return f


def flow_f04_alerts_vs_report(ctx_list, alerts_today, rules, action_list, today):
    """F04 预警铃铛 vs 报告。"""
    f = []
    rules_by_type = {}
    for r in rules:
        if r.get('enabled'):
            rules_by_type.setdefault(r['rule_type'], []).append(r)
    alerts_by_stock = {}
    for a in alerts_today:
        alerts_by_stock.setdefault(a['stock_id'], []).append(a)
    unread_total = sum(1 for a in alerts_today if not a.get('is_read'))
    stats = action_list.get('stats') or {}
    if stats.get('unread_alerts_today') is not None and \
            int(stats['unread_alerts_today']) != int(unread_total):
        f.append(_finding(
            'F04', '预警铃铛×行动清单', {'report': {'stock_id': None, 'stock_code': '全局', 'stock_name': ''}}, 'P1',
            f'alert_history 今日未读 {unread_total} 条',
            f'行动清单 stats.unread_alerts_today={stats.get("unread_alerts_today")}',
            '铃铛未读计数与行动清单统计不一致',
            'modules/action_list.py 路3 vs alert_history 直查', True, '核对 is_read 过滤与统计时点'))
    for ctx in ctx_list:
        sid = ctx['report']['stock_id']
        rep = ctx['report']
        als = alerts_by_stock.get(sid) or []
        # rating_change 预警 vs 报告评级
        for a in (x for x in als if x['alert_type'] == 'rating_change'):
            m = re.search(r'→ ?(.+?)[，,]', a['message'] or '')
            if m:
                alert_new = m.group(1).strip()
                if rep.get('rating') and alert_new != rep['rating']:
                    same_day = ctx['kline_date'] == ctx['report_date']
                    f.append(_finding(
                        'F04', '预警铃铛×报告', ctx, 'P1' if same_day else 'P2',
                        f'预警 new_rating=「{alert_new}」',
                        f'报告 rating=「{rep.get("rating")}」',
                        '评级预警的新档位与报告评级不一致（预警读 ratings_history，报告读 daily_reports——两表同日错位时用户看到两个"最新评级"）',
                        'alert_engine.check_rating_change（ratings_history）vs daily_reports',
                        False, '以报告为准展示；若同日常态错位需评估 ratings_history 写入时点（涉评级写入链路，单列方案）'))
        # score_below 触发 vs 报告分数（时点感知：报告在预警后重算 → 时点差）
        for a in (x for x in als if x['alert_type'] == 'score_below'):
            thr = None
            m_thr = re.search(r'阈值 ([\d.]+)', a['message'] or '')
            if m_thr:
                thr = float(m_thr.group(1))
            if thr is None:
                for r in rules_by_type.get('score_below') or []:
                    thr = r.get('threshold') if r.get('threshold') is not None else 65
                thr = float(thr or 65)
            if ctx['total_score'] is None:
                continue
            if float(ctx['total_score']) >= thr:
                trig_at = str(a.get('triggered_at') or '').replace('T', ' ')
                gen_at = str(ctx.get('generated_at') or '').replace('T', ' ')
                report_newer = bool(gen_at) and bool(trig_at) and gen_at[:19] > trig_at[:19]
                f.append(_finding(
                    'F04', '预警铃铛×报告', ctx, 'P2' if report_newer else 'P1',
                    f'今日 score_below 预警（触发值见消息，阈值 {thr}）',
                    f'报告总分 {ctx["total_score"]} ≥ 阈值（generated_at={gen_at[:19]}，'
                    f'预警 triggered_at={str(trig_at)[:19]}）',
                    '预警说跌破阈值而报告分数在阈值上方'
                    + ('——报告在预警扫描之后被重算（021K 过期重评/手动刷新），铃铛消息保留旧分数，'
                       '属时点差，明日扫描自然对齐' if report_newer
                       else '（两读取面口径差：analysis_results vs daily_reports 同期不同值）'),
                    'alert_engine.check_score_below（analysis_results）vs daily_reports 读取时点',
                    not report_newer,
                    '时点差型：无需修复（可评估铃铛消息标注时点）；同期错位型：核对两表写入链并统一读取面'))
        # 漏报检查：报告分低于全局阈值但今日无 score_below 预警
        if 'score_below' in rules_by_type and ctx['total_score'] is not None \
                and float(ctx['total_score']) < 65 \
                and not any(x['alert_type'] == 'score_below' for x in als):
            f.append(_finding(
                'F04', '预警铃铛×报告', ctx, 'P2',
                f'报告总分 {ctx["total_score"]} < 全局阈值 65',
                '今日无 score_below 预警',
                '分数低于阈值但铃铛未响（可能为扫描时点早于报告生成 / 幂等去重），需人工核对',
                'alert_engine.scan_once 挂载时点（15:54 批次）vs 报告生成时点',
                False, '若常态化漏报，评估预警挂载点后移或按报告完成后补偿扫描（单列方案）'))
    return f


def flow_f05_watchlist_scores_vs_report(ctx_list, ws_by_id):
    """F05 watchlist-scores 评级 vs 报告评级。"""
    f = []
    for ctx in ctx_list:
        sid = ctx['report']['stock_id']
        ws = ws_by_id.get(sid)
        if not ws:
            f.append(_finding(
                'F05', '看板评分×报告', ctx, 'P1',
                'watchlist-scores 无此股行',
                'daily_reports 有最新 ok 报告',
                '看板评分列表缺失有报告的自选股（JOIN 口径问题）',
                'watchlist_scores 019R 派生表', True, '核对 _latest_report_join_sql 与 stocks.status 过滤'))
            continue
        rep = ctx['report']
        if (ws.get('rating') or '') != (rep.get('rating') or ''):
            f.append(_finding(
                'F05', '看板评分×报告', ctx, 'P0',
                f'watchlist-scores rating=「{ws.get("rating")}」',
                f'报告 rating=「{rep.get("rating")}」',
                '看板评分卡与报告评级不一致（同表两查询口径分叉——019R 修复目标回归）',
                'watchlist_scores vs blueprints/analysis report-latest 选取口径',
                True, '核对两者 report_type/status/日期过滤是否同口径'))
        if ws.get('total_score') is not None and rep.get('total_score') is not None \
                and abs(float(ws['total_score']) - float(rep['total_score'])) > 0.05:
            f.append(_finding(
                'F05', '看板评分×报告', ctx, 'P0',
                f'watchlist-scores total_score={ws.get("total_score")}',
                f'报告 total_score={rep.get("total_score")}',
                '看板评分卡与报告总分不一致', '同上', True, '同上'))
        if ws.get('report_date') and rep.get('report_date') and ws['report_date'] != rep['report_date']:
            f.append(_finding(
                'F05', '看板评分×报告', ctx, 'P1',
                f'看板 report_date={ws.get("report_date")}',
                f'报告 report_date={rep.get("report_date")}',
                '看板与报告标注的报告日期不同（读了不同期报告）',
                '同上', True, '同上'))
    return f


# ================================================================
# 021BU：回测证据一致性（R16-R18/F06——审计脚本命名空间，非红线编号）
# ================================================================

# 样本分级门槛：import 生产同源常量（单一真相，不另设第二套门槛）
from modules.backtest_engine import EVIDENCE_N_FULL, EVIDENCE_N_PARTIAL  # noqa: E402


def _audit_evidence_cell(correct, n, label='历史命中'):
    """审计侧独立复算的证据格（与 backtest_engine.format_evidence_cell 同语义，
    互为对照——共享门槛常量，展示装配独立实现，防共享代码自身 bug 静默通过）。"""
    n = int(n or 0)
    correct = int(correct or 0)
    if n < EVIDENCE_N_PARTIAL:
        return {'grade': 'C', 'n': correct, 'm': n, 'acc': None,
                'display': f'样本不足（n={n}）'}
    acc = round(correct / n, 4)
    return {'grade': 'A' if n >= EVIDENCE_N_FULL else 'B', 'n': correct, 'm': n,
            'acc': acc, 'display': f'{label} {acc * 100:.0f}%（{correct}/{n}）'}


def load_evidence_baseline(cur, market):
    """审计基准（ro 直读，生产同款 SQL 独立复算——021BU R16 对照面）。

    口径与 backtest_engine.rating_evidence_table 相同：排除模拟行与
    rating_id=-1、自然键 (stock_id, rating_date) 去重取 max(id)、
    主口径 is_correct 列 + 动态窗口次行。
    """
    rows = [dict(r) for r in cur.execute(
        'SELECT br.id, br.stock_id, br.rating_date, br.rating, br.is_correct, '
        'br.dynamic_is_correct, rh.engine_version '
        'FROM backtest_results br '
        'LEFT JOIN ratings_history rh '
        'ON rh.stock_id = br.stock_id AND rh.rating_date = br.rating_date '
        'WHERE br.market = ? '
        'AND (br.is_simulated IS NULL OR br.is_simulated = 0) '
        'AND br.rating_id IS NOT NULL AND br.rating_id != -1 '
        'ORDER BY br.id', (market,)).fetchall()]
    best = {}
    for r in rows:
        key = (r['stock_id'], r['rating_date'])
        if key not in best or r['id'] > best[key]['id']:
            best[key] = r
    agg = {}
    for r in best.values():
        cell = agg.setdefault(r['rating'], {'n': 0, 'c': 0, 'dn': 0, 'dc': 0})
        if r['is_correct'] is not None:
            cell['n'] += 1
            if r['is_correct'] == 1:
                cell['c'] += 1
        if r['dynamic_is_correct'] in (0, 1):
            cell['dn'] += 1
            if r['dynamic_is_correct'] == 1:
                cell['dc'] += 1
    return {
        rating: {
            'primary': _audit_evidence_cell(cell['c'], cell['n']),
            'dynamic': (_audit_evidence_cell(cell['dc'], cell['dn'], label='动态窗口命中')
                        if cell['dn'] else None),
        }
        for rating, cell in agg.items()
    }


def _price_evidence_baseline(cur, market):
    """审计基准（021BU R18 对照面）：price_backtest_results 真实锚点口径独立复算。"""
    rows = [dict(r) for r in cur.execute(
        'SELECT buy_range_low, t20_hit_buy_range, t20_hit_target, t20_hit_stop_loss '
        'FROM price_backtest_results '
        'WHERE market = ? AND anchor_rating_date IS NOT NULL', (market,)).fetchall()]
    np_rows = [r for r in rows if r['buy_range_low'] is not None]

    def _cnt(rs, fld):
        return (sum(1 for r in rs if r[fld] == 1), sum(1 for r in rs if r[fld] is not None))

    bc, bn = _cnt(np_rows, 't20_hit_buy_range')
    tc, tn = _cnt(np_rows, 't20_hit_target')
    sc, sn = _cnt(rows, 't20_hit_stop_loss')
    return {
        'n': len(rows),
        'buy_range_t20': _audit_evidence_cell(bc, bn, label='买入区间 20 日内触及'),
        'target_t20': _audit_evidence_cell(tc, tn, label='目标价 20 日内达成'),
        'stop_loss_t20': _audit_evidence_cell(sc, sn, label='止损 20 日内触发'),
    }


def _evidence_cell_issues(where, cell):
    """R17 共享检查：单证据格诚实门（返回 [(severity, msg)]；纯函数供 selftest 复用）。"""
    issues = []
    if not isinstance(cell, dict):
        return issues
    acc = cell.get('acc')
    n, m = cell.get('n'), cell.get('m')
    display = str(cell.get('display') or '')
    grade = cell.get('grade')
    if acc is not None and (m is None or m < EVIDENCE_N_PARTIAL):
        issues.append(('P1', f'{where}: grade={grade} 但 acc 非空（n={n}, m={m}）——'
                             f'小样本命中率泄漏（诚实原则违规）'))
    if '%' in display and acc is None:
        issues.append(('P1', f'{where}: display 含百分数但 acc=None（诚实门失真）'))
    if acc is not None and (n is None or m is None):
        issues.append(('P1', f'{where}: acc 非空但 n/m 缺失（命中率未带样本量）'))
    if '%' in display and (n is None or m is None):
        issues.append(('P1', f'{where}: display 含百分数但无 n/m（诚实原则：命中率必须带样本量）'))
    return issues


def _empty_evidence_baseline_cell():
    """零样本档位基准（与 backtest_engine.empty_rating_evidence 同构）。"""
    return {'primary': _audit_evidence_cell(0, 0), 'dynamic': None}


def _cells_mismatch(live_cell, base_cell):
    """同数据日数字一致性（n/m/display 三元组；acc 为展示派生值不单独比对）。"""
    if not isinstance(live_cell, dict):
        return True
    return (live_cell.get('n'), live_cell.get('m'), str(live_cell.get('display') or '')) != (
        base_cell.get('n'), base_cell.get('m'), str(base_cell.get('display') or ''))


def rule_r16_evidence_badge(ctx):
    """R16（021BU）评级徽章同源：ws 响应 rating_evidence × ro 现算基准。"""
    f = []
    rating = ctx.get('rating')
    if not rating:
        return f  # 无评级无徽章（无可核对面）
    base = (ctx.get('evidence_baseline') or {}).get(rating) or _empty_evidence_baseline_cell()
    live = ctx.get('rating_evidence_live')
    if live is None:
        f.append(_finding(
            'R16', '看板评分×回测证据', ctx, 'P2',
            'watchlist-scores 无 rating_evidence 字段',
            f'审计基准 primary={base["primary"]["display"]}',
            '评级徽章证据字段缺失（有评级有报告时应接未接，降级展示）',
            'blueprints/portfolio/watchlist_scores.py（021BU 接入面）', True,
            '核对看板面是否消费 rating_evidence_table 同源函数'))
        return f
    live_p = live.get('primary') or {}
    if _cells_mismatch(live_p, base['primary']):
        f.append(_finding(
            'R16', '看板评分×回测证据', ctx, 'P1',
            f'看板徽章 primary n/m/display={live_p.get("n")}/{live_p.get("m")}/{live_p.get("display")}',
            f'审计现算基准={base["primary"]["n"]}/{base["primary"]["m"]}/{base["primary"]["display"]}',
            '评级徽章与回测现算基准数字不一致（同数据日数字打架）',
            'watchlist_scores 证据链路 vs backtest_engine 聚合口径', True,
            '核对两面是否同市场、同去重口径、同门槛常量'))
    live_d, base_d = live.get('dynamic'), base.get('dynamic')
    if (live_d or base_d) and _cells_mismatch(live_d or {}, base_d or {}):
        f.append(_finding(
            'R16', '看板评分×回测证据', ctx, 'P2',
            f'看板徽章 dynamic={live_d or "缺失"}',
            f'审计现算基准 dynamic={base_d or "缺失"}',
            '动态窗口次行不一致（标注面缺失或口径漂移）',
            '同上', True, '同上'))
    if not f:
        f.append(_ok('R16', ctx, f'评级徽章与现算基准一致（{base["primary"]["display"]}）'))
    return f


def rule_r17_honesty_gate(ctx):
    """R17（021BU）诚实原则展示门：响应证据对象格式审计（纯函数面）。"""
    f = []
    live = ctx.get('rating_evidence_live')
    if not isinstance(live, dict):
        return f
    for sub in ('primary', 'dynamic'):
        cell = live.get(sub)
        for sev, msg in _evidence_cell_issues(f'评级徽章.{sub}', cell):
            f.append(_finding(
                'R17', '证据×诚实门', ctx, sev, msg,
                '诚实原则：命中率必须带样本量；C 级（n<20）不展示百分数',
                '响应证据对象绕过共享诚实门（数据层断流失效）',
                'modules/backtest_engine.py format_evidence_cell（数据层强制点）', True,
                '核对消费面是否手工拼装证据响应（绕过共享函数）'))
    if not f:
        f.append(_ok('R17', ctx, '评级徽章证据诚实门通过（acc/n/m/display 自洽）'))
    return f


def check_r18_price_evidence_baseline(ws_data, cur):
    """R18（021BU）价格基准注记同源：ws 顶层 evidence_price × ro 现算基准（全局一次）。"""
    f = []
    live_map = (ws_data or {}).get('evidence_price') or {}
    for market in ('a_stock', 'hk_stock'):
        base = _price_evidence_baseline(cur, market)
        if base['n'] == 0:
            continue  # 无真实锚点样本的市场无基准可核对
        live = live_map.get(market)
        if live is None:
            f.append(_finding(
                'R18', '看板×价格基准', _base_ctx(), 'P2',
                f'watchlist-scores evidence_price 无 {market} 键',
                f'审计基准 n={base["n"]}（真实锚点）',
                '价格基准注记字段缺失（有基准样本时应接未接）',
                'blueprints/portfolio/watchlist_scores.py（021BU 接入面）', True,
                '核对 evidence_price 是否按市场装填'))
            continue
        for key, name in (('buy_range_t20', '买入区间'), ('target_t20', '目标价'),
                          ('stop_loss_t20', '止损')):
            lc, bc = live.get(key) or {}, base[key]
            if _cells_mismatch(lc, bc):
                f.append(_finding(
                    'R18', '看板×价格基准', _base_ctx(), 'P1',
                    f'{market} {name} T+20 n/m/display={lc.get("n")}/{lc.get("m")}/{lc.get("display")}',
                    f'审计现算基准={bc["n"]}/{bc["m"]}/{bc["display"]}',
                    '价格基准注记与现算基准不一致（同数据日数字打架）',
                    'evidence_price 装配链路 vs price_advice_evidence_summary 口径', True,
                    '核对是否非真实锚点口径（重建点/全体）冒充主口径（P2 需口径标注）或聚合口径漂移'))
            for sev, msg in _evidence_cell_issues(f'{market} 价格基准.{key}', lc):
                f.append(_finding(
                    'R18', '看板×价格基准', _base_ctx(), sev, msg,
                    '诚实原则展示门（R17 同规则）',
                    '价格基准证据格绕过共享诚实门',
                    'modules/backtest_engine.py format_evidence_cell', True,
                    '核对消费面是否手工拼装'))
        if not any(x['rule'] == 'R18' and x['severity'] in ('P0', 'P1', 'P2') for x in f):
            f.append(_ok('R18', _base_ctx(),
                         f'{market} 价格基准与现算一致（真实锚点 n={base["n"]}）'))
    return f


def check_static_same_source():
    """静态同源断言（021BU R16/R18 配套）：报告页/看板必须消费同一证据函数。

    report-latest 可能触发实时重评写库（B11/021K），审计刻意不调（V8 只读原则）；
    报告页第三面改由源码静态断言：消费点必须 import 同一共享函数。
    """
    f = []
    checks = [
        ('blueprints/analysis.py', 'rating_evidence_for', '报告页评级徽章'),
        ('blueprints/analysis.py', 'price_advice_evidence_summary', '报告页价格基准'),
        ('blueprints/analysis.py', 'position_note_for', '报告页位置注记'),
        ('blueprints/portfolio/watchlist_scores.py', 'rating_evidence_table', '看板评级徽章'),
        ('blueprints/portfolio/watchlist_scores.py', 'price_advice_evidence_summary', '看板价格基准'),
        ('blueprints/portfolio/watchlist_scores.py', 'position_note_for', '看板位置注记'),
    ]
    missing = []
    for rel, sym, desc in checks:
        path = os.path.join(_PROJECT_ROOT, rel)
        try:
            with open(path, encoding='utf-8') as fh:
                src = fh.read()
        except OSError:
            src = ''
        if sym not in src:
            missing.append(f'{rel} 缺 {sym}（{desc}）')
    if missing:
        f.append(_finding(
            'R16', '报告页×看板×共享函数', _base_ctx(), 'P1',
            '存在未消费共享证据函数的消费面',
            '；'.join(missing),
            '同源同值断言破约（存在第二数据路径）',
            'blueprints 消费面（021BU）', True,
            '恢复消费共享函数（backtest_engine.rating_evidence_* / price_advice_evidence_summary / position_note_for）'))
    else:
        f.append(_ok('R16', _base_ctx(),
                     '静态同源断言通过（报告页/看板六处消费点均指向共享证据函数）'))
    return f


def flow_f06_evidence_flow(ctx_list, ws_by_id, client, action_list):
    """F06（021BU）看板评分卡证据流：position_note ws×端点同源 + 分域契约观察。

    注：rating_evidence 的流量核对在 R16（徽章规则）断言，此处不重复计数。
    """
    f = []
    for ctx in ctx_list:
        sid = ctx['report']['stock_id']
        ws_note = (ws_by_id.get(sid) or {}).get('position_note')
        ep = _get_json(client, f'/api/stocks/{sid}/position-note')
        if ep and ep.get('success'):
            if not ws_note or not ws_note.get('text'):
                f.append(_finding(
                    'F06', '看板评分×位置注记', ctx, 'P2',
                    'watchlist-scores 无 position_note',
                    'position-note 端点有显著分化',
                    '看板位置注记缺失（应接未接，降级展示）',
                    'blueprints/portfolio/watchlist_scores.py（021BU 接入面）', True,
                    '核对看板面 position_note 装配'))
            elif (ws_note.get('text') or '') != (ep.get('text') or ''):
                f.append(_finding(
                    'F06', '看板评分×位置注记', ctx, 'P1',
                    f'看板 text={str(ws_note.get("text"))[:48]}…',
                    f'端点 text={str(ep.get("text"))[:48]}…',
                    '看板位置注记与端点现算不一致（同一 position_note_for 函数不应异判）',
                    'watchlist_scores vs backtest_engine.position_note_for', True,
                    '核对看板装配是否绕过共享函数或时点差'))
            else:
                f.append(_ok('F06', ctx, '位置注记看板×端点同源一致'))
        elif ws_note and ws_note.get('text'):
            f.append(_finding(
                'F06', '看板评分×位置注记', ctx, 'P1',
                f'看板有 position_note（{str(ws_note.get("text"))[:32]}…）',
                'position-note 端点无显著分化（404 静默）',
                '看板有而端点无——同一函数两个消费面异判（同源断裂）',
                '同上', True, '同上'))
        # 双侧静默（无显著分化）→ 一致，不产 OK 噪音
    # 分域契约观察（021BU：统计面不进指令面；只观察不设门禁）
    items = (action_list or {}).get('items') or []
    leaked = [it for it in items if isinstance(it, dict) and 'rating_evidence' in it]
    if leaked:
        f.append(_finding(
            'F06', '行动清单×证据分域', _base_ctx(), 'INFO',
            f'行动清单 {len(leaked)} 项含 rating_evidence 键',
            '021BR 分域契约：行动清单=指令面，证据徽章=统计面',
            '统计面数字泄漏进指令面（认知成本观察，不设门禁）',
            'blueprints/dashboard action-list', True, '维持分域（或另立任务裁定）'))
    return f


# ================================================================
# 合成用例自检（--selftest）：验证审计规则本身可信，不触库
# ================================================================


def _base_ctx():
    """一致基线（对照中免 021BR 修复后形态）：

    成本 10.5 → 纪律线 9.66；pa 止损 8.8 → 矩阵双源取高者 9.66；
    现价 8.5 双破（< 9.66 且 < 8.8）→ pa 状态机必须 S4、矩阵 status=stop_triggered、
    无「持有」行；持仓观望 × 下跌期(强) → 裁决信号 stage_leads_rating 在场。
    所有数字两两自洽，期望 0 条 P0/P1。
    """
    return {
        'report': {'stock_id': 9999, 'stock_code': 'T00000', 'stock_name': '合成用例',
                   'report_date': '2026-09-22', 'generated_at': '2026-09-22 15:54:00'},
        'report_date': '2026-09-22',
        'rating': HOLD_RATING,
        'rating_label': '',
        'total_score': 58.0,
        'market': 'a_stock',
        'dims': {
            'kline': {'score': 50.0, 'weight': 0.2715, 'top_factors': {}},
            'fundamental': {'score': 55.0, 'weight': 0.2171, 'top_factors': {}},
            'capital_flow': {'score': 60.0, 'weight': 0.4314, 'top_factors': {}},
            'news': {'score': 55.0, 'weight': 0.08, 'top_factors': {}},
        },
        'pa': {'available': True, 'has_position': True, 'current_close': 8.5,
               'stop_loss': 8.8, 'take_profit': 11.2, 'cost_price': 10.5,
               'profit_pct': -19.0, 'state': 'S4', 'state_name': '已破止损',
               'action_suggestion': '已破止损，建议止损'},
        'pa_available': True,
        'md': {'score': 58.0, 'rating': HOLD_RATING, 'action_line': None,
               'dims': None, 'risks': [], 'hysteresis': False, 'raw': 'x'},
        'trader_stored': {'stage_name': '下跌期', 'has_disagreement': False,
                          'top_action': '止损·9.66（已触发）'},
        'live_trend': {'overall': {'trend': 'down', 'strength': '中'}},
        'live_trader': {'stage': {'code': 'decline', 'name': '下跌期', 'confidence': '强'},
                        'disagreement': {'type': 'stage_leads_rating', 'text': 'x'}},
        'stage': {'code': 'decline', 'name': '下跌期', 'confidence': '强'},
        'ops': {'holding': {'qty': 100, 'cost': 10.5},
                'status': {'kind': 'stop_triggered', 'close': 8.5, 'stop_line': 9.66}},
        'disagreement': {'type': 'stage_leads_rating'},
        'linkage': ['止损纪律已触发，共振仅作反抽减仓参考'],
        'top_action_live': '止损·9.66（已触发）',
        'top_action_stored': '止损·9.66（已触发）',
        'status_line': {'kind': 'stop_triggered', 'close': 8.5, 'stop_line': 9.66},
        'held_rows': [{'action': '止损', 'status': 'triggered'},
                      {'action': '减仓检查', 'status': 'triggered'}],
        'held': {'total_qty': 100, 'avg_cost': 10.5},
        'close': 8.5,
        'kline_date': '2026-09-22',
    }


def selftest():
    """合成用例验证审计规则可信：每条用例 = (名称, 变异, 期望触发, 规则函数)。"""
    cases = []
    base = _base_ctx()

    # 用例1：减仓档 × 买入区间（021BF 契约违反）→ R01 P0
    c = dict(base)
    c['rating'] = '建议减仓'
    c['pa'] = {'available': True, 'has_position': False, 'zone_label': '买入区间',
               'action_suggestion': '当前价在买入区间内，可按计划买入', 'stop_loss': 8.8}
    cases.append(('减仓档×买入区间话术', c, 'P0', rule_r01_zone_semantics))

    # 用例2：止损已触发 × 矩阵出现持有行（层级契约①违反）→ R08 P0
    c = dict(base)
    c['held_rows'] = base['held_rows'] + [{'action': '持有', 'status': None}]
    cases.append(('止损已触发×持有行同屏', c, 'P0', rule_r08_hierarchy))

    # 用例3：止损已触发 × 联动行「持仓者持有」→ R08 P0
    c = dict(base)
    c['linkage'] = ['买点侧共振成立（最高 5 星）→ 空仓者可按区间分批，持仓者持有']
    cases.append(('止损已触发×共振行持仓者持有', c, 'P0', rule_r08_hierarchy))

    # 用例4：价格建议状态机失真（close<stop 但 state=S2）→ R02 P0
    c = dict(base)
    c['pa'] = dict(base['pa'], state='S2', state_name='浮盈中', action_suggestion='持有，等待止盈')
    cases.append(('已破止损但状态标浮盈', c, 'P0', rule_r02_state_machine))

    # 用例5：总分 70 标「建议减仓」且三层标注全缺 → R04 P1
    c = dict(base)
    c['total_score'] = 70.0
    c['rating'] = '建议减仓'
    c['md'] = dict(base['md'], hysteresis=False)
    c['score_tier_note_stored'] = None
    c['score_tier_note_live'] = None
    cases.append(('分数档位不符且无任何标注', c, 'P1', rule_r04_score_tier))

    # 用例6：同上但 markdown 带迟滞 → R04 降级 P2
    c = dict(base)
    c['total_score'] = 70.0
    c['rating'] = '建议减仓'
    c['md'] = dict(base['md'], hysteresis=True)
    cases.append(('分数档位不符但带迟滞标注', c, 'P2', rule_r04_score_tier))

    # 用例11（021BS）：失配 + key_factors 落库注记 → P2（标注面②）
    c = dict(base)
    c['total_score'] = 70.0
    c['rating'] = '建议减仓'
    c['md'] = dict(base['md'], hysteresis=False)
    c['score_tier_note_stored'] = '评级口径说明：总分 70.0 位于「推荐买入」档分数区间'
    c['score_tier_note_live'] = None
    cases.append(('失配+落库注记', c, 'P2', rule_r04_score_tier))

    # 用例12（021BS）：失配 + 读取面注记（存量报告由读取路径现算）→ P2（标注面③）
    c = dict(base)
    c['total_score'] = 70.0
    c['rating'] = '建议减仓'
    c['md'] = dict(base['md'], hysteresis=False)
    c['score_tier_note_stored'] = None
    c['score_tier_note_live'] = '评级口径说明：总分 70.0 位于「推荐买入」档分数区间'
    cases.append(('失配+读取面注记', c, 'P2', rule_r04_score_tier))

    # 用例13（021BS t4）：R03 同数据日现价不一致 + 双存储见证一致 → K线事后修订 P2
    c = dict(base)
    c['pa'] = dict(base['pa'], current_close=8.5)
    c['close'] = 8.3
    c['kline_date'] = '2026-09-22'
    c['price_at_rating_witness'] = {'rating_date': '2026-09-22', 'price_at_rating': 8.5}
    cases.append(('R03 K线事后修订（见证一致）', c, 'P2', rule_r03_pa_numbers))

    # 用例14（021BS t4）：R03 同数据日不一致且见证缺失 → 采集竞态 P1
    c = dict(base)
    c['pa'] = dict(base['pa'], current_close=8.5)
    c['close'] = 8.3
    c['kline_date'] = '2026-09-22'
    c['price_at_rating_witness'] = None
    cases.append(('R03 同数据日无见证', c, 'P1', rule_r03_pa_numbers))

    # 用例15（021BU）：R16 徽章×基准同数据日数字不一致 → P1
    c = dict(base)
    c['rating'] = '持有观望'
    c['evidence_baseline'] = {'持有观望': {
        'primary': {'grade': 'A', 'n': 159, 'm': 230, 'acc': 0.6913,
                    'display': '历史命中 69%（159/230）'},
        'dynamic': None}}
    c['rating_evidence_live'] = {'primary': {'grade': 'A', 'n': 100, 'm': 200, 'acc': 0.5,
                                             'display': '历史命中 50%（100/200）'},
                                 'dynamic': None}
    cases.append(('R16 徽章×基准不一致', c, 'P1', rule_r16_evidence_badge))

    # 用例16（021BU）：R16 有评级有报告而徽章字段缺失 → P2（应接未接）
    c = dict(base)
    c['rating'] = '持有观望'
    c['evidence_baseline'] = {'持有观望': {
        'primary': {'grade': 'A', 'n': 159, 'm': 230, 'acc': 0.6913,
                    'display': '历史命中 69%（159/230）'},
        'dynamic': None}}
    c['rating_evidence_live'] = None
    cases.append(('R16 徽章字段缺失', c, 'P2', rule_r16_evidence_badge))

    # 用例17（021BU）：R17 C 级证据带百分数 → P1（小样本误导，诚实原则违规）
    c = dict(base)
    c['rating_evidence_live'] = {'primary': {'grade': 'C', 'n': 1, 'm': 2, 'acc': 0.5,
                                             'display': '历史命中 50%（1/2）'},
                                 'dynamic': None}
    cases.append(('R17 C级泄漏百分数', c, 'P1', rule_r17_honesty_gate))

    # 用例18（021BU）：R16 徽章与基准一致 → OK
    c = dict(base)
    c['rating'] = '持有观望'
    c['evidence_baseline'] = {'持有观望': {
        'primary': {'grade': 'A', 'n': 159, 'm': 230, 'acc': 0.6913,
                    'display': '历史命中 69%（159/230）'},
        'dynamic': None}}
    c['rating_evidence_live'] = {'primary': {'grade': 'A', 'n': 159, 'm': 230, 'acc': 0.6913,
                                             'display': '历史命中 69%（159/230）'},
                                 'dynamic': None}
    cases.append(('R16 徽章与基准一致', c, 'OK', rule_r16_evidence_badge))

    # 用例7：一致基线（对照中免 021BR 修复后形态）→ 不应触发任何 P0/P1
    fired = []
    for rule in ALL_REPORT_RULES:
        fired.extend(rule(base))
    hard = [x for x in fired if x['severity'] in ('P0', 'P1')]
    print(f'[自检] 一致基线：规则触发 {len(fired)} 条，其中 P0/P1 {len(hard)} 条（期望 0）')
    for x in hard:
        print(f'  ✗ 意外 {x["severity"]} {x["rule"]}: {x["phenomenon"]}')
    ok_hard = len(hard) == 0

    ok_cases = True
    for name, ctx, expect_sev, fn in cases:
        fired = fn(ctx)
        sev = {x['severity'] for x in fired}
        hit = expect_sev in sev
        print(f'[自检] {name}: 期望 {expect_sev}，实际 {sorted(sev) or "无"} → {"✓" if hit else "✗"}')
        ok_cases = ok_cases and hit

    # 用例8：三止损数字并存（矩阵 56.16 / pa 52.93 / 现成本纪律线 48.00，
    # 持仓在报告后变更 57.53→52.17）→ R09 数据变更型 P2
    c = dict(base)
    c['pa'] = dict(base['pa'], stop_loss=52.93, cost_price=57.53)
    c['held'] = {'total_qty': 2100, 'avg_cost': 52.17}
    c['top_action_live'] = '止损·56.16（已触发）'
    c['status_line'] = {'kind': 'stop_triggered', 'close': 52.27, 'stop_line': 56.16}
    c['close'] = 52.27
    fired = rule_r09_three_stop_numbers(c)
    sev = {x['severity'] for x in fired}
    print(f'[自检] 三止损数字并存: 期望 P2（持仓变更型）或 P1，实际 {sorted(sev) or "无"} → '
          f'{"✓" if ({"P1", "P2"} & sev) else "✗"}')
    ok_cases = ok_cases and bool({'P1', 'P2'} & sev)

    # 用例9：减仓档 × 震荡无趋势（低置信）→ 设计内不硬造分歧（不得误报 P1）
    c = dict(base)
    c['rating'] = '建议减仓'
    c['stage'] = {'code': 'range', 'name': '震荡无趋势', 'confidence': '低'}
    c['disagreement'] = None
    fired = rule_r07_disagreement_channel(c)
    hard = [x for x in fired if x['severity'] in ('P0', 'P1')]
    oks = [x for x in fired if x['severity'] == 'OK']
    print(f'[自检] 低置信阶段不硬造分歧: 期望无 P0/P1 且有 OK，实际 '
          f'{[x["severity"] for x in fired] or "无"} → {"✓" if (not hard and oks) else "✗"}')
    ok_cases = ok_cases and (not hard and bool(oks))

    # 用例10：止损数字 = 报告期纪律线 且 pa 止损更高（021BR t3 前存量特征）→ F02 应 P2 自愈型
    c = dict(base)
    c['pa'] = dict(base['pa'], cost_price=57.2, stop_loss=61.49)
    c['top_action_stored'] = '止损·52.62'
    disc_report = discipline_stop(57.2)  # 52.62
    fired = _classify_stop_number_mismatch(52.62, 61.49, disc_report)
    print(f'[自检] t3 前时序残留特征: 期望 P2，实际 {fired[0]} → {"✓" if fired[0] == "P2" else "✗"}')
    ok_cases = ok_cases and fired[0] == 'P2'

    # 用例19（021BU）：诚实门纯函数边界（n=19/20 与 n=29/30；数据层强制——
    # C 级 acc=None 且 display 无百分数，任何消费面都泄漏不了小样本命中率）
    from modules.backtest_engine import format_evidence_cell

    c19 = format_evidence_cell(13, 19)
    c20 = format_evidence_cell(14, 20)
    c29 = format_evidence_cell(20, 29)
    c30 = format_evidence_cell(21, 30)
    _case19_ok = (
        c19['grade'] == 'C' and c19['acc'] is None and '%' not in c19['display']
        and '样本不足' in c19['display'] and c19['m'] == 19
        and c20['grade'] == 'B' and c20['acc'] is not None and '/' in c20['display']
        and c29['grade'] == 'B'
        and c30['grade'] == 'A'
    )
    print(f'[自检] 诚实门边界 n=19/20/29/30: 期望 C(无%)/B/B/A，实际 '
          f'{c19["grade"]}/ {c20["grade"]}/ {c29["grade"]}/ {c30["grade"]} → '
          f'{"✓" if _case19_ok else "✗"}')
    ok_cases = ok_cases and _case19_ok

    passed = ok_hard and ok_cases
    print(f'[自检] 结果：{"全部通过（审计规则可信）" if passed else "存在失败用例（规则需修订）"}')
    return passed


ALL_REPORT_RULES = [
    rule_r01_zone_semantics, rule_r02_state_machine, rule_r03_pa_numbers,
    rule_r04_score_tier, rule_r05_kline_vs_compass, rule_r06_rating_vs_compass,
    rule_r07_disagreement_channel, rule_r08_hierarchy, rule_r09_three_stop_numbers,
    rule_r10_trigger_crosscheck, rule_r11_compass_vs_stage, rule_r12_markdown,
    rule_r13_trader_stored_vs_live, rule_r14_pa_stored_vs_recomputed,
    rule_r15_weighted_sum,
    # 021BU：回测证据一致性（徽章同源 + 诚实门；R18/F06 为全局核对见 run_audit）
    rule_r16_evidence_badge, rule_r17_honesty_gate,
]


# ================================================================
# 主流程
# ================================================================


def run_audit(only_ids=None):
    today = datetime.now(CN_TZ).strftime('%Y-%m-%d')
    conn = open_ro_db()
    cur = conn.cursor()
    client = make_client()

    stocks = load_watchlist(cur)
    if only_ids:
        stocks = [s for s in stocks if s['id'] in set(only_ids)]
    latest_reports, prev_reports = load_reports(cur)
    held_map = load_holdings_agg(cur)
    rules = load_alert_rules(cur)

    print(f'[审计] 自选股 {len(stocks)} 只；最新 ok 日报 {len(latest_reports)} 只；持仓 {len(held_map)} 只')
    print('[审计] 拉取看板面（只读 GET）：action-list / watchlist-scores / holdings ...')
    action_list = _get_json(client, '/api/dashboard/action-list') or {}
    ws_data = _get_json(client, '/api/portfolio/watchlist-scores') or {}
    holdings_rows = (_get_json(client, '/api/portfolio/holdings') or {}).get('holdings') or []
    ws_by_id = {s['id']: s for s in (ws_data.get('stocks') or [])}

    # 021BU：回测证据审计基准（ro 直读独立复算，按市场一次）
    evidence_baseline = {}
    for _m in sorted({s.get('market') or 'a_stock' for s in stocks} | {'a_stock'}):
        evidence_baseline[_m] = load_evidence_baseline(cur, _m)

    findings = []
    oks = []
    ctx_list = []
    errors = []

    for i, s in enumerate(stocks, 1):
        sid = s['id']
        rep = latest_reports.get(sid)
        if not rep:
            errors.append(f"{s['symbol']} {s['name']}: 无最新 ok 日报（缺报股，报告面要素缺失）")
            continue
        rep['stock_id'] = sid
        live_trend = _get_json(client, f'/api/stocks/{sid}/trend')
        live_trader = _get_json(client, f'/api/stocks/{sid}/trader-advice')
        kline_head = load_kline_head(cur, sid)
        ctx = extract_elements(rep, live_trend, live_trader, held_map.get(sid), kline_head)
        ctx['market'] = s.get('market')
        ctx['symbol'] = s['symbol']
        ctx['name'] = s['name']
        # 021BS：读取面失配注记（看板 watchlist-scores，纯 GET 可观测标注面③）
        ctx['score_tier_note_live'] = (ws_by_id.get(sid) or {}).get('score_tier_note')
        # 021BU：回测证据（看板面响应字段 + ro 现算基准，供 R16/R17 断言）
        ctx['rating_evidence_live'] = (ws_by_id.get(sid) or {}).get('rating_evidence')
        ctx['evidence_baseline'] = evidence_baseline.get(s.get('market') or 'a_stock') or {}
        # 021BS t4：R03 生成时点双存储见证（price_at_rating）
        ctx['price_at_rating_witness'] = load_rating_witness(cur, sid)
        ctx_list.append(ctx)

        for fn in ALL_REPORT_RULES:
            try:
                for x in fn(ctx):
                    (oks if x['severity'] == 'OK' else findings).append(x)
            except Exception as e:  # noqa: BLE001
                errors.append(f'{s["symbol"]} {fn.__name__}: 规则执行异常 {e}')
        if (i % 10) == 0 or i == len(stocks):
            print(f'[审计] 进度 {i}/{len(stocks)}')

    # 数据流核对（F01-F05）
    try:
        findings += flow_f01_action_list(ctx_list, action_list, stocks, prev_reports, cur)
    except Exception as e:  # noqa: BLE001
        errors.append(f'F01 异常: {e}')
    try:
        findings += flow_f02_chip_vs_pa(ctx_list, ws_by_id)
    except Exception as e:  # noqa: BLE001
        errors.append(f'F02 异常: {e}')
    try:
        findings += flow_f03_holdings_vs_matrix(ctx_list, holdings_rows, ws_by_id)
    except Exception as e:  # noqa: BLE001
        errors.append(f'F03 异常: {e}')
    alerts_today = load_alerts_today(cur, today)
    try:
        findings += flow_f04_alerts_vs_report(ctx_list, alerts_today, rules, action_list, today)
    except Exception as e:  # noqa: BLE001
        errors.append(f'F04 异常: {e}')
    try:
        findings += flow_f05_watchlist_scores_vs_report(ctx_list, ws_by_id)
    except Exception as e:  # noqa: BLE001
        errors.append(f'F05 异常: {e}')
    try:
        findings += flow_f06_evidence_flow(ctx_list, ws_by_id, client, action_list)
    except Exception as e:  # noqa: BLE001
        errors.append(f'F06 异常: {e}')
    try:
        findings += check_r18_price_evidence_baseline(ws_data, cur)
    except Exception as e:  # noqa: BLE001
        errors.append(f'R18 异常: {e}')
    try:
        findings += check_static_same_source()
    except Exception as e:  # noqa: BLE001
        errors.append(f'静态同源检查异常: {e}')

    meta = {
        'today': today,
        'stocks': stocks,
        'n_latest': len(latest_reports),
        'held_map': held_map,
        'alerts_today': alerts_today,
        'rules': rules,
        'action_list': action_list,
        'ws_data': ws_data,
        'errors': errors,
        'oks': oks,
        'missing_reports': [f"{s['symbol']} {s['name']}" for s in stocks if s['id'] not in latest_reports],
    }
    conn.close()
    return findings, meta


def write_report(findings, meta, out_path):
    now = datetime.now(CN_TZ)
    sev_count = {k: 0 for k in ('P0', 'P1', 'P2', 'INFO', 'OK')}
    for x in findings:
        sev_count[x['severity']] = sev_count.get(x['severity'], 0) + 1
    ok_count = len(meta.get('oks') or [])
    findings_sorted = sorted(
        findings, key=lambda x: (SEV_ORDER.get(x['severity'], 9), x.get('stock_id') or 0, x['rule']))
    rule_stat = {}
    for x in findings:
        st = rule_stat.setdefault(x['rule'], {'fires': 0, 'sev': {}})
        st['fires'] += 1
        st['sev'][x['severity']] = st['sev'].get(x['severity'], 0) + 1
    for x in meta.get('oks') or []:
        st = rule_stat.setdefault(x['rule'], {'fires': 0, 'sev': {}})
        st.setdefault('ok', 0)
        st['ok'] += 1

    lines = []
    w = lines.append
    w('# 021BS 第一轮一致性审计报告（R1）— 报告内七要素两两核对 + 报告↔看板五数据流')
    w('')
    w(f'> 日期：{meta["today"]} ｜ 执行：scout（任务 t1，审计脚本 `scripts/audit_consistency_021bs.py`）｜ 批次：021BS')
    w('> 方法：**全程只读**——SQLite `mode=ro` + Flask test_client 仅调只读 GET 端点（trend / trader-advice /')
    w('> dashboard/action-list / portfolio/watchlist-scores / portfolio/holdings），零写库、零网络采集、')
    w('> 零触碰 advisor.generate_advice（B24）。审计基准面 = daily_reports 存量报告（刻意绕开会触发')
    w('> 实时重评写库的 report-latest 端点）。')
    w(f'> 运行时刻：{now.strftime("%Y-%m-%d %H:%M:%S")}；自选股 {len(meta["stocks"])} 只；有最新 ok 日报 {meta["n_latest"]} 只；持仓 {len(meta["held_map"])} 只；当日预警 {len(meta["alerts_today"])} 条。')
    w('> 规则集与分级定义见脚本文件头 docstring（规则即注释、注释即规则，脚本可反复重跑供后续轮次复用）。')
    w('')
    w('---')
    w('')
    w('## 0. 结论速览')
    w('')
    w('| 分级 | 数量 | 含义 |')
    w('|---|---|---|')
    for k in ('P0', 'P1', 'P2', 'INFO'):
        w(f'| {k} | {sev_count[k]} | {SEV_DESC[k]} |')
    w(f'| OK | {ok_count} | 规则核对一致（正常分层基线，含各规则 OK 计数） |')
    w('')
    hard = [x for x in findings_sorted if x['severity'] in ('P0', 'P1')]
    if hard:
        w(f'**需处置发现 {len(hard)} 条**（P0 {sev_count["P0"]} / P1 {sev_count["P1"]}）。逐条矩阵见 §1。')
    else:
        w('**无 P0/P1 发现。** P2 及以下见 §1。')
    if meta.get('missing_reports'):
        w('')
        w(f'缺报股（最新 ok 日报缺失，报告面无法核对，{len(meta["missing_reports"])} 只）：'
          + '、'.join(meta['missing_reports']))
    if meta.get('errors'):
        w('')
        w(f'审计过程异常 {len(meta["errors"])} 条（不阻塞结论，逐条见附录）：规则函数对个别股票的数据形态未覆盖。')
    w('')
    w('---')
    w('')
    w('## 1. 矛盾矩阵（逐条）')
    w('')
    for sev in ('P0', 'P1', 'P2', 'INFO'):
        rows = [x for x in findings_sorted if x['severity'] == sev]
        if not rows:
            continue
        w(f'### {SEV_DESC[sev]}（{len(rows)} 条）')
        w('')
        for i, x in enumerate(rows, 1):
            w(f'**{sev}-{i}｜{x["rule"]}（{x["pair"]}）｜{x["stock"]}**')
            w('')
            w(f'- 表面A：{x["surface_a"]}')
            w(f'- 表面B：{x["surface_b"]}')
            w(f'- 现象：{x["phenomenon"]}')
            w(f'- 根因模块：{x["root_cause"]}')
            fix = '做' if x.get('fix_do') else ('暂不做（核对/标注即可）' if x.get('fix_do') is False else '视复现而定')
            b24 = '；⚠️ 根因涉及 B24 冻结面（advisor.generate_advice），只允许外层调和' if x.get('b24') else ''
            w(f'- 修复建议：**{fix}**——{x["fix_how"]}{b24}')
            w('')
    if not any(x['severity'] in ('P0', 'P1', 'P2', 'INFO') for x in findings_sorted):
        w('（本轮无发现）')
        w('')
    w('---')
    w('')
    w('## 2. 报告↔看板五数据流核对结果')
    w('')
    flow_names = {
        'F01': '行动清单 vs 报告评级/操盘手',
        'F02': '看板 top_action chip vs 报告 price_advice',
        'F03': '持仓页价格 vs 矩阵现价（盘中 vs 收盘双口径）',
        'F04': '预警铃铛 vs 报告',
        'F05': 'watchlist-scores 评级 vs 报告评级',
        'F06': '看板证据流（位置注记同源/分域观察，021BU）',
    }
    w('| 数据流 | 发现数（P0/P1/P2/INFO） | 结论 |')
    w('|---|---|---|')
    for fid, fname in flow_names.items():
        rows = [x for x in findings if x['rule'] == fid]
        c = {k: sum(1 for x in rows if x['severity'] == k) for k in ('P0', 'P1', 'P2', 'INFO')}
        concl = '数据流一致' if not any(c.values()) else '存在不一致，见 §1 对应条目'
        w(f'| {fid} {fname} | {c["P0"]}/{c["P1"]}/{c["P2"]}/{c["INFO"]} | {concl} |')
    al = meta.get('action_list') or {}
    st = al.get('stats') or {}
    w('')
    w(f'行动清单快照：items={len(al.get("items") or [])}，stats={json.dumps(st, ensure_ascii=False)}。')
    w('')
    w('---')
    w('')
    w('## 3. 七要素两两核对统计（规则×触发）')
    w('')
    w('| 规则 | 核对面 | 触发 | P0 | P1 | P2 | INFO | OK（一致） |')
    w('|---|---|---|---|---|---|---|---|')
    rule_pairs = {
        'R01': '总评级×价格建议', 'R02': '价格建议×状态机', 'R03': '价格建议×K线',
        'R04': '总评级×评分', 'R05': '评分明细×罗盘', 'R06': '总评级×罗盘',
        'R07': '总评级×操盘手(裁决)', 'R08': '操盘手矩阵内部', 'R09': '操盘手×价格建议(止损数字)',
        'R10': '操盘手×价格建议(触发状态)', 'R11': '罗盘×阶段', 'R12': '总评级×建议文字',
        'R13': '操盘手存量×现算', 'R14': '价格建议×持仓口径', 'R15': '评分明细×总分',
        'R16': '评级徽章×回测基准(021BU)', 'R17': '证据×诚实门(021BU)', 'R18': '价格基准×现算(021BU)',
    }
    for rid in [f'R{n:02d}' for n in range(1, 19)]:
        st2 = rule_stat.get(rid, {})
        s = st2.get('sev', {})
        w(f'| {rid} | {rule_pairs.get(rid, "")} | {st2.get("fires", 0)} | {s.get("P0", 0)} | '
          f'{s.get("P1", 0)} | {s.get("P2", 0)} | {s.get("INFO", 0)} | {st2.get("ok", 0)} |')
    w('')
    w('---')
    w('')
    w('## 4. 修复建议汇总（做 / 不做 / 怎么做；B24 冻结面显式标注）')
    w('')
    rows = [x for x in findings_sorted if x['severity'] in ('P0', 'P1')]
    if rows:
        w('| 条目 | 分级 | 做/不做 | 建议 | B24 |')
        w('|---|---|---|---|---|')
        for x in rows:
            fix = '做' if x.get('fix_do') else '暂不做'
            b24 = '⚠️ 只能外层调和' if x.get('b24') else '—'
            w(f'| {x["rule"]} {x["stock"]} | {x["severity"]} | {fix} | {x["fix_how"]} | {b24} |')
    else:
        w('本轮无 P0/P1，无需修复动作。')
    w('')
    w('---')
    w('')
    w('## 5. 复现与口径说明')
    w('')
    w('```bash')
    w('python scripts/audit_consistency_021bs.py             # 全量重跑（只读）')
    w('python scripts/audit_consistency_021bs.py --selftest  # 合成用例自检（规则可信性）')
    w('python scripts/audit_consistency_021bs.py --stock 21  # 单股复跑')
    w('```')
    w('')
    w('- 报告面 = daily_reports 存量行（report_type=daily, status=ok, 每股最新一期）；')
    w('- 现算面 = trend_analyzer / trader_advisor 纯函数经 test_client 只读端点现算；')
    w('- 时点差分级依据：kline_upto（raw_kline 最新交易日）== report_date 视为同数据日')
    w('  （不一致升级 P1），否则视为时点差（P2，重生成报告自愈）；')
    w('- 持仓数据变更（如用户当日增删持仓）会造成存量报告与现算的成本基数差异，')
    w('  审计按「数据变更型时点差」分级为 P2 并在现象中注明，避免误报为代码矛盾；')
    w('- 本脚本零写库（sqlite mode=ro 硬保证）、零网络（只调本地 test_client 只读端点）、')
    w('  不触碰 B24（advisor.generate_advice）与评分引擎 R7/classify_stage（021BQ 锁）。')
    if meta.get('errors'):
        w('')
        w('### 附：审计过程异常清单')
        w('')
        for e in meta['errors']:
            w(f'- {e}')
    content = '\n'.join(lines) + '\n'
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(content)
    return out_path


def main():
    # Windows 控制台 GBK 兜底：统一 UTF-8 输出（R21 精神——脚本输出不依赖控制台代码页）
    for _stream in (sys.stdout, sys.stderr):
        if _stream is not None and hasattr(_stream, 'reconfigure'):
            try:
                _stream.reconfigure(encoding='utf-8', errors='replace')
            except (ValueError, OSError):
                pass
    parser = argparse.ArgumentParser(description='021BS 只读一致性审计')
    parser.add_argument('--selftest', action='store_true', help='只跑合成用例自检（不触库）')
    parser.add_argument('--stock', type=int, nargs='*', help='只审计指定 stock_id')
    parser.add_argument('--out', default=None, help='报告输出路径（默认 docs/reports/021bs_audit_r1_20260922.md）')
    parser.add_argument('--no-report', action='store_true', help='只跑核对，不写 md 文件')
    args = parser.parse_args()

    if args.selftest:
        ok = selftest()
        sys.exit(0 if ok else 1)

    findings, meta = run_audit(args.stock)
    sev = {}
    for x in findings:
        sev[x['severity']] = sev.get(x['severity'], 0) + 1
    print(f'[审计] 完成：发现 {len(findings)} 条 ' +
          ' '.join(f'{k}={v}' for k, v in sorted(sev.items())) +
          f'，OK {len(meta["oks"])} 条，异常 {len(meta["errors"])} 条')
    if not args.no_report:
        out = args.out or os.path.join(
            _PROJECT_ROOT, 'docs', 'reports', '021bs_audit_r1_20260922.md')
        path = write_report(findings, meta, out)
        print(f'[审计] 报告已写入：{path}')
    sys.exit(0)


if __name__ == '__main__':
    main()
