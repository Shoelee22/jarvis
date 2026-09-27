"""Encode/decode provider (Phase 10): reversible encodings + honest one-way hashes.

Two namespaces, 12 tools each (24 total):

  encode.text_to_<algo>   {"text": "..."} -> {"text", "algorithm", "result"}
  decode.<algo>_to_text   {"text": "..."} -> {"text", "algorithm", "result"}

Algorithms: base64, base64url, hex, urlencode, rot13, md5, sha1, sha256,
sha512, sha3_256, binary, morse.

Hashes are one-way: ``decode.md5_to_text`` (and the other hash decoders)
honestly return ``{"error": "... is one-way; cannot decode"}`` rather than
faking a result.

morse implements real ITU Morse for A-Z, 0-9 and common punctuation.
Letters are separated by a space, words by " / ". Unencodable characters and
undecodable sequences return honest errors.

binary is text <-> space-separated 8-bit groups (UTF-8 bytes).

Handlers never raise; all through stdlib (hashlib, base64, urllib.parse,
codecs).
"""
from __future__ import annotations

import base64
import binascii
import codecs
import hashlib
import urllib.parse

from ..base import Tool
from .providers import Provider

#: Algorithms in fixed order; used by both providers.
ALGORITHMS: list[str] = [
    "base64", "base64url", "hex", "urlencode", "rot13",
    "md5", "sha1", "sha256", "sha512", "sha3_256",
    "binary", "morse",
]

#: One-way algorithms: decode is impossible by construction.
ONE_WAY: frozenset[str] = frozenset(
    {"md5", "sha1", "sha256", "sha512", "sha3_256"}
)

_MORSE: dict[str, str] = {
    # letters
    "A": ".-", "B": "-...", "C": "-.-.", "D": "-..", "E": ".",
    "F": "..-.", "G": "--.", "H": "....", "I": "..", "J": ".---",
    "K": "-.-", "L": ".-..", "M": "--", "N": "-.", "O": "---",
    "P": ".--.", "Q": "--.-", "R": ".-.", "S": "...", "T": "-",
    "U": "..-", "V": "...-", "W": ".--", "X": "-..-", "Y": "-.--",
    "Z": "--..",
    # digits
    "0": "-----", "1": ".----", "2": "..---", "3": "...--", "4": "....-",
    "5": ".....", "6": "-....", "7": "--...", "8": "---..", "9": "----.",
    # punctuation (ITU)
    ".": ".-.-.-", ",": "--..--", "?": "..--..", "'": ".----.",
    "!": "-.-.--", "/": "-..-.", "(": "-.--.", ")": "-.--.-",
    "&": ".-...", ":": "---...", ";": "-.-.-.", "=": "-...-",
    "+": ".-.-.", "-": "-....-", "_": "..--..", '"': ".-.-.",
    "$": "...-..-", "@": ".--.-.",
}
_MORSE_REV: dict[str, str] = {v: k for k, v in _MORSE.items()}


# ---------------------------------------------------------------------------
# implementations
# ---------------------------------------------------------------------------

def _encode_base64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def _decode_base64(text: str) -> str:
    try:
        raw = base64.b64decode(text.strip(), validate=True)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"invalid base64: {e}")
    return _utf8(raw)


def _encode_base64url(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode("ascii")


def _decode_base64url(text: str) -> str:
    padded = text.strip() + "=" * (-len(text.strip()) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded)
    except (binascii.Error, ValueError) as e:
        raise ValueError(f"invalid base64url: {e}")
    return _utf8(raw)


def _utf8(raw: bytes) -> str:
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError:
        raise ValueError("decoded bytes are not valid UTF-8 text")


def _encode_hex(text: str) -> str:
    return text.encode("utf-8").hex()


def _decode_hex(text: str) -> str:
    try:
        raw = bytes.fromhex(text.strip())
    except ValueError as e:
        raise ValueError(f"invalid hex: {e}")
    return _utf8(raw)


def _encode_urlencode(text: str) -> str:
    return urllib.parse.quote(text, safe="")


def _decode_urlencode(text: str) -> str:
    return urllib.parse.unquote(text)


def _encode_rot13(text: str) -> str:
    return codecs.encode(text, "rot_13")


def _decode_rot13(text: str) -> str:
    return codecs.encode(text, "rot_13")  # rot13 is its own inverse


def _encode_md5(text: str) -> str:
    return hashlib.md5(text.encode("utf-8")).hexdigest()


def _encode_sha1(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def _encode_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _encode_sha512(text: str) -> str:
    return hashlib.sha512(text.encode("utf-8")).hexdigest()


def _encode_sha3_256(text: str) -> str:
    return hashlib.sha3_256(text.encode("utf-8")).hexdigest()


def _encode_binary(text: str) -> str:
    return " ".join(f"{b:08b}" for b in text.encode("utf-8"))


def _decode_binary(text: str) -> str:
    groups = text.split()
    if not groups:
        raise ValueError("no binary groups found")
    raw = bytearray()
    for g in groups:
        if len(g) != 8 or any(c not in "01" for c in g):
            raise ValueError(f"invalid 8-bit binary group: {g!r}")
        raw.append(int(g, 2))
    return _utf8(bytes(raw))


def _encode_morse(text: str) -> str:
    words = []
    for word in text.upper().split(" "):
        letters = []
        for ch in word:
            code = _MORSE.get(ch)
            if code is None:
                raise ValueError(f"cannot encode character {ch!r} in morse")
            letters.append(code)
        words.append(" ".join(letters))
    return " / ".join(words)


def _decode_morse(text: str) -> str:
    words = []
    for word in text.strip().split(" / "):
        letters = []
        for seq in word.split():
            ch = _MORSE_REV.get(seq)
            if ch is None:
                raise ValueError(f"unknown morse sequence: {seq!r}")
            letters.append(ch)
        words.append("".join(letters))
    return " ".join(words)


_ENCODERS: dict[str, object] = {
    "base64": _encode_base64, "base64url": _encode_base64url,
    "hex": _encode_hex, "urlencode": _encode_urlencode,
    "rot13": _encode_rot13, "md5": _encode_md5,
    "sha1": _encode_sha1, "sha256": _encode_sha256,
    "sha512": _encode_sha512, "sha3_256": _encode_sha3_256,
    "binary": _encode_binary, "morse": _encode_morse,
}

_DECODERS: dict[str, object] = {
    "base64": _decode_base64, "base64url": _decode_base64url,
    "hex": _decode_hex, "urlencode": _decode_urlencode,
    "rot13": _decode_rot13, "binary": _decode_binary,
    "morse": _decode_morse,
}


# ---------------------------------------------------------------------------
# providers
# ---------------------------------------------------------------------------

def _text_arg(args) -> str:
    if not isinstance(args, dict):
        raise ValueError("args must be an object")
    text = args.get("text")
    if not isinstance(text, str) or not text:
        raise ValueError("text is required and must be a non-empty string")
    return text


class EncodeProvider(Provider):
    """encode.text_to_<algo>: encode text with each algorithm. expand() == 12."""

    namespace = "encode"

    def expand(self) -> int:
        return len(ALGORITHMS)

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        local = self._local(name)
        if not local.startswith("text_to_"):
            return None
        algo = local[len("text_to_"):]
        encode_fn = _ENCODERS.get(algo)
        if encode_fn is None:
            return None

        def _encode(args: dict, _algo=algo, _fn=encode_fn) -> dict:
            try:
                text = _text_arg(args)
                try:
                    result = _fn(text)
                except ValueError as e:
                    return {"error": str(e)}
                return {"text": text, "algorithm": _algo, "result": result}
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        return Tool(
            name=name,
            description=f"Encode text to {algo}.",
            schema={"text": "string"},
            handler=_encode,
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        return [self._dotted(f"text_to_{a}") for a in ALGORITHMS[: max(0, n)]]


class DecodeProvider(Provider):
    """decode.<algo>_to_text: decode text; hashes honestly report one-way.

    expand() == 12 (hash decoders are addressable but return an honest error).
    """

    namespace = "decode"

    def expand(self) -> int:
        return len(ALGORITHMS)

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        local = self._local(name)
        if not local.endswith("_to_text"):
            return None
        algo = local[: -len("_to_text")]
        decode_fn = _DECODERS.get(algo)
        one_way = algo in ONE_WAY
        if decode_fn is None and not one_way:
            return None

        def _decode(args: dict, _algo=algo, _fn=decode_fn,
                    _one_way=one_way) -> dict:
            try:
                if _one_way:
                    return {"error": f"{_algo} is one-way; cannot decode"}
                text = _text_arg(args)
                try:
                    result = _fn(text)
                except ValueError as e:
                    return {"error": str(e)}
                return {"text": text, "algorithm": _algo, "result": result}
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        return Tool(
            name=name,
            description=(f"Decode {algo} to text."
                         if not one_way else
                         f"{algo} is one-way; this tool reports that honestly."),
            schema={"text": "string"},
            handler=_decode,
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        return [self._dotted(f"{a}_to_text") for a in ALGORITHMS[: max(0, n)]]
