# CrowdNav challenge: development and dashboard

This branch contains the CrowdNav strategy, the UPISAS runtime it needs, and a live dashboard. A fresh clone can build the runner directly; there is no need to clone the original UPISAS repository or copy its files into this checkout.

- **`development`**: shared workspace for the strategy, dashboard, tests, and container setup.
- **`main`**: the three-file submission. Keep dashboard/runtime files off this branch.

The runner connects to CrowdNav over HTTP. CrowdNav's API, Kafka, and SUMO simulation run in a separate repository and Compose stack.

## 1. Clone into this layout

Install Docker Desktop with Compose v2. Python 3.9+ is needed only if you want to run outside Docker; the runner image supplies Python 3.12.

From a parent directory of your choice:

```sh
mkdir fas-workspace
cd fas-workspace
git clone https://github.com/JeremyGDiamond/Crowdnav-FAS2024.git Crowdnav-FAS2024
git clone --branch development https://github.com/gabrielebnc/FAS-CC.git UPISAS
```

You should have:

```text
fas-workspace/
├── Crowdnav-FAS2024/   # Simulation, Kafka, HTTP API; its own Git repository
└── UPISAS/            # This repository, checked out on development
```

Keep these as sibling repositories. Do not nest this checkout inside another UPISAS checkout, replace `.git`, or copy the dashboard into CrowdNav. The sibling names make the commands below convenient; runtime communication uses Docker's network, not relative filesystem paths.

## 2. Prepare CrowdNav once

From `fas-workspace/Crowdnav-FAS2024`, edit `crowdnav/knobs.json` and set these values, leaving the rest unchanged:

```json
"explorationPercentage": 0.5,
"reRouteEveryTicks": 600
```

Build and start CrowdNav:

```sh
cd Crowdnav-FAS2024
docker compose up -d --build
```

The challenge's Kafka broker requires aiokafka 0.12.0. If your API requirements already pin that version, no runtime installation is needed. Otherwise apply the challenge fix:

```sh
docker exec http-server pip install 'aiokafka==0.12.0'
docker restart http-server
```

A runtime installation survives restarts, but must be reapplied when the API container is recreated or rebuilt. Pinning `aiokafka==0.12.0` in the CrowdNav API's requirements and rebuilding makes it persistent.

Wait for the API to start, then check monitoring:

```sh
curl -sS http://localhost:8080/monitor/
```

Expect JSON containing `car_stats` and `configs`; during startup it may return `null`. To create the command topic before CrowdNav subscribes, send the initial nine-knob command:

```sh
curl -sS -X PUT http://localhost:8080/execute/ \
  -H 'content-type: application/json' \
  -d '{"route_random_sigma":0.2,"exploration_percentage":0.5,"max_speed_and_length_factor":1,"average_edge_duration_factor":1,"freshness_update_factor":10,"freshness_cut_off_value":90,"re_route_every_ticks":600,"total_car_counter":750,"edge_average_influence":140}'
docker restart crowdnav
```

Confirm the command reply contains `"status":"success"`. After restarting, wait for monitoring to show a fresh, increasing simulation step. The API may briefly retain an old snapshot.

This topic preparation is needed after a fresh stack is created. Between experiments, `docker restart crowdnav` is generally enough; use Compose `stop`/`start` to retain containers and Kafka state instead of `down`/`up`.

## 3. Start the strategy and dashboard

From the CrowdNav folder:

```sh
cd ../UPISAS
docker compose up -d --build
```

If an older manually launched runner already owns the name `crawd-strat`, stop/remove that old container first, then rerun Compose. Avoid running two strategy processes against the same simulation.

Open **http://localhost:8090/dashboard**.

The runner waits for monitoring data, polls the strategy every three seconds, and independently samples dashboard metrics every three seconds. CrowdNav applies commands every ten simulation ticks. The initial policy changes exploration to `0.0` and rerouting to `60`, while keeping `750` cars and preserving the other knobs.

Useful checks:

```sh
docker compose ps
docker compose logs --tail 60 strategy-runner
curl -sS http://localhost:8090/api/metrics
curl -sS http://localhost:8080/monitor/
```

The runner joins the external network `fas-net`, created by CrowdNav. Its API address inside Docker is `http://http-server:8080`; local Python uses `http://localhost:8080`.

## 4. Read the dashboard

Each metric has a history chart and a difference chart. **Δ = current sample − previous sample**, without dividing by elapsed time. Sampling uses fixed scheduled deadlines, independently of strategy execution. Errors, missed slots, and simulation resets break the delta sequence; unchanged snapshots produce zero differences.

Use the time-window and metric selectors, hover over a chart for sample values, or export JSON. The HTTP data endpoint is `/api/metrics`. History retains 1,200 samples in memory (about one hour at three seconds); restarting the runner clears it.

The dashboard shows trip duration, trip overhead, completed trips, complaints, active cars, and processing timings. A strategy exception appears on the dashboard while sampling continues.

**The challenge scores median individual-trip overhead.** The dashboard's `total_trip_overhead_average` is a running average; taking a median of its snapshots does not reproduce the grading metric. Five paired-seed evaluations still need to collect individual trip events from `crowd-nav-trips` and compare their per-run medians through tick 10,000.

## 5. Edit and commit normally

Work from the `UPISAS` repository, on `development` or a feature branch based on it:

```sh
git pull --ff-only
git switch -c feature/my-policy
# Edit files, run checks, then:
git add UPISAS/strategies/my_strategy.py
git commit -m "Adjust CrowdNav adaptation policy"
git push -u origin feature/my-policy
```

Open a pull request targeting **`development`**. For small shared changes directly on `development`, use `git push origin development` after committing. Git operates on this checkout only; edits to the sibling CrowdNav repository must be committed there separately.

The tracked `.gitignore` excludes virtual environments, Python caches, results, and legacy files left over from the original local checkout. New strategy modules and development files can be added normally; no local Git allowlist or force-add is needed.

Main edit points:

| File | Purpose |
| --- | --- |
| `UPISAS/strategies/my_strategy.py` | Analyze observations, choose all nine knobs, execute adaptations |
| `UPISAS/exemplars/crowdnav.py` | API connection and startup readiness |
| `run_challenge.py` | Submission loop, exiting at the first observed step at or above 10,000 |
| `run_crawdnav.py` | Continuous development runner with dashboard |
| `UPISAS/dashboard.py` | Sampling, history, and dashboard HTTP routes |
| `UPISAS/dashboard.html` | Charts and dashboard UI |

Set `STRATEGY`, `ENDPOINT`, `SAMPLE_INTERVAL`, and `DASHBOARD_PORT` at the top of `run_crawdnav.py`. `ENDPOINT` reads `CROWDNAV_ENDPOINT` from the environment to support Docker and local execution. If changing the dashboard port, update `compose.yaml` too.

After changing code, rebuild/recreate the runner:

```sh
docker compose up -d --build
```

Restart CrowdNav separately when you need to begin from the bad initial configuration. The development runner continues indefinitely; the submission runner stops at the challenge horizon.

## 6. Run locally and test

For local execution, stop the Docker strategy runner first so only one policy controls CrowdNav:

```sh
docker compose stop strategy-runner
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest UPISAS.tests.upisas.test_dashboard -v
.venv/bin/python run_crawdnav.py
```

The local dashboard is also at http://localhost:8090/dashboard. Ctrl+C stops the local runner. To run the challenge entry point instead, restart CrowdNav, wait for fresh monitoring, then run `.venv/bin/python run_challenge.py`.

You can run the dashboard checks with Docker instead of installing local Python dependencies:

```sh
docker compose run --rm --no-deps --entrypoint python strategy-runner \
  -m unittest UPISAS.tests.upisas.test_dashboard -v
```

## 7. Keep the submission to three files

Do not merge the complete development branch into `main` for submission. To transfer a tested policy, use a separate clean worktree so development files stay available:

```sh
git worktree add ../FAS-CC-submission main
git -C ../FAS-CC-submission restore --source development -- \
  UPISAS/exemplars/crowdnav.py UPISAS/strategies/my_strategy.py run_challenge.py
git -C ../FAS-CC-submission add UPISAS/exemplars/crowdnav.py UPISAS/strategies/my_strategy.py run_challenge.py
git -C ../FAS-CC-submission diff --cached
# If there are intended changes:
git -C ../FAS-CC-submission commit -m "Update challenge strategy"
git -C ../FAS-CC-submission ls-tree -r --name-only HEAD
git -C ../FAS-CC-submission push origin main
```

The tree listing must contain only the three required paths. That submission is an overlay for the original UPISAS project; the development branch includes the runtime so collaborators can boot it directly.

## Troubleshooting and shutdown

- **Network `fas-net` missing:** start the CrowdNav Compose stack first.
- **Waiting for monitoring:** check the runner's printed URL/error, API logs, and `/monitor/`. `http-server` resolves inside Docker, while local Python needs localhost.
- **Commands accepted but values never change:** repeat command-topic preparation and restart CrowdNav; confirm all nine knobs were sent.
- **Execution errors:** verify the API's aiokafka version and inspect `docker logs http-server`.
- **Runner or port conflict:** stop the previous `crawd-strat` container/local runner; only one service can bind host port 8090.
- **Challenge script exits immediately:** monitoring may still show the previous run above tick 10,000. Wait for the restarted simulation's fresh snapshots.

Stop the runner with `docker compose stop` from `UPISAS/`. Stop CrowdNav with `docker compose stop` from its own folder. To start both again, start CrowdNav first, then the strategy runner.

## Upstream code

The included UPISAS runtime files originate from [S2-group/UPISAS](https://github.com/S2-group/UPISAS), upstream revision `63d7b6606fc04c447cfa9957339e07be5c053757`. The upstream Apache 2.0 license is retained in `LICENSE`.
