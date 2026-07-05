from app.models import RetryStrategyType


def compute_backoff_seconds(
    strategy: RetryStrategyType,
    attempt_number: int,
    base_delay_seconds: int,
    max_delay_seconds: int,
) -> int:
    """attempt_number is 1-indexed (this is the attempt that just failed)."""
    if strategy == RetryStrategyType.FIXED:
        delay = base_delay_seconds
    elif strategy == RetryStrategyType.LINEAR:
        delay = base_delay_seconds * attempt_number
    elif strategy == RetryStrategyType.EXPONENTIAL:
        delay = base_delay_seconds * (2 ** (attempt_number - 1))
    else:
        delay = base_delay_seconds
    return min(delay, max_delay_seconds)
