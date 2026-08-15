#!/usr/bin/env python3
"""Deterministic schema, traceability, and anomaly audit for final datasets."""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from common import load_json, resolve_config, write_json


ABSOLUTE_PATH = re.compile(
    r"(?:file://\S+|/(?:Users|home|volume)/[^\s)]+|"
    r"(?<![A-Za-z0-9])[A-Za-z]:[\\/](?:Users|Documents|Downloads|Windows|Program Files)/[^\s)]*)"
)


def percentile(values: list[int], fraction: float) -> int:
    if not values:
        return 0
    return sorted(values)[round((len(values) - 1) * fraction)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    args = parser.parse_args(); config = resolve_config(args.config)
    output, profile = Path(config["output"]), load_json(Path(config["profile"]))
    dataset_path = output / "dataset" / "environments.json"
    if not dataset_path.exists():
        raise SystemExit(f"dataset missing: {dataset_path}")
    rows: list[dict[str, Any]] = load_json(dataset_path)
    ids = [row.get("id", "") for row in rows]
    kind_label = [(row.get("kind", ""), row.get("label", "")) for row in rows]
    lengths = [len(row.get("statement", "")) for row in rows]
    errors, warnings = [], []
    duplicate_ids = sorted(key for key, count in Counter(ids).items() if count > 1)
    duplicate_kind_labels = sorted([list(key) for key, count in Counter(kind_label).items() if count > 1])
    empty = [row.get("id") for row in rows if not str(row.get("statement", "")).strip()]
    malformed_ids = [row.get("id") for row in rows if row.get("id") != f"{row.get('kind')}:{row.get('label')}"]
    unexpected_kinds = sorted(set(row.get("kind", "") for row in rows) - set(profile["kinds"]))
    bad_roles = [row.get("id") for row in rows if row.get("supporting_text_role", "") not in {
        "", "proof", "derivation", "explanation", "answer", "hint", "solution", "hint_or_solution"
    }]
    missing_sources = [row.get("id") for row in rows if not row.get("statement_sources")]
    path_leaks = [row.get("id") for row in rows if ABSOLUTE_PATH.search(
        row.get("statement", "") + "\n" + row.get("supporting_text", ""))]
    nonportable_sources = [row.get("id") for row in rows if any(
        source.get("image_source") or ABSOLUTE_PATH.search(json.dumps(source, ensure_ascii=False))
        for source in row.get("statement_sources", []) + row.get("supporting_sources", [])
    )]
    raw_path = output / "segmentation" / "environments_raw.json"
    missing_final, unexpected_final = [], []
    if raw_path.exists():
        raw_ids = {row.get("id", "") for row in load_json(raw_path)}
        final_ids = set(ids)
        missing_final = sorted(raw_ids - final_ids)
        unexpected_final = sorted(final_ids - raw_ids)
    missing_assets = []
    for row in rows:
        for asset in row.get("assets", []):
            if not (output / "dataset" / asset.get("image", "")).is_file():
                missing_assets.append({"id": row.get("id"), "image": asset.get("image")})
    for label, values in (
        ("empty dataset", ["no rows"] if not rows else []),
        ("duplicate ids", duplicate_ids), ("duplicate kind+label", duplicate_kind_labels),
        ("malformed ids", malformed_ids), ("unexpected kinds", unexpected_kinds),
        ("empty statements", empty), ("bad roles", bad_roles), ("absolute path leaks", path_leaks),
        ("nonportable source metadata", nonportable_sources), ("missing assets", missing_assets),
        ("raw records missing from final dataset", missing_final),
        ("unexpected final records", unexpected_final),
    ):
        if values: errors.append({"check": label, "items": values})
    if missing_sources: warnings.append({"check": "missing statement source trace", "items": missing_sources})
    if any(row.get("uncertain_spans") for row in rows):
        warnings.append({"check": "uncertain spans", "items": [row["id"] for row in rows if row.get("uncertain_spans")]})
    counts = dict(sorted(Counter(row.get("kind", "") for row in rows).items()))
    missing_kinds = [kind for kind in profile["kinds"] if counts.get(kind, 0) == 0]
    if missing_kinds:
        warnings.append({"check": "configured kinds with zero detections", "items": missing_kinds})
    expected = profile.get("expected_counts", {})
    count_mismatches = {kind: {"expected": count, "actual": counts.get(kind, 0)}
                        for kind, count in expected.items() if counts.get(kind, 0) != count}
    if count_mismatches:
        warnings.append({"check": "expected count mismatch", "items": count_mismatches})
    direct_retry = output / "direct" / "retry" / "summary.json"
    if direct_retry.is_file():
        retry = load_json(direct_retry)
        unresolved = {
            "missing_objects": retry.get("remaining_missing_objects", []),
            "missing_support": retry.get("remaining_missing_support", []),
            "incomplete": retry.get("remaining_incomplete", []),
            "conflicts": retry.get("remaining_conflicts", []),
            "context_fragments": retry.get("remaining_context_fragments", []),
        }
        unresolved = {key: value for key, value in unresolved.items() if value}
        if unresolved:
            errors.append({"check": "direct route unresolved after retry", "items": unresolved})
    direct_reconciliation = output / "direct" / "reconciliation" / "report.json"
    if direct_reconciliation.is_file():
        reconciliation = load_json(direct_reconciliation)
        if reconciliation.get("request_errors"):
            errors.append({"check": "direct route request errors", "items": reconciliation["request_errors"]})
    low, high = percentile(lengths, 0.02), percentile(lengths, 0.98)
    anomalies = [{"id": row["id"], "statement_chars": len(row["statement"])} for row in rows
                 if len(row["statement"]) <= low or len(row["statement"]) >= high]
    distant = []
    for row in rows:
        statement_pages = [x.get("pdf_page") for x in row.get("statement_sources", []) if x.get("pdf_page")]
        support_pages = [x.get("pdf_page") for x in row.get("supporting_sources", []) if x.get("pdf_page")]
        if statement_pages and support_pages:
            distance = min(abs(a-b) for a in statement_pages for b in support_pages)
            if distance >= 5: distant.append({"id": row["id"], "page_distance": distance})
    report = {
        "ok": not errors, "dataset": str(dataset_path), "environments": len(rows),
        "counts_by_kind": counts, "with_supporting_text": sum(bool(x.get("supporting_text")) for x in rows),
        "errors": errors, "warnings": warnings,
        "sampling": {
            "length_anomalies": anomalies,
            "distant_support": sorted(distant, key=lambda x: -x["page_distance"]),
            "one_per_kind": [next(row["id"] for row in rows if row["kind"] == kind) for kind in counts],
        },
        "statement_length": {"p02": low, "p50": percentile(lengths, .5), "p98": high},
    }
    write_json(output / "dataset" / "quality_report.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    raise SystemExit(0 if report["ok"] else 2)


if __name__ == "__main__":
    main()
