"""Smart Home Tool Pack (Phase 6 / Pack 7): Home Assistant calls, MQTT
publishing, a local device registry, and config-driven scenes.

Per-tool toggles and backend credentials come from
~/workspace/jarvis/tools_config.yaml (see
packaging/config_fragments/home_pack.yaml for the fragment); a missing or
broken file falls back to sane defaults (all backends disabled).

Handlers take a dict and return a dict, and NEVER raise.
"""
from __future__ import annotations

import functools
import json
import os
import sqlite3
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

from ...config import DATA_DIR
from ...security import egress

JARVIS_DIR = Path.home() / "workspace" / "jarvis"
CONFIG_PATH = JARVIS_DIR / "tools_config.yaml"

_DEFAULTS = {
    "tools": {
        "home.call": {"enabled": True},
        "mqtt.publish": {"enabled": True},
        "devices.register": {"enabled": True},
        "devices.list": {"enabled": True},
        "scenes.run": {"enabled": True},
    },
    "homeassistant": {"enabled": False, "base_url": "http://homeassistant.local:8123", "token": ""},
    "mqtt": {"enabled": False, "host": "", "port": 1883, "username": "", "password": ""},
    "scenes": {},
}

_cfg_cache: dict | None = None
_cfg_mtime: float = 0.0

_KINDS = {"light", "switch", "sensor", "other"}
_VIAS = {"homeassistant", "mqtt", "virtual"}

# Tool metadata for registry wiring (name, risk, needs_network).
TOOLS = [
    {"name": "home.call", "handler": "home_call", "risk": "high", "needs_network": True},
    {"name": "mqtt.publish", "handler": "mqtt_publish", "risk": "high", "needs_network": True},
    {"name": "devices.register", "handler": "devices_register", "risk": "low", "needs_network": False},
    {"name": "devices.list", "handler": "devices_list", "risk": "low", "needs_network": False},
    {"name": "scenes.run", "handler": "scenes_run", "risk": "medium", "needs_network": False},
]


def _load_config() -> dict:
    """Read tools_config.yaml at call time; cache by mtime, tolerate failure."""
    global _cfg_cache, _cfg_mtime
    try:
        mtime = CONFIG_PATH.stat().st_mtime
    except OSError:
        return _DEFAULTS
    if _cfg_cache is not None and mtime == _cfg_mtime:
        return _cfg_cache
    try:
        import yaml  # pyyaml ships with the sidecar env
        raw = yaml.safe_load(CONFIG_PATH.read_text()) or {}
        cfg = _deep_merge(dict(_DEFAULTS), raw)
    except Exception:
        cfg = _DEFAULTS
    _cfg_cache = cfg
    _cfg_mtime = mtime
    return cfg


def _deep_merge(base: dict, over: dict) -> dict:
    out = dict(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _enabled(tool_name: str) -> dict | None:
    """Return an error dict when the tool is disabled, else None."""
    tools = _load_config().get("tools", {})
    if not tools.get(tool_name, {}).get("enabled", True):
        return {"error": f"tool '{tool_name}' is disabled in tools_config.yaml"}
    return None


def _no_raise(fn):
    """Decorator: handlers take a dict and return a dict, NEVER raise."""
    @functools.wraps(fn)
    def wrapper(args: dict) -> dict:
        try:
            return fn(args or {})
        except Exception as e:
            return {"error": f"{fn.__name__} failed: {type(e).__name__}: {e}"}
    return wrapper


def _db() -> sqlite3.Connection:
    """Local device registry at DATA_DIR/home.db (JARVIS_DATA respected)."""
    data_dir = Path(os.environ.get("JARVIS_DATA") or DATA_DIR)
    data_dir.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(data_dir / "home.db"))
    con.execute(
        """CREATE TABLE IF NOT EXISTS devices (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               name TEXT UNIQUE NOT NULL,
               kind TEXT NOT NULL,
               via TEXT NOT NULL,
               address TEXT NOT NULL DEFAULT '',
               actions TEXT NOT NULL DEFAULT '[]',
               created_at INTEGER NOT NULL)""")
    con.execute(
        """CREATE TABLE IF NOT EXISTS scene_runs (
               id INTEGER PRIMARY KEY AUTOINCREMENT,
               scene TEXT NOT NULL,
               step_index INTEGER NOT NULL,
               device TEXT NOT NULL,
               action TEXT NOT NULL,
               ok INTEGER NOT NULL,
               result TEXT NOT NULL,
               ran_at INTEGER NOT NULL)""")
    return con


# ---------------------------------------------------------------- home.call
def _ha_ready() -> str | None:
    """Error string when the Home Assistant backend is unusable, else None."""
    cfg = _load_config().get("homeassistant", {}) or {}
    if not cfg.get("enabled"):
        return "not configured: fill in the homeassistant: section of tools_config.yaml"
    if not str(cfg.get("base_url", "") or "").strip():
        return "not configured: homeassistant.base_url is empty in tools_config.yaml"
    if not str(cfg.get("token", "") or ""):
        return "not configured: fill in the homeassistant: section of tools_config.yaml"
    return None


@_no_raise
def home_call(args: dict) -> dict:
    """Call a Home Assistant service: POST /api/services/{domain}/{service}.

    Args: {domain, service, entity_id?, data?}.
    """
    blocked = _enabled("home.call")
    if blocked:
        return blocked
    ready_err = _ha_ready()
    if ready_err:
        return {"error": ready_err}
    cfg = _load_config().get("homeassistant", {}) or {}
    base_url = str(cfg.get("base_url")).strip().rstrip("/")
    token = str(cfg.get("token") or "")
    domain = str(args.get("domain", "") or "").strip()
    service = str(args.get("service", "") or "").strip()
    if not domain or not service:
        return {"error": "domain and service are required"}
    host = urllib.parse.urlparse(base_url).hostname or ""
    if not egress.check("home.call", host):
        return {"error": f"egress denied: home.call -> {host} is not allowlisted"}
    payload: dict = dict(args.get("data") or {})
    if args.get("entity_id"):
        payload["entity_id"] = args["entity_id"]
    url = f"{base_url}/api/services/{domain}/{service}"
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"), method="POST",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", "replace")
        status = getattr(resp, "status", 0)
    try:
        return {"response": json.loads(body), "status": status}
    except Exception:
        return {"response": body, "status": status}


# -------------------------------------------------------------- mqtt.publish
def _mqtt_ready() -> str | None:
    """Error string when the MQTT backend is unusable, else None."""
    cfg = _load_config().get("mqtt", {}) or {}
    if not cfg.get("enabled"):
        return "not configured: fill in the mqtt: section of tools_config.yaml"
    if not str(cfg.get("host", "") or "").strip():
        return "not configured: mqtt.host is empty in tools_config.yaml"
    return None


@_no_raise
def mqtt_publish(args: dict) -> dict:
    """Publish to an MQTT broker. Args: {topic, payload, qos? (0-2), retain?}."""
    blocked = _enabled("mqtt.publish")
    if blocked:
        return blocked
    ready_err = _mqtt_ready()
    if ready_err:
        return {"error": ready_err}
    cfg = _load_config().get("mqtt", {}) or {}
    host = str(cfg.get("host") or "").strip()
    try:
        port = int(cfg.get("port", 1883))
    except (TypeError, ValueError):
        port = 1883
    topic = str(args.get("topic", "") or "").strip()
    if not topic:
        return {"error": "topic is required"}
    payload = args.get("payload", "")
    if isinstance(payload, (dict, list)):
        payload = json.dumps(payload)
    try:
        qos = max(0, min(2, int(args.get("qos", 0))))
    except (TypeError, ValueError):
        qos = 0
    retain = bool(args.get("retain", False))
    try:
        import paho.mqtt.client as paho
    except ImportError:
        return {"error": "mqtt needs paho-mqtt: pip install paho-mqtt"}
    if not egress.check("mqtt.publish", host):
        return {"error": f"egress denied: mqtt.publish -> {host} is not allowlisted"}

    client = paho.Client()
    if str(cfg.get("username") or ""):
        client.username_pw_set(str(cfg["username"]), str(cfg.get("password") or ""))
    connect_err: list[str] = []

    def _connect():
        try:
            client.connect(host, port, keepalive=60)
        except Exception as e:  # noqa: BLE001 - reported below
            connect_err.append(f"{type(e).__name__}: {e}")

    t = threading.Thread(target=_connect, daemon=True)
    t.start()
    t.join(5)  # 5s connect timeout
    if t.is_alive() or connect_err:
        return {"error": f"mqtt connect to {host}:{port} failed"
                f" ({connect_err[0] if connect_err else 'timed out after 5s'})"}
    try:
        info = client.publish(topic, payload, qos=qos, retain=retain)
        info.wait_for_publish(5)  # 5s publish timeout
        if getattr(info, "rc", 1) != 0:
            return {"error": f"mqtt publish failed: rc={getattr(info, 'rc', '?')}"}
        return {"published": True, "topic": topic, "qos": qos, "retain": retain}
    finally:
        try:
            client.disconnect()
        except Exception:
            pass


# ----------------------------------------------------------- devices.register
@_no_raise
def devices_register(args: dict) -> dict:
    """Register a device in the local registry. Args:
    {name, kind (light/switch/sensor/other), via (homeassistant|mqtt|virtual),
     address? (entity_id or topic), actions? ([strings])}."""
    blocked = _enabled("devices.register")
    if blocked:
        return blocked
    name = str(args.get("name", "") or "").strip()
    kind = str(args.get("kind", "") or "").strip().lower()
    via = str(args.get("via", "") or "").strip().lower()
    if not name:
        return {"error": "name is required"}
    if kind not in _KINDS:
        return {"error": f"kind must be one of {sorted(_KINDS)}"}
    if via not in _VIAS:
        return {"error": f"via must be one of {sorted(_VIAS)}"}
    address = str(args.get("address") or "")
    actions = args.get("actions") or []
    if not isinstance(actions, list):
        actions = [actions]
    con = _db()
    try:
        row = con.execute("SELECT id FROM devices WHERE name = ?", (name,)).fetchone()
        now = int(time.time())
        if row:
            con.execute(
                "UPDATE devices SET kind = ?, via = ?, address = ?, actions = ? WHERE name = ?",
                (kind, via, address, json.dumps([str(a) for a in actions]), name))
            con.commit()
            return {"id": row[0], "name": name, "updated": True}
        cur = con.execute(
            "INSERT INTO devices (name, kind, via, address, actions, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (name, kind, via, address, json.dumps([str(a) for a in actions]), now))
        con.commit()
        return {"id": cur.lastrowid, "name": name, "kind": kind, "via": via}
    finally:
        con.close()


# --------------------------------------------------------------- devices.list
@_no_raise
def devices_list(args: dict) -> dict:
    """List registered devices. Args: {kind?}."""
    blocked = _enabled("devices.list")
    if blocked:
        return blocked
    kind = str(args.get("kind", "") or "").strip().lower()
    if kind and kind not in _KINDS:
        return {"error": f"kind must be one of {sorted(_KINDS)}"}
    con = _db()
    try:
        if kind:
            rows = con.execute(
                "SELECT id, name, kind, via, address, actions FROM devices WHERE kind = ?"
                " ORDER BY name", (kind,)).fetchall()
        else:
            rows = con.execute(
                "SELECT id, name, kind, via, address, actions FROM devices ORDER BY name").fetchall()
        return {"devices": [
            {"id": r[0], "name": r[1], "kind": r[2], "via": r[3],
             "address": r[4], "actions": json.loads(r[5])} for r in rows]}
    finally:
        con.close()


# ------------------------------------------------------------------ scenes.run
def _find_device(name: str) -> dict | None:
    con = _db()
    try:
        r = con.execute(
            "SELECT id, name, kind, via, address, actions FROM devices WHERE name = ?",
            (name,)).fetchone()
        return ({"id": r[0], "name": r[1], "kind": r[2], "via": r[3],
                 "address": r[4], "actions": json.loads(r[5])} if r else None)
    finally:
        con.close()


def _scene_step(device_name: str, action: str, via_args: dict) -> dict:
    """Execute one scene step; honest about unconfigured backends."""
    if not device_name or not action:
        return {"device": device_name, "action": action, "ok": False,
                "error": "step needs both device and action"}
    dev = _find_device(device_name)
    if not dev:
        return {"device": device_name, "action": action, "ok": False,
                "error": f"device '{device_name}' is not registered"}
    via = dev["via"]
    if via == "virtual":
        return {"device": device_name, "action": action, "ok": True,
                "virtual": True, "note": "no physical backend; marked executed virtually"}
    if via == "homeassistant":
        ready_err = _ha_ready()
        if ready_err:
            return {"device": device_name, "action": action, "ok": False,
                    "via": "homeassistant", "error": ready_err}
        out = home_call({
            "domain": via_args.get("domain", ""),
            "service": via_args.get("service", ""),
            "entity_id": dev["address"],
            "data": via_args.get("data", {}),
        })
        step = {"device": device_name, "action": action, "via": "homeassistant",
                "ok": "error" not in out}
        step.update({"error": out["error"]} if "error" in out else {"response": out})
        return step
    if via == "mqtt":
        ready_err = _mqtt_ready()
        if ready_err:
            return {"device": device_name, "action": action, "ok": False,
                    "via": "mqtt", "error": ready_err}
        out = mqtt_publish({
            "topic": dev["address"],
            "payload": via_args.get("payload", action),
            "qos": via_args.get("qos", 0),
            "retain": via_args.get("retain", False),
        })
        step = {"device": device_name, "action": action, "via": "mqtt",
                "ok": "error" not in out}
        step.update({"error": out["error"]} if "error" in out else {"published": out})
        return step
    return {"device": device_name, "action": action, "ok": False,
            "error": f"unknown via '{via}' on device '{device_name}'"}


@_no_raise
def scenes_run(args: dict) -> dict:
    """Run a named scene from tools_config.yaml `scenes:`. Args: {scene}."""
    blocked = _enabled("scenes.run")
    if blocked:
        return blocked
    scene = str(args.get("scene", "") or "").strip()
    if not scene:
        return {"error": "scene is required"}
    scenes = _load_config().get("scenes", {}) or {}
    steps_cfg = scenes.get(scene)
    if not steps_cfg:
        return {"error": f"scene '{scene}' is not defined in tools_config.yaml"}
    if not isinstance(steps_cfg, list):
        return {"error": f"scene '{scene}' must be a list of steps"}
    results: list[dict] = []
    con = _db()
    try:
        for i, step_cfg in enumerate(steps_cfg):
            step_cfg = step_cfg if isinstance(step_cfg, dict) else {}
            r = _scene_step(str(step_cfg.get("device", "")),
                            str(step_cfg.get("action", "")),
                            dict(step_cfg.get("via_args") or {}))
            results.append(r)
            con.execute(
                "INSERT INTO scene_runs (scene, step_index, device, action, ok, result, ran_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (scene, i, r.get("device", ""), r.get("action", ""),
                 1 if r.get("ok") else 0, json.dumps(r), int(time.time())))
        con.commit()
    finally:
        con.close()
    return {"scene": scene, "steps": results}


# ------------------------------------------------------------------ wiring
def register(reg) -> None:
    """Wire the five home pack tools into a Registry (call from builtin.__init__)."""
    from ..base import Tool
    _HANDLERS = {"home_call": home_call, "mqtt_publish": mqtt_publish,
                 "devices_register": devices_register, "devices_list": devices_list,
                 "scenes_run": scenes_run}
    _SCHEMAS = {
        "home.call": {"domain": "string", "service": "string", "entity_id": "string?",
                      "data": "dict?"},
        "mqtt.publish": {"topic": "string", "payload": "string|dict", "qos": "int?",
                         "retain": "bool?"},
        "devices.register": {"name": "string", "kind": "light|switch|sensor|other",
                             "via": "homeassistant|mqtt|virtual", "address": "string?",
                             "actions": "list?"},
        "devices.list": {"kind": "string?"},
        "scenes.run": {"scene": "string"},
    }
    _DESCS = {
        "home.call": "Call a Home Assistant service. HIGH risk — needs confirmation.",
        "mqtt.publish": "Publish a message to an MQTT broker. HIGH risk — needs confirmation.",
        "devices.register": "Register a device in the local smart-home registry.",
        "devices.list": "List registered smart-home devices.",
        "scenes.run": "Run a named scene from tools_config.yaml `scenes:` against the device registry.",
    }
    for meta in TOOLS:
        reg.register(Tool(meta["name"], _DESCS[meta["name"]], _SCHEMAS[meta["name"]],
                           _HANDLERS[meta["handler"]], risk=meta["risk"],
                           needs_network=meta["needs_network"]))
