"""Run a selectable CrowdNav strategy until the absolute simulation horizon."""

import argparse
import os
import time

import requests

from UPISAS.exemplars.crowdnav import CrowdNav
from UPISAS.strategies.my_strategy import (
    BaselineStrategy,
    FixedStrategy,
    ReactiveStrategy,
)


ENDPOINT = os.environ.get("CROWDNAV_ENDPOINT", "http://localhost:8080")
POLL_INTERVAL = 1  # Seconds between monitoring requests.
RUN_STEPS = 10_000
STRATEGIES = {
    "baseline": BaselineStrategy,
    "fixed": FixedStrategy,
    "reactive": ReactiveStrategy,
}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--strategy", choices=STRATEGIES, default="reactive",
        help="Strategy to run (default: reactive).",
    )
    parser.add_argument(
        "--steps", type=int, default=RUN_STEPS,
        help=f"Absolute final simulation tick (default: {RUN_STEPS}).",
    )
    args = parser.parse_args(argv)
    if args.steps <= 0:
        parser.error("--steps must be a positive integer")

    exemplar = CrowdNav(ENDPOINT)
    exemplar.start_run()
    strategy = STRATEGIES[args.strategy](exemplar)

    last_step = None
    start_step = None
    start_trips = None
    start_overhead_total = None

    print(f"Selected strategy: {args.strategy}", flush=True)

    while True:
        try:
            # Monitor without requesting and printing CrowdNav's large JSON schema.
            strategy.monitor(with_validation=False)
            stats = strategy.knowledge.monitored_data["car_stats"][-1]
            step = stats["step"]
            trips = stats["total_trips"]
            cumulative_average = stats["total_trip_overhead_average"]

            if start_step is None:
                start_step = step
                start_trips = trips
                start_overhead_total = trips * cumulative_average
                print(
                    f"Observing from step {start_step} through absolute tick {args.steps}.",
                    flush=True,
                )
                print(f"At start: {start_trips} completed trips.", flush=True)

            if step < start_step or trips < start_trips:
                raise ValueError(
                    "CrowdNav appears to have reset during the measurement. "
                    "Restart this script to take a fresh initial snapshot."
                )

            if step >= args.steps:
                interval_trips = trips - start_trips
                print(f"\nMeasurement finished at step {step} "
                      f"({step - start_step} ticks observed).", flush=True)
                print(f"Strategy: {args.strategy}", flush=True)
                print(f"Completed trips during window: {interval_trips}", flush=True)
                if interval_trips:
                    interval_average = (
                        trips * cumulative_average - start_overhead_total
                    ) / interval_trips
                    print(f"Mean trip overhead during observed window (not the grading median): "
                          f"{interval_average:.6f}", flush=True)
                else:
                    print("No trips completed during window; overhead undefined.", flush=True)
                return

            if step != last_step:
                config = strategy.knowledge.monitored_data["configs"][-1]
                print(
                    f"Step {step}: exploration={config['exploration_percentage']}, "
                    f"reroute interval={config['re_route_every_ticks']}",
                    flush=True,
                )
                if strategy.analyze() and strategy.plan():
                    strategy.execute()
                last_step = step
        except (requests.RequestException, RuntimeError) as exc:
            print(f"Request failed; retrying: {exc}", flush=True)
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
