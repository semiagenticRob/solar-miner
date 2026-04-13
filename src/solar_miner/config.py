"""Load and validate config.yaml."""

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class EnphaseConfig:
    gateway_ip: str
    token: str
    token_expiry: str = ""
    poll_interval_seconds: int = 15


@dataclass
class MinerConfig:
    name: str
    ip: str
    min_power_watts: int = 1800
    max_power_watts: int = 3250
    ramp_step_watts: int = 200


@dataclass
class ControlConfig:
    safety_buffer_watts: int = 400
    min_surplus_to_start_watts: int = 2000
    smoothing_window_seconds: int = 120
    ramp_interval_seconds: int = 30
    max_grid_draw_watts: int = 50
    night_mode: str = "shutdown"


@dataclass
class ConsumptionConfig:
    source: str = "profile"  # "profile" or "enphase"
    profile_path: str = "./data/consumption_profile.yaml"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    db_path: str = "./data/solar_miner.db"


@dataclass
class NotificationConfig:
    enabled: bool = False
    service: str = "ntfy"
    topic: str = "solar-miner"


@dataclass
class DashboardConfig:
    enabled: bool = False
    port: int = 8080


@dataclass
class Config:
    enphase: EnphaseConfig
    miners: list[MinerConfig]
    control: ControlConfig = field(default_factory=ControlConfig)
    consumption: ConsumptionConfig = field(default_factory=ConsumptionConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    notifications: NotificationConfig = field(default_factory=NotificationConfig)
    dashboard: DashboardConfig = field(default_factory=DashboardConfig)


def load_config(path: str | Path = "config.yaml") -> Config:
    """Load config from YAML file."""
    with open(path) as f:
        raw = yaml.safe_load(f)

    enphase = EnphaseConfig(**raw["enphase"])
    miners = [MinerConfig(**m) for m in raw["miners"]]
    control = ControlConfig(**raw.get("control", {}))
    consumption = ConsumptionConfig(**raw.get("consumption", {}))
    logging_cfg = LoggingConfig(**raw.get("logging", {}))
    notifications = NotificationConfig(**raw.get("notifications", {}))
    dashboard = DashboardConfig(**raw.get("dashboard", {}))

    return Config(
        enphase=enphase,
        miners=miners,
        control=control,
        consumption=consumption,
        logging=logging_cfg,
        notifications=notifications,
        dashboard=dashboard,
    )
