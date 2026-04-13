#!/usr/bin/env python3
"""Offline simulation — run the control logic against historical solar data.

Usage:
    python scripts/simulate.py --solar data/solar_export.csv --monthly-kwh 900
    python scripts/simulate.py --solar data/solar_export.csv --consumption data/xcel_interval.csv

Outputs what the miners WOULD have done for each time period,
plus total kWh consumed and estimated BTC revenue.
"""

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

# Add src to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from solar_miner.config import ControlConfig, MinerConfig
from solar_miner.consumption.profile import (
    ConsumptionProfile,
    profile_from_interval_csv,
    profile_from_monthly_kwh,
)
from solar_miner.controller.smoother import AsymmetricEMA
from solar_miner.controller.throttle import ThrottleController


def load_solar_csv(path: str) -> list[tuple[datetime, float]]:
    """Load solar production data from Enphase export CSV.

    Expects columns with timestamp and production value (Wh or kWh).
    """
    data = []
    with open(path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_str = (
                row.get("Date/Time")
                or row.get("datetime")
                or row.get("Timestamp")
                or row.get("DATE")
                or ""
            )
            prod_str = (
                row.get("Energy Produced (Wh)")
                or row.get("production_wh")
                or row.get("Wh")
                or row.get("kWh")
                or "0"
            )

            if not ts_str:
                continue

            for fmt in ("%m/%d/%Y %H:%M", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M"):
                try:
                    ts = datetime.strptime(ts_str.strip(), fmt)
                    break
                except ValueError:
                    continue
            else:
                continue

            try:
                wh = float(prod_str.strip())
            except (ValueError, AttributeError):
                continue

            # Convert Wh to average watts for the interval (assume 15-min intervals)
            watts = wh * 4  # Wh per 15 min → average watts
            data.append((ts, watts))

    return sorted(data, key=lambda x: x[0])


def run_simulation(
    solar_data: list[tuple[datetime, float]],
    consumption: ConsumptionProfile,
    safety_buffer: int = 400,
    min_surplus_to_start: int = 2000,
):
    """Simulate the control logic against historical data."""
    miners = [
        MinerConfig(name="alpha", ip="sim", min_power_watts=1800, max_power_watts=3250),
        MinerConfig(name="beta", ip="sim", min_power_watts=1800, max_power_watts=3250),
    ]
    control = ControlConfig(
        safety_buffer_watts=safety_buffer,
        min_surplus_to_start_watts=min_surplus_to_start,
    )
    controller = ThrottleController(miners, control)
    smoother = AsymmetricEMA(window_seconds=120, poll_interval=15)

    # Force past startup hold for simulation
    import time
    controller._surplus_above_start_since = time.monotonic() - 600

    total_solar_wh = 0.0
    total_mined_wh = 0.0
    total_exported_wh = 0.0
    total_consumed_wh = 0.0
    mining_hours = 0
    interval_hours = 0.25  # 15-min intervals

    print(f"{'Timestamp':<20} {'Solar W':>8} {'House W':>8} {'Surplus':>8} {'Smoothed':>8} {'Alpha W':>8} {'Beta W':>8} {'Mining W':>9}")
    print("-" * 100)

    for ts, solar_w in solar_data:
        house_w = consumption.get_current_watts(ts)
        raw_surplus = solar_w - house_w
        smoothed = smoother.update(raw_surplus)
        available = max(0, smoothed - safety_buffer)

        decision = controller.calculate(available)
        mining_w = decision.total_target_w

        alpha_w = 0
        beta_w = 0
        for t in decision.targets:
            if t.name == "alpha":
                alpha_w = t.target_watts
            else:
                beta_w = t.target_watts

        # Accumulate energy (Wh for 15-min interval)
        total_solar_wh += solar_w * interval_hours
        total_consumed_wh += house_w * interval_hours
        total_mined_wh += mining_w * interval_hours
        exported_w = max(0, solar_w - house_w - mining_w)
        total_exported_wh += exported_w * interval_hours
        if mining_w > 0:
            mining_hours += interval_hours

        # Print key intervals (when solar > 0 or mining > 0)
        if solar_w > 100 or mining_w > 0:
            print(f"{ts.strftime('%Y-%m-%d %H:%M'):<20} {solar_w:>8.0f} {house_w:>8.0f} {raw_surplus:>8.0f} {smoothed:>8.0f} {alpha_w:>8} {beta_w:>8} {mining_w:>9}")

    print("\n" + "=" * 100)
    print("SIMULATION SUMMARY")
    print("=" * 100)
    print(f"Total solar production:   {total_solar_wh / 1000:>10.1f} kWh")
    print(f"Total house consumption:  {total_consumed_wh / 1000:>10.1f} kWh")
    print(f"Total energy to miners:   {total_mined_wh / 1000:>10.1f} kWh")
    print(f"Total exported to grid:   {total_exported_wh / 1000:>10.1f} kWh")
    print(f"Mining hours:             {mining_hours:>10.1f} hrs")
    print()

    # Economics (rough estimates)
    # S19 at 34 J/TH → ~95 TH at 3250W
    # Revenue ~$0.037/kWh at current hashprice
    btc_revenue_per_kwh = 0.037
    xcel_credit_per_kwh = 0.08  # rough average
    mined_kwh = total_mined_wh / 1000
    exported_kwh = total_exported_wh / 1000

    print(f"Estimated BTC revenue:    ${mined_kwh * btc_revenue_per_kwh:>10.2f} (at ${btc_revenue_per_kwh}/kWh)")
    print(f"Xcel credit (if exported): ${(mined_kwh + exported_kwh) * xcel_credit_per_kwh:>9.2f} (at ${xcel_credit_per_kwh}/kWh)")
    print(f"Net difference:           ${mined_kwh * btc_revenue_per_kwh - mined_kwh * xcel_credit_per_kwh:>10.2f}")
    print()
    print("NOTE: BTC revenue estimate uses current hashprice. Actual revenue depends on")
    print("network difficulty, pool luck, and BTC price at time of payout/sale.")


def main():
    parser = argparse.ArgumentParser(description="Simulate Solar Miner against historical data")
    parser.add_argument("--solar", required=True, help="Path to Enphase solar export CSV")
    parser.add_argument("--monthly-kwh", type=float, help="Monthly house consumption in kWh (builds default profile)")
    parser.add_argument("--consumption", help="Path to Xcel interval CSV for consumption profile")
    parser.add_argument("--safety-buffer", type=int, default=400, help="Safety buffer watts (default: 400)")
    args = parser.parse_args()

    print("Loading solar data...")
    solar_data = load_solar_csv(args.solar)
    print(f"  Loaded {len(solar_data)} intervals from {solar_data[0][0]} to {solar_data[-1][0]}")

    if args.consumption:
        print("Loading consumption profile from interval CSV...")
        consumption = profile_from_interval_csv(args.consumption)
    elif args.monthly_kwh:
        print(f"Building consumption profile from {args.monthly_kwh} kWh/month...")
        consumption = profile_from_monthly_kwh(args.monthly_kwh)
    else:
        print("ERROR: Provide either --monthly-kwh or --consumption")
        sys.exit(1)

    print(f"  Profile source: {consumption.source}")
    print(f"  Range: {min(consumption.hourly_watts.values()):.0f}W - {max(consumption.hourly_watts.values()):.0f}W")
    print()

    run_simulation(solar_data, consumption, safety_buffer=args.safety_buffer)


if __name__ == "__main__":
    main()
