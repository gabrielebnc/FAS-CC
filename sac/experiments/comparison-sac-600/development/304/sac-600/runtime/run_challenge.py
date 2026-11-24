"""Run a frozen, exported SAC actor against an externally started CrowdNav."""

import argparse
import json
import os
from pathlib import Path
import time

import requests

from UPISAS.exemplars.crowdnav import CrowdNav
from UPISAS.strategies.my_strategy import FrozenActor, SACStrategy


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=os.environ.get("CROWDNAV_ENDPOINT", "http://localhost:8080"))
    parser.add_argument("--steps", type=int, default=10_000)
    parser.add_argument("--checkpoint", type=Path, default=Path(__file__).resolve().parent / "checkpoint.json")
    parser.add_argument("--log", type=Path, default=Path("sac-decisions.jsonl"))
    args = parser.parse_args(argv)
    if args.steps <= 0:
        parser.error("--steps must be positive")
    actor = FrozenActor.load(args.checkpoint)
    args.log.parent.mkdir(parents=True, exist_ok=True)
    exemplar = CrowdNav(args.endpoint)
    with args.log.open("w", buffering=1) as stream:
        def log(record):
            stream.write(json.dumps(record, allow_nan=False) + "\n")
        strategy = SACStrategy(exemplar, actor.settings, actor, log)
        last_step, last_progress = None, time.monotonic()
        try:
            while True:
                try:
                    if strategy.monitor():
                        strategy.analyze()
                        step = strategy.latest.step
                        if step != last_step:
                            last_step, last_progress = step, time.monotonic()
                        if step >= args.steps:
                            if strategy.pending:
                                log(dict(event="incomplete_final_window", step=step))
                            return
                        # Reserve application time too; hold through the remaining tail.
                        reserve = actor.settings.settlement_ticks + actor.settings.window_ticks + 600
                        if strategy.phase == "ready" and step + reserve < args.steps:
                            strategy.plan()
                        strategy.execute()
                except requests.RequestException as exc:
                    log(dict(event="http_error", error=str(exc)))
                if time.monotonic() - last_progress > 120:
                    raise TimeoutError("CrowdNav monitoring made no progress for 120 seconds")
                time.sleep(0.05)
        finally:
            exemplar.close()


if __name__ == "__main__":
    main()
