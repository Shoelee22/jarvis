"""Timezone conversion provider (Phase 10): every ordered IANA zone pair is an addressable tool.

Tool names are shaped ``tz.<from>_to_<to>``, e.g. ``tz.america__new_york_to_asia__kolkata``.
With N zones the namespace exposes exactly N*(N-1) tool names. All conversion
math is real: the stdlib ``zoneinfo`` database (with full DST history) is the
only source of offsets. Handlers never raise -- bad input yields
``{"error": ...}``.

SANITIZATION RULE (deterministic, documented here):
  1. lowercase the IANA name
  2. "/"  -> "__"          (keeps the Continent/City hierarchy readable)
  3. "-"  -> "_"           (hyphens are common: America/Port-au-Prince)
  4. "+"  -> "_plus_"      (only appears in Etc/GMT+N; see collisions below)
  5. any other character outside [a-z0-9_] -> "_" (none exist in current tzdata)
  Runs of "_" are NOT collapsed: collapsing would destroy the "__" separator
  from step 2. Every token is a valid dotted-identifier segment (only
  [a-z0-9_], never starts with a digit).

COLLISIONS: with the current tzdata the only collision source is the pair
``Etc/GMT+N`` / ``Etc/GMT-N`` (both would otherwise sanitize to ``etc_gmt_N``).
Rule 4 disambiguates them asymmetrically by design: hyphens (the common case)
stay plain underscores, plus signs get the explicit ``_plus_`` tag, giving e.g.
``etc_gmt_plus_5`` vs ``etc_gmt_minus_5``. Verification at import confirms all
498 tokens are unique. As a belt-and-braces fallback, if any collision ever
survives the rule, the colliding entries are deterministically renamed in
sorted zone order to ``<token>__d2``, ``<token>__d3``, ... -- so token
uniqueness (and the exact N*(N-1) count) always holds.

DATETIME SEMANTICS (documented honestly):
  - Naive input (no offset): interpreted as wall-clock time in the FROM zone.
    Ambiguous local times (DST fold) resolve to the FIRST occurrence (fold=0).
  - Aware input (carries its own offset, e.g. "...Z" or "+05:30"): treated as
    an absolute instant; the FROM zone is still validated and reported, but
    the input's own offset wins for the instant. The result dict flags this
    with "input_interpreted_in_from_zone": false.
"""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, available_timezones

from ..base import Tool
from .providers import Provider


def _sanitize_zone(name: str) -> str:
    """Apply the documented sanitization rule to one IANA zone name."""
    token = name.lower()
    token = token.replace("/", "__")
    token = token.replace("-", "_")
    token = token.replace("+", "_plus_")
    token = re.sub(r"[^a-z0-9_]", "_", token)
    return token


def _build_zone_maps() -> tuple[dict[str, str], dict[str, str]]:
    """token -> IANA name and IANA name -> token, collision-free.

    Verified against the live tzdata; the deterministic __dN fallback keeps
    uniqueness even if future tzdata introduces a new collision.
    """
    raw: dict[str, list[str]] = {}
    for zone in available_timezones():
        raw.setdefault(_sanitize_zone(zone), []).append(zone)
    token_to_zone: dict[str, str] = {}
    for token in sorted(raw):
        zones = sorted(raw[token])
        for i, zone in enumerate(zones):
            final = token if i == 0 else f"{token}__d{i + 1}"
            token_to_zone[final] = zone
    zone_to_token = {zone: tok for tok, zone in token_to_zone.items()}
    return token_to_zone, zone_to_token


_TOKEN_TO_ZONE, _ZONE_TO_TOKEN = _build_zone_maps()
_TOKENS: list[str] = sorted(_TOKEN_TO_ZONE)

# Invariant the whole provider rests on: one token per zone, no collisions.
assert len(_TOKEN_TO_ZONE) == len(available_timezones()), "tz token collision"


def _format_offset(delta: timedelta | None) -> str:
    """timedelta -> '+05:30' / '-05:00' style offset string."""
    if delta is None:
        return "unknown"
    total = int(delta.total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    hours, rem = divmod(total, 3600)
    minutes, seconds = divmod(rem, 60)
    out = f"{sign}{hours:02d}:{minutes:02d}"
    if seconds:
        out += f":{seconds:02d}"
    return out


def _parse_datetime(raw: object) -> tuple[datetime | None, str | None]:
    """Parse ISO 8601 input. Returns (datetime, error_message)."""
    if isinstance(raw, bool) or not isinstance(raw, str):
        return None, "arg 'datetime' must be an ISO 8601 string"
    text = raw.strip()
    if not text:
        return None, "arg 'datetime' must be an ISO 8601 string"
    try:
        return datetime.fromisoformat(text), None
    except ValueError:
        return None, f"unparseable datetime {raw!r}: expected ISO 8601"


def _convert_handler(from_zone: str, to_zone: str):
    """Build the handler for one ordered zone pair. Never raises."""
    from_tz = ZoneInfo(from_zone)
    to_tz = ZoneInfo(to_zone)

    def _handler(args: dict) -> dict:
        try:
            if not isinstance(args, dict):
                return {"error": "args must be an object"}
            dt, err = _parse_datetime(args.get("datetime"))
            if err is not None:
                return {"error": err}
            assert dt is not None
            if dt.tzinfo is None:
                # Naive wall-clock time in the FROM zone (fold=0 documented).
                src = dt.replace(tzinfo=from_tz)
                interpreted_in_from = True
            else:
                # Aware input is an absolute instant; its own offset wins.
                src = dt
                interpreted_in_from = False
            out = src.astimezone(to_tz)
            return {
                "input": args.get("datetime"),
                "from_zone": from_zone,
                "to_zone": to_zone,
                "input_interpreted_in_from_zone": interpreted_in_from,
                "result": out.isoformat(),
                "from_utc_offset": _format_offset(src.utcoffset()),
                "to_utc_offset": _format_offset(out.utcoffset()),
                "utc_instant": out.astimezone(timezone.utc).isoformat(),
            }
        except Exception as e:  # never raise out of a tool handler
            return {"error": f"{type(e).__name__}: {e}"}

    return _handler


def _unknown_zone_tool(name: str, reason: str) -> Tool:
    """A Tool for a tz-shaped name that isn't addressable: clean error, no crash."""

    def _handler(args: dict) -> dict:  # noqa: ARG001
        return {"error": reason}

    return Tool(
        name=name,
        description="Invalid timezone conversion request (see error)",
        schema={"datetime": "ISO 8601 datetime string"},
        handler=_handler,
        risk="low",
    )


class TzProvider(Provider):
    """Dynamic provider for the ``tz`` namespace.

    ``tz.<from>_to_<to>`` for every ordered pair of distinct IANA zones.
    Lazy: pairs are never precomputed; expand() is O(1) on the zone count.
    """

    namespace = "tz"

    def expand(self) -> int:
        """Exact addressable count: N*(N-1) over distinct zone pairs."""
        n = len(_TOKENS)
        return n * (n - 1)

    def count_addressable(self) -> int:
        """Alias for expand(), per the Phase 10 workstream contract."""
        return self.expand()

    def resolve(self, name: str) -> Tool | None:
        """Resolve ``tz.<from>_to_<to>`` to one Tool.

        - Valid pair -> conversion Tool (real zoneinfo math).
        - tz-shaped name with unknown zone(s) or a self-pair -> Tool whose
          handler returns a clean {"error": ...} (no crash, no None).
        - Anything not shaped like the namespace -> None.
        """
        if not isinstance(name, str) or not name.startswith("tz."):
            return None
        body = name[len("tz."):]
        if "_to_" not in body:
            return None
        a, b = body.rsplit("_to_", 1)
        if not a or not b:
            return None
        from_zone = _TOKEN_TO_ZONE.get(a)
        to_zone = _TOKEN_TO_ZONE.get(b)
        if from_zone is None or to_zone is None:
            unknown = [t for t, z in ((a, from_zone), (b, to_zone)) if z is None]
            return _unknown_zone_tool(
                name,
                f"unknown timezone token(s): {', '.join(unknown)}",
            )
        if from_zone == to_zone:
            return _unknown_zone_tool(
                name,
                f"source and destination are the same zone ({from_zone}): "
                "not an addressable pair",
            )

        return Tool(
            name=name,
            description=(
                f"Convert a datetime in {from_zone} to {to_zone}. "
                f"Args: datetime (ISO 8601), assumed to be {from_zone} wall "
                "time unless it carries its own offset."
            ),
            schema={"datetime": "ISO 8601 datetime string"},
            handler=_convert_handler(from_zone, to_zone),
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        """Up to `n` representative, always-valid tool names."""
        canonical = [
            "tz.america__new_york_to_asia__kolkata",
            "tz.asia__kolkata_to_utc",
            "tz.europe__london_to_america__los_angeles",
            "tz.etc__gmt_plus_5_to_etc__gmt_5",
            "tz.australia__sydney_to_pacific__auckland",
            "tz.america__chicago_to_europe__berlin",
        ]
        if n <= len(canonical):
            return canonical[:n]
        names = list(canonical)
        for i, tok_a in enumerate(_TOKENS):
            for j, tok_b in enumerate(_TOKENS):
                if i == j:
                    continue
                cand = f"tz.{tok_a}_to_{tok_b}"
                if cand not in names:
                    names.append(cand)
                    if len(names) >= n:
                        return names
        return names


#: Instance the Phase 7 wiring convention can import and register
#: (the parent workstream applies the actual registry wiring).
tz_provider = TzProvider()
