"""Fixed-cadence metric collection and a dependency-free HTTP dashboard."""

from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import math
from pathlib import Path
from threading import Event, Lock, Thread
import time
from urllib.parse import urlsplit

import requests


class MetricHistory:
    def __init__(self, interval, capacity=1200):
        if not math.isfinite(interval) or interval <= 0:
            raise ValueError("Sampling interval must be a finite positive number")
        self.interval = interval
        self.samples = deque(maxlen=capacity)
        self.lock = Lock()
        self.strategy_error = None

    def record(self, slot, timestamp, payload=None, error=None):
        stats = payload.get("car_stats", {}) if isinstance(payload, dict) else {}
        valid = (isinstance(stats, dict) and isinstance(stats.get("step"), (int, float))
                 and not isinstance(stats["step"], bool) and math.isfinite(stats["step"]))
        if not valid:
            stats = {}
            error = error or "Waiting for a CrowdNav monitoring snapshot"
        values = {
            key: value for key, value in stats.items()
            if key != "step" and isinstance(value, (int, float))
            and not isinstance(value, bool) and math.isfinite(value)
        }
        with self.lock:
            previous = self.samples[-1] if self.samples else None
            reset = bool(valid and previous and previous["step"] is not None
                         and stats["step"] < previous["step"])
            consecutive = bool(previous and previous["slot"] == slot - 1
                               and previous["step"] is not None and valid and not reset)
            deltas = {
                key: value - previous["metrics"][key]
                if consecutive and key in previous["metrics"] else None
                for key, value in values.items()
            }
            self.samples.append({
                "slot": slot, "timestamp": timestamp, "observed_at": time.time(),
                "step": stats.get("step"), "metrics": values, "deltas": deltas,
                "error": error, "reset": reset,
            })

    def snapshot(self):
        with self.lock:
            return {"interval": self.interval, "capacity": self.samples.maxlen,
                    "strategy_error": self.strategy_error, "samples": list(self.samples)}

    def set_strategy_error(self, message):
        with self.lock:
            self.strategy_error = message


class Dashboard:
    def __init__(self, endpoint, interval=3, host="0.0.0.0", port=8090):
        self.history = MetricHistory(interval)
        self.endpoint = endpoint.rstrip("/")
        self.stop = Event()
        history = self.history
        page = Path(__file__).with_name("dashboard.html").read_bytes()

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                route = urlsplit(self.path).path
                if route in ("/", "/dashboard", "/dashboard/"):
                    body, content_type = page, "text/html; charset=utf-8"
                elif route == "/api/metrics":
                    body = json.dumps(history.snapshot(), allow_nan=False).encode()
                    content_type = "application/json"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_):
                pass

        self.server = ThreadingHTTPServer((host, port), Handler)
        self.sampler = Thread(target=self._sample, daemon=True)
        self.http = Thread(target=self.server.serve_forever, daemon=True)

    def start(self):
        self.http.start()
        self.sampler.start()

    def close(self):
        self.stop.set()
        self.server.shutdown()
        self.server.server_close()
        self.sampler.join(timeout=self.history.interval + 1)
        self.http.join(timeout=2)

    def _sample(self):
        interval = self.history.interval
        start = time.monotonic()
        wall_start = time.time()
        slot = 0
        with requests.Session() as session:
            while not self.stop.is_set():
                if self.stop.wait(max(0, start + slot * interval - time.monotonic())):
                    return
                payload, error = None, None
                try:
                    response = session.get(f"{self.endpoint}/monitor/", timeout=min(5, interval * 0.8))
                    response.raise_for_status()
                    payload = response.json()
                except (requests.RequestException, ValueError) as exc:
                    error = str(exc)
                self.history.record(slot, wall_start + slot * interval, payload, error)
                # Skip missed slots rather than taking a burst of catch-up samples.
                slot = max(slot + 1, math.floor((time.monotonic() - start) / interval) + 1)
