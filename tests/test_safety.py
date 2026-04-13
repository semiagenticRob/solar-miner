"""Tests for the safety layer."""

from solar_miner.controller.safety import SafetyCheck


class TestSafetyCheck:
    def test_normal_operation(self):
        safety = SafetyCheck(max_grid_draw_watts=50)
        action = safety.evaluate(solar_production_w=5000, surplus_w=2000, api_success=True)
        assert action.action == "ok"

    def test_soft_limit_single_import(self):
        """Single mild grid import — no action yet."""
        safety = SafetyCheck(max_grid_draw_watts=50)
        action = safety.evaluate(solar_production_w=3000, surplus_w=-100, api_success=True)
        # First occurrence — no action yet (need 2 consecutive)
        assert action.action == "ok"

    def test_soft_limit_consecutive_imports(self):
        """Two consecutive mild grid imports → decrement."""
        safety = SafetyCheck(max_grid_draw_watts=50)
        safety.evaluate(solar_production_w=3000, surplus_w=-100, api_success=True)
        action = safety.evaluate(solar_production_w=3000, surplus_w=-100, api_success=True)
        assert action.action == "decrement"

    def test_hard_limit_500w(self):
        """Grid import > 500W → drop to min."""
        safety = SafetyCheck(max_grid_draw_watts=50)
        action = safety.evaluate(solar_production_w=2000, surplus_w=-600, api_success=True)
        assert action.action == "drop_to_min"

    def test_emergency_1000w(self):
        """Grid import > 1000W → emergency shutdown."""
        safety = SafetyCheck(max_grid_draw_watts=50)
        action = safety.evaluate(solar_production_w=1000, surplus_w=-1200, api_success=True)
        assert action.action == "shutdown"
        assert "emergency" in action.reason

    def test_api_failure_emergency(self):
        """3 consecutive API failures → emergency shutdown."""
        safety = SafetyCheck(max_grid_draw_watts=50)
        safety.evaluate(solar_production_w=0, surplus_w=0, api_success=False)
        safety.evaluate(solar_production_w=0, surplus_w=0, api_success=False)
        action = safety.evaluate(solar_production_w=0, surplus_w=0, api_success=False)
        assert action.action == "shutdown"
        assert "api_unreachable" in action.reason

    def test_api_recovery_resets_counter(self):
        """A successful API call resets the failure counter."""
        safety = SafetyCheck(max_grid_draw_watts=50)
        safety.evaluate(solar_production_w=0, surplus_w=0, api_success=False)
        safety.evaluate(solar_production_w=0, surplus_w=0, api_success=False)
        safety.evaluate(solar_production_w=5000, surplus_w=3000, api_success=True)
        action = safety.evaluate(solar_production_w=0, surplus_w=0, api_success=False)
        # Only 1 failure after reset — should not trigger emergency
        assert action.action != "shutdown" or "emergency" not in action.reason

    def test_night_mode_immediate(self):
        """Zero solar doesn't trigger night mode immediately (needs 10 min)."""
        safety = SafetyCheck(max_grid_draw_watts=50)
        action = safety.evaluate(solar_production_w=0, surplus_w=-500, api_success=True)
        # First zero reading — night mode not yet active (hard limit may trigger)
        assert action.action != "shutdown" or "night" not in action.reason

    def test_grid_import_resets_on_export(self):
        """Consecutive import counter resets when exporting."""
        safety = SafetyCheck(max_grid_draw_watts=50)
        safety.evaluate(solar_production_w=3000, surplus_w=-100, api_success=True)
        # Now export
        safety.evaluate(solar_production_w=5000, surplus_w=2000, api_success=True)
        # Single import again — counter should be reset
        action = safety.evaluate(solar_production_w=3000, surplus_w=-100, api_success=True)
        assert action.action == "ok"
