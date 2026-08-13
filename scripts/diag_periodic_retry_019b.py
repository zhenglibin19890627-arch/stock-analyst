"""019B 恢复验证：周期性重试（15s间隔）采集贵州茅台，验证"等待窗口期即可恢复"。
只读（不写库），仅验证数据可取回。"""
import sys
import time
import requests

s = requests.Session()
s.trust_env = False
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
URL = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
params = {'lmt': '0', 'klt': '101', 'secid': '1.600519',
          'fields1': 'f1,f2,f3,f7', 'fields2': 'f51,f52,f53,f54,f55,f56',
          'ut': 'b2884a393a59ad64002292a3e90d46a5'}
headers = {'User-Agent': UA, 'Accept': 'application/json, text/plain, */*',
           'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8', 'Referer': 'https://quote.eastmoney.com/'}

for i in range(20):
    try:
        resp = s.get(URL, params=params, headers=headers, timeout=(5, 10),
                     proxies={'http': None, 'https': None})
        data = resp.json()
        klines = (data.get('data') or {}).get('klines') or []
        if klines:
            print(f'[周期重试] 第{i+1}次成功 klines={len(klines)}')
            print('最新:', klines[-1])
            break
        print(f'[周期重试] 第{i+1}次 空数据 rc={data.get("rc")}')
    except Exception as e:
        print(f'[周期重试] 第{i+1}次失败: {type(e).__name__}')
    time.sleep(15)