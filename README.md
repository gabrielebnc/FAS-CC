# CrowdNav challenge

The strategy and dashboard run locally in Python. CrowdNav, its HTTP API, and
Kafka run in the separate CrowdNav backbone repository. The evaluator starts
isolated backbone containers and runs the strategy as a local subprocess.

- `main` contains exactly the three assignment files.
- `development` adds the required framework runtime, optional dashboard,
  evaluator, integration helper, tests, and this guide.

The current submission is [commit 2b5de99](https://github.com/gabrielebnc/FAS-CC/commit/2b5de99).
Its trial-and-rollback policy passed the latest seed-11 test: median overhead
2.474374 baseline versus 2.012015 adaptive, an 18.69% reduction over 10,000 ticks.
The previous policy passed five seed pairs on 2026-11-11; those results do not
constitute a five-seed evaluation of this updated policy.
The initial correction sets exploration to `0.0` and rerouting to `60`; the
reactive policy waits for application, tests rerouting changes using recent trip
overhead, and rolls back changes that make the measured window worse. It sends
all nine knobs and preserves the 750-car workload.

## Set up the repositories

Install Python 3.9+, Git, and Docker Desktop with Compose v2. To integrate with
both original backbones, clone this repository inside the original UPISAS checkout:

```sh
mkdir fas-workspace
cd fas-workspace
git clone https://github.com/JeremyGDiamond/Crowdnav-FAS2024.git Crowdnav-FAS2024
git clone https://github.com/S2-group/UPISAS.git UPISAS
git -C UPISAS clone --branch development https://github.com/gabrielebnc/FAS-CC.git FAS-CC
cd UPISAS/FAS-CC
python3 tools/integrate.py
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

If both backbones already exist, start with the nested clone command. Layout:

```text
fas-workspace/
├── Crowdnav-FAS2024/          # Simulation, Kafka, API
└── UPISAS/                   # Original framework
    ├── UPISAS/               # Assignment paths link into FAS-CC
    ├── run_challenge.py      # Link into FAS-CC
    └── FAS-CC/               # This repository; edit and commit here
```

The helper links the three assignment files into the original framework,
backs up existing files in the backbone's `.fas-cc-backup/`, and installs
local Git guards that keep main limited to those three paths. Setup is
repeatable when the links already match. It refuses conflicting backups or
hooks. Already-tracked backbone paths may still appear modified there;
commit assignment changes only from the nested FAS-CC repository.

For a checkout elsewhere, pass the framework explicitly with
`python3 tools/integrate.py --upisas /path/to/original/UPISAS`. For a standalone
development checkout, `python3 tools/integrate.py --hooks-only` installs only
the guards. The tracked runtime supports local execution without those links.

## Prepare CrowdNav

In `Crowdnav-FAS2024/crowdnav/knobs.json`, set exploration to `0.5` and
rerouting to `600`, leaving the other settings unchanged. From that repository:

```sh
docker compose up -d --build
```

The old Kafka broker requires `aiokafka==0.12.0`. Prefer pinning it in
`api/requirements.txt` before building. Otherwise apply the assignment's fix:

```sh
docker exec http-server pip install 'aiokafka==0.12.0'
docker restart http-server
```

A runtime pip fix survives restarts but not container recreation. Check that
`curl -sS http://localhost:8080/monitor/` eventually returns `car_stats` and
`configs` rather than `null`.

Create the command topic before restarting the simulation:

```sh
curl -sS -X PUT http://localhost:8080/execute/ \
  -H 'content-type: application/json' \
  -d '{"route_random_sigma":0.2,"exploration_percentage":0.5,"max_speed_and_length_factor":1,"average_edge_duration_factor":1,"freshness_update_factor":10,"freshness_cut_off_value":90,"re_route_every_ticks":600,"total_car_counter":750,"edge_average_influence":140}'
docker restart crowdnav
```

The command must return `"status":"success"`. Repeat topic preparation after
a fresh stack is created. Between runs, restart only `crowdnav`; Compose
`stop`/`start` retains containers and Kafka state.

## Run the strategy and dashboard

From this development repository:

```sh
docker restart crowdnav
# Wait until /monitor/ shows the fresh run near zero and baseline values.
.venv/bin/python run_challenge.py
```

The strategy polls every second and stops at the first observed step
at or above 10,000. Monitoring can briefly retain the previous run's snapshot
after a restart. CrowdNav itself continues after the strategy stops. Ctrl+C
stops the local strategy; `docker stop crowdnav` stops the simulation.

Select a policy explicitly with `--strategy baseline`, `--strategy fixed`, or
`--strategy reactive` (the default). `--steps` sets an **absolute final simulation
tick**, defaulting to 10,000; it does not add another 10,000 ticks after attaching.
The printed observed-window mean is diagnostic, not the assignment's median.
Use the evaluator below for the full individual-trip comparison.

For the optional dashboard, use a second terminal in this repository:

```sh
.venv/bin/python run_dashboard.py
```

Open **http://localhost:8090/dashboard**. It samples independently every three
seconds, plots metric values and `current − previous` differences, and exposes
JSON at `/api/metrics`. Errors, missed samples, and simulation resets break the
difference sequence. History holds 1,200 samples in memory; restarting the
dashboard clears it. Ctrl+C stops it.

The dashboard is read-only and can observe a baseline without a strategy.
Its cumulative average overhead is not the assignment's individual-trip median.
Set dashboard timing and port constants in `run_dashboard.py`. Both entry points
read `CROWDNAV_ENDPOINT`, defaulting to `http://localhost:8080`.

Run only one strategy against a simulation. If an old container owns the API or
dashboard port, stop it before starting these local processes. This repository
has no strategy/dashboard Dockerfiles or Compose services.

To verify the submitted code with the original framework instead, install that
framework's requirements and run `.venv/bin/python -m run_challenge` from its
root. The linked assignment files contain no imports of development helpers.

## Compare baseline and adaptive runs

```sh
.venv/bin/python tools/evaluate.py --seeds 11 22 33 44 55
```

Start with `--seeds 11` for a smoke test. Each baseline/strategy pair starts with
the exact assignment knobs, 750 cars, and matched Python/SUMO seeds. Fresh Kafka,
API, and simulation containers isolate each run. The strategy runs locally with
the evaluator's Python interpreter. The baseline receives no adaptations. Each
simulation stops after completing tick 10,000.

The evaluator uses installed backbone images, falling back from stale container
IDs to their configured tags. If no usable image remains, it rebuilds from the
sibling `Crowdnav-FAS2024` source. Use `--backbone /path/to/Crowdnav-FAS2024` for a
different location, or select installed tags with `--crowdnav-image` and
`--api-image`. The API image itself must contain `aiokafka==0.12.0`; changes made
only inside an existing container do not carry over.

Existing development containers are left untouched. Stop unrelated simulations
to reduce host load: the strategy's one-second polling interval uses wall-clock
time. Each isolated API gets a free localhost port automatically. Evaluation
runs do not appear in a dashboard attached to the development API.

Results go under `experiments/paired-<timestamp>/`:

| File | Contents |
| --- | --- |
| `report.md` | Readable paired comparison |
| `pairs.csv` | Per-seed medians, percentage reductions, and pass/fail |
| `runs.csv` | Mean, median, p90/p95, trips, complaints, changes, and runtime |
| `comparison.json` | Results, completion status, and provenance |
| `metadata.json` | Images, source hashes, seeds, and Python environment |
| `<seed>-<policy>/trips.jsonl` | Individual trip events |
| `<seed>-<policy>/result.json` | Final counters and applied configuration history |
| `<seed>-<policy>/containers.log` | Backbone logs |
| `<seed>-strategy/strategy.log` | Local strategy output |

Scores use CrowdNav's normal smart-car trip events after its existing 200-tick
warmup, through tick 10,000 inclusive. Every paired adaptive median must be
strictly lower. Check that `completed_pairs` equals `requested_pairs`.
Exit status is `0` when all requested pairs pass, `1` for a failed comparison,
and `2` for an execution or validation error. Temporary containers are removed
after each run; source files are frozen before evaluation. The submitted code
does not import this instrumentation.

Regenerate exports without Docker, or combine batches with distinct seeds and
matching source/image/environment snapshots:

```sh
.venv/bin/python tools/evaluate.py --report-only experiments/paired-<timestamp>
.venv/bin/python tools/evaluate.py --report-only experiments/paired-<first> \
  experiments/paired-<second> --output experiments/combined-results
```

The updated policy has passed one seed pair; run all five before claiming the
assignment objective for this version. Use fresh seeds after tuning to assess
whether improvements generalize. Keep generated data local; `experiments/` is ignored.

## Edit, test, and commit

Edit and run Git from this repository, not from either backbone. Feature branches
and pull requests should target `development`. New Python strategy code lives in
`UPISAS/strategies/my_strategy.py`; the exemplar handles API readiness, and
`run_challenge.py` controls polling and the horizon. Restart Python after edits.

```sh
.venv/bin/python -m unittest UPISAS.tests.upisas.test_integration \
  UPISAS.tests.upisas.test_evaluation UPISAS.tests.upisas.test_dashboard \
  UPISAS.tests.upisas.test_submission -v
git add <changed-files>
git commit -m "Describe the change"
git push origin development
```

Keep main's tree limited to:

```text
UPISAS/exemplars/crowdnav.py
UPISAS/strategies/my_strategy.py
run_challenge.py
```

Never merge the complete development branch into main. Inspect `git worktree list`
and use an existing main worktree, or create one with `git worktree add
/path/to/submission main`. From that worktree, after committing the tested changes
on development:

```sh
git restore --source development -- UPISAS/exemplars/crowdnav.py \
  UPISAS/strategies/my_strategy.py run_challenge.py
git add UPISAS/exemplars/crowdnav.py UPISAS/strategies/my_strategy.py run_challenge.py
git diff --cached
git commit -m "Update challenge strategy"
git ls-tree -r --name-only HEAD
git push origin main
```

Setup installs main commit/push guards in Git metadata so they persist across
branches and worktrees. These are local safeguards, not server-side protection;
each developer must run setup. The three-file submission overlays the original
UPISAS project and requires its framework and dependencies.

## Upstream code

The included framework runtime is adapted from
[S2-group/UPISAS](https://github.com/S2-group/UPISAS), revision
`63d7b6606fc04c447cfa9957339e07be5c053757`. Its Apache 2.0 license is retained in
`LICENSE`.
