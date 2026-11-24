"""Estimate trip carryover from existing logs, without running CrowdNav.

For each recorded routing change, shift a hypothetical 600-tick measurement
window along the original held configuration. A trip is marked as carryover
when its departure predates application confirmation. This is a departure-based
proxy because trips can be rerouted in progress and road observations persist.
"""

import csv
import json
import math
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
EXPERIMENTS = HERE.parent
WAITS = (300, 450, 600, 750, 900, 1050, 1200, 1500, 1800, 2100, 2400)
KNOBS = ("exploration_percentage", "re_route_every_ticks")


def read_lines(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def same_routing(a, b):
    return all(math.isclose(a[key], b[key], rel_tol=1e-9, abs_tol=1e-9) for key in KNOBS)


def append_windows(rows, group, folder, cutoffs):
    events = read_lines(folder / "trips.jsonl")
    ends = np.array([event["tick"] for event in events])
    starts = np.array([event["departure_tick"] for event in events])
    overheads = np.array([event["overhead"] for event in events])
    result = json.loads((folder / "result.json").read_text())
    changes = [b["step"] for a, b in zip(result["configs"], result["configs"][1:])
               if not same_routing(a["configuration"], b["configuration"])]
    for cutoff in cutoffs:
        next_change = min((tick for tick in changes if tick > cutoff), default=result["final_step"] + 1)
        for wait in WAITS:
            lo, hi = cutoff + wait, cutoff + wait + 600
            if hi > result["final_step"] or hi >= next_change:
                continue
            begin, end = ends.searchsorted([lo, hi], side="right")
            count = int(end - begin)
            old_mask = starts[begin:end] < cutoff
            old = int(np.sum(old_mask))
            rows.append(dict(group=group, run=str(folder.relative_to(EXPERIMENTS)),
                             cutoff_tick=cutoff, settlement_ticks=wait,
                             window_start=lo, window_end=hi, trips=count,
                             started_before_cutoff=old,
                             overhead_sum=float(overheads[begin:end].sum()),
                             carryover_overhead_sum=float(overheads[begin:end][old_mask].sum()),
                             carryover_fraction=old / count if count else None))


def main():
    rows = []
    for name in ("sac-200-native", "sac-200-alpha01-settle1200", "sac-600-low-exploration"):
        for folder in sorted((EXPERIMENTS / name / "episodes").iterdir()):
            records = read_lines(folder / "decisions.jsonl")
            cutoffs = [d["confirmed_step"] for d in records if d["event"] == "application_confirmed"
                       and not same_routing(d["old_configuration"], d["requested_configuration"])]
            append_windows(rows, name, folder, cutoffs)
    scenario = EXPERIMENTS / "scenario-sac400-vs-fixed"
    for folder in sorted(scenario.glob("*/SAC/simulation")):
        records = read_lines(folder / "decisions.jsonl")
        cutoffs = [d["confirmed_step"] for d in records if d["event"] == "application_confirmed"
                   and not same_routing(d["old_configuration"], d["requested_configuration"])]
        append_windows(rows, "scenario_actual_changes", folder, cutoffs)
    for folder in sorted(scenario.glob("*/static/simulation")):
        # No routing change occurs at these boundaries. The observed trip ages
        # estimate departure carryover if an action were introduced during a jam.
        append_windows(rows, "scenario_blocked_virtual_boundaries", folder, range(3000, 6001, 200))

    summary = []
    for group in sorted({row["group"] for row in rows}):
        for wait in WAITS:
            selected = [row for row in rows if row["group"] == group and row["settlement_ticks"] == wait]
            nonempty = [row for row in selected if row["trips"]]
            fractions = np.array([row["carryover_fraction"] for row in nonempty])
            total, old = sum(row["trips"] for row in selected), sum(row["started_before_cutoff"] for row in selected)
            overhead_sum = sum(row["overhead_sum"] for row in selected)
            old_overhead_sum = sum(row["carryover_overhead_sum"] for row in selected)
            summary.append(dict(group=group, settlement_ticks=wait, windows=len(selected),
                                trips=total, started_before_cutoff=old,
                                trip_weighted_carryover_percent=100 * old / total if total else None,
                                carryover_share_of_overhead_sum_percent=100 * old_overhead_sum / overhead_sum if overhead_sum else None,
                                p90_window_carryover_percent=float(100 * np.quantile(fractions, .9)) if len(fractions) else None,
                                windows_within_eight_percent=float(100 * np.mean(fractions <= .08)) if len(fractions) else None,
                                median_trips=float(np.median([row["trips"] for row in nonempty])) if nonempty else None))
    for filename, contents in (("windows.csv", rows), ("summary.csv", summary)):
        with (HERE / filename).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=contents[0].keys())
            writer.writeheader()
            writer.writerows(contents)
    (HERE / "results.json").write_text(json.dumps(dict(
        measurement_ticks=600,
        carryover="departure_tick < application confirmation; excludes no-op actions",
        virtual_boundaries="Static scenario only, every 200 ticks from 3000 to 6000. Overlapping windows share trips.",
        exclusions="Windows reaching a subsequent applied routing change or extending beyond the logged horizon",
        limitations="Retrospective on held original trajectories. Earlier actions would change trajectories. Departure timing does not identify every routing decision or lingering road-data effects.",
        summary=summary), indent=2, allow_nan=False) + "\n")
    for row in summary:
        if row["settlement_ticks"] in (600, 750, 900, 1050, 1200, 2400):
            print(json.dumps(row), flush=True)


if __name__ == "__main__":
    main()
