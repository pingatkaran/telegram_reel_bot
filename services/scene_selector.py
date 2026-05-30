from __future__ import annotations

from dataclasses import dataclass

from services.transcriber import TranscriptSegment


HOOK_WORDS = {
    "secret",
    "mistake",
    "best",
    "worst",
    "why",
    "how",
    "never",
    "always",
    "first",
    "finally",
    "watch",
    "money",
    "growth",
    "learn",
    "simple",
    "quick",
    "truth",
    "story",
}


@dataclass(slots=True)
class SelectedSegment:
    start: float
    end: float
    reason: str

    @property
    def duration(self) -> float:
        return max(0.0, self.end - self.start)


class SceneSelector:
    def __init__(self, min_seconds: int = 15, max_seconds: int = 45) -> None:
        self.min_seconds = min_seconds
        self.max_seconds = max_seconds

    def pick(
        self,
        transcript: list[TranscriptSegment],
        video_duration: float,
    ) -> SelectedSegment:
        video_duration = max(1.0, video_duration)
        if video_duration <= self.max_seconds:
            return SelectedSegment(0.0, video_duration, "source is already reel length")

        if not transcript:
            duration = min(self.max_seconds, max(self.min_seconds, video_duration * 0.45))
            start = max(0.0, min(video_duration - duration, video_duration * 0.25))
            return SelectedSegment(start, start + duration, "fallback middle highlight")

        best: tuple[float, float, float, str] | None = None
        for index, segment in enumerate(transcript):
            window_start = max(0.0, segment.start - 0.75)
            window_text: list[str] = []
            window_end = segment.end

            for next_segment in transcript[index:]:
                if next_segment.end - window_start > self.max_seconds:
                    break
                window_text.append(next_segment.text)
                window_end = min(video_duration, next_segment.end + 0.5)
                duration = window_end - window_start
                if duration < self.min_seconds:
                    continue
                text = " ".join(window_text)
                score = self._score(text, duration)
                if best is None or score > best[0]:
                    best = (score, window_start, window_end, text[:80])

        if best is None:
            first = transcript[0]
            duration = min(self.max_seconds, max(self.min_seconds, video_duration * 0.35))
            start = min(first.start, video_duration - duration)
            return SelectedSegment(max(0.0, start), max(0.0, start) + duration, "first transcript window")

        _score, start, end, preview = best
        return SelectedSegment(start, end, f"high-signal transcript window: {preview}")

    def _score(self, text: str, duration: float) -> float:
        words = [word.strip(".,!?;:()[]{}\"'").lower() for word in text.split()]
        if not words:
            return 0.0

        keyword_bonus = sum(2.5 for word in words if word in HOOK_WORDS)
        number_bonus = sum(1.5 for word in words if any(char.isdigit() for char in word))
        question_bonus = 3.0 if "?" in text else 0.0
        exclamation_bonus = 1.0 if "!" in text else 0.0
        density = min(len(words) / max(duration, 1.0), 4.0)
        duration_fit = 4.0 - abs(28.0 - duration) / 10.0
        return density + keyword_bonus + number_bonus + question_bonus + exclamation_bonus + duration_fit
