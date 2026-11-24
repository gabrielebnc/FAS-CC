"""Run the authorized 200-window attempt, audit it, and export its frozen actor."""

import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from training import export_actor, main as train
from training_runtime import write_json

CONFIG = ROOT / "SAC_SETTINGS_scenario_200.json"
OUTPUT = ROOT / "experiments/sac-200-scenario-trend"
STATUS = OUTPUT.with_name(OUTPUT.name + "-job.json")


def main():
    started = time.monotonic()
    status = dict(status="running", phase="training", pid=os.getpid(), settings=str(CONFIG), output=str(OUTPUT))
    write_json(STATUS, status)
    try:
        train(["train", "--config", str(CONFIG), "--output", str(OUTPUT)])
        result = json.loads((OUTPUT / "training_result.json").read_text())
        if result["transitions"] != 200 or result["gradient_updates"] != 672:
            raise RuntimeError("Training did not reach 200 windows and 672 updates")
        status.update(phase="exporting")
        write_json(STATUS, status)
        audits = [dict(episode=path.parent.name, **json.loads(path.read_text()))
                  for path in sorted((OUTPUT / "episodes").glob("*/window_audit.json"))]
        if sum(row["transitions"] for row in audits) != 200:
            raise RuntimeError("Episode audits do not cover every training transition")
        write_json(OUTPUT / "window-audits.json", audits)
        changed = [window for row in audits for window in row["carryover_windows"]
                   if window["routing_changed"] and window["completed_trips"]]
        total = sum(window["completed_trips"] for window in changed)
        old = sum(window["departed_before_confirmation"] for window in changed)
        write_json(OUTPUT / "carryover-summary.json", dict(
            changed_routing_windows=len(changed), measured_completions=total,
            departed_before_confirmation=old, fraction=old / total if total else None,
            windows_over_eight_percent=sum(window["fraction"] > .08 for window in changed),
            interpretation="Departure-based diagnostic of original reward windows. All measured trips contribute to the reward."))
        checkpoint = OUTPUT / "checkpoint-000200"
        submission = OUTPUT.with_name(OUTPUT.name + "-submission")
        parity = export_actor(checkpoint, submission)
        status.update(status="complete", phase="complete", elapsed_seconds=time.monotonic() - started,
                      checkpoint=str(checkpoint), submission=str(submission), export_parity=parity)
        write_json(STATUS, status)
        print(json.dumps(status), flush=True)
    except BaseException as error:
        status.update(status="failed", elapsed_seconds=time.monotonic() - started,
                      error=f"{type(error).__name__}: {error}")
        write_json(STATUS, status)
        raise


if __name__ == "__main__":
    main()
