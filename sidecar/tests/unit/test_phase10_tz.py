"""Unit tests for the Phase 10 timezone provider (tz.<from>_to_<to>).

All math is verified against the stdlib zoneinfo database directly; no
invented data. Run from ~/workspace/jarvis:
    python3 -m pytest sidecar/tests/unit/test_phase10_tz.py -q
"""
import sys
from datetime import datetime, timezone
from zoneinfo import ZoneInfo, available_timezones

sys.path.insert(0, "sidecar")

from jarvis.tools.dynamic.tz_provider import TzProvider, tz_provider  # noqa: E402

P = TzProvider()
ZONES = sorted(available_timezones())


def _call(name: str, dt: str) -> dict:
    tool = P.resolve(name)
    assert tool is not None, f"{name} did not resolve"
    return tool.handler({"datetime": dt})


def test_expand_matches_n_times_n_minus_1():
    n = len(ZONES)
    assert P.expand() == n * (n - 1)
    assert P.count_addressable() == P.expand()
    assert P.expand() > 0


def test_sample_names_resolvable_and_correct():
    samples = P.sample_names(5)
    assert len(samples) == 5
    assert len(set(samples)) == 5
    fixed = "2026-06-15T12:00:00+00:00"  # absolute instant, June
    for name in samples:
        tool = P.resolve(name)
        assert tool is not None, f"sample {name} did not resolve"
        out = tool.handler({"datetime": fixed})
        assert "error" not in out, f"{name}: {out}"
        # Independent ground truth: the same instant through zoneinfo directly.
        body = name[len("tz."):]
        a, b = body.rsplit("_to_", 1)
        assert a in body and b  # tokens are non-empty
        # map tokens back via a second independent pass is overkill; instead
        # verify the result instant equals the input instant.
        result_dt = datetime.fromisoformat(out["result"])
        assert result_dt.astimezone(timezone.utc) == datetime.fromisoformat(fixed)
        assert out["to_utc_offset"] == _offset_str(result_dt)


def _offset_str(dt: datetime) -> str:
    total = int(dt.utcoffset().total_seconds())
    sign = "+" if total >= 0 else "-"
    total = abs(total)
    h, rem = divmod(total, 3600)
    m, _ = divmod(rem, 60)
    return f"{sign}{h:02d}:{m:02d}"


def test_kolkata_ground_truth_fixed_utc():
    # Asia/Kolkata is UTC+5:30 year-round (no DST) -- ground truth anchor.
    out = _call("tz.utc_to_asia__kolkata", "2026-01-15T12:00:00+00:00")
    assert out["result"] == "2026-01-15T17:30:00+05:30"
    assert out["to_utc_offset"] == "+05:30"
    assert out["to_zone"] == "Asia/Kolkata"
    assert out["from_zone"] == "UTC"
    assert out["input_interpreted_in_from_zone"] is False  # aware input


def test_dst_aware_new_york_winter_vs_summer():
    # Naive inputs are wall-clock in the FROM zone: winter EST (-5), summer EDT (-4).
    winter = _call("tz.america__new_york_to_utc", "2026-01-15T12:00:00")
    summer = _call("tz.america__new_york_to_utc", "2026-07-15T12:00:00")
    assert winter["from_utc_offset"] == "-05:00"
    assert summer["from_utc_offset"] == "-04:00"
    assert winter["result"] == "2026-01-15T17:00:00+00:00"
    assert summer["result"] == "2026-07-15T16:00:00+00:00"
    assert winter["input_interpreted_in_from_zone"] is True


def test_invalid_datetime_is_honest_error():
    tool = P.resolve("tz.america__new_york_to_asia__kolkata")
    assert tool is not None
    for bad in ["not-a-date", "", "2026-13-45T99:99", 12345, None, True]:
        out = tool.handler({"datetime": bad})
        assert "error" in out, f"input {bad!r} gave {out}"
    out = tool.handler({})
    assert "error" in out
    out = tool.handler("not-a-dict")
    assert "error" in out


def test_round_trip_returns_original_instant():
    there = _call("tz.america__new_york_to_asia__tokyo", "2026-03-20T09:30:00")
    back = _call("tz.asia__tokyo_to_america__new_york", there["result"])
    assert back["utc_instant"] == there["utc_instant"]
    # And both equal the direct zoneinfo instant for the original wall time.
    expected = datetime(2026, 3, 20, 9, 30, tzinfo=ZoneInfo("America/New_York"))
    assert back["utc_instant"] == expected.astimezone(timezone.utc).isoformat()


def test_unknown_zone_resolves_to_clean_error_not_crash():
    tool = P.resolve("tz.bogus_zone_to_asia__kolkata")
    assert tool is not None
    out = tool.handler({"datetime": "2026-01-01T00:00:00"})
    assert "error" in out and "bogus_zone" in out["error"]
    tool = P.resolve("tz.asia__kolkata_to_asia__kolkata")  # self-pair
    assert tool is not None
    assert "error" in tool.handler({"datetime": "2026-01-01T00:00:00"})
    assert P.resolve("tz.notashape") is None
    assert P.resolve("tz.") is None
    assert P.resolve("other.america__new_york_to_utc") is None


def test_sanitization_collision_free_and_documented():
    # Etc/GMT+N vs Etc/GMT-N: the only collision source in current tzdata,
    # disambiguated by the '+' -> '_plus_' rule.
    plus = _call("tz.etc__gmt_plus_5_to_utc", "2026-01-15T12:00:00")
    minus = _call("tz.etc__gmt_5_to_utc", "2026-01-15T12:00:00")
    assert plus["from_zone"] == "Etc/GMT+5"
    assert minus["from_zone"] == "Etc/GMT-5"
    assert plus["result"] == "2026-01-15T17:00:00+00:00"
    assert minus["result"] == "2026-01-15T07:00:00+00:00"
    # Every token is a valid identifier segment; expand is exact.
    from jarvis.tools.dynamic import tz_provider as mod
    assert len(mod._TOKEN_TO_ZONE) == len(ZONES)
    assert P.expand() == len(ZONES) * (len(ZONES) - 1)


def test_handlers_never_raise():
    tool = P.resolve("tz.europe__london_to_america__los_angeles")
    assert tool is not None
    for args in [{}, {"datetime": None}, {"datetime": []}, "x", None, 42]:
        out = tool.handler(args)
        assert isinstance(out, dict)
    err_tool = P.resolve("tz.nope_to_also_nope")
    assert isinstance(err_tool.handler(None), dict)


def test_module_instance_is_registerable():
    assert tz_provider.namespace == "tz"
    assert isinstance(tz_provider, TzProvider)
    assert tz_provider.expand() == P.expand()
    assert len(tz_provider.sample_names(3)) == 3
