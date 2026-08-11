"""Model-provider adapters. Credentials are read only from environment variables."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import urllib.request
import urllib.error
from pathlib import Path
from typing import Protocol

from .schema import LLMResponse


class LLMProvider(Protocol):
    def generate(self, system: str, prompt: str, *, schema: dict | None = None) -> LLMResponse: ...


class CodexCLIProvider:
    def __init__(self, model: str | None = None, reasoning_effort: str = "high", timeout: int = 1800):
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout

    def generate(self, system: str, prompt: str, *, schema: dict | None = None) -> LLMResponse:
        with tempfile.TemporaryDirectory(prefix="dataflow-lean-") as tmp:
            root = Path(tmp)
            output = root / "answer.txt"
            command = [
                "codex", "exec", "--ephemeral", "--skip-git-repo-check",
                "--ignore-user-config", "-s", "read-only", "-C", str(root),
                "-o", str(output), "-c", f'model_reasoning_effort="{self.reasoning_effort}"',
            ]
            if self.model:
                command += ["-m", self.model]
            if schema is not None:
                schema_path = root / "schema.json"
                schema_path.write_text(json.dumps(schema), encoding="utf-8")
                command += ["--output-schema", str(schema_path)]
            command.append(system + "\n\n" + prompt)
            proc = subprocess.run(command, text=True, stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, timeout=self.timeout)
            if proc.returncode != 0:
                raise RuntimeError(f"codex exited {proc.returncode}: {proc.stdout[-4000:]}")
            return LLMResponse(output.read_text(encoding="utf-8"), self.model or "codex-default",
                               raw={"trace": proc.stdout})


class OpenAICompatibleProvider:
    def __init__(self, base_url: str, model: str, api_key_env: str = "OPENAI_API_KEY", timeout: int = 1800):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self.api_key_env = api_key_env
        self.timeout = timeout

    def generate(self, system: str, prompt: str, *, schema: dict | None = None) -> LLMResponse:
        payload = {"model": self.model, "messages": [
            {"role": "system", "content": system}, {"role": "user", "content": prompt}
        ]}
        if schema is not None:
            payload["response_format"] = {"type": "json_schema", "json_schema": {
                "name": "answer", "strict": True, "schema": schema}}
        headers = {"Authorization": "Bearer " + os.environ[self.api_key_env], "Content-Type": "application/json"}

        def request_body(value):
            request = urllib.request.Request(self.url, json.dumps(value).encode(), headers)
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return json.load(response)
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", "replace")[-4000:]
                error = RuntimeError(f"model gateway HTTP {exc.code}: {detail}")
                error.status = exc.code
                raise error from exc

        try:
            body = request_body(payload)
        except RuntimeError as exc:
            if schema is None or getattr(exc, "status", None) not in (400, 404, 422):
                raise
            # Some OpenAI-compatible gateways do not implement response_format.
            payload.pop("response_format", None)
            payload["messages"][-1]["content"] += "\nReturn JSON conforming to this schema:\n" + json.dumps(schema)
            body = request_body(payload)
        usage = body.get("usage", {})
        return LLMResponse(body["choices"][0]["message"]["content"], self.model,
                           usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0), body)


class ScriptedProvider:
    """Deterministic provider used by tests and offline pipeline demonstrations."""
    def __init__(self, answers: list[str]):
        self.answers = iter(answers)

    def generate(self, system: str, prompt: str, *, schema: dict | None = None) -> LLMResponse:
        return LLMResponse(next(self.answers), "scripted")
