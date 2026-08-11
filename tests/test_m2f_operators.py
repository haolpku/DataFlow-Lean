import json
from pathlib import Path

import pytest

from dataflow_lean.control import ProjectSnapshot, VeriRefine
from dataflow_lean.framework import OPERATOR_REGISTRY
from dataflow_lean.operators.book import NormalizeBookOperator
from dataflow_lean.operators.stage1 import lean_signatures
from dataflow_lean.operators.stage2 import ProofRepairOperator
from dataflow_lean.providers import ScriptedProvider
from dataflow_lean.schema import Budget, Objective, Verification
from dataflow_lean.lean import HOLE_RE, lean_code_only
from dataflow_lean.pipelines.fateh_m2f_pipeline import aggregate_m2f


class MemoryStorage:
    def __init__(self, rows):
        self.rows = rows

    def read(self, output_type="dict"):
        assert output_type == "dict"
        return self.rows

    def write(self, rows):
        self.rows = rows
        return "memory://next"


class FakeLeanBackend:
    timeout = 1

    def __init__(self):
        self.calls = 0

    def verify_project(self, project: Path):
        self.calls += 1
        source = next(project.rglob("*.lean")).read_text()
        holes = source.count("sorry")
        return Verification(holes == 0, 0, 0, holes, "", 0)

    def verify_files(self, project: Path, targets):
        return self.verify_project(project)

    def goal_state(self, project, target, line):
        return "⊢ True"


def test_real_operator_names_are_registered():
    for name in ("NormalizeBookOperator", "StatementCompilationOperator", "ProofRepairOperator",
                 "LeanAuditOperator", "FATEHWorkspaceOperator"):
        assert OPERATOR_REGISTRY.get(name).__name__ == name


def test_signature_inventory_uses_qualified_names(tmp_path):
    (tmp_path / "T.lean").write_text(
        "namespace Outer\nsection\ntheorem t : True := by trivial\nend\nend Outer\n")
    signatures = lean_signatures(tmp_path)
    assert list(signatures) == ["Outer.t"]


def test_lean_scanner_ignores_escape_words_in_nested_comments_and_strings():
    source = '/- sorry /- axiom hidden -/ admit -/\n-- unsafe theorem\n#check "sorry"\ntheorem t : True := by sorry\n'
    code = lean_code_only(source)
    assert len(HOLE_RE.findall(code)) == 1
    assert "axiom hidden" not in code


def test_normalizer_is_vqa_adapter_not_an_extractor():
    storage = MemoryStorage([{"name": "book", "vqa_pair": [{"q": "prove", "a": "proof"}],
                              "output_merged_md_path": "/tmp/book.md"}])
    NormalizeBookOperator().run(storage)
    document = storage.rows[0]["document"]
    assert document["source_path"] == "/tmp/book.md"
    assert "prove" in document["text"]
    assert document["original"][0]["a"] == "proof"


def test_snapshot_rolls_back_on_exception(tmp_path):
    file = tmp_path / "T.lean"
    file.write_text("theorem t : True := by sorry\n")
    with pytest.raises(ValueError):
        with ProjectSnapshot(tmp_path):
            file.write_text("theorem changed : False := by sorry\n")
            raise ValueError("reject")
    assert file.read_text() == "theorem t : True := by sorry\n"


def test_verirefine_requires_strict_lexicographic_improvement(tmp_path):
    baseline = Verification(False, 1, 2, 3, "", 0)
    same = Verification(False, 1, 2, 3, "", 0)
    result, accepted = VeriRefine(lambda v: Objective(v.global_errors, v.holes)).attempt(
        tmp_path, baseline, lambda: None, lambda: same)
    assert not accepted and result is baseline


def test_stage2_plan_patch_verify_and_signature_freeze(tmp_path):
    target = tmp_path / "Book" / "T.lean"
    target.parent.mkdir()
    target.write_text("theorem t : True := by\n  sorry\n")
    stage1 = {"project_root": str(tmp_path), "signatures": lean_signatures(tmp_path)}
    storage = MemoryStorage([{"stage1": stage1}])
    planner = ScriptedProvider(["Use True.intro."])
    executor = ScriptedProvider([json.dumps({"files": [{"path": "Book/T.lean",
                                                         "content": "theorem t : True := by\n  trivial"}]})])
    ProofRepairOperator(planner, executor, Budget(planning_rounds=1, executor_attempts=1),
                        FakeLeanBackend()).run(storage)
    assert storage.rows[0]["stage2"]["success"]
    assert storage.rows[0]["stage2"]["signatures_frozen"]
    assert "sorry" not in target.read_text()


def test_stage2_rejects_statement_change_and_restores(tmp_path):
    target = tmp_path / "T.lean"
    original = "theorem t : True := by\n  sorry\n"
    target.write_text(original)
    storage = MemoryStorage([{"stage1": {"project_root": str(tmp_path),
                                          "signatures": lean_signatures(tmp_path)}}])
    executor = ScriptedProvider([json.dumps({"files": [{"path": "T.lean",
                                                         "content": "theorem t : False := by\n  trivial"}]})])
    ProofRepairOperator(ScriptedProvider(["bad plan"]), executor,
                        Budget(planning_rounds=1, executor_attempts=1), FakeLeanBackend()).run(storage)
    assert target.read_text() == original
    assert not storage.rows[0]["stage2"]["success"]


def test_fateh_metrics_use_final_audit_gate():
    metrics = aggregate_m2f([
        {"id": 1, "audit": {"passed": True}, "stage2": {"verifier_calls": 3, "planner_calls": 1,
                                                               "executor_calls": 2, "tokens": 100}},
        {"id": 2, "audit": {"passed": False}, "stage2": {"verifier_calls": 4, "planner_calls": 2,
                                                                "executor_calls": 3, "tokens": 200}},
    ])
    assert metrics == {"attempted": 2, "solved": 1, "psr": 0.5, "failed_ids": [2],
                       "verifier_calls": 7, "planner_calls": 3, "executor_calls": 5, "tokens": 300}
