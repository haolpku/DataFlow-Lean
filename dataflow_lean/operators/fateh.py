"""FATE-H adapter: turn benchmark rows into frozen M2F Stage-2 projects."""

from __future__ import annotations

import shutil
from pathlib import Path

from ..framework import OPERATOR_REGISTRY, OperatorABC
from .common import map_rows
from .stage1 import lean_signatures


@OPERATOR_REGISTRY.register()
class FATEHWorkspaceOperator(OperatorABC):
    def __init__(self, fateh_root: str, work_root: str):
        self.fateh_root, self.work_root = Path(fateh_root).resolve(), Path(work_root).resolve()

    def run(self, storage, input_id_key: str = "id", output_key: str = "stage1"):
        def transform(row):
            problem_id = int(row[input_id_key])
            project = self.work_root / str(problem_id)
            target_dir = project / "FATEH"
            target_dir.mkdir(parents=True, exist_ok=True)
            for name in ("lakefile.lean", "lake-manifest.json", "lean-toolchain"):
                shutil.copy2(self.fateh_root / name, project / name)
            shutil.copy2(self.fateh_root / "FATEH" / f"{problem_id}.lean", target_dir / f"{problem_id}.lean")
            # The benchmark root imports all 100 files.  An isolated shard must
            # expose a root module importing only the copied task.
            (project / "FATEH.lean").write_text(f'import FATEH.«{problem_id}»\n', encoding="utf-8")
            packages = project / ".lake/packages"
            source_packages = self.fateh_root / ".lake/packages"
            packages.parent.mkdir(exist_ok=True)
            if source_packages.exists() and not packages.exists():
                packages.symlink_to(source_packages, target_is_directory=True)
            signatures = lean_signatures(project)
            return {output_key: {
                "project_root": str(project), "compiled": True, "signatures": signatures,
                "provenance": {str(problem_id): {"benchmark": "FATE-H", "source": row.get("source"),
                                                  "informal_statement": row.get("informal_statement")}},
                "trace": [{"action": "FATEHWorkspace", "signatures": len(signatures)}],
            }}
        return map_rows(storage, transform)
