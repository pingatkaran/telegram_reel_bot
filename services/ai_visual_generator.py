from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from config import OUTPUTS_DIR, Settings


class AIPromptVisualGenerator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate_prompt_images(
        self,
        prompt: str,
        job_id: int,
        count: int,
        reference_image: Path | None = None,
    ) -> list[Path]:
        if not self.settings.enable_ai_prompt_visuals:
            return []
        if self._use_pollinations():
            return await self._generate_with_pollinations(prompt, job_id, count)
        if not self.settings.gemini_api_key:
            return []
        return await asyncio.to_thread(self._generate_with_gemini, prompt, job_id, count, reference_image)

    def _use_pollinations(self) -> bool:
        if self.settings.image_provider == "pollinations":
            return True
        return self.settings.image_provider == "auto" and bool(self.settings.pollinations_api_key)

    async def _generate_with_pollinations(self, prompt: str, job_id: int, count: int) -> list[Path]:
        output_dir = OUTPUTS_DIR / f"{job_id}_pollinations"
        output_dir.mkdir(parents=True, exist_ok=True)

        results: list[Path] = []
        target_count = max(1, min(count, 5))
        async with httpx.AsyncClient(timeout=180, follow_redirects=True) as client:
            for index in range(target_count):
                scene_prompt = build_pollinations_prompt(prompt, index, target_count)
                image_url = self._pollinations_image_url(scene_prompt, seed=job_id * 100 + index)
                response = await client.get(image_url)
                response.raise_for_status()
                content_type = response.headers.get("content-type", "").split(";")[0].strip().lower()
                if content_type and not content_type.startswith("image/"):
                    raise RuntimeError(f"Pollinations did not return an image: {content_type}")
                image_path = output_dir / f"scene_{index + 1:02}.png"
                image_path.write_bytes(response.content)
                if image_path.stat().st_size > 0:
                    results.append(image_path)
        return results

    def _pollinations_image_url(self, prompt: str, seed: int) -> str:
        base_url = self.settings.pollinations_base_url.rstrip("/")
        params = {
            "model": self.settings.pollinations_model,
            "width": "1080",
            "height": "1920",
            "seed": str(seed),
            "enhance": "true",
            "nologo": "true",
            "private": "true",
        }
        if self.settings.pollinations_api_key:
            params["key"] = self.settings.pollinations_api_key
        query = "&".join(f"{key}={quote(value, safe='')}" for key, value in params.items())
        return f"{base_url}/image/{quote(prompt, safe='')}?{query}"

    def _generate_with_gemini(
        self,
        prompt: str,
        job_id: int,
        count: int,
        reference_image: Path | None,
    ) -> list[Path]:
        from google import genai
        from google.genai import types
        from PIL import Image

        client = genai.Client(api_key=self.settings.gemini_api_key)
        output_dir = OUTPUTS_DIR / f"{job_id}_nano_banana"
        output_dir.mkdir(parents=True, exist_ok=True)

        if self.settings.gemini_image_model.startswith("imagen-"):
            return self._generate_with_imagen(client, types, prompt, output_dir, target_count=count)

        image_input = None
        if reference_image:
            with Image.open(reference_image) as image:
                image.load()
                image_input = image.copy()

        results: list[Path] = []
        target_count = max(1, min(count, 5))
        for index in range(target_count):
            scene_prompt = build_scene_prompt(prompt, index, target_count, reference_image is not None)
            contents: list[Any] = [scene_prompt]
            if image_input is not None:
                contents.append(image_input)

            response = self._generate_content(client, types, contents)
            image_bytes = first_image_bytes(response)
            if not image_bytes:
                continue
            image_path = output_dir / f"scene_{index + 1:02}.png"
            image_path.write_bytes(image_bytes)
            results.append(image_path)

        return results

    def _generate_with_imagen(
        self,
        client: Any,
        types: Any,
        prompt: str,
        output_dir: Path,
        target_count: int,
    ) -> list[Path]:
        count = max(1, min(target_count, 4))
        response = client.models.generate_images(
            model=self.settings.gemini_image_model,
            prompt=build_imagen_prompt(prompt),
            config=types.GenerateImagesConfig(
                number_of_images=count,
                aspect_ratio=self.settings.gemini_image_aspect_ratio,
                person_generation="allow_adult",
            ),
        )

        results: list[Path] = []
        for index, generated_image in enumerate(getattr(response, "generated_images", []) or []):
            image_bytes = generated_image_bytes(generated_image)
            if not image_bytes:
                continue
            image_path = output_dir / f"scene_{index + 1:02}.png"
            image_path.write_bytes(image_bytes)
            results.append(image_path)
        return results

    def _generate_content(self, client: Any, types: Any, contents: list[Any]) -> Any:
        try:
            image_config_kwargs = {"aspect_ratio": self.settings.gemini_image_aspect_ratio}
            if self.settings.gemini_image_size:
                image_config_kwargs["image_size"] = self.settings.gemini_image_size
            config = types.GenerateContentConfig(
                response_modalities=["Image"],
                image_config=types.ImageConfig(**image_config_kwargs),
            )
        except Exception:
            config = types.GenerateContentConfig(response_modalities=["Image"])

        return client.models.generate_content(
            model=self.settings.gemini_image_model,
            contents=contents,
            config=config,
        )


def build_scene_prompt(prompt: str, index: int, total: int, has_reference: bool) -> str:
    scene_styles = [
        "opening hook, wide cinematic composition, clear subject, strong mood",
        "middle beat, closer composition, more emotion and detail, dynamic lighting",
        "final beat, memorable polished composition, aspirational ending, high contrast",
        "alternate angle, atmospheric detail, editorial photography feel",
        "closing hero frame, premium social media poster quality",
    ]
    style = scene_styles[index % len(scene_styles)]
    reference_instruction = (
        "Use the attached image as a visual reference for subject, identity, product, or style. "
        if has_reference
        else ""
    )
    return (
        "Create one original vertical 9:16 Instagram Reel still frame. "
        "No subtitles, no captions, no logos, no watermark, no UI, no readable text. "
        f"{reference_instruction}"
        f"Scene {index + 1} of {total}: {style}. "
        "Make it cinematic, high-quality, realistic or polished depending on the prompt, "
        "with strong depth, natural composition, and room in the lower third for subtitles. "
        f"User prompt: {prompt}"
    )


def build_imagen_prompt(prompt: str) -> str:
    return (
        "Create vertical 9:16 Instagram Reel still frames. "
        "No subtitles, no captions, no logos, no watermark, no UI, no readable text. "
        "Cinematic social media composition, high-quality polished visual style, "
        "strong depth, natural lighting, and room in the lower third for subtitles. "
        "If people appear, they must be clearly adults. "
        f"User prompt: {prompt}"
    )


def build_pollinations_prompt(prompt: str, index: int, total: int) -> str:
    scene_styles = [
        "opening hook, cinematic wide shot, premium social media visual",
        "middle scene, closer framing, emotional detail, dynamic lighting",
        "final scene, polished hero frame, aspirational high-end composition",
        "alternate angle, editorial style, atmospheric depth",
        "closing frame, clean luxury campaign look",
    ]
    return (
        "Vertical 9:16 Instagram Reel still frame, 1080x1920, "
        "cinematic, high quality, polished, realistic or editorial as appropriate. "
        "No subtitles, no captions, no logos, no watermark, no UI, no readable text. "
        "Leave room in the lower third for subtitles. "
        "If people appear, they must be clearly adults. "
        f"Scene {index + 1} of {total}: {scene_styles[index % len(scene_styles)]}. "
        f"User prompt: {prompt}"
    )


def generated_image_bytes(generated_image: Any) -> bytes | None:
    image = getattr(generated_image, "image", None)
    if image is None:
        return None
    image_bytes = getattr(image, "image_bytes", None)
    if image_bytes is None:
        image_bytes = getattr(image, "imageBytes", None)
    if isinstance(image_bytes, bytes):
        return image_bytes
    if isinstance(image_bytes, str):
        return base64.b64decode(image_bytes)
    if hasattr(image, "save"):
        from io import BytesIO

        buffer = BytesIO()
        image.save(buffer, format="PNG")
        return buffer.getvalue()
    return None


def first_image_bytes(response: Any) -> bytes | None:
    for part in response_parts(response):
        if getattr(part, "inline_data", None) is not None:
            inline_data = part.inline_data
            data = getattr(inline_data, "data", None)
            if isinstance(data, bytes):
                return data
            if isinstance(data, str):
                return base64.b64decode(data)
        if hasattr(part, "as_image"):
            image = part.as_image()
            if image is not None:
                from io import BytesIO

                buffer = BytesIO()
                image.save(buffer, format="PNG")
                return buffer.getvalue()
    return None


def response_parts(response: Any) -> list[Any]:
    parts = getattr(response, "parts", None)
    if parts:
        return list(parts)

    candidates = getattr(response, "candidates", None) or []
    if not candidates:
        return []
    content = getattr(candidates[0], "content", None)
    return list(getattr(content, "parts", None) or [])
