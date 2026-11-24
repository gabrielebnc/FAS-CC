"""Select on development seeds, freeze the choice, then evaluate all held-out policies."""
import hashlib
import json
import subprocess
import sys
from pathlib import Path
from statistics import mean

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
sys.path.insert(0, str(ROOT))
from compare_policies import run_policy
from training_runtime import write_json

TRAIN = ROOT / "experiments/sac-600-low-exploration"
REFERENCE = ROOT / "experiments/comparison-sac-200-seed301/rule-based/runtime"
CONFIG = json.loads((TRAIN / "SAC_SETTINGS.json").read_text())
MILESTONES = (200, 400, 600)
DEV = (302, 303, 304)
TEST = (305, 306, 307)

def digest(file):
    return hashlib.sha256(file.read_bytes()).hexdigest()

def run(stage, seed, policy, milestone=None):
    target = HERE / stage / str(seed) / policy
    metrics = target / "metrics.json"
    if metrics.exists():
        return json.loads(metrics.read_text())
    mode = "SAC" if milestone else policy
    bundle = TRAIN.with_name(f"{TRAIN.name}-submission-{milestone}") if milestone else None
    if bundle:
        model_id = digest(TRAIN / f"checkpoint-{milestone:06d}/model.zip")
        actor = json.loads((bundle / "checkpoint.json").read_text())
        assert actor["model_id"] == model_id and actor["gradient_updates"] == milestone-32
    print(f"Starting {stage} seed {seed}, {policy}", flush=True)
    row = run_policy(target, mode, seed, bundle, REFERENCE, initial=CONFIG["initial_configuration"])
    assert row["ticks"] == 10000 and row["completed_trips"] > 0
    result = json.loads((target/"simulation/result.json").read_text())
    assert result["configs"][0]["configuration"] == CONFIG["initial_configuration"]
    for observed in result["configs"]:
        assert all(observed["configuration"][key] == value for key, value in CONFIG["initial_configuration"].items()
                   if key not in ("exploration_percentage","re_route_every_ticks"))
    if bundle:
        assert digest(bundle/"checkpoint.json") == digest(target/"runtime/checkpoint.json")
        assert json.loads((target/"window_audit.json").read_text())["transitions"] > 0
        row["model_id"] = model_id
    row.update(policy=policy, stage=stage)
    write_json(metrics, row)
    print(f'Finished {stage} {seed} {policy}: mean overhead {row["mean_overhead"]:.4f}, {row["completed_trips"]} trips', flush=True)
    return row

def main():
    scores = {k: [] for k in MILESTONES}
    for seed in DEV:
        for k in MILESTONES:
            scores[k].append(run("development", seed, f"sac-{k}", k)["mean_overhead"])
    averages = {k:mean(values) for k,values in scores.items()}
    best = min(MILESTONES, key=lambda k:(averages[k], k))
    write_json(HERE/"selection.json", dict(development_seeds=DEV, scored_by="lowest equal-seed mean overhead; ties prefer the earlier checkpoint", scores=averages, selected=best))
    print(f"Selected SAC-{best}; starting fresh seeds",flush=True)
    for seed in TEST:
        run("held-out",seed,f"sac-{best}",best)
        run("held-out",seed,"static")
        run("held-out",seed,"rule-based")
    subprocess.run([sys.executable, str(HERE/"report.py")],check=True)

if __name__ == "__main__":
    main()
