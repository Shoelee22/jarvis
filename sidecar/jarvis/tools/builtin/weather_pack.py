"""Weather Tool Pack (Phase 10): current conditions + 5-day forecast.

REAL network calls to Open-Meteo (https://open-meteo.com) — no API key
needed. needs_network=True. Host is gated by jarvis/security/egress.py:
without an allowlist entry the tools return an honest blocked error.
Network failures (timeout, DNS, bad response) return honest errors too —
weather is never invented.
"""
from __future__ import annotations

import json
import urllib.parse
import urllib.request

_HOST = "api.open-meteo.com"
_BASE = f"https://{_HOST}/v1/forecast"
_TIMEOUT = 15

# WMO weather-code -> plain description (standard Open-Meteo code table).
_WMO = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "depositing rime fog",
    51: "light drizzle", 53: "moderate drizzle", 55: "dense drizzle",
    56: "light freezing drizzle", 57: "dense freezing drizzle",
    61: "slight rain", 63: "moderate rain", 65: "heavy rain",
    66: "light freezing rain", 67: "heavy freezing rain",
    71: "slight snow", 73: "moderate snow", 75: "heavy snow",
    77: "snow grains",
    80: "slight rain showers", 81: "moderate rain showers",
    82: "violent rain showers",
    85: "slight snow showers", 86: "heavy snow showers",
    95: "thunderstorm", 96: "thunderstorm with slight hail",
    99: "thunderstorm with heavy hail",
}


# ------------------------------------------------------------ helpers
def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _coords(args: dict):
    try:
        lat = float(args["latitude"])
        lon = float(args["longitude"])
    except KeyError:
        return None, None, "latitude and longitude are required"
    except (TypeError, ValueError):
        return None, None, "latitude/longitude must be numbers"
    if not (-90 <= lat <= 90) or not (-180 <= lon <= 180):
        return None, None, "latitude must be -90..90, longitude -180..180"
    return lat, lon, None


def _egress_ok(tool: str) -> str | None:
    """Return an error string when egress is blocked, else None."""
    try:
        from ...security import egress
    except ImportError:
        return None  # outside the sidecar: skip egress gate
    if not egress.check(tool, _HOST):
        return (f"egress blocked: {_HOST} is not allowlisted for {tool}; "
                f"add \"{_HOST}\" to the \"{tool}\" ALLOWLIST entries in "
                "jarvis/security/egress.py")
    return None


def _get(tool: str, params: dict):
    """GET the Open-Meteo API; returns (payload, error)."""
    blocked = _egress_ok(tool)
    if blocked:
        return None, blocked
    url = _BASE + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "jarvis-sidecar"})
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                return None, f"Open-Meteo returned HTTP {resp.status}"
            payload = json.loads(resp.read().decode("utf-8", "replace"))
    except Exception as e:
        return None, (f"weather request failed: {type(e).__name__}: {e}")
    if not isinstance(payload, dict):
        return None, "Open-Meteo returned an unexpected response"
    return payload, None


def _describe(code) -> str:
    try:
        return _WMO.get(int(code), f"unknown code {code}")
    except (TypeError, ValueError):
        return "unknown"


# ----------------------------------------------------- weather.now
def weather_now(args: dict) -> dict:
    """Current temperature, humidity, wind, conditions (Open-Meteo, live)."""
    try:
        lat, lon, err = _coords(args)
        if err:
            return {"error": err}
        payload, perr = _get("weather.now", {
            "latitude": lat, "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,"
                       "weather_code,wind_speed_10m",
            "timezone": "auto",
        })
        if perr:
            return {"error": perr}
        cur = payload.get("current") or {}
        code = cur.get("weather_code")
        return {"latitude": lat, "longitude": lon,
                "temperature_c": cur.get("temperature_2m"),
                "humidity_pct": cur.get("relative_humidity_2m"),
                "wind_speed_kmh": cur.get("wind_speed_10m"),
                "weather_code": code, "conditions": _describe(code),
                "observed_at": cur.get("time"),
                "timezone": payload.get("timezone"),
                "source": "Open-Meteo (live)"}
    except Exception as e:
        return _fail("weather.now", e)


# ------------------------------------------------ weather.forecast
def weather_forecast(args: dict) -> dict:
    """5-day daily max/min temperatures + conditions (Open-Meteo, live)."""
    try:
        lat, lon, err = _coords(args)
        if err:
            return {"error": err}
        try:
            days = int(args.get("days", 5))
        except (TypeError, ValueError):
            return {"error": "days must be an integer"}
        days = max(1, min(days, 7))
        payload, perr = _get("weather.forecast", {
            "latitude": lat, "longitude": lon,
            "daily": "temperature_2m_max,temperature_2m_min,weathercode",
            "forecast_days": days, "timezone": "auto",
        })
        if perr:
            return {"error": perr}
        daily = payload.get("daily") or {}
        dates = daily.get("time") or []
        hi = daily.get("temperature_2m_max") or []
        lo = daily.get("temperature_2m_min") or []
        codes = daily.get("weathercode") or []
        out = []
        for k in range(min(days, len(dates))):
            out.append({"date": dates[k],
                        "max_c": hi[k] if k < len(hi) else None,
                        "min_c": lo[k] if k < len(lo) else None,
                        "weather_code": codes[k] if k < len(codes) else None,
                        "conditions": _describe(codes[k])
                        if k < len(codes) else "unknown"})
        return {"latitude": lat, "longitude": lon, "days": out,
                "timezone": payload.get("timezone"),
                "source": "Open-Meteo (live)"}
    except Exception as e:
        return _fail("weather.forecast", e)


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "weather.now",
     "description": "Current temperature, humidity, wind, conditions for "
                    "lat/lon (live Open-Meteo, no key needed).",
     "handler": weather_now, "risk": "low", "needs_network": True,
     "schema": {"latitude": "number", "longitude": "number"}},
    {"name": "weather.forecast",
     "description": "Daily max/min + conditions for lat/lon, up to 7 days "
                    "(live Open-Meteo, no key needed).",
     "handler": weather_forecast, "risk": "low", "needs_network": True,
     "schema": {"latitude": "number", "longitude": "number", "days": "int?"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(weather_pack.RISK_TABLE_ADDITIONS)
# Network egress note for the parent: weather tools also need
# "weather.now": ["api.open-meteo.com"] and
# "weather.forecast": ["api.open-meteo.com"] in jarvis/security/egress.py
# ALLOWLIST for live calls to be allowed.
RISK_TABLE_ADDITIONS = {
    "weather.now": ("low", True),
    "weather.forecast": ("low", True),
}


def register(reg) -> None:
    """Wire the two weather tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))


__all__ = ["TOOL_DEFS", "RISK_TABLE_ADDITIONS", "register",
           "weather_now", "weather_forecast"]
