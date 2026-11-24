"""Read-only diagnostics for the 600-transition experiment; no simulator runs or fitting SAC."""
import json
from pathlib import Path
import numpy as np
import torch
from stable_baselines3 import SAC

OUT = Path(__file__).resolve().parent
TRAIN = OUT.parent
EXPERIMENTS = TRAIN.parent
CHECK = EXPERIMENTS / "comparison-sac-600"

def records(file):
    return [json.loads(line) for line in file.read_text().splitlines()]

def mean(x):
    return float(np.mean(x))

def correlation(x, y):
    return float(np.corrcoef(x, y)[0, 1])

def cross_validate(X, y, episodes):
    """Leave one simulator seed out. Standardise on training folds; ridge penalty 1."""
    pred=np.zeros_like(y)
    for episode in np.unique(episodes):
        test=episodes==episode
        obs=X[~test]
        mu=obs.mean(axis=0)
        sd=obs.std(axis=0)
        sd[sd<1e-9]=1
        fit=(obs-mu)/sd
        beta=np.linalg.solve(fit.T@fit + np.eye(fit.shape[1]),fit.T@(y[~test]-y[~test].mean()))
        pred[test]=y[~test].mean()+(X[test]-mu)/sd@beta
    return float(np.sqrt(mean((y-pred)**2)))

def main():
    ds=[]; eps=[]; window_counts=[]; predates=0; for_reward=0; total=0
    for folder in sorted((TRAIN/"episodes").iterdir()):
        events=records(folder/"trips.jsonl")
        ends=np.array([e["tick"] for e in events]); starts=np.array([e["departure_tick"] for e in events])
        assert np.all(np.diff(ends)>=0)
        total+=len(events)
        for d in records(folder/"decisions.jsonl"):
            if d["event"]!="transition":
                continue
            ds.append(d);eps.append(d["simulator_seed"])
            w=d["after"]
            lo, hi=ends.searchsorted([w["start_step"],w["end_step"]],side="right")
            assert hi-lo==w["completed_trips"]
            for_reward+=hi-lo;window_counts.append(hi-lo)
            predates+=int(np.sum(starts[lo:hi]<d["confirmed_step"]))
    assert len(ds)==600
    S=np.array([d["state"] for d in ds])
    p=np.array([d["requested_configuration"]["exploration_percentage"] for d in ds])
    logR=np.log([d["requested_configuration"]["re_route_every_ticks"] for d in ds]);R=np.exp(logR).round().astype(int)
    b=np.array([d["before"]["overhead"] for d in ds]); y=np.array([d["after"]["overhead"] for d in ds])
    step=np.array([d["decision_step"] for d in ds])
    reward=np.array([d["reward"] for d in ds]); e=np.array(eps)
    def strat(mask):
        return {"n":int(mask.sum()),"after_mean":mean(y[mask]),"SD_across_windows":float(np.std(y[mask]))} if mask.any() else {}
    zero=p==0
    intervals={"30-45":R<=45,"46-90":(R>45)&(R<=90),"91-150":R>90}
    groups={s:{k:strat(zero&m&v) for k,v in intervals.items()} for s,m in
            {"all":p>=0,"before < 2.5":b<2.5,"before >= 2.5":b>=2.5}.items()}
    statecv={}
    for name,mask in {"all":p>=0,"zero_exploration":zero}.items():
        # R is centred on the physical reference. Interactions model reroute effects conditional on observed state.
        r=logR-np.log(60)
        A=np.column_stack([p,p*p,r,r*r,p*r])
        X=np.column_stack([A,S])
        models={"constant":np.empty((len(y),0)),"action":A,"action_and_state":X,
                "with_action_state_interactions":np.column_stack([X,r[:,None]*S,p[:,None]*S])}
        statecv[name]={k:cross_validate(v[mask],y[mask],e[mask]) for k,v in models.items()}
    print('LOEO RMSE',statecv)
    print('zero rows',groups)
    folds=[]
    for z in np.unique(e):
        sub=zero&(e==z);short=sub&(R<=45);long=sub&(R>90)
        if short.any() and long.any():
            folds.append(float(y[short].mean()-y[long].mean()))
    print('short minus long by episode',folds)
    # Directly query the minimum of the two SAC critics, and compare with actor actions.
    torch.set_num_threads(1)
    probes={}
    with torch.no_grad():
        for n in (200,400,600):
            model=SAC.load(TRAIN/f"checkpoint-{n:06d}/model.zip",device="cpu")
            # Use states actually observed by this checkpoint up to its training budget.
            T=torch.tensor(S[:n],dtype=torch.float32)
            def critics(u,v):
                action=torch.tensor([[u,v]],dtype=torch.float32).expand(len(T),2)
                return torch.cat(model.critic(T,action),dim=1).numpy()
            def span(pairs):
                vals=np.array([critics(u,v).min(axis=1) for u,v in pairs])
                return mean(np.ptp(vals,axis=0))
            def v_for(t): return np.log(t/60)/np.log(2 if t<60 else 2.5)
            candidates=[30,45,60,90,150]
            raw=np.stack([critics(-.5,v_for(t)) for t in candidates])
            best=raw.min(axis=2).argmax(axis=0)
            pair_agree=mean(raw[:,:,0].argmax(axis=0)==raw[:,:,1].argmax(axis=0))
            A=model.predict(T.numpy(),deterministic=True)[0]
            reroute=np.rint(np.where(A[:,1]<0,60*2**A[:,1],60*2.5**A[:,1]))
            probes[n]={"mean_Q_span_identical_zero_exploration_60_ticks":span([(u,0) for u in (-.9,-.5,-.1,0)]),
                  "mean_Q_span_distinct_rerouting_actions":span([(-.5,v_for(t)) for t in candidates]),
                  "rerouting_bins_preferred_by_min_Q":{r:int(sum(best==i)) for i,r in enumerate(candidates)},
                  "critic_pair_argmax_agreement":pair_agree,"actor_reroute_range":[int(reroute.min()),int(reroute.max())],
                  "max_actor_route_exploration":float(.1*np.maximum(0,A[:,0]).max())}
    print('critic probes',probes)
    early={}
    for seed in (305,306,307):
        early[seed]={}
        for policy in ("static","sac-400"):
            events=records(CHECK/"held-out"/str(seed)/policy/"simulation/trips.jsonl")
            early[seed][policy]=[]
            for lo,hi in [(200,2000),(2000,10000),(200,10000)]:
                h=[d['overhead'] for d in events if lo<d['tick']<=hi]
                early[seed][policy].append({"n":len(h),"mean":mean(h)})
    print('identical-start / later / total per seed',early)
    data={"reroute_bins_fixed_zero_exploration":groups,"short_minus_long_by_training_episode":folds,
          "leave_one_training_seed_out_RMSE":statecv,"critic_probes":probes,
          "heldout_first2000_then_rest_and_whole_run":early,
          "reward":{"corr_with_previous_overhead":correlation(b,reward),"corr_with_after_overhead":correlation(y,reward),
          "before_after_corr":correlation(b,y),"throughput_penalties":sum(d["throughput_penalty"]>0 for d in ds),
          "all_recorded_training_trips":total,"post_settlement_completions":int(for_reward),"started_before_confirmation":predates,
          "median_trips_in_window":float(np.median(window_counts))},
          "start_distribution":{"actions_by_tick_10000":int(sum(step<=10000)),"median_active_early":float(np.median(S[step<=10000,2]*750)),
          "median_active_after_10000":float(np.median(S[step>10000,2]*750))}}
    print('reward, early',data['reward'],data['start_distribution'])
    (OUT/"results.json").write_text(json.dumps(data,indent=2)+"\n")
if __name__=="__main__": main()
