#!/usr/bin/env python3
"""
019X 批次自测脚本：东财请求策略修复（T1 退避拉长 / T2 三窗错峰 / T3 放弃代理）

任务书：docs/tasks/dev_tasks_20260810_019X_em_request_strategy_fix.md
红线：
  1. 离线验证优先——退避时序/调度注册/开关断言全部 mock，不发真实请求；
  2. 限次真实探针——仅 probe 子命令发真实东财请求：≤15 次、串行、间隔 ≥30 秒、
     避开 16:00-17:00 采集窗口；
  3. 零写库——offline 全程不触碰 stock_analyst.db（fetch_capital_flow_batch 被 mock）。

用法（在项目根目录执行）：
  python scripts/selftest_019x_request_strategy.py offline   # 全部离线断言（默认）
  python scripts/selftest_019x_request_strategy.py probe     # 限次真实探针（6次，间隔≥30s）
  python scripts/selftest_019x_request_strategy.py timing    # 时序推算（退避×只数×600s软超时）
"""

import io
import logging as _logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone

sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import modules.daily_report as dr
import modules.data_collector as dc

# 压掉 INFO 噪音（T1 200次模拟会打大量 INFO），保留 WARNING 供 T2 跳过日志断言
_logging.getLogger('modules.data_collector').setLevel(_logging.WARNING)
_logging.getLogger('modules.daily_report').setLevel(_logging.WARNING)

_CN_TZ = timezone(timedelta(hours=8), name='Asia/Shanghai')
ALL_OK = True
FAILURES = []


def check(name, cond, detail=''):
    global ALL_OK
    tag = 'PASS' if cond else 'FAIL'
    if not cond:
        ALL_OK = False
        FAILURES.append(name)
    print(f'  [{tag}] {name} {detail}')


# ================================================================
# T1：退避时序离线模拟（mock sleep/uniform/Session，不发真实请求）
# ================================================================

def test_t1_backoff():
    print('\n=== T1 失败重试退避（离线模拟，不发真实请求） ===')
    sleeps = []

    def fake_sleep(sec):
        sleeps.append(float(sec))

    def fail_get(self, *a, **k):
        raise ConnectionError('RemoteDisconnected: 远端主动掐断（模拟 WAF 窗口）')

    orig_sleep, orig_uniform, orig_get = dc.time.sleep, dc._random.uniform, dc.requests.Session.get
    dc.time.sleep = fake_sleep
    dc.requests.Session.get = fail_get
    try:
        # 固定种子模拟一次全败路径
        import random as _r
        _r.seed(42)
        try:
            dc._http_get_em('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get',
                            params={'secid': '1.600519'})
            check('T1 全败应抛 ConnectionError', False, '未抛异常')
        except ConnectionError as e:
            check('T1 全败应抛 ConnectionError', True, f'err={str(e)[:60]}')
    finally:
        dc.time.sleep, dc._random.uniform, dc.requests.Session.get = orig_sleep, orig_uniform, orig_get

    # 分类：请求内延迟 U(1.5,3.5) 与轮间退避（≥25.5s 为退避）
    req_delays = [s for s in sleeps if s < 10]
    backoffs = [s for s in sleeps if s >= 25.5]
    print(f'  请求内延迟次数={len(req_delays)}（应为4）: {[f"{x:.2f}" for x in req_delays]}')
    print(f'  轮间退避序列={[f"{x:.2f}" for x in backoffs]}')
    check('T1 4轮=4次请求内延迟', len(req_delays) == 4, f'实际{len(req_delays)}')
    check('T1 3次轮间退避', len(backoffs) == 3, f'实际{len(backoffs)}')
    # 退避窗口校验：30±15%=[25.5,34.5]，60±15%=[51,69]
    expect = [(30, 25.5, 34.5), (60, 51, 69), (60, 51, 69)]
    seq_ok = True
    for i, (base, lo, hi) in enumerate(expect):
        b = backoffs[i]
        if not (lo <= b <= hi):
            seq_ok = False
            print(f'  第{i+1}个退避 {b:.2f}s 不在 [{lo},{hi}] 内')
    check('T1 退避序列 30/60/60 均±15%内', seq_ok)

    # 分布校验：跑 200 次采样（mock uniform 用真随机、sleep 只记录），验证每轮退避始终落窗内
    all_ok = True
    for _ in range(200):
        inner = []
        dc.time.sleep = lambda s: inner.append(float(s))
        dc.requests.Session.get = fail_get
        try:
            dc._http_get_em('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get')
        except ConnectionError:
            pass
        finally:
            dc.time.sleep = fake_sleep
            dc.requests.Session.get = fail_get
        b = [s for s in inner if s >= 25.5]
        if len(b) != 3:
            all_ok = False
            break
        for (base, lo, hi), v in zip(expect, b):
            if not (lo <= v <= hi):
                all_ok = False
    check('T1 200次采样退避全部落窗内', all_ok)

    # 轮数断言：rounds 默认 = _EM_RETRY_ROUNDS = 4（不依赖全局 MAX_RETRIES=3）
    check('T1 轮数=4（_EM_RETRY_ROUNDS）', dc._EM_RETRY_ROUNDS == 4, f'实际{dc._EM_RETRY_ROUNDS}')
    import config
    check('T1 全局 MAX_RETRIES 保持 3（未动新浪/腾讯@retry）', config.MAX_RETRIES == 3,
          f'实际{config.MAX_RETRIES}')
    # 单源最坏重试窗口：4×请求延迟(1.5~3.5) + (30+60+60)×(0.85~1.15)
    lo = 4 * 1.5 + 150 * 0.85
    hi = 4 * 3.5 + 150 * 1.15
    print(f'  T1 单源最坏重试窗口推算: [{lo:.1f}s, {hi:.1f}s]（约{lo/60:.1f}~{hi/60:.1f}分钟，'
          f'与 019W T2e「30秒间隔2分钟内捕获开放窗口」一致）')


# ================================================================
# T2：三窗调度注册离线模拟（冻结时钟 + mock Timer，不发真实请求）
# ================================================================

class _FrozenDT(datetime):
    _now = datetime(2026, 8, 10, 16, 10, 0, tzinfo=_CN_TZ)  # 周一 16:10

    @classmethod
    def now(cls, tz=None):
        return cls._now


class FakeTimer:
    instances = []

    def __init__(self, delay, fn, args=None):
        self.delay = delay
        self.fn = fn
        self.args = args if args is not None else ()
        self.daemon = False
        FakeTimer.instances.append(self)

    def start(self):
        pass

    def cancel(self):
        pass

    def is_alive(self):
        return False


def test_t2_scheduling():
    print('\n=== T2 三窗调度注册（离线模拟：冻结时钟 16:10 + mock Timer） ===')

    orig_timer, orig_dt, orig_get_all, orig_batch = (
        dr.threading.Timer, dr.datetime, dr._get_all_stocks, dr.fetch_capital_flow_batch
    )
    FakeTimer.instances = []
    dr.threading.Timer = FakeTimer
    dr.datetime = _FrozenDT

    stocks = [
        {'id': i + 1, 'symbol': s, 'name': f'股{s}', 'market': 'a_stock'}
        for i, s in enumerate(['600276', '000333', '000001', '600519', '002415',
                               '000651', '300750', '600036', '601318', '000002',
                               '600900', '002594', '601899'])
    ]
    dr._get_all_stocks = lambda: stocks
    collected = []
    dr.fetch_capital_flow_batch = lambda syms: collected.append(list(syms)) or {
        'success_count': len(syms), 'fail_count': 0, 'source': 'mock'}

    flow_order = []
    dr.generate_daily_report = lambda **k: flow_order.append('generate_daily_report') or {}
    import modules.alert_engine
    import modules.index_collector
    orig_scan, orig_refresh = modules.alert_engine.scan_once, modules.index_collector.refresh_all
    modules.alert_engine.scan_once = lambda: flow_order.append('scan_once')
    dr._schedule_capital_retry = lambda a: flow_order.append('schedule_capital_retry')
    modules.index_collector.refresh_all = lambda: flow_order.append('refresh_all')

    try:
        # --- 启动：_schedule_next 注册次日窗1（16:10） ---
        dr._schedule_next()
        check('T2 次日窗1延迟=86400s(明日16:10)', len(FakeTimer.instances) == 1
              and abs(FakeTimer.instances[0].delay - 86400) < 1,
              f'delay={FakeTimer.instances[0].delay if FakeTimer.instances else None}')
        check('T2 次日窗1参数=(0,)', FakeTimer.instances[0].args == (0,),
              f'args={FakeTimer.instances[0].args}')

        # --- 窗1（16:10）触发：注册窗2(16:40,1800s) + 采集第1/3 ---
        dr._scheduler_tick(0)
        check('T2 窗1触发后注册窗2=1800s(16:40)', len(FakeTimer.instances) == 2
              and abs(FakeTimer.instances[1].delay - 1800) < 1,
              f'delay={FakeTimer.instances[1].delay}')
        check('T2 窗2参数=(1,)', FakeTimer.instances[1].args == (1,))
        check('T2 窗1只采集第1份(前5只，代码排序)', collected and collected[0][:5] ==
              ['000001', '000002', '000333', '000651', '002415'],
              f'窗1清单={collected[0] if collected else None}')
        check('T2 窗1不生成报告', 'generate_daily_report' not in flow_order)

        # --- 窗2（16:40）触发：注册窗3(17:10) + 采集第2/3 ---
        dr._scheduler_tick(1)
        # 冻结时钟恒为 16:10 → 到 17:10 的延迟 = 3600s（真实运行中窗2在16:40触发则为1800s）
        check('T2 窗2触发后注册窗3=3600s(冻结16:10→17:10)', len(FakeTimer.instances) == 3
              and abs(FakeTimer.instances[2].delay - 3600) < 1,
              f'delay={FakeTimer.instances[2].delay}')
        check('T2 窗2参数=(2,)', FakeTimer.instances[2].args == (2,))
        check('T2 窗2采集第2份', collected[1][:5] ==
              ['002594', '300750', '600036', '600276', '600519'],
              f'窗2清单={collected[1]}')
        check('T2 窗2不生成报告', 'generate_daily_report' not in flow_order)

        # --- 窗3（17:10）触发：采集第3/3 + 完整日报流程 ---
        dr._scheduler_tick(2)
        check('T2 窗3采集第3份', collected[2] == ['600900', '601318', '601899'],
              f'窗3清单={collected[2]}')
        check('T2 窗3后日报流程挂载顺序一致', flow_order == [
            'generate_daily_report', 'scan_once', 'schedule_capital_retry', 'refresh_all'],
              f'实际顺序={flow_order}')
        # 窗3 finally → _schedule_next 注册次日窗1
        check('T2 窗3结束后注册次日16:10', len(FakeTimer.instances) == 4
              and FakeTimer.instances[3].args == (0,)
              and abs(FakeTimer.instances[3].delay - 86400) < 1,
              f'delay={FakeTimer.instances[3].delay}, args={FakeTimer.instances[3].args}')

        # --- 并发防护：前一窗未结束（锁被占）→ 跳过并记日志 ---
        class BusyLock:
            def acquire(self, timeout=0):
                return False

            def release(self):
                pass

        orig_lock = dr._generate_lock
        dr._generate_lock = BusyLock()
        collected_before = len(collected)
        import logging as _lg
        log_lines = []
        h = _lg.Handler()
        h.emit = lambda rec: log_lines.append(rec.getMessage())
        _lg.getLogger('modules.daily_report').addHandler(h)
        dr._scheduler_tick(1)  # 窗2 触发但锁忙 → 跳过采集
        check('T2 锁忙时后窗跳过采集', len(collected) == collected_before,
              f'仍采集={len(collected) - collected_before}次')
        check('T2 锁忙跳过有日志', any('获取生成锁超时' in x or '跳过本窗采集' in x for x in log_lines),
              f'日志样例={[x[:40] for x in log_lines if "跳过" in x]}')
        _lg.getLogger('modules.daily_report').removeHandler(h)
        dr._generate_lock = orig_lock
    finally:
        dr.threading.Timer, dr.datetime = orig_timer, orig_dt
        dr._get_all_stocks, dr.fetch_capital_flow_batch = orig_get_all, orig_batch
        modules.alert_engine.scan_once = orig_scan
        modules.index_collector.refresh_all = orig_refresh

    print(f'  窗1清单={collected[0]}')
    print(f'  窗2清单={collected[1]}')
    print(f'  窗3清单={collected[2]}')
    print('  三窗切分: 按代码排序固定切分（13只 → 5/5/3），可复现')


# ================================================================
# T3：EM_USE_PROXY 开关断言（mock 代理健康检查，不发真实请求）
# ================================================================

def test_t3_switch():
    print('\n=== T3 代理路径开关（离线断言，不发真实请求） ===')

    health_calls = {'is_available': 0, 'record_failure': 0, 'record_success': 0}

    class SpyHealth:
        def is_available(self):
            health_calls['is_available'] += 1
            return True

        def record_failure(self):
            health_calls['record_failure'] += 1

        def record_success(self):
            health_calls['record_success'] += 1

    spy = SpyHealth()
    orig_health, orig_getproxies, orig_sleep, orig_get = (
        dc._proxy_health, dc._urlreq.getproxies, dc.time.sleep, dc.requests.Session.get
    )
    dc._proxy_health = spy
    dc._urlreq.getproxies = lambda: {'http': 'http://127.0.0.1:7897',
                                     'https': 'http://127.0.0.1:7897'}
    dc.time.sleep = lambda s: None

    def fail_get(self, *a, **k):
        raise ConnectionError('RemoteDisconnected（模拟）')

    dc.requests.Session.get = fail_get
    try:
        # --- 开关 False：只走直连，零触碰代理健康检查 ---
        dc.EM_USE_PROXY = False
        health_calls['is_available'] = health_calls['record_failure'] = \
            health_calls['record_success'] = 0
        try:
            dc._http_get_em('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get',
                            max_retries=1)
        except ConnectionError:
            pass
        check('T3 False 时零触碰代理健康检查', health_calls == {'is_available': 0,
              'record_failure': 0, 'record_success': 0}, f'calls={health_calls}')

        # --- 开关 True：回退原逻辑（代理优先，触碰健康检查） ---
        dc.EM_USE_PROXY = True
        health_calls['is_available'] = health_calls['record_failure'] = \
            health_calls['record_success'] = 0
        try:
            dc._http_get_em('https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get',
                            max_retries=1)
        except ConnectionError:
            pass
        check('T3 True 时恢复原逻辑（代理优先+健康检查）',
              health_calls['is_available'] >= 1 and health_calls['record_failure'] >= 1,
              f'calls={health_calls}')
    finally:
        dc.EM_USE_PROXY = False
        dc._proxy_health, dc._urlreq.getproxies = orig_health, orig_getproxies
        dc.time.sleep, dc.requests.Session.get = orig_sleep, orig_get

    import config
    check('T3 config.EM_USE_PROXY 默认 False', config.EM_USE_PROXY is False)


# ================================================================
# 时序推算：退避拉长 × 批内只数 × 600s 软超时
# ================================================================

def test_timing_projection():
    print('\n=== 时序推算（退避拉长 × 批内只数 × 600s 软超时） ===')

    # 单只全败最坏耗时 = 4×(请求内延迟1.5~3.5) + (30+60+60)×(0.85~1.15)
    per_lo, per_hi = 4 * 1.5 + 150 * 0.85, 4 * 3.5 + 150 * 1.15
    per_typ = 4 * 2.5 + 150
    print(f'  单只全败耗时（_http_get_em 内）: 最坏[{per_lo:.1f}s, {per_hi:.1f}s]，典型{per_typ:.1f}s')
    # 批内逐只附加：错峰2~5s（_EM_INTER_DELAY_RANGE）、批间30~60s/5只、冷却60s×1次
    per_with_stagger = per_hi + 5.0
    cap = dc._EM_FALLBACK_TOTAL_CAP_SECONDS
    worst_processed = int(cap // per_with_stagger)
    print(f'  _EM_FALLBACK_TOTAL_CAP_SECONDS={cap}s（未放大）; 批内最坏每只约{per_with_stagger:.1f}s')
    print(f'  最坏情形下 600s 内最多处理 {worst_processed} 只（其余被软超时截断）')

    # 三窗对比：旧单批23只 vs 新三窗 8/8/7
    for label, n in [('旧单批 16:10 全量 23 只', 23),
                     ('新窗1/窗2/窗3 各 8/8/7 只', 8)]:
        truncated = max(0, n - worst_processed)
        print(f'  {label}: 最坏截断约 {truncated} 只')

    print('  截断兜底链（现状链路，未改动）: ① 窗3 日报流程内 fetch_capital_flow_batch(a_symbols)')
    print('    对全量清单重算补采清单（019E 补采清单机制）再试一轮 → ② 30分钟延迟补采（019Q M-5）')
    print('    → ③ 次日批次。退避拉长仅提高单窗口内截断概率，整体由三窗拆分布局缓解（三窗各')
    print('    有独立 600s 软超时，等价于总预算 ×3）。')
    print('  模拟验证（200次，全败最坏假设，8只/窗）:')

    import random as _r
    _r.seed(7)
    truncated_samples = []
    for _ in range(200):
        elapsed = 0.0
        done = 0
        for _i in range(8):
            if elapsed > cap:
                break
            stock_time = 4 * _r.uniform(1.5, 3.5) + (
                30 * _r.uniform(0.85, 1.15) + 60 * _r.uniform(0.85, 1.15) + 60 * _r.uniform(0.85, 1.15))
            if _i > 0:
                stock_time += _r.uniform(2.0, 5.0)
            elapsed += stock_time
            done += 1
        truncated_samples.append(8 - done)
    avg_trunc = sum(truncated_samples) / len(truncated_samples)
    worst_trunc = max(truncated_samples)
    print(f'  200次模拟（8只/窗、全败、最坏假设）: 截断均值 {avg_trunc:.1f} 只/窗，最坏 {worst_trunc} 只/窗')


# ================================================================
# 限次真实探针（probe 子命令）：≤15次、串行、间隔≥30s、避开16-17时
# ================================================================

def run_probe():
    print('\n=== 限次真实探针（直连路径，走生产 _http_get_em 且 max_retries=1） ===')
    now = datetime.now(_CN_TZ)
    if 16 <= now.hour < 17:
        print('  当前处于 16:00-17:00 采集窗口，跳过真实探针（红线）')
        return
    PROBE_BUDGET = 6
    INTERVAL = 30
    url = 'https://push2his.eastmoney.com/api/qt/stock/fflow/daykline/get'
    params = {
        'lmt': '0', 'klt': '101', 'secid': '1.600519',
        'fields1': 'f1,f2,f3,f7',
        'fields2': 'f51,f52,f53,f54,f55,f56,f57,f58,f59,f60,f61,f62,f63,f64,f65',
        'ut': 'b2884a393a59ad64002292a3e90d46a5',
    }
    print('  时间戳 | HTTP码 | 成败 | 错误摘要')
    ok = 0
    last = None
    for i in range(PROBE_BUDGET):
        if last is not None:
            wait = INTERVAL - (time.time() - last)
            if wait > 0:
                time.sleep(wait)
        last = time.time()
        ts = datetime.now().strftime('%H:%M:%S')
        status, err = '?', ''
        try:
            resp = dc._http_get_em(url, params=params, max_retries=1)
            status, ok = resp.status_code, ok + 1
            print(f'  {ts} | {status} | 成功 | len={len(resp.content)}')
        except Exception as e:
            err = str(e)[:80]
            print(f'  {ts} | 无 | 失败 | {err}')
    print(f'  探针汇总: {ok}/{PROBE_BUDGET} 成功（间隔≥{INTERVAL}s 串行，总计{PROBE_BUDGET}次≤15）')


# ================================================================
# 回归：模块导入
# ================================================================

def test_imports():
    print('\n=== 回归：模块导入 ===')
    import modules.daily_report as _dr
    import modules.data_collector as _dc

    check('回归 import modules.data_collector', _dc is not None)
    check('回归 import modules.daily_report', _dr is not None)
    check('回归 019Q _schedule_capital_retry 保留', hasattr(_dr, '_schedule_capital_retry'))
    check('回归 周日20:00 优化定时器保留', hasattr(_dr, '_schedule_optimizer_next'))
    check('回归 30分钟延迟补采保留', hasattr(_dr, '_capital_retry_once'))


if __name__ == '__main__':
    cmd = sys.argv[1] if len(sys.argv) > 1 else 'offline'
    print('#' * 72)
    print('# 019X 自测：东财请求策略修复（T1 退避 / T2 三窗 / T3 弃代理）')
    print('#' * 72)
    if cmd == 'probe':
        run_probe()
    elif cmd == 'timing':
        test_timing_projection()
    else:
        test_t1_backoff()
        test_t2_scheduling()
        test_t3_switch()
        test_timing_projection()
        test_imports()
        print(f'\n{"=" * 72}')
        print(f'  汇总: {"全部 PASS" if ALL_OK else "存在 FAIL: " + str(FAILURES)}')
        sys.exit(0 if ALL_OK else 1)
