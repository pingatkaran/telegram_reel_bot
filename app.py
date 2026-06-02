from __future__ import annotations

import asyncio
import logging
import re
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Request

from config import DOWNLOADS_DIR, OUTPUTS_DIR, Settings, ensure_directories, get_settings
from services.caption_generator import CaptionGenerator
from services.ai_visual_generator import AIPromptVisualGenerator
from services.database import Database
from services.downloader import Downloader, first_url
from services.instagram_uploader import InstagramUploader
from services.scene_selector import SceneSelector
from services.storage import create_storage
from services.telegram_service import TelegramService
from services.transcriber import Transcriber, transcript_text
from services.video_editor import VideoEditor, prompt_to_segments


logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)
logger = logging.getLogger("telegram_reel_bot")

settings = get_settings()
database = Database(settings.database_url)
telegram = TelegramService(settings)
downloader = Downloader(settings)
video_editor = VideoEditor()
transcriber = Transcriber(settings)
scene_selector = SceneSelector(settings.min_reel_seconds, settings.max_reel_seconds)
caption_generator = CaptionGenerator(settings)
visual_generator = AIPromptVisualGenerator(settings)
job_semaphore = asyncio.Semaphore(settings.max_concurrent_jobs)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    ensure_directories()
    database.init()
    if settings.telegram_bot_token and settings.webhook_url:
        asyncio.create_task(configure_telegram_webhook())
    elif not settings.public_base_url:
        logger.warning("PUBLIC_BASE_URL is not set; webhook will not be registered")
    yield


app = FastAPI(title="Telegram to Instagram Reel Bot", version="1.0.0", lifespan=lifespan)


async def configure_telegram_webhook() -> None:
    if not settings.webhook_url:
        return

    delays = [0, 5, 15, 30, 60, 120]
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            await telegram.set_webhook(settings.webhook_url, settings.telegram_webhook_secret)
            logger.info("Telegram webhook set to %s", settings.webhook_url)
            return
        except Exception:
            logger.exception(
                "Could not set Telegram webhook for %s on attempt %s/%s. Retrying if attempts remain.",
                settings.webhook_url,
                attempt,
                len(delays),
            )
    logger.error("Telegram webhook could not be set after retries: %s", settings.webhook_url)


@app.get("/")
@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.head("/")
@app.head("/health")
async def health_head() -> None:
    return None


@app.post(settings.webhook_path)
async def telegram_webhook(request: Request) -> dict[str, object]:
    if settings.telegram_webhook_secret:
        received = request.headers.get("x-telegram-bot-api-secret-token")
        if received != settings.telegram_webhook_secret:
            raise HTTPException(status_code=403, detail="Invalid Telegram webhook secret")

    update = await request.json()
    update_id = int(update.get("update_id", 0))
    parsed = parse_telegram_update(update)
    if parsed is None:
        return {"ok": True}

    chat_id, payload = parsed
    if payload.command == "start":
        return telegram_reply(chat_id, welcome_message())

    if payload.input_type == "unsupported":
        return telegram_reply(chat_id, "Send a prompt, YouTube URL, direct video URL, or upload a video file.")

    job_id = database.create_job(update_id, chat_id, payload.input_type, payload.source_label)
    if job_id is None:
        return {"ok": True}

    asyncio.create_task(process_job(job_id, chat_id, payload, send_queued=False))
    return telegram_reply(chat_id, f"Got it. Job #{job_id} is queued and I will publish the Reel when it is ready.")


def telegram_reply(chat_id: int | str, text: str) -> dict[str, object]:
    return {
        "method": "sendMessage",
        "chat_id": chat_id,
        "text": text,
        "disable_web_page_preview": True,
    }


async def process_job(
    job_id: int,
    chat_id: int | str,
    payload: "InputPayload",
    send_queued: bool = True,
) -> None:
    if send_queued:
        await safe_send_message(
            chat_id,
            f"Got it. Job #{job_id} is queued and I will publish the Reel when it is ready.",
            f"queued message for job {job_id}",
        )

    public_url: str | None = None
    async with job_semaphore:
        try:
            database.update_job(job_id, status="processing")
            source_path, source_text, source_type, transcript_segments = await prepare_source(job_id, payload)

            info = await video_editor.probe(source_path)
            if (
                settings.enable_transcription
                and not settings.low_memory_mode
                and not transcript_segments
                and info.has_audio
            ):
                audio_path = OUTPUTS_DIR / f"{job_id}_audio.wav"
                try:
                    await video_editor.extract_audio(source_path, audio_path)
                    transcript_segments = await transcriber.transcribe(audio_path)
                except Exception:
                    logger.exception("Audio extraction/transcription failed for job %s", job_id)

            selected = scene_selector.pick(transcript_segments, info.duration)
            transcript_for_caption = transcript_text(transcript_segments)
            add_subtitles = source_type != "prompt"
            fallback_subtitle = None if not add_subtitles else source_text or transcript_for_caption or "Highlights from this video"

            final_path = OUTPUTS_DIR / f"{job_id}_reel.mp4"
            await video_editor.create_vertical_reel(
                source_path,
                selected,
                transcript_segments,
                final_path,
                fallback_subtitle=fallback_subtitle,
                low_memory=settings.low_memory_mode,
                add_subtitles=add_subtitles,
            )

            caption_result = await caption_generator.generate(source_text, transcript_for_caption, source_type)
            storage = create_storage(settings)
            public_url = await storage.upload_public(final_path)
            database.update_job(job_id, public_url=public_url)
            instagram = InstagramUploader(settings)
            publish_result = await instagram.publish_reel(public_url, caption_result.full_text)

            database.update_job(
                job_id,
                status="completed",
                public_url=public_url,
                instagram_media_id=publish_result.media_id,
                instagram_permalink=publish_result.permalink,
                caption=caption_result.full_text,
            )

            message = [
                f"Done. Job #{job_id} published as an Instagram Reel.",
                f"Media ID: {publish_result.media_id}",
                f"Public video URL: {public_url}",
            ]
            if publish_result.permalink:
                message.append(f"Instagram link: {publish_result.permalink}")
            await safe_send_message(chat_id, "\n".join(message), f"success message for job {job_id}")
        except Exception as exc:
            logger.exception("Job %s failed", job_id)
            database.update_job(job_id, status="failed", error=str(exc))
            message = [
                f"Job #{job_id} failed:",
                short_error(exc),
            ]
            if public_url:
                message.append(f"Public video URL created before failure: {public_url}")
            message.append("Check your storage, Instagram token, and whether the source video is publicly downloadable.")
            await safe_send_message(chat_id, "\n\n".join(message), f"failure message for job {job_id}")


async def safe_send_message(chat_id: int | str, text: str, label: str) -> None:
    delays = [0, 5, 15, 30, 60, 120]
    for attempt, delay in enumerate(delays, start=1):
        if delay:
            await asyncio.sleep(delay)
        try:
            await telegram.send_message(chat_id, text)
            return
        except Exception:
            logger.exception(
                "Could not send Telegram %s on attempt %s/%s",
                label,
                attempt,
                len(delays),
            )


async def prepare_source(
    job_id: int,
    payload: "InputPayload",
) -> tuple[Path, str | None, str, list]:
    if payload.input_type in {"youtube_url", "direct_video_url"} and payload.url:
        source_path = await downloader.download_url(payload.url, job_id)
        return source_path, payload.text, payload.input_type, []

    if payload.input_type == "telegram_file" and payload.file_id:
        file_info = await telegram.get_file(payload.file_id)
        file_path = file_info.get("file_path")
        if not file_path:
            raise RuntimeError("Telegram did not return a downloadable file path")
        suffix = Path(payload.file_name or file_path).suffix or ".mp4"
        destination = DOWNLOADS_DIR / f"{job_id}_telegram{suffix}"
        source_path = await telegram.download_file(file_path, destination)
        return source_path, payload.text, payload.input_type, []

    if payload.input_type == "prompt" and payload.text:
        prompt_video_path = OUTPUTS_DIR / f"{job_id}_prompt.mp4"
        duration = max(settings.min_reel_seconds, min(settings.prompt_reel_seconds, settings.max_reel_seconds))
        reference_image_path = await download_reference_image(job_id, payload)
        if settings.enable_ai_prompt_visuals:
            try:
                image_paths = await visual_generator.generate_prompt_images(
                    payload.text,
                    job_id,
                    settings.prompt_visual_count,
                    reference_image=reference_image_path,
                )
                if image_paths:
                    await video_editor.create_image_reel(
                        image_paths,
                        prompt_video_path,
                        duration,
                        subtitle_text=None,
                        low_memory=settings.low_memory_mode,
                    )
                    return prompt_video_path, payload.text, "prompt", prompt_to_segments(payload.text, duration)
                logger.warning("AI prompt visuals did not return images for job %s; using text fallback", job_id)
            except Exception:
                logger.exception("AI prompt visuals failed for job %s; using text fallback", job_id)

        await video_editor.create_prompt_video(payload.text, prompt_video_path, duration)
        return prompt_video_path, payload.text, "prompt", prompt_to_segments(payload.text, duration)

    raise RuntimeError("Unsupported input")


async def download_reference_image(job_id: int, payload: "InputPayload") -> Path | None:
    if not payload.reference_file_id:
        return None
    file_info = await telegram.get_file(payload.reference_file_id)
    file_path = file_info.get("file_path")
    if not file_path:
        raise RuntimeError("Telegram did not return a downloadable image path")
    suffix = Path(payload.reference_file_name or file_path).suffix or ".jpg"
    destination = DOWNLOADS_DIR / f"{job_id}_reference{suffix}"
    return await telegram.download_file(file_path, destination)


@dataclass(slots=True)
class InputPayload:
    input_type: str
    source_label: str | None = None
    text: str | None = None
    url: str | None = None
    file_id: str | None = None
    file_name: str | None = None
    reference_file_id: str | None = None
    reference_file_name: str | None = None
    command: str | None = None


def parse_telegram_update(update: dict[str, Any]) -> tuple[int, InputPayload] | None:
    message = update.get("message") or update.get("edited_message")
    if not message:
        return None
    chat = message.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None

    text = message.get("text") or message.get("caption") or ""
    if text.strip().startswith("/start"):
        return int(chat_id), InputPayload(input_type="command", command="start")

    photos = message.get("photo") or []
    if photos:
        largest_photo = photos[-1]
        prompt = text.strip() or "Create a cinematic Instagram Reel based on this image."
        return int(chat_id), InputPayload(
            input_type="prompt",
            source_label=prompt[:120],
            text=prompt,
            reference_file_id=largest_photo.get("file_id"),
            reference_file_name="telegram-reference.jpg",
        )

    video = message.get("video")
    if video and video.get("file_id"):
        return int(chat_id), InputPayload(
            input_type="telegram_file",
            source_label="telegram video upload",
            text=text.strip() or None,
            file_id=video["file_id"],
            file_name=video.get("file_name") or "telegram-video.mp4",
        )

    document = message.get("document")
    if document and document.get("file_id"):
        mime_type = str(document.get("mime_type") or "")
        file_name = str(document.get("file_name") or "telegram-document.mp4")
        if mime_type.startswith("video/") or file_name.lower().endswith((".mp4", ".mov", ".m4v", ".webm", ".mkv")):
            return int(chat_id), InputPayload(
                input_type="telegram_file",
                source_label=f"telegram document {file_name}",
                text=text.strip() or None,
                file_id=document["file_id"],
                file_name=file_name,
            )

    url = first_url(text)
    if url:
        input_type = "youtube_url" if re.search(r"(youtube\.com|youtu\.be)", url, re.I) else "direct_video_url"
        return int(chat_id), InputPayload(
            input_type=input_type,
            source_label=url,
            text=text.replace(url, "").strip() or None,
            url=url,
        )

    if text.strip():
        return int(chat_id), InputPayload(
            input_type="prompt",
            source_label=text.strip()[:120],
            text=text.strip(),
        )

    return int(chat_id), InputPayload(input_type="unsupported")


def welcome_message() -> str:
    return (
        "Send me one of these:\n"
        "- a prompt\n"
        "- a photo with a prompt caption\n"
        "- a YouTube URL\n"
        "- a direct video URL\n"
        "- an uploaded video\n\n"
        "I will make a vertical Reel, add subtitles, upload the MP4 to public cloud storage, and publish it with the Instagram Graph API."
    )


def short_error(exc: Exception) -> str:
    text = " ".join(str(exc).split())
    return text[:900] if text else exc.__class__.__name__
