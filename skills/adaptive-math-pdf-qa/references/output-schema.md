# Output schema

Canonical `dataset/environments.json` is an array of:

```json
{
  "id": "theorem:2.5",
  "kind": "theorem",
  "label": "2.5",
  "title": "Optional printed title",
  "statement": "Source-faithful Markdown/LaTeX",
  "supporting_text": "Source proof, derivation, answer, hint, or solution",
  "supporting_text_role": "proof",
  "statement_sources": [{"part":"0","block_id":42,"pdf_page":17,"bbox":[0,0,0,0]}],
  "supporting_sources": [],
  "source_pdf_page": 17,
  "candidate_locations": [["0", 3]],
  "assets": [{"image":"assets/theorem_2.5_1.png","caption":"...","pdf_page":17}],
  "glossary_context": [{"id":"glossary:abc123", "symbol":"A(x)", "meaning":"...", "source_pdf_page":13}],
  "uncertain_spans": []
}
```

Rules:

- `id = kind:label`; different kinds may share a label.
- `statement` is always nonempty and contains the printed object heading when present.
- `supporting_text` is empty unless present in the source.
- `supporting_text_role` is one of `proof`, `derivation`, `explanation`, `answer`, `hint`,
  `solution`, `hint_or_solution`, or empty.
- Sources preserve traceability; final prose contains no local file path.
- `glossary_context` is optional source material from a printed global notation table. It supplements
  rather than rewrites `statement`; the complete table is saved as `dataset/symbol_glossary.json`.
  Despite the field name, `symbol` may contain a printed term or abbreviation when the source table
  defines prose terminology rather than mathematical notation. `matched_by` records whether the
  entry came from the table's applicability labels, a conservative statement-symbol match, or both.
- `qa_pairs.json` maps `statement → question` and `supporting_text → answer` without synthesizing
  either side.

The dataset directory also contains JSONL, Markdown, per-kind JSON, transcription audit,
segmentation summary, run summary, and deterministic `quality_report.json`.
