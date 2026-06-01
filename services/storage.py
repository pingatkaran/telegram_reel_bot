from __future__ import annotations

import asyncio
import mimetypes
import uuid
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import boto3
import httpx
from supabase import create_client

from config import Settings


class StorageClient:
    async def upload_public(self, file_path: Path) -> str:
        raise NotImplementedError

    async def verify_public_url(self, url: str) -> None:
        async with httpx.AsyncClient(follow_redirects=True, timeout=30) as client:
            response = await client.head(url)
            if response.status_code == 405:
                response = await client.get(url, headers={"Range": "bytes=0-1"})
        content_type = response.headers.get("content-type", "")
        if response.status_code >= 400:
            raise RuntimeError(f"Public storage URL is not accessible: HTTP {response.status_code} for {url}")
        if "video/" not in content_type and "application/octet-stream" not in content_type:
            raise RuntimeError(f"Public storage URL does not look like a video: content-type={content_type!r} for {url}")


class CloudflareR2Storage(StorageClient):
    def __init__(self, settings: Settings) -> None:
        required = {
            "CLOUDFLARE_R2_ACCESS_KEY_ID": settings.cloudflare_r2_access_key_id,
            "CLOUDFLARE_R2_SECRET_ACCESS_KEY": settings.cloudflare_r2_secret_access_key,
            "CLOUDFLARE_R2_BUCKET": settings.cloudflare_r2_bucket,
            "CLOUDFLARE_R2_ENDPOINT": settings.cloudflare_r2_endpoint,
            "CLOUDFLARE_R2_PUBLIC_URL": settings.cloudflare_r2_public_url,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing Cloudflare R2 settings: {', '.join(missing)}")
        self.settings = settings
        self.client = boto3.client(
            "s3",
            endpoint_url=settings.cloudflare_r2_endpoint,
            aws_access_key_id=settings.cloudflare_r2_access_key_id,
            aws_secret_access_key=settings.cloudflare_r2_secret_access_key,
            region_name="auto",
        )

    async def upload_public(self, file_path: Path) -> str:
        key = object_key(file_path)
        content_type = mimetypes.guess_type(file_path.name)[0] or "video/mp4"
        await asyncio.to_thread(
            self.client.upload_file,
            str(file_path),
            self.settings.cloudflare_r2_bucket,
            key,
            ExtraArgs={
                "ContentType": content_type,
                "CacheControl": "public, max-age=31536000",
            },
        )
        public_url = f"{self.settings.cloudflare_r2_public_url.rstrip('/')}/{quote(key)}"
        await self.verify_public_url(public_url)
        return public_url


class SupabaseStorage(StorageClient):
    def __init__(self, settings: Settings) -> None:
        required = {
            "SUPABASE_URL": settings.supabase_url,
            "SUPABASE_SERVICE_ROLE_KEY": settings.supabase_service_role_key,
            "SUPABASE_BUCKET": settings.supabase_bucket,
        }
        missing = [name for name, value in required.items() if not value]
        if missing:
            raise RuntimeError(f"Missing Supabase settings: {', '.join(missing)}")
        self.settings = settings
        self.client = create_client(settings.supabase_url, settings.supabase_service_role_key)

    async def upload_public(self, file_path: Path) -> str:
        key = object_key(file_path)
        content_type = mimetypes.guess_type(file_path.name)[0] or "video/mp4"
        await asyncio.to_thread(self._upload_sync, file_path, key, content_type)
        public_url = self.client.storage.from_(self.settings.supabase_bucket).get_public_url(key)
        public_url = str(public_url)
        await self.verify_public_url(public_url)
        return public_url

    def _upload_sync(self, file_path: Path, key: str, content_type: str) -> None:
        with file_path.open("rb") as file:
            self.client.storage.from_(self.settings.supabase_bucket).upload(
                path=key,
                file=file,
                file_options={
                    "content-type": content_type,
                    "cache-control": "31536000",
                    "upsert": "true",
                },
            )


def create_storage(settings: Settings) -> StorageClient:
    if settings.storage_provider == "supabase":
        return SupabaseStorage(settings)
    return CloudflareR2Storage(settings)


def object_key(file_path: Path) -> str:
    now = datetime.now(timezone.utc)
    suffix = file_path.suffix or ".mp4"
    return f"reels/{now:%Y/%m/%d}/{uuid.uuid4().hex}{suffix}"
