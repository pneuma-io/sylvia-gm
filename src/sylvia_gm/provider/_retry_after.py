"""Read provider-supplied retry timing without retaining arbitrary header content."""

from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from math import isfinite


def retry_after_seconds(error: object) -> float | None:
    """Extract seconds or an HTTP-date from an SDK error's response, without inventing a delay."""
    response = getattr(error, "response", None)
    headers = getattr(response, "headers", {})
    value = headers.get("retry-after")
    if not isinstance(value, str) or len(value) > 128:
        return None
    try:
        delay = float(value)
    except ValueError:
        try:
            deadline = parsedate_to_datetime(value)
            if deadline.tzinfo is None:
                return None
            delay = (deadline - datetime.now(UTC)).total_seconds()
        except (ValueError, TypeError, OverflowError):
            return None
    return delay if isfinite(delay) and delay >= 0 else None
