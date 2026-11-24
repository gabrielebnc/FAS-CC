"""SAC's CrowdNav measurement contract and portable deployed actor.

Learning belongs to training.py. This file knows only about observations,
actions, application confirmation, settled windows, and the exported actor.
"""

from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import requests

from UPISAS.strategy import Strategy


KNOBS = (
    "route_random_sigma", "exploration_percentage", "max_speed_and_length_factor",
    "average_edge_duration_factor", "freshness_update_factor", "freshness_cut_off_value",
    "re_route_every_ticks", "total_car_counter", "edge_average_influence",
)
CONTROLLED = ("exploration_percentage", "re_route_every_ticks")
FEATURES = ("overhead", "completion_rate", "population", "population_trend",
            "exploration", "rerouting")


@dataclass(frozen=True)
class Settings:
    settlement_ticks: int = 1800
    window_ticks: int = 600
    warmup_ticks: int = 200
    overhead_scale: float = 3.0
    rate_scale: float = 0.3
    rate_floor: float = 0.2
    population_scale: int = 750
    trend_ticks: int = 600
    exploration_max: float = 0.5
    reroute_min: int = 30
    reroute_max: int = 600
    throughput_weight: float = 1.0
    retry_ticks: int = 200
    max_attempts: int = 3
    version: str = "relative-overhead-v1"
    calibration: str = "pilot-only-unvalidated"
    zero_exploration_for_negative_actions: bool = False
    reroute_midpoint: int | None = None

    def __post_init__(self):
        for key, value in asdict(self).items():
            if isinstance(value, (int, float)) and (not math.isfinite(value) or value < 0):
                raise ValueError(f"Invalid setting {key}: {value}")
        for key in ("settlement_ticks", "window_ticks", "population_scale", "trend_ticks",
                    "retry_ticks", "max_attempts", "reroute_min", "reroute_max"):
            value = getattr(self, key)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{key} must be a positive integer")
        if min(self.overhead_scale, self.rate_scale, self.rate_floor) <= 0:
            raise ValueError("Observation scales and throughput floor must be positive")
        if not 0 < self.exploration_max <= 1 or self.reroute_min >= self.reroute_max:
            raise ValueError("Invalid action bounds")
        if self.reroute_midpoint is not None and not self.reroute_min < self.reroute_midpoint < self.reroute_max:
            raise ValueError("Reroute midpoint must be inside the action bounds")


def action_configuration(action, current, settings):
    if len(action) != 2 or any(not math.isfinite(float(x)) or not -1 <= x <= 1 for x in action):
        raise ValueError("SAC action must contain two finite values in [-1, 1]")
    config = dict(current)
    u, v = map(float, action)
    share = max(0.0, u) if settings.zero_exploration_for_negative_actions else (u + 1) / 2
    config[CONTROLLED[0]] = settings.exploration_max * share
    if settings.reroute_midpoint is None:
        interval = math.exp(math.log(settings.reroute_min) + (v + 1) / 2
                            * math.log(settings.reroute_max / settings.reroute_min))
    else:
        endpoint = settings.reroute_min if v < 0 else settings.reroute_max
        interval = settings.reroute_midpoint * (endpoint / settings.reroute_midpoint) ** abs(v)
    config[CONTROLLED[1]] = max(settings.reroute_min, min(settings.reroute_max, round(interval)))
    return config


def configurations_match(actual, expected):
    # Target population is an external workload input, not a routing decision.
    return set(actual) == set(expected) and all(
        math.isclose(actual[key], expected[key], rel_tol=1e-9, abs_tol=1e-9)
        for key in expected if key != "total_car_counter"
    )


@dataclass(frozen=True)
class Boundary:
    step: int
    trips: int
    overhead_sum: float
    duration_sum: float
    complaints: int
    population: int

    @classmethod
    def from_stats(cls, stats):
        for key in ("step", "total_trips", "total_complaints", "driving_car_counter"):
            if type(stats[key]) is not int or stats[key] < 0:
                raise ValueError(f"Invalid monitoring counter: {key}")
        for key in ("total_trip_overhead_average", "total_trip_average"):
            if not math.isfinite(stats[key]) or stats[key] < 0:
                raise ValueError(f"Invalid monitoring average: {key}")
        n = stats["total_trips"]
        # CrowdNav increments n before addToAverage: its average is sum/(n+1).
        return cls(stats["step"], n, (n + 1) * stats["total_trip_overhead_average"],
                   (n + 1) * stats["total_trip_average"], stats["total_complaints"],
                   stats["driving_car_counter"])


def measurement(start, end):
    ticks, trips = end.step - start.step, end.trips - start.trips
    if ticks <= 0 or trips < 0 or end.complaints < start.complaints:
        raise RuntimeError("CrowdNav counters reset during a measurement")
    overhead_sum = end.overhead_sum - start.overhead_sum
    duration_sum = end.duration_sum - start.duration_sum
    if overhead_sum < -1e-7 or duration_sum < -1e-7:
        raise RuntimeError("Cumulative trip sums decreased")
    return dict(start_step=start.step, end_step=end.step, ticks=ticks, completed_trips=trips,
                start_trip_count=start.trips, end_trip_count=end.trips, overhead_sum=overhead_sum,
                overhead=overhead_sum / trips if trips else None,
                duration=duration_sum / trips if trips else None,
                completion_rate=trips / ticks,
                complaint_rate=(end.complaints - start.complaints) / trips if trips else None,
                population_start=start.population, population_end=end.population,
                population_slope=(end.population - start.population) / ticks,
                low_confidence=trips < 30)


def observation(window, config, settings):
    # Undefined measured overhead stays null in logs. Only the state uses the cap.
    overhead = window["overhead"] if window["overhead"] is not None else 30.0
    return [overhead / settings.overhead_scale, window["completion_rate"] / settings.rate_scale,
            window["population_end"] / settings.population_scale,
            window["population_slope"] * settings.trend_ticks / settings.population_scale,
            2 * config[CONTROLLED[0]] / settings.exploration_max - 1,
            2 * math.log(config[CONTROLLED[1]] / settings.reroute_min)
            / math.log(settings.reroute_max / settings.reroute_min) - 1]


def reward(before, after, settings):
    penalty = settings.throughput_weight * max(0, 1 - after["completion_rate"] / settings.rate_floor)
    difference = relative = None
    if after["overhead"] is None:
        value, source = -1 - settings.throughput_weight, "no_completion_penalty"
    elif before["overhead"] is None:
        value, source = -penalty, "missing_before_measurement"
    else:
        difference = before["overhead"] - after["overhead"]
        relative = difference / max(before["overhead"], 1e-6)
        value, source = relative - penalty, "measured_difference"
    return dict(reward=value, reward_source=source, raw_overhead_difference=difference,
                relative_overhead_difference=relative, throughput_penalty=penalty)


class FrozenActor:
    """Evaluate exported ReLU layers and the tanh mean head with standard Python."""

    def __init__(self, data=None):
        self.data = data
        if self.data is None:
            raise ValueError("No trained actor loaded. Supply an exported checkpoint.json.")
        if self.data["features"] != list(FEATURES):
            raise ValueError("Exported actor has an incompatible feature order")
        self.settings = Settings(**self.data["settings"])
        width = len(FEATURES)
        if len(self.data["layers"]) != 3:
            raise ValueError("Expected two actor hidden layers and one mean head")
        for layer in self.data["layers"]:
            weights, biases = layer["weight"], layer["bias"]
            if not biases or len(weights) != len(biases) or any(len(row) != width for row in weights):
                raise ValueError("Invalid checkpoint layer dimensions")
            if any(not math.isfinite(value) for row in weights for value in row) or any(
                not math.isfinite(value) for value in biases
            ):
                raise ValueError("Checkpoint contains nonfinite weights")
            width = len(biases)
        if width != 2:
            raise ValueError("Expected two actor outputs")

    @classmethod
    def load(cls, checkpoint):
        return cls(json.loads(Path(checkpoint).read_text()))

    def __call__(self, state):
        values = [float(value) for value in state]
        if len(values) != len(FEATURES) or not all(math.isfinite(x) for x in values):
            raise ValueError("Invalid actor observation")
        for index, layer in enumerate(self.data["layers"]):
            values = [sum(w * x for w, x in zip(row, values)) + bias
                      for row, bias in zip(layer["weight"], layer["bias"])]
            if index < len(self.data["layers"]) - 1:
                values = [max(0.0, x) for x in values]
        return [math.tanh(x) for x in values]


class SACStrategy(Strategy):
    """One outstanding action, with confirmation -> settlement -> measurement.

    A reset, unexpected knob change, or failed confirmation aborts the episode.
    The caller logs the failure and starts a fresh run; invalid transitions never
    reach replay. Duplicate snapshots do not advance this state machine.
    """

    def __init__(self, exemplar, settings=None, actor=None, log=None):
        super().__init__(exemplar)
        self.settings = settings or Settings()
        self.actor = actor
        self.log = log or (lambda record: None)
        self.phase = "acquire"
        self.latest = self.config = self.expected = self.window = self.state = None
        self.start = self.pending = self.transition = None
        self.deadline = self.last_attempt_step = None
        self.attempts = self.transition_count = 0

    def monitor(self, **kwargs):
        snapshot = self.exemplar.snapshot()
        if snapshot is None:
            return False
        # Preserve the original Knowledge interface without unbounded history.
        for key in ("car_stats", "configs"):
            self.knowledge.monitored_data[key] = [snapshot[key]]
        return True

    def analyze(self):
        data = self.knowledge.monitored_data
        if not data:
            return False
        self.observe({key: data[key][-1] for key in ("car_stats", "configs")})
        return self.phase == "ready"

    def observe(self, snapshot):
        current = Boundary.from_stats(snapshot["car_stats"])
        config = {key: snapshot["configs"][key] for key in KNOBS}
        s = self.settings
        if any(not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0
               for value in config.values()):
            raise ValueError("Invalid routing configuration")
        if not (0 <= config[CONTROLLED[0]] <= s.exploration_max
                and s.reroute_min <= config[CONTROLLED[1]] <= s.reroute_max):
            raise RuntimeError("Monitored configuration is outside SAC action bounds")
        if self.latest is not None:
            if current.step < self.latest.step or current.trips < self.latest.trips:
                raise RuntimeError("CrowdNav reset; discard the pending transition")
            if current.step == self.latest.step:
                return
        if self.config is not None and config["total_car_counter"] != self.config["total_car_counter"]:
            self.log(dict(event="workload_changed", step=current.step,
                          previous_target=self.config["total_car_counter"],
                          target=config["total_car_counter"]))
        self.latest, self.config = current, config
        if self.phase == "acquire":
            self.expected = dict(config)
            self.deadline = max(current.step, s.warmup_ticks) + s.settlement_ticks
            self.phase = "settle"
            self.log(dict(event="acquisition", step=current.step, configuration=config,
                          settlement_deadline=self.deadline))
        if self.phase == "await_application":
            if configurations_match(config, self.expected):
                self._confirm()
            elif not configurations_match(config, self.pending["old_configuration"]):
                raise RuntimeError("Unexpected configuration while awaiting application")
            return
        if not configurations_match(config, self.expected):
            raise RuntimeError("Configuration drift; discard the pending transition")
        if self.phase == "settle" and current.step >= self.deadline:
            self.start = current
            self.deadline = current.step + s.window_ticks
            self.phase = "measure"
        elif self.phase == "measure" and current.step >= self.deadline:
            window = measurement(self.start, current)
            state = observation(window, config, s)
            if self.pending is not None:
                record = {**self.pending, "after": window, "next_state": state,
                          "observation_deadline": self.deadline,
                          "application_attempts": self.attempts,
                          "reward_version": s.version, **reward(self.window, window, s)}
                self.transition = record
                self.transition_count += 1
                self.log(dict(event="transition", **record))
            else:
                self.log(dict(event="initial_window", window=window, state=state))
            self.window, self.state = window, state
            self.knowledge.analysis_data = dict(window=window, state=state)
            self.pending = None
            self.phase = "ready"

    def plan(self, action=None):
        if self.phase != "ready":
            raise RuntimeError("A complete stable window is required before planning")
        if action is None:
            if self.actor is None:
                raise ValueError("Supply a normalized action or a frozen actor")
            action = self.actor(self.state)
        desired = action_configuration(action, self.config, self.settings)
        self.pending = dict(state=list(self.state), action=[float(x) for x in action],
                            before=dict(self.window), decision_step=self.latest.step,
                            old_configuration=dict(self.config), requested_configuration=desired)
        self.knowledge.plan_data = desired
        self.expected = desired
        self.attempts = 0
        self.last_attempt_step = None
        self.phase = "await_application"
        if configurations_match(self.config, desired):
            self._confirm()  # A no-op still receives a full settlement and window.
        return True

    def _confirm(self):
        self.pending["confirmed_step"] = self.latest.step
        self.pending["settlement_deadline"] = self.latest.step + self.settings.settlement_ticks
        self.deadline = self.pending["settlement_deadline"]
        self.phase = "settle"
        self.log(dict(event="application_confirmed", **self.pending))

    def execute(self, **kwargs):
        if self.phase != "await_application":
            return False
        step = self.latest.step
        if self.last_attempt_step is not None and step - self.last_attempt_step < self.settings.retry_ticks:
            return False
        if self.attempts >= self.settings.max_attempts:
            raise RuntimeError("Configuration was not confirmed after three attempts")
        self.last_attempt_step = step
        self.attempts += 1
        # A queued command must not overwrite an external population change.
        command = {key: self.expected[key] for key in CONTROLLED}
        try:
            self.exemplar.apply(command)
        except (requests.RequestException, RuntimeError) as exc:
            self.log(dict(event="command_failed", step=step, attempt=self.attempts, error=str(exc)))
            return False
        self.pending["accepted_step"] = step
        self.log(dict(event="command_accepted", step=step, attempt=self.attempts,
                      configuration=command))
        return True
