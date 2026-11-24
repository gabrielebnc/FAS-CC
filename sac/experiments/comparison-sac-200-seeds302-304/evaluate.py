"""Run four deployment policies on matched seeds, using one Docker stack at a time."""
import hashlib
import json
from pathlib import Path
import sys

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from compare_policies import SOURCES, run_policy
from training_runtime import write_json

POLICIES = {
    "new-sac": ("SAC", "sac-200-alpha01-settle1200"),
    "original-sac": ("SAC", "sac-200-native"),
    "rule-based": ("rule-based", None),
    "static": ("static", None),
}
if __name__ == "__main__":
    old_reference = ROOT / "experiments/comparison-sac-200-seed301/rule-based/runtime"
    for seed in (302, 303, 304):
        for label, (mode, experiment) in POLICIES.items():
            dest = HERE / str(seed) / label
            if (dest / "metrics.json").exists():
                continue
            submission = None
            if experiment:
                folder = ROOT / "experiments"
                submission = folder / (experiment + "-submission")
                weights = folder / experiment / "checkpoint-000200/model.zip"
                model_id = hashlib.sha256(weights.read_bytes()).hexdigest()
                assert json.loads((submission / "checkpoint.json").read_text())["model_id"] == model_id
            print(f"Starting {label} on seed {seed}", flush=True)
            row = run_policy(dest, mode, seed, submission, old_reference)
            assert row["ticks"] == 10_000 and row["completed_trips"] > 0
            if experiment:
                assert json.loads((dest / "window_audit.json").read_text())["transitions"] > 0
                row["model_id"] = model_id
            row["policy"] = label
            write_json(dest / "metrics.json", row)
            print(json.dumps(row), flush=True)
