// OPT-4（2026-09-07）：自 app.js 按业务域拆分（纯搬移）；加载顺序见 templates/index.html，core 必须最先。

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
