# CrowdNav challenge: development and dashboard

This branch contains the CrowdNav strategy, a tracked UPISAS runtime for the Docker images, and an optional dashboard. For integration with the two original backbones, nest this checkout inside the original UPISAS repository and run the one-time setup below. All development tooling is committed; the submission still consists of exactly three files.

- **`development`**: shared workspace for the strategy, dashboard, tests, and container setup.
- **`main`**: the three-file submission. Keep dashboard/runtime files off this branch.

The strategy runner and optional dashboard connect to CrowdNav independently over HTTP. The strategy image contains no dashboard code; the dashboard image contains no strategy code. CrowdNav's API, Kafka, and SUMO simulation run in a separate repository and Compose stack.

## 1. Clone inside the backbone checkout

Install Docker Desktop with Compose v2 and Python 3.9+. Keep the original two backbone repositories, then clone **this repository inside the original UPISAS checkout**:

```sh
mkdir fas-workspace
cd fas-workspace
git clone https://github.com/JeremyGDiamond/Crowdnav-FAS2024.git Crowdnav-FAS2024
git clone https://github.com/S2-group/UPISAS.git UPISAS
git -C UPISAS clone --branch development https://github.com/gabrielebnc/FAS-CC.git FAS-CC
cd UPISAS/FAS-CC
python3 tools/integrate.py
```

If both backbones already exist, only run the last three commands from their shared parent. Layout:

```text
fas-workspace/
├── Crowdnav-FAS2024/             # Original simulation / Kafka / API repository
└── UPISAS/                      # Original UPISAS framework repository
    ├── UPISAS/                  # Framework package; assignment files link below
    ├── run_challenge.py         # Link into FAS-CC
    └── FAS-CC/                  # THIS Git repository, on development
        ├── .git/
        ├── UPISAS/exemplars/crowdnav.py
        ├── UPISAS/strategies/my_strategy.py
        └── run_challenge.py
```

`tools/integrate.py` uses only Python's standard library and Git. It links the three canonical assignment files from `FAS-CC` into the backbone's expected paths. Existing files are preserved in the backbone's `.fas-cc-backup/`; rerunning setup is safe when links already match. It also installs main-branch commit/push guards and adds local excludes to the parent repository for the nested checkout and new links. If an assignment path was already tracked by the parent, Git will still show its replacement there; commit assignment changes only from the nested FAS-CC repository.

**Edit, commit, and push from `UPISAS/FAS-CC`, not the original UPISAS repository.** The links immediately reflect edits; there is no repeated copying or synchronization step. Backups and links are local setup artifacts, not runtime dependencies of the submitted code. If you move the checkout, rerun setup after resolving any old backups/links.

A clone can physically live inside the CrowdNav backbone instead, but not inside two directories at once. In that layout pass the original framework explicitly: `python3 tools/integrate.py --upisas ../../UPISAS`. Nesting inside the original UPISAS checkout is the documented default.

The development images remain self-contained: all Python runtime and dashboard files they use are tracked in this repository. The backbone UPISAS run uses its original framework plus the three linked assignment files; the submitted code never imports `tools/` or the dashboard.

## 2. Prepare CrowdNav once

From `fas-workspace/Crowdnav-FAS2024`, edit `crowdnav/knobs.json` and set these values, leaving the rest unchanged:

```json
"explorationPercentage": 0.5,
"reRouteEveryTicks": 600
```

Build and start CrowdNav:

```sh
cd ../../Crowdnav-FAS2024
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

## 3. Start the strategy, optionally with the dashboard

From the CrowdNav folder:

```sh
cd ../UPISAS/FAS-CC
docker compose up -d --build
```

This starts only `strategy-runner`. To also build and start the optional dashboard image:

```sh
docker compose --profile dashboard up -d --build
```

To observe CrowdNav without running a strategy (for example, for a baseline):

```sh
docker compose stop strategy-runner
docker compose --profile dashboard up -d --build dashboard
```

The dashboard never sends adaptations. To disable a dashboard already running, use `docker compose --profile dashboard stop dashboard`; omitting the profile on a later `up` does not stop an existing container.

If an older manually launched runner already owns the name `crawd-strat`, stop/remove that old container first, then rerun Compose. The previous combined container also owns port 8090, so migrate it before enabling the separate dashboard. Avoid running two strategy processes against the same simulation.

When the dashboard profile is enabled, open **http://localhost:8090/dashboard**.

The strategy waits for monitoring data and polls every three seconds. The optional dashboard samples independently every three seconds, even when the strategy is stopped. CrowdNav applies commands every ten simulation ticks. The initial policy changes exploration to `0.0` and rerouting to `60`, while keeping `750` cars and preserving the other knobs.

Useful checks:

```sh
docker compose ps
docker compose logs --tail 60 strategy-runner
docker compose --profile dashboard logs --tail 60 dashboard
curl -sS http://localhost:8090/api/metrics
curl -sS http://localhost:8080/monitor/
```

The runner joins the external network `fas-net`, created by CrowdNav. Its API address inside Docker is `http://http-server:8080`; local Python uses `http://localhost:8080`.

## 4. Read the dashboard

Each metric has a history chart and a difference chart. **Δ = current sample − previous sample**, without dividing by elapsed time. Sampling uses fixed scheduled deadlines, independently of strategy execution. Errors, missed slots, and simulation resets break the delta sequence; unchanged snapshots produce zero differences.

Use the time-window and metric selectors, hover over a chart for sample values, or export JSON. The HTTP data endpoint is `/api/metrics`. History retains 1,200 samples in memory (about one hour at three seconds); restarting the dashboard clears it. Restarting the strategy leaves dashboard history intact.

The dashboard shows trip duration, trip overhead, completed trips, complaints, active cars, and processing timings. Dashboard status describes the monitoring connection, not whether a strategy is running. Check strategy logs for policy errors; dashboard sampling continues independently.

**The challenge scores median individual-trip overhead.** The dashboard's `total_trip_overhead_average` is a running average; taking a median of its snapshots does not reproduce the grading metric. Five paired-seed evaluations still need to collect individual trip events from `crowd-nav-trips` and compare their per-run medians through tick 10,000.

## 5. Edit and commit normally

Work from the **nested `UPISAS/FAS-CC` repository**, on `development` or a feature branch based on it:

```sh
git pull --ff-only
git switch -c feature/my-policy
# Edit files, run checks, then:
git add UPISAS/strategies/my_strategy.py
git commit -m "Adjust CrowdNav adaptation policy"
git push -u origin feature/my-policy
```

Open a pull request targeting **`development`**. For small shared changes directly on `development`, use `git push origin development` after committing. Git operates on this checkout only; edits to the backbone repositories must be committed there separately. Confirm `git remote -v` points to `gabrielebnc/FAS-CC`, and use `git -C /path/to/UPISAS/FAS-CC ...` if your terminal is elsewhere.

The tracked `.gitignore` excludes virtual environments, Python caches, results, and legacy files left over from the original local checkout. New strategy modules and development files can be added normally; no local Git allowlist or force-add is needed.

Main edit points:

| File | Purpose |
| --- | --- |
| `UPISAS/strategies/my_strategy.py` | Analyze observations, choose all nine knobs, execute adaptations |
| `UPISAS/exemplars/crowdnav.py` | API connection and startup readiness |
| `run_challenge.py` | Submission loop, exiting at the first observed step at or above 10,000 |
| `run_crawdnav.py` | Continuous strategy-only development runner |
| `run_dashboard.py` | Optional read-only dashboard process |
| `UPISAS/dashboard.py` | Sampling, history, and dashboard HTTP routes |
| `UPISAS/dashboard.html` | Charts and dashboard UI |

Set `STRATEGY`, `ENDPOINT`, and `POLL_INTERVAL` in `run_crawdnav.py`. Set dashboard `ENDPOINT`, `SAMPLE_INTERVAL`, and `DASHBOARD_PORT` in `run_dashboard.py`. Both endpoints read `CROWDNAV_ENDPOINT` from the environment for Docker/local execution. If changing the dashboard port, update `compose.yaml` too.

After changing strategy code, rebuild/recreate the runner (use `docker compose --profile dashboard up -d --build` when also updating the dashboard):

```sh
docker compose up -d --build
```

Restart CrowdNav separately when you need to begin from the bad initial configuration. The development runner continues indefinitely; the submission runner stops at the challenge horizon.

## 6. Run locally and test

For local execution, stop the Docker strategy runner first so only one policy controls CrowdNav. From `UPISAS/FAS-CC`:

```sh
docker compose stop strategy-runner
cd ..
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m run_challenge
```

Running `-m run_challenge` from the original UPISAS directory uses the original framework and the linked submission code, stopping at tick 10,000. For a local dashboard, run `../.venv/bin/python run_dashboard.py` from `UPISAS/FAS-CC` in a second terminal, or leave the optional Docker dashboard running. Stop a Docker dashboard before starting a local one on the same port. Open http://localhost:8090/dashboard. Ctrl+C stops each local process independently. For continuous development instead, run `../.venv/bin/python run_crawdnav.py` from the nested repo. Run checks there with `../.venv/bin/python -m unittest UPISAS.tests.upisas.test_integration UPISAS.tests.upisas.test_dashboard -v`.

You can run the dashboard checks with Docker instead of installing local Python dependencies (nested integration/guard tests require host Git and run with the local command above):

```sh
docker compose run --rm --no-deps --entrypoint python -v "$PWD:/upisas:ro" strategy-runner \
  -m unittest UPISAS.tests.upisas.test_dashboard -v
```

## 7. Keep the submission to three files

Setup installs standalone Git hooks into this repository's Git metadata. They persist across branches and worktrees, even though main contains no helper files: a main commit or push is rejected unless its tree has exactly the three required paths. Run `python3 tools/integrate.py --hooks-only` to install the guards without linking backbones. They are local safeguards, not server-side branch protection; do not bypass them with `--no-verify`.

Do not merge the complete development branch into `main` for submission. To transfer a tested policy, use a separate clean worktree so development files stay available:

```sh
git worktree add ../../FAS-CC-submission main
git -C ../../FAS-CC-submission restore --source development -- \
  UPISAS/exemplars/crowdnav.py UPISAS/strategies/my_strategy.py run_challenge.py
git -C ../../FAS-CC-submission add UPISAS/exemplars/crowdnav.py UPISAS/strategies/my_strategy.py run_challenge.py
git -C ../../FAS-CC-submission diff --cached
# If there are intended changes:
git -C ../../FAS-CC-submission commit -m "Update challenge strategy"
git -C ../../FAS-CC-submission ls-tree -r --name-only HEAD
git -C ../../FAS-CC-submission push origin main
```

The tree listing must contain only the three required paths. That submission is an overlay for the original UPISAS project; the development branch includes the runtime so collaborators can boot it directly.

## Troubleshooting and shutdown

- **Network `fas-net` missing:** start the CrowdNav Compose stack first.
- **Waiting for monitoring:** check the runner's printed URL/error, API logs, and `/monitor/`. `http-server` resolves inside Docker, while local Python needs localhost.
- **Commands accepted but values never change:** repeat command-topic preparation and restart CrowdNav; confirm all nine knobs were sent.
- **Execution errors:** verify the API's aiokafka version and inspect `docker logs http-server`.
- **Runner or port conflict:** stop the previous `crawd-strat` container/local runner; only one service can bind host port 8090.
- **Challenge script exits immediately:** monitoring may still show the previous run above tick 10,000. Wait for the restarted simulation's fresh snapshots.

Stop the strategy with `docker compose stop strategy-runner` and the dashboard with `docker compose --profile dashboard stop dashboard` from `UPISAS/FAS-CC/`. Stop CrowdNav with `docker compose stop` from its own folder. To start both again, start CrowdNav first, then the strategy runner.

## Upstream code

The included UPISAS runtime files originate from [S2-group/UPISAS](https://github.com/S2-group/UPISAS), upstream revision `63d7b6606fc04c447cfa9957339e07be5c053757`. The upstream Apache 2.0 license is retained in `LICENSE`.
