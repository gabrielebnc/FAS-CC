"""Submission behavior that must survive strategy selection and trial rollback."""

from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
import run_challenge
from UPISAS.exemplars.crowdnav import CrowdNav
from UPISAS.strategies.my_strategy import BaselineStrategy, FixedStrategy, ReactiveStrategy
BASELINE = {
    "route_random_sigma": .2, "exploration_percentage": .5,
    "max_speed_and_length_factor": 1, "average_edge_duration_factor": 1,
    "freshness_update_factor": 10, "freshness_cut_off_value": 90,
    "re_route_every_ticks": 600, "total_car_counter": 750,
    "edge_average_influence": 140,
}


class TestSubmission(unittest.TestCase):
    def setUp(self):
        self.strategy = ReactiveStrategy(CrowdNav())
        self.config = BASELINE.copy()

    def observe(self, step, trips, average):
        self.strategy.knowledge.monitored_data = {
            "configs": [self.config.copy()],
            "car_stats": [{"step": step, "total_trips": trips,
                           "total_trip_overhead_average": average}],
        }
        return self.strategy.analyze()

    def execute(self):
        self.assertTrue(self.strategy.plan())
        payload = self.strategy.knowledge.plan_data.copy()
        self.assertEqual(set(payload), set(BASELINE))
        self.assertEqual(payload["total_car_counter"], 750)
        with patch("requests.put", return_value=Mock(json=lambda: {"status": "success"})):
            self.assertTrue(self.strategy.execute())
        return payload

    def test_waits_for_application_and_rolls_back_a_worse_trial(self):
        self.assertTrue(self.observe(0, 0, 0))
        initial = self.execute()
        self.assertEqual(initial["re_route_every_ticks"], 60)
        self.assertFalse(self.observe(10, 0, 0))
        self.assertIsNone(self.strategy.window_start)
        self.config.update(initial)
        self.assertFalse(self.observe(20, 0, 0))
        self.assertFalse(self.observe(620, 50, 2))
        self.assertTrue(self.observe(1220, 100, 2))
        trial = self.execute()
        self.assertEqual(trial["re_route_every_ticks"], 45)
        self.assertFalse(self.observe(1240, 100, 2))
        self.config.update(trial)
        self.assertFalse(self.observe(1440, 100, 2))
        self.assertFalse(self.observe(2040, 150, 2))
        self.assertTrue(self.observe(2640, 200, 2.25))
        rollback = self.execute()
        self.assertEqual(rollback["re_route_every_ticks"], 60)
        self.assertEqual(self.strategy.direction, 1)

    def test_failed_command_is_retried_without_waiting_forever(self):
        self.assertTrue(self.observe(0, 0, 0))
        self.strategy.plan()
        with patch("requests.put", side_effect=requests.ConnectionError("disconnected")):
            with self.assertRaises(requests.ConnectionError):
                self.strategy.execute()
        self.assertTrue(self.observe(10, 0, 0))
        self.execute()
        self.assertEqual(self.strategy.phase, "await_application")

    def test_trial_reverses_at_lower_bound_and_reset_starts_over(self):
        self.config.update(exploration_percentage=0, re_route_every_ticks=30)
        self.strategy.phase = "observe"
        self.strategy.window_start = (0, 0, 0)
        self.assertTrue(self.observe(600, 50, 2))
        self.assertEqual(self.execute()["re_route_every_ticks"], 45)
        self.config.update(re_route_every_ticks=600, exploration_percentage=.5)
        self.assertTrue(self.observe(1, 0, 0))
        self.assertFalse(self.strategy.trial_active)
        self.assertIsNone(self.strategy.reference_overhead)
        self.assertEqual(self.execute()["re_route_every_ticks"], 60)

    def test_baseline_leaves_baseline_unchanged_and_fixed_preserves_workload(self):
        baseline = BaselineStrategy(CrowdNav())
        baseline.knowledge.monitored_data = {"configs": [BASELINE.copy()]}
        self.assertFalse(baseline.analyze())
        fixed = FixedStrategy(CrowdNav())
        fixed.knowledge.monitored_data = {"configs": [{**BASELINE, "total_car_counter": 800}]}
        self.assertTrue(fixed.analyze())
        self.assertTrue(fixed.plan())
        self.assertEqual(fixed.knowledge.plan_data["total_car_counter"], 800)
        self.assertEqual(set(fixed.knowledge.plan_data), set(BASELINE))

    def test_runner_stops_at_absolute_horizon_when_attached_late(self):
        fake = Mock()
        fake.analyze.return_value = False
        fake.knowledge = SimpleNamespace(monitored_data={})
        snapshots = iter([(250, 10, 3), (10000, 100, 2)])

        def monitor(**kwargs):
            step, trips, average = next(snapshots)
            fake.knowledge.monitored_data = {
                "configs": [BASELINE.copy()],
                "car_stats": [{"step": step, "total_trips": trips,
                               "total_trip_overhead_average": average}],
            }

        fake.monitor.side_effect = monitor
        with patch.object(run_challenge, "CrowdNav"), \
             patch.dict(run_challenge.STRATEGIES, {"reactive": lambda exemplar: fake}), \
             patch.object(run_challenge.time, "sleep"), redirect_stdout(StringIO()) as output:
            run_challenge.main([])
        self.assertIn("finished at step 10000", output.getvalue())
        self.assertIn("not the grading median", output.getvalue())
        self.assertEqual(fake.analyze.call_count, 1)
        fake.execute.assert_not_called()


if __name__ == "__main__":
    unittest.main()
