"""Core throttle logic — maps surplus watts to miner power targets.

Two-miner distribution:
  < 1800W surplus  → Both OFF
  1800W - 3250W    → Miner A at surplus, Miner B OFF
  3250W - 3600W    → Miner A at max, Miner B OFF (surplus too low for B's floor)
  3600W - 6500W    → Both ON, split evenly
  > 6500W          → Both at max
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime

from solar_miner.braiins.client import BraiinsClient, MinerState
from solar_miner.config import ControlConfig, MinerConfig

logger = logging.getLogger(__name__)


@dataclass
class MinerTarget:
    """Desired state for a single miner."""
    name: str
    target_watts: int  # 0 = OFF
    state: MinerState
    state_entered_at: float = field(default_factory=time.monotonic)


@dataclass
class ThrottleDecision:
    """The output of the throttle calculation."""
    targets: list[MinerTarget]
    surplus_available_w: float
    total_target_w: int
    reason: str


class ThrottleController:
    """Calculate and apply power targets for the miner fleet."""

    STARTUP_HOLD_SECONDS = 300  # 5 min hold after starting a miner
    SURPLUS_HOLD_SECONDS = 300  # 5 min of sustained surplus before starting

    def __init__(self, miners: list[MinerConfig], control: ControlConfig):
        self.miners = miners
        self.control = control
        self._states: dict[str, MinerTarget] = {
            m.name: MinerTarget(name=m.name, target_watts=0, state=MinerState.OFF)
            for m in miners
        }
        self._surplus_above_start_since: float = 0.0
        self._surplus_below_min_since: float = 0.0
        # Alternate primary miner weekly
        self._primary_index = 0
        self._last_rotation = datetime.now()

    def calculate(self, available_surplus_w: float) -> ThrottleDecision:
        """Given available surplus (after safety buffer), calculate miner targets.

        Args:
            available_surplus_w: Surplus watts available for mining (already has safety buffer subtracted).
        """
        now = time.monotonic()
        available = max(0.0, available_surplus_w)

        # Rotate primary miner weekly
        self._maybe_rotate_primary()

        primary = self.miners[self._primary_index]
        secondary = self.miners[1 - self._primary_index]
        primary_state = self._states[primary.name]
        secondary_state = self._states[secondary.name]

        total_max = sum(m.max_power_watts for m in self.miners)
        min_single = primary.min_power_watts
        min_both = primary.min_power_watts + secondary.min_power_watts

        # Track how long surplus has been above/below thresholds
        if available >= self.control.min_surplus_to_start_watts:
            if self._surplus_above_start_since == 0:
                self._surplus_above_start_since = now
            self._surplus_below_min_since = 0
        elif available < min_single:
            if self._surplus_below_min_since == 0:
                self._surplus_below_min_since = now
            self._surplus_above_start_since = 0
        else:
            self._surplus_below_min_since = 0

        surplus_sustained = (
            self._surplus_above_start_since > 0
            and (now - self._surplus_above_start_since) >= self.SURPLUS_HOLD_SECONDS
        )
        deficit_sustained = (
            self._surplus_below_min_since > 0
            and (now - self._surplus_below_min_since) >= self.SURPLUS_HOLD_SECONDS
        )

        targets: list[MinerTarget] = []

        # === Decision logic ===

        if available < min_single:
            # Not enough for even one miner
            if deficit_sustained or primary_state.state == MinerState.OFF:
                targets = [
                    self._target_off(primary),
                    self._target_off(secondary),
                ]
                reason = f"surplus {available:.0f}W < min {min_single}W"
            else:
                # Keep current state during grace period (don't thrash on brief dips)
                targets = [
                    self._hold_or_ramp(primary, int(available), primary_state),
                    self._target_off(secondary),
                ]
                reason = f"surplus dipped to {available:.0f}W, grace period"
        elif available < primary.max_power_watts:
            # Enough for one miner, partial power
            if primary_state.state == MinerState.OFF and not surplus_sustained:
                targets = [
                    self._target_off(primary),
                    self._target_off(secondary),
                ]
                reason = f"surplus {available:.0f}W ok but waiting for sustained threshold"
            else:
                targets = [
                    self._target_on(primary, int(available), primary_state),
                    self._target_off(secondary),
                ]
                reason = f"one miner at {available:.0f}W"
        elif available < min_both:
            # Primary at max, not enough for secondary
            targets = [
                self._target_on(primary, primary.max_power_watts, primary_state),
                self._target_off(secondary),
            ]
            reason = f"primary at max, surplus {available:.0f}W too low for secondary"
        elif available <= total_max:
            # Both miners, split evenly
            per_miner = int(available / 2)
            targets = [
                self._target_on(primary, min(per_miner, primary.max_power_watts), primary_state),
                self._target_on(secondary, min(per_miner, secondary.max_power_watts), secondary_state),
            ]
            reason = f"both miners at ~{per_miner}W each"
        else:
            # More surplus than both can handle — both at max
            targets = [
                self._target_on(primary, primary.max_power_watts, primary_state),
                self._target_on(secondary, secondary.max_power_watts, secondary_state),
            ]
            reason = f"both miners at max, surplus {available:.0f}W exceeds capacity"

        total_target = sum(t.target_watts for t in targets)

        # Update internal state
        for t in targets:
            self._states[t.name] = t

        return ThrottleDecision(
            targets=targets,
            surplus_available_w=available,
            total_target_w=total_target,
            reason=reason,
        )

    def apply(self, decision: ThrottleDecision, clients: dict[str, BraiinsClient]) -> None:
        """Send power target commands to miners, respecting ramp step limits."""
        for target in decision.targets:
            client = clients.get(target.name)
            if client is None:
                logger.error("No client for miner %s", target.name)
                continue

            miner_cfg = next((m for m in self.miners if m.name == target.name), None)
            if miner_cfg is None:
                continue

            current = client.get_power_target()
            if current is None:
                logger.warning("Cannot read %s — skipping", target.name)
                continue

            desired = target.target_watts

            if desired == 0 and current > 0:
                # Shutting down — set to 0 directly
                client.set_power_target(0)
            elif desired > 0 and current == 0:
                # Starting up — set to min first, then ramp
                client.set_power_target(miner_cfg.min_power_watts)
            elif desired != current:
                # Ramp toward target, clamped to step size
                delta = desired - current
                clamped = max(-miner_cfg.ramp_step_watts, min(miner_cfg.ramp_step_watts, delta))
                new_target = current + clamped
                new_target = max(miner_cfg.min_power_watts, min(miner_cfg.max_power_watts, new_target))
                client.set_power_target(new_target)

    def force_shutdown(self, clients: dict[str, BraiinsClient]) -> None:
        """Emergency: set all miners to 0W immediately."""
        for name, client in clients.items():
            client.set_power_target(0)
            self._states[name] = MinerTarget(
                name=name, target_watts=0, state=MinerState.EMERGENCY_STOP
            )

    def force_drop_to_min(self, clients: dict[str, BraiinsClient]) -> None:
        """Hard safety limit: drop all running miners to their minimum."""
        for miner_cfg in self.miners:
            client = clients.get(miner_cfg.name)
            if client and self._states[miner_cfg.name].state == MinerState.RUNNING:
                client.set_power_target(miner_cfg.min_power_watts)
                self._states[miner_cfg.name].target_watts = miner_cfg.min_power_watts

    def _target_off(self, miner: MinerConfig) -> MinerTarget:
        return MinerTarget(name=miner.name, target_watts=0, state=MinerState.OFF)

    def _target_on(self, miner: MinerConfig, watts: int, current: MinerTarget) -> MinerTarget:
        clamped = max(miner.min_power_watts, min(miner.max_power_watts, watts))
        if current.state == MinerState.OFF:
            return MinerTarget(name=miner.name, target_watts=clamped, state=MinerState.STARTING)
        return MinerTarget(name=miner.name, target_watts=clamped, state=MinerState.RUNNING)

    def _hold_or_ramp(self, miner: MinerConfig, watts: int, current: MinerTarget) -> MinerTarget:
        """Keep running but adjust target, or hold at min if watts too low."""
        if current.state in (MinerState.RUNNING, MinerState.STARTING):
            clamped = max(miner.min_power_watts, min(miner.max_power_watts, watts))
            return MinerTarget(name=miner.name, target_watts=clamped, state=current.state)
        return self._target_off(miner)

    def _maybe_rotate_primary(self):
        """Rotate primary miner weekly to equalize wear."""
        now = datetime.now()
        if (now - self._last_rotation).days >= 7:
            self._primary_index = 1 - self._primary_index
            self._last_rotation = now
            logger.info("Rotated primary miner to %s", self.miners[self._primary_index].name)
