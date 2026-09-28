"""Opinions: the assistant's recorded stances on classic debate topics.

One dynamic tool:
  opinion.get  {topic} -> {"topic", "stance", "reasoning"}
               unknown topics -> {"topic", "stance": None, "note": ...}

Stances come from the built-in OPINIONS map, optionally extended/overridden by
a JSON/YAML file at the ``opinions.file`` config section
(``tools_config.dynamic.yaml``). Handlers are dict->dict, never raise, offline.
"""
from __future__ import annotations

import json
from pathlib import Path

from ..base import Tool
from .config import section
from .providers import Provider

# Built-in stance map: topic -> {"stance", "reasoning"}.
OPINIONS: dict[str, dict[str, str]] = {
    "pineapple on pizza": {
        "stance": "pro",
        "reasoning": "Sweet-acid contrast against salty cheese is a legitimate flavor pairing; dislike is cultural, not culinary.",
    },
    "tabs vs spaces": {
        "stance": "spaces",
        "reasoning": "Spaces render identically everywhere; tabs depend on editor settings. Consistency beats keystrokes.",
    },
    "vim vs emacs": {
        "stance": "vim motions",
        "reasoning": "Modal editing composes better than chorded shortcuts; the motions transfer to every editor worth using.",
    },
    "static typing": {
        "stance": "pro",
        "reasoning": "Types are machine-checked documentation. They pay for themselves the first time someone refactors.",
    },
    "comments in code": {
        "stance": "why-not-what",
        "reasoning": "Explain the why, never the what. A comment restating the code is a lie waiting to happen.",
    },
    "meetings": {
        "stance": "skeptical",
        "reasoning": "Most meetings are status updates wearing a calendar invite. Default to async; meet only to decide.",
    },
    "dark mode": {
        "stance": "pro",
        "reasoning": "Easier on the eyes in dim rooms and honest about OLED power savings. Light mode apologists fear the dark.",
    },
    "monorepo vs multirepo": {
        "stance": "monorepo",
        "reasoning": "Atomic cross-project changes beat version-dance choreography. Tooling has caught up; excuses haven't.",
    },
    "ai pair programming": {
        "stance": "pro",
        "reasoning": "A fast, tireless junior that never sleeps. Review everything it writes, like you would any junior.",
    },
    "100% test coverage": {
        "stance": "against",
        "reasoning": "Coverage is a side effect of good tests, not a goal. Chasing the number produces tests that assert nothing.",
    },
    "microservices": {
        "stance": "skeptical",
        "reasoning": "Start with a modular monolith. Split services when organizational or scaling pressure demands it, not before.",
    },
    "remote work": {
        "stance": "pro",
        "reasoning": "Output is what matters; commute time is dead time. In-person still wins for trust-building and hard debates.",
    },
    "chai vs coffee": {
        "stance": "chai",
        "reasoning": "Chai is a ritual; coffee is a transaction. Slow-brewed with ginger beats a paper cup of urgency.",
    },
    "smartphone upgrades": {
        "stance": "skeptical",
        "reasoning": "Yearly upgrades are a tax on impatience. Replace the battery, keep the phone.",
    },
    "hustle culture": {
        "stance": "against",
        "reasoning": "Burnout is not a badge. Sustainable pace beats heroic sprints; the tortoise had it right.",
    },
    "cloud vs local": {
        "stance": "local-first",
        "reasoning": "Your data on your machine is a fact; in someone else's cloud it is a promise.",
    },
    "mornings": {
        "stance": "pro",
        "reasoning": "Mornings are the only hours nobody can interrupt. Guard them accordingly.",
    },
    "gym": {
        "stance": "pro",
        "reasoning": "The cheapest therapy with the best side effects. Show up; motivation follows action, not the reverse.",
    },
    "street food": {
        "stance": "pro",
        "reasoning": "The best restaurant in any city has no roof. Follow the crowd of locals.",
    },
    "reading books": {
        "stance": "pro",
        "reasoning": "A book is a conversation with someone brilliant who cannot interrupt you.",
    },
    "social media": {
        "stance": "skeptical",
        "reasoning": "A useful tool and a terrible master. Post with intent; scroll with a timer.",
    },
    "electric vehicles": {
        "stance": "pro",
        "reasoning": "Fewer moving parts, instant torque, silent streets. The future is already quieter.",
    },
    "cricket vs football": {
        "stance": "cricket",
        "reasoning": "Cricket rewards patience and narrative; football rewards ninety minutes of held breath. Both beat scrolling.",
    },
    "biryani": {
        "stance": "pro",
        "reasoning": "Layered, patient, unapologetic. The rare dish that is also an argument-ender.",
    },
    "note-taking apps": {
        "stance": "skeptical",
        "reasoning": "The best note system is the one you actually revisit. Fancy apps are procrastination with sync.",
    },
    "multitasking": {
        "stance": "against",
        "reasoning": "Multitasking is doing several things badly at once. Single-tasking is a superpower.",
    },
    "weekend lie-ins": {
        "stance": "occasionally",
        "reasoning": "Rest is productive. But a lie-in past noon is just jet lag you gave yourself.",
    },
    "open source": {
        "stance": "pro",
        "reasoning": "Civilization advances when people share the recipe. Contribute back when you can.",
    },
    "ai hype": {
        "stance": "skeptical",
        "reasoning": "Useful tool, inflated promises. Judge every demo by what it does without the presenter in the room.",
    },
}


def _load_file_opinions() -> dict[str, dict[str, str]]:
    """Optional override file from the opinions.file config section."""
    path = (section("opinions", {}) or {}).get("file") or ""
    if not path:
        return {}
    try:
        text = Path(str(path)).expanduser().read_text(encoding="utf-8")
    except Exception:
        return {}
    try:
        data = json.loads(text)
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    out: dict[str, dict[str, str]] = {}
    for topic, val in data.items():
        if isinstance(val, str):
            out[str(topic)] = {"stance": val, "reasoning": ""}
        elif isinstance(val, dict):
            out[str(topic)] = {
                "stance": str(val.get("stance", "")),
                "reasoning": str(val.get("reasoning", "")),
            }
    return out


def _all_opinions() -> dict[str, dict[str, str]]:
    merged = dict(OPINIONS)
    merged.update(_load_file_opinions())
    return merged


def _find(topic: str) -> tuple[str | None, dict | None]:
    """Case-insensitive exact match, then substring match. Returns (key, entry)."""
    opinions = _all_opinions()
    t = (topic or "").strip().lower()
    if not t:
        return None, None
    for key in opinions:
        if key.lower() == t:
            return key, opinions[key]
    for key in opinions:
        if t in key.lower() or key.lower() in t:
            return key, opinions[key]
    return None, None


class OpinionProvider(Provider):
    """Single-tool namespace: opinion.get. expand() == 1."""

    namespace = "opinion"

    def expand(self) -> int:
        return 1

    def resolve(self, name: str) -> Tool | None:
        if not self._owns(name):
            return None
        if self._local(name) != "get":
            return None

        def _get(args: dict) -> dict:
            try:
                topic = str((args or {}).get("topic", "") or "")
                if not topic.strip():
                    return {"error": "topic is required"}
                key, entry = _find(topic)
                if entry is None:
                    return {
                        "topic": topic,
                        "stance": None,
                        "note": "no recorded opinion on this topic",
                    }
                return {
                    "topic": key,
                    "stance": entry.get("stance"),
                    "reasoning": entry.get("reasoning", ""),
                }
            except Exception as e:  # never raise
                return {"error": f"{type(e).__name__}: {e}"}

        return Tool(
            name=name,
            description="Get the assistant's recorded stance on a debate topic (None if unknown).",
            schema={"topic": "string"},
            handler=_get,
            risk="low",
        )

    def sample_names(self, n: int = 5) -> list[str]:
        return [self._dotted("get")][: max(0, n)]
