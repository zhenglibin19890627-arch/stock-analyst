#!/usr/bin/env node
/**
 * 021BY 市场扫描器前端逻辑冒烟检查（dev-only，不参与运行时；node 原生，零依赖）
 *
 * 覆盖（stub DOM/localStorage 后以 vm 加载 market.js，纯函数级断言）：
 * - t4/F-V3：预设行业条件在 fresh 会话（下拉无 option）挂起 → 扫描成功后重应用一次；
 *   手动改行业清除挂起；重复调用幂等
 * - t4/F-V4：行业筛选生效时说明行含「切换行业请重新扫描」提示，未筛选时不含
 * - 回归：t2 C7 载荷复数数组键 / 三态排序 None 殿后 / C1 位置筛选与低位优先 /
 *   C5 CSV 转义 / C4 预设保存-应用-删除闭环
 *
 * 用法：node scripts/check_market_js_021by.js [market.js 路径]（缺省 static/js/market.js）
 * 退出码：0=全过，1=有失败。
 */
'use strict';
const fs = require('fs');
const vm = require('vm');
const path = process.argv[2] || 'static/js/market.js';

function mkEl(opts) {
    opts = opts || {};
    return {
        value: opts.value != null ? opts.value : '',
        checked: !!opts.checked,
        options: opts.options || [],
        innerHTML: '',
        textContent: '',
        style: {},
        childElementCount: 0,
        _handlers: {},
        appendChild: function (c) { if (c && c.value != null) this.options.push(c); },
        addEventListener: function (type, fn) { this._handlers[type] = fn; },
        click: function () {},
    };
}

const elements = {};
function el(id, opts) {
    if (!elements[id]) elements[id] = mkEl(opts);
    if (opts) Object.assign(elements[id], opts);
    return elements[id];
}

const store = {};
const sandbox = {
    console, Date, isNaN, parseInt, parseFloat, JSON, Object, Array, String, Number, Math, RegExp,
    URL: { createObjectURL: function () { return 'blob:x'; } },
    Blob: function (parts) { this.parts = parts; },
    alert: function () {},
    confirm: function () { return true; },
    prompt: function () { return sandbox._nextPrompt != null ? sandbox._nextPrompt : null; },
    localStorage: {
        getItem: function (k) { return store[k] != null ? store[k] : null; },
        setItem: function (k, v) { store[k] = String(v); },
        removeItem: function (k) { delete store[k]; },
    },
    fetch: function () {
        return Promise.resolve({ json: function () { return Promise.resolve({ success: true, rows: [], groups: [] }); } });
    },
    document: {
        getElementById: function (id) { return elements[id] || null; },
        querySelectorAll: function (sel) {
            if (sel === '#msSignalChecks input:checked') {
                return (sandbox._sigChecks || []).filter(function (c) { return c.checked; });
            }
            if (sel === '#msSignalChecks input[type="checkbox"]') return sandbox._sigChecks || [];
            return [];
        },
        createElement: function () { return mkEl(); },
        body: { appendChild: function () {}, removeChild: function () {} },
    },
};
vm.createContext(sandbox);
vm.runInContext(fs.readFileSync(path, 'utf8'), sandbox, { filename: 'market.js' });

let fails = 0;
function check(name, cond) {
    console.log((cond ? 'PASS ' : 'FAIL ') + name);
    if (!cond) fails++;
}

// ---- 公共桩：12 项筛选输入 + 信号勾选 ----
el('msExSt', { checked: true });
el('msMktMin', { value: '100' });
el('msNmcMin', { value: '' }); el('msNmcMax', { value: '' });
el('msTurnMin', { value: '3' }); el('msTurnMax', { value: '' });
el('msVrMin', { value: '' }); el('msVrMax', { value: '' });
el('msChgMin', { value: '' }); el('msChgMax', { value: '' });
el('msBoard', { value: '' });
el('msIndustry', { value: '', options: [{ value: '' }] });   // fresh 会话：仅「全部」
el('msWindow', { value: '3' });
el('msPresetSel', { value: '' });
el('msCoarseResult', {});
sandbox._sigChecks = [];

// ---- 回归：F1 载荷复数数组键 ----
el('msBoard', { value: '主板' }); el('msIndustry', { value: '' });
let p = vm.runInContext('_msFiltersPayload()', sandbox);
check('F1 payload.boards 数组 / 无单数键', Array.isArray(p.boards) && p.boards[0] === '主板' && !('board' in p));

// ---- 回归：排序 None 殿后 ----
sandbox._msCoarseSortState = { key: 'mkt_cap', order: 'asc' };
let sorted = vm.runInContext('_msCoarseSorted([{mkt_cap:300},{mkt_cap:null},{mkt_cap:100}])', sandbox);
check('市值升序 None 殿后', sorted[0].mkt_cap === 100 && sorted[2].mkt_cap === null);
sandbox._msCoarseSortState = { key: null, order: 'none' };

// ---- 回归：位置筛选 / 低位优先 ----
sandbox._msSignalHits = [
    { symbol: 'a', pos_pctile: 0.85, pos_band: 'high' },
    { symbol: 'b', pos_pctile: 0.12, pos_band: 'low' },
    { symbol: 'c', pos_pctile: null, pos_band: null },
];
el('msPosFilter', { value: 'low' });
check('位置筛选 low 仅低位', vm.runInContext('_msPosFilteredHits()', sandbox).length === 1);
el('msPosFilter', { value: '' });
el('msPosSort', { checked: true });
const g = vm.runInContext('_msMaybePosSort([{pos_pctile:0.85},{pos_pctile:null},{pos_pctile:0.12}])', sandbox);
check('低位优先 None 殿后', g[0].pos_pctile === 0.12 && g[2].pos_pctile === null);
el('msPosSort', { checked: false });

// ---- 回归：CSV 转义 ----
check('CSV 引号双写', vm.runInContext('_msCsvCell("a,\\"b\\"")', sandbox) === '"a,""b"""');

// ---- t4/F-V3：fresh 会话预设行业挂起 → 扫描后重应用 ----
vm.runInContext('initMarketScanner()', sandbox);   // 绑定 change 监听（含 F-V3 清挂起）
el('msIndustry', { value: '银行' });                      // 保存时值可先于 option 存在
sandbox._nextPrompt = '银行方案';
sandbox._sigChecks = [];
vm.runInContext('msSavePreset()', sandbox);
check('预设已保存（含行业条件）', JSON.parse(store.ms_filter_presets_v1 || '{}')['银行方案'].filters.industry === '银行');

el('msIndustry', { value: '', options: [{ value: '' }] }); // 模拟 fresh 会话（仅「全部」）
el('msPresetSel', { value: '银行方案' });
vm.runInContext('msApplyPreset()', sandbox);
check('F-V3 挂起生效（pending=银行，select 未被静默赋值）',
    sandbox._msPendingIndustry === '银行' && el('msIndustry').value === '');

el('msIndustry', { value: '', options: [{ value: '' }, { value: '银行' }] });  // 扫描后下拉重建
vm.runInContext('_msApplyPendingIndustry()', sandbox);
check('F-V3 扫描成功后重应用行业', el('msIndustry').value === '银行');
check('F-V3 重应用后挂起清除（一次性）', sandbox._msPendingIndustry === null);
vm.runInContext('_msApplyPendingIndustry()', sandbox);
check('F-V3 幂等：重复调用无副作用', el('msIndustry').value === '银行' && sandbox._msPendingIndustry === null);

// 手动改行业 → 清挂起
el('msIndustry', { value: '' });
sandbox._msPendingIndustry = '电池';
const indEl = el('msIndustry');
if (indEl._handlers.change) indEl._handlers.change();
check('F-V3 手动改行业清除挂起', sandbox._msPendingIndustry === null);

// 下拉已有 option 时应用预设 → 立即生效不挂起
el('msIndustry', { value: '', options: [{ value: '' }, { value: '银行' }] });
el('msPresetSel', { value: '银行方案' });
vm.runInContext('msApplyPreset()', sandbox);
check('F-V3 option 已存在 → 立即生效不挂起',
    el('msIndustry').value === '银行' && sandbox._msPendingIndustry === null);

// ---- t4/F-V4：行业筛选提示 ----
sandbox._msRows = [
    { code: '600000', name: 'A', board: '主板', industry: '银行', price: 10, change_pct: 1, turnover: 3, volume_ratio: 1, mkt_cap: 200 },
    { code: '000001', name: 'B', board: '主板', industry: '银行', price: 11, change_pct: 2, turnover: 4, volume_ratio: 1, mkt_cap: 300 },
];
el('msIndustry', { value: '银行', options: [{ value: '' }, { value: '银行' }] });
vm.runInContext('msRenderCoarse()', sandbox);
check('F-V4 行业筛选时含「切换行业请重新扫描」提示',
    el('msCoarseResult').innerHTML.indexOf('切换行业请重新扫描') >= 0);
el('msIndustry', { value: '', options: [{ value: '' }, { value: '银行' }] });
vm.runInContext('msRenderCoarse()', sandbox);
check('F-V4 未筛行业时无该提示', el('msCoarseResult').innerHTML.indexOf('切换行业请重新扫描') < 0);

// ---- 回归：预设保存-应用-删除闭环（无行业条件路径） ----
sandbox._nextPrompt = '简洁方案';
sandbox._sigChecks = [Object.assign(mkEl({ value: 'kdj_golden' }), { checked: true })];
el('msWindow', { value: '5' });
vm.runInContext('msSavePreset()', sandbox);
el('msTurnMin', { value: '9' });
el('msPresetSel', { value: '简洁方案' });
vm.runInContext('msApplyPreset()', sandbox);
check('回归：应用预设恢复换手下限与窗口', Number(el('msTurnMin').value) === 3 && Number(el('msWindow').value) === 5);
el('msPresetSel', { value: '简洁方案' });
sandbox._nextPrompt = null;
vm.runInContext('msDeletePreset()', sandbox);
check('回归：删除后预设清空', Object.keys(JSON.parse(store.ms_filter_presets_v1 || '{}')).length === 1);

// ---- 021BW 姊妹批：内置选股方案（结构/说明/应用/删除守卫） ----
const builtins = sandbox._MS_BUILTIN_PRESETS;
check('内置方案 5 套', !!builtins && builtins.length === 5);
check('内置方案名唯一且带说明', (() => {
    const names = builtins.map(b => b.name);
    return new Set(names).size === 5 && builtins.every(b => b.desc && b.desc.length > 20 && b.filters && Array.isArray(b.signals) && b.signals.length === 4);
})());
const _legalKeys = ['exclude_st', 'mkt_cap_min', 'nmc_cap_min', 'nmc_cap_max', 'turnover_min', 'turnover_max', 'volume_ratio_min', 'volume_ratio_max', 'change_pct_min', 'change_pct_max', 'board', 'industry'];
check('内置 filters 键全部合法（12 键集）', builtins.every(b => Object.keys(b.filters).every(k => _legalKeys.indexOf(k) >= 0)));
check('内置 signals 全为四类金叉键', builtins.every(b => b.signals.every(s => sandbox._MS_ALL_BUY_SIGNALS.indexOf(s) >= 0)));
sandbox._sigChecks = ['macd_golden_above', 'macd_golden_below', 'kdj_golden_low', 'kdj_golden'].map(k => Object.assign(mkEl({ value: k }), { checked: false }));
el('msPresetSel', { value: '' });
el('msPosFilter', { value: '' });
el('msPosSort', { checked: false });
el('msPresetDesc', { textContent: '' });
vm.runInContext('msRenderPresets()', sandbox);
check('方案下拉含内置分组（★ 前缀）', elements['msPresetSel'].innerHTML.indexOf('★') >= 0);
elements['msPresetSel'].value = '★低位金叉（证据优先）';
vm.runInContext('msApplyPreset()', sandbox);
check('内置应用：位置筛选=low', elements['msPosFilter'].value === 'low');
check('内置应用：低位优先开', elements['msPosSort'].checked === true);
check('内置应用：四类金叉全勾', sandbox._sigChecks.every(c => c.checked));
check('内置应用：说明随选择显示（含 90% 证据）', elements['msPresetDesc'].textContent.indexOf('90%（9/10）') >= 0);
let _alertMsg = '';
sandbox.alert = function (m) { _alertMsg = m; };
vm.runInContext('msDeletePreset()', sandbox);
check('内置方案不可删除', _alertMsg.indexOf('不可删除') >= 0);

console.log(fails === 0 ? 'ALL FRONTEND CHECKS PASSED (' + path + ')' : fails + ' CHECKS FAILED');
process.exit(fails === 0 ? 0 : 1);
