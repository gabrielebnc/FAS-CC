"""Submission behavior that must survive strategy selection and trial rollback."""

from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import requests
import run_challenge
from UPISAS.exemplars.crowdnav import CrowdNav
from UPISAS.strategies.my_strategy import BaselineStrategy, FixedStrategy, ReactiveStrategy, RuleBasedStrategy
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


class TestRules(unittest.TestCase):
    def setUp(self):
        self.strategy = RuleBasedStrategy(CrowdNav())
        self.config = {**BASELINE, "exploration_percentage": 0.0, "re_route_every_ticks": 60}

    def observe(self, step, trips=0, overhead_sum=0):
        self.strategy.knowledge.monitored_data = {
            "configs": [self.config.copy()],
            "car_stats": [{"step": step, "total_trips": trips,
                           "total_trip_overhead_average": overhead_sum / (trips + 1)}],
        }
        with redirect_stdout(StringIO()):
            return self.strategy.analyze()

    def apply(self):
        self.assertTrue(self.strategy.plan())
        payload = self.strategy.knowledge.plan_data.copy()
        self.assertEqual(set(payload), set(BASELINE))
        for key in set(BASELINE) - {"exploration_percentage", "re_route_every_ticks"}:
            self.assertEqual(payload[key], self.config[key])
        with patch("requests.put", return_value=Mock(json=lambda: {"status": "success"})), \
             redirect_stdout(StringIO()):
            self.strategy.execute()
        self.config.update(payload)
        return payload

    def test_all_three_rules_only_review_on_200_tick_boundaries(self):
        self.assertFalse(self.observe(0))
        self.assertFalse(self.observe(199, 20, 60))
        self.assertTrue(self.observe(200, 20, 60))
        self.assertEqual(self.apply()["re_route_every_ticks"], 30)
        self.assertFalse(self.observe(210, 21, 63))  # Confirm application.
        self.assertFalse(self.observe(399, 41, 103))
        self.assertTrue(self.observe(400, 41, 103))  # Recent mean exactly 2.0.
        low = self.apply()
        self.assertEqual((low["exploration_percentage"], low["re_route_every_ticks"]), (.05, 90))
        self.assertFalse(self.observe(410, 41, 103))
        self.assertTrue(self.observe(600, 61, 147))  # Recent mean 2.2.
        moderate = self.apply()
        self.assertEqual((moderate["exploration_percentage"], moderate["re_route_every_ticks"]), (0, 60))

    def test_recovers_exact_interval_sum_from_backbone_average(self):
        self.assertFalse(self.observe(0, 20, 44))
        self.assertTrue(self.observe(200, 30, 64))
        self.assertAlmostEqual(self.strategy.knowledge.analysis_data["recent_overhead"], 2)
        self.assertEqual(self.apply()["exploration_percentage"], .05)

    def test_sparse_windows_accumulate_trips_without_early_decisions(self):
        self.assertFalse(self.observe(0))
        self.assertFalse(self.observe(200, 4, 12))
        self.assertEqual(self.strategy.window_start, (0, 0, 0))
        self.assertFalse(self.observe(201, 20, 60))
        self.assertTrue(self.observe(400, 20, 60))

    def test_unchanged_rule_does_not_send_redundant_commands(self):
        self.assertFalse(self.observe(0))
        self.assertFalse(self.observe(205, 20, 44))
        self.assertEqual(self.strategy.next_review, 400)
        self.assertIsNone(self.strategy.target)
        self.assertFalse(self.strategy.plan())

    def test_initial_correction_preserves_all_other_settings_and_workload(self):
        self.config = {**BASELINE, "total_car_counter": 800, "freshness_cut_off_value": 120}
        self.assertTrue(self.observe(5))
        payload = self.apply()
        self.assertEqual(payload["total_car_counter"], 800)
        self.assertEqual(payload["freshness_cut_off_value"], 120)
        self.assertEqual((payload["exploration_percentage"], payload["re_route_every_ticks"]), (0, 60))

    def test_failed_http_and_unapplied_commands_remain_retryable(self):
        self.config = BASELINE.copy()
        self.assertTrue(self.observe(0))
        self.strategy.plan()
        with patch("requests.put", side_effect=requests.ConnectionError("disconnected")):
            with self.assertRaises(requests.ConnectionError):
                self.strategy.execute()
        self.assertTrue(self.observe(10))
        with patch("requests.put", return_value=Mock(json=lambda: {"status": "success"})), \
             redirect_stdout(StringIO()):
            self.strategy.execute()
        self.assertFalse(self.observe(20))
        self.assertFalse(self.observe(209))
        self.assertTrue(self.observe(210))  # Accepted, but not observed in SUMO.

    def test_reset_clears_old_window_and_reinitializes(self):
        self.assertFalse(self.observe(600, 100, 220))
        self.config = BASELINE.copy()
        self.assertTrue(self.observe(1, 0, 0))
        self.assertIsNone(self.strategy.window_start)
        self.assertEqual(self.apply()["re_route_every_ticks"], 60)

    def test_opt_in_runner_selects_rules_and_stops_at_10000(self):
        fake = Mock()
        fake.knowledge.monitored_data = {
            "configs": [BASELINE],
            "car_stats": [{"step": 10000, "total_trips": 1, "total_trip_overhead_average": 1}],
        }
        with patch.object(run_challenge, "CrowdNav"), \
             patch.dict(run_challenge.STRATEGIES, {"rule-based": lambda exemplar: fake}), \
             redirect_stdout(StringIO()) as output:
            run_challenge.main(["--strategy", "rule-based"])
        self.assertIn("Selected strategy: rule-based", output.getvalue())
        self.assertIn("finished at step 10000", output.getvalue())
        fake.analyze.assert_not_called()


if __name__ == "__main__":
    unittest.main()
