"""Verify the measurement contract, SAC updates, and portable checkpoint export."""

from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import random
import tempfile
import unittest

import gymnasium as gym
import numpy as np
import torch

from UPISAS.strategies.my_strategy import (
    Boundary, FrozenActor, SACStrategy, Settings, action_configuration, measurement, observation, reward,
)
from training_runtime import INITIAL
from training import add_transition, export_actor, load_checkpoint, make_model, save_checkpoint


class FakeCrowdNav:
    def __init__(self):
        self.commands = []
        self.failure = False

    def apply(self, configuration):
        self.commands.append(dict(configuration))
        if self.failure:
            raise RuntimeError("Kafka rejected command")


def snapshot(step, trips=0, overhead_sum=0, duration_sum=0, config=None, population=750):
    return dict(car_stats=dict(step=step, total_trips=trips, total_complaints=0,
                total_trip_overhead_average=overhead_sum / (trips + 1),
                total_trip_average=duration_sum / (trips + 1), driving_car_counter=population),
                configs={**INITIAL, **(config or {})})


class MeasurementTests(unittest.TestCase):
    def setUp(self):
        self.settings = replace(Settings(), settlement_ticks=10, window_ticks=10,
                                warmup_ticks=0, retry_ticks=5)
        self.exemplar = FakeCrowdNav()
        self.logs = []
        self.strategy = SACStrategy(self.exemplar, self.settings, log=self.logs.append)

    def ready(self):
        for item in (snapshot(0), snapshot(10, 1, 2, 20), snapshot(20, 3, 10, 100)):
            self.strategy.observe(item)
        self.assertEqual(self.strategy.phase, "ready")

    def test_off_by_one_arithmetic(self):
        start = Boundary.from_stats(snapshot(10, 1, 2, 20)["car_stats"])
        end = Boundary.from_stats(snapshot(20, 3, 10, 100)["car_stats"])
        result = measurement(start, end)
        self.assertEqual(result["overhead"], 4)
        self.assertEqual(result["duration"], 40)

    def test_initial_acquisition_is_not_a_transition(self):
        self.ready()
        self.assertEqual(self.strategy.transition_count, 0)
        self.assertEqual(self.strategy.window["overhead"], 4)

    def test_confirmation_anchors_settlement(self):
        self.ready()
        self.strategy.plan([-1, -1])
        self.strategy.execute()
        self.assertEqual(self.strategy.phase, "await_application")
        self.strategy.observe(snapshot(24, 4, 30, 300))  # HTTP acceptance is insufficient.
        self.assertEqual(self.strategy.phase, "await_application")
        requested = self.exemplar.commands[-1]
        self.strategy.observe(snapshot(27, 5, 50, 500, requested))
        self.assertEqual(self.strategy.deadline, 37)
        self.strategy.observe(snapshot(37, 7, 100, 1000, requested))
        self.strategy.observe(snapshot(47, 10, 109, 1090, requested))
        record = self.strategy.transition
        self.assertAlmostEqual(record["after"]["overhead"], 3)
        self.assertEqual(record["before"]["overhead"], 4)
        self.assertAlmostEqual(record["relative_overhead_difference"], 0.25)
        self.assertEqual(record["after"]["start_step"], 37)
        self.assertEqual(record["confirmed_step"], 27)

    def test_noop_holds_for_full_window(self):
        self.ready()
        self.strategy.plan([1, 1])  # Exactly the starting physical configuration.
        self.assertEqual(self.strategy.phase, "settle")
        self.assertFalse(self.strategy.execute())
        self.strategy.observe(snapshot(30, 4, 14, 140))
        self.assertEqual(self.strategy.transition_count, 0)
        self.strategy.observe(snapshot(40, 6, 22, 220))
        self.assertEqual(self.strategy.transition_count, 1)

    def test_duplicate_snapshots_do_not_advance(self):
        self.ready()
        count = len(self.logs)
        self.strategy.observe(snapshot(20, 3, 10, 100))
        self.assertEqual(len(self.logs), count)

    def test_command_contains_only_actor_controlled_knobs(self):
        self.ready()
        self.strategy.plan([0, 0])
        self.strategy.execute()
        command = self.exemplar.commands[0]
        self.assertEqual(set(command), {"exploration_percentage", "re_route_every_ticks"})

    def test_failed_commands_retry_same_payload_and_stop(self):
        self.ready()
        self.strategy.plan([-1, -1])
        self.exemplar.failure = True
        self.strategy.execute()
        self.strategy.execute()
        self.assertEqual(len(self.exemplar.commands), 1)
        for step in (25, 30):
            self.strategy.observe(snapshot(step, 3, 10, 100))
            self.strategy.execute()
        self.strategy.observe(snapshot(35, 3, 10, 100))
        with self.assertRaises(RuntimeError):
            self.strategy.execute()
        self.assertEqual(len(self.exemplar.commands), 3)
        self.assertTrue(all(config == self.exemplar.commands[0] for config in self.exemplar.commands))
        self.assertEqual(self.strategy.transition_count, 0)

    def test_reset_invalidates_pending_transition(self):
        self.ready()
        self.strategy.plan([-1, -1])
        with self.assertRaisesRegex(RuntimeError, "reset"):
            self.strategy.observe(snapshot(1))
        self.assertEqual(self.strategy.transition_count, 0)

    def test_drift_invalidates_pending_transition(self):
        self.ready()
        drift = dict(INITIAL, freshness_cut_off_value=700)
        with self.assertRaisesRegex(RuntimeError, "drift"):
            self.strategy.observe(snapshot(21, 3, 10, 100, drift))

    def test_population_change_during_pending_application_keeps_window(self):
        self.ready()
        self.strategy.plan([-1, -1])
        self.strategy.execute()
        changed = dict(INITIAL, total_car_counter=1000)
        self.strategy.observe(snapshot(24, 4, 30, 300, changed, population=800))
        self.assertEqual(self.strategy.phase, "await_application")
        applied = {**changed, **self.exemplar.commands[-1]}
        self.strategy.observe(snapshot(27, 5, 50, 500, applied, population=850))
        self.strategy.observe(snapshot(37, 7, 100, 1000, applied, population=900))
        self.strategy.observe(snapshot(47, 10, 109, 1090, applied, population=950))
        self.assertEqual(self.strategy.transition_count, 1)
        self.assertAlmostEqual(self.strategy.transition["after"]["overhead"], 3)
        self.assertEqual(self.strategy.transition["after"]["population_end"], 950)
        self.assertEqual(self.strategy.config["total_car_counter"], 1000)
        self.assertEqual(sum(r["event"] == "workload_changed" for r in self.logs), 1)

    def test_partial_window_has_no_reward(self):
        self.ready()
        self.strategy.plan([1, 1])
        self.strategy.observe(snapshot(30, 5, 18, 180))
        self.strategy.observe(snapshot(39, 7, 26, 260))
        self.assertIsNone(self.strategy.transition)

    def test_carryover_audit_distinguishes_routing_changes_from_noops(self):
        from verify_run import verify
        trips = [dict(tick=35, departure_tick=15, overhead=2),
                 dict(tick=38, departure_tick=25, overhead=4),
                 dict(tick=55, departure_tick=29, overhead=1)]
        applied = dict(INITIAL, exploration_percentage=0, re_route_every_ticks=30)
        records = [dict(event="transition", decision_step=15, confirmed_step=20, settlement_deadline=30,
                        old_configuration=INITIAL, requested_configuration=applied,
                        after=dict(start_step=30, end_step=40, completed_trips=2, overhead=3)),
                   dict(event="transition", decision_step=35, confirmed_step=40, settlement_deadline=50,
                        old_configuration=applied, requested_configuration=applied,
                        after=dict(start_step=50, end_step=60, completed_trips=1, overhead=1))]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            for name, values in (("trips.jsonl", trips), ("decisions.jsonl", records)):
                (root / name).write_text("".join(json.dumps(value) + "\n" for value in values))
            audit = verify(root)
        self.assertEqual(audit["changed_routing_carryover_fraction"], .5)
        self.assertEqual(audit["changed_routing_windows_over_eight_percent"], 1)
        self.assertFalse(audit["carryover_windows"][1]["routing_changed"])
        self.assertEqual(audit["carryover_windows"][1]["fraction"], 1)

    def test_mapping_bounds_and_invalid_actions(self):
        low = action_configuration([-1, -1], INITIAL, self.settings)
        high = action_configuration([1, 1], INITIAL, self.settings)
        self.assertEqual((low["exploration_percentage"], low["re_route_every_ticks"]), (0, 30))
        self.assertEqual((high["exploration_percentage"], high["re_route_every_ticks"]), (0.5, 600))
        for action in ([0, float("nan")], [2, 0], [0]):
            with self.assertRaises(ValueError):
                action_configuration(action, INITIAL, self.settings)

    def test_population_level_and_trend_are_distinct(self):
        start = Boundary.from_stats(snapshot(10, population=700)["car_stats"])
        end = Boundary.from_stats(snapshot(610, 10, 30, 3000, population=760)["car_stats"])
        state = observation(measurement(start, end), INITIAL, Settings())
        self.assertAlmostEqual(state[2], 760 / 750)
        self.assertAlmostEqual(state[3], 0.08)

    def test_seventh_feature_uses_only_the_previous_completed_window(self):
        self.strategy = SACStrategy(self.exemplar, replace(self.settings, include_overhead_trend=True))
        self.ready()
        self.assertEqual(len(self.strategy.state), 7)
        self.assertEqual(self.strategy.state[-1], 0)
        self.strategy.plan([-1, -1])
        self.strategy.execute()
        applied = self.exemplar.commands[-1]
        for step, trips, overhead, duration in ((27, 5, 50, 500), (37, 7, 100, 1000), (47, 10, 109, 1090)):
            self.strategy.observe(snapshot(step, trips, overhead, duration, applied))
        self.assertAlmostEqual(self.strategy.transition["next_state"][-1], -.25)
        window = self.strategy.window
        for previous in (None, dict(overhead=None)):
            self.assertEqual(observation(window, self.strategy.config, self.strategy.settings, previous)[-1], 0)
        missing = dict(window, overhead=None)
        self.assertEqual(observation(missing, self.strategy.config, self.strategy.settings, window)[-1], 0)
        fresh = SACStrategy(self.exemplar, self.strategy.settings)
        for item in (snapshot(0), snapshot(10, 1, 2, 20), snapshot(20, 3, 10, 100)):
            fresh.observe(item)
        self.assertEqual(fresh.state[-1], 0)

    def test_benchmark_start_action_mapping(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "SAC_SETTINGS_600_low_exploration.json").read_text())
        settings = Settings(**config["environment"])
        initial = config["initial_configuration"]
        self.assertEqual(initial, action_configuration([0, 0], INITIAL, settings))
        for u, fraction, ticks in [(-1, 0, 30), (-0.5, 0, 42), (0, 0, 60), (0.5, 0.05, 95), (1, 0.10, 150)]:
            knobs = action_configuration([u, u], initial, settings)
            self.assertEqual((knobs["exploration_percentage"], knobs["re_route_every_ticks"]), (fraction, ticks))

    def test_reward_sign_and_throughput(self):
        settings = Settings()
        before = dict(overhead=3)
        self.assertAlmostEqual(reward(before, dict(overhead=2.7, completion_rate=0.3), settings)["reward"], 0.1)
        self.assertAlmostEqual(reward(before, dict(overhead=3.3, completion_rate=0.3), settings)["reward"], -0.1)
        self.assertAlmostEqual(reward(before, dict(overhead=2.7, completion_rate=0.1), settings)["reward"], -0.4)

    def test_sparse_and_zero_completion_windows(self):
        after = dict(overhead=None, completion_rate=0)
        result = reward(dict(overhead=3), after, Settings())
        self.assertEqual(result["reward"], -2)
        self.assertIsNone(result["relative_overhead_difference"])
        self.assertEqual(result["reward_source"], "no_completion_penalty")
        result = reward(dict(overhead=None), dict(overhead=3, completion_rate=0.3), Settings())
        self.assertEqual(result["reward"], 0)
        self.assertEqual(result["reward_source"], "missing_before_measurement")

    def test_untrained_deployment_fails_explicitly(self):
        with self.assertRaisesRegex(ValueError, "No trained actor"):
            FrozenActor()


class SpacesOnlyEnv(gym.Env):
    """No simulated traffic; supplies the spaces for learner/checkpoint fixtures."""
    observation_space = gym.spaces.Box(-100, 100, shape=(6,), dtype=np.float32)
    action_space = gym.spaces.Box(-1, 1, shape=(2,), dtype=np.float32)


class SevenFeatureEnv(SpacesOnlyEnv):
    observation_space = gym.spaces.Box(-100, 100, shape=(7,), dtype=np.float32)


class LearnerTests(unittest.TestCase):
    def setUp(self):
        self.model = make_model(SpacesOnlyEnv(), learning_starts=0, batch_size=2)
        self.state = np.array([1, 1, 1, 0, -1, 0], dtype=np.float32)
        self.before = [p.detach().clone() for p in self.model.actor.parameters()]
        for index in range(3):
            add_transition(self.model, self.state, [0.1, -0.2], 0.1, self.state * 1.01, index == 2)

    def test_real_sac_updates_actor_critics_and_entropy(self):
        self.assertEqual(self.model._n_updates, 2)
        self.assertTrue(any(not torch.equal(a, b) for a, b in zip(self.before, self.model.actor.parameters())))
        self.assertNotEqual(self.model.log_ent_coef.item(), 0)
        self.assertTrue(all(torch.isfinite(p).all() for p in self.model.policy.parameters()))

    def test_scenario_configuration_updates_four_times_and_resumes_seven_features(self):
        from training_runtime import CrowdNavEnv
        config = json.loads((Path(__file__).resolve().parents[1] / "SAC_SETTINGS_scenario_200.json").read_text())
        settings = Settings(**config["environment"])
        model = make_model(SevenFeatureEnv(), options=config["learner"], effective_exploration=True)
        state = np.append(self.state, -.1).astype(np.float32)
        for _ in range(32):
            add_transition(model, state, [-.5, .2], -.2, state, False)
        self.assertEqual(model._n_updates, 0)
        add_transition(model, state, [.1, -.2], .1, state, True)
        self.assertEqual(model._n_updates, 4)
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            env = CrowdNavEnv(root / "episodes", settings, initial=config["initial_configuration"], scenario=True)
            self.assertEqual(env.observation_space.shape, (7,))
            self.assertTrue(env.scenario)
            self.assertTrue(env.observation_space.contains(state))
            checkpoint = root / "checkpoint"
            save_checkpoint(model, checkpoint, settings, 502, 0)
            loaded, progress = load_checkpoint(checkpoint, SevenFeatureEnv(), settings)
            self.assertEqual(progress["next_simulator_seed"], 502)
            self.assertEqual(loaded.num_timesteps, 33)
            self.assertEqual(loaded.replay_buffer.size(), 33)
            self.assertEqual(loaded.gradient_steps, 4)
            expected, _ = loaded.predict(state, deterministic=True)
            report = export_actor(checkpoint, root / "submission")
            self.assertEqual(report["rounded_rerouting_mismatches"], 0)
            actor = FrozenActor.load(root / "submission/checkpoint.json")
            self.assertEqual(len(actor.features), 7)
            np.testing.assert_allclose(actor(state), expected, atol=1e-5)
            add_transition(loaded, state, [.1, -.2], .1, state, False)
            self.assertEqual(loaded._n_updates, 8)

    def test_truncation_bootstraps_from_final_observation(self):
        replay = self.model.replay_buffer
        self.assertEqual(replay.timeouts[2, 0], 1)
        self.assertEqual(replay.dones[2, 0], 1)
        sample = replay._get_samples(np.array([2]))
        self.assertEqual(float(sample.dones[0, 0]), 0)
        np.testing.assert_allclose(sample.next_observations[0].numpy(), self.state * 1.01)

    def test_effective_exploration_critics_keep_distinct_rewards_and_survive_reload(self):
        settings = replace(Settings(), zero_exploration_for_negative_actions=True)
        model = make_model(SpacesOnlyEnv(), learning_starts=0, batch_size=2, effective_exploration=True)
        for u, value in ((-0.9, -0.25), (-0.1, 0.3), (0.4, 0.1)):
            add_transition(model, self.state, [u, 0.2], value, self.state, False)
        np.testing.assert_allclose(model.replay_buffer.rewards[:3, 0], [-0.25, 0.3, 0.1])
        obs = torch.tensor(self.state).repeat(3, 1)
        for critic in (model.critic, model.critic_target):
            actions = torch.tensor([[-0.9, 0.2], [-0.1, 0.2], [0.4, 0.2]], requires_grad=True)
            for q in (*critic(obs, actions), critic.q1_forward(obs, actions)):
                torch.testing.assert_close(q[0], q[1], rtol=0, atol=0)
            gradient, = torch.autograd.grad(critic(obs, actions)[0].sum(), actions)
            self.assertTrue(torch.all(gradient[:2, 0] == 0))
            self.assertNotEqual(gradient[2, 0].item(), 0)
            self.assertTrue(torch.all(gradient[:, 1] != 0))
        with tempfile.TemporaryDirectory() as tmp:
            cp = Path(tmp) / "checkpoint"
            save_checkpoint(model, cp, settings, 114, 0)
            loaded, _ = load_checkpoint(cp, SpacesOnlyEnv(), settings)
            self.assertIs(type(loaded.critic), type(model.critic))
            self.assertIs(type(loaded.critic_target), type(model.critic_target))
            report = export_actor(cp, Path(tmp) / "submission")
            self.assertEqual(report["rounded_rerouting_mismatches"], 0)

    def test_checkpoint_roundtrip_and_portable_export(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / "checkpoint"
            settings = Settings()
            save_checkpoint(self.model, checkpoint, settings, 102, 0)
            expected_rng = (random.random(), np.random.random(), float(torch.rand(1).item()))
            expected, _ = self.model.predict(self.state, deterministic=True)
            loaded, progress = load_checkpoint(checkpoint, SpacesOnlyEnv(), settings)
            actual, _ = loaded.predict(self.state, deterministic=True)
            np.testing.assert_array_equal(actual, expected)
            self.assertEqual(expected_rng, (random.random(), np.random.random(), float(torch.rand(1).item())))
            self.assertEqual(loaded.replay_buffer.size(), 3)
            self.assertEqual(progress["next_simulator_seed"], 102)
            for left, right in zip(self.model.actor.optimizer.state.values(), loaded.actor.optimizer.state.values()):
                self.assertTrue(torch.equal(left["exp_avg"], right["exp_avg"]))
            report = export_actor(checkpoint, Path(temporary) / "submission")
            self.assertLess(report["max_action_error"], 1e-5)
            self.assertGreaterEqual(report["states_checked"], 1000)
            self.assertEqual(report["rounded_rerouting_mismatches"], 0)
            path = Path(temporary) / "submission/UPISAS/strategies/my_strategy.py"
            spec = importlib.util.spec_from_file_location("exported_strategy", path)
            module = importlib.util.module_from_spec(spec)
            import sys
            sys.modules[spec.name] = module
            spec.loader.exec_module(module)
            portable = module.FrozenActor.load(Path(temporary) / "submission/checkpoint.json")
            np.testing.assert_allclose(portable(self.state), expected, atol=1e-5)

    def test_incompatible_settings_reject_replay(self):
        with tempfile.TemporaryDirectory() as temporary:
            checkpoint = Path(temporary) / "checkpoint"
            save_checkpoint(self.model, checkpoint, Settings(), 102, 0)
            with self.assertRaisesRegex(ValueError, "settings differ"):
                load_checkpoint(checkpoint, SpacesOnlyEnv(), replace(Settings(), window_ticks=200))

    def test_200_sample_settings_start_learning_at_transition_33(self):
        config = json.loads((Path(__file__).resolve().parents[1] / "SAC_SETTINGS.json").read_text())
        model = make_model(SpacesOnlyEnv(), options=config["learner"])
        for index in range(32):
            add_transition(model, self.state, [0.1, -0.2], 0.1, self.state, False)
        self.assertEqual(model._n_updates, 0)
        add_transition(model, self.state, [0.1, -0.2], 0.1, self.state, False)
        self.assertEqual(model._n_updates, 1)
        self.assertEqual(model.batch_size, 32)


if __name__ == "__main__":
    unittest.main()
