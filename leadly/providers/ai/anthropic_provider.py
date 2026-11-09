"""Claude via the official Anthropic SDK. Needs ANTHROPIC_API_KEY."""

from __future__ import annotations

import json
import logging

import anthropic

from leadly.models import AIAnalysis
from leadly.providers.ai.base import (ANALYSIS_SCHEMA, ANALYSIS_TASK, PROPOSALS_SCHEMA, PROPOSALS_TASK,
                                      SYSTEM_PROMPT, AIContext, AIProvider, AIProviderError, parse_analysis,
                                      parse_proposals)

log = logging.getLogger(__name__)


class AnthropicAIProvider(AIProvider):
    name = "anthropic"

    def __init__(self, api_key: str, model: str = "claude-opus-5-5", client: anthropic.AsyncAnthropic | None = None):
        self.model = model
        self.client = client or anthropic.AsyncAnthropic(api_key=api_key or None, timeout=90.0, max_retries=2)

    def configured(self) -> tuple[bool, str]:
        return True, f"Anthropic · {self.model}"

    async def _json(self, task: str, ctx: AIContext, schema: dict) -> dict:
        try:
            response = await self.client.beta.messages.create(
                model=self.model,
                max_tokens=16000,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": task + ctx.as_prompt()}],
                output_config={"format": {"type": "json_schema", "schema": schema}},
                # on a safety decline the API retries on a fallback model inside the same call
                betas=["server-side-fallback-2026-07-01"],
                fallbacks="default",
            )
        except anthropic.AuthenticationError:
            raise AIProviderError("ИИ-провайдер отклонил ключ доступа — сообщите администратору.") from None
        except anthropic.RateLimitError:
            raise AIProviderError("ИИ временно перегружен запросами. Попробуй через минуту.") from None
        except anthropic.APIStatusError as e:
            log.warning("anthropic status error %s", e.status_code)
            raise AIProviderError("ИИ-сервис вернул ошибку. Попробуй позже.") from None
        except anthropic.APIConnectionError:
            raise AIProviderError("ИИ-сервис недоступен из сети сервера.") from None
        if response.stop_reason == "refusal":
            raise AIProviderError("ИИ отказался обрабатывать этот запрос.")
        text = next((b.text for b in response.content if b.type == "text"), "")
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise AIProviderError("ИИ вернул ответ в неожиданном формате.") from None

    async def analyze(self, ctx: AIContext) -> AIAnalysis:
        return parse_analysis(await self._json(ANALYSIS_TASK, ctx, ANALYSIS_SCHEMA), self.name)

    async def write_proposals(self, ctx: AIContext, round_: int = 0) -> dict[str, str | None]:
        task = PROPOSALS_TASK + (f"Это вариант №{round_ + 1}: сформулируй иначе, чем обычно.\n\n" if round_ else "")
        return parse_proposals(await self._json(task, ctx, PROPOSALS_SCHEMA))

    async def close(self) -> None:
        await self.client.close()
