#!/usr/bin/env python3
"""Run a bounded M2F experiment concurrently on selected FATE-H ids."""

import argparse
import concurrent.futures
import json
import traceback
from pathlib import Path

from dataflow_lean.operators.audit import LeanAuditOperator
from dataflow_lean.operators.fateh import FATEHWorkspaceOperator
from dataflow_lean.operators.stage2 import ProofRepairOperator
from dataflow_lean.providers import (CodexCLIProvider, OpenAICompatibleProvider,
                                     OpenAIMathlibToolProvider, OpenAIResponsesMathlibToolProvider)
from dataflow_lean.schema import Budget


class MemoryStorage:
    def __init__(self, rows):
        self.rows = rows

    def read(self, output_type="dict"):
        return self.rows

    def write(self, rows):
        self.rows = rows
        return "memory://next"


def run_one(row, fateh_root, output_root, model, reasoning, plans, attempts, base_url,
            tool_agent, responses_api):
    problem_id = int(row["id"])
    storage = MemoryStorage([row])
    FATEHWorkspaceOperator(str(fateh_root), str(output_root / "workspaces")).run(storage)
    project = Path(storage.rows[0]["stage1"]["project_root"])
    if base_url and tool_agent and responses_api:
        provider = OpenAIResponsesMathlibToolProvider(base_url, model, project, reasoning, timeout=1800)
    elif base_url and tool_agent:
        provider = OpenAIMathlibToolProvider(base_url, model, project, reasoning, timeout=1800)
    elif base_url:
        provider = OpenAICompatibleProvider(base_url, model, timeout=1800)
    else:
        provider = CodexCLIProvider(model, reasoning, timeout=1800)
    ProofRepairOperator(provider, provider, Budget(planning_rounds=plans, executor_attempts=attempts)).run(storage)
    if storage.rows[0]["stage2"]["success"]:
        LeanAuditOperator().run(storage)
    else:
        storage.rows[0]["audit"] = {"passed": False}
    result = storage.rows[0]
    result_path = output_root / "results" / f"{problem_id}.json"
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--fateh-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--ids", required=True)
    parser.add_argument("--workers", type=int, default=2)
    parser.add_argument("--model", default="gpt-5.6-sol")
    parser.add_argument("--base-url", help="OpenAI-compatible /v1 endpoint; otherwise use local Codex CLI")
    parser.add_argument("--tool-agent", action="store_true",
                        help="Enable controlled Mathlib search/read/Lean-check tool calling")
    parser.add_argument("--responses-api", action="store_true",
                        help="Use /responses with stateless function-call continuation")
    parser.add_argument("--reasoning-effort", default="medium")
    parser.add_argument("--planning-rounds", type=int, default=1)
    parser.add_argument("--executor-attempts", type=int, default=3)
    args = parser.parse_args()
    ids = {int(value) for value in args.ids.split(",")}
    inventory = json.loads((args.fateh_root / "FATE-H.json").read_text(encoding="utf-8"))
    rows = [row for row in inventory if int(row["id"]) in ids]
    if {int(row["id"]) for row in rows} != ids:
        parser.error("one or more ids are absent from FATE-H.json")
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(run_one, row, args.fateh_root, args.output_root, args.model,
                               args.reasoning_effort, args.planning_rounds, args.executor_attempts,
                               args.base_url, args.tool_agent, args.responses_api): row for row in rows}
        infrastructure_errors = []
        for future in concurrent.futures.as_completed(futures):
            row = futures[future]
            try:
                result = future.result()
            except Exception as exc:
                failure = {"id": int(row["id"]), "type": type(exc).__name__, "message": str(exc),
                           "traceback": traceback.format_exc()}
                infrastructure_errors.append(failure)
                error_path = args.output_root / "errors" / f"{row['id']}.json"
                error_path.parent.mkdir(parents=True, exist_ok=True)
                error_path.write_text(json.dumps(failure, indent=2) + "\n", encoding="utf-8")
                print(json.dumps({"id": row["id"], "infrastructure_error": str(exc)}))
                continue
            results.append(result)
            print(json.dumps({"id": result["id"], "success": result["audit"]["passed"],
                              "executor_calls": result["stage2"]["executor_calls"]}))
    metrics = {"attempted": len(results),
               "solved": sum(bool(row["audit"]["passed"]) for row in results),
               "failed_ids": sorted(int(row["id"]) for row in results if not row["audit"]["passed"]),
               "verifier_calls": sum(row["stage2"]["verifier_calls"] for row in results),
               "planner_calls": sum(row["stage2"]["planner_calls"] for row in results),
               "executor_calls": sum(row["stage2"]["executor_calls"] for row in results)}
    metrics["infrastructure_errors"] = infrastructure_errors
    metrics["psr"] = metrics["solved"] / metrics["attempted"] if metrics["attempted"] else 0.0
    args.output_root.mkdir(parents=True, exist_ok=True)
    (args.output_root / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
