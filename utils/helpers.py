"""Utility helper functions."""

import json
from datetime import datetime, timedelta
from decimal import Decimal, ROUND_DOWN
from typing import Any, Dict, List, Optional, Union


def format_price(price: float, decimals: int = 2) -> str:
    """
    Format a price with proper decimal places.

    Args:
        price: Price value
        decimals: Number of decimal places

    Returns:
        Formatted price string
    """
    if price >= 1:
        return f"${price:,.{decimals}f}"
    else:
        # For small prices, show more decimals
        return f"${price:.6f}"


def format_quantity(quantity: float, symbol: str = "") -> str:
    """
    Format a quantity based on the asset.

    Args:
        quantity: Quantity value
        symbol: Asset symbol for context

    Returns:
        Formatted quantity string
    """
    if quantity >= 1:
        return f"{quantity:.4f}"
    else:
        return f"{quantity:.8f}"


def format_percentage(value: float, include_sign: bool = True) -> str:
    """
    Format a percentage value.

    Args:
        value: Percentage value
        include_sign: Whether to include +/- sign

    Returns:
        Formatted percentage string
    """
    if include_sign:
        return f"{value:+.2f}%"
    return f"{value:.2f}%"


def format_pnl(pnl: float) -> str:
    """
    Format P&L with color indication.

    Args:
        pnl: Profit/loss value

    Returns:
        Formatted P&L string
    """
    if pnl >= 0:
        return f"+${pnl:.2f}"
    return f"-${abs(pnl):.2f}"


def truncate_decimal(value: float, decimals: int) -> float:
    """
    Truncate a decimal value (don't round up).

    Args:
        value: Value to truncate
        decimals: Number of decimal places

    Returns:
        Truncated value
    """
    d = Decimal(str(value))
    return float(d.quantize(Decimal(10) ** -decimals, rounding=ROUND_DOWN))


def safe_divide(numerator: float, denominator: float, default: float = 0.0) -> float:
    """
    Safely divide two numbers.

    Args:
        numerator: Numerator
        denominator: Denominator
        default: Default value if denominator is zero

    Returns:
        Division result or default
    """
    if denominator == 0:
        return default
    return numerator / denominator


def clamp(value: float, min_value: float, max_value: float) -> float:
    """
    Clamp a value between min and max.

    Args:
        value: Value to clamp
        min_value: Minimum bound
        max_value: Maximum bound

    Returns:
        Clamped value
    """
    return max(min_value, min(value, max_value))


def time_ago(dt: datetime) -> str:
    """
    Get human-readable time difference.

    Args:
        dt: Datetime to compare

    Returns:
        Human-readable string like "5 minutes ago"
    """
    now = datetime.utcnow()
    diff = now - dt

    seconds = int(diff.total_seconds())

    if seconds < 60:
        return f"{seconds}s ago"
    elif seconds < 3600:
        return f"{seconds // 60}m ago"
    elif seconds < 86400:
        return f"{seconds // 3600}h ago"
    else:
        return f"{seconds // 86400}d ago"


def parse_timeframe(timeframe: str) -> timedelta:
    """
    Parse a timeframe string to timedelta.

    Args:
        timeframe: Timeframe string (e.g., "1h", "4h", "1d")

    Returns:
        Timedelta object
    """
    unit = timeframe[-1]
    value = int(timeframe[:-1])

    if unit == "m":
        return timedelta(minutes=value)
    elif unit == "h":
        return timedelta(hours=value)
    elif unit == "d":
        return timedelta(days=value)
    elif unit == "w":
        return timedelta(weeks=value)
    else:
        raise ValueError(f"Unknown timeframe unit: {unit}")


def merge_dicts(base: Dict, override: Dict) -> Dict:
    """
    Deep merge two dictionaries.

    Args:
        base: Base dictionary
        override: Override dictionary

    Returns:
        Merged dictionary
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = merge_dicts(result[key], value)
        else:
            result[key] = value

    return result


def json_serialize(obj: Any) -> str:
    """
    Serialize an object to JSON with datetime handling.

    Args:
        obj: Object to serialize

    Returns:
        JSON string
    """
    def default(o):
        if isinstance(o, datetime):
            return o.isoformat()
        elif hasattr(o, "__dict__"):
            return o.__dict__
        return str(o)

    return json.dumps(obj, default=default, indent=2)


def calculate_change_pct(old_value: float, new_value: float) -> float:
    """
    Calculate percentage change between two values.

    Args:
        old_value: Original value
        new_value: New value

    Returns:
        Percentage change
    """
    if old_value == 0:
        return 0.0 if new_value == 0 else float("inf")
    return ((new_value - old_value) / old_value) * 100


def batch_list(items: List, batch_size: int) -> List[List]:
    """
    Split a list into batches.

    Args:
        items: List to split
        batch_size: Size of each batch

    Returns:
        List of batches
    """
    return [items[i:i + batch_size] for i in range(0, len(items), batch_size)]


def retry_with_backoff(
    func,
    max_retries: int = 3,
    initial_delay: float = 1.0,
    backoff_factor: float = 2.0,
    exceptions: tuple = (Exception,)
):
    """
    Retry a function with exponential backoff.

    Args:
        func: Function to retry
        max_retries: Maximum number of retries
        initial_delay: Initial delay between retries
        backoff_factor: Multiplier for delay
        exceptions: Tuple of exceptions to catch

    Returns:
        Function result
    """
    import time

    delay = initial_delay

    for attempt in range(max_retries + 1):
        try:
            return func()
        except exceptions as e:
            if attempt == max_retries:
                raise
            time.sleep(delay)
            delay *= backoff_factor


def is_market_hours() -> bool:
    """
    Check if crypto markets are open (always true for crypto).

    Returns:
        True (crypto markets are 24/7)
    """
    return True


def get_next_candle_time(timeframe: str) -> datetime:
    """
    Get the timestamp of the next candle close.

    Args:
        timeframe: Candle timeframe (e.g., "1h", "4h")

    Returns:
        Next candle close time
    """
    now = datetime.utcnow()
    delta = parse_timeframe(timeframe)

    # Calculate seconds since epoch
    epoch = datetime(1970, 1, 1)
    seconds_since_epoch = (now - epoch).total_seconds()

    # Round up to next interval
    interval_seconds = delta.total_seconds()
    next_interval = (
        (seconds_since_epoch // interval_seconds + 1) * interval_seconds
    )

    return epoch + timedelta(seconds=next_interval)
