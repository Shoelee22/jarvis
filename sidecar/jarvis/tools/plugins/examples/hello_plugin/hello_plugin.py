"""hello plugin: example handler module for the JARVIS Plugin SDK.

Handlers take a dict and return a dict, NEVER raise.
"""


def greet(args: dict) -> dict:
    name = (args or {}).get("name") or "sir"
    return {"greeting": f"Hello, {name}. At your service."}
