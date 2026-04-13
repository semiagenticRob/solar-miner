#!/usr/bin/env python3
"""Enphase Cloud API setup — complete OAuth flow and pull system info.

Usage:
    python scripts/enphase_setup.py

This will:
1. Open the Enphase authorization URL in your browser
2. Ask you to paste the redirect URL (contains the auth code)
3. Exchange the code for access/refresh tokens
4. List your solar systems
5. Check if consumption data is available
6. Export recent production data for simulation
"""

import json
import sys
import webbrowser
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import yaml

from solar_miner.enphase.cloud import EnphaseCloudClient, EnphaseCloudConfig


def main():
    # Load config
    config_path = Path(__file__).parent.parent / "config.yaml"
    if not config_path.exists():
        print("ERROR: config.yaml not found. Copy config.example.yaml and fill in your Enphase credentials.")
        print("  cp config.example.yaml config.yaml")
        sys.exit(1)

    with open(config_path) as f:
        raw = yaml.safe_load(f)

    cloud_cfg = raw.get("enphase_cloud")
    if not cloud_cfg:
        print("ERROR: No 'enphase_cloud' section in config.yaml. Add:")
        print("""
enphase_cloud:
  api_key: "YOUR_API_KEY"
  client_id: "YOUR_CLIENT_ID"
  client_secret: "YOUR_CLIENT_SECRET"
  token_path: "./data/enphase_tokens.json"
""")
        sys.exit(1)

    config = EnphaseCloudConfig(**cloud_cfg)
    client = EnphaseCloudClient(config)

    # Check if we already have tokens
    token_path = Path(config.token_path)
    if token_path.exists():
        print(f"Found existing tokens at {token_path}")
        try:
            systems = client.get_systems()
            print(f"Token is valid — found {len(systems)} system(s)")
            _explore_systems(client, systems)
            return
        except Exception as e:
            print(f"Existing token failed ({e}) — re-authorizing...")

    # OAuth flow
    auth_url = client.get_authorization_url()
    print("\n=== Enphase OAuth Authorization ===\n")
    print("Opening your browser to authorize Solar Miner...\n")
    print(f"URL: {auth_url}\n")
    webbrowser.open(auth_url)

    print("After authorizing, Enphase will redirect you to a URL.")
    print("Copy the ENTIRE redirect URL and paste it here.\n")
    redirect_url = input("Paste redirect URL: ").strip()

    # Extract auth code from redirect URL
    if "code=" in redirect_url:
        code = redirect_url.split("code=")[1].split("&")[0]
    else:
        code = redirect_url  # Maybe they just pasted the code

    print(f"\nExchanging code for tokens...")
    try:
        tokens = client.exchange_code(code)
        print(f"Success! Tokens saved to {config.token_path}")
    except Exception as e:
        print(f"ERROR: Token exchange failed: {e}")
        sys.exit(1)

    # Explore systems
    print("\nFetching your solar systems...")
    systems = client.get_systems()
    _explore_systems(client, systems)


def _explore_systems(client: EnphaseCloudClient, systems: list[dict]):
    """Explore systems, check for consumption data, export production."""
    if not systems:
        print("No systems found on your Enphase account.")
        return

    for sys_info in systems:
        sys_id = sys_info.get("system_id")
        name = sys_info.get("system_name", "Unknown")
        status = sys_info.get("status", "unknown")
        print(f"\n{'='*60}")
        print(f"System: {name} (ID: {sys_id}, Status: {status})")
        print(f"{'='*60}")

        # Get summary
        try:
            summary = client.get_system_summary(sys_id)
            print(f"  Modules: {summary.get('modules', 'N/A')}")
            print(f"  Size: {summary.get('size_w', 'N/A')}W")
            print(f"  Current power: {summary.get('current_power', 'N/A')}W")
            print(f"  Energy today: {summary.get('energy_today', 'N/A')} Wh")
            print(f"  Energy lifetime: {summary.get('energy_lifetime', 'N/A')} Wh")
        except Exception as e:
            print(f"  Summary unavailable: {e}")

        # Check consumption data
        print("\n  Checking consumption monitoring...")
        try:
            now = int(datetime.now().timestamp())
            day_ago = int((datetime.now() - timedelta(days=1)).timestamp())
            consumption = client.get_consumption_stats(sys_id, start_at=day_ago, end_at=now)
            if consumption:
                print(f"  CONSUMPTION DATA AVAILABLE — {len(consumption)} intervals found")
                avg_w = sum(i.get("enwh", 0) for i in consumption) / max(len(consumption), 1) * 4
                print(f"  Average consumption (last 24h): ~{avg_w:.0f}W")
                print("  >>> You have consumption CTs! The daemon can use real-time consumption data.")
            else:
                print("  No consumption data returned — CTs likely not installed.")
                print("  The daemon will use the billing-data consumption model (MVP approach).")
        except Exception as e:
            print(f"  Consumption check failed: {e}")

        # Export recent production data
        print("\n  Exporting last 7 days of production data for simulation...")
        try:
            week_ago = int((datetime.now() - timedelta(days=7)).timestamp())
            production = client.get_production_stats(sys_id, start_at=week_ago, end_at=now)
            if production:
                export_path = Path("data") / f"production_system_{sys_id}.json"
                export_path.parent.mkdir(parents=True, exist_ok=True)
                with open(export_path, "w") as f:
                    json.dump({"system_id": sys_id, "intervals": production}, f, indent=2)
                print(f"  Exported {len(production)} intervals to {export_path}")

                # Quick stats
                total_wh = sum(i.get("enwh", 0) for i in production)
                peak_w = max(i.get("enwh", 0) * 4 for i in production)
                print(f"  Total production (7d): {total_wh / 1000:.1f} kWh")
                print(f"  Peak interval power: ~{peak_w:.0f}W")
            else:
                print("  No production data returned.")
        except Exception as e:
            print(f"  Production export failed: {e}")

    print("\n" + "=" * 60)
    print("Setup complete. Next steps:")
    print("  1. If consumption data is available, update config.yaml:")
    print('     consumption.source: "enphase"')
    print("  2. Run simulation: python scripts/simulate.py --solar data/production_*.json --monthly-kwh 900")
    print("  3. Set up local IQ Gateway access for real-time control")
    print("=" * 60)


if __name__ == "__main__":
    main()
