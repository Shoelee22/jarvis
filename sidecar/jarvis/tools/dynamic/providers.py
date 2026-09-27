"""Base class for dynamic tool providers (Phase 7).

A Provider owns one dotted namespace (e.g. ``units`` -> ``units.convert``) and
materializes Tool objects lazily: `expand()` must give the exact addressable
count cheaply (no object explosion), `resolve()` builds exactly ONE Tool for a
given name.

EXACT Provider interface — other workers code against this:

    class MyProvider(Provider):
        namespace = "myns"            # class attr: dotted prefix (required)

        def __init__(self, data_dir=None):   # optional kwarg; most need no args
            super().__init__(data_dir)

        def expand(self) -> int:             # exact addressable count; cheap
            ...

        def resolve(self, name: str) -> Tool | None:   # ONE Tool, lazily, or None
            ...                                        # `name` is the FULL dotted name

        def static_tools(self) -> list[Tool]:         # eager tools; default []
            return super().static_tools()

        def sample_names(self, n: int = 5) -> list[str]:  # representative names
            ...

Tool shape (from ..base): name, description, schema (dict), handler
(Callable[[dict], dict] that NEVER raises -- returns {"error": ...} instead),
risk in {"low","medium","high"}, needs_network bool.
"""
from __future__ import annotations

from pathlib import Path

from ..base import Tool


class Provider:
    """Base class for dynamic tool providers."""

    #: Dotted namespace prefix owned by this provider, e.g. "units".
    namespace: str = ""

    def __init__(self, data_dir=None):
        self.data_dir = Path(data_dir) if data_dir is not None else None

    # -- contract ---------------------------------------------------------
    def expand(self) -> int:
        """Exact count of addressable tool names in this namespace.

        Must be cheap to compute -- no Tool object creation here.
        """
        raise NotImplementedError

    def resolve(self, name: str) -> Tool | None:
        """Build exactly ONE Tool for the full dotted `name`, or None.

        Return None when `name` does not belong to this namespace (DynamicRegistry
        already routes by namespace, so this is belt-and-braces).
        """
        raise NotImplementedError

    def static_tools(self) -> list[Tool]:
        """Eagerly materialized tools of this namespace (usually none)."""
        return []

    def sample_names(self, n: int = 5) -> list[str]:
        """Up to `n` representative dotted tool names for this namespace."""
        return [t.name for t in self.static_tools()[:n]]

    # -- helpers ----------------------------------------------------------
    def _dotted(self, local: str) -> str:
        """Prefix a local tool name with this provider's namespace."""
        return f"{self.namespace}.{local}"

    def _owns(self, name: str) -> bool:
        """True when `name` belongs to this namespace."""
        return bool(self.namespace) and name.startswith(self.namespace + ".")

    def _local(self, name: str) -> str:
        """Strip the namespace prefix from a dotted name."""
        return name[len(self.namespace) + 1:]
