from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config import Settings


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class TranscriptSegment:
    start: float
    end: float
    text: str


class Transcriber:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._model = None

    def _load_model(self):
        if self._model is None:
            from faster_whisper import WhisperModel

            self._model = WhisperModel(
                self.settings.whisper_model,
                device="cpu",
                compute_type="int8",
            )
        return self._model

    async def transcribe(self, audio_path: Path) -> list[TranscriptSegment]:
        if not audio_path.exists() or audio_path.stat().st_size == 0:
            return []
        try:
            return await asyncio.to_thread(self._transcribe_sync, audio_path)
        except Exception:
            logger.exception("Whisper transcription failed; continuing without transcript")
            return []

    def _transcribe_sync(self, audio_path: Path) -> list[TranscriptSegment]:
        model = self._load_model()
        segments, _info = model.transcribe(
            str(audio_path),
            beam_size=3,
            vad_filter=True,
            word_timestamps=False,
        )
        output: list[TranscriptSegment] = []
        for segment in segments:
            text = " ".join(segment.text.strip().split())
            if text:
                output.append(
                    TranscriptSegment(
                        start=max(0.0, float(segment.start)),
                        end=max(float(segment.end), float(segment.start) + 0.5),
                        text=text,
                    )
                )
        return output


def transcript_text(segments: list[TranscriptSegment], limit: int = 1800) -> str:
    text = " ".join(segment.text for segment in segments)
    return text[:limit].strip()
