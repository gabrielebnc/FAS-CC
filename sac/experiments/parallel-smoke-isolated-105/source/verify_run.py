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
    return dict(transitions=len(transitions), windows_checked=len(windows), maximum_mean_error=maximum)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    print(json.dumps(verify(parser.parse_args().directory), indent=2))
