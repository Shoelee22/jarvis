"""Tests for the Phase 10 dynamic providers (Workstream C):

encode/decode (24 tools), math (100 formulas), gen (6 tools),
languages_extra (48 new languages -> 60 total per greet/say namespace).

Providers are exercised directly (no registry wiring); handlers must never
raise. Run from ~/workspace/jarvis/sidecar:  python3 -m pytest tests/unit/test_phase10_encode_math.py
"""
from __future__ import annotations

import math
import re
import uuid

import pytest

from jarvis.tools.dynamic.encode_provider import (
    EncodeProvider, DecodeProvider, ALGORITHMS,
)
from jarvis.tools.dynamic.math_provider import MathProvider, FORMULAS
from jarvis.tools.dynamic.gen_provider import GenProvider
from jarvis.tools.dynamic.languages_extra import (
    ExtendedGreetProvider, ExtendedSayProvider,
    ALL_LANGUAGE_CODES, ALL_GREETINGS, EXTRA_LANGUAGE_CODES,
)


def _call(provider, name, args):
    tool = provider.resolve(name)
    assert tool is not None, f"unresolved: {name}"
    out = tool.handler(args)
    assert isinstance(out, dict)
    return out


# ===========================================================================
# encode / decode
# ===========================================================================

class TestEncodeDecode:
    def test_expand_counts(self):
        assert EncodeProvider().expand() == 12 == len(ALGORITHMS)
        assert DecodeProvider().expand() == 12 == len(ALGORITHMS)

    def test_all_names_resolve(self):
        enc, dec = EncodeProvider(), DecodeProvider()
        for a in ALGORITHMS:
            assert enc.resolve(f"encode.text_to_{a}") is not None
            assert dec.resolve(f"decode.{a}_to_text") is not None
        assert enc.resolve("encode.text_to_nope") is None
        assert dec.resolve("decode.nope_to_text") is None
        assert enc.resolve("bogus") is None

    def test_base64_roundtrip(self):
        out = _call(EncodeProvider(), "encode.text_to_base64", {"text": "hello"})
        assert out["result"] == "aGVsbG8="
        back = _call(DecodeProvider(), "decode.base64_to_text", {"text": "aGVsbG8="})
        assert back["result"] == "hello"

    def test_sha256_known_vector(self):
        out = _call(EncodeProvider(), "encode.text_to_sha256", {"text": "abc"})
        assert out["result"] == (
            "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"
        )

    def test_md5_decode_honest_error(self):
        out = _call(DecodeProvider(), "decode.md5_to_text",
                    {"text": "900150983cd24fb0d6963f7d28e17f72"})
        assert "error" in out
        assert "one-way" in out["error"]
        for algo in ("sha1", "sha256", "sha512", "sha3_256"):
            err = _call(DecodeProvider(), f"decode.{algo}_to_text", {"text": "x"})
            assert "error" in err and "one-way" in err["error"]

    def test_morse_sos_roundtrip(self):
        out = _call(EncodeProvider(), "encode.text_to_morse", {"text": "SOS"})
        assert out["result"] == "... --- ..."
        back = _call(DecodeProvider(), "decode.morse_to_text",
                     {"text": "... --- ..."})
        assert back["result"] == "SOS"

    def test_morse_word_separator(self):
        out = _call(EncodeProvider(), "encode.text_to_morse", {"text": "HI YOU"})
        assert out["result"] == ".... .. / -.-- --- ..-"

    def test_morse_bad_sequence_errors(self):
        out = _call(DecodeProvider(), "decode.morse_to_text",
                    {"text": "........"})
        assert "error" in out

    def test_morse_unencodable_char_errors(self):
        out = _call(EncodeProvider(), "encode.text_to_morse", {"text": "héllo"})
        assert "error" in out

    def test_binary_roundtrip(self):
        out = _call(EncodeProvider(), "encode.text_to_binary", {"text": "Hi"})
        assert out["result"] == "01001000 01101001"
        back = _call(DecodeProvider(), "decode.binary_to_text",
                     {"text": "01001000 01101001"})
        assert back["result"] == "Hi"

    def test_binary_bad_group_errors(self):
        out = _call(DecodeProvider(), "decode.binary_to_text",
                    {"text": "0100100 2"})
        assert "error" in out

    def test_hex_roundtrip(self):
        out = _call(EncodeProvider(), "encode.text_to_hex", {"text": "hi"})
        assert out["result"] == "6869"
        back = _call(DecodeProvider(), "decode.hex_to_text", {"text": "6869"})
        assert back["result"] == "hi"

    def test_urlencode_roundtrip(self):
        out = _call(EncodeProvider(), "encode.text_to_urlencode",
                    {"text": "a b&c"})
        assert out["result"] == "a%20b%26c"
        back = _call(DecodeProvider(), "decode.urlencode_to_text",
                     {"text": "a%20b%26c"})
        assert back["result"] == "a b&c"

    def test_rot13_roundtrip(self):
        out = _call(EncodeProvider(), "encode.text_to_rot13", {"text": "Hello"})
        assert out["result"] == "Uryyb"
        back = _call(DecodeProvider(), "decode.rot13_to_text", {"text": "Uryyb"})
        assert back["result"] == "Hello"

    def test_base64url_roundtrip(self):
        out = _call(EncodeProvider(), "encode.text_to_base64url",
                    {"text": ">>>???"})
        back = _call(DecodeProvider(), "decode.base64url_to_text",
                     {"text": out["result"]})
        assert back["result"] == ">>>???"

    def test_invalid_base64_errors(self):
        out = _call(DecodeProvider(), "decode.base64_to_text", {"text": "!!!"})
        assert "error" in out

    def test_missing_text_errors(self):
        out = _call(EncodeProvider(), "encode.text_to_hex", {})
        assert "error" in out


# ===========================================================================
# math (100 formulas)
# ===========================================================================

class TestMath:
    def test_expand_is_100(self):
        assert MathProvider().expand() == 100
        assert len(FORMULAS) == 100

    def test_category_split(self):
        cats = {}
        for spec in FORMULAS.values():
            cats[spec["category"]] = cats.get(spec["category"], 0) + 1
        assert cats == {"geometry": 32, "finance": 17, "physics": 22,
                        "statistics": 16, "algebra": 13}

    def test_unknown_formula(self):
        assert MathProvider().resolve("math.nope") is None

    def test_sample_names(self):
        names = MathProvider().sample_names(5)
        assert len(names) == 5 and all(n.startswith("math.") for n in names)

    def test_circle_area_r1_is_pi(self):
        out = _call(MathProvider(), "math.circle_area", {"radius": 1})
        assert out["result"] == pytest.approx(math.pi)

    def test_compound_interest_spot(self):
        out = _call(MathProvider(), "math.compound_interest",
                    {"principal": 1000, "annual_rate_percent": 5,
                     "years": 10, "compounds_per_year": 1})
        assert out["result"] == pytest.approx(1000 * 1.05 ** 10)

    def test_pythagorean_345(self):
        out = _call(MathProvider(), "math.pythagorean_hypotenuse",
                    {"leg_a": 3, "leg_b": 4})
        assert out["result"] == pytest.approx(5.0)

    def test_heron_345(self):
        out = _call(MathProvider(), "math.triangle_area_heron",
                    {"side_a": 3, "side_b": 4, "side_c": 5})
        assert out["result"] == pytest.approx(6.0)

    def test_kinetic_energy(self):
        out = _call(MathProvider(), "math.kinetic_energy",
                    {"mass_kg": 2, "velocity_ms": 3})
        assert out["result"] == pytest.approx(9.0)  # 1/2 * 2 * 3^2

    def test_ohms_law_voltage(self):
        out = _call(MathProvider(), "math.ohms_law_voltage",
                    {"current_a": 2, "resistance_ohm": 3})
        assert out["result"] == pytest.approx(6.0)

    def test_mean(self):
        out = _call(MathProvider(), "math.mean", {"values": [1, 2, 3, 4]})
        assert out["result"] == pytest.approx(2.5)

    def test_factorial(self):
        out = _call(MathProvider(), "math.factorial", {"n": 5})
        assert out["result"] == 120

    def test_simple_interest(self):
        out = _call(MathProvider(), "math.simple_interest",
                    {"principal": 1000, "annual_rate_percent": 5, "years": 2})
        assert out["result"] == pytest.approx(100.0)

    def test_sphere_volume(self):
        out = _call(MathProvider(), "math.sphere_volume", {"radius": 1})
        assert out["result"] == pytest.approx(4 / 3 * math.pi)

    def test_loan_emi_formula(self):
        # P=100000, 12% annual, 1y: r=0.01, n=12
        r, n = 0.01, 12
        expected = 100000 * r * (1 + r) ** n / ((1 + r) ** n - 1)
        out = _call(MathProvider(), "math.loan_emi",
                    {"principal": 100000, "annual_rate_percent": 12,
                     "years": 1})
        assert out["result"] == pytest.approx(expected, rel=1e-9)

    def test_negative_radius_errors(self):
        out = _call(MathProvider(), "math.circle_area", {"radius": -1})
        assert "error" in out

    def test_invalid_triangle_errors(self):
        out = _call(MathProvider(), "math.triangle_area_heron",
                    {"side_a": 1, "side_b": 2, "side_c": 10})
        assert "error" in out

    def test_division_by_zero_errors(self):
        out = _call(MathProvider(), "math.speed",
                    {"distance_m": 100, "time_s": 0})
        assert "error" in out

    def test_quadratic_complex_roots_honest(self):
        out = _call(MathProvider(), "math.quadratic_roots",
                    {"a": 1, "b": 0, "c": 1})
        assert out["result"]["root1"] == "0.0+1.0i"
        assert out["result"]["root2"] == "0.0-1.0i"

    def test_quadratic_real_roots(self):
        out = _call(MathProvider(), "math.quadratic_roots",
                    {"a": 1, "b": -3, "c": 2})
        roots = sorted([out["result"]["root1"], out["result"]["root2"]])
        assert roots == pytest.approx([1.0, 2.0])

    def test_every_formula_smoke(self):
        """Every formula resolves and its handler returns a dict (no raise)."""
        prov = MathProvider()
        smoke_args = {
            "geometry": {"radius": 2, "height": 3, "base": 4, "length": 5,
                         "width": 6, "side": 2, "diameter": 4,
                         "side_a": 3, "side_b": 4, "side_c": 5,
                         "leg_a": 3, "leg_b": 4, "base1": 2, "base2": 4,
                         "diagonal1": 4, "diagonal2": 6, "semi_major": 3,
                         "semi_minor": 2, "outer_radius": 5, "inner_radius": 3,
                         "angle_degrees": 90, "x1": 0, "y1": 0, "x2": 3,
                         "y2": 4, "base_area": 9, "radius_top": 2,
                         "radius_bottom": 4},
            "finance": {"principal": 1000, "annual_rate_percent": 5,
                        "years": 2, "compounds_per_year": 4, "gain": 150,
                        "cost": 100, "revenue": 200, "selling_price": 120,
                        "original_price": 100, "sale_price": 80, "price": 100,
                        "tax_rate_percent": 10, "bill": 50, "tip_percent": 15,
                        "nominal_rate_percent": 6, "salvage_value": 100,
                        "life_years": 5, "begin_value": 100, "end_value": 150,
                        "fixed_costs": 1000, "price_per_unit": 20,
                        "variable_cost_per_unit": 8, "present_value": 100,
                        "future_value": 150, "rate_percent": 5, "periods": 3},
            "physics": {"mass_kg": 2, "velocity_ms": 3, "height_m": 5,
                        "acceleration_ms2": 2, "distance_m": 100, "time_s": 10,
                        "speed_ms": 10, "current_a": 2, "resistance_ohm": 3,
                        "voltage_v": 6, "force_n": 10, "area_m2": 2,
                        "volume_m3": 4, "period_s": 0.5, "frequency_hz": 50,
                        "wave_speed_ms": 340, "v_initial_ms": 0,
                        "v_final_ms": 20, "length_m": 1,
                        "angle_degrees": 45},
            "statistics": {"values": [1, 2, 3, 4, 5], "p": 50, "value": 3,
                           "mean": 3, "stdev": 1, "x_values": [1, 2, 3],
                           "y_values": [2, 4, 6]},
            "algebra": {"a": 1, "b": -3, "c": 2, "n": 5, "k": 2,
                        "part": 25, "whole": 100, "old_value": 100,
                        "new_value": 120, "value": 8, "base": 2,
                        "exponent": 3},
        }
        for fname, spec in FORMULAS.items():
            tool = prov.resolve(f"math.{fname}")
            assert tool is not None, fname
            args = {}
            for aname, _kind, _desc in spec["args"]:
                if aname in smoke_args[spec["category"]]:
                    args[aname] = smoke_args[spec["category"]][aname]
                elif aname in spec["defaults"]:
                    args[aname] = spec["defaults"][aname]
                else:
                    pytest.fail(f"no smoke arg for {fname}.{aname}")
            out = tool.handler(args)
            assert isinstance(out, dict), fname


# ===========================================================================
# gen
# ===========================================================================

class TestGen:
    def test_expand(self):
        assert GenProvider().expand() == 6

    def test_uuid4_format(self):
        out = _call(GenProvider(), "gen.uuid4", {})
        parsed = uuid.UUID(out["uuid"], version=4)  # raises if malformed
        assert str(parsed) == out["uuid"]

    def test_password_length(self):
        out = _call(GenProvider(), "gen.password",
                    {"length": 20, "symbols": True})
        assert len(out["password"]) == 20

    def test_password_no_symbols(self):
        out = _call(GenProvider(), "gen.password",
                    {"length": 32, "symbols": False})
        assert len(out["password"]) == 32
        assert re.fullmatch(r"[A-Za-z0-9]{32}", out["password"])

    def test_password_bad_length(self):
        out = _call(GenProvider(), "gen.password", {"length": 2})
        assert "error" in out

    def test_lorem_paragraphs(self):
        out = _call(GenProvider(), "gen.lorem", {"paragraphs": 2})
        assert out["paragraphs"] == 2
        assert "Lorem ipsum" in out["text"]

    def test_lorem_words(self):
        out = _call(GenProvider(), "gen.lorem", {"words": 10})
        assert len(out["text"].split()) == 10

    def test_slug(self):
        out = _call(GenProvider(), "gen.slug", {"text": "Hello, World! 2026"})
        assert out["slug"] == "hello-world-2026"

    def test_timestamp(self):
        out = _call(GenProvider(), "gen.timestamp", {})
        assert isinstance(out["epoch"], int) and out["epoch"] > 0
        assert "T" in out["iso_utc"] and out["iso_utc"].endswith("+00:00")

    def test_random_int(self):
        out = _call(GenProvider(), "gen.random_int",
                    {"min_value": 5, "max_value": 5})
        assert out["value"] == 5
        out2 = _call(GenProvider(), "gen.random_int", {})
        assert 1 <= out2["value"] <= 100

    def test_random_int_bad_range(self):
        out = _call(GenProvider(), "gen.random_int",
                    {"min_value": 10, "max_value": 1})
        assert "error" in out

    def test_unknown_gen(self):
        assert GenProvider().resolve("gen.nope") is None


# ===========================================================================
# languages: 12 + 48 = 60
# ===========================================================================

EXPECTED_GREETINGS = {
    "it": "Ciao", "ja": "こんにちは", "zh": "你好", "ko": "안녕하세요",
    "ru": "Здравствуйте", "tr": "Merhaba", "el": "Γεια σας",
    "he": "שלום", "th": "สวัสดี", "vi": "Xin chào",
}


class TestLanguagesExtra:
    def test_counts(self):
        assert len(EXTRA_LANGUAGE_CODES) == 48
        assert len(ALL_LANGUAGE_CODES) == 60
        assert ExtendedGreetProvider().expand() == 60
        assert ExtendedSayProvider().expand() == 60

    def test_no_overlap_with_base(self):
        from jarvis.tools.dynamic.languages import LANGUAGE_CODES
        assert len(LANGUAGE_CODES) == 12
        assert not set(EXTRA_LANGUAGE_CODES) & set(LANGUAGE_CODES)

    def test_spot_check_greetings_exact(self):
        prov = ExtendedGreetProvider()
        for lang, expected in EXPECTED_GREETINGS.items():
            out = _call(prov, f"greet.{lang}", {})
            assert out["greeting"] == expected, lang
            assert out["language"] == lang
            assert out["language_name"]  # correct non-empty name

    def test_all_greetings_nonempty(self):
        prov = ExtendedGreetProvider()
        for lang in ALL_LANGUAGE_CODES:
            out = _call(prov, f"greet.{lang}", {})
            assert out["greeting"] and out["greeting"].strip(), lang

    def test_base_languages_still_work(self):
        out = _call(ExtendedGreetProvider(), "greet.en", {})
        assert out["greeting"] == "Hello"
        out = _call(ExtendedGreetProvider(), "greet.hi", {})
        assert out["greeting"] == "नमस्ते"

    def test_unknown_language(self):
        assert ExtendedGreetProvider().resolve("greet.xx") is None
        assert ExtendedSayProvider().resolve("say.xx") is None

    def test_say_honest_error_without_voice(self):
        out = _call(ExtendedSayProvider(), "say.ja", {"text": "hello"})
        # No Piper voice configured in test env -> honest error, never a raise.
        assert "error" in out

    def test_say_requires_text(self):
        out = _call(ExtendedSayProvider(), "say.it", {"text": ""})
        assert "error" in out
