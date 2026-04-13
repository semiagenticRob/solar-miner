"""Convenience loader for consumption profiles."""

from pathlib import Path

from solar_miner.config import ConsumptionConfig
from solar_miner.consumption.profile import (
    ConsumptionProfile,
    load_profile,
    profile_from_monthly_kwh,
)


def load_consumption(config: ConsumptionConfig) -> ConsumptionProfile:
    """Load consumption profile based on config.

    If a profile YAML exists at the configured path, load it.
    Otherwise, prompt the user to generate one.
    """
    path = Path(config.profile_path)
    if path.exists():
        return load_profile(path)

    raise FileNotFoundError(
        f"Consumption profile not found at {path}. "
        "Generate one with: python -m solar_miner.consumption.profile "
        "or create it manually. See config.example.yaml for format."
    )
