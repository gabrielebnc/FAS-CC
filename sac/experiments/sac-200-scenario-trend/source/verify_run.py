"""Audit settled-window arithmetic against exact trip events after a live run."""

import argparse
import json
import math
from pathlib import Path


def verify(directory):
    directory = Path(directory)
    trips = [json.loads(line) for line in (directory / "trips.jsonl").read_text().splitlines()]
    records = [json.loads(line) for line in (directory / "decisions.jsonl").read_text().splitlines()]
    transitions = [record for record in records if record["event"] == "transition"]
    by_window = {(record["after"]["start_step"], record["after"]["end_step"]): record
                 for record in transitions}
    carryover = []
    windows = []
    for record in records:
        if record["event"] == "initial_window":
            windows.append(record["window"])
        elif record["event"] == "transition":
            windows.append(record["after"])
            if record["after"]["start_step"] < record["settlement_deadline"]:
                raise ValueError("Reward window includes unsettled completions")
            if record["confirmed_step"] < record["decision_step"]:
                raise ValueError("Application confirmation precedes decision")
    maximum = 0
    for window in windows:
        events = [trip for trip in trips if window["start_step"] < trip["tick"] <= window["end_step"]]
        if len(events) != window["completed_trips"]:
            raise ValueError("Window trip count disagrees with raw events")
        if events:
            exact = sum(trip["overhead"] for trip in events) / len(events)
            maximum = max(maximum, abs(exact - window["overhead"]))
            if not math.isclose(exact, window["overhead"], rel_tol=1e-8, abs_tol=1e-8):
                raise ValueError("Window overhead disagrees with raw events")
        record = by_window.get((window["start_step"], window["end_step"]))
        if record is not None:
            old = sum(trip["departure_tick"] < record["confirmed_step"] for trip in events)
            changed = any(not math.isclose(record["old_configuration"][key], record["requested_configuration"][key])
                          for key in ("exploration_percentage", "re_route_every_ticks"))
            carryover.append(dict(decision_step=record["decision_step"], confirmed_step=record["confirmed_step"],
                                  window_start=window["start_step"], window_end=window["end_step"],
                                  routing_changed=changed, completed_trips=len(events),
                                  departed_before_confirmation=old,
                                  fraction=old / len(events) if events else None))
    changed = [window for window in carryover if window["routing_changed"] and window["completed_trips"]]
    total = sum(window["completed_trips"] for window in changed)
    old = sum(window["departed_before_confirmation"] for window in changed)
    return dict(transitions=len(transitions), windows_checked=len(windows), maximum_mean_error=maximum,
                carryover_windows=carryover,
                changed_routing_carryover_fraction=old / total if total else None,
                changed_routing_windows_over_eight_percent=sum(window["fraction"] > .08 for window in changed))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(verify(parser.parse_args().directory), indent=2))
