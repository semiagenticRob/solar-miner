"""Solar Miner — main control loop.

Reads solar production from Enphase, estimates consumption from billing data,
calculates surplus, and throttles S19 miners via Braiins OS+ API.
"""

import argparse
import logging
import signal
import sys
import time
from pathlib import Path

from solar_miner.braiins.client import BraiinsClient
from solar_miner.config import load_config
from solar_miner.consumption.loader import load_consumption
from solar_miner.controller.safety import SafetyCheck
from solar_miner.controller.smoother import AsymmetricEMA
from solar_miner.controller.throttle import ThrottleController
from solar_miner.enphase.client import EnphaseClient
from solar_miner.storage.db import init_db, log_reading, get_today_stats

logger = logging.getLogger("solar_miner")

# Graceful shutdown
_running = True


def _signal_handler(sig, frame):
    global _running
    logger.info("Received signal %s — shutting down gracefully", sig)
    _running = False


def main():
    parser = argparse.ArgumentParser(description="Solar Miner control daemon")
    parser.add_argument(
        "--config", "-c",
        default="config.yaml",
        help="Path to config file (default: config.yaml)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Read sensors and calculate targets but don't send commands to miners",
    )
    args = parser.parse_args()

    # Load config
    config = load_config(args.config)

    # Setup logging
    logging.basicConfig(
        level=getattr(logging, config.logging.level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if args.dry_run:
        logger.info("=== DRY RUN MODE — no commands will be sent to miners ===")

    # Init components
    logger.info("Loading consumption profile...")
    consumption = load_consumption(config.consumption)
    logger.info("Consumption profile loaded (source: %s)", consumption.source)

    logger.info("Connecting to Enphase IQ Gateway at %s...", config.enphase.gateway_ip)
    enphase = EnphaseClient(config.enphase.gateway_ip, config.enphase.token)

    logger.info("Connecting to %d miner(s)...", len(config.miners))
    miner_clients: dict[str, BraiinsClient] = {}
    for m in config.miners:
        client = BraiinsClient(m.name, m.ip)
        miner_clients[m.name] = client
        reachable = client.is_reachable
        logger.info("  %s (%s): %s", m.name, m.ip, "OK" if reachable else "UNREACHABLE")

    db = init_db(config.logging.db_path)
    smoother = AsymmetricEMA(
        window_seconds=config.control.smoothing_window_seconds,
        poll_interval=config.enphase.poll_interval_seconds,
    )
    safety = SafetyCheck(max_grid_draw_watts=config.control.max_grid_draw_watts)
    throttle = ThrottleController(config.miners, config.control)

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    logger.info("Solar Miner daemon started. Poll interval: %ds, Ramp interval: %ds",
                config.enphase.poll_interval_seconds, config.control.ramp_interval_seconds)

    last_ramp_time = 0.0
    poll_count = 0

    try:
        while _running:
            loop_start = time.monotonic()
            poll_count += 1

            # 1. Read solar production
            api_success = True
            try:
                reading = enphase.read_meters()
                solar_w = reading.production_watts
            except Exception as e:
                logger.error("Enphase read failed: %s", e)
                solar_w = 0.0
                api_success = False

            # 2. Get consumption estimate
            if reading.consumption_watts is not None:
                # Real-time CTs available
                house_w = reading.consumption_watts
                consumption_source = "enphase_ct"
            else:
                # Use billing-data profile
                house_w = consumption.get_current_watts()
                consumption_source = consumption.source

            # 3. Calculate surplus
            raw_surplus = solar_w - house_w
            smoothed_surplus = smoother.update(raw_surplus)
            available = smoothed_surplus - config.control.safety_buffer_watts

            # 4. Safety check
            safety_action = safety.evaluate(solar_w, raw_surplus, api_success)

            # Get current miner states for logging
            alpha_target = 0.0
            alpha_state = "off"
            beta_target = 0.0
            beta_state = "off"

            # 5. Act on safety or throttle
            if safety_action.action == "shutdown":
                if not args.dry_run:
                    throttle.force_shutdown(miner_clients)
                logger.warning("SAFETY: %s — all miners shut down", safety_action.reason)
            elif safety_action.action == "drop_to_min":
                if not args.dry_run:
                    throttle.force_drop_to_min(miner_clients)
                logger.warning("SAFETY: %s — miners dropped to minimum", safety_action.reason)
            elif safety_action.action == "decrement":
                # Let the throttle handle it with reduced available
                available = max(0, available - config.miners[0].ramp_step_watts)
            # else: "ok" — normal operation

            # 6. Throttle calculation (at ramp interval)
            now = time.monotonic()
            if now - last_ramp_time >= config.control.ramp_interval_seconds:
                if safety_action.action == "ok" or safety_action.action == "decrement":
                    decision = throttle.calculate(available)
                    if not args.dry_run:
                        throttle.apply(decision, miner_clients)

                    # Extract targets for logging
                    for t in decision.targets:
                        if t.name == config.miners[0].name:
                            alpha_target = t.target_watts
                            alpha_state = t.state.value
                        elif len(config.miners) > 1 and t.name == config.miners[1].name:
                            beta_target = t.target_watts
                            beta_state = t.state.value

                    if poll_count % 4 == 0:  # Log every ~1 min at 15s polls
                        logger.info(
                            "Solar: %.0fW | House: %.0fW (%s) | Surplus: %.0fW (smoothed: %.0fW) | "
                            "Mining: %s=%.0fW %s=%.0fW | %s",
                            solar_w, house_w, consumption_source,
                            raw_surplus, smoothed_surplus,
                            config.miners[0].name, alpha_target,
                            config.miners[1].name if len(config.miners) > 1 else "n/a", beta_target,
                            decision.reason,
                        )
                last_ramp_time = now

            # 7. Log to SQLite
            log_reading(
                db,
                solar_w=solar_w,
                house_w=house_w,
                consumption_source=consumption_source,
                net_surplus_w=raw_surplus,
                smoothed_w=smoothed_surplus,
                available_w=available,
                alpha_target_w=alpha_target,
                alpha_state=alpha_state,
                beta_target_w=beta_target,
                beta_state=beta_state,
                safety_action=safety_action.action,
                safety_reason=safety_action.reason,
            )

            # 8. Print today's stats periodically
            if poll_count % 40 == 0:  # Every ~10 min
                stats = get_today_stats(db)
                logger.info("Today: %d readings, avg solar %.0fW, avg mining %.0fW",
                            stats["readings"], stats["avg_solar_w"], stats["avg_mining_w"])

            # Sleep until next poll
            elapsed = time.monotonic() - loop_start
            sleep_time = max(0, config.enphase.poll_interval_seconds - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

    finally:
        logger.info("Shutting down — setting all miners to 0W")
        if not args.dry_run:
            for client in miner_clients.values():
                try:
                    client.set_power_target(0)
                except Exception:
                    pass

        for client in miner_clients.values():
            client.close()
        enphase.close()
        db.close()
        logger.info("Solar Miner daemon stopped.")


if __name__ == "__main__":
    main()
