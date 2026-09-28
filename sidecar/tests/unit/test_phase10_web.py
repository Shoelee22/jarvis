"""Phase 10 web expansion tests: structural contract for web_extra.py.

Run from ~/workspace/jarvis/sidecar:
    python -m pytest tests/unit/test_phase10_web.py -q
"""
import re
import urllib.parse

from jarvis.tools.dynamic.web_actions import SITES_BASE
from jarvis.tools.dynamic.web_extra import PAIR_COUNT, WEB_EXTRA


def _filled(template: str) -> str:
    """Substitute a realistic query so the URL parses like the real one."""
    if "{q}" in template:
        return template.replace("{q}", urllib.parse.quote_plus("iphone 15"))
    return template


# ---------------------------------------------------------------- contract
def test_site_count():
    assert len(WEB_EXTRA) >= 200, f"expected >= 200 sites, got {len(WEB_EXTRA)}"


def test_pair_count_constant():
    assert PAIR_COUNT == sum(len(a) for a in WEB_EXTRA.values())
    assert PAIR_COUNT >= 200


def test_every_site_has_action():
    for site, actions in WEB_EXTRA.items():
        assert isinstance(actions, dict) and actions, f"{site}: no actions"


def test_templates_are_absolute_https_with_placeholder_or_fixed_page():
    for site, actions in WEB_EXTRA.items():
        for action, template in actions.items():
            assert isinstance(template, str), f"{site}.{action}"
            assert template.startswith("https://"), (
                f"{site}.{action}: not absolute https -> {template}"
            )
            # Either a query placeholder, or a documented static landing page.
            assert "{q}" in template or " " not in template, (
                f"{site}.{action}: missing placeholder and malformed: {template}"
            )


def test_templates_parse_as_urls():
    for site, actions in WEB_EXTRA.items():
        for action, template in actions.items():
            filled = _filled(template)
            parts = urllib.parse.urlparse(filled)
            assert parts.scheme == "https", f"{site}.{action}: {filled}"
            assert parts.netloc and "." in parts.netloc, (
                f"{site}.{action}: bad host -> {filled}"
            )


def test_no_duplicate_keys_vs_web_actions():
    dupes = set(WEB_EXTRA) & set(SITES_BASE)
    assert not dupes, f"site keys already in web_actions.py: {sorted(dupes)}"


def test_merge_is_collision_free():
    merged = {k: dict(v) for k, v in SITES_BASE.items()}
    for _k, _v in WEB_EXTRA.items():
        merged.setdefault(_k, {}).update(_v)
    assert len(merged) == len(SITES_BASE) + len(WEB_EXTRA)
    # every expanded tool name stays unique
    names = [f"web.{s}.{a}" for s, acts in merged.items() for a in acts]
    assert len(names) == len(set(names))


def test_unverified_markers_are_comments_only():
    """The '# UNVERIFIED PATTERN' annotations must not leak into templates."""
    for site, actions in WEB_EXTRA.items():
        for action, template in actions.items():
            assert "UNVERIFIED" not in template, f"{site}.{action}"


def test_handlers_compatible_with_make_handler_shape():
    """Templates must survive the same substitution web_actions.py uses."""
    for site, actions in WEB_EXTRA.items():
        for action, template in actions.items():
            url = (
                template.replace("{q}", urllib.parse.quote_plus("a b&c=d"))
                if "{q}" in template
                else template
            )
            assert url.startswith("https://"), f"{site}.{action}"
            # no unsubstituted braces remain
            assert "{" not in url and "}" not in url, f"{site}.{action}: {url}"


def test_category_coverage_spotcheck():
    """Rough category coverage: each broad category should have >= 5 sites."""
    categories = {
        "video": {"rumble", "odysee", "peertube", "bilibili", "niconico", "rutube"},
        "music": {"pandora", "tidal", "amazonmusic", "musixmatch", "azlyrics"},
        "shopping": {"costco", "ikea", "sephora", "temu", "zalando"},
        "news": {"reuters", "apnews", "aljazeera", "nypost", "spiegel"},
        "reference": {"cambridgedict", "wikidata", "gutenberg", "jstor", "oeis"},
        "dev": {"bitbucket", "djangodocs", "hoogle", "rustdocs", "kubedocs"},
        "travel": {"rome2rio", "viator", "lonelyplanet", "wikivoyage"},
        "food": {"zomato", "eater", "bonappetit", "untappd", "vivino"},
        "maps": {"applemaps", "mapquest", "here", "geonames", "waze"},
        "social": {"mastodon", "meetup", "eventbrite", "discogs", "patreon"},
        "finance": {"morningstar", "coinmarketcap", "etherscan", "secedgar"},
        "education": {"khanacademy", "edx", "mitocw", "classcentral"},
        "jobs": {"glassdoor", "weworkremotely", "usajobs", "naukri", "adzuna"},
    }
    for cat, keys in categories.items():
        missing = keys - set(WEB_EXTRA)
        assert not missing, f"category {cat} missing: {sorted(missing)}"
