"""Unit tests for the Phase 6 / Pack 7 Smart Home Tool Pack. All offline:
urllib and paho are mocked; the device registry uses a real sqlite db in a
tmp dir (via JARVIS_DATA)."""
import json
import sys
import types
from pathlib import Path

sys.path.insert(0, "sidecar")

import pytest  # noqa: E402

from jarvis.tools.base import Registry  # noqa: E402
from jarvis.tools.builtin import home_pack  # noqa: E402
from jarvis.security import egress  # noqa: E402


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    # Point the registry sqlite at a fresh tmp dir and disable egress
    # (default-deny would block every fake host).
    monkeypatch.setenv("JARVIS_DATA", str(tmp_path))
    egress.set_enabled(False)
    yield
    egress.set_enabled(True)


def _cfg(**overrides):
    cfg = json.loads(json.dumps(home_pack._DEFAULTS))  # deep copy

    def merge(dst, src):
        for k, v in src.items():
            if isinstance(v, dict) and isinstance(dst.get(k), dict):
                merge(dst[k], v)
            else:
                dst[k] = v

    merge(cfg, overrides)
    return cfg


def _use_cfg(monkeypatch, **overrides):
    cfg = _cfg(**overrides)
    monkeypatch.setattr(home_pack, "_load_config", lambda: cfg)
    return cfg


# --------------------------------------------------------------- home.call
def test_home_call_not_configured(monkeypatch):
    _use_cfg(monkeypatch)  # homeassistant.enabled is False by default
    r = home_pack.home_call({"domain": "light", "service": "turn_on"})
    assert r == {"error": "not configured: fill in the homeassistant: section of tools_config.yaml"}


def test_home_call_missing_token(monkeypatch):
    _use_cfg(monkeypatch, homeassistant={"enabled": True, "base_url": "http://ha.local:8123", "token": ""})
    r = home_pack.home_call({"domain": "light", "service": "turn_on"})
    assert r == {"error": "not configured: fill in the homeassistant: section of tools_config.yaml"}


class _FakeResp:
    status = 200

    def read(self):
        return b'[{"entity_id": "light.lamp"}]'

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_home_call_url_auth_body(monkeypatch):
    _use_cfg(monkeypatch, homeassistant={"enabled": True, "base_url": "http://ha.local:8123", "token": "tok123"})
    seen = []

    def fake_urlopen(req, timeout=None):
        seen.append(req)
        return _FakeResp()

    monkeypatch.setattr(home_pack.urllib.request, "urlopen", fake_urlopen)
    r = home_pack.home_call({"domain": "light", "service": "turn_on",
                             "entity_id": "light.lamp", "data": {"brightness": 200}})
    assert r == {"response": [{"entity_id": "light.lamp"}], "status": 200}
    assert len(seen) == 1
    req = seen[0]
    assert req.full_url == "http://ha.local:8123/api/services/light/turn_on"
    assert req.get_header("Authorization") == "Bearer tok123"
    assert req.get_header("Content-type") == "application/json"
    body = json.loads(req.data.decode("utf-8"))
    assert body == {"brightness": 200, "entity_id": "light.lamp"}


def test_home_call_requires_domain_service(monkeypatch):
    _use_cfg(monkeypatch, homeassistant={"enabled": True, "base_url": "http://ha.local:8123", "token": "t"})
    assert home_pack.home_call({"service": "turn_on"})["error"] == "domain and service are required"


def test_home_call_egress_denied(monkeypatch):
    _use_cfg(monkeypatch, homeassistant={"enabled": True, "base_url": "http://ha.local:8123", "token": "t"})
    egress.set_enabled(True)  # re-enable for this test; fixture restores after
    r = home_pack.home_call({"domain": "light", "service": "turn_on"})
    assert "egress denied" in r["error"]
    assert egress.violations()


# ------------------------------------------------------------ mqtt.publish
def _fake_paho(monkeypatch):
    calls = {}

    class FakeInfo:
        rc = 0

        def wait_for_publish(self, timeout):
            calls["wait_timeout"] = timeout

    class FakeClient:
        def connect(self, host, port, keepalive=60):
            calls["connect"] = (host, port, keepalive)

        def username_pw_set(self, user, pw=None):
            calls["auth"] = (user, pw)

        def publish(self, topic, payload, qos=0, retain=False):
            calls["publish"] = (topic, payload, qos, retain)
            return FakeInfo()

        def disconnect(self):
            calls["disconnect"] = True

    mod = types.ModuleType("paho.mqtt.client")
    mod.Client = FakeClient
    # Register the parent packages too, so `import paho.mqtt.client`
    # resolves against the fake even when real paho is not installed.
    paho_pkg = types.ModuleType("paho")
    paho_pkg.__path__ = []
    mqtt_pkg = types.ModuleType("paho.mqtt")
    mqtt_pkg.__path__ = []
    mqtt_pkg.client = mod
    paho_pkg.mqtt = mqtt_pkg
    monkeypatch.setitem(sys.modules, "paho", paho_pkg)
    monkeypatch.setitem(sys.modules, "paho.mqtt", mqtt_pkg)
    monkeypatch.setitem(sys.modules, "paho.mqtt.client", mod)
    return calls


def test_mqtt_not_configured(monkeypatch):
    _use_cfg(monkeypatch)  # mqtt.enabled is False by default
    r = home_pack.mqtt_publish({"topic": "home/lamp", "payload": "ON"})
    assert r == {"error": "not configured: fill in the mqtt: section of tools_config.yaml"}


def test_mqtt_missing_paho(monkeypatch):
    _use_cfg(monkeypatch, mqtt={"enabled": True, "host": "mqtt.local"})
    monkeypatch.setitem(sys.modules, "paho", None)  # import -> ImportError
    r = home_pack.mqtt_publish({"topic": "home/lamp", "payload": "ON"})
    assert r == {"error": "mqtt needs paho-mqtt: pip install paho-mqtt"}


def test_mqtt_publish_success(monkeypatch):
    _use_cfg(monkeypatch, mqtt={"enabled": True, "host": "mqtt.local", "port": 1883,
                                "username": "u", "password": "p"})
    calls = _fake_paho(monkeypatch)
    r = home_pack.mqtt_publish({"topic": "home/lamp", "payload": "ON", "qos": 5, "retain": True})
    assert r == {"published": True, "topic": "home/lamp", "qos": 2, "retain": True}  # qos clamped 0-2
    assert calls["connect"] == ("mqtt.local", 1883, 60)
    assert calls["auth"] == ("u", "p")
    assert calls["publish"] == ("home/lamp", "ON", 2, True)
    assert calls["wait_timeout"] == 5
    assert calls["disconnect"] is True


def test_mqtt_dict_payload_json(monkeypatch):
    _use_cfg(monkeypatch, mqtt={"enabled": True, "host": "mqtt.local"})
    calls = _fake_paho(monkeypatch)
    r = home_pack.mqtt_publish({"topic": "home/t", "payload": {"state": "on"}})
    assert r["published"] is True
    assert calls["publish"][1] == '{"state": "on"}'


def test_mqtt_requires_topic(monkeypatch):
    _use_cfg(monkeypatch, mqtt={"enabled": True, "host": "mqtt.local"})
    assert home_pack.mqtt_publish({"payload": "ON"})["error"] == "topic is required"


# -------------------------------------------------------- devices registry
def test_devices_register_and_list(monkeypatch):
    _use_cfg(monkeypatch)
    r1 = home_pack.devices_register({"name": "Lamp", "kind": "light", "via": "virtual",
                                     "actions": ["on", "off"]})
    assert r1["id"] > 0 and r1["kind"] == "light"
    r2 = home_pack.devices_register({"name": "Blinds", "kind": "switch", "via": "mqtt",
                                     "address": "home/blinds"})
    assert r2["id"] != r1["id"]
    all_devs = home_pack.devices_list({})
    assert {d["name"] for d in all_devs["devices"]} == {"Lamp", "Blinds"}
    lamp = next(d for d in all_devs["devices"] if d["name"] == "Lamp")
    assert lamp["actions"] == ["on", "off"]
    lights = home_pack.devices_list({"kind": "light"})
    assert [d["name"] for d in lights["devices"]] == ["Lamp"]


def test_devices_register_updates_existing(monkeypatch):
    _use_cfg(monkeypatch)
    r1 = home_pack.devices_register({"name": "Lamp", "kind": "light", "via": "virtual"})
    r2 = home_pack.devices_register({"name": "Lamp", "kind": "light", "via": "mqtt",
                                     "address": "home/lamp"})
    assert r2["id"] == r1["id"] and r2["updated"] is True
    devs = home_pack.devices_list({})["devices"]
    assert len(devs) == 1 and devs[0]["via"] == "mqtt"


def test_devices_register_validation(monkeypatch):
    _use_cfg(monkeypatch)
    assert home_pack.devices_register({"kind": "light", "via": "virtual"})["error"] == "name is required"
    assert "kind must be" in home_pack.devices_register({"name": "X", "kind": "lamp", "via": "virtual"})["error"]
    assert "via must be" in home_pack.devices_register({"name": "X", "kind": "light", "via": "zigbee"})["error"]
    assert "kind must be" in home_pack.devices_list({"kind": "nope"})["error"]


# --------------------------------------------------------------- scenes.run
def test_scenes_run_virtual(monkeypatch, tmp_path):
    _use_cfg(monkeypatch,
             scenes={"movie_night": [
                 {"device": "Lamp", "action": "off"},
                 {"device": "Screen", "action": "lower"}]})
    home_pack.devices_register({"name": "Lamp", "kind": "light", "via": "virtual"})
    home_pack.devices_register({"name": "Screen", "kind": "switch", "via": "virtual"})
    r = home_pack.scenes_run({"scene": "movie_night"})
    assert r["scene"] == "movie_night"
    assert len(r["steps"]) == 2
    assert all(s["ok"] and s["virtual"] for s in r["steps"])
    assert [s["action"] for s in r["steps"]] == ["off", "lower"]
    # every run logged to sqlite
    import sqlite3
    con = sqlite3.connect(str(tmp_path / "home.db"))
    rows = con.execute("SELECT scene, step_index, device, action, ok FROM scene_runs ORDER BY step_index").fetchall()
    con.close()
    assert rows == [("movie_night", 0, "Lamp", "off", 1),
                    ("movie_night", 1, "Screen", "lower", 1)]


def test_scenes_run_unconfigured_backend_is_honest(monkeypatch):
    _use_cfg(monkeypatch,
             scenes={"evening": [{"device": "Blinds", "action": "close"}]})
    home_pack.devices_register({"name": "Blinds", "kind": "switch", "via": "mqtt",
                                "address": "home/blinds"})
    r = home_pack.scenes_run({"scene": "evening"})
    step = r["steps"][0]
    assert step["ok"] is False
    assert "not configured" in step["error"]  # honest per-step error, not fake success


def test_scenes_run_unknown_device_and_scene(monkeypatch):
    _use_cfg(monkeypatch, scenes={"evening": [{"device": "Ghost", "action": "on"}]})
    r = home_pack.scenes_run({"scene": "evening"})
    assert r["steps"][0]["ok"] is False
    assert "not registered" in r["steps"][0]["error"]
    assert "not defined" in home_pack.scenes_run({"scene": "nope"})["error"]
    assert home_pack.scenes_run({})["error"] == "scene is required"


def test_handlers_never_raise(monkeypatch):
    _use_cfg(monkeypatch)
    assert "error" in home_pack.home_call(None)
    assert "error" in home_pack.mqtt_publish("nope")
    assert "error" in home_pack.devices_register({"name": None})
    assert "error" in home_pack.scenes_run({"scene": 123})


# ------------------------------------------------------------- registry wire
def test_register_wires_five_tools(monkeypatch):
    _use_cfg(monkeypatch)
    reg = Registry()
    home_pack.register(reg)
    specs = {s["name"]: s for s in reg.spec_list()}
    assert set(specs) == {"home.call", "mqtt.publish", "devices.register",
                          "devices.list", "scenes.run"}
    assert specs["home.call"]["risk"] == "high" and specs["home.call"]["needs_network"] is True
    assert specs["mqtt.publish"]["risk"] == "high" and specs["mqtt.publish"]["needs_network"] is True
    assert specs["devices.register"]["risk"] == "low" and specs["devices.register"]["needs_network"] is False
    assert specs["scenes.run"]["risk"] == "medium" and specs["scenes.run"]["needs_network"] is False
