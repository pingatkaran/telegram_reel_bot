from __future__ import annotations

import asyncio
import mimetypes
import re
from pathlib import Path
from urllib.parse import urlparse

import httpx
from yt_dlp import YoutubeDL

from config import DATA_DIR, DOWNLOADS_DIR, Settings


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
        base_options = {
            "outtmpl": output_template,
            "merge_output_format": "mp4",
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 60,
            "source_address": "0.0.0.0",
            "retries": 10,
            "extractor_retries": 5,
            "fragment_retries": 10,
            "file_access_retries": 5,
            "sleep_interval_requests": 1,
            "http_headers": {
                "User-Agent": (
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/125.0.0.0 Safari/537.36"
                ),
                "Accept-Language": "en-US,en;q=0.9",
            },
        }
        cookie_file = self._youtube_cookie_file()

        format_attempts = [
            ("bestvideo*[height<=1080]+bestaudio/best[height<=1080]/best", ["web", "android"]),
            ("best[height<=1080]/best", ["web"]),
            ("bestvideo*+bestaudio/best", ["android", "web"]),
            ("worst[ext=mp4]/worst/best", ["web"]),
        ]

        last_error: Exception | None = None
        for format_selector, player_clients in format_attempts:
            options = {
                **base_options,
                "format": format_selector,
                "extractor_args": {"youtube": {"player_client": player_clients}},
            }
            if cookie_file:
                options["cookiefile"] = str(cookie_file)

            try:
                with YoutubeDL(options) as ydl:
                    info = ydl.extract_info(url, download=True)
                    downloaded = self._find_youtube_download(ydl, info, job_id)
                    if downloaded.stat().st_size > self.settings.max_download_bytes:
                        raise RuntimeError(f"Video exceeds MAX_DOWNLOAD_MB={self.settings.max_download_mb}")
                    return downloaded
            except Exception as exc:
                last_error = exc
                if "Requested format is not available" in str(exc):
                    continue
                self._raise_youtube_error(exc)

        if last_error:
            self._raise_youtube_error(last_error)
        raise RuntimeError("yt-dlp finished but no downloaded video file was found")

    def _find_youtube_download(self, ydl: YoutubeDL, info: dict, job_id: int) -> Path:
        requested = info.get("requested_downloads") or []
        for item in requested:
            filepath = item.get("filepath")
            if filepath and Path(filepath).exists():
                return Path(filepath)

        prepared = Path(ydl.prepare_filename(info))
        if prepared.exists():
            return prepared

        candidates = sorted(
            DOWNLOADS_DIR.glob(f"{job_id}_*"),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        for candidate in candidates:
            if candidate.suffix.lower() in VIDEO_EXTENSIONS and candidate.exists():
                return candidate
        raise RuntimeError("yt-dlp finished but no downloaded video file was found")

    def _raise_youtube_error(self, exc: Exception) -> None:
        message = str(exc)
        if "Sign in to confirm" in message or "not a bot" in message or "--cookies" in message:
            raise RuntimeError(
                "YouTube blocked this cloud server with a bot-verification challenge. "
                "Try uploading the video directly to Telegram, send a direct MP4 URL, "
                "or configure YOUTUBE_COOKIES_FILE/YOUTUBE_COOKIES_CONTENT."
            ) from exc
        if "UNEXPECTED_EOF_WHILE_READING" in message or "SSLError" in message:
            raise RuntimeError(
                "YouTube connection failed from this cloud host while downloading video metadata. "
                "Cookies are present, but YouTube/Hugging Face had a TLS/network failure. "
                "Retry once; if it repeats, upload the video directly to Telegram or send a direct MP4 URL."
            ) from exc
        if "Requested format is not available" in message:
            raise RuntimeError(
                "YouTube was accessible, but no downloadable format matched any fallback selector. "
                "This is common for restricted, livestream, members-only, Shorts-only, or cloud-blocked videos. "
                "Try another YouTube URL, upload the video directly to Telegram, or send a direct MP4 URL."
            ) from exc
        raise exc

    def _youtube_cookie_file(self) -> Path | None:
        if self.settings.youtube_cookies_file:
            path = Path(self.settings.youtube_cookies_file)
            if path.exists():
                return path
            raise RuntimeError(f"YOUTUBE_COOKIES_FILE does not exist: {path}")

        if self.settings.youtube_cookies_content:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            path = DATA_DIR / "youtube_cookies.txt"
            content = self.settings.youtube_cookies_content.replace("\\n", "\n")
            path.write_text(content, encoding="utf-8")
            path.chmod(0o600)
            return path
        return None

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
