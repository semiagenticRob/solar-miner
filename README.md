# Solar Miner

Throttle two Bitmain Antminer S19s to consume only surplus rooftop solar production. House electricity takes priority — all excess solar goes to Bitcoin mining instead of exporting to the grid at low credit rates.

## How It Works

A Python daemon reads real-time solar production from an Enphase IQ Gateway, estimates house consumption from a billing-data profile, and dynamically sets power targets on two Braiins OS+-equipped S19s. When surplus solar exceeds the threshold, miners ramp up. When it drops, they ramp down. Safety layer ensures zero grid draw for mining.

## Architecture

```
Enphase IQ Gateway ──▶ Control Daemon ──▶ S19-Alpha (Braiins OS+)
  (solar production)       (Python)    ──▶ S19-Beta  (Braiins OS+)
                              │
                        SQLite + Dashboard
```

## Setup

```bash
python3.11 -m venv .venv
source .venv/bin/activate
pip install -e .
cp config.example.yaml config.yaml
# Edit config.yaml with your IPs, token, and thresholds
python -m solar_miner.main
```

## Configuration

Copy `config.example.yaml` to `config.yaml` and fill in:
- Enphase IQ Gateway IP and API token
- Miner IPs and calibrated power ranges
- Safety buffer and control thresholds

## Rep 017 — The 100 Reps Project

This is Rep 017 of [The 100 Reps Project](https://100repsproject.com/).
