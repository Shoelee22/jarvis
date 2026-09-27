"""Voice pipeline interfaces. Heavy backends (faster-whisper, Piper) load lazily;
FakeSTT/FakeTTS let the loop run in tests and on machines without models."""
from __future__ import annotations
from dataclasses import dataclass


class STT:
    def transcribe_stream(self, pcm_chunks):  # -> generator of {partial|final, text}
        raise NotImplementedError


class TTS:
    def speak(self, text: str):  # -> generator of audio byte chunks
        raise NotImplementedError


class WakeWord:
    def start(self, on_wake):  # on_wake() callback; runs until stop()
        raise NotImplementedError

    def stop(self):
        raise NotImplementedError


@dataclass
class FakeSTT(STT):
    script: list[str] = None

    def transcribe_stream(self, pcm_chunks):
        for line in (self.script or ["hello jarvis"]):
            yield {"type": "final", "text": line}


@dataclass
class FakeTTS(TTS):
    spoken: list[str] = None

    def __post_init__(self):
        self.spoken = []

    def speak(self, text: str):
        self.spoken.append(text)
        yield b"\x00" * 320  # one fake 10ms frame


class FakeWakeWord(WakeWord):
    def start(self, on_wake):
        on_wake()

    def stop(self):
        pass


def load_real_stt(model_id: str = "small") -> STT:
    from faster_whisper import WhisperModel  # type: ignore

    class RealSTT(STT):
        def __init__(self):
            self.model = WhisperModel(model_id, device="auto", compute_type="int8")

        def transcribe_stream(self, pcm_chunks):
            import numpy as np  # type: ignore
            audio = np.concatenate([np.frombuffer(c, dtype=np.int16) for c in pcm_chunks]
                                   ).astype("float32") / 32768.0
            segments, _ = self.model.transcribe(audio)
            yield {"type": "final", "text": " ".join(s.text for s in segments)}

    return RealSTT()


# Movie-accurate voice: calm British male, Paul Bettany style.
# en_GB-alan-medium from the Piper voices collection (MIT licensed).
DEFAULT_VOICE_ID = "en_GB-alan-medium"


def default_voice_path() -> str:
    """Filesystem path of the pinned Piper voice.

    `make models` downloads it to DATA_DIR/models/<manifest file path>;
    check there first so the bundled app finds it, then fall back to the
    legacy repo-relative dev path.
    """
    import os
    from ..config import DATA_DIR
    bundled = (DATA_DIR / "models" / "piper-voices" / "en" / "en_GB" / "alan"
               / "medium" / f"{DEFAULT_VOICE_ID}.onnx")
    if bundled.exists():
        return str(bundled)
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, "..", "..", "..", "models", "tts",
                        f"{DEFAULT_VOICE_ID}.onnx")


def load_default_tts() -> TTS:
    """The JARVIS voice. Falls back to FakeTTS when the model isn't downloaded."""
    import os
    path = default_voice_path()
    if os.path.exists(path):
        return load_real_tts(path)
    return FakeTTS()


def load_real_tts(voice_onnx: str, sample_rate: int = 22050, chunk_ms: int = 200) -> TTS:
    """Real TTS via Piper (onnx). Streams PCM16 16kHz byte chunks.

    voice_onnx: path to a Piper voice file, e.g. models/tts/en_US-lessac-medium.onnx
    Download voices from https://huggingface.co/rhasspy/piper-voices (hash-pinned
    in models/manifest.json before use).
    """
    from piper import PiperVoice  # type: ignore

    class RealTTS(TTS):
        def __init__(self):
            self.voice = PiperVoice.load(voice_onnx)
            self.chunk_samples = int(16000 * chunk_ms / 1000)

        def speak(self, text: str):
            import numpy as np  # type: ignore
            buf = np.empty(0, dtype=np.int16)
            for chunk in self.voice.synthesize(text):
                pcm = (np.array(chunk.audio_float_array) * 32767).astype(np.int16)
                buf = np.concatenate([buf, pcm])
                while len(buf) >= self.chunk_samples:
                    yield buf[:self.chunk_samples].tobytes()
                    buf = buf[self.chunk_samples:]
            if len(buf):
                yield buf.tobytes()

    return RealTTS()
