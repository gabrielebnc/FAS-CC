"""Aggregate independently checked runs with equal weight for each evaluation seed."""
from pathlib import Path
import csv
import json
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter
import numpy as np

HERE = Path(__file__).resolve().parent
SEEDS = (302, 303, 304)
POLICIES = {
    "static": ("Static", "#ba600d"),
    "rule-based": ("Rule-based", "#225fa5"),
    "original-sac": ("Original SAC", "#6854a1"),
    "new-sac": ("New SAC", "#168055"),
}
MEASURES = ("mean_overhead", "completed_trips", "median_overhead", "p95_overhead", "complaint_rate", "configuration_changes")
EDGES = np.arange(0, 10001, 200)

def read(path):
    return json.loads(path.read_text())

def save_rows(filename, rows):
    with (HERE / filename).open("w", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)

def plot(series):
    plt.rcParams.update({"font.size": 13, "axes.spines.top": False, "axes.spines.right": False})
    overhead, (recent, cumulative) = plt.subplots(2, 1, sharex=True, figsize=(10, 10), dpi=120, layout="constrained")
    trips, counts = plt.subplots(figsize=(10, 10), dpi=120, layout="constrained")
    for policy, (label, color) in POLICIES.items():
        data = np.array(series[policy])
        for i, ax in enumerate((recent, cumulative, counts)):
            values = data[:, i, :]
            valid = np.all(np.isfinite(values), axis=0)
            ax.plot(EDGES[1:][valid], values[:,valid].mean(axis=0), label=label, color=color, linewidth=2.3)
    for fig in (overhead, trips):
        fig.suptitle("CrowdNav policies - 10k ticks, 750 cars", fontsize=20)
    recent.set_title("Mean overhead per 200 ticks, averaged across three seeds", loc="left", fontsize=15)
    cumulative.set_title("Cumulative mean overhead", loc="left", fontsize=15)
    counts.set_title("Completed smart-car trips, averaged across three seeds", loc="left", fontsize=15)
    for ax in (recent, cumulative, counts):
        ax.grid(axis="y", alpha=.22)
        ax.set_xlim(0, 10000)
        ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    for ax in (recent, cumulative):
        ax.set_ylabel("Trip overhead")
    for ax in (counts, cumulative):
        ax.set_xlabel("Simulation tick")
    for ax in (recent, counts):
        ax.legend(ncol=2, loc="upper left", framealpha=.95)
    counts.set_ylabel("Cumulative completed trips")
    counts.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    overhead.savefig(HERE / "overhead_comparison.png")
    trips.savefig(HERE / "completed_trips_comparison.png")
    plt.close("all")

def main():
    runs, intervals = [], []
    series = {p: [] for p in POLICIES}
    shared = None
    for seed in SEEDS:
        for policy in POLICIES:
            directory = HERE / str(seed) / policy
            row = read(directory / "metrics.json")
            sim = directory / "simulation"
            summary = read(sim / "result.json")
            meta = read(sim / "metadata.json")
            assert row["seed"] == meta["seed"] == summary["seed"] == seed
            assert row["ticks"] == meta["ticks"] == summary["final_step"] == 10000
            assert meta["initial"]["total_car_counter"] == 750
            conditions = (meta["initial"], meta["images"], summary["map"], summary["warmup_ticks"], summary["smart_car_percentage"])
            if shared is None:
                shared = conditions
            assert conditions == shared
            for config in summary["configs"]:
                assert all(config["configuration"][key] == value for key, value in meta["initial"].items()
                           if key not in ("exploration_percentage", "re_route_every_ticks"))
            events = [json.loads(line) for line in (sim / "trips.jsonl").read_text().splitlines()]
            ticks = np.array([x["tick"] for x in events])
            hs = np.array([x["overhead"] for x in events])
            assert np.all((ticks > 200) & (ticks <= 10000))
            assert len(hs) == row["completed_trips"] == summary["trip_count"]
            assert np.isclose(hs.mean(), row["mean_overhead"])
            assert np.isclose(np.median(hs), row["median_overhead"])
            assert np.isclose(np.quantile(hs, .95), row["p95_overhead"])
            assert np.isclose(mean(e["complaint"] for e in events), row["complaint_rate"])
            runs.append(dict(seed=seed, policy=policy, **{k:row[k] for k in MEASURES}))
            n = np.histogram(ticks - 1, EDGES)[0]
            total = np.histogram(ticks - 1, EDGES, weights=hs)[0]
            by_bin = np.divide(total, n, out=np.full(len(n), np.nan), where=n>0)
            cum_n = n.cumsum()
            cum_h = np.divide(total.cumsum(), cum_n, out=np.full(len(n),np.nan), where=cum_n>0)
            assert cum_n[-1] == len(hs) and np.isclose(cum_h[-1], hs.mean())
            series[policy].append((by_bin, cum_h, cum_n))
            for i, end in enumerate(EDGES[1:]):
                intervals.append(dict(policy=policy, seed=seed, end_tick=end, trips=n[i], cumulative_trips=cum_n[i],
                                      mean_overhead=by_bin[i] if n[i] else None, cumulative_overhead=cum_h[i] if cum_n[i] else None))
    aggregate = []
    for p in POLICIES:
        rows = [r for r in runs if r["policy"] == p]
        aggregate.append(dict(policy=p, seeds=len(rows), **{k:mean(r[k] for r in rows) for k in MEASURES}))
    save_rows("per_seed.csv", runs)
    save_rows("results.csv", aggregate)
    save_rows("overhead_evolution.csv", intervals)
    plot(series)
    print(json.dumps(aggregate, indent=2))

if __name__ == "__main__":
    main()
