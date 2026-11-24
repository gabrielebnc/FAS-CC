"""Local training infrastructure: isolated Docker episodes and the Gym adapter.

The deployment's three files never import this module.
"""

from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time
import uuid

import gymnasium as gym
import numpy as np
import requests

from UPISAS.exemplars.crowdnav import CrowdNav
from UPISAS.strategies.my_strategy import SACStrategy


ROOT = Path(__file__).resolve().parent
INITIAL = dict(route_random_sigma=0.2, exploration_percentage=0.5,
               max_speed_and_length_factor=1, average_edge_duration_factor=1,
               freshness_update_factor=10, freshness_cut_off_value=90,
               re_route_every_ticks=600, total_car_counter=750, edge_average_influence=140)


def command(*args):
    return subprocess.run(args, check=True, capture_output=True, text=True, timeout=300).stdout


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + "\n")


def mount(source, target, writable=False):
    return dict(type="bind", source=str(source.resolve()), target=target, read_only=not writable)


class DockerRun:
    """Own exactly one fresh Kafka/API/SUMO stack, with no shared Kafka state."""

    def __init__(self, directory, seed, ticks, crowdnav_image, api_image, initial=None):
        self.directory, self.seed, self.ticks = Path(directory).resolve(), seed, ticks
        self.initial = dict(INITIAL if initial is None else initial)
        self.images = dict(crowdnav=crowdnav_image, api=api_image)
        self.compose = None
        self.endpoint = None

    def start(self):
        self.directory.mkdir(parents=True, exist_ok=False)
        write_json(self.directory / "initial.json", self.initial)
        keys = dict(explorationPercentage="exploration_percentage", reRouteEveryTicks="re_route_every_ticks",
                    routeRandomSigma="route_random_sigma", maxSpeedAndLengthFactor="max_speed_and_length_factor",
                    averageEdgeDurationFactor="average_edge_duration_factor", freshnessUpdateFactor="freshness_update_factor",
                    freshnessCutOffValue="freshness_cut_off_value")
        write_json(self.directory / "knobs.json", {camel: self.initial[snake] for camel, snake in keys.items()})
        shutil.copyfile(ROOT / "simulation_worker.py", self.directory / "simulation_worker.py")
        metadata = dict(seed=self.seed, ticks=self.ticks, initial=self.initial, images={}, source_sha256={})
        for role, reference in self.images.items():
            metadata["images"][role] = command("docker", "image", "inspect", reference, "--format", "{{.Id}}").strip()
        for path in [*(ROOT / "UPISAS").rglob("*.py"), ROOT / "run_challenge.py",
                     ROOT / "training.py", ROOT / "training_runtime.py", ROOT / "simulation_worker.py",
                     *(ROOT / "Crowdnav/crowdnav/app").rglob("*.py"),
                     *(ROOT / "Crowdnav/api/app").rglob("*.py")]:
            metadata["source_sha256"][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
        write_json(self.directory / "metadata.json", metadata)
        services = {
            "kafka": dict(image="spotify/kafka:latest", environment=dict(ADVERTISED_HOST="kafka", ADVERTISED_PORT="9092"),
                          healthcheck=dict(test=["CMD-SHELL", "bash -c 'echo > /dev/tcp/localhost/9092'"],
                                           interval="2s", timeout="2s", retries=60)),
            "http-server": dict(image=self.images["api"], ports=["127.0.0.1::8080"],
                                depends_on={"kafka": {"condition": "service_healthy"}},
                                command=["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"],
                                volumes=[mount(ROOT / "Crowdnav/api/app", "/code/app"),
                                         mount(ROOT / "Crowdnav/api/specifications", "/code/specifications")]),
            "crowdnav": dict(image=self.images["crowdnav"],
                             depends_on={"kafka": {"condition": "service_healthy"}},
                             entrypoint=["python", "/results/simulation_worker.py"],
                             environment=dict(SIMULATION_SEED=str(self.seed), SIMULATION_TICKS=str(self.ticks),
                                              PYTHONHASHSEED=str(self.seed), PYTHONUNBUFFERED="1"),
                             volumes=[mount(ROOT / "Crowdnav/crowdnav", "/app"),
                                      mount(self.directory / "knobs.json", "/app/knobs.json"),
                                      mount(self.directory, "/results", writable=True)]),
        }
        config = self.directory / "compose.json"
        write_json(config, dict(services=services))
        self.compose = ["docker", "compose", "-p", "crowdnav-sac-" + uuid.uuid4().hex[:10], "-f", str(config)]
        try:
            command(*self.compose, "up", "-d", "--wait", "kafka", "http-server")
            address = command(*self.compose, "port", "http-server", "8080").strip()
            self.endpoint = "http://127.0.0.1:" + address.rsplit(":", 1)[1]
            deadline = time.monotonic() + 120
            while True:
                try:
                    response = requests.get(self.endpoint + "/", timeout=3)
                    response.raise_for_status()
                    break
                except requests.RequestException:
                    if time.monotonic() > deadline:
                        raise TimeoutError("Training API did not start")
                    time.sleep(0.2)
            command(*self.compose, "up", "-d", "crowdnav")
        except BaseException:
            self.close()
            raise
        return CrowdNav(self.endpoint)

    def wait_finished(self, timeout=None):
        # Long training episodes can have a substantial final hold after the
        # sample budget. The timeout must cover that unchanged simulator tail.
        if timeout is None:
            timeout = max(300, self.ticks * 0.05)
        deadline = time.monotonic() + timeout
        next_check = time.monotonic()
        while not (self.directory / "result.json").exists():
            if time.monotonic() > deadline:
                raise TimeoutError("Simulation did not reach its horizon")
            if time.monotonic() >= next_check:
                rows = command(*self.compose, "ps", "-a", "--format", "json")
                states = json.loads(rows) if rows.lstrip().startswith("[") else [
                    json.loads(line) for line in rows.splitlines() if line.strip()]
                if any(row.get("State") == "exited" for row in states):
                    if not (self.directory / "result.json").exists():
                        raise RuntimeError("A simulator service exited early; inspect containers.log")
                next_check = time.monotonic() + 5
            time.sleep(0.2)
        return json.loads((self.directory / "result.json").read_text())

    def close(self):
        if self.compose is not None:
            try:
                (self.directory / "containers.log").write_text(command(*self.compose, "logs", "--no-color"))
            finally:
                command(*self.compose, "down", "-v", "--remove-orphans")
                self.compose = None


def summarize(directory):
    import statistics
    directory = Path(directory)
    result = json.loads((directory / "result.json").read_text())
    trips = [json.loads(line) for line in (directory / "trips.jsonl").read_text().splitlines()]
    if len(trips) != result["trip_count"] or any(not 200 < t["tick"] <= result["final_step"] for t in trips):
        raise ValueError("Raw trip count or horizon mismatch")
    if trips:
        overheads = [trip["overhead"] for trip in trips]
        exact_mean = statistics.mean(overheads)
        published_sum = (len(trips) + 1) * result["published_overhead"]
        if not np.isclose(sum(overheads), published_sum, rtol=1e-8):
            raise ValueError("Published sum disagrees with individual trip events")
        result.update(mean_overhead=exact_mean, median_overhead=statistics.median(overheads),
                      p95_overhead=float(np.quantile(overheads, 0.95)),
                      mean_duration=statistics.mean(trip["duration"] for trip in trips),
                      p95_duration=float(np.quantile([trip["duration"] for trip in trips], 0.95)),
                      complaint_rate=sum(t["complaint"] for t in trips) / len(trips))
    write_json(directory / "summary.json", result)
    return result


class CrowdNavEnv(gym.Env):
    """One Gym step is a confirmed, settled, completely measured SAC action."""

    metadata = {"render_modes": []}

    def __init__(self, output, settings, episode_ticks=10_000, simulator_seed=101,
                 crowdnav_image="crowdnav-sac/simulation:local", api_image="crowdnav-sac/api:local"):
        super().__init__()
        self.output, self.settings, self.episode_ticks = Path(output), settings, episode_ticks
        self.simulator_seed, self.episode = simulator_seed, 0
        self.images = (crowdnav_image, api_image)
        self.run = self.strategy = self.exemplar = self.stream = None
        self.last_progress = time.monotonic()
        self.last_step = None
        self.action_space = gym.spaces.Box(-1, 1, shape=(2,), dtype=np.float32)
        self.observation_space = gym.spaces.Box(
            low=np.array([0, 0, 0, -np.inf, -1, -1], dtype=np.float32),
            high=np.array([np.inf, np.inf, np.inf, np.inf, 1, 1], dtype=np.float32))
        self.output.mkdir(parents=True, exist_ok=True)
        self.next_seed = simulator_seed
        self.last_record = None

    def _log(self, record):
        enriched = dict(episode=self.episode, simulator_seed=self.current_seed, **record)
        self.stream.write(json.dumps(enriched, allow_nan=False) + "\n")

    def _poll(self):
        try:
            if self.strategy.monitor():
                self.strategy.analyze()
                if self.strategy.latest.step != self.last_step:
                    self.last_step, self.last_progress = self.strategy.latest.step, time.monotonic()
                self.strategy.execute()
        except requests.RequestException as exc:
            self._log(dict(event="http_error", error=str(exc)))
        except (RuntimeError, ValueError, KeyError) as exc:
            self._log(dict(event="integration_failure", phase=self.strategy.phase, error=str(exc)))
            raise
        if time.monotonic() - self.last_progress > 120:
            raise TimeoutError("No new CrowdNav snapshot for 120 seconds")
        if (self.run.directory / "result.json").exists() and self.strategy.phase != "ready":
            raise RuntimeError("Horizon reached with an incomplete window; no replay transition created")
        if self.strategy.latest is not None and self.strategy.latest.step >= self.episode_ticks:
            raise RuntimeError("No complete transition before the episode horizon")
        time.sleep(0.03)

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        self.close()
        self.episode += 1
        self.current_seed = self.next_seed if seed is None else int(seed)
        self.next_seed = self.current_seed + 1
        directory = self.output / f"episode-{self.episode:04d}-seed-{self.current_seed}"
        self.run = DockerRun(directory, self.current_seed, self.episode_ticks, *self.images)
        try:
            self.exemplar = self.run.start()
            self.stream = (directory / "decisions.jsonl").open("w", buffering=1)
            self.strategy = SACStrategy(self.exemplar, self.settings, log=self._log)
            self.last_progress, self.last_step = time.monotonic(), None
            while self.strategy.phase != "ready":
                self._poll()
        except BaseException:
            self.close()
            raise
        return np.array(self.strategy.state, dtype=np.float32), dict(simulator_seed=self.current_seed)

    def step(self, action):
        count = self.strategy.transition_count
        self.strategy.plan(action)
        while self.strategy.transition_count == count:
            self._poll()
        record = self.strategy.transition
        self.last_record = dict(record)
        # Stop on a complete transition, then hold the final action through the tail.
        reserve = self.settings.settlement_ticks + self.settings.window_ticks + 600
        truncated = self.strategy.latest.step + reserve >= self.episode_ticks
        if truncated:
            self.run.wait_finished()
            summary = summarize(self.run.directory)
            self._log(dict(event="episode_finished", summary=summary))
        return (np.array(record["next_state"], dtype=np.float32), float(record["reward"]),
                False, truncated, dict(record, simulator_seed=self.current_seed))

    def close(self):
        if self.stream is not None:
            self.stream.close()
            self.stream = None
        if self.exemplar is not None:
            self.exemplar.close()
            self.exemplar = None
        if self.run is not None:
            self.run.close()
            self.run = None
