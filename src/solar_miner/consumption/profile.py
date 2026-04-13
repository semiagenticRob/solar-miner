"""Consumption profile model — estimates house power draw from billing data.

MVP approach: instead of real-time CT measurements, we model consumption
using historical billing data (Xcel interval exports or monthly totals).
The profile maps hour-of-day to estimated watts.
"""

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

# Standard residential load shape (fraction of daily average per hour).
# Based on typical US residential profiles: low overnight, morning peak,
# midday dip, evening peak. Sums to 24.0 (so each value × daily_avg_watts = hourly watts).
DEFAULT_LOAD_SHAPE = {
    0: 0.60, 1: 0.55, 2: 0.50, 3: 0.48, 4: 0.48, 5: 0.55,
    6: 0.75, 7: 0.95, 8: 1.00, 9: 0.95, 10: 0.85, 11: 0.80,
    12: 0.85, 13: 0.85, 14: 0.90, 15: 1.00, 16: 1.15, 17: 1.35,
    18: 1.50, 19: 1.50, 20: 1.40, 21: 1.25, 22: 1.05, 23: 0.80,
}


@dataclass
class ConsumptionProfile:
    """Hour-of-day consumption estimates in watts."""

    hourly_watts: dict[int, float]  # hour (0-23) → estimated watts
    source: str  # "xcel_interval", "monthly_total", "manual", etc.

    def get_current_watts(self, now: datetime | None = None) -> float:
        """Return estimated consumption for the current hour."""
        if now is None:
            now = datetime.now()
        hour = now.hour
        return self.hourly_watts.get(hour, 0.0)


def load_profile(path: str | Path) -> ConsumptionProfile:
    """Load a consumption profile from YAML.

    Expected format:
    ```yaml
    source: "xcel_interval"  # or "monthly_total", "manual"
    hourly_watts:
      0: 450
      1: 420
      ...
      23: 600
    ```
    """
    with open(path) as f:
        data = yaml.safe_load(f)

    hourly_watts = {int(k): float(v) for k, v in data["hourly_watts"].items()}
    return ConsumptionProfile(
        hourly_watts=hourly_watts,
        source=data.get("source", "manual"),
    )


def profile_from_monthly_kwh(monthly_kwh: float) -> ConsumptionProfile:
    """Build a profile from a single monthly kWh total.

    Distributes consumption across hours using the default residential load shape.
    """
    daily_kwh = monthly_kwh / 30.0
    daily_avg_watts = (daily_kwh * 1000) / 24.0  # average watts across the day

    # Scale the load shape so the sum over 24 hours matches daily_kwh
    shape_sum = sum(DEFAULT_LOAD_SHAPE.values())
    hourly_watts = {
        hour: (factor / shape_sum) * 24.0 * daily_avg_watts
        for hour, factor in DEFAULT_LOAD_SHAPE.items()
    }

    logger.info(
        "Built profile from %.0f kWh/month: avg %.0fW, range %.0f-%.0fW",
        monthly_kwh,
        daily_avg_watts,
        min(hourly_watts.values()),
        max(hourly_watts.values()),
    )

    return ConsumptionProfile(hourly_watts=hourly_watts, source="monthly_total")


def profile_from_interval_csv(csv_path: str | Path) -> ConsumptionProfile:
    """Build a profile from Xcel 15-minute or hourly interval data (CSV).

    Expects columns: datetime/timestamp, usage_kwh (or similar).
    Averages all readings by hour-of-day to build the profile.
    """
    import csv
    from collections import defaultdict
    from datetime import datetime as dt

    hourly_sums: dict[int, float] = defaultdict(float)
    hourly_counts: dict[int, int] = defaultdict(int)

    path = Path(csv_path)
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            # Try common Xcel CSV column names
            timestamp_str = (
                row.get("Date/Time")
                or row.get("datetime")
                or row.get("Timestamp")
                or row.get("DATE")
                or ""
            )
            usage_str = (
                row.get("Usage (kWh)")
                or row.get("usage_kwh")
                or row.get("kWh")
                or row.get("USAGE")
                or "0"
            )

            if not timestamp_str:
                continue

            # Parse timestamp — try common formats
            for fmt in ("%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
                try:
                    ts = dt.strptime(timestamp_str.strip(), fmt)
                    break
                except ValueError:
                    continue
            else:
                continue

            try:
                usage_kwh = float(usage_str.strip())
            except (ValueError, AttributeError):
                continue

            # Convert interval kWh to approximate watts
            # Assume 15-min intervals: watts = kWh * 4 * 1000
            # Assume hourly intervals: watts = kWh * 1000
            # We'll detect interval from the data, but default to 15-min
            watts = usage_kwh * 4 * 1000  # 15-min assumption

            hourly_sums[ts.hour] += watts
            hourly_counts[ts.hour] += 1

    if not hourly_counts:
        raise ValueError(f"No valid interval data found in {csv_path}")

    hourly_watts = {
        hour: hourly_sums[hour] / hourly_counts[hour]
        for hour in range(24)
        if hourly_counts.get(hour, 0) > 0
    }

    # Fill any missing hours with neighbors
    for hour in range(24):
        if hour not in hourly_watts:
            prev_h = (hour - 1) % 24
            next_h = (hour + 1) % 24
            hourly_watts[hour] = (
                hourly_watts.get(prev_h, 500) + hourly_watts.get(next_h, 500)
            ) / 2

    logger.info(
        "Built profile from interval CSV: range %.0f-%.0fW across %d days of data",
        min(hourly_watts.values()),
        max(hourly_watts.values()),
        max(hourly_counts.values()),
    )

    return ConsumptionProfile(hourly_watts=hourly_watts, source="xcel_interval")
