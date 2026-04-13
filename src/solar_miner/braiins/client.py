"""Braiins OS+ REST API client for S19 power target control."""

import logging
from dataclasses import dataclass
from enum import Enum

import httpx

logger = logging.getLogger(__name__)


class MinerState(Enum):
    OFF = "off"
    STARTING = "starting"
    RUNNING = "running"
    EMERGENCY_STOP = "emergency_stop"


@dataclass
class TunerStatus:
    power_target_watts: int
    approximate_power_watts: float
    is_tuning: bool
    mode: str  # "power-target", "hash-rate", etc.


class BraiinsClient:
    """Control a single Braiins OS+ miner via its REST API."""

    def __init__(self, name: str, ip: str, timeout: float = 10.0):
        self.name = name
        self.ip = ip
        self.base_url = f"http://{ip}"
        self._client = httpx.Client(timeout=timeout)

    def get_power_target(self) -> int | None:
        """Read the current power target in watts. Returns None if unreachable."""
        try:
            resp = self._client.get(f"{self.base_url}/api/v1/performance/power-target")
            resp.raise_for_status()
            data = resp.json()
            return data.get("watt")
        except Exception as e:
            logger.error("Failed to read power target from %s: %s", self.name, e)
            return None

    def set_power_target(self, watts: int) -> bool:
        """Set the power target in watts. Returns True on success."""
        try:
            resp = self._client.put(
                f"{self.base_url}/api/v1/performance/power-target",
                json={"watt": watts},
            )
            resp.raise_for_status()
            logger.info("Set %s power target to %dW", self.name, watts)
            return True
        except Exception as e:
            logger.error("Failed to set power target on %s to %dW: %s", self.name, watts, e)
            return False

    def increment_power(self, watts: int) -> bool:
        """Increment power target by watts."""
        try:
            resp = self._client.patch(
                f"{self.base_url}/api/v1/performance/power-target/increment",
                json={"watt": watts},
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error("Failed to increment %s by %dW: %s", self.name, watts, e)
            return False

    def decrement_power(self, watts: int) -> bool:
        """Decrement power target by watts."""
        try:
            resp = self._client.patch(
                f"{self.base_url}/api/v1/performance/power-target/decrement",
                json={"watt": watts},
            )
            resp.raise_for_status()
            return True
        except Exception as e:
            logger.error("Failed to decrement %s by %dW: %s", self.name, watts, e)
            return False

    def get_tuner_status(self) -> TunerStatus | None:
        """Read the current tuner state. Returns None if unreachable."""
        try:
            resp = self._client.get(f"{self.base_url}/api/v1/performance/tuner-state")
            resp.raise_for_status()
            data = resp.json()
            return TunerStatus(
                power_target_watts=data.get("powerTarget", {}).get("watt", 0),
                approximate_power_watts=data.get("approximatePowerConsumptionW", 0.0),
                is_tuning=data.get("isTuning", False),
                mode=data.get("mode", "unknown"),
            )
        except Exception as e:
            logger.error("Failed to read tuner status from %s: %s", self.name, e)
            return None

    def get_performance_mode(self) -> str | None:
        """Read the current performance mode."""
        try:
            resp = self._client.get(f"{self.base_url}/api/v1/performance/mode")
            resp.raise_for_status()
            return resp.json().get("mode")
        except Exception as e:
            logger.error("Failed to read performance mode from %s: %s", self.name, e)
            return None

    @property
    def is_reachable(self) -> bool:
        """Quick connectivity check."""
        try:
            resp = self._client.get(f"{self.base_url}/api/v1/performance/mode")
            return resp.status_code == 200
        except Exception:
            return False

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
