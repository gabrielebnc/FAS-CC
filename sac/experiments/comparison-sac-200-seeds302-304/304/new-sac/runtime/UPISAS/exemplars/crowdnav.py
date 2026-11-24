"""HTTP access to CrowdNav. The caller owns the simulator's lifecycle."""

import requests


class CrowdNav:
    def __init__(self, base_endpoint="http://localhost:8080"):
        self.base_endpoint = base_endpoint.rstrip("/")
        self.session = requests.Session()

    def snapshot(self):
        response = self.session.get(f"{self.base_endpoint}/monitor/", timeout=5)
        response.raise_for_status()
        data = response.json()
        if data is None:
            return None  # The API can be ready before the first Kafka snapshot.
        if not isinstance(data, dict) or not all(
            isinstance(data.get(key), dict) for key in ("car_stats", "configs")
        ):
            raise ValueError("Malformed CrowdNav monitoring snapshot")
        return data

    def apply(self, configuration):
        # The upstream schema incorrectly limits edge_average_influence to 1.
        # The strategy validates the real nine-knob payload instead.
        response = self.session.put(
            f"{self.base_endpoint}/execute/", json=configuration, timeout=5,
        )
        response.raise_for_status()
        result = response.json()
        if not isinstance(result, dict) or result.get("status") != "success":
            raise RuntimeError(f"CrowdNav did not accept the command: {result}")

    def close(self):
        self.session.close()
