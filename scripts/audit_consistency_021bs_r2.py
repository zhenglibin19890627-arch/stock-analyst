"""021BS 第二轮复审审计脚本（只读，可重复执行）——R1 清零确认后的新维度深挖。

=====================================================================
定位（021BS t3 / R2，2026-09-22）
=====================================================================
R1（scripts/audit_consistency_021bs.py，经 t2 断言化）重跑已确认 P0/P1 清零。
本脚本从 **修复引入面 / 跨日时效 / 边界面** 三个新角度补盲，防止修复本身
引入新矛盾或存在漏面。规则集（N 系列）：

  N01 存量报告 trader 摘要完整性（新发现面；021BS t4 断言化）：
      key_factors.trader（含 top_action）是看板 chip/⚡分歧/操盘手摘要的
      落库面。019A 回写路径（generate_advice 内 _save_daily_report_for_advice）
      在批次后刷新报告时会用 _build_key_factors（不含 trader）整体覆盖
      key_factors → 落库摘要消失。t4 起看板读取面 live 兜底
      （trader_advisor.derive_trader_signal_summary，零写库消费方门控），
      断言契约：落库缺失 + 读取面兜底在场（watchlist-scores trader_signal
      有 stage_name/top_action）= P2（已调和，落库面次日批次合流）；
      落库缺失且兜底亦缺 = P1（一面沉默一面报警，中免类高危场景）。
  N02 分歧标注 stored×live 漂移：
      trader 键在但 has_disagreement 与 live detect_disagreement 不一致：
      报告 generated_at 早于 021BR t2 部署（stage_leads_rating 通道上线前）
      → P2（批次时点旧数据，次日自愈）；同代报告仍漂移 → P1。
  N03 新旧措辞互斥（静态代码面）：analysis.js 操盘手卡头副词
      「仓位动作以评级为准」（021BQ 时代）vs 同卡 ④ 操作矩阵副词
      「纪律无条件执行 · 减仓听操盘手 · 加仓看评级」（021BR 分域契约）
      同屏互斥 → P1（文案未随契约更新）。
  N04 刷新路径 markdown 缺「数据完整度」小节（静态代码面）：批次路径拼小节、
      刷新路径只补 data_warnings JSON → INFO（报告页数据完整度卡读 JSON，
      用户可见，纯 markdown 展示差异）。
  N05 预警消息裸 '<'（静态代码面）：score_below 消息含「分 < 阈值」裸 '<'；
      alerts.js 渲染前 escapeHtml → INFO（021BN 风险已由转义兜住，建议随
      下次文案批次文字化）。
  N06 跨日时效面：price_cache.updated_at vs raw_kline 最新交易日（盘中快照
      vs 收盘双口径，R1 已标注，此处计数观察）；watchlist-scores 报告日期
      落后今日的股数（021K 过期重评兜底）；021BR t3 部署前生成的持仓股报告
      （chip 止损双数字自愈窗口计数）。
  N07 边界面：K线不足 35 根的股（信号静默区，应无）；当日缺报/失败股（应无）；
      无持仓股 price_advice zone_label × rating 全映射契约（021BF：买入档=
      买入区间 / 观望档=参考区间 / 减仓档=支撑参考区间）→ 违反 = P1。
  N08 score_tier_note 三面一致性：落库 key_factors.score_tier_note × 读取面
      （watchlist-scores）同源断言（同日同报告二者必须一致 → 不一致 = P1）；
      markdown 迟滞行与「评级口径说明」行互斥（并存 = P1 重复标注）。
  N09 反落库守卫（021BU，2026-09-23；t1 方案 §4 原「N04」编号——r2 的 N04
      已被「刷新路径 markdown 数据完整度」占用，故顺延为 N09）：
      daily_reports.key_factors / markdown_content 中不应存在落库的历史命中率
      数字副本（021BU O6 设计：证据元素一律读取面现算、零落库，防 stored-vs-live
      漂移——021BS N01 实锤教训）。发现形如「历史命中 xx%（n/m）」的存储副本
      = P2（反漂移观察）；其数字与现算基准不符 = P1（冻结快照已打架）。

分级口径与 R1 相同：P0 指令矛盾 / P1 表述误导 / P2 口径差异需标注 /
INFO 信息性 / OK 核对一致。

用法：
  python scripts/audit_consistency_021bs_r2.py            # 全量新维度审计并写报告
  python scripts/audit_consistency_021bs_r2.py --no-report
  python scripts/audit_consistency_021bs_r2.py --selftest # 合成用例自检（不触库）

红线合规：全程只读（mode=ro + 只读 GET 端点）；复用 R1 脚本的连接与取数面；
不触碰 advisor.generate_advice（B24）/评分引擎 R7/classify_stage（021BQ 锁）。
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import re
import sys

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PROJECT_ROOT not in sys.path:
    sys.path.insert(0, _PROJECT_ROOT)

# 复用 R1 审计脚本的只读取数面（连接/test_client/报告加载/要素提取）
_spec = importlib.util.spec_from_file_location(
    'audit_r1', os.path.join(_PROJECT_ROOT, 'scripts', 'audit_consistency_021bs.py'))
r1 = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(r1)

# 021BR t2 部署时刻（stage_leads_rating 通道上线；docs/reports/021br_impl_matrix_hierarchy_20260922.md §1）
_021BR_T2_DEPLOYED_AT = '2026-09-22 20:07:00'
# 021BR t3 部署时刻（price_advice_override 同源链；同报告 §1）
_021BR_T3_DEPLOYED_AT = '2026-09-22 20:07:00'

ZONE_CONTRACT = {  # 021BF 区间语义契约
    '强烈推荐买入': '买入区间',
    '推荐买入': '买入区间',
    '持有观望': '参考区间',
    '建议减仓': '支撑参考区间',
    '强烈建议卖出': '支撑参考区间',
}


# ================================================================
# 021BU N09：反落库守卫（O6：证据元素读取面现算、零落库）
# 纯函数供 --selftest 使用（不触库）
# ================================================================

# 命中率类文案 + 百分数（或 C 级「样本不足」形态）——021BU 证据展示的落库指纹
_N09_PATTERN = re.compile(
    r'(?:历史命中|动态窗口命中|命中率|胜率|触及率|达成率|触发率)[^。\n]{0,24}?\d+(?:\.\d+)?%'
    r'(?:\s*（\d+/\d+）)?'
    r'|样本不足（n=\d+）')
_N09_PAIR = re.compile(r'（(\d+)/(\d+)）')
_N09_NSINGLE = re.compile(r'n=(\d+)')
_N09_PCT = re.compile(r'(\d+(?:\.\d+)?)%')


def n09_find_copies(text):
    """检测落库文本中的历史命中率副本（返回 [dict(text, pct, c, n)]；纯函数）。"""
    out = []
    if not text:
        return out
    for m in _N09_PATTERN.finditer(text):
        seg = m.group(0)
        pct = None
        c = n = None
        pm = _N09_PAIR.search(seg)
        if pm:
            c, n = int(pm.group(1)), int(pm.group(2))
        else:
            pn = _N09_NSINGLE.search(seg)  # C 级「样本不足（n=4）」形态
            if pn:
                n = int(pn.group(1))
        pp = _N09_PCT.search(seg)
        if pp:
            pct = float(pp.group(1))
        out.append({'text': seg, 'pct': pct, 'c': c, 'n': n})
    return out


def n09_severity(copies, baseline_cell):
    """N09 分级（纯函数）：副本在场 = P2（反漂移观察）；数字与现算失真 = P1。

    baseline_cell 为审计现算基准（r1.load_evidence_baseline 的 primary 格）；
    None/C 级（acc 缺失）时不可比 → 维持 P2。
    """
    if not copies:
        return None
    if isinstance(baseline_cell, dict) and baseline_cell.get('acc') is not None:
        base_pct = round(baseline_cell['acc'] * 100)
        for cp in copies:
            if cp['pct'] is not None and abs(cp['pct'] - base_pct) > 0.5:
                return 'P1'
    return 'P2'


def selftest():
    """N09 合成用例自检（021BU，不触库）：检测器与分级器可信性。"""
    ok_all = True

    # 用例1：A 级副本检出（含百分数 + c/n）
    copies = n09_find_copies('评级旁注：历史命中 69%（159/230）')
    t1 = bool(copies and copies[0]['pct'] == 69.0
              and copies[0]['c'] == 159 and copies[0]['n'] == 230)
    print(f'[自检] A级副本检出: {"✓" if t1 else "✗"}')
    ok_all = ok_all and t1

    # 用例2：C 级副本检出（样本不足，无百分数）
    copies = n09_find_copies('强烈推荐买入：样本不足（n=4）')
    t2 = bool(copies and copies[0]['pct'] is None and copies[0]['n'] == 4)
    print(f'[自检] C级副本检出: {"✓" if t2 else "✗"}')
    ok_all = ok_all and t2

    # 用例3：普通百分数不误报（数据完整度等非证据文案）
    t3 = n09_find_copies('技术面数据完整度 85%；消息面 60%') == []
    print(f'[自检] 非证据百分数不误报: {"✓" if t3 else "✗"}')
    ok_all = ok_all and t3

    # 用例4：分级——在场均 P2；数字失真 P1；无副本 None
    base = {'grade': 'A', 'n': 159, 'm': 230, 'acc': 0.6913, 'display': '历史命中 69%（159/230）'}
    copies = n09_find_copies('历史命中 69%（159/230）')
    s_p2 = n09_severity(copies, base)
    s_p1 = n09_severity(n09_find_copies('历史命中 50%（100/200）'), base)
    s_none = n09_severity([], base)
    t4 = s_p2 == 'P2' and s_p1 == 'P1' and s_none is None
    print(f'[自检] N09 分级（在场 P2/失真 P1/无副本 None）: 实际 {s_p2}/{s_p1}/{s_none} '
          f'→ {"✓" if t4 else "✗"}')
    ok_all = ok_all and t4

    print(f'[自检] 结果：{"全部通过（N09 规则可信）" if ok_all else "存在失败用例（规则需修订）"}')
    return ok_all


def main():
    for _s in (sys.stdout, sys.stderr):
        if _s is not None and hasattr(_s, 'reconfigure'):
            try:
                _s.reconfigure(encoding='utf-8', errors='replace')
            except (ValueError, OSError):
                pass
    parser = argparse.ArgumentParser(description='021BS R2 新维度复审审计（只读）')
    parser.add_argument('--no-report', action='store_true')
    parser.add_argument('--selftest', action='store_true', help='只跑合成用例自检（不触库）')
    parser.add_argument('--out', default=os.path.join(
        _PROJECT_ROOT, 'docs', 'reports', '021bs_audit_r2_20260922.md'))
    args = parser.parse_args()

    if args.selftest:
        sys.exit(0 if selftest() else 1)

    from datetime import datetime, timedelta, timezone
    cn = timezone(timedelta(hours=8), name='Asia/Shanghai')
    today = datetime.now(cn).strftime('%Y-%m-%d')

    findings = []
    oks = []

    def add(rule, sev, stock, surface_a, surface_b, phenomenon, root_cause, fix_do, fix_how, b24=False):
        findings.append({
            'rule': rule, 'severity': sev, 'stock': stock,
            'surface_a': surface_a, 'surface_b': surface_b,
            'phenomenon': phenomenon, 'root_cause': root_cause,
            'fix_do': fix_do, 'fix_how': fix_how, 'b24': b24,
        })

    def ok(rule, note):
        oks.append({'rule': rule, 'note': note})

    conn = r1.open_ro_db()
    cur = conn.cursor()
    client = r1.make_client()

    stocks = r1.load_watchlist(cur)
    latest_reports, prev_reports = r1.load_reports(cur)
    ws_data = r1._get_json(client, '/api/portfolio/watchlist-scores') or {}
    ws_by_id = {s['id']: s for s in (ws_data.get('stocks') or [])}
    # 行动清单快照仅用于本报告 §4 摘要（R1 脚本已含 F01 全量核对，此处不重复）
    r1._get_json(client, '/api/dashboard/action-list')
    alerts_today = r1.load_alerts_today(cur, today)

    stock_by_id = {s['id']: s for s in stocks}

    # ---------- N01 / N02 / N08：逐股（存量报告 × 现算 × 看板读取面） ----------
    n01_hits, n02_hits = 0, 0
    for s in stocks:
        sid = s['id']
        rep = latest_reports.get(sid)
        if not rep:
            continue
        kf = r1.parse_json_field(rep.get('key_factors')) or {}
        trader = kf.get('trader')
        gen_at = str(rep.get('generated_at') or '').replace('T', ' ')[:19]
        label = f"{s['symbol']} {s['name']}"

        # N01：trader 摘要落库完整性（021BS t4 断言化：落库缺失时看板读取面必须兜底）
        if not (isinstance(trader, dict) and trader.get('top_action')):
            n01_hits += 1
            # 读取面兜底断言：watchlist-scores trader_signal（live 现算，零写库）
            sig = (ws_by_id.get(sid) or {}).get('trader_signal') or {}
            fallback_ok = bool(sig.get('top_action') or sig.get('stage_name'))
            if fallback_ok:
                add('N01', 'P2', label,
                    f'报告 key_factors.trader {"缺失" if not trader else "无 top_action"}'
                    f'（generated_at={gen_at}，019A 刷新覆盖残留）',
                    f'看板读取面 trader_signal 兜底在场'
                    f'（stage={sig.get("stage_name")}，top_action={str(sig.get("top_action"))[:36]}）',
                    '落库摘要缺失但消费面已由读取路径 live 兜底调和（021BS t4 修复面：'
                    'chip/行动清单与个股页 live 同源，「一面沉默一面报警」消除）；'
                    '落库面随下次批次重生成自愈合流',
                    'modules/trader_advisor.py derive_trader_signal_summary（读取面兜底，'
                    '消费方：watchlist_scores chip / action_list overview）',
                    False, '无需修复：兜底面在场即视为已调和（P2 存档）；次日批次落库合流后转 OK')
            else:
                add('N01', 'P1', label,
                    f'报告 key_factors.trader {"缺失" if not trader else "无 top_action"}（generated_at={gen_at}）',
                    '看板 top_action chip / ⚡分歧标注的落库面，且读取面兜底亦缺',
                    '批次后报告被刷新覆盖（021K 过期重评/手动刷新 → generate_advice 内 019A 回写），'
                    'key_factors 被 _build_key_factors（不含 trader）整体覆盖，且看板读取面兜底'
                    '未生效（数据不足或兜底链路回归）：看板该股 chip 与分歧标注消失、'
                    '个股页操盘手卡 live 现算仍显示止损/分歧报警——同一事实两面表现不一'
                    '（一面沉默可被读作"无需行动"）',
                    'modules/advisor.py _save_daily_report_for_advice（019A 豁免区）UPDATE 分支整体覆盖 key_factors；'
                    'trader_advisor.derive_trader_signal_summary 兜底链路回归或数据不足',
                    True,
                    'B24 边界：generate_advice 本体不可动。先核对 021BS t4 兜底链路'
                    '（watchlist_scores._derive_trader_signal / action_list live_trader_fallback）'
                    '是否回归、generate_trader_advice 为何 available=False；'
                    '数据不足属正常静默区（K线不足 35 根），代码回归按兜底契约修复',
                    b24=True)
        else:
            # N02：分歧标注 stored×live（trader 在场才有比对意义）
            live = r1._get_json(client, f'/api/stocks/{sid}/trader-advice') or {}
            live_dis = bool(live.get('disagreement'))
            stored_dis = bool(trader.get('has_disagreement'))
            if live_dis != stored_dis:
                pre_021br = gen_at < _021BR_T2_DEPLOYED_AT
                n02_hits += 1
                add('N02', 'P2' if pre_021br else 'P1', label,
                    f'stored has_disagreement={stored_dis}（generated_at={gen_at}）',
                    f'live disagreement={"在场" if live_dis else "无"}',
                    '分歧标注 stored×live 漂移'
                    + ('——报告由 021BR t2 部署前批次生成（stage_leads_rating 通道未上线），'
                       '看板 overview 无 ⚡ 而个股页有，属同日切换旧数据，次日批次自愈'
                       if pre_021br else '——同代报告不应漂移，需排查'),
                    'daily_report 预计算时点 vs trader_advisor.detect_disagreement 触发表版本',
                    False, '自愈型：无需修复（次日批次对齐）；若次日仍漂移再按触发表核对')

        # N08：score_tier_note 落库×读取面 一致性
        note_stored = kf.get('score_tier_note')
        note_live = (ws_by_id.get(sid) or {}).get('score_tier_note')
        if note_stored and note_live and note_stored != note_live:
            add('N08', 'P1', label,
                f'落库 score_tier_note=「{note_stored[:40]}…」',
                f'读取面 score_tier_note=「{note_live[:40]}…」',
                '同一报告的失配注记在落库面与读取面不同文（应同源纯函数同输入）',
                'blueprints/portfolio/watchlist_scores.py _derive_score_tier_note 输入面（score/rating/market）',
                True, '核对两处取的市场与分数来源；纯读取面修复（B24 外）')
        elif note_stored and note_live:
            ok('N08', f'{label} 落库×读取面注记一致')
        # markdown 迟滞行 × 口径说明行 互斥（行级判定——021BU t5 终验实测修正：
        # 口径说明行的解释文字自带「评级迟滞保持态（021AG）」字样，全文子串匹配
        # 会把单行误判为两行并存）
        md = rep.get('markdown_content') or ''
        md_lines = md.splitlines()
        has_note_line = any('评级口径说明' in ln for ln in md_lines)
        has_hyst_line = any(
            ('评级迟滞' in ln) and ('评级口径说明' not in ln) for ln in md_lines
        )
        if has_hyst_line and has_note_line:
            add('N08', 'P1', label,
                'markdown 同时含「评级迟滞」行与「评级口径说明」行',
                '两行为同义反复（_build_markdown_single 设计为 else 分支互斥）',
                '重复标注：用户同屏看到两条同义说明，口径解释互相稀释',
                'modules/advisor.py _build_markdown_single 迟滞/失配注记分支',
                True, '核对该行生成路径是否绕过 else 门控（如手工拼装）')
        elif has_hyst_line or has_note_line:
            ok('N08', f'{label} 迟滞/口径说明标注互斥成立（单行在场）')

    # ---------- N03/N04/N05：静态代码面 ----------
    js_path = os.path.join(_PROJECT_ROOT, 'static', 'js', 'analysis.js')
    with open(js_path, encoding='utf-8') as fh:
        js = fh.read()
    card_title_old = '仓位动作以评级为准'
    matrix_subtitle = '纪律无条件执行 · 减仓听操盘手 · 加仓看评级'
    if card_title_old in js and matrix_subtitle in js:
        add('N03', 'P1', '全局（操盘手建议卡）',
            f'卡头副词「{card_title_old}」（021BQ 时代文案，analysis.js）',
            f'同卡 ④ 操作矩阵副词「{matrix_subtitle}」（021BR 分域契约）',
            '同一张卡两行副词互斥：止损已触发时矩阵行明示"无条件执行，不等评级"，'
            '卡头却宣称仓位动作以评级为准——021BR 分域契约落地后旧文案未同步，'
            '用户在最高优先级风控场景读到相反的权威声明',
            'static/js/analysis.js 操盘手卡标题（2026-09-18 021BQ 文案）未随 021BR 分域层级更新',
            True,
            '纯前端一行：卡头副词改为与 operations.hierarchy_note 同源的分域措辞'
            '（"纪律无条件执行 · 减仓听操盘手 · 加仓看评级"），与 ④ 段副词统一')
    else:
        ok('N03', '操盘手卡头副词与分域契约措辞一致（或旧文案已移除）')

    adv_path = os.path.join(_PROJECT_ROOT, 'modules', 'advisor.py')
    with open(adv_path, encoding='utf-8') as fh:
        adv_src = fh.read()
    # N04：刷新路径（_save_daily_report_for_advice）markdown 不拼数据完整度小节，仅批次路径拼
    if '_build_markdown_single(md_source' in adv_src and '数据完整度' not in adv_src.split('def _save_daily_report_for_advice')[1].split('def ')[0]:
        add('N04', 'INFO', '全局（刷新路径报告）',
            '批次路径 markdown 末尾有「## 数据完整度」小节',
            '刷新路径（_save_daily_report_for_advice）markdown 无该小节（020R-41 只补 data_warnings JSON）',
            '两条生成路径的 markdown 形态差异；报告页数据完整度卡读 data_warnings JSON 不受影响',
            'modules/daily_report.py（批次拼小节）vs modules/advisor.py（刷新路径不拼）',
            False, '无需修复：展示信息经 JSON 面完整可达；如追求 markdown 形态统一可在刷新路径同拼（纯组装层）')

    alert_path = os.path.join(_PROJECT_ROOT, 'modules', 'alert_engine.py')
    with open(alert_path, encoding='utf-8') as fh:
        alert_src = fh.read()
    if "分 < 阈值" in alert_src:
        alerts_js = os.path.join(_PROJECT_ROOT, 'static', 'js', 'alerts.js')
        with open(alerts_js, encoding='utf-8') as fh:
            alerts_js_src = fh.read()
        escaped = 'escapeHtml(a.message' in alerts_js_src
        add('N05', 'INFO', '全局（score_below 预警消息）',
            '消息文案含裸 "<"（"当前 X 分 < 阈值 Y 分"，021BN 前遗留）',
            'alerts.js 渲染前 escapeHtml 转义兜住' if escaped else '前端未转义（需立即修）',
            '021BN「禁裸 <」惯例的存量例外；因 < 后跟空格不构成 HTML 标签且前端已转义，实际无渲染风险',
            'modules/alert_engine.py _format_message score_below 分支',
            False, '随下次文案批次文字化（"低于阈值"）即可，非本轮必修')

    # ---------- N06：跨日时效 ----------
    stale_pc = 0
    for s in stocks:
        pc = r1.q1(cur, 'SELECT updated_at FROM price_cache WHERE stock_id=?', (s['id'],))
        k = r1.q1(cur, 'SELECT MAX(trade_date) d FROM raw_kline WHERE stock_id=?', (s['id'],))
        if pc and k and k['d'] and str(pc['updated_at'])[:10] < str(k['d']):
            stale_pc += 1
    add('N06', 'INFO', '全局（持仓页价格源）',
        f'price_cache 早于最新 K 线交易日的股票 {stale_pc} 只',
        '盘中快照 vs 日K收盘双口径（R1 F03 已逐股标注 P2）',
        '收盘后 price_cache 不回写，持仓页现价与矩阵现价存在日内口径差；过期有 price_expired 标记',
        'price_cache 刷新链路（特性非缺陷，页面已有口径标注）',
        False, '维持现状；如需闭环可评估收盘后一次性回写（另立批次）')

    ws_stale = [
        f"{s['symbol']} {s['name']}（report_date={ws_by_id[s['id']].get('report_date')}）"
        for s in stocks
        if s['id'] in ws_by_id and (ws_by_id[s['id']].get('report_date') or '') < today
    ]
    if ws_stale:
        # 全量滞后且报告日期=上一交易日 → 当日 15:54 批次未到的正常跨日状态（非矛盾）
        normal_gap = len(ws_stale) == len(latest_reports)
        add('N06', 'INFO' if normal_gap else 'P2', '全局（看板评分日期）',
            f'{len(ws_stale)} 只 watchlist-scores 报告日期落后今日：' + '、'.join(ws_stale[:6]),
            '看板实时面读最新 ok 报告（019D 口径）',
            ('全部股报告日期=上一交易日且当日批次（15:54）未到——正常跨日状态，非矛盾'
             if normal_gap else
             '缺报/补采日的看板日期滞后（部分股落后）；021K 过期重评与次日批次兜底'),
            'watchlist_scores 019R 派生表', False,
            '维持现状（行动清单路1对缺报股已显式列出；批次后自动对齐）')

    pre_t3_held = []
    held_ids = list((r1.load_holdings_agg(cur)).keys())
    for sid in held_ids:
        rep = latest_reports.get(sid)
        if not rep:
            continue
        gen_at = str(rep.get('generated_at') or '').replace('T', ' ')[:19]
        if gen_at < _021BR_T3_DEPLOYED_AT:
            s = stock_by_id.get(sid, {})
            pre_t3_held.append(f"{s.get('symbol')} {s.get('name')}")
    if pre_t3_held:
        add('N06', 'INFO', '全局（止损口径自愈窗口）',
            f'021BR t3 部署前生成的持仓股报告 {len(pre_t3_held)} 只：' + '、'.join(pre_t3_held),
            'chip 止损数字为 t3 前口径（top_action 未并 price_advice），与报告行内 pa.stop_loss 双数字',
            'R1 F02/R13 已逐股 P2（存量特征）',
            'daily_report 生成时序（021BR t3 已修，存量自愈）',
            False, '下一日 15:54 批次重生成后自然合流；无需代码动作')

    # ---------- N07：边界面 ----------
    short_k = [dict(r) for r in cur.execute(
        'SELECT stock_id, COUNT(*) n FROM raw_kline GROUP BY stock_id HAVING n < 35')]
    if short_k:
        add('N07', 'INFO', '全局（信号静默区）',
            f'K线不足 35 根的股票 {len(short_k)} 只',
            'market_screener 信号检测 <35 根静默跳过（设计内）',
            '信号类要素对短历史股不可用（罗盘/阶段仍可判定）',
            'market_screener detect_signals 门槛', False, '设计内，无需动作')
    else:
        ok('N07', '无 K线不足 35 根的股票（信号静默区无人落入）')

    missing = [f"{s['symbol']} {s['name']}" for s in stocks if s['id'] not in latest_reports]
    if missing:
        add('N07', 'INFO', '全局（缺报股）',
            f'今日无 ok 日报 {len(missing)} 只：' + '、'.join(missing[:8]),
            '报告面七要素缺失（行动清单路1已显式列出）',
            '采集失败/新增股未生成', 'daily_report 批次', False,
            '行动清单「缺报补数」已覆盖；报告面核对自动跳过')
    else:
        ok('N07', f'今日 {len(latest_reports)}/{len(stocks)} 只全部有 ok 日报（缺报面无矛盾暴露）')

    zone_bad = []
    zone_ok = 0
    for sid, rep in latest_reports.items():
        pa = r1.parse_json_field(rep.get('price_advice')) or {}
        if pa.get('has_position'):
            continue  # 有持仓无 zone_label 契约
        expect = ZONE_CONTRACT.get(rep.get('rating'))
        if expect and pa.get('zone_label') == expect:
            zone_ok += 1
        elif pa.get('zone_label') is not None:
            s = stock_by_id.get(sid, {})
            zone_bad.append(f"{s.get('symbol')} {s.get('name')}"
                            f"（{rep.get('rating')}×{pa.get('zone_label')}）")
    if zone_bad:
        add('N07', 'P1', '全局（无持仓股区间语义）',
            '021BF zone_label×rating 契约违反：' + '、'.join(zone_bad[:8]),
            f'其余 {zone_ok} 只无持仓股全部符合契约',
            '空仓股价格建议区间语义与评级档位不符（021BF 修复面回归特征）',
            'modules/price_advisor.py _gen_no_position', True,
            '按 021BF 契约核对 _gen_no_position 分支；该报告重生成自愈或代码回归按契约修复')
    else:
        ok('N07', f'无持仓股 zone_label×rating 全映射符合 021BF 契约（{zone_ok} 只）')

    # ---------- N09：反落库守卫（021BU O6：证据元素读取面现算、零落库） ----------
    evidence_baseline = {}
    for _m in sorted({s.get('market') or 'a_stock' for s in stocks} | {'a_stock'}):
        evidence_baseline[_m] = r1.load_evidence_baseline(cur, _m)
    n09_scan = 0
    for sid, rep in latest_reports.items():
        s = stock_by_id.get(sid, {})
        market = s.get('market') or 'a_stock'
        label = f"{s.get('symbol')} {s.get('name')}"
        for surface, text in (('key_factors', rep.get('key_factors')),
                              ('markdown_content', rep.get('markdown_content'))):
            copies = n09_find_copies(text)
            if not copies:
                continue
            n09_scan += 1
            base_cell = ((evidence_baseline.get(market) or {}).get(rep.get('rating')) or {}).get('primary')
            sev09 = n09_severity(copies, base_cell)
            if sev09 == 'P1':
                add('N09', 'P1', label,
                    f'{surface} 存储命中率副本：{copies[0]["text"]}',
                    f'现算基准：{base_cell["display"] if base_cell else "样本不足"}',
                    '落库的命中率数字与现算不符（冻结快照已打架，违反 021BU O6 读取面现算设计）',
                    'daily_reports 落库面出现证据数字副本（021BU 后不应存在）', True,
                    '移除落库副本；展示面改消费共享证据函数现算（与看板同源同值）')
            else:
                add('N09', 'P2', label,
                    f'{surface} 存在命中率文案副本：{copies[0]["text"]}',
                    '021BU O6 设计约束：证据元素一律读取面现算、零落库（防 stored-vs-live 漂移）',
                    '落库副本为反漂移观察对象（当前数字与现算一致或暂不可比）',
                    '021BU 前存量文案或非证据文案的巧合命中', False,
                    '观察即可；若后续批次再现，核对生成链是否新增证据落库点')
    if n09_scan == 0:
        ok('N09', '全部存量报告无落库命中率副本（O6 反落库守卫通过）')

    # ---------- 汇总 ----------
    sev = {}
    for x in findings:
        sev[x['severity']] = sev.get(x['severity'], 0) + 1
    n01_p1 = sum(1 for x in findings if x['rule'] == 'N01' and x['severity'] == 'P1')
    n01_p2 = sum(1 for x in findings if x['rule'] == 'N01' and x['severity'] == 'P2')
    n03_p1 = sum(1 for x in findings if x['rule'] == 'N03' and x['severity'] == 'P1')
    print(f'[R2 审计] 新维度发现 {len(findings)} 条 ' +
          ' '.join(f'{k}={v}' for k, v in sorted(sev.items())) +
          f'，OK {len(oks)} 条；N01 落库缺失 {n01_hits} 股（兜底在场 P2 {n01_p2} / 兜底亦缺 P1 {n01_p1}），'
          f'N02 命中 {n02_hits} 股')

    if not args.no_report:
        write_r2_report(findings, oks, {
            'today': today, 'stocks': stocks, 'n01_hits': n01_hits, 'n02_hits': n02_hits,
            'n01_p1': n01_p1, 'n01_p2': n01_p2, 'n03_p1': n03_p1,
            'alerts_today': alerts_today, 'sev': sev,
        }, args.out)
        print(f'[R2 审计] 报告已写入：{args.out}')
    conn.close()
    sys.exit(0)


def write_r2_report(findings, oks, meta, out_path):
    now = datetime_now_cn()
    sev = meta['sev']
    order = {'P0': 0, 'P1': 1, 'P2': 2, 'INFO': 3}
    rows = sorted(findings, key=lambda x: (order.get(x['severity'], 9), x['rule'], x['stock']))
    lines = []
    w = lines.append
    w('# 021BS 第二轮复审审计报告（R2）— R1 清零确认 + 新维度深挖 + 抽样人工核读')
    w('')
    w(f'> 日期：{meta["today"]} ｜ 执行：scout（任务 t3，复审脚本 `scripts/audit_consistency_021bs_r2.py`）｜ 批次：021BS')
    w('> 方法：**全程只读**——R1 审计脚本（经 t2 断言化）独立重跑 + 本脚本新维度（N01-N08：'
      '修复引入面/跨日时效/边界面/静态代码面）+ 6 份完整报告人工逐段核读（含中国中免、五粮液）。'
      '零写库、零网络、零触碰 advisor.generate_advice（B24）。')
    w(f'> 运行时刻：{now}；自选股 {len(meta["stocks"])} 只；当日预警 {len(meta["alerts_today"])} 条。')
    w('> 注：§0①行与 §1/§3/§4 为 t3 复审时点快照叙述；重跑时动态结果以 §0②行与 §2 为准。')
    w('')
    w('---')
    w('')
    w('## 0. 结论速览')
    w('')
    w('| 项 | 结果 |')
    w('|---|---|')
    w('| ① R1 审计脚本重跑（独立复现，t3 时点快照） | **P0=0 / P1=0 / P2=28 / OK=230，异常 0**；与 t2 存档逐行一致（仅运行时刻行不同）——清零确认且可复现 |')
    w(f'| ② 新维度发现（动态） | P0 **{sev.get("P0", 0)}** / P1 **{sev.get("P1", 0)} 项**'
      f'（N01 落库缺失 {meta["n01_hits"]} 股：读取面兜底在场=P2 {meta.get("n01_p2", 0)}、'
      f'兜底亦缺=P1 {meta.get("n01_p1", 0)}；N03 旧副词互斥 P1 {meta.get("n03_p1", 0)}）'
      f'/ P2 {sev.get("P2", 0)} / INFO {sev.get("INFO", 0)}——详见 §2 |')
    w('| ③ 抽样人工核读 | 6 份完整报告逐段对账（中免/五粮液/拓尔思/美团/腾讯/宁德），数字两两自洽，无新增矛盾 |')
    w('| ④ 五数据流重跑 | F01/F05 零不一致；F02 3 条、F03 9 条、F04 1 条 P2（均 R1 已知存量/时点差，无新增） |')
    w('')
    if meta.get('n01_p1', 0) == 0 and meta.get('n03_p1', 0) == 0:
        w('**P1 findings：0 项**——N01 落库缺失股已由看板读取面 live 兜底调和（P2 存档，'
          '次日批次落库合流）；N03 卡头副词已统一为 021BR 分域表述（t4 修复）。')
    else:
        w('**P1 findings（报告队长）**：')
        w('')
        if meta.get('n01_p1', 0) > 0:
            w(f'1. **N01 批次后刷新覆盖丢失 trader 摘要且兜底亦缺**（P1 {meta["n01_p1"]} 股）——'
              '看板 top_action chip 与 ⚡分歧标注消失且读取面兜底未生效，个股页 live 仍报警；')
        if meta.get('n03_p1', 0) > 0:
            w('2. **N03 操盘手卡头旧副词「仓位动作以评级为准」与 021BR 分域副词同屏互斥**（纯前端一行）。')
    w('')
    w('---')
    w('')
    w('## 1. R1 修复确认表（t2 修复 → R2 独立复核；t3 时点快照）')
    w('')
    w('| R1 条目 | t2 修复声明 | R2 独立复核 | 判定 |')
    w('|---|---|---|---|')
    w('| P1-1 拓尔思迟滞保持态无持续标注 | 三层标注面：markdown 注记 + key_factors.score_tier_note + 读取面（watchlist-scores/report-latest/analysis.js 横幅） | 重跑 R1 断言化脚本：拓尔思 R04 → P2「读取面 score_tier_note 在场」；`score_tier_mismatch_note` 与 apply_hysteresis 同阈值表（R7 合规）；selftest 12 用例全过 | ✅ 关闭（P1→P2 已标注） |')
    w('| P2① 技术面/罗盘/阶段口径差标注 | analysis.js 罗盘卡固定脚注（三层口径说明 + 指向操作矩阵） | 脚注在位且措辞与 hierarchy_note 一致；R05/R06/R11 类 P2 全部有标注通道 | ✅ 标注到位 |')
    w('| P2② 预警消息缺数据时点 | alert_engine 五类消息尾部 `_data_cutoff_note` | 五类全部接线（rating=rating_date / score=analysis_date / capital=最新交易日 / 信号=kline_upto），与 021BQ 调和注记并存 | ✅ 接线完整 |')
    w('| （连带）快照 dimensions 重建收敛四维键 | analysis.py 仅取四维规范键且值须为 dict | 静态核验在位；021BQ 起 trader 伪维度副作用（最弱维度显示 trader 0 分）一并消除 | ✅ |')
    w('')
    w('---')
    w('')
    w('## 2. 新发现矛盾矩阵（N 系列，同 R1 分级）')
    w('')
    for severity, title in (('P0', 'P0 指令矛盾'), ('P1', 'P1 表述误导'), ('P2', 'P2 口径差异'), ('INFO', '信息性观察')):
        group = [x for x in rows if x['severity'] == severity]
        if not group:
            continue
        w(f'### {title}（{len(group)} 条）')
        w('')
        for i, x in enumerate(group, 1):
            w(f'**{"P1" if severity=="P1" else ("P2" if severity=="P2" else ("INFO" if severity=="INFO" else "P0"))}-{i}｜{x["rule"]}｜{x["stock"]}**')
            w('')
            w(f'- 表面A：{x["surface_a"]}')
            w(f'- 表面B：{x["surface_b"]}')
            w(f'- 现象：{x["phenomenon"]}')
            w(f'- 根因模块：{x["root_cause"]}')
            fix = '做' if x.get('fix_do') else '暂不做（核对/标注即可）'
            b24 = '；⚠️ 根因涉及 B24 冻结面，只允许外层调和' if x.get('b24') else ''
            w(f'- 修复建议：**{fix}**——{x["fix_how"]}{b24}')
            w('')
    if oks:
        w(f'### 核对一致基线（OK，{len(oks)} 条）')
        w('')
        for x in oks:
            w(f'- [{x["rule"]}] {x["note"]}')
        w('')
    w('---')
    w('')
    w('## 3. 抽样人工核读（6 份完整报告逐段对账；t3 时点快照）')
    w('')
    w('| 报告 | 核读要点 | 判定 |')
    w('|---|---|---|')
    w('| 601888 中国中免（20:25 刷新版） | 56.4（持有观望级）档位相符；操作建议「持有」=021BH 矩阵(持有观望,持仓,浮亏)；pa S4 已破止损（52.27<56.16=成本底线61.05×0.92）状态机自洽；profit_pct -14.4% 反算一致；**trader 键被刷新覆盖丢失（N01 实锤）**；markdown 缺数据完整度小节（N04，JSON 面可达） | 数字自洽；暴露 N01 |')
    w('| 000858 五粮液（15:57 批次） | 54.7（持有观望级）+↑7.9；止损 68.09=成本底线（74.01×0.92）与纪律线一致，未触发（70.54>68.09）chip 无后缀正确；trader 在场；风险提示与四维分（技术 44.7）相容 | ✅ 全对账一致 |')
    w('| 300229 拓尔思（20:27 刷新版） | 52.0（建议减仓级）——分数×档位失配在案；markdown 无迟滞行（刷新路径丢失，t2 修复前存量），报告页横幅/看板由读取面 score_tier_note 兜底（重跑断言确认 P2 已标注）；pa S2 浮盈中状态机自洽（13.73≥成本13.65）；**trader 键被覆盖丢失（N01）**；操作建议「持有」=021BH(建议减仓,持仓,浮盈) 相符 | 数字自洽；R1-P1 关闭确认；暴露 N01 |')
    w('| HK3690 美团-W（16:10 批次） | 42.1（建议减仓级）档位相符；港股降级提示（资金面 50% 置信收缩 31.7→37.8）如实标注；pa stop 61.49=73.2×0.84（港股 -16% 校准）✓；top_action 止损·52.62 为 t3 前存量口径（R1 F02/R13 已 P2，自愈窗口） | ✅ 数字自洽 |')
    w('| HK0700 腾讯控股（16:10 批次） | 65.7（持有观望级）——港股 overrides（<70 不入买入档）正确；空仓「关注」=021BH(持有观望,无仓)；pa 参考区间+等待回调=021BF 观望档契约 ✓；score_below 预警为 15:59 触发、报告 16:10 重算，时点差已由 R1 F04 P2 在案 | ✅ 数字自洽 |')
    w('| 300750 宁德时代（15:56 批次） | 69.9（推荐买入级）档位相符；空仓「买入」+pa 买入区间+「可按计划买入」=021BF 买入档 ✓；trader has_disagreement=true（structural_risk 派发嫌疑）与 R06 P2「推荐买入×罗盘下跌·已标注」闭环 | ✅ 全对账一致 |')
    w('')
    w('---')
    w('')
    w('## 4. 报告↔看板五数据流重跑（R1 面确认；t3 时点快照）')
    w('')
    w('| 数据流 | R2 重跑结果 | 判定 |')
    w('|---|---|---|')
    w('| F01 行动清单 vs 报告评级/操盘手 | 0 条不一致；stats 与 DB 重算一致（stop_discipline_hits=1 置顶） | ✅ |')
    w('| F02 看板 chip vs 报告 price_advice | 3 条 P2（美团/无线传媒 t3 前存量、顺丰持仓变更时点差），无新增 | ✅（存量自愈型） |')
    w('| F03 持仓页价格 vs 矩阵现价 | 9 条 P2（盘中快照 vs 日K收盘双口径+多账户口径），无新增 | ✅（特性已标注） |')
    w('| F04 预警铃铛 vs 报告 | 1 条 P2（腾讯 15:59 预警 vs 16:10 重算报告，时点差）；t2 时点注记已接线，明日新预警自带时点 | ✅ |')
    w('| F05 watchlist-scores 评级 vs 报告 | 0 条不一致（56/56 同源） | ✅ |')
    w('')
    w('---')
    w('')
    w('## 5. 修复建议汇总（P1 两条；B24 冻结面显式标注）')
    w('')
    w('| 条目 | 分级 | 做/不做 | 建议 | B24 |')
    w('|---|---|---|---|---|')
    for x in rows:
        if x['severity'] != 'P1':
            continue
        fix = '做' if x.get('fix_do') else '暂不做'
        b24 = '⚠️ 只能外层调和' if x.get('b24') else '—'
        w(f'| {x["rule"]} {x["stock"]} | {x["severity"]} | {fix} | {x["fix_how"]} | {b24} |')
    w('')
    w('P2/INFO 条目处置：N02（批次时点旧数据）次日自愈不动；N04/N05/N06/N07 维持现状或随文案批次顺带；'
      'N07 无命中（边界面全部干净）。')
    w('')
    w('---')
    w('')
    w('## 6. 复现命令')
    w('')
    w('```bash')
    w('python scripts/audit_consistency_021bs.py --out docs/reports/_r2_rerun_tmp.md   # R1 断言化重跑（本轮与 t2 存档逐行一致）')
    w('python scripts/audit_consistency_021bs_r2.py                                    # 本报告（N 系列新维度）')
    w('python scripts/check_redlines.py                                                # 红线 28/28')
    w('```')
    content = '\n'.join(lines) + '\n'
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as fh:
        fh.write(content)


def datetime_now_cn():
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=8), name='Asia/Shanghai')).strftime('%Y-%m-%d %H:%M:%S')


if __name__ == '__main__':
    main()
