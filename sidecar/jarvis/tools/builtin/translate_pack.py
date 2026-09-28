"""Translate Tool Pack (Phase 10): offline text translation + language detection.

OFFLINE-FIRST by design: translate.text uses Argos Translate models installed
on this machine; translate.detect uses langdetect. Neither needs network —
needs_network=False on both. Nothing here phones home.

Honest errors when the optional packages are missing (exact install hints),
and when no offline language package for the requested target exists.
"""
from __future__ import annotations

_ARGOS_HINT = ("argostranslate is not installed. Install it with: "
               "pip install argostranslate — then install an offline language "
               "package, e.g.: argospm install translate-en_es")
_LANGDETECT_HINT = ("langdetect is not installed. Install it with: "
                    "pip install langdetect")


# ------------------------------------------------------------ helpers
def _argos():
    try:
        import argostranslate  # noqa: F401
        return True
    except ImportError:
        return False


def _langdetect():
    try:
        import langdetect  # noqa: F401
        return True
    except ImportError:
        return False


def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


# --------------------------------------------------- translate.text
def translate_text(args: dict) -> dict:
    """Translate text with offline Argos Translate models (no network)."""
    try:
        if not _argos():
            return {"error": _ARGOS_HINT}
        text = str(args.get("text", ""))
        if not text.strip():
            return {"error": "text is required"}
        target = str(args.get("target_lang", "")).strip().lower()
        if not target:
            return {"error": "target_lang is required (ISO 639-1, e.g. 'es')"}
        source = str(args.get("source_lang", "")).strip().lower() or None

        from argostranslate import translate as at
        installed = at.get_installed_languages()
        targets = [lng for lng in installed if lng.code == target]
        if not targets:
            return {"error": f"no offline Argos language package for "
                             f"'{target}' is installed. Install one, e.g.: "
                             f"argospm install translate-en_{target} "
                             f"(packages: https://www.argosopentech.com/argospm/index/)"}
        # Pick a source language: explicit source_lang wins, else prefer one
        # that actually has a translation path to the target, else first.
        chosen, from_lng = None, None
        for lng in targets:
            for cand in installed:
                if source and cand.code != source:
                    continue
                try:
                    tr = lng.get_translation(cand)
                except Exception:
                    tr = None
                if tr is not None:
                    chosen, from_lng = tr, cand
                    break
            if chosen is not None:
                break
        if chosen is None:
            return {"error": f"no installed source language has a translation "
                             f"path to '{target}'"}
        result = chosen.translate(text)
        return {"text": text, "translated": result,
                "from": from_lng.code, "to": target,
                "offline": True,
                "note": "offline Argos Translate; quality depends on the "
                        "installed language package"}
    except Exception as e:
        return _fail("translate.text", e)


# ------------------------------------------------- translate.detect
def translate_detect(args: dict) -> dict:
    """Detect the language of text (offline, langdetect)."""
    try:
        if not _langdetect():
            return {"error": _LANGDETECT_HINT}
        text = str(args.get("text", ""))
        if not text.strip():
            return {"error": "text is required"}
        if len(text.strip()) < 10:
            return {"error": "text is too short for reliable detection "
                             "(min ~10 characters)"}
        from langdetect import detect_langs
        probs = detect_langs(text)
        top = probs[0]
        return {"text": text[:200],
                "language": top.lang, "confidence": round(top.prob, 3),
                "candidates": [{"language": p.lang,
                                "confidence": round(p.prob, 3)}
                               for p in probs[:3]],
                "offline": True}
    except Exception as e:
        return _fail("translate.detect", e)


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "translate.text",
     "description": "Translate text offline with Argos Translate models "
                    "(needs_network=False).",
     "handler": translate_text, "risk": "low", "needs_network": False,
     "schema": {"text": "string", "target_lang": "string",
                "source_lang": "string?"}},
    {"name": "translate.detect",
     "description": "Detect the language of text offline (langdetect; "
                    "needs_network=False).",
     "handler": translate_detect, "risk": "low", "needs_network": False,
     "schema": {"text": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(translate_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "translate.text": ("low", False),
    "translate.detect": ("low", False),
}


def register(reg) -> None:
    """Wire the two translate tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))


__all__ = ["TOOL_DEFS", "RISK_TABLE_ADDITIONS", "register",
           "translate_text", "translate_detect"]
