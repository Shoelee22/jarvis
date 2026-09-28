"""Education Tool Pack (Phase 10): flashcards, quizzes, explanation scaffolds.

Honest design — NO invented facts:
- edu.flashcards builds cards ONLY from the user-supplied `terms` list
  ({term, definition}). Without it, it returns a labeled scaffold the user
  fills in themselves.
- edu.quiz generates real multiple-choice questions ONLY from the
  user-supplied `word_list` ({term, definition}, >= 4 needed for distractors).
  No generic "placeholder-free questions about a topic" are invented.
- edu.explain returns a structured explanation OUTLINE — a study scaffold
  (sections to research, self-test prompts), labeled as such, never
  authoritative content.

All low risk, offline (needs_network=False).
"""
from __future__ import annotations


# ------------------------------------------------------------ helpers
def _fail(tool: str, e: Exception) -> dict:
    return {"error": f"{tool} failed: {type(e).__name__}: {e}"}


def _count(value, default: int, cap: int = 50) -> int:
    try:
        n = int(value if value is not None else default)
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, cap))


def _clean_terms(raw) -> list[dict]:
    """Normalize [{term, definition}, ...]; drop malformed entries."""
    terms: list[dict] = []
    if not isinstance(raw, list):
        return terms
    for item in raw:
        if not isinstance(item, dict):
            continue
        term = str(item.get("term", "")).strip()
        definition = str(item.get("definition", "")).strip()
        if term and definition:
            terms.append({"term": term, "definition": definition})
    return terms


# --------------------------------------------------- edu.flashcards
def edu_flashcards(args: dict) -> dict:
    """Flashcards from a user-supplied term list, or a fill-in scaffold."""
    try:
        topic = str(args.get("topic", "")).strip()
        if not topic:
            return {"error": "topic is required"}
        n = _count(args.get("count"), 10)
        terms = _clean_terms(args.get("terms"))
        if terms:
            chosen = terms[:n]
            cards = [{"front": t["term"], "back": t["definition"]}
                     for t in chosen]
            return {"topic": topic, "cards": cards, "count": len(cards),
                    "source": "built from your supplied terms",
                    "note": "content comes from the terms YOU provided — "
                            "verify definitions against your course material"}
        cards = [{"front": f"[{topic} term {i}]",
                  "back": "[write the definition in your own words]"}
                 for i in range(1, n + 1)]
        return {"topic": topic, "cards": cards, "count": len(cards),
                "source": "scaffold",
                "honest_note": "SCAFFOLD ONLY — I do not invent study facts. "
                               "Fill in real terms/definitions from your "
                               "textbook or class notes, then quiz yourself."}
    except Exception as e:
        return _fail("edu.flashcards", e)


# ------------------------------------------------------- edu.quiz
def edu_quiz(args: dict) -> dict:
    """Real MCQ quiz generated ONLY from a user-supplied word list."""
    try:
        topic = str(args.get("topic", "")).strip()
        if not topic:
            return {"error": "topic is required"}
        words = _clean_terms(args.get("word_list"))
        if len(words) < 4:
            return {"error": "quiz needs word_list: a list of at least 4 "
                             "{term, definition} entries so wrong options can "
                             "be drawn from real entries. I do not invent "
                             "quiz facts about a topic."}
        n = _count(args.get("count"), min(10, len(words)))
        questions = []
        m = len(words)
        for i, w in enumerate(words[:n]):
            # Deterministic distractors: the next 3 definitions in the list.
            distractors = [words[(i + k) % m]["definition"] for k in (1, 2, 3)]
            correct_pos = i % 4
            options = list(distractors)
            options.insert(correct_pos, w["definition"])
            questions.append({
                "question": f"What does '{w['term']}' mean?",
                "options": options,
                "answer_index": correct_pos,
                "answer": w["definition"],
            })
        return {"topic": topic, "questions": questions,
                "count": len(questions),
                "note": "questions generated from YOUR word_list — verify "
                        "definitions against your course material"}
    except Exception as e:
        return _fail("edu.quiz", e)


# ---------------------------------------------------- edu.explain
def edu_explain(args: dict) -> dict:
    """Structured explanation OUTLINE — a study scaffold, not content."""
    try:
        topic = str(args.get("topic", "")).strip()
        if not topic:
            return {"error": "topic is required"}
        level = str(args.get("level", "beginner")).strip().lower()
        if level not in ("beginner", "intermediate", "advanced"):
            level = "beginner"
        return {
            "topic": topic, "level": level,
            "outline": [
                {"section": "1. Define it",
                 "prompt": f"Write a one-sentence definition of '{topic}' in "
                           "your own words."},
                {"section": "2. Key ideas",
                 "prompt": "List the 3-5 core ideas. For each: what is it, "
                           "and why does it matter?"},
                {"section": "3. Example",
                 "prompt": "Find one concrete real-world example and explain "
                           "how it connects to each key idea."},
                {"section": "4. Common confusions",
                 "prompt": "What do learners usually mix up here? Write the "
                           "distinction explicitly."},
                {"section": "5. Self-test",
                 "prompt": "Close your notes and explain the topic aloud in "
                           "60 seconds. Note where you got stuck."},
            ],
            "honest_note": "STUDY SCAFFOLD — this is a learning structure to "
                           "fill in from trusted sources (textbook, teacher, "
                           "documentation), not authoritative content."}
    except Exception as e:
        return _fail("edu.explain", e)


# ------------------------------------------------- registry wiring metadata
TOOL_DEFS = [
    {"name": "edu.flashcards",
     "description": "Flashcards built only from your supplied terms "
                    "(or a labeled fill-in scaffold).",
     "handler": edu_flashcards, "risk": "low", "needs_network": False,
     "schema": {"topic": "string", "count": "int?", "terms": "list?"}},
    {"name": "edu.quiz",
     "description": "Multiple-choice quiz generated only from your supplied "
                    "word list (>=4 entries).",
     "handler": edu_quiz, "risk": "low", "needs_network": False,
     "schema": {"topic": "string", "count": "int?", "word_list": "list"}},
    {"name": "edu.explain",
     "description": "Structured explanation outline (study scaffold, not "
                    "authoritative content).",
     "handler": edu_explain, "risk": "low", "needs_network": False,
     "schema": {"topic": "string", "level": "string?"}},
]

# (risk, needs_network) additions for agent/policy.py's RISK_TABLE —
# without these, the policy engine default-denies unknown tools.
# Parent wiring:  from jarvis.agent.policy import RISK_TABLE
#                 RISK_TABLE.update(edu_pack.RISK_TABLE_ADDITIONS)
RISK_TABLE_ADDITIONS = {
    "edu.flashcards": ("low", False),
    "edu.quiz": ("low", False),
    "edu.explain": ("low", False),
}


def register(reg) -> None:
    """Wire the three education tools into a Registry."""
    from ..base import Tool
    for spec in TOOL_DEFS:
        reg.register(Tool(name=spec["name"], description=spec["description"],
                          schema=spec["schema"], handler=spec["handler"],
                          risk=spec["risk"], needs_network=spec["needs_network"]))


__all__ = ["TOOL_DEFS", "RISK_TABLE_ADDITIONS", "register",
           "edu_flashcards", "edu_quiz", "edu_explain"]
