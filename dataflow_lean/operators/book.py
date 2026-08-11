"""DataFlow VQA handoff, atomic decomposition and Rethlas-style blueprint gate."""

from __future__ import annotations

import json
import re
from typing import Any

from ..framework import OPERATOR_REGISTRY, OperatorABC
from ..providers import LLMProvider
from .common import json_object, map_rows


@OPERATOR_REGISTRY.register()
class NormalizeBookOperator(OperatorABC):
    """Normalize optimized PDF/VQA output without discarding source provenance."""

    def run(self, storage, input_key: str = "vqa_pair", output_key: str = "document"):
        def transform(row):
            raw = row.get(input_key)
            if raw is None:
                raw = row.get("messages") or row.get("content") or row.get("context") or ""
            text = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
            return {output_key: {
                "text": text, "source_name": row.get("name") or row.get("source_name", "unknown"),
                "source_path": row.get("output_merged_md_path") or row.get("source_path"),
                "page": row.get("page"), "original": raw,
            }}
        return map_rows(storage, transform)


@OPERATOR_REGISTRY.register()
class AtomicMathBlockOperator(OperatorABC):
    """Extract ordered definitions/theorems/exercises and an explicit dependency DAG."""

    def __init__(self, llm: LLMProvider | None = None):
        self.llm = llm

    def run(self, storage, input_key: str = "document", output_key: str = "math_items"):
        def transform(row):
            document = row[input_key]
            text = document["text"]
            if self.llm:
                response = self.llm.generate(
                    "You extract an ordered mathematical document into atomic declarations.",
                    "Return JSON {items:[{id,kind,name,statement,proof,dependencies,source_span:{start,end}}]}. "
                    "Kinds are definition, theorem, lemma, example, exercise. Preserve quantifiers and hypotheses.\n\n" + text,
                )
                items = json_object(response.text)["items"]
            else:
                # Useful deterministic handoff for already-structured VQA records.
                statement = row.get("formal_statement") or row.get("informal_statement") or text
                items = [{"id": str(row.get("id", "item_0")), "kind": row.get("kind", "exercise"),
                          "name": row.get("name", "generated_item"), "statement": statement,
                          "proof": row.get("proof", ""), "dependencies": row.get("dependencies", []),
                          "source_span": {"start": 0, "end": len(text)}}]
            known = {str(item["id"]) for item in items}
            for index, item in enumerate(items):
                item["order"] = index
                item["dependencies"] = [str(x) for x in item.get("dependencies", []) if str(x) in known]
                item["provenance"] = {**document, "text": None, "span": item.get("source_span")}
            return {output_key: items}
        return map_rows(storage, transform)


@OPERATOR_REGISTRY.register()
class BlueprintOperator(OperatorABC):
    """Generate and independently review informal proof blueprints before Lean translation."""

    def __init__(self, generator: LLMProvider, verifier: LLMProvider | None = None):
        self.generator, self.verifier = generator, verifier

    def run(self, storage, input_key: str = "math_items", output_key: str = "blueprints"):
        def transform(row):
            output = []
            for item in row[input_key]:
                generated = self.generator.generate(
                    "Write a rigorous proof blueprint. Expose every nontrivial lemma and edge case.",
                    json.dumps(item, ensure_ascii=False),
                )
                review = {"verdict": "unreviewed", "feedback": ""}
                if self.verifier:
                    checked = self.verifier.generate(
                        "Independently verify the proof. Return JSON {verdict: correct|incorrect, feedback: string}.",
                        "Problem:\n" + item["statement"] + "\nBlueprint:\n" + generated.text,
                    )
                    review = json_object(checked.text)
                output.append({"id": item["id"], "blueprint": generated.text, "review": review,
                               "accepted": review["verdict"] in ("correct", "unreviewed"),
                               "generation_model": generated.model})
            return {output_key: output}
        return map_rows(storage, transform)

