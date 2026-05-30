from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass

from config import Settings


@dataclass(slots=True)
class CaptionResult:
    caption: str
    hashtags: list[str]

    @property
    def full_text(self) -> str:
        tags = " ".join(self.hashtags)
        return f"{self.caption}\n\n{tags}".strip()


class CaptionGenerator:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    async def generate(
        self,
        source_text: str | None,
        transcript: str,
        source_type: str,
    ) -> CaptionResult:
        if self.settings.openai_api_key:
            try:
                return await asyncio.to_thread(
                    self._generate_with_openai,
                    source_text,
                    transcript,
                    source_type,
                )
            except Exception:
                pass
        return self._fallback(source_text, transcript, source_type)

    def _generate_with_openai(
        self,
        source_text: str | None,
        transcript: str,
        source_type: str,
    ) -> CaptionResult:
        from openai import OpenAI

        client = OpenAI(api_key=self.settings.openai_api_key)
        prompt = {
            "source_type": source_type,
            "user_prompt_or_caption": source_text or "",
            "transcript": transcript[:1800],
        }
        response = client.chat.completions.create(
            model=self.settings.openai_model,
            messages=[
                {
                    "role": "system",
                    "content": (
                        "Create an Instagram Reel caption. Return JSON with keys "
                        "caption and hashtags. The caption should be punchy, under "
                        "180 characters, and not promise false results. Hashtags "
                        "must be an array of 6 to 10 lowercase hashtags."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt)},
            ],
            response_format={"type": "json_object"},
            temperature=0.7,
        )
        content = response.choices[0].message.content or "{}"
        payload = json.loads(content)
        caption = str(payload.get("caption") or "").strip()
        hashtags = normalize_hashtags(payload.get("hashtags") or [])
        if not caption:
            return self._fallback(source_text, transcript, source_type)
        return CaptionResult(caption=caption, hashtags=hashtags or fallback_hashtags(source_text, transcript, source_type))

    def _fallback(self, source_text: str | None, transcript: str, source_type: str) -> CaptionResult:
        base = source_text or transcript
        base = " ".join((base or "").split())
        if base:
            words = base.split()
            caption = " ".join(words[:24])
            if len(words) > 24:
                caption += "..."
        else:
            caption = "A quick highlight worth watching."
        return CaptionResult(
            caption=caption,
            hashtags=fallback_hashtags(source_text, transcript, source_type),
        )


def fallback_hashtags(source_text: str | None, transcript: str, source_type: str) -> list[str]:
    text = f"{source_text or ''} {transcript}".lower()
    tags = ["#reels", "#instagramreels", "#shortvideo"]
    keyword_map = {
        "business": "#business",
        "startup": "#startup",
        "money": "#finance",
        "fitness": "#fitness",
        "food": "#food",
        "travel": "#travel",
        "ai": "#ai",
        "python": "#python",
        "marketing": "#marketing",
        "motivation": "#motivation",
        "learn": "#learn",
        "tutorial": "#tutorial",
    }
    for keyword, tag in keyword_map.items():
        if keyword in text and tag not in tags:
            tags.append(tag)
    if source_type == "prompt" and "#creative" not in tags:
        tags.append("#creative")
    for tag in ["#contentcreator", "#viralvideos", "#dailyreel"]:
        if len(tags) >= 9:
            break
        tags.append(tag)
    return tags[:10]


def normalize_hashtags(value: list[str]) -> list[str]:
    tags: list[str] = []
    for item in value:
        tag = str(item).strip().lower()
        tag = re.sub(r"[^a-z0-9_#]", "", tag)
        if not tag:
            continue
        if not tag.startswith("#"):
            tag = f"#{tag}"
        if tag not in tags:
            tags.append(tag)
    return tags[:10]
