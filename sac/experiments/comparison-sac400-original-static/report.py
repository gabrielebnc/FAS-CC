"""Plot equal-seed means from smart-car completions on the held-out seeds."""
import csv
import json
from statistics import mean
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.ticker import StrMethodFormatter
import numpy as np

def write_rows(path, rows):
    with path.open("w", newline="") as f:
        w=csv.DictWriter(f,fieldnames=rows[0].keys())
        w.writeheader()
        w.writerows(rows)

def report(output, original, seeds):
    policies={"static":("Fixed 50% / 600 ticks","#b95e12"),"sac-400":("SAC-400","#11734e")}
    rows, evolution, aggregate = [], [], []
    metrics=("mean_overhead","median_overhead","p95_overhead","completed_trips","complaint_rate")
    plt.rcParams.update({"font.size":15,"axes.spines.top":False,"axes.spines.right":False})
    options=dict(figsize=(10,10),dpi=120,layout="constrained")
    fig,(recent,cumulative)=plt.subplots(2,1,sharex=True,**options)
    trips,counts=plt.subplots(**options)
    ends=np.arange(200,10001,200)
    for policy,(label,color) in policies.items():
        series=[]
        for seed in seeds:
            folder=output/str(seed) if policy=="static" else original/"held-out"/str(seed)/policy
            row=json.loads((folder/"metrics.json").read_text())
            events=[json.loads(line) for line in (folder/"simulation/trips.jsonl").read_text().splitlines()]
            ticks=np.array([e["tick"] for e in events])
            h=np.array([e["overhead"] for e in events])
            assert len(h)==row["completed_trips"] and np.isclose(h.mean(),row["mean_overhead"])
            assert np.all((ticks>200)&(ticks<=10000))
            rows.append(dict(seed=seed,policy=policy,**{m:row[m] for m in metrics}))
            n=np.bincount((ticks-1)//200,minlength=len(ends))
            sums=np.bincount((ticks-1)//200,weights=h,minlength=len(ends))
            cn=n.cumsum()
            interval=np.divide(sums,n,out=np.full(len(n),np.nan),where=n>0)
            overall=np.divide(sums.cumsum(),cn,out=np.full(len(n),np.nan),where=cn>0)
            series.append((interval,overall,cn))
            for i,t in enumerate(ends):
                evolution.append(dict(seed=seed,policy=policy,end_tick=t,completed_trips=n[i],
                    interval_mean=interval[i] if n[i] else None,cumulative_trips=cn[i],
                    cumulative_mean=overall[i] if cn[i] else None))
        group=[r for r in rows if r["policy"]==policy]
        aggregate.append(dict(policy=policy, **{m:mean(r[m] for r in group) for m in metrics}))
        for i,ax in enumerate((recent,cumulative,counts)):
            vals=np.array(series)[:,i,:]
            valid=np.all(np.isfinite(vals),axis=0)
            ax.plot(ends[valid],vals[:,valid].mean(axis=0),color=color,label=label,linewidth=2.5)
    for figure in (fig,trips):
        figure.suptitle("SAC vs original static - 10k ticks, 750 cars",fontsize=20)
    recent.set_title("Mean overhead per 200 ticks",loc="left")
    cumulative.set_title("Cumulative mean overhead",loc="left")
    counts.set_title("Mean completed trips across seeds 305–307",loc="left")
    for ax in (recent,cumulative,counts):
        ax.set_xlim(0,10000)
        ax.xaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
        ax.grid(axis="y",alpha=.22)
    for ax in (recent,cumulative): ax.set_ylabel("Trip overhead")
    for ax in (counts,cumulative): ax.set_xlabel("Simulation tick")
    counts.set_ylabel("Cumulative completed smart-car trips")
    counts.yaxis.set_major_formatter(StrMethodFormatter("{x:,.0f}"))
    for ax in (recent,counts): ax.legend(loc="best",frameon=False)
    fig.savefig(output/"overhead_comparison.png")
    trips.savefig(output/"completed_trips_comparison.png")
    plt.close("all")
    write_rows(output/"results.csv",aggregate)
    write_rows(output/"per_seed.csv",rows)
    write_rows(output/"overhead_evolution.csv",evolution)
    print(json.dumps(aggregate),flush=True)
