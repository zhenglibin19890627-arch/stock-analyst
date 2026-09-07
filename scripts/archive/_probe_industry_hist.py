"""临时探针：验证东财板块历史资金流接口可补 08-18 数据（用后即删）。"""

import os

os.environ['NO_PROXY'] = '*'
os.environ['no_proxy'] = '*'

import requests

url = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
params = {
    'lmt': '5',
    'klt': '101',
    'secid': '90.BK1201',
    'fields1': 'f1,f2,f3,f7',
    'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
    'ut': 'b2884a393a59ad64002292a3e90d46a5',
}
r = requests.get(url, params=params, headers={'User-Agent': 'Mozilla/5.0'}, timeout=10)
print('HTTP', r.status_code)
data = r.json().get('data') or {}
klines = data.get('klines') or []
print(f"板块: {data.get('name')}  返回{len(klines)}行")
for k in klines:
    print(' ', k)
