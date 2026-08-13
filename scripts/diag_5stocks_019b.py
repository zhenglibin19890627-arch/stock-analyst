"""019B 诊断：对5只股票各重试多次，确认间歇性封禁模式。只读。"""
import sys
import time
import requests

s = requests.Session()
s.trust_env = False
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
URL = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
headers = {'User-Agent': UA, 'Accept': 'application/json, text/plain, */*',
           'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8', 'Referer': 'https://quote.eastmoney.com/'}

stocks = [
    ('300750', '宁德时代', '0.300750'),
    ('600519', '贵州茅台', '1.600519'),
    ('000333', '美的集团', '0.000333'),
    ('002415', '海康威视', '0.002415'),
    ('601888', '中国中免', '1.601888'),
]

for code, name, secid in stocks:
    params = {'lmt': '0', 'klt': '101', 'secid': secid,
              'fields1': 'f1,f2,f3,f7', 'fields2': 'f51,f52,f53,f54,f55,f56',
              'ut': 'b2884a393a59ad64002292a3e90d46a5'}
    ok = False
    for i in range(4):
        try:
            resp = s.get(URL, params=params, headers=headers, timeout=(5, 10),
                         proxies={'http': None, 'https': None})
            data = resp.json()
            klines = (data.get('data') or {}).get('klines') or []
            if klines:
                print(f'[{name}] 第{i+1}次成功 klines={len(klines)} 最新={klines[-1][:40]}')
                ok = True
                break
            else:
                print(f'[{name}] 第{i+1}次 空数据 rc={data.get("rc")}')
        except Exception as e:
            print(f'[{name}] 第{i+1}次失败: {type(e).__name__}: {str(e)[:60]}')
        time.sleep(2)
    if not ok:
        print(f'[{name}] **** 全部失败 ****')
    time.sleep(2)