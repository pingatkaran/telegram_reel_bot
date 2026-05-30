from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

from config import Settings


logger = logging.getLogger(__name__)


@dataclass(slots=True)
class InstagramPublishResult:
    media_id: str
    permalink: str | None = None


class InstagramUploader:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.base_url = f"https://graph.facebook.com/{settings.graph_api_version}"

    async def publish_reel(self, video_url: str, caption: str) -> InstagramPublishResult:
        if not self.settings.instagram_access_token or not self.settings.instagram_user_id:
            raise RuntimeError("Missing INSTAGRAM_ACCESS_TOKEN or INSTAGRAM_USER_ID")

        async with httpx.AsyncClient(timeout=60) as client:
            creation_id = await self._create_container(client, video_url, caption)
            await self._wait_until_ready(client, creation_id)
            media_id = await self._publish_container(client, creation_id)
            permalink = await self._get_permalink(client, media_id)
            return InstagramPublishResult(media_id=media_id, permalink=permalink)

    async def _create_container(self, client: httpx.AsyncClient, video_url: str, caption: str) -> str:
        response = await client.post(
            f"{self.base_url}/{self.settings.instagram_user_id}/media",
            data={
                "media_type": "REELS",
                "video_url": video_url,
                "caption": caption,
                "share_to_feed": "true",
                "access_token": self.settings.instagram_access_token,
            },
        )
        payload = self._json_or_raise(response)
        creation_id = payload.get("id")
        if not creation_id:
            raise RuntimeError(f"Instagram did not return a creation id: {payload}")
        return str(creation_id)

    async def _wait_until_ready(self, client: httpx.AsyncClient, creation_id: str) -> None:
        for attempt in range(90):
            response = await client.get(
                f"{self.base_url}/{creation_id}",
                params={
                    "fields": "status_code,status",
                    "access_token": self.settings.instagram_access_token,
                },
            )
            payload = self._json_or_raise(response)
            status_code = str(payload.get("status_code") or "").upper()
            status = payload.get("status")
            if status_code == "FINISHED":
                return
            if status_code in {"ERROR", "EXPIRED"}:
                raise RuntimeError(f"Instagram container failed: {payload}")
            logger.info("Instagram container %s not ready yet: %s", creation_id, status or status_code)
            await asyncio.sleep(10 if attempt > 5 else 4)
        raise RuntimeError("Instagram container did not finish processing in time")

    async def _publish_container(self, client: httpx.AsyncClient, creation_id: str) -> str:
        response = await client.post(
            f"{self.base_url}/{self.settings.instagram_user_id}/media_publish",
            data={
                "creation_id": creation_id,
                "access_token": self.settings.instagram_access_token,
            },
        )
        payload = self._json_or_raise(response)
        media_id = payload.get("id")
        if not media_id:
            raise RuntimeError(f"Instagram did not return media id: {payload}")
        return str(media_id)

    async def _get_permalink(self, client: httpx.AsyncClient, media_id: str) -> str | None:
        response = await client.get(
            f"{self.base_url}/{media_id}",
            params={
                "fields": "permalink",
                "access_token": self.settings.instagram_access_token,
            },
        )
        if response.status_code >= 400:
            logger.warning("Could not fetch Instagram permalink: %s", response.text)
            return None
        return response.json().get("permalink")

    def _json_or_raise(self, response: httpx.Response) -> dict:
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(f"Instagram returned non-JSON response: {response.text[:500]}") from exc
        if response.status_code >= 400 or "error" in payload:
            raise RuntimeError(f"Instagram API error ({response.status_code}): {payload}")
        return payload
