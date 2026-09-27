"""Tool runtime: registry + invocation with policy + audit. MCP-compatible shapes."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Callable

from ..agent.policy import PolicyEngine
from ..agent.audit import AuditLog


@dataclass
class Tool:
    name: str
    description: str
    schema: dict
    handler: Callable[[dict], dict]
    risk: str = "low"
    needs_network: bool = False


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)
    dynamic: object = None  # Phase 7: DynamicRegistry fallback for dotted names

    def register(self, tool: Tool):
        self.tools[tool.name] = tool

    def spec_list(self) -> list[dict]:
        specs = [{"name": t.name, "description": t.description, "risk": t.risk,
                  "needs_network": t.needs_network} for t in self.tools.values()]
        if self.dynamic is not None:
            for s in self.dynamic.spec_list():
                if "name" in s:
                    specs.append(s)
                else:
                    # Namespace summary -> shape-compatible pointer entry so the
                    # agent discovers the dynamic space without 168k entries.
                    ns = s.get("namespace", "?")
                    samples = ", ".join(s.get("sample_names", [])[:3])
                    specs.append({
                        "name": f"{ns}.*",
                        "description": (f"{s.get('addressable_count', 0)} dynamic tools "
                                        f"(e.g. {samples}). Call a concrete dotted name."),
                        "risk": "low", "needs_network": False,
                        "dynamic_namespace": True})
        return specs

    def call(self, name: str, args: dict, actor: str = "agent",
             audit: AuditLog | None = None, confirmed: bool = False) -> dict:
        if name not in self.tools and self.dynamic is not None:
            return self.dynamic.call(name, args or {}, actor=actor, audit=audit,
                                      confirmed=confirmed)
        policy = PolicyEngine()
        verdict = policy.decide(name, args)
        if verdict["action"] == "deny":
            result = {"ok": False, "error": verdict["reason"]}
        elif verdict["action"] == "confirm" and not confirmed:
            result = {"ok": False, "needs_confirmation": True,
                      "confirm_text": policy.confirm_text(name, args)}
        elif name not in self.tools:
            result = {"ok": False, "error": f"tool '{name}' not registered"}
        else:
            try:
                out = self.tools[name].handler(args or {})
                result = {"ok": True, "result": out}
            except Exception as e:  # never let a tool crash the loop
                result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
        if audit:
            summary = (result.get("error") or str(result.get("result")) or
                       ("awaiting confirmation" if result.get("needs_confirmation") else "ok"))
            audit.record(actor, name, args, summary, verdict["risk"])
        # Wrap tool output: downstream treats it as UNTRUSTED DATA, never instructions.
        if result.get("ok"):
            result["untrusted"] = True
        return result
