"""Model-provider adapters. Credentials are read only from environment variables."""

from __future__ import annotations

import json
import os
import shutil
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


class OpenAIMathlibToolProvider:
    """OpenAI-compatible agent with a small, read-only Lean/Mathlib tool surface."""

    def __init__(self, base_url: str, model: str, project: Path,
                 reasoning_effort: str = "medium", api_key_env: str = "OPENAI_API_KEY",
                 timeout: int = 1800, max_tool_rounds: int = 12):
        self.url = base_url.rstrip("/") + "/chat/completions"
        self.model, self.project = model, project
        self.reasoning_effort = reasoning_effort
        self.api_key_env, self.timeout = api_key_env, timeout
        self.max_tool_rounds = max_tool_rounds
        self.mathlib = project / ".lake" / "packages" / "mathlib" / "Mathlib"

    @staticmethod
    def _tools(schema: dict | None) -> list[dict]:
        functions = [
            {"name": "mathlib_search", "description": "Search Mathlib source with a regex.",
             "parameters": {"type": "object", "properties": {"query": {"type": "string"}},
                            "required": ["query"], "additionalProperties": False}},
            {"name": "read_mathlib", "description": "Read a line range from a Mathlib .lean file.",
             "parameters": {"type": "object", "properties": {
                 "path": {"type": "string"}, "start": {"type": "integer"},
                 "end": {"type": "integer"}}, "required": ["path", "start", "end"],
                            "additionalProperties": False}},
            {"name": "lean_check", "description": "Compile a temporary Lean snippet in the target project.",
             "parameters": {"type": "object", "properties": {"code": {"type": "string"}},
                            "required": ["code"], "additionalProperties": False}},
        ]
        if schema is not None:
            functions.append({"name": "submit_answer", "description": "Submit the final structured answer.",
                              "parameters": schema})
        return [{"type": "function", "function": function} for function in functions]

    def _run_tool(self, name: str, value: dict) -> str:
        if name == "mathlib_search":
            rg = shutil.which("rg")
            if rg is None or not self.mathlib.exists():
                return "Mathlib search unavailable."
            proc = subprocess.run([rg, "-n", "-m", "40", "--", str(value["query"]), str(self.mathlib)],
                                  text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                  timeout=60)
            return proc.stdout[-20000:] or "No matches."
        if name == "read_mathlib":
            supplied = Path(str(value["path"]))
            path = (supplied if supplied.is_absolute() else self.mathlib / supplied).resolve()
            if not path.is_relative_to(self.mathlib.resolve()) or path.suffix != ".lean" or not path.is_file():
                return "Rejected path: only Mathlib .lean files may be read."
            start, end = max(1, int(value["start"])), min(int(value["end"]), int(value["start"]) + 400)
            lines = path.read_text(encoding="utf-8").splitlines()
            return "\n".join(f"{i}: {lines[i - 1]}" for i in range(start, min(end, len(lines)) + 1))
        if name == "lean_check":
            with tempfile.NamedTemporaryFile("w", suffix=".lean", prefix="M2FToolQuery-",
                                             dir=self.project, encoding="utf-8", delete=False) as stream:
                stream.write(str(value["code"]))
                query = Path(stream.name)
            try:
                proc = subprocess.run(["lake", "env", "lean", query.name], cwd=self.project, text=True,
                                      stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=180)
                return f"exit_code={proc.returncode}\n{proc.stdout[-20000:]}"
            finally:
                query.unlink(missing_ok=True)
        return "Unknown or unavailable tool."

    def generate(self, system: str, prompt: str, *, schema: dict | None = None) -> LLMResponse:
        tools = self._tools(schema)
        messages: list[dict] = [{"role": "system", "content": system + (
            "\nUse the controlled tools iteratively to verify exact Mathlib APIs instead of guessing names. "
            "Do not ask for shell access. " +
            ("When finished, call submit_answer exactly once." if schema is not None
             else "When finished, return the plan as text."))},
                                {"role": "user", "content": prompt}]
        totals = {"prompt_tokens": 0, "completion_tokens": 0}
        last_body: dict = {}
        headers = {"Authorization": "Bearer " + os.environ[self.api_key_env],
                   "Content-Type": "application/json"}
        forced = False
        for tool_round in range(self.max_tool_rounds + 1):
            final_round = tool_round == self.max_tool_rounds
            available_tools = tools
            tool_choice: str | dict = "auto"
            if final_round:
                forced = True
                if schema is not None:
                    available_tools = [tool for tool in tools
                                       if tool["function"]["name"] == "submit_answer"]
                    tool_choice = "required"
                    messages.append({"role": "user", "content":
                                     "Tool budget exhausted. Submit your best complete answer now."})
                else:
                    available_tools = []
                    messages.append({"role": "user", "content":
                                     "Tool budget exhausted. Return the best concrete plan now."})
            payload = {"model": self.model, "max_completion_tokens": 16000,
                       "reasoning_effort": self.reasoning_effort,
                       "messages": messages, "tools": available_tools,
                       "tool_choice": tool_choice}
            request = urllib.request.Request(self.url, json.dumps(payload).encode(), headers)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                last_body = json.load(response)
            usage = last_body.get("usage", {})
            for key in totals:
                totals[key] += usage.get(key, 0)
            message = last_body["choices"][0]["message"]
            calls = message.get("tool_calls", [])
            for call in calls:
                if call["function"]["name"] == "submit_answer":
                    value = json.loads(call["function"]["arguments"])
                    return LLMResponse(json.dumps(value, ensure_ascii=False), self.model,
                                       totals["prompt_tokens"], totals["completion_tokens"],
                                       {**last_body, "m2f_tool_rounds": tool_round,
                                        "m2f_forced_submission": forced})
            if not calls:
                return LLMResponse(message.get("content", ""), self.model,
                                   totals["prompt_tokens"], totals["completion_tokens"],
                                   {**last_body, "m2f_tool_rounds": tool_round,
                                    "m2f_forced_submission": forced})
            messages.append(message)
            for call in calls:
                function = call["function"]
                try:
                    value = json.loads(function.get("arguments", "{}"))
                    result = self._run_tool(function["name"], value)
                except Exception as exc:
                    result = f"Tool error: {type(exc).__name__}: {exc}"
                messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
        raise RuntimeError("OpenAI Mathlib agent exceeded its tool-round budget")


class OpenAIResponsesMathlibToolProvider(OpenAIMathlibToolProvider):
    """Responses API variant using stateless function-call continuation."""

    def __init__(self, base_url: str, model: str, project: Path,
                 reasoning_effort: str = "medium", api_key_env: str = "OPENAI_API_KEY",
                 timeout: int = 1800, max_tool_rounds: int = 12):
        super().__init__(base_url, model, project, reasoning_effort, api_key_env,
                         timeout, max_tool_rounds)
        self.url = base_url.rstrip("/") + "/responses"

    @classmethod
    def _responses_tools(cls, schema: dict | None) -> list[dict]:
        return [{"type": "function", **tool["function"], "strict": True}
                for tool in cls._tools(schema)]

    def generate(self, system: str, prompt: str, *, schema: dict | None = None) -> LLMResponse:
        tools = self._responses_tools(schema)
        instructions = system + (
            "\nUse the controlled tools iteratively to verify exact Mathlib APIs instead of guessing names. "
            "Do not ask for shell access. " +
            ("When finished, call submit_answer exactly once." if schema is not None
             else "When finished, return the plan as text."))
        input_items: list[dict] = [
            {"role": "user", "content": [{"type": "input_text", "text": prompt}]}
        ]
        totals = {"input_tokens": 0, "output_tokens": 0}
        last_body: dict = {}
        headers = {"Authorization": "Bearer " + os.environ[self.api_key_env],
                   "Content-Type": "application/json"}
        forced = False
        for tool_round in range(self.max_tool_rounds + 1):
            final_round = tool_round == self.max_tool_rounds
            available_tools = tools
            tool_choice = "auto"
            if final_round:
                forced = True
                if schema is not None:
                    available_tools = [tool for tool in tools if tool["name"] == "submit_answer"]
                    tool_choice = "required"
                    input_items.append({"role": "user", "content": [{"type": "input_text",
                        "text": "Tool budget exhausted. Submit your best complete answer now."}]})
                else:
                    available_tools = []
                    input_items.append({"role": "user", "content": [{"type": "input_text",
                        "text": "Tool budget exhausted. Return the best concrete plan now."}]})
            payload = {"model": self.model, "instructions": instructions, "input": input_items,
                       "reasoning": {"effort": self.reasoning_effort}, "tools": available_tools,
                       "tool_choice": tool_choice, "max_output_tokens": 16000, "store": False}
            request = urllib.request.Request(self.url, json.dumps(payload).encode(), headers)
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                last_body = json.load(response)
            if last_body.get("error"):
                raise RuntimeError(f"Responses API error: {last_body['error']}")
            usage = last_body.get("usage", {})
            for key in totals:
                totals[key] += usage.get(key, 0)
            calls = [item for item in last_body.get("output", [])
                     if item.get("type") == "function_call"]
            for call in calls:
                if call["name"] == "submit_answer":
                    value = json.loads(call["arguments"])
                    return LLMResponse(json.dumps(value, ensure_ascii=False), self.model,
                                       totals["input_tokens"], totals["output_tokens"],
                                       {**last_body, "m2f_tool_rounds": tool_round,
                                        "m2f_forced_submission": forced})
            if not calls:
                texts = [part.get("text", "") for item in last_body.get("output", [])
                         if item.get("type") == "message" for part in item.get("content", [])
                         if part.get("type") == "output_text"]
                return LLMResponse("\n".join(texts), self.model,
                                   totals["input_tokens"], totals["output_tokens"],
                                   {**last_body, "m2f_tool_rounds": tool_round,
                                    "m2f_forced_submission": forced})
            for call in calls:
                input_items.append(call)
                try:
                    value = json.loads(call.get("arguments", "{}"))
                    result = self._run_tool(call["name"], value)
                except Exception as exc:
                    result = f"Tool error: {type(exc).__name__}: {exc}"
                input_items.append({"type": "function_call_output", "call_id": call["call_id"],
                                    "output": result})
        raise RuntimeError("Responses Mathlib agent exceeded its tool-round budget")


class ScriptedProvider:
    """Deterministic provider used by tests and offline pipeline demonstrations."""
    def __init__(self, answers: list[str]):
        self.answers = iter(answers)

    def generate(self, system: str, prompt: str, *, schema: dict | None = None) -> LLMResponse:
        return LLMResponse(next(self.answers), "scripted")
