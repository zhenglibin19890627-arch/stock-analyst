// OPT-4（2026-09-07）：自 app.js 按业务域拆分（纯搬移）；加载顺序见 templates/index.html，core 必须最先。

    // ============================================================
    // P3-B: 智能预警铃铛（架构师R4：页面可见时轮询 60s，隐藏时停止）
    // ============================================================
    var _alertPollTimer = null;
    var _alertTypeLabels = {
        'rating_change': '评级变动',
        'score_below': '评分跌破',
        'capital_outflow': '资金流出',
        'tech_signal': '买点信号',
        'sell_signal': '卖点信号'
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
        'capital_outflow': '资金流出',
        'tech_signal': '买点信号',
        'sell_signal': '卖点信号'
    };
    var _alertRuleTypeHints = {
        'rating_change': { label: '阈值', show: false, hint: '评级变动无需设置阈值，评级发生升降级时自动提醒' },
        'score_below': { label: '跌破阈值（0-100）', show: true, hint: '当综合评分跌破此值时提醒，建议设 40-70' },
        'capital_outflow': { label: '流出金额（万元）', show: true, hint: '当主力净流出超过此金额时提醒，建议设 500-5000' },
        'tech_signal': { label: '阈值', show: false, hint: '自选股当日出现 MACD/KDJ 买点信号时提醒，并标注共振组合星级；每日收盘后自动巡检，无需设置阈值' },
        'sell_signal': { label: '阈值', show: false, hint: '自选股当日出现 MACD/KDJ 死叉、破位MA20 等卖点信号时提醒，并标注共振组合星级；每日收盘后自动巡检，无需设置阈值' }
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
