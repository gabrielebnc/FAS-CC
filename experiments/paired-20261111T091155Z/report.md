# CrowdNav evaluation results

Completed pairs: 1/1. Horizon: 10,000 ticks per run.

| Seed | Baseline median | Adaptive median | Reduction | Baseline trips | Adaptive trips | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 11 | 2.553671 | 1.996334 | 21.82% | 2822 | 3816 | PASS |

Lower median overhead is better; ties fail. A positive reduction means improvement.

`runs.csv` includes mean, median, p90/p95, trip count, complaints, configuration changes,
and wall time (including startup). `pairs.csv` contains the paired comparison.

Scores use CrowdNav's existing smart-car trip events after its 200-tick warmup,
through tick 10,000 inclusive. Raw events, full configuration history and logs are
in each run's data directory. Strategies run in local Python; only backbones use Docker.

These results describe the tested seeds and policy snapshot; they do not guarantee
improvement on unseen seeds. Host load can affect a policy that polls in wall-clock time.
