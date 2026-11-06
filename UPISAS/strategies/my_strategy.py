"""Reduce exploration detours and react to traffic with more frequent rerouting."""

import requests

from UPISAS.strategy import Strategy


class MyStrategy(Strategy):
    EXPLORATION_PERCENTAGE = 0.0
    REROUTE_EVERY_TICKS = 60
    KNOBS = (
        "route_random_sigma", "exploration_percentage", "max_speed_and_length_factor",
        "average_edge_duration_factor", "freshness_update_factor", "freshness_cut_off_value",
        "re_route_every_ticks", "total_car_counter", "edge_average_influence",
    )

    def analyze(self):
        config = self.knowledge.monitored_data["configs"][-1]
        return (config["exploration_percentage"] != self.EXPLORATION_PERCENTAGE
                or config["re_route_every_ticks"] != self.REROUTE_EVERY_TICKS)

    def plan(self):
        config = self.knowledge.monitored_data["configs"][-1]
        # Send all nine controls, preserving workload and all unrelated settings.
        self.knowledge.plan_data = {key: config[key] for key in self.KNOBS}
        self.knowledge.plan_data.update(
            exploration_percentage=self.EXPLORATION_PERCENTAGE,
            re_route_every_ticks=self.REROUTE_EVERY_TICKS,
        )
        return True

    def _perform_get_request(self, endpoint_suffix):
        response = requests.get(f"{self.exemplar.base_endpoint}/{endpoint_suffix}/", timeout=5)
        response.raise_for_status()
        return response.json()

    def execute(self, adaptation=None, endpoint_suffix="execute", with_validation=False):
        # CrowdNav's schema incorrectly limits edge_average_influence to 0..1,
        # although its default is 140. Preserve the real value without that schema.
        payload = self.knowledge.plan_data if adaptation is None else adaptation
        if set(payload) != set(self.KNOBS):
            raise ValueError("Every adaptation must include all nine CrowdNav knobs")
        response = requests.put(
            f"{self.exemplar.base_endpoint}/{endpoint_suffix}/", json=payload, timeout=5,
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "success":
            raise RuntimeError(f"CrowdNav rejected adaptation: {result}")
        print(f"[Execute] Command accepted: {payload}", flush=True)
        return True
