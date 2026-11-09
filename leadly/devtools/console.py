"""Interactive offline chat with the bot — for UI checks when Telegram is unreachable.

    python -m leadly.devtools.console          # real data sources from .env (OpenStreetMap by default)
    python -m leadly.devtools.console --demo   # clearly marked fictional companies, no network needed

Type a button number to press it, any other text to send a message (/start, /leads …).
Uses the same handlers, database and providers as the real bot; nothing is sent to Telegram.
"""

from __future__ import annotations

import asyncio
import logging
import sys

from leadly.config import BASE_DIR, load_settings
from leadly.devtools.fake_telegram import TelegramSimulator, render


async def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    demo = "--demo" in sys.argv
    settings = load_settings(db_path=BASE_DIR / "data" / "console.db", log_file=None,
                             **({"search_providers": ["demo"]} if demo else {}))
    tg = await TelegramSimulator.create(settings)
    print("Leadly · тестовый режим без Telegram. Номер — нажать кнопку, текст — отправить, q — выход.\n")
    await tg.send("/start")
    shown_alerts = 0
    try:
        while True:
            last = tg.last
            print("─" * 60)
            print(render(last, numbered=True))
            for alert in tg.session.alerts[shown_alerts:]:
                print(f"  ⓘ {alert}")
            shown_alerts = len(tg.session.alerts)
            try:
                raw = (await asyncio.to_thread(input, "\n> ")).strip()
            except EOFError:
                break
            if raw in ("q", "quit", "exit"):
                break
            if raw.isdigit() and last.buttons:
                n = int(raw)
                if not 1 <= n <= len(last.buttons):
                    print("Нет такой кнопки")
                    continue
                btn = last.buttons[n - 1]
                if btn.url:
                    print(f"  ↗ ссылка: {btn.url}")
                    continue
                if btn.copy_text:
                    print(f"  ⧉ скопировано:\n{btn.copy_text.text}")
                    continue
                before = set(tg.session.messages)
                await tg.press(btn.text)
                for mid in sorted(set(tg.session.messages) - before):
                    m = tg.session.messages[mid]
                    if m is not tg.last:
                        print(render(m))
            elif raw:
                await tg.send(raw)
    finally:
        await tg.close()


if __name__ == "__main__":
    asyncio.run(main())
