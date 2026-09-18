// OPT-4（2026-09-07）：自 app.js 按业务域拆分（纯搬移）；加载顺序见 templates/index.html，core 必须最先。

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

                // 2026-09-18（回测提升①）：分档×位置矩阵 + 避损口径（条件化使用评级的数据基础）
                var pm = rpt.position_matrix, bands = rpt.position_bands, dr = rpt.drawdown_risk;
                if (pm && bands && Object.keys(pm).length) {
                    html += '<h4 style="margin:16px 0 8px;">分档 × 位置矩阵 <span style="font-size:12px;color:var(--text-3,#888);font-weight:normal;">' +
                        '（动态口径；评级发出时个股在近60日高低区间的位置——同档评级在不同位置的胜率不同，这是「条件化使用」的依据）</span></h4>';
                    html += '<table class="bt-report-table" style="font-size:13px;"><thead><tr style="background:#f0f7ff;"><th style="padding:8px;text-align:left;">评级</th>';
                    bands.forEach(function(b) { html += '<th style="padding:8px;text-align:center;">' + b.label + '</th>'; });
                    html += '</tr></thead><tbody>';
                    Object.keys(pm).sort(function(a, b) { return ratingOrder.indexOf(a) - ratingOrder.indexOf(b); }).forEach(function(rating) {
                        html += '<tr style="border-bottom:1px solid var(--border-light,#eee);"><td style="padding:8px;"><span class="rating-badge ' + getRatingClass(rating) + '">' + rating + '</span></td>';
                        bands.forEach(function(b) {
                            var cell = (pm[rating] || {})[b.key];
                            if (!cell || !cell.total) { html += '<td style="padding:8px;text-align:center;color:#bbb;">—</td>'; return; }
                            var acc = Math.round(cell.accuracy * 100);
                            var col = acc >= 60 ? '#27ae60' : acc >= 45 ? '#f39c12' : '#e74c3c';
                            html += '<td style="padding:8px;text-align:center;font-weight:700;color:' + col + ';" title="正确 ' + cell.correct + ' / 共 ' + cell.total + '">' + acc + '%<span style="font-weight:400;color:var(--text-3,#999);"> (' + cell.total + ')</span></td>';
                        });
                        html += '</tr>';
                    });
                    html += '</tbody></table>';
                    html += '<div style="margin-top:6px;font-size:12px;color:var(--text-3,#999);">子样本≥10 才有参考意义；分化显著时：同档评级在低位/高位的胜率差异即「条件化使用」空间。</div>';
                }
                if (dr && (dr.risk || {}).n >= 20) {
                    html += '<div style="margin-top:10px;padding:8px 12px;background:var(--surface-alt,#f8f9fa);border-radius:6px;font-size:12.5px;color:var(--text-2,#555);line-height:1.7;">' +
                        '<b>避损口径</b>（评级后20日最大回撤）：减仓/卖出档 平均 ' + dr.risk.mean + '% / 中位 ' + dr.risk.median + '% / 最深 ' + dr.risk.worst + '%（' + dr.risk.n + '条）；' +
                        '其他档 平均 ' + dr.other.mean + '% / 中位 ' + dr.other.median + '%（' + dr.other.n + '条）。</div>';
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
