"""019B 验证：调用现有 fetch_capital_flow 采集贵州茅台，验证当前retry策略下的表现。
只读核心逻辑：调用采集函数（会写库，属正常采集行为）。"""
import sys
import time

sys.path.insert(0, r'c:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
from modules.data_collector import fetch_capital_flow

results = []
for i in range(4):
    try:
        status, msg = fetch_capital_flow('600519', 'a_stock')
        results.append(f'第{i+1}次: status={status} | {msg[:80]}')
        if status == 'success':
            break
    except Exception as e:
        results.append(f'第{i+1}次: 异常 {type(e).__name__}: {str(e)[:80]}')
    time.sleep(3)

print('=== 贵州茅台 fetch_capital_flow 结果 ===')
for r in results:
    print(r)
