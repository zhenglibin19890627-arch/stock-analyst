// OPT-4（2026-09-07）：自 app.js 按业务域拆分（纯搬移）；加载顺序见 templates/index.html，core 必须最先。

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
