"""Strategies for evaluating CrowdNav configurations through UPISAS."""

import json
import math

import requests

from UPISAS.strategy import Strategy


class FixedStrategy(Strategy):

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
        # CrowdNav's schema limits edge_average_influence to 0..1 even though its default is 140.
        # Send the complete config without that incorrect execute-schema validation.
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


class BaselineStrategy(FixedStrategy):

    BASELINE_CONFIG = {
        "route_random_sigma": 0.2,
        "exploration_percentage": 0.5,
        "max_speed_and_length_factor": 1,
        "average_edge_duration_factor": 1,
        "freshness_update_factor": 10,
        "freshness_cut_off_value": 90,
        "re_route_every_ticks": 600,
        "edge_average_influence": 140,
    }

    def analyze(self):
        config = self.knowledge.monitored_data["configs"][-1]
        return any(config[key] != value for key, value in self.BASELINE_CONFIG.items())

    def plan(self):
        config = self.knowledge.monitored_data["configs"][-1]
        self.knowledge.plan_data = {key: config[key] for key in self.KNOBS}
        self.knowledge.plan_data.update(self.BASELINE_CONFIG)
        return True


class RuleBasedStrategy(FixedStrategy):
    """Opt-in experiment: review overhead every 200 ticks and select three rules.

    Start at 0% exploration / 60-tick rerouting. Then use 0% / 30 when
    overhead is high, 5% / 90 when low, and 0% / 60 in between. All other
    controls, including the current car population, are preserved.
    """

    WINDOW_TICKS = 200
    MIN_COMPLETED_TRIPS = 10
    LOW_OVERHEAD = 2.0
    HIGH_OVERHEAD = 2.5
    APPLICATION_RETRY_TICKS = 200

    def __init__(self, exemplar):
        super().__init__(exemplar)
        self._reset()

    def _reset(self):
        self.window_start = None
        self.next_review = None
        self.target = None
        self.command_step = None
        self.last_step = -1
        self.last_trips = -1

    @staticmethod
    def _snapshot(stats):
        # CrowdNav increments totalTrips BEFORE addToAverage(n, average, x),
        # so average = exact_sum / (n + 1), rather than exact_sum / n.
        trips = stats["total_trips"]
        return (stats["step"], trips,
                (trips + 1) * stats["total_trip_overhead_average"])

    def _start_window(self, stats):
        self.window_start = self._snapshot(stats)
        self.next_review = (stats["step"] // self.WINDOW_TICKS + 1) * self.WINDOW_TICKS

    def _request(self, exploration, reroute):
        self.target = {"exploration_percentage": exploration,
                       "re_route_every_ticks": reroute}
        self.command_step = None
        return True

    def analyze(self):
        stats = self.knowledge.monitored_data["car_stats"][-1]
        config = self.knowledge.monitored_data["configs"][-1]
        step, trips = stats["step"], stats["total_trips"]
        if step < self.last_step or trips < self.last_trips:
            self._reset()
        self.last_step, self.last_trips = step, trips

        if self.target is not None:
            if all(config[key] == value for key, value in self.target.items()):
                self.target = None
                self.command_step = None
                self._start_window(stats)
                return False
            # HTTP success confirms queue acceptance, not application in SUMO.
            return (self.command_step is None
                    or step - self.command_step >= self.APPLICATION_RETRY_TICKS)

        if self.window_start is None:
            if config["exploration_percentage"] != 0 or config["re_route_every_ticks"] != 60:
                return self._request(0.0, 60)
            self._start_window(stats)
            return False

        if step < self.next_review:
            return False

        start_step, start_trips, start_sum = self.window_start
        scheduled_step = self.next_review
        self.next_review = (step // self.WINDOW_TICKS + 1) * self.WINDOW_TICKS
        completed = trips - start_trips
        if completed < self.MIN_COMPLETED_TRIPS:
            # Keep accumulating evidence, but review only at the next boundary.
            self._log(step, scheduled_step, start_step, completed, None, "insufficient_trips", config)
            return False

        recent = ((trips + 1) * stats["total_trip_overhead_average"] - start_sum) / completed
        if not math.isfinite(recent) or recent < 0:
            raise ValueError("Invalid recent trip overhead; check CrowdNav aggregation")
        if recent >= self.HIGH_OVERHEAD:
            exploration, reroute, rule = 0.0, 30, "high_overhead"
        elif recent <= self.LOW_OVERHEAD:
            exploration, reroute, rule = 0.05, 90, "low_overhead"
        else:
            exploration, reroute, rule = 0.0, 60, "moderate_overhead"
        self.knowledge.analysis_data["recent_overhead"] = recent
        self.window_start = self._snapshot(stats)
        target = {**config, "exploration_percentage": exploration, "re_route_every_ticks": reroute}
        self._log(step, scheduled_step, start_step, completed, recent, rule, target)
        if all(config[key] == target[key] for key in ("exploration_percentage", "re_route_every_ticks")):
            return False
        return self._request(exploration, reroute)

    @staticmethod
    def _log(step, scheduled, start, trips, overhead, rule, config):
        print("[RuleBased] " + json.dumps({
            "step": step, "scheduled_step": scheduled, "window_start": start,
            "completed_trips": trips, "recent_overhead": overhead, "rule": rule,
            "exploration_percentage": config["exploration_percentage"],
            "re_route_every_ticks": config["re_route_every_ticks"],
        }, allow_nan=False), flush=True)

    def plan(self):
        if self.target is None:
            return False
        config = self.knowledge.monitored_data["configs"][-1]
        self.knowledge.plan_data = {key: config[key] for key in self.KNOBS}
        self.knowledge.plan_data.update(self.target)
        return True

    def execute(self, adaptation=None, endpoint_suffix="execute", with_validation=False):
        result = super().execute(adaptation, endpoint_suffix, with_validation)
        self.command_step = self.last_step
        return result


class ReactiveStrategy(FixedStrategy):
    """Adapt reroute frequency in response to recent completed-trip overhead."""

    EXPLORATION_PERCENTAGE = 0.0
    INITIAL_REROUTE_TICKS = 60
    WINDOW_TICKS = 600
    MIN_COMPLETED_TRIPS = 30
    REROUTE_STEP = 15
    MIN_REROUTE_TICKS = 30
    MAX_REROUTE_TICKS = 120
    OVERHEAD_TOLERANCE = 0.05

    def __init__(self, exemplar):
        super().__init__(exemplar)
        self.phase = "initial"
        self.window_start = None
        self.reference_overhead = None
        self.previous_reroute = None
        self.target_reroute = None
        self.trial_active = False
        self.direction = -1
        self.last_step = -1

    def analyze(self):
        stats = self.knowledge.monitored_data["car_stats"][-1]
        config = self.knowledge.monitored_data["configs"][-1]
        step = stats["step"]
        current_reroute = config["re_route_every_ticks"]

        if step < self.last_step:
            self.phase = "initial"
            self.window_start = None
            self.reference_overhead = None
            self.previous_reroute = None
            self.target_reroute = None
            self.trial_active = False
            self.direction = -1
        self.last_step = step

        if self.phase == "initial":
            if (config["exploration_percentage"] != self.EXPLORATION_PERCENTAGE
                    or current_reroute != self.INITIAL_REROUTE_TICKS):
                return self._request_change(self.INITIAL_REROUTE_TICKS)
            self.phase = "cooldown"
            self.window_start = self._snapshot(stats)
            return False

        if self.phase == "request_change":
            # Keep a failed HTTP command retryable before waiting for application.
            return True

        if self.phase == "await_application":
            if (current_reroute == self.target_reroute
                    and config["exploration_percentage"] == self.EXPLORATION_PERCENTAGE):
                self.phase = "cooldown"
                self.window_start = self._snapshot(stats)
            return False

        start_step, start_trips, start_average = self.window_start
        if step - start_step < self.WINDOW_TICKS:
            return False

        if self.phase == "cooldown":
            self.phase = "observe"
            self.window_start = self._snapshot(stats)
            return False

        completed_trips = stats["total_trips"] - start_trips
        if completed_trips < self.MIN_COMPLETED_TRIPS:
            return False

        recent_overhead = (
            stats["total_trips"] * stats["total_trip_overhead_average"]
            - start_trips * start_average
        ) / completed_trips
        self.window_start = self._snapshot(stats)
        self.knowledge.analysis_data["recent_overhead"] = recent_overhead

        if self.trial_active:
            relative_change = (
                recent_overhead / self.reference_overhead - 1
                if self.reference_overhead > 0 else 0
            )
            self.trial_active = False
            if relative_change > self.OVERHEAD_TOLERANCE:
                self.direction *= -1
                return self._request_change(self.previous_reroute)

            self.reference_overhead = recent_overhead
            if relative_change >= -self.OVERHEAD_TOLERANCE:
                return False
        else:
            self.reference_overhead = recent_overhead

        target = max(self.MIN_REROUTE_TICKS,
                     min(self.MAX_REROUTE_TICKS,
                         current_reroute + self.direction * self.REROUTE_STEP))
        if target == current_reroute:
            self.direction *= -1
            target = max(self.MIN_REROUTE_TICKS,
                         min(self.MAX_REROUTE_TICKS,
                             current_reroute + self.direction * self.REROUTE_STEP))
        if target == current_reroute:
            return False

        self.previous_reroute = current_reroute
        self.trial_active = True
        return self._request_change(target)

    def plan(self):
        config = self.knowledge.monitored_data["configs"][-1]
        self.knowledge.plan_data = {key: config[key] for key in self.KNOBS}
        self.knowledge.plan_data["exploration_percentage"] = self.EXPLORATION_PERCENTAGE
        self.knowledge.plan_data["re_route_every_ticks"] = self.knowledge.analysis_data["target_reroute"]
        return True

    @staticmethod
    def _snapshot(stats):
        return (stats["step"], stats["total_trips"],
                stats["total_trip_overhead_average"])

    def _request_change(self, target):
        self.target_reroute = target
        self.phase = "request_change"
        self.knowledge.analysis_data["target_reroute"] = target
        return True

    def execute(self, adaptation=None, endpoint_suffix="execute", with_validation=False):
        result = super().execute(adaptation, endpoint_suffix, with_validation)
        self.phase = "await_application"
        return result
