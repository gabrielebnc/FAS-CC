"""Validate scoring and isolation without requiring Docker or SUMO."""

import copy
import json
from pathlib import Path
import tempfile
import unittest
import sys
import subprocess
from unittest.mock import patch, call

from tools.evaluate import (BASELINE, HORIZON, SUBMISSION, FRAMEWORK, compare,
                            configuration, summarize, snapshot_runtime, start_strategy, stop_strategy,
                            local_image_tag, backbone_image, find_backbone)
from tools.evaluate import write_reports


class TestEvaluation(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name)
        self.result = {"seed": 11, "final_step": HORIZON, "trip_count": 3,
                       "map": "eichstaedt", "warmup_ticks": 200, "smart_car_percentage": .2,
                       "configs": [{"step": 1, "configs": BASELINE.copy()}]}
        self.events = [{"tick": 201, "overhead": 1}, {"tick": 9000, "overhead": 2},
                       {"tick": HORIZON, "overhead": 30}]

    def write(self):
        (self.path / "result.json").write_text(json.dumps(self.result))
        (self.path / "trips.jsonl").write_text("\n".join(json.dumps(e) for e in self.events))

    def test_scores_individual_trip_median_and_includes_final_tick(self):
        self.write()
        score = summarize(self.path)
        self.assertEqual(score["median_overhead"], 2)
        self.assertEqual(score["mean_overhead"], 11)
        self.assertAlmostEqual(score["p90_overhead"], 24.4)

    def test_exports_partial_and_completed_comparisons_with_raw_data_paths(self):
        (self.path / "metadata.json").write_text(json.dumps({
            "seeds": [11], "images": {}, "horizon": HORIZON, "source_sha256": {}}))
        (self.path / "evaluation_simulation.py").write_text("# Frozen wrapper\n")
        baseline = self.path / "11-baseline"
        baseline.mkdir()
        (baseline / "result.json").write_text(json.dumps(self.result))
        (baseline / "trips.jsonl").write_text("\n".join(json.dumps(e) for e in self.events))
        report = write_reports(self.path, [self.path])
        self.assertFalse(report["evaluation_complete"])
        self.assertEqual(report["completed_pairs"], 0)
        self.assertTrue((self.path / "runs.csv").exists())
        strategy = self.path / "11-strategy"
        strategy.mkdir()
        (strategy / "result.json").write_text(json.dumps(self.result))
        (strategy / "trips.jsonl").write_text("\n".join(json.dumps({**e, "overhead": .5}) for e in self.events))
        report = write_reports(self.path, [self.path])
        self.assertTrue(report["evaluation_complete"])
        self.assertTrue(report["all_tested_seeds_pass"])
        self.assertEqual(report["pairs"][0]["reduction_percent"], 75)
        self.assertIn("PASS", (self.path / "report.md").read_text())
        self.assertTrue((self.path / "pairs.csv").exists())

    def test_rejects_incomplete_or_mixed_runs_and_changed_workload(self):
        for change in ("early_stop", "missing_event", "past_horizon", "workload", "baseline"):
            with self.subTest(change=change):
                result = copy.deepcopy(self.result)
                events = copy.deepcopy(self.events)
                if change == "early_stop":
                    result["final_step"] = HORIZON - 1
                elif change == "missing_event":
                    events.pop()
                elif change == "past_horizon":
                    events[-1]["tick"] += 1
                elif change == "workload":
                    result["configs"].append({"step": 50, "configs": {**BASELINE, "total_car_counter": 500}})
                else:
                    result["configs"][0]["configs"]["exploration_percentage"] = 0
                (self.path / "result.json").write_text(json.dumps(result))
                (self.path / "trips.jsonl").write_text("\n".join(json.dumps(e) for e in events))
                with self.assertRaises(ValueError):
                    summarize(self.path)

    def test_every_pair_must_strictly_improve(self):
        b = {**self.result, "median_overhead": 2}
        s = {**self.result, "median_overhead": 1.5}
        self.assertTrue(compare(11, b, s)["passed"])
        self.assertEqual(compare(11, b, s)["reduction_percent"], 25)
        for median in (2, 3):
            self.assertFalse(compare(11, b, {**s, "median_overhead": median})["passed"])

    def test_rejects_unpaired_settings_or_adapted_baseline(self):
        b = {**self.result, "median_overhead": 2}
        for key, value in (("seed", 22), ("map", "different"), ("warmup_ticks", 100)):
            with self.assertRaises(ValueError):
                compare(11, b, {**b, key: value})
        b = copy.deepcopy(b)
        b["configs"].append({"step": 20, "configs": {**BASELINE, "re_route_every_ticks": 60}})
        with self.assertRaises(ValueError):
            compare(11, b, b)

    def test_compose_contains_only_backbones_and_loopback_api(self):
        images = {"api": "api:test", "crowdnav": "crowd:test"}
        config = configuration(images, self.path / "run", 11)
        self.assertNotIn("networks", config)
        services = config["services"]
        self.assertEqual(set(services), {"kafka", "http-server", "crowdnav"})
        for service in services.values():
            self.assertNotIn("container_name", service)
        self.assertNotIn("ports", services["kafka"])
        self.assertNotIn("ports", services["crowdnav"])
        port = services["http-server"]["ports"][0]
        self.assertEqual(port["host_ip"], "127.0.0.1")
        self.assertEqual(port["target"], 8080)
        self.assertNotIn("published", port)
        self.assertEqual(services["crowdnav"]["environment"]["PYTHONHASHSEED"], "11")
        for service in ("http-server", "crowdnav"):
            self.assertEqual(services[service]["pull_policy"], "never")

    def test_bare_local_image_ids_are_tagged_without_pulling(self):
        image_id = "sha256:" + "a" * 64
        tag = "fas-evaluation/api:" + "a" * 64
        with patch("tools.evaluate.command", side_effect=[image_id + "\n", ""]) as docker:
            self.assertEqual(local_image_tag(image_id, "api"), tag)
        self.assertEqual(docker.call_args_list, [
            call("docker", "image", "inspect", image_id, "--format", "{{.Id}}"),
            call("docker", "image", "tag", image_id, tag)])

    def test_deleted_container_image_falls_back_to_current_configured_tag(self):
        data = {"Image": "sha256:old", "Config": {"Image": "crowdnav:latest"}}
        missing = subprocess.CalledProcessError(1, ["docker"], stderr="No such image: sha256:old")
        with patch("tools.evaluate.command", return_value=json.dumps(data)), \
             patch("tools.evaluate.local_image_tag", side_effect=[missing, "pinned:current"]) as tag:
            self.assertEqual(backbone_image("crowdnav"), "pinned:current")
        self.assertEqual(tag.call_args_list, [call("sha256:old", "crowdnav"),
                                               call("crowdnav:latest", "crowdnav")])

    def test_missing_container_and_image_are_rebuilt_from_source(self):
        missing = subprocess.CalledProcessError(1, ["docker"], stderr="No such object: crowdnav")
        with patch("tools.evaluate.command", side_effect=[missing, ""]) as docker, \
             patch("tools.evaluate.local_image_tag", return_value="pinned:rebuilt"):
            self.assertEqual(backbone_image("crowdnav", source=self.path), "pinned:rebuilt")
        self.assertIn(call("docker", "build", "-t", "fas-evaluation/crowdnav:source",
                           str(self.path / "crowdnav"), capture=False), docker.call_args_list)

    def test_permission_failure_does_not_trigger_a_rebuild(self):
        denied = subprocess.CalledProcessError(1, ["docker"], stderr="permission denied")
        with patch("tools.evaluate.command", side_effect=denied) as docker:
            with self.assertRaises(subprocess.CalledProcessError):
                backbone_image("crowdnav", source=self.path)
        self.assertEqual(docker.call_count, 1)

    def test_backbone_detection_checks_both_build_contexts(self):
        self.assertIsNone(find_backbone(self.path))
        for directory in ("crowdnav", "api"):
            (self.path / directory).mkdir()
            (self.path / directory / "Dockerfile").touch()
        self.assertEqual(find_backbone(self.path), self.path.resolve())

    def test_snapshot_imports_submission_without_dashboard_or_extra_strategy_modules(self):
        runtime = snapshot_runtime(self.path)
        files = {str(p.relative_to(runtime)) for p in runtime.rglob("*.py")}
        self.assertEqual(files, set((*FRAMEWORK, *SUBMISSION,
                                    "UPISAS/exemplars/__init__.py", "UPISAS/strategies/__init__.py")))
        import subprocess
        result = subprocess.run([sys.executable, "-c",
            "import run_challenge; print(run_challenge.ReactiveStrategy.__name__)"],
            cwd=runtime, capture_output=True, text=True, check=True)
        self.assertIn("ReactiveStrategy", result.stdout)

    def test_local_process_uses_current_python_endpoint_and_captures_output(self):
        (self.path / "run_challenge.py").write_text(
            'import os,sys\nprint(sys.executable)\nprint(os.environ["CROWDNAV_ENDPOINT"])\n')
        with (self.path / "strategy.log").open("w") as log:
            process = start_strategy(self.path, "http://127.0.0.1:12345", log)
            self.addCleanup(stop_strategy, process)
            self.assertEqual(process.wait(timeout=10), 0)
        output = (self.path / "strategy.log").read_text()
        self.assertIn(sys.executable, output)
        self.assertIn("http://127.0.0.1:12345", output)

    def test_local_process_is_stopped_during_cleanup(self):
        (self.path / "run_challenge.py").write_text('import time\ntime.sleep(60)\n')
        with (self.path / "strategy.log").open("w") as log:
            process = start_strategy(self.path, "http://127.0.0.1:12345", log)
            self.addCleanup(stop_strategy, process)
            stop_strategy(process)
        self.assertIsNotNone(process.poll())


if __name__ == "__main__":
    unittest.main()
