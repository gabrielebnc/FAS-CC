"""Collect independent CrowdNav rollouts concurrently; update one SAC learner.

Only environments run in threads. Action sampling, replay insertion, and gradient
updates run in a fixed order on the main thread. Simulator gates prevent waiting
workers from advancing through an unobserved decision boundary.
"""

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
from pathlib import Path
import shutil
import time

import numpy as np
import torch

from training import add_transition, make_model, save_checkpoint
from training_runtime import CrowdNavEnv, ROOT, summarize, write_json
from UPISAS.strategies.my_strategy import Settings
from verify_run import verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    config = json.loads(args.config.read_text())
    experiment = config["training"]
    settings = Settings(**config["environment"])
    if not config["runtime"]["controller_gate"]:
        raise ValueError("Concurrent rollouts require controller gates")
    args.output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(args.config, args.output / "SAC_SETTINGS.json")
    # Preserve the exact implementation used for this attempt, outside submission.
    for source in [*ROOT.glob("*.py"), *ROOT.glob("requirements*.txt"), *(ROOT / "UPISAS").rglob("*.py")]:
        destination = args.output / "source" / source.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
    source_hashes = {str(p.relative_to(args.output / "source")): hashlib.sha256(p.read_bytes()).hexdigest()
                     for p in (args.output / "source").rglob("*") if p.is_file()}
    write_json(args.output / "source_hashes.json", source_hashes)
    workers, budget = experiment["workers"], experiment["transitions"]
    envs = [CrowdNavEnv(args.output / f"worker-{index}", settings,
                experiment["max_episode_ticks"], experiment["first_simulator_seed"] + index,
                config["runtime"]["crowdnav_image"], config["runtime"]["api_image"],
                fast_poll=config["runtime"]["fast_command_poll"], gated=True, seed_stride=workers)
            for index in range(workers)]
    model = make_model(envs[0], learner_seed=experiment["learner_seed"], options=config["learner"])
    initial_actor = [p.detach().clone() for p in model.actor.parameters()]
    initial_critics = [p.detach().clone() for p in model.critic.parameters()]
    initial_targets = [p.detach().clone() for p in model.critic_target.parameters()]
    initial_alpha = model.log_ent_coef.detach().item()
    started, wave, audits = time.monotonic(), 0, []
    try:
        with ThreadPoolExecutor(max_workers=workers) as executor, (args.output / "learning.jsonl").open("w", buffering=1) as log:
            while model.num_timesteps < budget:
                wave += 1
                active = envs[:min(workers, budget - model.num_timesteps)]
                # Avoid simulating a long unused tail in the final rollout wave.
                steps_each = math.ceil((budget - model.num_timesteps) / len(active))
                horizon = min(experiment["max_episode_ticks"], settings.warmup_ticks
                              + settings.settlement_ticks + settings.window_ticks
                              + steps_each * (settings.settlement_ticks + settings.window_ticks + 100) + 1000)
                for env in active:
                    env.episode_ticks = horizon
                states = [item[0] for item in executor.map(lambda env: env.reset(), active)]
                ended = False
                while not ended:
                    used = active[:min(len(active), budget - model.num_timesteps)]
                    actions = []
                    for index, state in enumerate(states[:len(used)]):
                        if model.num_timesteps + index < model.learning_starts:
                            actions.append(model.action_space.sample())
                        else:
                            actions.append(model.predict(state, deterministic=False)[0])
                    results = list(executor.map(lambda pair: pair[0].step(pair[1]), zip(used, actions)))
                    ended = any(result[3] for result in results) or model.num_timesteps + len(results) >= budget
                    for index, (env, action, result) in enumerate(zip(used, actions, results)):
                        next_state, value, terminated, truncated, info = result
                        if terminated:
                            raise RuntimeError("CrowdNav is continuing, not an absorbing terminal")
                        add_transition(model, states[index], action, value, next_state, ended)
                        record = dict(transition=model.num_timesteps, worker=index, wave=wave,
                            simulator_seed=env.current_seed, observation=list(map(float, states[index])),
                            action=list(map(float, action)), reward=value, gradient_updates=model._n_updates,
                            alpha=model.log_ent_coef.detach().exp().item(), truncated=ended,
                            **model.logger.name_to_value)
                        log.write(json.dumps(record, allow_nan=False) + "\n")
                        print(f"SAC {model.num_timesteps}/{budget}: seed={env.current_seed}, "
                              f"reward={value:+.4f}, updates={model._n_updates}", flush=True)
                        states[index] = next_state
                # Every checkpoint is an episode boundary, including the final budget.
                def finish(env):
                    env.run.wait_finished(timeout=1800)
                    summary = summarize(env.run.directory)
                    audit = verify(env.run.directory)
                    directory = str(env.run.directory)
                    env.close()
                    return dict(directory=directory, summary=summary, audit=audit)
                audits.extend(executor.map(finish, active))
                checkpoint = args.output / f"checkpoint-{model.num_timesteps:06d}"
                save_checkpoint(model, checkpoint, settings, min(env.next_seed for env in active), experiment["learner_seed"])
                write_json(checkpoint / "parallel_progress.json", dict(
                    next_simulator_seeds=[env.next_seed for env in envs], workers=workers,
                    settings_file="../../SAC_SETTINGS.json", boundary="all_workers_fresh_episode"))
                write_json(args.output / "progress.json", dict(
                    transitions=model.num_timesteps, gradient_updates=model._n_updates,
                    wave=wave, wall_seconds=time.monotonic() - started, checkpoint=str(checkpoint)))
        def changed(old, current):
            return any(not torch.equal(a, b.detach()) for a, b in zip(old, current))
        write_json(args.output / "training_result.json", dict(
            transitions=model.num_timesteps, gradient_updates=model._n_updates,
            replay_size=model.replay_buffer.size(), actor_changed=changed(initial_actor, model.actor.parameters()),
            critics_changed=changed(initial_critics, model.critic.parameters()),
            targets_changed=changed(initial_targets, model.critic_target.parameters()),
            entropy_changed=initial_alpha != model.log_ent_coef.detach().item(),
            wall_seconds=time.monotonic() - started, rollouts=audits))
    finally:
        for env in envs:
            env.close()


if __name__ == "__main__":
    main()
