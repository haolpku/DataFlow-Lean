from __future__ import annotations

import json
from typing import Any, Callable


def map_rows(storage, transform: Callable[[dict[str, Any]], dict[str, Any]]):
    rows = storage.read(output_type="dict")
    output = []
    for row in rows:
        merged = dict(row)
        merged.update(transform(dict(row)))
        output.append(merged)
    return storage.write(output)


def json_object(text: str) -> dict:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1].rsplit("```", 1)[0]
    value = json.loads(text)
    if not isinstance(value, dict):
        raise ValueError("model response must be a JSON object")
    return value

