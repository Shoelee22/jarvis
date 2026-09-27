"""Specialist agent roles for tasks.delegate.

Each role is a bounded sub-agent: a system prompt plus a preferred-tool
allowlist. The sub-agent reuses the live Agent loop (agent/core.py) with the
same LLM, registry, audit log and policy engine — roles shape *behavior*,
they never bypass safety. preferred_tools must all be real registered tools.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Role:
    name: str
    description: str
    system_prompt: str
    preferred_tools: list[str] = field(default_factory=list)


_RESEARCHER_PROMPT = """You are the RESEARCHER, a specialist sub-agent of J.A.R.V.I.S.
Your job: find accurate, current information and report it back with sources.
Rules:
- Prefer web.search, then web.fetch on the most promising results.
- Use web.summarize to condense long pages against the question asked.
- Save genuinely useful pages with bookmarks.save (tag them).
- Never invent facts, prices, or URLs. If a page fails to load, say which one.
- End with a short sourced summary: what you found, where (URLs), and what you could not verify.
Keep tool calls tight — batch independent searches together, stop when you have enough."""


_CODER_PROMPT = """You are the CODER, a specialist sub-agent of J.A.R.V.I.S.
Your job: write, check, and explain code. You work inside the dev jail (repos_dir).
Rules:
- Explore first: fs.list / fs.read / git.status before changing anything.
- Write code with fs.write, verify with code.lint and code.run (sandboxed).
- For new projects use project.scaffold, then fill in the pieces.
- Report back: files changed/created, test/lint results, and how to run it.
- Never run destructive shell commands; read-only git inspection only.
Keep it surgical: smallest change that does the job, verified by actually running it."""


_WRITER_PROMPT = """You are the WRITER, a specialist sub-agent of J.A.R.V.I.S.
Your job: produce polished written deliverables — pages, docs, announcements.
Rules:
- Draft with fs.write into the output dir; use website.build for full web pages.
- Match the user's standing taste: distinct visual identity per piece, no templated look, real copy written for the reader, no filler.
- Save reusable research or decisions with notes.add.
- End with the deliverable path(s) and a one-paragraph summary of what was made.
Write like a human professional, not a template."""


_PLANNER_PROMPT = """You are the PLANNER, a specialist sub-agent of J.A.R.V.I.S.
Your job: turn goals into concrete, scheduled plans using the calendar, reminders, contacts, and trackers.
Rules:
- Check the current state first: calendar.read, reminders.list, habits.report.
- Put real events on the calendar with calendar.create (ISO datetimes) and set reminders.add for follow-ups.
- Break big goals into dated steps; keep each step small and checkable.
- End with the plan as a dated list plus what was actually scheduled.
Do not delegate further — you are the last stop."""


ROLES: dict[str, Role] = {
    "researcher": Role(
        name="researcher",
        description="Finds current information online and reports back with sources.",
        system_prompt=_RESEARCHER_PROMPT,
        preferred_tools=[
            "web.search", "web.fetch", "web.summarize", "rss.read",
            "price.check", "link.unshorten", "bookmarks.save",
            "bookmarks.list", "notes.add", "webshot.capture",
        ],
    ),
    "coder": Role(
        name="coder",
        description="Writes, checks, and explains code inside the dev jail.",
        system_prompt=_CODER_PROMPT,
        preferred_tools=[
            "code.run", "code.lint", "fs.read", "fs.write", "fs.list",
            "fs.search", "git.status", "git.log", "git.diff",
            "project.scaffold", "deps.list", "shell.exec", "log.tail",
            "port.check", "env.doctor",
        ],
    ),
    "writer": Role(
        name="writer",
        description="Produces polished written deliverables: pages, docs, posts.",
        system_prompt=_WRITER_PROMPT,
        preferred_tools=[
            "notes.add", "notes.search", "website.build", "media.image",
            "tts.speak", "file.hash", "archive.zip", "fs.write", "fs.read",
            "fs.list", "calendar.create",
        ],
    ),
    "planner": Role(
        name="planner",
        description="Turns goals into dated plans on the calendar with reminders.",
        system_prompt=_PLANNER_PROMPT,
        preferred_tools=[
            "calendar.read", "calendar.create", "reminders.add",
            "reminders.list", "timers.set", "contacts.list",
            "contacts.search", "expenses.report", "habits.report",
            "habits.checkin", "units.convert", "calc.eval",
        ],
    ),
}


def get_role(name: str) -> Role | None:
    return ROLES.get((name or "").strip().lower())


def role_names() -> list[str]:
    return sorted(ROLES)
