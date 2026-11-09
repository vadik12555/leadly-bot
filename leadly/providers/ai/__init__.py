from leadly.providers.ai.base import AIContext, AINotConfigured, AIProvider, AIProviderError, NoAIProvider


def build_ai(settings) -> AIProvider:
    if settings.ai_provider in ("", "none"):
        return NoAIProvider()
    if settings.ai_provider == "anthropic":
        if not settings.anthropic_api_key:
            return NoAIProvider()
        from leadly.providers.ai.anthropic_provider import AnthropicAIProvider
        return AnthropicAIProvider(settings.anthropic_api_key, settings.anthropic_model)
    if settings.ai_provider == "openai_compat":
        from leadly.providers.ai.openai_compat import OpenAICompatProvider
        return OpenAICompatProvider(settings.openai_compat_base_url, settings.openai_compat_api_key,
                                    settings.openai_compat_model)
    raise ValueError(f"Неизвестный AI_PROVIDER: {settings.ai_provider} (none | anthropic | openai_compat)")


__all__ = ["AIContext", "AINotConfigured", "AIProvider", "AIProviderError", "NoAIProvider", "build_ai"]
