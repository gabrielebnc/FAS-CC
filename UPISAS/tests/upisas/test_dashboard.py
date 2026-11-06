import json
from io import BytesIO
import unittest
from unittest.mock import Mock, patch

from UPISAS.dashboard import Dashboard, MetricHistory


class TestMetricHistory(unittest.TestCase):
    def setUp(self):
        self.history = MetricHistory(3, capacity=4)

    def record(self, slot, step, **metrics):
        self.history.record(slot, 100 + 3 * slot, {"car_stats": {"step": step, **metrics}})
        return self.history.snapshot()["samples"][-1]

    def test_differences_include_negative_changes_and_repeated_steps(self):
        first = self.record(0, 20, total_trips=5, tick_duration=10)
        self.assertIsNone(first["deltas"]["total_trips"])
        second = self.record(1, 30, total_trips=8, tick_duration=4)
        self.assertEqual(second["deltas"], {"total_trips": 3, "tick_duration": -6})
        repeated = self.record(2, 30, total_trips=8)
        self.assertEqual(repeated["deltas"]["total_trips"], 0)

    def test_failures_missing_slots_and_resets_break_delta_sequence(self):
        self.record(0, 20, total_trips=5)
        self.history.record(1, 103, error="unreachable")
        self.assertIsNone(self.record(2, 40, total_trips=10)["deltas"]["total_trips"])
        self.assertIsNone(self.record(4, 60, total_trips=15)["deltas"]["total_trips"])
        reset = self.record(5, 1, total_trips=0)
        self.assertTrue(reset["reset"])
        self.assertIsNone(reset["deltas"]["total_trips"])
        self.assertEqual(self.record(6, 2, total_trips=1)["deltas"]["total_trips"], 1)

    def test_null_startup_numeric_filter_and_bounded_retention(self):
        self.history.record(0, 100, None)
        self.assertTrue(self.history.snapshot()["samples"][-1]["error"])
        sample = self.record(1, 1, total_trips=1, bad=float("nan"), flag=True, text="hello")
        self.assertEqual(sample["metrics"], {"total_trips": 1})
        for slot in range(2, 9):
            self.record(slot, slot, total_trips=slot)
        self.assertEqual(len(self.history.snapshot()["samples"]), 4)
        json.dumps(self.history.snapshot(), allow_nan=False)


class TestDashboardHTTP(unittest.TestCase):
    def test_slow_sampling_skips_slots_instead_of_catching_up(self):
        response = Mock()
        response.json.return_value = {"car_stats": {"step": 50, "total_trips": 7}}
        with patch("UPISAS.dashboard.ThreadingHTTPServer"), patch("UPISAS.dashboard.requests.Session") as session:
            session.return_value.__enter__.return_value.get.return_value = response
            dashboard = Dashboard("http://example", interval=3)
            with patch.object(dashboard.stop, "is_set", side_effect=[False, False, True]), patch.object(dashboard.stop, "wait", return_value=False), patch("UPISAS.dashboard.time.monotonic", side_effect=[0, 0, 10, 10, 12]):
                dashboard._sample()
            samples = dashboard.history.snapshot()["samples"]
            self.assertEqual([sample["slot"] for sample in samples], [0, 4])
            self.assertEqual(samples[1]["timestamp"] - samples[0]["timestamp"], 12)
            self.assertIsNone(samples[1]["deltas"]["total_trips"])

    def test_routes_and_sampling_without_any_browser(self):
        response = Mock()
        response.json.return_value = {"car_stats": {"step": 50, "total_trips": 7}}
        with patch("UPISAS.dashboard.requests.Session") as session, patch("UPISAS.dashboard.ThreadingHTTPServer") as server:
            session.return_value.__enter__.return_value.get.return_value = response
            dashboard = Dashboard("http://example", interval=3, host="127.0.0.1", port=0)
            # Run one scheduled sample without opening a real socket or sleeping.
            with patch.object(dashboard.stop, "is_set", side_effect=[False, True]), patch.object(dashboard.stop, "wait", return_value=False):
                dashboard._sample()
            handler_class = server.call_args.args[1]
            handler = object.__new__(handler_class)
            handler.send_response = Mock()
            handler.send_header = Mock()
            handler.end_headers = Mock()
            handler.send_error = Mock()
            for route in ["/", "/dashboard", "/dashboard/"]:
                handler.path, handler.wfile = route, BytesIO()
                handler.do_GET()
                self.assertIn(b"Strategy observatory", handler.wfile.getvalue())
            handler.path, handler.wfile = "/api/metrics", BytesIO()
            handler.do_GET()
            snapshot = json.loads(handler.wfile.getvalue())
            self.assertEqual(snapshot["interval"], 3)
            self.assertEqual(snapshot["samples"][-1]["metrics"]["total_trips"], 7)
            handler.path = "/unknown"
            handler.do_GET()
            handler.send_error.assert_called_once_with(404)
            session.return_value.__enter__.return_value.get.assert_called_once()


if __name__ == "__main__":
    unittest.main()
