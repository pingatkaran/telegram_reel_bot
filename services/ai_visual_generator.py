from __future__ import annotations

import asyncio
import base64
from pathlib import Path
from typing import Any

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
        if not self.settings.enable_ai_prompt_visuals or not self.settings.gemini_api_key:
            return []
        return await asyncio.to_thread(self._generate_with_gemini, prompt, job_id, count, reference_image)

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

    def _generate_content(self, client: Any, types: Any, contents: list[Any]) -> Any:
        response_format = {"image": {"aspect_ratio": self.settings.gemini_image_aspect_ratio}}
        if self.settings.gemini_image_size:
            response_format["image"]["image_size"] = self.settings.gemini_image_size

        try:
            return client.models.generate_content(
                model=self.settings.gemini_image_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    response_modalities=["Image"],
                    response_format=response_format,
                ),
            )
        except TypeError:
            return client.models.generate_content(
                model=self.settings.gemini_image_model,
                contents=contents,
                config=types.GenerateContentConfig(response_modalities=["Image"]),
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
