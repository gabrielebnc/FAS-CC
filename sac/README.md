# SAC adaptation for CrowdNav

[Checkpoint settings and artifact paths for the two 200-transition runs](CHECKPOINTS.md)

For a new run using the zero-exploration action mapping, set
`training.critic_uses_effective_exploration` to `true` in its experiment config
to feed `max(0, u)` into both online and target critics. This applies to replay
updates and actor updates. The actor still samples its original two outputs,
and replay retains each transition's measured reward. Earlier configs use the
original critics. The exported deployment actor requires no additional files.

This is a clean, separate implementation. The earlier reactive and rule-based
experiments in `../UPISAS/` remain untouched and are not imported here.
The initial integration smoke test is complete. The next attempt collects 200
training transitions, then compares a frozen actor with static and rule-based policies.
All training and deployment settings for that attempt are documented in
[SAC_SETTINGS.json](SAC_SETTINGS.json). Simulation speed changes were aborted at
the user's request; this attempt uses the original runtime and Docker resources.

## What runs where

CrowdNav/SUMO, Kafka, and the HTTP API run in Docker. UPISAS and SAC training run
in local Python. There is no controller container. Each training episode owns
a fresh, uniquely named Docker stack and removes only that stack when finished.

```text
CrowdNav -> Kafka -> GET /monitor/ -> UPISAS observation window
                                      |
                              SAC chooses two settings
                                      |
CrowdNav <- Kafka <- PUT /execute/ <- complete nine-knob command
    |
confirm settings -> settle -> measure -> reward -> replay -> SAC update
```

The framework files are the original UPISAS revision `63d7b66`. `Crowdnav/` is
CrowdNav revision `9d376aa`, with only the API compatibility fixes needed for
the legacy Kafka broker: aiokafka 0.12, monitoring reconnection, matching Kafka
exception types, and producer version negotiation. Its routing, traffic model,
arrival metrics, map, and warmup are unchanged. No previous policy is included.

## Code map

| File | Responsibility |
| --- | --- |
| `UPISAS/exemplars/crowdnav.py` | HTTP snapshots and routing configuration commands |
| `UPISAS/strategies/my_strategy.py` | Window arithmetic, six features, reward, action mapping, application/settlement phases, frozen actor inference |
| `run_challenge.py` | Deployment loop against an externally managed CrowdNav |
| `training.py` | SB3 SAC, explicit training loop, calibration, checkpoint save/load, actor export |
| `training_runtime.py` | Fresh seeded Docker episodes and Gymnasium adapter |
| `simulation_worker.py` | Seed upstream SUMO/Python, record original trip events and durations, stop at the exact horizon |
| `verify_run.py` | Compare controller windows with raw trip events |
| `smoke_deployment.py` | Run the exported submission while blocking training-library imports |

UPISAS `Strategy` and `Knowledge` are unchanged. Monitoring history is bounded.
Transport failures, unexpected configuration changes, simulator resets, and
incomplete windows do not become ordinary replay transitions.

## State, actions, and reward

The six actor inputs, in order, are:

1. Recent completed-trip mean overhead divided by a frozen calibration scale.
2. Completed smart-car trips per tick divided by a frozen reference rate.
3. Actual active-car count divided by 750.
4. Change in actual active-car count per tick, scaled by `600 / 750`.
5. Current exploration, normalized to `[-1, 1]`.
6. Current rerouting interval, logarithmically normalized to `[-1, 1]`.

The two continuous SAC outputs are in `[-1, 1]`. Exploration maps linearly to
`[0, 0.5]`; rerouting maps logarithmically to `[30, 600]` ticks and is rounded to
an integer. The other seven knobs, including target population, are preserved.
The starting experiment configuration is 50% exploration, 600-tick rerouting,
750 cars, sigma 0.2, static/dynamic factors 1, freshness factor 10, cutoff 90,
and historical edge influence 140.

The initial configuration is held through warmup, settlement, and one window
before the first action. After each action:

1. Confirm the routing settings in a fresh monitoring snapshot. HTTP acceptance
   alone does not start the clock. Retry the same command at most three times.
   Commands contain the two controlled knobs. External target population changes
   are accepted and logged, while unexpected routing changes invalidate the window.
2. Hold for 1,800 settlement ticks, excluding those completions from the reward.
3. Measure for 600 ticks, using actual observed boundaries.
4. Store one transition; reuse this window as the next pre-action window.

The reward is

```text
(overhead_before - overhead_after) / max(overhead_before, 1e-6)
    - max(0, 1 - completion_rate_after / calibrated_rate_floor)
```

The throughput floor is the tenth percentile (linear interpolation) of static
calibration-window completion rates. Calibration uses training seeds only.
The current cumulative averages have an upstream off-by-one denominator;
window sums are reconstructed using `(completed_trips + 1) * published_average`.
`verify_run.py` checks this against exact arrival events.

Windows with fewer than 30 completions are marked low confidence and retained.
Zero completions produce reward `-2`, keep measured overhead null in the log,
and use the simulator's overhead cap of 30 only for the next observation.
A missing pre-action mean cannot generate an apparent measured improvement.

The timing values are provisional duration-based probes from the design notes.
Static calibration estimates input scales and throughput; it does **not** prove
that 1,800 ticks eliminate carryover from previous actions. Full training needs
step-response checks across action directions and more calibration seeds.

SAC uses an actor with two 32-unit ReLU layers, twin critics with two 64-unit
ReLU layers each, target critics, and automatic entropy tuning. Learning rate is
`3e-4`, replay capacity 50,000, gamma 0.95 per adaptation decision, and tau 0.005.
Normal training starts learning after 256 transitions with batch size 64.
The smoke test explicitly uses learning start 0 and batch size 2 to exercise an
update with very little real simulation data. It is not a performance study.
SAC implementation: [Stable-Baselines3 documentation](https://stable-baselines3.readthedocs.io/en/master/modules/sac.html).

## Setup and smoke test

Run commands from this directory with Docker running:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-training.txt
docker build -t crowdnav-sac/simulation:local Crowdnav/crowdnav
docker build -t crowdnav-sac/api:local Crowdnav/api
.venv/bin/python -m unittest discover -s tests -v

.venv/bin/python training.py calibrate \
  --episode-ticks 10000 --simulator-seed 101 \
  --output experiments/calibration-smoke-101

.venv/bin/python training.py train --smoke --transitions 2 \
  --episode-ticks 10000 --simulator-seed 102 \
  --settings experiments/calibration-smoke-101/settings.json \
  --output experiments/sac-smoke-102

.venv/bin/python verify_run.py \
  experiments/sac-smoke-102/episodes/episode-0001-seed-102

.venv/bin/python training.py export \
  --checkpoint experiments/sac-smoke-102/checkpoint-000002 \
  --output experiments/smoke-submission

.venv/bin/python smoke_deployment.py \
  --submission experiments/smoke-submission \
  --seed 103 --output experiments/deployment-smoke-103
```

Output directories must be new. Each episode saves source hashes, image IDs,
all trip events, trip departure/arrival timing, unfinished-trip ages, settings,
actions, observed confirmation steps, measurement boundaries, and raw logs.
`learning.jsonl` includes losses, rewards, entropy coefficient, and update counts.
`training_result.json` reports whether actor, critics, targets, and entropy changed.
The tested local package resolution is saved in `requirements-training-lock.txt`.
Container dependency versions come from the selected images, whose IDs are logged.

One RL transition is a full adaptation lifecycle, **not** a SUMO tick. With these
holds and a conservative application reserve, only about two complete transitions
fit in a 10,000-tick episode. The final action is held through the remaining tail;
all reported whole-run metrics include that tail and the settlement periods.

## Submission and checkpoint

Export creates exactly these five files:

```text
UPISAS/exemplars/crowdnav.py
UPISAS/strategies/my_strategy.py
run_challenge.py
requirements.txt
checkpoint.json
```

`checkpoint.json` contains the trained actor's two hidden layers and mean head,
feature order, normalization, timing, bounds, model ID, and learning update count.
It is an inference checkpoint: the log-standard-deviation head and critics are
unneeded for deterministic deployment. Export checks at least 1,000 states
against SB3 inference, requiring action error below `1e-5` and identical rounded
rerouting settings. The parity report stays outside the submission directory.

Copy the five files over an original UPISAS checkout, preserving their paths.
Start CrowdNav separately, then run from that checkout:

```sh
python -m pip install -r requirements.txt
python run_challenge.py --endpoint http://localhost:8080
```

The default checkpoint path is next to `run_challenge.py`; `--checkpoint` can
override it. No training library or training tool is imported. Missing or invalid
weights fail explicitly; deployment never substitutes a random or old policy.
A smoke checkpoint is usable for integration only and must be replaced by a
properly trained, validated actor before assessing submission performance.

Full training checkpoints remain outside the submission. They include `model.zip`
(networks and optimizers), replay, RNG states, frozen settings, package versions,
and progress. Checkpoints are saved at episode boundaries. `--resume` restores
learning state and starts a fresh next simulator seed; it does not restore a
half-finished traffic simulation. `--transitions` specifies additional transitions
when resuming. The 200-transition attempt is run separately using the complete
configuration in `SAC_SETTINGS.json`:

```sh
.venv/bin/python training.py train --config SAC_SETTINGS.json \
  --output experiments/sac-200-native
.venv/bin/python compare_policies.py \
  --checkpoint experiments/sac-200-native/checkpoint-000200 \
  --output experiments/comparison-sac-200-seed301 --seed 301
```

## Verified smoke outcome

The completed local check used static calibration seed 101, training smoke seed
102, and frozen deployment smoke seed 103, each at exactly 10,000 ticks. All 19
tests passed. The two training transitions produced one real SAC gradient update;
actor, critics, targets, and entropy coefficient all changed. Three reconstructed
windows matched raw trip-event means within `1.5e-14`.

The exported actor matched SB3 on 1,002 states, with maximum action error
`9.99e-8` and no rounded rerouting differences. Its five-file submission completed
a fresh 10,000-tick run with training-library imports blocked, including two
confirmed actions and three window checks within `4.5e-14` of raw trip means.
The full checkpoint also reloaded its two replay entries, one gradient update,
optimizer state, entropy state, and next simulator seed.

Evidence: [training diagnostics](experiments/sac-smoke-102/training_result.json),
[export parity](experiments/smoke-submission-parity.json), and
[deployment audit](experiments/deployment-smoke-103/audit.json).
The ready-to-copy integration package is in `experiments/smoke-submission/`.
These checks establish a working learning/deployment path; one update does not
establish a useful policy or an improvement over static routing. No long training
or held-out performance evaluation was run.

## 200-transition comparison

The original-runtime attempt completed 200 training transitions and 168 gradient
updates across five 100,000-tick episodes. Training took about 2 h 36 min. All 200
transitions passed an audit against the raw trip events. No Docker resource or
simulation-speed changes were retained.

The frozen final actor and both references were evaluated on fresh seed 301,
with 10,000 ticks and a target of 750 cars per run.

| Policy | Mean overhead | Median overhead | p95 overhead | Recorded smart-car trips | Complaint rate |
| --- | ---: | ---: | ---: | ---: | ---: |
| Static (50% exploration, 600-tick rerouting) | 3.234 | 2.509 | 7.781 | 2,641 | 25.33% |
| Rule-based | 2.394 | 2.028 | 4.910 | 3,438 | 16.17% |
| SAC, 200 samples | 2.891 | 2.380 | 6.482 | 2,899 | 23.11% |

SAC reduced mean overhead by 10.59% versus static, but its mean overhead was
20.77% higher than the rule-based policy. This is a single-seed pilot. The
rule-based source matches the exact previous implementation.

Results: [full metrics](experiments/comparison-sac-200-seed301/results.json),
[verification](experiments/comparison-sac-200-seed301/verification.json),
[overhead PNG](experiments/comparison-sac-200-seed301/overhead_comparison.png),
and [trip-count PNG](experiments/comparison-sac-200-seed301/completed_trips_comparison.png).
The five-file submission is in `experiments/sac-200-native-submission/`; its
actor matches SB3 inference within `8.70e-8` on 1,002 states with identical rounded
rerouting intervals.
