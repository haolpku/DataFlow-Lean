# Direct visual route

## Contents

1. Configuration
2. Inventory
3. Extraction and reconciliation
4. Gap retries
5. Operational recovery

## 1. Configuration

`run.json` uses `route: direct` and a `direct` block:

```json
{
  "inventory_window_pages": 10,
  "inventory_overlap_pages": 2,
  "inventory_zoom": 1.25,
  "inventory_workers": 32,
  "inventory_split_window_ids": [],
  "inventory_window_page_overrides": {},
  "extraction_window_pages": 5,
  "extraction_overlap_pages": 1,
  "extraction_zoom": 2.4,
  "extraction_workers": 64,
  "extraction_window_page_overrides": {},
  "extraction_extra_windows": [],
  "retry_page_padding": 1,
  "retry_max_pages": 8,
  "retry_zoom": 2.8,
  "retry_workers": 32,
  "manual_retry_ids": [],
  "retry_focus": {}
}
```

Use lower concurrency when the endpoint throttles or returns transport errors. Increase extraction
zoom for small old type, dense subscripts, or degraded scans. Do not increase zoom merely to fix
object recall; recall is controlled by inventory and window coverage.

## 2. Inventory

Inventory uses low-resolution overlapping page windows and returns only kinds, labels, short cues,
page spans, and boundary status. It must not reproduce formulas. Unnumbered explicit objects receive
stable page-derived labels such as `p40.d2` or `p41.x1`.

Check `direct/inventory/summary.json` for window errors, kind counts, and continuity candidates.
Overlapping inventory votes improve robustness but do not prove completeness.

## 3. Extraction and reconciliation

High-resolution windows receive inventory targets and may discover extras. Reconciliation groups by
`(record_type, kind, label)`, prefers complete/longer candidates, and records materially different
overlap transcriptions as conflicts instead of silently averaging them.

Inspect:

- `direct/extraction/windows/`: raw checkpointed responses;
- `direct/reconciliation/merged.json`: selected candidates;
- `direct/reconciliation/report.json`: missing, incomplete, conflicts, request errors, and usage.

## 4. Gap retries

The retry stage scans short numerical gaps only when neighboring labels share the same hierarchy and
occur within a small page distance. The gap prompt asks for visible source evidence and must not infer
an object from numbering alone. Newly confirmed inventory objects are extracted at higher zoom.

The same targeted extraction mechanism handles inventory misses, cross-window incomplete objects,
conflicting duplicates, and short inline fragments whose pronouns or connective words require a
visible antecedent sentence/formula. Remaining failures stay visible in `direct/retry/summary.json`.
Put visually audited high-risk IDs in `manual_retry_ids` when a specific formula needs a higher-zoom
adjudication even though deterministic checks did not flag it.

## 5. Operational recovery

All remote window results are individual JSON checkpoints. Re-run a failed stage without
`--force-stage` to execute only missing files. Use `--force-stage` after changing a prompt/profile or
when deliberately replacing successful checkpoints. Never delete the entire output directory to
retry one stage.

If only a few dense windows repeatedly stall, list their IDs in `inventory_split_window_ids`, narrow
their core pages with `inventory_window_page_overrides` or `extraction_window_page_overrides`, and add
any uncovered single-page calls through `extraction_extra_windows`. Use `retry_focus` only for
visually audited targets that need an explicit source boundary; changing one target's focus changes
only that target's checkpoint hash.
