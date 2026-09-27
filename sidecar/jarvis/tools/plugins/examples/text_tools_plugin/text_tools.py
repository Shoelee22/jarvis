"""text plugin: example handler module for the JARVIS Plugin SDK.

Handlers take a dict and return a dict, NEVER raise.
"""


def _text(args: dict) -> str:
    value = (args or {}).get("text", "")
    return value if isinstance(value, str) else str(value)


def to_uppercase(args: dict) -> dict:
    return {"text": _text(args).upper()}


def wordcount(args: dict) -> dict:
    text = _text(args)
    return {
        "words": len(text.split()),
        "chars": len(text),
        "lines": len(text.splitlines()),
    }


def reverse_text(args: dict) -> dict:
    return {"text": _text(args)[::-1]}
