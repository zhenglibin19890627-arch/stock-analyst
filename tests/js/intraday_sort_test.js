// 021BV 盘中速览卡 v2 —— 前端排序逻辑契约测试（node 直接执行，exit 0=通过）。
// 被测对象：static/js/portfolio.js 的 _intradaySortRows（纯函数，文件顶层仅
// 函数/变量声明，加载期不触 DOM/网络——node 全局桩后可整文件求值）。
// 运行：node tests/js/intraday_sort_test.js（pytest 包装见 tests/test_intraday_v2.py）
'use strict';
const fs = require('fs');
const path = require('path');

const srcPath = path.join(__dirname, '..', '..', 'static', 'js', 'portfolio.js');
const src = fs.readFileSync(srcPath, 'utf8');

// 最小运行桩：portfolio.js 顶层初始化只读 window.localStorage 并注册一次
// document.addEventListener 事件委托；其余 DOM/网络均在函数体内，加载期不执行。
global.window = { localStorage: { getItem: () => null, setItem: () => {} } };
global.document = {
    getElementById: () => null,
    addEventListener: () => {},
};
(0, eval)(src); // 间接 eval：函数声明进入全局，供下方按名取用

const sortRows = global._intradaySortRows;
if (typeof sortRows !== 'function') {
    console.error('FAIL: _intradaySortRows 未在 portfolio.js 中定义');
    process.exit(1);
}

let failures = 0;
function assertEq(actual, expected, msg) {
    const a = JSON.stringify(actual);
    const e = JSON.stringify(expected);
    if (a !== e) {
        console.error(`FAIL: ${msg} —— 期望 ${e}，实际 ${a}`);
        failures += 1;
    }
}

// 用例矩阵：破线最深 / 逼近 / 无盈亏(null) / 正常
const rows = [
    { symbol: '600000', name: 'A', state: 'normal',     distance_pct: 5.0,  unrealized_pnl_pct: 2.0,  pct_change: 1.0 },
    { symbol: '000001', name: 'B', state: 'below_stop', distance_pct: -2.0, unrealized_pnl_pct: -8.0, pct_change: -3.0 },
    { symbol: '300001', name: 'C', state: 'normal',     distance_pct: 0.5,  unrealized_pnl_pct: null, pct_change: null },
    { symbol: '000002', name: 'D', state: 'near_stop',  distance_pct: 0.9,  unrealized_pnl_pct: -1.0, pct_change: 2.0 },
];

// ① 距止损排序：升序（破线最深在前），null 殿后——0.5% 比 0.9% 更贴近线，排前
assertEq(sortRows(rows, 'stop').map(r => r.symbol),
    ['000001', '300001', '000002', '600000'], 'stop 排序');

// ② 当日盈亏排序：升序（亏损最大在前），null 殿后
assertEq(sortRows(rows, 'pnl').map(r => r.symbol),
    ['000001', '000002', '600000', '300001'], 'pnl 排序');

// ③ 涨跌幅排序：升序（跌幅最大在前），null 殿后
assertEq(sortRows(rows, 'pct').map(r => r.symbol),
    ['000001', '600000', '000002', '300001'], 'pct 排序');

// ④ 纯函数：不改入参顺序
assertEq(rows.map(r => r.symbol), ['600000', '000001', '300001', '000002'], '入参不被修改');

// ⑤ 双 null 同级：按严重度（触线>逼近>其余）再按代码
const ties = [
    { symbol: '600000', state: 'normal',     distance_pct: null, unrealized_pnl_pct: null, pct_change: null },
    { symbol: '000002', state: 'near_stop',  distance_pct: null, unrealized_pnl_pct: null, pct_change: null },
    { symbol: '000001', state: 'below_stop', distance_pct: null, unrealized_pnl_pct: null, pct_change: null },
];
assertEq(sortRows(ties, 'pct').map(r => r.symbol),
    ['000001', '000002', '600000'], 'null 同级按严重度');

// ⑥ 缺省键：跟随模块记忆（localStorage 桩返回 null → 默认 'stop'）
assertEq(sortRows(rows).map(r => r.symbol),
    ['000001', '300001', '000002', '600000'], '缺省键默认 stop');

// ⑦ 排序记忆：intradaySetSort 持久化到 localStorage（状态记忆契约）
const stored = [];
global.window.localStorage.setItem = (k, v) => { stored.push([k, v]); };
if (typeof global.intradaySetSort === 'function') {
    global.intradaySetSort('pnl');
    assertEq(stored, [['intradaySort', 'pnl']], '排序记忆持久化');
    global.intradaySetSort('stop');
    assertEq(stored[1], ['intradaySort', 'stop'], '排序记忆二次切换');
} else {
    console.error('FAIL: intradaySetSort 未定义');
    failures += 1;
}

// ⑧ 速览卡新增文案禁裸 '<'：扫描各速览函数源内的字符串字面量（转义感知），
// 含中文的字面量剥除完整 HTML 标签后不得残留 '<'——文案若写「现价<X」这类
// 裸小于号会被拦下；'<span' 等标签起始不受影响，比较运算符在代码不在字面量。
function cjkLiteralsWithLt(fnSrc) {
    const bad = [];
    let i = 0;
    while (i < fnSrc.length) {
        const c = fnSrc[i];
        if (c === "'") {
            let j = i + 1, buf = '';
            while (j < fnSrc.length && fnSrc[j] !== "'") {
                if (fnSrc[j] === '\\') { buf += fnSrc[j + 1]; j += 2; continue; }
                buf += fnSrc[j]; j += 1;
            }
            const text = buf.replace(/<[^>]*>/g, ''); // 剥完整标签
            if (/[\u4e00-\u9fff]/.test(text) && text.includes('<')) bad.push(buf);
            i = j + 1; continue;
        }
        i += 1;
    }
    return bad;
}
const offenders = [];
for (const name of Object.getOwnPropertyNames(global)) {
    try {
        const v = global[name];
        if (typeof v === 'function' && /^(_intraday|intraday|renderIntradayCard)/.test(name)) {
            const bad = cjkLiteralsWithLt(String(v));
            if (bad.length) offenders.push(name + ': ' + bad.join(' | '));
        }
    } catch (e) { /* 忽略不可枚举项 */ }
}
assertEq(offenders, [], '盘中速览函数中文字面量无裸 <');

if (failures > 0) {
    console.error(`intraday sort tests: ${failures} failed`);
    process.exit(1);
}
console.log('intraday sort tests passed');
