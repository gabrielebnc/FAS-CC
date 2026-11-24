"""Probe equivalent zero-exploration encodings without altering any saved model.

Frozen soft targets marginalize next-action sampling. Look-ahead rows from the
same episode as the save are excluded from evaluation. Critic-only fits are
paired by optimizer seed and have fixed targets, data, and numbers of updates.
"""
import copy
import json
from pathlib import Path
import sys

import numpy as np
import torch
from stable_baselines3 import SAC

OUT = Path(__file__).resolve().parent
TRAIN = OUT.parent
sys.path.insert(0, str(TRAIN.parents[1]))
from UPISAS.strategies.my_strategy import Settings, action_configuration

def load(file):
    return json.loads(file.read_text())

def tensor(a):
    return torch.as_tensor(a, dtype=torch.float32)

def predictions(critic, s, a):
    return torch.cat(critic(s,a),dim=1)

def risk(critic, s, a, targets):
    with torch.no_grad():
        return float((predictions(critic,s,a).sub(targets[:,None]).square().mean()).sqrt())

@torch.no_grad()
def soft_targets(model, ns, rewards, seed):
    torch.manual_seed(seed)
    result=[]
    for block in ns.split(32):
        x=block.repeat(512,1)
        a, lp=model.actor.action_log_prob(x)
        v=predictions(model.critic_target,x,a).min(1).values-model.log_ent_coef.exp()*lp
        result.append(v.reshape(512,len(block)).mean(0))
    return rewards+model.gamma*torch.cat(result)

def effective(a):
    # The second axis is identical in the two fits. Isolate exploration aliases.
    return torch.stack((a[:,0].clamp_min(0),a[:,1]),dim=1)

def constant_actions(s,u,v):
    return tensor([u,v]).expand(len(s),2)

def reroute_action(ticks):
    return float(np.log(ticks/60)/np.log(2 if ticks<60 else 2.5))

@torch.no_grad()
def probe(critic,s,settings,old):
    aliases=(-.9,-.5,-.1,0.)
    ticks=(30,45,58,60,90,150)
    grid=[]
    for u in aliases:
        values=[]
        for r in ticks:
            v=reroute_action(r)
            assert action_configuration((u,v),old,settings)==dict(old,exploration_percentage=0.,re_route_every_ticks=r)
            q=predictions(critic,s,constant_actions(s,u,v)).min(1).values
            values.append(q)
        grid.append(torch.stack(values))
    q=torch.stack(grid).numpy() # encodings x intervals x states
    best=q.argmax(axis=1)
    gap=q[:,ticks.index(58)]-q[:,ticks.index(60)]
    # Comparing signs > 0 versus < 0 excludes numerical ties.
    return dict(states=len(s),alias_span_at_60_mean=float(np.ptp(q[:,ticks.index(60)],axis=0).mean()),
        rerouting_span_at_u_minus_half_mean=float(np.ptp(q[1],axis=0).mean()),
        best_of_six_changes=int((np.ptp(best,axis=0)>0).sum()),
        reversal_58_vs_60=int(((gap.min(0)<0)&(gap.max(0)>0)).sum()),
        near_tie_58_60_abs_Q_gap_median=float(np.median(abs(gap))),
        exact_repeated_forward_max_error=float(abs(q[1,ticks.index(60)]-predictions(critic,s,constant_actions(s,-.5,0.)).min(1).values.numpy()).max()))

def main():
    torch.set_num_threads(1)
    config=load(TRAIN/"SAC_SETTINGS.json")
    settings=Settings(**config["environment"])
    ds=[]
    for f in sorted((TRAIN/"episodes").glob("*/decisions.jsonl")):
        ds.extend([row for line in f.open() if (row:=json.loads(line))["event"]=="transition"])
    model=SAC.load(TRAIN/"checkpoint-000400/model.zip",device="cpu")
    saved=load(TRAIN/"checkpoint-000400/progress.json")
    n=saved["transitions"]
    s=tensor([r["state"] for r in ds]);ns=tensor([r["next_state"] for r in ds]);a=tensor([r["action"] for r in ds])
    rewards=tensor([r["reward"] for r in ds])
    seeds=np.array([r["simulator_seed"] for r in ds])
    train=np.arange(len(ds))<n
    test=seeds>=saved["next_simulator_seed"]
    assert not np.any(train&test) and set(seeds[train]).isdisjoint(seeds[test])
    zero=a[:,0].numpy()<=0
    for i,r in enumerate(ds):
        assert action_configuration(a[i].tolist(),r["old_configuration"],settings)==r["requested_configuration"]
    y=soft_targets(model,ns,rewards,43)
    y2=soft_targets(model,ns,rewards,44)
    # Common targets in both arms. Independent MC estimate checks integration precision.
    ymean=(y+y2)/2
    stats=dict(checkpoint=n,test_seeds=np.unique(seeds[test]).tolist(),test_rows=int(test.sum()),
        test_zero_rows=int((test&zero).sum()),test_positive_rows=int((test&~zero).sum()),
        repeated_target_estimate_rmse=float((y[test]-y2[test]).square().mean().sqrt()),
        train_state_probes=probe(model.critic,s[train],settings,ds[0]["old_configuration"]),
        test_state_probes=probe(model.critic,s[test],settings,ds[0]["old_configuration"]))
    held=test&zero
    stats["frozen_critic_test_RMSE_all"]=risk(model.critic,s[test],a[test],ymean[test])
    stats["frozen_critic_test_RMSE_zero"]=risk(model.critic,s[held],a[held],ymean[held])
    with torch.no_grad():
        vals=[]
        for u in (-.9,-.5,-.1,0.):
            aliases=a[held].clone();aliases[:,0]=u
            vals.append(predictions(model.critic,s[held],aliases).min(1).values)
        stats["alias_span_at_actually_applied_test_intervals"]=float((torch.stack(vals).amax(0)-torch.stack(vals).amin(0)).mean())
    fits={}
    for seed in (0,1,2,3,4):
        rng=np.random.default_rng(seed)
        batches=rng.integers(0,n,size=(400,32))
        for kind in ("raw","collapse_zero"):
            critic=copy.deepcopy(model.critic)
            optimizer=torch.optim.Adam(critic.parameters(),lr=3e-4)
            inputs=effective(a) if kind=="collapse_zero" else a
            for ids in batches:
                v=predictions(critic,s[ids],inputs[ids])
                loss=(v-ymean[ids,None]).square().mean()
                optimizer.zero_grad();loss.backward();optimizer.step()
            for label,mask in {"all":test,"zero":held,"positive":test&~zero}.items():
                fits.setdefault(kind,{}).setdefault(label,[]).append(risk(critic,s[mask],inputs[mask],ymean[mask]))
    stats["paired_critic_only_test_RMSE"]=fits
    # Real deployment commanded 0%/60 throughout this prefix, across both nominally matched runs.
    early={}
    for seed in (305,306,307):
        early[seed]={}
        for policy in ("static","sac-400"):
            folder=TRAIN.parent/"comparison-sac-600/held-out"/str(seed)/policy/"simulation"
            configs=[row for row in load(folder/"result.json")["configs"] if row["step"]<=2000]
            assert all(r["configuration"]["exploration_percentage"]==0 and r["configuration"]["re_route_every_ticks"]==60 for r in configs)
            overhead=[r["overhead"] for l in (folder/"trips.jsonl").open() if (r:=json.loads(l))["tick"]<=2000]
            early[seed][policy]=dict(completed=len(overhead),mean_overhead=float(np.mean(overhead)))
    stats["same_seed_same_settings_first_2000_ticks"]=early
    decisions=[]
    for seed in (305,306,307):
        folder=TRAIN.parent/"comparison-sac-600/held-out"/str(seed)/"sac-400/simulation"
        decisions.extend(r for l in (folder/"decisions.jsonl").open() if (r:=json.loads(l))["event"]=="transition")
    states=tensor([r["state"] for r in decisions])
    with torch.no_grad():
        acts=model.actor(states,deterministic=True)
        recorded=tensor([r["action"] for r in decisions])
        assert (acts-recorded).abs().max().item()<1e-5
        def difference(u):
            q=[]
            for r in (58,60):
                inp=torch.stack((u,torch.full_like(u,reroute_action(r))),dim=1)
                q.append(predictions(model.critic,states,inp).min(1).values)
            return q[0]-q[1]
        u=acts[:,0]
        assert torch.all(u<0)
        local=torch.stack([difference(torch.clamp(u+offset,-1,0)) for offset in (-.05,0.,.05)])
        stats["deployed_decisions"]={"n":len(decisions),"raw_u_range":[float(u.min()),float(u.max())],
            "Q_prefers_58_over_60_at_actor_u":int((difference(u)>0).sum()),
            "local_reversals_58_vs_60_within_raw_u_0.05":int(((local.min(0).values<0)&(local.max(0).values>0)).sum()),
            "median_Q58_minus_Q60":float(np.median(difference(u).numpy()))}
    scores={kind:float(np.mean(by_group["all"])) for kind,by_group in fits.items()}
    stats["mean_over_optimizer_seeds_RMSE"]=scores
    stats["fractional_reduction_proxy_RMSE"]=1-scores["collapse_zero"]/scores["raw"]
    (OUT/"critic_alias_results.json").write_text(json.dumps(stats,indent=2)+"\n")
    print(json.dumps(stats,indent=2))

if __name__=="__main__":
    main()
