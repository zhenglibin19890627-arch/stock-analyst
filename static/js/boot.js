// OPT-4（2026-09-07）：自 app.js 按业务域拆分（纯搬移）；加载顺序见 templates/index.html，core 必须最先。

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
