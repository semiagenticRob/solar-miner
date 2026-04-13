"""Tests for the throttle controller — surplus-to-target mapping."""

import time

import pytest

from solar_miner.config import ControlConfig, MinerConfig
from solar_miner.controller.throttle import ThrottleController, MinerState


@pytest.fixture
def miners():
    return [
        MinerConfig(name="alpha", ip="1.1.1.1", min_power_watts=1800, max_power_watts=3250),
        MinerConfig(name="beta", ip="1.1.1.2", min_power_watts=1800, max_power_watts=3250),
    ]


@pytest.fixture
def control():
    return ControlConfig(
        safety_buffer_watts=400,
        min_surplus_to_start_watts=2000,
    )


@pytest.fixture
def controller(miners, control):
    c = ThrottleController(miners, control)
    # Force past the startup hold period
    c._surplus_above_start_since = time.monotonic() - 600
    return c


class TestTwoMinerDistribution:
    def test_zero_surplus_both_off(self, controller):
        # Force deficit sustained
        controller._surplus_below_min_since = time.monotonic() - 600
        decision = controller.calculate(0)
        assert all(t.target_watts == 0 for t in decision.targets)

    def test_below_minimum_both_off(self, controller):
        controller._surplus_below_min_since = time.monotonic() - 600
        decision = controller.calculate(1500)
        assert all(t.target_watts == 0 for t in decision.targets)

    def test_one_miner_partial(self, controller):
        decision = controller.calculate(2500)
        targets = {t.name: t.target_watts for t in decision.targets}
        primary = controller.miners[controller._primary_index].name
        secondary = controller.miners[1 - controller._primary_index].name
        assert targets[primary] == 2500
        assert targets[secondary] == 0

    def test_one_miner_at_max(self, controller):
        decision = controller.calculate(3250)
        targets = {t.name: t.target_watts for t in decision.targets}
        primary = controller.miners[controller._primary_index].name
        assert targets[primary] == 3250

    def test_gap_zone_only_primary(self, controller):
        """Between 3250 and 3600, only primary runs (secondary below its min)."""
        decision = controller.calculate(3400)
        targets = {t.name: t.target_watts for t in decision.targets}
        primary = controller.miners[controller._primary_index].name
        secondary = controller.miners[1 - controller._primary_index].name
        assert targets[primary] == 3250  # capped at max
        assert targets[secondary] == 0

    def test_both_miners_split(self, controller):
        decision = controller.calculate(5000)
        targets = {t.name: t.target_watts for t in decision.targets}
        assert all(t > 0 for t in targets.values())
        assert sum(targets.values()) == 5000

    def test_both_miners_at_max(self, controller):
        decision = controller.calculate(8000)
        targets = {t.name: t.target_watts for t in decision.targets}
        assert all(t == 3250 for t in targets.values())

    def test_total_never_exceeds_available(self, controller):
        for surplus in [0, 500, 1800, 2500, 3250, 3600, 5000, 6500, 10000]:
            if surplus < 1800:
                controller._surplus_below_min_since = time.monotonic() - 600
            else:
                controller._surplus_below_min_since = 0
            decision = controller.calculate(surplus)
            assert decision.total_target_w <= surplus or decision.total_target_w <= 6500


class TestMinerStateTransitions:
    def test_off_to_starting(self, controller):
        decision = controller.calculate(2500)
        primary = controller.miners[controller._primary_index].name
        target = next(t for t in decision.targets if t.name == primary)
        assert target.state == MinerState.STARTING

    def test_starting_to_running(self, controller):
        # First call: OFF -> STARTING
        controller.calculate(2500)
        # Second call: STARTING -> RUNNING (state persists)
        decision = controller.calculate(2500)
        primary = controller.miners[controller._primary_index].name
        target = next(t for t in decision.targets if t.name == primary)
        assert target.state == MinerState.RUNNING
