"""AIProvider: analysis and copywriting on top of verified facts. Vendor-agnostic."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass

from leadly.models import AIAnalysis


class AIProviderError(Exception):
    def __init__(self, user_message: str):
        super().__init__(user_message)
        self.user_message = user_message


class AINotConfigured(AIProviderError):
    def __init__(self):
        super().__init__("ИИ-анализ пока не подключён администратором.")


@dataclass
class AIContext:
    """Only public business data and observed facts — no user data, no contacts."""

    service: str
    company_name: str
    category: str
    city: str
    website: str | None
    facts: list[str]
    not_checked: list[str]
    hypotheses: list[str]
    executor: dict | None = None  # the user's own profile, filled by them for their proposals

    def as_prompt(self) -> str:
        return json.dumps({
            "исполнитель": self.executor or "профиль не заполнен — используй [Ваше имя]",
            "услуга_исполнителя": self.service,
            "компания": {"название": self.company_name, "категория": self.category, "город": self.city,
                         "сайт": self.website or "не указан в источнике"},
            "подтверждённые_факты": self.facts,
            "не_проверено": self.not_checked,
            "гипотезы_по_правилам": self.hypotheses,
        }, ensure_ascii=False, indent=1)


SYSTEM_PROMPT = """Ты помогаешь фрилансеру подготовить первый контакт с потенциальным клиентом-бизнесом.
Строгие правила:
- Используй ТОЛЬКО данные из JSON. Ничего не выдумывай: ни отзывов, ни проблем, ни цифр, ни фактов о компании.
- Отсутствие данных в источнике не означает отсутствие у компании (сайта, записи и т.п.) — формулируй это как «не найдено/не проверено».
- Чётко разделяй факты и гипотезы. Гипотезы помечай словами «возможно», «стоит уточнить».
- Никаких ложных обещаний (рост продаж в N раз, гарантии и т.п.).
- Пиши по-русски, просто и вежливо."""

ANALYSIS_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string", "description": "1-2 предложения: что известно о компании"},
        "checked": {"type": "array", "items": {"type": "string"}, "description": "что удалось проверить"},
        "opportunities": {"type": "array", "items": {
            "type": "object",
            "properties": {"text": {"type": "string"},
                           "confidence": {"type": "string", "enum": ["низкая", "средняя", "высокая"]}},
            "required": ["text", "confidence"], "additionalProperties": False}},
        "recommendation": {"type": "string"},
        "confidence": {"type": "string", "enum": ["низкая", "средняя", "высокая"]},
    },
    "required": ["summary", "checked", "opportunities", "recommendation", "confidence"],
    "additionalProperties": False,
}

PROPOSALS_SCHEMA = {
    "type": "object",
    "properties": {
        "short": {"type": "string", "description": "короткое неформальное, 2-3 предложения"},
        "business": {"type": "string", "description": "деловое, 4-6 предложений"},
        "fact": {"type": "string", "description": "с акцентом на ОДНУ подтверждённую возможность; "
                                                  "пустая строка, если подтверждённых фактов нет"},
    },
    "required": ["short", "business", "fact"],
    "additionalProperties": False,
}

ANALYSIS_TASK = "Проанализируй компанию для услуги исполнителя. Ответ строго по JSON-схеме.\n\n"
PROPOSALS_TASK = ("Напиши три варианта первого сообщения от исполнителя компании. Не используй выдуманные факты. "
                  "Если профиль исполнителя заполнен — представься его именем, опиши его специализацию, добавь "
                  "ссылку на портфолио и контакт в подписи; не выдумывай опыт, кейсы и цены сверх профиля. "
                  "Если профиль не заполнен — пиши [Ваше имя]. Ответ строго по JSON-схеме.\n\n")


class AIProvider(ABC):
    name: str = "base"

    def configured(self) -> tuple[bool, str]:
        return True, "готов"

    @abstractmethod
    async def analyze(self, ctx: AIContext) -> AIAnalysis: ...

    @abstractmethod
    async def write_proposals(self, ctx: AIContext, round_: int = 0) -> dict[str, str | None]: ...

    async def close(self) -> None:
        pass


class NoAIProvider(AIProvider):
    name = "none"

    def configured(self) -> tuple[bool, str]:
        return False, "не подключён (AI_PROVIDER=none)"

    async def analyze(self, ctx: AIContext) -> AIAnalysis:
        raise AINotConfigured()

    async def write_proposals(self, ctx: AIContext, round_: int = 0) -> dict[str, str | None]:
        raise AINotConfigured()


def parse_analysis(data: dict, provider: str) -> AIAnalysis:
    return AIAnalysis(
        summary=str(data.get("summary", "")).strip(),
        checked=[str(x) for x in data.get("checked", [])][:8],
        opportunities=[{"text": str(o.get("text", "")), "confidence": str(o.get("confidence", "низкая"))}
                       for o in data.get("opportunities", []) if isinstance(o, dict)][:6],
        recommendation=str(data.get("recommendation", "")).strip(),
        confidence=str(data.get("confidence", "низкая")),
        provider=provider,
    )


def parse_proposals(data: dict) -> dict[str, str | None]:
    out: dict[str, str | None] = {}
    for key in ("short", "business", "fact"):
        text = str(data.get(key) or "").strip()
        out[key] = text or None
    return out
