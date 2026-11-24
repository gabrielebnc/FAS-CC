"""Evaluate the new scenario-trained actor, previous SAC-400, and fixed routing."""

import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from statistics import mean
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))

import numpy as np

from compare_policies import SOURCES, run_policy
from training_runtime import write_json
from UPISAS.strategies.my_strategy import FrozenActor

SEEDS = (601, 602, 603)
POLICIES = {
    "static": ("Fixed 0% / 60", "#b75f13", None),
    "previous-sac-400": ("Previous SAC-400", "#607992", ROOT / "experiments/sac-600-low-exploration-submission-400"),
    "scenario-sac-200": ("Scenario SAC-200", "#118357", ROOT / "experiments/sac-200-scenario-trend-submission"),
}
INITIAL = json.loads((ROOT / "SAC_SETTINGS_scenario_200.json").read_text())["initial_configuration"]
MEASURES = ("mean_overhead", "median_overhead", "p95_overhead", "completed_trips", "complaint_rate", "teleports")
PHASES = (("initial", 201, 1999), ("more_cars", 2000, 2999),
          ("blocked", 3000, 6999), ("reopened", 7000, 10000))


def read(path):
    return json.loads(path.read_text())


def csv_file(name, rows):
    with (HERE / name).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def prepare():
    models = {}
    for policy, (_, _, source) in POLICIES.items():
        if source is None:
            continue
        bundle = HERE / "submissions" / policy
        if not bundle.exists():
            shutil.copytree(source, bundle)
            for item in SOURCES:
                shutil.copyfile(ROOT / item, bundle / item)
        checkpoint = source / "checkpoint.json"
        if checkpoint.read_bytes() != (bundle / "checkpoint.json").read_bytes():
            raise ValueError("Evaluation bundle has different actor weights")
        actor = FrozenActor.load(bundle / "checkpoint.json")
        models[policy] = dict(source=str(source), model_id=actor.data["model_id"],
                              checkpoint_sha256=hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
                              features=list(actor.features), settings=actor.data["settings"])
    for item in (*SOURCES, "compare_policies.py", "training_runtime.py", "simulation_worker.py", "verify_run.py",
                 "Crowdnav/crowdnav/app/simulation/Simulation.py", "Crowdnav/crowdnav/app/entity/CarRegistry.py",
                 "Crowdnav/api/app/routers/execute.py"):
        destination = HERE / "source" / item
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / item, destination)
    write_json(HERE / "settings.json", dict(seeds=SEEDS, ticks=10000, initial=INITIAL, models=models,
               scenario=read(ROOT / "SAC_SETTINGS_scenario_200.json")["scenario"],
               policy_timing="Each frozen actor uses its own checkpoint's settlement and measurement settings.",
               primary_metric="Equal-seed mean of whole-run individual-trip mean overhead",
               interpretation="Three matched fresh seeds, one stochastic execution per policy and seed. Completed smart-car trips only. The trials combine several training changes."))


def report():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import StrMethodFormatter

    rows = [read(HERE / str(seed) / policy / "metrics.json") for seed in SEEDS for policy in POLICIES]
    summary = [dict(policy=policy, seeds=len(SEEDS),
                    **{key: mean(row[key] for row in rows if row["policy"] == policy) for key in MEASURES})
               for policy in POLICIES]
    paired = []
    for seed in SEEDS:
        fixed = next(row for row in rows if row["seed"] == seed and row["policy"] == "static")
        for policy in POLICIES:
            if policy == "static":
                continue
            row = next(row for row in rows if row["seed"] == seed and row["policy"] == policy)
            paired.append(dict(seed=seed, policy=policy, mean_reduction_vs_fixed_percent=100 *
                               (fixed["mean_overhead"] - row["mean_overhead"]) / fixed["mean_overhead"],
                               extra_completed_trips=row["completed_trips"] - fixed["completed_trips"]))
    csv_file("per_seed.csv", rows)
    csv_file("results.csv", summary)
    csv_file("paired_comparison.csv", paired)
    write_json(HERE / "results.json", dict(summary=summary, paired=paired))

    plt.rcParams.update({"font.size": 15, "axes.titlesize": 17, "axes.labelsize": 16,
                         "legend.fontsize": 12, "axes.spines.top": False, "axes.spines.right": False})
    options = dict(figsize=(10, 10), dpi=120, layout="constrained")
    overhead, axes = plt.subplots(2, 1, sharex=True, **options)
    completed, counts = plt.subplots(**options)
    edges = np.arange(0, 10001, 200)
    evolution, phases, actions = [], [], []
    for policy, (label, color, source) in POLICIES.items():
        series = []
        for seed in SEEDS:
            directory = HERE / str(seed) / policy / "simulation"
            events = [json.loads(line) for line in (directory / "trips.jsonl").read_text().splitlines()]
            ticks = np.array([event["tick"] for event in events])
            values = np.array([event["overhead"] for event in events])
            metrics = read(directory.parent / "metrics.json")
            if len(values) != metrics["completed_trips"] or not np.isclose(values.mean(), metrics["mean_overhead"]):
                raise ValueError("Plot data disagrees with the recorded run metrics")
            n = np.histogram(ticks - 1, edges)[0]
            sums = np.histogram(ticks - 1, edges, weights=values)[0]
            cumulative_n = n.cumsum()
            recent = np.divide(sums, n, out=np.full(50, np.nan), where=n > 0)
            overall = np.divide(sums.cumsum(), cumulative_n, out=np.full(50, np.nan), where=cumulative_n > 0)
            series.append((recent, overall, cumulative_n))
            for i, tick in enumerate(edges[1:]):
                evolution.append(dict(seed=seed, policy=policy, end_tick=int(tick), interval_trips=int(n[i]),
                                      cumulative_trips=int(cumulative_n[i]),
                                      interval_mean=float(recent[i]) if n[i] else None,
                                      cumulative_mean=float(overall[i]) if cumulative_n[i] else None))
            for phase, start, end in PHASES:
                selected = values[(ticks >= start) & (ticks <= end)]
                phases.append(dict(seed=seed, policy=policy, phase=phase, completed_trips=len(selected),
                                   mean_overhead=float(selected.mean()) if len(selected) else None,
                                   median_overhead=float(np.median(selected)) if len(selected) else None))
            if source is not None:
                for record in map(json.loads, (directory / "decisions.jsonl").read_text().splitlines()):
                    if record["event"] == "application_confirmed":
                        config = record["requested_configuration"]
                        actions.append(dict(seed=seed, policy=policy, decision_tick=record["decision_step"],
                                            confirmed_tick=record["confirmed_step"],
                                            exploration=config["exploration_percentage"],
                                            reroute_ticks=config["re_route_every_ticks"],
                                            overhead_trend=record["state"][6] if len(record["state"]) == 7 else None))
        for i, axis in enumerate((*axes, counts)):
            values = np.asarray(series)[:, i, :]
            valid = np.all(np.isfinite(values), axis=0)
            axis.plot(edges[1:][valid], values[:, valid].mean(axis=0), label=label, color=color, linewidth=2.5)
    for axis in (*axes, counts):
        axis.axvline(2000, color="#777777", linestyle=":", linewidth=1.2)
        axis.axvspan(3000, 7000, color="#bd5757", alpha=.09)
        axis.grid(axis="y", alpha=.2)
        axis.set_xlim(0, 10000)
        axis.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    for figure in (overhead, completed):
        figure.suptitle("SAC vs Fixed - 10k ticks, 750 to 1,000 cars", fontsize=20)
    axes[0].set_title("Mean overhead per 200-tick interval", loc="left")
    axes[1].set_title("Cumulative mean overhead", loc="left")
    counts.set_title("Cumulative completed trips", loc="left")
    for axis in axes:
        axis.set_ylabel("Trip overhead")
    for axis in (axes[1], counts):
        axis.set_xlabel("Simulation tick")
    axes[0].legend(loc="upper left")
    counts.legend(loc="upper left")
    counts.set_ylabel("Completed smart-car trips")
    counts.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    overhead.savefig(HERE / "overhead_comparison.png")
    completed.savefig(HERE / "completed_trips_comparison.png")
    plt.close("all")
    csv_file("overhead_evolution.csv", evolution)
    csv_file("phase_results.csv", phases)
    csv_file("sac_actions.csv", actions)
    print(json.dumps(dict(summary=summary, paired=paired)), flush=True)


def main():
    status = dict(status="running", completed_runs=0, total_runs=9,
                  started_utc=datetime.now(timezone.utc).isoformat())
    try:
        prepare()
        for seed in SEEDS:
            for policy, (_, _, source) in POLICIES.items():
                target = HERE / str(seed) / policy
                if (target / "metrics.json").exists():
                    status["completed_runs"] += 1
                    continue
                status.update(current_seed=seed, current_policy=policy)
                write_json(HERE / "job_status.json", status)
                print(f"Starting {policy}, seed {seed}", flush=True)
                bundle = HERE / "submissions" / policy if source is not None else None
                row = run_policy(target, "SAC" if source is not None else "static", seed,
                                 bundle, None, initial=INITIAL, scenario=True)
                result = read(target / "simulation/result.json")
                events = result["scenario_events"]
                if [event["tick"] for event in events] != [2000, 3000, 7000]:
                    raise ValueError("Scenario events occurred at unexpected ticks")
                if set(events[1]["lane_speeds"].values()) != {0.0} or set(events[2]["lane_speeds"].values()) != {25.0}:
                    raise ValueError("The corridor was not blocked and restored correctly")
                if len(events[1]["lane_speeds"]) != 8 or row["ticks"] != 10000 or row["completed_trips"] == 0:
                    raise ValueError("Run failed its scenario or horizon checks")
                row.update(policy=policy, teleports=result["teleports"],
                           model_id=read(bundle / "checkpoint.json")["model_id"] if bundle else None)
                row["configuration_changes"] = sum(
                    any(a["configuration"][key] != b["configuration"][key]
                        for key in ("exploration_percentage", "re_route_every_ticks"))
                    for a, b in zip(result["configs"], result["configs"][1:]))
                write_json(target / "metrics.json", row)
                status["completed_runs"] += 1
                write_json(HERE / "job_status.json", status)
                print(f"Finished {policy}, seed {seed}: {json.dumps(row)}", flush=True)
        status.update(phase="reporting")
        write_json(HERE / "job_status.json", status)
        report()
        status.update(status="complete", phase="complete", finished_utc=datetime.now(timezone.utc).isoformat())
        write_json(HERE / "job_status.json", status)
    except BaseException as error:
        status.update(status="failed", error=f"{type(error).__name__}: {error}")
        write_json(HERE / "job_status.json", status)
        raise


if __name__ == "__main__":
    main()
