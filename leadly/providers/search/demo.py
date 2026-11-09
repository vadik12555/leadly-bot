"""Demo provider: fictional companies for trying the interface without a data source.

Only enabled explicitly (SEARCH_PROVIDERS=demo). Every card and list is marked as
demo in the UI. No problems or reviews are invented — only placeholder contact data
(+7 (000) phones, reserved `.example` domains) that cannot belong to a real business.
"""

from __future__ import annotations

import random

from leadly import catalog as c
from leadly.models import Company, SearchQuery
from leadly.providers.search.base import SearchProvider
from leadly.utils import now_iso, stable_seed

NAMES = ("Альфа", "Вектор", "Гранд", "Лидер", "Премьер", "Сфера", "Опора", "Стандарт", "Мастер", "Центр",
         "Аврора", "Восход", "Горизонт", "Импульс", "Классик")


class DemoSearchProvider(SearchProvider):
    code = "demo"
    title = "Демо-данные"
    is_demo = True
    attribution = "Демо-режим: вымышленные компании"

    async def search(self, query: SearchQuery) -> list[Company]:
        rnd = random.Random(stable_seed(query.niche, query.niche_title, query.subcategory, query.city))
        if query.niche == "custom":
            category = query.niche_title
        else:
            niche = c.NICHES[query.niche]
            category = (niche.subcategory(query.subcategory) or niche.subcategories[0]).title
        names = rnd.sample(NAMES, k=min(len(NAMES), query.limit))
        fetched = now_iso()
        out = []
        for i, name in enumerate(names):
            has_site = rnd.random() < 0.6
            out.append(Company(
                source=self.code, source_title=self.title, source_id=f"demo-{i}",
                name=f"[ДЕМО] «{name}»", category=category, city=query.city,
                address=f"{query.city}, демо-адрес {i + 1}",
                phone=f"+7 (000) 000-00-{10 + i}" if rnd.random() < 0.8 else None,
                website=f"http://demo-{i}.example" if has_site else None,
                fetched_at=fetched, is_demo=True,
            ))
        return out
