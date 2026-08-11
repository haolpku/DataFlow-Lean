"""M2F Stage 1: document-level Lean statement compilation."""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from ..control import VeriRefine
from ..framework import OPERATOR_REGISTRY, OperatorABC
from ..lean import LeanBackend, lean_code_only, sha256
from ..providers import LLMProvider
from ..schema import Budget, Objective
from .common import json_object, map_rows

FILES_SCHEMA = {"type": "object", "properties": {"files": {"type": "array", "items": {
    "type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}},
    "required": ["path", "content"], "additionalProperties": False}}},
    "required": ["files"], "additionalProperties": False}


def lean_signatures(project: Path) -> dict[str, str]:
    signatures = {}
    command = re.compile(
        r"(?m)^[ \t]*(?:(namespace)[ \t]+([\w.']+)|(section)(?:[ \t]+[\w.']+)?|"
        r"(end)(?:[ \t]+[\w.']+)?|(?:theorem|lemma|def|abbrev|instance|structure|class)[ \t]+([\w.']+))"
    )
    for file in project.rglob("*.lean"):
        source, scopes = file.read_text(encoding="utf-8"), []
        matches = list(command.finditer(lean_code_only(source)))
        for index, match in enumerate(matches):
            if match.group(1):
                scopes.append(("namespace", match.group(2)))
                continue
            if match.group(3):
                scopes.append(("section", ""))
                continue
            if match.group(4):
                if scopes:
                    scopes.pop()
                continue
            name = match.group(5)
            qualified = ".".join([value for kind, value in scopes if kind == "namespace"] + [name])
            boundary = source.find(":=", match.end())
            next_command = matches[index + 1].start() if index + 1 < len(matches) else len(source)
            if boundary < 0 or boundary > next_command:
                continue
            signature = source[match.start():boundary].strip()
            key, suffix = qualified, 2
            while key in signatures:
                key, suffix = f"{qualified}#{suffix}", suffix + 1
            signatures[key] = sha256(signature)
    return signatures


def signatures_equal(left: dict[str, str], right: dict[str, str]) -> bool:
    """File moves/reordering are allowed; declaration text changes are not."""
    return Counter(left.values()) == Counter(right.values())


def apply_files(project: Path, files: list[dict]) -> None:
    for item in files:
        relative = Path(item["path"])
        if relative.is_absolute() or ".." in relative.parts or relative.suffix != ".lean":
            raise ValueError(f"unsafe Lean output path: {relative}")
        target = project / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(item["content"].rstrip() + "\n", encoding="utf-8")


@OPERATOR_REGISTRY.register()
class StatementCompilationOperator(OperatorABC):
    """GenSkeleton + diagnostic repair with Stage-1 accept/revert semantics."""

    def __init__(self, llm: LLMProvider, project_root: str, budget: Budget | None = None,
                 backend: LeanBackend | None = None, lean_version: str = "v4.28.0",
                 mathlib_revision: str = "v4.28.0"):
        self.llm, self.project_root = llm, Path(project_root)
        self.budget, self.backend = budget or Budget(), backend or LeanBackend()
        self.lean_version, self.mathlib_revision = lean_version, mathlib_revision

    def _ensure_project(self):
        self.project_root.mkdir(parents=True, exist_ok=True)
        toolchain = self.project_root / "lean-toolchain"
        if not toolchain.exists():
            toolchain.write_text(f"leanprover/lean4:{self.lean_version}\n", encoding="utf-8")
        lakefile = self.project_root / "lakefile.toml"
        if not lakefile.exists():
            lakefile.write_text(
                '[package]\nname = "GeneratedBook"\n\n'
                '[[require]]\nname = "mathlib"\ngit = "https://github.com/leanprover-community/mathlib4.git"\n'
                f'rev = "{self.mathlib_revision}"\n\n'
                '[[lean_lib]]\nname = "GeneratedBook"\n', encoding="utf-8")

    def run(self, storage, input_items_key: str = "math_items", input_blueprints_key: str = "blueprints",
            output_key: str = "stage1"):
        self._ensure_project()

        def transform(row):
            payload = {"items": row[input_items_key], "blueprints": row.get(input_blueprints_key, [])}
            response = self.llm.generate(
                "You are M2F Stage 1. Compile the ordered mathematical document into a coherent Lean 4 project. "
                "Create declaration skeletons with stable names and dependencies. `sorry` is allowed only in bodies. "
                "Never omit a source item. Return JSON files; paths must be under GeneratedBook/.",
                json.dumps(payload, ensure_ascii=False), schema=FILES_SCHEMA)
            files = json_object(response.text)["files"]
            apply_files(self.project_root, files)
            current = self.backend.verify_project(self.project_root)
            trace = [{"round": 0, "action": "GenSkeleton", "accepted": True,
                      "verification": current.asdict(), "model": response.model}]
            refine = VeriRefine(lambda v: Objective(v.global_errors, v.global_errors))
            for round_no in range(1, self.budget.statement_repairs + 1):
                if current.exit_code == 0:
                    break
                repair = self.llm.generate(
                    "You are M2F FixCompileError. Return only changed Lean files as JSON. Preserve all declaration "
                    "signatures and source coverage. Repair the localized compiler diagnostics; `sorry` bodies are allowed.",
                    "Diagnostics:\n" + current.output[-10000:] + "\nCurrent files:\n" + json.dumps([
                        {"path": str(p.relative_to(self.project_root)), "content": p.read_text(encoding="utf-8")}
                        for p in self.project_root.rglob("*.lean")], ensure_ascii=False), schema=FILES_SCHEMA)
                patch = json_object(repair.text)["files"]
                candidate, accepted = refine.attempt(
                    self.project_root, current, lambda: apply_files(self.project_root, patch),
                    lambda: self.backend.verify_project(self.project_root))
                current = candidate
                trace.append({"round": round_no, "action": "FixCompileError", "accepted": accepted,
                              "verification": current.asdict(), "model": repair.model})
            return {output_key: {"project_root": str(self.project_root), "compiled": current.exit_code == 0,
                                 "verification": current.asdict(), "signatures": lean_signatures(self.project_root),
                                 "trace": trace, "provenance": {str(x["id"]): x.get("provenance") for x in row[input_items_key]}}}
        return map_rows(storage, transform)


@OPERATOR_REGISTRY.register()
class StatementAlignmentOperator(OperatorABC):
    """Independent source↔Lean semantic check used to compute SCC/ARR."""

    def __init__(self, reviewer: LLMProvider):
        self.reviewer = reviewer

    def run(self, storage, input_items_key: str = "math_items", input_stage1_key: str = "stage1",
            output_key: str = "statement_alignment"):
        schema = {"type": "object", "properties": {"alignments": {"type": "array", "items": {
            "type": "object", "properties": {"id": {"type": "string"}, "matched": {"type": "boolean"},
                                                   "reason": {"type": "string"}},
            "required": ["id", "matched", "reason"], "additionalProperties": False}}},
            "required": ["alignments"], "additionalProperties": False}

        def transform(row):
            project = Path(row[input_stage1_key]["project_root"])
            files = [{"path": str(p.relative_to(project)), "content": p.read_text(encoding="utf-8")}
                     for p in project.rglob("*.lean")]
            response = self.reviewer.generate(
                "Independently compare every source mathematical item to its Lean declaration. Check quantifiers, "
                "hypotheses, types, direction and edge cases. Return one alignment per source id.",
                json.dumps({"items": row[input_items_key], "lean_files": files}, ensure_ascii=False), schema=schema)
            alignments = json_object(response.text)["alignments"]
            matched = sum(bool(x["matched"]) for x in alignments)
            total = len(row[input_items_key])
            return {output_key: {"alignments": alignments, "matched": matched, "total": total,
                                 "scc": matched / total if total else 0.0, "reviewer_model": response.model}}
        return map_rows(storage, transform)


@OPERATOR_REGISTRY.register()
class SplitLargeFilesOperator(OperatorABC):
    """Optional M2F SplitIfLargeAndResolve with signature/build rollback."""

    def __init__(self, llm: LLMProvider, max_lines: int = 2000, backend: LeanBackend | None = None):
        self.llm, self.max_lines, self.backend = llm, max_lines, backend or LeanBackend()

    def run(self, storage, input_key: str = "stage1", output_key: str = "stage1"):
        def transform(row):
            stage1 = dict(row[input_key])
            project = Path(stage1["project_root"])
            large = [p for p in project.rglob("*.lean")
                     if p.name != "lakefile.lean" and len(p.read_text(encoding="utf-8").splitlines()) > self.max_lines]
            if not large:
                stage1["split"] = {"needed": False, "accepted": False}
                return {output_key: stage1}
            before = lean_signatures(project)
            response = self.llm.generate(
                "You are M2F SplitIfLargeAndResolve. Split large Lean modules while preserving declaration text, "
                "order, namespaces and import reachability. Return JSON {files:[{path,content}], delete:[path]}. ",
                json.dumps({"large_files": [{"path": str(p.relative_to(project)),
                                              "content": p.read_text(encoding="utf-8")} for p in large]},
                           ensure_ascii=False))
            payload = json_object(response.text)
            from ..control import ProjectSnapshot
            accepted, diagnostic = False, ""
            with ProjectSnapshot(project) as snapshot:
                for raw in payload.get("delete", []):
                    path = Path(raw)
                    if path.is_absolute() or ".." in path.parts or path.suffix != ".lean":
                        raise ValueError(f"unsafe delete path: {path}")
                    (project / path).unlink(missing_ok=True)
                apply_files(project, payload["files"])
                verification = self.backend.verify_project(project)
                accepted = verification.exit_code == 0 and signatures_equal(lean_signatures(project), before)
                diagnostic = verification.output[-6000:]
                if not accepted:
                    snapshot.restore()
            stage1["split"] = {"needed": True, "accepted": accepted,
                               "files": [str(p.relative_to(project)) for p in large],
                               "diagnostic": diagnostic, "model": response.model}
            stage1["signatures"] = lean_signatures(project)
            return {output_key: stage1}
        return map_rows(storage, transform)
