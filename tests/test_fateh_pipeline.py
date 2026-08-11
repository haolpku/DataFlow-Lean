from dataflow_lean.pipelines.fateh_pipeline import (
    CodexProofRepair,
    FORBIDDEN,
    aggregate,
    error_count,
    parse_ids,
    statement_prefix,
)


def test_statement_prefix_freezes_first_theorem_boundary():
    source = "theorem t : True := by\n  let x : Nat := by exact 1\n  trivial\n"
    assert statement_prefix(source) == "theorem t : True := by"


def test_install_proof_dedents_model_output():
    source = "theorem t : True := by\n  sorry\n"
    proof = "    have h : True := by\n      trivial\n    exact h"
    assert CodexProofRepair.install_proof(source, proof) == (
        "theorem t : True := by\n"
        "  have h : True := by\n"
        "    trivial\n"
        "  exact h\n"
    )


def test_install_proof_removes_outer_by():
    source = "theorem t : True := by\n  sorry\n"
    assert CodexProofRepair.install_proof(source, "by\n  trivial") == (
        "theorem t : True := by\n  trivial\n"
    )


def test_forbidden_escape_hatches():
    assert FORBIDDEN["sorry"].search("theorem t : True := by sorry")
    assert FORBIDDEN["admit"].search("by admit")
    assert FORBIDDEN["axiom"].search("axiom hidden : False")
    assert FORBIDDEN["unsafe"].search("unsafe theorem t : True")


def test_error_count_handles_tagged_and_plain_errors():
    output = "a.lean:1:1: error: bad\na.lean:2:2: error(lean.foo): worse\n"
    assert error_count(output) == 2


def test_parse_ids_supports_ranges():
    assert parse_ids("1,3-5,10") == {1, 3, 4, 5, 10}
    assert parse_ids(None) is None


def test_aggregate_reports_pass_at_k():
    rows = [
        {
            "id": 1,
            "success": True,
            "attempt_count": 2,
            "wall_seconds": 3,
            "attempts": [
                {"verifier_calls": 1, "verification": {"success": False, "lean_seconds": 1}},
                {"verifier_calls": 1, "verification": {"success": True, "lean_seconds": 1}},
            ],
        },
        {
            "id": 2,
            "success": False,
            "attempt_count": 2,
            "wall_seconds": 4,
            "attempts": [
                {"verifier_calls": 1, "verification": {"success": False, "lean_seconds": 1}},
                {"verifier_calls": 1, "verification": {"success": False, "lean_seconds": 1}},
            ],
        },
    ]
    metrics = aggregate(rows)
    assert metrics["psr"] == 0.5
    assert metrics["pass_at"] == {"1": 0.0, "2": 0.5}
    assert metrics["verifier_calls"] == 4
