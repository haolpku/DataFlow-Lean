---
name: adaptive-math-pdf-qa
description: Inspect mathematics textbook PDFs visually, adapt a per-book extraction profile, and extract definitions, theorems, lemmas, propositions, corollaries, conjectures, examples, exercises, remarks, algorithms, proofs, hints, solutions, and answers into source-faithful structured JSON. Use when building or auditing textbook-to-JSON/Q&A datasets. Default to the direct VLM route with lightweight inventory, high-resolution transcription, continuity checks, and gap retries; use the retained MinerU route only when the user explicitly requests MinerU.
---

# Adaptive Math PDF Q&A

Keep one extraction engine and a small evidence-based profile per book. Default to direct visual
parsing. Do not invoke MinerU merely because a PDF lacks a text layer.

## Required workflow

1. Locate the PDF and choose a project/output directory outside this Skill.
2. Run `scripts/init_project.py PDF PROJECT_DIR`. This writes `route: direct` unless the user
   explicitly requests `--route mineru`.
3. Run `scripts/book_inspector.py`. Open every image listed in `inspection/manifest.json` and inspect
   title/front matter, representative body pages, theorem-heavy pages, examples/exercises,
   appendices, answer/hint sections, and front/back-matter glossaries of terminology,
   abbreviations, notation, or symbols. Native PDF text is secondary evidence.
4. Read [profile-schema.md](references/profile-schema.md), then edit only `book_profile.json` to
   describe observed labels, unheaded objects, proof boundaries, distant support, figures, and
   high-risk glyphs. Apply the glossary gate below before excluding front/back matter. Preserve the
   all-environment taxonomy unless the user narrows it.
5. Run `scripts/preflight.py --config PROJECT_DIR/run.json --network`. A direct project requires
   only the configured vision-model credential. Never print or store secret values.
6. Read [direct-route.md](references/direct-route.md), then run:

   ```bash
   scripts/direct_pipeline.py run --config PROJECT_DIR/run.json
   ```

   The pipeline separately extracts configured glossary pages, checkpoints inventory and extraction
   windows, audits numbering, retries only missing/incomplete/conflicting targets, builds the
   dataset, and validates it. Resume normally;
   use `--force-stage` only for the narrow stage that changed.
7. Inspect `dataset/quality_report.json`, `direct/reconciliation/report.json`, and
   `direct/retry/summary.json`. Visually sample every kind, all uncertain/conflicting items,
   formula-heavy records, page-window boundaries, distant support, and statement-length outliers.

## Front/back-matter glossary gate

Before excluding a terminology, abbreviation, notation, or symbol table, decide whether every
extracted object remains understandable when read alone without that table. Treat the table as
required context if it defines book-specific terminology, non-universal conventions, overloaded
symbols, named sequences/functions, conjecture markers, or abbreviations needed to interpret a
statement. Do not assume a convention is universal merely because it is common in the subject.

If the table is unnecessary, record that conclusion in `inspection_notes`. If it is necessary:

- keep its physical pages in `glossary_page_ranges` even when they lie inside an excluded
  front/back-matter range;
- transcribe only printed meanings—never expand abbreviations or infer definitions from memory;
- save the complete table in `dataset/symbol_glossary.json` and attach only relevant entries as
  `glossary_context`; never rewrite or expand the source `statement` in place;
- prefer the table's printed problem/section applicability labels, then allow conservative literal
  statement matching only for distinctive terms or symbols;
- list overloaded or ambiguous forms in `glossary_content_match_disabled_symbols`, so a symbol such
  as `P(n)` is not assigned one article's meaning in another article;
- visually adjudicate conflicting glossary transcriptions with narrow `glossary_overrides`, keeping
  source page traceability and the match reason.

The `symbol` field may contain a printed term or abbreviation as well as mathematical notation; the
`meaning` field contains its source-faithful explanation.

## Direct-route rules

- Treat page images as authoritative. The inventory identifies objects and boundaries but never
  transcribes mathematics.
- Use high-resolution overlapping windows for source-faithful text. Include inventory targets and
  allow the extractor to report additional visible objects.
- Compare inventory IDs with extracted IDs. Retry only missing source objects, incomplete
  cross-window records, materially conflicting duplicates, and visible labels confirmed by a
  continuity gap scan.
- After reconciliation, scan same-page containment and suspicious short fragments. Put visually
  confirmed inseparable fragments in `merge_object_groups`; put visually confirmed false positives
  in `drop_object_ids`. Never auto-delete a similar example/exercise pair without checking whether
  the source intentionally gives the passage both roles.
- A numbering gap is an audit signal, never permission to invent an object. A gap retry must return
  nothing unless the relevant printed heading/cue is visible.
- Preserve formulas, hypotheses, quantifiers, subparts, equation tags, primes, stars, inequality
  directions, dimensions, printed order, and spelling. Undo only typographic line-break
  hyphenation unless the user explicitly requests conservative spelling repair.
- Keep source proofs/answers/hints only when printed. Never solve or generate missing support.
- Use deterministic cleanup only for book-confirmed layout artifacts, such as a proof-ending glyph
  at the very end of proof text. Never globally delete vertical bars or normalize ambiguous
  operators.
- Put book-specific glyph distinctions in `visual_prompt_overlay`. Describe local semantics when
  similar glyphs mean different operations; do not impose one LaTeX macro book-wide from shape
  alone.

Read [prompting.md](references/prompting.md) for unusual boxes, multi-column layouts, distant
answers, interleaved examples, or inline definitions.

## MinerU route: explicit opt-in only

Use MinerU only when the user explicitly asks for it. Initialize with `--route mineru`, configure a
cached/local/API backend, run `scripts/preflight.py`, then run:

```bash
scripts/pipeline.py run --config PROJECT_DIR/run.json
```

The retained `pipeline.py` is the legacy MinerU + segmentation + per-object visual transcription
engine. Prefer cached MinerU, then an explicitly configured local command, then the API. MinerU API
uses `model_version=vlm`. Never silently fall back from direct to MinerU.

## Authentication

- Configure environment-variable names, not values, in `run.json`.
- The VLM endpoint must be OpenAI-compatible and accept image input. Use the strongest available
  vision model by default.
- Direct mode requires only `vlm.api_key_env`; MinerU credentials are irrelevant.
- MinerU mode additionally requires its selected cached/local/API backend.
- If authentication is absent, stop before the affected remote stage. Do not search files for keys.

## Completion gate

The canonical output is `dataset/environments.json`; `dataset/qa_pairs.json` is the flattened view.
Read [output-schema.md](references/output-schema.md) before changing fields.

Require:

- no empty statements or duplicate IDs;
- no failed windows left unaudited;
- every inventory target extracted or explicitly listed as unresolved;
- no unresolved incomplete/conflicting record accepted silently;
- no absolute image paths in final prose;
- every proof/answer/hint traceable to source pages;
- saved inventory, continuity, retry, run-summary, and quality reports;
- visual sampling across every object kind and high-risk typography.

`uncertain_spans = 0` is not proof of perfect transcription. Report coverage as detected source
objects unless a separate audit establishes completeness.

## Script map

- `scripts/init_project.py`: create direct-default config and all-object profile.
- `scripts/book_inspector.py`: render representative pages and optionally draft a profile.
- `scripts/preflight.py`: route-aware dependency, credential, endpoint, and backend check.
- `scripts/direct_pipeline.py`: direct `inventory`, `extract`, `reconcile`, `retry`, `build`,
  `validate`, or `run` stages.
- `scripts/pipeline.py`: retained MinerU route; use only by explicit request.
- `scripts/validate_dataset.py`: deterministic schema and anomaly audit.

All scripts support `--help`; paths in `run.json` may be relative to that config.
