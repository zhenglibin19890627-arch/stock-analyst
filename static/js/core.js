// OPT-4（2026-09-07）：自 app.js 按业务域拆分（纯搬移）；加载顺序见 templates/index.html，core 必须最先。

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

    /**
     * 021BP 项4：通用分批顺序驱动器（自 market.js msStartSignals 的 chunk 模式泛化，
     * 公共逻辑收口本文件——批量分析 watchlist.batchAnalyze（≤100 只自动拆 5×20）
     * 与后续同型需求（t5）复用，不复制两份）。
     *
     * 顺序执行（无并发）：单批完成才发下一批，与后端"单写者/采集限频"约束匹配。
     *
     * @param {Array} items 全量条目
     * @param {number} chunkSize 单批大小（= 后端单次上限，如 20=BATCH_OPERATION_LIMIT，
     *        R16 风控阈值——拆批只发生在前端多次调用，单次仍 ≤20，合规）
     * @param {function(Array, number, number): Promise} runChunk 单批执行器
     *        (chunkItems, chunkIndex, chunkCount) → Promise；批内失败请自行降级为
     *        失败记录返回，不要让 Promise 进入 rejected（驱动器会吞掉并继续下一批）
     * @param {function(number, number): void} [onProgress] (doneChunks, chunkCount)
     *        每批开始前回调（doneChunks=已完成批数；最后一轮 doneChunks=chunkCount，
     *        可用于把进度刷到 100%）
     * @returns {Promise<number>} 完成的批数（恒等于 ceil(items.length / chunkSize)）
     */
    function runChunked(items, chunkSize, runChunk, onProgress) {
        var chunks = [];
        for (var i = 0; i < items.length; i += chunkSize) {
            chunks.push(items.slice(i, i + chunkSize));
        }
        var done = 0;
        return new Promise(function(resolve) {
            var runNext = function() {
                if (typeof onProgress === 'function') onProgress(done, chunks.length);
                if (done >= chunks.length) { resolve(done); return; }
                var idx = done;
                Promise.resolve()
                    .then(function() { return runChunk(chunks[idx], idx, chunks.length); })
                    .catch(function() {})   // 单批异常不阻塞后续批次（与 msStartSignals 同语义）
                    .then(function() {
                        done++;
                        runNext();
                    });
            };
            runNext();
        });
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
