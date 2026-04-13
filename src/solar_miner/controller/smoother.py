"""Asymmetric exponential moving average for surplus signal smoothing.

Ramps UP slowly (conservative — don't chase sunny spikes).
Ramps DOWN quickly (aggressive — cut power fast when surplus drops).
This asymmetry prevents grid draw from cloud transients.
"""

import time


class AsymmetricEMA:
    """EMA that uses different smoothing factors for rising vs falling signals."""

    def __init__(self, window_seconds: float, poll_interval: float = 15.0):
        # Number of samples in the window
        n = max(1, window_seconds / poll_interval)
        # Standard EMA alpha
        self._alpha_up = 2.0 / (n + 1)       # slow ramp up
        self._alpha_down = 2.0 / (n / 3 + 1)  # 3x faster ramp down
        self._value: float | None = None
        self._last_time: float = 0.0

    @property
    def value(self) -> float:
        """Current smoothed value."""
        return self._value if self._value is not None else 0.0

    def update(self, raw: float) -> float:
        """Feed a new raw reading, return the smoothed value."""
        now = time.monotonic()

        if self._value is None:
            self._value = raw
            self._last_time = now
            return self._value

        # Pick alpha based on direction
        if raw < self._value:
            alpha = self._alpha_down  # fast ramp down
        else:
            alpha = self._alpha_up    # slow ramp up

        self._value = alpha * raw + (1 - alpha) * self._value
        self._last_time = now
        return self._value

    def reset(self):
        """Reset the smoother (e.g., at sunrise)."""
        self._value = None
