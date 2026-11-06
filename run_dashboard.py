"""Read-only telemetry process; runs independently of any strategy."""

import os

from UPISAS.dashboard import Dashboard


ENDPOINT = os.environ.get("CROWDNAV_ENDPOINT", "http://localhost:8080")
SAMPLE_INTERVAL = 3
DASHBOARD_PORT = 8090


def main():
    dashboard = Dashboard(ENDPOINT, interval=SAMPLE_INTERVAL, port=DASHBOARD_PORT)
    dashboard.start()
    print(f"Dashboard: http://localhost:{DASHBOARD_PORT}/dashboard", flush=True)
    try:
        dashboard.stop.wait()
    finally:
        dashboard.close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
