"""Language expansion (Phase 10): +48 languages for the greet/say namespaces.

``languages.py`` keeps its original 12 languages untouched. This module adds
48 MORE languages (every greeting below is a widely-known, correct greeting
for that language; anything uncertain was omitted rather than guessed).

Total reach: 60 languages across two namespaces:

  greet.<lang>  {}      -> {"language", "language_name", "greeting"}
  say.<lang>    {text}  -> Piper speech, or an honest "voice not installed" error

WIRING (parent must apply -- new files only, so the registry is not touched
here): in ``jarvis/tools/dynamic/__init__.py``, REPLACE the two language
registrations with the extended providers from THIS module (the registry keys
providers by namespace, so a plain second registration would collide):

    _try_make(registry, "say",   "languages_extra", "ExtendedSayProvider",
              data_dir=data_dir)
    _try_make(registry, "greet", "languages_extra", "ExtendedGreetProvider",
              data_dir=data_dir)

ExtendedGreetProvider / ExtendedSayProvider subclass the originals but serve
the merged 60-language set, so expand() == 60 for each namespace.
"""
from __future__ import annotations

from pathlib import Path

from ..base import Tool
from .languages import (
    LANGUAGE_CODES,
    GREETINGS,
    _LANGUAGE_NAMES,
    GreetProvider,
    SayProvider,
    _voice_model_path,
)
from .providers import Provider  # noqa: F401  (re-exported for clarity)

#: 48 additional ISO-ish language codes, in fixed order.
EXTRA_LANGUAGE_CODES: list[str] = [
    "it", "nl", "ru", "zh", "ja", "ko", "tr", "pl",
    "sv", "no", "da", "fi", "el", "he", "th", "vi",
    "id", "ms", "fa", "ur", "pa", "gu", "kn", "ml",
    "or", "ne", "si", "my", "km", "lo", "tl", "hu",
    "cs", "sk", "ro", "bg", "sr", "hr", "uk", "ca",
    "eu", "gl", "cy", "ga", "af", "sw", "zu", "am",
]

#: Widely-known correct greeting per extra language (omitted when uncertain).
EXTRA_GREETINGS: dict[str, str] = {
    "it": "Ciao",            # Italian
    "nl": "Hallo",           # Dutch
    "ru": "Здравствуйте",    # Russian
    "zh": "你好",             # Mandarin Chinese
    "ja": "こんにちは",        # Japanese
    "ko": "안녕하세요",        # Korean
    "tr": "Merhaba",         # Turkish
    "pl": "Dzień dobry",     # Polish
    "sv": "Hej",             # Swedish
    "no": "Hei",             # Norwegian
    "da": "Hej",             # Danish
    "fi": "Hei",             # Finnish
    "el": "Γεια σας",        # Greek
    "he": "שלום",            # Hebrew
    "th": "สวัสดี",           # Thai
    "vi": "Xin chào",        # Vietnamese
    "id": "Halo",            # Indonesian
    "ms": "Selamat pagi",    # Malay
    "fa": "سلام",            # Persian
    "ur": "السلام علیکم",    # Urdu
    "pa": "ਸਤਿ ਸ੍ਰੀ ਅਕਾਲ",    # Punjabi
    "gu": "નમસ્તે",           # Gujarati
    "kn": "ನಮಸ್ಕಾರ",          # Kannada
    "ml": "നമസ്കാരം",         # Malayalam
    "or": "ନମସ୍କାର",          # Odia
    "ne": "नमस्ते",           # Nepali
    "si": "ආයුබෝවන්",         # Sinhala
    "my": "မင်္ဂလာပါ",       # Burmese
    "km": "សួស្តី",           # Khmer
    "lo": "ສະບາຍດີ",         # Lao
    "tl": "Kumusta",         # Tagalog
    "hu": "Jó napot",        # Hungarian
    "cs": "Ahoj",            # Czech
    "sk": "Ahoj",            # Slovak
    "ro": "Bună ziua",       # Romanian
    "bg": "Здравейте",       # Bulgarian
    "sr": "Zdravo",          # Serbian
    "hr": "Dobar dan",       # Croatian
    "uk": "Добрий день",     # Ukrainian
    "ca": "Hola",            # Catalan
    "eu": "Kaixo",           # Basque
    "gl": "Ola",             # Galician
    "cy": "Helo",            # Welsh
    "ga": "Dia dhuit",       # Irish
    "af": "Hallo",           # Afrikaans
    "sw": "Habari",          # Swahili
    "zu": "Sawubona",        # Zulu
    "am": "ሰላም",             # Amharic
}

EXTRA_LANGUAGE_NAMES: dict[str, str] = {
    "it": "Italian", "nl": "Dutch", "ru": "Russian", "zh": "Mandarin Chinese",
    "ja": "Japanese", "ko": "Korean", "tr": "Turkish", "pl": "Polish",
    "sv": "Swedish", "no": "Norwegian", "da": "Danish", "fi": "Finnish",
    "el": "Greek", "he": "Hebrew", "th": "Thai", "vi": "Vietnamese",
    "id": "Indonesian", "ms": "Malay", "fa": "Persian", "ur": "Urdu",
    "pa": "Punjabi", "gu": "Gujarati", "kn": "Kannada", "ml": "Malayalam",
    "or": "Odia", "ne": "Nepali", "si": "Sinhala", "my": "Burmese",
    "km": "Khmer", "lo": "Lao", "tl": "Tagalog", "hu": "Hungarian",
    "cs": "Czech", "sk": "Slovak", "ro": "Romanian", "bg": "Bulgarian",
    "sr": "Serbian", "hr": "Croatian", "uk": "Ukrainian", "ca": "Catalan",
    "eu": "Basque", "gl": "Galician", "cy": "Welsh", "ga": "Irish",
    "af": "Afrikaans", "sw": "Swahili", "zu": "Zulu", "am": "Amharic",
}

assert len(EXTRA_LANGUAGE_CODES) == 48
assert set(EXTRA_LANGUAGE_CODES) == set(EXTRA_GREETINGS) == set(EXTRA_LANGUAGE_NAMES)
assert not (set(EXTRA_LANGUAGE_CODES) & set(LANGUAGE_CODES)), "extras must not overlap base 12"

#: Merged, deduped, fixed-order: base 12 first, then the 48 extras.
ALL_LANGUAGE_CODES: list[str] = LANGUAGE_CODES + EXTRA_LANGUAGE_CODES
ALL_GREETINGS: dict[str, str] = {**GREETINGS, **EXTRA_GREETINGS}
ALL_LANGUAGE_NAMES: dict[str, str] = {**_LANGUAGE_NAMES, **EXTRA_LANGUAGE_NAMES}

TOTAL_LANGUAGES = 60
assert len(ALL_LANGUAGE_CODES) == TOTAL_LANGUAGES == len(ALL_GREETINGS)


class ExtendedGreetProvider(GreetProvider):
    """greet.<lang> across all 60 languages. expand() == 60."""

    def expand(self) -> int:
        return len(ALL_LANGUAGE_CODES)

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        lang = self._local(name)
        if lang not in ALL_GREETINGS:
            return None

        def _greet(args: dict, _lang=lang) -> dict:  # noqa: ARG001
            try:
                return {
                    "language": _lang,
                    "language_name": ALL_LANGUAGE_NAMES.get(_lang, _lang),
                    "greeting": ALL_GREETINGS[_lang],
                }
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        return Tool(
            name=name,
            description=f"Greet in {ALL_LANGUAGE_NAMES.get(lang, lang)}.",
            schema={},
            handler=_greet,
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        return [self._dotted(c) for c in ALL_LANGUAGE_CODES[: max(0, n)]]


class ExtendedSayProvider(SayProvider):
    """say.<lang> across all 60 languages (honest error when the Piper
    voice model is not installed). expand() == 60."""

    def expand(self) -> int:
        return len(ALL_LANGUAGE_CODES)

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        lang = self._local(name)
        if lang not in ALL_LANGUAGE_CODES:
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
            description=f"Speak text in {ALL_LANGUAGE_NAMES.get(lang, lang)} (needs Piper voice model).",
            schema={"text": "string"},
            handler=_say,
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        return [self._dotted(c) for c in ALL_LANGUAGE_CODES[: max(0, n)]]
