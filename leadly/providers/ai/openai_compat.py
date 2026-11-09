"""Any OpenAI-compatible chat-completions API (YandexGPT compat endpoint, DeepSeek,
OpenRouter, a self-hosted model, …). Configure OPENAI_COMPAT_BASE_URL/_API_KEY/_MODEL."""

from __future__ import annotations

import asyncio
import json
import logging
import re
from typing import Callable

import aiohttp

from leadly.models import AIAnalysis
from leadly.providers.ai.base import (ANALYSIS_SCHEMA, ANALYSIS_TASK, PROPOSALS_SCHEMA, PROPOSALS_TASK,
                                      SYSTEM_PROMPT, AIContext, AIProvider, AIProviderError, parse_analysis,
                                      parse_proposals)
from leadly.providers.http import make_session

log = logging.getLogger(__name__)


class OpenAICompatProvider(AIProvider):
    name = "openai_compat"

    def __init__(self, base_url: str, api_key: str, model: str,
                 session_factory: Callable[[], aiohttp.ClientSession] | None = None):
        self.base_url, self.api_key, self.model = base_url.rstrip("/"), api_key, model
        self._session_factory = session_factory or (lambda: make_session(timeout=90))
        self._session: aiohttp.ClientSession | None = None

    def configured(self) -> tuple[bool, str]:
        if not (self.base_url and self.api_key and self.model):
            return False, "не заданы OPENAI_COMPAT_BASE_URL / _API_KEY / _MODEL"
        return True, f"OpenAI-совместимый · {self.model}"

    async def _json(self, task: str, ctx: AIContext, schema: dict) -> dict:
        if not self.configured()[0]:
            raise AIProviderError("ИИ-провайдер настроен не полностью — сообщите администратору.")
        if self._session is None or self._session.closed:
            self._session = self._session_factory()
        body = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + "\nОтвечай только JSON по схеме:\n"
                 + json.dumps(schema, ensure_ascii=False)},
                {"role": "user", "content": task + ctx.as_prompt()},
            ],
            "response_format": {"type": "json_object"},
        }
        try:
            async with self._session.post(f"{self.base_url}/chat/completions", json=body,
                                          headers={"Authorization": f"Bearer {self.api_key}"}) as resp:
                if resp.status in (401, 403):
                    raise AIProviderError("ИИ-провайдер отклонил ключ доступа — сообщите администратору.")
                if resp.status == 429:
                    raise AIProviderError("ИИ временно перегружен запросами. Попробуй через минуту.")
                if resp.status >= 400:
                    log.warning("openai-compatible status %s", resp.status)
                    raise AIProviderError("ИИ-сервис вернул ошибку. Попробуй позже.")
                data = await resp.json(content_type=None)
        except AIProviderError:
            raise
        except asyncio.TimeoutError:
            raise AIProviderError("ИИ-сервис не ответил вовремя.") from None
        except (aiohttp.ClientError, ValueError):
            raise AIProviderError("ИИ-сервис недоступен из сети сервера.") from None
        try:
            text = data["choices"][0]["message"]["content"]
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
            return json.loads(text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError):
            raise AIProviderError("ИИ вернул ответ в неожиданном формате.") from None

    async def analyze(self, ctx: AIContext) -> AIAnalysis:
        return parse_analysis(await self._json(ANALYSIS_TASK, ctx, ANALYSIS_SCHEMA), self.name)

    async def write_proposals(self, ctx: AIContext, round_: int = 0) -> dict[str, str | None]:
        task = PROPOSALS_TASK + (f"Вариант №{round_ + 1}: сформулируй иначе.\n\n" if round_ else "")
        return parse_proposals(await self._json(task, ctx, PROPOSALS_SCHEMA))

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
