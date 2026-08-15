# Book profile schema

The profile is the only book-specific control surface. Keep it evidence-based and short.

```json
{
  "schema_version": 1,
  "book_id": "stable-short-id",
  "title": "Printed title",
  "languages": ["en"],
  "include_unnumbered_named": false,
  "kinds": [
    "definition", "theorem", "lemma", "proposition", "corollary",
    "claim", "fact", "example", "exercise", "algorithm", "remark"
  ],
  "kind_aliases": {"problem": "exercise"},
  "label_regex": "(?:[0-9]+|[A-Z])(?:\\.[0-9]+)+",
  "numbering_scope": "book",
  "question_section_headings": ["Exercises", "Problems"],
  "support_section_headings": ["Hints", "Answers", "Solutions"],
  "distant_support_alignment": true,
  "preserve_markers": ["K", "KK", "KKK"],
  "worked_example_policy": "split_when_source_has_setup_and_resolution",
  "figure_policy": "only_explicit_dependencies",
  "segmentation_prompt_overlay": "Book-specific ID-selection rules only.",
  "visual_prompt_overlay": "Book-specific transcription rules only.",
  "direct_conflict_similarity": 0.94,
  "strip_terminal_proof_marks": false,
  "exclude_pdf_page_ranges": [],
  "merge_object_groups": [],
  "drop_object_ids": [],
  "inspection_notes": ["Facts observed from rendered pages."],
  "expected_counts": {}
}
```

## Decisions

- `include_unnumbered_named`: enable only when headings such as “Theorem” reliably delimit complete
  objects without labels. The engine assigns stable source labels such as `p17.b42`; synthetic
  labels cannot safely align distant support unless the support section prints another identifier.
- `numbering_scope`: use `book` when `(kind,label)` is globally unique; use `chapter` or `section`
  when labels restart. A scoped book needs a profile overlay telling segmentation to include the
  printed chapter/section prefix. Do not rely on source order to disambiguate repeated labels.
- `distant_support_alignment`: enable only if answer/proof labels use the same full label namespace.
- `worked_example_policy`: normally split visible setup/question from visible derivation/answer.
  Keep compact, inseparable explanatory examples entirely in `statement`.
- `expected_counts`: optional human/audited counts by kind. Use them as coverage alerts, not as a
  reason for the model to invent missing records.
- `direct_conflict_similarity`: normalized overlap similarity below which two transcriptions of the
  same ID require a targeted retry. Keep conservative; surface real disagreements.
- `strip_terminal_proof_marks`: enable only after visual inspection confirms a distinctive terminal
  proof glyph. The cleanup applies only at the end of supporting proof text.
- `exclude_pdf_page_ranges`: physical PDF page ranges known from visual inspection to contain only
  front matter, indexes, or other out-of-scope material. Do not exclude pages merely because native
  text extraction is empty.
- `merge_object_groups`: visually confirmed inventory fragments that form one inseparable source
  definition/example. Each entry has `target_id` and ordered `source_ids`; targeted retry transcribes
  one combined target and reconciliation removes the subordinate IDs.
- `drop_object_ids`: visually confirmed false-positive inventory objects, such as an introductory
  sentence mistaken for a standalone fact. These IDs are excluded from coverage, retry, and output.
  Use this only after checking the rendered source page.

## Visual inspection checklist

Inspect title/front matter, chapter starts, theorem-heavy pages, exercise sections, worked examples,
appendices, answer/hint sections, multi-column pages, algorithm boxes, pages with footnotes, and
cross-page environments. Record what is actually visible; do not infer a convention from one page.
