"""蓝图聚合注册。"""

from blueprints.alerts import bp as alerts_bp
from blueprints.analysis import bp as analysis_bp
from blueprints.backtest import bp as backtest_bp
from blueprints.export import bp as export_bp
from blueprints.index_ratings import bp as index_ratings_bp
from blueprints.market import bp as market_bp
from blueprints.portfolio import bp as portfolio_bp
from blueprints.report import bp as report_bp
from blueprints.system import bp as system_bp
from blueprints.watchlist import bp as watchlist_bp

ALL_BLUEPRINTS = [
    alerts_bp, analysis_bp, backtest_bp, export_bp, index_ratings_bp,
    market_bp, portfolio_bp, report_bp, system_bp, watchlist_bp,
]
