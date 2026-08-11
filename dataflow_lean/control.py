"""Paper-faithful accept/revert loop used by both M2F stages."""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Callable

from .schema import Objective, Verification


class ProjectSnapshot:
    def __init__(self, project: Path):
        self.project = project
        self._tmp: Path | None = None

    def __enter__(self):
        self._tmp = Path(tempfile.mkdtemp(prefix="m2f-snapshot-"))
        shutil.copytree(self.project, self._tmp / "project", dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".lake", ".git"))
        return self

    def restore(self):
        assert self._tmp is not None
        for path in self.project.rglob("*.lean"):
            path.unlink()
        shutil.copytree(self._tmp / "project", self.project, dirs_exist_ok=True)

    def __exit__(self, exc_type, *_):
        if exc_type is not None:
            self.restore()
        if self._tmp:
            shutil.rmtree(self._tmp, ignore_errors=True)


class VeriRefine:
    """Commit a mutation iff its verifier objective strictly improves."""

    def __init__(self, objective: Callable[[Verification], Objective]):
        self.objective = objective

    def attempt(self, project: Path, baseline: Verification, mutate: Callable[[], None],
                verify: Callable[[], Verification]) -> tuple[Verification, bool]:
        with ProjectSnapshot(project) as snapshot:
            mutate()
            candidate = verify()
            accepted = self.objective(candidate) < self.objective(baseline)
            if not accepted:
                snapshot.restore()
            return (candidate if accepted else baseline), accepted
