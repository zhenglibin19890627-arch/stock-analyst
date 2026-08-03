"""B26：两融历史数据回填脚本

调用 modules.data_collector.fetch_margin_balance 对所有 A 股自选股
回填融资余额历史数据，提升资金面完整度（目标 ≥80%）。

幂等：UPDATE/INSERT OR IGNORE，可重复运行。
"""

import io
import os
import sys

# Windows PowerShell 中文输出兜底
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')

# 设置项目根目录到 sys.path（脚本在 scripts/ 下，需引入上级 modules）
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, project_root)
os.chdir(project_root)

from database.db_manager import get_connection
from modules.data_collector import fetch_margin_balance

# 读取所有 A 股自选股
conn = get_connection()
cursor = conn.cursor()
cursor.execute("SELECT symbol, name FROM stocks WHERE market = 'a_stock'")
stocks = cursor.fetchall()
conn.close()

print('===== B26 两融历史数据回填 =====')
print(f'待回填股票数: {len(stocks)}')

success = 0
skipped = 0
failed = 0
for i, (symbol, name) in enumerate(stocks, 1):
    print(f'[{i}/{len(stocks)}] {symbol} {name} ...', end=' ')
    try:
        status, msg = fetch_margin_balance(symbol, 'a_stock')
        print(f'{status}: {msg}')
        if status == 'success':
            success += 1
        elif status == 'skipped':
            skipped += 1
        else:
            failed += 1
    except Exception as e:
        print(f'异常: {e}')
        failed += 1

# 验证填充率
conn = get_connection()
cursor = conn.cursor()
cursor.execute(
    'SELECT COUNT(*) AS total, '
    'SUM(CASE WHEN margin_balance IS NOT NULL THEN 1 ELSE 0 END) AS filled '
    'FROM raw_capital_flow'
)
row = cursor.fetchone()
conn.close()
total = row[0]
filled = row[1] or 0
pct = round(filled / total * 100, 1) if total else 0

print('\n===== 回填结果 =====')
print(f'成功: {success}  跳过: {skipped}  失败: {failed}')
print('\n===== margin_balance 填充率 =====')
print(f'{filled}/{total} 行非空 ({pct}%)')
if pct >= 80:
    print('✅ 达标（≥80%）')
else:
    print(f'⚠️ 未达标（{pct}% < 80%），请检查失败原因')
