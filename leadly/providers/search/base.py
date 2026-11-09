"""SearchProvider: the only place that knows where companies come from.

To add a source (Yandex, Google Places, another legal catalog): subclass
SearchProvider, implement `search`, register it in `leadly.providers.search.build_search`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from leadly.models import Company, SearchQuery


class SearchProviderError(Exception):
    """Source is unavailable or returned an error. `user_message` is safe to show."""

    def __init__(self, user_message: str, *, retryable: bool = True):
        super().__init__(user_message)
        self.user_message = user_message
        self.retryable = retryable


class SearchProvider(ABC):
    code: str = "base"
    title: str = "base"
    is_demo: bool = False
    attribution: str = ""  # required credit line, shown under results

    @abstractmethod
    async def search(self, query: SearchQuery) -> list[Company]:
        """Companies matching the query, at most `query.limit`. Raise SearchProviderError on failure."""

    def configured(self) -> tuple[bool, str]:
        """(ready, human-readable status) for the admin panel."""
        return True, "готов"

    async def close(self) -> None:
        pass
