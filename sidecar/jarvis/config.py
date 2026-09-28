"""Global config: paths, hardware tier, autonomy level."""
import os
from dataclasses import dataclass, field
from pathlib import Path


def _default_data_dir() -> Path:
    """Per-user data dir. Respects JARVIS_DATA; on Windows uses the
    roaming AppData folder (ai.jarvis.shell), elsewhere ~/.jarvis.
    The Rust shell mirrors this exact logic (see shell/src/ipc.rs)."""
    override = os.environ.get("JARVIS_DATA")
    if override:
        return Path(override)
    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata) / "ai.jarvis.shell"
    return Path.home() / ".jarvis"


DATA_DIR = _default_data_dir()
TOKEN_FILE = DATA_DIR / ".token"


@dataclass
class Settings:
    autonomy: str = "assisted"          # manual | assisted | autonomous
    wake_word: str = "jarvis"
    tts_voice: str = "en_IN"
    tts_speed: float = 1.0
    offline_mode: bool = False
    quiet_hours: tuple = (1, 6)         # self-tuning trains 01:00–06:00
    data_dir: Path = field(default_factory=lambda: DATA_DIR)

    def __post_init__(self):
        assert self.autonomy in ("manual", "assisted", "autonomous"), "bad autonomy level"
        self.data_dir.mkdir(parents=True, exist_ok=True)


def detect_tier(mem_gb: float | None = None) -> str:
    if mem_gb is None:
        try:
            with open("/proc/meminfo") as f:
                kb = int(f.readline().split()[1])
            mem_gb = kb / 1024 / 1024
        except Exception:
            mem_gb = 16
    if mem_gb >= 14:
        return "recommended"
    if mem_gb >= 7:
        return "minimum"
    return "below_minimum"
