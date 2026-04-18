#!/usr/bin/env python3
"""
Run the Crypto Trading Bot Web UI.

Usage:
    python run_web.py [--port PORT] [--debug]

Options:
    --port PORT   Port to run on (default: 5000)
    --debug       Enable debug mode
"""

import argparse
import sys
import os

# Add parent directory to path for imports
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from crypto_trading_bot.web.app import create_app
from crypto_trading_bot.utils import setup_logging


def main():
    parser = argparse.ArgumentParser(description="Crypto Trading Bot Web UI")
    parser.add_argument("--port", type=int, default=5000, help="Port to run on")
    parser.add_argument("--debug", action="store_true", help="Enable debug mode")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind to")

    args = parser.parse_args()

    # Setup logging
    log_level = "DEBUG" if args.debug else "INFO"
    setup_logging(log_level=log_level)

    # Create and run app
    app = create_app()

    print(f"""
╔══════════════════════════════════════════════════════════════╗
║           Crypto Trading Bot - Web Interface                 ║
╠══════════════════════════════════════════════════════════════╣
║                                                              ║
║  Dashboard: http://{args.host}:{args.port}/                          ║
║                                                              ║
║  Mode: {'DEBUG' if args.debug else 'PRODUCTION'} | Paper Trading: ENABLED                 ║
║                                                              ║
║  Press Ctrl+C to stop                                        ║
║                                                              ║
╚══════════════════════════════════════════════════════════════╝
""")

    app.run(
        host=args.host,
        port=args.port,
        debug=args.debug
    )


if __name__ == "__main__":
    main()
