"""Measure transport speed and verify identical fixed-policy trip outcomes."""

import argparse
import hashlib
import json
from pathlib import Path
import time

from training_runtime import DockerRun, write_json


def measure(output, fast, seed, ticks):
    started = time.monotonic()
    run = DockerRun(output, seed, ticks, "crowdnav-sac/simulation:local", "crowdnav-sac/api:local",
                    fast_poll=fast)
    exemplar = None
    try:
        exemplar = run.start()
        run.wait_finished(timeout=900)
    finally:
        if exemplar is not None:
            exemplar.close()
        run.close()
    result = json.loads((output / "result.json").read_text())
    events = (output / "trips.jsonl").read_bytes()
    return dict(wall_seconds=time.monotonic() - started, trip_count=result["trip_count"],
                trip_events_sha256=hashlib.sha256(events).hexdigest(), fast_command_poll=fast)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ticks", type=int, default=3000)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    baseline = measure(args.output / "original", False, 104, args.ticks)
    print("Original:", baseline, flush=True)
    optimized = measure(args.output / "fast", True, 104, args.ticks)
    report = dict(original=baseline, optimized=optimized,
                  speedup=baseline["wall_seconds"] / optimized["wall_seconds"],
                  identical_trip_events=baseline["trip_events_sha256"] == optimized["trip_events_sha256"])
    write_json(args.output / "benchmark.json", report)
    if not report["identical_trip_events"]:
        raise ValueError("Transport optimization changed fixed-policy trip events")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    main()
