"""Compare the existing frozen SAC-400 actor with fixed 0% / 60 under disruptions."""

import csv
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

SEEDS = (401, 402, 403)
ACTOR = ROOT / "experiments/sac-600-low-exploration-submission-400"
INITIAL = json.loads((ROOT / "SAC_SETTINGS_600_low_exploration.json").read_text())["initial_configuration"]
PHASES = (("initial", 201, 1999), ("more_cars", 2000, 2999),
          ("blocked", 3000, 6999), ("reopened", 7000, 10000))
MEASURES = ("mean_overhead", "median_overhead", "p95_overhead", "completed_trips", "complaint_rate", "teleports")


def read(path):
    return json.loads(path.read_text())


def csv_file(name, rows):
    with (HERE / name).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)


def report():
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import StrMethodFormatter

    rows = [read(HERE / str(seed) / policy / "metrics.json")
            for seed in SEEDS for policy in ("static", "SAC")]
    summary = [dict(policy=policy, seeds=len(SEEDS),
                    **{key: mean(row[key] for row in rows if row["policy"] == policy) for key in MEASURES})
               for policy in ("static", "SAC")]
    comparison = []
    for seed in SEEDS:
        fixed, sac = [next(row for row in rows if row["seed"] == seed and row["policy"] == p)
                      for p in ("static", "SAC")]
        comparison.append(dict(seed=seed, mean_overhead_reduction_percent=100 *
                               (fixed["mean_overhead"] - sac["mean_overhead"]) / fixed["mean_overhead"],
                               extra_completed_trips=sac["completed_trips"] - fixed["completed_trips"]))
    csv_file("per_seed.csv", rows)
    csv_file("results.csv", summary)
    csv_file("paired_comparison.csv", comparison)
    write_json(HERE / "results.json", dict(summary=summary, paired=comparison,
               interpretation="Three matched seeds, one stochastic execution per policy and seed. Completed smart-car trips only."))

    plt.rcParams.update({"font.size": 15, "axes.titlesize": 17, "axes.labelsize": 16,
                         "legend.fontsize": 13, "axes.spines.top": False, "axes.spines.right": False})
    options = dict(figsize=(10, 10), dpi=120, layout="constrained")
    overhead, axes = plt.subplots(2, 1, sharex=True, **options)
    completed, counts = plt.subplots(**options)
    edges = np.arange(0, 10001, 200)
    evolution, phase_rows, actions = [], [], []
    for policy, label, color in (("static", "Fixed 0% / 60", "#b75f13"), ("SAC", "SAC-400", "#118357")):
        series = []
        for seed in SEEDS:
            directory = HERE / str(seed) / policy / "simulation"
            events = [json.loads(line) for line in (directory / "trips.jsonl").read_text().splitlines()]
            ticks = np.array([e["tick"] for e in events])
            values = np.array([e["overhead"] for e in events])
            n = np.histogram(ticks - 1, edges)[0]
            sums = np.histogram(ticks - 1, edges, weights=values)[0]
            cumulative_n = n.cumsum()
            recent = np.divide(sums, n, out=np.full(50, np.nan), where=n > 0)
            overall = np.divide(sums.cumsum(), cumulative_n, out=np.full(50, np.nan), where=cumulative_n > 0)
            series.append((recent, overall, cumulative_n))
            for i, tick in enumerate(edges[1:]):
                evolution.append(dict(seed=seed, policy=policy, end_tick=int(tick),
                                      interval_trips=int(n[i]), cumulative_trips=int(cumulative_n[i]),
                                      interval_mean=float(recent[i]) if n[i] else None,
                                      cumulative_mean=float(overall[i]) if cumulative_n[i] else None))
            for name, start, end in PHASES:
                selected = values[(ticks >= start) & (ticks <= end)]
                phase_rows.append(dict(seed=seed, policy=policy, phase=name, first_tick=start, last_tick=end,
                                       completed_trips=len(selected),
                                       mean_overhead=float(selected.mean()) if len(selected) else None,
                                       median_overhead=float(np.median(selected)) if len(selected) else None))
            if policy == "SAC":
                records = [json.loads(line) for line in (directory / "decisions.jsonl").read_text().splitlines()]
                for record in records:
                    if record["event"] == "application_confirmed":
                        config = record["requested_configuration"]
                        actions.append(dict(seed=seed, decision_tick=record["decision_step"],
                                            confirmed_tick=record["confirmed_step"],
                                            exploration=config["exploration_percentage"],
                                            reroute_ticks=config["re_route_every_ticks"]))
        for i, axis in enumerate((*axes, counts)):
            values = np.asarray(series)[:, i, :]
            valid = np.all(np.isfinite(values), axis=0)
            axis.plot(edges[1:][valid], values[:, valid].mean(axis=0), label=label, color=color, linewidth=2.5)
    for axis in (*axes, counts):
        axis.axvline(2000, color="#777777", linestyle=":", linewidth=1.2)
        axis.axvspan(3000, 7000, color="#bd5757", alpha=0.09)
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
    csv_file("phase_results.csv", phase_rows)
    csv_file("sac_actions.csv", actions)
    print(json.dumps(dict(summary=summary, paired=comparison)), flush=True)


def main():
    bundle = HERE / "submission"
    if not bundle.exists():
        shutil.copytree(ACTOR, bundle)
        for item in SOURCES:
            shutil.copyfile(ROOT / item, bundle / item)
        # Record the simulator and controller implementation used for this test.
        for item in (*SOURCES, "simulation_worker.py", "training_runtime.py", "compare_policies.py",
                     "Crowdnav/crowdnav/app/simulation/Simulation.py", "Crowdnav/crowdnav/app/entity/CarRegistry.py",
                     "Crowdnav/api/app/routers/execute.py"):
            destination = HERE / "source" / item
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / item, destination)
        assert (bundle / "checkpoint.json").read_bytes() == (ACTOR / "checkpoint.json").read_bytes()
        write_json(HERE / "settings.json", dict(seeds=SEEDS, ticks=10000, initial=INITIAL,
                   checkpoint=str(ACTOR / "checkpoint.json"),
                   checkpoint_sha256=hashlib.sha256((bundle / "checkpoint.json").read_bytes()).hexdigest(),
                   actor_model_id=read(bundle / "checkpoint.json")["model_id"],
                   population_event=dict(tick=2000, target=1000),
                   corridor_edges=["2846#5", "-2846#5", "2846#6", "-2846#6"],
                   closure_tick=3000, reopening_tick=7000, blocked_lane_speed=0,
                   controller_adjustments="Accept external target population changes; send only the two routing knobs."))
    for seed in SEEDS:
        for policy in ("static", "SAC"):
            target = HERE / str(seed) / policy
            if (target / "metrics.json").exists():
                continue
            print(f"Starting {policy}, seed {seed}", flush=True)
            row = run_policy(target, policy, seed, bundle, None, initial=INITIAL, scenario=True)
            result = read(target / "simulation/result.json")
            assert [event["tick"] for event in result["scenario_events"]] == [2000, 3000, 7000]
            assert set(result["scenario_events"][1]["lane_speeds"].values()) == {0.0}
            assert set(result["scenario_events"][2]["lane_speeds"].values()) == {25.0}
            assert len(result["scenario_events"][1]["lane_speeds"]) == 8
            assert row["ticks"] == 10000 and row["completed_trips"] > 0
            row["teleports"] = result["teleports"]
            row["configuration_changes"] = sum(
                any(a["configuration"][key] != b["configuration"][key]
                    for key in ("exploration_percentage", "re_route_every_ticks"))
                for a, b in zip(result["configs"], result["configs"][1:]))
            write_json(target / "metrics.json", row)
            print(f"Finished {policy}, seed {seed}: {json.dumps(row)}", flush=True)
    report()


if __name__ == "__main__":
    main()
