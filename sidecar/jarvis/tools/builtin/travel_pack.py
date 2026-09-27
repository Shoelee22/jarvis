"""Travel Tool Pack (Phase 10): itinerary + packing-list templates, flight search.

Honest design:
- travel.itinerary and travel.packing_list are STRUCTURED TEMPLATES built
  from generic logic. They are labeled as template starters, NOT live data —
  no hotels, prices, timings, or availability are invented.
- travel.flight_search does NOT fake results. It needs a Duffel API key in
  ~/workspace/jarvis/tools_config.yaml under travel: duffel_api_key: —
  without it, it returns an honest error with exact setup steps.
"""
from __future__ import annotations

import json
import urllib.request
from pathlib import Path

HOME = Path.home()
CONFIG_PATH = HOME / "workspace" / "jarvis" / "tools_config.yaml"
_DUFFEL_HOST = "api.duffel.com"

_DUFFEL_SETUP_ERROR = (
    "flight search is not configured: no Duffel API key found. Setup: "
    "1) create a free account at https://dashboard.duffel.com and copy your "
    "test API key; "
    "2) add it to ~/workspace/jarvis/tools_config.yaml under "
    "'travel:' -> 'duffel_api_key: YOUR_KEY'; "
    "3) ask to allowlist 'api.duffel.com' for travel.flight_search in "
    "jarvis/security/egress.py. No fake results are returned."
)


# ------------------------------------------------------------ helpers
def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _days(value) -> int | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return n if 1 <= n <= 60 else None


_INTEREST_IDEAS = {
    "food": "local food walk / street-food trail",
    "history": "old town, heritage walk, local museum",
    "nature": "park, viewpoint, nature trail",
    "adventure": "day trek or outdoor activity with a licensed operator",
    "shopping": "local market / bazaar",
    "nightlife": "evening district (verify closing times locally)",
    "art": "galleries / art district",
    "beach": "beach time (check seasonal conditions)",
    "culture": "cultural performance or festival calendar",
    "relaxation": "slow morning, cafe-hopping",
}


# ------------------------------------------------- travel.itinerary
def travel_itinerary(args: dict) -> dict:
    """Day-by-day TEMPLATE itinerary (starter, not live data)."""
    try:
        destination = str(args.get("destination", "")).strip()
        if not destination:
            return {"error": "destination is required"}
        n = _days(args.get("days", 3))
        if n is None:
            return {"error": "days must be an integer between 1 and 60"}
        raw_interests = args.get("interests") or []
        interests = [str(i).strip().lower() for i in raw_interests
                     if str(i).strip()][:5]
        ideas = [_INTEREST_IDEAS.get(i, f"explore {i} locally") for i in interests]
        if not ideas:
            ideas = ["city orientation walk", "local landmarks"]

        plan = []
        for d in range(1, n + 1):
            if d == 1:
                theme = "arrival + orientation"
                morning = "arrive, check in, rest"
                afternoon = "orientation walk around your stay; " + ideas[0]
                evening = "early dinner nearby; rest for jet lag"
            elif d == n and n > 1:
                theme = "wrap-up + departure"
                morning = ideas[-1] + " (short)"
                afternoon = "souvenirs, pack, check out"
                evening = "depart / airport transfer"
            else:
                idea = ideas[(d - 2) % len(ideas)]
                theme = idea
                morning = f"{idea} (start early to beat crowds)"
                afternoon = "rest, then " + ideas[(d - 1) % len(ideas)]
                evening = "dinner; evening stroll"
            plan.append({"day": d, "theme": theme, "morning": morning,
                         "afternoon": afternoon, "evening": evening})
        return {"destination": destination, "days": n, "interests": interests,
                "itinerary": plan,
                "honest_note": "TEMPLATE STARTER — not live data. Hours, "
                               "prices, availability, and transit must be "
                               "verified locally before booking anything."}
    except Exception as e:
        return _fail("travel.itinerary", e)


# ----------------------------------------------- travel.packing_list
_TRIP_EXTRAS = {
    "beach": ["swimwear", "sunscreen SPF 50+", "beach towel", "flip-flops",
              "waterproof pouch"],
    "city": ["comfortable walking shoes", "daypack", "power bank",
             "offline maps downloaded"],
    "adventure": ["hiking shoes", "rain shell", "headlamp", "water bottle",
                  "basic first-aid kit"],
    "business": ["laptop + charger", "formal wear", "business cards",
                 "travel adapter"],
    "mountains": ["warm layers", "thermals", "gloves + beanie",
                  "moisturizer + lip balm"],
}


def travel_packing_list(args: dict) -> dict:
    """Packing list built from real list logic (days + trip type)."""
    try:
        destination = str(args.get("destination", "")).strip()
        if not destination:
            return {"error": "destination is required"}
        n = _days(args.get("days", 5))
        if n is None:
            return {"error": "days must be an integer between 1 and 60"}
        trip_type = str(args.get("trip_type", "city")).strip().lower()
        if trip_type not in _TRIP_EXTRAS:
            return {"error": f"trip_type must be one of: "
                             f"{', '.join(sorted(_TRIP_EXTRAS))}"}

        def _n(base: int, per_day: float = 1.0, cap: int = 10) -> int:
            return max(base, min(cap, int(-(-n * per_day // 1))))

        clothing = [f"tops x {_n(3)}", f"bottoms x {_n(2)}",
                    f"underwear x {_n(4, cap=14)}", f"socks x {_n(4, cap=14)}",
                    "sleepwear x 1-2", "1 warm layer", "1 rain layer"]
        essentials = ["passport / ID", "tickets + hotel confirmations",
                      "wallet + cards", "phone + charger", "travel adapter",
                      "toiletries", "medicines (personal)", "snacks + water"]
        extras = _TRIP_EXTRAS[trip_type]
        return {"destination": destination, "days": n, "trip_type": trip_type,
                "clothing": clothing, "essentials": essentials,
                "trip_extras": extras,
                "honest_note": "generic template list — adjust for season, "
                               "weather forecast, and airline baggage rules"}
    except Exception as e:
        return _fail("travel.packing_list", e)


# ---------------------------------------------- travel.flight_search
def _load_config() -> dict:
    """Read ~/workspace/jarvis/tools_config.yaml at call time; {} on failure."""
    try:
        raw = CONFIG_PATH.read_text()
    except OSError:
        return {}
    try:
        import yaml  # pyyaml ships with the sidecar env
        data = yaml.safe_load(raw) or {}
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    tv = data.get("travel", {})
    return tv if isinstance(tv, dict) else {}


def travel_flight_search(args: dict) -> dict:
    """Real Duffel offer search — gated on an API key; never faked."""
    try:
        cfg = _load_config()
        key = cfg.get("duffel_api_key")
        if not isinstance(key, str) or not key.strip():
            return {"error": _DUFFEL_SETUP_ERROR}
        origin = str(args.get("origin", "")).strip().upper()
        destination = str(args.get("destination", "")).strip().upper()
        date = str(args.get("date", "")).strip()
        if not origin or not destination or not date:
            return {"error": "origin, destination, and date are required "
                             "(IATA codes, date as YYYY-MM-DD)"}
        try:
            from ...security import egress
            if not egress.check("travel.flight_search", _DUFFEL_HOST):
                return {"error": "egress blocked: api.duffel.com is not "
                                 "allowlisted for travel.flight_search; add "
                                 "\"api.duffel.com\" to ALLOWLIST in "
                                 "jarvis/security/egress.py"}
        except ImportError:
            pass  # running outside the sidecar: skip egress gate
        body = json.dumps({"data": {"slices": [{"origin": origin,
                                                "destination": destination,
                                                "departure_date": date}],
                                      "passengers": [{"type": "adult"}],
                                      "max_connections": 1}}).encode()
        req = urllib.request.Request(
            f"https://{_DUFFEL_HOST}/air/offer_requests",
            data=body,
            headers={"Authorization": f"Bearer {key.strip()}",
                     "Content-Type": "application/json",
                     "Accept": "application/json",
                     "Duffel-Version": "v2"})
        try:
            with urllib.request.urlopen(req, timeout=20) as resp:
                payload = json.loads(resp.read().decode("utf-8", "replace"))
        except Exception as e:
            return {"error": f"Duffel request failed: "
                             f"{type(e).__name__}: {e}"}
        offers = []
        for offer in (payload.get("data") or {}).get("offers", [])[:10]:
            offers.append({"total_amount": offer.get("total_amount"),
                           "total_currency": offer.get("total_currency"),
                           "airline": (offer.get("owner") or {}).get("name"),
                           "stops": offer.get("slices", [{}])[0].get("segments",
                                                                    [])})
        return {"origin": origin, "destination": destination, "date": date,
                "offers": offers, "offer_count": len(offers),
                "source": "Duffel live API"}
    except Exception as e:
        return _fail("travel.flight_search", e)


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "travel.itinerary",
     "description": "Day-by-day TEMPLATE itinerary starter for a destination "
                    "(not live data; labeled honestly).",
     "handler": travel_itinerary, "risk": "low", "needs_network": False,
     "schema": {"destination": "string", "days": "int?",
                "interests": "list?"}},
    {"name": "travel.packing_list",
     "description": "Packing list from days + trip type (template logic).",
     "handler": travel_packing_list, "risk": "low", "needs_network": False,
     "schema": {"destination": "string", "days": "int?",
                "trip_type": "string?"}},
    {"name": "travel.flight_search",
     "description": "Real Duffel flight offer search (needs API key in "
                    "tools_config.yaml; never faked).",
     "handler": travel_flight_search, "risk": "low", "needs_network": True,
     "schema": {"origin": "string", "destination": "string", "date": "string"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(travel_pack.RISK_TABLE_ADDITIONS)
# Network egress note for the parent: travel.flight_search also needs
# "travel.flight_search": ["api.duffel.com"] in jarvis/security/egress.py
# ALLOWLIST for the live call to be allowed.
RISK_TABLE_ADDITIONS = {
    "travel.itinerary": ("low", False),
    "travel.packing_list": ("low", False),
    "travel.flight_search": ("low", True),
}


def register(reg) -> None:
    """Wire the three travel tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))


__all__ = ["TOOL_DEFS", "RISK_TABLE_ADDITIONS", "register",
           "travel_itinerary", "travel_packing_list", "travel_flight_search"]
