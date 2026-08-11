# DataFlow-Lean

DataFlow-Lean is a verifier-in-the-loop Lean 4 data-generation and evaluation
pipeline. Its first runnable pipeline is a paper-inspired reimplementation of
the M2F proof-repair stage evaluated on FATE-H.

The repository follows the packaging and production layout used by
[DataFlow-MemTensor](https://github.com/haolpku/DataFlow-MemTensor), while
keeping the core benchmark runner dependency-light. OpenDCAI DataFlow is an
optional dependency for upstream PDF/VQA book extraction.

## Pipeline

```text
FATE-H inventory
  -> isolated Lean workspace
  -> structured proof proposal
  -> independent Lean verifier
  -> compiler diagnostics
  -> repair (up to N attempts)
  -> JSONL checkpoints + pass@k / PSR metrics
```

A task passes only when:

- its theorem prefix through the first `:= by` is unchanged;
- the candidate contains no `sorry`, `admit`, new `axiom`, or `unsafe` escape hatch;
- `lake env lean FATEH/<id>.lean` exits successfully.

The proposer is read-only and cannot run Lean. The pipeline installs each
proposal and performs exactly one auditable verifier call per attempt.

## Install

Python 3.9+, Lean via `elan`, and the Codex CLI are required for a real run.

```bash
python -m pip install -e .
git clone https://github.com/frenzymath/FATE-H third_party/FATE-H
cd third_party/FATE-H
lake update
lake exe cache get
cd ../..
```

## Run

Small calibration:

```bash
dataflow-lean-fateh \
  --fateh-root third_party/FATE-H \
  --output-root artifacts/fateh_pilot \
  --ids 1,10,20 \
  --workers 1 \
  --attempts 3 \
  --reasoning-effort medium
```

Run the complete 100-task benchmark by omitting `--ids`:

```bash
dataflow-lean-fateh \
  --fateh-root third_party/FATE-H \
  --output-root artifacts/fateh_full100 \
  --workers 3 \
  --attempts 3 \
  --reasoning-effort medium
```

Completed tasks are checkpointed in `steps/10_results.jsonl`; rerunning the
same command resumes automatically. Use `--force` only when intentionally
discarding cached task results.

## Pilot result

The initial stratified 10-task run achieved pass@1 `10%`, pass@2 `50%`, and
pass@3 `60%`. See [the report](docs/FATEH_PILOT_20260811.md) and its
[machine-readable record](docs/FATEH_PILOT_20260811.json). This is a smoke-test
result, not a full FATE-H score.

## Book extraction handoff

For book-to-Lean generation, use the official DataFlow optimized PDF/VQA
pipeline to produce normalized records, then pass fields such as `context`,
`content`, `proof`, and `dependencies` to a statement-generation stage before
this verifier/repair pipeline. PDF extraction is intentionally kept separate
from FATE-H scoring so OCR/VLM quality does not contaminate the theorem-proving
metric.

DataFlow guide:
https://opendcai.github.io/DataFlow-Doc/zh/guide/vqa_extract_optimized/

## Tests

```bash
python -m pip install -r requirements-test.lock
python -m pytest -q
```
