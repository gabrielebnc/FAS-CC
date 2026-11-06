import os
import time

from UPISAS.exemplars.crowdnav import CrowdNav
from UPISAS.strategies.my_strategy import MyStrategy


STRATEGY = MyStrategy
ENDPOINT = os.environ.get("CROWDNAV_ENDPOINT", "http://localhost:8080")
POLL_INTERVAL = 3


def main():
    target = CrowdNav(ENDPOINT)
    target.start_run()
    strategy = STRATEGY(target)

    while True:
        strategy.monitor()
        print(strategy.knowledge.monitored_data["car_stats"][-1], flush=True)
        if strategy.analyze() and strategy.plan():
            strategy.execute()
        time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
