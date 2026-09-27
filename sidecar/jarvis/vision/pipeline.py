"""Vision pipeline interfaces. Frames are RAM-only unless the user asks to save."""
from __future__ import annotations
from dataclasses import dataclass, field


class VisionModel:
    def describe(self, frame: bytes, prompt: str = "What do you see?") -> str:
        raise NotImplementedError


class FaceID:
    def enroll(self, frame: bytes, name: str): raise NotImplementedError
    def identify(self, frame: bytes) -> str: raise NotImplementedError  # name | "stranger" | "none"


class WatchTrigger:
    def person_present(self, frame: bytes) -> bool: raise NotImplementedError


@dataclass
class FakeVision(VisionModel):
    descriptions: list[str] = field(default_factory=lambda: ["a room with a desk"])

    def describe(self, frame: bytes, prompt: str = "What do you see?") -> str:
        return self.descriptions[0] if self.descriptions else "nothing visible"


@dataclass
class FakeFaceID(FaceID):
    owner: str = "owner"

    def enroll(self, frame: bytes, name: str):
        self.owner = name

    def identify(self, frame: bytes) -> str:
        return self.owner if frame else "none"


class FakeWatch(WatchTrigger):
    def person_present(self, frame: bytes) -> bool:
        return bool(frame)


def load_real_vision(model_path: str) -> VisionModel:
    """Qwen2.5-VL via llama.cpp server (mtmd). Wired in Phase 2 on real hardware."""
    raise NotImplementedError("needs models/ + GPU/CPU on target machine")
