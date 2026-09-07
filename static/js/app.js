    // ========== 通用工具函数 ==========
    // 统一的 JSON 解析函数，即使服务器返回 HTML 错误页面也不会崩溃
    function safeJson(resp) {
        const ct = resp.headers.get('Content-Type') || '';
        if (ct.indexOf('application/json') === -1) {
            // 服务器返回的不是 JSON（通常是 HTML 错误页面）
            return resp.text().then(function() {
                return {success: false, message: '服务器返回了非JSON响应（HTTP ' + resp.status + '），请确认程序正常运行'};
            });
        }
        return resp.json();
    }

    // ========== 021BD：多主题皮肤 ==========
    var _THEMES = ['light', 'dark', 'green', 'warm'];

    /** 读主题 CSS 变量的运行时值（ECharts 为 canvas 渲染，CSS 变量不生效，须取实色） */
    function _themeCol(varName, fallback) {
        try {
            var v = getComputedStyle(document.documentElement).getPropertyValue(varName);
            v = v && v.trim();
            return v || fallback;
        } catch (e) { return fallback; }
    }

    /** 切换主题：写 data-theme + localStorage，广播 themechange（看板图表重渲染） */
    function setTheme(theme) {
        if (_THEMES.indexOf(theme) < 0) theme = 'light';
        if (theme === 'light') {
            document.documentElement.removeAttribute('data-theme');
        } else {
            document.documentElement.setAttribute('data-theme', theme);
        }
        try { localStorage.setItem('sa_theme', theme); } catch (e) {}
        var sel = document.getElementById('themeSelect');
        if (sel && sel.value !== theme) sel.value = theme;
        // 看板图表是 canvas，重渲染才能吃到新主题色；雷达/K线图随下次打开刷新
        window.dispatchEvent(new Event('themechange'));
    }

    // 主题切换后重渲染看板图表（pie/bar 读 _themeCol 实色）
    window.addEventListener('themechange', function() {
        try {
            if (_dashData && typeof dashRenderCharts === 'function') dashRenderCharts(_dashData.stocks, _dashData.summary);
        } catch (e) { console.error('themechange rerender:', e); }
    });

    // 页面加载后同步选择器显示值与实际主题（预应用脚本只设了 data-theme）
    window.addEventListener('DOMContentLoaded', function() {
        try {
            var cur = document.documentElement.getAttribute('data-theme') || 'light';
            var sel = document.getElementById('themeSelect');
            if (sel) sel.value = cur;
        } catch (e) {}
    });

    /**
     * 统一人民币格式化函数（强制使用"元"为单位，完整显示）
     * @param {number|null|undefined} value - 数值（单位：元）
     * @returns {string} 格式化后的字符串，如 "¥42,320.00"；null → "--"
     */
    function formatCNY(value) {
        if (value === null || value === undefined || isNaN(value)) return '--';
        // 银行家舍入法保留2位小数 + 千分位分隔
        return '¥' + Number(value).toLocaleString('zh-CN', {
            minimumFractionDigits: 2,
            maximumFractionDigits: 2
        });
    }

    /**
     * 统一盈亏格式化函数（带¥前缀和正负号）
     * 输出格式：¥+5,116.30 或 ¥-5,116.30
     */
    function formatPnl(value) {
        if (value === null || value === undefined || isNaN(value)) return '--';
        var sign = value > 0 ? '+' : '';
        return '¥' + sign + Number(value).toLocaleString('zh-CN', {
            minimumFractionDigits: 2, maximumFractionDigits: 2
        });
    }
    /**
     * 统一盈亏颜色：盈利=红色，亏损=绿色，持平=#333
     */
    function pnlColor(value) {
        if (value === null || value === undefined || isNaN(value)) return '#999';
        return value > 0 ? '#e74c3c' : value < 0 ? '#27ae60' : '#333';
    }

    // ========== 通用表格排序 ==========
    // 排序状态：{ tableKey: { key: 'market_value', order: 'desc' } }
    var _sortState = {};
    // 渲染函数注册表：{ tableKey: renderFunction }
    var _sortRenderers = {};
    // 数据缓存：供排序重渲染使用（避免重复请求 API）
    var _holdingsCache = [];
    var _stocksCache = [];

    /**
     * 通用排序函数，支持 NULL 安全 + 数字比较
     * @param {Array} rows - 数据行数组
     * @param {string} key - 排序字段名
     * @param {string} order - 'asc' | 'desc'
     * @returns {Array} 排序后的新数组（不修改原数组）
     */
    function sortTable(rows, key, order) {
        return rows.slice().sort(function(a, b) {
            var va = a[key], vb = b[key];
            // NULL 始终排末尾
            if (va == null && vb == null) return 0;
            if (va == null) return 1;
            if (vb == null) return -1;
            // 数值比较
            return order === 'asc' ? va - vb : vb - va;
        });
    }

    /**
     * 处理表头点击排序（三态循环：desc → asc → null）
     * 从 _sortRenderers 注册表查找重渲染函数，避免重复请求 API。
     * @param {string} tableKey - 表标识（如 'holdings' / 'stocks'）
     * @param {string} key - 排序字段名
     */
    function handleSortClick(tableKey, key) {
        var st = _sortState[tableKey];
        if (!st || st.key !== key) {
            // 新列，默认降序
            _sortState[tableKey] = { key: key, order: 'desc' };
        } else if (st.order === 'desc') {
            _sortState[tableKey] = { key: key, order: 'asc' };
        } else {
            // 已循环一轮，清除排序
            delete _sortState[tableKey];
        }
        // 持久化排序状态到 localStorage
        _saveSortState(tableKey);
        // 从注册表查找并执行重渲染
        if (_sortRenderers[tableKey]) _sortRenderers[tableKey]();
    }

    /**
     * 生成排序表头 HTML（带方向指示图标）
     * @param {string} tableKey - 表标识
     * @param {string} key - 排序字段名
     * @param {string} label - 表头文案
     * @returns {string} <th> HTML
     */
    function sortableTh(tableKey, key, label) {
        var st = _sortState[tableKey];
        var active = st && st.key === key;
        var icon = active ? (st.order === 'desc' ? ' ↓' : ' ↑') : ' ↕';
        var style = active ? 'style="cursor:pointer;color:#1a73e8;user-select:none;"' : 'style="cursor:pointer;color:var(--text-3,#888);user-select:none;"';
        return '<th class="sortable" ' + style + ' onclick="handleSortClick(\'' + tableKey + '\',\'' + key + '\')">' + label + '<span>' + icon + '</span></th>';
    }

    // ========== 021BI: 全市场选股扫描器 ==========
    var _msSignals = {};      // 信号库 {key: {label, note}}
    var _msRows = [];         // 粗筛全量（卫生线已在服务端应用，量比仅前300有值）
    var _msSignalHits = [];   // 信号扫描命中 [{symbol, name, matches}]
    var _msAbort = false;

    function initMarketScanner() {
        fetch('/api/market/scan/library').then(function(r) { return r.json(); }).then(function(d) {
            _msSignals = d.signals || {};
            var box = document.getElementById('msSignalChecks');
            if (!box || box.childElementCount > 0) return;
            Object.keys(_msSignals).forEach(function(key) {
                var lib = _msSignals[key];
                var label = document.createElement('label');
                label.title = lib.note;
                label.innerHTML = '<input type="checkbox" value="' + key + '" checked> ' + lib.label;
                box.appendChild(label);
            });
        }).catch(function() {});
        fetch('/api/market/scan/status').then(function(r) { return r.json(); }).then(function(d) {
            if (d && d.has_snapshot) {
                document.getElementById('msSnapshotMeta').textContent =
                    '上次快照：' + String(d.snapshot_at).replace('T', ' ') + '（' + d.universe + ' 只）';
            }
        }).catch(function() {});
        // 条件变更即时重渲（仅收窄；放宽需重新扫描）
        ['msExSt', 'msMktMin', 'msNmcMin', 'msNmcMax', 'msTurnMin', 'msTurnMax', 'msVrMin', 'msVrMax', 'msChgMin', 'msChgMax', 'msBoard', 'msIndustry'].forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.addEventListener('change', function() { if (_msRows.length) msRenderCoarse(); });
        });
        var resOnly = document.getElementById('msResOnly');
        if (resOnly) resOnly.addEventListener('change', function() { if (_msSignalHits.length) msRenderSignals(); });
    }

    function _msFilters() {
        return {
            exclude_st: document.getElementById('msExSt').checked,
            mkt_cap_min: parseFloat(document.getElementById('msMktMin').value) || 0,
            nmc_cap_min: parseFloat(document.getElementById('msNmcMin').value),
            nmc_cap_max: parseFloat(document.getElementById('msNmcMax').value),
            turnover_min: parseFloat(document.getElementById('msTurnMin').value) || 0,
            turnover_max: parseFloat(document.getElementById('msTurnMax').value),
            volume_ratio_min: parseFloat(document.getElementById('msVrMin').value),
            volume_ratio_max: parseFloat(document.getElementById('msVrMax').value),
            change_pct_min: parseFloat(document.getElementById('msChgMin').value),
            change_pct_max: parseFloat(document.getElementById('msChgMax').value),
            board: document.getElementById('msBoard').value,
            industry: document.getElementById('msIndustry').value
        };
    }

    function _msLocalFiltered() {
        var f = _msFilters();
        return _msRows.filter(function(r) {
            if (f.exclude_st && String(r.name || '').toUpperCase().indexOf('ST') >= 0) return false;
            if (f.mkt_cap_min && (r.mkt_cap || 0) < f.mkt_cap_min) return false;
            if (!isNaN(f.nmc_cap_min) && f.nmc_cap_min !== null && (r.nmc_cap || 0) < f.nmc_cap_min) return false;
            if (!isNaN(f.nmc_cap_max) && f.nmc_cap_max !== null && (r.nmc_cap || 0) > f.nmc_cap_max) return false;
            if (f.turnover_min && (r.turnover || 0) < f.turnover_min) return false;
            if (!isNaN(f.turnover_max) && f.turnover_max !== null && (r.turnover || 0) > f.turnover_max) return false;
            if (!isNaN(f.volume_ratio_min) && f.volume_ratio_min !== null && (r.volume_ratio || 0) < f.volume_ratio_min) return false;
            if (!isNaN(f.volume_ratio_max) && f.volume_ratio_max !== null && (r.volume_ratio || 0) > f.volume_ratio_max) return false;
            if (!isNaN(f.change_pct_min) && f.change_pct_min !== null && (r.change_pct || -999) < f.change_pct_min) return false;
            if (!isNaN(f.change_pct_max) && f.change_pct_max !== null && (r.change_pct || 999) > f.change_pct_max) return false;
            if (f.board && r.board !== f.board) return false;
            if (f.industry && r.industry !== f.industry) return false;
            return true;
        });
    }

    function msRunCoarse() {
        var btn = document.getElementById('msScanBtn');
        var status = document.getElementById('msCoarseStatus');
        btn.disabled = true;
        status.textContent = '扫描中（新浪约56页，预计1分钟，请勿关闭页面）…';
        fetch('/api/market/scan', {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ refresh: true, filters: _msFilters() })
        }).then(function(r) { return r.json(); }).then(function(d) {
            btn.disabled = false;
            if (!d.available) {
                status.innerHTML = '<span style="color:#e74c3c;">❌ ' + (d.error || '扫描失败') + '</span>';
                return;
            }
            _msRows = d.rows || [];
            status.textContent = '✅ 快照 ' + d.universe + ' 只，粗筛后 ' + _msRows.length + ' 只';
            document.getElementById('msSnapshotMeta').textContent =
                '快照截至：' + String(d.snapshot_at).replace('T', ' ');
            msPopulateIndustries();
            msRenderCoarse();
            document.getElementById('msSignalSection').style.display = 'block';
        }).catch(function(e) {
            btn.disabled = false;
            status.innerHTML = '<span style="color:#e74c3c;">❌ 请求失败: ' + e + '</span>';
        });
    }

    function msPopulateIndustries() {
        var sel = document.getElementById('msIndustry');
        var cur = sel.value;
        var inds = {};
        _msRows.forEach(function(r) { if (r.industry) inds[r.industry] = 1; });
        var keys = Object.keys(inds).sort();
        sel.innerHTML = '<option value="">全部</option>' + keys.map(function(k) {
            return '<option value="' + k + '">' + k + '</option>';
        }).join('');
        if (keys.indexOf(cur) >= 0) sel.value = cur;
    }

    function msRenderCoarse() {
        var box = document.getElementById('msCoarseResult');
        var rows = _msLocalFiltered();
        var shown = rows.slice(0, 100);
        if (!rows.length) {
            box.innerHTML = '<p style="color:var(--text-3,#999);font-size:13px;">无匹配结果</p>';
            return;
        }
        var html = '<p style="font-size:12px;color:var(--text-3,#999);margin-bottom:6px;">共 ' + rows.length +
            ' 只，展示前 ' + shown.length + ' 只（按市值降序）。调整条件即时收窄；放宽需重新扫描。</p>';
        html += '<table class="dash-table"><thead><tr><th>代码</th><th>名称</th><th>板块</th><th>行业</th>' +
            '<th>现价</th><th>涨跌%</th><th>换手%</th><th>量比</th><th>市值(亿)</th></tr></thead><tbody>';
        shown.forEach(function(r) {
            var pct = r.change_pct;
            var pctCls = pct > 0 ? 'pa-up' : (pct < 0 ? 'pa-down' : '');
            html += '<tr><td>' + r.code + '</td><td>' + r.name + '</td><td>' + (r.board || '') + '</td>' +
                '<td>' + (r.industry || '—') + '</td><td>' + (r.price != null ? r.price.toFixed(2) : '—') + '</td>' +
                '<td class="' + pctCls + '">' + (pct != null ? (pct > 0 ? '+' : '') + pct.toFixed(2) : '—') + '</td>' +
                '<td>' + (r.turnover != null ? r.turnover.toFixed(2) : '—') + '</td>' +
                '<td>' + (r.volume_ratio != null ? r.volume_ratio.toFixed(2) : '—') + '</td>' +
                '<td>' + (r.mkt_cap != null ? r.mkt_cap.toFixed(0) : '—') + '</td></tr>';
        });
        html += '</tbody></table>';
        box.innerHTML = html;
    }

    function msStartSignals() {
        var rows = _msLocalFiltered().slice(0, 300);
        var progress = document.getElementById('msSignalProgress');
        if (!rows.length) { progress.textContent = '粗筛结果为空，先完成第①步'; return; }
        var wanted = [];
        document.querySelectorAll('#msSignalChecks input:checked').forEach(function(cb) { wanted.push(cb.value); });
        if (!wanted.length) { progress.textContent = '请至少勾选一个信号'; return; }
        var windowN = parseInt(document.getElementById('msWindow').value, 10) || 3;
        _msAbort = false;
        _msSignalHits = [];
        document.getElementById('msSignalBtn').disabled = true;
        document.getElementById('msStopBtn').style.display = '';
        var chunks = [];
        for (var i = 0; i < rows.length; i += 25) chunks.push(rows.slice(i, i + 25));
        var done = 0;
        var t0 = Date.now();
        var runNext = function() {
            if (_msAbort || done >= chunks.length) {
                document.getElementById('msSignalBtn').disabled = false;
                document.getElementById('msStopBtn').style.display = 'none';
                progress.textContent = _msAbort ? '已停止：' : '完成：';
                progress.textContent += '扫描 ' + Math.min(done * 25, rows.length) + ' 只 / 命中 ' +
                    _msSignalHits.length + ' 只 / 耗时 ' + Math.round((Date.now() - t0) / 1000) + 's';
                msRenderSignals();
                return;
            }
            progress.textContent = '扫描中 ' + (done + 1) + '/' + chunks.length +
                ' 批（已命中 ' + _msSignalHits.length + ' 只）…';
            var chunk = chunks[done];
            fetch('/api/market/scan-signals', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    entries: chunk.map(function(r) { return { symbol: r.symbol, name: r.name }; }),
                    signals: wanted, window: windowN
                })
            }).then(function(r) { return r.json(); }).then(function(d) {
                if (d.success) (d.results || []).forEach(function(x) { _msSignalHits.push(x); });
            }).catch(function() {}).then(function() {
                done++;
                runNext();
            });
        };
        runNext();
    }

    function msStopSignals() { _msAbort = true; }

    // 021BI 跟进：共振组渲染顺序（⭐⭐⭐ → ⭐⭐ → ⚠️空头 → 💤观察）
    var _MS_RES_ORDER = ['res_bottom_reverse', 'res_double_golden', 'res_bear_confirm', 'res_oversold_watch'];
    var _MS_RES_META = {
        res_bottom_reverse: { icon: '⭐⭐⭐', note: 'KDJ低位金叉+MACD水下金叉同窗：超卖反转与趋势反弹双确认，经典底部结构' },
        res_double_golden:  { icon: '⭐⭐',  note: 'MACD系金叉+KDJ系金叉同窗：两个独立指标系同向触发' },
        res_bear_confirm:   { icon: '⚠️',   note: 'MACD死叉+超买状态同窗：转空双确认，建议排除' },
        res_oversold_watch: { icon: '💤',   note: 'KDJ与RSI双超卖且窗口内无金叉：深度超跌观察池，等右侧触发' }
    };

    function _msRowCells(r) {
        var pct = r.change_pct;
        var pctCls = pct > 0 ? 'pa-up' : (pct < 0 ? 'pa-down' : '');
        return '<td>' + (r.price != null ? r.price.toFixed(2) : '—') + '</td>' +
            '<td class="' + pctCls + '">' + (pct != null ? (pct > 0 ? '+' : '') + pct.toFixed(2) : '—') + '</td>' +
            '<td>' + (r.volume_ratio != null ? r.volume_ratio.toFixed(2) : '—') + '</td>' +
            '<td>' + (r.turnover != null ? r.turnover.toFixed(2) : '—') + '</td>' +
            '<td>' + (r.mkt_cap != null ? r.mkt_cap.toFixed(0) : '—') + '</td>' +
            '<td>' + (r.industry || '—') + '</td>';
    }

    function _msSelCell(h, r) {
        return '<input type="checkbox" class="ms-sel" data-symbol="' + h.symbol +
            '" data-code="' + (r.code || '') + '" data-name="' + (h.name || r.name || '') +
            '" onchange="msUpdateSelCount()">';
    }

    function msRenderSignals() {
        var box = document.getElementById('msSignalResult');
        if (!_msSignalHits.length) {
            box.innerHTML = '<p style="color:var(--text-3,#999);font-size:13px;">无信号命中</p>';
            return;
        }
        var rowMap = {};
        _msRows.forEach(function(r) { rowMap[r.symbol] = r; });
        var resOnly = document.getElementById('msResOnly').checked;
        var html = '';

        // ---- 共振组置顶 ----
        _MS_RES_ORDER.forEach(function(resKey) {
            var meta = _MS_RES_META[resKey];
            var group = _msSignalHits.filter(function(h) {
                return (h.resonances || []).some(function(x) { return x.key === resKey; });
            });
            if (!group.length) return;
            html += '<div style="margin-bottom:10px;"><div style="font-weight:600;font-size:13px;margin-bottom:4px;">' +
                '▸ ' + meta.icon + ' ' + meta.note.replace(/：.*/, '') + '（' + group.length + ' 只）' +
                '<span style="font-weight:normal;color:var(--text-3,#999);font-size:12px;"> ' + meta.note + '</span></div>' +
                '<table class="dash-table"><thead><tr><th>加入</th><th>代码</th><th>名称</th><th>共振构成</th>' +
                '<th>现价</th><th>涨跌%</th><th>量比</th><th>换手%</th><th>市值(亿)</th><th>行业</th></tr></thead><tbody>';
            group.forEach(function(h) {
                var r = rowMap[h.symbol] || {};
                var res = (h.resonances || []).filter(function(x) { return x.key === resKey; })[0] || {};
                html += '<tr><td>' + _msSelCell(h, r) + '</td><td>' + (r.code || '—') + '</td>' +
                    '<td>' + (h.name || r.name || '—') + '</td>' +
                    '<td style="font-size:12px;">' + (res.signals || '—') +
                    ((res.note || '').indexOf('（') >= 0 ? ' <span style="color:var(--text-3,#999);">' +
                     res.note.substring(res.note.indexOf('（')) + '</span>' : '') + '</td>' +
                    _msRowCells(r) + '</tr>';
            });
            html += '</tbody></table></div>';
        });

        // ---- 单一信号分组（共振优先时隐藏，但股票已可经共振组勾选） ----
        if (!resOnly) {
            Object.keys(_msSignals).forEach(function(key) {
                var group = _msSignalHits.filter(function(h) {
                    return h.matches.some(function(m) { return m.signal === key; });
                });
                if (!group.length) return;
                var lib = _msSignals[key];
                html += '<div style="margin-bottom:10px;"><div style="font-weight:600;font-size:13px;margin-bottom:4px;">' +
                    '▸ ' + lib.label + '（' + group.length + ' 只）<span style="font-weight:normal;color:var(--text-3,#999);font-size:12px;"> ' +
                    lib.note + '</span></div><table class="dash-table"><thead><tr><th>加入</th><th>代码</th><th>名称</th>' +
                    '<th>触发日</th><th>现价</th><th>涨跌%</th><th>量比</th><th>换手%</th><th>市值(亿)</th><th>行业</th></tr></thead><tbody>';
                group.forEach(function(h) {
                    var r = rowMap[h.symbol] || {};
                    var m = h.matches.filter(function(x) { return x.signal === key; })[0] || {};
                    html += '<tr><td>' + _msSelCell(h, r) + '</td><td>' + (r.code || '—') + '</td>' +
                        '<td>' + (h.name || r.name || '—') + '</td>' +
                        '<td>' + (m.trigger_date || '—') + '</td>' + _msRowCells(r) + '</tr>';
                });
                html += '</tbody></table></div>';
            });
        } else {
            var resCount = _msSignalHits.filter(function(h) { return (h.resonances || []).length; }).length;
            if (resCount) {
                html += '<p style="font-size:12px;color:var(--text-3,#999);">另有 ' +
                    (_msSignalHits.length - resCount) + ' 只为单一信号命中，取消勾选「只看共振」可查看。</p>';
            }
        }
        box.innerHTML = html;
        document.getElementById('msAddBar').style.display = '';
        msUpdateSelCount();
    }

    function msUpdateSelCount() {
        var n = document.querySelectorAll('.ms-sel:checked').length;
        document.getElementById('msSelCount').textContent = n;
    }

    function msAddSelected() {
        var seen = {};
        var checked = Array.prototype.slice.call(document.querySelectorAll('.ms-sel:checked'))
            .filter(function(el) {
                if (seen[el.getAttribute('data-symbol')]) return false;
                seen[el.getAttribute('data-symbol')] = 1;
                return true;
            });
        if (!checked.length) return;
        if (checked.length > 20) { alert('批量操作上限 20 只，当前已选 ' + checked.length + ' 只'); return; }
        var btns = document.querySelectorAll('#msAddBar button');
        btns.forEach(function(b) { b.disabled = true; });
        var okCount = 0;
        var failCount = 0;
        var seq = function(idx) {
            if (idx >= checked.length) {
                btns.forEach(function(b) { b.disabled = false; });
                alert('加入完成：成功 ' + okCount + ' 只' + (failCount ? '，失败 ' + failCount + ' 只' : '') +
                    '。请到「自选股」页执行「批量分析+评级」获取真评级。');
                return;
            }
            var el = checked[idx];
            fetch('/api/stocks', {
                method: 'POST', headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ symbol: el.getAttribute('data-code'), market: 'a_stock',
                                       name: el.getAttribute('data-name') })
            }).then(function(r) { return r.json(); }).then(function(d) {
                if (d.success) { okCount++; el.checked = false; } else { failCount++; }
                seq(idx + 1);
            }).catch(function() { failCount++; seq(idx + 1); });
        };
        seq(0);
    }

    // ========== v4.0 Hash 路由系统 ==========
    var ROUTES = {
        '#holdings':    { view: 'view-holdings',    label: '持仓管理', init: function() { loadAccounts(); } },
        '#watchlist':   { view: 'view-watchlist',   label: '自选股',   init: function() { loadStocks(); } },
        '#trades':      { view: 'view-trades',      label: '交易流水', init: function() { loadAllTrades(); loadAllAdjustments(); } },
        '#market':      { view: 'view-market',      label: '市场行情', init: function() { loadMarketOverview(); initMarketScanner(); } },
        '#report':      { view: 'view-report',      label: '分析报告', init: function() { /* 由 viewReport() 驱动 */ } },
        '#dashboard':   { view: 'view-dashboard',   label: '总览看板', init: function() { loadDashboard(); } },
        '#backtest':    { view: 'view-backtest',    label: '回测中心', init: function() { loadBacktestMarketReport(); } }
    };
    var DEFAULT_ROUTE = '#holdings';
    var _viewLoaded = {}; // 记录各视图是否已首次加载

    /** 导航到指定路由（旧 #adjustments/#daily 哈希自动归并） */
    function navigateTo(hash) {
        if (hash === '#adjustments') hash = '#trades';
        if (hash === '#daily') hash = '#dashboard';
        if (!ROUTES[hash]) hash = DEFAULT_ROUTE;
        window.location.hash = hash;
    }

    /** hashchange 事件处理：切换视图 + 高亮 Tab */
    function handleHashChange() {
        var hash = window.location.hash || DEFAULT_ROUTE;
        if (hash === '#adjustments') {
            window.location.hash = '#trades'; // 旧链接兼容，触发一次重定向
            return;
        }
        if (hash === '#daily') {
            window.location.hash = '#dashboard'; // 旧链接兼容，触发一次重定向
            return;
        }
        if (!ROUTES[hash]) hash = DEFAULT_ROUTE;
        var route = ROUTES[hash];

        // 切换视图容器
        var views = document.querySelectorAll('.view');
        for (var i = 0; i < views.length; i++) {
            views[i].classList.remove('active');
        }
        var target = document.getElementById(route.view);
        if (target) target.classList.add('active');

        // 高亮导航 Tab
        var tabs = document.querySelectorAll('.topnav-tab');
        for (var j = 0; j < tabs.length; j++) {
            tabs[j].classList.toggle('active', tabs[j].getAttribute('data-route') === hash);
        }

        // 首次进入该视图时加载数据
        if (!_viewLoaded[hash]) {
            _viewLoaded[hash] = true;
            if (route.init) route.init();
        } else if (hash === '#dashboard' && _dashData) {
            // 重进看板：批量评分表轻量刷新（分析/评级随时可能已在别处更新，免去手动点刷新）
            refreshDashboardData();
        }

        // 恢复该视图的排序状态
        _restoreSortState(hash);
    }

    /** 从 localStorage 恢复排序状态 */
    function _restoreSortState(routeHash) {
        try {
            var saved = localStorage.getItem('sortState_' + routeHash);
            if (saved) {
                var parsed = JSON.parse(saved);
                var tableKey = routeHash.substring(1); // '#holdings' -> 'holdings'
                if (parsed && !_sortState[tableKey]) {
                    _sortState[tableKey] = parsed;
                }
            }
        } catch(e) {}
    }

    /** 保存排序状态到 localStorage */
    function _saveSortState(tableKey) {
        try {
            var st = _sortState[tableKey];
            if (st) {
                localStorage.setItem('sortState_' + '#' + tableKey, JSON.stringify(st));
            } else {
                localStorage.removeItem('sortState_' + '#' + tableKey);
            }
        } catch(e) {}
    }

    // ========== 交易流水全局列表 ==========
    var _tradesCache = [];

    function loadAllTrades() {
        var filterType = '';
        var sel = document.getElementById('tradeFilterType');
        if (sel) filterType = sel.value;
        var url = '/api/portfolio/trades';
        if (filterType) url += '?type=' + filterType;
        fetch(url)
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success) return;
                _tradesCache = data.trades || [];
                _sortRenderers['trades'] = loadAllTrades;

                // 渲染汇总卡片（021AM：红利补税单列并计入净流入）
                var summaryEl = document.getElementById('tradesSummary');
                if (summaryEl && data.summary) {
                    var s = data.summary;
                    var netColor = s.net_amount > 0 ? '#e74c3c' : s.net_amount < 0 ? '#27ae60' : '#333';
                    summaryEl.innerHTML =
                        '<div class="view-summary-card"><div class="label">买入总额</div><div class="value" style="color:#e74c3c;">-' + formatCNY(s.total_buy_amount) + '</div></div>' +
                        '<div class="view-summary-card"><div class="label">卖出总额</div><div class="value" style="color:#27ae60;">+' + formatCNY(s.total_sell_amount) + '</div></div>' +
                        '<div class="view-summary-card"><div class="label">分红收入</div><div class="value" style="color:#f39c12;">+' + formatCNY(s.total_dividend) + '</div></div>' +
                        '<div class="view-summary-card"><div class="label">红利补税</div><div class="value" style="color:#8e44ad;">-' + formatCNY(s.total_dividend_tax || 0) + '</div></div>' +
                        '<div class="view-summary-card"><div class="label">净流入</div><div class="value" style="color:' + netColor + ';">' + (s.net_amount >= 0 ? '+' : '') + Math.abs(s.net_amount).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2}) + '</div></div>';
                }

                // 渲染表格
                var listEl = document.getElementById('allTradesList');
                if (!listEl) return;
                if (_tradesCache.length === 0) {
                    listEl.innerHTML = '<div class="empty">暂无交易流水</div>';
                    return;
                }
                var st = _sortState['trades'];
                var rows = st ? sortTable(_tradesCache, st.key, st.order) : _tradesCache;
                var html = '<table><thead><tr>' +
                    sortableTh('trades', 'trade_date', '日期') +
                    '<th>代码</th><th>名称</th>' +
                    '<th>账户</th>' +
                    '<th>类型</th>' +
                    sortableTh('trades', 'price', '价格') +
                    sortableTh('trades', 'quantity', '数量') +
                    sortableTh('trades', 'amount', '金额') +
                    '<th>备注</th></tr></thead><tbody>';
                rows.forEach(function(t) {
                    var typeLabel = tradeTypeLabel(t.trade_type);
                    var typeColor = tradeTypeColor(t.trade_type);
                    html += '<tr>' +
                        '<td>' + (t.trade_date || '—') + '</td>' +
                        '<td><strong>' + (t.symbol || '—') + '</strong></td>' +
                        '<td>' + (t.name || '—') + '</td>' +
                        '<td style="font-size:12px;color:var(--text-2,#666);">' + (t.account_name || '—') + '</td>' +
                        '<td style="color:' + typeColor + ';font-weight:600;">' + typeLabel + '</td>' +
                        '<td>' + (t.price != null ? t.price.toFixed(2) : '—') + '</td>' +
                        '<td>' + (t.quantity != null ? t.quantity.toLocaleString('zh-CN') : '—') + '</td>' +
                        '<td>' + (t.amount != null ? formatCNY(t.amount) : '—') + '</td>' +
                        '<td style="font-size:12px;color:var(--text-3,#888);">' + (t.notes || '') + '</td>' +
                    '</tr>';
                });
                html += '</tbody></table>';
                listEl.innerHTML = html;
            })
            .catch(function(err) { console.error('loadAllTrades:', err); });
    }

    // ========== 成本修正全局列表 ==========
    var _adjustmentsCache = [];

    function loadAllAdjustments() {
        fetch('/api/portfolio/cost-adjustments')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success) return;
                _adjustmentsCache = data.adjustments || [];
                var listEl = document.getElementById('allAdjustmentsList');
                if (!listEl) return;
                if (_adjustmentsCache.length === 0) {
                    listEl.innerHTML = '<div class="empty">暂无成本修正记录</div>';
                    return;
                }
                var html = '<table><thead><tr><th>时间</th><th>代码</th><th>名称</th>' +
                    '<th>修正前成本</th><th>修正后成本</th><th>偏差</th>' +
                    '<th>原因</th><th>备注</th></tr></thead><tbody>';
                _adjustmentsCache.forEach(function(a) {
                    var diff = (a.adjusted_avg_cost != null && a.original_avg_cost != null)
                        ? a.adjusted_avg_cost - a.original_avg_cost : null;
                    var diffDisplay = diff != null
                        ? '<span style="color:' + (diff > 0 ? '#e74c3c' : '#27ae60') + ';">' + (diff > 0 ? '+' : '') + diff.toFixed(3) + '</span>'
                        : '—';
                    html += '<tr>' +
                        '<td style="font-size:12px;">' + (a.created_at || '—') + '</td>' +
                        '<td><strong>' + (a.symbol || '—') + '</strong></td>' +
                        '<td>' + (a.name || '—') + '</td>' +
                        '<td>' + (a.original_avg_cost != null ? a.original_avg_cost.toFixed(3) : '—') + '</td>' +
                        '<td style="font-weight:600;color:#1a73e8;">' + (a.adjusted_avg_cost != null ? a.adjusted_avg_cost.toFixed(3) : '—') + '</td>' +
                        '<td>' + diffDisplay + '</td>' +
                        '<td>' + (a.adjustment_reason || '—') + '</td>' +
                        '<td style="font-size:12px;color:var(--text-3,#888);">' + (a.adjustment_notes || '') + '</td>' +
                    '</tr>';
                });
                html += '</tbody></table>';
                listEl.innerHTML = html;
            })
            .catch(function(err) { console.error('loadAllAdjustments:', err); });
    }

    // ========== 初始化 ==========
    window.addEventListener('hashchange', handleHashChange);

    window.onload = function() {
        initDB();
        loadGroups();
        loadDbStats();
        // 默认加载持仓管理数据
        loadPortfolioGroups();
        // 初始化路由（如果 URL 没有 hash，设置为默认）
        if (!window.location.hash) {
            window.location.hash = DEFAULT_ROUTE;
        } else {
            handleHashChange();
        }
    };

    function initDB() {
        fetch('/api/init-db', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.success) console.log('数据库就绪');
            });
    }

    // ========== 分组 ==========
    var currentWatchlistGroup = null;
    var _watchlistGroupCache = [];   // 缓存自选股分组列表
    
    function loadGroups() {
        fetch('/api/groups?type=watchlist')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    _watchlistGroupCache = data.groups || [];
                    // 填充添加股票表单的分组下拉
                    const sel = document.getElementById('group_id');
                    if (sel) {
                        sel.innerHTML = '<option value="">不分组</option>';
                        data.groups.forEach(function(g) {
                            sel.innerHTML += '<option value="' + g.id + '">' + g.name + '</option>';
                        });
                    }
                    // 渲染染自选股分组 Tab
                    renderWatchlistGroupTabs(data.groups);
                }
            })
            .catch(function(err) { console.error('loadGroups error:', err); });
    }
    
    // 渲染自选股分组 Tab 栏（021AO：隐藏空组，空组仍可在"管理分组"中维护）
    function renderWatchlistGroupTabs(groups) {
        var container = document.getElementById('watchlistGroupTabs');
        if (!container) return;
        var visible = groups.filter(function(g) { return (g.stock_count || 0) > 0; });
        // 当前选中的组若已无股票，自动回到"全部"
        if (currentWatchlistGroup !== null) {
            var still = visible.some(function(g) { return g.id == currentWatchlistGroup; });
            if (!still) currentWatchlistGroup = null;
        }
        var html = '<button class="btn btn-sm" style="' +
            (currentWatchlistGroup === null ? 'background:#1a73e8;color:white;' : 'background:#e0e0e0;') +
            '" onclick="selectWatchlistGroup(null)">全部</button>';
        visible.forEach(function(g) {
            var active = currentWatchlistGroup == g.id;
            html += '<button class="btn btn-sm" style="' +
                (active ? 'background:#1a73e8;color:white;' : 'background:#e0e0e0;') +
                '" onclick="selectWatchlistGroup(' + g.id + ')">' + g.name + ' (' + g.stock_count + ')</button>';
        });
        container.innerHTML = html;
    }
    
    function selectWatchlistGroup(gid) {
        currentWatchlistGroup = gid;
        loadGroups();      // 刷新 Tab 高亮
        loadStocks();       // 刷新列表（带 group_id 过滤）
    }

    // ========== 添加股票 ==========
    function addStock() {
        const symbol = document.getElementById('symbol').value.trim();
        const market = document.getElementById('market').value;
        const name = document.getElementById('name').value.trim();
        const group_id = document.getElementById('group_id').value || null;

        if (!symbol) { alert('请输入股票代码'); return; }

        fetch('/api/stocks', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ symbol, market, name, group_id })
        })
        .then(r => r.json().then(d => ({status: r.status, data: d})))
        .then(res => {
            if (res.data.success) {
                document.getElementById('symbol').value = '';
                document.getElementById('name').value = '';
                loadStocks();
            } else {
                alert('添加失败：' + (res.data.message || '未知错误'));
            }
        })
        .catch(err => {
            alert('网络错误，请确认程序仍在运行：' + err);
        });
    }

    // ========== 加载股票列表 ==========
    // 020R-61：刷新列表 = 刷新最新价（网络） + 重读列表（与持仓页「刷新价格」同源）
    function refreshStockList() {
        var btn = document.getElementById('stockListRefreshBtn');
        if (btn) { btn.disabled = true; btn.textContent = '🔄 刷新中...'; }
        fetch('/api/portfolio/refresh-prices', { method: 'POST' })
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    _showToast(data.message || '价格已刷新');
                } else {
                    _showToast('刷新失败：' + (data.message || '未知错误') + '，旧价格已保留');
                }
            })
            .catch(function() {
                _showToast('刷新失败：网络错误，旧价格已保留');
            })
            .finally(function() {
                if (btn) { btn.disabled = false; btn.textContent = '🔄 刷新列表'; }
                loadStocks();
            });
    }

    function loadStocks() {
        var url = '/api/stocks';
        if (currentWatchlistGroup) url += '?group_id=' + currentWatchlistGroup;
        fetch(url)
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success && data.stocks.length > 0) {
                    _stocksCache = data.stocks;
                    _sortRenderers['stocks'] = loadStocks;
                    var st = _sortState['stocks'];
                    var rows = _stocksCache.slice();
                    if (st) {
                        rows = sortTable(rows, st.key, st.order);
                    } else {
                        // 默认排序：有持仓的排前面（持仓数量 > 0），组内保持接口原始顺序
                        rows.sort(function(a, b) {
                            var ha = (a.quantity != null && a.quantity > 0) ? 1 : 0;
                            var hb = (b.quantity != null && b.quantity > 0) ? 1 : 0;
                            return hb - ha;
                        });
                    }
                    let html = '<table><thead><tr>' +
                        '<th style="width:30px;"><input type="checkbox" onclick="toggleAllStocks(this)"></th>' +
                        '<th>市场</th><th>代码</th><th>名称</th><th>分组</th>' +
                        sortableTh('stocks', 'latest_price', '最新价') +
                        '<th>持仓成本</th><th>持仓数量</th>' +
                        sortableTh('stocks', 'market_value', '市值') +
                        sortableTh('stocks', 'unrealized_pnl', '浮动盈亏') +
                        '<th>价格时间</th>' +
                        '<th>操作</th></tr></thead><tbody>';
                    rows.forEach(function(s) {
                        const marketTag = s.market === 'a_stock'
                            ? '<span class="tag tag-a">A股</span>'
                            : '<span class="tag tag-hk">港股</span>'
                        // 最新价显示
                        var priceDisplay = '—';
                        if (s.latest_price != null && s.latest_price > 0) {
                            var pColor = '#333';
                            if (s.price_pct_change != null) {
                                pColor = s.price_pct_change > 0 ? '#e74c3c' : s.price_pct_change < 0 ? '#27ae60' : '#333';
                            }
                            priceDisplay = '<span style="color:' + pColor + ';font-weight:600;">' + s.latest_price.toFixed(2) + '</span>';
                        }
                        // 价格时间显示
                        var timeDisplay = '—';
                        if (s.price_updated_at) {
                            var parts = String(s.price_updated_at).split(/[- :]/);
                            if (parts.length >= 5) {
                                timeDisplay = parts[1] + '-' + parts[2] + ' ' + parts[3] + ':' + parts[4];
                            }
                        }
                        // 持仓成本显示（无持仓显示 '--'）
                        var costDisplay = s.cost_price != null
                            ? '<span style="font-weight:600;">' + s.cost_price.toFixed(2) + '</span>'
                            : '<span style="color:var(--text-3,#999);font-size:12px;">--</span>';
                        // 持仓数量显示（无持仓或数量为 0 显示 '--'）
                        var qtyDisplay = (s.quantity != null && s.quantity > 0)
                            ? s.quantity.toLocaleString('zh-CN')
                            : '<span style="color:var(--text-3,#999);font-size:12px;">--</span>';
                        // 市值显示（无持仓或无价格显示 '--'）
                        var mvDisplay = '<span style="color:var(--text-3,#999);font-size:12px;">--</span>';
                        if (s.market_value != null) {
                            mvDisplay = '<span style="font-weight:600;">' + formatCNY(s.market_value) + '</span>';
                        }
                        // 浮动盈亏显示（仅有持仓且有价格时）
                        var upnlDisplay = '<span style="color:var(--text-3,#999);font-size:12px;">--</span>';
                        if (s.unrealized_pnl != null && !isNaN(s.unrealized_pnl)) {
                            var upnl = s.unrealized_pnl;
                            var uColor = upnl > 0 ? '#e74c3c' : upnl < 0 ? '#27ae60' : '#999';
                            var uSign = upnl > 0 ? '+' : upnl < 0 ? '-' : '';
                            upnlDisplay = '<span style="color:' + uColor + ';font-weight:600;" title="(最新价-成本)×数量">' + uSign + Math.abs(upnl).toLocaleString('zh-CN', {minimumFractionDigits:2, maximumFractionDigits:2}) + '</span>';
                        }
                        html += '<tr>' +
                            '<td><input type="checkbox" class="stock-cb" value="' + s.id + '"></td>' +
                            '<td>' + marketTag + '</td>' +
                            '<td><strong>' + s.symbol + '</strong></td>' +
                            '<td>' + (s.name || '—') + obosBadge(s.obos_signal) + '</td>' +
                            '<td>' + (s.group_name || '—') + '</td>' +
                            '<td>' + priceDisplay + '</td>' +
                            '<td>' + costDisplay + '</td>' +
                            '<td>' + qtyDisplay + '</td>' +
                            '<td>' + mvDisplay + '</td>' +
                            '<td>' + upnlDisplay + '</td>' +
                            '<td style="font-size:12px;">' + timeDisplay + '</td>' +
                            '<td style="white-space:nowrap;">' +
                                '<button class="btn btn-sm" style="background:#2ecc71;color:white;" onclick="viewReport(' + s.id + ')">📊 报告</button> ' +
                                '<button class="btn btn-sm" style="background:#3498db;color:white;" onclick="viewData(' + s.id + ')">📋 查看数据</button> ' +
                                '<div style="position:relative;display:inline-block;">' +
                                    '<button class="btn btn-sm" style="background:var(--surface-2,#f0f0f0);border:1px solid var(--border,#ccc);" onclick="toggleStockMore(' + s.id + ', event)">⋯ 更多</button>' +
                                    '<div id="stockMore' + s.id + '" class="stock-more-menu" style="display:none;position:absolute;right:0;top:100%;z-index:100;background:var(--surface,#fff);border:1px solid var(--border,#ddd);border-radius:6px;box-shadow:0 4px 12px rgba(0,0,0,0.15);min-width:130px;padding:4px 0;">' +
                                        '<a href="javascript:void(0)" onclick="event.stopPropagation();addStockToHoldings(' + s.id + ',\'' + s.symbol + '\',\'' + (s.name||'').replace(/'/g,' ') + '\',\'' + s.market + '\')" style="display:block;padding:6px 16px;font-size:13px;color:var(--text,#333);text-decoration:none;">💰 加入持仓</a>' +
                                        '<a href="javascript:void(0)" onclick="event.stopPropagation();openStockEditModal(' + s.id + ',\'' + s.symbol + '\',\'' + (s.name||'').replace(/'/g,' ') + '\',' + (s.group_id||'null') + ')" style="display:block;padding:6px 16px;font-size:13px;color:var(--text,#333);text-decoration:none;">✏️ 编辑</a>' +
                                        '<a href="javascript:void(0)" onclick="event.stopPropagation();deleteStock(' + s.id + ')" style="display:block;padding:6px 16px;font-size:13px;color:#e74c3c;text-decoration:none;">🗑 删除</a>' +
                                    '</div>' +
                                '</div>' +
                                (s.latest_price == null ? '<div style="margin-top:4px;font-size:11px;color:#f39c12;">💡 首次使用请勾选后点「⚡ 批量分析+评级」</div>' : '') +
                            '</td>' +
                        '</tr>';
                    });
                    html += '</tbody></table>'
                    document.getElementById('stockList').innerHTML = html;
                } else {
                    document.getElementById('stockList').innerHTML =
                        '<div class="empty">还没有添加自选股，请在上方添加</div>';
                }
            })
            .catch(function(err) { console.error('loadStocks error:', err); });
    }

    // ========== 删除股票 ==========
    function deleteStock(id) {
        if (!confirm('确定要删除这只股票吗？相关数据也会被删除。')) return;
        fetch('/api/stocks/' + id, { method: 'DELETE' })
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    loadStocks();
                } else {
                    alert('删除失败：' + (data.message || '未知错误'));
                }
            })
            .catch(function(err) {
                alert('网络错误：' + err + '，请确认程序仍在运行');
            });
    }

    // ========== 录入持仓 ==========
    function editPosition(id) {
        const cost = prompt('请输入持仓成本价（如 10.50）：');
        if (cost === null || cost === '') return;
        const qty = prompt('请输入持仓数量（股）：');
        if (qty === null || qty === '') return;

        fetch('/api/stocks/' + id + '/position', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cost_price: parseFloat(cost) || 0, quantity: parseInt(qty) || 0 })
        })
        .then(r => r.json().then(d => ({status: r.status, data: d})))
        .then(res => {
            if (res.data.success) {
                alert('持仓信息已更新');
                loadStocks();
            } else {
                alert('保存失败：' + (res.data.message || '未知错误'));
            }
        })
        .catch(err => {
            alert('网络错误：' + err);
        });
    }

    // ========== 触发数据采集 ==========
    function collectData(id, symbol, market) {
        const area = document.getElementById('collectArea');
        area.innerHTML = '<div class="card">' +
            '<div class="card-title">数据采集结果：' + symbol + '</div>' +
            '<div class="loading">正在采集中，请稍候（可能需要10-30秒）...</div>' +
            '</div>';
        area.scrollIntoView({ behavior: 'smooth' });

        // 使用 AbortController 设置 120 秒超时
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 120000);

        fetch('/api/collect/' + id, { method: 'POST', signal: controller.signal })
            .then(r => r.json())
            .then(data => {
                clearTimeout(timeoutId);
                if (data.success) {
                    let html = '<div class="card">' +
                        '<div class="card-title">数据采集结果：' + data.symbol + '（' + (data.market === 'a_stock' ? 'A股' : '港股') + '）</div>';

                    for (const [dim, info] of Object.entries(data.results)) {
                        // 019E Task 4.2：增加 estimated 分支（复用 status-partial CSS 类）
                        // 019K Task 4：增加 fallback 分支（THS 顶替，复用 status-partial CSS 类）
                        const statusClass = info.status === 'success' ? 'status-success'
                            : (info.status === 'partial' || info.status === 'estimated' || info.status === 'fallback') ? 'status-partial'
                            : 'status-failed';
                        const statusText = info.status === 'success' ? '✅ 成功'
                            : info.status === 'partial' ? '⚠️ 部分成功'
                            : info.status === 'estimated' ? '⚠️ 估算'
                            : info.status === 'fallback' ? '⚠️ 顶替'
                            : '❌ 失败';
                        html += '<div class="dim-result">' +
                            '<span><strong>' + dim + '</strong></span>' +
                            '<span class="' + statusClass + '">' + statusText + ' — ' + info.message + '</span>' +
                            '</div>';
                    }

                    html += '<div style="margin-top: 16px;">' +
                        '<button class="btn btn-primary btn-sm" onclick="viewData(' + id + ')">查看采集到的数据</button>' +
                        '</div>';
                    html += '</div>';
                    area.innerHTML = html;
                    loadDbStats();
                } else {
                    area.innerHTML = '<div class="card"><div class="alert alert-error">采集失败：' + (data.message || '未知错误') + '</div></div>';
                }
            })
            .catch(err => {
                clearTimeout(timeoutId);
                let msg = err.name === 'AbortError' ? '采集超时（120秒），请稍后重试' : ('请求失败：' + err + '，请确认程序仍在运行');
                area.innerHTML = '<div class="card"><div class="alert alert-error">' + msg + '</div></div>';
            });
    }

    // ========== 四维分析引擎 ==========
    function analyzeStock(id, symbol) {
        const area = document.getElementById('collectArea');
        area.innerHTML = '<div class="card">' +
            '<div class="card-title">四维分析引擎 — ' + symbol + '</div>' +
            '<div class="loading">正在计算评分...</div>' +
            '</div>';
        area.scrollIntoView({ behavior: 'smooth' });

        fetch('/api/stocks/' + id + '/analyze', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    area.innerHTML = renderAnalysisResult(data);
                } else {
                    area.innerHTML = '<div class="card"><div class="alert alert-error">分析失败：' + (data.message || '未知错误') + '</div></div>';
                }
            })
            .catch(err => {
                area.innerHTML = '<div class="card"><div class="alert alert-error">请求失败：' + err + '</div></div>';
            });
    }

    function renderAnalysisResult(data) {
        const dims = data.dimensions || {};
        const dimNames = {
            kline: '技术面(K线)',
            fundamental: '基本面',
            capital_flow: '资金面',
            news: '消息面'
        };
        const dimColors = {
            kline: '#3498db',
            fundamental: '#2ecc71',
            capital_flow: '#e74c3c',
            news: '#9b59b6'
        };

        let html = '<div class="card">';
        html += '<div class="card-title">四维分析结果 — ' + (data.stock_name || data.stock_code) + ' (' + data.stock_code + ')</div>';

        // 总分展示区
        html += '<div class="score-hero">';
        html += '<span class="big-score">' + data.total_score.toFixed(1) + '</span>';
        html += '<span class="big-rating">' + data.rating + '</span>';
        html += '<div class="score-label">' + (data.rating_label || '') + ' | 评分日期: ' + data.score_date + '</div>';
        if (data.weight_rescaled) {
            html += '<div class="score-label" style="margin-top:4px;">权重已自适应归一化（部分维度数据缺失）</div>';
        }
        html += '</div>';

        // 各维度得分卡片
        html += '<div style="text-align:center; margin-bottom:16px;">';
        ['kline', 'fundamental', 'capital_flow', 'news'].forEach(key => {
            const dim = dims[key] || {};
            const isActive = dim.status === 'ok';
            const color = isActive ? dimColors[key] : '#aaa';
            html += '<div class="dim-card' + (isActive ? '' : ' inactive') + '">';
            html += '<div class="dim-name">' + dimNames[key] + '</div>';
            html += '<div class="dim-score" style="color:' + color + '">' + (dim.score || 0).toFixed(1) + '</div>';
            html += '<div class="dim-weight">权重 ' + ((dim.weight || 0) * 100).toFixed(0) + '%</div>';
            if (dim.status && dim.status !== 'ok') {
                html += '<div style="font-size:11px; color:#e67e22; margin-top:4px;">' + (dim.reason || dim.status) + '</div>';
            }
            // 极端情绪预警标识
            if (key === 'news' && dim.extreme_warning) {
                html += '<div style="font-size:12px;color:#e65100;font-weight:600;margin-top:4px;">⚠️ 情绪极端，请人工复核原文</div>';
            }
            // 因子明细
            if (dim.factors && Object.keys(dim.factors).length > 0) {
                html += '<div class="factors-list">';
                for (const [fk, fv] of Object.entries(dim.factors)) {
                    if (fk.startsWith('_') || fk === 'extreme_warning' || fk === 'extreme_warning_titles') continue;
                    html += '<div>' + fk + ': ' + fv + '</div>';
                }
                html += '</div>';
            }
            html += '</div>';
        });
        html += '</div>';

        // 消息面核心见解（结构化摘要，≤3句）
        if (data.news_summary) {
            html += '<div class="advice-card" style="border-left-color:#9b59b6;margin-top:12px;">';
            html += '<div class="advice-label">📰 消息面核心见解</div>';
            html += '<div class="advice-text">' + data.news_summary + '</div>';
            html += '<div style="margin-top:6px;font-size:12px;color:var(--text-3,#888);">完整新闻列表请前往「查看数据」页面</div>';
            html += '</div>';
        }

        // 数据截止日期标签
        html += '<div style="margin-top:12px;">';
        html += '<span style="font-size:13px; color:var(--text-2,#666); font-weight:600;">数据截止日:</span> ';
        const cutoffs = data.data_cutoff || {};
        for (const [dim, date] of Object.entries(cutoffs)) {
            html += '<span class="data-cutoff-tag">' + dim + ': ' + date + '</span>';
        }
        html += '</div>';

        html += '</div>';
        return html;
    }

    // ========== 模块3：评级与建议生成 ==========
    function generateAdvice(id, symbol) {
        const area = document.getElementById('collectArea');
        area.innerHTML = '<div class="card">' +
            '<div class="card-title">评级与建议生成 — ' + symbol + '</div>' +
            '<div class="loading">正在分析并生成建议...</div>' +
            '</div>';
        area.scrollIntoView({ behavior: 'smooth' });

        fetch('/api/stocks/' + id + '/advise', { method: 'POST' })
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    area.innerHTML = renderAdviceResult(data);
                    refreshDashboardIfLoaded();  // 同步看板批量评分表
                } else {
                    area.innerHTML = '<div class="card"><div class="alert alert-error">建议生成失败：' + (data.message || '未知错误') + '</div></div>';
                }
            })
            .catch(err => {
                area.innerHTML = '<div class="card"><div class="alert alert-error">请求失败：' + err + '</div></div>';
            });
    }

    // U6(#3): 更多操作下拉菜单
    function toggleStockMore(stockId, e) {
        if (e) e.stopPropagation();
        document.querySelectorAll('.stock-more-menu').forEach(function(m) {
            if (m.id !== 'stockMore' + stockId) m.style.display = 'none';
        });
        var menu = document.getElementById('stockMore' + stockId);
        if (menu) {
            menu.style.display = (menu.style.display === 'none') ? '' : 'none';
        }
    }
    document.addEventListener('click', function() {
        document.querySelectorAll('.stock-more-menu').forEach(function(m) { m.style.display = 'none'; });
    });

    function renderAdviceResult(data) {
        const dims = data.dimensions || {};
        const dimNames = {
            kline: '技术面', fundamental: '基本面',
            capital_flow: '资金面', news: '消息面'
        };
        const dimColors = {
            kline: '#3498db', fundamental: '#2ecc71',
            capital_flow: '#e74c3c', news: '#9b59b6'
        };

        // 操作建议样式映射
        const actionClassMap = {
            '买入': 'action-buy', '加仓': 'action-buy', '继续持有': 'action-hold',
            '持有': 'action-hold', '持有观望': 'action-hold', '关注': 'action-watch',
            '观望': 'action-watch', '考虑减仓': 'action-reduce', '减仓': 'action-reduce',
            '建议止损': 'action-sell', '回避': 'action-sell'
        };
        const actionClass = actionClassMap[data.action_advice] || 'action-watch';

        let html = '<div class="card">';
        html += '<div class="card-title">评级与建议 — ' + (data.stock_name || data.stock_code) + ' (' + data.stock_code + ')</div>';

        // 评级变更提示
        if (data.rating_changed) {
            html += '<div class="change-banner">评级变更：' + (data.previous_rating || '—') + ' → ' + data.rating + '</div>';
        }

        // 总分 + 操作建议
        html += '<div class="score-hero">';
        html += '<span class="big-score">' + data.total_score.toFixed(1) + '</span>';
        html += '<span class="big-rating">' + data.rating + '</span>';
        html += '<div class="score-label">' + (data.rating_label || '') + ' | 评级日期: ' + data.rating_date + '</div>';
        html += '<div style="margin-top:8px;"><span class="action-badge ' + actionClass + '">' + data.action_advice + '</span></div>';
        if (data.previous_score !== null && data.previous_score !== undefined) {
            const diff = data.total_score - data.previous_score;
            const sign = diff >= 0 ? '+' : '';
            html += '<div class="score-label">上次评分: ' + data.previous_score.toFixed(1) + ' (' + data.previous_rating + ') | 变化: ' + sign + diff.toFixed(1) + '</div>';
        }
        html += '</div>';

        // 建议详情
        html += '<div class="advice-card">';
        html += '<div class="advice-label">综合解读</div>';
        html += '<div class="advice-text">' + (data.advice_detail || '—') + '</div>';
        html += '</div>';

        // 仓位建议
        if (data.position_advice) {
            html += '<div class="advice-card" style="border-left-color:#27ae60;">';
            html += '<div class="advice-label">仓位建议</div>';
            html += '<div class="advice-text">' + data.position_advice + '</div>';
            html += '</div>';
        }

        // 最强/最弱维度对比
        if (data.strongest_dim && data.weakest_dim) {
            html += '<div class="dim-compare">';
            html += '<div class="dim-compare-item strong">';
            html += '<div style="font-size:12px;color:#2e7d32;">最强维度</div>';
            html += '<div style="font-size:18px;font-weight:bold;color:#2e7d32;">' + data.strongest_dim.name + '</div>';
            html += '<div style="font-size:14px;color:var(--text,#555);">' + data.strongest_dim.score.toFixed(1) + '分</div>';
            html += '</div>';
            html += '<div class="dim-compare-item weak">';
            html += '<div style="font-size:12px;color:#c62828;">待改善维度</div>';
            html += '<div style="font-size:18px;font-weight:bold;color:#c62828;">' + data.weakest_dim.name + '</div>';
            html += '<div style="font-size:14px;color:var(--text,#555);">' + data.weakest_dim.score.toFixed(1) + '分</div>';
            html += '</div>';
            html += '</div>';
        }

        // 风险提示
        if (data.risk_warnings && data.risk_warnings.length > 0) {
            html += '<div style="margin:12px 0;">';
            html += '<div style="font-size:14px;font-weight:600;color:#e65100;margin-bottom:6px;">风险提示（' + data.risk_warnings.length + '项）</div>';
            data.risk_warnings.forEach(function(risk) {
                html += '<div class="risk-item">' + risk + '</div>';
            });
            html += '</div>';
        }

        // 各维度得分卡片（复用模块2样式）
        html += '<div style="text-align:center; margin:16px 0;">';
        ['kline', 'fundamental', 'capital_flow', 'news'].forEach(function(key) {
            const dim = dims[key] || {};
            const isActive = dim.status === 'ok';
            const color = isActive ? dimColors[key] : '#aaa';
            html += '<div class="dim-card' + (isActive ? '' : ' inactive') + '">';
            html += '<div class="dim-name">' + dimNames[key] + '</div>';
            html += '<div class="dim-score" style="color:' + color + '">' + (dim.score || 0).toFixed(1) + '</div>';
            html += '<div class="dim-weight">权重 ' + ((dim.weight || 0) * 100).toFixed(0) + '%</div>';
            if (dim.status && dim.status !== 'ok') {
                html += '<div style="font-size:11px;color:#e67e22;margin-top:4px;">' + (dim.reason || dim.status) + '</div>';
            }
            // 极端情绪预警
            if (key === 'news' && dim.extreme_warning) {
                html += '<div style="font-size:12px;color:#e65100;font-weight:600;margin-top:4px;">⚠️ 情绪极端，请人工复核</div>';
            }
            html += '</div>';
        });
        html += '</div>';

        // 消息面核心见解（结构化摘要，≤3句）
        if (data.news_summary) {
            html += '<div class="advice-card" style="border-left-color:#9b59b6;margin-top:12px;">';
            html += '<div class="advice-label">📰 消息面核心见解</div>';
            html += '<div class="advice-text">' + data.news_summary + '</div>';
            html += '<div style="margin-top:6px;font-size:12px;color:var(--text-3,#888);">完整新闻列表请前往「查看数据」页面</div>';
            html += '</div>';
        }

        // 数据截止日
        html += '<div style="margin-top:8px;">';
        html += '<span style="font-size:13px;color:var(--text-2,#666);font-weight:600;">数据截止日:</span> ';
        const cutoffs = data.data_cutoff || {};
        for (const [dim, date] of Object.entries(cutoffs)) {
            html += '<span class="data-cutoff-tag">' + dim + ': ' + date + '</span>';
        }
        html += '</div>';

        html += '</div>';
        return html;
    }

    // ========== 查看采集到的数据 ==========
    function viewData(id) {
        const area = document.getElementById('collectArea');
        area.innerHTML = `<div class="card"><div class="card-title">数据详情</div><div class="loading">加载中...</div></div>`;

        Promise.all([
            fetch(`/api/stocks/${id}/kline`).then(r => r.json()),
            fetch(`/api/stocks/${id}/fundamental`).then(r => r.json()),
            fetch(`/api/stocks/${id}/capital`).then(r => r.json()),
            fetch(`/api/stocks/${id}/status`).then(r => r.json()),
            fetch(`/api/stocks/${id}/news`).then(r => r.json()),
            fetch(`/api/stocks/${id}/orderbook`).then(r => r.json()),
            fetch(`/api/stocks/${id}/valuation`).then(r => r.json()),
            fetch(`/api/stocks/${id}/restricted-release`).then(r => r.json()),
            fetch(`/api/stocks/${id}/forecast`).then(r => r.json()),
        ]).then(([kline, fund, capital, status, news, orderbook, valuation, restricted, forecast]) => {
            // 采集状态卡片（显示在自选股列表下方、数据卡片上方）
            let html = '<div class="card"><div class="card-title">📋 采集状态记录</div>';
            if (status.success && status.data.length > 0) {
                html += '<table><thead><tr><th>维度</th><th>状态</th><th>说明</th><th>时间</th></tr></thead><tbody>';
                status.data.forEach(d => {
                    // 019E Task 4.2：增加 estimated 分支（复用 status-partial CSS 类）
                    // 019K Task 4：增加 fallback 分支（THS 顶替，复用 status-partial CSS 类）
                    const sc = d.status === 'success' ? 'status-success' : (d.status === 'partial' || d.status === 'estimated' || d.status === 'fallback') ? 'status-partial' : 'status-failed';
                    const st = d.status === 'success' ? '✅成功' : d.status === 'partial' ? '⚠️部分' : d.status === 'estimated' ? '⚠️估算' : d.status === 'fallback' ? '⚠️顶替' : '❌失败';
                    const dimNames = { kline: 'K线', fundamental: '基本面', capital: '资金面', sentiment: '消息面' };
                    html += `<tr><td>${dimNames[d.dimension] || d.dimension}</td><td class="${sc}">${st}</td><td>${d.message || '—'}</td><td>${d.fetched_at}</td></tr>`;
                });
                html += '</tbody></table>';
            } else {
                html += '<div class="alert alert-info">暂无采集状态记录</div>';
            }
            html += '</div>';

            html += '<div class="card"><div class="card-title">数据详情</div>';

            // K线数据
            // 019Y T1：存在 mootdx 降级行时动态标注来源
            const hasMootdxKline = kline.success && kline.data && kline.data.some(d => d.data_source === 'mootdx');
            const klineSourceLabel = hasMootdxKline ? '来源：腾讯财经（前复权）＋mootdx（备用源）' : '来源：腾讯财经（前复权）';
            html += '<h4 style="margin: 16px 0 8px;">K线数据（最近20条）<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　' + klineSourceLabel + '</span></h4>';
            if (kline.success && kline.count > 0) {
                html += '<table><thead><tr><th>日期</th><th>开盘</th><th>收盘</th><th>最高</th><th>最低</th><th>成交量</th><th>涨跌幅</th></tr></thead><tbody>';
                kline.data.forEach(d => {
                    const color = d.pct_change && d.pct_change.startsWith('+') ? '#e74c3c' : '#27ae60';
                    const srcTag = d.data_source === 'mootdx' ? '<sup style="color:#e67e22;font-size:11px">mootdx</sup>' : '';
                    html += `<tr><td>${d.trade_date}${srcTag}</td><td>${d.open}</td><td>${d.close}</td><td>${d.high}</td><td>${d.low}</td><td>${d.volume}</td><td style="color:${color}">${d.pct_change || '-'}</td></tr>`;
                });
                html += '</tbody></table>';
                html += '<div style="font-size:12px;color:var(--text-3,#999);margin-top:4px;">数据来源：腾讯财经日K线接口（前复权），与同花顺/通达信等APP显示可能因复权方式不同而有差异</div>';
            } else {
                html += '<div class="alert alert-warning">暂无K线数据，请先点击"采集数据"</div>';
            }

            // 基本面数据
            // 019P：静态来源文案改动态（A-3 三通道·前端通道，019K L2484-2490 同型）
            // 按 data_source 去重生成表头来源文案；行级 <sup> 标注混合来源行；估值来源表头注明腾讯
            // 021W-2：PE/PB 列展示"当时估值"（百度历史估值，按财报期关联）；未采集时提示补采
            const fundSources = new Set((fund.data || []).map(d => d.data_source).filter(Boolean));
            const fundSrcParts = [];
            if (fundSources.has('sina_abstract')) fundSrcParts.push('新浪关键指标(abstract)');
            if (fundSources.has('sina_analysis_indicator')) fundSrcParts.push('新浪指标(analysis_indicator降级)');
            if (fundSources.has('em_hk')) fundSrcParts.push('港股东方财富(EM)');
            const fundSourceLabel = (fundSrcParts.length ? '来源：' + fundSrcParts.join('、') : '来源：未标注') + '；当时PE/PB：百度历史估值';
            const fundMixed = fundSrcParts.length > 1;
            html += '<h4 style="margin: 24px 0 8px;">基本面数据<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　' + fundSourceLabel + '</span></h4>';
            if (fund.success && fund.count > 0) {
                html += '<table><thead><tr><th>财报日期</th><th>ROE(%)</th><th>当时PE(TTM)</th><th>当时PB</th><th>毛利率(%)</th><th>净利率(%)</th><th>负债率(%)</th><th>营收增长(%)</th></tr></thead><tbody>';
                fund.data.forEach(d => {
                    // 019P：混合来源时行级标注（多源并存时显示，单源不显示避免噪音）
                    let srcSup = '';
                    if (fundMixed) {
                        if (d.data_source === 'sina_abstract') srcSup = '<sup style="color:#1a73e8;font-size:11px">新浪</sup>';
                        else if (d.data_source === 'sina_analysis_indicator') srcSup = '<sup style="color:#e67e22;font-size:11px">新浪降级</sup>';
                        else if (d.data_source === 'em_hk') srcSup = '<sup style="color:#1a73e8;font-size:11px">东财</sup>';
                    }
                    // 021W-2：当时估值 = 该财报期末（或之前最近交易日）的真实历史估值快照
                    const histDate = d.hist_valuation_date || '';
                    const histTag = histDate ? '<sup style="color:#1a73e8;font-size:11px">' + String(histDate).slice(2).replace('-', '/') + '</sup>' : '';
                    const peCell = histDate ? (d.hist_pe_ttm != null ? d.hist_pe_ttm + histTag : '—') : '—';
                    const pbCell = histDate ? (d.hist_pb != null ? d.hist_pb + histTag : '—') : '—';
                    html += `<tr><td>${d.report_date || '—'}${srcSup}</td><td>${d.roe ?? '—'}</td><td>${peCell}</td><td>${pbCell}</td><td>${d.gross_margin ?? '—'}</td><td>${d.net_margin ?? '—'}</td><td>${d.debt_ratio ?? '—'}</td><td>${d.revenue_growth ?? '—'}</td></tr>`;
                });
                html += '</tbody></table>';
                if (fund.valuation_history_ready) {
                    // 021W-2：已采集历史估值 → 说明口径
                    html += '<div style="font-size:12px;color:var(--text-3,#999);margin-top:4px;">当时PE/PB 为该财报期末（或之前最近交易日）的市场真实估值（百度历史估值，约每两周一个快照点）；实时估值见下方"估值数据"区块</div>';
                } else {
                    // 021W-2：未采集 → 提供补采入口（一次性拉全历史）
                    html += `<div class="alert alert-warning" style="margin-top:8px;">历史估值未采集，PE/PB 暂无法显示"当时估值"。<button onclick="collectValuationHistory(${id})" style="margin-left:8px;padding:4px 10px;cursor:pointer;">补采历史估值</button></div>`;
                }
            } else {
                html += '<div class="alert alert-warning">暂无基本面数据，请先点击"采集数据"</div>';
            }

            // 业绩预告（东财，A股；业绩预告是财报前的先行指标）
            const fcTypes = {
                '预增': {color: '#c62828', label: '预增↑'}, '略增': {color: '#e65100', label: '略增'},
                '扭亏': {color: '#c62828', label: '扭亏↑'}, '续盈': {color: '#2e7d32', label: '续盈'},
                '预减': {color: '#1565c0', label: '预减↓'}, '略减': {color: '#1565c0', label: '略减'},
                '首亏': {color: '#1565c0', label: '首亏↓'}, '续亏': {color: '#1565c0', label: '续亏'},
            };
            html += '<h4 style="margin: 24px 0 8px;">📢 业绩预告<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　来源：东财业绩预告（akshare）</span></h4>';
            if (forecast.success && forecast.count > 0) {
                html += '<table><thead><tr><th>报告期</th><th>指标</th><th>预告类型</th><th>预测数值</th><th>变动幅度</th><th>上年同期</th><th>公告日期</th></tr></thead><tbody>';
                forecast.data.forEach(f => {
                    const t = fcTypes[f.forecast_type] || {color: 'var(--text-2,#666)', label: f.forecast_type || '—'};
                    const typeTag = '<span style="color:' + t.color + ';font-weight:600;">' + t.label + '</span>';
                    const pctColor = (f.change_pct ?? 0) > 0 ? '#c62828' : (f.change_pct ?? 0) < 0 ? '#1565c0' : '#666';
                    html += '<tr><td>' + (f.report_period || '—') + '</td>' +
                        '<td>' + (f.indicator || '—') + '</td>' +
                        '<td>' + typeTag + '</td>' +
                        '<td>' + (f.forecast_value_fmt || '—') + '</td>' +
                        '<td style="color:' + pctColor + ';font-weight:600;">' + (f.change_pct_fmt || '—') + '</td>' +
                        '<td>' + (f.last_year_value_fmt || '—') + '</td>' +
                        '<td>' + (f.announce_date || '—') + '</td></tr>';
                    html += '<tr><td colspan="7" style="color:var(--text-2,#666);font-size:12px;background:var(--surface-alt,#fafafa);">' + (f.change_desc || '') + '</td></tr>';
                });
                html += '</tbody></table>';
            } else {
                html += '<div class="alert alert-info">最近三个报告期暂无业绩预告</div>';
            }

            // 业绩快报（东财 stock_yjkb_em，A股；020R-50，财报前点值预估）
            html += '<h4 style="margin: 24px 0 8px;">📋 业绩快报<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　来源：东财业绩快报（akshare）</span></h4>';
            if (forecast.express && forecast.express.length > 0) {
                html += '<table><thead><tr><th>报告期</th><th>每股收益</th><th>营业收入</th><th>营收同比</th><th>净利润</th><th>净利同比</th><th>公告日期</th></tr></thead><tbody>';
                forecast.express.forEach(e => {
                    const pctColor = (e.np_yoy ?? 0) > 0 ? '#c62828' : (e.np_yoy ?? 0) < 0 ? '#1565c0' : '#666';
                    html += '<tr><td>' + (e.report_period || '—') + '</td>' +
                        '<td>' + (e.eps != null ? e.eps : '—') + '</td>' +
                        '<td>' + (e.revenue_fmt || '—') + '</td>' +
                        '<td>' + (e.revenue_yoy_fmt || '—') + '</td>' +
                        '<td>' + (e.np_fmt || '—') + '</td>' +
                        '<td style="color:' + pctColor + ';font-weight:600;">' + (e.np_yoy_fmt || '—') + '</td>' +
                        '<td>' + (e.announce_date || '—') + '</td></tr>';
                });
                html += '</tbody></table>';
            } else {
                html += '<div class="alert alert-info">最近三个报告期暂无业绩快报</div>';
            }

            // 资金面数据
            // 019E Task 4.1：动态表头——存在估算行时标注“含估算兜底数据”
            // 019K Task 4：动态表头——存在 THS 顶替行时标注“同花顺顶替（全部资金口径）”
            // 019S：ths_total 已无新增行（存量 27 行按方案 b 处置为估算语义），
            // 该分支成无害死分支；保留展示逻辑仅为存量期诚实标注，勿删。
            // 019Q Task 4：动态表头——存在新浪顶替行时标注“新浪顶替（主力口径）”
            const hasEstimated = capital.success && capital.data && capital.data.some(d => d.is_estimated === 1);
            const hasThsFallback = capital.success && capital.data && capital.data.some(d => d.capital_source === 'ths_total');
            const hasSinaMain = capital.success && capital.data && capital.data.some(d => d.capital_source === 'sina_main');
            const hasWestock = capital.success && capital.data && capital.data.some(d => d.capital_source === 'westock');
            const sourceNotes = [];
            if (hasEstimated) sourceNotes.push('含估算兜底数据');
            if (hasThsFallback) sourceNotes.push('同花顺顶替（全部资金口径）');
            if (hasSinaMain) sourceNotes.push('新浪顶替（主力口径）');
            // 020N：资金面行级来源标注（东财/腾讯逐行混合时诚实标注）
            const capitalBaseSource = hasWestock ? '来源：东方财富/腾讯自选股' : '来源：东方财富';
            const capitalSourceLabel = sourceNotes.length ? capitalBaseSource + '（' + sourceNotes.join('、') + '）' : capitalBaseSource;
            html += '<h4 style="margin: 24px 0 8px;">资金面数据（最近10条）<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　' + capitalSourceLabel + '</span></h4>';
            if (capital.success && capital.count > 0) {
                // 020O：按数据形态条件渲染列——A股有四档分解（超大/大/中/小），
                // 港股有腾讯全资金净流入（TotalNetFlow，主力+散户主动净额）
                const hasTiers = capital.data.some(d => d.super_large_net != null || d.large_net != null);
                const hasTotalNet = capital.data.some(d => d.total_net_inflow != null);
                const tierHeader = hasTiers ? '<th>超大单</th><th>大单</th><th>中单</th><th>小单</th>' : '';
                const totalNetHeader = hasTotalNet ? '<th title="腾讯全资金净流入（主力+散户主动净额）">全资金净流入<sup style="color:var(--text-3,#999)">腾讯</sup></th>' : '';
                html += '<table><thead><tr><th>日期</th><th>主力净流入</th><th>主力净流入占比</th>' + tierHeader + totalNetHeader + '</tr></thead><tbody>';
                capital.data.forEach(d => {
                    const color = d.main_net_inflow > 0 ? '#e74c3c' : '#27ae60';
                    const medColor = d.medium_net > 0 ? '#e74c3c' : d.medium_net < 0 ? '#27ae60' : '#999';
                    const smColor = d.small_net > 0 ? '#e74c3c' : d.small_net < 0 ? '#27ae60' : '#999';
                    const totColor = d.total_net_inflow > 0 ? '#e74c3c' : d.total_net_inflow < 0 ? '#27ae60' : '#999';
                    // 019E Task 4.1：估算行追加标注（仅资金面表格，评分卡片不标注）
                    // 019K Task 4：THS 顶替行追加“同花顺”标注（口径提示：全部资金净流入，非主力）
                    // 019S：ths_total 已无新增行（存量 27 行按方案 b 处置），
                    // 该分支为无害死分支，保留仅为存量期诚实标注，勿删。
                    // 019Q Task 4：新浪顶替行追加“新浪”标注（口径提示：主力口径 r0+r1）
                    const estTag = d.is_estimated === 1 ? '<sup style="color:#e67e22;font-size:11px">估算</sup>' : '';
                    const thsTag = d.capital_source === 'ths_total' ? '<sup style="color:#1a73e8;font-size:11px">同花顺</sup>' : '';
                    const sinaTag = d.capital_source === 'sina_main' ? '<sup style="color:#8e44ad;font-size:11px">新浪</sup>' : '';
                    const tierCells = hasTiers
                        ? `<td>${d.super_large_net ?? '—'}</td><td>${d.large_net ?? '—'}</td><td style="color:${medColor}">${d.medium_net ?? '—'}</td><td style="color:${smColor}">${d.small_net ?? '—'}</td>`
                        : '';
                    const totalNetCell = hasTotalNet
                        ? `<td style="color:${totColor}">${d.total_net_inflow ?? '—'}</td>`
                        : '';
                    html += `<tr><td>${d.trade_date}</td><td style="color:${color}">${d.main_net_inflow ?? '—'}${estTag}${thsTag}${sinaTag}</td><td>${d.main_net_inflow_pct ?? '—'}%</td>${tierCells}${totalNetCell}</tr>`;
                });
                html += '</tbody></table>';
                // 020N：原「同花顺净额」辅助列不再展示——同花顺历史接口无法获取，缺失日无法补齐；
                // 且东财/腾讯四档净额互补恒等（超大+大+中+小≡0，散户=被动方口径），
                // 无法合成同花顺「净额」口径，为避免误导按用户裁定移除以保证口径真实。
                // 020O：港股另展示腾讯全资金净流入（TotalNetFlow，港股散户非被动镜像、有实际意义）。
                html += '<div style="font-size:12px;color:var(--text-3,#999);margin-top:4px;">主力净流入来源：东方财富/腾讯（超大单+大单）；A股超大/大/中/小四档净额来自同一数据源、逐日完整，四档合计为零（散户为被动方口径）；港股全资金净流入来自腾讯（主力+散户主动净额）</div>';
            } else {
                html += '<div class="alert alert-warning">暂无资金面数据，请先点击"采集数据"</div>';
            }

            // 019Y T1：五档盘口（mootdx 通达信实时行情，红涨绿跌）
            html += '<h4 style="margin: 24px 0 8px;">五档盘口<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　来源：mootdx（通达信实时行情）</span></h4>';
            if (orderbook.success && orderbook.count > 0) {
                const ob = orderbook.data[0];
                html += '<table><thead><tr><th>卖5</th><th>卖4</th><th>卖3</th><th>卖2</th><th>卖1</th><th>最新价</th><th>买1</th><th>买2</th><th>买3</th><th>买4</th><th>买5</th></tr></thead><tbody>';
                html += '<tr>';
                for (let l = 5; l >= 1; l--) {
                    const p = ob['ask' + l + '_price'], v = ob['ask' + l + '_vol'];
                    html += `<td style="color:#27ae60;">${p ?? '—'}<br><span style="color:var(--text-3,#999);font-size:11px;font-weight:normal;">${v ?? '—'}手</span></td>`;
                }
                const pctColor = (ob.pct_change ?? 0) >= 0 ? '#e74c3c' : '#27ae60';
                html += `<td style="background:#fff3e0;font-weight:bold;color:${pctColor};">${ob.latest_price ?? '—'}<br><span style="color:var(--text-3,#999);font-size:11px;font-weight:normal;">${ob.pct_change ?? '—'}%</span></td>`;
                for (let l = 1; l <= 5; l++) {
                    const p = ob['bid' + l + '_price'], v = ob['bid' + l + '_vol'];
                    html += `<td style="color:#e74c3c;">${p ?? '—'}<br><span style="color:var(--text-3,#999);font-size:11px;font-weight:normal;">${v ?? '—'}手</span></td>`;
                }
                html += '</tr></tbody></table>';
                html += `<div style="font-size:12px;color:var(--text-3,#999);margin-top:4px;">快照时间：${ob.quote_time || '—'}（${ob.trade_date || ''}）　数据来源：${ob.source || 'mootdx'}　量单位为手（1手=100股）</div>`;
            } else {
                html += '<div class="alert alert-warning">暂无五档盘口数据（mootdx，仅A股）</div>';
            }

            // 019Y T2：估值数据（akshare 主源 / baostock 备用源）
            const valSources = new Set((valuation.data || []).map(d => d.source).filter(Boolean));
            const valSrcParts = [];
            if (valSources.has('akshare')) valSrcParts.push('akshare');
            if (valSources.has('baostock')) valSrcParts.push('baostock');
            const valSourceLabel = valSrcParts.length ? '来源：' + valSrcParts.join('、') : '来源：未标注';
            html += '<h4 style="margin: 24px 0 8px;">估值数据（PE/PB/PS/PCF）<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　' + valSourceLabel + '</span></h4>';
            if (valuation.success && valuation.count > 0) {
                html += '<table><thead><tr><th>日期</th><th>PE(TTM)</th><th>PE(静)</th><th>PB</th><th>PS(TTM)</th><th>PCF(TTM)</th><th>股息率</th><th>来源</th></tr></thead><tbody>';
                valuation.data.forEach(d => {
                    const vTag = d.source === 'baostock' ? '<sup style="color:#e67e22;font-size:11px">备用</sup>' : '';
                    html += `<tr><td>${d.trade_date || '—'}</td><td>${d.pe_ttm ?? '—'}</td><td>${d.pe ?? '—'}</td><td>${d.pb_mrq ?? '—'}</td><td>${d.ps_ttm ?? '—'}</td><td>${d.pcf_ncf_ttm ?? '—'}</td><td>${d.dv_ttm ?? '—'}</td><td>${(d.source || '—')}${vTag}</td></tr>`;
                });
                html += '</tbody></table>';
                html += '<div style="font-size:12px;color:var(--text-3,#999);margin-top:4px;">PE=市盈率（股价÷每股收益），PB=市净率（股价÷每股净资产），PS=市销率（股价÷每股销售额），PCF=市现率（股价÷每股经营现金流）</div>';
            } else {
                html += '<div class="alert alert-warning">暂无估值数据，请先点击"采集数据"</div>';
            }

            // 019Y T2：限售解禁（风险因子，未来解禁高亮）
            const nowD = new Date();
            const todayStr = nowD.getFullYear() + '-' + String(nowD.getMonth() + 1).padStart(2, '0') + '-' + String(nowD.getDate()).padStart(2, '0');
            const fmtBig = (v) => {
                if (v === null || v === undefined || v === '') return '—';
                const n = Number(v);
                if (isNaN(n)) return '—';
                if (Math.abs(n) >= 1e8) return (n / 1e8).toFixed(2) + '亿';
                if (Math.abs(n) >= 1e4) return (n / 1e4).toFixed(2) + '万';
                return String(n);
            };
            html += '<h4 style="margin: 24px 0 8px;">限售解禁（风险因子）<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　来源：akshare（东方财富）</span></h4>';
            if (restricted.success && restricted.count > 0) {
                html += '<table><thead><tr><th>解禁日期</th><th>解禁类型</th><th>解禁数量</th><th>实际解禁数量</th><th>解禁市值</th><th>占总股本</th></tr></thead><tbody>';
                restricted.data.forEach(d => {
                    const isFuture = d.release_date >= todayStr;
                    const dateTag = isFuture ? '<sup style="color:#e74c3c;font-size:11px">未解禁</sup>' : '';
                    const ratio = d.release_ratio !== null && d.release_ratio !== undefined ? d.release_ratio + '%' : '—';
                    html += `<tr><td>${d.release_date || '—'}${dateTag}</td><td>${d.release_type || '—'}</td><td>${fmtBig(d.release_shares)}</td><td>${fmtBig(d.actual_shares)}</td><td>${fmtBig(d.actual_mv)}</td><td>${ratio}</td></tr>`;
                });
                html += '</tbody></table>';
                html += '<div style="font-size:12px;color:var(--text-3,#999);margin-top:4px;">解禁属于事件级风险因子：大额解禁可能带来抛压，红色"未解禁"标注为尚未解禁的批次</div>';
            } else {
                html += '<div class="alert alert-warning">暂无限售解禁数据</div>';
            }

            // 消息面原始数据（含可点击原文链接）
            html += '<h4 style="margin: 24px 0 8px;">📰 消息面原始数据<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　来源：东方财富新闻</span></h4>';
            if (news.success && news.news_count > 0) {
                if (news.extreme_warning) {
                    html += '<div class="alert alert-warning">⚠️ 检测到极端情绪（avg_sentiment≥0.95），建议人工复核原文！</div>';
                }
                // 情绪摘要
                if (news.sentiment_summary && news.sentiment_summary.length > 0) {
                    var ns = news.sentiment_summary[0];
                    html += '<div style="background:#f0f4ff;padding:8px 12px;border-radius:6px;margin-bottom:8px;font-size:13px;">';
                    html += '日均情绪: <strong>' + (ns.avg_sentiment || 0).toFixed(2) + '</strong>';
                    html += ' | 正面' + (ns.positive_count || 0) + '条';
                    html += ' 负面' + (ns.negative_count || 0) + '条';
                    html += ' 中性' + (ns.neutral_count || 0) + '条';
                    html += ' | 总计' + (ns.total_count || 0) + '条（去重后' + news.news_count + '条）';
                    html += '</div>';
                }
                // 新闻列表表格（含可点击跳转的原文URL）
                html += '<table><thead><tr><th>标题</th><th style="width:90px;">日期</th><th style="width:100px;">来源</th><th style="width:60px;">情绪</th><th style="width:50px;">得分</th></tr></thead><tbody>';
                news.news_list.forEach(function(item) {
                    var labelColor = item.sentiment_label === '正面' ? '#27ae60' : (item.sentiment_label === '负面' ? '#e74c3c' : '#999');
                    var titleDisplay = item.title.length > 50 ? item.title.substring(0, 50) + '...' : item.title;
                    var titleHtml = titleDisplay;
                    // 标题可点击跳转至原文URL
                    if (item.source_url) {
                        titleHtml = '<a href="' + item.source_url + '" target="_blank" style="color:#1a73e8;text-decoration:none;">' + titleDisplay + '</a>';
                    }
                    var sourceDisplay = item.source_name || '—';
                    if (item.source_url) {
                        sourceDisplay = '<a href="' + item.source_url + '" target="_blank" style="color:#1a73e8;">' + sourceDisplay + ' ↗</a>';
                    }
                    html += '<tr>';
                    html += '<td title="' + (item.title || '').replace(/"/g, '&quot;') + '">' + titleHtml + '</td>';
                    html += '<td style="color:var(--text-2,#666);">' + (item.info_date || '—') + '</td>';
                    html += '<td>' + sourceDisplay + '</td>';
                    html += '<td style="color:' + labelColor + ';font-weight:600;">' + item.sentiment_label + '</td>';
                    html += '<td style="color:' + labelColor + ';">' + (item.sentiment_score || 0).toFixed(2) + '</td>';
                    html += '</tr>';
                });
                html += '</tbody></table>';
            } else if (news.success && news.sentiment_summary && news.sentiment_summary.length > 0 && news.sentiment_summary[0].total_count === 0 && news.sentiment_summary[0].top_news_title === '今日无新增新闻') {
                // 已采集但无新数据（空标记记录）
                html += '<div class="alert alert-info" style="background:#e8f4fd;border:1px solid #b3d9f2;color:#0066cc;">';
                html += 'ℹ️ 今日无新增新闻（数据源今天没有该股票的新消息，采集正常完成）';
                html += '</div>';
            } else {
                html += '<div class="alert alert-warning">暂无消息面数据，请先执行消息面采集</div>';
            }

            html += '</div>';
            area.innerHTML = html;
            enableFoldSections(area);
            area.scrollIntoView({ behavior: 'smooth' });
        });
    }

    // ========== 021W-2：补采历史估值（百度股市通，A股） ==========
    function collectValuationHistory(stockId) {
        if (!confirm('补采历史估值？将获取百度股市通约每两周一个快照点的真实历史 PE/PB（覆盖 2000 年至今，每只股票一次性请求）。')) return;
        fetch(`/api/stocks/${stockId}/valuation-history/collect`, { method: 'POST' })
            .then(r => r.json())
            .then(d => {
                alert(d.message || '补采完成');
                if (d.success) viewData(stockId);
            })
            .catch(e => alert('补采失败：' + e));
    }

    // ========== 数据卡片收起/展开 ==========
    function enableFoldSections(container) {
        // 把"数据详情"卡片内的 h4 区块包装为可折叠 section：
        // 每个 h4 标题 + 其后续内容（直到下一个 h4）组成一个折叠区，点击标题切换收起/展开。
        // 默认收起：页面聚焦采集状态与概览，需要看具体数据时再展开。
        var cards = container.querySelectorAll('.card');
        for (var ci = 0; ci < cards.length; ci++) {
            var h4s = Array.prototype.slice.call(cards[ci].querySelectorAll('h4'));
            if (!h4s.length) continue;
            h4s.forEach(function(h) {
                var wrapper = document.createElement('div');
                wrapper.className = 'fold-section';
                var body = document.createElement('div');
                body.className = 'fold-body';
                // 收集 h4 之后（直到下一个 h4）的所有兄弟节点移入 body
                var next = h.nextElementSibling;
                while (next && next.tagName !== 'H4') {
                    var following = next.nextElementSibling;
                    body.appendChild(next);
                    next = following;
                }
                h.parentNode.insertBefore(wrapper, h);
                wrapper.appendChild(h);
                wrapper.appendChild(body);
                h.classList.add('fold-title');
                h.style.cursor = 'pointer';
                // 默认收起
                body.style.display = 'none';
                h.classList.add('fold-closed');
                h.onclick = function() {
                    var closed = body.style.display === 'none';
                    body.style.display = closed ? '' : 'none';
                    h.classList.toggle('fold-closed', !closed);
                };
            });
        }
    }

    // ========== 全选/批量分析 ==========
    function toggleAllStocks(master) {
        document.querySelectorAll('.stock-cb').forEach(function(cb) { cb.checked = master.checked; });
    }

    function toggleSelectAll() {
        var master = document.getElementById('selectAllStocks');
        toggleAllStocks(master);
    }

    // 021AZ：看板「待评分」卡片直达入口——自动勾选该股并走批量分析统一后端
    // /api/batch-analyze（替代原 oneClickAnalyze 前端三步链：/analyze 与 /advise
    // 内部都调 generate_advice，旧路径每只股票重复计算一次完整评分）
    function quickBatchAnalyze(stockId) {
        var cb = document.querySelector('.stock-cb[value="' + stockId + '"]');
        if (!cb) {
            alert('未找到该股票的勾选框，请先在自选股列表中加载后再试');
            return;
        }
        document.querySelectorAll('.stock-cb:checked').forEach(function(x) { x.checked = false; });
        cb.checked = true;
        batchAnalyze();
    }

    function batchAnalyze() {
        var ids = [];
        document.querySelectorAll('.stock-cb:checked').forEach(function(cb) {
            ids.push(parseInt(cb.value));
        });
        if (ids.length === 0) {
            alert('请先勾选要分析的股票');
            return;
        }
        if (ids.length > 20) {
            alert('单次最多批量处理20只股票（后端限制），请分批勾选');
            return;
        }

        var area = document.getElementById('collectArea');
        var total = ids.length;
        var startTime = Date.now();

        // 020R-61：改走后端 /api/batch-analyze（采集+分析+评级+资金面批量预取+行业补取），
        // 删除原前端手写"仅分析"串行循环；逐只执行中无实时进度，用静态进度 UI 说明
        area.innerHTML = '<div class="card"><div class="card-title">⚡ 批量分析与评级</div>' +
            '<div style="margin:16px 0 8px;font-size:14px;color:var(--text,#333);">正在处理 ' + total + ' 只股票（含数据采集，逐只执行，可能需要数分钟）...</div>' +
            '<div style="width:100%;height:22px;background:#e0e0e0;border-radius:11px;overflow:hidden;position:relative;">' +
            '<div id="batchProgressBar" style="width:30%;height:100%;background:repeating-linear-gradient(90deg,#43a047,#66bb6a 20px,#43a047 40px);border-radius:11px;"></div>' +
            '</div>' +
            '<div style="margin-top:6px;font-size:12px;color:var(--text-3,#888);" id="batchProgressPct">⏳ 逐只执行中，完成后自动显示结果</div>' +
            '</div>';
        area.scrollIntoView({ behavior: 'smooth' });

        fetch('/api/batch-analyze', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ stock_ids: ids })
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                var elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
                if (data.success) {
                    renderBatchResults(data, elapsed);
                } else {
                    area.innerHTML = '<div class="card"><div class="alert alert-error">批量分析失败：' +
                        (data.message || '未知错误') + '</div></div>';
                }
            })
            .catch(function(err) {
                area.innerHTML = '<div class="card"><div class="alert alert-error">请求失败：' + err + '</div></div>';
            })
            .finally(function() {
                loadStocks();  // 完成后同步列表评分/价格
                refreshDashboardIfLoaded();  // 同步看板批量评分表
            });
    }

    function renderBatchResults(data, elapsed) {
        var area = document.getElementById('collectArea');
        var html = '<div class="card"><div class="card-title">⚡ 批量分析完成（' + data.success_count + '/' + data.total + '成功，耗时' + elapsed + '秒）</div>';

        if (data.results && data.results.length > 0) {
            html += '<table><thead><tr><th>代码</th><th>名称</th><th>状态</th><th>评级</th><th>总分</th><th>操作建议</th><th>评级时间</th></tr></thead><tbody>';
            data.results.forEach(function(r) {
                var statusBadge = r.status === 'completed' ?
                    '<span class="status-success">✅ 完成</span>' :
                    '<span class="status-failed">❌ 失败</span>';
                var ratingBadge = r.rating ? '<span class="action-badge ' + ratingActionClass(r.rating) + '" style="padding:2px 8px;font-size:12px;" title="' + getRatingTitle(r.rating) + '">' + r.rating + '</span>' : '—';
                html += '<tr>' +
                    '<td><strong>' + (r.symbol || '—') + '</strong></td>' +
                    '<td>' + (r.name || '—') + '</td>' +
                    '<td>' + statusBadge + '</td>' +
                    '<td>' + ratingBadge + '</td>' +
                    '<td>' + (r.total_score != null ? r.total_score.toFixed(1) : '—') + '</td>' +
                    '<td style="font-size:12px;">' + (r.operation_suggestion || r.error || '—') + '</td>' +
                    '<td style="font-size:12px;color:var(--text-2,#666);">' + (r.rating_time || '—') + '</td>' +
                    '</tr>';
            });
            html += '</tbody></table>';
        }
        html += '</div>';
        area.innerHTML = html;
    }

    function ratingActionClass(rating) {
        // RATING-ALIGN-004：兼容新中文5档 + 历史A/B+/B/C/D
        var map = {
            '强烈推荐买入': 'action-buy', '推荐买入': 'action-buy',
            '持有观望': 'action-hold',
            '建议减仓': 'action-reduce', '强烈建议卖出': 'action-sell',
            'A': 'action-buy', 'B+': 'action-buy', 'B': 'action-hold', 'C': 'action-reduce', 'D': 'action-sell'
        };
        return map[rating] || 'action-watch';
    }

    // ========== 数据库统计 ==========
    function loadDbStats() {
        fetch('/api/db-stats')
            .then(r => r.json())
            .then(data => {
                if (data.success) {
                    console.log('数据库统计:', data.stats);
                }
            });
    }

    // ========== 持仓管理 ==========
    var currentPortfolioGroup = null;
    var currentTradeStockId = null;
    var allStocksCache = [];        // 缓存全部股票列表，供搜索使用
    var selectedStockId = null;     // 新增模式下当前选中的股票id
    var _searchTimer = null;        // 搜索防抖timer
    var _groupCache = [];           // 缓存持仓分组列表
    var currentEditGroupId = null;  // 编辑模式下当前持仓的 group_id

    // ========== 021S 多交易账户 ==========
    var currentAccountId = 'all';   // 当前账户范围：'all' 或账户id
    var _accountsCache = [];        // 缓存账户列表（切换器/弹窗/下拉共用）

    // 加载账户列表 → 填充切换器，随后加载分组与持仓
    function loadAccounts() {
        return fetch('/api/accounts')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                _accountsCache = (data.success ? data.accounts : []) || [];
                _renderAccountSwitcher();
                // 无论账户是否加载成功，都继续加载分组与持仓
                loadPortfolioGroups();
            })
            .catch(function(err) {
                console.error('loadAccounts:', err);
                loadPortfolioGroups();
            });
    }

    function _renderAccountSwitcher() {
        var sel = document.getElementById('accountSwitchSelect');
        if (!sel) return;
        var html = '<option value="all"' + (currentAccountId === 'all' ? ' selected' : '') + '>全部账户</option>';
        _accountsCache.forEach(function(a) {
            var isSel = (currentAccountId !== 'all' && a.id == currentAccountId);
            html += '<option value="' + a.id + '"' + (isSel ? ' selected' : '') + '>' +
                a.name + (a.holding_count > 0 ? ' (' + a.holding_count + ')' : '') + '</option>';
        });
        sel.innerHTML = html;
    }

    // 切换账户范围（'all' 或账户id字符串）
    function selectAccount(id) {
        currentAccountId = (id === 'all') ? 'all' : parseInt(id);
        _renderAccountSwitcher();
        loadPortfolioGroups();
    }

    // 账户下拉选项 HTML（持仓弹窗/流水表单共用）
    function _accountOptionsHtml(selectedAccountId, includeAll) {
        var html = includeAll ? '<option value="all"' + (!selectedAccountId || selectedAccountId === 'all' ? ' selected' : '') + '>按当前筛选</option>' : '';
        _accountsCache.forEach(function(a) {
            var isSel = (selectedAccountId != null && a.id == selectedAccountId);
            html += '<option value="' + a.id + '"' + (isSel ? ' selected' : '') + '>' + a.name + '</option>';
        });
        return html;
    }

    // 把 account_id 拼到 API 查询串
    function _accountQueryParam(sep) {
        return (currentAccountId === 'all' ? '' : sep + 'account_id=' + currentAccountId);
    }

    // ========== 账户管理弹窗 ==========
    function openAccountModal() {
        document.getElementById('newAccountName').value = '';
        document.getElementById('newAccountBroker').value = '';
        document.getElementById('newAccountNotes').value = '';
        document.getElementById('accountModal').style.display = 'block';
        renderAccountList();
    }

    function closeAccountModal() {
        document.getElementById('accountModal').style.display = 'none';
    }

    function renderAccountList() {
        fetch('/api/accounts')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success) return;
                _accountsCache = data.accounts || [];
                _renderAccountSwitcher();
                var box = document.getElementById('accountList');
                if (!_accountsCache.length) {
                    box.innerHTML = '<div class="empty">暂无账户</div>';
                    return;
                }
                var html = '<table><thead><tr><th>名称</th><th>券商</th><th>在仓持仓</th><th>流水</th><th>操作</th></tr></thead><tbody>';
                _accountsCache.forEach(function(a) {
                    var defTag = a.is_default ? ' <span style="font-size:10px;background:#27ae60;color:white;padding:1px 4px;border-radius:3px;">默认</span>' : '';
                    html += '<tr>' +
                        '<td><strong>' + a.name + '</strong>' + defTag + '</td>' +
                        '<td>' + (a.broker || '—') + '</td>' +
                        '<td>' + (a.holding_count || 0) + '</td>' +
                        '<td>' + (a.trade_count || 0) + '</td>' +
                        '<td style="white-space:nowrap;">' +
                            '<button class="btn btn-sm" onclick="renameAccountAction(' + a.id + ',\'' + a.name.replace(/'/g, ' ') + '\')">改名</button>' +
                            (a.is_default ? '' : '<button class="btn btn-danger btn-sm" onclick="deleteAccountAction(' + a.id + ',\'' + a.name.replace(/'/g, ' ') + '\')">删除</button>') +
                        '</td></tr>';
                });
                html += '</tbody></table>';
                box.innerHTML = html;
            });
    }

    function createAccountAction() {
        var name = document.getElementById('newAccountName').value.trim();
        if (!name) { alert('请输入账户名称'); return; }
        fetch('/api/accounts', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                name: name,
                broker: document.getElementById('newAccountBroker').value.trim(),
                notes: document.getElementById('newAccountNotes').value.trim()
            })
        })
        .then(function(r) { return safeJson(r); })
        .then(function(data) {
            if (data.success) {
                document.getElementById('newAccountName').value = '';
                document.getElementById('newAccountBroker').value = '';
                document.getElementById('newAccountNotes').value = '';
                renderAccountList();
            } else {
                alert('创建失败：' + (data.message || '未知错误'));
            }
        });
    }

    function renameAccountAction(accountId, oldName) {
        var newName = prompt('修改账户名称：', oldName);
        if (newName === null) return;
        newName = newName.trim();
        if (!newName || newName === oldName) return;
        fetch('/api/accounts/' + accountId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: newName })
        })
        .then(function(r) { return safeJson(r); })
        .then(function(data) {
            if (data.success) {
                renderAccountList();
            } else {
                alert('修改失败：' + (data.message || '未知错误'));
            }
        });
    }

    function deleteAccountAction(accountId, name) {
        if (!confirm('确定删除账户「' + name + '」？其下持仓将一并移除，流水保留并归入默认账户。')) return;
        fetch('/api/accounts/' + accountId, {
            method: 'DELETE',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ force_confirm: true })
        })
        .then(function(r) { return safeJson(r); })
        .then(function(data) {
            if (data.success) {
                renderAccountList();
                loadPortfolioGroups();
            } else {
                alert('删除失败：' + (data.message || '未知错误'));
            }
        });
    }
    // ========== 021S 多账户 END ==========

    // 加载分组列表到下拉选择框（静态HTML <select>）
    function _loadGroupOptions(selectedGroupId) {
        var sel = document.getElementById('holdingGroupSelect');
        if (!sel) return;
        return fetch('/api/groups?type=portfolio')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                _groupCache = (data.success ? data.groups : []) || [];
                var html = '<option value="">无分组</option>';
                _groupCache.forEach(function(g) {
                    var isSel = (selectedGroupId != null && g.id == selectedGroupId);
                    html += '<option value="' + g.id + '"' + (isSel ? ' selected' : '') + '>' + g.name + '</option>';
                });
                sel.innerHTML = html;
            });
    }

    function loadPortfolioGroups() {
        fetch('/api/groups?type=portfolio')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    // 021AO：Tab 栏只显示有持仓的组（空组在"管理分组"里维护）
                    var visible = (data.groups || []).filter(function(g) {
                        return (g.holding_count || 0) > 0;
                    });
                    if (currentPortfolioGroup !== null) {
                        var still = visible.some(function(g) { return g.id == currentPortfolioGroup; });
                        if (!still) currentPortfolioGroup = null;  // 选中组已空 → 回"全部"
                    }
                    var tabsHtml = '<button class="btn btn-sm" style="' +
                        (currentPortfolioGroup === null ? 'background:#1a73e8;color:white;' : 'background:#e0e0e0;') +
                        '" onclick="selectPortfolioGroup(null)">全部</button>';
                    visible.forEach(function(g) {
                        var active = currentPortfolioGroup == g.id;
                        tabsHtml += '<button class="btn btn-sm" style="' +
                            (active ? 'background:#1a73e8;color:white;' : 'background:#e0e0e0;') +
                            '" onclick="selectPortfolioGroup(' + g.id + ')">' + g.name + ' (' + g.holding_count + ')</button>';
                    });
                    document.getElementById('portfolioGroupTabs').innerHTML = tabsHtml;
                } else {
                    document.getElementById('portfolioGroupTabs').innerHTML = '<span style="color:var(--text-3,#999);font-size:13px;">暂无分组</span>';
                }
                // 无论分组是否成功，都独立加载持仓列表
                loadHoldings();
            })
            .catch(function(err) {
                console.error('loadPortfolioGroups:', err);
                document.getElementById('portfolioGroupTabs').innerHTML = '<span style="color:#e74c3c;font-size:13px;">分组加载失败</span>';
                loadHoldings();
            });
    }

    function selectPortfolioGroup(gid) {
        currentPortfolioGroup = gid;
        loadPortfolioGroups();
    }

    function createPortfolioGroup() {
        var name = document.getElementById('newGroupName').value.trim();
        if (!name) { alert('请输入分组名称'); return; }
        fetch('/api/portfolio/groups', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name })
        })
        .then(function(r) { return safeJson(r); })
        .then(function(data) {
            if (data.success) {
                document.getElementById('newGroupName').value = '';
                loadPortfolioGroups();
            } else {
                alert('创建失败：' + (data.message || '未知错误'));
            }
        });
    }

    // 获取分组名称文本
    function _getCurrentGroupName() {
        if (!currentPortfolioGroup) return '未分组';
        var tabs = document.getElementById('portfolioGroupTabs');
        var btns = tabs ? tabs.querySelectorAll('button') : [];
        for (var i = 0; i < btns.length; i++) {
            if (btns[i].style.background.indexOf('1a73e8') >= 0 && btns[i].textContent !== '全部') {
                return btns[i].textContent.replace(/ \(\d+\)$/, '');
            }
        }
        return '当前分组';
    }

    // ========== 账户概览汇总 ==========
    function loadPortfolioSummary() {
        var url = '/api/portfolio/summary';
        var sep = '?';
        if (currentPortfolioGroup) { url += sep + 'group_id=' + currentPortfolioGroup; sep = '&'; }
        url += _accountQueryParam(sep);
        fetch(url)
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success) return;

                // 总市值（中性色）
                var mvEl = document.getElementById('sumMarketValue');
                if (mvEl) {
                    mvEl.textContent = data.total_market_value != null
                        ? formatCNY(data.total_market_value)
                        : '--';
                }

                // 总浮动盈亏（红盈绿亏，统一formatPnl）
                var uEl = document.getElementById('sumUnrealized');
                if (uEl) {
                    uEl.textContent = formatPnl(data.total_unrealized_pnl);
                    uEl.style.color = pnlColor(data.total_unrealized_pnl);
                }

                // 021BL：累计已实现（含分红与卖出差额，券商口径的次要指标）
                var tEl = document.getElementById('sumTotalPnl');
                if (tEl) {
                    tEl.textContent = formatPnl(data.total_realized_pnl);
                    tEl.style.color = pnlColor(data.total_realized_pnl);
                }

                // 021S：全账户视图下渲染分账户汇总条
                var bar = document.getElementById('accountsBreakdownBar');
                if (bar) {
                    var bd = data.accounts_breakdown;
                    if (currentAccountId === 'all' && bd && bd.length > 1) {
                        var html = '';
                        bd.forEach(function(a) {
                            html += '<div style="background:var(--surface-alt,#f8f9fa);border:1px solid var(--border,#e0e0e0);border-radius:8px;' +
                                'padding:6px 12px;font-size:12px;cursor:pointer;" ' +
                                'onclick="selectAccount(' + a.account_id + ')" ' +
                                'title="点击切换到该账户">' +
                                '<strong>' + a.account_name + '</strong>' +
                                ' <span style="color:var(--text-2,#666);">市值</span> ' + (a.total_market_value != null ? formatCNY(a.total_market_value) : '--') +
                                // 021BL：持仓盈亏与已实现分开显示，各自按红涨绿跌独立着色
                                ' <span style="color:var(--text-2,#666);">持仓盈亏</span> <span style="color:' + pnlColor(a.total_unrealized_pnl) + ';">' +
                                formatPnl(a.total_unrealized_pnl) + '</span>' +
                                ' <span style="color:var(--text-2,#666);">已实现</span> <span style="color:' + pnlColor(a.total_realized_pnl) + ';">' +
                                formatPnl(a.total_realized_pnl) + '</span>' +
                                ' <span style="color:var(--text-3,#999);">' + a.active_count + '仓</span></div>';
                        });
                        bar.innerHTML = html;
                        bar.style.display = 'flex';
                    } else {
                        bar.style.display = 'none';
                        bar.innerHTML = '';
                    }
                }
            })
            .catch(function(err) { console.error('loadPortfolioSummary:', err); });
    }

    function loadHoldings() {
        var url = '/api/portfolio/holdings';
        var sep = '?';
        if (currentPortfolioGroup) { url += sep + 'group_id=' + currentPortfolioGroup; sep = '&'; }
        url += _accountQueryParam(sep);
        var addBtnHtml = '<div class="btn-add-holding" onclick="openAddHoldingModal()">＋ 添加持仓</div>';
        var showAccountCol = (currentAccountId === 'all');  // 021S：全账户视图显示账户列
        fetch(url)
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success && data.holdings.length > 0) {
                    _holdingsCache = data.holdings;
                    _sortRenderers['holdings'] = loadHoldings;
                    var st = _sortState['holdings'];
                    var rows = st ? sortTable(_holdingsCache, st.key, st.order) : _holdingsCache;
                    var html = addBtnHtml + '<table><thead><tr><th>市场</th><th>代码</th><th>名称</th>' +
                        (showAccountCol ? '<th>账户</th>' : '') +
                        '<th>分组</th><th>成本价</th>' +
                        sortableTh('holdings', 'latest_price', '最新价') +
                        '<th>价格时间</th><th>数量</th>' +
                        sortableTh('holdings', 'market_value', '市值') +
                        sortableTh('holdings', 'unrealized_pnl', '持仓盈亏') +
                        sortableTh('holdings', 'unrealized_pnl_pct', '盈亏比例') +
                        '<th>状态</th><th>操作</th></tr></thead><tbody>';
                    rows.forEach(function(h) {
                        var mTag = h.market === 'a_stock' ? '<span class="tag tag-a">A股</span>' : '<span class="tag tag-hk">港股</span>';
                        var statusTag = h.status === 'cleared'
                            ? '<span style="color:var(--text-3,#999);font-size:12px;">已清仓</span>'
                            : '<span style="color:#27ae60;font-size:12px;">持仓中</span>';
                        // 格式化盈亏的公共函数
                        function _fmtPnl(val) {
                            if (val === null || val === undefined || isNaN(val)) return '<span style="color:var(--text-3,#999);font-size:12px;">--</span>';
                            if (val === 0) return '<span style="color:var(--text-3,#999);font-size:12px;">0.00</span>';
                            var sign = val > 0 ? '+' : '';
                            var color = val > 0 ? '#e74c3c' : '#27ae60';
                            return '<span style="color:' + color + ';font-weight:600;">' + sign + Math.abs(val).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + '</span>';
                        }
                        var pnl = h.realized_pnl || 0;
                        var pnlDisplay = pnl !== 0
                            ? '<span style="color:' + (pnl > 0 ? '#e74c3c' : '#27ae60') + ';font-weight:600;">' + (pnl > 0 ? '+' : '') + Math.abs(pnl).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + '</span>'
                            : '<span style="color:var(--text-3,#999);font-size:12px;">暂无</span>';
                        // 021BL：持仓盈亏（券商口径主列），悬停附累计已实现
                        var realizedTip = '累计已实现盈亏（含分红/卖出差额）：' +
                            (pnl > 0 ? '+' : '') + pnl.toFixed(2) + ' 元';
                        var unrealizedTip = '持仓盈亏=(现价-摊薄成本)×数量；' + realizedTip;
                        var unrealizedDisplay = '<span style="color:var(--text-3,#999);font-size:12px;cursor:help;" title="' + unrealizedTip + '">--</span>';
                        if (h.unrealized_pnl != null && !isNaN(h.unrealized_pnl)) {
                            unrealizedDisplay = '<span style="cursor:help;" title="' + unrealizedTip + '">' + _fmtPnl(h.unrealized_pnl) + '</span>';
                        }
                        // 盈亏比例（券商口径：相对摊薄成本）
                        var pctDisplay = '<span style="color:var(--text-3,#999);font-size:12px;">--</span>';
                        if (h.unrealized_pnl_pct != null && !isNaN(h.unrealized_pnl_pct)) {
                            var pv = h.unrealized_pnl_pct;
                            pctDisplay = '<span style="color:' + (pv > 0 ? '#e74c3c' : pv < 0 ? '#27ae60' : 'var(--text-3,#999)') + ';font-weight:600;">' +
                                (pv > 0 ? '+' : '') + pv.toFixed(2) + '%</span>';
                        }
                        // 成本显示：已修正加标签
                        var adjustedTag = h.is_cost_adjusted
                            ? ' <span style="font-size:10px;background:#6c5ce7;color:white;padding:1px 4px;border-radius:3px;">已修正</span>'
                            : '';
                        var costDisplay = Number(h.cost_price || 0).toFixed(2) + adjustedTag;
                        // 最新价显示
                        var priceDisplay = '—';
                        if (h.latest_price != null) {
                            var pColor = '#333';
                            if (h.price_pct_change != null) {
                                pColor = h.price_pct_change > 0 ? '#e74c3c' : h.price_pct_change < 0 ? '#27ae60' : '#333';
                            }
                            priceDisplay = '<span style="color:' + pColor + ';font-weight:600;">' + h.latest_price.toFixed(2) + '</span>';
                        }
                        // 价格时间显示
                        var timeDisplay = '—';
                        if (h.price_updated_at) {
                            // 格式化为 MM-DD HH:mm
                            var parts = h.price_updated_at.split(/[- :]/);
                            if (parts.length >= 5) {
                                timeDisplay = parts[1] + '-' + parts[2] + ' ' + parts[3] + ':' + parts[4];
                            }
                            if (h.price_expired) {
                                timeDisplay = '<span style="color:var(--text-3,#999);">' + timeDisplay + ' ⚠️</span>';
                            }
                        }
                        // 精确市值显示（基于 latest_price，强制元单位）
                        var mvDisplay = '—';
                        if (h.market_value != null && !isNaN(h.market_value)) {
                            var mvTip = '基于实时行情精确计算';
                            if (h.price_updated_at) mvTip += '，更新时间：' + h.price_updated_at;
                            mvDisplay = '<span style="font-weight:600;cursor:help;" title="' + mvTip + '">' + formatCNY(h.market_value) + '</span>';
                        } else if (h.market_value === null) {
                            mvDisplay = '<span style="color:var(--text-3,#999);font-size:12px;">--</span>';
                        }
                        html += '<tr>' +
                            '<td>' + mTag + '</td>' +
                            '<td><strong>' + h.symbol + '</strong></td>' +
                            '<td>' + (h.name || '—') + '</td>' +
                            (showAccountCol ? '<td>' + (h.account_name || '—') + '</td>' : '') +
                            '<td>' + (h.group_name || '—') + '</td>' +
                            '<td>' + costDisplay + '</td>' +
                            '<td>' + priceDisplay + '</td>' +
                            '<td style="font-size:12px;">' + timeDisplay + '</td>' +
                            '<td>' + (h.quantity || 0) + '</td>' +
                            '<td>' + mvDisplay + '</td>' +
                            '<td>' + unrealizedDisplay + '</td>' +
                            '<td>' + pctDisplay + '</td>' +
                            '<td>' + statusTag + '</td>' +
                            '<td style="white-space:nowrap;">' +
                                '<button class="btn btn-primary btn-sm" onclick="openEditHoldingModal(' + h.stock_id + ',' + (h.cost_price||0) + ',' + (h.quantity||0) + ',\'' + (h.notes||'').replace(/'/g,'\\\'') + '\',\'' + (h.group_name||'') + '\',' + (h.group_id||'null') + ',' + (h.account_id||'null') + ')">编辑</button>' +
                                '<button class="btn btn-sm" style="background:#f39c12;color:white;" onclick="openCostAdjustModal(' + h.id + ',\'' + (h.symbol||'').replace(/'/g,' ') + ' ' + (h.name||'').replace(/'/g,' ') + '\',' + (h.cost_price||0) + ')">✎ 修正成本</button>' +
                                '<button class="btn btn-success btn-sm" onclick="openTradeModal(' + h.stock_id + ',\'' + (h.name||h.symbol).replace(/'/g,' ') + '\')">流水</button>' +
                                '<button class="btn btn-sm" style="background:#6c5ce7;color:white;" onclick="syncHoldingToWatchlist(' + h.stock_id + ',\'' + (h.symbol||'').replace(/'/g,' ') + '\',\'' + (h.name||'').replace(/'/g,' ') + '\',\'' + (h.market||'a_stock') + '\')">同步到自选</button>' +
                                '<button class="btn btn-danger btn-sm" onclick="deleteHoldingById(' + h.stock_id + ',' + (h.account_id||'null') + ')">删除</button>' +
                            '</td>' +
                        '</tr>';
                    });
                    html += '</tbody></table>';
                    document.getElementById('holdingsList').innerHTML = html;
                    // 更新数据状态标签
                    _updateDataStatus(data.holdings);
                    // 更新账户概览
                    loadPortfolioSummary();
                } else {
                    document.getElementById('holdingsList').innerHTML =
                        '<div class="empty" style="margin-bottom:0;">暂无持仓记录</div>' + addBtnHtml;
                    loadPortfolioSummary();
                }
            })
            .catch(function(err) {
                console.error('loadHoldings:', err);
                // 即使API失败，也保证渲染添加按钮
                document.getElementById('holdingsList').innerHTML =
                    '<div class="alert alert-warning" style="margin-bottom:8px;">持仓加载失败，请检查服务是否正常</div>' + addBtnHtml;
            });
    }

    // ========== 新增持仓弹窗（含股票搜索）==========

    function openAddHoldingModal() {
        document.getElementById('holdingMode').value = 'add';
        document.getElementById('holdingModalTitle').textContent = '添加持仓';
        document.getElementById('holdingStockId').value = '';
        selectedStockId = null;

        // 显示搜索框，隐藏删除按钮
        document.getElementById('stockSearchRow').style.display = 'flex';
        document.getElementById('stockSearchInput').value = '';
        document.getElementById('stockSearchInput').style.display = 'block';
        document.getElementById('stockSearchResults').style.display = 'none';
        document.getElementById('stockSearchResults').innerHTML = '';
        document.getElementById('selectedStockDisplay').style.display = 'none';
        document.getElementById('selectedStockDisplay').textContent = '';
        document.getElementById('deleteHoldingBtn').style.display = 'none';

        // 清空表单
        document.getElementById('holdingCost').value = '';
        document.getElementById('holdingQty').value = '';
        document.getElementById('holdingNotes').value = '';
        // 重置预填 UI
        _resetPrefillUI();
        document.getElementById('existingHoldingWarning').style.display = 'none';
        document.getElementById('costDeviationWarning').style.display = 'none';
        // 加载分组下拉（默认选中当前Tab分组）
        _loadGroupOptions(currentPortfolioGroup);
        // 021S：账户下拉（新增模式可选；默认=当前筛选账户，'all' 时取默认账户）
        var accSel = document.getElementById('holdingAccountSelect');
        accSel.disabled = false;
        accSel.innerHTML = _accountOptionsHtml(currentAccountId === 'all' ? null : currentAccountId, false);

        document.getElementById('holdingModal').style.display = 'block';

        // 加载股票列表到缓存
        if (allStocksCache.length === 0) {
            fetch('/api/stocks')
                .then(function(r) { return safeJson(r); })
                .then(function(data) {
                    if (data.success) allStocksCache = data.stocks || [];
                });
        }

        // 自动聚焦搜索框
        setTimeout(function() { document.getElementById('stockSearchInput').focus(); }, 100);
    }

    function openEditHoldingModal(stockId, cost, qty, notes, groupName, groupId, accountId) {
        document.getElementById('holdingMode').value = 'edit';
        document.getElementById('holdingModalTitle').textContent = '编辑持仓';
        document.getElementById('holdingStockId').value = stockId;
        selectedStockId = stockId;
        currentEditGroupId = groupId || null;

        // 隐藏搜索框，显示删除按钮
        document.getElementById('stockSearchRow').style.display = 'flex';
        document.getElementById('stockSearchInput').style.display = 'none';
        document.getElementById('stockSearchResults').style.display = 'none';
        document.getElementById('selectedStockDisplay').style.display = 'inline';

        // 显示股票名称
        var stock = null;
        for (var i = 0; i < allStocksCache.length; i++) {
            if (allStocksCache[i].id == stockId) { stock = allStocksCache[i]; break; }
        }
        var displayText = stock ? stock.symbol + ' ' + (stock.name || '') : '#' + stockId;
        document.getElementById('selectedStockDisplay').textContent = displayText;

        document.getElementById('holdingCost').value = cost || '';
        document.getElementById('holdingQty').value = qty || '';
        document.getElementById('holdingNotes').value = notes || '';
        // 编辑模式：重置预填 UI，不显示预填标签
        _resetPrefillUI();
        document.getElementById('existingHoldingWarning').style.display = 'none';
        document.getElementById('costDeviationWarning').style.display = 'none';
        document.getElementById('tradeTypeRow').style.display = 'none'; // 编辑模式隐藏交易类型
        // 编辑模式：需传入手仓记录的 group_id 来预选下拉
        // 通过当前持仓数据查找 group_id
        _loadGroupOptions(currentEditGroupId).then(function() {
            if (currentEditGroupId) {
                document.getElementById('holdingGroupSelect').value = currentEditGroupId;
            }
        });
        // 021S：编辑模式账户不可变更（持仓归属账户固定，避免误移动）
        var accSelEdit = document.getElementById('holdingAccountSelect');
        accSelEdit.innerHTML = _accountOptionsHtml(accountId, false);
        accSelEdit.disabled = true;
        document.getElementById('deleteHoldingBtn').style.display = 'block';

        document.getElementById('holdingModal').style.display = 'block';
    }

    // 股票搜索联想（输入框 onkeyup 调用）
    function searchStockForHolding() {
        if (_searchTimer) clearTimeout(_searchTimer);
        _searchTimer = setTimeout(function() {
            _doSearchStock();
        }, 250);
    }

    function _doSearchStock() {
        var keyword = document.getElementById('stockSearchInput').value.trim().toLowerCase();
        var results = document.getElementById('stockSearchResults');

        if (keyword.length < 1) {
            results.style.display = 'none';
            results.innerHTML = '';
            return;
        }

        // 如果缓存为空，先加载
        if (allStocksCache.length === 0) {
            fetch('/api/stocks')
                .then(function(r) { return safeJson(r); })
                .then(function(data) {
                    if (data.success) {
                        allStocksCache = data.stocks || [];
                        _doSearchStock(); // 递归
                    }
                });
            return;
        }

        // 过滤匹配
        var matches = allStocksCache.filter(function(s) {
            return (s.symbol && s.symbol.toLowerCase().indexOf(keyword) >= 0) ||
                   (s.name && s.name.toLowerCase().indexOf(keyword) >= 0);
        }).slice(0, 10); // 最多显示10条

        if (matches.length === 0) {
            results.innerHTML = '<div style="padding:12px;color:var(--text-3,#999);text-align:center;font-size:13px;">未找到匹配的股票</div>';
            results.style.display = 'block';
            return;
        }

        var html = '';
        matches.forEach(function(s) {
            var tag = s.market === 'a_stock' ? 'A股' : '港股';
            html += '<div class="stock-suggestion" onclick="selectStockForHolding(' + s.id + ',\'' + s.symbol + '\',\'' + (s.name || '').replace(/'/g,' ') + '\',\'' + tag + '\')">' +
                '<span class="ss-symbol">' + s.symbol + '</span>' +
                '<span class="ss-name">' + (s.name || '—') + '</span>' +
                '<span class="ss-tag">' + tag + '</span>' +
                '</div>';
        });
        results.innerHTML = html;
        results.style.display = 'block';
    }

    function selectStockForHolding(stockId, symbol, name, marketTag) {
        selectedStockId = stockId;
        document.getElementById('holdingStockId').value = stockId;

        // 切换为选中状态：隐藏搜索框，显示选中的股票
        document.getElementById('stockSearchInput').style.display = 'none';
        document.getElementById('stockSearchResults').style.display = 'none';
        var display = document.getElementById('selectedStockDisplay');
        display.innerHTML = '<span class="ss-symbol" style="font-size:16px;">' + symbol + '</span> ' +
            '<span style="color:var(--text,#333);">' + name + '</span> ' +
            '<span class="tag tag-' + (marketTag === '港股' ? 'hk' : 'a') + '" style="margin-left:4px;">' + marketTag + '</span>' +
            ' <a href="javascript:void(0)" onclick="resetStockSearch()" style="color:#e74c3c;font-size:12px;margin-left:8px;">重新选择</a>';
        display.style.display = 'inline';

        // 聚焦成本价输入框
        document.getElementById('holdingCost').focus();
    }

    function resetStockSearch() {
        selectedStockId = null;
        document.getElementById('holdingStockId').value = '';
        document.getElementById('stockSearchInput').value = '';
        document.getElementById('stockSearchInput').style.display = 'block';
        document.getElementById('stockSearchResults').style.display = 'none';
        document.getElementById('selectedStockDisplay').style.display = 'none';
        setTimeout(function() { document.getElementById('stockSearchInput').focus(); }, 50);
    }

    // 点击页面其他地方关闭搜索结果
    document.addEventListener('click', function(e) {
        var results = document.getElementById('stockSearchResults');
        var input = document.getElementById('stockSearchInput');
        if (results && input && e.target !== input && !results.contains(e.target)) {
            results.style.display = 'none';
        }
    });

    function closeHoldingModal() {
        document.getElementById('holdingModal').style.display = 'none';
    }

    // ========== 自选股 <-> 持仓 数据同步 ==========

    // 预填状态跟踪
    var _prefillData = null;  // 存储当前预填的原始数据
    var _costForceConfirmed = false;  // 成本偏离是否已二次确认

    // 重置所有预填 UI 元素
    function _resetPrefillUI() {
        _prefillData = null;
        _costForceConfirmed = false;
        // 隐藏所有预填标签
        var tags = ['tradeTypeTag', 'costTag', 'qtyTag', 'suggestionTag'];
        tags.forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.style.display = 'none';
        });
        // 移除预填背景色
        ['holdingTradeType', 'holdingCost', 'holdingQty'].forEach(function(id) {
            var el = document.getElementById(id);
            if (el) el.classList.remove('prefill-bg');
        });
        // 显示交易类型行
        var tradeTypeRow = document.getElementById('tradeTypeRow');
        if (tradeTypeRow) tradeTypeRow.style.display = 'flex';
    }

    // ========== 预填埋点 SDK (P3) ==========
    function _trackPrefill(eventType, stockId, detail) {
        try {
            fetch('/api/analytics/prefill', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ event_type: eventType, stock_id: stockId, detail: detail || '' })
            }).catch(function() {});  // 静默失败，不干扰用户操作
        } catch(e) {}
    }

    // 从自选股列表「加入持仓」：预填股票信息 + 计划数量/目标成本 + 历史推荐，打开弹窗
    function addStockToHoldings(stockId, symbol, name, market) {
        // 确保缓存中有这只股票（含 planned_quantity / target_cost）
        var stock = null;
        for (var i = 0; i < allStocksCache.length; i++) {
            if (allStocksCache[i].id == stockId) { stock = allStocksCache[i]; break; }
        }
        if (!stock && name && symbol) {
            stock = { id: stockId, symbol: symbol, name: name, market: market };
            allStocksCache.push(stock);
        }

        // 先检查是否已有活跃持仓
        fetch('/api/portfolio/holdings')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                var existing = data.holdings ? data.holdings.find(function(h) { return h.stock_id == stockId; }) : null;
                var hasActive = existing && (existing.quantity || 0) > 0;

                // 打开新增模式弹窗
                openAddHoldingModal();

                // 预填交易类型=buy
                var tradeTypeSelect = document.getElementById('holdingTradeType');
                if (tradeTypeSelect) {
                    tradeTypeSelect.value = 'buy';
                    tradeTypeSelect.classList.add('prefill-bg');
                    var tag = document.getElementById('tradeTypeTag');
                    if (tag) tag.style.display = 'inline-block';
                }

                // 自动选中这只股票（模拟搜索选择流程，锁定代码）
                setTimeout(function() {
                    var mTag = market === 'a_stock' ? 'A股' : '港股';
                    selectStockForHolding(stockId, symbol, name || '', mTag);
                    // 锁定股票：隐藏「重新选择」链接
                    var display = document.getElementById('selectedStockDisplay');
                    if (display) {
                        display.innerHTML = '<span style="font-size:16px;">🔒 ' +
                            '<span class="ss-symbol">' + symbol + '</span> ' +
                            '<span style="color:var(--text,#333);">' + (name || '') + '</span> ' +
                            '<span class="tag tag-' + (mTag === '港股' ? 'hk' : 'a') + '">' + mTag + '</span></span>';
                    }
                }, 200);

                // 预填计划数量
                var plannedQty = stock && stock.planned_quantity ? stock.planned_quantity : null;
                if (plannedQty) {
                    document.getElementById('holdingQty').value = plannedQty;
                    document.getElementById('holdingQty').classList.add('prefill-bg');
                    var qtyTag = document.getElementById('qtyTag');
                    if (qtyTag) qtyTag.style.display = 'inline-block';
                }

                // 预填目标成本
                var targetCost = stock && stock.target_cost ? stock.target_cost : null;
                if (targetCost) {
                    document.getElementById('holdingCost').value = targetCost;
                    document.getElementById('holdingCost').classList.add('prefill-bg');
                    var costTag = document.getElementById('costTag');
                    if (costTag) costTag.style.display = 'inline-block';
                }

                // 显示追加买入提示
                if (hasActive) {
                    document.getElementById('existingHoldingWarning').style.display = 'block';
                }

                // 存储预填状态
                _prefillData = {
                    plannedQty: plannedQty,
                    targetCost: targetCost,
                    hasActive: hasActive,
                    latestPrice: existing ? existing.latest_price : null
                };

                // 绑定输入监听：修改后移除预填标签
                _bindPrefillListeners();

                // 埋点：预填已展示
                _trackPrefill('prefill_shown', stockId, 'qty=' + (plannedQty || 'null') + ',cost=' + (targetCost || 'null'));

                // P1: 历史流水智能推荐（异步，不阻塞预填）
                fetch('/api/portfolio/holdings/' + stockId + '/trade-suggestion')
                    .then(function(r) { return safeJson(r); })
                    .then(function(data) {
                        if (data.success && data.suggestion && data.suggestion.count > 0) {
                            _prefillData.suggestion = data.suggestion;
                            _showTradeSuggestion(data.suggestion, stockId);
                        }
                    }).catch(function() {});
            });
    }

    // 展示历史推荐提示条（不强制预填，供用户参考）
    function _showTradeSuggestion(suggestion, stockId) {
        var warn = document.getElementById('costDeviationWarning');
        if (!warn) return;
        // 仅在没有已显示的警告时才显示推荐提示
        if (warn.style.display === 'block') return;
        var msg = '📊 历史参考：近 ' + suggestion.count + ' 次买入均价 ' + suggestion.avg_price +
            '，均量 ' + suggestion.avg_quantity + ' 股（' + suggestion.latest_trade_date + '）';
        warn.innerHTML = msg +
            ' <button class="btn btn-sm" style="margin-left:8px;padding:2px 8px;" onclick="_applySuggestion()">采纳</button>';
        warn.style.background = '#e8f5e9';
        warn.style.borderColor = '#4caf50';
        warn.style.color = '#2e7d32';
        warn.style.display = 'block';
    }

    function _applySuggestion() {
        if (!_prefillData || !_prefillData.suggestion) return;
        document.getElementById('holdingCost').value = _prefillData.suggestion.avg_price;
        document.getElementById('holdingQty').value = _prefillData.suggestion.avg_quantity;
        var costTag = document.getElementById('costTag');
        if (costTag) { costTag.textContent = '历史参考'; costTag.style.display = 'inline-block'; }
        var qtyTag = document.getElementById('qtyTag');
        if (qtyTag) { qtyTag.textContent = '历史参考'; qtyTag.style.display = 'inline-block'; }
        document.getElementById('holdingCost').classList.add('prefill-bg');
        document.getElementById('holdingQty').classList.add('prefill-bg');
        // 隐藏提示条
        var warn = document.getElementById('costDeviationWarning');
        warn.style.display = 'none';
        _trackPrefill('suggestion_applied', null, 'avg=' + _prefillData.suggestion.avg_price);
    }

    // 绑定预填字段修改监听：用户修改后移除「自动填充」标签
    function _bindPrefillListeners() {
        var costInput = document.getElementById('holdingCost');
        var qtyInput = document.getElementById('holdingQty');
        var typeSelect = document.getElementById('holdingTradeType');
        var _tracked = {};

        if (costInput) costInput.oninput = function() {
            var tag = document.getElementById('costTag');
            if (tag) tag.style.display = 'none';
            costInput.classList.remove('prefill-bg');
            if (!_tracked.cost) { _trackPrefill('field_modified', null, 'cost'); _tracked.cost = true; }
        };
        if (qtyInput) qtyInput.oninput = function() {
            var tag = document.getElementById('qtyTag');
            if (tag) tag.style.display = 'none';
            qtyInput.classList.remove('prefill-bg');
            if (!_tracked.qty) { _trackPrefill('field_modified', null, 'qty'); _tracked.qty = true; }
        };
        if (typeSelect) typeSelect.onchange = function() {
            // 切换交易类型时移除标签（保持值）
            var tag = document.getElementById('tradeTypeTag');
            if (tag) tag.style.display = 'none';
            typeSelect.classList.remove('prefill-bg');
            if (!_tracked.type) { _trackPrefill('field_modified', null, 'type'); _tracked.type = true; }
        };
    }

    // 从持仓列表「同步到自选」：检查是否已在自选股，若不在则添加
    function syncHoldingToWatchlist(stockId, symbol, name, market) {
        fetch('/api/stocks')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    var exists = data.stocks.some(function(s) { return s.id == stockId; });
                    if (exists) {
                        alert(symbol + ' 已在自选股列表中');
                        return;
                    }
                    // 不在自选股中，需要添加
                    // 但持仓的股票一定在 stocks 表中（holdings INNER JOIN stocks）
                    // 所以实际上这个分支不太会触发，但作为安全检查保留
                    alert(symbol + ' 已在自选股列表中');
                }
            })
            .catch(function(err) {
                alert('检查失败：' + err);
            });
    }

    function saveHolding() {
        var mode = document.getElementById('holdingMode').value;
        var stockId = document.getElementById('holdingStockId').value;

        // 新增模式下必须选择股票
        if (mode === 'add' && !stockId) {
            alert('请先搜索并选择一只股票');
            document.getElementById('stockSearchInput').focus();
            return;
        }

        var cost = parseFloat(document.getElementById('holdingCost').value) || 0;
        var qty = parseInt(document.getElementById('holdingQty').value) || 0;
        var notes = document.getElementById('holdingNotes').value.trim();
        var gid = document.getElementById('holdingGroupSelect').value || null;
        // 021S：归属账户（编辑模式下拉已禁用，回传原账户）
        var accVal = document.getElementById('holdingAccountSelect').value;
        var accId = (accVal && accVal !== 'all') ? parseInt(accVal) : null;

        if (cost < 0 || qty < 0) {
            alert('成本价和数量不能为负数');
            return;
        }

        // 成本合理性校验（仅新增模式 + 预填场景）
        if (mode === 'add' && _prefillData && !_costForceConfirmed) {
            var marketPrice = _prefillData.latestPrice;
            if (marketPrice && marketPrice > 0) {
                // 有市价：偏离超50%触发二次确认
                var deviation = Math.abs(cost - marketPrice) / marketPrice;
                if (deviation > 0.5) {
                    var warn = document.getElementById('costDeviationWarning');
                    warn.innerHTML = '⚠ 输入成本价 ' + cost.toFixed(2) + ' 偏离最新市价 ' + marketPrice.toFixed(2) +
                        ' 达 ' + (deviation * 100).toFixed(1) + '%，请确认无误后再次点击「保存」';
                    warn.style.display = 'block';
                    _costForceConfirmed = true;
                    document.getElementById('saveHoldingBtn').textContent = '⚠ 确认成本无误，再次保存';
                    _trackPrefill('cost_confirm_triggered', stockId ? parseInt(stockId) : null, 'deviation=' + (deviation*100).toFixed(1) + '%');
                    return;  // 阻止提交，等待用户二次确认
                }
            } else {
                // 无市价：降级为成本 ≤ 0 提示
                if (cost <= 0 && qty > 0) {
                    var warn2 = document.getElementById('costDeviationWarning');
                    warn2.innerHTML = '⚠ 未获取到最新市价，且成本价为0。请确认成本价正确后再次点击「保存」';
                    warn2.style.display = 'block';
                    _costForceConfirmed = true;
                    document.getElementById('saveHoldingBtn').textContent = '⚠ 确认成本无误，再次保存';
                    return;
                }
            }
        }
        // 重置二次确认状态
        _costForceConfirmed = false;
        document.getElementById('costDeviationWarning').style.display = 'none';
        document.getElementById('saveHoldingBtn').textContent = '保存';

        fetch('/api/portfolio/holdings/' + stockId, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cost_price: cost, quantity: qty, group_id: gid, notes: notes, account_id: accId })
        })
        .then(function(r) { return safeJson(r); })
        .then(function(data) {
            if (data.success) {
                closeHoldingModal();
                loadHoldings();
                loadPortfolioGroups(); // 刷新Tab上的计数
            } else {
                alert('保存失败：' + (data.message || '未知错误'));
            }
        })
        .catch(function(err) {
            alert('网络错误：' + err);
        });
    }

    function deleteHolding() {
        var stockId = document.getElementById('holdingStockId').value;
        if (!confirm('确定删除此持仓？交易流水将保留。')) return;
        // 021S：编辑弹窗内删除时带上当前选中账户
        var accVal = document.getElementById('holdingAccountSelect').value;
        deleteHoldingById(stockId, (accVal && accVal !== 'all') ? parseInt(accVal) : null);
        closeHoldingModal();
    }

    function deleteHoldingById(stockId, accountId) {
        if (!confirm('确定删除此持仓？交易流水将保留。')) return;
        var qs = (accountId != null && accountId !== 'null') ? ('?account_id=' + accountId) : '';
        fetch('/api/portfolio/holdings/' + stockId + qs, { method: 'DELETE' })
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    loadHoldings();
                    loadPortfolioGroups();
                } else if (data.holdings && data.holdings.length > 1) {
                    // 021S：多账户同股未指定账户 → 提示选择
                    var opts = data.holdings.map(function(h) { return h.account_id + ': ' + (h.account_name || ('账户' + h.account_id)); }).join('\n');
                    alert('该股票在多个账户均有持仓，请先在对应账户视图下删除：\n' + opts);
                } else {
                    alert('删除失败：' + (data.message || '未知错误'));
                }
            });
    }

    // ========== 成本修正 + 价格刷新 ==========

    var _caForceConfirm = false;  // 是否已二次确认

    function openCostAdjustModal(holdingId, stockName, currentCost) {
        _caForceConfirm = false;
        document.getElementById('caHoldingId').value = holdingId;
        document.getElementById('caStockName').textContent = stockName;
        document.getElementById('caCurrentCost').textContent = currentCost;
        document.getElementById('caNewCost').value = '';
        document.getElementById('caReason').value = '';
        document.getElementById('caCustomReasonRow').style.display = 'none';
        document.getElementById('caCustomReason').value = '';
        document.getElementById('caWarning').style.display = 'none';
        document.getElementById('caSubmitBtn').textContent = '确认修正';
        document.getElementById('costAdjustModal').style.display = 'block';
    }

    function closeCostAdjustModal() {
        document.getElementById('costAdjustModal').style.display = 'none';
        _caForceConfirm = false;
    }

    // 原因选择切换：选中“其他”时显示自定义输入框
    document.addEventListener('change', function(e) {
        if (e.target && e.target.id === 'caReason') {
            var row = document.getElementById('caCustomReasonRow');
            row.style.display = (e.target.value === '其他') ? 'flex' : 'none';
        }
    });

    function submitCostAdjustment() {
        var holdingId = document.getElementById('caHoldingId').value;
        var newCost = parseFloat(document.getElementById('caNewCost').value);
        var reason = document.getElementById('caReason').value;
        if (reason === '其他') {
            reason = document.getElementById('caCustomReason').value.trim();
        }

        if (isNaN(newCost) || newCost < 0) {
            alert('修正值不能为空且不能为负'); return;
        }
        if (!reason) {
            alert('请选择或输入修正原因'); return;
        }

        var body = {
            adjusted_avg_cost: newCost,
            adjustment_reason: reason,
            force_confirm: _caForceConfirm
        };

        fetch('/api/positions/' + holdingId + '/cost-adjustment', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
        .then(function(r) {
            // 捕获 HTTP 状态码
            var status = r.status;
            return r.json().then(function(d) { d._status = status; return d; });
        })
        .then(function(data) {
            if (data.success) {
                closeCostAdjustModal();
                loadHoldings();
                _showToast('成本已修正：' + data.adjustment.old_cost + ' → ' + data.adjustment.new_cost);
            } else if (data.need_force_confirm) {
                // 偏离超 30%，需二次确认
                var warn = document.getElementById('caWarning');
                warn.innerHTML = '⚠ ' + data.message + '<br>再次点击「确认修正」以强制提交。';
                warn.style.display = 'block';
                document.getElementById('caSubmitBtn').textContent = '⚠ 强制确认修正';
                _caForceConfirm = true;
            } else if (data._status === 429) {
                alert(data.message || '操作过于频繁，请稍后重试');
            } else {
                alert(data.message || '修正失败');
            }
        })
        .catch(function(err) { alert('网络错误：' + err); });
    }

    /** 021AD：合并原「刷新」按钮职责——联网取价 + 全页重载（分组Tab/持仓/汇总/自选股）。
     *  原并排的「刷新」（loadPortfolioGroups 纯本地重读）是其弱子集：所有改数据的
     *  操作（流水/持仓/账户/分组）本就自动调用相应重载，该按钮无独立价值，已删除。
     *  取价失败时仍做本地重载，完整承接原「刷新」的兜底职责。 */
    function refreshPrices() {
        var btn = document.getElementById('refreshPricesBtn');
        if (btn) { btn.disabled = true; btn.textContent = '🔄 刷新中...'; }
        fetch('/api/portfolio/refresh-prices', { method: 'POST' })
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    // 构建详细提示
                    var msg = data.message;
                    if (data.data_source) {
                        msg += ' | 来源：' + (data.data_source === 'tencent_realtime' ? '腾讯实时行情' : data.data_source);
                    }
                    if (data.realtime_count != null && data.fallback_count != null) {
                        msg += '（实时' + data.realtime_count + '条，缓存' + data.fallback_count + '条）';
                    }
                    if (data.fetch_duration_ms != null) {
                        msg += ' 耗时' + data.fetch_duration_ms + 'ms';
                    }
                    _showToast(msg);
                    loadPortfolioGroups();  // 级联 loadHoldings → 汇总卡/状态标签，含分组Tab计数
                    loadStocks();           // 同步刷新自选股列表价格
                } else {
                    alert('刷新失败：' + (data.message || '未知错误'));
                    loadPortfolioGroups();  // 价格失败仍本地重载（原「刷新」职责）
                }
            })
            .catch(function(err) {
                console.error('[refreshPrices] 网络错误:', err);
                _showToast('刷新失败：网络错误，旧价格已保留');
                loadPortfolioGroups();      // 网络失败仍本地重载（原「刷新」职责）
            })
            .finally(function() {
                if (btn) { btn.disabled = false; btn.textContent = '🔄 刷新价格'; }
            });
    }

    // 根据持仓数据更新数据状态标签
    function _updateDataStatus(holdings) {
        var tag = document.getElementById('priceDataStatus');
        if (!tag || !holdings || holdings.length === 0) return;

        var realtimeCount = holdings.filter(function(h) { return h.data_status === 'realtime'; }).length;
        var cacheCount = holdings.filter(function(h) { return h.data_status === 'cache'; }).length;
        var offlineCount = holdings.filter(function(h) { return h.data_status === 'offline'; }).length;
        var total = holdings.length;

        var label, bg, color;
        if (offlineCount === total) {
            label = '离线'; bg = '#f5f5f5'; color = '#999';
        } else if (realtimeCount === total) {
            label = '实时'; bg = '#e8f5e9'; color = '#2e7d32';
        } else if (cacheCount > 0 && realtimeCount > 0) {
            label = '混合'; bg = '#fff3e0'; color = '#e65100';
        } else if (cacheCount > 0) {
            label = '缓存'; bg = '#fff3e0'; color = '#e65100';
        } else {
            label = '实时'; bg = '#e8f5e9'; color = '#2e7d32';
        }
        tag.textContent = label;
        tag.style.background = bg;
        tag.style.color = color;
        tag.style.display = 'inline-block';
    }

    // ========== 交易流水弹窗 ==========

    // 021AM：流水类型标签/配色（含红利补税）
    function tradeTypeLabel(t) {
        if (t === 'buy') return '买入';
        if (t === 'sell') return '卖出';
        if (t === 'dividend') return '分红';
        if (t === 'dividend_tax') return '红利补税';
        return t;
    }
    function tradeTypeColor(t) {
        if (t === 'buy') return '#e74c3c';
        if (t === 'sell') return '#27ae60';
        if (t === 'dividend') return '#f39c12';
        if (t === 'dividend_tax') return '#8e44ad';
        return '#666';
    }

    function openTradeModal(stockId, stockName) {
        currentTradeStockId = stockId;
        document.getElementById('tradeStockName').textContent = stockName;
        document.getElementById('tradeDate').value = new Date().toISOString().slice(0, 10);
        // 021S：新增流水归属账户下拉（默认=当前筛选账户，'all' 时取默认账户）
        var tAccSel = document.getElementById('tradeAccountSelect');
        if (tAccSel) {
            tAccSel.innerHTML = _accountOptionsHtml(currentAccountId === 'all' ? null : currentAccountId, false);
            tAccSel.disabled = false;
        }
        document.getElementById('tradeModal').style.display = 'block';
        loadTrades(stockId);
    }

    function closeTradeModal() {
        document.getElementById('tradeModal').style.display = 'none';
        currentTradeStockId = null;
    }

    function loadTrades(stockId) {
        fetch('/api/portfolio/holdings/' + stockId + '/trades')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success && data.trades.length > 0) {
                    var html = '<table><thead><tr><th>类型</th><th>账户</th><th>价格</th><th>数量</th><th>金额</th><th>手续费</th><th>日期</th><th>备注</th><th>操作</th></tr></thead><tbody>';
                    data.trades.forEach(function(t) {
                        var typeLabel = tradeTypeLabel(t.trade_type);
                        var typeColor = tradeTypeColor(t.trade_type);
                        html += '<tr><td style="color:' + typeColor + ';font-weight:600;">' + typeLabel + '</td>' +
                            '<td style="font-size:12px;color:var(--text-2,#666);">' + (t.account_name || '—') + '</td>' +
                            '<td>' + (t.price || '—') + '</td><td>' + (t.quantity || '—') + '</td>' +
                            '<td>' + (t.amount ? t.amount.toFixed(2) : '—') + '</td>' +
                            '<td>' + (t.commission ? (t.commission_estimated ? '<span title="系统自动估算，隔天交割单出来后可编辑为实际值" style="color:var(--text-3,#999);">≈</span>' : '') + Number(t.commission).toFixed(2) : '<span style="color:var(--text-3,#999);">—</span>') + '</td>' +
                            '<td>' + (t.trade_date || '—') + '</td><td>' + (t.notes || '—') + '</td>' +
                            '<td>' +
                                '<button class="btn btn-sm" style="background:#6c757d;color:white;" onclick="editTrade(' + t.id + ',\'' + t.trade_type + '\',' + (t.price||0) + ',' + (t.quantity||0) + ',\'' + (t.trade_date||'') + '\',\'' + (t.notes||'').replace(/'/g,' ') + '\',' + (t.commission||0) + ',' + (t.amount||0) + ')">编辑</button>' +
                                '<button class="btn btn-danger btn-sm" onclick="deleteTrade(' + t.id + ')">删除</button>' +
                            '</td></tr>';
                    });
                    html += '</tbody></table>';
                    document.getElementById('tradeRecords').innerHTML = html;
                } else {
                    document.getElementById('tradeRecords').innerHTML = '<div class="empty">暂无交易记录</div>';
                }
            });
    }

    // 编辑流水：填充表单，切换为编辑模式（021AM：含 amount 回填）
    var _editingTradeId = null;
    function editTrade(tradeId, tradeType, price, qty, tradeDate, notes, commission, amount) {
        _editingTradeId = tradeId;
        document.getElementById('tradeType').value = tradeType;
        document.getElementById('tradePrice').value = price || '';
        document.getElementById('tradeQty').value = qty || '';
        document.getElementById('tradeAmount').value = (amount != null && amount !== 0) ? amount : '';
        document.getElementById('tradeCommission').value = (commission != null && commission !== 0) ? commission : '';
        document.getElementById('tradeDate').value = tradeDate || '';
        document.getElementById('tradeNotes').value = notes || '';
        // 切换按钮为“保存修改”
        var btn = document.getElementById('addTradeBtn');
        btn.textContent = '保存修改';
        btn.setAttribute('onclick', 'saveEditTrade()');
        // 显示取消编辑按钮
        var cancelBtn = document.getElementById('cancelEditBtn');
        if (cancelBtn) cancelBtn.style.display = 'inline-block';
    }

    function cancelEditTrade() {
        _editingTradeId = null;
        document.getElementById('tradeType').value = 'buy';
        document.getElementById('tradePrice').value = '';
        document.getElementById('tradeQty').value = '';
        document.getElementById('tradeAmount').value = '';
        document.getElementById('tradeCommission').value = '';
        document.getElementById('tradeNotes').value = '';
        var btn = document.getElementById('addTradeBtn');
        btn.textContent = '添加记录';
        btn.setAttribute('onclick', 'addTrade()');
        var cancelBtn = document.getElementById('cancelEditBtn');
        if (cancelBtn) cancelBtn.style.display = 'none';
    }

    function saveEditTrade() {
        if (!_editingTradeId) return;
        var tradeType = document.getElementById('tradeType').value;
        var price = parseFloat(document.getElementById('tradePrice').value) || 0;
        var qty = parseInt(document.getElementById('tradeQty').value) || 0;
        var amount = parseFloat(document.getElementById('tradeAmount').value) || 0;  // 021AM：金额直填
        var commission = parseFloat(document.getElementById('tradeCommission').value) || 0;
        var tradeDate = document.getElementById('tradeDate').value;
        var notes = document.getElementById('tradeNotes').value.trim();

        if (!tradeDate) { alert('请选择交易日期'); return; }
        if ((tradeType === 'dividend' || tradeType === 'dividend_tax') && !amount && !price) {
            alert('请填写金额（或 成交价×数量）'); return;
        }

        fetch('/api/portfolio/trades/' + _editingTradeId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ trade_type: tradeType, price: price, quantity: qty, amount: amount || null, commission: commission, trade_date: tradeDate, notes: notes })
        })
        .then(function(r) {
            var status = r.status;
            return r.json().then(function(d) { d._status = status; return d; });
        })
        .then(function(data) {
            if (data.success) {
                cancelEditTrade();             // 重置为新增模式
                loadTrades(currentTradeStockId); // 刷新流水列表
                loadHoldings();                 // 刷新持仓列表
                loadPortfolioGroups();          // 刷新 Tab 计数
                if (data.recalculated_position) {
                    var p = data.recalculated_position;
                    _showToast('持仓已更新：数量 ' + p.quantity + ' · 均价 ' + p.avg_cost +
                        (p.realized_pnl ? ' · 已实现盈亏 ' + (p.realized_pnl > 0 ? '+' : '') + Math.abs(p.realized_pnl).toLocaleString('zh-CN', {minimumFractionDigits: 2, maximumFractionDigits: 2}) : ''));
                }
            } else if (data._status === 403) {
                if (data.message && data.message.indexOf('二次验证') >= 0) {
                    // 大额流水二次验证：用户确认后带 force_confirm=true 重试
                    if (confirm('该流水金额较大，需二次验证。确认继续修改吗？')) {
                        fetch('/api/portfolio/trades/' + _editingTradeId, {
                            method: 'PUT',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ trade_type: tradeType, price: price, quantity: qty, amount: amount || null, commission: commission, trade_date: tradeDate, notes: notes, force_confirm: true })
                        })
                        .then(function(r) { return r.json(); })
                        .then(function(d) {
                            if (d.success) {
                                cancelEditTrade();
                                loadTrades(currentTradeStockId);
                                loadHoldings();
                                loadPortfolioGroups();
                            } else {
                                alert('修改失败：' + (d.message || '未知错误'));
                            }
                        });
                    }
                } else {
                    alert('⛔ 操作被限制：' + (data.message || '此流水不允许修改'));
                }
            } else {
                alert('修改失败：' + (data.message || '未知错误'));
            }
        });
    }

    var _deleteTradeConfirmId = null;
    function deleteTrade(tradeId) {
        // 长按确认：第一次点击提示，再次确认才执行
        if (_deleteTradeConfirmId !== tradeId) {
            _deleteTradeConfirmId = tradeId;
            var btn = event.target;
            var origText = btn.textContent;
            btn.textContent = '⚠ 再点一次确认';
            btn.style.background = '#c0392b';
            setTimeout(function() {
                if (_deleteTradeConfirmId === tradeId) {
                    btn.textContent = origText;
                    btn.style.background = '';
                    _deleteTradeConfirmId = null;
                }
            }, 3000);
            return;
        }
        _deleteTradeConfirmId = null;

        fetch('/api/portfolio/trades/' + tradeId, { method: 'DELETE' })
            .then(function(r) {
                var status = r.status;
                return r.json().then(function(d) { d._status = status; return d; });
            })
            .then(function(data) {
                if (data.success) {
                    loadTrades(currentTradeStockId);
                    loadHoldings();
                    loadPortfolioGroups();
                    if (data.recalculated_position) {
                        var p = data.recalculated_position;
                        _showToast('持仓已重算：数量 ' + p.quantity + ' · 状态 ' + (p.status === 'cleared' ? '已清仓' : '持仓中'));
                    }
                } else if (data._status === 403) {
                    if (data.message && data.message.indexOf('二次验证') >= 0) {
                        // 大额流水二次验证：用户确认后带 force_confirm=true 重试
                        if (confirm('该流水金额较大，需二次验证。确认继续删除吗？')) {
                            fetch('/api/portfolio/trades/' + tradeId, {
                                method: 'DELETE',
                                headers: { 'Content-Type': 'application/json' },
                                body: JSON.stringify({ force_confirm: true })
                            })
                            .then(function(r) { return r.json(); })
                            .then(function(d) {
                                if (d.success) {
                                    loadTrades(currentTradeStockId);
                                    loadHoldings();
                                    loadPortfolioGroups();
                                } else {
                                    alert('删除失败：' + (d.message || '未知错误'));
                                }
                            });
                        }
                    } else {
                        alert('⛔ 操作被限制：' + (data.message || '此流水不允许删除'));
                    }
                } else {
                    alert('删除失败：' + (data.message || '未知错误'));
                }
            });
    }

    function addTrade() {
        if (!currentTradeStockId) return;
        // 如果在编辑模式，走编辑保存
        if (_editingTradeId) { saveEditTrade(); return; }

        var tradeType = document.getElementById('tradeType').value;
        var price = parseFloat(document.getElementById('tradePrice').value) || 0;
        var qty = parseInt(document.getElementById('tradeQty').value) || 0;
        var amount = parseFloat(document.getElementById('tradeAmount').value) || 0;  // 021AM：金额直填
        var commission = parseFloat(document.getElementById('tradeCommission').value) || 0;
        var tradeDate = document.getElementById('tradeDate').value;
        var notes = document.getElementById('tradeNotes').value.trim();
        // 021S：归属账户
        var tAccSel = document.getElementById('tradeAccountSelect');
        var tAccId = (tAccSel && tAccSel.value && tAccSel.value !== 'all') ? parseInt(tAccSel.value) : null;

        if (!tradeDate) { alert('请选择交易日期'); return; }
        if ((tradeType === 'dividend' || tradeType === 'dividend_tax') && !amount && !price) {
            alert('请填写金额（或 成交价×数量）'); return;
        }

        fetch('/api/portfolio/holdings/' + currentTradeStockId + '/trades', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify((function() {
                // 021BK：佣金留空 → 不传字段，后端自动估算（按账户券商费率）
                var b = { trade_type: tradeType, price: price, quantity: qty, amount: amount || null, trade_date: tradeDate, notes: notes, account_id: tAccId };
                if (commission > 0) b.commission = commission;
                return b;
            })())
        })
        .then(function(r) { return safeJson(r); })
        .then(function(data) {
            if (data.success) {
                document.getElementById('tradePrice').value = '';
                document.getElementById('tradeQty').value = '';
                document.getElementById('tradeAmount').value = '';
                document.getElementById('tradeCommission').value = '';
                document.getElementById('tradeNotes').value = '';
                loadTrades(currentTradeStockId);
                loadHoldings();     // 新增流水后也刷新持仓列表
                loadPortfolioGroups();
                if (data.recalculated_position) {
                    var p = data.recalculated_position;
                    _showToast('持仓已更新：数量 ' + p.quantity + ' · 均价 ' + p.avg_cost);
                }
            } else {
                alert('添加失败：' + (data.message || '未知错误'));
            }
        });
    }

    // ========== 分组管理弹窗（通用：自选股 / 持仓） ==========

    var _gmType = null;   // 'watchlist' 或 'portfolio'

    function openGroupManager(type) {
        _gmType = type;
        document.getElementById('gmType').value = type;
        document.getElementById('gmTitle').textContent =
            type === 'watchlist' ? '管理自选股分组' : '管理持仓分组';
        document.getElementById('gmNewName').value = '';
        document.getElementById('groupManagerModal').style.display = 'block';
        gmLoadList();
    }

    function closeGroupManager() {
        document.getElementById('groupManagerModal').style.display = 'none';
    }

    // 获取分组列表（根据类型调用不同 API）
    function _gmGetGroupsUrl() {
        return '/api/groups?type=' + _gmType;
    }
    function _gmGetCreateUrl() {
        return '/api/groups';
    }
    function _gmGetUpdateUrl(id) {
        return '/api/groups/' + id;
    }
    function _gmGetDeleteUrl(id) {
        return '/api/groups/' + id;
    }
    function _gmGetCountField() {
        return _gmType === 'watchlist' ? 'stock_count' : 'holding_count';
    }

    // 轻量 Toast 提示
    function _showToast(msg) {
        var toast = document.createElement('div');
        toast.style.cssText = 'position:fixed;bottom:24px;left:50%;transform:translateX(-50%);background:#333;color:white;padding:10px 20px;border-radius:8px;font-size:14px;z-index:9999;box-shadow:0 4px 12px rgba(0,0,0,0.3);opacity:0;transition:opacity 0.3s;';
        toast.textContent = msg;
        document.body.appendChild(toast);
        setTimeout(function() { toast.style.opacity = '1'; }, 10);
        setTimeout(function() {
            toast.style.opacity = '0';
            setTimeout(function() { if (toast.parentNode) toast.parentNode.removeChild(toast); }, 300);
        }, 2500);
    }

    // 获取另一类型的中文名称
    function _gmGetCounterpartLabel() {
        return _gmType === 'watchlist' ? '持仓' : '自选股';
    }

    function gmLoadList() {
        fetch(_gmGetGroupsUrl())
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    renderGmList(data.groups);
                }
            });
    }

    function renderGmList(groups) {
        var container = document.getElementById('gmList');
        if (!groups || groups.length === 0) {
            container.innerHTML = '<div style="padding:16px;text-align:center;color:var(--text-3,#999);">暂无分组</div>';
            return;
        }
        var countField = _gmGetCountField();
        var html = '';
        groups.forEach(function(g) {
            html += '<div class="gm-item" id="gm-item-' + g.id + '">' +
                '<span class="gm-name" onclick="gmStartEdit(' + g.id + ',\'' + (g.name||'').replace(/'/g,' ') + '\')">' + g.name + '</span>' +
                '<span class="gm-count">' + (g[countField] || 0) + ' 条</span>' +
                '<div class="gm-actions">' +
                    '<button class="gm-edit-btn" onclick="gmStartEdit(' + g.id + ',\'' + (g.name||'').replace(/'/g,' ') + '\')">✎ 编辑</button>' +
                    '<button class="gm-delete-btn" onclick="gmDeleteGroup(' + g.id + ',\'' + (g.name||'').replace(/'/g,' ') + '\',' + (g[countField]||0) + ')">🗑 删除</button>' +
                '</div>' +
            '</div>';
        });
        container.innerHTML = html;
    }

    // 新增分组（021AO：默认只建当前类型；勾选"两侧同步建"才同步）
    function gmCreateGroup() {
        var name = document.getElementById('gmNewName').value.trim();
        if (!name) { alert('请输入分组名称'); return; }
        var syncBox = document.getElementById('gmSyncOther');
        var syncOther = !!(syncBox && syncBox.checked);
        fetch(_gmGetCreateUrl(), {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: name, type: _gmType, sync_to_other_type: syncOther })
        })
        .then(function(r) { return safeJson(r); })
        .then(function(data) {
            if (data.success) {
                document.getElementById('gmNewName').value = '';
                if (syncBox) syncBox.checked = false;
                if (data.counterpart_created) {
                    _showToast('已同步创建到' + _gmGetCounterpartLabel() + '分组');
                }
                gmLoadList();
                _gmRefreshParentUI();
            } else {
                alert('创建失败：' + (data.message || '未知错误'));
            }
        });
    }

    // 内联编辑分组名称
    function gmStartEdit(groupId, oldName) {
        var item = document.getElementById('gm-item-' + groupId);
        if (!item) return;
        item.innerHTML =
            '<input type="text" class="gm-name-input" id="gm-edit-input-' + groupId + '" value="' + oldName + '" ' +
            'onkeydown="if(event.key===\'Enter\') gmSaveEdit(' + groupId + ');if(event.key===\'Escape\') gmLoadList();" ' +
            'onblur="gmSaveEdit(' + groupId + ')">' +
            '<span class="gm-count" style="color:var(--text-3,#999);">回车保存 · ESC 取消</span>';
        var input = document.getElementById('gm-edit-input-' + groupId);
        if (input) { input.focus(); input.select(); }
    }

    function gmSaveEdit(groupId) {
        var input = document.getElementById('gm-edit-input-' + groupId);
        if (!input) return;
        var newName = input.value.trim();
        if (!newName) { gmLoadList(); return; }

        fetch(_gmGetUpdateUrl(groupId), {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ name: newName })  // 021AO：改名默认只改当前类型
        })
        .then(function(r) { return safeJson(r); })
        .then(function(data) {
            if (data.success) {
                if (data.counterpart_updated) {
                    _showToast('已同步改名到' + _gmGetCounterpartLabel() + '分组');
                }
                gmLoadList();
                _gmRefreshParentUI();
            } else {
                alert('修改失败：' + (data.message || '未知错误'));
                gmLoadList();
            }
        });
    }

    // 删除分组（带迁移计数提示）
    function gmDeleteGroup(groupId, groupName, count) {
        var msg = '确定删除分组「' + groupName + '」？';
        if (count > 0) {
            msg += '\n该组下还有 ' + count + ' 条记录，删除后记录将移至「未分组」。';
        }
        if (!confirm(msg)) return;

        fetch(_gmGetDeleteUrl(groupId), { method: 'DELETE' })
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    var migrated = data.migrated_count || 0;
                    if (migrated > 0) {
                        alert('分组已删除，' + migrated + ' 条记录已移至「未分组」。');
                    }
                    gmLoadList();
                    _gmRefreshParentUI();
                } else {
                    alert('删除失败：' + (data.message || '未知错误'));
                }
            });
    }

    // 刷新父页面的 UI（Tab 栏、列表等）
    function _gmRefreshParentUI() {
        if (_gmType === 'watchlist') {
            loadGroups();
            loadStocks();
        } else {
            loadPortfolioGroups();
        }
    }

    // ========== 自选股编辑弹窗（修改分组） ==========

    var _editStockCurrentGroupId = null;

    function openStockEditModal(stockId, symbol, name, groupId) {
        _editStockCurrentGroupId = groupId;
        document.getElementById('editStockId').value = stockId;
        document.getElementById('editStockSymbol').textContent = symbol;
        document.getElementById('editStockName').textContent = name || '—';

        // 加载分组下拉
        var sel = document.getElementById('editStockGroupSelect');
        var html = '<option value="">无分组</option>';
        _watchlistGroupCache.forEach(function(g) {
            var isSel = (groupId != null && g.id == groupId);
            html += '<option value="' + g.id + '"' + (isSel ? ' selected' : '') + '>' + g.name + '</option>';
        });
        sel.innerHTML = html;

        document.getElementById('stockEditModal').style.display = 'block';
    }

    function closeStockEditModal() {
        document.getElementById('stockEditModal').style.display = 'none';
    }

    function saveStockEdit() {
        var stockId = document.getElementById('editStockId').value;
        var gid = document.getElementById('editStockGroupSelect').value || null;

        fetch('/api/stocks/' + stockId, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ group_id: gid })
        })
        .then(function(r) { return safeJson(r); })
        .then(function(data) {
            if (data.success) {
                closeStockEditModal();
                loadStocks();
                loadGroups();   // 刷新 Tab 计数
            } else {
                alert('保存失败：' + (data.message || '未知错误'));
            }
        });
    }

    // ========== P0: 个股分析报告页面 ==========
    var _reportStockId = null;
    var _reportTechDetail = null; // 020R-36：技术指标明细（供技术面卡渲染）
    var _reportFundDetail = null; // 020R-37：基本面指标明细（供基本面卡渲染）
    var _reportCapDetail = null;  // 020R-38：资金面指标明细（供资金面卡渲染）
    var _reportNewsDetail = null; // 020R-39：消息面指标明细（供消息面卡渲染）
    var _reportMarket = null;     // 021U：当前报告股票市场（'a_stock'/'hk_stock'，港股隐藏恒缺子项行）
    var _radarChart = null;
    var _klineChart = null;

    /**
     * 从自选股列表跳转到分析报告页
     */
    function viewReport(stockId) {
        _reportStockId = stockId;
        navigateTo('#report');
        loadReport(stockId);
    }

    /**
     * 主函数：加载并渲染完整分析报告
     * P3-A 附加修复：优先从 daily_reports 读取（与列表页同源），
     * 回退到实时 /advise 生成。
     * forceRefresh=true 时跳过缓存直接调用引擎。
     */
    function loadReport(stockId, forceRefresh) {
        var container = document.getElementById('reportContent');
        container.innerHTML = '<div class="report-loading">正在加载分析数据，请稍候...</div>';

        // K线数据始终实时获取
        var klinePromise = fetch('/api/stocks/' + stockId + '/kline')
            .then(function(r) { return safeJson(r); });

        if (!forceRefresh) {
            // 优先从 daily_reports 读取（与列表页/看板同源）
            // 021G：no-store 防浏览器 HTTP 缓存；快照过旧时后台自动重评（见 _autoRefreshReportIfStale）
            fetch('/api/stocks/' + stockId + '/report-latest', { cache: 'no-store' })
                .then(function(r) { return safeJson(r); })
                .then(function(reportData) {
                    if (reportData.success) {
                        // daily_reports 有数据 → 用快照渲染（保证一致性）
                        klinePromise.then(function(klineData) {
                            renderFullReport(reportData, klineData, stockId);
                            _autoRefreshReportIfStale(stockId, reportData.generated_at, klinePromise);
                        });
                    } else {
                        // 无快照 → 回退到实时 advise
                        _loadReportFromAdvise(stockId, klinePromise, container);
                    }
                })
                .catch(function() {
                    _loadReportFromAdvise(stockId, klinePromise, container);
                });
        } else {
            _loadReportFromAdvise(stockId, klinePromise, container);
        }
    }

    /**
     * 回退路径：实时调用 /advise 引擎生成
     */
    function _loadReportFromAdvise(stockId, klinePromise, container) {        var advisePromise = fetch('/api/stocks/' + stockId + '/advise', { method: 'POST' })
            .then(function(r) { return safeJson(r); });

        Promise.all([advisePromise, klinePromise])
            .then(function(results) {
                var adviseData = results[0];
                var klineData = results[1];

                if (!adviseData.success) {
                    container.innerHTML =
                        '<div class="alert alert-error" style="margin:20px;">' +
                        '分析失败：' + (adviseData.message || '请先采集数据') +
                        '</div>';
                    return;
                }

                renderFullReport(adviseData, klineData, stockId);
                refreshDashboardIfLoaded();  // 报告刷新=重新评级，同步看板批量评分表
            })
            .catch(function(err) {
                container.innerHTML =
                    '<div class="alert alert-error" style="margin:20px;">加载报告失败：' + err.message + '</div>';
            });
    }

    // ========== 021G：报告快照过旧时进入页面自动后台重评 ==========
    // 用户的实际痛点：报告页默认显示 daily_reports 快照（P3-A 与列表同源设计），
    // 早上批次生成后一直停留在旧数据，每次都要手动点「🔄 刷新报告」。
    // 现改为：快照超过 15 分钟即静默走 /advise 重评（与手动刷新同路径、同副作用），
    // 成功后重渲染并同步看板（021F）；失败则保留快照不打断浏览。
    var _REPORT_AUTO_REFRESH_STALE_MS = 15 * 60 * 1000;  // 快照新鲜度阈值：15分钟
    var _reportAutoRefreshing = {};  // 防重入：{ stockId: true }

    function _autoRefreshReportIfStale(stockId, generatedAt, klinePromise) {
        // 周末守卫（与后端 020M 同口径）：周末不自动实时生成，避免写出周末日期的报告行
        var d = new Date();
        if (d.getDay() === 0 || d.getDay() === 6) return;
        var ts = _parseIsoTs(generatedAt);
        if (!isNaN(ts) && (Date.now() - ts) < _REPORT_AUTO_REFRESH_STALE_MS) return;  // 快照够新
        if (_reportAutoRefreshing[stockId]) return;  // 已在后台重评中
        _reportAutoRefreshing[stockId] = true;
        fetch('/api/stocks/' + stockId + '/advise', { method: 'POST' })
            .then(function(r) { return safeJson(r); })
            .then(function(adviseData) {
                if (!adviseData.success) return;  // 失败：保留快照，不报错不打断
                if (_reportStockId !== stockId) return;  // 用户已切到别的股票/页面
                klinePromise.then(function(klineData) {
                    if (_reportStockId !== stockId) return;
                    renderFullReport(adviseData, klineData, stockId);
                    refreshDashboardIfLoaded();  // 021F：同步看板批量评分表
                });
            })
            .catch(function() { /* 静默失败：快照仍在展示 */ })
            .finally(function() { _reportAutoRefreshing[stockId] = false; });
    }

    // ========== 021J：报告新鲜度可见化 ==========
    // 链路证据（2026-08-18 盘前实测）：打开报告时服务端 B11 已实时重评（恒瑞 08:35:38 算出
    // 53.8，用户 14 秒后手动刷新仍得 53.8——盘前数据未变，分数自然相同），但页面无任何
    // 生成时间标识，用户无法区分"刚算的新报告"与"旧快照"，只能靠点刷新求安心。
    // 修复：头部徽标让新鲜度一眼可见——这正是 021I"UI/UX 反向校验契约层"的落地。

    /** 稳健解析 Python isoformat 时间戳（含 6 位微秒，部分浏览器 Date.parse 不识别，截断到毫秒） */
    function _parseIsoTs(s) {
        if (!s || typeof s !== 'string') return NaN;
        return Date.parse(s.replace(/(\.\d{3})\d+/, '$1'));
    }

    /** 报告头部新鲜度徽标：<5分钟=绿色"刚刚重新计算"；今日=灰色；更早=橙色"历史快照" */
    function _reportFreshnessBadge(generatedAt) {
        var ts = _parseIsoTs(generatedAt);
        if (isNaN(ts)) return '';
        var d = new Date(ts);
        var hhmmss = d.toTimeString().substring(0, 8);
        var ageMs = Date.now() - ts;
        var isToday = d.toDateString() === new Date().toDateString();
        var base = 'font-size:12px;padding:2px 10px;border-radius:10px;vertical-align:middle;margin-left:8px;white-space:nowrap;';
        if (ageMs < 5 * 60 * 1000) {
            return '<span style="' + base + 'background:#e8f5e9;color:#1b5e20;border:1px solid #a5d6a7;cursor:help;"' +
                ' title="本报告刚刚按库内最新数据重新计算。若分数与之前相同，说明底层数据未变化（如盘前/收盘后），并非旧版本。">✓ 刚刚重新计算 ' + hhmmss + '</span>';
        }
        if (isToday) {
            return '<span style="' + base + 'background:var(--surface-alt,#f5f5f5);color:var(--text-2,#666);border:1px solid var(--border,#ddd);cursor:help;"' +
                ' title="报告生成时间">生成于今日 ' + hhmmss + '</span>';
        }
        return '<span style="' + base + 'background:#fff3e0;color:#e65100;border:1px solid #ffcc80;cursor:help;"' +
            ' title="历史快照：进入页面已自动触发按最新数据重评（021G），完成后此徽标变绿">生成于 ' +
            (d.getMonth() + 1) + '-' + d.getDate() + ' ' + hhmmss + '（历史快照）</span>';
    }

    /**
     * 渲染完整报告
     */
    function renderFullReport(adviseData, klineData, stockId) {
        var container = document.getElementById('reportContent');
        var dims = adviseData.dimensions || {};
        var marketTag = adviseData.market === 'hk_stock'
            ? '<span class="market-badge market-badge-hk">港股</span>'
            : '<span class="market-badge market-badge-a">A股</span>';

        var html = '';

        // 1. 返回按钮 + 股票头部
        html += '<div class="report-actions">';
        html += '<button class="report-back-btn" onclick="navigateTo(\'#watchlist\')">← 返回自选股</button>';
        html += '<button class="btn btn-success btn-sm" onclick="loadReport(' + stockId + ', true)">🔄 刷新报告</button>';
        html += '</div>';

        html += '<div class="report-header">';
        html += '<span class="stock-code">' + adviseData.stock_code + '</span>';
        html += '<span class="stock-name">' + (adviseData.stock_name || '') + '</span>';
        html += marketTag;
        html += _reportFreshnessBadge(adviseData.generated_at);
        if (adviseData.rating_changed) {
            html += '<span class="rating-change-badge" style="background:#fff3e0;color:#e65100;">' +
                    '评级变更：' + (adviseData.previous_rating || '—') + ' → ' + adviseData.rating + '</span>';
        }
        html += '</div>';

        // 2. 评分卡 + 雷达图 + 价格建议卡（020R-7：价格建议与网格计划独立卡片，雷达图右侧）
        // 先构建价格建议卡片 HTML（后面插入 top-grid）
        var paSideHtml = '';
        if (adviseData.price_advice) {
            var pa = adviseData.price_advice;
            // 009: 状态/网格/资金面颜色映射
            var _paStateCls = {'S1':'pa-up','S2':'pa-up-light','S3':'pa-warning','S4':'pa-down'};
            var _paGridCls = {'buy':'pa-buy','reduce':'pa-reduce','add':'pa-add','watch':'pa-watch'};
            function _paCapitalCls(s) {
                if (s >= 1) return 'pa-up';
                if (s > 0) return 'pa-up-light';
                if (s <= -1) return 'pa-down';
                if (s < 0) return 'pa-warning';
                return '';
            }
            // 020R-11：先构建网格计划卡（首屏顺序：评分卡/雷达卡/网格计划/价格建议）
            var gridSideHtml = '';
            if (pa.grid && pa.grid.length > 0) {
                gridSideHtml += '<div class="pa-side-card">';
                gridSideHtml += '<div class="card-title" style="font-size:15px;margin-bottom:8px;">📊 ' +
                        (pa.has_position ? '操作网格计划'
                            : (pa.zone_label === '支撑参考区间' ? '支撑观察预案（非买入建议）' : '网格买入计划')) + '</div>';
                gridSideHtml += '<table class="pa-grid-table"><thead><tr><th>档位</th><th>价位</th><th>仓位</th><th>说明</th></tr></thead><tbody>';
                pa.grid.forEach(function(g) {
                    var typeCls = _paGridCls[g.type] || '';
                    gridSideHtml += '<tr><td>' + g.level + '</td>';
                    gridSideHtml += '<td class="' + typeCls + '">' + g.price.toFixed(2) + '</td>';
                    gridSideHtml += '<td>' + (g.pct != null ? g.pct + '%' : '观察') + '</td>';
                    gridSideHtml += '<td>' + g.label + '</td></tr>';
                });
                gridSideHtml += '</tbody></table>';
                gridSideHtml += '</div>';
            }
            paSideHtml += '<div class="pa-side-card">';
            if (pa.available) {
                paSideHtml += '<div class="card-title" style="font-size:15px;margin-bottom:8px;color:#e65100;">💰 价格建议' +
                        (pa.has_position ? '（持仓中）' : '（当前无持仓）') + '</div>';
                // 020Q：紧凑卡片式键值行（标签左、数值右）
                paSideHtml += '<div class="pa-kv-wrap">';

                if (pa.has_position) {
                    var profitClass = pa.profit_pct >= 0 ? 'pa-up' : 'pa-down';
                    var stateCls = _paStateCls[pa.state] || '';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">成本价</span><span class="pa-kv-value">' + pa.cost_price.toFixed(2) + '</span></div>';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">当前价</span><span class="pa-kv-value">' + pa.current_close.toFixed(2) + '</span></div>';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">浮盈</span><span class="pa-kv-value ' + profitClass + '">' +
                            (pa.profit_pct >= 0 ? '+' : '') + pa.profit_pct.toFixed(1) + '%</span></div>';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">状态</span><span class="pa-kv-value ' + stateCls + '">' +
                            (pa.state_name || '') + '</span></div>';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">止盈价</span><span class="pa-kv-value pa-up">' +
                            pa.take_profit.toFixed(2) + '</span></div>';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">止损价</span><span class="pa-kv-value pa-down">' +
                            pa.stop_loss.toFixed(2) + '</span></div>';
                    // 021AP：与回测中心口径对齐——补仓区间/持有区间
                    // 021AS：减仓/清仓评级给出建议减仓区间
                    if (pa.reduce_range && pa.reduce_range.low != null) {
                        var _rrLabel = pa.reduce_range.pct >= 100 ? '清仓区间' : '减仓区间';
                        var _rrText = pa.reduce_range.low === pa.reduce_range.high
                            ? pa.reduce_range.low.toFixed(2) + '（尽快）'
                            : pa.reduce_range.low.toFixed(2) + ' - ' + pa.reduce_range.high.toFixed(2);
                        paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label" title="该价格区间内分批执行减仓，越早越主动">' + _rrLabel + '（建议减' + pa.reduce_range.pct + '%）</span><span class="pa-kv-value pa-down" style="font-weight:600;">' +
                                _rrText + '</span></div>';
                    }
                    // 021AP：与回测中心口径对齐——补仓区间/持有区间
                    // 021AQ：网格有两档补仓，补仓区间取最高档（最先触发的补仓机会）
                    var _addLevels = (pa.grid || []).filter(function(g) { return g.type === 'add' && g.price; });
                    if (_addLevels.length > 0) {
                        var _firstAddPrice = Math.max.apply(null, _addLevels.map(function(g) { return g.price; }));
                        paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label" title="股价回落到该价位以下即出现补仓机会（10%仓位档）；已破止损时不提供">补仓区间</span><span class="pa-kv-value" style="color:#6a1b9a;font-weight:600;">≤ ' +
                                _firstAddPrice.toFixed(2) + '</span></div>';
                    }
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label" title="股价在此区间内继续持有不动；跌破下沿触发止损，涨过上沿触发止盈">持有区间</span><span class="pa-kv-value">' +
                            pa.stop_loss.toFixed(2) + ' ~ ' + pa.take_profit.toFixed(2) + '</span></div>';
                    paSideHtml += '<div class="pa-kv-row pa-kv-action"><span class="pa-kv-label">操作建议</span><span class="pa-kv-value">' +
                            (pa.action_suggestion || '') + '</span></div>';
                } else {
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">建议仓位</span><span class="pa-kv-value">' +
                            pa.position_pct + '%</span></div>';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">评级</span><span class="pa-kv-value">' +
                            (adviseData.rating || '') + '</span></div>';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label" title="021BF：区间语义随评级变化——买入档为买入区间，观望档为参考区间，减仓/卖出档仅为技术支撑参考">' +
                            (pa.zone_label || '买入区间') + '</span><span class="pa-kv-value">' +
                            pa.buy_range_low.toFixed(2) + ' - ' + pa.buy_range_high.toFixed(2) + '</span></div>';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">当前价</span><span class="pa-kv-value">' +
                            pa.current_close.toFixed(2) + '</span></div>';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">目标价</span><span class="pa-kv-value pa-up">' +
                            pa.target_price.toFixed(2) + '</span></div>';
                    paSideHtml += '<div class="pa-kv-row"><span class="pa-kv-label">止损价</span><span class="pa-kv-value pa-down">' +
                            pa.stop_loss.toFixed(2) + '</span></div>';
                    paSideHtml += '<div class="pa-kv-row pa-kv-action"><span class="pa-kv-label">操作建议</span><span class="pa-kv-value">' +
                            (pa.action_suggestion || '') + '</span></div>';
                }

                paSideHtml += '</div>';

                // 009: 资金面信号
                if (pa.capital_signal) {
                    paSideHtml += '<div class="pa-capital-signal">';
                    paSideHtml += '<span style="font-weight:600;">资金面：</span>';
                    paSideHtml += '<span class="' + _paCapitalCls(pa.capital_signal.strength) + '">' +
                            pa.capital_signal.label + '</span>';
                    if (pa.capital_signal.risk_warning) {
                        paSideHtml += ' <span class="pa-down">⚠️ ' + pa.capital_signal.risk_warning + '</span>';
                    }
                    paSideHtml += '</div>';
                }

                // 009: 交易分析摘要
                if (pa.trade_analysis && pa.trade_analysis.available) {
                    paSideHtml += '<div class="pa-trade-analysis">';
                    paSideHtml += '<span style="font-weight:600;">交易分析：</span>';
                    paSideHtml += '<span>' + pa.trade_analysis.summary + '</span>';
                    paSideHtml += '</div>';
                }

                paSideHtml += '<div class="price-advice-disclaimer">⚠️ 以上价格建议仅供参考，不构成投资建议。股市有风险，投资需谨慎。</div>';
                paSideHtml += '</div>';
            } else {
                // available=false: 数据不足
                paSideHtml += '<div class="card-title" style="font-size:15px;margin-bottom:8px;color:#e65100;">💰 价格建议</div>';
                paSideHtml += '<div class="advice-detail-text" style="border-left-color:var(--text-3,#999);color:var(--text-3,#999);">' +
                        '数据不足，暂无价格建议' +
                        (pa.reason ? '（' + pa.reason + '）' : '') + '</div>';
                paSideHtml += '</div>';
            }
            // 020R-11：网格计划卡在前、价格建议卡在后
            paSideHtml = gridSideHtml + paSideHtml;
        }

        html += '<div class="report-top-grid">';

        // 评分卡
        var scoreColor = _scoreColor(adviseData.total_score);
        var ratingClass = getRatingClass(adviseData.rating);
        html += '<div class="score-card">';
        html += '<div class="score-label">综合评分</div>';
        html += '<div class="score-value" style="color:' + scoreColor + ';">' +
                (adviseData.total_score != null ? adviseData.total_score.toFixed(1) : '--') + '</div>';
        html += '<div class="rating-badge ' + ratingClass + '" title="' + getRatingTitle(adviseData.rating) + '">评级 ' + adviseData.rating + '</div>';
        // 021AR：v5 中文5档 key=label 恒等，标签行/建议行与徽章重复时不再显示
        if (adviseData.rating_label && adviseData.rating_label !== adviseData.rating) {
            html += '<div class="rating-label">' + adviseData.rating_label + '</div>';
        }
        if (adviseData.action_advice && adviseData.action_advice !== adviseData.rating) {
            html += '<div class="action-advice">建议：' + adviseData.action_advice + '</div>';
        }
        html += '<div class="rating-time">报告生成于：' + _fmtGenTime(adviseData.generated_at) + '</div>';
        if (adviseData.latest_close != null) {
            html += '<div class="rating-time">最新收盘：' + adviseData.latest_close.toFixed(2) +
                    '（' + (adviseData.latest_close_date || '') + '）</div>';
        }
        // 引擎版本标签（021AE：v5 单引擎；非 v5 仅见于极早期历史快照）
        if (adviseData.engine_version) {
            var engineLabel = adviseData.engine_version === 'v5'
                ? '<span style="color:#1a73e8;font-weight:600;">v5.0 引擎</span>'
                : '<span style="color:var(--text-3,#888);">历史引擎</span>';
            html += '<div class="rating-time" style="margin-top:4px;">评分引擎：' + engineLabel + '</div>';
        }
        // 数据完整度（v5引擎）- B15-T4增强版
        // 021U：港股资金面完整度恒 50%（两融/户数制度性无披露源，分母仍为4）——
        // 标注港股口径避免误读为"数据丢了"；A股两融缺失才可能是故障
        var _isHkReport = adviseData.market === 'hk_stock';
        if (adviseData.data_quality) {
            var dq = adviseData.data_quality;
            var dqDims = [
                {name:'技术', val: dq.technical},
                {name:'基本', val: dq.fundamental},
                {name:'资金', val: dq.capital, hkNote: _isHkReport},
                {name:'消息', val: dq.news}
            ];
            var zeroCount = 0;
            var dqHtml = '数据完整度：';
            dqDims.forEach(function(d) {
                // U7(#5): val 为 null 时表示已采集但未统计完整度，不再误显 100% 或 0%
                if (d.val === null || d.val === undefined) {
                    dqHtml += '<span style="color:var(--text-3,#999);">' + d.name + ' 已采集</span> ';
                } else {
                    var pct = Math.round(d.val * 100);
                    // 021U：港股资金 50% = 港股满口径（两融/户数无披露源），非故障
                    if (d.hkNote && pct > 0) {
                        dqHtml += '<span title="港股两融/股东人数无披露源（制度性），主力+机构持仓为满口径；已按置信收缩降权处理">' +
                            d.name + ' ' + pct + '%<span style="color:var(--text-3,#999);font-size:10px;">(港股口径)</span></span> ';
                    } else if (pct === 0) {
                        dqHtml += '<span style="color:#e67e22;font-weight:600;">' + d.name + ' 0% ⚠️缺失</span> ';
                        zeroCount++;
                    } else if (pct <= 30) {
                        dqHtml += '<span style="color:#f39c12;">' + d.name + ' ' + pct + '% 偏低</span> ';
                    } else {
                        dqHtml += d.name + ' ' + pct + '% ';
                    }
                }
            });
            html += '<div class="rating-time" style="font-size:11px;color:var(--text-3,#aaa);">' + dqHtml + '</div>';

            // 总评级警告（≥2个维度为0%）
            if (zeroCount >= 2) {
                html += '<div style="background:#fff3cd;border:1px solid #ffc107;border-radius:6px;padding:8px 12px;margin-top:8px;font-size:13px;color:#856404;">' +
                        '⚠️ 数据严重不足（' + zeroCount + '个维度缺失），评级仅供参考，不建议作为操作依据</div>';
            }
        }
        html += '</div>';

        // 雷达图
        html += '<div class="radar-card">';
        html += '<div class="card-title" style="font-size:14px;margin-bottom:2px;">四维评分雷达图</div>';
        html += '<div id="radarChart"></div>';
        html += '</div>';

        // 020R-7：价格建议+网格计划卡片（雷达图右侧）
        html += paSideHtml;

        html += '</div><!-- /report-top-grid -->';

        // 3. 四维评分详情（2×2网格，紧跟首屏评分卡后）
        // 020R-36/37/38/39：四维指标明细并入对应维度卡内
        _reportTechDetail = adviseData.technical_detail || null;
        _reportFundDetail = adviseData.fundamental_detail || null;
        _reportCapDetail = adviseData.capital_detail || null;
        _reportNewsDetail = adviseData.news_detail || null;
        _reportMarket = adviseData.market || null;
        html += '<div class="card dim-detail-card">';
        html += '<div class="card-title" style="font-size:15px;margin-bottom:6px;">四维评分详情</div>';
        html += '<div class="dim-grid">';
        html += _renderDimensionCard('kline', '技术面', dims.kline || dims.technical);
        html += _renderDimensionCard('fundamental', '基本面', dims.fundamental);
        html += _renderDimensionCard('capital_flow', '资金面', dims.capital_flow || dims.capital);
        html += _renderDimensionCard('news', '消息面', dims.news || dims.sentiment);
        html += '</div>';
        html += '</div>';

        // 4. K线图卡片已移除（020R：用户裁定报告页不再平铺K线卡片；
        // K线数据仍可在「数据」页查看，评分雷达/详情/建议紧接展示）

        // 5. 综合分析卡（markdown 渲染）+ 维度亮点卡（020R-15：两列并排）
        html += '<div class="advice-two-col">';
        html += '<div class="advice-card md-card">';
        html += '<div class="card-title" style="font-size:15px;margin-bottom:10px;">📝 综合分析</div>';

        // 020R-40：综合分析文本为固定子项，缺失时给出兜底说明
        {
            // U7(#5): 综合文本（历史快照 markdown_content）中的「数据完整度」行
            // 可能与顶部实时 data_quality（已修复口径）不一致，移除该行避免矛盾，
            // 完整度统一以顶部权威展示为准。
            var detailText = adviseData.advice_detail || '';
            if (detailText) {
                // U7(#5): 移除综合文本中的「数据完整度」相关行，避免与顶部实时
                // data_quality 及右侧维度亮点卡内的数据完整度重复（020R-16）
                detailText = detailText.replace(/\n- \*\*数据完整度\*\*[^\n]*/g, '');
                detailText = detailText.replace(/\n## 数据完整度[\s\S]*$/g, '');
                // 020R-43：风险提示块移至下方固定「风险提示」区，正文不再重复展示
                detailText = detailText.replace(/\n- \*\*风险提示\*\*：[\s\S]*$/, '');
            }
            // 020R-14：markdown 渲染（marked）；未加载时降级为纯文本换行
            var _mdHtml = '';
            if (typeof marked !== 'undefined' && detailText) {
                try { _mdHtml = marked.parse(detailText); } catch (e) { _mdHtml = ''; }
            }
            html += '<div class="md-body">' +
                    (_mdHtml || ((detailText || '暂无综合分析文本。').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/\n/g, '<br>'))) +
                    '</div>';
        }

        // 020R-14：仓位建议移除（网格计划卡已含各档位仓位比例）

        // 020R-40：消息面摘要为固定子项，缺失时给出兜底说明
        html += '<div class="advice-section">';
        html += '<div class="advice-section-title">消息面摘要</div>';
        html += '<div class="advice-detail-text" style="border-left-color:#f39c12;font-size:13px;">' +
                (adviseData.news_summary || '暂无消息面摘要（近期无重大新闻，可参考右侧消息面指标明细）。') +
                '</div>';
        html += '</div>';

        // 020R-40：风险提示为固定子项，缺失时给出兜底说明
        html += '<div class="advice-section">';
        html += '<div class="advice-section-title" style="color:#c62828;">风险提示</div>';
        html += '<ul class="risk-list">';
        if (adviseData.risk_warnings && adviseData.risk_warnings.length > 0) {
            adviseData.risk_warnings.forEach(function(r) {
                html += '<li>' + r + '</li>';
            });
        } else {
            html += '<li>暂无风险提示。</li>';
        }
        html += '</ul>';
        html += '</div>';

        // 数据警告已移至维度亮点卡（020R-16），此处不再重复展示

        html += '</div><!-- /md-card -->';

        // 维度亮点卡（020R-40：固定子项——最强/最弱维度 + 数据完整度与提示，缺失自动兜底）
        {
            // 最强/最弱维度：后端缺失时从前端 dims 兜底计算，保证必显
            var _dimNameMap = { kline: '技术面', fundamental: '基本面', capital_flow: '资金面', news: '消息面' };
            var swFallback = null;
            if (!adviseData.strongest_dim || !adviseData.weakest_dim) {
                var scored = [];
                Object.keys(_dimNameMap).forEach(function(k) {
                    var d = dims[k];
                    if (d && d.score != null && d.status !== 'failed' && d.status !== 'no_data') {
                        scored.push({ name: _dimNameMap[k], score: Number(d.score) });
                    }
                });
                if (scored.length > 0) {
                    scored.sort(function(a, b) { return b.score - a.score; });
                    swFallback = { s: scored[0], w: scored[scored.length - 1] };
                }
            }
            var strongest = adviseData.strongest_dim || (swFallback && swFallback.s);
            var weakest = adviseData.weakest_dim || (swFallback && swFallback.w);

            html += '<div class="advice-card dim-hl-card">';
            html += '<div class="card-title" style="font-size:15px;margin-bottom:10px;">🌟 维度亮点</div>';
            if (strongest) {
                html += '<p style="font-size:14px;color:#27ae60;margin:0 0 6px;">' +
                        '★ 最强维度：' + strongest.name +
                        '（' + Number(strongest.score).toFixed(1) + '分）</p>';
            } else {
                html += '<p style="font-size:14px;color:var(--text-3,#999);margin:0 0 6px;">★ 最强维度：暂无数据</p>';
            }
            if (weakest) {
                html += '<p style="font-size:14px;color:#e74c3c;margin:0;">' +
                        '▼ 最弱维度：' + weakest.name +
                        '（' + Number(weakest.score).toFixed(1) + '分）</p>';
            } else {
                html += '<p style="font-size:14px;color:var(--text-3,#999);margin:0;">▼ 最弱维度：暂无数据</p>';
            }

            // 020R-16：数据完整度与提示并入维度亮点卡（不再与综合分析重复）
            // 020R-31：按数据状态分级提示效果——异常(红)/滞后(黄)/提示(灰)/正常(绿)
            // 020R-40：数据完整度为固定子项——无告警时按 data_quality 兜底、再兜底为「数据正常」
            // 020R-57：分类逻辑统一委托 _classifyDataWarning（与看板三态标签同源）
            var _classifyDw = function(w) {
                return _classifyDataWarning(w);
            };
            var DW_STYLE = {
                'bad':  { icon: '🔴', color: '#c62828', bg: '#ffebee', border: '#e57373', label: '异常' },
                'warn': { icon: '🟡', color: '#e65100', bg: '#fff8e1', border: '#ffb74d', label: '滞后' },
                'info': { icon: 'ℹ️', color: '#5d6d7e', bg: '#f5f7fa', border: '#cfd8e3', label: '提示' },
                'good': { icon: '✅', color: '#2e7d32', bg: '#eafaf1', border: '#81c784', label: '正常' }
            };
            var dwOrder = { 'bad': 0, 'warn': 1, 'info': 2, 'good': 3 };
            var dwItems = [];
            var dwList = adviseData.data_warnings || [];
            if (dwList.length > 0) {
                dwItems = dwList.map(function(w) {
                    // 020R-32：去掉每条前缀「数据完整度：」，只显示维度名与状态
                    return { text: w.replace(/^数据完整度：/, ''), state: _classifyDw(w) };
                }).sort(function(a, b) {
                    var oa = dwOrder[a.state] != null ? dwOrder[a.state] : 9;
                    var ob = dwOrder[b.state] != null ? dwOrder[b.state] : 9;
                    return oa - ob;
                });
            } else {
                // 无告警兜底：优先 data_quality 逐维度展示，否则整体「数据正常」
                var dq = adviseData.data_quality || {};
                var dqKeys = Object.keys(dq);
                var hasDqVal = dqKeys.some(function(k) { return dq[k] != null; });
                if (hasDqVal) {
                    // 021U：港股资金面完整度 50% 属港股满口径，不再标"数据正常"误导
                    var _dqHk = adviseData.market === 'hk_stock';
                    [['technical', '技术'], ['fundamental', '基本'], ['capital', '资金'], ['news', '消息']].forEach(function(pair) {
                        var v = dq[pair[0]];
                        if (v == null || v === undefined) {
                            dwItems.push({ text: pair[1] + '：已采集（未统计完整度）', state: 'info' });
                        } else if (v <= 0) {
                            dwItems.push({ text: pair[1] + '：数据缺失', state: 'bad' });
                        } else if (_dqHk && pair[0] === 'capital') {
                            dwItems.push({ text: '资金：完整度 ' + Math.round(v * 100) + '%（港股口径：两融/股东人数无披露源，主力+机构持仓已全覆盖）', state: 'good' });
                        } else {
                            dwItems.push({ text: pair[1] + '：完整度 ' + Math.round(v * 100) + '%', state: 'good' });
                        }
                    });
                } else {
                    dwItems.push({ text: '数据完整度未统计（历史快照），点击「🔄 刷新报告」获取实时完整度', state: 'info' });
                }
            }
            var badCount = dwItems.filter(function(x) { return x.state === 'bad'; }).length;
            var warnCount = dwItems.filter(function(x) { return x.state === 'warn'; }).length;
            var goodCount = dwItems.filter(function(x) { return x.state === 'good'; }).length;

            html += '<div class="advice-section" style="margin-top:10px;">';
            html += '<div class="advice-section-title" style="color:#f39c12;">📋 数据完整度与提示' +
                    '<span style="font-weight:normal;font-size:12px;color:var(--text-3,#999);margin-left:8px;">' +
                    (badCount + warnCount > 0
                        ? '🔴 ' + badCount + ' 项异常 · 🟡 ' + warnCount + ' 项滞后'
                        : (goodCount > 0 ? '✅ 数据状态正常' : 'ℹ️ 未统计完整度')) +
                    '</span></div>';
            html += '<ul class="risk-list" style="list-style:none;padding-left:0;">';
            dwItems.forEach(function(x) {
                var st = DW_STYLE[x.state] || DW_STYLE['info'];
                html += '<li style="display:flex;align-items:flex-start;gap:8px;background:' + st.bg +
                    ';border-left:3px solid ' + st.border + ';border-radius:6px;padding:6px 10px;margin-bottom:6px;font-size:13px;color:' + st.color + ';">' +
                    '<span style="flex-shrink:0;">' + st.icon + '</span>' +
                    '<span style="line-height:1.5;">' + x.text + '</span>' +
                    '<span style="flex-shrink:0;margin-left:auto;font-size:11px;opacity:.85;">' + st.label + '</span>' +
                    '</li>';
            });
            html += '</ul>';
            html += '</div>';
            html += '</div>';
        }
        html += '</div><!-- /advice-two-col -->';

        container.innerHTML = html;

        // 渲染 ECharts 图表（020R：K线卡片已移除，仅雷达图）
        _renderRadarChart(dims);
    }

    // ============================================================
    // US-11: 每日报告 页面逻辑
    // ============================================================

    function generateDailyReport() {
        var btn = document.getElementById('dailyGenBtn');
        var container = document.getElementById('dailyGenStatus');
        if (!container) return;
        var forceCheckbox = document.getElementById('dailyForceRefresh');
        var forceRefresh = forceCheckbox ? forceCheckbox.checked : false;
        if (btn) { btn.disabled = true; btn.textContent = '⏳ 生成中...'; }
        // 进度条：立即渲染 + 每 2 秒轮询进度接口
        var pollTimer = setInterval(function() { pollReportProgress(container, pollTimer, '每日报告'); }, 2000);
        renderProgressUI(container, {total: 1, current: 0, stage: '准备中', current_symbol: '', current_name: ''}, '每日报告');

        var bodyData = forceRefresh ? JSON.stringify({force: true}) : '{}';
        fetch('/api/daily-report/generate', { method: 'POST', headers: {'Content-Type':'application/json'}, body: bodyData })
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    clearInterval(pollTimer);
                    renderDailyReport(data);
                } else {
                    // 防抖拒绝（任务已在后台运行）时保留进度显示
                    if (data.message && data.message.indexOf('进行中') >= 0) {
                        container.innerHTML = '<div class="report-loading" style="color:#1a73e8;">⏳ 已有报告正在后台生成，实时进度如下...</div>';
                        return;
                    }
                    clearInterval(pollTimer);
                    container.innerHTML = '<div class="report-empty"><p style="color:#e74c3c;">生成失败：' + (data.message || '未知错误') + '</p></div>';
                }
            })
            .catch(function(e) {
                container.innerHTML = '<div class="report-empty"><p style="color:#e74c3c;">请求失败：' + e + '</p></div>';
            })
            .finally(function() {
                if (btn) { btn.disabled = false; btn.textContent = '🚀 生成今日报告'; }
            });
    }

    /** 轮询报告生成进度（进度条 + 当前正在做什么），title 区分 每日报告/盘中快报 */
    function pollReportProgress(container, timer, title) {
        fetch('/api/daily-report/progress')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success || !data.progress) return;
                var p = data.progress;
                if (p.status === 'done' || p.status === 'failed') {
                    // POST 返回后会渲染最终报告，此处停轮询即可
                    clearInterval(timer);
                    return;
                }
                renderProgressUI(container, p, title);
            })
            .catch(function() { /* 进度查询失败静默，下一轮重试 */ });
    }

    // 步骤时间线：阶段文本 → 步骤序号（0 准备 / 1 采集 / 2 分析 / 3 写入 / 4 完成）
    var _RP_STEP_LABELS = ['准备', '采集数据', '分析评分', '写入报告', '完成'];
    function _rpStepIndex(stage, status) {
        if (status === 'done') return 4;
        var s = stage || '';
        if (s.indexOf('准备') >= 0 || s.indexOf('开始') >= 0) return 0;
        if (s.indexOf('采集') >= 0) return 1;
        if (s.indexOf('分析') >= 0) return 2;
        if (s.indexOf('写入') >= 0) return 3;
        return 0;
    }

    /** 渲染动效进度面板：步骤时间线（✓ 完成 / 脉冲当前 / ✕ 失败）+ 流光进度条 + 当前股票/阶段 */
    function renderProgressUI(container, p, title) {
        var total = p.total || 1;
        var current = p.current || 0;
        var pct = Math.min(100, Math.round(current / total * 100));
        var stage = p.stage || '准备中';
        var failed = (p.status === 'failed') || (stage.indexOf('失败') >= 0);
        var stepIdx = failed ? 3 : _rpStepIndex(stage, p.status);
        var symbol = (p.current_symbol || '');
        var name = (p.current_name || '');
        var symbolText = symbol ? (symbol + (name ? ' ' + name : '')) : '';
        var heading = title || '正在生成每日报告';

        // 步骤时间线
        var stepsHtml = '';
        for (var i = 0; i < 5; i++) {
            var cls = 'rp-step';
            var dotText = String(i + 1);
            if (failed) {
                if (i < stepIdx) { cls += ' done'; dotText = '✓'; }
                else if (i === stepIdx) { cls += ' fail'; dotText = '✕'; }
            } else {
                if (i < stepIdx) { cls += ' done'; dotText = '✓'; }
                else if (i === stepIdx) { cls += ' active'; }
            }
            stepsHtml +=
                '<div class="' + cls + '">' +
                '<div class="rp-step-dot">' + dotText + '</div>' +
                '<div class="rp-step-label">' + _RP_STEP_LABELS[i] + '</div>' +
                '</div>';
        }

        container.innerHTML =
            '<div class="rp-card">' +
            '<div class="rp-title">📊 ' + heading + '<span class="rp-spinner"></span></div>' +
            '<div class="rp-steps">' + stepsHtml + '</div>' +
            '<div class="rp-bar"><div class="rp-bar-fill" style="width:' + pct + '%"></div></div>' +
            '<div class="rp-meta">' +
            '<span>' + pct + '% &nbsp;·&nbsp; 已完成 ' + current + ' / ' + total + ' 只</span>' +
            '<span class="rp-stage">' + (failed ? '⚠ ' : '') + stage + '</span>' +
            '</div>' +
            (symbolText
                ? '<div style="font-size:13px;color:var(--text-3,#888);margin-top:8px;">正在处理：<span class="rp-current">' + symbolText + '</span></div>'
                : '') +
            '</div>';
    }

    function generateIntradayReport() {
        var btn = document.getElementById('intradayGenBtn');
        var container = document.getElementById('dailyGenStatus');
        if (!container) return;
        if (btn) { btn.disabled = true; btn.textContent = '⏳ 生成中...'; }
        // 动效：立即渲染初始进度面板 + 每 1.5 秒轮询进度接口（能看到当前在做什么、做到哪一步）
        var pollTimer = setInterval(function() { pollReportProgress(container, pollTimer, '盘中快报'); }, 1500);
        renderProgressUI(container, {total: 1, current: 0, stage: '准备中', current_symbol: '', current_name: ''}, '盘中快报');

        fetch('/api/daily-report/generate-intraday', { method: 'POST', headers: {'Content-Type':'application/json'} })
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    clearInterval(pollTimer);
                    renderDailyReport(data);
                } else {
                    // 防抖拒绝（任务已在后台运行）时保留实时进度显示
                    if (data.message && data.message.indexOf('进行中') >= 0) {
                        container.innerHTML = '<div class="report-loading" style="color:#1a73e8;">⏳ 已有报告正在后台生成，实时进度如下...</div>';
                        return;
                    }
                    clearInterval(pollTimer);
                    container.innerHTML = '<div class="report-empty"><p style="color:#e74c3c;">盘中快报生成失败：' + (data.message || '未知错误') + '</p></div>';
                }
            })
            .catch(function(e) {
                clearInterval(pollTimer);
                container.innerHTML = '<div class="report-empty"><p style="color:#e74c3c;">请求失败：' + e + '</p></div>';
            })
            .finally(function() {
                if (btn) { btn.disabled = false; btn.textContent = '📊 盘中快报'; }
            });
    }

    // ========== US11-EXPORT: 导出功能 ==========
    function exportDailyExcel() {
        // 获取当前日报日期（如果页面已加载报告）
        var date = window._currentDailyDate || new Date().toISOString().slice(0, 10);
        window.location = '/api/export/daily-report?date=' + date;
    }

    function exportBacktestExcel() {
        var market = document.getElementById('btMarketSelect') ? document.getElementById('btMarketSelect').value : 'a_stock';
        window.location = '/api/export/backtest?market=' + market;
    }

    function loadLatestDailyReport() {
        var container = document.getElementById('dailyContent');
        if (!container) return; // 日报已融合到看板，无独立容器时不再渲染
        container.innerHTML = '<div class="report-loading">加载中...</div>';

        fetch('/api/daily-report/latest')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success && data.reports && data.reports.length > 0) {
                    window._currentDailyDate = data.report_date;
                    renderDailyReportList(data.report_date, data.reports);
                } else {
                    container.innerHTML = '<div class="report-empty">' +
                        '<p style="font-size:18px;margin-bottom:12px;">📅 每日分析报告</p>' +
                        '<p style="margin-bottom:16px;">暂无报告。基于v5.0引擎为全部自选股生成每日分析汇总报告，含评分变动、关键因子异动、降级提示。</p>' +
                        '<button class="btn btn-primary" onclick="generateDailyReport()" id="dailyGenBtn">🚀 生成今日报告</button>' +
                        '<button class="btn btn-warning" onclick="generateIntradayReport()" id="intradayGenBtn" style="margin-left:8px;">📊 盘中快报</button>' +
                        '<label style="margin-left:16px;font-size:13px;color:var(--text-2,#666);cursor:pointer;">' +
                        '<input type="checkbox" id="dailyForceRefresh" style="vertical-align:middle;"> 强制全量刷新（忽略已有结果）' +
                        '</label>' +
                        '</div>';
                }
            })
            .catch(function(e) {
                container.innerHTML = '<div class="report-empty"><p style="color:#e74c3c;">加载失败：' + e + '</p></div>';
            });
    }

    function renderDailyReport(genResult) {
        // 看板融合版：生成结果以紧凑提示显示在批量评分表上方，随后自动刷新表格
        var container = document.getElementById('dailyGenStatus');
        if (!container) return;
        var isintraday = genResult.report_type === 'intraday';
        var typeLabel = isintraday ? '盘中快报' : '收盘报告';
        var reuseCount = genResult.reuse_count || 0;
        var newCount = genResult.success_count - reuseCount;
        var failCount = genResult.fail_count || 0;
        var banner;
        if (newCount === 0 && reuseCount > 0) {
            // 021F：全部复用=未重算，醒目标注"沿用早前数据"并引导强制重算（避免静默沿用早盘分数）
            banner =
                '<div style="margin-bottom:12px;padding:12px 14px;background:#fff8e1;border:1px solid #f2d98b;border-radius:8px;font-size:13px;color:#8a6d1a;">' +
                '⚠️ <strong>' + typeLabel + '</strong>（' + (genResult.report_date || '') + '）已生成，但 <strong>' + reuseCount +
                ' 只全部沿用早前数据、未重新评分</strong>（分数仍是早前时点的）。' +
                '如需按最新数据重算：勾选「强制全量刷新」后重新生成，或在个股「分析报告」中点「🔄 刷新报告」。' +
                '</div>';
        } else {
            banner =
                '<div style="margin-bottom:12px;padding:10px 14px;background:#f0f9ff;border:1px solid #cfe7ff;border-radius:8px;font-size:13px;color:#1a5276;">' +
                '✅ <strong>' + typeLabel + '</strong>（' + (genResult.report_date || '') + '）生成完成：新分析 ' + newCount +
                ' 只 / 复用 ' + reuseCount + ' 只 / 失败 ' + failCount + ' 只，下方表格已刷新' +
                '</div>';
        }
        container.innerHTML = banner;
        refreshDashboardData();
    }

    /** 生成报告后轻量刷新看板表格与图表（保留表头提示信息，不整页重渲染）
     *  021E：fetch 显式 cache:'no-store' —— 服务端 watchlist-scores/summary 带 ETag，
     *  默认条件请求会命中 304 空响应（safeJson 非 JSON 判定 → success:false → 静默放弃刷新），
     *  导致"报告已生成但批量评分表不刷新"。no-store 直连网络恒取 200 全量。 */
    function refreshDashboardData() {
        var summaryPromise = fetch('/api/portfolio/summary', {cache: 'no-store'}).then(function(r) { return safeJson(r); });
        var scoresPromise  = fetch('/api/portfolio/watchlist-scores', {cache: 'no-store'}).then(function(r) { return safeJson(r); });
        Promise.all([summaryPromise, scoresPromise])
            .then(function(results) {
                var summary = results[0];
                var scores = results[1];
                if (!scores.success) {
                    var statusEl = document.getElementById('dailyGenStatus');
                    if (statusEl) {
                        statusEl.innerHTML = '<div style="margin-bottom:12px;padding:10px 14px;background:#fdf2f2;border:1px solid #f3c7c7;border-radius:8px;font-size:13px;color:#7b241c;">⚠️ 评分表刷新失败（' + (scores.message || '接口异常') + '），可点击「📊 详情」或刷新页面重试</div>';
                    }
                    return;
                }
                if (!_dashData) return;
                _dashData.summary = summary;
                _dashData.stocks = scores.stocks || [];
                _dashData.reportDate = scores.report_date;
                _dashData.reportDateMin = scores.report_date_min;
                _dashData.generatedAt = scores.generated_at;
                if (scores.report_date) window._currentDailyDate = scores.report_date;
                dashRenderTable(_dashData.stocks);
                dashRenderAdvice(_dashData.stocks);
                dashRenderCharts(_dashData.stocks, summary);
            })
            .catch(function(e) { console.error('refreshDashboardData:', e); });
    }

    /** 任一分析入口（批量分析/报告刷新/建议生成）完成后调用：
     *  看板已加载过则后台同步刷新其数据缓存与表格，切回看板即见最新评分，无需手动刷新。 */
    function refreshDashboardIfLoaded() {
        if (_dashData) refreshDashboardData();
    }

    function renderDailyReportList(reportDate, reports) {
        var container = document.getElementById('dailyContent');
        var html = '';

        // 019D: 计算本批最大生成时间
        var batchGenTime = '';
        reports.forEach(function(r) {
            if (r.generated_at && r.generated_at > batchGenTime) batchGenTime = r.generated_at;
        });

        html += '<div class="report-actions">';
        html += '<button class="report-back-btn" id="dailyGenBtn" onclick="generateDailyReport()">🚀 生成今日报告</button>';
        html += '<button class="report-back-btn" id="intradayGenBtn" onclick="generateIntradayReport()" style="background:#f39c12;color:#fff;margin-left:10px;">📊 盘中快报</button>';
        html += '<label style="margin-left:16px;font-size:13px;color:var(--text-2,#666);cursor:pointer;"><input type="checkbox" id="dailyForceRefresh" style="vertical-align:middle;"> 强制全量刷新（忽略已有结果）</label>';
        html += '<span style="color:var(--text-3,#888);font-size:13px;margin-left:15px;">最新报告日期：' + reportDate + '</span>';
        if (batchGenTime) {
            html += '<span style="color:var(--text-3,#888);font-size:13px;margin-left:15px;">本批生成时间：' + _fmtGenTime(batchGenTime) + '</span>';
        }
        html += '</div>';

        html += '<div class="card">';
        html += '<div class="card-title">📋 ' + reportDate + ' 评分概览</div>';
        html += '<table class="data-table" style="width:100%;border-collapse:collapse;">';
        html += '<thead><tr style="background:var(--surface-alt,#f5f5f5);text-align:left;">';
        html += '<th style="padding:8px;border-bottom:2px solid var(--border,#ddd);">股票</th>';
        html += '<th style="padding:8px;border-bottom:2px solid var(--border,#ddd);">引擎</th>';
        html += '<th style="padding:8px;border-bottom:2px solid var(--border,#ddd);">总分</th>';
        html += '<th style="padding:8px;border-bottom:2px solid var(--border,#ddd);">评级</th>';
        html += '<th style="padding:8px;border-bottom:2px solid var(--border,#ddd);">较昨日</th>';
        html += '<th style="padding:8px;border-bottom:2px solid var(--border,#ddd);" title="数据完整度：报告生成前对各维度数据新鲜度/来源的检查结果">数据</th>';
        html += '<th style="padding:8px;border-bottom:2px solid var(--border,#ddd);">生成于</th>';
        html += '</tr></thead><tbody>';

        reports.forEach(function(r) {
            if (r.status !== 'ok') {
                html += '<tr style="border-bottom:1px solid var(--border-light,#eee);background:#fff5f5;">';
                html += '<td style="padding:8px;"><strong>' + (r.stock_name || '') + '</strong><br><span style="color:var(--text-3,#888);font-size:12px;">' + r.stock_code + '</span></td>';
                html += '<td colspan="6" style="padding:8px;color:#e74c3c;">❌ 生成失败：' + (r.error_msg || '').substring(0, 50) + '</td>';
                html += '</tr>';
                return;
            }
            var engineTag = r.engine_version === 'v5'
                ? '<span style="color:#1a73e8;font-weight:600;">🚀 v5</span>'
                : '<span style="color:var(--text-3,#888);">⚙️ 经典</span>';
            var changeStr = '—';
            if (r.score_change != null) {
                var arrow = r.score_change > 0 ? '↑' : (r.score_change < 0 ? '↓' : '→');
                var color = r.score_change > 0 ? '#27ae60' : (r.score_change < 0 ? '#e74c3c' : '#888');
                changeStr = '<span style="color:' + color + ';">' + arrow + ' ' + Math.abs(r.score_change).toFixed(1) + '</span>';
            }
            // 020R-57：数据完整度三态标签（🔴异常/🟡滞后/✓正常，与报告页分类器同源）
            var dwList = [];
            try { dwList = JSON.parse(r.data_warnings || '[]'); } catch (e) { dwList = []; }
            var dataTag = _dataWarningTag(_dataWarningSummary(dwList, r.generated_at));
            html += '<tr style="border-bottom:1px solid var(--border-light,#eee);cursor:pointer;" onclick="viewReport(' + r.stock_id + ')" title="点击查看详细报告">';
            html += '<td style="padding:8px;"><strong>' + (r.stock_name || '') + '</strong><br><span style="color:var(--text-3,#888);font-size:12px;">' + r.stock_code + '</span></td>';
            html += '<td style="padding:8px;">' + engineTag + '</td>';
            html += '<td style="padding:8px;font-size:16px;font-weight:700;color:' + _scoreColor(r.total_score || 0) + ';">' + (r.total_score || 0).toFixed(1) + '</td>';
            html += '<td style="padding:8px;"><span class="rating-badge ' + getRatingClass(r.rating) + '" title="' + getRatingTitle(r.rating) + '">' + (r.rating || '—') + '</span></td>';
            html += '<td style="padding:8px;">' + changeStr + '</td>';
            html += '<td style="padding:8px;text-align:center;">' + dataTag + '</td>';
            html += '<td style="padding:8px;color:var(--text-3,#888);font-size:12px;">' + _fmtGenTime(r.generated_at) + '</td>';
            html += '</tr>';
        });

        html += '</tbody></table>';
        html += '</div>';

        container.innerHTML = html;
    }

    // ============================================================
    // P2: 总览看板 页面逻辑
    // ============================================================

    var _dashData = null;       // 看板原始数据缓存
    var _dashSortState = {};    // 排序状态

    function loadDashboard() {
        var container = document.getElementById('dashboardContent');
        container.innerHTML = '<div class="report-loading">正在加载总览看板...</div>';

        // 并行请求 summary + watchlist-scores
        // 021E：no-store 规避服务端 ETag 304 空响应导致的静默失败（同 refreshDashboardData）
        // 021O：移除 index-ratings 请求——大盘指数移至市场行情页查看，看板聚焦持仓/自选
        var summaryPromise = fetch('/api/portfolio/summary', {cache: 'no-store'}).then(function(r) { return safeJson(r); });
        var scoresPromise  = fetch('/api/portfolio/watchlist-scores', {cache: 'no-store'}).then(function(r) { return safeJson(r); });

        Promise.all([summaryPromise, scoresPromise])
            .then(function(results) {
                var summary = results[0];
                var scores = results[1];
                if (!scores.success) {
                    container.innerHTML = '<div class="report-empty"><p style="color:#e74c3c;">加载失败</p></div>';
                    return;
                }
                _dashData = { summary: summary, stocks: scores.stocks || [], reportDate: scores.report_date, reportDateMin: scores.report_date_min, generatedAt: scores.generated_at };
                renderDashboard(_dashData);
            })
            .catch(function(e) {
                container.innerHTML = '<div class="report-empty"><p style="color:#e74c3c;">加载失败：' + e + '</p></div>';
            });
    }

    function renderDashboard(data) {
        var container = document.getElementById('dashboardContent');
        var s = data.summary;
        var stocks = data.stocks;
        var html = '';

        // ---- 顶部标题栏 ----
        html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:20px;">';
        html += '<h2 style="margin:0;font-size:20px;">📈 总览看板</h2>';
        html += '<div>';
        if (data.reportDate) {
            var dateLabel = data.reportDate;
            if (data.reportDateMin && data.reportDateMin !== data.reportDate) {
                dateLabel = data.reportDateMin + ' ~ ' + data.reportDate;
            }
            html += '<span style="color:var(--text-3,#888);font-size:13px;margin-right:12px;">报告日期：' + dateLabel + '</span>';
        }
        if (data.generatedAt) {
            html += '<span style="color:var(--text-3,#888);font-size:13px;margin-right:12px;">生成时间：' + _fmtGenTime(data.generatedAt) + '</span>';
        }
        html += '<button class="btn btn-primary btn-sm" onclick="loadDashboard()" style="margin-right:8px;">🔄 刷新</button>';
        html += '</div></div>';

        // ---- 0.（021O 起大盘指数区域移除，改在市场行情页查看）----

        // ---- 1. 概览卡片 ----
        html += '<div class="dash-grid">';
        // 总资产
        var mv = s.total_market_value;
        html += '<div class="dash-card"><div class="dash-label">总资产</div>';
        html += '<div class="dash-value" style="color:var(--text,#333);">' + (mv != null ? formatCNY(mv) : '—') + '</div>';
        html += '<div class="dash-sub">&nbsp;</div></div>';
        // 当日盈亏（统一formatPnl + pnlColor）
        var pnl = s.total_unrealized_pnl;
        html += '<div class="dash-card"><div class="dash-label">持仓盈亏</div>';
        html += '<div class="dash-value" style="color:' + pnlColor(pnl) + ';">' + formatPnl(pnl) + '</div>';
        var pnlPct = (mv != null && pnl != null && mv > 0) ? (pnl / (mv - pnl) * 100).toFixed(2) : null;
        html += '<div class="dash-sub" style="color:' + pnlColor(pnl) + ';">' + (pnlPct != null ? pnlPct + '%' : '&nbsp;') + '</div></div>';
        // 持仓数
        html += '<div class="dash-card"><div class="dash-label">持仓 / 自选</div>';
        html += '<div class="dash-value" style="color:var(--text,#333);">' + s.active_count + '<span style="font-size:16px;color:var(--text-3,#888);"> / ' + stocks.length + '</span></div>';
        html += '<div class="dash-sub">&nbsp;</div></div>';
        // 平均评分
        var avgScore = s.avg_score;
        html += '<div class="dash-card"><div class="dash-label">平均评分</div>';
        html += '<div class="dash-value" style="color:' + _scoreColor(avgScore || 0) + ';">' + (avgScore != null ? avgScore.toFixed(1) : '—') + '</div>';
        var engStats = s.engine_stats || {};
        html += '<div class="dash-sub"><span style="color:#1a73e8;">v5:' + (engStats.v5 || 0) + '</span> <span style="color:var(--text-3,#888);margin-left:8px;">历史:' + (engStats.history || 0) + '</span></div></div>';
        html += '</div>'; // /dash-grid

        // ---- 1.5 操作建议卡片（评级×持仓盈亏 自动生成） ----
        html += '<div class="card" style="margin-bottom:20px;">';
        html += '<div class="card-title">📌 操作建议 <span style="font-size:13px;color:var(--text-3,#888);font-weight:normal;">（按操作紧急程度分级，点击个股查看报告）</span></div>';
        html += '<div id="dashAdviceList" style="display:flex;flex-wrap:wrap;gap:8px;"><span style="color:var(--text-3,#999);font-size:13px;">计算中...</span></div>';
        html += '<div style="margin-top:10px;font-size:12px;color:var(--text-3,#aaa);">以上建议由评分模型自动生成，仅供参考，不构成投资建议。</div>';
        html += '</div>';

        // ---- 2. 快速筛选器 ----
        html += '<div class="dash-filter-bar">';
        html += '<span style="font-weight:600;font-size:14px;">筛选：</span>';
        html += '<select id="dashFilterEngine" onchange="dashApplyFilter()"><option value="">全部引擎</option><option value="v5">v5引擎</option></select>';
        html += '<select id="dashFilterRating" onchange="dashApplyFilter()"><option value="">全部评级</option><option value="强烈推荐买入">强烈推荐买入</option><option value="推荐买入">推荐买入</option><option value="持有观望">持有观望</option><option value="建议减仓">建议减仓</option><option value="强烈建议卖出">强烈建议卖出</option></select>';
        html += '<select id="dashFilterIndustry" onchange="dashApplyFilter()"><option value="">全部行业</option>';
        // 动态填充行业选项
        var industries = {};
        stocks.forEach(function(st) { industries[st.industry || '未分类'] = true; });
        Object.keys(industries).sort().forEach(function(ind) {
            html += '<option value="' + ind + '">' + ind + '</option>';
        });
        html += '</select>';
        html += '<span style="margin-left:auto;color:var(--text-3,#888);font-size:13px;" id="dashFilterCount"></span>';
        html += '</div>';

        // ---- 3. 批量评分表（原每日报告评分概览表已并入本表） ----
        html += '<div class="card" style="margin-bottom:20px;">';
        html += '<div class="card-title" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;">';
        html += '<span>📋 批量评分表 <span style="font-size:13px;color:var(--text-3,#888);font-weight:normal;">（全部自选股最新评分，点击表头可排序）</span></span>';
        html += '<span style="font-weight:normal;">';
        html += '<button class="btn btn-primary btn-sm" id="dailyGenBtn" onclick="generateDailyReport()" title="盘后汇总，生成当日完整分析报告（含评分变动、降级提示）">🚀 生成今日报告</button>';
        html += '<button class="btn btn-warning btn-sm" id="intradayGenBtn" onclick="generateIntradayReport()" style="margin-left:8px;" title="盘中实时刷新评分，快速查看当日盘中变化（不覆盖盘后日报）">📊 盘中快报</button>';
        html += '<label style="margin-left:12px;font-size:13px;color:var(--text-2,#666);cursor:pointer;font-weight:normal;" title="忽略已有结果，全部重新分析"><input type="checkbox" id="dailyForceRefresh" style="vertical-align:middle;"> 强制全量刷新</label>';
        html += '</span></div>';
        // 生成结果提示区（生成完成后自动刷新下方表格）
        html += '<div id="dailyGenStatus"></div>';
        html += '<table class="data-table dash-table" style="width:100%;border-collapse:collapse;" id="dashTable">';
        html += '<thead><tr style="background:var(--surface-alt,#f5f5f5);text-align:left;">';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);" onclick="dashSort(\'name\')">股票 ↕</th>';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);">引擎</th>';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);" onclick="dashSort(\'score\')" title="四维加权总分（满分100）：技术面+基本面+资金面+消息面">评分 ↕</th>';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);">评级</th>';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);" onclick="dashSort(\'change\')">较上期 ↕</th>';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);" title="数据完整度：报告生成前对各维度数据新鲜度/来源的检查结果">数据</th>';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);">生成时间</th>';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);">报告日期</th>';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);">行业</th>';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);" onclick="dashSort(\'mv\')">市值 ↕</th>';
        html += '<th style="padding:10px;border-bottom:2px solid var(--border,#ddd);">操作</th>';
        html += '</tr></thead><tbody id="dashTableBody">';
        html += '</tbody></table>';
        html += '</div>';

        // ---- 4. 图表区 ----
        html += '<div class="dash-chart-row">';
        // 行业分布饼图
        html += '<div class="card"><div class="card-title">🏭 行业分布</div><div id="dashChartIndustry" style="width:100%;height:300px;"></div></div>';
        // 评级分布柱状图
        html += '<div class="card"><div class="card-title">📊 评级分布</div><div id="dashChartRating" style="width:100%;height:300px;"></div></div>';
        html += '</div>';

        container.innerHTML = html;

        // 渲染表格和图表
        dashRenderTable(stocks);
        dashRenderAdvice(stocks);
        dashRenderCharts(stocks, s);
        // 供「日报Excel」导出使用：记录当前报告日期
        if (data.reportDate) window._currentDailyDate = data.reportDate;
    }

    function dashRenderTable(stocks) {
        var tbody = document.getElementById('dashTableBody');
        if (!tbody) return;
        var html = '';
        stocks.forEach(function(st) {
            var engineTag = st.engine_version === 'v5'
                ? '<span style="color:#1a73e8;font-weight:600;">🚀 v5</span>'
                : '<span style="color:#ccc;">—</span>';
            var scoreStr = st.total_score != null ? st.total_score.toFixed(1) : '—';
            var changeStr = '—';
            if (st.score_change != null) {
                var arrow = st.score_change > 0 ? '↑' : (st.score_change < 0 ? '↓' : '→');
                var color = st.score_change > 0 ? '#e74c3c' : (st.score_change < 0 ? '#27ae60' : '#888');
                changeStr = '<span style="color:' + color + ';">' + arrow + ' ' + Math.abs(st.score_change).toFixed(1) + '</span>';
            }
            var mvStr = st.market_value != null ? formatCNY(st.market_value) : '—';
            var industryTag = st.industry === '未分类' ? '<span style="color:#f39c12;">⚠️ 未分类</span>' : st.industry;
            // 020R-54：行业资金背景徽标（所属行业当日主力方向 + 连续天数）
            var flowBadge = '';
            if (st.industry_flow_bg && st.industry_flow_bg.main_net != null) {
                var bg = st.industry_flow_bg;
                var dir = bg.main_net > 0 ? '流入' : (bg.main_net < 0 ? '流出' : '');
                if (dir) {
                    var bc = bg.main_net > 0 ? '#c62828' : '#1565c0';
                    var streakTxt = bg.streak_days > 0 ? ' 连续' + bg.streak_days + '日' : (bg.streak_days < 0 ? ' 连续' + Math.abs(bg.streak_days) + '日' : '');
                    flowBadge = '<span style="color:' + bc + ';font-size:11px;margin-left:4px;white-space:nowrap;" title="' +
                        bg.board + '：主力净' + dir + ' ' + Math.abs(bg.main_net / 1e8).toFixed(1) + ' 亿（第 ' + bg.rank + '/' + bg.total + ' 名，' + bg.trade_date + '）">' +
                        (bg.main_net > 0 ? '▲' : '▼') + Math.abs(bg.main_net / 1e8).toFixed(1) + '亿' + streakTxt + '</span>';
                }
            }
            // 020R-57：数据完整度三态标签（🔴异常/🟡滞后/✓正常，与报告页分类器同源）
            var dwList = [];
            try { dwList = JSON.parse(st.data_warnings || '[]'); } catch (e) { dwList = []; }
            var dataTag = _dataWarningTag(_dataWarningSummary(dwList, st.generated_at));

            html += '<tr style="border-bottom:1px solid var(--border-light,#eee);" id="dash-row-' + st.id + '">';
            html += '<td style="padding:10px;"><strong>' + (st.name || '') + obosBadge(st.obos_signal) + '</strong><br><span style="color:var(--text-3,#888);font-size:12px;">' + st.symbol + '</span></td>';
            html += '<td style="padding:10px;">' + engineTag + '</td>';
            html += '<td style="padding:10px;font-size:16px;font-weight:700;color:' + _scoreColor(st.total_score || 0) + ';">' + scoreStr + '</td>';
            html += '<td style="padding:10px;"><span class="rating-badge ' + getRatingClass(st.rating) + '" title="' + getRatingTitle(st.rating) + '">' + (st.rating || '—') + '</span></td>';
            html += '<td style="padding:10px;">' + changeStr + '</td>';
            html += '<td style="padding:10px;text-align:center;">' + dataTag + '</td>';
            html += '<td style="padding:10px;font-size:12px;color:var(--text-2,#666);white-space:nowrap;">' + _fmtGenTime(st.generated_at) + '</td>';
            html += '<td style="padding:10px;font-size:12px;color:var(--text-3,#888);white-space:nowrap;">' + (st.report_date || '<span style="color:#ccc;">暂无</span>') + '</td>';
            html += '<td style="padding:10px;font-size:13px;">' + industryTag + flowBadge + '</td>';
            html += '<td style="padding:10px;">' + mvStr + '</td>';
            html += '<td style="padding:10px;"><button class="btn btn-sm" style="padding:4px 10px;font-size:12px;" onclick="viewReport(' + st.id + ')">📊 详情</button></td>';
            html += '</tr>';
        });
        tbody.innerHTML = html;
        dashUpdateFilterCount(stocks.length, _dashData ? _dashData.stocks.length : stocks.length);
    }

    /**
     * 📌 操作建议卡片：按「评级 × 持仓盈亏」矩阵自动生成（与 advisor._determine_action 口径一致）。
     * 按操作紧急程度分级展示：🔴紧急处理（止损/减仓）→ 🟠考虑行动（买入/加仓/考虑减仓）→
     * 🟡保持关注（观望类）→ ⚪继续持有 → ⏳待评分；紧急级内亏损幅度大的排前。
     */
    function dashRenderAdvice(stocks) {
        var dom = document.getElementById('dashAdviceList');
        if (!dom) return;

        // RATING-ALIGN-004：评级归一化（兼容历史字母档位）
        var ratingOrder = ['强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出'];
        var legacyMap = { 'A': '强烈推荐买入', 'B+': '推荐买入', 'B': '持有观望', 'C': '建议减仓', 'D': '强烈建议卖出' };
        function _norm(r) {
            if (!r) return r;
            return ratingOrder.indexOf(r) >= 0 ? r : (legacyMap[r] || r);
        }

        // 与 advisor._determine_action 对齐的操作矩阵
        var MATRIX = {
            '强烈推荐买入': { '0': '买入',    '1-1': '加仓',     '1-0': '继续持有' },
            '推荐买入':     { '0': '买入',    '1-1': '持有',     '1-0': '继续持有' },
            '持有观望':     { '0': '关注',    '1-1': '持有',     '1-0': '持有' },
            '建议减仓':     { '0': '观望',    '1-1': '持有',     '1-0': '考虑减仓' },
            '强烈建议卖出': { '0': '回避',    '1-1': '减仓',     '1-0': '建议止损' }
        };
        // 动作 → 颜色（红=买入/机会，绿=卖出/风控，蓝=持有，橙=关注，灰=观望）
        var ACTION_COLOR = {
            '买入': '#e74c3c', '加仓': '#e74c3c',
            '持有': '#1a73e8', '继续持有': '#1a73e8',
            '持有观望': '#888', '观望': '#888', '回避': '#888',
            '考虑减仓': '#27ae60', '减仓': '#27ae60', '建议止损': '#27ae60',
            '关注': '#f39c12', '待评分': '#f39c12'
        };
        // 操作紧急程度分级（数字越小越紧急）
        var ACTION_LEVEL = {
            '建议止损': 1, '减仓': 1,
            '考虑减仓': 2, '买入': 2, '加仓': 2,
            '持有观望': 3, '关注': 3, '观望': 3, '回避': 3,
            '持有': 4, '继续持有': 4,
            '待评分': 5
        };
        var LEVELS = [
            { key: 1, icon: '🔴', label: '紧急处理', desc: '持仓出现止损/减仓信号，建议尽快评估' },
            { key: 2, icon: '🟠', label: '考虑行动', desc: '评级支持买入/加仓/减仓的方向性操作' },
            { key: 3, icon: '🟡', label: '保持关注', desc: '观望类建议，等待更明确的信号' },
            { key: 4, icon: '⚪', label: '继续持有', desc: '评级与盈亏支持持仓不动' },
            { key: 5, icon: '⏳', label: '待评分', desc: '暂无评分报告，建议先批量分析' }
        ];

        var items = [];
        stocks.forEach(function(st, idx) {
            var rating = _norm(st.rating);
            var hasPos = st.quantity != null && st.quantity > 0;
            var profitable = hasPos && st.unrealized_pnl != null && st.unrealized_pnl > 0;
            var key = hasPos ? (profitable ? '1-1' : '1-0') : '0';
            var action = '待评分';
            if (rating && MATRIX[rating]) action = MATRIX[rating][key] || '观望';
            // 盈亏百分比（用于紧急级内排序：亏损幅度大的排前）
            var pnlPct = null;
            if (st.quantity != null && st.quantity > 0 && st.cost_price && st.unrealized_pnl != null) {
                pnlPct = st.unrealized_pnl / (st.cost_price * st.quantity) * 100;
            }
            items.push({ st: st, action: action, idx: idx, pnlPct: pnlPct });
        });

        items.sort(function(a, b) {
            var la = ACTION_LEVEL[a.action] != null ? ACTION_LEVEL[a.action] : 9;
            var lb = ACTION_LEVEL[b.action] != null ? ACTION_LEVEL[b.action] : 9;
            if (la !== lb) return la - lb;
            if (la === 1) {
                // 紧急级内：亏损幅度大的排前
                var aP = a.pnlPct != null ? a.pnlPct : 0;
                var bP = b.pnlPct != null ? b.pnlPct : 0;
                return aP - bP;
            }
            return a.idx - b.idx;
        });

        if (items.length === 0) {
            dom.innerHTML = '<span style="color:var(--text-3,#999);font-size:13px;">暂无自选股数据。</span>';
            return;
        }

        var html = '';
        LEVELS.forEach(function(lv) {
            var group = items.filter(function(it) {
                var l = ACTION_LEVEL[it.action] != null ? ACTION_LEVEL[it.action] : 9;
                return l === lv.key;
            });
            if (group.length === 0) return;

            html += '<div style="width:100%;margin-top:8px;">';
            html += '<div style="font-size:13px;font-weight:700;color:var(--text,#444);margin-bottom:6px;">' + lv.icon + ' ' + lv.label +
                ' <span style="font-weight:400;color:var(--text-3,#999);font-size:12px;">· ' + lv.desc + '（' + group.length + '）</span></div>';
            html += '<div style="display:flex;flex-wrap:wrap;gap:8px;">';

            group.forEach(function(it) {
                var st = it.st;
                var action = it.action;
                var rating = _norm(st.rating);
                var color = ACTION_COLOR[action] || '#888';
                var scoreStr = st.total_score != null ? st.total_score.toFixed(1) : '—';

                // 持仓盈亏百分比（有成本价时展示）
                var pnlStr = '';
                if (st.quantity != null && st.quantity > 0 && st.cost_price && st.unrealized_pnl != null) {
                    var pct = st.unrealized_pnl / (st.cost_price * st.quantity) * 100;
                    var pColor = pct > 0 ? '#e74c3c' : (pct < 0 ? '#27ae60' : '#888');
                    pnlStr = '<span style="font-size:12px;font-weight:600;color:' + pColor + ';">' + (pct > 0 ? '+' : '') + pct.toFixed(1) + '%</span>';
                }

                // 021BM：买入侧动作带价格建议区间（读最新报告已存数据；有持仓显示补仓档）
                var zoneStr = '';
                var pa = st.pa_zone;
                if (pa && (action === '买入' || action === '加仓')) {
                    var lvTip = (pa.levels && pa.levels.length)
                        ? '；' + pa.levels.map(function(l) {
                              return (l.label || '档位') + ' ' + Number(l.price).toFixed(2) + (l.pct ? '（' + l.pct + '%仓位）' : '');
                          }).join('，')
                        : '';
                    var lvShow = (pa.levels && pa.levels.length)
                        ? ' · ' + pa.levels[0].label + ' ' + Number(pa.levels[0].price).toFixed(2)
                        : '';
                    var tip = pa.label + ' ' + Number(pa.low).toFixed(2) + ' - ' + Number(pa.high).toFixed(2) +
                        lvTip + (pa.stop_loss ? '；止损 ' + Number(pa.stop_loss).toFixed(2) : '') +
                        '（来自最新报告 ' + (st.report_date || '—') + '，点击查看完整价格建议）';
                    zoneStr = '<span style="font-size:11px;color:#1a73e8;background:rgba(26,115,232,0.08);padding:2px 7px;border-radius:10px;white-space:nowrap;cursor:help;" title="' + tip + '">' +
                        pa.label + ' ' + Number(pa.low).toFixed(2) + '~' + Number(pa.high).toFixed(2) + lvShow + '</span>';
                }

                var clickFn, clickTitle;
                if (action === '待评分') {
                    clickFn = 'quickBatchAnalyze(' + st.id + ')';
                    clickTitle = '暂无评分报告，点击自动勾选并批量分析该股';
                } else {
                    clickFn = 'viewReport(' + st.id + ')';
                    clickTitle = '点击查看个股分析报告';
                }

                html += '<span style="display:inline-flex;align-items:center;gap:6px;padding:6px 12px;border:1px solid var(--border-light,#e8e8e8);border-radius:20px;background:var(--surface-alt,#fafafa);cursor:pointer;transition:box-shadow .15s;" ' +
                    'onclick="' + clickFn + '" title="' + clickTitle + '" onmouseover="this.style.boxShadow=\'0 2px 8px rgba(0,0,0,0.12)\'" onmouseout="this.style.boxShadow=\'none\'">' +
                    '<span style="font-weight:700;font-size:12px;color:' + color + ';white-space:nowrap;">' + action + '</span>' +
                    '<strong style="font-size:13px;">' + (st.name || st.symbol) + '</strong>' +
                    (rating ? '<span style="font-size:11px;color:var(--text-3,#999);">' + st.symbol + '</span>' : '') +
                    (st.rating ? '<span class="rating-badge ' + getRatingClass(st.rating) + '" style="font-size:11px;">' + st.rating + '</span>' : '') +
                    '<span style="font-size:12px;color:var(--text-3,#888);">' + scoreStr + '分</span>' +
                    pnlStr +
                    zoneStr +
                    '</span>';
            });

            html += '</div></div>';
        });
        dom.innerHTML = html;
    }

    function dashRenderCharts(stocks, summary) {
        // 行业分布饼图
        var indData = {};
        stocks.forEach(function(st) {
            var ind = st.industry || '未分类';
            var mv = st.market_value || 0;
            indData[ind] = (indData[ind] || 0) + (mv > 0 ? mv : 1); // 无市值的按计数
        });
        var indChartDom = document.getElementById('dashChartIndustry');
        if (indChartDom && typeof echarts !== 'undefined') {
            var pieData = Object.keys(indData).map(function(k) { return { name: k, value: indData[k] }; });
            echarts.init(indChartDom).setOption({
                tooltip: { trigger: 'item', formatter: '{b}: {c} ({d}%)', backgroundColor: _themeCol('--surface', '#fff'), borderColor: _themeCol('--border', '#ccc'), textStyle: { color: _themeCol('--text', '#333') } },
                series: [{ type: 'pie', radius: ['40%', '70%'], data: pieData, label: { fontSize: 12, color: _themeCol('--text', '#333') } }]
            });
        }

        // 评级分布柱状图（RATING-ALIGN-004：中文5档 + 历史兼容归一化）
        var ratingOrder = ['强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出'];
        // ISSUE-1/2 修正：C→建议减仓, D→强烈建议卖出（与后端 RATING_LEGACY_MAP 对齐）
        var legacyMap = { 'A': '强烈推荐买入', 'B+': '推荐买入', 'B': '持有观望', 'C': '建议减仓', 'D': '强烈建议卖出' };
        // 前端归一化函数：旧字母→中文5档
        function _normRating(r) {
            if (!r) return r;
            return ratingOrder.indexOf(r) >= 0 ? r : (legacyMap[r] || r);
        }
        var rawDist = summary.rating_distribution || {};
        var dist = {};
        Object.keys(rawDist).forEach(function(k) {
            var normKey = ratingOrder.indexOf(k) >= 0 ? k : (legacyMap[k] || k);
            dist[normKey] = (dist[normKey] || 0) + rawDist[k];
        });
        var chartData = ratingOrder.filter(function(r) { return dist[r]; }).map(function(r) { return dist[r]; });
        var chartLabels = ratingOrder.filter(function(r) { return dist[r]; });
        var ratingChartDom = document.getElementById('dashChartRating');
        if (ratingChartDom && typeof echarts !== 'undefined') {
            var colors = { '强烈推荐买入': '#c8e6c9', '推荐买入': '#dcedc8', '持有观望': '#fff9c4', '建议减仓': '#ffe0b2', '强烈建议卖出': '#ffcdd2' };
            echarts.init(ratingChartDom).setOption({
                tooltip: { trigger: 'axis', backgroundColor: _themeCol('--surface', '#fff'), borderColor: _themeCol('--border', '#ccc'), textStyle: { color: _themeCol('--text', '#333') } },
                xAxis: { type: 'category', data: chartLabels, axisLabel: { color: _themeCol('--text-2', '#666') } },
                yAxis: { type: 'value', minInterval: 1, axisLabel: { color: _themeCol('--text-2', '#666') } },
                series: [{ type: 'bar', data: chartLabels.map(function(r) { return { value: dist[r], itemStyle: { color: colors[r] || '#3498db' } }; }), barWidth: '50%' }]
            });
        }
    }

    // ============================================================
    // B8: 大盘指数区域渲染
    // ============================================================
    function renderIndexSection(indices, updatedAt) {
        var html = '';
        html += '<div style="background:var(--surface,#fff);border-radius:12px;padding:16px 20px;margin-bottom:20px;box-shadow:0 1px 4px rgba(0,0,0,0.06);">';
        html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">';
        html += '<span style="font-weight:600;font-size:15px;">📊 大盘指数</span>';
        html += '<div>';
        if (updatedAt) { html += '<span style="color:var(--text-3,#aaa);font-size:12px;margin-right:10px;">更新: ' + updatedAt + '</span>'; }
        html += '<button class="btn btn-sm" style="padding:3px 10px;font-size:12px;border:1px solid var(--border,#ddd);border-radius:6px;background:var(--surface-alt,#f8f9fa);cursor:pointer;" onclick="refreshIndexRatings()">🔄 刷新指数评级</button>';
        html += '</div></div>';

        if (!indices || indices.length === 0) {
            html += '<div style="color:var(--text-3,#999);font-size:13px;padding:10px 0;">指数数据暂不可用，请点击“刷新指数评级”获取数据</div>';
            html += '</div>';
            return html;
        }

        html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px;">';
        for (var i = 0; i < indices.length; i++) {
            var idx = indices[i];
            var pctColor = '#999';
            var pctStr = '--';
            if (idx.pct_change != null) {
                pctColor = idx.pct_change >= 0 ? '#e74c3c' : '#27ae60';
                pctStr = (idx.pct_change >= 0 ? '+' : '') + idx.pct_change.toFixed(2) + '%';
            }
            var ratingColor = _indexRatingColor(idx.rating);
            var scoreStr = idx.total_score != null ? idx.total_score.toFixed(1) : '--';
            html += '<div style="background:var(--surface-alt,#f8f9fa);border-radius:8px;padding:10px 12px;text-align:center;">';
            html += '<div style="font-size:12px;color:var(--text-2,#666);margin-bottom:4px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;">' + idx.name + '</div>';
            html += '<div style="font-size:15px;font-weight:600;color:var(--text,#333);">' + (idx.close != null ? idx.close.toFixed(2) : '--') + '</div>';
            html += '<div style="font-size:13px;font-weight:500;color:' + pctColor + ';margin:2px 0;">' + pctStr + '</div>';
            html += '<div style="font-size:11px;color:' + ratingColor + ';font-weight:500;">' + (idx.rating || '--') + '</div>';
            html += '<div style="font-size:12px;color:var(--text-3,#888);margin-top:2px;">' + scoreStr + '分</div>';
            html += '</div>';
        }
        html += '</div>';
        html += '</div>';
        return html;
    }

    function _indexRatingColor(rating) {
        if (!rating) return '#999';
        if (rating.indexOf('推荐买入') >= 0 || rating.indexOf('强烈推荐') >= 0) return '#e74c3c';
        if (rating.indexOf('卖出') >= 0 || rating.indexOf('减仓') >= 0) return '#27ae60';
        return '#888';
    }

    function refreshIndexRatings() {
        var btn = event.target;
        btn.disabled = true;
        btn.textContent = '刷新中...';
        fetch('/api/index-ratings/refresh', {method: 'POST'})
            .then(function(r) { return r.json(); })
            .then(function(data) {
                btn.disabled = false;
                btn.textContent = '🔄 刷新指数评级';
                if (data.success) {
                    // 市场行情页刷新指数区；看板已移除指数区（021O），非市场页仅提示
                    if (window.location.hash === '#market') {
                        loadMarketIndexSection();
                    } else {
                        alert('指数评级已刷新，可在「市场行情」页查看');
                    }
                } else {
                    alert('指数刷新失败: ' + (data.error || '未知错误'));
                }
            })
            .catch(function(e) {
                btn.disabled = false;
                btn.textContent = '🔄 刷新指数评级';
                alert('指数刷新请求失败: ' + e);
            });
    }

    // ============================================================
    // 市场行情页：大盘指数 + 行业资金流向
    // ============================================================

    function loadMarketOverview() {
        loadMarketIndexSection();
        loadIndustryFlow();
    }

    function loadMarketIndexSection() {
        var dom = document.getElementById('marketIndexSection');
        if (!dom) return;
        dom.innerHTML = '<span style="color:var(--text-3,#999);font-size:13px;">加载中...</span>';
        fetch('/api/index-ratings')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!dom) return;
                if (data.success) {
                    dom.innerHTML = renderIndexSection(data.indices, data.updated_at);
                } else {
                    dom.innerHTML = '<div style="color:#e74c3c;font-size:13px;">指数数据获取失败：' + (data.error || '未知错误') + '</div>';
                }
            })
            .catch(function(e) {
                if (dom) dom.innerHTML = '<div style="color:#e74c3c;font-size:13px;">指数数据请求失败：' + e + '</div>';
            });
    }

    function loadIndustryFlow() {
        loadIndustryFlowFor(null);
    }

    // 020R-53：时间维度——支持按交易日回看历史快照
    function loadIndustryFlowFor(date) {
        var dom = document.getElementById('marketFlowList');
        var meta = document.getElementById('marketFlowMeta');
        var url = '/api/market/industry-fund-flow' + (date ? '?date=' + encodeURIComponent(date) : '');
        fetch(url)
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success) {
                    if (dom) dom.innerHTML = '<div class="empty">' + (data.error || '行业资金流暂不可用') + '，请点击「🔄 刷新」重试</div>';
                    return;
                }
                if (meta) {
                    meta.innerHTML = renderMarketFlowDateSelect(data.dates || [], data.trade_date)
                        + (data.updated_at ? '<span style="font-size:13px;color:var(--text-3,#888);margin-left:10px;">更新：' + String(data.updated_at).slice(5, 16) + '</span>' : '');
                }
                renderIndustryFlowTable(data.items, dom, data.trade_date, data.summary);
            })
            .catch(function(e) {
                if (dom) dom.innerHTML = '<div class="empty">行业资金流请求失败：' + e + '</div>';
            });
    }

    function renderMarketFlowDateSelect(dates, selected) {
        if (!dates || dates.length === 0) return '';
        var html = '<span style="font-size:13px;color:var(--text-3,#888);">交易日：</span><select id="marketFlowDateSel" style="font-size:13px;padding:2px 6px;border:1px solid var(--border,#ccc);border-radius:4px;" onchange="loadIndustryFlowFor(this.value)">';
        dates.forEach(function(d) {
            html += '<option value="' + d + '"' + (d === selected ? ' selected' : '') + '>' + d + '</option>';
        });
        html += '</select>';
        return html;
    }

    function refreshIndustryFlow() {
        var btn = document.getElementById('marketFlowRefreshBtn');
        if (btn) { btn.disabled = true; btn.textContent = '刷新中...'; }
        fetch('/api/market/industry-fund-flow/refresh', {method: 'POST'})
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (btn) { btn.disabled = false; btn.textContent = '🔄 刷新'; }
                if (data.success) {
                    var meta = document.getElementById('marketFlowMeta');
                    if (data.cooldown) {
                        // 020R-34：冷却期回放快照，温和提示（不弹窗）
                        if (meta) {
                            meta.innerHTML = '<span style="color:#e65100;font-size:13px;margin-right:10px;">' + (data.note || '限流冷却中，显示上次快照') + '</span>'
                                + renderMarketFlowDateSelect(data.dates || [], data.trade_date);
                        }
                        renderIndustryFlowTable(data.items, document.getElementById('marketFlowList'), data.trade_date);
                    } else {
                        // 刷新成功：重读最新快照（含 5 日累计列）并同步日期下拉
                        loadIndustryFlowFor();
                    }
                } else {
                    alert('行业资金流刷新失败：' + (data.error || '未知错误') + '。数据源限流时请稍后重试，页面仍显示上次快照。');
                }
            })
            .catch(function(e) {
                if (btn) { btn.disabled = false; btn.textContent = '🔄 刷新'; }
                alert('行业资金流刷新请求失败：' + e);
            });
    }

    // 020R-54：市场资金温度计（全行业主力净流入合计 + 流入/流出家数）
    function renderIndustryFlowSummary(summary) {
        if (!summary || summary.total_net === null || summary.total_net === undefined) return '';
        var tot = summary.total_net;
        var color = tot > 0 ? '#c62828' : tot < 0 ? '#1565c0' : '#666';
        var dirTxt = tot > 0 ? '净流入 ' : tot < 0 ? '净流出 ' : '净额 ';
        var inflowPct = summary.total > 0 ? Math.round(summary.inflow_count / summary.total * 100) : 0;
        return '<div style="background:var(--surface-alt,#f8f9fa);border:1px solid var(--border,#e0e0e0);border-radius:8px;padding:10px 14px;margin-bottom:12px;display:flex;align-items:center;flex-wrap:wrap;gap:10px;">' +
            '<span style="font-weight:600;">🌡️ 市场资金温度（' + summary.trade_date + '）</span>' +
            '<span style="color:' + color + ';font-weight:700;">全行业主力' + dirTxt + Math.abs(tot / 1e8).toFixed(1) + ' 亿</span>' +
            '<span style="font-size:12px;color:var(--text,#555);">流入 <b style="color:#c62828;">' + summary.inflow_count + '</b> 家 / 流出 <b style="color:#1565c0;">' + summary.outflow_count + '</b> 家 / 持平 ' + summary.flat_count + ' 家</span>' +
            '<span style="flex:1;min-width:120px;height:8px;background:#1565c0;border-radius:4px;position:relative;overflow:hidden;">' +
            '<span style="position:absolute;left:0;top:0;bottom:0;width:' + inflowPct + '%;background:#c62828;border-radius:4px 0 0 4px;"></span></span>' +
            '</div>';
    }

    // 021T：行业资金流表排序状态（三态：desc → asc → 原始序）
    var _flowSortState = { key: null, order: 'none' };

    /** 021T：行业资金流表排序切换（表头点击） */
    function flowSort(key) {
        if (_flowSortState.key === key) {
            _flowSortState.order = _flowSortState.order === 'desc' ? 'asc' : (_flowSortState.order === 'asc' ? 'none' : 'desc');
        } else {
            _flowSortState = { key: key, order: 'desc' };
        }
        renderIndustryFlowTable(_flowLastItems, document.getElementById('marketFlowList'), _flowLastDate, _flowLastSummary);
    }

    /** 021T：可排序表头（三态箭头：↓ 降序 / ↑ 升序 / ↕ 可排序） */
    function _flowTh(label, key, tip) {
        var active = _flowSortState.key === key && _flowSortState.order !== 'none';
        var arrow = active
            ? (_flowSortState.order === 'desc' ? ' ↓' : ' ↑')
            : ' <span style="color:#bbb;">↕</span>';
        var style = 'padding:8px;border-bottom:2px solid var(--border,#ddd);cursor:pointer;user-select:none;white-space:nowrap;' +
            (active ? 'color:#1565c0;' : '');
        var attr = tip ? ' title="' + tip + '"' : '';
        return '<th style="' + style + '"' + attr + ' onclick="flowSort(\'' + key + '\')">' + label + arrow + '</th>';
    }

    /** 021T：按当前排序状态重排行（None 值排最后，原始序保底） */
    function _flowSorted(items) {
        if (!_flowSortState.key || _flowSortState.order === 'none' || !_flowSortState.key) {
            return items;
        }
        var key = _flowSortState.key;
        var dir = _flowSortState.order === 'desc' ? -1 : 1;
        var arr = items.slice();
        arr.sort(function(a, b) {
            var va = a[key], vb = b[key];
            // null/undefined 统一排最后（与排序方向无关）
            var aNull = va == null || isNaN(va), bNull = vb == null || isNaN(vb);
            if (aNull && bNull) return 0;
            if (aNull) return 1;
            if (bNull) return -1;
            if (va === vb) return 0;
            return (va > vb ? 1 : -1) * dir;
        });
        return arr;
    }

    var _flowLastItems = null, _flowLastDate = null, _flowLastSummary = null;

    function renderIndustryFlowTable(items, dom, tradeDate, summary) {
        if (!dom) return;
        if (!items || items.length === 0) {
            dom.innerHTML = '<div class="empty">暂无行业资金流数据，请点击「🔄 刷新」获取</div>';
            return;
        }
        // 021T：缓存本批数据（表头点击重渲染用）
        _flowLastItems = items;
        _flowLastDate = tradeDate;
        _flowLastSummary = summary;
        // 持续流入/流出榜单与汇总基于原始序（与后端榜单口径一致），表格本体按排序状态展示
        var html = renderIndustryFlowSummary(summary);
        // 020R-54：持续流入/流出榜单（连续≥3日）
        var in3 = items.filter(function(it) { return (it.streak_days || 0) >= 3; }).slice(0, 5);
        var out3 = items.filter(function(it) { return (it.streak_days || 0) <= -3; }).slice(0, 5);
        if (in3.length || out3.length) {
            html += '<div style="font-size:12px;color:var(--text-2,#666);margin-bottom:10px;display:flex;flex-wrap:wrap;gap:6px;">';
            if (in3.length) {
                html += '<span>🔥 持续净流入（≥3日）：</span>' + in3.map(function(x) {
                    return '<span style="background:#fdecea;color:#c62828;border-radius:4px;padding:1px 8px;">' + x.name + ' ' + x.streak_days + '日</span>';
                }).join('');
            }
            if (out3.length) {
                html += '<span style="margin-left:8px;">💧 持续净流出（≥3日）：</span>' + out3.map(function(x) {
                    return '<span style="background:#e8f1fb;color:#1565c0;border-radius:4px;padding:1px 8px;">' + x.name + ' ' + Math.abs(x.streak_days) + '日</span>';
                }).join('');
            }
            html += '</div>';
        }
        html += '<table style="width:100%;border-collapse:collapse;"><thead><tr style="background:var(--surface-alt,#f5f5f5);text-align:left;">' +
            '<th style="padding:8px;border-bottom:2px solid var(--border,#ddd);width:36px;">#</th>' +
            '<th style="padding:8px;border-bottom:2px solid var(--border,#ddd);">行业</th>' +
            _flowTh('涨跌幅', 'pct_change') +
            _flowTh('主力净流入', 'main_net') +
            _flowTh('5日累计', 'main_net_5d', '截至该日（含）前 5 个交易日主力净流入累计') +
            _flowTh('连续', 'streak_days', '截至该日（含）连续净流入/流出天数') +
            _flowTh('主力净占比', 'main_pct') +
            _flowTh('超大单', 'super_net') +
            _flowTh('大单', 'big_net') +
            _flowTh('中单', 'mid_net') +
            _flowTh('小单', 'small_net') +
            '<th style="padding:8px;border-bottom:2px solid var(--border,#ddd);">领涨股</th>' +
            '</tr></thead><tbody>';
        _flowSorted(items).forEach(function(it, i) {
            var stk = it.streak_days || 0;
            var streakTxt = stk > 0
                ? '<span style="color:#c62828;font-weight:600;">流入' + stk + '日</span>'
                : stk < 0
                    ? '<span style="color:#1565c0;font-weight:600;">流出' + Math.abs(stk) + '日</span>'
                    : '<span style="color:var(--text-3,#999);">—</span>';
            html += '<tr style="border-bottom:1px solid var(--border-light,#eee);">' +
                '<td style="padding:6px 8px;color:var(--text-3,#999);font-size:12px;">' + (i + 1) + '</td>' +
                '<td style="padding:6px 8px;"><strong>' + (it.name || '—') + '</strong>' +
                '<span style="color:var(--text-3,#aaa);font-size:11px;margin-left:6px;">' + (it.code || '') + '</span></td>' +
                '<td style="padding:6px 8px;">' + fmtPct(it.pct_change) + '</td>' +
                '<td style="padding:6px 8px;">' + fmtFlow(it.main_net) + '</td>' +
                '<td style="padding:6px 8px;" title="截至 ' + (tradeDate || '') + '（含）前 5 个交易日累计">' + fmtFlow(it.main_net_5d) + '</td>' +
                '<td style="padding:6px 8px;font-size:12px;">' + streakTxt + '</td>' +
                '<td style="padding:6px 8px;">' + fmtPct(it.main_pct) + '</td>' +
                '<td style="padding:6px 8px;font-size:12px;">' + fmtFlow(it.super_net) + '</td>' +
                '<td style="padding:6px 8px;font-size:12px;">' + fmtFlow(it.big_net) + '</td>' +
                '<td style="padding:6px 8px;font-size:12px;">' + fmtFlow(it.mid_net) + '</td>' +
                '<td style="padding:6px 8px;font-size:12px;">' + fmtFlow(it.small_net) + '</td>' +
                '<td style="padding:6px 8px;font-size:12px;color:var(--text-2,#666);">' + (it.lead_stock || '—') + '</td>' +
                '</tr>';
        });
        html += '</tbody></table>';
        dom.innerHTML = html;
    }

    /** 资金流金额（元）→ 万/亿 格式化，红流入绿流出 */
    function fmtFlow(v) {
        if (v == null || isNaN(v)) return '<span style="color:var(--text-3,#999);">—</span>';
        var abs = Math.abs(v);
        var val = v / 10000;
        var unit = '万';
        if (abs >= 100000000) { val = v / 100000000; unit = '亿'; }
        var color = v > 0 ? '#e74c3c' : (v < 0 ? '#27ae60' : '#888');
        var sign = v > 0 ? '+' : (v < 0 ? '-' : '');
        return '<span style="color:' + color + ';font-weight:600;">' + sign +
            Math.abs(val).toLocaleString('zh-CN', {maximumFractionDigits: 1}) + unit + '</span>';
    }

    /** 百分比格式化（红涨绿跌） */
    function fmtPct(v) {
        if (v == null || isNaN(v)) return '<span style="color:var(--text-3,#999);">—</span>';
        var color = v > 0 ? '#e74c3c' : (v < 0 ? '#27ae60' : '#888');
        return '<span style="color:' + color + ';font-weight:600;">' + (v > 0 ? '+' : '') + v.toFixed(2) + '%</span>';
    }

    // 筛选
    function dashApplyFilter() {
        if (!_dashData) return;
        var engFilter = document.getElementById('dashFilterEngine').value;
        var rtFilter = document.getElementById('dashFilterRating').value;
        var indFilter = document.getElementById('dashFilterIndustry').value;

        var filtered = _dashData.stocks.filter(function(st) {
            if (engFilter && st.engine_version !== engFilter) return false;
            // ISSUE-2：筛选时归一化评级比较，兼容历史旧字母
            if (rtFilter && _normRating(st.rating) !== rtFilter) return false;
            if (indFilter && (st.industry || '未分类') !== indFilter) return false;
            return true;
        });
        dashRenderTable(filtered);
        dashRenderCharts(filtered, _dashData.summary);
    }

    // 排序
    function dashSort(field) {
        if (!_dashData) return;
        // 三态排序
        var state = _dashSortState[field] || 'none';
        var order = state === 'none' ? 'desc' : (state === 'desc' ? 'asc' : 'none');
        _dashSortState[field] = order;
        // 重置其他字段
        Object.keys(_dashSortState).forEach(function(k) { if (k !== field) _dashSortState[k] = 'none'; });

        if (order === 'none') {
            dashRenderTable(_dashData.stocks);
            dashApplyFilter();
            return;
        }

        // 取当前筛选后的数据
        var engFilter = document.getElementById('dashFilterEngine').value;
        var rtFilter = document.getElementById('dashFilterRating').value;
        var indFilter = document.getElementById('dashFilterIndustry').value;
        var list = _dashData.stocks.filter(function(st) {
            if (engFilter && st.engine_version !== engFilter) return false;
            if (rtFilter && st.rating !== rtFilter) return false;
            if (indFilter && (st.industry || '未分类') !== indFilter) return false;
            return true;
        });

        list.sort(function(a, b) {
            var va, vb;
            if (field === 'name') { va = a.name || ''; vb = b.name || ''; }
            else if (field === 'score') { va = a.total_score || -1; vb = b.total_score || -1; }
            else if (field === 'change') { va = a.score_change || 0; vb = b.score_change || 0; }
            else if (field === 'mv') { va = a.market_value || 0; vb = b.market_value || 0; }
            if (typeof va === 'string') {
                return order === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
            }
            return order === 'asc' ? va - vb : vb - va;
        });
        dashRenderTable(list);
    }

    function dashUpdateFilterCount(shown, total) {
        var el = document.getElementById('dashFilterCount');
        if (el) el.textContent = '显示 ' + shown + ' / ' + total + ' 只';
    }

    /**
     * 渲染单个维度卡片（三段式：得分→状态→≤3行关键指标）
     */
    function _renderDimensionCard(key, label, dimInfo) {
        // 无数据卡片
        if (!dimInfo || dimInfo.status === 'failed' || dimInfo.status === 'no_data') {
            return '<div class="dim-card">' +
                '<div class="dim-weight-corner">权重 —</div>' +
                '<div class="dim-top"><span class="dim-score-big" style="color:#ccc;">—</span>' +
                '<span class="dim-name">' + label + '</span></div>' +
                '<div class="dim-mid"><span class="dim-status-badge dim-badge-nodata">— 无数据</span></div>' +
                '<div class="dim-factors"><span style="color:#bbb;font-size:12px;">请先采集数据</span></div>' +
            '</div>';
        }

        var score = dimInfo.score != null ? dimInfo.score : 0;
        var scoreColor = _scoreColor(score);

        // 状态标签：✅健康 ≥70 / ⚠️偏弱 40-69 / 🔴风险 <40
        var badgeClass, badgeIcon, badgeText;
        if (score >= 70) {
            badgeClass = 'dim-badge-health'; badgeIcon = '\u2705'; badgeText = '健康';
        } else if (score >= 40) {
            badgeClass = 'dim-badge-weak'; badgeIcon = '\u26a0\ufe0f'; badgeText = '偏弱';
        } else {
            badgeClass = 'dim-badge-risk'; badgeIcon = '\ud83d\udd34'; badgeText = '风险';
        }

        // 关键指标：按优先级取 ≤3 个
        var factors = dimInfo.factors || {};
        var topFactors = _pickTopFactors(key, factors);

        var dimLabelTips = {
            'kline': '技术面：基于K线、均线、RSI、布林带等价格走势指标的分析',
            'fundamental': '基本面：基于PE、PB、ROE、营收增长等财务指标的分析',
            'capital_flow': '资金面：基于主力资金流向、融资余额等资金动向的分析',
            'news': '消息面：基于新闻舆情、市场情绪等文本数据的分析'
        };
        var dimLabelTip = dimLabelTips[key] || '';

        var factorsHtml = '';
        if (key === 'kline' && _reportTechDetail) {
            // 020R-36：技术指标明细并入技术面卡（均线/MACD/RSI/KDJ/布林/量能 + 近期走势）
            factorsHtml = _renderTechRows(_reportTechDetail, factors);
        } else if (key === 'fundamental' && _reportFundDetail) {
            // 020R-37：基本面指标明细并入基本面卡（估值/盈利/成长/现金流/财务健康 + 基本面趋势）
            factorsHtml = _renderFundamentalRows(_reportFundDetail, factors);
        } else if (key === 'capital_flow' && _reportCapDetail) {
            // 020R-38：资金面指标明细并入资金面卡（主力资金/互联互通/杠杆资金）
            factorsHtml = _renderCapitalRows(_reportCapDetail);
        } else if (key === 'news' && _reportNewsDetail) {
            // 020R-39：消息面指标明细并入消息面卡（情绪/新闻概览/重要新闻/股东行为）
            factorsHtml = _renderNewsRows(_reportNewsDetail);
        } else if (topFactors.length === 0) {
            factorsHtml = '<span style="color:#bbb;font-size:12px;">暂无关键因子</span>';
        } else {
            topFactors.forEach(function(item) {
                var isNeg = _isNegativeIndicator(item.value, key);
                var negClass = isNeg ? ' factor-row-negative' : '';
                var negValClass = isNeg ? ' factor-val-negative' : '';
                var titleAttr = item.tooltip ? ' title="' + item.tooltip + '" style="cursor:help;border-bottom:1px dotted #aaa;"' : '';
                factorsHtml +=
                    '<div class="factor-row' + negClass + '">' +
                    '<span class="factor-label"' + titleAttr + '>' + item.label + '</span>' +
                    '<span class="factor-val' + negValClass + '">' +
                    (isNeg ? '\ud83d\udd34 ' : '') + item.value +
                    '</span></div>';
            });
        }

        return '<div class="dim-card">' +
            '<div class="dim-weight-corner">权重 ' + ((dimInfo.weight || 0) * 100).toFixed(0) + '%</div>' +
            '<div class="dim-top">' +
                '<span class="dim-score-big" style="color:' + scoreColor + ';">' + score.toFixed(0) + '</span>' +
                '<span class="dim-name"' + (dimLabelTip ? ' title="' + dimLabelTip + '" style="cursor:help;border-bottom:1px dotted #ccc;"' : '') + '>' + label + '</span>' +
            '</div>' +
            '<div class="dim-mid">' +
                '<span class="dim-status-badge ' + badgeClass + '">' + badgeIcon + ' ' + badgeText + '</span>' +
            '</div>' +
            '<div class="dim-factors">' + factorsHtml + '</div>' +
        '</div>';
    }

    /**
     * 020R-35/36：技术指标明细——状态语义着色
     */
    function _techStateColor(state) {
        if (!state) return '#666';
        if (['超买', '触及上轨'].indexOf(state) >= 0) return '#f39c12';
        if (['超卖', '触及下轨'].indexOf(state) >= 0) return '#1a73e8';
        if (['多头排列', '上轨区', '中轨上方', '中性偏强'].indexOf(state) >= 0) return '#e74c3c';
        if (['空头排列', '下轨区', '中轨下方', '偏弱'].indexOf(state) >= 0) return '#27ae60';
        if (state.indexOf('多头') >= 0 || state.indexOf('金叉') >= 0) return '#e74c3c';
        if (state.indexOf('空头') >= 0 || state.indexOf('死叉') >= 0) return '#27ae60';
        if (/上方/.test(state)) return '#e74c3c';   // 020R-48：价在MA5上方
        if (/下方/.test(state)) return '#27ae60';   // 020R-48：价在MA5下方
        if (state.indexOf('放量') >= 0) return '#e67e22';
        return '#666';
    }

    /**
     * 020R-36：技术指标明细行（并入技术面卡内）——六类指标 + 近期走势
     * 021S：打分子项按周期分组折叠——月线方向层 25% / 周线波段层 45% / 日线择时层 30%，
     * 每组 <details> 可折叠；组内先列评分引擎 7 子项（得分+实际权重+明细），
     * 再列指标读数（均线/MACD/RSI/KDJ/布林/量能）。子项缺失时退回纯指标读数形态。
     */
    function _renderTechRows(td, factors) {
        function _row(label, body, state) {
            var color = _techStateColor(state);
            return '<div class="factor-row">' +
                '<span class="factor-label">' + label + '</span>' +
                '<span class="factor-val"><span style="color:var(--text,#333);font-weight:400;">' + (body || '—') + '</span>' +
                (state ? ' <span style="color:' + color + ';font-weight:700;">' + state + '</span>' : '') +
                '</span></div>';
        }

        var html = '<div style="font-size:11px;color:var(--text-3,#999);margin-bottom:2px;">技术面打分子项（按周期分组，点击折叠）' +
            (td.latest_date ? '（K线截至 ' + td.latest_date + '）' : '') + '</div>';

        // ---- 021S：评分引擎 7 子项（月线1 + 周线3 + 日线3），与 TECHNICAL_SUBITEMS 同口径 ----
        var subs = td.scoring_subitems || null;

        function _subRow(key, fallbackLabel) {
            var s = subs && subs[key];
            if (!s) {
                return _row(fallbackLabel, '<span style="color:#bbb;font-weight:400;">数据缺失（不占权重）</span>', null);
            }
            var parts = [];
            var det = s.detail || {};
            for (var k in det) {
                if (det.hasOwnProperty(k)) parts.push(String(det[k]));
            }
            var wPct = s.normalized_weight != null
                ? (s.normalized_weight * 100).toFixed(0)
                : (s.base_weight * 100).toFixed(0);
            var body = '<span style="color:' + _scoreColor(s.score) + ';font-weight:700;">' +
                Number(s.score).toFixed(1) + '分</span>' +
                '<span style="color:var(--text-3,#999);font-weight:400;font-size:11px;">（权重' + wPct + '%）</span>' +
                (parts.length
                    ? ' <span style="color:var(--text,#555);font-weight:400;">' + parts.join(' · ') + '</span>'
                    : '');
            return _row(s.name || fallbackLabel, body, null);
        }

        // 层内得分：子项按实际归一化权重的加权平均（层内归一，跨层可比）
        function _layerScore(keys) {
            if (!subs) return null;
            var sum = 0, w = 0;
            keys.forEach(function(kk) {
                var s = subs[kk];
                if (s && s.normalized_weight > 0) { sum += Number(s.score) * s.normalized_weight; w += s.normalized_weight; }
            });
            return w > 0 ? sum / w : null;
        }

        function _group(title, layerWeight, layerScore, inner) {
            // 021S：自定义折叠箭头——隐藏原生三角（list-style:none），用 ▸ 字符 +
            // details[open] 时旋转 90° 成 ▾，干净不与文字挤在一行
            return '<details open class="tech-fold" style="margin:4px 0;border:1px solid #e8eaee;border-radius:8px;background:var(--surface-alt,#fafbfc);">' +
                '<summary style="cursor:pointer;padding:6px 10px;font-size:12px;font-weight:600;color:var(--text,#444);user-select:none;list-style:none;display:flex;align-items:center;">' +
                '<span class="tech-fold-arrow" style="display:inline-block;margin-right:6px;color:#8a94a6;font-size:10px;transition:transform .15s;">▶</span>' +
                '<span>' + title +
                ' <span style="color:var(--text-3,#999);font-weight:400;font-size:11px;">· 权重' + layerWeight + '</span></span>' +
                (layerScore != null
                    ? '<span style="margin-left:auto;color:' + _scoreColor(layerScore) + ';font-weight:700;">' + layerScore.toFixed(1) + '分</span>'
                    : '') +
                '</summary>' +
                '<div style="padding:2px 10px 8px;border-top:1px dashed #eee;">' + inner + '</div>' +
                '</details>';
        }

        // ---- 月线方向层（25%）----
        // 021S：月线同样有六类指标（technical_detail.py 已按 monthly_ 前缀计算），
        // MA 排列 + MACD + RSI + KDJ + 布林 + 量能 + 近12月走势全量展示
        var monthlyInner = _subRow('monthly_trend', '月线方向');
        monthlyInner += _row('月线均线',
            (td.monthly_ma5 != null
                ? ('MA5 ' + td.monthly_ma5 +
                    (td.monthly_ma10 != null ? ' · MA10 ' + td.monthly_ma10 : '') +
                    (td.monthly_ma20 != null ? ' · MA20 ' + td.monthly_ma20 : ''))
                : null),
            td.monthly_ma_state);
        if (td.monthly_macd_state != null) {
            monthlyInner += _row('月线MACD',
                (td.monthly_macd_dif != null
                    ? ('DIF ' + td.monthly_macd_dif + ' · DEA ' + td.monthly_macd_dea +
                        ' · 柱 ' + (td.monthly_macd_hist >= 0 ? '+' : '') + td.monthly_macd_hist)
                    : null),
                td.monthly_macd_state);
        }
        if (td.monthly_rsi14 != null) {
            monthlyInner += _row('月线RSI', String(td.monthly_rsi14), td.monthly_rsi_state);
        }
        if (td.monthly_kdj_k != null) {
            monthlyInner += _row('月线KDJ',
                ('K ' + td.monthly_kdj_k + ' · D ' + td.monthly_kdj_d + ' · J ' + td.monthly_kdj_j),
                td.monthly_kdj_state);
        }
        if (td.monthly_boll_position != null) {
            monthlyInner += _row('月线布林',
                ('位置 ' + td.monthly_boll_position + '% · 上' + td.monthly_boll_upper + '/下' + td.monthly_boll_lower),
                td.monthly_boll_state);
        }
        if (td.monthly_vol_ratio != null) {
            monthlyInner += _row('月线量能',
                ('当月量/20月均量 ' + td.monthly_vol_ratio), td.monthly_vol_state);
        }
        if (td.monthly_latest_close != null) {
            monthlyInner += _row('月线收盘', String(td.monthly_latest_close), null);
        }
        if (td.monthly_penalty) {
            monthlyInner += '<div style="font-size:11px;color:#e67e22;margin:2px 0;">⚠ ' + td.monthly_penalty + '</div>';
        }
        html += _group('📈 月线方向层', '25%', _layerScore(['monthly_trend']), monthlyInner);

        // ---- 周线波段层（45%）----
        var weeklyInner = _subRow('weekly_trend', '周线趋势') +
            _subRow('weekly_obos', '周线超买超卖') +
            _subRow('weekly_vol', '周线波动');
        if (td.weekly_ma_state != null) {
            weeklyInner += _row('周线均线',
                (td.weekly_ma10 != null ? ('MA10 ' + td.weekly_ma10 + ' · MA20 ' + td.weekly_ma20) : null),
                td.weekly_ma_state);
        }
        if (td.weekly_macd_state != null) {
            weeklyInner += _row('周线MACD',
                (td.weekly_macd_dif != null ? ('DIF ' + td.weekly_macd_dif + ' · DEA ' + td.weekly_macd_dea) : null),
                td.weekly_macd_state);
        }
        if (td.weekly_rsi14 != null) {
            weeklyInner += _row('周线RSI', String(td.weekly_rsi14), td.weekly_rsi_state);
        }
        if (td.weekly_boll_position != null) {
            weeklyInner += _row('周线布林',
                ('位置 ' + td.weekly_boll_position + '% · 上' + td.weekly_boll_upper + '/下' + td.weekly_boll_lower),
                td.weekly_boll_state);
        }
        if (td.weekly_kdj_k != null) {
            weeklyInner += _row('周线KDJ',
                ('K ' + td.weekly_kdj_k + ' · D ' + td.weekly_kdj_d + ' · J ' + td.weekly_kdj_j),
                td.weekly_kdj_state);
        }
        if (td.weekly_vol_ratio != null) {
            weeklyInner += _row('周线量能', ('本周量/20周均量 ' + td.weekly_vol_ratio), td.weekly_vol_state);
        }
        html += _group('📊 周线波段层', '45%', _layerScore(['weekly_trend', 'weekly_obos', 'weekly_vol']), weeklyInner);

        // ---- 日线择时层（30%）----
        var dailyInner = _subRow('obos', '超买超卖') +
            _subRow('vol_price', '量价分析') +
            _subRow('vol_ratio', '量比') +
            _row('均线系统',
                (td.ma5 != null ? ('MA5 ' + td.ma5 + ' · MA10 ' + td.ma10 + ' · MA20 ' + td.ma20) : null),
                td.ma_state) +
            _row('MACD趋势',
                (td.macd_dif != null ? ('DIF ' + td.macd_dif + ' · DEA ' + td.macd_dea + ' · 柱 ' + (td.macd_hist >= 0 ? '+' : '') + td.macd_hist) : null),
                td.macd_state) +
            _row('RSI(14)', (td.rsi14 != null ? String(td.rsi14) : null), td.rsi_state) +
            _row('KDJ', (td.kdj_k != null ? ('K ' + td.kdj_k + ' · D ' + td.kdj_d + ' · J ' + td.kdj_j) : null), td.kdj_state) +
            _row('布林带',
                (td.boll_position != null
                    ? ('位置 ' + td.boll_position + '% · 上' + td.boll_upper + '/中' + td.boll_mid + '/下' + td.boll_lower)
                    : null),
                td.boll_state) +
            _row('量能', (td.vol_ratio != null ? ('量比 ' + td.vol_ratio) : null), td.vol_state);
        if (factors && factors.recent_trend) {
            dailyInner += _row('近期走势', String(factors.recent_trend), null);
        }
        html += _group('📉 日线择时层', '30%', _layerScore(['obos', 'vol_price', 'vol_ratio']), dailyInner);

        return html;
    }

    /**
     * 020R-37：基本面指标明细——状态语义着色（红=好/低估值，绿=差/高估，灰=中性）
     */
    function _fundStateColor(state) {
        if (!state) return '#666';
        var GOOD = ['低估', '破净', '合理偏低', '优秀', '良好', '高', '中高',
                    '高增长', '较快增长', '稳步增长', '充裕', '健康', '低杠杆', '充足',
                    '预增', '略增', '续盈', '扭亏', '快报增'];
        var BAD = ['偏高', '高估', '严重高估', '负值', '较差', '亏损', '低',
                   '小幅下滑', '明显下滑', '偏弱', '为负·警惕', '高杠杆', '极高杠杆', '偏紧', '紧张',
                   '预减', '略减', '首亏', '续亏', '快报减'];
        if (GOOD.indexOf(state) >= 0) return '#e74c3c';
        if (BAD.indexOf(state) >= 0) return '#27ae60';
        return '#666';
    }

    /**
     * 020R-37：基本面指标明细行（并入基本面卡内）——五类子项 + 基本面趋势
     */
    /**
     * 021V：维度折叠组通用工具（基本面/资金面/消息面，与技术面 021S 同视觉语言）
     * _dimFoldRow    —— 指标读数行（原 factor-row 结构）
     * _dimFoldSubRow —— 评分子项行（得分着色 + 实际权重% + 引擎明细文案）
     * _dimFoldStats  —— 组内统计（基础权重合计 + 按归一化权重的加权分）
     * _dimFoldGroup  —— 折叠组容器（▶ 箭头随展开旋转，组头含权重与组内得分）
     */
    function _dimFoldRow(label, html) {
        return '<div class="factor-row">' +
            '<span class="factor-label">' + label + '</span>' +
            '<span class="factor-val">' + (html || '—') + '</span></div>';
    }

    function _dimFoldSubRow(subs, key, fallbackLabel) {
        var s = subs && subs[key];
        if (!s) {
            return _dimFoldRow(fallbackLabel, '<span style="color:#bbb;font-weight:400;">数据缺失（不占权重）</span>');
        }
        var parts = [];
        var det = s.detail || {};
        for (var k in det) {
            if (det.hasOwnProperty(k)) parts.push(String(det[k]));
        }
        var wPct = s.normalized_weight != null
            ? (s.normalized_weight * 100).toFixed(0)
            : (s.base_weight * 100).toFixed(0);
        var body = '<span style="color:' + _scoreColor(s.score) + ';font-weight:700;">' +
            Number(s.score).toFixed(1) + '分</span>' +
            '<span style="color:var(--text-3,#999);font-weight:400;font-size:11px;">（权重' + wPct + '%）</span>' +
            (parts.length
                ? ' <span style="color:var(--text,#555);font-weight:400;">' + parts.join(' · ') + '</span>'
                : '');
        return _dimFoldRow(s.name || fallbackLabel, body);
    }

    function _dimFoldStats(subs, keys) {
        if (!subs) return { wpct: null, score: null };
        var sum = 0, w = 0, base = 0;
        keys.forEach(function(kk) {
            var s = subs[kk];
            if (!s) return;
            base += s.base_weight;
            if (s.normalized_weight > 0) {
                sum += Number(s.score) * s.normalized_weight;
                w += s.normalized_weight;
            }
        });
        return {
            wpct: base > 0 ? Math.round(base * 100) : null,
            score: w > 0 ? sum / w : null
        };
    }

    function _dimFoldGroup(title, icon, wpct, score, inner) {
        return '<details open class="tech-fold" style="margin:4px 0;border:1px solid #e8eaee;border-radius:8px;background:var(--surface-alt,#fafbfc);">' +
            '<summary style="cursor:pointer;padding:6px 10px;font-size:12px;font-weight:600;color:var(--text,#444);user-select:none;list-style:none;display:flex;align-items:center;">' +
            '<span class="tech-fold-arrow" style="display:inline-block;margin-right:6px;color:#8a94a6;font-size:10px;transition:transform .15s;">▶</span>' +
            '<span>' + icon + ' ' + title +
            (wpct != null ? ' <span style="color:var(--text-3,#999);font-weight:400;font-size:11px;">· 权重' + wpct + '%</span>' : '') +
            '</span>' +
            (score != null
                ? '<span style="margin-left:auto;color:' + _scoreColor(score) + ';font-weight:700;">' + score.toFixed(1) + '分</span>'
                : '') +
            '</summary>' +
            '<div style="padding:2px 10px 8px;border-top:1px dashed #eee;">' + inner + '</div>' +
            '</details>';
    }

    function _renderFundamentalRows(fd, factors) {
        function _fv(value, state) {
            var color = _fundStateColor(state);
            return '<span style="color:' + color + ';">' +
                (value != null ? String(value) : '—') +
                (state ? ' ' + state : '') + '</span>';
        }
        var subs = fd.scoring_subitems || null;
        var html = '<div style="font-size:11px;color:var(--text-3,#999);margin-bottom:2px;">基本面打分子项（按层分组，点击折叠）' +
            (fd.report_date ? '（最新财报 ' + fd.report_date + '）' : '') + '</div>';

        // ---- 021V：估值与盈利层（估值 25% + 盈利能力 30%）----
        var g1 = _dimFoldStats(subs, ['valuation', 'profitability']);
        var g1Html = '';
        if (subs) {
            g1Html += _dimFoldSubRow(subs, 'valuation', '估值') +
                _dimFoldSubRow(subs, 'profitability', '盈利能力');
        }
        g1Html += _dimFoldRow('估值',
            (fd.pe != null ? ('PE ' + _fv(fd.pe, fd.pe_state)) : '') +
            (fd.pb != null ? (' · PB ' + _fv(fd.pb, fd.pb_state)) : ''));
        g1Html += _dimFoldRow('盈利能力',
            (fd.roe != null ? ('ROE ' + _fv(fd.roe + '%', fd.roe_state)) : '') +
            (fd.gross_margin != null ? (' · 毛利率 ' + _fv(fd.gross_margin + '%', fd.gm_state)) : ''));
        html += _dimFoldGroup('估值与盈利', '📊', g1.wpct, g1.score, g1Html);

        // ---- 成长性层（25%；业绩预告/快报折价参评，020R-49/50）----
        var g2 = _dimFoldStats(subs, ['growth']);
        var g2Html = subs ? _dimFoldSubRow(subs, 'growth', '成长性') : '';
        g2Html += _dimFoldRow('成长性',
            (fd.revenue_growth != null ? ('营收 ' + _fv((fd.revenue_growth > 0 ? '+' : '') + fd.revenue_growth + '%', fd.rg_state)) : '') +
            (fd.profit_growth != null ? (' · 净利 ' + _fv((fd.profit_growth > 0 ? '+' : '') + fd.profit_growth + '%', fd.pg_state)) : ''));
        if (fd.forecast_type) {
            var fcBody = fd.forecast_type;
            if (fd.forecast_change_pct != null) {
                fcBody += ' ' + (fd.forecast_change_pct > 0 ? '+' : '') + fd.forecast_change_pct + '%';
            }
            if (fd.forecast_period) {
                var fp = String(fd.forecast_period);
                if (fd.forecast_type === '业绩快报') {
                    fcBody += '（' + fp.slice(0, 4) + '年' + fp.slice(4, 6) + '月快报）';
                } else {
                    fcBody += '（' + fp.slice(0, 4) + '年' + fp.slice(4, 6) + '月报预告）';
                }
            }
            if (fd.forecast_type === '业绩快报') {
                var exState = fd.forecast_change_pct > 0 ? '快报增' :
                    (fd.forecast_change_pct < 0 ? '快报减' : null);
                g2Html += _dimFoldRow('业绩快报',
                    '<span style="color:' + _fundStateColor(exState) + ';">' + fcBody + '</span>');
            } else {
                g2Html += _dimFoldRow('业绩预告', _fv(fcBody, fd.forecast_type));
            }
        }
        html += _dimFoldGroup('成长性', '📈', g2.wpct, g2.score, g2Html);

        // ---- 财务质量层（现金流质量 10% + 财务健康度 10%）----
        var g3 = _dimFoldStats(subs, ['cashflow', 'fin_health']);
        var g3Html = '';
        if (subs) {
            g3Html += _dimFoldSubRow(subs, 'cashflow', '现金流质量') +
                _dimFoldSubRow(subs, 'fin_health', '财务健康度');
        }
        g3Html += _dimFoldRow('现金流质量',
            fd.ocf_to_profit != null ? ('经营现金流/净利润 ' + _fv(fd.ocf_to_profit, fd.ocf_state)) : '');
        g3Html += _dimFoldRow('财务健康度',
            (fd.debt_ratio != null ? ('负债率 ' + _fv(fd.debt_ratio + '%', fd.dr_state)) : '') +
            (fd.current_ratio != null ? (' · 流动比率 ' + _fv(fd.current_ratio, fd.cr_state)) : ''));
        if (factors && factors.fund_trend) {
            g3Html += _dimFoldRow('基本面趋势', '<span style="color:var(--text,#333);font-weight:400;">' + String(factors.fund_trend) + '</span>');
        }
        html += _dimFoldGroup('财务质量', '🛡️', g3.wpct, g3.score, g3Html);

        return html;
    }

    /**
     * 020R-38：资金面指标明细——状态语义着色（流入/买入/增加红，流出/卖出/减少绿）
     */
    function _capitalStateColor(state) {
        if (!state) return '#666';
        // 020R-45：股东人数/机构持仓语义（筹码集中/机构高配=好→红；筹码分散/机构极少=差→绿）
        if (/(筹码集中|户数略降)/.test(state)) return '#e74c3c';
        if (/(筹码分散|户数略增)/.test(state)) return '#27ae60';
        if (/(机构重仓|机构高配)/.test(state)) return '#e74c3c';
        if (/(机构中等持仓|机构低配)/.test(state)) return '#e67e22';
        if (/机构极少/.test(state)) return '#27ae60';
        // 021Q：港股机构股东数量（增加=好→红；减少=差→绿，与A股户数方向相反）
        if (/机构数量(大)?增加/.test(state)) return '#e74c3c';
        if (/机构数量(大)?减少/.test(state)) return '#27ae60';
        if (/机构数量持平/.test(state)) return '#666';
        // 021Q：南下资金（增持=好→红；减持=差→绿）
        if (/南下.*增持/.test(state)) return '#e74c3c';
        if (/南下.*减持/.test(state)) return '#27ae60';
        if (/(流入|买入|增加)/.test(state)) return '#e74c3c';
        if (/(流出|卖出|减少)/.test(state)) return '#27ae60';
        return '#666';
    }

    /** 万元金额 → 万/亿 显示（带符号） */
    function _wanFmt(v) {
        if (v == null || isNaN(v)) return '—';
        var sign = v > 0 ? '+' : (v < 0 ? '-' : '');
        var abs = Math.abs(v);
        var text = abs >= 10000 ? (abs / 10000).toFixed(2) + '亿' : abs.toFixed(0) + '万';
        return sign + text;
    }

    /**
     * 020R-38：资金面指标明细行（并入资金面卡内）——主力资金/主力5日均/互联互通/杠杆资金
     */
    function _renderCapitalRows(cd) {
        function _fv(value, state) {
            var color = _capitalStateColor(state);
            return '<span style="color:' + color + ';">' + value +
                (state ? ' ' + state : '') + '</span>';
        }
        var subs = cd.scoring_subitems || null;
        var isHk = _reportMarket === 'hk_stock';
        var html = '<div style="font-size:11px;color:var(--text-3,#999);margin-bottom:2px;">资金面打分子项（按层分组，点击折叠）' +
            (cd.trade_date ? '（数据截至 ' + cd.trade_date + '）' : '') + '</div>';

        // ---- 021V：主力资金层（50%）----
        var g1 = _dimFoldStats(subs, ['main_capital']);
        var g1Html = subs ? _dimFoldSubRow(subs, 'main_capital', '主力资金') : '';
        g1Html += _dimFoldRow('主力资金',
            cd.main_net != null ? _fv(_wanFmt(cd.main_net), cd.main_state) :
            '<span style="color:var(--text-3,#999);">数据缺失</span>');
        if (cd.main_avg_5d != null) {
            g1Html += _dimFoldRow('主力5日均', '<span style="color:var(--text,#333);font-weight:400;">' + _wanFmt(cd.main_avg_5d) + '</span>');
        }
        // 021Q：南下资金·个股（港股通标的，展示暂不参评）
        if (cd.south_stock_net != null) {
            var sbBody = _fv(_wanFmt(cd.south_stock_net) + '港元', cd.south_stock_state);
            if (cd.south_hold_ratio != null) sbBody += '<span style="color:var(--text-3,#888);font-weight:400;"> · 持股占比 ' + cd.south_hold_ratio + '%</span>';
            g1Html += _dimFoldRow('南下资金·个股', sbBody);
        }
        html += _dimFoldGroup('主力资金', '💰', g1.wpct, g1.score, g1Html);

        // ---- 机构与筹码层（机构持仓 20% + 股东人数 10%）----
        var g2 = _dimFoldStats(subs, ['inst_hold', 'holder_count']);
        var g2Html = '';
        if (subs) {
            g2Html += _dimFoldSubRow(subs, 'inst_hold', '机构持仓') +
                _dimFoldSubRow(subs, 'holder_count', '股东人数');
        }
        g2Html += _dimFoldRow('机构持仓',
            cd.inst_ratio != null
                ? _fv(cd.inst_ratio + '%' + (cd.inst_report_date ? '（' + cd.inst_report_date + '）' : ''), cd.inst_state)
                : '<span style="color:var(--text-3,#999);">数据缺失</span>');
        // 021U/021Q：A股显示户数环比；港股显示机构股东数代理；两边皆无则 A股保留缺失提示、港股整行省略
        if (cd.holder_count_change_pct != null) {
            g2Html += _dimFoldRow('股东人数',
                '户数环比 ' + _fv((cd.holder_count_change_pct > 0 ? '+' : '') + cd.holder_count_change_pct + '%', cd.holder_state));
        } else if (cd.inst_count != null) {
            var icBody = cd.inst_count + ' 家机构';
            if (cd.inst_count_change_pct != null) {
                icBody += '（环比 ' + (cd.inst_count_change_pct > 0 ? '+' : '') + cd.inst_count_change_pct + '%）';
            }
            g2Html += _dimFoldRow('机构股东数(港股)', _fv(icBody, cd.inst_count_state));
        } else if (!isHk) {
            g2Html += _dimFoldRow('股东人数', '<span style="color:var(--text-3,#999);">数据缺失</span>');
        }
        // 020R-47：南向资金大盘参考（仅港股展示，不参评）
        if (cd.south_net_buy != null) {
            var southBody = '今日净买 ' + (cd.south_net_buy > 0 ? '+' : '') + cd.south_net_buy + ' 亿元';
            if (cd.south_hold_mv != null) southBody += ' · 持股市值 ' + cd.south_hold_mv + ' 万亿港元';
            if (cd.south_date) southBody += '（' + cd.south_date + '）';
            g2Html += _dimFoldRow('南向资金（参考）',
                '<span style="color:var(--text-3,#888);font-weight:400;">' + southBody + ' · 不参评</span>');
        }
        html += _dimFoldGroup('机构与筹码', '🏦', g2.wpct, g2.score, g2Html);

        // ---- 杠杆资金层（20%）----
        // 021U：港股无两融披露源（制度性恒缺）→ 整组不渲染；A股缺失保留提示行（可能是数据源故障）
        if (!isHk) {
            var g3 = _dimFoldStats(subs, ['margin_capital']);
            var g3Html = (subs ? _dimFoldSubRow(subs, 'margin_capital', '杠杆资金') : '') +
                _dimFoldRow('杠杆资金',
                    cd.margin_chg != null ? ('融资余额 ' + _fv(_wanFmt(cd.margin_chg), cd.margin_state)) :
                    '<span style="color:var(--text-3,#999);">数据缺失（A股两融）</span>');
            html += _dimFoldGroup('杠杆资金', '⚖️', g3.wpct, g3.score, g3Html);
        }

        return html;
    }

    /**
     * 020R-39：消息面指标明细——状态语义着色（正面红、负面绿、中性灰、增持红、减持绿）
     */
    function _newsStateColor(state) {
        if (!state) return '#666';
        if (/(正面|增持|利好)/.test(state)) return '#e74c3c';
        if (/(负面|减持)/.test(state)) return '#27ae60';
        return '#666';
    }

    /**
     * 020R-39：消息面指标明细行（并入消息面卡内）——情绪/新闻概览/重要新闻/股东行为
     */
    function _renderNewsRows(nd) {
        function _fv(value, state) {
            var color = _newsStateColor(state);
            return '<span style="color:' + color + ';">' + value +
                (state ? ' ' + state : '') + '</span>';
        }
        var subs = nd.scoring_subitems || null;
        var html = '<div style="font-size:11px;color:var(--text-3,#999);margin-bottom:2px;">消息面打分子项（按层分组，点击折叠）' +
            (nd.news_date ? '（新闻截至 ' + nd.news_date + '）' : '') + '</div>';

        // ---- 021V：市场情绪层（70%）----
        var g1 = _dimFoldStats(subs, ['sentiment']);
        var g1Html = subs ? _dimFoldSubRow(subs, 'sentiment', '情绪') : '';
        g1Html += _dimFoldRow('情绪',
            nd.avg_sentiment != null
                ? _fv((nd.avg_sentiment > 0 ? '+' : '') + nd.avg_sentiment.toFixed(2), nd.sentiment_state)
                : '<span style="color:var(--text-3,#999);">数据缺失</span>');
        if (nd.total_count != null) {
            var overview = '共 ' + nd.total_count + ' 条';
            if (nd.positive_ratio != null) overview += ' · 正面 ' + nd.positive_ratio + '%';
            if (nd.negative_count != null) overview += ' · 负面 ' + nd.negative_count;
            g1Html += _dimFoldRow('新闻概览', '<span style="color:var(--text,#333);font-weight:400;">' + overview + '</span>');
        }
        if (nd.top_news) {
            var t = String(nd.top_news);
            if (t.length > 40) t = t.slice(0, 40) + '…';
            g1Html += _dimFoldRow('重要新闻', '<span style="color:var(--text,#333);font-weight:400;">' + t + '</span>');
        }
        html += _dimFoldGroup('市场情绪', '📰', g1.wpct, g1.score, g1Html);

        // ---- 股东行为层（30%，020R-44 三态显示）----
        var g2 = _dimFoldStats(subs, ['holder']);
        var g2Html = subs ? _dimFoldSubRow(subs, 'holder', '股东行为') : '';
        if (nd.holder === true) {
            g2Html += _dimFoldRow('股东行为', _fv('增持', '增持·利好'));
        } else if (nd.holder === false) {
            g2Html += _dimFoldRow('股东行为',
                '<span style="color:#27ae60;font-weight:600;">近30天无增持</span>');
        } else {
            g2Html += _dimFoldRow('股东行为',
                '<span style="color:var(--text-3,#999);">数据缺失（接口不可用或港股未采集）</span>');
        }
        html += _dimFoldGroup('股东行为', '👥', g2.wpct, g2.score, g2Html);

        return html;
    }

    /**
     * 按维度优先级选取 ≤3 个关键因子
     */
    function _pickTopFactors(dimKey, factors) {
        var priority = _factorPriority[dimKey] || [];
        var labels = _dimFactorLabels[dimKey] || {};
        var tooltips = (_dimFactorTooltips[dimKey] || {});
        var result = [];
        for (var i = 0; i < priority.length && result.length < 3; i++) {
            var fk = priority[i];
            var fv = factors[fk];
            if (fv != null && fv !== '' && labels[fk]) {
                result.push({ label: labels[fk], value: String(fv), tooltip: tooltips[fk] || '' });
            }
        }
        return result;
    }

    /**
     * 负面指标检测：含关键词则标记红色高亮
     */
    function _isNegativeIndicator(val, dimKey) {
        if (val == null) return false;
        var s = String(val);
        // 正面指标豁免表：含这些关键词的字段不做负面检测
        // 例如 "正面10/负面0/中性0" 虽含"负面"但整体是正面数据
        var positiveContext = ['\u6b63\u9762\u5360\u6bd4', 'positive_ratio', '\u6b63\u9762\u65b0\u95fb'];
        // 负面关键词
        var negWords = ['\u6d41\u51fa', '\u4e0b\u964d', '\u8d85\u4e70', '\u8f83\u5dee',
                        '\u504f\u4f4e', '\u98ce\u9669', '\u8b66\u6212',
                        '\u5927\u5e45\u6d41\u51fa', '\u7a7a\u5934', '\u8d70\u5f31',
                        '\u503c\u504f\u9ad8', '\u8fde\u7eed\u6d41\u51fa', '\u8d85\u5356',
                        '\u8d1f\u9762\u65b0\u95fb'];
        for (var i = 0; i < negWords.length; i++) {
            if (s.indexOf(negWords[i]) >= 0) {
                // 检查是否在正面语境中（如“正面10/负面0/中性0”）
                if (s.indexOf('\u6b63\u9762') >= 0 && s.indexOf('/') >= 0) return false;
                return true;
            }
        }
        // 数值型负数（仅对资金面、基本面字段检查）
        if (dimKey === 'capital_flow' || dimKey === 'fundamental') {
            var numMatch = s.match(/-?[\d.]+/);
            if (numMatch) {
                var num = parseFloat(numMatch[0]);
                if (num < 0) return true;
            }
        }
        return false;
    }

    /** 各维度关键因子优先级（从高到低，取前3） */
    var _factorPriority = {
        kline: ['ma_trend', 'rsi_status', 'recent_trend', 'volume', 'boll_position'],
        // 019P：fund_trend 首位（因子卡必显，监理"就一条不够"的可见性落地）
        fundamental: ['fund_trend', 'pe_ratio', 'roe', 'revenue_growth', 'pb_ratio', 'net_margin', 'debt_ratio'],
        capital_flow: ['main_trend', 'consecutive', 'main_pct', 'super_large', 'main_avg_5d'],
        news: ['avg_sentiment', 'positive_ratio', 'news_count', 'top_news', 'news_activity', 'extreme_warning']
    };

    /** 维度关键因子中文标签映射 */
    var _dimFactorLabels = {
        kline: {
            ma_trend: '均线趋势', rsi_status: 'RSI状态', volume: '成交量',
            recent_trend: '近期走势', boll_position: '布林位置',
            ma5: 'MA5', ma20: 'MA20', rsi: 'RSI值',
            boll_upper: '布林上轨', boll_lower: '布林下轨', boll_mid: '布林中轨'
        },
        fundamental: {
            // 019P：fund_trend 标签
            fund_trend: '基本面趋势',
            pe_ratio: 'PE', pb_ratio: 'PB', roe: 'ROE',
            revenue_growth: '营收增长', net_margin: '净利率',
            debt_ratio: '负债率', gross_margin: '毛利率',
            fund_trend_detail: '趋势明细'
        },
        capital_flow: {
            main_trend: '主力趋势', consecutive: '连续流入/流出',
            main_avg_5d: '主力5日均', main_pct: '主力净占比',
            super_large: '超大单净流入'
        },
        news: {
            avg_sentiment: '平均情绪', news_activity: '新闻活跃度',
            positive_ratio: '正面占比', news_count: '新闻数量',
            top_news: '重要新闻', extreme_warning: '极端情绪预警'
        }
    };

    /** U4(#4): 各维度关键因子通俗解释（鼠标悬浮提示） */
    var _dimFactorTooltips = {
        kline: {
            ma_trend: '均线趋势：反映近期价格走向。多头排列（短>长）偏强，空头排列偏弱',
            rsi_status: 'RSI=相对强弱指标，范围0-100。>70偏热（超买），<30偏冷（超卖）',
            volume: '成交量：反映市场参与活跃度。放量上涨可信度更高',
            recent_trend: '近期走势：短期内股价的涨跌方向',
            boll_position: '布林带位置：反映价格在波动通道中的位置。贴近上轨偏强，贴近下轨偏弱'
        },
        fundamental: {
            // 019P：fund_trend tooltip（口径双轨制说明）
            fund_trend: '基本面趋势：对比最近8期财报（毛利率/净利率/负债率等较上期，ROE按同比），改善/恶化/平稳。仅展示，不影响评分',
            pe_ratio: 'PE=市盈率，股价÷每股收益。一般越低越便宜，但需结合行业判断（银行/地产天然偏低）',
            roe: 'ROE=净资产收益率，净利润÷净资产。越高代表公司盈利能力越强，>15%为优秀',
            revenue_growth: '营收增长率：反映公司成长性。正值代表增长，负值代表萎缩',
            pb_ratio: 'PB=市净率，股价÷每股净资产。越低可能越被低估，但需排除亏损股',
            net_margin: '净利率：净利润÷营收。越高代表赚钱效率越好',
            debt_ratio: '负债率：负债÷总资产。过高（>70%）可能有偿债风险',
            gross_margin: '毛利率：反映产品竞争力。越高代表定价权越强',
            fund_trend_detail: '趋势明细：单指标趋势串（ROE同比、毛利率环比等），口径：累计型ROE仅同比、增速看加快/放缓'
        },
        capital_flow: {
            main_trend: '主力资金趋势：反映大资金整体是流入还是流出。净流入偏多，净流出偏空',
            consecutive: '连续流入/流出天数：连续流入可能预示上涨动力，连续流出需警惕',
            main_pct: '主力净占比：主力净买入额占总成交的比例。正值表示主力净买入',
            super_large: '超大单净流入：机构级别大额资金动向。正值表示机构资金在买入',
            main_avg_5d: '主力5日均：近5个交易日主力资金平均动向，过滤单日波动'
        },
        news: {
            avg_sentiment: '平均情绪：新闻情绪的综合评分。正值偏正面，负值偏负面',
            news_activity: '新闻活跃度：近期新闻数量和关注度。活跃度高说明市场关注度高',
            positive_ratio: '正面占比：正面新闻在总数中的比例。越高说明舆论越乐观',
            news_count: '新闻数量：近期相关新闻报道总数',
            top_news: '重要新闻：近期最受关注的新闻摘要',
            extreme_warning: '极端情绪预警：情绪过度乐观或悲观时触发，建议人工复核原文'
        }
    };

    /**
     * 021AB：雷达图圆心计算（.radar-card 卡片几何中心，补偿标题占用的高度）。
     * 从 _renderRadarChart 内提出为共享函数——渲染与窗口 resize 重算复用同一口径。
     * 公式：center[1] = (cardH/2 - chartTopOffset) / chartH * 100%
     */
    function _radarCalcCenter(chartDom) {
        var cardDom = chartDom.parentElement;
        if (!cardDom) return ['50%', '50%'];
        var cardH = cardDom.clientHeight;
        var chartH = chartDom.clientHeight;
        var chartTopOffset = chartDom.offsetTop;
        if (chartH <= 0 || cardH <= 0) return ['50%', '50%'];
        var cy = (cardH / 2 - chartTopOffset) / chartH * 100;
        // 边界保护：补偿范围通常在 30%~70%，超出则回退 50%
        if (cy < 5 || cy > 95) return ['50%', '50%'];
        return ['50%', cy + '%'];
    }

    /**
     * 021AB：雷达图 resize 自适应——先 resize 画布，再在布局稳定后重算圆心。
     * 背景：020R 移除报告页K线卡后 _renderKlineChart 无人调用（死代码），原本绑在
     * 它体内的窗口 resize 监听随之失效，雷达图在窗口尺寸变化时不跟随容器（漂移）。
     * 顺序很重要：resize() 只按新容器缩放画布、不改 center 百分比；而圆心补偿量
     * 随容器几何变化，必须 resize 后按新几何重算 setOption。rAF + 120ms 双触发，
     * 兜底 CSS Grid stretch 在 resize 过程中的延迟稳定。
     */
    function _radarResizeRecenter() {
        if (!_radarChart || _radarChart.isDisposed()) return;
        try { _radarChart.resize(); } catch (e) {}
        var applyCenter = function() {
            if (!_radarChart || _radarChart.isDisposed()) return;
            var dom = document.getElementById('radarChart');
            if (!dom) return;
            try { _radarChart.setOption({ radar: { center: _radarCalcCenter(dom) } }); } catch (e) {}
        };
        requestAnimationFrame(applyCenter);
        setTimeout(applyCenter, 120);
    }

    /**
     * 021AC：雷达图容器尺寸监听——ResizeObserver 为主，window resize 兜底。
     * 021AB 的 window resize 监听有两处不足（实测仍漂移）：
     *   1) 时机：resize 事件回调里 CSS Grid stretch 可能尚在过渡，读到的几何非终态；
     *      ResizeObserver 在布局完成后才回调，几何必然已定，且覆盖断点切换等
     *      一切布局变化来源（不限于窗口 resize）。
     *   2) 根因在 CSS：#radarChart 原 flex-basis:auto 以内容（canvas 固定像素高）
     *      为基准，容器高度被旧 canvas 粘住、resize() 读到污染几何——已在 CSS 侧
     *      改 flex:1 1 0 切断反馈循环，本函数配合保证重算用的一直是布局终态。
     * 每次 _renderRadarChart 以当前 chartDom 重新 observe（innerHTML 重建后旧节点
     * 已脱离文档，观察它无意义）；rAF 合并连续触发（拖拽窗口时高频回调去抖）。
     */
    var _radarResizeObserver = null;

    function _bindRadarResize(chartDom) {
        if (!window._radarResizeBound) {
            window._radarResizeBound = true;
            window.addEventListener('resize', _radarResizeRecenter);
        }
        if (typeof ResizeObserver !== 'function') return;  // 老浏览器：仅 window resize
        if (_radarResizeObserver) {
            try { _radarResizeObserver.disconnect(); } catch (e) {}
        }
        var pending = false;
        _radarResizeObserver = new ResizeObserver(function() {
            if (pending) return;
            pending = true;
            requestAnimationFrame(function() {
                pending = false;
                _radarResizeRecenter();
            });
        });
        _radarResizeObserver.observe(chartDom);
    }

    /**
     * 渲染 ECharts 四维雷达图
     *
     * 居中策略（修复了浏览器 resize 时 absolute 方案溢出问题）：
     *   1. CSS 端：flex 布局，#radarChart 用 flex:1 1 0 占满标题下方空间
     *      （021AC：basis 必须为 0，auto 会被旧 canvas 内容高度粘住导致漂移）。
     *   2. ECharts 端：center 动态计算（_radarCalcCenter）— 补偿 #radarChart 顶部
     *      偏移（标题占用），让雷达图圆心精确对齐 .radar-card 卡片几何中心。
     *   3. 初始化时机：容器可能在异步渲染中宽高为 0，先 _ensureVisible 再 init；
     *      初始化后延迟重排（resize + 重算圆心）以应对 Grid stretch 延迟。
     *   4. 尺寸监听（021AC）：_bindRadarResize 以 ResizeObserver 监听容器
     *      （布局完成后回调，几何必为终态）+ window resize 兜底，触发
     *      _radarResizeRecenter（resize 画布 + 重算圆心）。
     */
    function _renderRadarChart(dims) {
        var chartDom = document.getElementById('radarChart');
        if (!chartDom) return;
        // 021AA：dispose 后必须置 null。renderFullReport 每次用 innerHTML 重建 DOM，
        // 旧实例绑定在已脱离的节点上不可复用；若不置 null，_applyOption 的
        // `if (!_radarChart)` 会跳过 echarts.init，对已 dispose 实例 setOption
        // 是静默 no-op（ECharts 5 行为）——刷新报告/切换股票后雷达图空白即此因。
        if (_radarChart) { _radarChart.dispose(); _radarChart = null; }
        if (typeof echarts === 'undefined') {
            chartDom.innerHTML = '<p style="text-align:center;color:var(--text-3,#999);padding:40px;">ECharts 未加载（请检查网络连接）</p>';
            return;
        }

        // 构造 ECharts option
        function _buildOption() {
            var dimList = [
                { key: 'kline',       label: '技术面', data: dims.kline || dims.technical },
                { key: 'fundamental', label: '基本面', data: dims.fundamental },
                { key: 'capital_flow',label: '资金面', data: dims.capital_flow || dims.capital },
                { key: 'news',        label: '消息面', data: dims.news || dims.sentiment }
            ];

            var indicator = [];
            var values = [];
            dimList.forEach(function(d) {
                var score = (d.data && d.data.score != null) ? d.data.score : 0;
                indicator.push({ name: d.label, max: 100 });
                values.push(score);
            });

            return {
                tooltip: { trigger: 'item', backgroundColor: _themeCol('--surface', '#fff'), borderColor: _themeCol('--border', '#ccc'), textStyle: { color: _themeCol('--text', '#333') } },
                radar: {
                    indicator: indicator,
                    shape: 'polygon',
                    // 水平 50%（居中）；垂直由 _radarCalcCenter 动态算，补偿标题占用的顶部空间
                    center: _radarCalcCenter(chartDom),
                    radius: '70%',
                    splitNumber: 4,
                    axisName: {
                        color: _themeCol('--text', '#444'),
                        fontSize: 12,
                        fontWeight: 600
                    },
                    nameGap: 6,
                    splitLine: { lineStyle: { color: _themeCol('--border', '#e0e0e0') } },
                    splitArea: { areaStyle: { color: [_themeCol('--surface-alt', '#fafafa'), _themeCol('--surface-2', '#f5f5f5'), _themeCol('--surface-alt', '#fafafa'), _themeCol('--surface-2', '#f0f0f0')] } },
                    axisLine: { lineStyle: { color: _themeCol('--border', '#ccc') } }
                },
                series: [{
                    type: 'radar',
                    data: [{
                        value: values,
                        name: '当前评分',
                        areaStyle: { color: 'rgba(26, 115, 232, 0.2)' },
                        lineStyle: { color: '#1a73e8', width: 2 },
                        itemStyle: { color: '#1a73e8' },
                        symbolSize: 5
                    }]
                }]
            };
        }

        // 应用/重渲染（每次渲染 DOM 均被 innerHTML 重建，实例不可跨渲染复用；
        // 021AA：isDisposed 兜底——实例一旦被销毁必须在当前新 DOM 上重新 init）
        function _applyOption() {
            if (!_radarChart || _radarChart.isDisposed()) {
                _radarChart = echarts.init(chartDom);
            }
            _radarChart.setOption(_buildOption(), { notMerge: false });
            try { _radarChart.resize(); } catch (e) {}
        }

        // 容器尺寸为 0（未渲染 / display:none）则下一帧重试，最多 10 次
        var _retry = 0;
        function _ensureVisible() {
            var w = chartDom.clientWidth;
            var h = chartDom.clientHeight;
            if (w > 0 && h > 0 || _retry >= 10) {
                _applyOption();
                // 再次重排：Grid stretch 完成后容器尺寸可能变化（021AB：resize 后重算圆心，
                // 避免 init 时按过渡态几何算出的 center 百分比过期导致漂移）
                setTimeout(_radarResizeRecenter, 50);
            } else {
                _retry++;
                requestAnimationFrame(_ensureVisible);
            }
        }
        _ensureVisible();
        // 021AC：挂载雷达图容器尺寸监听（ResizeObserver 为主 + window resize 兜底；
        // 每次渲染以当前 DOM 重新 observe，innerHTML 重建后旧观察自动失效）
        _bindRadarResize(chartDom);
    }

    /**
     * 渲染 ECharts K线图
     */
    function _renderKlineChart(klineData) {
        var chartDom = document.getElementById('klineChart');
        if (!chartDom) return;
        if (_klineChart) { _klineChart.dispose(); }
        if (typeof echarts === 'undefined') {
            chartDom.innerHTML = '<p style="text-align:center;color:var(--text-3,#999);padding:60px;">ECharts 未加载</p>';
            return;
        }
        if (!klineData.success || !klineData.data || klineData.data.length === 0) {
            chartDom.innerHTML = '<p style="text-align:center;color:var(--text-3,#999);padding:60px;">暂无K线数据，请先执行「采集数据」</p>';
            return;
        }
        _klineChart = echarts.init(chartDom);

        // K线数据按时间正序排列（API返回倒序，需翻转）
        var rows = klineData.data.slice().reverse();
        var dates = [];
        var ohlc = [];
        var volumes = [];

        rows.forEach(function(r) {
            dates.push(r.trade_date);
            ohlc.push([r.open, r.close, r.low, r.high]);
            volumes.push(r.volume || 0);
        });

        var option = {
            tooltip: {
                trigger: 'axis',
                axisPointer: { type: 'cross' },
                backgroundColor: _themeCol('--surface', '#fff'),
                borderColor: _themeCol('--border', '#ccc'),
                textStyle: { color: _themeCol('--text', '#333') }
            },
            legend: { data: ['K线', '成交量'], top: 0, textStyle: { color: _themeCol('--text-2', '#666') } },
            grid: [
                { left: '8%', right: '4%', top: '10%', height: '48%' },
                { left: '8%', right: '4%', top: '64%', height: '18%' }
            ],
            xAxis: [
                { type: 'category', data: dates, scale: true,
                  boundaryGap: false, axisLine: { onZero: false },
                  axisLabel: { color: _themeCol('--text-2', '#666') },
                  splitLine: { show: false }, min: 'dataMin', max: 'dataMax' },
                { type: 'category', gridIndex: 1, data: dates,
                  scale: true, boundaryGap: false,
                  axisLabel: { show: false } }
            ],
            yAxis: [
                { scale: true, splitArea: { show: true },
                  axisLabel: { color: _themeCol('--text-2', '#666') } },
                { gridIndex: 1, splitNumber: 2, axisLabel: { show: false },
                  axisLine: { show: false }, axisTick: { show: false } }
            ],
            dataZoom: [
                { type: 'inside', xAxisIndex: [0, 1], start: 0, end: 100 },
                { type: 'slider', xAxisIndex: [0, 1], start: 0, end: 100,
                  bottom: 12, height: 22,
                  borderColor: 'transparent',
                  backgroundColor: _themeCol('--surface-2', '#f5f5f5'),
                  fillerColor: 'rgba(26, 115, 232, 0.15)',
                  handleStyle: { color: '#1a73e8', borderColor: '#1a73e8' },
                  moveHandleStyle: { color: '#1a73e8' },
                  selectedDataBackground: { lineStyle: { color: '#1a73e8' }, areaStyle: { color: 'rgba(26,115,232,0.1)' } },
                  textStyle: { fontSize: 11 }
                }
            ],
            series: [
                {
                    name: 'K线',
                    type: 'candlestick',
                    data: ohlc,
                    itemStyle: {
                        color: '#e74c3c',        // 中国习惯：红涨
                        color0: '#27ae60',       // 绿跌
                        borderColor: '#e74c3c',
                        borderColor0: '#27ae60'
                    }
                },
                {
                    name: '成交量',
                    type: 'bar',
                    xAxisIndex: 1,
                    yAxisIndex: 1,
                    data: volumes,
                    itemStyle: {
                        color: function(params) {
                            var idx = params.dataIndex;
                            return ohlc[idx][1] >= ohlc[idx][0] ? '#e74c3c' : '#27ae60';
                        }
                    }
                }
            ]
        };
        _klineChart.setOption(option);

        // 窗口缩放时重绘K线图
        // 021AB：雷达图的 resize 处理已迁至 _bindRadarResize（随 _renderRadarChart 绑定）。
        // 原实现把雷达 resize 挂在这里——但 020R 移除报告页K线卡后本函数无人调用，
        // 监听从未绑定，雷达图窗口 resize 漂移即此因。勿再把雷达逻辑挂回本函数。
        if (!window._klineResizeBound) {
            window._klineResizeBound = true;
            window.addEventListener('resize', function() {
                if (_klineChart) _klineChart.resize();
            });
        }
    }

    /**
     * 评分颜色辅助函数
     */
    function _scoreColor(score) {
        if (score == null) return '#999';
        if (score >= 75) return '#27ae60';   // 绿色 - 优秀
        if (score >= 60) return '#1a73e8';   // 蓝色 - 良好
        if (score >= 40) return '#f39c12';   // 橙色 - 一般
        return '#e74c3c';                     // 红色 - 较差
    }

    /**
     * 019D: 分钟级生成时间格式化辅助函数
     * ISO(2026-08-03T14:23:45.678+08:00) → 2026-08-03 14:23
     * 空格分隔(2026-08-03 14:23:45) → 2026-08-03 14:23
     */
    function _fmtGenTime(s) {
        if (!s || typeof s !== 'string') return '—';
        return s.slice(0, 16).replace('T', ' ');
    }

    /**
     * 020R-57：数据完整度提示统一分类器（看板与报告页共用同一口径）
     * bad=异常(🔴) / warn=滞后(🟡) / good=正常(✅) / info=提示(无告警)
     */
    function _classifyDataWarning(w) {
        if (!w) return 'info';
        if (w.indexOf('⚠️') >= 0) return 'bad';
        var m = /滞后(\d+)天/.exec(w);
        if (m) {
            var n = parseInt(m[1], 10);
            if (n >= 5) return 'bad';
            if (n >= 1) return 'warn';
            return 'good';
        }
        if (w.indexOf('数据源暂不可用') >= 0) return 'warn';
        // 020R-57-HF1：「暂无」仅对数据维度缺失告警；「业绩预期暂无（预告/快报）」
        // 属正常状态（很多公司不发预告/快报），归为中性提示不告警
        if (w.indexOf('暂无') >= 0 && w.indexOf('业绩预期') < 0) return 'warn';
        if (w.indexOf('最新') >= 0) return 'good';
        return 'info';
    }

    /**
     * 020R-57：数据完整度汇总（看板三态标签用）——worst 取最差档，issues 收集异常/滞后行
     */
    function _dataWarningSummary(dwList, generatedAt) {
        var worst = 'good';
        var issues = [];
        (dwList || []).forEach(function(w) {
            var s = _classifyDataWarning(w);
            if (s === 'bad') {
                worst = 'bad';
                issues.push(w);
            } else if (s === 'warn') {
                issues.push(w);
                if (worst === 'good') worst = 'warn';
            }
        });
        return { worst: worst, issues: issues, generatedAt: generatedAt };
    }

    /**
     * 020R-57：三态数据标签 🔴异常 / 🟡滞后 / ✓正常（悬停列明细 + 检查时间）
     */
    function _dataWarningTag(summary) {
        var title = (summary.issues || []).map(function(w) { return w.replace(/"/g, '&quot;'); }).join('\n');
        if (summary.generatedAt) {
            title += (title ? '\n' : '') + '检查于 ' + _fmtGenTime(summary.generatedAt);
        }
        if (summary.worst === 'bad') {
            return '<span style="color:#e74c3c;font-weight:600;cursor:help;" title="' + title + '">🔴</span>';
        }
        if (summary.worst === 'warn') {
            return '<span style="color:#e65100;font-weight:600;cursor:help;" title="' + title + '">🟡</span>';
        }
        return '<span style="color:#27ae60;cursor:help;" title="数据完整，无滞后/替代源问题">✓</span>';
    }

    // ========== M8-BACKTEST-003 回测中心 ==========

    function switchBtTab(tab) {
        ['market', 'stock', 'exp'].forEach(function(t) {
            document.getElementById('btTabContent-' + t).style.display = (t === tab) ? '' : 'none';
            var btn = document.getElementById('btTab-' + t);
            if (btn) btn.classList.toggle('active', t === tab);
        });
        if (tab === 'market') loadBacktestMarketReport();
        if (tab === 'stock') initBtStockSelect();
        if (tab === 'exp') { loadOptimizerStatus(); loadWeightExperiments(); }
    }

    function loadBacktestMarketReport() {
        var market = document.getElementById('btMarketSelect') ? document.getElementById('btMarketSelect').value : 'a_stock';
        var el = document.getElementById('btMarketReportContent');
        if (!el) return;
        el.innerHTML = '<div class="report-empty"><p style="color:var(--text-3,#888);">加载中...</p></div>';
        fetch('/api/backtest/market-report?market=' + market)
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success) { el.innerHTML = '<p style="color:red;">加载失败</p>'; return; }
                var rpt = data.report;
                if (rpt.total === 0) {
                    el.innerHTML = '<div class="report-empty"><p>暂无回测数据</p><p style="color:var(--text-3,#888);font-size:13px;">请先执行批量分析生成评级记录，再点击「手动重跑回测」</p></div>';
                    return;
                }
                var warn = rpt.small_sample_warning ? '<span style="color:#e65100;font-size:12px;">⚠️ 小样本(N=' + rpt.total + ')，仅供参考</span>' : '';
                var html = '';
                html += '<div style="background:var(--surface,#fff);border-radius:10px;padding:20px;margin-bottom:16px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">';
                html += '<h3 style="margin:0 0 12px;">' + (market === 'a_stock' ? 'A股' : '港股') + ' 评级有效性报告</h3>';
                html += '<p style="font-size:13px;color:var(--text-3,#888);margin-bottom:16px;">' + rpt.sample_period_note + ' <span style="color:#27ae60;">✓ 全部真实样本（已排除模拟回测）</span> ' + warn + '</p>';
                // 020R-20/21：客观解读改为独立卡片逐条展示（评级有效性部分，含色调）
                window._btRatingParts = rpt.interpretation_parts || [];
                window._btRatingTones = rpt.interpretation_tones || [];
                _renderBtInterpretationCard();
                // U3(#10): 一句话总结
                var btSummaryParts = [];
                var btAccRound = Math.round((rpt.accuracy || 0) * 100);
                btSummaryParts.push('系统总体准确率 <strong style="color:' + (btAccRound >= 60 ? '#27ae60' : btAccRound >= 40 ? '#f39c12' : '#e74c3c') + ';">' + btAccRound + '%</strong>');
                if (rpt.period_accuracy && rpt.period_accuracy['1d'] && rpt.period_accuracy['1d'].accuracy !== null) {
                    btSummaryParts.push('T+1日准确率 <strong>' + Math.round(rpt.period_accuracy['1d'].accuracy * 100) + '%</strong>');
                }
                if (rpt.rating_stats) {
                    var bestRating = null, bestAcc = -1;
                    Object.keys(rpt.rating_stats).forEach(function(rating) {
                        var rs = rpt.rating_stats[rating];
                        if (rs.total >= 3 && rs.accuracy !== null && rs.accuracy > bestAcc) {
                            bestAcc = rs.accuracy; bestRating = rating;
                        }
                    });
                    if (bestRating) {
                        btSummaryParts.push('「<strong>' + bestRating + '</strong>」命中率最高（' + Math.round(bestAcc * 100) + '%）');
                    }
                }
                html += '<div style="background:linear-gradient(135deg,#e8f4fd,#f0f7ff);border-left:4px solid #1a73e8;border-radius:6px;padding:10px 16px;margin-bottom:16px;font-size:14px;color:var(--text,#333);">💡 ' + btSummaryParts.join('，') + '</div>';
                // 指标卡
                html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:20px;">';
                html += '<div style="text-align:center;padding:12px;background:var(--surface-alt,#f8f9fa);border-radius:8px;"><div style="font-size:24px;font-weight:700;color:#1a73e8;">' + rpt.total + '</div><div style="font-size:12px;color:var(--text-2,#666);">总回测数</div></div>';
                var accPct = Math.round((rpt.accuracy || 0) * 100);
                var accColor = accPct >= 60 ? '#27ae60' : accPct >= 40 ? '#f39c12' : '#e74c3c';
                html += '<div style="text-align:center;padding:12px;background:var(--surface-alt,#f8f9fa);border-radius:8px;"><div style="font-size:24px;font-weight:700;color:' + accColor + ';">' + accPct + '%</div><div style="font-size:12px;color:var(--text-2,#666);">总体准确率</div></div>';
                html += '<div style="text-align:center;padding:12px;background:var(--surface-alt,#f8f9fa);border-radius:8px;"><div style="font-size:24px;font-weight:700;color:#27ae60;">' + rpt.correct_count + '</div><div style="font-size:12px;color:var(--text-2,#666);">正确</div></div>';
                html += '<div style="text-align:center;padding:12px;background:var(--surface-alt,#f8f9fa);border-radius:8px;"><div style="font-size:24px;font-weight:700;color:#e74c3c;">' + rpt.wrong_count + '</div><div style="font-size:12px;color:var(--text-2,#666);">错误</div></div>';
                var dynPct = Math.round((rpt.dynamic_accuracy || 0) * 100);
                html += '<div style="text-align:center;padding:12px;background:var(--surface-alt,#f8f9fa);border-radius:8px;"><div style="font-size:24px;font-weight:700;color:#9c27b0;">' + dynPct + '%</div><div style="font-size:12px;color:var(--text-2,#666);">动态准确率(' + rpt.dynamic_count + ')</div></div>';
                html += '</div>';
                // 周期准确率
                if (rpt.period_accuracy) {
                    html += '<h4 style="margin:16px 0 8px;">周期准确率</h4>';
                    html += '<table class="bt-report-table" style="font-size:13px;"><thead><tr style="background:#f0f7ff;"><th style="padding:8px;text-align:left;">周期</th><th style="padding:8px;text-align:center;">判定数</th><th style="padding:8px;text-align:center;">正确</th><th style="padding:8px;text-align:center;" title="评级方向正确的比例（排除中性无法判定的记录）">准确率</th><th style="padding:8px;text-align:center;">平均收益</th></tr></thead><tbody>';
                    var periodLabels = {'1d': 'T+1日', '1w': 'T+1周', '1m': 'T+1月'};
                    ['1d', '1w', '1m'].forEach(function(p) {
                        var pa = rpt.period_accuracy[p];
                        if (!pa) return;
                        var paAcc = pa.accuracy !== null ? Math.round(pa.accuracy * 100) + '%' : '—';
                        var paAvg = pa.avg_return !== null ? (pa.avg_return > 0 ? '+' : '') + pa.avg_return + '%' : '—';
                        html += '<tr style="border-bottom:1px solid var(--border-light,#eee);"><td style="padding:8px;">' + periodLabels[p] + '</td><td style="padding:8px;text-align:center;">' + pa.total + '</td><td style="padding:8px;text-align:center;">' + pa.correct + '</td><td style="padding:8px;text-align:center;font-weight:700;">' + paAcc + '</td><td style="padding:8px;text-align:center;">' + paAvg + '</td></tr>';
                    });
                    html += '</tbody></table>';
                }
                // 020R-52：引擎分层统计（v5 新基线 vs 历史引擎）
                if (rpt.engine_stats && Object.keys(rpt.engine_stats).length) {
                    html += '<h4 style="margin:16px 0 8px;">引擎分层统计<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　（v5=当前规则引擎；021BB 起全部历史样本按自然键归因 v5）</span></h4>';
                    html += '<table class="bt-report-table" style="font-size:13px;"><thead><tr style="background:#f0f7ff;"><th style="padding:8px;text-align:left;">引擎</th><th style="padding:8px;text-align:center;">样本数</th><th style="padding:8px;text-align:center;">准确率</th><th style="padding:8px;text-align:center;">动态准确率</th><th style="padding:8px;text-align:center;">T+1月均收益</th></tr></thead><tbody>';
                    var evOrder = ['v5', '未标记(历史)'];
                    Object.keys(rpt.engine_stats).sort(function(a, b) { return evOrder.indexOf(a) - evOrder.indexOf(b); }).forEach(function(ev) {
                        var es = rpt.engine_stats[ev];
                        var evLabel = ev === 'v5' ? 'v5（当前规则）' : '历史引擎（未标记）';
                        var esAcc = es.accuracy !== null && es.accuracy !== undefined ? Math.round(es.accuracy * 100) + '%' : '—';
                        var esDyn = es.dyn_accuracy !== null && es.dyn_accuracy !== undefined ? Math.round(es.dyn_accuracy * 100) + '%' : '—';
                        var esAvg = es.avg_return_1m !== null && es.avg_return_1m !== undefined ? (es.avg_return_1m > 0 ? '+' : '') + es.avg_return_1m + '%' : '—';
                        html += '<tr style="border-bottom:1px solid var(--border-light,#eee);"><td style="padding:8px;">' + evLabel + '</td><td style="padding:8px;text-align:center;">' + es.total + '</td><td style="padding:8px;text-align:center;font-weight:700;">' + esAcc + '</td><td style="padding:8px;text-align:center;">' + esDyn + '</td><td style="padding:8px;text-align:center;">' + esAvg + '</td></tr>';
                    });
                    html += '</tbody></table>';
                }
                // 分级准确率
                if (rpt.rating_stats) {
                    html += '<h4 style="margin:16px 0 8px;">分级准确率</h4>';
                    html += '<table class="bt-report-table" style="font-size:13px;"><thead><tr style="background:#f0f7ff;"><th style="padding:8px;text-align:left;">评级</th><th style="padding:8px;text-align:center;">总数</th><th style="padding:8px;text-align:center;">正确</th><th style="padding:8px;text-align:center;">错误</th><th style="padding:8px;text-align:center;" title="评级方向正确的比例（排除中性无法判定的记录）">准确率</th><th style="padding:8px;text-align:center;" title="评级有效期内的方向命中率：从评级日持有至下次评级变更的判定">动态准确率</th><th style="padding:8px;text-align:center;" title="评级发出后第1个交易日的股价涨跌幅">T+1均收益</th><th style="padding:8px;text-align:center;" title="评级发出后第5个交易日的股价涨跌幅">T+1周均收益</th><th style="padding:8px;text-align:center;" title="评级发出后第20个交易日的股价涨跌幅">T+1月均收益</th></tr></thead><tbody>';
                    var ratingOrder = ['强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出'];
                    Object.keys(rpt.rating_stats).sort(function(a, b) { return ratingOrder.indexOf(a) - ratingOrder.indexOf(b); }).forEach(function(rating) {
                        var rs = rpt.rating_stats[rating];
                        var rsAcc = rs.accuracy !== null ? Math.round(rs.accuracy * 100) + '%' : '—';
                        var rsDynAcc = rs.dyn_accuracy !== null ? Math.round(rs.dyn_accuracy * 100) + '%' : '—';
                        var rsAvg1d = rs.avg_return_1d !== null ? (rs.avg_return_1d > 0 ? '+' : '') + rs.avg_return_1d + '%' : '—';
                        var rsAvg1w = rs.avg_return_1w !== null ? (rs.avg_return_1w > 0 ? '+' : '') + rs.avg_return_1w + '%' : '—';
                        var rsAvg1m = rs.avg_return_1m !== null ? (rs.avg_return_1m > 0 ? '+' : '') + rs.avg_return_1m + '%' : '—';
                        // B17-T3: T+1日/周收益红涨绿跌（正数红 #e74c3c，负数绿 #27ae60）
                        var ret1dColor = rs.avg_return_1d !== null ? (rs.avg_return_1d > 0 ? '#e74c3c' : rs.avg_return_1d < 0 ? '#27ae60' : '#666') : '#999';
                        var ret1wColor = rs.avg_return_1w !== null ? (rs.avg_return_1w > 0 ? '#e74c3c' : rs.avg_return_1w < 0 ? '#27ae60' : '#666') : '#999';
                        var ret1mColor = rs.avg_return_1m !== null ? (rs.avg_return_1m > 0 ? '#e74c3c' : rs.avg_return_1m < 0 ? '#27ae60' : '#666') : '#999';
                        // 动态准确率着色：≥60% 绿 / 45-60% 橙 / <45% 红
                        var dynColor = rs.dyn_accuracy !== null ? (rs.dyn_accuracy >= 0.6 ? '#27ae60' : rs.dyn_accuracy >= 0.45 ? '#f39c12' : '#e74c3c') : '#999';
                        html += '<tr style="border-bottom:1px solid var(--border-light,#eee);"><td style="padding:8px;"><span class="rating-badge ' + getRatingClass(rating) + '" title="' + getRatingTitle(rating) + '">' + rating + '</span></td><td style="padding:8px;text-align:center;">' + rs.total + '</td><td style="padding:8px;text-align:center;">' + rs.correct + '</td><td style="padding:8px;text-align:center;">' + rs.wrong + '</td><td style="padding:8px;text-align:center;font-weight:700;">' + rsAcc + '</td><td style="padding:8px;text-align:center;font-weight:700;color:' + dynColor + ';">' + rsDynAcc + '</td><td style="padding:8px;text-align:center;color:' + ret1dColor + ';font-weight:600;">' + rsAvg1d + '</td><td style="padding:8px;text-align:center;color:' + ret1wColor + ';font-weight:600;">' + rsAvg1w + '</td><td style="padding:8px;text-align:center;color:' + ret1mColor + ';font-weight:600;">' + rsAvg1m + '</td></tr>';
                    });
                    html += '</tbody></table>';
                }
                html += '</div>';
                el.innerHTML = html;
                loadPriceBacktestReport();
            })
            .catch(function(e) { el.innerHTML = '<p style="color:red;">加载失败: ' + e + '</p>'; });
    }

    function loadPriceBacktestReport() {
        var market = document.getElementById('btMarketSelect') ? document.getElementById('btMarketSelect').value : 'a_stock';
        var el = document.getElementById('btPriceBacktestContent');
        if (!el) return;
        el.innerHTML = '<div class="report-empty"><p style="color:var(--text-3,#888);">价格建议命中率加载中...</p></div>';
        fetch('/api/price-backtest/report?market=' + market)
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success || !data.report || data.report.total_points === 0) {
                    window._btPriceParts = [];
                    window._btPriceTones = [];
                    _renderBtInterpretationCard();
                    el.innerHTML = '<div style="background:#fff3cd;border:1px solid #ffeaa7;border-radius:6px;padding:12px;margin-top:16px;"><p style="font-size:13px;color:#856404;">暂无价格建议回测数据。<button class="btn btn-primary btn-sm" style="margin-left:8px;" onclick="runPriceBacktest()">▶ 运行价格建议回测</button></p></div>';
                    return;
                }
                var rpt = data.report;
                // 020R-52：主口径=真实评级回测点（无未来函数）；无真实样本时退回全样本并显著警示
                var useReal = !!(rpt.real_hit_rates && rpt.real_sample && rpt.real_sample.total > 0);
                var hitR = useReal ? rpt.real_hit_rates : rpt.hit_rates;
                var daysR = useReal ? (rpt.real_avg_days || rpt.avg_days) : rpt.avg_days;
                var rrR = useReal ? rpt.real_risk_reward : rpt.risk_reward_ratio;
                var html = '<div style="background:var(--surface-alt,#f8f9fa);border:1px solid var(--border,#e0e0e0);border-radius:8px;padding:16px;margin-top:16px;">';
                html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">';
                html += '<h4 style="margin:0;">价格建议命中率 <span style="font-size:12px;color:var(--text-3,#888);font-weight:normal;">（回测点: ' + rpt.total_points + ' | 无持仓: ' + rpt.no_position_count + ' | 有持仓: ' + rpt.has_position_count + '）</span></h4>';
                html += '<button class="btn btn-primary btn-sm" onclick="runPriceBacktest()">▶ 重新运行回测</button>';
                html += '</div>';
                // 020R-52：数据质量说明置顶
                if (useReal) {
                    html += '<div style="background:#e8f4fd;border:1px solid #b8dcf7;border-radius:6px;padding:8px 12px;margin-bottom:12px;font-size:12px;color:#1565c0;">✅ 以下指标为<b>真实评级回测点</b>口径（无未来函数，N=' + rpt.real_sample.total + '）；历史重建点（' + (rpt.total_points - rpt.real_sample.total) + ' 个，含未来函数偏差）仅作参照，不参与结论。</div>';
                } else {
                    html += '<div style="background:#fff3cd;border:1px solid #ffeaa7;border-radius:6px;padding:8px 12px;margin-bottom:12px;font-size:12px;color:#856404;">⚠️ 当前无真实评级回测点，以下指标来自历史重建点（未来函数偏差），可信度低，仅供参考。</div>';
                }
                // 核心指标卡片
                var t5 = hitR.t5;
                var t20 = hitR.t20;
                var rr = rrR !== null && rrR !== undefined ? rrR.toFixed(2) : '—';
                function pct(v) { return v !== null && v !== undefined ? Math.round(v * 100) + '%' : '—'; }
                function pctColor(v) {
                    if (v === null || v === undefined) return '#999';
                    return v >= 0.5 ? '#27ae60' : v >= 0.3 ? '#e67e22' : '#e74c3c';
                }
                html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin-bottom:16px;">';
                html += '<div style="background:var(--surface,#fff);border-radius:8px;padding:12px;text-align:center;border:1px solid var(--border-light,#eee);"><div style="font-size:11px;color:var(--text-3,#888);margin-bottom:4px;">买入区间命中率(T+20)</div><div style="font-size:24px;font-weight:700;color:' + pctColor(t20.buy_range) + '">' + pct(t20.buy_range) + '</div></div>';
                html += '<div style="background:var(--surface,#fff);border-radius:8px;padding:12px;text-align:center;border:1px solid var(--border-light,#eee);"><div style="font-size:11px;color:var(--text-3,#888);margin-bottom:4px;">目标价命中率(T+20)</div><div style="font-size:24px;font-weight:700;color:' + pctColor(t20.target) + '">' + pct(t20.target) + '</div></div>';
                html += '<div style="background:var(--surface,#fff);border-radius:8px;padding:12px;text-align:center;border:1px solid var(--border-light,#eee);"><div style="font-size:11px;color:var(--text-3,#888);margin-bottom:4px;">止损价命中率(T+20)</div><div style="font-size:24px;font-weight:700;color:' + pctColor(1 - (t20.stop_loss || 0)) + '">' + pct(t20.stop_loss) + '</div><div style="font-size:10px;color:var(--text-3,#999);">越低越好</div></div>';
                html += '<div style="background:var(--surface,#fff);border-radius:8px;padding:12px;text-align:center;border:1px solid var(--border-light,#eee);"><div style="font-size:11px;color:var(--text-3,#888);margin-bottom:4px;">风险收益比</div><div style="font-size:24px;font-weight:700;color:' + (rr !== '—' && parseFloat(rr) >= 1 ? '#27ae60' : rr !== '—' && parseFloat(rr) >= 0.5 ? '#e67e22' : '#999') + '">' + rr + '</div><div style="font-size:10px;color:var(--text-3,#999);">目标命中/止损命中</div></div>';
                html += '</div>';
                // T+5 vs T+20 对比表（020R-52：随主口径切换）
                html += '<h4 style="margin:16px 0 8px;">T+5 vs T+20 命中率对比<span style="font-size:12px;color:var(--text-3,#999);font-weight:normal;">　' + (useReal ? '（真实样本口径）' : '（全样本口径）') + '</span></h4>';
                html += '<table class="bt-report-table" style="font-size:13px;"><thead><tr style="background:#f0f7ff;"><th style="padding:8px;text-align:left;">建议项</th><th style="padding:8px;text-align:center;">T+5 命中率</th><th style="padding:8px;text-align:center;">T+20 命中率</th><th style="padding:8px;text-align:center;">T+5 平均天数</th><th style="padding:8px;text-align:center;">T+20 平均天数</th></tr></thead><tbody>';
                var items = [
                    {name: '买入区间', t5h: t5.buy_range, t20h: t20.buy_range, t5d: daysR.t5.buy_range, t20d: daysR.t20.buy_range},
                    {name: '目标价', t5h: t5.target, t20h: t20.target, t5d: daysR.t5.target, t20d: daysR.t20.target},
                    {name: '止损价', t5h: t5.stop_loss, t20h: t20.stop_loss, t5d: daysR.t5.stop_loss, t20d: daysR.t20.stop_loss}
                ];
                if (rpt.has_position_count > 0) {
                    items.push({name: '止盈价(持仓)', t5h: t5.take_profit, t20h: t20.take_profit, t5d: daysR.t5.take_profit, t20d: daysR.t20.take_profit});
                }
                items.forEach(function(it) {
                    function fmtDays(d) { return d !== null && d !== undefined ? d + '天' : '—'; }
                    html += '<tr style="border-bottom:1px solid var(--border-light,#eee);"><td style="padding:8px;">' + it.name + '</td><td style="padding:8px;text-align:center;font-weight:600;color:' + pctColor(it.t5h) + '">' + pct(it.t5h) + '</td><td style="padding:8px;text-align:center;font-weight:600;color:' + pctColor(it.t20h) + '">' + pct(it.t20h) + '</td><td style="padding:8px;text-align:center;">' + fmtDays(it.t5d) + '</td><td style="padding:8px;text-align:center;">' + fmtDays(it.t20d) + '</td></tr>';
                });
                html += '</tbody></table>';
                // 020R-52：全样本参照行（含未来函数偏差的重建点，仅陈列）
                if (useReal && rpt.hit_rates && rpt.hit_rates.t20) {
                    var allT20 = rpt.hit_rates.t20;
                    html += '<p style="font-size:12px;color:var(--text-3,#999);margin:8px 0 4px;">全样本参照（含 ' + (rpt.total_points - rpt.real_sample.total) + ' 个历史重建点，未来函数偏差，不参与结论）：买入区间 T+20 ' + pct(allT20.buy_range) + '、目标价 ' + pct(allT20.target) + '、止损 ' + pct(allT20.stop_loss) + '。</p>';
                }
                // 分评级统计（无持仓/有持仓拆分，各组分母一致）
                if (rpt.rating_stats) {
                    var ratingOrder = ['强烈推荐买入', '推荐买入', '持有观望', '建议减仓', '强烈建议卖出'];
                    var ratingKeys = Object.keys(rpt.rating_stats).sort(function(a, b) { return ratingOrder.indexOf(a) - ratingOrder.indexOf(b); });
                    html += '<p style="font-size:12px;color:var(--text-3,#888);margin:12px 0 4px;">已按持仓状态拆分统计，各表内分母一致可横向比较。' + (useReal ? '（分评级表为真实评级锚点口径，021AU 起评级/持仓均无未来函数）' : '') + '</p>';
                    // 表1：无持仓样本（买入区间/目标价/止损价）
                    html += '<h4 style="margin:16px 0 8px;">分评级命中率 — 无持仓样本 (T+20)</h4>';
                    html += '<table class="bt-report-table" style="font-size:13px;"><thead><tr style="background:#f0f7ff;"><th style="padding:8px;text-align:left;">评级</th><th style="padding:8px;text-align:center;">样本数</th><th style="padding:8px;text-align:center;">买入区间</th><th style="padding:8px;text-align:center;">目标价</th><th style="padding:8px;text-align:center;">止损价</th></tr></thead><tbody>';
                    ratingKeys.forEach(function(rating) {
                        var rs = rpt.rating_stats[rating];
                        html += '<tr style="border-bottom:1px solid var(--border-light,#eee);"><td style="padding:8px;"><span class="rating-badge ' + getRatingClass(rating) + '" title="' + getRatingTitle(rating) + '">' + rating + '</span></td><td style="padding:8px;text-align:center;">' + (rs.np_total || 0) + '</td><td style="padding:8px;text-align:center;font-weight:600;color:' + pctColor(rs.np_t20_buy_range) + '">' + pct(rs.np_t20_buy_range) + '</td><td style="padding:8px;text-align:center;font-weight:600;color:' + pctColor(rs.np_t20_target) + '">' + pct(rs.np_t20_target) + '</td><td style="padding:8px;text-align:center;font-weight:600;color:' + pctColor(rs.np_t20_stop_loss) + '">' + pct(rs.np_t20_stop_loss) + '</td></tr>';
                    });
                    html += '</tbody></table>';
                    // 表2：有持仓样本（补仓区间/持有区间/止盈价/止损价）
                    // 021AU：评级=锚点当日系统真实评级，持仓状态由交易流水倒推至当日
                    html += '<h4 style="margin:16px 0 8px;">分评级命中率 — 有持仓样本 (T+20)</h4>';
                    html += '<p style="font-size:12px;color:var(--text-3,#888);margin:0 0 6px;">口径：评级取锚点当日系统真实给出的评级，持仓状态由交易流水倒推至当日（均无未来函数）——各评级档在"当时持有该股"时的建议表现。</p>';
                    html += '<table class="bt-report-table" style="font-size:13px;"><thead><tr style="background:#f0f7ff;"><th style="padding:8px;text-align:left;">评级</th><th style="padding:8px;text-align:center;">样本数</th><th style="padding:8px;text-align:center;" title="股价回落到网格补仓位的概率（出现过加仓机会）">补仓区间</th><th style="padding:8px;text-align:center;" title="股价保持在止盈价与止损价之间、未触发任何边界（仅持有观望有持有语义）">持有区间</th><th style="padding:8px;text-align:center;">止盈价</th><th style="padding:8px;text-align:center;">止损价</th></tr></thead><tbody>';
                    ratingKeys.forEach(function(rating) {
                        var rs = rpt.rating_stats[rating];
                        var holdVal = (rating === '持有观望') ? pct(rs.hp_t20_hold) : '—';
                        html += '<tr style="border-bottom:1px solid var(--border-light,#eee);"><td style="padding:8px;"><span class="rating-badge ' + getRatingClass(rating) + '" title="' + getRatingTitle(rating) + '">' + rating + '</span></td><td style="padding:8px;text-align:center;">' + (rs.hp_total || 0) + '</td><td style="padding:8px;text-align:center;font-weight:600;color:' + pctColor(rs.hp_t20_add) + '">' + pct(rs.hp_t20_add) + '</td><td style="padding:8px;text-align:center;font-weight:600;color:' + pctColor(rs.hp_t20_hold) + '">' + holdVal + '</td><td style="padding:8px;text-align:center;font-weight:600;color:' + pctColor(rs.hp_t20_take_profit) + '">' + pct(rs.hp_t20_take_profit) + '</td><td style="padding:8px;text-align:center;font-weight:600;color:' + pctColor(rs.hp_t20_stop_loss) + '">' + pct(rs.hp_t20_stop_loss) + '</td></tr>';
                    });
                    html += '</tbody></table>';
                }
                html += '</div>';
                el.innerHTML = html;
                // 020R-20/21：客观解读卡（价格建议命中率部分，含色调）
                window._btPriceParts = rpt.interpretation_parts || [];
                window._btPriceTones = rpt.interpretation_tones || [];
                _renderBtInterpretationCard();
            })
            .catch(function(e) { el.innerHTML = '<p style="color:red;">价格建议回测加载失败: ' + e + '</p>'; });
    }

    // 020R-20/21：客观解读独立卡片——评级有效性 + 价格建议命中率，逐条列出；
    // 好的 ✓ 绿色提示、不好的 ⚠️ 橙色预警、中性信息普通圆点
    function _renderBtInterpretationCard() {
        var el = document.getElementById('btInterpretationContent');
        if (!el) return;
        var ratingParts = window._btRatingParts || [];
        var ratingTones = window._btRatingTones || [];
        var priceParts = window._btPriceParts || [];
        var priceTones = window._btPriceTones || [];
        function interpLi(text, tone) {
            if (tone === 'good') return '<li class="bt-interp-good"><span class="bt-interp-icon">✓</span>' + text + '</li>';
            if (tone === 'bad') return '<li class="bt-interp-bad"><span class="bt-interp-icon">⚠️</span>' + text + '</li>';
            return '<li>' + text + '</li>';
        }
        var html = '';
        html += '<div class="bt-interp-card">';
        html += '<div class="bt-interp-title">📋 客观解读</div>';
        if (ratingParts.length) {
            html += '<div class="bt-interp-group">评级有效性</div>';
            html += '<ul class="bt-interp-list">';
            ratingParts.forEach(function(p, i) { html += interpLi(p, ratingTones[i]); });
            html += '</ul>';
        }
        if (priceParts.length) {
            html += '<div class="bt-interp-group">价格建议命中率</div>';
            html += '<ul class="bt-interp-list">';
            priceParts.forEach(function(p, i) { html += interpLi(p, priceTones[i]); });
            html += '</ul>';
        }
        html += '</div>';
        el.innerHTML = html;
    }

    function runPriceBacktest() {
        var market = document.getElementById('btMarketSelect') ? document.getElementById('btMarketSelect').value : 'a_stock';
        var el = document.getElementById('btPriceBacktestContent');
        if (el) el.innerHTML = '<div class="report-empty"><p style="color:var(--text-3,#888);">价格建议回测运行中...</p></div>';
        fetch('/api/price-backtest/run', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({market: market, force: true})
        })
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    loadPriceBacktestReport();
                } else {
                    if (el) el.innerHTML = '<p style="color:red;">运行失败: ' + (data.error || '') + '</p>';
                }
            })
            .catch(function(e) { if (el) el.innerHTML = '<p style="color:red;">错误: ' + e + '</p>'; });
    }

    function rerunBacktest() {
        var statusEl = document.getElementById('btRerunStatus');
        if (statusEl) statusEl.textContent = '正在重跑...';
        var market = document.getElementById('btMarketSelect') ? document.getElementById('btMarketSelect').value : null;
        fetch('/api/backtest/rerun', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({market: market, force: true})
        })
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success) {
                    var res = data.result;
                    if (statusEl) statusEl.textContent = '完成: ' + res.success + '/' + res.total + ' 成功, ' + res.errors + ' 错误';
                    loadBacktestMarketReport();
                } else {
                    if (statusEl) statusEl.textContent = '失败: ' + (data.error || '');
                }
            })
            .catch(function(e) { if (statusEl) statusEl.textContent = '错误: ' + e; });
    }

    function initBtStockSelect() {
        var sel = document.getElementById('btStockSelect');
        if (!sel || sel.options.length > 1) return;
        fetch('/api/stocks')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (data.success && data.stocks) {
                    data.stocks.forEach(function(s) {
                        var opt = document.createElement('option');
                        opt.value = s.id;
                        opt.textContent = s.symbol + ' ' + s.name + ' (' + (s.market === 'a_stock' ? 'A股' : '港股') + ')';
                        sel.appendChild(opt);
                    });
                }
            });
    }

    function loadBacktestStockDetail() {
        var stockId = document.getElementById('btStockSelect').value;
        var el = document.getElementById('btStockDetailContent');
        if (!stockId) { el.innerHTML = '<div class="report-empty"><p style="color:var(--text-3,#888);">请选择股票</p></div>'; return; }
        el.innerHTML = '<div class="report-empty"><p style="color:var(--text-3,#888);">加载中...</p></div>';
        fetch('/api/backtest/stock/' + stockId)
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success) { el.innerHTML = '<p style="color:var(--text-3,#888);">' + (data.message || '无数据') + '</p>'; return; }
                var warn = data.small_sample_warning ? '<span style="color:#e65100;font-size:12px;">⚠️ 小样本</span>' : '';
                var html = '';
                html += '<div style="background:var(--surface,#fff);border-radius:10px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">';
                html += '<h3 style="margin:0 0 8px;">' + data.symbol + ' ' + data.name + '</h3>';
                html += '<p style="font-size:13px;color:var(--text-3,#888);margin-bottom:16px;">回测记录: ' + data.total + '条 ' + warn + '</p>';
                // 指标
                var accPct = Math.round((data.accuracy || 0) * 100);
                var dynPct = Math.round((data.dynamic_accuracy || 0) * 100);
                html += '<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:12px;margin-bottom:20px;">';
                html += '<div style="text-align:center;padding:12px;background:var(--surface-alt,#f8f9fa);border-radius:8px;"><div style="font-size:20px;font-weight:700;color:#1a73e8;">' + data.total + '</div><div style="font-size:12px;color:var(--text-2,#666);">总记录</div></div>';
                html += '<div style="text-align:center;padding:12px;background:var(--surface-alt,#f8f9fa);border-radius:8px;"><div style="font-size:20px;font-weight:700;color:#27ae60;">' + accPct + '%</div><div style="font-size:12px;color:var(--text-2,#666);">准确率</div></div>';
                html += '<div style="text-align:center;padding:12px;background:var(--surface-alt,#f8f9fa);border-radius:8px;"><div style="font-size:20px;font-weight:700;color:#9c27b0;">' + dynPct + '%</div><div style="font-size:12px;color:var(--text-2,#666);">动态准确率</div></div>';
                if (data.avg_return_1d !== null) html += '<div style="text-align:center;padding:12px;background:var(--surface-alt,#f8f9fa);border-radius:8px;"><div style="font-size:20px;font-weight:700;color:' + (data.avg_return_1d >= 0 ? '#27ae60' : '#e74c3c') + ';">' + (data.avg_return_1d > 0 ? '+' : '') + data.avg_return_1d + '%</div><div style="font-size:12px;color:var(--text-2,#666);">T+1均收益</div></div>';
                html += '</div>';
                // 明细表
                if (data.records && data.records.length > 0) {
                    html += '<table style="width:100%;border-collapse:collapse;font-size:12px;"><thead><tr style="background:#f0f7ff;">';
                    html += '<th style="padding:6px;text-align:left;">评级日</th><th style="padding:6px;">评级</th><th style="padding:6px;">基准价</th><th style="padding:6px;" title="评级发出后第1个交易日的股价涨跌幅">T+1收益</th><th style="padding:6px;" title="评级发出后第5个交易日的股价涨跌幅">T+1周收益</th><th style="padding:6px;" title="评级发出后第20个交易日的股价涨跌幅">T+1月收益</th><th style="padding:6px;">动态收益</th><th style="padding:6px;">判定</th>';
                    html += '</tr></thead><tbody>';
                    data.records.forEach(function(r) {
                        var verdict = r.is_correct === 1 ? '<span style="color:#27ae60;">✓</span>' : r.is_correct === 0 ? '<span style="color:#e74c3c;">✗</span>' : '<span style="color:var(--text-3,#888);">—</span>';
                        var fmtRet = function(v) { return v !== null && v !== undefined ? (v > 0 ? '+' : '') + v + '%' : '—'; };
                        html += '<tr style="border-bottom:1px solid var(--border-light,#eee);">';
                        html += '<td style="padding:6px;">' + (r.rating_date || '') + '</td>';
                        html += '<td style="padding:6px;"><span class="rating-badge ' + getRatingClass(r.rating) + '" title="' + getRatingTitle(r.rating) + '">' + (r.rating || '—') + '</span></td>';
                        html += '<td style="padding:6px;text-align:center;">' + (r.price_at_rating || '—') + '</td>';
                        html += '<td style="padding:6px;text-align:center;">' + fmtRet(r.return_1d) + '</td>';
                        html += '<td style="padding:6px;text-align:center;">' + fmtRet(r.return_1w) + '</td>';
                        html += '<td style="padding:6px;text-align:center;">' + fmtRet(r.return_1m) + '</td>';
                        html += '<td style="padding:6px;text-align:center;">' + fmtRet(r.dynamic_return) + '</td>';
                        html += '<td style="padding:6px;text-align:center;">' + verdict + '</td>';
                        html += '</tr>';
                    });
                    html += '</tbody></table>';
                }
                html += '</div>';
                el.innerHTML = html;
            })
            .catch(function(e) { el.innerHTML = '<p style="color:red;">加载失败: ' + e + '</p>'; });
    }

    // ========== M9 自动优化 ==========

    function loadOptimizerStatus() {
        var el = document.getElementById('btOptimizerContent');
        if (!el) return;
        el.innerHTML = '<div class="report-empty"><p style="color:var(--text-3,#888);">加载优化状态...</p></div>';
        fetch('/api/optimizer/status?market=a_stock')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success) { el.innerHTML = '<p style="color:red;">加载失败</p>'; return; }
                var p = data.params || {};
                var w = p.weights || {};
                var hist = data.history || [];
                var html = '<div style="background:var(--surface,#fff);border-radius:10px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">';
                html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:12px;">';
                html += '<h3 style="margin:0;">🧠 M9 自动优化引擎</h3>';
                html += '<button class="btn btn-primary btn-sm" onclick="runOptimizer()">手动执行优化</button>';
                html += '</div>';
                // 当前权重
                html += '<div style="margin-bottom:16px;"><strong>当前权重（A股）：</strong>';
                html += '<span style="margin-left:12px;font-size:13px;">';
                html += '技术面 ' + Math.round((w.kline||0)*100) + '%';
                html += ' · 基本面 ' + Math.round((w.fundamental||0)*100) + '%';
                html += ' · 资金面 ' + Math.round((w.capital_flow||0)*100) + '%';
                html += ' · 消息面 ' + Math.round((w.news||0)*100) + '%';
                html += '</span></div>';
                // 优化历史
                if (hist.length > 0) {
                    html += '<div style="font-size:13px;"><strong>最近优化记录：</strong></div>';
                    html += '<div style="max-height:180px;overflow-y:auto;margin-top:8px;">';
                    hist.slice(0, 5).forEach(function(h) {
                        var adj = h.adjusted !== undefined ? (h.adjusted ? '✅ 已调整' : '➖ 未调整') : '';
                        html += '<div style="padding:8px 12px;background:var(--surface-alt,#f8f9fa);border-radius:6px;margin-bottom:6px;font-size:12px;">';
                        html += '<span style="color:var(--text-3,#888);">' + (h.timestamp || h.updated_at || '') + '</span> ';
                        html += '<span>' + adj + '</span> ';
                        html += '<span style="color:var(--text,#555);">' + (h.reason || '') + '</span>';
                        if (h.accuracy_before !== undefined) {
                            html += ' <span style="color:#27ae60;">准确率 ' + Math.round(h.accuracy_before*100) + '%→' + Math.round(h.accuracy_after*100) + '%</span>';
                        }
                        html += '</div>';
                    });
                    html += '</div>';
                } else {
                    html += '<p style="font-size:13px;color:var(--text-3,#888);margin:8px 0 0;">尚未执行过优化（每周日 20:00 自动执行，或点击上方按钮手动触发）</p>';
                }
                html += '<div id="optimizerRunResult"></div>';
                html += '</div>';
                el.innerHTML = html;
            })
            .catch(function(e) { el.innerHTML = '<p style="color:red;">加载失败: ' + e + '</p>'; });
    }

    function runOptimizer() {
        var el = document.getElementById('optimizerRunResult');
        if (el) el.innerHTML = '<p style="color:var(--text-3,#888);font-size:13px;margin-top:12px;">正在执行优化...</p>';
        fetch('/api/optimizer/run', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({market: 'a_stock'})})
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!el) return;
                if (!data.success) { el.innerHTML = '<p style="color:red;font-size:13px;margin-top:12px;">' + (data.error || '失败') + '</p>'; return; }
                var res = data.result || {};
                var html = '<div style="margin-top:12px;padding:12px;background:#f0f7ff;border-radius:6px;font-size:13px;">';
                if (res.adjusted) {
                    html += '<strong style="color:#27ae60;">✅ 优化完成</strong>';
                } else {
                    html += '<strong style="color:var(--text-3,#888);">➖ 未调整</strong>';
                }
                html += '<p style="margin:6px 0 0;">' + (res.reason || '') + '</p>';
                if (res.accuracy_before !== undefined) {
                    html += '<p style="margin:4px 0 0;color:var(--text,#555);">准确率: ' + Math.round(res.accuracy_before*100) + '% → ' + Math.round(res.accuracy_after*100) + '%</p>';
                }
                if (res.sample_count) html += '<p style="margin:4px 0 0;color:var(--text-3,#888);font-size:12px;">样本量: ' + res.sample_count + '</p>';
                html += '</div>';
                el.innerHTML = html;
                // 刷新状态
                setTimeout(loadOptimizerStatus, 1000);
            })
            .catch(function(e) { if (el) el.innerHTML = '<p style="color:red;font-size:13px;margin-top:12px;">错误: ' + e + '</p>'; });
    }

    function loadWeightExperiments() {
        var el = document.getElementById('btExperimentsContent');
        if (!el) return;
        el.innerHTML = '<div class="report-empty"><p style="color:var(--text-3,#888);">加载中...</p></div>';
        fetch('/api/backtest/weight-experiments')
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!data.success || !data.experiments) { el.innerHTML = '<p>加载失败</p>'; return; }
                var html = '<div style="background:var(--surface,#fff);border-radius:10px;padding:20px;box-shadow:0 1px 3px rgba(0,0,0,0.1);">';
                html += '<h3 style="margin:0 0 8px;">权重实验场景</h3>';
                html += '<p style="font-size:13px;color:var(--text-3,#888);margin-bottom:16px;">D4裁定预留：仅模拟计算，不修改生产权重。实际权重变更由M9自动优化决策。</p>';
                data.experiments.forEach(function(exp) {
                    html += '<div style="border:1px solid var(--border,#e0e0e0);border-radius:8px;padding:16px;margin-bottom:12px;">';
                    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">';
                    html += '<div><strong>' + exp.name + '</strong><span style="margin-left:8px;font-size:12px;color:var(--text-3,#888);">(' + (exp.market === 'a_stock' ? 'A股' : '港股') + ')</span></div>';
                    html += '<button class="btn btn-primary btn-sm" onclick="runWeightExperiment(\'' + exp.id + '\')">运行实验</button>';
                    html += '</div>';
                    html += '<p style="font-size:13px;color:var(--text-2,#666);margin:0;">' + exp.description + '</p>';
                    html += '<div id="expResult-' + exp.id + '"></div>';
                    html += '</div>';
                });
                html += '</div>';
                el.innerHTML = html;
            })
            .catch(function(e) { el.innerHTML = '<p style="color:red;">加载失败: ' + e + '</p>'; });
    }

    function runWeightExperiment(expId) {
        var el = document.getElementById('expResult-' + expId);
        if (el) el.innerHTML = '<p style="color:var(--text-3,#888);font-size:13px;margin-top:8px;">正在运行...</p>';
        fetch('/api/backtest/weight-experiments/' + expId + '/run', {method: 'POST'})
            .then(function(r) { return safeJson(r); })
            .then(function(data) {
                if (!el) return;
                if (!data.success) { el.innerHTML = '<p style="color:red;font-size:13px;margin-top:8px;">' + (data.error || '失败') + '</p>'; return; }
                var html = '<div style="margin-top:12px;padding:12px;background:var(--surface-alt,#f8f9fa);border-radius:6px;font-size:13px;">';
                html += '<table style="width:100%;border-collapse:collapse;"><tbody>';
                html += '<tr><td style="padding:4px;color:var(--text-2,#666);">对照组准确率</td><td style="padding:4px;font-weight:700;">' + (data.control_accuracy !== null ? Math.round(data.control_accuracy * 100) + '%' : '—') + '</td></tr>';
                html += '<tr><td style="padding:4px;color:var(--text-2,#666);">实验组准确率</td><td style="padding:4px;font-weight:700;">' + (data.experiment_accuracy !== null ? Math.round(data.experiment_accuracy * 100) + '%' : '—') + '</td></tr>';
                var deltaSign = data.delta_accuracy > 0 ? '+' : '';
                var deltaColor = data.delta_accuracy > 0 ? '#27ae60' : data.delta_accuracy < 0 ? '#e74c3c' : '#888';
                html += '<tr><td style="padding:4px;color:var(--text-2,#666);">ΔAccuracy</td><td style="padding:4px;font-weight:700;color:' + deltaColor + ';">' + deltaSign + (data.delta_accuracy !== null ? Math.round(data.delta_accuracy * 10000) / 100 + '%' : '—') + '</td></tr>';
                html += '</tbody></table>';
                if (data.note) html += '<p style="margin:8px 0 0;color:var(--text-3,#888);font-size:12px;">' + data.note + '</p>';
                if (data.sample_warning) html += '<p style="margin:4px 0 0;color:#e65100;font-size:12px;">⚠️ 小样本，结论待M9复核</p>';
                html += '</div>';
                el.innerHTML = html;
            })
            .catch(function(e) { if (el) el.innerHTML = '<p style="color:red;font-size:13px;">错误: ' + e + '</p>'; });
    }

    /**
     * RATING-ALIGN-004：评级→CSS类名映射（兼容新中文5档 + 历史A/B+/B/C/D）
     */
    function getRatingClass(rating) {
        var map = {
            '强烈推荐买入': 'strong-buy',
            '推荐买入': 'buy',
            '持有观望': 'hold',
            '建议减仓': 'reduce',
            '强烈建议卖出': 'strong-sell',
            'A': 'strong-buy', 'B+': 'buy', 'B': 'hold', 'C': 'hold', 'D': 'reduce'
        };
        return 'rating-' + (map[rating] || 'hold');
    }

    /**
     * DEV-TASKS-20260727-003：超买超卖徽标 HTML（obos_signal → 醒目标签）
     * signal: 'overbought' | 'oversold' | null/undefined
     * 返回 '' 时不影响布局（正常状态不显示）
     */
    function obosBadge(signal) {
        if (signal === 'overbought') {
            return '<span class="obos-badge obos-overbought" title="RSI超买或触及布林上轨，短期有回调风险">⚠️ 超买</span>';
        }
        if (signal === 'oversold') {
            return '<span class="obos-badge obos-oversold" title="RSI超卖或触及布林下轨，可能存在反弹机会">⚡ 超卖</span>';
        }
        return '';
    }

    /** B13-T3：评级→分数区间 tooltip 文案 */
    function getRatingTitle(rating) {
        var map = {
            '强烈推荐买入': '综合评分≥85',
            '推荐买入': '综合评分70-84',
            '持有观望': '综合评分50-69',
            '建议减仓': '综合评分30-49',
            '强烈建议卖出': '综合评分<30',
            'A': '综合评分≥85', 'B+': '综合评分70-84', 'B': '综合评分50-69', 'C': '综合评分30-49', 'D': '综合评分<30'
        };
        return map[rating] || '';
    }

    // ============================================================
    // P3-B: 智能预警铃铛（架构师R4：页面可见时轮询 60s，隐藏时停止）
    // ============================================================
    var _alertPollTimer = null;
    var _alertTypeLabels = {
        'rating_change': '评级变动',
        'score_below': '评分跌破',
        'capital_outflow': '资金流出'
    };

    function toggleAlertDropdown(e) {
        if (e) e.stopPropagation();
        var dd = document.getElementById('alertDropdown');
        if (dd.classList.contains('show')) {
            dd.classList.remove('show');
        } else {
            dd.classList.add('show');
            fetchUnreadAlerts();
        }
    }

    // 点击外部关闭下拉
    document.addEventListener('click', function(e) {
        var wrap = document.querySelector('.alert-bell-wrap');
        var dd = document.getElementById('alertDropdown');
        if (wrap && dd && !wrap.contains(e.target)) {
            dd.classList.remove('show');
        }
    });

    function fetchUnreadAlerts() {
        fetch('/api/alerts/unread?limit=20')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.success) {
                    updateAlertBadge(data.unread_count || 0);
                    renderAlertList(data.alerts || []);
                }
            })
            .catch(function(err) { console.warn('[预警] 获取未读失败', err); });
    }

    function updateAlertBadge(count) {
        var badge = document.getElementById('alertBadge');
        if (count > 0) {
            badge.textContent = count > 99 ? '99+' : count;
            badge.classList.add('show');
        } else {
            badge.classList.remove('show');
        }
    }

    function renderAlertList(alerts) {
        var list = document.getElementById('alertList');
        if (!alerts.length) {
            list.innerHTML = '<div class="alert-empty">暂无未读预警</div>';
            return;
        }
        var html = '';
        for (var i = 0; i < alerts.length; i++) {
            var a = alerts[i];
            var typeLabel = _alertTypeLabels[a.alert_type] || a.alert_type;
            var stockLabel = a.name ? (a.name + '(' + (a.symbol || '') + ')') : '';
            html += '<div class="alert-item" onclick="markAlertRead(' + a.id + ', event)">';
            html += '<span class="alert-item-type ' + a.alert_type + '">' + typeLabel + '</span>';
            html += '<div class="alert-item-msg">' + escapeHtml(a.message || '') + '</div>';
            html += '<div class="alert-item-time">' + escapeHtml(formatAlertTime(a.triggered_at)) + '</div>';
            html += '</div>';
        }
        list.innerHTML = html;
    }

    function markAlertRead(alertId, e) {
        if (e) e.stopPropagation();
        fetch('/api/alerts/' + alertId + '/read', { method: 'POST' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.success) {
                    fetchUnreadAlerts();
                }
            })
            .catch(function(err) { console.warn('[预警] 标记已读失败', err); });
    }

    function markAllAlertsRead(e) {
        if (e) e.stopPropagation();
        fetch('/api/alerts/read-all', { method: 'POST' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.success) {
                    fetchUnreadAlerts();
                }
            })
            .catch(function(err) { console.warn('[预警] 全部已读失败', err); });
    }

    // ========== U5(#7): 预警规则管理（对接现有 API，不新增后端路由） ==========
    var _alertRuleTypeLabels = {
        'rating_change': '评级变动',
        'score_below': '评分跌破',
        'capital_outflow': '资金流出'
    };
    var _alertRuleTypeHints = {
        'rating_change': { label: '阈值', show: false, hint: '评级变动无需设置阈值，评级发生升降级时自动提醒' },
        'score_below': { label: '跌破阈值（0-100）', show: true, hint: '当综合评分跌破此值时提醒，建议设 40-70' },
        'capital_outflow': { label: '流出金额（万元）', show: true, hint: '当主力净流出超过此金额时提醒，建议设 500-5000' }
    };
    var _alertStocksCache = null;

    function toggleAlertRulesView(e) {
        if (e) e.stopPropagation();
        var alertList = document.getElementById('alertList');
        var rulesList = document.getElementById('alertRulesList');
        var btn = document.getElementById('alertManageBtn');
        var title = document.getElementById('alertDropdownTitle');
        if (rulesList.style.display === 'none') {
            // 切换到规则管理模式
            alertList.style.display = 'none';
            rulesList.style.display = '';
            btn.textContent = '📬 返回通知';
            title.textContent = '📋 预警规则管理';
            loadAlertRules();
        } else {
            // 切换回未读通知模式
            alertList.style.display = '';
            rulesList.style.display = 'none';
            btn.textContent = '⚙ 管理规则';
            title.textContent = '📋 智能预警';
            fetchUnreadAlerts();
        }
    }

    function loadAlertRules() {
        var list = document.getElementById('alertRulesList');
        list.innerHTML = '<div class="alert-empty">加载中...</div>';
        fetch('/api/alerts/rules')
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (!data.success) {
                    list.innerHTML = '<div class="alert-empty">加载失败</div>';
                    return;
                }
                var rules = data.rules || [];
                if (!rules.length) {
                    list.innerHTML = '<div class="alert-empty">暂无预警规则<br><span style="font-size:12px;">点击下方「添加预警规则」创建</span></div>';
                    return;
                }
                var html = '';
                rules.forEach(function(r) {
                    var typeLabel = _alertRuleTypeLabels[r.rule_type] || r.rule_type;
                    var scopeLabel = r.scope === '全局' ? '🌍 全局' : ('📈 ' + (r.name || r.symbol || '个股'));
                    var thresholdStr = r.threshold != null ? ' | 阈值: ' + r.threshold : '';
                    var enabledStr = r.enabled ? '<span style="color:#27ae60;">✅ 启用</span>' : '<span style="color:var(--text-3,#999);">⏸️ 停用</span>';
                    html += '<div style="padding:12px 16px;border-bottom:1px solid var(--border-light,#f0f0f0);">';
                    html += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px;">';
                    html += '<span class="alert-item-type ' + r.rule_type + '">' + typeLabel + '</span>';
                    html += '<button onclick="deleteAlertRule(' + r.id + ', event)" style="border:none;background:none;color:#e74c3c;cursor:pointer;font-size:12px;padding:2px 6px;">🗑 删除</button>';
                    html += '</div>';
                    html += '<div style="font-size:13px;color:var(--text,#555);">' + scopeLabel + thresholdStr + ' ' + enabledStr + '</div>';
                    html += '</div>';
                });
                list.innerHTML = html;
            })
            .catch(function(err) {
                list.innerHTML = '<div class="alert-empty">加载失败: ' + err + '</div>';
            });
    }

    function deleteAlertRule(ruleId, e) {
        if (e) e.stopPropagation();
        if (!confirm('确定删除这条预警规则吗？')) return;
        fetch('/api/alerts/rules/' + ruleId, { method: 'DELETE' })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.success) {
                    loadAlertRules();
                } else {
                    alert('删除失败：' + (data.message || '未知错误'));
                }
            })
            .catch(function(err) { alert('网络错误：' + err); });
    }

    function openAlertRuleModal(e) {
        if (e) e.stopPropagation();
        var modal = document.getElementById('alertRuleModal');
        modal.style.display = 'flex';
        // 重置表单
        document.getElementById('alertRuleType').value = 'rating_change';
        document.getElementById('alertRuleScope').value = 'global';
        document.getElementById('alertThresholdInput').value = '';
        document.getElementById('alertStockSelectWrap').style.display = 'none';
        document.getElementById('alertRuleError').style.display = 'none';
        onAlertRuleTypeChange();
        // 加载股票列表（缓存）
        if (!_alertStocksCache) {
            fetch('/api/stocks')
                .then(function(r) { return r.json(); })
                .then(function(data) {
                    if (data.success) {
                        _alertStocksCache = data.stocks || [];
                        populateAlertStockSelect();
                    }
                })
                .catch(function() {});
        } else {
            populateAlertStockSelect();
        }
    }

    function populateAlertStockSelect() {
        var sel = document.getElementById('alertStockSelect');
        var html = '';
        _alertStocksCache.forEach(function(s) {
            html += '<option value="' + s.id + '">' + s.symbol + ' ' + (s.name || '') + ' (' + (s.market === 'a_stock' ? 'A股' : '港股') + ')</option>';
        });
        sel.innerHTML = html;
    }

    function closeAlertRuleModal() {
        document.getElementById('alertRuleModal').style.display = 'none';
    }

    function onAlertRuleTypeChange() {
        var type = document.getElementById('alertRuleType').value;
        var conf = _alertRuleTypeHints[type] || {};
        var wrap = document.getElementById('alertThresholdWrap');
        var label = document.getElementById('alertThresholdLabel');
        var hint = document.getElementById('alertThresholdHint');
        if (conf.show) {
            wrap.style.display = '';
            label.textContent = conf.label;
            hint.textContent = conf.hint;
        } else {
            wrap.style.display = 'none';
        }
    }

    function onAlertScopeChange() {
        var scope = document.getElementById('alertRuleScope').value;
        document.getElementById('alertStockSelectWrap').style.display = (scope === 'stock') ? '' : 'none';
    }

    function submitAlertRule() {
        var type = document.getElementById('alertRuleType').value;
        var scope = document.getElementById('alertRuleScope').value;
        var thresholdInput = document.getElementById('alertThresholdInput').value;
        var errorDiv = document.getElementById('alertRuleError');
        errorDiv.style.display = 'none';

        var body = { rule_type: type };
        if (scope === 'stock') {
            body.stock_id = parseInt(document.getElementById('alertStockSelect').value);
        }
        var conf = _alertRuleTypeHints[type] || {};
        if (conf.show) {
            var tv = parseFloat(thresholdInput);
            if (isNaN(tv)) {
                errorDiv.textContent = '请输入有效的阈值';
                errorDiv.style.display = '';
                return;
            }
            body.threshold = tv;
        }

        fetch('/api/alerts/rules', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        })
            .then(function(r) { return r.json(); })
            .then(function(data) {
                if (data.success) {
                    closeAlertRuleModal();
                    // 确保在规则管理模式下
                    if (document.getElementById('alertRulesList').style.display === 'none') {
                        toggleAlertRulesView();
                    } else {
                        loadAlertRules();
                    }
                } else {
                    errorDiv.textContent = data.message || '创建失败';
                    errorDiv.style.display = '';
                }
            })
            .catch(function(err) {
                errorDiv.textContent = '网络错误：' + err;
                errorDiv.style.display = '';
            });
    }

    // 点击弹窗背景关闭
    document.getElementById('alertRuleModal').addEventListener('click', function(e) {
        if (e.target === this) closeAlertRuleModal();
    });

    function formatAlertTime(ts) {
        if (!ts) return '';
        return String(ts).replace('T', ' ').substring(0, 16);
    }

    function escapeHtml(str) {
        if (!str) return '';
        return String(str)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }

    // 页面可见时轮询，隐藏时停止（架构师R4）
    function startAlertPolling() {
        if (_alertPollTimer) return;
        _alertPollTimer = setInterval(fetchUnreadAlerts, 60000);
    }
    function stopAlertPolling() {
        if (_alertPollTimer) {
            clearInterval(_alertPollTimer);
            _alertPollTimer = null;
        }
    }
    document.addEventListener('visibilitychange', function() {
        if (document.hidden) {
            stopAlertPolling();
        } else {
            fetchUnreadAlerts();
            startAlertPolling();
        }
    });
    // 初始加载一次 + 启动轮询
    fetchUnreadAlerts();
    startAlertPolling();

