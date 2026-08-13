"""
019Y 立项前探针：mootdx + baostock 接口可用性实测
只读探针，不 import 任何项目模块，不动生产代码
"""
import sys
import time
import traceback


def section(title):
    print("\n" + "=" * 60)
    print(f"  {title}")
    print("=" * 60)

section("PROBE START")
print("Time:", time.strftime('%Y-%m-%d %H:%M:%S'))
print("Python:", sys.version.split()[0])

# ============================================================
# Part 1: mootdx
# ============================================================
section("Part 1: mootdx")

# 1a import
try:
    import mootdx
    print(f"[OK] mootdx import, version={mootdx.__version__}")
except Exception as e:
    print(f"[FAIL] mootdx import: {e}")
    traceback.print_exc()
    mootdx = None

if mootdx:
    # 1b Quotes init (bestip picks fastest server)
    client = None
    try:
        from mootdx.quotes import Quotes
        t0 = time.time()
        client = Quotes.factory(market='std', bestip=True, timeout=15)
        t1 = time.time()
        print(f"[OK] Quotes.factory init (bestip=True) cost={t1-t0:.2f}s")
    except Exception as e:
        print(f"[FAIL] Quotes.factory init: {e}")
        traceback.print_exc()

    if client:
        # 1c realtime quote - 000001
        try:
            t0 = time.time()
            df = client.quotes(symbol='000001')
            t1 = time.time()
            if df is not None and len(df) > 0:
                print(f"[OK] quotes(000001) cost={t1-t0:.2f}s rows={len(df)}")
                print(f"     columns: {list(df.columns)}")
                row = df.iloc[0]
                for c in df.columns:
                    print(f"     {c} = {row[c]}")
            else:
                print("[FAIL] quotes(000001) returned empty")
        except Exception as e:
            print(f"[FAIL] quotes(000001): {e}")
            traceback.print_exc()

        # 1d daily K-line - 000001, frequency=9
        try:
            t0 = time.time()
            bars = client.bars(symbol='000001', frequency=9, offset=10)
            t1 = time.time()
            if bars is not None and len(bars) > 0:
                print(f"[OK] bars(000001,freq=9) cost={t1-t0:.2f}s rows={len(bars)}")
                print(f"     columns: {list(bars.columns)}")
                print(f"     last 3 rows:\n{bars.tail(3).to_string()}")
            else:
                print("[FAIL] bars(000001) returned empty")
        except Exception as e:
            print(f"[FAIL] bars(000001): {e}")
            traceback.print_exc()

        # 1e index K-line - 000001 (Shanghai Index)
        try:
            t0 = time.time()
            idx = client.index(symbol='000001', frequency=9)
            t1 = time.time()
            if idx is not None and len(idx) > 0:
                print(f"[OK] index(000001,freq=9) cost={t1-t0:.2f}s rows={len(idx)}")
                print(f"     columns: {list(idx.columns)}")
                print(f"     last 2 rows:\n{idx.tail(2).to_string()}")
            else:
                print("[FAIL] index(000001) returned empty")
        except Exception as e:
            print(f"[FAIL] index(000001): {e}")
            traceback.print_exc()

        # 1f realtime quote - 600276 (Hengrui Medicine)
        try:
            t0 = time.time()
            df2 = client.quotes(symbol='600276')
            t1 = time.time()
            if df2 is not None and len(df2) > 0:
                print(f"[OK] quotes(600276) cost={t1-t0:.2f}s rows={len(df2)}")
                row = df2.iloc[0]
                for c in ['code', 'price', 'open', 'high', 'low', 'volume', 'amount']:
                    if c in df2.columns:
                        print(f"     {c} = {row[c]}")
            else:
                print("[FAIL] quotes(600276) returned empty")
        except Exception as e:
            print(f"[FAIL] quotes(600276): {e}")
            traceback.print_exc()

        # 1g minute data - 000001
        try:
            t0 = time.time()
            m = client.minute(symbol='000001')
            t1 = time.time()
            if m is not None and len(m) > 0:
                print(f"[OK] minute(000001) cost={t1-t0:.2f}s rows={len(m)}")
                print(f"     columns: {list(m.columns)}")
                print(f"     last 2 rows:\n{m.tail(2).to_string()}")
            else:
                print("[FAIL] minute(000001) returned empty")
        except Exception as e:
            print(f"[FAIL] minute(000001): {e}")
            traceback.print_exc()

# ============================================================
# Part 2: baostock
# ============================================================
section("Part 2: baostock")

import pandas as pd

try:
    import baostock as bs

    # login
    t0 = time.time()
    lg = bs.login()
    t1 = time.time()
    ok = lg.error_code == '0'
    print(f"[{'OK' if ok else 'FAIL'}] baostock login cost={t1-t0:.2f}s code={lg.error_code} msg={lg.error_msg}")

    if ok:
        # 2a daily K-line
        try:
            t0 = time.time()
            rs = bs.query_history_k_data_plus(
                "sz.000001",
                "date,code,open,high,low,close,volume,amount,turn,pctChg",
                start_date='2026-07-01', end_date='2026-08-11',
                frequency="d", adjustflag="3"
            )
            data_list = []
            while (rs.error_code == '0') & rs.next():
                data_list.append(rs.get_row_data())
            t1 = time.time()
            if len(data_list) > 0:
                result = pd.DataFrame(data_list, columns=rs.fields)
                print(f"[OK] daily K-line cost={t1-t0:.2f}s rows={len(result)}")
                print(f"     columns: {list(result.columns)}")
                print(f"     last 3:\n{result.tail(3).to_string()}")
            else:
                print(f"[FAIL] daily K-line empty, error={rs.error_msg}")
        except Exception as e:
            print(f"[FAIL] daily K-line: {e}")
            traceback.print_exc()

        # 2b profit data (financial)
        try:
            t0 = time.time()
            rs2 = bs.query_profit_data(code="sz.000001", year=2025, quarter=1)
            fin_list = []
            while (rs2.error_code == '0') & rs2.next():
                fin_list.append(rs2.get_row_data())
            t1 = time.time()
            if len(fin_list) > 0:
                result2 = pd.DataFrame(fin_list, columns=rs2.fields)
                print(f"[OK] profit data cost={t1-t0:.2f}s rows={len(result2)}")
                print(f"     columns: {list(result2.columns)}")
                print(result2.to_string())
            else:
                print(f"[FAIL] profit data empty, error={rs2.error_msg}")
        except Exception as e:
            print(f"[FAIL] profit data: {e}")
            traceback.print_exc()

        # 2c valuation (PE/PB/PS)
        try:
            t0 = time.time()
            rs3 = bs.query_history_k_data_plus(
                "sz.000001",
                "date,code,peTTM,pbMRQ,psTTM,pcfNcfTTM",
                start_date='2026-08-01', end_date='2026-08-11',
                frequency="d"
            )
            val_list = []
            while (rs3.error_code == '0') & rs3.next():
                val_list.append(rs3.get_row_data())
            t1 = time.time()
            if len(val_list) > 0:
                result3 = pd.DataFrame(val_list, columns=rs3.fields)
                print(f"[OK] valuation(PE/PB/PS) cost={t1-t0:.2f}s rows={len(result3)}")
                print(result3.tail(3).to_string())
            else:
                print(f"[FAIL] valuation empty, error={rs3.error_msg}")
        except Exception as e:
            print(f"[FAIL] valuation: {e}")
            traceback.print_exc()

    bs.logout()
    print("[OK] baostock logout")
except Exception as e:
    print(f"[FAIL] baostock test: {e}")
    traceback.print_exc()

# ============================================================
# Part 3: compatibility notes
# ============================================================
section("Part 3: compatibility analysis")
print("[INFO] mootdx core (Quotes/bars/index) uses tdxpy TCP socket protocol")
print("       -> does NOT go through requests/httpx, immune to project's")
print("          requests.Session.request global patch")
print("[INFO] baostock uses its own TCP socket protocol")
print("       -> does NOT go through requests/httpx either")
print("[INFO] httpx downgrade risk: mootdx wants httpx<0.26 but project env")
print("       needs httpx>=0.28. mootdx core socket functions unaffected.")

section("PROBE DONE")
