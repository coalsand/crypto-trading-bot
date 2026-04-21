"""Flask web application for the Crypto Trading Bot."""

import json
import threading
from datetime import datetime, timedelta
from functools import wraps

from flask import Flask, render_template, jsonify, request, Response

from ..config import settings, TRADEABLE_COINS, SUPPORTED_COINS
from ..storage import db, TradeStatus, SignalType
from ..data import market_data_fetcher, stock_data, stock_screener
from ..analysis import technical_analyzer, sentiment_analyzer
from ..strategy import signal_generator, portfolio_tracker, risk_manager
from ..execution import paper_trader, paper_order_manager
from ..utils import setup_logging, get_logger

# Initialize logging
logger = get_logger("web")

# Create Flask app
app = Flask(__name__)
app.config["SECRET_KEY"] = "crypto-trading-bot-secret-key"

# Bot state
bot_state = {
    "running": False,
    "last_cycle": None,
    "cycle_count": 0,
    "errors": []
}

# Background thread for bot
bot_thread = None


def run_bot_cycle():
    """Run a single bot cycle in background."""
    global bot_state

    try:
        bot_state["running"] = True
        logger.info("Running trading cycle from web UI...")

        # Fetch market data
        market_data = market_data_fetcher.fetch_all_coins_ohlcv(timeframe="1h", limit=500)
        current_prices = market_data_fetcher.get_current_prices()

        if not market_data or not current_prices:
            raise Exception("Failed to fetch market data")

        # Update portfolio prices
        paper_trader.update_portfolio_prices(current_prices)

        # Check and close positions
        paper_trader.check_and_close_positions(current_prices)

        # Generate signals
        signals = signal_generator.generate_all_signals(market_data, sentiment_hours=24)

        # Save all signals to database
        signal_generator.save_signals(signals)

        # Execute actionable signals
        actionable = signal_generator.get_actionable_signals(signals)
        for signal in actionable:
            paper_trader.execute_signal(signal, current_prices)

        # Save portfolio
        paper_trader.save_portfolio_snapshot()

        bot_state["last_cycle"] = datetime.utcnow().isoformat()
        bot_state["cycle_count"] += 1

        logger.info("Trading cycle completed successfully")

    except Exception as e:
        logger.error(f"Error in trading cycle: {e}")
        bot_state["errors"].append({
            "time": datetime.utcnow().isoformat(),
            "message": str(e)
        })
        # Keep only last 10 errors
        bot_state["errors"] = bot_state["errors"][-10:]

    finally:
        bot_state["running"] = False


# ============================================
# Page Routes
# ============================================

@app.route("/")
def dashboard():
    """Main dashboard page."""
    return render_template("dashboard.html")


@app.route("/positions")
def positions_page():
    """Positions page."""
    return render_template("positions.html")


@app.route("/signals")
def signals_page():
    """Signals page."""
    return render_template("signals.html")


@app.route("/history")
def history_page():
    """Trade history page."""
    return render_template("history.html")


@app.route("/settings")
def settings_page():
    """Settings page."""
    return render_template("settings.html")


@app.route("/stocks")
def stocks_page():
    """Stock watchlist page."""
    return render_template("stocks.html")


@app.route("/crypto")
def crypto_page():
    """Crypto markets page."""
    return render_template("crypto.html")


@app.route("/performance")
def performance_page():
    """Performance vs benchmarks."""
    return render_template("performance.html")


# ============================================
# API Routes
# ============================================

@app.route("/api/status")
def api_status():
    """
    Bot status. The 'cycle_count' and 'last_cycle' are derived from portfolio snapshots
    in the DB (written by the scheduler each cycle) so they reflect actual backend
    activity — not just the web process's local counter.
    """
    # Real cycle data comes from the portfolio-snapshot table written by the scheduler
    scheduler_cycles_total = 0
    scheduler_last_cycle = None
    try:
        from ..storage.models import Portfolio
        from sqlalchemy import func
        with db.get_session() as session:
            q = session.query(func.count(Portfolio.id), func.max(Portfolio.timestamp)).filter(
                Portfolio.is_paper == True
            ).one()
            scheduler_cycles_total = int(q[0] or 0)
            scheduler_last_cycle = q[1].isoformat() if q[1] else None
    except Exception as e:
        logger.warning(f"status: could not read scheduler cycle count: {e}")

    return jsonify({
        "running": bot_state["running"],
        "last_cycle": scheduler_last_cycle or bot_state["last_cycle"],
        "cycle_count": scheduler_cycles_total,
        "web_manual_cycles": bot_state["cycle_count"],
        "paper_mode": settings.paper_trading,
        "errors": bot_state["errors"][-5:]
    })


@app.route("/api/portfolio")
def api_portfolio():
    """Get portfolio summary."""
    try:
        summary = paper_trader.get_portfolio_summary()
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/positions")
def api_positions():
    """Get open positions with current prices (crypto + stock)."""
    try:
        current_prices = market_data_fetcher.get_current_prices()
        # Merge in stock prices for any open stock positions
        open_trades = db.get_open_trades(is_paper=True)
        stock_syms = [t.symbol for t in open_trades if getattr(t, "asset_type", "crypto") == "stock"]
        if stock_syms:
            current_prices = {**current_prices, **stock_data.get_current_prices(stock_syms)}
        summary = paper_order_manager.get_open_positions_summary(current_prices)
        return jsonify(summary)
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/trades")
def api_trades():
    """Get recent trades."""
    try:
        days = request.args.get("days", 30, type=int)
        trades = paper_order_manager.get_trade_history(days=days)

        asset_filter = request.args.get("asset_type")  # "crypto" | "stock" | None
        trades_data = []
        for t in trades:
            if asset_filter and getattr(t, "asset_type", "crypto") != asset_filter:
                continue
            trades_data.append({
                "id": t.id,
                "symbol": t.symbol,
                "asset_type": getattr(t, "asset_type", "crypto"),
                "type": t.trade_type.value,
                "status": t.status.value,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "quantity": t.quantity,
                "pnl": t.realized_pnl,
                "pnl_pct": t.realized_pnl_pct,
                "opened_at": t.opened_at.isoformat() if t.opened_at else None,
                "closed_at": t.closed_at.isoformat() if t.closed_at else None
            })

        return jsonify({"trades": trades_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/signals")
def api_signals():
    """Get recent signals."""
    try:
        # Get latest signals from database
        with db.get_session() as session:
            from ..storage.models import Signal
            signals = session.query(Signal).order_by(
                Signal.timestamp.desc()
            ).limit(50).all()

            asset_filter = request.args.get("asset_type")
            signals_data = []
            for s in signals:
                s_asset = getattr(s, "asset_type", "crypto")
                if asset_filter and s_asset != asset_filter:
                    continue
                signals_data.append({
                    "id": s.id,
                    "symbol": s.symbol,
                    "asset_type": s_asset,
                    "type": s.signal_type.value,
                    "technical_score": s.technical_score,
                    "sentiment_score": s.sentiment_score,
                    "combined_score": s.combined_score,
                    "strength": s.strength,
                    "confidence": s.confidence,
                    "entry_price": s.entry_price,
                    "stop_loss": s.stop_loss,
                    "take_profit": s.take_profit,
                    "executed": s.executed,
                    "timestamp": s.timestamp.isoformat()
                })

        return jsonify({"signals": signals_data})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/prices")
def api_prices():
    """Get current prices for all coins."""
    try:
        prices = market_data_fetcher.get_current_prices()
        return jsonify({"prices": prices})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stocks/ohlcv/<symbol>")
def api_stocks_ohlcv(symbol: str):
    """Daily OHLCV bars for a stock ticker (default 30 days)."""
    try:
        period = request.args.get("period", "30d")
        interval = request.args.get("interval", "1d")
        df = stock_data.fetch_ohlcv(symbol, period=period, interval=interval)
        if df.empty:
            return jsonify({"symbol": symbol, "ohlcv": []})
        bars = [
            {
                "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                "open": float(row["open"]),
                "high": float(row["high"]),
                "low": float(row["low"]),
                "close": float(row["close"]),
                "volume": float(row["volume"]),
            }
            for ts, row in df.iterrows()
        ]
        return jsonify({"symbol": symbol, "ohlcv": bars})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/stocks/active")
def api_stocks_active():
    """
    Return the active stock watchlist with the latest signal per ticker.

    Derived from the DB: the most recent stock-type signal for each symbol in
    the last 48 hours. The scheduler refreshes this daily via the screener.
    """
    try:
        from ..storage.models import Signal
        from ..config.stocks import is_market_open
        from sqlalchemy import func
        from datetime import timedelta

        cutoff = datetime.utcnow() - timedelta(hours=48)
        with db.get_session() as session:
            subq = (
                session.query(Signal.symbol, func.max(Signal.timestamp).label("ts"))
                .filter(Signal.asset_type == "stock", Signal.timestamp >= cutoff)
                .group_by(Signal.symbol)
                .subquery()
            )
            rows = (
                session.query(Signal)
                .join(subq, (Signal.symbol == subq.c.symbol) & (Signal.timestamp == subq.c.ts))
                .filter(Signal.asset_type == "stock")
                .all()
            )
            symbols = [r.symbol for r in rows]
            prices = stock_data.get_current_prices(symbols) if symbols else {}

            name_map = stock_screener.get_name_map()
            rows_sorted = sorted(rows, key=lambda r: abs(r.combined_score or 0), reverse=True)
            active = [
                {
                    "symbol": r.symbol,
                    "name": name_map.get(r.symbol, ""),
                    "price": prices.get(r.symbol),
                    "signal_type": r.signal_type.value,
                    "technical_score": r.technical_score,
                    "combined_score": r.combined_score,
                    "entry_price": r.entry_price,
                    "stop_loss": r.stop_loss,
                    "take_profit": r.take_profit,
                    "last_signal_at": r.timestamp.isoformat(),
                }
                for r in rows_sorted
            ]
        return jsonify({"active": active, "market_open": is_market_open()})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/sentiment/<symbol>")
def api_sentiment(symbol):
    """Get sentiment data for a coin."""
    try:
        sentiment = sentiment_analyzer.get_aggregated_sentiment(symbol.upper(), hours=24)
        return jsonify({
            "symbol": symbol.upper(),
            "overall_score": sentiment.overall_score,
            "reddit_score": sentiment.reddit_score,
            "stocktwits_score": sentiment.stocktwits_score,
            "news_score": sentiment.news_score,
            "source_count": sentiment.source_count,
            "post_count": sentiment.post_count
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/technical/<symbol>")
def api_technical(symbol):
    """Get technical analysis for a coin."""
    try:
        coin = TRADEABLE_COINS.get(symbol.upper())
        if not coin:
            return jsonify({"error": "Unknown symbol"}), 404

        # Fetch latest data
        df = market_data_fetcher.fetch_ohlcv(coin.kraken_pair, timeframe="1h", limit=500)
        if df.empty:
            return jsonify({"error": "No data available"}), 404

        signals = technical_analyzer.analyze(df)

        return jsonify({
            "symbol": symbol.upper(),
            "current_price": signals.current_price,
            "rsi": signals.rsi,
            "rsi_signal": signals.rsi_signal,
            "macd": signals.macd,
            "macd_signal": signals.macd_signal,
            "bb_upper": signals.bb_upper,
            "bb_middle": signals.bb_middle,
            "bb_lower": signals.bb_lower,
            "bb_signal": signals.bb_signal,
            "ema_20": signals.ema_20,
            "ema_50": signals.ema_50,
            "ema_200": signals.ema_200,
            "ema_signal": signals.ema_signal,
            "atr": signals.atr,
            "overall_score": signals.overall_score
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/ohlcv/<symbol>")
def api_ohlcv(symbol: str):
    """Get OHLCV data for charting."""
    try:
        timeframe = request.args.get('timeframe', '1h')
        limit = int(request.args.get('limit', 100))

        coin_info = TRADEABLE_COINS.get(symbol)
        if not coin_info:
            return jsonify({"error": f"Unknown symbol: {symbol}"}), 404

        df = market_data_fetcher.fetch_ohlcv(
            coin_info.kraken_pair,
            timeframe=timeframe,
            limit=limit
        )

        if df.empty:
            return jsonify({"ohlcv": []})

        ohlcv_data = []
        for _, row in df.iterrows():
            ohlcv_data.append({
                "timestamp": row["timestamp"].isoformat(),
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"]
            })

        return jsonify({"ohlcv": ohlcv_data})

    except Exception as e:
        logger.error(f"Error fetching OHLCV for {symbol}: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/performance/series")
def api_performance_series():
    """
    Time-series of portfolio value vs. benchmarks, normalized to % return since start.

    Query params:
      days (int, default 30): lookback window
    """
    try:
        import pandas as pd
        from datetime import timedelta
        from ..data import stock_data

        days = int(request.args.get("days", 30))

        snapshots = db.get_portfolio_history(days=days, is_paper=True)
        if not snapshots:
            return jsonify({"portfolio": [], "benchmarks": {}, "days": days})

        # Build portfolio series: [{date, value}]
        port_series = [
            {"date": s.timestamp.strftime("%Y-%m-%d"), "value": float(s.total_value_usd)}
            for s in snapshots
        ]
        port_start = port_series[0]["value"] or 1.0

        # Fetch benchmark bars covering the same date span
        start_dt = snapshots[0].timestamp.date()
        end_dt = snapshots[-1].timestamp.date() + timedelta(days=1)
        period = f"{max(days + 5, 7)}d"

        benchmark_tickers = {"SPY": "SPY", "QQQ": "QQQ", "BTC": "BTC-USD"}
        benchmarks = {}
        raw = {}
        for label, ticker in benchmark_tickers.items():
            df = stock_data.fetch_ohlcv(ticker, period=period, interval="1d")
            if df is None or df.empty:
                continue
            df = df.copy()
            df.index = pd.to_datetime(df.index).tz_localize(None).normalize()
            df = df[(df.index.date >= start_dt) & (df.index.date <= end_dt)]
            if df.empty:
                continue
            raw[label] = df

        # Normalize each series to % return from its first value
        def to_pct(series_vals):
            base = series_vals[0]
            return [((v / base) - 1.0) * 100 for v in series_vals]

        portfolio_pct = [{"date": p["date"], "pct": v}
                         for p, v in zip(port_series, to_pct([p["value"] for p in port_series]))]

        for label, df in raw.items():
            benchmarks[label] = [
                {"date": idx.strftime("%Y-%m-%d"), "pct": pct}
                for idx, pct in zip(df.index, to_pct(df["close"].tolist()))
            ]

        # 60/40 blend: 60% SPY, 40% BTC, computed daily on common dates
        if "SPY" in raw and "BTC" in raw:
            common = raw["SPY"].index.intersection(raw["BTC"].index)
            if len(common) > 1:
                spy = raw["SPY"].loc[common, "close"].tolist()
                btc = raw["BTC"].loc[common, "close"].tolist()
                spy_pct = to_pct(spy)
                btc_pct = to_pct(btc)
                blend = [0.6 * s + 0.4 * b for s, b in zip(spy_pct, btc_pct)]
                benchmarks["60/40 SPY+BTC"] = [
                    {"date": idx.strftime("%Y-%m-%d"), "pct": v}
                    for idx, v in zip(common, blend)
                ]

        # Metrics (portfolio-only for v1)
        values = [p["value"] for p in port_series]
        total_return_pct = ((values[-1] / values[0]) - 1.0) * 100 if values[0] else 0.0
        years = max((snapshots[-1].timestamp - snapshots[0].timestamp).total_seconds() / (365.25 * 86400), 1e-9)
        cagr_pct = ((values[-1] / values[0]) ** (1.0 / years) - 1.0) * 100 if values[0] else 0.0

        # Max drawdown
        peak = values[0]
        max_dd = 0.0
        for v in values:
            if v > peak:
                peak = v
            dd = (v / peak - 1.0) * 100
            if dd < max_dd:
                max_dd = dd

        # Daily Sharpe (resample to daily, assume 4% risk-free)
        sharpe = None
        if len(values) > 2:
            s = pd.Series(values, index=pd.to_datetime([p["date"] for p in port_series]))
            daily = s.resample("1D").last().ffill().dropna()
            if len(daily) > 2:
                rets = daily.pct_change().dropna()
                if rets.std() and rets.std() > 0:
                    rf_daily = 0.04 / 252
                    sharpe = float((rets.mean() - rf_daily) / rets.std() * (252 ** 0.5))

        return jsonify({
            "days": days,
            "portfolio": portfolio_pct,
            "benchmarks": benchmarks,
            "metrics": {
                "total_return_pct": total_return_pct,
                "cagr_pct": cagr_pct,
                "max_drawdown_pct": max_dd,
                "sharpe": sharpe,
                "start_date": port_series[0]["date"],
                "end_date": port_series[-1]["date"],
                "start_value": port_series[0]["value"],
                "end_value": port_series[-1]["value"],
            },
        })
    except Exception as e:
        logger.exception(f"performance series error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/api/performance")
def api_performance():
    """Get performance metrics."""
    try:
        metrics = paper_order_manager.get_performance_summary(days=30)
        daily = paper_order_manager.get_daily_summary()

        return jsonify({
            "all_time": metrics,
            "today": daily
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/coins")
def api_coins():
    """Get list of supported coins."""
    coins = []
    for symbol, info in SUPPORTED_COINS.items():
        coins.append({
            "symbol": symbol,
            "name": info.name,
            "tradeable": symbol in TRADEABLE_COINS
        })
    return jsonify({"coins": coins})


@app.route("/api/settings")
def api_settings():
    """Get current settings."""
    return jsonify({
        "paper_trading": settings.paper_trading,
        "signal_check_interval": settings.scheduler.signal_check_interval_minutes,
        "sentiment_update_interval": settings.scheduler.sentiment_update_interval_minutes,
        "max_position_size_pct": settings.trading.max_position_size_pct * 100,
        "max_open_positions": settings.trading.max_open_positions,
        "stop_loss_atr_multiplier": settings.trading.stop_loss_atr_multiplier,
        "take_profit_ratio": settings.trading.take_profit_ratio,
        "technical_weight": settings.trading.technical_weight * 100,
        "sentiment_weight": settings.trading.sentiment_weight * 100,
        "rsi_oversold": settings.trading.rsi_oversold,
        "rsi_overbought": settings.trading.rsi_overbought,
        "buy_threshold": settings.trading.buy_signal_threshold,
        "sell_threshold": settings.trading.sell_signal_threshold,
        "min_confirmations": getattr(settings.trading, 'min_confirmations', 2)
    })


@app.route("/api/settings/thresholds", methods=["POST"])
def api_update_thresholds():
    """Update signal thresholds."""
    try:
        data = request.get_json()

        buy_threshold = float(data.get('buy_threshold', 0.3))
        sell_threshold = float(data.get('sell_threshold', -0.3))
        min_confirmations = int(data.get('min_confirmations', 2))

        # Validate
        if not (0 <= buy_threshold <= 1):
            return jsonify({"error": "Buy threshold must be between 0 and 1"}), 400
        if not (-1 <= sell_threshold <= 0):
            return jsonify({"error": "Sell threshold must be between -1 and 0"}), 400
        if not (1 <= min_confirmations <= 4):
            return jsonify({"error": "Confirmations must be between 1 and 4"}), 400

        # Update settings
        settings.trading.buy_signal_threshold = buy_threshold
        settings.trading.sell_signal_threshold = sell_threshold
        settings.trading.min_confirmations = min_confirmations

        logger.info(f"Updated thresholds: buy={buy_threshold}, sell={sell_threshold}, confirmations={min_confirmations}")

        return jsonify({
            "success": True,
            "buy_threshold": buy_threshold,
            "sell_threshold": sell_threshold,
            "min_confirmations": min_confirmations
        })

    except Exception as e:
        logger.error(f"Error updating thresholds: {e}")
        return jsonify({"error": str(e)}), 500


# ============================================
# Action Routes
# ============================================

@app.route("/api/run-cycle", methods=["POST"])
def api_run_cycle():
    """Manually trigger a trading cycle."""
    global bot_thread

    if bot_state["running"]:
        return jsonify({"error": "Cycle already running"}), 400

    # Run in background thread
    bot_thread = threading.Thread(target=run_bot_cycle)
    bot_thread.start()

    return jsonify({"message": "Trading cycle started"})


@app.route("/api/close-position/<symbol>", methods=["POST"])
def api_close_position(symbol):
    """Manually close a position."""
    try:
        prices = market_data_fetcher.get_current_prices()
        current_price = prices.get(symbol.upper())

        if not current_price:
            return jsonify({"error": "Could not get current price"}), 400

        trade = paper_trader.manual_close(symbol.upper(), current_price)

        if trade:
            return jsonify({
                "message": f"Position closed",
                "pnl": trade.realized_pnl,
                "exit_price": trade.exit_price
            })
        else:
            return jsonify({"error": "No position found"}), 404

    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/refresh-sentiment", methods=["POST"])
def api_refresh_sentiment():
    """Refresh sentiment data."""
    try:
        from ..data import reddit_collector, stocktwits_collector, news_collector

        results = {
            "reddit": 0,
            "stocktwits": 0,
            "news": 0
        }

        try:
            reddit_results = reddit_collector.fetch_all_coins(sentiment_analyzer, limit_per_coin=30)
            results["reddit"] = sum(reddit_results.values())
        except Exception as e:
            logger.error(f"Reddit error: {e}")

        try:
            stocktwits_results = stocktwits_collector.fetch_all_coins(sentiment_analyzer, max_results_per_coin=30)
            results["stocktwits"] = sum(stocktwits_results.values())
        except Exception as e:
            logger.error(f"StockTwits error: {e}")

        try:
            news_results = news_collector.fetch_all_coins(sentiment_analyzer, hours=24)
            results["news"] = sum(news_results.values())
        except Exception as e:
            logger.error(f"News error: {e}")

        return jsonify({"message": "Sentiment refreshed", "results": results})

    except Exception as e:
        return jsonify({"error": str(e)}), 500


# ============================================
# SSE for real-time updates
# ============================================

@app.route("/api/stream")
def stream():
    """Server-sent events for real-time updates."""
    def generate():
        while True:
            try:
                # Get current data
                prices = market_data_fetcher.get_current_prices()
                portfolio = paper_trader.get_portfolio_summary()

                data = {
                    "type": "update",
                    "timestamp": datetime.utcnow().isoformat(),
                    "prices": prices,
                    "portfolio": {
                        "total_value": portfolio["total_value"],
                        "cash_balance": portfolio["cash_balance"],
                        "unrealized_pnl": portfolio["unrealized_pnl"]
                    },
                    "bot_running": bot_state["running"]
                }

                yield f"data: {json.dumps(data)}\n\n"

                import time
                time.sleep(30)  # Update every 30 seconds

            except Exception as e:
                logger.error(f"Stream error: {e}")
                import time
                time.sleep(5)

    return Response(generate(), mimetype="text/event-stream")


def create_app():
    """Create and configure the Flask app."""
    # Initialize database
    db.create_tables()

    return app


if __name__ == "__main__":
    setup_logging()
    app = create_app()
    app.run(debug=True, port=5000)
