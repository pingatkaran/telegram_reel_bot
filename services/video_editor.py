from __future__ import annotations

import asyncio
import json
import logging
import math
import textwrap
from dataclasses import dataclass
from pathlib import Path

from services.scene_selector import SelectedSegment
from services.transcriber import TranscriptSegment


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class VideoInfo:
    duration: float
    width: int
    height: int
    has_audio: bool


class VideoEditor:
    subtitle_style = (
        "FontName=DejaVu Sans,"
        "FontSize=18,"
        "PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&HAA000000,"
        "BorderStyle=1,"
        "Outline=2,"
        "Shadow=1,"
        "Alignment=2,"
        "MarginV=170"
    )

    async def probe(self, path: Path) -> VideoInfo:
        cmd = [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration:stream=codec_type,width,height",
            "-of",
            "json",
            str(path),
        ]
        stdout, _stderr = await self._run_capture(cmd)
        payload = json.loads(stdout)
        duration = float(payload.get("format", {}).get("duration") or 0.0)
        width = 0
        height = 0
        has_audio = False
        for stream in payload.get("streams", []):
            if stream.get("codec_type") == "video" and not width:
                width = int(stream.get("width") or 0)
                height = int(stream.get("height") or 0)
            if stream.get("codec_type") == "audio":
                has_audio = True
        return VideoInfo(duration=max(duration, 1.0), width=width, height=height, has_audio=has_audio)

    async def extract_audio(self, input_path: Path, output_path: Path) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-i",
            str(input_path),
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(output_path),
        ]
        await self._run(cmd)
        return output_path

    async def create_prompt_video(self, prompt: str, output_path: Path, duration: int) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        cmd = [
            "ffmpeg",
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=c=0x111827:s=1080x1920:d={duration}",
            "-f",
            "lavfi",
            "-i",
            "anullsrc=channel_layout=stereo:sample_rate=44100",
            "-map",
            "0:v",
            "-map",
            "1:a",
            "-t",
            str(duration),
            "-r",
            "30",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-movflags",
            "+faststart",
            str(output_path),
        ]
        await self._run(cmd)
        return output_path

    async def create_vertical_reel(
        self,
        input_path: Path,
        selected: SelectedSegment,
        transcript: list[TranscriptSegment],
        output_path: Path,
        fallback_subtitle: str | None = None,
    ) -> Path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        info = await self.probe(input_path)
        duration = max(1.0, selected.duration)
        subtitle_path = output_path.with_suffix(".srt")

        if transcript:
            write_srt(transcript, subtitle_path, offset=selected.start, duration=duration)
        else:
            text = fallback_subtitle or "Highlights from this video"
            write_srt(prompt_to_segments(text, min(int(duration), 12)), subtitle_path, 0.0, duration)

        vf = (
            "[0:v]scale=1080:1920:force_original_aspect_ratio=increase,"
            "crop=1080:1920,boxblur=luma_radius=24:luma_power=1[bg];"
            "[0:v]scale=1080:1920:force_original_aspect_ratio=decrease[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,"
            f"{subtitles_filter(subtitle_path, self.subtitle_style)},"
            "format=yuv420p[v]"
        )

        cmd = [
            "ffmpeg",
            "-y",
            "-ss",
            f"{selected.start:.3f}",
            "-t",
            f"{duration:.3f}",
            "-i",
            str(input_path),
        ]
        if not info.has_audio:
            cmd.extend(["-f", "lavfi", "-t", f"{duration:.3f}", "-i", "anullsrc=channel_layout=stereo:sample_rate=44100"])

        cmd.extend(
            [
                "-filter_complex",
                vf,
                "-map",
                "[v]",
                "-map",
                "0:a?" if info.has_audio else "1:a",
            ]
        )
        if info.has_audio:
            cmd.extend(["-af", "loudnorm=I=-16:TP=-1.5:LRA=11"])
        cmd.extend(
            [
                "-r",
                "30",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-profile:v",
                "high",
                "-level",
                "4.1",
                "-b:v",
                "4500k",
                "-maxrate",
                "6000k",
                "-bufsize",
                "12000k",
                "-c:a",
                "aac",
                "-b:a",
                "128k",
                "-movflags",
                "+faststart",
                "-shortest",
                str(output_path),
            ]
        )
        await self._run(cmd)
        return output_path

    async def _run(self, cmd: list[str]) -> None:
        _stdout, stderr = await self._run_capture(cmd)
        if stderr:
            logger.debug("ffmpeg output: %s", stderr[-1200:])

    async def _run_capture(self, cmd: list[str]) -> tuple[str, str]:
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        if process.returncode != 0:
            raise RuntimeError(f"Command failed ({process.returncode}): {' '.join(cmd)}\n{stderr[-4000:]}")
        return stdout, stderr


def prompt_to_segments(text: str, duration: float) -> list[TranscriptSegment]:
    cleaned = " ".join(text.strip().split())
    if not cleaned:
        cleaned = "New Reel"

    wrapped = textwrap.wrap(cleaned, width=42) or [cleaned]
    chunk_count = min(4, max(1, math.ceil(len(wrapped) / 2)))
    chunks: list[str] = []
    lines_per_chunk = max(1, math.ceil(len(wrapped) / chunk_count))
    for index in range(0, len(wrapped), lines_per_chunk):
        chunks.append("\n".join(wrapped[index : index + lines_per_chunk]))

    segment_duration = max(2.0, duration / len(chunks))
    segments: list[TranscriptSegment] = []
    cursor = 0.0
    for chunk in chunks:
        start = cursor
        end = min(duration, cursor + segment_duration)
        segments.append(TranscriptSegment(start=start, end=max(end, start + 1.5), text=chunk))
        cursor = end
    return segments


def write_srt(
    segments: list[TranscriptSegment],
    path: Path,
    offset: float,
    duration: float,
) -> None:
    lines: list[str] = []
    counter = 1
    for segment in segments:
        start = max(0.0, segment.start - offset)
        end = min(duration, segment.end - offset)
        if end <= 0 or start >= duration:
            continue
        if end <= start:
            end = min(duration, start + 1.5)
        text = _sanitize_subtitle_text(segment.text)
        if not text:
            continue
        lines.extend(
            [
                str(counter),
                f"{format_srt_time(start)} --> {format_srt_time(end)}",
                text,
                "",
            ]
        )
        counter += 1

    if not lines:
        lines = [
            "1",
            f"{format_srt_time(0)} --> {format_srt_time(min(duration, 3.0))}",
            "New Reel",
            "",
        ]
    path.write_text("\n".join(lines), encoding="utf-8")


def format_srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    millis = int(round((seconds - int(seconds)) * 1000))
    whole = int(seconds)
    hours = whole // 3600
    minutes = (whole % 3600) // 60
    secs = whole % 60
    return f"{hours:02}:{minutes:02}:{secs:02},{millis:03}"


def _sanitize_subtitle_text(text: str) -> str:
    cleaned = " ".join(text.replace("\r", " ").split())
    return "\n".join(textwrap.wrap(cleaned, width=34)[:3])


def subtitles_filter(path: Path, style: str) -> str:
    safe_path = path.as_posix().replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
    safe_style = style.replace("'", "\\'")
    return f"subtitles=filename='{safe_path}':force_style='{safe_style}'"
