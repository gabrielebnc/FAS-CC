# CrowdNav SAC checkpoints

## 200-window disruption attempt with seven state inputs

The new attempt uses fresh weights, simulator seeds beginning at 501, and fresh 10,000-tick episodes. Every episode increases the target population from 750 to 1,000 at tick 2,000, blocks the central corridor at tick 3,000 and reopens it at tick 7,000. SUMO retains its 120-second teleport setting.

The seventh input is relative overhead change between consecutive completed measurement windows. Its first value in each episode is zero. The corrected critics collapse all nonpositive exploration outputs to the same zero-exploration input. Automatic entropy starts at 0.01, and four gradient updates follow each eligible transition. Warmup is 32 transitions and batch size is 32, giving 672 updates after 200 windows. Routing actions span 0–10% exploration and 30–150 rerouting ticks.

Settlement is provisionally 1,050 ticks, followed by 600 measurement ticks. Each episode writes an exact reward-window audit and departure carryover counts. Congestion may exceed the 8% carryover target. The relative-overhead reward and throughput penalty use all completed trips in their measurement windows. Input scales and throughput calibration are recorded in the full settings file.

The full checkpoint, replay buffer, optimizers, entropy and RNG states will be saved under `experiments/sac-200-scenario-trend/checkpoint-000200/`. The background job exports the three Python files, requirements and JSON actor to `experiments/sac-200-scenario-trend-submission/`. Further evaluation follows the user's next request. A later extension to 400 restores this checkpoint and collects 200 additional transitions on the next fresh seeds.

[Full training and deployment settings](SAC_SETTINGS_scenario_200.json) · [Job log](experiments/sac-200-scenario-trend.log) · [Job status](experiments/sac-200-scenario-trend-job.json)

This attempt completed 200 windows and 672 updates over 50 episodes, seeds 501–550, totaling 500,000 simulation ticks. The exported seven-input actor passed 1,002 parity checks with zero rounded-rerouting mismatches. Measured departure carryover was 11.37% across 198 changed-routing windows, and 140 of those windows exceeded 8%. The 1,050-tick settlement target requires further assessment against congestion. The checkpoint model ID is `d89305cefe2f924ddf6c01483755a6df253f25945669d59b6c852fda9d810074`.

| Setting | Original SAC | Lower entropy and shorter wait |
| --- | --- | --- |
| Full training and deployment settings | [Original settings](experiments/sac-200-native/SAC_SETTINGS.json) | [Combined settings](SAC_SETTINGS_alpha01_settle1200.json) |
| Automatic entropy coefficient at training start | 1.0 | 0.1 |
| Target entropy | −2 | −2 |
| Settlement after confirmed application, in training and deployment | 1,800 ticks | 1,200 ticks |
| Reward measurement after settlement | 600 ticks | 600 ticks |
| Nominal first decision from episode start, including 200 warmup ticks | Tick 2,600 | Tick 2,000 |
| Nominal interval between subsequent decisions, plus application and polling delay | 2,400 ticks | 1,800 ticks |
| Training budget | 200 transitions | 200 transitions |
| Learning starts / batch size | 32 / 32 | 32 / 32 |
| Checkpoint selection | Final at 200 transitions | Final at 200 transitions |
| Training checkpoint directory | `experiments/sac-200-native/checkpoint-000200/` | `experiments/sac-200-alpha01-settle1200/checkpoint-000200/` |
| Deployment actor path | `experiments/sac-200-native-submission/checkpoint.json` | `experiments/sac-200-alpha01-settle1200-submission/checkpoint.json` |

Each run starts with fresh weights, learner seed 0, and simulator seed 110. The simulator seed advances by one per 100,000-tick episode. A shorter wait allows more transitions per episode, so the two runs can reach their budgets on different seeds. Each transition uses a completed post-action measurement window. Both runs target 750 cars and apply one gradient update per transition starting at transition 33.

Both use six state features and the same reward, the relative reduction in mean completed-trip overhead minus a penalty for throughput below the calibrated floor. The frozen actor deterministically selects route exploration from 0–50% and a rerouting interval from 30–600 ticks. Entropy tuning applies during training.

The original actor has 168 gradient updates. Its [export parity report](experiments/sac-200-native-submission-parity.json) and [seed 301 comparison](experiments/comparison-sac-200-seed301/results.csv) identify the evaluated model.

The combined run completed 200 transitions and 168 gradient updates in 2 h 2 min. The four 100,000-tick episodes contributed 53, 53, 53 and 41 transitions. The learned entropy coefficient finished at 0.0951, compared with 0.9506 in the original run. All 200 reward windows matched the raw trip records. The exported actor agreed with the training model on 1,002 states, including every rounded rerouting interval. [Training result](experiments/sac-200-alpha01-settle1200/training_result.json) · [Window audits](experiments/sac-200-alpha01-settle1200/window-audits.json) · [Export parity](experiments/sac-200-alpha01-settle1200-submission-parity.json)

## 600-window run starting at the fixed benchmark

This fresh run starts each episode at 0% route exploration and a 60-tick rerouting interval. Route exploration is `0.10 × max(0, u)`. The second actor output maps −1, 0 and +1 to 30, 60 and 150 ticks, with logarithmic interpolation on each side and integer rounding. State features describe the physical settings. Initial entropy is 0.1 with automatic tuning. Each action settles for 1,200 ticks before a 600-tick reward window. The relative-overhead reward and throughput floor follow the previous trial.

Training saves full checkpoints at windows 200, 400 and 600 in `experiments/sac-600-low-exploration/checkpoint-NNNNNN/`, as well as at episode boundaries. After training, the job exports a five-file deployment bundle for each milestone to `experiments/sac-600-low-exploration-submission-{window}/`. For matched evaluation, start CrowdNav with the same 0%/60-tick configuration before running each controller. [Full settings](SAC_SETTINGS_600_low_exploration.json) · [Job log](experiments/sac-600-low-exploration.log)
