"""Render checkpoint selection and held-out plots from the completed simulations."""
import csv
import json
from pathlib import Path
from statistics import mean

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter
import numpy as np

HERE = Path(__file__).resolve().parent
MEASURES = ("mean_overhead", "median_overhead", "p95_overhead", "completed_trips", "complaint_rate", "configuration_changes")
EDGES = np.arange(0, 10001, 200)

def read(path):
    return json.loads(path.read_text())

def csv_file(name, rows):
    with (HERE/name).open("w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=rows[0].keys())
        writer.writeheader()
        writer.writerows(rows)

def setup():
    plt.rcParams.update({"font.size": 14, "axes.spines.top": False, "axes.spines.right": False})
    return dict(figsize=(10,10), dpi=120, layout="constrained")

def checkpoint_plot(rows):
    fig, ax = plt.subplots(**setup())
    steps = [200,400,600]
    lines = []
    for seed in (302,303,304):
        values = [next(r["mean_overhead"] for r in rows if r["seed"]==seed and r["policy"]==f"sac-{n}") for n in steps]
        ax.plot(steps,values,marker="o",linewidth=1.6,label=f"Seed {seed}",alpha=.82)
        lines.append(values)
    ax.plot(steps, np.mean(lines,axis=0), marker="D",color="#23272c",linewidth=3,label="Three-seed mean")
    ax.set(xticks=steps, xlabel="Training windows",ylabel="Whole-run mean trip overhead",title="SAC checkpoints on development seeds")
    ax.grid(axis="y",alpha=.2)
    ax.legend()
    fig.savefig(HERE/"checkpoint_comparison.png")
    plt.close(fig)

def held_out_plots(best, rows):
    options=setup()
    overhead,(recent,cumulative)=plt.subplots(2,1,sharex=True,**options)
    completed,counts=plt.subplots(**options)
    labels={"static":("Fixed 0% / 60","#b75f13"),"rule-based":("Rule-based","#275caa"),f"sac-{best}":(f"SAC {best}","#118357")}
    evolution=[]
    for policy,(label,color) in labels.items():
        data=[]
        for row in [r for r in rows if r["policy"]==policy]:
            sim=HERE/"held-out"/str(row["seed"])/policy/"simulation"
            events=[json.loads(line) for line in (sim/"trips.jsonl").read_text().splitlines()]
            ticks=np.array([e["tick"] for e in events])
            h=np.array([e["overhead"] for e in events])
            assert len(h)==row["completed_trips"] and np.isclose(h.mean(),row["mean_overhead"])
            assert np.all((ticks>200)&(ticks<=10000))
            n=np.histogram(ticks-1,EDGES)[0]
            sums=np.histogram(ticks-1,EDGES,weights=h)[0]
            cn=n.cumsum()
            def ratio(values,num):
                return np.divide(values,num,out=np.full(len(num),np.nan),where=num>0)
            interval,overall=ratio(sums,n),ratio(sums.cumsum(),cn)
            for i,t in enumerate(EDGES[1:]):
                evolution.append(dict(seed=row["seed"],policy=policy,end_tick=t,interval_trips=n[i],total_trips=cn[i],
                                      interval_mean=interval[i] if n[i] else None,cumulative_mean=overall[i] if cn[i] else None))
            data.append((interval,overall,cn))
        assert len(data)==3
        for i,ax in enumerate((recent,cumulative,counts)):
            vals=np.asarray(data)[:,i,:]
            valid=np.all(np.isfinite(vals),axis=0)
            ax.plot(EDGES[1:][valid],vals[:,valid].mean(axis=0),label=label,color=color,linewidth=2.4)
    for fig in (overhead,completed):
        fig.suptitle("Held-out comparison - 10k ticks, 750 cars",fontsize=20)
    recent.set_title("Mean overhead per 200-tick interval",loc="left")
    cumulative.set_title("Cumulative mean overhead",loc="left")
    counts.set_title("Cumulative trips averaged across three seeds",loc="left")
    for ax in (recent,cumulative,counts):
        ax.grid(axis="y",alpha=.2)
        ax.set_xlim(0,10000)
        ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    for ax in (recent,cumulative): ax.set_ylabel("Trip overhead")
    for ax in (cumulative,counts): ax.set_xlabel("Simulation tick")
    for ax in (recent,counts): ax.legend(loc="best",fontsize=12)
    counts.set_ylabel("Completed smart-car trips")
    counts.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    overhead.savefig(HERE/"overhead_comparison.png")
    completed.savefig(HERE/"completed_trips_comparison.png")
    plt.close("all")
    csv_file("overhead_evolution.csv",evolution)

def main():
    best=read(HERE/"selection.json")["selected"]
    summary=[]
    runs=[]
    for stage in ("development","held-out"):
        rows=[read(p) for p in sorted((HERE/stage).glob("*/*/metrics.json"))]
        assert len(rows)==9 and all(row["ticks"]==10000 for row in rows)
        runs.extend(rows)
        for policy in sorted({r["policy"] for r in rows}):
            matched=[r for r in rows if r["policy"]==policy]
            assert len({r["seed"] for r in matched})==3
            summary.append(dict(stage=stage,policy=policy,**{m:mean(r[m] for r in matched) for m in MEASURES}))
        if stage=="development":
            checkpoint_plot(rows)
        else:
            held_out_plots(best,rows)
    csv_file("results.csv",summary)
    csv_file("per_seed.csv",[{k:r[k] for k in ("stage","policy","seed","ticks",*MEASURES)} for r in runs])
    print("Wrote three 1200-square PNGs, per-seed results and aggregate metrics.",flush=True)

if __name__=="__main__":
    main()
