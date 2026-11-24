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
        payload = response.json()
        if not isinstance(payload, dict):
            raise RuntimeError("Waiting for a CrowdNav monitoring snapshot")
        return payload

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


class ReactiveStrategy(MyStrategy):
    EXPLORATION_PERCENTAGE = 0.0
    INITIAL_REROUTE_TICKS = 60
    WINDOW_TICKS = 600
    MIN_COMPLETED_TRIPS = 30
    REROUTE_STEP = 15
    MIN_REROUTE_TICKS = 30
    MAX_REROUTE_TICKS = 120
    OVERHEAD_TOLERANCE = 0.05

    KNOBS = (
        "route_random_sigma", "exploration_percentage",
        "max_speed_and_length_factor", "average_edge_duration_factor",
        "freshness_update_factor", "freshness_cut_off_value",
        "re_route_every_ticks", "total_car_counter", "edge_average_influence",
    )

    def __init__(self, exemplar):
        super().__init__(exemplar)
        self.phase = "initial"
        self.window_start = None
        self.previous_overhead = None
        self.direction = -1
        self.last_step = -1

    def analyze(self):
        stats = self.knowledge.monitored_data["car_stats"][-1]
        config = self.knowledge.monitored_data["configs"][-1]
        step = stats["step"]

        if step < self.last_step:
            self.phase = "initial"
            self.window_start = None
            self.previous_overhead = None
            self.direction = -1
        self.last_step = step

        if self.phase == "initial":
            self.phase = "cooldown"
            self.window_start = self._snapshot(stats)
            self.knowledge.analysis_data["target_reroute"] = self.INITIAL_REROUTE_TICKS
            return (config["exploration_percentage"] != self.EXPLORATION_PERCENTAGE
                    or config["re_route_every_ticks"] != self.INITIAL_REROUTE_TICKS)

        start_step, start_trips, start_average = self.window_start
        if step - start_step < self.WINDOW_TICKS:
            return False

        if self.phase == "cooldown":
            self.phase = "observe"
            self.window_start = self._snapshot(stats)
            return False

        completed = stats["total_trips"] - start_trips
        if completed < self.MIN_COMPLETED_TRIPS:
            return False

        recent_overhead = (
            stats["total_trips"] * stats["total_trip_overhead_average"]
            - start_trips * start_average
        ) / completed
        self.window_start = self._snapshot(stats)
        self.knowledge.analysis_data["recent_overhead"] = recent_overhead

        if self.previous_overhead is None:
            target = config["re_route_every_ticks"] - self.REROUTE_STEP
        else:
            ratio = recent_overhead / self.previous_overhead if self.previous_overhead > 0 else 1
            if 1 - self.OVERHEAD_TOLERANCE <= ratio <= 1 + self.OVERHEAD_TOLERANCE:
                self.previous_overhead = recent_overhead
                return False
            if ratio > 1 + self.OVERHEAD_TOLERANCE:
                self.direction *= -1
            target = config["re_route_every_ticks"] + self.direction * self.REROUTE_STEP

        self.previous_overhead = recent_overhead
        target = max(self.MIN_REROUTE_TICKS, min(self.MAX_REROUTE_TICKS, target))
        if target == config["re_route_every_ticks"]:
            return False

        self.knowledge.analysis_data["target_reroute"] = target
        self.phase = "cooldown"
        return True

    @staticmethod
    def _snapshot(stats):
        return (stats["step"], stats["total_trips"], stats["total_trip_overhead_average"])

    def plan(self):
        config = self.knowledge.monitored_data["configs"][-1]
        self.knowledge.plan_data = {key: config[key] for key in self.KNOBS}
        self.knowledge.plan_data["exploration_percentage"] = self.EXPLORATION_PERCENTAGE
        self.knowledge.plan_data["re_route_every_ticks"] = self.knowledge.analysis_data["target_reroute"]
        return True

    def execute(self, adaptation=None, endpoint_suffix="execute", with_validation=False):
        payload = self.knowledge.plan_data if adaptation is None else adaptation
        if set(payload) != set(self.KNOBS):
            raise ValueError("All nine CrowdNav configuration fields are required")
        response = requests.put(
            f"{self.exemplar.base_endpoint}/{endpoint_suffix}/", json=payload, timeout=5
        )
        response.raise_for_status()
        result = response.json()
        if result.get("status") != "success":
            raise RuntimeError(f"CrowdNav rejected the configuration: {result}")
        print(f"[Execute] Configuration sent: {payload}", flush=True)
        return True
