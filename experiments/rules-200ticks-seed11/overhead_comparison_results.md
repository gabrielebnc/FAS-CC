# Static vs adaptive: recorded test comparison

**Adaptive performed better on this matched seed pair.** Both runs completed exactly 10,000 ticks with 750 cars.

| Metric | Static | Adaptive | Change |
| --- | ---: | ---: | ---: |
| Mean trip overhead | 3.1252 | 2.3860 | 23.65% lower |
| Median trip overhead | 2.4810 | 2.0386 | 17.83% lower |
| p95 trip overhead | 7.1081 | 4.8279 | 32.08% lower |
| Completed trips | 2,940 | 3,847 | 30.85% higher |
| Complaint rate | 24.08% | 16.45% | 7.63 percentage points lower |

Adaptive had lower mean overhead in **46 of 49** comparable 200-tick intervals.

![Two stacked overhead plots](/Users/gbianchi/Downloads/overhead_comparison_stacked.png)

The upper subplot compares nonoverlapping 200-tick interval means. The lower subplot uses the exact trip-weighted cumulative mean; it does not average interval means. Empty intervals remain undefined.

![Separate cumulative completed trips plot](/Users/gbianchi/Downloads/completed_trips_comparison.png)

The separate trips figure counts recorded smart-car trip completions after the existing warmup.

These results are from one matched seed pair. The comparison includes the initial change from 50% exploration / 600-tick rerouting to 0% / 60, so it does not isolate the benefit of later rule-based adjustments. It does not establish superiority over a tuned static policy.

This remains a local, uncommitted experiment. Standard defaults are unchanged.
