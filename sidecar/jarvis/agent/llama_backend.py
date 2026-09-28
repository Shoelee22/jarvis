"""llama.cpp-server backend for the Agent.

Implements the Agent's generate(messages, tools) contract against a local
llama-server.exe (OpenAI-compatible /v1/chat/completions with tools).

Never raises: on any failure returns {"text": <honest error>, "tool_calls": []}
so the agent loop degrades gracefully instead of 500ing.
"""
from __future__ import annotations
import json
import logging
import urllib.request

log = logging.getLogger(__name__)


def _to_json_schema(spec: dict) -> dict:
    """Normalize the registry's loose schema style to JSON Schema.

    Registry specs look like {"path": "string", "seconds": "int?"} where a
    trailing "?" marks optional. Convert to {"type":"object","properties":{...},
    "required":[...]}.
    """
    props: dict = {}
    required: list = []
    for name, typ in (spec or {}).items():
        optional = typ.endswith("?") if isinstance(typ, str) else False
        base = typ[:-1] if optional else typ
        js_type = {
            "string": "string", "int": "integer", "float": "number",
            "bool": "boolean", "list": "array", "dict": "object",
        }.get(base, "string")
        props[name] = {"type": js_type}
        if not optional:
            required.append(name)
    return {"type": "object", "properties": props, "required": required,
            "additionalProperties": True}


class LlamaServerLLM:
    """generate() via local llama-server's OpenAI-compatible endpoint."""

    def __init__(self, port: int = 8823, timeout: int = 120):
        self.port = port
        self.timeout = timeout

    def _url(self, path: str) -> str:
        return f"http://127.0.0.1:{self.port}{path}"

    def generate(self, messages, tools):
        try:
            return self._generate(messages, tools)
        except Exception as e:
            log.warning("brain generate failed: %s", e)
            return {"thought": "brain error",
                    "tool_calls": [],
                    "text": f"My local brain stumbled just now ({type(e).__name__}). "
                            f"Shall I try again, sir?"}

    def _generate(self, messages, tools):
        # llama-server expects OpenAI chat format.
        oai_messages = []
        for m in messages or []:
            role = m.get("role", "user")
            content = m.get("content", "")
            if not isinstance(content, str):
                content = json.dumps(content)
            oai_messages.append({"role": role, "content": content})

        oai_tools = []
        for t in tools or []:
            # tools entries: {"name":..., "description":..., "parameters":{...}}
            name = t.get("name", "")
            if not name:
                continue
            oai_tools.append({
                "type": "function",
                "function": {
                    "name": name,
                    "description": (t.get("description") or "")[:500],
                    "parameters": _to_json_schema(t.get("parameters") or {}),
                },
            })

        payload: dict = {
            "messages": oai_messages,
            "temperature": 0.7,
            "max_tokens": 1024,
        }
        if oai_tools:
            payload["tools"] = oai_tools
            payload["tool_choice"] = "auto"

        req = urllib.request.Request(
            self._url("/v1/chat/completions"),
            data=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            body = json.loads(resp.read().decode())

        choice = (body.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        text = msg.get("content") or ""
        tool_calls = []
        for tc in msg.get("tool_calls") or []:
            fn = (tc.get("function") or {})
            fname = fn.get("name", "")
            if not fname:
                continue
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except Exception:
                args = {}
            tool_calls.append({"name": fname, "args": args})

        return {"thought": "", "tool_calls": tool_calls, "text": text or ""}
