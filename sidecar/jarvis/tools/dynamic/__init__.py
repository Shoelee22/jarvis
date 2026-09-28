"""Dynamic tool providers (Phase 7): lazily-resolved, high-cardinality tool namespaces."""
from __future__ import annotations

from .registry import DynamicRegistry


def _try_make(registry: DynamicRegistry, label: str, module: str, cls_name: str,
              *args, **kwargs) -> None:
    """Import a provider class and register it; record failures on registry.skipped."""
    try:
        mod = __import__(f"jarvis.tools.dynamic.{module}", fromlist=[cls_name])
    except Exception:
        try:
            mod = __import__(f".{module}", fromlist=[cls_name])
        except Exception:
            registry.skipped.append(label)
            return
    try:
        cls = getattr(mod, cls_name)
    except AttributeError:
        registry.skipped.append(label)
        return
    try:
        try:
            provider = cls(*args, **kwargs)
        except TypeError:
            provider = cls()
    except Exception:
        registry.skipped.append(label)
        return
    registry.register_provider(provider)


def build_dynamic_registry(main_registry=None, data_dir=None) -> DynamicRegistry:
    """Build a DynamicRegistry with every Phase 7 provider wired up.

    Providers whose modules haven't landed (or fail to construct) are recorded
    on ``registry.skipped`` and skipped -- the registry operates normally with
    whatever is present.
    """
    registry = DynamicRegistry(main_registry=main_registry, data_dir=data_dir)

    _try_make(registry, "units", "units", "UnitsProvider", data_dir=data_dir)
    _try_make(registry, "automation", "automation", "AutomationProvider",
              data_dir, main_registry)
    _try_make(registry, "contact", "contacts_actions", "ContactsActionsProvider",
              data_dir=data_dir)
    _try_make(registry, "app", "apps", "AppsProvider", data_dir=data_dir)
    _try_make(registry, "web", "web_actions", "WebActionsProvider", data_dir=data_dir)
    _try_make(registry, "joke", "jokes", "JokesProvider", data_dir=data_dir)
    _try_make(registry, "say", "languages_extra", "ExtendedSayProvider", data_dir=data_dir)
    _try_make(registry, "greet", "languages_extra", "ExtendedGreetProvider", data_dir=data_dir)
    _try_make(registry, "opinion", "opinions", "OpinionProvider", data_dir=data_dir)
    _try_make(registry, "knowledge", "knowledge_wrap", "KnowledgeProvider",
              data_dir=data_dir)
    # --- Phase 10: more dynamic providers ---
    _try_make(registry, "tz", "tz_provider", "TzProvider", data_dir=data_dir)
    _try_make(registry, "color", "color_provider", "ColorProvider", data_dir=data_dir)
    _try_make(registry, "encode", "encode_provider", "EncodeProvider", data_dir=data_dir)
    _try_make(registry, "decode", "encode_provider", "DecodeProvider", data_dir=data_dir)
    _try_make(registry, "math", "math_provider", "MathProvider", data_dir=data_dir)
    _try_make(registry, "gen", "gen_provider", "GenProvider", data_dir=data_dir)
    return registry


__all__ = ["DynamicRegistry", "build_dynamic_registry"]
