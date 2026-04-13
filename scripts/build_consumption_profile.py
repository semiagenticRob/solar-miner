#!/usr/bin/env python3
"""Build consumption profiles from Xcel billing history.

Uses pre-solar billing data (Apr 2024 - Feb 2025) as ground truth for
actual house consumption, since those months had no solar offset.

Generates seasonal profiles (summer, winter, shoulder) with hour-of-day
load shapes based on the standard residential curve, scaled to match
actual monthly totals.

Usage:
    python scripts/build_consumption_profile.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import yaml
from solar_miner.consumption.profile import DEFAULT_LOAD_SHAPE, ConsumptionProfile

# Pre-solar months — these show ACTUAL house consumption (no solar offset)
# Grouped by season for Colorado climate
PRE_SOLAR = {
    # Summer (Jun-Sep): high AC load
    "summer": [
        {"month": "Jun 2024", "kwh": 720},
        {"month": "Jul 2024", "kwh": 773},
        {"month": "Aug 2024", "kwh": 873},
        {"month": "Sep 2024", "kwh": 913},
    ],
    # Winter (Dec-Feb): heating load (gas furnace fan + baseload)
    "winter": [
        {"month": "Dec 2024", "kwh": 856},
        {"month": "Jan 2025", "kwh": 605},
        {"month": "Feb 2025", "kwh": 516},
    ],
    # Shoulder (Mar-May, Oct-Nov): mild temps, lower HVAC
    "shoulder": [
        {"month": "Apr 2024", "kwh": 694},
        {"month": "May 2024", "kwh": 678},
        {"month": "Oct 2024", "kwh": 732},
        {"month": "Nov 2024", "kwh": 717},
    ],
}

# Month-to-season mapping
MONTH_SEASON = {
    1: "winter", 2: "winter", 3: "shoulder",
    4: "shoulder", 5: "shoulder", 6: "summer",
    7: "summer", 8: "summer", 9: "summer",
    10: "shoulder", 11: "shoulder", 12: "winter",
}


def build_profile_for_season(months: list[dict]) -> dict[int, float]:
    """Build hour-of-day watts from seasonal monthly kWh averages."""
    avg_monthly_kwh = sum(m["kwh"] for m in months) / len(months)
    daily_kwh = avg_monthly_kwh / 30.0
    daily_avg_watts = (daily_kwh * 1000) / 24.0

    # Scale load shape
    shape_sum = sum(DEFAULT_LOAD_SHAPE.values())
    return {
        hour: (factor / shape_sum) * 24.0 * daily_avg_watts
        for hour, factor in DEFAULT_LOAD_SHAPE.items()
    }


def main():
    print("=" * 70)
    print("XCEL BILLING ANALYSIS — Consumption Profile Builder")
    print("=" * 70)

    # Overall stats
    all_pre_solar = []
    for months in PRE_SOLAR.values():
        all_pre_solar.extend(months)
    total_kwh = sum(m["kwh"] for m in all_pre_solar)
    avg_monthly = total_kwh / len(all_pre_solar)
    daily_avg = avg_monthly / 30
    avg_watts = (daily_avg * 1000) / 24

    print(f"\nPre-solar period: Apr 2024 - Feb 2025 ({len(all_pre_solar)} months)")
    print(f"  Total consumption:  {total_kwh:>6,} kWh")
    print(f"  Monthly average:    {avg_monthly:>6.0f} kWh")
    print(f"  Daily average:      {daily_avg:>6.1f} kWh")
    print(f"  Average power draw: {avg_watts:>6.0f} W")

    print(f"\n{'Season':<12} {'Months':>6} {'Avg kWh/mo':>12} {'Avg Watts':>11} {'Range':>20}")
    print("-" * 70)

    profiles = {}
    for season, months in PRE_SOLAR.items():
        avg = sum(m["kwh"] for m in months) / len(months)
        min_kwh = min(m["kwh"] for m in months)
        max_kwh = max(m["kwh"] for m in months)
        avg_w = (avg / 30 * 1000) / 24
        print(f"  {season:<10} {len(months):>6} {avg:>12.0f} {avg_w:>11.0f} {min_kwh:>8}-{max_kwh:<8}")

        profiles[season] = build_profile_for_season(months)

    # Build the combined profile (picks season by month)
    # For the daemon, we generate a file that maps month+hour → watts
    print("\n" + "=" * 70)
    print("SEASONAL HOURLY PROFILES (watts)")
    print("=" * 70)
    print(f"\n{'Hour':<6}", end="")
    for season in ["summer", "shoulder", "winter"]:
        print(f"  {season:>10}", end="")
    print()
    print("-" * 40)

    for hour in range(24):
        print(f"  {hour:>2}:00", end="")
        for season in ["summer", "shoulder", "winter"]:
            print(f"  {profiles[season][hour]:>10.0f}", end="")
        print()

    # Write the profile YAML (used by the daemon)
    print("\n" + "=" * 70)
    print("WRITING CONSUMPTION PROFILES")
    print("=" * 70)

    data_dir = Path(__file__).parent.parent / "data"
    data_dir.mkdir(parents=True, exist_ok=True)

    for season, hourly in profiles.items():
        path = data_dir / f"consumption_profile_{season}.yaml"
        profile_data = {
            "source": "xcel_billing_presolar",
            "season": season,
            "months": [m["month"] for m in PRE_SOLAR[season]],
            "avg_monthly_kwh": sum(m["kwh"] for m in PRE_SOLAR[season]) / len(PRE_SOLAR[season]),
            "hourly_watts": {h: round(w, 1) for h, w in hourly.items()},
        }
        with open(path, "w") as f:
            yaml.dump(profile_data, f, default_flow_style=False, sort_keys=False)
        print(f"  Wrote {path}")

    # Write the "current month" profile (auto-selects season)
    # This is the one the daemon uses
    from datetime import datetime
    current_month = datetime.now().month
    current_season = MONTH_SEASON[current_month]
    current_profile = profiles[current_season]

    default_path = data_dir / "consumption_profile.yaml"
    with open(default_path, "w") as f:
        yaml.dump({
            "source": "xcel_billing_presolar",
            "season": current_season,
            "note": f"Auto-generated for month {current_month} ({current_season}). Rebuild with scripts/build_consumption_profile.py for season changes.",
            "hourly_watts": {h: round(w, 1) for h, w in current_profile.items()},
        }, f, default_flow_style=False, sort_keys=False)
    print(f"  Wrote {default_path} (current season: {current_season})")

    # Post-solar analysis
    print("\n" + "=" * 70)
    print("POST-SOLAR ANALYSIS — What the solar is offsetting")
    print("=" * 70)

    post_solar = [
        ("Mar 2025", 474), ("Apr 2025", 328), ("May 2025", 222),
        ("Jun 2025", 240), ("Jul 2025", 374), ("Aug 2025", 459),
        ("Sep 2025", 440), ("Oct 2025", 301), ("Nov 2025", 303),
        ("Dec 2025", 353), ("Jan 2026", 371), ("Feb 2026", 385),
        ("Mar 2026", 332),
    ]

    # Map pre-solar months to estimate what consumption would have been
    pre_solar_by_month_name = {
        "Apr": 694, "May": 678, "Jun": 720, "Jul": 773,
        "Aug": 873, "Sep": 913, "Oct": 732, "Nov": 717,
        "Dec": 856, "Jan": 605, "Feb": 516, "Mar": 474,  # Mar only has post-solar
    }

    print(f"\n{'Month':<12} {'Grid kWh':>10} {'Est. House kWh':>16} {'Solar Offset':>14} {'Offset %':>10}")
    print("-" * 70)

    total_grid = 0
    total_offset = 0
    for label, grid_kwh in post_solar:
        month_name = label.split()[0]
        est_house = pre_solar_by_month_name.get(month_name, avg_monthly)
        offset = est_house - grid_kwh
        pct = (offset / est_house * 100) if est_house > 0 else 0
        total_grid += grid_kwh
        total_offset += offset
        print(f"  {label:<10} {grid_kwh:>10} {est_house:>16} {offset:>14} {pct:>9.0f}%")

    print("-" * 70)
    avg_post_grid = total_grid / len(post_solar)
    avg_offset = total_offset / len(post_solar)
    print(f"  {'Average':<10} {avg_post_grid:>10.0f} {avg_monthly:>16.0f} {avg_offset:>14.0f} {avg_offset/avg_monthly*100:>9.0f}%")

    print(f"\n  Solar is offsetting an estimated ~{avg_offset:.0f} kWh/month")
    print(f"  That's ~{avg_offset/30:.1f} kWh/day or ~{avg_offset/30*1000/24:.0f}W average")
    print(f"  Peak offset months (May-Jun): ~{678-222:.0f}-{720-240:.0f} kWh — this is your surplus mining window")

    # Mining economics
    print("\n" + "=" * 70)
    print("MINING ECONOMICS — Is it worth mining vs. exporting?")
    print("=" * 70)

    # We don't know export vs self-consumption split, but we can estimate
    # Solar offset = self-consumed + exported
    # Grid draw = house consumption - self-consumed
    # So: self-consumed = house consumption - grid draw = offset (same thing)
    # But there's likely MORE solar produced than just what offsets grid —
    # some is exported too. We need Enphase data for the export number.

    print(f"""
  Your rate: Standard (not TOU)
  Xcel Standard Rate: ~$0.12-0.14/kWh
  S19 mining revenue: ~$0.037/kWh consumed

  WITHOUT knowing your export volume (need Enphase data for that):
  - Every kWh you mine instead of exporting costs you ~$0.08-0.10 in lost credit
  - Mining is only net-positive vs. exporting if:
    a) You exceed the annual net metering cap (excess paid at wholesale ~$0.03/kWh)
    b) You value BTC accumulation over fiat credits
    c) You capture the heat credit (winter months)

  WITH Enphase production data, we can calculate:
  - Total solar production per month
  - How much is self-consumed vs exported
  - The exact $/kWh value of mining vs exporting for YOUR system

  >>> Run the Enphase setup to get production data:
  >>> python scripts/enphase_setup.py
""")


if __name__ == "__main__":
    main()
