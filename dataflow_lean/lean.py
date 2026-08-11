"""Lean process boundary, diagnostics, snapshots and deterministic audits."""

from __future__ import annotations

import hashlib
import re
import subprocess
import time
from pathlib import Path

from .schema import Verification

FORBIDDEN = {
    "sorry": re.compile(r"\bsorry\b"), "admit": re.compile(r"\badmit\b"),
    "axiom": re.compile(r"(?m)^\s*axiom\b"), "unsafe": re.compile(r"(?m)^\s*unsafe\b"),
}
ERROR_RE = re.compile(r"(?m)^.*\berror(?:\([^)]*\))?:")
HOLE_RE = re.compile(r"\b(?:sorry|admit)\b")


def lean_code_only(source: str) -> str:
    """Blank comments/string contents while preserving offsets and newlines."""
    output, index, block_depth, string, line_comment = [], 0, 0, False, False
    while index < len(source):
        pair = source[index:index + 2]
        char = source[index]
        if line_comment:
            if char == "\n":
                line_comment = False
                output.append(char)
            else:
                output.append(" ")
            index += 1
            continue
        if block_depth:
            if pair == "/-":
                block_depth += 1
                output.extend("  ")
                index += 2
            elif pair == "-/":
                block_depth -= 1
                output.extend("  ")
                index += 2
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue
        if string:
            if char == "\\" and index + 1 < len(source):
                output.extend("  ")
                index += 2
            elif char == '"':
                string = False
                output.append(char)
                index += 1
            else:
                output.append("\n" if char == "\n" else " ")
                index += 1
            continue
        if pair == "--":
            line_comment = True
            output.extend("  ")
            index += 2
        elif pair == "/-":
            block_depth = 1
            output.extend("  ")
            index += 2
        elif char == '"':
            string = True
            output.append(char)
            index += 1
        else:
            output.append(char)
            index += 1
    return "".join(output)


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class LeanBackend:
    def __init__(self, timeout: int = 600):
        self.timeout = timeout
        self.calls = 0

    def verify(self, project: Path, target: str, *, reject_holes: bool = False) -> Verification:
        source = (project / target).read_text(encoding="utf-8")
        code = lean_code_only(source)
        forbidden = [key for key, pattern in FORBIDDEN.items() if pattern.search(code)] if reject_holes else []
        started = time.monotonic()
        self.calls += 1
        proc = subprocess.run(["lake", "env", "lean", target], cwd=project, text=True,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=self.timeout)
        output = proc.stdout[-30000:]
        holes = sum(len(HOLE_RE.findall(lean_code_only(p.read_text(encoding="utf-8"))))
                    for p in project.rglob("*.lean") if ".lake" not in p.parts)
        return Verification(proc.returncode == 0 and not forbidden and (not reject_holes or holes == 0),
                            proc.returncode, len(ERROR_RE.findall(output)), holes, output,
                            round(time.monotonic() - started, 3), forbidden)

    def verify_files(self, project: Path, targets: list[str]) -> Verification:
        """M2F inner-loop VerifyFile, aggregating diagnostics and global hole count."""
        results = [self.verify(project, target) for target in targets]
        holes = sum(len(HOLE_RE.findall(lean_code_only(p.read_text(encoding="utf-8"))))
                    for p in project.rglob("*.lean") if ".lake" not in p.parts)
        return Verification(all(x.exit_code == 0 for x in results),
                            0 if all(x.exit_code == 0 for x in results) else 1,
                            sum(x.global_errors for x in results), holes,
                            "\n".join(x.output for x in results)[-30000:],
                            round(sum(x.seconds for x in results), 3),
                            sorted({item for x in results for item in x.forbidden}))

    def verify_project(self, project: Path) -> Verification:
        started = time.monotonic()
        self.calls += 1
        proc = subprocess.run(["lake", "build"], cwd=project, text=True, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, timeout=self.timeout)
        output = proc.stdout[-30000:]
        holes = sum(len(HOLE_RE.findall(lean_code_only(p.read_text(encoding="utf-8"))))
                    for p in project.rglob("*.lean"))
        return Verification(proc.returncode == 0, proc.returncode, len(ERROR_RE.findall(output)), holes,
                            output, round(time.monotonic() - started, 3))

    def goal_state(self, project: Path, target: str, line: int, column: int = 1) -> str:
        """Query a goal when Lean exposes `lake env lean --stdin`; diagnostics are a portable fallback."""
        result = self.verify(project, target)
        relevant = [x for x in result.output.splitlines() if f":{line}:" in x]
        return "\n".join(relevant[-30:]) or result.output[-6000:]


def declaration_prefix(source: str) -> str:
    marker = ":= by"
    if marker not in source:
        raise ValueError("Lean declaration has no ':= by' boundary")
    return source.split(marker, 1)[0] + marker
