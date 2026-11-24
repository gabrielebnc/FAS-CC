"""Local, uncommitted comparison of the recorded static and adaptive test runs."""
from collections import defaultdict
import csv
import json
import math
from pathlib import Path
import statistics

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.ticker import StrMethodFormatter

ROOT = Path(__file__).resolve().parent
PLOT_OUTPUT = Path.home() / 'Downloads'
PLOT_OUTPUT.mkdir(parents=True, exist_ok=True)
SEED = 11
HORIZON = 10000
runs = {}
for policy, folder in [('Static', '11-baseline'), ('Adaptive', '11-strategy')]:
    directory = ROOT / folder
    result = json.loads((directory / 'result.json').read_text())
    events = [json.loads(line) for line in (directory / 'trips.jsonl').read_text().splitlines()]
    assert result['seed'] == SEED and result['final_step'] == HORIZON
    assert len(events) == result['trip_count'] and events
    assert all(200 < e['tick'] <= HORIZON and math.isfinite(e['overhead']) for e in events)
    assert all(c['configs']['total_car_counter'] == 750 for c in result['configs'])
    values = np.array([e['overhead'] for e in events])
    bins, by_tick = defaultdict(list), defaultdict(list)
    for event in events:
        bins[((event['tick'] - 1) // 200 + 1) * 200].append(event['overhead'])
        by_tick[event['tick']].append(event['overhead'])
    bin_ticks = np.arange(200, HORIZON + 1, 200)
    interval_means = [statistics.mean(bins[t]) if bins[t] else np.nan for t in bin_ticks]
    cumulative, cumulative_ticks, total, count = [], [], 0., 0
    for tick in sorted(by_tick):
        total += sum(by_tick[tick])
        count += len(by_tick[tick])
        cumulative_ticks.append(tick)
        cumulative.append(total / count)
    cumulative_ticks.append(HORIZON)
    cumulative.append(cumulative[-1])
    trip_ticks = np.arange(HORIZON + 1)
    trip_counts = np.cumsum([len(by_tick[t]) for t in trip_ticks])
    assert int(trip_counts[-1]) == len(events)
    complaints = sum(e['complaint'] for e in events)
    runs[policy] = dict(result=result, values=values, bins=bins,
        bin_ticks=bin_ticks, interval_means=interval_means,
        cumulative_ticks=cumulative_ticks, cumulative=cumulative,
        trip_ticks=trip_ticks, trip_counts=trip_counts,
        metrics=dict(mean_overhead=float(np.mean(values)), median_overhead=float(np.median(values)),
            p95_overhead=float(np.quantile(values, .95)), trips=len(events),
            complaints=complaints, complaint_rate=complaints / len(events)))

b, a = runs['Static'], runs['Adaptive']
for field in ('map', 'warmup_ticks', 'smart_car_percentage'):
    assert a['result'][field] == b['result'][field]
assert a['result']['configs'][0]['configs'] == b['result']['configs'][0]['configs']
initial = b['result']['configs'][0]['configs']
assert all(c['configs'] == initial for c in b['result']['configs'])
for item in a['result']['configs']:
    assert all(item['configs'][k] == v for k, v in initial.items()
               if k not in ('exploration_percentage', 're_route_every_ticks'))
paired_bins = [t for t in b['bin_ticks'] if b['bins'][t] and a['bins'][t]]
window_wins = sum(statistics.mean(a['bins'][t]) < statistics.mean(b['bins'][t]) for t in paired_bins)
metrics = {policy: run['metrics'] for policy, run in runs.items()}
reductions = {key: 100 * (metrics['Static'][key] - metrics['Adaptive'][key]) / metrics['Static'][key]
              for key in ('mean_overhead', 'median_overhead', 'p95_overhead')}
throughput_increase = 100 * (metrics['Adaptive']['trips'] / metrics['Static']['trips'] - 1)
complaint_pp_reduction = 100 * (metrics['Static']['complaint_rate'] - metrics['Adaptive']['complaint_rate'])
summary = dict(seed=SEED, horizon=HORIZON, metrics=metrics, reduction_percent=reductions,
    throughput_increase_percent=throughput_increase, complaint_rate_reduction_percentage_points=complaint_pp_reduction,
    adaptive_lower_mean_windows=window_wins, compared_nonempty_windows=len(paired_bins),
    conclusion='Adaptive performed better on this matched seed pair.',
    limits='One seed pair; comparison includes the initial configuration correction and does not isolate its benefit from subsequent adaptation.')
(ROOT / 'overhead_comparison_results.json').write_text(json.dumps(summary, indent=2) + '\n')

plt.rcParams.update({'font.family': 'DejaVu Sans', 'font.size': 14,
                     'axes.titlesize': 16, 'axes.labelsize': 15,
                     'xtick.labelsize': 13, 'ytick.labelsize': 13, 'legend.fontsize': 14,
                     'axes.spines.top': False, 'axes.spines.right': False})
fig, axes = plt.subplots(2, 1, figsize=(10, 10), sharex=True, layout='constrained')
colors = {'Static': '#c77727', 'Adaptive': '#2466b3'}
for policy, run in runs.items():
    axes[0].plot(run['bin_ticks'], run['interval_means'], color=colors[policy],
                 linewidth=1.8, marker='o', markersize=2.5, label=policy)
    axes[1].plot(run['cumulative_ticks'], run['cumulative'], color=colors[policy],
                 linewidth=2.1, label=policy)
    end = run['metrics']['mean_overhead']
    axes[1].annotate(f'{policy}: {end:.3f}', xy=(HORIZON, end), xytext=(-8, 12),
                     textcoords='offset points', ha='right', color=colors[policy],
                     weight='bold', bbox=dict(facecolor='white', edgecolor='none', alpha=.85, pad=1))
axes[0].set_title('Mean overhead per 200-tick interval', loc='left', pad=12)
axes[1].set_title('Cumulative mean overhead', loc='left', pad=12)
for ax in axes:
    ax.set_ylabel('Trip overhead (lower is better)')
    ax.set_xlim(0, HORIZON + 100)
    ax.axvspan(0, 200, color='#dde2e8', alpha=.65, zorder=0)
    ax.grid(axis='y', alpha=.2)
    ax.xaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
axes[0].legend(loc='upper right', frameon=False, ncol=2)
axes[0].set_ylim(1.8, max(np.nanmax(run['interval_means']) for run in runs.values()) + .2)
axes[1].set_ylim(max(0, min(min(run['cumulative']) for run in runs.values()) - .1),
                 max(max(run['cumulative']) for run in runs.values()) + .15)
axes[1].set_xlabel('CrowdNav simulation tick')
fig.suptitle('Static vs Baseline - 10k ticks, 750 cars', fontsize=20)
for extension in ('png',):
    fig.savefig(PLOT_OUTPUT / f'overhead_comparison_stacked.{extension}', dpi=120)
plt.close(fig)

fig, ax = plt.subplots(figsize=(10, 10), layout='constrained')
for policy, run in runs.items():
    ax.step(run['trip_ticks'], run['trip_counts'], where='post', color=colors[policy],
            linewidth=2.1, label=policy)
    trips = run['metrics']['trips']
    ax.annotate(f'{policy}: {trips:,}', xy=(HORIZON, trips),
                xytext=(-8, 12 if policy == 'Adaptive' else -20),
                textcoords='offset points', ha='right', color=colors[policy],
                weight='bold', bbox=dict(facecolor='white', edgecolor='none', alpha=.85, pad=1))
ax.set_title('Static vs Baseline - 10k ticks, 750 cars', fontsize=20, pad=14)
ax.set_ylabel('Recorded completed trips (higher is better)')
ax.set_xlabel('CrowdNav simulation tick')
ax.set_xlim(0, HORIZON + 100)
ax.set_ylim(0, max(run['metrics']['trips'] for run in runs.values()) * 1.14)
ax.axvspan(0, 200, color='#dde2e8', alpha=.65, zorder=0)
ax.grid(axis='y', alpha=.2)
ax.xaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
ax.yaxis.set_major_formatter(StrMethodFormatter('{x:,.0f}'))
ax.legend(loc='upper left', frameon=False, ncol=2)
for extension in ('png',):
    fig.savefig(PLOT_OUTPUT / f'completed_trips_comparison.{extension}', dpi=120)
plt.close(fig)

report = ['# Static vs adaptive: recorded test comparison', '',
    '**Adaptive performed better on this matched seed pair.** Both runs completed exactly 10,000 ticks with 750 cars.', '',
    '| Metric | Static | Adaptive | Change |', '| --- | ---: | ---: | ---: |']
for label, key in [('Mean trip overhead', 'mean_overhead'), ('Median trip overhead', 'median_overhead'), ('p95 trip overhead', 'p95_overhead')]:
    report.append(f'| {label} | {metrics["Static"][key]:.4f} | {metrics["Adaptive"][key]:.4f} | {reductions[key]:.2f}% lower |')
report += [f'| Completed trips | {metrics["Static"]["trips"]:,} | {metrics["Adaptive"]["trips"]:,} | {throughput_increase:.2f}% higher |',
    f'| Complaint rate | {metrics["Static"]["complaint_rate"]:.2%} | {metrics["Adaptive"]["complaint_rate"]:.2%} | {complaint_pp_reduction:.2f} percentage points lower |', '',
    f'Adaptive had lower mean overhead in **{window_wins} of {len(paired_bins)}** comparable 200-tick intervals.', '',
    f'![Two stacked overhead plots]({PLOT_OUTPUT / "overhead_comparison_stacked.png"})', '',
    'The upper subplot compares nonoverlapping 200-tick interval means. The lower subplot uses the exact trip-weighted cumulative mean; it does not average interval means. Empty intervals remain undefined.', '',
    f'![Separate cumulative completed trips plot]({PLOT_OUTPUT / "completed_trips_comparison.png"})', '',
    'The separate trips figure counts recorded smart-car trip completions after the existing warmup.', '',
    'These results are from one matched seed pair. The comparison includes the initial change from 50% exploration / 600-tick rerouting to 0% / 60, so it does not isolate the benefit of later rule-based adjustments. It does not establish superiority over a tuned static policy.', '',
    'This remains a local, uncommitted experiment. Standard defaults are unchanged.', '']
(ROOT / 'overhead_comparison_results.md').write_text('\n'.join(report))
print(json.dumps(summary, indent=2))
