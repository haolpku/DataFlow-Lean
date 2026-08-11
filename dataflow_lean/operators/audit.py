"""Kernel build, escape-hatch scan, signature and provenance audit."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ..framework import OPERATOR_REGISTRY, OperatorABC
from ..lean import FORBIDDEN, LeanBackend, lean_code_only
from .common import map_rows
from .stage1 import lean_signatures, signatures_equal


@OPERATOR_REGISTRY.register()
class LeanAuditOperator(OperatorABC):
    def __init__(self, backend: LeanBackend | None = None):
        self.backend = backend or LeanBackend()

    def run(self, storage, input_stage1_key: str = "stage1", input_stage2_key: str = "stage2",
            output_key: str = "audit"):
        def transform(row):
            stage1, stage2 = row[input_stage1_key], row[input_stage2_key]
            project = Path(stage2["project_root"])
            findings = []
            for file in project.rglob("*.lean"):
                source = file.read_text(encoding="utf-8")
                code = lean_code_only(source)
                for name, pattern in FORBIDDEN.items():
                    for match in pattern.finditer(code):
                        findings.append({"kind": name, "path": str(file.relative_to(project)),
                                         "line": source.count("\n", 0, match.start()) + 1})
            build = self.backend.verify_project(project)
            current_signatures = lean_signatures(project)
            frozen = signatures_equal(current_signatures, stage1["signatures"])
            source_ids = set(stage1.get("provenance", {}))
            # Ask Lean itself for transitive axioms of every generated declaration.
            def module_name(path):
                parts = path.relative_to(project).with_suffix("").parts
                return ".".join(part if part.isidentifier() else f"«{part}»" for part in parts)

            imports = sorted({module_name(p)
                              for p in project.rglob("*.lean")
                              if ".lake" not in p.parts and p.name not in {"lakefile.lean", "M2FAxiomAudit.lean"}})
            declarations = [key.split("#", 1)[0] for key in current_signatures]
            axiom_file = project / "M2FAxiomAudit.lean"
            axiom_file.write_text("\n".join([*(f"import {x}" for x in imports), "",
                                               *(f"#print axioms {x}" for x in declarations)]) + "\n",
                                  encoding="utf-8")
            try:
                proc = subprocess.run(["lake", "env", "lean", axiom_file.name], cwd=project, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                      timeout=self.backend.timeout)
                axiom_output = proc.stdout[-30000:]
            finally:
                axiom_file.unlink(missing_ok=True)
            sorry_axiom = "sorryAx" in axiom_output
            return {output_key: {
                "passed": build.exit_code == 0 and build.holes == 0 and not findings and frozen
                          and proc.returncode == 0 and not sorry_axiom,
                "lake_build": build.asdict(), "escape_hatches": findings,
                "signatures_frozen": frozen, "source_coverage": len(source_ids),
                "provenance": stage1.get("provenance", {}),
                "axiom_audit": {"exit_code": proc.returncode, "contains_sorryAx": sorry_axiom,
                                "output": axiom_output},
            }}
        return map_rows(storage, transform)
