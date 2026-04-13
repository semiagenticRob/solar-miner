"""Enphase IQ Gateway local API client."""

import logging
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


@dataclass
class MeterReading:
    production_watts: float
    consumption_watts: float | None  # None if no consumption CTs installed
    net_watts: float  # positive = exporting, negative = importing


class EnphaseClient:
    """Read solar production (and optionally consumption) from the IQ Gateway."""

    def __init__(self, gateway_ip: str, token: str, timeout: float = 10.0):
        self.base_url = f"https://{gateway_ip}"
        self.token = token
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            verify=False,  # IQ Gateway uses self-signed cert
            timeout=timeout,
        )

    def read_meters(self) -> MeterReading:
        """Read current production and consumption from /ivp/meters/readings.

        Returns instantaneous watts for production and consumption (if CTs present).
        Falls back to /production.json if meter readings fail.
        """
        try:
            return self._read_ivp_meters()
        except Exception as e:
            logger.warning("ivp/meters/readings failed (%s), trying production.json", e)
            return self._read_production_json()

    def _read_ivp_meters(self) -> MeterReading:
        """Fast path: /ivp/meters/readings (~64ms)."""
        resp = self._client.get(f"{self.base_url}/ivp/meters/readings")
        resp.raise_for_status()
        data = resp.json()

        production_w = 0.0
        consumption_w = None
        has_consumption = False

        for meter in data:
            # Meters are identified by measurementType or eid
            # Production meter: measurementType = "production"
            # Consumption meter: measurementType = "total-consumption" or "net-consumption"
            m_type = meter.get("measurementType", "")
            active_power = meter.get("activePower", 0.0)

            if m_type == "production":
                production_w = active_power
            elif m_type == "total-consumption":
                consumption_w = active_power
                has_consumption = True
            elif m_type == "net-consumption":
                # Net consumption: negative = exporting, positive = importing
                pass

        if not has_consumption:
            # No consumption CTs — net is just production (will be combined with modeled consumption)
            return MeterReading(
                production_watts=production_w,
                consumption_watts=None,
                net_watts=production_w,  # All production is "available" — daemon subtracts modeled consumption
            )

        net = production_w - (consumption_w or 0.0)
        return MeterReading(
            production_watts=production_w,
            consumption_watts=consumption_w,
            net_watts=net,
        )

    def _read_production_json(self) -> MeterReading:
        """Slow fallback: /production.json?details=1 (~2500ms)."""
        resp = self._client.get(f"{self.base_url}/production.json", params={"details": 1})
        resp.raise_for_status()
        data = resp.json()

        production_w = 0.0
        consumption_w = None

        for entry in data.get("production", []):
            if entry.get("type") == "eim":
                production_w = entry.get("wNow", 0.0)

        for entry in data.get("consumption", []):
            if entry.get("type") == "total-consumption":
                consumption_w = entry.get("wNow", 0.0)

        if consumption_w is not None:
            net = production_w - consumption_w
        else:
            net = production_w

        return MeterReading(
            production_watts=production_w,
            consumption_watts=consumption_w,
            net_watts=net,
        )

    def read_production(self) -> float:
        """Convenience: return just the current solar production in watts."""
        return self.read_meters().production_watts

    def close(self):
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
