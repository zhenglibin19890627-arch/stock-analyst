"""
红线自动核验脚本（021A 红线治理）

单一事实来源：docs/RED_LINES.md。本脚本把其中「可自动核验」的红线
落成机械检查，纳入 pytest（tests/test_redlines.py）随测试套件执行。

运行：python scripts/check_redlines.py
退出码：0 = 全部通过；1 = 存在违规。

注意：本脚本只读源码与配置（不触网、不写库），可安全并入 CI。
检查项的「锚点」是语义锚点（函数签名/唯一约束字面量/过滤表达式），
不依赖行号，避免文档行号漂移问题。
"""

import json
import os
import re
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)


def _read(rel_path):
    with open(os.path.join(BASE_DIR, rel_path), encoding='utf-8') as f:
        return f.read()


def _read_bytes(rel_path):
    with open(os.path.join(BASE_DIR, rel_path), 'rb') as f:
        return f.read()


_CHECK_FUNCS = []


def _check(rid):
    """注册红线检查（rid 对应 RED_LINES.md 中的红线编号）"""
    def deco(fn):
        _CHECK_FUNCS.append((rid, fn))
        return fn
    return deco


# ============================================================
# 评分与评级（R6 / R7 / R13 / R14 / R15）
# ============================================================


@_check('R6')
def rating_config_consistent():
    """评级边界 80/65/50/30 三处一致（config.py / config_weights.json / scoring_engine.py）"""
    from modules.analysis_engine import validate_rating_config

    issues = validate_rating_config()
    return (not issues), ('三处一致' if not issues else '; '.join(issues))


@_check('R6')
def weights_json_no_bom():
    """config_weights.json 写入必须无 BOM"""
    head = _read_bytes('config_weights.json')[:3]
    return head != b'\xef\xbb\xbf', ('无 BOM' if head != b'\xef\xbb\xbf' else '发现 UTF-8 BOM')


@_check('R6')
def weights_json_rating_mapping():
    """config_weights.json rating_mapping 边界与 80/65/50/30 档位一致"""
    expected = {
        '强烈推荐买入': (80, 100),
        '推荐买入': (65, 79),
        '持有观望': (50, 64),
        '建议减仓': (30, 49),
        '强烈建议卖出': (0, 29),
    }
    data = json.loads(_read('config_weights.json'))
    rm = data.get('rating_mapping', {})
    problems = []
    for label, (lo, hi) in expected.items():
        got = rm.get(label)
        if got is None:
            problems.append(f'缺少档位「{label}」')
        elif got.get('min') != lo or got.get('max') != hi:
            problems.append(f'档位「{label}」边界 {got.get("min")}/{got.get("max")} ≠ {lo}/{hi}')
    return (not problems), ('符合 80/65/50/30' if not problems else '; '.join(problems))


@_check('R7')
def alert_reuses_normalize_rating():
    """D4：alert_engine 复用 scoring_engine.normalize_rating，不得重实现「分数→评级」边界映射

    允许：RATING_ORDER 档位顺序表（5/4/3/2/1 排名，用于跨档比较）；
          check_score_below 的 threshold=65 默认值（预警规则阈值，与 db_manager 全局规则一致）。
    禁止：任何 score 与 80/65/50/30 的数值比较链（把分数映射成评级档位）。
    """
    src = _read('modules/alert_engine.py')
    uses_norm = 'from modules.scoring_engine import normalize_rating' in src
    boundary_reimpl = re.search(
        r'score\s*(?:>=|<=|>|<|==)\s*(?:80|65|50|30)\b'
        r'|\b(?:80|65|50|30)\s*(?:>=|<=|>|<|==)\s*score',
        src,
    )
    ok = uses_norm and not boundary_reimpl
    if not uses_norm:
        detail = '未引用 normalize_rating'
    elif boundary_reimpl:
        detail = '疑似重实现分数→评级边界映射'
    else:
        detail = '复用 normalize_rating（RATING_ORDER 仅档位顺序）'
    return ok, detail


@_check('R13')
def b24_generate_advice_anchor():
    """B24：generate_advice 签名锚点存在（任何变更必须走豁免流程）"""
    src = _read('modules/advisor.py')
    ok = 'def generate_advice(stock_id, report_date=None):' in src
    return ok, ('签名锚点存在' if ok else '签名锚点缺失（函数被改名/改签名？）')


@_check('R14')
def b24_build_capital_factors_anchor():
    """R14：_build_capital_factors 资金面因子构建函数存在"""
    src = _read('modules/advisor.py')
    ok = 'def _build_capital_factors(factors, stock_data, stock_id):' in src
    return ok, ('锚点存在' if ok else '锚点缺失')


@_check('R15')
def fetch_capital_flow_signature():
    """011：fetch_capital_flow(symbol, market) 签名不可加参数"""
    src = _read('modules/data_collector.py')
    ok = 'def fetch_capital_flow(symbol, market):' in src
    return ok, ('签名 (symbol, market) 未变' if ok else '签名被修改')


# ============================================================
# 数据可信度（R1 / R2 / R5）
# ============================================================


@_check('R1')
def scoring_filters_estimated_rows():
    """评分读取必须过滤估算行（is_estimated=1 仅展示不参评）"""
    src = _read('modules/data_adapter.py')
    ok = 'is_estimated = 0 OR is_estimated IS NULL' in src
    return ok, ('评分路径过滤估算行' if ok else '评分路径未过滤 is_estimated=1')


@_check('R1')
def estimate_fallback_marked():
    """估算兜底写入必须带 is_estimated=1 标记"""
    src = _read('modules/data_collector.py')
    ok = 'is_estimated=1' in src
    return ok, ('估算兜底带标记' if ok else '估算兜底标记缺失')


@_check('R2')
def no_insert_or_replace_capital():
    """019K：raw_capital_flow 写入严禁 INSERT OR REPLACE（会清除已有字段）"""
    src = _read('modules/data_collector.py')
    bad = re.search(r'INSERT OR REPLACE INTO raw_capital_flow', src, re.IGNORECASE)
    return bad is None, ('未发现违规写入模式' if bad is None else '发现 INSERT OR REPLACE INTO raw_capital_flow')


@_check('R3')
def westock_strict_date_match():
    """M-2（westock）：逐日历史必须校验 EndDate == date_str，严禁取错日"""
    src = _read('modules/data_collector.py')
    ok = "(row.get('EndDate') or '').strip() != date_str" in src
    return ok, ('westock EndDate 精确匹配校验存在' if ok else 'westock 日期校验缺失')


@_check('R3')
def lscjfb_strict_date_match():
    """M-2（新浪 lscjfb）：逐日历史必须 opendate == target_date 才写，严禁取最新行"""
    src = _read('modules/data_collector.py')
    ok = 'opendate != target_date' in src
    return ok, ('lscjfb opendate 精确匹配校验存在' if ok else 'lscjfb 日期校验缺失')


@_check('R4')
def capital_weekend_guard():
    """019G/020L：非交易日（周六/周日）资金面全链路跳过"""
    src = _read('modules/data_collector.py')
    ok = 'datetime.now(_CN_TZ).weekday() >= 5' in src
    return ok, ('fetch_capital_flow 周末守卫存在' if ok else '周末守卫缺失')


@_check('R4')
def orderbook_weekend_guard():
    """021C：五档盘口（mootdx）非交易日跳过，防周末脏行"""
    src = _read('modules/data_collector.py')
    ok = '非交易日跳过（mootdx 盘口）' in src
    return ok, ('盘口周末守卫存在' if ok else '盘口周末守卫缺失')


@_check('R5')
def net_calls_via_timeout_wrapper():
    """新浪/腾讯网络调用必须走模块级 _call_with_timeout，严禁裸调用"""
    src = _read('modules/data_collector.py')
    ok = 'def _call_with_timeout' in src
    return ok, ('超时包装函数存在' if ok else '_call_with_timeout 缺失')


# ============================================================
# 写库不变量（R9 / R10 / R11 / R12）
# ============================================================


@_check('R9')
def daily_reports_unique_constraint():
    """013：daily_reports 三列唯一约束 (report_date, stock_id, report_type)"""
    src = _read('database/db_manager.py')
    ok = 'UNIQUE(report_date, stock_id, report_type)' in src
    return ok, ('三列唯一约束存在' if ok else '三列唯一约束缺失')


@_check('R9')
def daily_tops_intraday():
    """013：daily 生成时顶替当天 intraday（不变量语义）"""
    src = _read('modules/daily_report.py')
    ok = "DELETE FROM daily_reports WHERE report_date=? AND stock_id=? AND report_type='intraday'" in src
    return ok, ('daily 顶替 intraday 语义存在' if ok else '顶替语义缺失')


@_check('R10')
def ratings_history_replace():
    """R10：ratings_history 每股每天一条（INSERT OR REPLACE 写入锚点）"""
    src = _read('modules/advisor.py')
    ok = 'INSERT OR REPLACE INTO ratings_history' in src
    return ok, ('INSERT OR REPLACE 锚点存在' if ok else '写入锚点缺失')


@_check('R11')
def backup_before_destructive():
    """破坏性操作前备份机制存在（db_manager.backup_database）"""
    src = _read('database/db_manager.py')
    ok = 'def backup_database' in src and 'source.backup(dest)' in src
    return ok, ('在线热备份机制存在' if ok else '备份机制缺失')


@_check('R8')
def business_modules_no_direct_akshare():
    """数据源解耦：业务模块严禁直接 import akshare（仅数据采集模块允许）"""
    allowed = {'data_collector.py', 'index_collector.py', 'news_collector.py'}
    offenders = []
    for name in os.listdir(os.path.join(BASE_DIR, 'modules')):
        if not name.endswith('.py') or name in allowed or name.startswith('__'):
            continue
        src = _read(os.path.join('modules', name))
        if re.search(r'^\s*(import akshare|from akshare)', src, re.MULTILINE):
            offenders.append(name)
    if offenders:
        return False, f'业务模块直接耦合 akshare: {offenders}'
    return True, '业务模块无直接 akshare 依赖'


@_check('R8')
def stockdata_contract_defined():
    """StockData 契约模型存在（字段集以 data_contract.py 定义为准）"""
    src = _read('modules/data_contract.py')
    ok = 'class StockData(BaseModel)' in src
    return ok, ('StockData Pydantic 契约存在' if ok else '契约模型缺失')


@_check('R11')
def backup_failure_aborts_destructive():
    """破坏性操作前备份失败必须中止（021B 起：调用点检查返回值）"""
    src = _read('database/db_manager.py')
    checked = src.count('if backup_database(') >= 2
    ok = checked and '备份失败，中止' in src
    return ok, ('备份失败中止守卫存在' if ok else '备份返回值未检查（工程债）')


@_check('R12')
def db_pragmas():
    """WAL + busy_timeout=10s + foreign_keys=OFF（应用层手动级联）配置锚点"""
    src = _read('database/db_manager.py')
    ok = (
        'PRAGMA journal_mode=WAL' in src
        and 'PRAGMA busy_timeout=10000' in src
        and 'PRAGMA foreign_keys=OFF' in src
    )
    return ok, ('WAL/busy_timeout/foreign_keys 配置存在' if ok else 'PRAGMA 配置被修改')


# ============================================================
# 风控与工程约束（R16 / R17 / R18 / R19）
# ============================================================


@_check('R16')
def risk_thresholds_default():
    """风控阈值 5 项为默认值（变更必须登记理由）"""
    import config

    ok = (
        config.COST_ADJUSTMENT_DEVIATION_THRESHOLD == 0.30
        and config.COST_ADJUSTMENT_COOLDOWN_HOURS == 24
        and config.TRADE_T1_LOCK_ENABLED is True
        and config.TRADE_AMOUNT_VERIFY_THRESHOLD == 50000
        and config.BATCH_OPERATION_LIMIT == 20
    )
    return ok, ('风控阈值 5 项为默认值' if ok else '风控阈值被修改（需登记理由）')


@_check('R17')
def dependency_whitelist():
    """零代码约束：依赖必须落在白名单（新增需评审并更新 RED_LINES.md 附录A）"""
    allowed = {
        'akshare', 'flask', 'pandas', 'numpy', 'python-dateutil', 'pydantic',
        'requests', 'openpyxl', 'pytest', 'pystray', 'pillow',
    }
    names = []
    for line in _read('requirements.txt').splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        m = re.match(r'^([A-Za-z0-9_.-]+)', line)
        if m:
            names.append(m.group(1).lower())
    unknown = [n for n in names if n not in allowed]
    if unknown:
        return False, f'新增依赖未登记: {unknown}'
    return True, f'{len(names)} 个依赖全部在白名单'


@_check('R17')
def westock_node_guard():
    """零代码约束：westock npm CLI 依赖 Node 环境，必须有优雅降级（无 Node 跳过该层）"""
    src = _read('modules/data_collector.py')
    ok = "shutil.which('npx')" in src and "shutil.which('npm')" in src
    return ok, ('westock Node 可用性守卫存在' if ok else 'westock Node 守卫缺失')


@_check('R18')
def no_with_threadpoolexecutor():
    """M-1：严禁 with ThreadPoolExecutor 实现超时保护（shutdown(wait=True) 挂死）"""
    src = _read('modules/daily_report.py')
    bad = re.search(r'with\s+ThreadPoolExecutor', src)
    return bad is None, ('未发现违规模式' if bad is None else '发现 with ThreadPoolExecutor')


@_check('R19')
def timeout_config_default():
    """012：日报超时配置 STOCK=90s / BATCH=1800s"""
    import config

    ok = config.STOCK_TIMEOUT_SECONDS == 90 and config.BATCH_TIMEOUT_SECONDS == 1800
    return ok, ('STOCK=90/BATCH=1800' if ok else '超时配置被修改')


def run_all_checks():
    """执行全部红线检查，返回 [{rid, name, ok, detail}, ...]"""
    results = []
    for rid, fn in _CHECK_FUNCS:
        name = fn.__name__
        try:
            ok, detail = fn()
        except Exception as e:  # noqa: BLE001 —— 检查自身异常视为失败，不中断其余检查
            ok, detail = False, f'检查执行异常: {e!r}'
        results.append({'rid': rid, 'name': name, 'ok': ok, 'detail': detail})
    return results


def main():
    results = run_all_checks()
    failed = [r for r in results if not r['ok']]
    width = max(len(r['name']) for r in results) if results else 0
    for r in results:
        mark = 'PASS' if r['ok'] else 'FAIL'
        print(f'[{mark}] [{r["rid"]}] {r["name"]:<{width}} {r["detail"]}')
    print()
    print(f'红线核验完成: {len(results) - len(failed)}/{len(results)} 通过')
    if failed:
        print('违规项: ' + ', '.join(f'[{r["rid"]}] {r["name"]}' for r in failed))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
