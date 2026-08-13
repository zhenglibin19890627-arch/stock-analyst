"""019B 诊断脚本：直接测试东方财富 push2his 资金流向接口。
绕过项目内封装，直接 requests 请求，确认接口本身是否可用。
"""
import sys
import time
import random
import urllib.request as _urlreq

import requests

sys.path.insert(0, r'c:\Users\zlb19\Desktop\Qoder cn\stock_analyst')

stocks = [
    ('300750', '宁德时代', '0.300750'),
    ('600519', '贵州茅台', '1.600519'),
    ('000333', '美的集团', '0.000333'),
    ('002415', '海康威视', '0.002415'),
    ('601888', '中国中免', '1.601888'),
]

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'

URL = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
PARAMS_TPL = {
    'lmt': '0',
    'klt': '101',
    'fields1': 'f1,f2,f3,f7',
    'fields2': 'f51,f52,f53,f54,f55,f56',
    'ut': 'b2884a393a59ad64002292a3e90d46a5',
}

print('=== 系统代理 ===', _urlreq.getproxies())


def test(stock, secid, label_proxy):
    params = dict(PARAMS_TPL)
    params['secid'] = secid
    headers = {
        'User-Agent': UA,
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
        'Referer': 'https://quote.eastmoney.com/',
        'Host': 'push2his.eastmoney.com',
    }
    s = requests.Session()
    s.trust_env = False
    try:
        resp = s.get(URL, params=params, headers=headers, timeout=(5, 10),
                     proxies={'http': None, 'https': None})
        data = resp.json()
        klines = (data.get('data') or {}).get('klines') or []
        print(f'[{label_proxy}] {stock}({secid}) HTTP={resp.status_code} '
              f'rc={data.get("rc")} klines={len(klines)}')
        if klines:
            print(f'    最新: {klines[-1]}')
        return True
    except Exception as e:
        print(f'[{label_proxy}] {stock}({secid}) 失败: {type(e).__name__}: {str(e)[:120]}')
        return False


print('=== 直连测试 ===')
for code, name, secid in stocks:
    test(name, secid, 'direct')
    time.sleep(random.uniform(1.0, 1.5))