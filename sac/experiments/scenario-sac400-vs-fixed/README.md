# Local disruption experiment

Compare the existing frozen SAC-400 actor with fixed 0% exploration and 60-tick rerouting. Each policy receives one fresh 10,000-tick simulation on seeds 401, 402 and 403, with the same starting nine-knob configuration. These seeds have not been used for training or checkpoint selection.

| Tick | Event |
| --- | --- |
| 1 | Start with a target of 750 cars |
| 2,000 | Increase the target to 1,000 cars for the rest of the run |
| 3,000 | Set all eight lanes of edges `2846#5`, `-2846#5`, `2846#6`, `-2846#6` to zero maximum speed |
| 7,000 | Restore each lane's original 25 m/s maximum speed |
| 10,000 | Stop |

The four directed edges form a contiguous central corridor with two lanes in each direction. The blockage acts on SUMO lane speeds. CrowdNav continues to discover traffic through its existing observations. Its routing graph remains connected, and minimal trip costs continue using the original map speeds. SUMO retains the existing 120-second teleport setting, so vehicles may bypass a blocked edge by teleporting. `telemetry.jsonl` and final results record teleport counts. The target population and actual active population can differ due to insertion queues and teleports.

The scenario is enabled only through `CROWDNAV_TEST_SCENARIO=1`. Its hook runs between the tick increment and `traci.simulationStep()` in both local copies of `Simulation.py`. Ordinary training uses the default disabled setting. Newly introduced vehicles start their trip clocks at tick 2,000.

The SAC weights, six inputs, normalization, action mapping, deterministic inference and 1,200/600-tick settlement and measurement timings come from the existing SAC-400 checkpoint. The evaluation bundle includes the runtime correction that accepts an external target population change. Commands contain only exploration and rerouting, and the API omits unspecified fields before forwarding them to Kafka. This prevents a queued controller command from restoring the old car count. Other unexpected routing changes still abort the controller. This experiment does not train a new policy.

`settings.json` identifies the exact checkpoint and scenario. `submission/checkpoint.json` is an identical copy of the existing checkpoint. `source/` records the implementation used for this attempt. Each simulation logs all original completed smart-car trips, monitoring snapshots every 100 ticks, applied configurations, lane-speed changes and controller decisions. SAC reward windows are checked against the raw trip events.

The report contains whole-run mean, median, p95 overhead, complaints, completed smart-car trips and teleports, with individual seeds and equal-seed averages. Phase results group trips by completion tick, so trips completing after an event can still reflect previous conditions. A single stochastic execution per policy and seed supports a preliminary comparison. Completed-trip metrics omit journeys still underway at the horizon.

The two PNGs are 1200 by 1200 pixels. The overhead figure has interval and cumulative subplots, and the completed-trip figure is separate. Lines average the three seeds. The dotted vertical line marks the population increase and the shaded region marks the corridor blockage.

Run from `sac/` with Docker available.

```sh
.venv/bin/python -u experiments/scenario-sac400-vs-fixed/evaluate.py
```
