"""Connect to the externally managed CrowdNav simulation."""

import time

import requests


class CrowdNav:
    def __init__(self, base_endpoint="http://localhost:8080"):
        self.base_endpoint = base_endpoint.rstrip("/")

    def start_run(self, timeout=120):
        """Wait for monitoring data; Docker Compose owns the simulation lifecycle."""
        deadline = time.monotonic() + timeout
        reason = "No monitoring snapshot yet"
        while time.monotonic() < deadline:
            try:
                response = requests.get(f"{self.base_endpoint}/monitor/", timeout=5)
                response.raise_for_status()
                data = response.json()
                if (isinstance(data, dict)
                        and isinstance(data.get("car_stats"), dict)
                        and "step" in data["car_stats"]
                        and isinstance(data.get("configs"), dict)):
                    return data
            except (requests.RequestException, ValueError) as exc:
                reason = str(exc)
            print(f"Waiting for CrowdNav: {reason}", flush=True)
            time.sleep(1)
        raise TimeoutError(f"CrowdNav did not become ready at {self.base_endpoint}: {reason}")
