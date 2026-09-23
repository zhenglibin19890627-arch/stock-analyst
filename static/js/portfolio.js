// OPT-4（2026-09-07）：自 app.js 按业务域拆分（纯搬移）；加载顺序见 templates/index.html，core 必须最先。

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


    // ============================================================
    // P2: 总览看板 页面逻辑
    // ============================================================

    var _dashData = null;       // 看板原始数据缓存
    var _dashSortState = {};    // 排序状态

    // OPT-8（2026-09-07）：数据源健康度卡片（红/黄/绿，近 7 天，只读）
    // 021BN：灰灯=已停更维度（不计入告警）；成功率剔除 skipped；新增"说明"列（接口口径）
    function _srcHealthDot(level) {
        var c = level === 'red' ? '#e74c3c' : (level === 'yellow' ? '#f39c12' : (level === 'grey' ? '#bbb' : '#27ae60'));
        return '<span style="display:inline-block;width:10px;height:10px;border-radius:50%;background:' + c + ';margin-right:6px;"></span>';
    }
    function loadSourceHealth() {
        var body = document.getElementById('sourceHealthBody');
        if (!body) return;
        fetch('/api/health/sources', {cache: 'no-store'}).then(function(r) { return safeJson(r); }).then(function(d) {
            if (!d || !d.success) { body.innerHTML = '<span style="color:#e74c3c;">加载失败</span>'; return; }
            var html = '';
            html += '<div style="margin-bottom:6px;">整体：' + _srcHealthDot(d.overall_level) +
                    '<b>' + (d.overall_level === 'red' ? '异常' : (d.overall_level === 'yellow' ? '注意' : '正常')) + '</b>' +
                    '<span style="color:var(--text-3,#888);font-size:12px;margin-left:8px;">近 ' + d.window_days + ' 天 · ' + d.generated_at + '</span></div>';
            html += '<div style="color:var(--text-3,#888);font-size:11.5px;margin-bottom:6px;">' +
                    '成功率已剔除"跳过"（节流/维度不适用，如港股无快报、当日重复采集）；' +
                    '<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#bbb;margin:0 2px;"></span>灰=数据源已停更，不计入告警</div>';
            if (d.dimensions.length) {
                html += '<table style="width:100%;border-collapse:collapse;font-size:12.5px;">';
                html += '<tr style="color:var(--text-3,#888);text-align:left;"><th style="padding:3px 8px 3px 0;">维度</th>' +
                        '<th style="text-align:left;font-weight:normal;">说明</th><th>7天成功率</th><th>跳过</th><th>最后成功</th><th>连续失败</th></tr>';
                d.dimensions.forEach(function(x) {
                    var rate = x.success_rate != null ? (x.success_rate * 100).toFixed(0) + '%' : (x.discontinued ? '停更' : '—');
                    var nameHtml = _srcHealthDot(x.level) + (x.label || x.dimension);
                    if (x.discontinued) {
                        nameHtml += ' <span style="background:var(--bg-light,#eee);color:#888;padding:0 6px;border-radius:8px;font-size:11px;">已停更</span>';
                    }
                    html += '<tr><td style="padding:2px 8px 2px 0;white-space:nowrap;">' + nameHtml + '</td>' +
                            '<td style="color:var(--text-3,#888);font-size:12px;">' + (x.desc || '') + '</td>' +
                            '<td>' + rate + '</td>' +
                            '<td style="color:var(--text-3,#888);text-align:center;">' + (x.skipped > 0 ? x.skipped : '—') + '</td>' +
                            '<td>' + (x.last_ok || '—') + '</td><td>' +
                            (x.consecutive_failures > 0 ? '<span style="color:#e74c3c;">' + x.consecutive_failures + '</span>' : '0') + '</td></tr>';
                });
                html += '</table>';
            } else {
                html += '<div style="margin:4px 0;">近 7 天无采集记录</div>';
            }
            if (d.sources.length) {
                html += '<div style="margin-top:6px;font-size:12.5px;">' + _srcHealthDot('red') + '近 7 天有报错的数据源模块：</div>';
                d.sources.slice(0, 5).forEach(function(x) {
                    html += '<div style="font-size:12.5px;padding-left:16px;">' + x.module +
                            ' <span style="color:#e74c3c;">' + x.errors_7d + ' 次</span>' +
                            ' <span style="color:var(--text-3,#888);">最近 ' + x.last_error_at + '</span></div>';
                });
            }
            body.innerHTML = html;
        }).catch(function(e) {
            body.innerHTML = '<span style="color:#e74c3c;">加载失败：' + e + '</span>';
        });
    }

    function loadDashboard() {
        var container = document.getElementById('dashboardContent');
        container.innerHTML = '<div class="report-loading">正在加载总览看板...</div>';

        // 并行请求 summary + watchlist-scores + action-list + intraday
        // 021E：no-store 规避服务端 ETag 304 空响应导致的静默失败（同 refreshDashboardData）
        // 021O：移除 index-ratings 请求——大盘指数移至市场行情页查看，看板聚焦持仓/自选
        var summaryPromise = fetch('/api/portfolio/summary', {cache: 'no-store'}).then(function(r) { return safeJson(r); });
        var scoresPromise  = fetch('/api/portfolio/watchlist-scores', {cache: 'no-store'}).then(function(r) { return safeJson(r); });
        // 021BP 项3：今日行动清单（四路只读聚合；失败静默降级不阻塞看板）
        var actionPromise  = fetch('/api/dashboard/action-list', {cache: 'no-store'}).then(function(r) { return safeJson(r); });
        // 021BT：盘中速览（持仓盘中状态一屏；失败/网络异常静默降级 null，不阻塞看板）
        var intradayPromise = fetch('/api/dashboard/intraday', {cache: 'no-store'})
            .then(function(r) { return safeJson(r); })
            .catch(function() { return null; });

        Promise.all([summaryPromise, scoresPromise, actionPromise, intradayPromise])
            .then(function(results) {
                var summary = results[0];
                var scores = results[1];
                var actionList = results[2];
                var intraday = results[3];
                if (!scores.success) {
                    container.innerHTML = '<div class="report-empty"><p style="color:#e74c3c;">加载失败</p></div>';
                    return;
                }
                _dashData = { summary: summary, stocks: scores.stocks || [], reportDate: scores.report_date, reportDateMin: scores.report_date_min, generatedAt: scores.generated_at, actionList: (actionList && actionList.success) ? actionList : null, intraday: (intraday && intraday.success) ? intraday : null };
                if (_dashData.intraday) _intradayAutoLastData = _dashData.intraday; // ④ 自动刷新时段判定参考
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
        html += '<div class="dash-grid">';        // 总资产
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

        // ---- 1.2 今日行动清单（021BP 项3：四路只读聚合，30 秒看完今天该关注什么） ----
        html += renderActionListCard(data.actionList);

        // ---- 1.25 盘中速览卡（021BT：持仓盘中状态一屏 + 一键刷新；失败静默收缩） ----
        html += renderIntradayCard(data.intraday);

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

        // ---- 5. OPT-8：数据源健康度卡片（异步填充，只读端点） ----
        html += '<div id="sourceHealthCard" style="margin-top:16px;"><div class="card"><div class="card-title">🩺 数据源健康度</div><div id="sourceHealthBody" style="color:var(--text-3,#999);font-size:13px;">加载中...</div></div></div>';

        container.innerHTML = html;
        loadSourceHealth();

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

            // 021BU：评级旁历史命中徽章 + 位置分化注记 chip（与报告页同源同值——
            // 消费 watchlist-scores 响应内联的 rating_evidence/position_note；
            // 港股展示暂缓（O8/R20）：隐藏而非空占位；文案渲染前转义（禁裸 '<'））
            var evBadge = '';
            if ((st.market || 'a_stock') !== 'hk_stock' && st.rating_evidence && st.rating_evidence.primary) {
                var ev = st.rating_evidence;
                var evWarn = ev.primary.grade === 'B' ? ' ⚠️样本偏小' : '';
                var evTip = '该评级档的历史回测命中率（T+1 主口径，与回测中心市场报告同源同值）';
                if (ev.dynamic && ev.dynamic.display) evTip += '；' + ev.dynamic.display;
                evBadge = '<div style="font-size:10.5px;color:var(--text-3,#999);margin-top:3px;" title="' +
                    escapeHtml(evTip) + '">📈 ' + escapeHtml(ev.primary.display) + escapeHtml(evWarn) + '</div>';
            }
            var posChip = '';
            if (st.position_note && st.position_note.text) {
                posChip = '<div style="margin-top:3px;"><span style="font-size:10.5px;color:#8a6d00;background:#fff8e1;' +
                    'border:1px solid #ffd54f;border-radius:10px;padding:1px 7px;cursor:help;" title="' +
                    escapeHtml(String(st.position_note.text)) + '">📍位置分化标注</span></div>';
            }

            html += '<tr style="border-bottom:1px solid var(--border-light,#eee);" id="dash-row-' + st.id + '">';
            html += '<td style="padding:10px;"><strong>' + (st.name || '') + obosBadge(st.obos_signal) + '</strong><br><span style="color:var(--text-3,#888);font-size:12px;">' + st.symbol + '</span></td>';
            html += '<td style="padding:10px;">' + engineTag + '</td>';
            html += '<td style="padding:10px;font-size:16px;font-weight:700;color:' + _scoreColor(st.total_score || 0) + ';">' + scoreStr + '</td>';
            html += '<td style="padding:10px;"><span class="rating-badge ' + getRatingClass(st.rating) + '" title="' + getRatingTitle(st.rating) + '">' + (st.rating || '—') + '</span>' + evBadge + posChip + '</td>';
            html += '<td style="padding:10px;">' + changeStr + '</td>';
            html += '<td style="padding:10px;text-align:center;">' + dataTag + '</td>';
            html += '<td style="padding:10px;font-size:12px;color:var(--text-2,#666);white-space:nowrap;">' + _fmtGenTime(st.generated_at) + '</td>';
            html += '<td style="padding:10px;font-size:12px;color:var(--text-3,#888);white-space:nowrap;">' + (st.report_date || '<span style="color:#ccc;">暂无</span>') + '</td>';
            html += '<td style="padding:10px;font-size:13px;">' + industryTag + flowBadge + '</td>';
            html += '<td style="padding:10px;">' + mvStr + '</td>';
            html += '<td style="padding:10px;"><button class="btn btn-sm" style="padding:4px 10px;font-size:12px;" onclick="viewReport(' + st.id + ')">📊 详情</button></td>';
            html += '</tr>';
        });
        // 021BU：价格建议历史基准脚注（市场级真实锚点注记，预期管理；A股行存在才展示，
        // 港股暂缓隐藏。与报告页价格建议卡消费同一后端函数——同源同值）
        var _hasA = stocks.some(function(s) { return (s.market || 'a_stock') !== 'hk_stock'; });
        var _pev = (_dashData && _dashData.evidence_price) ? _dashData.evidence_price.a_stock : null;
        if (_hasA && _pev && _pev.display) {
            html += '<tr><td colspan="11" style="padding:8px 10px;font-size:11.5px;color:var(--text-3,#999);' +
                'border-top:1px dashed var(--border-light,#eee);line-height:1.6;">' +
                escapeHtml(String(_pev.display)) + '</td></tr>';
        }
        tbody.innerHTML = html;
        dashUpdateFilterCount(stocks.length, _dashData ? _dashData.stocks.length : stocks.length);
    }

    /**
     * 🎯 今日行动清单卡（021BP 决策闭环 项3）：消费 GET /api/dashboard/action-list。
     * 后端已按"今日应做"排序：评级升降 > 买点共振≥4星 > 预警未读 > 超时缺报股；
     * 本函数只做渲染：徽标配色（红升绿降，与看板红涨绿跌惯例一致）+ 统计行 + 点击行看报告。
     * 聚合接口失败时返回空串静默跳过，不阻塞看板其余卡片。
     */
    // ========== 今日行动清单筛选（021BR：类型 chips + 只看持仓，纯前端过滤） ==========
    var _alFilter = { kind: 'all', heldOnly: false };
    function _alGroupOf(kind) {
        if (kind === 'stop_discipline') return 'stop';
        if (kind === 'sell_signal') return 'sell';
        if (kind === 'tech_signal') return 'buy';
        if (kind === 'alert_unread') return 'alert';
        if (kind === 'report_failed') return 'missing';
        if (kind && kind.indexOf('rating') === 0) return 'rating';
        return 'other';
    }
    function alSetFilter(kind) {
        // 再点同一 chip 取消筛选回到「全部」
        _alFilter.kind = (_alFilter.kind === kind) ? 'all' : kind;
        _alRefreshActionCard();
    }
    function alToggleHeld() {
        _alFilter.heldOnly = !_alFilter.heldOnly;
        _alRefreshActionCard();
    }
    function _alRefreshActionCard() {
        var el = document.getElementById('actionListCard');
        if (el && typeof _dashData !== 'undefined' && _dashData && _dashData.actionList) {
            el.outerHTML = renderActionListCard(_dashData.actionList);
        }
    }
    function _alChip(key, label, n, active) {
        if (!n) return '';
        var bgc = active ? '#34495e' : 'var(--bg-light,#f0f0f0)';
        var fgc = active ? '#fff' : 'var(--text-2,#666)';
        return '<span onclick="alSetFilter(\'' + key + '\')" style="cursor:pointer;user-select:none;font-size:12px;padding:2px 10px;border-radius:10px;background:' + bgc + ';color:' + fgc + ';white-space:nowrap;">' + label + ' ' + n + '</span>';
    }

    function renderActionListCard(al) {
        if (!al || !al.items) return '';
        var html = '<div class="card" id="actionListCard" style="margin-bottom:20px;">';
        html += '<div class="card-title">🎯 今日行动清单 <span style="font-size:13px;color:var(--text-3,#888);font-weight:normal;">（' + escapeHtml(al.date || '') + ' · 按今日应做排序，点击行查看个股报告）</span></div>';
        var st = al.stats || {};
        html += '<div style="display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;color:var(--text-3,#888);margin-bottom:8px;">';
        html += '<span>自选 <b>' + (st.active_count || 0) + '</b> 只</span>';
        html += '<span>今日已报 <b>' + (st.reported_ok_today || 0) + '</b></span>';
        if (st.failed_today > 0) html += '<span style="color:#e74c3c;">生成失败 <b>' + st.failed_today + '</b></span>';
        if (st.missing_today > 0) html += '<span>今日缺报 <b>' + st.missing_today + '</b></span>';
        if (st.rating_moves > 0) html += '<span style="color:#e65100;">评级变动 <b>' + st.rating_moves + '</b></span>';
        if (st.stop_discipline_hits > 0) html += '<span style="color:#c0392b;font-weight:600;">止损已触发 <b>' + st.stop_discipline_hits + '</b></span>';
        if (st.signal_hits > 0) html += '<span style="color:#1565c0;">买点信号 <b>' + st.signal_hits + '</b>（共振4星以上 ' + (st.resonance_hits || 0) + '）</span>';
        if (st.sell_hits > 0) html += '<span style="color:#00695c;">卖点信号 <b>' + st.sell_hits + '</b>（共振4星以上 ' + (st.sell_resonance_hits || 0) + '）</span>';
        if (st.unread_alerts_today > 0) html += '<span style="color:#2e7d32;">未读预警 <b>' + st.unread_alerts_today + '</b></span>';
        html += '</div>';
        // 021BR：筛选 chips（类型 + 只看持仓）——纯前端过滤，再点同一 chip 取消
        var _AL_GROUPS = [
            {key: 'stop', label: '🛑 止损纪律'},
            {key: 'rating', label: '⚖ 评级变动'},
            {key: 'sell', label: '🟢 卖点信号'},
            {key: 'buy', label: '🔵 买点信号'},
            {key: 'alert', label: '🔔 预警未读'},
            {key: 'missing', label: '📄 缺报补数'}
        ];
        var _groupCount = {};
        var _heldCount = 0;
        al.items.forEach(function(it) {
            var g = _alGroupOf(it.kind);
            _groupCount[g] = (_groupCount[g] || 0) + 1;
            if (it.held) _heldCount++;
        });
        html += '<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:8px;">';
        html += _alChip('all', '全部', al.items.length, _alFilter.kind === 'all');
        _AL_GROUPS.forEach(function(g) { html += _alChip(g.key, g.label, _groupCount[g.key] || 0, _alFilter.kind === g.key); });
        if (_heldCount > 0) {
            html += '<span onclick="alToggleHeld()" style="cursor:pointer;user-select:none;font-size:12px;padding:2px 10px;border-radius:10px;background:' + (_alFilter.heldOnly ? '#2e7d32' : '#e8f5e9') + ';color:' + (_alFilter.heldOnly ? '#fff' : '#2e7d32') + ';white-space:nowrap;">📍只看持仓 ' + _heldCount + '</span>';
        }
        html += '</div>';
        if (!al.items.length) {
            html += '<div style="padding:14px 0;font-size:13px;color:var(--text-3,#999);">今日暂无待办行动项——评级平稳、无新买点信号、预警全部已读</div>';
        } else {
            var _visibleCount = 0;
            html += '<div style="max-height:340px;overflow-y:auto;">';
            al.items.forEach(function(it) {
                var bg, fg, label;
                if (it.kind === 'stop_discipline') { bg = '#fdecea'; fg = '#c0392b'; label = '止损纪律·已触发'; }
                else if (it.kind === 'rating_upgrade') { bg = '#fdecea'; fg = '#c62828'; label = '评级升级'; }
                else if (it.kind === 'rating_downgrade') { bg = '#e8f5e9'; fg = '#2e7d32'; label = '评级降级'; }
                else if (it.kind === 'rating_change') { bg = '#fff3e0'; fg = '#e65100'; label = '评级变动'; }
                else if (it.kind === 'tech_signal') {
                    // 021BP 修订：与最新评级相悖（减仓/卖出档）的反弹信号 → 琥珀色徽标
                    var rc = it.detail && it.detail.rating_conflict;
                    bg = rc ? '#fff3e0' : '#e3f2fd'; fg = rc ? '#e65100' : '#1565c0';
                    label = rc ? '反弹信号·与评级相悖' : '买点信号';
                }
                else if (it.kind === 'sell_signal') {
                    // 021BQ：卖点信号（A股绿=风控惯例）——相悖琥珀 / 持仓绿系 / 空仓灰蓝
                    var sd = it.detail || {};
                    if (sd.rating_conflict) { bg = '#fff3e0'; fg = '#e65100'; label = '卖出信号·与评级相悖'; }
                    else if (sd.held) { bg = '#e8f5e9'; fg = '#2e7d32'; label = '卖出信号·持仓'; }
                    else { bg = '#e0f2f1'; fg = '#00695c'; label = '回避信号'; }
                }
                else if (it.kind === 'alert_unread') { bg = '#fff8e1'; fg = '#b26a00'; label = '预警未读'; }
                else { bg = 'var(--bg-light,#f0f0f0)'; fg = '#888'; label = '缺报补数'; }
                var g = _alGroupOf(it.kind);
                var visible = (_alFilter.kind === 'all' || _alFilter.kind === g) && (!_alFilter.heldOnly || it.held);
                if (visible) _visibleCount++;
                html += '<div onclick="viewReport(' + it.stock_id + ')" style="display:' + (visible ? 'flex' : 'none') + ';align-items:flex-start;gap:8px;padding:8px 4px;border-bottom:1px solid var(--border-light,#f0f0f0);cursor:pointer;">';
                html += '<span style="background:' + bg + ';color:' + fg + ';font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;white-space:nowrap;margin-top:1px;">' + label + '</span>';
                html += '<div style="flex:1;font-size:13px;line-height:1.5;">';
                html += '<b>' + escapeHtml(it.name || '') + '</b> <span style="color:var(--text-3,#888);">' + escapeHtml(it.symbol || '') + '</span>';
                if (it.held) {
                    // 021BR：持仓徽标扩展到全部行型（账户无关聚合口径，quantity>0 即持仓中）
                    var qtyTxt = (it.detail && it.detail.total_qty) ? it.detail.total_qty.toLocaleString() + ' 股' : '';
                    html += '<span style="background:#e8f5e9;color:#2e7d32;font-size:10.5px;padding:1px 6px;border-radius:8px;margin-left:6px;white-space:nowrap;">📍持仓' + qtyTxt + '</span>';
                }
                html += '<div style="color:var(--text-2,#555);">' + escapeHtml(it.reason || '') + '</div>';
                html += '</div></div>';
            });
            html += '</div>';
            if (_visibleCount === 0) {
                html += '<div style="padding:12px 0;font-size:13px;color:var(--text-3,#999);">当前筛选条件下没有行动项——点上方「全部」或取消「只看持仓」恢复。</div>';
            }
        }
        if ((st.missing_today || 0) > 0) {
            html += '<div style="margin-top:10px;font-size:12px;color:var(--text-3,#aaa);">另有 ' + st.missing_today + ' 只今日尚无有效报告（生成失败的已在上方列出）——可点下方「🚀 生成今日报告」补齐。</div>';
        }
        html += '<div style="margin-top:8px;font-size:12px;color:var(--text-3,#aaa);">买卖点信号均为离线快照参考口径（基于已采集K线复算，截止最新采集日；卖出信号同为离线快照参考，持仓标记来自持仓账户聚合），不构成投资建议。</div>';
        html += '</div>';
        return html;
    }

    // ========== ⏱ 盘中速览卡（021BT：持仓盘中状态一屏 + 一键刷新；021BV v2） ==========
    // 数据源 GET /api/dashboard/intraday（loadDashboard 第4路并行；行字段见端点 docstring 契约）。
    // 刷新走 POST /api/dashboard/intraday/refresh（仅持仓 8 只批量取价+写 price_cache，
    // 后端 60s 冷却权威节流——比全自选 refresh-prices 更克制，021BN-c 数据源风控）。
    // 021BV v2 增量：①当日浮动盈亏列（holdings 聚合同源）②排序切换（纯前端+记忆）
    // ③行动指引列（操盘手矩阵首行动作文）④交易时段自动刷新开关（默认关）
    // ⑤当日已录流水概览行。零写库契约不变。
    var _intradayLastRefreshAt = 0;
    var _intradayRefreshing = false;
    var INTRADAY_DISCLAIMER = '盘中口径，以收盘确认为准';
    // ---- 021BV v2 状态 ----
    var _intradaySort = 'stop';          // 'stop' 距止损 | 'pnl' 当日盈亏 | 'pct' 涨跌幅
    var _intradayAutoOn = false;         // 自动刷新开关（默认关；不跨会话记忆）
    var _intradayAutoTimer = null;
    var _intradayAutoLastData = null;    // 最近一次成功快照（自动刷新时段判定参考）
    var INTRADAY_AUTO_INTERVAL_SEC = 90; // 60~120s 区间内取中

    (function () {
        // 排序状态记忆：localStorage 持久（隐私模式等不可用时静默回默认）
        try {
            var saved = window.localStorage.getItem('intradaySort');
            if (saved === 'stop' || saved === 'pnl' || saved === 'pct') _intradaySort = saved;
        } catch (e) { /* localStorage 不可用：保持默认 */ }
    })();

    function _intradayPersistSort(key) {
        try { window.localStorage.setItem('intradaySort', key); } catch (e) { /* 忽略 */ }
    }

    function intradaySetSort(key) {
        // 排序 chips（纯前端重排，不重发请求；重复点击保持当前项）
        _intradaySort = key;
        _intradayPersistSort(key);
        var el = document.getElementById('intradayCard');
        if (el && typeof _dashData !== 'undefined' && _dashData && _dashData.intraday) {
            el.outerHTML = renderIntradayCard(_dashData.intraday);
        }
    }

    function _intradaySortRows(rows, sortKey) {
        // 纯函数排序（可被 node 契约测试直接加载验证，勿引外部 helper）：
        // 'stop' 距止损% 升序——已破线/最接近止损在前（风控优先）；
        // 'pnl'  当日浮动盈亏% 升序——亏损最大在前；
        // 'pct'  盘中涨跌幅% 升序——跌幅最大在前。
        // 键缺失（null/undefined）一律殿后；同值按严重度（触线>逼近>其余）再按代码。
        var key = sortKey || _intradaySort;
        var sev = { below_stop: 0, near_stop: 1 };
        function val(r) {
            if (key === 'pnl') return r.unrealized_pnl_pct;
            if (key === 'pct') return r.pct_change;
            return r.distance_pct;
        }
        return rows.slice().sort(function (a, b) {
            var va = val(a), vb = val(b);
            if (va == null && vb != null) return 1;
            if (vb == null && va != null) return -1;
            if (va != null && vb != null && va !== vb) return va - vb;
            var sa = sev[a.state] != null ? sev[a.state] : 2;
            var sb = sev[b.state] != null ? sev[b.state] : 2;
            if (sa !== sb) return sa - sb;
            var ta = a.symbol || '', tb = b.symbol || '';
            return ta < tb ? -1 : (ta > tb ? 1 : 0);
        });
    }

    function _intradaySortChip(key, label, title) {
        var active = _intradaySort === key;
        var bg = active ? '#34495e' : 'var(--bg-light,#f0f0f0)';
        var fg = active ? '#fff' : 'var(--text-2,#666)';
        return '<span onclick="intradaySetSort(\'' + key + '\')" title="' + title + '" style="cursor:pointer;user-select:none;font-size:12px;padding:2px 10px;border-radius:10px;background:' + bg + ';color:' + fg + ';white-space:nowrap;">' + label + '</span>';
    }

    function _intradayPnlCell(r) {
        // ① 当日浮动盈亏：现价×持仓数量 − 加权成本（holdings 账户无关聚合，
        // 与持仓列表端点 unrealized_pnl 公式同源）；红盈绿亏；缺失显式"—"
        var pnl = r.unrealized_pnl, pct = r.unrealized_pnl_pct;
        if (pnl == null && pct == null) return '<span style="color:#ccc;">—</span>';
        var main = pnl != null ? formatPnl(pnl) : '—';
        var pctTxt = pct != null ? ' <span style="font-size:10.5px;">(' + (pct > 0 ? '+' : '') + pct.toFixed(2) + '%)</span>' : '';
        var tip = '现价×持仓数量 − 加权成本（holdings 聚合，与持仓页同源）';
        if (r.avg_cost != null && r.total_qty) {
            tip = '成本 ' + r.avg_cost.toFixed(2) + ' × ' + r.total_qty.toLocaleString() + ' 股；' + tip;
        }
        var color = pnlColor(pnl != null ? pnl : pct);
        return '<span style="font-weight:600;color:' + color + ';white-space:nowrap;" title="' + escapeHtml(tip) + '">' + main + pctTxt + '</span>';
    }

    function _intradayActionCell(r) {
        // ③ 行动作指引：操盘手矩阵 held_rows 首行动作文（如「止损·56.16（已触发）」）。
        // stored（日报预计算）为收盘口径；行内盘中状态以本行状态徽标为准——
        // 盘中破线但矩阵未标已触发时两者并列、互不覆盖。
        var t = r.top_action;
        if (!t) return '<span style="color:#ccc;">—</span>';
        var triggered = String(t).indexOf('已触发') >= 0;
        var color = triggered ? '#c0392b' : (r.state === 'near_stop' ? '#e65100' : 'var(--text-2,#555)');
        var weight = triggered ? '700' : '600';
        var srcTip = (r.top_action_source === 'live')
            ? '操盘手矩阵首行动作（最新报告缺摘要，读取时只读现算）'
            : '操盘手矩阵首行动作（最新日报预计算，收盘口径；盘中状态见左列）';
        return '<span style="font-size:11px;font-weight:' + weight + ';color:' + color + ';white-space:nowrap;" title="' +
            escapeHtml(srcTip + '：' + t) + '">' + (triggered ? '🎯 ' : '') + escapeHtml(String(t)) + '</span>';
    }

    function _intradayClockInSession() {
        // 本地时钟粗判 A 股交易时段（工作日 09:15-11:35 / 12:55-15:05，边界含缓冲）；
        // 节假日盲区与后端一致（020R-59 已知边界）：误判时本卡仅做零网络内存读，无害
        var n = new Date();
        var dow = n.getDay();
        if (dow === 0 || dow === 6) return false;
        var hm = n.getHours() * 100 + n.getMinutes();
        return (hm >= 915 && hm <= 1135) || (hm >= 1255 && hm <= 1505);
    }

    function _intradayAutoSessionActive() {
        // 服务端 session 判定（权威）优先；本地时钟兜底（冷启动/快照陈旧时仍可启动）
        if (_intradayAutoLastData && _intradayAutoLastData.session &&
                _intradayAutoLastData.session.in_session === true) {
            return true;
        }
        return _intradayClockInSession();
    }

    function _intradayAutoStatusText(txt) {
        var el = document.getElementById('intradayAutoStatus');
        if (el) el.textContent = txt;
    }

    function intradayToggleAuto(on) {
        // ④ 交易时段自动刷新开关：默认关；开启后每 90s 一跳，仅交易时段生效，
        // 休市自动停（不请求，状态行标注）。只 GET 重读快照（内存读零网络零写库），
        // 不触发行情请求——实时取价仍由后端巡检按其间隔执行（021BN-c 数据源风控）。
        _intradayAutoOn = !!on;
        if (_intradayAutoTimer) { clearInterval(_intradayAutoTimer); _intradayAutoTimer = null; }
        if (!_intradayAutoOn) {
            _intradayAutoStatusText('已关闭');
            return;
        }
        _intradayAutoTimer = setInterval(intradayAutoTick, INTRADAY_AUTO_INTERVAL_SEC * 1000);
        intradayAutoTick(); // 开启即先刷一轮
    }

    function intradayAutoTick() {
        if (!_intradayAutoOn) return;
        if (!document.getElementById('intradayCard')) return; // 不在看板视图：零请求
        if (!_intradayAutoSessionActive()) {
            _intradayAutoStatusText('休市 · 已暂停');
            return;
        }
        _intradayAutoStatusText('刷新中...');
        Promise.all([
            fetch('/api/dashboard/intraday', {cache: 'no-store'}).then(function(x) { return safeJson(x); }).catch(function() { return null; }),
            fetch('/api/dashboard/action-list', {cache: 'no-store'}).then(function(x) { return safeJson(x); }).catch(function() { return null; })
        ]).then(function(pair) {
            if (!_intradayAutoOn) return; // 轮询期间被用户关闭
            var intraday = (pair[0] && pair[0].success) ? pair[0] : null;
            var actionList = (pair[1] && pair[1].success) ? pair[1] : null;
            var cardEl = document.getElementById('intradayCard');
            if (cardEl && intraday) cardEl.outerHTML = renderIntradayCard(intraday);
            var alEl = document.getElementById('actionListCard');
            if (alEl && actionList) alEl.outerHTML = renderActionListCard(actionList);
            if (_dashData) {
                if (intraday) { _dashData.intraday = intraday; _intradayAutoLastData = intraday; }
                if (actionList) _dashData.actionList = actionList;
            }
            _intradayAutoStatusText('开启 · 每' + INTRADAY_AUTO_INTERVAL_SEC + '秒（休市自动停）');
        }).catch(function(err) {
            console.error('[intradayAutoTick] 网络错误:', err);
            _intradayAutoStatusText('上次刷新失败 · 将自动重试');
        });
    }

    function _intradayTodayTradesLine(d) {
        // ⑤ 当日已录流水概览行（后端 trade_records 只读聚合，金额不含费）
        var tt = d.today_trades;
        if (tt && tt.total_count > 0) {
            return '<div style="margin-top:8px;font-size:12.5px;color:var(--text-2,#555);">🧾 今日已录流水：买 <b>' +
                tt.buy_count + '</b> 笔 ' + formatCNY(tt.buy_amount) + ' · 卖 <b>' + tt.sell_count +
                '</b> 笔 ' + formatCNY(tt.sell_amount) +
                ' <span style="color:var(--text-3,#999);font-size:11.5px;">（成交金额不含费用，完整流水见「交易流水」页）</span></div>';
        }
        return '<div style="margin-top:8px;font-size:12.5px;color:var(--text-3,#999);">🧾 今日暂无已录流水</div>';
    }

    function _intradayStateBadge(r) {
        // 状态徽标（A股惯例：风控=绿系、逼近=琥珀、正常=灰、无参考=浅灰）
        if (r.state === 'below_stop') return '<span style="background:#e8f5e9;color:#1b5e20;font-size:11px;font-weight:700;padding:2px 8px;border-radius:10px;white-space:nowrap;">🛑 触及止损</span>';
        if (r.state === 'near_stop') return '<span style="background:#fff3e0;color:#e65100;font-size:11px;font-weight:600;padding:2px 8px;border-radius:10px;white-space:nowrap;">⚠ 逼近止损</span>';
        if (r.state === 'no_data') return '<span style="background:#f5f5f5;color:#999;font-size:11px;padding:2px 8px;border-radius:10px;white-space:nowrap;">无数据</span>';
        if (r.state === 'unknown') return '<span style="background:#eee;color:#888;font-size:11px;padding:2px 8px;border-radius:10px;white-space:nowrap;">无止损参考</span>';
        return '<span style="background:#f5f5f5;color:#888;font-size:11px;padding:2px 8px;border-radius:10px;white-space:nowrap;">正常</span>';
    }

    function _intradayPctCell(v, opts) {
        // 百分比单元格：红涨绿跌；opts.bold 距止损破线时加粗
        if (v == null || isNaN(v)) return '<span style="color:#ccc;">—</span>';
        var color = pnlColor(v);
        var txt = (v > 0 ? '+' : '') + v.toFixed(2) + '%';
        var weight = opts && opts.bold ? 'font-weight:700;' : '';
        return '<span style="color:' + color + ';' + weight + '">' + txt + '</span>';
    }

    function _intradaySignalBadges(r) {
        // 今日信号标记（后端离线复算 overlay；买点蓝系/卖点绿系，与行动清单徽标同族）
        var labels = r.signal_labels || [];
        if (!labels.length) return '<span style="color:#ccc;">—</span>';
        return labels.map(function(s) {
            var buy = s.side === 'buy';
            var bg = buy ? '#e3f2fd' : '#e8f5e9';
            var fg = buy ? '#1565c0' : '#2e7d32';
            var icon = buy ? '🔵' : '🟢';
            return '<span style="background:' + bg + ';color:' + fg + ';font-size:10.5px;font-weight:600;padding:1px 6px;border-radius:8px;margin-right:4px;white-space:nowrap;" title="' +
                (buy ? '买点' : '卖点') + '信号·最新K线日(' + escapeHtml(s.date || '') + ')">' + icon + ' ' + escapeHtml(s.label || '') + '</span>';
        }).join('');
    }

    function renderIntradayCard(d) {
        var sessionBadge;
        if (d && d.session) {
            sessionBadge = d.session.in_session
                ? '<span style="background:#e8f5e9;color:#2e7d32;font-size:12px;font-weight:600;padding:2px 10px;border-radius:10px;margin-left:8px;">🟢 交易时段</span>'
                : '<span style="background:#eee;color:#888;font-size:12px;font-weight:600;padding:2px 10px;border-radius:10px;margin-left:8px;">⚪ 休市</span>';
        } else {
            sessionBadge = '';
        }
        var degradedNote = (d && d.degraded)
            ? '<span style="color:#e65100;font-size:12px;margin-left:8px;" title="' + escapeHtml(d.degrade_note || '') + '">🟡 数据源暂不可达，显示缓存价</span>'
            : '';
        var patrolNote = '';
        if (d && d.patrol) {
            if (d.patrol.enabled === false) {
                patrolNote = '<span style="color:#888;font-size:12px;margin-left:8px;">⏸ 巡检已停用</span>';
            } else if (d.patrol.paused) {
                patrolNote = '<span style="color:#e65100;font-size:12px;margin-left:8px;" title="数据源连续失败/疑似节假日保护，下一交易时段自动恢复">⏸ 巡检暂停中</span>';
            }
        }
        // ④ 自动刷新开关（默认关；仅交易时段轮询，休市自动停）
        var autoToggle = '';
        if (d) {
            autoToggle = '<label style="font-size:12px;color:var(--text-2,#666);cursor:pointer;font-weight:normal;user-select:none;display:inline-flex;align-items:center;gap:4px;" title="开启后每90秒自动重读盘中快照（仅交易时段生效，休市自动暂停）。只重读快照，不触发行情请求——实时取价仍由后端巡检按其间隔执行。">' +
                '<input type="checkbox" id="intradayAutoChk" style="vertical-align:middle;cursor:pointer;"' + (_intradayAutoOn ? ' checked' : '') + ' onchange="intradayToggleAuto(this.checked)"> 自动刷新</label>' +
                '<span id="intradayAutoStatus" style="font-size:11.5px;color:var(--text-3,#999);margin-left:2px;">' + (_intradayAutoOn ? '开启 · 每' + INTRADAY_AUTO_INTERVAL_SEC + '秒' : '已关闭') + '</span>';
        }
        var html = '<div class="card" id="intradayCard" style="margin-bottom:20px;">';
        html += '<div class="card-title" style="display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:8px;">';
        html += '<span>⏱ 盘中速览' + sessionBadge + degradedNote + patrolNote + '</span>';
        html += '<span style="font-weight:normal;display:flex;align-items:center;gap:10px;flex-wrap:wrap;">';
        html += autoToggle;
        if (d && d.updated_at) {
            html += '<span style="color:var(--text-3,#888);font-size:12px;">快照 ' + escapeHtml(String(d.updated_at)) + '</span>';
        }
        html += '<button class="btn btn-sm" id="intradayRefreshBtn" onclick="intradayManualRefresh()" title="立即巡检一轮持仓股实时快照（仅持仓，60s 冷却）">🔄 盘中刷新</button>';
        html += '</span></div>';
        html += '<div id="intradayCardBody">';
        if (!d) {
            html += '<div style="padding:10px 0;font-size:13px;color:var(--text-3,#999);">盘中速览暂不可用（巡检模块未响应），刷新页面重试。</div>';
        } else if (!d.stocks || !d.stocks.length) {
            html += '<div style="padding:10px 0;font-size:13px;color:var(--text-3,#999);">当前无持仓——盘中速览仅覆盖持仓股。</div>';
        } else {
            if (d.session && !d.session.in_session) {
                html += '<div style="margin-bottom:8px;font-size:12.5px;color:var(--text-3,#888);">⚪ 当前非交易时段，以下为最近快照（价格可能为缓存/收盘口径）——收盘确认判定以批量评分表与个股报告为准。</div>';
            }
            // ② 排序切换 chips（纯前端排序；距止损默认——风控优先）
            html += '<div style="display:flex;flex-wrap:wrap;gap:6px;align-items:center;margin-bottom:6px;">';
            html += '<span style="font-size:12px;color:var(--text-3,#888);">排序：</span>';
            html += _intradaySortChip('stop', '距止损', '距止损%升序：已破线/最接近止损的排前（无止损参考殿后）');
            html += _intradaySortChip('pnl', '当日盈亏', '当日浮动盈亏%升序：亏损最大的排前');
            html += _intradaySortChip('pct', '涨跌幅', '盘中涨跌幅%升序：跌幅最大的排前');
            html += '</div>';
            var rows = _intradaySortRows(d.stocks || [], _intradaySort);
            html += '<table style="width:100%;border-collapse:collapse;font-size:12.5px;">';
            html += '<tr style="color:var(--text-3,#888);text-align:left;"><th style="padding:4px 8px 4px 0;font-weight:normal;">股票</th>' +
                    '<th style="font-weight:normal;text-align:right;">现价</th><th style="font-weight:normal;text-align:right;">涨跌%</th>' +
                    '<th style="font-weight:normal;text-align:right;" title="现价×持仓数量 − 加权成本（holdings 聚合，与持仓页同源）；红盈绿亏">当日盈亏</th>' +
                    '<th style="font-weight:normal;text-align:right;" title="现价相对有效止损（成本线与建议止损取高者）的百分比，负值=已低于止损线">距止损</th>' +
                    '<th style="font-weight:normal;text-align:right;" title="现价相对20日均线（收盘口径）的百分比">距MA20</th>' +
                    '<th style="font-weight:normal;text-align:left;">今日信号</th><th style="font-weight:normal;text-align:left;">状态</th>' +
                    '<th style="font-weight:normal;text-align:left;" title="操盘手操作矩阵首行动作文（收盘口径）；盘中破线以状态列为准">行动指引</th></tr>';
            rows.forEach(function(r) {
                var isBelow = r.state === 'below_stop';
                var rowBg = isBelow ? 'background:#fdecea;' : '';
                html += '<tr style="' + rowBg + 'border-bottom:1px solid var(--border-light,#f0f0f0);cursor:pointer;" onclick="viewReport(' + r.stock_id + ')">';
                html += '<td style="padding:5px 8px 5px 0;white-space:nowrap;"><b>' + escapeHtml(r.name || '') + '</b> <span style="color:var(--text-3,#888);font-size:11.5px;">' + escapeHtml(r.symbol || '') + '</span></td>';
                var priceTxt = (r.price != null && !isNaN(r.price)) ? r.price.toFixed(2) : '—';
                var asOfTxt = r.as_of ? ' <span style="color:var(--text-3,#aaa);font-size:10.5px;">' + escapeHtml(r.as_of) + '</span>' : '';
                var noteTip = r.note ? ' title="' + escapeHtml(r.note) + '"' : '';
                html += '<td style="text-align:right;white-space:nowrap;"' + noteTip + '>' + priceTxt + asOfTxt + (r.quote_ok ? '' : ' <span style="color:#bbb;font-size:10.5px;">缓存</span>') + '</td>';
                html += '<td style="text-align:right;">' + _intradayPctCell(r.pct_change) + '</td>';
                // ① 当日浮动盈亏（现价×quantity 对加权成本；与距止损列并列）
                html += '<td style="text-align:right;">' + _intradayPnlCell(r) + '</td>';
                // 距止损：负值（已低于止损线）红色加粗警示；逼近带内琥珀。
                // 021BT 终验 F2：逼近高亮消费端点 state 字段（后端按 config 阈值判定），
                // 前端不自行计算 1%——阈值调整（config.INTRADAY_NEAR_STOP_PCT）单点生效。
                var stopCell;
                if (r.stop_line == null) {
                    stopCell = '<span style="color:#bbb;" title="无有效止损参考（无成本与建议止损）">无止损</span>';
                } else {
                    var dv = r.distance_pct;
                    var near = r.state === 'near_stop';
                    stopCell = _intradayPctCell(dv, { bold: isBelow });
                    if (near) stopCell = '<span style="color:#e65100;font-weight:600;">' + (dv > 0 ? '+' : '') + dv.toFixed(2) + '%</span>';
                    if (isBelow) stopCell = '<span style="color:#c0392b;font-weight:700;">' + (dv > 0 ? '+' : '') + dv.toFixed(2) + '%</span>';
                }
                html += '<td style="text-align:right;white-space:nowrap;">' + stopCell + '</td>';
                html += '<td style="text-align:right;">' + _intradayPctCell(r.ma20_distance_pct) + '</td>';
                html += '<td style="padding:5px 8px 5px 8px;white-space:nowrap;">' + _intradaySignalBadges(r) + '</td>';
                html += '<td style="padding:5px 4px 5px 0;">' + _intradayStateBadge(r) + '</td>';
                // ③ 行动作指引（操盘手矩阵首行动作文，如「止损·56.16（已触发）」）
                html += '<td style="padding:5px 0 5px 8px;max-width:200px;overflow:hidden;text-overflow:ellipsis;">' + _intradayActionCell(r) + '</td>';
                html += '</tr>';
            });
            html += '</table>';
            var c = d.counts || {};
            if (c.alerts > 0) {
                html += '<div style="margin-top:8px;font-size:12.5px;color:#c0392b;font-weight:600;">🛑 ' + c.alerts + ' 只持仓存在盘中提醒（触线/逼近/异动），详见行动清单置顶项。</div>';
            }
            // ⑤ 当日已录流水概览行
            html += _intradayTodayTradesLine(d);
        }
        html += '</div>';
        var intervalMin = (d && d.patrol && d.patrol.interval_min) ? d.patrol.interval_min : 5;
        html += '<div style="margin-top:8px;font-size:12px;color:var(--text-3,#aaa);">⏱ 定时巡检每 ' + intervalMin +
            ' 分钟（仅交易时段，数据写入价格缓存）· ' + INTRADAY_DISCLAIMER + '。</div>';
        html += '</div>';
        return html;
    }

    function intradayManualRefresh() {
        // 一键刷新（t3 ②）：后端巡检一轮（仅持仓）→ 原位重渲染速览卡 + 行动清单卡。
        // 节流双层：前端 60s 记忆 + 后端冷却权威（cooldown>0 时提示稍候）。
        if (_intradayRefreshing) return;
        var now = Date.now();
        if (now - _intradayLastRefreshAt < 60000) {
            var remain = Math.ceil((60000 - (now - _intradayLastRefreshAt)) / 1000);
            _showToast('刷新过于频繁，请稍候（' + remain + 's 后可再次刷新）');
            return;
        }
        var btn = document.getElementById('intradayRefreshBtn');
        if (btn) { btn.disabled = true; btn.textContent = '🔄 巡检中...'; }
        _intradayRefreshing = true;
        _intradayLastRefreshAt = Date.now();  // 无论成败都计时，防连点轰炸
        fetch('/api/dashboard/intraday/refresh', { method: 'POST', cache: 'no-store' })
            .then(function(r) { return safeJson(r); })
            .then(function(r) {
                if (!r || !r.success) {
                    _showToast((r && r.message) ? '刷新失败：' + r.message : '刷新失败');
                    return null;
                }
                if (r.ok === false && r.cooldown > 0) {
                    _showToast('冷却中（' + r.cooldown + 's 后可再次刷新）');
                    return null;
                }
                if (r.ok === false) {
                    // 非交易时段/巡检停用/连败暂停：明确提示（静默不阻塞页面）
                    _showToast(r.reason || '当前无法刷新');
                    return null;
                }
                // 刷新成功：并行重拉速览 + 行动清单，两卡原位刷新（不动整页）
                _showToast('已刷新持仓盘中快照');
                return Promise.all([
                    fetch('/api/dashboard/intraday', {cache: 'no-store'}).then(function(x) { return safeJson(x); }).catch(function() { return null; }),
                    fetch('/api/dashboard/action-list', {cache: 'no-store'}).then(function(x) { return safeJson(x); }).catch(function() { return null; })
                ]);
            })
            .then(function(pair) {
                if (!pair) return;
                var intraday = (pair[0] && pair[0].success) ? pair[0] : null;
                var actionList = (pair[1] && pair[1].success) ? pair[1] : null;
                var cardEl = document.getElementById('intradayCard');
                if (cardEl && intraday) cardEl.outerHTML = renderIntradayCard(intraday);
                var alEl = document.getElementById('actionListCard');
                if (alEl && actionList) alEl.outerHTML = renderActionListCard(actionList);
                if (_dashData) {
                    if (intraday) { _dashData.intraday = intraday; _intradayAutoLastData = intraday; }
                    if (actionList) _dashData.actionList = actionList;
                }
            })
            .catch(function(err) {
                console.error('[intradayManualRefresh] 网络错误:', err);
                _showToast('刷新失败：网络错误');
            })
            .finally(function() {
                _intradayRefreshing = false;
                var b2 = document.getElementById('intradayRefreshBtn');
                if (b2) { b2.disabled = false; b2.textContent = '🔄 盘中刷新'; }
            });
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

                // 2026-09-18：操盘手阶段×评级分歧标记（日报预计算，零重算读取）
                // 021BQ：增量 top_action（操作矩阵首行动作摘要；无分歧时以中性 chip 展示）
                var traderStr = '';
                var trader = st.trader_signal;
                if (trader && trader.has_disagreement) {
                    var tName = trader.stage_name || '';
                    var tTip = (trader.disagreement_text || '评级与操盘手阶段判定存在分歧，主指令仍以评级为准') +
                        (trader.top_action ? '；当前动作：' + trader.top_action : '') +
                        '（阶段：' + tName + '；来自最新报告 ' + (st.report_date || '—') + '，点击查看完整操盘手建议）';
                    traderStr = '<span style="font-size:11px;color:#8a6d00;background:#fff3cd;padding:2px 7px;' +
                        'border-radius:10px;white-space:nowrap;cursor:help;" title="' +
                        tTip.replace(/"/g, '&quot;') + '">⚡' + tName + '·分歧</span>';
                } else if (trader && trader.top_action) {
                    var taTip = '操盘手操作矩阵首行动作（触发条件与价位见个股页操盘手卡；来自最新报告 ' +
                        (st.report_date || '—') + '）';
                    traderStr = '<span style="font-size:11px;color:#1a3c6e;background:#eef4fb;padding:2px 7px;' +
                        'border-radius:10px;white-space:nowrap;cursor:help;" title="' +
                        taTip.replace(/"/g, '&quot;') + '">🎯' +
                        String(trader.top_action || '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;') + '</span>';
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
                    traderStr +
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
