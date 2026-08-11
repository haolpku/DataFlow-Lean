#!/usr/bin/env python3
"""DataFlow-style verifier-in-the-loop runner for the FATE-H benchmark."""

from __future__ import annotations

import argparse
import concurrent.futures
import dataclasses
import json
import os
import re
import shutil
import subprocess
import textwrap
import time
from pathlib import Path
from typing import Any, Iterable


PROOF_MARKER = ":= by"
FORBIDDEN = {
    "sorry": re.compile(r"\bsorry\b"),
    "admit": re.compile(r"\badmit\b"),
    "axiom": re.compile(r"(?m)^\s*axiom\b"),
    "unsafe": re.compile(r"(?m)^\s*unsafe\b"),
}


def write_jsonl(path: Path, rows: Iterable[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False) + "\n")
    os.replace(temp, path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as stream:
        return [json.loads(line) for line in stream if line.strip()]


@dataclasses.dataclass
class StepStorage:
    """Small JSONL step cache matching DataFlow's operator-pipeline model."""

    root: Path

    def path(self, step: str) -> Path:
        return self.root / f"{step}.jsonl"

    def read(self, step: str) -> list[dict[str, Any]]:
        return read_jsonl(self.path(step))

    def write(self, step: str, rows: Iterable[dict[str, Any]]) -> Path:
        path = self.path(step)
        write_jsonl(path, rows)
        return path


class FATEHTaskLoader:
    def run(self, fateh_root: Path, ids: set[int] | None) -> list[dict[str, Any]]:
        inventory = json.loads((fateh_root / "FATE-H.json").read_text(encoding="utf-8"))
        tasks = []
        for item in inventory:
            problem_id = int(item["id"])
            if ids is not None and problem_id not in ids:
                continue
            tasks.append(
                {
                    "id": problem_id,
                    "informal_statement": item["informal_statement"],
                    "formal_statement": item["formal_statement"],
                    "tag": item.get("tag", []),
                    "version": item.get("version"),
                    "source_file": str((fateh_root / "FATEH" / f"{problem_id}.lean").resolve()),
                }
            )
        return tasks


class WorkspaceBuilder:
    def run(self, task: dict[str, Any], fateh_root: Path, work_root: Path) -> Path:
        task_root = work_root / str(task["id"])
        source_dir = task_root / "FATEH"
        source_dir.mkdir(parents=True, exist_ok=True)
        for name in ("lakefile.lean", "lake-manifest.json", "lean-toolchain"):
            shutil.copy2(fateh_root / name, task_root / name)
        shutil.copy2(task["source_file"], source_dir / f"{task['id']}.lean")

        base_packages = fateh_root / ".lake" / "packages"
        task_lake = task_root / ".lake"
        task_lake.mkdir(exist_ok=True)
        packages = task_lake / "packages"
        if not packages.exists() and base_packages.exists():
            packages.symlink_to(base_packages, target_is_directory=True)
        return task_root


def statement_prefix(source: str) -> str:
    if PROOF_MARKER not in source:
        raise ValueError("target file does not contain ':= by'")
    # FATE-H files contain one top-level theorem, while generated tactic blocks may
    # contain many local declarations ending in `:= by`.  The first marker is the
    # frozen theorem boundary; using the last marker would mistake proof text for
    # part of the signature.
    return source.split(PROOF_MARKER, 1)[0] + PROOF_MARKER


def error_count(output: str) -> int:
    return len(re.findall(r"(?m)^.*\berror(?:\([^)]*\))?:", output))


class LeanVerifier:
    def run(self, task_root: Path, problem_id: int, original: str) -> dict[str, Any]:
        target = task_root / "FATEH" / f"{problem_id}.lean"
        candidate = target.read_text(encoding="utf-8")
        signature_unchanged = statement_prefix(candidate) == statement_prefix(original)
        forbidden = [name for name, pattern in FORBIDDEN.items() if pattern.search(candidate)]
        started = time.monotonic()
        proc = subprocess.run(
            ["lake", "env", "lean", f"FATEH/{problem_id}.lean"],
            cwd=task_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=600,
        )
        output = proc.stdout[-12000:]
        return {
            "lean_exit_code": proc.returncode,
            "lean_seconds": round(time.monotonic() - started, 3),
            "lean_errors": error_count(output),
            "lean_output": output,
            "signature_unchanged": signature_unchanged,
            "forbidden": forbidden,
            "success": proc.returncode == 0 and signature_unchanged and not forbidden,
        }


class CodexProofRepair:
    def __init__(self, attempts: int, model: str | None, reasoning_effort: str):
        self.attempts = attempts
        self.model = model
        self.reasoning_effort = reasoning_effort

    @staticmethod
    def prompt(
        problem_id: int, informal: str, source: str, attempt: int, diagnostics: str,
        previous_proof: str,
    ) -> str:
        diagnostic_section = ""
        if diagnostics:
            diagnostic_section = f"\nThe previous independent Lean check ended with:\n```\n{diagnostics[-6000:]}\n```\n"
        if previous_proof:
            diagnostic_section += (
                "\nPrevious proof proposal to repair:\n```lean\n"
                + previous_proof
                + "\n```\n"
            )
        return f"""Produce one candidate Lean 4 proof for a FATE-H theorem.

Target file: FATEH/{problem_id}.lean
Informal statement: {informal}

Exact source file:
```lean
{source}
```

Rules:
- Return JSON matching the requested schema. Put only the tactic block after `:= by`
  in `proof`; do not include an outer `by`.
- Do not use sorry, admit, axiom, unsafe, or change the statement.
- You may inspect the read-only Mathlib source if useful, but do not edit files and do
  not run Lean: the DataFlow verifier will compile exactly once after your proposal.
- Use the diagnostics from the previous proposal to repair it. This is proposal attempt {attempt}.
{diagnostic_section}
"""

    @staticmethod
    def install_proof(original: str, proof: str) -> str:
        # Models sometimes indent the whole returned tactic block.  Dedent it
        # before adding the two spaces required under the theorem's `by`.
        proof = textwrap.dedent(proof).strip()
        if proof.startswith("by\n") or proof == "by":
            proof = textwrap.dedent(proof[2:].lstrip("\n"))
        # Add the indentation required beneath `:= by` while preserving relative nesting.
        indented = "\n".join(("  " + line) if line else "" for line in proof.splitlines())
        return statement_prefix(original) + "\n" + indented + "\n"

    def run(self, task: dict[str, Any], task_root: Path, verifier: LeanVerifier) -> dict[str, Any]:
        problem_id = int(task["id"])
        target = task_root / "FATEH" / f"{problem_id}.lean"
        original = target.read_text(encoding="utf-8")
        diagnostics = ""
        previous_proof = ""
        attempts = []
        total_started = time.monotonic()

        for attempt in range(1, self.attempts + 1):
            # Each proposal starts from the certified statement and the latest diagnostic.
            target.write_text(original, encoding="utf-8")
            message_path = task_root / f"codex_attempt_{attempt}_last.txt"
            schema_path = Path(__file__).with_name("proof.schema.json").resolve()
            command = [
                "codex", "exec", "--ephemeral", "--skip-git-repo-check",
                "--ignore-user-config", "-s", "read-only", "--json",
                "--output-schema", str(schema_path),
                "-c", f'model_reasoning_effort="{self.reasoning_effort}"',
                "-C", str(task_root), "-o", str(message_path),
            ]
            if self.model:
                command.extend(["-m", self.model])
            command.append(
                self.prompt(
                    problem_id, task["informal_statement"], original, attempt,
                    diagnostics, previous_proof,
                )
            )

            started = time.monotonic()
            proc = subprocess.run(
                command,
                cwd=task_root,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                timeout=1800,
            )
            proposal_seconds = round(time.monotonic() - started, 3)
            trace = proc.stdout
            trace_path = task_root / f"codex_attempt_{attempt}.jsonl"
            trace_path.write_text(trace, encoding="utf-8")
            proposal_error = None
            try:
                payload = json.loads(message_path.read_text(encoding="utf-8"))
                previous_proof = payload["proof"]
                candidate = self.install_proof(original, previous_proof)
                target.write_text(candidate, encoding="utf-8")
                verification = verifier.run(task_root, problem_id, original)
            except Exception as exc:
                proposal_error = f"{type(exc).__name__}: {exc}"
                verification = {
                    "lean_exit_code": None,
                    "lean_seconds": 0.0,
                    "lean_errors": 1,
                    "lean_output": proposal_error,
                    "signature_unchanged": True,
                    "forbidden": [],
                    "success": False,
                }
            attempts.append(
                {
                    "attempt": attempt,
                    "codex_exit_code": proc.returncode,
                    "proposal_seconds": proposal_seconds,
                    "proposal_error": proposal_error,
                    "verifier_calls": 1 if proposal_error is None else 0,
                    "verification": verification,
                    "trace": str(trace_path),
                }
            )
            if verification["success"]:
                break
            diagnostics = verification["lean_output"]

        final_verification = attempts[-1]["verification"]
        if not final_verification["success"]:
            target.write_text(original, encoding="utf-8")
        return {
            **task,
            "success": final_verification["success"],
            "attempt_count": len(attempts),
            "wall_seconds": round(time.monotonic() - total_started, 3),
            "attempts": attempts,
            "workspace": str(task_root),
        }


def run_one(
    task: dict[str, Any], fateh_root: Path, work_root: Path, attempts: int,
    model: str | None, reasoning_effort: str,
) -> dict[str, Any]:
    builder = WorkspaceBuilder()
    verifier = LeanVerifier()
    prover = CodexProofRepair(attempts=attempts, model=model, reasoning_effort=reasoning_effort)
    task_root = builder.run(task, fateh_root, work_root)
    try:
        return prover.run(task, task_root, verifier)
    except subprocess.TimeoutExpired as exc:
        return {**task, "success": False, "error": f"timeout: {exc}", "workspace": str(task_root)}
    except Exception as exc:  # Preserve a task-level failure without stopping the batch.
        return {**task, "success": False, "error": f"{type(exc).__name__}: {exc}", "workspace": str(task_root)}


def aggregate(rows: list[dict[str, Any]]) -> dict[str, Any]:
    solved = sum(bool(row.get("success")) for row in rows)
    attempted = len(rows)
    max_attempts = max((len(row.get("attempts", [])) for row in rows), default=0)
    pass_at = {
        str(k): sum(
            any(
                bool((attempt.get("verification") or {}).get("success"))
                for attempt in row.get("attempts", [])[:k]
            )
            for row in rows
        ) / attempted if attempted else 0.0
        for k in range(1, max_attempts + 1)
    }
    return {
        "attempted": attempted,
        "solved": solved,
        "psr": solved / attempted if attempted else 0.0,
        "failed_ids": [row["id"] for row in rows if not row.get("success")],
        "total_wall_seconds_sum": round(sum(float(row.get("wall_seconds", 0)) for row in rows), 3),
        "total_attempts": sum(int(row.get("attempt_count", 0)) for row in rows),
        "verifier_calls": sum(
            int(attempt.get("verifier_calls", 0))
            for row in rows for attempt in row.get("attempts", [])
        ),
        "pass_at": pass_at,
        "lean_seconds_sum": round(sum(
            float((attempt.get("verification") or {}).get("lean_seconds", 0))
            for row in rows for attempt in row.get("attempts", [])
        ), 3),
    }


def parse_ids(raw: str | None) -> set[int] | None:
    if not raw:
        return None
    values: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if "-" in part:
            start, end = (int(value) for value in part.split("-", 1))
            values.update(range(start, end + 1))
        else:
            values.add(int(part))
    return values


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fateh-root", type=Path, default=Path("third_party/FATE-H"))
    parser.add_argument("--output-root", type=Path, default=Path("artifacts/fateh_dataflow"))
    parser.add_argument("--ids", help="Comma-separated ids and ranges, e.g. 1,2,10-20")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--attempts", type=int, default=1)
    parser.add_argument("--model")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--force", action="store_true")
    args = parser.parse_args()

    fateh_root = args.fateh_root.resolve()
    output_root = args.output_root.resolve()
    work_root = output_root / "workspaces"
    storage = StepStorage(output_root / "steps")
    tasks = FATEHTaskLoader().run(fateh_root, parse_ids(args.ids))
    storage.write("00_tasks", tasks)

    prior = {int(row["id"]): row for row in storage.read("10_results")}
    pending = [task for task in tasks if args.force or int(task["id"]) not in prior]
    results = [prior[int(task["id"])] for task in tasks if not args.force and int(task["id"]) in prior]

    if pending:
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
            futures = {
                executor.submit(
                    run_one, task, fateh_root, work_root, args.attempts,
                    args.model, args.reasoning_effort,
                ): task
                for task in pending
            }
            for future in concurrent.futures.as_completed(futures):
                result = future.result()
                results.append(result)
                results.sort(key=lambda row: int(row["id"]))
                storage.write("10_results", results)
                print(json.dumps({"id": result["id"], "success": result.get("success"), "done": len(results), "total": len(tasks)}))

    results.sort(key=lambda row: int(row["id"]))
    storage.write("10_results", results)
    metrics = aggregate(results)
    metrics_path = output_root / "metrics.json"
    metrics_path.parent.mkdir(parents=True, exist_ok=True)
    metrics_path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
