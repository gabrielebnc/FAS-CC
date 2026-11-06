import os

import requests

from UPISAS.exemplars.crowdnav import CrowdNav
from UPISAS.dashboard import Dashboard
from UPISAS.strategies.my_strategy import MyStrategy


STRATEGY = MyStrategy
ENDPOINT = os.environ.get("CROWDNAV_ENDPOINT", "http://localhost:8080")
SAMPLE_INTERVAL = 3
DASHBOARD_PORT = 8090


def main():
    target = CrowdNav(ENDPOINT)
    strategy = STRATEGY(target)
    dashboard = Dashboard(target.base_endpoint, interval=SAMPLE_INTERVAL,
                          port=DASHBOARD_PORT)
    dashboard.start()
    print(f"Dashboard: http://localhost:{DASHBOARD_PORT}/dashboard", flush=True)
    try:
        try:
            run_strategy(strategy, target, dashboard.stop)
        except Exception as exc:
            dashboard.history.set_strategy_error(f"{type(exc).__name__}: {exc}")
            print(f"Strategy stopped: {exc}. Dashboard remains available.", flush=True)
            dashboard.stop.wait()
    finally:
        dashboard.close()


def run_strategy(strategy, target, stop):

    # Kafka and the API may still be starting, or monitoring may initially be null.
    while not stop.is_set():
        try:
            response = requests.get(f"{target.base_endpoint}/monitor/", timeout=5)
            response.raise_for_status()
            if isinstance(response.json(), dict):
                break
            reason = "API is reachable but has not received a monitoring snapshot yet"
        except requests.RequestException as exc:
            reason = str(exc)
        print(f"Waiting for {target.base_endpoint}/monitor/: {reason}", flush=True)
        if stop.wait(3):
            return

    while not stop.is_set():
        strategy.monitor()
        print(strategy.knowledge.monitored_data["car_stats"][-1], flush=True)
        if strategy.analyze() and strategy.plan():
            strategy.execute()
        if stop.wait(3):
            return


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
