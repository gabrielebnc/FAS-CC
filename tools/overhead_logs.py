"""Export overhead evolution from exact trip events for either policy."""

from bisect import insort
import csv
import json
from pathlib import Path
import statistics


def write_csv(path, rows):
    if not rows:
        return
    with Path(path).open("w", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def export_overheads(directory, horizon=10_000, interval=200):
    """Produce tick-by-tick and nonoverlapping interval CSVs, retaining empty ticks.

    A blank overhead is undefined (no completed trips), never a zero-overhead trip.
    Cumulative metrics weight each individual completed trip equally. Configurations
    come from confirmed simulator history, not controller intentions.
    """
    directory = Path(directory)
    result = json.loads((directory / "result.json").read_text())
    events = [json.loads(line) for line in (directory / "trips.jsonl").read_text().splitlines()]
    if horizon <= 0 or interval <= 0 or result["final_step"] != horizon:
        raise ValueError("Invalid horizon or incomplete run")
    if len(events) != result["trip_count"]:
        raise ValueError("Trip count mismatch")
    by_tick = {}
    for event in events:
        if not (1 <= event["tick"] <= horizon):
            raise ValueError("Trip outside horizon")
        by_tick.setdefault(event["tick"], []).append(event["overhead"])
    ordered, running_sum, tick_rows, intervals = [], 0.0, [], []
    window_values = []
    history = result["configs"]
    config_index = 0
    for tick in range(1, horizon + 1):
        values = by_tick.get(tick, [])
        for value in values:
            insort(ordered, value)
            running_sum += value
        window_values.extend(values)
        while config_index + 1 < len(history) and history[config_index + 1]["step"] <= tick:
            config_index += 1
        config = history[config_index]["configs"]
        cumulative_mean = running_sum / len(ordered) if ordered else None
        cumulative_median = statistics.median(ordered) if ordered else None
        tick_rows.append({
            "tick": tick, "trips_completed": len(values), "total_trips": len(ordered),
            "mean_overhead_at_tick": statistics.mean(values) if values else None,
            "median_overhead_at_tick": statistics.median(values) if values else None,
            "cumulative_mean_overhead": cumulative_mean, "cumulative_median_overhead": cumulative_median,
            "exploration_percentage": config["exploration_percentage"],
            "re_route_every_ticks": config["re_route_every_ticks"],
        })
        if tick % interval == 0 or tick == horizon:
            intervals.append({
                "start_tick": (tick - 1) // interval * interval + 1, "end_tick": tick,
                "trips_completed": len(window_values), "total_trips": len(ordered),
                "mean_overhead": statistics.mean(window_values) if window_values else None,
                "median_overhead": statistics.median(window_values) if window_values else None,
                "cumulative_mean_overhead": cumulative_mean, "cumulative_median_overhead": cumulative_median,
                "exploration_percentage_at_end": config["exploration_percentage"],
                "re_route_every_ticks_at_end": config["re_route_every_ticks"],
            })
            window_values = []
    write_csv(directory / "overhead_by_tick.csv", tick_rows)
    write_csv(directory / f"overhead_{interval}ticks.csv", intervals)
    return intervals
