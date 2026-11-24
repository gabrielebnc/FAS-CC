# Experiments before and during SAC training

The first objective is to make action outcomes measurable. Network training comes after the timing and transition contract is trustworthy. The architecture and proposed reward are in [architecture.md](architecture.md).

## Initial analysis of saved runs

On 19 November 2026, the existing trip-event files were regrouped into complete, nonoverlapping windows `(start, start + W]`, starting at tick 200 and ending no later than tick 10,000. The five matched seeds are `11, 22, 33, 44, 55`.

Sources: local batches [paired-20261111T091155Z](../../experiments/paired-20261111T091155Z/) for seed 11 and [paired-20261111T091912Z](../../experiments/paired-20261111T091912Z/) for seeds 22-55. Each run has `trips.jsonl`, `result.json`, and batch metadata/source hashes. The baseline is the bad static configuration; the adaptive runs use the older reactive strategy, not SAC and not necessarily the current working-tree strategy.

| Policy | Window ticks | Number of windows | Minimum trips per window | Median trips per window | Windows with fewer than 30 trips |
| --- | ---: | ---: | ---: | ---: | ---: |
| Static bad baseline | 200 | 245 | 37 | 55 | 0 |
| Reactive | 200 | 245 | 48 | 73 | 0 |
| Static bad baseline | 600 | 80 | 133 | 165 | 0 |
| Reactive | 600 | 80 | 169 | 220 | 0 |

The ten runs contain 31,337 trip events: 13,538 baseline and 17,799 reactive. All reported windows contain completions. At 600 ticks, the incomplete final 200-tick tail is excluded from this window-count analysis; it remains part of the full-run score.

These counts address sample quantity only. They do not justify either window immediately after an action: a completed trip can carry most of its duration from the old configuration. They do not establish independent samples, a sufficient settlement interval, reliable means for arbitrary SAC actions, or any SAC improvement. These are descriptive observations from older runs, not a train/test benchmark for the new policy.

The same runs' `containers.log` files retain the published cumulative average trip duration at tick 10,000. Correcting its off-by-one denominator gives:

| Seed | Bad baseline mean duration, ticks | Reactive mean duration, ticks |
| --- | ---: | ---: |
| 11 | 549.23 | 413.11 |
| 22 | 563.62 | 419.19 |
| 33 | 540.04 | 403.18 |
| 44 | 518.06 | 404.75 |
| 55 | 526.03 | 426.13 |
| Pooled over completed trips | 539.23 | 412.88 |

For each run, `true_mean_duration = published_duration * (n + 1) / n`. Pool with trip-count weights, rather than giving differently sized runs equal weight. The existing event files do not contain individual durations, so this calculation cannot provide a duration p95 or detect unfinished-trip censoring.

On these means, 200 ticks is only 37-48% of an average trip duration. This ratio is a timescale comparison, not an estimate of the fraction of a trip affected or the fraction of clean completions. It makes duration-based settlement a prerequisite. Even 600 ticks spans only about 1.1 baseline mean trip durations before allowing for routing delay and duration tails.

The table can be reproduced from the `UPISAS/` directory using the standard library:

```python
import json
import statistics
from pathlib import Path

batches = [Path("experiments/paired-20261111T091155Z"),
           Path("experiments/paired-20261111T091912Z")]
for policy in ("baseline", "strategy"):
    runs = []
    for batch in batches:
        for run in sorted(batch.glob(f"*-{policy}")):
            if (run / "result.json").exists():
                runs.append([json.loads(line) for line in
                             (run / "trips.jsonl").read_text().splitlines()])
    for width in (200, 600):
        counts = [sum(start < e["tick"] <= start + width for e in events)
                  for events in runs
                  for start in range(200, 10_000 - width + 1, width)]
        print(policy, width, len(counts), min(counts),
              statistics.median(counts), sum(n < 30 for n in counts))
```

To reproduce the duration table using the same `batches` list:

```python
import re

pattern = re.compile(r"Step:(\d+).*?avgTripDuration:\s*([\d.eE+-]+)\((\d+)\)")
for policy in ("baseline", "strategy"):
    total_duration = total_trips = 0
    for batch in batches:
        for run in sorted(batch.glob(f"*-{policy}")):
            if not (run / "result.json").exists():
                continue
            records = pattern.findall((run / "containers.log").read_text())
            step, published, trips = max(
                (int(t), float(a), int(n)) for t, a, n in records)
            assert step == 10_000 and trips > 0
            trip_sum = (trips + 1) * published
            print(run.name, trip_sum / trips)
            total_duration += trip_sum
            total_trips += trips
    print(policy, "pooled duration", total_duration / total_trips)
```

The raw experiment directories are ignored local artifacts. Preserve their provenance if sharing the results; they will not automatically accompany this design folder in a clone.

## Experiment 0: measurement and application

Implement the phase machine with scripted actions before introducing a learner. Keep all new source in the three required files and use the existing original-framework runtime.

Check these behaviors with small deterministic fixtures and one instrumented live smoke run:

- A duplicate monitoring step creates no action or replay entry.
- HTTP success without a matching later configuration never starts settlement.
- A delayed application anchors settlement to the observed application step.
- No-op actions still consume the same settlement/measurement contract.
- All nine knobs are sent; the seven uncontrolled settings remain fixed.
- Counter/step resets, configuration drift, and timeouts discard the pending transition and remain visible in logs.
- The reconstructed window sum matches exact recorded trip-event sums, allowing floating-point tolerance and aligning actual observed boundaries.
- Zero-trip and low-throughput windows receive the documented treatment instead of being silently skipped.
- Partial final windows never become ordinary training transitions.

For an arithmetic fixture, overheads `[2, 3, 5]` produce published average `10 / 4` after three trips. Taking a boundary after the first trip must yield recent mean `(10 - 2) / 2 = 4`, not a difference computed with `n * published_average`.

The existing runner's printed mean and the older reactive estimator need the aggregation correction when those code paths are updated. Compare SAC measurements with raw events rather than assuming the existing summary is an exact oracle.

## Experiment 1: calibrate duration, exposure, and settlement

Use simulator seeds `101, 102, 103` for initial timing work. Hold the map, 750 cars, smart-car fraction, other knobs, warmup, horizon, and execution environment fixed. Set both Python and SUMO seeds. These seeds and their results belong to training/calibration, never final testing.

Test these changes independently, in both directions, with separate runs and long holds:

| Step | What it isolates |
| --- | --- |
| `(0.5, 600) -> (0.0, 600)` | Exploration response |
| `(0.0, 600) -> (0.0, 60)` | Rerouting response |
| `(0.5, 600) -> (0.0, 60)` | Combined response and interactions |
| No change under the same starting setting | Natural traffic evolution and monitoring noise |

First record recent mean trip duration under each held configuration. The average supplies a timescale, not a clean-cohort guarantee. Prefer recording individual departure and arrival times in the training-only worker in `run_challenge.py`, without adding source files or modifying the simulator. Also record unfinished-trip ages at the horizon. This allows duration-tail and exposure checks that the current HTTP snapshots and trip events cannot provide.

Choose a fixed action application target, provisionally tick 3,000 after an initial hold, and use confirmed application as time zero in the analysis. Send no second change during the response measurement. Follow outcomes for at least 5,000 ticks after application, extending the calibration horizon if the measured duration/response times require it. A single transition from a bad to a good setting cannot establish timing for every direction.

Plot aligned recent means, completion rates, complaints, and configuration confirmation delay in 100- or 200-tick bins. Use matched-seed no-change trajectories to expose natural trends. Common initial seeds help comparisons, but changed routing consumes randomness differently, so the two trajectories are not identical counterfactuals.

Let `D_scale` be the largest stable mean duration across calibration scenarios and `L_route` a conservative first-routing-response delay, initially the largest tested rerouting interval. Compare candidate settlements `S = round_up_100(L_route + k * D_scale)` for `k in {1, 2, 3}`. With the largest saved mean, 563.62 ticks, and `L_route = 600`, these are 1,200, 1,800, and 2,300 ticks. These are probes, not validated safe delays. If individual duration data are available, also probe around `L_route + D95` and check censoring.

Start with `W = round_up_100(D_scale)`, about 600 ticks with the saved data, and compare `2 * W`. Only then evaluate a shorter 200-tick measurement after the same supported settlement. Avoid separate runs when a long recorded response can support several window calculations. Reject candidates where later windows show a systematic continuing response larger than ordinary no-change variability. Use a provisional practical tolerance of 5% of the late-hold mean, then check whether noise makes that threshold meaningful across seeds and action directions.

When departure telemetry exists, report the fraction of measured completions whose trip began before `t_ack + L_route`. Use a provisional target of no more than 5% such completions, alongside stable outcomes across seeds; do not infer that threshold from the mean duration alone. A trip departing later can still inherit shared road-state effects, so the exposure check complements the step-response analysis.

Select the shortest interval supported consistently by the data. Check the chosen timing once on a fresh calibration seed. If no stable interval fits the decision budget, report that limitation; shortening settlement solely to get more training samples does not solve delayed attribution. Road-estimate history and the absence of departure cohorts remain limitations even after an apparently stable response.

Estimate input normalizer `H_scale` from baseline calibration trip events, `q_ref` from baseline completions per observed tick, and `q_floor` from the tenth percentile of the chosen-length baseline window completion rates for the fixed-750 challenge. `H_scale` only normalizes features; the reward compares each adjustment's measured `H_before` with `H_after`. Record the percentile convention, exact boundaries, and settings. Freeze these values before fitting the first learner.

## Experiment 2: check reward behavior

Before training, replay recorded windows through the proposed reward function:

1. Compare equal-length pre-adjustment and post-settlement windows. Verify the relative difference's sign and scale; report both means, the raw difference, relative difference, and throughput penalty separately.
2. Check that a no-change policy does not receive a systematic apparent improvement merely from comparing a startup window with a later window.
3. Inspect low-completion windows and explicit zero-completion fixtures. The learner must not be rewarded for hiding long trips beyond the measurement interval.
4. Compare the settled reward with whole-run mean, median, trip count, and p95. A higher reward does not automatically imply a better grading median.
5. Review settlement-period outcomes separately to catch actions with good late effects but severe transition costs.
6. Check unchanged good and poor configurations, worsening/recovery sequences, and natural population changes. A before/after reward is an observed difference, not proof that the adjustment caused it. Test a small outcome-level term only if the difference reward fails to sustain low overhead.

Do not add a complaint, computation, or switching penalty unless it addresses an observed failure or an explicit secondary objective. Record every reward version; changing normalization or reward coefficients requires recomputing compatible replay data or starting a new training run, rather than silently mixing incompatible data. Preserve before/after window boundaries so changes in reward can be audited.

## Experiment 3: train a small SAC policy

Start with the six-feature actor, twin critics, and bounds in the architecture. Use training simulation seeds from `100-199`, reserving validation seeds `201-205` and final test seeds `301-305`. The earlier `11-55` results and rule-based seed 11 have already influenced development; do not present them as unseen tests.

Use learner seeds `0, 1, 2` independently of simulator seeds. Shuffle the training simulation sequence reproducibly. After establishing a challenge-compatible policy at 750 cars, variation in workload or map is a separate generalization experiment. Different initial configurations can be included in training, with the official bad configuration retained for final evaluation.

For the future workload study, train and validate on declared rising, falling, and stable population trajectories, including situations with the same current count and opposite trends. Hold out complete demand schedules as well as seeds. The six-feature state is prepared for this, but adding a derivative to constant-population training does not teach demand response. Log actual and target populations, define how external population updates coordinate with complete configuration commands, and recalibrate settlement and population-dependent throughput safeguards. Compare against no-routing-change runs with the same demand schedules; the local reward can otherwise attribute an externally caused improvement to the routing action.

### Sample budget

With the duration-informed pilot `S = 1800`, `W = 600`, tick-200 warmup, and an initial `S + W` acquisition window, the first actor decision occurs around tick 2,600. Only about three complete 2,400-tick transitions then fit before tick 10,000, before application/polling delays. The earlier `S = W = 600` sketch would have yielded about seven transitions, but its settlement was not justified by trip exposure. Ten thousand simulator ticks are not ten thousand RL transitions.

| Stage | Adaptation transitions | Approximate 10,000-tick episodes at three transitions each | Purpose |
| --- | ---: | ---: | --- |
| Integration pilot | 100 | 34 | Check data collection and learning diagnostics; below the proposed learning start |
| Initial learning study | 2,000 | 667 | Test whether validation performance trends improve |
| Larger study if warranted | 10,000 | 3,334 | Assess convergence and seed sensitivity |

These budgets are planning examples, not convergence claims or authorization for automatic long runs. Measure transitions per episode and wall time in the pilot, then report the projected cost for all learner seeds. Checkpoint the optimizer, replay buffer, entropy state, RNG states, normalization constants, and training progress so runs can resume accurately. Replaying data does not create new evidence.

Longer continuous training episodes can amortize startup/acquisition and provide more complete holds; keep the final challenge evaluation at 10,000 ticks. Prefer that to shortening settlement below the measured exposure timescale. If this feedback rate makes SAC impractical, report the cost and compare with fixed-configuration optimization before changing the learning objective.

Train with sampled actions. At validation checkpoints, freeze updates and use deterministic actions on the same reserved validation scenarios. Choose the checkpoint using a declared validation criterion, primarily whole-run mean overhead with median and throughput guardrails. Keep final test seeds out of normalization, checkpoint selection, and hyperparameter tuning. [Evaluation guidance](https://stable-baselines3.readthedocs.io/en/master/guide/rl_tips.html)

If the validation policy only learns a nearly constant setting, compare it directly with a tuned fixed configuration. A small two-knob problem may not provide enough changing conditions for a state-dependent policy to help. That is an experimental result, not evidence that the learner needs a larger observation vector.

## Experiment 4: controlled ablations

Change one choice at a time and compare matched validation scenarios:

| Comparison | Question |
| --- | --- |
| Six features vs five without population trend | Does the derivative help on rising/falling workload trajectories? |
| Six features vs five without population level | Is current count useful in addition to its trend? |
| Six features vs seven with one overhead lag | Does short performance history address residual delay? |
| Actor `[32, 32]` vs `[64, 64]` | Is model capacity limiting validation performance? |
| Before/after relative difference alone vs difference plus throughput safeguard | Does the safeguard improve completion behavior without overwhelming the adaptation signal? |
| Difference reward vs difference plus a small post-action outcome term | Does a level term improve sustained performance? |
| Duration-scale `W` vs `2 * W`, then `W = 200` only after supported settlement | Does a shorter late measurement preserve representative outcomes and useful precision? |

A change in `S` or `W` changes the environment and the meaning of discounted returns. Use separate runs and versioned replay; keep `gamma` documented per decision and compare the same physical horizon.

## Experiment 5: frozen policy and three-file audit

Compare the exported deterministic SAC actor against the static bad baseline, fixed `(0.0, 60)` configuration, and current reactive/rule-based references. Give every policy the same initial bad configuration, exact horizon, workload, and measurement rules. Include the acquisition window in whole-run scoring; do not grant SAC a favorable scoring start.

The existing `tools/evaluate.py` currently accepts only `rule-based`, `fixed`, and `reactive`. It can assess those references today, but a documented `--strategy sac` command would be premature. Under the strict three-source-file design, SAC evaluation orchestration belongs in `run_challenge.py`; reuse the evaluator's isolation and event-recording approach without adding a required `tools/` import.

Report per seed and per learner seed:

- Whole-run median of individual trip overheads through tick 10,000, the challenge measure.
- Whole-run mean and p95 overhead, trip count, complaint rate, and configuration changes.
- Paired percentage change relative to each reference, including every failed comparison.
- Action/application delays, actual measurement lengths, incomplete or invalid transitions, and fallback events.
- Learning cost in simulator ticks, adaptation transitions, wall time, and gradient steps.

The local challenge criterion is a strictly lower median than the bad baseline for every required paired seed. Also show performance relative to the stronger fixed/reactive references. Five pairs do not establish broad statistical certainty; show all pairs and variation across learner seeds. A final-test-driven redesign needs new untouched test seeds.

Export the selected mean actor and verify continuous-action parity against framework inference on at least 1,000 representative/boundary states, using maximum absolute action error `<= 1e-5` as the initial tolerance. Check physical actions separately: rerouting should match after rounding except at an explicitly detected half-integer threshold, where a one-tick difference must be documented and reviewed. Also compare feature normalization and output mapping exactly.

Run a clean-overlay audit using only the three submitted files plus original UPISAS dependencies, without SB3, Gymnasium, PyTorch, NumPy, or external model files. Check imports, actor loading, a real monitoring snapshot, full command submission, confirmation, and horizon exit. The final main-tree file list must still contain exactly the three required paths.

## Record format

Emit one structured decision record with:

```text
episode_id, simulator_seed, learner_seed, model_id, reward_version,
state, feature_version, requested_normalized_action, requested_configuration,
decision_step, accepted_step, confirmed_step,
before_start_step, before_end_step, before_completed_trips,
settlement_deadline, observation_start_step, observation_end_step,
start_trip_count, end_trip_count, completed_trips,
overhead_before, overhead_after, mean_trip_duration, completion_rate,
active_cars_start, active_cars_end, population_slope, population_trend,
target_car_count, workload_schedule_id,
raw_overhead_difference, relative_overhead_difference,
reward_throughput_penalty, reward, reward_source,
low_confidence, next_state, terminated, truncated,
retry_count, invalid_reason
```

Use `null` for undefined measured overhead, including no-completion windows. Keep any penalty surrogate in a separate field or explicit reward source. Save all nine configuration values, feature order/scales, timing and action bounds, source hashes, runtime/package versions, images, and seed schedules in episode/batch metadata. If calibration telemetry is enabled, separately save departure/arrival ticks, individual durations, unfinished-trip ages, and the pre-action departure fraction in each measurement window. Exact trip events remain necessary for the grading median; aggregate API snapshots cannot reconstruct it.

## Next implementation order

1. Add the window/phase controller and scripted-action mode to the existing three files.
2. Run measurement verification and step-response calibration; freeze the first timing and reward version.
3. Add the optional training adapter and local seeded lifecycle; collect the integration pilot.
4. Train, validate, and compare with fixed/reactive references before increasing complexity.
5. Export the actor, audit the clean three-file overlay, and perform untouched-seed evaluation.
