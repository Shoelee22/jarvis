"""DynamicRegistry: lazy, namespace-routed tool registry (Phase 7).

Wraps Providers (see providers.py). Tool names are ``<namespace>.<local>``;
resolution splits on the first "." and delegates to the owning provider, which
builds exactly ONE Tool on demand -- nothing is materialized up front.

`call()` mirrors base.Registry.call's risk policy LOCALLY instead of using
PolicyEngine (which KeyErrors on unknown names):
  - risk "low"/"medium" -> allowed immediately
  - risk "high" -> requires confirmed=True, else returns needs_confirmation
Never raises. On success the result carries untrusted=True (tool output is
untrusted data, never instructions), matching base.Registry.call.
"""
from __future__ import annotations

from typing import Any

from ..base import Tool
from .providers import Provider

_LOW_RISK = ("low", "medium")  # allowed without confirmation


class DynamicRegistry:
    def __init__(self, main_registry=None, data_dir=None):
        self.main_registry = main_registry
        self.data_dir = data_dir
        self._providers: dict[str, Provider] = {}
        #: provider keys skipped at build time (module not landed yet etc.)
        self.skipped: list[str] = []

    # -- provider management ----------------------------------------------
    def register_provider(self, p: Provider) -> None:
        if not p.namespace:
            raise ValueError("provider has empty namespace")
        self._providers[p.namespace] = p

    def provider_namespaces(self) -> list[str]:
        return sorted(self._providers)

    # -- resolution ---------------------------------------------------------
    @staticmethod
    def _static_tools(p) -> list[Tool]:
        """Static tools of a provider, tolerant of partial implementations."""
        try:
            tools = p.static_tools()
        except (AttributeError, TypeError):
            return []
        return list(tools or [])

    def resolve(self, name: str) -> Tool | None:
        """Find the Tool for `name`: route by namespace, or scan static tools."""
        if "." in name:
            namespace = name.split(".", 1)[0]
            p = self._providers.get(namespace)
            if p is not None:
                for t in self._static_tools(p):
                    if t.name == name:
                        return t
                resolver = getattr(p, "resolve", None)
                if callable(resolver):
                    try:
                        return resolver(name)
                    except Exception:
                        return None
                return None
        # Fallback: scan every provider's static tools (covers undotted names
        # and any static tool whose namespace differs from its prefix).
        for p in self._providers.values():
            for t in self._static_tools(p):
                if t.name == name:
                    return t
        return None

    # -- invocation ---------------------------------------------------------
    def call(self, name: str, args: dict | None = None, actor: str = "agent",
             audit=None, confirmed: bool = False) -> dict:
        """Invoke a dynamic tool. Never raises.

        audit: optional object with .record(actor, name, args, summary, risk),
        mirroring the AuditLog protocol used by base.Registry.call.
        """
        args = args or {}
        try:
            tool = self.resolve(name)
            if tool is None:
                result: dict[str, Any] = {"ok": False,
                                          "error": f"tool '{name}' not registered"}
            elif tool.risk == "high" and not confirmed:
                result = {"ok": False, "needs_confirmation": True,
                          "confirm_text": (f"Tool '{name}' is high risk. "
                                           f"Confirm to proceed?")}
            elif tool.risk not in _LOW_RISK + ("high",):
                result = {"ok": False, "error":
                          f"tool '{name}' has unknown risk '{tool.risk}'"}
            else:
                try:
                    out = tool.handler(dict(args))
                    result = {"ok": True, "result": out}
                except Exception as e:  # never let a tool crash the loop
                    result = {"ok": False, "error": f"{type(e).__name__}: {e}"}
            if audit is not None:
                risk = tool.risk if tool is not None else "low"
                summary = (result.get("error") or str(result.get("result")) or
                           ("awaiting confirmation"
                            if result.get("needs_confirmation") else "ok"))
                try:
                    audit.record(actor, name, args, summary, risk)
                except Exception:
                    pass
            if result.get("ok"):
                result["untrusted"] = True
            return result
        except Exception as e:  # registry itself must never raise
            return {"ok": False, "error": f"{type(e).__name__}: {e}"}

    # -- introspection ------------------------------------------------------
    def _expand(self, p) -> int:
        try:
            e = p.expand()
        except Exception:
            return 0
        if isinstance(e, bool):
            return int(e)
        if isinstance(e, int):
            return e
        try:
            return len(e)  # some providers return the name list itself
        except Exception:
            return 0

    def count_addressable(self) -> int:
        """Total addressable tool names: sum of expand() + static counts."""
        total = 0
        for p in self._providers.values():
            total += self._expand(p) + len(self._static_tools(p))
        return total

    def list_namespaces(self) -> dict[str, int]:
        """Per-namespace addressable counts (expand() + static count)."""
        return {ns: self._expand(p) + len(self._static_tools(p))
                for ns, p in self._providers.items()}

    def spec_list(self) -> list[dict]:
        """Introspection list (no object explosion).

        Returns one dict per static tool (name/description/risk/needs_network,
        same shape as base.Registry.spec_list) plus one summary dict per
        provider namespace: {"namespace", "addressable_count", "sample_names"}.
        """
        specs: list[dict] = []
        for ns, p in self._providers.items():
            count = self._expand(p) + len(self._static_tools(p))
            try:
                samples = p.sample_names(5)
            except Exception:
                samples = []
            for t in self._static_tools(p):
                specs.append({"name": t.name, "description": t.description,
                              "risk": t.risk, "needs_network": t.needs_network})
            specs.append({"namespace": ns, "addressable_count": count,
                          "sample_names": samples})
        return specs
