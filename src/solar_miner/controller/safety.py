"""Safety layer — ensures miners never draw from the grid.

Multiple layers of protection:
1. Soft limit: consecutive mild grid import → decrement
2. Hard limit: significant grid import → drop to floor
3. Emergency: large import or API loss → full shutdown
4. Night mode: no solar → shutdown
"""

import logging
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class SafetyState:
    consecutive_grid_imports: int = 0
    consecutive_api_failures: int = 0
    emergency_active: bool = False
    emergency_start_time: float = 0.0
    night_mode_active: bool = False
    zero_solar_start: float = 0.0


class SafetyCheck:
    """Evaluate safety conditions and return required actions."""

    EMERGENCY_COOLDOWN_SECONDS = 300  # 5 minutes

    def __init__(self, max_grid_draw_watts: float = 50.0):
        self.max_grid_draw = max_grid_draw_watts
        self.state = SafetyState()

    def evaluate(
        self,
        solar_production_w: float,
        surplus_w: float,
        api_success: bool,
    ) -> "SafetyAction":
        """Evaluate current conditions and return the required action.

        Args:
            solar_production_w: Current solar production (from Enphase).
            surplus_w: Calculated surplus (may be from modeled consumption).
            api_success: Whether the latest Enphase API call succeeded.
        """
        now = time.monotonic()

        # Track API failures
        if not api_success:
            self.state.consecutive_api_failures += 1
        else:
            self.state.consecutive_api_failures = 0

        # Emergency: API unreachable for 3+ polls
        if self.state.consecutive_api_failures >= 3:
            logger.critical("Enphase API unreachable for %d polls — EMERGENCY SHUTDOWN",
                            self.state.consecutive_api_failures)
            return self._enter_emergency("api_unreachable")

        # Night mode: solar = 0 for 10+ minutes
        if solar_production_w <= 0:
            if self.state.zero_solar_start == 0:
                self.state.zero_solar_start = now
            elif now - self.state.zero_solar_start > 600:  # 10 min
                if not self.state.night_mode_active:
                    logger.info("Solar production zero for 10+ min — entering night mode")
                    self.state.night_mode_active = True
                return SafetyAction(action="shutdown", reason="night_mode")
        else:
            self.state.zero_solar_start = 0
            self.state.night_mode_active = False

        # Check if we're in emergency cooldown
        if self.state.emergency_active:
            elapsed = now - self.state.emergency_start_time
            if elapsed < self.EMERGENCY_COOLDOWN_SECONDS:
                remaining = self.EMERGENCY_COOLDOWN_SECONDS - elapsed
                return SafetyAction(action="shutdown", reason=f"emergency_cooldown ({remaining:.0f}s remaining)")
            else:
                logger.info("Emergency cooldown complete — resuming normal operation")
                self.state.emergency_active = False

        # Grid import checks (surplus < 0 means importing)
        grid_import = -surplus_w if surplus_w < 0 else 0.0

        if grid_import > 1000:
            # Emergency: massive grid import
            logger.critical("Grid import %.0fW exceeds 1000W — EMERGENCY SHUTDOWN", grid_import)
            return self._enter_emergency("massive_grid_import")

        if grid_import > 500:
            # Hard limit: significant import
            logger.warning("Grid import %.0fW exceeds 500W — dropping to minimum", grid_import)
            self.state.consecutive_grid_imports = 0
            return SafetyAction(action="drop_to_min", reason="hard_limit_500w")

        if grid_import > self.max_grid_draw:
            self.state.consecutive_grid_imports += 1
            if self.state.consecutive_grid_imports >= 2:
                logger.warning("Grid import %.0fW for %d consecutive reads — decrementing",
                               grid_import, self.state.consecutive_grid_imports)
                return SafetyAction(action="decrement", reason="soft_limit_consecutive")
        else:
            self.state.consecutive_grid_imports = 0

        return SafetyAction(action="ok", reason="normal")

    def _enter_emergency(self, reason: str) -> "SafetyAction":
        self.state.emergency_active = True
        self.state.emergency_start_time = time.monotonic()
        return SafetyAction(action="shutdown", reason=f"emergency_{reason}")


@dataclass
class SafetyAction:
    action: str  # "ok", "decrement", "drop_to_min", "shutdown"
    reason: str
