from __future__ import annotations

import asyncio
from pathlib import Path

import httpx

from config import Settings


class TelegramService:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    @property
    def api_base(self) -> str:
        if not self.settings.telegram_bot_token:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
        return f"https://api.telegram.org/bot{self.settings.telegram_bot_token}"

    async def set_webhook(self, url: str, secret_token: str | None = None) -> dict:
        data: dict[str, object] = {
            "url": url,
            "drop_pending_updates": False,
            "allowed_updates": ["message", "edited_message"],
        }
        if secret_token:
            data["secret_token"] = secret_token
        return await self._post("setWebhook", json=data)

    async def send_message(self, chat_id: int | str, text: str) -> None:
        chunks = [text[index : index + 3900] for index in range(0, len(text), 3900)] or [text]
        for chunk in chunks:
            await self._post(
                "sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": chunk,
                    "disable_web_page_preview": True,
                },
            )

    async def get_file(self, file_id: str) -> dict:
        return await self._post("getFile", json={"file_id": file_id})

    async def download_file(self, file_path: str, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        if not self.settings.telegram_bot_token:
            raise RuntimeError("Missing TELEGRAM_BOT_TOKEN")
        url = f"https://api.telegram.org/file/bot{self.settings.telegram_bot_token}/{file_path}"
        last_error: Exception | None = None
        for attempt in range(1, 5):
            try:
                if destination.exists():
                    destination.unlink()
                async with httpx.AsyncClient(timeout=180) as client:
                    async with client.stream("GET", url) as response:
                        response.raise_for_status()
                        with destination.open("wb") as file:
                            async for chunk in response.aiter_bytes():
                                if chunk:
                                    file.write(chunk)
                if destination.stat().st_size == 0:
                    raise RuntimeError("Telegram downloaded file is empty")
                return destination
            except (httpx.TimeoutException, httpx.NetworkError, httpx.HTTPStatusError, RuntimeError) as exc:
                last_error = exc
                if attempt == 4:
                    break
                await asyncio.sleep(5 * attempt)
        raise RuntimeError(
            "Could not download the Telegram-uploaded video after retries. "
            "Hugging Face is having outbound network trouble reaching Telegram. "
            "Retry once, or send a direct public MP4 URL."
        ) from last_error

    async def _post(self, method: str, **kwargs) -> dict:
        last_error: Exception | None = None
        for attempt in range(1, 4):
            try:
                async with httpx.AsyncClient(timeout=90) as client:
                    response = await client.post(f"{self.api_base}/{method}", **kwargs)
                payload = response.json()
                if response.status_code >= 400 or not payload.get("ok"):
                    raise RuntimeError(f"Telegram API error for {method}: {payload}")
                return payload.get("result") if isinstance(payload.get("result"), dict) else payload
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                last_error = exc
                if attempt == 3:
                    break
                await asyncio.sleep(3 * attempt)
        raise RuntimeError(f"Telegram API network error for {method}") from last_error
