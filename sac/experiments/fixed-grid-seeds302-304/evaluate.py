"""Compare two exploration percentages and two rerouting intervals from startup."""
import csv
import sys
from itertools import product
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))
from training_runtime import DockerRun, INITIAL, summarize, write_json

SEEDS = (302, 303, 304)
LEVELS = list(product((0, 15), (60, 150)))
METRICS = ("mean_overhead", "completed_trips", "median_overhead", "p95_overhead", "complaint_rate")


def save_csv(name, rows):
    with (HERE / name).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=rows[0])
        writer.writeheader()
        writer.writerows(rows)


def evaluate(seed, percent, interval):
    directory = HERE / str(seed) / f"explore-{percent}-reroute-{interval}"
    initial = dict(INITIAL, exploration_percentage=percent / 100, re_route_every_ticks=interval)
    run = DockerRun(directory, seed, 10000, "crowdnav-sac/simulation:local", "crowdnav-sac/api:local", initial=initial)
    try:
        api = run.start()
        api.close()
        run.wait_finished(timeout=1200)
        result = summarize(directory)
        assert result["final_step"] == 10000 and result["seed"] == seed and result["trip_count"] > 0
        assert len(result["configs"]) == 1 and result["configs"][0]["configuration"] == initial
        return dict(seed=seed, exploration_percent=percent, reroute_ticks=interval,
                    completed_trips=result["trip_count"], **{k:result[k] for k in METRICS if k != "completed_trips"})
    finally:
        run.close()


def main():
    rows = []
    for seed in SEEDS:
        for p, r in LEVELS:
            print(f"Starting seed {seed}, exploration {p}%, rerouting {r} ticks", flush=True)
            rows.append(evaluate(seed, p, r))
            save_csv("per_seed.csv", rows)
            print(rows[-1], flush=True)

    grouped = {(p, r): [x for x in rows if (x["exploration_percent"], x["reroute_ticks"]) == (p, r)] for p, r in LEVELS}
    summary = [dict(exploration_percent=p, reroute_ticks=r, seeds=len(group),
                    **{k:mean(x[k] for x in group) for k in METRICS}) for (p, r), group in grouped.items()]
    save_csv("results.csv", summary)
    contrasts = [
        ("raise exploration to 15% at 60 ticks", (15, 60), (0, 60)),
        ("raise exploration to 15% at 150 ticks", (15, 150), (0, 150)),
        ("shorten interval to 60 ticks at 0%", (0, 60), (0, 150)),
        ("shorten interval to 60 ticks at 15%", (15, 60), (15, 150)),
    ]
    effects = []
    for label, changed, reference in contrasts:
        for x, y in zip(grouped[changed], grouped[reference], strict=True):
            assert x["seed"] == y["seed"]
            effects.append(dict(comparison=label, seed=x["seed"], overhead_difference=x["mean_overhead"]-y["mean_overhead"],
                                completed_trips_difference=x["completed_trips"]-y["completed_trips"]))
    save_csv("paired_effects.csv", effects)
    write_json(HERE / "completion.json", dict(verified_runs=len(rows), ticks_each=10000, cars=750, seeds=SEEDS))
    print("All 12 runs and paired comparisons finished.", flush=True)


if __name__ == "__main__":
    main()
