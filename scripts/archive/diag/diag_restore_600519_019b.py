"""019B 恢复验证：周期性重试（15s间隔）调用现有 fetch_capital_flow 采集贵州茅台(600519)。
完全复用现有采集+写入逻辑，利用封禁窗口期取回数据并写库，满足验收标准3。
零代码约束：不新增pip依赖；仅周期性调用现有函数。"""
import sys
import time

sys.path.insert(0, r'c:\Users\zlb19\Desktop\Qoder cn\stock_analyst')

from modules.data_collector import fetch_capital_flow  # noqa: E402

SYMBOL = '600519'
MARKET = 'a_stock'
MAX_ATTEMPTS = 20
INTERVAL = 15  # 秒

for i in range(1, MAX_ATTEMPTS + 1):
    print(f'[恢复验证] 第{i}次调用 fetch_capital_flow({SYMBOL})...')
    try:
        status, msg = fetch_capital_flow(SYMBOL, MARKET)
        print(f'[恢复验证] 第{i}次 status={status} | {msg[:80]}')
        if status == 'success':
            print(f'[恢复验证] 成功！{SYMBOL} 东财数据已写入')
            sys.exit(0)
    except Exception as e:
        print(f'[恢复验证] 第{i}次异常: {type(e).__name__}: {str(e)[:80]}')
    if i < MAX_ATTEMPTS:
        print(f'[恢复验证] 等待 {INTERVAL}s 后重试...')
        time.sleep(INTERVAL)

print('[恢复验证] 全部尝试失败，未恢复')
sys.exit(1)