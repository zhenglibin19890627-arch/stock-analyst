// OPT-4（2026-09-07）：自 app.js 按业务域拆分（纯搬移）；加载顺序见 templates/index.html，core 必须最先。

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

    // 2026-09-07 共振重设计：4 组买点共振渲染顺序（死叉/超买/超卖类已删）
    var _MS_RES_ORDER = ['res_week_daily', 'res_bottom_reverse', 'res_zero_relay', 'res_double_golden'];
    var _MS_RES_META = {
        res_week_daily:     { icon: '⭐⭐⭐⭐⭐', note: '周线共振波段：周线MACD多头+日线金叉，中线波段结构' },
        res_bottom_reverse: { icon: '⭐⭐⭐⭐⭐', note: '底部反转共振：底背离+KDJ低位金叉+放量阳线，左侧反转最强确认' },
        res_zero_relay:     { icon: '⭐⭐⭐⭐⭐', note: '零轴上二次金叉：近15日MACD二次金叉+KDJ中位金叉，主升浪中继买点' },
        res_double_golden:  { icon: '⭐⭐⭐⭐',   note: '双金叉共振：MACD系+KDJ系金叉同窗，同日触发更佳' }
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
        // 2026-09-09：子项行 detail 已含对应读数（MA 状态等）——有子项时不再渲染
        // 重复读数行，一行说清（无子项数据的旧报告回退读数行）
        var monthlyInner = _subRow('monthly_trend', '月线方向');
        if (!(subs && subs['monthly_trend'])) {
            monthlyInner += _row('月线均线',
                (td.monthly_ma5 != null
                    ? ('MA5 ' + td.monthly_ma5 +
                        (td.monthly_ma10 != null ? ' · MA10 ' + td.monthly_ma10 : '') +
                        (td.monthly_ma20 != null ? ' · MA20 ' + td.monthly_ma20 : ''))
                    : null),
                td.monthly_ma_state);
        }
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
                ('当月量/前20月均 ' + td.monthly_vol_ratio), td.monthly_vol_state);
        }
        if (td.monthly_latest_close != null) {
            monthlyInner += _row('月线收盘', String(td.monthly_latest_close), null);
        }
        if (td.monthly_penalty) {
            // 2026-09-07：后端文本若含 "<" 会被 innerHTML 当标签吞掉（实测"⚠ 月线空头(MA5"
            // 半截显示）——此处做转义防守
            monthlyInner += '<div style="font-size:11px;color:#e67e22;margin:2px 0;">⚠ ' +
                String(td.monthly_penalty).replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</div>';
        }
        html += _group('📈 月线方向层', '25%', _layerScore(['monthly_trend']), monthlyInner);

        // ---- 周线波段层（45%）----
        var weeklyInner = _subRow('weekly_trend', '周线趋势') +
            _subRow('weekly_obos', '周线超买超卖') +
            _subRow('weekly_vol', '周线波动');
        // 读数行去重（2026-09-09）：均线/MACD 已并入周线趋势子项 detail、RSI 已并入
        // 周线超买超卖、布林位置已并入周线波动——有子项时不再重复渲染
        if (!(subs && subs['weekly_trend'])) {
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
        }
        if (!(subs && subs['weekly_obos']) && td.weekly_rsi14 != null) {
            weeklyInner += _row('周线RSI', String(td.weekly_rsi14), td.weekly_rsi_state);
        }
        if (!(subs && subs['weekly_vol']) && td.weekly_boll_position != null) {
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
            weeklyInner += _row('周线量能', ('本周量/前20周均 ' + td.weekly_vol_ratio), td.weekly_vol_state);
        }
        html += _group('📊 周线波段层', '45%', _layerScore(['weekly_trend', 'weekly_obos', 'weekly_vol']), weeklyInner);

        // ---- 日线择时层（30%）----
        // 读数行去重（2026-09-09）：RSI/KDJ 已并入超买超卖子项、量能已并入量比子项；
        // 均线系统/MACD趋势/布林带不参评，保留展示
        var dailyInner = _subRow('obos', '超买超卖') +
            _subRow('vol_price', '量价分析') +
            _subRow('vol_ratio', '量比') +
            _row('均线系统',
                (td.ma5 != null ? ('MA5 ' + td.ma5 + '/MA10 ' + td.ma10 + '/MA20 ' + td.ma20) : null),
                td.ma_state) +
            _row('MACD趋势',
                (td.macd_dif != null ? ('DIF' + td.macd_dif + '/DEA' + td.macd_dea + '/柱' + (td.macd_hist >= 0 ? '+' : '') + td.macd_hist) : null),
                td.macd_state) +
            _row('布林带',
                (td.boll_position != null
                    ? (td.boll_position + '% 上' + td.boll_upper + '/' + td.boll_mid + '/' + td.boll_lower)
                    : null),
                td.boll_state);
        if (!(subs && subs['obos'])) {
            dailyInner += _row('RSI(14)', (td.rsi14 != null ? String(td.rsi14) : null), td.rsi_state) +
                _row('KDJ', (td.kdj_k != null ? ('K ' + td.kdj_k + ' · D ' + td.kdj_d + ' · J ' + td.kdj_j) : null), td.kdj_state);
        }
        if (!(subs && subs['vol_ratio'])) {
            dailyInner += _row('量能', (td.vol_ratio != null ? ('量比 ' + td.vol_ratio) : null), td.vol_state);
        }
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
        // 读数行去重（2026-09-09）：子项 detail 已含同值——有子项时不再渲染
        if (!(subs && subs['valuation'])) {
            g1Html += _dimFoldRow('估值',
                (fd.pe != null ? ('PE ' + _fv(fd.pe, fd.pe_state)) : '') +
                (fd.pb != null ? (' · PB ' + _fv(fd.pb, fd.pb_state)) : ''));
        }
        if (!(subs && subs['profitability'])) {
            g1Html += _dimFoldRow('盈利能力',
                (fd.roe != null ? ('ROE ' + _fv(fd.roe + '%', fd.roe_state)) : '') +
                (fd.gross_margin != null ? (' · 毛利率 ' + _fv(fd.gross_margin + '%', fd.gm_state)) : ''));
        }
        html += _dimFoldGroup('估值与盈利', '📊', g1.wpct, g1.score, g1Html);

        // ---- 成长性层（25%；业绩预告/快报折价参评，020R-49/50）----
        var g2 = _dimFoldStats(subs, ['growth']);
        var g2Html = subs ? _dimFoldSubRow(subs, 'growth', '成长性') : '';
        if (!(subs && subs['growth'])) {
            g2Html += _dimFoldRow('成长性',
                (fd.revenue_growth != null ? ('营收 ' + _fv((fd.revenue_growth > 0 ? '+' : '') + fd.revenue_growth + '%', fd.rg_state)) : '') +
                (fd.profit_growth != null ? (' · 净利 ' + _fv((fd.profit_growth > 0 ? '+' : '') + fd.profit_growth + '%', fd.pg_state)) : ''));
        }
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
        if (!(subs && subs['cashflow'])) {
            g3Html += _dimFoldRow('现金流质量',
                fd.ocf_to_profit != null ? ('经营现金流/净利润 ' + _fv(fd.ocf_to_profit, fd.ocf_state)) : '');
        }
        if (!(subs && subs['fin_health'])) {
            g3Html += _dimFoldRow('财务健康度',
                (fd.debt_ratio != null ? ('负债率 ' + _fv(fd.debt_ratio + '%', fd.dr_state)) : '') +
                (fd.current_ratio != null ? (' · 流动比率 ' + _fv(fd.current_ratio, fd.cr_state)) : ''));
        }
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
        // 读数行去重（2026-09-09）：主力资金/主力5日均已并入子项 detail（融合口径
        // "当日X/5日均Y融合"信息量大于单独读数行）——有子项时不再渲染
        if (!(subs && subs['main_capital'])) {
            g1Html += _dimFoldRow('主力资金',
                cd.main_net != null ? _fv(_wanFmt(cd.main_net), cd.main_state) :
                '<span style="color:var(--text-3,#999);">数据缺失</span>');
            if (cd.main_avg_5d != null) {
                g1Html += _dimFoldRow('主力5日均', '<span style="color:var(--text,#333);font-weight:400;">' + _wanFmt(cd.main_avg_5d) + '</span>');
            }
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
        // 读数行去重（2026-09-09）：有子项时不重复渲染（港股机构股东数/南向参考无子项，保留）
        if (!(subs && subs['inst_hold'])) {
            g2Html += _dimFoldRow('机构持仓',
                cd.inst_ratio != null
                    ? _fv(cd.inst_ratio + '%' + (cd.inst_report_date ? '（' + cd.inst_report_date + '）' : ''), cd.inst_state)
                    : '<span style="color:var(--text-3,#999);">数据缺失</span>');
        }
        if (!(subs && subs['holder_count'])) {
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
            var g3Html = subs ? _dimFoldSubRow(subs, 'margin_capital', '杠杆资金') : '';
            if (!(subs && subs['margin_capital'])) {
                g3Html += _dimFoldRow('杠杆资金',
                    cd.margin_chg != null ? ('融资余额 ' + _fv(_wanFmt(cd.margin_chg), cd.margin_state)) :
                    '<span style="color:var(--text-3,#999);">数据缺失（A股两融）</span>');
            }
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
        // 读数行去重（2026-09-09）：情绪值已并入子项 detail——有子项时不再渲染
        if (!(subs && subs['sentiment'])) {
            g1Html += _dimFoldRow('情绪',
                nd.avg_sentiment != null
                    ? _fv((nd.avg_sentiment > 0 ? '+' : '') + nd.avg_sentiment.toFixed(2), nd.sentiment_state)
                    : '<span style="color:var(--text-3,#999);">数据缺失</span>');
        }
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
        // 读数行去重（2026-09-09）：有子项时不重复渲染
        if (!(subs && subs['holder'])) {
            if (nd.holder === true) {
                g2Html += _dimFoldRow('股东行为', _fv('增持', '增持·利好'));
            } else if (nd.holder === false) {
                g2Html += _dimFoldRow('股东行为',
                    '<span style="color:#27ae60;font-weight:600;">近30天无增持</span>');
            } else {
                g2Html += _dimFoldRow('股东行为',
                    '<span style="color:var(--text-3,#999);">数据缺失（接口不可用或港股未采集）</span>');
            }
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
