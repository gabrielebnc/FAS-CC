"""Verify the three-file overlay, requirements and actor checkpoint in isolation."""

import argparse
import json
from pathlib import Path
import shutil
import subprocess
import sys

from training_runtime import DockerRun, ROOT, summarize
from verify_run import verify


BLOCK_TRAINING_IMPORTS = '''
import importlib.abc
import runpy
import sys
class NoTrainingDependencies(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split(".")[0] in {"numpy", "torch", "gymnasium", "stable_baselines3", "training", "training_runtime"}:
            raise ImportError("Deployment attempted training dependency: " + fullname)
sys.meta_path.insert(0, NoTrainingDependencies())
runpy.run_module("run_challenge", run_name="__main__")
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--submission", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--seed", type=int, default=102)
    parser.add_argument("--crowdnav-image", default="crowdnav-sac/simulation:local")
    parser.add_argument("--api-image", default="crowdnav-sac/api:local")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    runtime = args.output / "runtime"
    submission = ("UPISAS/exemplars/crowdnav.py", "UPISAS/strategies/my_strategy.py", "run_challenge.py")
    package = (*submission, "requirements.txt", "checkpoint.json")
    actual = sorted(str(p.relative_to(args.submission)) for p in args.submission.rglob("*")
                    if p.is_file() and "__pycache__" not in p.parts)
    if actual != sorted(package):
        raise ValueError("Submission must contain three Python files, requirements, and checkpoint")
    framework = ("UPISAS/__init__.py", "UPISAS/strategy.py", "UPISAS/knowledge.py", "UPISAS/exceptions.py",
                 "UPISAS/exemplars/__init__.py", "UPISAS/strategies/__init__.py")
    for name in (*framework, *package):
        destination = runtime / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile((args.submission if name in package else ROOT) / name, destination)
    run = DockerRun(args.output / "simulation", args.seed, 10_000, args.crowdnav_image, args.api_image)
    process = exemplar = None
    try:
        exemplar = run.start()
        with (args.output / "deployment.log").open("w") as stream:
            process = subprocess.Popen(
                [sys.executable, "-u", "-c", BLOCK_TRAINING_IMPORTS, "--endpoint", run.endpoint,
                 "--log", str((run.directory / "decisions.jsonl").resolve())],
                cwd=runtime, stdout=stream, stderr=subprocess.STDOUT,
            )
            run.wait_finished(timeout=900)
            status = process.wait(timeout=120)
            if status != 0:
                raise RuntimeError("Three-file deployment failed; inspect deployment.log")
        report = dict(summary=summarize(run.directory), window_audit=verify(run.directory),
                      training_dependencies="blocked during deployment", files=list(package))
        (args.output / "audit.json").write_text(json.dumps(report, indent=2))
        print(json.dumps(report["window_audit"], indent=2))
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        if exemplar is not None:
            exemplar.close()
        run.close()


if __name__ == "__main__":
    main()
