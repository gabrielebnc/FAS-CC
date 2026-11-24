# CrowdNav evaluation results

Completed pairs: 4/4. Horizon: 10,000 ticks per run.

| Seed | Baseline median | Adaptive median | Reduction | Baseline trips | Adaptive trips | Result |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| 22 | 2.499580 | 2.049104 | 18.02% | 2624 | 3599 | PASS |
| 33 | 2.467221 | 1.974490 | 19.97% | 2677 | 3670 | PASS |
| 44 | 2.452216 | 1.975454 | 19.44% | 2868 | 3597 | PASS |
| 55 | 2.579493 | 1.999313 | 22.49% | 2547 | 3117 | PASS |

Lower median overhead is better; ties fail. A positive reduction means improvement.

`runs.csv` includes mean, median, p90/p95, trip count, complaints, configuration changes,
and wall time (including startup). `pairs.csv` contains the paired comparison.

Scores use CrowdNav's existing smart-car trip events after its 200-tick warmup,
through tick 10,000 inclusive. Raw events, full configuration history and logs are
in each run's data directory. Strategies run in local Python; only backbones use Docker.

These results describe the tested seeds and policy snapshot; they do not guarantee
improvement on unseen seeds. Host load can affect a policy that polls in wall-clock time.
