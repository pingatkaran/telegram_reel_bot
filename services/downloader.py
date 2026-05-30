from __future__ import annotations

import asyncio
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from yt_dlp import YoutubeDL

from config import DOWNLOADS_DIR, Settings


VIDEO_EXTENSIONS = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "www.youtu.be"}
URL_RE = re.compile(r"https?://\S+", re.IGNORECASE)


class Downloader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def download_url(self, url: str, job_id: int) -> Path:
        if is_youtube_url(url):
            return await asyncio.to_thread(self._download_youtube, url, job_id)
        return await self._download_direct_video(url, job_id)

    def _download_youtube(self, url: str, job_id: int) -> Path:
        DOWNLOADS_DIR.mkdir(parents=True, exist_ok=True)
        output_template = str(DOWNLOADS_DIR / f"{job_id}_%(id)s.%(ext)s")
        options = {
            "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 30,
            "retries": 3,
            "max_filesize": self.settings.max_download_bytes,
        }
        with YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=True)
            requested = info.get("requested_downloads") or []
            for item in requested:
                filepath = item.get("filepath")
                if filepath and Path(filepath).exists():
                    return Path(filepath)
            prepared = Path(ydl.prepare_filename(info))
            if prepared.exists():
                return prepared
            candidates = sorted(DOWNLOADS_DIR.glob(f"{job_id}_*"), key=lambda path: path.stat().st_mtime, reverse=True)
            for candidate in candidates:
                if candidate.suffix.lower() in VIDEO_EXTENSIONS and candidate.exists():
                    return candidate
        raise RuntimeError("yt-dlp finished but no downloaded video file was found")

    async def _download_direct_video(self, url: str, job_id: int) -> Path:
        suffix = Path(urlparse(url).path).suffix.lower()
        if suffix not in VIDEO_EXTENSIONS:
            suffix = ".mp4"

        output_path = DOWNLOADS_DIR / f"{job_id}_direct{suffix}"
        bytes_seen = 0
        async with httpx.AsyncClient(follow_redirects=True, timeout=60) as client:
            async with client.stream("GET", url) as response:
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].strip()
                if content_type and not (content_type.startswith("video/") or content_type == "application/octet-stream"):
                    guessed = mimetypes.guess_extension(content_type) or ""
                    if guessed.lower() not in VIDEO_EXTENSIONS:
                        raise RuntimeError(f"URL did not return a video content type: {content_type}")
                with output_path.open("wb") as file:
                    async for chunk in response.aiter_bytes():
                        if not chunk:
                            continue
                        bytes_seen += len(chunk)
                        if bytes_seen > self.settings.max_download_bytes:
                            raise RuntimeError(f"Video exceeds MAX_DOWNLOAD_MB={self.settings.max_download_mb}")
                        file.write(chunk)
        if output_path.stat().st_size == 0:
            raise RuntimeError("Downloaded video is empty")
        return output_path


def first_url(text: str | None) -> str | None:
    if not text:
        return None
    match = URL_RE.search(text)
    return match.group(0).rstrip(").,]") if match else None


def is_youtube_url(url: str) -> bool:
    host = urlparse(url).netloc.lower()
    return host in YOUTUBE_HOSTS or host.endswith(".youtube.com")
