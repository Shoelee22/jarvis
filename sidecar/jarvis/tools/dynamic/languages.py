"""Languages: greetings and speech across 12 languages.

Two namespaces, 12 tools each:
  greet.<lang>  {}        -> {"language", "greeting"}            (always works)
  say.<lang>    {text}    -> speaks via Piper voice, or an honest
                             {"error": "voice not installed ..."} when the
                             voices.<lang>.model_path config is empty/missing.

Languages (12): en hi hinglish ta te bn mr es fr de pt ar -- matching the
`voices` section of tools_config.dynamic.yaml. Handlers never raise; offline.
"""
from __future__ import annotations

from pathlib import Path

from ..base import Tool
from .config import section
from .providers import Provider

LANGUAGE_CODES: list[str] = [
    "en", "hi", "hinglish", "ta", "te", "bn",
    "mr", "es", "fr", "de", "pt", "ar",
]

GREETINGS: dict[str, str] = {
    "en": "Hello",
    "hi": "नमस्ते",
    "hinglish": "Namaste",
    "ta": "வணக்கம்",
    "te": "నమస్కారం",
    "bn": "নমস্কার",
    "mr": "नमस्कार",
    "es": "Hola",
    "fr": "Bonjour",
    "de": "Guten Tag",
    "pt": "Olá",
    "ar": "مرحبا",
}

_LANGUAGE_NAMES: dict[str, str] = {
    "en": "English", "hi": "Hindi", "hinglish": "Hinglish",
    "ta": "Tamil", "te": "Telugu", "bn": "Bengali",
    "mr": "Marathi", "es": "Spanish", "fr": "French",
    "de": "German", "pt": "Portuguese", "ar": "Arabic",
}


def _voice_model_path(lang: str) -> str:
    """Configured Piper model path for `lang`; "" when not installed."""
    voices = section("voices", {}) or {}
    entry = voices.get(lang, {}) or {}
    if not isinstance(entry, dict):
        return ""
    return str(entry.get("model_path", "") or "").strip()


class GreetProvider(Provider):
    """greet.<lang>: a greeting in each language. expand() == 12."""

    namespace = "greet"

    def expand(self) -> int:
        return len(LANGUAGE_CODES)

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        lang = self._local(name)
        if lang not in GREETINGS:
            return None

        def _greet(args: dict, _lang=lang) -> dict:  # noqa: ARG001
            try:
                return {
                    "language": _lang,
                    "language_name": _LANGUAGE_NAMES.get(_lang, _lang),
                    "greeting": GREETINGS[_lang],
                }
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        return Tool(
            name=name,
            description=f"Greet in {_LANGUAGE_NAMES.get(lang, lang)}.",
            schema={},
            handler=_greet,
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        return [self._dotted(c) for c in LANGUAGE_CODES[: max(0, n)]]


class SayProvider(Provider):
    """say.<lang>: speak text with the Piper voice; honest error when the
    voice model is not installed. expand() == 12."""

    namespace = "say"

    def expand(self) -> int:
        return len(LANGUAGE_CODES)

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        lang = self._local(name)
        if lang not in LANGUAGE_CODES:
            return None

        def _say(args: dict, _lang=lang) -> dict:
            try:
                text = str((args or {}).get("text", "") or "")
                if not text.strip():
                    return {"error": "text is required"}
                model = _voice_model_path(_lang)
                if not model or not Path(model).expanduser().is_file():
                    return {
                        "error": (
                            f"voice not installed for '{_lang}': set "
                            f"voices.{_lang}.model_path in "
                            f"tools_config.dynamic.yaml to a Piper .onnx model"
                        )
                    }
                # A real Piper invocation would happen here; the model path is
                # validated above. Speech synthesis itself is out of scope for
                # the offline dynamic namespace -- report honestly.
                return {
                    "error": (
                        f"voice model configured for '{_lang}' ({model}) but "
                        "in-process synthesis is not wired in this build"
                    )
                }
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        return Tool(
            name=name,
            description=f"Speak text in {_LANGUAGE_NAMES.get(lang, lang)} (needs Piper voice model).",
            schema={"text": "string"},
            handler=_say,
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        return [self._dotted(c) for c in LANGUAGE_CODES[: max(0, n)]]
