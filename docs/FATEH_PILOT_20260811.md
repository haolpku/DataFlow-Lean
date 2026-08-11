# FATE-H pilot — 2026-08-11

This is a local pipeline calibration, not a claimed full-benchmark score.

| Metric | Result |
|---|---:|
| pass@1 | 1/10 (10%) |
| pass@2 | 5/10 (50%) |
| pass@3 / PSR | 6/10 (60%) |
| Independent Lean verifier calls | 24 |

The stratified IDs were `1,10,20,30,40,50,60,70,80,90`. Passed IDs were
`1,10,20,40,50,80`; the remaining four failed after three proposals.

Configuration: FATE-H commit `17967b3118082adfba7c5a5fc03b5f4a53717b59`,
Lean/Mathlib `v4.28.0`, three workers, medium reasoning effort, and a three
proposal repair budget. Because the sample is systematic rather than random,
the official score requires running all 100 tasks with frozen settings.
