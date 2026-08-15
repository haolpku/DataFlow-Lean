# DataFlow-Lean

DataFlow-Lean is a DataFlow-native reimplementation of the complete M2F
document-to-Lean algorithm. It keeps OpenDCAI DataFlow's optimized book
extraction intact and adds the formalization control plane after its VQA output.

## Complete graph

```text
Official DataFlow optimized PDF/VQA extraction
  PDF merge → Flash-MinerU → layout conversion → chunked VQA → parse/merge
  ↓ vqa_pair + messages + image/source paths
NormalizeBook → AtomicMathBlock + dependency DAG + provenance
  ↓
Blueprint generation → independent blueprint verification
  ↓
M2F Stage 1
  GenSkeleton → VerifyProj → localized FixCompileError → accept/revert
  ↓ frozen declaration signatures + independent statement alignment (SCC)
M2F Stage 2
  hole inventory → Plan/Replan → GoalState + Mathlib retrieval
  → ProposeProofPatch/FixCompileError → VerifyProj → accept/revert
  ↓
lake build → sorry/admit/axiom/unsafe scan → #print axioms → provenance audit
```

The iterative stages use the paper's lexicographic VeriRefine objectives:

- Stage 1: `(global compile errors, localized compile errors)`;
- Stage 2: `(global compile errors, remaining holes)`.

A rejected patch is physically rolled back. Stage 2 also hashes all declaration
signatures and rejects any proof patch that changes them. Default upper bounds
match M2F: 21 planning rounds and 10 executor attempts per plan.

## Why book extraction is not reimplemented

`OptimizedPDFBookToLeanPipeline` accepts the exact operator instances from
DataFlow's
[optimized VQA extraction recipe](https://opendcai.github.io/DataFlow-Doc/zh/guide/vqa_extract_optimized/):
`PDF_Merger`, a configured `FileOrURLToMarkdownConverterFlash`,
`MinerU2LLMInputOperator`, `ChunkedPromptedGenerator`, `LLMOutputParser`,
`QA_Merger`, and `VQAFormatter`. Its first seven steps are the upstream graph;
the M2F operators then continue on the same `FileStorage`.

If extraction has already completed, use `M2FFromExtractedPipeline` on the
resulting JSONL. The adapter consumes `vqa_pair`, while retaining `messages`,
image paths, merged Markdown paths, page information, and original text as
provenance.

```python
from dataflow.utils.storage import FileStorage
from dataflow_lean.pipelines import M2FFromExtractedPipeline
from dataflow_lean.providers import OpenAICompatibleProvider

storage = FileStorage(
    first_entry_file_name="cache/vqa_extract_output.jsonl",
    cache_path="artifacts/book_run",
    file_name_prefix="m2f",
    cache_type="jsonl",
)
generator = OpenAICompatibleProvider(
    base_url="http://127.0.0.1:4100/v1",
    model="your-generation-model",
    api_key_env="M2F_API_KEY",
)
reviewer = OpenAICompatibleProvider(
    base_url="http://127.0.0.1:4100/v1",
    model="your-independent-review-model",
    api_key_env="M2F_API_KEY",
)
pipeline = M2FFromExtractedPipeline(
    storage,
    blueprint=generator,
    blueprint_verifier=reviewer,
    stage1_model=generator,
    planner=generator,
    executor=generator,
    project_root="artifacts/book_project",
)
pipeline.compile()
pipeline.forward()
```

Secrets are only read from the named environment variable and are never stored
in prompts, source control, or result records.

## Optional direct PDF extraction skill

The reusable Codex skill in [`skills/adaptive-math-pdf-qa`](skills/adaptive-math-pdf-qa/SKILL.md)
provides an alternative front end for scanned mathematics books. It defaults to direct VLM page
parsing with a lightweight inventory, numbering-continuity checks, overlapping high-resolution
transcription, and targeted gap/conflict retries. The retained MinerU route is explicit opt-in.

The skill emits source-faithful environment JSON and a flattened QA view suitable for adaptation to
`M2FFromExtractedPipeline`; it does not alter the DataFlow-Lean control-plane implementation.

## FATE-H evaluation

FATE-H bypasses extraction and Stage 1 because its statements are already Lean.
`FATEHM2FPipeline` creates isolated projects, freezes each theorem signature,
and runs the complete Stage-2 planner/executor loop and audit as real registered
DataFlow operators.

```bash
python -m pip install -e '.[dataflow]'

dataflow-lean-m2f-fateh \
  --input artifacts/shards/fateh-00.jsonl \
  --fateh-root third_party/FATE-H \
  --output-root artifacts/fateh-00 \
  --provider codex \
  --model gpt-5.6-sol \
  --reasoning-effort high
```

For an OpenAI-compatible gateway use `--provider openai --base-url ... --model
... --api-key-env M2F_API_KEY`. Separate JSONL shards can run on shared-disk
machines without two workers writing the same DataFlow step file.

For the supplied shared cluster, first create two disjoint shards, then launch
one per reachable node (30100 and 30200). The helper fixes Lean 4.28 and uses
the local mihomo endpoint; each worker must have a distinct output directory.

```bash
python scripts/make_fateh_shards.py \
  /vepfs-mlp2/c20250602/500050/lh/lianghao/FATE-H/FATE-H.json \
  /vepfs-mlp2/c20250602/500050/lh/lianghao/fateh_shards --shards 2

# Run this on each node with shard 00 or 01 respectively.
M2F_API_KEY=... scripts/run_remote_shard.sh \
  /vepfs-mlp2/c20250602/500050/lh/lianghao/fateh_shards/fateh-00-of-02.jsonl \
  /vepfs-mlp2/c20250602/500050/lh/lianghao/fateh_runs/shard-00 \
  openai gpt-5.4 http://127.0.0.1:4200/v1
```

The 30300 endpoint was unreachable during setup, so the documented default is
two shards. Recreate three shards when that endpoint is restored.

The earlier three-shot whole-proof baseline remains available as
`dataflow-lean-fateh`; it is retained for comparison only. Its stratified
10-task smoke test scored pass@1 10%, pass@2 50%, pass@3 60%. That is not the
full M2F algorithm and is not a full FATE-H score.

## Metrics and audit artifacts

Each DataFlow row retains:

- ordered source items, dependency edges and exact source provenance;
- generated and independently reviewed blueprints;
- every Stage-1 and Stage-2 proposal, verifier objective and accept/revert bit;
- frozen signature hashes and semantic statement alignments for SCC/ARR;
- remaining holes, verifier/model calls, lake-build output and `#print axioms`;
- forbidden escape-hatch findings.

A theorem passes only when the project builds, no hole/escape hatch remains,
the Stage-1 signatures are unchanged, and the kernel axiom audit succeeds.

## Development

```bash
python -m pip install -r requirements-test.lock
python -m pytest -q
```

The lightweight import boundary lets pure control-plane tests run without the
large PDF extras. Production and integration runs use `open-dataflow>=1.0.10`;
install `.[pdf]` for the optimized PDF dependencies.
