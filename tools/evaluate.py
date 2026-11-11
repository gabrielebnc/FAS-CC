"""Compare baseline and submission in isolated, seeded 10,000-tick runs.

The strategy runs in the invoking Python environment. Docker Compose runs only
Kafka, the API and CrowdNav. Results and raw trip events go in experiments/.
"""

import argparse
import csv
from datetime import datetime, timezone
import hashlib
from importlib.metadata import version
import json
import math
import os
from pathlib import Path
import shutil
import statistics
import subprocess
import sys
import time
import uuid

# Also works when loaded as tools.evaluate by the tests.
try:
    from .evaluation_simulation import BASELINE, HORIZON
except ImportError:
    from evaluation_simulation import BASELINE, HORIZON

ROOT = Path(__file__).resolve().parents[1]
SUBMISSION = ("UPISAS/exemplars/crowdnav.py", "UPISAS/strategies/my_strategy.py", "run_challenge.py")
FRAMEWORK = ("UPISAS/__init__.py", "UPISAS/strategy.py", "UPISAS/knowledge.py", "UPISAS/exceptions.py")


def command(*args, capture=True):
    return subprocess.run(args, check=True, text=True, capture_output=capture).stdout


def summarize(directory):
    result = json.loads((directory / "result.json").read_text())
    events = [json.loads(line) for line in (directory / "trips.jsonl").read_text().splitlines()]
    if result["final_step"] != HORIZON:
        raise ValueError("Incomplete simulation")
    if not events or len(events) != result["trip_count"]:
        raise ValueError("Missing trip events")
    if any(not (0 < e["tick"] <= HORIZON) or not math.isfinite(e["overhead"])
           or e["overhead"] < 0 for e in events):
        raise ValueError("Invalid trip events")
    history = result["configs"]
    if not history or history[0]["configs"] != BASELINE:
        raise ValueError("Incorrect initial baseline")
    if any(c["configs"]["total_car_counter"] != 750 for c in history):
        raise ValueError("Workload changed")
    overheads = [e["overhead"] for e in events]
    percentiles = statistics.quantiles(overheads, n=100, method="inclusive") if len(overheads) > 1 else overheads * 99
    complaints = sum(e.get("complaint", 0) for e in events)
    compose_file = directory / "compose.json"
    elapsed = ((directory / "result.json").stat().st_mtime - compose_file.stat().st_mtime
               if compose_file.exists() else None)
    return {**result, "median_overhead": statistics.median(overheads),
            "mean_overhead": statistics.mean(overheads),
            "p90_overhead": percentiles[89], "p95_overhead": percentiles[94],
            "complaints": complaints, "complaint_rate": complaints / len(events),
            "configuration_changes": len(history) - 1, "wall_seconds_including_startup": elapsed}


def compare(seed, baseline, strategy):
    if baseline["seed"] != seed or strategy["seed"] != seed:
        raise ValueError("Seed mismatch")
    if any(c["configs"] != BASELINE for c in baseline["configs"]):
        raise ValueError("Baseline received an adaptation")
    for key in ("final_step", "map", "warmup_ticks", "smart_car_percentage"):
        if baseline[key] != strategy[key]:
            raise ValueError(f"Run settings differ: {key}")
    b, s = baseline["median_overhead"], strategy["median_overhead"]
    return {"seed": seed, "baseline_median": b, "strategy_median": s,
            "baseline_trips": baseline["trip_count"], "strategy_trips": strategy["trip_count"],
            "reduction_percent": (100 * (b - s) / b) if b else None,
            "passed": s < b}


def write_reports(output, batches):
    """Export auditable run statistics and paired scores, including partial batches."""
    output.mkdir(parents=True, exist_ok=True)
    runs, pairs, expected = [], [], set()
    provenance = None
    for batch in batches:
        metadata = json.loads((batch / "metadata.json").read_text())
        signature = {k: metadata[k] for k in ("images", "horizon", "source_sha256")}
        signature["simulation_sha256"] = hashlib.sha256((batch / "evaluation_simulation.py").read_bytes()).hexdigest()
        signature["python_version"] = metadata.get("python_version")
        signature["local_packages"] = metadata.get("local_packages")
        if provenance is not None and signature != provenance:
            raise ValueError("Cannot combine batches with different images, horizons or source snapshots")
        provenance = signature
        if expected.intersection(metadata["seeds"]):
            raise ValueError("Cannot combine batches with duplicate seeds")
        expected.update(metadata["seeds"])
        for seed in metadata["seeds"]:
            scores = {}
            for policy in ("baseline", "strategy"):
                directory = batch / f"{seed}-{policy}"
                if (directory / "result.json").exists():
                    scores[policy] = summarize(directory)
                    runs.append({"seed": seed, "policy": policy,
                                 **{k: scores[policy][k] for k in (
                                     "final_step", "trip_count", "median_overhead", "mean_overhead",
                                     "p90_overhead", "p95_overhead", "complaints", "complaint_rate",
                                     "configuration_changes", "wall_seconds_including_startup")},
                                 "data_directory": str(directory.resolve())})
            if len(scores) == 2:
                pairs.append(compare(seed, scores["baseline"], scores["strategy"]))
    runs.sort(key=lambda row: (row["seed"], row["policy"]))
    pairs.sort(key=lambda row: row["seed"])
    report = {"pairs": pairs, "runs": runs, "completed_pairs": len(pairs),
              "requested_pairs": len(expected), "requested_seeds": sorted(expected),
              "evaluation_complete": len(pairs) == len(expected),
              "all_tested_seeds_pass": bool(pairs) and all(p["passed"] for p in pairs),
              "batches": [str(b.resolve()) for b in batches], "provenance": provenance}
    (output / "comparison.json").write_text(json.dumps(report, indent=2))
    for filename, rows in (("runs.csv", runs), ("pairs.csv", pairs)):
        if rows:
            with (output / filename).open("w", newline="") as stream:
                writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
    lines = ["# CrowdNav evaluation results", "",
             f"Completed pairs: {len(pairs)}/{len(expected)}. Horizon: {HORIZON:,} ticks per run.", "",
             "| Seed | Baseline median | Adaptive median | Reduction | Baseline trips | Adaptive trips | Result |",
             "| --- | ---: | ---: | ---: | ---: | ---: | --- |"]
    for pair in pairs:
        reduction = pair["reduction_percent"]
        percentage = f"{reduction:.2f}%" if reduction is not None else "N/A"
        lines.append(f"| {pair['seed']} | {pair['baseline_median']:.6f} | {pair['strategy_median']:.6f} | "
                     f"{percentage} | {pair['baseline_trips']} | {pair['strategy_trips']} | "
                     f"{'PASS' if pair['passed'] else 'FAIL'} |")
    lines += ["", "Lower median overhead is better; ties fail. A positive reduction means improvement.", "",
              "`runs.csv` includes mean, median, p90/p95, trip count, complaints, configuration changes,",
              "and wall time (including startup). `pairs.csv` contains the paired comparison.", "",
              "Scores use CrowdNav's existing smart-car trip events after its 200-tick warmup,",
              "through tick 10,000 inclusive. Raw events, full configuration history and logs are",
              "in each run's data directory. Strategies run in local Python; only backbones use Docker.", "",
              "These results describe the tested seeds and policy snapshot; they do not guarantee",
              "improvement on unseen seeds. Host load can affect a policy that polls in wall-clock time."]
    (output / "report.md").write_text("\n".join(lines) + "\n")
    return report


def volume(source, target, read_only=True):
    return {"type": "bind", "source": str(source), "target": target, "read_only": read_only}


def local_image_tag(reference, role):
    """Compose needs a repository tag, not Docker's bare sha256 image ID."""
    image_id = command("docker", "image", "inspect", reference, "--format", "{{.Id}}").strip()
    digest = image_id.removeprefix("sha256:")
    if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        raise ValueError(f"Invalid local image ID: {image_id}")
    tag = f"fas-evaluation/{role}:{digest}"
    command("docker", "image", "tag", image_id, tag)
    return tag


def missing_docker_object(error):
    return any(message in (error.stderr or "").lower()
               for message in ("no such image", "no such object", "no such container"))


def find_backbone(explicit=None):
    if explicit is not None:
        candidates = [explicit.expanduser().resolve()]
    else:
        # Standalone checkout and the documented nested UPISAS/FAS-CC layout.
        candidates = [parent / "Crowdnav-FAS2024" for parent in ROOT.parents]
    return next((path for path in candidates
                 if (path / "crowdnav/Dockerfile").is_file()
                 and (path / "api/Dockerfile").is_file()), None)


def backbone_image(role, override=None, source=None):
    """Recover from container metadata pointing at a deleted or replaced image."""
    if override:
        try:
            return local_image_tag(override, role)
        except subprocess.CalledProcessError as exc:
            if not missing_docker_object(exc):
                raise
            raise RuntimeError(f"Explicit {role} image {override!r} is not installed locally. "
                               "Build it first or omit the image override.") from exc

    container = "crowdnav" if role == "crowdnav" else "http-server"
    candidates = []
    try:
        data = json.loads(command("docker", "inspect", container, "--format", "{{json .}}"))
        candidates = [data.get("Image"), data.get("Config", {}).get("Image")]
        project = (data.get("Config", {}).get("Labels") or {}).get("com.docker.compose.project")
        if project:
            candidates.extend((f"{project}-{container}:latest", f"{project}_{container}:latest"))
    except subprocess.CalledProcessError as exc:
        if not missing_docker_object(exc):
            raise

    for reference in dict.fromkeys(filter(None, candidates)):
        try:
            return local_image_tag(reference, role)
        except subprocess.CalledProcessError as exc:
            if not missing_docker_object(exc):
                raise

    if source is None:
        raise RuntimeError(f"No usable local {role} image. Provide the CrowdNav source with "
                           "--backbone /path/to/Crowdnav-FAS2024 to build it, or supply "
                           f"--{'crowdnav' if role == 'crowdnav' else 'api'}-image with an installed image tag.")
    context = source / ("crowdnav" if role == "crowdnav" else "api")
    tag = f"fas-evaluation/{role}:source"
    print(f"No usable {role} image; building from {context}", flush=True)
    command("docker", "build", "-t", tag, str(context), capture=False)
    return local_image_tag(tag, role)


def configuration(images, directory, seed):
    api_health = {"test": ["CMD", "python", "-c",
        'import urllib.request; urllib.request.urlopen("http://localhost:8080/",timeout=2)'],
        "interval": "2s", "timeout": "3s", "retries": 60}
    services = {
        "kafka": {"image": "spotify/kafka",
            "environment": {"ADVERTISED_HOST": "kafka", "ADVERTISED_PORT": "9092"},
            "healthcheck": {"test": ["CMD", "python", "-c",
                'import socket; socket.create_connection(("localhost",9092),2).close()'],
                "interval": "2s", "timeout": "3s", "retries": 90}},
        "http-server": {"image": images["api"], "pull_policy": "never",
            # Docker chooses a free host port for this isolated API.
            "ports": [{"target": 8080, "host_ip": "127.0.0.1", "protocol": "tcp"}],
            "depends_on": {"kafka": {"condition": "service_healthy"}},
            "entrypoint": ["python", "-c",
                'import aiokafka; assert aiokafka.__version__ == "0.12.0", '
                '"Pin aiokafka==0.12.0 and rebuild the API image"; '
                'import uvicorn; uvicorn.run("app.main:app",host="0.0.0.0",port=8080)'],
            "healthcheck": api_health},
        "crowdnav": {"image": images["crowdnav"], "pull_policy": "never", "init": True,
            "depends_on": {"http-server": {"condition": "service_healthy"}},
            "working_dir": "/app",
            "entrypoint": ["python", "/evaluation_simulation.py"],
            "environment": {"EVALUATION_SEED": str(seed), "PYTHONHASHSEED": str(seed),
                            "PYTHONUNBUFFERED": "1"},
            "volumes": [volume(directory.parent / "evaluation_simulation.py", "/evaluation_simulation.py"),
                        volume(directory, "/results", False),
                        volume(directory / "knobs.json", "/app/knobs.json")]},
    }
    # No strategy container, fixed container names, external networks or shared volumes.
    return {"services": services}


def snapshot_runtime(output):
    """Freeze the submission and its framework without optional development code."""
    runtime = output / "runtime"
    for name in (*FRAMEWORK, *SUBMISSION):
        destination = runtime / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, destination)
    for package in ("exemplars", "strategies"):
        (runtime / "UPISAS" / package / "__init__.py").touch()
    return runtime


def start_strategy(runtime, endpoint, log):
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment["CROWDNAV_ENDPOINT"] = endpoint
    return subprocess.Popen([sys.executable, "-u", "-m", "run_challenge"],
                            cwd=runtime, env=environment, stdout=log, stderr=subprocess.STDOUT)


def stop_strategy(process):
    if process is not None and process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def run_one(images, directory, seed, policy, timeout):
    directory.mkdir()
    knobs = {"explorationPercentage": 0.5, "reRouteEveryTicks": 600,
             "routeRandomSigma": 0.2, "maxSpeedAndLengthFactor": 1,
             "averageEdgeDurationFactor": 1, "freshnessUpdateFactor": 10,
             "freshnessCutOffValue": 90}
    (directory / "knobs.json").write_text(json.dumps(knobs))
    compose_file = directory / "compose.json"
    compose_file.write_text(json.dumps(configuration(images, directory, seed), indent=2))
    compose = ["docker", "compose", "-p", "fas-eval-" + uuid.uuid4().hex[:10], "-f", str(compose_file)]
    deadline = time.monotonic() + timeout
    strategy = None
    strategy_log = None
    try:
        # Start the local strategy against the fresh API before advancing any ticks.
        command(*compose, "up", "-d", "--wait", "--wait-timeout", str(min(timeout, 240)),
                "kafka", "http-server", capture=False)
        if policy == "strategy":
            address = command(*compose, "port", "http-server", "8080").strip()
            port = int(address.rsplit(":", 1)[1])
            strategy_log = (directory / "strategy.log").open("w")
            strategy = start_strategy(directory.parent / "runtime",
                                      f"http://127.0.0.1:{port}", strategy_log)
        command(*compose, "up", "-d", "crowdnav", capture=False)
        while not (directory / "result.json").exists():
            progress = directory / "progress.json"
            step = json.loads(progress.read_text())["step"] if progress.exists() else 0
            if strategy is not None and strategy.poll() is not None:
                if strategy.returncode != 0 or step < HORIZON:
                    raise RuntimeError("Local strategy exited early; inspect strategy.log")
            states = command(*compose, "ps", "-a", "--format", "json")
            # Compose versions return either a JSON array or newline-delimited objects.
            rows = json.loads(states) if states.lstrip().startswith("[") else [
                json.loads(line) for line in states.splitlines() if line.strip()]
            for row in rows:
                service = row.get("Service")
                if row.get("State") == "exited":
                    if row.get("ExitCode", 0) != 0 or service == "crowdnav":
                        # The result file is atomically written before a successful exit.
                        if not (directory / "result.json").exists():
                            raise RuntimeError(f"{service} exited before a complete result; inspect logs")
            if time.monotonic() >= deadline:
                raise TimeoutError(f"{policy}, seed {seed} exceeded {timeout}s")
            if progress.exists():
                print(f"seed={seed} {policy}: tick {step}/{HORIZON}", flush=True)
            time.sleep(5)
        result = summarize(directory)
        if strategy is not None:
            try:
                status = strategy.wait(timeout=20)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Local strategy did not stop at the horizon; inspect strategy.log") from exc
            if status != 0:
                raise RuntimeError("Local strategy failed; inspect strategy.log")
        if policy == "strategy" and len(result["configs"]) < 2:
            raise RuntimeError("Strategy never adapted; inspect strategy logs")
        return result
    finally:
        stop_strategy(strategy)
        if strategy_log is not None:
            strategy_log.close()
        try:
            logs = command(*compose, "logs", "--no-color")
            (directory / "containers.log").write_text(logs)
        finally:
            command(*compose, "down", "-v", "--remove-orphans", capture=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--seeds", type=int, nargs="+", default=[11, 22, 33, 44, 55])
    parser.add_argument("--output", type=Path)
    parser.add_argument("--report-only", type=Path, nargs="+",
                        help="Export saved batch results without running Docker; may combine distinct seeds")
    parser.add_argument("--crowdnav-image", help="Use an installed local CrowdNav image")
    parser.add_argument("--api-image", help="Use an installed local API image")
    parser.add_argument("--backbone", type=Path,
                        help="CrowdNav source to rebuild missing images; auto-detects the sibling repository")
    parser.add_argument("--timeout", type=int, default=1800, help="Seconds per run, including startup")
    args = parser.parse_args()
    if args.report_only:
        batches = [path.resolve() for path in args.report_only]
        output = (args.output or batches[0]).resolve()
        write_reports(output, batches)
        print(f"Results exported to {output}")
        return 0
    if len(set(args.seeds)) != len(args.seeds) or any(s < 0 or s >= 2**31 for s in args.seeds):
        parser.error("Use distinct seeds between 0 and 2147483647")
    # Check the invoking environment, rather than requiring a strategy image.
    try:
        import requests
        import jsonschema
    except ImportError as exc:
        parser.error(f"Missing local dependency: {exc.name}; install requirements.txt in this Python environment")
    command("docker", "info")
    source = find_backbone(args.backbone)
    if args.backbone is not None and source is None:
        parser.error("--backbone must contain crowdnav/Dockerfile and api/Dockerfile")
    images = {"crowdnav": backbone_image("crowdnav", args.crowdnav_image, source),
              "api": backbone_image("api", args.api_image, source)}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    output = (args.output or ROOT / "experiments" / ("paired-" + stamp)).resolve()
    output.mkdir(parents=True, exist_ok=False)
    metadata = {"images": images, "seeds": args.seeds, "horizon": HORIZON,
        "python_executable": sys.executable, "python_version": sys.version,
        "local_packages": {"requests": requests.__version__, "jsonschema": version("jsonschema")}}
    runtime = snapshot_runtime(output)
    metadata["source_sha256"] = {p: hashlib.sha256((runtime / p).read_bytes()).hexdigest()
                                for p in (*FRAMEWORK, *SUBMISSION)}
    shutil.copyfile(ROOT / "tools/evaluation_simulation.py", output / "evaluation_simulation.py")
    (output / "metadata.json").write_text(json.dumps(metadata, indent=2))
    pairs = []
    for index, seed in enumerate(args.seeds):
        results = {}
        # Alternate order to reduce consistent order effects from host load.
        order = ("baseline", "strategy") if index % 2 == 0 else ("strategy", "baseline")
        for policy in order:
            print(f"Starting seed={seed} {policy}", flush=True)
            results[policy] = run_one(images, output / f"{seed}-{policy}", seed, policy, args.timeout)
        pair = compare(seed, results["baseline"], results["strategy"])
        pairs.append(pair)
        report = {"pairs": pairs, "all_tested_seeds_pass": all(p["passed"] for p in pairs),
                  "completed_pairs": len(pairs), "requested_pairs": len(args.seeds)}
        (output / "comparison.json").write_text(json.dumps(report, indent=2))
        write_reports(output, [output])
        print(json.dumps(pair), flush=True)
    print(f"Results: {output}", flush=True)
    return 0 if all(p["passed"] for p in pairs) else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (subprocess.CalledProcessError, RuntimeError, TimeoutError, ValueError) as exc:
        print(f"Evaluation failed: {exc}", file=sys.stderr)
        if isinstance(exc, subprocess.CalledProcessError) and exc.stderr:
            print(exc.stderr, file=sys.stderr)
        sys.exit(2)
