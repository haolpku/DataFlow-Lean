"""Actual DataFlow graph for the M2F Stage-2 FATE-H evaluation."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from ..framework import DATAFLOW_AVAILABLE, FileStorage, PipelineABC
from ..operators import FATEHWorkspaceOperator, LeanAuditOperator, ProofRepairOperator
from ..providers import CodexCLIProvider, OpenAICompatibleProvider
from ..schema import Budget


def aggregate_m2f(rows):
    attempted = len(rows)
    solved = sum(bool(row.get("audit", {}).get("passed")) for row in rows)
    return {"attempted": attempted, "solved": solved, "psr": solved / attempted if attempted else 0.0,
            "failed_ids": [int(row["id"]) for row in rows if not row.get("audit", {}).get("passed")],
            "verifier_calls": sum(int(row.get("stage2", {}).get("verifier_calls", 0)) for row in rows),
            "planner_calls": sum(int(row.get("stage2", {}).get("planner_calls", 0)) for row in rows),
            "executor_calls": sum(int(row.get("stage2", {}).get("executor_calls", 0)) for row in rows),
            "tokens": sum(int(row.get("stage2", {}).get("tokens", 0)) for row in rows)}


class FATEHM2FPipeline(PipelineABC):
    def __init__(self, storage, *, fateh_root: str, work_root: str, planner, executor, budget=None):
        super().__init__()
        self.storage = storage
        self.workspace = FATEHWorkspaceOperator(fateh_root, work_root)
        self.proof_repair = ProofRepairOperator(planner, executor, budget)
        self.audit = LeanAuditOperator()

    def forward(self):
        self.workspace.run(storage=self.storage.step(), input_id_key="id", output_key="stage1")
        self.proof_repair.run(storage=self.storage.step(), input_key="stage1", output_key="stage2")
        self.audit.run(storage=self.storage.step(), input_stage1_key="stage1", input_stage2_key="stage2", output_key="audit")


def main():
    parser = argparse.ArgumentParser(description="Run the full M2F repair algorithm on a FATE-H shard")
    parser.add_argument("--input", type=Path, required=True, help="JSON/JSONL FATE-H shard")
    parser.add_argument("--fateh-root", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--provider", choices=("codex", "openai"), default="codex")
    parser.add_argument("--base-url")
    parser.add_argument("--model")
    parser.add_argument("--api-key-env", default="OPENAI_API_KEY")
    parser.add_argument("--reasoning-effort", default="high")
    parser.add_argument("--planning-rounds", type=int, default=21)
    parser.add_argument("--executor-attempts", type=int, default=10)
    args = parser.parse_args()
    if not DATAFLOW_AVAILABLE:
        raise SystemExit("Install open-dataflow: pip install -e '.[dataflow]'")
    if args.provider == "openai":
        if not args.base_url or not args.model:
            parser.error("--base-url and --model are required for the openai provider")
        provider = OpenAICompatibleProvider(args.base_url, args.model, args.api_key_env)
    else:
        provider = CodexCLIProvider(args.model, args.reasoning_effort)
    args.output_root.mkdir(parents=True, exist_ok=True)
    storage = FileStorage(str(args.input.resolve()), str(args.output_root.resolve()), "fateh_m2f", "jsonl")
    pipeline = FATEHM2FPipeline(storage, fateh_root=str(args.fateh_root),
                               work_root=str(args.output_root / "workspaces"), planner=provider, executor=provider,
                               budget=Budget(args.planning_rounds, args.executor_attempts))
    pipeline.compile()
    pipeline.forward()
    result_path = args.output_root / "fateh_m2f_step3.jsonl"
    rows = [json.loads(line) for line in result_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    metrics = aggregate_m2f(rows)
    (args.output_root / "metrics.json").write_text(json.dumps(metrics, ensure_ascii=False, indent=2) + "\n",
                                                    encoding="utf-8")
    print(json.dumps(metrics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
