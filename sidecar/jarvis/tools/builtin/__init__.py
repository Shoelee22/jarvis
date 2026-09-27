"""Wire built-in tools into a Registry."""
from __future__ import annotations
from pathlib import Path

from ..base import Registry, Tool
from . import fs_tools, shell_tools, productivity, misc, net_tools, creator, comms, system_pack, home_pack, data_pack, web_pack, dev_pack, media_pack, delegate_tool, gui_pack, browser_pack, pc_pack, telegram_pack, gmail_pack, studio_pack, doctor_pack, forge_pack, swarm_pack, workers_pack, phone_pack, inbox_pack, pdf_pack, translate_pack, finance_pack, travel_pack, edu_pack, health_pack, devops_pack, news_pack, weather_pack, permissions_pack, autopilot_pack, proactive_pack, autonomy_pack, voice_pack, vision_pack, mind_pack, shell_pack, teach_pack, sleep_pack, self_pack, loops_pack, persona_pack, brain_pack, knowledge_pack, episodic_pack, research_pack, analyst_pack, builder_pack, crm_pack, system1_pack
from ..plugins import loader as plugin_loader


def build_registry(data_dir: str | Path) -> Registry:
    reg = Registry()
    store = productivity.Store(Path(data_dir) / "jarvis.db")

    def T(name, desc, handler, risk="low", needs_network=False, schema=None):
        reg.register(Tool(name, desc, schema or {}, handler, risk, needs_network))

    T("fs.read", "Read a text file (jailed to granted roots).", fs_tools.read,
      schema={"path": "string"})
    T("fs.write", "Write a text file.", fs_tools.write, risk="medium",
      schema={"path": "string", "content": "string"})
    T("fs.delete", "Delete a file. IRREVERSIBLE — needs confirmation.", fs_tools.delete,
      risk="high", schema={"path": "string"})
    T("fs.list", "List directory entries.", fs_tools.list_dir, schema={"path": "string?"})
    T("fs.search", "Search filenames under a root.", fs_tools.search,
      schema={"root": "string?", "pattern": "string"})
    T("shell.exec", "Run a shell command (guardrailed; destructive cmds need confirmation).",
      shell_tools.exec_cmd, risk="medium", schema={"cmd": "string", "timeout": "int?"})
    T("reminders.add", "Add a reminder (cron or once_at unix ts).", store.add_reminder,
      schema={"text": "string", "cron": "string?", "once_at": "int?"})
    T("reminders.list", "List pending reminders.", store.list_reminders)
    T("timers.set", "Set a timer.", productivity.set_timer,
      schema={"seconds": "int", "label": "string?"})
    T("notes.add", "Save a note.", store.add_note, schema={"text": "string"})
    T("notes.search", "Search notes.", store.search_notes, schema={"query": "string"})
    T("calc.eval", "Evaluate a math expression safely.", misc.calc,
      schema={"expression": "string"})
    T("clipboard.read", "Read clipboard text.", misc.clipboard_read, risk="medium")
    # --- Phase 6 / Pack 1: comms (replaces the net_tools stubs above) ---
    comms.register_tools(reg)
    # --- Phase 6 / Pack 6: system ---
    T("notify.send", "Send a desktop notification.", system_pack.notify_send,
      schema={"title": "string", "body": "string"})
    T("screen.capture", "Capture the primary display to a PNG.", system_pack.screen_capture,
      schema={"out": "string?"})
    T("clipboard.write", "Write text to the OS clipboard.", system_pack.clipboard_write,
      risk="medium", schema={"text": "string"})
    T("battery.status", "Battery percent and charge state (honest error on desktops).",
      system_pack.battery_status)
    T("disk.usage", "Disk usage for a path.", system_pack.disk_usage,
      schema={"path": "string?"})
    T("process.top", "Top-N processes by CPU.", system_pack.process_top,
      schema={"n": "int?"})
    T("system.uptime", "System uptime.", system_pack.system_uptime)
    # --- Phase 6 / Pack 7: smart home (all backends config-gated) ---
    home_pack.register(reg)
    # --- Phase 6 / Pack 3: data ---
    for _name, _desc, _handler, _risk, _net in data_pack.REGISTRATIONS:
        reg.register(Tool(_name, _desc, {}, _handler, _risk, _net))
    # --- Phase 6 / Pack 4: dev ---
    for _t in dev_pack.TOOLS:
        reg.register(Tool(_t["name"], _t["description"], _t.get("schema", {}),
                          _t["handler"], _t["risk"], _t["needs_network"]))
    # --- Phase 6 / Pack 5: media+ ---
    T("tts.speak", "Speak text with the Piper voice (honest error if no voice model).",
      media_pack.tts_speak, schema={"text": "string", "voice": "string?"})
    T("audio.transcribe", "Transcribe audio with faster-whisper (honest error if unavailable).",
      media_pack.audio_transcribe, schema={"path": "string"})
    T("image.resize", "Resize an image (needs PIL).", media_pack.image_resize,
      schema={"path": "string", "width": "int", "height": "int", "out": "string?"})
    T("image.convert", "Convert an image format (needs PIL).", media_pack.image_convert,
      schema={"path": "string", "format": "string", "out": "string?"})
    T("video.trim", "Trim a video with ffmpeg.", media_pack.video_trim,
      schema={"path": "string", "start": "number", "end": "number", "out": "string?"})
    T("video.gif", "Convert a video clip to GIF with ffmpeg.", media_pack.video_gif,
      schema={"path": "string", "out": "string?", "fps": "int?", "width": "int?"})
    T("video.contact_sheet", "Build a thumbnail contact sheet from a video.",
      media_pack.video_contact_sheet, schema={"path": "string", "cols": "int?", "rows": "int?"})
    T("file.hash", "Hash a file (sha256 default).", media_pack.file_hash,
      schema={"path": "string", "algo": "string?"})
    T("archive.zip", "Create a zip archive (jailed under home).", media_pack.archive_zip,
      risk="medium", schema={"paths": "list", "out": "string"})
    T("archive.unzip", "Extract a zip (zip-slip protected, dest jailed).", media_pack.archive_unzip,
      risk="medium", schema={"path": "string", "dest": "string?"})
    # --- Phase 6: specialist delegation ---
    T("tasks.delegate", "Hand a task to a specialist role (researcher/coder/writer/planner).",
      delegate_tool.delegate_handler, risk="medium",
      schema={"role": "string", "task": "string", "max_steps": "int?"})
    T("browser.task", "Do a web task in a local browser (booking, forms). IRREVERSIBLE steps need confirmation.",
      net_tools.browser_task, risk="high", needs_network=True,
      schema={"task": "string", "allow_domains": "list?"})
    # --- Phase 6 / Pack 2: web (replaces the net_tools web_search stub) ---
    web_pack.register(reg)
    T("camera.describe", "Describe what the camera sees.", net_tools.camera_describe,
      risk="medium", schema={"prompt": "string?", "save": "bool?"})
    T("screen.describe", "Describe the current screen.", net_tools.screen_describe,
      risk="medium")
    # --- Phase 4: Creator Tool Pack ---
    T("code.run", "Run python3 code in a jailed sandbox; capture stdout/stderr.",
      creator.code_run, risk="medium", schema={"code": "string", "timeout": "int?"})
    T("website.build", "Generate a single-file responsive HTML site from a brief.",
      creator.website_build, risk="low",
      schema={"name": "string", "brief": "string", "business_type": "string?"})
    T("media.image", "Generate an image from a prompt (Pollinations API).",
      creator.media_image, risk="low", needs_network=True,
      schema={"prompt": "string", "width": "int?", "height": "int?"})
    T("video.compose", "Compose a Ken Burns slideshow MP4 from images (ffmpeg).",
      creator.video_compose, risk="low",
      schema={"images": "list", "seconds_each": "float?", "orientation": "string?",
              "out_name": "string?", "audio": "string?"})
    T("social.post_instagram", "Post an image to Instagram. IRREVERSIBLE — needs confirmation.",
      creator.social_post_instagram, risk="high", needs_network=True,
      schema={"file": "string", "caption": "string", "dry_run": "bool?"})

    # --- Phase 7: dynamic tool universe (150k+ lazily-resolved tools) ---
    from ..dynamic import build_dynamic_registry
    reg.dynamic = build_dynamic_registry(reg, data_dir)

    # --- Phase 8: capability frontier packs ---
    for _pack in (gui_pack, browser_pack, pc_pack, telegram_pack, gmail_pack,
                  studio_pack, doctor_pack):
        for _t in _pack.TOOL_DEFS:
            reg.register(Tool(_t["name"], _t["description"], _t.get("schema", {}),
                              _t["handler"], _t["risk"], _t["needs_network"]))
    plugin_loader.register(reg)  # plugins.list/reload + discovered plugin tools
    from ...agent import policy as _policy
    _policy.RISK_TABLE.update(plugin_loader.risk_table_entries())

    # --- Phase 9: self-extending frontier packs ---
    for _pack in (forge_pack, swarm_pack, workers_pack, phone_pack, inbox_pack):
        _pack.register(reg)
        _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    # --- Phase 10: everyday packs ---
    for _pack in (pdf_pack, translate_pack, finance_pack, travel_pack, edu_pack,
                  health_pack, devops_pack, news_pack, weather_pack):
        _pack.register(reg)
        _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    # --- Phase 11: AGI autonomy packs ---
    for _pack in (permissions_pack, autopilot_pack, proactive_pack, autonomy_pack):
        _pack.register(reg)
        _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    # --- Phase 12: advanced packs (voice, vision, memory, shell, macros) ---
    for _pack in (voice_pack, vision_pack, mind_pack, shell_pack, teach_pack):
        _pack.register(reg)
        _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    # --- Phase 13: capstone packs (sleep, self-upgrade, loops, persona) ---
    for _pack in (sleep_pack, self_pack, loops_pack, persona_pack):
        _pack.register(reg)
        _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    # --- Phase 14: hyper capstone packs (brain, knowledge, episodic memory) ---
    for _pack in (brain_pack, knowledge_pack, episodic_pack):
        _pack.register(reg)
        _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    # --- Phase 15: professional packs (research, analyst, builder, crm) ---
    for _pack in (research_pack, analyst_pack, builder_pack, crm_pack):
        _pack.register(reg)
        _policy.RISK_TABLE.update(_pack.RISK_TABLE_ADDITIONS)

    # --- Phase 16: JEV fast System 1 decision layer (Laya, optional dep) ---
    system1_pack.register(reg)
    _policy.RISK_TABLE.update(system1_pack.RISK_TABLE_ADDITIONS)

    return reg
