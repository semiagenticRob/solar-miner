"""Tests for the asymmetric EMA smoother."""

from solar_miner.controller.smoother import AsymmetricEMA


class TestAsymmetricEMA:
    def test_first_value_is_raw(self):
        ema = AsymmetricEMA(window_seconds=120, poll_interval=15)
        result = ema.update(1000)
        assert result == 1000

    def test_rising_signal_slow(self):
        """Rising signal should track slowly (conservative ramp up)."""
        ema = AsymmetricEMA(window_seconds=120, poll_interval=15)
        ema.update(1000)

        # Jump to 3000 — should NOT immediately follow
        result = ema.update(3000)
        assert result < 2500  # Should be well below the new value
        assert result > 1000  # But above the old value

    def test_falling_signal_fast(self):
        """Falling signal should track quickly (aggressive ramp down)."""
        ema = AsymmetricEMA(window_seconds=120, poll_interval=15)
        ema.update(3000)

        # Drop to 1000 — should follow quickly
        result = ema.update(1000)
        assert result < 2500  # Should drop significantly

    def test_asymmetry(self):
        """Ramp down should be faster than ramp up for the same magnitude change."""
        ema_up = AsymmetricEMA(window_seconds=120, poll_interval=15)
        ema_up.update(1000)
        up_result = ema_up.update(3000)
        up_delta = up_result - 1000  # How far it moved up

        ema_down = AsymmetricEMA(window_seconds=120, poll_interval=15)
        ema_down.update(3000)
        down_result = ema_down.update(1000)
        down_delta = 3000 - down_result  # How far it moved down

        assert down_delta > up_delta  # Down should move further

    def test_steady_signal_converges(self):
        """Constant input should converge to that value."""
        ema = AsymmetricEMA(window_seconds=60, poll_interval=15)
        for _ in range(100):
            result = ema.update(2000)
        assert abs(result - 2000) < 1  # Should be essentially 2000

    def test_reset(self):
        ema = AsymmetricEMA(window_seconds=120, poll_interval=15)
        ema.update(5000)
        ema.reset()
        assert ema.value == 0
        result = ema.update(1000)
        assert result == 1000  # First value after reset
