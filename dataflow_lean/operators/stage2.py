"""M2F Stage 2: plan/execute/query/retrieve/repair until holes are discharged."""

from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from ..control import VeriRefine
from ..framework import OPERATOR_REGISTRY, OperatorABC
from ..lean import LeanBackend, lean_code_only
from ..providers import LLMProvider
from ..schema import Budget, Objective
from .common import json_object, map_rows
from .stage1 import FILES_SCHEMA, apply_files, lean_signatures, signatures_equal


def holes(project: Path) -> list[dict]:
    result = []
    for file in project.rglob("*.lean"):
        source = file.read_text(encoding="utf-8")
        for match in re.finditer(r"\b(?:sorry|admit)\b", lean_code_only(source)):
            result.append({"path": str(file.relative_to(project)), "line": source.count("\n", 0, match.start()) + 1,
                           "offset": match.start()})
    return result


class MathlibRetriever:
    def search(self, project: Path, query: str, limit: int = 20) -> str:
        roots = [project / ".lake/packages/mathlib/Mathlib"]
        roots = [x for x in roots if x.exists()]
        if not roots:
            return "Mathlib source is not locally available."
        stop = {"theorem", "lemma", "have", "exact", "import", "Mathlib", "open", "Polynomial",
                "Show", "that", "sorry", "integrally", "closed"}
        terms = sorted({x for x in re.findall(r"[A-Za-z][A-Za-z0-9_]{3,}", query) if x not in stop},
                       key=lambda x: (-len(x), x))
        if not terms:
            return ""
        proc = subprocess.run(["rg", "-n", "-m", "2", "|".join(map(re.escape, terms[:8])), str(roots[0])],
                              text=True, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        return "\n".join(proc.stdout.splitlines()[:limit])


@OPERATOR_REGISTRY.register()
class ProofRepairOperator(OperatorABC):
    """Bounded Plan/Replan × ProposeProofPatch executor with strict rollback."""

    def __init__(self, planner: LLMProvider, executor: LLMProvider, budget: Budget | None = None,
                 backend: LeanBackend | None = None, retriever: MathlibRetriever | None = None):
        self.planner, self.executor = planner, executor
        self.budget, self.backend = budget or Budget(), backend or LeanBackend()
        self.retriever = retriever or MathlibRetriever()

    def run(self, storage, input_key: str = "stage1", output_key: str = "stage2"):
        def transform(row):
            calls_before = self.backend.calls
            stage1 = row[input_key]
            project = Path(stage1["project_root"])
            frozen = stage1["signatures"]
            module_files = [str(p.relative_to(project)) for p in project.rglob("*.lean")
                            if ".lake" not in p.parts and p.name not in {"lakefile.lean"}
                            and not (p.parent == project and any(project.glob(f"{p.stem}/**/*.lean")))]
            current = self.backend.verify_files(project, module_files)
            trace, prior_plan = [], ""
            refine = VeriRefine(lambda v: Objective(v.global_errors, v.holes))
            for planning_round in range(1, self.budget.planning_rounds + 1):
                inventory = holes(project)
                if not inventory and current.exit_code == 0:
                    break
                focus = inventory[0] if inventory else {"path": "", "line": 1}
                target = project / focus["path"] if focus["path"] else None
                source = target.read_text(encoding="utf-8") if target else ""
                goal = self.backend.goal_state(project, focus["path"], focus["line"]) if target else current.output
                retrieval = self.retriever.search(project, source[max(0, focus.get("offset", 0)-1500):focus.get("offset", 0)+1500])
                plan_response = self.planner.generate(
                    "You are M2F Plan/Replan. Give a concrete Lean proof plan, relevant lemmas, intermediate claims, "
                    "and anticipated API/typeclass failures. Do not edit code and do not run shell, tools, or Lean; "
                    "all authorized query results are already supplied below.",
                    f"Hole: {focus}\nGoal/diagnostics:\n{goal}\nMathlib retrieval:\n{retrieval}\nPrior plan:\n{prior_plan}\nSource:\n{source}")
                prior_plan = plan_response.text
                for executor_attempt in range(1, self.budget.executor_attempts + 1):
                    patch_response = self.executor.generate(
                        "You are M2F ProposeProofPatch/FixCompileError. Return changed Lean files as JSON. "
                        "Do not change declaration signatures; do not add axioms, unsafe, sorry or admit. "
                        "Use diagnostics to make the smallest useful patch. Do not run shell, tools, or Lean; the "
                        "pipeline is the only verifier.",
                        f"Plan:\n{prior_plan}\nDiagnostics/goal:\n{current.output[-10000:]}\nRetrieval:\n{retrieval}\n"
                        "Files:\n" + json.dumps([{"path": str(p.relative_to(project)), "content": p.read_text(encoding="utf-8")}
                                                  for p in project.rglob("*.lean")], ensure_ascii=False),
                        schema=FILES_SCHEMA)
                    patch = json_object(patch_response.text)["files"]

                    def mutate():
                        apply_files(project, patch)
                        if not signatures_equal(lean_signatures(project), frozen):
                            raise ValueError("Stage 2 attempted to change a frozen declaration signature")

                    try:
                        candidate, accepted = refine.attempt(project, current, mutate,
                                                             lambda: self.backend.verify_files(project, module_files))
                    except ValueError as exc:
                        candidate, accepted = current, False
                        error = str(exc)
                    else:
                        error = None
                    current = candidate
                    trace.append({"planning_round": planning_round, "executor_attempt": executor_attempt,
                                  "accepted": accepted, "error": error, "objective": {
                                      "global_errors": current.global_errors, "holes": current.holes},
                                  "planner_model": plan_response.model, "executor_model": patch_response.model,
                                  "planner_tokens": plan_response.prompt_tokens + plan_response.completion_tokens,
                                  "executor_tokens": patch_response.prompt_tokens + patch_response.completion_tokens,
                                  "focus": focus})
                    if current.success and current.holes == 0:
                        break
                if current.success and current.holes == 0:
                    break
            final = self.backend.verify_project(project)
            return {output_key: {"project_root": str(project), "success": final.success and final.holes == 0,
                                 "verification": final.asdict(), "remaining_holes": holes(project),
                                 "signatures_frozen": signatures_equal(lean_signatures(project), frozen),
                                 "trace": trace, "verifier_calls": self.backend.calls - calls_before,
                                 "planner_calls": len({x["planning_round"] for x in trace}),
                                 "executor_calls": len(trace),
                                 "tokens": sum(x["planner_tokens"] + x["executor_tokens"] for x in trace)}}
        return map_rows(storage, transform)
