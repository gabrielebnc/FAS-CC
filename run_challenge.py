"""Run the submission strategy against an already-started CrowdNav instance."""

import os
import time

import requests

from UPISAS.exemplars.crowdnav import CrowdNav
from UPISAS.strategies.my_strategy import MyStrategy


ENDPOINT = os.environ.get("CROWDNAV_ENDPOINT", "http://localhost:8080")
POLL_INTERVAL = 3
FINAL_STEP = 10_000


def main():
    exemplar = CrowdNav(ENDPOINT)
    exemplar.start_run()
    strategy = MyStrategy(exemplar)
    last_step = None

    while True:
        try:
            strategy.monitor()
            stats = strategy.knowledge.monitored_data["car_stats"][-1]
            step = stats["step"]
            if step >= FINAL_STEP:
                print(f"Finished at step {step} (target: {FINAL_STEP}).", flush=True)
                return
            if step != last_step:
                config = strategy.knowledge.monitored_data["configs"][-1]
                print(f"Step {step}: exploration={config['exploration_percentage']}, "
                      f"reroute interval={config['re_route_every_ticks']}", flush=True)
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
