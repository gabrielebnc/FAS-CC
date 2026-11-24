"""Train, checkpoint, calibrate, and export SAC; never imported by deployment.

SAC losses, twin critics, target updates and entropy tuning are provided by
Stable-Baselines3. The explicit loop below controls costly simulator episodes
and checkpoints only at episode boundaries, so resuming starts a fresh seed.
"""

import argparse
from dataclasses import asdict, replace
import hashlib
import importlib.metadata
import json
from pathlib import Path
import platform
import random
import shutil
import time

import numpy as np
import torch
from stable_baselines3 import SAC
from stable_baselines3.common.logger import configure

from UPISAS.strategies.my_strategy import FEATURES, FrozenActor, Settings, action_configuration
from training_runtime import CrowdNavEnv, DockerRun, INITIAL, ROOT, summarize, write_json


def make_model(env, learner_seed=0, learning_starts=256, batch_size=64, options=None):
    torch.set_num_threads(1)
    parameters = dict(policy_kwargs=dict(net_arch=dict(pi=[32, 32], qf=[64, 64])),
        learning_rate=3e-4, buffer_size=50_000, learning_starts=learning_starts,
        batch_size=batch_size, gamma=0.95, tau=0.005, train_freq=1, gradient_steps=1,
        ent_coef="auto", target_entropy=-2.0, device="cpu", seed=learner_seed,
    )
    if options:
        parameters.update(options)
    model = SAC("MlpPolicy", env, **parameters)
    model.set_logger(configure(format_strings=[]))
    return model


def save_checkpoint(model, directory, settings, next_simulator_seed, learner_seed):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=False)
    model.save(directory / "model.zip")  # Includes actor/critic/entropy optimizers.
    model.save_replay_buffer(directory / "replay.pkl")
    torch.save(dict(python=random.getstate(), numpy=np.random.get_state(), torch=torch.get_rng_state(),
                    action_space=model.action_space.np_random.bit_generator.state), directory / "rng.pt")
    write_json(directory / "settings.json", asdict(settings))
    write_json(directory / "progress.json", dict(
        transitions=model.num_timesteps, gradient_updates=model._n_updates,
        next_simulator_seed=next_simulator_seed, learner_seed=learner_seed,
        boundary="fresh_episode", packages={name: importlib.metadata.version(name)
            for name in ("stable-baselines3", "torch", "gymnasium", "numpy", "requests")},
    ))


def load_checkpoint(directory, env, settings):
    directory = Path(directory)
    if json.loads((directory / "settings.json").read_text()) != asdict(settings):
        raise ValueError("Checkpoint settings differ: replay rewards and features would be incompatible")
    model = SAC.load(directory / "model.zip", env=env, device="cpu")
    model.set_logger(configure(format_strings=[]))
    model.load_replay_buffer(directory / "replay.pkl")
    progress = json.loads((directory / "progress.json").read_text())
    # Load only our own local trusted checkpoint; this file includes Python RNG tuples.
    rng = torch.load(directory / "rng.pt", map_location="cpu", weights_only=False)
    random.setstate(rng["python"])
    np.random.set_state(rng["numpy"])
    torch.set_rng_state(rng["torch"])
    model.action_space.np_random.bit_generator.state = rng["action_space"]
    return model, progress


def add_transition(model, state, action, value, next_state, truncated):
    # The physical knobs are logged separately; replay keeps the normalized action.
    # A time limit bootstraps from this measured next_state, never a reset state.
    model.replay_buffer.add(
        np.asarray(state, dtype=np.float32)[None], np.asarray(next_state, dtype=np.float32)[None],
        np.asarray(action, dtype=np.float32)[None], np.array([value], dtype=np.float32),
        np.array([truncated]), [dict(**{"TimeLimit.truncated": truncated})],
    )
    model.num_timesteps += 1
    if model.num_timesteps > model.learning_starts and model.replay_buffer.size() >= model.batch_size:
        model.train(gradient_steps=1, batch_size=model.batch_size)


def train(args):
    settings = Settings(**json.loads(args.settings.read_text())) if args.settings else Settings()
    if not args.smoke and args.settings is None and args.resume is None:
        raise ValueError("Provide calibrated --settings for training; --smoke explicitly permits pilot scales")
    if args.resume and args.settings is None:
        settings = Settings(**json.loads((args.resume / "settings.json").read_text()))
    args.output.mkdir(parents=True, exist_ok=False)
    write_json(args.output / "settings.json", asdict(settings))
    (args.output / "packages.txt").write_text("\n".join(sorted(
        f"{d.metadata['Name']}=={d.version}" for d in importlib.metadata.distributions())) + "\n")
    write_json(args.output / "experiment.json", dict(
        kind="integration-smoke-only" if args.smoke else "training", python=platform.python_version(),
        learner_seed=args.learner_seed, first_simulator_seed=args.simulator_seed,
        requested_transitions=args.transitions, episode_ticks=args.episode_ticks,
        settlement_validation="provisional; duration heuristic, not a clean-cohort guarantee",
        resume=str(args.resume) if args.resume else None,
    ))
    env = CrowdNavEnv(args.output / "episodes", settings, args.episode_ticks, args.simulator_seed,
                      args.crowdnav_image, args.api_image)
    model = None
    start_time = time.monotonic()
    try:
        if args.resume:
            model, progress = load_checkpoint(args.resume, env, settings)
            env.next_seed = progress["next_simulator_seed"]
            learner_seed = progress["learner_seed"]
        else:
            model = make_model(env, args.learner_seed, 0 if args.smoke else 256, 2 if args.smoke else 64)
            learner_seed = args.learner_seed
        initial_actor = [parameter.detach().clone() for parameter in model.actor.parameters()]
        initial_critic = [parameter.detach().clone() for parameter in model.critic.parameters()]
        initial_target = [parameter.detach().clone() for parameter in model.critic_target.parameters()]
        initial_alpha = float(model.log_ent_coef.detach().item())
        target = model.num_timesteps + args.transitions
        with (args.output / "learning.jsonl").open("w", buffering=1) as learning:
            while model.num_timesteps < target:
                state, _ = env.reset()
                ended = False
                while not ended:
                    if model.num_timesteps < model.learning_starts:
                        action = model.action_space.sample()
                    else:
                        action, _ = model.predict(state, deterministic=False)
                    next_state, value, terminated, truncated, info = env.step(action)
                    if terminated:
                        raise RuntimeError("Continuing CrowdNav must not have an absorbing terminal")
                    budget_end = model.num_timesteps + 1 == target
                    ended = truncated or budget_end
                    add_transition(model, state, action, value, next_state, ended)
                    record = dict(transition=model.num_timesteps, episode=env.episode,
                                  simulator_seed=env.current_seed, reward=value, state=list(map(float, state)),
                                  action=list(map(float, action)), gradient_updates=model._n_updates,
                                  alpha=float(model.log_ent_coef.detach().exp().item()),
                                  time_limit=ended, reason="horizon" if truncated else "budget" if budget_end else None,
                                  **model.logger.name_to_value)
                    learning.write(json.dumps(record, allow_nan=False) + "\n")
                    print(f"SAC transition {model.num_timesteps}: reward={value:.4f}, "
                          f"updates={model._n_updates}, seed={env.current_seed}", flush=True)
                    state = next_state
                    if budget_end and not truncated:
                        env.run.wait_finished()  # Same whole-run horizon, holding the last action.
                        summarize(env.run.directory)
                env.close()
                save_checkpoint(model, args.output / f"checkpoint-{model.num_timesteps:06d}", settings,
                                env.next_seed, learner_seed)
        def changed(previous, parameters):
            return any(not torch.equal(a, b.detach()) for a, b in zip(previous, parameters))
        diagnostics = dict(transitions=model.num_timesteps, gradient_updates=model._n_updates,
                           replay_size=model.replay_buffer.size(),
                           actor_changed=changed(initial_actor, model.actor.parameters()),
                           critics_changed=changed(initial_critic, model.critic.parameters()),
                           targets_changed=changed(initial_target, model.critic_target.parameters()),
                           entropy_changed=float(model.log_ent_coef.detach().item()) != initial_alpha,
                           wall_seconds=time.monotonic() - start_time,
                           performance_claim="none; integration smoke" if args.smoke else "requires held-out evaluation")
        write_json(args.output / "training_result.json", diagnostics)
        if args.smoke and not all(diagnostics[key] for key in
                                 ("actor_changed", "critics_changed", "targets_changed", "entropy_changed")):
            raise RuntimeError("Smoke did not exercise every SAC update; inspect training_result.json")
    finally:
        env.close()


def calibrate(args):
    """Calibrate input scales and throughput floor, without claiming timing validation."""
    args.output.mkdir(parents=True, exist_ok=False)
    run = DockerRun(args.output / "static", args.simulator_seed, args.episode_ticks,
                    args.crowdnav_image, args.api_image)
    exemplar = None
    try:
        exemplar = run.start()
        run.wait_finished(timeout=900)
        summary = summarize(run.directory)
    finally:
        if exemplar is not None:
            exemplar.close()
        run.close()
    settings = Settings()
    trips = [json.loads(line) for line in (run.directory / "trips.jsonl").read_text().splitlines()]
    start = settings.warmup_ticks + settings.settlement_ticks
    windows = []
    for end in range(start + settings.window_ticks, args.episode_ticks + 1, settings.window_ticks):
        events = [trip for trip in trips if end - settings.window_ticks < trip["tick"] <= end]
        windows.append(dict(start_step=end - settings.window_ticks, end_step=end, trips=len(events),
                            rate=len(events) / settings.window_ticks))
    if len(windows) < 3 or not any(window["trips"] for window in windows):
        raise ValueError("Calibration needs at least three full windows and recorded completions")
    measured = [trip for trip in trips if start < trip["tick"] <= windows[-1]["end_step"]]
    floor = float(np.quantile([window["rate"] for window in windows], 0.1, method="linear"))
    if floor <= 0:
        raise ValueError("Baseline throughput floor is zero; collect longer calibration windows")
    settings = replace(settings, overhead_scale=float(np.mean([t["overhead"] for t in measured])),
                       rate_scale=len(measured) / (windows[-1]["end_step"] - start), rate_floor=floor,
                       calibration=f"baseline-seed-{args.simulator_seed}; normalization only; timing provisional")
    write_json(args.output / "settings.json", asdict(settings))
    write_json(args.output / "calibration.json", dict(windows=windows, quantile_method="linear, p=0.1",
               summary=summary, limitation="One static hold calibrates scales, not action settlement timing"))
    print(f"Calibration saved: {args.output / 'settings.json'}", flush=True)


def export_actor(checkpoint, output):
    checkpoint, output = Path(checkpoint), Path(output)
    model = SAC.load(checkpoint / "model.zip", device="cpu")
    settings = Settings(**json.loads((checkpoint / "settings.json").read_text()))
    if model._n_updates == 0:
        raise ValueError("Refusing to export an actor with zero learning updates")
    layers = [layer for layer in model.actor.latent_pi if isinstance(layer, torch.nn.Linear)]
    layers.append(model.actor.mu)
    data = dict(model_id=hashlib.sha256((checkpoint / "model.zip").read_bytes()).hexdigest(),
                features=list(FEATURES), settings=asdict(settings), gradient_updates=model._n_updates,
                layers=[dict(weight=layer.weight.detach().cpu().tolist(), bias=layer.bias.detach().cpu().tolist())
                        for layer in layers])
    actor = FrozenActor(data)
    generator = np.random.default_rng(0)
    states = np.column_stack([generator.uniform(0, 10, 1000), generator.uniform(0, 4, 1000),
                              generator.uniform(0, 2, 1000), generator.uniform(-1, 1, 1000),
                              generator.uniform(-1, 1, 1000), generator.uniform(-1, 1, 1000)]).astype(np.float32)
    states = np.vstack([states, np.array([[0, 0, 0, 0, -1, -1], [30, 4, 2, -1, 1, 1]], dtype=np.float32)])
    expected, _ = model.predict(states, deterministic=True)
    actual = np.array([actor(state) for state in states])
    maximum = float(np.max(np.abs(expected - actual)))
    if maximum > 1e-5:
        raise ValueError(f"Export disagrees with SAC inference: max error={maximum}")
    physical_mismatches = []
    for index, (a, b) in enumerate(zip(expected, actual)):
        ca, cb = (action_configuration(u, INITIAL, settings) for u in (a, b))
        if ca["re_route_every_ticks"] != cb["re_route_every_ticks"]:
            physical_mismatches.append(index)
    if physical_mismatches:
        raise ValueError(f"Rounded physical actions disagree for states {physical_mismatches}")
    output.mkdir(parents=True, exist_ok=False)
    for name in ("UPISAS/exemplars/crowdnav.py", "run_challenge.py", "UPISAS/strategies/my_strategy.py"):
        destination = output / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    shutil.copyfile(ROOT / "requirements.txt", output / "requirements.txt")
    write_json(output / "checkpoint.json", data)
    # Verification evidence stays outside the five-file submission.
    report = dict(model_id=data["model_id"], states_checked=len(states), max_action_error=maximum,
                  rounded_rerouting_mismatches=0, gradient_updates=model._n_updates)
    write_json(output.parent / (output.name + "-parity.json"), report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="mode", required=True)
    for name in ("train", "calibrate"):
        sub = subparsers.add_parser(name)
        sub.add_argument("--output", type=Path, required=True)
        sub.add_argument("--episode-ticks", type=int, default=10_000)
        sub.add_argument("--simulator-seed", type=int, default=101)
        sub.add_argument("--crowdnav-image", default="crowdnav-sac/simulation:local")
        sub.add_argument("--api-image", default="crowdnav-sac/api:local")
        if name == "train":
            sub.add_argument("--transitions", type=int, required=True)
            sub.add_argument("--settings", type=Path)
            sub.add_argument("--learner-seed", type=int, default=0)
            sub.add_argument("--smoke", action="store_true")
            sub.add_argument("--resume", type=Path)
    export = subparsers.add_parser("export")
    export.add_argument("--checkpoint", type=Path, required=True)
    export.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.mode == "export":
        print(json.dumps(export_actor(args.checkpoint, args.output), indent=2))
    else:
        if args.episode_ticks < 6000 or not 0 <= args.simulator_seed < 2**31:
            parser.error("Use at least 6000 episode ticks and a nonnegative 31-bit simulator seed")
        if args.mode == "train" and args.transitions <= 0:
            parser.error("--transitions must be positive")
        (train if args.mode == "train" else calibrate)(args)


if __name__ == "__main__":
    main()
