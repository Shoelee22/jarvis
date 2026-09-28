"""Unit tests for the Phase 10 color provider (color.<from>_to_<to>).

All expected values below are hand-computed with the standard conversion
formulas (not just round-trips). Run from ~/workspace/jarvis/sidecar:
    python3 -m pytest tests/unit/test_phase10_color.py -q
"""
import math
import sys

import pytest

sys.path.insert(0, ".")

from jarvis.tools.dynamic.color_provider import (  # noqa: E402
    CSS_NAMED_COLORS,
    ColorProvider,
    color_provider,
)

P = ColorProvider()

# Hand-computed reference values (standard formulas, RGB hub).
# #3a7bd5 = (58, 123, 213):
#   r=0.227451, g=0.482353, b=0.835294, max=0.835294, min=0.227451
#   HSL: l=0.531373, s=0.607843/0.937255=0.648536, h=60*(-0.254902/0.607843+4)=214.8387
#   (verified against colorsys: 64.8536%, not a rough hand estimate)
#   HSV: v=0.835294, s=0.607843/0.835294=0.727700, h=214.8387
#   CMYK: k=0.164706, c=0.607843/0.835294=0.727700, m=0.352941/0.835294=0.422535, y=0
MID_HEX = "#3a7bd5"
MID_RGB = "58,123,213"
MID_HSL = "214.84,64.85%,53.14%"
MID_HSV = "214.84,0.7277,0.8353"
MID_CMYK = "0.7277,0.4225,0,0.1647"

CASES = [
    # (hex, rgb, hsl, hsv, cmyk)
    ("#ff0000", "255,0,0", "0,100%,50%", "0,1,1", "0,1,1,0"),          # red
    ("#ffffff", "255,255,255", "0,0%,100%", "0,0,1", "0,0,0,0"),       # white
    ("#000000", "0,0,0", "0,0%,0%", "0,0,0", "0,0,0,1"),               # black
    (MID_HEX, MID_RGB, MID_HSL, MID_HSV, MID_CMYK),                   # mid-tone
]

FORMATS = ("hex", "rgb", "hsl", "hsv", "cmyk")


def _call(name: str, value: str) -> dict:
    tool = P.resolve(name)
    assert tool is not None, f"{name} did not resolve"
    return tool.handler({"value": value})


# -- interface / count ------------------------------------------------------


def test_named_table_has_148_colors():
    assert len(CSS_NAMED_COLORS) == 148


def test_expand_exact_count():
    # (5 formats + 148 names) * (152 others) = 153 * 152 = 23256
    assert P.expand() == 153 * 152 == 23256
    assert P.count_addressable() == P.expand()
    assert color_provider.expand() == 23256


def test_resolve_rejects_non_addressable():
    assert P.resolve("color.hex_to_hex") is None        # self-pair
    assert P.resolve("color.crimson_to_crimson") is None
    assert P.resolve("color.hex_to_bogus") is None      # unknown unit
    assert P.resolve("color.bogus_to_hex") is None
    assert P.resolve("color.hex") is None               # no _to_
    assert P.resolve("convert.hex_to_rgb") is None      # wrong namespace
    assert P.resolve("color.") is None
    assert P.resolve("") is None
    assert P.resolve(None) is None


def test_sample_names_all_resolve():
    samples = P.sample_names(8)
    assert len(samples) == 8
    assert len(set(samples)) == 8
    for name in samples:
        tool = P.resolve(name)
        assert tool is not None, f"sample {name} did not resolve"


def test_sample_names_bounded():
    assert P.sample_names(3) == [
        "color.hex_to_rgb",
        "color.rgb_to_hex",
        "color.crimson_to_hex",
    ]


# -- exact forward conversions (hand-computed) -------------------------------


def test_hex_to_rgb_exact():
    for hx, rgb, _hsl, _hsv, _cmyk in CASES:
        out = _call("color.hex_to_rgb", hx)
        assert out["result"] == rgb, f"{hx}: {out}"


def test_rgb_to_hex_exact():
    for hx, rgb, _hsl, _hsv, _cmyk in CASES:
        out = _call("color.rgb_to_hex", rgb)
        assert out["result"] == hx, f"{rgb}: {out}"


def test_rgb_to_hsl_exact():
    for _hx, rgb, hsl, _hsv, _cmyk in CASES:
        out = _call("color.rgb_to_hsl", rgb)
        assert out["result"] == hsl, f"{rgb}: {out}"


def test_rgb_to_hsv_exact():
    for _hx, rgb, _hsl, hsv, _cmyk in CASES:
        out = _call("color.rgb_to_hsv", rgb)
        assert out["result"] == hsv, f"{rgb}: {out}"


def test_rgb_to_cmyk_exact():
    for _hx, rgb, _hsl, _hsv, cmyk in CASES:
        out = _call("color.rgb_to_cmyk", rgb)
        assert out["result"] == cmyk, f"{rgb}: {out}"


# -- round trips -------------------------------------------------------------


@pytest.mark.parametrize("fmt_in,fmt_out", [
    ("hex", "rgb"), ("rgb", "hex"),
    ("rgb", "hsl"), ("hsl", "rgb"),
    ("rgb", "hsv"), ("hsv", "rgb"),
    ("rgb", "cmyk"), ("cmyk", "rgb"),
    ("hex", "hsl"), ("hex", "hsv"), ("hex", "cmyk"),
])
def test_round_trips(fmt_in, fmt_out):
    for hx, rgb, hsl, hsv, cmyk in CASES:
        start = {"hex": hx, "rgb": rgb, "hsl": hsl, "hsv": hsv, "cmyk": cmyk}[fmt_in]
        want = {"hex": hx, "rgb": rgb, "hsl": hsl, "hsv": hsv, "cmyk": cmyk}[fmt_out]
        mid = _call(f"color.{fmt_in}_to_{fmt_out}", start)["result"]
        assert mid == want, f"{fmt_in}->{fmt_out} of {start}: got {mid}"
        back = _call(f"color.{fmt_out}_to_{fmt_in}", mid)["result"]
        assert back == start, f"round trip {start}: got {back}"


def test_structured_result_shape():
    out = _call("color.hex_to_hsl", "#ff0000")
    assert out["input"] == "#ff0000"
    assert out["from"] == "hex"
    assert out["to"] == "hsl"
    assert out["result"] == "0,100%,50%"
    assert out["components"] == {"h": 0.0, "s": 1.0, "l": 0.5}


# -- named colors ------------------------------------------------------------


def test_crimson_to_hex():
    out = _call("color.crimson_to_hex", "crimson")
    assert out["result"] == "#dc143c"
    assert out["components"] == {"hex": "#dc143c", "r": 220, "g": 20, "b": 60}


def test_rebeccapurple_to_rgb():
    out = _call("color.rebeccapurple_to_rgb", "rebeccapurple")
    assert out["result"] == "102,51,153"


def test_named_to_format_is_exact_lookup():
    out = _call("color.tomato_to_hsl", "tomato")  # (255,99,71)
    # hand-computed: l=0.639216, s=1.0, h=9.1304
    assert out["result"] == "9.13,100%,63.92%"


def test_format_to_named_returns_nearest_with_distance():
    # #dd1440 = (221,20,64); crimson = (220,20,60); dist = sqrt(1+0+16)
    out = _call("color.hex_to_crimson", "#dd1440")
    assert out["result"] == "crimson"
    nearest = out["nearest"]
    assert nearest["name"] == "crimson"
    assert nearest["hex"] == "#dc143c"
    assert nearest["rgb"] == [220, 20, 60]
    assert nearest["distance"] == pytest.approx(math.sqrt(17), abs=0.01)
    assert nearest["distance"] < 10  # honestly small
    assert "note" in out  # honest about being nearest, not exact


def test_format_to_named_exact_hit_has_zero_distance():
    out = _call("color.rgb_to_red", "255,0,0")
    assert out["result"] == "red"
    assert out["nearest"]["distance"] == 0.0


def test_named_to_named_excludes_self():
    out = _call("color.crimson_to_firebrick", "crimson")
    assert "error" not in out
    assert out["result"] != "crimson"  # nearest *distinct* name
    assert out["nearest"]["distance"] > 0


def test_named_from_unit_is_liberal_about_separators():
    out = _call("color.lightgoldenrodyellow_to_hex", "LightGoldenrodYellow")
    assert out["result"] == "#fafad2"


# -- liberal parsing ----------------------------------------------------------


def test_liberal_input_forms():
    assert _call("color.hex_to_rgb", "FF0000")["result"] == "255,0,0"
    assert _call("color.hex_to_rgb", "#f00")["result"] == "255,0,0"
    assert _call("color.rgb_to_hex", "rgb(255, 0, 0)")["result"] == "#ff0000"
    assert _call("color.rgb_to_hex", "100%,0%,0%")["result"] == "#ff0000"
    assert _call("color.hsl_to_hex", "hsl(0,100%,50%)")["result"] == "#ff0000"
    assert _call("color.hsv_to_hex", "hsv(0,1,1)")["result"] == "#ff0000"
    assert _call("color.cmyk_to_hex", "cmyk(0,1,1,0)")["result"] == "#ff0000"
    assert _call("color.hsl_to_hex", "360,100%,50%")["result"] == "#ff0000"  # hue wraps


# -- invalid input: honest errors, never crashes ------------------------------


@pytest.mark.parametrize("name,value", [
    ("color.hex_to_rgb", "notacolor"),
    ("color.hex_to_rgb", "#ff00"),          # bad length
    ("color.hex_to_rgb", "#gg0000"),        # bad digits
    ("color.rgb_to_hex", "300,0,0"),        # out of range
    ("color.rgb_to_hex", "255,0"),          # too few components
    ("color.rgb_to_hex", "255,0,0,0"),      # too many components
    ("color.hsl_to_hex", "0,120%,50%"),     # saturation out of range
    ("color.hsl_to_hex", "0,100%"),         # too few components
    ("color.hsv_to_hex", "0,2,1"),          # s out of range
    ("color.cmyk_to_hex", "0,1,1"),         # too few components
    ("color.cmyk_to_hex", "0,1,1,2"),       # k out of range
    ("color.chartreuse_to_hex", "notarealname"),  # unknown name
])
def test_invalid_values_return_errors(name, value):
    out = _call(name, value)
    assert "error" in out, f"{name}({value!r}) should error, got {out}"
    assert "result" not in out


def test_handler_never_raises():
    tool = P.resolve("color.hex_to_rgb")
    assert tool.handler(None)["error"]
    assert tool.handler("nope")["error"]
    assert tool.handler({})["error"]
    assert tool.handler({"value": 12345})["error"]      # not a string
    assert tool.handler({"value": ""})["error"]         # empty
    assert tool.handler({"value": "   "})["error"]     # blank
