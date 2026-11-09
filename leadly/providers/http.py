"""Shared aiohttp session factory for outbound API calls."""

from __future__ import annotations

import aiohttp

USER_AGENT = "LeadlyBot/1.0 (Telegram bot for B2B lead research)"


def make_session(timeout: float = 30, user_agent: str = USER_AGENT, **kwargs) -> aiohttp.ClientSession:
    # trust_env: honour HTTPS_PROXY / HTTP_PROXY on servers that need an egress proxy
    return aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=timeout),
                                 headers={"User-Agent": user_agent}, trust_env=True, **kwargs)
