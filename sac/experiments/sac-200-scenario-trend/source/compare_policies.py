"""Compare the frozen SAC actor, original static settings, and prior rule policy."""

import argparse
import csv
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import numpy as np

from training import export_actor
from training_runtime import DockerRun, ROOT, summarize, write_json
from verify_run import verify


FRAMEWORK = ("UPISAS/__init__.py", "UPISAS/strategy.py", "UPISAS/knowledge.py", "UPISAS/exceptions.py",
             "UPISAS/exemplars/__init__.py", "UPISAS/strategies/__init__.py")
SOURCES = ("UPISAS/exemplars/crowdnav.py", "UPISAS/strategies/my_strategy.py", "run_challenge.py")


def run_policy(output, name, seed, submission, reference, initial=None, scenario=False):
    output.mkdir(parents=True, exist_ok=False)
    runtime = output / "runtime"
    process = exemplar = None
    run = DockerRun(output / "simulation", seed, 10_000, "crowdnav-sac/simulation:local", "crowdnav-sac/api:local", initial=initial, scenario=scenario)
    started = time.monotonic()
    try:
        if name != "static":
            policy_source = reference if name == "rule-based" else submission
            for item in (*FRAMEWORK, *SOURCES, *(("requirements.txt", "checkpoint.json") if name == "SAC" else ())):
                destination = runtime / item
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile((ROOT if item in FRAMEWORK else policy_source) / item, destination)
            write_json(output / "controller_sources.json", {
                str(p.relative_to(runtime)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in runtime.rglob("*") if p.is_file()})
        exemplar = run.start()
        with (output / "controller.log").open("w") as log:
            if name != "static":
                environment = os.environ.copy()
                environment.pop("PYTHONPATH", None)
                environment["CROWDNAV_ENDPOINT"] = run.endpoint
                extra = (["--strategy", "rule-based"] if name == "rule-based" else
                         ["--log", str(run.directory / "decisions.jsonl")])
                process = subprocess.Popen([sys.executable, "-u", "-m", "run_challenge", *extra],
                    cwd=runtime, env=environment, stdout=log, stderr=subprocess.STDOUT)
            run.wait_finished(timeout=1200)
            if process is not None and process.wait(timeout=120) != 0:
                raise RuntimeError(f"{name} controller failed; inspect controller.log")
        result = summarize(run.directory)
        if name == "static" and len({json.dumps({key: value for key, value in c["configuration"].items()
                                                if key != "total_car_counter"}, sort_keys=True)
                                     for c in result["configs"]}) != 1:
            raise ValueError("Static policy received a configuration change")
        if name == "SAC":
            write_json(output / "window_audit.json", verify(run.directory))
        return dict(policy=name, seed=seed, ticks=result["final_step"], completed_trips=result["trip_count"],
                    **{key: result[key] for key in ("mean_overhead", "median_overhead", "p95_overhead", "complaint_rate")},
                    configuration_changes=len(result["configs"]) - 1, wall_seconds=time.monotonic() - started)
    finally:
        if process is not None and process.poll() is None:
            process.terminate()
            process.wait(timeout=15)
        if exemplar is not None:
            exemplar.close()
        run.close()


def plot_comparison(output, rows):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import StrMethodFormatter
    plt.rcParams.update({"font.size":14,"axes.labelsize":15,"axes.titlesize":16,
                         "xtick.labelsize":12,"ytick.labelsize":12,"legend.fontsize":13})
    colors = {"static":"#c77727", "rule-based":"#2466b3", "SAC":"#31965b"}
    labels = {"static": "Static", "rule-based": "Rule-based", "SAC": "SAC"}
    bins, evolution = np.arange(200,10_001,200), []
    fig, axes = plt.subplots(2,1,figsize=(10,10),dpi=120,sharex=True,layout="constrained")
    trips_fig, trips_ax = plt.subplots(figsize=(10,10),dpi=120,layout="constrained")
    for row in rows:
        name = row["policy"]
        events = [json.loads(line) for line in (output/name/"simulation/trips.jsonl").read_text().splitlines()]
        ticks=np.array([e["tick"] for e in events]); overhead=np.array([e["overhead"] for e in events])
        means=[]
        for end in bins:
            values=overhead[(ticks>end-200)&(ticks<=end)]
            mean=float(values.mean()) if len(values) else None
            means.append(np.nan if mean is None else mean)
            evolution.append(dict(policy=name,end_tick=int(end),mean_overhead=mean,
                                  completed_trips=len(values),cumulative_trips=int(np.sum(ticks<=end))))
        color=colors[name]
        axes[0].plot(bins,means,label=labels[name],color=color,linewidth=2)
        axes[1].plot(ticks,np.cumsum(overhead)/np.arange(1,len(overhead)+1),label=labels[name],color=color,linewidth=2)
        axes[1].annotate(f"{labels[name]}: {overhead.mean():.3f}", (ticks[-1], overhead.mean()),
                         xytext=(-10, 10), textcoords="offset points", ha="right",
                         color=color, fontsize=13, fontweight="bold")
        trips_ax.plot(ticks,np.arange(1,len(ticks)+1),label=f"{labels[name]}: {len(ticks):,}",color=color,linewidth=2)
    axes[0].set_title("Mean overhead per 200-tick interval",loc="left")
    axes[1].set_title("Cumulative mean overhead",loc="left")
    for figure in (fig, trips_fig):
        figure.text(0.5, 0.995, "SAC vs Static vs Rule-based - 10k ticks, 750 cars",
                    ha="center", va="top", fontsize=17)
        figure.get_layout_engine().set(rect=(0, 0, 1, 0.96))
    for axis in (*axes,trips_ax):
        axis.grid(axis="y",alpha=.2)
        axis.set_xlim(0,10_000)
        axis.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
        axis.spines[["top","right"]].set_visible(False)
    for axis in axes:
        axis.set_ylabel("Trip overhead (lower is better)")
    axes[0].legend(ncol=3,loc="upper right")
    axes[1].margins(y=0.12)
    axes[1].set_xlabel("CrowdNav simulation tick")
    trips_ax.set_xlabel("CrowdNav simulation tick")
    trips_ax.set_ylabel("Cumulative recorded completed trips")
    trips_ax.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    trips_ax.legend(loc="upper left")
    fig.savefig(output/"overhead_comparison.png")
    trips_fig.savefig(output/"completed_trips_comparison.png")
    plt.close("all")
    with (output/"overhead_evolution.csv").open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(evolution[0]));writer.writeheader();writer.writerows(evolution)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint",type=Path,required=True)
    parser.add_argument("--output",type=Path,required=True)
    parser.add_argument("--seed",type=int,default=301)
    parser.add_argument("--reference",type=Path,default=ROOT.parent/"UPISAS")
    args=parser.parse_args()
    args.output=args.output.resolve();args.output.mkdir(parents=True,exist_ok=False)
    submission = args.output.parent / (args.checkpoint.parent.name + "-submission")
    parity=export_actor(args.checkpoint,submission)
    rows=[]
    for name in ("static","rule-based","SAC"):
        print(f"Evaluating {name}, seed {args.seed}",flush=True)
        rows.append(run_policy(args.output/name,name,args.seed,submission,args.reference))
        write_json(args.output/"results.json",dict(runs=rows,export_parity=parity))
        print(json.dumps(rows[-1]),flush=True)
    for row in rows:
        row["mean_reduction_vs_static_percent"]=100*(rows[0]["mean_overhead"]-row["mean_overhead"])/rows[0]["mean_overhead"]
    report=dict(runs=rows,export_parity=parity,
                sac_beats_static_mean=rows[2]["mean_overhead"]<rows[0]["mean_overhead"],
                sac_beats_rules_mean=rows[2]["mean_overhead"]<rows[1]["mean_overhead"],
                interpretation="One fresh matched seed; preliminary comparison only")
    write_json(args.output/"results.json",report)
    with (args.output/"results.csv").open("w",newline="") as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]));writer.writeheader();writer.writerows(rows)
    plot_comparison(args.output,rows)


if __name__=="__main__":
    main()
