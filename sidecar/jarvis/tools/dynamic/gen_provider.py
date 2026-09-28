"""Generators provider (Phase 10): small honest generators.

Namespace ``gen`` (6 tools):

  gen.uuid4        {}                                  -> random UUID4 string
  gen.password     {"length", "symbols"}               -> secrets-based password
  gen.lorem        {"paragraphs", "words"}             -> real lorem ipsum text
  gen.slug         {"text"}                            -> URL slug
  gen.timestamp    {}                                  -> current UTC ISO + epoch
  gen.random_int   {"min_value", "max_value"}          -> secrets-based random int

All real (secrets module for randomness, the actual Lorem ipsum passage,
datetime for time). Handlers never raise.
"""
from __future__ import annotations

import datetime
import re
import secrets
import string
import uuid

from ..base import Tool
from .providers import Provider

_LOREM_WORDS = (
    "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod "
    "tempor incididunt ut labore et dolore magna aliqua. Ut enim ad minim "
    "veniam, quis nostrud exercitation ullamco laboris nisi ut aliquip ex ea "
    "commodo consequat. Duis aute irure dolor in reprehenderit in voluptate "
    "velit esse cillum dolore eu fugiat nulla pariatur. Excepteur sint occaecat "
    "cupidatat non proident, sunt in culpa qui officia deserunt mollit anim id "
    "est laborum."
).split()


def _uuid4(args: dict) -> dict:
    return {"uuid": str(uuid.uuid4())}


def _password(args: dict) -> dict:
    args = args or {}
    length = args.get("length", 16)
    symbols = args.get("symbols", True)
    if isinstance(length, bool) or not isinstance(length, (int, float)):
        return {"error": "length must be a number"}
    length = int(length)
    if length < 4 or length > 128:
        return {"error": "length must be between 4 and 128"}
    alphabet = string.ascii_letters + string.digits
    if symbols:
        alphabet += "!@#$%^&*()-_=+[]{};:,.<>?"
    return {
        "password": "".join(secrets.choice(alphabet) for _ in range(length)),
        "length": length,
        "symbols": bool(symbols),
    }


def _lorem(args: dict) -> dict:
    args = args or {}
    words = args.get("words")
    paragraphs = args.get("paragraphs", 3)
    if words is not None:
        if isinstance(words, bool) or not isinstance(words, (int, float)) or words < 1:
            return {"error": "words must be a positive number"}
        count = int(words)
        text = " ".join(_LOREM_WORDS[i % len(_LOREM_WORDS)] for i in range(count))
        return {"words": count, "text": text}
    if isinstance(paragraphs, bool) or not isinstance(paragraphs, (int, float)):
        return {"error": "paragraphs must be a number"}
    paragraphs = int(paragraphs)
    if paragraphs < 1 or paragraphs > 20:
        return {"error": "paragraphs must be between 1 and 20"}
    paras = []
    per_para = 60
    for p in range(paragraphs):
        ws = [_LOREM_WORDS[(p * per_para + i) % len(_LOREM_WORDS)]
              for i in range(per_para)]
        paras.append(" ".join(ws))
    return {"paragraphs": paragraphs, "text": "\n\n".join(paras)}


def _slug(args: dict) -> dict:
    args = args or {}
    text = args.get("text")
    if not isinstance(text, str) or not text.strip():
        return {"error": "text is required"}
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    if not slug:
        return {"error": "text has no slug-able characters"}
    return {"text": text, "slug": slug}


def _timestamp(args: dict) -> dict:
    now = datetime.datetime.now(datetime.timezone.utc)
    return {"iso_utc": now.isoformat(), "epoch": int(now.timestamp())}


def _random_int(args: dict) -> dict:
    args = args or {}
    lo = args.get("min_value", 1)
    hi = args.get("max_value", 100)
    for label, v in (("min_value", lo), ("max_value", hi)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            return {"error": f"{label} must be a number"}
    lo, hi = int(lo), int(hi)
    if lo > hi:
        return {"error": "min_value must not exceed max_value"}
    return {"min_value": lo, "max_value": hi,
            "value": lo + secrets.randbelow(hi - lo + 1)}


_GENS: dict[str, dict] = {
    "uuid4": {"handler": _uuid4, "schema": {},
              "description": "Generate a random UUID4."},
    "password": {"handler": _password, "schema": {"length": "number (default 16)",
                                                 "symbols": "bool (default true)"},
                 "description": "Generate a random password with the secrets module."},
    "lorem": {"handler": _lorem, "schema": {"paragraphs": "number (default 3)",
                                            "words": "number (optional)"},
              "description": "Generate real lorem ipsum placeholder text."},
    "slug": {"handler": _slug, "schema": {"text": "string"},
             "description": "Turn text into a URL slug."},
    "timestamp": {"handler": _timestamp, "schema": {},
                  "description": "Current UTC time as ISO-8601 and epoch."},
    "random_int": {"handler": _random_int,
                   "schema": {"min_value": "number (default 1)",
                              "max_value": "number (default 100)"},
                   "description": "Random integer in [min_value, max_value] via secrets."},
}


class GenProvider(Provider):
    """gen.<name>: small honest generators. expand() == 6."""

    namespace = "gen"

    def expand(self) -> int:
        return len(_GENS)

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        spec = _GENS.get(self._local(name))
        if spec is None:
            return None

        def _gen(args: dict, _fn=spec["handler"]) -> dict:
            try:
                return _fn(args if isinstance(args, dict) else {})
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        return Tool(
            name=name,
            description=spec["description"],
            schema=spec["schema"],
            handler=_gen,
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        return [self._dotted(k) for k in list(_GENS)[: max(0, n)]]
