"""Plot an existing paired evaluation. Optional dependency: matplotlib."""

import argparse
import json
from pathlib import Path
import statistics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("batch", type=Path)
    parser.add_argument("--seed", type=int, help="Seed to visualize; defaults to the first completed pair")
    args = parser.parse_args()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    batch = args.batch.resolve()
    report = json.loads((batch / "comparison.json").read_text())
    if not report["pairs"]:
        parser.error("No completed pair to plot")
    seed = args.seed if args.seed is not None else report["pairs"][0]["seed"]
    if seed not in [pair["seed"] for pair in report["pairs"]]:
        parser.error("The selected seed does not have a completed pair")
    fig, axes = plt.subplots(2, 2, figsize=(12, 7.5), layout="constrained")
    colors = {"baseline": "#929ba7", "strategy": "#3266c8"}
    names = {"baseline": "Static (50% / 600)", "strategy": "Rule based (200-tick reviews)"}
    summaries = {}
    for policy in ("baseline", "strategy"):
        directory = batch / f"{seed}-{policy}"
        result = json.loads((directory / "result.json").read_text())
        events = [json.loads(line) for line in (directory / "trips.jsonl").read_text().splitlines()]
        bins = {}
        for event in events:
            endpoint = ((event["tick"] - 1) // 200 + 1) * 200
            bins.setdefault(endpoint, []).append(event["overhead"])
        ticks = sorted(bins)
        axes[0, 0].plot(ticks, [statistics.mean(bins[t]) for t in ticks],
                        color=colors[policy], label=names[policy], linewidth=1.7)
        history = result["configs"]
        config_ticks = [c["step"] for c in history] + [result["final_step"]]
        for ax, key, scale in ((axes[1, 0], "re_route_every_ticks", 1),
                               (axes[1, 1], "exploration_percentage", 100)):
            values = [c["configs"][key] * scale for c in history]
            ax.step(config_ticks, values + [values[-1]], where="post", color=colors[policy], label=names[policy])
        overheads = [event["overhead"] for event in events]
        summaries[policy] = (statistics.mean(overheads), statistics.median(overheads))
    for offset, policy in ((-.18, "baseline"), (.18, "strategy")):
        bars = axes[0, 1].bar([offset, 1 + offset], summaries[policy], width=.34,
                              color=colors[policy], label=names[policy])
        axes[0, 1].bar_label(bars, fmt="%.3f", padding=3)
    axes[0, 1].set_xticks([0, 1], ["Mean overhead", "Median overhead"])
    axes[0, 1].set_ylim(0, max(v for values in summaries.values() for v in values) * 1.25)
    axes[0, 0].set_title("Mean overhead of trips completed in each 200-tick bin")
    axes[0, 0].set_ylabel("Trip overhead (lower is better)")
    axes[0, 0].legend(frameon=False, fontsize=9)
    axes[0, 1].set_title("Full-run individual-trip scores")
    axes[1, 0].set_title("Confirmed rerouting interval")
    axes[1, 0].set_ylabel("Ticks between rerouting attempts")
    axes[1, 1].set_title("Confirmed exploration percentage")
    axes[1, 1].set_ylabel("Exploration (%)")
    for ax in (axes[0, 0], axes[1, 0], axes[1, 1]):
        ax.set_xlabel("CrowdNav simulation tick")
    for ax in axes.flat:
        ax.grid(axis="y", alpha=.18)
        ax.set_axisbelow(True)
    fig.suptitle(f"CrowdNav rule-based MVP · matched seed {seed} · 10,000 ticks per run\n"
                 "750 cars; one seed pair is preliminary evidence", fontsize=14)
    for extension in ("png",):
        fig.savefig(batch / f"comparison.{extension}", dpi=180)
    plt.close(fig)
    print(f"Plots saved in {batch}")


if __name__ == "__main__":
    main()
