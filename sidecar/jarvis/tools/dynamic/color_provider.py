"""Color conversion provider (Phase 10): ~23k addressable tools.

Tools are named ``color.<from>_to_<to>`` for every ordered pair of DISTINCT
color representations. Representations ("units") are:

- 5 format units: ``hex``, ``rgb``, ``hsl``, ``hsv``, ``cmyk``
- 148 CSS Color Level 4 named colors (``aliceblue`` ... ``yellowgreen``),
  e.g. ``color.rebeccapurple_to_hex`` or ``color.rgb_to_crimson``.

With N = 5 + 148 = 153 units the namespace exposes exactly
N*(N-1) = 153*152 = 23,256 addressable tool names. Lazy: no pair is
precomputed; expand() is O(1) and resolve() builds exactly ONE Tool.

Conversion math is real (standard formulas, RGB as the hub):

- named color -> format: look up the keyword's RGB, then convert.
- format -> named color: convert to RGB, then find the NEAREST named color
  by Euclidean distance in 0-255 RGB space. The result carries the
  distance, so it is honest about being "nearest", not exact.
- named color -> named color (distinct names): nearest named color
  excluding the source name itself, with its distance.

Args: {"value": "<color string>"} where the string matches the FROM unit:

- hex:  "#ff0000", "ff0000", "#f00", "0xff0000" (case-insensitive)
- rgb:  "255,0,0", "rgb(255,0,0)", "255 0 0", or percents "100%,0%,0%"
- hsl:  "0,100%,50%", "hsl(0,100%,50%)"; hue wraps mod 360; s/l are 0-100%
- hsv:  "0,1,1", "hsv(0,1,1)"; hue in degrees (wraps), s/v in [0,1] or %
- cmyk: "0,1,1,0", "cmyk(0,1,1,0)"; each in [0,1] or %
- named: the keyword itself ("crimson"; spaces/hyphens are tolerated)

Parsing is liberal; output is strict and canonical:

- hex:  "#ff0000" (lowercase, 6 digits)
- rgb:  "255,0,0"
- hsl:  "214.84,64.86%,53.14%"
- hsv:  "214.84,0.7277,0.8353"
- cmyk: "0.7277,0.4225,0,0.1647"
- named: "crimson"

Handlers never raise: invalid input returns {"error": ...}, never a guess.
"""

from __future__ import annotations

import math
import re

from .providers import Provider
from ..base import Tool

# --------------------------------------------------------------------------
# Units
# --------------------------------------------------------------------------

FORMAT_UNITS: tuple[str, ...] = ("hex", "rgb", "hsl", "hsv", "cmyk")

# CSS Color Module Level 4 named colors: keyword -> (r, g, b) in 0-255.
# This is the full standard keyword table (148 entries).
CSS_NAMED_COLORS: dict[str, tuple[int, int, int]] = {
    "aliceblue": (240, 248, 255),
    "antiquewhite": (250, 235, 215),
    "aqua": (0, 255, 255),
    "aquamarine": (127, 255, 212),
    "azure": (240, 255, 255),
    "beige": (245, 245, 220),
    "bisque": (255, 228, 196),
    "black": (0, 0, 0),
    "blanchedalmond": (255, 235, 205),
    "blue": (0, 0, 255),
    "blueviolet": (138, 43, 226),
    "brown": (165, 42, 42),
    "burlywood": (222, 184, 135),
    "cadetblue": (95, 158, 160),
    "chartreuse": (127, 255, 0),
    "chocolate": (210, 105, 30),
    "coral": (255, 127, 80),
    "cornflowerblue": (100, 149, 237),
    "cornsilk": (255, 248, 220),
    "crimson": (220, 20, 60),
    "cyan": (0, 255, 255),
    "darkblue": (0, 0, 139),
    "darkcyan": (0, 139, 139),
    "darkgoldenrod": (184, 134, 11),
    "darkgray": (169, 169, 169),
    "darkgreen": (0, 100, 0),
    "darkgrey": (169, 169, 169),
    "darkkhaki": (189, 183, 107),
    "darkmagenta": (139, 0, 139),
    "darkolivegreen": (85, 107, 47),
    "darkorange": (255, 140, 0),
    "darkorchid": (153, 50, 204),
    "darkred": (139, 0, 0),
    "darksalmon": (233, 150, 122),
    "darkseagreen": (143, 188, 143),
    "darkslateblue": (72, 61, 139),
    "darkslategray": (47, 79, 79),
    "darkslategrey": (47, 79, 79),
    "darkturquoise": (0, 206, 209),
    "darkviolet": (148, 0, 211),
    "deeppink": (255, 20, 147),
    "deepskyblue": (0, 191, 255),
    "dimgray": (105, 105, 105),
    "dimgrey": (105, 105, 105),
    "dodgerblue": (30, 144, 255),
    "firebrick": (178, 34, 34),
    "floralwhite": (255, 250, 240),
    "forestgreen": (34, 139, 34),
    "fuchsia": (255, 0, 255),
    "gainsboro": (220, 220, 220),
    "ghostwhite": (248, 248, 255),
    "gold": (255, 215, 0),
    "goldenrod": (218, 165, 32),
    "gray": (128, 128, 128),
    "green": (0, 128, 0),
    "greenyellow": (173, 255, 47),
    "grey": (128, 128, 128),
    "honeydew": (240, 255, 240),
    "hotpink": (255, 105, 180),
    "indianred": (205, 92, 92),
    "indigo": (75, 0, 130),
    "ivory": (255, 255, 240),
    "khaki": (240, 230, 140),
    "lavender": (230, 230, 250),
    "lavenderblush": (255, 240, 245),
    "lawngreen": (124, 252, 0),
    "lemonchiffon": (255, 250, 205),
    "lightblue": (173, 216, 230),
    "lightcoral": (240, 128, 128),
    "lightcyan": (224, 255, 255),
    "lightgoldenrodyellow": (250, 250, 210),
    "lightgray": (211, 211, 211),
    "lightgreen": (144, 238, 144),
    "lightgrey": (211, 211, 211),
    "lightpink": (255, 182, 193),
    "lightsalmon": (255, 160, 122),
    "lightseagreen": (32, 178, 170),
    "lightskyblue": (135, 206, 250),
    "lightslategray": (119, 136, 153),
    "lightslategrey": (119, 136, 153),
    "lightsteelblue": (176, 196, 222),
    "lightyellow": (255, 255, 224),
    "lime": (0, 255, 0),
    "limegreen": (50, 205, 50),
    "linen": (250, 240, 230),
    "magenta": (255, 0, 255),
    "maroon": (128, 0, 0),
    "mediumaquamarine": (102, 205, 170),
    "mediumblue": (0, 0, 205),
    "mediumorchid": (186, 85, 211),
    "mediumpurple": (147, 112, 219),
    "mediumseagreen": (60, 179, 113),
    "mediumslateblue": (123, 104, 238),
    "mediumspringgreen": (0, 250, 154),
    "mediumturquoise": (72, 209, 204),
    "mediumvioletred": (199, 21, 133),
    "midnightblue": (25, 25, 112),
    "mintcream": (245, 255, 250),
    "mistyrose": (255, 228, 225),
    "moccasin": (255, 228, 181),
    "navajowhite": (255, 222, 173),
    "navy": (0, 0, 128),
    "oldlace": (253, 245, 230),
    "olive": (128, 128, 0),
    "olivedrab": (107, 142, 35),
    "orange": (255, 165, 0),
    "orangered": (255, 69, 0),
    "orchid": (218, 112, 214),
    "palegoldenrod": (238, 232, 170),
    "palegreen": (152, 251, 152),
    "paleturquoise": (175, 238, 238),
    "palevioletred": (219, 112, 147),
    "papayawhip": (255, 239, 213),
    "peachpuff": (255, 218, 185),
    "peru": (205, 133, 63),
    "pink": (255, 192, 203),
    "plum": (221, 160, 221),
    "powderblue": (176, 224, 230),
    "purple": (128, 0, 128),
    "rebeccapurple": (102, 51, 153),
    "red": (255, 0, 0),
    "rosybrown": (188, 143, 143),
    "royalblue": (65, 105, 225),
    "saddlebrown": (139, 69, 19),
    "salmon": (250, 128, 114),
    "sandybrown": (244, 164, 96),
    "seagreen": (46, 139, 87),
    "seashell": (255, 245, 238),
    "sienna": (160, 82, 45),
    "silver": (192, 192, 192),
    "skyblue": (135, 206, 235),
    "slateblue": (106, 90, 205),
    "slategray": (112, 128, 144),
    "slategrey": (112, 128, 144),
    "snow": (255, 250, 250),
    "springgreen": (0, 255, 127),
    "steelblue": (70, 130, 180),
    "tan": (210, 180, 140),
    "teal": (0, 128, 128),
    "thistle": (216, 191, 216),
    "tomato": (255, 99, 71),
    "turquoise": (64, 224, 208),
    "violet": (238, 130, 238),
    "wheat": (245, 222, 179),
    "white": (255, 255, 255),
    "whitesmoke": (245, 245, 245),
    "yellow": (255, 255, 0),
    "yellowgreen": (154, 205, 50),
}

_ALL_COLOR_UNITS: tuple[str, ...] = FORMAT_UNITS + tuple(CSS_NAMED_COLORS)
_UNIT_SET: frozenset[str] = frozenset(_ALL_COLOR_UNITS)

# --------------------------------------------------------------------------
# Small parsing helpers (liberal input)
# --------------------------------------------------------------------------

_HEX_DIGITS = frozenset("0123456789abcdef")
_COMPONENT_SPLIT = re.compile(r"[,\s/]+")


def _to_float(text: str) -> float:
    try:
        return float(text)
    except (TypeError, ValueError):
        raise ValueError(f"not a number: {text!r}")


def _unwrap(text: str, wrapper: str) -> list[str]:
    """Strip an optional ``wrapper(...)`` and split into components."""
    t = text.strip()
    low = t.lower()
    if low.startswith(wrapper + "(") and t.endswith(")"):
        t = t[len(wrapper) + 1 : -1]
    parts = [p for p in _COMPONENT_SPLIT.split(t) if p]
    return parts


def _parse_hex(s: str) -> tuple[int, int, int]:
    t = s.strip().lower()
    if t.startswith("#"):
        t = t[1:]
    elif t.startswith("0x"):
        t = t[2:]
    if len(t) == 3 and all(c in _HEX_DIGITS for c in t):
        t = "".join(c * 2 for c in t)
    if len(t) != 6 or any(c not in _HEX_DIGITS for c in t):
        raise ValueError(f"invalid hex color: {s!r} (want #rrggbb or #rgb)")
    return (int(t[0:2], 16), int(t[2:4], 16), int(t[4:6], 16))


def _parse_rgb(s: str) -> tuple[int, int, int]:
    parts = _unwrap(s, "rgb")
    if len(parts) != 3:
        raise ValueError(f"invalid rgb color: {s!r} (want 3 components)")
    out: list[int] = []
    for p in parts:
        if p.endswith("%"):
            v = _to_float(p[:-1]) * 255.0 / 100.0
        else:
            v = _to_float(p)
        if not 0.0 <= v <= 255.0:
            raise ValueError(f"rgb component out of range 0-255: {p!r}")
        out.append(int(round(v)))
    return (out[0], out[1], out[2])


def _parse_percent(p: str, what: str) -> float:
    """0-100 (with or without '%') -> fraction 0-1."""
    t = p[:-1] if p.endswith("%") else p
    v = _to_float(t)
    if not 0.0 <= v <= 100.0:
        raise ValueError(f"{what} out of range 0-100: {p!r}")
    return v / 100.0


def _parse_unit01(p: str, what: str) -> float:
    """Fraction in [0,1]; also accepts an explicit '%' (0-100%)."""
    if p.endswith("%"):
        v = _to_float(p[:-1])
        if not 0.0 <= v <= 100.0:
            raise ValueError(f"{what} out of range 0-100%: {p!r}")
        return v / 100.0
    v = _to_float(p)
    if not 0.0 <= v <= 1.0:
        raise ValueError(f"{what} out of range 0-1: {p!r}")
    return v


def _parse_hue(p: str) -> float:
    """Hue in degrees; wraps mod 360 (liberal)."""
    t = p[:-3] if p.lower().endswith("deg") else p
    return _to_float(t) % 360.0


def _parse_hsl(s: str) -> tuple[int, int, int]:
    parts = _unwrap(s, "hsl")
    if len(parts) != 3:
        raise ValueError(f"invalid hsl color: {s!r} (want h,s,l)")
    h, sv, lv = _parse_hue(parts[0]), _parse_percent(parts[1], "saturation"), _parse_percent(
        parts[2], "lightness"
    )
    return _hsl_to_rgb(h, sv, lv)


def _parse_hsv(s: str) -> tuple[int, int, int]:
    parts = _unwrap(s, "hsv")
    if len(parts) != 3:
        raise ValueError(f"invalid hsv color: {s!r} (want h,s,v)")
    h = _parse_hue(parts[0])
    sv, vv = _parse_unit01(parts[1], "saturation"), _parse_unit01(parts[2], "value")
    return _hsv_to_rgb(h, sv, vv)


def _parse_cmyk(s: str) -> tuple[int, int, int]:
    parts = _unwrap(s, "cmyk")
    if len(parts) != 4:
        raise ValueError(f"invalid cmyk color: {s!r} (want c,m,y,k)")
    c, m, y, k = (_parse_unit01(p, n) for p, n in zip(parts, ("cyan", "magenta", "yellow", "black")))
    return _cmyk_to_rgb(c, m, y, k)


def _parse_named(s: str) -> tuple[int, int, int]:
    key = re.sub(r"[\s_-]+", "", s.strip().lower())
    rgb = CSS_NAMED_COLORS.get(key)
    if rgb is None:
        raise ValueError(f"unknown CSS color name: {s!r}")
    return rgb


_PARSERS = {
    "hex": _parse_hex,
    "rgb": _parse_rgb,
    "hsl": _parse_hsl,
    "hsv": _parse_hsv,
    "cmyk": _parse_cmyk,
}


def _parse(unit: str, value: str) -> tuple[int, int, int]:
    parser = _PARSERS.get(unit)
    if parser is not None:
        return parser(value)
    return _parse_named(value)  # unit is a named color


# --------------------------------------------------------------------------
# Conversion math (RGB ints 0-255 as the hub)
# --------------------------------------------------------------------------


def _clamp255(v: float) -> int:
    return max(0, min(255, int(round(v))))


def _rgb_to_hsl(r: int, g: int, b: int) -> tuple[float, float, float]:
    """-> (h in [0,360), s in [0,1], l in [0,1])."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    l = (mx + mn) / 2.0
    if mx == mn:
        return (0.0, 0.0, l)
    d = mx - mn
    s = d / (1.0 - abs(2.0 * l - 1.0))
    if mx == rf:
        h = ((gf - bf) / d) % 6.0
    elif mx == gf:
        h = (bf - rf) / d + 2.0
    else:
        h = (rf - gf) / d + 4.0
    return (h * 60.0, s, l)


def _hsl_to_rgb(h: float, s: float, l: float) -> tuple[int, int, int]:
    h = h % 360.0
    c = (1.0 - abs(2.0 * l - 1.0)) * s
    hp = h / 60.0
    x = c * (1.0 - abs(hp % 2.0 - 1.0))
    m = l - c / 2.0
    sectors = [(c, x, 0.0), (x, c, 0.0), (0.0, c, x),
               (0.0, x, c), (x, 0.0, c), (c, 0.0, x)]
    rp, gp, bp = sectors[int(hp) % 6]
    return (_clamp255((rp + m) * 255.0),
            _clamp255((gp + m) * 255.0),
            _clamp255((bp + m) * 255.0))


def _rgb_to_hsv(r: int, g: int, b: int) -> tuple[float, float, float]:
    """-> (h in [0,360), s in [0,1], v in [0,1])."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    mx, mn = max(rf, gf, bf), min(rf, gf, bf)
    v = mx
    d = mx - mn
    if mx == 0.0:
        return (0.0, 0.0, 0.0)
    s = d / mx
    if d == 0.0:
        return (0.0, 0.0, v)
    if mx == rf:
        h = ((gf - bf) / d) % 6.0
    elif mx == gf:
        h = (bf - rf) / d + 2.0
    else:
        h = (rf - gf) / d + 4.0
    return (h * 60.0, s, v)


def _hsv_to_rgb(h: float, s: float, v: float) -> tuple[int, int, int]:
    h = h % 360.0
    c = v * s
    hp = h / 60.0
    x = c * (1.0 - abs(hp % 2.0 - 1.0))
    m = v - c
    sectors = [(c, x, 0.0), (x, c, 0.0), (0.0, c, x),
               (0.0, x, c), (x, 0.0, c), (c, 0.0, x)]
    rp, gp, bp = sectors[int(hp) % 6]
    return (_clamp255((rp + m) * 255.0),
            _clamp255((gp + m) * 255.0),
            _clamp255((bp + m) * 255.0))


def _rgb_to_cmyk(r: int, g: int, b: int) -> tuple[float, float, float, float]:
    """-> (c, m, y, k) each in [0,1]."""
    rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
    k = 1.0 - max(rf, gf, bf)
    if k >= 1.0:
        return (0.0, 0.0, 0.0, 1.0)
    inv = 1.0 - k
    return ((1.0 - rf - k) / inv, (1.0 - gf - k) / inv,
            (1.0 - bf - k) / inv, k)


def _cmyk_to_rgb(c: float, m: float, y: float, k: float) -> tuple[int, int, int]:
    inv = 1.0 - k
    return (_clamp255((1.0 - c) * inv * 255.0),
            _clamp255((1.0 - m) * inv * 255.0),
            _clamp255((1.0 - y) * inv * 255.0))


# --------------------------------------------------------------------------
# Canonical output formatting (strict)
# --------------------------------------------------------------------------


def _fmt(x: float, nd: int) -> str:
    v = round(x, nd)
    s = f"{v:.{nd}f}".rstrip("0").rstrip(".")
    return "0" if s in ("", "-0") else s


def _to_hex(r: int, g: int, b: int) -> str:
    return f"#{r:02x}{g:02x}{b:02x}"


def _nearest_name(r: int, g: int, b: int,
                  exclude: str | None = None) -> tuple[str, float]:
    """Nearest CSS named color by Euclidean distance in 0-255 RGB space.

    Deterministic: ties resolve to the first keyword in table order.
    """
    best: str | None = None
    best_d = math.inf
    for name, (nr, ng, nb) in CSS_NAMED_COLORS.items():
        if name == exclude:
            continue
        d = math.sqrt((r - nr) ** 2 + (g - ng) ** 2 + (b - nb) ** 2)
        if d < best_d:
            best_d = d
            best = name
    assert best is not None  # table is non-empty
    return best, best_d


def _convert(from_unit: str, to_unit: str, raw: str,
             r: int, g: int, b: int) -> dict:
    """Build the result dict for parsed RGB -> to_unit. Never raises."""
    base = {"input": raw, "from": from_unit, "to": to_unit}
    if to_unit == "hex":
        s = _to_hex(r, g, b)
        base.update(result=s, components={"hex": s, "r": r, "g": g, "b": b})
    elif to_unit == "rgb":
        base.update(result=f"{r},{g},{b}", components={"r": r, "g": g, "b": b})
    elif to_unit == "hsl":
        h, sv, lv = _rgb_to_hsl(r, g, b)
        s = f"{_fmt(h, 2)},{_fmt(sv * 100.0, 2)}%,{_fmt(lv * 100.0, 2)}%"
        base.update(result=s, components={"h": round(h, 2),
                                           "s": round(sv, 4),
                                           "l": round(lv, 4)})
    elif to_unit == "hsv":
        h, sv, vv = _rgb_to_hsv(r, g, b)
        s = f"{_fmt(h, 2)},{_fmt(sv, 4)},{_fmt(vv, 4)}"
        base.update(result=s, components={"h": round(h, 2),
                                           "s": round(sv, 4),
                                           "v": round(vv, 4)})
    elif to_unit == "cmyk":
        c, m, y, k = _rgb_to_cmyk(r, g, b)
        s = ",".join(_fmt(x, 4) for x in (c, m, y, k))
        base.update(result=s, components={"c": round(c, 4),
                                           "m": round(m, 4),
                                           "y": round(y, 4),
                                           "k": round(k, 4)})
    else:
        # Named-color target: nearest keyword, honestly labeled as nearest.
        exclude = from_unit if from_unit in CSS_NAMED_COLORS else None
        name, dist = _nearest_name(r, g, b, exclude=exclude)
        nr, ng, nb = CSS_NAMED_COLORS[name]
        nearest = {"name": name, "hex": _to_hex(nr, ng, nb),
                   "rgb": [nr, ng, nb], "distance": round(dist, 4)}
        base.update(result=name, components=nearest, nearest=nearest,
                    note="nearest CSS named color by Euclidean distance in "
                         "RGB space -- approximate, not exact")
    return base


def _make_handler(from_unit: str, to_unit: str):
    def _handler(args: dict) -> dict:
        try:
            if not isinstance(args, dict):
                return {"error": "args must be an object"}
            v = args.get("value")
            if not isinstance(v, str) or not v.strip():
                return {"error": "value must be a non-empty color string"}
            try:
                r, g, b = _parse(from_unit, v)
            except ValueError as e:
                return {"error": str(e)}
            return _convert(from_unit, to_unit, v, r, g, b)
        except Exception as e:  # never raise out of a tool handler
            return {"error": f"{type(e).__name__}: {e}"}

    return _handler


# --------------------------------------------------------------------------
# Provider
# --------------------------------------------------------------------------


class ColorProvider(Provider):
    """Dynamic provider for the ``color`` namespace.

    ``color.<from>_to_<to>`` for every ordered pair of distinct color
    representations (5 formats + 148 CSS named colors). Lazy: pairs are
    never precomputed; expand() is O(1) on the unit count.
    """

    namespace = "color"

    def expand(self) -> int:
        """Exact addressable count: N*(N-1) over distinct unit pairs."""
        n = len(_ALL_COLOR_UNITS)
        return n * (n - 1)

    def count_addressable(self) -> int:
        """Alias for expand(), per the Phase 10 workstream contract."""
        return self.expand()

    def resolve(self, name: str) -> Tool | None:
        """Resolve ``color.<from>_to_<to>`` to one Tool; None if not
        addressable (wrong namespace, unknown unit, or self-pair)."""
        if not isinstance(name, str) or not name.startswith("color."):
            return None
        body = name[len("color."):]
        if "_to_" not in body:
            return None
        a, b = body.rsplit("_to_", 1)
        if not a or not b or a == b:
            return None
        if a not in _UNIT_SET or b not in _UNIT_SET:
            return None
        if b in CSS_NAMED_COLORS:
            desc = (f"Convert a {a} color to the nearest CSS named color "
                    f"(by RGB distance). Args: value (color string in {a} "
                    "form). The result is honest about being nearest, "
                    "not exact.")
        else:
            desc = (f"Convert a {a} color to {b}. Args: value (color string "
                    f"in {a} form).")
        return Tool(
            name=name,
            description=desc,
            schema={"value": f"color string in {a} form"},
            handler=_make_handler(a, b),
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        """Up to `n` representative, always-valid tool names."""
        canonical = [
            "color.hex_to_rgb",
            "color.rgb_to_hex",
            "color.crimson_to_hex",
            "color.rgb_to_crimson",
            "color.hsl_to_cmyk",
            "color.rebeccapurple_to_hsv",
            "color.cmyk_to_hex",
            "color.white_to_black",
        ]
        if n <= len(canonical):
            return canonical[:n]
        names = list(canonical)
        for i, ua in enumerate(_ALL_COLOR_UNITS):
            for j, ub in enumerate(_ALL_COLOR_UNITS):
                if i == j:
                    continue
                cand = f"color.{ua}_to_{ub}"
                if cand not in names:
                    names.append(cand)
                    if len(names) >= n:
                        return names
        return names


#: Instance the Phase 7 wiring convention can import and register
#: (the parent workstream applies the actual registry wiring).
color_provider = ColorProvider()
