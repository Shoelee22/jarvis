"""Phase 10 (workstream D1): joke bank expansion checks.

Covers jarvis.tools.dynamic.jokes_extra (JOKES_EXTRA), the expansion of the
Phase 7 joke bank:

- >= 730 entries total
- all 10 of jokes.py's categories present, >= 60 entries each
- no duplicate joke text (case-insensitive) within the file or vs jokes.py
- no id collisions with jokes.py
- every entry has exactly the required keys with non-empty strings
"""
from __future__ import annotations

from collections import Counter

from jarvis.tools.dynamic import jokes as base
from jarvis.tools.dynamic import jokes_extra as extra


def test_count_at_least_730():
    assert len(extra.JOKES_EXTRA) >= 730, f"only {len(extra.JOKES_EXTRA)} jokes"


def test_all_ten_categories_with_min_60_each():
    counts = Counter(j["category"] for j in extra.JOKES_EXTRA)
    assert set(counts) == set(base.CATEGORIES), (
        f"category mismatch: extra={set(counts)}, base={set(base.CATEGORIES)}"
    )
    for cat in base.CATEGORIES:
        assert counts[cat] >= 60, f"category {cat!r} has only {counts[cat]} jokes"


def test_entry_shape_and_non_empty():
    required = {"id", "category", "text"}
    for j in extra.JOKES_EXTRA:
        assert set(j.keys()) == required, f"bad keys in {j!r}"
        assert isinstance(j["id"], str) and j["id"].strip(), f"bad id in {j!r}"
        assert isinstance(j["category"], str) and j["category"].strip(), f"bad category in {j!r}"
        assert isinstance(j["text"], str) and j["text"].strip(), f"bad text in {j!r}"
        assert j["category"] in base.CATEGORIES, f"unknown category in {j!r}"


def test_unique_ids_within_file():
    ids = [j["id"] for j in extra.JOKES_EXTRA]
    assert len(ids) == len(set(ids)), "duplicate ids within JOKES_EXTRA"


def test_ids_do_not_collide_with_base():
    base_ids = {j["id"] for j in base.JOKES_BASE}
    extra_ids = {j["id"] for j in extra.JOKES_EXTRA}
    collision = base_ids & extra_ids
    assert not collision, f"id collision with jokes.py: {sorted(collision)[:5]}"


def test_no_duplicate_text_within_file():
    texts = [j["text"].strip().lower() for j in extra.JOKES_EXTRA]
    dupes = [t for t, n in Counter(texts).items() if n > 1]
    assert not dupes, f"duplicate texts within JOKES_EXTRA: {dupes[:3]}"


def test_no_duplicate_text_vs_base():
    base_texts = {j["text"].strip().lower() for j in base.JOKES_BASE}
    overlap = [j["text"] for j in extra.JOKES_EXTRA if j["text"].strip().lower() in base_texts]
    assert not overlap, f"{len(overlap)} jokes duplicate jokes.py: {overlap[:3]}"
