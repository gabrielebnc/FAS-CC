# Local rule-based experiment (uncommitted)

This is an opt-in test, not the standard policy. The normal runner and evaluator
still default to `reactive`. The standard README has not been updated.

One adaptive run and a separate static comparison each stop at exactly 10,000
simulation ticks. Both use Python/SUMO seed 11, the same frozen CrowdNav image,
750 cars, the same map and all other controls. The static configuration keeps
exploration at 0.5 and rerouting at 600 ticks throughout.

The experimental rule controller starts at exploration 0 / rerouting 60 and
reviews every 200 ticks. Recent mean overhead >= 2.5 selects 0 / 30; <= 2.0
selects 0.05 / 90; otherwise it selects 0 / 60. A window with fewer than ten
completed trips accumulates more evidence. The recent sum is recovered from
CrowdNav's published average using `(total_trips + 1) * average`.

The controller runs locally in Python. CrowdNav/SUMO, Kafka and the HTTP API
run in isolated Docker containers. Monitoring flows SUMO -> Kafka -> API ->
controller; configuration commands flow controller -> API -> Kafka -> SUMO.
The controller polls every 0.2 seconds, reviews at the first observed tick after
a scheduled boundary, sends all nine knobs when the rule changes settings,
and waits for monitoring to confirm application. Commands are applied by
CrowdNav's normal ten-tick checks. Settings are not changed at exact wall-clock
intervals. The per-review CSV includes actual and scheduled ticks.

Reproduce from the UPISAS directory:

```sh
.venv/bin/python tools/evaluate.py --seeds 11 --strategy rule-based \
  --crowdnav-image crowdnav-fas2024-crowdnav:latest \
  --api-image fas-evaluation/api:source --output experiments/another-rules-test
```

Attach the experimental controller to an already-running CrowdNav API:

```sh
.venv/bin/python run_challenge.py --strategy rule-based --steps 10000
```

The `overhead_by_tick.csv` and `overhead_200ticks.csv` files inside both run
folders show exact individual-trip means and medians, cumulative statistics,
trip counts and confirmed configuration. Blank overheads mean no completed
trips. The first 200-tick bin is empty because of CrowdNav's existing warmup.
These are retrospective exports from the append-only trip journals, which
are recorded live during the simulation.

The combined CSV labels each row with seed and policy. `decisions.csv` logs
the rule controller's reviews. `comparison.png` plot the outcomes and
actual applied settings. `metadata.json` and `runtime/` preserve the exact
sources used for this test, independent of later working-tree changes.

The frozen runtime was captured before restoring the normal default selection;
the test explicitly passes `--strategy rule-based`. Its selected controller
and polling behavior match the current opt-in experimental mode.

A single seed pair gives preliminary evidence only; it does not establish
that these hand-written thresholds generalize or beat a tuned fixed policy.
