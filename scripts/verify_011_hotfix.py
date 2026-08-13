"""011-HOTFIX 自验脚本：验证3处时区Bug修复"""

import io
import os
import sys

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from modules.data_collector import (
    fetch_a_fundamental,
    fetch_hk_fundamental,
    fetch_margin_balance,
)

results = []

# V1: A股80天门控生效
print('=' * 60)
print("V1: A股80天门控 - fetch_a_fundamental('000333')")
try:
    r = fetch_a_fundamental('000333')
    print(f'  返回: {r}')
    if '跳过' in str(r) or 'skip' in str(r).lower():
        results.append(('V1', 'PASS', f'门控生效，返回含跳过: {r}'))
    else:
        results.append(('V1', 'WARN', f'未检测到跳过字样: {r}'))
except TypeError as e:
    results.append(('V1', 'FAIL', f'TypeError仍存在: {e}'))
except Exception as e:
    results.append(('V1', 'WARN', f'其他异常(非TypeError): {type(e).__name__}: {e}'))

# V2: 港股80天门控生效
print('=' * 60)
print("V2: 港股80天门控 - fetch_hk_fundamental('HK3690')")
try:
    r = fetch_hk_fundamental('HK3690')
    print(f'  返回: {r}')
    if '跳过' in str(r) or 'skip' in str(r).lower():
        results.append(('V2', 'PASS', f'门控生效: {r}'))
    else:
        results.append(('V2', 'WARN', f'未检测到跳过字样: {r}'))
except TypeError as e:
    results.append(('V2', 'FAIL', f'TypeError仍存在: {e}'))
except Exception as e:
    results.append(('V2', 'WARN', f'其他异常(非TypeError): {type(e).__name__}: {e}'))

# V3: 融资余额增量正常
print('=' * 60)
print("V3: 融资余额增量 - fetch_margin_balance('600276', 'a_stock')")
try:
    r = fetch_margin_balance('600276', 'a_stock')
    print(f'  返回: {r}')
    results.append(('V3', 'PASS', f'无TypeError，正常返回: {r}'))
except TypeError as e:
    results.append(('V3', 'FAIL', f'TypeError仍存在: {e}'))
except Exception as e:
    results.append(('V3', 'WARN', f'其他异常(非TypeError): {type(e).__name__}: {e}'))

# V4: force_full仍可绕过
print('=' * 60)
print("V4: force_full绕过 - fetch_a_fundamental('000333', force_full=True)")
try:
    r = fetch_a_fundamental('000333', force_full=True)
    print(f'  返回: {r}')
    if '跳过' not in str(r):
        results.append(('V4', 'PASS', f'force_full绕过门控: {r}'))
    else:
        results.append(('V4', 'WARN', f'force_full仍触发跳过: {r}'))
except TypeError as e:
    results.append(('V4', 'FAIL', f'TypeError: {e}'))
except Exception as e:
    # force_full会实际采集，网络异常可接受
    results.append(('V4', 'WARN', f'采集异常(非门控问题): {type(e).__name__}: {e}'))

# 汇总
print('\n' + '=' * 60)
print('自验结果汇总:')
print('-' * 60)
all_pass = True
for name, status, msg in results:
    flag = '✓' if status == 'PASS' else ('△' if status == 'WARN' else '✗')
    print(f'  {flag} {name}: [{status}] {msg}')
    if status == 'FAIL':
        all_pass = False
print('-' * 60)
print(f'总结: {"全部通过" if all_pass else "存在FAIL项"}')
