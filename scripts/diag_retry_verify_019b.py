"""019B 验证恢复方案：用 _http_get_em 增加重试（max_retries=3）采集贵州茅台 push2his。
验证"增加重试即可恢复"假设。只读（不写库）。"""
import sys
sys.path.insert(0, r'c:\Users\zlb19\Desktop\Qoder cn\stock_analyst')
from modules.data_collector import _http_get_em

url = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
params = {
    'lmt': '0', 'klt': '101', 'secid': '1.600519',
    'fields1': 'f1,f2,f3,f7', 'fields2': 'f51,f52,f53,f54,f55,f56',
    'ut': 'b2884a393a59ad64002292a3e90d46a5',
}

for mr in (1, 3, 5):
    try:
        resp = _http_get_em(url, params=params, max_retries=mr)
        data = resp.json()
        klines = (data.get('data') or {}).get('klines') or []
        print(f'[max_retries={mr}] 成功 klines={len(klines)} 最新={klines[-1][:40] if klines else "N/A"}')
        break
    except Exception as e:
        print(f'[max_retries={mr}] 失败: {str(e)[:100]}')