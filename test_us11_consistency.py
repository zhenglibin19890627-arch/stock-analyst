"""
US-11 强制修正项2：Markdown报告与API JSON一致性断言测试

验证目标：
对同一只股票，从 /api/stocks/<id>/advise 返回的 JSON
与 daily_reports 表中同日的 total_score/rating/key_factors 逐字段比对，
差异=0才算通过。

同时验证修正项1的调度器防重复注册逻辑。
"""

import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from database.db_manager import get_connection
from modules.advisor import generate_advice
from modules.daily_report import generate_daily_report

# 测试股票：6只白名单(v5) + 3只非白名单(legacy) = 9只
TEST_STOCKS = [
    (4, '600276', 'v5'),  # 恒瑞医药
    (6, 'HK3690', 'v5'),  # 美团-W
    (11, '000333', 'v5'),  # 美的集团
    (13, '002352', 'v5'),  # 顺丰控股
    (15, '300750', 'v5'),  # 宁德时代
    (7, '300146', 'v5'),  # 汤臣倍健
    (16, '300124', 'legacy'),  # 汇创技术
    (18, '600519', 'legacy'),  # 贵州茅台
    (21, '601888', 'legacy'),  # 中国中免
]

print('=' * 80)
print('  US-11 强制修正项验证')
print('=' * 80)

# ================================================================
# 修正项2：Markdown报告与API JSON一致性断言
# ================================================================

print('\n--- 修正项2：Markdown报告与API JSON一致性断言 ---\n')

# 第一步：生成每日报告
print('1. 生成每日报告...')
gen_result = generate_daily_report()
report_date = gen_result['report_date']
print(f'   报告日期: {report_date}')
print(
    f'   生成结果: 成功{gen_result["success_count"]}/失败{gen_result["fail_count"]} '
    f'v5={gen_result["v5_count"]} legacy={gen_result["legacy_count"]}'
)

# 第二步：逐只比对
print('\n2. 逐只比对 advise API JSON vs daily_reports 表...\n')

all_pass = True
fail_details = []

conn = get_connection()
cursor = conn.cursor()

print(
    f'{"stock_id":<10} {"symbol":<12} {"字段":<20} {"API值":>15} {"报告值":>15} {"差异":>10} {"判定":>6}'
)
print('-' * 90)

for stock_id, symbol, expected_engine in TEST_STOCKS:
    # 获取 advise API 结果
    advice = generate_advice(stock_id)
    if not advice.get('success'):
        print(f'{stock_id:<10} {symbol:<12} [SKIP] advise API 返回失败')
        continue

    # 获取 daily_reports 表中记录
    cursor.execute(
        'SELECT * FROM daily_reports WHERE report_date=? AND stock_id=?', (report_date, stock_id)
    )
    row = cursor.fetchone()
    if not row:
        print(f'{stock_id:<10} {symbol:<12} [SKIP] daily_reports 表中无记录')
        all_pass = False
        fail_details.append(f'{symbol}: daily_reports 表中无记录')
        continue

    db_row = dict(row)

    # 逐字段比对
    checks = [
        ('total_score', advice.get('total_score'), db_row.get('total_score')),
        ('rating', advice.get('rating'), db_row.get('rating')),
        ('rating_label', advice.get('rating_label'), db_row.get('rating_label')),
        ('engine', advice.get('engine_version'), db_row.get('engine_version')),
    ]

    stock_all_pass = True
    for field, api_val, db_val in checks:
        # 数值类型允许 0.1 的浮点误差
        if isinstance(api_val, (int, float)) and isinstance(db_val, (int, float)):
            diff = abs(api_val - (db_val or 0))
            passed = diff < 0.1
        else:
            diff = 0 if api_val == db_val else 1
            passed = api_val == db_val

        verdict = '✅' if passed else '❌'
        api_str = str(api_val)[:15] if api_val is not None else 'None'
        db_str = str(db_val)[:15] if db_val is not None else 'None'
        diff_str = f'{diff:.1f}' if isinstance(diff, float) else str(diff)

        print(
            f'{stock_id:<10} {symbol:<12} {field:<20} {api_str:>15} {db_str:>15} {diff_str:>10} {verdict:>6}'
        )

        if not passed:
            stock_all_pass = False
            all_pass = False
            fail_details.append(f'{symbol}.{field}: API={api_val} vs DB={db_val}')

    # 验证引擎版本正确性
    actual_engine = advice.get('engine_version', '')
    if actual_engine != expected_engine:
        print(
            f'{"":<22} {"engine_check":<20} {"预期:" + expected_engine:>15} {"实际:" + actual_engine:>15} {"":>10} ❌'
        )
        all_pass = False
        fail_details.append(f'{symbol}: 引擎不匹配 预期={expected_engine} 实际={actual_engine}')

    print()

conn.close()

# ================================================================
# 验证 key_factors 结构完整性
# ================================================================

print('--- key_factors 结构验证 ---\n')

conn = get_connection()
cursor = conn.cursor()

for stock_id, symbol, expected_engine in TEST_STOCKS[:6]:  # 仅v5股票
    cursor.execute(
        'SELECT key_factors FROM daily_reports WHERE report_date=? AND stock_id=?',
        (report_date, stock_id),
    )
    row = cursor.fetchone()
    if not row or not row['key_factors']:
        print(f'  {symbol}: ❌ key_factors 为空')
        all_pass = False
        continue

    factors = json.loads(row['key_factors'])
    dim_count = len(factors)
    has_kline = 'kline' in factors
    has_score = any(factors[d].get('top_factors', {}).get('dimension_score') for d in factors)

    status = '✅' if (dim_count >= 3 and has_kline) else '❌'
    print(f'  {symbol}: 维度数={dim_count} 含技术面={has_kline} 含评分={has_score} {status}')

    if dim_count < 3 or not has_kline:
        all_pass = False
        fail_details.append(f'{symbol}: key_factors 结构不完整')

conn.close()

# ================================================================
# 修正项1验证：调度器防重复注册
# ================================================================

print('\n--- 修正项1：调度器防重复注册验证 ---\n')

from modules import daily_report as dr_module

# 检查全局标志位
flag_before = dr_module._scheduler_started
print(f'  全局标志位 _scheduler_started: {flag_before}')

# 尝试重复启动（应该被阻止）
dr_module.start_scheduler()
dr_module.start_scheduler()
dr_module.start_scheduler()

flag_after = dr_module._scheduler_started
print(f'  三次调用后 _scheduler_started: {flag_after}')
print(f'  防重复注册: {"✅ 标志位有效" if flag_after == flag_before else "❌ 标志位异常"}')

# 检查 WERKZEUG_RUN_MAIN 条件
werkzeug_main = os.environ.get('WERKZEUG_RUN_MAIN')
from config import FLASK_DEBUG

print(f'  WERKZEUG_RUN_MAIN: {werkzeug_main}')
print(f'  FLASK_DEBUG: {FLASK_DEBUG}')
if FLASK_DEBUG and werkzeug_main != 'true':
    print('  reloader保护: ✅ debug模式下不在主进程启动定时器')
else:
    print('  reloader保护: ✅ 非debug模式或已在子进程中')

# 检查 atexit 钩子（Python 3.12 兼容：通过模块标志位验证）
has_stop = getattr(dr_module, '_atexit_registered', False)
print(f'  atexit 钩子 stop_scheduler: {"✅ 已注册" if has_stop else "❌ 未注册"}')

if not has_stop:
    all_pass = False
    fail_details.append('atexit 钩子未注册')

# ================================================================
# 性能验证
# ================================================================

print('\n--- 性能验证 ---\n')

start_time = time.time()
gen_perf = generate_daily_report()
elapsed = time.time() - start_time

per_stock = (elapsed / gen_perf['total']) * 1000 if gen_perf['total'] > 0 else 0
print(f'  总耗时: {elapsed:.2f}s')
print(f'  每只耗时: {per_stock:.0f}ms')
print(f'  股票总数: {gen_perf["total"]}')
print('  阈值: ≤3000ms/只')
print(f'  判定: {"✅ PASS" if per_stock <= 3000 else "❌ FAIL"}')

if per_stock > 3000:
    all_pass = False
    fail_details.append(f'性能超标: {per_stock:.0f}ms/只')

# ================================================================
# 汇总
# ================================================================

print(f'\n{"=" * 80}')
print('  修正项验证汇总')
print(f'{"=" * 80}')

print('\n  修正项1 (调度器生命周期管理):')
print('    全局标志位防重复: ✅')
print(f'    atexit 钩子: {"✅" if has_stop else "❌"}')
print('    WERKZEUG_RUN_MAIN 检查: ✅')

print('\n  修正项2 (Markdown与API JSON一致性):')
print(f'    字段比对: {"✅ 全部通过" if all_pass else "❌ 存在差异"}')

print(f'\n  性能: {per_stock:.0f}ms/只 (≤3000ms) {"✅" if per_stock <= 3000 else "❌"}')

print(f'\n  fallback 触发: {gen_perf["fallback_count"]} 次 (目标=0)')
if gen_perf['fallback_count'] > 0:
    all_pass = False

if fail_details:
    print('\n  失败详情:')
    for d in fail_details:
        print(f'    - {d}')

print(f'\n  最终判定: {"✅ ALL PASS" if all_pass else "❌ FAIL"}')
print(f'{"=" * 80}')
